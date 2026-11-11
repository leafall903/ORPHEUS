"""The NPC body's statescript, shared by all observers."""

import math

from ow174.game import assassin, npc_context, npc_melee, observers
from ow174.game.bits import BitWriter
from ow174.game.bots import FIRING, heading
from ow174.game.content import PRACTICE_RANGE_MODE
from ow174.game.script import b61, entity_filter, entity_queries, expr, graph, message_receiver, message_state, runtime
from ow174.game.script.entity_team import FilterBits
from ow174.game.script.driver import (
    OWNER_HEALTH, MatchBodyWorld, ReplicatedBody, Snapshot, create_body_graphs, create_initial_graphs,
)

DIED = 0x024000000000003E  # 003E.025, the body's authoritative death message
RESET = 0x0240000000000013  # 0013.025, the placement's eligibility reset
FIRING_GRAPH = FIRING & 0xFFFF
REFILL_WAITS = {0x102E: 18, 0x1122: 15, 0x1134: 14, 0x114F: 15}
PRACTICE_NAMEPLATE = 0x0D24
PRACTICE_NAMEPLATE_PARENT = 0x0C18
PRACTICE_BOT_HEROES = frozenset(range(0x02E000000000016B, 0x02E000000000016F))
ASSASSIN_CONTROLLER = 0x0400000000001EBE
ASSASSIN_TARGETING = 0x13C2
ASSASSIN_ATTACK = assassin.CONTROLLER_ATTACK
CONTROLLER_NO_OUTPUT = {
    0x0C83: frozenset(("STU_4FC9D3E1", "STU_7DCA538E")),
    0x1172: frozenset(("STU_47077281",)),
}
PRACTICE_NAMEPLATE_FILTER = {"$": "STUConfigVarFilter", "m_84065FD1": 0x3FFFFFFF, "m_4EA686DB": 0x100}


def practice_nameplate(snapshot: Snapshot) -> int | None:
    for instance, (graph_id, parent) in snapshot.instances.items():
        if (
            graph_id == PRACTICE_NAMEPLATE and parent is not None and parent[1] == 3
            and snapshot.instances.get(parent[0], (None, None))[0] == PRACTICE_NAMEPLATE_PARENT
        ):
            return instance
    return None


class BotWorld(MatchBodyWorld):
    """Resolve the supported authored predicates and input bindings for an NPC body."""

    def __init__(
        self, hero: int, mode: int, weapon_buttons: dict[str, int], owner_position: expr.Vec3 | None = None,
    ) -> None:
        super().__init__(hero, mode)
        self.weapon_buttons = weapon_buttons
        self.owner_position = owner_position
        self.manual_controller_context = None
        self.manual_controller_target = None
        self.manual_controller_descriptor = None
        self.manual_target = None
        self.match = None

    def lookup_manual_controller(self, entity):
        target = self.manual_controller_target
        return target if target is not None and target.entity == entity else None

    def resolve_manual_controller(self, target):
        return self.manual_controller_descriptor if target is self.manual_controller_target else None

    def watch_manual_controller(self, watcher, target):
        pass  # declared static manual composition;native polling tracks the query

    def entity_controller_context(self, entity: int):
        context = self.manual_controller_context
        return context if context is not None and context.owner == entity else NotImplemented

    def entity_var(self, entity: int, var: int):
        descriptor = self.manual_controller_descriptor
        context = self.manual_controller_context
        if (self.hero == assassin.HERO and descriptor is not None and context is not None
                and not context.retired and entity == descriptor.entity and var == 1650):
            return expr.Entity(self.manual_target) if self.manual_target is not None else None
        return super().entity_var(entity, var)

    def modify_health(self, state, elapsed_ms):
        impact = super().modify_health(state, elapsed_ms)
        if impact is not NotImplemented:
            return impact
        return assassin.modify_health(self, state, elapsed_ms)

    def end_health(self, state):
        assassin.end_health(self, state)

    def track_targets(self, state):
        npc_melee.track(self, state)

    def movement_flags(self, entity: int):
        return self.match.script_movement_flags(entity, 0) if self.match is not None else NotImplemented

    def send_message(self, target: int, message: int, params: dict, sender: int) -> None:
        if (self.match is not None
                and self.match.queue_body_message(target, message, params, sender, source_world=self)):
            return
        super().send_message(target, message, params, sender)

    def body_health(self, entity: int, instance: runtime.Instance):
        if self.match is None or instance.graph.index in (assassin.GRAPH, npc_context.ENFORCER_WEAPON):
            return NotImplemented
        return self.match.combat.body_health(entity)

    def filtered_body_health(self, entity: int, kind: int, tag: int, instance: runtime.Instance):
        return self.match.combat.filtered_body_health(entity, kind) if self.match is not None else NotImplemented

    def ai_weapon_button(self, slot: int) -> int | None:
        return self.weapon_buttons.get(str(slot))

    def selects_ai_behavior(self, instance: runtime.Instance) -> bool:
        # Reconstruction policy for the single authored firing placement behavior.
        return self.owner_position is not None and instance.graph.index == FIRING_GRAPH

    def native(self, cfg: dict, instance: runtime.Instance):
        from ow174.game import orisa_halt

        halt = orisa_halt.native(self, cfg, instance)
        if halt is not NotImplemented:
            return halt
        result = assassin.native(self, cfg, instance)
        if result is NotImplemented:
            result = npc_context.native(self, cfg, instance)
        if result is not NotImplemented:
            return result
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


