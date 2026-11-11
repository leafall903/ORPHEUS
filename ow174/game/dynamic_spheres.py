"""Match-owned linear velocity for the verified Halt sphere recipient class."""

import math
from dataclasses import dataclass
from typing import TYPE_CHECKING

from ow174.game.props import PlacedDynamicSphere
from ow174.game.script.expr import f32

if TYPE_CHECKING:
    from ow174.game.orisa_halt import ExplosionRequest


@dataclass
class SphereBody:
    placement: PlacedDynamicSphere
    inverse_mass: float
    centre: tuple[float, float, float]
    velocity: tuple[float, float, float] = (0.0, 0.0, 0.0)


class DynamicSpheres:
    def __init__(self) -> None:
        self._bodies: dict[tuple[int, int], SphereBody] = {}

    def register(self, placement: PlacedDynamicSphere, *, inverse_mass: float) -> SphereBody:
        if not math.isfinite(inverse_mass) or inverse_mass < 0:
            raise ValueError("sphere inverse mass must be finite and nonnegative")
        if not math.isfinite(placement.radius) or placement.radius <= 0:
            raise ValueError("sphere radius must be finite and positive")
        key = placement.placement, placement.shape
        if key in self._bodies:
            raise ValueError(f"duplicate dynamic sphere {key}")
        body = SphereBody(placement, inverse_mass, placement.centre)
        self._bodies[key] = body
        return body

    def register_initial(self, placements: tuple[PlacedDynamicSphere, ...]) -> None:
        for placement in placements:
            inverse_mass = placement.initial_inverse_mass
            key = placement.placement, placement.shape
            if inverse_mass is not None and key not in self._bodies:
                self.register(placement, inverse_mass=inverse_mass)

    def dispatch(self, request: "ExplosionRequest") -> None:
        for body in self._bodies.values():
            shape = body.placement
            if shape.category != 0x0100:
                continue
            delta = tuple(f32(f32(axis) - f32(origin))
                          for axis, origin in zip(body.centre, request.position, strict=True))
            distance = math.hypot(*delta)
            if f32(distance) > f32(request.radius + shape.radius):
                continue
            if distance == 0:
                # With coincident float32 centres the native spherical path uses +X.
                delta, distance = (1.0, 0.0, 0.0), 1.0
            magnitude = f32(abs(request.strength) * body.inverse_mass * (shape.radius / 0.18) ** 2)
            if magnitude <= f32(0.05):
                continue
            scale = math.copysign(min(magnitude, 240.0), request.strength) / distance
            body.velocity = tuple(f32(value + component * scale)
                                  for value, component in zip(body.velocity, delta, strict=True))
