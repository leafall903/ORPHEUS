
"""104319 B5C87F3D update and auxiliary scope with an explicit world host."""

import math
from dataclasses import dataclass

from ow174.game.script import expr, play, scopes

CLASS = "STU_B5C87F3D"
MIN_VECTOR_SQUARED = expr.f32(1.1754943508222875e-36)
ZERO = expr.Vec3(0, 0, 0)


@dataclass(frozen=True)
class Sample:
    """World point after eligibility and related component45 resolution."""

    position: expr.Vec3
    add_component45: object = NotImplemented


@dataclass(frozen=True)
class Context:
    sample: object  # None declares no scheduler owner.
    registry: object = NotImplemented

    def __post_init__(self):
        if self.sample is not None and not callable(self.sample):
            raise ValueError("expected a spatial sample service or known scheduler absence")
        if (self.registry is not NotImplemented and self.registry is not None
                and type(self.registry) is not scopes.Registry):
            raise ValueError("expected a local spatial registry or known absence")


def context_for(state):
    component = state.instance.component
    context = component.world.component45_spatial_context(component.entity)
    if type(context) is not Context:
        component.unsupported(state.instance, "component45 spatial context", CLASS, state.node)
    return context


def _check(state, context):
    instance = state.instance
    component = instance.component
    component.ensure_valid()
    if (not state.active or instance.stopped or instance.teardown
            or component.instances.get(instance.id) is not instance
            or instance.states.get(state.index) is not state
            or component.world.component45_spatial_context(component.entity) is not context):
        component.unsupported(instance, "component45 spatial owner retired", CLASS, state.node)


def _call(state, context, callback, feature, *args):
    if not callable(callback):
        state.instance.component.unsupported(state.instance, feature, CLASS, state.node)
    result = callback(*args)
    _check(state, context)
    return result


def register(state):
    context = context_for(state)
    registry = context.registry
    if registry is NotImplemented:
        state.instance.component.unsupported(
            state.instance, "component45 spatial registry", CLASS, state.node,
        )
    if registry is not None:
        registry.register(state)
        state.spatial_registry = registry


def unregister(state):
    registry, state.spatial_registry = state.spatial_registry, None
    if registry is not None:
        registry.unregister(state)


def _value(state, context, field, fallback):
    result = play.evaluate_scalar(state.instance, state.node.field(field), initial=fallback)
    _check(state, context)
    return result


def _vector(value):
    while type(value) is tuple:
        value = value[0] if value else None
    return value if type(value) is expr.Vec3 else ZERO


def _dot(a, b):
    x, y, z = (expr.f32(left * right) for left, right in zip(a, b, strict=True))
    return expr.f32(expr.f32(x + y) + z)


def _cosine_half_angle(angle):
    # 9045F0 uses this float32 polynomial after converting to half-turns.
    half_turns = expr.f32(expr.f32(expr.f32(angle * expr.f32(math.pi / 180)) * 0.5)
                          * expr.f32(1 / math.pi))
    turns = math.floor(half_turns)
    fraction = expr.f32(half_turns - turns)
    square = expr.f32(fraction * fraction)
    coefficient = expr.f32(6 - expr.f32(4 * fraction))
    cosine = expr.f32(1 - expr.f32(square * coefficient))
    return -cosine if turns & 1 else cosine


def _in_cone(direction, offset, distance_squared, angle):
    direction_squared = _dot(direction, direction)
    if direction_squared < MIN_VECTOR_SQUARED or distance_squared < MIN_VECTOR_SQUARED:
        return False
    # Native uses an SSE reciprocal square root estimate and one refinement.
    # The server normalizes with sqrt, so exact threshold parity is open.
    direction = tuple(expr.f32(value / math.sqrt(direction_squared)) for value in direction)
    offset = tuple(expr.f32(value / math.sqrt(distance_squared)) for value in offset)
    return _dot(direction, offset) > _cosine_half_angle(angle)


def update(state, context, elapsed):
    sample = _call(state, context, context.sample, "component45 spatial lookup", state)
    if sample is None:
        return
    if type(sample) is not Sample or type(sample.position) is not expr.Vec3:
        state.instance.component.unsupported(state.instance, "component45 spatial sample", CLASS, state.node)
    center = _vector(_value(state, context, "m_position", ZERO))
    radius = expr.to_float(_value(state, context, "m_radius", 1.0), 1.0)
    offset = tuple(expr.f32(point - origin)
                   for point, origin in zip(sample.position, center, strict=True))
    distance_squared = _dot(offset, offset)
    if not distance_squared < expr.f32(radius * radius):
        return
    angle = expr.to_float(_value(state, context, "m_A5A7B92D", 0.0))
    if angle > 0:
        direction = _vector(_value(state, context, "m_direction", ZERO))
        if not math.isfinite(angle) or not _in_cone(direction, offset, distance_squared, angle):
            return
    state.result = expr.to_float(_value(state, context, "m_4F5799DC", 0.0))
    rate = expr.to_float(_value(state, context, "m_C33731A1", 0.0))
    if rate > 0:
        seconds = expr.f32(expr.f32(elapsed) * expr.f32(0.001))
        amount = expr.f32(rate * seconds)
        _call(state, context, sample.add_component45, "component45 spatial accumulator", amount)
