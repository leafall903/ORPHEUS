"""Explicit reconstructed hosts for Mercy's extracted gameplay-effect scripts.

Each attachment runs its real0079/006C/10DC/10DD graph and000F message bindings.
Owner-command clocks, independent effect VMs, neutral healing inputs and sphere/
static-LOS policy are declared host bindings, not full native effect ownership,
filter flags, status modifiers, replication or client-presentation acceptance.
"""

from dataclasses import dataclass

from ow174.game.script import expr, graph, message_receive, regeneration, runtime, support

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


def staff_boost_selected(instance):
    # 006B's authored boost attachment and primary/secondary logical buttons.
    effect = instance.states.get(35)
    primary, secondary = instance.states.get(46), instance.states.get(47)
    return (secondary is not None and secondary.held() and not secondary.blocked()
            and ((effect is not None and effect.active) or primary is None or not primary.held()))


def boost_target_at(fight, target, frame):
    if frame < fight.replacement_ticks.get(target.entity, frame):
        return None
    vitals = fight.vitals_of(target)
    if not vitals.dead:
        return target
    if vitals.death_tick is not None and frame < vitals.death_tick:
        return fight.seen(target, frame)
    return None


class EffectWorld(runtime.World):
    def __init__(self, match, source, owner):
        super().__init__(0x02E0000000000004, match.game_map.mode_guid)
        self.match, self.source, self.owner = match, source, owner

    def entity_var(self, entity, var):
        target = next((t for t in self.match.combat._targets() if t.entity == entity), None)
        script = getattr(target.owner, "body_script", None) if target is not None else None
        if script is not None:
            if entity == self.source and var == 842 and self.hero == script.hero:
                # 0079 reads the caster's phase while resolving the source command.
                expiry = script.world.historical_valkyrie_deadline()
                if expiry is not None and script.component.now < expiry:
                    return True
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

    def linked_tracked_targets(self, state):
        targets = self.track_targets(state)
        return targets if targets is NotImplemented else tuple(expr.Entity(entity) for entity in targets)

    def track_targets(self, state):
        valid = ((state.instance.graph.index == 0x0079 and state.index == 19)
                 or (state.instance.graph.index == 0x006C and state.index == 12))
        if not valid:
            return NotImplemented
        from ow174.game.combat import capsule_visible

        fight = self.match.combat
        targets = {t.entity: t for t in fight._targets()}
        source, anchor = targets.get(self.source), targets.get(self.owner)
        if (source is None or anchor is None or fight.vitals_of(source).dead
                or source.team != anchor.team
                or self.match.game_map.free_for_all):
            return ()
        radius = expr.to_float(state.evaluate(state.node.field("m_radius"), watch=False))
        frame = state.instance.component.frame
        script = getattr(source.owner, "body_script", None)
        expiry = (script.world.historical_valkyrie_deadline()
                  if state.instance.graph.index == 0x006C and script is not None else None)
        # A delayed boost frame can precede a primary or secondary recipient's death.
        historical = expiry is not None and frame < fight.tick and state.now < expiry
        if historical:
            anchor = boost_target_at(fight, anchor, frame)
            if anchor is None:
                return ()
        elif fight.vitals_of(anchor).dead:
            return ()
        centre = (anchor.feet[0], anchor.feet[1] + anchor.height * 0.5, anchor.feet[2])
        found = []
        for target in targets.values():
            if target.entity in (self.source, self.owner) or target.team != source.team:
                continue
            if historical:
                target = boost_target_at(fight, target, frame)
                if target is None:
                    continue
            elif fight.vitals_of(target).dead:
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
        # An empty row means Valkyrie without a beam. None closes the phase.
        self.boost_frames: dict[int, dict[int, dict[int, float] | None]] = {}

    def retire(self, entity, *, boost_end_frame=None):
        if boost_end_frame is None:
            self.boost_frames.pop(entity, None)
        elif (frames := self.boost_frames.get(entity)):
            # Death ends live effects but leaves already simulated pre-death rows.
            for frame in tuple(frames):
                if frame >= boost_end_frame:
                    del frames[frame]
            frames[boost_end_frame] = None
        for frames in self.boost_frames.values():
            for bonuses in frames.values():
                if bonuses is not None:
                    bonuses.pop(entity, None)
        for key, item in list(self.attachments.items()):
            if entity in (item.source, item.target, item.anchor):
                self._remove(key)

    def retire_valkyrie_children(self, source, frame):
        self.boost_frames.setdefault(source, {})[frame] = None
        for key, item in tuple(self.attachments.items()):
            if item.source == source and item.definition in (CHAIN_HEAL, CHAIN_BOOST):
                self._remove(key)

    def prune(self, entities):
        first = self._first_boost_frame()
        for source, frames in tuple(self.boost_frames.items()):
            for frame in tuple(frames):
                if frame < first:
                    del frames[frame]
            if source not in entities or not frames:
                del self.boost_frames[source]
        for key, item in list(self.attachments.items()):
            if any(entity not in entities for entity in (item.source, item.target, item.anchor)):
                self._remove(key)

    def _first_boost_frame(self):
        from ow174.game.combat import HISTORY

        return self.match.combat.tick - HISTORY

    def _remove(self, key):
        for child_key, child in list(self.attachments.items()):
            if child.parent_key == key:
                self._remove(child_key)
        item = self.attachments.pop(key, None)
        if item is not None:
            item.component.destroy(item.instance)

    def _root_valid(self, item, *, historical_valkyrie=False):
        fight = self.match.combat
        source = next((p for p in self.match.players if p.has_body and p.body == item.source), None)
        if source is None or source.body_script is None or fight.is_dead(source):
            return False
        if item.parent_key is not None:
            parent = self.attachments.get(item.parent_key)
            if parent is None or not self._root_valid(parent):
                return False
            state_index = 19 if parent.definition == HEAL else 12
            state = parent.instance.states.get(state_index)
            return (state is not None and state.active
                    and item.target in parent.component.world.track_targets(state)
                    and (historical_valkyrie
                         or expr.truthy(source.body_script.component.entity_var(842).value())))
        instance = source.body_script.component.instances.get(item.key[1])
        state = instance.states.get(item.key[2]) if instance is not None else None
        hold = instance.states.get(5) if instance is not None else None
        return (state is not None and state.active and state.counter == item.key[3]
                and item.target in _entities(state.evaluate(state.node.field("m_targets"), watch=False))
                and hold is not None and hold.active
                and item.target in fight.script_targets(source.body, hold))

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
        active, handled, reports = set(), set(), []
        expiry = script.world.historical_valkyrie_deadline()
        historical = expiry is not None and script.component.now < expiry
        valkyrie = historical or expr.truthy(script.component.entity_var(842).value())
        bonuses = {}
        temporary = []
        fight = self.match.combat
        late = historical and frame < fight.tick

        def attach(state, anchor, parent_key=None, depth=0):
            if depth > 4:
                raise ValueError("Mercy support effect recursion exceeded extracted closure")
            asset = state.evaluate(state.node.field("m_A76A8CF6"), watch=False)
            if not isinstance(asset, expr.Asset) or asset.guid not in GRAPHS:
                return
            if graph.effect_script(asset.guid) is None:
                return
            historical_child = historical and asset.guid in (CHAIN_HEAL, CHAIN_BOOST)
            if parent_key is None:
                handled.add((state.instance.id, state.index))
            parameters = tuple((expr.guid_of(row.get("m_B5051BCE")),
                                state.evaluate(row.get("m_value"), watch=False))
                               for row in state.node.field("m_params") or ())
            for target in _entities(state.evaluate(state.node.field("m_targets"), watch=False)):
                key = ((source, state.instance.id, state.index, state.counter, target) if parent_key is None
                       else (*parent_key, state.instance.id, state.index, state.counter, target))
                vitals = fight.vitals.get(target) if late and asset.guid == BOOST else None
                if historical_child or (vitals is not None and vitals.dead):
                    temporary.append(key)
                item = self._touch(source, anchor, target, asset.guid, key, parent_key,
                                   parameters, frame, script.frame_ms)
                if item is None:
                    continue
                if not self._root_valid(item, historical_valkyrie=historical_child):
                    self._remove(key)
                    continue
                active.add(key)
                if valkyrie and item.definition in (BOOST, CHAIN_BOOST):
                    bonus = self._filter_bonus(item)
                    if bonus > 0:
                        bonuses[target] = max(bonuses.get(target, 0.0), bonus)
                for child in list(item.instance.states.values()):
                    if not child.active:
                        continue
                    if child.node.cls == "STU_431F3FCC":
                        attach(child, item.target, key, depth + 1)
                    if child.node.cls == "STUStatescriptStateModifyHealth":
                        modify = child.node.field("m_modifyHealth") or {}
                        instigator = expr.to_entity(child.evaluate(modify.get("m_instigator"), watch=False))
                        amount = expr.to_float(child.evaluate(modify.get("m_amount"), watch=False))
                        if instigator == source and amount > 0:
                            reports.append((frame, item.target, amount * script.frame_ms / 1000.0,
                                            support.healing_tags(child)))

        try:
            if not script.component.owner_dead:
                for instance in list(script.component.instances.values()):
                    if instance.graph.index == 0x006B:
                        for state in list(instance.states.values()):
                            if state.active and state.node.cls == "STU_431F3FCC":
                                attach(state, source)
        finally:
            # Late commands evaluate the original child without retaining it in the world.
            for key in temporary:
                self._remove(key)
        # Attack lookup and history pruning use world ticks, while the attached
        # VMs above must keep the caster's owner frame. Freeze the source event's
        # immutable birth mapping rather than its latest input-arrival pose.
        world_frame = regeneration.healing_world_tick(script, frame)
        if world_frame is None:
            world_frame = frame
        if valkyrie and world_frame >= self._first_boost_frame():
            self.boost_frames.setdefault(source, {})[world_frame] = bonuses
        elif not valkyrie and source in self.boost_frames:
            frames = self.boost_frames[source]
            if frames[max(frames)] is not None:
                frames[world_frame] = None
        for key, item in list(self.attachments.items()):
            if item.source == source and key not in active:
                self._remove(key)
        return handled, reports

    @staticmethod
    def _filter_bonus(item):
        if any(state.active and state.node.cls == "STUStatescriptStateCombatModFilter"
               for state in item.instance.states.values()):
            return expr.to_float(item.instance.find_var(graph.INSTANCE, 174).value())
        return 0.0

    def historical_boosts(self, source, frame, *, pending=False):
        frames = self.boost_frames.get(source)
        if not frames or frame is None or frame >= self.match.combat.tick:
            return None
        if not pending and frame < self._first_boost_frame():
            return None
        recorded = max((recorded for recorded in frames if recorded <= frame), default=None)
        return frames[recorded] if recorded is not None else None

    def boost(self, source, target, *, frame=None):
        if (bonuses := self.historical_boosts(source, frame)) is not None:
            return bonuses.get(target, 0.0)
        bonus = 0.0
        for item in self.attachments.values():
            if (item.source != source or item.target != target
                    or item.definition not in (BOOST, CHAIN_BOOST) or not self._root_valid(item)):
                continue
            bonus = max(bonus, self._filter_bonus(item))
        return bonus
