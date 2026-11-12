"""Reconstructed combat host for the 104319 Torbjorn turret."""

import math
from dataclasses import dataclass

from ow174.game.collision import NOT_GROUND
from ow174.game.script import expr, runtime

LAUNCH = (0x16C5, 3)
CARRIER = 0x0400000000002931
DEVICE = 0x04000000000028B7
DEVICE_RADIUS = 0.25  # Reconstructed volume, using the 1688/165 radius.
ACQUISITION_RANGE = 40.0  # 1688/96 track ray.
BUILD_TIME = 1.5  # Reconstructed placement phase using 1688/74.
PUBLISH = 0x0240000000000040  # 1688/154 -> 16C5/10.
PUBLISHED_BODY = 679
DISMANTLE = 0x024000000000003F  # 16C5/44 -> 1688/140.
DISMANTLE_DAMAGE = 10000  # 1688/146.
HAMMER = 0x168C
REPAIR = 0x024000000000008F  # 168C/7 -> 1688/16.
REPAIR_FULL = 0x0240000000000091  # 168C/9 -> 1688/24.
REPAIR_AMOUNT = 50  # 1688/19.
CAPACITY = 250.0  # 28B7's 0033 schema override C7.
SHOT_RATE = 4  # 1689/3.
SHOT_DAMAGE = 14  # 1689/24's default initialization of #476.
SHOT_LOADOUT = 0x0B90000000000225


@dataclass
class Device:
    owner: object
    source: int
    body: int
    position: tuple
    instance: runtime.Instance
    output: tuple
    ready: float
    next_shot: float
    health: float = CAPACITY

    def centre(self):
        x, y, z = self.position
        return x, y + 0.4, z


