"use strict";

const byId = (id) => document.getElementById(id);
const roomSelect = byId("room");
const actorSelect = byId("geometry-actor");
const centerInputs = ["center-x", "center-y", "center-z"].map(byId);
const halfExtent = byId("half-extent");
const sampleActor = byId("sample-actor");
const sampleSpawn = byId("sample-spawn");
const samplePoint = byId("sample-point");
const connection = byId("connection");
const controlBar = byId("control-bar");
const pauseButton = byId("room-pause");
const resumeButton = byId("room-resume");
const stepInput = byId("room-steps");
const stepButton = byId("room-step");
const controlStatus = byId("room-control-status");
const layers = ["triangles", "proxies", "actors", "physics", "navigation"].map(byId);
const vmEntitySelect = byId("vm-entity");
const views = [
  { canvas: byId("top"), vertical: 2, up: false },
  { canvas: byId("side"), vertical: 1, up: true },
];
const colors = getComputedStyle(document.documentElement);
const color = (name) => colors.getPropertyValue("--" + name).trim();
const number = (value) => Number.isFinite(value) ? value.toFixed(2) : "—";
const vector = (point) => point.map(number).join(", ");
const pollDelay = 500;

function setText(element, text) {
  if (element.textContent !== text) {
    element.textContent = text;
  }
}

let snapshot;
let geometry;
let roomOptions = "";
let actorOptions = "";
let vmEntityOptions = "";
let geometryLoading = false;
let geometryMessage = "";
let controlBusy = false;
let controlMessage = "";

async function getJson(route) {
  const response = await fetch("/api/debug/v1/" + route, {
    signal: AbortSignal.timeout(2000),
  });
  if (!response.ok) {
    throw new Error("HTTP " + response.status);
  }
  return response.json();
}

function renderRoomControls(room) {
  const enabled = Boolean(snapshot?.control_enabled);
  controlBar.hidden = !enabled;
  if (!enabled) {
    return;
  }
  const control = room?.control;
  const maxBudget = control?.max_step_budget ?? 60;
  stepInput.max = String(maxBudget);
  const paused = Boolean(control?.paused);
  const pending = control?.pending_steps ?? 0;
  pauseButton.disabled = controlBusy || !room || (paused && pending === 0);
  resumeButton.disabled = controlBusy || !room || !paused;
  stepInput.disabled = controlBusy || !room || !paused;
  stepButton.disabled = controlBusy || !room || !paused || pending >= maxBudget;
  let statusText = "No active room selected";
  if (control) {
    statusText = (paused ? "Paused" : "Running")
      + " · sim tick " + control.sim_tick
      + (pending ? " · pending " + pending + "/" + maxBudget : "")
      + (control.stepped_ticks ? " · stepped " + control.stepped_ticks : "");
  }
  if (controlMessage) {
    statusText += " · " + controlMessage;
  }
  setText(controlStatus, statusText);
}

async function sendRoomControl(action, steps) {
  const room = selectedRoom();
  if (!room || controlBusy || !snapshot?.control_enabled) {
    return;
  }
  controlBusy = true;
  controlMessage = "";
  renderRoomControls(room);
  const payload = steps == null ? { action } : { action, steps };
  try {
    const response = await fetch(
      "/api/debug/v1/rooms/" + encodeURIComponent(room.id) + "/control",
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
        signal: AbortSignal.timeout(2000),
      },
    );
    if (!response.ok) {
      throw new Error("HTTP " + response.status);
    }
    snapshot = await getJson("state");
  } catch (error) {
    controlMessage = "command rejected (" + error.message + ")";
  } finally {
    controlBusy = false;
    render();
  }
}

function selectedRoom() {
  return snapshot?.rooms.find((room) => room.id === roomSelect.value);
}

function matchingGeometry(room) {
  return geometry && (!room || room.map === geometry.map) ? geometry : undefined;
}

