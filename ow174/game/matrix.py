"""Defense Matrix interception from the active 0407 volume and projectile tags."""

from ow174.game import defenses, deflection, projectile_tags

DVA, MECH = defenses.DVA, defenses.DVA_MECH


def trace(fight, shooter, definition, origin, direction, reach, *, view_tick=None):
    """Return the nearest enemy Matrix contact using the sampled eye position."""
    if not projectile_tags.absorbable(definition):
        return None
    best = None
    for target in fight._enemies(shooter):
        state = defenses.matrix_state(target.owner)
        if state is None:
            continue
        shown = target if view_tick is None else fight.seen(target, view_tick)
        if shown is None:
            continue
        distance = deflection.box_contact(target, state, origin, direction, reach, shown=shown)
        if distance is not None and (best is None or distance < best[0]):
            best = distance, target
    return best
