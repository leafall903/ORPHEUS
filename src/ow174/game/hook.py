"""Roadhog 01C8/01C9 authoritative reconstruction.

Ordinary graphs own input/windup/cooldown. This consumer bridges the missing
TrackTargets/PlayScript services at the real launch state, with resource-backed
range, sweep radius, speed, damage, minimum separation and recovery. Capsule
sweeps remain reconstructed; admitted victim VMs supply the original relative
MoveTo request. Native target filters, Clamp and client adoption remain open.
"""

import math
from collections import deque
from dataclasses import dataclass, replace

from ow174.game import bots, defenses
from ow174.game.commands import Command
from ow174.game.mover import Mover, mover_data
from ow174.game.script import expr, graph, nodes


def event_tick(script, time):
    """Map Roadhog's authored event milliseconds through its immutable body epoch."""
    from ow174.game.combat import TICK

    birth = getattr(script, "roadhog_birth", None)
    if birth is None:
        return None
    world, frame = birth
    return world + math.floor((time - frame * script.frame_ms) / (TICK * 1000))


def view_world_tick(script, command, quanta):
    """Sample world target history at Roadhog's reported view time, without rounding."""
    from ow174.game.combat import TICK

    world, owner = script.roadhog_birth
    elapsed_ms = (command.frame - owner) * script.frame_ms - command.view_delay(quanta) * 1000
    return world + elapsed_ms / round(TICK * 1000)


@dataclass
class Flight:
    owner: object
    body: int
    instance: object
    origin: tuple
    direction: tuple
    reach: float
    speed: float
    radius: float
    damage: object
    pull_speed: float
    separation: float
    gap: float
    recovery: float
    limit: float
    last_tick: int
    launch_tick: int
    latch_delay: float = 0.0
    travelled: float = 0.0
    target: object = None
    caught_at: float = 0.0
    arrived_at: float | None = None
    npc_mover: object = None
    player_driven: bool = False
    victim_instance: object = None
    contact_published: bool = False
    victim_clock: tuple | None = None


