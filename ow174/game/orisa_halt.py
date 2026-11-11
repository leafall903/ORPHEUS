"""Original Halt target graph inputs; world pose and lifecycle remain reconstructed."""

import math
from dataclasses import dataclass

from ow174.game import bots, defenses, mei_wall, reinhardt_charge
from ow174.game.commands import Command
from ow174.game.mover import Mover, mover_data
from ow174.game.script import expr, graph, runtime, target_visibility

HALT = 0x0CCB
PROJECTILE = 0x040000000000172C
DETONATE = 0x024000000000003F
RADIUS = 5.0
ROADHOG = 0x02E0000000000040


class _VisibilityContext(expr.Context):
    """Explicit frozen-orb/authoritative root-yaw pose profile, not native bones.

    CAE3B0 binds the candidate entity through1B8D870 while testing visibility.
    Full engine transforms, tilt/animation and collision policy remain external.
    """

    def __init__(self, definition, orb, origin, candidate, position, yaw):
        super().__init__()
        self.orb, self.origin = orb, expr.Vec3(*origin)
        self.candidate, self.candidate_position = candidate, expr.Vec3(*position)
        angle = yaw * (2 * math.pi / 65536)
        cosine, sine = expr.f32(math.cos(angle)), expr.f32(math.sin(angle))
        self.rows = {"STU_66F3B89E": expr.Vec3(cosine, 0, -sine),
                     "STU_021D01CD": expr.Vec3(sine, 0, cosine),
                     "STU_D32169F6": expr.Vec3(0, 1, 0)}
        self.offset = expr.evaluate(definition.node(35).field("m_value"), self)

    def owner(self):
        return expr.Entity(self.orb)

    def var(self, scope, identifier):
        if scope == 0 and identifier == 10601:
            return self.offset
        raise target_visibility.VisibilityUnavailable("unbound Halt visibility variable")

    def param(self, identifier):
        if identifier == 92:
            return expr.Entity(self.candidate)
        raise target_visibility.VisibilityUnavailable("unbound Halt visibility parameter")

    def native(self, config):
        entity = expr.to_entity(expr.evaluate(config.get("m_entity"), self))
        cls = config.get("$")
        if cls == "STU_71A87E92":
            if entity == self.orb:
                return self.origin
            if entity == self.candidate:
                return self.candidate_position
        if entity == self.candidate and cls in self.rows:
            return self.rows[cls]
        if entity == self.orb and cls == "STU_2200A66D":
            return target_visibility.ZERO  # this manager's detonated orb is stationary
        raise target_visibility.VisibilityUnavailable("unbound Halt visibility pose query")


def configured_path(definition, *, orb, origin, candidate, position, yaw, ray):
    """Original0CCC successful sample path under explicit root-yaw/static-ray inputs.

    No successful segment means the caller still owes CA3570's outer fallback;
    it must not turn this helper's rejection into a whole-query rejection.
    """
    context = _VisibilityContext(definition, orb, origin, candidate, position, yaw)
    query = definition.node(1)
    def evaluate(config):
        return expr.evaluate(config, context)

    return target_visibility.sample_segments(query.field("m_47AB1887"),
                                            origin=evaluate(query.field("m_D59188D5")),
                                            evaluate=evaluate, ray=ray,
                                            test_offset_leg=evaluate(query.field("m_E8474EE8")))


def native(world, cfg, instance):
    if (instance is None or instance.graph.index != 0xCCE
            or cfg.get("$") != "STU_71A87E92"):
        return NotImplemented
    entity = expr.to_entity(instance.evaluate(cfg.get("m_entity")))
    if entity != instance.component.entity:
        return NotImplemented
    # Explicit authoritative own-pose bridge, not arbitrary native transform,
    # hardpoint or foreign entity support.
    position = getattr(world, "owner_position", None)
    if position is None:
        position = world.position(entity)
    return position if isinstance(position, expr.Vec3) else NotImplemented


@dataclass
class Orb:
    entity: int
    source_body: int
    shooter: object
    shooter_body: int
    position: tuple
    direction: tuple
    speed: float
    left: float
    born: int
    requested: int | None = None
    acquired: bool = False


@dataclass
class Pull:
    owner: object
    script: runtime.Instance
    shooter_body: int
    mover: Mover | None = None
    pose_tick: int | None = None


