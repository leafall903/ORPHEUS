"""Shots, damage, death and respawn: what the server decides and what it tells the clients.

What the client needs (read in the client unless marked):
- Health is component 51 (world.pool): an op-2 update of a pool's field changes the bar on every client
  that has the body. A body whose health pools (type 0) add up to 0 with a maximum above 0 is dead
  (0x7FF789ADBF00): the client then sets flag 0x2000 on its movement state (0x7FF7896A4F9A) and the 3P
  animation takes the dead pose 004.015 (0x7FF789B24889); flag 0x2000 in the records does the same.
- ClientInCombat 25001 {entity, u32 damage, u8 flags} u32 frame (104319 RVA 6C2D40) tells the shooter
  of a hit: it goes in his component 59's hit list (+0x12D0, records in frame order, not on his own
  body), which shows the target's health bar for about 5 s (RVA 6C88C1: int(1e6 / quanta) * 5 frames) and
  feeds the hit marker and its sound (event 7). The 104319 handler routes bit 0 to the critical counter at
  +0x124 instead of the ordinary counter at +0x120. Bit 1 independently increments the kill counter at +0x128.
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
- The attacker's ultimate: its body script credits the meter for the damage dealt and the healing done,
  and once more per elimination (ult_charge.py; the rates are data, the client's meter only shows the var).
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
import os
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass

from ow174.game import bots, contacts, observers, pools, projectiles, stats, ult_charge, voicelines, world
from ow174.game.bits import BitWriter
from ow174.game.collision import MOVER, RayWorld, World
from ow174.game.commands import Command
from ow174.game.content import JUNKENSTEIN_SHOCKING_SURPRISE, heroes
from ow174.game.mover import mover_data
from ow174.game.props import ModelPose
from ow174.game.script import expr, graph, nodes, runtime
from ow174.game.script.bot import DIED, BotScript
from ow174.game.script.driver import OWNER_HEALTH, Snapshot
from ow174.game.statescript import InstanceWire, StateWire, remote_full_frame
from ow174.game.world import ARMOUR_POOL, HEALTH_POOL, OP_DESTROY, POOLS, SHIELDS_POOL

_NO_COMBAT_NOTICES = os.environ.get("OW174_NO_COMBAT_NOTICES") == "1"  # ablation lever (this run only)

log = logging.getLogger("ow174.game")

HIT = 25001  # to the shooter: a hit
DEATH_NOTICE = 25004  # to the dead player: who killed him
KILL_NOTICE = 25005  # to the killer: whom he killed
KILLED_BY = 2  # 25004 +0x78: "you were killed by" (0F7B.025); anything else "you died" (0F7C.025)
CRITICAL, KILLING = 0x01, 0x02  # 25001 hit indicator flags
DEAD = 0x2000  # movement-state flag: dead (the 3P anim's dead pose)
# 003E.025's parameters, by the low 16 bits of their 026 identifiers (01CF rid4).
KILLER, CRIT, LOADOUT, ASSISTS = 0x0012, 0x0095, 0x03E6, 0x03E7
MODE_UI = 0x0CB6  # the mode's client UI graph: the kill feed 0DEA and the notices 1FC0 (client-only)
# A mode's own graph, where the server carries it on the game mode entity: (the graph, the remote slot
# of its state that posts 06DF.025, the state the mode UI hangs from). The client holds its scoreboard
# shut until that message is posted (docs/research/tab-board.md). Control's graph has no such host
# state: its client-only state 162 starts 1FEE, whose state 8 starts the mode UI, on the client alone.
# Assault, Escort and Hybrid (0F4A) are left out: with a board state on and no phase state the client
# draws an "UNSPECIFIED 0.0" objective header.
MODE_GRAPHS = {
    0x0230000000000007: (0x0CE8, 1, 14),  # Elimination
    0x0230000000000009: (0x0D0B, 6, 3),  # Capture the Flag
    0x0230000000000010: (0x0D76, 2, 15),  # Skirmish
    0x0230000000000017: (0x0EC2, 1, None),  # Control
    0x0230000000000018: (0x13C0, 4, 21),  # the Practice Range
    0x023000000000001D: (0x0F60, 0, 13),  # Deathmatch
    0x023000000000001E: (0x0F73, 0, 10),  # Team Deathmatch
    0x0230000000000046: (0x1A59, 6, 2),
    0x023000000000005A: (0x13C0, 4, 21),  # the Practice Range's other mode
    0x0230000000000070: (0x0F73, 0, 10),  # Team Deathmatch's other mode
}

EYE = 1.55  # m: STUFirstPersonComponent m_E29993A5, its default (OWLib) and Soldier's (DATA; unverified: eye)
#104319 STUFirstPersonComponent: m_E29993A5 standing, m_816B830F crouched.
EYE_STANDING = {0x04000000000002A3: 1.425}
EYE_CROUCHED = {0x04000000000003CF: 1.085, 0x04000000000002A3: 0.9975}
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
RIALTO_RETRIBUTION = 0x080000000000079F
TALON_HIT_REACTION = 0x224E
TALON_DAMAGE_REACTION = 0.48  # 224E/0 plays for the supplied duration minus 0.2 s


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


def _communication_screen_score(target: "Target", eye, forward, right, up) -> float:
    # C97F99 projects opposite corners of an upright player's bounds.
    lower = _sub(_along(target.feet, right, -target.radius), eye)
    upper = _sub(_along(_along(target.feet, right, target.radius), (0, 1, 0), target.height), eye)
    lower_depth, upper_depth = _dot(lower, forward), _dot(upper, forward)
    if min(lower_depth, upper_depth) < 1.1920928955078125e-6:
        return 100.0  # C98185 projection failure

    # The centered coordinates are twice the unit-viewport coordinates.
    x1, x2 = _dot(lower, right) / lower_depth, _dot(upper, right) / upper_depth
    y1, y2 = _dot(lower, up) / lower_depth, _dot(upper, up) / upper_depth
    dx = max(0.0, min(x1, x2), -max(x1, x2))
    dy = max(0.0, min(y1, y2), -max(y1, y2))
    return min(1.0, math.hypot(dx, dy) * math.sqrt(0.5))


def _communication_visible(
    rays: World | RayWorld, origin, target: "Target", category: int, pose: ModelPose | None, actor_shapes=(),
) -> bool:
    # CA408C, CA41F6 and CA4227 query the primary anchor, lower body and secondary anchor in order.
    for key, height_fraction in ((0x0DC0000000000001, None), (None, 0.3), (0x0DC00000000001CD, 0.6)):
        point = pose.hardpoint_position(key) if pose is not None and key is not None else None
        if point is None:
            if height_fraction is None:
                continue
            point = _along(target.feet, (0.0, 1.0, 0.0), target.height * height_fraction)
        delta = _sub(point, origin)
        distance = math.hypot(*delta)
        if distance <= EPSILON:
            return True
        direction = tuple(axis / distance for axis in delta)
        hit = rays.cast_ray(origin, direction, distance, category)
        nearest = hit.distance if hit is not None else distance
        owner = hit.attachment.entity if hit is not None and hit.attachment is not None else None
        for entity, matrix, geometry in actor_shapes:
            crossing = _posed_shape_distance(origin, direction, nearest, matrix, geometry)
            if crossing is not None and (crossing < nearest or (hit is None and owner is None)):
                nearest, owner = crossing, entity
        if owner == target.entity or (owner is None and hit is None):
            return True
    return False


def _posed_shape_distance(origin, direction, reach: float, matrix, geometry) -> float | None:
    """Intersect the world segment against an admitted bone-local shape."""
    x, y, z = matrix[:3], matrix[4:7], matrix[8:11]
    rows = (contacts.cross(y, z), contacts.cross(z, x), contacts.cross(x, y))
    determinant = _dot(x, rows[0])
    if not math.isfinite(determinant) or abs(determinant) <= EPSILON:
        return None
    relative = _sub(origin, matrix[12:15])
    local_origin = tuple(_dot(row, relative) / determinant for row in rows)
    local_direction = tuple(_dot(row, direction) / determinant for row in rows)
    if not all(math.isfinite(value) for value in (*local_origin, *local_direction)):
        return None
    kind = geometry[0]
    if kind == "hull":
        enter, leave = 0.0, reach
        for nx, ny, nz, plane in geometry[4]:
            normal = (nx, ny, nz)
            gap = plane - _dot(normal, local_origin)
            speed = _dot(normal, local_direction)
            if abs(speed) <= EPSILON:
                if gap < 0.0:
                    return None
                continue
            if speed < 0.0:
                enter = max(enter, gap / speed)
            else:
                leave = min(leave, gap / speed)
            if enter > leave:
                return None
        return enter
    if kind not in ("capsule", "sphere"):
        return None
    speed = math.hypot(*local_direction)
    if speed <= EPSILON:
        return None
    axis = tuple(value / speed for value in local_direction)
    a = geometry[2]
    b = geometry[3] if kind == "capsule" else a
    crossing = ray_capsule(local_origin, axis, a, b, geometry[-1])
    return crossing / speed if crossing is not None and crossing <= reach * speed else None


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


def capsule_visible(
    collision: World | RayWorld | None, origin, a, b, radius: float, category: int = MOVER,
    *, target_entity: int | None = None,
) -> bool:
    """Test the nearest capsule surface, allowing a hit owned by the intended target."""
    ab, ap = _sub(b, a), _sub(origin, a)
    length = _dot(ab, ab)
    share = 0.0 if length <= EPSILON else max(0.0, min(1.0, _dot(ap, ab) / length))
    toward = _sub(_along(a, ab, share), origin)
    distance = math.sqrt(_dot(toward, toward))
    if distance <= radius + EPSILON:
        return True
    reach = distance - radius
    direction = tuple(axis / distance for axis in toward)
    hit = collision.cast_ray(origin, direction, reach, category) if collision is not None else None
    if hit is None or hit.distance + EPSILON >= reach:
        return True
    return hit.attachment is not None and hit.attachment.entity == target_entity


def ray_world(
    collision: World | RayWorld | None, origin, direction, length: float, category: int = MOVER,
) -> float | None:
    """The distance to the first static hit, for callers that only need occlusion."""
    hit = collision.cast_ray(origin, direction, length, category) if collision is not None else None
    return hit.distance if hit is not None else None


# --- health -------------------------------------------------------------------------------------------


@dataclass
class WaveHealthPool:
    capacity: float
    current: float


class Vitals:
    """A body's pools as component 51 has them: health, armour, shields (world.health's ids and fields)."""

    def __init__(self, entity: int, health: float, armour: float = 0.0, shields: float = 0.0) -> None:
        self.entity = entity
        self.most = {HEALTH_POOL: float(health), ARMOUR_POOL: float(armour), SHIELDS_POOL: float(shields)}
        self.now = dict(self.most)
        self.temporary_shields: dict[int, tuple[int, float, float]] = {}
        self.ids = {}
        pool_id = 1
        for kind in (SHIELDS_POOL, ARMOUR_POOL):  # world.health numbers them so
            if self.most[kind] > 0:
                self.ids[kind] = pool_id
                pool_id += 1
        self.ids[HEALTH_POOL] = pool_id
        self.wave_health: WaveHealthPool | None = None
        self.wave_health_id = 0
        self.changed: set[int] = set()  # pools that changed since the last update went out
        self.died_at: float | None = None
        self.death_tick: int | None = None
        self.killer: int | None = None

    @property
    def dead(self) -> bool:
        return self.died_at is not None

    @property
    def total(self) -> float:
        bonus = self.wave_health.current if self.wave_health is not None else 0.0
        return sum(self.now.values()) + bonus + sum(now for _, _, now in self.temporary_shields.values())

    @property
    def capacity(self) -> float:
        return self.normal_health(maximum=True) + sum(most for _, most, _ in self.temporary_shields.values())

    def set_temporary_shield(self, pool_id: int, amount: float) -> None:
        for slot, (existing, _, _) in self.temporary_shields.items():
            if existing == pool_id:
                if amount <= 0.0:
                    self.temporary_shields[slot] = (0, 0.0, 0.0)
                    self.changed.add(SHIELDS_POOL)
                return
        if amount <= 0.0 or self.dead:
            return
        # Slot zero of this kind is reserved for the body's permanent shield.
        for slot in range(1, POOLS):
            if not self.temporary_shields.get(slot, (0, 0.0, 0.0))[0]:
                self.temporary_shields[slot] = (pool_id, amount, amount)
                self.changed.add(SHIELDS_POOL)
                return

    def clear_temporary_shields(self) -> None:
        for slot, (pool_id, _, _) in self.temporary_shields.items():
            if pool_id:
                self.temporary_shields[slot] = (0, 0.0, 0.0)
                self.changed.add(SHIELDS_POOL)

    def add_wave_health(self, amount: float):
        """Add one state-owned health record without modifying the body's base pools."""
        if self.dead or self.wave_health is not None or not math.isfinite(amount) or amount <= 0:
            raise ValueError("invalid or overlapping wave health pool")
        pool = WaveHealthPool(amount, amount)
        self.wave_health_id = max(self.wave_health_id, self.ids[HEALTH_POOL]) + 1
        self.wave_health = pool
        self.changed.add(HEALTH_POOL)

        def remove():
            if self.wave_health is pool:
                self.wave_health = None
                self.changed.add(HEALTH_POOL)

        return remove

    def pool_current(self, kind: int) -> float:
        bonus = self.wave_health
        return self.now.get(kind, 0.0) + (bonus.current if kind == HEALTH_POOL and bonus is not None else 0)

    def normal_health(self, maximum: bool) -> float:
        """Read the normal component-51 records in wire order with float32 accumulation."""
        pools = self.most if maximum else self.now
        total = 0.0
        for kind in (HEALTH_POOL, ARMOUR_POOL, SHIELDS_POOL):
            if kind in self.ids:
                total = expr.f32(total + expr.f32(pools[kind]))
                if kind == HEALTH_POOL and (bonus := self.wave_health) is not None:
                    total = expr.f32(total + expr.f32(bonus.capacity if maximum else bonus.current))
        return total

    def damage(self, amount: float, *, armour_scalar: float | None = None) -> float:
        """Consume temporary shields, shields, armour and health in order.

        By default armour subtracts ARMOUR_BLOCK, up to half (unverified).
        Return the amount lost across all pools.
        """
        left = max(0.0, amount)
        taken = 0.0
        for slot, (pool_id, most, current) in self.temporary_shields.items():
            take = min(left, current)
            if take > 0.0:
                self.temporary_shields[slot] = (pool_id, most, current - take)
                self.changed.add(SHIELDS_POOL)
                left -= take
                taken += take
        for kind in (SHIELDS_POOL, ARMOUR_POOL, HEALTH_POOL):
            if left <= 0.0:
                break
            if kind == ARMOUR_POOL and self.now[kind] > 0.0:
                left = (max(left - ARMOUR_BLOCK, left / 2) if armour_scalar is None
                        else left * armour_scalar)
            if kind == HEALTH_POOL and (bonus := self.wave_health) is not None:
                take = min(left, bonus.current)
                if take > 0:
                    bonus.current -= take
                    left -= take
                    taken += take
                    self.changed.add(kind)
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
            if kind == HEALTH_POOL and (bonus := self.wave_health) is not None:
                give = min(left, bonus.capacity - bonus.current)
                if give > 0:
                    bonus.current += give
                    left -= give
                    self.changed.add(kind)
        return max(0.0, amount) - left

    def component(self, kinds) -> dict:
        """Component 51 with these pools' fields set (the others are left as they are)."""
        parts: list = [None] * (3 * POOLS)
        for kind in kinds:
            if kind in self.ids:
                parts[kind * POOLS] = world.pool(self.ids[kind], kind, self.most[kind], self.now[kind])
            if kind == HEALTH_POOL and self.wave_health_id:
                bonus = self.wave_health
                parts[1] = (world.pool(self.wave_health_id, kind, bonus.capacity, bonus.current)
                            if bonus is not None else world.pool(0, kind, 0, 0))
        for slot, (pool_id, most, current) in self.temporary_shields.items():
            parts[SHIELDS_POOL * POOLS + slot] = world.pool(pool_id, SHIELDS_POOL, most, current)
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
    tags: tuple[int, ...] = ()
    ally_heal: float = 0.0
    projectile_headshot: bool = False

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
    from ow174.game import mccree

    def value(name, default=None):
        field = cfg.get(name)
        return default if field is None else _number(state.evaluate(field, watch=False), default)

    modify = cfg.get("m_modifyHealth") or {}
    amount = _number(state.evaluate(modify.get("m_amount"), watch=False), 0.0) if modify else 0.0
    loadout = state.evaluate(modify.get("m_loadout"), watch=False) if modify.get("m_loadout") else None
    effect = state.evaluate(modify.get("m_277A4ECA"), watch=False) if modify.get("m_277A4ECA") else None
    tags = tuple(
        tag.guid
        for entry in modify.get("m_148F3152") or ()
        if isinstance(entry, dict)
        and isinstance(tag := state.evaluate(entry.get("m_EE3055BD"), watch=False), expr.Asset)
    )
    rings = []
    for ring in cfg.get("m_0D30E40C") or []:  # WeaponProjectileExplosionRing {m_radius, m_B399CACB}
        if isinstance(ring, dict):
            radius = (
                state.evaluate(ring.get("m_radius"), watch=False) if ring.get("m_radius") is not None else 0.0
            )
            scalar = ring.get("m_B399CACB")
            scalar = state.evaluate(scalar, watch=False) if scalar is not None else 0.0
            rings.append((_number(radius, 0.0), _number(scalar, 0.0)))
    graph_index, node_index = state.instance.graph.index, state.index
    icicle = graph_index == 0x05E6 and node_index == 16
    return HitDef(
        amount=amount,
        loadout=loadout.guid if isinstance(loadout, expr.Asset) else None,
        falloff_start=value("m_EAD7F104"),
        falloff_end=value("m_157E5BC5"),
        falloff_floor=value("m_72A81154"),
        critical=value("m_A872C70E", 2.0 if icicle else mccree.critical_default(state)),
        rings=tuple(sorted(rings)),
        effect=effect.guid if isinstance(effect, expr.Asset) else None,
        tags=tags,
        # Reconstructed for the two authored Biotic Rifle volleys.
        ally_heal=amount if graph_index == 0x0B94 and node_index in (5, 44) else 0.0,
        projectile_headshot=icicle,
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
        middle = self.height * 0.5
        return (x, y + min(self.radius, middle), z), (x, y + max(self.height - self.radius, middle), z)


@dataclass(frozen=True)
class SplitVolley:
    directions: tuple
    speed: float
    lifetime: float
    direct: HitDef


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
    gravity: float = 0.0  # authored projectile acceleration; zero preserves linear projectiles
    velocity: tuple | None = None  # stored velocity for accelerated flight
    detonate_on_expiry: bool = False  # declared Flashbang range/lifetime-end policy
    scale_direct: bool = False  # apply the hit definition's distance falloff
    split_on_expiry: SplitVolley | None = None


class Combat:
    """The match's health, hits, deaths and respawns."""

    def __init__(self, match) -> None:
        self.match = match
        self.defender_damage_scale = 1.0
        self.model_pose_for: Callable[[int], ModelPose | None] = self._wheel_model_pose
        self.wheel_actor_admits: Callable[[int, int, int, int, int], bool] | None = (
            self._wheel_base_actor_admits
        )
        self.vitals: dict[int, Vitals] = {}  # body or bot entity -> its health
        self.history: dict[int, deque] = {}  # entity -> (tick, feet, height, eye) of the last HISTORY ticks
        self.replacement_ticks: dict[int, int] = {}  # replacement body -> authoritative world tick
        self.rockets: list[Rocket] = []
        self.respawns: list[tuple[float, object]] = []  # (when, a player or a bot)
        self.deadeye_notices = []  # (killer, victim, script, root, shot frame, deadline)
        self.next_bot = bots.BOT_ENTITY + len(match.bots)
        self.now = 0.0
        self.tick = 0
        self.melee_seen: dict[int, dict[tuple[int, int], int]] = {}  # body -> counters by instance/state
        self.tesla_frames: dict[int, int] = {}
        self.stuns: dict[int, int] = {}  # body identity -> exclusive world-tick deadline
        self.npc_hit_reactions: dict[int, tuple[int, runtime.Instance]] = {}
        self.pins: dict[int, int] = {}  # manual Assassin source body -> controlled victim body
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
        self.vitals.pop(player.body, None)
        self.history.pop(player.body, None)
        self.replacement_ticks.pop(player.body, None)
        self.melee_seen.pop(player.body, None)
        self.tesla_frames.pop(player.body, None)
        self.stuns.pop(player.body, None)
        self.clear_pins(player.body)
        self.deadeye_notices[:] = [entry for entry in self.deadeye_notices if entry[0] is not player]
        # Flight belongs to the retiring body, including projectiles synchronized without leases.
        self.rockets[:] = [rocket for rocket in self.rockets if rocket.shooter is not player]
        self.respawns[:] = [(when, who) for when, who in self.respawns if who is not player]

    def record_replacement(self, body: int) -> None:
        """Keep the creation boundary independent of the rolling pose history."""
        self.replacement_ticks[body] = self.match.tick
        self.stuns.pop(body, None)
        self.clear_pins(body)

    def cancel_npc_projectiles(self, bot: bots.Bot) -> None:
        self.rockets[:] = [rocket for rocket in self.rockets if rocket.shooter is not bot]
        self.projectiles.cancel_npc(bot, self.match.tick)

    def is_stunned(self, actor, *, tick: int | None = None) -> bool:
        entity = _source_entity(actor)
        return (entity in self.pins.values()
                or self.stuns.get(entity, 0) > (self.tick if tick is None else tick))

    def clear_pins(self, entity: int) -> None:
        for source, victim in list(self.pins.items()):
            if entity in (source, victim):
                self.release_pin(source)

    def release_pin(self, source: int) -> None:
        victim = self.pins.pop(source, None)
        if victim is not None:
            from ow174.game import assassin

            assassin.detach_victim(self.match, source, victim)

    def _hit_effect(self, target: Target, effect: int | None, *, shooter=None) -> None:
        """Apply the reconstructed Flashbang interruption and the scoped Talon reaction."""
        if effect != FLASHBANG_EFFECT or self.vitals_of(target).dead:
            return
        variables = graph.mode_variables(self.match.game_map.mode_guid) or {}
        duration = 1.7 if expr.truthy(variables.get(FLASHBANG_MODE_FLAG)) else 0.8
        deadline = self.tick + math.ceil(duration / TICK)
        self.stuns[target.entity] = max(deadline, self.stuns.get(target.entity, 0))
        self._interrupt(target)
        self._begin_npc_hit_reaction(target, duration, self.stuns[target.entity], shooter=shooter)

    def _begin_npc_hit_reaction(
        self, target: Target, duration: float, deadline: int, *, restart: bool = True, shooter=None,
    ) -> None:
        bot = target.owner
        if (self.match.game_map.map_guid != RIALTO_RETRIBUTION or not isinstance(bot, bots.Bot)
                or bot.spawner.get('manual_spawn') is not True or bot.body_script is None):
            return
        definition = graph.graph(TALON_HIT_REACTION)
        node = definition.node(0) if definition is not None else None
        if node is None or node.cls != 'STUStatescriptStateAnim' or node.remote != 0:
            return
        current = self.npc_hit_reactions.get(target.entity)
        if not restart and current is not None and current[1].node_state(node).active:
            return
        self._end_npc_hit_reaction(target.entity)
        component = bot.body_script.component
        # 224E/12 chooses floor(random(0, 4)). Only the tested presentation
        # branch is activated. The complete 224E entry path owns other effects.
        instance = component.create(definition, values={
            (expr.INSTANCE, 215): duration,
            (expr.INSTANCE, 1934): component.rng.randrange(4),
        }, init=False)
        instance.initialized = True
        instance.initialized_frame = component.frame
        instance.disabled_start_entries.update(entry.pos for entry in definition.entries if entry is not None)
        self.npc_hit_reactions[target.entity] = (deadline, instance)
        instance.node_state(node).begin()
        voicelines.talon_hit(shooter, bot)

    def _end_npc_hit_reaction(self, entity: int) -> None:
        reaction = self.npc_hit_reactions.pop(entity, None)
        if reaction is None:
            return
        _, instance = reaction
        if not instance.stopped:
            instance.component.destroy(instance)

    def _interrupt(self, target: Target) -> None:
        script = getattr(target.owner, "body_script", None)
        if script is None:
            return
        component = script.component
        if isinstance(target.owner, bots.Bot):
            from ow174.game import assassin

            component.discard_button_presses()
            assassin.bind(component.world, component, None)
        component.edges([(value, False) for value in tuple(component.held)])
        for instance in list(component.instances.values()):
            for state in list(instance.states.values()):
                if isinstance(state, nodes.Ability):
                    state.presses.clear()
                    state.stop()  # use authored exit/cooldown paths for running channels
        component.run_now()
        component.shots.clear()
        if hasattr(component.world, "velocity_requests"):
            component.world.velocity_requests.clear()

    def spawned(self, player) -> None:
        """The player's client has the world: the game mode entity gets its UI graph (mode_frame). While
        heroes are assembled the countdown's frames carry it."""
        host = self.match.scene_host
        if player.client is not None and not self.match.assembling() and not (host and host.client_ready):
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
        component = script.component
        shots, component.shots[:] = list(component.shots), []
        damage = tuple(component.damage_reports)
        component.damage_reports.clear()
        if self.is_dead(player) or self.is_stunned(player):
            return
        if command is not None:
            if damage:
                try:
                    self._apply_tracked_damage(player, command.frame, damage)
                except Exception:
                    log.exception("[game] %s: %s's tracked damage failed", self.match.label(), player.name)
            for state, number, time, activation in shots:
                try:
                    self._shoot(player, command, state, number, time, activation=activation)
                except Exception:
                    log.exception("[game] %s: %s's shot failed", self.match.label(), player.name)
        try:
            self._melee(player, command, script)
        except Exception:
            log.exception("[game] %s: %s's melee failed", self.match.label(), player.name)
        if command is not None:
            try:
                self._tesla(player, command, script)
            except Exception:
                log.exception("[game] %s: %s's Tesla Cannon failed", self.match.label(), player.name)

    def _apply_tracked_damage(self, player, frame: int, reports) -> None:
        """Apply active state reports after the body VM has finished its frame."""
        enemies = {target.entity: target for target in self._enemies(player)}
        for state, activation, entities, amount, loadout, tags in reports:
            if not state.active or state.counter != activation:
                continue
            for entity in entities:
                target = enemies.get(entity)
                if target is None or entity in state.hit_entities or self.vitals_of(target).dead:
                    continue
                state.hit_entities.add(entity)
                self._damage(player, target, amount, False, loadout, frame, hit_tags=tags)

    def _tesla(self, player, command, script) -> None:
        """Reconstruct 00FE's continuous ModifyHealth using the actual active states.

        DATA: st10 searches eight metres, angle var243=40; st13 amount=60.
        Inference: angle is a cone half-angle and amount is damage per second.
        Native target payloads, barriers and status filters are still unaccepted.
        """
        if player.hero.guid != 0x02E0000000000009 or command is None:
            return
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
            for target in self._enemies(player):
                shown = self.seen(target, view_tick)
                if shown is None:
                    continue
                a, b = shown.segment()
                # Aim at the closest capsule-axis point in height. Capsule radius
                # gives targets at the cone boundary their physical width.
                point = (a[0], max(a[1], min(b[1], origin[1])), a[2])
                offset = _sub(point, origin)
                distance = math.sqrt(_dot(offset, offset))
                if distance - shown.radius > reach:
                    continue
                forward = _dot(offset, direction)
                lateral = math.sqrt(max(0.0, distance * distance - forward * forward))
                if forward < 0 or lateral > forward * math.tan(math.radians(angle)) + shown.radius:
                    continue
                if distance > EPSILON:
                    ray = tuple(value / distance for value in offset)
                    wall = ray_world(self._collision(), origin, ray, distance)
                    if wall is not None and wall < max(0.0, distance - shown.radius):
                        continue
                self._damage(player, target, definition.amount * player.quanta / 1e6,
                             False, definition.loadout, command.frame, armour_scalar=0.8)

    def _melee(self, player, command, script) -> None:
        """Quick melee: 0043 st4 (StateModifyHealth) hits what its st5 TrackTargets finds: a sphere of
        m_radius 1.0 at (0, 0, 1.5) from the eye (DATA; unverified: along the aim), #476 = 30 damage (DATA),
        once per activation of st4. The server cannot run TrackTargets itself (the VM finds nothing)."""
        from ow174.game import assassin, mccree

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
                    self._damage(player, target, amount, False, loadout, command.frame,
                                 hit_reaction=player.hero.guid == mccree.HERO)
                    assassin.rescue_with_melee(self, player, target)

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
            for rocket in list(self.rockets):
                if at == tick or rocket.last_tick is not None:
                    self._fly(rocket)
            self._bot_weapons(at)
        self.projectiles.update(tick, first_tick=retained)
        self._flush_deadeye_notices()
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
        for entity, (deadline, _) in tuple(self.npc_hit_reactions.items()):
            if entity not in current or deadline <= tick:
                self._end_npc_hit_reaction(entity)
        for entity in [key for key, deadline in self.stuns.items() if deadline <= tick]:
            del self.stuns[entity]
        for table in (self.history, self.vitals, self.replacement_ticks, self.stuns):
            for entity in [key for key in table if key not in current]:
                del table[entity]
        for source, victim in list(self.pins.items()):
            if source not in current or victim not in current:
                self.release_pin(source)

    # --- targets -----------------------------------------------------------------------------------

    def _targets(self) -> list[Target]:
        found = []
        for player in self.match.players:
            if not (player.has_body and player.spawned and player.client is not None):
                continue
            data = player.mover.data
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
        return self.body_vitals(target.entity, target.owner)

    def body_vitals(self, entity: int, owner) -> Vitals:
        found = self.vitals.get(entity)
        if found is None:
            if isinstance(owner, bots.Bot):
                found = Vitals(entity, owner.health)
            else:
                found = Vitals(entity, *pools.body_pools(owner.hero.guid))
            self.vitals[entity] = found
        return found

    def nearby_enemy_count(self, owner: int, radius: float) -> int:
        targets = self._targets()
        source = next((target for target in targets if target.entity == owner), None)
        if source is None:
            return 0
        free_for_all = self.match.game_map.free_for_all
        return sum(
            target.entity != owner
            and (free_for_all or target.team != source.team)
            and not (target.entity in self.vitals and self.vitals[target.entity].dead)
            and math.dist(source.feet, target.feet) <= radius
            for target in targets
        )

    def set_temporary_shield(self, owner: int, pool_id: int, amount: float) -> None:
        target = next((target for target in self._targets() if target.entity == owner), None)
        if target is None:
            return
        vitals = self.vitals_of(target)
        vitals.set_temporary_shield(pool_id, amount)
        self._sync_body_health(target.owner, vitals)

    def clear_temporary_shields(self, owner: int) -> None:
        vitals = self.vitals.get(owner)
        if vitals is not None:
            vitals.clear_temporary_shields()

    def track_hostile_bodies(
        self, owner: int, centre, radius: float, origin, category: int, tracked: tuple[int, ...],
    ) -> tuple[int, ...]:
        """Resolve the supported authored filter against ordinary player and NPC capsules."""
        collision = self.match.collision_world
        source = self.match._message_player(owner)
        if collision is None or source is None:
            return ()
        rays = collision.ray_geometry or collision
        found = []
        # The modeled player and bot bodies stand in for the native category and tag checks.
        # CA7C4C/C8E660 compare the query sphere with the candidate's stance capsule.
        for target in self._enemies(source):
            if (target.entity in tracked
                    or segment_distance(centre, *target.segment()) > radius + target.radius):
                continue
            pose = self.model_pose_for(target.entity)
            if _communication_visible(rays, origin, target, category, pose):
                found.append(target.entity)
        return tuple(found)

    def _wheel_model_pose(self, entity: int) -> ModelPose | None:
        player = self.match._message_player(entity)
        script = player.body_script if player is not None else None
        if script is None or script.retired or script.component.fault is not None:
            return None
        mover = player.mover
        right = aim(mover.yaw + 16384, 0)
        forward = aim(mover.yaw, 0)
        transform = (*right, 0.0, 0.0, 1.0, 0.0, 0.0, *forward, 0.0, *mover.position, 1.0)
        return script.world.wheel_model_pose(script.component, transform)

    def _wheel_base_actor_admits(
        self, profile: int, _source: int, entity: int, category: int, _holder_mask: int,
    ) -> bool:
        # The two wheel queries leave the relation reference empty. Their dynamic masks
        # include category 0100, and catalog base player bodies have no authored receiver.
        if profile not in (0x0690000000000079, 0x0690000000000111) or category != 0x0100:
            return False
        player = self.match._message_player(entity)
        base = heroes().get(player.hero.guid) if player is not None else None
        return base is not None and player.hero.body == base.body

    def communication_target(
        self, entity: int, offset, radius: float, aperture: float, category: int, *, profile: int = 0,
    ) -> int | None:
        collision = self.match.collision_world
        source = self.match._message_player(entity)
        if collision is None or source is None or not isinstance(offset, expr.Vec3):
            return None
        if not (math.isfinite(radius) and radius > 0 and 0 < aperture < 180):
            return None

        mover = source.mover
        x, y, z = mover.position
        pivot = eye_height(source.hero.body, mover.crouched)
        eye = (x, y + pivot, z)
        forward = aim(mover.yaw, mover.pitch)
        right = aim(mover.yaw + 16384, 0)
        up = aim(mover.yaw, mover.pitch - 16384)
        # C9A92F pitches the local offset around the first-person pivot.
        origin = _along(_along(_along(eye, right, offset.x), up, offset.y - pivot), forward, offset.z)
        limit = math.cos(math.radians(aperture * 0.5))
        start, end = _along(origin, forward, -radius), _along(origin, forward, radius)
        rays = collision.ray_geometry or collision
        best = None
        best_score = math.inf
        targets = self._targets()
        poses = {}
        actor_shapes = []
        if self.wheel_actor_admits is not None:
            for actor in targets:
                if actor.entity == entity:
                    continue
                pose = poses[actor.entity] = self.model_pose_for(actor.entity)
                if pose is None:
                    continue
                for bone, actor_category, geometry in pose.actor_shapes:
                    if self.wheel_actor_admits(profile, entity, actor.entity, actor_category, geometry[1]):
                        matrix = pose.bone_matrix(bone)
                        if matrix is not None and len(matrix) == 16:
                            actor_shapes.append((actor.entity, matrix, geometry))
        for target in targets:
            if target.entity == entity or isinstance(target.owner, bots.Bot) or self.is_dead(target.owner):
                continue
            a, b = target.segment()
            if segment_distance(origin, a, b) > radius + target.radius:
                continue
            point, axis = contacts.closest_segments(a, b, start, end)
            gap = math.dist(point, axis)
            if gap > target.radius:
                point = _along(point, _sub(axis, point), target.radius / gap)
            delta = _sub(point, origin)
            depth = _dot(delta, forward)
            if depth < limit * math.hypot(*delta):
                continue
            pose = poses[target.entity] if target.entity in poses else self.model_pose_for(target.entity)
            if not _communication_visible(rays, origin, target, category, pose, actor_shapes):
                continue
            screen = _communication_screen_score(target, eye, forward, right, up)
            # C9753A reads the candidate transform's translation.
            score = screen + 0.01 * min(1.0, math.dist(origin, target.feet) / radius)
            if score < best_score:
                best, best_score = target.entity, score
        return best

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

    def _ally(self, shooter, target: Target) -> bool:
        return not self.match.game_map.free_for_all and target.team == shooter.team

    def _shot_targets(self, shooter, direct: HitDef | None) -> list[Target]:
        targets = self._enemies(shooter)
        if direct is None or direct.ally_heal <= 0.0 or self.match.game_map.free_for_all:
            return targets
        for target in self._targets():
            if target.entity == _source_entity(shooter) or not self._ally(shooter, target):
                continue
            vitals = self.vitals_of(target)
            if not vitals.dead and vitals.total < sum(vitals.most.values()) - EPSILON:
                targets.append(target)
        return targets

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

    def bind_scene_enemy(self, host, bot: bots.Bot) -> None:
        """Give an allocated scene bot's health and death lifecycle to the scene scheduler."""
        if bot not in self.match.bots:
            raise ValueError('scene enemy must be allocated in the match')
        vitals = self.vitals.get(bot.entity)
        if vitals is None:
            vitals = Vitals(bot.entity, bot.health)
        host.bind_combatant(bot, self._retire_scene_enemy)
        self.vitals[bot.entity] = vitals
        component = host.combatant_component(bot)
        self._write_scene_health(component, vitals)
        if component.instances:
            host.bind_body_projection(bot, lambda snapshot, first_death: self._publish_scene_body(
                bot, snapshot, first_death,
            ))

    def _publish_scene_body(self, bot, snapshot, first_death):
        for viewer in self.match.players:
            client = viewer.client
            if client is None or not viewer.spawned:
                continue
            if first_death:
                client.queue_bot_death(bot.entity, snapshot)
            elif bot.dead:
                client.queue_bot_death_update(bot.entity, snapshot)
            else:
                client.queue_bot_live(bot.entity, snapshot)

    def _scene_component(self, bot):
        host = getattr(self.match, 'scene_host', None)
        return host.combatant_component(bot) if host is not None else None

    def consume_scene_boss_shots(self, bot, component, frame):
        from ow174.game import junkenstein_ai

        if self._scene_component(bot) is not component:
            return
        if bot.hero == junkenstein_ai.MERCY:
            weapon_graph, volleys = 0x006D, (10,)
        elif bot.hero == junkenstein_ai.SYMMETRA:
            weapon_graph, volleys = 0x157C, (85,)
        else:
            primary = junkenstein_ai.BOSS_PRIMARY.get(bot.hero)
            if primary is None:
                return
            _, weapon_graph, volley = primary
            volleys = (volley,)
            if bot.hero == junkenstein_ai.ROADHOG:
                volleys += (junkenstein_ai.ROADHOG_SECONDARY_VOLLEY,)
        shots, component.shots[:] = tuple(component.shots), []
        if bot.dead or self.is_stunned(bot, tick=frame):
            return
        for state, number, time, activation in shots:
            if state.instance.graph.index == weapon_graph and state.node.pos in volleys:
                self._shoot(bot, None, state, number, time, activation=activation, launch_tick=frame)

    def _retire_scene_enemy(self, bot):
        if bot not in self.match.bots:
            return
        self.match.bots.remove(bot)
        self.respawns[:] = [(when, actor) for when, actor in self.respawns if actor is not bot]
        for table in (self.vitals, self.history, self.replacement_ticks, self.melee_seen, self.stuns):
            table.pop(bot.entity, None)
        self.clear_pins(bot.entity)
        for viewer in self.match.players:
            if viewer.client is not None:
                viewer.client.queue_bot_destroy(bot.entity)
        bot.body_script = None

    def bind_scene_zombardier(self, host, control) -> None:
        """Connect an allocated scene enemy to live combat targets and projectile launch."""
        def targets(shooter):
            if shooter.dead or self.is_stunned(shooter, tick=host.frame):
                return ()
            return (target for target in self._enemies(shooter) if target.entity in host.world.entities)

        def launch(shooter, state, number, time, activation, frame):
            if not shooter.dead and not self.is_stunned(shooter, tick=frame):
                projection = host.combatant_projection(shooter)
                snapshot = projection.latest_live if projection is not None else None
                self._shoot(shooter, None, state, number, time, activation=activation,
                            snapshot=snapshot, launch_tick=frame)

        host.bind_zombardier(control, collision=self._collision, targets=targets, launch=launch)

    def _bot_weapons(self, tick: int) -> None:
        """Advance the weapon bodies of the map's firing bots and consume their volley reports."""
        from ow174.game import npc_control

        host = getattr(self.match, 'scene_host', None)
        for bot in self.match.bots:
            if host is not None and (bot.entity in host.weapons or host.combatant_component(bot) is not None):
                continue
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
        *, activation: tuple[int, int], snapshot: Snapshot | None = None, launch_tick: int | None = None,
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
            from ow174.game import mccree

            direction = mccree.fan_direction(shooter, state, direction, number, time, activation)
        if not npc and state.instance.graph.index == 0x01EE:
            from ow174.game import mccree

            directed = mccree.shot_aim(self, shooter, state)
            if directed is None:
                return
            origin, direction = directed
        direct = hit_def(state, node.field("m_6396149F"))
        speed = field("m_projectileSpeed")
        if speed:
            flashbang_gravity = None if npc else mccree.flashbang_gravity(shooter, state)
            gravity = flashbang_gravity
            if flashbang_gravity is not None:
                direction = mccree.flashbang_direction(state, direction)
            if (npc and shooter.hero == 0x02E0000000000177
                    and state.instance.graph.index == 0x0CA1 and node.pos == 14):
                body = self._scene_component(shooter) or getattr(shooter.body_script, 'component', None)
                if state.instance.component is body:
                    gravity = projectiles.unconditional_gravity(state)
            if (npc and shooter.hero == 0x02E0000000000065 and state.instance.graph.index == 0x022B
                    and node.pos == 31 and self._scene_component(shooter) is state.instance.component):
                gravity = projectiles.unconditional_gravity(state)
            lifetime = field("m_projectileLifetime", 5.0) or 5.0
            reach = field("m_FEC435C6")
            if reach:
                lifetime = min(lifetime, reach / speed)
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
            if damaging or shot is not None:
                directions = (direction,)
                split = None
                if (npc and shooter.hero == 0x02E0000000000040 and state.instance.graph.index == 0x01C6
                        and node.pos in (26, 29)
                        and self._scene_component(shooter) is state.instance.component):
                    from ow174.game import weapon_spread

                    seed = weapon_spread.seed_base(shooter.entity, state.instance.id, node.pos, activation[0])
                    if node.pos == 26:
                        directions = weapon_spread.authored_ring_directions(state, direction, seed, number)
                    else:
                        split = self._split_volley(state, direction, seed, number)
                elif npc:
                    from ow174.game import npc_context

                    directions = npc_context.enforcer_directions(
                        shooter, state, direction, number, activation,
                    )
                else:
                    directions = projectiles.launch_directions(state, direction)
                scale_direct = not npc or len(directions) > 1 or bool(direct and direct.projectile_headshot)
                self.rockets.extend(
                    Rocket(
                        shooter, shooter.team, origin, launch, speed, lifetime, direct, blast,
                        shot if pellet == 0 else None,
                        last_tick=(self.tick if launch_tick is None else launch_tick) if npc else None,
                        gravity=gravity if gravity is not None else 0.0,
                        velocity=(tuple(expr.f32(value * speed) for value in launch)
                                  if gravity is not None else None),
                        detonate_on_expiry=flashbang_gravity is not None,
                        scale_direct=scale_direct,
                        split_on_expiry=split,
                    )
                    for pellet, launch in enumerate(directions)
                )
            return
        if direct is None or direct.amount <= 0.0:
            return
        reach = field("m_FEC435C6", RANGE) or RANGE
        # The input supplies this simulation frame's aim and view time. Notices use the volley clock.
        view_tick = self.tick if launch_tick is None else launch_tick
        if command is not None:
            view_tick = command.frame - command.view_delay(shooter.quanta) * 1e6 / shooter.quanta
        directions = (direction,)
        if (npc and shooter.hero == 0x02E0000000000002 and state.instance.graph.index == 0x0019
                and node.pos == 10 and self._scene_component(shooter) is state.instance.component):
            from ow174.game import weapon_spread

            seed = weapon_spread.seed_base(shooter.entity, state.instance.id, node.pos, activation[0])
            directions = weapon_spread.authored_ring_directions(state, direction, seed, number)
        targets = tuple((target, shown) for target in self._shot_targets(shooter, direct)
                        if (shown := self.seen(target, view_tick)) is not None)
        for pellet in directions:
            self._hitscan(shooter, state, origin, pellet, reach, targets, direct, shot_frame,
                          view_tick if npc else None)

    def _hitscan(self, shooter, state, origin, direction, reach, targets, direct, shot_frame, world_tick):
        best = None
        for target, shown in targets:
            a, b = shown.segment()
            t = ray_capsule(origin, direction, a, b, shown.radius)
            if t is not None and t <= reach and (best is None or t < best[0]):
                best = (t, target, shown)
        shield = self._barrier_hit(shooter, origin, direction, best[0] if best else reach)
        if best is None and shield is None:
            return
        distance = shield[0] if shield is not None else best[0]
        wall = ray_world(self._collision(), origin, direction, distance)
        if wall is not None and wall < distance:
            return
        if shield is not None:
            self._damage_barrier(shooter, shield[1], direct.amount * direct.scale(distance), direct.tags)
            return
        t, target, shown = best
        if direct.ally_heal and self._ally(shooter, target):
            self._heal(shooter, target, direct.ally_heal)
            return
        hit_y = origin[1] + direction[1] * t
        critical = hit_y >= shown.feet[1] + shown.eye - HEAD_BELOW
        amount = direct.amount * direct.scale(t) * (direct.critical if critical else 1.0)
        self._damage(
            shooter, target, amount, critical and direct.critical > 1.0, direct.loadout, shot_frame,
            world_tick=world_tick, hit_tags=direct.tags,
            award_ult=state.instance.graph.index != 0x01EE,
            deadeye=state.instance.graph.index == 0x01EE and state.node.pos == 14,
            hit_reaction=state.instance.graph.index == 0x01D2 and state.node.pos in (31, 37),
        )
        self._hit_effect(target, direct.effect, shooter=shooter)

    def _fly(self, rocket: Rocket) -> None:
        """Sweep elapsed world time against the current bodies and static collision."""
        # Player input can arrive immediately before the scheduler accounts for a gap.
        elapsed = 1 if rocket.last_tick is None else self.tick - rocket.last_tick
        if elapsed <= 0:
            return
        rocket.last_tick = self.tick
        if rocket.velocity is not None:
            # Preserve the fixed16ms accelerated trajectory when the host skips
            # world ticks. Existing target poses/effect clocks remain the host's.
            for _ in range(elapsed):
                if rocket not in self.rockets:
                    break
                self._fly_step(rocket, min(TICK, rocket.left))
            return
        self._fly_step(rocket, min(elapsed * TICK, rocket.left))

    def _split_volley(self, state, direction, seed, number):
        from ow174.game import weapon_spread

        transition = state.node.field("m_747ECB51")
        if not transition or transition.get("$") != "STU_DB5FDC79":
            return None
        volley = transition["m_volley"]
        direct = hit_def(state, volley["m_6396149F"])
        speed = expr.to_float(state.evaluate(volley["m_projectileSpeed"], watch=False))
        reach = expr.to_float(state.evaluate(volley["m_FEC435C6"], watch=False))
        lifetime = expr.to_float(state.evaluate(volley["m_projectileLifetime"], watch=False))
        directions = weapon_spread.authored_ring_directions(state, direction, seed, number, volley=volley)
        if direct is None or direct.amount <= 0.0 or speed <= 0 or lifetime <= 0 or reach <= 0:
            return None
        return SplitVolley(directions, speed, min(lifetime, reach / speed), direct)

    def _expire(self, rocket: Rocket) -> None:
        self._landed(rocket)
        if rocket.detonate_on_expiry:
            self._explode(rocket, rocket.position)
        if (split := rocket.split_on_expiry) is not None:
            self.rockets.extend(
                Rocket(rocket.shooter, rocket.team, rocket.position, direction, split.speed, split.lifetime,
                       split.direct, None, last_tick=self.tick, scale_direct=True)
                for direction in split.directions
            )

    def _fly_step(self, rocket: Rocket, duration: float) -> None:
        """One linear sweep after any authored acceleration update."""
        if duration <= 0.0:
            self._expire(rocket)
            return
        if rocket.velocity is not None:
            from ow174.game import mccree

            # Apply the recovered velocity update before this frame's sweep.
            # This semi-implicit sweep order is a server reconstruction.
            rocket.velocity = mccree.gravity_velocity(rocket.velocity, rocket.gravity, duration)
            rocket.speed = math.sqrt(sum(value * value for value in rocket.velocity))
            if rocket.speed:
                rocket.direction = tuple(value / rocket.speed for value in rocket.velocity)
        # Sweep the remaining flight even when the range or lifetime ends inside this tick.
        step = rocket.speed * duration
        best = None
        for target in self._shot_targets(rocket.shooter, rocket.direct):
            a, b = target.segment()
            t = ray_capsule(rocket.position, rocket.direction, a, b, target.radius)
            if t is not None and t <= step and (best is None or t < best[0]):
                best = (t, target)
        shield = self._barrier_hit(
            rocket.shooter, rocket.position, rocket.direction, best[0] if best else step,
        )
        if shield is not None:
            best = (shield[0], None)
        wall = ray_world(self._collision(), rocket.position, rocket.direction, step)
        if wall is not None and (best is None or wall < best[0]):
            best = (wall, None)
            shield = None
        if best is None:
            rocket.position = _along(rocket.position, rocket.direction, step)
            rocket.flown += duration
            rocket.left -= duration
            if rocket.left <= 0.0:
                self._expire(rocket)
            return
        t, struck = best
        rocket.flown += t / rocket.speed
        self._landed(rocket)
        point = _along(rocket.position, rocket.direction, t)
        if shield is not None:
            direct = rocket.direct
            if direct is not None:
                scalar = direct.scale(rocket.flown * rocket.speed) if rocket.scale_direct else 1.0
                amount = direct.amount * scalar
                self._damage_barrier(rocket.shooter, shield[1], amount, direct.tags)
            if rocket.blast is not None:
                blast = rocket.blast
                self._damage_barrier(rocket.shooter, shield[1], blast.amount * blast.splash(0.0), blast.tags)
            self._explode(rocket, point, wall=True, impact_barrier=shield[1])
            return
        if struck is not None and rocket.direct is not None and rocket.direct.amount > 0.0:
            direct = rocket.direct
            if direct.ally_heal and self._ally(rocket.shooter, struck):
                self._heal(rocket.shooter, struck, direct.ally_heal)
            else:
                critical = (direct.projectile_headshot and direct.critical > 1.0
                            and point[1] >= struck.feet[1] + struck.eye - HEAD_BELOW)
                amount = direct.amount * (direct.critical if critical else 1.0)
                if rocket.scale_direct:
                    amount *= direct.scale(rocket.flown * rocket.speed)
                self._damage(
                    rocket.shooter, struck, amount, critical, direct.loadout, self.tick,
                    world_tick=self.tick, hit_tags=direct.tags,
                )
                self._hit_effect(struck, direct.effect, shooter=rocket.shooter)
        self._explode(rocket, point, wall=wall is not None)

    def _explode(self, rocket: Rocket, point: tuple, *, wall: bool = False, impact_barrier=None) -> None:
        """Apply the configured blast at impact or a scoped expiry detonation."""
        blast = rocket.blast
        if blast is None or blast.amount <= 0.0:
            return
        shields_present = impact_barrier is not None or getattr(
            getattr(self.match, 'scene_host', None), 'barriers', None,
        )
        for target in self._enemies(rocket.shooter):
            a, b = target.segment()
            distance = max(0.0, segment_distance(point, a, b) - target.radius)
            scalar = blast.splash(distance)
            # Start just outside an impact wall, on the projectile's approach side.
            visible_from = _along(point, rocket.direction, -0.0001) if wall else point
            if scalar > 0.0 and shields_present:
                centre = tuple((low + high) / 2 for low, high in zip(a, b, strict=True))
                reach = math.dist(visible_from, centre)
                if reach > 0:
                    direction = tuple(
                        (end - start) / reach for end, start in zip(centre, visible_from, strict=True)
                    )
                    blocked_at_impact = (impact_barrier is not None
                                         and impact_barrier.trace(visible_from, direction, reach) is not None)
                    if blocked_at_impact or self._barrier_hit(
                        rocket.shooter, visible_from, direction, reach,
                    ) is not None:
                        continue
            if scalar > 0.0 and capsule_visible(self._collision(), visible_from, a, b, target.radius):
                self._damage(
                    rocket.shooter, target, blast.amount * scalar, False, blast.loadout, self.tick,
                    world_tick=self.tick, hit_tags=blast.tags,
                )
                self._hit_effect(target, blast.effect, shooter=rocket.shooter)

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
            self._heal(healer, target, amount)

    def _heal(self, healer, target: Target, amount: float) -> float:
        vitals = self.vitals_of(target)
        if vitals.dead:
            return 0.0
        given = vitals.heal(amount)
        if given > 0.0:
            if not isinstance(healer, bots.Bot):
                healer.stats.add(stats.HEALING, given)
                self._credit_ult(healer, ult_charge.HEALING, given)
            self._sync_body_health(target.owner, vitals)
        return given

    @staticmethod
    def _write_scene_health(component, vitals: Vitals) -> None:
        component.set(OWNER_HEALTH, float(vitals.total))

    def _sync_body_health(self, owner, vitals: Vitals) -> None:
        component = self._scene_component(owner) if isinstance(owner, bots.Bot) else None
        if component is not None:
            self._write_scene_health(component, vitals)
            return
        script = getattr(owner, "body_script", None)
        if script is not None:
            script.set_health(vitals.total, vitals.capacity)

    @staticmethod
    def _credit_ult(actor, kind: str, amount: float) -> None:
        """The actor's own body charges its ultimate from the event (ult_charge.py); a bot has no
        chargeable body script yet, so for now bots deal damage without feeding a meter."""
        script = getattr(actor, "body_script", None)
        if script is not None:
            script.credit_ult(kind, amount)

    def _barrier_hit(self, shooter, origin, direction, length):
        trace = getattr(getattr(self.match, 'scene_host', None), 'trace_barrier', None)
        return trace(shooter, origin, direction, length) if trace is not None else None

    def _collision(self):
        from ow174.game.collision import CollisionError

        world = self.match.collision_world
        if world is None:
            raise CollisionError("match collision is not ready")
        return world

    # --- damage and death --------------------------------------------------------------------------

    def _team_damage(self, shooter, amount):
        if self.defender_damage_scale != 1.0 and shooter.team == 0:
            return amount * self.defender_damage_scale
        return amount

    def _damage_barrier(self, shooter, barrier, amount, tags):
        self.match.scene_host.damage_barrier(barrier, self._team_damage(shooter, amount), tags)

    def _damage(
        self, shooter, target: Target, amount: float, critical: bool, loadout, frame: int,
        *, world_tick: int | None = None, award_ult: bool = True, deadeye: bool = False,
        hit_reaction: bool = False,
        hit_tags: tuple[int, ...] = (),
        armour_scalar: float | None = None,
    ) -> None:
        vitals = self.vitals_of(target)
        if vitals.dead or amount <= 0.0:
            return
        amount = self._team_damage(shooter, amount)
        component = self._scene_component(shooter) if isinstance(shooter, bots.Bot) else None
        if component is not None:
            amount = component.world.outgoing_combat_damage(shooter.entity, amount, hit_tags)
        dealt = vitals.damage(amount, armour_scalar=armour_scalar)
        if dealt <= 0.0:
            return
        killed = vitals.pool_current(HEALTH_POOL) <= EPSILON
        if killed:
            vitals.now[HEALTH_POOL] = 0.0
            vitals.clear_temporary_shields()
            vitals.died_at = self.now
            vitals.death_tick = self.match.tick if world_tick is None else world_tick
            vitals.killer = _source_entity(shooter)
        sandbox_charge = False
        if not isinstance(shooter, bots.Bot):
            flags = (CRITICAL if critical else 0) | (KILLING if killed else 0)
            self._tell_hit(shooter, target, dealt, flags, frame)
            shooter.stats.add(stats.HERO_DAMAGE, dealt)
            if award_ult:
                if isinstance(target.owner, bots.Bot):
                    from ow174.game import mccree

                    sandbox_charge = mccree.award_sandbox_charge(self.match, shooter, target.owner, dealt)
                if not sandbox_charge:
                    self._credit_ult(shooter, ult_charge.DAMAGE, dealt)
        elif award_ult and component is not None and target.team == 0:
            from ow174.game import junkenstein_ai

            if shooter.hero == junkenstein_ai.SYMMETRA:
                junkenstein_ai.summoner_damage_event(component, target.entity, dealt, hit_tags)
        victim = target.owner
        if killed and isinstance(victim, bots.Bot):
            # The NPC death script owns its terminal health change and death event ordering.
            self._bot_died(
                shooter, victim, critical, loadout, vitals.death_tick,
                deadeye=deadeye, award_ult=award_ult and not sandbox_charge,
            )
            return
        self._sync_body_health(victim, vitals)
        if isinstance(victim, bots.Bot) and isinstance(victim.body_script, BotScript):
            victim.body_script.show_damage_nameplate(_source_entity(shooter))
            if (hit_reaction or deadeye) and not self.is_stunned(victim):
                # Ordinary hit presentation does not interrupt AI or move the victim.
                deadline = self.tick + math.ceil(TALON_DAMAGE_REACTION / TICK)
                self._begin_npc_hit_reaction(
                    target, TALON_DAMAGE_REACTION, deadline, restart=False, shooter=shooter,
                )
        if killed:
            self._died(shooter, victim, critical, loadout)

    def _tell_hit(self, shooter, target: Target, dealt: float, flags: int, frame: int) -> None:
        """25001 to the shooter: the hit marker and the target's health bar (frame: the shot's)."""
        if _NO_COMBAT_NOTICES:
            return
        value = {
            "+0x78": {"+0x0": {"+0x0": target.entity}, "+0x4": max(1, round(dealt)), "+0x8": flags},
            "+0x84": frame & 0xFFFFFFFF,
        }
        self._queue_notice(shooter, target.owner, target.entity, HIT, value)

    def _kill_notice(self, killer, victim) -> None:
        """25005 to the killer: "ELIMINATED: <name>" (1126.07C; +0x89 assist would be 01F0.07C, +0x88 a
        score shown when above 0). Its handler (0x7FF7895D2B50) reads only +0x84, +0x88 and +0x89."""
        if _NO_COMBAT_NOTICES:
            return
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

    def _defer_deadeye_notice(self, killer, victim) -> bool:
        from ow174.game import mccree

        script = killer.body_script
        if (self.match.game_map.map_guid != RIALTO_RETRIBUTION
                or victim.spawner.get('manual_spawn') is not True
                or killer.hero.guid != mccree.HERO or script is None or killer.client is None):
            return False
        root = next(
            (i for i in script.component.instances.values() if i.graph.index == mccree.ULTIMATE), None,
        )
        if root is None:
            return False
        self.deadeye_notices.append((killer, victim, script, root.id, script.frame, self.match.tick + 125))
        return True

    def _flush_deadeye_notices(self) -> None:
        # The original client can leave its first-person presentation latched if
        # ELIMINATED arrives while Deadeye's exit states are still active.
        pending = self.deadeye_notices[:]
        self.deadeye_notices.clear()
        for killer, victim, script, root_id, shot_frame, deadline in pending:
            if killer.client is None or killer.body_script is not script:
                continue
            base = script.stream.base
            root = script.component.instances.get(root_id)
            ready = root is None or (base is not None and base.k > shot_frame and not any(
                base.states.get((root_id, root.graph.node(pos).state), (None, False))[1]
                for pos in (17, 37, 49, 90)))
            if ready or self.tick >= deadline:
                self._kill_notice(killer, victim)
            else:
                self.deadeye_notices.append((killer, victim, script, root_id, shot_frame, deadline))

    def _bot_died(
        self, killer, bot: bots.Bot, critical: bool, loadout, tick: int, *, deadeye: bool, award_ult: bool,
    ) -> None:
        component = self._scene_component(bot)
        self.clear_pins(bot.entity)
        self.stuns.pop(bot.entity, None)
        self._end_npc_hit_reaction(bot.entity)
        bot.died(tick)
        if component is None and not bot.retain_corpse:
            self.respawns.append((self.now + BOT_RESPAWN, bot))
        if not isinstance(killer, bots.Bot):
            killer.stats.add(stats.ELIMINATIONS)
            if award_ult:
                self._credit_ult(killer, ult_charge.KILL, 0.0)
            if not deadeye or not self._defer_deadeye_notice(killer, bot):
                self._kill_notice(killer, bot)
        log.info("[game] %s: %s killed %s", self.match.label(), killer.name, bot.name)
        if component is not None:
            component.owner_dead = True
            if self.match.card == JUNKENSTEIN_SHOCKING_SURPRISE:
                # 2B33/10 watches the body death flag before sending 14B6.
                component.set(478, True)
            component.world.notify_entity_filter_change(bot.entity)
            component.discard_button_presses()
            component.shots.clear()
            component.set(OWNER_HEALTH, 0.0)
            runtime.send_message(component, DIED, self._death_parameters(killer, critical, loadout),
                                 _source_entity(killer))
            return
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
        if self._scene_component(bot) is not None:
            return
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
        self.clear_pins(victim.body)
        self.stuns.pop(victim.body, None)
        label = self.match.label()
        log.info("[game] %s: %s killed %s", label, killer.name, victim.name)
        victim.stats.add(stats.DEATHS)
        bot_killer = isinstance(killer, bots.Bot)
        source = _source_entity(killer)
        if killer is not victim and not bot_killer:
            killer.stats.add(stats.ELIMINATIONS)
            self._credit_ult(killer, ult_charge.KILL, 0.0)
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
            script.world.velocity_requests.clear()
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
        match._prepare_bot_script(fresh, respawned=True)
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
    PvP countdown). Every full frame of this entity has to list 0CB6, or it would destroy it.

    A mode in MODE_GRAPHS gets its own graph as instance 1 with the state on that lets the scoreboard
    open, and 0CB6 as that graph's child, as the game data nests them. 0CB6's own remote state stays off
    there: these are the frames seen working in the client. A pack without the mode's graph keeps the
    plain frame."""
    ui = graph.graph(MODE_UI)
    values = {var: (value, ()) for var, value in entity_vars.items()}
    index, slot, host = MODE_GRAPHS.get(mode_guid, (None, None, None))
    script = graph.graph(index) if index is not None else None
    if script is not None:
        board = list(script.remote_states())[slot].state
        instances = [InstanceWire(1, script, states={board: StateWire(script.state(board).cls, True)})]
        graphs = {1: script}
        if host is not None:
            instances.append(InstanceWire(2, ui, parent=(1, host)))
            graphs[2] = ui
        return remote_full_frame(instances, values, graphs)
    # EntryClientOnlySharedVars starts the mode UI's sole remote state, st21.
    shared = ui.state(21)
    instance = InstanceWire(1, ui, states={21: StateWire(shared.cls, True)})
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
