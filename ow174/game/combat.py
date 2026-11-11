"""Shots, damage, death and respawn: what the server decides and what it tells the clients.

What the client needs (read in the client unless marked):
- Health is component 51 (world.pool): an op-2 update of a pool's field changes the bar on every client
  that has the body. A body whose health pools (type 0) add up to 0 with a maximum above 0 is dead
  (0x7FF789ADBF00): the client then sets flag 0x2000 on its movement state (0x7FF7896A4F9A) and the 3P
  animation takes the dead pose 004.015 (0x7FF789B24889); flag 0x2000 in the records does the same.
- ClientInCombat 25001 {entity, u32 damage, u8 flags} u32 frame (handler 0x7FF7895D2D40) tells the shooter
  of a hit: it goes in his component 59's hit list (+0x12D0, records in frame order, not on his own
  body), which shows the target's health bar for 5 s (0x7FF7895D8520: int(1e6 / quanta) * 5 frames) and
  feeds the hit marker and its sound (event 7). Flag bit 0 counts at +0x124, else +0x120; bit 1 at +0x128.
  Unverified: bit 0 = critical, bit 1 = kill (the OW2 cross-check: 19 / 38 damage with 0 / 1, the last hit 2 /
  3).
- 25005 {u64, entity, entity victim, u8 score, bool assist} (0x7FF7895D2B50) and 25004 {u8, entity}
  (0x7FF7895D2A70) post 0F76.025 and 0F7B.025 / 0F7C.025 to the game mode entity's statescript
  (0x7FF7895D7AF0, the first component-114 entity): its graph 1FC0 (a client-only child of 0CB6) shows
  "ELIMINATED: <name>" (1126.07C), "YOU WERE KILLED BY <name>" (1127.07C) or "YOU DIED" (1128.07C). So
  the mode entity carries 0CB6 (mode_frame); the kill feed 0DEA is its child too.
- The kill feed line: the dead body's 01CF takes game message 003E.025 (the body's "died": killer 0012,
  crit 0095, loadout 03E6, assists 03E7), pulses its ClientOnlyPulser st108, and every client's 01CF sends
  0765.025 to the mode entity's 0DEA (DATA). So the server gives 003E.025 to the dead player's body
  script; its frames carry the line.

What the server decides here:
- A shot is a volley's shot in the shooter's body script (nodes.WeaponVolley.report): hitscan along the
  command's aim from the eye (owned STUFirstPersonComponent m_E29993A5 and crouched m_816B830F;
  known body offsets in eye_height,otherwise legacy defaults), or a projectile flown each tick.
- Damage from the volley's hit definition (the graph data, evaluated by the body script): the
  ModifyHealth amount (Soldier 20, Helix 40 and 80 splash, quick melee 30), unverified: falloff from
  m_EAD7F104 to m_157E5BC5 metres down to m_72A81154 (30 m, 50 m, 30 %), critical times m_A872C70E (2), splash
  scaled between the explosion rings (1 m: 1.0, 3 m: 0.5). Shields, then armour (5 less a hit, at most half:
  unverified, OW1's rule; the client has no damage code), then health.
- Quick melee: 0043 st4 (ModifyHealth) on what its st5 TrackTargets would find (MELEE). The Biotic Field
  heals through heal_area (projectiles.py). A dead body's statescript takes no presses (the VM's
  owner_dead, as the client's LogicalButton 0x7FF78AAAC570), so the dead do not shoot.
- Bodies are capsules from the feet to the mover's height (radius the mover's, DATA; unverified as a hitbox),
  a hit at or above the eye height less HEAD_BELOW is critical (unverified). Shots stop at the map's collision
  (the mover's part of it, which the server keeps). Allies are not hit.
- Targets are where the shooter's client showed them: at the command's view time (commands.py: the
  frame's time less the view delay the command carries, 0x7FF7894BEE40 / 0x7FF7894BEEC0), from the
  server's own record of where each body was in every tick (the records the clients got, bots too),
  between two ticks by a straight line (unverified: as the client draws them), at most HISTORY ticks back.
- Respawn: heroes after the mode's time (Practice Range 3 s, PvP 10 s: the mode graphs' 06FD.025 to the
  body script 0C8E, DATA), at the spawn point as a new body (match.switch_hero); training bots after
  BOT_RESPAWN (unverified: 3 s in the OW2 cross-check) as new entities at their spawner.
"""

import logging
import math
from collections import deque
from dataclasses import dataclass, replace

from ow174.game import dva_matrix, sigma_flux, sombra_hack
from ow174.game import (
    assassin,
    barriers,
    reinhardt_barrier,
    world_bubbles,
    bots,
    contacts,
    defense_boxes,
    immunity,
    heavy_charge,
    junkrat,
    launch_ring,
    observers,
    pools,
    projectiles,
    sigma_grasp,
    stats,
    voicelines,
    world,
)
from ow174.game.bits import BitWriter
from ow174.game.collision import MOVER, World
from ow174.game.commands import Command
from ow174.game.mover import mover_data
from ow174.game.script import expr, graph, nodes, runtime
from ow174.game.script.bot import DEATH_VOLLEY_GRAPH, DIED, BotScript
from ow174.game.script.driver import Snapshot
from ow174.game.statescript import InstanceWire, StateWire, remote_full_frame
from ow174.game.world import ARMOUR_POOL, HEALTH_POOL, OP_DESTROY, POOLS, SHIELDS_POOL

log = logging.getLogger("ow174.game")

HIT = 25001  # to the shooter: a hit
DEATH_NOTICE = 25004  # to the dead player: who killed him
KILL_NOTICE = 25005  # to the killer: whom he killed
KILLED_BY = 2  # 25004 +0x78: "you were killed by" (0F7B.025); anything else "you died" (0F7C.025)
CRITICAL, KILLING = 0x01, 0x02  # 25001 flags (unverified, see above)
DEAD = 0x2000  # movement-state flag: dead (the 3P anim's dead pose)
# 003E.025's parameters, by the low 16 bits of their 026 identifiers (01CF rid4).
KILLER, CRIT, LOADOUT, ASSISTS = 0x0012, 0x0095, 0x03E6, 0x03E7
MODE_UI = 0x0CB6  # the mode's client UI graph: the kill feed 0DEA and the notices 1FC0 (client-only)

EYE = 1.55  # m: STUFirstPersonComponent m_E29993A5, its default (OWLib) and Soldier's (DATA; unverified: eye)
# Owned FirstPerson components: standing m_E29993A5,crouched m_816B830F.
EYE_STANDING = {0x0400000000000380: 1.25, 0x04000000000002A3: 1.425}
EYE_CROUCHED = {0x04000000000003CF: 1.085, 0x0400000000000380: .875,
                0x04000000000002A3: .9975}
EYE_CROUCHED_DEFAULT = 1.15
HEAD_BELOW = 0.2  # m under the eye where the head starts (unverified)
HISTORY = 64  # ticks of body positions kept for the shots' view time (about 1 s; unverified: retail's limit)
COMBAT_CATCH_UP = 64  # at most about a second of retained combat frames per update
RANGE = 200.0  # m a hitscan shot reaches when its volley names none (unverified)
RESPAWN = 10.0  # s a hero waits in the PvP modes (0F4A and 0EC2 send 06FD.025 {10}: DATA)
PRACTICE_RESPAWN = 3.0  # s in the Practice Range (13C0 sends 06FD.025 {3}: DATA)
BOT_RESPAWN = 3.0  # s a training bot lies dead (unverified: OW2 made two killed bots again after 2.98 s)
RESPAWN_RETRY = 0.25  # s between attempts when all player body IDs are still reserved
MELEE = {0x0043: (4, 1.5, 1.0)}  # graph -> (its ModifyHealth state, metres ahead of the eye, radius): DATA
ARMOUR_BLOCK = 5.0  # damage armour stops per hit (unverified: OW1's rule; the client has no damage code)
TICK = 0.016
EPSILON = 1e-9
FLASHBANG_EFFECT = 0x04A0000000000079  # .053/0079 ->0228 ->224E
FLASHBANG_MODE_FLAG = 13408  # 0228 duration: flag set1.7s, otherwise0.8s
SLEEP_DART_PROJECTILE = 0x04000000000015E8
SLEEP_DART_EFFECT = 0x04A000000000026F  # 0BDD/108 -> 026F.053 -> 0BDE
SLEEP_DURATION = 5.0
SLEEP_MINIMUM = 1.5
SLEEP_WAKE_RECOVERY = 0.5


# --- geometry -----------------------------------------------------------------------------------------


def eye_height(body: int, crouched: bool = False) -> float:
    """Known target-build FirstPerson heights;unrecorded bodies retain the legacy fallback."""
    if crouched:
        return EYE_CROUCHED.get(body, EYE_CROUCHED_DEFAULT)
    return EYE_STANDING.get(body, EYE)


def aim(yaw: int, pitch: int) -> tuple[float, float, float]:
    """The unit vector a command looks along: forward is (sin yaw, 0, cos yaw), the pitch is positive
    looking down (commands.py)."""
    y = yaw * 2 * math.pi / 65536
    p = pitch * 2 * math.pi / 65536
    return (math.sin(y) * math.cos(p), -math.sin(p), math.cos(y) * math.cos(p))


def _dot(a, b) -> float:
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def _sub(a, b):
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def _along(origin, direction, t: float):
    return (origin[0] + direction[0] * t, origin[1] + direction[1] * t, origin[2] + direction[2] * t)


