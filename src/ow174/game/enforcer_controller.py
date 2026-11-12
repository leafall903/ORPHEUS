"""Host authored NPC target-message branches in a live match."""

import gzip
import json
import os
from functools import lru_cache
from pathlib import Path

from ow174.game.assassin import HERO as ASSASSIN
from ow174.game.npc_context import ENFORCER, HEAVY_ASSAULT
from ow174.game.sniper_retreat import HERO as SNIPER
from ow174.game.script import expr, graph, message_receive, runtime

GRAPH = 0x13E9
TROOPER, TROOPER_GRAPH = 0x02E00000000001AC, 0x11E3
SNIPER_GRAPH, SNIPER_BODY_GATE = 0x1138, 25
HEAVY_GRAPH, HEAVY_BODY_GATE = 0x1251, 21
TARGET_MESSAGE = 0x0240000000000079
REASON, TARGET = 0x0A40000000000057, 0x0A40000000000059
SUPPLEMENT = "ORPHEUS_NPC_CONTROLLER_SUPPLEMENT"
HOSTS = {
    ENFORCER: ("enforcer_controller", GRAPH, 25, 7, (expr.INSTANCE, 536)),
    TROOPER: ("trooper_controller", TROOPER_GRAPH, 1, 20, (expr.ENTITY, 1650)),
    SNIPER: ("sniper_controller", SNIPER_GRAPH, 1, 21, (expr.ENTITY, 1650)),
    HEAVY_ASSAULT: ("heavy_controller", HEAVY_GRAPH, 1, HEAVY_BODY_GATE, (expr.ENTITY, 1650)),
}
RANGE_NODES = {TROOPER_GRAPH: 46, SNIPER_GRAPH: 40}
BODY_GATES = {SNIPER_GRAPH: SNIPER_BODY_GATE, HEAVY_GRAPH: HEAVY_BODY_GATE}


@lru_cache(maxsize=4)
def _definition(path, index):
    if path is None:
        return None
    with gzip.open(path, "rt", encoding="utf-8") as file:
        rows = json.load(file)["graphs"]
    record, = (row for row in rows if row["index"] == index)
    return graph.Graph(record)


class TargetWorld(runtime.World):
    def __init__(self, fight, body, hero):
        super().__init__(hero, fight.match.game_map.mode_guid)
        self.fight = fight
        self.body = body
        self.selected = None

    def filtered_body_health(self, entity, kind, tag):
        if entity != self.body or kind or tag:
            return NotImplemented
        vitals = self.fight.vitals.get(entity)
        return vitals.total if vitals is not None else None

    def target_record_scalar(self, owner, target):
        # The known scalar API supplies a per-target record. The reconstructed
        # selector uses a nonzero record only for its current visible target.
        if owner != _owner(self.body):
            return NotImplemented
        return 1.0 if target == self.selected else 0.0

    def entity_var(self, entity, var):
        if self.hero in (SNIPER, HEAVY_ASSAULT) and (entity, var) == (self.body, 888):
            bot = next((bot for bot in self.fight.match.bots if bot.entity == self.body), None)
            if bot is not None and bot.body_script is not None:
                slot = bot.body_script.component.entity_var(var, create=False)
                return slot.value() if slot is not None else None
            return None
        return super().entity_var(entity, var)

    def native(self, cfg, instance):
        _, index, health_node, _, _ = HOSTS[self.hero]
        if instance is not None and instance.graph.index == index:
            if cfg.get("$") == "STU_0AC15F78":
                health = instance.graph.node(health_node).field("m_condition")["m_configVars"][0]
                if cfg is health["m_entity"]:
                    return expr.Entity(self.body)
            if index in BODY_GATES and cfg.get("$") == "STU_00707E1E":
                body, health = instance.graph.node(BODY_GATES[index]).field("m_condition")["m_configVars"]
                if cfg is body or cfg is health["m_entity"]:
                    return expr.Entity(self.body)
            if index in RANGE_NODES and cfg.get("$") == "STU_47077281":
                condition = instance.graph.node(RANGE_NODES[index]).field("m_condition")
                if cfg is condition["m_configVars"][0]:
                    return expr.EVALUATION_FAILED
        return super().native(cfg, instance)


def require_trooper_controller():
    configured = os.environ.get(SUPPLEMENT)
    if _definition(Path(configured).resolve() if configured else None, TROOPER_GRAPH) is None:
        raise ValueError("authored coordination requires the Trooper controller supplement")


def _owner(body):
    return 0xB0000000 | (body & 0x0FFFFFFF)


def admit(fight, bot, selected):
    """Return whether the live authored target branch admits the selected shot."""
    field, graph_id, _, gate_node, target_slot = HOSTS[bot.hero]
    component = getattr(bot, field, None)
    if component is None:
        configured = os.environ.get(SUPPLEMENT)
        definition = _definition(Path(configured).resolve() if configured else None, graph_id)
        if definition is None:
            return selected is not None
        world = TargetWorld(fight, bot.entity, bot.hero)
        component = runtime.Component(_owner(bot.entity), world=world)
        setattr(bot, field, component)
        component.frame = fight.tick - 1
        component.now = component.frame * component.frame_ms
        component.create(definition)
    instance, = component.instances.values()
    body_gate = BODY_GATES.get(graph_id)
    if body_gate is not None:
        intro = instance.states.get(instance.graph.node(body_gate).state)
        if intro is not None and intro.active:
            intro.dependency_changed()
    entity = selected[1] if selected is not None else None
    if entity != component.world.selected:
        component.world.selected = entity
        if entity is not None:
            packet = message_receive.Packet(TARGET_MESSAGE, bot.entity, bot.entity, (
                (REASON, 0), (TARGET, expr.Entity(entity)),
            ))
            if message_receive.receive(instance, packet) != 1:
                raise ValueError("unexpected NPC target receiver")
        gate = instance.states.get(instance.graph.node(gate_node).state)
        if gate is not None and gate.active and gate_node != body_gate:
            gate.dependency_changed()
    if component.frame < fight.tick:
        component.run_frame(fight.tick)
    else:
        component.run_now()
    if body_gate is not None and body_gate != gate_node:
        intro = instance.states.get(instance.graph.node(body_gate).state)
        if intro is None or not intro.active or not intro.current:
            return False
    gate = instance.states.get(instance.graph.node(gate_node).state)
    latched = instance.find_var(*target_slot, create=False)
    return bool(entity is not None and gate is not None and gate.active and gate.current
                and latched is not None and latched.value() == expr.Entity(entity))


def retire(bot):
    host = HOSTS.get(bot.hero)
    if bot.hero == ASSASSIN:
        field = "assassin_controller"
    elif host is not None:
        field = host[0]
    else:
        return
    component = getattr(bot, field, None)
    if component is None:
        return
    delattr(bot, field)
    if bot.hero == ASSASSIN and component.strict:
        from ow174.game import assassin

        body = bot.body_script
        if body is not None and body.component.fault is None:
            assassin.stop(bot)
        world = component.world
        if getattr(world, "script_components", {}).get(component.entity) is component:
            if getattr(world, "auto_scene", False):
                for entity in tuple(world.entities):
                    world.unbind(entity)
                world.auto_scene = False
            else:
                world.unbind_script_component(component.entity, component)
    if component.fault is None:
        for instance in tuple(component.instances.values()):
            component.destroy(instance)
