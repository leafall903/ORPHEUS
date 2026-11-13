"""The NPC body's statescript, shared by all observers."""

import math

from ow174.game import assassin, observers
from ow174.game.bits import BitWriter
from ow174.game.bots import FIRING
from ow174.game.collision import NOT_GROUND
from ow174.game.content import PRACTICE_RANGE_MODE
from ow174.game.mover import mover_data
from ow174.game.script import body_owner, entity_link, entity_queries, expr, graph, play, runtime
from ow174.game.script.driver import BodyWorld, OWNER_HEALTH, ReplicatedBody, Snapshot, create_body_graphs, create_initial_graphs

DIED = 0x024000000000003E  # 003E.025, the body's authoritative death message
DEATH_VOLLEY_GRAPH = 0x2535
DEATH_VOLLEY_MODE_FLAG = 28300
ARCHIVES_MODIFIER_FLAGS = (DEATH_VOLLEY_MODE_FLAG, 34924)
RESET = 0x0240000000000013  # 0013.025, the placement's eligibility reset
FIRING_GRAPH = FIRING & 0xFFFF
PRACTICE_NAMEPLATE = 0x0D24
PRACTICE_NAMEPLATE_PARENT = 0x0C18
ENABLE_NAMEPLATE = 0x0240000000000C3C
NAMEPLATE_ENABLED = 8807
NAMEPLATE_NAME = 8812  # 226C.01C, 0D24's optional display name
PRACTICE_BOT_HEROES = frozenset(range(0x02E000000000016B, 0x02E000000000016F))
TROOPER_HERO = 0x02E00000000001AC
SNIPER_HERO = 0x02E00000000001B8
HEAVY_ASSAULT_HERO = 0x02E00000000001BA
ASSASSIN_HERO = 0x02E00000000001BB
ENFORCER_HERO = 0x02E00000000001CE
FLASHBANG_STACK_GRAPH = 0x224E
ASSASSIN_TARGET = 1650
ASSASSIN_CAPTURE_TARGET = 536
ASSASSIN_RANGE_VAR = 12018
ASSASSIN_VICTIM_VARS = (478, 2380)
PRACTICE_NAMEPLATE_FILTER = {"$": "STUConfigVarFilter", "m_84065FD1": 0x3FFFFFFF, "m_4EA686DB": 0x100}


def practice_nameplate(snapshot: Snapshot) -> int | None:
    for instance, (graph_id, parent) in snapshot.instances.items():
        if (
            graph_id == PRACTICE_NAMEPLATE and parent is not None and parent[1] == 3
            and snapshot.instances.get(parent[0], (None, None))[0] == PRACTICE_NAMEPLATE_PARENT
        ):
            return instance
    return None


def require_controller_graphs(record) -> None:
    """Reject missing literal graph dependencies before installing a controller namespace."""
    pending = [item['graph'] for item in record['graphs']]
    visited = set()

    def references(value):
        if isinstance(value, dict):
            for child in value.values():
                yield from references(child)
        elif isinstance(value, list):
            for child in value:
                yield from references(child)
        elif (asset := graph.guid(value)) is not None and asset >> 48 == 0x0580:
            yield asset & 0xFFFFFFFFFFFF

    while pending:
        index = pending.pop()
        if index in visited:
            continue
        definition = graph.graph(index)
        if definition is None:
            raise ValueError(f'NPC controller graph {index:04X} is missing')
        visited.add(index)
        pending.extend(references(definition.fields))
        for node in definition.nodes:
            pending.extend(references(node.fields))


class AssassinControllerWorld(runtime.World):
    """Reconstructed host services for the Assassin controller graphs."""

    def __init__(self, hero: int, mode: int, body: int, entity: int, match) -> None:
        super().__init__(hero, mode)
        self.mover = mover_data(body)
        self.body_entity = entity
        self.match = match
        self.button_edges: list[tuple[int, bool]] = []
        self.button_holders: set[tuple[int, int]] = set()
        self.movement_bits = 0
        self.candidates: tuple[expr.Entity, ...] = ()
        self.recovery_pending = False
        self.recovery_started = False

    def entity_var(self, entity: int, var: int):
        if self.match is not None and var in ASSASSIN_VICTIM_VARS:
            return self.match.combat.scripted_body_var(entity, var)
        return super().entity_var(entity, var)

    def send_message(self, target: int, message: int, params: dict, sender: int) -> None:
        if self.match is not None and target == self.body_entity and message == 0x02400000000009DA:
            bot = next((actor for actor in self.match.bots if actor.entity == target), None)
            script = bot.body_script if bot is not None else None
            if (script is not None and script.controller is not None
                    and script.controller.world is self and script.controller.entity == sender
                    and not script.controller.owner_dead and not script.component.owner_dead):
                self.recovery_pending |= bool(runtime.send_message(script.component, message, params, sender))
            return
        super().send_message(target, message, params, sender)

    def reselect_recovered_attack(
        self, controller: runtime.Component, body: runtime.Component, frame: int,
    ) -> None:
        """Reselect the attack after the authored recovery handshake completes."""
        if not self.recovery_pending:
            return
        body_phase = body.entity_var(13401, create=False)
        phase = body_phase.value() if body_phase is not None else None
        base_phase = body_phase.base if body_phase is not None else None
        # The 1157 release animation temporarily overlays the phase written by 13C3.
        if phase == 3 or base_phase == 3:
            self.recovery_started = True
        if not self.recovery_started or phase != 0 or base_phase != 0:
            return
        for instance in body.instances.values():
            if instance.graph.index == 0x1157:
                release = instance.states.get(instance.graph.node(31).state)
                if release is not None and release.active:
                    return
        if self.match.combat.stuns.get(self.body_entity, 0) > frame:
            return
        self.recovery_pending = self.recovery_started = False
        attack = next((i for i in controller.instances.values() if i.graph.index == 0x1158), None)
        if attack is None:
            return
        attack_phase = attack.find_var(runtime.INSTANCE_SCOPE, 59, create=False)
        root = attack.states.get(attack.graph.node(1).state)
        if attack_phase is not None and attack_phase.value() == 3 and root is not None and root.active:
            with controller.operation():
                root.end(False)
                root.begin()

    def sample_ground(self, collision, position) -> None:
        if collision is None or position is None:
            self.movement_bits = 0
            return
        data = self.mover
        x, y, z = position
        ground = collision.ground(x, z, y + expr.EPSILON, y - data.step_down)
        grounded = (ground is not None and not ground.surface & NOT_GROUND
                    and ground.normal[1] > math.cos(math.radians(data.max_slope)))
        self.movement_bits = 2 if grounded else 1

    def position(self, entity: int):
        if self.match is not None:
            targets = self.match.combat._targets()
            target = next((target for target in targets if target.entity == entity), None)
            if target is not None:
                return expr.Vec3(*target.feet)
        return super().position(entity)

    def native(self, cfg: dict, instance: runtime.Instance):
        if self.match is not None and instance is not None and instance.graph.index in (0x1158, 0x13C2):
            kind = cfg.get("$")
            if kind in ("STU_00707E1E", "STU_0AC15F78"):
                owner = cfg.get("m_00903303" if kind == "STU_00707E1E" else "m_entity")
                if instance.evaluate(owner) == expr.Entity(instance.component.entity):
                    return expr.Entity(self.body_entity)
            if kind == "STU_AC829876":
                selected = cfg.get("m_filter") or {}
                signature = (selected.get("m_84065FD1"), selected.get("m_4EA686DB"))
                target = instance.evaluate(cfg.get("m_entity"))
                if signature in ((262144, 304), (262144, 316), (1, 264)):
                    condition = selected.get("m_condition")
                    if signature == (262144, 316):
                        # The range switch checks this predicate before its
                        # unconditional subgraph links the projection slot.
                        condition = instance.graph.node(69).field("m_value")
                    return (target in self.candidates
                            and (signature == (262144, 304) or condition is None
                                 or expr.truthy(instance.evaluate(condition))))
            if kind in ("STU_9B7648C3", "STU_60146072"):
                entity = expr.to_entity(instance.evaluate(cfg.get("m_entity")))
                combat = self.match.combat
                body = next((target for target in combat._targets() if target.entity == entity), None)
                if body is None:
                    return 0.0
                vitals = combat.vitals_of(body)
                return expr.f32(vitals.total if kind == "STU_9B7648C3" else vitals.maximum)
            if kind == "STU_71A87E92":
                return self.position(expr.to_entity(instance.evaluate(cfg.get("m_entity"))))
            if kind == "STU_D379F515":
                owner = instance.evaluate(cfg.get("m_7826B991"))
                target = expr.to_entity(instance.evaluate(cfg.get("m_target")))
                return (owner == expr.Entity(instance.component.entity)
                        and self.match.combat.assassin_visible(self.body_entity, target))
        return super().native(cfg, instance)

    def selects_ai_behavior(self, instance: runtime.Instance) -> bool:
        return self.match is not None and instance.graph.index in (0x13C2, 0x1158)

    def refresh_candidates(self, controller: runtime.Component) -> None:
        if self.match is None:
            return
        tracker = next((i for i in controller.instances.values() if i.graph.index == 0x13C2), None)
        if tracker is None:
            return
        node = tracker.graph.node(4)
        reach = expr.to_float(tracker.evaluate(node.field("m_radius")))
        self.candidates = self.match.combat.assassin_candidates(self.body_entity, reach)
        slot = expr.lvalue(node.field("m_8A2905A0"))
        if not expr.same(tracker.find_var(*slot).value(), self.candidates):
            tracker.write(slot, self.candidates)
        target = controller.entity_var(ASSASSIN_TARGET, create=False)
        current = target.value() if target is not None else None
        if current in self.candidates:
            return
        if self.candidates:
            nominated = self.candidates[0]
            runtime.send_instance_message(tracker, 0x0240000000000A73, {0x5C5: nominated}, nominated.id)
        elif current:
            tracker.run_node(tracker.graph.node(16), None)

    def refresh_spatial_inputs(self, controller: runtime.Component) -> None:
        # The selected stacks cache native results in the VM. Re-evaluate them
        # before the next controller frame so moving bodies and walls are visible.
        with controller.operation():
            for instance in controller.instances.values():
                if instance.graph.index != 0x1158:
                    continue
                for position in (7, 68, 80):
                    state = instance.states.get(instance.graph.node(position).state)
                    if state is not None and state.active:
                        state.dependency_changed()

    def movement_flags(self, entity):
        resolver = getattr(self.match, "script_movement_flags", None)
        return resolver(entity, 0) if callable(resolver) else NotImplemented

    def movement_flag_projection(self, instance: runtime.Instance, node: graph.Node):
        if instance.graph.index == 0x1158 and node.pos == 19:
            return 3, self.movement_bits
        return None

    def controller_button(self, state: runtime.State, pressed: bool) -> None:
        if state.instance.graph.index != 0x1158 or state.node.pos != 12:
            return
        before = bool(self.button_holders)
        holder = (state.instance.id, state.index)
        if pressed:
            target = state.node.field("m_14EDDDA7") or {}
            owner = target.get("m_entity") or {}
            if (state.instance.component.owner_dead or target.get("$") != "STU_0AC15F78"
                    or owner.get("$") != "STU_B5A0CAF0"
                    or state.evaluate(state.node.field("m_7E2A700B"), watch=False) != 36
                    or state.evaluate(state.node.field("m_5E1D2E9C"), watch=False) is not False):
                return
            self.button_holders.add(holder)
        else:
            self.button_holders.discard(holder)
        after = bool(self.button_holders)
        if before != after:
            self.button_edges.append((36, after))


