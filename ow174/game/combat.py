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
  command's aim from per-body FirstPerson standing/crouched fields where verified (DATA),
  otherwise the existing fallback; camera/muzzle transforms remain unverified. Projectiles
  (m_projectileSpeed) are flown each tick.
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
    bots,
    mei_wall,
    observers,
    orisa_barrier,
    pools,
    projectile_tags,
    projectiles,
    reinhardt_barrier,
    sigma_barrier,
    stats,
    voicelines,
    winston_barrier,
    world,
)
from ow174.game.bits import BitWriter
from ow174.game.brigitte import FLAIL_LOADOUT, WHIP_LOADOUT, BrigitteCombat
from ow174.game.collision import MOVER, World
from ow174.game.commands import Command
from ow174.game.contacts import SKIN
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
#104319 FirstPerson standing/crouched fields; unlisted bodies keep the fallback.
EYE_STANDING = {0x0400000000001B1B: 1.58, 0x04000000000002A3: 1.425}
EYE_CROUCHED = {
    0x04000000000003CF: 1.085, 0x0400000000001B1B: 1.2, 0x04000000000002A3: 0.9975,
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
EPSILON = 1e-9
FLASHBANG_EFFECT = 0x04A0000000000079  # .053/0079 ->0228 ->224E
HELIX_LOADOUT = 0x0B9000000000008E
ACCRETION = 0x1BBB
FLASHBANG_MODE_FLAG = 13408  # 0228 duration: flag set1.7s, otherwise0.8s
BIOTIC_GRENADE_EFFECT = 0x04A0000000000267  # 0B97 -> .053/0267 -> 0C71
BIOTIC_HEAL_BOOST = 1.5  # 0B99 node8, incoming healing
BIOTIC_DURATION = 4.0  # 0B99 and 0C72 state1
BIOTIC_BOOST_DELAY = 0.05  # 0B99 state8
BIOTIC_CLEANSE_VARS = (6604, 8440, 478, 33551)  # 0C72 node8
# Graph -> (authority handoff state, protected phase). The graph owns phase timing.
PROTECTED_PHASES = {
    0x0020: (1, 3),    # Wraith Form
    0x0F7C: (1, 3),    # Fade
    0x05FE: (41, 6),   # Cryo-Freeze
    0x017B: (13, 3),   # Transcendence
}


def eye_height(body: int, crouched: bool = False) -> float:
    if crouched:
        return EYE_CROUCHED.get(body, EYE_CROUCHED_DEFAULT)
    return EYE_STANDING.get(body, EYE)


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
class TemporaryArmour:
    amount: float
    until: float


@dataclass
class TemporaryShield:
    amount: float
    maximum: float
    decay_from: float
    rate: float
    activation: tuple[int, int] | None = None


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
        self.changed: set[int] = set()  # pools that changed since the last update went out
        self.died_at: float | None = None
        self.death_tick: int | None = None
        self.killer: int | None = None
        self.bonus_armour: list[TemporaryArmour] = []
        self.bonus_declared = False
        self.temporary_shield: TemporaryShield | None = None
        self.healing_block = (0.0, 0.0)
        self.healing_boost = (0.0, 0.0)

    def healing_factor(self, now: float) -> float:
        if self.healing_block[0] <= now < self.healing_block[1]:
            return 0.0
        if self.healing_boost[0] <= now < self.healing_boost[1]:
            return BIOTIC_HEAL_BOOST
        return 1.0

    def healing_time(self, start: float, end: float) -> float:
        """Integrate incoming healing across status boundaries on this tick."""
        if end <= start:
            return 0.0
        block_start = max(start, self.healing_block[0])
        block_end = min(end, self.healing_block[1])
        boost_start = max(start, self.healing_boost[0])
        boost_end = min(end, self.healing_boost[1])
        blocked = max(0.0, block_end - block_start)
        boosted = max(0.0, boost_end - boost_start)
        overlap = max(0.0, min(block_end, boost_end) - max(block_start, boost_start))
        return end - start - blocked + (boosted - overlap) * (BIOTIC_HEAL_BOOST - 1.0)

    @property
    def temporary_armour(self) -> float:
        return sum(pool.amount for pool in self.bonus_armour)

    @property
    def maximum(self) -> float:
        shield = self.temporary_shield
        return sum(self.most.values()) + self.temporary_armour + (shield.maximum if shield else 0.0)

    def grant_armour(self, amount: float, cap: float, until: float) -> float:
        """Reconstructed cohorts; healing never refills them. Native pool hosting remains open."""
        give = max(0.0, min(amount, cap - self.temporary_armour))
        if give > 0 and not self.dead:
            self.bonus_armour.append(TemporaryArmour(give, until))
            self.bonus_declared = True
            self.changed.add(ARMOUR_POOL)
            return give
        return 0.0

    def expire_armour(self, now: float) -> float:
        before = self.temporary_armour
        self.bonus_armour[:] = [pool for pool in self.bonus_armour if pool.until > now]
        lost = before - self.temporary_armour
        if lost > 0:
            self.changed.add(ARMOUR_POOL)
        return lost

    @property
    def dead(self) -> bool:
        return self.died_at is not None

    @property
    def total(self) -> float:
        shield = self.temporary_shield
        return sum(self.now.values()) + self.temporary_armour + (shield.amount if shield else 0.0)

    def spend_temporary_shield(self, amount: float) -> float:
        shield = self.temporary_shield
        if shield is None or shield.amount <= 0.0:
            return 0.0
        taken = min(shield.amount, amount)
        if taken > 0.0:
            shield.amount -= taken
            if shield.amount <= EPSILON:
                shield.amount = shield.maximum = 0.0
            self.changed.add(SHIELDS_POOL)
        return taken

    def damage(self, amount: float) -> float:
        """Take a hit from shields, then armour, then health (unverified, as OW1 did: what reaches armour
        while it has some is ARMOUR_BLOCK less, at most halved). Returns what the pools lost."""
        left = max(0.0, amount)
        taken = self.spend_temporary_shield(left)
        left -= taken
        for kind in (SHIELDS_POOL, ARMOUR_POOL, HEALTH_POOL):
            if left <= 0.0:
                break
            if kind == ARMOUR_POOL and self.now[kind] + self.temporary_armour > 0.0:
                left = max(left - ARMOUR_BLOCK, left / 2)
                # Reconstruction: consume temporary cohorts oldest first;
                # apply armor reduction once across the combined armor type.
                for pool in self.bonus_armour:
                    take = min(left, pool.amount)
                    if take > 0:
                        pool.amount -= take
                        taken += take
                        left -= take
                        self.changed.add(kind)
                self.bonus_armour[:] = [pool for pool in self.bonus_armour if pool.amount > EPSILON]
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
        if self.bonus_declared:
            # Extra armor slot; id0 clears it on expiry. Byte18/slot allocation
            # are reconstructed, pending native temporary-pool hosting.
            amount = self.temporary_armour
            part = bytearray(world.pool(max(self.ids.values()) + 1 if amount > 0 else 0,
                                        ARMOUR_POOL, amount, amount))
            part[18] = 1
            parts[ARMOUR_POOL * POOLS + 1] = bytes(part)
        if self.temporary_shield is not None:
            shield = self.temporary_shield
            parts[SHIELDS_POOL * POOLS + 1] = world.pool(
                max(self.ids.values()) + 2, SHIELDS_POOL, shield.maximum, shield.amount,
            )
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
    knockback: tuple[float, float, tuple] | None = None

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
    from ow174.game import mccree

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
        critical=value("m_A872C70E", mccree.critical_default(state)),
        rings=tuple(sorted(rings)),
        effect=effect.guid if isinstance(effect, expr.Asset) else None,
        knockback=_accretion_knockback(state, modify),
    )


def _accretion_knockback(state, modify) -> tuple[float, float, tuple] | None:
    if state.instance.graph.index != ACCRETION or state.node.pos != 9:
        return None
    rows = modify.get("m_knockbackRings") or ()
    if not rows:
        return None

    def value(row, key):
        return _number(state.evaluate(row.get(key), watch=False), 0.0)

    rings = tuple((value(row, "m_radius"), value(row, "m_579D7CFF"), value(row, "m_10399AE7"))
                  for row in rows)
    return value(modify, "m_E33EFEE1"), value(modify, "m_583C208B"), rings


