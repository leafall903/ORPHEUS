"""Role16 flight-context arithmetic from build104319 C4A4B0.

Mover supplies a legacy statescript scope bridge and unparented Match pose
policy. These functions calculate context and post velocity; native scheduler,
pre-pose quaternion/view transport and parented movement remain separate.
"""

from dataclasses import dataclass

from ow174.game import contacts

f32 = contacts.f32


def _unit(vector):
    """The (3-r*r*length)*(.5*r) SSE refinement used by this path."""
    length = contacts.dot(vector, vector)
    r = contacts.rsqrt_estimate(length)
    factor = f32(f32(3.0 - f32(f32(r*r)*length))*f32(.5*r))
    return contacts.scale(vector, factor)


@dataclass(frozen=True)
class Context:
    normal: tuple
    normal_velocity: tuple
    remainder: tuple
    forward: tuple
    side: tuple
    right_throttle: float
    forward_throttle: float
    normal_speed: float


def context(look, right_axis, velocity, right, forward, parameter, jump=False, crouch=False):
    """C4A4B0, including zero-forward jump/crouch and sideways scaling.

    right_axis is incoming context+50, NOT an up vector. The production rsqrt
    estimate is software approximated; original-instruction comparisons use a
    float tolerance rather than claiming byte equality. Degenerate input axes
    retain IEEE NaNs, as this native path does; callers own valid-pose policy.
    """
    original = tuple(look)
    look = list(look)
    if jump != crouch:
        if forward == 0:
            look = [0.0, 1.0 if jump else -1.0, 0.0]
            forward = 1.0
        elif (forward > 0) == jump:
            look[1] = f32(look[1]+parameter)
        else:
            look[1] = f32(look[1]-parameter)
    length = contacts.dot(look, look)
    look = _unit(look) if length >= 1.1754943508222875e-36 else original
    negative_side = contacts.neg(right_axis)
    normal = _unit(contacts.cross(look, negative_side))
    normal_speed = contacts.dot(velocity, normal)
    normal_velocity = contacts.scale(normal, normal_speed)
    remainder = contacts.sub(velocity, normal_velocity)
    # 16EBF00 normalizes its two inputs and constructs the remaining axes.
    side = _unit(negative_side)
    unit_normal = _unit(normal)
    flight_forward = _unit(contacts.cross(side, unit_normal))
    return Context(normal, normal_velocity, remainder, flight_forward,
                   contacts.neg(side), f32(right*parameter), forward, normal_speed)


def _length(vector):
    squared = contacts.dot(vector, vector)
    if squared <= 1.1754943508222875e-36:
        return 0.0
    r = contacts.rsqrt_estimate(squared)
    factor = f32(f32(3.0-f32(squared*f32(r*r)))*f32(.5*r))
    return f32(squared*factor)


def friction(data, base_forward, velocity, ctx, dt, gravity=10.0):
    """C316A0/C33090's PC air branch, with explicit world gravity magnitude.

    Friction uses the context remainder while quadratic drag uses its normal
    projection. This is not ordinary horizontal friction plus vertical drag.
    """
    remainder = velocity if data.gravity_scale == 0.0 else ctx.remainder
    speed = _length(remainder)
    excess = max(0.0, f32(speed-data.decel_air_base_speed))
    rate = f32(f32(f32(excess*data.decel_air_per_speed)*base_forward)
               + f32(data.decel_air_base*data.run_forward))
    slow = min(speed, f32(dt*rate))
    drag = contacts.neg(remainder)
    if contacts.dot(drag, drag) >= 1.1754943508222875e-36:
        drag = _unit(drag)
    velocity = contacts.add(velocity, contacts.scale(drag, slow))
    normal_speed = ctx.normal_speed
    terminal = data.terminal_velocity_up if normal_speed > 0.0 else data.terminal_velocity
    if terminal > 1.1920928955078125e-5:
        magnitude = f32(f32(f32(gravity*data.gravity_scale)/f32(terminal*terminal))*normal_speed)
        change = contacts.scale(contacts.scale(ctx.normal_velocity, -abs(magnitude)), dt)
        size = _length(change)
        total = _length(velocity)
        if size > total:
            change = contacts.scale(change, f32(total/size))
        velocity = contacts.add(velocity, change)
    return velocity


def wish(data, ctx, crouched=False):
    """C34340/BBEF40's 3D wish and directional top speed for the retained stance."""
    forward = data.crouch_forward if crouched else data.run_forward
    backward = data.crouch_backward if crouched else data.run_backward
    strafe = data.crouch_strafe if crouched else data.run_strafe
    ahead = forward if ctx.forward_throttle > 0.0 else backward
    vector = contacts.add(contacts.scale(ctx.side, f32(ctx.right_throttle*strafe)),
                          contacts.scale(ctx.forward, f32(ctx.forward_throttle*ahead)))
    size = _length(vector)
    if size <= 0.0:
        return (0.0, 0.0, 0.0), 0.0
    direction = _unit(vector)
    cosine = contacts.dot(ctx.forward, direction)
    top = forward if cosine > 0.0 else backward
    cap = f32(strafe+f32(abs(cosine)*f32(top-strafe)))
    return direction, min(size, cap)


def accelerate(data, velocity, ctx, dt, crouched=False):
    """C42B70's 3D acceleration after air friction; no gravity addition."""
    direction, desired = wish(data, ctx, crouched)
    squared = contacts.dot(velocity, velocity)
    speed = _length(velocity) if squared >= 1.1754943508222875e-36 else -1.0
    target = max(speed, desired) if desired > 0.0 else desired
    delta = contacts.sub(contacts.scale(direction, target), velocity)
    size = _length(delta)
    if size <= 0.0 or target <= 0.0:
        return velocity
    step = f32(f32(data.accel_air*data.run_forward)*dt)
    fraction = max(0.0, min(1.0, f32(step/size)))
    candidate = contacts.add(velocity, contacts.scale(delta, fraction))
    if speed > 1.1920928955078125e-7 and contacts.dot(direction, velocity) > 0.0:
        length = f32(speed+min(step, f32(target-speed)))
        candidate = contacts.scale(_unit(candidate), length)
    return contacts.add(velocity, contacts.sub(candidate, velocity))


def velocity(data, base_forward, current, ctx, dt, gravity=10.0, *, crouched=False):
    """Finite unparented role16 post-phase arithmetic; engine adapters are external."""
    return accelerate(data, friction(data, base_forward, current, ctx, dt, gravity), ctx, dt, crouched)
