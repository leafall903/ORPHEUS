"""Wall-contact server reconstruction for Lucio's authored 0263 controller."""

import math

from ow174.game.mover import FELL, IN_AIR, JUMPED, TOUCHING, TURN, f32
from ow174.game.script import expr

HERO = 0x02E0000000000079
GRAPH = 0x0263
STATE = 8
CONTACT = 1204
BACKWARDS = 9473
JUMP_ON_RELEASE = 32856


def controller(script):
    if script is None or script.hero != HERO or script.retired or script.component.fault is not None:
        return None
    return next((instance for instance in script.component.instances.values()
                 if instance.graph.index == GRAPH and not instance.stopped), None)


def feedback(instance, contact):
    if instance is None:
        return
    normal, side, kind = (contact[2], contact[3], contact[1]) if contact else ((0., 1., 0.), 0, 1)
    for key, value in ((1207, expr.Vec3(*normal)), (14965, side), (14980, kind)):
        target = instance.find_var(expr.INSTANCE, key, create=False)
        if target is None or not expr.same(target.value(), value):
            instance.write((expr.INSTANCE, key), value)
    component = instance.component
    value = component.entity_var(CONTACT, create=False)
    if expr.truthy(value.value() if value is not None else None) != bool(contact):
        component.set(CONTACT, bool(contact))


def cancel(player):
    instance = controller(player.body_script)
    if instance is None:
        return
    mover = player.mover
    mover.wall_jump = None
    if mover.wall_contact is not None:
        mover.wall_contact = None
        feedback(instance, None)


def _wall(mover, backwards):
    """Pair lower and upper rays on the same static plane using the mover mask."""
    x, y, z = mover.position
    yaw = mover.yaw * TURN
    angles = (0, -45, 45, -90, 90, -135, 135, 180) if backwards else (0, -45, 45, -90, 90)
    best = None
    normals = []
    for offset in angles:
        angle = yaw + math.radians(offset)
        direction = (math.sin(angle), 0., math.cos(angle))
        # The captured stationary-plane selector rejects at .6 m. The ray
        # heights and fan here approximate the unavailable engine sweeps.
        lower = mover.world.cast_ray((x, y + .35, z), direction, .6, mover.category)
        if lower is None or lower.distance >= .6 or abs(lower.normal[1]) > .5:
            continue
        upper = mover.world.cast_ray((x, y + .9, z), direction, .6, mover.category)
        if upper is None or not same_plane(lower, upper):
            continue
        normals.append(lower)
        if best is None or lower.distance < best.distance:
            best = lower
    if best is None:
        return None
    nx, _, nz = best.normal
    horizontal = math.hypot(nx, nz)
    nx, nz = nx / horizontal, nz / horizontal
    lateral = nx * math.cos(yaw) - nz * math.sin(yaw)
    side = 0
    if lateral < -.1:
        side = 1
    elif lateral > .1:
        side = 2
    # Keep the host's closest-face selection. Count qualifying rays on
    # that face for the original half-weight normal update.
    selected = [(hit.normal[0], hit.normal[2]) for hit in normals if same_plane(best, hit)]
    radius = mover.values.crouch_radius if mover.crouched else mover.values.stand_radius
    clearance = candidate_clearance(best.distance ** 2, radius)
    return (sum(x for x, _ in selected), 0., sum(z for _, z in selected)), side, clearance


def same_plane(first, second):
    if abs(second.normal[1]) > .5 or math.dist(first.normal, second.normal) > .1:
        return False
    offset = tuple(b - a for a, b in zip(first.point, second.point, strict=True))
    return abs(sum(a * b for a, b in zip(offset, first.normal, strict=True))) <= .03


def candidate_clearance(distance_squared, radius):
    """C4D102-C4D14C: nearest qualifying distance minus clamped radius."""
    return max(0., f32(f32(math.sqrt(distance_squared)) - max(f32(.1), f32(radius))))


def blend_normal(previous, samples):
    """C4CB43: normalize the horizontal ray sum plus half of the old normal."""
    x = f32(samples[0] + f32(.5 * previous[0]))
    z = f32(samples[2] + f32(.5 * previous[2]))
    length = math.hypot(x, z)
    return (f32(x / length), 0., f32(z / length)) if length else previous


def phase_backdate(velocity, reference_speed, dt, tick_us):
    """C50154-C50210: count whole ticks of extra elapsed time in kind4."""
    if reference_speed <= 0 or tick_us <= 0:
        return 0
    ratio = min(1., f32(f32(math.hypot(*velocity)) / f32(reference_speed)))
    delay = f32(f32(f32(1. - ratio) * 5.) * f32(dt))
    return int(f32(f32(delay / tick_us) * 1_000_000.))


