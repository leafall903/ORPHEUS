"""The statescript runtime: graph instances on an entity, their states, variables and events, run the way
the client runs them, so that the server reaches the same states the owning client predicts.

Read in the client (the addresses are in the docstrings below):
- Time is int64 milliseconds. Command frame K is run as the window [16K, 16K+15]: an update of every
  instance at each time an event is due, and one at the window's end (component update 0x7FF78AA9C910,
  step 0x7FF78AA9DE30). An update drains the instance's events up to now, ticks the ticking states and
  runs the deferred list, until the deferred list is empty (0x7FF78AAA27C0).
- Events (dispatcher 0x7FF78AAA1690): 0 a state op (begin, finish, abort), 1 a timer, 2 a deferred
  completion check, 3 an Entry, 4 an assignment, 7 a stop, 8 a button edge, 9 a watched variable change.
  Inserted after every event of the same time (FIFO), never in the past.
- A plug walk (0x7FF78AA83FA0) follows the links in order. A state target only gets a begin (or abort)
  request: an event at now, so a state never begins inside a walk. Actions and conditions run at once.
  The server runs every node that is not client-only.
- DoBeginState 0x7FF78AA80790: exclusive states end, OnBegin, OnEnter, m_onBeginPlug, m_subgraphPlug, then
  the completion check (the transitions; a transition that is taken ends the state). DoAbortState
  0x7FF78AA80480 / DoFinishState 0x7FF78AA80B10: queued events dropped, the subgraph's states (the plug's
  m_FF3DAF1E list) aborted, the class's own end, then m_onAbortPlug / m_onFinishedPlug and m_onEndPlug.
- A variable is a base value plus links: the states that override it while they are active, in order, the
  last active one wins (resolver 0x7FF78AAB70B0). A Stack's entries are its links. Every variable a state
  reads while it evaluates registers the state as a watcher; a change sends it an event.

Node classes (nodes.py) subclass State and register their action handlers here.
"""

import bisect
import logging
from collections.abc import Callable
from contextlib import contextmanager
from dataclasses import dataclass

from ow174.game.script import expr
from ow174.game.script.expr import ENTITY, INSTANCE, Context, Entity
from ow174.game.script.graph import Graph, Node, is_plug, mode_variables
from ow174.game.world import GAME_MODE_ENTITY

log = logging.getLogger("ow174.script")

# Event kinds.
STATE_OP, TIMER, CHECK, ENTRY, ASSIGN, STOP, BUTTON, CHANGED, MESSAGE = 0, 1, 2, 3, 4, 7, 8, 9, 10
STOP_INSTANCE = 11
BEGIN, FINISH, ABORT = 0, 1, 2
NO_BUTTON = 171
EVENTS_PER_TIME = 1000  # the client stops an instance that queues more at one time
MAX_INSTANCE_UPDATES = 1000
MAX_COMPONENT_UPDATES = 10000
MAX_SCRIPT_WORK = 100000  # shared across all instances within one frame/update/plug operation
MAX_FOLLOW_DEPTH = 64  # stop immediate action cycles before Python's recursion limit
ABORT_PLUG = "m_F198FD3A"
SUBGRAPH_STATES = "m_FF3DAF1E"

STATE_CLASSES: dict[str, type["State"]] = {}  # node class -> runtime state class (nodes.py fills it)
STRICT_STATE_CLASSES: dict[str, type["State"]] = {}
SCOPED_STATE_CLASSES: dict[tuple[str, int, int], type["State"]] = {}
HANDLERS: dict[str, object] = {}  # action/condition handlers can defer with NotImplemented
ENTRY_CLASSES = {"STUStatescriptEntry", "STU_571CC72E"}  # run at Init (EntryClientOnlySharedVars agrees too)
MESSAGE_ENTRIES = {"STUStatescriptEntryGameMessage"}


class ScriptBudgetError(RuntimeError):
    """The component could not complete its simulation within a work/recursion limit."""


class UnsupportedScriptError(RuntimeError):
    """An authored mission path needs semantics the server has not implemented."""

    def __init__(self, instance: "Instance", feature: str, cls: str, node: Node | None = None) -> None:
        component = instance.component
        self.diagnostic = {
            "entity": component.entity, "instance": instance.id, "graph": instance.graph.index,
            "frame": component.frame, "time_ms": component.now,
            "feature": feature, "class": cls, "node": node.pos if node is not None else None,
        }
        super().__init__(
            f"graph {instance.graph.index:04X}, instance {instance.id}, node {self.diagnostic['node']}, "
            f"frame {component.frame}: unsupported {feature} {cls}"
        )


class EventSequence:
    """The next FIFO sequence number, observable without advancing the iterator."""

    __slots__ = ("_next_value",)

    def __init__(self, start: int = 0) -> None:
        if type(start) is not int or start < 0:
            raise ValueError("event sequence start must be a nonnegative integer")
        self._next_value = start

    @property
    def next_value(self) -> int:
        return self._next_value

    def __iter__(self):
        return self

    def __next__(self) -> int:
        value = self._next_value
        self._next_value += 1
        return value

    def __repr__(self) -> str:
        return f"EventSequence(next_value={self._next_value})"


def state_class(*names: str, strict_only: bool = False):
    def register(cls):
        registry = STRICT_STATE_CLASSES if strict_only else STATE_CLASSES
        for name in names:
            scope = getattr(cls, "state_scope", None)
            if strict_only and scope is not None:
                for position in scope[1]:
                    SCOPED_STATE_CLASSES[name, scope[0], position] = cls
            else:
                registry[name] = cls
        return cls

    return register


def state_type(name: str, strict: bool, graph_index=None, position=None):
    if strict and (model := SCOPED_STATE_CLASSES.get((name, graph_index, position))) is not None:
        return model
    if strict and name in STRICT_STATE_CLASSES:
        return STRICT_STATE_CLASSES[name]
    return STATE_CLASSES.get(name, State)


def handler(*names: str):
    def register(function):
        for name in names:
            HANDLERS[name] = function
        return function

    return register


@dataclass
class Link:
    """A state that overrides a variable while it is active: a Stack entry, a ChaseVar or volley output."""

    state: "State"
    slot: int = 0
    priority: float = 0.0
    above: bool = True  # among equal priorities a new entry goes above the others
    stack: bool = False  # a stack entry (Stack, an Ability's push): sent as a binding
    dynamic: bool = False  # Native binding+29; serialize the extra policy bit.
    frame_cached: bool = False  # Native binding+2A, encoded only with dynamic.


class Var:
    """One variable: the stored (base) value, the links that override it, the states that watch it."""

    __slots__ = ("base", "key", "links", "partial", "resolving", "watchers", "entity_owner")

    def __init__(self, key: tuple, entity_owner: "Component | None" = None) -> None:
        self.key = key  # (instance id or 0 for the entity, var id)
        self.base = None
        self.links: list[Link] = []
        self.watchers: dict[State, bool | None] = {}
        self.resolving = False
        self.partial = None
        self.entity_owner = entity_owner

    def value(self, below: "State | None" = None):
        # Native1BA70B0's in-resolution flag exposes the current lower value.
        # This also prevents a selection filter from re-entering its own link.
        if self.resolving:
            return self.partial
        self.resolving, self.partial = True, self.base
        try:
            for link in self.links:
                if link.state is below:
                    break
                if link.state.active:
                    supplied = link.state.output(link.slot)
                    if supplied is not expr.EVALUATION_FAILED:
                        self.partial = supplied
            return self.partial
        finally:
            self.resolving, self.partial = False, None

    def stack_states(self) -> list["State"]:
        return [link.state for link in self.links]


@dataclass
class Event:
    time: int
    seq: int
    kind: int
    state: "State | None" = None
    op: int = 0
    param: int = 0
    node: Node | None = None  # an Entry, or a message entry
    button: int = 0
    pressed: bool = False
    message: tuple | None = None  # (message GUID, params, sender)
    transient: bool = False  # a re-check at now, never in an owner frame's event list
    assignment: tuple | None = None  # (scope, variable identifier, evaluated value)

    def key(self):
        return self.time, self.seq


