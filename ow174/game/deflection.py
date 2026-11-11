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

GENJI = 0x02E0000000000029
EXCLUDED_TAG = 0x0D60000000000145
MAX_DEFLECTIONS = 8  # explicit bounded host cycle policy, not a recovered native limit
TICK = .016


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


_path = os.environ.get('ORPHEUS_PROJECTILE_TAGS')
_path = Path(_path) if _path else DATA_DIR / 'projectile_tags_174.json'
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


def blocks_melee(source, target, attack):
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
