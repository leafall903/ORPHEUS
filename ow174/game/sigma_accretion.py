"""Authored Sigma Accretion launch and direct-hit control routing."""

import math

from ow174.game.script import expr, play, runtime
from ow174.game.script.control_effect import apply_stun

HERO = 0x02E000000000023B
ACCRETION = 0x1BBB
LOADOUT = 0x0B9000000000028A


def launch(state, forward):
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


def hit(fight, rocket, target, dealt):
    """Feed the graph's tagged damage entry after an actual Accretion contact."""
    instance = rocket.accretion_source
    shooter = rocket.shooter
    if (rocket.source_body != shooter.body
            or shooter.body_script is None or dealt <= 0 or fight.vitals_of(target).dead):
        return
    component = shooter.body_script.component
    if component.instances.get(instance.id) is not instance or component.strict:
        return
    hit = instance.graph.node(9).field("m_6396149F")["m_modifyHealth"]
    tags = tuple(instance.evaluate(row["m_EE3055BD"]) for row in hit["m_148F3152"])
    message = expr.guid_of(instance.graph.node(17).field("m_gameMessage")["m_gameMessage"])
    runtime.send_instance_message(instance, message, {0x15: expr.Entity(target.entity), 0x13: dealt,
                                                      0x3D: tags}, shooter.body)
    component.run_now()


def query(world, cfg, instance):
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


def play_control(world, instance, node):
    """Translate the scoped 224E request to the host's existing control clock."""
    if (instance.component.strict or instance.graph.index != ACCRETION or node.pos != 18
            or world.match is None or world.hero != HERO or instance.component.entity != world.body):
        return False
    resource = play.resolve_resource(instance, node)
    entity = expr.to_entity(instance.evaluate(node.field("m_targets")))
    fight = world.match.combat
    target = next((t for t in fight._targets() if t.entity == entity), None)
    if target is not None:
        apply_stun(fight, target, resource)
