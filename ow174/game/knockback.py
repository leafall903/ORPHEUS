"""Unresisted external-impulse host reconstruction, not native damage routing.

The authored vector and one-frame mover arithmetic have native evidence.
Target clocks, unresisted eligibility and NPC physics/control handoff here are
server policies. Native resistance, immunity and world-target arbitration remain
unaccepted; active MoveTo owners are explicitly unsupported.
"""

import math
from dataclasses import replace

from ow174.game import velocity
from ow174.game.commands import Command
from ow174.game.collision import MOVER, TEAM0, TEAM1
from ow174.game.mover import ADD, Mover, mover_data
from ow174.game.script import expr, nodes

# Host event namespace, not the key construction of native FF3471E9.
# Actual0263 instance/node keys cannot have this upper32 prefix.
HOST_PREFIX = 0xFFFFFFFF00000000


def quick_melee_ring(state):
    """Capture 0043's single flat ring at activation."""
    modify = state.node.field("m_modifyHealth") or {}
    rings = modify.get("m_knockbackRings") or ()
    if len(rings) != 1:
        return None
    ring, = rings

    def value(fields, key):
        return expr.to_float(state.evaluate(fields.get(key), watch=False))

    radius = value(ring, "m_radius")
    if not math.isfinite(radius) or not 0 < radius:
        return None
    horizontal = value(modify, "m_E33EFEE1") * value(ring, "m_579D7CFF")
    upward = value(modify, "m_10399AE7") * value(ring, "m_10399AE7")
    return radius, horizontal, upward


def quick_melee_impulse(ring, source, target, direction):
    """Apply the captured ring using horizontal separation in metres."""
    if ring is None:
        return None
    radius, horizontal, upward = ring
    dx, dz = target.feet[0] - source[0], target.feet[2] - source[2]
    distance = math.hypot(dx, dz)
    if distance > radius:
        return None
    if distance == 0:
        dx, _, dz = direction
        distance = math.hypot(dx, dz)
    scale = horizontal / distance if distance else 0.0
    return expr.Vec3(dx * scale, upward, dz * scale)