function renderGeometryControls(actors) {
  const choices = actors.filter(({ position }) => position.every(Number.isFinite));
  const options = JSON.stringify(choices.map(({ entity, kind }) => [entity, kind]));
  if (options !== actorOptions) {
    const previous = actorSelect.value;
    actorSelect.replaceChildren();
    if (choices.length === 0) {
      actorSelect.add(new Option("No available actor", ""));
    } else {
      for (const { entity, kind } of choices) {
        actorSelect.add(new Option(kind + " · " + entity, String(entity)));
      }
      if (choices.some(({ entity }) => String(entity) === previous)) {
        actorSelect.value = previous;
      }
    }
    actorOptions = options;
  }
  actorSelect.disabled = geometryLoading || choices.length === 0;
  sampleActor.disabled = actorSelect.disabled;
  samplePoint.disabled = geometryLoading;
  sampleSpawn.disabled = geometryLoading;
  setText(byId("geometry-status"), geometryMessage);
}

function durationPercentile(histogram, bounds, percentile) {
  let remaining = Math.ceil(histogram.samples * percentile);
  for (const [index, count] of histogram.counts.entries()) {
    remaining -= count;
    if (remaining <= 0) {
      return index === bounds.length
        ? "≥" + bounds[index - 1] / 1000 + " ms"
        : "<" + bounds[index] / 1000 + " ms";
    }
  }
  return "—";
}

function renderTiming(timing) {
  const panel = byId("timing-panel");
  panel.hidden = !timing;
  if (!timing) return;

  const tick = timing.tick_duration_us;
  const lateness = timing.schedule_lateness_us;
  const bounds = timing.bin_upper_exclusive_us;
  const input = timing.input_budget;
  const percentiles = (histogram) => "p95 " + durationPercentile(histogram, bounds, .95)
    + " / p99 " + durationPercentile(histogram, bounds, .99);
  const stats = tick.samples
    ? "Tick " + percentiles(tick) + " · scheduling lateness " + percentiles(lateness)
      + " · " + tick.samples + " samples · " + timing.missed_ticks + " skipped ticks"
    : "Waiting for an active match.";
  setText(byId("timing-stats"), stats);
  setText(byId("timing-input"), input.used + " input work units · " + input.peer_limit_hits
    + " full peer budgets · " + input.turn_limit_hits + " full turn budgets · "
    + input.turns_with_backlog + " turns with queued work");
}

function renderFaults(faults = []) {
  byId("fault-panel").hidden = faults.length === 0;
  byId("fault-count").textContent = String(faults.length);
  const rows = document.createDocumentFragment();
  for (const fault of faults.slice().reverse()) {
    const row = rows.appendChild(document.createElement("tr"));
    for (const value of [fault.tick, fault.room, fault.stage, fault.connection ?? "—", fault.reason]) {
      row.insertCell().textContent = String(value);
    }
  }
  byId("fault-rows").replaceChildren(rows);
}

function render() {
  const rooms = snapshot?.rooms ?? [];
  const options = JSON.stringify(rooms.map(({ id, name }) => [id, name]));
  if (options !== roomOptions) {
    const previous = roomSelect.value;
    roomSelect.replaceChildren();
    if (rooms.length === 0) {
      roomSelect.add(new Option("No active room", ""));
    } else {
      for (const room of rooms) {
        roomSelect.add(new Option(room.name + " · " + room.id, room.id));
      }
      if (rooms.some((room) => room.id === previous)) {
        roomSelect.value = previous;
      }
    }
    roomSelect.disabled = rooms.length === 0;
    roomOptions = options;
  }

  const room = selectedRoom();
  const sample = matchingGeometry(room);
  const actors = room?.actors ?? [];
  const summary = room
    ? room.name + " · map " + room.map
    : sample ? "Prepared map " + sample.map : "No scene available";
  const roomStatus = !snapshot ? " · room state unavailable" : room ? "" : " · no active room";
  setText(byId("scene"), summary + roomStatus);
  setText(byId("tick"), snapshot
    ? "Tick " + snapshot.tick + (snapshot.truncated ? " · state capped" : "")
    : "");

  const info = byId("geometry-info");
  if (sample) {
    const { triangles, proxies } = sample.counts;
    const limited = Object.keys(sample.truncated).filter((key) => sample.truncated[key]);
    const extent = sample.bounds.max.map((value, axis) => number(value - sample.bounds.min[axis]));
    info.textContent = [
      triangles + " probe triangles",
      sample.sources.proxies ? proxies + " broad-phase bounds" : "Contact bounds unavailable",
      extent.join(" × ") + " m sample centered at (" + vector(sample.origin) + ")",
      sample.sources.movement_cache ?? sample.sources.triangles,
      limited.length ? "Capped " + limited.join(" and ") : "",
    ].filter(Boolean).join(" · ");
  } else {
    info.textContent = geometry ? "No collision sample for this room's map." : "Collision sample unavailable.";
  }

  setText(byId("overlay-info"), formatOverlaySummary(room));
  renderRoomControls(room);
  renderGeometryControls(geometry && !sample ? [] : actors);
  renderTiming(snapshot?.timing);
  renderFaults(snapshot?.faults);
  renderActors(actors);
  renderScripts(room);
  for (const view of views) {
    renderView(view, sample, room);
  }
}

