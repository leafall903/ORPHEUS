"""Reconstructed Orisa engine consumers; authored body graphs still own input/cooldowns.

Target acquisition is still reconstructed. Original0CCC configured visibility
paths use explicit root-yaw/static-ray services, with a reconstructed fallback.
Actual0CCE owns startup, MoveTo and post-slow. Native outer geometry/filter and
query lifecycle, separate PhysicsExplosion and full status dispatch remain open.
"""

import math
from dataclasses import dataclass

from ow174.game import body_protection, bots, orisa_halt, tracer, zarya_barrier
from ow174.game.script import expr, runtime

HALT_GRAPH = 0x0CCB
PROJECTILE = 0x040000000000172C
DETONATE = 0x024000000000003F
RADIUS = 5.0
ORB_END = 0.5
TARGET_UP = 1.3  # 0CCC TrackTargets visibility endpoints
TICK = 0.016


def _protected(owner):
    return (tracer.recalling(owner) or body_protection.active(owner)
            or body_protection.fortify_blocks_control(owner) or zarya_barrier.active(owner) is not None)


@dataclass
class Orb:
    entity: int
    rocket: object
    requested: int | None = None
    pulled: bool = False


@dataclass
class Pull:
    entity: int
    owner: object
    until: int
    source_team: int
    script: runtime.Instance


class Halt:
    def __init__(self, fight):
        self.fight = fight
        self.orbs: dict[int, Orb] = {}
        self.pulls: dict[int, Pull] = {}

    @staticmethod
    def can_launch(state):
        return (state.instance.graph.index == HALT_GRAPH and state.index == 4
                and orisa_halt.control_graph() is not None and orisa_halt.query_graph() is not None)

    def admit(self, rocket, state) -> None:
        entity = self.fight.next_bot
        if not bots.BOT_ENTITY <= entity < 0xD0000000:
            raise ValueError("ability entity allocator exhausted")
        self.fight.next_bot += 1  # shared monotonic NPC/dynamic-body allocator
        rocket.halt_entity = entity
        self.orbs[entity] = Orb(entity, rocket)
        output = expr.lvalue(state.node.field("m_505472EF"))
        state.instance.write(output, expr.Entity(entity))

    def receive(self, target: int, message: int, sender: int) -> bool:
        orb = self.orbs.get(target)
        if orb is None or message != DETONATE:
            return False
        rocket = orb.rocket
        # Only the live casting body can request detonation.
        if sender != rocket.halt_body or rocket.shooter.body != sender:
            return False
        vitals = self.fight.vitals.get(sender)
        if vitals is not None and vitals.dead:
            return False
        if orb.requested is None:
            orb.requested = self.fight.tick
        return True

    def position(self, entity: int):
        orb = self.orbs.get(entity)
        if orb is not None:
            return orb.rocket.position
        return None

    def flying(self, rocket) -> bool:
        orb = self.orbs.get(rocket.halt_entity)
        return orb is None or orb.requested is None

    def landed(self, rocket) -> None:
        orb = self.orbs.get(rocket.halt_entity)
        if orb is not None and orb.requested is None:
            orb.requested = self.fight.tick

    def retire(self, body: int) -> None:
        for orb in tuple(self.orbs.values()):
            if orb.rocket.halt_body == body:
                self._finish(orb, "retired")
        self.drop_pull(body)

    def drop_pull(self, entity):
        pull = self.pulls.pop(entity, None)
        if pull is not None and not pull.script.stopped:
            pull.script.component.destroy(pull.script)

    def _finish(self, orb: Orb, reason: str) -> None:
        rocket = orb.rocket
        self.orbs.pop(orb.entity, None)
        if rocket in self.fight.rockets:
            if reason == "retired":
                self.fight.rockets.remove(rocket)
            else:
                self.fight._landed(rocket)
        # Entity destruction does not rewrite the body's cached handle. Its
        # authored existence watchers start cooldown and clear their own scopes.

    def _pull(self, orb: Orb, tick: int) -> None:
        from ow174.game.combat import ray_world, segment_distance

        centre = orb.rocket.position
        control, query = orisa_halt.control_graph(), orisa_halt.query_graph()
        if control is None or query is None:
            return  # missing authored inputs must not silently revive the old50-speed substitute
        for target in self.fight._enemies(orb.rocket.shooter, team=orb.rocket.team):
            if isinstance(target.owner, bots.Bot) or _protected(target.owner):
                continue
            a, b = target.segment()
            if segment_distance(centre, a, b) - target.radius > RADIUS:
                continue
            body = getattr(target.owner, "body_script", None)
            if body is None:
                continue

            def blocked(start, end):
                delta = tuple(axis - old for axis, old in zip(end, start, strict=True))
                distance = math.sqrt(sum(axis * axis for axis in delta))
                if distance <= 1e-9:
                    return False
                direction = tuple(axis / distance for axis in delta)
                wall = ray_world(self.fight._collision(), tuple(start), direction, distance)
                return ((wall is not None and wall < distance - 1e-6)
                        or self.fight.walls.blocked(tuple(start), tuple(end)))

            yaw = target.owner.mover.yaw
            visible = orisa_halt.configured_path(query, orb=orb.entity, origin=centre,
                                                 candidate=target.entity, position=target.feet, yaw=yaw,
                                                 ray=blocked)
            # CA3570 accepts a clear configured segment before its defaults.
            # Retain the existing reconstructed central fallback until the full
            # native hardpoint/bounds/collision-mask path has execution evidence.
            sample = (target.feet[0], target.feet[1] + TARGET_UP, target.feet[2])
            if visible.segment is None and blocked(centre, sample):
                continue
            self.drop_pull(target.entity)
            instance = body.component.create(control)
            instance.start_entries(((0x0A40000000000078, expr.Vec3(*centre)),
                                    (0x0A4000000000007B, RADIUS),
                                    (0x0A4000000000000A, expr.Entity(orb.rocket.shooter.entity))), orb.entity)
            body.component.run_now()
            seconds = sum(expr.to_float(instance.evaluate(instance.graph.node(pos).field("m_timeout")))
                          for pos in (13, 9, 30, 5))
            self.pulls[target.entity] = Pull(target.entity, target.owner,
                                             tick + math.ceil(seconds / TICK), orb.rocket.team, instance)

    def _valid(self, pull: Pull, tick: int) -> bool:
        owner = pull.owner
        vitals = self.fight.vitals.get(pull.entity)
        if pull.script.stopped:
            return False
        for pos in (17, 29):
            state = pull.script.states.get(pull.script.graph.node(pos).state)
            if state is not None and state.active and (bool(state.current) if pos == 17
                                                      else state.current is False):
                return False
        linger = pull.script.states.get(pull.script.graph.node(5).state)
        if linger is not None and linger.ended_at:
            return False
        return (owner.has_body and not owner.gone and owner.spawned and owner.body == pull.entity
                and (vitals is None or not vitals.dead)
                and tick < pull.until and not _protected(owner)
                and (self.fight.match.game_map.free_for_all or owner.team != pull.source_team))

    def update(self, tick):
        for orb in tuple(self.orbs.values()):
            if orb.requested is None:
                continue
            if not orb.pulled:
                if tick < orb.requested + math.ceil(ORB_END / TICK):
                    self._pull(orb, tick)
                orb.pulled = True
            if tick >= orb.requested + math.ceil(ORB_END / TICK):
                self._finish(orb, "detonated")
        for entity, pull in tuple(self.pulls.items()):
            if not self._valid(pull, tick):
                self.drop_pull(entity)
