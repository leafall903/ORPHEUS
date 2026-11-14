"""Predicted projectiles: what the server sends so that the owner's own Helix Rocket and Biotic Field canister
live, explode and leave their field (addresses are the PC client's, build 104319).

The lease:
- The owner's client shoots a projectile volley itself only under a lease on its body: component 90
  (MECPredictor, accessor 0x7FF789AE5B90: word 1 bit 26), field m_leaseBlocks (type 0x7FF78BFE9200, +120,
  owner-only flag 0x10), an array of LeaseBlock {u64 definition, u32 start, u32 stop, u32 startEntityID,
  u8 perFrame} (type 0x7FF78BFE9140). The allocator (0x7FF789AE59D0) takes the first block of the
  projectile's definition with start <= f < stop for command frame f and gives the id 0xA0000000 |
  startEntityID + (f - start) * perFrame (one more per further projectile of that frame). No block, no
  projectile. On the wire the array is an aligned u32 count and 21 bytes per block.
- Each body gets its own id range per definition (slot, definition), so no two bodies ever share an id, and
  ids move on with the frame, so a new body never repeats a recent one (the client ignores a destroyed id
  for 30 s).

Confirmation (0x7FF78969D4CD):
- The predicted projectile stays only if the server confirms it: a create record of the same id sets its
  component 91 +28 = 1 and changes nothing else (a create for an id the client does not have yet makes an
  unflown entity at the record's place, and the client's own shot of that id is then refused). So the
  creates go out only once the client has acknowledged the owner frame that reported the volley (the
  client shoots the rocket at the frame of that owner frame, K, or at the press, f): lease(K) and
  lease(f).

The release (reader chain: ch2 0x7FF789A4A490 -> state 0x7FF789A34600 -> record 0x7FF789C78980):
- The client holds a networked rocket's explosion back for the server (072C, 1C86:
  m_delayCollisionEffectsBasedOnLatency). A ch2 projectile state for a frame ahead of the rocket's, alive,
  with an impact count, in the same entity block as the destroy, makes the destructor play it at the
  client's own impact point. So the server destroys a rocket a little after it would hit (combat.py flies
  it) with that state, once the client's create confirmation has arrived.
"""

import logging
import math
from dataclasses import dataclass, field, replace
from functools import cache

from ow174.game import bots, hanzo_projectiles, pharah, world
from ow174.game.bits import BitWriter
from ow174.game.commands import FRAME_MASK
from ow174.game.script import expr, nodes
from ow174.game.script import graph as graphs_data
from ow174.game.script.driver import OwnerReport, Snapshot
from ow174.game.statescript import InstanceWire, StateWire, Stream, remote_full_frame
from ow174.game.world import OP_CREATE, OP_DESTROY, WITH_SCALE, WITH_TRANSFORM, EntityUpdate

log = logging.getLogger("ow174.game")


def unconditional_gravity(state) -> float | None:
    """Resolve one unconditional E1A5B579 motion from the firing volley."""
    motions = state.node.field('m_projectileMotions') or ()
    if (len(motions) != 1 or motions[0].get('$') != 'STU_E1A5B579'
            or motions[0].get('m_D0C4CF49') is not None):
        return None
    cfg = motions[0].get('m_599AB8D7')
    return expr.f32(expr.to_float(state.evaluate(cfg, watch=False))) if cfg is not None else None

NO_BASELINE = 0xFFFFFFFF  # the state's baseline frame: none, a blank state (0x7FF789A348DD)
RELEASE_AHEAD = 4096  # frames: the release's frame, far past the rocket's own
RELEASE_POS = (0.0, -1000.0, 0.0)  # where a count above the client's would play one more explosion


def _count(out: BitWriter, value: int) -> None:
    """1 + 4 bits, 01 + 8 bits, 00 + 32 bits."""
    if value < 16:
        out.bit(1)
        out.bits(value, 4)
    elif value < 256:
        out.bit(0)
        out.bit(1)
        out.bits(value, 8)
    else:
        out.bit(0)
        out.bit(0)
        out.bits(value & 0xFFFFFFFF, 32)


def state_record(out: BitWriter, position=RELEASE_POS, count: int = 1) -> None:
    """A projectile state record against a blank baseline (0x7FF789C78980): alive (+4 = 0), no +260, the
    position (+16), then every optional part the blank baseline's, but the impact count (+256)."""
    out.bit(0)  # +4: alive
    out.bit(0)  # no +260
    for value in position:
        out.f32(value)
    out.bit(0)  # velocity: the baseline's
    out.bit(0)  # +88
    out.bit(0)  # list +96
    out.bit(0)  # list +176
    out.bit(0)  # list +208
    out.bit(1)  # the impact count
    _count(out, count)
    out.bit(0)  # hits +264
    out.bit(0)  # S
    out.bit(0)  # +368 / +372
    out.bit(0)  # +432
    out.bit(0)  # +436
    out.bit(0)  # no tail


def release(frame: int, count: int = 1) -> BitWriter:
    """ch2 after its channel bit: no shot records, one projectile state for `frame` from no baseline."""
    out = BitWriter()
    out.bit(0)  # no events
    out.bit(1)  # a projectile state
    out.var_b(NO_BASELINE)
    out.var_b((frame + 1) & 0xFFFFFFFF)  # the frame is baseline + this (u32)
    state_record(out, RELEASE_POS, count)
    return out


# --- the field entity's create (the Biotic Field) ---------------------------------------------------------

KEEP_ROTATION = 0x01000000  # movement state +0 bit 24: the block's own rotation, not the parent's


# --- the server's part ------------------------------------------------------------------------------------

PREDICTED = 1  # a volley's sync after LET_GAME_DECIDE: SHOTS_AND_PROJECTILES, the lease's kind
SHOTS_ONLY = 2
MAX_PELLETS = 64  # limit the server's collision work for one authored shot
FRAME_MS = 16
LEASE_FIRST = 0x00100000  # the first lease id; the server's own ids stay below
LEASE_SPAN = 1 << 21  # frames (= ids) of one window: 9.3 hours
LEASE_WINDOWS = 2
# Keep the original four block bases per slot. Use the upper half of
# the first block for the fifth definition, with the same window length.
LEASE_BLOCK_STRIDE = 2 * LEASE_SPAN
LEASE_SLOT_STRIDE = 4 * LEASE_BLOCK_STRIDE
LEASE_SLOTS = 32  # slot 31's final window ends below the 29-bit entity index limit
DEFINITIONS_PER_BODY = 5
# Keep the clock drain boundary and leave room for every advertised window.
MAX_LEASE_FRAME = FRAME_MASK - LEASE_WINDOWS * LEASE_BLOCK_STRIDE
REPORT_WINDOW = 64  # frames after the shot in which an owner frame may report it
CONFIRM_TICKS = 3  # at least this after the report (0.05 s)
UNREPORTED_TICKS = 32  # a shot no owner frame reported gets lease(f) after this
DELIVERY_GRACE_TICKS = 64  # server cleanup limit for pending projectile delivery after a shot ends
RELEASE_MARGIN = (
    6  # frames after the server's rocket ended before the release (unverified: the client's flies the same)
)
CREATE_AT = (0.0, -1000.0, 0.0)  # an id the client did not shoot becomes an unflown entity here
TALON_SNIPER_ROUND = 0x0400000000001E80
FIELD_ENTITY = 0x04000000000003DC  # 03DC.003, the Biotic Field (0257 st1's m_projectileSpawn)
GRAVITON_FIELD_ENTITY = 0x04000000000003E2
FIELD_GRAPH = 0x0258  # its initial graph
FIELD_FIRST, FIELD_SPAN = 0xA0080000, 0x00040000  # the field entities' ids
SEEN_FIRST, SEEN_SPAN = 0xA00C0000, 0x00040000  # the server projectiles the other clients see
FIELD_DELAY = 12  # approximate deployment delay in world ticks after admitting the cast projectile
FIELD_SECONDS = 5.0  # 0258: #567 = 5, st1 Wait #567 - 0.5 then st4 Wait 0.5, then DestroyEntity (DATA)
FIELD_TICKS = math.ceil(FIELD_SECONDS * 1000 / FRAME_MS)
FADE_SECONDS = 4.5  # 0258 st1 ends: st5 (its DataFlowMapping) goes off
HEAL_PER_SECOND = 35.0  # 0258 #420 = 35 a player, ApplyAura 025F.053's 002A (DATA; per second: unverified)
HEAL_RADIUS = 5.0  # 0258 #422 = 5: st0 TrackTargets m_radius (DATA)
HEAL_UP = 1.0  # TrackTargets' offset (0, 1, 0) from the field (DATA; unverified: the sphere's centre)
FIELD_STATES = {
    2: ("STU_691BFA55", {"counter": 1}),
    3: ("STUStatescriptStateCosmeticEntity", {}),
    5: ("STUStatescriptStateDataFlowMapping", {}),
    8: ("STU_38EE1100", {}),
}
# 0258's remote states on: st2 Effect 0B06 (the ring), st3 CosmeticEntity 068A (the canister on the ground),
# st5 DataFlowMapping 011E (until 4.5 s), st8 SendGameMessage; st9 Effect's player switch stays off.


