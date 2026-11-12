"""Read-only stored domain graphs for controlled GameServer replay.

External clocks, collision providers, sockets and lobby callbacks are named I/O
boundaries, verified by the journal's tapes/effects. This is not a restorable
checkpoint, live thread snapshot or a claim of original server mechanics.
"""

import dataclasses
import hashlib
import json
import math
import random
import struct
import types
import weakref
from collections import deque
from pathlib import Path

from ow174.game import (
    absorption,
    actors,
    baptiste,
    barrier,
    assassin_victim,
    barriers,
    breather,
    bots,
    brigitte,
    combat,
    correction,
    dynamic_spheres,
    echo_duplicate,
    genji,
    deflection,
    earthshatter,
    healing,
    graviton,
    health_packs,
    heroselect,
    mei_wall,
    moira,
    notices,
    npc_navigation,
    observers,
    orisa,
    orisa_barrier,
    orisa_halt,
    orisa_supercharger,
    photon_barrier,
    projectiles,
    props,
    reinhardt_barrier,
    reinhardt_charge,
    replay_values,
    sigma_barrier,
    statescript,
    stats,
    tracer_recall,
    pulse_bomb,
    zenyatta,
    voicelines,
    winston_barrier,
    world,
    zarya_barriers,
)
from ow174.game.script import fold, scopes, support_effects
from ow174.game import doomfist, meteor, knockback, lucio, sombra_control, sound_barrier
from ow174.game.bits import BitWriter
from ow174.game.collision import LoadState, World
from ow174.game.collision_fingerprint import WorldRegistry
from ow174.game.commands import CommandQueue
from ow174.game.content import GameMap, Hero, Spawn
from ow174.game.link import LinkCipher, ReceiveWindow
from ow174.game.match import Match, Player, RetainedFlight, ShownController
from ow174.game.mover import Mover, MoverData, loaded_mover_table
from ow174.game.script import body_owner, driver, entity_link, entity_queries, expr, play, runtime, spatial_queries
from ow174.game.script import graph as script_graph
from ow174.game.script.bot import AssassinControllerWorld, BotScript, BotWorld
from ow174.game.server import Client, GameServer, Handoff, _BotScriptDelivery
from ow174.paths import ROOT

SCHEMA = "ow174.controlled-server-stored-state.v1"
SCOPE = "registered-domain-fields-with-explicit-io-boundaries"
MAX_OBJECTS = 200_000
MAX_VALUES = 4_000_000


class ServerSnapshotError(RuntimeError):
    pass


def _fail(reason):
    raise ServerSnapshotError(reason)


def _json(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=True, allow_nan=False, separators=(",", ":"))


def _names(text):
    return frozenset(text.split())


