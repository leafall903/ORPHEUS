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
    ana_rifle,
    bots,
    contacts,
    fortify,
    graviton,
    hero_torbjorn,
    mei_blizzard,
    mei_frost,
    molten_core,
    observers,
    orisa_barrier,
    orisa_halt,
    orisa_supercharger,
    phase_defenses,
    pools,
    projectile_defenses,
    projectile_patterns,
    projectiles,
    reinhardt_barrier,
    sigma_accretion,
    sigma_barrier,
    symmetra_barrier,
    brigitte_shield,
    sigma_flux,
    sigma_grasp,
    sombra_control,
    stats,
    torbjorn_charge,
    torbjorn_mark,
    voicelines,
    world,
    world_bubbles,
    hook,
    steel_trap,
    zarya_bubbles,
)
from ow174.game.bits import BitWriter
from ow174.game.collision import MOVER, RayHit, World
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

EYE = 1.55  # STUFirstPersonComponent default; unknown bodies retain the existing fallback
# Explicit104319 FirstPerson m_E29993A5/m_816B830F, audited from the body's003.
EYE_STANDING = {
    0x04000000000026F0: 1.3,  # 104319 FirstPerson
    0x0400000000000022: 1.55,  # Reaper
    0x0400000000000024: 1.8,  # Reinhardt
    0x0400000000000225: 1.2,  # Genji
    0x04000000000002A3: 1.425,  # McCree
    0x04000000000003BF: 1.6,  # Zarya
    0x0400000000000407: 1.74,  # D.Va mech
    0x04000000000008BA: 1.3,  # Mei
    0x0400000000001C46: 1.7,  # Moira
    0x04000000000028B6: 1.0,  # Torbjorn
    0x0400000000002AB1: 2.1,  # Sigma
}
EYE_CROUCHED = {
    0x04000000000026F0: 0.91,  # 104319 FirstPerson
    0x0400000000000022: 1.085, 0x0400000000000225: .84,
    0x0400000000000024: 1.26,
    0x04000000000002A3: .9975, 0x04000000000003CF: 1.085,
    0x04000000000003BF: 1.12,
    0x0400000000000407: 1.74,
    0x04000000000008BA: .91, 0x0400000000001C46: 1.1,
    0x04000000000028B6: .7, 0x0400000000002AB1: 1.75,
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
AIRBURST_VOLLEYS = {(0x187F, 20), (0x187F, 28)}  # Sigma Hyperspheres
BEAM_ARMOUR_SCALE = .7
TICK = 0.016
EPSILON = 1e-9
FLASHBANG_EFFECT = 0x04A0000000000079  # .053/0079 ->0228 ->224E
FLASHBANG_MODE_FLAG = 13408  # 0228 duration: flag set1.7s, otherwise0.8s
ANA_SLEEP_EFFECT = 0x04A000000000026F  # .053/026F -> 0BDE -> 224E
ANA_SLEEP_DURATION = 5.0  # 0BDE passes override 00D7 to 224E
ANA_SLEEP_MINIMUM = 1.5
ANA_SLEEP_WAKE_RECOVERY = 0.5
ANA_ANTIHEAL_EFFECT = 0x04A0000000000267  # .053/0267 -> 0C71 -> 0B99
ANA_ANTIHEAL_DURATION = 4.0  # 0B99 wait


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
    return (EYE_CROUCHED.get(body, EYE_CROUCHED_DEFAULT) if crouched
            else EYE_STANDING.get(body, EYE))


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
    collision: World | None, origin, direction, length: float, category: int = MOVER, *, radius: float = 0.0,
) -> float | None:
    """First static contact for a ray or swept sphere."""
    if radius and collision is not None:
        hit = contacts.scene_sweep(
            collision.engine, origin, _along(origin, direction, length),
            ((0.0, 0.0, 0.0),), radius, category,
        )
        return hit[0] * length if hit is not None and _dot(direction, hit[2]) < -EPSILON else None
    hit = collision.cast_ray(origin, direction, length, category) if collision is not None else None
    return hit.distance if hit is not None else None


# --- health -------------------------------------------------------------------------------------------


@dataclass
class TemporaryArmour:
    amount: float = 0.0
    most: float = 0.0
    source: tuple[int, int] | None = None
    pool_id: int = 0


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
        self.over_armour = TemporaryArmour()
        self.temporary_shields: dict[int, TemporaryShield] = {}
        self.adaptive_shield_source = None
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
        return (sum(self.now.values()) + self.over_armour.amount
                + sum(p.now for p in self.temporary_shields.values()))

    @property
    def maximum(self) -> float:
        return sum(self.most.values()) + sum(p.most for p in self.temporary_shields.values())

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
            ids = [*self.ids.values(), self.over_armour.pool_id,
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

    def set_over_armour(self, source, amount: float = 0.0) -> bool:
        """An activation grants armour once; expiry discards its unspent remainder.

        A consumed pool retains its source until expiry so a repeated input or
        health synchronization cannot refill the same activation.
        """
        pool = self.over_armour
        if pool.source == source:
            return False
        pool.source = source
        pool.amount = pool.most = max(0.0, amount) if source is not None else 0.0
        if source is not None and not pool.pool_id:
            pool.pool_id = max((*self.ids.values(), *(p.pool_id for p in self.temporary_shields.values())),
                               default=0) + 1
        self.changed.add(ARMOUR_POOL)
        return True

    def damage(
        self, amount: float, *, armour_reduction: bool = True, beam_armour_scale: float | None = None,
    ) -> float:
        """Consume temporary armour before recoverable pools; reduce ordinary hits once.

        OW1 periodic damage bypasses armour reduction. Other hits retain the
        existing5-or-half reconstruction, even across multiple armour pools.
        A beam scale applies only to the part absorbed by an armour pool.
        """
        left = max(0.0, amount)
        taken = 0.0
        reduced = False
        armour_scale = 1.0 if beam_armour_scale is None else beam_armour_scale
        for tag in reversed(self.temporary_shields):
            shield = self.decay_temporary_shields(tag, left)
            taken += shield
            left -= shield
        if left > 0.0 and self.over_armour.amount > 0.0:
            if beam_armour_scale is None and armour_reduction:
                left = max(left - ARMOUR_BLOCK, left / 2)
                reduced = True
            over = min(left * armour_scale, self.over_armour.amount)
            self.over_armour.amount -= over
            self.changed.add(ARMOUR_POOL)
            taken += over
            left -= over / armour_scale
        for kind in (SHIELDS_POOL, ARMOUR_POOL, HEALTH_POOL):
            if left <= 0.0:
                break
            if (kind == ARMOUR_POOL and self.now[kind] > 0.0 and beam_armour_scale is None
                    and armour_reduction and not reduced):
                left = max(left - ARMOUR_BLOCK, left / 2)
                reduced = True
            scale = armour_scale if kind == ARMOUR_POOL else 1.0
            take = min(left * scale, self.now[kind])
            if take > 0.0:
                self.now[kind] -= take
                self.changed.add(kind)
                left -= take / scale
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
        over = self.over_armour
        if ARMOUR_POOL in kinds and over.pool_id:
            if over.source is None:
                parts[ARMOUR_POOL * POOLS + 1] = bytes(20)
            else:
                packed = bytearray(world.pool(over.pool_id, ARMOUR_POOL, over.most, over.amount))
                packed[18] = 1  # component51 treats this as over-armour, not a permanent maximum
                parts[ARMOUR_POOL * POOLS + 1] = bytes(packed)
        if SHIELDS_POOL in kinds:
            start = 1 if SHIELDS_POOL in self.ids else 0
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
    # Reviewed parent policy:owned McCree01D2/31 implicit primary critical2x.
    # This is scoped server reconstruction,not a recovered global null default.
    component = state.instance.component
    source = component.world
    implicit_critical = (2.0 if getattr(source, "hero", None) == 0x02E0000000000042
                         and getattr(source, "body", None) == component.entity
                         and state.instance.graph.index == 0x01D2 and state.node.pos == 31 else 1.0)

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
        critical=value("m_A872C70E", implicit_critical),
        rings=tuple(sorted(rings)),
        effect=effect.guid if isinstance(effect, expr.Asset) else None,
    )


