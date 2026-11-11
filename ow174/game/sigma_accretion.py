"""Selected authored 1BBB rock impact, 224E request and player displacement.

The launch acceleration and ring values come from the volley. The swept
projectile, admitted damage notification and impulse are host projections.
"""

import math

from ow174.game.script import expr, graph

HERO = 0x02E000000000023B
ACCRETION = 0x1BBB


def accretion_launch(state, forward):
    """Resolve the authored acceleration and launch bias of 1BBB node 9."""
    if state.instance.graph.index != ACCRETION or state.node.pos != 9:
        return None
    motions = state.node.field("m_projectileMotions") or []
    if len(motions) != 1 or motions[0].get("$") != "STU_E1A5B579":
        return None
    gravity = expr.to_float(state.evaluate(motions[0]["m_599AB8D7"], watch=False))
    launch = state.node.field("m_5EC1EB38")
    lift = expr.to_float(state.evaluate(launch["m_22CE7F30"], watch=False))
    # D5ECB4-D5ECEA adds the bias to Y before normalizing.
    x, y, z = (expr.f32(axis) for axis in forward)
    y = expr.f32(y + lift)
    squared = expr.f32(expr.f32(expr.f32(x * x) + expr.f32(y * y)) + expr.f32(z * z))
    direction = (tuple(expr.f32(axis / math.sqrt(squared)) for axis in (x, y, z))
                 if squared >= 1.1754943508222875e-36 else (-0.0, -1.0, -0.0))
    return gravity, direction


def accretion_knockback(state, modify):
    """Bake the authored strengths and rings for the scoped Accretion impulse."""
    if state.instance.graph.index != ACCRETION or state.node.pos != 9:
        return None

    def value(cfg):
        return expr.to_float(state.evaluate(cfg, watch=False))

    horizontal, vertical = (value(modify.get(key)) for key in ("m_E33EFEE1", "m_583C208B"))
    rings = []
    for row in modify.get("m_knockbackRings") or ():
        scales = tuple(value(row.get(key)) for key in ("m_9102DA3F", "m_579D7CFF", "m_10399AE7"))
        if scales[0] != scales[1] or scales[0] != scales[2]:
            return None
        rings.append((value(row["m_radius"]), scales[0]))
    if not rings or not all(math.isfinite(x) and x >= 0 for x in (horizontal, vertical)):
        return None
    return horizontal, vertical, tuple(rings)


def apply_accretion_impulse(fight, rocket, target, hit, point=None, distance=0.0):
    """Reconstruct horizontal and upward impulses using the authored values."""
    if rocket.accretion_source is None or hit.knockback is None or fight.vitals_of(target).dead:
        return
    horizontal, vertical, rings = hit.knockback
    dx, _, dz = rocket.direction
    if point is not None:
        dx, dz = target.feet[0] - point[0], target.feet[2] - point[2]
    length = math.hypot(dx, dz)
    scale = 0.0
    previous_radius, previous_scale = 0.0, rings[0][1]
    for radius, strength in rings:
        if distance <= radius:
            width = radius - previous_radius
            share = (distance - previous_radius) / width if width > 0 else 0
            scale = previous_scale + (strength - previous_scale) * share
            break
        previous_radius, previous_scale = radius, strength
    if not scale or not (horizontal or vertical):
        return
    mover = getattr(target.owner, "mover", None)
    if mover is None or fight.control_immune(target.owner):
        return
    lateral = horizontal * scale / length if length else 0.0
    mover.add_external_impulse((dx * lateral, vertical * scale, dz * lateral))


def accretion_hit(fight, rocket, target, dealt):
    """Feed the graph's tagged damage entry after an actual Accretion contact."""
    source = rocket.accretion_source
    shooter = rocket.shooter
    if (source is None or source[0] != shooter.body
            or shooter.body_script is None or dealt <= 0 or fight.vitals_of(target).dead):
        return
    component = shooter.body_script.component
    instance = component.instances.get(source[1])
    if instance is None or instance.graph.index != ACCRETION or component.strict:
        return
    from ow174.game.script import runtime

    hit = instance.graph.node(9).field("m_6396149F")["m_modifyHealth"]
    tags = tuple(instance.evaluate(row["m_EE3055BD"]) for row in hit["m_148F3152"])
    message = expr.guid_of(instance.graph.node(17).field("m_gameMessage")["m_gameMessage"])
    runtime.send_instance_message(
        instance, message, {0x15: expr.Entity(target.entity), 0x13: dealt, 0x3D: tags},
        shooter.body,
    )
    component.run_now()


def accretion_native(world, cfg, instance):
    if (instance.component.strict or instance.graph.index != ACCRETION or world.match is None
            or world.hero != HERO or instance.component.entity != world.body):
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


class _AccretionControlContext(expr.Context):
    """Read the target's actual gate variables without creating an effect instance."""

    def __init__(self, component, overrides):
        self.component = component
        self.overrides = {identifier & 0xFFFF: value for identifier, value in overrides}

    def var(self, scope, identifier):
        if scope == graph.INSTANCE:
            return self.overrides.get(identifier)
        variable = self.component.entity_var(identifier, create=False)
        return variable.value() if variable is not None else None

    def would_stack_to_top(self, var, priority):
        from ow174.game.script.runtime import would_stack_to_top

        if var is None or var[0] != graph.ENTITY:
            return False
        variable = self.component.entity_var(var[1], create=False)
        return variable is None or would_stack_to_top(variable, priority)


def play_accretion_control(world, instance, node):
    """Translate the scoped 224E request to the host's existing control clock."""
    if (instance.component.strict or instance.graph.index != ACCRETION or node.pos != 18
            or instance.component.entity != world.body):
        return False
    from ow174.game.script import play

    resource = play.resolve_resource(instance, node)
    entity = expr.to_entity(instance.evaluate(node.field("m_targets")))
    fight = world.match.combat
    target = next((t for t in fight._targets() if t.entity == entity), None)
    return apply_224e_control(fight, target, resource)


def apply_224e_control(fight, target, resource):
    """Apply the admitted 224E target gates and selected world stun clock."""
    if resource is None or resource.guid != ((graph.GRAPH_TYPE << 48) | 0x224E):
        return False
    duration = dict(resource.overrides).get(0x0D800000000000D7)
    if type(duration) not in (int, float) or not math.isfinite(duration) or duration <= 0:
        return False
    if target is None or fight.vitals_of(target).dead or fight.control_immune(target.owner):
        return True
    entity = target.entity
    script = getattr(target.owner, "body_script", None)
    definition = graph.graph(0x224E)
    if (definition is None or script is None or script.component.entity != entity
            or script.component.strict or script.component.owner_dead or script.component.fault is not None):
        return True
    context = _AccretionControlContext(script.component, resource.overrides)
    for pos in (4, 122):
        if not expr.truthy(expr.evaluate(definition.node(pos).field("m_condition"), context)):
            return True
    # The host retains the longer deadline and only interrupts again when
    # this request extends it. Native 224E's queue and status-filter services
    # are separate from these two target gates.
    seconds = expr.to_int(expr.f32(duration * 1000)) / 1000
    fight._apply_stun(target, seconds, extend_only=True)
    return True
