"""Static 104319 Photon Projector contact and damage."""

from functools import partial

from ow174.game import mei
from ow174.game.script import expr

HERO, WEAPON = 0x02E0000000000016, 0x157C
ORB = 0x04000000000026F1


def cover(fight, player, origin, direction, reach, frame):
    from ow174.game.combat import ray_world

    static = ray_world(fight._collision(), origin, direction, reach)
    if static is not None:
        yield static, None, None
    cryo = fight.cryo_contact(player, origin, direction, reach)
    if cryo is not None:
        yield cryo, None, None
    ice = fight.walls.cast(origin, direction, reach)
    if ice is not None:
        distance, piece, _ = ice
        yield distance, None, lambda amount: fight.walls.damage(piece, player, amount, frame)
    shield = fight.shields.cast(player, origin, direction, reach)
    if shield is not None:
        distance, bubble = shield
        yield distance, bubble.entity, lambda amount: fight.shields.damage(bubble, player, amount, frame)
    held = fight.reinhardt_barriers.trace(player, origin, direction, reach)
    if held is not None:
        distance, owner, state = held
        yield distance, owner.body, partial(fight.reinhardt_barriers.damage, owner, state)
    for service in (fight.orisa_barriers, fight.winston_barriers, fight.photon_barriers):
        hit = service.trace(player, origin, direction, reach)
        if hit is not None:
            distance, actor = hit
            yield distance, actor.body, partial(service.damage, actor)
    sigma = fight.sigma_barriers.trace(player, origin, direction, reach)
    if sigma is not None:
        distance, actor = sigma
        yield distance, actor.entity, partial(fight.sigma_barriers.damage, actor)


def native(world, cfg, instance):
    if world.match is None or instance.component.strict or instance.graph.index != WEAPON:
        return NotImplemented
    queries = instance.graph.state(25).field("m_condition")["m_configVars"]
    tag_query = cfg == instance.graph.state(94).field("m_condition")
    if not tag_query and cfg not in queries:
        return NotImplemented
    fight = world.match.combat
    target = instance.evaluate(cfg.get("m_entity"))
    barrier = isinstance(target, expr.Entity) and target.id == world.beam_barrier
    if tag_query:
        return barrier
    if not isinstance(target, expr.Entity):
        return False
    if barrier:
        return True
    if cfg.get("$") == "STU_E72F6348":
        return any(body.entity == target.id for body in fight._targets())
    if cfg.get("$") == "STU_AC829876":
        owner = next((p for p in world.match.players if p.body == world.body), None)
        return owner is not None and any(body.entity == target.id for body in fight._enemies(owner))
    return NotImplemented


def primary(fight, player, command):
    from ow174.game.combat import _number, aim, ray_capsule

    script = player.body_script
    instance = next((i for i in script.component.instances.values() if i.graph.index == WEAPON), None)
    if instance is None or script.component.strict:
        return
    search = instance.states.get(76)
    reach = _number(instance.find_var(0, 1648).value(), 0.0)
    active = command is not None and search is not None and search.active and not search.ending and reach > 0.
    selected = None
    selected_barrier = None
    if active:
        origin, direction = fight._eye(player), aim(*command.aim())
        view_tick = command.frame - command.view_delay(player.quanta) * 1e6 / player.quanta
        stop, blocking_barrier, damage_cover = min(
            cover(fight, player, origin, direction, reach, command.frame),
            default=(None, None, None),
            key=lambda hit: hit[0],
        )
        bodies = [(t, fight.seen(t, view_tick)) for t in fight._enemies(player) if not mei.protected(t.owner)]
        state = instance.state(86)
        modify = state.node.field("m_modifyHealth")
        loadout = state.evaluate(modify["m_loadout"], watch=False)
        loadout = loadout.guid if isinstance(loadout, expr.Asset) else None

    def hit_body(target, amount):
        fight._damage(player, target, amount, False, loadout, command.frame, beam=True)

    with script.component.operation():
        for index in (100, 101):
            ray = instance.states.get(index)
            distance = target = damage = None
            shield = None
            if active and ray is not None and ray.active:
                distance, damage, shield = stop, damage_cover, blocking_barrier
                radius = _number(ray.evaluate(ray.node.field("m_57DAE1B5"), watch=False), 0.0)
                for candidate, shown in bodies:
                    if shown is None:
                        continue
                    a, b = shown.segment()
                    at = ray_capsule(origin, direction, a, b, shown.radius + radius)
                    if at is not None and at <= reach and (distance is None or at < distance):
                        distance, target, shield = at, candidate, None
                        damage = partial(hit_body, candidate)
                if selected is None:
                    selected, selected_barrier = damage, shield
            output = instance.graph.state(index).field("m_target")
            entity = target.entity if target is not None else shield or expr.INVALID_ENTITY
            instance.write(expr.lvalue(output["m_692DFF72"]), expr.Entity(entity))
            instance.write(expr.lvalue(output["m_750377AA"]), distance is not None)
        if script.world.beam_barrier != selected_barrier:
            script.world.beam_barrier = selected_barrier
            for index in (25, 94):
                switch = instance.states.get(index)
                if switch is not None and switch.active:
                    switch.dependency_changed()
    if selected is not None:
        amount = _number(state.evaluate(modify["m_amount"], watch=False), 0.0)
        selected(amount * player.quanta / 1e6 * fight.outgoing_damage_scale(player))

