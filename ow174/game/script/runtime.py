"""The statescript runtime: graph instances on an entity, their states, variables and events, run the way
the client runs them, so that the server reaches the same states the owning client predicts.

Read in the client (the addresses are in the docstrings below):
- Time is int64 milliseconds. Command frame K is run as the window [16K, 16K+15]: an update of every
  instance at each time an event is due, and one at the window's end (component update 0x7FF78AA9C910,
  step 0x7FF78AA9DE30). An update drains the instance's events up to now, ticks the ticking states and
  runs the deferred list, until the deferred list is empty (0x7FF78AAA27C0).
- Events (dispatcher 0x7FF78AAA1690): 0 a state op (begin, finish, abort), 1 a timer, 2 a deferred
  completion check, 3 an Entry, 4 a variable assignment, 8 a button edge, 9 "a watched variable changed".
  Inserted after every event
  of the same time (FIFO), never in the past.
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
STATE_OP, TIMER, CHECK, ENTRY, ASSIGN, BUTTON, CHANGED, MESSAGE = 0, 1, 2, 3, 4, 8, 9, 10
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


def state_class(*names: str):
    def register(cls):
        for name in names:
            STATE_CLASSES[name] = cls
        return cls

    return register


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

    __slots__ = ("base", "key", "links", "partial", "resolving", "watchers")

    def __init__(self, key: tuple) -> None:
        self.key = key  # (instance id or 0 for the entity, var id)
        self.base = None
        self.links: list[Link] = []
        self.watchers: dict[State, None] = {}
        self.resolving = False
        self.partial = None

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
        return self._read(self.instance.find_var(scope, var, create=self.watcher is not None))

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

    def position(self, entity: int):
        component = self.instance.component
        value = component.world.position(entity)
        if value is None:
            if component.strict:
                component.unsupported(self.instance, "entity position", f"{entity:08X}")
            return expr.Vec3(0.0, 0.0, 0.0)
        return value

    def ruleset(self, key: int):
        found = self.var(ENTITY, key)
        if found is not None:
            return found
        value = self.instance.component.world.ruleset(key)
        return 0 if value is None else value

    def would_stack_to_top(self, var, priority):
        if var is None:
            return False
        target = self.instance.find_var(var[0], var[1], create=True)
        if self.watcher is not None:
            self.watcher.watch(target)
        return would_stack_to_top(target, priority)

    def poll_native(self, cfg: dict):
        """Keep native scalar and iterator dependencies on the existing poll list."""
        if self.watcher is not None:
            self.watcher.natives.add(cfg.get("$"))
            self.watcher.native_dependency(cfg)
            if not self.watcher.polled:
                self.watcher.polled = True
                self.watcher.ticks()

    def native(self, cfg: dict):
        """A native config's scalar +20 interface and compatibility polling."""
        if cfg.get("$") == "STU_4BD39CFD":
            from ow174.game.script import play

            return play.array_item(self.instance, cfg, watcher=self.watcher)
        self.poll_native(cfg)
        from ow174.game.script import controller_heroes, entity_queries, entity_team

        result = entity_queries.evaluate(self, cfg)
        if result is NotImplemented:
            result = controller_heroes.scalar(self, cfg)
        if result is NotImplemented:
            result = entity_team.evaluate(self, cfg)
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

    raycast: Callable[[RaycastRequest], RaycastResult | None] | None = None

    def __init__(self, hero: int = 0, mode: int = 0) -> None:
        self.hero = hero
        self.mode = mode

    def ruleset(self, key: int):
        from ow174.game.script import rulesets

        return rulesets.default(key, self.hero, self.mode)

    def entity_var(self, entity: int, var: int):
        if entity == GAME_MODE_ENTITY and self.mode:
            variables = mode_variables(self.mode)
            if variables is not None:
                return variables.get(var)
        return None

    def entity_var_slot(self, entity: int, var: int, *, create: bool = False):
        """A declared live foreign variable slot, known absence, or unknown service."""
        return NotImplemented

    def position(self, entity: int):
        return expr.Vec3(0.0, 0.0, 0.0)

    def ai_weapon_button(self, slot: int) -> int | None:
        return None

    def velocity_action(self, instance: "Instance", node: Node):
        """Optional body mover adapter; unknown semantics must remain explicit."""
        return NotImplemented

    def selects_ai_behavior(self, instance: "Instance") -> bool:
        return False

    def property_modifiers(self, entity: int):
        """An explicitly known native component 31, None if absent, or unknown."""
        return NotImplemented

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

    def voice_context(self, entity: int):
        """Explicit shared voice host/manager and separate +80 poll owner."""
        return NotImplemented

    def animation_aliases(self, entity: int):
        """Known component55 alias list, known absence, or unknown."""
        return NotImplemented

    def model_look_context(self, entity: int):
        """Explicit native namespace, target policy and model-look consumers."""
        return NotImplemented

    def data_flow_context(self, entity: int):
        """Explicit component47 mapping registry and native namespace availability."""
        return NotImplemented

    def entity_query_context(self, entity: int):
        """Explicit active query namespace, environment, entity and possession lookup."""
        return NotImplemented

    def controller_hero_context(self, entity: int, *, component: "Component | None" = None):
        """Ordered native hero catalog, controller lookup and availability services."""
        return NotImplemented

    def entity_team_context(self, entity: int, *, component: "Component | None" = None):
        """Native context, two entity lookups and filter/environment dependencies."""
        return NotImplemented

    def script_failed(self, component: "Component") -> None:
        """Discard external effects of a component that strict execution poisoned."""

    def native(self, cfg: dict, instance: "Instance | None"):
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

    def message_state_context(self, entity: int, *, component: "Component | None" = None):
        """Persistent State manager, explicit native frame/role/priority services."""
        return NotImplemented

    def game_message_subject(self, instance: "Instance", node: Node, instigator: int):
        instance.component.unsupported(instance, 'game message subject', node.cls, node)

    def play_script(self, instance: "Instance", node: Node) -> None:
        """Select a native PlayScript resource, then ask the world to route it."""
        from ow174.game.script.play import resolve_resource

        resource = resolve_resource(instance, node)
        if resource is not None:
            self.play_resolved_script(instance, node, resource)

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
        self.held: list[int] = []  # logical buttons held down, in press order
        self.button = NO_BUTTON  # the button of the edge being handled
        self.disabled: dict[int, list[State]] = {}  # button -> active DisableLogicalButton states
        self.seq = EventSequence()
        self.next_id = 1
        self.destroyed: list[Instance] = []
        # Volley state, shot number, time, and the frozen activation counter and start.
        self.shots: list[tuple[State, int, int, tuple[int, int]]] = []
        self.owner_dead = False  # the body's health is 0 (combat.py): presses and shots stop
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
            self.held.clear()
            self.code_states.clear()
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

    def changed(self, var: Var, before, *, stack_source: "State | None" = None):
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
            elif not changed:
                continue
            if state.active and not state.flags_changed:
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
            found = self.vars[var] = Var((0, var))
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
        times = [item.queue[0].time for item in self.instances.values() if item.queue and not item.stopped]
        return min(times) if times else None

    def edges(self, buttons: list[tuple[int, bool]]) -> None:
        """Button edges at now, on every instance (0x7FF789C66AA0 queues each on all the instances)."""
        for button, pressed in buttons:
            for instance in list(self.instances.values()):
                instance.post(Event(self.now, 0, BUTTON, button=button, pressed=pressed))

    def run_frame(self, frame: int, buttons: list[tuple[int, bool]] = (), *, end_phase=None) -> None:
        """Command frame `frame`: its edges at 16F, then an update at every due event time in [16F, 16F+15]
        and one at 16F+15. An explicit ordinary end phase runs once after those
        events and before deferred channel 1 (104319 1B8C910/D0D350)."""
        if end_phase is not None and not callable(end_phase):
            raise ValueError("ordinary end phase must be callable")
        with self.operation():
            self.frame = frame
            self.now = frame * self.frame_ms
            end = self.now + self.frame_ms - 1
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
        self.auto_retire = False  # Native Instance318 bit11, set for PlayScript byte zero.
        self.initialized_frame = 0  # Native Instance+58.
        self.retirement_pending = False  # Native Instance318 bit22.

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

    def changed(self, var: Var, before, *, stack_source: "State | None" = None) -> None:
        self.component.changed(var, before, stack_source=stack_source)

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
            if self.component.strict and node.cls not in STATE_CLASSES and node.cls != "STUStatescriptState":
                self.component.unsupported(self, "state", node.cls, node)
            found = self.states[index] = STATE_CLASSES.get(node.cls, State)(self, node, index)
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

    def init(self) -> None:
        """Init (0x7FF78AAA3F00): every Entry that runs on the server is queued at the start time."""
        self.initialized = True
        self.initialization_pending = False
        self.start = self.component.now
        self.initialized_frame = self.component.frame & 0xFFFFFFFF
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
                state.try_complete()
        elif event.kind == CHANGED:
            state.flags_changed = False
            if state.active:
                state.dependency_changed()
        elif event.kind == ENTRY:
            self.run_node(event.node, None)
        elif event.kind == ASSIGN:
            scope, identifier, value = event.assignment
            self.write((scope, identifier), value)
        elif event.kind == BUTTON:
            self.button_edge(event.button, event.pressed)
        elif event.kind == MESSAGE:
            self.deliver(event)

    def button_edge(self, button: int, pressed: bool) -> None:
        """A logical-button edge (event 8): the held list, then every LogicalButton/Ability state of the
        graph's m_logicalButtonStateIndices, active or not."""
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
            taken = self.run_node(target, None if target.is_action else caller) or taken
        return taken

    def follow_states(self, node: Node, path: str) -> list["State"]:
        """The states a subgraph plug lists (m_FF3DAF1E)."""
        plug = node.field(path)
        if not is_plug(plug):
            return []
        found = []
        for index in plug.get(SUBGRAPH_STATES) or []:
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
        self.watched: dict[Var, None] = {}
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
        self.on_enter()
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
        self.instance.exit_subgraph(self.node, "m_subgraphPlug")
        self.on_end(finished)
        self.on_cleanup()
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

    def watch(self, var: Var) -> None:
        if self.active and not self.ending:
            var.watchers[self] = None
            self.watched[var] = None

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
        self.instance.changed(var, before)
        for state in var.stack_states():
            state.stack_changed(var)
        return var

    def unlink(self, var: Var, bake: bool = False) -> None:
        before = var.value()
        if bake:
            var.base = self.output(0)
        var.links = [item for item in var.links if item.state is not self]
        self.instance.changed(var, before)
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
