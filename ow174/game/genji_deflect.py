"""Scoped104319 Genji3A3/st10 consumer for Mei icicle and airborne Blizzard.

Authored input, two-second window and cooldown stay in the VM. The three
boxes and incoming90-degree limit are resource data; eye-relative yaw-only
coordinates, aim-directed return and authority transfer are reconstructed.
08BE spray has excluded target tag0145;108B icicle does not. Other projectile
tags, native output plugs and client lease/visual ownership remain unaccepted.
Return legs are swept in the same frame, at most eight reflections per frame.
0946 Blizzard has tags0412/06B2/07D4, with no excluded0145 tag.
"""

import math

HERO = 0x02E0000000000029
ICICLE = 0x040000000000108B
BLIZZARD = 0x0400000000000946


def active(fight, owner):
    script = getattr(owner, 'body_script', None)
    if (getattr(script, 'hero', None) != HERO or fight.is_dead(owner)
            or fight.frozen(owner) or fight.is_stunned(owner)):
        return None
    instance = next((i for i in script.component.instances.values() if i.graph.index == 0x3A3), None)
    state = instance.states.get(10) if instance else None
    return state if state is not None and state.active else None


def interrupt(owner):
    """Scoped channel policy: freeze ends Deflect through its authored stop path.

    Masking interception alone let the two-second channel resume after thaw.
    Keep the original on-end/cooldown graph; do not synthesize its timer.
    """
    script = getattr(owner, 'body_script', None)
    if script is None or getattr(script, 'hero', None) != HERO:
        return
    instance = next((i for i in script.component.instances.values() if i.graph.index == 0x3A3), None)
    ability = instance.states.get(0) if instance else None
    if ability is not None:
        ability.presses.clear()
        ability.stop()


def box_ray(point, direction, low, high, length):
    enter, leave = 0.0, length
    for p, d, a, b in zip(point, direction, low, high, strict=True):
        a, b = min(a, b), max(a, b)
        if abs(d) < 1e-9:
            if p < a or p > b:
                return None
        else:
            near, far = sorted(((a - p) / d, (b - p) / d))
            enter, leave = max(enter, near), min(leave, far)
            if enter > leave:
                return None
    return enter


def blocks_melee(fight, owner, incoming):
    """Use the authored angle after melee capsule admission."""
    from ow174.game.combat import aim

    state = active(fight, owner)
    if state is None:
        return False
    facing = aim(owner.mover.yaw, owner.mover.pitch)
    angle = float(state.evaluate(state.node.field('m_025D0C39'), watch=False))
    return -sum(a * b for a, b in zip(facing, incoming, strict=True)) > math.cos(math.radians(angle))


def cast(fight, shooter, definition, origin, direction, length):
    from ow174.game.roadhog_projectiles import SCRAP, WHOLE_HOG
    from ow174.game.symmetra_beam import ORB

    if definition not in (ICICLE, BLIZZARD, ORB, WHOLE_HOG) and definition not in SCRAP:
        return None
    best = None
    for owner in fight.match.players:
        if owner is shooter or (not fight.match.game_map.free_for_all and owner.team == shooter.team):
            continue
        state = active(fight, owner)
        if state is None:
            continue
        hit = contact(fight, owner, state, origin, direction, length)
        if hit is not None and (best is None or hit[0] < best[0]):
            best = hit
    return best


def contact(fight, owner, state, origin, direction, length):
    """Resource boxes/angle, reconstructed eye-relative yaw transform."""
    from ow174.game.combat import aim

    yaw, pitch = owner.mover.yaw, owner.mover.pitch
    forward, facing = aim(yaw, 0), aim(yaw, pitch)
    angle = float(state.evaluate(state.node.field('m_025D0C39'), watch=False))
    if -sum(a * b for a, b in zip(facing, direction, strict=True)) <= math.cos(math.radians(angle)):
        return None
    offset = tuple(a - b for a, b in zip(origin, fight._eye(owner), strict=True))

    def local(v):
        return (v[0] * forward[2] - v[2] * forward[0], v[1],
                v[0] * forward[0] + v[2] * forward[2])

    best = None
    for box in state.node.field('m_boxes'):
        low = tuple(state.evaluate(box['m_CBA118B2'], watch=False))
        high = tuple(state.evaluate(box['m_AEF67365'], watch=False))
        distance = box_ray(local(offset), local(direction), low, high, length)
        if distance is not None and (best is None or distance < best[0]):
            best = distance, owner, facing
    return best
