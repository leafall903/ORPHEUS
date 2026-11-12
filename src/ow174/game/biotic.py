"""Selected Ana grenade health/status reconstruction; native effect host remains open."""

from ow174.game import bots, stats


def explode(fight, rocket, point, wall):
    """Apply actual B97 explosion to visible living allies/self and enemies.

    B99/C72 resources supply100 ally healing,1.5/0 incoming healing and4s.
    Admission, refresh and current capsule visibility are declared server policy.
    """
    from ow174.game import combat

    blast = rocket.blast
    origin = combat._along(point, rocket.direction, -0.0001) if wall is not None else point

    def cover_contact(origin, direction, reach):
        return fight._blast_cover_contact(rocket.shooter, origin, direction, reach)

    for target in fight._targets():
        vitals = fight.vitals_of(target)
        if vitals.dead:
            continue
        a, b = target.segment()
        scalar = blast.splash(max(0.0, combat.segment_distance(point, a, b) - target.radius))
        if scalar <= 0 or not combat.capsule_visible(
            fight._collision(), origin, a, b, target.radius, walls=fight.walls,
            cover_contact=cover_contact,
        ):
            continue
        friendly = target.owner is rocket.shooter or (
            not fight.match.game_map.free_for_all and target.team == rocket.shooter.team)
        if friendly:
            given = fight._heal_pools(target.owner, vitals, blast.ally_heal * scalar)
            if given > 0 and not isinstance(rocket.shooter, bots.Bot):
                rocket.shooter.stats.add(stats.HEALING, given)
            # Impact precedes this grenade's boost; repeated boosts refresh.
            vitals.healing_boost_until = max(vitals.healing_boost_until, fight.now + blast.biotic_duration)
        elif not fight.damage_immune(target.owner):
            if fight.zarya_bubbles.for_target(target.owner) is None:
                vitals.healing_block_until = max(vitals.healing_block_until, fight.now + blast.biotic_duration)
            fight._damage(rocket.shooter, target, blast.amount * scalar, False, blast.loadout,
                          fight.tick, world_tick=fight.tick, source_body=rocket.source_body)
