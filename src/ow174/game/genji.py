"""Explicit reconstruction of Genji mobility outside the native movement backend.

The normal capsule mover still resolves collision, gravity and pose history.
These policies consume button edges and authored movement values; they do not
claim restored native custom-State codecs or original-client prediction.
"""

import math
from dataclasses import dataclass

from ow174.game.commands import JUMP
from ow174.game.mover import (
    FELL,
    JUMP_INTENT,
    JUMP_LATCH,
    JUMPED,
    REAL_JUMP,
    WORLD_GRAVITY,
    intent,
    modded,
)

GENJI = 0x02E0000000000029


@dataclass
class Mobility:
    jump_held: bool = False
    air_jump_used: bool = False
    climbing: bool = False
    climb_elapsed: float = 0.0
    climb_spent: bool = False
    last_frame: int | None = None

    def before_pose(self, player, command, seconds, *, driven=False):
        mover = player.mover
        if self.last_frame is not None and self.climbing:
            self.climb_elapsed += max(0, command.frame - self.last_frame - 1) * seconds
        self.last_frame = command.frame
        active = sorted((m for m in mover.mods.values() if m.acts(command.frame)), key=lambda m: m.order())
        values = modded(mover.data, active)
        down = bool(command.buttons & JUMP)
        jumping = intent(active, JUMP_INTENT, 1.0 if down else 0.0) > 0.5
        pressed = down and not self.jump_held
        self.jump_held = down
        if not mover.airborne:
            self.air_jump_used = False
            self.climb_elapsed = 0.0
            self.climb_spent = False
        if driven or not jumping:
            self.end_climb(mover)
            return
        if self.climb(player, command, seconds, values):
            return
        self.end_climb(mover)
        if pressed and mover.airborne and not self.air_jump_used:
            vx, _, vz = mover.velocity
            mover.velocity = (vx, values.jump_speed, vz)
            mover.flags_word = mover.flags_word & ~FELL | JUMPED | REAL_JUMP | JUMP_LATCH
            mover.air_ticks = 0
            mover.fall = 0.0
            self.air_jump_used = True

    def end_climb(self, mover):
        if self.climbing:
            self.climb_spent = True
            # Reconstructed refresh policy; native custom-State transition unverified.
            self.air_jump_used = False
            vx, vy, vz = mover.velocity
            mover.velocity = (vx, min(vy, 0.0), vz)
        self.climbing = False

    def climb(self, player, command, seconds, values):
        mover = player.mover
        if not mover.airborne or command.forward <= 0 or self.climb_spent:
            return False
        script = player.body_script
        instance = next((i for i in script.component.instances.values() if i.graph.index == 0x0157), None)
        if instance is None:
            return False
        node = instance.graph.states[5]  # STU_92ED01D0, opaque custom movement State
        # Inferred field roles:7.5 upward speed and0.75 maximum active seconds.
        speed = instance.evaluate(node.field("m_4E3BB2CE"))
        duration = instance.evaluate(node.field("m_E7146E0B"))
        margin = instance.evaluate(node.field("m_70E47D79"))
        if (type(speed) not in (int, float) or type(duration) not in (int, float)
                or type(margin) not in (int, float) or not math.isfinite(margin) or margin <= 0
                or not math.isfinite(speed) or not math.isfinite(duration)
                or speed <= 0 or duration <= 0 or self.climb_elapsed >= duration):
            return False
        angle = command.yaw * math.tau / 65536
        forward = (math.sin(angle), 0.0, math.cos(angle))
        x, y, z = mover.position
        origin = (x, y + values.stand_height * 0.5, z)
        hit = mover.world.cast_ray(origin, forward, values.stand_radius + margin, mover.category)
        if hit is None or abs(hit.normal[1]) > 0.25:
            return False
        if sum(a * b for a, b in zip(hit.normal, forward, strict=True)) >= -0.5:
            return False
        self.climbing = True
        self.climb_elapsed += seconds
        vx, _, vz = mover.velocity
        mover.velocity = (vx, float(speed) + WORLD_GRAVITY * values.gravity_scale * seconds, vz)
        mover.fall = 0.0
        return True
