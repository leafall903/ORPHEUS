"""The NPC body's statescript, shared by all observers."""

import math

from ow174.game import assassin, heavy_charge, observers
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
ENFORCER, ENFORCER_PRIMARY = 0x02E00000000001CE, 0x13EB
TROOPER, TROOPER_MELEE = 0x02E00000000001AC, 0x1422
SNIPER, SNIPER_PRIMARY = 0x02E00000000001B8, 0x1122
MANUAL_ENFORCER_CONTROLLER = frozenset({0x4B})
PRIMARY_WINDUPS = {ENFORCER: (ENFORCER_PRIMARY, 11), SNIPER: (SNIPER_PRIMARY, 34)}


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
        *, match=None, manual_control: bool = False,
    ) -> None:
        super().__init__(hero, mode)
        self.weapon_buttons = weapon_buttons
        self.owner_position = owner_position
        self.match = match
        self.manual_control = manual_control
        self.manual_target = None

    def _manual_graph(self, instance, hero, graph_id):
        return (self.hero == hero and self.manual_control and self.match is not None
                and instance.graph.index == graph_id)

    def _heavy_attack(self, state, graph_id, position):
        return (self._manual_graph(state.instance, heavy_charge.HERO, graph_id)
                and state.node.pos == position)

    def track_targets(self, state):
        if self._heavy_attack(state, heavy_charge.GRAPH, heavy_charge.SEARCH):
            return heavy_charge.search(self.match.combat, state)
        if self._heavy_attack(state, 0x1408, 100):
            return self.match.combat.npc_contact_targets(state, required_target_flag=961)
        if self._heavy_attack(state, 0x140E, 6) or self._heavy_attack(state, 0x1408, 32):
            return self.match.combat.npc_contact_targets(state)
        if self._manual_graph(state.instance, TROOPER, TROOPER_MELEE) and state.node.pos == 5:
            return self.match.combat.npc_contact_targets(state)
        return NotImplemented

    def raycast_state(self, state):
        if (self.hero == 0x02E00000000001BA and self.manual_control and self.match is not None
                and state.instance.graph.index == 0x1408 and state.node.pos in (170, 172)):
            self.match.combat.heavy_charge_wall_probe(state)

    def host_modify_health(self, state, elapsed_ms):
        trooper_contact = self._manual_graph(state.instance, TROOPER, TROOPER_MELEE) and state.node.pos == 4
        melee_contact = trooper_contact or self._heavy_attack(state, 0x140E, 5)
        if melee_contact or any(self._heavy_attack(state, 0x1408, position) for position in (35, 102)):
            if elapsed_ms == 0:
                self.match.combat.npc_contact_hit(state, knockback=melee_contact)
            return True
        return assassin.modify_health(self, state, elapsed_ms)

    def host_end_health(self, state):
        assassin.end_health(self, state)

    def ai_weapon_button(self, slot: int) -> int | None:
        return self.weapon_buttons.get(str(slot))

    def selects_ai_behavior(self, instance: runtime.Instance) -> bool:
        # Reconstruction policy for the single authored firing placement behavior.
        return self.owner_position is not None and instance.graph.index == FIRING_GRAPH

    def native(self, cfg: dict, instance: runtime.Instance):
        if cfg.get("$") == "STU_71A87E92" and (
            self._manual_graph(instance, TROOPER, TROOPER_MELEE)
            or self._manual_graph(instance, heavy_charge.HERO, 0x140E)
        ):
            entity = expr.to_entity(instance.evaluate(cfg.get("m_entity")))
            for target in self.match.combat._targets():
                if target.entity == entity:
                    return expr.Vec3(*target.feet)
        if (cfg.get("$") == "STU_2200A66D" and self.hero == 0x02E00000000001BA
                and self.manual_control and self.match is not None and instance.graph.index == 0x1408
                and instance.evaluate(cfg.get("m_entity")) == expr.Entity(instance.component.entity)):
            from ow174.game.combat import aim

            source = next((bot for bot in self.match.bots if bot.entity == instance.component.entity
                           and bot.body_script is not None
                           and bot.body_script.component is instance.component and not bot.dead), None)
            if source is not None:
                return expr.Vec3(*aim(source.yaw, source.pitch))

        value = assassin.native(self, cfg, instance)
        if value is not NotImplemented:
            return value
        if (cfg.get("$") == "STU_B96D3A98" and self.hero == ENFORCER and self.manual_control
                and instance.graph.index == ENFORCER_PRIMARY
                and instance.evaluate(cfg.get("m_entity")) == expr.Entity(instance.component.entity)):
            # CC9F61 tests controller component 4B present and 5C absent.
            # The manual spawn policy supplies this profile for the local weapon only.
            return 0x4B in MANUAL_ENFORCER_CONTROLLER and 0x5C not in MANUAL_ENFORCER_CONTROLLER
        if cfg.get("$") == "STU_9B7648C3":
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
        *, firing_position: tuple | None = None, match=None, manual_control: bool = False,
    ) -> None:
        record = graph.entity_body(body)
        if record is None:
            raise ValueError(f"no statescript data for entity body {body:016X}")
        self.frame = frame
        buttons = record.get("ai_weapon_buttons") or {}
        firing = graph.graph(FIRING_GRAPH) if buttons and firing_position is not None else None
        position = expr.Vec3(*firing_position) if firing is not None else None
        world = BotWorld(hero, mode, buttons, position, match=match, manual_control=manual_control)
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

    def charge_velocity(self):
        """Read the active 1408 replacement vector for a manual Heavy."""
        component = self.component
        if component.world.hero != 0x02E00000000001BA or not component.world.manual_control:
            return None
        for instance in component.instances.values():
            if instance.graph.index != 0x1408:
                continue
            node = instance.graph.node(71)
            state = instance.states.get(node.state) if node is not None else None
            if state is not None and state.active and not state.ending:
                rows = node.field('m_BC5E91CF') or ()
                row = next((row for row in rows if row and row.get('$') == 'STU_09F9B1FE'
                            and row.get('m_CFEC5F76') == 0), None)
                if row is None:
                    return None
                value = state.evaluate(row["m_9B7A63EA"], watch=False)
                if isinstance(value, expr.Vec3) and all(math.isfinite(axis) for axis in value):
                    return tuple(value)
        return None

    def cancel_primary_windup(self) -> None:
        """Abort a pending volley through its authored graph state."""
        windup = PRIMARY_WINDUPS.get(self.component.world.hero)
        if windup is None:
            return
        graph_id, position = windup
        for instance in self.component.instances.values():
            if instance.graph.index == graph_id:
                state = instance.states.get(instance.graph.node(position).state)
                if state is not None and state.active:
                    state.request(runtime.ABORT)

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
    ) -> Snapshot | None:
        """Advance a live body, optionally delivering one explicit AI weapon-slot edge.

        The catalog's ai_weapon_buttons owns the slot mapping. This input adapter
        runs authored weapon graphs; it does not select targets or implement AI.
        Duplicate held input cannot synthesize additional press edges.
        """
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
        if frame <= self.frame or self.component.owner_dead:
            return None
        self.frame = frame
        if self.behavior is not None and position is not None:
            self.component.world.owner_position = expr.Vec3(*position)
        self.component.run_frame(frame, edges)
        assassin.refresh_control(self.component.world, self.component)
        heavy_charge.refresh_control(self.component.world, self.component)
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
        assassin.bind(component.world, component, None)
        # A bot can die in the tick that created it. Its initial frame is already
        # frozen, so run the terminal event after the bootstrap simulation frame.
        self.frame = max(frame, self.frame + 1)
        component.owner_dead = True
        if self.behavior is not None:
            component.destroy(self.behavior)
            self.behavior = None
        self.cancel_primary_windup()
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