# Exact registered classes. Field names are explicit: a newly introduced runtime
# attribute must be reviewed, rather than silently omitted from a passing hash.
FIELDS = {
    scopes.ForwardHost: _names("states component frame history dirty _processing _drain_depth"),
    GameServer: _names("environment collision_provider_factory scene_host_factory host port "
                       "on_leave on_join on_clock_ready "
                       "skin_of emote_aliases_of emote_blend_trees_of wheel_cosmetics_of sock lock handoffs clients _dropped matches tick _next_conn _left_players "
                       "_joined_players _reporting_players _clock_ticks_left _clock_ready_pending "
                       "_sent_home running"),
    Client: _names("server address conn cipher player window seq open closed heard next_reliable "
                   "_ownership_ordinal reliable "
                   "unreliable entities in_flight _combat_notices _body_health _combat_notice_tail "
                   "_body_destroy_wait _player_body_reuse_after _bot_scripts expected_in parked_in "
                   "leaving_since _own_controller_acked _mode_create_acked _pending_mode_frame "
                   "_mode_fragments _display_notices _pending_possessions _confirmed_possessions _npc_idle"),
    _BotScriptDelivery: _names("stream source live volleys death_frames death_snapshot nameplate_update historical_death"),
    Match: _names("environment id game_map card skin_of emote_aliases_of emote_blend_trees_of wheel_cosmetics_of tick controller assemble_ends assembled players "
                  "_body_messages body_components started on_leave ended bots _pending_bot_scripts bots_shown notices combat "
                  "dynamic_spheres collision_provider collision_state collision_world _collision_failed "
                  "scene_host_factory scene_host scene_host_started scene_host_error _npc_planning_turn"),
    Player: _names("match slot account_lo name rank role card card_sent card_again hero team quanta entity "
                   "body has_body skin client gone loaded_at steps_done spawned spawn mover commands "
                   "body_script controlled_mech body_script_failed genji_mobility seen shown script mode_script "
                   "next_countdown select_open controller_hero "
                   "stats stats_sent next_stats"),
    ShownController: _names("script create_acked pick card_again possession"),
    CommandQueue: _names("last_frame epoch wraps"),
    combat.Combat: _names(
        "match vitals history replacement_ticks input_locks booster_contacts rockets pending_attacks timed_hits recalls breathers sombra_controls sound_barriers lucio_auras knockbacks soundwaves support_effects health_packs health_pack_digest moira_orbs moira_clocks moira_hots photon_barriers zarya_gravitons respawns next_bot now tick melee_seen tesla_frames "
        "model_pose_for wheel_actor_admits defender_damage_scale "
        "pulse_bombs zenyatta projectiles observers voice stuns npc_hit_reactions pins deadeye_notices brigitte personal_barriers "
        "punch_impacts punches slams meteors projected_barriers projected_casts sigma_barriers charge_contacts "
        "reinhardt_geometry reinhardt_barriers winston_domes orisa_barriers walls echo_beam_seen reaper_blossoms reaper_shadows echo_duplicates barriers "
        "genji_strikes genji_blades genji_assists frost_hits next_barrier assassin_victims ability_impacts orisa baptiste deployables healing projectile_tags absorption barrier shatters charges"
    ),
    doomfist.Punches: _names("combat dashes impacts controls"),
    doomfist.Slams: _names("combat casts"),
    meteor.Meteors: _names("combat casts"),
    tracer_recall.Recalls: _names("fight history phases"),
    pulse_bomb.PulseBombs: _names("combat anchors poses context"),
    zenyatta.Zenyatta: _names("combat owners last_tick"),
    breather.Breathers: _names("fight channels"),
    sombra_control.Controls: _names("fight locks held device_receivers"),
    sound_barrier.SoundBarriers: _names("fight landings waves shielded seen pending"),
    lucio.Auras: _names("combat leases healing last_tick"),
    knockback.Knockbacks: _names("combat serial active"),
    support_effects.Effects: _names("match attachments boost_frames"),
    support_effects.EffectWorld: _names("hero mode match source owner"),
    sombra_control.DeviceWorld: _names("hero mode fight body tags"),
    combat.WaveHealthPool: _names("capacity current"),
    orisa_barrier.Barriers: _names("fight geometry active retiring pending"),
    orisa_barrier.BarrierWorld: _names("hero mode match entity location"),
    sigma_barrier.BarrierWorld: _names("hero mode barrier"),
    health_packs.PackWorld: _names("hero mode match entity"),
    orisa_barrier.Geometry: _names("world triangles source"),
    mei_wall.Walls: _names("combat pieces inputs"),
    combat.Vitals: _names(
        "entity most now ids wave_health wave_health_id changed died_at death_tick killer bonus_armour bonus_declared "
        "healing_block healing_boost temporary_shield ult_healing_deficit extra next_pool sleep_until sleep_wake_floor sleep_view nano_until invulnerable_until adaptive_activation conditional_pools"),
    brigitte.BrigitteCombat: _names(
        "combat hits inspire next_inspire whip_seen whips repair_seen repair_casts repair_deliveries "
        "repairs barriers rally_seen stuns"
    ),
    echo_duplicate.Duplicates: _names("combat states held busy"),
    barriers.Barriers: _names("fight live scripted seen next_entity world reinhardt"),
    barriers.reinhardt_barrier.ReinhardtBarriers: _names("fight geometry actors next_id"),
    barriers.world_bubbles.WorldBubbles: _names("fight actors next_id pending"),
    combat.TemporaryPool: _names("identifier kind slot pool_id most now last_tick removed"),
    barriers.Barrier: _names(
        "entity parent parent_body source instance state activation component "
        "mesh geometry_digest vitals retired recipient recipient_body recipient_component"),
    assassin_victim.Lease: _names(
        "source source_instance sender_state activation target source_component target_component instance"),

    dynamic_spheres.DynamicSpheres: _names("_bodies"),
    orisa_halt.PullQuery: _names("hero mode explosion_sink origin caster context instance track radius"),
    orisa.Orisa: _names("fight charge_metadata halt_graph halt_query_graph orbs pulls results "
                        "barriers superchargers"),
    orisa_supercharger.Superchargers: _names("fight active buffs graphs geometry"),
    orisa_supercharger.DeviceWorld: _names("hero mode fight entity location"),
    script_graph.Graph: _names("index header fields items containers nodes unknown_rids _by_pos _by_rid "
                               "states entries remote_nodes sync_vars _owner _remote _presence"),
    script_graph.Node: _names("pos cls uid rid remote state client_only server_only fields"),

    baptiste.BaptisteEffects: _names(
        "combat seen regeneration protection_seen fields field_bindings matrices"
    ),
    healing.HealingEffects: _names("combat spec anti amp"),
    absorption.Grasp: _names("combat converted last_tick"),
    barrier.BarrierField: _names("combat health children model_sha256"),
    graviton.Gravitons: _names("fight fields pulled npc_movers"),
    winston_barrier.DomeWorld: _names("hero mode manager actor"),
    photon_barrier.Barriers: _names("fight geometry actors casts"),
    photon_barrier.Geometry: _names("world triangles source"),
    photon_barrier.BarrierWorld: _names("hero mode actor"),
    projectiles.Projectiles: _names("combat match shots observed next_field next_seen"),
    projectiles.FieldArrived: _names("shot viewer stream done"),
    observers.Observers: _names("match watches"),
    observers.Watch: _names("body arrived stream sent next_lazy"),
    observers._CreateArrived: _names("watch"),
    voicelines.VoiceLines: _names("match pending"),
    notices.Notices: _names("match entered waiting notice_chunk sent_at sending"),
    stats.PlayerStats: _names("values"),
    statescript.Stream: _names("entity last delivered data_last cmfd next_ack"),
    driver.BodyStream: _names(
        "entity last cmfd base base_last pending sent_reports lost_keys latest_lost full_update initial_owner"
    ),
    driver._CreateArrived: _names("script"),
    driver.BodyScript: _names(
        "hero entity record frame_ms world component frame buttons stream create_arrived "
        "retired first_owner_frame world_frame_anchor server_only ult_seconds passive_charge_ms reaper_charge_ms reaper_charge_tick ult_gain_bonus ultimates reports report_count self_healing support_healing "
        "self_repairs control_windows sleep_windows frost_windows"
    ),
    driver.BodyWorld: _names("hero mode body body_definition throttles match zenyatta_charge_ammo communication_wheel velocity_requests wheel_callouts emote_aliases emote_blend_trees spray_unlocks voice_effects "
                             "_body_owner_context _entity_query_context"),
    BotScript: _names(
        "component frame server_only latest_live _initial_frame death_frame _produced behavior controller "
        "flashbang_weapon_stack"
    ),
    BotWorld: _names(
        "hero mode weapon_buttons owner_position controller component controller_inputs "
        "heavy_counter_stuns match controller_components manual_target manual_nameplate"
    ),
    AssassinControllerWorld: _names(
        "hero mode mover body_entity match button_edges button_holders movement_bits candidates "
        "recovery_pending recovery_started"
    ),
    entity_link.Context: _names("owner instance_present environment_present lookup watch_environment watch_link retired"),
    spatial_queries.Context: _names("owner instance_present namespace_present query retired"),
    body_owner.Context: _names("owner instance_present environment_present lookup watch_environment "
                               "watch_owner retired"),
    entity_queries.Context: _names("owner namespace_present environment_present possession_context_present "
                                   "lookup watch_environment watch_possession retired lookup_linked lookup_participant"),
    runtime.World: _names("hero mode"),
    LinkCipher: _names("key_in key_out salt_in salt_out"),
    ReceiveWindow: _names("expected bits"),
    world.EntityUpdate: _names(
        "entity op build movement chunk chunk_last ch2 stream resend correction controller_close npc_idle"
    ),
    BitWriter: _names("value count"),
    world.RecordWriter: _names("value count origin"),
    world.Movement: _names("position yaw pitch velocity flags throttles input_frame input_throttles "
                           "air_ticks gravity frame crouch_frame spring spring_speed fall"),
    bots.Bot: _names(
        "entity spawner hero_data hints dead body_script records_from hero name body team health speed accel "
        "brake pace direction position yaw velocity firing route index wait target start keys turning held "
        "last_held input_tick moved_tick last_tick retain_corpse attack_target attack_input attack_mode pitch navigation impulse_mover "
        "owner pet_owner source_body control_mover control_buttons clock_offset detached_bomb"
    ),
    npc_navigation.Search: _names("start target queue points parents costs visited done path blocked_by_teammate"),
    npc_navigation.Navigator: _names("mover path target goal next_plan last_tick stuck search"),
    npc_navigation.LedgeDrop: _names("started released"),
}
OPTIONAL_FIELDS = {
    BotWorld: _names("body _body_owner_context _entity_query_context _spatial_query_context _entity_link_context pool_profile"),
    driver.BodyWorld: _names("scope_host pending_ult_credits historical_boost_award advancing_valkyrie_clock _spatial_query_context"),
    driver.BodyScript: _names("passive_ult_deadline passive_ult_last_command_tick"),
    knockback.Knockbacks: _names("pending_npc"),
    Client: _names("_entity_lifecycles _entity_record_generations _frame_budget_warning _last_hit_notice_frame"),
    Player: _names("impulse_correction valkyrie_pose"),
    npc_navigation.Navigator: _names("charge_contact"),
    bots.Bot: _names("wall_mover pursuit assassin_controller enforcer_controller"),
}
DATACLASSES = (
    combat.ConditionalPool,
    fold.Scope,
    doomfist.Dash, doomfist.Impact, doomfist.Slam,
    meteor.Cast,
    combat.InputLock,
    zenyatta.Owner, zenyatta.Orb,
    pulse_bomb.CommandPoseAnchor,
    support_effects.Attachment, combat.Soundwave, sombra_control.Lock, sound_barrier.Shield, sound_barrier.Landing, sound_barrier.Wave, sound_barrier.Grant, lucio.Lease,
    driver.WheelCallout, driver.WheelSprayEvent,
    GameMap, Hero, Spawn, Handoff, LoadState, correction.SendToken, MoverData,
    heroselect.Controller, notices.Switch, combat.RangedContact, combat.MeleeContact, combat.PendingAttack, combat.HitDef, combat.Target, combat.Rocket, pulse_bomb.Bomb, combat.SplitVolley, combat.TimedHit,
    combat.PunchImpact, combat.ChargeContact, reinhardt_barrier.Geometry, reinhardt_barrier.Barrier,
    winston_barrier.Dome, orisa_barrier.Barrier, mei_wall.Pillar, mei_wall.Input,
    npc_navigation.Pursuit, tracer_recall.Sample, tracer_recall.Phase, breather.Channel, health_packs.HealthPack,
    combat.TemporaryArmour, combat.ProjectedBarrier, combat.TemporaryShield, sigma_barrier.Barrier,
    brigitte.InspireHeal, brigitte.RepairHeal, brigitte.WhipFlight, brigitte.RepairCast,
    brigitte.RepairDelivery, brigitte.Barrier,
    brigitte.BashStun,
    echo_duplicate.Copy, barriers.Bubble, barriers.world_bubbles.FixedBubble,
    reinhardt_barrier.RaisedBarrier,
    genji.Mobility, combat.Frost,
    earthshatter.Wave, reinhardt_charge.Charge,
    actors.Deployable, actors.PillarScript, baptiste.FieldSpec, baptiste._Field, baptiste._Regeneration,
    healing.Spec, healing.Modifier,
    deflection.ProjectileTags,
    moira.Orb, moira.SprayHeal, moira.SprayTrace, graviton.Field, photon_barrier.Shield,
    projectiles.Shot, projectiles.ShotRecord, projectiles._ObservedProjectile, projectiles._ProjectileArrival,
    RetainedFlight, scopes.History, scopes.Edge,
    driver.Snapshot, driver.OwnerReport, statescript.Graph,
    statescript.Bool, statescript.Int, statescript.Float, statescript.Asset, statescript.Entity,
    statescript.Instance, statescript.Binding, statescript.StateWire, statescript.EventWire,
    statescript.InstanceWire, expr.Asset, expr.Entity, expr.Vec3, expr.Handle, expr.Map, expr.PairArray,
    dynamic_spheres.SphereBody, props.PlacedDynamicSphere, play.InvocationContext,
    orisa.Orb, orisa.Pull, orisa_supercharger.Device, script_graph.SyncVar,
)


