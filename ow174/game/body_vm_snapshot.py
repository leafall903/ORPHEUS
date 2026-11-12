"""Read-only, fail-closed body VM storage export; transport has a separate owner.

No graph callbacks, variable resolution, frame snapshots or random draws run here.
Static Graph/Node storage is bound by exact typed definition and source-data hashes.
Cross-entity watches are recorded as references at the component boundary.
This is an observation format, not a restorer or a Python stack checkpoint.
"""

import hashlib
import json
import marshal
import random
import types
import uuid

from ow174.game import assassin_controller, enforcer_controller, hanzo_movement, replay_values
from ow174.game import npc_controller_storage
from ow174.game.script.player_controller import ControllerVM, ControllerWorld
from ow174.game.script.controller_filter import ControllerFilterInputs
from ow174.game.script import assassin_movement, scene
from ow174.game.script.support_effects import EffectWorld as SupportWorld
from ow174.game.script.healing_status import StatusWorld
from ow174.game.health_packs import PackWorld
from ow174.game.sombra_control import DeviceWorld as SombraDeviceWorld
from ow174.game.ball_mines import MineWorld
from ow174.game.sigma_barrier import BarrierWorld as ExperimentalBarrierWorld
from ow174.game.orisa_barrier import BarrierWorld
from ow174.game.orisa_halt import HaltPhysicsExplosion, PullQuery
from ow174.game.orisa_supercharger import DeviceWorld
from ow174.game.script import (
    body_owner,
    entity_link,
    data_flow,
    health_queries,
    entity_queries,
    expr,
    fold,
    graph,
    message_manager,
    message_receive,
    message_scope,
    nodes,
    play,
    runtime,
    scopes,
    spatial_queries,
)
from ow174.game.script.bot import AssassinControllerWorld, BotWorld
from ow174.game.script.driver import BodyScript, BodyWorld, WheelCallout, WheelSprayEvent
from ow174.game.photon_barrier import BarrierWorld as PhotonWorld
from ow174.game.winston_barrier import DomeWorld

SCHEMA = "ow174.body-vm-storage.v1"
COMPONENT_SCHEMA = "ow174.component-vm-storage.v1"
SCOPE = "body-vm-storage-excludes-transport"
COMPONENT_SCOPE = "component-vm-storage"
TRANSPORT_FIELDS = frozenset(("stream", "create_arrived", "first_owner_frame", "reports", "report_count"))
_COMPONENT = frozenset((
    "entity", "frame_ms", "world", "rng", "frame", "now", "instances", "vars", "held", "button",
    "disabled", "seq", "next_id", "destroyed", "shots", "health_reports", "motion_reports", "owner_dead", "input_suppressed", "fault", "_operation_depth",
    "_follow_depth", "_work_left", "strict", "code_states", "code_pending", "code_observers",
    "state_registries", "parameter_scopes", "startup_entity_values", "maximum_health",
))
_INSTANCE = frozenset((
    "component", "id", "graph", "parent", "parent_state", "vars", "states", "queue", "ticking",
    "deferred", "params", "stopped", "teardown", "initialized", "initialization_pending", "start",
    "disabled_start_entries", "disabled_message_entries", "cached_source_entity", "ruleset_target_entity",
    "auto_retire",
    "initialized_frame",
    "retirement_pending", "join_inputs", "plug_source",
))
_STATE = frozenset((
    "instance", "node", "index", "active", "ending", "counter", "start", "ended_at", "ticked_at",
    "watched", "flags_changed", "flags_check", "polled", "natives",
))
_SCRIPT = frozenset((
    "hero", "entity", "record", "frame_ms", "world", "component", "frame", "buttons", "stream",
    "create_arrived", "retired", "first_owner_frame", "world_frame_anchor", "server_only", "ult_seconds", "passive_charge_ms", "self_healing",
    "ult_gain_bonus", "ultimates", "reports", "report_count", "support_healing", "reaper_charge_ms", "reaper_charge_tick",
    "winston_charge_ms", "winston_charge_tick", "winston_primal_clock",
    "self_repairs", "control_windows", "sleep_windows", "frost_windows",
))
_SCRIPT_OPTIONAL = ("charge_clock", "passive_ult_deadline", "passive_ult_last_command_tick",
                    "orisa_pose_anchor", "orisa_fortify_pending", "grapple_interrupt_frame", "ball_birth", "cryo_birth")
