"""Selected Sigma 1BBB launch, tagged impact and 224E control request."""

import math

from ow174.game import fortify
from ow174.game.script import expr, graph, play, runtime

HERO = 0x02E000000000023B
GRAPH = 0x1BBB


def launch(state, direction):
    if (state.instance.graph.index, state.node.pos) != (GRAPH, 9):
        return None
    lift = state.node.field("m_5EC1EB38.m_22CE7F30")
    x, y, z = direction
    y = expr.f32(y + expr.to_float(state.evaluate(lift, watch=False)))
    length = math.hypot(x, y, z)
    return tuple(expr.f32(axis / length) for axis in (x, y, z))


def knockback(state, modify):
    def value(config):
        return expr.to_float(state.evaluate(config, watch=False))

    horizontal = value(modify.get("m_E33EFEE1"))
    vertical = value(modify.get("m_583C208B"))
    rings = tuple((value(row["m_radius"]), value(row.get("m_579D7CFF")), value(row.get("m_10399AE7")))
                  for row in modify.get("m_knockbackRings") or ())
    return horizontal, vertical, rings


def impulse(fight, target, profile, direction, distance):
    if (profile is None or fight.vitals_of(target).dead or not hasattr(target.owner, "mover")
            or fight._protected(target.owner) or fortify.control_immune(target.owner)):
        return
    horizontal, vertical, rings = profile
    if not rings:
        return
    previous_radius = 0.0
    previous_xz, previous_y = rings[0][1:]
    for radius, xz, up in rings:
        if distance <= radius:
            width = radius - previous_radius
            share = (distance - previous_radius) / width if width > 0 else 0.0
            dx, _, dz = direction
            flat = math.hypot(dx, dz)
            lateral = horizontal * (previous_xz + (xz - previous_xz) * share) / flat if flat else 0.0
            lift = vertical * (previous_y + (up - previous_y) * share)
            fight.external_impulses[target.entity] = (dx * lateral, lift, dz * lateral)
            return
        previous_radius, previous_xz, previous_y = radius, xz, up


def hit(fight, rocket, target, dealt):
    script = rocket.shooter.body_script
    if (dealt <= 0 or fight.vitals_of(target).dead or script is None or script.retired
            or script.component.entity != rocket.source_entity or script.component.strict):
        return
    component = script.component
    instance = component.instances.get(rocket.accretion_instance)
    if instance is None or instance.graph.index != GRAPH:
        return
    modify = instance.graph.node(9).field("m_6396149F.m_modifyHealth")
    tags = tuple(instance.evaluate(row["m_EE3055BD"]) for row in modify["m_148F3152"])
    message = expr.guid_of(instance.graph.node(17).field("m_gameMessage.m_gameMessage"))
    runtime.send_instance_message(instance, message, {0x15: expr.Entity(target.entity), 0x13: dealt,
                                                      0x3D: tags}, rocket.source_entity)
    component.run_now()


def native(world, cfg, instance):
    if (instance is None or instance.component.strict or instance.graph.index != GRAPH
            or world.match is None or world.hero != HERO or instance.component.entity != world.body):
        return NotImplemented
    caster = next((p for p in world.match.players if p.body == world.body and p.spawned and not p.gone), None)
    if caster is None:
        return NotImplemented
    if cfg == instance.graph.node(16).field("m_condition"):
        entity = expr.to_entity(instance.evaluate(cfg["m_entity"]))
        return any(target.entity == entity for target in world.match.combat._enemies(caster))
    if cfg == instance.graph.node(19).field("m_value"):
        return expr.Vec3(*caster.mover.position)
    return NotImplemented


class ControlContext(expr.Context):
    def __init__(self, component, overrides):
        self.component = component
        self.overrides = {identifier & 0xFFFF: value for identifier, value in overrides}

    def var(self, scope, identifier):
        if scope == expr.INSTANCE:
            return self.overrides.get(identifier)
        variable = self.component.entity_var(identifier, create=False)
        return variable.value() if variable is not None else None

    def would_stack_to_top(self, var, priority):
        if var is None or var[0] != expr.ENTITY:
            return False
        variable = self.component.entity_var(var[1], create=False)
        return variable is None or runtime.would_stack_to_top(variable, priority)


def control(fight, instance, node):
    resource = play.resolve_resource(instance, node)
    if resource is None or resource.guid != ((graph.GRAPH_TYPE << 48) | 0x224E):
        return NotImplemented
    duration = dict(resource.overrides).get(0x0D800000000000D7)
    if type(duration) not in (int, float) or not math.isfinite(duration) or duration <= 0:
        return NotImplemented
    entity = expr.to_entity(instance.evaluate(node.field("m_targets")))
    target = next((target for target in fight._targets() if target.entity == entity), None)
    if target is None or fight.vitals_of(target).dead:
        return True
    script = target.owner.body_script
    definition = graph.graph(0x224E)
    if (definition is None or script is None or script.component.entity != entity
            or script.component.strict or script.component.owner_dead or script.component.fault is not None):
        return True
    context = ControlContext(script.component, resource.overrides)
    conditions = (definition.node(pos).field("m_condition") for pos in (4, 122))
    if all(expr.truthy(expr.evaluate(condition, context)) for condition in conditions):
        seconds = round(duration * 1000) / 1000
        fight.stun(target, seconds, extend_only=True)
    return True
