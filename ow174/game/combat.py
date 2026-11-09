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
  command's aim from declared body/stance FirstPerson offsets (eye_height;DATA;
  native animated camera/muzzle equivalence unverified), or a projectile
  (m_projectileSpeed) flown each tick.
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
    ana_grenade,
    ana_nano,
    ana_sleep,
    barriers,
    bots,
    defensive_casts,
    deflection,
    dva,
    dva_matrix,
    healing,
    heavy_charge,
    hook,
    launch_ring,
    matrix,
    mei_blizzard,
    mei_frost,
    observers,
    orisa_barrier,
    orisa_halt,
    pools,
    projectiles,
    reaper_shadow,
    reinhardt_barrier,
    sigma,
    sigma_grasp,
    sombra_hack,
    stats,
    voicelines,
    weapon_spread,
    winston_barrier,
    world,
    zarya_barriers,
    zenyatta,
)
from ow174.game.bits import BitWriter
from ow174.game.collision import MOVER, World
from ow174.game.commands import Command
from ow174.game.mover import mover_data
from ow174.game.script import expr, graph, nodes, runtime
from ow174.game.script.bot import DIED, BotScript
from ow174.game.script.driver import BASTION, Snapshot
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
EYE_STANDING = {0x04000000000001FD: 1.82, 0x0400000000000216: 1.55,
                0x04000000000002A3: 1.425, 0x0400000000000407: 1.74}
EYE_CROUCHED = {0x04000000000001FD: 1.35, 0x0400000000000216: 1.15,
                0x04000000000002A3: 0.9975, 0x04000000000003CF: 1.085, 0x0400000000000407: 1.74}
EYE_CROUCHED_DEFAULT = 1.15



HEAD_BELOW = 0.2  # m under the eye where the head starts (unverified)
# Model 0B44, bone 0933, clip 0E35 last / 0EB3 first. Static deployed pose.
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


def ray_box(origin, direction, low, high, reach):
    """Slab entry into a local-space authored box, including starts inside."""
    first, last = 0.0, reach
    for p, d, a, b in zip(origin, direction, low, high, strict=True):
        a, b = min(a, b), max(a, b)
        if abs(d) <= EPSILON:
            if p < a or p > b:
                return None
            continue
        near, far = (a-p)/d, (b-p)/d
        first, last = max(first, min(near, far)), min(last, max(near, far))
        if first > last:
            return None
    return first


def projectile_box(target, origin, direction, reach, graph_index):
    """Authored active DeflectProjectiles boxes, reconstructed eye-space hosting.

    Box values are evaluated from the real state. Local origin/orientation and
    front incidence are reconstruction policies; the caller selects the response.
    No hitscan, beam, shield or melee semantics are inferred by this helper.
    """
    player = target.owner
    script = getattr(player, "body_script", None)
    component = getattr(script, "component", None)
    mover = getattr(player, "mover", None)
    if not isinstance(component, runtime.Component) or mover is None:
        return None
    instances = [i for i in component.instances.values() if i.graph.index == graph_index]
    if not instances:
        return None
    forward = aim(mover.yaw, mover.pitch)
    if _dot(direction, forward) >= 0:
        return None
    yaw = mover.yaw * 2 * math.pi / 65536
    right = (-math.cos(yaw), 0.0, math.sin(yaw))
    up = (
        right[1]*forward[2]-right[2]*forward[1],
        right[2]*forward[0]-right[0]*forward[2],
        right[0]*forward[1]-right[1]*forward[0],
    )
    eye = (target.feet[0], target.feet[1]+target.eye, target.feet[2])
    delta = _sub(origin, eye)
    local_origin = tuple(_dot(delta, basis) for basis in (right, up, forward))
    local_direction = tuple(_dot(direction, basis) for basis in (right, up, forward))
    best = None
    for instance in instances:
        for state in instance.states.values():
            if not state.active or state.node.cls != "STUStatescriptStateDeflectProjectiles":
                continue
            for box in state.node.field("m_boxes") or ():
                low = state.evaluate(box.get("m_CBA118B2"), watch=False)
                high = state.evaluate(box.get("m_AEF67365"), watch=False)
                if not isinstance(low, expr.Vec3) or not isinstance(high, expr.Vec3):
                    continue
                t = ray_box(local_origin, local_direction, tuple(low), tuple(high), reach)
                if t is not None and (best is None or t < best[0]):
                    best = (t, state)
    return best


def deflect_ray(target, origin, direction, reach):
    found = projectile_box(target, origin, direction, reach, 0x03A3)
    return found[0] if found is not None else None


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


def segment_pair_distance(a, b, c, d) -> float:
    """Minimum distance between finite segments, including points and parallel segments."""
    u, v, w = _sub(b, a), _sub(d, c), _sub(a, c)
    aa, bb, cc = _dot(u, u), _dot(u, v), _dot(v, v)
    dd, ee = _dot(u, w), _dot(v, w)
    # An interior minimum, when present, plus all four constrained edges.
    candidates = [segment_distance(p, c, d) for p in (a, b)]
    candidates.extend(segment_distance(p, a, b) for p in (c, d))
    determinant = aa * cc - bb * bb
    if determinant > EPSILON * aa * cc:
        s, t = (bb * ee - cc * dd) / determinant, (aa * ee - bb * dd) / determinant
        if 0.0 <= s <= 1.0 and 0.0 <= t <= 1.0:
            delta = _sub(_along(a, u, s), _along(c, v, t))
            candidates.append(math.sqrt(_dot(delta, delta)))
    return min(candidates)
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
        self.bonus_shields = 0.0
        self.bonus_shields_max = 0.0
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
        self.antiheal_until: float | None = None
        self.healing_boost: tuple[float, float] | None = None
        self.nano_until = 0.0

    @property
    def dead(self) -> bool:
        return self.died_at is not None

    @property
    def total(self) -> float:
        temporary = sum(pool.now for pool in self.temporary_shields.values())
        return sum(self.now.values()) + temporary + self.bonus_shields

    @property
    def maximum(self) -> float:
        temporary = sum(pool.most for pool in self.temporary_shields.values())
        return sum(self.most.values()) + temporary + self.bonus_shields_max

    def grant_temporary_shields(self, tag: int, amount: float, limit: float) -> float:
        """Nonrecoverable tagged bonus,stable wire slot/ID and current-amount cap."""
        if not math.isfinite(amount) or not math.isfinite(limit) or amount < 0 or limit < 0:
            raise ValueError("temporary shields need finite nonnegative amount/cap")
        if self.dead or amount == 0:
            return 0.0
        pool = self.temporary_shields.get(tag)
        if pool is None:
            if len(self.temporary_shields) >= POOLS - 1:
                raise ValueError("temporary shield slots exhausted")
            ids = [*self.ids.values(),
                   *(p.pool_id for p in self.temporary_shields.values())]
            pool = self.temporary_shields[tag] = TemporaryShield(max(ids, default=0) + 1, 0, 0)
        given = min(amount, max(0.0, limit - pool.now))
        if given > 0:
            pool.now += given
            pool.most = pool.now
            self.changed.add(SHIELDS_POOL)
        return given

    def decay_temporary_shields(self, tag: int, amount: float) -> float:
        if not math.isfinite(amount) or amount < 0:
            raise ValueError("temporary shield decay needs finite nonnegative amount")
        pool = self.temporary_shields.get(tag)
        if pool is None:
            return 0.0
        taken = min(amount, pool.now)
        if taken > 0:
            pool.now -= taken
            if pool.now <= EPSILON:
                pool.now = pool.most = 0.0
            self.changed.add(SHIELDS_POOL)
        return taken

    def _damage_losses(self, amount):
        left = max(0.0, amount)
        for tag in reversed(self.temporary_shields):
            take = min(left, self.temporary_shields[tag].now)
            if take:
                left -= take
                yield (tag,), take
        take = min(left, self.bonus_shields)
        if take:
            left -= take
            yield None, take
        for kind in (SHIELDS_POOL, ARMOUR_POOL, HEALTH_POOL):
            if left <= 0:
                break
            if kind == ARMOUR_POOL and self.now[kind] > 0:
                left = max(left-ARMOUR_BLOCK, left/2)
            take = min(left, self.now[kind])
            if take > 0:
                left -= take
                yield kind, take

    def damage(self, amount: float) -> float:
        """Apply the existing shield, armour and health losses."""
        taken = 0.0
        for kind, take in self._damage_losses(amount):
            if isinstance(kind, tuple):
                taken += self.decay_temporary_shields(kind[0], take)
                continue
            if kind is None:
                self.bonus_shields -= take
                kind = SHIELDS_POOL
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
        if SHIELDS_POOL in kinds:
            start = 1 if SHIELDS_POOL in self.ids else 0
            for slot, pool in enumerate(self.temporary_shields.values(), start):
                parts[SHIELDS_POOL * POOLS + slot] = world.pool(
                    pool.pool_id, SHIELDS_POOL, pool.most, pool.now)
            if self.bonus_shields_max:
                slot = max(1, start + len(self.temporary_shields))
                ids = [*self.ids.values(), *(pool.pool_id for pool in self.temporary_shields.values())]
                pool_id = max(4, max(ids, default=0) + 1)
                value = world.pool(pool_id, SHIELDS_POOL, self.bonus_shields_max, self.bonus_shields)
                parts[SHIELDS_POOL * POOLS + slot] = value[:17] + bytes((0,)) + value[18:]
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
    self_splash: float = 0.0
    knockback: tuple = ()
    boost_credit: tuple | None = None

    def scale(self, distance: float) -> float:
        """Unverified: full damage to falloff_start, falloff_floor from falloff_end on, a straight line
        between."""
        start, end, floor = self.falloff_start, self.falloff_end, self.falloff_floor
        if start is None or end is None or floor is None or end <= start or distance <= start:
            return 1.0
        if distance >= end:
            return floor
        return 1.0 + (floor - 1.0) * (distance - start) / (end - start)

    def impulse(self, delta, distance):
        """Tank's two ring coefficients and a resolved outward displacement."""
        horizontal, vertical, rings = self.knockback
        previous = None
        for radius, flat, up in rings:
            if distance < radius:
                if previous is not None:
                    inner_radius, inner_flat, inner_up = previous
                    share = (distance - inner_radius) / max(0.0001, radius - inner_radius)
                    flat = inner_flat + (flat - inner_flat) * share
                    up = inner_up + (up - inner_up) * share
                x, y, z = delta
                length = math.hypot(x, y, z)
                ground = math.hypot(x, z)
                return (x / ground * horizontal * flat if ground > EPSILON else 0.0,
                        y / length * vertical * up if length else 0.0,
                        z / ground * horizontal * flat if ground > EPSILON else 0.0)
            previous = radius, flat, up
        return (0.0, 0.0, 0.0)

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


