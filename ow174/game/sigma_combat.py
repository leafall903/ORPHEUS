"""Static host bindings for Sigma's authored weapon and Accretion."""

import math

from ow174.game import contacts, flashbang, fortify, knockback
from ow174.game.collision import MOVER
from ow174.game.script import expr, graph, play, runtime

HERO = 0x02E000000000023B
SPHERES = (0x0400000000002D35, 0x04000000000032B4)
ACCRETION = 0x1BBB
ROCK = 0x04000000000018B7
PROJECTILES = (*SPHERES, ROCK)
CHARGE_LOADOUTS = (0x0B9000000000028C, 0x0B9000000000028A, 0x0B900000000002F5)


def launch(shooter, state, direction):
    volley = state.instance.graph.index, state.node.pos
    if shooter.hero.guid != HERO or volley not in ((0x187F, 45), (0x187F, 56), (ACCRETION, 9)):
        return None
    motion, = state.node.field("m_projectileMotions")
    gravity = expr.to_float(state.evaluate(motion["m_599AB8D7"], watch=False))
    if volley[0] == ACCRETION:
        bias = state.node.field("m_5EC1EB38.m_22CE7F30")
        direction = flashbang.biased(direction, expr.to_float(state.evaluate(bias, watch=False)))
    return gravity, direction


def impact(state, field):
    config = state.node.field(field) or {}
    modify = config.get("m_modifyHealth") or {}

    def value(row, key):
        return expr.to_float(state.evaluate(row.get(key), watch=False))
    rings = tuple((value(row, "m_radius"), value(row, "m_579D7CFF"),
                   value(row, "m_10399AE7"), 1.0)
                  for row in modify.get("m_knockbackRings") or ())
    if not rings:
        return None
    horizontal, vertical = (value(modify, key) for key in ("m_E33EFEE1", "m_583C208B"))
    if not horizontal and not vertical:
        return None
    # The host treats the signed strength as a velocity contribution. Set the
    # per-contact limit from the ring profile, including the inward sphere pull.
    return knockback.Force(horizontal, abs(horizontal), vertical, abs(vertical), rings)


def sphere(state):
    bounce = state.node.field("m_E365DB20")
    direct = state.node.field("m_6396149F")

    def value(row, key):
        return expr.to_float(state.evaluate(row.get(key), watch=False))
    return (value(direct, "m_57DAE1B5"), value(direct, "m_1FFD8312"),
            int(value(bounce, "m_360294F0")), value(bounce, "m_0D20DAFD"),
            value(bounce, "m_C29D35E8"))


def wall_sweep(collision, rocket, length):
    end = tuple(a + b * length for a, b in zip(rocket.position, rocket.direction, strict=True))
    hit = contacts.scene_sweep(
        collision.engine, rocket.position, end, ((0., 0., 0.),), rocket.wall_radius, MOVER,
    )
    if hit is not None and sum(a * b for a, b in zip(rocket.direction, hit[2], strict=True)) < -1e-6:
        return hit[0] * length, hit[2]


def bounce(rocket, normal, distance, duration):
    travel = distance / rocket.speed
    rocket.position = tuple(p + d * distance + n * .001
                            for p, d, n in zip(rocket.position, rocket.direction, normal, strict=True))
    dot = sum(a * b for a, b in zip(rocket.velocity, normal, strict=True))
    rocket.velocity = tuple(
        expr.f32((axis - dot * n) * rocket.bounce_tangent - dot * n * rocket.bounce_normal)
        for axis, n in zip(rocket.velocity, normal, strict=True)
    )
    rocket.speed = math.hypot(*rocket.velocity)
    if rocket.speed:
        rocket.direction = tuple(v / rocket.speed for v in rocket.velocity)
    rocket.bounces -= 1
    rocket.flown += travel
    rocket.left -= travel
    return min(duration - travel, rocket.left) if rocket.speed else 0.


def damage_contact(fight, rocket, target, dealt):
    shooter = rocket.shooter
    if (rocket.sigma_instance is None or rocket.source_body != shooter.body or dealt <= 0
            or target.is_device or fight.vitals_of(target).dead or shooter.body_script is None):
        return
    component = shooter.body_script.component
    instance = component.instances.get(rocket.sigma_instance)
    if instance is None or instance.graph.index != ACCRETION or component.owner_dead:
        return
    message = expr.guid_of(instance.graph.node(17).field("m_gameMessage")["m_gameMessage"])
    runtime.send_instance_message(instance, message, {
        0x15: expr.Entity(target.entity), 0x13: dealt,
        0x3D: tuple(expr.Asset(tag) for tag in rocket.direct.tags),
    }, shooter.body)
    component.run_now()


def native(world, cfg, instance):
    if (instance is None or instance.graph.index != ACCRETION or world.match is None
            or world.hero != HERO or instance.component.entity != world.body):
        return NotImplemented
    caster = next((p for p in world.match.players if p.body == world.body and world.match._shown(p)), None)
    if caster is None:
        return NotImplemented
    if cfg == instance.graph.node(16).field("m_condition"):
        entity = expr.to_entity(instance.evaluate(cfg["m_entity"]))
        return any(t.entity == entity and not t.is_device for t in world.match.combat._enemies(caster))
    if cfg == instance.graph.node(19).field("m_value"):
        return expr.Vec3(*caster.mover.position)
    return NotImplemented


class _ControlContext(expr.Context):
    def __init__(self, component, overrides):
        self.component = component
        self.overrides = {key & 0xFFFF: value for key, value in overrides}

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
    if world.match is None or (instance.graph.index, node.pos) != (ACCRETION, 18):
        return False
    resource = play.resolve_resource(instance, node)
    if resource is None or resource.guid != 0x058000000000224E:
        return False
    duration = dict(resource.overrides).get(0x0D800000000000D7)
    definition = graph.graph(0x224E)
    if (definition is None or type(duration) not in (int, float)
            or not math.isfinite(duration) or duration <= 0):
        return True
    entity = expr.to_entity(instance.evaluate(node.field("m_targets")))
    fight = world.match.combat
    target = next((t for t in fight._targets() if t.entity == entity and not t.is_device), None)
    if target is None or fight.vitals_of(target).dead or fortify.active(fight, target.owner):
        return True
    script = target.owner.body_script
    if script is not None and not script.component.owner_dead:
        context = _ControlContext(script.component, resource.overrides)
        if all(expr.truthy(expr.evaluate(definition.node(pos).field("m_condition"), context))
               for pos in (4, 122)):
            fight.apply_stun(target, expr.to_int(expr.f32(duration * 1000)) / 1000, extend_only=True)
    return True
