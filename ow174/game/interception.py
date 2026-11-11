"""Authored physical Grasp/Matrix/Deflect with external target-build projectile tags.

Adapted from Genji43f4e1b's Grasp service and DVaee2db6d's tag reader/box policy.
Eye attachment, aim frame, frontal angle and tag145 exclusion are reconstructions.
Temporary shields use a scoped host service; native HealthPool services, filter
profiles and visual transport remain open.
"""

import hashlib
import json
import math
import os
import re
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType

from ow174.game.commands import Command
from ow174.game.script import expr, graph, nodes

BUILD = "1.74.0.0.104319"
EXCLUDED_TAG = 0x0D60000000000145
EPSILON = 1e-9
SHIELD_TAG = 0x5847  # scoped host ownership tag, adapted from Sigmae6a9550


def _parse_tags(data):
    if type(data.get("version")) is not int or data["version"] != 1 or data.get("build") != BUILD:
        raise ValueError("projectile tags require build104319 version1")
    entities = data.get("entities")
    if not isinstance(entities, dict):
        raise ValueError("projectile tag entities must be a mapping")
    result = {}
    for key, tags in entities.items():
        if not re.fullmatch(r"0x0400[0-9A-F]{12}", key):
            raise ValueError("projectile tag key must be a full003 GUID")
        if (not isinstance(tags, list)
                or any(not isinstance(tag, str) or not re.fullmatch(r"0x0D60[0-9A-F]{12}", tag)
                       for tag in tags)
                or len(set(tags)) != len(tags)):
            raise ValueError("invalid projectile target tags")
        result[int(key, 16)] = frozenset(int(tag, 16) for tag in tags)
    return result


def read_tags(path):
    return _parse_tags(json.loads(Path(path).read_bytes()))


_path = os.environ.get("ORPHEUS_PROJECTILE_TAGS")
_blob = Path(_path).read_bytes() if _path else None
_TAGS = MappingProxyType(_parse_tags(json.loads(_blob)) if _blob is not None else {})
CATALOG_SHA256 = hashlib.sha256(_blob).hexdigest() if _blob is not None else None


def catalog_snapshot():
    """Read-only canonical input bytes, without loading files during a stored snapshot."""
    return tuple((key, tuple(sorted(tags))) for key, tags in sorted(_TAGS.items()))


def eligible(definition):
    tags = _TAGS.get(definition)
    return tags is not None and EXCLUDED_TAG not in tags


def ray_box(origin, direction, low, high, length):
    first, last = 0.0, length
    for o, d, lo, hi in zip(origin, direction, low, high, strict=True):
        if abs(d) < EPSILON:
            if not lo <= o <= hi:
                return None
            continue
        a, b = (lo - o) / d, (hi - o) / d
        first, last = max(first, min(a, b)), min(last, max(a, b))
        if first > last:
            return None
    return first


DEFENSE_GRAPHS = {
    0x02E0000000000029: 0x03A3,
    0x02E000000000023B: 0x1D02,
    0x02E000000000007A: 0x02CC,
}
DEFENSE_TIMERS = {0x03A3: (10, 1), 0x1D02: (1, 20), 0x02CC: (3, 26)}
DEFENSE_BUTTONS = {0x03A3: 16, 0x1D02: 8, 0x02CC: 2}


@dataclass(frozen=True)
class DefenseCost:
    sampled_ms: int
    cooldowns: tuple[tuple[tuple[int, int], float, float], ...]
    resource: float | None

    def restore(self, instance):
        if instance.graph.index == 0x03A3:
            # Deflect's node 156 reaches the cooldown write at node 5 after 0.2 s.
            cooldown_wait = instance.states.get(76)
            if isinstance(cooldown_wait, nodes.Wait) and cooldown_wait.active:
                cooldown_wait.end(False)
        elapsed = max(0, instance.component.now - self.sampled_ms) / 1000
        for target, remaining, rate in self.cooldowns:
            instance.write(target, expr.f32(max(0, remaining - elapsed * rate)))
        if self.resource is not None:
            instance.write((graph.INSTANCE, 87), self.resource)


