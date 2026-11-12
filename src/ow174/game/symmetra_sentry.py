"""Server combat for the 104319 Symmetra sentry deployment."""

import math
from dataclasses import dataclass

from ow174.game.mover import ADD, SPEED
from ow174.game.script import expr, graph, runtime

LAUNCH = (0x157E, 30)
DEFINITION = "0x0400000000002658"
DEVICE_RADIUS = 0.2  # 157F/64, used as the reconstructed damage volume.
MAX_DEVICES = 3
FLIGHT_LIMIT = 5.0  # Safety bound for the managed zero-lifetime projectile.
ATTACH_DELAY = 1.0  # 157F/20 wait and 157F/7 chase.
ATTACH_GRAPH = 0x157F
ATTACH_MESSAGE = 0x02400000000005D4
DEPLOYED = 478
READY_PROGRESS = 639
RANGE = 10.0  # 1590/2 and /13.
DAMAGE_RATE = 40.0  # 1590/22, default mode branch.
DAMAGE_INTERVAL = 0.2  # 1590/3 period code 3.
LOADOUT = 0x0B90000000000036
SLOW_PER_BEAM = 0.2  # Reconstructed from the 1590/11 effect parameter.
SLOW_GRAPH = 0x01E7


@dataclass
class Sentry:
    owner: object
    source: int
    body: int
    instance: runtime.Instance
    output: tuple
    position: tuple
    direction: tuple
    speed: float
    launch_tick: int
    health: float = 30.0  # 2658's 0033 override.
    attached: bool = False
    next_shot: float = 0.0
    target: int | None = None
    aura: runtime.Instance | None = None
    support_pillar: int | None = None
    placement: runtime.Instance | None = None

    def centre(self):
        return self.position


