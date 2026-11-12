"""Wrecking Ball's authored 14E4 launch trace against static map geometry."""

import math

from ow174.game.script import expr
from ow174.game.script.runtime import State, state_class

GRAPH = 0x14E4
LAUNCH_NODE = 1
STATIC_FILTER = 0x069000000000012E
HARDPOINT = 0x0DC0000000000282
# Body 200B's model 67DD, hardpoint 0282 in its upright reference pose.
# X/Z are effectively zero for this pose. Animated bone offsets are not sampled.
LAUNCH_HEIGHT = 0.7908548712730408
MS = expr.f32(0.001)
OWNER = {"$": "STU_B5A0CAF0"}


@state_class("STU_4BBC5D09")
class GrappleTrace(State):
    """Resolve the static ray and advance its endpoint at the authored speed."""

    def __init__(self, *args) -> None:
        super().__init__(*args)
        self.flight = None

    def _write(self, port: str, value) -> None:
        self.instance.write(expr.lvalue(self.node.field(port)), value)

    def on_enter(self) -> None:
        instance, node = self.instance, self.node
        component = instance.component
        if component.strict:
            component.unsupported(instance, "grapple trace service", node.cls, node)
        if (instance.graph.index, node.rid) != (GRAPH, LAUNCH_NODE):
            return
        self._write("m_9E1A7C58", False)  # Did the trace hit?
        filter_config = node.field("m_66B658A5")
        profile = filter_config.get("m_C1903771") if isinstance(filter_config, dict) else None
        hardpoint = node.field("m_F745334E")
        if not (
            isinstance(profile, dict) and isinstance(hardpoint, dict)
            and filter_config.get("$") == "STUConfigVarFilter"
            and profile.get("$") == "STU_20DB8AB5"
            and expr.guid_of(profile.get("m_C1903771")) == STATIC_FILTER
            and profile.get("m_resourceKey") is None
            and hardpoint.get("$") == "STUConfigVarHardPoint"
            and expr.guid_of(hardpoint.get("m_hardPoint")) == HARDPOINT
        ):
            self.request_finish()
            return

        self._write("m_1FFD7AC0", expr.Entity(0))  # Static anchors use world coordinates.
        self._write("m_9169D109", False)  # The secondary anchor candidate needs a separate query.
        cast = getattr(component.world, "static_ray", None)
        if cast is None:
            self.request_finish()
            return
        feet = component.world.position(component.entity)
        direction = self.evaluate({"$": "STU_2200A66D", "m_entity": OWNER}, watch=False)
        length = expr.to_float(self.evaluate(node.field("m_2AFC083F"), watch=False))
        speed = expr.to_float(self.evaluate(node.field("m_speed"), watch=False))
        if (not isinstance(feet, expr.Vec3) or not isinstance(direction, expr.Vec3)
                or not math.isfinite(length) or length <= 0.0
                or not math.isfinite(speed) or speed <= 0.0):
            self.request_finish()
            return

        origin = expr.Vec3(feet.x, expr.f32(feet.y + LAUNCH_HEIGHT), feet.z)
        result = cast(origin, direction, length)
        if result is None:
            self.request_finish()
            return
        point = result.point if result.hit else None
        normal = result.normal if point is not None else None
        distance = length if normal is None else min(length, math.dist(origin, point))
        self.flight = (origin, direction, speed, distance, point, normal)
        self._write("m_5ED803CF", origin)
        self._write("m_F2F8606D", origin)
        self._write("m_64801077", direction)
        self.ticks()

    def tick(self) -> None:
        if self.flight is None:
            return
        origin, direction, speed, distance, point, normal = self.flight
        # C94752-C9476F: elapsed milliseconds from state start, converted and scaled as float32.
        elapsed = max(0, self.now - self.start)
        travel = expr.f32(expr.f32(expr.f32(elapsed) * MS) * speed)
        reached = travel >= distance
        if reached and point is not None and normal is not None:
            self._write("m_5ED803CF", point)
            self._write("m_F2F8606D", point)
            self._write("m_D7A2A03B", normal)
            self._write("m_13BE2D3D", normal)
            self._write("m_9E1A7C58", True)
        else:
            offset = min(travel, distance)
            endpoint = expr.Vec3(*(
                expr.f32(axis + expr.f32(vector * offset))
                for axis, vector in zip(origin, direction, strict=True)
            ))
            self._write("m_F2F8606D", endpoint)
        if reached:
            self.request_finish()

    def on_end(self, finished: bool) -> None:
        self.flight = None

    def payload(self) -> dict:
        return {"start": self.start} if self.flight is not None else {}
