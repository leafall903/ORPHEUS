"""Selected Whole Hog, Soundwave and Rocket Punch external motion.

DATA:01C6/33 ModifyHealth force14/cap10/lift1.8/lift-cap14 expressions and
distance rings5/20/100, all evaluated through the existing ruleset interface.
RECONSTRUCTED: field interpretation, one contribution per volley/recipient,
additive capped external velocity, linear ring interpolation, mover-data damping,
capsule sweep ordering and restraint/immunity priority. No native consumer parity.
"""

import math
from dataclasses import dataclass
from itertools import pairwise

from ow174.game import bots, defenses, reinhardt_charge
from ow174.game.commands import Command
from ow174.game.mover import Mover, mover_data
from ow174.game.script import expr


@dataclass(frozen=True)
class Spec:
    force: float
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
                share = (distance-previous[0])/max(1e-9, ring[0]-previous[0])
                return tuple(a+(b-a)*share for a, b in zip(previous[1:], ring[1:], strict=True))
            previous = ring
        return (0.0, 0.0, 0.0)


def evaluate(state, modify):
    instance = getattr(state, 'instance', None)
    definition = getattr(instance, 'graph', None)
    identity = getattr(definition, 'index', None), getattr(getattr(state, 'node', None), 'pos', None)
    if identity != (0x1C6, 33):
        return None

    def number(config):
        value = expr.to_float(state.evaluate(config, watch=False))
        if not math.isfinite(value) or value < 0:
            raise ValueError('invalid Whole Hog force input')
        return value

    values = [number(modify[k]) for k in ('m_E33EFEE1', 'm_10399AE7', 'm_583C208B', 'm_8394CF8C')]
    rings = tuple(sorted(tuple(number(r[k]) for k in ('m_radius', 'm_9102DA3F', 'm_579D7CFF',
                                                      'm_10399AE7'))
                         for r in modify['m_knockbackRings']))
    if not rings or any(b[0] <= a[0] for a, b in pairwise(rings)):
        raise ValueError('invalid Whole Hog force rings')
    return Spec(*values, rings)


@dataclass
class Motion:
    owner: object
    body: int
    velocity: tuple
    tick: int
    npc_mover: object = None


class Impulses:
    def __init__(self, fight):
        self.fight, self.live, self.seen = fight, {}, {}

    def retire(self, entity, *, forget=True):
        motion = self.live.pop(entity, None)
        if motion is not None and isinstance(motion.owner, bots.Bot):
            motion.owner.velocity = (0.0, 0.0, 0.0)
        if forget:
            self.seen = {k: t for k, t in self.seen.items() if k[-1] != entity}

    def velocity(self, entity):
        motion = self.live.get(entity)
        return motion.velocity if motion is not None else (0.0, 0.0, 0.0)

    def _restrained(self, entity, tick):
        fight = self.fight
        return (fight.hooks.controlled(entity) or fight.traps.rooted(entity, tick)
                or entity in fight.pins.values() or reinhardt_charge.pinned(fight, entity)
                or fight.halt.drove_pose(entity, tick))

    def _eligible(self, target):
        fight = self.fight
        return (not fight.vitals_of(target).dead
                and not defenses.control_immune(target.owner)
                and not self._restrained(target.entity, fight.tick)
                and target.entity not in fight.gravitons.pulled)

    def _motion(self, target):
        motion = self.live.get(target.entity)
        if motion is None:
            motion = Motion(target.owner, target.entity, (0.0, 0.0, 0.0), self.fight.tick)
            if isinstance(target.owner, bots.Bot):
                actor = target.owner
                motion.npc_mover = Mover(actor.position, round(actor.yaw), self.fight._collision(),
                                         mover_data(actor.body))
            self.live[target.entity] = motion
        return motion

    def vector(self, target, vector):
        # Reuse the selected external-motion channel and its control priority.
        if (not isinstance(vector, expr.Vec3) or not all(map(math.isfinite, vector))
                or not any(vector) or not self._eligible(target)):
            return False
        motion = self._motion(target)
        motion.velocity = tuple(expr.f32(a + b) for a, b in zip(motion.velocity, vector, strict=True))
        return True

    def hit(self, target, direction, distance, spec, packet):
        fight = self.fight
        if spec is None or packet is None or not self._eligible(target):
            return
        key = (*packet, target.entity)
        if key in self.seen:
            return
        self.seen[key] = fight.tick
        horizontal, vertical, cap = spec.scale(distance)
        size = math.hypot(direction[0], direction[2])
        if size <= 1e-9:
            return
        motion = self._motion(target)
        x, y, z = motion.velocity
        x += direction[0]/size*spec.force*horizontal
        z += direction[2]/size*spec.force*horizontal
        flat = math.hypot(x, z)
        limit = spec.cap*cap
        if flat > limit:
            x, z = x/flat*limit, z/flat*limit
        y = min(spec.lift_cap*cap, y+spec.lift*vertical)
        motion.velocity = tuple(expr.f32(v) for v in (x, y, z))

    def update(self, tick, *, deferred=None):
        fight = self.fight
        if deferred is None:
            self.seen = {k: at for k, at in self.seen.items() if tick-at <= 128}
        motions = tuple(self.live.items()) if deferred is None else deferred
        pending = []
        if not motions:
            return pending
        targets = {t.entity: t for t in fight._targets()}
        for entity, motion in motions:
            if self.live.get(entity) is not motion:
                continue
            target = targets.get(entity)
            if (target is None or target.owner is not motion.owner or fight.vitals_of(target).dead
                    or defenses.control_immune(target.owner) or self._restrained(entity, tick)):
                self.retire(entity)
                continue
            if tick <= motion.tick:
                continue
            if deferred is None and fight.halt.can_drive_pose(entity):
                pending.append((entity, motion))
                continue
            # Combat retains every intervening impulse tick; never integrate a
            # future command or spend the same world frame twice.
            mover = motion.npc_mover or motion.owner.mover
            for at in range(motion.tick+1, min(tick, motion.tick+128)+1):
                dt = .016
                motion.tick = at
                if motion.npc_mover is not None:
                    mover.position = list(motion.owner.position)
                    mover.body = None
                    mover.step_pose(Command(at), dt, at)
                velocity = mover.displace(motion.velocity, dt)
                data = mover.values
                flat = math.hypot(velocity[0], velocity[2])
                # Ground-base units are multiples of run_forward. Applying this
                # linear damping to the external channel in air is reconstructed.
                remaining = max(0.0, flat-data.decel_ground_base*data.run_forward*dt)
                factor = remaining/flat if flat > 1e-9 else 0.0
                lift = max(0.0, velocity[1]-10*data.gravity_scale*dt)
                motion.velocity = (velocity[0]*factor, lift, velocity[2]*factor)
                if motion.npc_mover is not None:
                    motion.owner.position = tuple(mover.position)
                    motion.owner.velocity = tuple(a+b for a, b in
                                                  zip(mover.velocity, motion.velocity, strict=True))
                if max(map(abs, motion.velocity)) < 1e-4 and (motion.npc_mover is None or not mover.airborne):
                    self.retire(entity, forget=False)
                    break
        return pending