function formatOverlaySummary(room) {
  if (!room) return "";
  const parts = [];
  if (room.physics) {
    const p = room.physics;
    const t = [];
    if (p.truncated?.rays) t.push("rays");
    if (p.truncated?.contact_normals) t.push("normals");
    if (p.truncated?.ground_probes) t.push("probes");
    parts.push(
      p.rays.length + " rays, " + p.contact_normals.length + " normals, "
      + p.ground_probes.length + " ground probes" + (t.length ? " (capped " + t.join(", ") + ")" : "")
    );
  }
  if (room.navigation) {
    const n = room.navigation;
    const t = [];
    if (n.truncated?.routes) t.push("routes");
    if (n.truncated?.spawn_candidates) t.push("spawns");
    parts.push(
      n.routes.length + " AI routes, " + n.spawn_candidates.length + " spawn candidates"
      + (t.length ? " (capped " + t.join(", ") + ")" : "")
    );
  }
  return parts.join(" · ");
}

function formatVarList(vars, truncated) {
  if (!vars || vars.length === 0) return truncated ? "(capped)" : "—";
  const text = vars.map((entry) => entry.id + (entry.resolved ? "*=" : "=") + entry.value).join(", ");
  return truncated ? text + " · (capped)" : text;
}

function renderScripts(room) {
  const panel = byId("vm-panel");
  const scripts = room?.scripts ?? [];
  panel.hidden = scripts.length === 0;
  setText(byId("vm-count"), scripts.length
    ? String(scripts.length) + (room?.scripts_truncated ? " (capped)" : "")
    : "");
  if (scripts.length === 0) {
    vmEntityOptions = "";
    vmEntitySelect.replaceChildren();
    vmEntitySelect.disabled = true;
    setText(byId("vm-meta"), "");
    setText(byId("vm-entity-vars"), "");
    byId("vm-rows").replaceChildren();
    return;
  }

  const keyOf = (entry) => entry.entity + ":" + entry.kind;
  const options = JSON.stringify(scripts.map((entry) => [entry.entity, entry.kind]));
  if (options !== vmEntityOptions) {
    const previous = vmEntitySelect.value;
    vmEntitySelect.replaceChildren();
    for (const entry of scripts) {
      vmEntitySelect.add(new Option(entry.entity + " · " + entry.kind, keyOf(entry)));
    }
    if (scripts.some((entry) => keyOf(entry) === previous)) {
      vmEntitySelect.value = previous;
    }
    vmEntityOptions = options;
  }
  vmEntitySelect.disabled = false;

  const selected = scripts.find((entry) => keyOf(entry) === vmEntitySelect.value) ?? scripts[0];
  setText(
    byId("vm-meta"),
    "now " + selected.now_ms + " ms · " + selected.diagnostics + " diagnostics"
      + (selected.instances_truncated ? " · instances capped" : ""),
  );
  setText(
    byId("vm-entity-vars"),
    "Entity vars: " + formatVarList(selected.variables, selected.variables_truncated),
  );

  const rows = document.createDocumentFragment();
  if (selected.instances.length === 0) {
    const cell = rows.appendChild(document.createElement("tr")).insertCell();
    cell.colSpan = 5;
    cell.className = "empty";
    cell.textContent = "No script instances.";
  }
  for (const instance of selected.instances) {
    const row = rows.appendChild(document.createElement("tr"));
    const parent = instance.parent ? instance.parent[0] + ":" + instance.parent[1] : "—";
    const states = instance.active_states.map((state) => {
      const base = state.raw + ":" + state.kind + "#" + state.activation;
      return state.flags != null ? base + "[f=" + state.flags + "]" : base;
    });
    const statesText = (states.length ? states.join(", ") : "—")
      + (instance.states_truncated ? " · (capped)" : "");
    const varsText = formatVarList(instance.variables, instance.variables_truncated);
    for (const value of [instance.id, instance.graph, parent, statesText, varsText]) {
      const cell = row.insertCell();
      cell.textContent = String(value);
      cell.className = "coordinates";
    }
  }
  byId("vm-rows").replaceChildren(rows);
}