def ray_capsule(origin, direction, a, b, radius: float) -> float | None:
    """Where a ray (unit direction) first meets the capsule around segment a-b: its distance, 0 from
    inside, None when it misses."""
    ba, oa = _sub(b, a), _sub(origin, a)
    baba, bard, baoa = _dot(ba, ba), _dot(ba, direction), _dot(ba, oa)
    if segment_distance(origin, a, b) <= radius:
        return 0.0
    rdoa, oaoa = _dot(direction, oa), _dot(oa, oa)
    k2 = baba - bard * bard
    k1 = baba * rdoa - baoa * bard
    k0 = baba * oaoa - baoa * baoa - radius * radius * baba
    if abs(k2) > EPSILON:
        h = k1 * k1 - k2 * k0
        if h < 0.0:
            return None
        t = (-k1 - math.sqrt(h)) / k2
        y = baoa + t * bard
        if 0.0 < y < baba and t >= 0.0:
            return t
    best = None
    for centre in (a, b):  # the end spheres
        oc = _sub(origin, centre)
        half = _dot(direction, oc)
        h = half * half - (_dot(oc, oc) - radius * radius)
        if h >= 0.0:
            t = -half - math.sqrt(h)
            if t >= 0.0 and (best is None or t < best):
                best = t
    return best


def segment_distance(point, a, b) -> float:
    """The distance from a point to the segment a-b."""
    ab, ap = _sub(b, a), _sub(point, a)
    length = _dot(ab, ab)
    t = 0.0 if length <= EPSILON else max(0.0, min(1.0, _dot(ap, ab) / length))
    closest = _along(a, ab, t)
    d = _sub(point, closest)
    return math.sqrt(_dot(d, d))


def capsule_visible(collision: World | None, origin, a, b, radius: float) -> bool:
    """Reconstructed visibility: a static ray to the nearest capsule surface."""
    ab, ap = _sub(b, a), _sub(origin, a)
    length = _dot(ab, ab)
    share = 0.0 if length <= EPSILON else max(0.0, min(1.0, _dot(ap, ab) / length))
    toward = _sub(_along(a, ab, share), origin)
    distance = math.sqrt(_dot(toward, toward))
    if distance <= radius + EPSILON:
        return True
    reach = distance - radius
    hit = ray_world(collision, origin, tuple(axis / distance for axis in toward), reach)
    return hit is None or hit + EPSILON >= reach


def ray_world(
    collision: World | None, origin, direction, length: float, category: int = MOVER,
) -> float | None:
    """The distance to the first static hit, for callers that only need occlusion."""
    hit = collision.cast_ray(origin, direction, length, category) if collision is not None else None
    return hit.distance if hit is not None else None


# --- health -------------------------------------------------------------------------------------------


@dataclass
class TemporaryShield:
    pool_id: int
    most: float
    now: float


class Vitals:
    """A body's pools as component 51 has them: health, armour, shields (world.health's ids and fields)."""

    def __init__(self, entity: int, health: float, armour: float = 0.0, shields: float = 0.0) -> None:
        self.entity = entity
        self.most = {HEALTH_POOL: float(health), ARMOUR_POOL: float(armour), SHIELDS_POOL: float(shields)}
        self.now = dict(self.most)
        self.temporary_shields: dict[int, TemporaryShield] = {}
        self.ids = {}
        pool_id = 1
        for kind in (SHIELDS_POOL, ARMOUR_POOL):  # world.health numbers them so
            if self.most[kind] > 0:
                self.ids[kind] = pool_id
                pool_id += 1
        self.ids[HEALTH_POOL] = pool_id
        self.changed: set[int] = set()  # pools that changed since the last update went out
        self.died_at: float | None = None
        self.death_tick: int | None = None
        self.killer: int | None = None
        self.sleep_until = 0
        self.sleep_wake_floor = 0

    @property
    def dead(self) -> bool:
        return self.died_at is not None

    @property
    def total(self) -> float:
        return sum(self.now.values()) + sum(pool.now for pool in self.temporary_shields.values())

    @property
    def maximum(self) -> float:
        return sum(self.most.values()) + sum(pool.most for pool in self.temporary_shields.values())

    def grant_temporary_shields(self, tag: int, amount: float, limit: float) -> float:
        """Reconstructed tagged shields: no healing refill, capped current amount.

        Keep a stable component51 slot/ID even after depletion; an empty record
        clears the previous value without borrowing the body's base shield pool.
        """
        if not math.isfinite(amount) or not math.isfinite(limit) or amount < 0 or limit < 0:
            raise ValueError("temporary shields require finite nonnegative amount and limit")
        if self.dead or amount == 0:
            return 0.0
        pool = self.temporary_shields.get(tag)
        if pool is None:
            if len(self.temporary_shields) >= POOLS - 1:
                raise ValueError("temporary shield pool slots exhausted")
            identifiers = [*self.ids.values(), *(p.pool_id for p in self.temporary_shields.values())]
            pool = self.temporary_shields[tag] = TemporaryShield(max(identifiers, default=0) + 1, 0, 0)
        given = min(amount, max(0.0, limit - pool.now))
        if given > 0:
            pool.now += given
            pool.most = pool.now
            self.changed.add(SHIELDS_POOL)
        return given

    def decay_temporary_shields(self, tag: int, amount: float) -> float:
        if not math.isfinite(amount) or amount < 0:
            raise ValueError("shield decay requires a finite nonnegative amount")
        pool = self.temporary_shields.get(tag)
        if pool is None:
            return 0.0
        taken = min(amount, pool.now)
        if taken > 0:
            pool.now -= taken
            if pool.now <= EPSILON:
                pool.now, pool.most = 0.0, 0.0
            self.changed.add(SHIELDS_POOL)
        return taken

    def damage(self, amount: float) -> float:
        """Take a hit from shields, then armour, then health (unverified, as OW1 did: what reaches armour
        while it has some is ARMOUR_BLOCK less, at most halved). Returns what the pools lost."""
        left = max(0.0, amount)
        taken = 0.0
        # Temporary shields are consumed before the body's ordinary shields.
        # Newer tagged grants precede older ones (reconstructed server policy).
        for tag in reversed(self.temporary_shields):
            take = self.decay_temporary_shields(tag, left)
            left -= take
            taken += take
        for kind in (SHIELDS_POOL, ARMOUR_POOL, HEALTH_POOL):
            if left <= 0.0:
                break
            if kind == ARMOUR_POOL and self.now[kind] > 0.0:
                left = max(left - ARMOUR_BLOCK, left / 2)
            take = min(left, self.now[kind])
            if take > 0.0:
                self.now[kind] -= take
                self.changed.add(kind)
                left -= take
                taken += take
        return taken

    def heal(self, amount: float) -> float:
        """Heal health, then armour, then shields (unverified: the order). Returns what it gave."""
        left = max(0.0, amount)
        for kind in (HEALTH_POOL, ARMOUR_POOL, SHIELDS_POOL):
            give = min(left, self.most[kind] - self.now[kind])
            if give > 0.0:
                self.now[kind] += give
                self.changed.add(kind)
                left -= give
        return max(0.0, amount) - left

    def component(self, kinds) -> dict:
        """Component 51 with these pools' fields set (the others are left as they are)."""
        parts: list = [None] * (3 * POOLS)
        for kind in kinds:
            if kind in self.ids:
                parts[kind * POOLS] = world.pool(self.ids[kind], kind, self.most[kind], self.now[kind])
        start = 1 if SHIELDS_POOL in self.ids else 0
        for slot, pool in enumerate(self.temporary_shields.values(), start):
            parts[SHIELDS_POOL * POOLS + slot] = world.pool(pool.pool_id, SHIELDS_POOL, pool.most, pool.now)
        return {51: parts}


@dataclass
class HitDef:
    """A hit definition evaluated: ModifyHealth's amount and loadout, and the volley's own fields."""

    amount: float = 0.0
    loadout: int | None = None
    falloff_start: float | None = None
    falloff_end: float | None = None
    falloff_floor: float | None = None
    critical: float = 1.0
    rings: tuple = ()  # (radius, scalar) of an explosion
    effect: int | None = None  # ModifyHealth's authored hit-effect resource
    deflectable: bool = False

    def scale(self, distance: float) -> float:
        """Unverified: full damage to falloff_start, falloff_floor from falloff_end on, a straight line
        between."""
        start, end, floor = self.falloff_start, self.falloff_end, self.falloff_floor
        if start is None or end is None or floor is None or end <= start or distance <= start:
            return 1.0
        if distance >= end:
            return floor
        return 1.0 + (floor - 1.0) * (distance - start) / (end - start)

    def splash(self, distance: float) -> float:
        """Unverified: an explosion's scalar at a distance: the first ring's inside it, a straight line from
        ring to ring, nothing past the last."""
        if not self.rings:
            return 0.0
        last_radius, last_scalar = self.rings[0]
        if distance <= last_radius:
            return last_scalar
        for radius, scalar in self.rings[1:]:
            if distance <= radius:
                share = (distance - last_radius) / max(EPSILON, radius - last_radius)
                return last_scalar + (scalar - last_scalar) * share
            last_radius, last_scalar = radius, scalar
        return 0.0


def _number(value, default=None):
    if value is None:
        return default
    try:
        return float(expr.to_float(value))
    except (TypeError, ValueError):
        return default


