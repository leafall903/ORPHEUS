"""Reconstructed Sigma combat services over the extracted build-104319 graphs.

Grasp's box and absorption amount output come from 1D02 state1. The server's
box transform, interception ordering and nominal damage accounting are rebuilt;
this adapter is not proof of native engine services or client presentation.
"""

import math

from ow174.game import bots
from ow174.game.script import expr, graph

HERO = 0x02E000000000023B
GRASP = 0x1D02
# Only the ordinary Soldier rifle hitscan has an executed classification fixture
# here. Other hitscan/beam families stay open until their eligibility is known.
GRASP_HITSCAN = {0x0254}
UNABSORBABLE_PROJECTILES = {0x1BBB}  # Accretion is not an ordinary absorbable projectile.


def ray_box(origin, direction, low, high, reach):
    """First nonnegative intersection with a local axis-aligned box, or None."""
    start, stop = 0.0, reach
    for point, vector, minimum, maximum in zip(origin, direction, low, high, strict=True):
        if abs(vector) < 1e-9:
            if point < minimum or point > maximum:
                return None
            continue
        enter, leave = (minimum - point) / vector, (maximum - point) / vector
        start, stop = max(start, min(enter, leave)), min(stop, max(enter, leave))
        if start > stop:
            return None
    return start


def grasp_intersection(combat, shooter, origin, direction, reach):
    """Nearest live enemy Grasp box before the caller's nearer body/wall bound.

    The local box is reconstructed as view-oriented and eye-relative. Bounds
    themselves are evaluated from the actual active State, including body scale.
    """
    best = None
    for target in combat._enemies(shooter):
        actor = target.owner
        npc = isinstance(actor, bots.Bot)
        hero = actor.hero if npc else actor.hero.guid
        script = actor.body_script
        if hero != HERO or script is None or script.component.owner_dead:
            continue
        yaw, pitch = (actor.yaw, 0) if npc else (actor.mover.yaw, actor.mover.pitch)
        yaw, pitch = yaw * math.tau / 65536, pitch * math.tau / 65536
        sy, cy, sp, cp = math.sin(yaw), math.cos(yaw), math.sin(pitch), math.cos(pitch)
        basis = ((cy, 0, -sy), (sy * sp, cp, cy * sp), (sy * cp, -sp, cy * cp))
        eye = (target.feet[0], target.feet[1] + target.eye, target.feet[2])
        delta = tuple(a - b for a, b in zip(origin, eye, strict=True))
        local_origin = tuple(sum(a * b for a, b in zip(delta, axis, strict=True)) for axis in basis)
        local_direction = tuple(sum(a * b for a, b in zip(direction, axis, strict=True)) for axis in basis)
        for instance in script.component.instances.values():
            if instance.graph.index != GRASP:
                continue
            state = instance.states.get(1)
            if state is None or not state.active:
                continue
            for box in state.node.field("m_boxes") or []:
                low = state.evaluate(box.get("m_CBA118B2"), watch=False)
                high = state.evaluate(box.get("m_AEF67365"), watch=False)
                if not isinstance(low, expr.Vec3) or not isinstance(high, expr.Vec3):
                    continue
                hit = ray_box(local_origin, local_direction, low, high, reach)
                if hit is not None and (best is None or hit < best[0]):
                    best = hit, state
    return best


def absorbed(state, amount):
    """Feed the actual 1D02 absorption output and its authored accumulator plug.

    6584 is the per-hit amount; the authored node32 adds it to21312. Do not grant
    health or bypass the authored end/PlayScript path here. Health-pool engine
    services and temporary-shield replication remain separate open work.
    """
    state.instance.write((graph.INSTANCE, 6584), float(amount))
    state.instance.follow(state.node, "m_160B5DBE", state)
    state.instance.component.run_now()
