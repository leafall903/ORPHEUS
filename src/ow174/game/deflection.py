"""Selected Genji03A3 host; native geometry/filter/ownership services remain open.

Endpoint ordering follows the bounded native comparison. Eye/aim attachment,
0145 exclusion, angle interpretation and outgoing aim are reconstruction policy.
Unknown003 metadata is never admitted by guessing from a health amount/tag.
"""

import hashlib
import json
import math
import os
import re
from dataclasses import dataclass
from functools import cache
from pathlib import Path
from types import MappingProxyType

from ow174.game.script import expr, runtime
from ow174.paths import DATA_DIR
from ow174.game.projectile_tags import BUILD

GENJI = 0x02E0000000000029
EXCLUDED_TAG = 0x0D60000000000145
MAX_DEFLECTIONS = 8  # explicit bounded host cycle policy, not a recovered native limit
TICK = .016


def reflected_projectile(state, source, amount):
    """Send the accepted physical reflection through the authored output."""
    instance = state.instance
    instance.write(expr.lvalue(state.node.field("m_F6CDC81C")), expr.f32(amount))
    instance.write(expr.lvalue(state.node.field("m_0A2CABA3")), expr.Entity(source))
    instance.follow(state.node, "m_160B5DBE", state)
    instance.component.run_now()


@cache
def _rows(blob):
    data = json.loads(blob)
    if data.get('version') != 1 or data.get('build') != '1.74.0.0.104319':
        raise ValueError('projectile catalog requires104319 version1')
    entities = data.get('entities')
    if not isinstance(entities, dict):
        raise ValueError('projectile catalog requires an entity mapping')
    rows = {}
    for key, tags in entities.items():
        if (not isinstance(key, str) or not re.fullmatch(r'0x0400[0-9A-F]{12}', key)
                or not isinstance(tags, list)
                or any(not isinstance(tag, str) or not re.fullmatch(r'0x0D60[0-9A-F]{12}', tag)
                       for tag in tags) or len(tags) != len(set(tags))):
            raise ValueError('invalid projectile catalog row')
        rows[int(key, 16)] = frozenset(int(tag, 16) for tag in tags)
    return MappingProxyType(rows)


@dataclass(frozen=True)
class ProjectileTags:
    blob: bytes | None

    def __post_init__(self):
        if self.blob is not None:
            if type(self.blob) is not bytes:
                raise ValueError('projectile catalog requires immutable bytes')
            _rows(self.blob)

    def admitted(self, definition):
        tags = _rows(self.blob).get(definition) if self.blob is not None else None
        return tags is not None and EXCLUDED_TAG not in tags

    def absorbable(self, definition):
        """Delivered Matrix query over this same immutable0145 tag catalog."""
        return self.admitted(definition)


_path = os.environ.get('ORPHEUS_PROJECTILE_TAGS')
_path = Path(_path) if _path else DATA_DIR / 'projectile_tags_174.json'
TAGS_PATH = _path  # Original input identity; queries keep using immutable catalog bytes.
_CATALOG = ProjectileTags(_path.read_bytes() if _path.is_file() else None)
if os.environ.get('ORPHEUS_PROJECTILE_TAGS') and _CATALOG.blob is None:
    raise FileNotFoundError(_path)
CATALOG_SHA256 = hashlib.sha256(_CATALOG.blob).hexdigest() if _CATALOG.blob is not None else None


def catalog():
    return _CATALOG


def basis(yaw, pitch):
    yaw, pitch = yaw * math.tau / 65536, pitch * math.tau / 65536
    sy, cy, sp, cp = math.sin(yaw), math.cos(yaw), math.sin(pitch), math.cos(pitch)
    return (cy, 0, -sy), (sy * sp, cp, cy * sp), (sy * cp, -sp, cy * cp)