_GRAPH = frozenset((
    "index", "header", "fields", "items", "containers", "nodes", "unknown_rids", "_by_pos", "_by_rid",
    "states", "entries", "remote_nodes", "sync_vars", "_owner", "_remote", "_presence",
))
_NODE = frozenset(("pos", "cls", "uid", "rid", "remote", "state", "client_only", "server_only", "fields"))
_VAR = ("base", "key", "links", "partial", "resolving", "watchers")
_STATE_LAYOUTS = {
    nodes.BodyRecallTarget: (("result", "outputs", "ever_valid"), ()),
    nodes.BodyRecallMove: (("accepted", "destination"), ()),
    nodes.AuthoredBodyHealthPool: (("remove_wave_health",), ()),
    nodes.Component86NumericPairState: (("registry", "request_registration"), ()),
    nodes.AIControlOverride: (("remove_record", "request_registration"), ()),
    nodes.BodyPlacement: ((), ()),
    nodes.JunkensteinControllerState: ((), ()),
    nodes.JunkensteinBehaviorBranch: ((), ()),
    nodes.FilterArray: ((), ()),
    nodes.StaticTagTarget: ((), ()),
    nodes.RecentInputState: (("recent",), ("history",)),
    runtime.State: ((), ()),
    HaltPhysicsExplosion: ((), ()),
    nodes.RegisteredState: ((), ()),
    nodes.CombatModFilter: (("combat_filter",), ()),
    nodes.CombatDamageFilter: (("combat_filter",), ()),
    nodes.RecallRewind: ((), ()),
    nodes.SharedVariables: ((), ()),
    nodes.EntityControl: (("control_radius", "control_bypass", "control_inverse_a", "control_inverse_b",
                           "control_targets", "control_previous", "control_generation"), ()),
    nodes.AnimationAlias: (("alias_handle",), ()),
    nodes.ModelLook: (("scope_registry", "look", "look_target"), ()),
    nodes.DataFlowMapping: (("mapping", "mapping_target", "mapping_self"), ()),
    nodes.VoiceStimulus: (("voice_handle", "voice_attempts", "voice_duration", "voice_part",
                           "voice_started_at", "voice_delay", "voice_cooldown", "voice_retry_at",
                           "voice_submitted", "voice_failed"), ()),
    nodes.VoiceSetOverride: ((), ()),
    nodes.NumericPropertyModifiers: (("per_frame",), ()),
    nodes.LinkedTarget: (("mover_angles", "linked_entity", "adjustment", "local_yaw", "local_limits", "linked_cache"), ()),
    nodes.ScopedSubgraph: (("scope_registry",), ()),
    nodes.AuxiliaryEffect: (("scope_registry", "effect", "effect_list", "owned_effects"), ()),
    nodes.BooleanSwitch: (("current", "pending"), ()),
    nodes.DelayedBooleanSwitch: (("current",), ()),
    nodes.MovementFlags: (("value", "last_frame"), ()),
    nodes.EncounterTargetTag: ((), ()),
    nodes.Stack: (("top", "under", "value", "var"), ()),
    nodes.LogicalButton: (("down", "pressed_at", "early_counter"), ()),
    nodes.AIBehavior: (("selected", "ai_previous"), ()),
    nodes.AIBehaviorState: (("record", "remove_record", "registration"), ()),
    nodes.ActiveOutput: ((), ()),
    nodes.AssassinMovementState: (("movement_outputs", "movement_frame"), ()),
    nodes.AssassinPursuitCallbacks: (("pursuit_hosted", "pursuit_refresh"), ()),
    nodes.AssassinFacingState: ((), ()),
    nodes.AssassinNavigationState: ((), ()),
    nodes.AssassinButtonRequest: ((), ()),
    nodes.AIFireWeapon: (("button_id",), ()),
    nodes.ControllerButton: ((), ()),
    nodes.DisableLogicalButton: ((), ("button_id",)),
    nodes.Wait: ((), ("ms", "world_deadline")), nodes.WaitForServerFrames: ((), ()),
    nodes.ConditionalTimer: ((), ()),
    nodes.WaitFrames: (("target",), ()),
    nodes.ChaseVar: (("cur", "reached", "remaining", "last", "var", "interrupted"), ("world_used",)),
    nodes.WeaponVolley: (
        ("offset", "volleys", "per_shot", "ammo0", "rate", "spread0", "spread_ramp", "spread_decay", "links", "reported", "last_values"),
        ("max_shots", "min_shots", "shot_spread"),
    ),
    nodes.GrappleCast: (("cast",), ()),
    nodes.MoveTo: (("movement",), ()),
    nodes.PositionSearch: ((), ()),
    nodes.MovementFlagProjection: (("value", "vars"), ()),
    nodes.PlacementPreview: (("last_frame", "latest_frame"), ()),
    nodes.SummonerPlacement: (("confirmed",), ()),
    nodes.Anim: ((), ("scalar_value",)),
    nodes.Ability: (
        ("flags", "presses", "cur", "cooldown_rate", "cooldown_observed", "last", "var", "stack_var"), (),
    ),
    nodes.Defer: ((), ()), nodes.Watch: ((), ("seen",)), nodes.Switch: (("case",), ()),
    nodes.ForCodeSynced: ((), ()),
    nodes.SubScript: (("child",), ()),
    nodes.LinkedPlayScript: ((), ()),
    nodes.GameMessageEntry: (("message_context", "message_sender", "message_stacked", "message_key",
                             "message_flags", "message_selected", "message_generation", "message_count",
                             "message_sources", "message_cache", "message_refresh", "message_updating",
                             "message_getting", "message_epoch", "message_last_epoch", "message_bindings",
                             "message_observed"), ("received", "host_capture_started")),
    nodes.SendGameMessageState: (("message_context", "message_frame", "message_sequence", "message_keys",
                                  "message_notified"), ()),
    nodes.CosmeticEntity: ((), ("var",)),
    nodes.GamePadVibration: ((), ()), nodes.TrackTargets: (("targets", "managed", "target_var", "last_refresh"), ()),
    nodes.CompatiblePet: (("pet_entity", "pet_var", "linked_pet"), ()),
    nodes.TargetEffects: (("children",), ()), nodes.Effect: ((), ()),
    nodes.ModifyHealth: (("last_applied", "target_body", "heal_last", "heal_elapsed", "heal_duration", "heal_amount"), ()),
    nodes.SprayPlacement: (("placement",), ()),
    nodes.CompatibleTrackRay: (("ray_hit",), ()),
    nodes.CreateEntity: ((), ()),
    nodes.Component51CounterState: (("health_counter",), ()),
    nodes.DataFlowSnapshotState: (("scope_registry", "snapshot_registration"), ()),
    nodes.ClientOnlyPulser: (("pulses", "pulsed_at"), ()), nodes.ActiveQuery: ((), ()),
}

from ow174.game.script import distance_constraints, possession_requests

