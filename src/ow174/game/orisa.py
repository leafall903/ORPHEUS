"""Reconstructed Orisa engine consumers; authored body graphs still own input/cooldowns.

Target acquisition is still reconstructed. Original0CCC configured visibility
paths use explicit root-yaw/static-ray services, with a reconstructed fallback.
Actual0CCE owns startup, MoveTo and post-slow. Native outer geometry/filter and
query lifecycle, separate PhysicsExplosion and full status dispatch remain open.
"""

import math
from collections import deque
from dataclasses import dataclass, field

from ow174.game import bots, correction, orisa_charge, orisa_clock, orisa_halt, projectiles, wraith
from ow174.game.commands import Command
from ow174.game.mover import Mover, mover_data
from ow174.game.orisa_barrier import Barriers
from ow174.game.orisa_supercharger import Superchargers
from ow174.game.script import expr, graph, runtime

HALT_GRAPH = 0x0CCB
DETONATE = 0x024000000000003F
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
    query: orisa_halt.PullQuery | None = None
    origin: tuple | None = None
    actor_generations: dict = field(default_factory=dict)


@dataclass
class Pull:
    entity: int
    owner: object
    destination: tuple
    until: int
    last_tick: int
    mover: Mover | None = None
    script: runtime.Instance | None = None
    motion_requested: bool = False


