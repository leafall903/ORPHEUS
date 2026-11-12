"""The NPC body's statescript, shared by all observers."""

from ow174.game import observers
from ow174.game.bits import BitWriter
from ow174.game.bots import FIRING
from ow174.game.content import PRACTICE_RANGE_MODE
from ow174.game.script import expr, graph, runtime
from ow174.game.script.driver import OWNER_HEALTH, ReplicatedBody, Snapshot, create_body_graphs

DIED = 0x024000000000003E  # 003E.025, the body's authoritative death message
RESET = 0x0240000000000013  # 0013.025, the placement's eligibility reset
FIRING_GRAPH = FIRING & 0xFFFF
REBUILD_GRAPH = 0x0BA8  # the Training Bot's body graph: its 0013 entry starts the Anim that rebuilds it
PRACTICE_NAMEPLATE = 0x0D24
PRACTICE_NAMEPLATE_PARENT = 0x0C18
ENABLE_NAMEPLATE = 0x0240000000000C3C
NAMEPLATE_ENABLED = 8807
NAMEPLATE_NAME = 8812  # 226C.01C, 0D24's optional display name
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
        *, controller_components: tuple[int, ...] | None = None, manual_nameplate: bool = False,
    ) -> None:
        super().__init__(hero, mode)
        self.weapon_buttons = weapon_buttons
        self.owner_position = owner_position
        self.controller_components = controller_components
        self.manual_nameplate = manual_nameplate
        self.match = None
        self.manual_target = None

    def ai_weapon_button(self, slot: int) -> int | None:
        if self.hero == 0x02E00000000001BB and self.controller_components is not None and slot == 0:
            return 36
        return self.weapon_buttons.get(str(slot))

    def selects_ai_behavior(self, instance: runtime.Instance) -> bool:
        # Reconstruction policy for the single authored firing placement behavior.
        return self.owner_position is not None and instance.graph.index == FIRING_GRAPH

    def native(self, cfg: dict, instance: runtime.Instance):
        from ow174.game import assassin, npc_context

        value = assassin.native(self, cfg, instance)
        if value is NotImplemented:
            value = npc_context.native(self, cfg, instance)
        if value is not NotImplemented:
            return value
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

    def tracked_targets(self, state):
        from ow174.game import heavy_assault

        return heavy_assault.tracked_targets(self, state)

    def modify_health(self, state, elapsed_ms):
        from ow174.game import assassin, heavy_assault

        value = heavy_assault.modify_health(self, state, elapsed_ms)
        return assassin.modify_health(self, state, elapsed_ms) if value is NotImplemented else value

    def end_health(self, state):
        from ow174.game import assassin

        assassin.end_health(self, state)

    def voice_stimulus_began(self, state):
        from ow174.game import heavy_assault

        heavy_assault.emit_charge_voice(self, state)


class BotScript(ReplicatedBody):
    """Run the NPC's body and supported placement behavior on the server clock."""

    def __init__(
        self,
        body: int,
        hero: int,
        entity: int,
        frame: int,
        mode: int,
        health: float,
        *,
        firing_position: tuple | None = None,
        controller_components: tuple[int, ...] | None = None,
        manual_nameplate: bool = False,
        nameplate_name: str | None = None,
        manual_rialto_spawn: bool = False,
        respawned: bool = False,
    ) -> None:
        from ow174.game import heavy_assault

        record = graph.entity_body(body)
        if record is None:
            raise ValueError(f"no statescript data for entity body {body:016X}")
        self.frame = frame
        buttons = record.get("ai_weapon_buttons") or {}
        firing = graph.graph(FIRING_GRAPH) if buttons and firing_position is not None else None
        position = expr.Vec3(*firing_position) if firing is not None else None
        world = BotWorld(
            hero,
            mode,
            buttons,
            position,
            controller_components=controller_components,
            manual_nameplate=manual_nameplate,
        )
        component = self.component = runtime.Component(entity, world=world, seed=entity if firing else 1)
        component.frame = frame
        component.now = frame * component.frame_ms
        self.set_health(health, health)
        for instance in create_body_graphs(component, record):
            instance.init()
            if manual_rialto_spawn and hero == heavy_assault.HERO:
                heavy_assault.skip_sandbox_intro(instance)
        component.run_frame(frame)
        self.server_only = self.initial_exclusions()
        if respawned:
            # Reconstruction: a bot made again at its spawner gets the reset on its rebuild graph, whose
            # 0013 entry starts the Anim that puts a Training Bot together (docs/research/bot-rebuild.md).
            rebuilds = [item for item in component.instances.values() if item.graph.index == REBUILD_GRAPH]
            for instance in rebuilds:
                runtime.send_instance_message(instance, RESET, {}, entity)
            component.run_now()
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
        return (instance for instance in super().replicated_instances() if instance is not self.behavior)

    def show_damage_nameplate(self, sender: int) -> None:
        if not self.component.world.manual_nameplate:
            return
        snapshot = self.latest_live
        child = practice_nameplate(snapshot)
        if child is None or snapshot.vars.get((child, NAMEPLATE_ENABLED), (None,))[0] is True:
            return
        instance = self.component.instances.get(child)
        if instance is not None:
            runtime.send_instance_message(instance, ENABLE_NAMEPLATE, {}, sender)

    def advance_live(self, frame: int, position: tuple | None = None) -> Snapshot | None:
        """Advance a live weapon body and freeze its latest observer baseline."""
        if frame <= self.frame or self.component.owner_dead:
            return None
        self.frame = frame
        if self.behavior is not None and position is not None:
            self.component.world.owner_position = expr.Vec3(*position)
        self.component.run_frame(frame)
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
