"""Sigma Accretion launch and direct control requests for the Match host."""

import math

from ow174.game.script import expr, graph

HERO = 0x02E000000000023B
ACCRETION = 0x1BBB
PROJECTILE = 0x04000000000018B7


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
    runtime.send_instance_message(instance, message, {0x15: expr.Entity(target.entity), 0x13: dealt,
                                                      0x3D: tags}, shooter.body)
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

    def __init__(self, component, overrides, inactive_barrier_status=None):
        self.component = component
        self.overrides = {identifier & 0xFFFF: value for identifier, value in overrides}
        self.inactive_barrier_status = inactive_barrier_status

    def var(self, scope, identifier):
        if scope == graph.INSTANCE:
            return self.overrides.get(identifier)
        variable = self.component.entity_var(identifier, create=False)
        if variable is None:
            return None
        excluded = self.inactive_barrier_status if identifier == 2547 else None
        return variable.value(excluding=excluded)

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
    if resource is None or resource.guid != ((graph.GRAPH_TYPE << 48) | 0x224E):
        return False
    duration = dict(resource.overrides).get(0x0D800000000000D7)
    if type(duration) not in (int, float) or not math.isfinite(duration) or duration <= 0:
        return False
    entity = expr.to_entity(instance.evaluate(node.field("m_targets")))
    fight = world.match.combat
    target = next((t for t in fight._targets() if t.entity == entity), None)
    if target is None or fight.vitals_of(target).dead:
        return True
    script = target.owner.body_script
    definition = graph.graph(0x224E)
    if (definition is None or script is None or script.component.entity != entity
            or script.component.strict or script.component.owner_dead or script.component.fault is not None):
        return True
    barrier_status = None
    expiry = fight.zarya_barriers.expiries.get((entity, False))
    if expiry is not None and not expiry.started <= fight.tick <= expiry.tick:
        barrier = expiry.duration.instance
        # 0244 node 18 publishes the personal barrier's 224E immunity input.
        barrier_status = barrier.states.get(barrier.graph.node(18).state)
    context = _AccretionControlContext(script.component, resource.overrides, barrier_status)
    for pos in (4, 122):
        if not expr.truthy(expr.evaluate(definition.node(pos).field("m_condition"), context)):
            return True
    # The host retains the longer deadline and only interrupts again when
    # this request extends it. Native 224E's queue and status-filter services
    # are separate from these two target gates.
    seconds = expr.to_int(expr.f32(duration * 1000)) / 1000
    from ow174.game.combat import TICK

    if fight.tick + math.ceil(seconds / TICK) > fight.stuns.get(entity, 0):
        fight.stun(target, seconds)
    return True
