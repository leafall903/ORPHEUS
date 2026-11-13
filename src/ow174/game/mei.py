"""Explicit Mei-only reconstruction adapters, not generic native State semantics.

104319 graph05FE's unspecified54FEB21C gate otherwise stalls forever in the
permissive body VM. Complete that one gate immediately in the server model so
the unchanged authored duration, cancel and cooldown branches can execute.
Original-client timing/presentation and the native meaning remain unaccepted.
"""

import math
from dataclasses import dataclass, field

from ow174.game.script import expr

HERO = 0x02E00000000000DD
CRYO = 0x05FE
SPRAY_LOADOUT = 0x0B900000000000D3
# Actual104319 0B77/node31 and05E7/node28, inspected outside the source tree.
FROST_STEP, FROST_INITIAL = expr.f32(0.0285), expr.f32(0.2)
FROST_GAP_MS, FREEZE_MS = 1000, 1300


@dataclass
class CryoHealing:
    """Integrate sampled VM healing over the combat status history."""

    activation: tuple
    at: float
    accounted: float = 0.
    statuses: list[tuple[float, ...]] = field(default_factory=list)

    def note(self, at, blocked_until, boosted_until, received=1.):
        row = (at, blocked_until, boosted_until) if received == 1. else (at, blocked_until, boosted_until, received)
        if self.statuses and at >= self.statuses[-1][0] and self.statuses[-1][1:] == row[1:]:
            return
        self.statuses[:] = [old for old in self.statuses if old[0] != at]
        self.statuses.append(row)
        self.statuses.sort(key=lambda old: old[0])

    def sample(self, cumulative, rate, tick, boost):
        amount = cumulative - self.accounted
        if amount <= 0.:
            return 0.
        span = amount / rate
        count = max(1, round(span / tick))
        period = span / count
        total, status = 0., 0
        for index in range(1, count + 1):
            pulse = self.at + index * period + 1e-9
            while status + 1 < len(self.statuses) and self.statuses[status + 1][0] <= pulse:
                status += 1
            row = self.statuses[status]
            blocked_until, boosted_until = row[1:3]
            if pulse >= blocked_until:
                received = row[3] if len(row) > 3 else 1.
                total += received * (boost if pulse < boosted_until else 1.)
        self.statuses[:] = self.statuses[status:]
        self.at += span
        self.accounted = cumulative
        return amount * total / count


def frost_immune(actor):
    """0B77/node15 retires frost for these actual entity predicates.

    This only consumes their frost policy; it does not infer generic damage
    immunity or native tag/foreign-setting semantics from their identifiers.
    """
    component = getattr(getattr(actor, 'body_script', None), 'component', None)
    if component is None:
        return False
    return any((var := component.entity_var(key, create=False)) is not None and expr.truthy(var.value())
               for key in (6604, 8440, 9161))


def frost_view(sample, now_ms):
    """Reconstructed aggregate frost lifetime; no hidden wall-clock timers."""
    if sample is None:
        return None
    _progress, touched, frozen_until, _yaw, _pitch = sample
    if frozen_until:
        return sample if now_ms < frozen_until else None
    return sample if now_ms < touched + FROST_GAP_MS else None


def frost_hit(sample, now_ms, yaw, pitch, *, initial=FROST_INITIAL, step=FROST_STEP, freeze_ms=FREEZE_MS):
    sample = frost_view(sample, now_ms)
    if sample is not None and sample[2] > now_ms:
        return sample  # Explicit reconstruction: an active freeze is not extended by hits.
    progress = sample[0] if sample is not None else 0.0
    progress = min(1.0, max(initial, expr.f32(progress + step)))
    return progress, now_ms, now_ms + freeze_ms if progress >= 1.0 else 0, yaw, pitch


def projectile_target_limit(state) -> int:
    """Reconstruct spray's99-target field; do not infer other weapons' policy."""
    if state.instance.graph.index != 0x05E6 or state.index != 13:
        return 1
    config = state.node.field("m_6396149F") or {}
    value = config.get("m_B4454CD7")
    return max(1, min(99, expr.to_int(state.evaluate(value, watch=False)))) if value else 1


def command_edges(body, edges):
    """Mei-only reconstruction of the engine's automatic reload button.

    Keep the actual reload graph/timer/ammo writers. A dry clip (or fewer than
    ten rounds when requesting an icicle) supplies one logical reload tap.
    The current body VM otherwise waits forever for a manual reload press.
    """
    if body.hero != HERO or body.component.owner_dead:
        return edges
    edges = _hack_edges(body, edges)
    held = set(body.component.held)
    for button, pressed in edges:
        held.add(button) if pressed else held.discard(button)
    if any(pressed and button in (14, 35) for button, pressed in edges):
        return edges
    frost = body.component.entity_var(2427, create=False)
    if frost is not None and expr.to_float(frost.value()) >= 1.0:
        return edges
    for instance in body.component.instances.values():
        if instance.graph.index == CRYO:
            duration = instance.states.get(6)
            if duration is not None and duration.active:
                return edges
    weapon = next((i for i in body.component.instances.values() if i.graph.index == 0x05E6), None)
    if weapon is None:
        return edges
    windup = weapon.states.get(31)
    if windup is not None and windup.active:
        return edges  # Ammo can be reserved during an already valid icicle windup.
    ammo = expr.to_int(weapon.find_var(0, 53).value())
    needed = 10 if 29 in held and 20 not in held else 1
    reloading = weapon.states.get(7)
    if ammo < needed and (reloading is None or not reloading.active):
        return [*edges, (14, True), (14, False)]
    return edges