class InstanceContext(Context):
    """Evaluation context of one instance: variable reads register `watcher` (a state) when one is set."""

    def __init__(self, instance: "Instance", watcher: "State | None") -> None:
        self.instance = instance
        self.watcher = watcher
        self.rng = instance.component.rng

    def _read(self, var: Var | None):
        if var is None:
            return None
        if self.watcher is not None:
            self.watcher.watch(var)
            if self.watcher.reads_below(var):
                return var.value(below=self.watcher)
        return var.value()

    def var(self, scope: int, var: int):
        value = self._read(self.instance.find_var(scope, var, create=self.watcher is not None))
        if scope == ENTITY:
            return self.instance.component.world.entity_value(self.instance, self.watcher, var, value)
        return value

    def proxy_var(self, levels: int, scope: int, var: int):
        instance = self.instance
        for _ in range(levels):
            instance = instance.parent
            if instance is None:
                return None
        return self._read(instance.find_var(scope, var, create=self.watcher is not None))

    def proxy_entity(self, levels: int):
        return Entity(self.instance.component.entity)

    def param(self, var: int):
        return self.parameter_result(var)[1]

    def parameter_result(self, var: int, *, initial=None):
        """1B93200: reverse VM providers, carrying the destination through failures."""
        component = self.instance.component
        providers = [(item, False) for item in self.instance.params]
        providers.extend((item, True) for item in component.parameter_scopes)
        value = initial
        for provider, native_scope in reversed(providers):
            component.consume_work('parameter scope lookup')
            if provider is None and native_scope:
                break  # A null native stack entry terminates the search.
            lookup = getattr(provider, 'lookup_parameter', None)
            if callable(lookup):
                result = lookup(var, initial=value)
                component.ensure_valid()
                if (type(result) is not tuple or len(result) != 2
                        or type(result[0]) is not bool):
                    component.unsupported(self.instance, 'parameter scope result', type(provider).__name__)
                success, value = result
                if success:
                    return True, value
                continue
            if provider is not None and (var in provider or None in provider):
                return True, provider.get(var, provider.get(None))
        return False, value

    def owner(self):
        return Entity(self.instance.component.entity)

    def member(self, entity: int, var: int):
        component = self.instance.component
        if entity == component.entity:
            return self._read(component.entity_var(var, create=self.watcher is not None))
        slot = component.world.entity_var_slot(entity, var, create=self.watcher is not None)
        if slot is not NotImplemented:
            return self._read(slot)
        value = component.world.entity_var(entity, var)
        if value is NotImplemented:
            if component.strict:
                component.unsupported(self.instance, "entity variable service", f"{entity:08X}:{var}")
            return None
        return value

    def resolved_member(self, entity: int, var: int):
        # 1B80D97 writes +230 only after the namespace resolves a script VM.
        component = self.instance.component
        if entity == component.entity or component.world.script_component_present(entity) is True:
            self.mark_ruleset_target(entity)
        return self.member(entity, var)

    def position(self, entity: int):
        component = self.instance.component
        value = component.world.position(entity)
        if value is None:
            if component.strict:
                component.unsupported(self.instance, "entity position", f"{entity:08X}")
            return expr.Vec3(0.0, 0.0, 0.0)
        return value

    def ruleset_expression(self, config):
        from ow174.game import matrix

        hosted = self.instance.component.world.ruleset_expression(self, config)
        if hosted is not NotImplemented:
            return hosted
        if self.instance.graph.index != 0x2CC or self.instance.component.world.hero != matrix.HERO:
            return NotImplemented
        if not isinstance(config, dict) or config.get('$') != 'STU_919FD47C':
            return NotImplemented
        expression = config.get('m_expression') or {}
        fields = expression.get('m_dynamicVars') or ()
        if expression.get('m_opcodes') != [14, 0, 7, 0, 15, 0] or len(fields) != 1:
            return NotImplemented
        key = expr.dynamic(fields[0])
        if key is None or key[1] not in (9743, 9777, 9778):
            return NotImplemented
        value = expr.evaluate(config, self)
        return self.ruleset(key[1]) if value is None else value

    def ruleset(self, key: int):
        found = self.var(ENTITY, key)
        if found is not None:
            return found
        component = self.instance.component
        value = component.world.ruleset_for(component.entity, key)
        return 0 if value is None else value

    def reset_ruleset_target(self):
        self.instance.ruleset_target_entity = expr.INVALID_ENTITY

    def mark_ruleset_target(self, entity: int):
        self.instance.ruleset_target_entity = entity & 0xFFFFFFFF

    def ruleset_target(self):
        return self.instance.ruleset_target_entity

    def ruleset_default(self, key: int, entity: int):
        component = self.instance.component
        if not expr.valid_entity(entity) or entity == component.entity:
            return component.world.ruleset_for(component.entity, key)
        value = component.world.entity_ruleset(entity, key)
        if value is NotImplemented and component.strict:
            component.unsupported(self.instance, "foreign entity ruleset", f"{entity:08X}:{key}")
        return value

    def would_stack_to_top(self, var, priority):
        if var is None:
            return False
        target = self.instance.find_var(var[0], var[1], create=True)
        if self.watcher is not None:
            self.watcher.watch(target, structural=True)
        return would_stack_to_top(target, priority)

    def poll_native(self, cfg: dict):
        """Keep native scalar and iterator dependencies on the existing poll list."""
        if self.watcher is not None:
            self.watcher.natives.add(cfg.get("$"))
            self.watcher.native_dependency(cfg)
            if not self.watcher.polled:
                self.watcher.polled = True
                self.watcher.ticks()

    def fold(self, cfg: dict, *, initial=None):
        from ow174.game.script import fold

        component = self.instance.component
        if (not component.strict and component.world.hero == 0x02E0000000000006
                and getattr(component.world, "body_definition", None) is not None
                and self.instance.graph.index == 0x0B8B):
            # Delivered012 pool records are the explicit sandbox tag/loss
            # representation. Preserve its3EBB deduction before generic folds.
            from ow174.game import torbjorn_charge

            result = torbjorn_charge.native(component.world, cfg, self.instance)
            if result is not NotImplemented:
                return result
        return fold.evaluate(self.instance, cfg, initial=initial, watcher=self.watcher)

    def native(self, cfg: dict):
        """A native config's scalar +20 interface and compatibility polling."""
        cls = cfg.get("$")
        from ow174.game.script import spatial_queries

        if cls in spatial_queries.INTERFACES:
            result = spatial_queries.evaluate(self, cfg)
            if result is not NotImplemented:
                return result
        if cls in ("STU_7042F028", "STU_45B0D2B2"):
            from ow174.game.script import play

            if cls == play.AUTHORED_ARRAY:
                return play.evaluate_scalar_result(self.instance, cfg, watcher=self.watcher)[0]
            return play.authored_map(self.instance, cfg, watcher=self.watcher)
        if cls in ("STU_4BD39CFD", "STU_17330930", "STU_0C37A184", "STU_BD2D717B"):
            from ow174.game.script import play

            if cls == "STU_4BD39CFD":
                return play.array_item(self.instance, cfg, watcher=self.watcher)
            if cls == play.MODE_SCORE_THRESHOLDS:
                return play.evaluate_scalar_result(self.instance, cfg, watcher=self.watcher)[0]
            if cls == play.SESSION_ENTITIES:
                values = play.session_entities(self.instance, cfg, watcher=self.watcher)
                return values[0] if values else expr.EVALUATION_FAILED
            return play.minimum_value(self.instance, cfg, watcher=self.watcher)
        self.poll_native(cfg)
        from ow174.game.script import (
            body_owner, controller_heroes, controller_queries, entity_filter, entity_link, entity_queries, entity_team, health_queries,
        )

        component = self.instance.component
        if (not component.strict and component.world.hero == 0x02E0000000000016
                and getattr(component.world, "body_definition", None) is not None
                and self.instance.graph.index == 0x0B8B):
            from ow174.game import symmetra_charge

            result = symmetra_charge.native(component.world, cfg, self.instance)
            if result is not NotImplemented:
                return result
        if (not component.strict and component.world.hero == 0x02E0000000000016
                and getattr(component.world, "body_definition", None) is not None
                and self.instance.graph.index == 0x0033):
            from ow174.game import symmetra

            result = symmetra.pool_query(component.world, cfg, self.instance)
            if result is not NotImplemented:
                return result
        if (not component.strict and component.world.hero == 0x02E0000000000016
                and getattr(component.world, "body_definition", None) is not None
                and self.instance.graph.index == 0x157C):
            from ow174.game import symmetra

            result = symmetra.target_query(component.world, cfg, self.instance)
            if result is not NotImplemented:
                return result
        if (not component.strict and component.world.hero == 0x02E0000000000006
                and getattr(component.world, "body_definition", None) is not None
                and self.instance.graph.index == 0x0B8B):
            # Preserve the delivered Torb normal/practice gate before the newer
            # generic typed component43 query. Other owners keep their hosts.
            from ow174.game import torbjorn_charge

            result = torbjorn_charge.native(component.world, cfg, self.instance)
            if result is not NotImplemented:
                return result
            if cls == body_owner.CLASS:
                return False  # Body construction has not published its owner script yet.
        if (not component.strict and component.world.hero == 0x02E0000000000004
                and cls == body_owner.CLASS and self.instance.graph.index in (0x0B8B, 0x006B)):
            result = component.world.native(cfg, self.instance)
            if result is not NotImplemented:
                return result
        if (not component.strict and component.world.hero == 0x02E0000000000029
                and cls == body_owner.CLASS and self.instance.graph.index == 0x0B8B
                and getattr(component.world, "match", None) is not None
                and component.entity == getattr(component.world, "body", None)
                and (cfg.get("m_801B298F") or {}).get("$") == "STU_B5A0CAF0"):
            result = component.world.native(cfg, self.instance)
            if result is not NotImplemented:
                return result
        if cls == body_owner.CLASS:
            context = component.world.body_owner_context(component.entity)
            if type(context) is body_owner.Context and not context.instance_present:
                # Preserve the delivered unavailable-instance guard before any
                # alternate query provider evaluates the entity selector.
                return body_owner.evaluate(self, cfg)
        result = health_queries.evaluate(self, cfg)
        if result is NotImplemented:
            result = controller_queries.evaluate(self, cfg)
        if cls == body_owner.CLASS and self.instance.graph.index == 0x0B8B and not component.strict:
            from ow174.game import orisa_charge

            result = orisa_charge.native(component.world, cfg, self.instance)
        if result is NotImplemented:
            result = entity_link.evaluate(self, cfg)
        if result is NotImplemented:
            result = body_owner.evaluate(self, cfg)
        if result is NotImplemented:
            result = entity_queries.evaluate(self, cfg)
        if result is NotImplemented:
            result = controller_heroes.scalar(self, cfg)
        if result is NotImplemented:
            result = entity_team.evaluate(self, cfg)
        if result is NotImplemented:
            result = entity_filter.evaluate(self, cfg)
        if result is NotImplemented:
            return self.instance.component.world.native(cfg, self.instance)
        return result


def insert_link(var: Var, link: Link) -> None:
    """A Stack push (0x7FF78AA82F90 -> the sort of PS4 sub_172E790): entries ascending by priority, the last
    is the top; a new entry goes after every lower priority, above the equal ones when its flag is set,
    below them otherwise. Pushing the same state again replaces its entry."""
    var.links = [item for item in var.links if item.state is not link.state]
    at = len(var.links)
    for index, item in enumerate(var.links):
        if item.priority > link.priority or (item.priority == link.priority and not link.above):
            at = index
            break
    var.links.insert(at, link)


def would_stack_to_top(var: Var, priority: tuple[float, bool], exclude: "State | None" = None) -> bool:
    """Whether an entry with this priority would be the top of the stack variable now."""
    level, above = priority
    for link in var.links:
        if link.state is exclude or not link.state.active:
            continue
        if link.priority > level or (link.priority == level and not above):
            return False
    return True


@dataclass(frozen=True)
class RaycastRequest:
    """Resolved world-space inputs with the authored filter kept opaque."""

    origin: expr.Vec3
    direction: expr.Vec3
    length: float
    ignored_entities: tuple[int, ...]
    filter_config: dict | None
    radius: float = 0.0


@dataclass(frozen=True)
class RaycastResult:
    """All outputs of a completed cast, including the provider's values on a miss."""

    hit: bool
    entity: Entity | None
    point: expr.Vec3 | None
    normal: expr.Vec3 | None


