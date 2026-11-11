"""Resource-backed launch and hit control for Sigma's 1BBB Accretion graph."""

import math

from ow174.game.script import expr, graph, play, runtime

HERO = 0x02E000000000023B
ACCRETION = 0x1BBB


def launch(state, direction):
    """Apply the authored vertical bias before normalizing the launch vector."""
    config = state.node.field("m_5EC1EB38")
    bias = expr.to_float(state.evaluate(config["m_22CE7F30"], watch=False))
    x, y, z = (expr.f32(axis) for axis in direction)
    y = expr.f32(y + bias)
    squared = expr.f32(expr.f32(expr.f32(x * x) + expr.f32(y * y)) + expr.f32(z * z))
    if squared < 1.1754943508222875e-36:
        return (0.0, -1.0, 0.0)
    return tuple(expr.f32(axis / math.sqrt(squared)) for axis in (x, y, z))


def knockback(state, definition):
    config = definition["m_modifyHealth"]

    def value(row, key):
        return expr.to_float(state.evaluate(row.get(key), watch=False))

    rings = tuple((value(row, "m_radius"), value(row, "m_579D7CFF"), value(row, "m_10399AE7"))
                  for row in config.get("m_knockbackRings") or ())
    return (value(config, "m_E33EFEE1"), value(config, "m_583C208B"), rings) if rings else None


def impulse(target, strengths, direction, distance=0.0):
    """Apply authored per-axis strengths through the server's colliding player mover."""
    mover = getattr(target.owner, "mover", None)
    if strengths is None or mover is None:
        return
    horizontal, vertical, rings = strengths
    dx, _, dz = direction
    length = math.hypot(dx, dz)
    previous_radius = 0.0
    previous_xz, previous_y = rings[0][1:]
    for radius, scale_xz, scale_y in rings:
        if distance <= radius:
            fraction = (distance - previous_radius) / (radius - previous_radius) if radius > previous_radius else 0.0
            xz = horizontal * (previous_xz + (scale_xz - previous_xz) * fraction)
            y = vertical * (previous_y + (scale_y - previous_y) * fraction)
            if not (xz or y):
                return
            lateral = xz / length if length else 0.0
            mover.add_external_impulse((dx * lateral, y, dz * lateral))
            return
        previous_radius, previous_xz, previous_y = radius, scale_xz, scale_y


def hit(fight, rocket, target, dealt):
    """Report the effective direct damage to the live cast's tagged message entry."""
    shooter = rocket.shooter
    script = shooter.body_script
    if (dealt <= 0 or fight.vitals_of(target).dead or rocket.source_entity != shooter.body
            or script is None or script.retired):
        return
    component = script.component
    instance = component.instances.get(rocket.accretion_instance)
    if instance is None or instance.graph.index != ACCRETION or component.strict:
        return
    config = instance.graph.node(9).field("m_6396149F")["m_modifyHealth"]
    tags = tuple(instance.evaluate(tag["m_EE3055BD"]) for tag in config["m_148F3152"])
    message = expr.guid_of(instance.graph.node(17).field("m_gameMessage")["m_gameMessage"])
    runtime.send_instance_message(instance, message, {0x15: expr.Entity(target.entity), 0x13: dealt,
                                                      0x3D: tags}, rocket.source_entity)
    component.run_now()


def native(world, cfg, instance):
    if (instance.component.strict or instance.graph.index != ACCRETION or world.match is None
            or world.hero != HERO or instance.component.entity != world.body):
        return NotImplemented
    cls = cfg.get("$")
    if cls not in ("STU_AC829876", "STU_71A87E92"):
        return NotImplemented
    caster = next((p for p in world.match.players if world.match._shown(p) and p.body == world.body), None)
    if caster is None:
        return NotImplemented
    if cfg == instance.graph.node(16).field("m_condition"):
        entity = expr.to_entity(instance.evaluate(cfg["m_entity"]))
        return any(target.entity == entity for target in world.match.combat._enemies(caster))
    if cfg == instance.graph.node(19).field("m_value"):
        return expr.Vec3(*caster.mover.position)
    return NotImplemented


class ControlContext(expr.Context):
    """Evaluate the requested target gates against the victim's existing body variables."""

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


def play_control(world, instance, node):
    """Admit the actual 224E request to the server control clock."""
    if (instance.component.strict or instance.graph.index != ACCRETION or node.pos != 18
            or instance.component.entity != world.body or world.hero != HERO):
        return
    resource = play.resolve_resource(instance, node)
    if resource is None or resource.guid != ((graph.GRAPH_TYPE << 48) | 0x224E):
        return
    duration = dict(resource.overrides).get(0x0D800000000000D7)
    if type(duration) not in (int, float) or not math.isfinite(duration) or duration <= 0:
        return
    entity = expr.to_entity(instance.evaluate(node.field("m_targets")))
    fight = world.match.combat
    target = next((t for t in fight._targets() if t.entity == entity), None)
    if target is None:
        return
    script = target.owner.body_script
    definition = graph.graph(0x224E)
    if (definition is None or script is None or script.component.entity != entity
            or script.component.strict or script.component.owner_dead or script.component.fault is not None):
        return
    context = ControlContext(script.component, resource.overrides)
    if all(expr.truthy(expr.evaluate(definition.node(pos).field("m_condition"), context)) for pos in (4, 122)):
        seconds = expr.to_int(expr.f32(duration * 1000)) / 1000
        fight._apply_stun(target, seconds, extend_only=True)
