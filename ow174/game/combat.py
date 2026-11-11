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
  command's aim from the eye (STUFirstPersonComponent m_E29993A5 = 1.55 m, crouched m_816B830F, DATA;
  unverified as the eye height), or a projectile (m_projectileSpeed) flown each tick.
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
from dataclasses import dataclass, field, replace

from ow174.game import (
    ana_grenade,
    bots,
    breather,
    dva_matrix,
    enforcer,
    genji_deflect,
    heavy_charge,
    hook,
    mei,
    mei_blizzard,
    mei_frost,
    mei_ultimate,
    mei_wall,
    nano_boost,
    npc_context,
    npc_melee,
    observers,
    orisa_barrier,
    phase_defenses,
    photon_barrier,
    pools,
    projectiles,
    reinhardt_barrier,
    roadhog_projectiles,
    sigma_accretion,
    sigma_barrier,
    sigma_flux,
    sigma_grasp,
    sigma_hypersphere,
    sniper_reposition,
    stats,
    symmetra_beam,
    symmetra_sentry,
    voicelines,
    whole_hog,
    winston_barrier,
    world,
    zarya_shields,
)
from ow174.game.bits import BitWriter
from ow174.game.collision import MOVER, World
from ow174.game.commands import Command
from ow174.game.mover import mover_data
from ow174.game.script import expr, graph, nodes, runtime
from ow174.game.script.bot import DIED, BotScript
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
EYE_STANDING = {0x04000000000008BA: 1.3, 0x04000000000002A3: 1.425}
EYE_CROUCHED = {0x04000000000003CF: 1.085, 0x04000000000008BA: .91, 0x04000000000002A3: .9975}
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
BEAM_ARMOUR_SCALE = 0.7  # Photon Projector static armour reconstruction.
ARMOUR_BLOCK = 5.0  # damage armour stops per hit (unverified: OW1's rule; the client has no damage code)
TICK = 0.016
EPSILON = 1e-9
FLASHBANG_EFFECT = 0x04A0000000000079  # .053/0079 ->0228 ->224E
FLASHBANG_MODE_FLAG = 13408  # 0228 duration: flag set1.7s, otherwise0.8s
SLEEP_DART_EFFECT = 0x04A000000000026F  # 026F -> 0BDE -> 224E
SLEEP_DURATION, SLEEP_WAKE_FLOOR, SLEEP_WAKE_RECOVERY = 5., 1.5, .5


# --- geometry -----------------------------------------------------------------------------------------


def eye_height(body: int, crouched: bool = False) -> float:
    """Declared104319 FirstPerson offsets; retain existing unknown-body fallback."""
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
    """Reconstructed melee policy: a static ray to the nearest capsule surface."""
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
        self.healing_block_until = 0.
        self.healing_boost_until = 0.
        self.nano_until = 0.
        self.sleep_until = 0
        self.sleep_wake_floor = 0

    def clear_sleep(self) -> None:
        self.sleep_until = self.sleep_wake_floor = 0

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
            if pool.now <= expr.EPSILON:
                pool.now, pool.most = 0.0, 0.0
            self.changed.add(SHIELDS_POOL)
        return taken


    def damage(self, amount: float, *, beam: bool = False) -> float:
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
                left = left * BEAM_ARMOUR_SCALE if beam else max(left - ARMOUR_BLOCK, left / 2)
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
        if SHIELDS_POOL in kinds:
            start = 1 if SHIELDS_POOL in self.ids else 0
            for slot, pool in enumerate(self.temporary_shields.values(), start):
                parts[SHIELDS_POOL * POOLS + slot] = world.pool(
                    pool.pool_id, SHIELDS_POOL, pool.most, pool.now,
                )
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

    knockback: tuple | None = None

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
        # Mei's icicle omits the multiplier in the extracted 104319 graph. Reconstruct
        # its 2x headshot rule locally; this is not a measured native default for all
        # STU_00D9C7E1 records. The spray explicitly supplies 1 and remains noncritical.
        critical=value("m_A872C70E", 2.0 if (
            state.instance.graph.index == 0x05E6 and state.index == 16
        ) else 1.0),
        rings=tuple(sorted(rings)),
        knockback=sigma_accretion.projectile_knockback(state, modify),
        effect=effect.guid if isinstance(effect, expr.Asset) else None,
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

    def segment(self, feet=None):
        """The capsule's segment (from the feet to the top, a radius in from both ends)."""
        x, y, z = feet or self.feet
        return (x, y + self.radius, z), (x, y + max(self.radius, self.height - self.radius), z)


@dataclass
class ScrapBurst:
    directions: tuple
    speed: float
    lifetime: float
    hit: HitDef | None
    blast: HitDef | None
    definition: int | None


