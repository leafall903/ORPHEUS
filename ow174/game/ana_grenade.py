"""104319 0B97 Biotic Grenade effects and reconstructed server contact."""

import math

from ow174.game import mei, phase_defenses, stats
from ow174.game.script import expr

DEFINITION = 0x0400000000001426
# 0267.053 -> C71 -> B99/C72 effect resources.
ALLY_HEAL = 100.
HEAL_BOOST = 1.5
DURATION = 4.


def gravity(state):
    if state.instance.graph.index != 0x0B97:
        return 0.
    for motion in state.node.field("m_projectileMotions") or ():
        if motion.get("$") == "STU_E1A5B579" and motion.get("m_D0C4CF49") is None:
            amount = expr.to_float(state.evaluate(motion["m_599AB8D7"], watch=False))
            if math.isfinite(amount):
                return amount
    return 0.


def healing_amount(vitals, amount, now):
    return amount * healing_scale(now, vitals.healing_block_until, vitals.healing_boost_until)


def healing_scale(now, blocked_until, boosted_until):
    if now < blocked_until:
        return 0.
    return HEAL_BOOST if now < boosted_until else 1.


def field_pulse_scale(fight, vitals, first_tick, last_tick):
    """Weight retained field pulses by the effects active on their own ticks."""
    from ow174.game.combat import TICK

    if not (vitals.healing_block_until or vitals.healing_boost_until):
        return last_tick - first_tick + 1
    scale = 0.
    for tick in range(first_tick, last_tick + 1):
        now = fight.now - (fight.tick - tick) * TICK
        # Compare simulated tick boundaries despite floating-point roundoff.
        scale += healing_scale(now + 1e-9, vitals.healing_block_until, vitals.healing_boost_until)
    return scale


def collision_targets(fight, shooter):
    return (target for target in fight._targets()
            if target.owner is not shooter and not fight.vitals_of(target).dead
            and not phase_defenses.active(target.owner))


def splash(fight, rocket, point, exclude, protected):
    from ow174.game import combat

    origin = combat._along(point, rocket.direction, -0.0001)
    targets = fight._targets()
    cryo_cover = [(target, target.segment()) for target in targets if mei.protected(target.owner)]
    for target in targets:
        if target.entity in exclude or phase_defenses.active(target.owner):
            continue
        vitals = fight.vitals_of(target)
        if vitals.dead:
            continue
        a, b = target.segment()
        scalar = rocket.blast.splash(max(0., combat.segment_distance(point, a, b) - target.radius))
        centre = (target.feet[0], target.feet[1] + target.height * .5, target.feet[2])
        if (scalar <= 0. or not combat.capsule_visible(fight._collision(), origin, a, b, target.radius)
                or fight.walls.blocked(origin, centre) or occluded(origin, centre, target, cryo_cover)):
            continue
        ally = target.owner is rocket.shooter or (
            not fight.match.game_map.free_for_all and target.team == rocket.team
        )
        if ally:
            if mei.protected(target.owner):
                fight._cryo_healing(target.owner)
            given = vitals.heal(healing_amount(vitals, ALLY_HEAL * scalar, fight.now))
            rocket.shooter.stats.add(stats.HEALING, given)
            fight._sync_body_health(target.owner, vitals)
            vitals.healing_boost_until = max(vitals.healing_boost_until, fight.now + DURATION)
            history = fight.cryo_samples.get(target.entity)
            if history is not None and mei.protected(target.owner):
                history.note(fight.now, vitals.healing_block_until, vitals.healing_boost_until)
        elif target.entity not in protected and not mei.protected(target.owner):
            damage = rocket.damage_scale * rocket.blast.amount * scalar
            dealt = fight._damage(rocket.shooter, target, damage, False,
                                  rocket.blast.loadout, fight.tick, world_tick=fight.tick)
            if dealt:
                if not vitals.dead:
                    vitals.healing_block_until = max(vitals.healing_block_until, fight.now + DURATION)
                fight._hit_effect(target, rocket.blast.effect)


def occluded(origin, centre, target, cover):
    from ow174.game.combat import ray_capsule

    delta = tuple(b - a for a, b in zip(origin, centre, strict=True))
    reach = math.sqrt(sum(axis * axis for axis in delta))
    if reach <= 1e-9:
        return False
    direction = tuple(axis / reach for axis in delta)
    return any(blocker is not target
               and (distance := ray_capsule(origin, direction, a, b, blocker.radius)) is not None
               and distance < reach for blocker, (a, b) in cover)
