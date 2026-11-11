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
    body_protection,
    bots,
    deadeye,
    deflection,
    dragon,
    dva,
    earthshatter,
    graviton,
    halt,
    lucio_aura,
    lucio_barrier,
    lucio_soundwave,
    mccree_fan,
    mccree_roll,
    mei_blizzard,
    mei_frost,
    mei_wall,
    mercy,
    mercy_effects,
    moira,
    moira_orb,
    observers,
    orisa_barrier,
    orisa_supercharger,
    pools,
    projectiles,
    pulse_bomb,
    reaper,
    reinhardt_barrier,
    roadhog_breather,
    roadhog_hook,
    roadhog_whole_hog,
    sigma_accretion,
    sigma_barrier,
    sigma_grasp,
    sigma_hyperspheres,
    soldier_visor,
    sonic,
    stats,
    tracer,
    tracer_pistol,
    ultimate,
    voicelines,
    wall_ride,
    winston_barrier,
    world,
    zarya_barrier,
    zenyatta_orbs,
)
from ow174.game.bits import BitWriter
from ow174.game.collision import MOVER, World
from ow174.game.commands import Command
from ow174.game.mover import mover_data
from ow174.game.script import expr, graph, nodes, runtime
from ow174.game.script.bot import DIED, BotScript
from ow174.game.script.driver import ULT_CHARGE, ULT_COST, Snapshot
from ow174.game.shot_patterns import orient_forward, ring_directions
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
EYE_STANDING = {
    0x0400000000000001: 1.25, 0x04000000000001C0: 1.5, 0x04000000000002A3: 1.425,
    0x0400000000001B1B: 1.58,
    0x04000000000003BF: 1.6,
    0x04000000000026F0: 1.3,
    0x0400000000000024: 1.8,
    0x0400000000000407: 1.74,
}
EYE_CROUCHED = {
    0x04000000000003CF: 1.085, 0x0400000000000001: 0.875, 0x04000000000001C0: 1.05,
    0x04000000000002A3: 0.9975, 0x0400000000001B1B: 1.2,
    0x04000000000003BF: 1.12,
    0x04000000000026F0: 0.91,
    0x0400000000000024: 1.26,
    0x0400000000000407: 1.74,
}  # verified104319 FirstPerson fields
EYE_CROUCHED_DEFAULT = 1.15
MEI_ICICLE = 0x040000000000108B
FIRE_STRIKE = 0x04000000000001EA
BASTION_BULLETS = (0x0400000000000205, 0x0400000000000207)  # Recon and Sentry
BASTION_TANK_SHELL = 0x04000000000003CE
SOLDIER_HELIX = 0x040000000000072C
ZENYATTA_ORBS = (0x0400000000000236, 0x0400000000000261)
HANZO_STORM_ARROW = 0x040000000000270F
HANZO_BOW_ARROWS = (
    0x0400000000000144, 0x040000000000020B,  # ordinary
    *sonic.DEFINITIONS,
    HANZO_STORM_ARROW,
)
SCRAP_GUN_PROJECTILES = (0x0400000000000295, 0x040000000000029A)
NPC_DEFENSE_ROUNDS = (0x04000000000023F0, 0x0400000000001E80)  # Enforcer and Sniper
NPC_HITSCAN_ROUNDS = (0x0400000000001FE6, 0x040000000000206A)  # Trooper and Heavy Assault
PHARAH_BARRAGE = 0x0400000000000052
PHARAH_CONCUSSION = 0x040000000000010F
HEAD_BELOW = 0.2  # legacy height classifier for bodies without a standing region
TRACER_BODY = 0x0400000000000001
# Motion-1 sphere 282 in Tracer model 02B1, reconstructed in standing bind pose.
TRACER_HEAD = (-5.5390897614415735e-05, 1.5670119524002075, -0.006037640385329723)
TRACER_HEAD_RADIUS = 0.1599999964237213
# Model 0B44, bone 0933, in the deployed 0E35/0EB3 pose (height, forward depth).
SENTRY_CORE = ((1.26459091, -0.56047679), (1.25363660, -0.65987499))
SENTRY_CORE_RADIUS = 0.07999999
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
ANA_GRENADE = 0x0400000000001426
ANA_GRENADE_TICKS = math.ceil(4.0 / TICK)
SLEEP_DART = 0x04000000000015E8
SLEEP_EFFECT = 0x04A000000000026F
SLEEP_TICKS = math.ceil(5.0 / TICK)
SLEEP_WAKE_TICKS = math.ceil(0.5 / TICK)
SLEEP_WAKE_EXCLUDED = 0x0D8000000000191F
FLASHBANG_EFFECT = 0x04A0000000000079  # .053/0079 ->0228 ->224E
FLASHBANG_MODE_FLAG = 13408  # 0228 duration: flag set1.7s, otherwise0.8s


# --- geometry -----------------------------------------------------------------------------------------


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


def eye_height(body: int, crouched: bool = False) -> float:
    return EYE_CROUCHED.get(body, EYE_CROUCHED_DEFAULT) if crouched else EYE_STANDING.get(body, EYE)


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
    if walls is not None and walls.blocked(origin, _along(origin, direction, reach)):
        return False
    hit = ray_world(collision, origin, direction, reach)
    return hit is None or hit + EPSILON >= reach


def segment_pair_distance(a, b, c, d) -> float:
    """Minimum distance between finite segments, including degenerate points."""
    u, v, w = _sub(b, a), _sub(d, c), _sub(a, c)
    aa, bb, cc = _dot(u, u), _dot(u, v), _dot(v, v)
    dd, ee = _dot(u, w), _dot(v, w)
    distances = [segment_distance(point, c, d) for point in (a, b)]
    distances.extend(segment_distance(point, a, b) for point in (c, d))
    determinant = aa * cc - bb * bb
    if determinant > EPSILON * aa * cc:
        s, t = (bb * ee - cc * dd) / determinant, (aa * ee - bb * dd) / determinant
        if 0.0 <= s <= 1.0 and 0.0 <= t <= 1.0:
            distances.append(math.dist(_along(a, u, s), _along(c, v, t)))
    return min(distances)


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
        self.particle_barriers: dict[tuple[int, int, int], float] = {}
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
        self.sleep_until = self.sleep_wake_after = 0
        self.healing_block_until = self.healing_boost_until = 0
        self.nano_until = 0
        self.hack_until = 0
        self.rising_until = self.revive_protection_until = 0

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

    def damage(
        self, amount: float, *, beam: bool = False, damage_over_time: bool = False, limit: float = math.inf,
    ) -> float:
        """Take a hit from shields, then armour, then health (unverified, as OW1 did: what reaches armour
        while it has some is ARMOUR_BLOCK less, at most halved). Returns what the pools lost."""
        left = max(0.0, amount)
        taken = 0.0
        for tag in reversed(self.temporary_shields):
            take = self.decay_temporary_shields(tag, min(left, max(0.0, limit - taken)))
            left -= take
            taken += take
        for kind in (SHIELDS_POOL, ARMOUR_POOL, HEALTH_POOL):
            if left <= 0.0:
                break
            if kind == ARMOUR_POOL and self.now[kind] > 0.0 and not damage_over_time:
                left = left * 0.8 if beam else max(left - ARMOUR_BLOCK, left / 2)
            take = min(left, self.now[kind], max(0.0, limit - taken))
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
    tags: tuple = ()  # authored ModifyHealth tags used by the charge eligibility graph
    effect: int | None = None  # ModifyHealth's authored hit-effect resource
    outgoing_scalar: float = 1.0
    self_splash: float = 0.0
    tank_knockback: tuple | None = None

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

    def tank_impulse(self, delta, distance):
        """Use the Tank blast's separate horizontal and vertical ring coefficients."""
        horizontal, vertical, scale, rings = self.tank_knockback
        previous = None
        for radius, flat, up in rings:
            if distance < radius:
                if previous is not None:
                    inner_radius, inner_flat, inner_up = previous
                    share = (distance - inner_radius) / max(EPSILON, radius - inner_radius)
                    flat = inner_flat + (flat - inner_flat) * share
                    up = inner_up + (up - inner_up) * share
                x, y, z = delta
                length = math.hypot(x, y, z)
                ground = math.hypot(x, z)
                lateral = horizontal * flat * scale / ground if ground > EPSILON else 0.0
                rise = y * vertical * up * scale / length if length > EPSILON else 0.0
                return x * lateral, rise, z * lateral
            previous = radius, flat, up
        return 0.0, 0.0, 0.0


def _number(value, default=None):
    if value is None:
        return default
    try:
        return float(expr.to_float(value))
    except (TypeError, ValueError):
        return default


def damage_tags(state, modify):
    tags = []
    for row in modify.get("m_148F3152") or ():
        value = state.evaluate(row.get("m_EE3055BD"), watch=False) if isinstance(row, dict) else None
        if isinstance(value, expr.Asset):
            tags.append(value.guid)
    return tuple(tags)


def hit_def(state, cfg, *, tank_splash=False) -> HitDef | None:
    """Evaluate a volley's hit definition (STU_00D9C7E1) or explosion (STU_4AA6CA44) with the shooter's
    body script."""
    if not isinstance(cfg, dict):
        return None

    def value(name, default=None):
        field = cfg.get(name)
        return default if field is None else _number(state.evaluate(field, watch=False), default)

    modify = cfg.get("m_modifyHealth") or {}
    knockback = None
    if tank_splash and (rows := modify.get("m_knockbackRings")):
        def strength(field):
            return _number(state.evaluate(field, watch=False), 0.0) if field is not None else 0.0

        knockback = (
            strength(modify.get("m_E33EFEE1")), strength(modify.get("m_10399AE7")),
            value("m_2FE97560", 1.0),
            tuple(tuple(strength(row.get(key)) for key in ("m_radius", "m_579D7CFF", "m_10399AE7"))
                  for row in rows),
        )
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
        # These extracted volley rows omit the critical multiplier. Use the
        # reconstructed per-weapon fallback only when no value was authored.
        critical=value(
            "m_A872C70E", 2.0 if (state.instance.graph.index, state.index) in (
                (0x0016, 8), (0x012E, 14), (0x05E6, 16), (0x0164, 14), (0x0164, 18),
            ) else 1.0,
        ),
        rings=tuple(sorted(rings)),
        tags=damage_tags(state, modify),
        effect=effect.guid if isinstance(effect, expr.Asset) else None,
        self_splash=value("m_8A3E3883", 0.0) if tank_splash else 0.0,
        tank_knockback=knockback,
    )


# --- who can be hit -------------------------------------------------------------------------------------


def _source_entity(actor) -> int:
    """The combat actor's live entity ID."""
    return actor.entity if isinstance(actor, bots.Bot) else actor.body


@dataclass(frozen=True)
class CommandPoseAnchor:
    world: int
    first: int
    trusted: bool
    latest: int | None
    recall_frames: tuple[int, ...]
    deflect_frames: tuple[int, ...]
    matrix_frames: tuple[tuple[int, tuple], ...] = ()
    grasp_frames: tuple[tuple[int, tuple], ...] = ()


@dataclass
class Target:
    entity: int
    team: int
    feet: tuple
    height: float
    radius: float
    eye: float
    owner: object  # a match.Player or a bots.Bot
    head_offset: tuple | None = None
    sentry_yaw: int | None = None

    def segment(self, feet=None):
        """The capsule's segment (from the feet to the top, a radius in from both ends)."""
        x, y, z = feet or self.feet
        return (x, y + self.radius, z), (x, y + max(self.radius, self.height - self.radius), z)

    def sentry_core(self):
        x, y, z = self.feet
        dx, _, dz = aim(self.sentry_yaw, 0)
        return tuple((x + dx * depth, y + height, z + dz * depth) for height, depth in SENTRY_CORE)


@dataclass(frozen=True)
class FragmentBurst:
    rays: tuple
    speed: float
    lifetime: float
    direct: HitDef | None
    blast: HitDef | None
    definition: int | None


@dataclass(frozen=True)
class ImpactPose:
    target: Target
    recall: bool
    deflect_aim: tuple[float, float, float] | None
    matrix: tuple[tuple, tuple[float, float, float]] | None
    grasp: tuple[tuple, tuple] | None


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
    definition: int | None = None  # projectile target-tag lookup
    accretion_source: tuple[int, int] | None = None
    velocity: tuple | None = None
    gravity: float = 0.0
    direct_impulse: tuple | None = None
    blast_impulse: tuple | None = None
    sphere_owner: int | None = None
    graviton_body: int | None = None
    blizzard_body: int | None = None
    orisa_deploy_body: int | None = None
    supercharger_body: int | None = None
    spray: moira.Spray | None = None
    halt_body: int | None = None
    halt_entity: int | None = None
    target_limit: int = 1
    hit_entities: set[int] = field(default_factory=set)
    hit_barriers: set[int] = field(default_factory=set)
    body_radius: float = 0.0
    wall_radius: float = 0.0
    bounces: int = 0
    bounce_normal: float = 0.0
    bounce_tangent: float = 0.0
    pharah_primary: bool = False
    fragment_burst: FragmentBurst | None = None
    sonic_started: int | None = None
    dragon_source: int | None = None
    dragon_started: int | None = None