def hit_def(state, cfg) -> HitDef | None:
    """Evaluate a volley's hit definition (STU_00D9C7E1) or explosion (STU_4AA6CA44) with the shooter's
    body script."""
    if not isinstance(cfg, dict):
        return None

    def value(name, default=None):
        field = cfg.get(name)
        return default if field is None else _number(state.evaluate(field, watch=False), default)

    modify = cfg.get("m_modifyHealth") or {}
    amount = _number(state.evaluate(modify.get("m_amount"), watch=False), 0.0) if modify else 0.0
    loadout = state.evaluate(modify.get("m_loadout"), watch=False) if modify.get("m_loadout") else None
    effect = state.evaluate(modify.get("m_277A4ECA"), watch=False) if modify.get("m_277A4ECA") else None
    rings = []
    for ring in cfg.get("m_0D30E40C") or []:  # WeaponProjectileExplosionRing {m_radius, m_B399CACB}
        if isinstance(ring, dict):
            radius = (
                state.evaluate(ring.get("m_radius"), watch=False) if ring.get("m_radius") is not None else 0.0
            )
            scalar = ring.get("m_B399CACB")
            scalar = state.evaluate(scalar, watch=False) if scalar is not None else 0.0
            rings.append((_number(radius, 0.0), _number(scalar, 0.0)))
    return HitDef(
        amount=amount,
        loadout=loadout.guid if isinstance(loadout, expr.Asset) else None,
        falloff_start=value("m_EAD7F104"),
        falloff_end=value("m_157E5BC5"),
        falloff_floor=value("m_72A81154"),
        critical=value("m_A872C70E", 1.0),
        rings=tuple(sorted(rings)),
        effect=effect.guid if isinstance(effect, expr.Asset) else None,
        deflectable=any(
            (tag.get("m_EE3055BD") or {}).get("m_identifier") == "0x0D800000000003BF"
            for tag in modify.get("m_148F3152") or () if isinstance(tag, dict)
        ),
    )


# --- who can be hit -------------------------------------------------------------------------------------


def _source_entity(actor) -> int:
    """The combat actor's live entity ID."""
    return actor.entity if isinstance(actor, bots.Bot) else actor.body


@dataclass
class Target:
    entity: int
    team: int
    feet: tuple
    height: float
    radius: float
    eye: float
    owner: object  # a match.Player or a bots.Bot
    construct: object = None  # a separately damageable owned deployable

    def segment(self, feet=None):
        """The capsule's segment (from the feet to the top, a radius in from both ends)."""
        x, y, z = feet or self.feet
        return (x, y + self.radius, z), (x, y + max(self.radius, self.height - self.radius), z)


@dataclass
class Rocket:
    """A projectile in flight."""

    shooter: object
    team: int
    position: tuple
    direction: tuple
    speed: float
    left: float  # seconds of flight remaining
    direct: HitDef | None
    blast: HitDef | None
    shot: object = None  # its projectiles.Shot: told when it ends
    flown: float = 0.0  # seconds in the air
    last_tick: int | None = None  # last world tick, or None before player input is simulated
    gravity: float = 0.0  # reconstructed downward acceleration from E1A5B579/599AB8D7
    explode_on_expiry: bool = False  # reconstructed Total Mayhem fuse; other lifetime policies remain open
    mine: object = None  # registered sticky mine; impact parks it until a remote message
    source_entity: int | None = None  # body lease at launch,including post-death volleys
    distance_flown: float = 0.0  # trajectory length for direct-hit falloff
    radius: float = 0.0
    terrain_bounces: int = 0
    terrain_bounce_limit: int = 0
    terrain_bounce: tuple[float, float, float] | None = None
    grasp_eligible: bool = False
    max_distance: float | None = None  # independent cumulative flight limit for Junkrat's volleys
    definition: int | None = None  # authored projectile entity, for defense admission

    def __post_init__(self):
        if self.source_entity is None:
            self.source_entity = _source_entity(self.shooter)