class Knockbacks:
    def __init__(self, combat):
        self.combat = combat
        self.serial = 0
        self.active: dict[int, Mover] = {}
        self.whole_hog_seen = {}

    def apply(self, target, impulse, *, impact_frame: int | None = None):
        from ow174.game import bots

        if (not isinstance(impulse, expr.Vec3) or not all(map(math.isfinite, impulse))
                or not any(impulse) or self.combat.vitals_of(target).dead):
            return False
        owner = target.owner
        match = self.combat.match
        if isinstance(owner, bots.Bot):
            if (owner not in match.bots or owner.entity != target.entity or owner.dead
                    or self.combat.junkrat.rooted(target.entity, max(match.tick, impact_frame or 0))):
                return False
            frame = max(match.tick, impact_frame or 0) + 1
        else:
            script = getattr(owner, 'body_script', None)
            if (owner not in match.players or not owner.has_body or owner.body != target.entity
                    or script is None or script.retired
                    or any(isinstance(state, nodes.MoveTo) and state.active
                           for instance in script.component.instances.values()
                           for state in instance.states.values())):
                return False
            mover = owner.mover
            # The recipient may still have poses to replay before the attack.
            # If it already processed the impact pose, use its next one.
            frame = max(script.component.frame + 1, match.tick, impact_frame or 0)
        if not 0 <= frame <= 0xFFFFFFFF or self.serial >= 0xFFFFFFFF:
            return False
        key = HOST_PREFIX | (self.serial + 1)
        if isinstance(owner, bots.Bot):
            if frame - 1 > match.tick:
                if not hasattr(self, "pending_npc"):
                    self.pending_npc = {}
                events = self.pending_npc.setdefault(owner.entity, [])
                events.append((frame - 1, key, impulse))
                events.sort()
            else:
                self._start_npc(owner, frame - 1, key, impulse)
        else:
            self._enqueue(target.entity, mover, frame, key, impulse)
        self.serial += 1
        return True

    def whole_hog_hit(self, target, direction, distance, spec, packet):
        """Original per-volley force, sharing the sole velocity/mover queue."""
        from ow174.game import bots, reinhardt_charge

        fight = self.combat
        entity = target.entity
        if (spec is None or packet is None or fight.vitals_of(target).dead
                or fight._control_immune(target) or fight.hooks.controlled(entity)
                or fight.junkrat.rooted(entity, fight.tick)
                or entity in fight.pins.values() or reinhardt_charge.pinned(fight, entity)
                or fight.orisa.controls(entity) or entity in fight.zarya_gravitons.pulled):
            return False
        key = (*packet, entity)
        if key in self.whole_hog_seen:
            return False
        size = math.hypot(direction[0], direction[2])
        if size <= 1e-9:
            return False
        horizontal, vertical, cap = spec.scale(distance)
        owner = target.owner
        if isinstance(owner, bots.Bot):
            # This source scope is the ordinary NPC impulse channel. Owned
            # mechs retain their distinct body-clock movement path.
            if owner.control_mover is not None:
                return False
            mover = owner.impulse_mover
            frame = max(fight.match.tick, fight.tick) + 1
        else:
            mover = owner.mover
            frame = max(owner.body_script.component.frame + 1, fight.match.tick, fight.tick)
        current = (0.0, 0.0, 0.0) if mover is None else tuple(mover.velocity)
        if mover is not None:
            current = velocity.consume(current, mover.values.jump_speed,
                                       mover.velocity_changes, frame)
        x, y, z = current
        x += direction[0] / size * spec.force * horizontal
        z += direction[2] / size * spec.force * horizontal
        flat = math.hypot(x, z)
        limit = spec.cap * cap
        if flat > limit:
            x, z = x / flat * limit, z / flat * limit
        y = min(spec.lift_cap * cap, y + spec.lift * vertical)
        delta = expr.Vec3(*(expr.f32(after - before)
                           for before, after in zip(current, (x, y, z))))
        if not self.apply(target, delta, impact_frame=fight.tick):
            return False
        self.whole_hog_seen[key] = fight.tick
        return True

    def schedule_victim(self, target, source, instigator):
        """Retain the original uppercut script delivery on this same movement host."""
        from ow174.game.combat import _source_entity

        entity = _source_entity(target.owner)
        mover = self.active.get(entity)
        if target.entity != entity:
            return
        key = HOST_PREFIX | self.serial
        frame = next((row.frame for row in mover.velocity_changes if row.key == key), None) if mover else None
        if frame is None:
            frame = next((impact + 1 for impact, event_key, _impulse in getattr(self, "pending_npc", {}).get(entity, ())
                          if event_key == key), None)
        if frame is None:
            # Fully resisted source launch still delivers its authored victim graph.
            if self.serial >= 0xFFFFFFFF:
                return
            self.serial += 1
            key = HOST_PREFIX | self.serial
            frame = max(self.combat.match.tick, self.combat.tick) + 1
        if not hasattr(self, "pending_victims"):
            self.pending_victims = {}
        self.pending_victims[entity] = (frame, key, source, instigator)

    def prepare_victim(self, owner, pose):
        from ow174.game import bob_victim
        from ow174.game.combat import _source_entity
        from ow174.game.mover import SET, statescript_mods
        from ow174.game.script import graph

        entity = _source_entity(owner)
        pending = getattr(self, "pending_victims", {})
        event = pending.get(entity)
        if event is None or pose < event[0] - 1:
            return
        mover = self.active.get(entity)
        if mover is None and entity in getattr(self, "pending_npc", {}):
            return  # Its existing queued launch has not created the shared mover yet.
        frame, key, source, instigator = pending.pop(entity)
        if not pending and hasattr(self, "pending_victims"):
            del self.pending_victims
        if bob_victim.start(self.combat, source, owner, graph.graph(0x1537), instigator=instigator):
            if mover is None:
                # No motion pool is created for a fully resisted NPC request.
                from ow174.game.bots import Bot
                if isinstance(owner, Bot):
                    owner.velocity = (0.0, 0.0, 0.0)
                return
            # Scalar reset executes before the already queued additive launch.
            change = velocity.numeric(frame, 0, SET, 0.0)
            velocity.enqueue(mover.velocity_changes, replace(change, key=key))
            script = owner.body_script
            mover.track_mods(self.combat.movement_mods(owner, statescript_mods(script.component)), script.component.frame)

    def _enqueue(self, entity, mover, frame, key, impulse):
        change = velocity.vector(frame, ADD, tuple(impulse), mover.velocity)
        velocity.enqueue(mover.velocity_changes, replace(change, key=key))
        self.active[entity] = mover
        mover.external_impulse = True

    def _start_npc(self, bot, impact, key, impulse):
        mover = bot.impulse_mover
        if mover is None:
            from ow174.game import npc_navigation

            if bot.navigation is not None:
                npc_navigation.stop(bot)
            match = self.combat.match
            category = MOVER if match.game_map.free_for_all or bot.team not in (0, 1) else (TEAM0, TEAM1)[bot.team]
            mover = Mover(bot.position, round(bot.yaw), match.collision_world,
                          mover_data(bot.body), category=category)
            mover.velocity = tuple(bot.velocity)
            mover.last_command = Command(impact, yaw=mover.yaw)
        self._enqueue(bot.entity, mover, impact + 1, key, impulse)
        bot.impulse_mover = mover
        bot.last_tick = impact

    def step_npc(self, bot, tick, seconds, collision, **movement):
        self.prepare_victim(bot, tick)
        pending = getattr(self, "pending_npc", None)
        events = pending.get(bot.entity) if pending is not None else None
        last_impact = None
        if events and bot.dead:
            self._discard_pending_npc(bot.entity)
            return
        if events:
            while events and events[0][0] <= tick:
                impact, key, impulse = events.pop(0)
                if last_impact != impact:
                    bot.step(impact, seconds, collision, **movement)
                self._start_npc(bot, impact, key, impulse)
                self.prepare_victim(bot, tick)
                last_impact = impact
            if not events:
                self._discard_pending_npc(bot.entity)
        if last_impact != tick:
            bot.step(tick, seconds, collision, **movement)

    def _discard_pending_npc(self, entity):
        pending = getattr(self, "pending_npc", None)
        if pending is not None:
            pending.pop(entity, None)
            if not pending:
                del self.pending_npc

    def has_pending(self, entity):
        return entity in getattr(self, "pending_npc", {})

    def affecting(self, entity):
        return entity in self.active

    def retire(self, entity, *, forget=True):
        if forget:
            self.whole_hog_seen = {key: tick for key, tick in self.whole_hog_seen.items()
                                   if key[-1] != entity}
        pending = getattr(self, "pending_victims", None)
        if pending is not None:
            pending.pop(entity, None)
            if not pending:
                del self.pending_victims
        self._discard_pending_npc(entity)
        mover = self.active.pop(entity, None)
        if mover is not None:
            mover.velocity_changes[:] = [row for row in mover.velocity_changes
                                         if row.key is None or row.key & HOST_PREFIX != HOST_PREFIX]

    def update(self):
        self.whole_hog_seen = {key: tick for key, tick in self.whole_hog_seen.items()
                               if self.combat.tick - tick <= 128}
        match = self.combat.match
        live = {player.body: player.mover for player in match.players
                if player.has_body and not self.combat.is_dead(player)}
        npc_movers = {bot.entity: bot.impulse_mover for bot in match.bots if not bot.dead}
        live.update(npc_movers)
        for entity, mover in tuple(self.active.items()):
            if (live.get(entity) is not mover or (not mover.velocity_changes
                    and ((entity not in npc_movers and mover.landed)
                         or (not mover.airborne and not any(mover.velocity))))):
                self.retire(entity, forget=live.get(entity) is not mover)