class _Graph:
    def __init__(self, boundaries, assets, definition_digests):
        self.boundaries = {}
        for name, obj in boundaries.items():
            if obj is None:
                continue
            existing = self.boundaries.get(id(obj))
            if existing is not None and existing[1] != name:
                _fail("one external object has conflicting boundary names")
            self.boundaries[id(obj)] = (obj, name)
        self.assets = assets
        self.definition_digests = definition_digests
        self.asset_descriptors = {}
        self.asset_worlds = {}
        self.objects = []
        self.memo = {}
        self.nodes = []
        self.weak_nodes = []
        self.vm_nodes = []
        self.vm_extra = {}
        self.physics_owners = {}
        self.values = 0

    def value(self, obj):
        self.values += 1
        if self.values > MAX_VALUES:
            _fail("stored graph value budget exhausted")
        if obj is NotImplemented:
            return ["sentinel", "NotImplemented"]
        if obj is None or type(obj) in (bool, int, float, str, bytes):
            return replay_values.encode(obj)
        boundary = self.boundaries.get(id(obj))
        if boundary is not None and boundary[0] is obj:
            return ["boundary", boundary[1]]
        from ow174.game.body_vm_snapshot import _STATE_LAYOUTS

        if any(type(obj) is cls for cls in _STATE_LAYOUTS):
            component = obj.instance.component
            self.vm_extra.setdefault(id(component), []).append(obj)
            return ["vm_state", self.value(component), obj.instance.id, obj.index]
        if type(obj) is runtime.Instance:
            component = obj.component
            self.vm_extra.setdefault(id(component), []).append(obj)
            return ["vm_instance", self.value(component), obj.id]
        if type(obj) is tuple:
            return ["tuple", [self.value(item) for item in obj]]
        identity = id(obj)
        found = self.memo.get(identity)
        if found is not None:
            if self.objects[found] is not obj:
                _fail("stored graph identity conflict")
            return ["ref", found]
        if len(self.objects) >= MAX_OBJECTS:
            _fail("stored graph object budget exhausted")
        number = len(self.objects)
        self.memo[identity] = number
        self.objects.append(obj)
        self.nodes.append(None)
        return ["ref", number]

    def _asset(self, obj):
        existing = self.asset_worlds.get(id(obj))
        if existing is not None:
            return existing
        descriptor = self.assets.register(obj)
        key = _json(descriptor)
        self.asset_descriptors[key] = descriptor
        self.asset_worlds[id(obj)] = descriptor
        return descriptor

    def _fields(self, obj, required, optional=frozenset()):
        actual = object.__getattribute__(obj, "__dict__")
        names = set(actual)
        if not required <= names or names - required - optional:
            _fail(f"unregistered stored fields on {type(obj).__name__}")
        # Preserve attribute-dictionary identity and insertion order as well as the
        # reviewed layout. Two objects can otherwise share all future attribute writes.
        return {"fields": sorted(actual), "storage": self.value(actual)}

    def _code(self, code):
        path = Path(code.co_filename).resolve()
        try:
            relative = path.relative_to(ROOT.resolve()).as_posix()
        except ValueError:
            _fail("builder function is outside the pinned source model")
        if not relative.startswith("ow174/game/"):
            _fail("unregistered builder code source")
        constants = []
        for value in code.co_consts:
            constants.append(self._constant(value))
        return {
            "source": relative, "name": code.co_name, "qualname": code.co_qualname,
            "first_line": code.co_firstlineno, "argcount": code.co_argcount,
            "posonlyargcount": code.co_posonlyargcount, "kwonlyargcount": code.co_kwonlyargcount,
            "nlocals": code.co_nlocals, "stacksize": code.co_stacksize, "flags": code.co_flags,
            "bytecode": code.co_code.hex(), "constants": constants, "names": list(code.co_names),
            "varnames": list(code.co_varnames), "freevars": list(code.co_freevars),
            "cellvars": list(code.co_cellvars), "linetable": code.co_linetable.hex(),
            "exceptiontable": code.co_exceptiontable.hex(),
        }

    def _constant(self, value):
        if type(value) is types.CodeType:
            return ["code", self._code(value)]
        if type(value) is tuple:
            return ["tuple", [self._constant(item) for item in value]]
        if type(value) is frozenset:
            return ["frozenset", sorted((self._constant(item) for item in value), key=_json)]
        if value is Ellipsis:
            return ["ellipsis"]
        if type(value) is float and not math.isfinite(value):
            # Python code may contain an overflowed literal. It is code metadata,
            # not permission for a nonfinite simulation field or typed root value.
            return ["code_float_bits", struct.pack(">d", value).hex()]
        return ["value", replay_values.encode(value)]

    def _function(self, obj):
        if not obj.__module__.startswith("ow174.game."):
            _fail("unregistered builder function module")
        if vars(obj):
            _fail("builder function has unregistered mutable attributes")
        free = []
        for name, cell in zip(obj.__code__.co_freevars, obj.__closure__ or (), strict=True):
            try:
                value = cell.cell_contents
            except ValueError:
                _fail("builder function has an empty closure cell")
            free.append([name, self.value(value)])
        return {
            "module": obj.__module__, "qualname": obj.__qualname__, "code": self._code(obj.__code__),
            "defaults": self.value(obj.__defaults__), "kwdefaults": self.value(obj.__kwdefaults__),
            "closure": free,
        }

    def build(self, number):
        obj, kind = self.objects[number], type(self.objects[number])
        label = f"{kind.__module__}.{kind.__qualname__}"
        if kind is list:
            data = [self.value(value) for value in obj]
        elif kind is dict:
            data = [[self.value(key), self.value(value)] for key, value in obj.items()]
        elif kind is deque:
            data = {"maxlen": obj.maxlen, "items": [self.value(value) for value in obj]}
        elif kind in (set, frozenset):
            # Registered game sets here are membership sets of immutable plain keys.
            # Causal VM watcher/state sets are handled by the component adapter.
            encoded = [replay_values.encode(value) for value in obj]
            data = sorted(encoded, key=_json)
        elif kind in (weakref.WeakKeyDictionary, weakref.WeakSet):
            values = list(obj.items()) if kind is weakref.WeakKeyDictionary else list(obj)
            self.weak_nodes.append((number, kind, values))
            data = []
        elif kind is types.MethodType:
            data = {"self": self.value(obj.__self__), "function": self.value(obj.__func__)}
        elif kind is types.FunctionType:
            data = self._function(obj)
        elif kind is World:
            data = {"asset": self._asset(obj)}
        elif kind is Mover:
            from ow174.game.commands import Command
            from ow174.game.mover import Snapshot
            from ow174.game.physics_snapshot import snapshot_mover_with_identities

            physics, identities = snapshot_mover_with_identities(
                obj, world_descriptor=self._asset(obj.world)
            )
            for _, owned in identities:
                if owned is obj or type(owned) in (tuple, MoverData, Command, Snapshot):
                    continue
                previous = self.physics_owners.get(id(owned))
                if previous is not None and previous[0] is owned and previous[1] is not obj:
                    _fail("mutable physics state shared across Mover artifacts is unsupported")
                self.physics_owners[id(owned)] = (owned, obj)

            data = {
                "world": self.value(obj.world), "data": self.value(obj.data),
                "values": self.value(obj.values), "physics": physics,
            }
        elif kind is runtime.Component:
            # Extra refs may be discovered later through callbacks/voice pending work.
            self.vm_nodes.append(number)
            data = {"world": self.value(obj.world), "rng": self.value(obj.rng),
                    "sequence": self.value(obj.seq), "component": None}
        elif kind is runtime.EventSequence:
            if tuple(kind.__slots__) != ("_next_value",):
                _fail("unregistered event sequence storage")
            data = {"next_value": replay_values.encode(object.__getattribute__(obj, "_next_value"))}
        elif kind is random.Random:
            data = {"fields": self._fields(obj, frozenset(("gauss_next",))),
                    "rng_state": replay_values.encode(random.Random.getstate(obj))}
        elif kind in FIELDS:
            data = self._fields(obj, FIELDS[kind], OPTIONAL_FIELDS.get(kind, frozenset()))
        elif kind in DATACLASSES:
            fields = frozenset(field.name for field in dataclasses.fields(kind))
            data = self._fields(obj, fields)
        else:
            _fail(f"unregistered stored domain type: {label}")
        self.nodes[number] = {"id": number, "type": label, "state": data}

    def finish(self):
        index = 0
        while index < len(self.objects):
            self.build(index)
            index += 1
        from ow174.game.body_vm_snapshot import export_component_vm_with_identities

        owners = {}
        graph_definitions = {}
        for number in self.vm_nodes:
            component = self.objects[number]
            extras = self.vm_extra.get(id(component), ())
            document, identities = export_component_vm_with_identities(
                component, extra_roots=extras, _graph_definitions=graph_definitions,
                _definition_digests=self.definition_digests,
            )
            bridges = []
            for storage_id, obj, scope in identities:
                core = self.memo.get(id(obj))
                if core is not None and self.objects[core] is obj:
                    bridges.append([storage_id, ["ref", core]])
                    continue
                old = owners.get(id(obj))
                if old is not None and old[0] is obj and old[1] != number:
                    if scope != "model" or old[2] != "model":
                        _fail("private mutable VM storage shared across Component artifacts is unsupported")
                else:
                    owners[id(obj)] = (obj, number, scope)
            self.nodes[number]["state"]["component"] = document
            self.nodes[number]["state"]["storage_bridges"] = bridges
        # Weak lookup entries without another strong domain reference cannot affect
        # later queue/ACK decisions. Do not retain them to manufacture a new root.
        for number, kind, values in self.weak_nodes:
            entries = []
            if kind is weakref.WeakKeyDictionary:
                for key, value in values:
                    target = self.memo.get(id(key))
                    if target is not None and self.objects[target] is key:
                        entries.append([target, replay_values.encode(value)])
                entries.sort(key=lambda row: row[0])
            else:
                for value in values:
                    target = self.memo.get(id(value))
                    if target is not None and self.objects[target] is value:
                        entries.append(target)
                entries.sort()
            self.nodes[number]["state"] = entries
        return self.nodes


