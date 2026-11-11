"""Hosted 104319 Earthshatter using the authored graph and sampled ground fronts."""

import math
from dataclasses import dataclass, field

from ow174.game import sigma_barrier, sigma_hyperspheres
from ow174.game.collision import MOVER
from ow174.game.script import expr, runtime

HERO = 0x02E0000000000007
GRAPH = 0x00BE
FRONTS = 101


def owner(world):
    if world.match is None or world.hero != HERO:
        return None
    return next((p for p in world.match.players if p.has_body and p.body == world.body), None)


def prepare_landing(player):
    script = player.body_script
    script.component.set(417, player.mover.airborne)
    if player.mover.airborne or script.component.strict:
        return
    instance = next((i for i in script.component.instances.values() if i.graph.index == GRAPH), None)
    waiting = instance.states.get(8) if instance is not None else None
    if waiting is None or not waiting.active:
        return
    # Only 00BE's landing transition uses this reconstructed elapsed gate.
    gate = instance.graph.node(44)
    minimum = expr.to_float(waiting.evaluate(gate.field("m_AA6E611E"), watch=False))
    if waiting.now - waiting.start >= minimum * 1000 and instance.follow(gate, "m_truePlug", waiting):
        waiting.request(runtime.ABORT)


def ground_ray(world):
    player = owner(world)
    if player is None or world.match.collision_world is None:
        return runtime.RaycastResult(False, None, None, None)
    x, y, z = player.mover.position
    ground = world.match.collision_world.ground(x, z, y + 0.05, y - 1.5, player.mover.category)
    if ground is None:
        return runtime.RaycastResult(False, None, None, None)
    return runtime.RaycastResult(True, None, expr.Vec3(x, ground.y, z), expr.Vec3(*ground.normal))


def native(world, cfg, instance):
    player = owner(world)
    if player is None or instance.evaluate(cfg.get("m_entity")) != expr.Entity(world.body):
        return NotImplemented
    kind = cfg["$"]
    yaw = player.mover.yaw * math.tau / 65536
    if kind == "STU_71A87E92":
        return expr.Vec3(*player.mover.position)
    if kind == "STU_021D01CD":
        return expr.Vec3(math.sin(yaw), 0.0, math.cos(yaw))
    return math.degrees(yaw)


@dataclass
class Wave:
    caster: object
    body: int
    instance_id: int
    activation: int
    origin: tuple
    yaw: float
    speed: float
    reach: float
    cone: float
    height: float
    stun: float
    direct: object
    near: object
    near_centre: tuple
    near_radius: float
    started: float
    fronts: list
    travelled: float = 0.0
    hit_bodies: set = field(default_factory=set)
    hit_barriers: set = field(default_factory=set)


def front_distance(feet, start, end):
    dx, dz = end[0] - start[0], end[2] - start[2]
    length2 = dx * dx + dz * dz
    share = ((feet[0] - start[0]) * dx + (feet[2] - start[2]) * dz) / length2 if length2 else 0
    share = max(0.0, min(1.0, share))
    return math.hypot(feet[0] - start[0] - share * dx, feet[2] - start[2] - share * dz)


