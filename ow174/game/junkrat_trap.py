"""Static Steel Trap host for 104319 graphs 09BA, 09BB and 09BC.

The authoritative host reconstructs flight, target selection and damage
collision. The receiver graph owns the movement modifiers and supplies the
pull request. Client replication remains separate.
"""

import math
from dataclasses import dataclass

from ow174.game.script import expr, graph, message_receive, runtime

LAUNCH, DEVICE, RECEIVER = 0x09BA, 0x09BB, 0x09BC
PROJECTILE, DEFINITION = 0x04000000000011A9, 0x040000000000103A
APPLY, RELEASE = 0x024000000000000F, 0x0240000000000FC1
LOADOUT = 0x0B900000000000CC
FRAME_MS = 16
ARM_MS, ROOT_MS = 500, 3000  # 09BB nodes 3 and 75, ordinary target
SHORT_ROOT_MS, SHORT_ROOT_FLAG = 10, 9161  # 09BB nodes 74 and 76
RADIUS, DAMAGE = 1.0, 80  # 09BB nodes 2 and 4
HEALTH = 100.0  # 103A health schema override on 0033
HIT_RADIUS = 0.25  # static approximation for model receiver 00A5.098


def launches(state) -> bool:
    return state.instance.graph.index == LAUNCH and state.node.pos == 7


@dataclass
class Flight:
    owner: object
    body: int
    team: int
    position: tuple
    velocity: tuple
    gravity: float
    remaining: float
    tick: int


@dataclass
class Device:
    entity: int
    owner: object
    body: int
    team: int
    position: tuple
    arms_ms: int
    health: float = HEALTH
    victim: int | None = None

    def hit_centre(self):
        x, y, z = self.position
        return x, y + HIT_RADIUS, z


@dataclass
class Root:
    source: int
    position: tuple
    component: runtime.Component
    instance: int
    ends_ms: int


