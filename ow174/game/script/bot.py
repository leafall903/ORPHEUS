"""The NPC body's statescript, shared by all observers."""

from ow174.game import observers
from ow174.game.bits import BitWriter
from ow174.game.bots import FIRING
from ow174.game.content import PRACTICE_RANGE_MODE
from ow174.game.script import body_owner, entity_link, entity_queries, expr, graph, runtime, spatial_queries
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
        *, body: int = 0, match=None, controller_components: tuple[int, ...] | None = None,
    ) -> None:
        super().__init__(hero, mode)
        self.match = match  # installed only for explicitly owned body services.
        self.body = body
        self.controller_components = controller_components
        self.manual_target = None
        self.weapon_buttons = weapon_buttons
        self.owner_position = owner_position
        self._body_owner_context = body_owner.Context(
            body, True, True, lookup=self._lookup_body_owner,
            watch_environment=self._poll_body_owner, watch_owner=self._poll_body_owner,
        )
        self._entity_query_context = entity_queries.Context(
            body, True, True, True, lookup=self._lookup_entity_query,
            watch_environment=self._poll_body_owner, watch_possession=self._poll_body_owner,
        ) if expr.valid_entity(body) else None
        self._spatial_query_context = spatial_queries.Context(body, True, True, query=self._query_spatial)
        self._entity_link_context = entity_link.Context(
            body, True, True, lookup=self._lookup_entity_link,
            watch_environment=self._poll_body_owner, watch_link=self._poll_body_owner,
        )

    _lookup_body_owner = BodyWorld._lookup_body_owner
    _poll_body_owner = BodyWorld._poll_body_owner
    body_owner_context = BodyWorld.body_owner_context
    _lookup_entity_query = BodyWorld._lookup_entity_query
    entity_query_context = BodyWorld.entity_query_context
    _query_spatial = BodyWorld._query_spatial
    spatial_query_context = BodyWorld.spatial_query_context
    script_component_present = BodyWorld.script_component_present
    _body_script = BodyWorld._body_script
    _body_component = BodyWorld._body_component
    entity_var_slot = BodyWorld.entity_var_slot
    entity_ruleset = BodyWorld.entity_ruleset

    def entity_link_context(self, entity):
        if self.match is None or entity != self.body:
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

    def send_message(self, target: int, message: int, params: dict, sender: int) -> None:
        match = self.match
        if (match is not None and sender == self.body
                and match.queue_body_message(target, message, params, sender)):
            return
        super().send_message(target, message, params, sender)

    def ai_weapon_button(self, slot: int) -> int | None:
        if self.hero == 0x02E00000000001BB and self.controller_components is not None and slot == 0:
            return 36
        return self.weapon_buttons.get(str(slot))

    def destroy_script_body(self, instance, node):
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

    def selects_ai_behavior(self, instance: runtime.Instance) -> bool:
        # Reconstruction policy for the single authored firing placement behavior.
        return self.owner_position is not None and instance.graph.index == FIRING_GRAPH

    def play_owned_script(self, instance, node):
        from ow174.game import assassin, sombra_emp

        if self.match is not None:
            result = sombra_emp.dispatch(self.match, instance, node)
            if result is not NotImplemented:
                return result
        return assassin.play_victim_effect(self, instance, node)

    def native(self, cfg: dict, instance: runtime.Instance):
        from ow174.game import assassin, body_entities, npc_context, sombra_emp

        result = sombra_emp.filter_body(self, cfg, instance)
        if result is not NotImplemented:
            return result
        result = assassin.native(self, cfg, instance)
        if result is NotImplemented:
            result = npc_context.native(self, cfg, instance)
        if result is not NotImplemented:
            return result
        primary = body_entities.primary_query(self, cfg, instance)
        if primary is not NotImplemented:
            return primary
        if self.hero == body_entities.DVA and self.match is not None and instance is not None:
            cls = cfg.get("$")
            if (cls == "STU_71A87E92" and instance.graph.index == 0x0B2F
                    and instance.evaluate(cfg.get("m_entity")) == expr.Entity(self.body)):
                position = self.match.script_body_position(self.body, self.body)
                return position if position is not None else expr.EVALUATION_FAILED
            if cls == "STU_FDBB0B67":
                from ow174.game.script.driver import BODY_SCALE_OVERRIDE, UNIT_ENTITY_SCALE

                if instance.evaluate(cfg.get("m_entity")) == expr.Entity(self.body):
                    slot = instance.component.entity_var(BODY_SCALE_OVERRIDE, create=False)
                    scale = slot.value() if slot is not None else None
                    if scale is None or (expr.kind(scale) in ("int", "float") and scale == UNIT_ENTITY_SCALE):
                        return UNIT_ENTITY_SCALE
            if cls == "STU_310D6529":
                from ow174.game.script.driver import THROTTLE_UNIT

                bot = self.match._message_actor(self.body)
                if bot is not None and bot.control_mover is not None:
                    right, forward = bot.control_mover.throttles
                    return expr.Vec3(expr.f32(right * THROTTLE_UNIT), expr.f32(forward * THROTTLE_UNIT), 0.0)
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
        if instance.graph.index == 0x1408 and self.match is not None:
            owner = cfg.get("m_entity")
            if isinstance(owner, dict) and owner.get("$") == "STU_B5A0CAF0":
                if cfg.get("$") == "STU_71A87E92":
                    position = self.match.script_body_position(self.body, self.body)
                    return position if position is not None else expr.EVALUATION_FAILED
                if cfg.get("$") in ("STU_2200A66D", "STU_021D01CD"):
                    from ow174.game.combat import aim

                    bot = self._manual_body(instance, "manual_charge")
                    return expr.Vec3(*aim(round(bot.yaw), 0)) if bot is not None else expr.EVALUATION_FAILED
        if (cfg.get("$") == "STU_71A87E92" and instance.graph.index == 0x1422
                and self.match is not None):
            entity = expr.to_entity(instance.evaluate(cfg.get("m_entity")))
            position = self.match.script_body_position(entity, instance.component.entity)
            return position if position is not None else expr.EVALUATION_FAILED
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
        from ow174.game import assassin, npc_charge, sombra_emp

        result = sombra_emp.modify_health(self, state, elapsed_ms)
        if result is not NotImplemented:
            return result
        if (state.instance.graph.index, state.index) == npc_charge.CONTACT[:2]:
            bot = self._manual_body(state.instance, "manual_charge")
            if bot is not None and not elapsed_ms:
                self.match.combat.npc_charge_damage(bot, state)
                state.request_finish()
            return True
        if self.detached_ultimate(state, 22):
            if not elapsed_ms:
                self.match.combat.dva_ultimate_damage(state)
                state.request_finish()
            return True
        return assassin.modify_health(self, state, elapsed_ms)

    def track_targets(self, state):
        if self.detached_ultimate(state, 8):
            self.match.combat.dva_ultimate_targets(state)
            return
        from ow174.game import npc_charge, npc_control

        instance = state.instance
        site = instance.graph.index, state.node.pos
        charge = site == (npc_charge.GRAPH, npc_charge.CONTACT[2])
        spec = npc_charge.CONTACT if charge else npc_control.MELEE_GRAPHS.get(self.hero)
        if spec is None or site != (spec[0], spec[2]):
            return
        bot = self._manual_body(instance, "manual_charge" if charge else "manual_melee")
        if bot is None:
            return
        slot = expr.dynamic(state.node.field("m_8A2905A0"))
        if slot is not None:
            targets = self.match.combat.npc_melee_targets(bot, instance, state.node)
            instance.write(slot, tuple(expr.Entity(target.entity) for target in targets))
            state.ticks()

    def _manual_body(self, instance, flag):
        if self.match is None:
            return None
        return next((b for b in self.match.bots
                     if b.entity == self.body and not b.dead and b.spawner.get(flag)
                     and b.body_script is not None and b.body_script.component is instance.component), None)

    def detached_ultimate(self, state, position):
        if self.match is None or state.instance.graph.index != 0x0B2F or state.node.pos != position:
            return False
        bot = self.match._message_actor(self.body)
        return bot is not None and bot.detached_bomb and bot.body_script.component is state.instance.component

    def end_health(self, state):
        from ow174.game import assassin

        assassin.end_health(self, state)