def capture_defense_costs(script):
    from ow174.game.script.driver import EDGE_BITS

    graph_index = DEFENSE_GRAPHS.get(script.hero)
    if graph_index is None:
        return {}
    buttons = {button for bit, button in EDGE_BITS if bit == DEFENSE_BUTTONS[graph_index]}
    costs = {}
    for instance in script.component.instances.values():
        if instance.graph.index != graph_index:
            continue
        cooldowns = []
        for state in instance.states.values():
            if isinstance(state, nodes.Ability) and state.var is not None and buttons.intersection(state.buttons()):
                target = expr.lvalue(state.node.field("m_out_CooldownVar"))
                if target is not None:
                    cooldowns.append((target, state.remaining(), state.cooldown_rate))
        meter = instance.vars.get(87) if graph_index == 0x02CC else None
        resource = meter.value() if meter is not None else None
        costs[instance.id] = DefenseCost(script.component.now, tuple(cooldowns), resource)
    return costs


@dataclass
class DefenseWindow:
    """An authored projectile defense activation on the world clock."""

    state: object
    start: int
    end: int
    expiry_frame: int
    cost: DefenseCost | None = None


def _activation(state):
    return state.instance.id, state.index, state.counter, state.start


def observe(fight, player, script, costs=None):
    """Record world admission of active volumes independently of client command frames."""
    graph_index = DEFENSE_GRAPHS.get(script.hero)
    if graph_index is None:
        return
    starts = fight.projectile_defense_starts
    previous = starts.get(player.body, {})
    current = {}
    for instance in script.component.instances.values():
        if instance.graph.index != graph_index:
            continue
        for state in instance.states.values():
            if state.active and state.node.cls == "STUStatescriptStateDeflectProjectiles":
                key = _activation(state)
                current[key] = previous.get(key, fight.match.tick)
    if graph_index in (0x03A3, 0x02CC, 0x1D02):
        _observe_windows(fight, player.body, script, graph_index, previous, current, costs or {})
    if current:
        starts[player.body] = current
    else:
        starts.pop(player.body, None)


def _expiry_ms(timer):
    if isinstance(timer, nodes.Wait):
        return timer.start + timer.ms
    if isinstance(timer, nodes.ChaseVar):
        remaining = timer.remaining
        if remaining is None:
            rate = expr.to_float(timer.evaluate(timer.node.field("m_9B0385B4"), watch=False))
            if rate <= 0:
                return None
            destination = expr.to_float(timer.evaluate(timer.node.field("m_destination"), watch=False))
            remaining = round(abs(expr.to_float(timer.cur) - destination) * 1000 / rate)
        return timer.last + remaining - 1
    return None


def _window_deadline(script, timer, start, start_ms):
    from ow174.game.combat import TICK

    if timer is None or not timer.active or (until := _expiry_ms(timer)) is None:
        return None
    frame = until // script.frame_ms
    elapsed = frame * script.frame_ms - start_ms
    return start + math.ceil(elapsed / (TICK * 1000)) + 1, frame


def _observe_windows(fight, body, script, graph_index, previous, current, costs):

    windows = fight.projectile_defense_windows.setdefault(body, {})
    for key in previous.keys() - current.keys():
        window = windows.get(key)
        # A manual exit can shorten the wait. A completed wait keeps its world deadline.
        if window is not None and script.frame < window.expiry_frame:
            window.end = min(window.end, fight.match.tick)
    matrix = graph_index == 0x02CC
    for key, start in current.items():
        window = windows.get(key)
        if window is not None and (not matrix or window.end <= fight.match.tick):
            continue
        instance_id, volume_index, _, start_ms = key
        instance = script.component.instances[instance_id]
        timer_index = DEFENSE_TIMERS[graph_index][1]
        deadline = _window_deadline(script, instance.states.get(45 if matrix else timer_index), start, start_ms)
        if matrix and not script.buttons & 2:
            minimum = _window_deadline(script, instance.states.get(timer_index), start, start_ms)
            if minimum is not None and fight.match.tick < minimum[0]:
                deadline = min(deadline, minimum) if deadline is not None else minimum
        if deadline is not None:
            if window is None:
                windows[key] = DefenseWindow(instance.states[volume_index], start, *deadline,
                                             cost=costs.get(instance_id))
            else:
                window.end, window.expiry_frame = deadline
    _prune_defense_body(fight, body, current, fight.tick)