class World:
    """What the runtime asks outside its entity: the owner's hero and mode (for ruleset defaults), other
    entities, config vars the server cannot compute, and game messages to other entities. The base
    answers like an empty world."""

    def respawn_body(self, instance, node):
        return NotImplemented

    def movement_flag_projection(self, instance, node):
        """Explicitly supported (known-bit mask, value), or unknown engine input."""
        return None

    def placement_confirmation(self, state):
        """Explicit world placement adapter, or an unknown engine service."""
        return NotImplemented

    def spawn_deployable(self, instance, node):
        """Explicit authored spawn adapter, or an unknown engine service."""
        return NotImplemented

    def completes_empty_marker(self, state: "State") -> bool:
        """Allow a host to complete an explicitly reconstructed empty marker."""
        return False

    def pet_lifecycle(self, state, entering):
        """Optional selected Pet host. Other worlds leave the lifecycle unbound."""

    raycast: Callable[[RaycastRequest], RaycastResult | None] | None = None
    for_code_transport: bool | None = None  # Native script VM +8 role result, when known.
    outline_context_available = NotImplemented
    local_combat_context_available: bool | None = None
    tips_context_available: bool | None = None
    ux_presenter_available: bool | None = None
    queue_mode_action = None
    queue_ghost_action = None
    queue_mystery_swap = None
    junkenstein_difficulty: expr.Asset | None = None

    def tracked_ray(self, state):
        """Explicit TrackRay query provider; unsupported differs from a completed miss."""
        return NotImplemented

    def search_position(self, state: "State"):
        return NotImplemented

    def __init__(self, hero: int = 0, mode: int = 0) -> None:
        self.hero = hero
        self.mode = mode

    def raycast_provider(self, instance: "Instance", node: Node):
        return self.raycast

    def physics_explosion(self, state: "State"):
        return NotImplemented

    def ruleset(self, key: int):
        from ow174.game.script import rulesets

        return rulesets.default(key, self.hero, self.mode)

    def ruleset_for(self, entity: int, key: int):
        return self.ruleset(key)

    def ruleset_expression(self, evaluation, config):
        return NotImplemented

    def entity_var(self, entity: int, var: int):
        if entity == GAME_MODE_ENTITY and self.mode:
            variables = mode_variables(self.mode)
            if variables is not None:
                return variables.get(var)
        return None

    def entity_component92_present(self, entity: int) -> bool | None:
        """Return the known component presence for a bound scene entity."""
        return None

    def register_display_name_override(self, state, entity: int, name: int):
        return NotImplemented

    def entity_var_slot(self, entity: int, var: int, *, create: bool = False):
        """A declared live foreign variable slot, known absence, or unknown service."""
        return NotImplemented

    def script_component_present(self, entity: int):
        """Explicit member-expression VM resolution; unknown is not a resolved VM."""
        return NotImplemented

    def entity_ruleset(self, entity: int, key: int):
        """Declared foreign body's ruleset default, after its variable read failed."""
        return NotImplemented

    def position(self, entity: int):
        return expr.Vec3(0.0, 0.0, 0.0)

    def chase_clock(self, state):
        return NotImplemented

    def external_wait(self, state):
        return False

    def compatibility_elapsed_condition(self, instance, node):
        return NotImplemented

    def controller_button(self, state: "State", pressed: bool) -> None:
        """Host transport for a supported controller logical-button state."""
        if state.instance.component.strict:
            state.instance.component.unsupported(state.instance, "state", state.node.cls, state.node)

    def ai_weapon_button(self, slot: int) -> int | None:
        return None

    def velocity_action(self, instance: "Instance", node: Node):
        return NotImplemented

    def linked_tracked_targets(self, state: "State"):
        return NotImplemented

    def move_ended(self, state: "State", finished: bool):
        pass

    def grapple_cast(self, state: "State"):
        return None

    def tracked_targets(self, state: "State"):
        return NotImplemented

    def ghost_model_action(self, component, entity):
        return NotImplemented

    def set_ghost_transform(self, component, entity, position, yaw):
        return NotImplemented

    def ghost_chase_target_is_live(self, instance):
        return NotImplemented

    def static_target_tag(self, tag: int):
        """A unique bound map target, or NotImplemented for unresolved lookup."""
        return NotImplemented

    def evaluate_static_target_tag(self, query, evaluate):
        """The unfiltered tag-query shape shared by the state and entity config."""
        primary = query("m_9C7BD8B8")
        required = query("m_B486603E")
        if (query("m_filter") is not None or query("m_8B2A4DF3") is not None
                or (primary is None) == (required is None)):
            return NotImplemented
        tag = evaluate(primary if primary is not None else required)
        if isinstance(tag, expr.Asset) and tag.guid >> 48 == 0x0D60:
            return self.static_target_tag(tag.guid)
        return NotImplemented

    def query_participants(self, instance, filter_config, *, required_tag=None):
        return NotImplemented

    def routed_game_message_targets(self, instance, node, message):
        return NotImplemented

    def static_tag_targets(self, tag: int | None):
        return NotImplemented

    def targets_with_tag(self, instance, tag: int, *, excluded_tag: int | None = None):
        return NotImplemented

    def resolve_spray(self, length: float) -> dict | None:
        return None

    def target_effects(self, state: "State"):
        return NotImplemented

    def bind_combat_damage_filter(self, state: "State"):
        return NotImplemented

    def unbind_combat_damage_filter(self, state: "State") -> None:
        pass

    def emit_contact_effect(self, state: "State", entity: int, contact_set: int, targets: tuple):
        return NotImplemented

    def run_effect_entity_action(self, instance: "Instance", node):
        return NotImplemented

    def request_corpse_retirement(self, instance: "Instance", entity: int, *, phase_exit=False):
        return NotImplemented

    def objective_route_active(self, instance: "Instance"):
        return NotImplemented

    def movement_snapshot(self, instance: "Instance"):
        return NotImplemented

    def player_stats_available(self, instance: "Instance"):
        return NotImplemented

    def grant_target_tag(self, state: "State", tag: int):
        return NotImplemented

    def clear_target_tag(self, state: "State") -> None:
        pass

    def ability_start_allowed(self, state: "State") -> bool:
        return True

    def track_targets(self, state: "State") -> bool:
        """Return true when the selection must refresh every VM update."""
        return False

    def compatibility_message_receiver(self, state: "State"):
        return NotImplemented

    def compatibility_stop_script(self, instance: "Instance", node: Node):
        return NotImplemented

    def target_tag_entities(self, tag: int):
        """Ordered entity IDs for an explicitly known single-tag search."""
        return NotImplemented


    def resolved_animation_ms(self, state: "State"):
        """Optional duration of the resolved finite animation for this activation."""
        return NotImplemented


    def skill_locked(self, state: "State") -> bool:
        return False


    def body_health(self, entity: int):
        """(current, maximum), None for missing entity, or an unknown service.

        An explicitly known entity without component51 supplies (0.0, 0.0).
        """
        return NotImplemented


    def component51_state_counter(self, entity: int):
        """Explicit component51 +4EC storage, known absence, or unknown service."""
        return NotImplemented


    def filtered_body_health(self, entity: int, kind: int, tag: int):
        """Current selected component51 pool total from an explicit host."""
        return NotImplemented


    def selects_ai_behavior(self, instance: "Instance") -> bool:
        return False

    def modify_health(self, state: "State", elapsed_ms: int):
        return NotImplemented

    def end_health(self, state: "State") -> None:
        pass

    def set_health_pool(self, state: "State", active: bool):
        return NotImplemented

    def spatial_binding_owner(self, entity: int):
        """An explicit 96333BBE auxiliary owner, or None for known absence."""
        return NotImplemented

    def component45_spatial_context(self, entity: int):
        """Explicit component45 spatial scheduler, lookup and registration services."""
        return NotImplemented

    def assassin_movement_sample(self, entity: int):
        """Explicit 1158 movement sample, known absence, or an unknown service."""
        return NotImplemented

    def hosts_assassin_pursuit_callbacks(self, entity: int):
        """Whether the owner has a 1158 callback host, or an unknown service."""
        return NotImplemented

    def component76_keys(self, entity: int):
        """Explicit component76 keys, known absence, or unknown service."""
        return NotImplemented

    def indexed_target_lookup(self, entity: int):
        """The A37A910D instance service, known absence, or unknown service."""
        return NotImplemented

    def target_record_scalar(self, owner: int, target: int):
        """A D379F515 component46 record scalar, known absence, or unknown service."""
        return NotImplemented

    def controller_record(self, entity: int):
        """Explicit controller_queries.Record, None missing, or unknown service.

        Component43's related ID is independent of the primary-possessor registry.
        """
        return NotImplemented

    def entity_filter_record(self, entity: int):
        """Explicit classification record, known absence or unknown service."""
        return NotImplemented

    def array_filter_accepts(self, state: "State", value):
        """Apply the authored 0A6BE10E filter to a candidate value."""
        return NotImplemented

    def property_modifiers(self, entity: int):
        """An explicitly known native component 31, None if absent, or unknown."""
        return NotImplemented

    def linked_target_sample(self, entity: int):
        """Return (linked entity or None, yaw radians), None if absent, or NotImplemented."""
        return NotImplemented

    def model_gate_consumer(self, entity: int):
        """Component1 bit-0 setter, known absence, or unknown service."""
        return NotImplemented

    def b61_registry(self, entity: int):
        """Explicit registry for B61 state records."""
        return NotImplemented

    def component86(self, entity: int):
        """Explicit target registry, known absence, or unknown service."""
        return NotImplemented

    def native_state_factory_miss(self, cls: str) -> bool:
        """Whether an explicit host catalog establishes an empty native state slot."""
        return False

    def entity_value(self, instance, state, identifier: int, value):
        return value

    def subgraph_scopes(self, entity: int):
        """Known auxiliary-interface registration service, known no service, or unknown."""
        return NotImplemented

    def auxiliary_effect_context(self, entity: int):
        """Known native context, known absent engine context, or unknown."""
        return NotImplemented

    def auxiliary_effect_target(self, entity: int):
        """Known component43 descriptor, known component absence, or unknown."""
        return NotImplemented

    def entity_control_context(self, entity: int):
        """Known native engine context/consumers, known absence, or unknown."""
        return NotImplemented

    def voice_stimulus_began(self, state: "State") -> None:
        """Optional presentation hook for permissive combat worlds."""

    def voice_category_service(self, instance):
        """Return the host callback or declare a known absent voice service with None."""
        return NotImplemented

    def voice_context(self, entity: int):
        """Explicit shared voice host/manager and separate +80 poll owner."""
        return NotImplemented

    def apply_voice_set(self, state, definition: int):
        return NotImplemented

    def clear_voice_set(self, state) -> None:
        pass

    def set_global_data_flow(self, state, resource: int, value: bool):
        return NotImplemented

    def clear_global_data_flow(self, state) -> None:
        pass

    def animation_seconds(self, state: "State") -> float | None:
        """Return a resolved authored animation duration when available."""
        return None

    def animation_aliases(self, entity: int):
        """Known component55 alias list, known absence, or unknown."""
        return NotImplemented

    def model_look_context(self, entity: int):
        """Explicit native namespace, target policy and model-look consumers."""
        return NotImplemented

    def data_flow_context(self, entity: int):
        """Explicit component47 mapping registry and native namespace availability."""
        return NotImplemented

    def body_owner_context(self, entity: int):
        return NotImplemented

    def entity_query_context(self, entity: int):
        """Explicit active query namespace, environment, entity and possession lookup."""
        return NotImplemented

    def entity_definition_resource(self, entity: int):
        """Declared entity-definition GUID, typed zero for missing, or unknown."""
        return NotImplemented

    def controller_hero_context(self, entity: int, *, component: "Component | None" = None):
        """Ordered native hero catalog, controller lookup and availability services."""
        return NotImplemented

    def set_script_entity_team(self, instance, target, team):
        return NotImplemented

    def filter_array_candidate(self, state, candidate, config):
        return NotImplemented

    def notify_entity_filter_change(self, entity: int) -> None:
        pass

    def submit_spawn_search(self, state, request, complete):
        return NotImplemented

    def register_ai_control_record(self, state, target, record):
        return NotImplemented

    def register_ai_facing_record(self, state, target, record):
        return NotImplemented

    def set_ai_control_setting(self, state, value, flag):
        return NotImplemented

    def register_ai_weapon_record(self, state, target, record):
        return NotImplemented

    def register_ai_behavior_request(self, state, record):
        return NotImplemented

    def health_pool_capacity(self, entity, kind):
        return NotImplemented

    def add_wave_health(self, state, amount):
        return NotImplemented

    def suppress_same_team_contact(self, state, entity):
        return NotImplemented

    def session_script_entities(self, instance):
        return NotImplemented

    def mode_score_threshold_row(self):
        return NotImplemented

    def entity_team_context(self, entity: int, *, component: "Component | None" = None):
        """Native context, two entity lookups and filter/environment dependencies."""
        return NotImplemented

    def script_failed(self, component: "Component") -> None:
        """Discard external effects of a component that strict execution poisoned."""

    def matches_entity_filter(self, entity: int, cfg, *, instance, watcher):
        """CF4340 environment+150; unknown engine admission must not become false."""
        from ow174.game.script import entity_filter

        host = self.entity_filter_host(instance)
        if host is not NotImplemented:
            entity_filter._check_owner(instance)
            return entity_filter.admit(host, entity, cfg, instance=instance, watcher=watcher)
        return NotImplemented

    def entity_filter_host(self, instance):
        """Current entity components and native filter services for this instance."""
        return NotImplemented

    def native(self, cfg: dict, instance: "Instance | None"):
        if cfg.get("$") == "STU_A77AC2AC" and self.mode:
            from ow174.game.content import TUTORIAL_MODE

            return self.mode == TUTORIAL_MODE  # CE8580 compares the selected mode resource.
        if cfg.get("$") == "STU_A3DEF368" and self.mode and mode_variables(self.mode) is not None:
            return Entity(GAME_MODE_ENTITY)
        if instance is not None and instance.component.strict and cfg.get("$") not in expr.NATIVE_DEFAULTS:
            instance.component.unsupported(instance, "world query", cfg.get("$", "?"))
        return expr.default_native(cfg)

    def send_message(self, target: int, message: int, params: dict, sender: int) -> None:
        expr.warn_once(f"message{message:X}", "game message %X to %08X is not delivered", message, target)

    def send_script_message(self, instance: "Instance", node: Node, target: int,
                            message: int, params: dict, sender: int) -> None:
        """Strict authored messages require a declared cross-component transport."""
        instance.component.unsupported(instance, 'game message transport', node.cls, node)

    def game_message_owner(self, instance: "Instance", node: Node):
        instance.component.unsupported(instance, 'game message owner', node.cls, node)

    def game_message_source(self, instance: "Instance", node: Node):
        instance.component.unsupported(instance, 'game message source', node.cls, node)

    def validate_game_message_source(self, instance: "Instance", node: Node) -> None:
        component = instance.component
        component.ensure_valid()
        if (instance.stopped or instance.teardown
                or component.instances.get(instance.id) is not instance):
            component.unsupported(instance, 'game message source lifecycle', node.cls, node)

    def submit_game_message(self, instance: "Instance", node: Node, request) -> None:
        instance.component.unsupported(instance, 'game message submission', node.cls, node)

    def game_message_filter(self, instance: "Instance", node: Node, config, caller: int):
        """None: known absent compiled filter; bool: known predicate result."""
        return NotImplemented

    def replicate_wheel_event(self, instance: "Instance", node: Node) -> None:
        pass

    def message_state_context(self, entity: int, *, component: "Component | None" = None):
        """Persistent State manager, explicit native frame/role/priority services."""
        return NotImplemented

    def game_message_subject(self, instance: "Instance", node: Node, instigator: int):
        instance.component.unsupported(instance, 'game message subject', node.cls, node)

    def animation_duration(self, instance: "Instance", node: Node) -> float | None:
        """Resolve the animation duration for the current model, if available."""
        return None

    def animation_scalar(self, instance: "Instance", node: Node) -> float | None:
        """Resolve a phase-independent Anim scalar for the current model, if available."""
        return None

    def finish_wheel_emote(self, instance: "Instance", node: Node):
        return NotImplemented

    def create_world_entity(self, instance: "Instance", node: Node):
        return NotImplemented

    def play_static_actor(self, instance: "Instance", node: Node):
        return NotImplemented

    def after_set_var(self, instance: "Instance", node: Node) -> None:
        """Optional world reaction before queued variable-change notifications."""

    def placement_probe(self, state: "State") -> bool:
        return False

    def list_selection_random(self, state: "State"):
        """Return an explicit uint32 draw for the active list State."""
        return NotImplemented

    def tagged_entity_world_available(self, instance: "Instance", node: Node):
        """Declare whether the active tag State can resolve its world."""
        return NotImplemented

    def tagged_entity_candidates(self, instance: "Instance", node: Node):
        """Ordered entity records from a declared tag-query service."""
        return NotImplemented

    def stop_tracking_targets(self, state: "State") -> None:
        """Optional cleanup of a host-owned target query."""

    def linked_script_changed(self, state: "State", *, entered: bool = False) -> None:
        """Optional host lifecycle for a script bound to the source state."""

    def apply_script_pose(self, instance: "Instance", node: Node) -> bool:
        """Return whether the host handled this action's target pose."""
        return False

    def apply_modify_health(self, state: "State") -> None:
        """Optional host combat consumer."""

    def assassin_capture_sender(self, instance: "Instance") -> int | None:
        return None

    def play_script_host(self, instance: "Instance", node: Node) -> bool:
        """Optional explicitly scoped body adapter; false leaves legacy bodies unchanged."""
        return False

    def create_entity(self, instance, node):
        return NotImplemented

    def body_placement(self, state):
        return NotImplemented

    def spatial_query_context(self, entity):
        return NotImplemented

    def entity_link_context(self, entity):
        return NotImplemented

    def confirm_portal(self, state):
        return NotImplemented

    def relocate_through_portal(self, instance, node):
        return NotImplemented

    def destroy_entity(self, instance, node):
        return NotImplemented

    def play_script_selected(self, instance, node):
        return NotImplemented

    def play_script(self, instance: "Instance", node: Node) -> None:
        """Select a native PlayScript resource, then ask the world to route it."""
        if not instance.component.strict:
            self.compatibility_play_script(instance, node)
            return
        from ow174.game.script.play import resolve_resource

        resource = resolve_resource(instance, node)
        if resource is not None:
            self.play_resolved_script(instance, node, resource)

    def create_script_entity(self, instance: "Instance", node: Node, definition: int, position: expr.Vec3):
        instance.component.unsupported(instance, "entity creation service", node.cls, node)

    def create_script_hero(
        self, instance: "Instance", node: Node, hero: int, position: expr.Vec3, team: int, yaw: float,
        flags: tuple, *, controller_definition: int | None = None, skin_theme: int | None = None,
    ):
        instance.component.unsupported(instance, "hero creation service", node.cls, node)

    def play_compatible_script(self, instance: "Instance", node: Node) -> None:
        """Host explicitly supported actions on a permissive body."""

    def create_compatible_pet(self, state: "State"):
        return NotImplemented

    def retire_compatible_pet(self, state: "State", entity: int) -> None:
        pass

    def compatibility_play_script(self, instance: "Instance", node: Node) -> None:
        """Optional explicitly scoped owner routing in the permissive body VM."""

    def create_pet(self, state: "State") -> int:
        if state.instance.component.strict:
            state.instance.component.unsupported(state.instance, "pet factory", state.node.cls, state.node)
        return 0

    def retire_pet(self, entity: int) -> None:
        """Retire only a factory-owned pet when its source state ends."""

    def play_resolved_script(self, instance: "Instance", node: Node, resource) -> None:
        """Construct the native request before asking the backend to submit it.

        Empty target iterators follow output without asking for unavailable
        context/transport services. A real request requires both services.
        """
        from ow174.game.script.play import resolve_request

        request = resolve_request(
            instance, node, resource, context_provider=lambda: self.play_script_context(instance, node),
        )
        if request is not None:
            self.submit_script_request(instance, node, request)

    def play_script_context(self, instance: "Instance", node: Node):
        """Supply native caller, owner and inherited instigator without guessing."""
        instance.component.unsupported(instance, "native PlayScript request context", node.cls, node)

    def submit_script_request(self, instance: "Instance", node: Node, request) -> None:
        """Resolve actual target components/resources, clock and transport policy.

        Request construction alone cannot create instances or accept wire flags.
        A concrete mission owner must implement this service.
        """
        instance.component.unsupported(instance, "native PlayScript request", node.cls, node)

    def managed_script_resource(self, instance: "Instance", node: Node, resource):
        """Resolve a state-owned, local graph through the world's loader."""
        instance.component.unsupported(instance, "managed script loader", node.cls, node)

    def code_changed(self, component: "Component", token: int) -> None:
        """Deliver an explicitly registered native-service notification."""
        instance = next(iter(component.instances.values()), None)
        if instance is not None:
            component.unsupported(instance, "native code observer", str(token))
        raise NotImplementedError(f"no native code observer backend for token {token}")


