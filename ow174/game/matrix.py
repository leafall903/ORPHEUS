"""Defense Matrix interception from the active 0407 volume and projectile tags."""

from ow174.game import deflection, projectile_tags
from ow174.game.transcendence import active_state

DVA, MECH = 0x02E000000000007A, 0x0400000000000407
GRAPH = 0x02CC


def active(player):
    if getattr(getattr(player, "hero", None), "body", None) != MECH:
        return None
    state = active_state(player, DVA, GRAPH, 3)
    return state if state is not None and state.node.cls == "STUStatescriptStateDeflectProjectiles" else None


def trace(fight, shooter, definition, origin, direction, reach, *, implicit=False):
    """Return the nearest enemy Matrix contact in the current reconstructed eye frame."""
    if not projectile_tags.absorbable(definition, implicit=implicit):
        return None
    best = None
    for target in fight._enemies(shooter):
        state = active(target.owner)
        if state is None:
            continue
        distance = deflection.box_contact(target, state, origin, direction, reach)
        if distance is not None and (best is None or distance < best[0]):
            best = distance, target
    return best
