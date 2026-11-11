"""Reconstructed target and damage services for Sombra's authored Hack against Zen."""

import math

from ow174.game import sombra_emp
from ow174.game.commands import SECONDARY
from ow174.game.script import expr, graph, play, runtime

CAST = 0x0B1B
ROUTE = sombra_emp.SOMBRA, CAST, 141
TRACKERS = (50, 52, 68)
FRONT_CONE = math.cos(math.radians(30))


def caster_for(world):
    return next((p for p in world.match.players if p.has_body and p.body == world.body and not p.gone), None)


def choices(world, state, command=None, *, retained=None):
    from ow174.game.combat import aim, segment_distance

    fight = world.match.combat
    caster = caster_for(world)
    if caster is None or fight.is_dead(caster) or world.match.collision_world is None:
        return ()
    origin = fight._eye(caster)
    direction = aim(*(command.aim() if command is not None else (caster.mover.yaw, caster.mover.pitch)))
    radius = expr.to_float(state.evaluate(state.node.field("m_radius"), watch=False))
    selected = []
    for target in fight._enemies(caster):
        if (getattr(getattr(target.owner, "hero", None), "guid", None) != sombra_emp.ZENYATTA
                or (retained is not None and target.entity != retained)
                or sombra_emp.protected(fight, target)):
            continue
        a, b = target.segment()
        middle = tuple((lo + hi) * .5 for lo, hi in zip(a, b, strict=True))
        delta = tuple(v-o for v, o in zip(middle, origin, strict=True))
        length = math.dist(middle, origin)
        if length == 0 or segment_distance(origin, a, b)-target.radius > radius:
            continue
        alignment = sum(v*d for v, d in zip(delta, direction, strict=True))/length
        if alignment >= FRONT_CONE and fight.area_visible(caster, origin, target):
            selected.append((-alignment, length, target.entity))
    return (expr.Entity(min(selected)[2]),) if selected else ()


def targets(world, state):
    if (world.hero != sombra_emp.SOMBRA or world.match is None or state.instance.component.strict
            or state.instance.graph.index != CAST or state.node.pos not in TRACKERS):
        return None
    retained = None
    if state.node.pos == 52:
        retained = expr.to_entity(state.instance.find_var(expr.INSTANCE, 8909).value())
    return choices(world, state, retained=retained)


def prepare(script, command):
    if script.hero != sombra_emp.SOMBRA or script.world.match is None:
        return
    for instance in script.component.instances.values():
        if instance.graph.index != CAST:
            continue
        channel = instance.states.get(instance.graph.node(100).state)
        if channel is not None and channel.active:
            retained = expr.to_entity(instance.find_var(expr.INSTANCE, 8909).value())
            tracker = instance.node_state(instance.graph.node(52))
            if not choices(script.world, tracker, command, retained=retained):
                channel.request(runtime.ABORT)
                script.component.run_now()
                script.component.set(9061, ())
            return
        tracker = instance.node_state(instance.graph.node(50))
        selected = choices(script.world, tracker, command) if command.buttons & SECONDARY else ()
        script.component.set(9061, selected)
        return


def direct_effect(instance):
    if instance.graph.index != sombra_emp.EFFECT:
        return False
    value = instance.vars.get(10409)
    return value is not None and value.value() == (False,)


def predicate(world, cfg, instance):
    if world.match is None or instance is None or instance.component.strict:
        return NotImplemented
    if (world.hero == sombra_emp.SOMBRA and instance.graph.index == CAST
            and cfg is instance.graph.node(204).field("m_condition")):
        target = instance.find_var(expr.INSTANCE, 8909).value()
        tracker = instance.node_state(instance.graph.node(52))
        return target in choices(world, tracker, retained=expr.to_entity(target))
    if world.hero == sombra_emp.ZENYATTA and direct_effect(instance):
        if cfg is instance.graph.node(21).field("m_condition"):
            return True  # scoped receiver filter for the hosted player body
        if cfg is instance.graph.node(19).field("m_condition"):
            source = expr.to_entity(instance.evaluate(cfg["m_entity"]))
            return any(p.has_body and p.body == source and not p.gone for p in world.match.players)
    return NotImplemented


def route(world, instance, node, resource):
    if ((world.hero, instance.graph.index, node.pos) != ROUTE
            or world.match is None or instance.component.strict
            or resource.guid != (0x0580000000000000 | sombra_emp.EFFECT)):
        return False
    source = world.body
    context = play.InvocationContext(source, source, source)
    request = play.resolve_request(instance, node, resource, context_provider=lambda: context)
    definition = graph.graph(sombra_emp.EFFECT)
    if request is None or definition is None:
        return True
    caster = caster_for(world)
    if caster is None or world.match.combat.is_dead(caster):
        return True
    fight = world.match.combat
    for target in fight._enemies(caster):
        player = target.owner
        script = getattr(player, "body_script", None)
        hero = getattr(getattr(player, "hero", None), "guid", None)
        if (target.entity not in request.targets or hero != sombra_emp.ZENYATTA
                or script is None or script.retired or sombra_emp.protected(fight, target)
                or locked(script, script.frame)):
            continue
        component = script.component
        component.play_loaded_graph(
            definition, source=source, params=request.params, overrides=resource.overrides,
            policy=request.policy, instigator=request.context.instigator,
        )
        component.run_now()
    return True


def received_damage(player, source):
    if getattr(getattr(player, "hero", None), "guid", None) != sombra_emp.SOMBRA:
        return
    script = player.body_script
    if script is None or script.retired:
        return
    for instance in script.component.instances.values():
        if instance.graph.index == CAST:
            channel = instance.states.get(instance.graph.node(100).state)
            if channel is not None and channel.active:
                runtime.send_instance_message(instance, 0x0240000000000011, {0x3D: ()}, source)
                script.component.run_now()
            return


def locked(script, frame):
    for instance in script.component.instances.values():
        if direct_effect(instance):
            wait = instance.states.get(instance.graph.node(118).state)
            if wait is not None and wait.active and frame * script.frame_ms < wait.start + wait.ms:
                return True
    return False


def retire_finished(script):
    if script.hero != sombra_emp.ZENYATTA:
        return
    for instance in tuple(script.component.instances.values()):
        if direct_effect(instance):
            wait = instance.states.get(instance.graph.node(118).state)
            if wait is not None and wait.counter and not wait.active:
                script.component.destroy(instance)
