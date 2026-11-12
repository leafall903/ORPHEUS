"""Roadhog 01C8/01C9 authoritative reconstruction.

Ordinary graphs own input/windup/cooldown. This consumer bridges the missing
TrackTargets/PlayScript services at the real launch state, with resource-backed
range, sweep radius, speed, damage, minimum separation and recovery. Capsule
sweeps, horizontal relative destination and phase timing remain reconstructed;
native target filters, competing controls and defense services remain open.
"""

import math
from dataclasses import dataclass, replace

from ow174.game import bots, defenses
from ow174.game.commands import Command
from ow174.game.mover import Mover, mover_data
from ow174.game.script import expr, graph, nodes


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
    travelled: float = 0.0
    target: object = None
    caught_at: float = 0.0
    arrived_at: float | None = None
    npc_mover: object = None
    player_driven: bool = False


class Hooks:
    def __init__(self, combat):
        self.combat = combat
        self.flights = {}
        self.seen = {}
        self.player_pose_ticks = {}
        self.player_command_cutoffs = {}
        self.command_clocks = {}

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
            recovery = definition._by_pos[7].field("m_timeout")["m_value"]
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
            self.flights[player.body] = Flight(
                player, player.body, instance, self.combat._eye(player), combat.aim(*command.aim()),
                reach, speed, radius, direct, pull, separation, gap, recovery, limit,
                self.combat.tick, self.combat.tick,
            )
            flight = self.flights[player.body]
            endpoint = combat._along(flight.origin, flight.direction, flight.reach)
            # 01C8's client-only CosmeticEntity35 (03F7) and chain consume
            # these synchronized world positions. The missing native ray/hardpoint
            # queries otherwise leave 431 as scalar zero and terminate ChaseVar.
            instance.write((0, 1179), expr.Vec3(*endpoint))
            instance.write((0, 10534), expr.Vec3(*flight.origin))
            instance.write((0, 433), expr.Vec3(*flight.direction))
            self.present(flight, flight.origin)

    @staticmethod
    def present(flight, position):
        """Feed the authored hook/chain cosmetic through the existing body stream."""
        point = expr.Vec3(*position)
        flight.instance.write((0, 431), point)

    @property
    def fight(self):
        return self.combat

    def release_target(self, entity):
        for body, flight in tuple(self.flights.items()):
            if flight.target is not None and flight.target.entity == entity:
                self.cancel_owner(body)

    def forced_move(self, player):
        for flight in self.flights.values():
            if (flight.target is not None and flight.target.entity == player.body
                    and flight.arrived_at is None):
                return self.destination(flight), flight.pull_speed
        return None

    def controlled(self, entity):
        return any(f.target is not None and f.target.entity == entity for f in self.flights.values())

    def cancel_owner(self, entity):
        """An interrupted caster releases its own flight/pull immediately."""
        flight = self.flights.pop(entity, None)
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

    def update(self, tick=None):
        from ow174.game import combat

        fight = self.combat
        tick = fight.tick if tick is None else tick
        targets = {t.entity: t for t in fight._targets()}
        for body, flight in list(self.flights.items()):
            if body not in self.flights:
                continue
            if (flight.owner.body != body or body not in targets
                    or fight.vitals_of(targets[body]).dead or self.controlled(body)
                    or fight.is_stunned(flight.owner, tick=tick)
                    or (tick - flight.launch_tick) * combat.TICK
                    > flight.reach / flight.speed + flight.limit):
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
                flight.target, flight.caught_at = best[1], fight.now
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
                if isinstance(flight.target.owner, bots.Bot):
                    bot = flight.target.owner
                    from ow174.game.collision import MOVER, TEAM0, TEAM1

                    category = MOVER if fight.match.game_map.free_for_all or bot.team not in (0, 1) else (TEAM0, TEAM1)[bot.team]
                    flight.npc_mover = bot.control_mover or bot.ensure_manual_mover(tick, fight._collision(), category)
                    if flight.npc_mover is None:
                        flight.npc_mover = Mover(bot.position, round(bot.yaw), fight._collision(),
                                                mover_data(bot.body), category=category)
            target = targets.get(flight.target.entity)
            if (target is not None and (fight.damage_immune(target.owner)
                    or fight._control_immune(target)
                    or fight.barriers.for_target(target.owner) is not None
                    or (not fight.match.game_map.free_for_all and target.team == flight.owner.team))):
                self.cancel_owner(body)
                continue
            if target is None or fight.vitals_of(target).dead or fight.now - flight.caught_at >= flight.limit:
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
            destination = self.destination(flight)
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
                    mover._move_to(elapsed, destination, flight.pull_speed)
                else:
                    command = Command(tick, yaw=round(target.owner.yaw), pitch=round(target.owner.pitch))
                    ground = fight.walls.ground(mover) if fight.walls.pieces else None
                    mover.step_pose(command, elapsed, tick, move_to=(destination, flight.pull_speed), ground_override=ground)
                    mover.last_command = command
                if fight.walls.pieces:
                    fight.walls.move_mover(mover, before)
                target.owner.last_tick = target.owner.moved_tick = tick
                flight.target.owner.position = tuple(mover.position)
                flight.target.owner.velocity = mover.velocity
            elif not isinstance(target.owner, bots.Bot):
                self.move_player(flight, target, destination, tick, elapsed)
            feet = (flight.target.owner.position if flight.npc_mover is not None
                    else flight.target.owner.mover.position)
            if flight.arrived_at is None and math.dist(feet, destination) < 0.15:
                flight.arrived_at = fight.now
            if flight.arrived_at is not None and fight.now - flight.arrived_at >= flight.recovery:
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
        if elapsed > 0 and flight.arrived_at is None:
            before = tuple(mover.position)
            mover.use(world=fight._collision())
            mover.driven = True
            mover._move_to(elapsed, destination, flight.pull_speed)
            fight.walls.move_mover(mover, before)
            history = fight.history.get(target.entity)
            if history:
                latest = history[-1]
                history[-1] = (latest[0], tuple(mover.position), *latest[2:])
        if math.dist(mover.position, destination) < 0.15 or flight.arrived_at is not None:
            mover.velocity = (0.0, 0.0, 0.0)
            mover.driven = False
