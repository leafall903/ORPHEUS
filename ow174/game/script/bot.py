"""The NPC body's statescript, shared by all observers."""

from ow174.game import observers
from ow174.game.bits import BitWriter
from ow174.game.bots import FIRING
from ow174.game.content import PRACTICE_RANGE_MODE
from ow174.game.script import expr, graph, runtime
from ow174.game.script.driver import (
    OWNER_HEALTH,
    ReplicatedBody,
    Snapshot,
    create_body_graphs,
    create_initial_graphs,
)

DIED = 0x024000000000003E  # 003E.025, the body's authoritative death message
RESET = 0x0240000000000013  # 0013.025, the placement's eligibility reset
FIRING_GRAPH = FIRING & 0xFFFF
RELOAD_WAITS_ON_DEATH = {
    0x102E: 18,  # Trooper
    0x1122: 15,  # Sniper primary
    0x1134: 14,  # Sniper alternate
    0x114F: 15,  # Heavy Assault
    0x13EB: 19,  # Enforcer
}
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
        *, controller_components: tuple[int, ...] | None = None, body_definition: int | None = None,
    ) -> None:
        super().__init__(hero, mode)
        self.weapon_buttons = weapon_buttons
        self.body_definition = body_definition
        self.owner_position = owner_position
        self.controller_components = controller_components
        self.match = None
        self.manual_target = None
        self.controller = None
        self.enforcer_selected = False

    def entity_var(self, entity, var):
        controller = self.controller
        if controller is not None and not controller.owner_dead and entity == controller.entity:
            slot = controller.entity_var(var, create=False)
            return slot.value() if slot is not None else None
        return super().entity_var(entity, var)

    def position(self, entity):
        from ow174.game import assassin, npc_context

        if (self.hero in (npc_context.ENFORCER, assassin.HERO) and self.controller_components is None
                and self.match is not None):
            target = next((t for t in self.match.combat._targets() if t.entity == entity), None)
            if target is not None:
                return expr.Vec3(*target.feet)
        return super().position(entity)

    def ai_weapon_button(self, slot: int) -> int | None:
        if self.hero == 0x02E00000000001BB and self.controller_components is not None and slot == 0:
            return 36
        return self.weapon_buttons.get(str(slot))

    def selects_ai_behavior(self, instance: runtime.Instance) -> bool:
        # Reconstruction policy for the single authored firing placement behavior.
        return self.owner_position is not None and instance.graph.index == FIRING_GRAPH

    def game_message_owner(self, instance, node):
        from ow174.game import assassin

        if self.hero == assassin.HERO and instance.graph.index == assassin.CONTROLLER_GRAPH:
            return instance.component.entity
        return super().game_message_owner(instance, node)

    def game_message_source(self, instance, node):
        self.game_message_owner(instance, node)
        return instance.source_entity()

    def submit_game_message(self, instance, node, request):
        from ow174.game import assassin
        from ow174.game.script import message_receive

        if (self.hero == assassin.HERO and instance.graph.index == assassin.CONTROLLER_GRAPH
                and request.message == assassin.CONTROLLER_ANIMATION_MESSAGE):
            receiver = assassin.controller_message_receiver(self, instance)
            if (receiver is not None and request.owner == instance.component.entity
                    and request.targets == (receiver.entity,) and not request.self_target):
                packet = message_receive.Packet(request.message, request.owner, request.instigator, request.params)
                message_receive.deliver(receiver, packet)
                return
        super().submit_game_message(instance, node, request)

    def native(self, cfg: dict, instance: runtime.Instance):
        from ow174.game import assassin, npc_context

        kind = cfg.get("$")
        if (kind == "STU_1A3FA5B0" and self.body_definition is not None
                and instance is not None and instance.graph.index == 0x143A
                and instance.evaluate(cfg.get("m_entity")) == expr.Entity(instance.component.entity)):
            return expr.Asset(self.body_definition)
        if (kind == "STU_F7FD94F9" and self.hero == 0x02E00000000001BA
                and instance is not None and instance.graph.index == 0x1408
                and instance.evaluate(cfg.get("m_entity")) == expr.Entity(instance.component.entity)):
            from ow174.game import heavy_charge
            from ow174.game.mover import mover_data

            # Native D17470 returns a scaled stance radius. Manual Heavy has
            # a standing capsule and unit model scale in the server mover.
            return expr.f32(mover_data(heavy_charge.BODY).stand_radius)

        if (kind in ("STU_71A87E92", "STU_021D01CD") and self.hero == 0x02E00000000001BA
                and instance is not None and instance.graph.index == 0x1408 and self.match is not None
                and instance.evaluate(cfg.get("m_entity")) == expr.Entity(instance.component.entity)):
            from ow174.game import combat

            owner = instance.component.entity
            bot = next((b for b in self.match.bots if b.entity == owner and not b.dead), None)
            if bot is not None:
                return expr.Vec3(*(bot.position if kind == "STU_71A87E92" else combat.aim(round(bot.yaw), 0)))

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
        from ow174.game import assassin, npc_melee

        value = npc_melee.modify_health(self, state, elapsed_ms)
        return assassin.modify_health(self, state, elapsed_ms) if value is NotImplemented else value

    def end_health(self, state):
        from ow174.game import assassin

        assassin.end_health(self, state)


