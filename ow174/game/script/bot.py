"""The NPC body's statescript, shared by all observers."""

from ow174.game import npc_specials, observers
from ow174.game.bits import BitWriter
from ow174.game.bots import FIRING
from ow174.game.content import PRACTICE_RANGE_MODE
from ow174.game.script import assassin_filter, body_owner, expr, graph, runtime
from ow174.game.script.driver import (
    DEFAULT_SLOT,
    OWNER_HEALTH,
    WEAPON_SLOTS,
    ReplicatedBody,
    Snapshot,
    create_initial_graphs,
    create_weapon_graphs,
)

DIED = 0x024000000000003E  # 003E.025, the body's authoritative death message
RESET = 0x0240000000000013  # 0013.025, the placement's eligibility reset
FIRING_GRAPH = FIRING & 0xFFFF
PRACTICE_NAMEPLATE = 0x0D24
PRACTICE_NAMEPLATE_PARENT = 0x0C18
PRACTICE_BOT_HEROES = frozenset(range(0x02E000000000016B, 0x02E000000000016F))
PRACTICE_NAMEPLATE_FILTER = {"$": "STUConfigVarFilter", "m_84065FD1": 0x3FFFFFFF, "m_4EA686DB": 0x100}
TALON_REACTION_GRAPHS = {
    npc_specials.TROOPER_BODY: 0x1231,
    npc_specials.ENFORCER_BODY: 0x142F,
    0x0400000000001E7D: 0x14D1,
    npc_specials.ASSASSIN_BODY: None,
    0x0400000000001EBB: 0x0E54,
}
# 104319 F525E0 maps STU_76AF64F0 in these controller definitions to component 92.
TALON_COMPONENT_92_CONTROLLERS = frozenset(
    0x0400000000000000 | key for key in (0x1D41, 0x227A, 0x1E7E, 0x1EBE, 0x1EBC)
)
TALON_STARTUP_KEYS = {118: 0x25EB, 120: 0x25EF, 122: 0x25EA}


def practice_nameplate(snapshot: Snapshot) -> int | None:
    for instance, (graph_id, parent) in snapshot.instances.items():
        if (
            graph_id == PRACTICE_NAMEPLATE and parent is not None and parent[1] == 3
            and snapshot.instances.get(parent[0], (None, None))[0] == PRACTICE_NAMEPLATE_PARENT
        ):
            return instance
    return None