def wall_force(velocity, normal, kind, age, dt, clearance, enabled=True):
    """Stationary C4FA10 normal force with the upward input at zero."""
    vx, vy, vz = velocity
    nx, _, nz = normal
    projection = vx * nx + vz * nz
    strength = 35.
    if enabled:
        horizontal = math.hypot(vx, vz)
        alignment = max(-1., min(1., projection / horizontal)) if horizontal else 0.
        if kind == 5:
            if alignment > 1.1920928955078125e-5:
                return velocity
        else:
            if f32(age) > f32(.3):
                removed = projection * (1. - .3 ** (dt / .016))
                vx, vz = vx - removed * nx, vz - removed * nz
                strength = 540.
            if alignment <= 1.1920928955078125e-5:
                strength *= 1. - abs(alignment)
    impulse = clearance * dt * strength
    return f32(vx - nx * impulse), vy, f32(vz - nz * impulse)


def accelerate_tangent(velocity, direction, wish, rate, dt, origin=(0., 0., 0.)):
    """C334A0 wall branch with stationary, horizontal movement axes."""
    vx, vy, vz = velocity
    tx, _, tz = direction
    current = f32(f32(f32(vx - origin[0]) * tx) + f32(f32(vz - origin[2]) * tz))
    change = max(0., min(f32(rate * dt), f32(wish - current)))
    return f32(vx + f32(change * tx)), vy, f32(vz + f32(change * tz))


def stationary_friction(velocity, normal, base, threshold, per_speed, run, reference, dt):
    """C39FA0 and default-PC C33090 for a stationary contact plane."""
    projection = sum(v * n for v, n in zip(velocity, normal, strict=True))
    relative = tuple(v - projection * n for v, n in zip(velocity, normal, strict=True))
    speed = math.hypot(*relative)
    if not speed:
        return velocity
    rate = base * run + max(0., speed - threshold) * per_speed * reference
    scale = min(speed, rate * dt) / speed
    return tuple(f32(v - direction * scale) for v, direction in zip(velocity, relative, strict=True))


def wall_tangent(base, normal, side):
    """C4AA00 stationary direction with world up and the default profile."""
    nx, _, nz = normal
    if side == 1:
        tx, tz = nz, -nx
    elif side == 2:
        tx, tz = -nz, nx
    else:
        bx, _, bz = base
        projection = bx * nx + bz * nz
        tx, tz = bx - projection * nx, bz - projection * nz
    length = math.hypot(tx, tz)
    return (f32(tx / length), 0., f32(tz / length)) if length > 1e-5 else (0., 0., 0.)


def wall_ramp(kind, phase_age):
    """C50A5F phase3 forward magnitude. Phase4 requests full forward."""
    return min(1., max(f32(.2), f32(f32(phase_age) / f32(.1)))) if kind == 3 else 1.


def advance(mover, frame, dt, jump, controller_frame, backwards):
    """Prepare contact, then take over velocity only in sustained phases."""
    old = mover.wall_contact
    vx, vy, vz = mover.velocity
    if old is None and (not jump or math.hypot(vx, vz) < .01):
        return False
    hit = _wall(mover, backwards)
    if hit is None:
        mover.wall_contact = None
        return False
    samples, side, clearance = hit
    relative = mover.velocity
    age = 0.
    if old is None:
        entered, kind = frame, 2
        previous = (0., 0., 0.)
    else:
        # Continuous corners retain their current phase.
        entered, kind, previous, previous_side = old
        if side == 0:
            side = previous_side  # retain orientation through a reconstructed front-face tie
        age = (frame - entered) * dt
        if kind == 4:
            extra = phase_backdate(relative, mover.values.run_forward, dt, round(dt * 1_000_000.))
            entered = max(0, entered - extra)
        elif (kind == 2 and age > .3) or (kind == 3 and age > .1):
            entered, kind = frame, kind + 1
    normal = blend_normal(previous, samples)
    mover.wall_contact = (entered, kind, normal, side)

    # Phase2 keeps ordinary movement after the wall helper.
    dispatch_kind = old[1] if old is not None else kind
    controller_age = max(0, frame - controller_frame) * dt
    if dispatch_kind == 2:
        vx, vy, vz = wall_force(relative, normal, dispatch_kind, controller_age, dt, clearance)
        mover.velocity = (vx, max(3., vy), vz)
        return kind != 2
    values = mover.values
    direction = wall_tangent(relative, normal, side)
    # BC0400 clears the jump/fall bits before sustained dispatch. Use that
    # state for velocity coefficients while keeping host collision bookkeeping.
    airborne = mover.flags_word & (IN_AIR & ~(JUMPED | FELL))
    if not airborne:
        vx, vy, vz = stationary_friction(
            relative, normal, values.decel_ground_base, values.decel_ground_base_speed,
            values.decel_ground_per_speed, values.run_forward, mover.data.run_forward, dt,
        )
    if direction != (0., 0., 0.):
        if not airborne:
            acceleration = values.accel_ground
        else:
            acceleration = values.accel_air_touching if mover.flags_word & TOUCHING else values.accel_air
        speed = values.crouch_forward if mover.crouched else values.run_forward
        vx, vy, vz = accelerate_tangent(
            (vx, vy, vz), direction, wall_ramp(dispatch_kind, age) * speed,
            acceleration * values.run_forward, dt,
        )
    vx, vy, vz = wall_force((vx, vy, vz), normal, dispatch_kind, controller_age, dt, clearance)
    mover.velocity = (vx, max(3., vy), vz)
    return True


