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
from dataclasses import dataclass, replace

from ow174.game import (
    barriers,
    bots,
    cryo,
    defense_matrix,
    enforcer_movement,
    heavy_charge,
    junkrat_trap,
    lucio_soundwave,
    mei_blizzard,
    observers,
    orisa_barrier,
    phase_defenses,
    pools,
    projectiles,
    sigma_barrier,
    sniper_reposition,
    sombra_hack,
    stats,
    voicelines,
    winston_barriers,
    world,
    zarya_barriers,
)
from ow174.game.bits import BitWriter
from ow174.game.collision import MOVER, World
from ow174.game.commands import Command
from ow174.game.mover import mover_data
from ow174.game.script import (
    charge,
    expr,
    graph,
    healing_status,
    nodes,
    projectile_defense,
    regeneration,
    runtime,
    support,
    support_effects,
)
from ow174.game.script.bot import DIED, BotScript
from ow174.game.script.driver import ULT_CHARGE, BodyScript, Snapshot
from ow174.game.statescript import InstanceWire, StateWire, remote_full_frame
from ow174.game.world import ARMOUR_POOL, HEALTH_POOL, OP_DESTROY, POOLS, SHIELDS_POOL

log = logging.getLogger("ow174.game")

HIT = 25001  # to the shooter: a hit
DEATH_NOTICE = 25004  # to the dead player: who killed him
KILL_NOTICE = 25005  # to the killer: whom he killed
KILLED_BY = 2  # 25004 +0x78: "you were killed by" (0F7B.025); anything else "you died" (0F7C.025)
CRITICAL, KILLING = 0x01, 0x02  # 25001 flags (unverified, see above)
MERCY_BLASTER_LOADOUT = 0x0B9000000000003E
DEAD = 0x2000  # movement-state flag: dead (the 3P anim's dead pose)
# 003E.025's parameters, by the low 16 bits of their 026 identifiers (01CF rid4).
KILLER, CRIT, LOADOUT, ASSISTS = 0x0012, 0x0095, 0x03E6, 0x03E7
MODE_UI = 0x0CB6  # the mode's client UI graph: the kill feed 0DEA and the notices 1FC0 (client-only)

EYE = 1.55  # m: STUFirstPersonComponent m_E29993A5, its default (OWLib) and Soldier's (DATA; unverified: eye)
#104319 explicit FirstPerson standing/crouched fields; camera-based combat
# origins are reconstructed,not animated muzzle/head bone geometry.
EYE_STANDING = {0x04000000000000E4: 1.45, 0x04000000000002A3: 1.425}
EYE_CROUCHED = {0x04000000000003CF: 1.085, 0x04000000000000E4: 1.015,
                0x04000000000002A3: 0.9975}
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
SLEEP_DART_EFFECT = 0x04A000000000026F
MEI_SPRAY_LOADOUT = 0x0B900000000000D3
FROST_INITIAL, FROST_STEP = expr.f32(0.2), expr.f32(0.0285)
FROST_GAP, FREEZE_SECONDS = 1.0, 1.3
FLASHBANG_MODE_FLAG = 13408  # 0228 duration: flag set1.7s, otherwise0.8s


# --- geometry -----------------------------------------------------------------------------------------


def eye_height(body: int, crouched: bool = False) -> float:
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
        self.invulnerable_until = 0.0
        self.control_locked_until = 0.0
        self.sleep_until = 0.0
        self.hack_until = 0.0
        self.pending_impulse: tuple[float, float, float] | None = None
        self.frost_sources: dict[tuple[int, int], tuple[float, float]] = {}
        self.frozen_until = 0.0

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
            if len(self.temporary_shields) + (SHIELDS_POOL in self.ids) >= POOLS:
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

    def _damage_losses(self, amount: float):
        """Resolve losses with the server's shield/armour/health rule without writing pools."""
        left = max(0.0, amount)
        for tag, pool in reversed(self.temporary_shields.items()):
            take = min(left, pool.now)
            if take > 0.0:
                left -= take
                yield tag, take
        for kind in (SHIELDS_POOL, ARMOUR_POOL, HEALTH_POOL):
            if left <= 0.0:
                break
            if kind == ARMOUR_POOL and self.now[kind] > 0.0:
                left = max(left - ARMOUR_BLOCK, left / 2)
            take = min(left, self.now[kind])
            if take > 0.0:
                left -= take
                yield kind, take

    def capped_damage(self, amount: float) -> float:
        return sum(take for _, take in self._damage_losses(amount))

    def damage(self, amount: float) -> float:
        """Apply the resolved losses, returning the actual amount removed."""
        taken = 0.0
        for kind, take in self._damage_losses(amount):
            if kind in self.temporary_shields:
                self.decay_temporary_shields(kind, take)
            else:
                self.now[kind] -= take
                self.changed.add(kind)
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
            if kind == SHIELDS_POOL:
                start = int(SHIELDS_POOL in self.ids)
                for slot, pool in enumerate(self.temporary_shields.values(), start):
                    parts[kind * POOLS + slot] = world.pool(pool.pool_id, kind, pool.most, pool.now)
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
    damage_scale: float = 1.0  # launch snapshot for resource-owned impact health applications
    damage_charge: tuple[int, tuple[expr.Asset, ...]] | None = None
    boost_credit: tuple[BodyScript, float] | None = None
    definition: int | None = None  # authored projectile asset, independent of observer transport


@dataclass(frozen=True)
class RangedContact:
    hit: HitDef
    origin: tuple
    direction: tuple
    reach: float
    view_tick: float
    charge_context: tuple | None
    definition: int | None


@dataclass(frozen=True)
class MeleeContact:
    hit: HitDef
    origin: tuple
    centre: tuple
    radius: float
    view_tick: float
    charge_context: tuple | None
    tags: tuple


@dataclass(frozen=True)
class PendingAttack:
    shooter: object
    source: BodyScript
    frame: int
    deadline: int
    waiting: tuple[BodyScript, ...]
    bonuses: tuple[tuple[BodyScript, float], ...]
    payload: RangedContact | MeleeContact | Rocket