def _sync(state) -> int:
    """A volley's resolved sync (as the PS4 build resolves it): LET_GAME_DECIDE (0) is NONE
    (3) for a volley of 2 or more shots at 5.5 a second or more, SHOTS_ONLY (2) without a lifetime or with
    more than 7 projectiles a shot, else SHOTS_AND_PROJECTILES (1)."""
    node = state.node

    def number(name, default=0.0):
        cfg = node.field(name)
        value = state.evaluate(cfg, watch=False) if cfg is not None else None
        try:
            number = float(value) if value is not None else default
        except (TypeError, ValueError, OverflowError):
            return default
        return number if math.isfinite(number) else default

    sync = int(number("m_5E4A4E85"))
    if sync:
        return sync
    if number("m_A17BE89B", 1) >= 2 and number("m_numShotsPerSecond") >= 5.5:
        return 3
    if number("m_projectileLifetime") < 1.19e-5 or number("m_numProjectilesPerShot", 1) > 7:
        return SHOTS_ONLY
    return PREDICTED


def owner_to_world(clock_origin, frame: int) -> int:
    """Map a source-owner frame; None preserves the legacy frame convention."""
    if clock_origin is None:
        return frame
    tick, origin, frame_ms = clock_origin
    return tick + (frame - origin) * frame_ms // FRAME_MS


def world_to_owner(clock_origin, tick: int) -> int:
    """Name a native release in the captured source-owner clock domain."""
    if clock_origin is None:
        return tick
    origin, frame, frame_ms = clock_origin
    return frame + (tick - origin) * FRAME_MS // frame_ms






def launch_directions(
    state, direction: tuple[float, float, float],
) -> tuple[tuple[float, float, float], ...]:
    """Resolve the authored pellet rings for a shot without allocating projectile entities.

    The spread is in degrees. Use fixed, evenly spaced directions within each ring;
    the native azimuth and the variation of individual pellet angles remain unverified.
    Projectile sync modes with entity leases still use the existing single direction.
    """
    single = (direction,)
    launch = state.node.field("m_5EC1EB38")
    if not isinstance(launch, dict) or launch.get("$") != "STU_613BDDB0":
        return single

    def number(config):
        value = state.evaluate(config, watch=False) if config is not None else None
        return value if type(value) in (int, float) else None

    count = number(state.node.field("m_numProjectilesPerShot"))
    rings = launch.get("m_0D30E40C")
    if count is None or not 2 <= count <= MAX_PELLETS or not isinstance(rings, list) or len(rings) > count:
        return single
    spread = number(launch.get("m_spread"))
    if spread is None or not 0 <= spread <= 180 or _sync(state) != SHOTS_ONLY:
        return single

    layout = []
    for ring in rings:
        if not isinstance(ring, dict) or ring.get("$") != "STU_B101467F":
            return single
        radius = number(ring.get("m_6425FFB9"))
        pellets = number(ring.get("m_B19BD35D"))
        if radius is None or not 0 <= radius <= 1 or pellets is None or not 1 <= pellets <= count:
            return single
        if int(pellets) != pellets:
            return single
        layout.append((math.radians(radius * spread), int(pellets)))
    if sum(pellets for _, pellets in layout) != count:
        return single

    x, y, z = direction
    horizontal = math.hypot(x, z)
    right = (z / horizontal, 0.0, -x / horizontal) if horizontal else (1.0, 0.0, 0.0)
    up = (y * right[2], horizontal, -y * right[0])
    basis = tuple(zip(direction, right, up, strict=True))
    directions = []
    for tilt, pellets in layout:
        if tilt == 0:
            directions.extend((direction,) * pellets)
            continue
        forward_scale, radial_scale = math.cos(tilt), math.sin(tilt)
        for index in range(pellets):
            phase = math.tau * index / pellets
            right_scale = radial_scale * math.cos(phase)
            up_scale = radial_scale * math.sin(phase)
            directions.append(tuple(
                forward_scale * forward + right_scale * across + up_scale * vertical
                for forward, across, vertical in basis
            ))
    return tuple(directions)


def _defs(cfg) -> list[int]:
    """Every 003 GUID a projectile entity field names (both sides of a condition)."""
    out = []
    if isinstance(cfg, dict):
        if cfg.get("$") == "STU_8556841E" and isinstance(cfg.get("m_entityDef"), str):
            out.append(int(cfg["m_entityDef"], 16))
        for value in cfg.values():
            out += _defs(value)
    elif isinstance(cfg, list):
        for value in cfg:
            out += _defs(value)
    return out


def definitions(script) -> list[int]:
    """The projectile definitions the body's client predicts under a lease: every definition a predicted
    (m_localPrediction) SHOTS_AND_PROJECTILES volley of the body's graphs names, in graph order."""
    found: list[int] = []
    for instance in script.component.instances.values():
        for node in instance.graph.states:
            if node is None or node.cls != "STUStatescriptStateWeaponVolley" or not node.field("m_45271E8C"):
                continue
            if _sync(instance.state(node.state)) != PREDICTED:
                continue
            definitions = _defs(node.field("m_projectileEntity"))
            if not definitions and (default := pharah.primary_projectile_default(instance.state(node.state))):
                definitions = [default]
            for guid in definitions:
                if guid not in found:
                    found.append(guid)
    return found


@dataclass
class _ProjectileArrival:
    received: bool = False

    def arrived(self, last: int) -> None:
        self.received = True


@dataclass
class Shot:
    """One projectile's authority and observer deliveries, with lease state for player shots."""

    player: object
    body: int
    definition: int
    frame: int  # f: the command frame the VM fired it in
    instance: int
    state: int
    activation: tuple[int, int]  # full volley counter and start time at emission
    spawn: int | None = None  # the entity it leaves where it lands (03DC.003), else None
    where: tuple = (0.0, 0.0, 0.0)  # the shooter's feet when it fired
    bound: int = 0  # frames the projectile lives at most (lifetime, range / speed)
    seen_reports: int = 0  # the number of the last owner frame looked at
    reports: list[OwnerReport] = field(default_factory=list)
    created: dict[int, _ProjectileArrival] = field(default_factory=dict)  # owner creates and their ACKs
    created_tick: int | None = None
    release_at: int | None = None  # the tick of the release and destroy
    field_ids: dict[tuple[int, int | None], int] = field(default_factory=dict)  # (viewer slot, parent) -> id
    observers: list = field(default_factory=list)  # per-viewer launches of this projectile
    authoritative: bool = False  # server-authored flights without prediction leases
    observer_only: bool = False  # locally predicted SHOTS_ONLY flight, remote copy only
    lease_offset: int | None = None  # one pellet, or all IDs for a single-flight volley
    shot_index: int = 0
    field_due: int | None = None
    field_tick: int | None = None
    field_airborne: bool = False
    faded: bool = False
    arrivals: list = field(default_factory=list)  # the field entities whose create arrived
    filter_bits: int | None = None  # source body's team/FFA bits captured with the volley
    clock_origin: tuple[int, int, int] | None = None  # immutable world tick, owner frame, owner frame ms
    entity: int | None = None  # shared identity for Sigma's authored projectile output
    source_script: object | None = None  # launch VM retained only for turret alias retirement
    echo_launch: 'ShotRecord | None' = None  # original Echo event, before any observer baseline ACK
    observer_record: "ShotRecord | None" = None  # admitted Hanzo event, including solo casts
    observer_deadline: int | None = None  # original absolute deadline, never refreshed by joining
    observer_activation: tuple | None = None  # exact original H=0 state for the retained volley queue