def box_hit(origin, direction, first, second, reach):
    enter, leave = 0.0, reach
    for point, axis, low, high in zip(origin, direction, first, second, strict=True):
        if not all(math.isfinite(v) for v in (point, axis, low, high)):
            return None
        if low > high:  # original COMISS/JBE ordering, including reversed side boxes
            low, high = high, low
        if abs(axis) < 1e-12:
            if point < low or point > high:
                return None
            continue
        a, b = (low - point) / axis, (high - point) / axis
        enter, leave = max(enter, min(a, b)), min(leave, max(a, b))
        if enter > leave:
            return None
    return enter


def window(instance, at_tick):
    state, wait = instance.states.get(10), instance.states.get(1)
    if (state is None or not state.active or state.node.cls != 'STUStatescriptStateDeflectProjectiles'
            or wait is None or not wait.active or wait.node.pos != 7):
        return None
    now = at_tick * TICK * 1000
    # One command bucket admits authored sub-frame activation. Deadline is exact;
    # no owner command is synthesized to keep a stale state alive.
    if now + instance.component.frame_ms - 1 < state.start or now >= wait.start + wait.ms:
        return None
    return state


def intercept(state, eye, yaw, pitch, origin, direction, reach, *, radius=0.0):
    axes = basis(yaw, pitch)
    angle = expr.to_float(state.evaluate(state.node.field('m_025D0C39'), watch=False))
    incoming = -sum(a * b for a, b in zip(direction, axes[2], strict=True))
    if not math.isfinite(angle) or incoming <= math.cos(math.radians(angle)):
        return None
    relative = tuple(a - b for a, b in zip(origin, eye, strict=True))
    local = tuple(sum(a * b for a, b in zip(relative, axis, strict=True)) for axis in axes)
    toward = tuple(sum(a * b for a, b in zip(direction, axis, strict=True)) for axis in axes)
    closest = None
    for row in state.node.field('m_boxes') or ():
        first = state.evaluate(row.get('m_CBA118B2'), watch=False)
        second = state.evaluate(row.get('m_AEF67365'), watch=False)
        if not isinstance(first, expr.Vec3) or not isinstance(second, expr.Vec3):
            continue
        if radius > 0:
            # Reconstructed swept projectile bound in the guard's local axes.
            bounds = tuple(zip(first, second, strict=True))
            first = tuple(min(a, b) - radius for a, b in bounds)
            second = tuple(max(a, b) + radius for a, b in bounds)
        found = box_hit(local, toward, first, second, reach)
        if found is not None and (closest is None or found < closest):
            closest = found
    return (closest, axes[2]) if closest is not None else None


class MeleeContext(runtime.InstanceContext):
    def __init__(self, instance, target, source):
        super().__init__(instance, None)
        self.target, self.source = target, source

    def parameter_result(self, var, *, initial=None):
        if var == 8:
            return True, expr.Vec3(*self.source.mover.position)
        return super().parameter_result(var, initial=initial)

    def position(self, entity):
        if entity == self.target.entity:
            return expr.Vec3(*self.target.feet)
        return super().position(entity)

    def native(self, cfg):
        if (cfg.get('$') == 'STU_021D01CD'
                and expr.to_entity(expr.evaluate(cfg.get('m_entity'), self)) == self.target.entity):
            mover = self.target.owner.mover
            return expr.Vec3(*basis(mover.yaw, mover.pitch)[2])
        return super().native(cfg)


def blocks_tagged_melee(source, target, attack):
    """Selected03A3/node107 filter, not projectile reflection or generic immunity."""
    component = getattr(getattr(target.owner, 'body_script', None), 'component', None)
    if (not isinstance(component, runtime.Component) or getattr(target.owner, 'mover', None) is None
            or getattr(getattr(target.owner, 'hero', None), 'guid', None) != GENJI):
        return False
    tags = {value.guid for row in (attack.node.field('m_modifyHealth') or {}).get('m_148F3152') or ()
            if isinstance(value := attack.evaluate(row.get('m_EE3055BD'), watch=False), expr.Asset)}
    for instance in component.instances.values():
        if instance.graph.index != 0x03A3:
            continue
        if window(instance, source.match.combat.tick) is None:
            continue
        node = instance.graph.node(107)
        guard = instance.states.get(node.state)
        if guard is None or not guard.active:
            continue
        included = {expr.guid_of(t) for t in node.field('m_F60AA143') or ()}
        if (expr.guid_of(node.field('m_60703B1E')) == 0x0D80000000000318 and tags & included
                and expr.to_float(guard.evaluate(node.field('m_50530EAB'), watch=False)) == 0
                and expr.truthy(expr.evaluate((node.field('m_6AA5D70F') or {}).get('m_condition'),
                                             MeleeContext(instance, target, source)))):
            return True
    return False


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


