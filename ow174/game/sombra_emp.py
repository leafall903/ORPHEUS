"""Match routing for authored Sombra body applications against D.Va."""

import math

from ow174.game import defenses
from ow174.game.body_entities import DVA
from ow174.game.script import expr, graph, play, runtime

HERO = 0x02E000000000012E
HACK, EMP, PULSE, EFFECT = 0x0B1B, 0x0B31, 0x0B32, 0x0B1F
DAMAGE_MESSAGE = 0x0240000000000011


def targets(fight, caster, state):
    """Reconstruct the body-capsule and static LOS checks using the authored radius."""
    from ow174.game.combat import ray_world

    radius = expr.to_float(state.evaluate(state.node.field("m_radius"), watch=False))
    if fight.match.collision_world is None or not math.isfinite(radius) or radius <= 0:
        return ()
    origin = fight._eye(caster)
    selected = []
    for target in fight._enemies(caster):
        if not _eligible(target):
            continue
        bottom, top = target.segment()
        offset = (bottom[0] - origin[0], max(bottom[1], min(top[1], origin[1])) - origin[1],
                  bottom[2] - origin[2])
        distance = math.hypot(*offset)
        reach = max(0.0, distance - target.radius)
        if reach > radius:
            continue
        if distance > 0:
            ray = tuple(axis / distance for axis in offset)
            wall = ray_world(fight._collision(), origin, ray, reach)
            if wall is not None and wall < reach:
                continue
        selected.append((reach, target.entity))
    return tuple(expr.Entity(entity) for _, entity in sorted(selected))


def _eligible(target):
    script = getattr(target.owner, "body_script", None)
    return (script is not None and script.component.world.hero == DVA and not script.component.owner_dead
            and script.component.fault is None and defenses.protection(target.owner) is None)


def _hack_clear(fight, caster, origin, direction, reach):
    """Use the Match collision hosts for Hack's reconstructed line of sight."""
    from ow174.game.combat import ray_world

    wall = ray_world(fight._collision(), origin, direction, reach)
    return (wall is None or wall >= reach) and fight._shield_hit(caster, origin, direction, reach) is None


def hack_targets(fight, caster, state):
    """Reconstruct aimed Hack acquisition and the retained-target check."""
    from ow174.game.combat import aim, ray_capsule

    radius = expr.to_float(state.evaluate(state.node.field("m_radius"), watch=False))
    if fight.match.collision_world is None or not math.isfinite(radius) or radius <= 0:
        return ()
    origin = fight._eye(caster)
    facing = aim(caster.mover.yaw, caster.mover.pitch)
    retain = state.node.pos == 52
    prior = state.instance.find_var(graph.INSTANCE, 375).value() if retain else ()
    selected = []
    for target in fight._enemies(caster):
        if not _eligible(target):
            continue
        bottom, top = target.segment()
        if retain:
            if not isinstance(prior, tuple) or expr.Entity(target.entity) not in prior:
                continue
            offset = (bottom[0] - origin[0], max(bottom[1], min(top[1], origin[1])) - origin[1],
                      bottom[2] - origin[2])
            if sum(axis * forward for axis, forward in zip(offset, facing, strict=True)) <= 0:
                continue
            distance = math.hypot(*offset)
            direction = tuple(axis / distance for axis in offset)
            reach = max(0.0, distance - target.radius)
        else:
            direction = facing
            reach = ray_capsule(origin, direction, bottom, top, target.radius)
        if reach is None or reach > radius:
            continue
        if _hack_clear(fight, caster, origin, direction, reach):
            selected.append((reach, target.entity))
    return tuple(expr.Entity(entity) for _, entity in sorted(selected)[:1])


def track(match, state):
    site = state.instance.graph.index, state.node.pos
    if site not in ((EMP, 40), (HACK, 50), (HACK, 52), (HACK, 68)):
        return False
    caster = match._message_actor(state.instance.component.entity)
    if caster is None or caster.body_script.hero != HERO:
        return False
    select = hack_targets if site[0] == HACK else targets
    selected = select(match.combat, caster, state)
    state.instance.write(expr.dynamic(state.node.field("m_8A2905A0")), selected)
    return True


def tracked_ray(match, state):
    """Recheck LOS to the live target selected by the Hack graph."""
    if (state.instance.graph.index, state.node.pos) != (HACK, 59):
        return NotImplemented
    caster = match._message_actor(state.instance.component.entity)
    if caster is None or caster.body_script.hero != HERO:
        return NotImplemented
    miss = runtime.RaycastResult(False, expr.Entity(0), None, None)
    if match.collision_world is None:
        return miss
    fight = match.combat
    selected = state.instance.find_var(graph.INSTANCE, 8909).value()
    for target in fight._enemies(caster):
        if expr.Entity(target.entity) != selected or not _eligible(target):
            continue
        origin = fight._eye(caster)
        x, y, z = target.feet
        point = x, y + target.eye, z
        offset = tuple(end - start for start, end in zip(origin, point, strict=True))
        distance = math.hypot(*offset)
        if distance:
            direction = tuple(axis / distance for axis in offset)
            reach = max(0.0, distance - target.radius)
            if not _hack_clear(fight, caster, origin, direction, reach):
                return miss
        return runtime.RaycastResult(True, expr.Entity(target.entity), expr.Vec3(*point), None)
    return miss


