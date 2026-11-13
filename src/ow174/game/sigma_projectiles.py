"""Original023B Hypersphere profile on the shared Rocket host."""
from ow174.game import projectile_clock
from ow174.game.script import expr


def advance(fight, rocket):
    """Opt in only the original spheres/rock with a mapped world launch tick."""
    if ((rocket.source_graph, rocket.definition) not in (
            (0x187F, 0x0400000000002D35),
            (0x187F, 0x04000000000032B4),
            (0x1BBB, 0x04000000000018B7)) or type(rocket.last_tick) is not int):
        return False
    projectile_clock.advance_accepted(fight, rocket)
    return True


def hypersphere_motion(state):
    """Read both stock Hypersphere launch rows for the scoped sphere host."""
    node = state.node
    if state.instance.graph.index != 0x187F or node.pos not in (45, 56):
        return None
    bounce = node.field("m_E365DB20") or {}
    motions = node.field("m_projectileMotions") or ()
    if (bounce.get("$") != "STU_3FE1A93B" or len(motions) != 1
            or motions[0].get("$") != "STU_E1A5B579"):
        return None

    def value(row, key):
        return expr.to_float(state.evaluate(row.get(key), watch=False))

    hit = node.field("m_6396149F")
    return (value(motions[0], "m_599AB8D7"),
            value(hit, "m_57DAE1B5"), value(hit, "m_1FFD8312"),
            expr.to_int(state.evaluate(bounce.get("m_360294F0"), watch=False)),
            value(bounce, "m_0D20DAFD"), value(bounce, "m_C29D35E8"))



def surface(rocket, collision, direction, step):
    from ow174.game import contacts
    from ow174.game.collision import MOVER
    from ow174.game.combat import EPSILON, _along, _dot

    radius = rocket.terrain_bounce[0]
    if radius > 0:
        hit = contacts.scene_sweep(collision.engine, rocket.position,
            _along(rocket.position, direction, step), ((0.0, 0.0, 0.0),), radius, MOVER)
        if hit is not None and _dot(direction, hit[2]) < -EPSILON:
            return hit[0] * step, hit[2]
        return None, None
    hit = collision.cast_ray(rocket.position, direction, step)
    return (hit.distance, hit.normal) if hit is not None else (None, None)


def bounce(fight, rocket, direction, normal, distance, step, duration, previous_velocity, reflected):
    """Original D70627 fractional gravity and normalized D78961 response."""
    import math
    from ow174.game.combat import EPSILON, TICK, _along, _dot

    travel = duration * distance / step if step > EPSILON else 0.0
    fight._amplify_flight(rocket, direction, distance, fight.tick - duration / TICK,
                          duration / (TICK * step) if step > EPSILON else 0.0)
    centre = _along(rocket.position, direction, distance)
    if previous_velocity is None:
        raise ValueError("Hypersphere wall contact requires its actual incoming velocity")
    vx, vy, vz = previous_velocity
    delta = expr.f32(rocket.velocity[1] - vy)
    rocket.velocity = (vx, expr.f32(vy + expr.f32(delta * expr.f32(travel / duration))), vz)
    vn = _dot(rocket.velocity, normal)
    _, bounce_normal, bounce_tangent = rocket.terrain_bounce
    if vn < 0:
        rocket.velocity = tuple(
            expr.f32((axis - vn * n) * bounce_tangent - vn * n * bounce_normal)
            for axis, n in zip(rocket.velocity, normal, strict=True))
    if vn < 0 or distance == 0:
        squared = _dot(rocket.velocity, rocket.velocity)
        rocket.direction = (tuple(axis / math.sqrt(squared) for axis in rocket.velocity)
                            if squared >= 1.1754943508222875e-36 else normal)
        rocket.velocity = tuple(expr.f32(axis) for axis in rocket.direction)
        rocket.speed = 1.0
    else:
        rocket.speed = math.sqrt(_dot(rocket.velocity, rocket.velocity))
        if rocket.speed:
            rocket.direction = tuple(axis / rocket.speed for axis in rocket.velocity)
    rocket.terrain_bounce_limit -= 1
    rocket.position = _along(centre, normal, .001)
    rocket.flown += travel
    rocket.travelled += distance
    rocket.left -= travel
    if rocket.terrain_bounce_limit < 0:
        fight._landed(rocket)
    elif rocket.left <= 0:
        fight._expire(rocket)
    elif duration > travel:
        return min(duration - travel, rocket.left)
    return None
