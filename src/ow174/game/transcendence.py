"""Selected 017B protection and ally healing consumers for 104319."""

import math

from ow174.game.script import expr

HERO = 0x02E0000000000020
GRAPH = 0x017B
PROTECTION = 31  # node62 stacks entity variable 10414.


def phase(owner):
    if getattr(getattr(owner, "hero", None), "guid", None) != HERO:
        return None
    script = owner.body_script
    if (script is None or script.retired or script.component.owner_dead
            or script.component.fault is not None):
        return None
    flag = script.component.entity_var(10414, create=False)
    if flag is None or not expr.truthy(flag.value()):
        return None
    for instance in script.component.instances.values():
        if instance.graph.index == GRAPH:
            state = instance.states.get(PROTECTION)
            if state is not None and state.active and not state.ending:
                return state
    return None


def active(owner):
    return phase(owner) is not None


def recipients(fight, owner, tracker):
    from ow174.game.combat import capsule_visible, segment_distance

    collision = fight.match.collision_world
    if collision is None:
        return
    radius = expr.to_float(tracker.evaluate(tracker.node.field("m_radius"), watch=False))
    offset = tracker.evaluate(tracker.node.field("m_05BE62DD"), watch=False)
    if not math.isfinite(radius) or radius <= 0 or not isinstance(offset, expr.Vec3):
        return
    centre = tuple(axis + shift for axis, shift in zip(owner.mover.position, offset, strict=True))
    for target in fight._targets():
        if (target.owner is owner or fight.match.game_map.free_for_all or target.team != owner.team
                or fight.replacement_ticks.get(target.entity, fight.tick) > fight.tick
                or fight.vitals_of(target).dead):
            continue
        a, b = target.segment()
        if (segment_distance(centre, a, b) - target.radius <= radius
                and capsule_visible(collision, centre, a, b, target.radius,
                                    pillars=fight.ice_wall.pieces.values())):
            yield target


def heal_tick(fight, owner, seconds):
    protection = phase(owner)
    if protection is None:
        return
    instance = protection.instance
    tracker, heal = instance.states.get(0), instance.states.get(6)
    if tracker is None or not tracker.active or heal is None or not heal.active or heal.ending:
        return
    amount = expr.to_float(heal.evaluate(heal.node.field("m_modifyHealth")["m_amount"], watch=False))
    duration = expr.to_float(heal.evaluate(heal.node.field("m_95F1FB0B"), watch=False))
    if not all(math.isfinite(value) and value > 0 for value in (amount, duration)):
        return
    for target in recipients(fight, owner, tracker):
        fight._heal(owner, target, amount * seconds / duration)
