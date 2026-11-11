"""Scoped Match target acquisition for Sombra's authored Hack graph."""
import math
from dataclasses import replace

from ow174.game import immunity
from ow174.game.script import expr, graph

HERO = 0x02E000000000012E
WEAPON = 0x0B1B
EMP = 0x0B31
EMP_RECEIVER = 0x0B32
HACK_RECEIVER = 0x0B1F
TRACK_NODES = (50, 52, 68)


def track_targets(world, state):
    instance = state.instance
    if (not instance.component.strict and world.hero == HERO
            and instance.graph.index == EMP and state.node.pos == 40):
        return emp_targets(world, state)
    if (instance.component.strict or world.hero != HERO or instance.graph.index != WEAPON
            or state.node.pos not in TRACK_NODES):
        return NotImplemented
    from ow174.game import combat

    match = world.match
    fight = match.combat
    player = next((p for p in match.players if p.has_body and p.body == world.body), None)
    if player is None or not player.spawned or fight.is_dead(player) or match.collision_world is None:
        return ()
    radius = expr.to_float(state.evaluate(state.node.field("m_radius"), watch=False))
    if not math.isfinite(radius) or radius <= 0:
        return ()
    origin = fight._eye(player)
    direction = combat.aim(player.mover.yaw, player.mover.pitch)
    retaining = state.node.pos == 52
    locked = instance.find_var(graph.INSTANCE, 375).value()
    locked_ids = {expr.to_entity(value) for value in locked} if isinstance(locked, tuple) else set()
    found = []
    for target in fight._enemies(player):
        if (target.construct is not None or immunity.control_immune(target.owner, target.entity)
                or (retaining and target.entity not in locked_ids)):
            continue
        a, b = target.segment()
        nearest = (a[0], max(a[1], min(b[1], origin[1])), a[2])
        offset = combat._sub(nearest, origin)
        distance = math.sqrt(combat._dot(offset, offset))
        if distance - target.radius > radius:
            continue
        if retaining:
            if distance <= combat.EPSILON or combat._dot(offset, direction) <= 0:
                continue
            ray = tuple(axis / distance for axis in offset)
            hit = max(0.0, distance - target.radius)
        else:
            ray = direction
            hit = combat.ray_capsule(origin, ray, a, b, target.radius)
            if hit is None or hit > radius:
                continue
        wall = combat.ray_world(fight._collision(), origin, ray, hit)
        if ((wall is None or wall >= hit) and fight.barriers.for_target(target.owner) is None
                and fight.barriers.intercept(player, origin, ray, hit) is None):
            found.append((hit, target.entity))
    return (expr.Entity(min(found)[1]),) if found else ()


def emp_targets(world, state):
    from ow174.game import combat

    match = world.match
    fight = match.combat
    player = next((p for p in match.players if p.has_body and p.body == world.body), None)
    if player is None or fight.is_dead(player) or match.collision_world is None:
        return ()
    radius = expr.to_float(state.evaluate(state.node.field("m_radius"), watch=False))
    if not math.isfinite(radius) or radius <= 0:
        return ()
    origin = fight._eye(player)
    found = []
    for target in fight._enemies(player):
        if (target.construct is not None or immunity.phased_out(target.owner, target.entity)
                or immunity.cryo_active(target.owner, target.entity)):
            continue
        a, b = target.segment()
        nearest = (a[0], max(a[1], min(b[1], origin[1])), a[2])
        offset = combat._sub(nearest, origin)
        distance = math.sqrt(combat._dot(offset, offset))
        if distance - target.radius <= radius and combat.capsule_visible(
                fight._collision(), origin, a, b, target.radius):
            found.append(expr.Entity(target.entity))
    for barrier in emp_barriers(fight, player):
        anchor = barrier.centre()
        if math.dist(origin, anchor) <= radius and combat.capsule_visible(
                fight._collision(), origin, anchor, anchor, 0.0):
            found.append(expr.Entity(barrier.entity))
    return tuple(found)


def emp_barriers(fight, caster):
    return (*fight.barriers.world.enemies(caster), *fight.barriers.reinhardt.enemies(caster))


def track_body_ray(world, state):
    instance = state.instance
    if (instance.component.strict or world.hero != HERO or instance.graph.index != WEAPON
            or state.node.pos != 59):
        return NotImplemented
    from ow174.game import combat

    match = world.match
    fight = match.combat
    player = next((p for p in match.players if p.has_body and p.body == world.body), None)
    if player is None or fight.is_dead(player) or match.collision_world is None:
        return False, None
    entity = expr.to_entity(instance.find_var(graph.INSTANCE, 8909).value())
    target = next((t for t in fight._enemies(player) if t.entity == entity and t.construct is None), None)
    if (target is None or immunity.control_immune(target.owner, target.entity)
            or fight.barriers.for_target(target.owner) is not None):
        return False, None
    origin = fight._eye(player)
    point = (target.feet[0], target.feet[1] + target.eye, target.feet[2])
    offset = combat._sub(point, origin)
    distance = math.sqrt(combat._dot(offset, offset))
    if distance <= combat.EPSILON:
        return True, expr.Vec3(*point)
    direction = tuple(axis / distance for axis in offset)
    reach = max(0.0, distance - target.radius)
    wall = combat.ray_world(fight._collision(), origin, direction, reach)
    clear = wall is None and fight.barriers.intercept(player, origin, direction, reach) is None
    return clear, expr.Vec3(*point)