function renderActors(actors) {
  const body = byId("actor-rows");
  const rows = document.createDocumentFragment();
  byId("actor-count").textContent = String(actors.length);
  if (actors.length === 0) {
    const cell = rows.appendChild(document.createElement("tr")).insertCell();
    cell.colSpan = 9;
    cell.className = "empty";
    cell.textContent = "No actors in this room.";
  }
  for (const actor of actors) {
    const row = rows.appendChild(document.createElement("tr"));
    const entity = actor.generation == null ? String(actor.entity) : actor.entity + " / " + actor.generation;
    const clip = actor.last_contact_clip;
    const fields = [
      entity, actor.kind, actor.team, actor.alive ? "Alive" : "Dead", number(actor.health),
      actor.script_active ? "Active" : "—", vector(actor.position), vector(actor.velocity),
      clip ? "Tick " + clip.tick + " · proxy " + clip.proxy_id + " · mask 0x"
        + clip.category.toString(16) + " · fraction " + clip.fraction.toFixed(3) : "—",
    ];
    for (const value of fields) {
      const cell = row.insertCell();
      cell.textContent = value;
      if (row.cells.length >= 7) {
        cell.className = "coordinates";
      }
    }
    if (clip) {
      row.cells[8].title = "Feet before (" + vector(clip.before) + ") · intended ("
        + vector(clip.intended) + ") · after (" + vector(clip.after) + ")";
    }
  }
  body.replaceChildren(rows);
}

function actorBounds(actors) {
  if (actors.length === 0) {
    return { min: [-12, -12, -12], max: [12, 12, 12] };
  }
  const min = actors[0].position.map((value) => value - 4);
  const max = actors[0].position.map((value) => value + 4);
  for (const { position } of actors) {
    for (let axis = 0; axis < 3; axis++) {
      min[axis] = Math.min(min[axis], position[axis] - 4);
      max[axis] = Math.max(max[axis], position[axis] + 4);
    }
  }
  return { min, max };
}