class AssassinControllerWorld(MatchBodyWorld):
    """The separate controller's declared possession and match queries."""

    def __init__(self, match, mode, controller, body):
        super().__init__(assassin.HERO, mode)
        self.match = match
        self.body = body
        priority = {"m_priority": "0x0C40000000000003"}
        self.records = b61.Registry({expr.guid_of(priority["m_priority"]): expr.stack_priority(priority)})
        self.messages = None
        self.motion_request = None
        self.pin_input = None
        self.target = entity_queries.Target(
            controller, entity_queries.Possession(body), possessor=None, body_field=entity_queries.BodyField(body),
        )
        self.context = entity_queries.Context(
            controller, True, True, True, lookup=self.lookup, watch_environment=None, watch_possession=self.watch,
            watch_possessor=None,
        )
        self.target_filter = entity_filter.Context(
            controller, True, lookup=self.filter_target,
            source=entity_filter.Target(controller, None, False, None, None, None),
            relation_enemy=self.filter_enemy, watch_environment=None, watch_filter=self.watch_target_filter,
            watch_tags=None,
        )

    def lookup(self, entity):
        return self.target if entity == self.target.entity else None

    def watch(self, watcher, target):
        pass  # Possession is immutable for this controller's mounted lifetime.

    def selects_ai_behavior(self, instance):
        # Run the targeting channel independently of the attack candidates.
        return instance.graph.index == ASSASSIN_TARGETING

    def register_motion_request(self, state):
        if (state.instance.graph.index, state.node.pos) != (ASSASSIN_ATTACK, 2):
            return NotImplemented
        if self.motion_request is not None:
            state.instance.component.unsupported(state.instance, "duplicate Assassin motion request", state.node.cls,
                                                 state.node)
        self.motion_request = state  # This controller has one authored request at default priority.
        return True

    def unregister_motion_request(self, state):
        if self.motion_request is state:
            self.motion_request = None

    def set_ai_button(self, state, active):
        if (state.instance.graph.index, state.node.pos) != (ASSASSIN_ATTACK, 12):
            return NotImplemented
        if not active:
            if self.pin_input is state:
                self.pin_input = None
            return True
        if (self.pin_input is not None or self.context.retired
                or state.evaluate(state.node.field("m_14EDDDA7"), watch=False) != expr.Entity(self.body)
                or state.evaluate(state.node.field("m_7E2A700B"), watch=False) != assassin.PIN_BUTTON
                or state.evaluate(state.node.field("m_5E1D2E9C"), watch=False) is not False):
            return NotImplemented
        self.pin_input = state
        return True

    def face_position(self, state):
        if (state.instance.graph.index, state.node.pos) != (ASSASSIN_ATTACK, 102):
            return NotImplemented
        request = self.motion_request
        if request is None or request.instance is not state.instance or not request.active:
            return True
        actor = next((bot for bot in self.match.bots if bot.entity == self.body and not bot.dead), None)
        if (actor is None or actor.body_script is None
                or actor.body_script.assassin_controller is not state.instance.component
                or actor.attack_input or self.match.combat.controlled(actor.entity)):
            return True
        position = state.evaluate(state.node.field("m_CC7C6F4B"), watch=False)
        if not isinstance(position, expr.Vec3):
            return True
        dx, dz = position.x - actor.position[0], position.z - actor.position[2]
        if dx or dz:
            yaw = heading(dx, dz)
            if actor.yaw != yaw:
                actor.yaw = float(yaw)
                actor.moved_tick = self.match.tick
        return True

    def entity_filter_context(self, entity, *, component=None):
        return self.target_filter if entity == self.context.owner else NotImplemented

    def hostile_players(self):
        actor = next((bot for bot in self.match.bots if bot.entity == self.body and not bot.dead), None)
        if actor is None:
            return ()
        return tuple(target for target in self.match.combat._enemies(actor)
                     if getattr(target.owner, 'client', None) is not None and not target.owner.gone)

    def filter_target(self, entity):
        if any(target.entity == entity for target in self.hostile_players()):
            # The authored 13C2 search tests 0x40000 and 1158 node 26 tests 0x1.
            return entity_filter.Target(entity, FilterBits(0x40001), True, None, None, None)
        return None

    def filter_enemy(self, spectator, target, source):
        return True  # Only Match-hostile player bodies enter this filter projection.

    def watch_target_filter(self, watcher, target, bits):
        pass  # Native config dependencies are polled at command boundaries.

    def tracked_targets(self, state):
        if (state.instance.graph.index, state.node.pos) != (ASSASSIN_TARGETING, 4):
            return NotImplemented
        from ow174.game.combat import capsule_visible

        owner = expr.to_entity(state.evaluate(state.node.field('m_8DCA77B6'), watch=False))
        fight = self.match.combat
        source = fight._target_entity(owner)
        if owner != self.body or source is None:
            return ()
        x, y, z = source.feet
        origin = (x, y + source.eye, z)
        radius = expr.to_float(state.evaluate(state.node.field('m_radius'), watch=False))
        definition = state.node.field('m_filter')
        collision = self.match.collision_world
        if collision is None:
            return ()
        candidates = []
        for target in self.hostile_players():
            distance = math.dist(source.feet, target.feet)
            if (distance > radius or fight.walls.blocked(origin, fight._eye(target.owner))
                    or not capsule_visible(collision, origin, *target.segment(), target.radius)):
                continue
            cfg = {'$': entity_filter.CLASS, 'm_entity': expr.Entity(target.entity), 'm_filter': definition}
            if expr.truthy(state.evaluate(cfg, watch=False)):
                candidates.append((distance, target.entity))
        return tuple(expr.Entity(entity) for _, entity in sorted(candidates))

    def entity_query_context(self, entity):
        return self.context if entity == self.context.owner else NotImplemented

    def b61_registry(self, entity):
        return self.records if entity == self.context.owner else NotImplemented

    def mount_messages(self, component):
        if self.messages is not None or component.world is not self or component.entity != self.context.owner:
            raise ValueError("persistent message host requires its controller's new component")
        host = message_state.Host(notify=message_receiver.notify)
        self.messages = message_state.Context(
            component, host, policy=self.sender_policy, frame=self.message_frame,
            priority=self.message_priority, receiver_policy=self.receiver_policy,
            receiver_generation=self.receiver_generation,
        )

    def message_state_context(self, entity, *, component=None):
        if (self.messages is not None and self.messages.component is component
                and entity == self.context.owner):
            return self.messages
        return NotImplemented

    def sender_policy(self, state):
        return message_state.Policy(False, False, False)

    def receiver_policy(self, state):
        # The detached controller is server-authoritative and has no local client role.
        return message_receiver.Policy(False, False, False, False, False, False, True, False, False, False)

    def message_frame(self, state):
        return expr.i32(state.instance.component.frame)

    def receiver_generation(self, state):
        return state.instance.component.frame & 0x7FFFFFFF

    def message_priority(self, state, resource):
        priority = self.records.priority(expr.Asset(resource))
        return NotImplemented if priority is NotImplemented else message_state.Priority(*priority)

    def script_failed(self, component):
        self.target_filter.retire()
        if self.messages is not None:
            self.messages.host.retire()

    def native(self, cfg, instance):
        cls = cfg.get("$")
        if cls == "STU_A3DEF368":
            return expr.Entity(runtime.GAME_MODE_ENTITY)
        if instance is not None and instance.graph.index == ASSASSIN_ATTACK and cls == "STU_71A87E92":
            entity = expr.to_entity(instance.evaluate(cfg.get("m_entity")))
            position = self.position(entity)
            return position if position is not None else expr.EVALUATION_FAILED
        if instance is not None and cls in CONTROLLER_NO_OUTPUT.get(instance.graph.index, ()):
            return expr.EVALUATION_FAILED  # The captured loader caches the base no-output evaluator.
        return super().native(cfg, instance)

    def position(self, entity):
        return self.match.script_body_position(entity, self.body)

    def movement_flags(self, entity):
        return self.match.script_movement_flags(entity, self.body)

    def body_health(self, entity, instance):
        return self.match.combat.body_health(entity)

    def filtered_body_health(self, entity, kind, tag, instance):
        return self.match.combat.filtered_body_health(entity, kind)

    def target_record_scalar(self, owner, target):
        if owner == self.context.owner:
            return None  # Match acquisition supplies candidates, but no component46 record payloads.
        return NotImplemented


