"""Roadhog 01C8/01C9 authoritative reconstruction.

Ordinary graphs own input/windup/cooldown. This consumer bridges the missing
TrackTargets/PlayScript services at the real launch state, with resource-backed
range, sweep radius, speed, damage, minimum separation and recovery. Capsule
sweeps, horizontal relative destination and phase timing remain reconstructed;
native target filters, competing controls and defense services remain open.
"""

import math
from dataclasses import dataclass

from ow174.game import bots, mei, phase_defenses
from ow174.game.commands import Command
from ow174.game.mover import Mover, mover_data
from ow174.game.script import expr, graph, nodes
from ow174.game.symmetra_beam import cover


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

    def command(self, player, command):
        if player.hero.guid != 0x02E0000000000040 or command is None or self.controlled(player.body):
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
            move = definition.node(0)
            # These exact scalar expressions are retained by the extractor.
            # The full relative-transform evaluator remains a native-service gap.
            pull = move.field("m_speed")["m_expression"]["m_D99EF254"][0]
            offset = move.field("m_865F3D01")["m_configVars"][2]
            separation = offset["m_expression"]["m_D99EF254"][0]
            gap = offset["m_configVars"][0]["m_expression"]["m_D99EF254"][0]
            recovery = definition.node(7).field("m_timeout")["m_value"]
            limit = definition.node(5).field("m_timeout")["m_value"]
            direct = combat.hit_def(launch, {
                "m_modifyHealth": instance.graph.node(19).field("m_modifyHealth"),
            })
            reach = expr.to_float(instance.find_var(0, 1648).value())
            speed = expr.to_float(launch.evaluate(
                instance.graph.node(136).field("m_9B0385B4"), watch=False,
            ))
            radius = expr.to_float(launch.evaluate(instance.graph.node(17).field("m_radius"), watch=False))
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
            flight.target.owner.velocity = (0.0, 0.0, 0.0)
            flight.target.owner.moved_tick = self.combat.tick
        elif flight is not None and flight.player_driven:
            target = flight.target.owner
            if target.body == flight.target.entity:
                target.mover.velocity = (0.0, 0.0, 0.0)
                target.mover.driven = False

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

    def update(self, tick):
        if not self.flights:
            return
        from ow174.game import combat

        fight = self.combat
        targets = {t.entity: t for t in fight._targets()}
        for body, flight in list(self.flights.items()):
            if body not in self.flights:
                continue
            if (flight.owner.body != body or body not in targets
                    or fight.vitals_of(targets[body]).dead or self.controlled(body)
                    or fight.is_stunned(flight.owner, tick=tick) or fight.frozen(flight.owner)
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
                limit = best[0] if best is not None else step
                shield = min(cover(fight, flight.owner, origin, flight.direction, limit, tick),
                             default=None, key=lambda row: row[0])
                if shield is not None:
                    if shield[2] is not None:
                        shield[2](flight.damage.amount)
                    self.cancel_owner(body)
                    continue
                if best is None:
                    flight.travelled += step
                    if flight.travelled >= flight.reach:
                        self.cancel_owner(body)
                    continue
                travelled, target = best
                flight.travelled += travelled
                if mei.protected(target.owner):
                    self.cancel_owner(body)
                    continue
                flight.separation = max(flight.separation,
                                        targets[body].radius + target.radius + flight.gap)
                fight._damage(flight.owner, target, flight.damage.amount, False,
                              flight.damage.loadout, tick, world_tick=tick)
                if fight.vitals_of(target).dead or fight._fortify_state(target.owner) is not None:
                    self.cancel_owner(body)
                    continue
                flight.target, flight.caught_at = target, fight.now
                self.interrupt(target)
                if isinstance(flight.target.owner, bots.Bot):
                    bot = flight.target.owner
                    flight.npc_mover = Mover(
                        bot.position, round(bot.yaw), fight._collision(), mover_data(bot.body),
                    )
            target = targets.get(flight.target.entity)
            if (target is None or fight.vitals_of(target).dead or mei.protected(target.owner)
                    or phase_defenses.active(target.owner) or fight.now - flight.caught_at >= flight.limit):
                self.cancel_owner(body)
                continue
            # Maintain the link to the capsule center, with ordinary static occlusion.
            centre = (target.feet[0], target.feet[1] + target.height / 2, target.feet[2])
            delta = combat._sub(centre, fight._eye(flight.owner))
            length = math.sqrt(combat._dot(delta, delta))
            if length > 1e-9:
                direction = tuple(axis / length for axis in delta)
                shield = next(cover(
                    fight, flight.owner, fight._eye(flight.owner), direction,
                    max(0.0, length - target.radius), tick,
                ), None)
                if shield is not None:
                    self.cancel_owner(body)
                    continue
            destination = self.destination(flight)
            if flight.npc_mover is not None and elapsed > 0 and flight.arrived_at is None:
                mover = flight.npc_mover
                mover.step_pose(Command(tick), elapsed, tick, move_to=(destination, flight.pull_speed))
                flight.target.owner.position = tuple(mover.position)
                flight.target.owner.velocity = mover.velocity
            elif not isinstance(target.owner, bots.Bot):
                self.move_player(flight, target, destination, elapsed)
            feet = (flight.target.owner.position if flight.npc_mover is not None
                    else flight.target.owner.mover.position)
            if flight.arrived_at is None and math.dist(feet, destination) < 0.15:
                flight.arrived_at = fight.now
            if flight.arrived_at is not None and fight.now - flight.arrived_at >= flight.recovery:
                self.cancel_owner(body)

    def move_player(self, flight, target, destination, elapsed):
        fight = self.combat
        player = target.owner
        mover = player.mover
        flight.player_driven = True
        mover.throttles = (0, 0)
        if elapsed > 0 and flight.arrived_at is None:
            before = tuple(mover.position)
            mover.use(world=fight._collision())
            mover.driven = True
            mover._move_to(elapsed, destination, flight.pull_speed)
            fight.walls.move_player(player, before)
        if math.dist(mover.position, destination) < 0.15 or flight.arrived_at is not None:
            mover.velocity = (0.0, 0.0, 0.0)
            mover.driven = False