class BotScript(ReplicatedBody):
    """Run the NPC's body and supported placement behavior on the server clock."""

    def __init__(
        self, body: int, hero: int, entity: int, frame: int, mode: int, health: float,
        *, firing_position: tuple | None = None,
        match=None,
        controller_components: tuple[int, ...] | None = None,
    ) -> None:
        record = graph.entity_body(body)
        if record is None:
            raise ValueError(f"no statescript data for entity body {body:016X}")
        self.frame = frame
        buttons = record.get("ai_weapon_buttons") or {}
        firing = graph.graph(FIRING_GRAPH) if buttons and firing_position is not None else None
        position = expr.Vec3(*firing_position) if firing is not None else None
        world = BotWorld(hero, mode, buttons, position, body=entity, match=match,
                         controller_components=controller_components)
        component = self.component = runtime.Component(entity, world=world, seed=entity if firing else 1)
        component.frame = frame
        component.now = frame * component.frame_ms
        if match is not None:
            match.register_body_component(component)
        try:
            self.set_health(health, health)
            for instance in create_body_graphs(component, record):
                instance.init()
            component.run_frame(frame)
            self.server_only = self.initial_exclusions()
            self.behavior = component.create(firing) if firing is not None else None
            if self.behavior is not None:
                # Other0013 receivers keep their own reset policy.
                runtime.send_instance_message(self.behavior, RESET, {}, entity)
                component.run_now()
            snapshot = observers.snapshot(self)
            self.latest_live = snapshot
            self._initial_frame: BitWriter | None = observers.full_frame(self, snapshot)
            self.death_frame: int | None = None
            self._produced = snapshot
        except Exception:
            self.retire()
            raise

    def _initial_payload(self) -> BitWriter:
        if self._initial_frame is None:
            self._initial_frame = observers.full_frame(self, self.latest_live)
        return self._initial_frame

    def retire(self) -> None:
        """Disconnect a failed/retired VM namespace while retaining its stored history."""
        if self.component.world.match is not None:
            self.component.world.match.combat.clear_pins(self.component.entity)
            self.component.world.match.unregister_body_component(self.component)
        self.component.disconnect_foreign_watchers()
        self.component.world.match = None
        self.component.world.manual_target = None

    @property
    def initial(self) -> tuple[BitWriter, Snapshot]:
        """Encode the current live baseline only when a viewer needs its full frame."""
        return self._initial_payload(), self.latest_live

    def replicated_instances(self):
        return (instance for instance in super().replicated_instances() if instance is not self.behavior)

    def advance_live(
        self, frame: int, position: tuple | None = None, *, edges=(), publish=True,
    ) -> Snapshot | None:
        """Advance a live body, optionally freezing its latest observer baseline."""
        if frame <= self.frame or self.component.owner_dead:
            return None
        self.frame = frame
        if self.behavior is not None and position is not None:
            self.component.world.owner_position = expr.Vec3(*position)
        self.component.run_frame(frame, edges)
        from ow174.game import assassin

        assassin.refresh_control(self.component.world, self.component)
        self._report_shots()
        if not publish:
            return None
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
        from ow174.game import assassin, sombra_emp

        assassin.bind(component.world, component, None)
        sombra_emp.retire_applications(component)
        # A bot can die in the tick that created it. Its initial frame is already
        # frozen, so run the terminal event after the bootstrap simulation frame.
        self.frame = max(frame, self.frame + 1)
        component.owner_dead = True
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
