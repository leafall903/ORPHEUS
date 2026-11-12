"""Baptiste 104319 field landing and actor graph hosting.

Launch inputs are authored. Projectile collision and recipient selection
are reconstructed from the local geometry and live actors.
"""

import math
from dataclasses import dataclass

from ow174.game import mei_wall
from ow174.game.sombra_control import DeviceWorld
from ow174.game.collision import NOT_GROUND
from ow174.game.script import expr, graph, runtime

DEVICE_RADIUS = 0.4
FLOOR_FRACTION = 0.1  # 1763/node8


@dataclass
class Flight:
    owner: object
    source: runtime.Component
    body: int
    position: tuple
    velocity: tuple
    gravity: float
    remaining: float
    tick: int


@dataclass
class Device:
    owner: object
    body: int
    position: tuple
    component: runtime.Component
    health: float = 150.0  # 29A9 overrides 0033/C7.

    def centre(self):
        # Reconstructed 29A9 damage volume.
        x, y, z = self.position
        return x, y + 0.45, z


class FieldWorld(DeviceWorld):
    def __init__(self, fight, body, position):
        super().__init__(fight, body, frozenset((0x6E6,)), hero=0x02E0000000000200)
        self.origin = expr.Vec3(*position)
        self.retired = False

    def position(self, entity):
        if entity == self.body:
            return self.origin
        return super().position(entity)


    def destroy_entity(self, instance, node):
        if instance.graph.index == 0x1762 and node.pos == 1:
            self.retired = True
            return True
        return NotImplemented