@dataclass
class _RetiredTurretThrow:
    shot: Shot
    until: int
    aliases: set[int]


@dataclass(frozen=True)
class _FieldProfile:
    ticks: int
    fade_ticks: int
    radius: float
    heal_value: float

class _PlayerFieldInputs(expr.Context):
    def __init__(self):
        super().__init__()
        self.values = {}

    def var(self, scope: int, var: int):
        return self.values.get((scope, var))

    def native(self, cfg):
        if cfg.get("$") == "STU_D3EB8D87":
            return False  # this host deploys fields from player input
        return super().native(cfg)

def _field_inputs(field_graph) -> _PlayerFieldInputs:
    """Resolve the authored player branch's initial SetVars."""
    inputs = _PlayerFieldInputs()
    for position in (2, 3, 4):
        node = field_graph.node(position)
        key = expr.lvalue(node.field("m_out_Var"))
        inputs.values[key] = expr.evaluate(node.field("m_value"), inputs)
    return inputs


@cache
def _field_profile(field_graph) -> _FieldProfile:
    """Resolve 0258's player parameters and two sequential authored waits."""
    inputs = _field_inputs(field_graph)
    fade = expr.to_float(expr.evaluate(field_graph.node(5).field("m_timeout"), inputs))
    tail = expr.to_float(expr.evaluate(field_graph.node(9).field("m_timeout"), inputs))
    radius = expr.to_float(expr.evaluate(field_graph.node(1).field("m_radius"), inputs))
    heal = expr.to_float(inputs.var(expr.INSTANCE, 420))
    if not all(math.isfinite(value) and value >= 0 for value in (fade, tail, radius, heal)):
        raise ValueError("invalid player Biotic Field resource values")
    fade_ms = nodes.seconds_to_ms(fade)
    lifetime_ms = fade_ms + nodes.seconds_to_ms(tail)
    return _FieldProfile(math.ceil(lifetime_ms / FRAME_MS), math.ceil(fade_ms / FRAME_MS), radius, heal)