class BotScript(ReplicatedBody):
    """Run the NPC's body and supported placement behavior on the server clock."""

    def __init__(
        self, body: int, hero: int, entity: int, frame: int, mode: int, health: float,
        *, firing_position: tuple | None = None,
        controller_components: tuple[int, ...] | None = None,
        controller_entity: int | None = None,
    ) -> None:
        record = graph.entity_body(body)
        if record is None:
            raise ValueError(f"no statescript data for entity body {body:016X}")
        self.frame = frame
        buttons = record.get("ai_weapon_buttons") or {}
        firing = graph.graph(FIRING_GRAPH) if buttons and firing_position is not None else None
        position = expr.Vec3(*firing_position) if firing is not None else None
        world = BotWorld(hero, mode, buttons, position, controller_components=controller_components,
                         body_definition=body)
        component = self.component = runtime.Component(entity, world=world, seed=entity if firing else 1)
        self.controller = None
        if controller_entity is not None:
            from ow174.game import npc_context

            definition = npc_context.controller_definition(body)
            if (definition is None or type(controller_entity) is not int
                    or not 0 < controller_entity <= 0xFFFFFFFF or controller_entity == entity):
                raise ValueError("invalid associated NPC controller")
            controller = runtime.Component(controller_entity, world=BotWorld(hero, mode, {}))
            self.controller = world.controller = controller
            controller.frame = frame
            controller.now = frame * controller.frame_ms
            for instance in create_initial_graphs(controller, definition):
                instance.init()
                if instance.graph.index == npc_context.CONTROLLER_GRAPH:
                    instance.play_schema((), reorder_entries=True)
            controller.run_frame(frame)
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

    def advance_live(self, frame: int, position: tuple | None = None, *, buttons=()) -> Snapshot | None:
        """Advance a live weapon body and freeze its latest observer baseline."""
        if frame <= self.frame or self.component.owner_dead:
            return None
        self.frame = frame
        if self.behavior is not None and position is not None:
            self.component.world.owner_position = expr.Vec3(*position)
        if self.controller is not None and not self.controller.owner_dead:
            from ow174.game import assassin, npc_context

            if self.component.world.hero == assassin.HERO:
                assassin.advance_controller(self.controller, frame)
            else:
                npc_context.advance_controller(self.controller, frame)
        self.component.run_frame(frame, buttons)
        from ow174.game import assassin

        assassin.refresh_control(self.component.world, self.component)
        self._report_shots()
        return self.capture_live()

    def capture_live(self) -> Snapshot | None:
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
        self.retire_controller()
        # A bot can die in the tick that created it. Its initial frame is already
        # frozen, so run the terminal event after the bootstrap simulation frame.
        self.frame = max(frame, self.frame + 1)
        component.owner_dead = True
        # Retained corpses still tick for death animation. Abort the verified
        # weapon refill waits before those ticks can refill a dead magazine.
        for instance in component.instances.values():
            wait_index = RELOAD_WAITS_ON_DEATH.get(instance.graph.index)
            if wait_index is not None and (reload_wait := instance.states.get(wait_index)) is not None:
                reload_wait.end(False)
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

    def retire_controller(self):
        from ow174.game import assassin

        controller = self.controller
        if controller is not None and not controller.owner_dead:
            if self.component.world.hero == assassin.HERO:
                self.component.edges([(36, False)])
            controller.owner_dead = True
            controller.world.manual_target = None
            controller.world.enforcer_selected = False
            controller.held.clear()
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