class Traps:
    def __init__(self, fight):
        self.fight = fight
        self.flights: list[Flight] = []
        self.devices: dict[int, Device] = {}
        self.roots: dict[tuple[int, int], Root] = {}

    def launch(self, shooter, state, position, direction, speed, lifetime):
        refs = (
            ("m_projectileEntity.m_entityDef", PROJECTILE),
            ("m_49CBA3BC.m_entityDefinition", DEFINITION),
            ("m_49CBA3BC.m_410BF8BD", graph.GRAPH_TYPE << 48 | DEVICE),
        )
        if any(state.evaluate(state.node.field(field), watch=False) != expr.Asset(guid)
               for field, guid in refs):
            return
        if graph.graph(RECEIVER) is None:
            return
        motion = next(row for row in state.node.field("m_projectileMotions") if row["$"] == "STU_E1A5B579")
        gravity = expr.to_float(state.evaluate(motion["m_599AB8D7"], watch=False))
        velocity = tuple(speed * axis for axis in direction)
        self.flights.append(Flight(shooter, shooter.body, shooter.team, position,
                                   velocity, gravity, lifetime, self.fight.tick))

    def retire(self, body):
        self.flights[:] = [flight for flight in self.flights if flight.body != body]
        self.devices.pop(body, None)
        for key, root in tuple(self.roots.items()):
            if body in (root.source, key[0]):
                self.release(key)

    def deploy(self, flight, tick):
        entity = self.fight.next_bot
        if not 0xC0001000 <= entity < 0xD0000000:
            raise ValueError("Steel Trap entity allocator exhausted")
        self.fight.next_bot += 1
        previous = self.devices.get(flight.body)
        if previous is not None and previous.victim is not None:
            self.release((previous.victim, previous.entity))
        self.devices[flight.body] = Device(entity, flight.owner, flight.body, flight.team,
                                           flight.position, tick * FRAME_MS + ARM_MS)

    def trigger(self, device, target, tick):
        absorbed = self.fight._damage(device.owner, target, DAMAGE, False, LOADOUT, tick, world_tick=tick)
        script = getattr(target.owner, "body_script", None)
        if absorbed or script is None or self.fight.vitals_of(target).dead:
            self.devices.pop(device.body, None)
            return
        device.victim = target.entity
        component = script.component
        short = component.entity_var(SHORT_ROOT_FLAG, create=False)
        duration = SHORT_ROOT_MS if short is not None and expr.truthy(short.value()) else ROOT_MS
        instance = component.create(graph.graph(RECEIVER))
        self.roots[target.entity, device.entity] = Root(device.body, device.position, component,
                                                        instance.id, tick * FRAME_MS + duration)
        message_receive.receive(instance, message_receive.Packet(APPLY, device.entity, device.body, ()))
        component.run_now()

    def pull_destination(self, victim, device):
        root = self.roots.get((victim, device))
        return expr.Vec3(*root.position) if root is not None else None

    def release(self, key):
        root = self.roots.pop(key, None)
        if root is None:
            return
        device = self.devices.get(root.source)
        if device is not None and device.entity == key[1]:
            del self.devices[root.source]
        instance = root.component.instances.get(root.instance)
        if instance is not None:
            message_receive.receive(instance, message_receive.Packet(RELEASE, key[1], root.source, ()))
            root.component.run_now()

    def enemy_devices(self, shooter):
        free_for_all = self.fight.match.game_map.free_for_all
        return (d for d in self.devices.values()
                if d.owner is not shooter and (free_for_all or d.team != shooter.team))

    def contact(self, shooter, origin, direction, reach):
        from ow174.game.combat import ray_capsule

        best = None
        for device in self.enemy_devices(shooter):
            centre = device.hit_centre()
            hit = ray_capsule(origin, direction, centre, centre, HIT_RADIUS)
            if hit is not None and hit <= reach and (best is None or (hit, device.entity) < best[:2]):
                best = hit, device.entity, device
        return (best[0], best[2]) if best is not None else None

    def melee(self, shooter, origin, centre, radius, amount):
        from ow174.game.combat import capsule_visible

        for device in tuple(self.enemy_devices(shooter)):
            point = device.hit_centre()
            if (math.dist(centre, point) <= radius + HIT_RADIUS
                    and capsule_visible(self.fight._collision(), origin, point, point, HIT_RADIUS)):
                self.damage(device, amount)

    def damage(self, device, amount):
        if amount <= 0 or self.devices.get(device.body) is not device:
            return
        device.health = max(0.0, device.health - amount)
        if device.health == 0.0:
            del self.devices[device.body]
            if device.victim is not None:
                self.release((device.victim, device.entity))

    def update(self, tick):
        from ow174.game.combat import capsule_visible, segment_distance
        from ow174.game.projectiles import deployable_contact

        for flight in tuple(self.flights):
            hit = deployable_contact(flight, tick, self.fight._collision())
            if hit is not None:
                self.deploy(flight, hit)
            if hit is not None or flight.remaining <= 0:
                self.flights.remove(flight)
        now = tick * FRAME_MS
        for key, root in tuple(self.roots.items()):
            vitals = self.fight.vitals.get(key[0])
            if now >= root.ends_ms or vitals is None or vitals.dead:
                self.release(key)
        if not self.devices:
            return
        free_for_all = self.fight.match.game_map.free_for_all
        collision = self.fight._collision()
        for device in tuple(self.devices.values()):
            if device.victim is not None or now < device.arms_ms:
                continue
            candidates = []
            for target in self.fight._targets():
                if target.owner is device.owner or (not free_for_all and target.team == device.team):
                    continue
                if self.fight.vitals_of(target).dead or self.fight.damage_immune(target):
                    continue
                a, b = target.segment()
                distance = segment_distance(device.position, a, b) - target.radius
                if distance <= RADIUS and capsule_visible(collision, device.position, a, b, target.radius):
                    candidates.append((distance, target.entity, target))
            if candidates:
                self.trigger(device, min(candidates)[2], tick)