def body_condition(world, cfg, instance):
    if instance is None or instance.component.strict or world.match is None:
        return NotImplemented
    index = instance.graph.index
    fight = world.match.combat
    if index == WEAPON and world.hero == HERO and cfg is instance.graph.node(204).field("m_condition"):
        caster = next((p for p in world.match.players if p.has_body and p.body == world.body), None)
        if caster is None or fight.is_dead(caster):
            return False
        entity = expr.to_entity(instance.evaluate(cfg.get("m_entity")))
        return any(t.entity == entity and t.construct is None
                   and not immunity.control_immune(t.owner, t.entity)
                   and fight.barriers.for_target(t.owner) is None for t in fight._enemies(caster))
    if index == 0x0B1F and cfg.get("$") == "STU_E72F6348":
        entity = expr.to_entity(instance.evaluate(cfg.get("m_entity")))
        return any(t.entity == entity and t.construct is None for t in fight._targets())
    return NotImplemented


def play_sombra_script(world, instance, node):
    index = instance.graph.index
    if index == EMP_RECEIVER and node.pos in (6, 15, 16):
        receiver = HACK_RECEIVER
        source = instance.source_entity()
    elif world.hero == HERO and (index, node.pos) in ((WEAPON, 141), (EMP, 8)):
        receiver = HACK_RECEIVER if index == WEAPON else EMP_RECEIVER
        source = instance.component.entity
    else:
        return False
    from ow174.game.script import play

    resource = play.resolve_resource(instance, node)
    if resource is None or resource.guid & 0xFFFFFFFFFFFF != receiver or not source:
        return False
    context = play.InvocationContext(source, source, source)
    request = play.resolve_request(instance, node, resource, context_provider=lambda: context)
    definition = graph.graph(receiver)
    if request is None or definition is None:
        return False
    fight = world.match.combat
    caster = next((p for p in world.match.players if p.has_body and p.body == source), None)
    if receiver == EMP_RECEIVER:
        if caster is None or fight.is_dead(caster):
            return False
        barrier_config = definition.node(3).field("m_modifyHealth")
        barrier_damage = expr.to_float(instance.evaluate(barrier_config.get("m_amount")))
        if math.isfinite(barrier_damage) and barrier_damage > 0:
            for barrier in emp_barriers(fight, caster):
                if barrier.entity in request.targets:
                    fight.barriers.damage(barrier, barrier_damage, caster)
    for target in fight._targets():
        if target.entity not in request.targets or target.construct is not None or fight.vitals_of(target).dead:
            continue
        if receiver == EMP_RECEIVER and barrier_damage > 0:
            bubble = fight.barriers.attached_enemy(caster, target.entity)
            if bubble is not None:
                fight.barriers.damage(bubble, barrier_damage, caster)
        script = getattr(target.owner, "body_script", None)
        if script is None:
            continue
        component = script.component
        saved_now = component.now
        with component.operation():
            try:
                component.now = max(saved_now, instance.component.now)
                application = component.play_loaded_graph(
                    definition, source=request.context.caller, params=request.params,
                    overrides=resource.overrides, policy=request.policy,
                    instigator=request.context.instigator,
                )
                if application is not None:
                    if receiver == HACK_RECEIVER:
                        apply_status(fight, target.entity, application, pulse=instance if index == EMP_RECEIVER else None)
                    application.update()
            finally:
                component.now = saved_now
    return True


JUNKRAT = 0x02E0000000000065
JUNKRAT_SKILL_BUTTONS = 0x02 | 0x08 | 0x10 | 0x20
SILENCE = 888


def apply_status(fight, entity, application, *, pulse=None):
    from ow174.game.combat import TICK

    duration = expr.to_float(application.evaluate(application.graph.node(96).field("m_value")))
    if not math.isfinite(duration) or duration <= 0:
        return
    component = application.component
    _, deadlines = fight.sombra_hacks.setdefault(entity, (component.entity_var(SILENCE).base, {}))
    deadline = fight.tick + math.ceil(duration / TICK)
    deadlines[application.id] = deadline
    if pulse is not None and pulse.component is component:
        deadlines[pulse.id] = deadline
    component.set(SILENCE, True)
    if component.world.hero == JUNKRAT:
        interrupt_mine_cast(component)


def interrupt_mine_cast(component):
    from ow174.game.script import nodes

    for instance in tuple(component.instances.values()):
        if instance.graph.index != 0x096A:
            continue
        for state in tuple(instance.states.values()):
            if isinstance(state, nodes.Ability) and state.buttons()[0] == 35:
                state.presses.clear()
                state.stop()


def constrain(fight, player, command):
    if player.body in fight.sombra_hacks and player.hero.guid == JUNKRAT:
        return replace(command, buttons=command.buttons & ~JUNKRAT_SKILL_BUTTONS)
    return command


