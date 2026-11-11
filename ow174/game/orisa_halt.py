"""Original Halt target graph inputs; world pose and lifecycle remain reconstructed."""

import json
import math
import os
from dataclasses import dataclass
from pathlib import Path

from ow174.game import bots, fortify, mei, phase_defenses, reinhardt_barrier, shot_guards
from ow174.game.mover import Mover, mover_data
from ow174.game.script import expr, graph, nodes, runtime, target_visibility
from ow174.paths import DATA_DIR

HALT = 0x0CCB
CONTROL = 0x0CCE
PROJECTILE = 0x040000000000172C
DETONATE = 0x024000000000003F
RADIUS = 5.0
REPLAY_LIMIT = 256


def definitions():
    path = Path(os.environ.get("ORPHEUS_ORISA_HALT", DATA_DIR / "orisa_halt_174.json"))
    if not path.is_file():
        rows = graph._records()
        return rows.get(CONTROL), rows.get(0xCCC)
    data = json.loads(path.read_text(encoding="utf-8"))
    if (data.get("schema") != "orpheus-orisa-halt-v1"
            or data.get("build") != "1.74.0.0.104319"):
        raise ValueError("Halt metadata has wrong schema/build")
    rows = {row["index"]: row for row in data["graphs"]}
    return rows[CONTROL], rows[0xCCC]


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
    if (instance is None or instance.graph.index != CONTROL
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
    owner: object
    source: runtime.Instance
    shooter: object
    shooter_body: int
    team: int
    position: tuple
    direction: tuple
    speed: float
    left: float
    tick: int
    requested: int | None = None
    acquired: bool = False


@dataclass
class Pull:
    owner: object
    script: runtime.Instance
    mover: Mover | None = None
    moved_tick: int = 0


class Halt:
    def __init__(self, fight):
        self.fight = fight
        self.graph_records = None
        self.orbs: dict[int, Orb] = {}
        self.pulls: dict[int, Pull] = {}

    def fired(self, owner, state, origin, direction):
        if self.graph_records is None:
            self.graph_records = definitions()
        if None in self.graph_records or any(orb.source is state.instance for orb in self.orbs.values()):
            return
        projectile = state.evaluate(state.node.field("m_projectileEntity.m_entityDef"), watch=False)
        if not isinstance(projectile, expr.Asset) or projectile.guid != PROJECTILE:
            return
        speed = expr.to_float(state.evaluate(state.node.field("m_projectileSpeed"), watch=False))
        lifetime = expr.to_float(state.evaluate(state.node.field("m_projectileLifetime"), watch=False))
        if not all(math.isfinite(value) and value > 0 for value in (speed, lifetime)):
            return
        entity = self.fight.projectiles.allocate_seen_entity()
        self.orbs[entity] = Orb(entity, owner, state.instance, owner, owner.body, owner.team, origin,
                                direction, speed, lifetime, self.fight.tick)
        state.instance.write(expr.dynamic(state.node.field("m_505472EF")), expr.Entity(entity))

    def receive(self, target, message, sender):
        orb = self.orbs.get(target)
        if orb is None or message != DETONATE:
            return False
        if (orb.shooter is orb.owner and orb.source.component.entity == sender == orb.owner.body
                and not self.fight.is_dead(orb.owner) and orb.requested is None):
            orb.requested = self.fight.tick
        return True

    def retire(self, body):
        for entity, orb in tuple(self.orbs.items()):
            if body == orb.shooter_body:
                self.orbs.pop(entity)
        self.drop(body)

    def drop(self, entity):
        pull = self.pulls.pop(entity, None)
        if pull is not None and not pull.script.stopped:
            pull.script.component.destroy(pull.script)

    def moving(self, entity):
        pull = self.pulls.get(entity)
        return pull is not None and pull.moved_tick == self.fight.tick

    def fly(self, orb, tick, seconds, returns=0):
        from ow174.game import combat

        duration = min(seconds, orb.left)
        length = orb.speed * duration
        if length <= 0:
            orb.requested = tick
            return
        fight = self.fight
        wall = combat.ray_world(fight._line_collision(), orb.position, orb.direction, length)
        reach = length if wall is None else wall
        gate = fight._projectile_guard(orb, reach, returns < shot_guards.MAX_RETURNS)
        if gate is not None:
            distance, _, target, direction = gate
            if shot_guards.guard_hero(target.owner) != shot_guards.GENJI:
                self.orbs.pop(orb.entity, None)
                return
            spent = distance / orb.speed
            orb.left -= spent
            orb.shooter, orb.shooter_body, orb.team = target.owner, target.entity, target.team
            point = combat._along(orb.position, orb.direction, distance)
            orb.position = combat._along(point, direction, 0.001)
            orb.direction = direction
            if duration > spent:
                self.fly(orb, tick, duration - spent, returns + 1)
            return
        travel = reach if wall is None else max(0, reach - 0.001)
        orb.position = combat._along(orb.position, orb.direction, travel)
        orb.left -= duration
        if wall is not None or orb.left <= 0:
            orb.requested = tick

    def protected(self, owner):
        return (mei.protected(owner) or phase_defenses.active(owner) or fortify.active(self.fight, owner)
                or reinhardt_barrier.body_bubble(self.fight, owner) is not None)

    def acquire(self, orb, tick):
        from ow174.game.combat import capsule_visible, segment_distance

        fight = self.fight
        control, query = map(graph.Graph, self.graph_records)
        barriers = reinhardt_barrier.enemy_barriers(fight, orb.shooter, team=orb.team)
        screens = tuple(barrier for barrier in barriers if barrier.mesh)
        for target in fight._enemies(orb.shooter, team=orb.team):
            owner = target.owner
            body = getattr(owner, "body_script", None)
            if target.is_device or body is None or self.protected(owner):
                continue
            a, b = target.segment()
            if segment_distance(orb.position, a, b) - target.radius > RADIUS:
                continue

            def blocked(start, end):
                return not capsule_visible(fight._line_collision(), tuple(start), tuple(end), tuple(end), 0,
                                           barriers=screens)

            bot = isinstance(owner, bots.Bot)
            visible = configured_path(
                query, orb=orb.entity, origin=orb.position, candidate=target.entity,
                position=target.feet, yaw=owner.yaw if bot else owner.mover.yaw, ray=blocked,
            )
            x, y, z = target.feet
            if visible.segment is None and blocked(orb.position, (x, y + 1.3, z)):
                continue
            self.drop(target.entity)
            if bot:
                body.component.world.owner_position = expr.Vec3(*target.feet)
            component = body.component
            saved = component.now
            try:
                component.now = max(saved, tick * component.frame_ms)
                instance = component.create(control)
                instance.start_entries(((0x0A40000000000078, expr.Vec3(*orb.position)),
                                        (0x0A4000000000007B, RADIUS),
                                        (0x0A4000000000000A, expr.Entity(orb.shooter.entity))), orb.entity)
                component.run_now()
            finally:
                component.now = saved
            self.pulls[target.entity] = Pull(owner, instance)

    @staticmethod
    def advance_waits(instance, tick):
        component = instance.component
        saved = component.now
        end = (tick + 1) * component.frame_ms - 1
        with component.operation():
            try:
                for state in tuple(instance.states.values()):
                    if isinstance(state, nodes.Wait) and state.active and state.start + state.ms <= end:
                        component.now = max(saved, state.start + state.ms)
                        state.end(True)
                        instance.update()
            finally:
                component.now = saved

    def move(self, entity, pull, tick, dt):
        fight = self.fight
        owner, script = pull.owner, pull.script
        vitals = fight.vitals.get(entity)
        if script.stopped or vitals is None or vitals.dead or self.protected(owner):
            self.drop(entity)
            return
        self.advance_waits(script, tick)
        linger = script.states.get(script.graph.node(5).state)
        if (linger is not None and linger.ended_at) or self.rejected(pull):
            self.drop(entity)
            return
        state = script.states.get(script.graph.node(3).state)
        request = state.move_request() if state is not None else None
        if (request is None or fight.hooks.controlled(entity) or entity in fight.gravitons.player_pulls
                or entity in fight.gravitons.npc_movers or entity in fight.pins.values()):
            return
        bot = isinstance(owner, bots.Bot)
        if bot:
            if pull.mover is None:
                pull.mover = owner.dva_mover or Mover(
                    owner.position, round(owner.yaw), fight._collision(), mover_data(owner.body),
                )
            mover = pull.mover
            if tuple(mover.position) != owner.position:
                mover.position[:] = owner.position
                mover.body = None
        else:
            mover = owner.mover
        fight.impulses.retire(entity)
        destination, speed = request
        outcome = mover._move_to(dt, tuple(destination), speed)
        pull.moved_tick = tick
        if bot:
            owner.position, owner.velocity = tuple(mover.position), mover.velocity
            owner.moved_tick = owner.last_tick = tick
            script.component.world.owner_position = expr.Vec3(*owner.position)
        history = fight.history.get(entity)
        if history and history[-1][0] == tick:
            frame, _, height, eye = history[-1]
            history[-1] = frame, tuple(mover.position), height, eye
        if outcome is not None:
            component = script.component
            saved = component.now
            with component.operation():
                try:
                    component.now = max(saved, (tick + 1) * component.frame_ms - 1)
                    state.end(outcome)
                    script.update()
                finally:
                    component.now = saved

    @staticmethod
    def rejected(pull):
        for pos in (17, 29):
            state = pull.script.states.get(pull.script.graph.node(pos).state)
            if (state is not None and state.active
                    and (bool(state.current) if pos == 17 else state.current is False)):
                return True
        return False

    def update(self, tick, dt):
        for orb in tuple(self.orbs.values()):
            if orb.requested is None and tick > orb.tick:
                elapsed = tick - orb.tick
                orb.tick = tick
                self.fly(orb, tick, elapsed * dt)
            if orb.requested is None:
                continue
            if not orb.acquired:
                self.acquire(orb, tick)
                orb.acquired = True
            if tick >= orb.requested + math.ceil(0.5 / dt):
                self.orbs.pop(orb.entity, None)
        for entity, pull in tuple(self.pulls.items()):
            self.move(entity, pull, tick, dt)
