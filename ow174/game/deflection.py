"""D.Va Matrix interception reconstruction; see docs/DVA_MATRIX.txt for limits.

Bounds and tags come from authored inputs. The native shared tag predicate
verifies the exclusion slot used for tag145. Eye attachment, aim orientation
and the contact path are reconstructed.
Unknown projectile metadata never silently becomes an absorbable attack.
"""

import json
import math
import os
import re
from functools import cache
from pathlib import Path

from ow174.game.mover import REPEAT
from ow174.game.script import expr, nodes
from ow174.game.script.expr import Vec3

BUILD = "1.74.0.0.104319"
EXCLUDED_TAG = 0x0D60000000000145
_path = os.environ.get("ORPHEUS_PROJECTILE_TAGS")
TAGS_PATH = Path(_path).resolve() if _path else None


def read_tags(path):
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if data.get("version") != 1 or data.get("build") != BUILD:
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


@cache
def tags():
    return read_tags(TAGS_PATH) if TAGS_PATH is not None else {}


def absorbable(definition):
    known = tags().get(definition)
    return known is not None and EXCLUDED_TAG not in known


def ray_box(origin, direction, lower, upper, reach):
    """First intersection of a bounded ray with a local axis-aligned box."""
    near, far = 0.0, reach
    for point, velocity, low, high in zip(origin, direction, lower, upper, strict=True):
        # D6EF90 applies float32 epsilon to local segment displacement.
        if abs(velocity * reach) < 2**-23:
            if point < low or point > high:
                return None
            continue
        a, b = (low - point) / velocity, (high - point) / velocity
        near, far = max(near, min(a, b)), min(far, max(a, b))
        if near > far:
            return None
    return near


def matrix_distance(script, origin, direction, reach, anchor, forward, *, visible=None, at=None, radius=0.0):
    """Distance to active authored02CC/st3 boxes in a reconstructed aim frame.

    The caller supplies the live mech VM, eye anchor and normalized aim.
    Death, team, static cover and nearer body ordering belong to Combat.
    """
    contact = _box_contact(script, origin, direction, reach, anchor, forward, 0x02CC, 3,
                           visible=visible, at=at, radius=radius)
    return contact[0] if contact is not None else None


def matrix_active(script):
    return next(_active_states(script, 0x02CC, 3), None) is not None


def matrix_abilities(script):
    for volume in _active_states(script, 0x02CC, 3):
        ability = volume.instance.states.get(0)
        if isinstance(ability, nodes.Ability) and ability.remaining() == 0:
            yield ability, volume


def genji_contact(script, origin, direction, reach, anchor, forward, *, visible=None, radius=0.0):
    return _box_contact(script, origin, direction, reach, anchor, forward, 0x03A3, 10,
                        visible=visible, radius=radius)


def reflected_projectile(state, source, amount):
    """Send the accepted physical reflection through the authored output."""
    instance = state.instance
    instance.write(expr.lvalue(state.node.field("m_F6CDC81C")), expr.f32(amount))
    instance.write(expr.lvalue(state.node.field("m_0A2CABA3")), expr.Entity(source))
    instance.follow(state.node, "m_160B5DBE", state)
    instance.component.run_now()


def grasp_contact(script, origin, direction, reach, anchor, forward, *, visible=None, radius=0.0):
    return _box_contact(script, origin, direction, reach, anchor, forward, 0x1D02, 1,
                        visible=visible, radius=radius)


def _active_states(script, graph_id, state_index):
    if (script is None or getattr(script, "retired", False)
            or script.component.owner_dead or script.component.fault is not None):
        return
    for instance in script.component.instances.values():
        if instance.graph.index != graph_id:
            continue
        state = instance.states.get(state_index)
        if state is not None and state.active and state.node.cls == "STUStatescriptStateDeflectProjectiles":
            yield state


def _faces_source(state, towards_source, forward):
    angle = state.evaluate(state.node.field("m_025D0C39"), watch=False)
    facing = sum(a * b for a, b in zip(towards_source, forward, strict=True))
    return facing > math.cos(math.radians(angle))


def genji_blocks_melee(script, origin, feet, forward):
    # Reconstruct the melee direction from the attacker's horizontal bearing.
    x, z = origin[0] - feet[0], origin[2] - feet[2]
    distance = math.hypot(x, z)
    if distance <= 1e-9:
        return False
    towards_source = (x / distance, 0.0, z / distance)
    return any(_faces_source(state, towards_source, forward) for state in _active_states(script, 0x03A3, 10))