def modify_health(world, state, elapsed_ms):
    """Reconstruct the EMP shield-pool target for a hosted projected barrier."""
    if (world.match is None or world.hero != DVA or state.instance.component.strict
            or (state.instance.graph.index, state.node.pos) != (PULSE, 9)):
        return NotImplemented
    if not elapsed_ms:
        config = state.node.field("m_modifyHealth")
        tags = {tag["m_EE3055BD"]["m_identifier"] for tag in config["m_148F3152"]}
        if tags != {"0x0D8000000000025A", "0x0D80000000002DA6", "0x0D80000000002910"}:
            return NotImplemented
        match = world.match
        target = match._message_actor(state.instance.component.entity)
        bubble = match.combat.zarya_bubbles.for_target(target) if target is not None else None
        if bubble is not None:
            amount = expr.to_float(state.evaluate(config["m_amount"], watch=False))
            if math.isfinite(amount) and amount > 0:
                match.combat.zarya_bubbles.damage(bubble, amount, None, credit_energy=False)
        state.request_finish()
    return True


def _damage_reinhardt_barriers(fight, caster, instance, pulse):
    """Host the authored 003A-tagged pulse on the reconstructed 011A shield surface."""
    from ow174.game import reinhardt_barrier

    if fight.match.collision_world is None:
        return
    radius = expr.to_float(instance.evaluate(instance.graph.node(40).field("m_radius")))
    config = pulse.node(3).field("m_modifyHealth")
    amount = expr.to_float(instance.evaluate(config["m_amount"]))
    if not all(math.isfinite(value) and value > 0 for value in (radius, amount)):
        return
    shields = tuple(reinhardt_barrier.blast_targets(
        fight, caster, fight._eye(caster), radius, blocked_by_barriers=False,
    ))
    for shield, _ in shields:
        reinhardt_barrier.damage(shield, amount)


def dispatch(match, instance, node):
    """Submit only authored Sombra edges through the regular PlayScript core."""
    site = instance.graph.index, node.pos
    if site not in ((EMP, 8), (PULSE, 6), (HACK, 141)) or instance.component.strict:
        return NotImplemented
    casting = site[0] != PULSE
    caller = instance.component.entity
    source = caller if casting else instance.source_entity()
    caster = match._message_actor(source)
    if caster is None or caster.body_script.hero != HERO:
        return NotImplemented
    resource = play.resolve_resource(instance, node)
    expected = PULSE if site[0] == EMP else EFFECT
    if resource is None or resource.guid != ((graph.GRAPH_TYPE << 48) | expected):
        return NotImplemented
    definition = graph.graph(expected)
    if definition is None:
        return NotImplemented
    request = play.resolve_request(
        instance, node, resource, context_provider=lambda: play.InvocationContext(caller, caller, source),
    )
    if site[0] == EMP:
        _damage_reinhardt_barriers(match.combat, caster, instance, definition)
    if request is not None:
        for target in match.combat._enemies(caster):
            if target.entity not in request.targets or not _eligible(target):
                continue
            component = target.owner.body_script.component
            if not casting and component is not instance.component:
                continue
            component.play_loaded_graph(
                definition, source=request.context.instigator, params=request.params,
                overrides=resource.overrides, policy=request.policy, instigator=request.context.instigator,
            )
    return True


def notify_damage(match, shooter, victim, tags, sender):
    """Route effective tagged damage to 0B1B/106 with its current attacker."""
    recipient = getattr(victim, "body_script", None)
    attacker = getattr(shooter, "body_script", None)
    if getattr(recipient, "hero", None) != HERO or attacker is None:
        return
    component = recipient.component
    active = component.entity_var(8358, create=False)
    if component.strict or active is None or not expr.truthy(active.value()):
        return
    source = attacker.component.entity if sender is None else sender
    match.queue_body_message(component.entity, DAMAGE_MESSAGE, {0x3D: tuple(map(expr.Asset, tags))}, source)


def retire_applications(component):
    """Detach Match-hosted Sombra graphs when their D.Va body dies."""
    if component.strict or component.world.hero != DVA:
        return
    for instance in tuple(component.instances.values()):
        if instance.graph.index in (PULSE, EFFECT):
            component.destroy(instance)


def filter_body(world, cfg, instance):
    """Reconstruct the scoped Hack target filter and D.Va receiver classification."""
    if world.match is None or instance is None or cfg.get("$") != "STU_AC829876":
        return NotImplemented
    if world.hero == HERO and instance.graph.index == HACK and not instance.component.strict:
        node = instance.graph.node(204)
        if node is not None and cfg is node.field("m_condition"):
            ray = instance.state(instance.graph.node(59).state)
            result = tracked_ray(world.match, ray)
            return result is not NotImplemented and result.hit
    if world.hero != DVA or instance.graph.index != EFFECT:
        return NotImplemented
    node = instance.graph.node(21)
    if node is None or cfg is not node.field("m_condition"):
        return NotImplemented
    if instance.evaluate(cfg.get("m_entity")) != expr.Entity(world.body):
        return NotImplemented
    return any(target.entity == world.body and _eligible(target)
               for target in world.match.combat._targets())
