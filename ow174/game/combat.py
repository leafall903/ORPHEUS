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
    adaptive_shield,
    ball_grapple,
    ball_mines,
    baptiste_field,
    barriers,
    bots,
    defenses,
    doomfist_passive,
    graviton,
    hand_cannon,
    heavy_charge,
    hook,
    junkrat_trap,
    knockback,
    lucio_barrier,
    lucio_soundwave,
    mei_blizzard,
    mei_wall,
    meteor_strike,
    observers,
    orisa_barrier,
    orisa_halt,
    piledriver,
    pools,
    projectiles,
    quad_cannons,
    reinhardt_charge,
    rocket_punch,
    seismic_slam,
    sigma_barrier,
    sniper_reposition,
    sombra,
    spread,
    stats,
    symmetra_barrier,
    ultimate,
    voicelines,
    winston_barrier,
    world,
    zarya_barriers,
)
from ow174.game.bits import BitWriter
from ow174.game.collision import MOVER, World
from ow174.game.commands import Command
from ow174.game.mover import STEP, mover_data
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
# Selected104319 FirstPerson fields; other bodies retain the explicit fallback.
EYE_STANDING = {0x040000000000028F: 1.6, 0x04000000000002A3: 1.425}
EYE_CROUCHED = {0x04000000000003CF: 1.085, 0x040000000000028F: 1.12,
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
MELEE_LOADOUT = 0x0B900000000002F5
MELEE = {0x0043: (4, 1.5, 1.0)}  # graph -> (its ModifyHealth state, metres ahead of the eye, radius): DATA
ARMOUR_BLOCK = 5.0  # damage armour stops per hit (unverified: OW1's rule; the client has no damage code)
TICK = 0.016
EPSILON = 1e-9
INCOMING_DAMAGE_SCALE = 8243  # live graph multiplier: Breather0.5, Fortify0.6, Ironclad0.8; normal1.
ANA_SLEEP_MINIMUM = 1.5
ANA_SLEEP_WAKE_RECOVERY = 0.5
MEI_PRIMARY_FREEZE_EFFECT = 0x04A0000000000169  # .053/0169 -> 0B77 -> 05E7 -> 224E
ANA_SLEEP_EFFECT = 0x04A000000000026F  # .053/026F -> 0BDE -> 224E
ANA_ANTIHEAL_EFFECT = 0x04A0000000000267  # .053/0267 -> 0C71 -> 0B99
FLASHBANG_EFFECT = 0x04A0000000000079  # .053/0079 ->0228 ->224E
FLASHBANG_MODE_FLAG = 13408  # 0228 duration: flag set1.7s, otherwise0.8s
MAX_DEFLECTIONS = 8  # bounded host recursion; native overlapping-box policy remains open


# --- geometry -----------------------------------------------------------------------------------------


def eye_height(body: int, crouched: bool) -> float:
    """Authored standing/crouched FirstPerson height for selected bodies; explicit fallback otherwise."""
    return EYE_CROUCHED.get(body, EYE_CROUCHED_DEFAULT) if crouched else EYE_STANDING.get(body, EYE)


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


def capsule_visible(collision: World | None, origin, a, b, radius: float, *, walls=None) -> bool:
    """Reconstructed visibility: a static ray to the nearest capsule surface."""
    ab, ap = _sub(b, a), _sub(origin, a)
    length = _dot(ab, ab)
    share = 0.0 if length <= EPSILON else max(0.0, min(1.0, _dot(ap, ab) / length))
    toward = _sub(_along(a, ab, share), origin)
    distance = math.sqrt(_dot(toward, toward))
    if distance <= radius + EPSILON:
        return True
    reach = distance - radius
    direction = tuple(axis / distance for axis in toward)
    hit = ray_world(collision, origin, direction, reach)
    if hit is not None and hit + EPSILON < reach:
        return False
    return walls is None or not walls.blocked(origin, _along(origin, direction, reach))


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
        self.conditional_pools: dict[int, TemporaryShield] = {}
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
        self.ult_healing_deficit = 0.0  # reconstructed enemy-caused HP eligible for healing charge
        self.healing_damage: list[tuple[int, float]] = []
        self.health_changes: dict[int, float] = {}

    @property
    def dead(self) -> bool:
        return self.died_at is not None

    def pool_total(self, kind):
        extra = self.conditional_pools.get(kind)
        return self.now[kind] + (extra.now if extra is not None else 0)

    @property
    def permanent_total(self):
        return sum(self.now.values()) + sum(pool.now for pool in self.conditional_pools.values())

    @property
    def permanent_maximum(self):
        return sum(self.most.values()) + sum(pool.most for pool in self.conditional_pools.values())

    @property
    def total(self) -> float:
        return self.permanent_total + sum(pool.now for pool in self.temporary_shields.values())

    @property
    def maximum(self) -> float:
        return self.permanent_maximum + sum(pool.most for pool in self.temporary_shields.values())

    def next_pool_id(self):
        return 1 + max((*self.ids.values(), *(p.pool_id for p in self.temporary_shields.values()),
                        *(p.pool_id for p in self.conditional_pools.values())), default=0)

    def set_conditional_pool(self, kind, amount):
        """A scoped NPC pool owns slot one for its kind throughout this body lifetime."""
        if not math.isfinite(amount) or amount < 0:
            raise ValueError("conditional pools require a finite nonnegative amount")
        pool = self.conditional_pools.get(kind)
        if pool is None:
            if amount == 0:
                return
            pool = TemporaryShield(self.next_pool_id(), 0, 0)
            self.conditional_pools[kind] = pool
        pool.most = pool.now = amount
        self.changed.add(kind)
        self._cap_healing_deficit(self.ult_healing_deficit)

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
            reserved = 2 if SHIELDS_POOL in self.conditional_pools else 1
            if len(self.temporary_shields) >= POOLS - reserved:
                raise ValueError("temporary shield pool slots exhausted")
            pool = self.temporary_shields[tag] = TemporaryShield(self.next_pool_id(), 0, 0)
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

    def damage(self, amount: float, *, floor: float = 0.0) -> float:
        """Take a hit from shields, then armour, then health (unverified, as OW1 did: what reaches armour
        while it has some is ARMOUR_BLOCK less, at most halved). Returns what the pools lost."""
        left = max(0.0, amount)
        taken = 0.0
        available = max(0.0, self.total - floor)
        for tag in reversed(self.temporary_shields):
            take = self.decay_temporary_shields(tag, min(left, max(0.0, available - taken)))
            left -= take
            taken += take
        for kind in (SHIELDS_POOL, ARMOUR_POOL, HEALTH_POOL):
            if left <= 0.0:
                break
            if kind == ARMOUR_POOL and self.now[kind] > 0.0:
                left = max(left - ARMOUR_BLOCK, left / 2)
            pool = self.conditional_pools.get(kind)
            if pool is not None:
                take = min(left, pool.now, available - taken)
                if take > 0:
                    pool.now -= take
                    self.changed.add(kind)
                    left -= take
                    taken += take
            take = min(left, self.now[kind], available - taken)
            if take > 0.0:
                self.now[kind] -= take
                self.changed.add(kind)
                left -= take
                taken += take
        return taken

    def record_health_change(self, tick: int, amount: float) -> None:
        self.health_changes[tick] = self.health_changes.get(tick, 0.0) + amount

    def historical_heal_capacity(self, tick: int) -> float:
        later = sum(change for at, change in self.health_changes.items() if at > tick)
        return max(0.0, self.permanent_maximum - self.permanent_total + later)

    def credit_healing_damage(self, amount: float, tick: int) -> None:
        previous = self.ult_healing_deficit
        self.ult_healing_deficit = min(self.permanent_maximum - self.permanent_total, previous + amount)
        added = self.ult_healing_deficit - previous
        if added > 0:
            if self.healing_damage and self.healing_damage[-1][0] == tick:
                _, last = self.healing_damage[-1]
                self.healing_damage[-1] = tick, last + added
            else:
                self.healing_damage.append((tick, added))

    def healing_eligible(self, tick: int | None) -> float:
        if tick is None:
            return self.ult_healing_deficit
        future = sum(amount for at, amount in self.healing_damage if at > tick)
        return max(0.0, self.ult_healing_deficit - future)

    def heal(self, amount: float, *, tick: int | None = None) -> float:
        """Heal health, then armour, then shields (unverified: the order). Returns what it gave."""
        left = max(0.0, amount)
        for kind in (HEALTH_POOL, ARMOUR_POOL, SHIELDS_POOL):
            give = min(left, self.most[kind] - self.now[kind])
            if give > 0.0:
                self.now[kind] += give
                self.changed.add(kind)
                left -= give
            pool = self.conditional_pools.get(kind)
            if pool is not None:
                give = min(left, pool.most - pool.now)
                if give > 0:
                    pool.now += give
                    self.changed.add(kind)
                    left -= give
        given = max(0.0, amount) - left
        eligible = min(given, self.healing_eligible(tick))
        self._cap_healing_deficit(self.ult_healing_deficit - eligible)
        return given

    def _cap_healing_deficit(self, eligible: float) -> None:
        self.ult_healing_deficit = max(0.0, min(eligible, self.permanent_maximum - self.permanent_total))
        # Undated damage is older than every tracked entry. Trim the oldest
        # tracked damage when consumed or displaced by the remaining HP deficit.
        tracked = sum(amount for _, amount in self.healing_damage)
        remaining = max(0.0, tracked - self.ult_healing_deficit)
        for index, (at, amount) in enumerate(self.healing_damage):
            if amount > remaining:
                self.healing_damage[index] = at, amount - remaining
                del self.healing_damage[:index]
                break
            remaining -= amount
        else:
            self.healing_damage.clear()

    def component(self, kinds) -> dict:
        """Component 51 with these pools' fields set (the others are left as they are)."""
        parts: list = [None] * (3 * POOLS)
        for kind in kinds:
            if kind in self.ids:
                parts[kind * POOLS] = world.pool(self.ids[kind], kind, self.most[kind], self.now[kind])
        for kind, pool in self.conditional_pools.items():
            parts[kind * POOLS + 1] = world.pool(pool.pool_id, kind, pool.most, pool.now)
        start = 2 if SHIELDS_POOL in self.conditional_pools else int(SHIELDS_POOL in self.ids)
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
    impulse: object = None  # selected Whole Hog external-force specification
    stun_ms: int = 0  # selected authored receiver duration for a direct hit

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


def hit_def(state, cfg, *, critical_default=1.0) -> HitDef | None:
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
    stun_ms = 0
    if (state.instance.graph.index, state.node.pos) == (0x1BBB, 9) and cfg is state.node.field("m_6396149F"):
        # Reconstruct the direct hit's 2947 damage-message branch as a host
        # control event, using the duration from the authored 224E invocation.
        receiver = state.instance.graph.node(18)
        duration = receiver.field("m_69F4B52C.m_6525E98B.m_1EB5A024")[0]["m_value"]
        stun_ms = nodes.seconds_to_ms(state.evaluate(duration, watch=False))
    return HitDef(
        amount=amount,
        loadout=loadout.guid if isinstance(loadout, expr.Asset) else None,
        falloff_start=value("m_EAD7F104"),
        falloff_end=value("m_157E5BC5"),
        falloff_floor=value("m_72A81154"),
        critical=value("m_A872C70E", critical_default),
        rings=tuple(sorted(rings)),
        effect=effect.guid if isinstance(effect, expr.Asset) else None,
        impulse=knockback.evaluate(state, modify),
        stun_ms=stun_ms,
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
    critical_allowed: bool = True

    def critical_at(self, hit_y: float, hit: HitDef) -> bool:
        return (self.critical_allowed and 0 < hit.critical != 1.0
                and hit_y >= self.feet[1] + self.eye - HEAD_BELOW and not defenses.fortified(self.owner))

    def segment(self, feet=None):
        """The capsule's segment (from the feet to the top, a radius in from both ends)."""
        x, y, z = feet or self.feet
        return (x, y + self.radius, z), (x, y + max(self.radius, self.height - self.radius), z)


@dataclass
class FragmentBurst:
    """Resolved Roadhog slug child volley; emission-on-expiry is reconstructed."""

    directions: tuple
    speed: float
    lifetime: float
    direct: HitDef | None
    blast: HitDef | None
    definition: int | None = None


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
    burst: FragmentBurst | None = None
    definition: int | None = None
    reflected: bool = False
    source_body: int | None = None  # Hand Cannon retains the emitting body across replacement.


class Combat:
    """The match's health, hits, deaths and respawns."""

    def __init__(self, match) -> None:
        self.match = match
        self.vitals: dict[int, Vitals] = {}  # body or bot entity -> its health
        self.history: dict[int, deque] = {}  # entity -> (tick, feet, height, eye, critical_allowed)
        self.replacement_ticks: dict[int, int] = {}  # replacement body -> authoritative world tick
        self.rockets: list[Rocket] = []
        self.respawns: list[tuple[float, object]] = []  # (when, a player or a bot)
        self.next_bot = bots.BOT_ENTITY + len(match.bots)
        self.now = 0.0
        self.tick = 0
        self.melee_seen: dict[int, dict[tuple[int, int], int]] = {}  # body -> counters by instance/state
        self.barrier_geometry = barriers.configured_geometry()
        self.brigitte_geometry = barriers.brigitte_geometry()
        self.barriers: dict[int, barriers.Barrier] = {}
        self.charges: dict[int, reinhardt_charge.Charge] = {}
        self.punch_dashes: dict[int, rocket_punch.Dash] = {}
        self.punch_walls: dict[int, rocket_punch.WallImpact] = {}
        self.doomfist_shields: dict[int, defenses.ShieldDecay] = {}
        self.slams: dict[int, seismic_slam.Cast] = {}
        self.meteors: dict[int, meteor_strike.Cast] = {}
        self.pile_air_locks: dict[int, tuple[int, int]] = {}
        self.adaptive_shields: dict[int, adaptive_shield.Lease] = {}
        self.heavy_charges: dict[int, heavy_charge.Contact] = {}
        self.sniper_repositions: dict[int, object] = {}
        self.pins: dict[int, int] = {}  # manual Assassin source body -> controlled victim body
        self.hard_control_command_cutoffs: dict[int, int] = {}
        self.stuns: dict[int, int] = {}  # body identity -> exclusive world-tick deadline
        self.sleep: dict[int, int] = {}
        self.sleep_wake_floor: dict[int, int] = {}
        self.mei_frost: dict[tuple[int, int, int], tuple[int, int]] = {}
        self.blizzard = mei_blizzard.Blizzard(self)
        self.walls = mei_wall.Walls(self)
        self.sound_barriers = lucio_barrier.Barriers(self)
        self.gravitons = graviton.Gravitons(self)
        self.traps = junkrat_trap.Traps(self)
        self.mines = ball_mines.Minefield(self)
        self.ball_grapples = {}
        self.grapple_seen = {}
        self.ball_contacts = {}
        self.antiheal: dict[int, int] = {}
        self.heal_boost: dict[int, int] = {}
        self.healing_status_history: dict[int, list[tuple[bool, int, int]]] = {}
        self.breather_deadlines: dict[int, int] = {}
        self.projectiles = projectiles.Projectiles(self)  # leases, confirmations, the Biotic Field
        self.hooks = hook.Hooks(self)
        self.impulses = knockback.Impulses(self)
        self.zarya_bubbles = zarya_barriers.Barriers(self)
        self.winston_bubbles = winston_barrier.Barriers(self)
        self.baptiste_fields = baptiste_field.Fields(self)
        self.orisa_barriers = orisa_barrier.Barriers(self)
        self.halt = orisa_halt.Halt(self)
        self.sigma_barriers = sigma_barrier.Barriers(self)
        self.grasp_shields: dict[int, defenses.ShieldDecay] = {}
        self.grasp_grants: dict[int, tuple[int, int]] = {}
        self.symmetra_barriers = symmetra_barrier.Barriers(self)
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
        self.hooks.retire(player.body)
        self.impulses.retire(player.body)
        self.zarya_bubbles.retire(player.body)
        self.sigma_barriers.retire(player.body)
        self.grasp_shields.pop(player.body, None)
        self.grasp_grants.pop(player.body, None)
        self.symmetra_barriers.retire(player.body)
        self.baptiste_fields.retire(player.body)
        self.mines.retire(player.body)
        ball_grapple.retire(self, player.body)
        self.blizzard.retire(player.body)
        self.walls.retire(player.body)
        self.sound_barriers.retire(player.body)
        adaptive_shield.drop(self, player.body)
        self.gravitons.retire(player.body)
        self.halt.retire(player.body)
        self.traps.retire_body(player.body)
        self.traps.player_command_cutoffs.pop(player.body, None)
        self.clear_pins(player.body)
        self.hard_control_command_cutoffs.pop(player.body, None)
        rocket_punch.retire(self, player.body, replaced=True)
        self.slams.pop(player.body, None)
        self.meteors.pop(player.body, None)
        self.vitals.pop(player.body, None)
        self.history.pop(player.body, None)
        self.replacement_ticks.pop(player.body, None)
        self.melee_seen.pop(player.body, None)
        self.clear_statuses(player.body)
        self.barriers.pop(player.body, None)
        # Scrap pellets and reflected flights can lack a projectile lease.
        # Their current shooter still owns the flight.
        scrap = player.hero.guid == 0x02E0000000000040
        for rocket in self.rockets:
            if rocket.shooter is player and rocket.source_body is not None:
                rocket.shot = None  # The old body's presentation lease retires separately.
        self.rockets[:] = [
            rocket for rocket in self.rockets
            if (rocket.shooter is not player or rocket.source_body is not None
                or (rocket.shot is None and not (scrap or rocket.reflected)))
        ]
        self.respawns[:] = [(when, who) for when, who in self.respawns if who is not player]

    def record_replacement(self, body: int) -> None:
        """Keep the creation boundary independent of the rolling pose history."""
        self.replacement_ticks[body] = self.match.tick
        self.clear_pins(body)
        self.clear_statuses(body)

    def clear_statuses(self, entity: int) -> None:
        self.stuns.pop(entity, None)
        self.pile_air_locks.pop(entity, None)
        self.sleep.pop(entity, None)
        for pair in list(self.mei_frost):
            if entity in pair[:2]:
                del self.mei_frost[pair]
        self.sleep_wake_floor.pop(entity, None)
        self.antiheal.pop(entity, None)
        self.heal_boost.pop(entity, None)
        self.healing_status_history.pop(entity, None)
        self.breather_deadlines.pop(entity, None)

    def clear_antiheal(self, entity: int) -> None:
        self.antiheal.pop(entity, None)
        history = self.healing_status_history.get(entity)
        if history is not None:
            # Live commands see the cleanse immediately. For missing commands,
            # treat this command-driven transition as occurring after this tick's healing.
            history[:] = [(boost, start, end if boost else min(end, self.tick + 1))
                          for boost, start, end in history]

    def _set_healing_status(self, entity, *, boost):
        deadline = self.tick + math.ceil(4.0 / TICK)
        table = self.heal_boost if boost else self.antiheal
        table[entity] = deadline
        self.healing_status_history.setdefault(entity, []).append((boost, self.tick, deadline))

    def revise_world_pose(self, entity, feet):
        history = self.history.get(entity)
        if history:
            frame, _, height, eye, critical_allowed = history[-1]
            history[-1] = frame, tuple(feet), height, eye, critical_allowed

    def control_deadline(self, entity: int) -> int:
        return max(self.stuns.get(entity, 0), self.sleep.get(entity, 0))

    def is_stunned(self, actor, *, tick: int | None = None) -> bool:
        entity = _source_entity(actor)
        return (entity in self.pins.values() or reinhardt_charge.pinned(self, entity)
                or self.control_deadline(entity) > (self.tick if tick is None else tick))

    @staticmethod
    def track_control_input(player, tick: int, cutoffs: dict[int, int]) -> None:
        # Project controlled world ticks from the last admitted input clock.
        # The inclusive cutoff survives release so an old input batch cannot act.
        if player.command_clock is not None:
            frame, received = player.command_clock
            elapsed_us = (tick - received) * round(TICK * 1_000_000)
            cutoff = frame + elapsed_us // player.quanta
            cutoffs[player.body] = max(cutoff, cutoffs.get(player.body, -1))

    def clear_pins(self, entity: int) -> None:
        reinhardt_charge.retire(self, entity)
        for source, victim in list(self.pins.items()):
            if entity in (source, victim):
                del self.pins[source]

    def _hit_effect(self, target: Target, effect: int | None, *, stun_ms: int = 0) -> None:
        """Host selected receiver effects on the server's body clock."""
        if not stun_ms and effect not in (FLASHBANG_EFFECT, ANA_SLEEP_EFFECT, ANA_ANTIHEAL_EFFECT):
            return
        if (self.vitals_of(target).dead or defenses.protection(target.owner)[0]
                or self.zarya_bubbles.for_target(target.owner) is not None):
            return
        if effect == ANA_ANTIHEAL_EFFECT:
            self._set_healing_status(target.entity, boost=False)
            return
        if defenses.control_immune(target.owner):
            return
        entity = target.entity
        if effect == ANA_SLEEP_EFFECT:
            minimum = self.tick + math.ceil(ANA_SLEEP_MINIMUM / TICK)
            if minimum <= self.control_deadline(entity):
                return
            self.stuns.pop(entity, None)
            self.sleep[entity] = self.tick + math.ceil(5.0 / TICK)
            self.sleep_wake_floor[entity] = minimum
        else:
            if not stun_ms:
                variables = graph.mode_variables(self.match.game_map.mode_guid) or {}
                stun_ms = 1700 if expr.truthy(variables.get(FLASHBANG_MODE_FLAG)) else 800
            deadline = self.tick + math.ceil(stun_ms / (1000 * TICK))
            if deadline <= self.control_deadline(entity):
                return
            self.sleep.pop(entity, None)
            self.sleep_wake_floor.pop(entity, None)
            self.stuns[entity] = deadline
        self._interrupt(target)

    def _mei_frost_contact(self, shooter, target: Target, *, channel=0, floor=2000, increment=285) -> None:
        """Reconstructed source binding and refresh for the authored 0B77 paths."""
        if self.vitals_of(target).dead or defenses.control_immune(target.owner):
            return
        pair = (_source_entity(shooter), target.entity, channel)
        amount, expires = self.mei_frost.get(pair, (0, 0))
        if self.tick >= expires:
            amount = 0
        # Integer units preserve the two authored floors and increments.
        updated = min(10000, max(floor, amount + increment))
        self.mei_frost[pair] = updated, self.tick + math.ceil(1.0 / TICK)
        if amount < 10000 and updated == 10000:
            self._hit_effect(target, None, stun_ms=1300)

    def sync_mei_slow(self, player) -> None:
        """Supply the lower stack value read by Roadhog's 01C7/152 speed mod."""
        if player.hero.guid != 0x02E0000000000040:
            return
        component = player.body_script.component
        amount = max((amount for (_, victim, _), (amount, deadline) in self.mei_frost.items()
                      if victim == player.body and self.tick < deadline), default=0)
        strength = min(7000, amount) / 10000 if amount else None
        slot = component.entity_var(23186, create=False)
        if strength != (slot.base if slot is not None else None):
            component.set(23186, strength)

    def shield_bash_move(self, player):
        """Drive the authored bash window through the ordinary collision mover."""
        script = player.body_script
        if script is None or script.hero != barriers.BRIGITTE_HERO:
            return None
        instance = self._barrier_instance(player)
        if instance is None:
            return None
        window = instance.states.get(instance.graph.node(21).state)
        if window is None or not window.active:
            # The unhosted n20 transition normally clears the hit latch via
            # n143/n142. Retire it once the dash window has ended.
            latch = instance.find_var(expr.INSTANCE, 488, create=False)
            if latch is not None and expr.truthy(latch.value()):
                instance.write((expr.INSTANCE, 488), False)
            return None
        hit = instance.states.get(instance.graph.node(15).state)
        if hit is not None and hit.counter and hit.start >= window.start:
            return None
        # n171 stores the authored displacement per 60 Hz step. The host
        # applies the equivalent speed in its own simulation step.
        speed = expr.to_float(instance.find_var(expr.INSTANCE, 7726).value()) * 60
        direction = aim(player.mover.yaw, 0)
        distance = speed * window.ms / 1000
        # The ordinary mover handles map geometry. Stop the reconstructed
        # forward bash path before any live hostile barrier surface.
        barrier = self._barrier_contact(player, self._eye(player), direction, distance)
        if barrier is not None:
            distance = max(0.0, barrier[0] - STEP)
        movement = (_along(player.mover.position, direction, distance), speed)
        return movement, window if barrier is not None else None

    def shield_bash_targets(self, state):
        """Reconstructed forward body contact for the authored Brigitte search."""
        caster = self._cast_owner(state.instance)
        if caster is None:
            return ()
        origin = self._eye(caster)
        direction = aim(caster.mover.yaw, caster.mover.pitch)
        reach = expr.to_float(state.evaluate(state.node.field("m_radius"), watch=False))
        hits = []
        for target in self._enemies(caster):
            if self.zarya_bubbles.for_target(target.owner) is not None:
                continue
            a, b = target.segment()
            distance = ray_capsule(origin, direction, a, b, target.radius)
            if distance is not None and distance <= reach:
                hits.append((distance, target.entity))
        if not hits:
            return ()
        distance, entity = min(hits)
        if (ray_world(self._collision(), origin, direction, distance) is not None
                or self._barrier_contact(caster, origin, direction, distance) is not None):
            return ()
        return (expr.Entity(entity),)

    def _cast_owner(self, instance, *, released=False):
        return next((p for p in self.match.players if p.body == instance.component.entity
                     and p.has_body and p.spawned and not p.gone
                     and (released or not self.is_dead(p))), None)

    def shield_bash_hit(self, state):
        instance = state.instance
        caster = self._cast_owner(instance)
        if caster is None:
            return
        selected = instance.find_var(expr.INSTANCE, 375).value()
        if not selected:
            return
        target_id = expr.to_entity(selected[0])
        target = next((t for t in self._enemies(caster) if t.entity == target_id), None)
        if target is None:
            return
        modify = state.node.field("m_modifyHealth")
        amount = expr.to_float(state.evaluate(modify["m_amount"], watch=False))
        loadout = state.evaluate(modify["m_loadout"], watch=False)
        self._damage(caster, target, amount, False, loadout.guid, self.tick)

    def shield_bash_stun(self, instance, node):
        targets = instance.evaluate(node.field("m_targets"))
        if targets:
            target_id = expr.to_entity(targets[0])
            target = next((t for t in self._targets() if t.entity == target_id), None)
            if target is not None:
                duration = node.field("m_69F4B52C.m_6525E98B.m_1EB5A024")[0]["m_value"]
                self._hit_effect(target, None, stun_ms=nodes.seconds_to_ms(instance.evaluate(duration)))
        return True

    def earthshatter_targets(self, state):
        """Reconstructed horizontal front with one contact per victim per release."""
        instance = state.instance
        # Once launched, keep propagating from the retained pose even if the
        # original owner dies. A retired or replaced body cannot supply a cast.
        caster = self._cast_owner(instance, released=state.origin is not None)
        if caster is None:
            return state.targets
        if state.origin is None:
            state.origin = tuple(caster.mover.position)
            state.forward = aim(caster.mover.yaw, 0)
            state.launch_tick = self.tick
        origin = state.origin
        front = min(state.reach, (self.tick - state.launch_tick) * TICK * state.speed)
        if front <= 0:
            return state.targets
        cone = math.cos(math.radians(expr.to_float(state.evaluate(
            state.node.field("m_A30B383E"), watch=False,
        ))))
        modify = instance.graph.node(3).field("m_modifyHealth")
        amount = expr.to_float(instance.evaluate(modify["m_amount"]))
        hits = list(state.targets)
        for target in self._enemies(caster):
            entity = expr.Entity(target.entity)
            if entity in hits or abs(target.feet[1] - origin[1]) > 2:
                continue
            delta = (target.feet[0] - origin[0], 0, target.feet[2] - origin[2])
            distance = math.hypot(delta[0], delta[2])
            if (distance <= EPSILON or distance - target.radius > front
                    or _dot(delta, state.forward) < distance * cone):
                continue
            if not self._earthshatter_visible(caster, origin, target):
                continue
            hits.append(entity)
            self._earthshatter_hit(caster, target, instance, modify, amount)
        return tuple(hits)

    def _earthshatter_visible(self, caster, origin, target):
        eye = (origin[0], origin[1] + (self._eye(caster)[1] - caster.mover.position[1]), origin[2])
        a, b = target.segment()
        if not capsule_visible(self._collision(), eye, a, b, target.radius, walls=self.walls):
            return False
        sight = _sub(b, eye)
        length = math.sqrt(_dot(sight, sight))
        return length <= EPSILON or self._barrier_contact(
            caster, eye, tuple(v / length for v in sight), length,
        ) is None

    def earthshatter_close_targets(self, state):
        instance = state.instance
        caster = self._cast_owner(instance)
        wave = instance.states.get(instance.graph.node(1).state)
        if caster is None or wave is None or wave.origin is None:
            return ()
        # n115 places the close search ahead of the caster. Reconstruct its
        # horizontal contact around that authored offset and n114 radius.
        offset = instance.graph.node(115).field("m_value.m_expression.m_D99EF254")[0]
        centre = _along(wave.origin, wave.forward, offset)
        radius = expr.to_float(state.evaluate(state.node.field("m_radius"), watch=False))
        return tuple(expr.Entity(t.entity) for t in self._enemies(caster)
                     if math.dist(t.feet, centre) <= radius + t.radius
                     and self._earthshatter_visible(caster, wave.origin, t))

    def earthshatter_close_hit(self, state):
        instance = state.instance
        caster = self._cast_owner(instance)
        wave = instance.states.get(instance.graph.node(1).state)
        if caster is None or wave is None:
            return
        selected = instance.find_var(expr.INSTANCE, 468).value() or ()
        modify = state.node.field("m_modifyHealth")
        amount = expr.to_float(state.evaluate(modify["m_amount"], watch=False))
        before = wave.targets
        for target in self._enemies(caster):
            entity = expr.Entity(target.entity)
            if entity in selected and entity not in wave.targets:
                wave.targets += (entity,)
                self._earthshatter_hit(caster, target, instance, modify, amount)
        if wave.var is not None and before != wave.targets:
            instance.changed(wave.var, before)

    def _earthshatter_hit(self, caster, target, instance, modify, amount):
        protected = self.zarya_bubbles.for_target(target.owner) is not None
        loadout = instance.evaluate(modify["m_loadout"]).guid
        self._damage(caster, target, amount, False, loadout, self.tick)
        if not protected:
            stun_ms = nodes.seconds_to_ms(instance.find_var(expr.INSTANCE, 215).value())
            self._hit_effect(target, None, stun_ms=stun_ms)

    def _interrupt(self, target: Target) -> None:
        if not isinstance(target.owner, bots.Bot) and self.is_stunned(target.owner):
            self.track_control_input(target.owner, self.tick, self.hard_control_command_cutoffs)
        self.hooks.cancel_owner(target.entity)
        self.walls.cancel_preview(target.owner)
        script = getattr(target.owner, "body_script", None)
        if script is None:
            return
        component = script.component
        if isinstance(target.owner, bots.Bot):
            from ow174.game import assassin

            assassin.bind(component.world, component, None)
            sniper_reposition.cancel(self, target.owner)
            if target.owner.attack_action == "charge":
                heavy_charge.finish(self, target.owner, heavy_charge.instance_of(target.owner))
        reinhardt_charge.interrupt(self, target, script)
        if not isinstance(target.owner, bots.Bot):
            seismic_slam.interrupt(self, target.owner)
            ball_grapple.cancel_grapple(self, target.owner)
            self.ball_contacts.pop(target.entity, None)
        dash = self.punch_dashes.get(target.entity)
        if dash is not None and not dash.finished:
            rocket_punch.finish(target.owner, dash)
        component.edges([(value, False) for value in tuple(component.held)])
        for instance in list(component.instances.values()):
            for state in list(instance.states.values()):
                if isinstance(state, nodes.Ability):
                    state.presses.clear()
                    if instance.graph.index == 0x02CC and state.node.pos == 4:
                        matrix = instance.state(3)
                        if matrix is not None and matrix.active:
                            # Forced stop skips the logical release branch. Reuse its cooldown write.
                            nodes.set_var(instance, instance.graph.node(68), None)
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
        self.sigma_barriers.sync(player)
        shots, script.component.shots[:] = list(script.component.shots), []
        if self.is_dead(player) or self.is_stunned(player):
            self.walls.cancel_preview(player)
            return
        meteor_strike.command(self, player, command)
        if meteor_strike.protected(player):
            return
        defenses.advance_entry(player)
        if self.walls.command(player, command):
            return
        self._sync_barrier(player)
        self.zarya_bubbles.sync(player)
        self.sound_barriers.command(player)
        adaptive_shield.command(self, player)
        self.hooks.command(player, command)
        seismic_slam.command(self, player, command)
        piledriver.command(self, player, command)
        ball_grapple.command(self, player, command)
        ball_grapple.fireball(self, player, command)
        if command is not None:
            for state, number, time, activation in shots:
                try:
                    self._shoot(player, command, state, number, time, activation=activation)
                except Exception:
                    log.exception("[game] %s: %s's shot failed", self.match.label(), player.name)
        try:
            self._melee(player, command, script)
        except Exception:
            log.exception("[game] %s: %s's melee failed", self.match.label(), player.name)
        self._soundwave(player, command, script)

    def _soundwave(self, player, command, script):
        """Apply actual 0776 st11 once, using reconstructed target selection."""
        if command is None or player.hero.guid != lucio_soundwave.HERO:
            return
        for instance in script.component.instances.values():
            if instance.graph.index != lucio_soundwave.GRAPH:
                continue
            state, search = instance.states.get(11), instance.states.get(10)
            if state is None or search is None:
                continue
            counters = self.melee_seen.setdefault(script.entity, {})
            key = (instance.id, state.index)
            seen = counters.get(key, 0)
            counters[key] = state.counter
            if state.counter == seen or not state.active:
                continue
            hit = hit_def(state, {"m_modifyHealth": state.node.field("m_modifyHealth")})
            radius = _number(search.evaluate(search.node.field("m_radius"), watch=False), 0.0)
            angle = _number(search.evaluate(search.node.field("m_A30B383E"), watch=False), 0.0)
            if hit is None or hit.amount <= 0 or radius <= 0 or not 0 < angle <= 360:
                continue
            vector = state.evaluate(state.node.field("m_modifyHealth.m_1376B4DF"), watch=False)
            origin, direction = self._eye(player), aim(*command.aim())
            threshold = math.cos(math.radians(angle / 2))
            view_tick = command.frame - command.view_delay(player.quanta) * 1e6 / player.quanta
            for target in self._enemies(player):
                shown = self.seen(target, view_tick)
                if shown is None:
                    continue
                a, b = shown.segment()
                if segment_distance(origin, a, b) - shown.radius > radius:
                    continue
                centre = (shown.feet[0], shown.feet[1] + shown.height / 2, shown.feet[2])
                offset = _sub(centre, origin)
                length = math.sqrt(_dot(offset, offset))
                if length > EPSILON and _dot(offset, direction) < threshold * length:
                    continue
                if not capsule_visible(self._collision(), origin, a, b, shown.radius, walls=self.walls):
                    continue
                immune = defenses.control_immune(target.owner)
                self._damage(player, target, hit.amount, False, hit.loadout, command.frame)
                if not immune:
                    self.impulses.vector(target, vector)

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
            centre = _along(self._eye(player), aim(*command.aim()), reach)
            view_tick = command.frame - command.view_delay(player.quanta) * 1e6 / player.quanta
            protected = self.zarya_bubbles.area(player, centre, radius, amount)
            self.damage_barriers_in_volumes(player, ((centre, centre, radius),), amount)
            for device in self.baptiste_fields.enemies(player):
                point = device.centre()
                device_radius = baptiste_field.DEVICE_RADIUS
                if (math.dist(centre, point) <= radius + device_radius
                        and capsule_visible(
                            self._collision(), self._eye(player), point, point, device_radius,
                        )):
                    self.baptiste_fields.damage(device, amount)
            for mine in tuple(self.mines.enemies(player)):
                point, hit_radius = mine.centre(), ball_mines.HIT_RADIUS
                if (math.dist(centre, point) <= radius + hit_radius
                        and capsule_visible(self._collision(), self._eye(player), point, point, hit_radius,
                                            walls=self.walls)):
                    self.mines.damage(mine, amount)
            for trap in tuple(self.traps.enemies(player)):
                point = trap.centre()
                if (math.dist(centre, point) <= radius + junkrat_trap.HIT_RADIUS
                        and capsule_visible(self._collision(), self._eye(player), point, point,
                                            junkrat_trap.HIT_RADIUS)):
                    self.traps.damage(trap, amount)
            for target in self._enemies(player):
                if target.entity in protected:
                    continue
                shown = self.seen(target, view_tick)
                if shown is None:
                    continue
                a, b = shown.segment()
                if (segment_distance(centre, a, b) - shown.radius <= radius and amount > 0.0
                        and capsule_visible(self._collision(), self._eye(player), a, b, shown.radius,
                                            walls=self.walls)
                        and not defenses.parries_melee(target, self._eye(player))):
                    self._damage(player, target, amount, False, loadout, command.frame)

    def update(self, now: float, tick: int, frames: bool = True) -> None:
        """Each tick: keep where the bodies are, fly the rockets, respawn, send health that changed. The
        other players' body frames only on the ticks that send (`frames`)."""
        if self.match.collision_world is None:
            return
        retained = max(self.tick + 1, tick - COMBAT_CATCH_UP + 1)
        first = tick
        waves = [
            state for player in self.match.players
            if player.body_script is not None and player.body_script.hero == 0x02E0000000000007
            for instance in player.body_script.component.instances.values()
            if instance.graph.index == 0xBE
            for state in instance.states.values()
            if isinstance(state, nodes.EarthshatterWave) and state.active and state.launch_tick is not None
        ]
        needs_substeps = (
            waves or self.hooks.flights or self.impulses.live or self.charges or self.sniper_repositions
            or self.punch_walls
            or any(cast.wave_tick is not None and not cast.finished for cast in self.slams.values())
            or self.baptiste_fields.flights or self.baptiste_fields.devices or self.mines.entities
            or self.blizzard.flights or self.blizzard.areas or self.traps.live or self.walls.pieces
            or self.gravitons.flights or self.gravitons.fields or self.sound_barriers.leases
            or self.halt.orbs or self.halt.pulls
            or self.orisa_barriers.flights or self.orisa_barriers.actors or self.symmetra_barriers.actors
            or any(actor.mode == 2 for actor in self.sigma_barriers.actors.values())
            or any(
                bot.firing and not bot.dead and isinstance(bot.body_script, BotScript)
                for bot in self.match.bots
            )
        )
        if self.tick and tick > self.tick + 1 and needs_substeps:
            first = retained
        for target in self._targets():
            history = self.history.setdefault(target.entity, deque(maxlen=HISTORY))
            history.append((tick, target.feet, target.height, target.eye, target.critical_allowed))
        # Replay the retained combat frames using the most recent poses. Player input
        # accepted just before the scheduler caught up joins only at the endpoint.
        for at in range(first, tick + 1):
            self.now, self.tick = now - (tick - at) * TICK, at
            self.winston_bubbles.update(at)
            self.baptiste_fields.update(at, TICK)
            self.orisa_barriers.update(at, TICK)
            self.sigma_barriers.update(at, TICK)
            self.symmetra_barriers.update(at)
            pending_impulses = self.impulses.update(at)
            rocket_punch.update(self)
            for player in self.match.players:
                self._sync_barrier(player)
                self.zarya_bubbles.sync(player)
            self.hooks.update(at)
            seismic_slam.update(self)
            for wave in waves:
                if wave.active:
                    wave.advance(at, TICK)
            for rocket in list(self.rockets):
                if at == tick or rocket.last_tick is not None:
                    self._fly(rocket)
            self.walls.update(at)
            self.sound_barriers.update(at, TICK)
            adaptive_shield.update(self, at)
            self.blizzard.update(at)
            self.gravitons.update(at, TICK)
            self.halt.update(at, TICK)
            # Halt's world-driven targets can take over before their deferred force is integrated.
            if pending_impulses:
                self.impulses.update(at, deferred=pending_impulses)
            self.traps.update(at, TICK)
            self.mines.update(at)
            reinhardt_charge.sync(self)
            self._bot_weapons(at)
            if self.pins or self.charges or self.heavy_charges or self.stuns or self.sleep:
                for player in self.match.players:
                    if player.body in self.pins.values() or reinhardt_charge.pinned(self, player.body):
                        self.track_control_input(player, at, self.hard_control_command_cutoffs)
                    elif deadline := self.control_deadline(player.body):
                        self.track_control_input(player, min(at, deadline - 1), self.hard_control_command_cutoffs)
        self.projectiles.update(tick, first_tick=retained)
        for body, (_, deadline) in tuple(self.pile_air_locks.items()):
            if tick >= deadline:
                del self.pile_air_locks[body]
        self.match.deliver_body_messages()
        defenses.update_temporary_shields(self, self.grasp_shields, defenses.SHIELD_TAG)
        defenses.update_temporary_shields(self, self.doomfist_shields, doomfist_passive.SHIELD_TAG)
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
        for statuses in (self.stuns, self.sleep, self.sleep_wake_floor, self.antiheal, self.heal_boost):
            expired = [key for key, deadline in statuses.items() if deadline <= tick or key not in current]
            for entity in expired:
                del statuses[entity]
        for pair, (_, deadline) in list(self.mei_frost.items()):
            if deadline <= tick or pair[0] not in current or pair[1] not in current:
                del self.mei_frost[pair]
        for table in (self.history, self.vitals, self.replacement_ticks,
                      self.barriers, self.healing_status_history, self.breather_deadlines,
                      self.hard_control_command_cutoffs):
            for entity in [key for key in table if key not in current]:
                del table[entity]
        for source, victim in list(self.pins.items()):
            if source not in current or victim not in current:
                del self.pins[source]

    # --- targets -----------------------------------------------------------------------------------

    def _attached_geometry(self, player):
        if player.hero.guid == barriers.BRIGITTE_HERO:
            return self.brigitte_geometry
        return self.barrier_geometry

    @staticmethod
    def _barrier_instance(player):
        script = player.body_script
        if script is None or script.retired:
            return None
        index = {0x02E0000000000007: 0x95, barriers.BRIGITTE_HERO: 0xEE3}.get(script.hero)
        return next((i for i in script.component.instances.values() if i.graph.index == index), None)

    def _sync_barrier(self, player):
        if not player.has_body:
            return None
        geometry = self._attached_geometry(player)
        if geometry is None:
            return None
        instance = self._barrier_instance(player)
        if instance is None:
            return None
        brigitte = instance.graph.index == 0xEE3
        state = self.barriers.get(player.body)
        if state is None:
            maximum = geometry.maximum
            if brigitte:
                capacity = instance.vars.get(587)
                if capacity is not None and capacity.value() is not None:
                    maximum = expr.to_float(capacity.value())
            state = barriers.Barrier(maximum, maximum, self.now, self.now)
            self.barriers[player.body] = state
        pet = instance.states.get(5 if brigitte else 17)
        if self.is_dead(player) or player.gone or not player.spawned or player.client is None:
            if pet is not None and pet.active:
                self._lower_barrier(instance)
            state.active, state.last_update = False, self.now
            return state
        if (self.is_stunned(player) or self.hooks.controlled(player.body)) and pet is not None and pet.active:
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
        self._publish_barrier_health(instance, state.current)
        return state

    @staticmethod
    def _publish_barrier_health(instance, health):
        current = instance.find_var(0, 586)
        brigitte = instance.graph.index == 0xEE3
        if expr.same(current.base if brigitte else current.value(), health):
            return
        instance.write((0, 586), health)
        if brigitte:
            # The authored stack reads the pet pool. Refresh that output after
            # updating the reconstructed pool that substitutes for the pet.
            stack = instance.states.get(instance.graph.node(52).state)
            if stack is not None and stack.active:
                stack.dependency_changed()

    @staticmethod
    def _lower_barrier(instance):
        brigitte = instance.graph.index == 0xEE3
        ability = instance.states.get(30 if brigitte else 59)
        if isinstance(ability, nodes.Ability):
            ability.stop()
        pet = instance.states.get(5 if brigitte else 17)
        if pet is not None and pet.active:
            pet.request(runtime.ABORT)
        instance.component.run_now()

    def _enemy_barriers(self, shooter):
        if self.barrier_geometry is None and self.brigitte_geometry is None:
            return
        source = shooter.entity if isinstance(shooter, bots.Bot) else shooter.body
        for owner in self.match.players:
            if owner is shooter or owner.body == source:
                continue
            if not self.match.game_map.free_for_all and owner.team == shooter.team:
                continue
            state = self._sync_barrier(owner)
            if state is None or not state.active:
                continue
            yield owner, state

    def _barrier_contacts(self, shooter, origin, direction, length, radius=0.0):
        found = []
        for owner, state in self._enemy_barriers(shooter):
            distance = barriers.intersect(self._attached_geometry(owner), owner.mover.position,
                                          owner.mover.yaw, owner.mover.pitch,
                                          origin, direction, length, radius,
                                          owner.mover.values.stand_radius, owner.mover.values.stand_height)
            if distance is not None:
                found.append((distance, owner, state))
        bubble = self.zarya_bubbles.intercept(shooter, origin, direction, length, radius=radius)
        if bubble is not None:
            found.append((bubble[0], bubble[1], None))
        found.extend(self.winston_bubbles.intercept(shooter, origin, direction, length, radius))
        for actor in self.orisa_barriers.enemies(shooter):
            distance = self.orisa_barriers.ray(actor, origin, direction, length, radius)
            if distance is not None:
                found.append((distance, actor, None))
        for actor in self.sigma_barriers.enemies(shooter):
            distance = self.sigma_barriers.ray(actor, origin, direction, length, radius)
            if distance is not None:
                found.append((distance, actor, None))
        for actor in self.symmetra_barriers.enemies(shooter):
            distance = self.symmetra_barriers.ray(actor, origin, direction, length, radius)
            if distance is not None:
                found.append((distance, actor, None))
        for piece in self.walls.pieces.values():
            hit = mei_wall.ray(piece, origin, direction, length, radius=radius)
            if hit is not None:
                found.append((hit[0], piece, None))
        return sorted(found, key=lambda hit: (hit[0], hit[1].body))

    def _barrier_surface(self, contact, origin, direction, length):
        _, owner, _ = contact
        if isinstance(owner, mei_wall.Pillar):
            hit = mei_wall.ray(owner, origin, direction, length)
            return hit[0] if hit is not None else None
        if isinstance(owner, (baptiste_field.Device, junkrat_trap.Trap, ball_mines.Mine)):
            return None  # A projectile can hit a deployable without it shielding nearby bodies.
        if isinstance(owner, orisa_barrier.Shield):
            return self.orisa_barriers.ray(owner, origin, direction, length)
        if isinstance(owner, sigma_barrier.Shield):
            return self.sigma_barriers.ray(owner, origin, direction, length)
        if isinstance(owner, symmetra_barrier.Shield):
            return self.symmetra_barriers.ray(owner, origin, direction, length)
        if isinstance(owner, winston_barrier.Bubble):
            distance = owner.ray(origin, direction)
            return distance if distance is not None and distance <= length else None
        if isinstance(owner, zarya_barriers.Bubble):
            return zarya_barriers.sphere(origin, direction, owner.centre(), zarya_barriers.RADIUS, length)
        return barriers.intersect(
            self._attached_geometry(owner), owner.mover.position, owner.mover.yaw, owner.mover.pitch,
            origin, direction, length, owner_radius=owner.mover.values.stand_radius,
            owner_height=owner.mover.values.stand_height,
        )

    def _barrier_contact(self, shooter, origin, direction, length, radius=0.0):
        found = self._barrier_contacts(shooter, origin, direction, length, radius)
        return found[0] if found else None

    def _enemy_barrier_meshes(self, shooter):
        meshes = [(owner, state, self._attached_geometry(owner), owner.mover.position, owner.mover.yaw,
                   owner.mover.pitch, owner.mover.values.stand_radius, owner.mover.values.stand_height)
                  for owner, state in self._enemy_barriers(shooter)]
        meshes.extend((actor, None, self.orisa_barriers.geometry, actor.position, actor.yaw, 0, 0, 0)
                      for actor in self.orisa_barriers.enemies(shooter))
        meshes.extend(
            (actor, None, self.sigma_barriers.geometry, actor.position, actor.yaw, actor.pitch, 0, 0)
            for actor in self.sigma_barriers.enemies(shooter)
        )
        meshes.extend((actor, None, self.symmetra_barriers.geometry, actor.position, actor.yaw, 0, 0, 0)
                      for actor in self.symmetra_barriers.enemies(shooter))
        return meshes

    def touching_energy_barriers(self, shooter, centre, radius, *, include_zarya=True):
        """Hostile energy surfaces touching a sphere, with a visible contact witness."""
        collision = self._collision()
        meshes = self._enemy_barrier_meshes(shooter)
        for owner, state, geometry, feet, yaw, pitch, owner_radius, owner_height in meshes:
            points = barriers.overlap_points(geometry, feet, yaw, pitch, centre, centre, radius,
                                             owner_radius, owner_height)
            if any(capsule_visible(collision, centre, point, point, 0, walls=self.walls) for point in points):
                yield 0.0, owner, state
        spheres = [
            (actor, actor.centre, winston_barrier.RADIUS)
            for actor in self.winston_bubbles.actors.values()
            if not actor.retired and actor.health > 0 and self.winston_bubbles.enemy(actor, shooter)
        ]
        if include_zarya:
            spheres.extend((bubble, bubble.centre(), zarya_barriers.RADIUS)
                           for bubble in self.zarya_bubbles.enemies(shooter))
        for owner, origin, surface in spheres:
            distance = math.dist(origin, centre)
            if distance <= EPSILON or abs(distance - surface) > radius:
                continue
            point = _along(origin, _sub(centre, origin), surface / distance)
            if capsule_visible(collision, centre, point, point, 0, walls=self.walls):
                yield 0.0, owner, None

    def damage_barriers_in_volumes(self, shooter, volumes, amount, seen=()):
        volumes = tuple(volumes)
        origin = self._eye(shooter)
        found = self.winston_bubbles.melee(shooter, volumes, amount, seen)
        self.walls.melee(shooter, volumes, amount, origin, seen)
        meshes = self._enemy_barrier_meshes(shooter)
        for owner, state, geometry, feet, yaw, pitch, owner_radius, owner_height in meshes:
            if ("barrier", owner.body) in seen:
                continue
            visible = False
            for a, b, radius in volumes:
                for point in barriers.overlap_points(
                    geometry, feet, yaw, pitch, a, b, radius, owner_radius, owner_height,
                ):
                    delta = _sub(point, origin)
                    length = math.sqrt(_dot(delta, delta))
                    wall = (ray_world(self._collision(), origin, tuple(v / length for v in delta), length)
                            if length > EPSILON else None)
                    if wall is None or wall >= length - EPSILON:
                        visible = True
                        break
                if visible:
                    break
            if visible:
                self._damage_barrier((0.0, owner, state), amount, shooter=shooter)
                found.append(owner)
        return found

    def _weapon_cover(self, shooter, origin, direction, length):
        contact = self._barrier_contact(shooter, origin, direction, length)
        limit = length if contact is None else contact[0]
        for device in self.baptiste_fields.enemies(shooter):
            distance = zarya_barriers.sphere(
                origin, direction, device.centre(), baptiste_field.DEVICE_RADIUS, limit,
            )
            if distance is not None and (contact is None or distance < limit):
                contact = (distance, device, None)
                limit = distance
        for trap in self.traps.enemies(shooter):
            distance = zarya_barriers.sphere(origin, direction, trap.centre(), junkrat_trap.HIT_RADIUS, limit)
            if distance is not None and (contact is None or distance < limit):
                contact = (distance, trap, None)
                limit = distance
        for mine in self.mines.enemies(shooter):
            distance = zarya_barriers.sphere(origin, direction, mine.centre(), ball_mines.HIT_RADIUS, limit)
            if distance is not None and (contact is None or distance < limit):
                contact = (distance, mine, None)
                limit = distance
        return contact

    def _damage_barrier(self, contact, amount, *, shooter=None, source_body=None):
        _, owner, state = contact
        if isinstance(owner, mei_wall.Pillar):
            self.walls.damage(owner, shooter, amount, self.tick)
            return
        if isinstance(owner, ball_mines.Mine):
            self.mines.damage(owner, amount)
            return
        if isinstance(owner, junkrat_trap.Trap):
            self.traps.damage(owner, amount)
            return
        if isinstance(owner, baptiste_field.Device):
            self.baptiste_fields.damage(owner, amount)
            return
        if isinstance(owner, orisa_barrier.Shield):
            self.orisa_barriers.damage(owner, amount)
            return
        if isinstance(owner, sigma_barrier.Shield):
            self.sigma_barriers.damage(owner, amount)
            return
        if isinstance(owner, symmetra_barrier.Shield):
            self.symmetra_barriers.damage(owner, amount)
            return
        if isinstance(owner, winston_barrier.Bubble):
            self.winston_bubbles.damage(owner, amount)
            return
        if isinstance(owner, zarya_barriers.Bubble):
            if shooter is None:
                raise ValueError("Zarya bubble damage requires source identity")
            self.zarya_bubbles.damage(owner, amount, shooter)
            return
        if amount <= 0.0 or not state.active:
            return
        state.current = expr.f32(max(0.0, state.current - amount))
        instance = self._barrier_instance(owner)
        if instance is None:
            return
        self._publish_barrier_health(instance, state.current)
        if state.current <= 0.0:
            geometry = self._attached_geometry(owner)
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
                       player, critical_allowed=not ball_grapple.rolling(player))
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
                found = Vitals(target.entity, *pools.body_pools(target.owner.hero.guid))
            self.vitals[target.entity] = found
        return found

    def _enemies(self, shooter) -> list[Target]:
        free_for_all = self.match.game_map.free_for_all
        source = _source_entity(shooter)
        found = []
        for target in self._targets():
            if defenses.protection(target.owner)[1]:
                continue
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
            _, feet, height, eye = before[:4]
        else:
            share = (view_tick - before[0]) / (after[0] - before[0])
            feet = tuple(a + (b - a) * share for a, b in zip(before[1], after[1], strict=True))
            height, eye = (before[2], before[3]) if share < 0.5 else (after[2], after[3])
        critical_allowed = before[4] if len(before) > 4 else target.critical_allowed
        return Target(target.entity, target.team, feet, height, target.radius, eye, target.owner,
                      critical_allowed=critical_allowed)

    # --- shots -------------------------------------------------------------------------------------

    def _bot_weapons(self, tick: int) -> None:
        """Advance the weapon bodies of the map's firing bots and consume their volley reports."""
        from ow174.game import npc_control

        for bot in self.match.bots:
            script = bot.body_script
            manual = npc_control.manual(bot)
            if ((not bot.firing and not manual) or bot.dead
                    or not isinstance(script, BotScript) or script.frame > tick):
                continue
            try:
                sniper_reposition.step(self, bot, tick)
                allowed = (npc_control.update_input(self, bot) if manual
                           else not self.hooks.controlled(bot.entity) and not self.is_stunned(bot))
                snapshot = script.advance_live(tick, bot.position)
                if manual and bot.attack_action == "charge":
                    heavy_charge.advance(self, bot, TICK, allowed=allowed)
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
                sniper_reposition.cancel(self, bot)
                self.clear_pins(bot.entity)
                bot.body_script = None
                log.exception("[game] %s: %s's weapon script failed", self.match.label(), bot.name)

    def _eye(self, player) -> tuple:
        if isinstance(player, bots.Bot):
            x, y, z = player.position
            return (x, y + EYE, z)
        x, y, z = player.mover.position
        crouched = player.mover.crouched
        height = eye_height(player.hero.body, crouched)
        return (x, y + height, z)

    def _shoot(
        self, shooter, command, state, number: int, time: int,
        *, activation: tuple[int, int], snapshot: Snapshot | None = None,
    ) -> None:
        npc = isinstance(shooter, bots.Bot)
        if npc:
            x, y, z = shooter.position
            # Use the server body pose until the animated muzzle transform is available.
            origin, direction = (x, y + EYE, z), aim(round(shooter.yaw), round(shooter.pitch))
        else:
            yaw, pitch = command.aim()
            if self.mines.volley(shooter, state, number, time // state.instance.component.frame_ms,
                                 activation, yaw):
                return
            origin, direction = self._eye(shooter), aim(yaw, pitch)
            if shooter.hero.guid == quad_cannons.HERO and quad_cannons.selected(state):
                direction = quad_cannons.direction(shooter, state, direction, number, time, activation)
        for pellet in spread.directions(state, direction):
            self._shoot_pellet(
                shooter, command, state, number, time, origin, pellet,
                activation=activation, snapshot=snapshot,
            )

    def _shoot_pellet(
        self, shooter, command, state, number, time, origin, direction,
        *, activation: tuple[int, int], snapshot: Snapshot | None = None,
    ) -> None:
        node = state.node
        shot_frame = time // state.instance.component.frame_ms
        npc = isinstance(shooter, bots.Bot)
        if not npc and self.halt.fired(shooter, state, origin, direction):
            return
        if not npc and self.gravitons.fired(
            shooter, state, shot_frame, origin, direction, activation=activation, snapshot=snapshot,
        ):
            return
        if not npc and self.blizzard.fired(
            shooter, state, shot_frame, origin, direction, activation=activation, snapshot=snapshot,
        ):
            return
        if not npc and (state.instance.graph.index, node.pos) == (0x09BA, 7):
            self.traps.launch(shooter, state, origin, direction)
            return
        if not npc and (state.instance.graph.index, node.pos) == (0x1761, 21):
            self.baptiste_fields.launch(shooter, state, origin, direction)
            return

        if not npc and self.orisa_barriers.geometry is not None and (
            state.instance.graph.index, node.pos
        ) == (0xBD7, 1):
            self.orisa_barriers.launch(shooter, state, origin, direction, command.aim()[0])
            return

        def field(name, default=None):
            cfg = node.field(name)
            return default if cfg is None else _number(state.evaluate(cfg, watch=False), default)

        # Reconstructed defaults for the primary pellets and Whole Hog. The
        # unburst secondary projectile keeps the ordinary 1x fallback.
        scrap_critical = state.instance.graph.index == 0x01C6 and node.pos in (26, 33)
        hand = not npc and hand_cannon.selected(state)
        # Hand Cannon keeps its null critical sentinel. It does not become a damage multiplier.
        quad_critical = not npc and shooter.hero.guid == quad_cannons.HERO and quad_cannons.selected(state)
        critical_default = -1.0 if hand else 2.0 if scrap_critical or quad_critical else 1.0
        direct = hit_def(state, node.field("m_6396149F"), critical_default=critical_default)
        entity_def = (node.field("m_projectileEntity") or {}).get("m_entityDef")
        asset = state.evaluate(entity_def, watch=False) if entity_def is not None else None
        definition = asset.guid if isinstance(asset, expr.Asset) else None
        speed = field("m_projectileSpeed")
        if speed:
            lifetime = field("m_projectileLifetime", 5.0) or 5.0
            reach = field("m_FEC435C6")
            if reach:
                lifetime = min(lifetime, reach / speed)
            blast = hit_def(state, node.field("m_D2D11CE9"))
            shot = None
            if definition is not None:
                shot = self.projectiles.fired(
                    shooter, state, shot_frame, definition, lifetime, origin, direction,
                    activation=activation, snapshot=snapshot,
                )
            damaging = (direct is not None and direct.amount > 0.0) or (
                blast is not None and blast.amount > 0.0
            )
            if damaging or shot is not None:
                body = _source_entity(shooter) if hand else None
                headings = hand_cannon.directions(state, direction, body, number, activation) if hand else (
                    direction,
                )
                for index, heading in enumerate(headings):
                    self.rockets.append(
                        Rocket(
                            shooter, shooter.team, origin, heading, speed, lifetime, direct, blast,
                            shot if index == 0 else None, last_tick=self.tick if npc else None,
                            burst=self._fragment_burst(state, heading), definition=definition,
                            source_body=body,
                        )
                    )
            return
        if direct is None or direct.amount <= 0.0:
            return
        reach = field("m_FEC435C6", RANGE) or RANGE
        self._hitscan(shooter, command, origin, direction, reach, direct, shot_frame,
                      npc=npc, definition=definition,
                      impulse_packet=(state.instance.component.entity, activation, time))

    def _hitscan(self, shooter, command, origin, direction, reach, direct, shot_frame,
                 *, npc=False, distance=0.0, reflections=0, definition=None, impulse_packet=None):
        if reflections >= MAX_DEFLECTIONS or reach <= 0:
            return
        # The input supplies this simulation frame's aim and view time. Notices use the volley clock.
        view_tick = self.tick
        if command is not None:
            view_tick = command.frame - command.view_delay(shooter.quanta) * 1e6 / shooter.quanta
        best = None
        for target in self._enemies(shooter):
            shown = self.seen(target, view_tick)
            if shown is None:
                continue
            a, b = shown.segment()
            t = ray_capsule(origin, direction, a, b, shown.radius)
            if t is not None and t <= reach and (best is None or t < best[0]):
                best = (t, target, shown)
        wall = ray_world(self._collision(), origin, direction, reach)
        limit = min(reach, wall if wall is not None else reach, best[0] if best else reach)
        shield = self._weapon_cover(shooter, origin, direction, limit)
        gate = defenses.projectile_gate(
            origin, direction, min(limit, shield[0]) if shield is not None else limit,
            self._enemies(shooter), definition,
        )
        if (gate is not None and (wall is None or gate[0] < wall)
                and (shield is None or gate[0] < shield[0])):
            travel, defender, mode = gate
            if mode == 0:
                owner = defender.owner
                reflected = aim(round(owner.mover.yaw), round(owner.mover.pitch))
                point = _along(origin, direction, travel)
                self._hitscan(owner, None, _along(point, reflected, 0.001), reflected,
                              reach - travel, direct, shot_frame, distance=distance + travel,
                              reflections=reflections + 1, definition=definition,
                              impulse_packet=impulse_packet)
            else:
                defenses.grasp_absorbed(defender.owner, direct.amount * direct.scale(distance + travel))
            return
        if shield is not None and (wall is None or shield[0] < wall):
            self._damage_barrier(shield, direct.amount * direct.scale(distance + shield[0]), shooter=shooter)
            return
        if best is None or (wall is not None and wall < best[0]):
            return
        t, target, shown = best
        hit_y = origin[1] + direction[1] * t
        critical = shown.critical_at(hit_y, direct)
        amount = direct.amount * direct.scale(distance + t) * (direct.critical if critical else 1.0)
        before = self.vitals_of(target).total
        self._damage(
            shooter, target, amount, critical and direct.critical > 1.0, direct.loadout, shot_frame,
            world_tick=self.tick if npc or reflections else None,
        )
        if self.vitals_of(target).total < before:
            self.impulses.hit(target, direction, distance+t, direct.impulse, impulse_packet)
        self._hit_effect(target, direct.effect)

    @staticmethod
    def _fragment_burst(state, direction) -> FragmentBurst | None:
        # Do not generalize unknown projectile callback classes. This exact resource
        # has an embedded25-pellet volley. Lifetime expiry emits it; a prior impact
        # keeps only the slug's direct hit. Native callback timing remains inferred.
        if state.instance.graph.index != 0x01C6 or state.node.pos != 29:
            return None
        callback = state.node.field("m_747ECB51") or {}
        child = callback.get("m_volley")
        if callback.get("$") != "STU_DB5FDC79" or not isinstance(child, dict):
            return None

        def field(name, default):
            cfg = child.get(name)
            return default if cfg is None else _number(state.evaluate(cfg, watch=False), default)

        speed = field("m_projectileSpeed", 0.0)
        if speed <= 0:
            return None
        lifetime = min(field("m_projectileLifetime", 5.0), field("m_FEC435C6", RANGE) / speed)
        entity_cfg = (child.get("m_projectileEntity") or {}).get("m_entityDef")
        asset = state.evaluate(entity_cfg, watch=False) if entity_cfg is not None else None
        return FragmentBurst(
            spread.directions(state, direction, fields=child), speed, lifetime,
            hit_def(state, child.get("m_6396149F"), critical_default=2.0),
            hit_def(state, child.get("m_D2D11CE9")),
            asset.guid if isinstance(asset, expr.Asset) else None,
        )

    def _expired(self, rocket: Rocket, remaining: float = 0.0) -> None:
        self._landed(rocket)
        burst = rocket.burst
        if burst is not None:
            for direction in burst.directions:
                child = Rocket(
                    rocket.shooter, rocket.team, rocket.position, direction, burst.speed,
                    burst.lifetime, burst.direct, burst.blast, last_tick=self.tick,
                    definition=burst.definition, reflected=rocket.reflected,
                )
                self.rockets.append(child)
                if remaining > 0:
                    self._fly(child, seconds=remaining)

    def _fly(self, rocket: Rocket, *, seconds: float | None = None, reflections=0) -> None:
        """Sweep elapsed world time against the current bodies and static collision."""
        # Player input can arrive immediately before the scheduler accounts for a gap.
        if seconds is None:
            elapsed = 1 if rocket.last_tick is None else self.tick - rocket.last_tick
            if elapsed <= 0:
                return
            seconds = elapsed * TICK
        rocket.last_tick = self.tick
        if reflections >= MAX_DEFLECTIONS:
            self._landed(rocket)
            return
        duration = min(seconds, rocket.left)
        if duration <= 0.0:
            self._expired(rocket, seconds)
            return
        # Sweep the remaining flight even when the range or lifetime ends inside this tick.
        step = rocket.speed * duration
        best = None
        grenade = rocket.blast is not None and rocket.blast.effect == ANA_ANTIHEAL_EFFECT
        allies = self._grenade_allies(rocket.shooter) if grenade else []
        enemies = self._enemies(rocket.shooter)
        for target in enemies + [t for t in allies if t.owner is not rocket.shooter]:
            a, b = target.segment()
            t = ray_capsule(rocket.position, rocket.direction, a, b, target.radius)
            if t is not None and t <= step and (best is None or t < best[0]):
                best = (t, target)
        wall = ray_world(self._collision(), rocket.position, rocket.direction, step)
        if wall is not None and (best is None or wall < best[0]):
            best = (wall, None)
        limit = best[0] if best else step
        shield = self._weapon_cover(rocket.shooter, rocket.position, rocket.direction, limit)
        if shield is not None and (best is None or shield[0] < best[0]):
            limit = shield[0]
        else:
            shield = None
        gate = defenses.projectile_gate(
            rocket.position, rocket.direction, limit, self._enemies(rocket.shooter), rocket.definition,
        )
        if (gate is not None and (wall is None or gate[0] < wall)
                and (shield is None or gate[0] < shield[0])):
            distance, defender, mode = gate
            travelled = distance / rocket.speed
            rocket.flown += travelled
            self.rockets.remove(rocket)
            if rocket.shot is not None:
                self.projectiles.absorbed(rocket.shot)
            if mode == 1:
                amount = sum(max(0.0, hit.amount) for hit in (rocket.direct, rocket.blast) if hit is not None)
                defenses.grasp_absorbed(defender.owner, amount)
            if mode == 0 and rocket.left > travelled:
                owner = defender.owner
                direction = aim(round(owner.mover.yaw), round(owner.mover.pitch))
                point = _along(rocket.position, rocket.direction, distance)
                burst = rocket.burst
                if burst is not None:
                    burst = replace(burst, directions=spread.reorient(
                        burst.directions, rocket.direction, direction,
                    ))
                reflected = Rocket(owner, defender.team, _along(point, direction, 0.001), direction,
                                   rocket.speed, rocket.left - travelled, rocket.direct, rocket.blast,
                                   flown=rocket.flown, last_tick=self.tick, burst=burst,
                                   definition=rocket.definition, reflected=True)
                self.rockets.append(reflected)
                remaining = max(0.0, seconds - travelled)
                if remaining > 0:
                    self._fly(reflected, seconds=remaining, reflections=reflections + 1)
            return
        if shield is not None:
            best = (shield[0], None)
        if best is None:
            rocket.position = _along(rocket.position, rocket.direction, step)
            rocket.flown += duration
            rocket.left -= duration
            if rocket.left <= 0.0:
                self._expired(rocket, max(0.0, seconds - duration))
            return
        t, struck = best
        rocket.flown += t / rocket.speed
        self._landed(rocket)
        point = _along(rocket.position, rocket.direction, t)
        blast = rocket.blast
        shield_damage = set()
        if shield is not None:
            amount = (rocket.direct.amount * rocket.direct.scale(rocket.speed * rocket.flown)
                      if rocket.direct is not None else 0.0)
            amount += blast.amount * blast.splash(0.0) if blast is not None else 0.0
            self._damage_barrier(shield, amount, shooter=rocket.shooter)
            shield_damage.add(shield[1].body)
            point = _along(point, rocket.direction, -0.0001)
        if (struck is not None and struck not in allies
                and rocket.direct is not None and rocket.direct.amount > 0.0):
            direct = rocket.direct
            distance = rocket.speed * rocket.flown
            critical = struck.critical_at(point[1], direct)
            amount = direct.amount * direct.scale(distance) * (direct.critical if critical else 1.0)
            before = self.vitals_of(struck).total if direct.effect == MEI_PRIMARY_FREEZE_EFFECT else 0.0
            self._damage(
                rocket.shooter, struck, amount, critical and direct.critical > 1.0, direct.loadout, self.tick,
                world_tick=self.tick, source_body=rocket.source_body,
            )
            if direct.effect == MEI_PRIMARY_FREEZE_EFFECT and self.vitals_of(struck).total < before:
                self._mei_frost_contact(rocket.shooter, struck)
            if direct.stun_ms:
                self._hit_effect(struck, direct.effect, stun_ms=direct.stun_ms)
            else:
                self._hit_effect(struck, direct.effect)
        blast = rocket.blast
        if blast is None or blast.amount <= 0.0:
            return
        for target in self._enemies(rocket.shooter) + allies:
            a, b = target.segment()
            distance = max(0.0, segment_distance(point, a, b) - target.radius)
            scalar = blast.splash(distance)
            # Start just outside an impact wall, on the projectile's approach side.
            visible_from = _along(point, rocket.direction, -0.0001) if wall is not None else point
            if scalar > 0.0 and capsule_visible(self._collision(), visible_from, a, b, target.radius,
                                                  walls=self.walls):
                axis = _sub(b, a)
                squared = _dot(axis, axis)
                share = max(0.0, min(1.0, _dot(_sub(point, a), axis) / squared)) if squared > EPSILON else 0.0
                closest = _along(a, axis, share)
                delta = _sub(closest, point)
                length = math.sqrt(_dot(delta, delta))
                if length > EPSILON:
                    toward = tuple(v / length for v in delta)
                    cover = self._barrier_contact(rocket.shooter, point, toward, length)
                    # Preserve the struck surface for this blast even if the hit broke it.
                    if shield is not None:
                        owner = shield[1]
                        blocked = self._barrier_surface(shield, point, toward, length)
                        if blocked is not None and (cover is None or blocked < cover[0]):
                            cover = (blocked, owner, shield[2])
                    if cover is not None:
                        if cover[1].body not in shield_damage:
                            self._damage_barrier(cover, blast.amount * blast.splash(cover[0]),
                                                 shooter=rocket.shooter)
                            shield_damage.add(cover[1].body)
                        continue
                if target in allies:
                    # 0267 -> 0C71 -> 0C72. The receiver supplies the 60 heal
                    # and four-second flag; the incoming-heal scalar is reconstructed.
                    self._heal_target(rocket.shooter, target, 60 * scalar)
                    self._set_healing_status(target.entity, boost=True)
                else:
                    self._damage(
                        rocket.shooter, target, blast.amount * scalar, False, blast.loadout, self.tick,
                        world_tick=self.tick,
                    )
                    self._hit_effect(target, blast.effect)

    def _grenade_allies(self, shooter):
        return [t for t in self._targets()
                if (t.owner is shooter or (not self.match.game_map.free_for_all and t.team == shooter.team))
                and not self.vitals_of(t).dead and not defenses.protection(t.owner)[0]]

    def _landed(self, rocket: Rocket) -> None:
        """The projectile ended (hit, range, lifetime): the predicted one's release follows."""
        if rocket in self.rockets:
            self.rockets.remove(rocket)
        if rocket.shot is not None:
            self.projectiles.flown(rocket.shot, rocket.flown)

    def heal_self(self, entity: int, amount: float, *, tick: int | None = None) -> None:
        """Apply an owned self heal to a live body's pools, never to another actor."""
        for target in self._targets():
            if target.entity != entity:
                continue
            self._heal_target(target.owner, target, amount, tick=tick)
            return

    def _heal_target(self, healer, target, amount, *, tick=None, ceiling=None):
        vitals = self.vitals_of(target)
        if tick is None or tick >= self.tick:
            denied = self.antiheal.get(target.entity, 0) > self.tick
            boosted = self.heal_boost.get(target.entity, 0) > self.tick
        else:
            statuses = {boost for boost, start, end in self.healing_status_history.get(target.entity, ())
                        if start <= tick < end}
            denied, boosted = False in statuses, True in statuses
        if vitals.dead or denied:
            return
        if boosted:
            amount *= 1.5
        if tick is not None and tick < self.tick:
            amount = min(amount, vitals.historical_heal_capacity(tick))
        if ceiling is not None:
            amount = min(amount, max(0.0, ceiling - vitals.total))
        eligible = vitals.healing_eligible(tick)
        given = vitals.heal(amount, tick=tick)
        if given > 0.0:
            vitals.record_health_change(self.tick if tick is None else tick, given)
            if not isinstance(healer, bots.Bot):
                healer.stats.add(stats.HEALING, given)
                self._gain_ult_charge(healer, min(given, eligible), kind="healing")
            self._sync_body_health(target.owner, vitals)

    def heal_area(self, healer, centre, radius: float, amount: float) -> None:
        """Heal the healer and his teammates (everyone's own team in a free-for-all is his alone) whose body
        is within `radius` of `centre` (unverified: to the body's capsule)."""
        for target in self._targets():
            ally = not self.match.game_map.free_for_all and target.team == healer.team
            if target.owner is not healer and not ally:
                continue
            a, b = target.segment()
            if segment_distance(centre, a, b) - target.radius > radius:
                continue
            self._heal_target(healer, target, amount)

    @staticmethod
    def _gain_ult_charge(owner, points: float, *, kind: str = "damage") -> None:
        if isinstance(owner, bots.Bot):
            return
        gain = getattr(getattr(owner, "body_script", None), "gain_ult_charge", None)
        if callable(gain):
            gain(points, kind=kind)

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

    def note_breather_clock(self, player, tick: int | None) -> None:
        if player.hero.guid != 0x02E0000000000040:
            return
        script = player.body_script
        for instance in script.component.instances.values():
            if instance.graph.index == 0x0240:
                wait = instance.states.get(1)
                if isinstance(wait, nodes.Wait) and wait.active:
                    remaining = max(0, wait.start + wait.ms - script.component.now)
                    frames = (remaining + script.frame_ms - 1) // script.frame_ms
                    self.breather_deadlines[player.body] = (self.tick if tick is None else tick) + frames
                    return
        self.breather_deadlines.pop(player.body, None)

    def incoming_damage_scale(self, owner) -> float:
        """Consume the authored live stack, before the existing reconstructed pool pipeline."""
        script = getattr(owner, "body_script", None)
        component = getattr(script, "component", None)
        if not isinstance(component, runtime.Component):
            return 1.0
        slot = component.entity_var(INCOMING_DAMAGE_SCALE, create=False)
        scalar = _number(slot.value(), 1.0) if slot is not None else 1.0
        if scalar == 0.5 and self.tick > self.breather_deadlines.get(_source_entity(owner), self.tick):
            # World impacts precede body input on the deadline tick. Later
            # impacts must not keep the stale Breather stack during input loss.
            return 1.0
        return max(0.0, scalar)

    def _damage(
        self, shooter, target: Target, amount: float, critical: bool, loadout, frame: int,
        *, world_tick: int | None = None, source_body: int | None = None,
    ) -> None:
        vitals = self.vitals_of(target)
        if vitals.dead or amount <= 0.0 or defenses.protection(target.owner)[0]:
            return
        bubble = self.zarya_bubbles.for_target(target.owner)
        if bubble is not None and (self.match.game_map.free_for_all or target.team != shooter.team):
            self.zarya_bubbles.damage(bubble, amount, shooter)
            return
        floor = self.baptiste_fields.health_floor(target, vitals) if self.baptiste_fields.devices else 0.0
        base_before = vitals.permanent_total if vitals.temporary_shields else None
        dealt = vitals.damage(amount * self.incoming_damage_scale(target.owner), floor=floor)
        if dealt <= 0.0:
            return
        sombra.cancel_hack(target.owner)
        asleep_until = self.sleep.get(target.entity, 0)
        damage_tick = self.tick if world_tick is None else world_tick
        base_dealt = dealt if base_before is None else base_before - vitals.permanent_total
        if base_dealt > 0:
            vitals.record_health_change(damage_tick, -base_dealt)
        if asleep_until > damage_tick:
            # Reconstructed wake transition. Later damage cannot extend recovery.
            recovery = damage_tick + math.ceil(ANA_SLEEP_WAKE_RECOVERY / TICK)
            wake = max(self.sleep_wake_floor.get(target.entity, 0), recovery)
            self.sleep[target.entity] = min(asleep_until, wake)
        hostile = target.owner is not shooter and (
            self.match.game_map.free_for_all or target.team != shooter.team
        )
        current_source = _source_entity(shooter)
        source = current_source if source_body is None else source_body
        live_source = current_source == source
        if hostile and base_dealt > 0:
            vitals.credit_healing_damage(base_dealt, damage_tick)
            hero_guid = getattr(getattr(shooter, "hero", None), "guid", None)
            cannon = loadout == hand_cannon.LOADOUT and hero_guid == hand_cannon.HERO
            if live_source and (not cannon or not self.is_dead(shooter)):
                kind = "hand_cannon" if cannon else "damage"
                if loadout == ball_grapple.FIREBALL_LOADOUT:
                    kind = "ball_fireball"
                elif loadout == quad_cannons.LOADOUT:
                    kind = "quad_cannons"
                elif loadout == MELEE_LOADOUT and hero_guid == ball_grapple.HERO:
                    kind = "ball_melee"
                receiver_script = getattr(target.owner, "body_script", None)
                credit = base_dealt * ultimate.damage_received_multiplier(receiver_script)
                self._gain_ult_charge(shooter, credit, kind=kind)
        killed = vitals.pool_total(HEALTH_POOL) <= EPSILON
        if killed:
            vitals.now[HEALTH_POOL] = 0.0
            vitals.died_at = self.now
            vitals.death_tick = self.match.tick if world_tick is None else world_tick
            vitals.killer = source
        if live_source and not isinstance(shooter, bots.Bot):
            flags = (CRITICAL if critical else 0) | (KILLING if killed else 0)
            self._tell_hit(shooter, target, dealt, flags, frame)
            shooter.stats.add(stats.HERO_DAMAGE, dealt)
        victim = target.owner
        if killed and isinstance(victim, bots.Bot):
            # The NPC death script owns its terminal health change and death event ordering.
            self._bot_died(shooter, victim, critical, loadout, vitals.death_tick, source)
            return
        self._sync_body_health(victim, vitals)
        if (hostile and loadout == MELEE_LOADOUT and target.entity in self.pins
                and not isinstance(shooter, bots.Bot)):
            from ow174.game import assassin

            assassin.rescue_melee(self, victim, shooter)
        if killed:
            self._died(shooter, victim, critical, loadout, source)

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
    def _death_parameters(source, critical: bool, loadout) -> dict:
        return {
            KILLER: expr.Entity(source),
            CRIT: bool(critical),
            LOADOUT: expr.Asset(loadout) if loadout else None,
            ASSISTS: (),
        }

    def _bot_died(self, killer, bot: bots.Bot, critical: bool, loadout, tick: int, source: int) -> None:
        self.halt.drop(bot.entity)
        ball_grapple.retire(self, bot.entity)
        sniper_reposition.cancel(self, bot)
        rocket_punch.retire(self, bot.entity)
        self.clear_statuses(bot.entity)
        self.clear_pins(bot.entity)
        bot.died(tick)
        if not bot.retain_corpse:
            self.respawns.append((self.now + BOT_RESPAWN, bot))
        if not isinstance(killer, bots.Bot) and source == _source_entity(killer):
            killer.stats.add(stats.ELIMINATIONS)
            self._kill_notice(killer, bot)
        log.info("[game] %s: %s killed %s", self.match.label(), killer.name, bot.name)
        script = bot.body_script
        if script is None:
            return
        try:
            params = self._death_parameters(source, critical, loadout)
            if isinstance(script, BotScript):
                tick = max(tick, script.frame)
            event = script.die(tick, params, source)
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

    def _died(self, killer, victim, critical: bool, loadout, source: int) -> None:
        """A player died: the notices, his body script's "died" (the kill feed line), and the respawn."""
        self.halt.drop(victim.body)
        self.clear_pins(victim.body)
        ball_grapple.cancel_grapple(self, victim)
        ball_grapple.retire(self, victim.body)
        rocket_punch.retire(self, victim.body)
        adaptive_shield.drop(self, victim.body)
        self.slams.pop(victim.body, None)
        self.meteors.pop(victim.body, None)
        self.clear_statuses(victim.body)
        label = self.match.label()
        log.info("[game] %s: %s killed %s", label, killer.name, victim.name)
        victim.stats.add(stats.DEATHS)
        bot_killer = isinstance(killer, bots.Bot)
        if killer is not victim and not bot_killer and source == _source_entity(killer):
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
                params = self._death_parameters(source, critical, loadout)
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