class Combat:
    """The match's health, hits, deaths and respawns."""

    junkrat_traps = None

    def __init__(self, match) -> None:
        self.match = match
        self.vitals: dict[int, Vitals] = {}  # body or bot entity -> its health
        self.history: dict[int, deque] = {}  # entity -> (tick, feet, height, eye) of the last HISTORY ticks
        self.replacement_ticks: dict[int, int] = {}  # body -> first authoritative tick of its current life
        self.blizzard = mei_blizzard.Blizzard(self)
        self.sigma_barriers: dict[int, sigma_barrier.Barrier] = {}
        self.orisa_barriers = orisa_barrier.Barriers(self)
        self.winston_barriers = winston_barriers.Barriers(self)
        self.zarya_barriers = zarya_barriers.Barriers(self)
        self.barrier_geometry = barriers.configured_geometry()
        self.barriers: dict[int, barriers.Barrier] = {}
        self.grasp_shields: dict[int, projectile_defense.ShieldDecay] = {}
        self.grasp_grants: dict[int, tuple[int, int]] = {}
        self.rockets: list[Rocket] = []
        self.pending_attacks: list[PendingAttack] = []
        self.respawns: list[tuple[float, object]] = []  # (when, a player or a bot)
        self.next_bot = bots.BOT_ENTITY + len(match.bots)
        self.now = 0.0
        self.tick = 0
        self.melee_seen: dict[int, dict[tuple[int, int], int]] = {}  # body -> counters by instance/state
        self.heavy_impact_requests: dict[int, tuple] = {}
        self.heavy_pins: dict[int, tuple] = {}
        self.sniper_repositions = {}
        self.heavy_grazes: dict[int, set[int]] = {}
        self.pins: dict[int, int] = {}  # Assassin source body to pinned player body
        self.stuns: dict[int, int] = {}  # body identity -> exclusive world-tick deadline
        self.support_effects = support_effects.Effects(match)
        self.healing_status = healing_status.Statuses(match)
        self.projectiles = projectiles.Projectiles(self)  # leases, confirmations, the Biotic Field
        self.observers = observers.Observers(match)  # the bodies' statescript for the other clients
        self.voice = voicelines.VoiceLines(match)  # the hero's spawn voice line (voicelines.py)

    # --- hooks (match.py) --------------------------------------------------------------------------

    def is_dead(self, player) -> bool:
        vitals = self.vitals.get(player.body) if player.has_body else None
        return vitals is not None and vitals.dead

    def control_locked(self, player) -> bool:
        vitals = self.vitals.get(player.body) if player.has_body else None
        return vitals is not None and self.now < vitals.control_locked_until

    def resurrect_player(self, caster, entity: int, instance) -> bool:
        """Reconstructed00CA engine respawn: revive the same friendly player body.

        Completion/range/cooldown are authored by the caster graph. Full health,
        same-body revival and the00CB1.5s control/2.25s immunity windows are host
        policies pending native/server comparison and original-client acceptance.
        """
        if (caster.hero.guid != regeneration.MERCY or self.is_dead(caster)
                or self.match.game_map.free_for_all or caster.body == entity
                or caster.body_script is None or caster.body_script.component is not instance.component):
            return False
        target = next((t for t in self._targets() if t.entity == entity), None)
        if (target is None or target.team != caster.team or isinstance(target.owner, bots.Bot)
                or not self.vitals_of(target).dead):
            return False
        helper = graph.graph(0x00CB)
        if helper is None:
            return False
        immunity = next((n for n in helper.nodes if n.pos == 1), None)
        controls = next((n for n in helper.nodes if n.pos == 3), None)
        if immunity is None or controls is None:
            return False
        immune_seconds = _number(instance.evaluate(immunity.field("m_timeout")), 0.0)
        control_seconds = _number(instance.evaluate(controls.field("m_timeout")), 0.0)
        vitals = self.vitals_of(target)
        for kind, most in vitals.most.items():
            if vitals.now[kind] != most:
                vitals.now[kind] = most
                vitals.changed.add(kind)
        vitals.died_at = vitals.death_tick = vitals.killer = None
        vitals.invulnerable_until = self.now + immune_seconds
        vitals.control_locked_until = self.now + control_seconds
        vitals.sleep_until = vitals.hack_until = 0.0
        vitals.pending_impulse = None
        vitals.frost_sources.clear()
        vitals.frozen_until = 0.0
        self.respawns[:] = [(when, who) for when, who in self.respawns if who is not target.owner]
        self.support_effects.retire(entity)
        self.record_replacement(entity)
        self._sync_body_health(target.owner, vitals)
        subject = graph.graph(0x01C7)
        credit = next((n for n in subject.nodes if n.pos == 29), None) if subject else None
        if credit is not None:
            for stat in credit.field("m_stats") or ():
                caster.stats.add(expr.guid_of(stat), 1.0)
        return True

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
        self.barriers.pop(player.body, None)
        heavy_charge.retire_victim(self, player)
        self.blizzard.retire(player.body)
        if self.junkrat_traps is not None:
            self.junkrat_traps.retire(player.body)
        self.orisa_barriers.retire(player.body)
        self.winston_barriers.retire(player.body)
        self.zarya_barriers.retire(player.body)
        self.grasp_shields.pop(player.body, None)
        self.grasp_grants.pop(player.body, None)
        self.vitals.pop(player.body, None)
        self.history.pop(player.body, None)
        self.replacement_ticks.pop(player.body, None)
        self.melee_seen.pop(player.body, None)
        self.stuns.pop(player.body, None)
        self.clear_pins(player.body)
        self.support_effects.retire(player.body)
        self.pending_attacks[:] = [attack for attack in self.pending_attacks if attack.shooter is not player]
        self.healing_status.retire(player.body)
        # The owner's leased projectiles and their observer copies retire with the old body.
        self.rockets[:] = [
            rocket for rocket in self.rockets if rocket.shooter is not player or rocket.shot is None
        ]
        self.respawns[:] = [(when, who) for when, who in self.respawns if who is not player]

    def record_replacement(self, body: int) -> None:
        """Start a combat life independently of the rolling pose history."""
        self.replacement_ticks[body] = self.match.tick
        self.stuns.pop(body, None)

    def apply_hack(self, target: Target, duration: float) -> None:
        vitals = self.vitals_of(target)
        vitals.hack_until = max(vitals.hack_until, self.now + duration)
        script = target.owner.body_script
        for instance in tuple(script.component.instances.values()):
            if instance.graph.index in sombra_hack.MERCY_SKILLS:
                for state in tuple(instance.states.values()):
                    if isinstance(state, nodes.Ability):
                        state.presses.clear()
                        if support.interrupt_on_stun(script, state):
                            state.stop()

    def hack_blocks(self, state) -> bool:
        instance = state.instance
        if instance.graph.index not in sombra_hack.MERCY_SKILLS or not any(state.buttons()):
            return False
        vitals = self.vitals.get(instance.component.entity)
        return vitals is not None and self.now < vitals.hack_until

    def queue_impulse(self, target: Target, impulse) -> None:
        vitals = self.vitals_of(target)
        if (isinstance(target.owner, bots.Bot) or vitals.dead
                or not all(map(math.isfinite, impulse)) or not any(impulse)):
            return
        pending = vitals.pending_impulse or (0.0, 0.0, 0.0)
        vitals.pending_impulse = tuple(a + b for a, b in zip(pending, impulse, strict=True))

    def take_impulse(self, actor):
        vitals = self.vitals.get(_source_entity(actor))
        if vitals is None or vitals.dead:
            return None
        impulse, vitals.pending_impulse = vitals.pending_impulse, None
        return impulse

    def is_asleep(self, actor) -> bool:
        vitals = self.vitals.get(_source_entity(actor))
        return vitals is not None and not vitals.dead and self.now < vitals.sleep_until

    def frost_progress(self, actor) -> float:
        vitals = self.vitals.get(_source_entity(actor))
        if vitals is None or vitals.dead:
            return 0.0
        if self.now < vitals.frozen_until:
            return 1.0
        return max((progress for progress, until in vitals.frost_sources.values() if self.now < until),
                   default=0.0)

    def _frost_hit(self, shooter, target: Target, vitals: Vitals, *,
                   channel=0, initial=FROST_INITIAL, step=FROST_STEP) -> None:
        if self.now < vitals.frozen_until:
            return
        sources = vitals.frost_sources
        for entity, (_, until) in tuple(sources.items()):
            if until <= self.now:
                del sources[entity]
        source = (_source_entity(shooter), channel)
        old, _ = sources.get(source, (0.0, 0.0))
        progress = min(1.0, max(initial, expr.f32(old + step)))
        if progress < 1.0:
            sources[source] = progress, self.now + FROST_GAP
        else:
            sources.clear()
            vitals.frozen_until = self.now + FREEZE_SECONDS
            self._interrupt(target)

    def is_stunned(self, actor, *, tick: int | None = None) -> bool:
        entity = _source_entity(actor)
        at = self.tick if tick is None else tick
        return (heavy_charge.controlled(self, actor) or self.is_asleep(actor)
                or self.frost_progress(actor) >= 1.0 or entity in self.pins.values()
                or self.stuns.get(entity, 0) > at)

    def clear_pins(self, entity: int) -> None:
        for source, victim in tuple(self.pins.items()):
            if entity in (source, victim):
                del self.pins[source]

    def damage_immune(self, target: Target) -> bool:
        return (self.now < self.vitals_of(target).invulnerable_until
                or cryo.protected(target.owner) or phase_defenses.active(target.owner))

    def _hit_effect(self, target: Target, effect: int | None) -> None:
        """Resource-backed stun reconstruction; native immunity/presentation remain unverified."""
        vitals = self.vitals_of(target)
        if effect not in (FLASHBANG_EFFECT, SLEEP_DART_EFFECT) or vitals.dead or self.damage_immune(target):
            return
        if effect == SLEEP_DART_EFFECT:
            # 0BDD/14 applies effect026F after the dart's own 5 damage.
            vitals.sleep_until = self.now + 5.0
            self._interrupt(target)
            return
        variables = graph.mode_variables(self.match.game_map.mode_guid) or {}
        duration = 1.7 if expr.truthy(variables.get(FLASHBANG_MODE_FLAG)) else 0.8
        self.apply_stun(target, duration)

    def apply_stun(self, target: Target, duration: float) -> None:
        if self.vitals_of(target).dead:
            return
        deadline = self.tick + math.ceil(duration / TICK)
        self.stuns[target.entity] = max(deadline, self.stuns.get(target.entity, 0))
        self._interrupt(target)

    def _interrupt(self, target: Target) -> None:
        script = getattr(target.owner, "body_script", None)
        if script is None:
            return
        component = script.component
        if isinstance(target.owner, bots.Bot):
            from ow174.game import assassin

            assassin.bind(component.world, component, None)
        component.edges([(value, False) for value in tuple(component.held)])
        for instance in list(component.instances.values()):
            for state in list(instance.states.values()):
                if isinstance(state, nodes.Ability):
                    state.presses.clear()
                    if support.interrupt_on_stun(script, state):
                        state.stop()  # use authored exit/cooldown paths for running channels
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

    def command(self, player, command: Command | None) -> None:
        """Consume the body's reports. Only a frame with a pose can affect targets."""
        if self.match.collision_world is None:
            return
        script = player.body_script
        if script is None:
            return
        self._sync_barrier(player)
        self.winston_barriers.command(player, command, dead=self.is_dead(player))
        shots, script.component.shots[:] = list(script.component.shots), []
        healing, script.self_healing = script.self_healing, 0.0
        support_healing, script.support_healing = script.support_healing, []
        if self.is_dead(player):
            return
        self.healing_status.update(self.tick)
        if healing > 0.0:
            target = next((target for target in self._targets() if target.owner is player), None)
            if target is not None:
                vitals = self.vitals_of(target)
                vitals.heal(healing)
                self._sync_body_health(player, vitals)
        for frame, entity, amount, tags in support_healing:
            self.heal_target(player, entity, amount, frame, tags=tags)
        if script.hero == regeneration.MERCY:
            self._flush_pending_attacks()
        if self.is_stunned(player):
            return  # input/attacks are suppressed; passive healing still consumes actual frames
        if command is not None:
            for state, number, time, activation in shots:
                try:
                    self._shoot(player, command, state, number, time, activation=activation)
                except Exception:
                    log.exception("[game] %s: %s's shot failed", self.match.label(), player.name)
        if player.hero.guid == lucio_soundwave.HERO:
            lucio_soundwave.resolve(self, player, command, script)
        try:
            self._melee(player, command, script)
        except Exception:
            log.exception("[game] %s: %s's melee failed", self.match.label(), player.name)

    def _melee(self, player, command, script) -> None:
        """Quick melee: 0043 st4 (StateModifyHealth) hits what its st5 TrackTargets finds: a sphere of
        m_radius 1.0 at (0, 0, 1.5) from the eye (DATA; unverified: along the aim), #476 = 30 damage (DATA),
        once per activation of st4. The server cannot run TrackTargets itself (the VM finds nothing)."""
        for instance in tuple(script.component.instances.values()):
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
            charge_context = charge.damage_context(script, state, modify)
            tags = charge_context[1] if charge_context is not None else support.modify_health_tags(state, modify)
            loadout = (
                state.evaluate(modify.get("m_loadout"), watch=False) if modify.get("m_loadout") else None
            )
            loadout = loadout.guid if isinstance(loadout, expr.Asset) else None
            origin = self._eye(player)
            centre = _along(origin, aim(*command.aim()), reach)
            view_tick = command.frame - command.view_delay(player.quanta) * 1e6 / player.quanta
            self._submit_attack(player, command.frame, MeleeContact(
                HitDef(amount=amount, loadout=loadout), origin, centre, radius, view_tick,
                charge_context, tags,
            ))

    def _resolve_melee(self, shooter, frame, contact, amount, boost):
        origin, centre, radius = contact.origin, contact.centre, contact.radius
        self.melee_barriers(shooter, origin, centre, radius, amount)
        if self.junkrat_traps is not None:
            self.junkrat_traps.melee(shooter, origin, centre, radius, amount)
        for target in self._enemies(shooter):
            shown = self.seen(target, contact.view_tick)
            if shown is None:
                continue
            a, b = shown.segment()
            if (segment_distance(centre, a, b) - shown.radius <= radius and amount > 0.0
                    and capsule_visible(self._collision(), origin, a, b, shown.radius)):
                pinned = target.entity in self.pins
                before = self.vitals_of(target).total if pinned else 0
                self._damage(
                    shooter, target, amount, False, contact.hit.loadout, frame,
                    damage_charge=contact.charge_context, boost_credit=boost, melee_tags=contact.tags,
                )
                if pinned and self.vitals_of(target).total < before:
                    from ow174.game import assassin

                    assassin.melee_rescue(self, shooter, target)

    def update(self, now: float, tick: int, frames: bool = True) -> None:
        """Each tick: keep where the bodies are, fly the rockets, respawn, send health that changed. The
        other players' body frames only on the ticks that send (`frames`)."""
        if self.match.collision_world is None:
            return
        retained = max(self.tick + 1, tick - COMBAT_CATCH_UP + 1)
        first = tick
        if self.tick and tick > self.tick + 1 and any(
            (bot.firing or bot.entity in self.sniper_repositions or self._profile_play_active(bot))
            and not bot.dead and isinstance(bot.body_script, BotScript) for bot in self.match.bots
        ):
            first = retained
        for target in self._targets():
            history = self.history.setdefault(target.entity, deque(maxlen=HISTORY))
            history.append((tick, target.feet, target.height, target.eye))
        # Replay the retained combat frames using the most recent poses. Player input
        # accepted just before the scheduler caught up joins only at the endpoint.
        for at in range(first, tick + 1):
            self.now, self.tick = now - (tick - at) * TICK, at
            for player in self.match.players:
                script = player.body_script
                if (script is not None and script.hero == regeneration.MERCY and player.spawned
                        and not player.gone and not script.retired and script.component.fault is None):
                    try:
                        expired = charge.advance_valkyrie_expiry(script, at)
                        if expired is not None:
                            player.finish_valkyrie_pose(*expired)
                            self.support_effects.retire_valkyrie_children(script.entity, expired[0])
                        charge.advance_passive(script, at)
                    except (runtime.ScriptBudgetError, runtime.UnsupportedScriptError):
                        log.exception("[game] %s: %s's world timers failed", self.match.label(), player.name)
                        self.match.stop_body_script(player, failed=True)
            if self.barrier_geometry is not None:
                for player in self.match.players:
                    self._sync_barrier(player)
            projectile_defense.update_shields(self)
            self.healing_status.update(at)
            self.healing_status.prune_filters(at)
            self.blizzard.update(at)
            if self.junkrat_traps is not None:
                self.junkrat_traps.update(at)
            sigma_barrier.update_barriers(self)
            self.orisa_barriers.update(at)
            self.winston_barriers.update()
            self._flush_pending_attacks()
            for rocket in list(self.rockets):
                if at == tick or rocket.last_tick is not None:
                    self._fly(rocket)
            self._bot_weapons(at)
        self.projectiles.update(tick, first_tick=retained)
        self.match.deliver_body_messages()
        if frames:
            self.observers.update()
        # A skipped tick can reach a respawn deadline with the old body's terminal pool still dirty.
        self._send_health()
        for bot in tuple(self.match.bots):
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
        self.support_effects.prune(current)
        for entity in [key for key, deadline in self.stuns.items() if deadline <= tick]:
            del self.stuns[entity]
        for table in (self.history, self.vitals, self.replacement_ticks, self.stuns):
            for entity in [key for key in table if key not in current]:
                del table[entity]

    # --- targets -----------------------------------------------------------------------------------

    def script_targets(self, source: int, state):
        """Reconstructed Mercy staff selection, restricted to its actual query states.

        Radius and cone angle are evaluated from104319 fields. Capsule-centre
        aim ranking/occlusion and half-angle interpretation await native/client
        comparison. Other target geometries and status filters remain unmounted.
        """
        if state.instance.graph.index == sombra_hack.EMP:
            return sombra_hack.emp_targets(self, state)
        if state.instance.graph.index == sombra_hack.WEAPON:
            return sombra_hack.track(self, state)
        if state.instance.graph.index == 0x025C:
            return self.zarya_barriers.track_targets(state)
        staff = state.instance.graph.index == 0x006B and state.index in (4, 5, 51)
        guardian = state.instance.graph.index == 0x006E and state.index in (5, 14)
        resurrect = state.instance.graph.index == 0x00CA and state.index in (14, 38)
        if not staff and not guardian and not resurrect:
            return NotImplemented
        player = next((p for p in self.match.players if p.has_body and p.body == source), None)
        if player is None or player.hero.guid != regeneration.MERCY:
            return NotImplemented
        if self.is_dead(player) or self.match.game_map.free_for_all:
            return ()
        radius = _number(state.evaluate(state.node.field("m_radius"), watch=False), 0.0)
        angle = _number(state.evaluate(state.node.field("m_A30B383E"), watch=False), 360.0)
        origin = self._eye(player)
        direction = aim(player.mover.yaw, player.mover.pitch)
        retained = None
        if staff and state.index == 5:
            selected = state.instance.find_var(graph.INSTANCE, 375).value()
            retained = {item.id for item in selected if isinstance(item, expr.Entity)} if isinstance(
                selected, tuple) else set()
        elif staff:
            hold = state.instance.states.get(5)
            if hold is not None and hold.active:
                # A retiring hold cancels its queued begins. Let its authored
                # reset finish before the acquisition state hands off again.
                return ()
        component = state.instance.component
        frame = component.frame
        expiry = component.world.historical_valkyrie_deadline() if staff else None
        historical_boost = (expiry is not None and frame < self.tick and state.now < expiry
                            and support_effects.staff_boost_selected(state.instance))
        candidates = []
        for target in self._targets():
            if target.entity == source or target.team != player.team:
                continue
            dead = self.vitals_of(target).dead
            if resurrect and (not dead or isinstance(target.owner, bots.Bot)):
                continue
            if historical_boost:
                target = support_effects.boost_target_at(self, target, frame)
                if target is None:
                    continue
            elif dead and not resurrect and (not guardian or isinstance(target.owner, bots.Bot)):
                continue
            if retained is not None and target.entity not in retained:
                continue
            centre = (target.feet[0], target.feet[1] + target.height * 0.5, target.feet[2])
            offset = _sub(centre, origin)
            distance = math.sqrt(_dot(offset, offset))
            if distance > radius or distance <= EPSILON:
                continue
            forward = tuple(value / distance for value in offset)
            alignment = _dot(direction, forward)
            if angle < 360 and alignment < math.cos(math.radians(max(0.0, angle) * 0.5)):
                continue
            wall = ray_world(self._collision(), origin, forward, distance)
            if wall is not None and wall < distance:
                continue
            candidates.append((-alignment, distance, target.entity))
        candidates.sort()
        return tuple(row[2] for row in candidates[:1])

    @staticmethod
    def _barrier_instance(player):
        script = player.body_script
        if script is None or script.hero != 0x02E0000000000007:
            return None
        return next((i for i in script.component.instances.values() if i.graph.index == 0x95), None)

    def _sync_barrier(self, player):
        geometry = self.barrier_geometry
        if geometry is None or not player.has_body:
            return None
        instance = self._barrier_instance(player)
        if instance is None:
            return None
        state = self.barriers.get(player.body)
        if state is None:
            state = barriers.Barrier(geometry.maximum, geometry.maximum, self.now, self.now)
            self.barriers[player.body] = state
        pet = instance.states.get(17)
        if self.is_dead(player) or player.gone or not player.spawned or player.client is None:
            if pet is not None and pet.active:
                self._lower_barrier(instance)
            state.active, state.last_update = False, self.now
            return state
        if (self.is_stunned(player) or self.control_locked(player)) and pet is not None and pet.active:
            self._lower_barrier(instance)
        raised = pet is not None and pet.active
        if not raised and state.active:
            state.regen_after = self.now + geometry.regen_delay
        if not raised and self.now > max(state.last_update, state.regen_after):
            duration = self.now - max(state.last_update, state.regen_after)
            state.current = expr.f32(min(state.maximum, state.current + geometry.regen_rate * duration))
        if raised and (state.current <= 0.0 or self.now < state.broken_until):
            self._lower_barrier(instance)
            raised = False
        state.active = raised and state.current > 0.0 and self.now >= state.broken_until
        state.last_update = max(self.now, state.last_update)
        current = instance.find_var(0, 586)
        if not expr.same(current.value(), expr.f32(state.current)):
            instance.write((0, 586), expr.f32(state.current))
        return state

    @staticmethod
    def _lower_barrier(instance):
        ability = instance.states.get(59)
        if isinstance(ability, nodes.Ability):
            ability.stop()
        pet = instance.states.get(17)
        if pet is not None and pet.active:
            pet.request(runtime.ABORT)
        instance.component.run_now()

    def _enemy_barriers(self, shooter):
        if self.barrier_geometry is None:
            return
        source = _source_entity(shooter)
        for owner in self.match.players:
            if owner is shooter or owner.body == source:
                continue
            if not self.match.game_map.free_for_all and owner.team == shooter.team:
                continue
            state = self._sync_barrier(owner)
            if state is None or not state.active:
                continue
            yield owner, state

    def _barrier_surfaces(self, shooter, centre):
        for sphere in self.winston_barriers.enemies(shooter):
            offset = tuple(a - b for a, b in zip(centre, sphere.centre, strict=True))
            length = math.hypot(*offset)
            direction = tuple(axis / length for axis in offset) if length else (0.0, 0.0, 1.0)
            yield sphere.owner, sphere, _along(sphere.centre, direction, sphere.radius)
        for shield in tuple(self.orisa_barriers.enemies(shooter)):
            point = self.orisa_barriers.geometry.closest(shield.position, shield.yaw, centre)
            yield shield.owner, shield, point
        for shield in sigma_barrier.enemies(self, shooter):
            data = shield.geometry.data
            triangles = (data[i:i + 9] for i in range(0, len(data), 13))
            yield shield.owner, shield, barriers.closest_mesh_point(
                triangles, shield.position, shield.basis, centre,
            )
        for owner, shield in self._enemy_barriers(shooter):
            mover = owner.mover
            position, axes = barriers.frame(
                self.barrier_geometry, mover.position, mover.yaw, mover.pitch,
                mover.values.stand_radius, mover.values.stand_height,
            )
            yield owner, shield, barriers.closest_mesh_point(
                self.barrier_geometry.triangles, position, axes, centre,
            )

    def melee_barriers(self, shooter, origin, centre, radius, amount):
        if amount <= 0:
            return
        for owner, shield, point in self._barrier_surfaces(shooter, centre):
            if math.dist(centre, point) > radius:
                continue
            offset = tuple(end - start for start, end in zip(origin, point, strict=True))
            distance = math.hypot(*offset)
            if distance > EPSILON:
                wall = ray_world(self._collision(), origin, tuple(v / distance for v in offset), distance)
                if wall is not None and wall < distance - EPSILON:
                    continue
            self._damage_barrier((distance, owner, shield), amount)

    def _barrier_contacts(self, shooter, origin, direction, length, radius=0.0):
        found = []
        for owner, state in self._enemy_barriers(shooter):
            distance = barriers.intersect(self.barrier_geometry, owner.mover.position,
                                          owner.mover.yaw, owner.mover.pitch,
                                          origin, direction, length, radius,
                                          owner.mover.values.stand_radius, owner.mover.values.stand_height)
            if distance is not None:
                found.append((distance, owner, state))
        if radius == 0.0:
            found.extend(self.winston_barriers.contacts(shooter, origin, direction, length))
            found.extend(self.orisa_barriers.contacts(shooter, origin, direction, length))
            found.extend(sigma_barrier.contacts(self, shooter, origin, direction, length))
        return sorted(found, key=lambda hit: (hit[0], hit[1].body))

    def _barrier_contact(self, shooter, origin, direction, length, radius=0.0):
        found = self._barrier_contacts(shooter, origin, direction, length, radius)
        return found[0] if found else None

    def _damage_barrier(self, contact, amount):
        _, owner, state = contact
        if isinstance(state, sigma_barrier.Barrier):
            sigma_barrier.damage_barrier(self, state, amount)
            return
        if isinstance(state, orisa_barrier.Barrier):
            self.orisa_barriers.damage(state, amount)
            return
        if isinstance(state, winston_barriers.Bubble):
            state.damage(amount)
            self.winston_barriers.update()
            return
        if amount <= 0.0 or not state.active:
            return
        state.current = expr.f32(max(0.0, state.current - amount))
        instance = self._barrier_instance(owner)
        if instance is None:
            return
        instance.write((0, 586), state.current)
        if state.current <= 0.0:
            geometry = self.barrier_geometry
            state.broken_until = self.now + geometry.break_delay
            state.regen_after = self.now + geometry.regen_delay
            state.active = False
            self._lower_barrier(instance)
            instance.write((0, 61), geometry.break_delay)
            instance.component.run_now()

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
        return found

    def vitals_of(self, target: Target) -> Vitals:
        found = self.vitals.get(target.entity)
        if found is None:
            if isinstance(target.owner, bots.Bot):
                found = Vitals(target.entity, target.owner.health)
            else:
                hero = target.owner.hero
                found = Vitals(target.entity, *pools.body_pools(hero.guid, hero.body))
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
            if self.vitals_of(target).dead or phase_defenses.active(target.owner):
                continue
            found.append(target)
        return found

    def seen(self, target: Target, view_tick: float) -> Target | None:
        """Interpolate retained poses after the start of the target's current life."""
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

    def damage_scale(self, shooter) -> float:
        boost = self._damage_boost(shooter)
        return boost[1] if boost is not None else 1.0

    def _damage_boost(self, shooter, *, frame=None) -> tuple[BodyScript, float] | None:
        """Capture the largest bonus and its exact source script at attack creation."""
        return self._best_boost(self._boost_candidates(shooter, frame))

    def _boost_candidates(self, shooter, frame):
        if self.match.game_map.free_for_all:
            return
        entity = _source_entity(shooter)
        for healer in self.match.players:
            script = healer.body_script
            if healer is shooter or healer.team != shooter.team or script is None or script.retired:
                continue
            if self.is_dead(healer):
                recorded = self.support_effects.historical_boosts(script.entity, frame)
                if recorded is not None:
                    yield script, recorded.get(entity, 0.0)
            else:
                yield script, support.boost_amount(script, entity, frame=frame)

    @staticmethod
    def _best_boost(candidates):
        winner = max(candidates, key=lambda item: item[1], default=None)
        if winner is None or winner[1] <= 0:
            return None
        source, bonus = winner
        return source, 1.0 + bonus

    def _awaited_boost_sources(self, shooter, frame):
        if self.match.game_map.free_for_all or not self.tick - HISTORY <= frame < self.tick:
            return ()
        waiting = []
        for healer in self.match.players:
            script = healer.body_script
            if (healer is shooter or healer.team != shooter.team or healer.gone
                    or script is None or script.hero != regeneration.MERCY
                    or script.retired or script.frame >= frame or self.is_dead(healer)):
                continue
            retained = getattr(healer, "valkyrie_pose", None)
            if ((retained is not None and (frame + 1) * script.frame_ms - 1 < retained.expires_ms)
                    or self.support_effects.historical_boosts(script.entity, frame) is not None):
                waiting.append(script)
        return tuple(waiting)

    def _submit_attack(self, shooter, frame, payload):
        if not isinstance(shooter, bots.Bot) and (source := shooter.body_script) is not None:
            view = frame if isinstance(payload, Rocket) else min(frame, math.floor(payload.view_tick))
            deadline = view + HISTORY - 1
            waiting = self._awaited_boost_sources(shooter, frame) if self.tick < deadline else ()
            if waiting or any(attack.source is source for attack in self.pending_attacks):
                bonuses = tuple(self._boost_candidates(shooter, frame))
                self.pending_attacks.append(PendingAttack(shooter, source, frame, deadline, waiting, bonuses, payload))
                return
        self._commit_attack(shooter, frame, payload, self._damage_boost(shooter, frame=frame))

    def _flush_pending_attacks(self):
        if not self.pending_attacks:
            return
        pending, self.pending_attacks = self.pending_attacks, []
        blocked = set()
        for attack in pending:
            source = attack.source
            if attack.shooter.gone or attack.shooter.body_script is not source or source.retired:
                continue
            if (source in blocked or (self.tick < attack.deadline and any(
                not script.retired and not script.component.owner_dead and script.frame < attack.frame
                for script in attack.waiting
            ))):
                blocked.add(source)
                self.pending_attacks.append(attack)
                continue
            try:
                candidates = []
                for caster, bonus in attack.bonuses:
                    if caster in attack.waiting and not caster.retired:
                        recorded = self.support_effects.historical_boosts(caster.entity, attack.frame, pending=True)
                        if recorded is not None:
                            bonus = recorded.get(source.entity, 0.0)
                        elif caster.component.owner_dead:
                            # The attack was waiting for a frame this caster never simulated.
                            bonus = 0.0
                    candidates.append((caster, bonus))
                self._commit_attack(attack.shooter, attack.frame, attack.payload, self._best_boost(candidates))
            except Exception:
                log.exception("[game] %s: %s's pending attack failed", self.match.label(), attack.shooter.name)

    def _commit_attack(self, shooter, frame, payload, boost):
        scale = boost[1] if boost is not None else 1.0
        if isinstance(payload, Rocket):
            if scale != 1.0:
                if payload.direct is not None:
                    payload.direct = replace(payload.direct, amount=payload.direct.amount * scale)
                if payload.blast is not None:
                    payload.blast = replace(payload.blast, amount=payload.blast.amount * scale)
            payload.damage_scale, payload.boost_credit = scale, boost
            self.rockets.append(payload)
        elif isinstance(payload, MeleeContact):
            self._resolve_melee(shooter, frame, payload, payload.hit.amount * scale, boost)
        else:
            hit = replace(payload.hit, amount=payload.hit.amount * scale) if scale != 1.0 else payload.hit
            self._hitscan(shooter, hit, payload.origin, payload.direction, payload.reach, frame,
                          payload.view_tick, boost=boost, charge_context=payload.charge_context,
                          definition=payload.definition)

    @staticmethod
    def _profile_play_active(bot) -> bool:
        script = bot.body_script
        return (isinstance(script, BotScript)
                and script.component.world.profile_play_queue(script.component) is not None)

    def _bot_weapons(self, tick: int) -> None:
        """Advance the weapon bodies of the map's firing bots and consume their volley reports."""
        from ow174.game import npc_control

        for bot in tuple(self.match.bots):
            script = bot.body_script
            controlled = npc_control.manual(bot)
            if ((not bot.firing and not controlled and bot.entity not in self.sniper_repositions
                 and not self._profile_play_active(bot)) or bot.dead
                    or not isinstance(script, BotScript) or script.frame > tick):
                continue
            try:
                allowed = npc_control.update_input(self, bot) if controlled else not self.is_stunned(bot)
                if bot.spawner.get("owned_pet"):
                    from ow174.game import bob

                    bob.move(self, bot, tick)
                    allowed = bob.update_input(self, bot)
                sniper_reposition.step(self, bot, tick)
                moved = heavy_charge.move(bot, self, tick)
                snapshot = script.advance_live(tick, bot.position)
                if getattr(script.component.world, "pet_destroy_requested", False):
                    from ow174.game import bob

                    bob.finish_retirement(self.match, bot)
                    continue
                if bot.body == heavy_charge.BODY:
                    heavy_charge.finish_frame(bot, self, moved)
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
                heavy_charge.release(self, bot.entity)
                sniper_reposition.cancel(self, bot)
                self.heavy_impact_requests.pop(bot.entity, None)
                self.heavy_grazes.pop(bot.entity, None)
                enforcer_movement.retire(script)
                script.component.world.retire_profile_play_queue(script.component)
                bot.body_script = None
                log.exception("[game] %s: %s's weapon script failed", self.match.label(), bot.name)

    def _eye(self, player) -> tuple:
        x, y, z = player.mover.position
        crouched = player.mover.crouched
        height = eye_height(player.hero.body, crouched)
        return (x, y + height, z)

    def _shoot(
        self, shooter, command, state, number: int, time: int,
        *, activation: tuple[int, ...], snapshot: Snapshot | None = None,
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
        if not npc and self.blizzard.fired(
            shooter, state, shot_frame, origin, direction, activation=activation, snapshot=snapshot,
        ):
            return
        hit = node.field("m_6396149F")
        direct = hit_def(state, hit)
        if (direct is not None and direct.loadout == MERCY_BLASTER_LOADOUT and not npc
                and shooter.hero.guid == regeneration.MERCY
                and state.instance.graph.index == 0x006D and node.pos == 10
                and hit.get("m_A872C70E") is None
                and expr.to_int(state.evaluate(hit.get("m_66D29548"), watch=False)) == 2):
            # 006D/node10 supplies mode2 and omits the float override. Use a
            # scoped 2x server policy; mode2 itself is not a multiplier, and
            # the native downstream default/animated head hitbox is unproven.
            direct = replace(direct, critical=2.0)
        charge_context = None
        if (not npc and direct is not None and direct.loadout == MERCY_BLASTER_LOADOUT
                and state.instance.graph.index == 0x006D and node.pos == 10):
            charge_context = charge.damage_context(
                shooter.body_script, state, hit.get("m_modifyHealth") or {},
            )
        speed = field("m_projectileSpeed")
        if speed:
            lifetime = field("m_projectileLifetime", 5.0) or 5.0
            reach = field("m_FEC435C6")
            if reach:
                lifetime = min(lifetime, reach / speed)
            if not npc and junkrat_trap.launches(state):
                if self.junkrat_traps is None:
                    self.junkrat_traps = junkrat_trap.Traps(self)
                self.junkrat_traps.launch(shooter, state, origin, direction, speed, lifetime)
                return
            if not npc and self.orisa_barriers.launch(shooter, state, origin, direction, speed, lifetime):
                return
            blast = hit_def(state, node.field("m_D2D11CE9"))
            entity_def = (node.field("m_projectileEntity") or {}).get("m_entityDef")
            definition = state.evaluate(entity_def, watch=False) if entity_def is not None else None
            rays = (direction,)
            if (npc and shooter.hero == 0x02E00000000001CE
                    and state.instance.graph.index == 0x13EB and node.pos == 24):
                from ow174.game.pellets import ring_directions

                rays = ring_directions(state, direction)
            damaging = (direct is not None and direct.amount > 0.0) or (
                blast is not None and blast.amount > 0.0
            )
            for direction in rays:
                shot = None
                if isinstance(definition, expr.Asset):
                    shot = self.projectiles.fired(
                        shooter, state, shot_frame, definition.guid, lifetime, origin, direction,
                        activation=activation, snapshot=snapshot,
                    )
                if damaging or shot is not None:
                    self._submit_attack(
                        shooter, shot_frame,
                        Rocket(
                            shooter, shooter.team, origin, direction, speed, lifetime, direct, blast, shot,
                            last_tick=self.tick if npc else None,
                            damage_charge=charge_context,
                            definition=definition.guid if isinstance(definition, expr.Asset) else None,
                        )
                    )
            return
        if direct is None or direct.amount <= 0.0:
            return
        reach = field("m_FEC435C6", RANGE) or RANGE
        # The input supplies this simulation frame's aim and view time. Notices use the volley clock.
        view_tick = self.tick
        if command is not None:
            view_tick = command.frame - command.view_delay(shooter.quanta) * 1e6 / shooter.quanta
        definition = None
        if npc:
            expected = projectile_defense.NPC_HITSCAN.get((state.instance.graph.index, node.pos))
            if expected is not None and state.evaluate(
                node.field("m_projectileEntity.m_entityDef"), watch=False,
            ) == expr.Asset(expected):
                definition = expected
        elif (expected := projectile_defense.PLAYER_HITSCAN.get((state.instance.graph.index, node.pos))) is not None:
            entity_def = state.evaluate(node.field("m_projectileEntity.m_entityDef"), watch=False)
            if isinstance(entity_def, expr.Asset) and entity_def.guid in expected:
                definition = entity_def.guid
        self._submit_attack(shooter, shot_frame, RangedContact(
            direct, origin, direction, reach, view_tick, charge_context, definition,
        ))

    def _hitscan(self, shooter, direct, origin, direction, reach, shot_frame, view_tick, *,
                 boost=None, charge_context=None, definition=None, reflectors=()):
        targets = self._enemies(shooter)
        best = None
        for target in targets:
            shown = self.seen(target, view_tick)
            if shown is None:
                continue
            a, b = shown.segment()
            t = ray_capsule(origin, direction, a, b, shown.radius)
            if t is not None and t <= reach and (best is None or t < best[0]):
                best = (t, target, shown)
        if direct.amount > 0 and self.junkrat_traps is not None:
            device = self.junkrat_traps.contact(shooter, origin, direction, best[0] if best else reach)
            if device is not None and (best is None or device[0] < best[0]):
                best = (*device, None)
        limit = best[0] if best is not None else reach
        shield = self._barrier_contact(shooter, origin, direction, limit)
        if definition is not None:
            wall = ray_world(self._collision(), origin, direction, limit)
            bound = min(limit, wall if wall is not None else limit, shield[0] if shield is not None else limit)
            defense = projectile_defense.intersection(targets, origin, direction, bound, aim, definition)
            matrix_bound = min(bound, defense[0]) if defense is not None else bound
            if defense_matrix.absorbable(definition):
                matrix = self._matrix_distance(targets, origin, direction, matrix_bound)
                if matrix is not None and matrix < matrix_bound:
                    return
            if defense is not None and defense[0] < bound:
                distance, defender, (state, facing, absorb) = defense
                if absorb:
                    projectile_defense.report_absorption(
                        state, _source_entity(shooter), direct.amount * direct.scale(distance),
                    )
                # Overlapping reflect boxes can meet the same ray at zero distance.
                elif defender.entity not in reflectors:
                    if boost is not None:
                        direct = replace(direct, amount=direct.amount / boost[1])
                    self._hitscan(defender.owner, direct, _along(origin, direction, distance), facing,
                                  reach - distance, self.tick, self.tick, definition=definition,
                                  reflectors=(*reflectors, defender.entity))
                return
        if (shield is not None and (best is None or shield[0] < limit)
                and ray_world(self._collision(), origin, direction, shield[0]) is None):
            self._damage_barrier(shield, direct.amount * direct.scale(shield[0]))
            return
        if best is None:
            return
        t, target, shown = best
        wall = ray_world(self._collision(), origin, direction, t)
        if wall is not None and wall < t:
            return
        if isinstance(target, junkrat_trap.Device):
            self.junkrat_traps.damage(target, direct.amount * direct.scale(t))
            return
        hit_y = origin[1] + direction[1] * t
        critical = hit_y >= shown.feet[1] + shown.eye - HEAD_BELOW
        amount = direct.amount * direct.scale(t) * (direct.critical if critical else 1.0)
        absorbed = self._damage(
            shooter, target, amount, critical and direct.critical > 1.0, direct.loadout, shot_frame,
            world_tick=self.tick if isinstance(shooter, bots.Bot) else None,
            damage_charge=charge_context, boost_credit=boost,
        )
        if not absorbed:
            self._hit_effect(target, direct.effect)

    def _intercept_projectile(self, rocket: Rocket, target: Target, defense, point, duration: float) -> None:
        state, facing, absorb = defense
        if absorb:
            amount = sum(hit.amount for hit in (rocket.direct, rocket.blast) if hit is not None)
            self._landed(rocket)
            projectile_defense.report_absorption(state, _source_entity(rocket.shooter), amount)
            return
        # Retire the old predicted segment, then continue the authoritative
        # projectile under the defender's faction and aimed direction.
        if rocket.shot is not None:
            self.projectiles.flown(rocket.shot, rocket.flown)
            rocket.shot = None
        rocket.shooter, rocket.team = target.owner, target.team
        rocket.position, rocket.direction = point, facing
        rocket.left -= duration
        if rocket.damage_scale != 1.0:
            if rocket.direct is not None:
                rocket.direct = replace(rocket.direct, amount=rocket.direct.amount / rocket.damage_scale)
            if rocket.blast is not None:
                rocket.blast = replace(rocket.blast, amount=rocket.blast.amount / rocket.damage_scale)
        rocket.damage_scale = 1.0
        rocket.damage_charge = rocket.boost_credit = None

    def _fly(self, rocket: Rocket) -> None:
        """Sweep elapsed world time against the current bodies and static collision."""
        # Player input can arrive immediately before the scheduler accounts for a gap.
        elapsed = 1 if rocket.last_tick is None else self.tick - rocket.last_tick
        if elapsed <= 0:
            return
        rocket.last_tick = self.tick
        duration = min(elapsed * TICK, rocket.left)
        if duration <= 0.0:
            self._landed(rocket)
            return
        # Sweep the remaining flight even when the range or lifetime ends inside this tick.
        step = rocket.speed * duration
        best = None
        blast = rocket.blast
        grenade = blast is not None and blast.effect == healing_status.GRENADE
        scripted_grenade = grenade and self.healing_status.available()
        if scripted_grenade:
            source = _source_entity(rocket.shooter)
            free_for_all = self.match.game_map.free_for_all
            targets = (
                t for t in self._targets()
                if t.entity != source and not self.vitals_of(t).dead
                and ((not free_for_all and t.team == rocket.team) or not phase_defenses.active(t.owner))
            )
        else:
            targets = self._enemies(rocket.shooter)
        for target in targets:
            a, b = target.segment()
            t = ray_capsule(rocket.position, rocket.direction, a, b, target.radius)
            if t is not None and t <= step and (best is None or t < best[0]):
                best = (t, target)
        if self.junkrat_traps is not None and rocket.direct is not None and rocket.direct.amount > 0:
            device = self.junkrat_traps.contact(
                rocket.shooter, rocket.position, rocket.direction, best[0] if best else step,
            )
            if device is not None and (best is None or device[0] < best[0]):
                best = device
        wall = ray_world(self._collision(), rocket.position, rocket.direction, step)
        if wall is not None and (best is None or wall < best[0]):
            best = (wall, None)
        blast_shield = None
        if blast is not None and blast.amount > 0:
            limit = best[0] if best is not None else step
            candidate = self._barrier_contact(rocket.shooter, rocket.position, rocket.direction, limit)
            if candidate is not None and (best is None or candidate[0] < limit):
                blast_shield = candidate
                best = (candidate[0], None)
        definition = self._projectile_definition(rocket)
        if grenade or (rocket.direct is not None and rocket.direct.amount > 0 and blast is None
                       and (rocket.direct.loadout == MERCY_BLASTER_LOADOUT
                            or rocket.direct.effect == SLEEP_DART_EFFECT
                            or definition == projectile_defense.ANA_RIFLE_PROJECTILE
                            or definition in projectile_defense.TALON_PROJECTILES)):
            limit = best[0] if best is not None else step
            shield = None if grenade else self._barrier_contact(
                rocket.shooter, rocket.position, rocket.direction, limit,
            )
            shield_hit = shield is not None and (best is None or shield[0] < limit)
            if shield_hit:
                limit = shield[0]
            defense_targets = self._enemies(rocket.shooter) if grenade else targets
            defense = projectile_defense.intersection(
                defense_targets, rocket.position, rocket.direction, limit, aim, definition,
            )
            matrix_limit = min(limit, defense[0]) if defense is not None else limit
            if self._absorb_in_matrix(
                rocket, defense_targets, definition, matrix_limit,
                blocked=best is not None or shield_hit or defense is not None,
            ):
                return
            if defense is not None and (not (shield_hit or best is not None) or defense[0] < limit):
                distance, owner, contact = defense
                rocket.flown += distance / rocket.speed
                point = _along(rocket.position, rocket.direction, distance)
                self._intercept_projectile(rocket, owner, contact, point, duration)
                return
            if shield_hit:
                rocket.flown += shield[0] / rocket.speed
                self._damage_barrier(shield, rocket.direct.amount)
                self._landed(rocket)
                return
        elif rocket.direct is not None and rocket.direct.amount > 0 and rocket.blast is None:
            limit = best[0] if best is not None else step
            shield = self._barrier_contact(rocket.shooter, rocket.position, rocket.direction, limit)
            if shield is not None and (best is None or shield[0] < limit):
                rocket.flown += shield[0] / rocket.speed
                self._damage_barrier(shield, rocket.direct.amount)
                self._landed(rocket)
                return
        if best is None:
            rocket.position = _along(rocket.position, rocket.direction, step)
            rocket.flown += duration
            rocket.left -= duration
            if rocket.left <= 0.0:
                self._landed(rocket)
            return
        t, struck = best
        rocket.flown += t / rocket.speed
        point = _along(rocket.position, rocket.direction, t)
        self._landed(rocket)
        if isinstance(struck, junkrat_trap.Device):
            self.junkrat_traps.damage(struck, rocket.direct.amount)
        elif struck is not None and rocket.direct is not None and rocket.direct.amount > 0.0:
            direct = rocket.direct
            critical = (direct.loadout == MERCY_BLASTER_LOADOUT and direct.critical > 1.0
                        and point[1] >= struck.feet[1] + struck.eye - HEAD_BELOW)
            absorbed = self._damage(
                rocket.shooter, struck, direct.amount * (direct.critical if critical else 1.0),
                critical, direct.loadout, self.tick,
                world_tick=self.tick, damage_charge=rocket.damage_charge, boost_credit=rocket.boost_credit,
            )
            if not absorbed:
                self._hit_effect(struck, direct.effect)
        blast = rocket.blast
        if blast is None or blast.amount <= 0.0:
            return
        # Sample cover before the explosion spends shield durability.
        visible_from = _along(point, rocket.direction, -0.0001) if wall is not None or blast_shield else point
        splash_hits = []
        for target in self._targets() if scripted_grenade else self._enemies(rocket.shooter):
            a, b = target.segment()
            distance = max(0.0, segment_distance(point, a, b) - target.radius)
            scalar = blast.splash(distance)
            if scalar <= 0 or not capsule_visible(self._collision(), visible_from, a, b, target.radius):
                continue
            if self._blast_occluded(rocket.shooter, visible_from, target):
                continue
            splash_hits.append((target, scalar))
        self._splash_barriers(rocket, point, visible_from, blast, blast_shield)
        for target, scalar in splash_hits:
            if scripted_grenade:
                #0267's original faction graphs own instant100 healing/60
                #damage. Its outer50 event is not applied a second time.
                hostile = target.owner is not rocket.shooter and (
                    self.match.game_map.free_for_all or target.team != rocket.team
                )
                if hostile and self.zarya_barriers.intercept(
                    target.owner, blast.amount * scalar, _source_entity(rocket.shooter),
                ):
                    continue
                self.healing_status.impact(
                    rocket.shooter, target, rocket.damage_scale, boost_credit=rocket.boost_credit,
                )
                continue
            absorbed = self._damage(
                rocket.shooter, target, blast.amount * scalar, False, blast.loadout, self.tick,
                world_tick=self.tick, boost_credit=rocket.boost_credit,
            )
            if not absorbed:
                self._hit_effect(target, blast.effect)

    def _splash_barriers(self, rocket, impact, visible_from, blast, direct_contact):
        hits = []
        if direct_contact is not None:
            direct_amount = rocket.direct.amount if rocket.direct is not None else 0
            hits.append((direct_contact, direct_amount + blast.amount * blast.splash(0)))
        for owner, shield, point in self._barrier_surfaces(rocket.shooter, impact):
            if direct_contact is not None and shield is direct_contact[2]:
                continue
            amount = blast.amount * blast.splash(math.dist(impact, point))
            if amount <= 0:
                continue
            offset = tuple(end - start for start, end in zip(visible_from, point, strict=True))
            distance = math.hypot(*offset)
            if distance > EPSILON:
                direction = tuple(axis / distance for axis in offset)
                reach = distance - EPSILON
                if ray_world(self._collision(), visible_from, direction, reach) is not None:
                    continue
                obstruction = self._barrier_contact(rocket.shooter, visible_from, direction, reach)
                if obstruction is not None and obstruction[2] is not shield:
                    continue
            hits.append(((distance, owner, shield), amount))
        for contact, amount in hits:
            self._damage_barrier(contact, amount)

    def _blast_occluded(self, shooter, origin, target):
        a, b = target.segment()
        point = a[0], max(a[1], min(b[1], origin[1])), a[2]
        offset = tuple(end - start for start, end in zip(origin, point, strict=True))
        length = math.hypot(*offset)
        reach = max(0.0, length - target.radius)
        return reach > 0 and self._barrier_contact(
            shooter, origin, tuple(axis / length for axis in offset), reach,
        ) is not None

    @staticmethod
    def _projectile_definition(rocket: Rocket) -> int | None:
        if rocket.definition is None and rocket.shot is not None:
            return rocket.shot.definition
        return rocket.definition

    def _absorb_in_matrix(self, rocket, targets, definition, limit, *, blocked):
        if not defense_matrix.absorbable(definition):
            return False
        distance = self._matrix_distance(targets, rocket.position, rocket.direction, limit)
        if distance is None or (blocked and distance >= limit):
            return False
        self.rockets.remove(rocket)
        if rocket.shot is not None:
            self.projectiles.absorbed(rocket.shot)
        return True

    @staticmethod
    def _matrix_distance(targets, origin, direction, reach):
        nearest = None
        for target in targets:
            player = target.owner
            if isinstance(player, bots.Bot) or player.hero.body != defense_matrix.MECH_BODY:
                continue
            distance = defense_matrix.matrix_distance(
                player.body_script, origin, direction, reach,
                (target.feet[0], target.feet[1] + target.eye, target.feet[2]),
                aim(player.mover.yaw, player.mover.pitch),
            )
            if distance is not None and (nearest is None or distance < nearest):
                nearest = distance
        return nearest

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
            ally = not self.match.game_map.free_for_all and target.team == healer.team
            if target.owner is not healer and not ally:
                continue
            vitals = self.vitals_of(target)
            if vitals.dead:
                continue
            a, b = target.segment()
            if segment_distance(centre, a, b) - target.radius > radius:
                continue
            given = vitals.heal(amount * self.healing_status.scale(target.entity))
            if given <= 0.0:
                continue
            healer.stats.add(stats.HEALING, given)
            self._sync_body_health(target.owner, vitals)

    def heal_target(self, healer, entity: int, amount: float, frame: int, *,
                    allow_self=False, tags=None) -> float:
        """Apply a reported friendly-target heal to the same live body lifecycle.

        The staff selector owns range/occlusion. Selected active031A status
        filters modify actual received healing; general native combat-filter
        semantics and other healing/status services remain open.
        """
        if ((self.match.game_map.free_for_all and entity != healer.body)
                or (entity == healer.body and not allow_self) or amount <= 0):
            return 0.0
        if self.replacement_ticks.get(entity, frame) > frame:
            return 0.0
        target = next((target for target in self._targets() if target.entity == entity), None)
        if target is None or target.team != healer.team:
            return 0.0
        vitals = self.vitals_of(target)
        if vitals.dead:
            return 0.0
        given = vitals.heal(amount * self.healing_status.scale(
            target.entity, tuple(tag.guid for tag in tags or ()), frame=frame))
        if given > 0:
            healer.stats.add(stats.HEALING, given)
            self._sync_body_health(target.owner, vitals)
            charge.healed(healer.body_script, entity, given, tags, frame=frame)
        return given

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
    def _genji_blocks_melee(shooter, target: Target, tags) -> bool:
        """03A3/107 zeros matching frontal health changes while Deflect is active."""
        if isinstance(target.owner, bots.Bot) or target.owner.hero.guid != 0x02E0000000000029:
            return False
        script = getattr(target.owner, "body_script", None)
        if script is None or script.retired:
            return False
        for instance in script.component.instances.values():
            if instance.graph.index != 0x03A3:
                continue
            state = instance.states.get(59)  # CombatModFilter at node 107, separate from the projectile boxes.
            if state is None or not state.active or state.ending:
                continue
            filter_tags = {expr.guid_of(value) for value in state.node.field("m_F60AA143") or ()}
            if not any(tag.guid in filter_tags for tag in tags):
                continue
            x, _, z = shooter.position if isinstance(shooter, bots.Bot) else shooter.mover.position
            tx, _, tz = target.feet
            fx, _, fz = aim(target.owner.mover.yaw, 0)
            return expr.f32((x - tx) * fx + (z - tz) * fz) > expr.EPSILON
        return False

    def _damage(
        self, shooter, target: Target, amount: float, critical: bool, loadout, frame: int,
        *, world_tick: int | None = None, damage_charge=None, boost_credit=None, melee_tags=(),
    ) -> bool:
        """Apply a hit and report whether a Zarya barrier absorbed it."""
        vitals = self.vitals_of(target)
        if vitals.dead or amount <= 0.0 or self.damage_immune(target):
            return False
        if (shooter is not target.owner
                and self.zarya_barriers.intercept(target.owner, amount, _source_entity(shooter))):
            return True
        if melee_tags and self._genji_blocks_melee(shooter, target, melee_tags):
            return False
        amount *= phase_defenses.fortify_damage_scale(target.owner, target.entity)
        base = vitals.capped_damage(amount / boost_credit[1]) if boost_credit is not None else 0.0
        dealt = vitals.damage(amount)
        if dealt <= 0.0:
            return False
        # Only a later admitted positive hit wakes the sleeper.
        vitals.sleep_until = 0.0
        extra = max(0.0, dealt - base) if boost_credit is not None else 0.0
        if damage_charge is not None:
            charge.damaged(shooter.body_script, target.entity, dealt, damage_charge,
                           frame=frame if world_tick is None else world_tick, extra=extra)
        if boost_credit is not None and extra > EPSILON:
            source, scale = boost_credit
            impact = frame if world_tick is None else world_tick
            historical = self.support_effects.historical_boosts(source.entity, impact, pending=True) is not None
            charge.boosted(source, target.entity, dealt, extra, scale - 1.0,
                           valkyrie_frame=impact if historical else None)
        killed = vitals.now[HEALTH_POOL] <= EPSILON
        if killed:
            vitals.now[HEALTH_POOL] = 0.0
            vitals.died_at = self.now
            vitals.death_tick = self.match.tick if world_tick is None else world_tick
            vitals.killer = _source_entity(shooter)
            vitals.pending_impulse = None
            vitals.frost_sources.clear()
            vitals.frozen_until = 0.0
        elif loadout == MEI_SPRAY_LOADOUT:
            self._frost_hit(shooter, target, vitals)
        if not isinstance(shooter, bots.Bot):
            flags = (CRITICAL if critical else 0) | (KILLING if killed else 0)
            self._tell_hit(shooter, target, dealt, flags, frame)
            shooter.stats.add(stats.HERO_DAMAGE, dealt)
        victim = target.owner
        if killed and isinstance(victim, bots.Bot):
            # The NPC death script owns its terminal health change and death event ordering.
            self._bot_died(shooter, victim, critical, loadout, vitals.death_tick)
            return False
        self._sync_body_health(victim, vitals)
        if not killed and not isinstance(victim, bots.Bot) and victim.body_script is not None:
            sender = _source_entity(shooter)
            regeneration.damaged(victim.body_script, sender, self.tick)
            if damage_charge is not None:
                sombra_hack.damaged(victim.body_script, sender, damage_charge[1])
        if killed:
            self._died(shooter, victim, critical, loadout, vitals.death_tick)
        return False

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
        heavy_charge.release(self, bot.entity)
        sniper_reposition.cancel(self, bot)
        self.heavy_grazes.pop(bot.entity, None)
        self.support_effects.retire(bot.entity)
        self.healing_status.retire(bot.entity)
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
            lifetime = None if bot.spawner.get("owned_pet") else int(BOT_RESPAWN / TICK)
            events = script.advance_death(tick, lifetime)
        except Exception:
            bot.body_script = None
            log.exception("[game] %s: %s's death script timer failed", self.match.label(), bot.name)
            return
        for event in events:
            for viewer in self.match.players:
                if viewer.client is not None:
                    viewer.client.queue_bot_death_update(bot.entity, event)
        if getattr(script.component.world, "pet_destroy_requested", False):
            from ow174.game import bob

            bob.finish_retirement(self.match, bot)

    def _died(self, killer, victim, critical: bool, loadout, death_tick: int) -> None:
        """A player died: the notices, his body script's "died" (the kill feed line), and the respawn."""
        heavy_charge.retire_victim(self, victim)
        victim.clear_valkyrie_pose()
        self.clear_pins(victim.body)
        self.zarya_barriers.retire(victim.body)
        script = victim.body_script
        boost_end_frame = min(death_tick, script.frame + 1) if script is not None else None
        self.support_effects.retire(victim.body, boost_end_frame=boost_end_frame)
        self.healing_status.retire(victim.body)
        self.stuns.pop(victim.body, None)
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
        old_script = player.body_script
        points = None
        pending = ()
        if old_script is not None and player.hero.guid == regeneration.MERCY:
            points = old_script.component.entity_var(ULT_CHARGE).value()
            pending = getattr(old_script.world, "pending_ult_credits", ())
        if not self.match.switch_hero(player, player.hero):
            self.respawns.append((self.now + RESPAWN_RETRY, player))
            return
        if points is not None and player.body_script is not None:
            script = player.body_script
            script.set_ult_charge(points)
            charge.set_pending_credits(script.world, tuple(credit for credit in pending
                                                          if credit[0] > script.component.now))
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
            kinds = vitals.ids.keys() | {SHIELDS_POOL} if vitals.temporary_shields else vitals.ids
            components = vitals.component(kinds)
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
