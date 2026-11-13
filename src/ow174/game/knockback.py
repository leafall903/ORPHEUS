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
from ow174.game.mover import ADD, MULTIPLY, SET, Mover, f32, mover_data
from ow174.game.script import expr, nodes

# Host event namespace, not the key construction of native FF3471E9.
# Actual0263 instance/node keys cannot have this upper32 prefix.
REPLAY_LIMIT = 256
HOST_PREFIX = 0xFFFFFFFF00000000


def quick_melee_ring(state):
    """Capture 0043's single flat ring at activation."""
    if state.instance.graph.index != 0x0043 or state.node.pos != 10:
        return None
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
        self.launches: dict[int, int] = {}
        self.launch_sources: dict[int, int | None] = {}
        self.body_epochs: dict[int, tuple] = {}
        self.replayed_frames: dict[int, tuple] = {}
        self.whole_hog_seen = {}
        self.whole_hog_motion = {}

    def apply(self, target, impulse, *, impact_frame: int | None = None,
              impact_tick: int | None = None, source_entity: int | None = None,
              velocity_mode: int = 1, velocity_priority: float = 0.0,
              launch_modifiers: tuple = ()):
        """One force entry. impact_tick is world time; no source-clock conversion.

        impact_frame retains the old ambiguous contract for unmigrated callers.
        New producers must supply impact_tick and preserve HIT owner labels apart.
        Optional finite modifiers share the actual recipient launch frame,
        including delayed NPC admission; producers must not guess that clock.
        """
        from ow174.game import bots, correction
        from ow174.game.combat import TICK

        if type(velocity_mode) is not int or velocity_mode not in (1, 4):
            raise ValueError('unsupported external velocity mode')
        if not math.isfinite(velocity_priority):
            raise ValueError('finite velocity priority required')
        velocity_priority = f32(velocity_priority)
        launch_modifiers = tuple(tuple(row) for row in launch_modifiers)
        if any(len(row) != 5 or not all(math.isfinite(v) for v in row)
               or type(row[0]) is not int or not 0 <= row[0] <= 38
               or type(row[1]) is not int or row[1] not in (ADD, MULTIPLY, SET)
               or row[3] <= 0 for row in launch_modifiers):
            raise ValueError('invalid finite launch modifier')
        for row in launch_modifiers:
            f32(row[2])
            f32(row[4])
        if (not isinstance(impulse, expr.Vec3) or not all(map(math.isfinite, impulse))
                or not any(impulse) or self.combat.vitals_of(target).dead):
            return False
        owner = target.owner
        match = self.combat.match
        if impact_tick is not None and (type(impact_tick) is not int or impact_tick < 0):
            raise ValueError('impact_tick must be a nonnegative world tick')
        controller = self._controlled_receiver(owner) if isinstance(owner, bots.Bot) else owner
        controlled = controller is not None and controller is not owner
        if isinstance(owner, bots.Bot) and owner.control_mover is not None and not controlled:
            return False  # invalid possession must not fall back onto a second NPC mover
        if isinstance(owner, bots.Bot) and not controlled:
            if (owner not in match.bots or owner.entity != target.entity or owner.dead
                    or self.combat.junkrat.rooted(target.entity, max(match.tick, impact_frame or 0))):
                return False
            frame = max(match.tick, impact_frame or 0) + 1 if impact_tick is None else impact_tick + 1
        else:
            script = getattr(controller, 'body_script', None)
            if (controller not in match.players or not controller.has_body
                    or (not controlled and controller.body != target.entity)
                    or script is None or script.retired
                    or (not controlled and any(isinstance(state, nodes.MoveTo) and state.active
                           for instance in script.component.instances.values()
                           for state in instance.states.values()))):
                return False
            mover = owner.control_mover if controlled else owner.mover
            # The recipient may still have poses to replay before the attack.
            # If it already processed the impact pose, use its next one.
            if impact_tick is None:
                frame = max(script.component.frame + 1, match.tick, impact_frame or 0)
            else:
                mapped = self.body_frame(controller, impact_tick, TICK)
                if mapped is None:
                    return False  # no fabricated equal-epoch anchor
                current = (mover.last_command.frame if controlled and mover.last_command is not None
                           else script.component.frame)
                frame = max(current + 1, mapped + 1)
        if (not 0 <= frame <= 0xFFFFFFFF or self.serial >= 0xFFFFFFFF
                or any(frame + math.ceil(row[3] / TICK) > 0xFFFFFFFF for row in launch_modifiers)):
            return False
        self.retire_whole_hog(target.entity, forget=False)
        key = HOST_PREFIX | (self.serial + 1)
        if isinstance(owner, bots.Bot) and not controlled:
            if frame - 1 > match.tick:
                if not hasattr(self, "pending_npc"):
                    self.pending_npc = {}
                events = self.pending_npc.setdefault(owner.entity, [])
                events.append((frame - 1, key, impulse, velocity_mode, velocity_priority, launch_modifiers))
                events.sort()
            else:
                self._start_npc(owner, frame - 1, key, impulse, velocity_mode, velocity_priority, launch_modifiers)
        else:
            expires = max(match.tick, impact_tick if impact_tick is not None else match.tick)
            expires += (correction.IMPULSE_WINDOW_US - 1) // round(TICK * 1_000_000)
            if not correction.request_motion(controller, first_frame=frame, body=target.entity,
                                             expires_tick=expires):
                return False
            self._enqueue(target.entity, mover, frame, key, impulse, velocity_mode, velocity_priority, launch_modifiers)
            if not controlled:
                self.record_launch(target, source_entity=source_entity)
        self.serial += 1
        if isinstance(owner, bots.Bot):
            from ow174.game import body_script_requests

            body_script_requests.activate_npc(match, owner)
        return True

    def _controlled_receiver(self, bot):
        """Resolve the actual native local body; ordinary NPCs have no owner lease."""
        from ow174.game import correction

        for player in self.combat.match.players:
            if getattr(player, 'controlled_mech', None) is bot:
                current = correction._target(player)
                if current is not None and current[0] == bot.entity and current[1] is bot.control_mover:
                    return player
        return None

    def observe_body(self, player, script, tick):
        """Keep the actual constructor pair, without enabling an input lease."""
        self.body_epochs[player.body] = (script, (tick, script.frame), None)
        self.replayed_frames.pop(player.body, None)

    def observe_pose(self, player, frame, tick):
        """Record only a real physical pose; replay must not rewrite this pair."""
        script = player.body_script
        if script is None or script.retired:
            return
        previous = self.body_epochs.get(player.body)
        birth = previous[1] if previous is not None and previous[0] is script else None
        self.body_epochs[player.body] = (script, birth, (tick, frame))

    def forget_body(self, entity, script=None):
        previous = self.body_epochs.get(entity)
        if previous is not None and (script is None or previous[0] is script):
            self.body_epochs.pop(entity, None)
            self.launches.pop(entity, None)
            self.launch_sources.pop(entity, None)
            self.replayed_frames.pop(entity, None)

    def replayed_pose(self, player, frame):
        previous = self.replayed_frames.get(player.body)
        return previous is not None and previous[0] is player.body_script and frame <= previous[1]

    def body_frame(self, player, tick, dt):
        from ow174.game.sombra_replay import body_frame
        script = player.body_script
        if script is None:
            return None
        mapped = body_frame(script, tick, round(dt * 1000))
        if mapped is not None:
            return mapped
        previous = self.body_epochs.get(player.body)
        if previous is None or previous[0] is not script:
            return None
        pair = previous[2] if previous[2] is not None else previous[1]
        if pair is None:
            return None
        at, frame = pair
        return frame + (tick - at) * round(dt * 1000) // script.frame_ms

    def world_frame(self, player, frame, dt):
        """Inverse of the observed recipient clock; source labels stay separate."""
        from ow174.game.sombra_replay import world_frame
        script = player.body_script
        if script is None:
            return None
        mapped = world_frame(script, frame, round(dt * 1000))
        if mapped is not None:
            return mapped
        previous = self.body_epochs.get(player.body)
        if previous is None or previous[0] is not script:
            return None
        pair = previous[2] if previous[2] is not None else previous[1]
        if pair is None:
            return None
        at, owner = pair
        return at + (frame - owner) * script.frame_ms // round(dt * 1000)

    def record_launch(self, target, *, source_entity=None):
        """Retain an accepted player impulse on this same physical host."""
        from ow174.game import bots
        if not isinstance(target.owner, bots.Bot):
            self.launches[target.entity] = self.combat.tick
            source = source_entity if type(source_entity) is int and expr.valid_entity(source_entity) else None
            previous = self.launch_sources.get(target.entity)
            # ADD composes with the live force. A later unknown/self event cannot
            # revoke a still-unretired foreign victim's endpoint ownership.
            if previous is None or previous == target.entity:
                self.launch_sources[target.entity] = source

    def replay_until(self, previous):
        return previous + REPLAY_LIMIT if self.launches else previous

    def replay_launches(self, tick, dt, *, endpoint=False):
        """Original neutral missing-pose consumer, using declared epoch anchors."""
        from ow174.game.projectiles import MAX_LEASE_FRAME
        fight, match = self.combat, self.combat.match
        if match.ended or not self.launches:
            return
        targets = {target.entity: target for target in fight._targets()}
        scheduled = match.tick, fight.tick, fight.now
        try:
            match.tick = fight.tick = tick
            for entity, launched in tuple(self.launches.items()):
                if tick < launched:
                    continue
                target = targets.get(entity)
                if target is None or not match._shown(target.owner) or fight.vitals_of(target).dead:
                    self.launches.pop(entity, None)
                    self.launch_sources.pop(entity, None)
                    continue
                player = target.owner
                script = player.body_script
                if script is None or script.retired:
                    self.launches.pop(entity, None)
                    self.launch_sources.pop(entity, None)
                    continue
                if endpoint:
                    source = self.launch_sources.get(entity)
                    if source is None or source == entity:
                        continue
                    from ow174.game import pharah
                    own_pharah = script.hero == pharah.HERO and (
                        entity in fight.pharah_airborne or pharah.pending_release(script)
                        or pharah.hover_recovering(script))
                    if own_pharah:
                        continue  # Preserve an actually selected C68 action, not every Pharah body.
                frame = self.body_frame(player, tick, dt)
                if frame is None or frame > MAX_LEASE_FRAME:
                    continue  # No equal-epoch or stale receipt-frame assumption.
                mover = player.mover
                if (mover.last_command is None or mover.last_command.frame < frame) and script.frame < frame:
                    command = Command(frame, yaw=mover.yaw, pitch=mover.pitch)
                    mover.last_command = command
                    player._step_body_pose(command, dt, tick, replay_accepted=True)
                    self.replayed_frames[entity] = (script, frame)
                    history = fight.history.get(entity)
                    if history and history[-1][0] >= tick:
                        at, _, *shape = history[-1]
                        history[-1] = at, tuple(mover.position), *shape
                if not mover.airborne and not any(mover.velocity) and not mover.velocity_changes:
                    self.launches.pop(entity, None)
                    self.launch_sources.pop(entity, None)
        finally:
            match.tick, fight.tick, fight.now = scheduled

    def whole_hog_hit(self, target, direction, distance, spec, packet, *,
                      impact_tick, source_entity):
        """Submit one capped authored volley through the common recipient host.

        Damage remains per pellet. The legacy cap/ring interpretation is retained
        here as producer policy; native force/mass conversion is still unaccepted.
        """
        from ow174.game import defenses, reinhardt_charge, whole_hog

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
        from ow174.game import bots

        owner = target.owner
        mover = self.active.get(entity)
        if mover is None:
            mover = ((owner.control_mover or owner.impulse_mover) if isinstance(owner, bots.Bot)
                     else owner.mover)
        current = mover.velocity if mover is not None else owner.velocity
        impulse = whole_hog.volley_impulse(direction, distance, spec, current=current)
        if impulse is None:
            return False
        scale = defenses.knockback_scale(target.owner)
        impulse = expr.Vec3(*(axis * scale for axis in impulse))
        # Consume the volley even when resistance is zero or the receiver declines:
        # the other damaging pellets must not retry the physical submission.
        self.whole_hog_seen[key] = fight.tick
        return self.apply(target, impulse, impact_tick=impact_tick,
                          source_entity=source_entity)

    def whole_hog_velocity(self, entity):
        motion = self.whole_hog_motion.get(entity)
        return motion.velocity if motion is not None else (0.0, 0.0, 0.0)

    def retire_whole_hog(self, entity, *, forget=True):
        motion = self.whole_hog_motion.pop(entity, None)
        if motion is not None and motion.npc_mover is not None:
            owner = motion.owner
            if (owner.control_mover is motion.npc_mover
                    or owner.control_mover is None and owner.impulse_mover is motion.npc_mover):
                owner.velocity = motion.npc_mover.velocity
        if forget:
            self.whole_hog_seen = {key: tick for key, tick in self.whole_hog_seen.items()
                                   if key[-1] != entity}

    def _advance_whole_hog(self):
        """Original selected world damping, displacing the same owned capsule."""
        from ow174.game import bots, reinhardt_charge
        from ow174.game.mover import statescript_mods

        fight = self.combat
        tick = fight.tick
        targets = {target.entity: target for target in fight._targets()}
        for entity, motion in tuple(self.whole_hog_motion.items()):
            target = targets.get(entity)
            if (target is None or target.owner is not motion.owner or fight.vitals_of(target).dead
                    or fight._control_immune(target) or fight.hooks.controlled(entity)
                    or fight.junkrat.rooted(entity, tick) or entity in fight.pins.values()
                    or reinhardt_charge.pinned(fight, entity) or entity in self.active
                    or entity in fight.zarya_gravitons.pulled):
                self.retire_whole_hog(entity)
                continue
            if fight.orisa.controls(entity):
                self.retire_whole_hog(entity)
                continue  # Do not replay paused external slices after Halt releases.
            npc = isinstance(motion.owner, bots.Bot)
            controlled = npc and motion.owner.control_mover is motion.npc_mover
            if npc and ((motion.owner.control_mover is not None and not controlled)
                        or motion.owner.control_mover is None and motion.owner.impulse_mover is not motion.npc_mover):
                self.retire_whole_hog(entity)
                continue
            mover = motion.npc_mover if npc else motion.owner.mover
            if npc and tuple(mover.position) != tuple(motion.owner.position):
                mover.position = list(motion.owner.position)
                mover.body = None
            for at in range(motion.tick + 1, min(tick, motion.tick + 128) + 1):
                dt = .016
                motion.tick = at
                if npc and not controlled:
                    component = getattr(getattr(motion.owner, "body_script", None), "component", None)
                    mods = statescript_mods(component) if component is not None else {}
                    mover.track_mods(fight.movement_mods(motion.owner, mods, tick=at), at - 1)
                    command = Command(at, yaw=round(motion.owner.yaw), pitch=round(motion.owner.pitch))
                    ground = fight.walls.ground(mover) if fight.walls.pieces else None
                    mover.step_pose(command, dt, at, ground_override=ground)
                    mover.last_command = command
                constrain = fight.walls.constrain if fight.walls.pieces else None
                value = mover.displace(motion.velocity, dt, constrain=constrain)
                data = mover.values
                flat = math.hypot(value[0], value[2])
                remaining = max(0.0, flat - data.decel_ground_base * data.run_forward * dt)
                factor = remaining / flat if flat > 1e-9 else 0.0
                lift = max(0.0, value[1] - 10 * data.gravity_scale * dt)
                motion.velocity = (value[0] * factor, lift, value[2] * factor)
                if npc:
                    motion.owner.position = tuple(mover.position)
                    motion.owner.velocity = tuple(a + b for a, b in zip(mover.velocity, motion.velocity, strict=True))
                    motion.owner.last_tick = motion.owner.moved_tick = at
                if max(map(abs, motion.velocity)) < 1e-4 and (not npc or not mover.airborne):
                    self.retire_whole_hog(entity, forget=False)
                    break

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
            frame = next((event[0] + 1 for event in getattr(self, "pending_npc", {}).get(entity, ())
                          if event[1] == key), None)
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

    def _enqueue(self, entity, mover, frame, key, impulse, velocity_mode=1,
                 velocity_priority=0.0, launch_modifiers=()):
        from ow174.game.combat import TICK

        change = (velocity.projected(frame, tuple(impulse)) if velocity_mode == 4 else
                  velocity.vector(frame, ADD, tuple(impulse), mover.velocity))
        velocity.enqueue(mover.velocity_changes, replace(change, key=key, priority=velocity_priority))
        for index, op, value, seconds, priority in launch_modifiers:
            mover.add_timed_mod(index, op, value, frame, frame + math.ceil(seconds / TICK),
                                priority=priority, frame_clock=True)
        self.active[entity] = mover
        mover.external_impulse = True

    def _start_npc(self, bot, impact, key, impulse, velocity_mode=1,
                   velocity_priority=0.0, launch_modifiers=()):
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
        self._enqueue(bot.entity, mover, impact + 1, key, impulse, velocity_mode, velocity_priority, launch_modifiers)
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
                impact, key, impulse, velocity_mode, velocity_priority, launch_modifiers = events.pop(0)
                if last_impact != impact:
                    bot.step(impact, seconds, collision, **movement)
                self._start_npc(bot, impact, key, impulse, velocity_mode, velocity_priority, launch_modifiers)
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
        return entity in self.active or entity in self.whole_hog_motion

    def retire(self, entity, *, forget=True):
        self.launches.pop(entity, None)
        self.launch_sources.pop(entity, None)
        self.retire_whole_hog(entity, forget=forget)
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
        self._advance_whole_hog()
        self.whole_hog_seen = {key: tick for key, tick in self.whole_hog_seen.items()
                               if self.combat.tick - tick <= 128}
        match = self.combat.match
        live = {player.body: player.mover for player in match.players
                if player.has_body and not self.combat.is_dead(player)}
        npc_movers = {bot.entity: (bot.control_mover if self._controlled_receiver(bot) is not None
                                  else bot.impulse_mover) for bot in match.bots if not bot.dead}
        live.update(npc_movers)
        for entity, mover in tuple(self.active.items()):
            if (live.get(entity) is not mover or (not mover.velocity_changes
                    and ((entity not in npc_movers and mover.landed)
                         or (not mover.airborne and not any(mover.velocity))))):
                self.retire(entity, forget=live.get(entity) is not mover)
