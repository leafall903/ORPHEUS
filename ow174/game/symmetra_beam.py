"""Host 157C's authored Photon Projector primary ray against Match bodies."""

from ow174.game import dva_matrix, sigma
from ow174.game.script import expr, graph

HERO = 0x02E0000000000016
WEAPON = 0x157C


def target_query(world, cfg, instance):
    """Answer the primary beam's exact charge and Experimental Barrier queries."""
    if (world.hero != HERO or world.match is None or instance.graph.index != WEAPON
            or instance.component.strict or instance.component.entity != world.body):
        return NotImplemented
    kind = cfg.get("$")
    if kind == "STU_25ECB55F":
        if cfg != instance.graph.node(185).field("m_condition"):
            return NotImplemented
    elif kind not in ("STU_E72F6348", "STU_AC829876") or cfg not in (
        instance.graph.state(25).field("m_condition")["m_configVars"]
    ):
        return NotImplemented
    entity = expr.to_entity(instance.evaluate(cfg.get("m_entity")))
    fight = world.match.combat
    owner = next((p for p in world.match.players if p.body == world.body), None)
    if owner is None:
        return False
    barrier = fight.barriers.get(entity)
    if (isinstance(barrier, sigma.Barrier) and barrier.active and barrier.owner is not owner
            and (fight.match.game_map.free_for_all or barrier.owner.team != owner.team)):
        if kind == "STU_25ECB55F":
            tag = expr.guid_of(cfg.get("m_88B0050A"))
            return tag in dva_matrix.tags().get(sigma.BARRIER_DEFINITION, ())
        return True
    if kind == "STU_25ECB55F":
        return False
    targets = fight._targets() if kind == "STU_E72F6348" else fight._enemies(owner)
    return any(target.entity == entity for target in targets)


def _contact(fight, player, ray, origin, direction, reach, view_tick):
    from ow174.game.combat import ray_capsule, ray_world

    radius = expr.to_float(ray.evaluate(ray.node.field("m_57DAE1B5"), watch=False))
    wall = ray_world(fight._collision(), origin, direction, reach)
    limit = reach if wall is None else wall
    body = None
    for target in fight._enemies(player):
        shown = fight.seen(target, view_tick)
        if shown is None:
            continue
        distance = ray_capsule(origin, direction, *shown.segment(), shown.radius + radius)
        if (distance is not None and distance <= reach and (wall is None or distance < wall)
                and (body is None or distance < limit)):
            limit, body = distance, target
    barrier = sigma.barrier_intersection(fight, player, origin, direction, limit)
    if barrier is not None and (wall is None or barrier[0] < wall):
        return None, barrier[1], True
    return body, None, body is not None or wall is not None


def attack(fight, player, command, script):
    from ow174.game.combat import aim, hit_def

    if script.hero != HERO or script.component.strict:
        return
    weapon = next((i for i in script.component.instances.values() if i.graph.index == WEAPON), None)
    if weapon is None:
        return
    search = weapon.states.get(76)
    active = command is not None and search is not None and search.active
    reach = expr.to_float(weapon.context().var(graph.INSTANCE, 1648))
    if active and reach > 0:
        origin, direction = fight._eye(player), aim(*command.aim())
        view_tick = command.frame - command.view_delay(player.quanta) * 1e6 / player.quanta
    victim = shield = None
    with script.component.operation():
        for index in (100, 101):
            ray = weapon.states.get(index)
            body = barrier = None
            hit = False
            if active and reach > 0 and ray is not None and ray.active:
                body, barrier, hit = _contact(fight, player, ray, origin, direction, reach, view_tick)
            if victim is None and shield is None:
                victim, shield = body, barrier
            entity = body.entity if body is not None else (
                barrier.entity if isinstance(barrier, sigma.Barrier) else expr.INVALID_ENTITY
            )
            output = weapon.graph.state(index).field("m_target")
            weapon.write(expr.lvalue(output.get("m_692DFF72")), expr.Entity(entity))
            weapon.write(expr.lvalue(output.get("m_750377AA")), hit)
    if not active or (victim is None and shield is None):
        return
    damage = weapon.state(86)
    definition = hit_def(damage, {"m_modifyHealth": damage.node.field("m_modifyHealth")})
    if definition is None or definition.amount <= 0:
        return
    amount = definition.amount * player.quanta / 1e6
    if shield is not None:
        sigma.damage_barrier(fight, shield, amount)
    else:
        fight._damage(player, victim, amount, False, definition.loadout, command.frame, beam=True)
