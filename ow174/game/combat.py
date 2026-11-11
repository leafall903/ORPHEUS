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
    defense_filters,
    dva,
    healing,
    matrix,
    mei_blizzard,
    mei_frost,
    npc_shields,
    observers,
    pools,
    projectiles,
    sigma,
    stats,
    voicelines,
    world,
    zenyatta,
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
EYE_STANDING = {0x0400000000000216: 1.55, 0x04000000000002A3: 1.425,
                0x0400000000000407: 1.74}  # manifest-verified104319 FirstPerson fields
EYE_CROUCHED = {0x04000000000003CF: 1.085, 0x0400000000000216: 1.15,
               0x04000000000002A3: .9975, 0x0400000000000407: 1.74}
EYE_CROUCHED_DEFAULT = 1.15


def eye_height(body, crouched=False):
    """Known FirstPerson stance fields; camera/muzzle and bone-hit transforms remain reconstructed."""
    return (EYE_CROUCHED.get(body, EYE_CROUCHED_DEFAULT) if crouched
            else EYE_STANDING.get(body, EYE))
HEAD_BELOW = 0.2  # m under the eye where the head starts (unverified)
HISTORY = 64  # ticks of body positions kept for the shots' view time (about 1 s; unverified: retail's limit)
COMBAT_CATCH_UP = 64  # at most about a second of retained combat frames per update
RANGE = 200.0  # m a hitscan shot reaches when its volley names none (unverified)
# Reconstructed admission for these authored hitscan bullet nodes.
HITSCAN_GUARD_VOLLEYS = frozenset({
    (0x0016, 12),  # Tracer
    (0x0019, 10),  # Reaper, each shotgun pellet
    (0x001B, 9),   # Widowmaker, unscoped
    (0x001B, 19),  # Widowmaker, scoped
    (0x012E, 30),  # Bastion, Recon
    (0x01C6, 33),  # Roadhog, Whole Hog pellets
    (0x01D2, 31),  # McCree, primary
    (0x01D2, 37),  # McCree, Fan
    (0x0254, 25),  # Soldier
    (0x0B1B, 10),  # Sombra
    (0x0B94, 88),  # Ana, scoped
    (0x14FC, 14),  # Ashe, scoped
    (0x14FC, 220), # Ashe, hip fire
    (0x1932, 10),  # Baptiste, each round of the primary burst
})
RESPAWN = 10.0  # s a hero waits in the PvP modes (0F4A and 0EC2 send 06FD.025 {10}: DATA)
PRACTICE_RESPAWN = 3.0  # s in the Practice Range (13C0 sends 06FD.025 {3}: DATA)
BOT_RESPAWN = 3.0  # s a training bot lies dead (unverified: OW2 made two killed bots again after 2.98 s)
RESPAWN_RETRY = 0.25  # s between attempts when all player body IDs are still reserved
MELEE = {0x0043: (4, 1.5, 1.0)}  # graph -> (its ModifyHealth state, metres ahead of the eye, radius): DATA
ARMOUR_BLOCK = 5.0  # damage armour stops per hit (unverified: OW1's rule; the client has no damage code)
TICK = 0.016
EPSILON = 1e-9
FLASHBANG_EFFECT = 0x04A0000000000079  # .053/0079 ->0228 ->224E
# Authored Ana 0BDD -> 0BDE/51 -> 224E profile 676B.
ANA_SLEEP_EFFECT = 0x04A000000000026F
ANA_SLEEP_DURATION = 5.0
ANA_SLEEP_DAMAGE_GRACE = 0.5
ANA_SLEEP_WAKE = 0.5
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


def projectile_box(target, origin, direction, reach, graph_index, *, tick):
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
        end = defense_filters.last_projectile_guard_world_tick(instance)
        if end is not None and tick > end:
            continue
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


def deflect_ray(target, origin, direction, reach, *, tick):
    found = projectile_box(target, origin, direction, reach, 0x03A3, tick=tick)
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


def ring_layout(state, volley):
    """Resource-authored radial projectile layout (613BDDB0), reconstructed phase.

    Ring counts/radii and spread come from the volley. Spread is interpreted as
    degrees and phase starts at local up; native angular phase/muzzle placement
    and multi-pellet client transport are not accepted by this reconstruction.
    """
    config = volley.get("m_5EC1EB38") or {}
    if config.get("$") != "STU_613BDDB0":
        return None
    count = expr.to_int(state.evaluate(volley.get("m_numProjectilesPerShot"), watch=False))
    spread = expr.to_float(state.evaluate(config.get("m_spread"), watch=False))
    if not 1 <= count <= 64 or not math.isfinite(spread) or not 0 <= spread < 90:
        raise ValueError("unsupported radial volley count/spread")
    rings = []
    for ring in config.get("m_0D30E40C") or ():
        radius = expr.to_float(state.evaluate(ring.get("m_6425FFB9"), watch=False))
        amount = expr.to_int(state.evaluate(ring.get("m_B19BD35D"), watch=False))
        if not math.isfinite(radius) or radius < 0 or not 1 <= amount <= 64:
            raise ValueError("unsupported radial volley ring")
        if not math.isfinite(spread*radius) or spread*radius >= 90:
            raise ValueError("unsupported radial volley angular extent")
        rings.append((amount, math.tan(math.radians(spread*radius))))
    if sum(amount for amount, _ in rings) != count:
        raise ValueError("radial volley ring count does not match authored projectile count")
    return tuple(rings)


