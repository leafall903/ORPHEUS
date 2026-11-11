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
OWNED_PILOT_RULES = (9743, 9777, 9778, 10611)
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
        self.body = None
        self.manual_target = None

    def send_message(self, target, message, params, sender):
        if (self.match is not None and sender == self.body
                and self.match.queue_body_message(target, message, params, sender)):
            return
        super().send_message(target, message, params, sender)

    def dva_detonation_targets(self, state):
        from ow174.game import dva_destruct

        return dva_destruct.targets(self, state)

    def destroy_script_body(self, instance, node):
        site = instance.graph.index, node.pos
        if (self.match is None or instance.component.strict
                or site not in ((0x0B2F, 13), (0x303B, 5), (0x303B, 6))):
            return NotImplemented
        entity = expr.to_entity(instance.evaluate(node.field("m_entity")))
        bot = next((b for b in self.match.bots if b.owner and b.entity == entity
                    and b.body_script is not None and b.body_script.component is instance.component), None)
        if bot is None or (site == (0x0B2F, 13) and not bot.detached_bomb):
            return NotImplemented
        from ow174.game import npc

        npc.remove_body(self.match, bot)
        return True

    def ai_weapon_button(self, slot: int) -> int | None:
        if self.hero == 0x02E00000000001BB and self.controller_components is not None and slot == 0:
            return 36
        return self.weapon_buttons.get(str(slot))

    def selects_ai_behavior(self, instance: runtime.Instance) -> bool:
        # Reconstruction policy for the single authored firing placement behavior.
        return self.owner_position is not None and instance.graph.index == FIRING_GRAPH

    def _owned_pilot(self):
        if self.match is None or self.hero != 0x02E000000000007A:
            return None
        mech = next((b for b in self.match.bots if b.entity == self.body and b.owner), None)
        if mech is None or mech.detached_bomb:
            return None
        return next((p for p in self.match.players if p.entity == mech.owner
                     and p.has_body and p.body == mech.source_body and not p.gone), None)

    def script_component_present(self, entity):
        pilot = self._owned_pilot()
        if pilot is not None and pilot.body == entity:
            return True
        return super().script_component_present(entity)

    def entity_var(self, entity, var):
        # Possession lookups already poll. Read the pilot's value without adding
        # a foreign watcher to its VM, which is exported independently.
        pilot = self._owned_pilot() if var in OWNED_PILOT_RULES else None
        if pilot is not None and pilot.body == entity and pilot.body_script is not None:
            slot = pilot.body_script.component.entity_var(var, create=False)
            return slot.value() if slot is not None else None
        return super().entity_var(entity, var)

    def entity_ruleset(self, entity, key):
        pilot = self._owned_pilot() if key in OWNED_PILOT_RULES else None
        if pilot is not None and pilot.body == entity:
            return self.ruleset(key)
        return super().entity_ruleset(entity, key)

    def native(self, cfg: dict, instance: runtime.Instance):
        from ow174.game import assassin, npc_context

        value = assassin.native(self, cfg, instance)
        if value is NotImplemented:
            value = npc_context.native(self, cfg, instance)
        if value is not NotImplemented:
            return value
        if (self.match is not None and self.hero == 0x02E000000000007A
                and not instance.component.strict
                and instance.graph.index in (0x0276, 0x02CC, 0x047E, 0x0B2F)):
            cls = cfg.get("$")
            linked = cfg.get("m_entity") or {}
            if cls == "STU_0AC15F78" and linked.get("$") in ("STU_F7262B36", "STU_F76C486C"):
                owner = linked.get("m_801B298F", linked.get("m_D63CBEC2")) or {}
                if owner.get("$") == "STU_B5A0CAF0":
                    pilot = self._owned_pilot()
                    if pilot is None and linked.get("$") == "STU_F76C486C":
                        from ow174.game import dva_body

                        pilot = dva_body.charge_recipient(self.match, self.body)
                    return expr.Entity(pilot.body if pilot is not None else 0)
            if cls == "STU_FDBB0B67" and instance.evaluate(linked) == expr.Entity(self.body):
                from ow174.game.script.driver import BODY_SCALE_OVERRIDE
                from ow174.game.world import UNIT_ENTITY_SCALE

                slot = instance.component.entity_var(BODY_SCALE_OVERRIDE, create=False)
                scale = slot.value() if slot is not None else None
                if scale is None or (expr.kind(scale) in ("int", "float") and scale == UNIT_ENTITY_SCALE):
                    return UNIT_ENTITY_SCALE
            if cls == "STU_71A87E92" and instance.evaluate(linked) == expr.Entity(self.body):
                position = self.match.script_body_position(self.body, self.body)
                return position if position is not None else expr.EVALUATION_FAILED
        if (self.match is not None and self.hero == 0x02E000000000007A
                and cfg.get("$") == "STU_F7262B36"):
            entity = expr.to_entity(instance.evaluate(cfg.get("m_801B298F")))
            bot = next((b for b in self.match.bots if b.entity == entity and b.owner), None)
            if bot is not None:
                return expr.Entity(bot.owner)
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
        from ow174.game import assassin, dva_destruct

        result = dva_destruct.damage(self, state, elapsed_ms)
        return assassin.modify_health(self, state, elapsed_ms) if result is NotImplemented else result

    def end_health(self, state):
        from ow174.game import assassin

        assassin.end_health(self, state)


class BotScript(ReplicatedBody):
    """Run the NPC's body and supported placement behavior on the server clock."""

    def __init__(
        self, body: int, hero: int, entity: int, frame: int, mode: int, health: float,
        *, firing_position: tuple | None = None,
        controller_components: tuple[int, ...] | None = None, match=None,
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
        world.body = entity
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

    def advance_live(self, frame: int, position: tuple | None = None, *, edges=None) -> Snapshot | None:
        """Advance a live weapon body and freeze its latest observer baseline."""
        if frame <= self.frame or self.component.owner_dead:
            return None
        self.frame = frame
        if self.behavior is not None and position is not None:
            self.component.world.owner_position = expr.Vec3(*position)
        self.component.run_frame(frame, edges or [])
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
