"""Host the selected 104319 Mercy staff effects and their Valkyrie children."""

import math
from dataclasses import dataclass

from ow174.game import mercy
from ow174.game.script import expr, graph, message_receive, runtime

HEAL, BOOST = 0x04A0000000000019, 0x04A0000000000016
CHAIN_HEAL, CHAIN_BOOST = 0x04A00000000002CC, 0x04A00000000002CD
GRAPHS = {HEAL: 0x0079, BOOST: 0x006C, CHAIN_HEAL: 0x10DC, CHAIN_BOOST: 0x10DD}
START = 0x024000000000000F


def _entities(value):
    if isinstance(value, expr.Entity):
        return (value.id,)
    if isinstance(value, tuple):
        return tuple(dict.fromkeys(entity for item in value for entity in _entities(item)))
    return ()


class EffectWorld(runtime.World):
    def __init__(self, match, source, owner):
        super().__init__(0x02E0000000000004, match.game_map.mode_guid)
        self.match, self.source, self.owner = match, source, owner

    def entity_var(self, entity, var):
        target = next((t for t in self.match.combat._targets() if t.entity == entity), None)
        script = getattr(target.owner, "body_script", None) if target is not None else None
        if script is not None:
            slot = script.component.entity_var(var, create=False)
            return slot.value() if slot is not None else None
        return super().entity_var(entity, var)

    def game_message_subject(self, instance, node, instigator):
        return self.source  # declared original instigator for child effect42

    def position(self, entity):
        target = next((t for t in self.match.combat._targets() if t.entity == entity), None)
        return expr.Vec3(*target.feet) if target is not None else super().position(entity)

    def native(self, cfg, instance):
        cls = cfg.get("$")
        if cls == "STU_1FF67CA6":
            return expr.Entity(0)  # no selected local viewer in this authoritative host
        if cls == "STU_D3EB8D87":
            return False  # declared neutral healing gate; native status meaning remains open
        if cls == "STU_4049CF77" and expr.guid_of(cfg.get("m_F00C2F3D")) & 0xFFFF == 0x2162:
            return 1.0  # declared neutral received-healing multiplier, not a general stat service
        if cls in ("STU_32E6E4D8", "STU_7D7D2470"):
            entity = expr.to_entity(instance.evaluate(cfg.get("m_entity")))
            target = next((t for t in self.match.combat._targets() if t.entity == entity), None)
            if target is None:
                return 0.0
            vitals = self.match.combat.vitals_of(target)
            return vitals.total if cls == "STU_32E6E4D8" else sum(vitals.most.values())
        return super().native(cfg, instance)

    def tracked_targets(self, state):
        valid = ((state.instance.graph.index == 0x0079 and state.index == 19)
                 or (state.instance.graph.index == 0x006C and state.index == 12))
        if not valid:
            return NotImplemented
        from ow174.game.combat import capsule_visible

        fight = self.match.combat
        targets = {t.entity: t for t in fight._targets()}
        source, anchor = targets.get(self.source), targets.get(self.owner)
        if (source is None or anchor is None or fight.vitals_of(source).dead
                or fight.vitals_of(anchor).dead or source.team != anchor.team
                or self.match.game_map.free_for_all):
            return ()
        radius = expr.to_float(state.evaluate(state.node.field("m_radius"), watch=False))
        centre = (anchor.feet[0], anchor.feet[1] + anchor.height * 0.5, anchor.feet[2])
        found = []
        for target in targets.values():
            if (target.entity in (self.source, self.owner) or target.team != source.team
                    or fight.vitals_of(target).dead):
                continue
            point = (target.feet[0], target.feet[1] + target.height * 0.5, target.feet[2])
            distance = sum((a - b) ** 2 for a, b in zip(centre, point, strict=True))
            a, b = target.segment()
            if (distance <= radius * radius
                    and capsule_visible(fight._collision(), centre, a, b, target.radius)):
                found.append((distance, target.entity))
        return tuple(entity for _, entity in sorted(found))


@dataclass
class Attachment:
    key: tuple
    source: int
    target: int
    anchor: int
    definition: int
    parent_key: tuple | None
    component: runtime.Component
    instance: runtime.Instance
    parameters: tuple
    last_frame: int