class Combat:
    """The match's health, hits, deaths and respawns."""

    def __init__(self, match) -> None:
        self.match = match
        self.vitals: dict[int, Vitals] = {}  # body or bot entity -> its health
        self.history: dict[int, deque] = {}  # entity -> (tick, feet, height, eye) of the last HISTORY ticks
        self.replacement_ticks: dict[int, int] = {}  # replacement body -> authoritative world tick
        self.rockets: list[Rocket] = []
        self.respawns: list[tuple[float, object]] = []  # (when, a player or a bot)
        self.next_bot = bots.BOT_ENTITY + len(match.bots)
        self.now = 0.0
        self.tick = 0
        self.melee_seen: dict[int, dict[tuple[int, int], int]] = {}  # body -> counters by instance/state
        self.grasp_shields: dict[int, sigma_grasp.ShieldDecay] = {}
        self.grasp_grants: dict[int, tuple[int, int]] = {}
        self.npc_melee_pending: set[int] = set()
        self.npc_melee_seen: dict[int, tuple[int, int]] = {}
        self.heavy_charge_contacts: dict[int, set[int]] = {}
        self.heavy_charge_pins: dict[int, runtime.Instance] = {}
        self.pins: dict[int, int] = {}  # Assassin source body to controlled player body
        self.sombra_hacks = {}
        self.stuns: dict[int, int] = {}  # body identity -> exclusive world-tick deadline
        self.barriers = barriers.Barriers(self)
        self.junkrat = junkrat.Junkrat(self)
        self.projectiles = projectiles.Projectiles(self)  # leases, confirmations, the Biotic Field
        self.observers = observers.Observers(match)  # the bodies' statescript for the other clients
        self.voice = voicelines.VoiceLines(match)  # the hero's spawn voice line (voicelines.py)

    # --- hooks (match.py) --------------------------------------------------------------------------

    def is_dead(self, player) -> bool:
        vitals = self.vitals.get(player.body) if player.has_body else None
        return vitals is not None and vitals.dead

    def kill_feed_allowed(self, viewer, entity: int) -> bool:
        """First playback waits for the mode UI and uses the death's world clock."""
        vitals = self.vitals.get(entity)
        if not viewer.mode_script.delivered or vitals is None or vitals.death_tick is None:
            return False
        age = (self.match.tick - vitals.death_tick) * TICK * 1000
        return (
            0 <= age <= nodes.ClientOnlyPulser.FRESH_MS
            and viewer.client.combat_identity_ready(entity)
            and viewer.client.combat_identity_ready(vitals.killer)
        )

    def retire_player_body(self, player) -> None:
        """Discard body-local combat state when the player's current body retires."""
        sombra_hack.retire(self, player.body, player.body_script)
        self.barriers.retire(player.body)
        self.vitals.pop(player.body, None)
        self.history.pop(player.body, None)
        self.replacement_ticks.pop(player.body, None)
        self.melee_seen.pop(player.body, None)
        self.grasp_shields.pop(player.body, None)
        self.grasp_grants.pop(player.body, None)
        self.stuns.pop(player.body, None)
        self.clear_pins(player.body)
        self.junkrat.retire_body(player.body)
        # The owner's leased projectiles and their observer copies retire with the old body.
        self.rockets[:] = [
            rocket for rocket in self.rockets if rocket.shooter is not player or rocket.shot is None
        ]
        self.respawns[:] = [(when, who) for when, who in self.respawns if who is not player]

    def record_replacement(self, body: int) -> None:
        """Keep the creation boundary independent of the rolling pose history."""
        self.replacement_ticks[body] = self.match.tick
        self.stuns.pop(body, None)
        self.clear_pins(body)

    def is_stunned(self, actor, *, tick: int | None = None) -> bool:
        entity = _source_entity(actor)
        at = self.tick if tick is None else tick
        vitals = self.vitals.get(entity)
        return (entity in self.pins.values() or self.stuns.get(entity, 0) > at
                or (vitals is not None and vitals.sleep_until > at))

    def clear_pins(self, entity: int) -> None:
        for source, victim in list(self.pins.items()):
            if entity in (source, victim):
                self.release_pin(source)

    def release_pin(self, source: int) -> None:
        if source in self.heavy_charge_pins:
            heavy_charge.release(self, source)
        elif (victim := self.pins.pop(source, None)) is not None:
            assassin.detach_victim(self.match, source, victim)

    def _hit_effect(self, target: Target, effect: int | None) -> None:
        """Apply resource-backed body control with reconstructed interruption semantics."""
        if effect not in (FLASHBANG_EFFECT, SLEEP_DART_EFFECT) or target.construct is not None:
            return  # Constructs do not route control to their creator.
        vitals = self.vitals_of(target)
        if vitals.dead or immunity.control_immune(target.owner, target.entity):
            return
        stun_end = self.stuns.get(target.entity, 0)
        if effect == SLEEP_DART_EFFECT:
            floor = self.tick + math.ceil(SLEEP_MINIMUM / TICK)
            if floor <= max(vitals.sleep_until, stun_end):
                return
            self.stuns.pop(target.entity, None)
            vitals.sleep_until = self.tick + math.ceil(SLEEP_DURATION / TICK)
            vitals.sleep_wake_floor = floor
        else:
            variables = graph.mode_variables(self.match.game_map.mode_guid) or {}
            duration = 1.7 if expr.truthy(variables.get(FLASHBANG_MODE_FLAG)) else 0.8
            deadline = self.tick + math.ceil(duration / TICK)
            if vitals.sleep_until > self.tick:
                if deadline <= vitals.sleep_until:
                    return
                vitals.sleep_until = vitals.sleep_wake_floor = 0
            self.stuns[target.entity] = max(deadline, stun_end)
        self._interrupt(target)
        if isinstance(target.owner, bots.Bot) and target.owner.body_script is not None:
            component = target.owner.body_script.component
            assassin.bind(component.world, component, None)

    def _interrupt(self, target: Target) -> None:
        script = getattr(target.owner, "body_script", None)
        if script is None:
            return
        component = script.component
        dva_matrix.settle_natural_close(component, self.tick)
        component.edges([(value, False) for value in tuple(component.held)])
        dva_matrix.interrupt(component, self.tick)
        if isinstance(script, BotScript):
            script.abort_trooper_reload()
        if hasattr(script, "buttons"):
            script.buttons = 0  # driver edges must agree even if no input arrives during control
        for instance in list(component.instances.values()):
            for state in list(instance.states.values()):
                if isinstance(state, nodes.Ability):
                    state.presses.clear()
                    state.stop()  # use authored exit/cooldown paths for running channels
        component.run_now()
        component.shots.clear()
        sigma_flux.interrupt_flux_lift(self, target.owner)

    def spawned(self, player) -> None:
        """The player's client has the world: the game mode entity gets its UI graph (mode_frame). While
        heroes are assembled the countdown's frames carry it."""
        if player.client is not None and not self.match.assembling():
            player.client.queue_mode_frame(mode_frame(
                {}, player.match.game_map.mode_guid, player.match.game_map.map_guid,
            ))
        self.voice.spawned(player, self.now)

    def command(self, player, command: Command | None) -> None:
        """Consume the body's reports. Only a frame with a pose can affect targets."""
        if self.match.collision_world is None:
            return
        script = player.body_script
        if script is None:
            return
        if command is not None:
            self.junkrat.tire.begin(player, command)
        shots, script.component.shots[:] = list(script.component.shots), []
        dead = self.is_dead(player)
        busy = self.junkrat.tire.busy(player.body)
        stunned = self.is_stunned(player)
        if stunned:
            shots = []
        elif dead or busy:
            shots = [shot for shot in shots if shot[0].allows_dead()]
        if command is not None:
            for state, number, time, activation in shots:
                try:
                    self._shoot(player, command, state, number, time, activation=activation)
                except Exception:
                    log.exception("[game] %s: %s's shot failed", self.match.label(), player.name)
        if not dead and not busy and not stunned:
            try:
                self._melee(player, command, script)
            except Exception:
                log.exception("[game] %s: %s's melee failed", self.match.label(), player.name)

    def _melee(self, player, command, script) -> None:
        """Quick melee: 0043 st4 (StateModifyHealth) hits what its st5 TrackTargets finds: a sphere of
        m_radius 1.0 at (0, 0, 1.5) from the eye (DATA; unverified: along the aim), #476 = 30 damage (DATA),
        once per activation of st4. The server cannot run TrackTargets itself (the VM finds nothing)."""
        for instance in script.component.instances.values():
            spec = MELEE.get(instance.graph.index)
            if spec is None:
                continue
            index, reach, radius = spec
            state = instance.states.get(index)
            if state is None:
                continue
            counters = self.melee_seen.setdefault(script.entity, {})
            key = (instance.id, index)
            seen = counters.get(key, 0)
            counters[key] = state.counter
            if state.counter == seen or not state.active or command is None:
                continue
            modify = state.node.field("m_modifyHealth") or {}
            amount = _number(state.evaluate(modify.get("m_amount"), watch=False), 0.0) if modify else 0.0
            loadout = (
                state.evaluate(modify.get("m_loadout"), watch=False) if modify.get("m_loadout") else None
            )
            loadout = loadout.guid if isinstance(loadout, expr.Asset) else None
            facing = aim(*command.aim())
            centre = _along(self._eye(player), facing, reach)
            view_tick = command.frame - command.view_delay(player.quanta) * 1e6 / player.quanta
            protected = self.barriers.area(player, centre, radius, amount) if amount > 0 else set()
            for target in self._enemies(player):
                if target.entity in protected:
                    continue
                shown = self.seen(target, view_tick)
                if shown is None:
                    continue
                a, b = shown.segment()
                if (segment_distance(centre, a, b) - shown.radius <= radius and amount > 0.0
                        and capsule_visible(self._collision(), self._eye(player), a, b, shown.radius)
                        and not self._genji_deflects(target, facing)):
                    pinned = self.vitals_of(target) if target.entity in self.pins else None
                    before = pinned.total if pinned is not None else 0.0
                    self._damage(player, target, amount, False, loadout, command.frame)
                    if pinned is not None and pinned.total < before:
                        assassin.rescue_with_melee(self, player, target)

    def _genji_deflects(self, target, incoming) -> bool:
        return self._genji_deflect_state(target, incoming) is not None

    def _genji_deflect_state(self, target, incoming):
        owner = target.owner
        if (target.construct is not None or getattr(getattr(owner, "hero", None), "guid", None)
                != 0x02E0000000000029 or self.vitals_of(target).dead):
            return None
        script = getattr(owner, "body_script", None)
        if script is None or _dot(aim(owner.mover.yaw, owner.mover.pitch), incoming) >= -EPSILON:
            return None
        for instance in script.component.instances.values():
            if instance.graph.index == 0x03A3:
                state = instance.states.get(10)
                if state is not None and state.active:
                    return state
        return None

    def _genji_deflection(self, target, shown, origin, direction, reach, *, radius=0.0):
        state = self._genji_deflect_state(target, direction)
        if state is None:
            return None
        x, y, z = shown.feet
        mover = target.owner.mover
        return defense_boxes.intersect(state, (x, y + shown.eye, z), mover.yaw, mover.pitch,
                                       origin, direction, reach, radius=radius)

    def update(self, now: float, tick: int, frames: bool = True) -> None:
        """Each tick: keep where the bodies are, fly the rockets, respawn, send health that changed. The
        other players' body frames only on the ticks that send (`frames`)."""
        if self.match.collision_world is None:
            return
        from ow174.game import archives_thunderstorm

        sombra_hack.replay_orphaned_hacks(self, tick)
        sombra_hack.replay_emp(self, tick)
        retained = max(self.tick + 1, tick - COMBAT_CATCH_UP + 1)
        first = tick
        if self.tick and tick > self.tick + 1 and any(
            not bot.dead and isinstance(bot.body_script, BotScript)
            and (bot.firing or heavy_charge.clocked(bot)
                 or archives_thunderstorm.active_tracker(bot.body_script.component) is not None)
            for bot in self.match.bots
        ):
            first = retained
        if self.tick and (self.junkrat.traps or self.junkrat.tire.actors or self.junkrat.tire.recovery):
            first = retained  # deployed traps retain bounded arming/capture/release world ticks
        for player in self.match.players:
            self.barriers.sync(player)
        for target in self._targets():
            history = self.history.setdefault(target.entity, deque(maxlen=HISTORY))
            history.append((tick, target.feet, target.height, target.eye))
        def ballistic_flights():
            return (
                rocket for rocket in self.rockets
                if rocket.gravity and rocket.last_tick is not None
                and (rocket.mine is None or not rocket.mine.parked)
            )

        def next_deployable_tick(after):
            deadlines = [when for when, _ in self.junkrat.trap_deadlines()]
            deadlines.extend(self.junkrat.tire.deadlines())
            return min((when for when in deadlines if when >= after), default=first)

        # Replay known ballistic flights and deployable deadlines outside
        # the retained controller window. Admission uses the most recent poses.
        at = min(first, next_deployable_tick(self.tick + 1),
                 min((rocket.last_tick + 1 for rocket in ballistic_flights()), default=first))
        while at <= tick:
            self.now, self.tick = now - (tick - at) * TICK, at
            self.barriers.world.update(at)
            for rocket in list(self.rockets):
                if at == tick or rocket.last_tick is not None:
                    self._fly(rocket)
            if at >= first:
                self._bot_weapons(at)
                self.junkrat.update()
            else:
                if self.junkrat.traps:
                    due = [trap for when, trap in self.junkrat.trap_deadlines() if when == at]
                    self.junkrat.update_traps(due)
                if self.junkrat.tire.actors or self.junkrat.tire.recovery:
                    self.junkrat.tire.update(deadlines_only=True)
            at += 1
            if at < first and not any(ballistic_flights()):
                at = min(first, next_deployable_tick(at))
        sigma_grasp.update_shields(self)
        self.projectiles.update(tick, first_tick=retained)
        self.match.deliver_body_messages()
        if frames:
            self.observers.update()
        # A skipped tick can reach a respawn deadline with the old body's terminal pool still dirty.
        self._send_health()
        for bot in self.match.bots:
            if bot.dead and bot.retain_corpse:
                self._advance_bot_death(bot, tick)
        for when, who in list(self.respawns):
            if isinstance(who, bots.Bot) and who in self.match.bots:
                self._advance_bot_death(who, tick)
            if now >= when:
                self.respawns.remove((when, who))
                self._respawn(who)
        self.voice.expire(now)
        sombra_hack.update(self, tick)
        current = {target.entity for target in self._targets()}
        for entity in [key for key, deadline in self.stuns.items() if deadline <= tick]:
            del self.stuns[entity]
        for vitals in self.vitals.values():
            if 0 < vitals.sleep_until <= tick:
                vitals.sleep_until = vitals.sleep_wake_floor = 0
        for table in (self.history, self.vitals, self.replacement_ticks, self.stuns, self.grasp_grants):
            for entity in [key for key in table if key not in current]:
                del table[entity]
        for source, victim in list(self.pins.items()):
            if source not in current or victim not in current:
                self.clear_pins(source)

    # --- targets -----------------------------------------------------------------------------------

    def _targets(self) -> list[Target]:
        found = []
        for player in self.match.players:
            if not (player.has_body and player.spawned and player.client is not None):
                continue
            data = mover_data(player.hero.body)
            crouched = player.mover.crouched
            height = data.crouch_height if crouched else data.stand_height
            eye = eye_height(player.hero.body, crouched)
            found.append(
                Target(player.body, player.team, tuple(player.mover.position), height, data.stand_radius, eye,
                       player)
            )  # fmt: skip
        for bot in self.match.bots:
            data = mover_data(bot.body)
            found.append(
                Target(
                    bot.entity, bot.team, tuple(bot.position), data.stand_height, data.stand_radius, EYE, bot
                )
            )
        found.extend(self.junkrat.targets())
        return found

    def vitals_of(self, target: Target) -> Vitals:
        if target.construct is not None:
            return target.construct.vitals
        found = self.vitals.get(target.entity)
        if found is None:
            if isinstance(target.owner, bots.Bot):
                found = Vitals(target.entity, target.owner.health)
            else:
                found = Vitals(target.entity, *pools.body_pools(target.owner.hero.guid, target.owner.hero.body))
            self.vitals[target.entity] = found
        return found

    def _enemies(self, shooter) -> list[Target]:
        free_for_all = self.match.game_map.free_for_all
        source = _source_entity(shooter)
        found = []
        for target in self._targets():
            if target.owner is shooter or target.entity == source:
                continue
            if not free_for_all and target.team == shooter.team:
                continue
            if self.vitals_of(target).dead:
                continue
            found.append(target)
        return found

    def seen(self, target: Target, view_tick: float) -> Target | None:
        """Interpolate retained poses or use the nearest sample for a body that already existed."""
        replaced_at = self.replacement_ticks.get(target.entity)
        if replaced_at is not None and view_tick < replaced_at:
            return None
        history = self.history.get(target.entity)
        if not history:
            return target
        before = after = None
        for entry in history:
            if entry[0] <= view_tick:
                before = entry
            else:
                after = entry
                break
        if before is None:
            before = after
        if after is None or after is before:
            _, feet, height, eye = before
        else:
            share = (view_tick - before[0]) / (after[0] - before[0])
            feet = tuple(a + (b - a) * share for a, b in zip(before[1], after[1], strict=True))
            height, eye = (before[2], before[3]) if share < 0.5 else (after[2], after[3])
        return Target(
            target.entity, target.team, feet, height, target.radius, eye, target.owner, target.construct,
        )

    # --- shots -------------------------------------------------------------------------------------

    def _bot_weapons(self, tick: int) -> None:
        """Advance the weapon bodies of the map's firing bots and consume their volley reports."""
        from ow174.game import archives_thunderstorm, npc_control, sniper_reposition

        for bot in self.match.bots:
            script = bot.body_script
            controlled = npc_control.manual(bot)
            if ((not bot.firing and not controlled) or bot.dead
                    or not isinstance(script, BotScript) or script.frame > tick):
                continue
            try:
                charging = controlled and heavy_charge.clocked(bot)
                drive_step = False
                if charging:
                    charge_phase = heavy_charge.phase(heavy_charge.charge(bot))
                    if self.is_stunned(bot) or self.junkrat.rooted(bot.entity, tick):
                        heavy_charge.stop(bot, self)
                    elif script.frame < tick:
                        drive_step = heavy_charge.move(bot, self, tick)
                    component = script.component
                    if heavy_charge.requested(bot) and heavy_charge.BUTTON not in component.held:
                        component.edges([(heavy_charge.BUTTON, True)])
                    allowed = False
                else:
                    if controlled and script.frame < tick:
                        sniper_reposition.advance(self, bot)
                    allowed = npc_control.update_input(self, bot) if controlled else not self.is_stunned(bot)
                if bot.entity in self.npc_melee_pending:
                    self.npc_melee_pending.discard(bot.entity)
                    if not charging and not self.is_stunned(bot):
                        button = heavy_charge.MELEE_BUTTON
                        script.component.edges([(button, True), (button, False)])
                if controlled:
                    sigma_flux.sync_flux_airborne(script.component, bot.displacement)
                advanced = script.frame < tick
                snapshot = script.advance_live(tick, bot.position)
                if advanced:
                    archives_thunderstorm.advance(self, bot, script.component)
                if controlled:
                    sigma_flux.sync_npc_mods(bot, script.component, tick)
                if controlled and not charging and not self.is_stunned(bot):
                    heavy_charge.melee(bot, self)
                if charging:
                    instance = heavy_charge.charge(bot)
                    heavy_charge.body_impact(bot, self, instance)
                    heavy_charge.front_contact(bot, self, instance, drive_step=drive_step)
                    heavy_charge.side_contacts(bot, self, instance)
                    if instance is not None and not heavy_charge.driving(instance):
                        bot.velocity = (0.0, 0.0, 0.0)
                        if charge_phase and not heavy_charge.phase(instance):
                            heavy_charge.stop(bot, self)
                shots, script.component.shots[:] = list(script.component.shots), []
                for state, number, time, activation in shots:
                    if not allowed:
                        continue
                    self._shoot(
                        bot, None, state, number, time, activation=activation,
                        snapshot=snapshot or script.latest_live,
                    )
                if snapshot is not None:
                    for viewer in self.match.players:
                        if viewer.spawned and viewer.client is not None:
                            viewer.client.queue_bot_live(bot.entity, snapshot)
            except Exception:
                self.clear_pins(bot.entity)
                self.heavy_charge_contacts.pop(bot.entity, None)
                bot.body_script = None
                log.exception("[game] %s: %s's weapon script failed", self.match.label(), bot.name)

    def _eye(self, player) -> tuple:
        x, y, z = player.mover.position
        crouched = player.mover.crouched
        height = eye_height(player.hero.body, crouched)
        return (x, y + height, z)

    def _shoot(
        self, shooter, command, state, number: int, time: int,
        *, activation: tuple[int, int], snapshot: Snapshot | None = None,
    ) -> None:
        node = state.node
        shot_frame = time // state.instance.component.frame_ms

        def field(name, default=None):
            cfg = node.field(name)
            return default if cfg is None else _number(state.evaluate(cfg, watch=False), default)

        npc = isinstance(shooter, bots.Bot)
        if npc:
            x, y, z = shooter.position
            # Use the server body pose until the animated muzzle transform is available.
            origin, direction = (x, y + EYE, z), aim(round(shooter.yaw), round(shooter.pitch))
        else:
            origin, direction = self._eye(shooter), aim(*command.aim())
        direct = hit_def(state, node.field("m_6396149F"))
        speed = field("m_projectileSpeed")
        if not speed and (direct is None or direct.amount <= 0.0):
            return
        entity_def = node.field("m_projectileEntity.m_entityDef")
        definition = state.evaluate(entity_def, watch=False) if entity_def is not None else None
        if speed:
            gravity = 0.0
            for motion in node.field("m_projectileMotions") or ():
                if isinstance(motion, dict) and motion.get("$") == "STU_E1A5B579":
                    value = _number(state.evaluate(motion.get("m_599AB8D7"), watch=False), 0.0)
                    if math.isfinite(value) and value >= 0.0:
                        gravity += value
            lifetime = field("m_projectileLifetime", 5.0) or 5.0
            blast = hit_def(state, node.field("m_D2D11CE9"))
            reach = field("m_FEC435C6")
            max_distance = reach if reach and reach > 0 else None
            launch_tick = self.tick if command is not None and command.frame == self.tick else None
            if not npc and isinstance(definition, expr.Asset) and definition.guid == junkrat.MINE:
                self.junkrat.cast(shooter, state, origin, direction, speed, lifetime, gravity, blast,
                                  max_distance=max_distance, launch_tick=launch_tick)
                return
            if not npc and isinstance(definition, expr.Asset) and definition.guid == junkrat.TRAP_PROJECTILE:
                self.junkrat.cast_trap(shooter, state, origin, direction, speed, lifetime, gravity,
                                       max_distance=max_distance, launch_tick=launch_tick)
                return
            frag = state.instance.graph.index == 0x022B and node.pos == 31
            mayhem = state.instance.graph.index == 0x022A
            if not (frag or mayhem):
                max_distance = None
            if reach and max_distance is None:
                lifetime = min(lifetime, reach / speed)
            directions = launch_ring.projectile_directions(state, direction, number, activation)
            if directions is None:
                log.warning('[game] %s: unsupported multi-projectile launch %04X/%d',
                            self.match.label(), state.instance.graph.index, node.pos)
                return
            damaging = (direct is not None and direct.amount > 0.0) or (
                blast is not None and blast.amount > 0.0
            )
            sleep_dart = (isinstance(definition, expr.Asset) and definition.guid == SLEEP_DART_PROJECTILE
                          and direct is not None and direct.effect == SLEEP_DART_EFFECT)
            if sleep_dart:
                # Reconstruct defense admission for the authored 0BDD dart and its payload.
                direct = replace(direct, deflectable=True)
            grasp_eligible = frag or sleep_dart or (
                isinstance(definition, expr.Asset) and definition.guid in sigma_grasp.NPC_PROJECTILES
            )
            collision = node.field("m_8236947A") if frag else None
            radius = float(state.evaluate(collision["m_9655ED90"], watch=False)) if collision else 0.0
            bounce = node.field("m_E365DB20") if frag or mayhem else None
            terrain_bounce = (
                float(state.evaluate(bounce["m_0D20DAFD"], watch=False)),
                float(state.evaluate(bounce["m_CE33458D"], watch=False)) * 0.016,
                _number(state.evaluate(bounce.get("m_C29D35E8"), watch=False), 1.0),
            ) if bounce else None
            bounce_limit = int(state.evaluate(bounce["m_360294F0"], watch=False)) if bounce else 0
            for direction in directions:
                shot = None
                if isinstance(definition, expr.Asset):
                    shot = self.projectiles.fired(
                        shooter, state, shot_frame, definition.guid, lifetime, origin, direction,
                        activation=activation, snapshot=snapshot,
                    )
                if damaging or shot is not None:
                    self.rockets.append(
                        Rocket(
                            shooter, shooter.team, origin, direction, speed, lifetime, direct, blast, shot,
                            # NPC fire, death volleys and synchronized Frag Launcher commands have a world tick.
                            last_tick=self.tick if npc or mayhem else launch_tick if frag else None,
                            gravity=gravity,
                            explode_on_expiry=frag or mayhem,
                            max_distance=max_distance,
                            radius=radius,
                            terrain_bounce_limit=bounce_limit,
                            terrain_bounce=terrain_bounce,
                            grasp_eligible=grasp_eligible,
                            definition=definition.guid if isinstance(definition, expr.Asset) else None,
                        )
                    )
            return
        grasp_eligible = (
            isinstance(definition, expr.Asset) and definition.guid in sigma_grasp.HITSCAN_PROJECTILES
        )
        reach = field("m_FEC435C6", RANGE) or RANGE
        # The input supplies this simulation frame's aim and view time. Notices use the volley clock.
        view_tick = self.tick
        if command is not None:
            view_tick = command.frame - command.view_delay(shooter.quanta) * 1e6 / shooter.quanta
        rays = (direction,)
        if not npc and shooter.hero.guid == dva_matrix.HERO and (state.instance.graph.index, node.pos) == (0x050A, 6):
            from ow174.game import fusion_spread

            counter = activation[0] if activation is not None else state.counter
            rays = fusion_spread.volley_directions(state, direction, shooter.body, number, counter)
        for ray in rays:
            self._hitscan(shooter, origin, ray, reach, direct, view_tick, shot_frame,
                          grasp_eligible=grasp_eligible, world_tick=self.tick if npc else None,
                          definition=definition.guid if isinstance(definition, expr.Asset) else None)

    def _hitscan(self, shooter, origin, direction, reach, direct, view_tick, shot_frame,
                 *, grasp_eligible=False, world_tick=None, definition=None):
        """Resolve one independent ray with the existing damage and defense order."""
        travelled = 0.0
        reflectors = set()
        while travelled < reach:
            remaining = reach - travelled
            best = None
            for target in self._enemies(shooter):
                if immunity.phased_out(target.owner, target.entity):
                    continue
                shown = self.seen(target, view_tick)
                if shown is None:
                    continue
                a, b = shown.segment()
                t = ray_capsule(origin, direction, a, b, shown.radius)
                if t is not None and t <= remaining and (best is None or t < best[0]):
                    best = (t, target, shown, False)
                if direct.deflectable:
                    limit = remaining if best is None else best[0]
                    contact = self._genji_deflection(target, shown, origin, direction, limit)
                    if contact is not None and (best is None or contact < best[0]):
                        best = (contact, target, shown, True)
            reach_to_contact = remaining if best is None else best[0]
            barrier = self.barriers.intercept(shooter, origin, direction, reach_to_contact)
            distance = reach_to_contact if barrier is None else barrier[0]
            matrix = dva_matrix.intercept(self, shooter, definition, origin, direction, distance)
            if matrix is not None and (barrier is not None or best is not None) and matrix >= distance:
                matrix = None  # preserve the existing contact on a tie
            grasp = None
            if grasp_eligible:
                grasp_limit = distance if matrix is None else matrix
                grasp = sigma_grasp.grasp_intersection(self, shooter, origin, direction, grasp_limit)
                if grasp is not None and barrier is not None and grasp[0] >= distance:
                    grasp = None
            matrix_first = matrix is not None and (grasp is None or matrix <= grasp[0])
            if not matrix_first and grasp is None and barrier is None and best is None:
                return
            if matrix_first:
                distance = matrix
            elif grasp is not None:
                distance = grasp[0]
            wall = ray_world(self._collision(), origin, direction, distance)
            if wall is not None and wall < distance:
                return
            if matrix_first:
                return
            if grasp is not None:
                sigma_grasp.absorbed(grasp[1], direct.amount * direct.scale(travelled + distance))
                return
            if barrier is not None:
                self.barriers.damage(barrier[1], direct.amount * direct.scale(travelled + distance), shooter)
                return
            t, target, shown, deflected = best
            travelled += t
            if deflected:
                # Terminate cycles, including coincident frontal capsules.
                if target.entity in reflectors:
                    return
                reflectors.add(target.entity)
                shooter = target.owner
                origin = _along(origin, direction, t)
                direction = aim(shooter.mover.yaw, shooter.mover.pitch)
                world_tick = view_tick = shot_frame = self.tick
                continue
            hit_y = origin[1] + direction[1] * t
            critical = target.construct is None and hit_y >= shown.feet[1] + shown.eye - HEAD_BELOW
            amount = direct.amount * direct.scale(travelled) * (direct.critical if critical else 1.0)
            self._damage(
                shooter, target, amount, critical and direct.critical > 1.0, direct.loadout, shot_frame,
                world_tick=world_tick,
            )
            self._hit_effect(target, direct.effect)
            return

    def _fly(self, rocket: Rocket) -> None:
        """Sweep elapsed world time against the current bodies and static collision."""
        if rocket.mine is not None and rocket.mine.parked:
            return
        # Player input can arrive immediately before the scheduler accounts for a gap.
        elapsed = 1 if rocket.last_tick is None else self.tick - rocket.last_tick
        if elapsed <= 0:
            return
        rocket.last_tick = self.tick
        self._fly_step(rocket, min(elapsed * TICK, rocket.left))

    def _fly_step(self, rocket: Rocket, duration: float) -> None:
        range_left = math.inf if rocket.max_distance is None else rocket.max_distance - rocket.distance_flown
        if duration <= 0.0 or range_left <= 0.0:
            self._expire_rocket(rocket)
            return
        # Sweep the remaining flight even when the range or lifetime ends inside this tick.
        available_duration = duration
        velocity = tuple(axis * rocket.speed for axis in rocket.direction)
        displacement = tuple(
            axis * duration - (0.5 * rocket.gravity * duration * duration if index == 1 else 0.0)
            for index, axis in enumerate(velocity)
        )
        step = math.sqrt(_dot(displacement, displacement))
        direction = tuple(axis / step for axis in displacement) if step > EPSILON else rocket.direction
        # D72C80 clips the chord to the available distance and scales its elapsed time.
        reaches_range = step >= range_left
        if step > range_left:
            duration *= range_left / step
            step = range_left
        best = None
        deflection = None
        deflectable = rocket.direct is not None and rocket.direct.deflectable
        for target in self._enemies(rocket.shooter) if rocket.mine is None else ():
            if immunity.phased_out(target.owner, target.entity):
                continue
            a, b = target.segment()
            t = ray_capsule(rocket.position, direction, a, b, target.radius + rocket.radius)
            if t is not None and t <= step and (best is None or t < best[0]):
                best = (t, target)
            if deflectable:
                limit = step if deflection is None else deflection[0]
                contact = self._genji_deflection(target, target, rocket.position, direction, limit,
                                                 radius=rocket.radius)
                if contact is not None and (deflection is None or contact < deflection[0]):
                    deflection = (contact, target)
        # Include a rounded endpoint: otherwise the following sweep starts on the
        # surface and the static ray query deliberately ignores that zero-distance hit.
        collision = self._collision()
        normal = None
        if rocket.radius and step > EPSILON:
            hit = contacts.scene_sweep(collision.engine, rocket.position,
                                       _along(rocket.position, direction, step + 1e-6),
                                       ((0.0, 0.0, 0.0),), rocket.radius, MOVER)
            wall = None if hit is None else hit[0] * (step + 1e-6)
            if hit is not None:
                normal = hit[2]
        else:
            hit = collision.cast_ray(rocket.position, direction, step + 1e-6, MOVER)
            wall = hit.distance if hit is not None else None
            if hit is not None:
                normal = hit.normal
        if wall is not None and (best is None or wall < best[0]):
            best = (min(wall, step), None)
        if rocket.mine is None:
            limit = step if best is None else best[0]
            barrier = self.barriers.intercept(rocket.shooter, rocket.position, direction, limit,
                                              radius=rocket.radius)
            if barrier is not None and (best is None or barrier[0] < limit):
                best = barrier
        deflected = deflection is not None and (best is None or deflection[0] < best[0])
        if deflected:
            best = deflection
        limit = step if best is None else best[0]
        matrix = dva_matrix.intercept(self, rocket.shooter, rocket.definition, rocket.position, direction, limit)
        if matrix is not None and best is not None and matrix >= best[0]:
            matrix = None  # a coincident body, wall or other defense keeps its existing priority
        if rocket.grasp_eligible:
            grasp_limit = limit if matrix is None else matrix
            intercepted = sigma_grasp.grasp_intersection(
                self, rocket.shooter, rocket.position, direction, grasp_limit,
            )
            if intercepted is not None and (matrix is None or intercepted[0] < matrix):
                distance, grasp = intercepted
                amount = sum(max(0.0, hit.amount) for hit in (rocket.direct, rocket.blast) if hit is not None)
                rocket.flown += duration * distance / max(EPSILON, step)
                rocket.distance_flown += distance
                self._landed(rocket)
                sigma_grasp.absorbed(grasp, amount)
                return
        if matrix is not None:
            rocket.flown += duration * matrix / max(EPSILON, step)
            rocket.distance_flown += matrix
            if rocket.mine is None:
                self._landed(rocket)
            else:
                self.junkrat.retire(rocket.mine)
            return
        if best is None:
            rocket.position = _along(rocket.position, direction, step)
            if rocket.gravity:
                velocity = (velocity[0], velocity[1] - rocket.gravity * duration, velocity[2])
                rocket.speed = math.sqrt(_dot(velocity, velocity))
                if rocket.speed > EPSILON:
                    rocket.direction = tuple(axis / rocket.speed for axis in velocity)
            rocket.flown += duration
            rocket.distance_flown += step
            rocket.left -= duration
            if rocket.left <= 0.0 or reaches_range:
                self._expire_rocket(rocket)
            return
        t, struck = best
        travel = duration * t / max(EPSILON, step)
        rocket.flown += travel
        rocket.distance_flown += t
        if rocket.mine is not None:
            rocket.position = _along(rocket.position, direction, max(0.0, t - 1e-4))
            rocket.mine.parked = True
            rocket.mine.parked_at = self.now
            return
        point = _along(rocket.position, direction, t)
        if isinstance(struck, (barriers.Bubble, world_bubbles.FixedBubble, reinhardt_barrier.RaisedBarrier)):
            self._landed(rocket)
            if rocket.direct is not None:
                self.barriers.damage(struck, rocket.direct.amount * rocket.direct.scale(rocket.distance_flown),
                                     rocket.shooter)
            visible_from = point
            if isinstance(struck, (world_bubbles.FixedBubble, reinhardt_barrier.RaisedBarrier)):
                visible_from = _along(point, direction, -1e-4)
            self._explode(rocket, point, struck_barrier=struck, visible_from=visible_from)
            return
        if deflected:
            if rocket.shot is not None:
                self.projectiles.flown(rocket.shot, rocket.flown)
                rocket.shot = None
            rocket.shooter, rocket.team = struck.owner, struck.team
            rocket.source_entity = struck.entity
            rocket.direction = aim(struck.owner.mover.yaw, struck.owner.mover.pitch)
            rocket.position = _along(point, rocket.direction, 1e-4)
            rocket.left -= travel
            return
        if struck is None and normal is not None and rocket.terrain_bounce is not None:
            if _dot(normal, direction) > 0.0:
                normal = tuple(-axis for axis in normal)
            impact = (velocity[0], velocity[1] - rocket.gravity * travel, velocity[2])
            approach = _dot(impact, normal)
            outgoing = impact
            if approach < 0.0:
                # Native D77B80 uses separate normal restitution and tangent losses.
                restitution, tangent_loss, tangent_retention = rocket.terrain_bounce
                tangent = _along(impact, normal, -approach)
                tangent_speed = math.hypot(*tangent)
                tangent_scale = tangent_retention * max(1.0 - tangent_loss / tangent_speed, 0.0) \
                    if tangent_speed else 0.0
                outgoing = _along(tuple(axis * tangent_scale for axis in tangent), normal, -approach * restitution)
            rocket.speed = math.hypot(*outgoing)
            if rocket.speed > EPSILON:
                rocket.direction = tuple(axis / rocket.speed for axis in outgoing)
            rocket.position = _along(point, normal, 1e-3)
            rocket.left -= travel
            rocket.terrain_bounces += 1
            # The native check is strict and runs after updating the bounce response.
            if rocket.terrain_bounces > rocket.terrain_bounce_limit:
                rocket.speed = 0.0
                self._landed(rocket)
                return
            if rocket.left <= 0.0 or (reaches_range and t == step) or available_duration > travel:
                self._fly_step(rocket, available_duration - travel)
            return
        self._landed(rocket)
        if struck is not None and rocket.direct is not None and rocket.direct.amount > 0.0:
            self._damage(
                rocket.shooter, struck, rocket.direct.amount * rocket.direct.scale(rocket.distance_flown),
                False, rocket.direct.loadout, self.tick,
                world_tick=self.tick, source_entity=rocket.source_entity,
            )
            self._hit_effect(struck, rocket.direct.effect)
        if struck is None:
            # Keep the explosion on the incoming side of the contacted triangle.
            point = _along(point, direction, -1e-4)
        self._explode(rocket, point)

    def _explode(self, rocket: Rocket, point, *, struck_barrier=None, visible_from=None) -> set[int]:
        blast = rocket.blast
        if blast is None or blast.amount <= 0.0:
            return set()
        protected = self.barriers.explosion(rocket.shooter, point, blast, struck_barrier, visible_from=visible_from)
        if isinstance(struck_barrier, barriers.Bubble):
            protected.add(struck_barrier.body)
        for target in self._enemies(rocket.shooter):
            if target.entity in protected:
                continue
            a, b = target.segment()
            distance = max(0.0, segment_distance(point, a, b) - target.radius)
            scalar = blast.splash(distance)
            if scalar > 0.0 and self._blast_visible(point, target):
                self._damage(
                    rocket.shooter, target, blast.amount * scalar, False, blast.loadout, self.tick,
                    world_tick=self.tick, source_entity=rocket.source_entity,
                )
                self._hit_effect(target, blast.effect)
        return protected

    def _blast_visible(self, point, target: Target) -> bool:
        """Reconstructed static occlusion to the nearest point on the body capsule's axis.

        Barrier entities and native explosion visibility sampling remain separate gaps.
        Static impacts supply a point just on the incoming side of the contacted surface.
        """
        a, b = target.segment()
        offset = _sub(b, a)
        length = _dot(offset, offset)
        fraction = 0.0 if length <= EPSILON else max(0.0, min(1.0, _dot(_sub(point, a), offset) / length))
        centre = _along(a, offset, fraction)
        toward = _sub(centre, point)
        distance = math.sqrt(_dot(toward, toward))
        if distance <= target.radius:
            return True
        direction = tuple(axis / distance for axis in toward)
        # Test up to the capsule surface; geometry behind the body cannot shield it.
        wall = ray_world(self._collision(), point, direction, distance - target.radius)
        return wall is None

    def _expire_rocket(self, rocket: Rocket) -> None:
        if rocket.mine is not None:
            if rocket.explode_on_expiry:
                self.junkrat.detonate(rocket.mine)
            else:
                self.junkrat.retire(rocket.mine)
            return
        self._landed(rocket)
        if rocket.explode_on_expiry:
            self._explode(rocket, rocket.position)

    def _landed(self, rocket: Rocket) -> None:
        """The projectile ended (hit, range, lifetime): the predicted one's release follows."""
        if rocket in self.rockets:
            self.rockets.remove(rocket)
        if rocket.shot is not None:
            self.projectiles.flown(rocket.shot, rocket.flown)

    def heal_area(self, healer, centre, radius: float, amount: float) -> None:
        """Heal the healer and his teammates (everyone's own team in a free-for-all is his alone) whose body
        is within `radius` of `centre` (unverified: to the body's capsule)."""
        for target in self._targets():
            if target.construct is not None:
                continue  # these destructible mines are not healable hero bodies
            ally = not self.match.game_map.free_for_all and target.team == healer.team
            if target.owner is not healer and not ally:
                continue
            vitals = self.vitals_of(target)
            if vitals.dead:
                continue
            a, b = target.segment()
            if segment_distance(centre, a, b) - target.radius > radius:
                continue
            given = vitals.heal(amount)
            if given <= 0.0:
                continue
            healer.stats.add(stats.HEALING, given)
            self._sync_body_health(target.owner, vitals)

    @staticmethod
    def _sync_body_health(owner, vitals: Vitals) -> None:
        script = getattr(owner, "body_script", None)
        if script is not None:
            script.set_health(vitals.total, vitals.maximum)

    def _collision(self):
        from ow174.game.collision import CollisionError

        world = self.match.collision_world
        if world is None:
            raise CollisionError("match collision is not ready")
        return world

    # --- damage and death --------------------------------------------------------------------------

    def _damage(
        self, shooter, target: Target, amount: float, critical: bool, loadout, frame: int,
        *, world_tick: int | None = None, source_entity: int | None = None,
    ) -> None:
        vitals = self.vitals_of(target)
        if (vitals.dead or amount <= 0.0 or immunity.phased_out(target.owner, target.entity)
                or immunity.cryo_active(target.owner, target.entity)):
            return
        dealt = vitals.damage(amount * immunity.damage_scale(target.owner, target.entity))
        if dealt <= 0.0:
            return
        self.junkrat.charge.damage(shooter, target, dealt, loadout, source_entity)
        if target.construct is not None:
            killed = vitals.total <= EPSILON
            if not isinstance(shooter, bots.Bot):
                self._tell_hit(shooter, target, dealt, KILLING if killed else 0, frame)
            if killed:
                vitals.died_at = self.now
                vitals.death_tick = self.tick if world_tick is None else world_tick
                vitals.killer = _source_entity(shooter)
                self.junkrat.retire(target.construct)
            return
        killed = vitals.now[HEALTH_POOL] <= EPSILON
        if killed:
            vitals.now[HEALTH_POOL] = 0.0
            vitals.died_at = self.now
            vitals.death_tick = self.match.tick if world_tick is None else world_tick
            vitals.killer = _source_entity(shooter)
            vitals.sleep_until = vitals.sleep_wake_floor = 0
        elif vitals.sleep_until > self.tick:
            wake = max(vitals.sleep_wake_floor, self.tick + math.ceil(SLEEP_WAKE_RECOVERY / TICK))
            vitals.sleep_until = min(vitals.sleep_until, wake)
        if not isinstance(shooter, bots.Bot):
            flags = (CRITICAL if critical else 0) | (KILLING if killed else 0)
            self._tell_hit(shooter, target, dealt, flags, frame)
            shooter.stats.add(stats.HERO_DAMAGE, dealt)
        victim = target.owner
        if killed and isinstance(victim, bots.Bot):
            # The NPC death script owns its terminal health change and death event ordering.
            self._bot_died(shooter, victim, critical, loadout, vitals.death_tick)
            return
        self._sync_body_health(victim, vitals)
        if killed:
            self._died(shooter, victim, critical, loadout)

    def _tell_hit(self, shooter, target: Target, dealt: float, flags: int, frame: int) -> None:
        """25001 to the shooter: the hit marker and the target's health bar (frame: the shot's)."""
        value = {
            "+0x78": {"+0x0": {"+0x0": target.entity}, "+0x4": max(1, round(dealt)), "+0x8": flags},
            "+0x84": frame & 0xFFFFFFFF,
        }
        self._queue_notice(shooter, target.owner, target.entity, HIT, value)

    def _kill_notice(self, killer, victim) -> None:
        """25005 to the killer: "ELIMINATED: <name>" (1126.07C; +0x89 assist would be 01F0.07C, +0x88 a
        score shown when above 0). Its handler (0x7FF7895D2B50) reads only +0x84, +0x88 and +0x89."""
        entity = _source_entity(victim)
        notice = {
            "+0x78": 0,
            "+0x80": {"+0x0": killer.body},
            "+0x84": {"+0x0": entity},
            "+0x88": 0,
            "+0x89": False,
        }
        self._queue_notice(killer, victim, entity, KILL_NOTICE, notice)

    def _queue_notice(self, viewer, actor, entity: int, msg_id: int, value: dict) -> None:
        client = getattr(viewer, "client", None)
        if client is None:
            return
        if not isinstance(actor, bots.Bot) and actor.body == entity:
            self.match.ensure_remote_body(viewer, actor)
        client.queue_combat_notice(entity, msg_id, value)

    @staticmethod
    def _death_parameters(killer, critical: bool, loadout) -> dict:
        return {
            KILLER: expr.Entity(_source_entity(killer)),
            CRIT: bool(critical),
            LOADOUT: expr.Asset(loadout) if loadout else None,
            ASSISTS: (),
        }

    def _bot_died(self, killer, bot: bots.Bot, critical: bool, loadout, tick: int) -> None:
        self.stuns.pop(bot.entity, None)
        self.clear_pins(bot.entity)
        self.heavy_charge_contacts.pop(bot.entity, None)
        self.npc_melee_pending.discard(bot.entity)
        self.npc_melee_seen.pop(bot.entity, None)
        bot.died(tick)
        self.junkrat.release_trap_victim(bot.entity)
        if not bot.retain_corpse:
            self.respawns.append((self.now + BOT_RESPAWN, bot))
        if not isinstance(killer, bots.Bot):
            killer.stats.add(stats.ELIMINATIONS)
            self._kill_notice(killer, bot)
        log.info("[game] %s: %s killed %s", self.match.label(), killer.name, bot.name)
        script = bot.body_script
        if script is None:
            return
        try:
            params = self._death_parameters(killer, critical, loadout)
            if isinstance(script, BotScript):
                tick = max(tick, script.frame)
            event = script.die(tick, params, _source_entity(killer))
            if isinstance(script, BotScript):
                self._consume_bot_death_volleys(bot, script)
        except Exception:
            bot.body_script = None
            log.exception("[game] %s: %s's body script failed on %X", self.match.label(), bot.name, DIED)
            return
        for viewer in self.match.players:
            if viewer.client is not None:
                viewer.client.queue_bot_death(bot.entity, event)

    def _consume_bot_death_volleys(self, bot, script):
        reports, script.component.shots[:] = list(script.component.shots), []
        for state, number, time, activation in reports:
            if (state.instance.graph.index, state.node.pos) == (DEATH_VOLLEY_GRAPH, 0) and state.allows_dead():
                self._shoot(bot, None, state, number, time, activation=activation)

    def _advance_bot_death(self, bot: bots.Bot, tick: int) -> None:
        script = bot.body_script
        if script is None:
            return
        try:
            events = script.advance_death(tick, int(BOT_RESPAWN / TICK))
            if isinstance(script, BotScript):
                self._consume_bot_death_volleys(bot, script)
        except Exception:
            bot.body_script = None
            log.exception("[game] %s: %s's death script timer failed", self.match.label(), bot.name)
            return
        for event in events:
            for viewer in self.match.players:
                if viewer.client is not None:
                    viewer.client.queue_bot_death_update(bot.entity, event)

    def _died(self, killer, victim, critical: bool, loadout) -> None:
        """A player died: the notices, his body script's "died" (the kill feed line), and the respawn."""
        sombra_hack.retire(self, victim.body, victim.body_script)
        self.stuns.pop(victim.body, None)
        self.grasp_shields.pop(victim.body, None)
        sigma_flux.interrupt_flux_lift(self, victim)
        self.clear_pins(victim.body)
        self.junkrat.release_trap_victim(victim.body)
        label = self.match.label()
        log.info("[game] %s: %s killed %s", label, killer.name, victim.name)
        victim.stats.add(stats.DEATHS)
        bot_killer = isinstance(killer, bots.Bot)
        source = _source_entity(killer)
        if killer is not victim and not bot_killer:
            killer.stats.add(stats.ELIMINATIONS)
            self._kill_notice(killer, victim)
        if victim.client is not None:
            how = KILLED_BY if killer is not victim else 0
            notice = {"+0x78": how, "+0x7C": {"+0x0": source}}
            if how == KILLED_BY:
                self._queue_notice(victim, killer, source, DEATH_NOTICE, notice)
            else:  # the native "you died" event does not resolve a killer
                victim.client.queue_combat_notice(None, DEATH_NOTICE, notice)
        script = victim.body_script
        if script is not None:
            try:
                component = script.component
                params = self._death_parameters(killer, critical, loadout)
                if runtime.send_message(component, DIED, params, source):
                    # The server owns the death event. Run it before the observer snapshot even
                    # if the victim sends no further commands.
                    component.run_now()
                    # Terminal volleys do not depend on another command arriving
                    # from the dead player. Ordinary volleys are still rejected.
                    script._report_shots()
                    self.command(victim, Command(
                        self.tick, yaw=round(victim.mover.yaw), pitch=round(victim.mover.pitch),
                    ))
            except Exception:
                log.exception("[game] %s: %s's body script failed on %X", label, victim.name, DIED)
        self.respawns.append((self.now + respawn_seconds(self.match), victim))

    def _respawn(self, who) -> None:
        if isinstance(who, bots.Bot):
            self._respawn_bot(who)
            return
        player = who
        if player.gone or not player.has_body or not self.is_dead(player):
            return
        if self.junkrat.tire.busy(player.body):
            self.respawns.append((self.now + RESPAWN_RETRY, player))
            return
        if not self.match.switch_hero(player, player.hero):
            self.respawns.append((self.now + RESPAWN_RETRY, player))
            return
        self.voice.spawned(player, self.now)
        log.info("[game] %s: %s respawned", self.match.label(), player.name)

    def _respawn_bot(self, bot) -> None:
        """A new bot from the same spawner under a new entity (the client ignores a destroyed id for 30 s);
        the dead one goes."""
        match = self.match
        if bot not in match.bots or bot.retain_corpse:
            return
        entity = self.next_bot
        self.next_bot += 1
        fresh = bots.respawned(bot, entity, match.tick + match_record_delay())
        match._prepare_bot_script(fresh)
        match.bots[match.bots.index(bot)] = fresh
        self.vitals.pop(bot.entity, None)
        self.record_replacement(fresh.entity)
        for viewer in match.players:
            if viewer.client is None or match.bots_shown.get(viewer.slot) is None:
                continue
            viewer.client.queue_entities(
                [
                    world.EntityUpdate(bot.entity, OP_DESTROY),
                    match._bot_create_update(fresh),
                ]
            )
        log.info("[game] %s: %s respawned as %08X", match.label(), fresh.name, fresh.entity)

    # --- what the clients get ----------------------------------------------------------------------

    def _send_health(self) -> None:
        """Send the latest complete pool snapshot for each body that changed."""
        for entity, vitals in list(self.vitals.items()):
            if not vitals.changed:
                continue
            # A replacement covers prior unacknowledged changes in any pool.
            components = vitals.component(vitals.ids)
            vitals.changed.clear()
            for viewer in self.match.players:
                client = viewer.client
                if client is not None and viewer.spawned:
                    client.queue_body_health(entity, components)