class Projectiles:
    """Projectile leases, confirmations, observer delivery, and the Biotic Field."""

    def __init__(self, combat) -> None:
        self.combat = combat
        self.match = combat.match
        self.shots: list[Shot] = []
        self.retired_turrets: list[_RetiredTurretThrow] = []
        self.observed: list[_ObservedProjectile] = []
        self.next_field = 0
        self.next_seen = 0
        self.sigma_scripts = {}  # original18B7/1EB9 components, only in admitted profiles

    # --- the lease ------------------------------------------------------------------------------------

    @staticmethod
    def _base(slot: int, index: int) -> int:
        start = LEASE_FIRST + slot * LEASE_SLOT_STRIDE
        if index == DEFINITIONS_PER_BODY - 1:
            return start + LEASE_SPAN
        return start + index * LEASE_BLOCK_STRIDE

    def lease(self, player) -> list | None:
        """The body's lease blocks (component 90): two windows from the current frame for each predicted
        definition, with the slot's own ids."""
        script = player.body_script
        if script is None or not 0 <= player.slot < LEASE_SLOTS:
            return None
        newest = player.commands.last_frame
        frame = self.match.tick if newest is None else newest
        blocks = []
        for index, guid in enumerate(definitions(script)[:DEFINITIONS_PER_BODY]):
            width = self._lease_width(player, guid)
            span = LEASE_SPAN // width
            start = frame - frame % span
            base = self._base(player.slot, index)
            for index in range(LEASE_WINDOWS):
                window = start + index * span
                blocks.append((guid, window, window + span, base, width))
        return blocks or None

    def lease_id(self, player, definition: int, frame: int) -> int | None:
        """The id the client's allocator gives the first projectile of `definition` in `frame`."""
        script = player.body_script
        if script is None or not 0 <= player.slot < LEASE_SLOTS:
            return None
        available = definitions(script)[:DEFINITIONS_PER_BODY]
        if definition not in available:
            return None
        base = self._base(player.slot, available.index(definition))
        width = self._lease_width(player, definition)
        span = LEASE_SPAN // width
        return 0xA0000000 | (base + (frame % span) * width)

    @staticmethod
    def _lease_width(player, definition: int) -> int:
        # 19E5 emits all three Tri-Shot pellets during one command frame.
        # Reserve and confirm every ID the native allocator consumes.
        if definition == 0x0400000000002CBF and player.body_script is not None:
            for instance in player.body_script.component.instances.values():
                if instance.graph.index == 0x19E5:
                    state = instance.state(3)
                    return max(1, min(MAX_PELLETS, expr.to_int(
                        state.evaluate(state.node.field("m_numProjectilesPerShot"), watch=False)
                    )))
        return 1

    # --- shots ----------------------------------------------------------------------------------------

    def fired(
        self, player, state, frame: int, definition: int, bound_seconds: float, origin=None, direction=None,
        *, activation: tuple[int, int], snapshot: Snapshot | None = None, clock_origin=None,
        lease_offset: int | None = None, publish: bool = True, shot_index: int = 0, observer_activation=None,
    ) -> Shot | None:
        """Register a volley projectile on the owner and observer paths that apply to its source."""
        npc = isinstance(player, bots.Bot)
        sigma_rock = ((state.instance.graph.index, state.node.pos, definition)
                      == (0x1BBB, 9, 0x04000000000018B7)
                      and not state.node.field("m_45271E8C")
                      and _sync(state) == PREDICTED)
        fire_strike = ((state.instance.graph.index, state.node.pos, definition)
                       == (0x00E2, 3, 0x04000000000001EA)
                       and not state.node.field("m_45271E8C")
                       and _sync(state) == PREDICTED)
        ana_grenade = (not npc and player.hero.guid == 0x02E000000000013B
                       and (state.instance.graph.index, state.node.pos, definition)
                       == (0x0B97, 1, 0x0400000000001426))
        ana_dart = (not npc and player.hero.guid == 0x02E000000000013B
                    and (state.instance.graph.index, state.node.pos, definition)
                    == (0x0BDD, 14, 0x04000000000015E8)
                    and _sync(state) == SHOTS_ONLY)
        moira_orb = (not npc and player.hero.guid == 0x02E00000000001A2
                     and (state.instance.graph.index, state.node.pos, definition) in (
                         (0x10E4, 20, 0x0400000000001E60),
                         (0x10E4, 21, 0x0400000000001E61),
                     ) and not state.node.field("m_45271E8C"))
        baptiste_canister = ((state.instance.graph.index, state.node.pos, definition)
                             == (0x1761, 21, 0x04000000000029A8)
                             and not state.node.field("m_45271E8C")
                             and _sync(state) == PREDICTED)
        authoritative = (not npc and (
            (player.hero.guid == 0x02E000000000013E
             and state.instance.graph.index in (0x0BD4, 0x0CCB, 0x0BD7))
            or (player.hero.guid == 0x02E0000000000016
                and (state.instance.graph.index, state.node.pos, definition)
                == (0x157C, 85, 0x04000000000026F1)
                and not state.node.field("m_45271E8C") and _sync(state) == PREDICTED)
            or (player.hero.guid == 0x02E000000000023B and sigma_rock)
            or (player.hero.guid == 0x02E0000000000007 and fire_strike)
            or (player.hero.guid == 0x02E000000000000A
                and (state.instance.graph.index, state.node.pos, definition)
                == (0x0056, 1, 0x04000000000000AF)
                and not state.node.field("m_45271E8C")
                and _sync(state) == PREDICTED)
            or (player.hero.guid == 0x02E0000000000065
                and (state.instance.graph.index, state.node.pos, definition)
                == (0x09BA, 7, 0x04000000000011A9)
                and not state.node.field("m_45271E8C") and _sync(state) == PREDICTED)
            or ana_grenade or moira_orb
            or (player.hero.guid == 0x02E0000000000221 and baptiste_canister)))
        if (not authoritative and not ana_dart
                and (_sync(state) != PREDICTED
                     or (not npc and self.lease_id(player, definition, frame) is None))):
            return None
        if clock_origin is not None:
            if (not isinstance(clock_origin, tuple) or len(clock_origin) != 3
                    or any(type(value) is not int for value in clock_origin) or clock_origin[2] <= 0):
                raise ValueError('projectile clock requires immutable world/owner/frame-ms integers')
        spawned = _defs(state.node.field("m_49CBA3BC"))
        if npc and spawned:
            return None  # the NPC path covers ballistic projectiles
        shot = Shot(
            player,
            player.entity if npc else player.body,
            definition,
            frame,
            state.instance.id,
            state.index,
            activation,
            spawn=spawned[0] if spawned else None,
            where=tuple(player.position if npc else player.mover.position),
            bound=math.ceil(bound_seconds * 1000 / FRAME_MS),
            seen_reports=0 if npc else player.body_script.report_count,
            authoritative=authoritative,
            clock_origin=clock_origin,
            filter_bits=None if npc else player.team_bit,
            observer_only=ana_dart,
            lease_offset=lease_offset,
            shot_index=shot_index,
        )
        if (not npc and (state.instance.graph.index, state.node.pos, definition)
                == (0x025F, 1, 0x04000000000008CE)):
            # 025F exports its projectile through1752. Every peer must resolve
            # that same actual lease identity, including the source owner.
            shot.entity = self.lease_id(player, definition, frame)
        if not npc and not authoritative and not ana_dart:
            self.shots.append(shot)
        turret_carrier = (not npc and player.hero.guid == 0x02E0000000000006
                          and definition == 0x0400000000002931
                          and shot.spawn == 0x04000000000028B7)
        if turret_carrier:
            shot.source_script = player.body_script
        supercharger_carrier = (not npc and player.hero.guid == 0x02E000000000013E
                                and state.instance.graph.index == 0x0CF5
                                and definition == 0x0400000000001CCB
                                and shot.spawn == 0x040000000000174E)
        if origin is not None and direction is not None:
            hanzo_projectiles.remember_launch(
                shot, state, origin, direction, self.match.tick + shot.bound + DELIVERY_GRACE_TICKS,
                observer_activation)
            if not npc and player.hero.guid == 0x02E0000000000020:
                self.combat.zenyatta.remember_launch(
                    shot, state, origin, direction, self.match.tick + shot.bound + DELIVERY_GRACE_TICKS,
                    observer_activation)
        if (origin is not None and direction is not None
                and (state.instance.graph.index, definition) in (
                    (0x19E5, 0x0400000000002CBF), (0x1C36, 0x0400000000002CBA))):
            if not npc and player.hero.guid == 0x02E0000000000206:
                from ow174.game import echo_projectiles

                echo_projectiles.remember_launch(
                    shot, state, origin, direction, self.match.tick + shot.bound + DELIVERY_GRACE_TICKS,
                    observer_activation)
            else:
                shot.echo_launch = ShotRecord(
                    frame, shot.instance, shot.state, activation[0], tuple(origin), tuple(direction), (),
                    shot=shot_index, volleys=state.volleys & 0xFF,
                )
        if (publish and (shot.spawn in (None, GRAVITON_FIELD_ENTITY) or authoritative
                         or turret_carrier or supercharger_carrier)
                and origin is not None and direction is not None):
            if shot.observer_activation is not None:
                self._show_others(
                    shot, state, origin, direction, snapshot, original_record=shot.observer_record,
                    deadline=shot.observer_deadline, original_activation=shot.observer_activation,
                )
            else:
                self._show_others(shot, state, origin, direction, snapshot)
        return shot

    def fired_volley(self, player, state, frame, definition, bound_seconds, origin, direction,
                     *, activation, snapshot, count, shot_index, clock_origin=None, observer_activation=None):
        """Keep each pellet's collision and retirement independent under one native volley event."""
        shots = tuple(self.fired(
            player, state, frame, definition, bound_seconds, origin, direction,
            activation=activation, snapshot=snapshot, lease_offset=pellet, publish=False,
            shot_index=shot_index, clock_origin=clock_origin, observer_activation=observer_activation,
        ) for pellet in range(count))
        if any(shot is None for shot in shots):
            raise ValueError("multi-projectile volley has no prediction lease")
        options = {}
        if shots[0].observer_activation is not None:
            options = {'original_record': shots[0].observer_record, 'deadline': shots[0].observer_deadline,
                       'original_activation': shots[0].observer_activation}
        self._show_others(shots[0], state, origin, direction, snapshot, shots=shots, **options)
        return shots

    def _show_others(self, shot: Shot, state, origin, direction, snapshot: Snapshot | None,
                     *, shots=None, viewers=None, original_record=None, deadline=None,
                     original_activation=None) -> None:
        """Send all projectile IDs in one native volley event, with independent retirements."""
        # A live-flight catch-up supplies the immutable event and its original deadline.
        shots = (shot,) if shots is None else shots
        if (not isinstance(shot.player, bots.Bot) and shot.player.hero.guid == 0x02E0000000000206
                and (state.instance.graph.index, shot.definition) in (
                    (0x19E5, 0x0400000000002CBF), (0x1C36, 0x0400000000002CBA))
                and original_activation is None):
            return  # A mutable consume-time State is not an authentic Echo emission.
        if original_activation is not None:
            payload = dict(original_activation[2])
            if (original_record is None or original_activation[0] != state.node.cls
                    or (payload.get("counter"), payload.get("start"), payload.get("volleys", 0) & 0xFF)
                    != (original_record.activation, shot.activation[1], original_record.volleys)):
                raise ValueError("original volley activation differs from the accepted shot")
        observers = self.combat.observers
        npc = isinstance(shot.player, bots.Bot)
        if (shot.authoritative and state.instance.graph.index == 0x1BBB
                and state.node.pos == 9 and shot.definition == 0x04000000000018B7):
            # 1BBB exports this actual projectile as entity20360. Its remote
            # graphs must resolve the same identity on every connection.
            shot.entity = SEEN_FIRST + self.next_seen % SEEN_SPAN
            self.next_seen += 1
        if (shot.authoritative and state.instance.graph.index == 0x0CCB
                and shot.definition == 0x040000000000172C):
            # Export the actual orb ID used by the target's0CCE effect caller.
            shot.entity = SEEN_FIRST + self.next_seen % SEEN_SPAN
            self.next_seen += 1
        for viewer in self.match.players if viewers is None else viewers:
            if (viewer is shot.player and not shot.authoritative) or viewer.client is None:
                continue
            stream = observers.projectile_stream(viewer, shot.body, npc=npc)
            if stream is None:
                continue
            if shot.entity is not None:
                ids = (shot.entity,)
            else:
                ids = tuple(SEEN_FIRST + (self.next_seen + index) % SEEN_SPAN
                            for index in range(len(shots)))
                self.next_seen += len(shots)
            if original_record is None:
                record = ShotRecord(
                    shot.frame, shot.instance, shot.state, shot.activation[0],
                    tuple(origin), tuple(direction), ids, shot=shot.shot_index, volleys=state.volleys & 0xFF,
                )
            else:
                record = replace(original_record, ids=ids)
            launch = EntityUpdate(shot.body)
            launch.ch2 = shot_events([record])
            group = _ProjectileVolley(len(shots)) if len(shots) > 1 else None
            delivery_deadline = (self.match.tick + shot.bound + DELIVERY_GRACE_TICKS
                                 if deadline is None else deadline)
            for member, entity in zip(shots, ids, strict=True):
                delivery = _ObservedProjectile(
                    viewer, viewer.client, stream, record, launch, delivery_deadline,
                    npc=npc, entity=entity, volley=group, clock_origin=member.clock_origin,
                )
                if group is not None:
                    group.creations.append(delivery.creation)
                    delivery.launch_arrival = group.arrival
                activation = delivery.matching_volley(snapshot) if npc else None
                if activation is not None:
                    cls, active, payload = activation
                    delivery.activation = cls, active, tuple(sorted(dict(payload).items()))
                    viewer.client.retain_bot_volley(delivery)
                elif not npc and viewer is not shot.player:
                    observers.retain_volley(delivery, state)
                    if original_activation is not None and delivery.activation is not None:
                        delivery.activation = original_activation
                components = dict(hanzo_projectiles.confirmation_components(member) or {})
                # Original1E60/1E61 Pet source is the captured cast body. Keep
                # the existing generic confirmation-components API shared.
                if (state.instance.graph.index, state.node.pos, member.definition) in (
                        (0x10E4, 20, 0x0400000000001E60),
                        (0x10E4, 21, 0x0400000000001E61)):
                    components[72] = [member.body]
                create = EntityUpdate(entity, OP_CREATE, confirm_record(
                    member.definition, components=components))
                create.stream = delivery.creation
                launch.stream = delivery.launch_arrival
                member.observers.append(delivery)
                self.observed.append(delivery)
                if stream.base is None or member.entity is not None:
                    delivery.pending_create = create
                else:
                    viewer.client.queue_entities([create])

    def observe_live(self, shot: Shot, graph_index: int) -> None:
        """Catch up a connection to a caller-qualified, still-live single flight.

        The caller owns physical lifetime eligibility. Reuse the original volley
        and deadline rather than starting another flight or extending delivery.
        """
        owner = shot.player
        script = owner.body_script
        if (owner.body != shot.body or script is None or script.retired or not shot.observers
                or not (shot.authoritative or shot.observer_only)):
            return
        instance = script.component.instances.get(shot.instance)
        state = (instance.states.get(shot.state)
                 if instance is not None and instance.graph.index == graph_index else None)
        if state is None:
            return
        viewers = [viewer for viewer in self.match.players if viewer.client is not None and viewer.spawned
                   and not viewer.gone and not any(
                       delivery.viewer is viewer and delivery.client is viewer.client
                       for delivery in shot.observers)]
        if not viewers:
            return
        original = shot.observers[0].record
        deadline = min(delivery.deadline for delivery in shot.observers)
        if self.match.tick >= deadline:
            return
        self._show_others(
            shot, state, original.origin, original.direction, None,
            viewers=viewers, original_record=original, deadline=deadline,
        )

    def observe_existing_volley(self, viewer, source: int, record: "ShotRecord"):
        """Launch existing actor IDs; their factory retains create/flight/retire authority."""
        stream = self.combat.observers.projectile_stream(viewer, source, npc=False)
        if viewer.client is None or stream is None or not record.ids:
            return ()
        launch = EntityUpdate(source)
        launch.ch2 = shot_events([record])
        group = _ProjectileVolley(len(record.ids))
        launch.stream = group.arrival
        deliveries = []
        for entity in record.ids:
            delivery = _ObservedProjectile(
                viewer, viewer.client, stream, record, launch, None,
                entity=entity, volley=group, externally_managed=True,
            )
            delivery.launch_arrival = group.arrival
            group.creations.append(delivery.creation)
            deliveries.append(delivery)
        self.observed.extend(deliveries)
        return tuple(deliveries)


    def retire_existing(self, delivery, tick: int):
        """Close presentation when its sole actor factory retires that same ID."""
        if not delivery.externally_managed or not any(item is delivery for item in self.observed):
            return
        delivery.finish(tick, False)
        delivery.advance(tick)
        self.observed[:] = [item for item in self.observed if item is not delivery]
        remaining = [item for item in self.observed if item.launch is delivery.launch]
        if remaining and not delivery.volley.queued:
            record = replace(delivery.record, ids=tuple(item.entity for item in remaining))
            delivery.launch.ch2 = shot_events([record])
            for item in remaining:
                item.record = record


    def absorbed(self, shot: Shot) -> None:
        """Retire an intercepted projectile without impact release or spawned field."""
        if isinstance(shot.player, bots.Bot):
            for delivery in shot.observers:
                delivery.finish(self.match.tick, False)
            return
        self._end(shot, self.match.tick, explode=False)

    def flown(self, shot: Shot, seconds: float) -> None:
        """Finish an authoritative NPC copy or schedule the predicted player's release."""
        if shot.definition == 0x0400000000001CCB and shot.spawn == 0x040000000000174E:
            # The original carrier has a model, while the server separately
            # creates174E. Let a late observer launch finish its brief flight;
            # retire the carrier without a second impact/deployable release.
            self._end(shot, self.match.tick, explode=False, drain=True,
                      visual_ticks=math.ceil(seconds * 1000 / FRAME_MS))
            return
        if shot.definition == 0x0400000000002931 and shot.spawn == 0x04000000000028B7:
            # The landed actor owns construction and firing. Retire every
            # prediction alias without asking the carrier to spawn another one.
            self._end(shot, self.match.tick, explode=False)
            if shot.source_script is not None and not any(item.shot is shot for item in self.retired_turrets):
                until = max(self.match.tick, owner_to_world(shot.clock_origin, shot.frame + REPORT_WINDOW))
                self.retired_turrets.append(_RetiredTurretThrow(
                    shot, until + DELIVERY_GRACE_TICKS, set(shot.created)))
            return
        if shot.authoritative or shot.observer_only:
            for delivery in shot.observers:
                delivery.finish(self.match.tick, True, drain=True)
            return
        if isinstance(shot.player, bots.Bot):
            # The remote launch can arrive after this short round has already hit.
            # Give the client's own flight and attached effects time to reach the impact.
            visual_ticks = (
                math.ceil(seconds * 1000 / FRAME_MS) if shot.definition == TALON_SNIPER_ROUND else 0
            )
            for delivery in shot.observers:
                delivery.finish(self.match.tick, False, drain=True, visual_ticks=visual_ticks)
            return
        if shot.spawn not in (None, GRAVITON_FIELD_ENTITY) or shot.release_at is not None:
            return
        start = next((report.cmfd for report in shot.reports if report.received is not False), shot.frame)
        due = owner_to_world(shot.clock_origin, start) + math.ceil(seconds * 1000 / FRAME_MS) + RELEASE_MARGIN
        # A delayed scheduler can finish a flight after its input-frame deadline.
        # Start the delivery window no earlier than this simulation update.
        shot.release_at = max(due, self.match.tick)

    def cancel_npc(self, bot: bots.Bot, tick: int) -> None:
        """Retire a stopped NPC's client deliveries, including shots still draining."""
        for shot in list(self.shots):
            if shot.player is bot:
                self._end(shot, tick, False)
        remaining = []
        for delivery in self.observed:
            if delivery.npc and delivery.launch.entity == bot.entity:
                delivery.finish(tick, False)
                delivery.advance(tick)
            else:
                remaining.append(delivery)
        self.observed = remaining

    def update(self, tick: int, *, first_tick: int | None = None) -> None:
        from ow174.game import accretion

        accretion.update_scripts(self, tick)
        first_tick = tick if first_tick is None else first_tick
        for shot in list(self.shots):
            try:
                self._step(shot, tick, first_tick)
            except Exception:
                log.exception("[game] %s: projectile %X failed", self.match.label(), shot.definition & 0xFFFF)
                for delivery in shot.observers:
                    delivery.finish(tick, False)
                if shot in self.shots:
                    self.shots.remove(shot)
        self.retired_turrets[:] = [item for item in self.retired_turrets if self._retired_turret(item, tick)]
        self.observed = [delivery for delivery in self.observed if delivery.advance(tick)]

    def _retired_turret(self, item: _RetiredTurretThrow, tick: int) -> bool:
        shot, player = item.shot, item.shot.player
        if (tick > item.until or player not in self.match.players or player.client is None
                or not player.has_body or player.body != shot.body
                or player.body_script is not shot.source_script):
            return False
        self._collect_reports(shot, shot.source_script)
        updates = []
        for entity in self._confirmation_ids(shot, tick):
            if entity not in item.aliases:
                item.aliases.add(entity)
                updates.append(EntityUpdate(entity, OP_DESTROY))
        if updates:
            player.client.queue_entities(updates)
        return tick < item.until

    @staticmethod
    def _collect_reports(shot: Shot, script) -> None:
        shot.reports[:] = [report for report in shot.reports if report.received is not False]
        key = (shot.instance, shot.state)
        for report in script.reports:
            if (
                report.number > shot.seen_reports and report.received is not False
                and shot.frame <= report.cmfd <= shot.frame + REPORT_WINDOW
                and report.volleys.get(key) == shot.activation
            ):
                shot.reports.append(report)
        shot.seen_reports = script.report_count

    def _step(self, shot: Shot, tick: int, first_tick: int) -> None:
        player = shot.player
        script = player.body_script
        alive = player.client is not None and player.has_body and player.body == shot.body
        if not alive or script is None:
            self._end(shot, tick, explode=alive)
            return
        self._collect_reports(shot, script)
        if shot.spawn == FIELD_ENTITY:
            self._field(shot, tick, first_tick)
            return
        due = shot.release_at
        if due is None and shot.created_tick is not None:
            due = shot.created_tick + shot.bound + RELEASE_MARGIN  # no flight from the server: the bound
        if due is not None and tick >= due + DELIVERY_GRACE_TICKS:
            self._end(shot, tick, explode=True)
            return
        self._confirm(shot, tick)
        # Queuing a destroy retires queued and in-flight creates, so even a late
        # confirmation must arrive before we send the explosion release.
        if (due is not None and tick >= due and shot.created
                and all(arrival.received for arrival in shot.created.values())):
            self._end(shot, tick, explode=True)

    def _confirmation_ids(self, shot: Shot, tick: int) -> list[int]:
        ready = [report for report in shot.reports if report.received is True]
        if ready:
            # A stop or repeat of an already acknowledged activation still
            # confirms the original shot. Only an introduction supplies an alias.
            key = (shot.instance, shot.state)
            frames = [shot.frame, *(report.cmfd for report in ready if key in report.introductions)]
        elif (not shot.reports and tick >= owner_to_world(shot.clock_origin, shot.frame) + UNREPORTED_TICKS
              and not shot.created):
            frames = [shot.frame]
        else:
            return []
        new = []
        for frame in frames:
            entity = self.lease_id(shot.player, shot.definition, frame)
            if entity is not None:
                offsets = (range(self._lease_width(shot.player, shot.definition))
                           if shot.lease_offset is None else (shot.lease_offset,))
                for offset in offsets:
                    pellet = entity + offset
                    if pellet not in shot.created and pellet not in new:
                        new.append(pellet)
        return new

    def _confirm(self, shot: Shot, tick: int) -> None:
        new = self._confirmation_ids(shot, tick)
        if not new:
            return
        if shot.created_tick is None:
            shot.created_tick = tick
        build = confirm_record(shot.definition, components=hanzo_projectiles.confirmation_components(shot))
        updates = []
        for entity in new:
            arrival = shot.created[entity] = _ProjectileArrival()
            update = EntityUpdate(entity, OP_CREATE, build)
            update.stream = arrival
            updates.append(update)
        shot.player.client.queue_entities(updates)
        log.info(
            "[game] %s: %s's projectile %04X (frame %d, reported at %s) queued confirmation as %s",
            self.match.label(),
            shot.player.name,
            shot.definition & 0xFFFF,
            shot.frame,
            [report.cmfd for report in shot.reports if report.received is True],
            " ".join(f"{entity:08X}" for entity in new),
        )

    def intercepted(self, shot: Shot, tick: int) -> None:
        """Retire the original prediction without an explosion release."""
        self._end(shot, tick, explode=False)

    def _end(self, shot: Shot, tick: int, explode: bool, *, drain=False, visual_ticks=0) -> None:
        """End the shot, release its owner copies and field entities, and retire observer deliveries."""
        if shot in self.shots:
            self.shots.remove(shot)
        for delivery in shot.observers:
            delivery.finish(tick, explode, drain=drain, visual_ticks=visual_ticks)
        for (slot, _), entity in shot.field_ids.items():
            viewer = next((player for player in self.match.players if player.slot == slot), None)
            if viewer is not None and viewer.client is not None:
                viewer.client.queue_entities([EntityUpdate(entity, OP_DESTROY)])
        client = getattr(shot.player, "client", None)
        if client is None or not shot.created:
            return
        updates = []
        for entity, arrival in shot.created.items():
            update = EntityUpdate(entity, OP_DESTROY)
            if explode and shot.spawn in (None, GRAVITON_FIELD_ENTITY) and arrival.received:
                update.ch2 = release(world_to_owner(shot.clock_origin, tick) + RELEASE_AHEAD)
            updates.append(update)
        client.queue_entities(updates)

    def retire_fields(self, player) -> None:
        """Release the retiring body's canisters and deployed field entities."""
        for shot in tuple(self.shots):
            if shot.player is player and shot.body == player.body and shot.spawn == FIELD_ENTITY:
                self._end(shot, self.match.tick, explode=False)

    # --- the Biotic Field -----------------------------------------------------------------------------

    def finish_field_flight(self, shot: Shot, tick: int, ground_point=None) -> None:
        """Resolve host placement before the field can heal or send a create."""
        if not shot.field_airborne:
            return
        shot.field_airborne = False
        shot.field_due = max(tick, shot.field_due if shot.field_due is not None else tick + FIELD_DELAY)
        if ground_point is not None:
            shot.where = tuple(expr.f32(axis) for axis in ground_point)

    def _field(self, shot: Shot, tick: int, first_tick: int) -> None:
        if shot.field_due is None:
            shot.field_due = tick + FIELD_DELAY
        if shot.field_airborne:
            self._confirm(shot, tick)
            return
        profile = _field_profile(graphs_data.graph(FIELD_GRAPH))
        start = shot.field_due
        expires = start + profile.ticks
        if tick < expires:
            self._confirm(shot, tick)
        if tick < start:
            return
        if shot.field_tick is None:
            shot.field_tick = start
            log.info("[game] %s: %s's Biotic Field opened", self.match.label(), shot.player.name)
        self._heal_field(shot, profile, start, first_tick, tick)
        if tick >= expires:
            self._end(shot, tick, explode=False)
            return
        if not shot.faded and tick - start >= profile.fade_ticks:
            shot.faded = True
            for arrival in shot.arrivals:
                arrival.send()
        self._deliver_field(shot)

    @staticmethod
    def last_field_heal_tick(fields) -> int:
        profile = _field_profile(graphs_data.graph(FIELD_GRAPH))
        return max(shot.field_due for shot in fields) + profile.ticks - 1

    def heal_fields(self, first_tick: int, last_tick: int) -> None:
        """Settle healing before replayed combat without advancing field delivery."""
        fields = []
        for shot in self.shots:
            player = shot.player
            start = shot.field_due
            if (shot.spawn == FIELD_ENTITY and not shot.field_airborne
                    and start is not None and start < last_tick
                    and player.client is not None and player.has_body and player.body == shot.body
                    and player.body_script is not None):
                fields.append((shot, start))
        if not fields:
            return
        profile = _field_profile(graphs_data.graph(FIELD_GRAPH))
        if len(fields) == 1:
            shot, start = fields[0]
            self._heal_field(shot, profile, start, first_tick, last_tick)
            return
        # Each field takes its turn on every tick. Batching per field lets
        # the first caster claim wounds that another field healed earlier.
        first = max(first_tick, min(start for _, start in fields) + 1)
        last = min(last_tick, max(start for _, start in fields) + profile.ticks - 1)
        for tick in range(first, last + 1):
            for shot, start in fields:
                self._heal_field(shot, profile, start, tick, tick)

    def _heal_field(
        self, shot: Shot, profile: _FieldProfile, start: int, first_tick: int, last_tick: int,
    ) -> None:
        # The opening and expiry ticks have no heal. Replay uses the latest poses.
        first = max(first_tick, start + 1)
        last = min(last_tick, start + profile.ticks - 1)
        steps = last - first + 1
        if steps > 0:
            amount = profile.heal_value * FRAME_MS / 1000 * steps
            self.combat.heal_area(
                shot.player, field_centre(shot), profile.radius, amount, ticks=(first, last), source_body=shot.body,
            )

    def _deliver_field(self, shot: Shot) -> None:
        """One landed field per viewer, independent of prediction confirmation aliases."""
        theme, _ = shot.player.skin
        for viewer in self.match.players:
            if viewer.client is None or not viewer.spawned:
                continue
            if viewer is shot.player and not any(arrival.received for arrival in shot.created.values()):
                continue
            key = viewer.slot, None
            if key in shot.field_ids:
                continue
            entity = FIELD_FIRST + self.next_field % FIELD_SPAN
            self.next_field += 1
            shot.field_ids[key] = entity
            # Confirmation aliases identify the same predicted throw. Parenting
            # one area to each alias multiplied the native effect under delay.
            # Use the authoritative landing transform, as observers already do.
            create = EntityUpdate(entity, OP_CREATE, field_create(
                None, shot.where, theme, source_body=shot.body, filter_bits=shot.filter_bits))
            create.stream = FieldArrived(shot, viewer, Stream(entity))
            viewer.client.queue_entities([create])