def hypersphere_collision(state):
    """Read the 187F projectile's authored sphere radii and wall response."""
    node = state.node
    hit = node.field("m_6396149F") or {}
    bounce = node.field("m_E365DB20") or {}
    if bounce.get("$") != "STU_3FE1A93B":
        return {}

    def number(record, key):
        return _number(state.evaluate(record.get(key), watch=False), 0.0)

    return {
        "body_radius": number(hit, "m_57DAE1B5"),
        "wall_radius": number(hit, "m_1FFD8312"),
        "bounces": int(number(bounce, "m_360294F0")),
        "bounce_normal": number(bounce, "m_0D20DAFD"),
        "bounce_tangent": number(bounce, "m_C29D35E8"),
    }


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

    def segment(self, feet=None):
        """The capsule's segment (from the feet to the top, a radius in from both ends)."""
        x, y, z = feet or self.feet
        return (x, y + self.radius, z), (x, y + max(self.radius, self.height - self.radius), z)


@dataclass(eq=False)
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
    gravity: float = 0.0  # authored E1A5B579 downward acceleration, m/s^2
    distance: float = 0.0  # accumulated flight path, including the impact segment
    spawn: int | None = None  # authoritative landing entity, independent of prediction/ACK tracking
    definition: int | None = None  # authoritative003 tags, independent of prediction/ACK tracking
    target_limit: int = 1
    hit_entities: tuple[int, ...] = ()
    segment_start: float = 0.0  # cumulative flight time when the current ballistic segment began
    airburst: bool = False
    accretion_source: tuple[int, int] | None = None
    body_radius: float = 0.0
    wall_radius: float = 0.0
    bounces: int = 0
    bounce_normal: float = 0.0
    bounce_tangent: float = 0.0


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
        self.now = 0.0
        self.tick = 0
        self.melee_seen: dict[int, dict[tuple[int, int], int]] = {}  # body -> counters by instance/state
        self.stuns: dict[int, int] = {}  # body identity -> exclusive world-tick deadline
        self.sleep: dict[int, tuple[int, int]] = {}  # expiry and earliest damage wake
        self.antiheal: dict[int, list[tuple[int, int]]] = {}  # half-open tick windows
        self.pins: dict[int, int] = {}  # manual Assassin source -> controlled victim body
        self.projectiles = projectiles.Projectiles(self)  # leases, confirmations, the Biotic Field
        self.observers = observers.Observers(match)  # the bodies' statescript for the other clients
        self.voice = voicelines.VoiceLines(match)  # the hero's spawn voice line (voicelines.py)
        self.turrets = {}  # controller entity -> reconstructed Torbjorn deployable state
        self.molten_pools = {}
        self.molten_burns = {}
        self.torbjorn_marks = {}
        self.hammer_contacts = {}
        self.grasp_grants = {}
        self.grasp_shields = {}
        self.barriers = reinhardt_barrier.ReinhardtBarriers(self)
        self.bubbles = zarya_bubbles.ZaryaBubbles(self)
        self.world_bubbles = world_bubbles.WorldBubbles(self)
        self.steel_traps = steel_trap.Traps(self)
        self.orisa_barriers = orisa_barrier.Barriers(self)
        self.halt = orisa_halt.Halt(self)
        self.hooks = hook.Hooks(self)
        self.superchargers = orisa_supercharger.Superchargers(self)
        self.gravitons = graviton.Gravitons(self)
        self.sigma_barriers = sigma_barrier.Barriers(self)
        self.symmetra_barriers = symmetra_barrier.Barriers(self)
        self.brigitte_shields = brigitte_shield.Shields(self)
        self.sombra_hacks = sombra_control.Hacks(self)
        self.frost = mei_frost.Frost(self)
        self.blizzard = mei_blizzard.Blizzard(self)
        self.rifle_wounds = ana_rifle.Wounds(self)

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

    def retire_player_body(self, player, *, next_hero=None) -> None:
        """Discard body-local combat state when the player's current body retires."""
        if next_hero is not None and next_hero != player.hero.guid:
            molten_core.retire_owner(self, player)
        if player.hero.guid == hero_torbjorn.HERO and next_hero not in (None, hero_torbjorn.HERO):
            hero_torbjorn.destroy_turret(self, player)
            for key, mark in tuple(self.torbjorn_marks.items()):
                if mark.owner is player:
                    torbjorn_mark.remove(self, key)
            for rocket in self.rockets:
                if (rocket.shooter is player and rocket.direct is not None
                        and rocket.direct.effect == torbjorn_mark.EFFECT):
                    rocket.direct = replace(rocket.direct, effect=None)
        sigma_flux.retire_recipient(player)
        self.vitals.pop(player.body, None)
        self.history.pop(player.body, None)
        self.replacement_ticks.pop(player.body, None)
        self.melee_seen.pop(player.body, None)
        self.hammer_contacts.pop(player.body, None)
        self.grasp_grants.pop(player.body, None)
        self.grasp_shields.pop(player.body, None)
        self.stuns.pop(player.body, None)
        self.sleep.pop(player.body, None)
        self.antiheal.pop(player.body, None)
        self.frost.retire(player.body, player)
        self.blizzard.retire(player.body)
        self.rifle_wounds.retire(player.body)
        self.halt.retire(player.body)
        self.hooks.retire(player.body)
        self.gravitons.retire(player.body)
        self.superchargers.retire(player.body)
        self.sombra_hacks.retire(player.body)
        self.barriers.retire(player.body)
        self.bubbles.retire(player.body)
        self.world_bubbles.retire(player.body)
        self.steel_traps.retire(player.body)
        self.steel_traps.retire_victim(player.body)
        self.sigma_barriers.retire(player.body)
        self.symmetra_barriers.retire(player.body)
        self.brigitte_shields.retire(player.body)
        self.clear_pins(player.body)
        # The owner's leased projectiles and their observer copies retire with the old body.
        self.rockets[:] = [
            rocket for rocket in self.rockets if rocket.shooter is not player
            or (rocket.shot is None and rocket.definition != mei_blizzard.DEFINITION
                and rocket.accretion_source is None
                and rocket.spawn not in (graviton.FIELD_ENTITY, orisa_supercharger.SPAWN))
        ]
        self.respawns[:] = [(when, who) for when, who in self.respawns if who is not player]

    def record_replacement(self, body: int) -> None:
        """Keep the creation boundary independent of the rolling pose history."""
        self.replacement_ticks[body] = self.match.tick
        self.stuns.pop(body, None)
        self.sleep.pop(body, None)
        self.antiheal.pop(body, None)
        self.frost.retire(body)
        self.clear_pins(body)

    def is_stunned(self, actor, *, tick: int | None = None) -> bool:
        entity = _source_entity(actor)
        deadline = max(self.stuns.get(entity, 0), self.sleep.get(entity, (0, 0))[0])
        current = self.tick if tick is None else tick
        frost = self.frost.sample(actor, current)
        return (self.hooks.controlled(entity) or entity in self.pins.values()
                or deadline > current or (frost is not None and bool(frost[2])))

    def clear_pins(self, entity: int) -> None:
        for source, victim in list(self.pins.items()):
            if entity in (source, victim):
                del self.pins[source]

    def _hit_effect(self, target: Target, effect: int | None, shooter=None) -> None:
        """Scoped resource-backed status hosts; native immunity/presentation remain unverified."""
        if phase_defenses.phase(target.owner)[0]:
            return
        if effect == torbjorn_mark.EFFECT:
            torbjorn_mark.apply(self, shooter, target)
            return
        if effect not in (FLASHBANG_EFFECT, ANA_SLEEP_EFFECT, ANA_ANTIHEAL_EFFECT):
            return
        if self.vitals_of(target).dead:
            return
        if effect == ANA_ANTIHEAL_EFFECT:
            if not isinstance(target.owner, bots.Bot) and target.owner.hero.guid == hero_torbjorn.HERO:
                windows = self.antiheal.setdefault(target.entity, [])
                deadline = self.tick + math.ceil(ANA_ANTIHEAL_DURATION / TICK)
                if windows and self.tick <= windows[-1][1]:
                    windows[-1] = windows[-1][0], deadline
                else:
                    windows.append((self.tick, deadline))
            return
        if effect == ANA_SLEEP_EFFECT:
            owner = target.owner
            accepts_sleep = (owner.spawner.get("manual_spawn") is True if isinstance(owner, bots.Bot)
                             else (owner.hero.guid == hero_torbjorn.HERO
                                   or target.entity in self.brigitte_shields.by_body))
            if accepts_sleep:
                minimum = self.tick + math.ceil(ANA_SLEEP_MINIMUM / TICK)
                if self._replace_timed_control(target, minimum * 16):
                    self.sleep[target.entity] = self.tick + math.ceil(ANA_SLEEP_DURATION / TICK), minimum
                    self._interrupt(target)
            return
        if fortify.control_flag_active(target.owner):
            return
        variables = graph.mode_variables(self.match.game_map.mode_guid) or {}
        duration = 1.7 if expr.truthy(variables.get(FLASHBANG_MODE_FLAG)) else 0.8
        deadline = self.tick + math.ceil(duration / TICK)
        if self._replace_timed_control(target, deadline * 16):
            self.stuns[target.entity] = deadline
            self._interrupt(target)

    def _replace_timed_control(self, target: Target, deadline_ms: int) -> bool:
        entity = target.entity
        # Sleep competes by its guaranteed minimum. Frost deadlines use world milliseconds.
        sleep_floor = self.sleep.get(entity, (0, 0))[1] * 16
        frost = self.frost.sample(target.owner, self.tick)
        thaw = frost[2] if frost is not None else 0
        if deadline_ms <= max(sleep_floor, self.stuns.get(entity, 0) * 16, thaw):
            return False
        self.sleep.pop(entity, None)
        self.stuns.pop(entity, None)
        if thaw:
            self.frost.retire(entity, target.owner)
        return True

    def _interrupt(self, target: Target) -> None:
        self.hooks.cancel_owner(target.entity)
        self.brigitte_shields.interrupt_bash(target.entity, target.owner)
        hero_torbjorn.cancel_hammer(self, target.entity)
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
                    if not hero_torbjorn.preserve_cast_during_control(state):
                        state.stop()  # use authored exit/cooldown paths for running channels
        sigma_flux.cancel_warning(component)
        orisa_supercharger.cancel_windup(component)
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
        hero_torbjorn.overload(self, player, script)
        torbjorn_charge.sync_adaptive_shield(self, player, script)
        self.barriers.sync(player)
        self.bubbles.sync(player)
        self.sigma_barriers.sync(player)
        self.brigitte_shields.enforce_lockout(player)
        shots, script.component.shots[:] = list(script.component.shots), []
        if self.is_dead(player) or self.is_stunned(player):
            return
        if command is not None:
            from ow174.game import winston_tesla

            self.hooks.command(player, command)
            self.brigitte_shields.whip_command(player, command, script)
            winston_tesla.command(self, player, script, command.frame)
            for state, number, time, activation in shots:
                try:
                    self._shoot(player, command, state, number, time, activation=activation)
                except Exception:
                    log.exception("[game] %s: %s's shot failed", self.match.label(), player.name)
        try:
            self._melee(player, command, script)
            self.brigitte_shields.flail_command(player, command, script)
            hero_torbjorn.hammer(self, player, command, script)
        except Exception:
            log.exception("[game] %s: %s's melee failed", self.match.label(), player.name)

    def _melee(self, player, command, script) -> None:
        """Quick melee: 0043 st4 (StateModifyHealth) hits what its st5 TrackTargets finds: a sphere of
        m_radius 1.0 at (0, 0, 1.5) from the eye (DATA; unverified: along the aim), #476 = 30 damage (DATA),
        once per activation of st4. The server cannot run TrackTargets itself (the VM finds nothing)."""
        immune = phase_defenses.phase(player)[0]
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
            if immune or state.counter == seen or not state.active or command is None:
                continue
            modify = state.node.field("m_modifyHealth") or {}
            amount = _number(state.evaluate(modify.get("m_amount"), watch=False), 0.0) if modify else 0.0
            loadout = (
                state.evaluate(modify.get("m_loadout"), watch=False) if modify.get("m_loadout") else None
            )
            loadout = loadout.guid if isinstance(loadout, expr.Asset) else None
            centre = _along(self._eye(player), aim(*command.aim()), reach)
            self.barriers.melee(player, centre, radius, amount)
            self.orisa_barriers.melee(player, centre, radius, amount)
            self.sigma_barriers.melee(player, centre, radius, amount)
            self.symmetra_barriers.melee(player, centre, radius, amount)
            self.steel_traps.melee(player, self._eye(player), centre, radius, amount)
            protected = self.bubbles.area(player, centre, radius, amount)
            view_tick = command.frame - command.view_delay(player.quanta) * 1e6 / player.quanta
            for target in self._enemies(player):
                if target.entity in protected:
                    continue
                shown = self.seen(target, view_tick)
                if shown is None:
                    continue
                a, b = shown.segment()
                if (segment_distance(centre, a, b) - shown.radius <= radius and amount > 0.0
                        and capsule_visible(self._collision(), self._eye(player), a, b, shown.radius)
                        and not projectile_defenses.blocks_torbjorn_melee(player, target, state, modify)):
                    self._damage(player, target, amount, False, loadout, command.frame)

    def update(self, now: float, tick: int, frames: bool = True) -> None:
        """Each tick: keep where the bodies are, fly the rockets, respawn, send health that changed. The
        other players' body frames only on the ticks that send (`frames`)."""
        if self.match.collision_world is None:
            return
        retained = max(self.tick + 1, tick - COMBAT_CATCH_UP + 1)
        first = tick
        if self.tick and tick > self.tick + 1 and (
            self.turrets or self.molten_pools or self.blizzard.areas or self.halt.orbs or self.halt.pulls
            or self.gravitons.fields or self.superchargers.active or self.hooks.flights or self.steel_traps.devices
            or any(r.definition == mei_blizzard.DEFINITION and r.last_tick is not None for r in self.rockets)
            or self.rifle_wounds.active or self.orisa_barriers.flights or self.orisa_barriers.actors
            or self.symmetra_barriers.actors
            or any(actor.mode == 2 for actor in self.sigma_barriers.actors.values())
            or any(bot.firing and not bot.dead and isinstance(bot.body_script, BotScript)
                   for bot in self.match.bots)
        ):
            first = retained
        for target in self._targets():
            history = self.history.setdefault(target.entity, deque(maxlen=HISTORY))
            history.append((tick, target.feet, target.height, target.eye))
        # Replay the retained combat frames using the most recent poses. Player input
        # accepted just before the scheduler caught up joins only at the endpoint.
        for at in range(first, tick + 1):
            self.now, self.tick = now - (tick - at) * TICK, at
            self.barriers.update()
            self.world_bubbles.update(at)
            self.orisa_barriers.update(at, TICK)
            self.steel_traps.update(at, TICK)
            self.halt.update(at)
            self.sigma_barriers.update(at, TICK)
            self.symmetra_barriers.update(at)
            self.brigitte_shields.update(self.now)
            self.brigitte_shields._fly_whips()
            self.sombra_hacks.update(at)
            for player in self.match.players:
                self.bubbles.sync(player)
            if self.superchargers.active:
                self.superchargers.update(at)
            for rocket in list(self.rockets):
                if at == tick or rocket.last_tick is not None:
                    self._fly(rocket)
            self.gravitons.update(at)
            self.halt.move_npcs(at)
            self.hooks.update()
            torbjorn_mark.update(self)
            hero_torbjorn.update_turrets(self)
            molten_core.update(self)
            self.blizzard.update(at)
            self.rifle_wounds.update(at)
            sigma_grasp.update(self)
            self._bot_weapons(at)
            phase_defenses.heal_transcendence(self, at)
        self.frost.update(tick)
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
        for entity in [key for key, deadline in self.stuns.items() if deadline <= tick]:
            del self.stuns[entity]
        for entity, windows in tuple(self.antiheal.items()):
            remaining = [window for window in windows if window[1] > tick]
            if remaining and entity in current:
                self.antiheal[entity] = remaining
            else:
                del self.antiheal[entity]
        for entity, (deadline, _) in tuple(self.sleep.items()):
            if deadline <= tick or entity not in current:
                del self.sleep[entity]
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
            data = mover_data(bot.body)
            height, radius, eye, critical = (bot.combat_geometry or
                                           (data.stand_height, data.stand_radius, EYE, True))
            found.append(
                Target(
                    bot.entity, bot.team, tuple(bot.position), height, radius, eye, bot, critical
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
        owner = shooter.combat_owner if isinstance(shooter, bots.Bot) else shooter
        found = []
        for target in self._targets():
            if phase_defenses.phase(target.owner)[1]:
                continue
            if (target.owner is shooter or target.entity == source or target.owner is owner
                    or (owner is not None and getattr(target.owner, "combat_owner", None) is owner)):
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
        return Target(target.entity, target.team, feet, height, target.radius, eye, target.owner,
                      target.critical_allowed)

    # --- shots -------------------------------------------------------------------------------------

    def _bot_weapons(self, tick: int) -> None:
        """Advance the weapon bodies of the map's firing bots and consume their volley reports."""
        from ow174.game import npc_control

        for bot in self.match.bots:
            script = bot.body_script
            controlled = npc_control.manual(bot)
            if ((not bot.firing and not controlled and bot.entity not in self.halt.pulls) or bot.dead
                    or not isinstance(script, BotScript) or script.frame > tick):
                continue
            try:
                allowed = (npc_control.update_input(self, bot) if controlled
                           else bot.firing and not self.is_stunned(bot))
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
                self.halt.release(bot.entity)
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
        if phase_defenses.phase(shooter)[0]:
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
            eye = shooter.combat_geometry[2] if shooter.combat_geometry else EYE
            origin, direction = (x, y + eye, z), aim(round(shooter.yaw), round(shooter.pitch))
        else:
            origin, direction = self._eye(shooter), aim(*command.aim())
        if (not npc and self.orisa_barriers.geometry is not None
                and (state.instance.graph.index, node.pos) == (0xBD7, 1)):
            self.orisa_barriers.launch(shooter, state, origin, direction, command.aim()[0])
            return
        if not npc and (state.instance.graph.index, node.pos) == steel_trap.LAUNCH:
            self.steel_traps.launch(shooter, state, origin, direction)
            return
        direct = hit_def(state, node.field("m_6396149F"))
        entity_def = (node.field("m_projectileEntity") or {}).get("m_entityDef")
        entity_value = state.evaluate(entity_def, watch=False) if entity_def is not None else None
        definition = entity_value.guid if isinstance(entity_value, expr.Asset) else None
        speed = field("m_projectileSpeed")
        if speed:
            lifetime = field("m_projectileLifetime", 5.0) or 5.0
            reach = field("m_FEC435C6")
            if reach:
                lifetime = min(lifetime, reach / speed)
            blast = hit_def(state, node.field("m_D2D11CE9"))
            gravity = sum(
                _number(state.evaluate(motion.get("m_599AB8D7"), watch=False), 0.0)
                for motion in node.field("m_projectileMotions") or []
                if isinstance(motion, dict) and motion.get("$") == "STU_E1A5B579"
            )
            accretion_source = None
            if (not npc and shooter.hero.guid == sigma_accretion.HERO
                    and state.instance.graph.index == sigma_accretion.CAST and node.pos == 9):
                direction = sigma_accretion.launch_direction(state, direction)
                accretion_source = shooter.body, state.instance.id
            sphere = (state.instance.graph.index, node.state) in AIRBURST_VOLLEYS
            sphere_motion = hypersphere_collision(state) if sphere else {}
            spawn_cfg = (node.field("m_49CBA3BC") or {}).get("m_entityDefinition")
            spawn_value = state.evaluate(spawn_cfg, watch=False) if spawn_cfg is not None else None
            spawn = spawn_value.guid if isinstance(spawn_value, expr.Asset) else None
            shot = None
            if definition is not None:
                shot = self.projectiles.fired(
                    shooter, state, shot_frame, definition, lifetime, origin, direction,
                    activation=activation, snapshot=snapshot,
                )
            if not npc and self.halt.launch(
                shooter, state, origin, direction, definition, speed, lifetime, shot,
            ):
                return
            damaging = (direct is not None and direct.amount > 0.0) or (
                blast is not None and blast.amount > 0.0
            )
            device = spawn == orisa_supercharger.SPAWN and self.superchargers.geometry is not None
            if damaging or shot is not None or definition == mei_blizzard.DEFINITION or device:
                # Predicted projectile leases still carry one projectile per
                # authored record. Only unpredicted ring launches expand here.
                directions = ((direction,) if shot is not None
                              else projectile_patterns.directions(state, direction))
                for flight_direction in directions:
                    self.rockets.append(
                        Rocket(
                            shooter, shooter.team, origin, flight_direction, speed, lifetime, direct, blast,
                            shot, last_tick=self.tick if npc else None, gravity=gravity,
                            spawn=spawn, definition=definition,
                            target_limit=mei_frost.projectile_target_limit(state),
                            airburst=sphere, accretion_source=accretion_source, **sphere_motion,
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
        self._ray(shooter, origin, direction, reach, direct, definition, shot_frame, view_tick,
                  world_tick=self.tick if npc else None)

    def _ray(self, shooter, origin, direction, reach, direct, definition, shot_frame, view_tick,
             *, world_tick=None, distance=0.0, reflections=0):
        """Hitscan sweep, including mapped gates before the nearest body/wall."""
        best = None
        targets = self._enemies(shooter)
        for target in targets:
            shown = self.seen(target, view_tick)
            if shown is None:
                continue
            a, b = shown.segment()
            t = ray_capsule(origin, direction, a, b, shown.radius)
            if t is not None and t <= reach and (best is None or t < best[0]):
                best = (t, target, shown)
        wall = ray_world(self._collision(), origin, direction, reach if best is None else best[0])
        limit = reach if best is None else best[0]
        if wall is not None:
            limit = min(limit, wall)
        barrier = self._shield_trace(shooter, origin, direction, limit)
        if barrier is not None and ((best is None and wall is None) or barrier[0] < limit - EPSILON):
            limit = barrier[0]
        else:
            barrier = None
        gate = projectile_defenses.projectile_gate(
            origin, direction, limit, targets, definition, collision=self._collision())
        if gate is not None and gate[0] < limit - EPSILON:
            t, defender, mode, reflected_direction, state = gate
            if mode == 1:
                sigma_grasp.absorbed(state, direct.amount * direct.scale(distance + t))
            if mode == 0 and reflections < projectile_defenses.MAX_REFLECTIONS_PER_STEP:
                self._ray(defender.owner, _along(origin, direction, t), reflected_direction,
                          reach - t, direct, definition, self.tick, self.tick,
                          world_tick=self.tick, distance=distance + t, reflections=reflections + 1)
            return
        if barrier is not None:
            self._shield_damage(shooter, barrier[1], direct.amount * direct.scale(distance + limit))
            return
        if best is None or (wall is not None and wall < best[0]):
            return
        t, target, shown = best
        hit_y = origin[1] + direction[1] * t
        critical = (shown.critical_allowed and hit_y >= shown.feet[1] + shown.eye - HEAD_BELOW
                    and fortify.state_for(target.owner) is None)
        amount = direct.amount * direct.scale(distance + t) * (direct.critical if critical else 1.0)
        self._projectile_hit(shooter, target, direct, amount, critical, shot_frame, world_tick)

    def _projectile_hit(self, shooter, target, direct, amount, critical, frame, world_tick):
        if direct.loadout == ana_rifle.LOADOUT and direct.effect in ana_rifle.HIT_EFFECTS:
            self.rifle_wounds.apply(shooter, target, amount, self.tick if world_tick is None else world_tick)
            return
        self._damage(
            shooter, target, amount, critical and direct.critical > 1.0, direct.loadout, frame,
            world_tick=world_tick,
        )
        self._hit_effect(target, direct.effect, shooter)

    def _fly(self, rocket: Rocket) -> None:
        """Sweep elapsed world time against the current bodies and static collision."""
        # Player input can arrive immediately before the scheduler accounts for a gap.
        elapsed = 1 if rocket.last_tick is None else self.tick - rocket.last_tick
        if elapsed <= 0:
            return
        rocket.last_tick = self.tick
        duration = min(elapsed * TICK, rocket.left)
        if duration <= EPSILON:
            self._expire_rocket(rocket)
            return
        # Reconstruct a ballistic path with a sweep for each normal simulation
        # interval. A scheduler gap must not become a single chord through terrain.
        remaining = duration
        while remaining > EPSILON and rocket in self.rockets:
            step_time = min(TICK, remaining)
            rocket = self._fly_step(rocket, step_time)
            remaining -= step_time

    def _fly_step(self, rocket: Rocket, duration: float, reflections=0) -> Rocket | None:
        """Sweep one ballistic chord (reconstructed; native integration unverified)."""
        # Sweep the remaining flight even when the range or lifetime ends inside this tick.
        displacement = tuple(rocket.speed * axis * duration for axis in rocket.direction)
        segment_age = rocket.flown - rocket.segment_start
        displacement = (
            displacement[0],
            displacement[1] - rocket.gravity * (segment_age * duration + 0.5 * duration * duration),
            displacement[2],
        )
        step = math.sqrt(_dot(displacement, displacement))
        direction = tuple(axis / step for axis in displacement) if step > EPSILON else rocket.direction
        best = None
        targets = self._enemies(rocket.shooter)
        for target in targets:
            if (rocket.definition == mei_blizzard.DEFINITION or rocket.spawn == orisa_supercharger.SPAWN
                    or target.entity in rocket.hit_entities):
                continue
            a, b = target.segment()
            t = ray_capsule(rocket.position, direction, a, b, target.radius + rocket.body_radius)
            if t is not None and t <= step and (best is None or t < best[0]):
                best = (t, target)
        collision = self._collision()
        if rocket.wall_radius:
            hit = contacts.scene_sweep(
                collision.engine, rocket.position, _along(rocket.position, direction, step),
                ((0.0, 0.0, 0.0),), rocket.wall_radius, MOVER,
            )
            wall = (RayHit(hit[0] * step, hit[1], hit[2], MOVER, 0)
                    if hit is not None and _dot(direction, hit[2]) < -EPSILON else None)
        else:
            wall = collision.cast_ray(rocket.position, direction, step)
        if wall is not None and (best is None or wall.distance < best[0]):
            best = (wall.distance, None)
        limit = step if best is None else best[0]
        direct, blast = rocket.direct, rocket.blast
        barrier = None
        if (direct is not None and direct.amount > 0) or (blast is not None and blast.amount > 0):
            barrier = self._shield_trace(rocket.shooter, rocket.position, direction, limit)
            if barrier is not None and (best is None or barrier[0] < limit - EPSILON):
                limit = barrier[0]
            else:
                barrier = None
        gate = projectile_defenses.projectile_gate(
            rocket.position, direction, limit, targets, rocket.definition, collision=self._collision())
        if gate is not None and gate[0] < limit - EPSILON:
            t, defender, mode, reflected_direction, state = gate
            spent = duration * t / step if step > EPSILON else 0.0
            rocket.flown += spent
            rocket.distance += t
            rocket.left -= spent
            if mode == 1:
                nominal = ((rocket.direct.amount * rocket.direct.scale(rocket.distance)
                            if rocket.direct is not None else 0.0)
                           + (rocket.blast.amount if rocket.blast is not None else 0.0))
                sigma_grasp.absorbed(state, nominal)
            self._landed(rocket, intercepted=True)  # no original impact explosion/landing/blast/pool
            if mode != 0 or reflections >= projectile_defenses.MAX_REFLECTIONS_PER_STEP:
                return None
            velocity = [axis * rocket.speed for axis in rocket.direction]
            velocity[1] -= rocket.gravity * (rocket.flown - rocket.segment_start)
            speed = math.sqrt(_dot(velocity, velocity))
            reflected = Rocket(
                defender.owner, defender.team, _along(rocket.position, direction, t),
                reflected_direction, speed, rocket.left, rocket.direct, rocket.blast,
                last_tick=self.tick, gravity=rocket.gravity, distance=rocket.distance,
                spawn=rocket.spawn, definition=rocket.definition,
                target_limit=rocket.target_limit, hit_entities=rocket.hit_entities,
                airburst=rocket.airburst, body_radius=rocket.body_radius, wall_radius=rocket.wall_radius,
                bounces=rocket.bounces,
                bounce_normal=rocket.bounce_normal, bounce_tangent=rocket.bounce_tangent,
            )
            self.rockets.append(reflected)
            rest = duration - spent
            return (self._fly_step(reflected, rest, reflections + 1)
                    if rest > EPSILON else reflected)
        if barrier is not None:
            _, owner = barrier
            rocket.flown += duration * limit / step if step > EPSILON else 0.0
            rocket.distance += limit
            self._landed(rocket)
            point = _along(rocket.position, direction, limit)
            if rocket.spawn == graviton.FIELD_ENTITY:
                self.gravitons.land(rocket, point, _along(point, direction, -.0001))
            amount = direct.amount * direct.scale(rocket.distance) if direct is not None else 0.0
            if blast is not None and blast.amount > 0:
                self._blast(rocket, point, _along(point, direction, -.0001),
                            impact_owner=owner, impact_amount=amount + blast.amount)
            else:
                self._shield_damage(rocket.shooter, owner, amount)
            return None
        if best is None:
            rocket.position = _along(rocket.position, direction, step)
            rocket.flown += duration
            rocket.distance += step
            rocket.left -= duration
            if rocket.left <= EPSILON:
                self._expire_rocket(rocket)
                return None
            return rocket
        t, struck = best
        spent = duration * t / step if step > EPSILON else 0.0
        rocket.flown += spent
        rocket.distance += t
        piercing = struck is not None and len(rocket.hit_entities) + 1 < rocket.target_limit
        point = _along(rocket.position, direction, t)
        if struck is None and rocket.bounces > 0:
            rocket.bounces -= 1
            rocket.left -= spent
            molten_core.rebound(rocket, point, wall.normal, normal_scale=rocket.bounce_normal,
                                tangent_scale=rocket.bounce_tangent)
            if rocket.left <= EPSILON:
                self._expire_rocket(rocket)
                return None
            if reflections >= projectile_defenses.MAX_REFLECTIONS_PER_STEP:
                return rocket
            rest = min(duration - spent, rocket.left)
            return self._fly_step(rocket, rest, reflections + 1) if rest > EPSILON else rocket
        if struck is None and rocket.spawn == molten_core.POOL and wall.normal[1] < molten_core.GROUND_NORMAL:
            rocket.left -= spent
            if rocket.left <= EPSILON or reflections >= projectile_defenses.MAX_REFLECTIONS_PER_STEP:
                self._landed(rocket)
                return None
            molten_core.rebound(rocket, point, wall.normal)
            rest = min(duration - spent, rocket.left)
            return self._fly_step(rocket, rest, reflections + 1) if rest > EPSILON else rocket
        if not piercing:
            self._landed(rocket)
            hero_torbjorn.projectile_impact(self, rocket, point, struck)
            molten_core.projectile_impact(self, rocket, wall if struck is None else None, struck)
            if rocket.definition == mei_blizzard.DEFINITION and struck is None:
                self.blizzard.land(rocket.shooter, point)
            if (rocket.spawn == orisa_supercharger.SPAWN and wall is not None
                    and struck is None and wall.normal[1] > .707):
                self.superchargers.land(rocket, point)
        if struck is not None and rocket.direct is not None and rocket.direct.amount > 0.0:
            direct = rocket.direct
            # Graviton has fixed impact damage, including at head height.
            critical = (rocket.spawn != graviton.FIELD_ENTITY and struck.critical_allowed
                        and point[1] >= struck.feet[1] + struck.eye - HEAD_BELOW
                        and fortify.state_for(struck.owner) is None)
            amount = direct.amount * direct.scale(rocket.distance)
            if critical:
                amount *= direct.critical
            before = self.vitals_of(struck).total if rocket.accretion_source is not None else None
            self._projectile_hit(rocket.shooter, struck, direct, amount, critical, self.tick, self.tick)
            if before is not None:
                sigma_accretion.direct_hit(self, rocket, struck, before - self.vitals_of(struck).total)
        if piercing:
            rocket.hit_entities += (struck.entity,)
            rocket.position = point
            rocket.left -= spent
            rest = min(duration - spent, rocket.left)
            return self._fly_step(rocket, rest, reflections) if rest > EPSILON else rocket
        # Start just outside an impact wall, on the projectile's approach side.
        visible_from = _along(point, rocket.direction, -0.0001) if wall is not None else point
        if rocket.spawn == graviton.FIELD_ENTITY:
            self.gravitons.land(rocket, point, visible_from)
        self._blast(rocket, point, visible_from)

    def _shield_trace(
        self, shooter, origin, direction, limit, *, include_attached=True, include_orisa=True,
        include_zarya=True, radius=0.0,
    ):
        mesh = self.barriers.trace(shooter, origin, direction, limit, radius=radius)
        bubble = (self.bubbles.trace(shooter, origin, direction, limit, radius=radius)
                  if include_attached and include_zarya else None)
        fixed = self.world_bubbles.trace(shooter, origin, direction, limit, radius=radius)
        orisa = self.orisa_barriers.trace(shooter, origin, direction, limit, radius=radius) if include_orisa else None
        sigma = self.sigma_barriers.trace(shooter, origin, direction, limit, radius=radius)
        photon = self.symmetra_barriers.trace(shooter, origin, direction, limit, radius=radius)
        brig = self.brigitte_shields.trace(shooter, origin, direction, limit, radius=radius)
        device = self.superchargers.trace(shooter, origin, direction, limit) if include_attached else None
        trap = self.steel_traps.trace(shooter, origin, direction, limit) if include_attached else None
        return min((hit for hit in (mesh, bubble, fixed, orisa, sigma, photon, brig, device, trap) if hit is not None),
                   key=lambda hit: hit[0], default=None)

    def _shield_damage(self, shooter, shield, amount):
        amount *= self.superchargers.scale(shooter)
        if isinstance(shield, orisa_supercharger.Device):
            self.superchargers.damage(shield, amount)
        elif isinstance(shield, zarya_bubbles.Bubble):
            self.bubbles.damage(shield, amount, getattr(shooter, "combat_owner", None) or shooter)
        elif isinstance(shield, brigitte_shield.Shield):
            self.brigitte_shields.damage(shield, amount)
        elif isinstance(shield, symmetra_barrier.Shield):
            self.symmetra_barriers.damage(shield, amount)
        elif isinstance(shield, sigma_barrier.Shield):
            self.sigma_barriers.damage(shield, amount)
        elif isinstance(shield, orisa_barrier.Shield):
            self.orisa_barriers.damage(shield, amount)
        elif isinstance(shield, steel_trap.Trap):
            self.steel_traps.damage(shield, amount)
        elif isinstance(shield, world_bubbles.FixedBubble):
            self.world_bubbles.damage(shield, amount)
        else:
            self.barriers.damage(shield, amount)

    def _blast(self, rocket, point, visible_from, *, impact_owner=None, impact_amount=0):
        blast = rocket.blast
        if blast is None or blast.amount <= 0.0:
            return
        shooter = rocket.shooter
        collision = self._collision()
        impacted = impact_owner if isinstance(impact_owner, zarya_bubbles.Bubble) else None
        rein = self.barriers.enemies(shooter)
        shells = tuple(self.world_bubbles.enemies(shooter))
        orisa = tuple(self.orisa_barriers.enemies(shooter))
        sigma = tuple(self.sigma_barriers.enemies(shooter))
        photon = tuple(self.symmetra_barriers.enemies(shooter))
        brig = tuple(self.brigitte_shields.enemies(shooter))
        # Plan the whole event before applying damage. A broken front barrier still
        # blocks every ray in this explosion, including rays aimed at other barriers.
        groups = (
            (rein, self.barriers.nearest, self.barriers.blocked),
            (shells, self.world_bubbles.nearest, self.world_bubbles.blocked),
            (orisa, self.orisa_barriers.nearest, self.orisa_barriers.blocked),
            (sigma, self.sigma_barriers.nearest, self.sigma_barriers.blocked),
            (photon, self.symmetra_barriers.nearest, self.symmetra_barriers.blocked),
            (brig, self.brigitte_shields.nearest, self.brigitte_shields.blocked),
        )

        def blocked(destination, skip=None, *, include_rein=True):
            selected = groups if include_rein else groups[1:]
            return any(test(visible_from, destination, (actor for actor in actors if actor is not skip))
                       for actors, _, test in selected)

        debits = [(impact_owner, impact_amount)] if impact_owner is not None else []
        for actors, nearest, _ in groups:
            for actor in actors:
                if actor is impact_owner:
                    continue
                contact = nearest(actor, point)
                if contact is None:
                    continue
                distance, surface = contact
                amount = blast.amount * blast.splash(distance)
                if (amount > 0 and capsule_visible(collision, visible_from, surface, surface, 0)
                        and not blocked(surface, actor)):
                    debits.append((actor, amount))

        protected = {impacted.body} if impacted is not None else set()
        for bubble in self.bubbles.enemies(shooter):
            centre = bubble.centre()
            radius = zarya_bubbles.RADIUS
            scalar = blast.splash(max(0.0, math.dist(point, centre) - radius))
            if (bubble.body not in protected and scalar > 0
                    and capsule_visible(collision, visible_from, centre, centre, radius)
                    and not self.barriers.covers(visible_from, centre, centre, radius, rein)
                    and not blocked(centre, include_rein=False)):
                protected.add(bubble.body)
                debits.append((bubble, blast.amount * scalar))
        victims = []
        for target in self._enemies(shooter):
            if target.entity in protected:
                continue
            a, b = target.segment()
            distance = max(0.0, segment_distance(point, a, b) - target.radius)
            scalar = blast.splash(distance)
            centre = tuple((x + y) * .5 for x, y in zip(a, b, strict=True))
            if (scalar > 0 and capsule_visible(collision, visible_from, a, b, target.radius)
                    and not self.barriers.covers(visible_from, a, b, target.radius, rein)
                    and not blocked(centre, include_rein=False)):
                victims.append((target, blast.amount * scalar))
        for actor, amount in debits:
            self._shield_damage(shooter, actor, amount)
        for target, amount in victims:
            self._damage(shooter, target, amount, False, blast.loadout, self.tick, world_tick=self.tick)
            self._hit_effect(target, blast.effect, shooter)

    def _expire_rocket(self, rocket: Rocket) -> None:
        self._landed(rocket)
        if rocket.airburst:
            self._blast(rocket, rocket.position, rocket.position)

    def _landed(self, rocket: Rocket, *, intercepted=False) -> None:
        """The projectile ended (hit, range, lifetime): the predicted one's release follows."""
        if rocket in self.rockets:
            self.rockets.remove(rocket)
        if rocket.shot is not None:
            if intercepted:
                self.projectiles.flown(rocket.shot, rocket.flown, intercepted=True)
            else:
                self.projectiles.flown(rocket.shot, rocket.flown)

    def cleanse_antiheal(self, entity: int) -> None:
        windows = self.antiheal.get(entity)
        if windows:
            start, end = windows[-1]
            if start <= self.tick < end:
                # Keep the elapsed interval for healing batches that straddle the cleanse.
                windows[-1] = start, self.tick

    def heal_area(self, healer, centre, radius: float, amount: float, *, ticks=None) -> None:
        """Heal the healer and his teammates (everyone's own team in a free-for-all is his alone) whose body
        is within `radius` of `centre` (unverified: to the body's capsule)."""
        # Field updates can span expiry. Count only their unblocked ticks.
        first, last = ticks if ticks is not None else (self.tick, self.tick)
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
            self._heal_target_ticks(healer, target, amount, first, last)

    def _heal_target_ticks(self, healer, target, amount, first, last):
        vitals = self.vitals_of(target)
        if vitals.dead:
            return
        steps = last - first + 1
        blocked = sum(max(0, min(last + 1, end) - max(first, start))
                      for start, end in self.antiheal.get(target.entity, ()))
        given = vitals.heal(amount * (steps - blocked) / steps)
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
        *, world_tick: int | None = None, armour_reduction: bool = True,
        beam_armour_scale: float | None = None,
    ) -> None:
        amount *= self.superchargers.scale(shooter)
        shooter = getattr(shooter, "combat_owner", None) or shooter
        vitals = self.vitals_of(target)
        if (vitals.dead or amount <= 0.0 or phase_defenses.phase(target.owner)[0]
                or phase_defenses.transcendence_invulnerable(target.owner, self.tick)):
            return
        adaptive_shield = vitals.temporary_shields.get(torbjorn_charge.ADAPTIVE_SHIELD_TAG)
        shield_before = adaptive_shield.now if adaptive_shield is not None else 0.0
        mei_spray = (loadout == mei_frost.SPRAY_LOADOUT
                     and getattr(getattr(shooter, "hero", None), "guid", None) == mei_frost.HERO)
        if mei_spray:
            beam_armour_scale = BEAM_ARMOUR_SCALE
        defense = fortify.state_for(target.owner)
        if defense is not None:
            amount *= fortify.damage_scale(defense)
        dealt = vitals.damage(amount, armour_reduction=armour_reduction, beam_armour_scale=beam_armour_scale)
        if dealt <= 0.0:
            return
        sleep = self.sleep.get(target.entity)
        damage_tick = self.tick if world_tick is None else world_tick
        if sleep is not None and sleep[0] > damage_tick:
            deadline, earliest = sleep
            recovery = damage_tick + math.ceil(ANA_SLEEP_WAKE_RECOVERY / TICK)
            self.sleep[target.entity] = min(deadline, max(earliest, recovery)), earliest
        hero_torbjorn.turret_damaged(self, target)
        shield_loss = shield_before - adaptive_shield.now if adaptive_shield is not None else 0.0
        torbjorn_charge.damage(self, shooter, target, dealt, loadout, shield_loss)
        killed = vitals.now[HEALTH_POOL] <= EPSILON
        if killed:
            vitals.now[HEALTH_POOL] = 0.0
            vitals.set_over_armour(None)
            vitals.died_at = self.now
            vitals.death_tick = self.match.tick if world_tick is None else world_tick
            vitals.killer = _source_entity(shooter)
        if not isinstance(shooter, bots.Bot):
            flags = (CRITICAL if critical else 0) | (KILLING if killed else 0)
            self._tell_hit(shooter, target, dealt, flags, frame)
            shooter.stats.add(stats.HERO_DAMAGE, dealt)
        victim = target.owner
        if (not killed and mei_spray
                and getattr(getattr(victim, "hero", None), "guid", None) == hero_torbjorn.HERO):
            self.frost.apply(shooter, target, tick=self.tick)
        sombra_control.received_damage(victim, source=_source_entity(shooter))
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
        self.stuns.pop(bot.entity, None)
        self.sleep.pop(bot.entity, None)
        self.steel_traps.retire_victim(bot.entity)
        self.clear_pins(bot.entity)
        hero_torbjorn.turret_killed(self, bot)
        self.superchargers.retire(bot.entity)
        self.halt.release(bot.entity)
        self.hooks.retire(bot.entity)
        bot.died(tick)
        self.gravitons.release_npc(bot)
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
        """A player died: the notices, his body script's "died" (the kill feed line), and the respawn."""
        self.brigitte_shields.retire(victim.body)
        self.hammer_contacts.pop(victim.body, None)
        self.stuns.pop(victim.body, None)
        self.sleep.pop(victim.body, None)
        self.antiheal.pop(victim.body, None)
        if victim.body in self.halt.pulls and victim.mover.driven:
            victim.mover.velocity = (0.0, 0.0, 0.0)
            victim.mover.driven = False
        self.halt.retire(victim.body)
        self.hooks.retire(victim.body)
        sigma_flux.retire_recipient(victim)
        self.gravitons.release_player(victim)
        self.superchargers.retire(victim.body)
        self.frost.retire(victim.body, victim)
        self.steel_traps.retire_victim(victim.body)
        self.sombra_hacks.retire(victim.body)
        self.clear_pins(victim.body)
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
                sigma_flux.cancel_warning(component)
                orisa_supercharger.cancel_windup(component)
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
