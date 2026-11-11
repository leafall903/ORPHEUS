"""Authored Deflect and Defense Matrix states with reconstructed contact geometry."""

import math

from ow174.game import ana_biotic, ana_sleep, defenses, graviton, sigma_grasp, zarya_weapons
from ow174.game.content import DVA, DVA_MECH
from ow174.game.halt import PROJECTILE as HALT
from ow174.game.script import expr

GENJI = 0x02E0000000000029
FIRE_STRIKE = 0x04000000000001EA  # 104319 entity target tags are empty
SNIPER_RIFLE = 0x0400000000001E80  # 104319 target tags 02F9 and 0535 omit 0145
SNIPER_SIDEARM = 0x04000000000023BB  # 104319 target tag 02F9 omits 0145
ENFORCER_PELLET = 0x04000000000023F0  # 104319 target tag 02F9 omits 0145
TALON_PROJECTILES = (SNIPER_RIFLE, SNIPER_SIDEARM, ENFORCER_PELLET)
TROOPER_RIFLE = 0x0400000000001FE6
HEAVY_CANNON = 0x040000000000206A
TALON_RAYS = (TROOPER_RIFLE, HEAVY_CANNON)  # 104319 target tag 02F9 omits 0145
SOLDIER_RIFLE_VOLLEY = (0x0254, 25)
SOLDIER_RAYS = (0x04000000000003D4, 0x0400000000001C85)  # ordinary and Visor rifle
RAYS = TALON_RAYS + SOLDIER_RAYS
SOLDIER_HELIX = (0x040000000000072C, 0x0400000000001C86)  # target tags omit 0145
BLIZZARD = 0x0400000000000946  # 104319 entity target tags omit 0145
FUSION_DRIVER = 0x0B900000000000EF  # 0BD4/node50 has a loadout but no projectile entity.
MAX_REDIRECTIONS = 8
DIRECT_PROJECTILES = (
    ana_sleep.PROJECTILE, ana_biotic.PROJECTILE, zarya_weapons.SECONDARY, graviton.DEFINITION, HALT,
    *TALON_PROJECTILES, *SOLDIER_HELIX,
)


def frontal(target, incoming):
    from ow174.game.combat import _dot, aim

    state = defenses.active_state(target.owner, GENJI, 0x03A3, 10)
    if state is None:
        return None
    mover = target.owner.mover
    angle = state.evaluate(state.node.field("m_025D0C39"), watch=False)
    facing = aim(mover.yaw, mover.pitch)
    if -_dot(facing, incoming) >= math.cos(math.radians(angle)) - 1e-9:
        return state
    return None


def _box_distance(origin, direction, low, high, length, radius):
    enter, leave = 0.0, length
    for position, velocity, a, b in zip(origin, direction, low, high, strict=True):
        lo, hi = min(a, b) - radius, max(a, b) + radius
        if abs(velocity) <= 1e-9:
            if not lo <= position <= hi:
                return None
            continue
        first, last = (lo - position) / velocity, (hi - position) / velocity
        enter, leave = max(enter, min(first, last)), min(leave, max(first, last))
        if enter > leave:
            return None
    return enter


def box_contact(target, state, origin, direction, length, radius):
    from ow174.game.combat import _dot, _sub, aim

    mover = target.owner.mover
    forward = aim(mover.yaw, mover.pitch)
    right = aim(mover.yaw + 16384, 0)
    up = (forward[1] * right[2],
          forward[2] * right[0] - forward[0] * right[2],
          -forward[1] * right[0])
    x, y, z = target.feet
    offset = _sub(origin, (x, y + target.eye, z))
    local = tuple(_dot(offset, axis) for axis in (right, up, forward))
    ray = tuple(_dot(direction, axis) for axis in (right, up, forward))
    best = None
    for box in state.node.field("m_boxes") or ():
        low = state.evaluate(box.get("m_CBA118B2"), watch=False)
        high = state.evaluate(box.get("m_AEF67365"), watch=False)
        if not isinstance(low, expr.Vec3) or not isinstance(high, expr.Vec3):
            continue
        distance = _box_distance(local, ray, low, high, length, radius)
        if distance is not None and (best is None or distance < best):
            best = distance
    return best


def direct_projectile(definition, loadout=None):
    return definition in DIRECT_PROJECTILES or (definition is None and loadout == FUSION_DRIVER)


def projectile_contact(definition, targets, origin, direction, length, radius=0.0, *, loadout=None):
    if definition not in (FIRE_STRIKE, BLIZZARD, *RAYS) and not direct_projectile(definition, loadout):
        return None
    best = None
    for target in targets:
        state = frontal(target, direction)
        reflect = state is not None
        if not reflect and getattr(getattr(target.owner, "hero", None), "body", None) == DVA_MECH:
            state = defenses.active_state(target.owner, DVA, 0x02CC, 3)
        if state is None:
            state = defenses.active_state(target.owner, sigma_grasp.SIGMA, sigma_grasp.GRAPH, 1)
        if state is None:
            continue
        distance = box_contact(target, state, origin, direction, length, radius)
        if distance is not None and (best is None or (distance, target.entity) < best[:2]):
            best = distance, target.entity, target, reflect, state
    return best