class Orisa:
    def __init__(self, fight):
        self.fight = fight
        self.charge_metadata = orisa_charge.catalog()
        self.halt_graph = orisa_halt.control_graph()
        self.halt_query_graph = orisa_halt.query_graph()
        self.orbs: dict[int, Orb] = {}
        self.pulls: dict[int, Pull] = {}
        self.results: deque[tuple[int, tuple[int, ...]]] = deque(maxlen=64)
        self.barriers = Barriers(fight)
        self.superchargers = Superchargers(fight)

    def admit(self, rocket) -> None:
        if rocket.source_graph != HALT_GRAPH:
            return
        entity = getattr(rocket.shot, "entity", None)
        if entity is None:
            entity = self.fight.next_bot
            if not bots.BOT_ENTITY <= entity < 0xD0000000:
                raise ValueError("ability entity allocator exhausted")
            self.fight.next_bot += 1  # fallback for a flight without client delivery
        rocket.entity = entity
        self.orbs[entity] = Orb(entity, rocket, origin=rocket.position)
        state = rocket.source_state
        output = expr.lvalue(state.node.field("m_505472EF"))
        state.instance.write(output, expr.Entity(entity))

    def receive(self, target: int, message: int, sender: int) -> bool:
        orb = self.orbs.get(target)
        if orb is None or message != DETONATE:
            return False
        rocket = orb.rocket
        # Identity is the body at emission, never the player's later possession.
        if sender != rocket.source_body or _identity(rocket.shooter) != sender:
            return False
        vitals = self.fight.vitals.get(sender)
        if vitals is not None and vitals.dead:
            return False
        if orb.requested is None:
            script = rocket.shooter.body_script
            at = orisa_clock.event_tick(script, script.component.now)
            cursor = rocket.last_tick
            if at < cursor and orb.origin is not None:
                # An airborne Halt has no body damage or impacts to undo. Replay
                # its straight flight to the accepted detonation event's time.
                rocket.position = orb.origin
                rocket.left += rocket.flown
                rocket.flown = rocket.travelled = 0.0
                rocket.direction = rocket.launch_direction
                cursor = rocket.started_tick
                rocket.last_tick = cursor
            for tick in range(cursor + 1, at + 1):
                if rocket not in self.fight.rockets:
                    break
                rocket.last_tick = tick
                self.fight._fly_step(rocket, min(TICK, rocket.left))
            if orb.requested is None:
                orb.requested = at
        return True

    def position(self, entity: int):
        orb = self.orbs.get(entity)
        if orb is not None:
            return orb.rocket.position
        device = self.superchargers.active.get(entity)
        return device.position if device is not None else None

    def flying(self, rocket) -> bool:
        orb = self.orbs.get(rocket.entity)
        return orb is None or orb.requested is None

    def landed(self, rocket) -> None:
        self.superchargers.landed(rocket)
        if rocket.source_graph == 0x0CF5 and rocket.shot is not None:
            rocket.shot.release_at = (orisa_clock.impact_tick(rocket, self.fight.tick)
                                      + projectiles.RELEASE_MARGIN)
        orb = self.orbs.get(rocket.entity)
        if orb is not None and orb.requested is None:
            orb.requested = orisa_clock.impact_tick(rocket, self.fight.tick)

    def retire(self, body: int) -> None:
        self.barriers.retire(body)
        self.superchargers.retire(body)
        for orb in tuple(self.orbs.values()):
            if orb.rocket.source_body == body:
                self._finish(orb, "retired")
        self._drop(body)

    def _drop(self, entity):
        pull = self.pulls.pop(entity, None)
        if pull is not None and pull.script is not None:
            pull.script.component.destroy(pull.script)
            if isinstance(pull.owner, bots.Bot) and not pull.owner.firing and not pull.owner.dead:
                body = pull.owner.body_script
                if body is not None and body.frame < self.fight.tick:
                    # Passive training bodies leave the weapon scheduler again
                    # after this pull. Publish the removed control namespace now.
                    body.advance_live(self.fight.tick, pull.owner.position)

    @staticmethod
    def interrupt_supercharger_cast(owner) -> None:
        """Scoped control consumer for0CF5's detached pre-launch waits.

        Its start output stops Ability immediately, while Wait30 still schedules
        the health-checked launch. Abort that pending wait and its cast scope via
        normal State.end(False); spent charge stays spent. Native cancellation
        dispatch/animation and other status effects remain unverified.
        """
        script = getattr(owner, "body_script", None)
        if script is None:
            return
        for instance in script.component.instances.values():
            if instance.graph.index != 0x0CF5:
                continue
            pending = instance.states.get(instance.graph.node(30).state)
            if pending is None or not pending.active:
                continue  # flight/deployed lifetime is no longer a startup cast
            pending.end(False)
            scope = instance.states.get(instance.graph.node(7).state)
            if scope is not None and scope.active:
                scope.end(False)

    @staticmethod
    def _immune(owner) -> bool:
        script = getattr(owner, "body_script", None)
        var = script.component.entity_var(9161, create=False) if script is not None else None
        return var is not None and expr.truthy(var.value())

    def _finish(self, orb: Orb, reason: str) -> None:
        rocket = orb.rocket
        self.orbs.pop(orb.entity, None)
        if orb.query is not None:
            instance = orb.query.instance
            instance.component.destroy(instance)
        rocket.ended = reason
        if rocket in self.fight.rockets:
            if reason == "retired":
                self.fight.rockets.remove(rocket)
            else:
                self.fight._landed(rocket)
        # Entity destruction does not rewrite the body's cached handle. Its
        # authored existence watchers start cooldown and clear their own scopes.

    def _dispatch_halt_explosion(self, request: orisa_halt.ExplosionRequest) -> None:
        match = self.fight.match
        match.dynamic_spheres.dispatch(request)
        dispatch = getattr(match.collision_world, "dispatch_physics_explosion", None)
        if dispatch is not None:
            dispatch(request)

    def _pull(self, orb: Orb, tick: int) -> None:
        from ow174.game.combat import ray_world, segment_distance

        centre = orb.rocket.position
        selected = []
        if self.halt_graph is None or self.halt_query_graph is None:
            self.results.append((orb.entity, ()))
            return  # missing authored inputs must not silently revive the old50-speed substitute
        query = orisa_halt.PullQuery(self.halt_query_graph, orb.entity, centre, orb.rocket.shooter.entity,
                                     explosion_sink=self._dispatch_halt_explosion)
        for target in self.fight._enemies(orb.rocket.shooter):
            if self._immune(target.owner):
                continue
            a, b = target.segment()
            if segment_distance(centre, a, b) - target.radius > query.radius:
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
                return wall is not None and wall < distance - 1e-6

            yaw = target.owner.yaw if isinstance(target.owner, bots.Bot) else target.owner.mover.yaw
            visible = orisa_halt.configured_path(self.halt_query_graph, orb=orb.entity, origin=centre,
                                                 candidate=target.entity, position=target.feet, yaw=yaw,
                                                 ray=blocked)
            # CA3570 accepts a clear configured segment before its defaults.
            # Retain the existing reconstructed central fallback until the full
            # native hardpoint/bounds/collision-mask path has execution evidence.
            sample = (target.feet[0], target.feet[1] + TARGET_UP, target.feet[2])
            if visible.segment is None and blocked(centre, sample):
                continue
            selected.append((target, body))
        targets = tuple(target.entity for target, _ in selected)
        request = query.complete(targets)
        if not targets:
            query.start_tail(tick)
            orb.query = query
            self.results.append((orb.entity, ()))
            return
        expected = (graph.GRAPH_TYPE << 48) | self.halt_graph.index
        if (request is None or request.resource.guid != expected or request.targets != targets
                or request.policy != 3 or request.wire_flag != 0):
            self.results.append((orb.entity, ()))
            return
        query.start_tail(tick)
        orb.query = query
        # This immediate 0CCE receiver consumes the authored one-value parameters
        # as scalars. General PlayScript requests retain their array parameters.
        params = tuple((identifier, value) for identifier, (value,) in request.params)
        for target, body in selected:
            self._drop(target.entity)
            if isinstance(target.owner, bots.Bot):
                body.component.world.owner_position = expr.Vec3(*target.feet)
            instance = body.component.play_loaded_graph(
                self.halt_graph, source=request.context.caller, params=params,
                overrides=request.resource.overrides, policy=request.policy, instigator=request.context.instigator,
            )
            # Retain the already-resolved instigator on the receiver's wire
            # descriptor; implicit SELF would initialize it from the victim.
            instance.set_source_entity(instance.source_entity(), publish_source=True)
            body.component.run_now()
            seconds = sum(expr.to_float(instance.evaluate(instance.graph.node(pos).field("m_timeout")))
                          for pos in (13, 9, 30, 5))
            self.pulls[target.entity] = Pull(target.entity, target.owner, centre,
                                             tick + math.ceil(seconds / TICK), tick, script=instance)
        self.results.append((orb.entity, targets))

    def _valid(self, pull: Pull, tick: int) -> bool:
        owner = pull.owner
        entity = owner.entity if isinstance(owner, bots.Bot) else owner.body
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
        return (entity == pull.entity
                and (vitals is None or not vitals.dead)
                and not self._immune(owner) and not wraith.intangible(owner)
                and (owner in self.fight.match.bots if isinstance(owner, bots.Bot)
                     else owner in self.fight.match.players and owner.has_body
                     and owner.spawned and owner.client is not None))

    def movement(self, player, *, command_frame=None):
        pull = self.pulls.get(player.body)
        if pull is None:
            return None
        if not self._valid(pull, self.fight.tick):
            self._drop(pull.entity)
            return None
        # Retire the old independent force when this authored MoveTo takes over.
        # Body epochs and completed-pose receipts belong to the live body.
        if (self._request(pull)[1] is not None
                and not self.fight.hooks.controlled(player.body)
                and player.body not in self.fight.zarya_gravitons.pulled
                and player.body not in self.fight.pins.values()):
            self.fight.knockbacks.retire(player.body)
            player.mover.retire_impulses()
            if command_frame is not None and not pull.motion_requested:
                # This original foreign MoveTo changes the existing body mover.
                # Publish its actual command poses through the common finite
                # receiver; do not synthesize another impulse or owner epoch.
                pull.motion_requested = correction.request_motion(
                    player, first_frame=command_frame, body=pull.entity,
                )
        # The normal player MoveTo path consumes the actual0CCE state request
        # and reports arrival/abort to that same state. No forced50-speed path.
        return None

    @staticmethod
    def _request(pull):
        state = pull.script.states.get(pull.script.graph.node(3).state)
        return (state, state.move_request()) if state is not None else (None, None)

    def controls(self, entity: int, tick: int | None = None) -> bool:
        pull = self.pulls.get(entity)
        return (pull is not None and self._valid(pull, self.fight.tick if tick is None else tick)
                and self._request(pull)[1] is not None)

    def npc_movement_mods(self, entity: int, tick: int):
        pull = self.pulls.get(entity)
        if pull is not None and self._valid(pull, tick):
            return orisa_halt.npc_movement_mods(pull)
        return None

    def update(self, tick: int) -> None:
        self.barriers.update(tick)
        self.superchargers.update(tick)
        for orb in tuple(self.orbs.values()):
            if _identity(orb.rocket.shooter) != orb.rocket.source_body:
                self._finish(orb, "retired")
                continue
            if orb.requested is None:
                continue
            if not orb.pulled:
                if tick < orb.requested + math.ceil(ORB_END / TICK):
                    self._pull(orb, tick)
                orb.pulled = True
            if tick >= orb.requested + math.ceil(ORB_END / TICK):
                self._finish(orb, "detonated")
            elif orb.query is not None:
                orb.query.instance.component.run_frame(tick)
        from ow174.game import orisa_presentation

        orisa_presentation.sync_orbs(self.fight)
        for entity, pull in tuple(self.pulls.items()):
            if not self._valid(pull, tick):
                self._drop(entity)
                continue
            if not isinstance(pull.owner, bots.Bot):
                continue  # player input drives that body's mover once per command
            bot = pull.owner
            pull.script.component.world.owner_position = expr.Vec3(*bot.position)
            state, request = self._request(pull)
            if request is None:
                pull.last_tick = tick
                continue
            self.fight.knockbacks.retire(entity)
            if pull.mover is None:
                pull.mover = bot.impulse_mover
                if pull.mover is None:
                    pull.mover = Mover(bot.position, int(bot.yaw), self.fight._collision(),
                                       bot.movement_data or mover_data(bot.body), category=bot.category)
                pull.mover.retire_impulses()
                # The same body solver must resume gravity when authored MoveTo
                # ends; a detached pull-only mover leaves static victims in air.
                bot.impulse_mover = pull.mover
            destination, speed = request
            pull.destination = tuple(destination)
            for at in range(max(pull.last_tick + 1, tick - 63), tick + 1):
                outcome = pull.mover.step_pose(Command(at, yaw=int(bot.yaw)), TICK, at,
                                                move_to=(pull.destination, speed))
                if outcome is not None:
                    state.request(runtime.FINISH if outcome else runtime.ABORT)
                    break
            pull.last_tick = tick
            bot.position, bot.velocity = tuple(pull.mover.position), pull.mover.velocity
            retained = bot.impulse_mover
            if retained is not None and retained is not pull.mover:
                # The independent force retired at takeover; keep its reusable
                # body solver at the actual controlled pose for release.
                retained.position[:] = bot.position
                retained.velocity = bot.velocity
                retained.retire_impulses()
                retained.body = None
            pull.script.component.world.owner_position = expr.Vec3(*bot.position)
            bot.moved_tick = bot.last_tick = tick
            bot.pace, bot.keys = 0.0, False
