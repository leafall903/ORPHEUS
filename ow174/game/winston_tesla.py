"""Host the continuous search and ModifyHealth states in Winston's 00FE."""

import math

from ow174.game import barriers, damage_charge, winston_primal
from ow174.game.script import expr


def charge(script, amount, target, damage_tags=()):
    """Run authored 0B8B damage arithmetic on the admitted enemy-body loss."""
    if amount <= 0 or script.hero != winston_primal.HERO:
        return
    instance = damage_charge.admitted_source(script)
    if instance is not None and winston_primal.pool(script.component) is None:
        damage_charge.apply(script, instance, amount, target, damage_tags)


def contact(fight, player, command, script):
    if command is None or script.hero != winston_primal.HERO or winston_primal.pool(script.component):
        return
    for instance in script.component.instances.values():
        if instance.graph.index != winston_primal.WEAPON:
            continue
        search, damage = instance.states.get(10), instance.states.get(13)
        if search is not None and damage is not None and search.active and damage.active:
            sweep(fight, player, command, search, damage)
        return


def sweep(fight, player, command, search, damage):
    from ow174.game.combat import EPSILON, aim, hit_def, ray_world

    # 00FE supplies radius, angle and amount. Interpret the angle as a cone
    # half-angle and the continuous ModifyHealth amount as damage per second.
    reach = expr.to_float(search.evaluate(search.node.field("m_radius"), watch=False))
    angle = expr.to_float(search.evaluate(search.node.field("m_A30B383E"), watch=False))
    definition = hit_def(damage, {"m_modifyHealth": damage.node.field("m_modifyHealth")})
    amount = definition.amount * player.quanta / 1e6 * fight.outgoing_scale(player)
    origin, direction = fight._eye(player), aim(*command.aim())
    slope = math.tan(math.radians(angle))
    view_tick = command.frame - command.view_delay(player.quanta) * 1e6 / player.quanta
    shields, pillars, exposed = {}, {}, []

    def covered(ray, length):
        static = ray_world(fight._collision(), origin, ray, length)
        limit = length if static is None else static
        pillar = fight.walls.cast(origin, ray, limit)
        if pillar is not None:
            limit = pillar[0]
        obstruction = barriers.intercept(fight, player, origin, ray, limit)
        if obstruction is not None:
            shield = obstruction[1]
            shields[barriers.clock_key(shield)] = shield
            return True
        if pillar is not None:
            piece = pillar[1]
            pillars[piece.entity] = piece
        return static is not None or pillar is not None

    # Aimed cover can be hit even with no body behind it.
    covered(direction, reach)
    for target in fight._enemies(player):
        shown = fight.seen(target, view_tick)
        if shown is None:
            continue
        a, b = shown.segment()
        point = (a[0], max(a[1], min(b[1], origin[1])), a[2])
        offset = tuple(value - start for value, start in zip(point, origin, strict=True))
        distance = math.hypot(*offset)
        surface = max(0.0, distance - shown.radius)
        forward = sum(value * axis for value, axis in zip(offset, direction, strict=True))
        lateral = math.sqrt(max(0.0, distance * distance - forward * forward))
        if surface > reach or forward < 0 or lateral > forward * slope + shown.radius:
            continue
        if distance > EPSILON:
            ray = tuple(value / distance for value in offset)
            if covered(ray, surface):
                continue
        exposed.append(target)

    # Resolve cover for the entire slice before spending any shield pool.
    # A breaking shield cannot expose a second target within the same slice.
    for shield in shields.values():
        barriers.damage(fight, shield, amount, player)
    for piece in pillars.values():
        fight.walls.damage(piece, player, amount, command.frame)
    for target in exposed:
        fight._damage(player, target, amount, False, definition.loadout, command.frame, beam=True,
                      damage_tags=definition.damage_tags)
