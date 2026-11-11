"""Hosted Whole Hog force from the 104319 Scrap Gun volley."""

import math
from dataclasses import dataclass
from itertools import pairwise

from ow174.game import body_protection, bots
from ow174.game.script import expr


@dataclass(frozen=True)
class Force:
    speed: float
    cap: float
    lift: float
    lift_cap: float
    rings: tuple

    def scale(self, distance):
        if distance <= self.rings[0][0]:
            return self.rings[0][1:]
        for left, right in pairwise(self.rings):
            if distance <= right[0]:
                share = (distance - left[0]) / (right[0] - left[0])
                return tuple(a + (b - a) * share for a, b in zip(left[1:], right[1:], strict=True))
        return 0.0, 0.0, 0.0


def selected(state):
    return (state.instance.graph.index, state.node.pos) == (0x01C6, 33)


def force(state):
    modify = state.node.field("m_6396149F.m_modifyHealth")

    def number(cfg):
        value = expr.to_float(state.evaluate(cfg, watch=False))
        if not math.isfinite(value) or value < 0:
            raise ValueError("invalid Whole Hog force input")
        return value

    values = [number(modify[k]) for k in ("m_E33EFEE1", "m_10399AE7", "m_583C208B", "m_8394CF8C")]
    rings = tuple(sorted(
        tuple(number(row[k]) for k in ("m_radius", "m_9102DA3F", "m_579D7CFF", "m_10399AE7"))
        for row in modify["m_knockbackRings"]
    ))
    if not rings or any(b[0] <= a[0] for a, b in pairwise(rings)):
        raise ValueError("invalid Whole Hog force rings")
    return Force(*values, rings)


def push(fight, target, direction, distance, spec):
    owner = target.owner
    if (fight.vitals_of(target).dead or body_protection.fortify_blocks_control(owner)
            or fight.roadhog_hooks.controlled(target.entity)
            or target.entity in fight.pins.values() or target.entity in fight.gravitons.player_pulls):
        return
    dx, _, dz = direction
    length = math.hypot(dx, dz)
    if length <= 1e-9:
        return
    horizontal, vertical, cap = spec.scale(distance)
    if not (horizontal or vertical):
        return
    npc = isinstance(owner, bots.Bot)
    vx, vy, vz = owner.velocity if npc else owner.mover.velocity
    push_x = vx + dx * spec.speed * horizontal / length
    push_z = vz + dz * spec.speed * horizontal / length
    speed = math.hypot(push_x, push_z)
    limit = spec.cap * cap
    if speed > limit:
        push_x, push_z = push_x * limit / speed, push_z * limit / speed
    lift = min(spec.lift_cap * cap, vy + spec.lift * vertical)
    delta = (push_x - vx, lift - vy, push_z - vz)
    if npc:
        owner.push(fight._collision(), delta, fight.tick)
    else:
        owner.mover.add_external_impulse(delta)
