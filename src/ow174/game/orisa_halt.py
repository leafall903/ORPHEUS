"""Original Halt target graph inputs; world pose and lifecycle remain reconstructed."""

import json
import math
import os
from dataclasses import dataclass
from pathlib import Path

from ow174.game.mover import MOD_OPS, statescript_mods
from ow174.game.script import expr, graph, play, runtime, target_visibility
from ow174.paths import DATA_DIR


def _graph(index):
    path = Path(os.environ.get("ORPHEUS_ORISA_HALT", DATA_DIR / "orisa_halt_174.json"))
    if not path.is_file():
        return graph.graph(index)
    data = json.loads(path.read_text(encoding="utf-8"))
    if (data.get("schema") != "orpheus-orisa-halt-v1"
            or data.get("build") != "1.74.0.0.104319"):
        raise ValueError("Halt metadata has wrong schema/build")
    record = next((g for g in data["graphs"] if g["index"] == index), None)
    if record is None:
        raise ValueError(f"Halt supplement lacks original{index:04X}")
    return graph.Graph(record)


def control_graph():
    return _graph(0xCCE)


def query_graph():
    return _graph(0xCCC)


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


@dataclass(frozen=True)
class ExplosionRequest:
    entity: int
    start_ms: int
    position: expr.Vec3
    strength: float
    radius: float
    parameter: float
    interval: float


@runtime.state_class("STUStatescriptStatePhysicsExplosion")
class HaltPhysicsExplosion(runtime.State):
    def on_enter(self):
        world = self.instance.component.world
        if isinstance(world, PullQuery):
            world.dispatch_explosion(self)
        elif (self.instance.graph.index, self.node.pos) == (0x0636, 64):
            from ow174.game import ice_wall

            ice_wall.pillar_physics_explosion(self)
        elif (self.instance.graph.index, self.node.pos) in ((0x0F7C, 52), (0x0F7C, 54)):
            from ow174.game import moira

            moira.fade_physics_explosion(self)


class PullQuery(runtime.World):
    """Evaluate the authored radius and request around the static candidate scan."""

    def __init__(self, definition, orb, origin, caster, *, explosion_sink=None):
        super().__init__()
        self.explosion_sink = explosion_sink
        self.origin = expr.Vec3(*origin)
        self.caster = expr.Entity(caster)
        self.context = play.InvocationContext(orb, orb, caster)
        component = runtime.Component(orb, world=self, strict=True)
        self.instance = component.create(definition, init=False)
        self.track = definition.node(1)
        for position in (25, 26, 31):
            setter = definition.node(position)
            self.instance.write(expr.lvalue(setter.field("m_out_Var")),
                                self.instance.evaluate(setter.field("m_value")))
        self.radius = expr.to_float(self.instance.evaluate(self.track.field("m_radius")))

    def native(self, cfg, instance):
        kind = cfg.get("$")
        if kind == "STU_71A87E92":
            entity = instance.evaluate(cfg.get("m_entity"))
            if expr.to_entity(entity) == instance.component.entity:
                return self.origin
        if kind == "STU_2B2E2F6D" and cfg.get("m_7BDB02D1") == 2:
            entity = instance.evaluate(cfg.get("m_81A364DE"))
            if expr.to_entity(entity) == instance.component.entity:
                return self.caster
        return super().native(cfg, instance)

    def dispatch_explosion(self, state):
        if self.explosion_sink is None:
            return
        entity = state.instance.component.entity
        position_owner = state.evaluate(state.node.field("m_position"), watch=False)
        if expr.to_entity(position_owner) != entity:
            return
        strength, radius, parameter, interval = (
            expr.to_float(state.evaluate(state.node.field(field), watch=False))
            for field in ("m_6EFD110C", "m_radius", "m_65438EEB", "m_D80AEDB1")
        )
        self.explosion_sink(ExplosionRequest(entity, state.start, self.origin,
                                            strength, radius, parameter, interval))

    def start_tail(self, tick):
        """Run the authored post-query states on the orb's simulation clock."""
        instance = self.instance
        component = instance.component
        component.frame = tick
        component.now = tick * component.frame_ms
        instance.init()
        # The match already completed TrackTargets and submitted node 12.
        # Run only its outgoing branch. The explosion dispatches on entry.
        instance.queue.clear()
        component.strict = False
        instance.follow(instance.graph.node(12), "m_outPlug")
        component.run_now()

    def complete(self, targets):
        links = self.track.links("m_transitionPlug[0]")
        if len(links) != 1:
            return None
        position, plug = links[0]
        node = self.instance.graph.node(position)
        if node is None or node.cls != "STUStatescriptActionPlayScript" or plug != "m_inPlug":
            return None
        output = self.track.field("m_8A2905A0")
        self.instance.write(expr.lvalue(output), tuple(expr.Entity(target) for target in targets))
        # Resolve the linked action's request. Its output also starts detached
        # states whose native lifecycle is outside this immediate pull bridge.
        resource = play.resolve_resource(self.instance, node)
        if resource is None:
            return None
        return play.resolve_request(self.instance, node, resource, context_provider=lambda: self.context)


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

def npc_movement_mods(pull):
    """Evaluate the shared movement consumer for NPC bodies missing that state."""
    instance = pull.script
    component = instance.component
    mods = statescript_mods(component)
    wait = instance.states.get(instance.graph.node(5).state)
    if wait is None or not wait.active or any(
        root.graph.index == 0x1C7 for root in component.instances.values()
    ):
        return mods
    common = graph.graph(0x1C7)
    if common is None:
        return mods
    state = common.node(152)
    for slot, mod in enumerate(state.field("m_BC5E91CF")):
        op, _ = MOD_OPS[mod["$"]]
        value = expr.to_float(instance.evaluate(mod["m_9B7A63EA"]))
        priority = expr.to_float(instance.evaluate(mod.get("m_priority")))
        mods[(instance.id, state.state, slot)] = (mod["m_A9561CA0"], op, value, priority)
    return mods
