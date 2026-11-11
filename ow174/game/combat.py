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
  command's aim from the eye (authored STUFirstPersonComponent m_E29993A5/m_816B830F where cataloged;
  generic offsets otherwise, animated eye/muzzle equivalence unverified), or a projectile flown each tick.
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
from dataclasses import dataclass

from ow174.game import steel_trap
from ow174.game import (
    ana_nano,
    baptiste_field,
    barrage_motion,
    barrier_pet,
    brigitte_bash,
    brigitte_whip,
    bots,
    defenses,
    dva_matrix,
    heavy_charge,
    hook,
    mei_blizzard,
    mei_frost,
    mei_wall,
    observers,
    orisa_deployable,
    pools,
    projectiles,
    sigma_accretion,
    sigma_barrier,
    sigma_grasp,
    sombra_hack,
    sound_barrier,
    soundwave,
    stats,
    voicelines,
    winston_barrier,
    world,
    zarya_barrier,
    zenyatta_discord,
)
from ow174.game.bits import BitWriter
from ow174.game.collision import MOVER, TEAM0, TEAM1, World
from ow174.game.commands import Command
from ow174.game.mover import mover_data, turned_pitch
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
#104319 FirstPerson standing/crouched scalars in the current eye-origin model.
EYE_STANDING = {0x0400000000000032: 1.35, 0x04000000000002A3: 1.425,
                0x0400000000001B1B: 1.58}
EYE_CROUCHED = {0x04000000000003CF: 1.085, 0x0400000000000032: .945,
                0x04000000000002A3: .9975, 0x0400000000001B1B: 1.2}
EYE_CROUCHED_DEFAULT = 1.15
HEAD_BELOW = 0.2  # m under the eye where the head starts (unverified)
HISTORY = 64  # ticks of body positions kept for the shots' view time (about 1 s; unverified: retail's limit)
COMBAT_CATCH_UP = 64  # at most about a second of retained combat frames per update
RANGE = 200.0  # m a hitscan shot reaches when its volley names none (unverified)
RESPAWN = 10.0  # s a hero waits in the PvP modes (0F4A and 0EC2 send 06FD.025 {10}: DATA)
PRACTICE_RESPAWN = 3.0  # s in the Practice Range (13C0 sends 06FD.025 {3}: DATA)
BOT_RESPAWN = 3.0  # s a training bot lies dead (unverified: OW2 made two killed bots again after 2.98 s)
RESPAWN_RETRY = 0.25  # s between attempts when all player body IDs are still reserved
MELEE = {0x0043: (4, 5, 1.5)}  # graph -> (ModifyHealth state, TrackTargets state, forward offset)
ARMOUR_BLOCK = 5.0  # damage armour stops per hit (unverified: OW1's rule; the client has no damage code)
TICK = 0.016
EPSILON = 1e-9
BLAST_SURFACE_OFFSET = 0.001  # m: keep a wall impact's visibility ray on the incoming side (reconstructed)
FLASHBANG_EFFECT = 0x04A0000000000079  # .053/0079 ->0228 ->224E
SLEEP_DART_EFFECT = 0x04A000000000026F  # 0BDD/state6 -> .053/026F -> 0BDE
SLEEP_DART_SECONDS = 5.0  # reconstructed local control window
BIOTIC_GRENADE_EFFECT = 0x04A0000000000267  # .053/0267 -> 0C71 -> 0B99/0C72
BIOTIC_GRENADE_SECONDS = 4.0
BIOTIC_GRENADE_HEAL = 100.0
BIOTIC_GRENADE_BOOST = 0.5
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
    """Authored offsets for cataloged bodies in the server's current eye model."""
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


def source_blast_geometry(feet, height: float, radius: float, impact, normal) -> tuple[float, tuple]:
    """Impulse distance and direction from D6D6C6, with an upright mover anchor."""
    x, y, z = feet
    half = height * 0.5
    center = (x, y + half, z)
    delta = _sub(center, impact)
    distance = math.hypot(*delta)
    reach = max(0.0, distance - radius)
    if distance <= EPSILON:
        return reach, (0.0, 0.0, 0.0)
    direction = tuple(axis / distance for axis in delta)
    if half * (4.0 / 3.0) <= 0.004 or radius <= 0.0:
        return reach, direction

    lower, upper = center[1] - half / 3.0, center[1] + half
    closest = (x, max(lower, min(upper, impact[1])), z)
    if math.dist(closest, impact) < radius:
        dx, dz = impact[0] - x, impact[2] - z
        length = math.hypot(dx, dz)
        escape = (dx / length, 0.0, dz / length) if length > EPSILON else normal
        impact = _along((x, impact[1], z), escape, radius)
    raised = (x, max(lower, min(upper, impact[1])) + 0.2, z)
    offset = _sub(raised, impact)
    length = math.hypot(*offset)
    if length > EPSILON:
        direction = tuple(axis / length for axis in offset)
    return reach, direction


def capsule_visible(collision: World | None, origin, a, b, radius: float, *, walls=None) -> bool:
    """Reconstructed visibility to the nearest capsule surface, including optional Ice Wall."""
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
    return ((hit is None or hit + EPSILON >= reach)
            and (walls is None or walls.cast(origin, direction, reach - EPSILON) is None))


def ray_world(
    collision: World | None, origin, direction, length: float, category: int = MOVER,
) -> float | None:
    """The distance to the first static hit, for callers that only need occlusion."""
    hit = collision.cast_ray(origin, direction, length, category) if collision is not None else None
    return hit.distance if hit is not None else None


def deployable_contact(barriers, origin, direction, reach):
    nearest = None
    for barrier in barriers:
        distance = barrier.ray(origin, direction, reach)
        if distance is not None and distance <= reach and (nearest is None or distance < nearest[0]):
            nearest = distance, barrier
    return nearest


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
        self.sleep_until = 0  # exclusive world-tick deadline
        self.healing_blocks: list[tuple[int, int]] = []  # half-open world-tick intervals
        self.healing_boosts: list[tuple[int, int]] = []

    def grenade_interval(self, intervals, tick):
        intervals[:] = [(start, end) for start, end in intervals if end > tick - COMBAT_CATCH_UP]
        start = intervals.pop()[0] if intervals and tick <= intervals[-1][1] else tick
        intervals.append((start, tick + math.ceil(BIOTIC_GRENADE_SECONDS / TICK)))

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

    def damage(self, amount: float, minimum_health: float = 0.0) -> float:
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
            available = self.now[kind] if kind != HEALTH_POOL else max(0.0, self.now[kind] - minimum_health)
            take = min(left, available)
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
    horizontal_impulse: float = 0.0  # reconstructed ModifyHealth knockback fields
    vertical_impulse: float = 0.0
    vertical_impulse_minimum: float | None = None
    impulse_rings: tuple = ()  # (radius, horizontal scalar, vertical scalar)
    self_scale: float = 0.0
    source_impulse_scale: float = 0.0
    source_impulse_ignores_los: bool = False
    effect: int | None = None  # ModifyHealth's authored hit-effect resource
    tags: tuple = ()

    def impulse_scales(self, distance: float) -> tuple[float, float]:
        """Select and interpolate the two ModifyHealth knockback channels."""
        if not self.impulse_rings:
            return 1.0, 1.0
        previous = None
        for radius, horizontal, vertical in self.impulse_rings:
            if distance < radius:
                if previous is None:
                    return horizontal, vertical
                inner, inner_horizontal, inner_vertical = previous
                share = (distance - inner) / max(0.0001, radius - inner)
                return (inner_horizontal + (horizontal - inner_horizontal) * share,
                        inner_vertical + (vertical - inner_vertical) * share)
            previous = radius, horizontal, vertical
        return 0.0, 0.0

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


