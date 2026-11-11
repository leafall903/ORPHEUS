"""The NPC body's statescript, shared by all observers."""

from math import dist, isfinite

from ow174.game import enforcer, observers
from ow174.game.bits import BitWriter
from ow174.game.bots import FIRING
from ow174.game.content import PRACTICE_RANGE_MODE
from ow174.game.script import expr, graph, runtime
from ow174.game.script.driver import OWNER_HEALTH, ReplicatedBody, Snapshot, create_body_graphs

DIED = 0x024000000000003E  # 003E.025, the body's authoritative death message
DEATH_VOLLEY_GRAPH = 0x2535
DEATH_VOLLEY_MODE_FLAG = 28300
ARCHIVES_MODIFIER_FLAGS = (DEATH_VOLLEY_MODE_FLAG, 34924)
RESET = 0x0240000000000013  # 0013.025, the placement's eligibility reset
FIRING_GRAPH = FIRING & 0xFFFF
PRACTICE_NAMEPLATE = 0x0D24
PRACTICE_NAMEPLATE_PARENT = 0x0C18
PRACTICE_BOT_HEROES = frozenset(range(0x02E000000000016B, 0x02E000000000016F))
PRACTICE_NAMEPLATE_FILTER = {"$": "STUConfigVarFilter", "m_84065FD1": 0x3FFFFFFF, "m_4EA686DB": 0x100}


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
        *, controller_components: tuple[int, ...] | None = None,
    ) -> None:
        super().__init__(hero, mode)
        self.weapon_buttons = weapon_buttons
        self.owner_position = owner_position
        self.match = None  # an optional live Match; detached resource probes keep it absent
        self.controller_components = controller_components
        self.manual_target = None
        self.enforcer_controller = None

    def track_targets(self, state):
        from ow174.game import assassin, heavy_charge

        targets = assassin.track_lunge(self, state)
        if targets is not NotImplemented:
            return targets
        if (self.match is not None and state.instance.graph.index == DEATH_VOLLEY_GRAPH
                and state.node.pos == 70):
            return self.thunderstorm_targets(state)
        return heavy_charge.trooper_track(self, state)

    def thunderstorm_targets(self, state):
        source = next((bot for bot in self.match.bots
                       if bot.entity == state.instance.component.entity), None)
        if source is None or source.dead:
            return ()
        radius = expr.to_float(state.evaluate(state.node.field("m_radius"), watch=False))
        offset = state.evaluate(state.node.field("m_05BE62DD"), watch=False)
        if not isfinite(radius) or radius <= 0 or not isinstance(offset, expr.Vec3):
            return ()
        center = tuple(axis + delta for axis, delta in zip(source.position, offset))
        # Reconstructed search: hostile living player mover origins in the authored sphere.
        # Obstruction belongs to the graph's later ray.
        players = {player.body: player for player in self.match.players}
        return tuple(expr.Entity(target.entity) for target in self.match.combat._enemies(source)
                     if target.entity in players
                     and dist(center, players[target.entity].mover.position) <= radius)

    def entity_definition(self, entity: int):
        if self.match is None:
            return NotImplemented
        return self.match.script_body_definition(entity)

    def entity_relation_context(self, entity, *, component=None):
        controller = self.enforcer_controller
        if controller is not None and entity == controller.target.entity:
            return controller.context
        return super().entity_relation_context(entity, component=component)

    def entity_var_slot(self, entity, var, *, create=False):
        if entity == runtime.GAME_MODE_ENTITY and var in ARCHIVES_MODIFIER_FLAGS and self.match is not None:
            host = self.match.scene_host
            if host is not None:
                slot = host.world.entity_var_slot(entity, var, create=False)
                if slot is not NotImplemented:
                    return slot
        return super().entity_var_slot(entity, var, create=create)

    def play_compatible_script(self, instance, node) -> bool:
        if self.match is None or (instance.graph.index, node.pos) != (0x0E62, 117):
            return False
        if not any(self.has_archives_modifier(flag) for flag in ARCHIVES_MODIFIER_FLAGS):
            return False
        from ow174.game.script.play import resolve_resource

        resource = resolve_resource(instance, node)
        entity = instance.component.entity
        if (resource is None or resource.guid != 0x0580000000002535
                or instance.evaluate(node.field("m_targets")) != expr.Entity(entity)):
            return False
        definition = graph.graph(DEATH_VOLLEY_GRAPH)
        if definition is None:
            return False
        instance.component.play_loaded_graph(definition, source=entity, instigator=entity)
        return True

    def has_archives_modifier(self, flag):
        mode = runtime.GAME_MODE_ENTITY
        slot = self.entity_var_slot(mode, flag)
        # A declared false flag can change while the hosted graph is running.
        if slot is NotImplemented:
            return self.entity_var(mode, flag) is not None
        return slot is not None

    def entity_var(self, entity, var):
        controller = self.enforcer_controller
        if controller is not None and entity == controller.host.entity:
            # The authored startup gate is sampled by the body on evaluation.
            slot = controller.host.entity_var(var, create=False)
            return slot.value() if slot is not None else None
        return super().entity_var(entity, var)

    def weapon_launch_inputs(self, state, number, activation, count):
        if self.enforcer_controller is not None:
            return enforcer.launch_inputs(state, count)
        return None

    def ai_weapon_button(self, slot: int) -> int | None:
        if self.hero == 0x02E00000000001BB and self.controller_components is not None and slot == 0:
            return 36
        return self.weapon_buttons.get(str(slot))

    def selects_ai_behavior(self, instance: runtime.Instance) -> bool:
        # Reconstruction policy for the single authored firing placement behavior.
        return self.owner_position is not None and instance.graph.index == FIRING_GRAPH

    def native(self, cfg: dict, instance: runtime.Instance):
        from ow174.game import assassin

        value = assassin.native(self, cfg, instance)
        if value is not NotImplemented:
            return value
        if self.match is not None:
            from ow174.game import sigma_flux
            from ow174.game.script import health_query

            value = sigma_flux.flux_effect_native(self, cfg, instance)
            if value is not NotImplemented:
                return value
            value = health_query.evaluate(self.match, cfg, instance)
            if value is not NotImplemented:
                return value
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


    def modify_health(self, state, elapsed_ms):
        from ow174.game import assassin, heavy_charge, sigma_flux

        value = sigma_flux.flux_modify_health(self, state, elapsed_ms)
        if value is not NotImplemented:
            return value
        if (self.match is not None and state.instance.graph.index == heavy_charge.GRAPH
                and state.node.pos == heavy_charge.RELEASE_HEALTH
                and not expr.to_entity(state.evaluate(state.node.field("m_FA3ECC43")))):
            # Clearing the tracker can wake the authored release wait with no victim left.
            state.request_finish()
            return True
        return assassin.modify_health(self, state, elapsed_ms)

    def end_health(self, state):
        from ow174.game import assassin

        assassin.end_health(self, state)