class Turrets:
    def __init__(self, fight):
        self.fight = fight
        self.devices = {}

    def land(self, rocket, hit):
        if hit.normal[1] <= 0.5 or hit.surface & NOT_GROUND:
            return
        instance = rocket.turret_instance
        owner, source = rocket.shooter, rocket.source_entity
        if owner.gone or owner.body != source or instance.stopped or instance.component.fault is not None:
            return
        entry, = runtime.message_entries(instance, PUBLISH)
        body = self.fight.next_bot
        if body >= 0xD0000000:
            raise ValueError("turret entity allocator exhausted")
        self.fight.next_bot += 1
        output = expr.dynamic(instance.graph.node(LAUNCH[1]).field("m_505472EF"))
        self.retire(source)
        ready = self.fight.now + BUILD_TIME
        self.devices[source] = Device(owner, source, body, hit.point, instance, output, ready, ready)
        instance.write(output, expr.Entity(body))
        # Node 10 only binds the ID. Publish at landing so a queued message
        # cannot restore a retired ID after a subsequent removal.
        instance.deliver(runtime.Event(instance.component.now, 0, runtime.MESSAGE, node=entry,
                                       message=(PUBLISH, {0x5B: expr.Entity(body)}, body)))

    def retire(self, source):
        device = self.devices.pop(source, None)
        if device is None:
            return
        self.fight.sombra_controls.retire(device.body)
        if device.instance.stopped or device.instance.component.fault is not None:
            return
        slot = device.instance.find_var(*device.output, create=False)
        if slot is not None and slot.value() == expr.Entity(device.body):
            device.instance.write(device.output, expr.Entity(0))
        slot = device.instance.component.entity_var(PUBLISHED_BODY, create=False)
        if slot is not None and slot.value() == expr.Entity(device.body):
            device.instance.write((expr.ENTITY, PUBLISHED_BODY), expr.Entity(0))

    def owned_device(self, source, entity):
        device = self.devices.get(source)
        return device if device is not None and device.body == entity else None

    def entity_var(self, source, entity, var):
        device = self.owned_device(source, entity)
        if device is not None:
            if var == 253:
                return device.health
            if var == 1884:
                # The reconstructed construction window hosts the authored gate.
                return self.fight.now < device.ready
            if var == 639:
                return 2 if self.fight.now >= device.ready else 0
        return NotImplemented

    def repair_targets(self, state):
        from ow174.game.combat import capsule_visible

        component = state.instance.component
        device = self.devices.get(component.entity)
        if device is None or device.owner.gone or device.owner.body != device.source:
            return ()
        offset = state.evaluate(state.node.field("m_05BE62DD"), watch=False)
        radius = expr.to_float(state.evaluate(state.node.field("m_radius"), watch=False))
        if not isinstance(offset, expr.Vec3) or radius <= 0:
            return ()
        origin = tuple(position + delta for position, delta in zip(device.owner.mover.position, offset))
        centre = device.centre()
        if (math.dist(origin, centre) > radius
                or not capsule_visible(self.fight._collision(), origin, centre, centre, DEVICE_RADIUS)):
            return ()
        candidate = expr.Entity(device.body)
        with component.parameter_scope({8: candidate}):
            matches = state.evaluate(state.node.field("m_filter.m_condition"), watch=False)
        return (candidate,) if expr.truthy(matches) else ()

    def receive(self, target, message, sender):
        if message not in (DISMANTLE, REPAIR, REPAIR_FULL):
            return False
        device = self.owned_device(sender, target)
        if device is None:
            return False
        if not device.owner.gone and device.owner.body == sender and self.fight.now >= device.ready:
            if message == DISMANTLE:
                self.damage(device, DISMANTLE_DAMAGE)
            elif message == REPAIR:
                device.health = expr.f32(min(CAPACITY, device.health + REPAIR_AMOUNT))
        return True

    def enemies(self, shooter, *, team=None):
        team = shooter.team if team is None else team
        for device in self.devices.values():
            if device.owner is not shooter and (self.fight.match.game_map.free_for_all or device.owner.team != team):
                yield device

    def damage(self, device, amount):
        if self.devices.get(device.source) is device and amount > 0:
            device.health = expr.f32(max(0.0, device.health - amount))
            if not device.health:
                self.retire(device.source)

    def trace(self, shooter, origin, direction, reach, *, team=None):
        from ow174.game.combat import ray_capsule

        best = None
        for device in self.enemies(shooter, team=team):
            centre = device.centre()
            distance = ray_capsule(origin, direction, centre, centre, DEVICE_RADIUS)
            if distance is not None and distance <= reach and (best is None or distance < best[0]):
                best = distance, device
        return best

    def update(self, tick):
        from ow174.game.combat import TICK, HitDef, capsule_visible

        fight = self.fight
        for source, device in tuple(self.devices.items()):
            instance = device.instance
            slot = instance.find_var(*device.output, create=False)
            if (device.owner.gone or device.owner.body != source or instance.stopped
                    or instance.component.fault is not None
                    or slot is None or slot.value() != expr.Entity(device.body)):
                self.retire(source)
                continue
            if device.body in fight.sombra_controls.locks or fight.now < device.next_shot:
                continue
            origin = device.centre()
            candidates = []
            for target in fight._enemies(device.owner):
                if fight._protected(target.owner):
                    continue
                a, b = target.segment()
                point = (a[0], (a[1] + b[1]) / 2, a[2])
                distance = math.dist(origin, point)
                if 0 < distance <= ACQUISITION_RANGE and capsule_visible(fight._collision(), origin, a, b,
                                                                          target.radius):
                    candidates.append((distance, target.entity, point))
            if candidates:
                distance, _, point = min(candidates)
                direction = tuple((end - start) / distance for start, end in zip(origin, point, strict=True))
                scheduled = device.next_shot if fight.now - device.next_shot < TICK else fight.now
                device.next_shot = scheduled + 1 / SHOT_RATE
                fight._hitscan(device.owner, HitDef(SHOT_DAMAGE, SHOT_LOADOUT), origin, direction,
                               ACQUISITION_RANGE, tick, tick, True, source_entity=device.body)