class Fields:
    def __init__(self, fight):
        self.fight = fight
        self.flights = {}
        self.devices = {}

    def launch(self, owner, state, origin, direction):
        node = state.node
        impact = node.field("m_49CBA3BC")
        if (impact["m_entityDefinition"]["m_entityDef"], impact["m_410BF8BD"]["m_graph"]) != (
            "0x04000000000029A9", "0x0580000000001762",
        ):
            raise ValueError("Baptiste landing resources changed")
        speed = expr.to_float(state.evaluate(node.field("m_projectileSpeed"), watch=False))
        lifetime = expr.to_float(state.evaluate(node.field("m_projectileLifetime"), watch=False))
        motion, = node.field("m_projectileMotions")
        gravity = expr.to_float(state.evaluate(motion["m_599AB8D7"], watch=False))
        body = self.fight.next_bot
        if body >= 0xD0000000:
            raise ValueError("field entity allocator exhausted")
        self.fight.next_bot += 1
        velocity = tuple(speed * axis for axis in direction)
        self.flights[owner.body] = Flight(owner, state.instance.component, body, origin, velocity,
                                          gravity, lifetime, self.fight.tick)
        state.instance.write(expr.dynamic(node.field("m_505472EF")), expr.Entity(body))

    def land(self, flight, position, tick):
        source = flight.source.entity
        body = self.fight.next_bot
        if body >= 0xD0000000:
            raise ValueError("field entity allocator exhausted")
        self.fight.next_bot += 1
        world = FieldWorld(self.fight, body, position)
        component = runtime.Component(body, world=world)
        component.frame, component.now = tick, tick * component.frame_ms
        component.play_loaded_graph(graph.graph(0x1762), source=source, instigator=source)
        component.run_now()
        self.retire_device(source)
        self.devices[source] = Device(flight.owner, body, position, component)

    def retire_device(self, source):
        device = self.devices.pop(source, None)
        if device is not None:
            self.fight.sombra_controls.retire(device.body)
            component = device.component
            component.world.retired = True
            for instance in tuple(component.instances.values()):
                component.destroy(instance)

    def retire(self, source):
        self.flights.pop(source, None)
        self.retire_device(source)

    def enemies(self, shooter, *, team=None):
        if team is None:
            team = shooter.team
        for device in self.devices.values():
            if device.health <= 0 or device.component.world.retired or device.owner is shooter:
                continue
            if self.fight.match.game_map.free_for_all or device.owner.team != team:
                yield device

    def damage(self, device, amount):
        if amount > 0:
            device.health = expr.f32(max(0.0, device.health - amount))
            if not device.health:
                device.component.world.retired = True

    def trace(self, shooter, origin, direction, reach, *, team=None):
        from ow174.game.combat import ray_capsule

        best = None
        for device in self.enemies(shooter, team=team):
            centre = device.centre()
            distance = ray_capsule(origin, direction, centre, centre, DEVICE_RADIUS)
            if distance is not None and distance <= reach and (best is None or distance < best[0]):
                best = distance, device
        return best

    def health_floor(self, target, vitals):
        device = self.protecting_device(target)
        return FLOOR_FRACTION * sum(vitals.most.values()) if device is not None else 0.0

    def protecting_device(self, target):
        """Reconstructed 1762 team, range and LOS selection."""
        collision = self.fight._collision()
        free_for_all = self.fight.match.game_map.free_for_all
        for device in self.devices.values():
            if device.health <= 0 or device.component.world.retired or device.owner.gone:
                continue
            silence = device.component.entity_var(8912, create=False)
            if silence is not None and expr.truthy(silence.value()):
                continue
            if target.owner is not device.owner and (free_for_all or target.team != device.owner.team):
                continue
            instance = next(iter(device.component.instances.values()), None)
            if instance is None:
                continue
            radius = expr.to_float(instance.vars[1165].value())
            if math.dist(device.position, target.feet) > radius:
                continue
            # Raise the source above the landing surface for the static LOS ray.
            x, y, z = device.position
            origin = (x, y + 0.2, z)
            tx, ty, tz = target.feet
            offset = (tx - x, ty + target.eye - origin[1], tz - z)
            length = math.hypot(*offset)
            if collision is not None and length > 1e-6:
                direction = tuple(axis / length for axis in offset)
                if (collision.cast_ray(origin, direction, length) is not None
                        or any(mei_wall.ray(piece, origin, direction, length) is not None
                               for piece in self.fight.ice_wall.pieces.values())):
                    continue
            return device
        return None

    def advance(self, flight, tick, dt):
        flight.tick = tick
        flight.remaining -= dt
        x, y, z = flight.velocity
        displacement = (x * dt, (y - flight.gravity * dt / 2) * dt, z * dt)
        distance = math.hypot(*displacement)
        velocity = x, y - flight.gravity * dt, z
        if distance <= 1e-9:
            flight.velocity = velocity
            return False
        direction = tuple(axis / distance for axis in displacement)
        hit = self.fight._collision().cast_ray(flight.position, direction, distance)
        if hit is None:
            flight.position = tuple(a + b for a, b in zip(flight.position, displacement, strict=True))
            flight.velocity = velocity
            return False
        normal = hit.normal
        if normal[1] > 0.5 and y <= 0 and not hit.surface & NOT_GROUND:
            self.land(flight, hit.point, tick)
            return True
        projection = sum(a * b for a, b in zip(velocity, normal, strict=True))
        flight.velocity = tuple(a - 1.5 * projection * b for a, b in zip(velocity, normal, strict=True))
        flight.position = tuple(a + 0.002 * b for a, b in zip(hit.point, normal, strict=True))
        return False

    def update(self, tick, dt):
        for source, flight in tuple(self.flights.items()):
            script = flight.owner.body_script
            if flight.owner.gone or script is None or script.retired or script.component is not flight.source:
                del self.flights[source]
                continue
            for frame in range(flight.tick + 1, tick + 1):
                if flight.remaining <= 0 or self.advance(flight, frame, min(dt, flight.remaining)):
                    del self.flights[source]
                    break
        for source, device in tuple(self.devices.items()):
            component = device.component
            for frame in range(component.frame + 1, tick + 1):
                component.run_frame(frame)
                if component.world.retired:
                    break
            if component.world.retired or device.owner.gone or device.owner.body != source:
                self.retire_device(source)

        if self.devices:
            for target in self.fight._targets():
                vitals = self.fight.vitals_of(target)
                if vitals.dead:
                    continue
                # 1763/8 supplies the fraction. 1763/13 gates the /2 heal.
                floor = FLOOR_FRACTION * sum(vitals.most.values())
                if vitals.total < floor and (device := self.protecting_device(target)) is not None:
                    self.fight._heal(device.owner, target, floor - vitals.total, ceiling=floor)
