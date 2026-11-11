"""01C6/33 Whole Hog force profile and per-volley recipient arbitration."""

import math
from dataclasses import dataclass, field
from itertools import pairwise

from ow174.game import bots, defenses
from ow174.game.script import expr

VOLLEY = (0x01C6, 33)


@dataclass
class Volley:
    force: float
    cap: float
    lift: float
    lift_cap: float
    rings: tuple
    recipients: set = field(default_factory=set)

    def scale(self, distance):
        first = self.rings[0]
        if distance <= first[0]:
            return first[1:]
        for inner, outer in pairwise(self.rings):
            if distance <= outer[0]:
                share = (distance - inner[0]) / (outer[0] - inner[0])
                return tuple(a + (b - a) * share for a, b in zip(inner[1:], outer[1:], strict=True))
        return 0.0, 0.0, 0.0

    def push(self, fight, target, direction, distance):
        """Add one swept force per recipient. Ring interpolation and damping are host policies."""
        owner = target.owner
        training_bot = isinstance(owner, bots.Bot) and owner.spawner.get("manual_spawn") is not True
        if (target.entity in self.recipients or training_bot
                or fight.vitals_of(target).dead or defenses.invulnerable(target.owner)
                or fight.fortify_control_immune(target) or fight.hooks.controlled(target.entity)):
            return
        size = math.hypot(direction[0], direction[2])
        if size <= 1e-9:
            return
        self.recipients.add(target.entity)
        horizontal, vertical, cap = self.scale(distance)
        _, (x, y, z) = fight.npc_impulses.get(target.entity, (fight.tick, (0.0, 0.0, 0.0)))
        x += direction[0] * self.force * horizontal / size
        z += direction[2] * self.force * horizontal / size
        flat = math.hypot(x, z)
        limit = self.cap * cap
        if flat > limit:
            x, z = x * limit / flat, z * limit / flat
        velocity = (x, min(y + self.lift * vertical, self.lift_cap * cap), z)
        if any(velocity):
            fight.npc_impulses[target.entity] = (fight.tick, velocity)


def evaluate(state):
    modify = state.node.field("m_6396149F.m_modifyHealth")

    def number(config):
        value = expr.to_float(state.evaluate(config, watch=False))
        if not math.isfinite(value) or value < 0:
            raise ValueError("invalid Whole Hog force value")
        return value

    force, cap, lift, lift_cap = (
        number(modify[key]) for key in ("m_E33EFEE1", "m_10399AE7", "m_583C208B", "m_8394CF8C")
    )
    rings = tuple(sorted(tuple(number(ring[key]) for key in (
        "m_radius", "m_9102DA3F", "m_579D7CFF", "m_10399AE7",
    )) for ring in modify["m_knockbackRings"]))
    if not rings or any(outer[0] <= inner[0] for inner, outer in pairwise(rings)):
        raise ValueError("invalid Whole Hog force rings")
    return Volley(force, cap, lift, lift_cap, rings)
