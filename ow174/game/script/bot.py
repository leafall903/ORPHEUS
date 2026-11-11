"""The NPC body's statescript, shared by all observers."""

from ow174.game import observers
from ow174.game.bits import BitWriter
from ow174.game.bots import FIRING
from ow174.game.content import PRACTICE_RANGE_MODE
from ow174.game.script import expr, graph, message_receive, modifiers, runtime
from ow174.game.script.driver import OWNER_HEALTH, BodyWorld, ReplicatedBody, Snapshot, create_body_graphs

DIED = 0x024000000000003E  # 003E.025, the body's authoritative death message
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
        self.controller_components = controller_components
        self.match = None
        self.body = 0
        self.dva_queries = None
        self.manual_target = None
        if hero == 0x02E00000000001BB and controller_components is not None:
            self.assassin_direction_records = {}
            self.assassin_numeric_modifiers = modifiers.NumericModifiers(host_velocity=True)

    def script_failed(self, component):
        backend = getattr(self, "assassin_numeric_modifiers", None)
        if backend is not None:
            backend.records.clear()
            backend.velocity_sets.clear()

    entity_query_context = BodyWorld.entity_query_context
    body_owner_context = BodyWorld.body_owner_context
    polled_entity_var = BodyWorld.polled_entity_var
    script_component_present = BodyWorld.script_component_present
    entity_ruleset = BodyWorld.entity_ruleset

    def send_message(self, target: int, message: int, params: dict, sender: int) -> None:
        from ow174.game import dva

        if (self.dva_queries is not None and self.match is not None and sender == self.body
                and message in (dva.RETURN_TO_PILOT, dva.SELF_DESTRUCT_COMPLETE)
                and self.match.queue_body_message(target, message, params, sender)):
            return
        super().send_message(target, message, params, sender)

    def game_message_owner(self, instance, node):
        from ow174.game import assassin

        if assassin.self_message_sender(instance, node):
            return instance.component.entity
        return super().game_message_owner(instance, node)

    def game_message_source(self, instance, node):
        from ow174.game import assassin

        if assassin.self_message_sender(instance, node):
            return instance.component.entity
        return super().game_message_source(instance, node)

    def submit_game_message(self, instance, node, request):
        from ow174.game import assassin

        component = instance.component
        if (assassin.self_message_sender(instance, node) and request.message == assassin.SELF_CONTACT_MESSAGE
                and request.owner == component.entity and request.instigator == component.entity
                and request.self_target and not request.targets and not request.params):
            message_receive.deliver(component, message_receive.Packet(request.message, request.owner, request.instigator))
            return
        super().submit_game_message(instance, node, request)

    def ai_weapon_button(self, slot: int) -> int | None:
        if self.hero == 0x02E00000000001BB and self.controller_components is not None and slot == 0:
            return 36
        return self.weapon_buttons.get(str(slot))

    def selects_ai_behavior(self, instance: runtime.Instance) -> bool:
        # Reconstruction policy for the single authored firing placement behavior.
        return self.owner_position is not None and instance.graph.index == FIRING_GRAPH

    def native(self, cfg: dict, instance: runtime.Instance):
        from ow174.game import assassin, dva, npc_context, npc_shields

        if self.dva_queries is not None:
            value = dva.shell_native(self, instance, cfg)
            if value is not NotImplemented:
                return value

        if self.dva_queries is not None and cfg.get("$") == "STU_FDBB0B67":
            scale = BodyWorld.unit_body_scale(self, cfg, instance)
            if scale is not NotImplemented:
                return scale
        value = assassin.native(self, cfg, instance)
        if value is NotImplemented:
            value = npc_context.native(self, cfg, instance)
        if value is NotImplemented:
            value = npc_shields.native(self, cfg, instance)
        if value is NotImplemented and cfg.get("$") in (
                "STU_021D01CD", "STU_71A87E92", "STU_21A13145", "STU_2200A66D"):
            value = self.explosion_pose(cfg, instance)
        if value is not NotImplemented:
            return value
        profile = getattr(self, "pool_profile", None)
        if profile is not None and instance is not None and instance.graph.index == 0x143A:
            body, tier = profile
            if cfg.get("$") == "STU_C3619A38":
                identifier = (0x195E, 0x195F, 0x26DF, 0x1960)[tier]
                return expr.Asset(0x0D80000000000000 | identifier)
            if (cfg.get("$") == "STU_1A3FA5B0"
                    and instance.evaluate(cfg.get("m_entity")) == expr.Entity(instance.component.entity)):
                return expr.Asset(body)
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

    def explosion_pose(self, cfg: dict, instance: runtime.Instance):
        """Reconstruct pose for authored manual NPC explosion inputs."""
        if self.match is None or instance is None:
            return NotImplemented
        logical = {0x1408: 145, 0x1422: 27, 0x142C: 120}.get(instance.graph.index)
        node = instance.graph.node(logical) if logical is not None else None
        if node is None:
            return NotImplemented
        forward = cfg is node.field("m_direction")
        if not forward:
            if instance.graph.index not in (0x1422, 0x142C):
                return NotImplemented
            position = node.field("m_position")
            configs = position.get("m_configVars") if isinstance(position, dict) else None
            if not configs or not any(cfg is item for item in configs):
                return NotImplemented
        aimed_forward = cfg.get("$") == "STU_2200A66D"
        entity = expr.to_entity(instance.evaluate(cfg.get("m_entity")))
        if entity != instance.component.entity:
            return NotImplemented
        from ow174.game.combat import aim

        for bot in self.match.bots:
            if (bot.entity == entity and bot.spawner.get("manual_spawn") is True
                    and bot.body_script is not None and bot.body_script.component is instance.component):
                if forward or aimed_forward:
                    pitch = round(bot.pitch) if aimed_forward else 0
                    return expr.Vec3(*aim(round(bot.yaw), pitch))
                return expr.Vec3(*bot.position)
        return NotImplemented

    def track_targets(self, state):
        from ow174.game import dva, heavy_assault

        dva.shell_targets(self, state)
        from ow174.game.script.expr import Entity, lvalue

        targets = heavy_assault.tracked_targets(self, state)
        if targets is not NotImplemented:
            state.instance.write(lvalue(state.node.field("m_8A2905A0")), tuple(Entity(e) for e in targets))
            return True

    def destroy_script_body(self, instance, node):
        from ow174.game import dva

        shell = dva.shell_owner(self, instance)
        if shell is None or node.pos != 13:
            return NotImplemented
        shell.dva_shell_spent = True
        return True

    def modify_health(self, state, elapsed_ms):
        from ow174.game import assassin, dva, heavy_assault, npc_shields

        result = npc_shields.modify_health(self, state, elapsed_ms)
        if result is not NotImplemented:
            return result
        result = heavy_assault.modify_health(self, state, elapsed_ms)
        if result is not NotImplemented:
            return result
        result = dva.shell_damage(self, state, elapsed_ms)
        return assassin.modify_health(self, state, elapsed_ms) if result is NotImplemented else result

    def end_health(self, state):
        from ow174.game import assassin

        assassin.end_health(self, state)

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
        *, firing_position: tuple | None = None,
        controller_components: tuple[int, ...] | None = None,
        frame_ms: int = 16, dva_match=None, pool_match=None, difficulty_index: int | None = None,
    ) -> None:
        record = graph.entity_body(body)
        if record is None:
            raise ValueError(f"no statescript data for entity body {body:016X}")
        self.frame = frame
        buttons = record.get("ai_weapon_buttons") or {}
        firing = graph.graph(FIRING_GRAPH) if buttons and firing_position is not None else None
        position = expr.Vec3(*firing_position) if firing is not None else None
        world = BotWorld(hero, mode, buttons, position, controller_components=controller_components)
        if difficulty_index is not None:
            world.pool_profile = body, difficulty_index
            world.match = pool_match
        if dva_match is not None:
            from ow174.game import dva

            world.match = dva_match
            world.body = entity
            world.dva_queries = dva.QueryRegistry(world)
        component = self.component = runtime.Component(
            entity, frame_ms, world=world, seed=entity if firing else 1)
        component.frame = frame
        component.now = frame * component.frame_ms
        self.set_health(health, health)
        for instance in create_body_graphs(component, record):
            instance.init()
        if difficulty_index is not None and not any(
            i.graph.index == 0x143A for i in component.instances.values()
        ):
            raise ValueError("NPC difficulty requires body graph 143A")
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

    def advance_live(self, frame: int, position: tuple | None = None, *, edges=()) -> Snapshot | None:
        """Advance a live weapon body and freeze its latest observer baseline."""
        if frame <= self.frame or self.component.owner_dead:
            return None
        self.frame = frame
        if self.behavior is not None and position is not None:
            self.component.world.owner_position = expr.Vec3(*position)
        self.component.run_frame(frame, edges)
        from ow174.game import assassin

        assassin.refresh_control(self.component.world, self.component)
        self._report_shots()
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
        from ow174.game import assassin

        assassin.bind(component.world, component, None)
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
