"""Static ground confirmation, motion and damage for Sigma's authored Flux."""

import math
from dataclasses import replace

from ow174.game import phase_defenses
from ow174.game.collision import NOT_GROUND
from ow174.game.commands import JUMP
from ow174.game.mover import STEP
from ow174.game.script import expr, graph, play, runtime

HERO = 0x02E000000000023B
CAST = 0x1E41
EFFECT = 0x1DBC


def caster_for(fight, body, *, allow_dead=False):
    return next((player for player in fight.match.players if player.has_body and not player.gone
                 and player.body == body and player.hero.guid == HERO
                 and (allow_dead or not fight.is_dead(player))), None)


def cancel_warning(component):
    if component.strict:
        return
    for instance in component.instances.values():
        if instance.graph.index != CAST:
            continue
        warning = instance.states.get(instance.graph.node(31).state)
        if warning is not None and warning.active:
            warning.request(runtime.ABORT)


def retire_recipient(player):
    script = player.body_script
    if script is None or script.component.strict:
        return
    component = script.component
    for effect in tuple(component.instances.values()):
        if effect.graph.index != EFFECT:
            continue
        lift = effect.states.get(effect.graph.node(33).state)
        slam = effect.states.get(effect.graph.node(14).state)
        if (lift is not None and lift.active) or (slam is not None and slam.active):
            player.mover.driven = False
            player.mover.velocity = (0.0, 0.0, 0.0)
        component.destroy(effect)


def confirm(state):
    instance = state.instance
    world = instance.component.world
    match = getattr(world, "match", None)
    if (match is None or world.hero != HERO or instance.graph.index != CAST
            or state.node.pos != 23 or instance.component.entity != world.body):
        return NotImplemented
    if not state.active:
        return False
    caster = caster_for(match.combat, world.body)
    hit = None
    if caster is not None and match.collision_world is not None:
        from ow174.game.combat import aim

        direction = aim(caster.mover.yaw, caster.mover.pitch)
        distance = expr.to_float(state.evaluate(state.node.field("m_6386E7B4")))
        hit = match.collision_world.cast_ray(match.combat._eye(caster), direction, distance)
    if hit is None or hit.normal[1] <= 0 or hit.surface & NOT_GROUND:
        instance.follow(state.node, "m_425337C6", state)
        return True
    instance.write(expr.lvalue(state.node.field("m_30710B76")), expr.Vec3(*hit.point))
    instance.follow(state.node, "m_F6955E81", state)
    return True


def targets(fight, state):
    from ow174.game.combat import capsule_visible

    instance = state.instance
    if instance.component.strict or (instance.graph.index, state.node.pos) != (CAST, 37):
        return NotImplemented
    caster = caster_for(fight, instance.component.entity)
    ability = instance.states.get(instance.graph.node(3).state)
    centre = instance.context().var(graph.INSTANCE, 2478)
    if (caster is None or ability is None or not ability.flags & ability.RUNNING
            or not isinstance(centre, expr.Vec3) or fight.match.collision_world is None):
        return ()
    radius = expr.to_float(state.evaluate(state.node.field("m_radius"), watch=False))
    bottom = state.evaluate(state.node.field("m_05BE62DD"), watch=False)
    top = state.evaluate(state.node.field("m_D59188D5"), watch=False)
    if not isinstance(bottom, expr.Vec3) or not isinstance(top, expr.Vec3):
        return ()
    origin = (top.x, top.y, top.z)
    selected = []
    for target in fight._enemies(caster):
        if target.owner not in fight.match.players or phase_defenses.phase(target.owner)[0]:
            continue
        x, y, z = target.feet
        if math.hypot(x - centre.x, z - centre.z) > radius or not bottom.y <= y <= top.y:
            continue
        a, b = target.segment()
        if capsule_visible(fight._collision(), origin, a, b, target.radius):
            selected.append(expr.Entity(target.entity))
    return selected


