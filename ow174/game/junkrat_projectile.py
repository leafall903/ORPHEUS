"""Source0065 Frag/Total Mayhem launch and terrain response on the shared Rocket host."""
import math
from ow174.game import contacts
from ow174.game.collision import MOVER
from ow174.game.script import expr

HERO = 0x02E0000000000065


def profile(state):
    if state.instance.component.world.hero != HERO:
        return None
    frag = (state.instance.graph.index, state.node.pos) == (0x022B, 31)
    mayhem = state.instance.graph.index == 0x022A
    if not (frag or mayhem):
        return None
    number = lambda config: expr.to_float(state.evaluate(config, watch=False))
    gravity = 0.0
    for motion in state.node.field("m_projectileMotions") or ():
        if isinstance(motion, dict) and motion.get("$") == "STU_E1A5B579":
            value = number(motion.get("m_599AB8D7"))
            if math.isfinite(value) and value >= 0.0:
                gravity += value
    collision = state.node.field("m_8236947A") if frag else None
    radius = number(collision["m_9655ED90"]) if collision else 0.0
    bounce = state.node.field("m_E365DB20")
    response = (number(bounce["m_0D20DAFD"]), number(bounce["m_CE33458D"]) * .016,
                number(bounce.get("m_C29D35E8")) if bounce.get("m_C29D35E8") is not None else 1.0) if bounce else None
    limit = int(number(bounce["m_360294F0"])) if bounce else 0
    return gravity, radius, response, limit


def surface(rocket, collision, direction, step):
    from ow174.game.combat import EPSILON, _along

    if rocket.radius and step > EPSILON:
        hit = contacts.scene_sweep(collision.engine, rocket.position,
            _along(rocket.position, direction, step + 1e-6), ((0.0, 0.0, 0.0),), rocket.radius, MOVER)
        return (min(step, hit[0] * (step + 1e-6)), hit[2]) if hit is not None else (None, None)
    hit = collision.cast_ray(rocket.position, direction, step + 1e-6, MOVER)
    return (min(step, hit.distance), hit.normal) if hit is not None else (None, None)


def bounce(fight, rocket, direction, normal, distance, step, duration, integrated_duration, reflected, range_ends):
    """Retain source D77B80 normal restitution, tangent loss and strict count bound."""
    from ow174.game.combat import EPSILON, _along, _dot

    used = duration * distance / step if step > EPSILON else 0.0
    if _dot(normal, direction) > 0:
        normal = tuple(-axis for axis in normal)
    vx, vy, vz = rocket.velocity if rocket.velocity is not None else tuple(axis * rocket.speed for axis in direction)
    impact = vx, vy + rocket.gravity * (integrated_duration - used), vz
    approach = _dot(impact, normal)
    outgoing = impact
    if approach < 0:
        restitution, tangent_loss, tangent_retention = rocket.terrain_bounce
        tangent = _along(impact, normal, -approach)
        tangent_speed = math.hypot(*tangent)
        scale = tangent_retention * max(1.0 - tangent_loss / tangent_speed, 0.0) if tangent_speed else 0.0
        outgoing = _along(tuple(axis * scale for axis in tangent), normal, -approach * restitution)
    from ow174.game.combat import TICK

    fight._amplify_flight(rocket, direction, distance, fight.tick - duration / TICK,
                          duration / (TICK * step) if step > EPSILON else 0.0)
    point = _along(rocket.position, direction, distance)
    rocket.position = _along(point, normal, 1e-3)
    rocket.velocity = outgoing
    rocket.speed = math.hypot(*outgoing)
    if rocket.speed > EPSILON:
        rocket.direction = tuple(axis / rocket.speed for axis in outgoing)
    rocket.flown += used
    rocket.travelled += distance
    rocket.left -= used
    rocket.terrain_bounces += 1
    if rocket.terrain_bounces > rocket.terrain_bounce_limit:
        rocket.speed = 0.0
        fight._landed(rocket)
    elif rocket.left <= 0 or range_ends and distance == step:
        fight._expire(rocket)
    elif duration > used:
        fight._fly_step(rocket, duration - used, reflected=reflected)