def _matrix_release_at(instance):
    component = instance.component
    ability = instance.states.get(0)
    tap = instance.states.get(26)
    release = None
    if isinstance(ability, nodes.Ability) and ability.held(29):
        # Gap recovery releases held input at the start of the first neutral frame.
        release = component.now + REPEAT * component.frame_ms + 1
    if isinstance(tap, nodes.Wait) and tap.active:
        release = max(release or tap.start, tap.start + tap.ms)
    return release


def matrix_close_before(instance, at):
    """Find a projected natural close before a forced-control event."""
    release = _matrix_release_at(instance)
    if release is not None and release > at:
        release = None
    drain = instance.states.get(45)
    end = at if release is None else release
    if not isinstance(drain, nodes.ChaseVar) or not drain.active or not drain.reaches_at(end):
        return release
    # The authored chase is monotonic. Find the first millisecond at zero.
    start = instance.component.now
    while start < end:
        middle = (start + end) // 2
        if drain.reaches_at(middle):
            end = middle
        else:
            start = middle + 1
    return start


def settle_matrix_drain(ability, close, *, natural):
    drain = ability.instance.states.get(45)
    if isinstance(drain, nodes.ChaseVar) and drain.active:
        sample = close
        if natural and not drain.reaches_at(close):
            # A release event exits the chase before its next poll.
            frame_ms = ability.instance.component.frame_ms
            frame_start = close - close % frame_ms
            sample = frame_start - 1 if frame_start == close else frame_start
        drain.advance_to(sample)


def anchor_matrix_close(ability, close, *, recharge):
    if ability.remaining() > 0:
        ability.restart(at=close)
    if recharge:
        wait = ability.instance.states.get(21)
        if isinstance(wait, nodes.Wait) and wait.active and wait.start == ability.now:
            wait.reschedule(close)


def recover_matrix_close(script, before):
    """Settle a projected close in the omitted prefix of a long command gap."""
    for ability, _ in tuple(matrix_abilities(script)):
        instance = ability.instance
        close = matrix_close_before(instance, before)
        if close is None:
            continue
        settle_matrix_drain(ability, close, natural=True)
        ability.presses.clear()
        instance.run_node(instance.graph.node(97), None)
        instance.component.run_now()
        anchor_matrix_close(ability, close, recharge=True)


def recover_matrix_recharge(script, before):
    """Start authored recharge at its deadline if the next VM frame skips it."""
    component = script.component
    for instance in tuple(component.instances.values()):
        if instance.graph.index != 0x02CC:
            continue
        wait = instance.states.get(21)
        if not isinstance(wait, nodes.Wait) or not wait.active:
            continue
        deadline = wait.start + wait.ms
        if component.now < deadline < before:
            previous = component.now
            try:
                component.now = deadline
                instance.update()
            finally:
                component.now = previous


def _matrix_closed_at(instance, at):
    release = _matrix_release_at(instance)
    if release is not None and at >= release:
        return True
    drain = instance.states.get(45)
    return isinstance(drain, nodes.ChaseVar) and drain.active and drain.reaches_at(at)


def _box_contact(
    script, origin, direction, reach, anchor, forward, graph_id, state_index, *, visible, at=None, radius=0.0,
):
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
    towards_source = tuple(-v for v in direction)
    for state in _active_states(script, graph_id, state_index):
        if graph_id == 0x02CC and at is not None and _matrix_closed_at(state.instance, at):
            continue
        if graph_id == 0x1D02 and state.ending:
            continue
        angle = state.evaluate(state.node.field("m_025D0C39"), watch=False)
        # D5E8B0 compares incidence with cos(authored angle), including equality.
        threshold = 0.0 if angle == 90 else math.cos(math.radians(angle))
        facing = sum(a * b for a, b in zip(towards_source, forward, strict=True))
        if facing < threshold:
            continue
        for box in state.node.field("m_boxes") or ():
            low = state.evaluate(box.get("m_CBA118B2"), watch=False)
            high = state.evaluate(box.get("m_AEF67365"), watch=False)
            if (not isinstance(low, Vec3) or not isinstance(high, Vec3)
                    or any(not math.isfinite(v) for v in (*low, *high))):
                raise ValueError("invalid authored deflection box")
            # Native DeflectProjectiles sorts each axis before registering the box.
            corners = tuple(zip(low, high, strict=True))
            # D715D6 adds the projectile's authored radius to all three extents.
            low = tuple(min(pair) - radius for pair in corners)
            high = tuple(max(pair) + radius for pair in corners)
            hit = ray_box(local_origin, local_direction, low, high, reach)
            if (hit is not None and (best is None or hit < best[0])
                    and (visible is None or visible(hit))):
                best = hit, state
    return best
