"""Reconstructed external impulses through the existing collision-backed mover.

Players already have a Mover. NPC route walkers need a temporary one while an
external impulse owns their motion. Native mass/stack/status policies are not
inferred here; callers supply the already evaluated world-space velocity delta.
"""

import math
from functools import partial

from ow174.game import bots, npc_specials
from ow174.game.commands import Command
from ow174.game.mover import FELL, IN_AIR, Mover
from ow174.game.script import expr

TICK = .016
HISTORY = 64


class Impulses:
    def __init__(self, combat):
        self.combat = combat
        self.npcs = {}

    def retire(self, entity):
        item = self.npcs.pop(entity, None)
        if item is not None:
            item[0].external_flags = 0

    def radial_health(self, target, origin, state, scalar=1.0):
        """Project authored ModifyHealth impulse magnitudes away from the source."""
        horizontal = expr.to_float(state.evaluate(
            state.node.field("m_modifyHealth.m_E33EFEE1"), watch=False
        )) * scalar
        vertical = expr.to_float(state.evaluate(
            state.node.field("m_modifyHealth.m_583C208B"), watch=False
        )) * scalar
        dx, dz = target.feet[0] - origin[0], target.feet[2] - origin[2]
        distance = math.hypot(dx, dz)
        x, z = ((dx / distance, dz / distance) if distance else (0.0, 0.0))
        self.apply(target, (x * horizontal, vertical, z * horizontal))

    def apply(self, target, velocity):
        from ow174.game import protections

        actor = target.owner
        if protections.force_immune(actor):
            return
        velocity = tuple(velocity)
        if any(velocity):
            self.combat.assassins.interrupt(actor)
            if isinstance(actor, bots.Bot) and actor.entity in self.combat.sniper_repositions:
                from ow174.game import sniper_reposition

                sniper_reposition.cancel(self.combat, actor)
        mover = getattr(actor, 'mover', None)
        if mover is None:
            if not isinstance(actor, bots.Bot) or actor.dead:
                return
            mover = self._npc_mover(actor, self.combat.tick)
        mover.velocity = tuple(a + b for a, b in zip(mover.velocity, velocity, strict=True))
        if velocity[1] > 0:
            mover.flags_word |= FELL
        if isinstance(actor, bots.Bot):
            actor.external_flags = mover.flags

    def _npc_mover(self, actor, previous):
        item = self.npcs.get(actor.entity)
        if item is not None:
            return item[1]
        mover = Mover(actor.position, round(actor.yaw), self.combat._collision(), actor.movement_data)
        mover.velocity = actor.velocity
        self.npcs[actor.entity] = (actor, mover, previous)
        return mover

    def advance(self, tick):
        for actor in self.combat.match.bots:
            state = npc_specials.reaction_movement(actor)
            if state is not None:
                self._npc_mover(actor, state.start // state.instance.component.frame_ms)
        for entity, (actor, mover, previous) in tuple(self.npcs.items()):
            if actor not in self.combat.match.bots or actor.dead:
                self.retire(entity)
                continue
            if tick <= previous:
                continue
            walls = self.combat.walls
            for frame in range(max(previous + 1, tick - HISTORY + 1), tick + 1):
                before = tuple(mover.position)
                motion = npc_specials.reaction_movement(actor, frame)
                vector = motion.vector(motion.node.field('m_BC5E91CF[0].m_9B7A63EA')) if motion else None
                pose = (partial(mover.step_pose, horizontal_velocity=(vector.x, vector.z))
                        if vector is not None else None)
                mover.step(Command(frame, yaw=round(actor.yaw)), TICK, frame, simulate_pose=pose)
                if walls.pieces:
                    walls.move_mover(mover, before, tick=frame)
            actor.position = tuple(mover.position)
            actor.velocity = mover.velocity
            actor.external_flags = mover.flags
            actor.moved_tick = tick
            self.npcs[entity] = (actor, mover, tick)
            if (npc_specials.reaction_movement(actor) is None and not mover.flags_word & IN_AIR
                    and sum(v * v for v in mover.velocity) < .0001):
                self.npcs.pop(entity, None)
                actor.external_flags = 0