class Sentries:
    def __init__(self, fight):
        self.fight = fight
        self.devices = {}

    def launch(self, owner, state, origin, direction):
        node = state.node
        if node.field("m_projectileEntity")["m_entityDef"]["m_entityDef"] != DEFINITION:
            raise ValueError("Symmetra sentry deployment resource changed")
        source = owner.body
        body = self.fight.next_bot
        if body >= 0xD0000000:
            raise ValueError("sentry entity allocator exhausted")
        speed = expr.to_float(state.evaluate(node.field("m_projectileSpeed"), watch=False))
        if not math.isfinite(speed) or speed <= 0:
            raise ValueError("invalid Symmetra sentry launch speed")
        self.fight.next_bot += 1
        output = expr.dynamic(node.field("m_505472EF"))
        owned = [device for device in self.devices.values() if device.source == source]
        if len(owned) >= MAX_DEVICES:
            self.remove(owned[0])
        self.devices[body] = Sentry(owner, source, body, state.instance, output, origin, direction, speed,
                                    self.fight.tick)
        state.instance.write(output, expr.Entity(body))

    def remove(self, device):
        self.devices.pop(device.body)
        self.fight.sombra_controls.retire(device.body)
        self.set_target(device, None)
        placement = device.placement
        if placement is not None and not placement.stopped and placement.component.fault is None:
            placement.component.destroy(placement)
        instance = device.instance
        if instance.stopped or instance.component.fault is not None:
            return
        slot = instance.find_var(*device.output, create=False)
        if slot is not None and slot.value() == expr.Entity(device.body):
            instance.write(device.output, expr.Entity(0))

    def retire(self, source):
        for device in tuple(self.devices.values()):
            if device.source == source:
                self.remove(device)

    def retire_supported_by(self, pillar):
        for device in tuple(self.devices.values()):
            if device.support_pillar == pillar:
                self.remove(device)

    def enemies(self, shooter, *, team=None):
        team = shooter.team if team is None else team
        for device in self.devices.values():
            if device.owner is not shooter and (self.fight.match.game_map.free_for_all or device.owner.team != team):
                yield device

    def damage(self, device, amount):
        if self.devices.get(device.body) is device and amount > 0:
            device.health = expr.f32(max(0.0, device.health - amount))
            if not device.health:
                self.remove(device)

    def trace(self, shooter, origin, direction, reach, *, team=None):
        from ow174.game.combat import ray_capsule

        best = None
        for device in self.enemies(shooter, team=team):
            centre = device.centre()
            distance = ray_capsule(origin, direction, centre, centre, DEVICE_RADIUS)
            if distance is not None and distance <= reach and (best is None or distance < best[0]):
                best = distance, device
        return best

    def movement_mods(self, body):
        # The stock prepared bundle omits 01E7. Use the same speed modifier
        # that the authored aura produces when the graph is available.
        return {(device.body, 0, 0): (SPEED, ADD, -SLOW_PER_BEAM, 0.0)
                for device in self.devices.values() if device.target == body and device.aura is None}

    def set_target(self, device, entity):
        if entity == device.target:
            return
        aura = device.aura
        if aura is not None and not aura.stopped and aura.component.fault is None:
            aura.component.destroy(aura)
            aura.component.run_now()
        device.target, device.aura = entity, None
        if entity is None or (status := graph.graph(SLOW_GRAPH)) is None:
            return
        target = next((target for target in self.fight._targets() if target.entity == entity), None)
        script = getattr(target.owner, "body_script", None) if target is not None else None
        if script is None or script.component.fault is not None:
            return
        component = script.component
        aura = component.create(status)
        if runtime.send_instance_message(aura, 0x024000000000000F, {0x2A: 20}, device.body) != 1:
            component.destroy(aura)
            raise ValueError("Symmetra sentry aura activation resource changed")
        component.run_now()
        device.aura = aura

    def advance_flight(self, device, tick, dt):
        from ow174.game.combat import _along, _dot

        elapsed = (tick - device.launch_tick) * dt
        if elapsed <= 0:
            return
        duration = min(dt, FLIGHT_LIMIT - elapsed + dt)
        if duration <= 0:
            self.remove(device)
            return
        distance = device.speed * duration
        static = self.fight._collision().cast_ray(device.position, device.direction, distance)
        ice = self.fight.ice_wall.cast(device.position, device.direction, distance)
        if ice is not None and (static is None or ice[0] < static.distance):
            at, pillar, normal = ice
            point = _along(device.position, device.direction, at)
            device.support_pillar = pillar.entity
        elif static is not None:
            point, normal = static.point, static.normal
        else:
            device.position = _along(device.position, device.direction, distance)
            if elapsed >= FLIGHT_LIMIT:
                self.remove(device)
            return
        if _dot(normal, device.direction) > 0:
            normal = tuple(-axis for axis in normal)
        device.position = _along(point, normal, DEVICE_RADIUS + 1e-4)
        device.attached = True
        device.next_shot = self.fight.now + ATTACH_DELAY
        if (placement := graph.graph(ATTACH_GRAPH)) is not None:
            mode = self.fight.match.game_map.mode_guid
            world = runtime.World(device.owner.hero.guid, mode)
            component = runtime.Component(device.body, world=world)
            component.frame, component.now = tick, tick * component.frame_ms
            component.set(DEPLOYED, True)
            device.placement = component.create(placement)
            component.run_now()
            if not runtime.send_instance_message(device.placement, ATTACH_MESSAGE, {}, device.body):
                raise ValueError("Symmetra sentry placement activation resource changed")
            component.run_now()

    def update(self, tick, dt):
        fight = self.fight
        for device in tuple(self.devices.values()):
            if self.devices.get(device.body) is not device:
                continue
            if (device.owner.gone or device.owner.body != device.source or device.instance.stopped
                    or device.instance.component.fault is not None):
                self.remove(device)
                continue
            if not device.attached:
                self.advance_flight(device, tick, dt)
                continue
            placement = device.placement
            if placement is not None:
                component = placement.component
                component.run_frame(tick)
                progress = component.entity_var(READY_PROGRESS, create=False)
                if progress is None or expr.to_float(progress.value()) < 1:
                    continue
            if device.body not in fight.sombra_controls.locks:
                self.beam(device, tick, dt)

    def beam(self, device, tick, dt):
        from ow174.game.combat import HitDef, capsule_visible

        fight, origin = self.fight, device.centre()
        candidates = []
        for target in fight._enemies(device.owner):
            if fight._protected(target.owner):
                continue
            a, b = target.segment()
            point = (a[0], (a[1] + b[1]) / 2, a[2])
            distance = math.dist(origin, point)
            if 0 < distance <= RANGE and capsule_visible(
                fight._collision(), origin, a, b, target.radius, pillars=fight.ice_wall.pieces.values(),
            ):
                candidates.append((distance, target.entity, point))
        closest = min(candidates, default=None)
        if closest is None or closest[1] != device.target:
            self.set_target(device, None)
        if closest is None or fight.now < device.next_shot:
            return
        distance, _, point = closest
        direction = tuple((end - start) / distance for start, end in zip(origin, point, strict=True))
        scheduled = device.next_shot if fight.now - device.next_shot < dt else fight.now
        device.next_shot = scheduled + DAMAGE_INTERVAL
        entity = fight._hitscan(
            device.owner, HitDef(DAMAGE_RATE * DAMAGE_INTERVAL, LOADOUT), origin, direction,
            RANGE, tick, tick, True, source_entity=device.body, beam=True,
        )
        self.set_target(device, entity)
