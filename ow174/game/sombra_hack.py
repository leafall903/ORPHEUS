"""Scoped 104319 Sombra Hack channel and Bastion skill silence."""

import math
from dataclasses import dataclass

from ow174.game import defensive_casts, reinhardt_barrier
from ow174.game.script import expr, graph, nodes, play, runtime
from ow174.game.script.driver import BASTION

HERO = 0x02E000000000012E
WEAPON, APPLICATION = 0x0B1B, 0x0B1F
SKILL_BUTTONS = 2 | 8 | 32


@dataclass
class Hack:
    application: object
    until: int
    base: object


def owner(world):
    if world.match is None or world.hero != HERO:
        return None
    return next((p for p in world.match.players if p.has_body and not p.gone and p.body == world.body), None)


def _targets(fight, caster):
    return (t for t in fight._enemies(caster)
            if getattr(getattr(t.owner, "hero", None), "guid", None) == BASTION
            and t.owner.body_script is not None and not defensive_casts.invulnerable(t.owner))


def _clear(fight, caster, origin, ray, reach):
    from ow174.game.combat import ray_world

    wall = ray_world(fight._collision(), origin, ray, reach)
    return (wall is None or wall >= reach) and reinhardt_barrier.intercept(
        fight, caster, origin, ray, reach) is None


def track_targets(world, state):
    from ow174.game.combat import aim, ray_capsule

    if state.instance.graph.index != WEAPON or state.node.pos not in (50, 52, 68):
        return NotImplemented
    caster = owner(world)
    if caster is None:
        return NotImplemented
    fight = world.match.combat
    radius = expr.to_float(state.evaluate(state.node.field("m_radius"), watch=False))
    origin, direction = fight._eye(caster), aim(caster.mover.yaw, caster.mover.pitch)
    locked = state.instance.find_var(expr.INSTANCE, 375).value()
    locked_ids = {expr.to_entity(value) for value in locked} if isinstance(locked, tuple) else set()
    found = []
    for target in _targets(fight, caster):
        a, b = target.segment()
        if state.node.pos == 52:
            if target.entity not in locked_ids:
                continue
            point = (a[0], max(a[1], min(b[1], origin[1])), a[2])
            delta = tuple(x - y for x, y in zip(point, origin, strict=True))
            distance = math.sqrt(sum(value * value for value in delta))
            if distance <= 0.0 or sum(x * y for x, y in zip(delta, direction, strict=True)) <= 0.0:
                continue
            hit, ray = max(0.0, distance - target.radius), tuple(value / distance for value in delta)
        else:
            ray = direction
            hit = ray_capsule(origin, ray, a, b, target.radius)
        if hit is not None and hit <= radius and _clear(fight, caster, origin, ray, hit):
            found.append((hit, target.entity))
    return tuple(entity for _, entity in sorted(found)[:1])


def track_ray(world, state):
    if state.instance.graph.index != WEAPON or state.node.pos != 59:
        return NotImplemented
    caster = owner(world)
    if caster is None:
        return NotImplemented
    fight = world.match.combat
    entity = expr.to_entity(state.instance.find_var(expr.INSTANCE, 8909).value())
    target = next((t for t in _targets(fight, caster) if t.entity == entity), None)
    if target is None:
        return False, None
    point = fight._eye(target.owner)
    origin = fight._eye(caster)
    delta = tuple(x - y for x, y in zip(point, origin, strict=True))
    distance = math.sqrt(sum(value * value for value in delta))
    clear = distance == 0 or _clear(fight, caster, origin, tuple(value / distance for value in delta),
                                     max(0.0, distance - target.radius))
    return clear, expr.Vec3(*point)


def prepare(caster, command):
    if caster.hero.guid != HERO or not command.buttons & 2:
        return
    script = caster.body_script
    for instance in script.component.instances.values():
        if instance.graph.index != WEAPON:
            continue
        ability = instance.state(53)
        if ability.flags & nodes.Ability.RUNNING and instance.find_var(expr.INSTANCE, 375).value():
            return
        search = instance.node_state(instance.graph.node(68))
        targets = track_targets(script.world, search)
        instance.write((expr.INSTANCE, 375), tuple(expr.Entity(entity) for entity in targets))


def filter_target(world, cfg, instance):
    if (world.hero != HERO or instance is None or instance.graph.index != WEAPON
            or cfg is not instance.graph.node(204).field("m_condition")):
        return NotImplemented
    caster = owner(world)
    if caster is None:
        return False
    entity = expr.to_entity(instance.evaluate(cfg.get("m_entity")))
    if not any(t.entity == entity for t in _targets(world.match.combat, caster)):
        return False
    with instance.component.parameter_scope({8: expr.Entity(entity)}):
        return expr.truthy(instance.evaluate(cfg["m_filter"].get("m_condition")))


def apply(world, instance, node):
    if instance.graph.index != WEAPON or node.pos != 141:
        return
    caster = owner(world)
    if caster is None:
        return
    resource = play.resolve_resource(instance, node)
    if resource is None or resource.guid & 0xFFFFFFFFFFFF != APPLICATION:
        return
    context = play.InvocationContext(caster.body, caster.body, caster.body)
    request = play.resolve_request(instance, node, resource, context_provider=lambda: context)
    definition = graph.graph(APPLICATION)
    if request is None or definition is None:
        return
    fight = world.match.combat
    for target in _targets(fight, caster):
        if target.entity not in request.targets:
            continue
        script = target.owner.body_script
        component = script.component
        application = component.play_loaded_graph(
            definition, source=request.context.caller, params=request.params,
            overrides=resource.overrides, policy=request.policy, instigator=request.context.instigator,
        )
        if application is None:
            continue
        duration = expr.to_float(application.evaluate(definition.node(96).field("m_value")))
        previous = fight.hacks.get(target.entity)
        base = previous.base if previous is not None else component.entity_var(888).base
        if previous is not None and previous.application is not application:
            old = previous.application
            if component.instances.get(old.id) is old:
                component.destroy(old)
        from ow174.game.combat import TICK

        fight.hacks[target.entity] = Hack(application, fight.tick + math.ceil(duration / TICK), base)
        component.set(888, True)
        if 29 in component.held:
            component.edges([(29, False)])
        for repair in component.instances.values():
            if repair.graph.index == 0x0153:
                ability = repair.states.get(61)
                if ability is not None:
                    ability.presses.clear()
                    ability.stop()
        script.self_repairs.clear()
        component.run_now()


def retire(fight, entity):
    effect = fight.hacks.pop(entity, None)
    if effect is None:
        return
    application = effect.application
    component = application.component
    if component.instances.get(application.id) is application:
        component.destroy(application)
    component.set(888, effect.base)


def update(fight):
    for entity, effect in tuple(fight.hacks.items()):
        # Native tag selection is unmounted. The application can take the 3s
        # fallback presentation branch, while the character input policy owns 5s.
        if fight.tick >= effect.until:
            retire(fight, entity)

def damage_interrupt(source, target):
    """Deliver the authored receiver event only for an active Hack channel."""
    script = getattr(target.owner, "body_script", None)
    if getattr(script, "hero", None) != HERO:
        return
    for instance in script.component.instances.values():
        if instance.graph.index == WEAPON:
            ability = instance.states.get(53)
            if ability is not None and ability.flags & nodes.Ability.RUNNING:
                runtime.send_message(script.component, 0x0240000000000011, {}, source)
            return