class Hooks:
    def __init__(self, combat):
        self.combat = combat
        self.flights = {}
        self.seen = {}
        self.player_pose_ticks = {}
        self.player_command_cutoffs = {}
        self.command_clocks = {}
        self.recipient_clocks = {}

    def observe_clocks(self, tick):
        """Record actual recipient VM cursors at the world Hook update phase."""
        from ow174.game.combat import HISTORY

        if not any(p.has_body and p.hero.guid == 0x02E0000000000040 for p in self.combat.match.players):
            self.recipient_clocks.clear()
            return
        for target in self.combat._targets():
            script = getattr(target.owner, 'body_script', None)
            if script is None:
                continue
            row = self.recipient_clocks.get(target.entity)
            if row is None or row[0] is not script:
                row = self.recipient_clocks[target.entity] = (script, deque(maxlen=HISTORY))
            clocks = row[1]
            if not clocks or tick > clocks[-1][0]:
                clocks.append((tick, script.component.frame, script.component.now))

    def recipient_clock(self, script, tick):
        if script is None:
            return None
        row = self.recipient_clocks.get(script.component.entity)
        if row is not None and row[0] is script:
            return next(((frame, now) for at, frame, now in reversed(row[1]) if at == tick), None)
        return None

    @staticmethod
    def finish_victim_move(flight, *, finished):
        victim = flight.victim_instance
        if victim is None:
            return
        component = victim.component
        move = victim.states.get(victim.graph.node(0).state)
        if component.instances.get(victim.id) is not victim or move is None or not move.active:
            return
        clock = flight.victim_clock or (component.frame, component.now)
        with component.observed_clock(*clock):
            if finished:
                move.request_finish()
            else:
                from ow174.game.script import runtime

                move.request(runtime.ABORT)
        component.advance_instance_until(victim, clock[1], clock[1])

    def observe_input(self, player, commands):
        actor = player.controlled_mech or player
        entity = actor.entity if isinstance(actor, bots.Bot) else player.body
        if (commands and player.has_body and player.body_script is not None
                and not self.controlled(entity) and not self.combat.is_stunned(actor)
                and commands[-1].frame > self.player_command_cutoffs.get(entity, -1)):
            self.command_clocks[entity] = commands[-1].frame, self.combat.match.tick

    def input(self, player, command):
        entity = player.controlled_mech.entity if player.controlled_mech is not None else player.body
        if self.controlled(entity) or command.frame <= self.player_command_cutoffs.get(entity, -1):
            return replace(command, forward=0, right=0, buttons=0, action=0)
        return command

    def track_cutoff(self, entity, quanta, tick):
        from ow174.game.combat import TICK

        clock = self.command_clocks.get(entity)
        if clock is not None:
            frame, received = clock
            elapsed_us = (tick - received) * round(TICK * 1_000_000)
            cutoff = frame + elapsed_us // quanta
            self.player_command_cutoffs[entity] = max(cutoff, self.player_command_cutoffs.get(entity, -1))

    def drive_allowed(self, entity, actor):
        fight = self.combat
        return (not fight.junkrat.rooted(entity, fight.tick)
                and not fight.recalls.active(actor) and not fight.orisa.controls(entity)
                and entity not in fight.zarya_gravitons.pulled
                and not fight.knockbacks.affecting(entity)
                and not fight.knockbacks.has_pending(entity))

    def command(self, player, command):
        if command is None or self.controlled(player.body):
            return
        from ow174.game import combat

        for instance in player.body_script.component.instances.values():
            if instance.graph.index != 0x01C8:
                continue
            launch = next((s for s in instance.states.values() if s.node.pos == 12 and s.active), None)
            if launch is None:
                continue
            key = (player.body, instance.id)
            if self.seen.get(key) == launch.counter:
                continue
            self.seen[key] = launch.counter
            definition = graph.graph(0x01C9)
            if definition is None:
                continue
            move = definition._by_pos[0]
            # These exact scalar expressions are retained by the extractor.
            # The full relative-transform evaluator remains a native-service gap.
            pull = move.field("m_speed")["m_expression"]["m_D99EF254"][0]
            offset = move.field("m_865F3D01")["m_configVars"][2]
            separation = offset["m_expression"]["m_D99EF254"][0]
            gap = offset["m_configVars"][0]["m_expression"]["m_D99EF254"][0]
            # Wait7 is the initial hit stun, preceding 50 -> 71 -> MoveTo0.
            # MoveTo's end sends C6, then Wait30 owns the release interval.
            latch_delay = definition._by_pos[7].field("m_timeout")["m_value"]
            recovery = definition._by_pos[30].field("m_timeout")["m_value"]
            limit = definition._by_pos[5].field("m_timeout")["m_value"]
            direct = combat.hit_def(launch, {
                "m_modifyHealth": instance.graph._by_pos[19].field("m_modifyHealth"),
            })
            reach = expr.to_float(instance.find_var(0, 1648).value())
            speed = expr.to_float(launch.evaluate(
                instance.graph._by_pos[136].field("m_9B0385B4"), watch=False,
            ))
            radius = expr.to_float(launch.evaluate(instance.graph._by_pos[17].field("m_radius"), watch=False))
            if direct is None or min(reach, speed, pull, separation, limit) <= 0 or radius < 0:
                continue
            launched = event_tick(player.body_script, launch.start)
            launched = self.combat.tick if launched is None else launched
            origin = instance.find_var(0, 10534, create=False)
            endpoint = instance.find_var(0, 1179, create=False)
            origin = origin.value() if origin is not None else None
            endpoint = endpoint.value() if endpoint is not None else None
            if not isinstance(origin, expr.Vec3) or not isinstance(endpoint, expr.Vec3):
                continue
            # Original190 captures eye + forward * .5 * scale. Chase136 starts
            # there and ends at134's endpoint; do not overwrite its inputs with
            # the host eye or extend the authored endpoint by another .5m.
            travel = math.dist(origin, endpoint)
            if travel <= 1e-9 or not all(map(math.isfinite, (*origin, *endpoint))):
                continue
            direction = tuple((b - a) / travel for a, b in zip(origin, endpoint, strict=True))
            self.flights[player.body] = Flight(
                player, player.body, instance, tuple(origin), direction,
                travel, speed, radius, direct, pull, separation, gap, recovery, limit,
                launched, launched,
                latch_delay=latch_delay,
            )
            flight = self.flights[player.body]
            # 01C8's client-only CosmeticEntity35 (03F7) and chain consume
            # the original synchronized inputs and the live ChaseVar binding.
            self.present(flight, flight.origin)
            for at in range(launched + 1, self.combat.tick + 1):
                if player.body not in self.flights:
                    break
                self.update(at, only_body=player.body)

    @staticmethod
    def present(flight, position):
        """Feed the authored hook/chain cosmetic through the existing body stream."""
        point = expr.Vec3(*position)
        instance = flight.instance
        chase = next((state for state in instance.states.values()
                      if state.node.pos == 136 and isinstance(state, nodes.ChaseVar)
                      and state.active and state.var is not None), None)
        if chase is not None:
            before = chase.var.value()
            chase.cur = point
            instance.changed(chase.var, before)
        else:
            instance.write((0, 431), point)

    @property
    def fight(self):
        return self.combat

    def release_target(self, entity):
        for body, flight in tuple(self.flights.items()):
            if flight.target is not None and flight.target.entity == entity:
                self.cancel_owner(body)

    def play_victim(self, world, instance, node):
        """Deliver 01C8's authored impact request to the selected victim VM."""
        if (world.hero != 0x02E0000000000040 or instance.graph.index != 0x01C8
                or node.pos != 14):
            return False
        flight = self.flights.get(world.body)
        if flight is None or flight.instance is not instance or flight.target is None:
            return True
        from ow174.game.script import play

        resource = play.resolve_resource(instance, node)
        if resource is None or resource.guid != 0x05800000000001C9:
            return True
        request = play.resolve_request(instance, node, resource,
            context_provider=lambda: play.InvocationContext(world.body, world.body, instance.source_entity()))
        script = flight.target.owner.body_script
        if (request is None or request.targets != (flight.target.entity,)
                or script is None or flight.victim_instance is not None):
            return True
        component = script.component
        if (component.entity != flight.target.entity or component.fault is not None or component.owner_dead
                or component.strict or getattr(component.world, "match", None) is not self.combat.match):
            return True
        clock = self.recipient_clock(script, flight.last_tick)
        if clock is None:
            clock = component.frame, component.now
        with component.observed_clock(*clock):
            flight.victim_instance = component.play_loaded_graph(
                graph.graph(0x01C9), source=request.context.caller, instigator=request.context.instigator,
                params=request.params, overrides=resource.overrides, policy=request.policy)
        if flight.victim_instance is not None:
            component.advance_instance_until(flight.victim_instance, clock[1], clock[1])
            flight.victim_clock = clock
        return True

    def victim_actor(self, component, entity):
        for flight in self.flights.values():
            victim = flight.victim_instance
            if victim is None or victim.component is not component:
                continue
            if entity == flight.body:
                return flight.owner
            if entity == flight.target.entity:
                return flight.target.owner
        return None

    def victim_position(self, component, entity):
        actor = self.victim_actor(component, entity)
        if actor is None:
            return NotImplemented
        return expr.Vec3(*(actor.position if isinstance(actor, bots.Bot) else actor.mover.position))

    def victim_message(self, world, target, message, params, sender):
        """Relay original01C9's arrival notification from its actual NPC victim."""
        if message != 0x02400000000000C6 or params:
            return False
        flight = self.flights.get(target)
        victim = flight.victim_instance if flight is not None else None
        if (victim is None or victim.component.world is not world
                or victim.component.entity != sender
                or victim.component.instances.get(victim.id) is not victim
                or flight.target.entity != sender):
            return False
        caster = flight.instance.component
        if (flight.owner.body != target or caster.owner_dead or caster.fault is not None
                or caster.instances.get(flight.instance.id) is not flight.instance):
            return False
        from ow174.game.script import runtime

        # The shared body-message queue deliberately excludes unowned Practice
        # NPCs. Keep this transport restricted to this exact live victim/caster.
        runtime.send_instance_message(flight.instance, message, params, sender)
        caster.run_now()
        return True

    def victim_native(self, instance, cfg):
        if instance.graph.index != 0x01C9:
            return NotImplemented
        from ow174.game.script import play

        entity = expr.to_entity(play.evaluate_scalar(instance, cfg.get("m_entity")))
        actor = self.victim_actor(instance.component, entity)
        if actor is None:
            return NotImplemented
        if cfg.get("$") == "STU_E72F6348":
            return any(target.entity == entity and not self.combat.vitals_of(target).dead
                       for target in self.combat._targets())
        if cfg.get("$") == "STU_71A87E92":
            return self.victim_position(instance.component, entity)
        if cfg.get("$") == "STU_21A13145":
            return expr.Vec3(*self.combat._eye(actor))
        if cfg.get("$") == "STU_FDBB0B67":
            mover = (actor.control_mover or actor.impulse_mover) if isinstance(actor, bots.Bot) else actor.mover
            return mover.body_scale if mover is not None else 1.0
        if cfg.get("$") == "STU_F7FD94F9":
            target = next((t for t in self.combat._targets() if t.entity == entity), None)
            return expr.f32(target.radius) if target is not None else expr.EVALUATION_FAILED
        if cfg.get("$") == "STU_021D01CD":
            from ow174.game.combat import aim

            yaw = actor.yaw if isinstance(actor, bots.Bot) else actor.mover.yaw
            return expr.Vec3(*aim(yaw, 0))
        if cfg.get("$") == "STU_588FF314":
            return self.pull_basis(instance, cfg, actor)
        return NotImplemented

    def caster_native(self, world, instance, cfg):
        if (instance.graph.index != 0x01C8 or world.hero != 0x02E0000000000040
                or cfg.get("$") not in ("STU_2200A66D", "STU_66F3B89E")):
            return NotImplemented
        from ow174.game.combat import aim
        from ow174.game.script import play

        entity = expr.to_entity(play.evaluate_scalar(instance, cfg.get("m_entity")))
        actor = next((p for p in self.combat.match.players if p.has_body and p.body == entity), None)
        if (entity != world.body or actor is None or actor.body_script is None
                or actor.body_script.component is not instance.component):
            return NotImplemented
        if cfg.get("$") == "STU_2200A66D":
            return expr.Vec3(*aim(actor.mover.yaw, actor.mover.pitch))
        if cfg.get("$") == "STU_66F3B89E":
            return expr.Vec3(*aim(actor.mover.yaw + 16384, 0))
        return NotImplemented

    def pull_basis(self, instance, cfg, actor):
        # Reuse SceneWorld's original CEB820-bound relative-basis interpretation.
        # This supplies a config query; the victim's original MoveTo owns motion.
        from ow174.game import props
        from ow174.game.script import play

        basis = cfg.get("m_64E06CA4") or {}
        if (basis.get("$") != "STU_0762B33A" or basis.get("m_E26E5E09") is not None
                or cfg.get("m_AEE599AA") != 0 or cfg.get("m_1FDD7084") is not None
                or cfg.get("m_hardPoint") is not None):
            return NotImplemented
        forward = play.evaluate_scalar(instance, basis.get("m_32B00625"))
        heading = expr._index(play.evaluate_scalar(instance, basis.get("m_3661BC45")), 0)
        limit = play.evaluate_scalar(instance, basis.get("m_5023B9B0"))
        if not isinstance(forward, expr.Vec3) or not isinstance(heading, expr.Vec3) or type(limit) not in (int, float):
            return expr.EVALUATION_FAILED
        yaw = math.atan2(heading.x, heading.z)
        desired = math.atan2(heading.y, math.hypot(heading.x, heading.z))
        current = math.atan2(forward.y, math.hypot(forward.x, forward.z))
        limit = math.radians(limit)
        pitch = max(desired - limit, min(current, desired + limit))
        direction = (math.sin(yaw) * math.cos(pitch), math.sin(pitch), math.cos(yaw) * math.cos(pitch))
        actor_yaw = actor.yaw if isinstance(actor, bots.Bot) else actor.mover.yaw
        half = actor_yaw * math.pi / 65536
        return expr.Vec3(*(expr.f32(a) for a in props.place(direction, (0, 0, 0), (1, 1, 1),
                                                          (0, -math.sin(half), 0, math.cos(half)))))

    def victim_move(self, flight):
        """Evaluate the active original MoveTo relative to its actual caster."""
        from ow174.game import props
        from ow174.game.script import play

        victim = flight.victim_instance
        if victim is None or victim.component.instances.get(victim.id) is not victim:
            return None
        move = victim.states.get(victim.graph.node(0).state)
        if not isinstance(move, nodes.MoveTo) or not move.active:
            return None
        entity = expr.to_entity(play.evaluate_scalar(victim, move.node.field("m_destinationEntity")))
        actor = self.victim_actor(victim.component, entity)
        if actor is None:
            return None
        offset = move.evaluate(move.node.field("m_865F3D01"), watch=False)
        speed = move.evaluate(move.node.field("m_speed"), watch=False)
        if (not isinstance(offset, expr.Vec3) or not all(map(math.isfinite, offset))
                or type(speed) not in (float, int) or not 0 < speed < math.inf):
            return None
        feet = actor.position if isinstance(actor, bots.Bot) else actor.mover.position
        yaw = actor.yaw if isinstance(actor, bots.Bot) else actor.mover.yaw
        half = yaw * math.pi / 65536
        destination = props.place(tuple(offset), tuple(feet), (1, 1, 1),
                                  (0, math.sin(half), 0, math.cos(half)))
        return destination, speed

    def forced_move(self, player):
        for flight in self.flights.values():
            if (flight.target is not None and flight.target.entity == player.body
                    and flight.arrived_at is None
                    and self.combat.now >= flight.caught_at + flight.latch_delay):
                return (self.victim_move(flight) if flight.victim_instance is not None
                        else (self.destination(flight), flight.pull_speed))
        return None

    def controlled(self, entity):
        return any(f.target is not None and f.target.entity == entity for f in self.flights.values())

    def cancel_owner(self, entity):
        """An interrupted caster releases its own flight/pull immediately."""
        flight = self.flights.pop(entity, None)
        if flight is not None:
            flight.instance.write((0, 375), ())
            if flight.contact_published and (flight.arrived_at is None or flight.victim_instance is None):
                from ow174.game.script import runtime

                # Interrupted pulls still release the caster's impact phase.
                # Normal arrival already sends original01C9/3's C6; repeating
                # it after recovery restarts the caster's authored .2s wait.
                runtime.send_instance_message(flight.instance, 0x02400000000000C6,
                                              {}, flight.target.entity)
        if flight is not None and flight.victim_instance is not None:
            victim = flight.victim_instance
            if victim.component.instances.get(victim.id) is victim:
                victim.component.destroy(victim)
        if (flight is not None and flight.npc_mover is not None
                and self.drive_allowed(flight.target.entity, flight.target.owner)):
            # No ordinary player command follows a manual NPC's forced pose.
            # Retire its velocity too, rather than leaving a moving wire pose.
            bot = flight.target.owner
            bot.velocity = flight.npc_mover.velocity = (0.0, 0.0, 0.0)
            if flight.npc_mover is not bot.control_mover:
                flight.npc_mover.last_command = Command(self.combat.tick, yaw=round(bot.yaw), pitch=round(bot.pitch))
            flight.npc_mover.driven = False
            bot.last_tick = bot.moved_tick = self.combat.tick
        elif (flight is not None and flight.player_driven
              and self.drive_allowed(flight.target.entity, flight.target.owner)
              and not flight.target.owner.mover.external_impulse):
            target = flight.target.owner
            if target.body == flight.target.entity:
                target.mover.velocity = (0.0, 0.0, 0.0)
                target.mover.driven = False

    def retire(self, entity):
        for body, flight in list(self.flights.items()):
            if body == entity or (flight.target is not None and flight.target.entity == entity):
                self.cancel_owner(body)
        self.seen = {key: value for key, value in self.seen.items() if key[0] != entity}
        self.player_pose_ticks.pop(entity, None)
        self.player_command_cutoffs.pop(entity, None)
        self.command_clocks.pop(entity, None)
        self.recipient_clocks.pop(entity, None)

    def destination(self, flight):
        owner = flight.owner.mover.position
        target = flight.target.owner
        feet = target.position if isinstance(target, bots.Bot) else target.mover.position
        dx, dz = feet[0] - owner[0], feet[2] - owner[2]
        length = math.hypot(dx, dz)
        if length < 1e-9:
            dx, dz, length = flight.direction[0], flight.direction[2], math.hypot(*flight.direction[::2])
        scale = flight.separation / max(length, 1e-9)
        return (owner[0] + dx * scale, owner[1], owner[2] + dz * scale)

    def interrupt(self, target):
        self.combat._interrupt(target)
        script = target.owner.body_script
        if script is None:
            return
        for instance in tuple(script.component.instances.values()):
            for state in tuple(instance.states.values()):
                if isinstance(state, nodes.Ability) and state.flags & nodes.Ability.RUNNING:
                    state.stop()
                elif isinstance(state, nodes.WeaponVolley) and state.active:
                    state.end(False)
        script.component.shots.clear()

    def update(self, tick=None, *, only_body=None):
        from ow174.game import combat

        fight = self.combat
        tick = fight.tick if tick is None else tick
        now = fight.now - (fight.tick - tick) * combat.TICK
        targets = {t.entity: t for t in fight._targets()}
        for body, flight in list(self.flights.items()):
            if only_body is not None and body != only_body:
                continue
            if tick < flight.launch_tick:
                continue
            if body not in self.flights:
                continue
            if (flight.owner.body != body or body not in targets
                    or fight.vitals_of(targets[body]).dead or self.controlled(body)
                    or fight.is_stunned(flight.owner, tick=tick)
                    or (tick - flight.launch_tick) * combat.TICK
                    > flight.reach / flight.speed + flight.latch_delay + flight.limit + flight.recovery):
                self.cancel_owner(body)
                continue
            elapsed = max(0, tick - flight.last_tick) * combat.TICK
            flight.last_tick = tick
            if flight.target is None:
                ability = next((s for s in flight.instance.states.values() if s.node.pos == 11), None)
                if ability is None or not ability.flags & nodes.Ability.RUNNING:
                    self.cancel_owner(body)
                    continue
                step = min(flight.reach - flight.travelled, flight.speed * elapsed)
                origin = combat._along(flight.origin, flight.direction, flight.travelled)
                best = None
                for target in fight._enemies(flight.owner):
                    if self.controlled(target.entity):
                        continue  # Competing-control priority is explicitly outside this first consumer.
                    a, b = target.segment()
                    distance = combat.ray_capsule(
                        origin, flight.direction, a, b, target.radius + flight.radius,
                    )
                    if distance is not None and distance <= step and (best is None or distance < best[0]):
                        best = (distance, target)
                wall = combat.ray_world(fight._collision(), origin, flight.direction, step)
                limit = min(best[0] if best else step, wall if wall is not None else step)
                shield = fight._barrier_contact(
                    flight.owner, origin, flight.direction, limit, flight.radius,
                )
                if shield is not None and (wall is None or shield[0] < wall):
                    fight._damage_contact_barrier(shield, flight.damage.amount, shooter=flight.owner)
                    self.cancel_owner(body)
                    continue
                if wall is not None and (best is None or wall < best[0]):
                    self.cancel_owner(body)
                    continue
                flight.travelled += step
                self.present(flight, combat._along(flight.origin, flight.direction, flight.travelled))
                if best is None:
                    if flight.travelled >= flight.reach:
                        self.cancel_owner(body)
                    continue
                flight.target, flight.caught_at = best[1], now
                if fight.damage_immune(flight.target.owner):
                    self.cancel_owner(body)
                    continue
                flight.separation = max(flight.separation,
                                        targets[body].radius + flight.target.radius + flight.gap)
                landed = fight._damage(flight.owner, flight.target, flight.damage.amount, False,
                              flight.damage.loadout, tick, world_tick=tick)
                if (not landed or fight.vitals_of(flight.target).dead or fight._control_immune(flight.target)
                        or (not isinstance(flight.target.owner, bots.Bot)
                            and flight.target.owner not in fight.match.players)):
                    self.cancel_owner(body)
                    continue
                # Original Hook overrides the selected Whole Hog external channel;
                # other shared physical owners retain their existing priority.
                fight.knockbacks.retire_whole_hog(flight.target.entity, forget=False)
                self.interrupt(flight.target)
                # Publish the actual contact through the original graph. This
                # advances its impact branch and authored victim PlayScript;
                # the host above remains the sole authoritative damage owner.
                flight.contact_published = True
                flight.instance.write((0, 375), (expr.Entity(flight.target.entity),))
                flight.instance.component.run_now()
                if isinstance(flight.target.owner, bots.Bot):
                    bot = flight.target.owner
                    from ow174.game.collision import MOVER, TEAM0, TEAM1

                    category = MOVER if fight.match.game_map.free_for_all or bot.team not in (0, 1) else (TEAM0, TEAM1)[bot.team]
                    flight.npc_mover = bot.control_mover or bot.ensure_manual_mover(tick, fight._collision(), category)
                    if flight.npc_mover is None:
                        flight.npc_mover = Mover(bot.position, round(bot.yaw), fight._collision(),
                                                mover_data(bot.body), category=category)
                    bot._pause(tick)
            target = targets.get(flight.target.entity)
            if (target is not None and (fight.damage_immune(target.owner)
                    or fight._control_immune(target)
                    or fight.barriers.for_target(target.owner) is not None
                    or (not fight.match.game_map.free_for_all and target.team == flight.owner.team))):
                self.cancel_owner(body)
                continue
            if (target is None or fight.vitals_of(target).dead
                    or now - flight.caught_at >= flight.latch_delay + flight.limit + flight.recovery):
                self.cancel_owner(body)
                continue
            # Maintain the link to the capsule center, with ordinary static occlusion.
            centre = (target.feet[0], target.feet[1] + target.height / 2, target.feet[2])
            self.present(flight, centre)
            delta = combat._sub(centre, fight._eye(flight.owner))
            length = math.sqrt(combat._dot(delta, delta))
            if length > 1e-9:
                direction = tuple(axis / length for axis in delta)
                wall = combat.ray_world(fight._collision(), fight._eye(flight.owner), direction,
                                        max(0.0, length - target.radius))
                shield = fight._barrier_contact(
                    flight.owner, fight._eye(flight.owner), direction, max(0.0, length - target.radius),
                )
                if wall is not None or shield is not None:
                    self.cancel_owner(body)
                    continue
            if flight.victim_instance is not None and flight.arrived_at is None:
                victim = flight.victim_instance
                clock = self.recipient_clock(target.owner.body_script, tick)
                if (clock is not None and flight.victim_clock is not None
                        and clock[1] >= flight.victim_clock[1]
                        and victim.component.instances.get(victim.id) is victim):
                    victim.component.advance_instance_until(victim, flight.victim_clock[1], clock[1])
                    flight.victim_clock = clock
                request = self.victim_move(flight)
                if request is None:
                    victim = flight.victim_instance
                    move = victim.states.get(victim.graph.node(0).state)
                    if (victim.component.instances.get(victim.id) is not victim
                            or (move is not None and (move.active or move.counter > 0))):
                        self.cancel_owner(body)  # Invalid/retired original motion cannot retain control.
                    continue  # The original victim VM, not a parallel timer, owns pull admission.
                destination, flight.pull_speed = request
            else:
                pull_at = flight.caught_at + flight.latch_delay
                if now <= pull_at:
                    continue
                elapsed = min(elapsed, now - pull_at)
                destination = self.destination(flight)
            outcome = None
            if (flight.npc_mover is not None and elapsed > 0 and flight.arrived_at is None
                    and self.drive_allowed(target.entity, target.owner)):
                mover = flight.npc_mover
                before = tuple(mover.position)
                if mover is target.owner.control_mover:
                    # The existing mech mover uses the owner's body-command epoch.
                    # World pull changes its capsule, never fabricates a body frame.
                    pilot = next((p for p in fight.match.players if p.controlled_mech is target.owner), None)
                    if pilot is not None:
                        self.track_cutoff(target.entity, pilot.quanta, tick)
                    mover.driven = True
                    outcome = mover._move_to(elapsed, destination, flight.pull_speed)
                else:
                    command = Command(tick, yaw=round(target.owner.yaw), pitch=round(target.owner.pitch))
                    ground = fight.walls.ground(mover) if fight.walls.pieces else None
                    outcome = mover.step_pose(command, elapsed, tick, move_to=(destination, flight.pull_speed), ground_override=ground)
                    mover.last_command = command
                if fight.walls.pieces:
                    fight.walls.move_mover(mover, before)
                target.owner.last_tick = target.owner.moved_tick = tick
                flight.target.owner.position = tuple(mover.position)
                flight.target.owner.velocity = mover.velocity
                history = fight.history.get(target.entity)
                if history:
                    latest = history[-1]
                    history[-1] = (latest[0], tuple(mover.position), *latest[2:])
            elif not isinstance(target.owner, bots.Bot):
                outcome = self.move_player(flight, target, destination, tick, elapsed)
            if outcome is False:
                self.finish_victim_move(flight, finished=False)
                self.cancel_owner(body)
                continue
            if flight.arrived_at is None and outcome is True:
                flight.arrived_at = now
            if flight.arrived_at is not None and flight.victim_instance is not None:
                self.finish_victim_move(flight, finished=True)
            if flight.arrived_at is not None and now - flight.arrived_at >= flight.recovery:
                self.cancel_owner(body)

    def move_player(self, flight, target, destination, tick, elapsed):
        from ow174.game import reinhardt_charge
        from ow174.game.combat import TICK

        fight = self.combat
        player = target.owner
        # Project from the pre-pull clock anchor. Late windows cannot re-anchor
        # their covered prefix to a post-release receipt tick.
        self.track_cutoff(target.entity, player.quanta, tick)
        if (not self.drive_allowed(target.entity, player)
                or player.mover.external_impulse
                or reinhardt_charge.pinned(fight, target.entity)):
            return
        mover = player.mover
        flight.player_driven = True
        self.player_pose_ticks[target.entity] = tick
        mover.throttles = (0, 0)
        outcome = None
        if elapsed > 0 and flight.arrived_at is None:
            before = tuple(mover.position)
            mover.use(world=fight._collision())
            mover.driven = True
            outcome = mover._move_to(elapsed, destination, flight.pull_speed)
            fight.walls.move_mover(mover, before)
            history = fight.history.get(target.entity)
            if history:
                latest = history[-1]
                history[-1] = (latest[0], tuple(mover.position), *latest[2:])
        if outcome is not None or flight.arrived_at is not None:
            mover.velocity = (0.0, 0.0, 0.0)
            mover.driven = False
        return outcome
