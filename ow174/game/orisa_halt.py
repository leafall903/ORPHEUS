"""Scoped Orisa Halt flight and original 0CCE target graph."""

import json
import math
import os
from dataclasses import dataclass
from pathlib import Path

from ow174.game import bots, defensive_casts
from ow174.game.script import expr, graph, target_visibility
from ow174.paths import DATA_DIR

GRAPH = 0x0CCB
DETONATE = 0x024000000000003F
TICK = 0.016
RADIUS = 5.0  # original 0CCC TrackTargets
ORB_END = 0.5


def halt_graphs():
    path = Path(os.environ.get("ORPHEUS_ORISA_HALT", DATA_DIR / "orisa_halt_174.json"))
    if not path.is_file():
        return graph.graph(0xCCC), graph.graph(0xCCE)
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("schema") != "orpheus-orisa-halt-v1" or data.get("build") != "1.74.0.0.104319":
        raise ValueError("Halt supplement has wrong schema/build")
    records = {record["index"]: record for record in data["graphs"]}
    return graph.Graph(records[0xCCC]), graph.Graph(records[0xCCE])


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

@dataclass
class Orb:
    owner: object
    source: int
    position: tuple
    direction: tuple
    speed: float
    left: float
    last_tick: int | None = None
    requested: int | None = None
    selected: bool = False


class Halt:
    def __init__(self, fight):
        self.fight = fight
        self.query, self.control = halt_graphs()
        self.orbs = {}
        self.pulls = {}

    def launch(self, shooter, state, origin, direction):
        node = state.node
        speed, lifetime = (expr.to_float(state.evaluate(node.field(field), watch=False))
                           for field in ("m_projectileSpeed", "m_projectileLifetime"))
        if not all(math.isfinite(v) and v > 0 for v in (speed, lifetime)):
            return
        entity = self.fight.next_bot
        if not bots.BOT_ENTITY <= entity < 0xD0000000:
            raise ValueError("ability entity allocator exhausted")
        self.fight.next_bot += 1
        self.orbs[entity] = Orb(shooter, shooter.body, origin, direction, speed, lifetime)
        state.instance.write(expr.lvalue(node.field("m_505472EF")), expr.Entity(entity))

    def receive(self, target, message, sender):
        orb = self.orbs.get(target)
        if (orb is None or message != DETONATE or orb.source != sender
                or orb.owner.body != sender):
            return False
        vitals = self.fight.vitals.get(sender)
        if vitals is not None and vitals.dead:
            return False
        if orb.requested is None:
            orb.requested = self.fight.tick
        return True

    def retire(self, source):
        for entity, orb in tuple(self.orbs.items()):
            if orb.source == source:
                del self.orbs[entity]

    def release(self, body):
        instance = self.pulls.pop(body, None)
        if instance is not None and not instance.stopped:
            instance.component.destroy(instance)

    def _select(self, entity, orb):
        from ow174.game.combat import ray_world, segment_distance

        if self.control is None or self.query is None:
            return
        world = self.fight._collision()

        def blocked(start, end):
            delta = tuple(b - a for a, b in zip(start, end, strict=True))
            distance = math.hypot(*delta)
            if distance <= 1e-6:
                return False
            direction = tuple(v / distance for v in delta)
            return ray_world(world, tuple(start), direction, distance - 1e-6) is not None

        for target in self.fight._enemies(orb.owner):
            script = getattr(target.owner, "body_script", None)
            if (isinstance(target.owner, bots.Bot) or script is None
                    or self.fight.vitals_of(target).dead
                    or defensive_casts.fortify_force_immune(target.owner)
                    or defensive_casts.invulnerable(target.owner)):
                continue
            a, b = target.segment()
            if segment_distance(orb.position, a, b) - target.radius > RADIUS:
                continue
            visible = configured_path(
                self.query, orb=entity, origin=orb.position, candidate=target.entity,
                position=target.feet, yaw=target.owner.mover.yaw, ray=blocked,
            )
            x, y, z = target.feet
            if visible.segment is None and blocked(orb.position, (x, y + 1.3, z)):
                continue
            if self.fight.zarya_barriers.blocks_halt(target.owner):
                continue
            component = script.component
            self.release(target.entity)
            instance = component.create(self.control)
            instance.start_entries(((0x0A40000000000078, expr.Vec3(*orb.position)),
                                    (0x0A4000000000007B, RADIUS),
                                    (0x0A4000000000000A, expr.Entity(orb.owner.entity))), entity)
            component.run_now()
            self.pulls[target.entity] = instance

    def update(self, tick):
        for body, instance in tuple(self.pulls.items()):
            wait = instance.states.get(instance.graph.node(5).state)
            if instance.stopped or (wait is not None and wait.ended_at):
                self.release(body)
        if self.pulls:
            for owner in self.fight.match.players:
                instance = self.pulls.get(owner.body)
                if instance is None:
                    continue
                linger = instance.states.get(instance.graph.node(5).state)
                if (linger is None or not linger.active) and self.fight.zarya_barriers.blocks_halt(owner):
                    self.release(owner.body)
        for entity, orb in tuple(self.orbs.items()):
            first = tick if orb.last_tick is None else orb.last_tick + 1
            orb.last_tick = tick
            for at in range(first, tick + 1):
                if orb.requested is not None:
                    if at < orb.requested:
                        continue
                    if tick >= orb.requested + math.ceil(ORB_END / TICK):
                        del self.orbs[entity]
                        break
                    if not orb.selected:
                        self._select(entity, orb)
                        orb.selected = True
                    continue
                duration = min(TICK, orb.left)
                distance = orb.speed * duration
                hit = self.fight._collision().cast_ray(orb.position, orb.direction, distance)
                if hit is not None:
                    # Keep the visibility origin on the incident side of the surface.
                    distance = max(0.0, hit.distance - 0.001)
                orb.position = tuple(
                    p + v * distance for p, v in zip(orb.position, orb.direction, strict=True)
                )
                orb.left -= duration
                if hit is not None or orb.left <= 1e-9:
                    orb.requested = at
