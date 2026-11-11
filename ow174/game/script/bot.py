"""The NPC body's statescript, shared by all observers."""

from ow174.game import assassin, observers
from ow174.game.bits import BitWriter
from ow174.game.bots import FIRING
from ow174.game.content import PRACTICE_RANGE_MODE
from ow174.game.script import expr, graph, runtime
from ow174.game.script.driver import OWNER_HEALTH, ReplicatedBody, Snapshot, create_body_graphs

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

    sniper_target = None
    sniper_retry_at = 0

    def __init__(
        self, hero: int, mode: int, weapon_buttons: dict[str, int], owner_position: expr.Vec3 | None = None,
        *, controller_components: tuple[int, ...] | None = None,
    ) -> None:
        super().__init__(hero, mode)
        self.weapon_buttons = weapon_buttons
        self.owner_position = owner_position
        self.controller_components = controller_components
        self.match = None
        self.manual_target = None

    def body_health(self, entity):
        return NotImplemented if self.match is None else self.match.combat.body_health(entity)

    def controller_record(self, entity):
        return NotImplemented if self.match is None else self.match.controller_record(entity)

    def resolved_animation_ms(self, state):
        if (assassin.scoped(self, state.instance) and state.node.pos == 31
                and expr.guid_of(state.node.field("m_11E0A658.m_animAlias")) == 0x0C80000000000561
                and expr.guid_of(state.node.field("m_22326315.m_category")) == 0x0280000000000082):
            # Blend tree 0640 selects 508A.006 at unit playback rate.
            return 1467
        return super().resolved_animation_ms(state)

    def ai_weapon_button(self, slot: int) -> int | None:
        if slot == 0 and assassin.manual(self):
            return assassin.BUTTON
        return self.weapon_buttons.get(str(slot))

    def tracked_targets(self, state):
        from ow174.game import npc

        instance = state.instance
        if (self.match is not None and self.hero == npc.TROOPER_HERO
                and (instance.graph.index, state.node.pos) == (0x1034, 16)):
            return tuple(expr.Entity(entity) for entity in
                         npc.trooper_coordination_targets(self.match.combat, instance.component.entity, state))
        if (self.match is not None and not instance.component.strict
                and instance.graph.index == 0x1408 and state.node.pos in (32, 96, 100)):
            return self.match.combat.heavy_charge_targets(state)
        spec = npc.MELEE_GRAPHS.get(self.hero)
        if (self.match is None or instance.component.strict or spec is None
                or (instance.graph.index, state.node.pos) != (spec[0], spec[2])):
            return NotImplemented
        bot = next((body for body in self.match.bots if body.entity == instance.component.entity), None)
        if bot is None or bot.dead:
            return ()
        return tuple(expr.Entity(target.entity) for target in
                     self.match.combat.npc_melee_targets(bot, instance, state.node))

    def play_script_selected(self, instance, node):
        if self.match is None:
            return NotImplemented
        return self.match.combat.sombra_controls.play(instance, node)

    def skill_locked(self, state):
        return (self.match is not None and not state.instance.component.strict
                and self.match.combat.sombra_controls.blocked(state))

    def modify_health(self, state, elapsed_ms):
        instance = state.instance
        if (self.match is not None and not instance.component.strict
                and (instance.graph.index, state.node.pos) == (0x0B32, 9)):
            if elapsed_ms == 0:
                self.match.combat.sombra_controls.drain(state)
                state.request_finish()
            return True
        if self.match is not None and not instance.component.strict and instance.graph.index == 0x1408:
            if state.node.pos == 35:
                self.match.combat.heavy_charge_damage(state, fresh=elapsed_ms == 0)
                return True
            if state.node.pos in (29, 102):
                if elapsed_ms == 0:
                    self.match.combat.heavy_charge_damage(state, fresh=True)
                    state.request_finish()
                return True
        return assassin.modify_health(self, state, elapsed_ms)

    def selects_ai_behavior(self, instance: runtime.Instance) -> bool:
        # Reconstruction policy for the single authored firing placement behavior.
        return self.owner_position is not None and instance.graph.index == FIRING_GRAPH

    def native(self, cfg: dict, instance: runtime.Instance):
        from ow174.game import npc_context, sniper_retreat

        value = sniper_retreat.native(self, cfg, instance)
        if value is NotImplemented:
            value = assassin.native(self, cfg, instance)
        if value is NotImplemented:
            value = npc_context.native(self, cfg, instance)
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
        if instance.graph.index == 0x1408 and self.match is not None and not instance.component.strict:
            owner = cfg.get("m_entity")
            if isinstance(owner, dict) and owner.get("$") == "STU_B5A0CAF0":
                entity = instance.component.entity
                kind = cfg.get("$")
                if kind == "STU_71A87E92":
                    return self.match.script_body_position(entity, entity)
                if kind in ("STU_2200A66D", "STU_021D01CD"):
                    from ow174.game.combat import aim

                    bot = next((b for b in self.match.bots if b.entity == entity and not b.dead), None)
                    return expr.Vec3(*aim(round(bot.yaw), 0)) if bot is not None else None
        if (cfg.get("$") == "STU_71A87E92" and self.match is not None
                and instance.graph.index in (0x1422, 0x140E) and not instance.component.strict):
            entity = expr.to_entity(instance.evaluate(cfg.get("m_entity")))
            return self.match.script_body_position(entity, instance.component.entity)
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
        controller_components: tuple[int, ...] | None = None,
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
        self.component.run_frame(frame)
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
        if component.world.manual_target is not None:
            assassin.bind(component, None)
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