class Component:
    """The statescript component of one entity: its clock, its instances (in creation order) and its
    entity-scope variables."""

    def __init__(
        self, entity: int, frame_ms: int = 16, world: World | None = None, seed: int = 1,
        *, strict: bool = False,
    ) -> None:
        self.entity = entity
        # Mission reconstruction cannot advance through placeholder native nodes.
        # Existing player/body prediction keeps its current compatibility policy.
        self.strict = strict
        self.code_states: list[State] = []
        self.code_pending: list[list[int]] = [[], []]
        # A token identifies one backend callback/context pair; None matches any identifier.
        self.code_observers: list[tuple[int, int | None]] = []
        # 1B8D5E0: per authored type, in native linked-state registration order.
        self.state_registries: dict[str, list[State]] = {}
        self.parameter_scopes: list = []  # Native VM+4F8 providers, shared by its instances.
        self.frame_ms = frame_ms
        self.world = world or World()
        self.rng = expr.random.Random(seed)
        self.frame = 0
        self.now = 0
        self.instances: dict[int, Instance] = {}
        self.vars: dict[int, Var] = {}
        self.startup_entity_values: list[tuple[int, float]] = []
        self.held: list[int] = []  # logical buttons held down, in press order
        self.button = NO_BUTTON  # the button of the edge being handled
        self.disabled: dict[int, list[State]] = {}  # button -> active DisableLogicalButton states
        self.seq = EventSequence()
        self.next_id = 1
        self.destroyed: list[Instance] = []
        # Volley state, shot number, time, and the frozen activation counter and start.
        self.shots: list[tuple[State, int, int, tuple[int, int]]] = []
        self.health_reports: list[tuple[tuple[int, ...], float, int | None, int, tuple | None]] = []
        self.motion_reports: list[tuple[tuple[int, ...], tuple, int]] = []
        self.owner_dead = False  # the body's health is 0 (combat.py): presses and shots stop
        self.input_suppressed = False  # delivered input-only control, separate from death/physics
        self.maximum_health = None  # explicit authoritative body-health input, when supplied
        self.fault: ScriptBudgetError | UnsupportedScriptError | None = None
        self._operation_depth = 0
        self._follow_depth = 0
        self._work_left = MAX_SCRIPT_WORK

    def ensure_valid(self) -> None:
        if self.fault is not None:
            raise self.fault

    def budget_exceeded(self, message: str) -> None:
        """Fail the whole body, without running more graph callbacks or retaining shots."""
        self._fail(ScriptBudgetError(
            f"entity {self.entity:08X}, frame {self.frame}, time {self.now} ms: {message}"
        ))

    def unsupported(self, instance: "Instance", feature: str, cls: str, node: Node | None = None) -> None:
        self._fail(UnsupportedScriptError(instance, feature, cls, node))

    def _fail(self, error: ScriptBudgetError | UnsupportedScriptError) -> None:
        if self.fault is None:
            self.fault = error
            self.world.script_failed(self)
            self.shots.clear()
            self.health_reports.clear()
            self.motion_reports.clear()
            self.held.clear()
            self.code_states.clear()
            self.startup_entity_values.clear()
            self.state_registries.clear()
            for pending in self.code_pending:
                pending.clear()
            for instance in self.instances.values():
                instance.stopped = True
                instance.initialization_pending = False
                instance.queue.clear()
                instance.deferred.clear()
                instance.ticking.clear()
                for state in instance.states.values():
                    state.on_failed()
                    state.active = False
        raise self.fault

    @contextmanager
    def parameter_scope(self, provider):
        self.ensure_valid()
        self.parameter_scopes.append(provider)
        try:
            yield
        finally:
            if self.parameter_scopes and self.parameter_scopes[-1] is provider:
                self.parameter_scopes.pop()
            else:
                self.budget_exceeded('parameter scope stack changed during callback')

    @contextmanager
    def operation(self):
        """Nested calls and cross-instance events share one bounded operation."""
        self.ensure_valid()
        if self._operation_depth == 0:
            self._work_left = MAX_SCRIPT_WORK
        self._operation_depth += 1
        try:
            yield
        except RecursionError:
            self.budget_exceeded("script recursion limit exceeded")
        finally:
            self._operation_depth -= 1

    def consume_work(self, where: str) -> None:
        self.ensure_valid()
        if self._work_left <= 0:
            self.budget_exceeded(f"script work budget {MAX_SCRIPT_WORK} exhausted during {where}")
        self._work_left -= 1

    @contextmanager
    def follow_scope(self):
        with self.operation():
            self._follow_depth += 1
            try:
                if self._follow_depth > MAX_FOLLOW_DEPTH:
                    self.budget_exceeded(f"plug depth budget {MAX_FOLLOW_DEPTH} exhausted")
                self.consume_work("plug traversal")
                yield
            finally:
                self._follow_depth -= 1

    # --- variables ---------------------------------------------------------------------------------

    def disconnect_foreign_watchers(self) -> None:
        """Release cross-body subscriptions without changing retained VM values."""
        variables = list(self.vars.values())
        for instance in (*self.instances.values(), *self.destroyed):
            variables.extend(instance.vars.values())
            for state in instance.states.values():
                for var in list(state.watched):
                    if var.entity_owner not in (None, self):
                        var.watchers.pop(state, None)
                        state.watched.pop(var, None)
        for var in variables:
            for state in list(var.watchers):
                if state.instance.component is self:
                    continue
                var.watchers.pop(state, None)
                state.watched.pop(var, None)
                if state.active and not state.ending and not state.flags_changed:
                    state.flags_changed = True
                    state.instance.post(Event(state.instance.component.now, 0, CHANGED, state=state))

    def changed(self, var: Var, before, *, stack_source: "State | None" = None, structural=False):
        """Notify watchers and return the checked value without resolving a live getter twice."""
        after = var.value()
        changed = not expr.same(before, after)
        above_source = None
        if stack_source is not None:
            # A Stack value change can only affect the inputs of entries above it.
            above_source = set()
            above = False
            for link in var.links:
                if above:
                    above_source.add(link.state)
                elif link.state is stack_source:
                    above = True
        for state in list(var.watchers):
            if state.reads_below(var):
                if above_source is not None and state not in above_source:
                    continue
            elif not changed and not (structural and var.watchers[state]):
                continue
            if state.active and not state.ending and not state.flags_changed:
                state.flags_changed = True
                state.instance.post(Event(self.now, 0, CHANGED, state=state))
        return after

    def set(self, var: int, value) -> None:
        """Set an entity variable from outside the graphs (the server's own code)."""
        self.ensure_valid()
        target = self.entity_var(var)
        before = target.value()
        target.base = value
        self.changed(target, before)

    # --- values exposed to native code --------------------------------------------------------------

    def code_value(self, identifier: int, initial=None) -> tuple[bool, object]:
        """First registered matching state/row wins (104319 RVA D0D8B0, D4B230).

        A null config or a known evaluator that fails without writing leaves the
        destination unchanged; each nonmatching state clears it. The default
        represents a fresh null destination. Read a found expression now, without
        the ticking state's dependency watcher.
        """
        value = initial
        with self.operation():
            for state in self.code_states:
                for row in state.node.field("m_values") or []:
                    self.consume_work("native code value lookup")
                    if expr.guid_of(row.get("m_identifier")) == identifier:
                        config = row.get("m_value")
                        if config is not None:
                            value = state.instance.evaluate(config, initial=value)
                        return True, value
                value = None
        return False, value

    def unregister_code_state(self, state: "State") -> None:
        """Native D0E920 removes by swapping in the last registered state."""
        if state in self.code_states:
            index = self.code_states.index(state)
            last = self.code_states.pop()
            if index < len(self.code_states):
                self.code_states[index] = last

    def notify_code(self, channel: int) -> None:
        """D0D370: collect observer pairs once, clear pending IDs, then dispatch.

        Tokens preserve callback/context identity without storing native pointers.
        Changes queued by a callback survive for a subsequent notification pass.
        """
        with self.operation():
            self._notify_code(channel)

    def _notify_code(self, channel: int) -> None:
        notifications = []
        for identifier in self.code_pending[channel]:
            for token, selected in self.code_observers:
                self.consume_work("native code observer selection")
                if selected in (None, identifier) and token not in notifications:
                    notifications.append(token)
        self.code_pending[channel].clear()
        for token in notifications:
            self.consume_work("native code notification")
            self.world.code_changed(self, token)

    def entity_var(self, var: int, create: bool = True) -> Var | None:
        found = self.vars.get(var)
        if found is None and create:
            found = self.vars[var] = Var((0, var), self)
        return found

    # --- instances ---------------------------------------------------------------------------------

    def allocate_id(self) -> int:
        while self.next_id in self.instances:
            self.next_id += 1
        found = self.next_id
        self.next_id += 1
        return found

    def create(
        self,
        graph: Graph,
        instance_id: int | None = None,
        parent: "Instance | None" = None,
        parent_state: int | None = None,
        values: dict | None = None,
        init: bool = True,
    ) -> "Instance":
        """A new instance; with `init` its Entries are queued at now (they run in its next update)."""
        if instance_id is None:
            instance_id = self.allocate_id()
        if instance_id in self.instances:
            raise ValueError(f"instance {instance_id} exists")
        instance = Instance(self, instance_id, graph, parent, parent_state)
        self.instances[instance_id] = instance
        self.next_id = max(self.next_id, instance_id + 1)
        for (scope, var), value in (values or {}).items():
            instance.find_var(scope, var).base = value
        if init:
            instance.init()
        return instance

    def destroy(self, instance: "Instance") -> None:
        if instance.id not in self.instances:
            return
        instance.teardown = True
        for child in [item for item in self.instances.values() if item.parent is instance]:
            self.destroy(child)
        for state in list(instance.states.values()):
            state.end(False)
        for state in list(instance.states.values()):
            state.on_destroyed()
        instance.stopped = True
        instance.initialization_pending = False
        instance.queue.clear()
        instance.deferred.clear()
        instance.join_inputs.clear()
        del self.instances[instance.id]
        self.destroyed.append(instance)

    def play_loaded_graph(
        self, definition: Graph, *, source: int, params=(), overrides=(), policy: int = 0,
        instigator: int | None = None,
    ) -> "Instance | None":
        """Invoke an already-resolved graph on this component (64D2C0, 64CD10).

        Policies: 0 skip an existing graph; 1 create; 2 reuse its first instance
        or create; 3 retire all matching instances before creating. This core does
        not resolve world targets, resources or wire flags. Evaluated overrides
        follow public schema defaults, before the separately queued start inputs.
        The native start context must supply source explicitly. This core uses
        context kind zero; entries declaring another kind do not match it.
        An explicitly supplied instigator seeds builtin001B on new allocation
        before schema evaluation. Only zero falls back to this component's owner;
        skipped/reused instances keep their allocation source. None leaves it unknown.
        """
        self.ensure_valid()
        if type(policy) is not int or policy not in range(4):
            raise ValueError("unknown PlayScript instance policy")
        if type(source) is not int or not 0 <= source <= 0xFFFFFFFF:
            raise ValueError("PlayScript source must be a 32-bit entity identifier")
        if instigator is not None and (type(instigator) is not int or not 0 <= instigator <= 0xFFFFFFFF):
            raise ValueError("PlayScript instigator must be a 32-bit entity identifier")
        params = tuple(params)
        overrides = tuple(overrides)
        for name, pairs in (("parameter", params), ("override", overrides)):
            if any(type(key) is not int or not 0 <= key <= 0xFFFFFFFFFFFFFFFF for key, _ in pairs):
                raise ValueError(f"PlayScript {name} keys must be 64-bit identifiers")
        with self.operation():
            matches = []
            for instance in self.instances.values():
                self.consume_work("played graph lookup")
                if instance.graph.index == definition.index:
                    matches.append(instance)
            if matches and policy == 0:
                return None
            if policy == 3:
                for instance in matches:
                    self.consume_work("replace played graph")
                    self.destroy(instance)
            reused = bool(matches and policy == 2)
            instance = matches[0] if reused else self.create(definition)
            if not reused and instigator is not None:
                instance.set_source_entity(self.entity if instigator == 0 else instigator)
            instance.play_schema(overrides, reorder_entries=not reused)
            instance.start_entries(params, source)
            return instance

    # --- time --------------------------------------------------------------------------------------

    def next_event_time(self) -> int | None:
        times = [item.queue[0].time for item in self.instances.values()
                 if item.queue and not item.stopped and (item.initialized or item.initialization_pending)]
        return min(times) if times else None

    def edges(self, buttons: list[tuple[int, bool]]) -> None:
        """Button edges at now, on every instance (0x7FF789C66AA0 queues each on all the instances)."""
        for button, pressed in buttons:
            if pressed and self.input_suppressed:
                continue
            for instance in list(self.instances.values()):
                instance.post(Event(self.now, 0, BUTTON, button=button, pressed=pressed))

    def discard_button_presses(self) -> None:
        """Discard queued down edges before a host interrupts an NPC."""
        for instance in self.instances.values():
            instance.queue[:] = [event for event in instance.queue if event.kind != BUTTON or not event.pressed]

    def run_frame(self, frame: int, buttons: list[tuple[int, bool]] = (), *, end_phase=None) -> None:
        """Advance from the frame start or an already established cursor in that frame.

        Apply edges at that cursor, update at each due event time and at the frame end.
        An explicit ordinary end phase runs once after those
        events and before deferred channel 1 (104319 1B8C910/D0D350)."""
        if end_phase is not None and not callable(end_phase):
            raise ValueError("ordinary end phase must be callable")
        with self.operation():
            start = frame * self.frame_ms
            end = start + self.frame_ms - 1
            # Mid-frame creation has already queued schema writes at its allocation time.
            # Keep entry initialization at or after those writes when the owner first advances it.
            if self.frame != frame or not start <= self.now <= end:
                self.now = start
            self.frame = frame
            self.edges(list(buttons))
            self._initialize_pending()
            for _ in range(MAX_COMPONENT_UPDATES):
                due = self.next_event_time()
                self.now = end if due is None or due > end else max(due, self.now)
                if end_phase is None:
                    self.update()
                else:
                    self._update_instances()
                if self.now >= end:
                    due = self.next_event_time()
                    if due is None or due > end:
                        break
            else:
                self.budget_exceeded(f"component frame update budget {MAX_COMPONENT_UPDATES} exhausted")
            self.now = end
            if end_phase is not None:
                end_phase()
                self.notify_code(1)

    def run_now(self) -> None:
        """Update every instance at the current time until nothing more is due now (spawn)."""
        with self.operation():
            self._initialize_pending()
            for _ in range(MAX_COMPONENT_UPDATES):
                self.update()
                due = self.next_event_time()
                if due is None or due > self.now:
                    break
            else:
                self.budget_exceeded(
                    f"component current-time update budget {MAX_COMPONENT_UPDATES} exhausted"
                )

    def update(self) -> None:
        with self.operation():
            self._initialize_pending()
            self._update_instances()
            # The native component override D0D350 calls the ordinary update,
            # then drains deferred channel 1 through D0D370.
            self.notify_code(1)

    def _initialize_pending(self) -> None:
        # 1B8E970 prepares all instances before the ordinary due-event loop.
        for instance in list(self.instances.values()):
            if instance.initialization_pending and not instance.stopped:
                self.consume_work("component pending initialization")
                instance.init()
        if self.startup_entity_values:
            values, self.startup_entity_values = self.startup_entity_values, []
            for key, value in values:
                self.consume_work("component pending entity assignment")
                self.set(key, value)

    def _update_instances(self) -> None:
        self.consume_work("component update")
        for instance in list(self.instances.values()):
            if instance.id in self.instances:
                instance.update()