def ring_directions(rings, direction):
    """Orient a resolved ring layout around the current flight direction."""
    horizontal = math.hypot(direction[0], direction[2])
    right = ((-direction[2]/horizontal, 0, direction[0]/horizontal)
             if horizontal > EPSILON else (-1, 0, 0))
    up = (right[1]*direction[2]-right[2]*direction[1],
          right[2]*direction[0]-right[0]*direction[2],
          right[0]*direction[1]-right[1]*direction[0])
    found = []
    for amount, tangent in rings:
        for index in range(amount):
            angle = index*2*math.pi/amount
            vector = tuple(direction[k] + tangent*(right[k]*math.sin(angle)+up[k]*math.cos(angle))
                           for k in range(3))
            length = math.sqrt(_dot(vector, vector))
            found.append(tuple(value/length for value in vector))
    return found


class WholeHogPush:
    """Per-volley push using 01C6/33 ModifyHealth and the reconstructed mover policy."""

    def __init__(self, state):
        settings = state.node.field("m_6396149F")["m_modifyHealth"]

        def value(config):
            return expr.to_float(state.evaluate(config, watch=False))

        self.horizontal = value(settings["m_E33EFEE1"])
        self.cap = value(settings["m_8394CF8C"])
        self.lift = value(settings["m_583C208B"])
        self.rings = tuple((value(ring["m_radius"]), value(ring["m_9102DA3F"]), value(ring["m_10399AE7"]))
                           for ring in settings["m_knockbackRings"])
        self.pushed = set()

    def apply(self, target, direction, distance):
        mover = getattr(target.owner, "mover", None)
        planar = math.hypot(direction[0], direction[2])
        if mover is None or target.entity in self.pushed or planar <= EPSILON:
            return
        for radius, horizontal_scale, lift_scale in self.rings:
            if distance > radius:
                continue
            dx, dz = direction[0] / planar, direction[2] / planar
            vx, vy, vz = mover.velocity
            forward = vx * dx + vz * dz
            change = max(0.0, min(self.horizontal * horizontal_scale, self.cap * horizontal_scale - forward))
            mover.velocity = (vx + change * dx, max(vy, self.lift * lift_scale), vz + change * dz)
            self.pushed.add(target.entity)
            return


def ray_world(
    collision: World | None, origin, direction, length: float, category: int = MOVER,
) -> float | None:
    """The distance to the first static hit, for callers that only need occlusion."""
    hit = collision.cast_ray(origin, direction, length, category) if collision is not None else None
    return hit.distance if hit is not None else None


# --- health -------------------------------------------------------------------------------------------


@dataclass
class ConditionalPool:
    pool_id: int
    most: float
    now: float


class Vitals:
    """A body's pools as component 51 has them: health, armour, shields (world.health's ids and fields)."""

    def __init__(self, entity: int, health: float, armour: float = 0.0, shields: float = 0.0) -> None:
        self.entity = entity
        self.most = {HEALTH_POOL: float(health), ARMOUR_POOL: float(armour), SHIELDS_POOL: float(shields)}
        self.now = dict(self.most)
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
        self.nano_until = 0.0
        self.died_at: float | None = None
        self.death_tick: int | None = None
        self.killer: int | None = None

    @property
    def dead(self) -> bool:
        return self.died_at is not None

    @property
    def total(self) -> float:
        extra = sum(p.now for p in getattr(self, "conditional_pools", {}).values())
        return sum(self.now.values()) + extra + self.bonus_shields

    @property
    def maximum(self) -> float:
        return sum(self.most.values()) + sum(p.most for p in getattr(self, "conditional_pools", {}).values())

    def pool_total(self, kind):
        extra = getattr(self, "conditional_pools", {}).get(kind)
        return self.now[kind] + (extra.now if extra is not None else 0)

    def pool_maximum(self, kind):
        extra = getattr(self, "conditional_pools", {}).get(kind)
        return self.most[kind] + (extra.most if extra is not None else 0)

    def set_conditional_pool(self, kind, amount):
        if not math.isfinite(amount) or amount < 0:
            raise ValueError("conditional pool amount must be finite and nonnegative")
        pools = getattr(self, "conditional_pools", None)
        pool = pools.get(kind) if pools is not None else None
        if pool is None:
            if amount == 0:
                return
            if pools is None:
                pools = self.conditional_pools = {}
            # Base pools use IDs 1–3 and Sigma's temporary pool uses 4.
            pool = pools[kind] = ConditionalPool(5 + len(pools), 0, 0)
        pool.most = pool.now = amount
        self.changed.add(kind)

    def _damage_losses(self, amount):
        left = max(0.0, amount)
        take = min(left, self.bonus_shields)
        if take:
            left -= take
            yield None, take
        for kind in (SHIELDS_POOL, ARMOUR_POOL, HEALTH_POOL):
            if left <= 0:
                break
            if kind == ARMOUR_POOL and self.now[kind] > 0:
                left = max(left-ARMOUR_BLOCK, left/2)
            take = min(left, self.pool_total(kind))
            if take > 0:
                left -= take
                yield kind, take

    def damage(self, amount: float) -> float:
        """Apply the existing shield, armour and health losses."""
        taken = 0.0
        for kind, take in self._damage_losses(amount):
            if kind is None:
                self.bonus_shields -= take
                kind = SHIELDS_POOL
            else:
                pool = getattr(self, "conditional_pools", {}).get(kind)
                if pool is not None:
                    extra = min(pool.now, take)
                    pool.now -= extra
                else:
                    extra = 0
                self.now[kind] -= take - extra
            self.changed.add(kind)
            taken += take
        return taken

    def heal(self, amount: float) -> float:
        """Heal health, then armour, then shields (unverified: the order). Returns what it gave."""
        left = max(0.0, amount)
        for kind in (HEALTH_POOL, ARMOUR_POOL, SHIELDS_POOL):
            left -= self.restore_pool(kind, left)
        return max(0.0, amount) - left

    def restore_pool(self, kind, amount):
        amount = max(0.0, amount)
        given = min(amount, max(0.0, self.most[kind] - self.now[kind]))
        if given:
            self.now[kind] += given
        pool = getattr(self, "conditional_pools", {}).get(kind)
        if pool is not None:
            extra = min(amount - given, max(0.0, pool.most - pool.now))
            pool.now += extra
            given += extra
        if given:
            self.changed.add(kind)
        return given

    def component(self, kinds) -> dict:
        """Component 51 with these pools' fields set (the others are left as they are)."""
        parts: list = [None] * (3 * POOLS)
        for kind in kinds:
            if kind in self.ids:
                parts[kind * POOLS] = world.pool(self.ids[kind], kind, self.most[kind], self.now[kind])
            pool = getattr(self, "conditional_pools", {}).get(kind)
            if pool is not None:
                parts[kind * POOLS + 1] = world.pool(pool.pool_id, kind, pool.most, pool.now)
        # A separate, unhealable extra-shield pool. Pool slot/id and its HUD
        # byte17=0 over-health classification are explicit reconstruction policy.
        if self.bonus_shields_max:
            value = world.pool(4, SHIELDS_POOL, self.bonus_shields_max, self.bonus_shields)
            slot = 2 if SHIELDS_POOL in getattr(self, "conditional_pools", {}) else 1
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
        # 0164's two Orb volleys omit this config. Reconstructed OW1 behavior:
        # both can headshot for 2x. Keep this fallback local to that weapon;
        # the engine default for other omitted configs is still unverified.
        # Scoped reconstruction of missing human gun defaults; explicit Fan1
        # and every other authored factor keep their original value.
        critical=value("m_A872C70E", 2.0 if (
            state.instance.graph.index == 0x0164
            or (state.instance.component.world.hero == 0x02E0000000000042
                and state.instance.graph.index == 0x01D2 and state.node.pos == 31)) else 1.0),
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