class BotWorld(runtime.World):
    """Resolve the supported authored predicates and input bindings for an NPC body."""

    @property
    def combat(self):
        return self.match.combat if self.match is not None else None

    def entity_possessor_context(self, entity):
        # NPC/DVa existing scoped native adapters do not declare component43 here.
        return NotImplemented

    body_button_allowed = BodyWorld.body_button_allowed
    movement_flags = BodyWorld.movement_flags

    def movement_flag_projection(self, instance, node):
        # DVa's controlled mover keeps its existing separate owner contract.
        if self.hero == 0x02E000000000007A:
            return None
        from ow174.game.script.driver import project_movement_flags
        return project_movement_flags(self, instance, node)

    def search_position(self, state):
        from ow174.game import sniper_reposition, sniper_retreat, sniper_special

        if sniper_retreat.scoped(self, state.instance) and state.node.pos == 39:
            return None  # Original automatic host resolves the active search after VM advance.
        result = sniper_reposition.primary_search(self, state)
        if result is not NotImplemented:
            return result
        return sniper_special.search(self, state)

    def position(self, entity):
        if self.match is not None and self.component is not None:
            value = self.match.combat.hooks.victim_position(self.component, entity)
            if value is not NotImplemented:
                return value
        if self.hero == 0x02E00000000001B8 and self.match is not None:
            value = self.match.script_body_position(entity, entity)
            if value is not None:
                return value
        return super().position(entity)

    _lookup_body_owner = BodyWorld._lookup_body_owner
    _lookup_entity_query = BodyWorld._lookup_entity_query
    _poll_entity_query = BodyWorld._poll_entity_query
    _body_component = BodyWorld._body_component
    script_component_present = BodyWorld.script_component_present
    entity_ruleset = BodyWorld.entity_ruleset
    _query_spatial = BodyWorld._query_spatial
    spatial_query_context = BodyWorld.spatial_query_context

    def body_owner_context(self, entity):
        if getattr(self, "body", None) == entity and self.match is not None:
            return self._body_owner_context
        return NotImplemented

    def entity_query_context(self, entity):
        if getattr(self, "body", None) == entity and self.match is not None:
            return self._entity_query_context
        return NotImplemented

    def entity_link_context(self, entity):
        if self.match is None or entity != getattr(self, "body", None):
            return NotImplemented
        return self._entity_link_context

    def _lookup_entity_link(self, entity):
        if self.match is None:
            return None
        for bot in self.match.bots:
            if bot.entity == entity:
                # Only a declared decoded component field is a known input.
                # Do not infer another body's pet relation from direct ownership.
                if bot.pet_owner is None:
                    return NotImplemented
                return entity_link.Target(entity, bot.pet_owner)
        if any(not p.gone and (p.entity == entity or (p.has_body and p.body == entity))
               for p in self.match.players):
            return NotImplemented
        return None

    def destroy_entity(self, instance, node):
        site = instance.graph.index, node.pos
        if self.match is None or site not in ((0x303B, 5), (0x303B, 6), (0x0B2F, 13)):
            return NotImplemented
        entity = expr.to_entity(instance.evaluate(node.field("m_entity")))
        bot = next((b for b in self.match.bots if b.entity == entity and b.owner
                    and b.entity == instance.component.entity), None)
        if bot is None or (site == (0x0B2F, 13) and not bot.detached_bomb):
            return NotImplemented
        from ow174.game import npc

        npc.remove_body(self.match, bot)
        return True

    def __init__(
        self, hero: int, mode: int, weapon_buttons: dict[str, int], owner_position: expr.Vec3 | None = None,
        *, controller_components: tuple[int, ...] | None = None, manual_nameplate: bool = False, match=None,
    ) -> None:
        super().__init__(hero, mode)
        self.match = match
        # Each VM owns its runtime lookup; the source body record stays immutable.
        self.weapon_buttons = dict(weapon_buttons)
        self.owner_position = owner_position
        self.control_context = None
        self.controller_components = controller_components
        self.manual_nameplate = manual_nameplate
        self.manual_target = None
        self.sniper_target = None
        self.sniper_retry_at = 0
        self.controller: runtime.Component | None = None
        self.component: runtime.Component | None = None
        self.controller_inputs: dict[int, int] = {}
        self.heavy_counter_stuns: list[tuple[int, float]] = []

    def constant_native(self, cfg, instance):
        # These inputs are fixed by this stock Practice host's construction.
        # Health, buttons, timers, filters and every other live query still poll.
        if (self.mode != PRACTICE_RANGE_MODE or self.hero not in PRACTICE_BOT_HEROES
                or self.control_context is not None):
            return NotImplemented
        cls = cfg.get('$')
        if cls == 'STU_A77AC2AC':
            return False  # This namespace's mode cannot become Tutorial.
        if cls == 'STU_A3DEF368' and graph.mode_variables(self.mode) is not None:
            return expr.Entity(runtime.GAME_MODE_ENTITY)
        if cls == 'STU_AC829876' and instance.graph.index == PRACTICE_NAMEPLATE_PARENT:
            owner, target_filter = cfg.get('m_entity'), cfg.get('m_filter')
            if (owner in ({'$': 'STU_B5A0CAF0'}, {'$': 'STU_B5A0CAF0', 'm_EE729DCB': 0})
                    and isinstance(target_filter, dict)
                    and {key: value for key, value in target_filter.items() if value not in (None, 0)}
                    == PRACTICE_NAMEPLATE_FILTER):
                return True  # Same fixed nameplate gate as native() below.
        return NotImplemented

    def send_message(self, target: int, message: int, params: dict, sender: int) -> None:
        if self.control_context is not None and self.control_context.send(target, message, params, sender):
            return
        if (self.match is not None
                and self.match.combat.hooks.victim_message(self, target, message, params, sender)):
            return
        controller = self.controller
        if (self.hero == 0x02E000000000007A and self.match is not None
                and sender == getattr(self, "body", None)
                and self.match.queue_body_message(target, message, params, sender)):
            return
        if (self.hero == ASSASSIN_HERO and controller is not None
                and target == controller.entity and sender == self.component.entity
                and message in (0x024000000000083A, 0x0240000000000AA3)):
            if not controller.owner_dead and not self.component.owner_dead:
                runtime.send_message(controller, message, params, sender)
            return
        super().send_message(target, message, params, sender)

    def entity_var_slot(self, entity: int, var: int, *, create: bool = False):
        if var in (1624, 1625) and self.match is not None and self.component is not None:
            controls = getattr(getattr(self.match, "combat", None), "sombra_controls", None)
            if controls is not None:
                slot = controls.endpoint_slot(self.component.entity, entity, var)
                if slot is not NotImplemented:
                    return slot
        if self.control_context is not None:
            result = self.control_context.variable(entity, var, create=create)
            if result is not NotImplemented:
                return result
        if entity == runtime.GAME_MODE_ENTITY and var in ARCHIVES_MODIFIER_FLAGS and self.match is not None:
            host = self.match.scene_host
            if host is not None:
                slot = host.world.entity_var_slot(entity, var, create=False)
                if slot is not NotImplemented:
                    return slot
        controller = self.controller
        selected = var in (7681, 9134) or (self.hero == ASSASSIN_HERO and var == ASSASSIN_TARGET)
        if controller is not None and entity == controller.entity and selected:
            # Selected host polling policy: local input slots avoid sharing VM
            # storage namespaces. These reserved IDs are never replicated.
            if var not in self.controller_inputs:
                self.controller_inputs[var] = 0x10000 + var
            self.sync_controller_inputs()
            return self.component.entity_var(self.controller_inputs[var])
        if self.hero == 0x02E000000000007A and self.match is not None:
            return BodyWorld.entity_var_slot(self, entity, var, create=create)
        return NotImplemented

    def sync_controller_inputs(self) -> None:
        for key, proxy in self.controller_inputs.items():
            slot = self.component.entity_var(proxy)
            native = self.controller.entity_var(key, create=False)
            value = native.value() if native is not None and not self.controller.owner_dead else None
            if self.hero == ASSASSIN_HERO and key == ASSASSIN_TARGET:
                if isinstance(value, expr.Entity):
                    value = 0 if self.assassin_target_dead(value) else expr.Entity(value.id)
                elif value is not None and (type(value) is not int or value != 0):
                    raise ValueError('Assassin controller target must be an entity or empty')
            elif value is not None and type(value) not in (bool, int, float):
                raise ValueError('selected controller input must be scalar')
            before = slot.value()
            slot.base = value
            self.component.changed(slot, before)

    def animation_seconds(self, state: runtime.State) -> float | None:
        index = state.instance.graph.index
        alias = graph.guid((state.node.field("m_11E0A658") or {}).get("m_animAlias"))
        category = graph.guid((state.node.field("m_22326315") or {}).get("m_category"))
        if self.hero == HEAVY_ASSAULT_HERO and index == 0x1254 and category == 0x0280000000000245:
            # Reconstruct category 0245 with the first linked 07C0.021 branch at unit rate.
            # 0064 and 0066 resolve to the main clips 6A36.006 and 6A37.006.
            if (state.node.pos, alias) == (46, 0x0C80000000000064):
                return 0.6666666865348816
            if (state.node.pos, alias) == (44, 0x0C80000000000066):
                return 1.0666667222976685
        if self.hero != ASSASSIN_HERO or index != 0x1157 or state.node.pos != 31:
            return None
        if alias == 0x0C80000000000561 and category == 0x0280000000000082:
            # 07C3.021 alias 0561 -> 0640.020, category slot 3 -> clip slot 4.
            # Use the 508A.006 header duration at unit playback rate.
            return 1.4666666984558105
        return None

    def after_set_var(self, instance: runtime.Instance, node) -> None:
        if self.hero == HEAVY_ASSAULT_HERO and instance.graph.index == 0x1408 and node.pos == 136:
            drive = instance.states.get(instance.graph.node(71).state)
            if drive is not None:
                # As with wall contact, check the authored drive transition before the capture watcher.
                drive.try_complete()

    def linked_tracked_targets(self, state: runtime.State):
        if self.match is None:
            return NotImplemented
        if self.hero == TROOPER_HERO and state.instance.graph.index == 0x1422 and state.node.pos == 5:
            target = self.match.combat.trooper_melee_target(state)
            targets = (expr.Entity(target.entity),) if target is not None else ()
        elif (self.hero == HEAVY_ASSAULT_HERO and state.instance.graph.index == 0x140E
              and state.node.pos == 6):
            fight = self.match.combat
            attacker = fight._npc_melee_attacker(state)
            targets = (() if attacker is None else tuple(expr.Entity(target.entity) for target in
                       fight._npc_melee_candidates(attacker, state, state.node)))
        elif (self.hero == HEAVY_ASSAULT_HERO and state.instance.graph.index == 0x1408
              and state.node.pos in (32, 85, 96, 100, 134)):
            combat = self.match.combat
            targets = (combat.heavy_cone_targets(state) if state.node.pos in (96, 134)
                       else combat.heavy_filtered_targets(state))
        else:
            return NotImplemented
        return targets

    def track_targets(self, state: runtime.State) -> bool:
        if self.match is not None and self.hero == 0x02E000000000007A and (state.instance.graph.index, state.node.pos) == (0x0B2F, 8):
            self.match.combat.dva_ultimate_targets(state)
            return True
        targets = self.linked_tracked_targets(state)
        if targets is NotImplemented:
            return False
        state.publish_targets(targets)
        return True

    def stop_tracking_targets(self, state: runtime.State) -> None:
        instance = state.instance
        if (self.hero == HEAVY_ASSAULT_HERO and self.match is not None and not instance.teardown
                and instance.graph.index == 0x1408 and state.node.pos == 96
                and instance.find_var(runtime.INSTANCE_SCOPE, 59).value() == 0):
            # Phase two still needs the selected victim for its impact.
            instance.write(expr.lvalue(state.node.field("m_8A2905A0")), ())

    def linked_script_changed(self, state: runtime.State, *, entered: bool = False) -> None:
        instance = state.instance
        if (self.hero != HEAVY_ASSAULT_HERO or self.match is None
                or instance.graph.index != 0x1408 or state.node.pos != 158):
            return
        combat = self.match.combat
        if entered:
            state.watch(instance.find_var(runtime.INSTANCE_SCOPE, 59))
            combat.heavy_capture_target(state)
        elif state.ending:
            combat.release_heavy_capture(instance.component.entity)
        elif instance.find_var(runtime.INSTANCE_SCOPE, 59).value() == 0:
            state.request(runtime.ABORT)

    def apply_script_pose(self, instance: runtime.Instance, node: graph.Node) -> bool:
        if (self.hero != HEAVY_ASSAULT_HERO or self.match is None
                or instance.graph.index != 0x1408 or node.pos != 86):
            return False
        self.match.combat.place_heavy_release_target(instance, node)
        return True

    def apply_modify_health(self, state: runtime.State) -> None:
        action = (self.hero, state.instance.graph.index, state.node.pos)
        if self.match is not None and action in (
            (TROOPER_HERO, 0x1422, 4),
            (HEAVY_ASSAULT_HERO, 0x1408, 29),
            (HEAVY_ASSAULT_HERO, 0x1408, 35),
            (HEAVY_ASSAULT_HERO, 0x1408, 88),
            (HEAVY_ASSAULT_HERO, 0x1408, 102),
            (HEAVY_ASSAULT_HERO, 0x1408, 120),
        ):
            self.match.combat.npc_selected_damage(state, lateral_force=state.node.pos in (35, 88, 120))
            if action == (HEAVY_ASSAULT_HERO, 0x1408, 88):
                # The detached release effect is a one-shot, including the empty startup entry.
                state.request_finish()
            return
        if (self.hero == HEAVY_ASSAULT_HERO and self.match is not None
                and state.instance.graph.index == 0x140E and state.node.pos == 5):
            self.match.combat.heavy_melee(state)
            return
        if (self.hero == ASSASSIN_HERO and self.match is not None
                and state.instance.graph.index == 0x1157 and state.node.pos in (25, 15)):
            controller = self.controller
            if controller is None or controller.owner_dead:
                return
            for attack in controller.instances.values():
                if attack.graph.index != 0x1158:
                    continue
                # Only the authored default range is supported by this host path.
                init = attack.graph.node(21)
                range_var = (runtime.INSTANCE_SCOPE, ASSASSIN_RANGE_VAR)
                for assignment in init.field("m_143D4C5B") or ():
                    if assignment and expr.dynamic(assignment.get("m_out_Var")) == range_var:
                        reach = expr.to_float(attack.evaluate(assignment.get("m_value")))
                        if reach > 0:
                            self.match.combat.assassin_damage(state, reach)
                        return

    def has_archives_modifier(self, flag):
        entity = runtime.GAME_MODE_ENTITY
        slot = self.entity_var_slot(entity, flag)
        return self.entity_var(entity, flag) is not None if slot is NotImplemented else slot is not None

    def play_archives_modifier(self, instance, node):
        if self.match is None or (instance.graph.index, node.pos) != (0x0E62, 117):
            return False
        if not any(self.has_archives_modifier(flag) for flag in ARCHIVES_MODIFIER_FLAGS):
            return False
        resource = play.resolve_resource(instance, node)
        entity = instance.component.entity
        if (resource is None or resource.guid != 0x0580000000002535
                or instance.evaluate(node.field("m_targets")) != expr.Entity(entity)):
            return False
        definition = graph.graph(DEATH_VOLLEY_GRAPH)
        if definition is None:
            return False
        instance.component.play_loaded_graph(definition, source=entity, instigator=entity)
        return True

    def play_script_host(self, instance: runtime.Instance, node: graph.Node) -> bool:
        if self.play_archives_modifier(instance, node):
            return True
        if (self.hero == ASSASSIN_HERO and self.control_context is not None
                and self.control_context.target_host is not None
                and (instance.graph.index, node.pos) == (0x1157, 45)):
            from ow174.game import assassin
            resource = play.resolve_resource(instance, node)
            if resource is not None and assassin.effect_host(self, instance, node) is not None:
                self.play_resolved_script(instance, node, resource)
            return True  # original permissive profile without declared effect services
        if self.hero == ASSASSIN_HERO and self.match is not None:
            return self.match.combat.assassin_play_capture(instance, node)
        if (self.hero != HEAVY_ASSAULT_HERO or self.match is None
                or instance.graph.index != 0x1408 or node.pos not in (132, 140, 189)):
            return False
        resource = play.resolve_resource(instance, node)
        if resource is None or resource.guid != 0x058000000000224E:
            return False
        overrides = dict(resource.overrides)
        duration = expr.to_float(overrides.get(0x0D800000000000D7))
        if (not math.isfinite(duration) or duration <= 0
                or overrides.get(0x0D80000000006BD6) != expr.Asset(0x0D80000000006BBE)):
            return True
        selected = instance.evaluate(node.field("m_targets"))
        targets = selected if isinstance(selected, tuple) else (selected,)
        # Defer until the impact has dispatched damage, before control interrupts an actor.
        self.heavy_counter_stuns.extend((target.id, duration) for target in targets
                                        if isinstance(target, expr.Entity))
        return True

    def assassin_target_dead(self, value) -> bool:
        if self.match is None or not isinstance(value, expr.Entity):
            return False
        vitals = self.match.combat.vitals.get(value.id)
        return vitals is not None and vitals.dead

    def velocity_action(self, instance, node):
        from ow174.game import sigma_flux

        if sigma_flux.flux_velocity(instance, node):
            return True
        if not instance.component.strict and instance.graph.index == 0x17F6 and node.pos == 4:
            return True
        return super().velocity_action(instance, node)

    def queue_velocity_changes(self, frame, source, changes):
        from ow174.game import body_script_requests

        component = getattr(self, 'component', None)
        instance = component.instances.get(source[0]) if component is not None else None
        if (self.match is not None and instance is not None
                and instance.graph.index == body_script_requests.UPPERCUT_HELPER):
            body_script_requests.queue_npc_velocity_changes(self.match, component, frame, source, changes)

    def ai_weapon_button(self, slot: int) -> int | None:
        if (self.hero == assassin.HERO and self.controller_components is not None and slot == 0
                and self.control_context is None):
            return 36
        return self.weapon_buttons.get(str(slot))

    def selects_ai_behavior(self, instance: runtime.Instance) -> bool:
        if self.control_context is not None and self.control_context.selects(instance):
            return True
        # Reconstruction policy for the single authored firing placement behavior.
        return self.owner_position is not None and instance.graph.index == FIRING_GRAPH

    def entity_var(self, entity: int, var: int):
        if self.match is not None and var in (1735, 2381):
            # Crossfade's 0267 reads the player's song and Amp from its source body.
            return self.match.combat.scripted_body_var(entity, var)
        # 1157's timed release reads these members on the engaged victim.
        if (self.hero == assassin.HERO and self.match is not None and entity == self.manual_target
                and var in (478, 2380)):
            for player in self.match.players:
                script = player.body_script
                if (player.body == entity and player.spawned and not player.gone
                        and script is not None and script.component.entity == entity):
                    value = script.component.entity_var(var, create=False)
                    return value.value() if value is not None else None
            return None
        if self.match is not None and (
            (self.hero == ASSASSIN_HERO and var in ASSASSIN_VICTIM_VARS)
            or (self.hero == HEAVY_ASSAULT_HERO and var == 9161)
        ):
            return self.match.combat.scripted_body_var(entity, var)
        # Use the same read-only live body registry as player recipients. The
        # foreign body owns its slots; this creates neither a slot nor a watcher.
        if self.match is not None and entity != runtime.GAME_MODE_ENTITY:
            component = self._body_component(entity)
            if component is not None:
                slot = component.entity_var(var, create=False)
                return slot.value() if slot is not None else None
        return super().entity_var(entity, var)

    def native(self, cfg: dict, instance: runtime.Instance):
        from ow174.game import assassin, doomfist_helper, npc_context, npc_shields, orisa_halt

        if self.match is not None and instance is not None and instance.graph.index == 0x1590:
            response = self.match.combat.sentries.native(cfg, instance)
            if response is not NotImplemented:
                return response

        response = doomfist_helper.native(self, cfg, instance)
        if response is not NotImplemented:
            return response

        if instance is not None and self.match is not None:
            value = self.match.combat.hooks.victim_native(instance, cfg)
            if value is not NotImplemented:
                return value

        if self.control_context is not None and self.control_context.target_host is not None:
            value = self.control_context.native(cfg, instance)
            if value is not NotImplemented:
                return value
            value = assassin.target_health(self, cfg, instance)
            if value is not NotImplemented:
                return value

        if instance is not None and self.match is not None:
            from ow174.game import sigma_flux

            result = sigma_flux.flux_effect_native(self, cfg, instance)
            if result is not NotImplemented:
                return result

        if self.hero == 0x02E00000000001B8:
            from ow174.game import sniper_reposition, sniper_retreat, sniper_special

            result = sniper_retreat.native(self, cfg, instance)
            if result is not NotImplemented:
                return result
            result = sniper_reposition.primary_native(self, cfg, instance)
            if result is not NotImplemented:
                return result
            result = sniper_special.native(self, cfg, instance)
            if result is not NotImplemented:
                return result

        if self.hero == 0x02E000000000007A and self.match is not None:
            from ow174.game import body_entities

            primary = body_entities.primary_query(self, cfg, instance)
            if primary is not NotImplemented:
                return primary
            if cfg.get("$") == "STU_71A87E92":
                entity = expr.to_entity(instance.evaluate(cfg.get("m_entity")))
                point = self.match.script_body_position(entity, self.body)
                if point is not None:
                    return point
            if cfg.get("$") == "STU_FDBB0B67" and instance.evaluate(cfg.get("m_entity")) == expr.Entity(self.body):
                from ow174.game.script.driver import BODY_SCALE_OVERRIDE
                from ow174.game.world import UNIT_ENTITY_SCALE

                slot = instance.component.entity_var(BODY_SCALE_OVERRIDE, create=False)
                scale = slot.value() if slot is not None else None
                if scale is None or (expr.kind(scale) in ("int", "float") and scale == UNIT_ENTITY_SCALE):
                    return UNIT_ENTITY_SCALE
            if cfg.get("$") == "STU_310D6529":
                from ow174.game.script.driver import THROTTLE_UNIT

                bot = self.match._message_actor(self.body)
                if bot is not None and bot.control_mover is not None:
                    right, forward = bot.control_mover.throttles
                    return expr.Vec3(expr.f32(right * THROTTLE_UNIT), expr.f32(forward * THROTTLE_UNIT), 0.0)

        result = assassin.native(self, cfg, instance)
        if result is NotImplemented:
            result = npc_context.native(self, cfg, instance)
        if result is NotImplemented:
            result = npc_shields.native(self, cfg, instance)
        if result is not NotImplemented:
            return result
        profile = getattr(self, "pool_profile", None)
        if profile is not None and instance is not None and instance.graph.index == 0x143A:
            body, tier = profile
            if cfg.get("$") == "STU_C3619A38":
                return expr.Asset(0x0D80000000000000 | (0x195E, 0x195F, 0x26DF, 0x1960)[tier])
            if (cfg.get("$") == "STU_1A3FA5B0"
                    and instance.evaluate(cfg.get("m_entity")) == expr.Entity(instance.component.entity)):
                return expr.Asset(body)
        halt = orisa_halt.native(self, cfg, instance)
        if halt is not NotImplemented:
            return halt
        if cfg.get("$") == "STU_AC829876" and self.match is not None:
            result = self.match.combat.brigitte.native(cfg, instance)
            if result is not NotImplemented:
                return result
        if (cfg.get("$") == "STU_9B7648C3" and isinstance(cfg.get("m_entity"), dict)
                and cfg["m_entity"].get("$") == "STU_B5A0CAF0"):
            # Explicit owner-health service. Enforcer13EB gates its weapon on
            # this value; no global/foreign-entity health lookup is inferred.
            health = instance.component.entity_var(OWNER_HEALTH, create=False)
            return health.value() if health is not None else None
        if (self.match is not None and instance is not None and cfg.get("$") == "STU_71A87E92"
                and (self.hero, instance.graph.index) in
                ((TROOPER_HERO, 0x1422), (HEAVY_ASSAULT_HERO, 0x140E))):
            entity = instance.evaluate(cfg.get("m_entity"))
            if isinstance(entity, expr.Entity):
                targets = self.match.combat._targets()
                target = next((item for item in targets if item.entity == entity.id), None)
                if target is not None:
                    return expr.Vec3(*target.feet)
        if (self.hero == ASSASSIN_HERO and cfg.get("$") == "STU_DB742151"
                and instance is not None and self.controller is not None
                and instance.evaluate(cfg.get("m_entity")) == expr.Entity(instance.component.entity)):
            return None if self.controller.owner_dead else expr.Entity(self.controller.entity)
        if (cfg.get("$") == "STU_F7262B36" and instance is not None and self.controller is not None
                and instance.evaluate(cfg.get("m_801B298F")) == expr.Entity(instance.component.entity)):
            return None if self.controller.owner_dead else expr.Entity(self.controller.entity)
        if cfg.get("$") in {"STU_32E6E4D8", "STU_7D7D2470", "STU_9B7648C3"} and instance is not None:
            target = instance.evaluate(cfg.get("m_entity"))
            if target == expr.Entity(instance.component.entity):
                if cfg.get("$") == "STU_7D7D2470":
                    return getattr(instance.component, "maximum_health", None)
                health = instance.component.entity_var(OWNER_HEALTH, create=False)
                value = health.value() if health is not None else None
                if cfg.get("$") == "STU_9B7648C3" and type(value) in (int, float):
                    #104319 CEB6C0 ->D177C0 selects component51 and calls+40.
                    # Explicit NPC-owned aggregate health projection; foreign
                    # entities and complete native pool-getter equivalence stay unknown.
                    return expr.f32(value)
                return value
            if (self.hero == ASSASSIN_HERO and instance.graph.index == 0x1157
                    and cfg.get("$") == "STU_9B7648C3" and isinstance(target, expr.Entity)
                    and self.match is not None):
                # Host projection of the 1157 target gate onto authoritative combat pools.
                combat = self.match.combat
                for body in combat._targets():
                    if body.entity == target.id:
                        return expr.f32(combat.vitals_of(body).total)
                return 0.0
        if (
            cfg.get("$") == "STU_AC829876"
            and (
                self.manual_nameplate
                or (self.mode == PRACTICE_RANGE_MODE and self.hero in PRACTICE_BOT_HEROES)
            )
            and instance.graph.index == PRACTICE_NAMEPLATE_PARENT
        ):
            owner, target_filter = cfg.get("m_entity"), cfg.get("m_filter")
            # Reconstruct the 0C18 owner gate for its authored 0D24 child.
            # The native meaning of filter flag 0x100 remains unverified.
            if (
                owner in ({"$": "STU_B5A0CAF0"}, {"$": "STU_B5A0CAF0", "m_EE729DCB": 0})
                and isinstance(target_filter, dict)
                and {key: value for key, value in target_filter.items() if value not in (None, 0)}
                == PRACTICE_NAMEPLATE_FILTER
            ):
                return True
        if self.selects_ai_behavior(instance):
            owner = cfg.get("m_entity")
            if isinstance(owner, dict) and owner.get("$") == "STU_0AC15F78":
                entity = owner.get("m_entity")
                if isinstance(entity, dict) and entity.get("$") == "STU_B5A0CAF0":
                    if cfg.get("$") == "STU_71A87E92":
                        return self.owner_position
                    if cfg.get("$") == "STU_295D117D":
                        return None  # this NPC has no player possessor
        return super().native(cfg, instance)

    def entity_filter_context(self, entity, *, component=None):
        if component is not None:
            return NotImplemented  # No source SDK namespace/cache binding is installed here.
        context = self.control_context
        return context if context is not None and entity in (context.body, context.controller) else NotImplemented

    def play_script_context(self, instance, node):
        if self.control_context is not None:
            return self.control_context.play_script_context(instance, node)
        return super().play_script_context(instance, node)

    def session_script_entities(self, instance):
        if (self.hero == 0x02E0000000000016 and self.match is not None
                and instance.graph.index == 0x157D):
            return tuple(expr.Entity(player.entity) for player in self.match.players if not player.gone)
        return super().session_script_entities(instance)

    def submit_script_request(self, instance, node, request):
        from ow174.game import assassin
        if not assassin.submit_pin_effect(self, instance, node, request):
            super().submit_script_request(instance, node, request)

    def body_health(self, entity):
        lookup = getattr(self.match.combat, "body_health", None) if self.match is not None else None
        return lookup(entity) if lookup is not None else NotImplemented

    def controller_record(self, entity):
        lookup = getattr(self.match, "controller_record", None) if self.match is not None else None
        return lookup(entity) if lookup is not None else NotImplemented

    def play_script_selected(self, instance, node):
        controls = getattr(self.match.combat, "sombra_controls", None) if self.match is not None else None
        return controls.play(instance, node) if controls is not None else NotImplemented

    def skill_locked(self, state):
        controls = getattr(self.match.combat, "sombra_controls", None) if self.match is not None else None
        if self.match is not None and self.match.combat.punches.controlled(state.instance.component.entity):
            return True
        return controls is not None and not state.instance.component.strict and controls.blocked(state)

    def thunderstorm_targets(self, state):
        source = next((bot for bot in self.match.bots
                       if bot.entity == state.instance.component.entity), None)
        if source is None or source.dead:
            return ()
        radius = expr.to_float(state.evaluate(state.node.field("m_radius"), watch=False))
        offset = state.evaluate(state.node.field("m_05BE62DD"), watch=False)
        if not math.isfinite(radius) or radius <= 0 or not isinstance(offset, expr.Vec3):
            return ()
        center = tuple(axis + delta for axis, delta in zip(source.position, offset))
        # Reconstructed search: hostile living player mover origins in the authored sphere.
        # Obstruction belongs to the graph's later ray.
        players = {player.body: player for player in self.match.players}
        return tuple(expr.Entity(target.entity) for target in self.match.combat._enemies(source)
                     if target.entity in players
                     and math.dist(center, players[target.entity].mover.position) <= radius)

    def tracked_targets(self, state):
        from ow174.game import heavy_assault, npc

        if (self.match is not None and state.instance.graph.index == DEATH_VOLLEY_GRAPH
                and state.node.pos == 70):
            return tuple(target.id for target in self.thunderstorm_targets(state))

        if (self.match is not None and self.hero == npc.TROOPER_HERO
                and (state.instance.graph.index, state.node.pos) == (0x1034, 16)):
            return npc.trooper_coordination_targets(self.match.combat, state.instance.component.entity, state)

        targets = heavy_assault.tracked_targets(self, state)
        if targets is not NotImplemented or self.match is None:
            return targets
        targets = self.match.combat._npc_melee(state)
        return targets if targets is NotImplemented else tuple(target.id for target in targets)

    def modify_health(self, state, elapsed_ms):
        from ow174.game import assassin, heavy_assault, npc_shields

        if self.match is not None and state.instance.graph.index == 0x1590:
            value = self.match.combat.sentries.graph_health(state)
            if value is not NotImplemented:
                return value

        from ow174.game import sigma_flux
        flux = sigma_flux.flux_modify_health(self, state, elapsed_ms)
        if flux is not NotImplemented:
            return flux

        value = npc_shields.modify_health(self, state, elapsed_ms)
        if value is not NotImplemented:
            return value

        if self.match is not None and self.hero == 0x02E000000000007A and (state.instance.graph.index, state.node.pos) == (0x0B2F, 22):
            if not elapsed_ms:
                self.match.combat.dva_ultimate_damage(state)
                state.request_finish()
            return True

        controls = getattr(self.match.combat, "sombra_controls", None) if self.match is not None else None
        if (controls is not None and not state.instance.component.strict
                and (state.instance.graph.index, state.node.pos) == (0x0B32, 9)):
            if elapsed_ms == 0:
                controls.drain(state)
                state.request_finish()
            return True
        if (self.match is not None and (self.hero, state.instance.graph.index, state.node.pos) in
                ((TROOPER_HERO, 0x1422, 4), (HEAVY_ASSAULT_HERO, 0x140E, 5))):
            attacker = self.match.combat._npc_melee_attacker(state)
            if (attacker is not None and attacker.spawner.get("manual_spawn") is True
                    and (not attacker.firing or attacker.attack_mode == "melee")):
                if elapsed_ms == 0:
                    self.apply_modify_health(state)
                    settings = state.node.field("m_modifyHealth") or {}
                    origin = state.evaluate(settings.get("m_C0139FA6"), watch=False)
                    recipients = state.evaluate(state.node.field("m_FA3ECC43"), watch=False)
                    if isinstance(origin, expr.Vec3) and isinstance(recipients, tuple):
                        horizontal = expr.to_float(state.evaluate(settings.get("m_E33EFEE1"), watch=False))
                        upward = expr.to_float(state.evaluate(settings.get("m_10399AE7"), watch=False))
                        fight = self.match.combat
                        for target in fight._enemies(attacker):
                            if expr.Entity(target.entity) in recipients and not fight.vitals_of(target).dead:
                                recipient = getattr(target.owner, "body_script", None)
                                if ((self.hero, state.instance.graph.index, state.node.pos) == (HEAVY_ASSAULT_HERO, 0x140E, 5)
                                        and recipient is not None and recipient.hero == 0x02E0000000000015
                                        and recipient.component.entity_var(1216).value() == 2):
                                    heavy_assault._push(attacker, target, state, settings, defer=True)
                                else:
                                    fight._radial_impulse(target, tuple(origin), horizontal, upward,
                                                          replay_source=attacker.entity if upward > 0 else None)
                return True
        value = heavy_assault.modify_health(self, state, elapsed_ms)
        if value is NotImplemented and self.match is not None and elapsed_ms == 0:
            value = self.match.combat._npc_melee(state, health=True)
        return assassin.modify_health(self, state, elapsed_ms) if value is NotImplemented else value

    def tracked_ray(self, state):
        if self.match is not None and state.instance.graph.index == 0x1590:
            return self.match.combat.sentries.ray(state)
        return super().tracked_ray(state)

    def end_health(self, state):
        from ow174.game import assassin

        assassin.end_health(self, state)

    def voice_stimulus_began(self, state):
        from ow174.game import heavy_assault

        heavy_assault.emit_charge_voice(self, state)



    def compatibility_play_script(self, instance, node):
        from ow174.game import assassin_victim

        assassin_victim.play_script(self, instance, node)


    def set_health_pool(self, state, active):
        instance = state.instance
        if getattr(self, "pool_profile", None) is None or instance.graph.index != 0x143A:
            return NotImplemented
        from ow174.game.world import HEALTH_POOL, SHIELDS_POOL

        owner = state.evaluate(state.node.field("m_FA3ECC43"), watch=False)
        kind = state.node.field("m_7E8B666C")
        if owner != expr.Entity(instance.component.entity) or kind not in (HEALTH_POOL, SHIELDS_POOL):
            raise ValueError("unsupported conditional NPC pool")
        vitals = self.match.combat.vitals[owner.id]
        amount = expr.to_float(state.evaluate(state.node.field("m_amount"), watch=False)) if active else 0
        vitals.set_conditional_pool(kind, amount if not vitals.dead else 0)
        instance.component.set(OWNER_HEALTH, float(vitals.total))
        return True