def _prune_defense_body(fight, body, active, tick):
    windows = fight.projectile_defense_windows.get(body, {})
    for key in tuple(windows):
        if windows[key].end <= tick and key not in active:
            del windows[key]
    if not windows:
        fight.projectile_defense_windows.pop(body, None)


def prune_defense_windows(fight):
    for body in tuple(fight.projectile_defense_windows):
        _prune_defense_body(fight, body, fight.projectile_defense_starts.get(body, {}), fight.tick)


def admission_edges(fight, tick):
    admissions = {}
    for body, windows in fight.projectile_defense_windows.items():
        for window in windows.values():
            if fight.tick < window.start <= tick and window.start < window.end:
                admissions.setdefault(window.start, set()).add(body)
    return admissions


def _close_window(fight, body, key, window, cutoff):
    window.end = cutoff
    if window.state.instance.graph.index == 0x1D02:
        fight.grasp_health.cancel_pending(body, key)


def reject_sleep_admissions(fight, body, tick):
    """Reject casts that are still under Sleep when their world input becomes due."""
    from ow174.game.script.driver import button_edges

    for key, window in fight.projectile_defense_windows.get(body, {}).items():
        if window.start != tick or tick >= window.end:
            continue
        state = window.state
        _close_window(fight, body, key, window, tick)
        if key != _activation(state):
            continue
        instance = state.instance
        component = instance.component
        component.edges(button_edges(DEFENSE_BUTTONS[instance.graph.index], 0))
        for ability in list(instance.states.values()):
            if isinstance(ability, nodes.Ability):
                ability.presses.clear()
                ability.stop()
        component.run_now()
        if window.cost is not None:
            window.cost.restore(instance)
            component.run_now()


def interrupt_windows(fight, body, tick, until=None):
    """Close affected windows and preserve live input for a cast admitted after recovery."""
    from ow174.game.script.driver import button_edges

    preserved, held, refunds = set(), set(), []
    for key, window in fight.projectile_defense_windows.get(body, {}).items():
        state = window.state
        graph_index = state.instance.graph.index
        if tick < window.start:
            if until is None:
                continue
            if window.start >= until:
                if state.active and key == _activation(state):
                    preserved.add(state.instance)
                    held.update(button for button, _ in button_edges(0, DEFENSE_BUTTONS[graph_index]))
                continue
        cutoff = max(tick, window.start)
        if cutoff < window.end:
            _close_window(fight, body, key, window, cutoff)
            if tick < window.start and window.cost is not None and key == _activation(state):
                refunds.append((state.instance, window.cost))
    return preserved, held, refunds


def _volume_states(fight, target, script, graph_index, mode, tick):
    starts = fight.projectile_defense_starts.get(target.entity, {}) if fight is not None else {}
    windows = (fight.projectile_defense_windows.get(target.entity, {})
               if fight is not None and tick is not None else {})
    for instance in script.component.instances.values():
        if instance.graph.index != graph_index:
            continue
        for state in instance.states.values():
            if (not state.active or state.node.cls != "STUStatescriptStateDeflectProjectiles"
                    or state.node.field("m_8B251D53") != mode):
                continue
            key = _activation(state)
            if key not in windows and (tick is None or starts.get(key, tick) <= tick):
                yield state
    for window in windows.values():
        if (window.start <= tick < window.end and window.state.instance.graph.index == graph_index
                and window.state.node.field("m_8B251D53") == mode):
            yield window.state


