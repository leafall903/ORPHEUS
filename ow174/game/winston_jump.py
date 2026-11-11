"""Host 010A's accepted Jump Pack casts and collision-backed impacts."""

import math

from ow174.game import barriers, damage_charge, winston_primal
from ow174.game.mover import FELL, JUMPED, REAL_JUMP
from ow174.game.script import expr

GRAPH = 0x010A


def stop(instance):
    movement = instance.states.get(2)
    if movement is not None and movement.active:
        movement.end(False)
        instance.component.run_now()


def cancel(fight, player, *, keep_fall=False):
    pending = fight.winston_landing_pending.pop(player.body, None)
    if pending is not None:
        stop(pending[1])
    flight = fight.winston_jumps.pop(player.body, None)
    if flight is None:
        return
    instance, tick, frame = flight
    stop(instance)
    if keep_fall and player.mover.airborne:
        fight.impulse_landings.setdefault(player.body, (tick, frame))
        player.mover.velocity = (0.0, player.mover.velocity[1], 0.0)


def command(fight, player, command, script):
    if script.hero != winston_primal.HERO:
        return
    flight = fight.winston_jumps.get(player.body)
    if flight is not None:
        instance, _, _ = flight
        if player.mover.landed:
            fight.winston_jumps.pop(player.body)
            centre = tuple(player.mover.position)
            if command is None:
                fight.winston_landing_pending[player.body] = player, instance, script.frame, centre
            else:
                impact(fight, player, instance, script.frame, landing=True, centre=centre)
                stop(instance)
        elif command is not None:
            fight.winston_jumps[player.body] = instance, fight.tick, script.frame

    for instance in tuple(script.component.instances.values()):
        if instance.graph.index != GRAPH:
            continue
        cast = instance.states.get(26)
        if cast is None or not cast.counter:
            return
        token = instance.id, cast.counter
        if fight.winston_jump_seen.get(player.body) == token:
            return
        fight.winston_jump_seen[player.body] = token
        if command is not None and cast.active:
            cancel(fight, player)
            launch(fight, player, instance, command)
        return


def settle(fight):
    """Apply closed-frame impacts after all bodies have advanced their protection states."""
    for body in sorted(fight.winston_landing_pending):
        pending = fight.winston_landing_pending.pop(body, None)
        if pending is None:
            continue
        player, instance, frame, centre = pending
        impact(fight, player, instance, frame, landing=True, centre=centre)
        stop(instance)


def launch(fight, player, instance, command):
    movement = instance.state(2)
    if not movement.active:
        # A valid Primal air recast needs a fresh movement scope.
        movement.begin()
        instance.component.run_now()
    config = instance.graph.node(85).field("m_value")["m_configVars"][0]
    lower = expr.to_float(instance.evaluate(config["m_E26E5E09"]))
    upper = expr.to_float(instance.evaluate(config["m_5023B9B0"]))
    speed = expr.to_float(instance.find_var(expr.INSTANCE, 1262).value())
    yaw, pitch = command.aim()
    heading = yaw * math.tau / 65536
    elevation = math.radians(max(lower, min(upper, -pitch * 360 / 65536)))
    horizontal = speed * math.cos(elevation)
    mover = player.mover
    mover.velocity = (math.sin(heading) * horizontal, speed * math.sin(elevation),
                      math.cos(heading) * horizontal)
    mover.flags_word = mover.flags_word & ~FELL | JUMPED | REAL_JUMP
    mover.air_ticks = 0
    mover.landed = False
    fight.winston_jumps[player.body] = instance, fight.tick, command.frame
    impact(fight, player, instance, command.frame, landing=False, centre=tuple(mover.position))


def impact(fight, player, instance, frame, *, landing, centre):
    from ow174.game.combat import EPSILON, ray_world, segment_distance

    search = instance.state(4 if landing else 6)
    damage = instance.state(3 if landing else 7)
    radius = expr.to_float(search.evaluate(search.node.field("m_radius"), watch=False))
    modify = damage.node.field("m_modifyHealth")
    damage_tags = damage_charge.event_tags(damage, modify)
    amount = (expr.to_float(instance.find_var(expr.INSTANCE, 476).value()) if landing
              else expr.to_float(damage.evaluate(modify["m_amount"], watch=False)))
    amount *= fight.outgoing_scale(player)
    loadout = damage.evaluate(modify["m_loadout"], watch=False).guid
    origin = (centre[0], centre[1] + 0.1, centre[2])
    shields, contacts = {}, []
    for target in fight._enemies(player):
        bottom, top = target.segment()
        distance = max(0.0, segment_distance(centre, bottom, top) - target.radius)
        if distance >= radius:
            continue
        scalar = 1.0 - distance / radius if landing else 1.0
        x, y, z = target.feet
        point = (x, y + target.height / 2, z)
        delta = tuple(value - start for value, start in zip(point, origin, strict=True))
        length = math.hypot(*delta)
        if length > EPSILON:
            ray = tuple(axis / length for axis in delta)
            surface = max(0.0, length - target.radius)
            if (ray_world(fight._collision(), origin, ray, surface) is not None
                    or fight.walls.cast(origin, ray, surface) is not None):
                continue
            blocked = barriers.intercept(fight, player, origin, ray, surface)
            if blocked is not None:
                shield = blocked[1]
                key = barriers.clock_key(shield)
                previous = shields.get(key)
                if previous is None or scalar > previous[1]:
                    shields[key] = shield, scalar
                continue
        contacts.append((target, scalar))

    for shield, scalar in shields.values():
        barriers.damage(fight, shield, amount * scalar, player)
    for target, scalar in contacts:
        if fight._damage(player, target, amount * scalar, False, loadout, frame, world_tick=fight.tick,
                         damage_tags=damage_tags):
            winston_primal.push(fight, player, target, damage, modify, scale=scalar, origin=centre)