class BotWorld(runtime.World):
    """Resolve the supported authored predicates and input bindings for an NPC body."""

    def __init__(
        self, hero: int, mode: int, weapon_buttons: dict[str, int], owner_position: expr.Vec3 | None = None,
        *, entity: int = 0, match=None, body_definition: int = 0,
    ) -> None:
        super().__init__(hero, mode)
        self.weapon_buttons = weapon_buttons
        self.owner_position = owner_position
        self.body = entity
        self.body_definition = body_definition
        self.match = match
        self.manual_target = 0
        self._body_owner_context = body_owner.Context(
            entity, True, True, lookup=self._lookup_body_owner,
            watch_environment=self._poll_body_owner, watch_owner=self._poll_body_owner,
        )

    def _poll_body_owner(self, *_):
        """Native ownership dependencies use the existing per-frame native poll."""

    def _lookup_body_owner(self, entity):
        for bot in self.match.bots:
            if entity == bot.entity:
                return body_owner.Target(entity, bot.controller_entity or 0)
            if entity == bot.controller_entity:
                return body_owner.Target(entity, None)
        for player in self.match.players:
            if not player.gone and player.has_body and player.body == entity:
                return body_owner.Target(entity, player.entity)
            if not player.gone and player.entity == entity:
                return body_owner.Target(entity, None)
        return None

    def body_owner_context(self, entity):
        return (self._body_owner_context if self.match is not None and entity == self.body
                else NotImplemented)

    def entity_var(self, entity, var):
        if self.match is None:
            return super().entity_var(entity, var)
        component = self.match.script_actor_component(entity)
        if component is None:
            return super().entity_var(entity, var)
        slot = component.entity_var(var, create=False)
        return None if slot is None else slot.value()

    def script_component_present(self, entity):
        if self.match is None:
            return NotImplemented
        if any(not p.gone and p.entity == entity for p in self.match.players):
            #000E.003 declares STUStatescriptComponent with no initial graphs.
            # Presence does not alias its member variables to the possessed body.
            return True
        return self.match.script_actor_component(entity) is not None

    def poll_foreign_members(self, entity):
        return self.match is not None and self.match.script_actor_component(entity) is not None

    def position(self, entity):
        if self.match is not None:
            point = self.match.script_body_position(entity, self.body)
            if point is not None:
                return point
            for bot in self.match.bots:
                if bot.controller_entity == entity:
                    return expr.Vec3(*bot.position)
        return super().position(entity)

    def ai_weapon_button(self, slot: int) -> int | None:
        return self.weapon_buttons.get(str(slot))

    def track_targets(self, state):
        from ow174.game import heavy_charge

        found = heavy_charge.targets(self, state)
        if found is not NotImplemented:
            return found

        found = npc_specials.melee_targets(self, state)
        if found is not NotImplemented:
            return found
        return assassin_filter.track_targets(self, state)

    def animation_duration(self, state):
        return npc_specials.recovery_duration(self, state)

    def ability_allowed(self, state):
        from ow174.game import heavy_charge

        if (self.match is not None and state.instance.graph.index == heavy_charge.GRAPH
                and state.node.pos == heavy_charge.ABILITY):
            return (not heavy_charge.phase(state.instance)
                    and not self.match.combat.hacks.active(self.body))

        context = npc_specials.melee_context(self, state, 3)
        return context is None or not context[0].hacks.active(self.body)

    def modify_health_enter(self, state):
        from ow174.game import hacks, heavy_charge, npc_specials

        if self.match is not None and state.instance.graph.index == hacks.EMP_HIT and state.node.pos == 9:
            return self.match.combat.hacks.emp_health(state)
        impact = heavy_charge.impact_health(self, state)
        if impact is not NotImplemented:
            return impact
        melee = npc_specials.melee_health(self, state)
        if melee is not NotImplemented:
            return melee
        return npc_specials.initial_health(self, state)

    def talon_startup_graph(self, instance, node):
        if self.body_definition not in TALON_REACTION_GRAPHS or instance.graph.index != 0x0E62:
            return None
        if node.pos == 120:
            return TALON_REACTION_GRAPHS[self.body_definition]
        return {118: 0x0E5E, 122: 0x0E20}.get(node.pos)

    def seed_talon_reactions(self, record, instances):
        """Project the literal reaction overrides before the authored entries run."""
        if self.body_definition not in TALON_REACTION_GRAPHS:
            return
        root = next((i for i in instances if i.graph.index == 0x0E62), None)
        if root is None:
            return
        declaration = next(item for item in record['graphs'] if item['graph'] == root.graph.index)
        overrides = {expr.guid_of(row.get('m_0D09D2D9')): row.get('m_value')
                     for row in declaration.get('m_1EB5A024') or ()}
        for position, key in TALON_STARTUP_KEYS.items():
            node = root.graph.node(position)
            if node is None or node.cls != 'STU_CB4905E9':
                continue
            target = expr.dynamic(node.field('m_69F4B52C'))
            if target != (expr.INSTANCE, key):
                continue
            config = overrides.get(0x0D80000000000000 | key)
            inline = graph.field_at(config, 'm_36F7B64C')
            if (not isinstance(config, dict) or config.get('$') != 'STU_6DF07EE5'
                    or not isinstance(inline, dict) or inline.get('$') != 'STU_29C4C486'
                    or any(inline.get(key) for key in ('m_17BB822A', 'm_CB2634D7', 'm_75408640'))):
                continue
            resource = inline.get('m_C974E369')
            if (isinstance(resource, dict) and resource.get('$') == 'STU_105E1BCC'
                    and resource.get('m_resourceKey') is None):
                root.write(target, expr.Asset(expr.guid_of(resource.get('m_graph'))))

    def handles_play_script(self, instance, node):
        from ow174.game import hacks, heavy_charge

        if self.match is not None and heavy_charge.handles(instance, node):
            return True

        if self.match is not None and hacks.plays(instance, node, hacks.EMP_HIT, 6, hacks.APPLICATION):
            return True
        if self.talon_startup_graph(instance, node) is not None:
            return True
        script = node.field('m_script')
        return (self.match is not None and instance.graph.index == 0x1157
                and isinstance(script, dict) and script.get('$') == 'STU_105E1BCC'
                and script.get('m_graph') == '0x0580000000001177')

    def play_script_context(self, instance, node):
        from ow174.game import hacks
        from ow174.game.script.play import InvocationContext

        if not self.handles_play_script(instance, node):
            return super().play_script_context(instance, node)
        source = instance.component.entity
        if instance.graph.index == hacks.EMP_HIT:
            caller = instance.source_entity()
            return InvocationContext(caller, source, caller)
        return InvocationContext(source, source, source)

    def submit_script_request(self, instance, node, request):
        from ow174.game import hacks, heavy_charge

        if not self.handles_play_script(instance, node):
            return super().submit_script_request(instance, node, request)
        if heavy_charge.handles(instance, node):
            if node.pos == heavy_charge.IMPACT_CONTROL:
                return heavy_charge.impact_control(self, instance, request)
            if node.pos in heavy_charge.CLASH_CONTROL:
                return heavy_charge.clash_control(self, request)
            return heavy_charge.pin(self, instance, request)
        if instance.graph.index == hacks.EMP_HIT:
            return self.match.combat.hacks.submit(instance, request)
        startup_graph = self.talon_startup_graph(instance, node)
        if startup_graph is not None:
            # Reconstructed local submission for the authored, preloaded self target.
            expected_guid = (graph.GRAPH_TYPE << 48) | startup_graph
            if (request.resource.guid != expected_guid or request.resource.overrides
                    or request.targets != (self.body,) or request.params
                    or request.policy != 0 or request.wire_flag != 1):
                raise ValueError('unsupported Talon reaction startup request')
            definition = graph.graph(startup_graph)
            if definition is None:
                return None
            return instance.component.play_loaded_graph(definition, source=request.context.caller,
                                                        instigator=request.context.instigator,
                                                        policy=request.policy)
        if (request.resource.guid != 0x0580000000001177 or request.resource.overrides
                or request.policy != 0 or request.wire_flag != 0 or request.params):
            raise ValueError('unsupported Assassin victim-script request')
        for target in request.targets:
            player = next((p for p in self.match.players if p.entity == target and not p.gone
                           and p.spawned and p.has_body and p.body_script is not None), None)
            if (player is None or player.body_script.component.owner_dead
                    or self.match.combat.is_dead(player)):
                continue
            actor = next((b for b in self.match.bots if b.entity == instance.component.entity), None)
            if (actor is None or self.match.combat.actions_blocked(actor)
                    or actor.body_script is None or actor.body_script.controller is None
                    or not actor.body_script.controller.world.manual_target
                    or (not self.match.game_map.free_for_all and actor.team == player.team)):
                continue
            from ow174.game import npc_specials

            if npc_specials.contact_target(self.match.combat, actor, player) is None:
                continue
            # Declared host policy: the initial hit must reach the body to pin it.
            if not self.match.combat.assassins.admit(instance, player):
                continue
            component = player.controller_component
            created = component.play_loaded_graph(graph.graph(0x1177), source=request.context.caller,
                                                  instigator=request.context.instigator,
                                                  policy=request.policy)
            component.run_now()
            if created is not None:
                self.match.combat.assassins.bind(actor, player)
                self.match.combat.interrupt_actions(player)

    def selects_ai_behavior(self, instance: runtime.Instance) -> bool:
        # Reconstruction policy for the single authored firing placement behavior.
        index = instance.graph.index
        targeted = bool(self.manual_target) and index in (0x13C2, npc_specials.ENFORCER_CONTROLLER)
        if index == npc_specials.ENFORCER_CONTROLLER and not targeted:
            enforcer = npc_specials.enforcer_actor(self, instance)
            targeted = enforcer is not None and bool(enforcer.spawner.get('auto_target'))
        if targeted and not instance.component.owner_dead:
            candidate = next((n for n in instance.graph.nodes if n.cls == 'STU_91EF24A9'), None)
            return candidate is not None and all(
                expr.truthy(instance.evaluate(config)) for config in candidate.field('m_AAAC951E') or ())
        return self.owner_position is not None and instance.graph.index == FIRING_GRAPH

    def native(self, cfg: dict, instance: runtime.Instance):
        if (cfg.get('$') == 'STU_D3EB8D87' and self.match is not None
                and instance.graph.index == TALON_REACTION_GRAPHS.get(self.body_definition)):
            # CDB700 resolves the selected entity and checks component 92. For
            # the authored reaction graphs, reconstruct presence from the
            # live hosted controller and its verified definition.
            entity = expr.to_entity(instance.evaluate(cfg.get('m_00903303')))
            return any(bot.controller_entity == entity
                       and bot.controller_definition in TALON_COMPONENT_92_CONTROLLERS
                       and bot.body_script is not None and bot.body_script.controller is not None
                       for bot in self.match.bots)
        axis = npc_specials.reaction_axis(self, instance, cfg)
        if axis is not NotImplemented:
            return axis
        query = npc_specials.trooper_magazine_query(self, instance, cfg)
        if query is not NotImplemented:
            return query
        query = npc_specials.enforcer_query(self, instance, cfg)
        if query is not NotImplemented:
            return query
        if (self.match is not None and instance.graph.index == 0x1157
                and cfg.get('$') == 'STU_2200A66D'):
            # Exact source-facing expression reads the targeted controller's
            # possessed body aim. This is a declared host transform service.
            from ow174.game.combat import aim

            target = expr.to_entity(instance.evaluate(cfg.get('m_entity')))
            player = next((p for p in self.match.players if not p.gone and p.entity == target
                           and p.has_body and p.body_script is not None), None)
            if player is not None:
                return expr.Vec3(*(expr.f32(v) for v in aim(player.mover.yaw, player.mover.pitch)))
        filtered = assassin_filter.evaluate(self, instance, cfg)
        if filtered is not NotImplemented:
            return filtered
        if self.match is not None and cfg.get('$') in ('STU_DB742151', 'STU_0AC15F78'):
            # Scoped paired-actor host reconstruction; no general native
            # possessor registry or entity category/filter answer is inferred.
            from ow174.game.script import play

            target = expr.to_entity(play.evaluate_scalar(
                instance, cfg.get('m_entity'), initial=expr.Entity(0)))
            for bot in self.match.bots:
                if bot.controller_entity is None:
                    continue
                if cfg['$'] == 'STU_DB742151' and target in (bot.entity, bot.controller_entity):
                    return expr.Entity(bot.controller_entity)
                if cfg['$'] == 'STU_0AC15F78' and target == bot.controller_entity:
                    return expr.Entity(bot.entity)
        if cfg.get("$") == "STU_9B7648C3":
            if self.match is not None and instance.graph.index == 0x1157:
                from ow174.game.script import play

                target_id = expr.to_entity(play.evaluate_scalar(
                    instance, cfg.get('m_entity'), initial=expr.Entity(0)))
                player = next((p for p in self.match.players if p.entity == target_id and not p.gone
                               and p.has_body and p.body_script is not None), None)
                target = next((t for t in self.match.combat._targets() if t.owner is player), None)
                if target is not None:
                    return expr.f32(self.match.combat.vitals_of(target).total)
            # 104319 CEB6C0 -> D177C0 selects component51 via BCC870.
            # Reconstruct its numeric getter from the authoritative aggregate
            # health input for this NPC only; foreign entities remain unsupported.
            target = cfg.get("m_entity")
            if (isinstance(target, dict)
                    and instance.evaluate(target) == expr.Entity(instance.component.entity)):
                health = instance.component.entity_var(OWNER_HEALTH, create=False)
                value = health.value() if health is not None else None
                if type(value) in (int, float):
                    return expr.f32(value)
        if (
            cfg.get("$") == "STU_AC829876"
            and self.mode == PRACTICE_RANGE_MODE
            and self.hero in PRACTICE_BOT_HEROES
            and instance.graph.index == PRACTICE_NAMEPLATE_PARENT
        ):
            owner, target_filter = cfg.get("m_entity"), cfg.get("m_filter")
            # Reconstruct the stock 0C18 owner gate for its authored 0D24 child.
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