@dataclass
class Rocket:
    """A projectile in flight."""

    shooter: object
    team: int
    position: tuple
    direction: tuple
    speed: float
    left: float  # seconds of life: its lifetime, or its range at its speed
    direct: HitDef | None
    blast: HitDef | None
    shot: object = None  # its projectiles.Shot: told when it ends
    flown: float = 0.0  # seconds in the air
    last_tick: int | None = None  # last world tick, or None before player input is simulated
    target_limit: int = 1
    hit_entities: set[int] = field(default_factory=set)
    definition: int | None = None  # resource identity even without a prediction lease
    gravity: float = 0.
    damage_scale: float = 1.
    accretion_source: tuple[int, int] | None = None
    sphere: sigma_hypersphere.Motion | None = None
    scrap_burst: ScrapBurst | None = None


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
        self.heavy_charge_contacts: dict[int, set[int]] = {}
        self.heavy_charge_pins: dict[int, runtime.Instance] = {}
        self.sniper_repositions: dict[int, object] = {}
        self.stuns: dict[int, int] = {}  # body identity -> exclusive world-tick deadline
        self.pins: dict[int, int] = {}  # NPC source body -> controlled victim body
        self.cryo_samples: dict[int, mei.CryoHealing] = {}
        self.frost_hits: dict[int, tuple] = {}  # progress, touched ms, frozen-until ms, yaw, pitch
        self.frost_sources: dict[int, dict[tuple[int, int], tuple]] = {}
        self.walls = mei_wall.Walls(self)
        self.blizzard = mei_blizzard.Blizzard(self)
        self.shields = zarya_shields.Shields(self)
        self.reinhardt_barriers = reinhardt_barrier.Barriers(self)
        self.winston_barriers = winston_barrier.Barriers(self)
        self.grasp_shields: dict[int, sigma_grasp.ShieldDecay] = {}
        self.grasp_grants: dict[int, tuple[int, int]] = {}
        self.hooks = hook.Hooks(self)
        self.sentries = symmetra_sentry.Turrets(self)
        self.photon_barriers = photon_barrier.Barriers(self)
        self.sigma_barriers = sigma_barrier.Barriers(self)
        self.orisa_barriers = orisa_barrier.Barriers(self)
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
        self.hooks.retire(player.body)
        """Discard body-local combat state when the player's current body retires."""
        self.vitals.pop(player.body, None)
        self.walls.retire(player.body)
        self.blizzard.retire(player.body)
        self.shields.retire(player.body)
        self.grasp_shields.pop(player.body, None)
        self.grasp_grants.pop(player.body, None)
        self.sigma_barriers.retire(player.body)
        self.reinhardt_barriers.retire(player.body)
        self.cryo_samples.pop(player.body, None)
        self.frost_hits.pop(player.body, None)
        self.frost_sources.pop(player.body, None)
        self.history.pop(player.body, None)
        self.replacement_ticks.pop(player.body, None)
        self.melee_seen.pop(player.body, None)
        self.stuns.pop(player.body, None)
        self.clear_pins(player.body)
        # The owner's leased projectiles and their observer copies retire with the old body.
        self.rockets[:] = [
            rocket for rocket in self.rockets if rocket.shooter is not player or rocket.shot is None
        ]
        self.respawns[:] = [(when, who) for when, who in self.respawns if who is not player]

    def record_replacement(self, body: int) -> None:
        """Keep the creation boundary independent of the rolling pose history."""
        self.replacement_ticks[body] = self.match.tick
        self.stuns.pop(body, None)

    def is_stunned(self, actor, *, tick=None) -> bool:
        entity = _source_entity(actor)
        return (entity in self.pins.values()
                or self.control_deadline(entity) > (self.tick if tick is None else tick))

    def control_deadline(self, entity: int) -> int:
        vitals = self.vitals.get(entity)
        return max(self.stuns.get(entity, 0), vitals.sleep_until if vitals is not None else 0)

    def outgoing_damage_scale(self, actor) -> float:
        vitals = self.vitals.get(_source_entity(actor))
        return (nano_boost.OUTGOING if vitals is not None and not vitals.dead
                and self.now < vitals.nano_until else 1.)

    def clear_pins(self, entity):
        for source, victim in tuple(self.pins.items()):
            if entity in (source, victim):
                if source in self.heavy_charge_pins:
                    heavy_charge.release(self, source)
                    bot = next((actor for actor in self.match.bots if actor.entity == source), None)
                    if bot is not None:
                        heavy_charge.stop(bot, self)
                else:
                    del self.pins[source]

    def _hit_effect(self, target, effect):
        """Apply the reconstructed Flashbang and Sleep Dart control effects."""
        if (effect not in (FLASHBANG_EFFECT, SLEEP_DART_EFFECT) or self.vitals_of(target).dead
                or mei.protected(target.owner) or phase_defenses.active(target.owner)
                or self.shields.shielded(target.owner)):
            return
        vitals = self.vitals_of(target)
        if effect == SLEEP_DART_EFFECT:
            floor = self.tick + math.ceil(SLEEP_WAKE_FLOOR / TICK)
            if floor <= self.control_deadline(target.entity):
                return
            self.stuns.pop(target.entity, None)
            vitals.sleep_until = self.tick + math.ceil(SLEEP_DURATION / TICK)
            vitals.sleep_wake_floor = floor
        else:
            variables = graph.mode_variables(self.match.game_map.mode_guid) or {}
            duration = 1.7 if expr.truthy(variables.get(FLASHBANG_MODE_FLAG)) else 0.8
            deadline = self.tick + math.ceil(duration / TICK)
            if vitals.sleep_until >= deadline:
                return
            vitals.clear_sleep()
            self.stuns[target.entity] = max(deadline, self.stuns.get(target.entity, 0))
        self._interrupt(target)

    def _interrupt(self, target):
        self.hooks.cancel_owner(target.entity)
        sigma_flux.interrupt_flux_lift(self, target.owner)
        script = getattr(target.owner, "body_script", None)
        if script is None:
            return
        photon_barrier.interrupt(script)
        component = script.component
        if isinstance(target.owner, bots.Bot):
            from ow174.game import assassin

            assassin.bind(component.world, component, None)
        else:
            self.walls.cancel_preview(target.owner)
        component.edges([(value, False) for value in tuple(component.held)])
        for instance in list(component.instances.values()):
            for state in list(instance.states.values()):
                if isinstance(state, nodes.Ability):
                    state.presses.clear()
                    state.stop()
        component.run_now()
        component.shots.clear()

    def spawned(self, player) -> None:
        """The player's client has the world: the game mode entity gets its UI graph (mode_frame). While
        heroes are assembled the countdown's frames carry it."""
        if player.client is not None and not self.match.assembling():
            player.client.queue_mode_frame(mode_frame(
                {}, player.match.game_map.mode_guid, player.match.game_map.map_guid,
            ))
        self.voice.spawned(player, self.now)

    def command(self, player, command: Command | None, *, defer_effects: bool = False) -> None:
        """Consume the body's reports. Only a frame with a pose can affect targets."""
        if self.match.collision_world is None:
            return
        script = player.body_script
        if script is None:
            return
        shots, script.component.shots[:] = list(script.component.shots), []
        if player.hero.guid == symmetra_beam.HERO and (
            defer_effects or self.is_dead(player) or self.frozen(player) or self.is_stunned(player)
        ):
            symmetra_beam.primary(self, player, None)
        if defer_effects:
            if not self.is_dead(player):
                self._cryo_healing(player)
                # Admitted Q windups release from the latest known pose or locked target.
                # Missing input cannot create ordinary shots.
                for state, number, time, activation in shots:
                    if (state.instance.graph.index, state.index) in ((0x064A, 3), (nano_boost.GRAPH, 1)):
                        pose = Command(script.frame, yaw=player.mover.yaw, pitch=player.mover.pitch)
                        self._shoot(player, pose, state, number, time, activation=activation)
                    elif (self.orisa_barriers.admits(state) and not self.frozen(player)
                          and not self.is_stunned(player)):
                        self.orisa_barriers.launch_unposed(player, state, time, activation)
            self._melee(player, None, script)
            return
        self.shields.update()
        if self.is_dead(player):
            self.walls.cancel_preview(player)
            return
        self._cryo_healing(player)
        if phase_defenses.active(player):
            self._clear_frost(player)
            self._melee(player, None, script)
            return
        if self.frozen(player) or self.is_stunned(player) or self.hooks.controlled(player.body):
            self.walls.cancel_preview(player)
            self._melee(player, None, script)  # consume counters; do not replay a melee after thawing
            return
        if self.walls.command(player, command):
            script.component.shots.clear()
            self._melee(player, None, script)
            return
        if command is not None:
            for state, number, time, activation in shots:
                try:
                    self._shoot(player, command, state, number, time, activation=activation)
                except Exception:
                    log.exception("[game] %s: %s's shot failed", self.match.label(), player.name)
        if player.hero.guid == symmetra_beam.HERO:
            symmetra_beam.primary(self, player, command)
        self.hooks.command(player, command)
        try:
            self._melee(player, command, script)
        except Exception:
            log.exception("[game] %s: %s's melee failed", self.match.label(), player.name)

    def _cryo_healing(self, player) -> None:
        if mei.protected(player):
            self.sentries.release_target(player.body)
            self._clear_frost(player)
        sample = mei.healing_sample(player)
        if sample is None:
            return
        key, cumulative, rate = sample
        target = next((t for t in self._targets() if t.owner is player), None)
        if target is None:
            return
        vitals = self.vitals_of(target)
        if not vitals.dead:
            history = self.cryo_samples.get(player.body)
            if history is None or history.activation != key:
                start = self.now - cumulative / rate
                history = self.cryo_samples[player.body] = mei.CryoHealing(key, start)
                history.note(start, vitals.healing_block_until, vitals.healing_boost_until)
            history.note(self.now, vitals.healing_block_until, vitals.healing_boost_until)
            due = history.sample(cumulative, rate, player.body_script.component.frame_ms / 1000,
                                 ana_grenade.HEAL_BOOST)
            if due <= 0.:
                return
            given = vitals.heal(due)
            player.stats.add(stats.HEALING, given)
            mei_ultimate.grant(player, given, healing=True)
            self._sync_body_health(player, vitals)

    def frost(self, actor, *, tick=None):
        at = self.match.tick if tick is None else tick
        return mei.frost_view(self.frost_hits.get(_source_entity(actor)), at * 16)

    def frozen(self, actor, *, tick=None) -> bool:
        sample = self.frost(actor, tick=tick)
        return sample is not None and bool(sample[2])

    def control_command(self, player, command):
        sample = self.frost(player)
        if sample is None or not sample[2]:
            if self.is_stunned(player):
                return replace(command, buttons=0, action=0, forward=0, right=0)
            return command
        return replace(command, buttons=0, action=0, forward=0, right=0, yaw=sample[3], pitch=sample[4])

    def movement_mods(self, player, active):
        sample = self.frost(player)
        stunned = self.is_stunned(player)
        if sample is None and not stunned:
            return active
        active = dict(active)
        # Reconstructed consumer of the authored frost progress. 05E7 caps slow
        # contribution at0.7; freeze blocks all throttle axes and jump intent.
        if sample is not None:
            active[(-1, 0, 0)] = (0, 1, max(0.3, 1.0 - sample[0]), 99.0)
        if stunned or (sample is not None and sample[2]):
            for index in (17, 18, 19, 20, 21):
                active[(-1, 0, index)] = (index, 2, 0.0, 99.0)
        return active

    def move_bot(self, bot, tick, seconds, collision):
        from ow174.game import assassin

        if bot.body == symmetra_sentry.BODY or self.hooks.controlled(bot.entity):
            return
        if bot.entity in self.sniper_repositions:
            bot.last_tick = tick
            bot.held, bot.pace = (0, 0), 0.0
            return
        instance = heavy_charge.charge(bot)
        if instance is not None and heavy_charge.phase(instance):
            bot.last_tick = tick
            bot.held, bot.pace = (0, 0), 0.0
            return
        if self.walls.step_lifted_bot(bot, tick, seconds, collision):
            return
        before = bot.position
        sample = self.frost(bot, tick=tick)
        if sample is not None and sample[2]:
            bot.last_tick = tick  # no route catch-up through the frozen interval
            bot.held, bot.velocity, bot.pace = (0, 0), (0.0, 0.0, 0.0), 0.0
            bot.moved_tick = tick
        else:
            speed = bot.speed
            try:
                bot.speed *= npc_context.movement_scale(bot)
                if sample is not None:
                    bot.speed *= max(0.3, 1.0 - sample[0])
                if not assassin.pursue(self, bot, tick, seconds, collision):
                    bot.step(tick, seconds, collision, paused_until=self.control_deadline(bot.entity))
            finally:
                bot.speed = speed
        self.walls.move_bot(bot, before)

    def _clear_frost(self, actor):
        self.frost_sources.pop(_source_entity(actor), None)
        if self.frost_hits.pop(_source_entity(actor), None) is None:
            return  # Unrelated NPC script wrappers need no frost/VM consumer.
        script = getattr(actor, "body_script", None)
        if script is not None:
            var = script.component.entity_var(2427, create=False)
            if var is not None:
                script.component.set(2427, 0.0)

    def _advance_frost(self):
        if not self.frost_hits:
            return
        current = {target.entity: target for target in self._targets()}
        for entity in tuple(self.frost_hits):
            target = current.get(entity)
            if target is None:
                del self.frost_hits[entity]
                self.frost_sources.pop(entity, None)
            elif (self.vitals_of(target).dead or mei.protected(target.owner) or mei.frost_immune(target.owner)
                  or self.frost(target.owner) is None):
                self._clear_frost(target.owner)
            elif not self.frost_hits[entity][2] and entity in self.frost_sources:
                entries = self.frost_sources[entity]
                at = self.match.tick * 16
                mei_frost.prune(entries, at)
                sample = mei_frost.combine(entries, at, *self.frost_hits[entity][3:])
                if sample is None:
                    self._clear_frost(target.owner)
                else:
                    self._write_frost(target.owner, sample)

    def _frost_hit(self, target, *, source=None, channel=0, now_ms=None,
                   initial=mei.FROST_INITIAL, step=mei.FROST_STEP):
        actor = target.owner
        if self.shields.shielded(actor) or mei.frost_immune(actor) or phase_defenses.active(actor):
            return
        if isinstance(actor, bots.Bot):
            yaw, pitch = round(actor.yaw), 0
        else:
            yaw, pitch = actor.mover.yaw, actor.mover.pitch
        sample = self.frost_hits.get(target.entity)
        at = self.tick * 16 if now_ms is None else now_ms
        if sample is not None:
            at = max(at, sample[1])  # A second source cannot move aggregate time backwards.
            if sample[2]:
                if at < sample[2]:
                    return
                self.frost_sources.pop(target.entity, None)
        entries = self.frost_sources.setdefault(target.entity, {})
        mei_frost.prune(entries, at)
        minimum, scalar, freeze_ms, stacking = mei_frost.settings(source, weapon=channel == 0)
        key = (_source_entity(source) if source is not None else 0, channel)
        previous = entries.get(key, (None, False))[0]
        entries[key] = (mei.frost_hit(previous, at, yaw, pitch,
                                     initial=initial if minimum is None else minimum,
                                     step=expr.f32(step * scalar), freeze_ms=freeze_ms), stacking)
        sample = mei_frost.combine(entries, at, yaw, pitch, freeze_ms=freeze_ms)
        self._write_frost(actor, sample)

    def _write_frost(self, actor, sample):
        self.frost_hits[_source_entity(actor)] = sample
        if sample[2]:
            self.hooks.cancel_owner(_source_entity(actor))
            dva_matrix.interrupt(actor)
            sigma_flux.interrupt_flux_lift(self, actor)
            script = getattr(actor, "body_script", None)
            if script is not None:
                from ow174.game import sombra_hack

                photon_barrier.interrupt(script)
                sombra_hack.interrupt_held_hack(script)
            sigma_grasp.interrupt(actor)
            genji_deflect.interrupt(actor)
            breather.interrupt(actor)
            whole_hog.interrupt(actor)
            if isinstance(actor, bots.Bot) and actor.body_script is not None:
                from ow174.game import assassin

                component = actor.body_script.component
                assassin.bind(component.world, component, None)
        script = getattr(actor, "body_script", None)
        if script is not None:
            script.component.set(2427, sample[0])

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
            amount *= self.outgoing_damage_scale(player)
            loadout = (
                state.evaluate(modify.get("m_loadout"), watch=False) if modify.get("m_loadout") else None
            )
            loadout = loadout.guid if isinstance(loadout, expr.Asset) else None
            direction = aim(*command.aim())
            if script.hero == mei.HERO:
                scale = player.mover.body_scale
                reach, radius = reach * scale, radius * scale
            centre = _along(self._eye(player), direction, reach)
            view_tick = command.frame - command.view_delay(player.quanta) * 1e6 / player.quanta
            for target in self._enemies(player):
                shown = self.seen(target, view_tick)
                if shown is None:
                    continue
                a, b = shown.segment()
                aim_point = (shown.feet[0], shown.feet[1] + shown.eye, shown.feet[2])
                if (segment_distance(centre, a, b) - shown.radius <= radius and amount > 0.0
                        and capsule_visible(self._collision(), self._eye(player), a, b, shown.radius)
                        and not self.walls.blocked(self._eye(player), aim_point)):
                    if genji_deflect.blocks_melee(self, target.owner, direction):
                        continue
                    self._damage(player, target, amount, False, loadout, command.frame)
            for piece in tuple(self.walls.pieces.values()):
                surface = mei_wall.closest(piece, self._eye(player))
                if (mei_wall.distance(piece, centre) <= radius
                        and capsule_visible(self._collision(), self._eye(player), surface, surface, 0.0)
                        and not self.walls.blocked(self._eye(player), surface)):
                    self.walls.damage(piece, player, amount, command.frame)
            if script.hero == mei.HERO:
                eye = self._eye(player)

                def visible(point, eye=eye):
                    return (not self.walls.blocked(eye, point)
                            and capsule_visible(self._collision(), eye, point, point, 0.0))

                if self.reinhardt_barriers.geometry is not None:
                    self.reinhardt_barriers.melee(player, centre, radius, amount, visible)
                self.winston_barriers.melee(player, centre, radius, amount, visible)
                self.photon_barriers.melee(player, centre, radius, amount, visible)
                self.sigma_barriers.melee(player, centre, radius, amount, visible)
                self.orisa_barriers.melee(player, centre, radius, amount, visible)

    def _advance_idle_body(self, player):
        """Commit older zero-input windows; keep this world's current input slot.

        Explicit scoped server policy, not native transport/rollback parity.
        Body timers retain their own epoch/quanta; no separate defense timer.
        """
        script = player.body_script
        if (script is None or script.retired or not player.spawned or not player.has_body
                or script.world_frame_anchor is None):
            return
        at, frame = script.world_frame_anchor
        target = min(projectiles.MAX_LEASE_FRAME - 1,
                     frame + max(0, self.tick - at) * 16 // script.frame_ms - 1)
        if target > script.frame:
            player._advance_body_gap(target + 1, world_idle=True)

    def update(self, now: float, tick: int, frames: bool = True) -> None:
        """Each tick: keep where the bodies are, fly the rockets, respawn, send health that changed. The
        other players' body frames only on the ticks that send (`frames`)."""
        if self.match.collision_world is None:
            return
        retained = max(self.tick + 1, tick - COMBAT_CATCH_UP + 1)
        first = tick
        clocked = any(p.body_script is not None and not p.body_script.retired
                      and p.body_script.world_frame_anchor is not None for p in self.match.players)
        if self.tick and tick > self.tick + 1 and (
            clocked or self.winston_barriers.actors or self.orisa_barriers.actors
            or self.hooks.flights
            or self.sentries.sentries
            or self.photon_barriers.actors
            or self.sigma_barriers.actors or any(rocket.sphere for rocket in self.rockets)
            or self.orisa_barriers.flights or any(rocket.gravity for rocket in self.rockets)
        ):
            first = self.tick + 1
        if self.tick and tick > self.tick + 1 and any(
            not bot.dead and isinstance(bot.body_script, BotScript)
            and (bot.firing or heavy_charge.clocked(bot) or heavy_charge.recovering(bot)
                 or npc_melee.clocked(bot) or npc_context.clocked(bot)
                 or bot.entity in self.sniper_repositions)
            for bot in self.match.bots
        ):
            first = min(first, retained)
        for target in self._targets():
            history = self.history.setdefault(target.entity, deque(maxlen=HISTORY))
            history.append((tick, target.feet, target.height, target.eye))
        # Replay the retained combat frames using the most recent poses. Player input
        # accepted just before the scheduler caught up joins only at the endpoint.
        for at in range(first, tick + 1):
            self.now, self.tick = now - (tick - at) * TICK, at
            if at == first and retained < at:
                self.projectiles.heal_fields(retained, at - 1)
            for player in self.match.players:
                self._advance_idle_body(player)
            self.walls.update(at)
            self.shields.update()
            self.reinhardt_barriers.update()
            self.winston_barriers.update(at)
            sigma_grasp.update_shields(self)
            self.photon_barriers.update(at)
            self.sigma_barriers.update()
            self.orisa_barriers.update(at, TICK)
            for rocket in list(self.rockets):
                if at == tick or rocket.last_tick is not None:
                    self._fly(rocket)
            self._bot_weapons(at, accept_ability_input=at == tick)
            self.hooks.update(at)
            self.sentries.update(accept_new=at == tick)
            self.blizzard.update(at)
            if at >= retained:
                self.projectiles.heal_fields(at, at)
        self.projectiles.update(tick)
        self._advance_frost()
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
        current = {target.entity for target in self._targets()}
        for entity in [key for key, deadline in self.stuns.items() if deadline <= tick]:
            del self.stuns[entity]
        for vitals in self.vitals.values():
            if vitals.sleep_until and vitals.sleep_until <= tick:
                vitals.clear_sleep()
        for table in (self.history, self.vitals, self.replacement_ticks, self.stuns):
            for entity in [key for key in table if key not in current]:
                del table[entity]

    # --- targets -----------------------------------------------------------------------------------

    def _targets(self) -> list[Target]:
        found = []
        for player in self.match.players:
            if not (player.has_body and player.spawned and player.client is not None):
                continue
            data = player.mover.values
            crouched = player.mover.crouched
            height = data.crouch_height if crouched else data.stand_height
            eye = eye_height(player.hero.body, crouched) * player.mover.body_scale
            found.append(
                Target(player.body, player.team, tuple(player.mover.position), height, data.stand_radius, eye,
                       player)
            )  # fmt: skip
        for bot in self.match.bots:
            if bot.body == symmetra_sentry.BODY:
                x, y, z = bot.position
                found.append(Target(bot.entity, bot.team, (x, y - .2, z), .4, .2, .2, bot))
                continue
            data = mover_data(bot.body)
            found.append(
                Target(
                    bot.entity, bot.team, tuple(bot.position), data.stand_height, data.stand_radius, EYE, bot
                )
            )
        return found

    def vitals_of(self, target: Target) -> Vitals:
        found = self.vitals.get(target.entity)
        if found is None:
            if isinstance(target.owner, bots.Bot):
                found = Vitals(target.entity, target.owner.health)
            else:
                found = Vitals(target.entity, *pools.body_pools(target.owner.hero.guid))
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
            if phase_defenses.active(target.owner):
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
        return Target(target.entity, target.team, feet, height, target.radius, eye, target.owner)

    # --- shots -------------------------------------------------------------------------------------

    def publish_bot_live(self, bot, snapshot) -> None:
        if snapshot is not None:
            for viewer in self.match.players:
                if viewer.spawned and viewer.client is not None:
                    viewer.client.queue_bot_live(bot.entity, snapshot)

    def _bot_weapons(self, tick: int, *, accept_ability_input: bool = True) -> None:
        """Advance firing and charging bot bodies and consume their volley reports."""
        from ow174.game import assassin

        for bot in self.match.bots:
            script = bot.body_script
            if (bot.dead or self.hooks.controlled(bot.entity)
                    or not isinstance(script, BotScript) or script.frame > tick):
                continue
            charging = heavy_charge.clocked(bot)
            melee = npc_melee.clocked(bot)
            retreat = bot.entity in self.sniper_repositions
            if not (bot.firing or charging or melee or retreat or heavy_charge.recovering(bot)
                    or npc_context.clocked(bot)
                    or assassin.releasing(script.component)):
                continue
            try:
                if retreat and script.frame < tick:
                    sniper_reposition.step(self, bot, tick)
                if self.frozen(bot) or self.is_stunned(bot):
                    if charging:
                        heavy_charge.stop(bot, self)
                    if melee:
                        npc_melee.stop(bot)
                elif charging and script.frame < tick:
                    heavy_charge.move(bot, self, tick)
                assassin.validate_binding(script.component.world, script.component)
                buttons = ()
                if (charging or melee) and script.frame < tick:
                    button = heavy_charge.BUTTON if charging else npc_melee.BUTTON
                    if button in script.component.held:
                        buttons = [(button, False)]
                    elif (accept_ability_input and bot.pending_ability == button
                          and (charging or npc_melee.ready(bot))):
                        buttons = [(button, True)]
                        bot.pending_ability = None
                snapshot = script.advance_live(tick, bot.position, buttons=buttons)
                if melee:
                    npc_melee.sample_trooper(bot, self)
                instance = heavy_charge.charge(bot) if charging else None
                if charging:
                    heavy_charge.counter_charge(bot, self, instance)
                    heavy_charge.body_impact(bot, self, instance)
                    heavy_charge.front_contact(bot, self, instance)
                    heavy_charge.side_contacts(bot, self, instance)
                    if instance is not None and not heavy_charge.driving(instance):
                        bot.velocity = (0.0, 0.0, 0.0)
                shots, script.component.shots[:] = list(script.component.shots), []
                for state, number, time, activation in shots:
                    self._shoot(
                        bot, None, state, number, time, activation=activation,
                        snapshot=snapshot or script.latest_live,
                    )
                self.publish_bot_live(bot, snapshot)
            except Exception:
                self.clear_pins(bot.entity)
                self.sniper_repositions.pop(bot.entity, None)
                self.heavy_charge_contacts.pop(bot.entity, None)
                bot.body_script = None
                log.exception("[game] %s: %s's weapon script failed", self.match.label(), bot.name)

    def _eye(self, player) -> tuple:
        x, y, z = player.mover.position
        crouched = player.mover.crouched
        height = eye_height(player.hero.body, crouched) * player.mover.body_scale
        return (x, y + height, z)

    def _shoot(
        self, shooter, command, state, number: int, time: int,
        *, activation: tuple[int, int], snapshot: Snapshot | None = None,
    ) -> None:
        if self.frozen(shooter) or self.is_stunned(shooter) or phase_defenses.active(shooter):
            return
        if state.instance.graph.index == nano_boost.GRAPH and state.index == 1:
            nano_boost.release(self, shooter, state)
            return
        node = state.node
        shot_frame = time // state.instance.component.frame_ms

        def field(name, default=None):
            cfg = node.field(name)
            return default if cfg is None else _number(state.evaluate(cfg, watch=False), default)

        npc = isinstance(shooter, bots.Bot)
        if npc:
            x, y, z = shooter.position
            # Use the server body pose until the animated muzzle transform is available.
            origin, direction = (x, y + EYE, z), aim(round(shooter.yaw), 0)
        else:
            origin, direction = self._eye(shooter), aim(*command.aim())
        if not npc and self.sentries.fired(shooter, command, state):
            return
        if not npc and self.orisa_barriers.admits(state):
            self.orisa_barriers.launch(shooter, state, origin, direction, command.aim()[0],
                                       frame=shot_frame, activation=activation, snapshot=snapshot)
            return
        if self.blizzard.fired(shooter, state, shot_frame, origin, direction,
                               activation=activation, snapshot=snapshot):
            return
        direct = hit_def(state, node.field("m_6396149F"))
        road_ring = not npc and state.instance.graph.index == 0x01C6 and node.pos in (26, 33)
        pellets = enforcer.ring_directions(state, direction, node.fields) if road_ring else None
        if road_ring and direct is not None and node.field("m_6396149F").get("m_A872C70E") is None:
            direct = replace(direct, critical=2.0)
        damage_scale = self.outgoing_damage_scale(shooter)
        speed = field("m_projectileSpeed")
        if speed:
            lifetime = field("m_projectileLifetime", 5.0) or 5.0
            reach = field("m_FEC435C6")
            if reach:
                lifetime = min(lifetime, reach / speed)
            blast = hit_def(state, node.field("m_D2D11CE9"))
            entity_def = (node.field("m_projectileEntity") or {}).get("m_entityDef")
            definition = state.evaluate(entity_def, watch=False) if entity_def is not None else None
            gravity = ana_grenade.gravity(state)
            motion = sigma_accretion.accretion_launch(state, direction) if not npc else None
            accretion_source = None
            if motion is not None:
                gravity, direction = motion
                accretion_source = shooter.body, state.instance.id
            sphere_motion = sigma_hypersphere.hypersphere_motion(state) if not npc else None
            if sphere_motion is not None:
                gravity, *sphere_values = sphere_motion
            damaging = (direct is not None and direct.amount > 0.0) or (
                blast is not None and blast.amount > 0.0
            )
            burst = (self._scrap_burst(state, direction)
                     if not npc and state.instance.graph.index == 0x01C6 and node.pos == 29 else None)
            for pellet_direction in pellets or enforcer.directions(
                shooter, state, direction, activation=activation, number=number,
            ):
                shot = None
                if isinstance(definition, expr.Asset):
                    shot = self.projectiles.fired(
                        shooter, state, shot_frame, definition.guid, lifetime, origin, pellet_direction,
                        activation=activation, snapshot=snapshot,
                    )
                if damaging or shot is not None:
                    self.rockets.append(
                        Rocket(
                            shooter, shooter.team, origin, pellet_direction, speed, lifetime,
                            direct, blast, shot,
                            last_tick=self.tick if npc else None,
                            target_limit=mei.projectile_target_limit(state),
                            definition=definition.guid if isinstance(definition, expr.Asset) else None,
                            gravity=gravity,
                            sphere=sigma_hypersphere.Motion(*sphere_values) if sphere_motion else None,
                            accretion_source=accretion_source,
                            damage_scale=damage_scale,
                            scrap_burst=burst,
                        )
                    )
            return
        if direct is None or direct.amount <= 0.0:
            return
        if damage_scale != 1.:
            direct = replace(direct, amount=direct.amount * damage_scale)
        reach = field("m_FEC435C6", RANGE) or RANGE
        # The input supplies this simulation frame's aim and view time. Notices use the volley clock.
        view_tick = self.tick
        if command is not None:
            view_tick = command.frame - command.view_delay(shooter.quanta) * 1e6 / shooter.quanta
        force = whole_hog.spec(state) if road_ring and node.pos == whole_hog.VOLLEY[1] else None
        pushed = set()
        for pellet_direction in pellets or (direction,):
            hit = self._hitscan_pellet(shooter, origin, pellet_direction, reach, view_tick,
                                       direct, shot_frame, npc, whole_hog_volley=force is not None)
            if force is not None and hit is not None:
                distance, target, direction_at_hit = hit
                if target.entity not in pushed:
                    whole_hog.push(self, target, direction_at_hit, distance, force)
                    pushed.add(target.entity)

    def _hitscan_pellet(self, shooter, origin, direction, reach, view_tick, direct, shot_frame, npc,
                         *, whole_hog_volley=False, traveled=0.0, reflections=0):
        best = None
        for target in self._enemies(shooter):
            shown = self.seen(target, view_tick)
            if shown is None:
                continue
            a, b = shown.segment()
            t = ray_capsule(origin, direction, a, b, shown.radius)
            if t is not None and t <= reach and (best is None or t < best[0]):
                best = (t, target, shown)
        limit = reach if best is None else best[0]
        wall_hit = self.walls.cast(origin, direction, limit)
        shield_hit = self.shields.cast(shooter, origin, direction, limit)
        if whole_hog_volley:
            nearer = min((hit[0] for hit in (wall_hit, shield_hit) if hit is not None), default=limit)
            barrier = self._whole_hog_barrier(shooter, origin, direction, nearer)
            cryo = self.cryo_contact(shooter, origin, direction, nearer)
            static = ray_world(self._collision(), origin, direction, nearer)
            gate_limit = min(t for t in (nearer, barrier[0] if barrier else None, cryo, static)
                             if t is not None)
            gate = self._whole_hog_active_contact(shooter, origin, direction, gate_limit)
            if gate is not None and gate[0] < gate_limit:
                distance, kind, args = gate
                if kind == "grasp":
                    sigma_grasp.absorbed(args[0], direct.amount * direct.scale(traveled + distance))
                elif kind == "deflect" and reflections < 8:
                    owner, outgoing = args
                    point = tuple(p + d * distance for p, d in zip(origin, direction, strict=True))
                    scale = self.outgoing_damage_scale(owner) / self.outgoing_damage_scale(shooter)
                    reflected = replace(direct, amount=direct.amount * scale)
                    return self._hitscan_pellet(
                        owner, point, outgoing, reach - distance, view_tick, reflected, shot_frame, npc,
                        whole_hog_volley=True, traveled=traveled + distance, reflections=reflections + 1,
                    )
                return None
            if (cryo is not None and (wall_hit is None or cryo < wall_hit[0])
                    and (shield_hit is None or cryo < shield_hit[0])
                    and (barrier is None or cryo < barrier[0])):
                return None
            if barrier is not None:
                distance, damage, args = barrier
                static = ray_world(self._collision(), origin, direction, distance)
                if static is None or static >= distance:
                    damage(*args, direct.amount * direct.scale(traveled + distance))
                return None
        if shield_hit is not None and (wall_hit is None or shield_hit[0] < wall_hit[0]):
            distance, bubble = shield_hit
            static = ray_world(self._collision(), origin, direction, distance)
            if static is None or static >= distance:
                amount = direct.amount * direct.scale(traveled + distance)
                self.shields.damage(bubble, shooter, amount, shot_frame)
            return
        if wall_hit is not None:
            distance, piece, _normal = wall_hit
            static = ray_world(self._collision(), origin, direction, distance)
            if static is None or static >= distance:
                amount = direct.amount * direct.scale(traveled + distance)
                self.walls.damage(piece, shooter, amount, shot_frame)
            return
        if best is None:
            return
        t, target, shown = best
        wall = ray_world(self._collision(), origin, direction, t)
        if wall is not None and wall < t:
            return
        hit_y = origin[1] + direction[1] * t
        critical = (hit_y >= shown.feet[1] + shown.eye - HEAD_BELOW
                    and self._fortify_state(target.owner) is None)
        amount = direct.amount * direct.scale(traveled + t) * (direct.critical if critical else 1.0)
        dealt = self._damage(
            shooter, target, amount, critical and direct.critical > 1.0, direct.loadout, shot_frame,
            world_tick=self.tick if npc else None,
        )
        if dealt:
            self._hit_effect(target, direct.effect)
            return traveled + t, target, direction
        return None

    def _whole_hog_active_contact(self, shooter, origin, direction, limit):
        definition = roadhog_projectiles.WHOLE_HOG
        hits = []
        for kind, cast in (("grasp", sigma_grasp.cast), ("matrix", dva_matrix.cast),
                           ("deflect", genji_deflect.cast)):
            hit = cast(self, shooter, definition, origin, direction, limit)
            if hit is not None:
                hits.append((hit[0], kind, hit[1:]))
        return min(hits, key=lambda hit: hit[0], default=None)

    def _whole_hog_barrier(self, shooter, origin, direction, limit):
        best = None
        for barrier in (self.orisa_barriers, self.winston_barriers, self.reinhardt_barriers,
                        self.photon_barriers, self.sigma_barriers):
            hit = barrier.trace(shooter, origin, direction, limit)
            if hit is not None and hit[0] < limit:
                limit = hit[0]
                best = limit, barrier.damage, hit[1:]
        return best

    @staticmethod
    def _scrap_burst(state, direction):
        # The authored callback supplies the child volley. Expiry-only dispatch
        # and the even cone reconstruction are scoped to this one callback.
        callback = state.node.field("m_747ECB51") or {}
        fields = callback.get("m_volley")
        if callback.get("$") != "STU_DB5FDC79" or not isinstance(fields, dict):
            return None

        def number(name, default):
            cfg = fields.get(name)
            return default if cfg is None else _number(state.evaluate(cfg, watch=False), default)

        speed = number("m_projectileSpeed", 0.0)
        if speed <= 0.0:
            return None
        lifetime = min(number("m_projectileLifetime", 5.0), number("m_FEC435C6", RANGE) / speed)
        hit_fields = fields.get("m_6396149F")
        hit = hit_def(state, hit_fields)
        if hit is not None and hit_fields.get("m_A872C70E") is None:
            hit = replace(hit, critical=2.0)
        cfg = (fields.get("m_projectileEntity") or {}).get("m_entityDef")
        asset = state.evaluate(cfg, watch=False) if cfg is not None else None
        return ScrapBurst(
            enforcer.ring_directions(state, direction, fields), speed, lifetime, hit,
            hit_def(state, fields.get("m_D2D11CE9")), asset.guid if isinstance(asset, expr.Asset) else None,
        )

    def _expire_projectile(self, rocket, remainder):
        self._landed(rocket)
        burst = rocket.scrap_burst
        if burst is None:
            return
        for direction in burst.directions:
            pellet = Rocket(rocket.shooter, rocket.team, rocket.position, direction, burst.speed,
                            burst.lifetime, burst.hit, burst.blast, last_tick=self.tick,
                            definition=burst.definition, damage_scale=rocket.damage_scale)
            self.rockets.append(pellet)
            if remainder > 0.0:
                self._fly(pellet, seconds=remainder)

    def _fly(self, rocket: Rocket, *, seconds=None) -> None:
        """Sweep elapsed world time against the current bodies and static collision."""
        # Player input can arrive immediately before the scheduler accounts for a gap.
        if seconds is None:
            elapsed = 1 if rocket.last_tick is None else self.tick - rocket.last_tick
            seconds = elapsed * TICK
        if seconds <= 0.0:
            return
        rocket.last_tick = self.tick
        duration = min(seconds, rocket.left)
        if duration <= 0.0:
            self._expire_projectile(rocket, seconds)
            return
        if rocket.gravity:
            velocity = [axis * rocket.speed for axis in rocket.direction]
            velocity[1] -= rocket.gravity * duration
            rocket.speed = math.sqrt(sum(axis * axis for axis in velocity))
            if rocket.speed > EPSILON:
                rocket.direction = tuple(axis / rocket.speed for axis in velocity)
        for _ in range(8):
            remaining = self._sweep(rocket, duration, after_end=seconds - duration)
            if remaining is None or remaining <= 0.0:
                return
            duration = remaining
        rocket.flown += duration
        rocket.left -= duration
        if rocket.left <= 0.0:
            self._expire_projectile(rocket, seconds - duration)
            if rocket.sphere is not None:
                self._splash(rocket, rocket.position)

    def cryo_contact(self, shooter, origin, direction, reach):
        nearest = None
        for target in self._targets():
            if target.owner is shooter or not mei.protected(target.owner):
                continue
            a, b = target.segment()
            distance = ray_capsule(origin, direction, a, b, target.radius)
            if distance is not None and distance <= reach and (nearest is None or distance < nearest):
                nearest = distance
        return nearest

    def _sweep(self, rocket: Rocket, duration: float, reflections: int = 0, *, after_end=0.0) -> float | None:
        # Sweep the remaining flight even when the range or lifetime ends inside this tick.
        step = rocket.speed * duration
        contacts = []
        targets = (ana_grenade.collision_targets(self, rocket.shooter)
                   if rocket.definition == ana_grenade.DEFINITION else self._enemies(rocket.shooter))
        for target in targets:
            if target.entity in rocket.hit_entities:
                continue
            a, b = target.segment()
            radius = target.radius + (rocket.sphere.body_radius if rocket.sphere is not None else 0.)
            t = ray_capsule(rocket.position, rocket.direction, a, b, radius)
            if t is not None and t <= step:
                contacts.append((t, target))
        if rocket.sphere is None:
            wall = ray_world(self._collision(), rocket.position, rocket.direction, step)
            world_normal = None
        else:
            wall, world_normal = sigma_hypersphere.wall_contact(
                self._collision(), rocket.position, rocket.direction, step, rocket.sphere.wall_radius,
            )
        if wall is not None:
            contacts.append((wall, None))
        if rocket.definition == symmetra_beam.ORB or rocket.definition in roadhog_projectiles.SCRAP:
            cryo = self.cryo_contact(rocket.shooter, rocket.position, rocket.direction, step)
            if cryo is not None:
                contacts.append((cryo, None))
        wall_hit = self.walls.cast(rocket.position, rocket.direction, step)
        if wall_hit is not None:
            contacts.append((wall_hit[0], wall_hit[1]))
        shield_hit = self.shields.cast(rocket.shooter, rocket.position, rocket.direction, step)
        if shield_hit is not None:
            contacts.append(shield_hit)
        definition = rocket.shot.definition if rocket.shot is not None else None
        if rocket.definition == symmetra_beam.ORB or rocket.definition in roadhog_projectiles.SCRAP:
            definition = rocket.definition
        barrier = dome = orisa = sigma = photon = None
        if (rocket.definition in (*reinhardt_barrier.MEI_PROJECTILES, symmetra_beam.ORB)
                or rocket.definition in roadhog_projectiles.SCRAP):
            orisa = self.orisa_barriers.trace(rocket.shooter, rocket.position, rocket.direction, step)
            if orisa is not None:
                contacts.append((orisa[0], orisa))
            dome = self.winston_barriers.trace(rocket.shooter, rocket.position, rocket.direction, step)
            if dome is not None:
                contacts.append((dome[0], dome))
            barrier = self.reinhardt_barriers.trace(rocket.shooter, rocket.position, rocket.direction, step)
            if barrier is not None:
                contacts.append((barrier[0], barrier))
            photon = self.photon_barriers.trace(rocket.shooter, rocket.position, rocket.direction, step)
            if photon is not None:
                contacts.append((photon[0], photon))
            sigma = self.sigma_barriers.trace(rocket.shooter, rocket.position, rocket.direction, step)
            if sigma is not None:
                contacts.append((sigma[0], sigma))
        grasp = sigma_grasp.cast(self, rocket.shooter, definition, rocket.position, rocket.direction, step)
        if grasp is not None and (wall is None or grasp[0] < wall):
            contacts.append(grasp)
        absorption = dva_matrix.cast(
            self, rocket.shooter, definition, rocket.position, rocket.direction, step,
        )
        if absorption is not None:
            contacts.append((absorption[0], absorption))
        reflection = genji_deflect.cast(
            self, rocket.shooter, definition, rocket.position, rocket.direction, step,
        )
        if reflection is not None:
            contacts.append((reflection[0], reflection))
        flown_before = rocket.flown
        for t, struck in sorted(contacts, key=lambda contact: (contact[0], contact[1] is None)):
            rocket.flown = flown_before + t / rocket.speed
            point = _along(rocket.position, rocket.direction, t)
            if struck is None and world_normal is not None and rocket.sphere.bounces > 0:
                elapsed = t / rocket.speed
                sigma_hypersphere.bounce(rocket, point, world_normal, elapsed)
                if rocket.left <= 0.0:
                    self._landed(rocket)
                    self._splash(rocket, rocket.position)
                    return None
                return min(duration - elapsed, rocket.left) if rocket.speed else None
            if struck is not None and (
                struck is barrier or struck is dome or struck is orisa or struck is sigma or struck is photon
            ):
                if rocket.definition == symmetra_beam.ORB:
                    # Resolve approach-side splash while the intercepting plane still exists.
                    self._splash(rocket, point)
                if rocket.direct is not None:
                    amount = rocket.damage_scale * rocket.direct.amount * rocket.direct.scale(
                        rocket.flown * rocket.speed,
                    )
                    if struck is dome:
                        self.winston_barriers.damage(dome[1], amount)
                    elif struck is photon:
                        self.photon_barriers.damage(photon[1], amount)
                    elif struck is sigma:
                        self.sigma_barriers.damage(sigma[1], amount)
                    elif struck is orisa:
                        self.orisa_barriers.damage(orisa[1], amount)
                    else:
                        self.reinhardt_barriers.damage(barrier[1], barrier[2], amount)
                self._landed(rocket)
                return
            if grasp is not None and struck is grasp[1]:
                amount = 0.0 if rocket.direct is None else rocket.damage_scale * rocket.direct.amount * (
                    rocket.direct.scale(rocket.flown * rocket.speed)
                )
                sigma_grasp.absorbed(struck, amount)
                self._landed(rocket)
                return
            if struck is absorption and absorption is not None:
                self._landed(rocket)
                return
            if struck is reflection and reflection is not None:
                before = rocket.direction
                rocket.shooter, rocket.direction = reflection[1:]
                if rocket.scrap_burst is not None:
                    directions = roadhog_projectiles.reorient(
                        rocket.scrap_burst.directions, before, rocket.direction,
                    )
                    rocket.scrap_burst = replace(rocket.scrap_burst, directions=directions)
                rocket.team = rocket.shooter.team
                rocket.damage_scale = self.outgoing_damage_scale(rocket.shooter)
                rocket.position = point
                spent = t / rocket.speed
                rocket.left -= spent
                rocket.hit_entities.clear()
                if rocket.left <= 0.0:
                    self._landed(rocket)
                elif duration > spent and reflections < 8:
                    # Sweep the return leg too: close targets and walls must
                    # not be skipped when reflection happens mid-frame.
                    self._sweep(rocket, duration - spent, reflections + 1)
                return
            if isinstance(struck, zarya_shields.Bubble):
                if rocket.direct is not None:
                    amount = rocket.damage_scale * rocket.direct.amount * rocket.direct.scale(
                        rocket.flown * rocket.speed,
                    )
                    self.shields.damage(struck, rocket.shooter, amount, self.tick)
                self._landed(rocket)
                self._splash(rocket, point, exclude=(struck.target_body,))
                return
            if isinstance(struck, mei_wall.Pillar):
                if rocket.direct is not None:
                    self.walls.damage(struck, rocket.shooter,
                                      rocket.damage_scale * rocket.direct.amount
                                      * rocket.direct.scale(rocket.flown * rocket.speed),
                                      self.tick)
                self._landed(rocket)
                # Keep the blast just outside the struck face. Starting exactly
                # on an OBB face makes tangent rays self-occlude adjacent pillars.
                self._splash(rocket, _along(point, rocket.direction, -2 * mei_wall.EPS))
                return
            if struck is not None:
                rocket.hit_entities.add(struck.entity)
                direct = rocket.direct
                if direct is not None and direct.amount > 0.0:
                    critical = (direct.critical > 1.0 and point[1] >= struck.feet[1] + struck.eye - HEAD_BELOW
                                and self._fortify_state(struck.owner) is None)
                    amount = rocket.damage_scale * direct.amount * direct.scale(rocket.flown * rocket.speed)
                    if critical:
                        amount *= direct.critical
                    dealt = self._damage(
                        rocket.shooter, struck, amount, critical, direct.loadout, self.tick,
                        world_tick=self.tick,
                    )
                    if dealt:
                        sigma_accretion.apply_impact_impulse(self, rocket.direction, struck, direct.knockback)
                        sigma_accretion.accretion_hit(self, rocket, struck, dealt)
                        self._hit_effect(struck, direct.effect)
                if len(rocket.hit_entities) < rocket.target_limit and rocket.blast is None:
                    continue
            self._landed(rocket)
            self._splash(rocket, point, direct_target=struck.entity if struck is not None else None)
            return
        rocket.position = _along(rocket.position, rocket.direction, step)
        rocket.flown = flown_before + duration
        rocket.left -= duration
        if rocket.left <= 0.0:
            self._expire_projectile(rocket, after_end)
            if rocket.sphere is not None:
                self._splash(rocket, rocket.position)

    def _orb_splash_blocked(self, shooter, origin, destination):
        reach = math.dist(origin, destination)
        if reach <= EPSILON:
            return False
        direction = tuple((b - a) / reach for a, b in zip(origin, destination, strict=True))
        if self.cryo_contact(shooter, origin, direction, reach) is not None:
            return True
        return any(service.trace(shooter, origin, direction, reach) is not None for service in (
            self.orisa_barriers, self.winston_barriers, self.reinhardt_barriers,
            self.photon_barriers, self.sigma_barriers,
        ))

    def _splash(self, rocket: Rocket, point, *, exclude=(), direct_target=None) -> None:
        blast = rocket.blast
        if blast is None or blast.amount <= 0.0:
            return
        if rocket.damage_scale != 1.:
            blast = replace(blast, amount=blast.amount * rocket.damage_scale)
        protected = {b.target_body for b in self.shields.bubbles.values() if self.shields.valid(b)}
        grenade = rocket.definition == ana_grenade.DEFINITION
        if grenade:
            ana_grenade.splash(self, rocket, point, exclude, protected)
        visible_from = _along(point, rocket.direction, -0.0001)
        targets = () if grenade else self._enemies(rocket.shooter)
        orb = rocket.definition == symmetra_beam.ORB
        if orb:
            # Sample all Orb splash cover before any hit can retire a barrier.
            def covered(destination):
                return self._orb_splash_blocked(rocket.shooter, visible_from, destination)

            targets = [t for t in targets if not covered((t.feet[0], t.feet[1] + t.eye, t.feet[2]))]
            orb_wall_hits = self.walls.splash_contacts(point, blast, blocked=covered)
            orb_shield_hits = self.shields.splash_contacts(
                rocket.shooter, blast, visible_from, blocked=covered,
            )
        for target in targets:
            if target.entity in exclude or target.entity in protected:
                continue
            a, b = target.segment()
            distance = max(0.0, segment_distance(point, a, b) - target.radius)
            scalar = blast.splash(distance)
            eye = (target.feet[0], target.feet[1] + target.eye, target.feet[2])
            if (scalar > 0.0 and capsule_visible(self._collision(), visible_from, a, b, target.radius)
                    and not self.walls.blocked(visible_from, eye)):
                dealt = self._damage(
                    rocket.shooter, target, blast.amount * scalar, False, blast.loadout, self.tick,
                    world_tick=self.tick,
                )
                if dealt:
                    has_direct_impulse = (target.entity == direct_target and rocket.direct is not None
                                          and rocket.direct.knockback is not None)
                    if not has_direct_impulse:
                        sigma_accretion.apply_impact_impulse(
                            self, rocket.direction, target, blast.knockback, point, distance,
                        )
                    self._hit_effect(target, blast.effect)
        if orb:
            for piece, amount in orb_wall_hits:
                self.walls.damage(piece, rocket.shooter, amount, self.tick)
            for bubble, amount in orb_shield_hits:
                self.shields.damage(bubble, rocket.shooter, amount, self.tick)
        else:
            self.walls.splash(rocket.shooter, point, blast, self.tick)
            self.shields.splash(rocket.shooter, blast, visible_from, self.tick)

    def _landed(self, rocket: Rocket) -> None:
        """The projectile ended (hit, range, lifetime): the predicted one's release follows."""
        if rocket in self.rockets:
            self.rockets.remove(rocket)
        if rocket.shot is not None:
            self.projectiles.flown(rocket.shot, rocket.flown)

    def heal_area(
        self, healer, centre, radius: float, pulse: float, *, first_tick: int, last_tick: int,
    ) -> None:
        """Heal nearby allies for retained field pulses, using their current poses."""
        for target in self._targets():
            ally = not self.match.game_map.free_for_all and target.team == healer.team
            if target.owner is not healer and not ally:
                continue
            vitals = self.vitals_of(target)
            if vitals.dead:
                continue
            a, b = target.segment()
            if segment_distance(centre, a, b) - target.radius > radius:
                continue
            given = vitals.heal(pulse * ana_grenade.field_pulse_scale(self, vitals, first_tick, last_tick))
            if given <= 0.0:
                continue
            healer.stats.add(stats.HEALING, given)
            self._sync_body_health(target.owner, vitals)

    def heal_breather(self, entity: int, amount: float) -> None:
        target = next((target for target in self._targets() if target.entity == entity), None)
        if target is None:
            return
        vitals = self.vitals_of(target)
        if vitals.dead or self.now < vitals.healing_block_until:
            return
        if self.now < vitals.healing_boost_until:
            amount *= ana_grenade.HEAL_BOOST
        given = vitals.heal(amount)
        if given > 0.0:
            target.owner.stats.add(stats.HEALING, given)
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

    @staticmethod
    def _fortify_state(owner):
        """Return the authored 0CF4 CombatModFilter during its live window."""
        script = getattr(owner, "body_script", None)
        if (script is None or script.component.world.hero != 0x02E000000000013E
                or script.component.strict or script.component.owner_dead):
            return None
        for instance in script.component.instances.values():
            if instance.graph.index == 0x0CF4:
                state = instance.states.get(4)
                if state is not None and state.active and not state.ending:
                    return state
        return None

    def _damage(
        self, shooter, target: Target, amount: float, critical: bool, loadout, frame: int,
        *, world_tick: int | None = None, beam: bool = False,
    ) -> float:
        vitals = self.vitals_of(target)
        if (vitals.dead or amount <= 0.0 or mei.protected(target.owner)
                or phase_defenses.active(target.owner)):
            return 0.0
        if self.shields.absorb(target.owner, shooter, amount, frame):
            return 0.0
        if (fortify := self._fortify_state(target.owner)) is not None:
            modifier = _number(fortify.evaluate(fortify.node.field("m_50530EAB"), watch=False), 0.0)
            amount *= max(0.0, min(1.0, 1.0 + modifier))
        if self.now < vitals.nano_until:
            amount *= nano_boost.INCOMING
        dealt = vitals.damage(amount * breather.incoming_scale(target.owner), beam=beam)
        if dealt <= 0.0:
            return 0.0
        killed = vitals.now[HEALTH_POOL] <= EPSILON
        if killed:
            vitals.now[HEALTH_POOL] = 0.0
            vitals.died_at = self.now
            vitals.death_tick = self.match.tick if world_tick is None else world_tick
            vitals.killer = _source_entity(shooter)
            vitals.clear_sleep()
            self._clear_frost(target.owner)
        else:
            if vitals.sleep_until > self.tick:
                wake = max(vitals.sleep_wake_floor, self.tick + math.ceil(SLEEP_WAKE_RECOVERY / TICK))
                vitals.sleep_until = min(vitals.sleep_until, wake)
            if loadout == mei.SPRAY_LOADOUT:
                self._frost_hit(target, source=shooter)
        if not isinstance(shooter, bots.Bot):
            flags = (CRITICAL if critical else 0) | (KILLING if killed else 0)
            self._tell_hit(shooter, target, dealt, flags, frame)
            shooter.stats.add(stats.HERO_DAMAGE, dealt)
            if (shooter is not target.owner
                    and (self.match.game_map.free_for_all or shooter.team != target.team)):
                mei_ultimate.grant(shooter, dealt, loadout=loadout)
        victim = target.owner
        if killed and isinstance(victim, bots.Bot):
            # The NPC death script owns its terminal health change and death event ordering.
            self._bot_died(shooter, victim, critical, loadout, vitals.death_tick)
            return dealt
        self._sync_body_health(victim, vitals)
        if killed:
            self._died(shooter, victim, critical, loadout)
        return dealt

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
        self.clear_pins(bot.entity)
        sniper_reposition.finish(self, bot)
        self.heavy_charge_contacts.pop(bot.entity, None)
        self.melee_seen.pop(bot.entity, None)
        self.shields.retire(bot.entity)
        self.stuns.pop(bot.entity, None)
        bot.died(tick)
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
        except Exception:
            bot.body_script = None
            log.exception("[game] %s: %s's body script failed on %X", self.match.label(), bot.name, DIED)
            return
        for viewer in self.match.players:
            if viewer.client is not None:
                viewer.client.queue_bot_death(bot.entity, event)

    def _advance_bot_death(self, bot: bots.Bot, tick: int) -> None:
        script = bot.body_script
        if script is None:
            return
        try:
            events = script.advance_death(tick, int(BOT_RESPAWN / TICK))
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
        self.stuns.pop(victim.body, None)
        self.clear_pins(victim.body)
        self.shields.retire(victim.body)
        sigma_flux.interrupt_flux_lift(self, victim)
        self.grasp_shields.pop(victim.body, None)
        self.grasp_grants.pop(victim.body, None)
        self.sigma_barriers.retire(victim.body)
        self.reinhardt_barriers.retire(victim.body)
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
        script = player.body_script
        retained = (expr.to_float(script.component.entity_var(342).value())
                    if script is not None and script.hero == mei.HERO else None)
        if not self.match.switch_hero(player, player.hero):
            self.respawns.append((self.now + RESPAWN_RETRY, player))
            return
        if (retained is not None and player.body_script is not None
                and not mei_ultimate.spawn_ready(player.body_script)):
            cost = expr.to_float(player.body_script.component.entity_var(338).value())
            player.body_script.set_ult_charge(min(cost, max(0., retained)))
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
