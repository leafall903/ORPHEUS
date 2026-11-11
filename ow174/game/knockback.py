"""Whole Hog's authored force values with reconstructed external movement."""

import math
from dataclasses import dataclass
from itertools import pairwise

from ow174.game import bots, fortify, mei, phase_defenses
from ow174.game.commands import Command
from ow174.game.mover import FELL, IN_AIR, WORLD_GRAVITY, Mover, apply_op, mover_data, statescript_mods
from ow174.game.script import expr

WHOLE_HOG = (0x01C6, 33)
REPLAY_LIMIT = 256
EPSILON = 1e-4


@dataclass(frozen=True)
class Force:
    horizontal: float
    cap: float
    lift: float
    lift_cap: float
    rings: tuple

    def scale(self, distance):
        previous = self.rings[0]
        if distance <= previous[0]:
            return previous[1:]
        for ring in self.rings[1:]:
            if distance <= ring[0]:
                ratio = (distance - previous[0]) / (ring[0] - previous[0])
                return tuple(a + (b - a) * ratio for a, b in zip(previous[1:], ring[1:], strict=True))
            previous = ring
        return 0.0, 0.0, 0.0


def evaluate(state):
    if (state.instance.graph.index, state.node.pos) != WHOLE_HOG:
        return None
    modify = state.node.field("m_6396149F.m_modifyHealth") or {}

    def number(cfg, key):
        return expr.to_float(state.evaluate(cfg.get(key), watch=False))

    values = tuple(number(modify, key) for key in ("m_E33EFEE1", "m_10399AE7", "m_583C208B", "m_8394CF8C"))
    rings = tuple(sorted(
        tuple(number(ring, key) for key in ("m_radius", "m_9102DA3F", "m_579D7CFF", "m_10399AE7"))
        for ring in (modify.get("m_knockbackRings") or ())
    ))
    if (not rings or any(b[0] <= a[0] for a, b in pairwise(rings))
            or any(not math.isfinite(value) or value < 0
                   for row in (values, *rings) for value in row)):
        return None
    return Force(*values, rings)


@dataclass
class Motion:
    owner: object
    velocity: tuple
    vertical: bool
    tick: int
    npc_mover: Mover | None = None
    ballistic: bool = False


