"""Server field for Zarya's 025F volley and 03E2 deployable.

0260 supplies radius and duration. 0261 supplies the damage amount and tags. The
movement speed, target admission and ability gating are reconstructed policy.
"""

import math
from dataclasses import dataclass

from ow174.game import bots, heavy_assault, immunity, moira, orisa_fortify, reinhardt_charge, sigma_flux
from ow174.game.mover import Mover, mover_data
from ow174.game.script import graph

FIELD_ENTITY = 0x04000000000003E2
INPUT_LOCK = "graviton"
BOOSTERS_BUTTON = 0x08
DAMAGE_PER_SECOND = 5.0  # 0261/node2
PULL_SPEED = 3.0  # host interpretation of 0261/node15
LOADOUT = 0x0B9000000000005B
DAMAGE_TAGS = (
    0x0D8000000000025A, 0x0D80000000000990, 0x0D80000000002D60,
    0x0D80000000003FCE, 0x0D80000000004D29, 0x0D80000000004D61,
)  # 0261/node2 ModifyHealth


@dataclass
class Field:
    shooter: object
    source_body: int
    team: int
    position: tuple
    last_tick: int
    end_tick: int
    radius: float
    offense_scale: float = 1.0
    entity: int | None = None


class Gravitons:
    def __init__(self, fight):
        self.fight = fight
        self.fields: list[Field] = []
        self.pulled: set[int] = set()
        self.npc_movers: dict[int, Mover] = {}

    def clock_ticks(self, previous, tick, flights):
        ticks = {at for field in self.fields
                 for at in range(max(previous, field.last_tick) + 1, min(tick, field.end_tick) + 1)}
        if flights:
            duration = self.duration_ticks()
            for flight in flights:
                ticks.update(range(flight[0] + 1, min(tick, flight[-1] + duration) + 1))
        return ticks

    @staticmethod
    def duration_ticks():
        duration = float(graph.graph(0x0260).node(4).field("m_value.m_value"))
        return math.ceil(duration / 0.016)

    def landed(self, rocket, point, tick_seconds):
        definition = graph.graph(0x0260)
        if definition is None or getattr(rocket.shooter, 'gone', False) or self.fight.match.ended:
            return
        duration = definition.node(4).field('m_value.m_value')
        radius = definition.node(5).field('m_value.m_value')
        tick = self.fight.tick
        actor = self.fight.create_deployable(
            rocket.shooter, FIELD_ENTITY, "Graviton Surge", point,
            health=0, radius=0, height=0, seconds=duration, publish=False,
        )
        from ow174.game.match import TEAM_BIT

        actor.team = rocket.team
        actor.filter_bits = TEAM_BIT << rocket.team
        for viewer in self.fight.match.players:
            if viewer.spawned and viewer.client is not None:
                viewer.client.queue_entities([self.fight.deployable_create(actor)])
        self.fields.append(Field(rocket.shooter, rocket.source_body, rocket.team, point, tick,
                                 tick + math.ceil(duration / tick_seconds), radius,
                                 rocket.offense_scale if rocket.offense_scale is not None else 1.0,
                                 actor.entity))

    def _includes(self, field, target):
        from ow174.game.combat import segment_distance

        owner = target.owner
        if (self.fight.match.ended or getattr(field.shooter, "gone", False)
                or self.fight.replacement_ticks.get(target.entity, 0) > self.fight.tick
                or owner is field.shooter or target.entity == field.source_body
                or getattr(owner, 'gone', False)
                or self.fight.vitals_of(target).dead
                or (not self.fight.match.game_map.free_for_all and target.team == field.team)
                or moira.fading(owner) or self.fight.recalls.active(owner)
                or immunity.wraith_active(owner, target.entity) or immunity.cryo_active(owner, target.entity)):
            return False
        a, b = target.segment()
        if segment_distance(field.position, a, b) - target.radius > field.radius:
            return False
        point = (target.feet[0], target.feet[1] + target.eye, target.feet[2])
        delta = tuple(v - o for v, o in zip(point, field.position, strict=True))
        distance = math.sqrt(sum(v * v for v in delta))
        if distance <= 1e-9:
            return True
        wall = self.fight.cover_ray(field.position, tuple(v / distance for v in delta), distance)
        return wall is None or wall >= distance

    def includes(self, field, target, *, tick=None):
        return self._includes(field, target)

    def pulls(self, field, target, *, tick=None):
        at = self.fight.tick if tick is None else tick
        fortified = (immunity.fortified(target.owner, target.entity)
                     and not orisa_fortify.historical_pending(target.owner, at))
        return at < field.end_tick and not fortified and self.includes(field, target, tick=tick)

    def movement_for(self, actor):
        target = next((target for target in self.fight._targets() if target.owner is actor), None)
        if target is None:
            return None
        for field in self.fields:
            if self.pulls(field, target):
                x, y, z = field.position
                return (x, y - target.eye, z), PULL_SPEED
        return None

    def release_player(self, player):
        self.release(player)

    @staticmethod
    def _pull_horizontally(mover, destination, duration):
        x, _, z = destination
        velocity, fall, driven = mover.velocity, mover.fall, mover.driven
        mover._move_to(duration, (x, mover.position[1], z), PULL_SPEED)
        vx, _, vz = mover.velocity
        mover.velocity, mover.fall, mover.driven = velocity, fall, driven
        return vx, velocity[1], vz

    def update(self, tick, tick_seconds):
        if not self.fields and not self.pulled:
            return
        pulls = {}
        targets = self.fight._targets()
        for field in tuple(self.fields):
            if getattr(field.shooter, 'gone', False) or self.fight.match.ended:
                if field.entity is not None:
                    self.fight.destroy_deployable(field.entity)
                self.fields.remove(field)
                continue
            until = min(tick, field.end_tick)
            seconds = max(0, until - field.last_tick) * tick_seconds
            field.last_tick = max(field.last_tick, until)
            for target in targets:
                if not self._includes(field, target):
                    continue
                if seconds:
                    amount = self.fight._team_damage(field.shooter, DAMAGE_PER_SECOND * seconds * field.offense_scale)
                    amount *= self.fight.orisa.superchargers.scale(field.shooter)
                    self.fight._damage(field.shooter, target, amount,
                                       False, LOADOUT, tick, world_tick=tick,
                                       source_body=field.source_body, hit_tags=DAMAGE_TAGS,
                                       award_ult=False, armour_scalar=1.0, source_scaled=True)
                if self.fight.vitals_of(target).dead:
                    pulls.pop(target.entity, None)
                    continue
                if (self.pulls(field, target, tick=tick) and target.entity not in self.fight.pins.values()
                        and not reinhardt_charge.pinned(self.fight, target.entity)):
                    pulls.setdefault(target.entity, (target, field.position, seconds))
            if tick >= field.end_tick:
                if field.entity is not None:
                    self.fight.destroy_deployable(field.entity)
                ready = self.fight.zarya_charge_ready.get(field.source_body)
                if ready is not None:
                    self.fight.zarya_charge_ready[field.source_body] = max(ready, field.end_tick + 1)
                self.fields.remove(field)
        # Resolve all damage before movement, so a later field can kill a target
        # without leaving a pull queued for its retired body.
        for entity, (target, position, seconds) in pulls.items():
            if entity not in self.pulled:
                if isinstance(target.owner, bots.Bot):
                    script = target.owner.body_script
                    if script is not None:
                        heavy_assault.stop(script.component)
                else:
                    moira.clear_fade_press(target.owner)
                    from ow174.game import tracer

                    tracer.discard_mobility_input(target.owner)
            owner = target.owner
            self.fight.stop_boosters(owner)
            if not isinstance(owner, bots.Bot):
                self.fight.stop_genji_strike(owner)
                self.fight.winston_jump.cancel(target.entity)
                charge = self.fight.charges.get(target.entity)
                if charge is not None:
                    reinhardt_charge.stop(self.fight, target.entity, charge)
            if seconds:
                self._pull(target, position, seconds, tick)
        for target in targets:
            if target.entity in self.pulled and target.entity not in pulls:
                self.release(target.owner)
        self.pulled = set(pulls)
        self.npc_movers = {entity: mover for entity, mover in self.npc_movers.items()
                           if entity in pulls}

    def release(self, owner):
        npc = isinstance(owner, bots.Bot)
        entity = owner.entity if npc else owner.body
        if entity not in self.pulled:
            return
        self.pulled.remove(entity)
        if npc:
            previous = self.npc_movers.pop(entity, None)
            mover = owner.control_mover or owner.impulse_mover or previous
        else:
            mover = owner.mover
        if mover is not None:
            vertical = mover.velocity[1] if sigma_flux.has_effect(owner) else 0.0
            mover.velocity = (0.0, vertical, 0.0)
            mover.driven = False
            if npc:
                owner.velocity = mover.velocity
            self._request_pull_delivery(owner, mover, self.fight.tick)

    def _request_pull_delivery(self, owner, mover, tick):
        """Publish the existing pull/release pose without adding another force."""
        from ow174.game import correction
        from ow174.game.combat import TICK

        npc = isinstance(owner, bots.Bot)
        receiver = self.fight.knockbacks._controlled_receiver(owner) if npc else owner
        if receiver is None or receiver.body_script is None:
            return
        frame = self.fight.knockbacks.body_frame(receiver, tick, TICK)
        if frame is None:
            return
        current = (mover.last_command.frame if npc and mover.last_command is not None
                   else receiver.body_script.component.frame)
        entity = owner.entity if npc else owner.body
        correction.request_motion(receiver, first_frame=max(current + 1, frame + 1), body=entity)

    def _pull(self, target, position, seconds, tick):
        owner = target.owner
        npc = isinstance(owner, bots.Bot)
        if npc:
            mover = owner.control_mover or owner.impulse_mover or self.npc_movers.get(owner.entity)
            if mover is None:
                mover = Mover(owner.position, int(owner.yaw), self.fight._collision(),
                              owner.movement_data or mover_data(owner.body))
                owner.impulse_mover = mover
            self.npc_movers[owner.entity] = mover  # one alias to the existing movement owner
            if owner.control_mover is None:
                mover.position[:] = owner.position
        else:
            mover = owner.mover
        x, y, z = position
        before = tuple(mover.position)
        horizontal_velocity = None
        if sigma_flux.has_effect(owner):
            horizontal_velocity = self._pull_horizontally(mover, (x, y - target.eye, z), seconds)
        else:
            mover.driven = True
            mover._move_to(seconds, (x, y - target.eye, z), PULL_SPEED)
        if self.fight.walls.pieces:
            radius, segment = mover._capsule()
            after, normals = self.fight.walls.constrain(
                before, tuple(mover.position), radius, 2 * radius + segment + mover._pogo())
            if normals:
                self.fight.walls.place_mover(mover, after, normals)
        if npc:
            owner.position = tuple(mover.position)
            owner.velocity = horizontal_velocity if horizontal_velocity is not None else mover.velocity
            owner.moved_tick = tick
        self._request_pull_delivery(owner, mover, tick)