def export_server_state(server, *, archives=(), collision_provider=None, assets=None, _definition_digests=None):
    """Export all registered stored fields; unknown fields/types fail instead of being skipped.

    Only stopped controlled servers are accepted. Every match must use the same
    explicitly supplied external collision boundary. Asset descriptors are checked
    at registration; a journal must also revalidate the static assets at its end.
    The returned document includes private key material: keep it in memory.
    """
    if type(server) is not GameServer or server.running:
        _fail("stored exporter requires a stopped controlled GameServer")
    if server.scene_host_factory is not None:
        _fail('shared scene script runtime storage is not implemented')
    archives = tuple(archives)
    matches = list(server.matches) + list(archives)
    for match in matches:
        if type(match) is not Match:
            _fail("unregistered archived match type")
        if match.scene_host_factory is not None:
            _fail('shared scene script runtime storage is not implemented')
        if (match.environment is not server.environment or match.skin_of is not server.skin_of
                or match.emote_aliases_of is not server.emote_aliases_of
                or match.emote_blend_trees_of is not server.emote_blend_trees_of
                or match.wheel_cosmetics_of is not server.wheel_cosmetics_of):
            _fail("match external boundaries differ from its controlled server")
        if collision_provider is None or match.collision_provider is not collision_provider:
            _fail("match has an unregistered external collision provider")
    boundaries = {
        "environment": server.environment, "socket": server.sock, "domain_lock": server.lock,
        "emotes": server.emote_aliases_of, "emote_blend_trees": server.emote_blend_trees_of, "wheel_cosmetics": server.wheel_cosmetics_of,
        "skin": server.skin_of, "joined_callback": server.on_join, "left_callback": server.on_leave,
        "clock_ready_callback": server.on_clock_ready,
        "collision_factory": server.collision_provider_factory, "collision_provider": collision_provider,
    }
    graph = _Graph(boundaries, WorldRegistry() if assets is None else assets, _definition_digests)
    with server.lock:
        roots = {"server": graph.value(server), "archived_matches": graph.value(archives),
                 "canonical_movers": graph.value(loaded_mover_table())}
        nodes = graph.finish()
    return {
        "schema": SCHEMA, "scope": SCOPE, "restorable": False,
        "roots": roots, "nodes": nodes,
        "assets": [graph.asset_descriptors[key] for key in sorted(graph.asset_descriptors)],
    }


def snapshot_digest(document):
    """Digest a detached stored graph without evaluating any gameplay object."""
    names = {"schema", "scope", "restorable", "roots", "nodes", "assets"}
    if (type(document) is not dict or set(document) != names or document["schema"] != SCHEMA
            or document["scope"] != SCOPE or document["restorable"] is not False):
        _fail("invalid stored server snapshot schema")
    if type(document["nodes"]) is not list:
        _fail("invalid stored node table")
    for index, node in enumerate(document["nodes"]):
        if (type(node) is not dict or set(node) != {"id", "type", "state"}
                or type(node["id"]) is not int or node["id"] != index or type(node["type"]) is not str):
            _fail("invalid stored node identity")
    return hashlib.sha256(_json(document).encode("utf-8")).hexdigest()
