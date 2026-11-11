"""Scoped server services for the authored 0B1B Hack channel."""

import math
from dataclasses import dataclass

from ow174.game.script import expr, graph, play

HERO = 0x02E000000000012E
HACK = 0x0B1B
PHARAH = 0x02E0000000000008
PHARAH_SKILLS = 0x02 | 0x08 | 0x10 | 0x20 | 0x40


@dataclass
class Hack:
    component: object
    instance: object
    deadline: int


def caster(instance):
    component = instance.component
    world = component.world
    match = getattr(world, "match", None)
    if component.strict or match is None or world.hero != HERO or instance.graph.index != HACK:
        return None
    return next((p for p in match.players if p.has_body and not p.gone and p.body == component.entity), None)


def query(state):
    from ow174.game.combat import aim, ray_capsule

    player = caster(state.instance)
    if player is None:
        return ()
    fight = player.match.combat
    if fight.is_dead(player):
        return ()
    origin = fight._eye(player)
    direction = aim(player.mover.yaw, player.mover.pitch)
    reach = expr.to_float(state.evaluate(state.node.field("m_radius"), watch=False))
    locked = state.instance.find_var(graph.INSTANCE, 375).value()
    hits = []
    for target in fight._enemies(player):
        if state.node.pos == 52:
            if not isinstance(locked, tuple) or expr.Entity(target.entity) not in locked:
                continue
            point = (target.feet[0], target.feet[1] + target.eye, target.feet[2])
            offset = tuple(b - a for a, b in zip(origin, point, strict=True))
            if sum(a * b for a, b in zip(offset, direction, strict=True)) <= 0:
                continue
            distance = math.dist(origin, point) - target.radius
        else:
            distance = ray_capsule(origin, direction, *target.segment(), target.radius)
        if distance is not None and distance <= reach and visible(player, target):
            hits.append((distance, target.entity))
    return (expr.Entity(min(hits)[1]),) if hits else ()


def visible(player, target):
    from ow174.game import (
        barrier_pet,
        defenses,
        orisa_deployable,
        sigma_barrier,
        winston_barrier,
        zarya_barrier,
    )

    if defenses.phase(target.owner)[0]:
        return False
    fight = player.match.combat
    if any(bubble.body == target.entity for bubble in zarya_barrier.active(fight)):
        return False
    origin = fight._eye(player)
    colliders = barrier_pet.active_colliders(fight, fight._enemies(player))
    devices = (winston_barrier.colliders(fight, player.body, player.team)
               + orisa_deployable.colliders(fight, player.body, player.team)
               + sigma_barrier.colliders(fight, player.body, player.team))
    return fight._blast_reaches(origin, target, colliders, devices)


def prepare(player, command):
    from ow174.game.script.nodes import Ability

    if player.hero.guid != HERO or not command.buttons & 2:
        return
    for instance in player.body_script.component.instances.values():
        if instance.graph.index != HACK:
            continue
        ability = instance.states.get(53)
        selected = instance.find_var(graph.INSTANCE, 375).value()
        if ability is not None and ability.flags & Ability.RUNNING and selected:
            return
        search = instance.node_state(instance.graph.node(68))
        instance.write((graph.INSTANCE, 375), query(search))


def ray(state):
    player = caster(state.instance)
    if player is None:
        return False, None
    entity = expr.to_entity(state.instance.find_var(graph.INSTANCE, 8909).value())
    for target in player.match.combat._enemies(player):
        if target.entity == entity and visible(player, target):
            x, y, z = target.feet
            return True, expr.Vec3(x, y + target.eye, z)
    return False, None


def filter_target(instance, cfg):
    player = caster(instance)
    if player is None or cfg is not instance.graph.node(204).field("m_condition"):
        return NotImplemented
    entity = expr.to_entity(instance.evaluate(cfg.get("m_entity")))
    for target in player.match.combat._enemies(player):
        if target.entity == entity and visible(player, target):
            with instance.component.parameter_scope({8: expr.Entity(entity)}):
                return expr.truthy(instance.evaluate(cfg["m_filter"].get("m_condition")))
    return False


def apply(instance, node):
    from ow174.game.combat import TICK
    from ow174.game.script.nodes import Ability

    player = caster(instance)
    if player is None:
        return
    resource = play.resolve_resource(instance, node)
    if resource is None or resource.guid != 0x0580000000000B1F:
        return
    context = play.InvocationContext(player.body, player.body, player.body)
    request = play.resolve_request(instance, node, resource, context_provider=lambda: context)
    definition = graph.graph(0x0B1F)
    if request is None or definition is None:
        return
    fight = player.match.combat
    for target in fight._enemies(player):
        if target.entity not in request.targets or not visible(player, target):
            continue
        owner = target.owner
        script = getattr(owner, "body_script", None)
        if script is None:
            continue
        component = script.component
        application = component.play_loaded_graph(
            definition, source=request.context.caller, params=request.params, overrides=resource.overrides,
            policy=request.policy, instigator=request.context.instigator)
        if application is None:
            continue
        duration = expr.to_float(application.evaluate(definition.node(96).field("m_value")))
        if duration <= 0 or not math.isfinite(duration):
            component.destroy(application)
            continue
        fight.hacks.setdefault(target.entity, []).append(
            Hack(component, application, fight.tick + math.ceil(duration / TICK)))
        if getattr(getattr(owner, "hero", None), "guid", None) == PHARAH:
            component.edges([(button, False) for button in (11, 29, 35, 36, 37)
                             if button in component.held])
            for skill in tuple(component.instances.values()):
                if skill.graph.index in (0x25, 0x27, 0x7B, 0x2B):
                    for state in tuple(skill.states.values()):
                        if isinstance(state, Ability):
                            state.presses.clear()
                            state.stop()
            script.interrupt_control()
        component.run_now()


def active(combat, body, tick):
    return any(tick < item.deadline for item in combat.hacks.get(body, ()))


def received_damage(owner, source, tags):
    from ow174.game.script import runtime
    from ow174.game.script.nodes import Ability

    if getattr(getattr(owner, "hero", None), "guid", None) != HERO or owner.body_script is None:
        return
    component = owner.body_script.component
    if any(instance.graph.index == HACK and (state := instance.states.get(53)) is not None
           and state.flags & Ability.RUNNING for instance in component.instances.values()):
        runtime.send_message(component, 0x0240000000000011,
                             {0x3D: tuple(expr.Asset(tag) for tag in tags)}, source)


def update(combat):
    for body, effects in tuple(combat.hacks.items()):
        retained = []
        for effect in effects:
            if combat.tick < effect.deadline:
                retained.append(effect)
            elif effect.component.instances.get(effect.instance.id) is effect.instance:
                effect.component.destroy(effect.instance)
        if retained:
            combat.hacks[body] = retained
        else:
            del combat.hacks[body]


def retire(combat, body):
    for effect in combat.hacks.pop(body, ()):
        if effect.component.instances.get(effect.instance.id) is effect.instance:
            effect.component.destroy(effect.instance)