class BotScript(ReplicatedBody):
    """Run the NPC's body and supported placement behavior on the server clock."""

    def __init__(
        self, body: int, hero: int, entity: int, frame: int, mode: int, health: float,
        *, firing_position: tuple | None = None, controller_entity: int | None = None, match=None,
        controller_components: tuple[int, ...] | None = None,
        manual_nameplate: bool = False,
        nameplate_name: str | None = None,
        manual_rialto_spawn: bool = False,
        difficulty_index: int | None = None,
        record: dict | None = None, world: runtime.World | None = None,
        resolve_graph=None, source_entity: int | None = None,
        manual_controller_definition: int | None = None, body_position: tuple | None = None,
    ) -> None:
        from ow174.game import heavy_assault

        record = graph.entity_body(body) if record is None else record
        if record is None:
            raise ValueError(f"no statescript data for entity body {body:016X}")
        self.frame = frame
        if hero == 0x02E000000000007A and match is not None:
            self.dva_birth = (match.tick, frame)
        buttons = record.get("ai_weapon_buttons") or {}
        firing = graph.graph(FIRING_GRAPH) if buttons and firing_position is not None else None
        position = expr.Vec3(*firing_position) if firing_position is not None else None
        external_world = world is not None
        world = world if external_world else BotWorld(
            hero, mode, buttons, position, controller_components=controller_components,
            manual_nameplate=manual_nameplate)
        world.match = match
        if difficulty_index is not None:
            world.pool_profile = body, difficulty_index
        if hero == 0x02E000000000007A:
            from ow174.game.script import spatial_queries

            world.body = entity
            world._entity_link_context = entity_link.Context(
                entity, True, True, lookup=world._lookup_entity_link,
                watch_environment=world._poll_entity_query, watch_link=world._poll_entity_query,
            )
            world._spatial_query_context = spatial_queries.Context(entity, True, True, query=world._query_spatial)
            world._body_owner_context = body_owner.Context(
                entity, True, True, lookup=world._lookup_body_owner,
                watch_environment=world._poll_entity_query, watch_owner=world._poll_entity_query,
            )
            world._entity_query_context = entity_queries.Context(
                entity, True, True, True, lookup=world._lookup_entity_query,
                watch_environment=world._poll_entity_query, watch_possession=world._poll_entity_query,
            )
        component = self.component = runtime.Component(entity, world=world, seed=entity if firing else 1)
        if not external_world:
            world.component = component
        component.frame = frame
        component.now = frame * component.frame_ms
        self.control_context = None
        self.controller: runtime.Component | None = None
        if manual_controller_definition is not None and record.get("controller") is not None:
            raise ValueError('manual association cannot replace an existing controller')
        if record.get("controller") is not None or manual_controller_definition is not None:
            asset = (manual_controller_definition if manual_controller_definition is not None
                     else graph.guid(record["controller"]))
            if manual_controller_definition is not None:
                association = graph.npc_controller(body)
                if (association is None or graph.guid(association.get("definition")) != asset
                        or external_world):
                    raise ValueError('manual controller requires the explicit body association')
            definition = graph.entity_body(asset) if asset is not None and asset >> 48 == 0x0400 else None
            if (definition is None or type(controller_entity) is not int
                    or not expr.valid_entity(controller_entity) or controller_entity == entity):
                raise ValueError('NPC controller requires definition and separate live entity identity')
            require_controller_graphs(definition)
            controller_world = runtime.World(hero, mode)
            if manual_controller_definition is not None:
                from ow174.game.script.npc_context import NpcContext
                context = self.control_context = NpcContext(entity, controller_entity, asset, component)
                if body_position is not None:
                    context.position = expr.Vec3(*body_position)
                world.control_context = context
                controller_world = BotWorld(hero, mode, buttons, match=match)
                controller_world.control_context = context
            elif hero == ASSASSIN_HERO:
                controller_world = AssassinControllerWorld(hero, mode, body, entity, match)
            controller = self.controller = runtime.Component(controller_entity, world=controller_world)
            controller.frame, controller.now = frame, frame * controller.frame_ms
            if self.control_context is not None:
                self.control_context.controller_component = controller
                controller_world.component = controller
            world.controller = controller
            for instance in create_initial_graphs(controller, definition):
                instance.init()
            controller.run_frame(frame)
        elif controller_entity is not None:
            raise ValueError('controller identity supplied without an explicit definition association')
        registered = match is not None and (hero == 0x02E000000000007A or external_world)
        if registered:
            match.register_body_component(component)
        try:
            self.set_health(health, health)
            for instance in create_body_graphs(component, record, resolve_graph=resolve_graph):
                if source_entity is not None:
                    instance.set_source_entity(source_entity)
                instance.init()
                if manual_rialto_spawn and hero == heavy_assault.HERO:
                    heavy_assault.skip_sandbox_intro(instance)
            if difficulty_index is not None and not any(i.graph.index == 0x143A for i in component.instances.values()):
                raise ValueError("NPC difficulty requires body graph 143A")
            component.run_frame(frame)
            self.server_only = self.initial_exclusions()
            self.flashbang_weapon_stack: runtime.Instance | None = None
            self.behavior = component.create(firing) if firing is not None else None
            if self.behavior is not None:
                # Scope the reconstructed spawn reset to this instance. Other 0013 receivers have their own rules.
                runtime.send_instance_message(self.behavior, RESET, {}, entity)
                component.run_now()
            snapshot = observers.snapshot(self)
            if manual_nameplate and nameplate_name:
                child = practice_nameplate(snapshot)
                if child is not None:
                    instance = component.instances[child]
                    instance.find_var(expr.INSTANCE, NAMEPLATE_NAME).base = nameplate_name
                    snapshot = observers.snapshot(self)
            self.latest_live = snapshot
            self._initial_frame: BitWriter | None = observers.full_frame(self, snapshot)
            self.death_frame: int | None = None
            self._produced = snapshot
        except Exception:
            if registered:
                match.unregister_body_component(component)
                for instance in list(component.instances.values()):
                    component.destroy(instance)
                world.match = None
            raise


    def _initial_payload(self) -> BitWriter:
        if self._initial_frame is None:
            self._initial_frame = observers.full_frame(self, self.latest_live)
        return self._initial_frame

    @property
    def initial(self) -> tuple[BitWriter, Snapshot]:
        """Encode the current live baseline only when a viewer needs its full frame."""
        return self._initial_payload(), self.latest_live

    @property
    def terminal_snapshot(self) -> Snapshot | None:
        """Latest death state, after the retained live baseline for new viewers."""
        if self.component.owner_dead and self._produced is not self.latest_live:
            return self._produced
        return None

    def replicated_instances(self):
        return (instance for instance in super().replicated_instances()
                if instance is not self.behavior and instance is not self.flashbang_weapon_stack)

    def show_damage_nameplate(self, sender: int) -> None:
        world = self.component.world
        if not (world.manual_nameplate
                or (world.mode == PRACTICE_RANGE_MODE and world.hero in PRACTICE_BOT_HEROES)):
            return
        snapshot = self.latest_live
        child = practice_nameplate(snapshot)
        if child is None or snapshot.vars.get((child, NAMEPLATE_ENABLED), (None,))[0] is True:
            return
        instance = self.component.instances.get(child)
        if instance is not None:
            runtime.send_instance_message(instance, ENABLE_NAMEPLATE, {}, sender)

    def replicated_vars(self, instance=None):
        hidden = ({0x10000 + key for key in self.component.world.controller_inputs}
                  if instance is None and isinstance(self.component.world, BotWorld) else set())
        return ((key, value) for key, value in super().replicated_vars(instance) if key not in hidden)

    @property
    def has_controller_buttons(self) -> bool:
        return (self.controller is not None and not self.controller.owner_dead
                and isinstance(self.controller.world, AssassinControllerWorld))

    def retire(self) -> None:
        """Disconnect a retired namespace while retaining its stored history."""
        from ow174.game import body_script_requests

        body_script_requests.retire(self.component)
        world = self.component.world
        if world.match is not None:
            world.match.combat.clear_pins(self.component.entity)
            world.match.unregister_body_component(self.component)
        self.component.disconnect_foreign_watchers()
        world.match = None
        world.manual_target = None

    def advance_live(self, frame: int, position: tuple | None = None, *, edges=(), publish=True) -> Snapshot | None:
        """Advance a live weapon body and freeze its latest observer baseline."""
        from ow174.game import body_script_requests

        body_script_requests.discard_unaccepted_npc(self.component)
        if frame <= self.frame or self.component.owner_dead:
            return None
        self.frame = frame
        if (self.behavior is not None or self.component.world.hero == 0x02E000000000007A) and position is not None:
            self.component.world.owner_position = expr.Vec3(*position)
        if self.control_context is not None:
            self.control_context.advance(frame, position)
        elif self.controller is not None and not self.controller.owner_dead:
            if self.has_controller_buttons:
                match = self.component.world.match
                collision = match.collision_world if match is not None else None
                self.controller.world.sample_ground(collision, position)
                self.controller.world.refresh_candidates(self.controller)
                self.controller.world.refresh_spatial_inputs(self.controller)
                self.controller.world.reselect_recovered_attack(self.controller, self.component, frame)
            self.controller.run_frame(frame)
            self.component.world.sync_controller_inputs()
            if self.has_controller_buttons:
                pending = self.controller.world.button_edges
                edges = (*pending, *edges)
                pending.clear()
        world = self.component.world
        stunned = world.match is not None and world.match.combat.stuns.get(self.component.entity, 0) > frame
        if stunned:
            if world.hero in (SNIPER_HERO, TROOPER_HERO, ENFORCER_HERO):
                # A blocked press must not leave a volley queued for after the stun.
                edges = tuple(edge for edge in edges if edge != (20, True))
            elif world.hero == HEAVY_ASSAULT_HERO:
                edges = tuple((button, down) for button, down in edges if not down)
        else:
            self._release_flashbang_weapon_stack()
        self._abort_assassin_attack(frame, dead_target_only=True)
        if world.match is not None:
            from ow174.game import bob_victim
            owner = next((bot for bot in world.match.bots if bot.entity == self.component.entity), None)
            if owner is not None:
                bob_victim.sample(owner, self.component)
        self.component.run_frame(frame, edges)
        if world.hero == HEAVY_ASSAULT_HERO and world.match is not None:
            world.match.combat.advance_heavy_capture(self.component)
            world.match.combat.apply_heavy_counter_stuns(world.heavy_counter_stuns)
        assassin.refresh_control(self.component.world, self.component)
        self._report_shots()
        if not publish:
            return None
        snapshot = self._changed_snapshot()
        if snapshot is not None:
            self.latest_live = snapshot
            self._initial_frame = None
        return snapshot

    def suspend_flashbang_weapon(self) -> None:
        """Project 224E's weapon-stack preemption for an active 102E/114F attack."""
        component = self.component
        selected = {TROOPER_HERO: (0x102E, 44), HEAVY_ASSAULT_HERO: (0x114F, 28)}.get(component.world.hero)
        if selected is None or component.owner_dead or self.flashbang_weapon_stack is not None:
            return
        graph_index, position = selected
        weapon = next((instance for instance in component.instances.values()
                       if instance.graph.index == graph_index), None)
        active = weapon.states.get(weapon.graph.node(position).state) if weapon is not None else None
        if active is None or not active.active or not active.top:
            return
        definition = graph.graph(FLASHBANG_STACK_GRAPH)
        if definition is None:
            return
        effect = component.create(definition)
        # Only the stack branch runs here. The host owns the Flashbang lifetime.
        effect.queue.clear()
        self.flashbang_weapon_stack = effect
        with component.operation():
            effect.node_state(definition.node(33)).begin()

    def _release_flashbang_weapon_stack(self) -> None:
        if self.flashbang_weapon_stack is not None:
            self.component.destroy(self.flashbang_weapon_stack)
            self.flashbang_weapon_stack = None

    def interrupt_attack(self) -> None:
        """Route host Flashbang control to supported NPC attack graphs."""
        if self.component.owner_dead:
            return
        if self.component.world.hero == SNIPER_HERO:
            # 224E node 33 preempts the weapon stack at 1122 node 34.
            self._abort_sniper_charge()
        elif self.component.world.hero == ENFORCER_HERO:
            # 224E/node 33 preempts 13EB/node 0, whose under link aborts charge node 11.
            self._abort_weapon_charge(0x13EB, 11)
        elif self.component.world.hero == HEAVY_ASSAULT_HERO:
            self._abort_heavy_capture()
        elif self.has_controller_buttons:
            for instance in self.component.instances.values():
                if instance.graph.index == 0x1460:
                    runtime.send_instance_message(instance, 0x024000000000083A, {}, self.component.entity)

    def die(self, frame: int, params: dict, sender: int) -> Snapshot | None:
        """Deliver 003E.025 and capture the change that happens immediately."""
        component = self.component
        if component.owner_dead:
            return None
        if frame < self.frame:
            raise ValueError("bot death predates its script")
        # A terminal graph may remove instances needed to encode the last live baseline.
        self._initial_payload()
        from ow174.game import assassin, heavy_assault, npc_control

        assassin.bind(component.world, component, None, dying=True)
        heavy_assault.stop(component)
        if component.world.manual_nameplate:
            npc_control.stop_enforcer_windup(component)
            npc_control.stop_sniper_aim(component)
        # A bot can die in the tick that created it. Its initial frame is already
        # frozen, so run the terminal event after the bootstrap simulation frame.
        self.frame = max(frame, self.frame + 1)
        component.owner_dead = True
        if getattr(component.world, "pool_profile", None) is not None:
            for instance in tuple(component.instances.values()):
                if instance.graph.index == 0x143A:
                    for state in tuple(instance.states.values()):
                        if state.node.cls == "STUStatescriptStateHealthPool":
                            state.end(False)
        assassin.bind(component.world, component, None)
        self.retire_controller()
        if self.behavior is not None:
            component.destroy(self.behavior)
            self.behavior = None
        component.shots.clear()
        self._abort_sniper_charge()
        self._abort_heavy_capture()
        self._abort_assassin_attack(self.frame)
        component.run_frame(self.frame)
        component.held.clear()
        component.set(OWNER_HEALTH, 0.0)
        delivered = runtime.send_message(component, DIED, params, sender)
        if delivered:
            component.run_now()
        self._release_flashbang_weapon_stack()
        if not delivered:
            return None
        self.death_frame = frame
        return self._changed_snapshot()

    def _abort_heavy_capture(self) -> None:
        if self.component.world.hero != HEAVY_ASSAULT_HERO:
            return
        for instance in self.component.instances.values():
            if instance.graph.index == 0x1408:
                sender = instance.states.get(instance.graph.node(158).state)
                if sender is not None and sender.active:
                    instance.write(expr.lvalue(sender.node.field("m_targets")), False)
                    sender.request(runtime.ABORT)

    def _abort_sniper_charge(self) -> None:
        if self.component.world.hero == SNIPER_HERO:
            self._abort_weapon_charge(0x1122, 53)

    def _abort_weapon_charge(self, graph_index: int, position: int) -> None:
        for instance in self.component.instances.values():
            if instance.graph.index == graph_index:
                charge = instance.states.get(instance.graph.node(position).state)
                if charge is not None and charge.active:
                    charge.request(runtime.ABORT)

    def _abort_assassin_attack(self, frame: int, *, dead_target_only: bool = False) -> None:
        component = self.component
        world = component.world
        if world.hero != ASSASSIN_HERO:
            return
        timestamp = frame * component.frame_ms
        for instance in component.instances.values():
            if instance.graph.index != 0x1157:
                continue
            if dead_target_only:
                slot = instance.find_var(runtime.INSTANCE_SCOPE, ASSASSIN_CAPTURE_TARGET, create=False)
                if slot is None or not world.assassin_target_dead(slot.value()):
                    continue
                instance.post(runtime.Event(timestamp, 0, runtime.ASSIGN,
                                            assignment=(runtime.INSTANCE_SCOPE, ASSASSIN_CAPTURE_TARGET, 0)))
            # The delayed damage state can be active outside capture's cleanup list.
            for position in (23, 15):
                state = instance.states.get(instance.graph.node(position).state)
                if state is not None and state.active:
                    instance.post(runtime.Event(
                        timestamp, 0, runtime.STATE_OP, state=state, op=runtime.ABORT,
                    ))

    def retire_controller(self) -> None:
        """Retire the selected host namespace; native controller/network lifecycle remains open."""
        controller = self.controller
        if self.control_context is not None:
            self.control_context.retire()
        if controller is None or controller.owner_dead:
            return
        for instance in list(controller.instances.values()):
            if controller.instances.get(instance.id) is instance:
                controller.destroy(instance)
        controller.owner_dead = True
        controller.held.clear()
        controller.shots.clear()
        if isinstance(controller.world, AssassinControllerWorld):
            controller.world.button_edges.clear()
            controller.world.button_holders.clear()
            controller.world.recovery_pending = controller.world.recovery_started = False
        self.component.world.sync_controller_inputs()

    def advance_death(self, frame: int, lifetime_ticks: int) -> list[Snapshot]:
        """Run due graph events within this death's lifetime and retain each frame's change in order."""
        if self.death_frame is None:
            return []
        limit = min(frame, self.death_frame + lifetime_ticks)
        component = self.component
        payloads = []
        while (due := component.next_event_time()) is not None:
            due_frame = due // component.frame_ms
            if due_frame > limit:
                break
            self.frame = due_frame
            component.run_frame(due_frame)
            payload = self._changed_snapshot()
            if payload is not None:
                payloads.append(payload)
        return payloads

    def _changed_snapshot(self) -> Snapshot | None:
        snapshot = observers.snapshot(self)
        before = self._produced
        if (
            snapshot.instances == before.instances
            and snapshot.vars == before.vars
            and snapshot.states == before.states
        ):
            return None
        self._produced = snapshot
        return snapshot