class Instance:
    """One graph instance."""

    def __init__(self, component: Component, instance_id: int, graph: Graph, parent=None, parent_state=None):
        self.component = component
        self.id = instance_id
        self.graph = graph
        self.parent: Instance | None = parent
        self.parent_state = parent_state  # the parent graph's m_states index of the SubScript state
        self.vars: dict[int, Var] = {}
        self.states: dict[int, State] = {}
        self.queue: list[Event] = []
        self.ticking: list[State] = []
        self.deferred: list[tuple[float, int, State]] = []
        self.params: list[dict | None] = []
        self.stopped = False
        self.teardown = False
        self.initialized = False
        self.initialization_pending = False
        self.start = 0
        self.disabled_start_entries: set[int] = set()
        self.disabled_message_entries: set[int] = set()  # Native entry-array indices, not node positions.
        self.cached_source_entity = 0  # Native Instance+234, initially the invalid entity.
        self.ruleset_target_entity = 0  # Native Instance+230, selected by an entity member expression.
        self.auto_retire = False  # Native Instance318 bit11, set for PlayScript byte zero.
        self.initialized_frame = 0  # Native Instance+58.
        self.retirement_pending = False  # Native Instance318 bit22.
        self.join_inputs: list[tuple[int, int, str]] = []  # target, producer, output plug path
        self.plug_source: tuple[int, str] | None = None  # current native input traversal

    def __repr__(self) -> str:
        return f"<Instance {self.id} {self.graph.index:04X}>"

    # --- variables ---------------------------------------------------------------------------------

    def find_var(self, scope: int, var: int, create: bool = True) -> Var | None:
        if scope == ENTITY:
            return self.component.entity_var(var, create)
        found = self.vars.get(var)
        if found is None and create:
            found = self.vars[var] = Var((self.id, var))
        return found

    def context(self, watcher: "State | None" = None) -> InstanceContext:
        return InstanceContext(self, watcher)

    def evaluate(self, cfg, watcher: "State | None" = None, *, initial=None):
        return expr.evaluate(cfg, self.context(watcher), initial=initial)

    def source_entity(self) -> int:
        """1B92E50/1B92ED0: retain a valid cache, otherwise cast builtin001B now."""
        self.component.ensure_valid()
        if not expr.valid_entity(self.cached_source_entity):
            self.cached_source_entity = expr.to_entity(self.context().var(INSTANCE, 0x1B)) & 0xFFFFFFFF
            self.component.ensure_valid()
        return self.cached_source_entity

    def set_source_entity(self, entity: int) -> None:
        """1B94B10: write the typed builtin synchronously, then commit its cache."""
        if type(entity) is not int or not 0 <= entity <= 0xFFFFFFFF:
            raise ValueError('script source must be a 32-bit entity identifier')
        with self.component.operation():
            self.write((INSTANCE, 0x1B), Entity(entity))
            self.component.ensure_valid()
            self.cached_source_entity = entity

    def write(self, target: tuple[int, int] | None, value) -> None:
        """A plain assignment with the value's own type (0x7FF78AAB5650)."""
        if target is None:
            return
        var = self.find_var(*target)
        before = var.value()
        var.base = value
        for link in list(var.links):
            if link.state.active:
                link.state.var_written(var)
        self.changed(var, before)

    def changed(self, var: Var, before, *, stack_source: "State | None" = None, structural=False) -> None:
        self.component.changed(var, before, stack_source=stack_source, structural=structural)

    # --- states ------------------------------------------------------------------------------------

    def state(self, index: int) -> "State | None":
        """The runtime state of an m_states index, made on first use (0x7FF78AAA1460)."""
        found = self.states.get(index)
        if found is None:
            node = self.graph.state(index)
            if node is None:
                if self.component.strict:
                    self.component.unsupported(self, "state reference", str(index))
                return None
            model = state_type(node.cls, self.component.strict, self.graph.index, node.pos)
            registered_base = (STATE_CLASSES.get(node.cls) is State or STRICT_STATE_CLASSES.get(node.cls) is State)
            if self.component.strict and node.cls != "STUStatescriptState" and model is State and not registered_base:
                if self.component.world.native_state_factory_miss(node.cls):
                    return None
                self.component.unsupported(self, "state", node.cls, node)
            found = self.states[index] = model(self, node, index)
            found.on_created()
        return found

    def node_state(self, node: Node) -> "State | None":
        return self.state(node.state) if node.state is not None else None

    # --- events ------------------------------------------------------------------------------------

    def post(self, event: Event) -> None:
        """Queue an event: never in the past, after every event of the same time (0x7FF78AAA2990)."""
        event.time = max(event.time, self.component.now)
        event.seq = next(self.component.seq)
        bisect.insort(self.queue, event, key=Event.key)

    def cancel(self, state: "State") -> None:
        """Drop every queued event of a state (0x7FF78AAA12F0)."""
        self.queue = [event for event in self.queue if event.state is not state]
        # These guards describe queued notifications. Keeping a guard after its
        # event was removed prevents a later activation from observing changes
        # (Baptiste's secondary-ammo/cooldown gate then never opens again).
        state.flags_changed = False
        state.flags_check = False

    def drop_begins(self, states: list["State"]) -> None:
        """Drop the begin requests queued at now for these states (0x7FF78AAA10F0)."""
        now = self.component.now
        drop = {id(state) for state in states}
        self.queue = [
            event
            for event in self.queue
            if not (event.kind == STATE_OP and event.op == BEGIN and event.time == now)
            or id(event.state) not in drop
        ]

    def timers(self) -> list[Event]:
        """The pending events the owner frame's event list can carry: timers and finish requests."""
        return [
            event
            for event in self.queue
            if event.state is not None
            and not event.transient
            and (event.kind == TIMER or (event.kind == STATE_OP and event.op == FINISH))
            and event.state.networked
        ]

    # --- running -----------------------------------------------------------------------------------

    def init(self, *, run_entries: bool = True) -> None:
        """Init (0x7FF78AAA3F00): queue ordinary Entries for the root namespace."""
        self.initialized = True
        self.initialization_pending = False
        self.start = self.component.now
        self.initialized_frame = self.component.frame & 0xFFFFFFFF
        if not run_entries:
            return
        for node in self.graph.entries:
            if node is not None and not node.client_only and node.cls in ENTRY_CLASSES:
                self.disabled_start_entries.add(node.pos)
                self.post(Event(self.start, 0, ENTRY, node=node))

    def play_schema(self, overrides: tuple, *, reorder_entries: bool) -> None:
        """BE90C0: queue callee defaults, then the already-evaluated request values.

        Default evaluation uses this instance without a watcher. Writes remain
        queued, so later defaults read the pre-assignment variable values. A new
        instance moves ordinary entries behind other events at the same time
        (1B93D40); a reused instance keeps its existing event order.
        """
        from ow174.game.script.play import evaluate_value

        with self.component.operation():
            schema = self.graph.fields.get("m_publicSchema")
            if schema is not None:
                if (not isinstance(schema, dict)
                        or schema.get("$") not in ("STUStatescriptSchema", "STU_77990ED7")
                        or schema.get("m_89EAAC40") is not None):
                    self.component.unsupported(self, "played graph schema", str(schema.get("$")
                                               if isinstance(schema, dict) else type(schema)))
                for row in schema.get("m_entries") or ():
                    self.component.consume_work("played graph schema evaluation")
                    if not isinstance(row, dict):
                        self.component.unsupported(self, "played graph schema row", str(type(row)))
                    config = row.get("m_value")
                    if config is not None:
                        failed = object()
                        value = evaluate_value(self, config, initial=failed)
                        # BE9AC0 skips a failed +28 evaluation. This differs
                        # from D058D0's explicit int-zero override fallback.
                        if value is failed:
                            continue
                        identifier = expr.guid_of(row.get("m_0D09D2D9")) & 0xFFFF
                        self.post(Event(self.component.now, 0, ASSIGN,
                                        assignment=(INSTANCE, identifier, value)))
            for key, value in overrides:
                self.component.consume_work("played graph override assignment")
                self.post(Event(self.component.now, 0, ASSIGN, assignment=(INSTANCE, key & 0xFFFF, value)))
            if reorder_entries and (schema is not None or overrides):
                for event in self.queue:
                    self.component.consume_work("played graph initialization order")
                    if event.kind == ENTRY:
                        event.seq = next(self.component.seq)
                self.queue.sort(key=Event.key)

    def start_entries(self, params: tuple, source: int) -> int:
        """Queue native BB4AC733 start-entry bindings before each entry (1B95950).

        Named parameters find the first matching writable row; key zero uses the
        parameter's position. Missing parameters leave previous variables intact.
        A nonzero FDBDCB70 disables that entry for later invocations.
        """
        count = 0
        with self.component.operation():
            if self.stopped:
                return count
            for node in self.graph.entries:
                if (node is None or node.client_only or node.cls != "STU_BB4AC733"
                        or node.pos in self.disabled_start_entries):
                    continue
                self.component.consume_work("script start entry")
                if node.field("m_5AEA0A51"):
                    continue
                rows = node.field("m_params") or []
                for position, (key, value) in enumerate(params):
                    for row_position, row in enumerate(rows):
                        self.component.consume_work("script start parameter binding")
                        if row is None:
                            continue
                        match = (expr.guid_of(row.get("m_B5051BCE")) == key
                                 if key else row_position == position)
                        if not match or row.get("m_out_Var") is None:
                            continue
                        target = expr.dynamic(row["m_out_Var"])
                        if target is None:
                            self.component.unsupported(self, "script start parameter target", node.cls, node)
                        if target[1]:
                            self.post(Event(self.component.now, 0, ASSIGN, assignment=(*target, value)))
                        break
                source_config = node.field("m_B60ABDA4")
                if source_config is not None and expr.valid_entity(source):
                    target = expr.dynamic(source_config)
                    if target is None:
                        self.component.unsupported(self, "script start source target", node.cls, node)
                    self.post(Event(self.component.now, 0, ASSIGN, assignment=(*target, Entity(source))))
                if node.field("m_FDBDCB70"):
                    self.disabled_start_entries.add(node.pos)
                self.post(Event(self.component.now, 0, ENTRY, node=node))
                count += 1
        return count

    def update(self) -> None:
        """One update at the component's now (0x7FF78AAA27C0)."""
        with self.component.operation():
            self.retirement_pending = False
            if self.stopped or not self.initialized:
                return
            for _ in range(MAX_INSTANCE_UPDATES):
                self.drain()
                if self.stopped:
                    return
                for state in list(self.ticking):
                    if state.active and state.ticked_at != self.component.now:
                        self.component.consume_work("state tick")
                        state.ticked_at = self.component.now
                        state.tick()
                if not self.run_deferred():
                    break
            else:
                self.component.budget_exceeded(
                    f"instance {self.id} update budget {MAX_INSTANCE_UPDATES} exhausted"
                )
            self.check_auto_retirement()

    def check_auto_retirement(self) -> None:
        """1B95440/1B935D0: no events, no enabled keepalive entries, then age/states."""
        if not self.auto_retire or self.queue or self.stopped or not self.initialized:
            return
        for node in self.graph.entries:
            self.component.consume_work('automatic script lifetime entry')
            if (node is not None and node.pos not in self.disabled_start_entries
                    and node.cls not in ('STU_BB4AC733', 'STU_8A7E9DA8', *MESSAGE_ENTRIES)):
                return
        if (self.initialized_frame+1) & 0xFFFFFFFF >= self.component.frame & 0xFFFFFFFF:
            self.retirement_pending = True
            return
        if any(state.active for state in self.states.values()):
            return
        self.component.destroy(self)

    def drain(self) -> None:
        with self.component.operation():
            now = self.component.now
            count = 0
            while self.queue and self.queue[0].time <= now and not self.stopped:
                if count >= EVENTS_PER_TIME:
                    self.component.budget_exceeded(
                        f"instance {self.id} event budget {EVENTS_PER_TIME} exhausted at {now} ms"
                    )
                count += 1
                self.component.consume_work("event dispatch")
                event = self.queue.pop(0)
                self.dispatch(event)

    def stop(self) -> None:
        """1B95240: stop the instance, then notify its still-matching parent state."""
        if self.stopped or self.teardown:
            return
        self.teardown = True
        self.initialization_pending = False
        for state in list(self.states.values()):
            state.end(False)
        for state in list(self.states.values()):
            state.on_destroyed()
        self.states.clear()
        self.queue.clear()
        self.join_inputs.clear()
        self.deferred.clear()
        self.stopped = True
        self.teardown = False
        if self.parent is not None:
            state = self.parent.states.get(self.parent_state)
            if state is not None and state.active and not state.ending:
                state.child_stopped(self)

    def dispatch(self, event: Event) -> None:
        state = event.state
        if event.kind == STATE_OP:
            if event.op == BEGIN:
                state.begin()
            else:
                state.end(event.op == FINISH)
        elif event.kind == TIMER:
            if state.active:
                state.timer(event.param)
                state.try_complete()
            else:
                state.timer_inactive(event.param)
        elif event.kind == CHECK:
            state.flags_check = False
            if state.active:
                previous = self.component.button
                self.component.button = event.button
                try:
                    state.try_complete()
                finally:
                    self.component.button = previous
        elif event.kind == CHANGED:
            state.flags_changed = False
            if state.active and not state.ending:
                state.dependency_changed()
                # Delivered variable-driven transitions apply to every authored
                # owner, including146A's exhausted Storm Arrow branch.
                if state.active and not state.ending:
                    state.try_complete()
        elif event.kind == ENTRY:
            self.run_node(event.node, None)
        elif event.kind == ASSIGN:
            scope, identifier, value = event.assignment
            self.write((scope, identifier), value)
        elif event.kind == STOP:
            self.stop()
        elif event.kind == STOP_INSTANCE:
            self.component.destroy(self)
        elif event.kind == BUTTON:
            self.button_edge(event.button, event.pressed)
        elif event.kind == MESSAGE:
            self.deliver(event)

    def button_edge(self, button: int, pressed: bool) -> None:
        """A logical-button edge (event 8): the held list, then every LogicalButton/Ability state of the
        graph's m_logicalButtonStateIndices, active or not."""
        if pressed and self.component.input_suppressed:
            return
        component = self.component
        component.button = button
        if pressed and button not in component.held:
            component.held.append(button)
        elif not pressed and button in component.held:
            component.held.remove(button)
        for index in self.graph.fields.get("m_680A2CB2") or []:
            state = self.state(index)
            if state is not None:
                state.button(button, pressed)
        # A logical-button condition is not itself a State. Its compiled owner
        # list identifies transition States that need a completion check when
        # this input changes (e.g. Reaper00F2/st1 waiting for Shadow Step).
        for node in self.graph.nodes:
            if node.cls != "STU_7DEB522D" or not self.accepts(node):
                continue
            cfg = node.field("m_logicalButton")
            if expr.to_int(self.evaluate(cfg)) != button:
                continue
            for index in node.field("m_122DF0CA") or ():
                state = self.states.get(index)
                if state is not None and state.active and not state.flags_check:
                    state.flags_check = True
                    self.post(Event(component.now, 0, CHECK, state=state, button=button, pressed=pressed))
        component.button = NO_BUTTON

    def run_deferred(self) -> bool:
        """The deferred list (Defer), from the last entry down. Returns whether something ran."""
        if not self.deferred:
            return False
        work, self.deferred = self.deferred, []
        for _, _, state in reversed(work):
            if state.active:
                state.timer(1)
        return True

    # --- plugs -------------------------------------------------------------------------------------

    def accepts(self, node: Node | None) -> bool:
        """The server's node filter: everything that is not client-only."""
        return node is not None and not node.client_only

    def follow(self, node: Node, path: str, caller: "State | None" = None) -> bool:
        """Follow a plug of a node (0x7FF78AA83FA0). Returns whether a link was taken."""
        with self.component.follow_scope():
            return self._follow(node, path, caller)

    def _follow(self, node: Node, path: str, caller: "State | None") -> bool:
        plug = node.field(path)
        if not is_plug(plug):
            return False
        taken = False
        for position, input_path in plug["links"]:
            self.component.consume_work("plug target")
            if self.stopped:
                break
            target = self.graph.node(position) if position is not None else None
            if target is None:
                if self.component.strict:
                    self.component.unsupported(self, "plug target", str(position), node)
                taken = True
                continue
            if not self.accepts(target):
                continue
            if target.is_state:
                state = self.node_state(target)
                if state is not None:
                    taken = state.plug_enter(input_path) or taken
                continue
            previous = self.plug_source
            self.plug_source = (node.pos, path)
            try:
                taken = self.run_node(target, None if target.is_action else caller) or taken
            finally:
                self.plug_source = previous
        return taken

    def join(self, node: Node, caller: "State | None") -> bool:
        """104319 1B6EED0/1B73BC0: join distinct input edges before following output."""
        if self.plug_source is None:
            self.component.unsupported(self, "join input origin", node.cls, node)
        arrival = (node.pos, *self.plug_source)
        if arrival in self.join_inputs:
            return False
        previous = sum(row[0] == node.pos for row in self.join_inputs)
        self.join_inputs.append(arrival)
        indexes = node.field("m_122DF0CA") or ()
        if previous == 0:
            for index in indexes:
                self.component.consume_work("join completion check")
                state = self.states.get(index)
                if state is not None and state.active and state is not caller and not state.flags_check:
                    state.flags_check = True
                    self.post(Event(self.component.now, 0, CHECK, state=state))
            return False
        if previous != expr.to_int(node.field("m_B24D1794")) - 1:
            return False
        # 1B700F0 removes this target's registrations by swapping with the tail.
        index = 0
        while index < len(self.join_inputs):
            if self.join_inputs[index][0] == node.pos:
                self.join_inputs[index] = self.join_inputs[-1]
                self.join_inputs.pop()
            else:
                index += 1
        if not self.follow(node, "m_outPlug", caller):
            return False
        for index in indexes:
            self.component.consume_work("join state abort")
            state = self.states.get(index)
            if state is not None and state.active:
                state.request(ABORT)
        return True

    def follow_states(self, node: Node, path: str) -> list["State"]:
        """The states a subgraph plug lists (m_FF3DAF1E)."""
        plug = node.field(path)
        if not is_plug(plug):
            return []
        found = []
        for index in plug.get(SUBGRAPH_STATES) or []:
            target = self.graph.state(index) if index is not None else None
            if target is not None and not self.accepts(target):
                continue
            state = self.state(index) if index is not None else None
            if state is not None:
                found.append(state)
        return found

    def exit_subgraph(self, node: Node, path: str) -> None:
        """Abort the states of a subgraph and drop their begins queued at now (0x7FF78AA7FD00); not while
        the instance is torn down."""
        if self.teardown:
            return
        states = self.follow_states(node, path)
        for state in states:
            if state.active:
                state.end(False)
        self.drop_begins(states)

    def run_node(self, node: Node, caller: "State | None") -> bool:
        """Run an action, condition or entry now; returns whether it took a path."""
        with self.component.operation():
            self.component.consume_work("node execution")
            return self._run_node(node, caller)

    def _run_node(self, node: Node, caller: "State | None") -> bool:
        function = HANDLERS.get(node.cls)
        if function is not None:
            accepted = function(self, node, caller)
            if accepted is not NotImplemented:
                return accepted
        if node.cls not in ENTRY_CLASSES and node.cls not in MESSAGE_ENTRIES:
            if self.component.strict:
                self.component.unsupported(self, "node", node.cls, node)
            expr.warn_once(node.cls, "node class %s has no model: it only follows m_outPlug", node.cls)
        self.follow(node, "m_outPlug", None)
        return True

    # --- game messages -----------------------------------------------------------------------------

    def deliver(self, event: Event) -> None:
        message, params, sender = event.message
        node = event.node
        if node is None:
            return
        with self.component.parameter_scope(params):
            if node.is_state:
                state = self.node_state(node)
                if state is not None and state.active:
                    state.message(message, params, sender)
            else:
                outs = node.field("m_params") or []
                for item in outs:
                    if isinstance(item, dict):
                        target = expr.lvalue(item.get("m_out_Var"))
                        key = expr.guid_of(item.get("m_B5051BCE")) & 0xFFFF
                        if target is not None:
                            self.write(target, params.get(key))
                # Legacy body messages have one source identity. The native
                # entry binds caller and instigator before following its plug.
                for field in ("m_B60ABDA4", "m_FF703F20"):
                    target = expr.dynamic(node.field(field))
                    if target is not None:
                        self.write(target, Entity(sender))
                self.follow(node, "m_outPlug", None)