def field_centre(shot: Shot) -> tuple:
    x, y, z = shot.where
    return (x, y + HEAL_UP, z)


class FieldArrived:
    """The field entity's create arrived: its graph's frame goes, and again when it fades."""

    def __init__(self, shot: Shot, viewer, stream: Stream) -> None:
        self.shot, self.viewer, self.stream = shot, viewer, stream
        self.done = False

    def arrived(self, last: int) -> None:
        if self.done:
            return
        self.done = True
        self.shot.arrivals.append(self)
        self.send()

    def send(self) -> None:
        if self.viewer.client is not None:
            self.viewer.client.queue_entities([self.stream.full_frame(field_frame(self.shot.faded))])


def confirm_record(definition: int, *, components=None):
    """A plain create of the projectile's definition: for an id the client shot it only confirms it."""

    def build(origin: int):
        return world.create(
            origin, definition, components or {}, flags=WITH_TRANSFORM | WITH_SCALE, position=CREATE_AT)

    return build


def field_create(
    parent: int | None, where: tuple, theme: int = 0, *, source_body: int | None = None,
    filter_bits: int | None = None,
):
    """03DC.003's create: with the movement block that names its parent (the owner's canister), else placed
    at `where`. The caster's skin theme supplies the field's cosmetic substitutions."""

    skin = (theme, False)
    components = {}
    if source_body is not None:
        components[72] = [source_body]
    if filter_bits is not None:
        components[26] = [filter_bits]

    def build(origin: int):
        if parent is None:
            return world.create(origin, FIELD_ENTITY, components, position=where, skin=skin)
        return world.create(
            origin, FIELD_ENTITY, components, movement=world.Movement((0.0, 0.0, 0.0), flags=KEEP_ROTATION),
            skin=skin, movement_parent=parent,
        )

    return build