@dataclass(frozen=True)
class SplitVolley:
    rings: tuple[tuple[int, float], ...]
    definition: int
    speed: float
    lifetime: float
    direct: HitDef


@dataclass
class ChainHook:
    owner: object
    activation: tuple[int, int]
    origin: tuple
    direction: tuple
    remaining: float
    speed: float
    radius: float
    damage: float
    loadout: int
    pull_speed: float
    victim: int | None = None


def scrap_ball_split(state, nano: float) -> SplitVolley | None:
    """Resolve 01C6/29's nested payload while its firing state is still live."""
    if state.instance.graph.index != 0x01C6 or state.node.pos != 29:
        return None
    config = state.node.field("m_747ECB51") or {}
    volley = config.get("m_volley") or {}
    if config.get("$") != "STU_DB5FDC79" or volley.get("$") != "STUStatescriptStateWeaponVolley":
        return None
    rings = ring_layout(state, volley)
    entity = (volley.get("m_projectileEntity") or {}).get("m_entityDef")
    definition = state.evaluate(entity, watch=False)
    speed = _number(state.evaluate(volley.get("m_projectileSpeed"), watch=False), 0)
    lifetime = _number(state.evaluate(volley.get("m_projectileLifetime"), watch=False), 0)
    reach = _number(state.evaluate(volley.get("m_FEC435C6"), watch=False), 0)
    direct = hit_def(state, volley.get("m_6396149F"))
    if not (rings and isinstance(definition, expr.Asset)
            and math.isfinite(speed) and speed > 0 and math.isfinite(lifetime) and lifetime > 0
            and direct is not None):
        return None
    if reach > 0:
        lifetime = min(lifetime, reach / speed)
    if nano != 1.0:
        direct = replace(direct, amount=expr.f32(direct.amount*nano))
    return SplitVolley(rings, definition.guid, speed, lifetime, direct)


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
    definition: int | None = None  # actual WeaponVolley entity definition, including unconfirmed flights
    gravity: float = 0.0
    velocity: tuple | None = None
    detonate_on_expiry: bool = False
    target_limit: int = 1
    hit_entities: tuple[int, ...] = ()
    split: SplitVolley | None = None


