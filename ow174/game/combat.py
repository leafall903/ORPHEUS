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
  scaled between the explosion rings (1 m: 1.0, 3 m: 0.5). Shields, then armour, then health.
  Ordinary hits retain the existing5-less/at-most-half armour policy. Zarya beam uses the documented
  OW1 scalar0.7 (2020-08-31 patch); Graviton field DOT uses1.0 (2019-03-19 patch). Native pool-boundary
  timing is still reconstructed; no target-server damage routine has been executed.
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
    accretion,
    assassin,
    bots,
    contacts,
    defenses,
    dva,
    earthshatter,
    graviton,
    heavy_charge,
    hypersphere,
    interception,
    mccree,
    observers,
    orisa_barrier,
    orisa_halt,
    phase,
    pools,
    projectiles,
    reinhardt_barrier,
    reinhardt_charge,
    sigma_barrier,
    sigma_flux,
    stats,
    voicelines,
    winston_barrier,
    winston_jump,
    winston_primal,
    world,
    zarya,
)
from ow174.game.bits import BitWriter
from ow174.game.collision import MOVER, TEAM0, TEAM1, World
from ow174.game.commands import Command
from ow174.game.mover import FELL, mover_data
from ow174.game.script import entity_queries, expr, graph, nodes, runtime
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
DEAD = 0x2000  # movement-state flag: dead (the 3P anim's dead pose)
# 003E.025's parameters, by the low 16 bits of their 026 identifiers (01CF rid4).
KILLER, CRIT, LOADOUT, ASSISTS = 0x0012, 0x0095, 0x03E6, 0x03E7
MODE_UI = 0x0CB6  # the mode's client UI graph: the kill feed 0DEA and the notices 1FC0 (client-only)

EYE = 1.55  # m: STUFirstPersonComponent m_E29993A5, its default (OWLib) and Soldier's (DATA; unverified: eye)
EYE_PROFILES = (
    (0x04000000000003BF, 1.6, 1.12),
    (0x04000000000002A3, 1.425, 0.9975),
    (0x04000000000003CF, 1.55, 1.085),
    (0x04000000000001C0, 1.5, 1.05),
)  # body, original FirstPerson m_E29993A5, m_816B830F; sealed in replay configuration
EYE_CROUCHED_DEFAULT = 1.15
HEAD_BELOW = 0.2  # m under the eye where the head starts (unverified)
HISTORY = 64  # ticks of body positions kept for the shots' view time (about 1 s; unverified: retail's limit)
COMBAT_CATCH_UP = 64  # at most about a second of retained combat frames per update
RANGE = 200.0  # m a hitscan shot reaches when its volley names none (unverified)
RESPAWN = 10.0  # s a hero waits in the PvP modes (0F4A and 0EC2 send 06FD.025 {10}: DATA)
PRACTICE_RESPAWN = 3.0  # s in the Practice Range (13C0 sends 06FD.025 {3}: DATA)
BOT_RESPAWN = 3.0  # s a training bot lies dead (unverified: OW2 made two killed bots again after 2.98 s)
RESPAWN_RETRY = 0.25  # s between attempts when all player body IDs are still reserved
HAMMER = 0x0048
HAMMER_TARGETS = {3: (1, 8), 4: (2, 9)}
MELEE = {0x0043: (4, 1.5, 1.0)}  # graph -> (its ModifyHealth state, metres ahead of the eye, radius): DATA
ARMOUR_BLOCK = 5.0  # damage armour stops per hit (unverified: OW1's rule; the client has no damage code)
BEAM_ARMOUR_SCALAR = 0.7  # Blizzard2020-08-31 live patch: beam reduction30%
TICK = 0.016
RIFLE_DOSE_SECONDS = 0.6
RIFLE_DOSE_TICKS = math.ceil(RIFLE_DOSE_SECONDS / TICK)
GENJI_ASSIST_TICKS = math.ceil(5.0 / TICK)  # Reconstructed damage contribution window.
EPSILON = 1e-9
FLASHBANG_EFFECT = 0x04A0000000000079  # .053/0079 ->0228 ->224E
SLEEP_DART_EFFECT = 0x04A000000000026F  # 0BDD/state6's authored hit effect
SLEEP_TICKS = math.ceil(5.0 / TICK)  # Reconstructed control duration.
ANA_GRENADE = 0x0400000000001426
FIRE_STRIKE_PROJECTILE = 0x04000000000001EA
ENFORCER_PELLET = 0x04000000000023F0  # Authored 13EB volley projectile.
GENJI_SHURIKEN = 0x04000000000007CA  # Both authored 0304 volley nodes.
SNIPER_ROUND = 0x0400000000001E80  # Authored 1122 volley projectile.
BIOTIC_TICKS = math.ceil(4.0 / TICK)
FLASHBANG_MODE_FLAG = 13408  # 0228 duration: flag set1.7s, otherwise0.8s


# --- geometry -----------------------------------------------------------------------------------------


def eye_height(body: int, crouched: bool = False) -> float:
    """Verified FirstPerson base heights; unlisted bodies retain their existing fallback."""
    for definition, standing, low in EYE_PROFILES:
        if body == definition:
            return low if crouched else standing
    return EYE_CROUCHED_DEFAULT if crouched else EYE


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


class DvaBoosterImpulse(runtime.InstanceContext):
    def native(self, cfg: dict):
        result = super().native(cfg)
        if cfg.get("$") == entity_queries.POSSESSED and not expr.valid_entity(expr.to_entity(result)):
            linked = cfg.get("m_entity") or {}
            owner = linked.get("m_D63CBEC2") or {}
            if linked.get("$") == "STU_F76C486C" and owner.get("$") == "STU_B5A0CAF0":
                # Explicit 0407 selection puts the owner and the controlled mech in one body.
                return expr.Entity(self.instance.component.entity)
        return result


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


def _segment_point(point, a, b):
    ab, ap = _sub(b, a), _sub(point, a)
    length = _dot(ab, ab)
    t = 0.0 if length <= EPSILON else max(0.0, min(1.0, _dot(ap, ab) / length))
    return _along(a, ab, t)


def segment_distance(point, a, b) -> float:
    """The distance from a point to the segment a-b."""
    d = _sub(point, _segment_point(point, a, b))
    return math.sqrt(_dot(d, d))


def capsule_visible(collision: World | None, origin, a, b, radius: float, *, cover=None) -> bool:
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
    hit = ray_world(collision, origin, direction, reach) if cover is None else cover(origin, direction, reach)
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
        self.healing_block_until = 0
        self.healing_boost_until = 0
        self.nano_until = 0

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
        """Tagged reconstructed pools: capped current amount, no healing refill.

        Stable slots/IDs remain after depletion to clear the previous wire value.
        Native pool query/stacking and temporary UI flags remain unaccepted.
        """
        if not math.isfinite(amount) or not math.isfinite(limit) or amount < 0 or limit < 0:
            raise ValueError("temporary shields require finite nonnegative amount and limit")
        if self.dead or amount == 0:
            return 0.0
        pool = self.temporary_shields.get(tag)
        if pool is None:
            slots = POOLS - int(SHIELDS_POOL in self.ids)
            if len(self.temporary_shields) >= slots:
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

    def damage(self, amount: float, *, armour_scalar: float | None = None) -> float:
        """Consume pools; ordinary hits use flat armour reduction, beam/DOT supply a scalar.

        Scalar applies only when reaching a nonempty armour pool. Overflow keeps
        this pool service's reduced remainder; exact native boundary timing is open.
        """
        left = max(0.0, amount)
        taken = 0.0
        # Host policy: temporary shields precede base pools, newest tag first.
        for tag in reversed(self.temporary_shields):
            take = self.decay_temporary_shields(tag, left)
            left -= take
            taken += take
        for kind in (SHIELDS_POOL, ARMOUR_POOL, HEALTH_POOL):
            if left <= 0.0:
                break
            if kind == ARMOUR_POOL and self.now[kind] > 0.0:
                left = (max(left - ARMOUR_BLOCK, left / 2) if armour_scalar is None
                        else left * armour_scalar)
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
        kinds = set(kinds)
        parts: list = [None] * (3 * POOLS)
        for kind in kinds:
            if kind in self.ids:
                parts[kind * POOLS] = world.pool(self.ids[kind], kind, self.most[kind], self.now[kind])
        if SHIELDS_POOL in kinds:
            start = int(SHIELDS_POOL in self.ids)
            for slot, pool in enumerate(self.temporary_shields.values(), start):
                parts[SHIELDS_POOL * POOLS + slot] = world.pool(
                    pool.pool_id, SHIELDS_POOL, pool.most, pool.now)
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
    self_scale: float = 0.0
    self_impulse_scale: float = 0.0
    horizontal_impulse: float = 0.0
    vertical_impulse: float = 0.0
    impulse_rings: tuple = ()
    biotic_rifle: bool = False

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
        return self._ring_scale(self.rings, distance)

    @staticmethod
    def _ring_scale(rings, distance):
        if not rings:
            return 0.0
        last_radius, last_scalar = rings[0]
        if distance <= last_radius:
            return last_scalar
        for radius, scalar in rings[1:]:
            if distance <= radius:
                share = (distance - last_radius) / max(EPSILON, radius - last_radius)
                return last_scalar + (scalar - last_scalar) * share
            last_radius, last_scalar = radius, scalar
        return 0.0

    def knockback_scale(self, distance, own):
        column = 2 if own else 1
        scale = self._ring_scale(tuple((row[0], row[column]) for row in self.impulse_rings), distance)
        return scale * self.self_impulse_scale if own else scale


def _number(value, default=None):
    if value is None:
        return default
    try:
        return float(expr.to_float(value))
    except (TypeError, ValueError):
        return default