def _accretion_launch(state, direction, speed):
    motions = state.node.field("m_projectileMotions") or ()
    bias = state.node.field("m_5EC1EB38")
    if len(motions) != 1 or motions[0].get("$") != "STU_E1A5B579" or not bias:
        return direction, None, 0.0
    gravity = _number(state.evaluate(motions[0].get("m_599AB8D7"), watch=False), 0.0)
    lift = _number(state.evaluate(bias.get("m_22CE7F30"), watch=False), 0.0)
    x, y, z = (expr.f32(axis) for axis in direction)
    y = expr.f32(y + lift)
    squared = expr.f32(expr.f32(expr.f32(x * x) + expr.f32(y * y)) + expr.f32(z * z))
    direction = (tuple(expr.f32(axis / math.sqrt(squared)) for axis in (x, y, z))
                 if squared >= 1.1754943508222875e-36 else (0.0, -1.0, 0.0))
    return direction, tuple(expr.f32(axis * speed) for axis in direction), gravity


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
    reflected: bool = False
    definition: int | None = None
    accretion_source: tuple[int, int] | None = None
    velocity: tuple[float, float, float] | None = None
    gravity: float = 0.0
    deploy_body: int | None = None


@dataclass
class ProjectedBarrier:
    source: int
    until: float
    health: float


@dataclass(frozen=True)
class PunchImpact:
    source: object
    source_body: int
    direction: tuple[float, float, float]
    damage: float
    deadline: int