def field_frame(faded: bool = False) -> BitWriter:
    """0258's H = 0 full frame as instance 1 (the client builds it itself from 03DC's initial graphs and the
    frame adopts it): the ring, the canister on the ground and, until it fades, st5."""
    field_graph = graphs_data.graph(FIELD_GRAPH)
    item = InstanceWire(1, field_graph)
    for (scope, var), value in _field_inputs(field_graph).values.items():
        if scope == expr.INSTANCE:
            item.vars[var] = (value, ())
    # CosmeticEntity.output: its authored output variable identifies the child
    # state that DataFlowMapping addresses. Full frames must retain that handle.
    cosmetic = field_graph.states[3]
    scope, var = expr.lvalue(cosmetic.field("m_1DEF59FA"))
    if scope == expr.INSTANCE:
        item.vars[var] = (expr.Handle(1, item.id, cosmetic.state, 0), ())
    for index, (cls, payload) in FIELD_STATES.items():
        if not (faded and index == 5):
            item.states[index] = StateWire(cls, True, dict(payload))
    return remote_full_frame([item], {}, {1: field_graph})


# --- the shot record: how the other clients see a predicted projectile -------------------------------------
#
# A SHOTS_AND_PROJECTILES volley's projectile on the other clients is a server entity that a shot record
# starts (the PC reader 0x7FF789C91F80): the record goes in ch2 of the shooter's entity block (events bit,
# then per event a bit, the frame (the first a var_b, then steps), the record; a 0 bit; then no projectile
# state). The record: the shot counter, a spread bit, form 2 (bit 0): instance w16, state w16, the activation
# counter (6 bits), the volley counter (a 1 bit for 1), a w16; the origin (3 raw f32, world), yaw16 and
# pitch16 (value ^ 0x8000, pitch up, 0x7FF789CC1360), and the projectile ids (bit, w16 count, the first id,
# then steps). The client starts the projectile only while it holds the shooter's volley record of that key:
# entity, instance id, state index and volley counter (key 0x7FF789C95180; the volley's begin 0x7FF789C71950
# takes the instance's id +0x18 and the state's +0x70, the m_states index the state constructor 0x7FF78AAA1460
# stores). Unverified: the other client holds that record from the volley state the H = 0 frames begin.