def timed_expiries(fight):
    """Map the active defense timers to the world clock that admitted them."""
    from ow174.game.combat import TICK
    from ow174.game.projectiles import MAX_LEASE_FRAME

    for player in fight.match.players:
        starts = fight.projectile_defense_starts.get(player.body)
        script = player.body_script
        if not starts or script is None or script.retired or player.gone or fight.is_dead(player):
            continue
        graph_index = DEFENSE_GRAPHS.get(script.hero)
        if graph_index is None:
            continue
        volume_index, timer_index = DEFENSE_TIMERS[graph_index]
        if graph_index == 0x02CC and script.buttons & 2:
            timer_index = 45  # Held Matrix exits when its resource drains.
        for instance in script.component.instances.values():
            if instance.graph.index != graph_index:
                continue
            volume, timer = instance.states.get(volume_index), instance.states.get(timer_index)
            if volume is None or not volume.active or timer is None or not timer.active:
                continue
            start = starts.get(_activation(volume))
            until = _expiry_ms(timer)
            if start is None or until is None:
                continue
            frame = until // script.frame_ms
            if script.frame <= frame < MAX_LEASE_FRAME:
                elapsed = frame * script.frame_ms - volume.start
                yield player, volume, frame, start + math.ceil(elapsed / (TICK * 1000))


def advance_expiries(fight, expiries):
    for owner, volume, frame, tick in expiries:
        script = owner.body_script
        if (fight.tick > tick and volume.active and script is not None and not script.retired
                and script.component is volume.instance.component and not fight.match.ended
                and not owner.gone and not fight.is_dead(owner)):
            # Give real input the expiry frame. One more frame covers chase rounding.
            last = frame + min(1, fight.tick - tick - 1)
            if volume.instance.graph.index == 0x02CC and script.buttons & 2:
                # Preserve the admitted hold so the authored depletion branch runs.
                if script.frame < last:
                    yaw, pitch = script.world.aim
                    forward, right = script.world.throttles
                    command = Command(last, forward=forward, right=right,
                                      yaw=yaw, pitch=pitch, buttons=script.buttons)
                    owner._advance_body_frame(command, posed=False)
            else:
                owner._advance_body_gap(last + 1)


def _admission(fight, shooter, definition, origin, direction, length, *, team, graph_index, mode, radius):
    """Nearest authored box; eye frame and corner normalization are host policy."""
    if not eligible(definition):
        return None
    best = None
    for target in fight._enemies(shooter, team=team):
        if fight.replacement_ticks.get(target.entity, 0) > fight.tick:
            continue
        owner = target.owner
        mover = getattr(owner, "mover", None)
        script = getattr(owner, "body_script", None)
        if mover is None or script is None or script.retired:
            continue
        if graph_index == 0x02CC:
            hero = getattr(owner, "hero", None)
            if (getattr(hero, "guid", None) != 0x02E000000000007A
                    or getattr(hero, "body", None) != 0x0400000000000407):
                continue
        yaw, pitch = mover.yaw * math.tau / 65536, mover.pitch * math.tau / 65536
        sy, cy, sp, cp = math.sin(yaw), math.cos(yaw), math.sin(pitch), math.cos(pitch)
        basis = ((cy, 0, -sy), (sy * sp, cp, cy * sp), (sy * cp, -sp, cy * cp))
        eye = fight._eye(owner)
        delta = tuple(a - b for a, b in zip(origin, eye, strict=True))
        local_origin = tuple(sum(a * b for a, b in zip(delta, axis, strict=True)) for axis in basis)
        local_direction = tuple(sum(a * b for a, b in zip(direction, axis, strict=True)) for axis in basis)
        # Genji's90-degree admission is reconstructed from the authored angle;
        # side/reversed-corner boxes alone also intersect some rear approaches.
        if mode == 0 and local_direction[2] >= -EPSILON:
            continue
        for state in _volume_states(fight, target, script, graph_index, mode, fight.tick):
            for box in state.node.field("m_boxes") or ():
                low = state.evaluate(box.get("m_CBA118B2"), watch=False)
                high = state.evaluate(box.get("m_AEF67365"), watch=False)
                if (not isinstance(low, expr.Vec3) or not isinstance(high, expr.Vec3)
                        or any(not math.isfinite(v) for v in (*low, *high))):
                    raise ValueError("invalid authored projectile defense box")
                low, high = (tuple(min(a, b) - radius for a, b in zip(low, high, strict=True)),
                             tuple(max(a, b) + radius for a, b in zip(low, high, strict=True)))
                distance = ray_box(local_origin, local_direction, low, high, length)
                if distance is not None and (best is None or distance < best[0]):
                    best = distance, state, target
    return best