def permissive_state_enter(state) -> None:
    if (not state.instance.component.strict and state.instance.graph.index == CRYO
            and state.index == 41 and state.node.cls == "STU_54FEB21C"):
        state.request_finish()


def cryo_instance(actor):
    body = getattr(actor, "body_script", None)
    if body is None or getattr(body, "hero", None) != HERO:
        return None
    return next((i for i in body.component.instances.values() if i.graph.index == CRYO), None)


def projectile_clock_origin(actor):
    """Capture this actual body's birth clock; never consult its replacement later."""
    script = getattr(actor, "body_script", None)
    born = getattr(script, "cryo_birth", None)
    if (script is None or script.hero != HERO or script.retired
            or script.component.entity != actor.body or born is None):
        return None
    return (*born, script.frame_ms)


def world_frame(actor, frame):
    """Translate an authored Mei event using this body's immutable birth pair."""
    origin = projectile_clock_origin(actor)
    if origin is None:
        return None
    from ow174.game.combat import TICK

    world_ms = round(TICK * 1000)
    world, body, frame_ms = origin
    delta = (frame - body) * frame_ms
    return world + -(-delta // world_ms)


def view_world_tick(actor, owner_view_frame):
    """Historical target queries use world time, retaining fractional poses."""
    origin = projectile_clock_origin(actor)
    if origin is None:
        return None
    world, body, frame_ms = origin
    return world + (owner_view_frame - body) * frame_ms / 16


def cryo_world_deadline(actor):
    """Original05FE Chase6 lifetime, mapped from this actual body's birth pair."""
    instance = cryo_instance(actor)
    script = getattr(actor, "body_script", None)
    born = getattr(script, "cryo_birth", None)
    if (instance is None or script.retired or script.component.owner_dead
            or script.component.entity != actor.body or born is None):
        return None
    chase = instance.states.get(6)
    if chase is None or not chase.active:
        return None
    lifetime = expr.to_float(chase.evaluate(chase.node.field("m_EB0FC261"), watch=False))
    if not math.isfinite(lifetime) or lifetime <= 0:
        return None
    # Keep the delivered floor in the source body domain before crossing clocks.
    deadline = (chase.start + round(lifetime * 1000)) // script.frame_ms
    return world_frame(actor, deadline)


def cryo_world_expired(actor):
    deadline = cryo_world_deadline(actor)
    match = getattr(getattr(getattr(actor, "body_script", None), "world", None), "match", None)
    return (deadline is not None and match is not None and match.combat.tick >= deadline)


def protected(actor) -> bool:
    instance = cryo_instance(actor)
    state = instance.states.get(6) if instance is not None else None
    return state is not None and state.active


def healing_sample(actor):
    """Authored total200 over duration4 (modified vars6578e/567i).

    Continuous distribution is reconstructed server behavior. Native
    ModifyHealth's mode3/interval scheduling has not been measured.
    """
    instance = cryo_instance(actor)
    state = instance.states.get(13) if instance is not None else None
    if state is None or not state.counter:
        return None
    duration = expr.to_float(instance.find_var(0, 567).value()) * 1000.0
    amount = expr.to_float(instance.component.entity_var(6578).value())
    end = instance.component.now if state.active else state.ended_at
    elapsed = max(0.0, min(duration, end - state.start))
    # ChaseVar's float32 completion can precede its nominal endpoint by 1ms.
    if elapsed >= duration - 1.0:
        elapsed = duration
    if duration <= 0 or amount <= 0:
        return None
    return (instance.id, state.counter, state.start), amount * elapsed / duration, amount * 1000 / duration


def _hack_edges(body, edges):
    """Original Mei edge lock, consuming the existing Hack/EMP owner."""
    match = body.world.match
    if match is None or body.entity not in match.combat.sombra_controls.locks:
        return edges
    for ability in body.ultimates:
        ability.presses.clear()
        ability.stop()
    owner = match._message_player(body.entity)
    if owner is not None:
        match.combat.walls.cancel_preview(owner)
    buttons = frozenset((35, 36, 37, 110, 114, 116))
    releases = [(button, False) for button in body.component.held if button in buttons]
    return releases + [(button, pressed) for button, pressed in edges if button not in buttons]