def damage_tags(state, modify) -> tuple:
    return tuple(value.guid for item in modify.get("m_148F3152") or () if isinstance(item, dict)
                 and isinstance(value := state.evaluate(item.get("m_EE3055BD"), watch=False), expr.Asset))


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
    rock_blast = cfg.get("$") == "STU_4AA6CA44" and loadout == expr.Asset(sigma_accretion.LOADOUT)
    impulse_rings = []
    for ring in modify.get("m_knockbackRings") or []:
        if not isinstance(ring, dict):
            continue
        def ring_value(name, ring=ring):
            field = ring.get(name)
            return _number(state.evaluate(field, watch=False), 0.0) if field is not None else 0.0

        impulse_rings.append((ring_value("m_radius"), ring_value("m_579D7CFF"), ring_value("m_10399AE7")))

    def modify_value(name):
        field = modify.get(name)
        return _number(state.evaluate(field, watch=False), 0.0) if field is not None else 0.0

    return HitDef(
        amount=amount,
        loadout=loadout.guid if isinstance(loadout, expr.Asset) else None,
        falloff_start=value("m_EAD7F104"),
        falloff_end=value("m_157E5BC5"),
        falloff_floor=value("m_72A81154"),
        critical=value("m_A872C70E", 1.0),
        rings=tuple(sorted(rings)),
        horizontal_impulse=modify_value("m_E33EFEE1"),
        vertical_impulse=modify_value("m_10399AE7"),
        vertical_impulse_minimum=(
            modify_value("m_583C208B") if modify.get("m_583C208B") is not None else None
        ),
        impulse_rings=tuple(impulse_rings),
        self_scale=value("m_8A3E3883", float(rock_blast)),
        source_impulse_scale=value("m_2FE97560", float(rock_blast)) if cfg.get("m_49F60FB8") else 0.0,
        source_impulse_ignores_los=bool(cfg.get("m_2A970629")),
        effect=effect.guid if isinstance(effect, expr.Asset) else None,
        tags=damage_tags(state, modify),
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
    matrix_eligible: bool = False
    reflected_by: int | None = None
    motion: tuple = ()
    motion_phase_offset: float = 0.0
    range_limit: float = 0.0
    distance_flown: float = 0.0
    deflect_lifetime: float | None = None
    barrage_source: tuple[object, runtime.State, int] | None = None  # original owner, volley, shot
    contact_extent: float = 0.0
    source_body: int | None = None
    accretion_source: runtime.Instance | None = None
    whip_source: runtime.State | None = None
    gravity: float = 0.0
    velocity: tuple | None = None


class Combat:
    """The match's health, hits, deaths and respawns."""

    def __init__(self, match) -> None:
        self.match = match
        self.steel_traps = {}
        self.vitals: dict[int, Vitals] = {}  # body or bot entity -> its health
        self.history: dict[int, deque] = {}  # entity -> (tick, feet, height, eye) of the last HISTORY ticks
        self.replacement_ticks: dict[int, int] = {}  # replacement body -> authoritative world tick
        self.rockets: list[Rocket] = []
        self.respawns: list[tuple[float, object]] = []  # (when, a player or a bot)
        self.next_bot = bots.BOT_ENTITY + len(match.bots)
        self.now = 0.0
        self.tick = 0
        self.melee_seen: dict[int, dict[tuple[int, int], int]] = {}  # body -> counters by instance/state
        self.stuns: dict[int, int] = {}  # body identity -> exclusive world-tick deadline
        self.heavy_pins = {}
        self.heavy_grazes = {}
        self.heavy_impact_requests = {}
        self.pins: dict[int, int] = {}  # manual Assassin source body -> controlled victim body
        self.barrier_pets: dict[int, runtime.Component] = {}
        self.barrier_geometry = barrier_pet.configured_geometry()
        self.winston_bubbles: dict[int, winston_barrier.Bubble] = {}
        self.orisa_geometry = orisa_deployable.configured_geometry()
        self.orisa_flights: list[orisa_deployable.Flight] = []
        self.orisa_shields: dict[int, orisa_deployable.Shield] = {}
        self.sigma_geometry = sigma_barrier.configured_geometry()
        self.brigitte_geometry = brigitte_bash.configured_geometry()
        self.brigitte_barriers: dict[int, brigitte_bash.Barrier] = {}
        self.sigma_barriers: dict[int, sigma_barrier.Barrier] = {}
        self.grasp_shields: dict[int, sigma_grasp.ShieldDecay] = {}
        self.grasp_grants: dict[int, tuple[int, int]] = {}
        self.transcendence_seen: dict[int, tuple[int, int]] = {}
        self.sound_barriers = sound_barrier.SoundBarriers(self)
        self.baptiste_flights: list[orisa_deployable.Flight] = []
        self.baptiste_fields: dict[int, baptiste_field.Field] = {}
        self.hacks: dict[int, list[sombra_hack.Hack]] = {}
        self.frost: dict[int, mei_frost.Frost] = {}
        self.blizzard_flights: list[mei_blizzard.Flight] = []
        self.blizzards: dict[int, mei_blizzard.Area] = {}
        self.walls = mei_wall.Walls(self)
        self.discord: dict[int, zenyatta_discord.Orb] = {}
        self.nano_casts: dict[int, int] = {}
        self.nano_buffs: dict[int, tuple[int, float, float]] = {}
        self.hooks = hook.Hooks(self)
        self.zarya_bubbles: dict[tuple[int, int], zarya_barrier.Bubble] = {}
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
        player.mover.retire_impulses()
        barrier_pet.retire(self, player.body)
        self.winston_bubbles.pop(player.body, None)
        orisa_deployable.retire(self, player.body)
        sigma_barrier.retire(self, player.body)
        self.brigitte_barriers.pop(player.body, None)
        self.grasp_shields.pop(player.body, None)
        self.grasp_grants.pop(player.body, None)
        self.transcendence_seen.pop(player.body, None)
        self.sound_barriers.retire(player.body)
        baptiste_field.retire(self, player.body)
        steel_trap.retire_body(self, player.body)
        sombra_hack.retire(self, player.body)
        self.frost.pop(player.body, None)
        mei_blizzard.retire(self, player.body)
        self.walls.retire(player.body)
        self.hooks.retire(player.body)
        zenyatta_discord.retire(self, player.body)
        ana_nano.retire(self, player.body)
        zarya_barrier.retire(self, player.body)
        self.clear_pins(player.body)
        self.vitals.pop(player.body, None)
        self.history.pop(player.body, None)
        self.replacement_ticks.pop(player.body, None)
        self.melee_seen.pop(player.body, None)
        self.stuns.pop(player.body, None)
        # The owner's leased projectiles and their observer copies retire with the old body.
        self.rockets[:] = [
            rocket for rocket in self.rockets
            if rocket.shooter is not player or (rocket.shot is None and rocket.whip_source is None)
        ]
        self.respawns[:] = [(when, who) for when, who in self.respawns if who is not player]

    def record_replacement(self, body: int) -> None:
        """Keep the creation boundary independent of the rolling pose history."""
        self.clear_pins(body)
        self.hooks.retire(body)
        self.replacement_ticks[body] = self.match.tick
        self.stuns.pop(body, None)

    def is_stunned(self, actor, *, tick: int | None = None) -> bool:
        return self.stuns.get(_source_entity(actor), 0) > (self.tick if tick is None else tick)

    def is_asleep(self, actor, *, tick: int | None = None) -> bool:
        vitals = self.vitals.get(_source_entity(actor))
        return (vitals is not None and not vitals.dead
                and vitals.sleep_until > (self.tick if tick is None else tick))

    def is_incapacitated(self, actor, *, tick: int | None = None) -> bool:
        tick = self.tick if tick is None else tick
        frost = mei_frost.sample(self, _source_entity(actor), tick)
        return (self.hooks.controlled(_source_entity(actor))
                or self.is_stunned(actor, tick=tick) or self.is_asleep(actor, tick=tick)
                or bool(frost and frost.until))

    def is_pinned(self, actor) -> bool:
        return heavy_charge.controlled(self, actor) or _source_entity(actor) in self.pins.values()

    def clear_pins(self, entity: int) -> None:
        heavy_charge.release(self, entity)
        self.heavy_grazes.pop(entity, None)
        self.heavy_impact_requests.pop(entity, None)
        for source, (owner, _) in tuple(self.heavy_pins.items()):
            if owner.body == entity:
                heavy_charge.release(self, source)
        for source, victim in list(self.pins.items()):
            if source == entity or victim == entity:
                self.pins.pop(source, None)

    def _hit_effect(self, target: Target, effect: int | None, source=None) -> None:
        """Resource-backed stun reconstruction; native immunity/presentation remain unverified."""
        if effect not in (FLASHBANG_EFFECT, SLEEP_DART_EFFECT, BIOTIC_GRENADE_EFFECT, mei_frost.SPRAY_EFFECT):
            return
        vitals = self.vitals_of(target)
        if vitals.dead or defenses.phase(target.owner)[0]:
            return
        if effect == mei_frost.SPRAY_EFFECT:
            if source is not None:
                mei_frost.hit(self, target, source)
            return
        if effect == BIOTIC_GRENADE_EFFECT:
            vitals.grenade_interval(vitals.healing_blocks, self.tick)
            return
        if defenses.fortified(target.owner):
            return
        if effect == SLEEP_DART_EFFECT:
            vitals.sleep_until = self.tick + math.ceil(SLEEP_DART_SECONDS / TICK)
        else:
            variables = graph.mode_variables(self.match.game_map.mode_guid) or {}
            duration = 1.7 if expr.truthy(variables.get(FLASHBANG_MODE_FLAG)) else 0.8
            self._apply_stun(target, duration)
            return
        if isinstance(target.owner, bots.Bot):
            from ow174.game import assassin

            assassin.cancel(self.match, target.owner)
        self._interrupt(target)

    def _apply_stun(self, target: Target, duration: float, *, extend_only: bool = False) -> None:
        if (self.vitals_of(target).dead or defenses.phase(target.owner)[0]
                or defenses.fortified(target.owner)):
            return
        deadline = self.tick + math.ceil(duration / TICK)
        previous = self.stuns.get(target.entity, 0)
        if extend_only and deadline <= previous:
            return
        self.stuns[target.entity] = max(deadline, previous)
        if isinstance(target.owner, bots.Bot):
            from ow174.game import assassin

            assassin.cancel(self.match, target.owner)
        self._interrupt(target)

    def _interrupt(self, target: Target) -> None:
        """Release held input and authored running skill scopes for local control."""
        self.hooks.cancel_owner(target.entity)
        from ow174.game.sigma_flux import interrupt_flux_lift

        interrupt_flux_lift(self, target.owner)
        script = getattr(target.owner, "body_script", None)
        if script is None:
            return
        component = script.component
        component.edges([(value, False) for value in tuple(component.held)])
        for instance in list(component.instances.values()):
            for state in list(instance.states.values()):
                if isinstance(state, nodes.Ability):
                    state.presses.clear()
                    state.stop()  # use authored exit/cooldown paths for running channels
        interrupt = getattr(script, "interrupt_control", None)
        if interrupt is not None:
            interrupt()
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
        shots, script.component.shots[:] = list(script.component.shots), []
        if self.is_dead(player) or self.is_incapacitated(player) or self.is_pinned(player):
            self.walls.cancel_preview(player)
            self._melee(player, None, script)
            return
        self.sound_barriers.observe(player)
        self.hooks.command(player, command)
        zenyatta_discord.cast(self, player, command)
        if command is not None:
            ana_nano.apply(self, player)
        if self.walls.command(player, command):
            script.component.shots.clear()
            self._melee(player, None, script)
            return
        if command is not None:
            if player.hero.guid == brigitte_bash.HERO:
                brigitte_bash.consume(self, player, command)
                brigitte_whip.launch(self, player, command, script)
            winston_barrier.deploy(self, player)
            zarya_barrier.sync(self, player)
            for state, number, time, activation in shots:
                try:
                    self._shoot(player, command, state, number, time, activation=activation)
                except Exception:
                    log.exception("[game] %s: %s's shot failed", self.match.label(), player.name)
        try:
            self._melee(player, command, script)
        except Exception:
            log.exception("[game] %s: %s's melee failed", self.match.label(), player.name)
        if player.hero.guid == soundwave.HERO:
            try:
                self._soundwave(player, command, script)
            except Exception:
                log.exception("[game] %s: %s's Soundwave failed", self.match.label(), player.name)

    def _melee(self, player, command, script) -> None:
        """Resolve 0043's health operation with the authored radius and the local aim/capsule adapter."""
        if defenses.phase(player)[0]:
            return
        for instance in script.component.instances.values():
            spec = MELEE.get(instance.graph.index)
            if spec is None:
                continue
            index, search_index, reach = spec
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
            if amount <= 0:
                continue
            search = instance.graph.state(search_index)
            if search is None:
                continue
            radius = _number(instance.evaluate(search.field("m_radius")), 0.0)
            if radius <= 0.0 or not math.isfinite(radius):
                continue
            origin = self._eye(player)
            yaw, pitch = command.aim()
            direction = aim(yaw, turned_pitch(pitch)[0])
            centre = _along(origin, direction, reach)
            wall_hits = self.walls.melee_hits(origin, centre, radius)
            view_tick = command.frame - command.view_delay(player.quanta) * 1e6 / player.quanta
            # 0043 uses the zero-result 002B override in receiver 002A.
            # Keep world sight separate from attached Zarya pool absorption.
            protected = {bubble.body: bubble for bubble in zarya_barrier.active(self)}
            tags = damage_tags(state, modify)
            non_body_damage = amount * ana_nano.outgoing(self, player.body, player.body, tags=tags)
            for target in self._enemies(player):
                shown = self.seen(target, view_tick)
                if shown is None:
                    continue
                a, b = shown.segment()
                if (segment_distance(centre, a, b) - shown.radius <= radius
                        and capsule_visible(self._collision(), origin, a, b, shown.radius, walls=self.walls)
                        and not defenses.deflects_melee(target.owner, direction)):
                    bubble = protected.get(target.entity)
                    if bubble is not None:
                        bubble.damage(non_body_damage, player.body)
                    else:
                        self._damage(player, target, amount, False, loadout, command.frame,
                                     tags=tags)
                        if target.entity in self.pins:
                            from ow174.game import assassin

                            assassin.melee_release(self.match, target.owner)
            for trap in steel_trap.hostile(self, player.body, player.team):
                a, b = trap.segment()
                if (segment_distance(centre, a, b) - trap.radius <= radius
                        and capsule_visible(self._collision(), origin, a, b, trap.radius, walls=self.walls)):
                    trap.health = max(0.0, trap.health - non_body_damage)
                    if trap.health == 0:
                        steel_trap.retire(self, trap)
            # A pillar broken by this swing still occludes the body checks above.
            for piece in wall_hits:
                self.walls.damage(piece, player, non_body_damage, command.frame)

    def _soundwave(self, player, command, script) -> None:
        """Consume 0776's health operation using its cone and the local capsule adapter."""
        if defenses.phase(player)[0]:
            return
        for instance in script.component.instances.values():
            if instance.graph.index != soundwave.GRAPH:
                continue
            state, search = instance.states.get(11), instance.states.get(10)
            if state is None or search is None:
                continue
            counters = self.melee_seen.setdefault(script.entity, {})
            key = (instance.id, state.index)
            seen = counters.get(key, 0)
            counters[key] = state.counter
            if state.counter == seen or not state.active or command is None:
                continue
            hit = hit_def(state, {"m_modifyHealth": state.node.field("m_modifyHealth")})
            radius = _number(search.evaluate(search.node.field("m_radius"), watch=False), 0.0)
            angle = _number(search.evaluate(search.node.field("m_A30B383E"), watch=False), 0.0)
            offset = search.evaluate(search.node.field("m_05BE62DD"), watch=False)
            if (hit is None or radius <= 0 or not 0 < angle <= 360
                    or not isinstance(offset, expr.Vec3)):
                continue
            impulse = state.evaluate(state.node.field("m_modifyHealth.m_1376B4DF"), watch=False)
            origin = _along(player.mover.position, tuple(offset), 1.0)
            direction = aim(*command.aim())
            threshold = math.cos(math.radians(angle / 2))
            view_tick = command.frame - command.view_delay(player.quanta) * 1e6 / player.quanta
            enemies = self._enemies(player)
            held = barrier_pet.active_colliders(self, enemies)
            deployed = (self._deployed_shields(player.body, player.team)
                        + zarya_barrier.colliders(self, player.body, player.team))
            for target in enemies:
                shown = self.seen(target, view_tick)
                if shown is None:
                    continue
                a, b = shown.segment()
                if segment_distance(origin, a, b) - shown.radius > radius:
                    continue
                x, y, z = shown.feet
                delta = _sub((x, y + shown.height / 2, z), origin)
                length = math.hypot(*delta)
                if length > EPSILON and _dot(delta, direction) < threshold * length:
                    continue
                if not self._blast_reaches(origin, shown, held, deployed):
                    continue
                self._damage(player, target, hit.amount, False, hit.loadout, command.frame, tags=hit.tags)
                if isinstance(impulse, expr.Vec3) and not self.vitals_of(target).dead:
                    self._apply_impulse(target, tuple(impulse), next_pose=True)

    def update(self, now: float, tick: int, frames: bool = True) -> None:
        """Each tick: keep where the bodies are, fly the rockets, respawn, send health that changed. The
        other players' body frames only on the ticks that send (`frames`)."""
        if self.match.collision_world is None:
            return
        retained = max(self.tick + 1, tick - COMBAT_CATCH_UP + 1)
        first = tick
        if self.tick and tick > self.tick + 1 and (self.steel_traps or any(
            bot.firing and not bot.dead and isinstance(bot.body_script, BotScript) for bot in self.match.bots
        )):
            first = retained
        for target in self._targets():
            history = self.history.setdefault(target.entity, deque(maxlen=HISTORY))
            history.append((tick, target.feet, target.height, target.eye))
        # Replay the retained combat frames using the most recent poses. Player input
        # accepted just before the scheduler caught up joins only at the endpoint.
        for at in range(first, tick + 1):
            self.now, self.tick = now - (tick - at) * TICK, at
            self.sound_barriers.update()
            orisa_deployable.update(self, at, TICK)
            sigma_barrier.update_barriers(self)
            brigitte_bash.update(self)
            baptiste_field.update(self, at, TICK)
            self.walls.update(at)
            zenyatta_discord.update(self)
            ana_nano.expire(self)
            self.hooks.update(at)
            mei_blizzard.update(self, at, TICK)
            for rocket in list(self.rockets):
                if at == tick or rocket.last_tick is not None:
                    self._fly(rocket)
            steel_trap.update(self, at)
            self._bot_weapons(at)
        self.projectiles.update(tick, first_tick=retained)
        sombra_hack.update(self)
        mei_frost.update(self)
        if retained <= tick:
            self._transcendence_heal(retained)
        for pet in self.barrier_pets.values():
            if tick > pet.frame:
                pet.run_frame(tick)
        self.match.deliver_body_messages()
        sigma_grasp.update_shields(self)
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
        for table in (self.history, self.vitals, self.replacement_ticks, self.stuns):
            for entity in [key for key in table if key not in current]:
                del table[entity]
        for source, victim in list(self.pins.items()):
            if source not in current or victim not in current:
                self.pins.pop(source, None)

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
            if defenses.phase(target.owner)[1]:
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
            _, feet, height, eye = before
        else:
            share = (view_tick - before[0]) / (after[0] - before[0])
            feet = tuple(a + (b - a) * share for a, b in zip(before[1], after[1], strict=True))
            height, eye = (before[2], before[3]) if share < 0.5 else (after[2], after[3])
        return Target(target.entity, target.team, feet, height, target.radius, eye, target.owner)

    # --- shots -------------------------------------------------------------------------------------

    def _bot_weapons(self, tick: int) -> None:
        """Advance the weapon bodies of the map's firing bots and consume their volley reports."""
        from ow174.game import npc_control

        for bot in self.match.bots:
            script = bot.body_script
            controlled = npc_control.manual(bot)
            if ((not bot.firing and not controlled) or bot.dead
                    or not isinstance(script, BotScript) or script.frame > tick):
                continue
            try:
                allowed = (npc_control.update_input(self, bot) if controlled
                           else not self.is_incapacitated(bot))
                moved = heavy_charge.move(bot, self, tick)
                snapshot = script.advance_live(tick, bot.position)
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
                if isinstance(bot.body_script, BotScript):
                    bot.body_script.retire(failed=True)
                bot.body_script = None
                log.exception("[game] %s: %s's weapon script failed", self.match.label(), bot.name)

    def _eye(self, player) -> tuple:
        x, y, z = player.mover.position
        crouched = player.mover.crouched
        height = eye_height(player.hero.body, crouched)
        return (x, y + height, z)

    def _deployed_shields(self, body, team):
        return (winston_barrier.colliders(self, body, team)
                + orisa_deployable.colliders(self, body, team)
                + sigma_barrier.colliders(self, body, team))

    def _shoot(
        self, shooter, command, state, number: int, time: int,
        *, activation: tuple[int, int], snapshot: Snapshot | None = None,
    ) -> None:
        if defenses.phase(shooter)[0]:
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
            origin, direction = (x, y + EYE, z), aim(round(shooter.yaw), round(shooter.pitch))
        else:
            origin, direction = self._eye(shooter), aim(*command.aim())
        direct_config = node.field("m_6396149F")
        direct = hit_def(state, direct_config)
        speed = field("m_projectileSpeed")
        if speed:
            pharah_volley = (
                not npc and shooter.hero.guid == 0x02E0000000000008
                and (state.instance.graph.index, state.index) in dva_matrix.PHARAH_VOLLEYS
            )
            contact_extent = 0.0
            if pharah_volley:
                # D9F821 selects the authored stance height and adds the mover's spring at +0x2C.
                x, y, z = shooter.mover.position
                height = expr.f32(eye_height(shooter.hero.body, shooter.mover.crouched))
                origin = (x, y + expr.f32(height + shooter.mover.spring), z)
                # D8AFA8 initializes the scalar for the body and defense queries.
                extent = (direct_config or {}).get("m_57DAE1B5")
                if extent is not None:
                    contact_extent = _number(state.evaluate(extent, watch=False), 0.0)
            barrage = (pharah_volley and state.instance.graph.index == barrage_motion.GRAPH
                       and state.index == barrage_motion.STATE)
            lifetime = field("m_projectileLifetime", 5.0) or 5.0
            reach = field("m_FEC435C6")
            if reach and not barrage:
                lifetime = min(lifetime, reach / speed)
            if not npc and mei_blizzard.launch(
                    self, shooter, state, shot_frame, origin, direction, speed, lifetime,
                    activation=activation, snapshot=snapshot):
                return
            if (not npc and shooter.hero.guid == orisa_deployable.HERO
                    and orisa_deployable.launch(self, shooter, state, origin, direction, speed, lifetime)):
                return
            if (not npc and shooter.hero.guid == baptiste_field.HERO
                    and baptiste_field.launch(self, shooter, state, origin, direction, speed, lifetime)):
                return
            if (not npc and shooter.hero.guid == steel_trap.HERO
                    and steel_trap.launch(self, shooter, state, origin, direction, speed, reach)):
                return
            rock = sigma_accretion.launch(state, direction) if not npc else None
            gravity, velocity = 0.0, None
            if rock is not None:
                gravity, direction = rock
                velocity = tuple(expr.f32(speed * axis) for axis in direction)
            motion = ()
            if barrage:
                direction, motion = barrage_motion.launch(state, activation[0], number, direction)
            blast = hit_def(state, node.field("m_D2D11CE9"))
            entity_def = (node.field("m_projectileEntity") or {}).get("m_entityDef")
            definition = state.evaluate(entity_def, watch=False) if entity_def is not None else None
            shot = None
            if isinstance(definition, expr.Asset):
                shot = self.projectiles.fired(
                    shooter, state, shot_frame, definition.guid, lifetime, origin, direction,
                    activation=activation, snapshot=snapshot,
                )
            damaging = (direct is not None and direct.amount > 0.0) or (
                blast is not None and blast.amount > 0.0
            )
            pharah_impulse = (
                not npc and shooter.hero.guid == 0x02E0000000000008
                and blast is not None and blast.impulse_rings
            )
            if damaging or shot is not None or pharah_impulse:
                matrix_eligible = pharah_volley or (
                    npc and isinstance(definition, expr.Asset)
                    and definition.guid in dva_matrix.NPC_PROJECTILES
                )
                rays = (direction,)
                if (npc and shooter.body == 0x0400000000002279
                        and state.instance.graph.index == 0x13EB and node.pos == 24):
                    from ow174.game.pellets import ring_directions

                    rays = ring_directions(state, direction)
                for pellet, ray in enumerate(rays):
                    self.rockets.append(
                        Rocket(
                            shooter, shooter.team, origin, ray, speed, lifetime, direct, blast,
                            shot if pellet == 0 else None,
                            last_tick=self.tick if npc else None, motion=motion,
                            matrix_eligible=matrix_eligible, deflect_lifetime=lifetime if motion else None,
                            range_limit=reach if barrage else 0.0,
                            barrage_source=(shooter, state, number) if barrage else None,
                            contact_extent=contact_extent,
                            source_body=_source_entity(shooter),
                            accretion_source=state.instance if rock is not None else None,
                            gravity=gravity, velocity=velocity,
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
        reflectors = set()
        world_tick = self.tick if npc else None
        while True:
            reflector = self._trace_hitscan(
                shooter, origin, direction, reach, direct, shot_frame, view_tick,
                world_tick=world_tick, reflected=bool(reflectors),
            )
            if reflector is None or reflector.entity in reflectors:
                return
            reflectors.add(reflector.entity)
            shooter = reflector.owner
            origin = defenses.defense_eye(shooter)
            direction = aim(shooter.mover.yaw, shooter.mover.pitch)
            view_tick = self.tick

    def _trace_hitscan(self, shooter, origin, direction, reach, direct, shot_frame, view_tick,
                       *, world_tick, reflected):
        body, team = _source_entity(shooter), shooter.team
        brigitte_shields = brigitte_bash.colliders(self, body, team)
        mesh_shields = {guard.owner.body: guard for guard in brigitte_shields if guard.geometry is not None}
        best = None
        enemies = self._enemies(shooter)
        for target in enemies:
            shown = self.seen(target, view_tick)
            if shown is None:
                continue
            a, b = shown.segment()
            t = ray_capsule(origin, direction, a, b, shown.radius)
            guard = mesh_shields.get(target.entity)
            # The mover capsule can overlap the attached mesh.
            if (guard is not None and t is not None and t <= reach
                    and guard.ray(origin, direction, min(reach, t + shown.radius)) is not None):
                continue
            if t is not None and t <= reach and (best is None or t < best[0]):
                best = (t, target, shown)
        limit = best[0] if best is not None else reach
        shields = (self._deployed_shields(body, team) + brigitte_shields
                   + zarya_barrier.colliders(self, body, team))
        barrier = deployable_contact(shields, origin, direction, limit)
        if barrier is not None and (best is None or barrier[0] < best[0]):
            limit = barrier[0]
        else:
            barrier = None
        held = barrier_pet.contact(barrier_pet.active_colliders(self, enemies), origin, direction, limit)
        if held is not None and (best is None or held[0] < best[0]):
            limit = held[0]
            barrier = None
        else:
            held = None
        ice = self.walls.cast(origin, direction, limit)
        limit = ice[0] if ice is not None else limit
        gate = defenses.projectile_gate(origin, direction, limit, enemies, matrix_eligible=True)
        if best is None and ice is None and barrier is None and held is None and gate is None:
            return
        wall = ray_world(self._collision(), origin, direction, limit)
        if (gate is not None and (wall is None or gate[0] < wall)
                and (gate[0] < limit or (ice is None and barrier is None and held is None))):
            distance, target, mode, state = gate
            if mode == 1:
                sigma_grasp.absorbed(state, direct.amount * direct.scale(distance))
            return target if mode == 0 else None
        if ice is not None:
            distance, piece, _ = ice
            if wall is None or distance <= wall:
                self.walls.damage(piece, shooter, direct.amount * direct.scale(distance), shot_frame)
            return
        if held is not None:
            distance, owner, pet = held
            if wall is None or distance < wall:
                barrier_pet.damage(owner, pet, direct.amount * direct.scale(distance),
                                   _along(origin, direction, distance))
            return
        if barrier is not None:
            distance, shield = barrier
            if wall is None or distance < wall:
                amount = direct.amount * direct.scale(distance)
                if isinstance(shield, zarya_barrier.Bubble):
                    shield.damage(amount, body)
                else:
                    shield.damage(amount)
            return
        if best is None:
            return
        t, target, shown = best
        if wall is not None and wall < t:
            return
        hit_y = origin[1] + direction[1] * t
        critical = hit_y >= shown.feet[1] + shown.eye - HEAD_BELOW
        amount = direct.amount * direct.scale(t) * (direct.critical if critical else 1.0)
        self._damage(
            shooter, target, amount, critical and direct.critical > 1.0, direct.loadout, shot_frame,
            world_tick=world_tick, tags=direct.tags, reflected_by=body if reflected else None,
        )
        self._hit_effect(target, direct.effect, shooter)

    def _fly(self, rocket: Rocket) -> None:
        """Sweep elapsed world time against the current bodies and static collision."""
        if ((rocket.accretion_source is not None and rocket.source_body != rocket.shooter.body)
                or (rocket.whip_source is not None and not brigitte_whip.active(self, rocket))):
            self._landed(rocket)
            return
        # Player input can arrive immediately before the scheduler accounts for a gap.
        elapsed = 1 if rocket.last_tick is None else self.tick - rocket.last_tick
        if elapsed <= 0:
            return
        duration = min(elapsed * TICK, rocket.left)
        if duration <= 0.0:
            self._landed(rocket)
            return
        if rocket.velocity is not None:
            # A gravity projectile may land before the end of a batched update.
            for flight_tick in range(self.tick - elapsed + 1, self.tick + 1):
                rocket.last_tick = flight_tick
                if self._sweep_rocket(rocket, min(TICK, rocket.left)) is None:
                    break
            return
        rocket.last_tick = self.tick
        if not rocket.motion:
            self._sweep_rocket(rocket, duration)
            return
        for _ in range(elapsed):
            remaining = TICK
            for _ in range(1000):  # D703D0 bounds repeated contacts in a single step.
                flown = rocket.flown
                continuation = self._sweep_rocket(rocket, min(remaining, rocket.left))
                if continuation is None:
                    return
                if continuation is rocket:
                    break
                remaining -= rocket.flown - flown
                # D7085F and D70864 resume at the elapsed fraction of this tick.
                continuation.motion_phase_offset = TICK - remaining
                rocket = continuation
                if remaining <= 0.0:
                    break
            else:
                # The native limit ends this update with the projectile still registered.
                rocket.motion_phase_offset += remaining
                return

    def _sweep_rocket(self, rocket: Rocket, duration: float) -> Rocket | None:
        # Sweep the remaining flight even when the range or lifetime ends inside this tick.
        if rocket.velocity is not None:
            # D71ACD-D71ADE updates Y before the sweep.
            vx, vy, vz = rocket.velocity
            vy = expr.f32(vy - expr.f32(expr.f32(rocket.gravity) * expr.f32(duration)))
            rocket.velocity = (vx, vy, vz)
            rocket.speed = math.hypot(*rocket.velocity)
            if not rocket.speed:
                rocket.flown += duration
                rocket.left -= duration
                if rocket.left <= 0.0:
                    self._landed(rocket)
                    return None
                return rocket
            rocket.direction = tuple(axis / rocket.speed for axis in rocket.velocity)
        step = rocket.speed * duration
        heading = rocket.direction
        if rocket.motion:
            phase = rocket.flown + rocket.motion_phase_offset
            lateral = barrage_motion.displacement(rocket.motion, heading, phase, duration)
            chord = _along(lateral, heading, step)
            step = math.hypot(*chord)
            heading = tuple(axis / step for axis in chord)
        range_ends = False
        if rocket.range_limit:
            remaining = rocket.range_limit - rocket.distance_flown
            if remaining <= 0.0:
                self._landed(rocket)
                return None
            if remaining <= step:
                # D72C80 clips the full chord and its time by the same fraction.
                duration *= remaining / step
                step = remaining
                range_ends = True
        trap = self.steel_traps.get(rocket.source_body)
        if trap is not None and trap.flight is rocket:
            return steel_trap.sweep(self, trap, heading, step, duration, range_ends)
        best = None
        enemies = self._enemies(rocket.shooter)
        colliders = barrier_pet.active_colliders(self, enemies)
        shield = barrier_pet.contact(colliders, rocket.position, heading, step)
        body = _source_entity(rocket.shooter)
        attached = zarya_barrier.colliders(self, body, rocket.team)
        brigitte_shields = brigitte_bash.colliders(self, body, rocket.team)
        mesh_shields = {guard.owner.body: guard for guard in brigitte_shields if guard.geometry is not None}
        deployables = self._deployed_shields(body, rocket.team) + brigitte_shields + attached
        barrier = deployable_contact(deployables, rocket.position, heading, step)
        grenade = rocket.blast is not None and rocket.blast.effect == BIOTIC_GRENADE_EFFECT
        allies = []
        if grenade and not self.match.game_map.free_for_all:
            allies = [target for target in self._targets() if target.owner is not rocket.shooter
                      and target.team == rocket.shooter.team and not self.vitals_of(target).dead
                      and not defenses.phase(target.owner)[1]]
        extent_hit = None
        whip = rocket.whip_source is not None
        extent = 0.0 if whip else rocket.contact_extent
        for target in enemies + allies:
            a, b = target.segment()
            radius = target.radius + rocket.contact_extent if whip else target.radius
            t = ray_capsule(rocket.position, heading, a, b, radius)
            guard = mesh_shields.get(target.entity)
            # The stand-in mover capsule can overlap the real attached mesh.
            # Defer its candidate if this chord reaches the shield within that
            # front half of the capsule. Keep the mesh's own contact distance.
            if (guard is not None and t is not None and t <= step
                    and guard.ray(rocket.position, heading, t + target.radius) is not None):
                continue
            if t is not None and t <= step and (best is None or t < best[0]):
                best = (t, target)
            # D6F190 admits prepared actors through their authored health flag.
            if extent > 0 and (not isinstance(target.owner, bots.Bot)
                               or target.owner.spawner.get("manual_spawn") is True):
                t = ray_capsule(rocket.position, heading, a, b, target.radius + extent)
                if t is not None and t <= step and (extent_hit is None or t < extent_hit[0]):
                    if (guard is not None
                            and guard.ray(rocket.position, heading, t + target.radius + extent) is not None):
                        continue
                    extent_hit = (t, target)
        collision = self._collision()
        wall_hit = collision.cast_ray(rocket.position, heading, step) if collision is not None else None
        wall = wall_hit.distance if wall_hit is not None else None
        if wall is not None and (best is None or wall < best[0]):
            best = (wall, None)
        if shield is not None and (best is None or shield[0] < best[0]):
            best = (shield[0], None)
        else:
            shield = None
        held_at_body = (barrier is not None and isinstance(barrier[1], brigitte_bash.Barrier)
                        and best is not None and best[1] is not None and best[1].owner is barrier[1].owner
                        and abs(barrier[0] - best[0]) <= EPSILON)
        if barrier is not None and (best is None or barrier[0] < best[0] or held_at_body):
            best, shield = (barrier[0], None), None
        else:
            barrier = None
        wards = (baptiste_field.hostile(self, body, rocket.team)
                 + steel_trap.hostile(self, body, rocket.team))
        device = deployable_contact(wards, rocket.position, heading, step)
        if device is not None and (best is None or device[0] < best[0]):
            best, shield, barrier = (device[0], None), None, None
        else:
            device = None
        pillars = tuple(self.walls.pieces.values())
        ice = self.walls.cast(rocket.position, heading, step, pillars)
        if ice is not None and (best is None or ice[0] <= best[0]):
            best, shield, barrier, device = (ice[0], None), None, None, None
        else:
            ice = None
        # D7633C compares the candidate's distance plus its query radius.
        # Preserve the selected contact's actual distance for the impact.
        shield_margin = (EPSILON if barrier is not None and isinstance(barrier[1], brigitte_bash.Barrier)
                         and extent_hit is not None and extent_hit[1].owner is barrier[1].owner else 0.0)
        if extent_hit is not None and (best is None or extent_hit[0] + extent < best[0] - shield_margin):
            best, shield, barrier, device, ice = extent_hit, None, None, None, None
        rock = rocket.accretion_source is not None
        gate = None
        if not rock and not whip:
            gate = defenses.projectile_gate(
                rocket.position, heading, step, enemies, matrix_eligible=rocket.matrix_eligible,
                projectile_extent=rocket.contact_extent,
            )
        if gate is not None and (best is None or gate[0] < best[0]
                                 or (gate[0] == best[0] and ice is None)):
            distance, target, mode, state = gate
            travelled = duration * (distance / step) if rocket.motion else distance / rocket.speed
            rocket.flown += travelled
            if rocket.range_limit:
                rocket.distance_flown = expr.f32(rocket.distance_flown + expr.f32(distance))
            self._landed(rocket)
            if mode == 1:
                amount = sum(max(0.0, hit.amount) for hit in (rocket.direct, rocket.blast) if hit is not None)
                sigma_grasp.absorbed(state, amount)
            if mode == 0 and rocket.left > travelled:
                direction = aim(target.owner.mover.yaw, target.owner.mover.pitch)
                lifetime = rocket.deflect_lifetime
                if lifetime is None:
                    lifetime = rocket.left - travelled
                    point = _along(rocket.position, heading, distance)
                    origin = _along(point, direction, 0.001)
                else:
                    # D74105 sets the eye origin. D74C5B and D74C98 restart the flight.
                    origin = defenses.defense_eye(target.owner)
                if rocket.barrage_source is not None:
                    # D6AB50 resolves the original weapon state before D5EBA0 reapplies spread.
                    owner, volley, shot = rocket.barrage_source
                    instance = volley.instance
                    script = owner.body_script
                    if (script is not None and script.component is instance.component
                            and script.component.instances.get(instance.id) is instance):
                        seed = barrage_motion.seed_for_shot(
                            0, instance.id, volley.index, volley.counter, shot,
                        )
                        direction = barrage_motion.shot_direction(volley, direction, seed)
                reflected = Rocket(
                    target.owner, target.team, origin, direction,
                    rocket.speed, lifetime, rocket.direct, rocket.blast,
                    last_tick=self.tick, motion=rocket.motion, deflect_lifetime=rocket.deflect_lifetime,
                    matrix_eligible=rocket.matrix_eligible, reflected_by=target.entity,
                    range_limit=rocket.range_limit, barrage_source=rocket.barrage_source,
                    contact_extent=rocket.contact_extent,
                    source_body=target.entity,
                )
                self.rockets.append(reflected)
                if reflected.deflect_lifetime is not None:
                    return reflected
            return None  # absorb/reflect never creates an impact blast at the gate
        if best is None:
            rocket.position = _along(rocket.position, heading, step)
            rocket.flown += duration
            rocket.left -= duration
            if rocket.range_limit:
                rocket.distance_flown = expr.f32(rocket.distance_flown + expr.f32(step))
            if range_ends or rocket.left <= 0.0:
                self._landed(rocket)
                return None
            return rocket
        t, struck = best
        rocket.flown += duration * (t / step) if rocket.motion else t / rocket.speed
        if rocket.range_limit:
            rocket.distance_flown = expr.f32(rocket.distance_flown + expr.f32(t))
        self._landed(rocket)
        point = _along(rocket.position, heading, t)
        originating_body = rocket.reflected_by or rocket.source_body
        direct_amount = 0.0
        if rocket.direct is not None:
            direct_amount = rocket.direct.amount * ana_nano.outgoing(
                self, body, originating_body, tags=rocket.direct.tags,
            )
        blast = rocket.blast
        blast_scale = (ana_nano.outgoing(self, body, originating_body, tags=blast.tags)
                       if blast is not None else 1.0)
        if device is not None and rocket.direct is not None:
            device[1].health = max(0.0, device[1].health - direct_amount)
        if shield is not None or barrier is not None:
            amount = direct_amount
            if blast is not None:
                amount += blast.amount * blast.splash(0.0) * blast_scale
            if amount > 0:
                if shield is not None:
                    barrier_pet.damage(shield[1], shield[2], amount, point)
                elif isinstance(barrier[1], zarya_barrier.Bubble):
                    barrier[1].damage(amount, body)
                else:
                    barrier[1].damage(amount)
        if struck is not None and rocket.direct is not None and rocket.direct.amount > 0.0:
            if whip and defenses.deflects_melee(struck.owner, heading):
                return None
            previous = self.vitals_of(struck).total if rock or whip else None
            self._damage(
                rocket.shooter, struck, rocket.direct.amount, False, rocket.direct.loadout, self.tick,
                world_tick=self.tick, tags=rocket.direct.tags, reflected_by=rocket.reflected_by,
                source_body=rocket.source_body,
            )
            self._hit_effect(struck, rocket.direct.effect, rocket.shooter)
            if rock:
                sigma_accretion.hit(self, rocket, struck, previous - self.vitals_of(struck).total)
            if whip and self.vitals_of(struck).total < previous and not self.vitals_of(struck).dead:
                brigitte_whip.impulse(self, rocket, struck)
        if blast is None:
            if ice is not None and rocket.direct is not None:
                self.walls.damage(ice[1], rocket.shooter, direct_amount, self.tick)
            return None
        # A sweep stops exactly on a triangle. Start the visibility test just before that
        # surface so a zero-distance hit does not hide victims on the incoming side.
        visibility_origin = (
            _along(point, heading, -BLAST_SURFACE_OFFSET) if struck is None else point
        )
        wall_hits = self.walls.splash_hits(point, visibility_origin, blast)
        if ice is not None and rocket.direct is not None:
            self.walls.damage(ice[1], rocket.shooter, direct_amount, self.tick)
        for piece, amount in wall_hits:
            self.walls.damage(piece, rocket.shooter, amount * blast_scale, self.tick)
        for ward in wards:
            a, b = ward.segment()
            distance = max(0.0, segment_distance(point, a, b) - ward.radius)
            if ward.health > 0 and ((device is not None and ward is device[1])
                                    or self._blast_reaches(
                                        visibility_origin, ward, colliders, deployables, pillars)):
                amount = blast.amount * blast.splash(distance) * blast_scale
                ward.health = max(0.0, ward.health - amount)
        for bubble in attached:
            if barrier is not None and barrier[1] is bubble:
                continue
            delta = _sub(bubble.centre(), visibility_origin)
            distance = math.sqrt(_dot(delta, delta))
            reach = distance - zarya_barrier.RADIUS
            amount = blast.amount * blast.splash(max(0.0, math.dist(point, bubble.centre())
                                                    - zarya_barrier.RADIUS))
            if amount <= 0:
                continue
            if reach > EPSILON:
                direction = tuple(axis / distance for axis in delta)
                limit = reach - EPSILON
                if (ray_world(self._collision(), visibility_origin, direction, limit) is not None
                        or barrier_pet.contact(colliders, visibility_origin, direction, limit) is not None
                        or deployable_contact(deployables, visibility_origin, direction, limit) is not None
                        or self.walls.cast(visibility_origin, direction, limit, pillars) is not None):
                    continue
            bubble.damage(amount * blast_scale, body)
        protected = {bubble.body for bubble in attached}
        # The payload retains its original weapon behavior after a Genji redirect.
        pharah = blast.loadout in {0x0B9000000000000F, 0x0B9000000000000E, 0x0B9000000000000B}
        candidates = self._enemies(rocket.shooter)
        if pharah or rock:
            candidates.extend(t for t in self._targets() if t.owner is rocket.shooter)
        for target in candidates:
            if target.entity in protected or self.vitals_of(target).dead:
                continue
            a, b = target.segment()
            distance = max(0.0, segment_distance(point, a, b) - target.radius)
            own = target.owner is rocket.shooter
            scalar = blast.splash(distance) * (blast.self_scale if own else 1.0)
            impulse = (0.0, 0.0)
            impulse_direction = None
            source_scale = 1.0
            if own:
                source_scale = blast.source_impulse_scale if target.entity == rocket.source_body else 0.0
            if rock and source_scale:
                horizontal, vertical = blast.impulse_scales(distance)
                impulse = (blast.horizontal_impulse * horizontal * source_scale,
                           (blast.vertical_impulse_minimum or 0.0) * vertical * source_scale)
            elif pharah and source_scale:
                impulse_distance = distance
                vertical_direction = 1.0
                if own:
                    normal = (wall_hit.normal if wall_hit is not None and t == wall
                              and struck is None and shield is None and barrier is None
                              and device is None and ice is None else (0.0, 0.0, 0.0))
                    impulse_distance, impulse_direction = source_blast_geometry(
                        target.feet, target.height, target.radius, point, normal,
                    )
                    vertical_direction = impulse_direction[1]
                horizontal, vertical = blast.impulse_scales(impulse_distance)
                if impulse_direction is not None:
                    dx, _, dz = impulse_direction
                    if math.hypot(dx, dz) <= EPSILON:
                        horizontal = 0.0
                upward = blast.vertical_impulse * vertical * vertical_direction * source_scale
                if vertical > 0.0 and blast.vertical_impulse_minimum is not None:
                    # The original source prediction supplies a zero reference.
                    # For other targets the server uses the current mover velocity.
                    velocity_y = 0.0
                    if not own:
                        actor = target.owner
                        velocity_y = (
                            actor.velocity[1] if isinstance(actor, bots.Bot) else actor.mover.velocity[1]
                        )
                    upward = max(upward, blast.vertical_impulse_minimum - velocity_y)
                impulse = blast.horizontal_impulse * horizontal * source_scale, upward
            has_impulse = any(impulse)
            visible = ((scalar > 0.0 or (has_impulse and (rock or not own)))
                       and self._blast_reaches(visibility_origin, target, colliders, deployables, pillars))
            if scalar > 0.0 and visible:
                self._damage(
                    rocket.shooter, target, blast.amount * scalar, False, blast.loadout, self.tick,
                    world_tick=self.tick, tags=blast.tags, reflected_by=rocket.reflected_by,
                    source_body=rocket.source_body,
                )
                self._hit_effect(target, blast.effect, rocket.shooter)
            if has_impulse and not self.vitals_of(target).dead and (
                self._source_blast_reaches(point, heading, target, blast, colliders, deployables, pillars)
                if own and pharah else visible
            ):
                self._blast_impulse(target, point, *impulse, direction=impulse_direction, carry=rock)
        for raised in brigitte_shields:
            if barrier is not None and barrier[1] is raised:
                continue
            closest = raised.closest_front(point)
            if closest is None:
                continue
            amount = blast.amount * blast.splash(math.dist(point, closest))
            if amount <= 0:
                continue
            delta = _sub(closest, visibility_origin)
            reach = math.hypot(*delta)
            blockers = tuple(other for other in deployables if other is not raised)
            if reach <= EPSILON or self._blast_path_clear(
                    visibility_origin, tuple(axis / reach for axis in delta), reach, colliders, blockers, pillars):
                raised.damage(amount * blast_scale)
        if rock and struck is not None and rocket.direct is not None and not self.vitals_of(struck).dead:
            horizontal, vertical = rocket.direct.impulse_scales(0.0)
            self._blast_impulse(struck, point, rocket.direct.horizontal_impulse * horizontal,
                                (rocket.direct.vertical_impulse_minimum or 0.0) * vertical,
                                direction=heading, carry=True)
        if grenade:
            allies.extend(t for t in self._targets() if t.owner is rocket.shooter)
            for target in allies:
                if self.vitals_of(target).dead or defenses.phase(target.owner)[1]:
                    continue
                a, b = target.segment()
                scalar = blast.splash(max(0.0, segment_distance(point, a, b) - target.radius))
                if scalar > 0 and self._blast_reaches(
                        visibility_origin, target, colliders, deployables, pillars):
                    self._heal_target(rocket.shooter, target, BIOTIC_GRENADE_HEAL * scalar,
                                      self.tick, self.tick)
                    vitals = self.vitals_of(target)
                    vitals.grenade_interval(vitals.healing_boosts, self.tick)
        return None

    def _blast_impulse(self, target: Target, point, horizontal: float, vertical: float,
                       *, direction=None, carry: bool = False) -> None:
        """Apply impulse along the supplied horizontal direction or the target radial.

        Players and prepared manual NPCs integrate with the normal body mover.
        Authored modifier26 scales response; native force conversion, complete
        resistance, animation/contact and automatic AI remain unproven.
        """
        if direction is None:
            dx, dz = target.feet[0] - point[0], target.feet[2] - point[2]
        else:
            dx, _, dz = direction
        length = math.hypot(dx, dz)
        scale = horizontal / length if length > EPSILON else 0.0
        self._apply_impulse(target, (dx*scale, vertical, dz*scale), carry=carry)

    def _apply_impulse(self, target: Target, impulse, *, next_pose: bool = False, carry: bool = False) -> None:
        if (defenses.fortified(target.owner)
                or (defenses.phase(target.owner)[0] and not defenses.transcendence(target.owner))):
            return
        if isinstance(target.owner, bots.Bot):
            category = MOVER
            if not self.match.game_map.free_for_all and target.team in (0, 1):
                category = (TEAM0, TEAM1)[target.team]
            target.owner.apply_impulse(impulse, self.tick, self.match.collision_world, category=category)
            return
        mover = target.owner.mover
        script = target.owner.body_script
        if carry:
            frame = script.component.frame + 1 if script is not None else self.tick
            mover.add_impulse(impulse, frame, next_pose=script is not None, carry=True)
        elif next_pose and script is not None:
            mover.add_impulse(impulse, script.component.frame + 1, next_pose=True)
        else:
            mover.add_impulse(impulse, self.tick)

    def _blast_reaches(self, origin, target: Target, colliders=(), deployables=(), pillars=None) -> bool:
        """Test the nearest capsule point against the map and impact-time barriers."""
        a, b = target.segment()
        axis = (a[0], max(a[1], min(b[1], origin[1])), a[2])
        delta = _sub(axis, origin)
        distance = math.sqrt(_dot(delta, delta))
        if distance <= EPSILON:
            return True
        direction = tuple(value / distance for value in delta)
        # The wearer's stand-in capsule can protrude through the attached mesh.
        # Test that shield up to the body axis even when the capsule begins first.
        if any(isinstance(guard, brigitte_bash.Barrier) and guard.geometry is not None
               and guard.owner is target.owner and guard.ray(origin, direction, distance) is not None
               for guard in deployables):
            return False
        reach = distance - target.radius
        return self._blast_path_clear(origin, direction, reach, colliders, deployables, pillars)

    def _source_blast_reaches(self, impact, heading, target: Target, blast: HitDef,
                              colliders, deployables, pillars) -> bool:
        """B333F0 source admission and two sight points on the upright mover."""
        origin = _along(impact, heading, -0.2)
        x, bottom, z = target.feet
        top = bottom + target.height
        nearest = (x, max(bottom, min(top, origin[1])), z)
        distance = math.dist(origin, nearest) - target.radius
        if not blast.rings or distance >= blast.rings[-1][0]:
            return False
        if blast.source_impulse_ignores_los:
            return True
        a, b = target.segment()
        for sample in ((x, bottom + target.height / 2, z), nearest):
            delta = _sub(sample, origin)
            reach = math.hypot(*delta)
            if reach <= EPSILON:
                return True
            direction = tuple(axis / reach for axis in delta)
            body = ray_capsule(origin, direction, a, b, target.radius)
            if body is not None:
                reach = min(reach, body)
            if self._blast_path_clear(origin, direction, reach, colliders, deployables, pillars):
                return True
        return False

    def _blast_path_clear(self, origin, direction, reach, colliders, deployables, pillars) -> bool:
        if reach <= EPSILON:
            return True
        wall = ray_world(self._collision(), origin, direction, reach)
        return ((wall is None or wall >= reach - EPSILON)
                and barrier_pet.contact(colliders, origin, direction, reach - EPSILON) is None
                and deployable_contact(deployables, origin, direction, reach - EPSILON) is None
                and self.walls.cast(origin, direction, reach - EPSILON, pillars) is None)

    def _landed(self, rocket: Rocket) -> None:
        """The projectile ended (hit, range, lifetime): the predicted one's release follows."""
        if rocket in self.rockets:
            self.rockets.remove(rocket)
        if rocket.shot is not None:
            self.projectiles.flown(rocket.shot, rocket.flown)

    def _transcendence_heal(self, retained):
        """Restore the caster at entry and heal nearby visible allies during the phase."""
        for zen in self.match.players:
            if zen.gone or not zen.has_body:
                continue
            state = defenses.transcendence_state(zen)
            if state is None:
                self.transcendence_seen.pop(zen.body, None)
                continue
            targets = self._targets()
            own = next((target for target in targets if target.owner is zen), None)
            if own is None or self.vitals_of(own).dead:
                continue
            first = retained
            activation = state.instance.id, state.counter
            if self.transcendence_seen.get(zen.body) != activation:
                self.transcendence_seen[zen.body] = activation
                first = self.tick
                vitals = self.vitals_of(own)
                vitals.healing_blocks.clear()
                if vitals.heal(vitals.maximum) > 0:
                    self._sync_body_health(zen, vitals)
            if self.match.game_map.free_for_all:
                continue
            origin = self._eye(zen)
            colliders = barrier_pet.active_colliders(self, self._enemies(zen))
            deployables = self._deployed_shields(zen.body, zen.team)
            amount = 300.0 * TICK * (self.tick - first + 1)
            for target in targets:
                if target.owner is zen or target.team != zen.team or self.vitals_of(target).dead:
                    continue
                point = _along(target.feet, (0, 1, 0), target.eye)
                if (math.dist(origin, point) <= 10.0
                        and self._blast_reaches(origin, target, colliders, deployables)):
                    self._heal_target(zen, target, amount, first, self.tick)

    def heal_area(self, healer, centre, radius: float, amount: float, *, ticks=None) -> None:
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
            first, last = (self.tick, self.tick) if ticks is None else ticks
            self._heal_target(healer, target, amount, first, last)

    def _heal_target(self, healer, target, amount, first, last):
        vitals = self.vitals_of(target)

        def overlap(start, end):
            return max(0, min(last + 1, end) - max(first, start))

        blocked = sum(overlap(start, end) for start, end in vitals.healing_blocks)
        boosted = sum(overlap(start, end) for start, end in vitals.healing_boosts)
        boosted -= sum(overlap(max(start, blocked_start), min(end, blocked_end))
                       for start, end in vitals.healing_boosts
                       for blocked_start, blocked_end in vitals.healing_blocks)
        count = last - first + 1
        given = vitals.heal(amount * (count - blocked + BIOTIC_GRENADE_BOOST * boosted) / count)
        if given > 0.0:
            healer.stats.add(stats.HEALING, given)
            self._sync_body_health(target.owner, vitals)

    @staticmethod
    def _sync_body_health(owner, vitals: Vitals) -> None:
        script = getattr(owner, "body_script", None)
        if script is not None:
            script.set_health(vitals.total, vitals.maximum,
                              health_pools=vitals.component(vitals.ids)[51])

    def _collision(self):
        from ow174.game.collision import CollisionError

        world = self.match.collision_world
        if world is None:
            raise CollisionError("match collision is not ready")
        return world

    # --- damage and death --------------------------------------------------------------------------

    def _damage(
        self, shooter, target: Target, amount: float, critical: bool, loadout, frame: int,
        *, world_tick: int | None = None, tags=(), reflected_by: int | None = None,
        source_body: int | None = None,
    ) -> None:
        vitals = self.vitals_of(target)
        if vitals.dead or amount <= 0.0 or defenses.phase(target.owner)[0]:
            return
        amount *= zenyatta_discord.multiplier(self, shooter, target) * defenses.fortify_scale(target.owner)
        amount *= ana_nano.multiplier(
            self, _source_entity(shooter), target.entity, reflected_by or source_body, tags=tags,
        )
        dealt = vitals.damage(amount, baptiste_field.floor(self, target, vitals))
        if dealt <= 0.0:
            return
        # Sleep Dart installs its status after its own damage. Later damage wakes the target.
        vitals.sleep_until = 0
        killed = vitals.now[HEALTH_POOL] <= EPSILON
        if killed:
            vitals.now[HEALTH_POOL] = 0.0
            vitals.died_at = self.now
            vitals.death_tick = self.match.tick if world_tick is None else world_tick
            vitals.killer = _source_entity(shooter)
        if not isinstance(shooter, bots.Bot) and target.owner is not shooter:
            flags = (CRITICAL if critical else 0) | (KILLING if killed else 0)
            self._tell_hit(shooter, target, dealt, flags, frame)
            shooter.stats.add(stats.HERO_DAMAGE, dealt)
            enemy = self.match.game_map.free_for_all or target.team != shooter.team
            credited_body = reflected_by if reflected_by is not None else source_body
            if (enemy and shooter.body_script is not None
                    and (credited_body is None or credited_body == shooter.body)):
                shooter.body_script.damage_charge(dealt, loadout, reflected=reflected_by is not None)
        victim = target.owner
        sombra_hack.received_damage(victim, _source_entity(shooter), tags)
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
        steel_trap.retire_body(self, bot.entity, source=False)
        self.frost.pop(bot.entity, None)
        sombra_hack.retire(self, bot.entity)
        from ow174.game import npc_control

        npc_control.cancel_combat_movement(bot)
        heavy_charge.stop(bot, self)
        self.clear_pins(bot.entity)
        from ow174.game import sniper_reposition

        sniper_reposition.cancel(bot)
        self.hooks.retire(bot.entity)
        zenyatta_discord.retire(self, bot.entity)
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
                vitals = self.vitals[bot.entity]
                event = script.die(tick, params, _source_entity(killer),
                                   health_pools=vitals.component(vitals.ids)[51])
            else:
                event = script.die(tick, params, _source_entity(killer))
        except Exception:
            if isinstance(bot.body_script, BotScript):
                bot.body_script.retire(failed=True)
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
            if isinstance(bot.body_script, BotScript):
                bot.body_script.retire(failed=True)
            bot.body_script = None
            log.exception("[game] %s: %s's death script timer failed", self.match.label(), bot.name)
            return
        for event in events:
            for viewer in self.match.players:
                if viewer.client is not None:
                    viewer.client.queue_bot_death_update(bot.entity, event)

    def _died(self, killer, victim, critical: bool, loadout) -> None:
        steel_trap.retire_body(self, victim.body, source=False)
        from ow174.game.sigma_flux import interrupt_flux_lift

        interrupt_flux_lift(self, victim)
        """A player died: the notices, his body script's "died" (the kill feed line), and the respawn."""
        victim.mover.retire_impulses()
        barrier_pet.retire(self, victim.body)
        sigma_barrier.retire(self, victim.body)
        self.brigitte_barriers.pop(victim.body, None)
        if victim.hero.guid == brigitte_bash.HERO:
            self.rockets[:] = [rocket for rocket in self.rockets
                               if rocket.shooter is not victim or rocket.whip_source is None]
        sombra_hack.retire(self, victim.body)
        self.frost.pop(victim.body, None)
        self.grasp_shields.pop(victim.body, None)
        self.grasp_grants.pop(victim.body, None)
        self.clear_pins(victim.body)
        self.hooks.retire(victim.body)
        zenyatta_discord.retire(self, victim.body)
        ana_nano.retire(self, victim.body)
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