def absorption(fight, shooter, definition, origin, direction, length, *, team, radius=0.0):
    """Nearest active Grasp/Matrix, using the emitted projectile's frozen team."""
    found = None
    for graph_index in (0x1D02, 0x02CC):
        candidate = _admission(fight, shooter, definition, origin, direction, length,
                               team=team, graph_index=graph_index, mode=1, radius=radius)
        if candidate is not None and (found is None or candidate[0] < found[0]):
            found = candidate
    return found[:2] if found is not None else None


def reflection(fight, shooter, definition, origin, direction, length, *, team, radius=0.0):
    """Definition-tag admission covers Zarya's nondamaging direct-hit definition."""
    return _admission(fight, shooter, definition, origin, direction, length,
                      team=team, graph_index=0x03A3, mode=0, radius=radius)


def blocks_melee(target, incoming, *, tick=None):
    """Reconstructed frontal capsule gate, adapted from Genjid714f0c."""
    owner = target.owner
    script = getattr(owner, "body_script", None)
    mover = getattr(owner, "mover", None)
    if mover is None or script is None or getattr(script, "retired", False):
        return False
    yaw, pitch = mover.yaw * math.tau / 65536, mover.pitch * math.tau / 65536
    facing = (math.sin(yaw) * math.cos(pitch), -math.sin(pitch), math.cos(yaw) * math.cos(pitch))
    if sum(a * b for a, b in zip(facing, incoming, strict=True)) >= -EPSILON:
        return False
    match = getattr(owner, "match", None)
    fight = match.combat if match is not None else None
    return next(_volume_states(fight, target, script, 0x03A3, 0, tick), None) is not None


def absorbed(state, amount, *, fight=None):
    """Write the per-projectile amount and execute the authored accumulation plug."""
    if (fight is not None and state.instance.graph.index == 0x1D02
            and not fight.grasp_health.credit_absorption(state, amount)):
        return
    output = expr.lvalue(state.node.field("m_F6CDC81C"))
    if output is not None:
        state.instance.write(output, expr.f32(amount))
    state.instance.follow(state.node, "m_160B5DBE", state)
    state.instance.component.run_now()


@dataclass
class ShieldDecay:
    starts_tick: float
    last_tick: int
    rate: float


@dataclass
class PendingGraspGrant:
    tick: int
    absorbed: float
    scale: float
    limit: float
    delay: float
    rate: float


