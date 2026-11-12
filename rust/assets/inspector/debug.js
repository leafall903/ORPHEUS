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
const layers = ["triangles", "proxies", "actors"].map(byId);
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
let geometryLoading = false;
let geometryMessage = "";

async function getJson(route) {
  const response = await fetch("/api/debug/v1/" + route, {
    signal: AbortSignal.timeout(2000),
  });
  if (!response.ok) {
    throw new Error("HTTP " + response.status);
  }
  return response.json();
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

  renderGeometryControls(geometry && !sample ? [] : actors);
  renderTiming(snapshot?.timing);
  renderFaults(snapshot?.faults);
  renderActors(actors);
  for (const view of views) {
    renderView(view, sample, actors);
  }
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

function renderView({ canvas, vertical, up }, sample, actors) {
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
    setText(connection, "Live · read-only");
  } catch {
    connection.dataset.state = "stale";
    setText(connection, snapshot ? "Stale · last available state" : "Server state unavailable");
  } finally {
    render();
    window.setTimeout(poll, pollDelay);
  }
}

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
for (const layer of layers) {
  layer.addEventListener("change", render);
}
const resize = new ResizeObserver(() => {
  const room = selectedRoom();
  for (const view of views) {
    renderView(view, matchingGeometry(room), room?.actors ?? []);
  }
});
for (const { canvas } of views) {
  resize.observe(canvas);
}
void poll();
