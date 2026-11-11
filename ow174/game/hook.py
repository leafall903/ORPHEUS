"""Roadhog 01C8/01C9 authoritative reconstruction.

Ordinary graphs own input/windup/cooldown. This consumer bridges the missing
TrackTargets/PlayScript services at the real launch state, with resource-backed
range, sweep radius, speed, damage, minimum separation and recovery. Capsule
sweeps, horizontal relative destination and phase timing remain reconstructed;
native target filters, competing controls and defense services remain open.
"""

import math
from dataclasses import dataclass

from ow174.game import (
    barrier_pet, bots, defenses, orisa_deployable, sigma_barrier,
    sombra_hack, winston_barrier, zarya_barrier,
)
from ow174.game.collision import MOVER, TEAM0, TEAM1
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


class Hooks:
    def __init__(self, combat):
        self.combat = combat
        self.flights = {}
        self.seen = {}

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

    def controlled(self, entity):
        return any(f.target is not None and f.target.entity == entity for f in self.flights.values())

    def cancel_owner(self, entity):
        """An interrupted caster releases its own flight/pull immediately."""
        flight = self.flights.pop(entity, None)
        if flight is not None and flight.npc_mover is not None:
            # No ordinary player command follows a manual NPC's forced pose.
            # Retire its velocity too, rather than leaving a moving wire pose.
            bot = flight.target.owner
            bot.velocity = flight.npc_mover.velocity = (0.0, 0.0, 0.0)
            tick = self.combat.tick
            flight.npc_mover.last_command = Command(tick, yaw=round(bot.yaw), pitch=round(bot.pitch))
            bot.last_tick = bot.moved_tick = tick

    def retire(self, entity):
        for body, flight in list(self.flights.items()):
            if body == entity or (flight.target is not None and flight.target.entity == entity):
                self.cancel_owner(body)
        self.seen = {key: value for key, value in self.seen.items() if key[0] != entity}

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

    def forced_move(self, entity):
        for flight in self.flights.values():
            if flight.target is not None and flight.target.entity == entity:
                return self.destination(flight), flight.pull_speed if flight.arrived_at is None else 0.0
        return None

    def interrupt(self, target):
        self.combat._interrupt(target)
        script = getattr(target.owner, "body_script", None)
        if script is None:
            return
        for instance in tuple(script.component.instances.values()):
            for state in tuple(instance.states.values()):
                if isinstance(state, nodes.Ability) and state.flags & nodes.Ability.RUNNING:
                    state.stop()
                elif isinstance(state, nodes.WeaponVolley) and state.active:
                    state.end(False)
        script.component.shots.clear()

    def barrier(self, flight, origin, direction, reach):
        from ow174.game.combat import deployable_contact

        fight = self.combat
        body, team = flight.body, flight.owner.team
        enemies = fight._enemies(flight.owner)
        hostile = {target.entity for target in enemies}
        personal = tuple(bubble for bubble in zarya_barrier.active(fight) if bubble.body in hostile)
        deployed = (winston_barrier.colliders(fight, body, team)
                    + orisa_deployable.colliders(fight, body, team)
                    + sigma_barrier.colliders(fight, body, team) + personal)
        shield = barrier_pet.contact(barrier_pet.active_colliders(fight, enemies), origin, direction, reach)
        device = deployable_contact(deployed, origin, direction, reach)
        if device is not None and (shield is None or device[0] < shield[0]):
            return *device, None
        return shield

    def update(self, tick):
        from ow174.game import combat

        if not self.flights:
            return
        fight = self.combat
        targets = {t.entity: t for t in fight._targets()}
        for body, flight in list(self.flights.items()):
            if body not in self.flights:
                continue
            if (flight.owner.body != body or body not in targets
                    or fight.vitals_of(targets[body]).dead
                    or fight.is_incapacitated(flight.owner, tick=tick)
                    or fight.is_pinned(flight.owner) or sombra_hack.active(fight, body, tick)
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
                ice = fight.walls.cast(origin, flight.direction, limit)
                if ice is not None:
                    wall = ice[0]
                    limit = wall
                shield = self.barrier(flight, origin, flight.direction, limit)
                if shield is not None and (wall is None or shield[0] < wall):
                    distance, device, pet = shield
                    if pet is not None:
                        barrier_pet.damage(device, pet, flight.damage.amount,
                                           combat._along(origin, flight.direction, distance))
                    elif isinstance(device, zarya_barrier.Bubble):
                        device.damage(flight.damage.amount, body)
                    else:
                        device.damage(flight.damage.amount)
                    self.cancel_owner(body)
                    continue
                if wall is not None and (best is None or wall <= best[0]):
                    if ice is not None:
                        fight.walls.damage(ice[1], flight.owner, flight.damage.amount, tick)
                    self.cancel_owner(body)
                    continue
                flight.travelled += step
                if best is None:
                    if flight.travelled >= flight.reach:
                        self.cancel_owner(body)
                    continue
                flight.target, flight.caught_at = best[1], fight.now
                if defenses.phase(flight.target.owner)[0]:
                    self.cancel_owner(body)
                    continue
                flight.separation = max(flight.separation,
                                        targets[body].radius + flight.target.radius + flight.gap)
                fight._damage(flight.owner, flight.target, flight.damage.amount, False,
                              flight.damage.loadout, tick, world_tick=tick)
                if fight.vitals_of(flight.target).dead or defenses.fortified(flight.target.owner):
                    self.cancel_owner(body)
                    continue
                self.interrupt(flight.target)
                if isinstance(flight.target.owner, bots.Bot):
                    bot = flight.target.owner
                    category = MOVER
                    if not fight.match.game_map.free_for_all and bot.team in (0, 1):
                        category = (TEAM0, TEAM1)[bot.team]
                    scene = fight.match.collision_world
                    flight.npc_mover = bot.ensure_manual_mover(tick, scene, category)
                    if flight.npc_mover is None:
                        flight.npc_mover = Mover(
                            bot.position, round(bot.yaw), scene, mover_data(bot.body), category=category,
                        )
            target = targets.get(flight.target.entity)
            if (target is None or fight.vitals_of(target).dead or defenses.phase(target.owner)[0]
                    or fight.now - flight.caught_at >= flight.limit):
                self.cancel_owner(body)
                continue
            # Maintain the link to the capsule center, with ordinary static occlusion.
            centre = (target.feet[0], target.feet[1] + target.height / 2, target.feet[2])
            delta = combat._sub(centre, fight._eye(flight.owner))
            length = math.sqrt(combat._dot(delta, delta))
            if length > 1e-9:
                direction = tuple(axis / length for axis in delta)
                wall = combat.ray_world(fight._collision(), fight._eye(flight.owner), direction,
                                        max(0.0, length - target.radius))
                origin = fight._eye(flight.owner)
                limit = max(0.0, length - target.radius)
                if (wall is not None or fight.walls.cast(origin, direction, limit) is not None
                        or self.barrier(flight, origin, direction, limit) is not None):
                    self.cancel_owner(body)
                    continue
            destination = self.destination(flight)
            if flight.npc_mover is not None and elapsed > 0 and flight.arrived_at is None:
                mover = flight.npc_mover
                bot = flight.target.owner
                before = tuple(mover.position)
                ground = fight.walls.ground(mover) if fight.walls.pieces else None
                command = Command(tick, yaw=round(bot.yaw), pitch=round(bot.pitch))
                mover.step_pose(command, elapsed, tick, move_to=(destination, flight.pull_speed),
                                ground_override=ground)
                fight.walls.move_mover(mover, before)
                mover.last_command = command
                bot.position, bot.velocity = tuple(mover.position), mover.velocity
                bot.held = (0, 0)
                bot.last_tick = bot.moved_tick = tick
            feet = (flight.target.owner.position if flight.npc_mover is not None
                    else flight.target.owner.mover.position)
            if flight.arrived_at is None and math.dist(feet, destination) < 0.15:
                flight.arrived_at = fight.now
            if flight.arrived_at is not None and fight.now - flight.arrived_at >= flight.recovery:
                self.cancel_owner(body)