class GraspShields:
    """Scoped1D02/node34 ->1D03 reconstruction, adapted from Sigmae6a9550.

    The real PlayScript parameters supply grant/cap;1D02 supplies delay/rate.
    Tagged pools, grant idempotence and authoritative world-clock integration
    are host policy. This does not execute native1D03 HealthPool services.
    """

    def __init__(self, combat):
        self.combat = combat
        self.grants: dict[int, tuple] = {}
        self.decays: dict[int, ShieldDecay] = {}
        self.pending: dict[tuple[int, tuple], PendingGraspGrant] = {}

    def retire(self, entity):
        self.grants.pop(entity, None)
        self.decays.pop(entity, None)
        for key in tuple(self.pending):
            if key[0] == entity:
                del self.pending[key]

    def grant(self, entity, instance, node):
        if instance.graph.index != 0x1D02 or node.pos != 34:
            return False
        if (instance.evaluate(node.field("m_script")) != expr.Asset(0x0580000000001D03)
                or instance.evaluate(node.field("m_targets")) != expr.Entity(entity)):
            return False
        target = next((t for t in self.combat._targets() if t.entity == entity), None)
        if target is None:
            return True
        script = getattr(target.owner, "body_script", None)
        if (instance.component.entity != entity or script is None or script.retired
                or script.component is not instance.component):
            return False
        absorption = instance.states.get(1)
        if absorption is None or absorption.counter == 0:
            return True
        key = _activation(absorption)
        activation = key[0], key[2], key[3]
        if self.grants.get(entity) == activation:
            return True
        vitals = self.combat.vitals_of(target)
        if vitals.dead or self.combat.match.ended:
            return True
        params = {graph.guid(entry["m_B5051BCE"]): entry["m_value"]
                  for entry in node.field("m_params") or ()}
        amount_expr = params[0x0A4000000000002A]
        limit = expr.to_float(instance.evaluate(params[0x0A400000000003D1]))
        delay = expr.to_float(instance.evaluate(instance.graph.node(71).field("m_timeout")))
        modify = instance.graph.node(70).field("m_modifyHealth")
        rate = expr.to_float(instance.evaluate(modify["m_amount"]))
        if not math.isfinite(delay) or not math.isfinite(rate) or delay < 0 or rate < 0:
            raise ValueError("Grasp decay requires finite nonnegative delay and rate")
        window = self.combat.projectile_defense_windows.get(entity, {}).get(key)
        if (window is not None and instance.component.frame >= window.expiry_frame
                and self.combat.tick < window.end - 1):
            # Node 34 multiplies node 32's per-activation accumulator by the authored scale.
            expression = amount_expr["m_expression"]
            accumulator = expression["m_dynamicVars"][0]
            total = expr.to_float(instance.evaluate(accumulator))
            scale = expr.to_float(expression["m_D99EF254"][0])
            self.pending.setdefault((entity, activation),
                                    PendingGraspGrant(window.end - 1, total, scale, limit, delay, rate))
        else:
            self._apply(target, activation, expr.to_float(instance.evaluate(amount_expr)), limit, delay, rate)
        return True

    def credit_absorption(self, state, amount):
        entity = state.instance.component.entity
        for key, window in self.combat.projectile_defense_windows.get(entity, {}).items():
            if window.state is not state or not window.start <= self.combat.tick < window.end:
                continue
            activation = key[0], key[2], key[3]
            grant = self.pending.get((entity, activation))
            if grant is not None:
                grant.absorbed = expr.f32(grant.absorbed + expr.f32(amount))
            # An earlier activation cannot write the current instance's accumulator.
            return key == _activation(state)
        return True

    def cancel_pending(self, entity, key):
        self.pending.pop((entity, (key[0], key[2], key[3])), None)

    def grant_edges(self, tick):
        return {grant.tick for grant in self.pending.values() if self.combat.tick < grant.tick <= tick}

    def advance_grants(self):
        due = [key for key, grant in self.pending.items() if grant.tick <= self.combat.tick]
        if not due:
            return
        targets = {target.entity: target for target in self.combat._targets()}
        for entity, activation in due:
            grant = self.pending.pop((entity, activation))
            target = targets.get(entity)
            if target is not None and self.grants.get(entity) != activation:
                amount = expr.f32(grant.absorbed * grant.scale)
                self._apply(target, activation, amount, grant.limit, grant.delay, grant.rate)

    def _apply(self, target, activation, amount, limit, delay, rate):
        entity = target.entity
        vitals = self.combat.vitals_of(target)
        if vitals.dead or self.combat.match.ended:
            return
        self.grants[entity] = activation
        if vitals.grant_temporary_shields(SHIELD_TAG, amount, limit) > 0:
            from ow174.game.combat import TICK

            now = self.combat.tick
            self.decays[entity] = ShieldDecay(now + delay / TICK, now, rate)
            self.combat._sync_body_health(target.owner, vitals)

    def update(self, tick):
        from ow174.game.combat import TICK

        owners = {t.entity: t.owner for t in self.combat._targets()}
        for entity in set(self.grants) | set(self.decays):
            vitals = self.combat.vitals.get(entity)
            if vitals is None or vitals.dead or entity not in owners or self.combat.match.ended:
                if vitals is not None and self.combat.match.ended:
                    pool = vitals.temporary_shields.get(SHIELD_TAG)
                    if (pool is not None and vitals.decay_temporary_shields(SHIELD_TAG, pool.now) > 0
                            and entity in owners):
                        self.combat._sync_body_health(owners[entity], vitals)
                self.retire(entity)
        for entity, decay in list(self.decays.items()):
            vitals = self.combat.vitals[entity]
            seconds = max(0.0, tick - max(decay.last_tick, decay.starts_tick)) * TICK
            decay.last_tick = max(decay.last_tick, tick)
            if vitals.decay_temporary_shields(SHIELD_TAG, seconds * decay.rate) > 0:
                self.combat._sync_body_health(owners[entity], vitals)
            if vitals.temporary_shields[SHIELD_TAG].now <= 0:
                del self.decays[entity]