function renderView({ canvas, vertical, up }, sample, room) {
  const { clientWidth: width, clientHeight: height } = canvas;
  if (width === 0 || height === 0) {
    return;
  }
  const pixelRatio = Math.min(window.devicePixelRatio, 2);
  const rasterWidth = Math.round(width * pixelRatio);
  const rasterHeight = Math.round(height * pixelRatio);
  if (canvas.width !== rasterWidth || canvas.height !== rasterHeight) {
    canvas.width = rasterWidth;
    canvas.height = rasterHeight;
  }

  const actors = room?.actors ?? [];
  const ctx = canvas.getContext("2d");
  ctx.setTransform(pixelRatio, 0, 0, pixelRatio, 0, 0);
  ctx.clearRect(0, 0, width, height);
  const volume = sample?.origin_source === "requested-center" ? sample.bounds : undefined;
  const positionedActors = actors.filter(({ position }) => position.every((value, axis) =>
    Number.isFinite(value) && (!volume || (volume.min[axis] <= value && value <= volume.max[axis]))));
  const { min, max } = sample?.bounds ?? actorBounds(positionedActors);
  const scale = Math.min(
    Math.max(1, width - 32) / Math.max(1, max[0] - min[0]),
    Math.max(1, height - 32) / Math.max(1, max[vertical] - min[vertical]),
  );
  const centerX = (min[0] + max[0]) / 2;
  const centerV = (min[vertical] + max[vertical]) / 2;
  const x = (value) => width / 2 + (value - centerX) * scale;
  const y = (value) => height / 2 + (value - centerV) * scale * (up ? -1 : 1);
  const project = (point) => [x(point[0]), y(point[vertical])];
  const finitePoint = (point) => Array.isArray(point) && point.length === 3 && point.every(Number.isFinite);

  drawGrid(ctx, { min, max, vertical, scale, x, y });
  if (sample && layers[0].checked) {
    ctx.beginPath();
    for (const { vertices } of sample.triangles) {
      ctx.moveTo(...project(vertices[0]));
      ctx.lineTo(...project(vertices[1]));
      ctx.lineTo(...project(vertices[2]));
      ctx.closePath();
    }
    ctx.strokeStyle = color("triangle");
    ctx.globalAlpha = .55;
    ctx.stroke();
    ctx.globalAlpha = 1;
  }
  if (sample && layers[1].checked) {
    ctx.strokeStyle = color("proxy");
    ctx.setLineDash([4, 3]);
    for (const bound of sample.proxies) {
      const [left, a] = project(bound.min);
      const [right, b] = project(bound.max);
      ctx.strokeRect(left, Math.min(a, b), right - left, Math.abs(a - b));
    }
    ctx.setLineDash([]);
  }
  if (room?.navigation && layers[4].checked) {
    drawNavigationOverlay(ctx, project, room.navigation);
  }
  if (room?.physics && layers[3].checked) {
    drawPhysicsOverlay(ctx, project, room.physics);
  }
  if (layers[2].checked) {
    ctx.font = "12px monospace";
    for (const actor of positionedActors) {
      const [left, top] = project(actor.position);
      ctx.beginPath();
      ctx.arc(left, top, 4, 0, 2 * Math.PI);
      const tint = color(actor.alive ? (actor.kind === "player" ? "player" : "bot") : "dead");
      ctx.fillStyle = tint;
      ctx.strokeStyle = tint;
      if (actor.alive) {
        ctx.fill();
      } else {
        ctx.stroke();
      }
      ctx.fillText(String(actor.entity), left + 7, top - 6);
    }
  }

  function drawPhysicsOverlay(ctx, project, physics) {
    for (const probe of physics.ground_probes ?? []) {
      if (!finitePoint(probe.origin)) continue;
      const [ox, oy] = project(probe.origin);
      const tint = color(probe.grounded ? "probe-hit" : "probe-miss");
      ctx.strokeStyle = tint;
      ctx.fillStyle = tint;
      if (finitePoint(probe.hit)) {
        const [hx, hy] = project(probe.hit);
        ctx.beginPath();
        ctx.moveTo(ox, oy);
        ctx.lineTo(hx, hy);
        ctx.stroke();
        if (finitePoint(probe.normal)) {
          const tip = probe.hit.map((v, i) => v + probe.normal[i] * 0.6);
          ctx.beginPath();
          ctx.moveTo(hx, hy);
          ctx.lineTo(...project(tip));
          ctx.stroke();
        }
        ctx.beginPath();
        ctx.arc(hx, hy, 2.5, 0, 2 * Math.PI);
        ctx.fill();
      } else {
        ctx.beginPath();
        ctx.arc(ox, oy, 2.5, 0, 2 * Math.PI);
        ctx.stroke();
      }
    }

    ctx.strokeStyle = color("normal");
    ctx.fillStyle = color("normal");
    for (const contact of physics.contact_normals ?? []) {
      if (!finitePoint(contact.point) || !finitePoint(contact.normal)) continue;
      const tip = contact.point.map((v, i) => v + contact.normal[i] * 0.8);
      const [px, py] = project(contact.point);
      ctx.beginPath();
      ctx.moveTo(px, py);
      ctx.lineTo(...project(tip));
      ctx.stroke();
      ctx.beginPath();
      ctx.arc(px, py, 2, 0, 2 * Math.PI);
      ctx.fill();
    }

    for (const ray of physics.rays ?? []) {
      if (!finitePoint(ray.origin) || !finitePoint(ray.end)) continue;
      const tint = color(ray.hit === "actor" ? "ray-hit" : ray.hit === "world" ? "ray-world" : "ray-miss");
      ctx.strokeStyle = tint;
      ctx.fillStyle = tint;
      ctx.setLineDash(ray.hit === "none" ? [4, 3] : []);
      const [sx, sy] = project(ray.origin);
      const [ex, ey] = project(ray.end);
      ctx.beginPath();
      ctx.moveTo(sx, sy);
      ctx.lineTo(ex, ey);
      ctx.stroke();
      ctx.beginPath();
      ctx.arc(ex, ey, 2.5, 0, 2 * Math.PI);
      ctx.fill();
    }
    ctx.setLineDash([]);
  }

  function drawNavigationOverlay(ctx, project, navigation) {
    const actorByEntity = new Map(actors.map((actor) => [actor.entity, actor]));
    for (const route of navigation.routes ?? []) {
      if (route.waypoints?.length) {
        ctx.strokeStyle = color("route");
        ctx.fillStyle = color("route");
        ctx.setLineDash([5, 4]);
        ctx.beginPath();
        let started = false;
        let firstFinite = null;
        let finiteCount = 0;
        for (const pt of route.waypoints) {
          if (!finitePoint(pt)) continue;
          const [px, py] = project(pt);
          if (!started) {
            ctx.moveTo(px, py);
            firstFinite = pt;
            started = true;
          } else {
            ctx.lineTo(px, py);
          }
          finiteCount += 1;
        }
        if (finiteCount > 2 && firstFinite) {
          ctx.closePath();
        }
        if (started) {
          ctx.stroke();
        }
        ctx.setLineDash([]);
        for (const pt of route.waypoints) {
          if (!finitePoint(pt)) continue;
          const [px, py] = project(pt);
          ctx.fillRect(px - 2, py - 2, 4, 4);
        }
      }
      const actorPos = actorByEntity.get(route.entity)?.position;
      const linePoints = [];
      if (finitePoint(actorPos)) {
        linePoints.push(actorPos);
      }
      for (const pt of route.path ?? []) {
        if (finitePoint(pt)) {
          linePoints.push(pt);
        }
      }
      if (finitePoint(route.target)) {
        linePoints.push(route.target);
      }
      if (linePoints.length >= 2) {
        ctx.strokeStyle = color("path");
        ctx.beginPath();
        let started = false;
        for (const pt of linePoints) {
          const [px, py] = project(pt);
          if (!started) {
            ctx.moveTo(px, py);
            started = true;
          } else {
            ctx.lineTo(px, py);
          }
        }
        if (started) {
          ctx.stroke();
        }
      }
      if (finitePoint(route.target)) {
        const [tx, ty] = project(route.target);
        ctx.strokeStyle = color("path");
        ctx.beginPath();
        ctx.moveTo(tx, ty - 4);
        ctx.lineTo(tx + 4, ty);
        ctx.lineTo(tx, ty + 4);
        ctx.lineTo(tx - 4, ty);
        ctx.closePath();
        ctx.stroke();
      }
    }

    for (const cand of navigation.spawn_candidates ?? []) {
      if (!finitePoint(cand.origin) || !finitePoint(cand.candidate)) continue;
      const tint = color(cand.accepted ? "spawn-ok" : "spawn-no");
      const end = finitePoint(cand.landed) ? cand.landed : cand.candidate;
      const [ox, oy] = project(cand.origin);
      const [cx, cy] = project(end);
      ctx.strokeStyle = tint;
      ctx.fillStyle = tint;
      ctx.globalAlpha = .7;
      ctx.beginPath();
      ctx.moveTo(ox, oy);
      ctx.lineTo(cx, cy);
      ctx.stroke();
      ctx.globalAlpha = 1;
      if (cand.accepted) {
        ctx.beginPath();
        ctx.arc(cx, cy, 2.5, 0, 2 * Math.PI);
        ctx.fill();
      } else {
        ctx.beginPath();
        ctx.moveTo(cx - 3, cy - 3);
        ctx.lineTo(cx + 3, cy + 3);
        ctx.moveTo(cx + 3, cy - 3);
        ctx.lineTo(cx - 3, cy + 3);
        ctx.stroke();
      }
    }
  }
}