def _critical_default(state) -> float:
    """Scoped primary defaults from the McCree and D.Va lanes."""
    world = state.instance.component.world
    if getattr(world, "body", None) != state.instance.component.entity:
        return 1.0
    volley = (getattr(world, "hero", None), state.instance.graph.index, state.node.pos)
    return 2.0 if volley in ((0x02E0000000000042, 0x01D2, 31), (dva.HERO, dva.LIGHT_GUN, 10)) else 1.0


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
    world = state.instance.component.world
    secondary = (cfg.get("$") == "STU_4AA6CA44" and world.hero == zarya.HERO
                 and state.instance.graph.index == 0x0241 and state.node.pos == 41)
    impulse_rings = ()
    rock = state.instance.graph.index == accretion.ACCRETION and state.node.pos == 9
    sigma_impulse = rock or (state.instance.graph.index == 0x187F and state.node.pos in (45, 56))
    if secondary or sigma_impulse:
        impulse_rings = tuple(sorted(
            tuple(_number(state.evaluate(ring.get(key), watch=False), 0.0)
                  for key in ("m_radius", "m_579D7CFF", "m_10399AE7"))
            for ring in modify.get("m_knockbackRings") or ()
        ))
    return HitDef(
        amount=amount,
        biotic_rifle=state.instance.graph.index == 0x0B94 and state.index in (5, 44),
        loadout=loadout.guid if isinstance(loadout, expr.Asset) else None,
        falloff_start=value("m_EAD7F104"),
        falloff_end=value("m_157E5BC5"),
        falloff_floor=value("m_72A81154"),
        critical=value("m_A872C70E", _critical_default(state)),
        rings=tuple(sorted(rings)),
        effect=effect.guid if isinstance(effect, expr.Asset) else None,
        self_scale=value("m_8A3E3883", 0.0) if secondary else 0.0,
        self_impulse_scale=value("m_2FE97560", 0.0) if secondary else 0.0,
        horizontal_impulse=_number(state.evaluate(modify.get("m_E33EFEE1"), watch=False), 0.0)
        if secondary or sigma_impulse else 0.0,
        vertical_impulse=_number(state.evaluate(modify.get("m_583C208B" if sigma_impulse else "m_10399AE7"),
                                                watch=False), 0.0) if secondary or sigma_impulse else 0.0,
        impulse_rings=impulse_rings,
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
class CommandContact:
    shooter: object
    body: int
    frame: int
    origin: tuple
    direction: tuple
    view_tick: float
    reach: float
    radius: float
    amount: float
    loadout: int | None
    melee: bool = False


@dataclass
class RifleDose:
    shooter: object
    source: int
    team: int
    target: int
    start_tick: int
    amount: float
    healing: bool
    loadout: int | None
    frame: int
    applied: float = 0.0


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
    spawn: int | None = None  # authored deployable, independent of client prediction delivery
    source_entity: int | None = None  # body identity at emission, retained across replacements
    offense_scale: float = 1.0
    gravity: float = 0.0  # reconstructed Zarya E1A5B579 vertical acceleration
    definition: int | None = None  # emitted projectile identity, independent of a prediction lease
    distance: float = 0.0  # cumulative swept path, including before a reflection
    velocity: tuple | None = None  # accelerated Flashbang sweep velocity
    detonate_on_expiry: bool = False
    penetration_radius: float = 0.0
    hit_targets: set | None = None  # Fire Strike receiver lifetimes
    halt_instance: int | None = None
    halt_at: int | None = None
    halt_trigger_tick: int | None = None
    halt_entity: int | None = None
    orisa_launch: tuple | None = None
    accretion_source: tuple[int, int] | None = None
    sphere_radius: float = 0.0
    sphere_wall_radius: float = 0.0
    sphere_bounces: int = 0
    bounce_normal: float = 0.0
    bounce_tangent: float = 0.0


def _world_timed_flight(definition, spawn, direct: HitDef | None, blast: HitDef | None) -> bool:
    return (definition in (zarya.PARTICLE_GRENADE, accretion.PROJECTILE, ANA_GRENADE, FIRE_STRIKE_PROJECTILE)
            or spawn == graviton.FIELD_ENTITY
            or (direct is not None and (direct.effect == SLEEP_DART_EFFECT or direct.biotic_rifle))
            or (blast is not None and blast.effect == FLASHBANG_EFFECT))


class Combat:
    """The match's health, hits, deaths and respawns."""

    def __init__(self, match) -> None:
        self.match = match
        self.vitals: dict[int, Vitals] = {}  # body or bot entity -> its health
        self.history: dict[int, deque] = {}  # entity -> (tick, feet, height, eye) of the last HISTORY ticks
        self.replacement_ticks: dict[int, int] = {}  # body -> authoritative creation tick
        self.projectile_defense_starts: dict[int, dict[tuple[int, int, int, int], int]] = {}
        self.projectile_defense_windows: dict[int, dict[tuple[int, int, int, int], interception.DefenseWindow]] = {}
        self.rockets: list[Rocket] = []
        self.respawns: list[tuple[float, object]] = []  # (when, a player or a bot)
        self.next_bot = bots.BOT_ENTITY + len(match.bots)
        self.now = 0.0
        self.tick = 0
        self.zarya_charge_tick = 0
        self.zarya_charge_ready: dict[int, int] = {}
        self.melee_seen: dict[int, dict[tuple[int, int], int]] = {}  # body -> counters by instance/state
        self.zarya_melee_ready: dict[int, int] = {}
        self.zarya_secondary_ready: dict[int, int] = {}
        self.genji_strikes: dict[int, tuple[int, int, set[int]]] = {}
        self.genji_blades: dict[int, dict[tuple[int, int], tuple[int, set[int]]]] = {}
        self.genji_assists: dict[int, dict[int, tuple[object, int]]] = {}
        self.rifle_doses: list[RifleDose] = []
        self.pending_nano: dict[int, list[tuple[int, tuple, int]]] = {}
        self.pending_contacts: dict[int, list[CommandContact]] = {}
        self.sleeps: dict[int, int] = {}
        self.charge_contacts: dict[int, reinhardt_charge.Contact] = {}
        self.halt_pulls: dict[int, tuple[tuple, int, int]] = {}
        self.shatters: dict[int, earthshatter.Wave] = {}
        self.booster_contacts: dict[int, tuple[int, int, set[int]]] = {}
        self.hammer_hits: dict[int, dict] = {}
        self.beam_seen: dict[int, tuple] = {}  # body -> authored activation and consumed command time
        self.zarya_beam_world_ms: dict[int, int] = {}
        self.zarya_barriers = zarya.Barriers(self)
        self.zarya_recovery = zarya.ShieldRecovery(self)
        self.zarya_gravitons = graviton.Gravitons(self)
        self.winston_jump = winston_jump.JumpPack(self)
        self.winston_primal = winston_primal.Primal(self)
        self.grasp_health = interception.GraspShields(self)
        self.reinhardt_barriers = reinhardt_barrier.ReinhardtBarriers(self)
        self.stuns: dict[int, int] = {}  # body identity -> exclusive world-tick deadline
        self.pins: dict[int, int] = {}  # reconstructed Assassin body -> current pinned body
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
        self.clear_pins(player.body)
        self.halt_pulls.pop(player.body, None)
        self.vitals.pop(player.body, None)
        self.history.pop(player.body, None)
        self.replacement_ticks.pop(player.body, None)
        self.projectile_defense_starts.pop(player.body, None)
        self.projectile_defense_windows.pop(player.body, None)
        self.melee_seen.pop(player.body, None)
        self.zarya_melee_ready.pop(player.body, None)
        self.zarya_secondary_ready.pop(player.body, None)
        self.genji_strikes.pop(player.body, None)
        self.genji_blades.pop(player.body, None)
        self.genji_assists.pop(player.body, None)
        self.rifle_doses[:] = [dose for dose in self.rifle_doses if dose.target != player.body]
        self.hammer_hits.pop(player.body, None)
        self.shatters.pop(player.body, None)
        self.booster_contacts.pop(player.body, None)
        self.beam_seen.pop(player.body, None)
        self.zarya_beam_world_ms.pop(player.body, None)
        self.winston_jump.retire(player.body)
        self.winston_primal.retire(player.body)
        self.zarya_barriers.retire(player.body)
        self.zarya_charge_ready.pop(player.body, None)
        self.zarya_recovery.retire(player.body)
        self.grasp_health.retire(player.body)
        self.reinhardt_barriers.retire(player.body)
        self.stuns.pop(player.body, None)
        self.sleeps.pop(player.body, None)
        # The owner's leased projectiles and their observer copies retire with the old body.
        self.rockets[:] = [
            rocket for rocket in self.rockets
            if rocket.shooter is not player or (rocket.shot is None and rocket.accretion_source is None
                                                and not rocket.sphere_radius and rocket.halt_instance is None
                                                and rocket.orisa_launch is None)
        ]
        self.respawns[:] = [(when, who) for when, who in self.respawns if who is not player]

    def record_replacement(self, body: int) -> None:
        """Keep the creation boundary independent of the rolling pose history."""
        self.replacement_ticks[body] = self.match.tick
        self.stuns.pop(body, None)
        self.sleeps.pop(body, None)
        self.grasp_health.retire(body)

    def is_stunned(self, actor, *, tick: int | None = None) -> bool:
        body = _source_entity(actor)
        return (self.control_deadline(body) > (self.tick if tick is None else tick)
                or body in self.pins.values() or reinhardt_charge.pinned(self, body))

    def control_deadline(self, body: int) -> int:
        return max(self.stuns.get(body, 0), self.sleeps.get(body, 0))

    def settle_sleep_before_input(self, body: int) -> None:
        """Resolve earlier wake damage before masking an ahead-of-world input."""
        admitted = self.match.tick
        previous = admitted - 1
        if previous <= self.tick or self.sleeps.get(body, 0) <= self.tick:
            return
        now = self.now + (previous - self.tick) * TICK
        self.match.tick = previous
        try:
            self.update(now, previous, frames=False)
        finally:
            self.match.tick = admitted

    def clear_pins(self, entity: int) -> None:
        reinhardt_charge.retire(self, entity)
        for source, target in tuple(self.pins.items()):
            if entity in (source, target):
                self.pins.pop(source, None)

    def _hit_effect(self, target: Target, effect: int | None, tick: int | None = None) -> None:
        """Resource-backed stun reconstruction; native immunity/presentation remain unverified."""
        tick = self.tick if tick is None else tick
        if effect == SLEEP_DART_EFFECT:
            if not self.vitals_of(target).dead and defenses.fortify_state(target.owner, tick=tick) is None:
                self.sleeps[target.entity] = tick + SLEEP_TICKS
                # Damage can wake the target before a later input becomes due.
                self._interrupt(target, tick=tick, until=tick + 1)
            return
        if effect != FLASHBANG_EFFECT:
            return
        variables = graph.mode_variables(self.match.game_map.mode_guid) or {}
        duration = 1.7 if expr.truthy(variables.get(FLASHBANG_MODE_FLAG)) else 0.8
        self.stun(target, duration, tick=tick)

    def stun(self, target: Target, duration: float, *, tick=None) -> None:
        tick = self.tick if tick is None else tick
        if duration <= 0 or self.vitals_of(target).dead or defenses.fortify_state(target.owner, tick=tick) is not None:
            return
        deadline = tick + math.ceil(duration / TICK)
        self.stuns[target.entity] = max(deadline, self.stuns.get(target.entity, 0))
        self._interrupt(target, tick=tick, until=self.stuns[target.entity])

    def _interrupt(self, target: Target, *, preserve_zarya_barriers=False, tick=None, until=None) -> None:
        """Use authored ability exits for reconstructed Flashbang/Assassin control."""
        preserved, held, refunds = interception.interrupt_windows(
            self, target.entity, self.tick if tick is None else tick, until,
        )
        script = getattr(target.owner, "body_script", None)
        if script is None:
            return
        component = script.component
        sigma_flux.interrupt_flux_lift(self, target.owner)
        component.edges([(value, False) for value in tuple(component.held) if value not in held])
        for instance in list(component.instances.values()):
            if instance in preserved:
                continue
            if component.world.hero == reinhardt_barrier.HERO and instance.graph.index == HAMMER:
                # Hammer is driven by a stack and waits rather than an Ability.
                swing = instance.states.get(13)
                if swing is not None and swing.active:
                    swing.request(runtime.ABORT)
            for state in list(instance.states.values()):
                if isinstance(state, nodes.Ability):
                    state.presses.clear()
                    persistent_blade = (component.world.hero == 0x02E0000000000029
                                        and instance.graph.index == 0x0317 and state.index == 3)
                    retained_barrier = preserve_zarya_barriers and instance.graph.index in (0x0244, 0x025C)
                    if not (persistent_blade or retained_barrier):
                        state.stop()  # use authored exit/cooldown paths for running channels
        component.run_now()
        for instance, cost in refunds:
            cost.restore(instance)
        if refunds:
            component.run_now()
        component.shots.clear()
        self.winston_primal.interrupt(script)
        self.stop_genji_strike(target.owner)
        if isinstance(target.owner, bots.Bot) and target.owner.attack_action == "charge":
            heavy_charge.finish(self, target.owner, heavy_charge.instance_of(target.owner))
        else:
            reinhardt_charge.stop(self, target.owner)

    def spawned(self, player) -> None:
        """The player's client has the world: the game mode entity gets its UI graph (mode_frame). While
        heroes are assembled the countdown's frames carry it."""
        if player.client is not None and not self.match.assembling():
            player.client.queue_mode_frame(mode_frame(
                {}, player.match.game_map.mode_guid, player.match.game_map.map_guid,
            ))
        self.voice.spawned(player, self.now)

    def command(self, player, command: Command | None, *, defense_costs=None) -> None:
        """Consume the body's reports. Only a frame with a pose can affect targets."""
        if self.match.collision_world is None:
            return
        script = player.body_script
        if script is None:
            return
        shots, script.component.shots[:] = list(script.component.shots), []
        if command is not None:
            sigma_barrier.observe_phase_command(self, player)
            self.reinhardt_barriers.sync(player, admitted_tick=self.match.tick)
        elif self.match.tick <= self.tick:
            self.reinhardt_barriers.sync(player)
        self.winston_primal.sync(player, script)
        interception.observe(self, player, script, defense_costs)
        if self.is_dead(player):
            return
        if command is not None:
            self.winston_jump.command(player, command, script)
        if self.is_stunned(player, tick=self.match.tick):
            return
        if command is not None:
            winston_barrier.observe_cast(self, player, script)
            self._start_shatter(player)
            self._booster_contact(player, command)
            for state, number, time, activation in shots:
                try:
                    self._shoot(player, command, state, number, time, activation=activation)
                except Exception:
                    log.exception("[game] %s: %s's shot failed", self.match.label(), player.name)
        try:
            self._beam(player, command, script)
            self._tesla(player, command, script)
        except Exception:
            log.exception("[game] %s: %s's beam failed", self.match.label(), player.name)
        try:
            self._melee(player, command, script)
            self._hammer(player, command, script)
            self._genji_blade(player, command, script)
            if command is not None:
                self.winston_primal.command(player, command, script)
        except Exception:
            log.exception("[game] %s: %s's melee failed", self.match.label(), player.name)

    def stop_boosters(self, player) -> None:
        instance = dva.boosters(player)
        if instance is None:
            return
        ability = instance.states.get(0)
        if not isinstance(ability, nodes.Ability) or not ability.flags & ability.RUNNING:
            return
        ability.presses.clear()
        ability.stop()
        instance.component.run_now()
        self.booster_contacts.pop(player.body, None)
        player.sync_movement_mods(instance.component.frame)

    def _booster_contact(self, player, command) -> None:
        instance = dva.boosters(player)
        if instance is None:
            return
        track, modify = instance.states.get(6), instance.states.get(7)
        if track is None or modify is None or not (track.active and modify.active):
            self.booster_contacts.pop(player.body, None)
            return
        offset = track.evaluate(track.node.field("m_05BE62DD"), watch=False)
        radius = _number(track.evaluate(track.node.field("m_radius"), watch=False), 0)
        hit = modify.node.field("m_modifyHealth")
        amount = _number(modify.evaluate(hit.get("m_amount"), watch=False), 0)
        loadout = modify.evaluate(hit.get("m_loadout"), watch=False)
        loadout = loadout.guid if isinstance(loadout, expr.Asset) else None
        if not isinstance(offset, expr.Vec3) or radius <= 0 or amount <= 0:
            return
        mover = player.mover
        yaw, pitch = mover.yaw, mover.pitch
        centre = _along(mover.position, aim(yaw - 16384, 0), offset.x)
        centre = _along(centre, aim(yaw, pitch - 16384), offset.y)
        centre = _along(centre, aim(yaw, pitch), offset.z)
        activation = instance.id, modify.counter
        contacts = self.booster_contacts.get(player.body)
        if contacts is None or contacts[:2] != activation:
            contacts = self.booster_contacts[player.body] = (*activation, set())
        touched = contacts[2]
        eye = self._eye(player)
        scaled = amount * self.offense_scale(player.body)
        volumes = ((centre, centre, radius),)
        for target in self._enemies(player):
            if target.entity in touched or phase.protection(target.owner) == "phase":
                continue
            a, b = target.segment()
            if (segment_distance(centre, a, b) > radius + target.radius
                    or not self.combat_visible(eye, a, b, target.radius, exclude=target.owner)):
                if (scaled > 0 and self._melee_bubble(player, eye, target, volumes)
                        and self.zarya_barriers.intercept(target.owner, scaled, player.body)):
                    touched.add(target.entity)
                continue
            touched.add(target.entity)
            if not self._damage(player, target, amount, False, loadout, command.frame):
                continue
            if self.vitals_of(target).dead:
                continue
            context = DvaBoosterImpulse(instance, None)
            horizontal = _number(expr.evaluate(hit.get("m_E33EFEE1"), context), 0)
            vertical = _number(expr.evaluate(hit.get("m_583C208B"), context), 0)
            heading = target.feet[0] - mover.position[0], target.feet[2] - mover.position[2]
            if math.hypot(*heading) <= EPSILON:
                heading = aim(yaw, 0)[::2]
            if self._body_impulse(target, heading, horizontal, vertical, external=True):
                assassin.interrupt_pin(self, target)

    def shatter_ground(self, body):
        """Declared BE/37 downward1.5m ground query, using the posed owner's feet."""
        player = next((p for p in self.match.players if p.body == body), None)
        if player is None or self.match.collision_world is None:
            return None
        x, y, z = player.mover.position
        return self._collision().ground(x, z, y + .05, y - 1.5, player.mover.category)

    def _start_shatter(self, player):
        script = player.body_script
        if script.hero != reinhardt_barrier.HERO or player.body in self.shatters:
            return
        instance = next((i for i in script.component.instances.values() if i.graph.index == 0xBE), None)
        state = instance.states.get(0) if instance is not None else None
        if state is None or not state.active:
            return
        origin = instance.find_var(0, 431).value()
        near = instance.find_var(0, 1188).value()
        if not isinstance(origin, expr.Vec3) or not isinstance(near, expr.Vec3):
            return
        def number(name):
            return _number(state.evaluate(state.node.field(name), watch=False), 0)

        base = instance.state(1).node.field("m_modifyHealth")
        close = instance.state(41).node.field("m_modifyHealth")
        loadout = state.evaluate(base.get("m_loadout"), watch=False)
        wave = earthshatter.Wave(
            player, player.body, player.team, instance.id, state.counter, (origin.x, origin.y, origin.z),
            player.mover.yaw * math.tau / 65536, number("m_speed"), number("m_6A79BD98"),
            number("m_A30B383E"), number("m_525E5A40"),
            _number(instance.find_var(0, 215).value(), 0),
            _number(state.evaluate(base.get("m_amount"), watch=False), 0),
            _number(state.evaluate(close.get("m_amount"), watch=False), 0),
            (near.x, near.y, near.z),
            _number(state.evaluate(instance.state(40).node.field("m_radius"), watch=False), 0),
            loadout.guid if isinstance(loadout, expr.Asset) else None, self.now,
        )
        if wave.speed > 0 and wave.reach > 0:
            self.shatters[player.body] = wave
            earthshatter.advance(self, wave)

    def _finish_shatter(self, wave):
        self.shatters.pop(wave.body, None)
        script = wave.shooter.body_script
        if script is None or wave.shooter.body != wave.body:
            return
        instance = script.component.instances.get(wave.instance)
        state = instance.states.get(0) if instance is not None else None
        if state is not None and state.active and state.counter == wave.activation:
            state.request(runtime.FINISH)
            script.component.run_now()  # authored onEnd/ActionStopAbility owns the channel end

    def prepare_shatter_frame(self, player):
        script = player.body_script
        if script is None or script.hero != reinhardt_barrier.HERO:
            return
        instance = next((i for i in script.component.instances.values() if i.graph.index == 0xBE), None)
        script.component.set(417, bool(player.mover.airborne))
        air = instance.states.get(8) if instance is not None else None
        if air is not None and air.active and not player.mover.airborne:
            # Scoped reconstruction of44's missing elapsed-state predicate. Its
            # authored .383s floor precedes the original landing-animation path;
            # do not change unrelated permissive4E2BCAAB conditions.
            gate = instance.graph.node(44)
            delay = _number(air.evaluate(gate.field("m_AA6E611E"), watch=False), 0)
            if (script.component.now - air.start >= delay * 1000
                    and instance.follow(gate, "m_truePlug", air)):
                air.request(runtime.ABORT)
        tracker = instance.states.get(37) if instance is not None else None
        if tracker is not None and tracker.active:
            ground = self.shatter_ground(player.body)
            instance.write((0, 10360), ground is not None)
            if ground is not None:
                instance.write((0, 1724), expr.Vec3(player.mover.position[0], ground.y,
                                                  player.mover.position[2]))
                marker = instance.states.get(29)
                if marker is not None and marker.active:
                    marker.request_finish(0)

    def _tesla(self, player, command, script) -> None:
        """Resolve 00FE's continuous body arcs using its authored search and damage states."""
        if (command is None or script.hero != winston_jump.HERO
                or winston_primal.health_state(script) is not None):
            return
        instance = next((i for i in script.component.instances.values() if i.graph.index == 0x00FE), None)
        if instance is None:
            return
        search, damage = instance.states.get(10), instance.states.get(13)
        if search is None or damage is None or not (search.active and damage.active):
            return
        now = command.frame * script.component.frame_ms
        previous = self.beam_seen.get(player.body)
        if previous is not None and now <= previous[1]:
            return
        self.beam_seen[player.body] = ((instance.id, damage.counter, damage.start), now)
        reach = _number(search.evaluate(search.node.field("m_radius"), watch=False), 0)
        angle = _number(search.evaluate(search.node.field("m_A30B383E"), watch=False), 0)
        definition = hit_def(damage, {"m_modifyHealth": damage.node.field("m_modifyHealth")})
        if definition is None or definition.amount <= 0 or reach <= 0 or not 0 < angle < 90:
            return
        amount = definition.amount * player.quanta / 1e6
        width = math.tan(math.radians(angle))
        origin, direction = self._eye(player), aim(*command.aim())
        view_tick = command.frame - command.view_delay(player.quanta) * 1e6 / player.quanta
        for target in self._enemies(player):
            shown = self.seen(target, view_tick)
            if shown is None:
                continue
            a, b = shown.segment()
            point = (a[0], max(a[1], min(b[1], origin[1])), a[2])
            offset = _sub(point, origin)
            distance_sq = _dot(offset, offset)
            forward = _dot(offset, direction)
            lateral = math.sqrt(max(0.0, distance_sq - forward * forward))
            if (distance_sq > (reach + shown.radius) ** 2 or forward < 0
                    or lateral > forward * width + shown.radius):
                continue
            if self.combat_visible(origin, a, b, shown.radius, exclude=player):
                self._damage(player, target, amount, False, definition.loadout, command.frame,
                             armour_scalar=0.8)  # Host continuous-armour policy for Tesla.

    def _beam(self, player, command, script) -> None:
        """Bridge Zarya's authored continuous ModifyHealth, not a WeaponVolley.

        0241/node28 supplies DPS (75..170); node81 supplies range 15 and radius .15.
        Reconstruction: integrate DPS over consumed command time within elapsed world
        time for this activation, and sweep from the eye
        against body capsules and barrier spheres centred on the historical body midpoint.
        Native muzzle geometry, shield attachment and ray output bindings remain open.
        """
        if command is None:
            return
        for instance in script.component.instances.values():
            if instance.graph.index != 0x0241:
                continue
            state = instance.state(10)
            if not state.active:
                return
            ray = instance.graph.node(81)
            now = command.frame * script.component.frame_ms
            activation = (instance.id, state.counter, state.start)
            previous = self.beam_seen.get(player.body)
            world_now = self.match.tick * round(TICK * 1000)
            if previous is None or previous[0] != activation:
                self.beam_seen[player.body] = (activation, now)
                self.zarya_beam_world_ms[player.body] = world_now
                return
            if now <= previous[1]:
                return
            self.beam_seen[player.body] = (activation, now)
            world_used = self.zarya_beam_world_ms.get(player.body, world_now)
            elapsed = min(now - previous[1], world_now - world_used)
            if elapsed <= 0:
                return
            self.zarya_beam_world_ms[player.body] = world_used + elapsed
            direct = hit_def(state, {"m_modifyHealth": state.node.field("m_modifyHealth")})
            if direct is None or direct.amount <= 0:
                return
            reach = _number(state.evaluate(ray.field("m_F5732403.m_6A79BD98"), watch=False), 0)
            radius = _number(state.evaluate(ray.field("m_57DAE1B5"), watch=False), 0)
            origin, direction = self._eye(player), aim(*command.aim())
            view_tick = command.frame - command.view_delay(player.quanta) * 1e6 / player.quanta
            self._command_contact(CommandContact(
                player, player.body, command.frame, origin, direction, view_tick,
                reach, radius, direct.amount * elapsed / 1000, direct.loadout,
            ))
            return

    def _command_contact(self, contact: CommandContact) -> None:
        if self.match.tick > self.tick:
            self.pending_contacts.setdefault(self.match.tick, []).append(contact)
        else:
            self._resolve_command_contact(contact)

    def _resolve_command_contact(self, contact: CommandContact) -> None:
        player = contact.shooter
        if (self.match.ended or player.body != contact.body or player.gone
                or self.is_dead(player) or self.is_stunned(player)):
            return
        if contact.melee:
            self._melee_contact(contact, tick=self.tick)
        else:
            self._beam_contact(contact)

    def _beam_contact(self, contact: CommandContact) -> None:
        player = contact.shooter
        origin, direction = contact.origin, contact.direction
        reach, radius = contact.reach, contact.radius
        best = None
        for target in self._enemies(player):
            if (self.replacement_ticks.get(target.entity, 0) > self.tick
                    or phase.protection(target.owner, tick=self.tick) is not None):
                continue
            shown = self.seen(target, contact.view_tick)
            if shown is None:
                continue
            a, b = shown.segment()
            distance = ray_capsule(origin, direction, a, b, shown.radius + radius)
            limit = min(reach, distance if distance is not None else reach, best[0] if best else reach)
            edge = self._bubble_distance(shown, origin, direction, limit, radius, tick=self.tick)
            if edge is not None:
                distance = edge
            if distance is not None and distance <= reach and (best is None or distance < best[0]):
                best = distance, target, edge is not None
        limit = best[0] if best is not None else reach
        barrier = self.reinhardt_barriers.trace(player, origin, direction, limit, radius=radius)
        if best is None and barrier is None:
            return
        wall = self.cover_ray(origin, direction, limit, exclude=player, cryo_radius=radius, tick=self.tick)
        amount = contact.amount
        if barrier is not None and (wall is None or barrier[0] < wall):
            self.reinhardt_barriers.damage(barrier[1], amount * self.offense_scale(player.body))
        elif best is not None and (wall is None or wall >= limit):
            _, target, bubble = best
            if bubble:
                self.zarya_barriers.intercept(target.owner, amount * self.offense_scale(player.body),
                                              contact.body, tick=self.tick)
            else:
                self._damage(player, target, amount, False, contact.loadout, contact.frame,
                             world_tick=self.tick, armour_scalar=BEAM_ARMOUR_SCALAR)

    def _bubble_distance(self, target: Target, origin, direction, limit, radius=0.0, *, tick=None):
        """Intersect the authored sphere at a reconstructed body-midpoint attachment."""
        x, y, z = target.feet
        centre = (x, y + target.height / 2, z)
        edge = ray_capsule(origin, direction, centre, centre, zarya.BUBBLE_RADIUS + radius)
        if edge is not None and edge <= limit and self.zarya_barriers.protects(target.owner, tick=tick):
            return edge
        return None

    def stop_genji_strike(self, player) -> None:
        if getattr(player.hero, "guid", None) != 0x02E0000000000029 or player.body_script is None:
            return
        self.genji_strikes.pop(player.body, None)
        component = player.body_script.component
        instance = next((i for i in component.instances.values() if i.graph.index == 0x01DB), None)
        dash = instance.states.get(1) if instance is not None else None
        if dash is not None and dash.active:
            dash.request(runtime.ABORT)
            component.run_now()

    def genji_strike(self, player, state, start, frame, *, finished: bool) -> None:
        """Sweep 01DB over actual displacement, crediting each body or its barrier once."""
        instance = state.instance
        identity = instance.id, state.counter
        contact = self.genji_strikes.get(player.body)
        victims = contact[2] if contact is not None and contact[:2] == identity else set()
        if finished:
            self.genji_strikes.pop(player.body, None)
        else:
            self.genji_strikes[player.body] = (*identity, victims)
        centre = _along(player.mover.position, (0, 1, 0), 1)
        start = _along(start, (0, 1, 0), 1)
        delta = _sub(centre, start)
        length = math.sqrt(_dot(delta, delta))
        if length <= EPSILON:
            return
        tracker = instance.state(33)
        radius = _number(tracker.evaluate(tracker.node.field("m_radius"), watch=False), 0)
        modify = instance.state(3).node.field("m_modifyHealth")
        amount = _number(instance.evaluate(modify.get("m_amount")), 0)
        loadout = instance.evaluate(modify.get("m_loadout"))
        direction = tuple(axis / length for axis in delta)
        volumes = ((start, centre, radius),)
        scaled = amount * self.offense_scale(player.body)
        for target in self._enemies(player):
            if target.entity in victims:
                continue
            a, b = target.segment()
            distance = ray_capsule(start, direction, a, b, target.radius + radius)
            if (distance is not None and distance <= length
                    and self.combat_visible(centre, a, b, target.radius, exclude=player)):
                victims.add(target.entity)
                self._damage(player, target, amount, False,
                             loadout.guid if isinstance(loadout, expr.Asset) else None, frame)
            elif scaled > 0.0 and self._melee_bubble(player, centre, target, volumes):
                if self.zarya_barriers.intercept(target.owner, scaled, player.body):
                    victims.add(target.entity)

    def _genji_blade(self, player, command, script) -> None:
        """Resolve 0304's authored sword windows against live bodies and barriers."""
        if script.hero != 0x02E0000000000029 or command is None:
            return
        instance = next((i for i in script.component.instances.values() if i.graph.index == 0x0304), None)
        if instance is None:
            return
        centre = _along(player.mover.position, (0, 1, 0), 1)
        facing = aim(*command.aim())
        for tracker, damage in ((66, 21), (67, 22)):
            track = instance.states.get(tracker)
            if track is None or not track.active:
                continue
            records = self.genji_blades.setdefault(player.body, {})
            key = (instance.id, tracker)
            activation, victims = records.get(key, (0, set()))
            if activation != track.counter:
                victims = set()
                records[key] = track.counter, victims
            radius = _number(track.evaluate(track.node.field("m_radius"), watch=False), 0)
            angle = _number(track.evaluate(track.node.field("m_A30B383E"), watch=False), 0)
            threshold = math.cos(math.radians(angle))
            state = instance.state(damage)
            modify = state.node.field("m_modifyHealth")
            amount = _number(state.evaluate(modify.get("m_amount"), watch=False), 0)
            loadout = state.evaluate(modify.get("m_loadout"), watch=False)
            volumes = ((centre, centre, radius),)
            scaled = amount * self.offense_scale(player.body)
            for target in self._enemies(player):
                if target.entity in victims:
                    continue
                a, b = target.segment()
                delta = _sub(tuple((x + y) / 2 for x, y in zip(a, b, strict=True)), centre)
                distance = math.sqrt(_dot(delta, delta))
                if _dot(facing, delta) + EPSILON < threshold * distance:
                    continue
                body = (segment_distance(centre, a, b) <= radius + target.radius
                        and self.combat_visible(centre, a, b, target.radius, exclude=player))
                if not body and (scaled <= 0.0 or not self._melee_bubble(player, centre, target, volumes)):
                    continue
                victims.add(target.entity)
                if interception.blocks_melee(target, facing):
                    continue
                if body:
                    self._damage(player, target, amount, False,
                                 loadout.guid if isinstance(loadout, expr.Asset) else None,
                                 command.frame, ultimate_damage=True)
                else:
                    self.zarya_barriers.intercept(target.owner, scaled, player.body)

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
            zarya_melee = script.hero == zarya.HERO and instance.graph.index == 0x0043
            if zarya_melee:
                ability = instance.states.get(instance.graph.node(14).state)
                if isinstance(ability, nodes.Ability) and ability.cur > 0 and ability.cooldown_rate > 0:
                    ready_frame = (ability.last + ability.cooldown_ms()) // script.frame_ms
                    elapsed = (ready_frame - command.frame) * script.frame_ms
                    self.zarya_melee_ready[player.body] = self.match.tick + math.ceil(elapsed / (TICK * 1000))
            modify = state.node.field("m_modifyHealth") or {}
            amount = _number(state.evaluate(modify.get("m_amount"), watch=False), 0.0) if modify else 0.0
            loadout = (
                state.evaluate(modify.get("m_loadout"), watch=False) if modify.get("m_loadout") else None
            )
            loadout = loadout.guid if isinstance(loadout, expr.Asset) else None
            eye = self._eye(player)
            facing = aim(*command.aim())
            view_tick = command.frame - command.view_delay(player.quanta) * 1e6 / player.quanta
            contact = CommandContact(player, player.body, command.frame, eye, facing, view_tick,
                                     reach, radius, amount, loadout, melee=True)
            if zarya_melee:
                self._command_contact(contact)
            else:
                self._melee_contact(contact)

    def _melee_contact(self, contact: CommandContact, *, tick=None) -> None:
        player = contact.shooter
        eye, facing = contact.origin, contact.direction
        radius, amount = contact.radius, contact.amount
        centre = _along(eye, facing, contact.reach)
        volumes = ((centre, centre, radius),)
        scaled = amount * self.offense_scale(contact.body)
        self.reinhardt_barriers.melee(player, volumes, scaled, eye=eye, tick=tick)
        for target in self._enemies(player):
            if tick is not None and self.replacement_ticks.get(target.entity, 0) > tick:
                continue
            shown = self.seen(target, contact.view_tick)
            if shown is None:
                continue
            a, b = shown.segment()
            if (segment_distance(centre, a, b) - shown.radius <= radius and amount > 0.0
                    and self.combat_visible(eye, a, b, shown.radius, exclude=player, tick=tick)):
                if interception.blocks_melee(target, facing, tick=tick):
                    continue
                if self._damage(player, target, amount, False, contact.loadout, contact.frame, world_tick=tick):
                    assassin.interrupt_pin(self, target)
                continue
            if (scaled > 0.0 and self._melee_bubble(player, eye, shown, volumes, tick=tick)
                    and not interception.blocks_melee(target, facing, tick=tick)):
                self.zarya_barriers.intercept(target.owner, scaled, contact.body, tick=tick)

    def _melee_bubble(self, player, eye, shown, volumes, *, tick=None):
        """Check visible barrier contact against the attack's capsule volumes."""
        if phase.protection(shown.owner, tick=tick) is not None:
            return False
        x, y, z = shown.feet
        bubble = (x, y + shown.height / 2, z)
        for a, b, radius in volumes:
            delta = _sub(_segment_point(bubble, a, b), bubble)
            distance = math.hypot(*delta)
            if distance > radius + zarya.BUBBLE_RADIUS:
                continue
            contact = _along(bubble, delta, zarya.BUBBLE_RADIUS / max(distance, zarya.BUBBLE_RADIUS))
            if self.combat_visible(eye, contact, contact, 0.0, exclude=player, tick=tick):
                return self.zarya_barriers.protects(shown.owner, tick=tick)
        return False

    def _hammer(self, player, command, script) -> None:
        """Reconstructed 0048 target search; authored states own swing timing and damage.

        Endpoints/radii are evaluated from the two native queries per swing. Local
        query space is mapped through command aim about the feet. Exact native
        sweep/filter/knockback behavior and defense services remain unaccepted.
        """
        if script.hero != reinhardt_barrier.HERO or command is None:
            return
        forward = aim(*command.aim())
        yaw = command.aim()[0] * 2 * math.pi / 65536
        right = (math.cos(yaw), 0.0, -math.sin(yaw))
        up = (forward[1] * right[2], forward[2] * right[0] - forward[0] * right[2],
              -forward[1] * right[0])
        feet = tuple(player.mover.position)
        eye = self._eye(player)

        def position(value):
            return tuple(feet[k] + right[k] * value.x + up[k] * value.y + forward[k] * value.z
                         for k in range(3))

        view_tick = command.frame - command.view_delay(player.quanta) * 1e6 / player.quanta
        for instance in script.component.instances.values():
            if instance.graph.index != HAMMER:
                continue
            records = self.hammer_hits.setdefault(script.entity, {})
            for index, queries in HAMMER_TARGETS.items():
                state = instance.states.get(index)
                key = (instance.id, index)
                if state is None or not state.active:
                    records.pop(key, None)
                    continue
                counter, hit = records.get(key, (state.counter, set()))
                if counter != state.counter:
                    hit = set()
                records[key] = (state.counter, hit)
                modify = state.node.field("m_modifyHealth") or {}
                amount = _number(state.evaluate(modify.get("m_amount"), watch=False), 0.0)
                loadout = state.evaluate(modify.get("m_loadout"), watch=False)
                loadout = loadout.guid if isinstance(loadout, expr.Asset) else None
                volumes = []
                for query in queries:
                    tracker = instance.states.get(query)
                    if tracker is None or not tracker.active:
                        continue
                    a, b = (tracker.evaluate(tracker.node.field(name), watch=False)
                            for name in ("m_05BE62DD", "m_B3A77CD0"))
                    radius = _number(tracker.evaluate(tracker.node.field("m_radius"), watch=False), 0.0)
                    if isinstance(a, expr.Vec3) and isinstance(b, expr.Vec3) and radius > 0.0:
                        volumes.append((position(a), position(b), radius))
                scaled = amount * self.offense_scale(player.body)
                self.reinhardt_barriers.melee(player, volumes, scaled, hit)
                for target in self._enemies(player):
                    if ("body", target.entity) in hit:
                        continue
                    shown = self.seen(target, view_tick)
                    if shown is None:
                        continue
                    c, d = shown.segment()
                    body = (any(segment_pair_distance(a, b, c, d) <= radius + shown.radius
                                for a, b, radius in volumes)
                            and self.combat_visible(eye, c, d, shown.radius, exclude=player))
                    if not body and (scaled <= 0.0 or not self._melee_bubble(player, eye, shown, volumes)):
                        continue
                    hit.add(("body", target.entity))
                    incoming = _sub(shown.feet, feet)
                    distance = math.hypot(*incoming)
                    direction = tuple(axis / distance for axis in incoming) if distance else forward
                    if interception.blocks_melee(target, direction):
                        continue
                    if not body:
                        self.zarya_barriers.intercept(target.owner, scaled, player.body)
                    elif self._damage(player, target, amount, False, loadout, command.frame):
                        assassin.interrupt_pin(self, target)

    def _advance_idle_zarya_reloads(self) -> None:
        if self.match.ended:
            return
        for owner in self.match.players:
            script = owner.body_script
            if (script is None or script.hero != zarya.HERO or script.retired
                    or owner.gone or not owner.has_body or self.is_dead(owner)):
                continue
            weapon = next((i for i in script.component.instances.values() if i.graph.index == 0x0241), None)
            if weapon is None:
                continue
            for index in (46, 12):  # Refill before recovery if a world update crosses both deadlines.
                wait = weapon.states.get(index)
                deadline = getattr(wait, "world_deadline", None)
                if deadline is None or not wait.active or self.tick <= deadline:
                    continue
                # The deadline frame remains available to real input.
                # An idle completion must not spend a press buffered before recovery.
                for button_index in (5, 16):
                    button = weapon.states.get(button_index)
                    if isinstance(button, nodes.LogicalButton):
                        button.pressed_at = None
                frame = (wait.start + wait.ms) // script.frame_ms
                owner._advance_body_gap(frame + 1)
                if owner.body_script is not script:
                    break
                if not wait.active:
                    continue
                try:
                    script.finish_idle_reload(wait)
                    self.command(owner, None)
                    owner.sync_movement_mods(script.frame)
                except Exception:
                    log.exception("[game] %s: %s's idle reload failed", self.match.label(), owner.name)
                    self.match.stop_body_script(owner, failed=True)
                    break

    def _advance_zarya_energy(self, tick: int) -> None:
        if self.match.ended:
            return
        for player in self.match.players:
            script = player.body_script
            if (script is None or script.hero != zarya.HERO or script.retired
                    or player.gone or not player.has_body or self.is_dead(player)):
                continue
            try:
                zarya.advance_energy(script, tick)
            except Exception:
                log.exception("[game] %s: %s's idle energy failed", self.match.label(), player.name)
                self.match.stop_body_script(player, failed=True)

    def update(self, now: float, tick: int, frames: bool = True) -> None:
        """Each tick: keep where the bodies are, fly the rockets, respawn, send health that changed. The
        other players' body frames only on the ticks that send (`frames`)."""
        if self.match.collision_world is None:
            return
        previous_tick = self.tick
        retained = max(previous_tick + 1, tick - COMBAT_CATCH_UP + 1)
        first = tick
        if self.tick and tick > self.tick + 1 and (
            self.rifle_doses or self.shatters or self.zarya_gravitons.fields or any(
                bot.firing and not bot.dead and isinstance(bot.body_script, BotScript)
                for bot in self.match.bots
            )
        ):
            first = retained
        for target in self._targets():
            history = self.history.setdefault(target.entity, deque(maxlen=HISTORY))
            history.append((tick, target.feet, target.height, target.eye))
        # Replay the retained combat frames using the most recent poses. Player input
        # accepted just before the scheduler caught up joins only at the endpoint.
        ticks = (set(range(first, tick + 1)) | winston_barrier.clock_edges(self, now, tick)
                 | self.winston_primal.clock_edges(tick) | self.zarya_barriers.clock_edges(tick))
        defense_expiries = tuple(interception.timed_expiries(self))
        defense_admissions = interception.admission_edges(self, tick)
        protection_edges = phase.clock_edges(self.match.players, self.tick, tick)
        protection_edges.update(defense_admissions)
        protection_edges.update(self.zarya_barriers.start_edges(tick))
        protection_edges.update(sigma_barrier.phase_edges(self, tick))
        protection_edges.update(self.reinhardt_barriers.request_edges(tick))
        protection_edges.update(self.grasp_health.grant_edges(tick))
        protection_edges.update(due for due, _, _ in self.reinhardt_barriers.orisa_pending
                                if self.tick < due <= tick)
        if self.match.ended:
            self.pending_nano.clear()
        protection_edges.update(at for at in self.pending_nano if self.tick < at <= tick)
        protection_edges.update(at for at in self.pending_contacts if self.tick < at <= tick)
        halt_ticks = orisa_halt.clock_ticks(self, tick)
        protection_edges.update(edge for _, _, _, end in defense_expiries
                                for edge in (end, end + 1, end + 2) if self.tick < edge <= tick)
        if self.rifle_doses:
            # Preserve pending doses' original status and barrier windows across long gaps.
            dose_end = max(dose.start_tick for dose in self.rifle_doses) + RIFLE_DOSE_TICKS
            ticks.update(range(self.tick + 1, min(first, dose_end + 1)))
        scheduled_flights = [
            (rocket, range(max(self.tick, rocket.last_tick) + 1,
                           min(tick, rocket.last_tick + math.ceil(rocket.left / TICK)) + 1))
            for rocket in self.rockets
            if rocket.last_tick is not None
            and (rocket.orisa_launch is not None
                 or _world_timed_flight(rocket.definition, rocket.spawn, rocket.direct, rocket.blast))
        ]
        flight_ticks = {at for _, flight in scheduled_flights for at in flight}
        graviton_flights = [flight for rocket, flight in scheduled_flights
                            if flight and rocket.spawn == graviton.FIELD_ENTITY]
        # Graviton damage, pull and death must interleave even before the retained frames.
        field_ticks = self.zarya_gravitons.clock_ticks(self.tick, tick, graviton_flights)
        # A rifle projectile can start its dose during the same catch-up.
        rifle_followups = {
            at for rocket, flight in scheduled_flights
            if flight and rocket.direct is not None and rocket.direct.biotic_rifle
            for at in range(flight[0] + 1, min(tick, flight[-1] + RIFLE_DOSE_TICKS) + 1)
        }
        # A shield can land and become due before the current update ends.
        deploy_delay = orisa_barrier.deployment_delay_ticks()
        deploy_followups = {
            at + deploy_delay for rocket, flight in scheduled_flights if rocket.orisa_launch is not None
            for at in flight if at + deploy_delay <= tick
        }
        for at in sorted(ticks.union(flight_ticks, field_ticks, rifle_followups,
                                     protection_edges, halt_ticks, deploy_followups)):
            if at not in ticks and at not in protection_edges:
                halt_live = at in halt_ticks and (self.halt_pulls or any(
                    rocket.halt_entity is not None for rocket in self.rockets
                ))
                flight_live = at in flight_ticks and any(
                    at in flight and rocket in self.rockets for rocket, flight in scheduled_flights
                )
                field_live = at in field_ticks and any(
                    field.last_tick < at <= field.end_tick for field in self.zarya_gravitons.fields
                )
                rifle_live = at in rifle_followups and any(
                    dose.start_tick < at <= dose.start_tick + RIFLE_DOSE_TICKS for dose in self.rifle_doses
                )
                deploy_live = at in deploy_followups and any(
                    due == at for due, _, _ in self.reinhardt_barriers.orisa_pending
                )
                if not (halt_live or flight_live or field_live or rifle_live or deploy_live):
                    continue
            phase.expire(self.match.players, at)
            if at > self.zarya_charge_tick:
                if self.zarya_charge_tick:
                    self._charge_zarya_passive(self.zarya_charge_tick + 1, at)
                self.zarya_charge_tick = at
            self.now, self.tick = now - (tick - at) * TICK, at
            for body in sorted(defense_admissions.get(at, ())):
                if self.sleeps.get(body, 0) > at:
                    interception.reject_sleep_admissions(self, body, at)
            self._advance_zarya_energy(at)
            winston_barrier.advance_releases(self)
            sigma_barrier.advance_phase_changes(self)
            self.winston_primal.advance_expiries()
            self.zarya_barriers.advance_expiries()
            self.zarya_barriers.apply_cleanses()
            for body, targets, duration in self.pending_nano.pop(at, ()):
                caster = next((p for p in self.match.players if p.body == body and p.has_body and not p.gone), None)
                if caster is not None and not self.is_dead(caster):
                    self._apply_nano(caster, targets, duration)
            interception.advance_expiries(self, defense_expiries)
            if self.grasp_health.decays:
                self.grasp_health.update(at)
            for vitals in self.vitals.values():
                if vitals.nano_until and at >= vitals.nano_until:
                    vitals.nano_until = 0
            for rocket in list(self.rockets):
                if at == tick or rocket.last_tick is not None:
                    self._fly(rocket)
            if at in ticks:
                self._bot_weapons(at)
            self._advance_rifle_doses()
            self.zarya_gravitons.update(at)
            if at > previous_tick:
                orisa_halt.update(self, at)
            for wave in list(self.shatters.values()):
                if wave.shooter.gone or wave.shooter.body != wave.body or earthshatter.advance(self, wave):
                    self._finish_shatter(wave)
            # Keep the normal frame order: Orisa's landed pet deploys after this tick's contacts.
            orisa_barrier.advance_deployments(self.reinhardt_barriers, at)
            self.grasp_health.advance_grants()
            contacts = self.pending_contacts.pop(at, ())
            for index, contact in enumerate(contacts):
                self.reinhardt_barriers.advance_requests(index)
                try:
                    self._resolve_command_contact(contact)
                except Exception:
                    log.exception("[game] %s: %s's queued attack failed", self.match.label(), contact.shooter.name)
            self.reinhardt_barriers.advance_requests(len(contacts))
        self._advance_idle_zarya_reloads()
        interception.prune_defense_windows(self)
        self.grasp_health.update(tick)
        self.reinhardt_barriers.update()
        reinhardt_charge.sync(self)
        self.projectiles.update(tick, first_tick=retained)
        self.zarya_recovery.update(tick)
        # Recovery integrates skipped ticks and still needs the expired deadlines.
        for vitals in self.vitals.values():
            if vitals.healing_block_until and tick >= vitals.healing_block_until:
                vitals.healing_block_until = 0
            if vitals.healing_boost_until and tick >= vitals.healing_boost_until:
                vitals.healing_boost_until = 0
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
        for source, target in tuple(self.pins.items()):
            if self.match.ended or source not in current or target not in current:
                self.pins.pop(source, None)
        for control in (self.stuns, self.sleeps):
            for entity in [key for key, deadline in control.items() if deadline <= tick]:
                del control[entity]
        for table in (self.history, self.vitals, self.replacement_ticks, self.stuns, self.sleeps):
            for entity in [key for key in table if key not in current]:
                del table[entity]
        for entity, sources in tuple(self.genji_assists.items()):
            for body, (_, damaged) in tuple(sources.items()):
                if body not in current or tick - damaged > GENJI_ASSIST_TICKS:
                    del sources[body]
            if entity not in current or not sources:
                del self.genji_assists[entity]

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
                found = Vitals(target.entity, *pools.active_pools(
                    target.owner.hero.guid, target.owner.hero.body))
            self.vitals[target.entity] = found
        return found

    def _enemies(self, shooter, *, team: int | None = None) -> list[Target]:
        free_for_all = self.match.game_map.free_for_all
        source = _source_entity(shooter)
        team = shooter.team if team is None else team
        found = []
        for target in self._targets():
            if target.owner is shooter or target.entity == source:
                continue
            if not free_for_all and target.team == team:
                continue
            if self.vitals_of(target).dead:
                continue
            found.append(target)
        return found

    def cover_ray(self, origin, direction, length, *, exclude=None, cryo_radius=0.0, tick=None):
        """Static cover and active Cryo-Freeze pet geometry on either team."""
        best = ray_world(self._collision(), origin, direction, length)
        for player in self.match.players:
            if player is exclude or (capsule := phase.cryo_capsule(player, tick=tick)) is None:
                continue
            a, b, radius = capsule
            distance = ray_capsule(origin, direction, a, b, radius + cryo_radius)
            if distance is not None and distance <= length and (best is None or distance < best):
                best = distance
        return best

    def combat_visible(self, origin, a, b, radius, *, exclude=None, tick=None):
        return capsule_visible(
            None, origin, a, b, radius,
            cover=lambda o, d, reach: self.cover_ray(o, d, reach, exclude=exclude, tick=tick),
        )

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
                if controlled and bot.attack_action == "charge":
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
        if (state.instance.graph.index == 0x0241 and state.index == 19
                and not isinstance(shooter, bots.Bot) and shooter.hero.guid == zarya.HERO):
            self.zarya_secondary_ready[shooter.body] = self.match.tick + zarya.SECONDARY_RECOVERY_TICKS
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
            direction = mccree.fan_direction(shooter, state, direction, number, time, activation)
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
            rays = (direction,)
            distribution = node.field("m_5EC1EB38") or {}
            if (state.instance.graph.index == 0x0304 and distribution.get("$") == "STU_1145C42E"
                    and field("m_numProjectilesPerShot") == 3):
                # Match the Genji lane's reconstructed planar distribution.
                spread = math.radians(_number(state.evaluate(distribution.get("m_spread"), watch=False), 0.0))
                horizontal = math.hypot(direction[0], direction[2])
                right = ((direction[2] / horizontal, 0, -direction[0] / horizontal)
                         if horizontal > EPSILON else (1, 0, 0))
                rays = tuple(_along(tuple(axis * math.cos(offset) for axis in direction), right,
                                    math.sin(offset)) for offset in (-spread, 0, spread))
            elif npc and shooter.hero == 0x02E00000000001CE and state.instance.graph.index == 0x13EB:
                from ow174.game.pellets import ring_directions

                rays = ring_directions(state, direction)
            damaging = (direct is not None and direct.amount > 0.0) or (
                blast is not None and blast.amount > 0.0
            )
            for direction in rays:
                self._projectile(shooter, state, shot_frame, origin, direction, definition,
                                 speed, lifetime, direct, blast, damaging, npc, activation, snapshot)
            return
        if direct is None or direct.amount <= 0.0:
            return
        reach = field("m_FEC435C6", RANGE) or RANGE
        # The input supplies this simulation frame's aim and view time. Notices use the volley clock.
        view_tick = self.tick
        if command is not None:
            view_tick = command.frame - command.view_delay(shooter.quanta) * 1e6 / shooter.quanta
        for ray in dva.pellet_directions(shooter, state, direction):
            self._hitscan(shooter, origin, ray, reach, direct, view_tick, shot_frame,
                          world_tick=self.tick if npc else None)

    def _projectile(self, shooter, state, shot_frame, origin, direction, definition,
                    speed, lifetime, direct, blast, damaging, npc, activation, snapshot):
        """Each physical pellet owns its delivery lifecycle when its sync supports it."""
        node = state.node
        flash_gravity = None if npc else mccree.flashbang_gravity(shooter, state)
        if flash_gravity is not None:
            direction = mccree.flashbang_direction(state, direction)
        rock_motion = None if npc else accretion.accretion_launch(state, direction)
        if rock_motion is not None:
            rock_gravity, direction = rock_motion
        sphere = None if npc else hypersphere.hypersphere_motion(state)
        shot = None
        if isinstance(definition, expr.Asset):
            shot = self.projectiles.fired(
                shooter, state, shot_frame, definition.guid, lifetime, origin, direction,
                activation=activation, snapshot=snapshot,
            )
        halt = not npc and state.instance.graph.index == orisa_halt.GRAPH and node.pos == 8
        deploy = not npc and state.instance.graph.index == orisa_barrier.GRAPH
        if damaging or shot is not None or halt or deploy:
            offense = self.offense_scale(_source_entity(shooter))
            if offense != 1.0:
                if direct is not None and not direct.biotic_rifle:
                    direct = replace(direct, amount=direct.amount * offense)
                if blast is not None:
                    blast = replace(blast, amount=blast.amount * offense)
            spawned = projectiles._defs(node.field("m_49CBA3BC"))
            gravity = flash_gravity if flash_gravity is not None else 0.0
            body_radius = wall_radius = normal = tangent = 0.0
            sphere_bounces = 0
            if rock_motion is not None:
                gravity = rock_gravity
            elif sphere is not None:
                gravity, body_radius, wall_radius, sphere_bounces, normal, tangent = sphere
            grenade = isinstance(definition, expr.Asset) and definition.guid == ANA_GRENADE
            if grenade or deploy or state.instance.graph.index in (0x0241, 0x025F):
                for motion in node.field("m_projectileMotions") or ():
                    if motion.get("$") == "STU_E1A5B579":
                        gravity = _number(state.evaluate(motion.get("m_599AB8D7"), watch=False), 0.0)
            fire_strike = (state.instance.component.world.hero == reinhardt_barrier.HERO
                           and state.instance.graph.index == 0x00E2 and state.index == 0)
            radius = 0.0
            if fire_strike:
                hit_fields = node.field("m_6396149F") or {}
                radius = max(_number(state.evaluate(hit_fields.get(name), watch=False), 0.0)
                             for name in ("m_57DAE1B5", "m_61CD1F5B"))
            accelerated = grenade or any(value is not None for value in (flash_gravity, rock_motion, sphere))
            definition_guid = definition.guid if isinstance(definition, expr.Asset) else None
            spawn = spawned[0] if spawned else None
            last_tick = self.tick if npc else None
            if not npc and (deploy or _world_timed_flight(definition_guid, spawn, direct, blast)):
                # Anchor the first flight step to world admission, regardless of the command frame.
                last_tick = self.match.tick
            self.rockets.append(
                Rocket(
                    shooter, shooter.team, origin, direction, speed, lifetime, direct, blast, shot,
                    last_tick=last_tick, spawn=spawn, source_entity=_source_entity(shooter),
                    offense_scale=offense,
                    gravity=gravity,
                    definition=definition_guid,
                    velocity=tuple(speed * v for v in direction) if accelerated else None,
                    halt_instance=state.instance.id if halt else None,
                    orisa_launch=direction if deploy else None,
                    accretion_source=(shooter.body, state.instance.id) if rock_motion is not None else None,
                    detonate_on_expiry=flash_gravity is not None or sphere is not None,
                    sphere_radius=body_radius, sphere_wall_radius=wall_radius,
                    sphere_bounces=sphere_bounces, bounce_normal=normal, bounce_tangent=tangent,
                    penetration_radius=radius, hit_targets=set() if fire_strike else None,
                )
            )

            if halt:
                orisa_halt.bind(self, self.rockets[-1], state)
            if (not npc and state.instance.graph.index == 0x025F
                    and spawn == graviton.FIELD_ENTITY and shooter.hero.guid == zarya.HERO):
                script = shooter.body_script
                ready_frame = zarya.ultimate_charge_ready_frame(script)
                if ready_frame:
                    elapsed = max(0, ready_frame - script.frame) * script.frame_ms
                    self.zarya_charge_ready[shooter.body] = self.match.tick + math.ceil(elapsed / (TICK * 1000))

    def _hitscan(self, shooter, origin, direction, reach, direct, view_tick, shot_frame,
                 *, world_tick=None) -> None:
        """Resolve each ray independently against bodies and hostile barriers."""
        best = None
        targets = self._enemies(shooter)
        if direct.biotic_rifle:
            targets.extend(self._rifle_allies(shooter, shooter.team))
        for target in targets:
            if phase.protection(target.owner) is not None:
                continue
            shown = self.seen(target, view_tick)
            if shown is None:
                continue
            a, b = shown.segment()
            t = ray_capsule(origin, direction, a, b, shown.radius)
            edge = None
            if self.match.game_map.free_for_all or target.team != shooter.team:
                limit = min(reach, t if t is not None else reach, best[0] if best else reach)
                edge = self._bubble_distance(shown, origin, direction, limit, tick=world_tick)
                if edge is not None:
                    t = edge
            if t is not None and t <= reach and (best is None or t < best[0]):
                best = (t, target, shown, edge is not None)
        limit = best[0] if best is not None else reach
        barrier = self.reinhardt_barriers.trace(shooter, origin, direction, limit)
        if best is None and barrier is None:
            return
        wall = self.cover_ray(origin, direction, limit, exclude=shooter)
        if barrier is not None and (wall is None or barrier[0] < wall):
            distance, owner = barrier
            self.reinhardt_barriers.damage(owner, direct.amount * direct.scale(distance)
                                          * self.offense_scale(_source_entity(shooter)))
            return
        if best is None or (wall is not None and wall < limit):
            return
        t, target, shown, bubble = best
        if bubble:
            source = _source_entity(shooter)
            amount = direct.amount * direct.scale(t) * self.offense_scale(source)
            self.zarya_barriers.intercept(target.owner, amount, source, tick=world_tick)
            return
        hit_y = origin[1] + direction[1] * t
        critical = (hit_y >= shown.feet[1] + shown.eye - HEAD_BELOW
                    and defenses.fortify_state(target.owner) is None)
        amount = direct.amount * direct.scale(t) * (direct.critical if critical else 1.0)
        if direct.biotic_rifle:
            source = _source_entity(shooter)
            self._admit_rifle(shooter, source, shooter.team, target, direct, amount, shot_frame, tick=world_tick)
            return
        self._damage(
            shooter, target, amount, critical and direct.critical > 1.0, direct.loadout, shot_frame,
            world_tick=world_tick,
            effect=direct.effect,
        )

    def _fly(self, rocket: Rocket) -> None:
        """Sweep elapsed world time against the current bodies and static collision."""
        if getattr(rocket.shooter, "gone", False) or self.match.ended:
            if rocket in self.rockets:
                self.rockets.remove(rocket)
            if rocket.shot is not None:
                self.projectiles._end(rocket.shot, self.tick, False)
            return
        if rocket.halt_entity is not None:
            orisa_halt.fly(self, rocket)
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
                self._fly_for(rocket, min(TICK, rocket.left), 8)
            return
        duration = min(elapsed * TICK, rocket.left)
        if duration <= 0.0:
            self._expire(rocket)
            return
        self._fly_for(rocket, duration, 8)

    def _fly_for(self, rocket: Rocket, duration: float, bounces: int) -> None:
        """Use the remaining elapsed interval after a reconstructed physical reflection."""
        if duration <= EPSILON:
            if rocket.left <= EPSILON:
                self._expire(rocket)
            return
        if bounces <= 0:
            # Bounded host policy for coincident/opposing defense volumes.
            self.rockets.remove(rocket)
            if rocket.shot is not None:
                self.projectiles._end(rocket.shot, self.tick, False)
            return
        # Sweep the remaining flight even when the range or lifetime ends inside this tick.
        velocity = None
        if rocket.velocity is not None:
            # Fixed-step velocity-before-sweep ordering is a host policy.
            velocity = mccree.gravity_velocity(rocket.velocity, rocket.gravity, duration)
            displacement = [v * duration for v in velocity]
        else:
            displacement = [v * rocket.speed * duration for v in rocket.direction]
            displacement[1] -= 0.5 * rocket.gravity * duration * duration
        step = math.sqrt(sum(v * v for v in displacement))
        if step <= EPSILON:
            if velocity is not None:
                rocket.velocity = velocity
            else:
                rocket.direction = (rocket.direction[0],
                                    rocket.direction[1] - rocket.gravity * duration / rocket.speed,
                                    rocket.direction[2])
            rocket.flown += duration
            rocket.left -= duration
            if rocket.left <= 0.0:
                self._expire(rocket)
            return
        direction = tuple(v / step for v in displacement)
        if rocket.orisa_launch is not None:
            hit = self._collision().cast_ray(rocket.position, direction, step)
            if hit is not None:
                rocket.flown += duration * hit.distance / step
                self._landed(rocket, hit.point)
                orisa_barrier.land(self, rocket, hit)
                return
            rocket.position = _along(rocket.position, direction, step)
            rocket.direction = (rocket.direction[0],
                                rocket.direction[1] - rocket.gravity * duration / rocket.speed,
                                rocket.direction[2])
            rocket.flown += duration
            rocket.left -= duration
            if rocket.left <= 0:
                self._expire(rocket)
            return
        best = None
        impact_owner = None
        wall_normal = None
        micro_missile = rocket.definition == dva.MICRO_MISSILE
        if rocket.hit_targets is not None:
            radius = rocket.penetration_radius
            wall = self.cover_ray(rocket.position, direction, step,
                                  exclude=rocket.shooter, cryo_radius=radius, tick=self.tick)
            end = _along(rocket.position, direction, step)
            hit = contacts.scene_sweep(self._collision().engine, rocket.position, end,
                                       ((0.0, 0.0, 0.0),), radius, MOVER)
            if hit is not None and (wall is None or hit[0] * step < wall):
                wall = hit[0] * step
            if wall is not None:
                best = (wall, None, False)
            limit = wall if wall is not None else step
        else:
            targets = self._enemies(rocket.shooter, team=rocket.team)
            rifle = rocket.direct is not None and rocket.direct.biotic_rifle
            grenade = rocket.definition == ANA_GRENADE
            bubble_collision = (rocket.definition == zarya.PARTICLE_GRENADE
                                or grenade or rifle or micro_missile
                                or rocket.definition in (ENFORCER_PELLET, GENJI_SHURIKEN, SNIPER_ROUND,
                                                         dva.LIGHT_GUN_PROJECTILE)
                                or rocket.spawn == graviton.FIELD_ENTITY
                                or rocket.accretion_source is not None
                                or rocket.sphere_radius > 0.0
                                or (rocket.blast is not None and rocket.blast.effect == FLASHBANG_EFFECT)
                                or (rocket.direct is not None and rocket.direct.effect == SLEEP_DART_EFFECT))
            if grenade:
                targets.extend(self._biotic_allies(rocket, include_self=False))
            elif rifle:
                targets.extend(self._rifle_allies(rocket.shooter, rocket.team))
            for target in targets:
                if (self.replacement_ticks.get(target.entity, 0) > self.tick
                        or phase.protection(target.owner, tick=self.tick) is not None):
                    continue
                a, b = target.segment()
                t = ray_capsule(rocket.position, direction, a, b, target.radius + rocket.sphere_radius)
                edge = None
                if bubble_collision and (self.match.game_map.free_for_all or target.team != rocket.team):
                    limit = min(step, t if t is not None else step, best[0] if best else step)
                    edge = self._bubble_distance(target, rocket.position, direction,
                                                 limit, rocket.sphere_radius, tick=self.tick)
                    if edge is not None:
                        t = edge
                if t is not None and t <= step and (best is None or t < best[0]):
                    best = (t, target, edge is not None)
            wall = self.cover_ray(rocket.position, direction, step, exclude=rocket.shooter, tick=self.tick)
            if rocket.sphere_wall_radius:
                end = _along(rocket.position, direction, step)
                hit = contacts.scene_sweep(self._collision().engine, rocket.position, end,
                                           ((0.0, 0.0, 0.0),), rocket.sphere_wall_radius, MOVER)
                if hit is not None and _dot(direction, hit[2]) < -EPSILON:
                    distance = hit[0] * step
                    if wall is None or distance <= wall:
                        wall, wall_normal = distance, hit[2]
            if wall is not None and (best is None or wall < best[0]):
                best = (wall, None, False)
            limit = best[0] if best is not None else step
            barrier = self.reinhardt_barriers.trace(
                rocket.shooter, rocket.position, direction, limit, team=rocket.team,
            )
            if barrier is not None and (best is None or barrier[0] < limit):
                limit, impact_owner = barrier
                best = (limit, None, False)
        reflected = interception.reflection(
            self, rocket.shooter, rocket.definition, rocket.position, direction,
            limit, team=rocket.team, radius=rocket.penetration_radius,
        )
        bound = reflected[0] if reflected is not None else limit
        absorbed = interception.absorption(
            self, rocket.shooter, rocket.definition, rocket.position, direction,
            bound, team=rocket.team, radius=rocket.penetration_radius,
        )
        direct_scale = (rocket.offense_scale
                        if rocket.direct is not None and rocket.direct.biotic_rifle else 1.0)
        if rocket.hit_targets is not None:
            stop = absorbed[0] if absorbed is not None else bound
            stopped = best is not None or reflected is not None or absorbed is not None
            self._fire_strike_contacts(rocket, direction, stop, stops=stopped)
        if absorbed is not None:
            distance, state = absorbed
            rocket.distance += distance
            rocket.flown += duration * distance / step
            self.rockets.remove(rocket)
            if rocket.shot is not None:
                self.projectiles._end(rocket.shot, self.tick, False)
            amount = (rocket.blast.amount if rocket.blast is not None else 0.0)
            if rocket.direct is not None:
                amount += rocket.direct.amount * direct_scale
            interception.absorbed(state, amount, fight=self)
            return
        if reflected is not None:
            distance, state, target = reflected
            rocket.distance += distance
            travelled = duration * distance / step
            rocket.flown += travelled
            rocket.left -= travelled
            if rocket.shot is not None:
                self.projectiles._end(rocket.shot, self.tick, False)
                rocket.shot = None
            point = _along(rocket.position, direction, distance)
            rocket.shooter, rocket.team, rocket.source_entity = target.owner, target.team, target.entity
            rocket.direction = aim(target.owner.mover.yaw, target.owner.mover.pitch)
            if rocket.velocity is not None:
                contact_velocity = mccree.gravity_velocity(rocket.velocity, rocket.gravity, travelled)
                speed = math.sqrt(sum(v * v for v in contact_velocity))
                rocket.velocity = tuple(v * speed for v in rocket.direction)
            rocket.position = _along(point, rocket.direction, 1e-4)
            amount = ((rocket.direct.amount * direct_scale if rocket.direct is not None else 0.0)
                      + (rocket.blast.amount if rocket.blast is not None else 0.0))
            interception.absorbed(state, amount)
            self._fly_for(rocket, duration - travelled, bounces - 1)
            return
        if best is None:
            rocket.distance += step
            rocket.position = _along(rocket.position, direction, step)
            if velocity is not None:
                rocket.velocity = velocity
                rocket.direction = direction
            else:
                rocket.direction = (rocket.direction[0],
                                    rocket.direction[1] - rocket.gravity * duration / rocket.speed,
                                    rocket.direction[2])
            rocket.flown += duration
            rocket.left -= duration
            if rocket.left <= 0.0:
                self._expire(rocket)
            return
        t, struck, bubble = best
        rocket.distance += t
        rocket.flown += duration * t / step
        point = _along(rocket.position, direction, t)
        if struck is None and impact_owner is None and wall_normal is not None and rocket.sphere_bounces:
            travelled = duration * t / step
            projection = _dot(velocity, wall_normal)
            rocket.velocity = tuple(
                expr.f32((axis - projection * normal) * rocket.bounce_tangent
                         - projection * normal * rocket.bounce_normal)
                for axis, normal in zip(velocity, wall_normal, strict=True)
            )
            rocket.position = _along(point, wall_normal, 0.001)
            rocket.sphere_bounces -= 1
            rocket.left -= travelled
            self._fly_for(rocket, duration - travelled, bounces - 1)
            return
        if struck is None:
            # Reconstruction: keep explosions/fields on the incoming side of a
            # struck face so visibility does not start exactly inside the wall.
            point = _along(point, direction, -0.01)
        self._landed(rocket, point)
        if impact_owner is not None:
            amount = rocket.blast.amount if rocket.blast is not None else 0.0
            if rocket.direct is not None:
                amount += rocket.direct.amount * rocket.direct.scale(rocket.distance) * direct_scale
            self.reinhardt_barriers.damage(impact_owner, amount)
        pushed = None
        combine_contact = ((rocket.sphere_radius or rocket.accretion_source is not None or micro_missile)
                           and rocket.direct is not None and rocket.blast is not None)
        if struck is not None and (bubble or combine_contact):
            combined = rocket.blast.amount if rocket.blast is not None else 0.0
            if rocket.direct is not None:
                combined += rocket.direct.amount * rocket.direct.scale(rocket.distance) * direct_scale
            if self.zarya_barriers.intercept(struck.owner, combined, rocket.source_entity, tick=self.tick):
                self._explode(rocket, point, excluded=struck.entity)
                return
        if struck is not None and rocket.direct is not None and rocket.direct.biotic_rifle:
            direct = rocket.direct
            self._admit_rifle(rocket.shooter, rocket.source_entity, rocket.team, struck, direct,
                              direct.amount * direct.scale(rocket.distance), self.tick,
                              offense=rocket.offense_scale, tick=self.tick)
        elif struck is not None and rocket.direct is not None and rocket.direct.amount > 0.0:
            before = self.vitals_of(struck).total if rocket.accretion_source is not None else None
            critical = (rocket.definition == dva.LIGHT_GUN_PROJECTILE and rocket.direct.critical > 1.0
                        and point[1] >= struck.feet[1] + struck.eye - HEAD_BELOW
                        and defenses.fortify_state(struck.owner) is None)
            amount = rocket.direct.amount * rocket.direct.scale(rocket.distance)
            if critical:
                amount *= rocket.direct.critical
            admitted = self._damage(
                rocket.shooter, struck, amount, critical, rocket.direct.loadout, self.tick,
                world_tick=self.tick,
                source_entity=rocket.source_entity,
                effect=rocket.direct.effect,
                ultimate_damage=rocket.spawn == graviton.FIELD_ENTITY,
                source_scaled=True,
            )
            if admitted and before is not None:
                accretion.accretion_hit(self, rocket, struck, before - self.vitals_of(struck).total)
                if not self.vitals_of(struck).dead:
                    self._sigma_impulse(struck, rocket.direct, (direction[0], direction[2]), 0.0)
                    pushed = struck.entity
        self._explode(rocket, point, impact_owner=impact_owner, pushed=pushed)

    def _fire_strike_contacts(self, rocket, direction, limit, *, stops):
        """Apply Fire Strike once per body or raised shield lifetime along this segment."""
        radius = rocket.penetration_radius
        targets = []
        barriers = self.reinhardt_barriers
        for owner in barriers.enemies(rocket.shooter, team=rocket.team):
            if isinstance(owner, (orisa_barrier.Barrier, winston_barrier.Bubble)):
                key = (-owner.entity, None)
            elif isinstance(owner, sigma_barrier.Barrier):
                key = (-owner.entity, owner.instance.states[4].counter)
            else:
                key = (-owner.body, barriers.states[owner.body].request[1])
            if key in rocket.hit_targets:
                continue
            distance = barriers.intersect(owner, rocket.position, direction, limit, radius=radius)
            if distance is not None and (not stops or distance < limit):
                targets.append((distance, key, owner, False))
        for target in self._enemies(rocket.shooter, team=rocket.team):
            birth = self.replacement_ticks.get(target.entity)
            key = (target.entity, birth)
            if ((birth is not None and birth > self.tick) or key in rocket.hit_targets
                    or phase.protection(target.owner, tick=self.tick) is not None):
                continue
            edge = self._bubble_distance(target, rocket.position, direction, limit, radius, tick=self.tick)
            if edge is not None and (edge < limit or not stops):
                targets.append((edge, key, target, True))
            a, b = target.segment()
            distance = ray_capsule(rocket.position, direction, a, b, target.radius + radius)
            if distance is not None and (distance < limit or (distance == limit and not stops)):
                targets.append((distance, key, target, False))
        for distance, key, target, bubble in sorted(targets, key=lambda row: (row[0], row[1][0])):
            if key in rocket.hit_targets:
                continue
            amount = rocket.direct.amount * rocket.direct.scale(rocket.distance + distance)
            if bubble:
                if not self.zarya_barriers.intercept(
                    target.owner, amount, rocket.source_entity, tick=self.tick,
                ):
                    continue
            elif key[0] < 0:
                barriers.damage(target, amount)
            else:
                self._damage(rocket.shooter, target, amount, False, rocket.direct.loadout, self.tick,
                             world_tick=self.tick, source_entity=rocket.source_entity,
                             effect=rocket.direct.effect, source_scaled=True)
            rocket.hit_targets.add(key)

    def _expire(self, rocket: Rocket) -> None:
        self._landed(rocket)
        if rocket.detonate_on_expiry:
            self._explode(rocket, rocket.position)

    def _explode(self, rocket: Rocket, point: tuple, *,
                 impact_owner=None, pushed=None, excluded=None) -> None:
        blast = rocket.blast
        if blast is None or blast.amount <= 0.0:
            return
        cover = self.reinhardt_barriers.splash(rocket.shooter, point, blast,
                                              team=rocket.team, impact_owner=impact_owner)
        targets = self._enemies(rocket.shooter, team=rocket.team)
        biotic = rocket.definition == ANA_GRENADE
        bubble_splash = (biotic or rocket.definition == zarya.PARTICLE_GRENADE
                         or blast.effect == FLASHBANG_EFFECT or rocket.sphere_radius > 0.0
                         or rocket.accretion_source is not None or rocket.definition == dva.MICRO_MISSILE)
        allies = self._biotic_allies(rocket, include_self=True) if biotic else ()
        targets.extend(allies)
        allied_bodies = {target.entity for target in allies}
        hero = getattr(getattr(rocket.shooter, "hero", None), "guid", None)
        if (blast.self_scale > 0 and hero == zarya.HERO
                and _source_entity(rocket.shooter) == rocket.source_entity):
            targets.extend(t for t in self._targets()
                           if t.owner is rocket.shooter and t.entity == rocket.source_entity)
        for target in targets:
            if target.entity == excluded or self.replacement_ticks.get(target.entity, 0) > self.tick:
                continue
            a, b = target.segment()
            distance = max(0.0, segment_distance(point, a, b) - target.radius)
            scalar = blast.splash(distance)
            if scalar <= 0.0:
                hostile = target.owner is not rocket.shooter and target.entity not in allied_bodies
                if bubble_splash and hostile:
                    self._bubble_splash(rocket, point, target, cover)
                continue
            if (self.combat_visible(point, a, b, target.radius, exclude=rocket.shooter, tick=self.tick)
                    and not self.reinhardt_barriers.covers(point, a, b, target.radius, cover)):
                own = target.owner is rocket.shooter
                if target.entity in allied_bodies:
                    if phase.protection(target.owner, tick=self.tick) is None:
                        self._heal(rocket.shooter, target, 100 * scalar)
                        self.vitals_of(target).healing_boost_until = self.tick + BIOTIC_TICKS
                    continue
                amount = blast.amount * scalar * (blast.self_scale if own else 1.0)
                if own:
                    amount /= rocket.offense_scale
                admitted = self._damage(
                    rocket.shooter, target, amount, False, blast.loadout, self.tick,
                    world_tick=self.tick,
                    source_entity=rocket.source_entity,
                    effect=blast.effect,
                    ultimate_damage=rocket.spawn == graviton.FIELD_ENTITY,
                    source_scaled=True,
                )
                if admitted and not self.vitals_of(target).dead:
                    if biotic:
                        self.vitals_of(target).healing_block_until = self.tick + BIOTIC_TICKS
                    if rocket.accretion_source is None and not rocket.sphere_radius:
                        self.radial_impulse(target, point, blast.horizontal_impulse, blast.vertical_impulse,
                                            scale=blast.knockback_scale(distance, own), tick=self.tick)
                    elif target.entity != pushed:
                        heading = (target.feet[0] - point[0], target.feet[2] - point[2])
                        self._sigma_impulse(target, blast, heading, distance)

    def _bubble_splash(self, rocket: Rocket, point: tuple, target: Target, cover) -> None:
        """Apply splash that reaches a hostile bubble outside its recipient's blast reach."""
        x, y, z = target.feet
        centre = (x, y + target.height / 2, z)
        radius = zarya.BUBBLE_RADIUS
        scalar = rocket.blast.splash(max(0.0, math.dist(point, centre) - radius))
        if (scalar > 0.0 and phase.protection(target.owner, tick=self.tick) is None
                and self.zarya_barriers.protects(target.owner, tick=self.tick)
                and self.combat_visible(point, centre, centre, radius, exclude=rocket.shooter, tick=self.tick)
                and not self.reinhardt_barriers.covers(point, centre, centre, radius, cover)):
            self.zarya_barriers.intercept(target.owner, rocket.blast.amount * scalar,
                                          rocket.source_entity, tick=self.tick)

    def _rifle_allies(self, shooter, team: int) -> list[Target]:
        if self.match.game_map.free_for_all:
            return []
        result = []
        for target in self._targets():
            if target.owner is shooter or target.team != team:
                continue
            vitals = self.vitals_of(target)
            if not vitals.dead and sum(vitals.now.values()) < sum(vitals.most.values()) - EPSILON:
                result.append(target)
        return result

    def _admit_rifle(
        self, shooter, source, team, target, direct, amount, frame, *, offense=None, tick=None,
    ) -> None:
        healing = not self.match.game_map.free_for_all and target.team == team
        if not healing:
            amount *= self.offense_scale(source) if offense is None else offense
        if not healing and self.zarya_barriers.intercept(target.owner, amount, source, tick=tick):
            return
        start_tick = self.match.tick if tick is None else tick
        self.rifle_doses.append(RifleDose(shooter, source, team, target.entity, start_tick,
                                         amount, healing, direct.loadout, frame))

    def _advance_rifle_doses(self) -> None:
        if not self.rifle_doses:
            return
        targets = {target.entity: target for target in self._targets()}
        pending = []
        free_for_all = self.match.game_map.free_for_all
        for dose in self.rifle_doses:
            target = targets.get(dose.target)
            if target is None or self.vitals_of(target).dead or self.match.ended:
                continue
            # Keep the firing team. A recipient change ends the dose before it can cross teams.
            if dose.healing != (not free_for_all and target.team == dose.team):
                continue
            # B95's 0.6 seconds, with the peer lane's reconstructed linear cadence.
            share = min(1.0, max(0, self.tick - dose.start_tick) * TICK / RIFLE_DOSE_SECONDS)
            cumulative = dose.amount if share == 1 else round(dose.amount * share * 1024) / 1024
            amount = cumulative - dose.applied
            if amount > 0:
                dose.applied = cumulative
                if dose.healing:
                    self._heal(dose.shooter, target, amount)
                else:
                    self._damage(dose.shooter, target, amount, False, dose.loadout, dose.frame,
                                 world_tick=self.tick, source_entity=dose.source, armour_scalar=1.0,
                                 source_scaled=True)
            if share < 1 and not self.vitals_of(target).dead:
                pending.append(dose)
        self.rifle_doses = pending

    def _biotic_allies(self, rocket: Rocket, *, include_self: bool) -> list[Target]:
        return [
            target for target in self._targets()
            if not self.vitals_of(target).dead and (
                (include_self and target.entity == rocket.source_entity)
                or (target.owner is not rocket.shooter and not self.match.game_map.free_for_all
                    and target.team == rocket.team)
            )
        ]

    def radial_impulse(self, target: Target, point, horizontal, vertical, *, scale=1.0, tick=None) -> None:
        """Apply a body-admitted horizontal/upward impulse using live mover resistance."""
        heading = (target.feet[0] - point[0], target.feet[2] - point[2])
        self._body_impulse(target, heading, horizontal, vertical, scale=scale, tick=tick)

    def _sigma_impulse(self, target, hit, heading, distance):
        horizontal = hit.horizontal_impulse * hit._ring_scale(
            tuple((radius, lateral) for radius, lateral, _ in hit.impulse_rings), distance)
        vertical = hit.vertical_impulse * hit._ring_scale(
            tuple((radius, lift) for radius, _, lift in hit.impulse_rings), distance)
        self._body_impulse(target, heading, horizontal, vertical, external=True, tick=self.tick)

    def _body_impulse(
        self, target, heading, horizontal, vertical, *, scale=1.0, external=False, tick=None,
    ) -> bool:
        """Return whether this hit applied a nonzero impulse to the body."""
        if (defenses.fortify_state(target.owner, tick=tick) is not None
                or reinhardt_charge.pinned(self, target.entity)):
            return False
        scale *= defenses.knockback_scale(target.owner, tick=tick)
        if scale <= 0 or any(self.zarya_gravitons.pulls(field, target, tick=tick)
                             for field in self.zarya_gravitons.fields):
            return False
        x, z = heading
        length = math.hypot(x, z)
        radial = horizontal * scale / length if length > EPSILON else 0.0
        impulse = (x * radial, vertical * scale, z * radial)
        if not any(impulse) or not all(math.isfinite(axis) for axis in impulse):
            return False
        owner = target.owner
        if isinstance(owner, bots.Bot):
            team_collision = owner.team in (0, 1) and not self.match.game_map.free_for_all
            category = (TEAM0, TEAM1)[owner.team] if team_collision else MOVER
            owner.apply_impulse(impulse, self.tick, self._collision(), category=category)
        elif external:
            owner.mover.add_external_impulse(impulse)
        else:
            mover = owner.mover
            mover.driven = False
            mover.velocity = tuple(a + b for a, b in zip(mover.velocity, impulse, strict=True))
            if mover.velocity[1] > 0:
                mover.flags_word |= FELL
                mover.fall = 0.0
        return True

    def _landed(self, rocket: Rocket, point=None) -> None:
        """The projectile ended (hit, range, lifetime): the predicted one's release follows."""
        if rocket in self.rockets:
            self.rockets.remove(rocket)
            self.zarya_gravitons.landed(rocket, rocket.position if point is None else point)
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

    def nano_targets(self, body: int, state, angles) -> tuple:
        owner = next((p for p in self.match.players if p.has_body and p.body == body and not p.gone), None)
        if (owner is None or self.match.collision_world is None or self.match.game_map.free_for_all
                or self.is_dead(owner) or self.is_stunned(owner)):
            return ()
        radius = _number(state.evaluate(state.node.field("m_radius"), watch=False), 0)
        origin, direction = self._eye(owner), aim(*angles)
        candidates = []
        for target in self._targets():
            if target.owner is owner or target.team != owner.team or self.vitals_of(target).dead:
                continue
            point = _along(target.feet, (0, 1, 0), target.eye)
            delta = _sub(point, origin)
            distance = math.sqrt(_dot(delta, delta))
            if not EPSILON < distance <= radius:
                continue
            alignment = _dot(direction, delta) / distance
            if alignment < math.cos(math.radians(10)):
                continue
            wall = self.cover_ray(origin, tuple(axis / distance for axis in delta), distance, exclude=owner)
            if wall is None or wall >= distance - EPSILON:
                candidates.append((-alignment, distance, target.entity))
        return (min(candidates)[2],) if candidates else ()

    def apply_nano(self, shooter, targets, duration: float) -> None:
        if self.match.ended or self.match.game_map.free_for_all or not math.isfinite(duration) or duration <= 0:
            return
        targets = (targets,) if isinstance(targets, expr.Entity) else targets
        if not isinstance(targets, tuple) or not targets:
            return
        ticks = math.ceil(duration / TICK)
        if self.match.tick > self.tick:
            # Older impacts must settle before either the heal or resistance is applied.
            self.pending_nano.setdefault(self.match.tick, []).append((shooter.body, targets, ticks))
            return
        self._apply_nano(shooter, targets, ticks)

    def _apply_nano(self, shooter, targets: tuple, duration: int) -> None:
        for target in self._targets():
            if (target.owner is shooter or target.team != shooter.team
                    or expr.Entity(target.entity) not in targets):
                continue
            vitals = self.vitals_of(target)
            if not vitals.dead:
                self._heal(shooter, target, 250)
                vitals.nano_until = self.tick + duration

    def offense_scale(self, entity: int) -> float:
        vitals = self.vitals.get(entity)
        return 1.5 if vitals is not None and not vitals.dead and self.tick < vitals.nano_until else 1.0

    def _heal(self, healer, target: Target, amount: float) -> None:
        vitals = self.vitals_of(target)
        if vitals.dead or self.tick < vitals.healing_block_until:
            return
        self.zarya_recovery.before_pool_change(target.entity, self.tick)
        if self.tick < vitals.healing_boost_until:
            amount *= 1.5
        given = vitals.heal(amount)
        if given > 0:
            if not isinstance(healer, bots.Bot):
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

    def _zarya_charge_script(self, player, source: int):
        script = getattr(player, "body_script", None)
        if (self.match.ended or not isinstance(script, BodyScript) or script.hero != zarya.HERO
                or script.entity != source or player.body != source or player.gone
                or not player.spawned or not player.has_body or player.client is None
                or script.retired or script.component.owner_dead or script.component.fault is not None):
            return None
        if any(rocket.source_entity == source and rocket.spawn == graviton.FIELD_ENTITY
               for rocket in self.rockets):
            return None
        return script

    def _zarya_charge_ready_tick(self, script) -> int | None:
        authored = zarya.ultimate_charge_ready_frame(script)
        ready = self.zarya_charge_ready.get(script.entity)
        if authored is None or (ready is None and authored):
            return None
        # A sparse world update can pass the field's deadline before removing the field.
        field_ready = max((field.end_tick + 1 for field in self.zarya_gravitons.fields
                           if field.source_entity == script.entity), default=0)
        return max(ready or 0, field_ready)

    def _charge_zarya_passive(self, first: int, last: int) -> None:
        for player in self.match.players:
            script = self._zarya_charge_script(player, player.body)
            if script is not None and script.ult_seconds > 0:
                ready = self._zarya_charge_ready_tick(script)
                if ready is not None:
                    start = max(first, ready, self.replacement_ticks.get(player.body, 0) + 1)
                    if start <= last:
                        elapsed = (last - start + 1) * TICK
                        script.award_ult_charge(elapsed * zarya.ULT_PASSIVE_PER_SECOND)

    def _damage(
        self, shooter, target: Target, amount: float, critical: bool, loadout, frame: int,
        *, world_tick: int | None = None,
        source_entity: int | None = None,
        effect: int | None = None,
        armour_scalar: float | None = None,
        ultimate_damage: bool = False,
        source_scaled: bool = False,
    ) -> bool:
        if world_tick is None and isinstance(shooter, bots.Bot) and self.tick:
            world_tick = self.tick
        vitals = self.vitals_of(target)
        if vitals.dead or amount <= 0.0 or phase.protection(target.owner, tick=world_tick) is not None:
            return False
        source = _source_entity(shooter) if source_entity is None else source_entity
        if not source_scaled and shooter is not target.owner:
            amount *= self.offense_scale(source)
        if shooter is not target.owner and self.zarya_barriers.intercept(
            target.owner, amount, source, tick=world_tick,
        ):
            return False
        if defenses.invulnerable(target.owner, tick=world_tick):
            return False
        if shooter is not target.owner and self.tick < vitals.nano_until:
            amount *= 0.5
        fortify = defenses.fortify_scale(target.owner, tick=world_tick)
        if fortify is not None:
            amount *= fortify
        damage_tick = self.match.tick if world_tick is None else world_tick
        self.zarya_recovery.before_pool_change(target.entity, damage_tick)
        dealt = vitals.damage(amount, armour_scalar=armour_scalar)
        if dealt <= 0.0:
            return False
        self.sleeps.pop(target.entity, None)
        self.zarya_recovery.damaged(target.owner, damage_tick)
        killed = vitals.now[HEALTH_POOL] <= EPSILON
        if killed:
            vitals.now[HEALTH_POOL] = 0.0
            vitals.died_at = self.now
            vitals.death_tick = damage_tick
            vitals.killer = source
        if shooter is not target.owner and not isinstance(shooter, bots.Bot):
            flags = (CRITICAL if critical else 0) | (KILLING if killed else 0)
            self._tell_hit(shooter, target, dealt, flags, frame)
            shooter.stats.add(stats.HERO_DAMAGE, dealt)
            if not ultimate_damage and shooter is not target.owner:
                script = self._zarya_charge_script(shooter, source)
                if script is not None:
                    ready = self._zarya_charge_ready_tick(script)
                    if ready is not None and ready <= (self.tick if world_tick is None else world_tick):
                        script.award_ult_charge(dealt)
        if (getattr(getattr(shooter, "hero", None), "guid", None) == 0x02E0000000000029
                and shooter is not target.owner and source == shooter.body
                and (self.match.game_map.free_for_all or shooter.team != target.team)):
            self.genji_assists.setdefault(target.entity, {})[source] = (shooter, damage_tick)
        if killed:
            self._genji_elimination(target, shooter, source, damage_tick)
        victim = target.owner
        if killed and isinstance(victim, bots.Bot):
            # The NPC death script owns its terminal health change and death event ordering.
            self._bot_died(shooter, victim, critical, loadout, vitals.death_tick, source_entity=source)
            return True
        self._sync_body_health(victim, vitals)
        if killed:
            self._died(shooter, victim, critical, loadout, source_entity=source)
        # The body receives a hit effect only when this hit reaches its pools.
        # A Zarya barrier intercepts the whole hit, including its control effect.
        self._hit_effect(target, effect, damage_tick)
        return True

    def _genji_elimination(self, target: Target, killer, source: int, tick: int) -> None:
        for body, (player, damaged) in self.genji_assists.pop(target.entity, {}).items():
            direct = player is killer and body == source
            if (not 0 <= tick - damaged <= GENJI_ASSIST_TICKS or player.body != body
                    or not player.has_body or player.gone or player not in self.match.players
                    or player.hero.guid != 0x02E0000000000029 or self.is_dead(player)):
                continue
            if not direct and (player is killer or killer is target.owner or self.match.game_map.free_for_all
                               or player.team != killer.team or player.team == target.team):
                continue
            script = player.body_script
            if script is None or script.retired or script.component.fault is not None:
                continue
            instance = next((i for i in script.component.instances.values() if i.graph.index == 0x01DB), None)
            if instance is not None:
                message, parameter = (0x0240000000000093, 0x15) if direct else (0x024000000000169F, 0x45)
                params = {parameter: expr.Entity(target.entity)}
                runtime.send_instance_message(instance, message, params, body)
                script.component.run_now()

    def _tell_hit(self, shooter, target: Target, dealt: float, flags: int, frame: int) -> None:
        """25001 to the shooter: the hit marker and the target's health bar (frame: the shot's)."""
        value = {
            "+0x78": {"+0x0": {"+0x0": target.entity}, "+0x4": max(1, round(dealt)), "+0x8": flags},
            "+0x84": frame & 0xFFFFFFFF,
        }
        self._queue_notice(shooter, target.owner, target.entity, HIT, value)

    def _kill_notice(self, killer, victim, *, source_entity=None) -> None:
        """25005 to the killer: "ELIMINATED: <name>" (1126.07C; +0x89 assist would be 01F0.07C, +0x88 a
        score shown when above 0). Its handler (0x7FF7895D2B50) reads only +0x84, +0x88 and +0x89."""
        entity = _source_entity(victim)
        notice = {
            "+0x78": 0,
            "+0x80": {"+0x0": killer.body if source_entity is None else source_entity},
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
    def _death_parameters(killer, critical: bool, loadout, *, source_entity=None) -> dict:
        return {
            KILLER: expr.Entity(_source_entity(killer) if source_entity is None else source_entity),
            CRIT: bool(critical),
            LOADOUT: expr.Asset(loadout) if loadout else None,
            ASSISTS: (),
        }

    def _bot_died(self, killer, bot: bots.Bot, critical: bool, loadout, tick: int,
                  *, source_entity=None) -> None:
        self.clear_pins(bot.entity)
        self.halt_pulls.pop(bot.entity, None)
        self.stuns.pop(bot.entity, None)
        self.sleeps.pop(bot.entity, None)
        self.zarya_barriers.retire(bot.entity)
        bot.died(tick)
        if not bot.retain_corpse:
            self.respawns.append((self.now + BOT_RESPAWN, bot))
        if not isinstance(killer, bots.Bot):
            killer.stats.add(stats.ELIMINATIONS)
            self._kill_notice(killer, bot, source_entity=source_entity)
        log.info("[game] %s: %s killed %s", self.match.label(), killer.name, bot.name)
        script = bot.body_script
        if script is None:
            return
        try:
            params = self._death_parameters(killer, critical, loadout, source_entity=source_entity)
            if isinstance(script, BotScript):
                tick = max(tick, script.frame)
            source = _source_entity(killer) if source_entity is None else source_entity
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

    def _died(self, killer, victim, critical: bool, loadout, *, source_entity=None) -> None:
        """A player died: the notices, his body script's "died" (the kill feed line), and the respawn."""
        sigma_flux.interrupt_flux_lift(self, victim)
        self.winston_jump.cancel(victim.body)
        self.winston_primal.sync(victim, victim.body_script)
        self.clear_pins(victim.body)
        self.genji_strikes.pop(victim.body, None)
        self.genji_blades.pop(victim.body, None)
        self.stuns.pop(victim.body, None)
        self.sleeps.pop(victim.body, None)
        self.zarya_barriers.retire(victim.body)
        label = self.match.label()
        log.info("[game] %s: %s killed %s", label, killer.name, victim.name)
        victim.stats.add(stats.DEATHS)
        bot_killer = isinstance(killer, bots.Bot)
        source = _source_entity(killer) if source_entity is None else source_entity
        if killer is not victim and not bot_killer:
            killer.stats.add(stats.ELIMINATIONS)
            self._kill_notice(killer, victim, source_entity=source)
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
                params = self._death_parameters(killer, critical, loadout, source_entity=source)
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
        charge = None
        if (isinstance(script, BodyScript) and script.hero == zarya.HERO
                and player.hero.guid == zarya.HERO and script.component.fault is None):
            charge = script.component.entity_var(ULT_CHARGE).value()
        if not self.match.switch_hero(player, player.hero):
            self.respawns.append((self.now + RESPAWN_RETRY, player))
            return
        if charge is not None and player.body_script is not None:
            player.body_script.set_ult_charge(charge)
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