class BotScript(ReplicatedBody):
    """Run the NPC's body and supported placement behavior on the server clock."""

    def __init__(
        self, body: int, hero: int, entity: int, frame: int, mode: int, health: float,
        *, firing_position: tuple | None = None, match=None,
        controller_components: tuple[int, ...] | None = None, manual=False,
    ) -> None:
        record = graph.entity_body(body)
        if record is None:
            raise ValueError(f"no statescript data for entity body {body:016X}")
        self.frame = frame
        buttons = record.get("ai_weapon_buttons") or {}
        firing = graph.graph(FIRING_GRAPH) if buttons and firing_position is not None else None
        position = expr.Vec3(*firing_position) if firing is not None else None
        world = BotWorld(hero, mode, buttons, position, controller_components=controller_components)
        world.match = match
        if manual:
            world.enforcer_controller = enforcer.attach(body, entity, mode, frame)
        component = self.component = runtime.Component(entity, world=world, seed=entity if firing else 1)
        component.frame = frame
        component.now = frame * component.frame_ms
        self.set_health(health, health)
        for instance in create_body_graphs(component, record):
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

    def advance_live(self, frame: int, position: tuple | None = None) -> Snapshot | None:
        """Advance a live weapon body and freeze its latest observer baseline."""
        if frame <= self.frame or self.component.owner_dead:
            return None
        self.frame = frame
        if self.behavior is not None and position is not None:
            self.component.world.owner_position = expr.Vec3(*position)
        controller = self.component.world.enforcer_controller
        if controller is not None:
            controller.host.run_frame(frame)
        self.component.run_frame(frame)
        from ow174.game import assassin

        assassin.refresh_control(self.component.world, self.component)
        self._report_shots()
        snapshot = self._changed_snapshot()
        if snapshot is not None:
            self.latest_live = snapshot
            self._initial_frame = None
        return snapshot

    def request_trooper_reload(self) -> None:
        self.component.edges([(14, True), (14, False)])  # Trooper 102E/96
        for instance in self.component.instances.values():
            if instance.graph.index != 0x102E:
                continue
            selector = instance.states.get(instance.graph.node(1).state)
            stack = instance.states.get(instance.graph.node(42).state)
            # At zero ammo an abort can leave the authored selector true with no branch.
            if selector is not None and selector.active and selector.current and stack is not None and not stack.active:
                selector.enter()

    def abort_trooper_reload(self) -> None:
        for instance in tuple(self.component.instances.values()):
            if instance.graph.index != 0x102E:
                continue
            # End the authored waits, animation and reload stack together.
            states = [instance.states.get(instance.graph.node(position).state)
                      for position in (47, 48, 23, 42)]
            for state in states:
                if state is not None and state.active:
                    state.end(False)
            reloading = instance.vars.get(51)
            if reloading is not None and reloading.value():
                instance.write((expr.INSTANCE, 51), False)

    def die(self, frame: int, params: dict, sender: int) -> Snapshot | None:
        """Deliver 003E.025 and capture the change that happens immediately."""
        from ow174.game import assassin

        component = self.component
        assassin.bind(component.world, component, None)
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
        self.abort_trooper_reload()
        if self.behavior is not None:
            component.destroy(self.behavior)
            self.behavior = None
        component.shots.clear()
        component.run_frame(self.frame)
        component.held.clear()
        component.set(OWNER_HEALTH, 0.0)
        if not runtime.send_message(component, DIED, params, sender):
            return None
        component.run_now()
        self.death_frame = frame
        return self._changed_snapshot()

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