function drawGrid(ctx, { min, max, vertical, scale, x, y }) {
  const step = Math.pow(10, Math.ceil(Math.log10(28 / scale)));
  const left = x(min[0]);
  const right = x(max[0]);
  const top = Math.min(y(min[vertical]), y(max[vertical]));
  const bottom = Math.max(y(min[vertical]), y(max[vertical]));
  ctx.beginPath();
  ctx.strokeStyle = color("edge");
  for (let value = Math.ceil(min[0] / step) * step; value < max[0]; value += step) {
    ctx.moveTo(x(value), top);
    ctx.lineTo(x(value), bottom);
  }
  for (let value = Math.ceil(min[vertical] / step) * step; value < max[vertical]; value += step) {
    ctx.moveTo(left, y(value));
    ctx.lineTo(right, y(value));
  }
  ctx.stroke();
  ctx.strokeRect(left, top, right - left, bottom - top);
  ctx.font = "11px monospace";
  ctx.fillStyle = color("muted");
  ctx.fillText(step + " m grid", left + 5, top + 13);
}

function setCenterInputs(center) {
  center.forEach((value, axis) => {
    centerInputs[axis].value = String(value);
  });
}

async function loadGeometry(route, resetCenter = false) {
  if (geometryLoading) {
    return;
  }
  geometryLoading = true;
  geometryMessage = "Loading collision sample.";
  render();
  try {
    geometry = await getJson(route);
    if (resetCenter || centerInputs.every(({ value }) => value === "")) {
      setCenterInputs(geometry.origin);
    }
    geometryMessage = "";
  } catch (error) {
    geometryMessage = "Collision sample unavailable (" + error.message + ")."
      + (geometry ? " Showing the previous sample." : "");
  } finally {
    geometryLoading = false;
    render();
  }
}

