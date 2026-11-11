"""Scoped Fusion Driver interception using Genji's authored 03A3 boxes."""

import math

from ow174.game.script import expr

GENJI = 0x02E0000000000029


def box_entry(origin, direction, left, right, length):
    """Return the entry distance along the ray in box coordinates."""
    enter, exit_at = 0.0, length
    for position, delta, a, b in zip(origin, direction, left, right, strict=True):
        low, high = sorted((a, b))
        if abs(delta) < 1e-9:
            if not low <= position <= high:
                return None
            continue
        start, end = sorted(((low - position) / delta, (high - position) / delta))
        enter, exit_at = max(enter, start), min(exit_at, end)
        if enter > exit_at:
            return None
    return enter


def _local(vector, sine, cosine):
    x, y, z = vector
    return x * cosine - z * sine, y, x * sine + z * cosine


def interception(fight, rocket, direction, length):
    """Use a server eye-origin, yaw-aligned pose and a frontal enemy filter."""
    if rocket.source_graph != 0xBD4 or rocket.reflected:
        return None
    best = None
    for target in fight._enemies(rocket.shooter):
        player = target.owner
        script = getattr(player, "body_script", None)
        if script is None or getattr(getattr(player, "hero", None), "guid", None) != GENJI:
            continue
        if fight.is_stunned(player):
            continue
        yaw = player.mover.yaw * math.tau / 65536
        sine, cosine = math.sin(yaw), math.cos(yaw)

        ray = _local(direction, sine, cosine)
        if ray[2] >= 0:
            continue
        offset = tuple(a - b for a, b in zip(rocket.position, fight._eye(player), strict=True))
        origin = _local(offset, sine, cosine)
        for instance in script.component.instances.values():
            if instance.graph.index != 0x03A3:
                continue
            node = instance.graph.node(18)
            state = instance.states.get(node.state)
            if state is None or not state.active:
                continue
            for box in node.field("m_boxes") or ():
                left = state.evaluate(box["m_CBA118B2"], watch=False)
                right = state.evaluate(box["m_AEF67365"], watch=False)
                if not isinstance(left, expr.Vec3) or not isinstance(right, expr.Vec3):
                    continue
                distance = box_entry(origin, ray, left, right, length)
                if distance is not None and (best is None or distance < best[0]):
                    best = (distance, player)
    return best


class _MeleeFilterContext(expr.Context):
    """Bind the authored melee condition to the two server actor poses."""

    def __init__(self, attacker, defender):
        super().__init__()
        self.attacker, self.defender = attacker, defender

    def owner(self):
        return expr.Entity(self.defender.body)

    def param(self, identifier):
        if identifier == 8:
            return expr.Entity(self.attacker.body)
        return None

    def position(self, entity):
        actor = self.attacker if entity == self.attacker.body else self.defender
        return expr.Vec3(*actor.mover.position)

    def native(self, config):
        if config.get("$") == "STU_021D01CD" and expr.evaluate(config.get("m_entity"), self) == self.owner():
            yaw = self.defender.mover.yaw * math.tau / 65536
            return expr.Vec3(expr.f32(math.sin(yaw)), 0, expr.f32(math.cos(yaw)))
        return expr.EVALUATION_FAILED


def blocks_melee(fight, attacker, defender, modify):
    """Apply 03A3 node 107 to an authored quick-melee damage event."""
    if getattr(getattr(defender, "hero", None), "guid", None) != GENJI or fight.is_stunned(defender):
        return False
    script = defender.body_script
    if script is None:
        return False
    tags = {tag["m_EE3055BD"]["m_identifier"] for tag in modify.get("m_148F3152") or ()}
    for instance in script.component.instances.values():
        if instance.graph.index != 0x03A3:
            continue
        node = instance.graph.node(107)
        state = instance.states.get(node.state)
        if state is None or not state.active or tags.isdisjoint(node.field("m_F60AA143") or ()):
            continue
        if state.evaluate(node.field("m_50530EAB"), watch=False) != 0:
            continue
        filter_config = node.field("m_6AA5D70F") or {}
        condition = filter_config.get("m_condition")
        if expr.evaluate(condition, _MeleeFilterContext(attacker, defender)) is True:
            return True
    return False


def _active_states(script, graph_id, state_index):
    if (script is None or getattr(script, "retired", False)
            or script.component.owner_dead or script.component.fault is not None):
        return
    for instance in script.component.instances.values():
        if instance.graph.index != graph_id:
            continue
        state = instance.state(state_index)
        if state is not None and state.active and state.node.cls == "STUStatescriptStateDeflectProjectiles":
            yield state


def matrix_contact(script, origin, direction, reach, anchor, forward, *, visible):
    x, y, z = forward
    length = math.hypot(x, z)
    right = (z / length, 0.0, -x / length) if length > 1e-9 else (1.0, 0.0, 0.0)
    rx, ry, rz = right
    up = (y * rz - z * ry, z * rx - x * rz, x * ry - y * rx)
    basis = (right, up, forward)
    delta = tuple(a - b for a, b in zip(origin, anchor, strict=True))
    local_origin = tuple(sum(a * b for a, b in zip(delta, axis, strict=True)) for axis in basis)
    local_direction = tuple(sum(a * b for a, b in zip(direction, axis, strict=True)) for axis in basis)
    best = None
    for state in _active_states(script, 0x02CC, 3):
        for box in state.node.field("m_boxes") or ():
            low = state.evaluate(box.get("m_CBA118B2"), watch=False)
            high = state.evaluate(box.get("m_AEF67365"), watch=False)
            if (not isinstance(low, expr.Vec3) or not isinstance(high, expr.Vec3)
                    or any(not math.isfinite(v) for v in (*low, *high))):
                raise ValueError("invalid authored deflection box")
            hit = box_entry(local_origin, local_direction, low, high, reach)
            if (hit is not None and (best is None or hit < best[0])
                    and (visible is None or visible(hit))):
                best = hit, state
    return best