def controls(player, stunned):
    script = player.body_script
    instance = controller(script)
    if stunned or player.mover.external_impulse or instance is None:
        return None
    state = instance.states.get(STATE)
    if state is None or not state.active or state.ending:
        return None
    slot = script.component.entity_var(BACKWARDS, create=False)
    backwards = expr.truthy(slot.value()) if slot is not None else False
    return state.start // script.frame_ms + 1, backwards


def owner(world, instance):
    return next((p for p in world.match.players if p.has_body and p.body == world.body
                 and p.body_script is not None and p.body_script.component is instance.component), None)


def native(world, cfg, instance):
    cls = cfg.get("$")
    if (instance.graph.index != GRAPH or cls not in (
        "STU_2200A66D", "STU_021D01CD", "STU_66F3B89E", "STU_309B0570", "STU_A2A35433",
    )):
        return NotImplemented
    target = cfg.get("m_entity")
    if instance.evaluate(target) != expr.Entity(world.body):
        return NotImplemented
    if cls == "STU_A2A35433":
        return expr.to_int(instance.evaluate(cfg.get("m_logicalButton"))) in instance.component.held
    player = owner(world, instance)
    if player is None:
        return NotImplemented
    mover = player.mover
    if cls == "STU_309B0570":
        return expr.Vec3(*mover.velocity) if cfg.get("m_5B94EDB8") is None else NotImplemented
    yaw, pitch = mover.yaw * TURN, mover.pitch * TURN
    sine, cosine = f32(math.sin(yaw)), f32(math.cos(yaw))
    if cls == "STU_021D01CD":
        return expr.Vec3(sine, 0., cosine)
    if cls == "STU_66F3B89E":
        return expr.Vec3(cosine, 0., -sine)
    horizontal = f32(math.cos(pitch))
    return expr.Vec3(f32(sine * horizontal), f32(-math.sin(pitch)), f32(cosine * horizontal))


def action(world, instance, node):
    if instance.graph.index != GRAPH or world.match is None or world.hero != HERO:
        return NotImplemented
    player = owner(world, instance)
    if player is None or world.match.combat.is_dead(player) or world.match.combat.is_stunned(player):
        return True
    rows = node.field("m_BC5E91CF")
    if not isinstance(rows, list) or len(rows) != 2:
        return NotImplemented
    for row, cls, field, index in zip(rows, ("STU_9AF0FA80", "STU_4635C5E2"),
                                     ("m_A9561CA0", "m_CFEC5F76"), (3, 1), strict=True):
        if row.get("$") != cls or row.get(field) != index or row.get("m_priority") is not None:
            return NotImplemented
    multiplier = instance.evaluate(rows[0].get("m_9B7A63EA"))
    delta = instance.evaluate(rows[1].get("m_9B7A63EA"))
    if (type(multiplier) not in (int, float) or not math.isfinite(multiplier)
            or not isinstance(delta, expr.Vec3) or not all(map(math.isfinite, delta))):
        return NotImplemented
    player.mover.wall_jump = instance.component.frame + 1, f32(multiplier), tuple(delta)
    return True


def consume_jump(mover, frame):
    request = mover.wall_jump
    if request is None or request[0] > frame:
        return
    mover.wall_jump = None
    if request[0] != frame or mover.external_impulse:
        return
    _, multiplier, delta = request
    x, y, z = mover.velocity
    jump_speed = mover.values.jump_speed
    if jump_speed > 1.1920928955078125e-5:
        y = f32(f32(f32(y / jump_speed) * multiplier) * jump_speed)
    mover.velocity = tuple(f32(axis + extra) for axis, extra in zip((x, y, z), delta, strict=True))
    if mover.velocity[1] > 0:
        mover.flags_word |= FELL