class Impulses:
    def __init__(self, fight):
        self.fight = fight
        self.live: dict[int, Motion] = {}
        self.seen: dict[tuple, int] = {}
        self.launches: dict[int, int] = {}

    def retire(self, entity):
        self.launches.pop(entity, None)
        motion = self.live.pop(entity, None)
        if motion is not None and isinstance(motion.owner, bots.Bot):
            motion.owner.velocity = (0.0, 0.0, 0.0)

    def vertical(self, entity):
        motion = self.live.get(entity)
        return motion is not None and motion.vertical

    def airborne(self, entity):
        motion = self.live.get(entity)
        return motion is not None and motion.ballistic and motion.vertical

    def velocity(self, entity, base):
        motion = self.live.get(entity)
        if motion is None:
            return base
        return tuple(a + b for a, b in zip(base, motion.velocity, strict=True))

    def eligible(self, target):
        fight = self.fight
        return (not target.is_device
                and not fight.vitals_of(target).dead and not phase_defenses.active(target.owner)
                and not mei.protected(target.owner)
                and not fortify.active(fight, target.owner)
                and not fight.hooks.controlled(target.entity) and target.entity not in fight.pins.values()
                and target.entity not in fight.gravitons.player_pulls
                and target.entity not in fight.gravitons.npc_movers)

    @staticmethod
    def mover(motion):
        if isinstance(motion.owner, bots.Bot):
            return motion.owner.dva_mover or motion.npc_mover
        return motion.owner.mover

    def _motion(self, target):
        motion = self.live.get(target.entity)
        if motion is None:
            owner = target.owner
            motion = Motion(owner, (0.0, 0.0, 0.0), False, self.fight.tick)
            if isinstance(owner, bots.Bot) and owner.dva_mover is None:
                motion.npc_mover = Mover(owner.position, round(owner.yaw), self.fight._collision(),
                                         mover_data(owner.body))
            self.live[target.entity] = motion
        return motion

    def launch(self, target, velocity):
        """Add an authored launch to the player mover's own velocity."""
        if not self.eligible(target):
            return
        mover = target.owner.mover
        mover.velocity = tuple(a + b for a, b in zip(mover.velocity, velocity, strict=True))
        if velocity[1] > 0:
            mover.flags_word |= FELL
            self.launches[target.entity] = self.fight.tick

    def replay_launches(self, tick, dt):
        """Give a launched player a neutral pose when their world frame had no input."""
        if not self.launches:
            return
        from ow174.game.projectiles import MAX_LEASE_FRAME

        fight, match = self.fight, self.fight.match
        if match.ended or tick > MAX_LEASE_FRAME:
            return
        targets = {target.entity: target for target in fight._targets()}
        scheduled = match.tick
        try:
            match.tick = tick
            for entity, launched in tuple(self.launches.items()):
                if tick < launched:
                    continue
                target = targets.get(entity)
                # Eligibility governs new impulses. A launched mover still needs
                # its normal poses while ability and movement rules take effect.
                if (target is None or not match._shown(target.owner) or fight.vitals_of(target).dead):
                    self.launches.pop(entity, None)
                    continue
                player = target.owner
                mover = player.mover
                if mover.last_command is None or mover.last_command.frame < tick:
                    command = Command(tick, yaw=mover.yaw, pitch=mover.pitch)
                    mover.last_command = command
                    player._step_body_pose(command, dt, tick, replay_accepted=True)
                    history = fight.history.get(entity)
                    if history and history[-1][0] >= tick:
                        at, _, height, eye = history[-1]
                        history[-1] = at, tuple(mover.position), height, eye
                if not mover.flags_word & IN_AIR:
                    self.launches.pop(entity, None)
        finally:
            match.tick = scheduled

    def hit(self, target, direction, distance, force, packet=None, *, ballistic=False):
        if not self.eligible(target):
            return
        key = (*packet, target.entity) if packet is not None else None
        if key is not None and key in self.seen:
            return
        horizontal, vertical, cap = force.scale(distance)
        length = math.hypot(direction[0], direction[2])
        if length <= EPSILON or cap <= 0:
            return
        if key is not None:
            self.seen[key] = self.fight.tick
        motion = self._motion(target)
        motion.ballistic |= ballistic
        x, y, z = motion.velocity
        x += direction[0] * force.horizontal * horizontal / length
        z += direction[2] * force.horizontal * horizontal / length
        speed = math.hypot(x, z)
        ceiling = force.cap * cap
        if speed > ceiling:
            x, z = x * ceiling / speed, z * ceiling / speed
        lift = force.lift * vertical
        if lift > 0 and not motion.vertical:
            mover = self.mover(motion)
            vx, y, vz = mover.velocity
            mover.velocity = (vx, 0.0, vz)
            motion.vertical = True
        motion.velocity = tuple(expr.f32(v) for v in (x, min(force.lift_cap * cap, y + lift), z))

    def push(self, target, direction, horizontal, minimum_up, *, source=False, vertical=0.):
        if not self.eligible(target):
            return
        response = 1.
        script = target.owner.body_script
        if script is not None:
            mods = statescript_mods(script.component)
            for _, (index, op, value, _) in sorted(mods.items(), key=lambda row: (row[1][3], row[0])):
                if index == 26:
                    response = apply_op(op, response, value)
        if response <= 0.:
            return
        motion = self._motion(target)
        mover = self.mover(motion)
        x, y, z = motion.velocity
        length = math.hypot(direction[0], direction[2])
        if length > EPSILON:
            x += direction[0] * horizontal * response / length
            z += direction[2] * horizontal * response / length
        if minimum_up > 0 or vertical:
            vx, vy, vz = mover.velocity
            if not motion.vertical:
                y = vy
                mover.velocity = (vx, 0., vz)
            lift = 0.
            if minimum_up > 0:
                lift = minimum_up if source else max(0., minimum_up - y)
            y += (vertical + lift) * response
            motion.vertical = motion.ballistic = True
        motion.velocity = tuple(expr.f32(v) for v in (x, y, z))

    def update(self, tick, dt):
        if self.seen:
            self.seen = {key: at for key, at in self.seen.items() if tick - at <= REPLAY_LIMIT}
        if not self.live:
            return
        fight = self.fight
        targets = {target.entity: target for target in fight._targets()}
        for entity, motion in tuple(self.live.items()):
            target = targets.get(entity)
            if target is None or target.owner is not motion.owner or not self.eligible(target):
                self.retire(entity)
                continue
            if tick <= motion.tick:
                continue
            motion.tick = tick
            mover = self.mover(motion)
            owner = motion.owner
            if motion.npc_mover is not None:
                mover.use(world=fight._collision())
                if tuple(mover.position) != owner.position:
                    mover.position[:] = owner.position
                    mover.body = None
            x, y, z = mover.displace(motion.velocity, dt, external_airborne=motion.ballistic)
            data = mover.values
            flat = math.hypot(x, z)
            decel = 0. if motion.ballistic and motion.vertical else data.decel_ground_base
            remaining = max(0.0, flat - decel * data.run_forward * dt)
            damping = remaining / flat if flat > EPSILON else 0.0
            if motion.velocity[1] < 0 and y >= -EPSILON:
                motion.vertical = False
            vertical = y - WORLD_GRAVITY * data.gravity_scale * dt if motion.vertical else 0.0
            motion.velocity = x * damping, vertical, z * damping
            if isinstance(owner, bots.Bot):
                owner.position = tuple(mover.position)
                owner.velocity = tuple(a + b for a, b in zip(mover.velocity, motion.velocity, strict=True))
                owner.moved_tick = tick
            history = fight.history.get(entity)
            if history and history[-1][0] == tick:
                frame, _, height, eye = history[-1]
                history[-1] = frame, tuple(mover.position), height, eye
            if remaining <= EPSILON and not motion.vertical:
                self.retire(entity)