class Halt:
    """Adapt the Orisa provider's stationary orb and authored target graph."""

    def __init__(self, fight):
        self.fight = fight
        self.control = graph.graph(0xCCE)
        self.query = graph.graph(0xCCC)
        self.orbs: dict[int, Orb] = {}
        self.pulls: dict[int, Pull] = {}
        self.player_pose_ticks: dict[int, int] = {}
        self.player_command_cutoffs: dict[int, int] = {}

    def fired(self, owner, state, origin, direction):
        if ((state.instance.graph.index, state.node.pos) != (HALT, 8)
                or self.control is None or self.query is None):
            return False
        speed = expr.to_float(state.evaluate(state.node.field('m_projectileSpeed'), watch=False))
        lifetime = expr.to_float(state.evaluate(state.node.field('m_projectileLifetime'), watch=False))
        entity = self.fight.next_bot
        self.fight.next_bot += 1
        body = state.instance.component.entity
        self.orbs[entity] = Orb(entity, body, owner, body, origin, direction, speed, lifetime,
                                self.fight.tick)
        state.instance.write(expr.dynamic(state.node.field('m_505472EF')), expr.Entity(entity))
        return True

    def receive(self, target, message, sender):
        orb = self.orbs.get(target)
        if orb is None or message != DETONATE:
            return False
        if (orb.shooter_body == orb.source_body == sender == orb.shooter.body
                and not self.fight.is_dead(orb.shooter) and orb.requested is None):
            orb.requested = self.fight.tick
        return True

    def retire(self, body):
        # The launch graph keeps the detonation input. The reflected orb belongs to its new shooter.
        for entity, orb in tuple(self.orbs.items()):
            if orb.shooter_body == body:
                self.orbs.pop(entity)
        for entity, pull in tuple(self.pulls.items()):
            if entity == body or pull.shooter_body == body:
                self.drop(entity)
        self.player_pose_ticks.pop(body, None)
        self.player_command_cutoffs.pop(body, None)

    def drop(self, entity):
        pull = self.pulls.pop(entity, None)
        if pull is None:
            return
        if pull.script.external_time is not None:
            self.stop_player_motion(entity, pull.owner)
        if not pull.script.stopped:
            pull.script.component.destroy(pull.script)

    def can_drive_pose(self, entity):
        pull = self.pulls.get(entity)
        return pull is not None and (
            pull.script.external_time is not None or isinstance(pull.owner, bots.Bot)
        )

    def drove_pose(self, entity, tick):
        pull = self.pulls.get(entity)
        return self.player_pose_ticks.get(entity) == tick or (pull is not None and pull.pose_tick == tick)

    def stop_player_motion(self, entity, owner):
        fight = self.fight
        last = self.player_pose_ticks.get(entity)
        mover = owner.mover
        # Only clear velocity still owned by the most recent Halt displacement.
        if (mover.driven and last is not None and last >= fight.tick - 1
                and fight.hooks.player_pose_ticks.get(entity, -1) < last
                and entity not in fight.gravitons.pulled
                and not reinhardt_charge.pinned(fight, entity)):
            mover.velocity = (0.0, 0.0, 0.0)
            mover.driven = False

    def fly(self, orb, tick, seconds, reflections=0):
        from ow174.game import combat

        duration = min(seconds, orb.left)
        length = orb.speed * duration
        if length <= 0:
            orb.requested = tick
            return
        fight = self.fight
        wall = combat.ray_world(fight._collision(), orb.position, orb.direction, length)
        ice = fight.walls.cast(orb.position, orb.direction, length)
        if ice is not None:
            wall = ice[0] if wall is None else min(wall, ice[0])
        gate = defenses.projectile_gate(orb.position, orb.direction, length if wall is None else wall,
                                        fight._enemies(orb.shooter), PROJECTILE)
        if gate is not None and (wall is None or gate[0] < wall):
            distance, target, mode = gate
            if mode == 1 or reflections >= combat.MAX_DEFLECTIONS:
                self.orbs.pop(orb.entity)
                return
            spent = distance / orb.speed
            orb.left -= spent
            orb.shooter = target.owner
            orb.shooter_body = target.entity
            point = combat._along(orb.position, orb.direction, distance)
            orb.direction = combat.aim(round(target.owner.mover.yaw), round(target.owner.mover.pitch))
            orb.position = combat._along(point, orb.direction, .001)
            if duration > spent:
                self.fly(orb, tick, duration - spent, reflections + 1)
            return
        orb.position = combat._along(orb.position, orb.direction, length if wall is None else wall)
        orb.left -= duration
        if wall is not None or orb.left <= 0:
            orb.requested = tick

    def acquire(self, orb):
        from ow174.game.combat import EPSILON, capsule_visible, segment_distance

        fight = self.fight

        def blocked(start, end):
            start, end = tuple(start), tuple(end)
            if not capsule_visible(fight._collision(), start, end, end, 0, walls=fight.walls):
                return True
            # Energy barriers affect only pull visibility. Ice Wall already used the terrain test.
            distance = math.dist(start, end)
            if distance <= EPSILON:
                return False
            direction = tuple((b - a) / distance for a, b in zip(start, end, strict=True))
            return any(
                hit + EPSILON < distance and not isinstance(owner, mei_wall.Pillar)
                for hit, owner, _ in fight._barrier_contacts(orb.shooter, start, direction, distance)
            )

        for target in fight._enemies(orb.shooter):
            body = target.owner.body_script
            if (body is None or defenses.protection(target.owner)[0] or defenses.control_immune(target.owner)
                    or fight.zarya_bubbles.for_target(target.owner) is not None):
                continue
            a, b = target.segment()
            if segment_distance(orb.position, a, b) - target.radius > RADIUS:
                continue

            bot = isinstance(target.owner, bots.Bot)
            yaw = target.owner.yaw if bot else target.owner.mover.yaw
            visible = configured_path(
                self.query, orb=orb.entity, origin=orb.position, candidate=target.entity,
                position=target.feet, yaw=yaw, ray=blocked,
            )
            x, y, z = target.feet
            if visible.segment is None and blocked(orb.position, (x, y + 1.3, z)):
                continue
            self.drop(target.entity)
            if bot:
                body.component.world.owner_position = expr.Vec3(*target.feet)
            instance = body.component.create(self.control)
            if not bot and target.owner.hero.guid == ROADHOG:
                instance.external_time = body.component.now
            instance.start_entries(((0x0A40000000000078, expr.Vec3(*orb.position)),
                                    (0x0A4000000000007B, RADIUS),
                                    (0x0A4000000000000A, expr.Entity(orb.shooter.entity))), orb.entity)
            if instance.external_time is None:
                body.component.run_now()
            else:
                instance.run_external(instance.external_time)
            self.pulls[target.entity] = Pull(target.owner, instance, orb.shooter_body)

    def update(self, tick, dt):
        fight = self.fight
        for orb in tuple(self.orbs.values()):
            if orb.requested is None and tick > orb.born:
                self.fly(orb, tick, dt)
            if orb.requested is None:
                continue
            if not orb.acquired:
                self.acquire(orb)
                orb.acquired = True
            if tick >= orb.requested + math.ceil(.5 / dt):
                self.orbs.pop(orb.entity, None)
        for entity, pull in tuple(self.pulls.items()):
            owner = pull.owner
            script = pull.script
            if script.external_time is not None:
                script.run_external(script.external_time + round(dt * 1000))
            linger = pull.script.states.get(self.control.node(5).state)
            vitals = fight.vitals.get(entity)
            if (pull.script.stopped or vitals is None or vitals.dead or defenses.protection(owner)[0]
                    or defenses.control_immune(owner) or fight.zarya_bubbles.for_target(owner) is not None
                    or (linger is not None and linger.ended_at) or self.rejected(pull)):
                self.drop(entity)
                continue
            state = pull.script.states.get(self.control.node(3).state)
            request = state.move_request() if state is not None else None
            # Leave the pose with the control that already owns it.
            if (request is None or fight.hooks.controlled(entity) or fight.traps.rooted(entity, tick)
                    or entity in fight.gravitons.pulled or entity in fight.pins.values()
                    or reinhardt_charge.pinned(fight, entity)
                    or (script.external_time is not None and fight.is_stunned(owner, tick=tick))):
                if script.external_time is not None:
                    self.stop_player_motion(entity, owner)
                continue
            if not isinstance(owner, bots.Bot):
                if script.external_time is not None:
                    fight.impulses.retire(entity, forget=False)
                    self.move_player(owner, state, request, tick, dt)
                continue
            fight.impulses.retire(entity, forget=False)
            pull.pose_tick = tick
            if pull.mover is None:
                pull.mover = Mover(
                    owner.position, round(owner.yaw), fight._collision(), mover_data(owner.body),
                )
            else:
                pull.mover.position[:] = owner.position
            destination, speed = request
            outcome = pull.mover.step_pose(Command(tick, yaw=round(owner.yaw)), dt, tick,
                                           move_to=(tuple(destination), speed))
            if outcome is not None:
                state.request(runtime.FINISH if outcome else runtime.ABORT)
            owner.position, owner.velocity = tuple(pull.mover.position), pull.mover.velocity
            owner.moved_tick = owner.last_tick = tick
            pull.script.component.world.owner_position = expr.Vec3(*owner.position)

    def move_player(self, owner, state, request, tick, dt):
        fight = self.fight
        entity = owner.body
        self.player_pose_ticks[entity] = tick
        if owner.command_clock is not None:
            frame, received = owner.command_clock
            cutoff = frame + (tick - received) * round(dt * 1_000_000) // owner.quanta
            self.player_command_cutoffs[entity] = max(cutoff, self.player_command_cutoffs.get(entity, -1))
        mover = owner.mover
        before = tuple(mover.position)
        destination, speed = request
        mover.use(world=fight._collision())
        mover.driven = True
        mover.throttles = (0, 0)
        outcome = mover._move_to(dt, tuple(destination), speed)
        fight.walls.move_player(owner, before)
        fight.revise_world_pose(entity, mover.position)
        if outcome is not None:
            state.request(runtime.FINISH if outcome else runtime.ABORT)
            script = state.instance
            script.run_external(script.external_time)

    @staticmethod
    def rejected(pull):
        for pos in (17, 29):
            state = pull.script.states.get(pull.script.graph.node(pos).state)
            if (state is not None and state.active
                    and (bool(state.current) if pos == 17 else state.current is False)):
                return True
        return False