def retire(fight, entity, script):
    record = fight.sombra_hacks.pop(entity, None)
    if record is None or script is None or getattr(script, "retired", False):
        return
    component = script.component
    base, deadlines = record
    with component.operation():
        for instance_id in deadlines:
            if instance := component.instances.get(instance_id):
                component.destroy(instance)
        component.set(SILENCE, base)


def update(fight, tick):
    if not fight.sombra_hacks:
        return
    targets = {t.entity: t for t in fight._targets() if t.construct is None}
    for entity, (_, deadlines) in tuple(fight.sombra_hacks.items()):
        target = targets.get(entity)
        script = getattr(target.owner, "body_script", None) if target is not None else None
        if script is None or getattr(script, "retired", False) or fight.vitals_of(target).dead:
            retire(fight, entity, script)
            continue
        component = script.component
        ended = {i for i, deadline in deadlines.items() if deadline <= tick or i not in component.instances}
        active_sources = {component.instances[i].source_entity() for i in deadlines if i not in ended
                          and component.instances[i].graph.index == HACK_RECEIVER}
        ended.update(i for i in deadlines if i not in ended and component.instances[i].graph.index == EMP_RECEIVER
                     and component.instances[i].source_entity() not in active_sources)
        if len(ended) == len(deadlines):
            retire(fight, entity, script)
        elif ended:
            with component.operation():
                for instance_id in tuple(deadlines):
                    if instance_id not in ended:
                        continue
                    deadlines.pop(instance_id)
                    if instance := component.instances.get(instance_id):
                        component.destroy(instance)


EMP_REPLAY_FRAMES = 64  # authored pulse at +21 and cast tail within this window


def replay_emp(fight, tick):
    """Advance accepted EMP timers through prior world frames without consuming input."""
    from ow174.game.script import nodes

    match = fight.match
    if match.ended:
        return
    previous_tick = fight.tick
    for player in match.players:
        script = player.body_script
        if (script is None or script.hero != HERO or script.retired or fight.is_dead(player)
                or not match._shown(player) or script.frame >= tick - 1):
            continue
        component = script.component
        instance = next((i for i in component.instances.values() if i.graph.index == EMP), None)
        ability = instance.states.get(1) if instance is not None else None
        if not isinstance(ability, nodes.Ability) or not ability.flags & nodes.Ability.RUNNING:
            continue
        saved_frame, saved_now = component.frame, component.now
        start = max(script.frame + 1, previous_tick)
        end = min(tick - 1, script.frame + EMP_REPLAY_FRAMES)
        try:
            with component.operation():
                for frame in range(start, end + 1):
                    fight.tick = frame
                    component.frame = frame
                    component.now = max(saved_now, (frame + 1) * component.frame_ms - 1)
                    instance.update()
                    if not ability.flags & nodes.Ability.RUNNING:
                        break
        finally:
            fight.tick = previous_tick
            component.frame, component.now = saved_frame, saved_now


def replay_orphaned_hacks(fight, tick):
    """Poll authored source-loss timers when the recipient sends no frames."""
    if not fight.sombra_hacks:
        return
    targets = {t.entity: t for t in fight._targets() if t.construct is None}
    for entity, (_, deadlines) in tuple(fight.sombra_hacks.items()):
        target = targets.get(entity)
        script = getattr(target.owner, "body_script", None) if target is not None else None
        if script is None or getattr(script, "retired", False) or script.frame >= tick - 1:
            continue
        component = script.component
        saved_frame, saved_now = component.frame, component.now
        try:
            with component.operation():
                for instance_id, deadline in tuple(deadlines.items()):
                    instance = component.instances.get(instance_id)
                    if (instance is None or instance.graph.index != HACK_RECEIVER
                            or instance.source_entity() in targets):
                        continue
                    start = max(fight.tick, script.frame + 1)
                    for frame in range(start, min(tick - 1, deadline) + 1):
                        component.frame = frame
                        component.now = max(saved_now, (frame + 1) * component.frame_ms - 1)
                        instance.update()
                        if instance.stopped:
                            break
        finally:
            component.frame, component.now = saved_frame, saved_now


def emp_modify_health(world, state, elapsed_ms):
    instance = state.instance
    if (world.match is None or instance.component.strict or instance.graph.index != EMP_RECEIVER
            or state.node.pos != 9):
        return NotImplemented
    if elapsed_ms:
        return True
    from ow174.game.world import SHIELDS_POOL

    fight = world.match.combat
    entity = instance.component.entity
    target = next((t for t in fight._targets() if t.entity == entity and t.construct is None), None)
    if target is None:
        return True
    vitals = fight.vitals_of(target)
    if vitals.dead:
        return True
    config = state.node.field("m_modifyHealth")
    amount = expr.to_float(state.evaluate(config.get("m_amount"), watch=False))
    available = vitals.now[SHIELDS_POOL] + sum(pool.now for pool in vitals.temporary_shields.values())
    if math.isfinite(amount) and amount > 0 and available > 0:
        vitals.damage(min(amount, available))
        fight._sync_body_health(target.owner, vitals)
    return True