@dataclass
class ChargeContact:
    hit: set[int] = field(default_factory=set)
    captured: int | None = None


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
        self.pins: dict[int, int] = {}  # manual Assassin source body -> controlled victim body
        self.stuns: dict[int, int] = {}  # body identity -> exclusive world-tick deadline
        self.punch_impacts: dict[int, PunchImpact] = {}
        self.charge_contacts: dict[int, ChargeContact] = {}
        self.personal_barriers: dict[int, tuple[int, int, float]] = {}  # body -> instance, Pet activation, HP
        self.projected_barriers: dict[int, ProjectedBarrier] = {}
        self.sigma_barriers: dict[int, sigma_barrier.Barrier] = {}
        self.reinhardt_geometry = reinhardt_barrier.configured_geometry()
        self.reinhardt_barriers: dict[int, reinhardt_barrier.Barrier] = {}
        self.walls = mei_wall.Walls(self)
        self.orisa_barriers = orisa_barrier.Barriers(self)
        self.winston_domes: dict[int, winston_barrier.Dome] = {}
        self.projected_casts: dict[tuple[int, int], int] = {}
        self.brigitte = BrigitteCombat(self)
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
        self.stuns.pop(player.body, None)
        for entity, impact in tuple(self.punch_impacts.items()):
            if player.body in (entity, impact.source_body):
                del self.punch_impacts[entity]
        self.personal_barriers.pop(player.body, None)
        self.reinhardt_barriers.pop(player.body, None)
        self.projected_barriers.pop(player.body, None)
        for entity, barrier in list(self.sigma_barriers.items()):
            if barrier.owner_body == player.body:
                del self.sigma_barriers[entity]
        self.clear_pins(player.body)
        self.brigitte.retire(player.body)
        self.orisa_barriers.retire(player.body)
        self.walls.retire(player.body)
        # The owner's leased projectiles and their observer copies retire with the old body.
        self.rockets[:] = [
            rocket for rocket in self.rockets
            if rocket.shooter is not player
            or (rocket.shot is None and not rocket.reflected and rocket.accretion_source is None
                and rocket.deploy_body is None)
        ]
        self.respawns[:] = [(when, who) for when, who in self.respawns if who is not player]

    def record_replacement(self, body: int) -> None:
        """Keep the creation boundary independent of the rolling pose history."""
        self.replacement_ticks[body] = self.match.tick
        self.stuns.pop(body, None)
        self.clear_pins(body)

    def is_stunned(self, actor, *, tick: int | None = None) -> bool:
        body = _source_entity(actor)
        return (body in self.pins.values() or any(c.captured == body for c in self.charge_contacts.values())
                or self.stuns.get(body, 0) > (self.tick if tick is None else tick)
                or self.brigitte.controlled(body))

    def clear_pins(self, entity: int) -> None:
        for source, victim in list(self.pins.items()):
            if entity in (source, victim):
                del self.pins[source]
        self.charge_contacts.pop(entity, None)
        for contact in self.charge_contacts.values():
            if contact.captured == entity:
                contact.captured = None

    def _interrupt(self, target: Target) -> None:
        self.brigitte.interrupted(target.entity)
        self.walls.cancel_preview(target.owner)
        self.interrupt_controlled(getattr(target.owner, "body_script", None))

    def _hit_effect(self, target: Target, effect: int | None) -> None:
        """Resource-backed stun reconstruction; native immunity/presentation remain unverified."""
        if effect != FLASHBANG_EFFECT or self.vitals_of(target).dead or self._invulnerable(target.owner):
            return
        variables = graph.mode_variables(self.match.game_map.mode_guid) or {}
        duration = 1.7 if expr.truthy(variables.get(FLASHBANG_MODE_FLAG)) else 0.8
        deadline = self.tick + math.ceil(duration / TICK)
        self.stuns[target.entity] = max(deadline, self.stuns.get(target.entity, 0))
        self._interrupt(target)

    @staticmethod
    def interrupt_controlled(script) -> None:
        """Reconstructed input/channel interruption, shared by hosted control effects."""
        if script is None:
            return
        component = script.component
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

    @staticmethod
    def _charge_instance(player):
        script = player.body_script
        if script is not None and player.hero.guid == 0x02E0000000000007:
            return next((i for i in script.component.instances.values() if i.graph.index == 0x007D), None)
        return None

    def charge_contact(self, player, command: Command, start, seconds: float) -> None:
        """Resolve ordinary Charge on the existing 007D mover segment."""
        instance = self._charge_instance(player)
        phase = instance.states.get(3) if instance is not None else None
        if phase is None or not phase.active or self.is_stunned(player):
            self._release_charge(player, instance, phase)
            return
        contact = self.charge_contacts.setdefault(player.body, ChargeContact())
        targets = {t.entity: t for t in self._enemies(player)}
        captured = targets.get(contact.captured)
        if captured is None:
            contact.captured = None
        feet = tuple(player.mover.position)
        delta = _sub(feet, start)
        progress = math.hypot(delta[0], delta[2])
        yaw = player.mover.yaw * math.tau / 65536
        forward = (math.sin(yaw), 0.0, math.cos(yaw))
        if progress > EPSILON:
            forward = (delta[0] / progress, 0.0, delta[2] / progress)
            captured = self._charge_acquire(
                player, command, instance, contact, targets, start, forward, progress,
            )

        speed = _number(instance.find_var(0, 7726).value(), 0) * 60
        blocked = progress < speed * seconds / 2 and ray_world(
            self._collision(), _along(feet, (0, 1, 0), 1), forward,
            player.mover.data.stand_radius + speed * seconds + SKIN,
        ) is not None
        if captured is not None:
            mover = captured.owner.mover
            destination = _along(feet, forward, player.mover.data.stand_radius + captured.radius + 0.02)
            distance = math.dist(mover.position, destination)
            mover._move_to(seconds, destination, distance / seconds + 1)
            if mover.history and mover.history[-1].frame == command.frame:
                mover.history[-1] = mover.snapshot(mover.history[-1].frame)
            blocked |= math.dist(mover.position, destination) > 0.03
        if blocked:
            self.charge_contacts.pop(player.body, None)
            if captured is not None:
                self._charge_damage(player, captured, instance, 12, command.frame)
            instance.write((0, 59), 0)
            instance.component.run_now()

    def _charge_acquire(self, player, command, instance, contact, targets, start, forward, progress):
        origin = _along(start, (0, 1, 0), 1)
        radius = _number(instance.find_var(0, 9149).value(), 0)
        width = _number(instance.find_var(0, 11861).value(), 0)
        hits = []
        for target in targets.values():
            if target.entity in contact.hit or _dot(_sub(target.feet, start), forward) < 0:
                continue
            a, b = target.segment()
            hit = ray_capsule(origin, forward, a, b, radius + target.radius)
            if hit is None or hit > progress:
                continue
            centre = _along(target.feet, (0, 1, 0), 1)
            sight = _sub(centre, origin)
            length = math.hypot(*sight)
            if length > EPSILON and ray_world(
                self._collision(), origin, tuple(v / length for v in sight), length,
            ) is not None:
                continue
            hits.append((hit, target.entity, target, a, b))
        for _, _, target, a, b in sorted(hits):
            if self._invulnerable(target.owner):
                continue
            contact.hit.add(target.entity)
            pin = ray_capsule(origin, forward, a, b, width + target.radius)
            script = getattr(target.owner, "body_script", None)
            immunity = script.component.entity_var(2547, create=False) if script is not None else None
            immune = immunity is not None and expr.truthy(immunity.value())
            if (contact.captured is None and pin is not None and pin <= progress + radius
                    and not isinstance(target.owner, bots.Bot) and not self.is_stunned(target.owner)
                    and not immune):
                contact.captured = target.entity
                self._interrupt(target)
            elif (self._charge_damage(player, target, instance, 15, command.frame)
                  and not immune and not self.vitals_of(target).dead):
                # Side contact throws away from a point behind the charger.
                back = _along(start, forward, -5)
                dx, _, dz = _sub(target.feet, back)
                length = math.hypot(dx, dz)
                if length > EPSILON:
                    self._charge_impulse(target, instance, 15, (dx / length, 0.0, dz / length))
        return targets.get(contact.captured)

    def _release_charge(self, player, instance, phase) -> None:
        contact = self.charge_contacts.pop(player.body, None)
        if (contact is None or contact.captured is None or phase is None or phase.active
                or self.is_dead(player) or self.is_stunned(player) or player.gone):
            return
        target = next((t for t in self._enemies(player) if t.entity == contact.captured), None)
        if target is not None:
            yaw = player.mover.yaw * math.tau / 65536
            self._charge_impulse(target, instance, 49, (math.sin(yaw), 0.0, math.cos(yaw)))

    def _charge_impulse(self, target, instance, index, direction) -> None:
        state = instance.state(index)
        modify = state.node.field("m_modifyHealth") or {}
        strength = _number(state.evaluate(modify.get("m_E33EFEE1"), watch=False), 0)
        lift = _number(state.evaluate(modify.get("m_583C208B"), watch=False), 0)
        impulse = (direction[0] * strength, lift, direction[2] * strength)
        if isinstance(target.owner, bots.Bot):
            target.owner.impulse(impulse, self.tick, self._collision())
        else:
            target.owner.mover.add_external_impulse(impulse)

    def _charge_damage(self, player, target, instance, index, frame):
        state = instance.state(index)
        modify = state.node.field("m_modifyHealth") or {}
        amount = _number(state.evaluate(modify.get("m_amount"), watch=False), 0)
        loadout = state.evaluate(modify.get("m_loadout"), watch=False)
        return self._damage(player, target, amount, False,
                            loadout.guid if isinstance(loadout, expr.Asset) else None, frame)

    def punch_contact(self, player, command: Command, start) -> None:
        """Resolve 0D06 contact over the pose segment already admitted by the mover."""
        if self.is_stunned(player):
            return
        component = player.body_script.component
        instance = next((i for i in component.instances.values() if i.graph.index == 0x0D06), None)
        if instance is None:
            return
        motion, tracker = instance.states.get(7), instance.states.get(6)
        if motion is None or not motion.active or tracker is None or not tracker.active:
            return
        delta = _sub(player.mover.position, start)
        distance = math.sqrt(_dot(delta, delta))
        if distance <= EPSILON:
            return
        direction = tuple(v / distance for v in delta)
        origin = _along(start, (0, 1, 0), self._eye(player)[1] - player.mover.position[1] - 0.18)
        node = instance.graph.node(14)
        radius = _number(tracker.evaluate(node.field("m_radius"), watch=False), 0)
        if radius <= 0:
            return
        contacts = []
        for target in self._enemies(player):
            a, b = target.segment()
            hit = ray_capsule(origin, direction, a, b, target.radius + radius)
            if hit is None or hit > distance:
                continue
            centre = tuple((lo + hi) / 2 for lo, hi in zip(a, b, strict=True))
            sight = _sub(centre, origin)
            length = math.sqrt(_dot(sight, sight))
            if length > EPSILON and ray_world(
                self._collision(), origin, tuple(v / length for v in sight), length,
            ) is not None:
                continue
            contacts.append((hit, target.entity, target))
        if not contacts:
            return
        _, _, target = min(contacts)
        helper = graph.graph(0x0D07)
        duration, low, high = (_number(instance.vars[index].value(), 0) for index in (215, 11271, 567))
        if high <= low:
            return
        charge = min(1.0, max(0.0, (duration - low) / (high - low)))

        def scaled(low_node, high_node):
            a, b = (
                _number(instance.evaluate(helper.node(i).field("m_value")), 0) for i in (low_node, high_node)
            )
            return a + (b - a) * charge

        recovery = instance.states.get(5)
        if recovery is not None and recovery.active:
            recovery.request(runtime.FINISH)
        if not self._damage(player, target, scaled(34, 33), False, 0x0B90000000000107, command.frame):
            return
        self._grant_rocket_punch_shield(player)
        if self.vitals_of(target).dead:
            return
        script = getattr(target.owner, "body_script", None)
        immunity = script.component.entity_var(2547, create=False) if script is not None else None
        if immunity is not None and expr.truthy(immunity.value()):
            return
        timeout = _number(instance.evaluate(helper.node(1).field("m_timeout")), 0)
        deadline = self.tick + math.ceil(timeout / TICK)
        self.stuns[target.entity] = max(deadline, self.stuns.get(target.entity, 0))
        self._interrupt(target)
        flat = math.hypot(direction[0], direction[2])
        if flat > EPSILON and hasattr(target.owner, "mover"):
            heading = direction[0] / flat, 0.0, direction[2] / flat
            push = scaled(32, 31)
            target.owner.mover.add_external_impulse(tuple(axis * push for axis in heading))
            self.punch_impacts[target.entity] = PunchImpact(
                player, player.body, heading, scaled(36, 35), deadline,
            )

    def _grant_rocket_punch_shield(self, player) -> None:
        """Reconstruct 0DDD's tagged pool on a confirmed direct Punch hit."""
        component = player.body_script.component
        passive = next((i for i in component.instances.values() if i.graph.index == 0x0DDD), None)
        if passive is None:
            return
        cap_slot = passive.vars.get(11218)
        cap = _number(cap_slot.value() if cap_slot is not None else None, 0)
        gain = _number(passive.evaluate(passive.graph.node(20).field("m_value")), 0)
        rate = _number(passive.evaluate(passive.graph.node(3).field("m_modifyHealth.m_amount")), 0)
        delay = _number(passive.evaluate(passive.graph.node(2).field("m_timeout")), 0)
        if not all(math.isfinite(value) and value >= 0 for value in (cap, gain, rate, delay)):
            return
        vitals = self.vitals[player.body]
        shield = vitals.temporary_shield
        old = shield.amount if shield is not None else 0.0
        given = min(gain, max(0.0, cap - old))
        if given > 0:
            current = old + given
            vitals.temporary_shield = TemporaryShield(current, current, self.now + delay, rate)
            vitals.changed.add(SHIELDS_POOL)
            self._sync_body_health(player, vitals)

    def punch_wall_contact(self, player, command: Command, tick: int) -> None:
        """Consume one pending wall hit at the victim's admitted pose."""
        impact = self.punch_impacts.get(player.body)
        if impact is None or tick >= impact.deadline:
            return
        target = next((t for t in self._targets() if t.entity == player.body), None)
        if target is None or self.vitals_of(target).dead:
            return
        origin = _along(target.feet, (0, 1, 0), target.height / 2)
        if ray_world(self._collision(), origin, impact.direction, target.radius + SKIN + STEP) is None:
            return
        del self.punch_impacts[player.body]
        self._damage(impact.source, target, impact.damage, False, 0x0B90000000000107, command.frame)

    def spawned(self, player) -> None:
        """The player's client has the world: the game mode entity gets its UI graph (mode_frame). While
        heroes are assembled the countdown's frames carry it."""
        if player.client is not None and not self.match.assembling():
            player.client.queue_mode_frame(mode_frame(
                {}, player.match.game_map.mode_guid, player.match.game_map.map_guid,
            ))
        self.voice.spawned(player, self.now)

    def prepare(self, player, command: Command) -> None:
        """Supply live target services before this posed input advances its script."""
        if self.match.collision_world is None or player.body_script is None or self.is_dead(player):
            return
        try:
            self.brigitte.prepare(player, command, player.body_script)
        except Exception:
            log.exception("[game] %s: %s's combat targets failed", self.match.label(), player.name)

    def command(self, player, command: Command | None) -> None:
        """Consume the body's reports. Only a frame with a pose can affect targets."""
        if self.match.collision_world is None:
            return
        script = player.body_script
        if script is None:
            return
        shots, script.component.shots[:] = list(script.component.shots), []
        if self.is_dead(player):
            return
        if player.hero.guid == 0x02E0000000000068:
            self._sync_projected_barrier(player, script)
        reinhardt_barrier.sync(self, player)
        self.brigitte.passive_command(player, script)
        if self.is_stunned(player):
            return
        if command is not None:
            self._advance_protected_phases(script.component)
            self._cleanse_biotic_block(player, self.vitals.get(player.body))
            if self.walls.command(player, command):
                return
            for state, number, time, activation in shots:
                try:
                    self._shoot(player, command, state, number, time, activation=activation)
                except Exception:
                    log.exception("[game] %s: %s's shot failed", self.match.label(), player.name)
        try:
            self._melee(player, command, script)
            self.brigitte.command(player, command, script)
        except Exception:
            log.exception("[game] %s: %s's melee failed", self.match.label(), player.name)

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
            self._decay_temporary_shields()
            sigma_barrier.update_barriers(self)
            self.walls.update(at)
            for body, dome in tuple(self.winston_domes.items()):
                if dome.health <= 0 or self.now >= dome.until:
                    del self.winston_domes[body]
            for rocket in list(self.rockets):
                if at == tick or rocket.last_tick is not None:
                    self._fly(rocket)
            self.orisa_barriers.update(at)
            self._bot_weapons(at)
            self.brigitte.update()
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
        for entity, barrier in list(self.projected_barriers.items()):
            if entity not in current or self.now >= barrier.until:
                del self.projected_barriers[entity]
        for key in list(self.projected_casts):
            if key[0] not in current:
                del self.projected_casts[key]
        if self.personal_barriers:
            active = {p.body for p in self.match.players if p.body in self.personal_barriers
                      and self._personal_barrier_pet(p) is not None}
            for body in self.personal_barriers.keys() - active:
                del self.personal_barriers[body]
        for entity in [key for key, deadline in self.stuns.items() if deadline <= tick]:
            del self.stuns[entity]
        for player in self.match.players:
            if player.body not in self.charge_contacts:
                continue
            instance = self._charge_instance(player)
            phase = instance.states.get(3) if instance is not None else None
            if phase is None or not phase.active or self.is_dead(player) or self.is_stunned(player):
                self._release_charge(player, instance, phase)
        for entity, impact in tuple(self.punch_impacts.items()):
            if (impact.deadline <= tick or entity not in current or impact.source_body not in current
                    or self.vitals[entity].dead):
                del self.punch_impacts[entity]
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
        direct = hit_def(state, node.field("m_6396149F"))
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
            accretion_source, velocity, gravity = None, None, 0.0
            if state.instance.graph.index == ACCRETION and node.pos == 9 and not npc:
                accretion_source = shooter.body, state.instance.id
                direction, velocity, gravity = _accretion_launch(state, direction, speed)
            deployment = (
                not npc and self.orisa_barriers.geometry is not None
                and state.instance.graph.index == orisa_barrier.GRAPH
                and orisa_barrier.DEFINITION in projectiles._defs(node.field("m_49CBA3BC"))
            )
            if deployment:
                gravity = sum(
                    _number(state.evaluate(motion.get("m_599AB8D7"), watch=False), 0.0)
                    for motion in node.field("m_projectileMotions") or ()
                    if motion.get("$") == "STU_E1A5B579"
                    and not expr.truthy(state.evaluate(motion.get("m_D0C4CF49"), watch=False))
                )
                velocity = tuple(expr.f32(axis * speed) for axis in direction)
            shot = None
            if definition is not None:
                shot = self.projectiles.fired(
                    shooter, state, shot_frame, definition, lifetime, origin, direction,
                    activation=activation, snapshot=snapshot,
                )
            damaging = (direct is not None and direct.amount > 0.0) or (
                blast is not None and blast.amount > 0.0
            )
            if damaging or shot is not None or deployment:
                self.rockets.append(
                    Rocket(
                        shooter, shooter.team, origin, direction, speed, lifetime, direct, blast, shot,
                        last_tick=self.tick if npc else None,
                        definition=definition, accretion_source=accretion_source,
                        velocity=velocity, gravity=gravity,
                        deploy_body=shooter.body if deployment else None,
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
        self._hitscan(shooter, origin, direction, reach, direct, view_tick, shot_frame,
                      world_tick=self.tick if npc else None, definition=definition)

    def _hitscan(self, shooter, origin, direction, reach, direct, view_tick, shot_frame,
                 *, world_tick=None, reflected=False, definition=None):
        best = None
        for target in self._enemies(shooter):
            shown = self.seen(target, view_tick)
            if shown is None:
                continue
            a, b = shown.segment()
            t = ray_capsule(origin, direction, a, b, shown.radius)
            if t is not None and t <= reach and (best is None or t < best[0]):
                best = (t, target, shown)
        limit = min(reach, best[0] if best else reach)
        redirect = None
        if direct.loadout == 0x0B9000000000008D:
            redirect = self._trace_deflection(shooter, origin, direction, limit, view_tick=view_tick)
            if redirect is not None:
                limit = redirect[0]
        absorption = self._trace_absorption(
            shooter, definition, origin, direction, limit,
            grasp=direct.loadout == 0x0B9000000000008D, view_tick=view_tick,
        )
        if absorption is not None and absorption[0] < limit:
            limit = absorption[0]
            redirect = None
        else:
            absorption = None
        wall = ray_world(self._collision(), origin, direction, limit)
        ice = self.walls.cast(origin, direction, limit if wall is None else wall)
        if ice is not None and (wall is None or ice[0] < wall):
            wall = ice[0]
        else:
            ice = None
        barrier = self.brigitte.barrier_ray(shooter, origin, direction, wall if wall is not None else limit)
        bound = barrier[0] if barrier is not None else wall if wall is not None else limit
        deployed = sigma_barrier.barrier_intersection(self, shooter, origin, direction, bound)
        if deployed is not None and (
            deployed[0] < bound or (barrier is None and wall is None and best is None
                                   and redirect is None and absorption is None)
        ):
            distance, actor = deployed
            sigma_barrier.damage_barrier(self, actor, direct.amount * direct.scale(distance))
            return
        if barrier is not None:
            distance, shield = barrier
            self.brigitte.damage_barrier(shield, direct.amount * direct.scale(distance))
            return
        if ice is not None:
            self.walls.damage(ice[1], shooter, direct.amount * direct.scale(ice[0]))
            return
        if absorption is not None:
            if wall is None:
                self._absorbed(absorption[1], direct.amount * direct.scale(limit))
            return
        if redirect is not None and wall is None:
            distance, target = redirect
            if not reflected and reach > distance:
                owner = target.owner
                facing = aim(owner.mover.yaw, owner.mover.pitch)
                self._hitscan(
                    owner, self._eye(owner), facing, reach - distance, direct, self.tick, shot_frame,
                    world_tick=self.tick, reflected=True, definition=definition,
                )
            return
        if best is None or (wall is not None and wall < best[0]):
            return
        t, target, shown = best
        hit_y = origin[1] + direction[1] * t
        critical = hit_y >= shown.feet[1] + shown.eye - HEAD_BELOW
        amount = direct.amount * direct.scale(t) * (direct.critical if critical else 1.0)
        landed = self._damage(
            shooter, target, amount, critical and direct.critical > 1.0, direct.loadout, shot_frame,
            world_tick=world_tick,
        )
        if landed:
            self._hit_effect(target, direct.effect)

    def _fly(self, rocket: Rocket) -> None:
        """Sweep elapsed world time against the current bodies and static collision."""
        # Player input can arrive immediately before the scheduler accounts for a gap.
        elapsed = 1 if rocket.last_tick is None else self.tick - rocket.last_tick
        if elapsed <= 0:
            return
        rocket.last_tick = self.tick
        duration = min(elapsed * TICK, rocket.left)
        if duration <= 0.0:
            self._landed(rocket)
            return
        if rocket.velocity is None:
            self._fly_slice(rocket, duration, self.tick)
            return
        # Integrate authored gravity on fixed ticks even across scheduler gaps.
        for tick in range(self.tick - elapsed + 1, self.tick + 1):
            step = min(TICK, rocket.left)
            vx, vy, vz = rocket.velocity
            vy = expr.f32(vy - expr.f32(rocket.gravity * expr.f32(step)))
            rocket.velocity = vx, vy, vz
            rocket.speed = math.sqrt(vx * vx + vy * vy + vz * vz)
            if rocket.speed > EPSILON:
                rocket.direction = tuple(axis / rocket.speed for axis in rocket.velocity)
            self._fly_slice(rocket, step, tick)
            if rocket not in self.rockets:
                break

    def _fly_slice(self, rocket: Rocket, duration: float, tick: int) -> None:
        if rocket.deploy_body is not None:
            step = rocket.speed * duration
            wall = ray_world(self._collision(), rocket.position, rocket.direction, step)
            if wall is not None:
                rocket.position = _along(rocket.position, rocket.direction, wall)
                rocket.flown += wall / rocket.speed
                self.orisa_barriers.landed(rocket, tick)
                self._landed(rocket)
                return
            rocket.position = _along(rocket.position, rocket.direction, step)
            rocket.flown += duration
            rocket.left -= duration
            if rocket.left <= EPSILON:
                self._landed(rocket)
            return
        # Re-sweep the unspent slice after redirects. Bound overlapping
        # deflectors that could repeatedly collide at distance zero.
        for _ in range(8):
            duration = self._sweep_rocket(rocket, duration)
            if duration <= EPSILON:
                return
        rocket.flown += duration
        rocket.left -= duration
        if rocket.left <= EPSILON:
            self._landed(rocket)

    def _sweep_rocket(self, rocket: Rocket, duration: float) -> float:
        """Advance a slice and return any unspent time after reflection."""
        # Sweep the remaining flight even when the range or lifetime ends inside this tick.
        step = rocket.speed * duration
        blast = rocket.blast
        biotic = blast is not None and blast.effect == BIOTIC_GRENADE_EFFECT
        candidates = self._targets() if biotic else self._enemies(rocket.shooter)
        best = None
        for target in candidates:
            if target.owner is rocket.shooter or self.vitals_of(target).dead:
                continue
            a, b = target.segment()
            t = ray_capsule(rocket.position, rocket.direction, a, b, target.radius)
            if t is not None and t <= step and (best is None or t < best[0]):
                best = (t, target)
        wall = ray_world(self._collision(), rocket.position, rocket.direction, step)
        if wall is not None and (best is None or wall < best[0]):
            best = (wall, None)
        ice = self.walls.cast(rocket.position, rocket.direction, best[0] if best else step)
        if ice is not None and (best is None or ice[0] < best[0]):
            best = (ice[0], None)
        else:
            ice = None
        redirect = None
        if (biotic or (blast is not None and blast.effect == FLASHBANG_EFFECT)
                or (rocket.direct is not None and rocket.direct.loadout == HELIX_LOADOUT)):
            limit = best[0] if best else step
            candidate = self._trace_deflection(rocket.shooter, rocket.position, rocket.direction, limit)
            if candidate is not None and (best is None or candidate[0] < best[0]):
                redirect = candidate
                best = candidate
        limit = best[0] if best else step
        absorption = self._trace_absorption(
            rocket.shooter, rocket.definition, rocket.position, rocket.direction, limit,
            grasp=rocket.direct is not None and rocket.direct.loadout == HELIX_LOADOUT,
        )
        if absorption is not None and (best is None or absorption[0] < limit):
            best = absorption[0], None
            redirect = None
        else:
            absorption = None
        shield = self.brigitte.barrier_ray(
            rocket.shooter, rocket.position, rocket.direction, min(step, best[0] if best else step),
        )
        if shield is not None:
            best = shield[0], None
            redirect = None
            absorption = None
        limit = best[0] if best else step
        deployed = sigma_barrier.barrier_intersection(
            self, rocket.shooter, rocket.position, rocket.direction, limit,
        )
        if deployed is not None and (best is None or deployed[0] < limit):
            best = deployed[0], None
            shield = redirect = absorption = None
        else:
            deployed = None
        if best is None:
            rocket.position = _along(rocket.position, rocket.direction, step)
            rocket.flown += duration
            rocket.left -= duration
            if rocket.left <= 0.0:
                self._landed(rocket)
            return 0.0
        t, struck = best
        rocket.flown += t / rocket.speed
        if absorption is not None:
            self.rockets.remove(rocket)
            if rocket.shot is not None:
                self.projectiles.absorbed(rocket.shot)
            amount = sum(hit.amount for hit in (rocket.direct, blast) if hit is not None)
            self._absorbed(absorption[1], amount)
            return 0.0
        if redirect is not None:
            if rocket.shot is not None:
                self.projectiles.flown(rocket.shot, rocket.flown)
                rocket.shot = None
            point = _along(rocket.position, rocket.direction, t)
            rocket.shooter, rocket.team = struck.owner, struck.team
            rocket.direction = aim(struck.owner.mover.yaw, struck.owner.mover.pitch)
            rocket.position = _along(point, rocket.direction, 1e-4)
            rocket.reflected = True
            spent = min(duration, (t + 1e-4) / rocket.speed)
            rocket.flown += spent - t / rocket.speed
            rocket.left -= spent
            if rocket.left <= EPSILON:
                self._landed(rocket)
                return 0.0
            return duration - spent
        self._landed(rocket)
        # Keep splash sight rays outside the surface that ended this sweep;
        # casting exactly from a triangle contact would hit that triangle at0.
        point = _along(rocket.position, rocket.direction, max(0.0, t - 1e-4))
        broken_particle_barriers: set[int] = set()
        if struck is not None and rocket.direct is not None and rocket.direct.amount > 0.0:
            before = self.vitals_of(struck).total if rocket.accretion_source is not None else 0.0
            landed = self._damage(
                rocket.shooter, struck, rocket.direct.amount, False, rocket.direct.loadout, self.tick,
                world_tick=self.tick, broken_particle_barriers=broken_particle_barriers,
            )
            if landed:
                self._hit_effect(struck, rocket.direct.effect)
                if rocket.accretion_source is not None:
                    self._accretion_hit(rocket, struck, before - self.vitals_of(struck).total)
                    self._apply_accretion_impulse(rocket, struck, rocket.direct)
        if ice is not None and (shield is not None or deployed is not None or t != ice[0]):
            ice = None
        ice_damage = rocket.direct.amount if ice is not None and rocket.direct is not None else 0.0
        shield_damage = [(shield[1], rocket.direct.amount)] if shield and rocket.direct else []
        deployed_damage = rocket.direct.amount if deployed is not None and rocket.direct is not None else 0.0
        if blast is None or blast.amount <= 0.0:
            if ice is not None:
                self.walls.damage(ice[1], rocket.shooter, ice_damage, team=rocket.team)
            for barrier, amount in shield_damage:
                self.brigitte.damage_barrier(barrier, amount)
            if deployed is not None:
                sigma_barrier.damage_barrier(self, deployed[1], deployed_damage)
            return 0.0
        # Resolve all cover against the shields present at impact. A shield
        # breaking on this blast does not spill that same blast into its owner.
        shield_damage.extend(self.brigitte.blast_barriers(rocket.shooter, point, blast))
        deployed_splash = sigma_barrier.blast_contacts(
            self, rocket.shooter, point, blast, struck=deployed[1] if deployed is not None else None,
        )
        victims = []
        for target in candidates:
            if self.vitals_of(target).dead:
                continue
            a, b = target.segment()
            distance = max(0.0, segment_distance(point, a, b) - target.radius)
            scalar = blast.splash(distance)
            if scalar <= 0.0:
                continue
            centre = (a[0], min(b[1], max(a[1], point[1])), a[2])
            delta = _sub(centre, point)
            length = math.sqrt(_dot(delta, delta))
            if length > EPSILON:
                direction = tuple(value / length for value in delta)
                wall = self.ray_obstruction(point, direction, length)
                if wall is not None and wall < length - target.radius:
                    continue
                if self.brigitte.barrier_ray(rocket.shooter, point, direction, length) is not None:
                    continue
                if sigma_barrier.barrier_intersection(
                    self, rocket.shooter, point, direction, max(0.0, length - target.radius),
                ) is not None:
                    continue
            victims.append((target, blast.amount * scalar))
        if ice is not None:
            self.walls.damage(ice[1], rocket.shooter, ice_damage + blast.amount * blast.splash(0.0),
                              team=rocket.team)
        for barrier, amount in shield_damage:
            self.brigitte.damage_barrier(barrier, amount)
        for barrier, amount in deployed_splash:
            sigma_barrier.damage_barrier(self, barrier, amount)
        if deployed is not None:
            amount = deployed_damage + blast.amount * blast.splash(0.0)
            sigma_barrier.damage_barrier(self, deployed[1], amount)
        if biotic:
            self._biotic_impact(rocket.shooter, blast, victims, broken_particle_barriers)
        else:
            for target, amount in victims:
                if self._damage(
                    rocket.shooter, target, amount, False, blast.loadout, self.tick, world_tick=self.tick,
                    broken_particle_barriers=broken_particle_barriers,
                ):
                    self._hit_effect(target, blast.effect)
                    if rocket.accretion_source is not None and (
                        target is not struck or rocket.direct is None or rocket.direct.knockback is None
                    ):
                        self._apply_accretion_impulse(rocket, target, blast, point=point)
        return 0.0

    def _accretion_hit(self, rocket: Rocket, target: Target, dealt: float) -> None:
        """Feed the tagged direct damage to the caster's authored control entry."""
        body, identifier = rocket.accretion_source
        shooter = rocket.shooter
        script = shooter.body_script
        if shooter.body != body or script is None or dealt <= 0 or self.vitals_of(target).dead:
            return
        component = script.component
        instance = component.instances.get(identifier)
        if instance is None or instance.stopped or instance.graph.index != ACCRETION:
            return
        hit = instance.graph.node(9).field("m_6396149F")["m_modifyHealth"]
        tags = tuple(instance.evaluate(row["m_EE3055BD"]) for row in hit["m_148F3152"])
        message = expr.guid_of(instance.graph.node(17).field("m_gameMessage")["m_gameMessage"])
        runtime.send_instance_message(instance, message, {
            0x15: expr.Entity(target.entity), 0x13: dealt, 0x3D: tags,
        }, body)
        component.run_now()

    def _apply_accretion_impulse(self, rocket: Rocket, target: Target, hit: HitDef, *, point=None) -> None:
        strength = hit.knockback
        if strength is None or self.vitals_of(target).dead:
            return
        owner = target.owner
        horizontal, vertical, rings = strength
        dx, _, dz = rocket.direction
        distance = 0.0
        if point is not None:
            dx, dz = target.feet[0] - point[0], target.feet[2] - point[2]
            distance = max(0.0, segment_distance(point, *target.segment()) - target.radius)
        previous_radius = 0.0
        previous_xz, previous_y = rings[0][1:]
        for radius, xz, y in rings:
            if distance <= radius:
                width = radius - previous_radius
                share = (distance - previous_radius) / width if width > 0 else 0
                horizontal *= previous_xz + (xz - previous_xz) * share
                vertical *= previous_y + (y - previous_y) * share
                break
            previous_radius, previous_xz, previous_y = radius, xz, y
        else:
            return
        if not (horizontal or vertical):
            return
        length = math.hypot(dx, dz)
        scale = horizontal / length if length > EPSILON else 0.0
        impulse = dx * scale, vertical, dz * scale
        if isinstance(owner, bots.Bot):
            owner.impulse(
                impulse, self.tick, self._collision(), free_for_all=self.match.game_map.free_for_all,
            )
        else:
            owner.mover.add_external_impulse(impulse)

    def _biotic_impact(self, shooter, blast: HitDef, victims, broken_particle_barriers=None) -> None:
        """Reconstruct 0C71 relation dispatch for the normal hero profile."""
        self.brigitte._heal_effects(self.brigitte.inspire)
        self.brigitte._heal_effects(self.brigitte.repairs)
        for target, amount in victims:
            vitals = self.vitals_of(target)
            ally = target.owner is shooter or (
                not self.match.game_map.free_for_all and target.team == shooter.team
            )
            scalar = amount / blast.amount
            if ally:
                # 0B99 replaces the old boost before the 100-point impact heal.
                vitals.healing_boost = (0.0, 0.0)
                given = vitals.heal(100.0 * scalar * vitals.healing_factor(self.now))
                if given:
                    if not isinstance(shooter, bots.Bot):
                        shooter.stats.add(stats.HEALING, given)
                    self._sync_body_health(target.owner, vitals)
                vitals.healing_boost = self.now + BIOTIC_BOOST_DELAY, self.now + BIOTIC_DURATION
            elif self._damage(
                shooter, target, 60.0 * scalar, False, blast.loadout, self.tick, world_tick=self.tick,
                broken_particle_barriers=broken_particle_barriers,
            ) and not vitals.dead:
                # 0C72 owns 60 damage and a zero incoming-healing multiplier.
                vitals.healing_block = self.now, self.now + BIOTIC_DURATION
                self._cleanse_biotic_block(target.owner, vitals)

    def _cleanse_biotic_block(self, owner, vitals: Vitals | None) -> None:
        if vitals is None or self.now >= vitals.healing_block[1]:
            return
        if self._personal_barrier_pet(owner) is not None:
            vitals.healing_block = vitals.healing_block[0], self.now
            return
        component = getattr(getattr(owner, 'body_script', None), 'component', None)
        if not isinstance(component, runtime.Component):
            return
        for key in BIOTIC_CLEANSE_VARS:
            slot = component.entity_var(key, create=False)
            if slot is not None and expr.truthy(slot.value()):
                vitals.healing_block = vitals.healing_block[0], self.now
                break

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
            given = vitals.heal(amount * vitals.healing_factor(self.now))
            if given <= 0.0:
                continue
            healer.stats.add(stats.HEALING, given)
            self._sync_body_health(target.owner, vitals)

    @staticmethod
    def _sync_body_health(owner, vitals: Vitals) -> None:
        script = getattr(owner, "body_script", None)
        if script is not None:
            script.set_health(vitals.total, vitals.maximum)

    def ray_obstruction(self, origin, direction, length):
        terrain = ray_world(self._collision(), origin, direction, length)
        ice = self.walls.cast(origin, direction, length if terrain is None else terrain)
        return ice[0] if ice is not None else terrain

    def _collision(self):
        from ow174.game.collision import CollisionError

        world = self.match.collision_world
        if world is None:
            raise CollisionError("match collision is not ready")
        return world

    # --- damage and death --------------------------------------------------------------------------

    @staticmethod
    def _advance_protected_phases(component: runtime.Component) -> None:
        """Supply a scene-side handoff for four authored zero-delay wrappers."""
        pending = []
        for instance in component.instances.values():
            spec = PROTECTED_PHASES.get(instance.graph.index)
            if spec is None:
                continue
            wrapper = instance.states.get(spec[0])
            if (isinstance(wrapper, nodes.ConditionalTimer) and wrapper.active and not wrapper.ending
                    and wrapper.node.field('m_7C8AD485') is None
                    and not expr.truthy(wrapper.evaluate(wrapper.node.field('m_ED97A97C'), watch=False))):
                pending.append(wrapper)
        for wrapper in pending:
            wrapper.request_finish()
        if pending:
            component.run_now()

    @staticmethod
    def _invulnerable(owner, *, phases: frozenset[int] | None = None) -> bool:
        component = getattr(getattr(owner, 'body_script', None), 'component', None)
        if not isinstance(component, runtime.Component) or component.owner_dead:
            return False
        for instance in component.instances.values():
            spec = PROTECTED_PHASES.get(instance.graph.index)
            if spec is None or (phases is not None and instance.graph.index not in phases):
                continue
            phase = instance.states.get(spec[1])
            if phase is not None and phase.active and not phase.ending:
                return True
        return False

    @staticmethod
    def _deflect_state(owner) -> runtime.State | None:
        if getattr(getattr(owner, 'hero', None), 'guid', None) != 0x02E0000000000029:
            return None
        component = getattr(getattr(owner, 'body_script', None), 'component', None)
        if not isinstance(component, runtime.Component) or component.owner_dead:
            return None
        for instance in component.instances.values():
            if instance.graph.index != 0x03A3:
                continue
            state = instance.states.get(10)
            if state is None or not state.active or state.ending:
                continue
            return state
        return None

    @staticmethod
    def _deflect_cosine(state: runtime.State) -> float:
        angle = _number(state.evaluate(state.node.field('m_025D0C39'), watch=False), 0.0)
        return math.cos(math.radians(angle))

    def _deflects(self, target: Target, origin) -> bool:
        """Check facing on the supported melee contacts."""
        owner = target.owner
        state = self._deflect_state(owner)
        if state is None:
            return False
        toward = _sub(origin, self._eye(owner))
        distance = math.sqrt(_dot(toward, toward))
        facing = aim(owner.mover.yaw, owner.mover.pitch)
        return _dot(facing, toward) >= distance * self._deflect_cosine(state)

    def _trace_deflection(self, shooter, origin, direction, reach, *, view_tick=None):
        from ow174.game.interception import box_contact

        best = None
        for target in self._enemies(shooter):
            owner = target.owner
            state = self._deflect_state(owner)
            if state is None:
                continue
            facing = aim(owner.mover.yaw, owner.mover.pitch)
            if -_dot(facing, direction) + EPSILON < self._deflect_cosine(state):
                continue
            shown = target if view_tick is None else self.seen(target, view_tick)
            if shown is None:
                continue
            distance = box_contact(target, state, origin, direction, reach, shown=shown)
            if distance is not None and (best is None or distance < best[0]):
                best = distance, target
        return best

    def _trace_absorption(
        self, shooter, definition, origin, direction, reach, *, grasp=False, view_tick=None,
    ):
        from ow174.game.interception import box_contact

        matrix = projectile_tags.absorbable(definition)
        if not matrix and not grasp:
            return None
        nearest = None
        for target in self._enemies(shooter):
            hero = getattr(target.owner, "hero", None)
            if matrix and getattr(hero, "body", None) == 0x0400000000000407:
                graph_index, state_index = 0x02CC, 3
            elif grasp and getattr(hero, "guid", None) == 0x02E000000000023B:
                graph_index, state_index = 0x1D02, 1
            else:
                continue
            script = target.owner.body_script
            if script is None or script.retired or script.component.owner_dead:
                continue
            shown = target if view_tick is None else self.seen(target, view_tick)
            if shown is None:
                continue
            for instance in script.component.instances.values():
                if instance.graph.index != graph_index or instance.stopped:
                    continue
                state = instance.states.get(state_index)
                if (state is not None and state.active and not state.ending
                        and state.node.cls == "STUStatescriptStateDeflectProjectiles"):
                    distance = box_contact(target, state, origin, direction, reach, shown=shown)
                    if distance is not None and (nearest is None or distance < nearest[0]):
                        nearest = distance, state
        return nearest

    @staticmethod
    def _absorbed(state: runtime.State, amount: float) -> None:
        instance = state.instance
        if instance.graph.index == 0x1D02:
            instance.write((expr.INSTANCE, 6584), amount)
            instance.follow(state.node, "m_160B5DBE", state)
            instance.component.run_now()

    def grant_grasp_shield(self, instance: runtime.Instance, node) -> None:
        """Host the self-targeted 1D03 request from Grasp's authored exit."""
        entity = instance.component.entity
        if (instance.evaluate(node.field("m_script")) != expr.Asset(0x0580000000001D03)
                or instance.evaluate(node.field("m_targets")) != expr.Entity(entity)):
            return
        state = instance.states.get(1)
        if state is None or not state.counter or instance.component.owner_dead:
            return
        target = next((target for target in self._targets() if target.entity == entity), None)
        script = getattr(target.owner, "body_script", None) if target is not None else None
        if script is None or script.component is not instance.component:
            return
        vitals = self.vitals_of(target)
        activation = instance.id, state.counter
        shield = vitals.temporary_shield
        if vitals.dead or (shield is not None and shield.activation == activation):
            return
        params = {graph.guid(entry["m_B5051BCE"]): expr.to_float(instance.evaluate(entry["m_value"]))
                  for entry in node.field("m_params") or ()}
        amount, cap = params[0x0A4000000000002A], params[0x0A400000000003D1]
        delay = expr.to_float(instance.evaluate(instance.graph.node(71).field("m_timeout")))
        rate = expr.to_float(instance.evaluate(instance.graph.node(70).field("m_modifyHealth")["m_amount"]))
        if not all(math.isfinite(value) and value >= 0.0 for value in (amount, cap, delay, rate)):
            return
        old = shield.amount if shield is not None else 0.0
        current = old + min(amount, max(0.0, cap - old))
        if shield is not None:
            shield.activation = activation
        if current > old:
            vitals.temporary_shield = TemporaryShield(
                current, current, instance.component.now / 1000 + delay, rate, activation,
            )
            vitals.changed.add(SHIELDS_POOL)
            self._sync_body_health(target.owner, vitals)

    def _decay_temporary_shields(self) -> None:
        for player in self.match.players:
            vitals = self.vitals.get(player.body)
            if vitals is None or vitals.dead or vitals.temporary_shield is None:
                continue
            shield = vitals.temporary_shield
            if shield.amount <= 0.0 or self.now <= shield.decay_from:
                continue
            amount = (self.now - shield.decay_from) * shield.rate
            shield.decay_from = self.now
            if vitals.spend_temporary_shield(amount) > 0:
                self._sync_body_health(player, vitals)

    @staticmethod
    def _personal_barrier_pet(owner) -> runtime.State | None:
        if getattr(getattr(owner, 'hero', None), 'guid', None) != 0x02E0000000000068:
            return None
        component = getattr(getattr(owner, 'body_script', None), 'component', None)
        if not isinstance(component, runtime.Component) or component.owner_dead:
            return None
        for instance in component.instances.values():
            if instance.graph.index == 0x0244:
                pet = instance.states.get(2)  # node8 opens Pet13F4 for the authored Chase lifetime
                if pet is not None and pet.active and not pet.ending:
                    return pet
        return None

    def projected_barrier_targets(self, body: int, state: runtime.State) -> tuple:
        """Reconstruct the authored ally search using the current aim and static collision."""
        caster = next((player for player in self.match.players
                       if not player.gone and player.has_body and player.body == body), None)
        if (caster is None or self._collision() is None or self.match.game_map.free_for_all
                or self.is_dead(caster) or self.is_stunned(caster)):
            return ()
        radius = _number(state.evaluate(state.node.field("m_radius"), watch=False), 0.0)
        scale = state.instance.component.entity_var(9595, create=False)
        value = scale.value() if scale is not None else None
        angle = 10.0 * (expr.to_float(value) if value is not None else 1.0)
        if not math.isfinite(radius) or radius <= 0 or not 0 <= angle <= 180:
            return ()
        minimum = math.cos(math.radians(angle))
        origin, direction = self._eye(caster), aim(caster.mover.yaw, caster.mover.pitch)
        candidates = []
        for target in self._targets():
            if (target.owner is caster or target.team != caster.team or self.vitals_of(target).dead
                    or self._active_projected_barrier(target.entity)):
                continue
            script = target.owner.body_script
            if script is not None:
                flags = (script.component.entity_var(key, create=False) for key in (6600, 11264))
                if any(slot is not None and expr.truthy(slot.value()) for slot in flags):
                    continue
            centre = (target.feet[0], target.feet[1] + target.height / 2, target.feet[2])
            delta = _sub(centre, origin)
            distance = math.sqrt(_dot(delta, delta))
            if not EPSILON < distance <= radius:
                continue
            alignment = _dot(direction, delta) / distance
            if alignment < minimum:
                continue
            wall = ray_world(self._collision(), origin, tuple(axis / distance for axis in delta), distance)
            if wall is None or wall >= distance - EPSILON:
                candidates.append((-alignment, distance, target.entity))
        return (expr.Entity(min(candidates)[2]),) if candidates else ()

    def _active_projected_barrier(self, entity):
        barrier = self.projected_barriers.get(entity)
        return barrier if barrier is not None and barrier.health > 0 and self.now < barrier.until else None

    def _sync_projected_barrier(self, caster, script) -> None:
        for instance in script.component.instances.values():
            if instance.graph.index != 0x025C or instance.stopped:
                continue
            pet, effect = instance.states.get(10), instance.states.get(18)
            if pet is None or not pet.active or effect is None or not effect.active:
                continue
            key = caster.body, instance.id
            if self.projected_casts.get(key) == pet.counter:
                continue
            remaining = instance.vars.get(215)
            seconds = expr.to_float(remaining.value()) if remaining is not None else 0.0
            if not math.isfinite(seconds) or seconds <= 0:
                continue
            self.projected_casts[key] = pet.counter
            recipients = instance.evaluate(effect.node.field("m_targets"))
            recipients = (recipients,) if isinstance(recipients, expr.Entity) else recipients
            if not isinstance(recipients, tuple) or self.match.game_map.free_for_all:
                continue
            capacity = self._particle_barrier_capacity()
            if capacity <= 0:
                continue
            for target in self._targets():
                if (target.owner is caster or target.team != caster.team
                        or expr.Entity(target.entity) not in recipients or self.vitals_of(target).dead
                        or self._active_projected_barrier(target.entity)):
                    continue
                self.projected_barriers[target.entity] = ProjectedBarrier(
                    caster.body, self.now + seconds, capacity,
                )
                vitals = self.vitals_of(target)
                if self.now < vitals.healing_block[1]:
                    vitals.healing_block = vitals.healing_block[0], self.now

    @staticmethod
    def _particle_barrier_capacity() -> float:
        pool = graph.graph(0x0256)
        if pool is None:
            return 0.0
        return _number(expr.evaluate(pool.node(0).field("m_amount"), expr.Context()), 0.0)

    def _barrier_energy(self, caster, loss: float, attacker: int) -> None:
        script = caster.body_script
        if script is None or script.retired or script.component.fault is not None or self.is_dead(caster):
            return
        component = script.component
        params = {0x2A: loss, 0x12: expr.Entity(attacker)}
        if runtime.send_message(component, 0x0240000000000146, params, caster.body):
            component.run_now()

    def _absorb_personal_barrier(self, target: Target, amount: float, attacker: int) -> float | None:
        pet = self._personal_barrier_pet(target.owner)
        if pet is None:
            return None
        activation = pet.instance.id, pet.counter
        prior = self.personal_barriers.get(target.entity)
        if prior is not None and prior[:2] == activation:
            health = prior[2]
        else:
            # 0244 node9 launches0256 on the pet. Its HealthPool node0
            # evaluates to200 with the default controller configuration.
            health = self._particle_barrier_capacity()
            if health <= 0:
                return None
        remaining = max(0.0, health - amount)
        if amount >= health:
            self.personal_barriers.pop(target.entity, None)
            # Pet's onEnd runs node13, which stops the ability and applies
            # cooldown via node7. The breaking hit is absorbed in full.
            pet.request_finish()
            pet.instance.component.run_now()
        else:
            self.personal_barriers[target.entity] = (*activation, remaining)
        self._barrier_energy(target.owner, min(health, amount), attacker)
        return remaining

    def _absorb_projected_barrier(self, target: Target, amount: float, attacker: int) -> float | None:
        barrier = self._active_projected_barrier(target.entity)
        if barrier is None:
            return None
        loss = min(barrier.health, amount)
        barrier.health = max(0.0, barrier.health - amount)
        caster = next((player for player in self.match.players
                       if not player.gone and player.has_body and player.body == barrier.source), None)
        if caster is not None:
            self._barrier_energy(caster, loss, attacker)
        return barrier.health

    @staticmethod
    def _incoming_damage(target: Target, amount: float) -> float:
        """Host live incoming-damage8243, including actual0CF4 Fortify's0.6 stack.

        Applying it before the shared armor consumer is server reconstruction;
        native combat-mod/armor ordering has not been recovered. Read without
        creating an absent entity variable or changing its authored lifetime.
        """
        script = getattr(target.owner, 'body_script', None)
        component = getattr(script, 'component', None)
        slot = component.entity_var(8243, create=False) if isinstance(component, runtime.Component) else None
        scalar = _number(slot.value() if isinstance(slot, runtime.Var) else None, 1.0)
        if not math.isfinite(scalar):
            scalar = 1.0
        return amount * max(0.0, scalar)

    def _damage(
        self, shooter, target: Target, amount: float, critical: bool, loadout, frame: int,
        *, world_tick: int | None = None, broken_particle_barriers: set[int] | None = None,
    ) -> bool:
        """Apply damage and report whether a body hit can carry an attached effect."""
        vitals = self.vitals_of(target)
        if vitals.dead or amount <= 0.0 or self._invulnerable(target.owner):
            return False
        if loadout in (FLAIL_LOADOUT, WHIP_LOADOUT) and self._deflects(target, self._eye(shooter)):
            return False
        # A broken Particle Barrier still covers this body's remaining
        # contacts from the same rocket, including its splash and status.
        if broken_particle_barriers is not None and target.entity in broken_particle_barriers:
            return False
        attacker = _source_entity(shooter)
        remaining = self._absorb_personal_barrier(target, amount, attacker)
        if remaining is None:
            remaining = self._absorb_projected_barrier(target, amount, attacker)
        if remaining is not None:
            if remaining <= 0 and broken_particle_barriers is not None:
                broken_particle_barriers.add(target.entity)
            return False
        dealt = vitals.damage(self._incoming_damage(target, amount))
        if dealt <= 0.0:
            return False
        self.brigitte.damaged(shooter, loadout)
        if shooter is not target.owner:
            self.brigitte.charge(shooter, dealt)
        killed = vitals.now[HEALTH_POOL] <= EPSILON
        if killed:
            vitals.now[HEALTH_POOL] = 0.0
            vitals.died_at = self.now
            vitals.death_tick = self.match.tick if world_tick is None else world_tick
            vitals.killer = _source_entity(shooter)
        if not isinstance(shooter, bots.Bot):
            flags = (CRITICAL if critical else 0) | (KILLING if killed else 0)
            self._tell_hit(shooter, target, dealt, flags, frame)
            shooter.stats.add(stats.HERO_DAMAGE, dealt)
        victim = target.owner
        if killed and isinstance(victim, bots.Bot):
            # The NPC death script owns its terminal health change and death event ordering.
            self._bot_died(shooter, victim, critical, loadout, vitals.death_tick)
            return True
        self._sync_body_health(victim, vitals)
        if killed:
            self._died(shooter, victim, critical, loadout)
        return True

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
        self.projected_barriers.pop(bot.entity, None)
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
        """A player died: the notices, his body script's "died" (the kill feed line), and the respawn."""
        self.clear_pins(victim.body)
        self.stuns.pop(victim.body, None)
        self.personal_barriers.pop(victim.body, None)
        self.projected_barriers.pop(victim.body, None)
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
        self.brigitte.died(victim)
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