class Effects:
    def __init__(self, match):
        self.match = match
        self.attachments = {}
        self.valkyrie_deadlines = {}

    def observe_valkyrie(self, script, tick):
        from ow174.game.combat import TICK

        for instance in script.component.instances.values():
            if instance.graph.index != mercy.VALKYRIE:
                continue
            profile = instance.states.get(15)
            remaining = expr.to_float(instance.find_var(expr.INSTANCE, 215).value())
            if (profile is not None and profile.active and remaining > 0
                    and expr.truthy(script.component.entity_var(842).value())):
                activation = instance.id, profile.counter
                recorded = self.valkyrie_deadlines.get(script.entity)
                if recorded is None or recorded[0] != activation:
                    self.valkyrie_deadlines[script.entity] = activation, tick + math.ceil(remaining / TICK)
                return
        self.valkyrie_deadlines.pop(script.entity, None)

    def chaining(self, source):
        deadline = self.valkyrie_deadlines.get(source)
        return deadline is not None and self.match.combat.tick < deadline[1]

    def retire(self, entity):
        self.valkyrie_deadlines.pop(entity, None)
        for key, item in list(self.attachments.items()):
            if entity in (item.source, item.target, item.anchor):
                self._remove(key)

    def prune(self, entities):
        for entity in tuple(self.valkyrie_deadlines):
            if entity not in entities:
                del self.valkyrie_deadlines[entity]
        for key, item in list(self.attachments.items()):
            if (any(entity not in entities for entity in (item.source, item.target, item.anchor))
                    or (item.parent_key is not None and not self.chaining(item.source))):
                self._remove(key)

    def _remove(self, key):
        for child_key, child in list(self.attachments.items()):
            if child.parent_key == key:
                self._remove(child_key)
        item = self.attachments.pop(key, None)
        if item is not None:
            item.component.destroy(item.instance)

    def _root_valid(self, item):
        fight = self.match.combat
        source = next((p for p in self.match.players if p.has_body and p.body == item.source), None)
        if (source is None or source.body_script is None or source.gone
                or fight.is_dead(source) or fight.is_stunned(source)):
            return False
        if item.parent_key is not None:
            parent = self.attachments.get(item.parent_key)
            if parent is None or not self._root_valid(parent):
                return False
            state_index = 19 if parent.definition == HEAL else 12
            state = parent.instance.states.get(state_index)
            return (state is not None and state.active
                    and item.target in parent.component.world.tracked_targets(state)
                    and self.chaining(item.source)
                    and expr.truthy(source.body_script.component.entity_var(842).value()))
        instance = source.body_script.component.instances.get(item.key[1])
        state = instance.states.get(item.key[2]) if instance is not None else None
        hold = instance.states.get(5) if instance is not None else None
        return (state is not None and state.active and not state.ending and state.counter == item.key[3]
                and item.target in _entities(state.evaluate(state.node.field("m_targets"), watch=False))
                and hold is not None and hold.active and not hold.ending
                and item.target in mercy.query(fight, source, hold))

    def _touch(self, source, anchor, target, definition, key, parent_key, parameters, frame, frame_ms):
        binding = graph.effect_script(definition)
        if binding is None or definition not in GRAPHS:
            return None
        model = graph.graph(binding["graph"])
        if model is None or model.index != GRAPHS[definition]:
            return None
        item = self.attachments.get(key)
        if item is None:
            component = runtime.Component(target, frame_ms, EffectWorld(self.match, source, target))
            component.frame, component.now = frame, frame * frame_ms
            instance = component.create(model)
            instance.set_source_entity(target)
            caller = source if parent_key is None else anchor
            packet = message_receive.Packet(START, caller, source, parameters)
            if message_receive.receive(instance, packet) != 1:
                component.destroy(instance)
                return None
            item = Attachment(key, source, target, anchor, definition, parent_key,
                              component, instance, parameters, frame - 1)
            self.attachments[key] = item
        elif item.parameters != parameters:
            # Refresh the original000F parameter destinations without restarting
            # its active states/counters. General effect parameter semantics stay open.
            values = dict(parameters)
            for entry in item.instance.graph.entries:
                if entry is None or entry.cls != "STUStatescriptEntryGameMessage":
                    continue
                if expr.guid_of(entry.field("m_gameMessage.m_gameMessage")) != START:
                    continue
                for row in entry.field("m_params") or ():
                    identifier = expr.guid_of(row.get("m_B5051BCE"))
                    if identifier in values:
                        item.instance.write(expr.lvalue(row.get("m_out_Var")), values[identifier])
            item.parameters = parameters
        if item.last_frame != frame:
            item.component.run_frame(frame)
            item.last_frame = frame
        return item

    def advance(self, script, frame):
        """Run active resource trees once per simulated source command frame."""
        source = script.entity
        active, reports = set(), []

        def attach(state, anchor, parent_key=None):
            asset = state.evaluate(state.node.field("m_A76A8CF6"), watch=False)
            if not isinstance(asset, expr.Asset) or asset.guid not in GRAPHS:
                return
            if graph.effect_script(asset.guid) is None:
                return
            parameters = tuple((expr.guid_of(row.get("m_B5051BCE")),
                                state.evaluate(row.get("m_value"), watch=False))
                               for row in state.node.field("m_params") or ())
            for target in _entities(state.evaluate(state.node.field("m_targets"), watch=False)):
                key = ((source, state.instance.id, state.index, state.counter, target) if parent_key is None
                       else (*parent_key, state.instance.id, state.index, state.counter, target))
                item = self._touch(source, anchor, target, asset.guid, key, parent_key,
                                   parameters, frame, script.frame_ms)
                if item is None:
                    continue
                if not self._root_valid(item):
                    self._remove(key)
                    continue
                active.add(key)
                for child in list(item.instance.states.values()):
                    if not child.active:
                        continue
                    if child.node.cls == "STU_431F3FCC" and parent_key is None and self.chaining(source):
                        attach(child, item.target, key)
                    if child.node.cls == "STUStatescriptStateModifyHealth":
                        modify = child.node.field("m_modifyHealth") or {}
                        instigator = expr.to_entity(child.evaluate(modify.get("m_instigator"), watch=False))
                        amount = expr.to_float(child.evaluate(modify.get("m_amount"), watch=False))
                        if instigator == source and amount > 0:
                            reports.append((item.target, amount * script.frame_ms / 1000.0))

        if not script.component.owner_dead:
            for instance in list(script.component.instances.values()):
                if instance.graph.index == 0x006B:
                    for state in list(instance.states.values()):
                        if state.active and state.node.cls == "STU_431F3FCC":
                            attach(state, source)
        for key, item in list(self.attachments.items()):
            if item.source == source and key not in active:
                self._remove(key)
        return reports

    def boost(self, source, target):
        bonus = 0.0
        for item in self.attachments.values():
            if (item.source != source or item.target != target
                    or item.definition not in (BOOST, CHAIN_BOOST) or not self._root_valid(item)):
                continue
            if any(s.active and s.node.cls == "STUStatescriptStateCombatModFilter"
                   for s in item.instance.states.values()):
                bonus = max(bonus, expr.to_float(item.instance.find_var(graph.INSTANCE, 174).value()))
        return max(0.0, bonus)
