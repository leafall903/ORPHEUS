"""Reconstructed Orisa engine consumers; authored body graphs still own input/cooldowns.

Target acquisition is still reconstructed. Original0CCC configured visibility
paths use explicit root-yaw/static-ray services, with a reconstructed fallback.
Actual0CCE owns startup, MoveTo and post-slow. Native outer geometry/filter and
query lifecycle, separate PhysicsExplosion and full status dispatch remain open.
"""

import math
from dataclasses import dataclass

from ow174.game import bots, defenses, orisa_halt
from ow174.game.commands import Command
from ow174.game.mover import Mover, mover_data
from ow174.game.script import expr, runtime

HALT_GRAPH = 0x0CCB
PROJECTILE = 0x040000000000172C
DETONATE = 0x024000000000003F
RADIUS = 5.0
WINDUP = 0.4
ORB_END = 0.5
TARGET_UP = 1.3  # 0CCC TrackTargets visibility endpoints
TICK = 0.016


def _identity(actor) -> int:
    return actor.entity if isinstance(actor, bots.Bot) else actor.body


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
    script: runtime.Instance | None = None
    mover: Mover | None = None


class Halt:
    def __init__(self, fight):
        self.fight = fight
        self.orbs: dict[int, Orb] = {}
        self.pulls: dict[int, Pull] = {}

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
        # Only the current projectile owner can send this message. Reflection
        # transfers the lease and a later body replacement cannot inherit it.
        if sender != rocket.halt_body or _identity(rocket.shooter) != sender:
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

    def drop_pull(self, entity, *, terminal=False):
        from ow174.game.script.bot import BotScript

        pull = self.pulls.pop(entity, None)
        if pull is None:
            return
        body = pull.owner.body_script if isinstance(pull.owner, bots.Bot) else None
        bot_script = body if isinstance(body, BotScript) else None
        if bot_script is not None and terminal:
            bot_script.freeze_live_baseline()
        if pull.script is not None and not pull.script.stopped:
            pull.script.component.destroy(pull.script)
        if bot_script is not None and not terminal and pull.owner in self.fight.match.bots:
            snapshot = bot_script.capture_live()
            if snapshot is not None:
                for viewer in self.fight.match.players:
                    if viewer.spawned and viewer.client is not None:
                        viewer.client.queue_bot_live(entity, snapshot)
        self._release_mover(pull)

    def _release_mover(self, pull):
        entity = pull.entity
        mover = pull.mover
        pull.mover = None
        if (mover is not None and pull.owner.velocity == mover.velocity
                and entity not in self.fight.knockbacks and entity not in self.fight.gravitons.pulled):
            pull.owner.velocity = mover.velocity = (0.0, 0.0, 0.0)
            if (mover.airborne and pull.owner in self.fight.match.bots
                    and not pull.owner.dead and not self.fight.pinned(entity)):
                self.fight.knockbacks[entity] = mover

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
            if (defenses.control_immune(target.owner)
                    or self.fight.pinned(target.entity) or target.entity in self.fight.zarya_bubbles):
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

            npc = isinstance(target.owner, bots.Bot)
            yaw = target.owner.yaw if npc else target.owner.mover.yaw
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
            if npc and body.frame < tick - 1:
                body.advance_live(tick - 1, target.feet)
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
        entity = _identity(owner)
        vitals = self.fight.vitals.get(pull.entity)
        if pull.script is None or pull.script.stopped:
            return False
        for pos in (17, 29):
            state = pull.script.states.get(pull.script.graph.node(pos).state)
            if state is not None and state.active and (bool(state.current) if pos == 17
                                                      else state.current is False):
                return False
        linger = pull.script.states.get(pull.script.graph.node(5).state)
        if linger is not None and linger.ended_at:
            return False
        present = (owner in self.fight.match.bots and not owner.dead if isinstance(owner, bots.Bot)
                   else owner in self.fight.match.players and owner.has_body and owner.spawned
                   and owner.client is not None)
        return (present and entity == pull.entity
                and (vitals is None or not vitals.dead)
                and tick < pull.until and not defenses.control_immune(owner) and not self.fight.pinned(entity)
                and (self.fight.match.game_map.free_for_all or owner.team != pull.source_team))

    def moving(self, entity):
        pull = self.pulls.get(entity)
        if pull is None:
            return False
        state = pull.script.states.get(pull.script.graph.node(3).state)
        return state is not None and state.move_request() is not None

    def step_npc(self, bot, tick, seconds):
        pull = self.pulls.get(bot.entity)
        if pull is None or not self._valid(pull, tick):
            return False
        state = pull.script.states.get(pull.script.graph.node(3).state)
        request = state.move_request() if state is not None else None
        if request is None:
            self._release_mover(pull)
            return False
        if pull.mover is None:
            pull.mover = self.fight.knockbacks.pop(bot.entity, None)
            if pull.mover is None:
                pull.mover = Mover(bot.position, int(bot.yaw), self.fight._collision(), mover_data(bot.body))
        mover = pull.mover
        mover.use(world=self.fight._collision())
        if tuple(mover.position) != tuple(bot.position):
            mover.position[:] = bot.position
            mover.body = None
        destination, speed = request
        outcome = mover.step_pose(Command(tick, yaw=int(bot.yaw)), seconds, tick,
                                  move_to=(tuple(destination), speed))
        bot.position, bot.velocity = tuple(mover.position), mover.velocity
        bot.moved_tick = bot.last_tick = tick
        bot.pace, bot.keys = 0.0, False
        if outcome is not None:
            state.request(runtime.FINISH if outcome else runtime.ABORT)
        return True

    def patrol_mods(self, bot, tick):
        pull = self.pulls.get(bot.entity)
        if pull is None or not self._valid(pull, tick):
            return None
        instance = pull.script
        wait = instance.states.get(instance.graph.node(5).state)
        if wait is not None and wait.active:
            return orisa_halt.npc_movement_mods(instance)
        return None

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
