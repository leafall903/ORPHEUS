"""Original Halt target graph inputs; world pose and lifecycle remain reconstructed."""

import json
import math
import os
from pathlib import Path

from ow174.game.mover import MOD_OPS, statescript_mods
from ow174.game.script import expr, graph, target_visibility
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


def native(world, cfg, instance):
    if (instance is None or instance.graph.index != 0xCCE
            or cfg.get("$") != "STU_71A87E92"):
        return NotImplemented
    entity = expr.to_entity(instance.evaluate(cfg.get("m_entity")))
    if entity != instance.component.entity:
        return NotImplemented
    # Explicit authoritative own-pose bridge, not arbitrary native transform,
    # hardpoint or foreign entity support.
    match = getattr(world, "match", None)
    position = match.script_body_position(entity, entity) if match is not None else None
    if position is None:
        position = getattr(world, "owner_position", None)
    if position is None:
        position = world.position(entity)
    return position if isinstance(position, expr.Vec3) else NotImplemented


def npc_movement_mods(instance):
    component = instance.component
    mods = statescript_mods(component)
    if any(root.graph.index == 0x1C7 for root in component.instances.values()):
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