function sampleRegion(center) {
  if (center.every(Number.isFinite) && halfExtent.reportValidity()) {
    void loadGeometry("geometry?center=" + center.join(",") + "&half_extent=" + halfExtent.valueAsNumber);
  }
}

async function poll() {
  if (!geometry && !geometryLoading) {
    void loadGeometry("geometry");
  }
  try {
    snapshot = await getJson("state");
    connection.dataset.state = "live";
    setText(
      connection,
      snapshot?.control_enabled ? "Live · room control enabled" : "Live · read-only",
    );
  } catch {
    connection.dataset.state = "stale";
    setText(connection, snapshot ? "Stale · last available state" : "Server state unavailable");
  } finally {
    render();
    window.setTimeout(poll, pollDelay);
  }
}

pauseButton.addEventListener("click", () => void sendRoomControl("pause"));
resumeButton.addEventListener("click", () => void sendRoomControl("resume"));
stepButton.addEventListener("click", () => {
  if (stepInput.reportValidity()) {
    void sendRoomControl("step", stepInput.valueAsNumber);
  }
});
byId("geometry-form").addEventListener("submit", (event) => {
  event.preventDefault();
  sampleRegion(centerInputs.map(({ valueAsNumber }) => valueAsNumber));
});
sampleActor.addEventListener("click", () => {
  const actor = selectedRoom()?.actors.find(({ entity }) => String(entity) === actorSelect.value);
  if (actor) {
    setCenterInputs(actor.position);
    sampleRegion(actor.position);
  }
});
sampleSpawn.addEventListener("click", () => void loadGeometry("geometry", true));
roomSelect.addEventListener("change", render);
vmEntitySelect.addEventListener("change", render);
for (const layer of layers) {
  layer.addEventListener("change", render);
}
const resize = new ResizeObserver(() => {
  const room = selectedRoom();
  for (const view of views) {
    renderView(view, matchingGeometry(room), room);
  }
});
for (const { canvas } of views) {
  resize.observe(canvas);
}
void poll();