class Combat:
    """The match's health, hits, deaths and respawns."""

    def __init__(self, match) -> None:
        self.match = match
        self.vitals: dict[int, Vitals] = {}
        self.mercy_effects = mercy_effects.Effects(match)
        self.lucio_barriers = lucio_barrier.Barriers(self)
        self.lucio_auras = lucio_aura.Auras(self)
        self.zenyatta_orbs = zenyatta_orbs.Orbs(self)
        self.roadhog_hooks = roadhog_hook.Hooks(self)
        self.breather_deadlines = {}
        self.history: dict[int, deque] = {}  # entity -> recent position and hit-region poses
        self.replacement_ticks: dict[int, int] = {}  # replacement body -> authoritative world tick
        self.rockets: list[Rocket] = []
        self.sonic_profile: sonic.Profile | None = None
        self.sonars: list[sonic.Beacon] = []
        self.dragon_profile: dragon.Profile | None = None
        self.command_pose_anchors: dict[int, CommandPoseAnchor] = {}
        self.dragons: list[dragon.Flight] = []
        self.gravitons = graviton.Gravitons(self)
        self.blizzard = mei_blizzard.Blizzard(self)
        self.orisa_barriers = orisa_barrier.Barriers(self)
        self.superchargers = orisa_supercharger.Superchargers(self)
        self.halt = halt.Halt(self)
        self.reaper_blossoms: dict[int, reaper.Blossom] = {}
        self.reaper_shadows: dict[int, reaper.Shadow] = {}
        self.moira_hots: dict[int, moira.SprayHeal] = {}
        self.moira_orbs: list[moira_orb.Orb] = []
        self.earthshatters = earthshatter.Earthshatters(self)
        self.walls = mei_wall.Walls(self)
        self.frost_hits: dict[int, tuple] = {}
        self.frost_sources: dict[int, dict] = {}
        self.respawns: list[tuple[float, object]] = []  # (when, a player or a bot)
        self.next_bot = bots.BOT_ENTITY + len(match.bots)
        self.now = 0.0
        self.tick = 0
        self.melee_seen: dict[int, dict[tuple[int, int], int]] = {}  # body -> counters by instance/state
        self.tesla_frames: dict[int, int] = {}  # body -> last consumed command frame
        self.flail_hits: dict[int, dict[tuple[int, int], tuple[int, set[int]]]] = {}
        self.zarya_beam_frames: dict[int, int] = {}
        self.symmetra_beam_frames: dict[int, int] = {}
        self.hammer_hits: dict[int, dict[tuple[int, int], tuple[int, set[tuple[str, int]]]]] = {}
        self.stuns: dict[int, int] = {}  # body identity -> exclusive world-tick deadline
        self.pins: dict[int, int] = {}  # manual Assassin source body -> controlled victim body
        self.projectiles = projectiles.Projectiles(self)  # leases, confirmations, the Biotic Field
        self.pulse_bombs = pulse_bomb.PulseBombs(self)
        self.winston_barriers = winston_barrier.Barriers(self)
        self.reinhardt_barriers = reinhardt_barrier.Barriers(self)
        self.sigma_barriers: dict[int, sigma_barrier.Barrier] = {}
        self.grasp_shields: dict[int, sigma_grasp.ShieldDecay] = {}
        self.grasp_grants: dict[int, tuple[int, int]] = {}
        self.grasp_conversions: dict[tuple[int, tuple[int, int]], sigma_grasp.Conversion] = {}
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
        from ow174.game import npc_control

        npc_control.retire_target(self.match, player.body)
        self.mercy_effects.retire(player.body)
        self.lucio_barriers.retire(player.body)
        self.lucio_auras.retire(player.body)
        self.zenyatta_orbs.retire(player.body)
        self.roadhog_hooks.retire(player.body)
        self.breather_deadlines.pop(player.body, None)
        self.pulse_bombs.retire(player.body)
        self.blizzard.retire(player.body)
        self.orisa_barriers.retire(player.body)
        self.superchargers.retire(player.body)
        self.halt.retire(player.body)
        self.reaper_blossoms.pop(player.body, None)
        self.reaper_shadows.pop(player.body, None)
        self.moira_hots.pop(player.body, None)
        self.earthshatters.retire(player.body)
        self.walls.retire(player.body)
        mei_frost.clear(self, player.body, player)
        for rocket in list(self.rockets):
            if (rocket.sphere_owner == player.body or rocket.graviton_body == player.body
                    or rocket.blizzard_body == player.body or rocket.orisa_deploy_body == player.body
                    or rocket.supercharger_body == player.body
                    or (rocket.accretion_source is not None and rocket.accretion_source[0] == player.body)):
                self._landed(rocket)
        self.winston_barriers.retire(player.body)
        self.reinhardt_barriers.retire(player.body)
        self.grasp_shields.pop(player.body, None)
        self.grasp_grants.pop(player.body, None)
        for key in [key for key in self.grasp_conversions if key[0] == player.body]:
            del self.grasp_conversions[key]
        for entity, barrier in tuple(self.sigma_barriers.items()):
            if barrier.owner_body == player.body:
                self.sigma_barriers.pop(entity)
        self.vitals.pop(player.body, None)
        self.history.pop(player.body, None)
        self.replacement_ticks.pop(player.body, None)
        self.command_pose_anchors.pop(player.body, None)
        self.melee_seen.pop(player.body, None)
        self.tesla_frames.pop(player.body, None)
        self.flail_hits.pop(player.body, None)
        self.zarya_beam_frames.pop(player.body, None)
        self.symmetra_beam_frames.pop(player.body, None)
        self.hammer_hits.pop(player.body, None)
        self.stuns.pop(player.body, None)
        self.clear_pins(player.body)
        # Leased projectiles and all Tank shells retire with their owner's old body.
        self.rockets[:] = [
            rocket for rocket in self.rockets
            if rocket.shooter is not player
            or (rocket.shot is None and rocket.definition != BASTION_TANK_SHELL)
        ]
        self.respawns[:] = [(when, who) for when, who in self.respawns if who is not player]

    def record_replacement(self, body: int) -> None:
        """Keep the creation boundary independent of the rolling pose history."""
        self.replacement_ticks[body] = self.match.tick
        self.stuns.pop(body, None)
        self.clear_pins(body)

    def is_stunned(self, actor, *, tick: int | None = None) -> bool:
        entity = _source_entity(actor)
        tick = self.tick if tick is None else tick
        frost = mei_frost.frost_view(self.frost_hits.get(entity), tick * 16)
        return (entity in self.pins.values() or self.roadhog_hooks.controlled(entity)
                or self.control_deadline(entity) > tick
                or bool(frost and frost[2]))

    def control_deadline(self, entity: int) -> int:
        vitals = self.vitals.get(entity)
        health_lock = max(vitals.sleep_until, vitals.rising_until) if vitals is not None else 0
        return max(self.stuns.get(entity, 0), health_lock)

    def clear_pins(self, entity: int) -> None:
        for source, victim in list(self.pins.items()):
            if entity in (source, victim):
                del self.pins[source]

    def _hit_effect(self, target: Target, effect: int | None) -> None:
        """Resource-backed stun reconstruction; native immunity/presentation remain unverified."""
        vitals = self.vitals_of(target)
        if (effect not in (FLASHBANG_EFFECT, SLEEP_EFFECT) or vitals.dead
                or self.tick < vitals.revive_protection_until
                or tracer.recalling(target.owner) or body_protection.active(target.owner)
                or self.zenyatta_orbs.protects(target.owner)):
            return
        if effect == SLEEP_EFFECT:
            if body_protection.fortify_blocks_control(target.owner):
                return
            vitals.sleep_until = self.tick + SLEEP_TICKS
            vitals.sleep_wake_after = self.tick + SLEEP_WAKE_TICKS
            self._interrupt(target)
            return
        variables = graph.mode_variables(self.match.game_map.mode_guid) or {}
        duration = 1.7 if expr.truthy(variables.get(FLASHBANG_MODE_FLAG)) else 0.8
        self._apply_stun(target, duration)

    def _apply_stun(self, target: Target, duration: float, *, extend_only: bool = False) -> None:
        if body_protection.fortify_blocks_control(target.owner) or self.zenyatta_orbs.protects(target.owner):
            return
        deadline = self.tick + math.ceil(duration / TICK)
        previous = self.stuns.get(target.entity, 0)
        if extend_only and deadline <= previous:
            return
        self.stuns[target.entity] = max(deadline, previous)
        self._interrupt(target)

    def _interrupt(self, target: Target) -> None:
        from ow174.game import bastion_repair

        self.roadhog_hooks.cancel(target.entity)
        script = getattr(target.owner, "body_script", None)
        if script is None:
            return
        component = script.component
        grasp_interrupt = None
        if not isinstance(target.owner, bots.Bot) and target.owner.hero.guid == sigma_grasp.HERO:
            grasp_interrupt = sigma_grasp.prepare_interrupt(self, target.entity, component)
        if isinstance(target.owner, bots.Bot):
            from ow174.game import assassin

            assassin.bind(component.world, component, None)
        else:
            wall_ride.cancel(target.owner)
            mccree_roll.clear(script)
            self.superchargers.interrupt_cast(script)
            reaper.interrupt_blossom(self, target.owner)
            reaper.interrupt_shadow(self, target.owner, script)
        component.edges([(value, False) for value in tuple(component.held)])
        bastion_repair.interrupt(component)
        for instance in list(component.instances.values()):
            for state in list(instance.states.values()):
                if isinstance(state, nodes.LogicalButton):
                    # A released early press can still be replayed when a guarded
                    # weapon re-enters its button state during the stun.
                    state.pressed_at = None
                elif isinstance(state, nodes.Ability):
                    state.presses.clear()
                    if not lucio_aura.preserve_amp(target.owner, state):
                        state.stop()  # use authored exit/cooldown paths for running channels
        component.run_now()
        if grasp_interrupt is not None:
            ability, started_at = grasp_interrupt
            ability.retime_cooldown(started_at)
        component.shots.clear()
        if not isinstance(target.owner, bots.Bot):
            self._note_breather(target.owner)

    def _note_breather(self, player):
        if player.hero.guid != roadhog_breather.HERO:
            return
        end = roadhog_breather.deadline(player, self.tick, round(TICK * 1000))
        if end is None:
            self.breather_deadlines.pop(player.body, None)
        else:
            self.breather_deadlines[player.body] = end

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
        shots, script.component.shots[:] = list(script.component.shots), []
        self._note_breather(player)
        # 00FA's deployment wait can finish after its casting ability is interrupted.
        self.winston_barriers.command(player, command, dead=self.is_dead(player))
        self.reinhardt_barriers.sync(player)
        self.zenyatta_orbs.command(player, command)
        if self.is_dead(player) or self.is_stunned(player):
            self.walls.cancel_preview(player)
            return
        self.roadhog_hooks.command(player, command)
        if self.walls.command(player, command):
            return
        if command is not None:
            if player.hero.guid == reaper.HERO:
                reaper.command_shadow(self, player, script, command)
            for state, number, time, activation in shots:
                try:
                    self._shoot(player, command, state, number, time, activation=activation)
                except Exception:
                    log.exception("[game] %s: %s's shot failed", self.match.label(), player.name)
        try:
            self._melee(player, command, script)
        except Exception:
            log.exception("[game] %s: %s's melee failed", self.match.label(), player.name)
        if command is not None and player.hero.guid == reaper.HERO:
            reaper.register_blossom(self, player, script)
        if command is not None and player.hero.guid == mercy.HERO:
            self.mercy_effects.observe_valkyrie(script, self.tick)
            for entity, amount in self.mercy_effects.advance(script, command.frame):
                target = mercy.body_target(self, entity)
                if target is not None:
                    self._heal(player, target, amount)
            if graph.effect_script(mercy.HEAL_EFFECT) is None:
                mercy.heal(self, player)
        if player.hero.guid == lucio_soundwave.HERO:
            lucio_soundwave.resolve(self, player, command, script)
            self.lucio_barriers.command(player, script, command)
        if command is not None and player.hero.guid == moira.HERO:
            moira.grasp(self, player, command)
            moira.coalescence(self, player, script, command)
        if command is not None and player.hero.guid == winston_barrier.HERO:
            self._tesla(player, command, script)
        if command is not None and player.hero.guid == 0x02E0000000000195:
            self._flail(player, command, script)
        if command is not None and player.hero.guid == 0x02E0000000000068:
            self._zarya_beam(player, command, script)
        if player.hero.guid == 0x02E0000000000016:
            self._symmetra_beam(player, command, script)
        if command is not None and player.hero.guid == 0x02E0000000000007:
            self._hammer(player, command, script)
            self.earthshatters.command(player)

    def _hammer(self, player, command, script):
        """Adapt lane 0007's 0048 sweep using the authored active queries."""
        yaw_units, pitch_units = command.aim()
        forward = aim(yaw_units, pitch_units)
        yaw = yaw_units * math.tau / 65536
        right = (math.cos(yaw), 0.0, -math.sin(yaw))
        up = (forward[1] * right[2], forward[2] * right[0] - forward[0] * right[2],
              -forward[1] * right[0])
        feet = tuple(player.mover.position)
        eye = self._eye(player)

        def position(value):
            return tuple(feet[k] + right[k] * value.x + up[k] * value.y + forward[k] * value.z
                         for k in range(3))

        view_tick = command.frame - command.view_delay(player.quanta) * 1e6 / player.quanta
        old = self.hammer_hits.get(player.body, {})
        live = {}
        for instance in script.component.instances.values():
            if instance.graph.index != 0x0048:
                continue
            for index, queries in ((3, (1, 8)), (4, (2, 9))):
                state = instance.states.get(index)
                if state is None or not state.active:
                    continue
                key = (instance.id, index)
                counter, hit = old.get(key, (state.counter, set()))
                if counter != state.counter:
                    hit = set()
                live[key] = (state.counter, hit)
                definition = hit_def(state, {"m_modifyHealth": state.node.field("m_modifyHealth")})
                if definition is None or not math.isfinite(definition.amount) or definition.amount <= 0:
                    continue
                volumes = []
                for query in queries:
                    tracker = instance.states.get(query)
                    if tracker is None or not tracker.active:
                        continue
                    a, b = (tracker.evaluate(tracker.node.field(name), watch=False)
                            for name in ("m_05BE62DD", "m_B3A77CD0"))
                    radius = _number(tracker.evaluate(tracker.node.field("m_radius"), watch=False), 0.0)
                    if (isinstance(a, expr.Vec3) and isinstance(b, expr.Vec3)
                            and math.isfinite(radius) and radius > 0):
                        volumes.append((position(a), position(b), radius))
                for a, b, radius in volumes:
                    self.winston_barriers.hit_capsule(player, a, b, radius, definition.amount, eye, seen=hit)
                for target in self._enemies(player):
                    target_key = ("body", target.entity)
                    shown = self.seen(target, view_tick)
                    if target_key in hit or shown is None:
                        continue
                    c, d = shown.segment()
                    if (any(segment_pair_distance(a, b, c, d) <= radius + shown.radius
                            for a, b, radius in volumes)
                            and capsule_visible(self._collision(), eye, c, d, shown.radius)):
                        hit.add(target_key)
                        incoming = _sub(shown.feet, feet)
                        distance = math.hypot(*incoming)
                        direction = tuple(axis / distance for axis in incoming) if distance else forward
                        if not self._genji_deflects(target, direction):
                            self._damage(player, target, definition.amount, False, definition.loadout,
                                         command.frame, tags=definition.tags)
        if live:
            self.hammer_hits[player.body] = live
        else:
            self.hammer_hits.pop(player.body, None)

    def _symmetra_beam(self, player, command, script):
        """Feed 157C's two ray outputs and consume one authored damage slice."""
        if command is not None:
            if command.frame <= self.symmetra_beam_frames.get(player.body, -1):
                return
            self.symmetra_beam_frames[player.body] = command.frame
        instance = next((i for i in script.component.instances.values() if i.graph.index == 0x157C), None)
        if instance is None:
            return
        search = instance.states.get(76)
        active = command is not None and search is not None and search.active
        reach_slot = instance.vars.get(1648)
        reach = _number(reach_slot.value(), 0.0) if reach_slot is not None else 0.0
        selected = None
        blocked_by = None
        if active and math.isfinite(reach) and reach > 0:
            origin, direction = self._eye(player), aim(*command.aim())
            view_tick = command.frame - command.view_delay(player.quanta) * 1e6 / player.quanta
            wall = ray_world(self._collision(), origin, direction, reach)
            barrier = self.winston_barriers.first(player, origin, direction, reach if wall is None else wall)
            limit = reach if wall is None else wall
            if barrier is not None:
                limit = barrier[0]
        else:
            active = False
        with script.component.operation():
            for index in (100, 101):
                state = instance.states.get(index)
                target, hit = None, False
                if active and state is not None and state.active:
                    radius = _number(state.evaluate(state.node.field("m_57DAE1B5"), watch=False), 0.0)
                    if math.isfinite(radius) and radius >= 0:
                        closest = limit
                        for candidate in self._enemies(player):
                            shown = self.seen(candidate, view_tick)
                            if shown is None:
                                continue
                            distance = ray_capsule(origin, direction, *shown.segment(), shown.radius + radius)
                            if distance is not None and distance < closest:
                                closest, target = distance, candidate
                        hit = target is not None or wall is not None or barrier is not None
                        if target is None and barrier is not None:
                            blocked_by = barrier[1]
                if selected is None and target is not None:
                    selected = target
                output = instance.graph.state(index).field("m_target")
                for key, value in (
                    ("m_692DFF72", expr.Entity(target.entity if target else expr.INVALID_ENTITY)),
                    ("m_750377AA", hit),
                ):
                    config = output.get(key)
                    if config is not None and config.get("$") == "STUConfigVarDynamic":
                        slot = (config.get("m_60DB8F99", graph.INSTANCE), config["m_identifier"])
                        instance.write(slot, value)
        if not active or (selected is None and blocked_by is None):
            return
        # An enemy barrier is a world contact, not a body selected by 157C.
        # The active search still supplies one slice at the authored rate.
        damage = instance.state(86) if selected is None else instance.states.get(86)
        if damage is None or (selected is not None and not damage.active):
            return
        definition = hit_def(damage, {"m_modifyHealth": damage.node.field("m_modifyHealth")})
        if definition is None or not math.isfinite(definition.amount) or definition.amount <= 0:
            return
        amount = definition.amount * script.component.frame_ms / 1000
        if selected is None:
            blocked_by.damage(amount)
        else:
            self._damage(player, selected, amount, False, definition.loadout, command.frame,
                         tags=definition.tags, beam=True)

    def zarya_beam_ray(self, body, state):
        """Resolve the scoped 0241 TrackRay using live body capsules and map geometry."""
        from ow174.game.script.runtime import RaycastResult

        miss = RaycastResult(False, expr.Entity(0), None, None)
        caster = next((p for p in self.match.players if not p.gone and p.has_body and p.body == body), None)
        if (caster is None or self.match.collision_world is None
                or self.is_dead(caster) or self.is_stunned(caster)):
            return miss
        reach = _number(state.evaluate(state.node.field("m_F5732403.m_6A79BD98"), watch=False), 0.0)
        radius = _number(state.evaluate(state.node.field("m_57DAE1B5"), watch=False), 0.0)
        if not math.isfinite(reach) or reach <= 0 or not math.isfinite(radius) or radius < 0:
            return miss
        origin, direction = self._eye(caster), aim(caster.mover.yaw, caster.mover.pitch)
        wall = ray_world(self._collision(), origin, direction, reach)
        closest, struck = reach if wall is None else wall, None
        for target in self._enemies(caster):
            distance = ray_capsule(origin, direction, *target.segment(), target.radius + radius)
            if distance is not None and distance < closest:
                closest, struck = distance, target
        return RaycastResult(struck is not None or wall is not None,
                             expr.Entity(struck.entity if struck is not None else 0),
                             expr.Vec3(*_along(origin, direction, closest)), None)

    def _zarya_beam(self, caster, command, script):
        """Apply 0241's active health rate once per accepted command pose."""
        if command.frame <= self.zarya_beam_frames.get(caster.body, -1):
            return
        self.zarya_beam_frames[caster.body] = command.frame
        instance = next((i for i in script.component.instances.values() if i.graph.index == 0x0241), None)
        state = instance.states.get(10) if instance is not None else None
        if state is None or not state.active:
            return
        selected = instance.vars.get(536)
        entity = expr.to_entity(selected.value()) if selected is not None else 0
        target = next((t for t in self._enemies(caster) if t.entity == entity), None)
        if target is None:
            return
        definition = hit_def(state, {"m_modifyHealth": state.node.field("m_modifyHealth")})
        if definition is None or not math.isfinite(definition.amount) or definition.amount <= 0:
            return
        self._damage(caster, target, definition.amount * script.component.frame_ms / 1000,
                     False, definition.loadout, command.frame, tags=definition.tags, beam=True)

    def _flail(self, player, command, script) -> None:
        """Read 1248's damage states with lane 0195's reconstructed swing volume."""
        eye, direction = self._eye(player), aim(*command.aim())
        view_tick = command.frame - command.view_delay(player.quanta) * 1e6 / player.quanta
        old = self.flail_hits.get(player.body, {})
        live = {}
        for instance in script.component.instances.values():
            if instance.graph.index != 0x1248:
                continue
            reach_slot = instance.vars.get(1648)
            reach = _number(reach_slot.value(), 0.0) if reach_slot is not None else 0.0
            if reach <= 0:
                continue
            for index in (7, 8):
                state = instance.states.get(index)
                if state is None or not state.active:
                    continue
                key = (instance.id, index)
                counter, hit = old.get(key, (state.counter, set()))
                if counter != state.counter:
                    hit = set()
                live[key] = (state.counter, hit)
                definition = hit_def(state, {"m_modifyHealth": state.node.field("m_modifyHealth")})
                if definition is None or definition.amount <= 0:
                    continue
                for target in self._enemies(player):
                    if target.entity in hit:
                        continue
                    shown = self.seen(target, view_tick)
                    if shown is None:
                        continue
                    a, b = shown.segment()
                    centre = (a[0], min(b[1], max(a[1], eye[1])), a[2])
                    delta = _sub(centre, eye)
                    distance = math.sqrt(_dot(delta, delta))
                    if distance - shown.radius > reach:
                        continue
                    if distance > EPSILON:
                        toward = tuple(value / distance for value in delta)
                        if _dot(direction, toward) < 0.5:  # reconstructed 60-degree half-angle
                            continue
                        wall = ray_world(self._collision(), eye, toward, distance)
                        if wall is not None and wall < distance - shown.radius:
                            continue
                        if self._genji_deflects(target, toward):
                            hit.add(target.entity)
                            continue
                    hit.add(target.entity)
                    self._damage(player, target, definition.amount, False, definition.loadout,
                                 command.frame, tags=definition.tags)
        if live:
            self.flail_hits[player.body] = live
        else:
            self.flail_hits.pop(player.body, None)

    def _tesla(self, player, command, script) -> None:
        """00FE's active search and damage states drive the reconstructed cone."""
        if command.frame <= self.tesla_frames.get(player.body, -1):
            return
        self.tesla_frames[player.body] = command.frame
        for instance in script.component.instances.values():
            if instance.graph.index != 0x00FE:
                continue
            search, damage = instance.states.get(10), instance.states.get(13)
            if search is None or damage is None or not (search.active and damage.active):
                continue
            reach = _number(search.evaluate(search.node.field("m_radius"), watch=False), 0.0)
            angle = _number(search.evaluate(search.node.field("m_A30B383E"), watch=False), 0.0)
            definition = hit_def(damage, {"m_modifyHealth": damage.node.field("m_modifyHealth")})
            if definition is None or definition.amount <= 0 or reach <= 0 or not 0 < angle < 90:
                continue
            origin, direction = self._eye(player), aim(*command.aim())
            view_tick = command.frame - command.view_delay(player.quanta) * 1e6 / player.quanta
            barriers = self.winston_barriers.snapshot()
            damaged_barriers = set()
            amount = definition.amount * player.quanta / 1e6
            slope = math.tan(math.radians(angle))
            for target in self._enemies(player):
                shown = self.seen(target, view_tick)
                if shown is None:
                    continue
                a, b = shown.segment()
                point = (a[0], max(a[1], min(b[1], origin[1])), a[2])
                offset = _sub(point, origin)
                distance = math.sqrt(_dot(offset, offset))
                surface = max(0.0, distance - shown.radius)
                if surface > reach:
                    continue
                forward = _dot(offset, direction)
                lateral = math.sqrt(max(0.0, distance * distance - forward * forward))
                if forward < 0 or lateral > forward * slope + shown.radius:
                    continue
                if distance > EPSILON:
                    ray = tuple(value / distance for value in offset)
                    wall = ray_world(self._collision(), origin, ray, distance)
                    if wall is not None and wall < surface:
                        continue
                    obstruction = self.winston_barriers.first(player, origin, ray, surface, snapshot=barriers)
                    if obstruction is not None:
                        _, barrier = obstruction
                        if barrier.body not in damaged_barriers:
                            barrier.damage(amount)
                            damaged_barriers.add(barrier.body)
                        continue
                self._damage(player, target, amount, False, definition.loadout, command.frame,
                             tags=definition.tags, beam=True)

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
            amount *= self.offense_scale(player)
            loadout = (
                state.evaluate(modify.get("m_loadout"), watch=False) if modify.get("m_loadout") else None
            )
            loadout = loadout.guid if isinstance(loadout, expr.Asset) else None
            eye = self._eye(player)
            centre = _along(eye, aim(*command.aim()), reach)
            view_tick = command.frame - command.view_delay(player.quanta) * 1e6 / player.quanta
            def visible(point, _entity, eye=eye):
                return capsule_visible(self._collision(), eye, point, point, 0, walls=self.walls)

            self.winston_barriers.hit_capsule(player, centre, centre, radius, amount, eye)
            for owner, barrier, _ in self.reinhardt_barriers.within(
                player, centre, radius,
                visible=visible,
            ):
                self.reinhardt_barriers.damage(owner, barrier, amount)
            for barrier, _ in sigma_barrier.within(
                self, player, centre, radius,
                visible=visible,
            ):
                sigma_barrier.damage_barrier(barrier, amount)
            for barrier, _ in self.orisa_barriers.within(player, centre, radius, visible=visible):
                self.orisa_barriers.damage(barrier, amount)
            for device, _ in tuple(self.superchargers.within(player, centre, radius, visible=visible)):
                self.superchargers.damage(device, amount)
            for target in self._enemies(player):
                shown = self.seen(target, view_tick)
                if shown is None:
                    continue
                a, b = shown.segment()
                if (segment_distance(centre, a, b) - shown.radius <= radius and amount > 0.0
                        and capsule_visible(self._collision(), eye, a, b, shown.radius, walls=self.walls)):
                    if self._genji_deflects(target, aim(*command.aim())):
                        continue  # reconstructed melee block; no return damage
                    tags = damage_tags(state, modify)
                    self._damage(
                        player, target, amount, False, loadout, command.frame, tags=tags, source_scaled=True,
                    )
            for piece in tuple(self.walls.pieces.values()):
                contact = mei_wall.closest(piece, centre)
                if (math.dist(centre, contact) <= radius
                        and not self.walls.blocked(eye, contact, ignore=piece)
                        and capsule_visible(self._collision(), eye, contact, contact, 0)):
                    self.walls.damage(piece, player, amount)

    def update(self, now: float, tick: int, frames: bool = True) -> None:
        """Each tick: keep where the bodies are, fly the rockets, respawn, send health that changed. The
        other players' body frames only on the ticks that send (`frames`)."""
        if self.match.collision_world is None:
            return
        retained = max(self.tick + 1, tick - COMBAT_CATCH_UP + 1)
        first = tick
        # Replay observed impacts against their own frame of Sound Barrier decay.
        shielded_flight = self.lucio_barriers.leases and (self.rockets or self.pulse_bombs.bombs)
        admitted_tank = [rocket for rocket in self.rockets
                         if rocket.definition == BASTION_TANK_SHELL and rocket.last_tick is not None]
        admitted_pins = [bot for bot in self.match.bots if bot.entity in self.pins] if self.pins else []
        active_aura = any(receiver.sources for receiver in self.lucio_auras.receivers.values())
        active_zen = self.zenyatta_orbs.links or self.zenyatta_orbs.transcendence
        breather_flight = self.pulse_bombs.bombs and any(
            end >= self.tick for end in self.breather_deadlines.values()
        )
        if self.tick and tick > self.tick + 1 and (
            shielded_flight or admitted_tank or active_aura or active_zen or breather_flight
            or self.grasp_conversions or self.roadhog_hooks.flights or self.winston_barriers.pending
            or (self.pulse_bombs.bombs and self.winston_barriers.actors)
            or any(
                bot.firing and not bot.dead and isinstance(bot.body_script, BotScript)
                for bot in self.match.bots
            )
        ):
            first = retained
        targets = self._targets()
        impact_poses = self._impact_gap_poses(tick, targets)
        if (self.pulse_bombs.bombs and any(pose.grasp for pose in impact_poses.values())
                and any(rocket.last_tick is not None for rocket in self.rockets)):
            # An in-flight attack can interrupt Grasp before or after the bomb reaches it.
            first = retained
        for target in targets:
            history = self.history.setdefault(target.entity, deque(maxlen=HISTORY))
            history.append(
                (tick, target.feet, target.height, target.eye, target.head_offset, target.sentry_yaw)
            )
            vitals = self.vitals.get(target.entity)
            if vitals is not None and vitals.particle_barriers:
                active = {zarya_barrier.particle_barrier_key(pet)
                          for pet in zarya_barrier.particle_barriers(target.owner)}
                for key in tuple(vitals.particle_barriers):
                    if key not in active:
                        del vitals.particle_barriers[key]
        # Replay the retained combat frames using the most recent poses. Player input
        # accepted just before the scheduler caught up joins only at the endpoint.
        if first > self.tick + 1 and (active_zen or breather_flight or admitted_tank or admitted_pins):
            # Advance already-active effects before the retained window.
            bodies = {target.entity: target for target in targets}
            at = self.tick + 1
            while at < first:
                self.now, self.tick = now - (tick - at) * TICK, at
                self.zenyatta_orbs.update(at, bodies)
                if admitted_tank or admitted_pins or self.pulse_bombs.bombs:
                    self.lucio_barriers.update(at)
                if admitted_tank:
                    self.winston_barriers.update()
                    self.reinhardt_barriers.update()
                    sigma_barrier.update_barriers(self)
                    self.orisa_barriers.update(at)
                    for rocket in admitted_tank:
                        self._fly(rocket)
                    admitted_tank = [rocket for rocket in admitted_tank if rocket in self.rockets]
                if admitted_pins:
                    self._bot_weapons(at, admitted_pins)
                    admitted_pins = [bot for bot in admitted_pins if bot.entity in self.pins]
                if self.pulse_bombs.bombs:
                    self.pulse_bombs.update(at, poses=impact_poses)
                if admitted_tank or admitted_pins or self.pulse_bombs.bombs:
                    at += 1
                else:
                    at = self.zenyatta_orbs.next_skipped_tick(bodies, at, first)
                self.walls.update(at - 1)
        for at in range(first, tick + 1):
            self.now, self.tick = now - (tick - at) * TICK, at
            if at == first:
                if self.grasp_conversions:
                    sigma_grasp.finish_conversions(self, at - 1, TICK)
                if self.winston_barriers.pending:
                    self.winston_barriers.deploy_due(at - 1)
            self.zenyatta_orbs.update(at)
            self.winston_barriers.update()
            self.reinhardt_barriers.update()
            projecting = {
                barrier.entity: (barrier.position, barrier.last)
                for barrier in self.sigma_barriers.values()
                if barrier.active and barrier.phase == 2
            } if self.earthshatters.waves else {}
            sigma_barrier.update_barriers(self)
            if self.grasp_shields:
                sigma_grasp.update_shields(self)
            if self.lucio_barriers.leases:
                self.lucio_barriers.update(at)
            if self.roadhog_hooks.flights:
                self.roadhog_hooks.update(at)
            for rocket in list(self.rockets):
                if at == tick or rocket.last_tick is not None:
                    self._fly(rocket)
            self._bot_weapons(at)
            if self.sonars:
                self.sonars[:] = [beacon for beacon in self.sonars if sonic.advance(self, beacon, at)]
            if self.dragons:
                self.dragons[:] = [
                    flight for flight in self.dragons if dragon.advance(self, flight, at, poses=impact_poses)
                ]
            self.pulse_bombs.update(at, poses=impact_poses)
            for channel in tuple(self.reaper_blossoms.values()):
                reaper.tick_blossom(self, channel)
            self.gravitons.update(at)
            self.blizzard.update(at)
            self.orisa_barriers.update(at)
            self.superchargers.update(at)
            self.halt.update(at)
            self.earthshatters.update(projecting)
            self.walls.update(at)
            moira.advance_spray_heals(self)
            moira_orb.update(self)
            self.lucio_auras.update(at)
            mei_frost.update(self)
            if self.grasp_conversions:
                sigma_grasp.finish_conversions(self, at, TICK)
            if self.winston_barriers.pending:
                self.winston_barriers.deploy_due(at)
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
        current = {target.entity for target in self._targets()}
        self.mercy_effects.prune(current)
        for entity in [key for key, deadline in self.stuns.items() if deadline <= tick]:
            del self.stuns[entity]
        tables = (self.history, self.vitals, self.replacement_ticks, self.command_pose_anchors, self.stuns)
        for table in tables:
            for entity in [key for key in table if key not in current]:
                del table[entity]
        for source, victim in list(self.pins.items()):
            if source not in current or victim not in current:
                del self.pins[source]

    # --- targets -----------------------------------------------------------------------------------

    def future_command_tick(self, entity, frame):
        """Map the next command of an observed consecutive batch onto world time."""
        anchor = self.command_pose_anchors.get(entity)
        if (anchor is not None and anchor.trusted and anchor.world == self.match.tick
                and anchor.latest == frame - 1 and frame > anchor.first):
            return anchor.world + frame - anchor.first
        return None

    def observe_command_pose(self, player, frame: int, exact: bool) -> None:
        """Track an observed command/world anchor, retaining its first pose during a batch."""
        if not player.mover.history or player.mover.history[-1].frame != frame:
            self.command_pose_anchors.pop(player.body, None)
            return
        world = self.match.tick
        previous = self.command_pose_anchors.get(player.body)
        latest = frame if exact else None
        protected = (frame,) if tracer.recalling(player) else ()
        stunned = self.is_stunned(player)
        deflecting = (frame,) if not stunned and self._active_genji_deflect(player) else ()
        boxes = deflection.matrix_boxes(player) if not stunned else ()
        matrix = ((frame, boxes),) if boxes else ()
        grasp_boxes = sigma_grasp.grasp_boxes(player) if not stunned else ()
        grasp = ((frame, grasp_boxes),) if grasp_boxes else ()
        if previous is None:
            anchor = CommandPoseAnchor(world, frame, False, latest, protected, deflecting, matrix, grasp)
        else:
            old_world, old_frame = previous.world, previous.first
            trusted, old_latest = previous.trusted, previous.latest
            if world == old_world:
                continued = (trusted and exact and old_latest is not None and frame == old_latest + 1
                             and frame - old_frame < player.mover.history.maxlen)
                frames = previous.recall_frames + protected if continued else ()
                deflect_frames = previous.deflect_frames + deflecting if continued else ()
                matrix_frames = previous.matrix_frames + matrix if continued else ()
                grasp_frames = previous.grasp_frames + grasp if continued else ()
                anchor = CommandPoseAnchor(
                    world, old_frame, continued, latest, frames, deflect_frames, matrix_frames, grasp_frames,
                )
            else:
                aligned = (exact and world == old_world + 1 and old_latest == old_frame
                           and frame == old_frame + 1)
                anchor = CommandPoseAnchor(
                    world, frame, aligned, latest, protected, deflecting, matrix, grasp,
                )
        self.command_pose_anchors[player.body] = anchor

    def _impact_gap_poses(self, tick: int, targets) -> dict:
        """Use a complete, explicitly received movement batch for this world gap."""
        if tick <= self.tick + 1:
            return {}
        active = self.pulse_bombs.bombs or self.dragons
        if not active and not any(r.dragon_source is not None for r in self.rockets):
            return {}
        from ow174.game.mover import CROUCHED

        poses = {}
        for target in targets:
            anchor = self.command_pose_anchors.get(target.entity)
            if anchor is None:
                continue
            world, frame, latest = anchor.world, anchor.first, anchor.latest
            if not anchor.trusted or world != self.tick or latest != frame + tick - world:
                continue
            history = {sample.frame: sample for sample in target.owner.mover.history}
            if latest not in history or history[latest].position != target.feet:
                continue
            frames = range(frame, latest)
            if any(number not in history for number in frames):
                continue
            body = target.owner.hero.body
            data = mover_data(body)
            matrix_frames = dict(anchor.matrix_frames)
            grasp_frames = dict(anchor.grasp_frames)
            for step, number in enumerate(frames, 1):
                sample = history[number]
                crouched = bool(sample.flags & CROUCHED)
                height = data.crouch_height if crouched else data.stand_height
                pose = replace(target, feet=sample.position, height=height, eye=eye_height(body, crouched))
                facing = aim(sample.yaw, sample.pitch) if number in anchor.deflect_frames else None
                boxes = matrix_frames.get(number)
                matrix = (boxes, aim(sample.yaw, sample.pitch)) if boxes else None
                grasp_boxes = grasp_frames.get(number)
                grasp = None
                if grasp_boxes:
                    grasp = sigma_barrier.view_basis(sample.yaw, sample.pitch), grasp_boxes
                poses[target.entity, world + step] = ImpactPose(
                    pose, number in anchor.recall_frames, facing, matrix, grasp,
                )
        return poses

    def _targets(self) -> list[Target]:
        found = []
        for player in self.match.players:
            if not (player.has_body and player.spawned and player.client is not None):
                continue
            data = mover_data(player.hero.body)
            crouched = player.mover.crouched
            height = data.crouch_height if crouched else data.stand_height
            eye = eye_height(player.hero.body, crouched)
            head_offset = None
            if player.hero.body == TRACER_BODY and not crouched:
                hx, hy, hz = TRACER_HEAD
                sin_yaw, _, cos_yaw = aim(player.mover.yaw, 0)
                head_offset = (hx * cos_yaw + hz * sin_yaw, hy, hz * cos_yaw - hx * sin_yaw)
            sentry_yaw = None
            script = player.body_script
            if player.hero.guid == ultimate.BASTION and script is not None:
                mode = script.component.entity_var(1216, create=False)
                if mode is not None and mode.value() == 2:
                    sentry_yaw = round(player.mover.yaw)
            found.append(
                Target(player.body, player.team, tuple(player.mover.position), height, data.stand_radius, eye,
                       player, head_offset, sentry_yaw)
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
                found = Vitals(target.entity, *pools.active_pools(
                    target.owner.hero.guid, target.owner.hero.body))
            self.vitals[target.entity] = found
        return found

    def _enemies(self, shooter, *, team=None, include_allies=False, poses=None, tick=None) -> list[Target]:
        free_for_all = self.match.game_map.free_for_all
        source = _source_entity(shooter)
        team = shooter.team if team is None else team
        found = []
        for target in self._targets():
            if target.owner is shooter or target.entity == source:
                continue
            if not include_allies and not free_for_all and target.team == team:
                continue
            pose, recall = self.sample_impact_pose(target, tick, poses)
            if (self.vitals_of(target).dead or (tracer.recalling(target.owner) if recall is None else recall)
                    or body_protection.active(target.owner) == "phase"):
                continue
            found.append(pose)
        return found

    @staticmethod
    def sample_impact_pose(target, tick, poses):
        sample = poses.get((target.entity, tick)) if poses else None
        return (sample.target, sample.recall) if sample is not None else (target, None)

    def nano_targets(self, body: int, state) -> tuple:
        shooter = next((p for p in self.match.players if p.has_body and p.body == body), None)
        if (shooter is None or self.match.collision_world is None or self.match.game_map.free_for_all
                or self.is_dead(shooter) or self.is_stunned(shooter)):
            return ()
        reach = _number(state.evaluate(state.node.field("m_radius"), watch=False), 0)
        origin, direction = self._eye(shooter), aim(shooter.mover.yaw, shooter.mover.pitch)
        candidates = []
        for target in self._targets():
            if target.owner is shooter or target.team != shooter.team or self.vitals_of(target).dead:
                continue
            centre = (target.feet[0], target.feet[1] + target.height / 2, target.feet[2])
            delta = _sub(centre, origin)
            distance = math.hypot(*delta)
            if not EPSILON < distance <= reach:
                continue
            alignment = _dot(direction, delta) / distance
            if alignment < math.cos(math.radians(10)):
                continue
            if capsule_visible(self._collision(), origin, centre, centre, 0, walls=self.walls):
                candidates.append((-alignment, distance, target.entity))
        return (min(candidates)[2],) if candidates else ()

    def apply_nano(self, shooter, targets, duration: float) -> None:
        if (self.match.game_map.free_for_all or self.is_dead(shooter) or self.is_stunned(shooter)
                or not math.isfinite(duration) or duration <= 0):
            return
        if isinstance(targets, expr.Entity):
            targets = (targets,)
        if not isinstance(targets, tuple):
            return
        for target in self._targets():
            if (expr.Entity(target.entity) not in targets or target.owner is shooter
                    or target.team != shooter.team):
                continue
            vitals = self.vitals_of(target)
            if vitals.dead:
                continue
            self._heal(shooter, target, 250.0)
            vitals.nano_until = max(vitals.nano_until, self.tick + math.ceil(duration / TICK))

    def nano_offense(self, shooter) -> float:
        vitals = self.vitals.get(_source_entity(shooter))
        return 1.5 if vitals is not None and not vitals.dead and self.tick < vitals.nano_until else 1.0

    def offense_scale(self, shooter) -> float:
        other = self.nano_offense(shooter) + self.superchargers.scale(shooter) - 1.0
        return other + mercy.bonus(self, shooter)

    def scale_outgoing_hit(self, shooter, hit):
        scalar = self.offense_scale(shooter)
        if hit is None or hit.outgoing_scalar == scalar:
            return hit
        return replace(hit, amount=hit.amount * scalar / hit.outgoing_scalar, outgoing_scalar=scalar)

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
            sample = before
            feet = sample[1]
        else:
            share = (view_tick - before[0]) / (after[0] - before[0])
            feet = tuple(a + (b - a) * share for a, b in zip(before[1], after[1], strict=True))
            sample = before if share < 0.5 else after
        _, _, height, eye, *region = sample
        head_offset = region[0] if region else None
        sentry_yaw = region[1] if len(region) > 1 else None
        return Target(target.entity, target.team, feet, height, target.radius, eye, target.owner,
                      head_offset, sentry_yaw)

    # --- shots -------------------------------------------------------------------------------------

    def _bot_weapons(self, tick: int, firing_bots: list[bots.Bot] | None = None) -> None:
        """Advance the weapon bodies of the map's firing bots and consume their volley reports."""
        from ow174.game import npc_control

        active_bots = self.match.bots if firing_bots is None else firing_bots
        for bot in active_bots:
            script = bot.body_script
            controlled = npc_control.manual(bot)
            if ((not bot.firing and not controlled) or bot.dead
                    or not isinstance(script, BotScript) or script.frame > tick):
                continue
            try:
                allowed = npc_control.update_input(self, bot) if controlled else not self.is_stunned(bot)
                snapshot = script.advance_live(tick, bot.position)
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
        if state.instance.graph.index == 0x0B9E and state.index == 1:
            target = state.instance.find_var(expr.INSTANCE, 8070, create=False)
            duration = state.instance.find_var(expr.INSTANCE, 215, create=False)
            self.apply_nano(shooter, target.value() if target is not None else (),
                            _number(duration.value(), 0) if duration is not None else 0)
            return

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
        if not npc and state.instance.graph.index == deadeye.TARGET_EFFECT:
            directed = deadeye.shot_aim(self, shooter, state)
            if directed is None:
                return
            origin, direction = directed
        direct = hit_def(state, node.field("m_6396149F"))
        spray = moira.spray_trace(shooter, state, direct)
        if spray is None:
            direct = self.scale_outgoing_hit(shooter, direct)
        entity_def = (node.field("m_projectileEntity") or {}).get("m_entityDef")
        asset = state.evaluate(entity_def, watch=False) if entity_def is not None else None
        definition = asset.guid if isinstance(asset, expr.Asset) else None
        if (not npc and definition == soldier_visor.VISOR_BULLET
                and state.instance.graph.index == soldier_visor.RIFLE):
            direction = soldier_visor.rifle_direction(self, shooter, command, state, origin, direction)
        speed = field("m_projectileSpeed")
        if speed:
            pulse = pulse_bomb.is_pulse(shooter, state)
            concussion = definition == PHARAH_CONCUSSION
            if pulse:
                direction = pulse_bomb.launch_direction(state, direction)
            motion = None if npc else sigma_accretion.accretion_launch(state, direction)
            sphere = None if npc else sigma_hyperspheres.motion(state)
            surge = not npc and graviton.launched(state)
            snowball = (not npc and (state.instance.graph.index, state.index) == (0x064A, 3)
                        and definition == mei_blizzard.DEFINITION)
            orisa_deploy = (not npc and self.orisa_barriers.geometry is not None
                            and state.instance.graph.index == orisa_barrier.GRAPH
                            and orisa_barrier.DEFINITION in projectiles._defs(node.field("m_49CBA3BC")))
            supercharger = (not npc and self.superchargers.geometry is not None
                            and state.instance.graph.index == orisa_supercharger.GRAPH
                            and orisa_supercharger.DEFINITION in projectiles._defs(node.field("m_49CBA3BC")))
            halt_cast = not npc and self.halt.can_launch(state)
            gravity, velocity, source = 0.0, None, None
            body_radius = wall_radius = bounce_normal = bounce_tangent = 0.0
            bounces = 0
            dragonstrike = (
                self.dragon_profile is not None and not npc and definition == dragon.INITIAL
                and shooter.hero.guid == dragon.HANZO and state.instance.graph.index == 0x0101
            )
            target_limit = (self.dragon_profile.initial_hit_limit if dragonstrike
                            else mei_frost.projectile_target_limit(state))
            fire_strike = not npc and (state.instance.graph.index, state.index) == (0x00E2, 0)
            if fire_strike:
                body_radius = max(field("m_6396149F.m_57DAE1B5", 0.0),
                                  field("m_6396149F.m_61CD1F5B", 0.0))
                wall_radius = body_radius
                target_limit = max(1, int(field("m_6396149F.m_B4454CD7", 1)))
            if definition in ZENYATTA_ORBS:
                body_radius = field("m_6396149F.m_57DAE1B5", 0.0)
            if motion is not None:
                gravity, direction = motion
                velocity = tuple(expr.f32(axis * speed) for axis in direction)
                source = (shooter.body, state.instance.id)
            elif sphere is not None:
                gravity, body_radius, wall_radius, bounces, bounce_normal, bounce_tangent = sphere
                velocity = tuple(expr.f32(axis * speed) for axis in direction)
            elif orisa_deploy or supercharger or definition in HANZO_BOW_ARROWS:
                gravity = sum(_number(state.evaluate(row.get("m_599AB8D7"), watch=False), 0.0)
                              for row in node.field("m_projectileMotions") or ()
                              if row.get("$") == "STU_E1A5B579"
                              and not expr.truthy(state.evaluate(row.get("m_D0C4CF49"), watch=False)))
                velocity = tuple(expr.f32(axis * speed) for axis in direction)
                # A Sonic volley keeps its entity definition while Storm is active.
                sonic_storm = (
                    definition in sonic.DEFINITIONS
                    and (state.instance.graph.index, node.pos) == (0x00B9, 114)
                    and (storm := state.instance.find_var(expr.INSTANCE, 18944, create=False)) is not None
                    and storm.value() is True
                )
                if definition == HANZO_STORM_ARROW or sonic_storm:
                    bounces = 1
                    bounce_normal = bounce_tangent = 1.0
            elif (surge or snowball or definition == ANA_GRENADE
                    or (state.instance.graph.index, node.pos) == (0x0241, 41)):
                # These physical launch rows each have one authored acceleration.
                acceleration, = node.field("m_projectileMotions")
                gravity = expr.to_float(state.evaluate(acceleration["m_599AB8D7"], watch=False))
                velocity = tuple(expr.f32(axis * speed) for axis in direction)
                if surge:
                    body_radius = expr.to_float(state.evaluate(
                        node.field("m_6396149F.m_57DAE1B5"), watch=False,
                    ))
            lifetime = field("m_projectileLifetime", 5.0) or 5.0
            reach = field("m_FEC435C6")
            if reach:
                lifetime = min(lifetime, reach / speed)
            blast = hit_def(state, node.field("m_D2D11CE9"), tank_splash=definition == BASTION_TANK_SHELL)
            blast = self.scale_outgoing_hit(shooter, blast)
            # The empty authored blast row must not end Fire Strike on its first body hit.
            if (fire_strike or dragonstrike) and blast is not None and blast.amount == 0:
                blast = None
            shot = None
            if definition is not None:
                shot = self.projectiles.fired(
                    shooter, state, shot_frame, definition, lifetime, origin, direction,
                    activation=activation, snapshot=snapshot,
                )
            if moira_orb.launch(
                self, shooter, state, definition, speed, lifetime, origin, direction, shot,
            ):
                return
            damaging = (direct is not None and direct.amount > 0.0) or (
                blast is not None and blast.amount > 0.0
            )
            if pulse:
                self.pulse_bombs.launch(shooter, state, origin, direction, speed, lifetime,
                                       direct, blast, shot, definition=definition)
                return
            if not npc and (state.instance.graph.index, node.pos) == (0x01C6, 26):
                for index, ray in enumerate(ring_directions(state, direction)):
                    self.rockets.append(Rocket(
                        shooter, shooter.team, origin, ray, speed, lifetime, direct, blast,
                        shot if index == 0 else None, definition=definition,
                    ))
                return
            deployment = orisa_deploy or supercharger or halt_cast
            if damaging or shot is not None or snowball or concussion or deployment:
                self.rockets.append(
                    Rocket(
                        shooter, shooter.team, origin, direction, speed, lifetime, direct, blast, shot,
                        last_tick=self.tick if npc else None, definition=definition,
                        dragon_source=_source_entity(shooter) if dragonstrike else None,
                        accretion_source=source, velocity=velocity, gravity=gravity,
                        direct_impulse=sigma_accretion.projectile_knockback(state, node.field("m_6396149F"))
                        if source is not None or sphere is not None else None,
                        blast_impulse=sigma_accretion.projectile_knockback(state, node.field("m_D2D11CE9"))
                        if source is not None or sphere is not None or concussion else None,
                        sphere_owner=shooter.body if sphere is not None else None,
                        graviton_body=shooter.body if surge else None,
                        blizzard_body=shooter.body if snowball else None,
                        orisa_deploy_body=shooter.body if orisa_deploy else None,
                        supercharger_body=shooter.body if supercharger else None,
                        spray=spray,
                        halt_body=shooter.body if halt_cast else None,
                        target_limit=target_limit,
                        body_radius=body_radius, wall_radius=wall_radius, bounces=bounces,
                        bounce_normal=bounce_normal, bounce_tangent=bounce_tangent,
                        # Pharah's primary volley has no projectile entity for the tag lookup.
                        pharah_primary=(not npc and shooter.hero.guid == 0x02E0000000000008
                                        and (state.instance.graph.index, state.index) == (0x0023, 6)),
                        fragment_burst=self._scrap_fragments(shooter, state),
                    )
                )
                if halt_cast:
                    self.halt.admit(self.rockets[-1], state)
            return
        if direct is None or direct.amount <= 0.0:
            return
        reach = field("m_FEC435C6", RANGE) or RANGE
        # The input supplies this simulation frame's aim and view time. Notices use the volley clock.
        view_tick = self.tick
        if command is not None:
            view_tick = command.frame - command.view_delay(shooter.quanta) * 1e6 / shooter.quanta
        count = max(0, int(field("m_numProjectilesPerShot", 1)))
        knockback = None
        if not npc and roadhog_whole_hog.selected(state):
            rays = ring_directions(state, direction)
            knockback = roadhog_whole_hog.force(state), set()
        elif state.instance.graph.index == dva.FUSION_CANNONS:
            rays = dva.pellet_directions(shooter, state, direction)
        elif (not npc and shooter.hero.guid == reaper.HERO
              and state.instance.graph.index == reaper.SHOTGUN_GRAPH):
            rays = ring_directions(state, direction)
        elif not npc and mccree_fan.active(state):
            rays = (mccree_fan.ray(state, direction, number, time, activation),) * count
        else:
            rays = tracer_pistol.rays(state, number, time, activation, direction, count)
        for ray in rays:
            self._hitscan(shooter, direct, origin, ray, reach, view_tick, shot_frame,
                          npc=npc, definition=definition, knockback=knockback)

    def _genji_deflects(self, target: Target, incoming) -> bool:
        """Reconstruct frontal capsule admission while actual03A3 state10 is active.

        Reviewed Genji-lane policy: native authored boxes/filter semantics are
        still open. Callers decide attack eligibility, not blanket immunity.
        """
        owner = target.owner
        if not self._active_genji_deflect(owner) or self.vitals_of(target).dead or self.is_stunned(owner):
            return False
        return _dot(aim(owner.mover.yaw, owner.mover.pitch), incoming) < -EPSILON

    @staticmethod
    def _active_genji_deflect(owner):
        if getattr(getattr(owner, "hero", None), "guid", None) != 0x02E0000000000029:
            return False
        script = getattr(owner, "body_script", None)
        if script is None or script.component.owner_dead:
            return False
        for instance in script.component.instances.values():
            if instance.graph.index == 0x03A3:
                state = instance.states.get(10)
                if state is not None and state.active:
                    return True
        return False

    def pulse_deflect_aim(self, target, incoming, tick, poses):
        if target is None:
            return None
        sample = poses.get((target.entity, tick)) if poses else None
        if sample is not None:
            facing = sample.deflect_aim
            return facing if facing is not None and _dot(facing, incoming) < -EPSILON else None
        if self._genji_deflects(target, incoming):
            return aim(target.owner.mover.yaw, target.owner.mover.pitch)
        return None

    def _matrix_hit(self, shooter, origin, direction, reach, definition, *, team=None, eligible=False,
                    poses=None, tick=None):
        """Nearest active enemy Matrix at the sampled impact pose when available."""
        if not eligible and not deflection.absorbable(definition):
            return None
        best = None
        for target in self._enemies(shooter, team=team, poses=poses, tick=tick):
            player = target.owner
            if (isinstance(player, bots.Bot) or player.hero.guid != dva.HERO
                    or player.hero.body != dva.MECH):
                continue
            sample = poses.get((target.entity, tick)) if poses else None
            if sample is not None:
                if sample.matrix is None:
                    continue
                boxes, facing = sample.matrix
                x, y, z = target.feet
                hit = deflection.matrix_box_distance(
                    boxes, origin, direction, reach, (x, y + target.eye, z), facing,
                )
            else:
                if self.is_stunned(player):
                    continue
                hit = deflection.matrix_distance(
                    player, origin, direction, reach, self._eye(player),
                    aim(player.mover.yaw, player.mover.pitch),
                )
            if hit is not None and (best is None or hit < best):
                best = hit
        return best

    def _hitscan(self, shooter, direct, origin, direction, reach, view_tick, shot_frame,
                 *, npc=False, definition=None, knockback=None):
        """Resolve one ray, preserving tags and distance through bounded reflections."""
        travelled = 0.0
        for bounce in range(8):  # reconstructed finite bound, no extra reflection damage
            best = None
            for target in self._enemies(shooter):
                shown = self.seen(target, view_tick)
                if shown is None:
                    continue
                a, b = shown.segment()
                t = ray_capsule(origin, direction, a, b, shown.radius)
                if shown.sentry_yaw is not None:
                    core = ray_capsule(origin, direction, *shown.sentry_core(), SENTRY_CORE_RADIUS)
                    critical = core is not None and (t is None or core < t)
                    if critical:
                        t = core
                elif shown.head_offset is not None:
                    centre = _along(shown.feet, shown.head_offset, 1.0)
                    head = ray_capsule(origin, direction, centre, centre, TRACER_HEAD_RADIUS)
                    # The movement capsule overlaps the bind head and is not
                    # a separate surface in front of that same head.
                    critical = head is not None and head <= reach
                    if critical:
                        t = head
                else:
                    critical = t is not None and origin[1] + direction[1] * t >= (
                        shown.feet[1] + shown.eye - HEAD_BELOW)
                if t is not None and t <= reach and (best is None or t < best[0]):
                    best = (t, target, critical)
            limit = min(reach, best[0]) if best is not None else reach
            wall = ray_world(self._collision(), origin, direction, limit)
            pillar = self.walls.cast(origin, direction, limit)
            if pillar is not None and (wall is None or pillar[0] < wall):
                wall = pillar[0]
            else:
                pillar = None
            contact = self.winston_barriers.first(
                shooter, origin, direction, min(limit, wall) if wall is not None else limit)
            stop = min(limit, wall) if wall is not None else limit
            if contact is not None:
                stop = min(stop, contact[0])
            shield = self.reinhardt_barriers.first(shooter, origin, direction, stop)
            if shield is not None:
                stop = shield[0]
            projected = sigma_barrier.barrier_intersection(self, shooter, origin, direction, stop)
            if projected is not None and projected[0] < stop:
                stop = projected[0]
            else:
                projected = None
            deployed = self.orisa_barriers.first(shooter, origin, direction, stop)
            if deployed is not None and deployed[0] < stop:
                stop = deployed[0]
            else:
                deployed = None
            device_hit = self.superchargers.first(shooter, origin, direction, stop)
            if device_hit is not None and device_hit[0] < stop:
                stop = device_hit[0]
            else:
                device_hit = None
            absorbed = self._matrix_hit(shooter, origin, direction, stop, definition)
            grasp_limit = min(stop, absorbed) if absorbed is not None else stop
            if definition == sigma_grasp.TRACER_ROUND or (
                (definition in BASTION_BULLETS or definition in NPC_HITSCAN_ROUNDS or knockback is not None)
                and deflection.absorbable(definition)
            ):
                grasp = sigma_grasp.grasp_intersection(self, shooter, origin, direction, grasp_limit)
                if grasp is not None and grasp[0] < grasp_limit:
                    distance, state = grasp
                    sigma_grasp.absorbed(state, direct.amount * direct.scale(travelled + distance))
                    return
            if absorbed is not None and absorbed < stop:
                return
            if device_hit is not None:
                distance, device = device_hit
                self.superchargers.damage(device, direct.amount * direct.scale(travelled + distance))
                return
            if deployed is not None:
                distance, barrier = deployed
                self.orisa_barriers.damage(barrier, direct.amount * direct.scale(travelled + distance))
                return
            if projected is not None:
                distance, barrier = projected
                sigma_barrier.damage_barrier(barrier, direct.amount * direct.scale(travelled + distance))
                return
            if shield is not None:
                distance, owner, state = shield
                amount = direct.amount * direct.scale(travelled + distance)
                self.reinhardt_barriers.damage(owner, state, amount)
                return
            if contact is not None:
                distance, barrier = contact
                barrier.damage(direct.amount * direct.scale(travelled + distance))
                return  # breaking ray has no spill; the next pellet resolves afresh
            if pillar is not None:
                distance, piece, _normal = pillar
                self.walls.damage(piece, shooter, direct.amount * direct.scale(travelled + distance))
                return
            if best is None or (wall is not None and wall < best[0]):
                return
            t, target, critical = best
            point = _along(origin, direction, t)
            travelled += t
            # Explicit bullet/projectile tag admission is reconstructed. Keeping
            # the complete evaluated tag tuple also preserves charge exclusions.
            if 0x0D800000000003BF in direct.tags and self._genji_deflects(target, direction):
                shooter = target.owner
                direct = self.scale_outgoing_hit(shooter, direct)
                direction = aim(shooter.mover.yaw, shooter.mover.pitch)
                origin = _along(point, direction, 1e-4)
                reach -= t + 1e-4
                travelled += 1e-4
                view_tick = self.tick
                shot_frame = self.tick
                continue
            critical = critical and body_protection.fortify(target.owner) is None
            amount = direct.amount * direct.scale(travelled) * (direct.critical if critical else 1.0)
            dealt = self._damage(
                shooter, target, amount, critical and direct.critical > 1.0, direct.loadout, shot_frame,
                world_tick=self.tick if npc or bounce else None, tags=direct.tags, source_scaled=True,
            )
            if knockback is not None and dealt > 0:
                spec, pushed = knockback
                if target.entity not in pushed:
                    pushed.add(target.entity)
                    roadhog_whole_hog.push(self, target, direction, travelled, spec)
            self._hit_effect(target, direct.effect)
            return

    def _scrap_fragments(self, shooter, state) -> FragmentBurst | None:
        if (state.instance.graph.index, state.node.pos) != (0x01C6, 29):
            return None
        callback = state.node.field("m_747ECB51") or {}
        child = callback.get("m_volley")
        if callback.get("$") != "STU_DB5FDC79" or not isinstance(child, dict):
            return None

        def field(name, default):
            value = child.get(name)
            return default if value is None else _number(state.evaluate(value, watch=False), default)

        speed = field("m_projectileSpeed", 0.0)
        if speed <= 0:
            return None
        lifetime = min(field("m_projectileLifetime", 5.0), field("m_FEC435C6", RANGE) / speed)
        entity = (child.get("m_projectileEntity") or {}).get("m_entityDef")
        asset = state.evaluate(entity, watch=False) if entity is not None else None
        return FragmentBurst(
            ring_directions(state, (0.0, 0.0, 1.0), fields=child), speed, lifetime,
            self.scale_outgoing_hit(shooter, hit_def(state, child.get("m_6396149F"))),
            self.scale_outgoing_hit(shooter, hit_def(state, child.get("m_D2D11CE9"))),
            asset.guid if isinstance(asset, expr.Asset) else None,
        )

    def configure_sonic(self, profile: sonic.Profile) -> None:
        if not isinstance(profile, sonic.Profile) or self.rockets or self.sonars:
            raise ValueError("Sonic profile must be mounted before active projectiles")
        self.sonic_profile = profile

    def revealed_to(self, viewer, target) -> bool:
        return any(sonic.visible(beacon, viewer, _source_entity(target), self.match.game_map.free_for_all)
                   for beacon in self.sonars)

    def configure_dragonstrike(self, profile: dragon.Profile) -> None:
        if not isinstance(profile, dragon.Profile) or self.rockets or self.dragons:
            raise ValueError("Dragonstrike profile must be mounted before active projectiles")
        self.dragon_profile = profile

    def _continue_dragonstrike(self, rocket: Rocket, point) -> None:
        if rocket.dragon_source is None:
            return
        born = rocket.dragon_started + rocket.flown / TICK
        self.dragons.append(dragon.Flight(
            rocket.shooter, rocket.dragon_source, rocket.team, point, rocket.direction,
            self.dragon_profile, born,
        ))

    def _fly(self, rocket: Rocket) -> None:
        """Sweep elapsed world time against the current bodies and static collision."""
        # Player input can arrive immediately before the scheduler accounts for a gap.
        if rocket.halt_entity is not None and not self.halt.flying(rocket):
            return
        elapsed = 1 if rocket.last_tick is None else self.tick - rocket.last_tick
        if elapsed <= 0:
            return
        rocket.last_tick = self.tick
        if rocket.dragon_source is not None:
            if getattr(rocket.shooter, "gone", False):
                self._landed(rocket)
                return
            if rocket.dragon_started is None:
                rocket.dragon_started = self.tick - elapsed
        if (self.sonic_profile is not None and rocket.sonic_started is None
                and rocket.definition in sonic.DEFINITIONS):
            rocket.sonic_started = self.tick - elapsed
        if rocket.velocity is None:
            seconds = elapsed * TICK
            self._fly_step(rocket, seconds if rocket.fragment_burst else min(seconds, rocket.left))
            return
        for _ in range(min(elapsed, math.ceil(rocket.left / TICK) + 1)):
            if rocket not in self.rockets:
                break
            self._fly_step(rocket, TICK if rocket.fragment_burst else min(TICK, rocket.left))

    def _fly_step(self, rocket: Rocket, duration: float) -> None:
        if duration <= 0.0:
            self._expire_projectile(rocket)
            return
        if rocket.spray is not None:
            moira.fly_spray(self, rocket, duration)
            return
        if rocket.velocity is not None:
            # D71ACD-D71ADE updates the vertical velocity before the host sweep.
            vx, vy, vz = rocket.velocity
            vy = expr.f32(vy - expr.f32(expr.f32(rocket.gravity) * expr.f32(duration)))
            rocket.velocity = (vx, vy, vz)
            rocket.speed = math.hypot(*rocket.velocity)
            if not rocket.speed:
                rocket.left -= duration
                if rocket.left <= 0.0:
                    self._expire_projectile(rocket)
                return
            rocket.direction = tuple(axis / rocket.speed for axis in rocket.velocity)
        # Continue the step after a bounce or reflection. Bound overlapping
        # contact surfaces while still spending their remaining flight time.
        for _ in range(7 + rocket.target_limit):
            remaining = self._sweep_projectile(rocket, duration)
            if remaining is None or remaining <= 0.0:
                return
            duration = remaining
        rocket.flown += duration
        rocket.left -= duration
        if rocket.left <= 0.0:
            self._expire_projectile(rocket)

    def _expire_projectile(self, rocket: Rocket, remaining=0.0) -> None:
        if rocket.halt_entity is not None:
            self.halt.landed(rocket)
            return
        self._landed(rocket)
        self._continue_dragonstrike(rocket, rocket.position)
        if rocket.sphere_owner is not None:
            self._explode_projectile(rocket, rocket.position, rocket.position)
        burst = rocket.fragment_burst
        if burst is not None:
            for ray in orient_forward(burst.rays, rocket.direction):
                child = Rocket(
                    rocket.shooter, rocket.team, rocket.position, ray, burst.speed,
                    burst.lifetime, burst.direct, burst.blast, last_tick=self.tick,
                    definition=burst.definition,
                )
                self.rockets.append(child)
                if remaining > 0:
                    self._fly_step(child, min(remaining, child.left))

    def _sweep_projectile(self, rocket: Rocket, duration: float) -> float | None:
        excess = max(0.0, duration - rocket.left)
        duration = min(duration, rocket.left)
        # Sweep the remaining flight even when the range or lifetime ends inside this tick.
        step = rocket.speed * duration
        best = None
        critical = False
        zarya_projectile = rocket.definition in (0x04000000000003DB, 0x04000000000008CE)
        icicle = rocket.definition == MEI_ICICLE
        zen_orb = rocket.definition in ZENYATTA_ORBS
        dart = rocket.definition == SLEEP_DART
        grenade = rocket.definition == ANA_GRENADE
        snowball = rocket.blizzard_body is not None
        orisa_deploy = rocket.orisa_deploy_body is not None
        supercharger = rocket.supercharger_body is not None
        pharah_area = rocket.definition in (PHARAH_BARRAGE, PHARAH_CONCUSSION)
        helix = rocket.definition in (SOLDIER_HELIX, soldier_visor.VISOR_HELIX)
        scrap_gun = rocket.definition in SCRAP_GUN_PROJECTILES
        barrier_projectile = (
            zarya_projectile or icicle or dart or grenade or pharah_area or helix or zen_orb
            or rocket.definition in HANZO_BOW_ARROWS or rocket.definition == dragon.INITIAL
            or scrap_gun or rocket.definition == BASTION_TANK_SHELL or rocket.definition in NPC_DEFENSE_ROUNDS
        )
        fire_strike = rocket.definition == FIRE_STRIKE
        halt_orb = rocket.halt_entity is not None
        grasp_requires_tags = barrier_projectile or fire_strike or halt_orb
        physical_defense = (
            (grasp_requires_tags or snowball) and deflection.absorbable(rocket.definition)
        )
        targets = () if snowball or halt_orb else self._enemies(
            rocket.shooter, team=rocket.team, include_allies=grenade,
        )
        for target in targets:
            if target.entity in rocket.hit_entities:
                continue
            a, b = target.segment()
            t = ray_capsule(rocket.position, rocket.direction, a, b, target.radius + rocket.body_radius)
            head_hit = False
            if (icicle or zen_orb) and target.head_offset is not None:
                centre = _along(target.feet, target.head_offset, 1.0)
                head_radius = TRACER_HEAD_RADIUS + (rocket.body_radius if zen_orb else 0.0)
                head = ray_capsule(rocket.position, rocket.direction, centre, centre, head_radius)
                if head is not None and head <= rocket.speed * rocket.left:
                    t, head_hit = head, True
            elif (icicle or zen_orb) and t is not None:
                hit_y = rocket.position[1] + rocket.direction[1] * t
                head_hit = hit_y >= target.feet[1] + target.eye - HEAD_BELOW
            if t is not None and t <= step and (best is None or t < best[0]):
                best = (t, target)
                critical = head_hit
        collision = self._collision()
        normal = None
        if rocket.wall_radius > 0.0:
            from ow174.game import contacts

            hit = contacts.scene_sweep(
                collision.engine, rocket.position, _along(rocket.position, rocket.direction, step),
                ((0.0, 0.0, 0.0),), rocket.wall_radius, MOVER,
            )
            wall = None
            if hit is not None and _dot(rocket.direction, hit[2]) < -EPSILON:
                wall, normal = hit[0] * step, hit[2]
        elif rocket.bounces:
            hit = collision.cast_ray(rocket.position, rocket.direction, step)
            wall = hit.distance if hit is not None else None
            if hit is not None:
                normal = hit.normal
                if _dot(rocket.direction, normal) > 0:
                    normal = tuple(-axis for axis in normal)
                if _dot(rocket.direction, normal) >= -EPSILON:
                    normal = None
        else:
            wall = ray_world(collision, rocket.position, rocket.direction, step)
        pillar = self.walls.cast(rocket.position, rocket.direction, step,
                                 radius=rocket.wall_radius if fire_strike else 0.0)
        if (pillar is not None and (wall is None or pillar[0] < wall)
                and (best is None or pillar[0] < best[0])):
            wall, _piece, normal = pillar
        else:
            pillar = None
        if wall is not None and (best is None or wall < best[0]):
            best = (wall, None)
        stop = best[0] if best is not None else step
        shield = None
        checks_barriers = rocket.pharah_primary or rocket.sphere_owner is not None or barrier_projectile
        if checks_barriers or rocket.target_limit > 1:
            shield = sigma_hyperspheres.barrier_contact(
                self, rocket.shooter, rocket.position, rocket.direction, stop,
                radius=rocket.wall_radius, excluded=rocket.hit_barriers,
            )
            if shield is not None:
                stop = shield[0]
        reflectable = physical_defense or rocket.pharah_primary
        reflected = self._physical_deflection(rocket, stop) if reflectable else None
        limit = reflected[0] if reflected is not None else stop
        absorbed = self._matrix_hit(
            rocket.shooter, rocket.position, rocket.direction, limit, rocket.definition, team=rocket.team,
            eligible=rocket.pharah_primary,
        )
        graspable = rocket.pharah_primary or rocket.sphere_owner is not None
        if graspable or (grasp_requires_tags and physical_defense):
            limit = min(limit, absorbed) if absorbed is not None else limit
            grasp = sigma_grasp.grasp_intersection(
                self, rocket.shooter, rocket.position, rocket.direction, limit, team=rocket.team,
            )
            if grasp is not None and grasp[0] < limit:
                sigma_grasp.absorbed(grasp[1], self._projectile_damage_budget(rocket))
                absorbed = grasp[0]
        if absorbed is not None and ((best is None and shield is None) or absorbed < stop):
            self.rockets.remove(rocket)
            if halt_orb:
                self.halt.orbs.pop(rocket.halt_entity, None)
            if rocket.shot is not None:
                self.projectiles.absorbed(rocket.shot)
            return
        if reflected is not None:
            distance, state, target = reflected
            travel = distance / rocket.speed
            point = _along(rocket.position, rocket.direction, distance)
            rocket.flown += travel
            state.instance.write((graph.INSTANCE, 6584), self._projectile_damage_budget(rocket))
            self._reflect_projectile(rocket, target, point, travel)
            if rocket.left <= 0:
                self._expire_projectile(rocket, excess)
                return None
            if rocket.fragment_burst is not None:
                return duration + excess - travel
            return min(duration - travel, rocket.left)
        if shield is not None:
            distance, damage, args, barrier_body = shield
            point = _along(rocket.position, rocket.direction, distance)
            travel = distance / rocket.speed
            rocket.flown += travel
            if fire_strike:
                rocket.hit_barriers.add(barrier_body)
                damage(*args, self._projectile_damage_budget(rocket))
                rocket.position = point
                rocket.left -= travel
                if rocket.left <= 0.0:
                    self._expire_projectile(rocket)
                    return None
                return min(duration - travel, rocket.left)
            self._landed(rocket)
            if rocket.graviton_body is not None:
                self.gravitons.landed(rocket, _along(point, rocket.direction, -0.0001))
            self._explode_projectile(rocket, point, _along(point, rocket.direction, -0.0001))
            damage(*args, self._projectile_damage_budget(rocket))
            return
        if best is not None and best[1] is None and normal is not None and rocket.bounces:
            travel = best[0] / rocket.speed
            centre = _along(rocket.position, rocket.direction, best[0])
            vn = _dot(rocket.velocity, normal)
            rocket.velocity = tuple(
                expr.f32((axis - vn * n) * rocket.bounce_tangent - vn * n * rocket.bounce_normal)
                for axis, n in zip(rocket.velocity, normal, strict=True)
            )
            rocket.speed = math.hypot(*rocket.velocity)
            if rocket.speed:
                rocket.direction = tuple(axis / rocket.speed for axis in rocket.velocity)
            rocket.bounces -= 1
            rocket.position = _along(centre, normal, 0.001)
            rocket.flown += travel
            rocket.left -= travel
            if rocket.left <= 0.0:
                self._expire_projectile(rocket)
                return None
            return min(duration - travel, rocket.left) if rocket.speed else None
        if best is None:
            rocket.position = _along(rocket.position, rocket.direction, step)
            rocket.flown += duration
            rocket.left -= duration
            if rocket.left <= 0.0:
                self._expire_projectile(rocket, excess)
            return
        t, struck = best
        travel = t / rocket.speed
        rocket.flown += travel
        point = _along(rocket.position, rocket.direction, t)
        if rocket.halt_entity is not None:
            rocket.position = _along(point, rocket.direction, -0.0001)
            self.halt.landed(rocket)
            return
        if (rocket.sphere_owner is not None and struck is not None
                and self._genji_deflects(struck, rocket.direction)):
            self._reflect_projectile(rocket, struck, point, travel)
            if rocket.left <= 0.0:
                self._expire_projectile(rocket)
                return None
            return min(duration - travel, rocket.left)
        particle = zarya_barrier.active(struck.owner) if struck is not None else None
        pierces = (struck is not None and len(rocket.hit_entities) + 1 < rocket.target_limit
                   and rocket.blast is None and particle is None
                   and body_protection.active(struck.owner) != "cryo")
        if not pierces:
            self._landed(rocket)
        if snowball:
            self.blizzard.land(rocket, _along(point, rocket.direction, -0.0001))
            return
        if orisa_deploy or supercharger:
            if struck is None and pillar is None:
                rocket.position = point
                manager = self.superchargers if supercharger else self.orisa_barriers
                manager.landed(rocket)
            return
        if rocket.graviton_body is not None:
            origin = point if struck is not None else _along(point, rocket.direction, -0.0001)
            self.gravitons.landed(rocket, origin)
        if particle is not None:
            zarya_barrier.damage_particle_barrier(
                particle, self.vitals_of(struck), self._projectile_damage_budget(rocket),
                _source_entity(rocket.shooter),
            )
            self._explode_projectile(rocket, point, point, excluded=struck.entity)
            return
        if not pierces:
            self._continue_dragonstrike(rocket, point)
        if rocket.sonic_started is not None:
            offset = (tuple(axis - foot for axis, foot in zip(point, struck.feet, strict=True))
                      if struck is not None else (0.0, 0.0, 0.0))
            self.sonars.append(sonic.Beacon(
                rocket.shooter, rocket.team, point, struck.entity if struck is not None else None,
                offset, rocket.sonic_started + rocket.flown / TICK, self.sonic_profile,
            ))
        if struck is not None and rocket.direct is not None and rocket.direct.amount > 0.0:
            previous = self.vitals_of(struck).total if rocket.accretion_source is not None else None
            critical = critical and body_protection.fortify(struck.owner) is None
            multiplier = rocket.direct.critical if critical else 1.0
            self._damage(
                rocket.shooter, struck, rocket.direct.amount * multiplier, multiplier > 1.0,
                rocket.direct.loadout, self.tick,
                world_tick=self.tick, tags=rocket.direct.tags, source_scaled=True,
                source_entity=rocket.dragon_source,
            )
            self._hit_effect(struck, rocket.direct.effect)
            if previous is not None:
                sigma_accretion.accretion_hit(self, rocket, struck, previous - self.vitals_of(struck).total)
            if rocket.direct_impulse is not None:
                sigma_accretion.apply_projectile_impulse(self, rocket, struck, rocket.direct_impulse)
        if pierces:
            rocket.hit_entities.add(struck.entity)
            rocket.position = point
            rocket.left -= travel
            if rocket.left <= 0.0:
                self._expire_projectile(rocket)
                return None
            return min(duration - travel, rocket.left)
        # For a wall impact start the LOS test on the projectile's approach side.
        visible_from = _along(point, rocket.direction, -0.0001) if struck is None else point
        self._explode_projectile(rocket, point, visible_from, struck=struck)
        if pillar is not None and rocket.direct is not None:
            self.walls.damage(pillar[1], rocket.shooter, rocket.direct.amount)

    @staticmethod
    def _projectile_damage_budget(rocket: Rocket) -> float:
        return sum(max(0.0, hit.amount) for hit in (rocket.direct, rocket.blast) if hit is not None)

    def _physical_deflection(self, rocket: Rocket, reach: float):
        best = None
        for target in self._enemies(rocket.shooter, team=rocket.team):
            owner = target.owner
            if (getattr(getattr(owner, "hero", None), "guid", None) != 0x02E0000000000029
                    or self.is_stunned(owner)):
                continue
            hit = deflection.genji_distance(
                owner, rocket.position, rocket.direction, reach, self._eye(owner), radius=rocket.body_radius,
            )
            if hit is not None and (best is None or hit[0] < best[0]):
                best = (*hit, target)
        return best

    def _reflect_projectile(self, rocket: Rocket, target: Target, point, travel: float) -> None:
        if rocket.shot is not None:
            self.projectiles.flown(rocket.shot, rocket.flown)
            rocket.shot = None
        rocket.shooter = target.owner
        if rocket.dragon_source is not None:
            rocket.dragon_source = target.entity
        rocket.direct = self.scale_outgoing_hit(rocket.shooter, rocket.direct)
        rocket.blast = self.scale_outgoing_hit(rocket.shooter, rocket.blast)
        if rocket.fragment_burst is not None:
            burst = rocket.fragment_burst
            rocket.fragment_burst = replace(
                burst, direct=self.scale_outgoing_hit(rocket.shooter, burst.direct),
                blast=self.scale_outgoing_hit(rocket.shooter, burst.blast),
            )
        rocket.team = target.team
        if rocket.sphere_owner is not None:
            rocket.sphere_owner = target.entity
        if rocket.graviton_body is not None:
            rocket.graviton_body = target.entity
        if rocket.blizzard_body is not None:
            rocket.blizzard_body = target.entity
        if rocket.halt_body is not None:
            rocket.halt_body = target.entity
        rocket.left -= travel
        rocket.direction = aim(target.owner.mover.yaw, target.owner.mover.pitch)
        rocket.velocity = tuple(expr.f32(rocket.speed * axis) for axis in rocket.direction)
        rocket.position = _along(point, rocket.direction, 0.001)

    def _explode_projectile(self, rocket: Rocket, point, visible_from, *, struck=None, excluded=None) -> None:
        blast = rocket.blast
        concussion = rocket.definition == PHARAH_CONCUSSION
        if blast is None or (blast.amount <= 0.0 and not concussion):
            return
        grenade = rocket.definition == ANA_GRENADE
        shooter_hero = getattr(getattr(rocket.shooter, "hero", None), "guid", None)
        tank_self_splash = (
            rocket.definition == BASTION_TANK_SHELL
            and blast.self_splash > 0
            and shooter_hero == ultimate.BASTION
        )
        deadline = self.tick + ANA_GRENADE_TICKS
        targets = self._enemies(rocket.shooter, team=rocket.team, include_allies=grenade)
        if grenade or concussion or tank_self_splash:
            targets.extend(t for t in self._targets() if t.owner is rocket.shooter)
        for target in targets:
            if target.entity == excluded:
                continue
            a, b = target.segment()
            distance = max(0.0, segment_distance(point, a, b) - target.radius)
            scalar = blast.splash(distance)
            if tank_self_splash and target.owner is rocket.shooter:
                scalar *= blast.self_splash
            if scalar > 0.0 and capsule_visible(
                self._collision(), visible_from, a, b, target.radius, walls=self.walls,
            ):
                if (rocket.pharah_primary or grenade or rocket.sphere_owner is not None
                        or rocket.definition in (
                            0x04000000000003DB, PHARAH_BARRAGE, PHARAH_CONCUSSION,
                            SOLDIER_HELIX, soldier_visor.VISOR_HELIX, BASTION_TANK_SHELL,
                        )):
                    nearest = (a[0], max(a[1], min(b[1], visible_from[1])), a[2])
                    delta = _sub(nearest, visible_from)
                    length = math.hypot(*delta)
                    if length > target.radius and sigma_hyperspheres.barrier_contact(
                        self, rocket.shooter, visible_from, tuple(axis / length for axis in delta),
                        length - target.radius,
                    ) is not None:
                        continue
                vitals = self.vitals_of(target)
                if grenade:
                    ally = target.owner is rocket.shooter or (
                        not self.match.game_map.free_for_all and target.team == rocket.team
                    )
                    if ally:
                        if not vitals.dead:
                            self._heal(rocket.shooter, target, 100.0 * scalar)
                            vitals.healing_boost_until = max(vitals.healing_boost_until, deadline)
                        continue
                protected = zarya_barrier.active(target.owner) is not None
                dealt = self._damage(
                    rocket.shooter, target, blast.amount * scalar, False, blast.loadout, self.tick,
                    world_tick=self.tick, tags=blast.tags, source_scaled=True,
                )
                if grenade and dealt > 0 and not vitals.dead:
                    vitals.healing_block_until = max(vitals.healing_block_until, deadline)
                if protected:
                    continue
                self._hit_effect(target, blast.effect)
                if (blast.tank_knockback and dealt > 0 and not vitals.dead
                        and not body_protection.fortify_blocks_control(target.owner)):
                    nearest = (a[0], max(a[1], min(point[1], b[1])), a[2])
                    impulse = blast.tank_impulse(_sub(nearest, point), distance)
                    if any(impulse):
                        if isinstance(target.owner, bots.Bot):
                            target.owner.push(self._collision(), impulse, self.tick)
                        else:
                            target.owner.mover.add_external_impulse(impulse)
                direct_push = struck is not None and target.entity == struck.entity and rocket.direct_impulse
                if not direct_push and rocket.blast_impulse is not None:
                    sigma_accretion.apply_projectile_impulse(self, rocket, target, rocket.blast_impulse,
                                                            point, distance)

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
            given = self._heal(healer, target, amount)
            ultimate.credit_healing(healer, target, given)

    def _heal(self, healer, target, amount: float, *, limit=math.inf) -> float:
        return self._settle_heal(healer, target.owner, self.vitals_of(target), amount, limit=limit)

    def _settle_heal(self, healer, owner, vitals, amount, *, limit=math.inf):
        if vitals.dead or self.tick < vitals.healing_block_until:
            return 0.0
        if self.tick < vitals.healing_boost_until:
            amount *= 1.5
        given = vitals.heal(min(amount, limit))
        if given > 0.0:
            if not isinstance(healer, bots.Bot):
                healer.stats.add(stats.HEALING, given)
            self._sync_body_health(owner, vitals)
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

    def _damage(
        self, shooter, target: Target, amount: float, critical: bool, loadout, frame: int,
        *, world_tick: int | None = None, tags: tuple = (), beam: bool = False,
        damage_over_time: bool = False, source_scaled: bool = False, limit: float = math.inf,
        source_entity: int | None = None, recall_protected: bool | None = None,
    ) -> float:
        vitals = self.vitals_of(target)
        recalling = tracer.recalling(target.owner) if recall_protected is None else recall_protected
        if (vitals.dead or self.tick < vitals.revive_protection_until
                or amount <= 0.0 or recalling
                or body_protection.active(target.owner) or self.zenyatta_orbs.protects(target.owner)):
            return 0.0
        if not source_scaled:
            amount *= self.offense_scale(shooter)
        particle = zarya_barrier.active(target.owner)
        if particle is not None:
            source = source_entity if source_entity is not None else _source_entity(shooter)
            zarya_barrier.damage_particle_barrier(particle, vitals, amount, source)
            return 0.0
        if self.tick < vitals.nano_until:
            amount *= 0.5
        if self.tick <= self.breather_deadlines.get(target.entity, -1):
            amount *= roadhog_breather.damage_scale(target.owner)
        amount *= self.zenyatta_orbs.damage_scale(target)
        damage_filter = body_protection.fortify(target.owner) or body_protection.ironclad(target.owner)
        if damage_filter is not None:
            modifier = damage_filter.node.field("m_50530EAB")
            reduction = _number(damage_filter.evaluate(modifier, watch=False), 0.0)
            amount *= max(0.0, min(1.0, 1.0 + reduction))
        base_credit = getattr(getattr(shooter, "hero", None), "guid", None) in ultimate.BASE_DAMAGE_HEROES
        base_before = sum(vitals.now.values()) if base_credit else 0.0
        dealt = vitals.damage(amount, beam=beam, damage_over_time=damage_over_time, limit=limit)
        if dealt <= 0.0:
            return 0.0
        # The 224E wake branch ignores damage in the first 0.5 seconds and
        # only shortens an eligible sleeper to the 0.5-second wake interval.
        at = self.tick if world_tick is None else world_tick
        if vitals.sleep_wake_after <= at < vitals.sleep_until and SLEEP_WAKE_EXCLUDED not in tags:
            vitals.sleep_until = min(vitals.sleep_until, at + SLEEP_WAKE_TICKS)
        killed = vitals.now[HEALTH_POOL] <= EPSILON
        if killed:
            vitals.sleep_until = vitals.sleep_wake_after = 0
            vitals.healing_block_until = vitals.healing_boost_until = 0
            vitals.nano_until = vitals.hack_until = 0
            vitals.now[HEALTH_POOL] = 0.0
            vitals.died_at = self.now
            vitals.death_tick = self.match.tick if world_tick is None else world_tick
            vitals.killer = source_entity if source_entity is not None else _source_entity(shooter)
            mei_frost.clear(self, target.entity, target.owner)
        elif loadout == mei_frost.SPRAY_LOADOUT:
            mei_frost.contact(self, shooter, target, self.tick if world_tick is None else world_tick)
        if not isinstance(shooter, bots.Bot) and target.owner is not shooter:
            flags = (CRITICAL if critical else 0) | (KILLING if killed else 0)
            self._tell_hit(shooter, target, dealt, flags, frame)
            shooter.stats.add(stats.HERO_DAMAGE, dealt)
            charge_damage = base_before - sum(vitals.now.values()) if base_credit else dealt
            ultimate.credit(shooter, target, charge_damage, tags)
            if self.match.game_map.free_for_all or shooter.team != target.team:
                ratio = reaper.reaping_ratio(shooter)
                owner_health = self.vitals.get(shooter.body) if ratio else None
                if owner_health is not None:
                    self._settle_heal(shooter, shooter, owner_health, dealt * ratio)
        victim = target.owner
        if killed and isinstance(victim, bots.Bot):
            # The NPC death script owns its terminal health change and death event ordering.
            self._bot_died(shooter, victim, critical, loadout, vitals.death_tick, source=vitals.killer)
            return dealt
        self._sync_body_health(victim, vitals)
        if killed:
            self._died(shooter, victim, critical, loadout, source=vitals.killer)
        return dealt

    def _tell_hit(self, shooter, target: Target, dealt: float, flags: int, frame: int) -> None:
        """25001 to the shooter: the hit marker and the target's health bar (frame: the shot's)."""
        value = {
            "+0x78": {"+0x0": {"+0x0": target.entity}, "+0x4": max(1, round(dealt)), "+0x8": flags},
            "+0x84": frame & 0xFFFFFFFF,
        }
        self._queue_notice(shooter, target.owner, target.entity, HIT, value)

    def _kill_notice(self, killer, victim, *, source=None) -> None:
        """25005 to the killer: "ELIMINATED: <name>" (1126.07C; +0x89 assist would be 01F0.07C, +0x88 a
        score shown when above 0). Its handler (0x7FF7895D2B50) reads only +0x84, +0x88 and +0x89."""
        entity = _source_entity(victim)
        notice = {
            "+0x78": 0,
            "+0x80": {"+0x0": source if source is not None else killer.body},
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
    def _death_parameters(killer, critical: bool, loadout, *, source=None) -> dict:
        return {
            KILLER: expr.Entity(source if source is not None else _source_entity(killer)),
            CRIT: bool(critical),
            LOADOUT: expr.Asset(loadout) if loadout else None,
            ASSISTS: (),
        }

    def _bot_died(self, killer, bot: bots.Bot, critical: bool, loadout, tick: int, *, source=None) -> None:
        self.mercy_effects.retire(bot.entity)
        self.lucio_barriers.retire(bot.entity)
        self.lucio_auras.retire(bot.entity)
        self.zenyatta_orbs.retire(bot.entity)
        self.roadhog_hooks.retire(bot.entity)
        self.clear_pins(bot.entity)
        self.stuns.pop(bot.entity, None)
        bot.died(tick)
        if not bot.retain_corpse:
            self.respawns.append((self.now + BOT_RESPAWN, bot))
        if not isinstance(killer, bots.Bot):
            killer.stats.add(stats.ELIMINATIONS)
            self._kill_notice(killer, bot, source=source)
        log.info("[game] %s: %s killed %s", self.match.label(), killer.name, bot.name)
        script = bot.body_script
        if script is None:
            return
        try:
            source = source if source is not None else _source_entity(killer)
            params = self._death_parameters(killer, critical, loadout, source=source)
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

    def _died(self, killer, victim, critical: bool, loadout, *, source=None) -> None:
        """A player died: the notices, his body script's "died" (the kill feed line), and the respawn."""
        self.mercy_effects.retire(victim.body)
        self.lucio_barriers.retire(victim.body)
        self.lucio_auras.retire(victim.body)
        self.zenyatta_orbs.retire(victim.body)
        self.roadhog_hooks.retire(victim.body)
        self.breather_deadlines.pop(victim.body, None)
        wall_ride.cancel(victim)
        self.clear_pins(victim.body)
        self.reaper_blossoms.pop(victim.body, None)
        self.reaper_shadows.pop(victim.body, None)
        mccree_roll.clear(victim.body_script)
        self.moira_hots.pop(victim.body, None)
        self.stuns.pop(victim.body, None)
        label = self.match.label()
        log.info("[game] %s: %s killed %s", label, killer.name, victim.name)
        victim.stats.add(stats.DEATHS)
        bot_killer = isinstance(killer, bots.Bot)
        source = source if source is not None else _source_entity(killer)
        if killer is not victim and not bot_killer:
            killer.stats.add(stats.ELIMINATIONS)
            self._kill_notice(killer, victim, source=source)
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
                params = self._death_parameters(killer, critical, loadout, source=source)
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
        charge = _number(script.component.entity_var(ULT_CHARGE).value(), 0.0) if script else None
        if not self.match.switch_hero(player, player.hero):
            self.respawns.append((self.now + RESPAWN_RETRY, player))
            return
        if charge is not None and player.body_script is not None:
            cost = _number(player.body_script.component.entity_var(ULT_COST).value(), 0.0)
            player.body_script.set_ult_charge(max(0.0, min(charge, cost)))
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
