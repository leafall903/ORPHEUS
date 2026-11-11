"""Explicit server reconstruction of104319 Earthshatter's ground propagation.

Authored speed/range/cone/damage/control drive a sampled ground front. This is
not the missing native Shockwave implementation or its skeletal presentation.
"""

import math
from dataclasses import dataclass, field

from ow174.game import rein_barrier
from ow174.game.collision import MOVER
from ow174.game.script import expr, runtime


@dataclass
class Wave:
    shooter: object
    body: int
    instance: int
    activation: int
    origin: tuple
    yaw: float
    speed: float
    reach: float
    cone: float
    height: float
    duration: float
    amount: float
    near_amount: float
    near_centre: tuple
    near_radius: float
    loadout: int | None
    started: float
    travelled: float = 0.0
    near_done: bool = False
    fronts: list = field(default_factory=list)
    blocked: set = field(default_factory=set)
    hit: set = field(default_factory=set)
    near_hit: set = field(default_factory=set)
    shield_poses: dict = field(default_factory=dict)


def _distance_xz(point, a, b):
    dx, dz = b[0] - a[0], b[2] - a[2]
    squared = dx * dx + dz * dz
    share = max(0.0, min(1.0, ((point[0] - a[0]) * dx + (point[2] - a[2]) * dz) / squared)) if squared else 0
    return math.hypot(point[0] - a[0] - share * dx, point[2] - a[2] - share * dz)


HERO = 0x02E0000000000007


def _shield(fight, wave, start, end):
    delta = tuple(b - a for a, b in zip(start, end, strict=True))
    length = math.hypot(*delta)
    if not length:
        return None
    direction = tuple(v / length for v in delta)
    best = fight._shield_hit(wave.shooter, start, direction, length)
    if wave.shield_poses:
        vertices, triangles = rein_barrier._geometry(fight.rein_geometry)
        for centre, basis in wave.shield_poses.values():
            t = rein_barrier.surface_distance(start, direction, length, centre, basis, vertices, triangles)
            if t is not None and (best is None or t < best[0]):
                best = t, None
    return best


def _damage_shield(fight, wave, contact):
    if not isinstance(contact, rein_barrier.Contact):
        return
    instance = contact.instance
    key = instance.component.entity, instance.id
    if key in wave.shield_poses:
        return
    for owner, centre, basis, _, _ in rein_barrier._surfaces(fight, wave.shooter):
        if owner is instance:
            wave.shield_poses[key] = centre, basis
            contact.damage(wave.amount)
            return


def _hurt(fight, wave, target, amount, *, near=False):
    vitals = fight.vitals_of(target)
    before = vitals.total
    fight._damage(wave.shooter, target, amount, False, wave.loadout, fight.tick, world_tick=fight.tick)
    if (vitals.total < before and not vitals.dead and not fight.control_immune(target.owner)
            and wave.duration > 0):
        fight._apply_stun(target, wave.duration, extend_only=True)
    (wave.near_hit if near else wave.hit).add(target.entity)


def advance(fight, wave):
    """Sample101 rays across the authored cone; retain front, terrain and shield history."""
    from ow174.game.combat import capsule_visible, segment_distance

    world = fight._collision()
    targets = list(fight._enemies(wave.shooter))
    if not wave.fronts:
        wave.fronts = [wave.origin] * 101
    if not wave.near_done:
        wave.near_done = True
        for target in targets:
            a, b = target.segment()
            if segment_distance(wave.near_centre, a, b) > wave.near_radius + target.radius:
                continue
            end = (target.feet[0], target.feet[1] + .1, target.feet[2])
            start = (wave.origin[0], wave.origin[1] + .1, wave.origin[2])
            if (capsule_visible(world, start, a, b, target.radius)
                    and not fight.walls.blocked(start, end)
                    and _shield(fight, wave, start, end) is None):
                _hurt(fight, wave, target, wave.near_amount, near=True)
    distance = min(wave.reach, max(0.0, (fight.now - wave.started) * wave.speed))
    if distance <= wave.travelled:
        return False
    segments = []
    for index, previous in enumerate(wave.fronts):
        if index in wave.blocked:
            continue
        angle = wave.yaw + math.radians(wave.cone * (index / 100 - .5))
        x, z = wave.origin[0] + math.sin(angle) * distance, wave.origin[2] + math.cos(angle) * distance
        ground = world.ground(x, z, previous[1] + wave.height, previous[1] - wave.height, MOVER)
        if ground is None or ground.normal[1] < .5:
            wave.blocked.add(index)
            continue
        current = (x, ground.y, z)
        # A waist-height chord rejects walls while allowing ground-height changes
        # up to the declared step envelope. Exact native terrain traversal is open.
        start = (previous[0], previous[1] + wave.height, previous[2])
        end = (x, ground.y + wave.height, z)
        delta = tuple(b - a for a, b in zip(start, end, strict=True))
        length = math.hypot(*delta)
        wall = world.cast_ray(start, tuple(v / length for v in delta), length, MOVER) if length else None
        if wall is not None or fight.walls.blocked(start, end):
            wave.blocked.add(index)
            continue
        start = (previous[0], previous[1] + .1, previous[2])
        end = (x, ground.y + .1, z)
        shield = _shield(fight, wave, start, end)
        if shield is not None:
            contact, shield_state = shield
            delta = tuple(b - a for a, b in zip(start, end, strict=True))
            length = math.hypot(*delta)
            current = tuple(start[k] + delta[k] * contact / length - (.1 if k == 1 else 0) for k in range(3))
            wave.blocked.add(index)
            _damage_shield(fight, wave, shield_state)
        segments.append((previous, current))
        wave.fronts[index] = current
    for target in targets:
        if target.entity in wave.hit or fight.vitals_of(target).dead:
            continue
        if any(-.1 <= target.feet[1] - end[1] <= wave.height
               and _distance_xz(target.feet, start, end) <= target.radius
               for start, end in segments):
            _hurt(fight, wave, target, wave.amount)
    wave.travelled = distance
    return distance >= wave.reach



