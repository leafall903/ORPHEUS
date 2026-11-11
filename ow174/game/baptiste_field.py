"""Baptiste 104319 field landing and actor graph hosting.

Launch inputs are authored. The server reconstructs projectile collision
using the same ground and bounce policy as Orisa's thrown barrier.
"""

import math
from dataclasses import dataclass

from ow174.game.orisa_barrier import Flight, advance_thrown_device
from ow174.game.script import expr, graph, runtime

HERO = 0x02E0000000000221
DEVICE_RADIUS = 0.4


@dataclass
class Device:
    owner: object
    body: int
    position: tuple
    component: runtime.Component
    floor_fraction: float
    health: float = 150.0  # 104319 29A9 overrides 0033/C7.

    def centre(self):
        # Reconstructed 29A9 damage volume.
        x, y, z = self.position
        return x, y + 0.45, z


class FieldWorld(runtime.World):
    def __init__(self, mode, body, position):
        super().__init__(HERO, mode)
        self.body = body
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
        self.fight.next_bot += 1
        velocity = tuple(speed * axis for axis in direction)
        self.flights[owner.body] = Flight(owner, state.instance.component, body, origin, velocity,
                                          0, gravity, lifetime, self.fight.tick)
        state.instance.write(expr.dynamic(node.field("m_505472EF")), expr.Entity(body))

    def land(self, flight, position, tick):
        source = flight.source.entity
        body = self.fight.next_bot
        self.fight.next_bot += 1
        world = FieldWorld(self.fight.match.game_map.mode_guid, body, position)
        component = runtime.Component(body, world=world)
        component.frame, component.now = tick, tick * component.frame_ms
        component.play_loaded_graph(graph.graph(0x1762), source=source, instigator=source)
        component.run_now()
        self.retire_device(source)
        fraction, = graph.graph(0x1763).node(8).field("m_value.m_expression.m_D99EF254")
        self.devices[source] = Device(flight.owner, body, position, component, fraction)

    def retire_device(self, source):
        device = self.devices.pop(source, None)
        if device is not None:
            component = device.component
            component.world.retired = True
            for instance in tuple(component.instances.values()):
                component.destroy(instance)

    def retire(self, source):
        self.flights.pop(source, None)
        self.retire_device(source)

    def enemies(self, shooter):
        for device in self.devices.values():
            if device.health <= 0 or device.component.world.retired or device.owner is shooter:
                continue
            if self.fight.match.game_map.free_for_all or device.owner.team != shooter.team:
                yield device

    def targets(self, shooter):
        from ow174.game.combat import Target

        for device in self.enemies(shooter):
            x, y, z = device.centre()
            yield Target(device.body, device.owner.team, (x, y - DEVICE_RADIUS, z),
                         2 * DEVICE_RADIUS, DEVICE_RADIUS, DEVICE_RADIUS, device)

    def damage(self, device, amount):
        if amount > 0:
            device.health = expr.f32(max(0.0, device.health - amount))
            if not device.health:
                device.component.world.retired = True

    def health_floor(self, target, vitals):
        device = self.protecting_device(target)
        return device.floor_fraction * sum(vitals.most.values()) if device is not None else 0.0

    def protecting_device(self, target):
        """Reconstructed 1762 team, range and LOS selection."""
        collision = self.fight._collision()
        free_for_all = self.fight.match.game_map.free_for_all
        for device in self.devices.values():
            if device.health <= 0 or device.component.world.retired or device.owner.gone:
                continue
            if target.owner is not device.owner and (free_for_all or target.team != device.owner.team):
                continue
            instance = next(iter(device.component.instances.values()), None)
            if instance is None:
                continue
            # 1762's lifetime and target tracker both gate the 1763 protection.
            protection, tracking = instance.states.get(7), instance.states.get(4)
            if any(state is None or not state.active or state.ending for state in (protection, tracking)):
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
                if collision.cast_ray(origin, direction, length) is not None:
                    continue
            if not self.fight.walls.blocked(origin, (tx, ty + target.eye, tz)):
                return device
        return None

    def update(self, tick, dt):
        for source, flight in tuple(self.flights.items()):
            script = flight.owner.body_script
            if flight.owner.gone or script is None or script.retired or script.component is not flight.source:
                del self.flights[source]
                continue
            for frame in range(flight.tick + 1, tick + 1):
                if flight.remaining <= 0 or advance_thrown_device(
                    self.fight, flight, frame, min(dt, flight.remaining), self.land,
                ):
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
                device = self.protecting_device(target)
                if device is not None:
                    floor = device.floor_fraction * sum(vitals.most.values())
                    if vitals.total < floor:
                        self.fight._heal(
                            device.owner, target.owner, vitals, floor - vitals.total, ceiling=floor,
                        )