def match_record_delay() -> int:
    from ow174.game import match as match_module

    return match_module.BOT_RECORD_DELAY


def respawn_seconds(match) -> float:
    """The mode's respawn time for heroes (DATA: the mode graphs' 06FD.025 to the body script 0C8E)."""
    from ow174.game import heroselect

    return PRACTICE_RESPAWN if match.controller is heroselect.PRACTICE else RESPAWN


def mode_frame(entity_vars: dict, mode_guid: int | None = None, map_guid: int | None = None) -> BitWriter:
    """A plain full frame of the game mode entity: the mode's client UI graph 0CB6 as instance 1 (its
    client-only Entries make the kill feed 0DEA and the notices 1FC0) and the entity variables given (the
    PvP countdown). Every full frame of this entity has to list 0CB6, or it would destroy it."""
    ui = graph.graph(MODE_UI)
    # EntryClientOnlySharedVars starts the mode UI's sole remote state, st21.
    shared = ui.state(21)
    instance = InstanceWire(1, ui, states={21: StateWire(shared.cls, True)})
    values = {var: (value, ()) for var, value in entity_vars.items()}
    from ow174.game import pve

    entrance = pve.entrance(mode_guid, map_guid) if mode_guid is not None else None
    if entrance is None:
        return remote_full_frame([instance], values, {1: ui})
    primary = entrance.graph
    states = {}
    if entrance.ready_state is not None:
        states[entrance.ready_state] = StateWire(pve.READY_CLASS, True)
    # Keep the existing presentation root. The mode's common-UI SubScript is
    # not remote-synced, so an H=0 frame cannot activate it. Mission execution
    # will own that relationship when its server runtime is implemented.
    instances, graphs = [instance], {1: ui}
    if entrance.mode_graph is not None:
        instances.append(InstanceWire(2, entrance.mode_graph))
        graphs[2] = entrance.mode_graph
    root_id = len(instances) + 1
    instances.append(InstanceWire(root_id, primary, states=states))
    graphs[root_id] = primary
    return remote_full_frame(instances, values, graphs)