class Shatters:
    def __init__(self, combat):
        self.combat = combat
        self.shatters = {}

    def update(self):
        for wave in tuple(self.shatters.values()):
            if wave.shooter.body != wave.body or advance(self.combat, wave):
                self._finish_shatter(wave)

    def shatter_ground(self, body):
        """Declared BE/37 downward1.5m ground query, using the posed owner's feet."""
        player = next((p for p in self.combat.match.players if p.body == body), None)
        if player is None or self.combat.match.collision_world is None:
            return None
        x, y, z = player.mover.position
        return self.combat._collision().ground(x, z, y + .05, y - 1.5, player.mover.category)

    def _start_shatter(self, player):
        from ow174.game.combat import _number

        script = player.body_script
        if script.hero != HERO or player.body in self.shatters:
            return
        instance = next((i for i in script.component.instances.values() if i.graph.index == 0xBE), None)
        state = instance.states.get(0) if instance is not None else None
        if state is None or not state.active:
            return
        origin = instance.find_var(0, 431).value()
        near = instance.find_var(0, 1188).value()
        if not isinstance(origin, expr.Vec3) or not isinstance(near, expr.Vec3):
            return
        def number(name):
            return _number(state.evaluate(state.node.field(name), watch=False), 0)

        base = instance.state(1).node.field("m_modifyHealth")
        close = instance.state(41).node.field("m_modifyHealth")
        loadout = state.evaluate(base.get("m_loadout"), watch=False)
        wave = Wave(
            player, player.body, instance.id, state.counter, (origin.x, origin.y, origin.z),
            player.mover.yaw * math.tau / 65536, number("m_speed"), number("m_6A79BD98"),
            number("m_A30B383E"), number("m_525E5A40"),
            _number(instance.find_var(0, 215).value(), 0),
            _number(state.evaluate(base.get("m_amount"), watch=False), 0),
            _number(state.evaluate(close.get("m_amount"), watch=False), 0),
            (near.x, near.y, near.z),
            _number(state.evaluate(instance.state(40).node.field("m_radius"), watch=False), 0),
            loadout.guid if isinstance(loadout, expr.Asset) else None, self.combat.now,
        )
        if wave.speed > 0 and wave.reach > 0:
            self.shatters[player.body] = wave
            advance(self.combat, wave)

    def _finish_shatter(self, wave):
        self.shatters.pop(wave.body, None)
        script = wave.shooter.body_script
        if script is None or wave.shooter.body != wave.body:
            return
        instance = script.component.instances.get(wave.instance)
        state = instance.states.get(0) if instance is not None else None
        if state is not None and state.active and state.counter == wave.activation:
            state.request(runtime.FINISH)
            script.component.run_now()  # authored onEnd/ActionStopAbility owns the channel end

    def prepare_shatter_frame(self, player):
        from ow174.game.combat import _number

        script = player.body_script
        if script is None or script.hero != HERO:
            return
        instance = next((i for i in script.component.instances.values() if i.graph.index == 0xBE), None)
        script.component.set(417, bool(player.mover.airborne))
        air = instance.states.get(8) if instance is not None else None
        if air is not None and air.active and not player.mover.airborne:
            # Scoped reconstruction of44's missing elapsed-state predicate. Its
            # authored .383s floor precedes the original landing-animation path;
            # do not change unrelated permissive4E2BCAAB conditions.
            gate = instance.graph.node(44)
            delay = _number(air.evaluate(gate.field("m_AA6E611E"), watch=False), 0)
            if (script.component.now - air.start >= delay * 1000
                    and instance.follow(gate, "m_truePlug", air)):
                air.request(runtime.ABORT)
        tracker = instance.states.get(37) if instance is not None else None
        if tracker is not None and tracker.active:
            ground = self.shatter_ground(player.body)
            instance.write((0, 10360), ground is not None)
            if ground is not None:
                instance.write((0, 1724), expr.Vec3(player.mover.position[0], ground.y,
                                                  player.mover.position[2]))
                marker = instance.states.get(29)
                if marker is not None and marker.active:
                    marker.request_finish(0)
