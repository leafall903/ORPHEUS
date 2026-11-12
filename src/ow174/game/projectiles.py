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
from dataclasses import dataclass, field
from functools import cache

from ow174.game import bots, world
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
}
# 0258's remote states on: st2 Effect 0B06 (the ring), st3 CosmeticEntity 068A (the canister on the ground),
# st5 DataFlowMapping 011E (until 4.5 s); st8 SendGameMessage and st9 Effect (under a switch) stay off.


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
            for guid in _defs(node.field("m_projectileEntity")):
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
    field_due: int | None = None
    field_tick: int | None = None
    field_airborne: bool = False
    faded: bool = False
    arrivals: list = field(default_factory=list)  # the field entities whose create arrived


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

@cache
def _field_profile(field_graph) -> _FieldProfile:
    """Resolve 0258's player parameters and two sequential authored waits."""
    inputs = _PlayerFieldInputs()
    for position in (2, 3, 4):
        node = field_graph.node(position)
        key = expr.lvalue(node.field("m_out_Var"))
        inputs.values[key] = expr.evaluate(node.field("m_value"), inputs)
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
        self.observed: list[_ObservedProjectile] = []
        self.next_field = 0
        self.next_seen = 0

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
        start = frame - frame % LEASE_SPAN
        blocks = []
        for index, guid in enumerate(definitions(script)[:DEFINITIONS_PER_BODY]):
            base = self._base(player.slot, index)
            for index in range(LEASE_WINDOWS):
                window = start + index * LEASE_SPAN
                blocks.append((guid, window, window + LEASE_SPAN, base, 1))
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
        return 0xA0000000 | (base + frame % LEASE_SPAN)

    # --- shots ----------------------------------------------------------------------------------------

    def fired(
        self, player, state, frame: int, definition: int, bound_seconds: float, origin=None, direction=None,
        *, activation: tuple[int, int], snapshot: Snapshot | None = None,
    ) -> Shot | None:
        """Register a volley projectile on the owner and observer paths that apply to its source."""
        npc = isinstance(player, bots.Bot)
        if _sync(state) != PREDICTED or (not npc and self.lease_id(player, definition, frame) is None):
            return None
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
        )
        if not npc:
            self.shots.append(shot)
        if shot.spawn is None and origin is not None and direction is not None:
            self._show_others(shot, state, origin, direction, snapshot)
        return shot

    def _show_others(self, shot: Shot, state, origin, direction, snapshot: Snapshot | None) -> None:
        """Every other client with the shooter's body: a server entity of the projectile's definition (under
        the map until a record starts it) and the shot record in the shooter's ch2 naming it."""
        observers = self.combat.observers
        npc = isinstance(shot.player, bots.Bot)
        for viewer in self.match.players:
            if viewer is shot.player or viewer.client is None:
                continue
            stream = observers.projectile_stream(viewer, shot.body, npc=npc)
            if stream is None:
                continue  # the client has no statescript stream for this body
            entity = SEEN_FIRST + self.next_seen % SEEN_SPAN
            self.next_seen += 1
            record = ShotRecord(
                shot.frame,
                shot.instance,
                shot.state,
                shot.activation[0],
                tuple(origin),
                tuple(direction),
                (entity,),
                volleys=state.volleys & 0xFF,
            )
            launch = EntityUpdate(shot.body)
            launch.ch2 = shot_events([record])
            deadline = self.match.tick + shot.bound + DELIVERY_GRACE_TICKS
            delivery = _ObservedProjectile(viewer, viewer.client, stream, record, launch, deadline, npc=npc)
            activation = delivery.matching_volley(snapshot) if npc else None
            if activation is not None:
                cls, active, payload = activation
                delivery.activation = cls, active, tuple(sorted(dict(payload).items()))
                viewer.client.retain_bot_volley(delivery)
            create = EntityUpdate(entity, OP_CREATE, confirm_record(shot.definition))
            create.stream = delivery.creation
            launch.stream = delivery.launch_arrival
            shot.observers.append(delivery)
            self.observed.append(delivery)
            if stream.base is None:
                delivery.pending_create = create
            else:
                viewer.client.queue_entities([create])

    def absorbed(self, shot: Shot) -> None:
        """Retire an intercepted projectile without impact release or spawned field."""
        if isinstance(shot.player, bots.Bot):
            for delivery in shot.observers:
                delivery.finish(self.match.tick, False)
            return
        self._end(shot, self.match.tick, explode=False)

    def flown(self, shot: Shot, seconds: float) -> None:
        """Finish an authoritative NPC copy or schedule the predicted player's release."""
        if isinstance(shot.player, bots.Bot):
            # The remote launch can arrive after this short round has already hit.
            # Give the client's own flight and attached effects time to reach the impact.
            visual_ticks = (
                math.ceil(seconds * 1000 / FRAME_MS) if shot.definition == TALON_SNIPER_ROUND else 0
            )
            for delivery in shot.observers:
                delivery.finish(self.match.tick, False, drain=True, visual_ticks=visual_ticks)
            return
        if shot.spawn is not None or shot.release_at is not None:
            return
        start = next((report.cmfd for report in shot.reports if report.received is not False), shot.frame)
        due = start + math.ceil(seconds * 1000 / FRAME_MS) + RELEASE_MARGIN
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
        self.observed = [delivery for delivery in self.observed if delivery.advance(tick)]

    def _step(self, shot: Shot, tick: int, first_tick: int) -> None:
        player = shot.player
        script = player.body_script
        alive = player.client is not None and player.has_body and player.body == shot.body
        if not alive or script is None:
            self._end(shot, tick, explode=alive)
            return
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

    def _confirm(self, shot: Shot, tick: int) -> None:
        ready = [report for report in shot.reports if report.received is True]
        if ready:
            # A stop or repeat of an already acknowledged activation still
            # confirms the original shot. Only an introduction supplies an alias.
            key = (shot.instance, shot.state)
            frames = [shot.frame, *(report.cmfd for report in ready if key in report.introductions)]
        elif not shot.reports and tick >= shot.frame + UNREPORTED_TICKS and not shot.created:
            frames = [shot.frame]
        else:
            return
        new = []
        for frame in frames:
            entity = self.lease_id(shot.player, shot.definition, frame)
            if entity is not None and entity not in shot.created and entity not in new:
                new.append(entity)
        if not new:
            return
        if shot.created_tick is None:
            shot.created_tick = tick
        build = confirm_record(shot.definition)
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
            [report.cmfd for report in ready],
            " ".join(f"{entity:08X}" for entity in new),
        )

    def intercepted(self, shot: Shot, tick: int) -> None:
        """Retire the original prediction without an explosion release."""
        self._end(shot, tick, explode=False)

    def _end(self, shot: Shot, tick: int, explode: bool) -> None:
        """End the shot, release its owner copies and field entities, and retire observer deliveries."""
        if shot in self.shots:
            self.shots.remove(shot)
        for delivery in shot.observers:
            delivery.finish(tick, explode)
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
            if explode and shot.spawn is None and arrival.received:
                update.ch2 = release(tick + RELEASE_AHEAD)
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
            if (shot.spawn == FIELD_ENTITY and not shot.field_airborne and start is not None and start < last_tick
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
        """Create each field once. Owner copies wait for their canister parent to arrive."""
        theme, _ = shot.player.skin
        for viewer in self.match.players:
            if viewer.client is None or not viewer.spawned:
                continue
            parents = (
                (parent for parent, arrival in shot.created.items() if arrival.received)
                if viewer is shot.player else (None,)
            )
            for parent in parents:
                key = viewer.slot, parent
                if key in shot.field_ids:
                    continue
                entity = FIELD_FIRST + self.next_field % FIELD_SPAN
                self.next_field += 1
                shot.field_ids[key] = entity
                create = EntityUpdate(entity, OP_CREATE, field_create(parent, shot.where, theme))
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


def confirm_record(definition: int):
    """A plain create of the projectile's definition: for an id the client shot it only confirms it."""

    def build(origin: int):
        return world.create(origin, definition, {}, flags=WITH_TRANSFORM | WITH_SCALE, position=CREATE_AT)

    return build


def field_create(parent: int | None, where: tuple, theme: int = 0):
    """03DC.003's create: with the movement block that names its parent (the owner's canister), else placed
    at `where`. The caster's skin theme supplies the field's cosmetic substitutions."""

    skin = (theme, False)

    def build(origin: int):
        if parent is None:
            return world.create(origin, FIELD_ENTITY, {}, position=where, skin=skin)
        return world.create(
            origin, FIELD_ENTITY, {}, movement=world.Movement((0.0, 0.0, 0.0), flags=KEEP_ROTATION),
            skin=skin, movement_parent=parent,
        )

    return build


def field_frame(faded: bool = False) -> BitWriter:
    """0258's H = 0 full frame as instance 1 (the client builds it itself from 03DC's initial graphs and the
    frame adopts it): the ring, the canister on the ground and, until it fades, st5."""
    field_graph = graphs_data.graph(FIELD_GRAPH)
    item = InstanceWire(1, field_graph)
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
class _ObservedProjectile:
    """One connection's create, shooter event and retirement for a remote projectile."""

    viewer: object
    client: object
    stream: object
    record: ShotRecord
    launch: EntityUpdate
    deadline: int
    npc: bool = False
    pending_create: EntityUpdate | None = None  # queued only once the source baseline arrives
    activation: tuple | None = None  # an exact H=0 volley retained until launch ACK
    creation: _ProjectileArrival = field(default_factory=_ProjectileArrival)
    launch_arrival: _ProjectileArrival = field(default_factory=_ProjectileArrival)
    launch_queued: bool = False
    launch_ack_tick: int | None = None
    finished: bool = False
    explode: bool = False
    visual_ticks: int = 0

    def finish(self, tick: int, explode: bool, *, drain: bool = False, visual_ticks: int = 0) -> None:
        self.finished = True
        self.explode = explode
        self.visual_ticks = visual_ticks
        grace = DELIVERY_GRACE_TICKS if explode or drain else 0
        self.deadline = min(self.deadline, tick + grace)

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
        if not source_live or tick >= self.deadline or (self.finished and visual_done):
            if self.launch_queued and not launched:
                self.client.cancel_entity_record(self.launch)
            self._release_volley()
            if self.pending_create is not None:
                return False  # nothing about this projectile reached the client
            update = EntityUpdate(self.record.ids[0], OP_DESTROY)
            if source_live and self.explode and launched:
                update.ch2 = release(tick + RELEASE_AHEAD)
            self.client.queue_entities([update])
            return False
        if launched:
            self._release_volley()
        if self.pending_create is not None and self.stream.base is not None:
            self.client.queue_entities([self.pending_create])
            self.pending_create = None
        if not self.launch_queued and self.creation.received and self._volley_arrived():
            self.launch_queued = True
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
        return self.matching_volley(self.stream.base) is not None

    def _release_volley(self) -> None:
        if self.activation is not None:
            self.client.release_bot_volley(self)
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