class Combat:
    """The match's health, hits, deaths and respawns."""

    def __init__(self, match) -> None:
        self.match = match
        self.vitals: dict[int, Vitals] = {}  # body or bot entity -> its health
        self.history: dict[int, deque] = {}  # entity -> (tick, feet, height, eye) of the last HISTORY ticks
        self.replacement_ticks: dict[int, int] = {}  # replacement body -> authoritative world tick
        self.rockets: list[Rocket] = []
        self.chain_hooks: dict[int, ChainHook] = {}
        self.respawns: list[tuple[float, object]] = []  # (when, a player or a bot)
        self.next_bot = bots.BOT_ENTITY + len(match.bots)
        self.now = 0.0
        self.tick = 0
        self.melee_seen: dict[int, dict[tuple[int, int], int]] = {}  # body -> counters by instance/state
        self.stuns: dict[int, int] = {}  # body identity -> exclusive world-tick deadline
        self.control_history: dict[int, list[tuple[int, int]]] = {}  # bounded timed control intervals
        self.frost = mei_frost.Frost(self)
        self.blizzard = mei_blizzard.Blizzard(self)
        self.sleeps: dict[int, tuple[int, int, bool]] = {}  # start, exclusive end, waking
        self.pins: dict[int, int] = {}  # manual Assassin source body -> controlled victim body
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
        self.vitals.pop(player.body, None)
        self.history.pop(player.body, None)
        self.replacement_ticks.pop(player.body, None)
        self.melee_seen.pop(player.body, None)
        self.chain_hooks.pop(player.body, None)
        self.zenyatta.retire(player.body)
        self.stuns.pop(player.body, None)
        self.sleeps.pop(player.body, None)
        self.control_history.pop(player.body, None)
        self.frost.retire(player.body, player)
        self.blizzard.retire(player.body)
        self.sigma.retire(player.body)
        self.barriers.retire(player.body)
        self.healing.retire(player.body)
        self.clear_pins(player.body)
        # The owner's leased projectiles and their observer copies retire with the old body.
        self.rockets[:] = [
            rocket for rocket in self.rockets if rocket.shooter is not player or (
                rocket.shot is None and not (rocket.direct and rocket.direct.effect == ANA_SLEEP_EFFECT)
            )
        ]
        self.respawns[:] = [(when, who) for when, who in self.respawns if who is not player]

    def record_replacement(self, body: int) -> None:
        """Keep the creation boundary independent of the rolling pose history."""
        self.replacement_ticks[body] = self.match.tick
        self.stuns.pop(body, None)
        self.sleeps.pop(body, None)
        self.control_history.pop(body, None)
        self.frost.retire(body)
        self.clear_pins(body)

    def is_stunned(self, actor, *, tick: int | None = None) -> bool:
        entity = _source_entity(actor)
        if entity in self.pins.values() or self.control_deadline(entity, tick=tick) > (self.tick if tick is None else tick):
            return True
        # Player input can replay missed poses after world combat has retired a deadline.
        return tick is not None and tick < self.tick and any(
            start <= tick < end for start, end in self.control_history.get(entity, ())
        )

    def _stun(self, entity: int, deadline: int) -> None:
        self.stuns[entity] = max(deadline, self.stuns.get(entity, 0))
        self.control_history.setdefault(entity, []).append((self.tick, deadline))

    def control_deadline(self, entity, *, tick=None):
        sleep = self.sleeps.get(entity)
        frost = self.frost.samples.get(entity) if tick is None else self.frost.sample_for_body(entity, tick)
        freeze = math.ceil(frost[2] / 16) if frost is not None else 0
        return max(self.stuns.get(entity, 0), sleep[1] if sleep is not None else 0, freeze)

    def clear_pins(self, entity: int) -> None:
        for source, victim in list(self.pins.items()):
            if entity in (source, victim):
                del self.pins[source]

    def _hit_effect(self, target: Target, effect: int | None) -> None:
        """Resource-backed stun reconstruction; native immunity/presentation remain unverified."""
        # The reconstructed Transcendence service rejects this control as well
        # as its damage; this is not a generic native immunity/tag implementation.
        if (effect not in (FLASHBANG_EFFECT, ANA_SLEEP_EFFECT) or self.vitals_of(target).dead
                or self.barriers.bubble(target) is not None
                or self.zenyatta.ultimate(target.owner)):
            return
        if effect == ANA_SLEEP_EFFECT:
            self.sleeps[target.entity] = (self.tick, self.tick + math.ceil(ANA_SLEEP_DURATION / TICK), False)
            self._interrupt(target)
            return
        variables = graph.mode_variables(self.match.game_map.mode_guid) or {}
        duration = 1.7 if expr.truthy(variables.get(FLASHBANG_MODE_FLAG)) else 0.8
        deadline = self.tick + math.ceil(duration / TICK)
        self._stun(target.entity, deadline)
        self._interrupt(target)

    def _interrupt(self, target: Target) -> None:
        self.zenyatta.interrupt(target.owner)
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

    def command(self, player, command: Command | None) -> None:
        """Consume the body's reports. Only a frame with a pose can affect targets."""
        if self.match.collision_world is None:
            return
        script = player.body_script
        if script is None:
            return
        shots, script.component.shots[:] = list(script.component.shots), []
        if self.is_dead(player) or self.is_stunned(player):
            return
        self.zenyatta.command(player, command)
        if command is not None:
            self._cast_chain_hook(player, command, script)
            for state, number, time, activation in shots:
                try:
                    self._shoot(player, command, state, number, time, activation=activation)
                except Exception:
                    log.exception("[game] %s: %s's shot failed", self.match.label(), player.name)
        try:
            self._melee(player, command, script)
        except Exception:
            log.exception("[game] %s: %s's melee failed", self.match.label(), player.name)

    def _cast_chain_hook(self, player, command, script) -> None:
        if player.hero.guid != 0x02E0000000000040:
            return
        instance = next((i for i in script.component.instances.values() if i.graph.index == 0x01C8), None)
        if instance is None:
            return
        state = next((s for s in instance.states.values() if s.node.pos == 12 and s.active), None)
        if state is None:
            return
        activation = state.counter, state.start
        previous = self.chain_hooks.get(player.body)
        if previous is not None and previous.activation == activation:
            return

        def value(node, field):
            return expr.to_float(state.evaluate(instance.graph.node(node).field(field), watch=False))

        # 01C8/12 enters after the authored cast delay. The TrackRay/MoveTo
        # geometry is hosted here until the native query and child transport run.
        settings = instance.graph.node(19).field("m_modifyHealth")
        damage = expr.to_float(state.evaluate(settings["m_amount"], watch=False))
        loadout = state.evaluate(settings["m_loadout"], watch=False)
        pull = graph.graph(0x01C9).node(0).field("m_speed")
        speed = pull["m_expression"]["m_D99EF254"][0]
        self.chain_hooks[player.body] = ChainHook(
            player, activation, self._eye(player), aim(*command.aim()), value(247, "m_value"),
            value(248, "m_value"), value(17, "m_radius"), damage, loadout.guid, speed,
        )

    def _advance_chain_hooks(self) -> None:
        from ow174.game import npc_control

        for hook in self.chain_hooks.values():
            if hook.remaining <= 0 and hook.victim is None:
                continue
            owner = hook.owner
            if self.is_dead(owner) or self.is_stunned(owner):
                hook.remaining, hook.victim = 0, None
                continue
            enemies = self._enemies(owner)
            if hook.victim is not None:
                target = next((target for target in enemies if target.entity == hook.victim), None)
                if (target is None or self.barriers.bubble(target) is not None
                        or not self.area_visible(owner, self._eye(owner), target)):
                    hook.victim = None
                    continue
                x, _, z = owner.mover.position
                tx, ty, tz = target.feet
                dx, dz = tx-x, tz-z
                distance = math.hypot(dx, dz)
                if distance <= 3:
                    hook.victim = None
                    continue
                destination = (x + dx * 3 / distance, ty, z + dz * 3 / distance)
                actor = target.owner
                manual_npc = isinstance(actor, bots.Bot)
                if manual_npc:
                    collision = self._collision()
                    mover = actor.displacement_mover(collision, self.tick)
                    mover.use(world=collision)
                else:
                    mover = actor.mover
                moved = mover._move_to(TICK, destination, hook.pull_speed)
                # This world-driven movement already spent the tick.
                mover.velocity = (0.0, 0.0, 0.0)
                if manual_npc:
                    actor.position, actor.velocity = tuple(mover.position), mover.velocity
                    actor.held, actor.pace = (0, 0), 0.0
                    actor.moved_tick = self.tick
                self._stun(target.entity, self.tick + 1)
                if moved is not None:
                    hook.victim = None
                continue

            reach = min(hook.remaining, hook.speed * TICK)
            wall = ray_world(self._collision(), hook.origin, hook.direction, reach)
            shield = self.barriers.ray(owner, hook.origin, hook.direction, reach)
            blocked = min(wall if wall is not None else math.inf, shield[0] if shield else math.inf)
            nearest, victim = blocked, None
            for target in enemies:
                a, b = target.segment()
                distance = ray_capsule(hook.origin, hook.direction, a, b, target.radius + hook.radius)
                if distance is not None and distance <= reach and distance < nearest:
                    nearest, victim = distance, target
            if victim is None and blocked == math.inf:
                hook.origin = _along(hook.origin, hook.direction, reach)
                hook.remaining -= reach
                continue
            hook.remaining = 0
            if victim is None or self.barriers.bubble(victim) is not None:
                continue
            self._damage(owner, victim, hook.damage, False, hook.loadout, self.tick, world_tick=self.tick)
            actor = victim.owner
            pullable = (npc_control.manual(actor) and actor.dva_mover is None
                        if isinstance(actor, bots.Bot) else getattr(actor, "mover", None) is not None)
            if self.vitals_of(victim).dead or not pullable:
                continue
            if not self.zenyatta.ultimate(victim.owner):
                self._interrupt(victim)
            hook.victim = victim.entity
            self._stun(victim.entity, self.tick + 1)

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
            for target in self._enemies(player):
                shown = self.seen(target, view_tick)
                if shown is None:
                    continue
                a, b = shown.segment()
                if (segment_distance(centre, a, b) - shown.radius <= radius and amount > 0.0
                        and capsule_visible(self._collision(), self._eye(player), a, b, shown.radius)
                        and not defense_filters.blocks_melee(player, target, state)):
                    self._damage(player, target, amount, False, loadout, command.frame,
                                 melee=True, boost_credit=credit)

    def update(self, now: float, tick: int, frames: bool = True) -> None:
        """Each tick: keep where the bodies are, fly the rockets, respawn, send health that changed. The
        other players' body frames only on the ticks that send (`frames`)."""
        if self.match.collision_world is None:
            return
        retained = max(self.tick + 1, tick - COMBAT_CATCH_UP + 1)
        first = tick
        replay_zen_support = False
        competing_heals = False
        if self.tick and tick > self.tick + 1:
            npc_firing = any(
                bot.firing and not bot.dead and isinstance(bot.body_script, BotScript) for bot in self.match.bots
            )
            # A gap across Q expiry needs healing before post-expiry damage.
            # Competing heals must share the remaining pools in per-tick order.
            competing_heals = self.zenyatta.competing_heals()
            replay_zen_support = competing_heals or any(
                self.tick <= end < tick
                for owner in self.zenyatta.owners.values()
                if (end := self.zenyatta.ultimate_last_world_tick(owner.player)) is not None
            )
            # Admitted flight must sample timed protection and Discord at impact.
            timed_flight = any(rocket.last_tick is not None for rocket in self.rockets) and (
                self.barriers.domes or any(rocket.split is not None for rocket in self.rockets)
                or self.barriers.bubble_deadline_in_gap(self.now, now)
                or self.zenyatta.discord_deadline_in_gap(self.now, now)
                or defense_filters.projectile_guard_deadline_in_gap(self.match.players, self.tick, tick)
                or any(matrix.active_matrix_depleted_before(player, tick) for player in self.match.players)
                or any(matrix.active_matrix_depleted_before(bot, dva.actor_frame(bot, now, tick))
                       for bot in self.match.bots if bot.dva_mover is not None)
            )
            if (npc_firing or replay_zen_support or timed_flight
                    or any(hook.remaining > 0 or hook.victim is not None for hook in self.chain_hooks.values())
                    or any(npc_shields.recovery_pending(bot) for bot in self.match.bots)):
                first = retained
        for target in self._targets():
            history = self.history.setdefault(target.entity, deque(maxlen=HISTORY))
            history.append((tick, target.feet, target.height, target.eye))
        # Replay the retained combat frames using the most recent poses. A shot
        # without a completed world launch frame joins only at the endpoint.
        if replay_zen_support and first > self.tick + 1:
            # Older poses are unavailable. Integrate support without replaying old attacks.
            while (competing_heals and self.tick + 1 < first
                   and self.zenyatta.competing_heals(wounded=True)):
                at = self.tick + 1
                self.now, self.tick = now - (tick - at) * TICK, at
                self.zenyatta.update()
            self.now, self.tick = now - (tick - first + 1) * TICK, first - 1
            self.zenyatta.update()
        for at in range(first, tick + 1):
            self.now, self.tick = now - (tick - at) * TICK, at
            # Expire timed domes before this update's attacks and support sample them.
            self.barriers.advance_domes()
            self._advance_chain_hooks()
            for rocket in list(self.rockets):
                if at == tick or rocket.last_tick is not None:
                    self._fly(rocket)
            self._bot_weapons(at)
            self.blizzard.update(at)
            if replay_zen_support and at < tick:
                self.zenyatta.update()
        self.barriers.update()
        self.projectiles.update(tick, first_tick=retained)
        self.sigma.update()
        self.zenyatta.update()
        self.frost.update(tick)
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
        for entity in [key for key, sleep in self.sleeps.items() if sleep[1] <= tick]:
            start, end, _ = self.sleeps.pop(entity)
            self.control_history.setdefault(entity, []).append((start, end))
        for entity, intervals in list(self.control_history.items()):
            recent = [(start, end) for start, end in intervals if end > tick - COMBAT_CATCH_UP]
            if recent:
                self.control_history[entity] = recent
            else:
                del self.control_history[entity]
        for table in (self.history, self.vitals, self.replacement_ticks, self.stuns, self.sleeps,
                      self.control_history):
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
            found.append(
                Target(player.body, player.team, tuple(player.mover.position), height, data.stand_radius, eye,
                       player)
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
        return Target(target.entity, target.team, feet, height, target.radius, eye, target.owner)

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
                self.clear_pins(bot.entity)
                bot.body_script = None
                log.exception("[game] %s: %s's weapon script failed", self.match.label(), bot.name)

    def advance_bot_weapon(self, bot, script, frame, allowed, edges=()):
        snapshot = script.advance_live(frame, bot.position, edges=edges)
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

    def _nano_outgoing(self, actor):
        source = self.vitals.get(_source_entity(actor))
        return 1.5 if source is not None and not source.dead and self.now < source.nano_until else 1.0

    def _eye(self, player) -> tuple:
        x, y, z = player.mover.position
        crouched = player.mover.crouched
        height = eye_height(player.hero.body, crouched)
        return (x, y + height, z)

    def _shoot(
        self, shooter, command, state, number: int, time: int,
        *, activation: tuple[int, int], snapshot: Snapshot | None = None, _direction=None, _push=None,
    ) -> None:
        if state.instance.graph.index == 0x0B9E and state.index == 1:
            if shooter.hero.guid == 0x02E000000000013B:
                target = state.instance.find_var(expr.INSTANCE, 8070, create=False)
                duration = state.instance.find_var(expr.INSTANCE, 215, create=False)
                self.apply_nano(_source_entity(shooter), target.value() if target is not None else (),
                                _number(duration.value(), 0) if duration is not None else 0)
            return
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
        if _direction is None:
            rings = ring_layout(state, node.fields)
            if rings is not None:
                push = WholeHogPush(state) if (state.instance.graph.index, node.pos) == (0x01C6, 33) else None
                for pellet in ring_directions(rings, direction):
                    self._shoot(shooter, command, state, number, time, activation=activation,
                                snapshot=snapshot, _direction=pellet, _push=push)
                return
        else:
            direction = _direction
        direct = hit_def(state, node.field("m_6396149F"))
        nano = self._nano_outgoing(shooter)
        if direct is not None and direct.amount > 0 and nano != 1.0:
            direct = replace(direct, amount=expr.f32(direct.amount*nano))
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
            blast = hit_def(state, node.field("m_D2D11CE9"))
            if blast is not None and blast.amount > 0 and nano != 1.0:
                blast = replace(blast, amount=expr.f32(blast.amount*nano))
            entity_def = (node.field("m_projectileEntity") or {}).get("m_entityDef")
            definition = state.evaluate(entity_def, watch=False) if entity_def is not None else None
            shot = None
            if isinstance(definition, expr.Asset):
                shot = self.projectiles.fired(
                    shooter, state, shot_frame, definition.guid, lifetime, origin, direction,
                    activation=activation, snapshot=snapshot,
                )
            if self.blizzard.launch(shooter, state, origin, direction,
                                     definition.guid if isinstance(definition, expr.Asset) else None,
                                     speed, lifetime, shot):
                return
            damaging = (direct is not None and direct.amount > 0.0) or (
                blast is not None and blast.amount > 0.0
            )
            if damaging or shot is not None:
                self.rockets.append(
                    Rocket(
                        shooter, shooter.team, origin, direction, speed, lifetime, direct, blast, shot,
                        last_tick=self.tick if npc or command.frame == self.tick else None,
                        definition=definition.guid if isinstance(definition, expr.Asset) else None,
                        gravity=gravity if gravity is not None else 0.0,
                        velocity=(tuple(expr.f32(value*speed) for value in direction)
                                  if gravity is not None else None),
                        detonate_on_expiry=gravity is not None,
                        target_limit=mei_frost.projectile_target_limit(state),
                        split=scrap_ball_split(state, nano),
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
        deflections = 8 if (state.instance.graph.index, node.pos) in HITSCAN_GUARD_VOLLEYS else 0
        self._hitscan(shooter, origin, direction, reach, direct, shot_frame, view_tick, deflections, push=_push)

    def beam_target(self, player, command, reach, radius):
        origin, direction = self._eye(player), aim(*command.aim())
        wall = ray_world(self._collision(), origin, direction, reach)
        shield = self.barriers.ray(player, origin, direction, reach)
        limit = reach if wall is None else wall
        if shield is not None and (wall is None or shield[0] < wall):
            limit = shield[0]
        else:
            shield = None
        view_tick = command.frame - command.view_delay(player.quanta) * 1e6 / player.quanta
        target = None
        for candidate in self._enemies(player):
            shown = self.seen(candidate, view_tick)
            if shown is None:
                continue
            a, b = shown.segment()
            hit = ray_capsule(origin, direction, a, b, shown.radius + radius)
            if hit is not None and hit < limit:
                target, limit = candidate, hit
        return target, shield[1] if target is None and shield is not None else None

    def _hitscan(
        self, shooter, origin, direction, reach, direct, shot_frame, view_tick, deflections, travelled=0.0, *, push=None,
    ):
        best = None
        guard = None
        for target in self._enemies(shooter):
            shown = self.seen(target, view_tick)
            if shown is None:
                continue
            a, b = shown.segment()
            t = ray_capsule(origin, direction, a, b, shown.radius)
            if t is not None and t <= reach and (best is None or t < best[0]):
                best = (t, target, shown)
            if deflections:
                for graph, kind in ((0x03A3, "reflect"), (0x1D02, "absorb")):
                    caught = projectile_box(shown, origin, direction, reach, graph, tick=self.tick)
                    if caught is not None and (guard is None or caught[0] < guard[0]):
                        guard = (caught[0], kind, target.owner, caught[1])

        if deflections:
            distance = self._matrix_intersection(shooter, origin, direction, reach)
            if distance is not None and (guard is None or distance < guard[0]):
                guard = (distance, "matrix", None, None)

        impact = best[0] if best is not None else math.inf
        if guard is not None and guard[0] < impact:
            impact = guard[0]
        else:
            guard = None
        barrier = self.barriers.ray(shooter, origin, direction, reach)
        if barrier is not None and barrier[0] < impact:
            distance, shield = barrier
            wall = ray_world(self._collision(), origin, direction, distance)
            if wall is None or wall >= distance:
                self.barriers.damage(shield, direct.amount*direct.scale(travelled+distance), _source_entity(shooter))
            return
        if best is None and guard is None:
            return
        wall = ray_world(self._collision(), origin, direction, impact)
        if wall is not None and wall < impact:
            return
        if guard is not None:
            _, kind, owner, state = guard
            if kind == "absorb":
                amount = direct.amount * direct.scale(travelled+impact)
                self.sigma.record_absorption(state, shooter, amount)
            elif kind == "reflect" and deflections > 1 and reach > impact:
                direction_out = aim(owner.mover.yaw, owner.mover.pitch)
                self._hitscan(owner, _along(origin, direction, impact), direction_out, reach-impact,
                              direct, shot_frame, self.tick, deflections-1, travelled+impact, push=push)
            return
        t, target, shown = best
        hit_y = origin[1] + direction[1] * t
        critical = hit_y >= shown.feet[1] + shown.eye - HEAD_BELOW
        amount = direct.amount * direct.scale(travelled+t) * (direct.critical if critical else 1.0)
        push_body = push is not None and self.barriers.bubble(target) is None
        self._damage(
            shooter, target, amount, critical and direct.critical > 1.0, direct.loadout, shot_frame,
            world_tick=self.tick if isinstance(shooter, bots.Bot) else None,
        )
        if push_body and not self.vitals_of(target).dead:
            push.apply(target, direction, travelled+t)
        self._hit_effect(target, direct.effect)

    def _fly(self, rocket: Rocket) -> None:
        """Sweep elapsed world time against the current bodies and static collision."""
        # A shot without a world anchor gets one initial flight update.
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
            if rocket.split is None:
                self._landed(rocket)
            else:
                self._expire_projectile(rocket, elapsed * TICK)
            return
        after_expiry = elapsed * TICK - duration if rocket.split is not None else 0.0
        self._fly_for(rocket, duration, after_expiry=after_expiry)

    def _matrix_hit(self, shooter, origin, direction, reach, definition, *, tick=None):
        """Apply the finite projectile's tag admission before checking Matrix geometry."""
        if not matrix.absorbable(definition):
            return None
        return self._matrix_intersection(shooter, origin, direction, reach, tick=tick)

    def _matrix_intersection(self, shooter, origin, direction, reach, *, tick=None):
        """Nearest active 0407 Matrix box using reconstructed current poses."""
        tick = self.tick if tick is None else tick
        now = self.now - (self.tick-tick)*TICK
        best = None
        for target in self._enemies(shooter):
            actor = target.owner
            if isinstance(actor, bots.Bot):
                if actor.dva_mover is None:
                    continue
                x, y, z = target.feet
                eye = (x, y + target.eye, z)
                forward = aim(round(actor.yaw), round(actor.pitch))
                frame = dva.actor_frame(actor, now, tick)
            else:
                eye = self._eye(actor)
                forward = aim(actor.mover.yaw, actor.mover.pitch)
                frame = tick
            distance = matrix.matrix_distance(actor, origin, direction, reach, eye, forward, frame=frame)
            if distance is not None and (best is None or distance < best):
                best = distance
        return best

    def _fly_for(self, rocket: Rocket, duration: float, redirects: int = 0, *, after_expiry=0.0) -> None:
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
                reflected = deflect_ray(target, rocket.position, rocket.direction, step, tick=self.tick)
            if reflected is not None and (best is None or reflected < best[0]):
                best = (reflected, target, "reflect", None)
            absorbed = (projectile_box(target, rocket.position, rocket.direction, step, 0x1D02, tick=self.tick)
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
                self._expire_projectile(rocket, after_expiry)
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
            if rocket.split is not None and rocket.left <= 0.0:
                self._expire_projectile(rocket, after_expiry)
            elif redirects >= 8:
                # Overlapping boxes must not create an immortal zero-distance
                # reflection cycle. Consume remaining time under the bounded
                # reconstruction policy, without fabricating travel distance.
                rocket.left -= remaining
                if rocket.left <= 0:
                    self._expire_projectile(rocket, after_expiry)
            elif remaining > EPSILON:
                self._fly_for(rocket, remaining, redirects + 1, after_expiry=after_expiry)
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

    def _expire_projectile(self, rocket: Rocket, remaining=0.0) -> None:
        self._landed(rocket)
        if rocket.detonate_on_expiry:
            self._explode(rocket, rocket.position)
        split = rocket.split
        if split is not None:
            # Spawn only on free expiry. Contacts and absorption already retired the ball.
            for direction in ring_directions(split.rings, rocket.direction):
                pellet = Rocket(rocket.shooter, rocket.team, rocket.position, direction, split.speed,
                                split.lifetime, split.direct, None, last_tick=self.tick,
                                definition=split.definition)
                self.rockets.append(pellet)
                if remaining > EPSILON:
                    self._fly_for(pellet, min(remaining, pellet.left))

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
            given = vitals.heal(amount*self.healing.factor(target, seconds, end_tick=end_tick))
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
        *, world_tick: int | None = None, melee: bool = False, boost_credit=None,
    ) -> None:
        vitals = self.vitals_of(target)
        if vitals.dead or amount <= 0.0:
            return
        if self.zenyatta.ultimate(target.owner):
            return
        if (bubble := self.barriers.bubble(target)) is not None:
            self.barriers.damage(bubble, amount, _source_entity(shooter))
            return
        amount *= self.zenyatta.boost(target)
        if self.now < vitals.nano_until:
            amount *= .5
        self.zenyatta.regenerate_before_damage(target, vitals)
        base = sum(loss for _, loss in vitals._damage_losses(amount/boost_credit[1])) if boost_credit else 0.0
        dealt = vitals.damage(amount)
        extra = max(0.0, dealt-base) if boost_credit else 0.0
        if extra:
            from ow174.game import mercy_boost

            source, scale = boost_credit
            mercy_boost.award(source, target.entity, dealt, extra, scale-1)
        if dealt <= 0.0:
            return
        self.zenyatta.damaged(target)
        sleep = self.sleeps.get(target.entity)
        if sleep is not None:
            start, end, waking = sleep
            if not waking and self.tick < end and (self.tick - start) * TICK > ANA_SLEEP_DAMAGE_GRACE:
                wake = self.tick + math.ceil(ANA_SLEEP_WAKE / TICK)
                self.sleeps[target.entity] = (start, min(end, wake), True)
        killed = vitals.pool_total(HEALTH_POOL) <= EPSILON
        if killed:
            vitals.now[HEALTH_POOL] = 0.0
            vitals.died_at = self.now
            vitals.death_tick = self.match.tick if world_tick is None else world_tick
            vitals.killer = _source_entity(shooter)
        if not isinstance(shooter, bots.Bot):
            flags = (CRITICAL if critical else 0) | (KILLING if killed else 0)
            self._tell_hit(shooter, target, dealt, flags, frame)
            shooter.stats.add(stats.HERO_DAMAGE, dealt)
            self.zenyatta.charge(shooter, dealt-extra)
        victim = target.owner
        if (not killed and loadout == mei_frost.SPRAY_LOADOUT
                and getattr(getattr(shooter, "hero", None), "guid", None) == mei_frost.HERO):
            self.frost.apply(shooter, target, tick=self.tick)
        if melee and not killed and isinstance(victim, bots.Bot) and victim.entity in self.pins:
            from ow174.game.assassin import release_from_melee

            release_from_melee(self, victim)
        if killed and isinstance(victim, bots.Bot):
            # The NPC death script owns its terminal health change and death event ordering.
            self._bot_died(shooter, victim, critical, loadout, vitals.death_tick)
            return
        self._sync_body_health(victim, vitals)
        if killed:
            self._died(shooter, victim, critical, loadout)
        elif isinstance(victim, bots.Bot):
            npc_shields.received_damage(self, victim, vitals, _source_entity(shooter))
        elif getattr(getattr(victim, "hero", None), "guid", None) == 0x02E000000000012E:
            from ow174.game import sombra_hack

            sombra_hack.received_damage(victim, _source_entity(shooter))

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
        from ow174.game import heavy_assault, sniper_reposition

        sniper_reposition.stop(bot)
        if bot.hero == heavy_assault.HERO and bot.special_attack and bot.body_script is not None:
            heavy_assault.stop(bot.body_script.component)
        self.clear_pins(bot.entity)
        self.stuns.pop(bot.entity, None)
        self.sleeps.pop(bot.entity, None)
        self.control_history.pop(bot.entity, None)
        self.frost.retire(bot.entity, bot)
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
        self.clear_pins(victim.body)
        self.stuns.pop(victim.body, None)
        self.sleeps.pop(victim.body, None)
        self.control_history.pop(victim.body, None)
        self.frost.retire(victim.body, victim)
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
