"""Host Sigma's authored Accretion hit event and timed knockdown request."""

import math

from ow174.game.script import expr, graph, play, runtime

HERO = 0x02E000000000023B
CAST = 0x1BBB
KNOCKDOWN = 0x224E


def launch_direction(state, direction):
    config = state.node.field("m_5EC1EB38") or {}
    lift = expr.to_float(state.evaluate(config.get("m_22CE7F30"), watch=False))
    x, y, z = direction
    biased = (x, y + lift, z)
    length = math.hypot(*biased)
    return tuple(axis / length for axis in biased) if length else (0.0, -1.0, 0.0)


def direct_hit(fight, rocket, target, dealt):
    shooter = rocket.shooter
    body, instance_id = rocket.accretion_source
    script = shooter.body_script
    if dealt <= 0 or fight.vitals_of(target).dead or shooter.body != body or script is None or script.retired:
        return
    component = script.component
    instance = component.instances.get(instance_id)
    if instance is None or instance.graph.index != CAST or component.strict:
        return
    hit = instance.graph.node(9).field("m_6396149F")["m_modifyHealth"]
    tags = tuple(instance.evaluate(row["m_EE3055BD"]) for row in hit["m_148F3152"])
    message = expr.guid_of(instance.graph.node(17).field("m_gameMessage")["m_gameMessage"])
    runtime.send_instance_message(instance, message, {
        0x15: expr.Entity(target.entity), 0x13: dealt, 0x3D: tags,
    }, body)
    component.run_now()


def native(world, cfg, instance):
    if (world.match is None or world.hero != HERO or instance is None or instance.component.strict
            or instance.graph.index != CAST or instance.component.entity != world.body):
        return NotImplemented
    caster = next((player for player in world.match.players
                   if player.has_body and not player.gone and player.body == world.body), None)
    if caster is None:
        return NotImplemented
    if cfg is instance.graph.node(16).field("m_condition"):
        entity = expr.to_entity(instance.evaluate(cfg["m_entity"]))
        return any(target.entity == entity for target in world.match.combat._enemies(caster))
    if cfg is instance.graph.node(19).field("m_value"):
        return expr.Vec3(*caster.mover.position)
    return NotImplemented


class ControlContext(expr.Context):
    def __init__(self, component, overrides):
        self.component = component
        self.overrides = {identifier & 0xFFFF: value for identifier, value in overrides}

    def var(self, scope, identifier):
        if scope == graph.INSTANCE:
            return self.overrides.get(identifier)
        variable = self.component.entity_var(identifier, create=False)
        return variable.value() if variable is not None else None

    def would_stack_to_top(self, var, priority):
        if var is None or var[0] != graph.ENTITY:
            return False
        variable = self.component.entity_var(var[1], create=False)
        return variable is None or runtime.would_stack_to_top(variable, priority)


def apply_control(world, instance, node):
    if (world.match is None or world.hero != HERO or instance.component.strict
            or instance.graph.index != CAST or node.pos != 18 or instance.component.entity != world.body):
        return False
    resource = play.resolve_resource(instance, node)
    if resource is None or resource.guid != ((graph.GRAPH_TYPE << 48) | KNOCKDOWN):
        return False
    seconds = dict(resource.overrides).get(0x0D800000000000D7)
    if type(seconds) not in (int, float) or not math.isfinite(seconds) or seconds <= 0:
        return False

    entity = expr.to_entity(instance.evaluate(node.field("m_targets")))
    fight = world.match.combat
    target = next((target for target in fight._targets() if target.entity == entity), None)
    if target is None or fight.vitals_of(target).dead:
        return True
    definition = graph.graph(KNOCKDOWN)
    script = getattr(target.owner, "body_script", None)
    if (definition is None or script is None or getattr(script, "retired", False)
            or script.component.entity != entity or script.component.strict
            or script.component.owner_dead or script.component.fault is not None):
        return True
    context = ControlContext(script.component, resource.overrides)
    for pos in (4, 122):
        if not expr.truthy(expr.evaluate(definition.node(pos).field("m_condition"), context)):
            return True

    milliseconds = expr.to_int(expr.f32(seconds * 1000))
    deadline = fight.tick + (milliseconds + 15) // 16
    if fight._replace_timed_control(target, deadline * 16):
        fight.stuns[entity] = deadline
        fight._interrupt(target)
    return True