YAW16 = 32768 / math.pi  # 10430.378: radians to the record's units
PITCH16 = 65536 / math.pi  # 20860.756


def _w16(out: BitWriter, value: int) -> None:
    """0x7FF78A76B9E0: 0 + 4 bits, 10 + 8, 11 + 16."""
    value &= 0xFFFF
    if value < 16:
        out.bit(0)
        out.bits(value, 4)
    elif value < 256:
        out.bit(1)
        out.bit(0)
        out.bits(value, 8)
    else:
        out.bit(1)
        out.bit(1)
        out.bits(value, 16)


def _w_counter(out: BitWriter, value: int) -> None:
    """0x7FF78A76BB90: 0 + 4 bits, 1 + 8 bits (below 0xFF), 1 + 0xFF + 32 bits."""
    value &= 0xFFFFFFFF
    if value < 16:
        out.bit(0)
        out.bits(value, 4)
    elif value < 0xFF:
        out.bit(1)
        out.bits(value, 8)
    else:
        out.bit(1)
        out.bits(0xFF, 8)
        out.bits(value, 32)


def _w_entity(out: BitWriter, entity: int) -> None:
    """0x7FF789CC0F00: 2 type bits, then 0 + 8 bits, 1 + 16 bits (below 0xFFFF), 1 + 0xFFFF + 32 bits."""
    out.bits((entity >> 29) & 3, 2)
    value = entity & 0x1FFFFFFF
    if value < 256:
        out.bit(0)
        out.bits(value, 8)
    elif value < 0xFFFF:
        out.bit(1)
        out.bits(value, 16)
    else:
        out.bit(1)
        out.bits(0xFFFF, 16)
        out.bits(value, 32)