class Earthshatters:
    def __init__(self, combat):
        self.combat = combat
        self.waves: dict[int, Wave] = {}

    def command(self, player):
        from ow174.game.combat import hit_def

        if player.body in self.waves:
            return
        script = player.body_script
        instance = next((i for i in script.component.instances.values() if i.graph.index == GRAPH), None)
        state = instance.states.get(0) if instance is not None else None
        if state is None or not state.active:
            return
        origin, near = (instance.find_var(expr.INSTANCE, var).value() for var in (431, 1188))
        if not isinstance(origin, expr.Vec3) or not isinstance(near, expr.Vec3):
            return

        def number(name):
            return expr.to_float(state.evaluate(state.node.field(name), watch=False))

        base_node, close_node = (instance.graph.state(index) for index in (1, 41))
        direct = hit_def(state, {"m_modifyHealth": base_node.field("m_modifyHealth")})
        close = hit_def(state, {"m_modifyHealth": close_node.field("m_modifyHealth")})
        speed, reach = number("m_speed"), number("m_6A79BD98")
        if direct is None or close is None or not (speed > 0 and reach > 0):
            return
        point = (origin.x, origin.y, origin.z)
        wave = Wave(
            player, player.body, instance.id, state.counter, point,
            player.mover.yaw * math.tau / 65536, speed, reach, number("m_A30B383E"),
            number("m_525E5A40"), expr.to_float(instance.find_var(expr.INSTANCE, 215).value()),
            direct, close, (near.x, near.y, near.z),
            expr.to_float(state.evaluate(instance.graph.state(40).field("m_radius"), watch=False)),
            self.combat.now, [point] * FRONTS,
        )
        self.waves[player.body] = wave
        self.close_hit(wave)

    def damage(self, wave, target, hit):
        fight = self.combat
        dealt = fight._damage(wave.caster, target, hit.amount, False, hit.loadout, fight.tick,
                              world_tick=fight.tick, tags=hit.tags)
        if dealt and not fight.vitals_of(target).dead:
            fight._apply_stun(target, wave.stun, extend_only=True)

    def close_hit(self, wave):
        from ow174.game.combat import capsule_visible, segment_distance

        fight = self.combat
        x, y, z = wave.origin
        start = (x, y + 0.1, z)
        for target in fight._enemies(wave.caster):
            a, b = target.segment()
            if segment_distance(wave.near_centre, a, b) > wave.near_radius + target.radius:
                continue
            end = (target.feet[0], target.feet[1] + 0.1, target.feet[2])
            if (capsule_visible(fight._collision(), start, a, b, target.radius, walls=fight.walls)
                    and self.shield(wave, start, end) is None):
                # 00BE excludes TrackTargets output 468 from the ground wave,
                # regardless of whether the close damage reaches health.
                wave.hit_bodies.add(target.entity)
                self.damage(wave, target, wave.near)

    def shield(self, wave, start, end, projecting=None):
        delta = tuple(b - a for a, b in zip(start, end, strict=True))
        length = math.hypot(*delta)
        if not length:
            return None
        direction = tuple(v / length for v in delta)
        best = sigma_hyperspheres.barrier_contact(
            self.combat, wave.caster, start, direction, length, excluded=projecting or (),
        )
        for barrier, start_offset, end_offset in (projecting or {}).values():
            # Sweep in the frame of the translating mesh and compare contact
            # times along the original wave segment.
            relative_start = tuple(v - d for v, d in zip(start, start_offset, strict=True))
            relative_end = tuple(v - d for v, d in zip(end, end_offset, strict=True))
            relative = tuple(b - a for a, b in zip(relative_start, relative_end, strict=True))
            span = math.hypot(*relative)
            if not span:
                continue
            distance = barrier.intersection(relative_start, tuple(v / span for v in relative), span)
            if distance is not None:
                contact = distance / span * length
                if best is None or contact < best[0]:
                    best = contact, sigma_barrier.damage_barrier, (barrier,), barrier.entity
        return best

    def advance(self, wave, distance, projecting):
        from ow174.game.combat import ray_world

        fight = self.combat
        world = fight._collision()
        segments = []
        shield_contacts = {}
        for index, previous in enumerate(wave.fronts):
            if previous is None:
                continue
            angle = wave.yaw + math.radians(wave.cone * (index / (FRONTS - 1) - 0.5))
            x = wave.origin[0] + math.sin(angle) * distance
            z = wave.origin[2] + math.cos(angle) * distance
            ground = world.ground(x, z, previous[1] + wave.height, previous[1] - wave.height, MOVER)
            if ground is None or ground.normal[1] < 0.5:
                wave.fronts[index] = None
                continue
            current = (x, ground.y, z)
            start = (previous[0], previous[1] + wave.height, previous[2])
            end = (x, ground.y + wave.height, z)
            delta = tuple(b - a for a, b in zip(start, end, strict=True))
            length = math.hypot(*delta)
            wall = ray_world(world, start, tuple(v / length for v in delta), length) if length else None
            if wall is not None or fight.walls.blocked(start, end):
                wave.fronts[index] = None
                continue
            shield = self.shield(wave, (previous[0], previous[1] + 0.1, previous[2]),
                                 (x, ground.y + 0.1, z), projecting)
            if shield is not None:
                _, damage, args, body = shield
                shield_contacts.setdefault(body, (damage, args))
                wave.fronts[index] = None
                continue
            segments.append((previous, current))
            wave.fronts[index] = current
        for body, (damage, args) in shield_contacts.items():
            if body not in wave.hit_barriers:
                wave.hit_barriers.add(body)
                damage(*args, wave.direct.amount)
        for target in fight._enemies(wave.caster):
            if target.entity in wave.hit_bodies:
                continue
            if any(-0.1 <= target.feet[1] - end[1] <= wave.height
                   and front_distance(target.feet, start, end) <= target.radius
                   for start, end in segments):
                wave.hit_bodies.add(target.entity)
                self.damage(wave, target, wave.direct)

    def retire(self, body):
        self.waves.pop(body, None)

    def update(self, projecting):
        from ow174.game.combat import TICK

        for body, wave in tuple(self.waves.items()):
            script = wave.caster.body_script
            if script is None or wave.caster.body != body or wave.caster.gone:
                self.retire(body)
                continue
            distance = min(wave.reach, (self.combat.now - wave.started) * wave.speed)
            moving = [
                (barrier, *projecting[barrier.entity])
                for barrier in sigma_barrier.enemies(self.combat, wave.caster)
                if barrier.entity in projecting and barrier.phase == 2
                and projecting[barrier.entity][0] != barrier.position
            ] if projecting else []
            while distance > wave.travelled:
                previous = wave.travelled
                wave.travelled = min(distance, previous + wave.speed * TICK)
                motion = {}
                for barrier, position, last in moving:
                    if not barrier.active:
                        continue
                    elapsed = barrier.last - last
                    delta = tuple(a - b for a, b in zip(position, barrier.position, strict=True))
                    offsets = []
                    for travelled in (previous, wave.travelled):
                        time = wave.started + travelled / wave.speed
                        remaining = max(0.0, min(1.0, (barrier.last - time) / elapsed))
                        offsets.append(tuple(axis * remaining for axis in delta))
                    motion[barrier.entity] = (barrier, *offsets)
                self.advance(wave, wave.travelled, motion)
            if distance >= wave.reach:
                self.retire(body)
                instance = script.component.instances.get(wave.instance_id)
                state = instance.states.get(0) if instance is not None else None
                if state is not None and state.active and state.counter == wave.activation:
                    state.request_finish()
                    script.component.run_now()