class BotScript(ReplicatedBody):
    """Run the NPC's body and supported placement behavior on the server clock."""

    def __init__(
        self, body: int, hero: int, entity: int, frame: int, mode: int, health: float,
        *, firing_position: tuple | None = None, match=None,
        controller_definition: int | None = None, controller_entity: int | None = None,
    ) -> None:
        record = graph.entity_body(body)
        if record is None:
            raise ValueError(f"no statescript data for entity body {body:016X}")
        self.controller = None
        if (controller_definition is None) != (controller_entity is None):
            raise ValueError('NPC controller requires both definition and entity identity')
        if controller_definition is not None:
            controller_record = graph.entity_body(controller_definition)
            if (controller_record is None or type(controller_entity) is not int
                    or not 0 < controller_entity <= 0xFFFFFFFF or controller_entity == entity):
                raise ValueError('NPC controller requires prepared definition and distinct uint32 identity')
            controller_world = BotWorld(hero, mode, {}, entity=controller_entity, match=match)
            self.controller = runtime.Component(controller_entity, world=controller_world)
            self.controller.frame = frame
            self.controller.now = frame * self.controller.frame_ms
            for instance in create_initial_graphs(self.controller, controller_record):
                instance.init()
                if instance.graph.index == npc_specials.ENFORCER_CONTROLLER:
                    instance.play_schema((), reorder_entries=True)
            self.controller.run_frame(frame)
        self.frame = frame
        buttons = record.get("ai_weapon_buttons") or {}
        firing = graph.graph(FIRING_GRAPH) if buttons and firing_position is not None else None
        position = expr.Vec3(*firing_position) if firing is not None else None
        world = BotWorld(hero, mode, buttons, position, entity=entity, match=match, body_definition=body)
        component = self.component = runtime.Component(entity, world=world, seed=entity if firing else 1)
        component.frame = frame
        component.now = frame * component.frame_ms
        weapons = record.get("weapons") or []
        component.set(WEAPON_SLOTS, tuple(item is not None for item in weapons))
        component.set(DEFAULT_SLOT, next((slot for slot, item in enumerate(weapons) if item is not None), 0))
        self.set_health(health, health)
        created = create_initial_graphs(component, record)
        world.seed_talon_reactions(record, created)
        created.extend(create_weapon_graphs(component, record))
        for instance in created:
            instance.init()
        component.run_frame(frame)
        self.server_only = self.initial_exclusions()
        self.behavior = component.create(firing) if firing is not None else None
        if self.behavior is not None:
            # Scope the reconstructed spawn reset to this instance. Other 0013 receivers have their own rules.
            runtime.send_instance_message(self.behavior, RESET, {}, entity)
            component.run_now()
        snapshot = observers.snapshot(self)
        self.latest_live = snapshot
        self._initial_frame: BitWriter | None = observers.full_frame(self, snapshot)
        self.death_frame: int | None = None
        self._produced = snapshot

    def _initial_payload(self) -> BitWriter:
        if self._initial_frame is None:
            self._initial_frame = observers.full_frame(self, self.latest_live)
        return self._initial_frame

    @property
    def initial(self) -> tuple[BitWriter, Snapshot]:
        """Encode the current live baseline only when a viewer needs its full frame."""
        return self._initial_payload(), self.latest_live

    def replicated_instances(self):
        return (instance for instance in super().replicated_instances() if instance is not self.behavior)

    def advance_live(
        self, frame: int, position: tuple | None = None, *, weapon_input: tuple[int, bool] | None = None,
        ability_input: tuple[int, bool] | None = None,
    ) -> Snapshot | None:
        """Advance a live body, optionally delivering one explicit AI weapon-slot edge.

        The catalog's ai_weapon_buttons owns the slot mapping. This input adapter
        runs authored weapon graphs; it does not select targets or implement AI.
        Duplicate held input cannot synthesize additional press edges.
        """
        from ow174.game import heavy_charge, sniper_reposition

        edges = []
        if weapon_input is not None:
            if (type(weapon_input) is not tuple or len(weapon_input) != 2
                    or type(weapon_input[0]) is not int or type(weapon_input[1]) is not bool):
                raise ValueError("NPC weapon input needs an integer slot and a Boolean press")
            slot, pressed = weapon_input
            button = self.component.world.ai_weapon_button(slot)
            if type(button) is not int:
                raise ValueError("NPC body has no authored mapping for this weapon slot")
            if pressed != (button in self.component.held):
                edges = [(button, pressed)]
        if ability_input is not None:
            if (type(ability_input) is not tuple or len(ability_input) != 2
                    or type(ability_input[0]) is not int or type(ability_input[1]) is not bool):
                raise ValueError('NPC ability input needs a logical button and Boolean press')
            button, pressed = ability_input
            declared = {expr.to_int(instance.evaluate(node.field('m_logicalButton')))
                        for instance in self.component.instances.values() for node in instance.graph.states
                        if node is not None and node.cls == 'STUStatescriptStateLogicalButton'}
            for instance in self.component.instances.values():
                spec = npc_specials.NPC_MELEE.get(instance.graph.index)
                if spec is not None:
                    ability = instance.graph.node(spec[3])
                    state = instance.node_state(ability)
                    if npc_specials.melee_context(self.component.world, state, 3) is not None:
                        declared.add(expr.to_int(instance.evaluate(ability.field('m_A5F1A77B'))))
                if instance.graph.index == heavy_charge.GRAPH:
                    ability = instance.graph.node(heavy_charge.ABILITY)
                    declared.add(expr.to_int(instance.evaluate(ability.field('m_A5F1A77B'))))
            if button not in declared:
                raise ValueError('NPC body does not declare this logical ability input')
            if any(edge[0] == button for edge in edges):
                raise ValueError('NPC weapon and ability inputs overlap')
            if pressed != (button in self.component.held):
                edges.append((button, pressed))
        if frame <= self.frame or self.component.owner_dead:
            return None
        match = self.component.world.match
        actor = None
        if match is not None:
            if frame > match.tick:
                raise ValueError('NPC input cannot advance beyond the current world tick')
            actor = next((b for b in match.bots if b.entity == self.component.entity), None)
            if actor is not None and match.combat.actions_blocked(actor):
                edges = [(button, False) for button in tuple(self.component.held)]
        if self.controller is not None and not self.controller.owner_dead:
            from ow174.game.script.nodes import AIBehavior

            for instance in list(self.controller.instances.values()):
                for state in list(instance.states.values()):
                    if isinstance(state, AIBehavior):
                        state.refresh_selection()
            self.controller.run_frame(frame)
            npc_specials.poll_enforcer_gate(self.controller)
        self.frame = frame
        if self.behavior is not None and position is not None:
            self.component.world.owner_position = expr.Vec3(*position)
        charge = heavy_charge.move(actor, match.combat, frame) if actor is not None else None
        self.component.run_frame(frame, edges)
        if actor is not None:
            sniper_reposition.step(match.combat, actor, frame)
        if actor is not None and actor.body == heavy_charge.BODY:
            heavy_charge.finish_frame(actor, match.combat, charge)
        self._report_shots()
        if match is not None and match.combat.tick == frame:
            match.combat.assassins.update()
        snapshot = self._changed_snapshot()
        if snapshot is not None:
            self.latest_live = snapshot
            self._initial_frame = None
        return snapshot

    def die(self, frame: int, params: dict, sender: int) -> Snapshot | None:
        """Deliver 003E.025 and capture the change that happens immediately."""
        component = self.component
        if component.owner_dead:
            return None
        if frame < self.frame:
            raise ValueError("bot death predates its script")
        # A terminal graph may remove instances needed to encode the last live baseline.
        self._initial_payload()
        # A bot can die in the tick that created it. Its initial frame is already
        # frozen, so run the terminal event after the bootstrap simulation frame.
        self.frame = max(frame, self.frame + 1)
        component.owner_dead = True
        self.retire_controller()
        if self.behavior is not None:
            component.destroy(self.behavior)
            self.behavior = None
        component.shots.clear()
        npc_specials.discard_talon_hit_messages(component)
        component.run_frame(self.frame)
        npc_specials.retire_talon_hit(component)
        component.held.clear()
        component.set(OWNER_HEALTH, 0.0)
        if not runtime.send_message(component, DIED, params, sender):
            return None
        component.run_now()
        self.death_frame = frame
        return self._changed_snapshot()

    def set_manual_target(self, entity: int | None = None):
        """Declare a target input for a prepared NPC controller."""
        target = 0 if entity is None else entity
        controller = self.controller
        if (type(target) is not int or not 0 <= target <= 0xFFFFFFFF
                or (target and not expr.valid_entity(target))
                or target == self.component.entity
                or (controller is not None and target == controller.entity)):
            raise ValueError('manual NPC target requires a distinct uint32 identity')
        if controller is None:
            raise ValueError('manual NPC target requires a prepared associated controller')
        if not controller.owner_dead:
            controller.world.manual_target = target
            enforcer = any(i.graph.index == npc_specials.ENFORCER_CONTROLLER
                           for i in controller.instances.values())
            if not enforcer:
                controller.set(14365, expr.Entity(target))

    def retire_controller(self):
        controller = self.controller
        if controller is not None and not controller.owner_dead:
            controller.owner_dead = True
            controller.world.manual_target = 0
            controller.held.clear()
            controller.shots.clear()
            for instance in list(controller.instances.values()):
                controller.destroy(instance)

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