class State:
    """A state node's runtime object: the base class is the plain State (7C37840C)."""

    def __init__(self, instance: Instance, node: Node, index: int) -> None:
        self.instance = instance
        self.node = node
        self.index = index
        self.active = False
        self.ending = False
        self.counter = 0  # activation counter (u16)
        self.start = 0
        self.ended_at = 0
        self.ticked_at = None
        self.watched: dict[Var, bool | None] = {}
        self.flags_changed = False
        self.flags_check = False
        self.polled = False
        self.natives: set[str] = set()  # the classes of the world's config vars it read (see BodyScript)

    def __repr__(self) -> str:
        return f"<{type(self).__name__} i{self.instance.id} st{self.index} {'on' if self.active else 'off'}>"

    @property
    def networked(self) -> bool:
        return not self.node.client_only and not self.node.server_only

    @property
    def now(self) -> int:
        return self.instance.component.now

    # --- requests (events at now) ------------------------------------------------------------------

    def request(self, op: int, delay: int = 0) -> None:
        self.instance.post(Event(self.now + delay, 0, STATE_OP, state=self, op=op))

    def request_finish(self, delay: int = 0) -> None:
        self.request(FINISH, delay)

    def timer_at(self, delay: int, param: int = 1, transient: bool = False) -> None:
        self.instance.post(Event(self.now + delay, 0, TIMER, state=self, param=param, transient=transient))

    def plug_enter(self, input_path: str) -> bool:
        """A link enters the state (base 0x7FF78AA83D80): the abort plug asks for an abort, others a begin."""
        if input_path == "m_C721B06C" and self.active and self.instance.component.world.placement_probe(self):
            return True
        if ((self.instance.graph.index, self.node.pos, input_path) == (0x156B, 57, "m_6647ED22")
                and getattr(self.instance.component.world, "confirm_photon_barrier", lambda _: False)(self)):
            return True
        if input_path == ABORT_PLUG:
            self.request(ABORT)
        else:
            self.request(BEGIN)
        return True

    # --- begin and end -----------------------------------------------------------------------------

    def begin(self, backdate: int = 0) -> None:
        """DoBeginState (0x7FF78AA80790)."""
        if self.active:
            self.reentry()
            return
        self.counter = (self.counter + 1) & 0xFFFF
        self.on_beginning()
        indexes = self.node.fields.get("m_exclusiveStateIndices") or []
        exclusive = [self.instance.state(index) for index in indexes]
        for state in exclusive:
            if state is not None and state.active:
                state.end(False)
        self.instance.drop_begins([state for state in exclusive if state is not None])
        self.active = True
        self.start = self.now - backdate
        self.ticked_at = self.now
        self.on_begin()
        if self.node.cls == "STUStatescriptStatePet":
            self.instance.component.world.pet_lifecycle(self, True)
        self.on_enter()
        if self.node.cls == "STUStatescriptStatePet":
            self.instance.component.world.create_entity(self.instance, self.node)
        if not self.active:
            return
        self.instance.follow(self.node, "m_onBeginPlug", self)
        self.instance.follow(self.node, "m_subgraphPlug", self)
        if self.active:
            self.try_complete()

    def end(self, finished: bool) -> None:
        """DoFinishState (finished) / DoAbortState (0x7FF78AA80B10 / 0x7FF78AA80480)."""
        if not self.active or self.ending:
            return
        self.ending = True
        self.on_ending()
        self.instance.cancel(self)
        # The coalescing bits describe queued events, which cancel just removed.
        self.flags_check = self.flags_changed = False
        self.instance.exit_subgraph(self.node, "m_subgraphPlug")
        self.on_end(finished)
        if self.node.cls == "STUStatescriptStatePet":
            self.instance.component.world.pet_lifecycle(self, False)
        self.on_cleanup()
        if self.node.cls == "STUStatescriptStatePet":
            self.instance.component.world.destroy_entity(self.instance, self.node)
        self.unwatch()
        self.polled = False
        if self in self.instance.ticking:
            self.instance.ticking.remove(self)
        self.active = False
        self.ending = False
        self.ended_at = self.now
        if self.instance.teardown:
            return
        self.follow_end_plugs(finished)

    def follow_end_plugs(self, finished: bool) -> None:
        self.instance.follow(self.node, "m_onFinishedPlug" if finished else "m_onAbortPlug", self)
        self.instance.follow(self.node, "m_onEndPlug", self)

    def try_complete(self) -> None:
        """TryComplete (0x7FF78AA81570): when IsComplete, the state queues its own abort at now."""
        if self.active and not self.ending and self.is_complete():
            self.request(ABORT)

    def is_complete(self) -> bool:
        """Base IsComplete (0x7FF78AA83EC0): the first transition plug that takes a link."""
        for number, plug in enumerate(self.node.fields.get("m_transitionPlug") or []):
            if is_plug(plug) and self.instance.follow(self.node, f"m_transitionPlug[{number}]", self):
                return True
        return False

    # --- watching ----------------------------------------------------------------------------------

    def watch(self, var: Var, *, structural=False) -> None:
        if self.active and not self.ending:
            # Equal-valued link changes can still alter a priority predicate.
            kind = True if structural or var.watchers.get(self) else None
            var.watchers[self] = kind
            self.watched[var] = kind

    def unwatch(self) -> None:
        for var in self.watched:
            var.watchers.pop(self, None)
        self.watched.clear()

    def reads_below(self, var: Var) -> bool:
        """Whether this state's expression reads the value beneath its link to var."""
        return False

    def evaluate(self, cfg, watch: bool = True):
        return self.instance.evaluate(cfg, self if watch else None)

    def ticks(self) -> None:
        if self not in self.instance.ticking:
            self.instance.ticking.append(self)

    # --- class hooks -------------------------------------------------------------------------------

    def on_created(self) -> None:
        """1B91460: after insertion into the instance, before its first begin."""

    def on_destroyed(self) -> None:
        """Release object-lifetime services without following authored plugs."""

    def child_stopped(self, child: Instance) -> None:
        """Handle a child stop for a state that owns script instances."""

    def on_failed(self) -> None:
        """Abandon local external-effect bookkeeping without authored callbacks."""

    def message_context_retired(self) -> None:
        """Quietly release object-owned message bindings after context retirement."""

    def on_effect_abandoned(self, effect) -> None:
        """A world binding retired an effect without invoking native graph code."""

    def on_begin(self) -> None:
        """OnBegin (vt+0xB8): before the base plugs."""

    def on_beginning(self) -> None:
        """Component +100: after counter increment, before exclusives/active=true."""

    def on_ending(self) -> None:
        """Component +118: before child/end cleanup, while active and ending."""

    def on_enter(self) -> None:
        """OnEnter (vt+0x60)."""
        if (self.node.cls == "STU_54FEB21C"
                and self.instance.component.world.completes_empty_marker(self)):
            self.request_finish()

    def reentry(self) -> None:
        """A begin while active (vt+0x68)."""

    def on_end(self, finished: bool) -> None:
        """OnFinish/OnAbort and OnEnd (vt+0xA0/0xA8, vt+0xB0)."""

    def on_cleanup(self) -> None:
        """1B71740 / vt+C0: after the class end hooks, before authored end plugs."""

    def timer(self, param: int) -> None:
        """A timer of an active state (vt+0x90)."""

    def timer_inactive(self, param: int) -> None:
        """A timer of an inactive state (vt+0x98)."""

    def tick(self) -> None:
        """Once per update while registered as ticking (vt+0x70); a polled state re-checks."""
        if self.polled:
            self.dependency_changed()

    def dependency_changed(self) -> None:
        """A watched variable changed (vt+0xC8)."""
        if self.instance.graph.index == 0x073A and self.node.pos == 30:
            self.try_complete()

    def native_dependency(self, cfg) -> None:
        """1B72AD0: a native evaluator marked this dependency epoch as used."""

    def button(self, button: int, pressed: bool) -> None:
        """A logical-button edge (vt+0x128), active or not."""

    def message(self, message: int, params: dict, sender: int) -> None:
        """A game message for this state (GameMessageEntry)."""

    def var_written(self, var: Var) -> None:
        """Someone wrote a variable this state is linked to (vt+0xD0)."""

    def stack_changed(self, var: Var) -> None:
        """An entry was pushed on or popped from a stack variable this state is on (vt+0xD8)."""

    def output(self, slot: int):
        """The value this state gives a variable it is linked to (vt+0x188)."""
        return None

    def payload(self) -> dict:
        """The class's owner-frame payload, as values the encoder writes."""
        return {}

    # --- links -------------------------------------------------------------------------------------

    def link(self, target, slot: int = 0, priority: float = 0.0, above: bool = True, stack: bool = False):
        var = self.instance.find_var(*target)
        before = var.value()
        insert_link(var, Link(self, slot, priority, above, stack))
        self.instance.changed(var, before, structural=True)
        for state in var.stack_states():
            state.stack_changed(var)
        return var

    def unlink(self, var: Var, bake: bool = False) -> None:
        before = var.value()
        if bake:
            var.base = self.output(0)
        previous = len(var.links)
        var.links = [item for item in var.links if item.state is not self]
        self.instance.changed(var, before, structural=len(var.links) != previous)
        for state in var.stack_states():
            state.stack_changed(var)