def tags():
    catalog = _CATALOG
    return _rows(catalog.blob) if catalog.blob is not None else {}


def absorbable(definition):
    return _CATALOG.admitted(definition)


def matrix_distance(player, origin, direction, reach, anchor, forward):
    """Distance to active authored02CC/st3 boxes in a reconstructed aim frame.

    The caller supplies a live enemy player, eye anchor and normalized aim.
    Death, team, static cover and nearer body ordering belong to Combat.
    """
    hero = getattr(player, "hero", None)
    script = getattr(player, "body_script", None)
    if (hero is None or hero.guid != 0x02E000000000007A or hero.body != 0x0400000000000407
            or script is None or script.retired):
        return None
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
    for instance in script.component.instances.values():
        if instance.graph.index != 0x02CC:
            continue
        state = instance.state(3)
        if state is None or not state.active or state.node.cls != "STUStatescriptStateDeflectProjectiles":
            continue
        for box in state.node.field("m_boxes") or ():
            low = state.evaluate(box.get("m_CBA118B2"), watch=False)
            high = state.evaluate(box.get("m_AEF67365"), watch=False)
            if (not isinstance(low, expr.Vec3) or not isinstance(high, expr.Vec3)
                    or any(not math.isfinite(v) for v in (*low, *high))
                    or any(a > b for a, b in zip(low, high, strict=True))):
                raise ValueError("invalid authored Matrix box")
            hit = box_hit(local_origin, local_direction, low, high, reach)
            if hit is not None and (best is None or hit < best):
                best = hit
    return best


from ow174.game.mover import REPEAT
from ow174.game.script import nodes

def matrix_active(script):
    return next(_active_states(script, 0x02CC, 3), None) is not None


def matrix_abilities(script):
    for volume in _active_states(script, 0x02CC, 3):
        ability = volume.instance.states.get(0)
        if isinstance(ability, nodes.Ability) and ability.remaining() == 0:
            yield ability, volume


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


def countdown_deadline(chase):
    """Read an active authored scalar ChaseVar's remaining time without ticking it."""
    if chase is None or not chase.active or chase.node.cls != "STUStatescriptStateChaseVar":
        return None
    if chase.remaining is not None:
        remaining = max(0.0, chase.remaining / 1000)
    else:
        destination = expr.to_float(chase.evaluate(chase.node.field("m_destination"), watch=False))
        config = chase.node.field("m_9B0385B4")
        rate = expr.to_float(chase.evaluate(config, watch=False)) if config is not None else 1.0
        if not all(math.isfinite(value) for value in (chase.cur, destination, rate)):
            raise ValueError("invalid scalar defense countdown")
        if rate <= 0:
            return math.inf
        remaining = abs(chase.cur - destination) / rate
    return chase.last / 1000 + remaining


def state_deadline(state):
    """Deadline from a live native timer/countdown record; no owner-VM advancement."""
    if state is None or not state.active:
        return None
    if state.node.cls == "STUStatescriptStateWait":
        return (state.start + max(0, state.ms)) / 1000
    return countdown_deadline(state)


def world_active(state, now, deadline):
    """Current command window only: future activation cannot protect older catch-up hits."""
    if state is None or not state.active or deadline is None:
        return False
    window_end = now + (state.instance.component.frame_ms - 1) / 1000
    return window_end + 1e-9 >= state.start / 1000 and now + 1e-9 < deadline


def matrix_deadline(instance):
    chase = instance.states.get(45)
    if chase is None or chase.node.pos != 104:
        return None
    return countdown_deadline(chase)
