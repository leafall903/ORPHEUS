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
  command's aim from the body's FirstPerson height (m_E29993A5 standing, m_816B830F crouched, DATA;
  native camera/muzzle animation unverified), or a projectile (m_projectileSpeed) flown each tick.
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
    bots,
    dva_matrix,
    enforcer,
    genji,
    mei_frost,
    molten_core,
    moira_beams,
    moira_orbs,
    observers,
    photon_barrier,
    pools,
    projectiles,
    sigma,
    stats,
    symmetra_beam,
    symmetra_sentry,
    torbjorn_turret,
    tracer_pistol,
    voicelines,
    winston_barrier,
    winston_jump,
    winston_primal,
    winston_tesla,
    world,
)
from ow174.game.bits import BitWriter
from ow174.game.collision import MOVER, World
from ow174.game.commands import Command
from ow174.game.mover import mover_data, statescript_mods
from ow174.game.script import expr, graph, nodes, runtime
from ow174.game.script.bot import DIED, BotScript
from ow174.game.script.driver import ULT_CHARGE, ULT_COST, Snapshot
from ow174.game.shot_patterns import ring_directions
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
#104319 STUFirstPersonComponent m_E29993A5/m_816B830F, body XML; camera animation remains open.
EYE_STANDING = {0x04000000000002A3: 1.425, dva_matrix.MECH: 1.74,
                0x0400000000001C46: 1.7, 0x04000000000026F0: 1.3, 0x0400000000002AB1: 2.1}