def hit_def(state, cfg, *, critical_default: float = 1.0, tank_splash: bool = False) -> HitDef | None:
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
    knockback = ()
    if tank_splash and (knockback_rings := modify.get("m_knockbackRings")):
        def read(field):
            return _number(state.evaluate(field, watch=False), 0.0) if field is not None else 0.0

        knockback = (
            read(modify.get("m_E33EFEE1")), read(modify.get("m_10399AE7")),
            tuple(tuple(read(ring.get(key)) for key in ("m_radius", "m_579D7CFF", "m_10399AE7"))
                  for ring in knockback_rings),
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
    return HitDef(
        amount=amount,
        loadout=loadout.guid if isinstance(loadout, expr.Asset) else None,
        falloff_start=value("m_EAD7F104"),
        falloff_end=value("m_157E5BC5"),
        falloff_floor=value("m_72A81154"),
        critical=value("m_A872C70E", 2.0 if (state.instance.graph.index == 0x0164
                         or (state.instance.component.world.hero == 0x02E0000000000042
                             and state.instance.graph.index == 0x01D2 and state.node.pos == 31))
                       else critical_default),
        rings=tuple(sorted(rings)),
        effect=effect.guid if isinstance(effect, expr.Asset) else None,
        self_splash=value("m_8A3E3883", 0.0) if tank_splash else 0.0,
        knockback=knockback,
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
    sentry_yaw: int | None = None

    def segment(self, feet=None):
        """The capsule's segment (from the feet to the top, a radius in from both ends)."""
        x, y, z = feet or self.feet
        return (x, y + self.radius, z), (x, y + max(self.radius, self.height - self.radius), z)


    def core_segment(self):
        x, y, z = self.feet
        dx, _, dz = aim(self.sentry_yaw, 0)
        return tuple((x + dx * depth, y + height, z + dz * depth) for height, depth in SENTRY_CORE)


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
    charge_eligible: bool = True  # frozen at emission; Tank damage cannot charge after mode expiry
    deflectable: bool = False  # scoped Bastion/Genji reconstruction, frozen at emission
    ana_grenade: bool = False  # frozen normal 0B97 effect selection
    definition: int | None = None  # actual WeaponVolley entity definition, including unconfirmed flights
    gravity: float = 0.0
    velocity: tuple | None = None
    detonate_on_expiry: bool = False
    target_limit: int = 1
    hit_entities: tuple[int, ...] = ()


class Combat:
    """The match's health, hits, deaths and respawns."""

    def __init__(self, match) -> None:
        self.match = match
        self.grasp_grants = {}
        self.grasp_shields = {}
        self.hooks = hook.Hooks(self)
        self.winston_barriers = winston_barrier.Barriers(self)
        self.orisa_barriers = orisa_barrier.Barriers(self)
        self.orisa_halt = orisa_halt.Halt(self)
        self.tesla_frames: dict[int, int] = {}
        self.shadow_steps = reaper_shadow.ShadowSteps(self)
        self.reaper_cast_anchors: dict[int, tuple[int, int]] = {}
        self.zarya_barriers = zarya_barriers.Barriers(self)
        self.vitals: dict[int, Vitals] = {}  # body or bot entity -> its health
        self.history: dict[int, deque] = {}  # entity -> retained pose samples
        self.replacement_ticks: dict[int, int] = {}  # replacement body -> authoritative world tick
        self.rockets: list[Rocket] = []
        self.respawns: list[tuple[float, object]] = []  # (when, a player or a bot)
        self.next_bot = bots.BOT_ENTITY + len(match.bots)
        self.now = 0.0
        self.tick = 0
        self.melee_seen: dict[int, dict[tuple[int, int], int]] = {}  # body -> counters by instance/state
        self.pins: dict[int, int] = {}  # Assassin body -> pinned player body
        self.stuns: dict[int, int] = {}  # body identity -> exclusive world-tick deadline
        self.sleeps: dict[int, ana_sleep.Sleep] = {}
        self.frost: dict[int, mei_frost.Frost] = {}
        self.hacks: dict[int, sombra_hack.Hack] = {}
        self.heavy_drives: dict[int, heavy_charge.Drive] = {}
        self.blizzard = mei_blizzard.Blizzard(self)
        self.projectiles = projectiles.Projectiles(self)  # leases, confirmations, the Biotic Field
        self.observers = observers.Observers(match)  # the bodies' statescript for the other clients
        self.voice = voicelines.VoiceLines(match)  # the hero's spawn voice line (voicelines.py)
        self.zenyatta = zenyatta.Zenyatta(self)
        self.sigma = sigma.Sigma(self)
        self.barriers = barriers.Barriers(self)
        self.healing = healing.HealingEffects(self)

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
        dva.retire_source(self.match, player)
        self.hooks.retire(player.body)
        self.winston_barriers.retire(player.body)
        self.orisa_barriers.retire(player.body)
        self.orisa_halt.retire(player.body)
        self.orisa_halt.release(player.body)
        self.tesla_frames.pop(player.body, None)
        self.shadow_steps.retire(player.body)
        self.reaper_cast_anchors.pop(player.body, None)
        self.vitals.pop(player.body, None)
        self.grasp_grants.pop(player.body, None)
        self.grasp_shields.pop(player.body, None)
        self.zarya_barriers.retire(player.body)
        self.history.pop(player.body, None)
        self.replacement_ticks.pop(player.body, None)
        self.melee_seen.pop(player.body, None)
        self.stuns.pop(player.body, None)
        self.sleeps.pop(player.body, None)
        self.frost.pop(player.body, None)
        sombra_hack.retire(self, player.body)
        self._clear_pins(player.body)
        # Damage authority retires with the old body even when network lease
        # allocation failed. Transport availability must not keep an old attack
        # alive and later attribute it to this player's replacement body.
        self.rockets[:] = [rocket for rocket in self.rockets if rocket.shooter is not player]
        self.zenyatta.retire(player.body)
        self.blizzard.retire(player.body)
        self.sigma.retire(player.body)
        self.barriers.retire(player.body)
        self.healing.retire(player.body)
        self.respawns[:] = [(when, who) for when, who in self.respawns if who is not player]

    def record_replacement(self, body: int) -> None:
        """Keep the creation boundary independent of the rolling pose history."""
        self.replacement_ticks[body] = self.match.tick
        self.stuns.pop(body, None)
        self.sleeps.pop(body, None)
        self.frost.pop(body, None)
        self._clear_pins(body)

    def is_stunned(self, actor, *, tick: int | None = None) -> bool:
        entity = _source_entity(actor)
        at = self.tick if tick is None else tick
        sleep = self.sleeps.get(entity)
        frost = self.frost.get(entity)
        return (self.hooks.controlled(entity) or entity in self.pins.values()
                or self.stuns.get(entity, 0) > at
                or (sleep is not None and sleep.active_at(at))
                or (frost is not None and frost.frozen(at)))

    def _clear_pins(self, entity: int) -> None:
        for source, victim in tuple(self.pins.items()):
            if entity in (source, victim):
                del self.pins[source]

    def _hit_effect(self, target: Target, effect: int | None, source=None) -> None:
        """Resource-backed stun reconstruction; native immunity/presentation remain unverified."""
        if effect == mei_frost.EFFECT and source is not None:
            self._apply_frost(source, target)
            return
        if (effect not in (FLASHBANG_EFFECT, ana_sleep.EFFECT) or self.vitals_of(target).dead
                or defensive_casts.invulnerable(target.owner)):
            return
        sleep = effect == ana_sleep.EFFECT
        if sleep:
            deadline = self.tick + math.ceil(ana_sleep.DURATION / TICK)
            self.sleeps[target.entity] = ana_sleep.Sleep(self.tick, deadline)
        else:
            variables = graph.mode_variables(self.match.game_map.mode_guid) or {}
            duration = 1.7 if expr.truthy(variables.get(FLASHBANG_MODE_FLAG)) else 0.8
            deadline = max(self.tick + math.ceil(duration / TICK), self.stuns.get(target.entity, 0))
            self.stuns[target.entity] = deadline
        from ow174.game import assassin

        self._interrupt(target)
        script = getattr(target.owner, "body_script", None)
        if script is not None:
            component = script.component
            assassin.bind(component.world, component, None)
            apply_control = getattr(script, "apply_control", None)
            if apply_control is not None:
                apply_control(self.tick, deadline, sleep=sleep)

    def _apply_frost(
        self, source, target: Target, *, at_ms=None, channel=0,
        initial=mei_frost.INITIAL, step=mei_frost.STEP,
    ) -> None:
        owner = target.owner
        if (getattr(getattr(source, "hero", None), "guid", None) != defensive_casts.MEI
                or source.body_script is None or self.vitals_of(target).dead
                or self._frost_immune(owner)):
            return
        sample = self.frost.get(target.entity)
        now = self.tick * 16 if at_ms is None else at_ms
        if sample is not None:
            now = max(now, max((entry[1] for entry in sample.sources.values()), default=0))
            if sample.frozen_until and now < sample.frozen_until:
                return
        if sample is None or sample.frozen_until:
            sample = self.frost[target.entity] = mei_frost.Frost()
        mover = getattr(owner, "mover", None)
        yaw = mover.yaw if mover is not None else round(getattr(owner, "yaw", 0))
        pitch = mover.pitch if mover is not None else 0
        sample.hit(source, now, yaw, pitch, channel=channel, initial=initial, step=step)
        script = owner.body_script
        if script is not None:
            script.component.set(2427, sample.progress(now))
        if sample.frozen_until:
            self._interrupt(target)
            if mover is not None and script is not None:
                script.apply_frost(self.tick, (sample.frozen_until + 15) // 16)

    def _frost_immune(self, owner) -> bool:
        return defensive_casts.invulnerable(owner) or self.zenyatta.ultimate(owner) or mei_frost.immune(owner)

    def _clear_frost(self, entity, owner, *, cleansed=True) -> None:
        if self.frost.pop(entity, None) is None:
            return
        script = owner.body_script
        if script is not None:
            script.component.set(2427, 0.0)
            if cleansed:
                script.shorten_frost(self.tick)

    def _advance_frost(self) -> None:
        if not self.frost:
            return
        for target in self._targets():
            sample = self.frost.get(target.entity)
            if sample is None:
                continue
            now = self.tick * 16
            owner = target.owner
            script = owner.body_script
            sample.prune(now)
            expired = now >= sample.frozen_until if sample.frozen_until else not sample.sources
            cleansed = self._frost_immune(owner)
            if expired or cleansed:
                self._clear_frost(target.entity, owner, cleansed=cleansed)
            elif script is not None and not sample.frozen_until:
                progress = sample.progress(now)
                if progress != script.component.entity_var(2427).value():
                    script.component.set(2427, progress)

    def _wake_from_damage(self, target: Target) -> None:
        sleep = self.sleeps.get(target.entity)
        if (sleep is None or sleep.waking or not sleep.active_at(self.tick)
                or (self.tick - sleep.start) * TICK <= ana_sleep.DAMAGE_GRACE):
            return
        sleep.end = min(sleep.end, self.tick + math.ceil(ana_sleep.WAKE / TICK))
        sleep.waking = True
        script = getattr(target.owner, "body_script", None)
        shorten = getattr(script, "shorten_sleep", None)
        if shorten is not None:
            shorten(sleep.end)

    def _interrupt(self, target: Target) -> None:
        self.zenyatta.interrupt(target.owner)
        self.hooks.cancel_owner(target.entity)
        self.shadow_steps.interrupt(target.owner)
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

    def command(self, player, command: Command | None, *, recovered_tick=None) -> None:
        """Consume the body's reports. Only a frame with a pose can affect targets."""
        if self.match.collision_world is None:
            return
        script = player.body_script
        if script is None:
            return
        shots, script.component.shots[:] = list(script.component.shots), []
        repairs, script.self_repairs[:] = list(script.self_repairs), []
        if (self.is_dead(player) or self.is_stunned(player)
                or (command is not None and script.controlled_at(command.frame))):
            return
        self.zenyatta.command(player, command)
        defensive_casts.advance_entry(player)
        self.shadow_steps.command(player, command)
        self.winston_barriers.command(player, command)
        if command is not None and defensive_casts.reaper_cast_needs_clock(player):
            anchor = self.reaper_cast_anchors.get(player.body)
            if command.frame == script.frame and (anchor is None or command.frame > anchor[1]):
                self.reaper_cast_anchors[player.body] = (self.tick, command.frame)
        self.hooks.command(player, command)
        if repairs and player.body not in self.hacks:
            target = next((target for target in self._targets() if target.owner is player), None)
            if target is not None:
                vitals = self.vitals_of(target)
                given = vitals.heal(sum(repairs) * ana_grenade.healing_factor(player, vitals, self.now))
                player.stats.add(stats.HEALING, given)
                self._sync_body_health(player, vitals)
                script.report_self_healing(given)
        if command is not None:
            for state, number, time, activation in shots:
                try:
                    self._shoot(
                        player, command, state, number, time, activation=activation,
                        recovered_tick=recovered_tick,
                    )
                except Exception:
                    log.exception("[game] %s: %s's shot failed", self.match.label(), player.name)
        self._tesla(player, command, script, recovered_tick)
        try:
            self._melee(player, command, script, recovered_tick=recovered_tick)
        except Exception:
            log.exception("[game] %s: %s's melee failed", self.match.label(), player.name)

    def _tesla(self, player, command, script, recovered_tick) -> None:
        """Consume the authored 00FE continuous health effect through a static cone."""
        if player.hero.guid != winston_barrier.HERO or command is None:
            return
        if command.frame <= self.tesla_frames.get(player.body, -1):
            return
        self.tesla_frames[player.body] = command.frame
        instance = next((i for i in script.component.instances.values() if i.graph.index == 0x00FE), None)
        if instance is None:
            return
        search, effect = instance.states.get(10), instance.states.get(13)
        if search is None or effect is None or not (search.active and effect.active):
            return
        reach = _number(search.evaluate(search.node.field("m_radius"), watch=False), 0.0)
        angle = _number(search.evaluate(search.node.field("m_A30B383E"), watch=False), 0.0)
        damage = hit_def(effect, {"m_modifyHealth": effect.node.field("m_modifyHealth")})
        if damage is None or damage.amount <= 0 or reach <= 0 or not 0 < angle < 90:
            return
        origin, direction = self._eye(player), aim(*command.aim())
        cone = math.tan(math.radians(angle))
        view_tick = command.frame - command.view_delay(player.quanta) * 1e6 / player.quanta
        amount = damage.amount * player.quanta / 1e6 * self._nano_outgoing(player)
        barriers = {}
        victims = []
        for target in self._enemies(player, recovered_tick=recovered_tick):
            shown = self.seen(target, view_tick)
            if shown is None:
                continue
            a, b = shown.segment()
            point = (a[0], max(a[1], min(b[1], origin[1])), a[2])
            offset = _sub(point, origin)
            distance = math.sqrt(_dot(offset, offset))
            forward = _dot(offset, direction)
            lateral = math.sqrt(max(0.0, distance * distance - forward * forward))
            if distance - shown.radius > reach or forward < 0 or lateral > forward * cone + shown.radius:
                continue
            if distance > EPSILON:
                ray = tuple(value / distance for value in offset)
                contact = max(0.0, distance - shown.radius)
                if ray_world(self._collision(), origin, ray, contact) is not None:
                    continue
                barrier = self._barrier_contact(player, origin, ray, contact)
                if barrier is not None:
                    barriers[id(barrier[1])] = barrier[1]
                    continue
            victims.append(target)
        for barrier in barriers.values():
            self._damage_barrier(barrier, amount)
        for target in victims:
            self._damage(player, target, amount, False, damage.loadout, command.frame,
                         recovered_tick=recovered_tick)

    def _melee(self, player, command, script, *, recovered_tick=None) -> None:
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
            amount *= self._nano_outgoing(player)
            credit = None
            if player.hero.guid == 0x02E0000000000020:
                from ow174.game import mercy_boost

                credit = mercy_boost.credit(self, player)
                if credit is not None:
                    amount = expr.f32(amount*credit[1])
            loadout = (
                state.evaluate(modify.get("m_loadout"), watch=False) if modify.get("m_loadout") else None
            )
            loadout = loadout.guid if isinstance(loadout, expr.Asset) else None
            centre = _along(self._eye(player), aim(*command.aim()), reach)
            view_tick = command.frame - command.view_delay(player.quanta) * 1e6 / player.quanta
            for target in self._enemies(player, recovered_tick=recovered_tick):
                shown = self.seen(target, view_tick)
                if shown is None:
                    continue
                a, b = shown.segment()
                if (segment_distance(centre, a, b) - shown.radius <= radius and amount > 0.0
                        and capsule_visible(self._collision(), self._eye(player), a, b, shown.radius)):
                    if player.hero.guid == BASTION and self._genji_blocks_melee(player, target, shown):
                        continue
                    self._damage(player, target, amount * self._nano_outgoing(player),
                                 False, loadout, command.frame, recovered_tick=recovered_tick,
                                 melee=True, boost_credit=credit)
            if player.hero.guid == BASTION:
                self.winston_barriers.hit_capsule(player, centre, centre, radius, amount, self._eye(player))

    def _genji_blocks_melee(self, attacker, target, shown):
        owner = target.owner
        script = getattr(owner, 'body_script', None)
        if (isinstance(owner, bots.Bot) or script is None or script.retired
                or owner.hero.guid != 0x02E0000000000029):
            return False
        instance = next((i for i in script.component.instances.values() if i.graph.index == 0x03A3), None)
        state = instance.states.get(59) if instance is not None else None
        return bool(state is not None and state.active and deflection.blocks_melee(
            state, target.entity, shown.feet, self._eye(attacker), owner.mover.yaw, owner.mover.pitch))

    def _advance_idle_reaper_casts(self) -> None:
        """Run past unposed command windows for Reaper casts, keeping the current frame available."""
        for player in self.match.players:
            anchor = self.reaper_cast_anchors.get(player.body)
            if anchor is None:
                continue
            if (player.gone or not player.has_body or not player.spawned
                    or not defensive_casts.reaper_cast_needs_clock(player)):
                continue
            tick, frame = anchor
            elapsed = max(0, self.tick - tick) * round(TICK * 1000)
            available = min(projectiles.MAX_LEASE_FRAME, frame + elapsed // player.body_script.frame_ms)
            player._advance_body_gap(available)

    def update(self, now: float, tick: int, frames: bool = True) -> None:
        """Each tick: keep where the bodies are, fly the rockets, respawn, send health that changed. The
        other players' body frames only on the ticks that send (`frames`)."""
        if self.match.collision_world is None:
            return
        retained = max(self.tick + 1, tick - COMBAT_CATCH_UP + 1)
        combat_ticks = {tick}
        if self.tick and tick > self.tick + 1:
            if any(bot.firing and not bot.dead and isinstance(bot.body_script, BotScript)
                   for bot in self.match.bots):
                combat_ticks.update(range(retained, tick + 1))
            reaper_cast = self.reaper_cast_anchors and any(
                defensive_casts.reaper_cast_needs_clock(player) for player in self.match.players
            )
            placed_barrier = (
                self.orisa_barriers.flights or self.orisa_barriers.pending or self.orisa_barriers.active
            )
            if reaper_cast or placed_barrier:
                # Resolve protection changes and each Tank sweep in world order,
                # even when the endpoint is outside the normal combat retention.
                for rocket in self.rockets:
                    if rocket.deflectable and not rocket.charge_eligible and rocket.last_tick is not None:
                        end = min(tick, rocket.last_tick + math.ceil(rocket.left / TICK))
                        combat_ticks.update(range(self.tick + 1, end + 1))
        for target in self._targets():
            history = self.history.setdefault(target.entity, deque(maxlen=HISTORY))
            history.append((tick, target.feet, target.height, target.eye, target.sentry_yaw))
        # Replay the retained combat frames using the most recent poses. Player input
        # accepted just before the scheduler caught up joins only at the endpoint.
        for at in sorted(combat_ticks):
            self.now, self.tick = now - (tick - at) * TICK, at
            self.winston_barriers.update()
            self.orisa_barriers.update(at)
            if at == tick:
                self.orisa_halt.update(at)
            if self.reaper_cast_anchors:
                self._advance_idle_reaper_casts()
            for rocket in list(self.rockets):
                if at == tick or rocket.last_tick is not None:
                    self._fly(rocket)
            self._bot_weapons(at)
            self.blizzard.update(at)
            self._advance_frost()
            sombra_hack.update(self)
            if self.hooks.flights:
                self.hooks.update(at)
        if self.grasp_shields:
            sigma_grasp.update(self)
        self.barriers.update()
        self.projectiles.update(tick, first_tick=retained)
        self.sigma.update()
        self.zenyatta.update()
        self.healing.update()  # retire expired rows after integrating the prior healing interval
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
        for entity in [key for key, sleep in self.sleeps.items() if sleep.end <= tick]:
            del self.sleeps[entity]
        for table in (self.history, self.vitals, self.replacement_ticks, self.stuns,
                      self.sleeps, self.frost, self.grasp_grants, self.reaper_cast_anchors,
                      self.tesla_frames):
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
            if dva.controlled_mech(player) is not None:
                continue
            data = mover_data(player.hero.body)
            crouched = player.mover.crouched
            height = data.crouch_height if crouched else data.stand_height
            eye = eye_height(player.hero.body, crouched)
            script = player.body_script
            sentry = (player.hero.guid == BASTION and script is not None
                      and script.component.entity_var(1216).value() == 2)
            sentry_yaw = round(player.mover.yaw) if sentry else None
            found.append(
                Target(player.body, player.team, tuple(player.mover.position), height, data.stand_radius, eye,
                       player, sentry_yaw)
            )  # fmt: skip
        for bot in self.match.bots:
            if bot.dva_shell:
                continue
            data = mover_data(bot.body)
            found.append(
                Target(
                    bot.entity, bot.team, tuple(bot.position), data.stand_height, data.stand_radius,
                    eye_height(bot.body), bot
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

    def _reaper_protected(self, actor, recovered_tick) -> bool:
        if recovered_tick is None:
            return defensive_casts.intangible(actor)
        if not reaper_shadow.is_reaper(actor):
            return False
        anchor = self.reaper_cast_anchors.get(actor.body)
        script = actor.body_script
        if anchor is None or script is None:
            return defensive_casts.intangible(actor)
        tick, frame = anchor
        frame_ms = script.frame_ms
        # A world-filled cursor leaves the current command frame open.
        idle_offset = int(script.frame > frame)
        time = frame * frame_ms + (recovered_tick - tick - idle_offset) * round(TICK * 1000)
        return any(state is not None and state.counter and state.start <= time
                   and (state.active or time < state.ended_at)
                   for state in defensive_casts.reaper_protection_states(actor))

    def _enemies(self, shooter, *, recovered_tick=None) -> list[Target]:
        free_for_all = self.match.game_map.free_for_all
        source = _source_entity(shooter)
        found = []
        for target in self._targets():
            if target.owner is shooter or target.entity == source:
                continue
            if not free_for_all and target.team == shooter.team:
                continue
            if self.vitals_of(target).dead or self._reaper_protected(target.owner, recovered_tick):
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
            pose = before
            feet = pose[1]
        else:
            share = (view_tick - before[0]) / (after[0] - before[0])
            feet = tuple(a + (b - a) * share for a, b in zip(before[1], after[1], strict=True))
            pose = before if share < 0.5 else after
        height, eye = pose[2:4]
        sentry_yaw = pose[4] if len(pose) > 4 else None
        return Target(target.entity, target.team, feet, height, target.radius, eye, target.owner, sentry_yaw)

    # --- shots -------------------------------------------------------------------------------------

    def _bot_weapons(self, tick: int) -> None:
        """Advance the weapon bodies of the map's firing bots and consume their volley reports."""
        from ow174.game import npc, npc_control

        for bot in tuple(self.match.bots):
            script = bot.body_script
            controlled = npc_control.manual(bot)
            frame = dva.actor_frame(bot, self.now, tick)
            if ((not bot.firing and not controlled) or bot.dead
                    or (bot.dva_mover is not None and not bot.dva_shell)
                    or not isinstance(script, BotScript) or script.frame > frame):
                continue
            try:
                allowed = npc_control.update_input(self, bot) if controlled else not self.is_stunned(bot)
                self.advance_bot_weapon(bot, script, frame, allowed and not bot.dva_shell)
                if bot.dva_shell_spent:
                    npc.remove_body(self.match, bot)
            except Exception:
                self._clear_pins(bot.entity)
                self.heavy_drives.pop(bot.entity, None)
                bot.body_script = None
                log.exception("[game] %s: %s's weapon script failed", self.match.label(), bot.name)

    def advance_bot_weapon(self, bot, script, frame, allowed, edges=()):
        snapshot = script.advance_live(frame, bot.position, edges=edges)
        from ow174.game import npc_control, sniper_reposition

        if npc_control.manual(bot):
            heavy_charge.advance(self, bot, allowed)
            sniper_reposition.advance(self, bot)
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

    def nano_targets(self, body: int, state) -> tuple:
        """Reconstruct the base allied Nano search using live bodies and static LOS.

        Range40 comes from0B9E #1648. The base cone10 is from its target State;
        native ranking, zoom modifiers and engine filters are not claimed here.
        """
        shooter = next((player for player in self.match.players
                        if player.has_body and player.body == body), None)
        if (shooter is None or self.match.collision_world is None or self.match.game_map.free_for_all
                or self.is_dead(shooter) or self.is_stunned(shooter)):
            return ()
        radius = _number(state.evaluate(state.node.field("m_radius"), watch=False), 0)
        if radius <= 0:
            return ()
        origin = self._eye(shooter)
        direction = aim(shooter.mover.yaw, shooter.mover.pitch)
        candidates = []
        for target in self._targets():
            if target.owner is shooter or target.team != shooter.team or self.vitals_of(target).dead:
                continue
            centre = (target.feet[0], target.feet[1] + target.height / 2, target.feet[2])
            delta = _sub(centre, origin)
            distance = math.sqrt(_dot(delta, delta))
            if not EPSILON < distance <= radius:
                continue
            alignment = _dot(direction, delta) / distance
            if alignment < math.cos(math.radians(10)):
                continue
            wall = ray_world(self._collision(), origin, tuple(v / distance for v in delta), distance)
            if wall is not None and wall < distance - EPSILON:
                continue
            candidates.append((-alignment, distance, target.entity))
        return (expr.Entity(min(candidates)[2]),) if candidates else ()

    def apply_nano(self, body: int, targets, duration: float) -> bool:
        """Apply0B9E's released0269 effect to its selected living ally.

        B9F defines250 healing and additive+0.5 outgoing/-0.5 incoming damage.
        The authored source graph supplies duration8. This reconstructed target
        deadline survives caster death/retirement; native presentation is separate.
        """
        shooter = next((player for player in self.match.players
                        if player.has_body and player.body == body), None)
        if (shooter is None or self.match.game_map.free_for_all or self.is_dead(shooter)
                or self.is_stunned(shooter)
                or not math.isfinite(duration) or duration <= 0):
            return False
        targets = (targets,) if isinstance(targets, expr.Entity) else targets
        if type(targets) is not tuple:
            return False
        applied = False
        for target in self._targets():
            if (expr.Entity(target.entity) not in targets or target.owner is shooter
                    or target.team != shooter.team):
                continue
            vitals = self.vitals_of(target)
            if vitals.dead:
                continue
            given = vitals.heal(250.0*self.healing.factor(target))
            if given > 0:
                shooter.stats.add(stats.HEALING, given)
                self._sync_body_health(target.owner, vitals)
            vitals.nano_until = max(vitals.nano_until, self.now + duration)
            applied = True
        return applied

    def _eye(self, player) -> tuple:
        x, y, z = player.mover.position
        crouched = player.mover.crouched
        height = eye_height(player.hero.body, crouched)
        return (x, y + height, z)

    def _shoot(
        self, shooter, command, state, number: int, time: int,
        *, activation: tuple[int, int], snapshot: Snapshot | None = None, recovered_tick=None,
    ) -> None:
        node = state.node
        shot_frame = time // state.instance.component.frame_ms

        def field(name, default=None):
            cfg = node.field(name)
            return default if cfg is None else _number(state.evaluate(cfg, watch=False), default)

        npc = isinstance(shooter, bots.Bot)
        if npc:
            shot_frame = dva.actor_world_frame(shooter, time, shot_frame)
            x, y, z = shooter.position
            # Use the server body pose until the animated muzzle transform is available.
            origin, direction = (x, y + EYE, z), aim(round(shooter.yaw), round(shooter.pitch))
        else:
            from ow174.game.mccree_fan import fan_direction

            origin, direction = self._eye(shooter), aim(*command.aim())
            direction = fan_direction(shooter, state, direction, number, time, activation)
        recon = (not npc and shooter.hero.guid == BASTION
                 and (state.instance.graph.index, node.pos) == (0x012E, 30))
        # Reconstruct Recon's omitted multiplier while retaining explicit authored values.
        if not npc and shooter.hero.guid == ana_grenade.HERO and state.instance.graph.index == ana_nano.GRAPH:
            if state.index == 1:
                ana_nano.apply(self, shooter, state)
            return
        damage_scalar = self._nano_outgoing(shooter)
        direct = hit_def(state, node.field("m_6396149F"), critical_default=2.0 if recon else 1.0)
        if direct is not None and damage_scalar != 1.0:
            direct = replace(direct, amount=direct.amount * damage_scalar)
        if (not npc and shooter.hero.guid == defensive_casts.ORISA
                and state.instance.graph.index == orisa_barrier.GRAPH):
            self.orisa_barriers.launch(shooter, state, origin, direction)
            return
        if (not npc and shooter.hero.guid == defensive_casts.ORISA
                and state.instance.graph.index == orisa_halt.GRAPH):
            self.orisa_halt.launch(shooter, state, origin, direction)
            return
        if (not npc and direct is not None and state.instance.graph.index == 0x0164
                and shooter.hero.guid == 0x02E0000000000020):
            from ow174.game import mercy_boost

            credit = mercy_boost.credit(self, shooter)
            if credit is not None:
                direct = replace(direct, amount=expr.f32(direct.amount*credit[1]), boost_credit=credit)
        speed = field("m_projectileSpeed")
        if speed:
            from ow174.game import flashbang

            gravity = None if npc else flashbang.flashbang_gravity(shooter, state)
            if gravity is not None:
                direction = flashbang.flashbang_direction(state, direction)
            lifetime = field("m_projectileLifetime", 5.0) or 5.0
            reach = field("m_FEC435C6")
            if reach:
                lifetime = min(lifetime, reach / speed)
            # Reconstructed owner splash from the Tank explosion's authored scalar.
            tank_splash = (not npc and shooter.hero.guid == BASTION
                           and (state.instance.graph.index, node.pos) == (0x024F, 23))
            blast = hit_def(state, node.field("m_D2D11CE9"), tank_splash=tank_splash)
            if blast is not None and damage_scalar != 1.0:
                blast = replace(blast, amount=blast.amount * damage_scalar)
            entity_def = (node.field("m_projectileEntity") or {}).get("m_entityDef")
            definition = state.evaluate(entity_def, watch=False) if entity_def is not None else None
            directions = launch_ring.projectile_directions(state, direction, number, activation)
            if directions is None:
                log.warning('[game] %s: unsupported multi-projectile launch %04X/%d',
                            self.match.label(), state.instance.graph.index, node.pos)
                return
            damaging = (direct is not None and direct.amount > 0.0) or (
                blast is not None and blast.amount > 0.0
            )
            for direction in directions:
                shot = None
                if isinstance(definition, expr.Asset):
                    shot = self.projectiles.fired(
                        shooter, state, shot_frame, definition.guid, lifetime, origin, direction,
                        activation=activation, snapshot=snapshot,
                    )
                if not npc and self.blizzard.launch(
                    shooter, state, origin, direction,
                    definition.guid if isinstance(definition, expr.Asset) else None,
                    speed, lifetime, shot,
                ):
                    continue
                if damaging or shot is not None:
                    self.rockets.append(
                        Rocket(
                            shooter, shooter.team, origin, direction, speed, lifetime, direct, blast, shot,
                            last_tick=self.tick if npc else None,
                            definition=definition.guid if isinstance(definition, expr.Asset) else None,
                            gravity=gravity if gravity is not None else 0.0,
                            velocity=(tuple(expr.f32(value * speed) for value in direction)
                                      if gravity is not None else None),
                            detonate_on_expiry=gravity is not None,
                            target_limit=mei_frost.projectile_target_limit(state),
                            charge_eligible=state.instance.graph.index != 0x024F,
                            deflectable=not npc and shooter.hero.guid == BASTION,
                            ana_grenade=(not npc and shooter.hero.guid == ana_grenade.HERO
                                         and state.instance.graph.index == 0x0B97
                                         and blast is not None and blast.effect == ana_grenade.EFFECT),
                        )
                        )
            return
        if direct is None or direct.amount <= 0.0:
            return
        if (not npc and shooter.hero.guid == BASTION
                and state.spread_ramp is not None
                and (state.instance.graph.index, node.pos) in ((0x012E, 30), (0x013A, 26))):
            launch = node.field("m_5EC1EB38")
            low, high = (expr.to_float(state.evaluate(launch[name], watch=False))
                         for name in ("m_7F0FED02", "m_429A6C85"))
            # AE71CF passes the launch count before AE73F2 increments it.
            normalized = state.spread_after_shots(number)
            degrees = expr.f32(expr.f32(expr.f32(high - low) * normalized) + expr.f32(low))
            cone = expr.f32(degrees * expr.f32(math.pi / 180))
            # Server identities and uniform radius are reconstructed launch inputs.
            base = weapon_spread.seed_base(shooter.body, state.instance.id, node.pos, activation[0])
            direction = weapon_spread.direction(direction, cone, base, number)
        reach = field("m_FEC435C6", RANGE) or RANGE
        # The input supplies this simulation frame's aim and view time. Notices use the volley clock.
        view_tick = self.tick
        if command is not None:
            view_tick = command.frame - command.view_delay(shooter.quanta) * 1e6 / shooter.quanta
        directions = ring_directions(state, direction) if npc and shooter.body == dva.MECH else None
        for ray in directions if directions is not None else (direction,):
            self._hitscan(shooter, origin, ray, direct, reach, view_tick, shot_frame,
                          deflectable=not npc and shooter.hero.guid == BASTION,
                          world_tick=self.tick if npc else None, recovered_tick=recovered_tick)

    def _deflector(self, shooter, origin, direction, reach, view_tick=None):
        """Known Genji03A3/st10 only; do not imply general native filter support."""
        closest = None
        for target in self._enemies(shooter):
            owner = target.owner
            script = getattr(owner, 'body_script', None)
            if (isinstance(owner, bots.Bot) or script is None or script.retired
                    or owner.hero.guid != 0x02E0000000000029):
                continue
            instance = next((i for i in script.component.instances.values() if i.graph.index == 0x03A3), None)
            state = instance.states.get(10) if instance is not None else None
            if state is None or not state.active or state.node.cls != 'STUStatescriptStateDeflectProjectiles':
                continue
            shown = self.seen(target, view_tick) if view_tick is not None else target
            if shown is None:
                continue
            eye = (shown.feet[0], shown.feet[1] + shown.eye, shown.feet[2])
            hit, outgoing = deflection.intercept(state, eye, owner.mover.yaw, owner.mover.pitch,
                                                 origin, direction, reach)
            if hit is not None and (closest is None or hit < closest[0]):
                closest = (hit, target, outgoing)
        return closest

    def _absorber(self, shooter, origin, direction, reach):
        """Active Sigma 1D02/st1 with authored boxes and a reconstructed aim transform."""
        closest = None
        for target in self._enemies(shooter):
            owner = target.owner
            if getattr(getattr(owner, "hero", None), "guid", None) != 0x02E000000000023B:
                continue
            script = owner.body_script
            if script is None or script.retired or script.component.owner_dead:
                continue
            instance = next((i for i in script.component.instances.values() if i.graph.index == 0x1D02), None)
            state = instance.states.get(1) if instance is not None else None
            if state is None or not state.active or state.ending:
                continue
            yaw, pitch = owner.mover.yaw, owner.mover.pitch
            angle = _number(state.evaluate(state.node.field("m_025D0C39"), watch=False), 0.0)
            if angle <= 0 or -_dot(aim(yaw, pitch), direction) < math.cos(math.radians(angle / 2)):
                continue
            hit, _ = deflection.intercept(state, self._eye(owner), yaw, pitch, origin, direction, reach)
            if hit is not None and (closest is None or hit < closest[0]):
                closest = hit, state
        return closest

    @staticmethod
    def _absorb_hit(state, shooter, amount):
        instance = state.instance
        instance.write((expr.INSTANCE, 6584), expr.f32(amount))
        instance.write((expr.INSTANCE, 41), expr.Entity(_source_entity(shooter)))
        instance.follow(state.node, "m_160B5DBE", state)
        instance.component.run_now()

    def _barrier_contact(self, shooter, origin, direction, reach):
        mesh = reinhardt_barrier.intercept(self, shooter, origin, direction, reach)
        sphere = self.winston_barriers.first(shooter, origin, direction, reach)
        placed = self.orisa_barriers.trace(shooter, origin, direction, reach)
        return min((hit for hit in (mesh, sphere, placed) if hit is not None),
                   key=lambda hit: hit[0], default=None)

    def _damage_barrier(self, barrier, amount):
        if isinstance(barrier, orisa_barrier.Barrier):
            self.orisa_barriers.damage(barrier, amount)
        elif isinstance(barrier, winston_barrier.Bubble):
            barrier.damage(amount)
        else:
            reinhardt_barrier.damage(barrier, amount)

    def _hitscan(self, shooter, origin, direction, direct, reach, view_tick, shot_frame,
                 *, deflectable=False, world_tick=None, reflections=8, recovered_tick=None):
        best = None
        for target in self._enemies(shooter, recovered_tick=recovered_tick):
            shown = self.seen(target, view_tick)
            if shown is None:
                continue
            a, b = shown.segment()
            t = ray_capsule(origin, direction, a, b, shown.radius)
            critical = False
            if shown.sentry_yaw is not None:
                core = ray_capsule(origin, direction, *shown.core_segment(), SENTRY_CORE_RADIUS)
                if core is not None and (t is None or core < t):
                    t, critical = core, True
            elif t is not None:
                hit_y = origin[1] + direction[1] * t
                critical = hit_y >= shown.feet[1] + shown.eye - HEAD_BELOW
            if t is not None and t <= reach and (best is None or t < best[0]):
                best = (t, target, critical)
        stop = best[0] if best is not None else reach
        wall = ray_world(self._collision(), origin, direction, stop)
        limit = min(stop, wall) if wall is not None else stop
        if deflectable:
            shield = self._barrier_contact(shooter, origin, direction, limit)
        else:
            # Zarya's cast owns hitscan damage interception in _damage.
            shield = self.barriers.ray(shooter, origin, direction, limit, include_bubbles=False)
        if shield is not None and shield[0] < limit - EPSILON:
            limit = shield[0]
        else:
            shield = None
        if deflectable:
            reflected = self._deflector(shooter, origin, direction, limit, view_tick) if reflections else None
            absorber = self._absorber(shooter, origin, direction, reflected[0] if reflected else limit)
            matrix_limit = absorber[0] if absorber else reflected[0] if reflected else limit
            matrix = dva_matrix.intercept(self, shooter, origin, direction, matrix_limit)
            if matrix is not None and matrix < matrix_limit - EPSILON:
                return
            if absorber is not None and absorber[0] < limit - EPSILON:
                t, state = absorber
                self._absorb_hit(state, shooter, direct.amount * direct.scale(t))
                return
            if reflected is not None and reflected[0] < limit - EPSILON:
                t, defender, outgoing = reflected
                return self._hitscan(defender.owner, _along(origin, direction, t), outgoing, direct,
                                     reach - t, self.tick, shot_frame, deflectable=True,
                                     world_tick=world_tick, reflections=reflections - 1,
                                     recovered_tick=recovered_tick)
        if shield is not None:
            amount = direct.amount * direct.scale(shield[0])
            if deflectable:
                self._damage_barrier(shield[1], amount)
            else:
                self.barriers.damage(shield[1], amount, _source_entity(shooter))
            return
        if best is None or (wall is not None and wall < best[0]):
            return
        t, target, critical = best
        critical = critical and defensive_casts.fortify_scale(target.owner) is None
        amount = direct.amount * direct.scale(t) * (direct.critical if critical else 1.0)
        if self._damage(
            shooter, target, amount, critical and direct.critical > 1.0, direct.loadout, shot_frame,
            world_tick=world_tick, recovered_tick=recovered_tick,
        ):
            self._hit_effect(target, direct.effect, shooter)

    def _fly(self, rocket: Rocket) -> None:
        """Sweep elapsed world time against the current bodies and static collision."""
        if rocket.definition is not None and not (rocket.deflectable or rocket.ana_grenade):
            self._fly_defined_projectile(rocket)
            return
        # Player input can arrive immediately before the scheduler accounts for a gap.
        elapsed = 1 if rocket.last_tick is None else self.tick - rocket.last_tick
        if elapsed <= 0:
            return
        rocket.last_tick = self.tick
        duration = min(elapsed * TICK, rocket.left)
        if duration <= 0.0:
            self._landed(rocket)
            return
        self._sweep_rocket(rocket, duration)

    def _sweep_rocket(self, rocket: Rocket, duration: float, reflections=8) -> None:
        # Sweep the remaining flight even when the range or lifetime ends inside this tick.
        step = rocket.speed * duration
        best = None
        candidates = self._targets() if rocket.ana_grenade else self._enemies(rocket.shooter)
        for target in candidates:
            if rocket.ana_grenade and (target.owner is rocket.shooter or self.vitals_of(target).dead):
                continue
            a, b = target.segment()
            t = ray_capsule(rocket.position, rocket.direction, a, b, target.radius)
            if t is not None and t <= step and (best is None or t < best[0]):
                best = (t, target)
        wall = ray_world(self._collision(), rocket.position, rocket.direction, step)
        if wall is not None and (best is None or wall < best[0]):
            best = (wall, None)
        shield = None
        limit = best[0] if best is not None else step
        if rocket.deflectable or rocket.ana_grenade:
            shield = self._barrier_contact(rocket.shooter, rocket.position, rocket.direction, limit)
            if shield is not None and (best is None or shield[0] < limit - EPSILON):
                limit = shield[0]
                best = (limit, None)
            else:
                shield = None
        if rocket.deflectable:
            reflected = (self._deflector(rocket.shooter, rocket.position, rocket.direction, limit)
                         if reflections else None)
            absorber = self._absorber(rocket.shooter, rocket.position, rocket.direction,
                                     reflected[0] if reflected else limit)
            matrix_limit = absorber[0] if absorber else reflected[0] if reflected else limit
            matrix = dva_matrix.intercept(
                self, rocket.shooter, rocket.position, rocket.direction, matrix_limit)
            if matrix is not None and (matrix < matrix_limit - EPSILON or (
                    best is None and reflected is None and absorber is None)):
                rocket.flown += matrix / rocket.speed
                self._landed(rocket)
                return
            if absorber is not None and (best is None or absorber[0] < limit - EPSILON):
                distance, state = absorber
                rocket.flown += distance / rocket.speed
                amount = (rocket.direct.amount * rocket.direct.scale(rocket.flown * rocket.speed)
                          if rocket.direct is not None else 0.0)
                amount += rocket.blast.amount if rocket.blast is not None else 0.0
                self._absorb_hit(state, rocket.shooter, amount)
                self._landed(rocket)
                return
            if reflected is not None and (best is None or reflected[0] < best[0] - EPSILON):
                distance, defender, outgoing = reflected
                spent = distance / rocket.speed
                rocket.position = _along(rocket.position, rocket.direction, distance)
                rocket.flown += spent
                rocket.left -= spent
                if rocket.shot is not None:
                    # The old predicted projectile ends here. A native reflected
                    # projectile creation/wire lifetime remains separate work.
                    self.projectiles.flown(rocket.shot, rocket.flown)
                    rocket.shot = None
                rocket.shooter, rocket.team, rocket.direction = defender.owner, defender.team, outgoing
                remaining = min(duration - spent, rocket.left)
                if remaining > 0:
                    self._sweep_rocket(rocket, remaining, reflections - 1)
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
        self._landed(rocket)
        point = _along(rocket.position, rocket.direction, t)
        if rocket.ana_grenade:
            self._ana_grenade_impact(rocket, point, struck is None)
            if shield is not None:
                self._damage_barrier(shield[1], ana_grenade.DAMAGE)
            return
        direct = rocket.direct
        if struck is not None and direct is not None and self._damage(
            rocket.shooter, struck, direct.amount, False, direct.loadout, self.tick,
            world_tick=self.tick,
            gain_charge=rocket.charge_eligible,
        ):
            self._hit_effect(struck, direct.effect, rocket.shooter)
        blast = rocket.blast
        if blast is not None and blast.amount > 0.0:
            offset = wall is not None or shield is not None
            self._rocket_splash(rocket, point, offset)
            if rocket.deflectable:
                origin = _along(point, rocket.direction, -0.0001) if offset else point
                self.orisa_barriers.splash(
                    rocket.shooter, point, origin, blast, struck=shield[1] if shield else None,
                )
        if shield is not None:
            amount = rocket.direct.amount if rocket.direct is not None else 0.0
            amount += blast.amount if blast is not None else 0.0
            self._damage_barrier(shield[1], amount)

    def _rocket_splash(self, rocket: Rocket, point, offset_origin: bool) -> None:
        blast = rocket.blast
        targets = self._enemies(rocket.shooter)
        if (blast.self_splash > 0.0 and not isinstance(rocket.shooter, bots.Bot)
                and rocket.shooter.hero.guid == BASTION):
            targets.extend(target for target in self._targets() if target.owner is rocket.shooter)
        # Resolve blast visibility from the approach side before applying shield damage.
        visible_from = _along(point, rocket.direction, -0.0001) if offset_origin else point
        for target in targets:
            a, b = target.segment()
            distance = max(0.0, segment_distance(point, a, b) - target.radius)
            scalar = blast.splash(distance)
            if target.owner is rocket.shooter:
                scalar *= blast.self_splash
            if scalar > 0.0 and self._rocket_splash_visible(rocket, visible_from, a, b, target.radius):
                if not self._damage(
                    rocket.shooter, target, blast.amount * scalar, False, blast.loadout, self.tick,
                    world_tick=self.tick,
                    gain_charge=rocket.charge_eligible,
                ):
                    continue
                self._hit_effect(target, blast.effect, rocket.shooter)
                if (blast.knockback and not self.vitals_of(target).dead
                        and not defensive_casts.invulnerable(target.owner)
                        and not defensive_casts.fortify_force_immune(target.owner)):
                    # Reconstructed host contact: vector from impact to the
                    # nearest point on the target capsule axis.
                    nearest = (a[0], max(a[1], min(point[1], b[1])), a[2])
                    impulse = blast.impulse(_sub(nearest, point), distance)
                    if any(impulse):
                        if isinstance(target.owner, bots.Bot):
                            target.owner.apply_impulse(impulse, self.tick, self._collision())
                        else:
                            target.owner.mover.apply_impulse(impulse)

    def _rocket_splash_visible(self, rocket: Rocket, origin, a, b, radius: float) -> bool:
        if not capsule_visible(self._collision(), origin, a, b, radius):
            return False
        if not (rocket.deflectable or rocket.ana_grenade):
            return True
        # Reconstructed barrier LOS samples the target capsule midpoint.
        delta = _sub(_along(a, _sub(b, a), 0.5), origin)
        distance = math.sqrt(sum(axis * axis for axis in delta))
        return distance == 0 or self._barrier_contact(
            rocket.shooter, origin, tuple(axis / distance for axis in delta), distance) is None

    def _ana_grenade_impact(self, rocket: Rocket, point, offset_origin: bool) -> None:
        shooter, blast = rocket.shooter, rocket.blast
        origin = _along(point, rocket.direction, -0.0001) if offset_origin else point
        for target in self._targets():
            vitals = self.vitals_of(target)
            if vitals.dead:
                continue
            a, b = target.segment()
            scalar = blast.splash(max(0.0, segment_distance(point, a, b) - target.radius))
            if scalar <= 0 or not self._rocket_splash_visible(rocket, origin, a, b, target.radius):
                continue
            ally = target.owner is shooter or (not self.match.game_map.free_for_all
                                              and shooter.team == target.team)
            end = self.now + ana_grenade.DURATION
            if ally:
                # The friendly play replaces the old boost; its own impact heal precedes the new boost.
                vitals.healing_boost = None
                self._heal(shooter, target, vitals, ana_grenade.HEAL * scalar)
                vitals.healing_boost = self.now + ana_grenade.BOOST_DELAY, end
            elif self._damage(shooter, target, ana_grenade.DAMAGE * scalar, False, blast.loadout,
                              self.tick, world_tick=self.tick) and not vitals.dead:
                vitals.antiheal_until = end

    def _fly_defined_projectile(self, rocket: Rocket) -> None:
        """Sweep elapsed world time against the current bodies and static collision."""
        # Player input can arrive immediately before the scheduler accounts for a gap.
        elapsed = 1 if rocket.last_tick is None else self.tick - rocket.last_tick
        if elapsed <= 0:
            return
        rocket.last_tick = self.tick
        if rocket.velocity is not None:
            from ow174.game.flashbang import gravity_velocity

            for _ in range(elapsed):
                if rocket not in self.rockets:
                    break
                duration = min(TICK, rocket.left)
                if duration <= 0.0:
                    self._expire_projectile(rocket)
                    break
                rocket.velocity = gravity_velocity(rocket.velocity, rocket.gravity, duration)
                rocket.speed = math.sqrt(_dot(rocket.velocity, rocket.velocity))
                if rocket.speed:
                    rocket.direction = tuple(value/rocket.speed for value in rocket.velocity)
                self._fly_for(rocket, duration)
            return
        duration = min(elapsed * TICK, rocket.left)
        if duration <= 0.0:
            self._landed(rocket)
            return
        self._fly_for(rocket, duration)

    def _matrix_hit(self, shooter, origin, direction, reach, definition, *, bastion_ray=False):
        """Nearest live enemy0407's current active boxes; finite-Volley hosting only.

        No historical Matrix pose, generic native097 filter or Q ownership is inferred.
        """
        if not bastion_ray and not matrix.absorbable(definition):
            return None
        best = None
        for target in self._enemies(shooter):
            actor = target.owner
            if isinstance(actor, bots.Bot):
                if actor.dva_mover is None:
                    continue
                x, y, z = target.feet
                eye = (x, y + target.eye, z)
                forward = aim(round(actor.yaw), round(actor.pitch))
            else:
                eye = self._eye(actor)
                forward = aim(actor.mover.yaw, actor.mover.pitch)
            distance = matrix.matrix_distance(actor, origin, direction, reach, eye, forward)
            if distance is not None and (best is None or distance < best):
                best = distance
        return best

    def _fly_for(self, rocket: Rocket, duration: float, redirects: int = 0) -> None:
        """Spend one elapsed interval, retaining its residual travel after a deflection."""
        # Sweep the remaining flight even when the range or lifetime ends inside this tick.
        step = rocket.speed * duration
        best = None
        candidates = self._enemies(rocket.shooter)
        if rocket.blast is not None and rocket.blast.effect == healing.GRENADE:
            candidates = (t for t in self._targets() if t.owner is not rocket.shooter
                          and not self.vitals_of(t).dead)
        for target in candidates:
            a, b = target.segment()
            t = ray_capsule(rocket.position, rocket.direction, a, b, target.radius)
            if (target.entity not in rocket.hit_entities and t is not None and t <= step
                    and (best is None or t < best[0])):
                best = (t, target, "body", None)
            hostile = self.match.game_map.free_for_all or target.team != rocket.shooter.team
            reflected = None
            if hostile and rocket.definition != mei_frost.SPRAY_PROJECTILE:
                reflected = deflect_ray(target, rocket.position, rocket.direction, step)
            if reflected is not None and (best is None or reflected < best[0]):
                best = (reflected, target, "reflect", None)
            absorbed = (projectile_box(target, rocket.position, rocket.direction, step, 0x1D02)
                        if hostile else None)
            if absorbed is not None and (best is None or absorbed[0] < best[0]):
                best = (absorbed[0], target, "absorb", absorbed[1])
        wall = ray_world(self._collision(), rocket.position, rocket.direction, step)
        if wall is not None and (best is None or wall < best[0]):
            best = (wall, None, "body", None)
        barrier = self.barriers.ray(rocket.shooter, rocket.position, rocket.direction, step)
        if barrier is not None and (best is None or barrier[0] < best[0]):
            best = (barrier[0], None, "barrier", barrier[1])
        consumed = self._matrix_hit(rocket.shooter, rocket.position, rocket.direction, step,
                                    rocket.definition)
        if consumed is not None and (best is None or consumed < best[0]):
            best = (consumed, None, 'matrix', None)
        if best is None:
            rocket.position = _along(rocket.position, rocket.direction, step)
            rocket.flown += duration
            rocket.left -= duration
            if rocket.left <= 0.0:
                self._expire_projectile(rocket)
            return
        t, struck, interaction, defense_state = best
        rocket.flown += t / rocket.speed
        if interaction == 'matrix':
            self.rockets.remove(rocket)
            if rocket.shot is not None:
                self.projectiles._end(rocket.shot, self.tick, False)
            return
        if interaction == 'barrier':
            distance = rocket.flown*rocket.speed
            amount = rocket.direct.amount*rocket.direct.scale(distance) if rocket.direct else 0.0
            amount += rocket.blast.amount if rocket.blast else 0.0
            self.barriers.damage(defense_state, amount, _source_entity(rocket.shooter))
        if interaction == "absorb":
            self.sigma.absorb(defense_state, rocket, rocket.flown * rocket.speed)
            self.rockets.remove(rocket)
            if rocket.shot is not None:
                self.projectiles._end(rocket.shot, self.tick, False)
            return  # consumed projectiles neither hit a body nor explode
        if interaction == "reflect":
            point = _along(rocket.position, rocket.direction, t)
            rocket.left -= t / rocket.speed
            rocket.shooter, rocket.team = struck.owner, struck.team
            if rocket.direct is not None and rocket.direct.boost_credit is not None:
                rocket.direct = replace(rocket.direct, boost_credit=None)
            rocket.direction = aim(struck.owner.mover.yaw, struck.owner.mover.pitch)
            if rocket.velocity is not None:
                rocket.velocity = tuple(expr.f32(value*rocket.speed) for value in rocket.direction)
            rocket.position = _along(point, rocket.direction, 1e-4)
            remaining = duration - t / rocket.speed
            if redirects >= 8:
                # Overlapping boxes must not create an immortal zero-distance
                # reflection cycle. Consume remaining time under the bounded
                # reconstruction policy, without fabricating travel distance.
                rocket.left -= remaining
                if rocket.left <= 0:
                    self._expire_projectile(rocket)
            elif remaining > EPSILON:
                self._fly_for(rocket, remaining, redirects + 1)
            return
        piercing = (struck is not None and rocket.blast is None and rocket.direct is not None
                    and rocket.direct.amount > 0.0 and len(rocket.hit_entities) + 1 < rocket.target_limit)
        if not piercing:
            self._landed(rocket)
        point = _along(rocket.position, rocket.direction, t)
        if struck is not None and rocket.direct is not None and rocket.direct.amount > 0.0:
            direct = rocket.direct
            critical = point[1] >= struck.feet[1] + struck.eye - HEAD_BELOW and direct.critical > 1.0
            # As with hitscan, apply distance falloff and the hit's critical
            # scalar before the target's shield/armour/health pools. Splash
            # below remains a separate, noncritical hit.
            distance = rocket.flown * rocket.speed
            amount = direct.amount * direct.scale(distance) * (direct.critical if critical else 1.0)
            self._damage(
                rocket.shooter, struck, amount, critical, direct.loadout, self.tick,
                world_tick=self.tick, boost_credit=direct.boost_credit,
            )
            self._hit_effect(struck, rocket.direct.effect)
        if piercing:
            rocket.hit_entities += (struck.entity,)
            rocket.position = _along(point, rocket.direction, 1e-4)
            elapsed = t / rocket.speed
            rocket.left -= elapsed
            remaining = min(duration - elapsed, rocket.left)
            if remaining > EPSILON:
                self._fly_for(rocket, remaining, redirects)
            elif rocket.left <= 0:
                self._expire_projectile(rocket)
            return
        extra = defense_state if interaction == 'barrier' else None
        self._explode(rocket, point, occluded=wall is not None or extra is not None, extra=extra)

    def _expire_projectile(self, rocket: Rocket) -> None:
        self._landed(rocket)
        if rocket.detonate_on_expiry:
            self._explode(rocket, rocket.position)

    def _explode(self, rocket: Rocket, point: tuple, *, occluded=False, extra=None) -> None:
        blast = rocket.blast
        if blast is None or blast.amount <= 0.0:
            return
        visible_from = _along(point, rocket.direction, -0.0001) if occluded else point
        if blast.effect == healing.GRENADE:
            self.healing.grenade(rocket, point, visible_from, extra)
            return
        for target in self._enemies(rocket.shooter):
            a, b = target.segment()
            distance = max(0.0, segment_distance(point, a, b) - target.radius)
            scalar = blast.splash(distance)
            if scalar > 0.0 and self.area_visible(rocket.shooter, visible_from, target, extra):
                self._damage(
                    rocket.shooter, target, blast.amount * scalar, False, blast.loadout, self.tick,
                    world_tick=self.tick,
                )
                self._hit_effect(target, blast.effect)

    def area_visible(self, shooter, origin, target, extra_barrier=None):
        a, b = target.segment()
        middle = tuple((a[k]+b[k])/2 for k in range(3))
        delta = _sub(middle, origin)
        length = math.sqrt(_dot(delta, delta))
        blocked = (length > EPSILON and self.barriers.ray(
            shooter, origin, tuple(v/length for v in delta),
            max(0.0, length-target.radius), extra=extra_barrier,
        ) is not None)
        return not blocked and capsule_visible(self._collision(), origin, a, b, target.radius)

    def _landed(self, rocket: Rocket) -> None:
        """The projectile ended (hit, range, lifetime): the predicted one's release follows."""
        if rocket in self.rockets:
            self.rockets.remove(rocket)
        if rocket.shot is not None:
            self.projectiles.flown(rocket.shot, rocket.flown)

    def heal_area(self, healer, centre, radius: float, amount: float, *, seconds=0.0, end_tick=None) -> None:
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
            self._heal(healer, target, vitals, amount)

    def _heal(self, healer, target: Target, vitals: Vitals, amount: float) -> None:
        given = vitals.heal(amount * ana_grenade.healing_factor(target.owner, vitals, self.now))
        if given > 0.0:
            healer.stats.add(stats.HEALING, given)
            self._sync_body_health(target.owner, vitals)

    @staticmethod
    def _sync_body_health(owner, vitals: Vitals) -> None:
        script = getattr(owner, "body_script", None)
        if script is not None:
            script.set_health(vitals.total, sum(vitals.most.values()),
                              health_pools=vitals.component(vitals.ids)[51])

    def _collision(self):
        from ow174.game.collision import CollisionError

        world = self.match.collision_world
        if world is None:
            raise CollisionError("match collision is not ready")
        return world

    # --- damage and death --------------------------------------------------------------------------

    @staticmethod
    def _bastion_damage_scalar(owner) -> float:
        """Reconstructed Ironclad: 012F/st124's authored additive damage modifier.

        Apply before armour. Do not infer generic CombatModFilter semantics or
        enable unrelated hero filters from this one known graph/state pair.
        """
        if isinstance(owner, bots.Bot) or owner.hero.guid != 0x02E0000000000015 or owner.body_script is None:
            return 1.0
        for instance in owner.body_script.component.instances.values():
            state = instance.states.get(124) if instance.graph.index == 0x012F else None
            if state is not None and state.active:
                modifier = _number(state.evaluate(state.node.field("m_50530EAB"), watch=False), 0.0)
                return max(0.0, 1.0 + modifier) if math.isfinite(modifier) else 1.0
        return 1.0

    def _damage(
        self, shooter, target: Target, amount: float, critical: bool, loadout, frame: int,
        *, world_tick: int | None = None, gain_charge: bool = True, recovered_tick=None,
        melee: bool = False, boost_credit=None,
    ) -> bool:
        vitals = self.vitals_of(target)
        protected = (self._reaper_protected(target.owner, recovered_tick)
                     if recovered_tick is not None and reaper_shadow.is_reaper(target.owner)
                     else defensive_casts.invulnerable(target.owner))
        if vitals.dead or amount <= 0.0 or protected or self.zenyatta.ultimate(target.owner):
            return False
        hostile = shooter is not target.owner and (
            self.match.game_map.free_for_all or shooter.team != target.team)
        if hostile and self.zarya_barriers.intercept(target.owner, amount, _source_entity(shooter)):
            return False
        if hostile and (bubble := self.barriers.bubble(target)) is not None:
            self.barriers.damage(bubble, amount, _source_entity(shooter))
            return False
        amount *= self._bastion_damage_scalar(target.owner)
        amount *= self.zenyatta.boost(target)
        fortify = defensive_casts.fortify_scale(target.owner)
        if fortify is not None:
            amount *= fortify
        if self.now < vitals.nano_until:
            amount *= ana_nano.INCOMING
        base = 0.0
        if boost_credit:
            base = sum(loss for _, loss in vitals._damage_losses(amount / boost_credit[1]))
        dealt = vitals.damage(amount)
        extra = max(0.0, dealt-base) if boost_credit else 0.0
        if extra:
            from ow174.game import mercy_boost

            source, scale = boost_credit
            mercy_boost.award(source, target.entity, dealt, extra, scale-1)
        if dealt <= 0.0:
            return False
        self._wake_from_damage(target)
        self.zenyatta.damaged(target)
        sombra_hack.damage_interrupt(_source_entity(shooter), target)
        if (gain_charge and shooter is not target.owner
                and (self.match.game_map.free_for_all or shooter.team != target.team)):
            gain = getattr(getattr(shooter, "body_script", None), "gain_ult_charge", None)
            if gain is not None:
                gain(dealt)
        killed = vitals.now[HEALTH_POOL] <= EPSILON
        if killed:
            vitals.now[HEALTH_POOL] = 0.0
            vitals.died_at = self.now
            vitals.death_tick = self.match.tick if world_tick is None else world_tick
            vitals.killer = _source_entity(shooter)
        if shooter is not target.owner and not isinstance(shooter, bots.Bot):
            flags = (CRITICAL if critical else 0) | (KILLING if killed else 0)
            self._tell_hit(shooter, target, dealt, flags, frame)
            shooter.stats.add(stats.HERO_DAMAGE, dealt)
            self.zenyatta.charge(shooter, dealt-extra)
        victim = target.owner
        if (not killed and loadout == mei_frost.SPRAY_LOADOUT
                and getattr(getattr(shooter, "hero", None), "guid", None) == mei_frost.HERO):
            self._hit_effect(target, mei_frost.EFFECT, shooter)
        if melee and not killed and isinstance(victim, bots.Bot) and victim.entity in self.pins:
            from ow174.game.assassin import release_from_melee

            release_from_melee(self, victim)
        if killed and isinstance(victim, bots.Bot):
            # The NPC death script owns its terminal health change and death event ordering.
            self._bot_died(shooter, victim, critical, loadout, vitals.death_tick)
            return True
        self._sync_body_health(victim, vitals)
        if killed:
            self._died(shooter, victim, critical, loadout)
        return True

    def _nano_outgoing(self, shooter) -> float:
        vitals = self.vitals.get(_source_entity(shooter))
        if vitals is not None and not vitals.dead and self.now < vitals.nano_until:
            return ana_nano.OUTGOING
        return 1.0

    @staticmethod
    def _bastion_damage_scalar(owner) -> float:
        """Reconstructed Ironclad: 012F/st124's authored additive damage modifier.

        Apply before armour. Do not infer generic CombatModFilter semantics or
        enable unrelated hero filters from this one known graph/state pair.
        """
        if isinstance(owner, bots.Bot) or owner.hero.guid != BASTION or owner.body_script is None:
            return 1.0
        for instance in owner.body_script.component.instances.values():
            state = instance.states.get(124) if instance.graph.index == 0x012F else None
            if state is not None and state.active:
                modifier = _number(state.evaluate(state.node.field("m_50530EAB"), watch=False), 0.0)
                return max(0.0, 1.0 + modifier) if math.isfinite(modifier) else 1.0
        return 1.0

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
        self.hooks.retire(bot.entity)
        self.stuns.pop(bot.entity, None)
        self.sleeps.pop(bot.entity, None)
        self.heavy_drives.pop(bot.entity, None)
        self._clear_pins(bot.entity)
        self.frost.pop(bot.entity, None)
        self.healing.retire(bot.entity)
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
                death_now = self.now-(self.tick-tick)*TICK
                tick = max(dva.actor_frame(bot, death_now, tick), script.frame)
            event = script.die(tick, params, _source_entity(killer))
            if bot.dva_mover is not None:
                dva.queue_demech_return(self.match, bot)
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
            frame, lifetime = tick, int(BOT_RESPAWN/TICK)
            if isinstance(script, BotScript):
                frame = dva.actor_frame(bot, self.now, tick)
                lifetime = int(BOT_RESPAWN*1000/script.component.frame_ms)
            events = script.advance_death(frame, lifetime)
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
        self.hooks.retire(victim.body)
        self.orisa_halt.release(victim.body)
        self.shadow_steps.retire(victim.body)
        self.reaper_cast_anchors.pop(victim.body, None)
        self.stuns.pop(victim.body, None)
        self.sleeps.pop(victim.body, None)
        self.frost.pop(victim.body, None)
        sombra_hack.retire(self, victim.body)
        self.zarya_barriers.retire(victim.body)
        self._clear_pins(victim.body)
        self.healing.retire(victim.body)
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