_VALUE_LAYOUTS = {
    possession_requests.Inputs: ("owner", "clock"),
    possession_requests.Selection: ("slot", "identifier", "flag"),
    possession_requests.Request: ("owner", "clock", "kind", "priority", "above_equal", "payload", "selection"),
    possession_requests.Registry: ("inputs", "accepted_selections", "_requests", "_selections"),
    distance_constraints.Entry: ("state", "priority", "sequence"),
    distance_constraints.Registry: ("_entries", "_sequence"),
    nodes.AIBehaviorRequest: ("kind", "priority", "prefer_later", "shared", "inputs"),
    ControllerVM: ("definition", "retired", "component", "reserved_wire_ids"),
    ControllerFilterInputs: ("owner", "component_masks", "current_bits", "replicated_bits", "primary_status", "health_status", "flags28", "provenance", "revision", "retired", "bound_host"),
    assassin_movement.Sample: ("flags", "axis"),
    scene.SceneEntity: npc_controller_storage.SCENE_ENTITY_FIELDS,
    scene.Placement: npc_controller_storage.PLACEMENT_FIELDS,
    entity_queries.Target: ("entity", "possession", "possessor", "body_field"),
    entity_queries.Possession: ("current",),
    entity_queries.BodyField: ("entity",),
    scopes.Registry: ("states",),
    hanzo_movement.Climb: ("elapsed", "active", "blocked", "normal", "mantle"),
    scopes.ForwardHost: ("states", "component", "frame", "history", "dirty", "_processing", "_drain_depth"),
    scopes.History: ("records", "initial", "dispatched"),
    scopes.Edge: ("frame", "active"),
    data_flow.Snapshot: ("mapping", "value"),
    data_flow.SnapshotRegistry: ("entries",),
    health_queries.StateCounter: ("value",),
    WheelCallout: ("serial", "instance", "start", "node", "time", "choice", "values"),
    WheelSprayEvent: ("serial", "instance", "start", "node", "time", "choice", "values", "placement"),
    entity_link.Context: ("owner", "instance_present", "environment_present", "lookup", "watch_environment", "watch_link", "retired"),
    spatial_queries.Context: ("owner", "instance_present", "namespace_present", "query", "retired"),
    body_owner.Context: ("owner", "instance_present", "environment_present", "lookup",
                         "watch_environment", "watch_owner", "retired"),
    entity_queries.Context: ("owner", "namespace_present", "environment_present",
                             "possession_context_present", "lookup", "watch_environment",
                             "watch_possession", "retired", "lookup_linked", "lookup_participant", "watch_possessor"),
    expr.Asset: ("guid",), expr.Entity: ("id",), expr.Vec3: ("x", "y", "z"),
    expr.Handle: ("kind", "instance", "state", "extra"), expr.Map: ("items",), expr.PairArray: ("items",),
    runtime.RaycastResult: ("hit", "entity", "point", "normal"),
    runtime.Link: ("state", "slot", "priority", "above", "stack", "dynamic", "frame_cached"),
    runtime.Event: ("time", "seq", "kind", "state", "op", "param", "node", "button", "pressed",
                    "message", "assignment", "transient"),
    message_receive.Packet: ("message", "caller", "instigator", "params"),
    message_receive.ParamScope: ("instance", "node", "packet"),
    message_scope.ParamScope: ("receiver", "sender"),
    fold.Scope: ("values", "accumulator", "index"),
    message_manager.Key: ("entity", "flags", "message"),
    graph.SyncVar: ("index", "var", "scope", "flag"),
    play.InvocationContext: ("caller", "owner", "instigator"),
}


class BodyVMSnapshotError(ValueError):
    pass


def _name(cls) -> str:
    return f"{cls.__module__}.{cls.__qualname__}"


def _layout(obj, required, optional=()):
    fields = vars(obj)
    required, optional = set(required), set(optional)
    if required - fields.keys() or fields.keys() - required - optional:
        raise BodyVMSnapshotError(f"unknown or missing stored fields in {_name(type(obj))}")
    return fields


def _integer(value, where):
    if type(value) is not int:
        raise BodyVMSnapshotError(f"invalid integer identity at {where}")
    return value


def _containers(fields, names, kind, where):
    if any(type(fields[name]) is not kind for name in names):
        raise BodyVMSnapshotError(f"unknown stored container at {where}")