EYE_CROUCHED = {0x04000000000003CF: 1.085, 0x04000000000002A3: 0.9975,
                dva_matrix.MECH: 1.74, 0x0400000000001C46: 1.1,
                0x04000000000026F0: 0.91, 0x0400000000002AB1: 1.75}
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
# Reconstructed server policy, scoped to Sigma's two 187F Hypersphere volleys.
# DATA supplies their 0.44 s lifetime, 50 m/s speed and 30-point explosion;
# the native server expiry dispatcher is not available. Other projectiles keep
# their existing expiry behavior until their individual policy is established.
AIRBURST_VOLLEYS = {(0x187F, 20), (0x187F, 28)}
BLAST_SURFACE_OFFSET = 1e-4  # reconstructed: retain the incoming side of an impact surface
FLASHBANG_EFFECT = 0x04A0000000000079  # .053/0079 ->0228 ->224E
FLASHBANG_MODE_FLAG = 13408  # 0228 duration: flag set1.7s, otherwise0.8s
SLEEP_DART_EFFECT = 0x04A000000000026F  # 026F.053 -> 0BDE:51 -> 224E
SLEEP_DART_PROJECTILE = 0x04000000000015E8
SLEEP_SECONDS = 5.0
SLEEP_WAKE_SECONDS = 0.5
SLEEP_NO_WAKE_TAG = expr.Asset(0x0D8000000000191F)  # 224E:75
BIOTIC_GRENADE = 0x0400000000001426
BIOTIC_GRENADE_EFFECT = 0x04A0000000000267  # 0267.053 -> 0C71 -> 0C72 or 0B99
BIOTIC_SECONDS = 4
BIOTIC_DAMAGE = 60  # 0C72:6, stock player branch
BIOTIC_HEAL = 100  # 0B99:7, stock player branch
BIOTIC_HEAL_SCALE = 1.5  # 0B99:8
BIOTIC_HEAL_DELAY = 0.05  # 0B99:17


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
    if crouched:
        return EYE_CROUCHED.get(body, EYE_CROUCHED_DEFAULT)
    return EYE_STANDING.get(body, EYE)


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
        self.shield_recovery_tick: float | None = None
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

    def damage(self, amount: float, *, beam: bool = False, armour_reduction: bool = True) -> float:
        """Take a hit from shields, then armour, then health (unverified, as OW1 did: what reaches armour
        while it has some is ARMOUR_BLOCK less, at most halved). Beams use the late OW1 30% reduction.
        Returns what the pools lost."""
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
            if armour_reduction and kind == ARMOUR_POOL and self.now[kind] > 0.0:
                left = left * 0.7 if beam else max(left - ARMOUR_BLOCK, left / 2)
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
    knockback: tuple | None = None
    self_damage_scale: float = 0.0
    self_impulse_scale: float = 0.0
    wakes_sleep: bool = True

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
    loadout = loadout.guid if isinstance(loadout, expr.Asset) else None
    effect = state.evaluate(modify.get("m_277A4ECA"), watch=False) if modify.get("m_277A4ECA") else None
    wakes_sleep = all(
        state.evaluate(tag["m_EE3055BD"], watch=False) != SLEEP_NO_WAKE_TAG
        for tag in modify.get("m_148F3152") or ()
        if isinstance(tag, dict) and tag.get("m_EE3055BD") is not None
    )
    sigma_blast = cfg.get("$") == "STU_4AA6CA44" and loadout in (
        sigma.HYPERSPHERE_LOADOUT, sigma.ACCRETION_LOADOUT,
    )
    rings = []
    for ring in cfg.get("m_0D30E40C") or []:  # WeaponProjectileExplosionRing {m_radius, m_B399CACB}
        if isinstance(ring, dict):
            radius = (
                state.evaluate(ring.get("m_radius"), watch=False) if ring.get("m_radius") is not None else 0.0
            )
            scalar = ring.get("m_B399CACB")
            scalar = state.evaluate(scalar, watch=False) if scalar is not None else None
            # 104319 D8C80A evaluates the ring multiplier with a 1.0 fallback.
            rings.append((_number(radius, 0.0), _number(scalar, 1.0)))
    return HitDef(
        amount=amount,
        wakes_sleep=wakes_sleep,
        knockback=sigma.projectile_knockback(state, modify),
        loadout=loadout,
        falloff_start=value("m_EAD7F104"),
        falloff_end=value("m_157E5BC5"),
        falloff_floor=value("m_72A81154"),
        critical=value("m_A872C70E", 1.0),
        rings=tuple(sorted(rings)),
        effect=effect.guid if isinstance(effect, expr.Asset) else None,
        self_damage_scale=value("m_8A3E3883", 1.0) if sigma_blast else 0.0,
        self_impulse_scale=value("m_2FE97560", 1.0) if sigma_blast and cfg.get("m_49F60FB8") else 0.0,
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
    airburst: bool = False  # explode at the final flight position when lifetime/range expires
    absorbable: bool = True  # reconstructed projectile eligibility; Accretion is excluded
    gravity: float = 0.0
    velocity: tuple | None = None
    accretion_source: tuple[int, int] | None = None  # launch body and graph instance
    reflectable: bool = False
    owner_body: int | None = None  # body-scoped flight, updated on Deflect
    halt_instance: int | None = None
    halt_at: int | None = None
    body_radius: float = 0.0
    wall_radius: float = 0.0
    bounces: int = 0
    bounce_normal: float = 0.0
    bounce_tangent: float = 0.0
    definition: int | None = None  # authored identity also used without a prediction lease
    penetrated: set[tuple[int, int | None]] | None = None
    target_limit: int = 1  # Multiple body contacts for Mei spray; Fire Strike is unbounded.

    def impact_damage(self) -> float:
        if self.direct is None:
            return 0.0
        if isinstance(self.shooter, bots.Bot) and enforcer.manual(self.shooter):
            return self.direct.amount * self.direct.scale(self.flown * self.speed)
        return self.direct.amount


class Combat:
    """The match's health, hits, deaths and respawns."""

    def __init__(self, match) -> None:
        self.match = match
        self.vitals: dict[int, Vitals] = {}  # body or bot entity -> its health
        self.history: dict[int, deque] = {}  # entity -> (tick, feet, height, eye) of the last HISTORY ticks
        self.replacement_ticks: dict[int, int] = {}  # replacement body -> authoritative world tick
        self.rockets: list[Rocket] = []
        self.halt_pulls: dict[int, tuple[tuple[float, float, float], int]] = {}
        self.halt_slows: dict[int, tuple[int, int]] = {}
        self.frost_sources: dict[int, dict[int, tuple[float, int, bool]]] = {}
        self.frost_hits: dict[int, tuple[float, int, int, int, int]] = {}
        self.respawns: list[tuple[float, object]] = []  # (when, a player or a bot)
        self.next_bot = bots.BOT_ENTITY + len(match.bots)
        self.now = 0.0
        self.tick = 0
        self.melee_seen: dict[int, dict[tuple[int, int], int]] = {}  # body -> counters by instance/state
        self.winston_jump = winston_jump.JumpPack(self)
        self.winston_primal = winston_primal.PrimalRage(self)
        self.tesla_frames: dict[int, int] = {}  # body -> last consumed posed command frame
        self.genji_strikes: dict[int, tuple[int, int, set[int]]] = {}
        self.booster_contacts: dict[int, tuple[int, int, set[int]]] = {}
        self.genji_blades: dict[int, dict[tuple[int, int], tuple[int, set[int]]]] = {}
        self.grasp_shields: dict[int, sigma.ShieldDecay] = {}
        self.grasp_grants: dict[int, tuple[int, int]] = {}  # last instance/absorption activation
        self.stuns: dict[int, int] = {}  # body identity -> exclusive world-tick deadline
        self.sleeps: dict[int, tuple[int, int]] = {}  # body identity -> (start, exclusive deadline)
        self.biotic: dict[int, tuple[int, int, int]] = {}  # body -> (block until, boost from, boost until)
        self.pins: dict[int, int] = {}  # manual Assassin source body -> controlled victim body

        self.barriers: dict[int, sigma.Barrier | winston_barrier.Bubble | photon_barrier.Wall] = {}
        self.sentries: list[symmetra_sentry.Sentry] = []
        self.torbjorn_turrets: dict[int, torbjorn_turret.Turret] = {}
        self.molten_pools: dict[int, molten_core.Pool] = {}
        self.molten_burns: dict[tuple[int, int], tuple] = {}
        self.moira_orbs: list[moira_orbs.Orb] = []
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
        symmetra_sentry.release_target(self, player.body)
        molten_core.release_target(self, player.body)
        self.vitals.pop(player.body, None)
        self.history.pop(player.body, None)
        self.replacement_ticks.pop(player.body, None)
        self.melee_seen.pop(player.body, None)
        self.tesla_frames.pop(player.body, None)
        self.winston_jump.retire(player.body)
        self.winston_primal.retire(player.body)
        self.genji_strikes.pop(player.body, None)
        self.booster_contacts.pop(player.body, None)
        self.genji_blades.pop(player.body, None)
        self.grasp_shields.pop(player.body, None)
        self.grasp_grants.pop(player.body, None)
        self.stuns.pop(player.body, None)
        self.sleeps.pop(player.body, None)
        self.biotic.pop(player.body, None)
        self.halt_pulls.pop(player.body, None)
        self.halt_slows.pop(player.body, None)
        self.clear_frost(player)
        self.clear_pins(player.body)

        for entity, barrier in list(self.barriers.items()):
            if barrier.owner_body == player.body and not getattr(barrier, "persists_after_respawn", False):
                del self.barriers[entity]
        # Retire leased flight and body-scoped flight even without a lease.
        self.rockets[:] = [
            rocket for rocket in self.rockets
            if rocket.shooter is not player
            or (rocket.shot is None and rocket.owner_body != player.body)
        ]
        self.respawns[:] = [(when, who) for when, who in self.respawns if who is not player]

    def record_replacement(self, body: int) -> None:
        """Keep the creation boundary independent of the rolling pose history."""
        self.replacement_ticks[body] = self.match.tick
        self.stuns.pop(body, None)
        self.sleeps.pop(body, None)
        self.biotic.pop(body, None)
        self.clear_pins(body)

    def is_stunned(self, actor, *, tick: int | None = None) -> bool:
        entity = _source_entity(actor)
        at = self.tick if tick is None else tick
        frost = self.frost(actor, tick=tick)
        return (bool(frost and frost[2]) or entity in self.pins.values()
                or self.stuns.get(entity, 0) > at or self.sleeps.get(entity, (0, 0))[1] > at)

    def frost(self, actor, *, tick: int | None = None):
        at = self.tick if tick is None else tick
        return mei_frost.live(self.frost_hits.get(_source_entity(actor)), at * round(TICK * 1000))

    def clear_frost(self, player) -> None:
        self.frost_sources.pop(player.body, None)
        if self.frost_hits.pop(player.body, None) is not None and player.body_script is not None:
            player.body_script.component.set(mei_frost.PROGRESS_VAR, 0.0)

    def _advance_frost(self) -> None:
        now = self.tick * round(TICK * 1000)
        for player in self.match.players:
            previous = self.frost_hits.get(player.body)
            if previous is None:
                continue
            script = player.body_script
            if (mei_frost.live(previous, now) is None or script is None or sigma.body_protection(player)
                    or sigma.fortify(player) is not None or mei_frost.immune(script.component)):
                self.clear_frost(player)
                continue
            sources = self.frost_sources.get(player.body)
            if sources is None:
                continue
            count = len(sources)
            mei_frost.prune(sources, now)
            if len(sources) != count:
                sample = mei_frost.aggregate(sources, now, *previous[3:])
                self.frost_hits[player.body] = sample
                script.component.set(mei_frost.PROGRESS_VAR, sample[0])

    def _apply_mei_frost(self, shooter, target: Target) -> None:
        player = target.owner
        if isinstance(player, bots.Bot) or player.body_script is None or sigma.fortify(player) is not None:
            return
        component = player.body_script.component
        if mei_frost.immune(component):
            return
        previous = self.frost(player)
        if previous is not None and previous[2]:
            return
        now = self.tick * round(TICK * 1000)
        sources = self.frost_sources.setdefault(target.entity, {})
        mei_frost.prune(sources, now)
        source = _source_entity(shooter)
        progress, _, _ = sources.get(source, (0.0, 0, False))
        sources[source] = mei_frost.source_hit(shooter, progress, now, initial=mei_frost.INITIAL, step=mei_frost.STEP)
        pose = player.mover
        sample = mei_frost.aggregate(sources, now, round(pose.yaw), round(pose.pitch))
        self.frost_hits[target.entity] = sample
        component.set(mei_frost.PROGRESS_VAR, sample[0])
        if sample[2]:
            self.frost_sources.pop(target.entity, None)
            self._interrupt(target)

    def clear_pins(self, entity: int) -> None:
        from ow174.game import heavy_assault

        for source, victim in list(self.pins.items()):
            if entity in (source, victim):
                del self.pins[source]
                heavy_assault.release_capture(self, source, victim)

    def _hit_effect(self, target: Target, effect: int | None) -> None:
        """Resource-backed stun reconstruction; native immunity/presentation remain unverified."""
        if effect == SLEEP_DART_EFFECT:
            if (not isinstance(target.owner, bots.Bot) and self._control_allowed(target)
                    and self.sleeps.get(target.entity, (0, 0))[1] <= self.tick):
                self.sleeps[target.entity] = self.tick, self.tick + math.ceil(SLEEP_SECONDS / TICK)
                self._interrupt(target)
            return
        if effect != FLASHBANG_EFFECT or self.vitals_of(target).dead:
            return
        variables = graph.mode_variables(self.match.game_map.mode_guid) or {}
        duration = 1.7 if expr.truthy(variables.get(FLASHBANG_MODE_FLAG)) else 0.8
        self._apply_stun(target, duration)

    def _control_allowed(self, target: Target) -> bool:
        return (not self.vitals_of(target).dead and not sigma.body_protection(target.owner)
                and sigma.particle_barrier(target.owner) is None and sigma.fortify(target.owner) is None)

    def _apply_stun(self, target: Target, duration: float, *, extend_only: bool = False) -> None:
        if not self._control_allowed(target):
            return
        deadline = self.tick + math.ceil(duration / TICK)
        previous = self.stuns.get(target.entity, 0)
        if extend_only and deadline <= previous:
            return
        self.stuns[target.entity] = max(deadline, previous)
        self._interrupt(target)

    def _interrupt(self, target: Target) -> None:
        script = getattr(target.owner, "body_script", None)
        if script is None:
            return
        component = script.component
        if isinstance(target.owner, bots.Bot):
            from ow174.game import assassin, heavy_assault, sniper

            sniper.stop(target.owner)
            assassin.bind(component.world, component, None)
            if target.owner.hero == heavy_assault.HERO and target.entity in self.pins:
                heavy_assault.stop(component, target.owner)
                self.clear_pins(target.entity)
        else:
            self.winston_jump.cancel(target.entity)
            self.winston_primal.interrupt(script)
            photon_barrier.interrupt(script)
        component.edges([(value, False) for value in tuple(component.held)])
        for instance in list(component.instances.values()):
            for state in list(instance.states.values()):
                if instance.graph.index == genji.STRIKE and isinstance(state, nodes.MoveTo) and state.active:
                    state.end(False)
                if isinstance(state, nodes.Ability):
                    state.presses.clear()
                    if instance.graph.index != genji.BLADE or state not in getattr(script, "ultimates", ()):
                        state.stop()  # use authored exit/cooldown paths for running channels
        component.run_now()
        component.shots.clear()
        if not isinstance(target.owner, bots.Bot) and target.owner.hero.body == dva_matrix.MECH:
            target.owner.mover.track_mods(statescript_mods(component), script.frame)
            self.booster_contacts.pop(target.entity, None)
        self.genji_strikes.pop(target.entity, None)
        sigma.interrupt_flux_lift(self, target.owner)

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
        strike = self.genji_strikes.get(player.body)
        if strike is not None:
            instance = script.component.instances.get(strike[0])
            movement = instance.states.get(1) if instance is not None else None
            if movement is None or not movement.active or movement.counter != strike[1]:
                self.genji_strikes.pop(player.body, None)
        shots, script.component.shots[:] = list(script.component.shots), []
        self.winston_primal.sync_health(player, script)
        if self.is_dead(player) or self.is_stunned(player):
            return
        if command is not None:
            for state, number, time, activation in shots:
                try:
                    self._shoot(player, command, state, number, time, activation=activation)
                except Exception:
                    log.exception("[game] %s: %s's shot failed", self.match.label(), player.name)
        try:
            if command is not None:
                self._zarya_beam(player, command, script)
                moira_beams.attack(self, player, command, script)
                winston_tesla.attack(self, player, command, script)
                self.winston_jump.command(player, command)
                self.winston_primal.command(player, command)
                self._genji_blade(player, command, script)
                dva_matrix.booster_contact(self, player, command, script)
            symmetra_beam.attack(self, player, command, script)
            self._melee(player, command, script)
        except Exception:
            log.exception("[game] %s: %s's beam or melee failed", self.match.label(), player.name)

    def genji_strike(self, player, movement, previous, command, ended) -> None:
        """Resolve the authored tracker over the collision-clipped movement segment."""
        instance = movement.instance
        if player.hero.guid != genji.HERO or instance.component.strict:
            return
        identity = (instance.id, movement.counter)
        record = self.genji_strikes.get(player.body)
        victims = record[2] if record is not None and record[:2] == identity else set()
        track = instance.states.get(33)
        if track is not None:
            radius = _number(track.evaluate(track.node.field("m_radius"), watch=False), 0)
            modify = instance.state(3).node.field("m_modifyHealth") or {}
            amount = _number(instance.evaluate(modify.get("m_amount")), 0)
            loadout = instance.evaluate(modify.get("m_loadout"))
            start = _along(previous, (0, 1, 0), 1)
            end = _along(player.mover.position, (0, 1, 0), 1)
            delta = _sub(end, start)
            length = math.sqrt(_dot(delta, delta))
            if length > EPSILON and radius > 0 and amount > 0:
                direction = tuple(axis / length for axis in delta)
                for target in self._enemies(player):
                    if target.entity in victims:
                        continue
                    a, b = target.segment()
                    distance = ray_capsule(start, direction, a, b, target.radius + radius)
                    if distance is None or distance > length:
                        continue
                    if not capsule_visible(self._collision(), end, a, b, target.radius):
                        continue
                    victims.add(target.entity)
                    self._damage(player, target, amount, False,
                                 loadout.guid if isinstance(loadout, expr.Asset) else None, command.frame)
                    if self.vitals_of(target).dead:
                        genji.reset_strike(player, target.entity)
        if ended:
            self.genji_strikes.pop(player.body, None)
        else:
            self.genji_strikes[player.body] = (*identity, victims)

    def _genji_blade(self, player, command: Command, script) -> None:
        """Resolve the authored sword target windows once per enemy body."""
        if player.hero.guid != genji.HERO or script.component.strict:
            return
        weapon = next((i for i in script.component.instances.values() if i.graph.index == 0x0304), None)
        if weapon is None:
            return
        centre = _along(player.mover.position, (0, 1, 0), 1)
        facing = aim(*command.aim())
        active = False
        for tracking, damage in ((66, 21), (67, 22)):
            track = weapon.states.get(tracking)
            if track is None or not track.active:
                continue
            active = True
            records = self.genji_blades.setdefault(player.body, {})
            key = (weapon.id, tracking)
            activation, victims = records.get(key, (0, set()))
            if activation != track.counter:
                victims = set()
                records[key] = (track.counter, victims)
            radius = _number(track.evaluate(track.node.field("m_radius"), watch=False), 0)
            angle = _number(track.evaluate(track.node.field("m_A30B383E"), watch=False), 0)
            threshold = math.cos(math.radians(angle))
            state = weapon.state(damage)
            modify = state.node.field("m_modifyHealth") or {}
            amount = _number(state.evaluate(modify.get("m_amount"), watch=False), 0)
            loadout = state.evaluate(modify.get("m_loadout"), watch=False)
            for target in self._enemies(player):
                if target.entity in victims:
                    continue
                a, b = target.segment()
                if segment_distance(centre, a, b) > radius + target.radius:
                    continue
                delta = _sub(_along(a, _sub(b, a), 0.5), centre)
                distance = math.sqrt(_dot(delta, delta))
                if _dot(facing, delta) + EPSILON < threshold * distance:
                    continue
                if not capsule_visible(self._collision(), centre, a, b, target.radius):
                    continue
                victims.add(target.entity)
                self._damage(player, target, amount, False,
                             loadout.guid if isinstance(loadout, expr.Asset) else None, command.frame)

        if not active:
            self.genji_blades.pop(player.body, None)

    def _zarya_beam(self, player, command: Command, script) -> None:
        """Resolve the stock Zarya beam while 0241's ray and damage states are active."""
        if script.hero != 0x02E0000000000068 or script.component.strict:
            return
        weapon = next((i for i in script.component.instances.values() if i.graph.index == 0x0241), None)
        if weapon is None:
            return
        ray, damage = weapon.states.get(43), weapon.states.get(10)
        if ray is None or damage is None or not (ray.active and damage.active):
            return
        reach = expr.to_float(weapon.context().var(graph.INSTANCE, 1648))
        if reach <= 0:
            return
        radius = expr.to_float(ray.evaluate(ray.node.field("m_57DAE1B5"), watch=False))
        origin, direction = self._eye(player), aim(*command.aim())
        wall = ray_world(self._collision(), origin, direction, reach)
        limit = reach if wall is None else wall
        victim = None
        for target in self._enemies(player):
            distance = ray_capsule(origin, direction, *target.segment(), target.radius + radius)
            if distance is not None and distance < limit:
                limit, victim = distance, target
        stopped = (
            sigma.barrier_intersection(self, player, origin, direction, limit) if self.barriers else None
        )
        config = damage.node.field("m_modifyHealth")
        amount = expr.to_float(damage.evaluate(config["m_amount"], watch=False)) * player.quanta / 1e6
        if amount <= 0:
            return
        if stopped is not None:
            sigma.damage_barrier(self, stopped[1], amount)
        elif victim is not None:
            loadout = damage.evaluate(config["m_loadout"], watch=False)
            self._damage(player, victim, amount, False,
                         loadout.guid if isinstance(loadout, expr.Asset) else None, command.frame)

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
            eye = self._eye(player)
            centre = _along(eye, aim(*command.aim()), reach)
            if self.barriers and amount > 0.0:
                sigma.hit_barrier_with_melee(self, player, eye, centre, radius, amount)
            view_tick = command.frame - command.view_delay(player.quanta) * 1e6 / player.quanta
            for target in self._enemies(player):
                shown = self.seen(target, view_tick)
                if shown is None:
                    continue
                a, b = shown.segment()
                if (segment_distance(centre, a, b) - shown.radius <= radius and amount > 0.0
                        and capsule_visible(self._collision(), self._eye(player), a, b, shown.radius)
                        and not genji.parries_melee(self, target, player.mover.position, modify)):
                    self._damage(player, target, amount, False, loadout, command.frame)

    def update(self, now: float, tick: int, frames: bool = True) -> None:
        """Each tick: keep where the bodies are, fly the rockets, respawn, send health that changed. The
        other players' body frames only on the ticks that send (`frames`)."""
        if self.match.collision_world is None:
            return
        retained = max(self.tick + 1, tick - COMBAT_CATCH_UP + 1)
        first = tick
        replaying_sentry = any(sentry.bot.moved_tick is not None and not sentry.bot.dead
                              for sentry in self.sentries)
        if self.tick and tick > self.tick + 1 and (
            any(rocket.last_tick is not None for rocket in self.rockets)
            or any(orb.last_tick is not None for orb in self.moira_orbs)
            or replaying_sentry
            or any(
                bot.firing and not bot.dead and isinstance(bot.body_script, BotScript)
                for bot in self.match.bots
            )
        ):
            first = retained
        if replaying_sentry and first > self.tick + 1:
            self._catch_up_respawns_before_sentry_replay(now, tick, first)
        for target in self._targets():
            history = self.history.setdefault(target.entity, deque(maxlen=HISTORY))
            history.append((tick, target.feet, target.height, target.eye))
        # Replay the retained combat frames using the most recent poses. Player input
        # accepted just before the scheduler caught up joins only at the endpoint.
        for at in range(first, tick + 1):
            self.now, self.tick = now - (tick - at) * TICK, at
            if self.frost_hits:
                self._advance_frost()
            if self.winston_primal.expiries:
                self.winston_primal.update(round(TICK * 1000))
            sigma.update_barriers(self)
            sigma.recover_base_shields(self, TICK)
            for rocket in list(self.rockets):
                if at == tick or rocket.last_tick is not None:
                    self._fly(rocket)
            self._bot_weapons(at)
            moira_orbs.update(self, endpoint=at == tick)
            symmetra_sentry.update(self, endpoint=at == tick)
            if self.torbjorn_turrets:
                torbjorn_turret.update(self)
            if self.molten_pools or self.molten_burns:
                molten_core.update(self)
            if at < tick and any(sentry.bot.moved_tick is not None for sentry in self.sentries):
                self._respawn_players_during_sentry_replay()
        if any(bot.assassin_controller is not None for bot in self.match.bots):
            # Mounted controllers move during the combat step, after the initial pose capture.
            for target in self._targets():
                if isinstance(target.owner, bots.Bot) and target.owner.assassin_controller is not None:
                    self.history[target.entity][-1] = (tick, target.feet, target.height, target.eye)
        sigma.update_shields(self)
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
        current = {target.entity: target.owner for target in self._targets()}
        for effects in (self.halt_pulls, self.halt_slows):
            for entity, (_, deadline) in list(effects.items()):
                if entity not in current or deadline <= tick:
                    del effects[entity]
        for entity, owner in current.items():
            vitals = self.vitals.get(entity)
            if vitals is not None and vitals.particle_barriers:
                active = {sigma.particle_barrier_key(pet) for pet in sigma.particle_barriers(owner)}
                for key in vitals.particle_barriers.keys() - active:
                    del vitals.particle_barriers[key]
        for entity in [key for key, deadline in self.stuns.items() if deadline <= tick]:
            del self.stuns[entity]
        for entity, (_, deadline) in list(self.sleeps.items()):
            if entity not in current or deadline <= tick:
                del self.sleeps[entity]
        for entity, deadlines in list(self.biotic.items()):
            if entity not in current or max(deadlines) <= tick:
                del self.biotic[entity]
        for table in (self.history, self.vitals, self.replacement_ticks, self.stuns):
            for entity in [key for key in table if key not in current]:
                del table[entity]
        for source, victim in list(self.pins.items()):
            if source not in current or victim not in current:
                del self.pins[source]

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
            if bot.body == torbjorn_turret.BODY:
                found.append(Target(bot.entity, bot.team, tuple(bot.position), torbjorn_turret.HEIGHT,
                                    torbjorn_turret.RADIUS, torbjorn_turret.EYE, bot))
                continue
            if bot.body == symmetra_sentry.BODY:
                found.append(Target(bot.entity, bot.team, tuple(bot.position), 1.0, .2, .5, bot))
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
        owned = {sentry.bot.entity for sentry in self.sentries if sentry.owner is shooter}
        owned.update(entity for entity, turret in self.torbjorn_turrets.items() if turret.owner is shooter)
        turret = self.torbjorn_turrets.get(source)
        if turret is not None:
            owned.add(turret.owner.body)
        found = []
        for target in self._targets():
            if target.owner is shooter or target.entity == source or target.entity in owned:
                continue
            if not free_for_all and target.team == shooter.team:
                continue
            if self.vitals_of(target).dead or sigma.body_protection(target.owner) == "phase":
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

    def _bot_weapons(self, tick: int) -> None:
        """Advance the weapon bodies of the map's firing bots and consume their volley reports."""
        from ow174.game import heavy_assault, npc_control

        for bot in self.match.bots:
            script = bot.body_script
            controller = bot.assassin_controller
            controlled = npc_control.manual(bot)
            if ((not bot.firing and not controlled and controller is None) or bot.dead
                    or not isinstance(script, BotScript) or script.frame > tick):
                continue
            try:
                if controller is not None:
                    allowed = controller.update(tick)
                else:
                    allowed = npc_control.update_input(self, bot) if controlled else not self.is_stunned(bot)
                sigma.sync_flux_airborne(script.component, bot.effect_mover)
                charge = (heavy_assault.ability(script.component, heavy_assault.CHARGE_GRAPH, heavy_assault.CHARGE_ABILITY)
                          if controlled and bot.hero == heavy_assault.HERO else None)
                phase = charge.instance.vars.get(59) if charge is not None else None
                charging = phase is not None and expr.to_int(phase.value()) == 1
                snapshot = script.advance_live(tick, bot.position)
                if charging and expr.to_int(phase.value()) == 0 and bot.effect_mover is None:
                    bot.velocity = (0.0, 0.0, 0.0)
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
                if controller is not None:
                    controller.retire()
                    bot.assassin_controller = None
                bot.body_script = None
                log.exception("[game] %s: %s's weapon script failed", self.match.label(), bot.name)
        heavy_assault.sync_captures(self)

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
        if (not npc and shooter.hero.guid == symmetra_beam.HERO
                and state.instance.graph.index == symmetra_sentry.LAUNCH and state.index == 12):
            symmetra_sentry.launch(self, shooter, command, state)
            return
        if npc:
            x, y, z = shooter.position
            # Use the server body pose until the animated muzzle transform is available.
            eye = torbjorn_turret.EYE if shooter.body == torbjorn_turret.BODY else EYE
            origin, direction = (x, y + eye, z), aim(round(shooter.yaw), round(shooter.pitch))
        else:
            origin, direction = self._eye(shooter), aim(*command.aim())
        direct = hit_def(state, node.field("m_6396149F"))
        speed = field("m_projectileSpeed")
        entity_def = node.field("m_projectileEntity.m_entityDef")
        definition = state.evaluate(entity_def, watch=False) if entity_def is not None else None
        projectile = definition.guid if isinstance(definition, expr.Asset) else None
        grasp_eligible = projectile not in sigma.GRASP_EXCLUDED_PROJECTILES
        sleep_dart = (not npc and projectile == SLEEP_DART_PROJECTILE
                      and (state.instance.graph.index, node.pos) == (0x0BDD, 14))
        biotic_grenade = (not npc and projectile == BIOTIC_GRENADE
                          and (state.instance.graph.index, node.pos) == (0x0B97, 1))
        turret_throw = (not npc and (state.instance.graph.index, node.pos) == (0x16C5, 3)
                        and projectile == torbjorn_turret.THROW)
        molten = (
            projectile is not None and (not npc or projectile == molten_core.DEATH_GLOB)
            and molten_core.LAUNCHES.get((state.instance.graph.index, node.pos)) == projectile
        )
        if molten and projectile == molten_core.DEATH_GLOB:
            launch = state.evaluate(node.field("m_5EC1EB38.m_E2FD6F04"), watch=False)
            if not isinstance(launch, expr.Vec3):
                return
            direction = tuple(launch)
        if speed:
            motion = None if npc else sigma.accretion_launch(state, direction)
            sphere = sigma.hypersphere_motion(state) if not npc and shooter.hero.guid == sigma.HERO else None
            body_radius = wall_radius = bounce_normal = bounce_tangent = 0.0
            bounces = 0
            if motion is not None:
                gravity, direction = motion
                body_radius = field("m_6396149F.m_57DAE1B5", 0.0)
                wall_radius = field("m_6396149F.m_1FFD8312", 0.0)
                velocity = tuple(expr.f32(speed * axis) for axis in direction)
            elif sphere is not None:
                gravity, body_radius, wall_radius, bounces, bounce_normal, bounce_tangent = sphere
                velocity = tuple(expr.f32(speed * axis) for axis in direction)
            elif turret_throw or molten or biotic_grenade or (state.instance.graph.index, node.pos) == (0x0241, 41):
                # These throws carry a single authored acceleration row.
                acceleration, = node.field("m_projectileMotions")
                gravity = expr.to_float(state.evaluate(acceleration["m_599AB8D7"], watch=False))
                velocity = tuple(expr.f32(speed * axis) for axis in direction)
                if molten or biotic_grenade:
                    body_radius = field("m_6396149F.m_57DAE1B5", 0.0)
                if molten:
                    wall_radius = field("m_6396149F.m_1FFD8312", 0.0)
                    bounces = int(field("m_E365DB20.m_360294F0", 0))
                    bounce_normal = field("m_E365DB20.m_0D20DAFD", 0.0)
            else:
                gravity, velocity = 0.0, None
            if sleep_dart:
                body_radius = field("m_6396149F.m_57DAE1B5", 0.0)
            lifetime = field("m_projectileLifetime", 5.0) or 5.0
            reach = field("m_FEC435C6")
            if reach:
                lifetime = min(lifetime, reach / speed)
            blast = hit_def(state, node.field("m_D2D11CE9"))
            orb = (moira_orbs.launch(shooter, state, definition, origin, direction, speed, lifetime, direct)
                   if not npc else None)
            fire_strike = (not npc and (state.instance.graph.index, node.pos) == (0x00E2, 3)
                           and definition == expr.Asset(0x04000000000001EA))
            if fire_strike:
                body_radius = field("m_6396149F.m_57DAE1B5", 0.0)
                wall_radius = field("m_6396149F.m_61CD1F5B", 0.0)
            shot = None
            if isinstance(definition, expr.Asset):
                shot = self.projectiles.fired(
                    shooter, state, shot_frame, definition.guid, lifetime, origin, direction,
                    activation=activation, snapshot=snapshot,
                )
            if orb is not None:
                orb.shot = shot
                self.moira_orbs.append(orb)
                return
            damaging = (direct is not None and direct.amount > 0.0) or (
                blast is not None and blast.amount > 0.0
            )
            halt = not npc and (state.instance.graph.index, node.pos) == (0x0CCB, 8)
            body_scoped = (
                motion is not None or sphere is not None or halt or fire_strike or turret_throw
                or molten or sleep_dart or biotic_grenade
            )
            # Deflect's 03A3 filter excludes tag 0145. Grenade 1426 is
            # server-owned and qualifies without an owner prediction lease.
            reflectable = (biotic_grenade and grasp_eligible) or (
                not npc and node.field("m_45271E8C") == 1
                and ((sleep_dart and grasp_eligible)
                     or (shooter.hero.guid == sigma.HERO and state.instance.graph.index == 0x187F
                         and node.pos in (45, 56)))
            )
            target_limit = mei_frost.projectile_target_limit(state)
            if damaging or shot is not None or halt:
                self.rockets.append(
                    Rocket(
                        shooter, shooter.team, origin, direction, speed, lifetime, direct, blast, shot,
                        last_tick=self.tick if npc else None,
                        airburst=(state.instance.graph.index, node.state) in AIRBURST_VOLLEYS,
                        absorbable=grasp_eligible and state.instance.graph.index != 0x1BBB,
                        gravity=gravity, velocity=velocity,
                        accretion_source=(shooter.body, state.instance.id) if motion is not None else None,
                        owner_body=_source_entity(shooter) if body_scoped else None,
                        halt_instance=state.instance.id if halt else None,
                        reflectable=reflectable,
                        body_radius=body_radius, wall_radius=wall_radius, bounces=bounces,
                        bounce_normal=bounce_normal, bounce_tangent=bounce_tangent,
                        definition=projectile,
                        penetrated=set() if fire_strike or target_limit > 1 else None,
                        target_limit=target_limit,
                    )
                )
                rocket = self.rockets[-1]
                spread = (enforcer.pellet_directions(shooter, state, direction) if npc
                          else genji.shuriken_fan(shooter, state, direction, command.aim()[0]))
                self.rockets.extend(replace(rocket, direction=pellet, shot=None) for pellet in spread)
            return
        if direct is None or direct.amount <= 0.0:
            return
        reach = field("m_FEC435C6", RANGE) or RANGE
        # The input supplies this simulation frame's aim and view time. Notices use the volley clock.
        view_tick = self.tick
        if command is not None:
            view_tick = command.frame - command.view_delay(shooter.quanta) * 1e6 / shooter.quanta
        # Ordinary hitscan bullets declare an entity without projectile speed.
        absorbable = grasp_eligible and entity_def is not None and node.field("m_projectileSpeed") is None
        if (state.instance.graph.index, node.state) in ((0x0019, 6), (0x050A, 3)):
            directions = ring_directions(state, direction)
        elif tracer_pistol.active(state):
            count = max(0, int(field("m_numProjectilesPerShot", 1)))
            directions = tracer_pistol.rays(state, number, time, activation, direction, count)
        else:
            directions = (direction,)
        for ray in directions:
            self._hitscan(shooter, origin, ray, direct, reach, view_tick, shot_frame, absorbable=absorbable)

    def _hitscan(self, shooter, origin, direction, direct, reach, view_tick, shot_frame, *, absorbable):
        """Resolve one ray so each pellet meets cover, defenses and damage pools independently."""
        best = None
        for target in self._enemies(shooter):
            shown = self.seen(target, view_tick)
            if shown is None:
                continue
            a, b = shown.segment()
            t = ray_capsule(origin, direction, a, b, shown.radius)
            if t is not None and t <= reach and (best is None or t < best[0]):
                best = (t, target, shown)
        stopped = None
        if self.barriers:
            limit = reach if best is None else best[0]
            wall = ray_world(self._collision(), origin, direction, limit)
            stopped = sigma.barrier_intersection(
                self, shooter, origin, direction, limit if wall is None else wall,
            )
        if absorbable:
            limit = reach if best is None else best[0]
            if stopped is not None:
                limit = min(limit, stopped[0])
            wall = ray_world(self._collision(), origin, direction, limit)
            intercepted = sigma.grasp_intersection(
                self, shooter, origin, direction, limit if wall is None else wall,
            )
            if intercepted is not None:
                distance, grasp = intercepted
                sigma.absorbed(grasp, direct.amount * direct.scale(distance))
                return
        if stopped is not None:
            distance, barrier = stopped
            sigma.damage_barrier(self, barrier, direct.amount * direct.scale(distance))
            return
        if best is None:
            return
        t, target, shown = best
        wall = ray_world(self._collision(), origin, direction, t)
        if wall is not None and wall < t:
            return
        hit_y = origin[1] + direction[1] * t
        critical = hit_y >= shown.feet[1] + shown.eye - HEAD_BELOW
        amount = direct.amount * direct.scale(t) * (direct.critical if critical else 1.0)
        blocked = sigma.particle_barrier(target.owner) is not None
        self._damage(
            shooter, target, amount, critical and direct.critical > 1.0, direct.loadout, shot_frame,
            world_tick=self.tick if isinstance(shooter, bots.Bot) else None, wakes_sleep=direct.wakes_sleep,
        )
        if not blocked:
            self._hit_effect(target, direct.effect)

    def _fly(self, rocket: Rocket) -> None:
        """Sweep elapsed world time against the current bodies and static collision."""
        if rocket.halt_at is not None:
            if self.tick >= rocket.halt_at:
                self._landed(rocket)
                sigma.halt_pull(self, rocket)
            return
        # Player input can arrive immediately before the scheduler accounts for a gap.
        elapsed = 1 if rocket.last_tick is None else self.tick - rocket.last_tick
        if elapsed <= 0:
            return
        rocket.last_tick = self.tick
        if rocket.velocity is not None:
            for _ in range(elapsed):
                if rocket not in self.rockets:
                    break
                self._fly_step(rocket, min(TICK, rocket.left))
            return
        self._fly_step(rocket, min(elapsed * TICK, rocket.left))

    def _fly_step(self, rocket: Rocket, duration: float) -> None:
        if duration <= 0.0:
            self._landed(rocket)
            if rocket.airburst:
                self._explode(rocket, rocket.position)
            return
        # Bound degenerate corner contacts without resetting projectile age.
        for _ in range(8):
            previous_velocity = rocket.velocity
            if previous_velocity is not None:
                # D71ACD-D71ADE predicts gravity for this substep's sweep.
                vx, vy, vz = previous_velocity
                vy = expr.f32(vy - expr.f32(expr.f32(rocket.gravity) * expr.f32(duration)))
                rocket.velocity = (vx, vy, vz)
                rocket.speed = math.sqrt(vx * vx + vy * vy + vz * vz)
                if not rocket.speed:
                    rocket.left -= duration
                    return
                rocket.direction = tuple(axis / rocket.speed for axis in rocket.velocity)
            remaining = self._sweep_projectile(rocket, duration, previous_velocity)
            if remaining is None or remaining <= 0.0:
                return
            duration = remaining
        rocket.left -= duration
        rocket.flown += duration
        if rocket.left <= 0.0:
            self._landed(rocket)
            if rocket.airburst:
                self._explode(rocket, rocket.position)

    def _matrix_hit(self, rocket: Rocket, reach: float) -> float | None:
        if not dva_matrix.absorbable(rocket.definition):
            return None
        best = None
        for target in self._enemies(rocket.shooter):
            player = target.owner
            if isinstance(player, bots.Bot) or player.hero.guid != dva_matrix.HERO or self.is_stunned(player):
                continue
            distance = dva_matrix.matrix_distance(
                player, rocket.position, rocket.direction, reach, self._eye(player),
                aim(player.mover.yaw, player.mover.pitch),
            )
            if distance is not None and (best is None or distance < best):
                best = distance
        return best

    def _sweep_projectile(self, rocket: Rocket, duration: float, previous_velocity: tuple | None) -> float | None:
        """Sweep one segment and return unused step time after a world bounce."""
        # Sweep the remaining flight even when the range or lifetime ends inside this tick.
        step = rocket.speed * duration
        best = None
        targets = () if rocket.halt_instance is not None else self._enemies(rocket.shooter)
        penetrating = rocket.penetrated is not None
        penetrates_barriers = penetrating and rocket.target_limit == 1
        collision = self._collision()
        if not penetrating:
            for target in targets:
                a, b = target.segment()
                t = ray_capsule(rocket.position, rocket.direction, a, b, target.radius + rocket.body_radius)
                if t is not None and t <= step and (best is None or t < best[0]):
                    if rocket.accretion_source is not None:
                        # Check cover to the real capsule from the swept contact.
                        point = _along(rocket.position, rocket.direction, t)
                        if not capsule_visible(collision, point, a, b, target.radius):
                            continue
                        if self.barriers and not self._blast_passes_barriers(rocket.shooter, point, target, None):
                            continue
                    best = (t, target)
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
        elif rocket.definition == torbjorn_turret.THROW:
            hit = collision.cast_ray(rocket.position, rocket.direction, step, MOVER)
            wall = hit.distance if hit is not None else None
            normal = hit.normal if hit is not None else None
        else:
            wall = ray_world(collision, rocket.position, rocket.direction, step)
        stopped = None
        if self.barriers and rocket.halt_instance is None and not penetrates_barriers:
            limit = step if best is None else best[0]
            if wall is not None:
                limit = min(limit, wall)
            stopped = sigma.barrier_intersection(
                self, rocket.shooter, rocket.position, rocket.direction, limit,
            )
        limit = step if best is None else best[0]
        if wall is not None:
            limit = min(limit, wall)
        if stopped is not None:
            limit = min(limit, stopped[0])
        matrix = self._matrix_hit(rocket, limit)
        if matrix is not None:
            limit = matrix
        reflected = None
        if rocket.reflectable:
            reflected = sigma.deflect_intersection(
                self, rocket.shooter, rocket.position, rocket.direction, limit,
            )
            if reflected is not None:
                limit = reflected[0]
        intercepted = None
        if rocket.absorbable:
            intercepted = sigma.grasp_intersection(
                self, rocket.shooter, rocket.position, rocket.direction, limit,
            )
        if penetrating:
            if intercepted is not None:
                limit = intercepted[0]
            stops = any(hit is not None for hit in (intercepted, reflected, matrix, wall, stopped))
            contact = self._penetrating_contacts(rocket, targets, limit, stops=stops)
            if contact is not None:
                rocket.flown += contact / rocket.speed
                self._landed(rocket)
                return
        if intercepted is not None:
            distance, grasp = intercepted
            amount = sum(max(0.0, hit.amount) for hit in (rocket.direct, rocket.blast) if hit is not None)
            sigma.absorbed(grasp, amount)
            rocket.flown += distance / rocket.speed
            if penetrates_barriers:
                self._absorb_projectile(rocket)
            else:
                self._landed(rocket)
            return
        if reflected is not None:
            distance, deflector, direction = reflected
            point = _along(rocket.position, rocket.direction, distance)
            elapsed = distance / rocket.speed
            rocket.flown += elapsed
            rocket.left -= elapsed
            if rocket.shot is not None:
                self.projectiles.flown(rocket.shot, rocket.flown)
                rocket.shot = None
            rocket.shooter, rocket.team = deflector, deflector.team
            rocket.owner_body = deflector.body
            rocket.position, rocket.direction = _along(point, direction, BLAST_SURFACE_OFFSET), direction
            if rocket.velocity is not None:
                rocket.velocity = tuple(expr.f32(rocket.speed * axis) for axis in direction)
            return
        if matrix is not None:
            self._absorb_projectile(rocket)
            return
        if stopped is not None:
            distance, barrier = stopped
            point = _along(rocket.position, rocket.direction, distance)
            rocket.flown += distance / rocket.speed
            self._landed(rocket)
            blast = max(0.0, rocket.blast.amount) if rocket.blast else 0.0
            amount = max(0.0, rocket.impact_damage()) + blast
            sigma.damage_barrier(self, barrier, amount)
            self._explode(rocket, point, stopped_by=barrier)
            return
        if wall is not None and (best is None or wall < best[0]):
            if rocket.halt_instance is not None:
                travel = max(0.0, wall - BLAST_SURFACE_OFFSET)
                rocket.position = _along(rocket.position, rocket.direction, travel)
                rocket.halt_at = self.tick + math.ceil(0.4 / TICK)
                return
            if normal is not None and rocket.definition in molten_core.PROJECTILES:
                travel = wall / rocket.speed
                centre = _along(rocket.position, rocket.direction, wall)
                if normal[1] >= molten_core.GROUND_NORMAL:
                    rocket.flown += travel
                    self._landed(rocket)
                    molten_core.land(self, rocket, _along(centre, normal, -rocket.wall_radius), normal)
                    return
                if rocket.bounces > 0 and previous_velocity is not None:
                    vx, vy, vz = previous_velocity
                    incoming = (vx, vy - rocket.gravity * travel, vz)
                    change = (1 + rocket.bounce_normal) * _dot(incoming, normal)
                    rocket.velocity = tuple(expr.f32(v - change * n) for v, n in zip(incoming, normal, strict=True))
                    rocket.position = _along(centre, normal, .001)
                    rocket.flown += travel
                    rocket.left -= travel
                    rocket.bounces -= 1
                    return min(duration - travel, rocket.left)
            if normal is not None and rocket.airburst:
                travel = wall / rocket.speed
                centre = _along(rocket.position, rocket.direction, wall)
                # D70627-D7065D commits only the gravity up to contact.
                assert previous_velocity is not None
                vx, vy, vz = previous_velocity
                delta = expr.f32(rocket.velocity[1] - vy)
                rocket.velocity = (vx, expr.f32(vy + expr.f32(delta * expr.f32(travel / duration))), vz)
                vn = _dot(rocket.velocity, normal)
                if vn < 0.0:
                    rocket.velocity = tuple(
                        expr.f32((axis - vn * n) * rocket.bounce_tangent - vn * n * rocket.bounce_normal)
                        for axis, n in zip(rocket.velocity, normal, strict=True)
                    )
                if vn < 0.0 or wall == 0.0:
                    # D78961 normalizes the stock Hypersphere response.
                    # D70457 advances it with the default motion multiplier of 1.
                    squared = _dot(rocket.velocity, rocket.velocity)
                    if squared >= 1.1754943508222875e-36:
                        length = math.sqrt(squared)
                        rocket.direction = tuple(axis / length for axis in rocket.velocity)
                    else:
                        rocket.direction = normal
                    rocket.velocity = tuple(expr.f32(axis) for axis in rocket.direction)
                    rocket.speed = 1.0
                else:
                    rocket.speed = math.sqrt(_dot(rocket.velocity, rocket.velocity))
                    if rocket.speed:
                        rocket.direction = tuple(axis / rocket.speed for axis in rocket.velocity)
                rocket.bounces -= 1
                rocket.position = _along(centre, normal, 0.001)
                rocket.flown += travel
                rocket.left -= travel
                if rocket.bounces < 0:
                    # D75110 dispatches the final bounce and retires the sphere.
                    self._landed(rocket)
                    return None
                if rocket.left <= 0.0:
                    self._landed(rocket)
                    if rocket.airburst:
                        self._explode(rocket, rocket.position)
                    return None
                return min(duration - travel, rocket.left)
            best = (wall, None)
        if best is None:
            rocket.position = _along(rocket.position, rocket.direction, step)
            rocket.flown += duration
            rocket.left -= duration
            if rocket.left <= 0.0:
                self._landed(rocket)
                if rocket.airburst:
                    self._explode(rocket, rocket.position)
            return
        t, struck = best
        rocket.flown += t / rocket.speed
        self._landed(rocket)
        point = _along(rocket.position, rocket.direction, t)
        if struck is None and normal is not None and rocket.definition == torbjorn_turret.THROW:
            torbjorn_turret.land(self, rocket, point, normal)
        bubble = sigma.particle_barrier(struck.owner) if struck is not None else None
        if bubble is not None:
            blast = max(0.0, rocket.blast.amount) if rocket.blast else 0.0
            amount = max(0.0, rocket.impact_damage()) + blast
            sigma.damage_particle_barrier(
                bubble, self.vitals_of(struck), amount, _source_entity(rocket.shooter),
            )
            self._explode(rocket, point, excluded=struck.entity)
            return
        if struck is not None and rocket.direct is not None and rocket.direct.amount > 0.0:
            previous_health = self.vitals_of(struck).total if rocket.accretion_source is not None else None
            self._damage(
                rocket.shooter, struck, rocket.impact_damage(), False, rocket.direct.loadout, self.tick,
                world_tick=self.tick, wakes_sleep=rocket.direct.wakes_sleep,
            )
            self._hit_effect(struck, rocket.direct.effect)
            if previous_health is not None:
                sigma.accretion_hit(self, rocket, struck, previous_health - self.vitals_of(struck).total)
        self._explode(rocket, point, struck=struck)
        if struck is not None and rocket.direct is not None:
            sigma.apply_impact_impulse(self, rocket.direction, struck, rocket.direct.knockback)

    def _penetrating_contacts(self, rocket, targets, limit, *, stops):
        """Resolve crossed bodies and return the distance of a terminating body contact."""
        direct = rocket.direct
        if direct is None or direct.amount <= 0.0:
            return
        contacts = []
        if rocket.target_limit == 1:  # Fire Strike also passes through shield planes.
            for distance, barrier in sigma.barrier_intersections(
                self, rocket.shooter, rocket.position, rocket.direction, limit,
            ):
                key = (barrier.entity, None)
                if key not in rocket.penetrated and (distance < limit or not stops):
                    contacts.append((distance, key, None, barrier))
        for target in targets:
            key = (target.entity, self.replacement_ticks.get(target.entity))
            if key in rocket.penetrated:
                continue
            a, b = target.segment()
            radius = target.radius + rocket.body_radius
            distance = ray_capsule(rocket.position, rocket.direction, a, b, radius)
            if distance is not None and (distance < limit or (distance == limit and not stops)):
                contacts.append((distance, key, target, None))
        for distance, key, target, barrier in sorted(contacts, key=lambda item: item[0]):
            rocket.penetrated.add(key)
            if barrier is not None:
                sigma.damage_barrier(self, barrier, direct.amount)
            else:
                protected = sigma.particle_barrier(target.owner) is not None
                self._damage(rocket.shooter, target, direct.amount, False, direct.loadout,
                             self.tick, world_tick=self.tick, wakes_sleep=direct.wakes_sleep)
                if not protected:
                    self._hit_effect(target, direct.effect)
                if rocket.target_limit > 1 and (
                    len(rocket.penetrated) >= rocket.target_limit or protected
                    or sigma.body_protection(target.owner) == "cryo"
                ):
                    return distance
        return None

    def _absorb_projectile(self, rocket):
        self.rockets.remove(rocket)
        if rocket.shot is not None:
            self.projectiles.absorbed(rocket.shot)

    def _explode(self, rocket: Rocket, point, *, stopped_by=None, struck=None, excluded=None) -> None:
        """Resolve a single impact/airburst through the ordinary damage/death path."""
        blast = rocket.blast
        if blast is None or (blast.amount <= 0.0 and not blast.self_impulse_scale):
            return
        shooter = rocket.shooter
        targets = self._enemies(shooter) if blast.amount > 0.0 else []
        biotic = rocket.definition == BIOTIC_GRENADE and blast.effect == BIOTIC_GRENADE_EFFECT
        if biotic:
            targets.extend(target for target in self._targets() if self._biotic_friendly(shooter, target))
        if ((blast.self_damage_scale > 0 or blast.self_impulse_scale) and not isinstance(shooter, bots.Bot)
                and shooter.hero.guid == sigma.HERO and rocket.owner_body == shooter.body):
            targets.extend(target for target in self._targets() if target.owner is shooter)
        # Trace from the incoming side of a wall contact.
        source = _along(point, rocket.direction, -BLAST_SURFACE_OFFSET)
        photon_contacts = tuple(
            photon_barrier.splash_contacts(self, shooter, point, source, blast, stopped_by)
        )
        for target in targets:
            if target.entity == excluded:
                continue
            a, b = target.segment()
            distance = max(0.0, segment_distance(point, a, b) - target.radius)
            scalar = blast.splash(distance)
            self_hit = target.owner is shooter
            if (scalar > 0.0 and self._unobstructed(source, target)
                    and self._blast_passes_barriers(shooter, source, target, stopped_by)):
                if biotic and self._biotic_friendly(shooter, target):
                    self.heal_target(shooter, target, BIOTIC_HEAL * scalar)
                    self._apply_biotic(target, boost=True)
                    continue
                amount = blast.amount * scalar * (blast.self_damage_scale if self_hit else 1.0)
                bubble = sigma.particle_barrier(target.owner)
                if bubble is not None:
                    if amount > 0.0:
                        sigma.damage_particle_barrier(
                            bubble, self.vitals_of(target), amount, _source_entity(shooter),
                        )
                    continue
                if amount > 0.0:
                    if biotic and not isinstance(target.owner, bots.Bot):
                        amount = BIOTIC_DAMAGE * scalar
                    self._damage(
                        shooter, target, amount, False, blast.loadout, self.tick, world_tick=self.tick,
                        wakes_sleep=blast.wakes_sleep,
                    )
                    if biotic and not isinstance(target.owner, bots.Bot):
                        self._apply_biotic(target, boost=False)
                    else:
                        self._hit_effect(target, blast.effect)
                if (struck is None or struck.entity != target.entity
                        or rocket.direct is None or rocket.direct.knockback is None):
                    sigma.apply_impact_impulse(
                        self, rocket.direction, target, blast.knockback, point, distance,
                        strength=blast.self_impulse_scale if self_hit else 1.0,
                    )
        for wall, amount in photon_contacts:
            sigma.damage_barrier(self, wall, amount)

    def _biotic_friendly(self, shooter, target: Target) -> bool:
        return (not isinstance(target.owner, bots.Bot)
                and (target.owner is shooter
                     or (not self.match.game_map.free_for_all and target.team == shooter.team)))

    def _apply_biotic(self, target: Target, *, boost: bool) -> None:
        if self.vitals_of(target).dead or sigma.body_protection(target.owner):
            return
        end = self.tick + math.ceil(BIOTIC_SECONDS / TICK)
        blocked, boost_start, boost_end = self.biotic.get(target.entity, (0, 0, 0))
        if boost:
            if boost_end <= self.tick:
                boost_start = self.tick + math.ceil(BIOTIC_HEAL_DELAY / TICK)
            self.biotic[target.entity] = blocked, boost_start, end
        else:
            self.biotic[target.entity] = end, boost_start, boost_end

    def _blast_passes_barriers(self, shooter, source, target, stopped_by) -> bool:
        a, b = target.segment()
        nearest = (a[0], max(a[1], min(b[1], source[1])), a[2])
        delta = _sub(nearest, source)
        length = math.sqrt(_dot(delta, delta))
        reach = length - target.radius
        if reach <= EPSILON:
            return True
        direction = tuple(value / length for value in delta)
        # Reconstructed per-impact snapshot: the breaking projectile does not
        # leak its own explosion through the shield it just destroyed.
        if stopped_by is not None and stopped_by.intersection(source, direction, reach) is not None:
            return False
        return sigma.barrier_intersection(self, shooter, source, direction, reach) is None

    def _unobstructed(self, source, target: Target) -> bool:
        """Reconstructed damage LOS: static geometry to the nearest capsule surface.

        This is a conservative single-sample policy, not native multi-point
        explosion exposure. Dynamic shields/defenses have separate semantics.
        """
        a, b = target.segment()
        nearest = (a[0], max(a[1], min(b[1], source[1])), a[2])
        delta = _sub(nearest, source)
        length = math.sqrt(_dot(delta, delta))
        reach = length - target.radius
        if reach <= EPSILON:
            return True
        direction = tuple(value / length for value in delta)
        return ray_world(self._collision(), source, direction, reach) is None

    def _landed(self, rocket: Rocket) -> None:
        """The projectile ended (hit, range, lifetime): the predicted one's release follows."""
        if rocket in self.rockets:
            self.rockets.remove(rocket)
        if rocket.shot is not None:
            self.projectiles.flown(rocket.shot, rocket.flown)
        instance = sigma.halt_owner(rocket)
        if instance is not None:
            instance.run_node(instance.graph.node(41), None)

    def heal_area(self, healer, centre, radius: float, amount: float) -> None:
        """Heal the healer and his teammates (everyone's own team in a free-for-all is his alone) whose body
        is within `radius` of `centre` (unverified: to the body's capsule)."""
        for target in self._targets():
            ally = not self.match.game_map.free_for_all and target.team == healer.team
            if target.owner is not healer and not ally:
                continue
            if self.vitals_of(target).dead:
                continue
            a, b = target.segment()
            if segment_distance(centre, a, b) - target.radius > radius:
                continue
            self.heal_target(healer, target, amount)

    def heal_target(self, healer, target: Target, amount: float, *, credit: bool = True) -> None:
        vitals = self.vitals_of(target)
        blocked, boost_start, boost_end = self.biotic.get(target.entity, (0, 0, 0))
        if vitals.dead or blocked > self.tick:
            return
        if boost_start <= self.tick < boost_end:
            amount *= BIOTIC_HEAL_SCALE
        given = vitals.heal(amount)
        if given > 0.0:
            if credit:
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
        *, world_tick: int | None = None, beam: bool = False, source_body: int | None = None,
        armour_reduction: bool = True, wakes_sleep: bool = True,
    ) -> None:
        vitals = self.vitals_of(target)
        if vitals.dead or amount <= 0.0 or sigma.body_protection(target.owner):
            return
        source = _source_entity(shooter) if source_body is None else source_body
        bubble = sigma.particle_barrier(target.owner)
        if bubble is not None:
            sigma.damage_particle_barrier(bubble, vitals, amount, source)
            return
        fortify = sigma.fortify(target.owner)
        if fortify is not None:
            modifier = fortify.evaluate(fortify.node.field("m_50530EAB"), watch=False)
            amount *= 1.0 + expr.to_float(modifier)
        charge_script = sigma.damage_charge_script(self, shooter, target, loadout)
        base_before = sum(vitals.now.values()) if charge_script is not None else 0.0
        dealt = vitals.damage(amount, beam=beam, armour_reduction=armour_reduction)
        if dealt <= 0.0:
            return
        damage_tick = self.match.tick if world_tick is None else world_tick
        killed = vitals.now[HEALTH_POOL] <= EPSILON
        if killed:
            vitals.now[HEALTH_POOL] = 0.0
            vitals.died_at = self.now
            vitals.death_tick = damage_tick
            vitals.killer = source
        else:
            sigma.delay_base_shields(target.owner, vitals, damage_tick, TICK)
            if wakes_sleep and (sleep := self.sleeps.get(target.entity)) is not None:
                start, deadline = sleep
                if (damage_tick - start) * TICK > SLEEP_WAKE_SECONDS:
                    wake = damage_tick + math.ceil(SLEEP_WAKE_SECONDS / TICK)
                    if wake < deadline:
                        self.sleeps[target.entity] = start, wake
            if loadout == mei_frost.SPRAY_LOADOUT:
                self._apply_mei_frost(shooter, target)
        if charge_script is not None:
            charge_script.award_ult_charge(base_before - sum(vitals.now.values()))
        if not isinstance(shooter, bots.Bot) and shooter is not target.owner:
            flags = (CRITICAL if critical else 0) | (KILLING if killed else 0)
            self._tell_hit(shooter, target, dealt, flags, frame)
            shooter.stats.add(stats.HERO_DAMAGE, dealt)
        victim = target.owner
        if killed and isinstance(victim, bots.Bot):
            # The NPC death script owns its terminal health change and death event ordering.
            self._bot_died(shooter, victim, critical, loadout, vitals.death_tick, source)
            return
        self._sync_body_health(victim, vitals)
        if killed:
            self._died(shooter, victim, critical, loadout, source)

    def _tell_hit(self, shooter, target: Target, dealt: float, flags: int, frame: int) -> None:
        """25001 to the shooter: the hit marker and the target's health bar (frame: the shot's)."""
        value = {
            "+0x78": {"+0x0": {"+0x0": target.entity}, "+0x4": max(1, round(dealt)), "+0x8": flags},
            "+0x84": frame & 0xFFFFFFFF,
        }
        self._queue_notice(shooter, target.owner, target.entity, HIT, value)

    def _kill_notice(self, killer, victim, source: int) -> None:
        """25005 to the killer: "ELIMINATED: <name>" (1126.07C; +0x89 assist would be 01F0.07C, +0x88 a
        score shown when above 0). Its handler (0x7FF7895D2B50) reads only +0x84, +0x88 and +0x89."""
        entity = _source_entity(victim)
        notice = {
            "+0x78": 0,
            "+0x80": {"+0x0": source},
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
    def _death_parameters(source: int, critical: bool, loadout) -> dict:
        return {
            KILLER: expr.Entity(source),
            CRIT: bool(critical),
            LOADOUT: expr.Asset(loadout) if loadout else None,
            ASSISTS: (),
        }

    def _bot_died(self, killer, bot: bots.Bot, critical: bool, loadout, tick: int, source: int) -> None:
        from ow174.game import sniper

        self.clear_pins(bot.entity)
        self.stuns.pop(bot.entity, None)
        sniper.stop(bot)
        bot.died(tick)
        if not bot.retain_corpse:
            self.respawns.append((self.now + BOT_RESPAWN, bot))
        if not isinstance(killer, bots.Bot):
            killer.stats.add(stats.ELIMINATIONS)
            self._kill_notice(killer, bot, source)
        log.info("[game] %s: %s killed %s", self.match.label(), killer.name, bot.name)
        script = bot.body_script
        if script is None:
            return
        try:
            params = self._death_parameters(source, critical, loadout)
            if isinstance(script, BotScript):
                tick = max(tick, script.frame)
            event = script.die(tick, params, source)
            if isinstance(script, BotScript):
                self._death_volleys(bot, script.component, snapshot=event or script.latest_live)
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
        self.clear_frost(victim)
        """A player died: the notices, his body script's "died" (the kill feed line), and the respawn."""
        symmetra_sentry.release_target(self, victim.body)
        sigma.interrupt_flux_lift(self, victim)
        for barrier in tuple(self.barriers.values()):
            if barrier.owner_body == victim.body and not barrier.persists_after_death:
                sigma.retire_barrier(self, barrier)
        self.tesla_frames.pop(victim.body, None)
        self.winston_jump.retire(victim.body)
        if victim.body_script is not None:
            self.winston_primal.sync_health(victim, victim.body_script, ending=True)
        self.winston_primal.retire(victim.body)
        self.grasp_shields.pop(victim.body, None)
        self.grasp_grants.pop(victim.body, None)
        self.genji_strikes.pop(victim.body, None)
        self.booster_contacts.pop(victim.body, None)
        self.genji_blades.pop(victim.body, None)
        self.clear_pins(victim.body)
        self.stuns.pop(victim.body, None)
        self.sleeps.pop(victim.body, None)
        self.biotic.pop(victim.body, None)
        self.halt_pulls.pop(victim.body, None)
        self.halt_slows.pop(victim.body, None)
        label = self.match.label()
        log.info("[game] %s: %s killed %s", label, killer.name, victim.name)
        victim.stats.add(stats.DEATHS)
        bot_killer = isinstance(killer, bots.Bot)
        if killer is not victim and not bot_killer:
            killer.stats.add(stats.ELIMINATIONS)
            self._kill_notice(killer, victim, source)
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
                    self._death_volleys(victim, component)
            except Exception:
                log.exception("[game] %s: %s's body script failed on %X", label, victim.name, DIED)
        self.respawns.append((self.now + respawn_seconds(self.match), victim))

    def _death_volleys(self, owner, component, *, snapshot=None) -> None:
        if self.match.collision_world is None:
            return
        shots = [shot for shot in component.shots if shot[0].fires_on_death]
        if not shots:
            return
        component.shots[:] = [shot for shot in component.shots if not shot[0].fires_on_death]
        pose = None if isinstance(owner, bots.Bot) else Command(
            self.tick, yaw=owner.mover.yaw, pitch=owner.mover.pitch,
        )
        for state, number, time, activation in shots:
            self._shoot(owner, pose, state, number, time, activation=activation, snapshot=snapshot)

    def _respawn(self, who) -> None:
        if isinstance(who, bots.Bot):
            self._respawn_bot(who)
            return
        player = who
        if player.gone or not player.has_body or not self.is_dead(player):
            return
        script = player.body_script
        charge = 0.0
        if player.hero.guid == sigma.HERO and script is not None and script.entity == player.body:
            charge = expr.to_float(script.component.entity_var(ULT_CHARGE).value())
        if not self.match.switch_hero(player, player.hero):
            self.respawns.append((self.now + RESPAWN_RETRY, player))
            return
        replacement = player.body_script
        if charge > 0 and replacement is not None and replacement.entity == player.body:
            cost = expr.to_float(replacement.component.entity_var(ULT_COST).value())
            replacement.set_ult_charge(min(cost, charge))
        self.voice.spawned(player, self.now)
        log.info("[game] %s: %s respawned", self.match.label(), player.name)

    def _catch_up_respawns_before_sentry_replay(self, now, tick, first) -> None:
        """Process omitted player respawn timers without replaying omitted attacks."""
        earliest = self.tick + 1
        last_omitted = now - (tick - first + 1) * TICK
        # Keep the omitted work bounded if body allocation fails repeatedly.
        for _ in range(COMBAT_CATCH_UP):
            deadline = min((when for when, player in self.respawns
                            if not isinstance(player, bots.Bot)), default=None)
            if deadline is None or deadline > last_omitted + EPSILON:
                return
            at = max(earliest, tick - math.floor((now - deadline + EPSILON) / TICK))
            self.now, self.tick = now - (tick - at) * TICK, at
            self._respawn_players_during_sentry_replay()

    def _respawn_players_during_sentry_replay(self) -> None:
        """Replace due player bodies on this tick, after any retained contacts."""
        due = [(when, player) for when, player in self.respawns
               if when <= self.now + EPSILON and not isinstance(player, bots.Bot)]
        if not due:
            return
        # Preserve the dead body's final health and use this frame for the
        # replacement's history, initial body script and possible retry.
        endpoint = self.match.tick
        self.match.tick = self.tick
        try:
            self._send_health()
            for entry in due:
                self.respawns.remove(entry)
                self._respawn(entry[1])
        finally:
            self.match.tick = endpoint

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
