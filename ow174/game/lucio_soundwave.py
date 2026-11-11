"""Soundwave resource vector, target selection and host impulse."""

import math

from ow174.game.mover import TURN, f32
from ow174.game.script import expr

HERO, GRAPH = 0x02E0000000000079, 0x0776


def query(world, cfg, instance):
    cls = cfg.get("$")
    if (instance is None or instance.graph.index != GRAPH
            or cls not in ("STU_0762B33A", "STU_2200A66D", "STU_021D01CD")):
        return NotImplemented
    player = next((p for p in world.match.players if p.has_body and p.body == world.body
                   and p.body_script is not None and p.body_script.component is instance.component), None)
    if player is None:
        return expr.EVALUATION_FAILED
    if cls == "STU_0762B33A":
        return soundwave_direction(instance, cfg)
    if instance.evaluate(cfg.get("m_entity")) != expr.Entity(world.body):
        return NotImplemented
    yaw = player.mover.yaw * TURN
    pitch = 0.0 if cls == "STU_021D01CD" else player.mover.pitch * TURN
    return expr.Vec3(f32(math.sin(yaw) * math.cos(pitch)), f32(-math.sin(pitch)),
                    f32(math.cos(yaw) * math.cos(pitch)))


def soundwave_direction(instance, cfg):
    """CEB820's actual0776 family: desired yaw/pitch around its forward+up basis.

    This computes the resource's vector only. It does not apply health knockback
    to another body, choose resistance, or implement the general limiter API.
    """
    yaw_limit = instance.evaluate(cfg.get("m_5023B9B0"))
    pitch_limit = instance.evaluate(cfg.get("m_E26E5E09"))
    if yaw_limit != 90 or pitch_limit != 25:
        return NotImplemented
    desired = instance.evaluate(cfg.get("m_32B00625"))
    basis = instance.evaluate(cfg.get("m_3661BC45"))
    if (not isinstance(desired, expr.Vec3) or not isinstance(basis, expr.Vec3)
            or not all(map(math.isfinite, (*desired, *basis)))):
        return NotImplemented
    angles = []
    for value in (desired, basis):
        square = f32(sum(f32(axis * axis) for axis in value))
        if square < expr.EPSILON:
            return expr.EVALUATION_FAILED
        length = math.sqrt(square)
        x, y, z = (f32(axis / length) for axis in value)
        angles.append((math.atan2(x, z), math.asin(max(-1., min(1., -y)))))
    result = []
    for wanted, centre, limit in zip(angles[0], angles[1], (yaw_limit, pitch_limit), strict=True):
        difference = (wanted - centre + math.pi) % math.tau - math.pi
        limit = math.radians(limit)
        result.append(centre + max(-limit, min(limit, difference)))
    yaw, pitch = result
    horizontal = math.cos(pitch)
    return expr.Vec3(f32(math.sin(yaw) * horizontal), f32(-math.sin(pitch)),
                     f32(math.cos(yaw) * horizontal))



def resolve(fight, player, command, script) -> None:
    """0079's 0776 st11 health operation, once per authored activation.

    Radius and damage come from the target build's st10/st11 configuration.
    Reconstruction: st10's angle 70 is treated as a full cone angle, with
    capsule-centre angular selection and static line of sight. Native filter
    semantics and cross-hero interception remain unaccepted. The authored
    impulse uses an explicitly unresisted reconstructed target host.
    """
    from ow174.game.combat import EPSILON, _dot, _number, _sub, aim, hit_def, ray_world, segment_distance

    if player.hero.guid != HERO:
        return
    for instance in script.component.instances.values():
        if instance.graph.index != GRAPH:
            continue
        state, search = instance.states.get(11), instance.states.get(10)
        if state is None or search is None:
            continue
        counters = fight.melee_seen.setdefault(script.entity, {})
        key = (instance.id, state.index)
        seen = counters.get(key, 0)
        counters[key] = state.counter
        if state.counter == seen or not state.active or command is None:
            continue
        hit = hit_def(state, {"m_modifyHealth": state.node.field("m_modifyHealth")})
        radius = _number(search.evaluate(search.node.field("m_radius"), watch=False), 0.0)
        angle = _number(search.evaluate(search.node.field("m_A30B383E"), watch=False), 0.0)
        if hit is None or hit.amount <= 0 or radius <= 0 or not 0 < angle <= 360:
            continue
        impulse = state.evaluate(state.node.field("m_modifyHealth.m_1376B4DF"), watch=False)
        origin, direction = fight._eye(player), aim(*command.aim())
        threshold = math.cos(math.radians(angle / 2))
        view_tick = command.frame - command.view_delay(player.quanta) * 1e6 / player.quanta
        for target in fight._enemies(player):
            shown = fight.seen(target, view_tick)
            if shown is None:
                continue
            a, b = shown.segment()
            if segment_distance(origin, a, b) - shown.radius > radius:
                continue
            centre = (shown.feet[0], shown.feet[1] + shown.height / 2, shown.feet[2])
            offset = _sub(centre, origin)
            length = math.sqrt(_dot(offset, offset))
            if length > EPSILON:
                toward = tuple(value / length for value in offset)
                if _dot(toward, direction) < threshold:
                    continue
                wall = ray_world(fight._collision(), origin, toward, length)
                if wall is not None and wall < length:
                    continue
            if fight.damage_immune(target):
                continue
            absorbed = fight._damage(player, target, hit.amount, False, hit.loadout, command.frame)
            if not absorbed and isinstance(impulse, expr.Vec3):
                fight.queue_impulse(target, impulse)