def run_entry(instance: Instance, node: Node, caller: State | None) -> bool:
    return instance.follow(node, "m_outPlug", None)


for _name in ENTRY_CLASSES:
    HANDLERS[_name] = run_entry


def message_entries(instance: Instance, message: int) -> list[Node]:
    """The EntryGameMessage nodes and active GameMessageEntry states of an instance for a message."""
    found = []
    for node in instance.graph.nodes:
        if node.client_only:
            continue
        cfg = node.field("m_gameMessage")
        if not isinstance(cfg, dict) or expr.guid_of(cfg.get("m_gameMessage")) != message:
            continue
        if node.cls in MESSAGE_ENTRIES:
            found.append(node)
        elif node.is_state:
            state = instance.node_state(node)
            if state is not None and state.active:
                found.append(node)
    return found


def send_message(component: Component, message: int, params: dict, sender: int) -> int:
    """Deliver a game message to every instance of the component (at now). Returns how many nodes get it."""
    return sum(send_instance_message(instance, message, params, sender)
               for instance in list(component.instances.values()))


def send_instance_message(instance: Instance, message: int, params: dict, sender: int) -> int:
    """Queue a message at the component clock for only this graph instance."""
    entries = message_entries(instance, message)
    for node in entries:
        delivery = (message, dict(params), sender)
        instance.post(Event(instance.component.now, 0, MESSAGE, node=node, message=delivery))
    return len(entries)


INSTANCE_SCOPE, ENTITY_SCOPE = INSTANCE, ENTITY