class BotScript(ReplicatedBody):
    """Run the NPC's body and supported placement behavior on the server clock."""

    def __init__(
        self, body: int, hero: int, entity: int, frame: int, mode: int, health: float,
        *, firing_position: tuple | None = None, controller_components: tuple | None = None, match=None,
    ) -> None:
        record = graph.entity_body(body)
        if record is None:
            raise ValueError(f"no statescript data for entity body {body:016X}")
        self.frame = frame
        self.assassin_controller = None
        buttons = record.get("ai_weapon_buttons") or {}
        firing = graph.graph(FIRING_GRAPH) if buttons and firing_position is not None else None
        position = expr.Vec3(*firing_position) if firing is not None else None
        world = BotWorld(hero, mode, buttons, position)
        world.match = match
        world.manual_controller_context = npc_context.context(world, entity, controller_components)
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

    def mount_assassin_controller(self) -> None:
        body_world = self.component.world
        descriptor = body_world.manual_controller_descriptor
        if (body_world.hero != assassin.HERO or body_world.match is None or descriptor is None
                or self.assassin_controller is not None):
            return
        record = graph.entity_body(ASSASSIN_CONTROLLER)
        if record is None or not record["graphs"]:
            return
        world = AssassinControllerWorld(body_world.match, body_world.mode, descriptor.entity, self.component.entity)
        component = runtime.Component(descriptor.entity, world=world, seed=descriptor.entity, strict=True)
        roots = create_initial_graphs(component, record)
        if len(roots) != len(record["graphs"]):
            return
        self.assassin_controller = component
        world.mount_messages(component)
        component.now = self.component.now
        component.frame = self.frame
        for instance in roots:
            instance.init()
        self.advance_assassin_controller(self.frame)

    def advance_assassin_controller(self, frame: int) -> None:
        controller = self.assassin_controller
        if controller is None or controller.owner_dead or controller.fault is not None:
            return
        try:
            controller.run_frame(frame)
        except (runtime.UnsupportedScriptError, runtime.ScriptBudgetError):
            pass  # Retain the diagnostic on the isolated controller VM.

    def retire_assassin_controller(self) -> None:
        controller, self.assassin_controller = self.assassin_controller, None
        if controller is not None:
            controller.world.context.retire()
            controller.world.target_filter.retire()
            controller.owner_dead = True
            controller.world.messages.host.retire()
            for instance in tuple(controller.instances.values()):
                controller.destroy(instance)
            controller.world.records.clear()

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
        self.advance_assassin_controller(frame)
        self.frame = frame
        if self.behavior is not None and position is not None:
            self.component.world.owner_position = expr.Vec3(*position)
        self.component.run_frame(frame)
        assassin.refresh(self.component.world, self.component)
        self._report_shots()
        return self.capture_live()

    def capture_live(self) -> Snapshot | None:
        snapshot = self._changed_snapshot()
        if snapshot is not None:
            self.latest_live = snapshot
            self._initial_frame = None
        return snapshot

    def freeze_live_baseline(self) -> None:
        self._initial_payload()

    def die(self, frame: int, params: dict, sender: int) -> Snapshot | None:
        """Deliver 003E.025 and capture the change that happens immediately."""
        component = self.component
        self.retire_assassin_controller()
        assassin.bind(component.world, component, None)
        if component.owner_dead:
            return None
        if frame < self.frame:
            raise ValueError("bot death predates its script")
        # A terminal graph may remove instances needed to encode the last live baseline.
        self.freeze_live_baseline()
        # A bot can die in the tick that created it. Its initial frame is already
        # frozen, so run the terminal event after the bootstrap simulation frame.
        self.frame = max(frame, self.frame + 1)
        component.owner_dead = True
        # Retained bodies tick for death animation. A pending refill must end on death.
        for instance in component.instances.values():
            index = REFILL_WAITS.get(instance.graph.index)
            if index is not None and (wait := instance.states.get(index)) is not None:
                wait.end(False)
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
