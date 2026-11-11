"""Hosted Chain Hook using the 104319 launch and pull graphs."""

import math
from dataclasses import dataclass

from ow174.game import body_protection, bots, sigma_hyperspheres
from ow174.game.commands import Command
from ow174.game.mover import Mover, mover_data
from ow174.game.script import expr, graph, nodes

HERO = 0x02E0000000000040


@dataclass
class Flight:
    owner: object
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
    travelled: float = 0.0
    victim: object = None
    victim_body: int | None = None
    caught_at: float = 0.0
    arrived_at: float | None = None
    npc_mover: Mover | None = None


class Hooks:
    def __init__(self, fight):
        self.fight = fight
        self.flights = {}
        self.seen = {}
        self.input_anchors = {}
        self.input_cutoffs = {}

    def controlled(self, body):
        return any(flight.victim_body == body for flight in self.flights.values())

    def blocks_input(self, body, frame):
        return frame <= self.input_cutoffs.get(body, -1) or self.controlled(body)

    def note_input(self, body, frame, tick):
        if not self.blocks_input(body, frame):
            self.input_anchors[body] = frame, tick

    def cancel(self, body):
        flight = self.flights.pop(body, None)
        if flight is None or flight.victim is None:
            return
        if flight.npc_mover is not None:
            flight.victim.velocity = (0.0, 0.0, 0.0)
            flight.victim.moved_tick = self.fight.tick
        elif flight.victim.body == flight.victim_body:
            flight.victim.mover.velocity = (0.0, 0.0, 0.0)
            flight.victim.mover.driven = False

    def retire(self, body):
        for owner, flight in tuple(self.flights.items()):
            if body in (owner, flight.victim_body):
                self.cancel(owner)
        self.seen.pop(body, None)
        self.input_anchors.pop(body, None)
        self.input_cutoffs.pop(body, None)

    def command(self, player, command):
        from ow174.game import combat

        if player.hero.guid != HERO or command is None or self.fight.is_stunned(player):
            return
        instance = next((item for item in player.body_script.component.instances.values()
                         if item.graph.index == 0x01C8), None)
        if instance is None:
            return
        launch = next(
            (state for state in instance.states.values() if state.node.pos == 12 and state.active), None,
        )
        if launch is None or self.seen.get(player.body) == (instance.id, launch.counter):
            return
        self.seen[player.body] = instance.id, launch.counter
        definition = graph.graph(0x01C9)
        if definition is None:
            return
        move = definition.node(0)
        pull = move.field("m_speed")["m_expression"]["m_D99EF254"][0]
        offset = move.field("m_865F3D01")["m_configVars"][2]
        separation = offset["m_expression"]["m_D99EF254"][0]
        gap = offset["m_configVars"][0]["m_expression"]["m_D99EF254"][0]
        recovery = definition.node(7).field("m_timeout")["m_value"]
        limit = definition.node(5).field("m_timeout")["m_value"]
        direct = combat.hit_def(launch, {"m_modifyHealth": instance.graph.node(19).field("m_modifyHealth")})
        reach = expr.to_float(instance.find_var(0, 1648).value())
        speed = expr.to_float(launch.evaluate(instance.graph.node(136).field("m_9B0385B4"), watch=False))
        radius = expr.to_float(launch.evaluate(instance.graph.node(17).field("m_radius"), watch=False))
        if direct is None or min(reach, speed, pull, separation, limit) <= 0 or radius < 0:
            return
        self.cancel(player.body)
        self.flights[player.body] = Flight(
            player, instance, self.fight._eye(player), combat.aim(*command.aim()), reach,
            speed, radius, direct, pull, separation, gap, recovery, limit, self.fight.tick,
        )

    def _catch(self, body, flight, elapsed):
        from ow174.game import combat

        fight = self.fight
        ability = next((state for state in flight.instance.states.values() if state.node.pos == 11), None)
        if ability is None or not ability.flags & nodes.Ability.RUNNING:
            self.cancel(body)
            return
        step = min(flight.reach - flight.travelled, flight.speed * elapsed)
        origin = combat._along(flight.origin, flight.direction, flight.travelled)
        contacts = []
        for target in fight._enemies(flight.owner):
            if isinstance(target.owner, bots.Bot) and target.owner.spawner.get("manual_spawn") is not True:
                continue
            if self.controlled(target.entity) or fight.vitals_of(target).dead:
                continue
            distance = combat.ray_capsule(
                origin, flight.direction, *target.segment(), target.radius + flight.radius,
            )
            if distance is not None and distance <= step:
                contacts.append((distance, target.entity, target))
        distance, _, target = min(contacts, default=(step, 0, None))
        wall = combat.ray_world(fight._collision(), origin, flight.direction, distance)
        pillar = fight.walls.cast(origin, flight.direction, distance, radius=flight.radius)
        cover = min((hit for hit in (wall, pillar[0] if pillar else None) if hit is not None), default=None)
        shield = sigma_hyperspheres.barrier_contact(
            fight, flight.owner, origin, flight.direction, cover if cover is not None else distance,
            radius=flight.radius,
        )
        if shield is not None:
            _, damage, args, _ = shield
            damage(*args, flight.damage.amount)
        if cover is not None or shield is not None:
            self.cancel(body)
            return
        if target is None:
            flight.travelled += step
            if flight.travelled >= flight.reach:
                self.cancel(body)
            return
        dealt = fight._damage(flight.owner, target, flight.damage.amount, False, flight.damage.loadout,
                              fight.tick, world_tick=fight.tick, tags=flight.damage.tags)
        if not dealt or fight.vitals_of(target).dead or body_protection.fortify_blocks_control(target.owner):
            self.cancel(body)
            return
        fight._interrupt(target)
        flight.victim, flight.victim_body, flight.caught_at = target.owner, target.entity, fight.now
        combined = flight.owner.mover.data.stand_radius + target.radius + flight.gap
        flight.separation = max(flight.separation, combined)
        if isinstance(target.owner, bots.Bot):
            from ow174.game import npc_navigation

            bot = target.owner
            npc_navigation.stop(bot)
            bot.impulse_mover = None
            fight.clear_pins(bot.entity)
            flight.npc_mover = Mover(bot.position, round(bot.yaw), fight._collision(), mover_data(bot.body))
            return
        if target.entity not in self.input_anchors and target.owner.body_script is not None:
            self.input_anchors[target.entity] = target.owner.body_script.frame, fight.tick
        mover = target.owner.mover
        if mover.last_command is not None:
            mover.last_command = Command(mover.last_command.frame, yaw=mover.yaw, pitch=mover.pitch)

    def update(self, tick):
        from ow174.game import combat

        fight = self.fight
        targets = {target.entity: target for target in fight._targets()}
        for body, flight in tuple(self.flights.items()):
            if body not in self.flights:
                continue
            if (flight.owner.body != body or body not in targets
                    or fight.is_dead(flight.owner) or fight.is_stunned(flight.owner)):
                self.cancel(body)
                continue
            elapsed = (tick - flight.last_tick) * combat.TICK
            if elapsed <= 0:
                continue
            flight.last_tick = tick
            if flight.victim is None:
                self._catch(body, flight, elapsed)
            if flight.victim is not None:
                self._pull(body, flight, targets.get(flight.victim_body), elapsed)

    def _pull(self, body, flight, target, elapsed):
        from ow174.game import combat

        fight = self.fight
        if target is None or fight.vitals_of(target).dead or fight.now - flight.caught_at >= flight.limit:
            self.cancel(body)
            return
        if not fight.match.game_map.free_for_all and target.team == flight.owner.team:
            self.cancel(body)
            return
        mover = flight.npc_mover if flight.npc_mover is not None else target.owner.mover
        eye = fight._eye(flight.owner)
        if not combat.capsule_visible(
            fight._collision(), eye, *target.segment(), target.radius, walls=fight.walls,
        ):
            self.cancel(body)
            return
        tx, ty, tz = target.feet
        offset = (tx - eye[0], ty + target.height / 2 - eye[1], tz - eye[2])
        distance = math.hypot(*offset)
        if distance > target.radius and sigma_hyperspheres.barrier_contact(
            fight, flight.owner, eye, tuple(axis / distance for axis in offset), distance - target.radius,
        ) is not None:
            self.cancel(body)
            return
        # Keep the pre-pull clock anchor even when the client sends a late batch.
        anchor = self.input_anchors.get(target.entity)
        if anchor is not None:
            frame, world_tick = anchor
            elapsed_us = (fight.tick - world_tick) * round(combat.TICK * 1_000_000)
            cutoff = frame + elapsed_us // target.owner.quanta
            self.input_cutoffs[target.entity] = max(cutoff, self.input_cutoffs.get(target.entity, -1))
        x, y, z = flight.owner.mover.position
        dx, dz = mover.position[0] - x, mover.position[2] - z
        if math.hypot(dx, dz) < 1e-9:
            dx, dz = flight.direction[0], flight.direction[2]
        scale = flight.separation / max(math.hypot(dx, dz), 1e-9)
        destination = (x + dx * scale, y, z + dz * scale)
        if flight.arrived_at is None:
            before = tuple(mover.position)
            mover.use(world=fight._collision())
            mover.throttles = (0, 0)
            mover.driven = True
            mover._move_to(elapsed, destination, flight.pull_speed)
            if flight.npc_mover is not None:
                bot = target.owner
                if fight.walls.blocks_movement(before, mover.position, target.radius, target.height):
                    self.cancel(body)
                    return
                bot.position, bot.velocity = tuple(mover.position), mover.velocity
                bot.last_tick = bot.moved_tick = fight.tick
                bot.pace, bot.held, bot.keys = 0.0, (0, 0), False
            else:
                fight.walls.move_player(target.owner, before)
            poses = fight.history.get(target.entity)
            if poses and poses[-1][0] == fight.match.tick:
                tick, _, *pose = poses[-1]
                poses[-1] = tick, tuple(mover.position), *pose
            if math.dist(mover.position, destination) < 0.15:
                flight.arrived_at = fight.now
                mover.velocity = (0.0, 0.0, 0.0)
                if flight.npc_mover is not None:
                    target.owner.velocity = mover.velocity
        elif fight.now - flight.arrived_at >= flight.recovery:
            self.cancel(body)