def direction16(direction) -> tuple[int, int]:
    """A unit direction as the record's (yaw16, pitch16): angles rounded half away from zero, value ^ 0x8000;
    the pitch is up-positive (0x7FF789CC1360 reads back (sin a cos b, sin b, cos a cos b))."""
    x, y, z = direction
    yaw = math.atan2(x, z) * YAW16 if (x or z) else 0.0
    pitch = math.asin(max(-1.0, min(1.0, y))) * PITCH16
    out = []
    for value in (yaw, pitch):
        number = int(value + (0.5 if value >= 0 else -0.5))
        number = max(-0x8000, min(0x7FFF, number))
        out.append((number & 0xFFFF) ^ 0x8000)
    return out[0], out[1]


@dataclass(frozen=True)
class ShotRecord:
    frame: int  # the shot's command frame
    instance: int  # the volley's instance
    state: int  # its m_states index (unverified: the client's state +104)
    activation: int  # the volley state's activation counter (6 bits)
    origin: tuple  # world position
    direction: tuple  # unit direction
    ids: tuple  # the projectile entities it starts
    shot: int = 0
    volleys: int = 1


@dataclass
class _ProjectileVolley:
    remaining: int
    creations: list = field(default_factory=list)
    arrival: _ProjectileArrival = field(default_factory=_ProjectileArrival)
    queued: bool = False


@dataclass
class _ObservedProjectile:
    """One connection's create, shooter event and retirement for a remote projectile."""

    viewer: object
    client: object
    stream: object
    record: ShotRecord
    launch: EntityUpdate
    deadline: int | None
    npc: bool = False
    entity: int | None = None
    volley: _ProjectileVolley | None = None
    pending_create: EntityUpdate | None = None  # queued only once the source baseline arrives
    activation: tuple | None = None  # an exact H=0 volley retained until launch ACK
    creation: _ProjectileArrival = field(default_factory=_ProjectileArrival)
    launch_arrival: _ProjectileArrival = field(default_factory=_ProjectileArrival)
    launch_queued: bool = False
    launch_ack_tick: int | None = None
    finished: bool = False
    explode: bool = False
    visual_ticks: int = 0
    clock_origin: tuple[int, int, int] | None = None
    externally_managed: bool = False

    def finish(self, tick: int, explode: bool, *, drain: bool = False, visual_ticks: int = 0) -> None:
        self.finished = True
        self.explode = explode
        self.visual_ticks = visual_ticks
        grace = DELIVERY_GRACE_TICKS if explode or drain else 0
        self.deadline = tick + grace if self.deadline is None else min(self.deadline, tick + grace)
        if self.externally_managed and self.volley is not None:
            self.volley.creations[:] = [item for item in self.volley.creations if item is not self.creation]

    def advance(self, tick: int) -> bool:
        if self.viewer.client is not self.client:
            self._release_volley()
            return False
        observers = self.viewer.match.combat.observers
        current = observers.projectile_stream(self.viewer, self.launch.entity, npc=self.npc)
        source_live = current is self.stream
        launched = self.launch_arrival.received
        if launched and self.launch_ack_tick is None:
            self.launch_ack_tick = tick
        visual_done = self.launch_ack_tick is not None and tick >= self.launch_ack_tick + self.visual_ticks
        expired = self.deadline is not None and tick >= self.deadline
        if not source_live or expired or (self.finished and visual_done):
            if self.volley is not None:
                self.volley.remaining -= 1
            last = self.volley is None or self.volley.remaining == 0
            queued = self.launch_queued if self.volley is None else self.volley.queued
            if queued and not launched and last:
                self.client.cancel_entity_record(self.launch)
            self._release_volley()
            if self.externally_managed:
                return False  # the actor factory owns its sole DESTROY
            if self.pending_create is not None:
                return False  # nothing about this projectile reached the client
            update = EntityUpdate(self.record.ids[0] if self.entity is None else self.entity, OP_DESTROY)
            if source_live and self.explode and launched:
                update.ch2 = release(world_to_owner(self.clock_origin, tick) + RELEASE_AHEAD)
            self.client.queue_entities([update])
            return False
        if launched:
            self._release_volley()
        if self.pending_create is not None and self.stream.base is not None:
            self.client.queue_entities([self.pending_create])
            self.pending_create = None
        queued = self.launch_queued if self.volley is None else self.volley.queued
        created = (self.creation.received if self.volley is None
                   else all(item.received for item in self.volley.creations))
        if not queued and created and self._volley_arrived():
            self.launch_queued = True
            if self.volley is not None:
                self.volley.queued = True
            self.client.queue_entities([self.launch])
        return True

    @property
    def volley_key(self) -> tuple[int, int]:
        return self.record.instance, self.record.state

    def matching_volley(self, snapshot: Snapshot | None) -> tuple | None:
        state = snapshot.states.get(self.volley_key) if snapshot is not None else None
        if state is None:
            return None
        payload = dict(state[2])
        key = (payload.get("counter"), payload.get("volleys", 0) & 0xFF)
        return state if key == (self.record.activation, self.record.volleys) else None

    def _volley_arrived(self) -> bool:
        # The owner executes its volley locally. Owner frames deliberately omit
        # remote-only volley states, so their Snapshot cannot acknowledge one.
        if self.viewer.body == self.launch.entity:
            return self.stream.base is not None
        return self.matching_volley(self.stream.base) is not None

    def _release_volley(self) -> None:
        if self.activation is not None:
            if self.npc:
                self.client.release_bot_volley(self)
            else:
                self.viewer.match.combat.observers.release_volley(self)
            self.activation = None


def write_shot(out: BitWriter, shot: ShotRecord) -> None:
    """One record after its frame (0x7FF789C91F80, form 2)."""
    _w_counter(out, shot.shot)
    out.bit(0)  # no spread
    out.bit(0)  # form 2
    _w16(out, shot.instance)
    _w16(out, shot.state)
    out.bits(shot.activation & 63, 6)
    if shot.volleys == 1:
        out.bit(1)
    else:
        out.bit(0)
        _w16(out, shot.volleys)
    _w16(out, 0)
    for value in shot.origin:
        out.f32(value)
    yaw16, pitch16 = direction16(shot.direction)
    out.bits(yaw16, 16)
    out.bits(pitch16, 16)
    if not shot.ids:
        out.bit(0)
        return
    out.bit(1)
    _w16(out, len(shot.ids))
    _w_entity(out, shot.ids[0])
    for before, entity in zip(shot.ids, shot.ids[1:], strict=False):
        step = entity - before
        if step == 1:
            out.bit(1)
        else:
            out.bit(0)
            out.bit(step < 0)
            _w_counter(out, abs(step))


def shot_events(shots: list) -> BitWriter:
    """ch2 after its channel bit in the shooter's entity block: the shot records in frame order, then no
    projectile state (0x7FF789A4A490 -> 0x7FF789A50130)."""
    out = BitWriter()
    out.bit(1)  # events
    last = None
    for shot in sorted(shots, key=lambda item: item.frame):
        out.bit(1)
        if last is None:
            out.var_b(shot.frame & 0xFFFFFFFF)
        else:
            step = shot.frame - last
            if step < 16:
                out.bit(0)
                out.bits(step, 4)
            elif step < 256:
                out.bit(1)
                out.bit(0)
                out.bits(step, 8)
            else:
                out.bit(1)
                out.bit(1)
                out.bits(step & 0xFFFFFFFF, 32)
        write_shot(out, shot)
        last = shot.frame
    out.bit(0)  # the end of the events
    out.bit(0)  # no projectile state
    return out