def _digest(value) -> str:
    text = json.dumps(value, ensure_ascii=True, allow_nan=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


class _Export:
    def __init__(self, component, definition_digests, external_reference=None):
        if type(component) is not runtime.Component:
            raise BodyVMSnapshotError("unknown component class")
        fields = _layout(component, _COMPONENT)
        _containers(fields, ("instances", "vars", "disabled", "state_registries"), dict, "component")
        _containers(fields, ("held", "destroyed", "shots", "code_states", "code_pending", "code_observers"),
                    list, "component")
        _containers(fields, ("parameter_scopes", "startup_entity_values"), list, "component")
        _integer(fields["entity"], "component")
        self.component = component
        self.definition_digests = definition_digests
        self.external_reference = external_reference
        self.instances = {}
        self.states = {}
        self.variables = {}
        self.entity_components = {component.entity: component}
        self.graphs = {}
        self.node_owners = {}
        self.node_refs = {}
        self.pending = []
        self.records = []
        self.ancestors = set()
        self.storage = []
        self.storage_ids = {}
        self.storage_scopes = []

    def identity(self, obj, scope="vm"):
        """Number only original stored objects; keep them alive for this capture's duration."""
        found = self.storage_ids.get(id(obj))
        if found is None:
            found = len(self.storage)
            self.storage_ids[id(obj)] = found
            self.storage.append(obj)
            self.storage_scopes.append(set())
        elif self.storage[found] is not obj:
            raise BodyVMSnapshotError("storage identity changed during capture")
        self.storage_scopes[found].add(scope)
        return ["storage", found]

    def identities(self):
        return tuple((number, obj, "+".join(tag for tag in ("vm", "model")
                                           if tag in self.storage_scopes[number]))
                     for number, obj in enumerate(self.storage))

    def _instance(self, obj):
        if type(obj) is not runtime.Instance:
            raise BodyVMSnapshotError("unknown instance class")
        fields = _layout(obj, _INSTANCE)
        _containers(fields, ("vars", "states"), dict, "instance")
        _containers(fields, ("queue", "ticking", "deferred", "params", "join_inputs"), list, "instance")
        _containers(fields, ("disabled_start_entries", "disabled_message_entries"), set, "instance")
        source = fields["cached_source_entity"]
        if type(source) is not int or not 0 <= source <= 0xFFFFFFFF:
            raise BodyVMSnapshotError('invalid instance source entity cache')
        target = fields["ruleset_target_entity"]
        if type(target) is not int or not 0 <= target <= 0xFFFFFFFF:
            raise BodyVMSnapshotError('invalid instance ruleset target entity')
        frame = fields['initialized_frame']
        if type(frame) is not int or not 0 <= frame <= 0xFFFFFFFF:
            raise BodyVMSnapshotError('invalid instance initialization frame')
        if any(type(fields[key]) is not bool for key in ('auto_retire', 'retirement_pending')):
            raise BodyVMSnapshotError('invalid instance automatic lifetime flags')
        if fields["component"] is not self.component:
            raise BodyVMSnapshotError("foreign component instance reference")
        number = _integer(fields["id"], "instance")
        if number in self.instances and self.instances[number] is not obj:
            raise BodyVMSnapshotError("distinct instances reuse a local instance identity")
        if number not in self.instances:
            self.instances[number] = obj
            self.pending.append(("instance", obj))
        return ["instance", number]

    def _state(self, obj):
        kind = type(obj)
        if kind not in _STATE_LAYOUTS:
            raise BodyVMSnapshotError(f"unknown state class {_name(kind)}")
        required, optional = _STATE_LAYOUTS[kind]
        fields = _layout(obj, _STATE | set(required), optional)
        _containers(fields, ("watched",), dict, "state")
        _containers(fields, ("natives",), set, "state")
        owner = fields["instance"]
        self._instance(owner)
        self._graph(owner.graph)
        index = _integer(fields["index"], "state")
        if owner.states.get(index) is not obj:
            raise BodyVMSnapshotError("state is not registered under its instance identity")
        if (type(fields["node"]) is not graph.Node or not 0 <= index < len(owner.graph.states)
                or owner.graph.states[index] is not fields["node"]):
            raise BodyVMSnapshotError("state node does not match its graph slot")
        if kind is not runtime.state_type(fields["node"].cls, owner.component.strict, owner.graph.index, fields["node"].pos):
            raise BodyVMSnapshotError("state class does not match its registered node model")
        key = (owner.id, index)
        if key not in self.states:
            self.states[key] = obj
            self.pending.append(("state", obj))
        elif self.states[key] is not obj:
            raise BodyVMSnapshotError("distinct states reuse a local state identity")
        return ["state", owner.id, index]

    def _var(self, obj):
        if type(obj) is not runtime.Var:
            raise BodyVMSnapshotError("unknown variable class")
        if runtime.Var.__slots__ != (*_VAR, "entity_owner"):
            raise BodyVMSnapshotError("unknown variable slot layout")
        try:
            data = {name: object.__getattribute__(obj, name) for name in _VAR}
        except AttributeError:
            raise BodyVMSnapshotError("missing variable storage field") from None
        _containers(data, ("links",), list, "variable")
        _containers(data, ("watchers",), dict, "variable")
        key = data["key"]
        if type(key) is not tuple or len(key) != 2 or any(type(value) is not int for value in key):
            raise BodyVMSnapshotError("invalid variable identity")
        if key not in self.variables:
            self.variables[key] = obj
            self.pending.append(("var", obj))
        elif self.variables[key] is not obj:
            raise BodyVMSnapshotError("distinct variables reuse a local variable identity")
        return ["var", *key]

    def _external_entity(self, component):
        if type(component) is not runtime.Component:
            raise BodyVMSnapshotError("unknown external watch component")
        entity = _integer(component.entity, "external watch entity")
        if not 0 <= entity <= 0xFFFFFFFF or not expr.valid_entity(entity):
            raise BodyVMSnapshotError("invalid external watch entity")
        if entity == self.component.entity or self.entity_components.setdefault(entity, component) is not component:
            raise BodyVMSnapshotError("external watch components reuse an entity identity")
        return entity

    def _watched_var(self, state, var):
        if type(var) is runtime.Var and var.entity_owner not in (None, self.component):
            owner = var.entity_owner
            entity = self._external_entity(owner)
            scope, number = var.key
            if scope != 0 or owner.vars.get(number) is not var or state not in var.watchers:
                raise BodyVMSnapshotError("invalid external watched variable")
            return {"ref": ["external-var", entity, _integer(number, "external variable id")]}
        return self.value(var)

    def _var_watcher(self, var, state):
        if type(state) in _STATE_LAYOUTS and state.instance.component is not self.component:
            instance = state.instance
            entity = self._external_entity(instance.component)
            if (var.entity_owner is not self.component or var not in state.watched
                    or instance.component.instances.get(instance.id) is not instance
                    or instance.states.get(state.index) is not state):
                raise BodyVMSnapshotError("invalid external variable watcher")
            return {"ref": ["external-state", entity, _integer(instance.id, "external watch instance"),
                            _integer(state.index, "external watch state")]}
        return self.value(state)

    def _watch_map(self, mapping, key_ref):
        return {"kind": "dict", "identity": self.identity(mapping),
                "items": [[key_ref(key), self.value(value)] for key, value in mapping.items()]}

    def _graph(self, obj):
        if type(obj) is not graph.Graph:
            raise BodyVMSnapshotError("unknown graph class")
        fields = _layout(obj, _GRAPH)
        _containers(fields, ("header", "fields", "containers", "_by_pos", "_by_rid"), dict, "graph")
        _containers(fields, ("nodes", "unknown_rids", "states", "entries", "remote_nodes", "sync_vars",
                            "_owner", "_remote", "_presence"), list, "graph")
        index = _integer(fields["index"], "graph")
        if index in self.graphs and self.graphs[index] is not obj:
            raise BodyVMSnapshotError("distinct graph objects reuse a graph identity")
        if index not in self.graphs:
            self.graphs[index] = obj
            self.pending.append(("graph", obj))
            for node in fields["nodes"]:
                if type(node) is not graph.Node:
                    raise BodyVMSnapshotError("unknown graph node class")
                data = _layout(node, _NODE)
                _containers(data, ("fields",), dict, "node")
                if type(data["cls"]) is not str:
                    raise BodyVMSnapshotError("unknown node model class identity")
                if any(type(data[name]) is not bool for name in ("client_only", "server_only")):
                    raise BodyVMSnapshotError("invalid graph node filter flags")
                for name in ("uid", "rid", "remote"):
                    _integer(data[name], f"node.{name}")
                if data["state"] is not None:
                    _integer(data["state"], "node.state")
                position = _integer(data["pos"], "node")
                key = (index, position)
                if key in self.node_owners:
                    raise BodyVMSnapshotError("duplicate graph node identity")
                if id(node) in self.node_refs:
                    raise BodyVMSnapshotError("node is shared by distinct graph identities")
                self.node_owners[key] = node
                self.node_refs[id(node)] = key
            for name, attribute in (("_by_pos", "pos"), ("_by_rid", "rid")):
                for key, node in fields[name].items():
                    if (type(key) is not int or type(node) is not graph.Node
                            or self.node_refs.get(id(node), (None,))[0] != index
                            or getattr(node, attribute) != key):
                        raise BodyVMSnapshotError("graph lookup does not match node identity")
            for name in ("states", "entries", "remote_nodes", "_owner", "_remote"):
                for node in fields[name]:
                    if node is not None and self.node_refs.get(id(node), (None,))[0] != index:
                        raise BodyVMSnapshotError("graph list refers to a foreign node")
            expected_owner = [node for node in fields["states"] if node is not None
                              and not node.client_only and not node.server_only]
            expected_remote = []
            for node in fields["remote_nodes"]:
                if node is None:
                    continue
                flag = node.fields.get("m_2BBEEAB8")
                if flag is not None and type(flag) not in (bool, int):
                    raise BodyVMSnapshotError("invalid graph node state flag")
                if flag and not node.client_only and not node.server_only:
                    expected_remote.append(node)
            expected_presence = []
            for item in fields["sync_vars"]:
                if type(item) is not graph.SyncVar:
                    raise BodyVMSnapshotError("unknown graph sync variable class")
                values = _layout(item, _VALUE_LAYOUTS[graph.SyncVar])
                for name in ("index", "scope", "flag"):
                    _integer(values[name], f"sync variable.{name}")
                if values["var"] is not None:
                    _integer(values["var"], "sync variable.var")
                if values["var"] is not None and values["scope"] == graph.INSTANCE:
                    expected_presence.append(item)
            for name, expected in (("_owner", expected_owner), ("_remote", expected_remote),
                                   ("_presence", expected_presence)):
                if (len(fields[name]) != len(expected)
                        or any(a is not b for a, b in zip(fields[name], expected, strict=True))):
                    raise BodyVMSnapshotError("graph derived cache differs from its source sequence")
        return ["graph", index]

    def _node(self, obj):
        found = self.node_refs.get(id(obj))
        if found is None or self.node_owners[found] is not obj:
            raise BodyVMSnapshotError("node is outside the component's graph definitions")
        _layout(obj, _NODE)
        return ["node", *found]

    def fields(self, obj, required, optional=(), exclude=()):
        data = _layout(obj, required, optional)
        self.identity(data)
        result = []
        for name, value in data.items():
            if name not in exclude:
                scope = "model" if type(obj) is BodyScript and name == "record" else "vm"
                stored = (self._watch_map(value, lambda var: self._watched_var(obj, var))
                          if name == "watched" else self.value(value, scope=scope))
                result.append([name, stored])
        return result

    def _set_key(self, obj):
        """Sort unordered members before registering any identity or reference."""
        kind = type(obj)
        if kind is uuid.UUID:
            return ["uuid", format(obj.int, "032x")]
        if obj is None or kind in (bool, int, float, str, bytes):
            return replay_values.encode(obj)
        if kind is runtime.Instance:
            return ["instance", _integer(obj.id, "set instance")]
        if kind in _STATE_LAYOUTS:
            if type(obj.instance) is not runtime.Instance:
                raise BodyVMSnapshotError("unknown set state owner")
            return ["state", _integer(obj.instance.id, "set instance"), _integer(obj.index, "set state")]
        if kind is runtime.Var:
            return ["var", replay_values.encode(obj.key)]
        if kind is graph.Node:
            return self._node(obj)
        if kind in (expr.Asset, expr.Entity, expr.Vec3, expr.Handle):
            data = _layout(obj, _VALUE_LAYOUTS[kind])
            return [_name(kind), [[key, self._set_key(value)] for key, value in data.items()]]
        if kind in (tuple, frozenset):
            values = [self._set_key(item) for item in obj]
            if kind is frozenset:
                values.sort(key=lambda value: json.dumps(value, sort_keys=True))
            return [kind.__name__, values]
        raise BodyVMSnapshotError("set member has no supported stable ordering key")

    def value(self, obj, *, scope="vm"):
        if obj is NotImplemented:
            return {"sentinel": "NotImplemented"}
        kind = type(obj)
        if kind is uuid.UUID:
            return {"class": "uuid.UUID", "value": format(obj.int, "032x")}
        if obj is None or kind in (bool, int, float, str, bytes):
            return obj
        if obj is self.component:
            return {"ref": ["component"]}
        if self.external_reference is not None:
            reference = self.external_reference(obj)
            if reference is not None:
                return {"external_ref": reference}
        if kind is runtime.Instance:
            return {"ref": self._instance(obj)}
        if kind in _STATE_LAYOUTS:
            return {"ref": self._state(obj)}
        if kind is runtime.Var:
            if (type(self.component.world) is assassin_controller.Phase2World
                    and npc_controller_storage.is_scene_revision(obj)):
                fields = {name: object.__getattribute__(obj, name) for name in _VAR}
                _containers(fields, ("links",), list, "scene revision")
                _containers(fields, ("watchers",), dict, "scene revision")
                return {"class": _name(kind), "identity": self.identity(obj),
                        "fields": [[name, self.value(value)] for name, value in fields.items()]}
            return {"ref": self._var(obj)}
        if kind is graph.Graph:
            return {"ref": self._graph(obj)}
        if kind is graph.Node:
            return {"ref": self._node(obj)}
        if kind in (runtime.World, BodyWorld, BotWorld, ControllerWorld, AssassinControllerWorld, BarrierWorld, DomeWorld, PhotonWorld, DeviceWorld, PullQuery,
                    PackWorld, ExperimentalBarrierWorld, SombraDeviceWorld, SupportWorld, StatusWorld, MineWorld,
                    enforcer_controller.TargetWorld, assassin_controller.TargetWorld,
                    assassin_controller.Phase2World):
            if obj is not self.component.world:
                raise BodyVMSnapshotError("foreign VM world reference")
            fields = {"hero", "mode"}
            optional = ()
            if kind is ControllerWorld:
                fields.update(("readers", "filter_inputs", "pin_services", "linked_target_inputs", "numeric_modifiers"))
            if kind is BodyWorld:
                optional = ("beam_barrier", "healing_tick", "passive_charge_due", "scope_host", "pending_ult_credits", "historical_boost_award", "advancing_valkyrie_clock", "_spatial_query_context", "passive_healing_scales", "hanzo_climb")
                fields.update(("body", "body_definition", "throttles", "match", "velocity_requests", "communication_wheel",
                               "wheel_callouts", "emote_aliases", "emote_blend_trees", "spray_unlocks", "voice_effects",
                               "_body_owner_context", "_entity_query_context", "_possessor_query", "zenyatta_charge_ammo"))
            if kind is BotWorld:
                optional = ("body", "_body_owner_context", "_entity_query_context", "_spatial_query_context", "_entity_link_context", "pool_profile")
                fields.update(("weapon_buttons", "owner_position", "controller_components", "match",
                               "manual_target", "manual_nameplate", "controller", "component",
                               "controller_inputs", "heavy_counter_stuns", "control_context", "sniper_target", "sniper_retry_at"))
            if kind is BarrierWorld:
                fields.update(("match", "entity", "location"))
            if kind is PackWorld:
                fields.update(("match", "entity"))
            if kind is ExperimentalBarrierWorld:
                fields.add("barrier")
            if kind is DeviceWorld:
                fields.update(("fight", "entity", "location"))
            if kind in (SupportWorld, StatusWorld):
                fields.update(("match", "source", "owner"))
            if kind is SombraDeviceWorld:
                fields.update(("fight", "body", "tags"))
            if kind is MineWorld:
                fields.update(("manager", "mine"))
            if kind is PullQuery:
                fields.update(("explosion_sink", "origin", "caster", "context", "instance", "track", "radius"))
            # The match and its pending deliveries belong to the enclosing server snapshot.
            if kind is AssassinControllerWorld:
                fields.update(("mover", "body_entity", "match", "button_edges", "button_holders",
                               "movement_bits", "candidates", "recovery_pending", "recovery_started"))
            if kind is DomeWorld:
                fields.update(("manager", "actor"))
            if kind is PhotonWorld:
                fields.add("actor")
            if kind is enforcer_controller.TargetWorld:
                fields.update(("fight", "body", "selected"))
            if kind is assassin_controller.TargetWorld:
                fields.update(("fight", "body", "candidates", "attack_target", "windup_target"))
            if kind is assassin_controller.Phase2World:
                fields.update(npc_controller_storage.SCENE_WORLD_FIELDS)
                fields.update(("fight", "body"))
                optional = ("auto_scene", "auto_target", "sample")
            return {"class": _name(kind), "fields_identity": self.identity(vars(obj)),
                    "fields": self.fields(obj, fields, optional, exclude={"match", "fight", "explosion_sink", "controller", "mover", "manager", "actor", "barrier", "mine"})}
        if kind is types.BuiltinMethodType and type(obj.__self__) is dict and obj.__name__ == "get":
            return {"class": "builtins.dict.get", "identity": self.identity(obj),
                    "owner": self.value(obj.__self__)}
        if (kind is types.FunctionType and type(self.component.world) is assassin_controller.Phase2World
                and obj.__code__ in npc_controller_storage.phase2_observer_codes()):
            _layout(obj, set())
            if obj.__closure__ is not None or obj.__defaults__ is not None or obj.__kwdefaults__ is not None:
                raise BodyVMSnapshotError("unexpected scene observer closure or defaults")
            code = obj.__code__.replace(co_filename="ow174/game/assassin_controller.py")
            return {"class": "scene.observer", "identity": self.identity(obj),
                    "code_sha256": hashlib.sha256(marshal.dumps(code)).hexdigest()}
        if (kind is types.MethodType and obj.__self__ is self.component.world
                and type(obj.__self__) in (BodyWorld, BotWorld)
                and obj.__func__ in (BodyWorld._lookup_body_owner, BodyWorld._lookup_entity_query,
                                     BodyWorld._lookup_possessor, BodyWorld._watch_possessor,
                                     BodyWorld._poll_entity_query, BodyWorld._query_spatial, BotWorld._lookup_entity_link)):
            return {"class": "builtins.method", "identity": self.identity(obj),
                    "owner": ["component", "world"], "function": _name(obj.__func__)}
        if kind is random.Random:
            if obj is not self.component.rng:
                raise BodyVMSnapshotError("foreign RNG reference")
            data = _layout(obj, {"gauss_next"})
            return {
                "class": _name(kind), "identity": self.identity(obj),
                "fields_identity": self.identity(data), "fields": self.value(data),
                "state": self.value(random.Random.getstate(obj)),
            }
        if kind is runtime.EventSequence:
            if (runtime.EventSequence.__slots__ != ("_next_value",) or obj is not self.component.seq
                    or type(obj.next_value) is not int or obj.next_value < 0):
                raise BodyVMSnapshotError("invalid event sequence")
            return {"class": _name(kind), "identity": self.identity(obj), "next_value": obj.next_value}
        if kind is runtime.ScriptBudgetError:
            _layout(obj, set())
            return {"class": _name(kind), "identity": self.identity(obj),
                    "fields_identity": self.identity(vars(obj)), "args": self.value(obj.args)}
        if kind is runtime.UnsupportedScriptError:
            fields = _layout(obj, {"diagnostic"})
            return {"class": _name(kind), "identity": self.identity(obj),
                    "fields_identity": self.identity(fields), "fields": self.value(fields),
                    "args": self.value(obj.args)}
        if kind in _VALUE_LAYOUTS:
            fields = _layout(obj, _VALUE_LAYOUTS[kind])
            result = {"class": _name(kind), "fields_identity": self.identity(fields, scope)}
            identity = id(obj)
            if kind in (runtime.Link, runtime.Event, body_owner.Context, entity_queries.Context,
                        entity_queries.Possession, entity_queries.BodyField, spatial_queries.Context, entity_link.Context):
                reference = self.identity(obj, scope)
                if identity in self.ancestors:
                    return {"ref": reference}
                result["identity"] = reference
            elif identity in self.ancestors:
                raise BodyVMSnapshotError("cyclic immutable value record")
            self.ancestors.add(identity)
            try:
                result["fields"] = [[name, self.value(value, scope=scope)] for name, value in fields.items()]
                return result
            finally:
                self.ancestors.remove(identity)
        if kind not in (list, tuple, dict, set, frozenset):
            raise BodyVMSnapshotError(f"unknown stored value class: {_name(kind)}")
        identity = id(obj)
        reference = self.identity(obj, scope) if kind in (list, dict, set) else None
        if reference is not None and identity in self.ancestors:
            return {"ref": reference}
        if reference is not None:
            self.ancestors.add(identity)
        try:
            if kind is dict:
                return {
                    "kind": "dict", "identity": reference,
                    "items": [[self.value(key, scope=scope), self.value(value, scope=scope)]
                              for key, value in obj.items()],
                }
            if kind in (set, frozenset):
                ordered = sorted(obj, key=lambda item: json.dumps(self._set_key(item), sort_keys=True))
            else:
                ordered = obj
            result = {"kind": kind.__name__, "items": [self.value(value, scope=scope) for value in ordered]}
            if reference is not None:
                result["identity"] = reference
            return result
        finally:
            if reference is not None:
                self.ancestors.remove(identity)

    def _graph_definition(self, obj):
        data = _layout(obj, _GRAPH)
        identities, objects, active, nodes = {}, [], set(), {}

        def identity(value):
            number = identities.get(id(value))
            if number is None:
                number = len(identities)
                identities[id(value)] = number
                objects.append(value)
            return ["model_storage", number]

        def node_ref(node):
            ref = self._node(node)
            nodes[id(node)] = (node, ref)
            return ref

        def value(item):
            kind = type(item)
            if item is None or kind in (bool, int, float, str, bytes):
                return item
            if kind is graph.Node:
                return {"ref": node_ref(item)}
            if kind is graph.SyncVar:
                fields = _layout(item, _VALUE_LAYOUTS[kind])
                return {"class": _name(kind), "fields_identity": identity(fields),
                        "fields": [[name, value(nested)] for name, nested in fields.items()]}
            if kind not in (dict, list, tuple):
                raise BodyVMSnapshotError("unknown graph definition value")
            reference = identity(item) if kind in (dict, list) else None
            if reference is not None and id(item) in active:
                return {"ref": reference}
            if reference is not None:
                active.add(id(item))
            try:
                if kind is dict:
                    if any(type(key) not in (str, int) for key in item):
                        raise BodyVMSnapshotError("unknown graph definition mapping key")
                    children = [[key, value(nested)] for key, nested in item.items()]
                else:
                    children = [value(nested) for nested in item]
                row = {"kind": kind.__name__, "items": children}
                if reference is not None:
                    row["identity"] = reference
                return row
            finally:
                if reference is not None:
                    active.remove(id(item))

        definition = {"fields_identity": identity(data),
                      "fields": [[name, value(nested)] for name, nested in data.items()], "nodes": []}
        for node in data["nodes"]:
            fields = _layout(node, _NODE)
            definition["nodes"].append({
                "ref": node_ref(node), "fields_identity": identity(fields),
                "fields": [[name, value(nested)] for name, nested in fields.items()],
            })
        digest = self._definition_digest(obj, definition)
        return digest, objects, tuple(nodes.values())

    def _definition_digest(self, obj, definition):
        if self.definition_digests is None:
            return _digest(replay_values.encode(definition))
        # The definition is rebuilt and validated on every capture. Marshal v2 is
        # only an in-process equality check on plain values, without reference
        # tracking. The persisted digest still uses the canonical replay encoding.
        try:
            guard = marshal.dumps(definition, 2)
        except ValueError:
            return _digest(replay_values.encode(definition))
        key = (replay_values.MAX_DEPTH, replay_values.MAX_VALUES, guard)
        cached = self.definition_digests.get(obj)
        if cached is not None and cached[0] == key:
            return cached[1]
        digest = _digest(replay_values.encode(definition))
        self.definition_digests[obj] = (key, digest)
        return digest

    def _graph_record(self, obj, graph_definitions):
        # This cache belongs to one locked server snapshot. Component-local storage
        # identities are rebuilt, and every graph reference must be valid here too.
        cached = graph_definitions.get(obj)
        if cached is None:
            cached = graph_definitions[obj] = self._graph_definition(obj)
        else:
            for node, ref in cached[2]:
                if self._node(node) != ref:
                    raise BodyVMSnapshotError("shared graph node reference changed during capture")
        digest, objects, _ = cached
        bindings = [{"local": number, "storage": self.identity(value, "model")}
                    for number, value in enumerate(objects)]
        return {"ref": ["graph", obj.index], "class": _name(type(obj)),
                "fields_identity": bindings[0]["storage"], "storage_bindings": bindings,
                "definition_sha256": digest}

    def capture(self, extra_roots, script=None, *, graph_definitions):
        # Register graph ownership before a queue/state refers to one of its Nodes.
        for number, instance in self.component.instances.items():
            self._instance(instance)
            if type(number) is not int or number != instance.id:
                raise BodyVMSnapshotError("instance allocation mapping identity mismatch")
        for instance in (*self.component.instances.values(), *self.component.destroyed):
            self._instance(instance)
            self._graph(instance.graph)
        for root in extra_roots:
            if type(root) is runtime.Instance:
                self._instance(root)
                self._graph(root.graph)
            elif type(root) in _STATE_LAYOUTS:
                self._state(root)
                self._graph(root.instance.graph)
            elif type(root) is runtime.Var:
                self._var(root)
            else:
                raise BodyVMSnapshotError("extra root must be a known Instance, State or Var")
        result = {
            "component_fields_identity": self.identity(vars(self.component)),
            "component": self.fields(self.component, _COMPONENT), "objects": self.records,
            "extra_roots": [self.value(root) for root in extra_roots],
        }
        if script is not None:
            result["body_script_fields_identity"] = self.identity(vars(script))
            result["body_script"] = self.fields(script, _SCRIPT, _SCRIPT_OPTIONAL, exclude=TRANSPORT_FIELDS)
        cursor = 0
        while cursor < len(self.pending):
            kind, obj = self.pending[cursor]
            cursor += 1
            if kind == "instance":
                self._graph(obj.graph)
                for key, state in obj.states.items():
                    self._state(state)
                    if type(key) is not int or key != state.index:
                        raise BodyVMSnapshotError("state allocation mapping identity mismatch")
                record = {"ref": ["instance", obj.id], "class": _name(type(obj)),
                          "fields_identity": self.identity(vars(obj)),
                          "fields": self.fields(obj, _INSTANCE)}
            elif kind == "state":
                required, optional = _STATE_LAYOUTS[type(obj)]
                record = {"ref": ["state", obj.instance.id, obj.index], "class": _name(type(obj)),
                          "fields_identity": self.identity(vars(obj)),
                          "fields": self.fields(obj, _STATE | set(required), optional)}
            elif kind == "var":
                owner, number = obj.key
                table = self.component.vars if owner == 0 else self.instances.get(owner, None)
                table = table.vars if type(table) is runtime.Instance else table
                expected_owner = self.component if owner == 0 else None
                if type(table) is not dict or table.get(number) is not obj or obj.entity_owner is not expected_owner:
                    raise BodyVMSnapshotError("variable is outside its owner's storage")
                record = {"ref": ["var", *obj.key], "class": _name(type(obj)),
                          "fields": [[name, self._watch_map(obj.watchers, lambda state: self._var_watcher(obj, state))
                                      if name == "watchers" else self.value(object.__getattribute__(obj, name))]
                                     for name in _VAR]}
            else:
                record = self._graph_record(obj, graph_definitions)
            self.records.append(record)
        if len(self.entity_components) > 1:
            result["external_components"] = [
                {"identity": self.identity(owner), "entity": entity}
                for entity, owner in self.entity_components.items() if owner is not self.component
            ]
        return result


def _document(component, extra_roots, script=None, *, graph_definitions=None, definition_digests=None,
              external_reference=None):
    if type(extra_roots) not in (tuple, list):
        raise BodyVMSnapshotError("extra_roots must be a tuple or list")
    try:
        source = hashlib.sha256(graph.GRAPHS_PATH.read_bytes()).hexdigest()
        exporter = _Export(component, definition_digests, external_reference)
        raw = exporter.capture(extra_roots, script,
                               graph_definitions={} if graph_definitions is None else graph_definitions)
        document = {
            "schema": SCHEMA if script is not None else COMPONENT_SCHEMA,
            "scope": SCOPE if script is not None else COMPONENT_SCOPE,
            "graph_data_sha256": source, "value": replay_values.encode(raw),
        }
        return document, exporter.identities()
    except (OSError, replay_values.ReplayValueError, RecursionError) as error:
        raise BodyVMSnapshotError(f"VM storage export failed: {type(error).__name__}") from None


def export_component_vm(component, *, extra_roots=()) -> dict:
    """Export one Component namespace, including retired objects retained by extra roots."""
    return _document(component, extra_roots)[0]


def export_component_vm_with_identities(component, *, extra_roots=(), _graph_definitions=None,
                                        _definition_digests=None, external_reference=None) -> tuple:
    """Return one capture plus ephemeral (storage id, original object, scope tag) bindings.

    Scope tags are vm, model or vm+model. The sidecar is not serializable replay
    data; an outer adapter can detect unsupported sharing across artifact scopes.
    The optional outer encoder supplies references to registered enclosing storage.
    No gameplay function is invoked.
    """
    return _document(component, extra_roots, graph_definitions=_graph_definitions,
                     definition_digests=_definition_digests, external_reference=external_reference)


def export_body_vm(script) -> dict:
    if type(script) is not BodyScript:
        raise BodyVMSnapshotError("unknown body script class")
    data = _layout(script, _SCRIPT, _SCRIPT_OPTIONAL)
    if data["world"] is not data["component"].world or data["entity"] != data["component"].entity:
        raise BodyVMSnapshotError("body script component/world identity mismatch")
    return _document(data["component"], data["ultimates"], script)[0]


def snapshot_digest(document) -> str:
    """Validate the typed envelope and hash it; no live object is inspected."""
    if type(document) is not dict or set(document) != {"schema", "scope", "graph_data_sha256", "value"}:
        raise BodyVMSnapshotError("invalid VM snapshot fields")
    scopes = {SCHEMA: SCOPE, COMPONENT_SCHEMA: COMPONENT_SCOPE}
    if type(document["schema"]) is not str or document["schema"] not in scopes:
        raise BodyVMSnapshotError("unknown VM snapshot schema")
    if document["scope"] != scopes[document["schema"]]:
        raise BodyVMSnapshotError("invalid VM snapshot scope")
    source = document["graph_data_sha256"]
    if type(source) is not str or len(source) != 64 or any(c not in "0123456789abcdef" for c in source):
        raise BodyVMSnapshotError("invalid graph source signature")
    try:
        replay_values.decode(document["value"])
    except replay_values.ReplayValueError:
        raise BodyVMSnapshotError("invalid typed VM snapshot") from None
    return _digest(document)