def play_effect(fight, instance, node):
    if instance.component.strict or (instance.graph.index, node.pos) != (CAST, 38):
        return False
    caster = caster_for(fight, instance.component.entity)
    resource = play.resolve_resource(instance, node)
    definition = graph.graph(EFFECT)
    if (caster is None or definition is None or resource is None
            or resource.guid != ((graph.GRAPH_TYPE << 48) | EFFECT)):
        return False
    request = play.resolve_request(instance, node, resource, context_provider=lambda: play.InvocationContext(
        caster.body, caster.body, caster.body))
    if request is not None:
        for target in fight._enemies(caster):
            if target.entity not in request.targets or target.owner not in fight.match.players:
                continue
            script = target.owner.body_script
            if (script is not None and not script.retired and not script.component.strict
                    and script.component.fault is None):
                script.component.play_loaded_graph(
                    definition, source=request.context.caller, instigator=request.context.instigator,
                    params=request.params, overrides=resource.overrides, policy=request.policy,
                )
    return True


def active_motion(player):
    script = player.body_script
    if script is None or script.component.strict:
        return None, False
    effect = next((i for i in script.component.instances.values() if i.graph.index == EFFECT), None)
    if effect is None:
        return None, False
    lift = effect.states.get(effect.graph.node(33).state)
    slam = effect.states.get(effect.graph.node(14).state)
    rising = lift is not None and lift.active
    if not rising and (slam is None or not slam.active):
        return None, False
    return effect, rising


def step_pose(player, command, seconds, tick, speed_scale):
    effect, rising = active_motion(player)
    if effect is None:
        return False
    script = player.body_script
    if caster_for(player.match.combat, effect.source_entity(), allow_dead=True) is None:
        script.component.destroy(effect)
        return False

    mover = player.mover
    move_to = None
    if rising:
        # Static reconstruction of the upward physics request in 1E41 node 59.
        config = graph.graph(CAST).node(59)
        speed = config.field("m_6EFD110C")["m_value"]
        height = config.field("m_D80AEDB1")["m_value"]
        x, y, z = mover.position
        ground = mover.world.ground(x, z, y + STEP, y - height - 1, mover.category)
        if ground is not None and ground.normal[1] > mover.slope_cos and not ground.surface & NOT_GROUND:
            destination = max(y, ground.y + height)
            move_to = ((x, destination, z), speed)
    pose = replace(command, forward=0, right=0, buttons=command.buttons & ~JUMP)
    mover.velocity = (0.0, mover.velocity[1], 0.0)
    mover.step_pose(pose, seconds, tick, move_to=move_to, speed_scale=speed_scale)
    return True


def damage(fight, state, elapsed_ms):
    instance = state.instance
    if instance.component.strict or instance.graph.index != EFFECT or state.node.pos not in (12, 14):
        return NotImplemented
    slam = state.node.pos == 14
    caster = caster_for(fight, instance.source_entity(), allow_dead=slam)
    if caster is None:
        state.request_finish()
        return True
    target = next((target for target in fight._enemies(caster)
                   if target.entity == instance.component.entity), None)
    if slam and target is not None and (not elapsed_ms or target.owner.mover.airborne):
        return True
    state.request_finish()
    if target is not None and (slam or not elapsed_ms):
        config = state.node.field("m_modifyHealth")
        amount = expr.to_float(state.evaluate(config["m_amount"], watch=False))
        # The attached barrier receives the health event separately from the lift.
        bubble = fight.bubbles.for_target(target.owner)
        if bubble is not None:
            fight.bubbles.damage(bubble, amount, caster)
        else:
            loadout = expr.guid_of(state.evaluate(config["m_loadout"], watch=False))
            fight._damage(caster, target, amount, False, loadout, fight.tick)
    return True


def native(world, cfg, instance):
    if world.match is None or instance is None or instance.component.strict:
        return NotImplemented
    if instance.graph.index == EFFECT and instance.component.entity == world.body:
        amount = instance.graph.node(14).field("m_modifyHealth")["m_amount"]
        if cfg is amount["m_configVars"][0]:
            hp = world.match.combat.vitals.get(world.body)
            return hp.maximum if hp is not None else 0
    if (world.hero == HERO and instance.graph.index == CAST
            and instance.component.entity == world.body and cfg is instance.graph.node(77).field("m_value")):
        # The confirmed point belongs to the static world, with no attached mover transform.
        return instance.evaluate(cfg["m_BD631144"])
    return NotImplemented
