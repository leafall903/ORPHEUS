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
from dataclasses import dataclass, field

from ow174.game import (
    assassin_victim,
    barriers,
    bots,
    interception,
    observers,
    pools,
    projectiles,
    stats,
    voicelines,
    world,
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
#104319 FirstPerson m_E29993A5/m_816B830F; camera offsets, not native bone hitboxes.
EYE_STANDING = {0x0400000000000225: 1.2, 0x04000000000002A3: 1.425, 0x04000000000003BF: 1.6}
EYE_CROUCHED = {
    0x04000000000003CF: 1.085, 0x0400000000000225: .84, 0x04000000000002A3: .9975, 0x04000000000003BF: 1.12,
}
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
GENJI_ASSIST_TICKS = math.ceil(5.0 / TICK)  # reconstructed damage-contribution credit window
EPSILON = 1e-9
FLASHBANG_EFFECT = 0x04A0000000000079  # .053/0079 ->0228 ->224E
FLASHBANG_MODE_FLAG = 13408  # 0228 duration: flag set1.7s, otherwise0.8s
SLEEP_DART_ENTITY = 0x04000000000015E8  # 0BDD state 6
SLEEP_DART_EFFECT = 0x04A000000000026F  # .053/026F -> 0BDE
SLEEP_TICKS = math.ceil(5.0 / TICK)
SLEEP_WAKE_FLOOR_TICKS = math.ceil(1.5 / TICK)
SLEEP_WAKE_RECOVERY_TICKS = math.ceil(0.5 / TICK)
MEI_SPRAY_ENTITY = 0x04000000000008BE  # root target tag 0145, excluded by Genji 03A3 state 10
MEI_SPRAY_LOADOUT = 0x0B900000000000D3  # 05E6 state 13 -> frost effect 0169
FROST_PROGRESS = 2427  # 0B77 aggregate consumed by 05E7
FROST_INITIAL, FROST_STEP = expr.f32(0.2), expr.f32(0.0285)
FROST_GAP_TICKS, FROST_FREEZE_TICKS = math.ceil(1.0 / TICK), math.ceil(1.3 / TICK)
PHASE_STATES = {
    0x02E0000000000002: (0x0020, 5),  # Reaper Wraith, node11 writes entity 530
    0x02E00000000001A2: (0x0F7C, 4),  # Moira Fade, node10 writes entity 530
}
DAMAGE_IMMUNITY_STATES = PHASE_STATES | {
    0x02E0000000000020: (0x017B, 31),  # Transcendence, node62 writes entity 10414
    0x02E00000000000DD: (0x05FE, 35),  # Cryo-Freeze, node58 writes entity 10414
}
BEAM_STATES = {
    0x02E0000000000016: (0x157C, 100, 86),  # Symmetra
    0x02E0000000000068: (0x0241, 81, 10),  # Zarya
    0x02E00000000001A2: (0x0F76, 106, 22),  # Moira damage grasp
}


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


def genji_fan(state, direction):
    """Apply 0304's authored fan rotation and direction order (native D808F0)."""
    cfg = state.node.field("m_5EC1EB38") or {}
    if state.instance.graph.index != 0x0304 or cfg.get("$") != "STU_1145C42E":
        return (direction,)
    count = _number(state.evaluate(state.node.field("m_numProjectilesPerShot"), watch=False), 1)
    if count != 3:
        return (direction,)
    # D5EAA0 converts the authored full angle to radians for D808F0.
    half_spread = math.radians(_number(state.evaluate(cfg.get("m_spread"), watch=False), 0.0)) / 2
    rotation = math.radians(_number(state.evaluate(cfg.get("m_A2A9890A"), watch=False), 90.0))
    x, y, z = direction
    horizontal = math.hypot(x, z)
    if horizontal > EPSILON:
        right = (z / horizontal, 0.0, -x / horizontal)
        up = (-y * x / horizontal, horizontal, -y * z / horizontal)
    else:
        right, up = (-1.0, 0.0, 0.0), (0.0, 0.0, math.copysign(1.0, y))
    tangent = tuple(u * math.cos(rotation) - r * math.sin(rotation) for u, r in zip(up, right))
    return tuple(
        tuple(d * math.cos(offset) + t * math.sin(offset) for d, t in zip(direction, tangent))
        for offset in (-half_spread, 0.0, half_spread)
    )


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


def _segment_point(point, a, b):
    """The nearest point on segment a-b."""
    ab, ap = _sub(b, a), _sub(point, a)
    length = _dot(ab, ab)
    t = 0.0 if length <= EPSILON else max(0.0, min(1.0, _dot(ap, ab) / length))
    return _along(a, ab, t)


def segment_distance(point, a, b) -> float:
    """The distance from a point to the segment a-b."""
    d = _sub(point, _segment_point(point, a, b))
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


def ring_directions(state, direction):
    """Resource-authored radial projectile layout (613BDDB0), reconstructed phase.

    Ring counts/radii and spread come from the volley. Spread is interpreted as
    degrees and phase starts at local up; native angular phase/muzzle placement
    and multi-pellet client transport are not accepted by this reconstruction.
    """
    config = state.node.field("m_5EC1EB38") or {}
    if config.get("$") != "STU_613BDDB0":
        return None
    count = expr.to_int(state.evaluate(state.node.field("m_numProjectilesPerShot"), watch=False))
    spread = expr.to_float(state.evaluate(config.get("m_spread"), watch=False))
    if not 1 <= count <= 64 or not math.isfinite(spread) or not 0 <= spread < 90:
        raise ValueError("unsupported radial volley count/spread")
    horizontal = math.hypot(direction[0], direction[2])
    right = ((-direction[2]/horizontal, 0, direction[0]/horizontal)
             if horizontal > EPSILON else (-1, 0, 0))
    up = (right[1]*direction[2]-right[2]*direction[1],
          right[2]*direction[0]-right[0]*direction[2],
          right[0]*direction[1]-right[1]*direction[0])
    found = []
    for ring in config.get("m_0D30E40C") or ():
        radius = expr.to_float(state.evaluate(ring.get("m_6425FFB9"), watch=False))
        amount = expr.to_int(state.evaluate(ring.get("m_B19BD35D"), watch=False))
        if not math.isfinite(radius) or radius < 0 or not 1 <= amount <= 64:
            raise ValueError("unsupported radial volley ring")
        if not math.isfinite(spread*radius) or spread*radius >= 90:
            raise ValueError("unsupported radial volley angular extent")
        tangent = math.tan(math.radians(spread*radius))
        for index in range(amount):
            angle = index*2*math.pi/amount
            vector = tuple(direction[k] + tangent*(right[k]*math.sin(angle)+up[k]*math.cos(angle))
                           for k in range(3))
            length = math.sqrt(_dot(vector, vector))
            found.append(tuple(value/length for value in vector))
    if len(found) != count:
        raise ValueError("radial volley ring count does not match authored projectile count")
    return found


def ray_world(
    collision: World | None, origin, direction, length: float, category: int = MOVER,
) -> float | None:
    """The distance to the first static hit, for callers that only need occlusion."""
    hit = collision.cast_ray(origin, direction, length, category) if collision is not None else None
    return hit.distance if hit is not None else None


# --- health -------------------------------------------------------------------------------------------


@dataclass
class TemporaryPool:
    identifier: int
    kind: int
    slot: int
    pool_id: int
    most: float
    now: float
    last_tick: int
    removed: bool = False


class Vitals:
    """A body's pools as component 51 has them: health, armour, shields (world.health's ids and fields)."""

    def __init__(self, entity: int, health: float, armour: float = 0.0, shields: float = 0.0) -> None:
        self.entity = entity
        self.most = {HEALTH_POOL: float(health), ARMOUR_POOL: float(armour), SHIELDS_POOL: float(shields)}
        self.now = dict(self.most)
        self.ids = {}
        pool_id = 1
        for kind in (SHIELDS_POOL, ARMOUR_POOL):  # world.health numbers them so
            if self.most[kind] > 0:
                self.ids[kind] = pool_id
                pool_id += 1
        self.ids[HEALTH_POOL] = pool_id
        self.extra: dict[tuple, TemporaryPool] = {}
        self.next_pool = pool_id + 1
        self.changed: set[int] = set()  # pools that changed since the last update went out
        self.died_at: float | None = None
        self.death_tick: int | None = None
        self.killer: int | None = None
        self.sleep_until = 0
        self.sleep_wake_floor = 0

    def clear_sleep(self) -> None:
        self.sleep_until = self.sleep_wake_floor = 0

    @property
    def dead(self) -> bool:
        return self.died_at is not None

    @property
    def total(self) -> float:
        return sum(self.now.values()) + sum(pool.now for pool in self.extra.values())

    @property
    def maximum(self) -> float:
        return sum(self.most.values()) + sum(pool.most for pool in self.extra.values())

    def add_temporary(self, key, identifier, kind, amount, tick):
        occupied = {pool.slot for pool in self.extra.values() if pool.kind == kind and not pool.removed}
        slot = next((slot for slot in range(1, POOLS) if slot not in occupied), None)
        if slot is None or not math.isfinite(amount) or amount <= 0:
            return None
        # Reuse a retired wire slot, while preserving monotonically unique pool IDs.
        self.extra = {key: pool for key, pool in self.extra.items()
                      if pool.kind != kind or pool.slot != slot}
        pool = TemporaryPool(identifier, kind, slot, self.next_pool, amount, amount, tick)
        self.next_pool += 1
        self.extra[key] = pool
        self.changed.add(kind)
        return pool

    def remove_temporary(self, key):
        pool = self.extra[key]
        pool.most = pool.now = 0.0
        pool.removed = True
        self.changed.add(pool.kind)  # keep a zero-valued wire tombstone until slot reuse

    def damage(self, amount: float) -> float:
        """Take a hit from shields, then armour, then health (unverified, as OW1 did: what reaches armour
        while it has some is ARMOUR_BLOCK less, at most halved). Returns what the pools lost."""
        left = max(0.0, amount)
        taken = 0.0
        for kind in (SHIELDS_POOL, ARMOUR_POOL, HEALTH_POOL):
            for pool in self.extra.values():
                if pool.kind == kind and pool.now > 0 and left > 0:
                    take = min(left, pool.now)
                    pool.now -= take
                    taken += take
                    left -= take
                    self.changed.add(kind)
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
            for pool in self.extra.values():
                if pool.kind == kind:
                    parts[kind * POOLS + pool.slot] = world.pool(
                        pool.pool_id, kind, pool.most, pool.now)
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
    deflectable: bool = False  # reconstructed admission: explicit projectile/bullet tag003BF
    effect: int | None = None  # ModifyHealth's authored hit-effect resource
    projectile_extent: float = 0.0  # m_57DAE1B5, used for flight against bodies and interception boxes
    deflect_override: bool | None = None  # scoped projectile root evidence for Genji

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


@dataclass
class Frost:
    """Default 0B77 max-across-sources progress and exclusive freeze deadline."""

    sources: dict[int, tuple[float, int]] = field(default_factory=dict)
    frozen_until: int = 0

    def progress(self, tick: int) -> float:
        if tick < self.frozen_until:
            return 1.0
        return max((value for value, expires in self.sources.values() if tick < expires), default=0.0)


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
        # Genji omits the override that Helix explicitly sets to1. Reconstruct
        # his normal2x critical default; native class-default dispatch is open.
        critical=value("m_A872C70E", 2.0 if state.instance.graph.index == 0x0304 else 1.0),
        rings=tuple(sorted(rings)),
        deflectable=any(
            (tag.get("m_EE3055BD") or {}).get("m_identifier") == "0x0D800000000003BF"
            for tag in modify.get("m_148F3152") or [] if isinstance(tag, dict)
        ),
        effect=effect.guid if isinstance(effect, expr.Asset) else None,
        projectile_extent=value("m_57DAE1B5", 0.0),
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
    target_limit: int = 1
    hit_entities: set[int] = field(default_factory=set)


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
        self.barriers: dict[int, barriers.Barrier] = {}
        self.next_barrier = barriers.FIRST_ENTITY
        self.now = 0.0
        self.tick = 0
        self.melee_seen: dict[int, dict[tuple[int, int], int]] = {}  # body -> counters by instance/state
        self.genji_strikes: dict[int, tuple] = {}  # body -> (instance, activation, previous centre, victims)
        self.genji_blades: dict[int, dict] = {}  # body -> (instance, tracker) -> (activation, victims)
        self.genji_assists: dict[int, dict] = {}  # victim -> source body -> (Genji player, last damage tick)
        self.stuns: dict[int, int] = {}  # body identity -> exclusive world-tick deadline
        self.frost_hits: dict[int, Frost] = {}
        self.pins: dict[int, int] = {}  # manual Assassin source body -> victim body
        self.assassin_victims: dict[int, assassin_victim.Lease] = {}
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
        barriers.retire_owner(self, player)
        assassin_victim.retire_target(self, player.body)
        self.clear_pins(player.body)
        self.vitals.pop(player.body, None)
        self.history.pop(player.body, None)
        self.replacement_ticks.pop(player.body, None)
        self.melee_seen.pop(player.body, None)
        self.stuns.pop(player.body, None)
        self.frost_hits.pop(player.body, None)
        self.genji_strikes.pop(player.body, None)
        self.genji_blades.pop(player.body, None)
        self.genji_assists.pop(player.body, None)
        for victim, sources in list(self.genji_assists.items()):
            sources.pop(player.body, None)
            if not sources:
                del self.genji_assists[victim]
        # Reconstructed ownership policy: body replacement/leave also retires
        # nonleased authority; it cannot acquire the replacement body's credit.
        self.rockets[:] = [rocket for rocket in self.rockets if rocket.shooter is not player]
        self.respawns[:] = [(when, who) for when, who in self.respawns if who is not player]

    def record_replacement(self, body: int) -> None:
        """Keep the creation boundary independent of the rolling pose history."""
        self.replacement_ticks[body] = self.match.tick
        self.stuns.pop(body, None)
        self.frost_hits.pop(body, None)
        self.clear_pins(body)

    def is_stunned(self, actor, *, tick: int | None = None) -> bool:
        entity = _source_entity(actor)
        at = self.tick if tick is None else tick
        return (entity in self.pins.values()
                or assassin_victim.controlled(self, entity)
                or self.stuns.get(entity, 0) > at
                or self.is_asleep(actor, tick=at)
                or self.frost_progress(actor, tick=at) >= 1.0)

    def is_asleep(self, actor, *, tick: int | None = None) -> bool:
        vitals = self.vitals.get(_source_entity(actor))
        return vitals is not None and (self.tick if tick is None else tick) < vitals.sleep_until

    def frost_progress(self, actor, *, tick: int) -> float:
        frost = self.frost_hits.get(_source_entity(actor))
        return frost.progress(tick) if frost is not None else 0.0

    @staticmethod
    def _write_frost(component, progress: float) -> None:
        if component.entity_var(FROST_PROGRESS).value() != progress:
            component.set(FROST_PROGRESS, progress)

    def _frost_immune(self, target: Target) -> bool:
        component = target.owner.body_script.component
        return self._has_protection(target, DAMAGE_IMMUNITY_STATES) or any(
            (variable := component.entity_var(key, create=False)) is not None and expr.truthy(variable.value())
            for key in (6604, 8440, 9161)
        )

    def _mei_frost_hit(self, shooter, target: Target, tick: int) -> None:
        owner = target.owner
        if isinstance(owner, bots.Bot) or owner.body_script is None or self._frost_immune(target):
            return
        frost = self.frost_hits.setdefault(target.entity, Frost())
        if tick < frost.frozen_until:
            return  # Further hits do not extend an established freeze.
        source = _source_entity(shooter)
        previous, expires = frost.sources.get(source, (0.0, 0))
        progress = min(1.0, max(FROST_INITIAL, expr.f32((previous if tick < expires else 0.0) + FROST_STEP)))
        frost.sources[source] = (progress, tick + FROST_GAP_TICKS)
        if progress >= 1.0:
            frost.sources.clear()
            frost.frozen_until = tick + FROST_FREEZE_TICKS
            self._interrupt(target)
        self._write_frost(owner.body_script.component, frost.progress(tick))

    def clear_pins(self, entity: int) -> None:
        for source, victim in list(self.pins.items()):
            if entity in (source, victim):
                del self.pins[source]

    def _hit_effect(self, target: Target, effect: int | None) -> None:
        """Apply scoped hit effects after damage has reached a living body."""
        if effect not in (FLASHBANG_EFFECT, SLEEP_DART_EFFECT):
            return
        vitals = self.vitals_of(target)
        if vitals.dead:
            return
        if effect == SLEEP_DART_EFFECT:
            floor = self.tick + SLEEP_WAKE_FLOOR_TICKS
            if floor > max(vitals.sleep_until, self.stuns.get(target.entity, 0)):
                vitals.sleep_wake_floor = floor
                vitals.sleep_until = self.tick + SLEEP_TICKS
                self._interrupt(target)
            return
        variables = graph.mode_variables(self.match.game_map.mode_guid) or {}
        duration = 1.7 if expr.truthy(variables.get(FLASHBANG_MODE_FLAG)) else 0.8
        deadline = self.tick + math.ceil(duration / TICK)
        if vitals.sleep_until > self.tick:
            if deadline <= vitals.sleep_until:
                return
            vitals.clear_sleep()
        self.stuns[target.entity] = max(deadline, self.stuns.get(target.entity, 0))
        self._interrupt(target)

    def _interrupt(self, target: Target) -> None:
        """Shared reconstructed command/ability interruption; persistent Q keeps its timer."""
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
                if instance.graph.index == 0x01DB and isinstance(state, nodes.MoveTo) and state.active:
                    #01DB starts movement from its on-start plug, outside the
                    #Ability subgraph. Stop the phase too: a short control must
                    #not merely pause and then resume the old dash destination.
                    state.end(False)
                    self.genji_strikes.pop(target.entity, None)
                if isinstance(state, nodes.Ability):
                    state.presses.clear()
                    # Genji0317's persistent transform has no continue condition
                    # or continuing stack priority. Preserve its authored timer;
                    # the stunned command gate still blocks drawing new swings.
                    # This scoped server control policy is reconstructed.
                    if not (instance.graph.index == 0x0317 and state in getattr(script, "ultimates", ())):
                        state.stop()  # authored exit/cooldown paths for running channels
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
        interception.update_pools(self, self.tick)
        barriers.advance(self, self.tick)
        if self.is_dead(player):
            self.genji_strikes.pop(player.body, None)
            self.genji_blades.pop(player.body, None)
        if self.is_dead(player) or self.is_stunned(player):
            return
        if command is not None:
            for state, number, time, activation in shots:
                try:
                    self._shoot(player, command, state, number, time, activation=activation)
                except Exception:
                    log.exception("[game] %s: %s's shot failed", self.match.label(), player.name)
        try:
            self._weapon_beam(player, command, script)
            self._winston_tesla(player, command, script)
            self._melee(player, command, script)
            self._genji_strike(player, command, script)
            self._genji_blade(player, command, script)
        except Exception:
            log.exception("[game] %s: %s's posed combat failed", self.match.label(), player.name)

    def _weapon_beam(self, player, command, script) -> None:
        """Select a beam target and evaluate its authored ModifyHealth.

        Body capsules, eye origin and one damage sample per posed command are
        server reconstruction. Native selection, lock tolerances and cadence remain open.
        """
        spec = BEAM_STATES.get(player.hero.guid)
        if spec is None:
            return
        graph_index, ray_index, health_index = spec
        instance = next((i for i in script.component.instances.values() if i.graph.index == graph_index), None)
        if instance is None:
            return
        ray = instance.states.get(ray_index)
        if ray is None:
            return
        # Moira's damage state consumes TrackTargets' selection. The separate
        # TrackRay in that graph does not supply the damage target.
        body_only = ray.node.cls == "STUStatescriptStateTrackTargets"
        target_field = "m_8A2905A0" if body_only else "m_target.m_692DFF72"
        range_field = "m_radius" if body_only else "m_F5732403.m_6A79BD98"
        output = expr.lvalue(ray.node.field(target_field))
        if output is None:
            return
        body = shield = None
        if command is not None and ray.active:
            reach = expr.to_float(ray.evaluate(ray.node.field(range_field), watch=False))
            if reach > 0:
                origin = self._eye(player)
                direction = aim(player.mover.yaw, player.mover.pitch)
                wall = ray_world(self._collision(), origin, direction, reach)
                limit = wall if wall is not None else math.inf
                hit = barriers.ray_hit(self, player, origin, direction, min(reach, limit))
                if hit is not None and hit[0] < limit:
                    limit = hit[0]
                    if not body_only:
                        shield = hit[1]
                for target in self._enemies(player):
                    a, b = target.segment()
                    distance = ray_capsule(origin, direction, a, b, target.radius)
                    if distance is not None and distance <= reach and distance < limit:
                        limit, body, shield = distance, target, None
        entity = body.entity if body is not None else shield.entity if shield is not None else 0
        current = instance.find_var(*output).value()
        if expr.to_entity(current) != entity:
            instance.write(output, expr.Entity(entity) if entity else None)
            instance.component.run_now()
        health = instance.states.get(health_index)
        if not entity or health is None or not health.active or expr.to_entity(
            health.evaluate(health.node.field("m_FA3ECC43"), watch=False)
        ) != entity:
            return
        modify = health.node.field("m_modifyHealth")
        rate = expr.to_float(health.evaluate(modify.get("m_amount"), watch=False))
        sample_seconds = script.component.frame_ms / 1000
        amount = rate * sample_seconds
        if shield is not None:
            barriers.damage(self, shield, amount, attacker=player.body)
        elif body is not None:
            loadout = health.evaluate(modify.get("m_loadout"), watch=False)
            reached = self._damage(player, body, amount, False,
                                   loadout.guid if isinstance(loadout, expr.Asset) else None, command.frame)
            if reached and graph_index == 0x0F76:
                self._moira_grasp_heal(player, instance, sample_seconds)

    def _moira_grasp_heal(self, player, instance, sample_seconds):
        """Apply 0F76's active self-heal with the same sample clock as its damage."""
        heal = instance.states.get(45)
        if heal is None or not heal.active or expr.to_entity(
            heal.evaluate(heal.node.field("m_FA3ECC43"), watch=False)
        ) != player.body:
            return
        recipient = next((target for target in self._targets() if target.owner is player), None)
        if recipient is not None:
            rate = expr.to_float(heal.evaluate(heal.node.field("m_modifyHealth.m_amount"), watch=False))
            self._heal_target(player, recipient, self.vitals_of(recipient), rate * sample_seconds)

    def _winston_tesla(self, player, command, script):
        """Feed 00FE's multi-target damage state with a reconstructed visible cone."""
        if player.hero.guid != 0x02E0000000000009:
            return
        instance = next((i for i in script.component.instances.values() if i.graph.index == 0x00FE), None)
        if instance is None:
            return
        track = instance.states.get(10)
        if track is None or not track.active:
            return
        output = expr.lvalue(track.node.field("m_8A2905A0"))
        if output is None:
            return
        targets = []
        offset = track.evaluate(track.node.field("m_05BE62DD"), watch=False) if command is not None else None
        if isinstance(offset, expr.Vec3):
            centre = tuple(a + b for a, b in zip(player.mover.position, offset, strict=True))
            facing = aim(*command.aim())
            reach = expr.to_float(track.evaluate(track.node.field("m_radius"), watch=False))
            angle = expr.to_float(track.evaluate(track.node.field("m_A30B383E"), watch=False))
            threshold = math.cos(math.radians(angle))
            collision = self._collision()
            for target in self._enemies(player):
                a, b = target.segment()
                toward = _sub(_segment_point(centre, a, b), centre)
                distance = math.sqrt(_dot(toward, toward))
                if (distance <= reach + target.radius and _dot(facing, toward) + EPSILON >= threshold * distance
                        and capsule_visible(collision, centre, a, b, target.radius)):
                    targets.append(target)
        selected = tuple(expr.Entity(target.entity) for target in targets)
        if instance.find_var(*output).value() != selected:
            instance.write(output, selected)
            instance.component.run_now()
        damage = instance.states.get(13)
        if not targets or damage is None or not damage.active or damage.evaluate(
            damage.node.field("m_FA3ECC43"), watch=False
        ) != selected:
            return
        modify = damage.node.field("m_modifyHealth")
        rate = expr.to_float(damage.evaluate(modify.get("m_amount"), watch=False))
        amount = rate * script.component.frame_ms / 1000
        loadout = damage.evaluate(modify.get("m_loadout"), watch=False)
        for target in targets:
            self._damage(player, target, amount, False,
                         loadout.guid if isinstance(loadout, expr.Asset) else None, command.frame)

    def _genji_strike(self, player, command, script) -> None:
        """Reconstruct 01DB's enemy tracking during actual MoveTo movement.

        The authored body-centred sphere has radius1.5 and amount50. Sweep it
        between poses and admit each live enemy once per activation. Native
        filter/shape semantics are unverified; dynamic shields remain open.
        """
        if player.hero.guid != 0x02E0000000000029 or command is None:
            return
        instance = next((i for i in script.component.instances.values() if i.graph.index == 0x01DB), None)
        movement = instance.states.get(1) if instance is not None else None
        if movement is None:
            return
        previous = self.genji_strikes.get(player.body)
        identity = (instance.id, movement.counter)
        centre = tuple(a + b for a, b in zip(player.mover.position, (0, 1, 0), strict=True))
        if previous is None or previous[:2] != identity:
            if not movement.active:
                self.genji_strikes.pop(player.body, None)
                return
            previous = (*identity, centre, set())
        start, victims = previous[2:]
        track = instance.states.get(33)
        radius = _number(track.evaluate(track.node.field("m_radius"), watch=False), 0) if track else 0
        modify = instance.state(3).node.field("m_modifyHealth") or {}
        amount = _number(instance.evaluate(modify.get("m_amount")), 0)
        loadout = instance.evaluate(modify.get("m_loadout"))
        delta = _sub(centre, start)
        length = math.sqrt(_dot(delta, delta))
        if not movement.active and length <= EPSILON:
            self.genji_strikes.pop(player.body, None)
            return
        direction = tuple(v / length for v in delta) if length > EPSILON else (0, 0, 1)
        for target in self._enemies(player):
            if target.entity in victims:
                continue
            a, b = target.segment()
            distance = ray_capsule(start, direction, a, b, target.radius + radius)
            if distance is None or distance > length:
                continue
            midpoint = tuple((x + y) / 2 for x, y in zip(a, b, strict=True))
            to_target = _sub(midpoint, centre)
            reach = math.sqrt(_dot(to_target, to_target))
            if reach > EPSILON:
                wall = ray_world(self._collision(), centre, tuple(v / reach for v in to_target), reach)
                if wall is not None and wall < reach:
                    continue
            victims.add(target.entity)
            self._damage(player, target, amount, False,
                         loadout.guid if isinstance(loadout, expr.Asset) else None, command.frame)
        if movement.active:
            self.genji_strikes[player.body] = (*identity, centre, victims)
        else:
            self.genji_strikes.pop(player.body, None)

    def _genji_blade(self, player, command, script) -> None:
        """Reconstruct 0304's short-lived sword target windows, once per victim.

        Use authored range5, angle90 and the evaluated amount (110 in 104319).
        Cone/capsule and static LOS rules are server reconstruction; dynamic
        defenses and original-client geometry require separate acceptance.
        """
        if player.hero.guid != 0x02E0000000000029 or command is None:
            return
        instance = next((i for i in script.component.instances.values() if i.graph.index == 0x0304), None)
        if instance is None:
            return
        records = self.genji_blades.setdefault(player.body, {})
        centre = tuple(a + b for a, b in zip(player.mover.position, (0, 1, 0), strict=True))
        facing = aim(*command.aim())
        for tracking, damage in ((66, 21), (67, 22)):
            track = instance.states.get(tracking)
            if track is None or not track.active:
                continue
            key = (instance.id, tracking)
            activation, victims = records.get(key, (0, set()))
            if activation != track.counter:
                victims = set()
                records[key] = (track.counter, victims)
            radius = _number(track.evaluate(track.node.field("m_radius"), watch=False), 0)
            angle = _number(track.evaluate(track.node.field("m_A30B383E"), watch=False), 0)
            threshold = math.cos(math.radians(angle))
            state = instance.state(damage)
            modify = state.node.field("m_modifyHealth") or {}
            amount = _number(state.evaluate(modify.get("m_amount"), watch=False), 0)
            loadout = state.evaluate(modify.get("m_loadout"), watch=False)
            for target in self._enemies(player):
                if target.entity in victims:
                    continue
                a, b = target.segment()
                if segment_distance(centre, a, b) > radius + target.radius:
                    continue
                midpoint = tuple((x + y) / 2 for x, y in zip(a, b, strict=True))
                delta = _sub(midpoint, centre)
                distance = math.sqrt(_dot(delta, delta))
                if distance > EPSILON:
                    direction = tuple(v / distance for v in delta)
                    if _dot(facing, direction) + EPSILON < threshold:
                        continue
                    wall = ray_world(self._collision(), centre, direction, distance)
                    if wall is not None and wall < distance:
                        continue
                victims.add(target.entity)
                if self._genji_deflects(target, facing):
                    continue  # sword damage is blocked, not reflected
                self._damage(player, target, amount, False,
                             loadout.guid if isinstance(loadout, expr.Asset) else None, command.frame)

    def _genji_deflects(self, target, incoming) -> bool:
        """Frontal melee block while the authored Deflect state is active."""
        return self._genji_deflect_state(target, incoming) is not None

    def _genji_deflect_state(self, target, incoming):
        owner = target.owner
        hero = getattr(owner, "hero", None)
        if getattr(hero, "guid", None) != 0x02E0000000000029 or self.vitals_of(target).dead:
            return None
        script = getattr(owner, "body_script", None)
        if script is None or script.retired:
            return None
        facing = aim(owner.mover.yaw, owner.mover.pitch)
        if _dot(facing, incoming) >= -EPSILON:
            return None
        for instance in script.component.instances.values():
            if instance.graph.index == 0x03A3:
                state = instance.states.get(10)
                if state is not None and state.active:
                    return state
        return None

    def _genji_deflect_contact(self, target, hit, origin, direction, length, *, padding=0.0):
        """Clip an eligible shot against the authored boxes at the server pose."""
        if hit is None or not (hit.deflectable if hit.deflect_override is None else hit.deflect_override):
            return None
        state = self._genji_deflect_state(target, direction)
        if state is None:
            return None
        mover = target.owner.mover
        yaw = mover.yaw * math.tau / 65536
        forward = aim(mover.yaw, mover.pitch)
        right = (math.cos(yaw), 0.0, -math.sin(yaw))
        up = (forward[1] * right[2], forward[2] * right[0] - forward[0] * right[2],
              -forward[1] * right[0])
        # World-up reference for the box basis, with a stable right axis at vertical aim.
        basis = (right, up, forward)
        anchor = _along(target.feet, (0.0, 1.0, 0.0), target.eye)
        delta = _sub(origin, anchor)
        local_origin = tuple(_dot(delta, axis) for axis in basis)
        local_direction = tuple(_dot(direction, axis) for axis in basis)
        best = None
        for box in state.node.field("m_boxes") or ():
            low = state.evaluate(box.get("m_CBA118B2"), watch=False)
            high = state.evaluate(box.get("m_AEF67365"), watch=False)
            if isinstance(low, expr.Vec3) and isinstance(high, expr.Vec3):
                distance = interception.ray_box(local_origin, local_direction, low, high, length, padding=padding)
                if distance is not None and (best is None or distance < best):
                    best = distance
        return best

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
            for target in self._enemies(player):
                shown = self.seen(target, view_tick)
                if shown is None:
                    continue
                a, b = shown.segment()
                if (segment_distance(centre, a, b) - shown.radius <= radius and amount > 0.0
                        and capsule_visible(self._collision(), self._eye(player), a, b, shown.radius)):
                    if self._genji_deflects(target, aim(*command.aim())):
                        continue
                    self._damage(player, target, amount, False, loadout, command.frame)

    def update(self, now: float, tick: int, frames: bool = True) -> None:
        """Each tick: keep where the bodies are, fly the rockets, respawn, send health that changed. The
        other players' body frames only on the ticks that send (`frames`)."""
        if self.match.collision_world is None:
            return
        retained = max(self.tick + 1, tick - COMBAT_CATCH_UP + 1)
        first = tick
        if self.tick and tick > self.tick + 1 and any(
            bot.firing and not bot.dead and isinstance(bot.body_script, BotScript) for bot in self.match.bots
        ):
            first = retained
        for target in self._targets():
            history = self.history.setdefault(target.entity, deque(maxlen=HISTORY))
            history.append((tick, target.feet, target.height, target.eye))
        # Replay the retained combat frames using the most recent poses. Player input
        # accepted just before the scheduler caught up joins only at the endpoint.
        for at in range(first, tick + 1):
            self.now, self.tick = now - (tick - at) * TICK, at
            interception.update_pools(self, at)
            barriers.advance(self, at)
            for rocket in list(self.rockets):
                if at == tick or rocket.last_tick is not None:
                    self._fly(rocket)
            self._bot_weapons(at)
            assassin_victim.advance(self)
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
        current = {target.entity: target for target in self._targets()}
        for entity, frost in list(self.frost_hits.items()):
            target = current.get(entity)
            if target is None or target.owner.body_script is None:
                del self.frost_hits[entity]
                continue
            progress = 0.0 if self._frost_immune(target) else frost.progress(tick)
            self._write_frost(target.owner.body_script.component, progress)
            if not progress:
                del self.frost_hits[entity]
            else:
                for source, (_, expires) in list(frost.sources.items()):
                    if tick >= expires:
                        del frost.sources[source]
        for source, victim in list(self.pins.items()):
            if source not in current or victim not in current:
                del self.pins[source]
        for entity in [key for key, deadline in self.stuns.items() if deadline <= tick]:
            del self.stuns[entity]
        for vitals in self.vitals.values():
            if vitals.sleep_until and tick >= vitals.sleep_until:
                vitals.clear_sleep()
        for table in (self.history, self.vitals, self.replacement_ticks, self.genji_assists, self.stuns):
            for entity in [key for key in table if key not in current]:
                del table[entity]

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
                found = Vitals(target.entity, *pools.body_pools(target.owner.hero.guid))
            self.vitals[target.entity] = found
        return found

    @staticmethod
    def _has_protection(target: Target, states) -> bool:
        """Read the live authored protection stack for this admission policy."""
        owner = target.owner
        profile = states.get(getattr(getattr(owner, "hero", None), "guid", None))
        if profile is None:
            return False
        graph_index, state_index = profile
        script = getattr(owner, "body_script", None)
        if script is None or script.retired:
            return False
        for instance in script.component.instances.values():
            if instance.graph.index == graph_index:
                state = instance.states.get(state_index)
                return state is not None and state.active
        return False

    def _enemies(self, shooter) -> list[Target]:
        free_for_all = self.match.game_map.free_for_all
        source = _source_entity(shooter)
        found = []
        for target in self._targets():
            if target.owner is shooter or target.entity == source:
                continue
            if not free_for_all and target.team == shooter.team:
                continue
            if self.vitals_of(target).dead or self._has_protection(target, PHASE_STATES):
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
        *, activation: tuple[int, int], snapshot: Snapshot | None = None, _direction=None,
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
        if _direction is None:
            directions = ring_directions(state, direction)
            if directions is not None:
                for pellet in directions:
                    self._shoot(shooter, command, state, number, time, activation=activation,
                                snapshot=snapshot, _direction=pellet)
                return
        else:
            direction = _direction
        direct = hit_def(state, node.field("m_6396149F"))
        speed = field("m_projectileSpeed")
        if speed:
            lifetime = field("m_projectileLifetime", 5.0) or 5.0
            reach = field("m_FEC435C6")
            if reach:
                lifetime = min(lifetime, reach / speed)
            blast = hit_def(state, node.field("m_D2D11CE9"))
            entity_def = (node.field("m_projectileEntity") or {}).get("m_entityDef")
            definition = state.evaluate(entity_def, watch=False) if entity_def is not None else None
            target_limit = 1
            if direct is not None and isinstance(definition, expr.Asset):
                if definition.guid == MEI_SPRAY_ENTITY:
                    direct.deflect_override = False
                    target_limit = max(1, min(99, int(field("m_6396149F.m_B4454CD7", 1))))
                elif definition.guid == SLEEP_DART_ENTITY:
                    direct.deflect_override = True
            damaging = (direct is not None and direct.amount > 0.0) or (
                blast is not None and blast.amount > 0.0
            )
            for projectile_direction in genji_fan(state, direction):
                shot = None
                if isinstance(definition, expr.Asset):
                    shot = self.projectiles.fired(
                        shooter, state, shot_frame, definition.guid, lifetime, origin, projectile_direction,
                        activation=activation, snapshot=snapshot,
                    )
                if damaging or shot is not None:
                    self.rockets.append(
                        Rocket(
                            shooter, shooter.team, origin, projectile_direction, speed, lifetime,
                            direct, blast, shot,
                            last_tick=self.tick if npc else None,
                            target_limit=target_limit,
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
        travelled = 0.0
        for bounce in range(8):  # bounded reconstructed multi-Deflect interaction
            absorbed = interception.absorption(self, shooter, direct, origin, direction, reach)
            shield = barriers.ray_hit(self, shooter, origin, direction, reach)
            best = None
            for target in self._enemies(shooter):
                shown = self.seen(target, view_tick)
                if shown is None:
                    continue
                a, b = shown.segment()
                t = ray_capsule(origin, direction, a, b, shown.radius)
                if t is not None and t <= reach and (best is None or t < best[0]):
                    best = (t, target, shown, False)
                t = self._genji_deflect_contact(shown, direct, origin, direction, reach)
                if t is not None and (best is None or t <= best[0]):
                    best = (t, target, shown, True)
            if (shield is not None and (best is None or shield[0] <= best[0])
                    and (absorbed is None or shield[0] < absorbed[0])):
                distance, barrier = shield
                wall = ray_world(self._collision(), origin, direction, distance)
                if wall is None or wall >= distance:
                    barriers.damage(self, barrier, direct.amount * direct.scale(travelled + distance),
                                    attacker=_source_entity(shooter))
                return
            if absorbed is not None and (best is None or absorbed[0] <= best[0]):
                distance, state = absorbed
                wall = ray_world(self._collision(), origin, direction, distance)
                if wall is None or wall >= distance:
                    interception.absorbed(state, direct.amount * direct.scale(travelled + distance))
                return
            if best is None:
                return
            t, target, shown, reflected = best
            wall = ray_world(self._collision(), origin, direction, t)
            if wall is not None and wall < t:
                return
            point = _along(origin, direction, t)
            travelled += t
            if reflected:
                shooter = target.owner
                direction = aim(shooter.mover.yaw, shooter.mover.pitch)
                origin = _along(point, direction, 1e-4)
                reach -= t
                view_tick = self.tick
                shot_frame = self.tick
                continue
            critical = point[1] >= shown.feet[1] + shown.eye - HEAD_BELOW
            amount = direct.amount * direct.scale(travelled) * (direct.critical if critical else 1.0)
            if self._damage(
                shooter, target, amount, critical and direct.critical > 1.0, direct.loadout, shot_frame,
                world_tick=self.tick if npc or bounce else None,
            ):
                self._hit_effect(target, direct.effect)
            return

    def _fly(self, rocket: Rocket) -> None:
        """Sweep elapsed world time against the current bodies and static collision."""
        # Player input can arrive immediately before the scheduler accounts for a gap.
        elapsed = 1 if rocket.last_tick is None else self.tick - rocket.last_tick
        if elapsed <= 0:
            return
        rocket.last_tick = self.tick
        remaining = elapsed * TICK
        while remaining > 0.0:
            duration = min(remaining, rocket.left)
            if duration <= 0.0:
                self._landed(rocket)
                return
            spent = self._sweep_rocket(rocket, duration, expires=duration < remaining)
            if spent is None:
                return
            remaining -= spent

    def _sweep_rocket(self, rocket: Rocket, duration: float, *, expires: bool) -> float | None:
        """Return time consumed by a piercing hit so the same tick can continue."""
        # Sweep the remaining flight even when the range or lifetime ends inside this tick.
        step = rocket.speed * duration
        padding = rocket.direct.projectile_extent if rocket.direct is not None else 0.0
        absorbed = interception.absorption(
            self, rocket.shooter, rocket.direct, rocket.position, rocket.direction, step, padding=padding,
        )
        best = None
        for target in self._enemies(rocket.shooter):
            if target.entity in rocket.hit_entities:
                continue
            a, b = target.segment()
            t = ray_capsule(rocket.position, rocket.direction, a, b, target.radius + padding)
            if t is not None and t <= step and (best is None or t < best[0]):
                best = (t, target, False)
            t = self._genji_deflect_contact(
                target, rocket.direct, rocket.position, rocket.direction, step, padding=padding,
            )
            if t is not None and (best is None or t <= best[0]):
                best = (t, target, True)
        collision = self._collision()
        wall = ray_world(collision, rocket.position, rocket.direction, step)
        if padding > 0:
            swept = collision.sweep_sphere(rocket.position, rocket.direction, step, padding)
            if swept is not None and (wall is None or swept < wall):
                wall = swept
        if wall is not None and (best is None or wall < best[0]):
            best = (wall, None, False)
        shield = barriers.ray_hit(self, rocket.shooter, rocket.position, rocket.direction, step,
                                  padding=padding)
        if (shield is not None and (best is None or shield[0] <= best[0])
                and (absorbed is None or shield[0] < absorbed[0])):
            rocket.flown += shield[0] / rocket.speed
            self._landed(rocket)
            # Reconstructed direct-impact admission. Native splash redistribution
            # around the mesh and beam/projectile-specific receiver filters remain open.
            amount = ((rocket.direct.amount if rocket.direct is not None else 0.0)
                      + (rocket.blast.amount if rocket.blast is not None else 0.0))
            barriers.damage(self, shield[1], amount, attacker=_source_entity(rocket.shooter))
            return
        if absorbed is not None and (best is None or absorbed[0] <= best[0]):
            if rocket in self.rockets:
                self.rockets.remove(rocket)
            if rocket.shot is not None:
                self.projectiles._end(rocket.shot, self.tick, False)
            amount = rocket.direct.amount + (rocket.blast.amount if rocket.blast is not None else 0.0)
            interception.absorbed(absorbed[1], amount)
            return
        if best is None:
            rocket.position = _along(rocket.position, rocket.direction, step)
            rocket.flown += duration
            rocket.left -= duration
            # D72C80 clips on overshoot. An exact endpoint still ends on the next step.
            if expires:
                self._landed(rocket)
            return
        t, struck, reflected = best
        rocket.flown += t / rocket.speed
        point = _along(rocket.position, rocket.direction, t)
        if reflected:
            # Authority transfers without resetting remaining range/lifetime.
            # Retire the original prediction; reflected client visuals remain open.
            if rocket.shot is not None:
                self.projectiles.flown(rocket.shot, rocket.flown)
                rocket.shot = None
            rocket.shooter = struck.owner
            rocket.team = struck.team
            rocket.direction = aim(struck.owner.mover.yaw, struck.owner.mover.pitch)
            rocket.position = _along(point, rocket.direction, 1e-4)
            rocket.left -= t / rocket.speed
            return
        piercing = (struck is not None and rocket.target_limit > 1 and rocket.blast is None
                    and len(rocket.hit_entities) + 1 < rocket.target_limit)
        if not piercing:
            self._landed(rocket)
        if struck is not None and rocket.direct is not None and rocket.direct.amount > 0.0:
            # Sweep time follows the projectile centre. Classify the hit at
            # the unexpanded target surface, as in the native capsule query.
            contact_y = point[1]
            if padding:
                axis_y = _segment_point(point, *struck.segment())[1]
                contact_y = axis_y + (point[1] - axis_y) * struck.radius / (struck.radius + padding)
            critical = contact_y >= struck.feet[1] + struck.eye - HEAD_BELOW
            amount = rocket.direct.amount * (rocket.direct.critical if critical else 1.0)
            if self._damage(
                rocket.shooter, struck, amount, critical and rocket.direct.critical > 1.0,
                rocket.direct.loadout, self.tick,
                world_tick=self.tick,
            ):
                self._hit_effect(struck, rocket.direct.effect)
        if piercing:
            rocket.hit_entities.add(struck.entity)
            rocket.position = point
            spent = t / rocket.speed
            rocket.left -= spent
            return spent
        blast = rocket.blast
        if blast is None or blast.amount <= 0.0:
            return
        for target in self._enemies(rocket.shooter):
            a, b = target.segment()
            distance = max(0.0, segment_distance(point, a, b) - target.radius)
            scalar = blast.splash(distance)
            # Start just outside an impact wall, on the projectile's approach side.
            visible_from = _along(point, rocket.direction, -0.0001) if wall is not None else point
            if scalar <= 0.0 or not capsule_visible(self._collision(), visible_from, a, b, target.radius):
                continue
            if self._damage(
                rocket.shooter, target, blast.amount * scalar, False, blast.loadout, self.tick,
                world_tick=self.tick,
            ):
                self._hit_effect(target, blast.effect)

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
            self._heal_target(healer, target, vitals, amount)

    def _heal_target(self, healer, target, vitals, amount):
        given = vitals.heal(amount)
        if given > 0:
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
        *, world_tick: int | None = None,
    ) -> bool:
        """Apply body damage and report whether its hit effect reached the body."""
        vitals = self.vitals_of(target)
        if vitals.dead or amount <= 0.0 or self._has_protection(target, DAMAGE_IMMUNITY_STATES):
            return False
        if barriers.protect_body(self, target, amount, attacker=_source_entity(shooter)):
            return False
        dealt = vitals.damage(amount)
        if dealt <= 0.0:
            return False
        killed = vitals.now[HEALTH_POOL] <= EPSILON
        if killed:
            vitals.now[HEALTH_POOL] = 0.0
            vitals.died_at = self.now
            vitals.death_tick = self.match.tick if world_tick is None else world_tick
            vitals.killer = _source_entity(shooter)
            vitals.clear_sleep()
        elif self.tick < vitals.sleep_until:
            wake = max(vitals.sleep_wake_floor, self.tick + SLEEP_WAKE_RECOVERY_TICKS)
            vitals.sleep_until = min(vitals.sleep_until, wake)
        if not isinstance(shooter, bots.Bot):
            flags = (CRITICAL if critical else 0) | (KILLING if killed else 0)
            self._tell_hit(shooter, target, dealt, flags, frame)
            shooter.stats.add(stats.HERO_DAMAGE, dealt)
            enemy = target.owner is not shooter and (
                self.match.game_map.free_for_all or target.team != shooter.team
            )
            script = getattr(shooter, "body_script", None)
            if enemy and script is not None:
                script.gain_ult_charge(dealt)
            if enemy and getattr(getattr(shooter, "hero", None), "guid", None) == 0x02E0000000000029:
                tick = self.match.tick if world_tick is None else world_tick
                self.genji_assists.setdefault(target.entity, {})[shooter.body] = (shooter, tick)
        victim = target.owner
        if killed:
            self._genji_eliminations(target, shooter, vitals.death_tick)
        if killed and isinstance(victim, bots.Bot):
            # The NPC death script owns its terminal health change and death event ordering.
            self._bot_died(shooter, victim, critical, loadout, vitals.death_tick)
            return True
        self._sync_body_health(victim, vitals)
        if killed:
            self._died(shooter, victim, critical, loadout)
        elif loadout == MEI_SPRAY_LOADOUT:
            self._mei_frost_hit(shooter, target, self.tick if world_tick is None else world_tick)
        return True

    def _genji_eliminations(self, target, killer, tick):
        """Deliver authored reset entries for reconstructed kill/damage assists.

        0093/parameter15 and169F/parameter45 both lead through the scoped body
        predicate to SetVar61=0. Full native elimination event fan-out, credit
        windows and payload/filter semantics remain unverified.
        """
        entity = target.entity
        for body, (player, damaged) in self.genji_assists.pop(entity, {}).items():
            if (not 0 <= tick - damaged <= GENJI_ASSIST_TICKS or player.body != body
                    or player.gone or player not in self.match.players
                    or player.hero.guid != 0x02E0000000000029):
                continue
            script = player.body_script
            if script is None or script.retired or script.component.fault is not None:
                continue
            if player is not killer:
                player.stats.add(stats.ELIMINATIONS)
                self._kill_notice(player, target.owner)
            instance = next((i for i in script.component.instances.values() if i.graph.index == 0x01DB), None)
            if instance is not None:
                message, parameter = (
                    (0x0240000000000093, 0x15) if player is killer else (0x024000000000169F, 0x45)
                )
                runtime.send_instance_message(instance, message, {parameter: expr.Entity(entity)}, body)
                script.component.run_now()

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
        assassin_victim.retire_target(self, victim.body)
        self.clear_pins(victim.body)
        """A player died: the notices, his body script's "died" (the kill feed line), and the respawn."""
        self.stuns.pop(victim.body, None)
        if self.frost_hits.pop(victim.body, None) is not None and victim.body_script is not None:
            self._write_frost(victim.body_script.component, 0.0)
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
            components = vitals.component(vitals.most)
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
