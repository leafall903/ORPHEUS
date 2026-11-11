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
from dataclasses import dataclass

from ow174.game import (
    absorption,
    barriers,
    biotic,
    bots,
    cryo,
    graviton,
    heavy_charge,
    mei_wall,
    deflection,
    moira,
    zarya_barriers,
    observers,
    orisa_barrier,
    photon_barrier,
    pools,
    projectiles,
    sigma_barrier,
    stats,
    voicelines,
    winston_barrier,
    world,
)
from ow174.game.bits import BitWriter
from ow174.game.collision import MOVER, World
from ow174.game.commands import Command
from ow174.game.mover import FELL, PITCH_LIMIT, PITCH_RADIANS, clamp_linked_target_command, mover_data
from ow174.game.script import expr, graph, message_receive, nodes, runtime
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
EYE_STANDING = {0x0400000000001C46: 1.7, 0x04000000000002A3: 1.425}  # m_E29993A5 (DATA)
EYE_CROUCHED = {0x04000000000003CF: 1.085, 0x0400000000001C46: 1.1,
                0x04000000000002A3: 0.9975}  # m_816B830F (DATA)
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
FLASHBANG_EFFECT = 0x04A0000000000079  # target-build053/0079 ->0228 ->224E
FLASHBANG_MODE_FLAG = 13408  # graph0228: normal .8s; special mode flag1.7s
SLEEP_DART_EFFECT = 0x04A000000000026F  # target-build 053/026F -> 0BDE -> 224E
SLEEP_DART_DURATION = 5.0  # 0BDE node 51 passes this duration to 224E
SLEEP_WAKE_DELAY = 0.5
SLEEP_VIEW_DELAY = 0.15  # 224E node 29
SLEEP_WAKE_MESSAGE = 0x0240000000000011  # 224E node 77
SLEEP_WAKE_TAGS = 0x0A4000000000003D  # receiver parameter bound to variable 510
SLEEP_VARIANT = 0x0D8000000000676B
WRAITH = (0x02E0000000000002, 0x0020, 5)
CRYO_FREEZE = (0x02E00000000000DD, 0x05FE, 35)
CRYO_RADIUS, CRYO_HEIGHT = 0.5, 2.0  # 090B STU_BE9A0B3E
DAMAGE_IMMUNITY = (
    WRAITH,
    (0x02E0000000000020, 0x017B, 31),  # Zenyatta Transcendence
    CRYO_FREEZE,
)


# --- geometry -----------------------------------------------------------------------------------------


def eye_height(body: int, crouched: bool = False) -> float:
    if crouched:
        return EYE_CROUCHED.get(body, EYE_CROUCHED_DEFAULT)
    return EYE_STANDING.get(body, EYE)


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


def capsule_visible(collision: World | None, origin, a, b, radius: float, *, walls=None,
                    cover_contact=None) -> bool:
    """Reconstructed visibility to the nearest capsule surface."""
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
            and (walls is None or walls.cast(origin, direction, reach) is None)
            and (cover_contact is None or cover_contact(origin, direction, reach) is None))


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


class _AdaptiveShieldContext(runtime.InstanceContext):
    def __init__(self, instance, enemy_count):
        super().__init__(instance, None)
        self.enemy_count = enemy_count

    def var(self, scope, var):
        if scope == runtime.INSTANCE_SCOPE and var == 11398:
            return self.enemy_count
        return super().var(scope, var)


class Vitals:
    """A body's pools as component 51 has them: health, armour, shields (world.health's ids and fields)."""

    def __init__(self, entity: int, health: float, armour: float = 0.0, shields: float = 0.0) -> None:
        self.entity = entity
        self.most = {HEALTH_POOL: float(health), ARMOUR_POOL: float(armour), SHIELDS_POOL: float(shields)}
        self.now = dict(self.most)
        self.temporary_shields: dict[int, TemporaryShield] = {}
        self.adaptive_activation: tuple[int, int] | None = None
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
        self.sleep_until = 0.0
        self.sleep_wake_after = 0.0
        self.sleep_view: tuple[float, ...] | None = None
        self.nano_until = 0.0
        self.healing_boost_until = 0.0
        self.healing_block_until = 0.0

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

    def damage(self, amount: float) -> float:
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
    healing: bool = False  # ModifyHealth m_93CDFD2F: 0 heals, 1 damages (DATA)
    effect: int | None = None
    ally_heal: float = 0.0
    detonate_allies: bool = False
    biotic_duration: float = 0.0
    sleep_duration: float = 0.0
    tags: tuple[int, ...] = ()

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


def health_tags(state, modify) -> tuple[int, ...]:
    """Authored health-operation tags; no generic native filter interpretation."""
    found = []
    for row in modify.get("m_148F3152") or ():
        tag = state.evaluate(row.get("m_EE3055BD"), watch=False)
        if isinstance(tag, expr.Asset):
            found.append(tag.guid)
    return tuple(found)


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
    effect_guid = effect.guid if isinstance(effect, expr.Asset) else None
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
        critical=value("m_A872C70E", 1.0),
        rings=tuple(sorted(rings)),
        healing=modify.get("m_93CDFD2F", 1) == 0,
        effect=effect_guid,
        ally_heal=100.0 if state.instance.graph.index == 0xB97 and cfg.get("$") == "STU_4AA6CA44" else 0.0,
        detonate_allies=state.instance.graph.index == 0xB97,
        biotic_duration=4.0 if state.instance.graph.index == 0xB97 else 0.0,
        sleep_duration=SLEEP_DART_DURATION if effect_guid == SLEEP_DART_EFFECT else 0.0,
        tags=health_tags(state, modify),
    )


def projectile_gravity(state) -> float:
    """E1A5B579 motion scalar -> native D71A70's downward velocity step.

    Conditional/contact-only motion and engine gravity multipliers remain open.
    Ana0B97 supplies unconditional instance variable7389=9.8.
    """
    gravity = 0.0
    for motion in state.node.field("m_projectileMotions") or ():
        if not isinstance(motion, dict) or motion.get("$") != "STU_E1A5B579":
            continue
        if expr.truthy(state.evaluate(motion.get("m_D0C4CF49"), watch=False)):
            continue
        value = _number(state.evaluate(motion.get("m_599AB8D7"), watch=False), 0.0)
        if math.isfinite(value):
            gravity += value
    return gravity


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
    spray: "moira.SprayTrace | None" = None
    gravity: float = 0.0
    source_body: int | None = None  # emitting body, independent of later hero/body replacement
    velocity: tuple[float, float, float] | None = None
    detonate_on_expiry: bool = False
    definition: int | None = None
    spawn: int | None = None


# Graph -> (NPC body, TrackTargets position, ModifyHealth position).
NPC_MELEE = {
    0x1422: (0x0400000000001D40, 5, 4),
    0x140E: (0x0400000000001EBB, 6, 5),
}


class Combat:
    """The match's health, hits, deaths and respawns."""

    def __init__(self, match) -> None:
        self.match = match
        self.vitals: dict[int, Vitals] = {}  # body or bot entity -> its health
        self.history: dict[int, deque] = {}  # entity -> (tick, feet, height, eye) of the last HISTORY ticks
        self.replacement_ticks: dict[int, int] = {}  # replacement body -> authoritative world tick
        self.rockets: list[Rocket] = []
        self.moira_orbs: list[moira.Orb] = []
        self.moira_clocks = {}
        self.moira_hots: dict[int, moira.SprayHeal] = {}
        self.barrier_geometry = barriers.configured_geometry()
        self.barriers: dict[int, barriers.Barrier] = {}
        self.zarya_bubbles = zarya_barriers.Barriers(self)
        self.winston_barriers = winston_barrier.Barriers(self)
        self.orisa_barriers = orisa_barrier.Barriers(self)
        self.photon_barriers = photon_barrier.Barriers(self)
        self.sigma_barriers: dict[int, sigma_barrier.Barrier] = {}
        self.zarya_gravitons = graviton.Gravitons(self)
        self.cryo_actors: dict[int, cryo.Ice] = {}
        self.next_cryo = cryo.FIRST_ENTITY
        self.grasp_shields: dict[int, absorption.ShieldDecay] = {}
        self.grasp_grants: dict[int, tuple[int, int]] = {}
        self.respawns: list[tuple[float, object]] = []  # (when, a player or a bot)
        self.next_bot = bots.BOT_ENTITY + len(match.bots)
        self.now = 0.0
        self.tick = 0
        self.melee_seen: dict[int, dict[tuple[int, int], int]] = {}  # body -> counters by instance/state
        self.walls = mei_wall.Walls(self)
        self.heavy_charge_contacts: dict[int, set[int]] = {}
        self.heavy_charge_impacts: dict[int, tuple[tuple[int, ...], float]] = {}
        self.heavy_charge_pins: dict[int, runtime.Instance] = {}
        self.stuns: dict[int, int] = {}  # body -> exclusive world-tick deadline
        self.pins: dict[int, int] = {}  # manual Assassin source -> controlled victim body
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
        self.clear_pins(player.body)
        self.barriers.pop(player.body, None)
        self.zarya_bubbles.retire(player.body)
        self.zarya_gravitons.release(player)
        cryo.retire_owner(self, player)
        self.walls.retire(player.body)
        self.grasp_shields.pop(player.body, None)
        self.grasp_grants.pop(player.body, None)
        # The owner's leased projectiles and their observer copies retire with the old body.
        self.rockets[:] = [
            rocket for rocket in self.rockets if rocket.shooter is not player or rocket.shot is None
        ]
        self.respawns[:] = [(when, who) for when, who in self.respawns if who is not player]

    def record_replacement(self, body: int) -> None:
        """Keep the creation boundary independent of the rolling pose history."""
        self.replacement_ticks[body] = self.match.tick
        self.stuns.pop(body, None)
        self.clear_pins(body)

    def is_asleep(self, actor, *, now: float | None = None) -> bool:
        vitals = self.vitals.get(_source_entity(actor))
        return (vitals is not None and not vitals.dead
                and (self.now if now is None else now) < vitals.sleep_until)

    def _update_sleep_view(self, player):
        """Sample the reconstructed player's pose when the authored view delay elapses."""
        vitals = self.vitals.get(player.body)
        if vitals is None:
            return
        if vitals.dead or self.now >= vitals.sleep_until:
            vitals.sleep_view = None
        elif (vitals.sleep_view is None
                and self.now + EPSILON >= vitals.sleep_wake_after - SLEEP_WAKE_DELAY + SLEEP_VIEW_DELAY):
            script = player.body_script
            if script is not None and not script.retired:
                vitals.sleep_view = script.world.sample_sleep_view()
                if vitals.sleep_view is not None:
                    return
            # Retain the reconstructed lock when there is no hosted sample.
            yaw = expr.f32(player.mover.yaw * PITCH_RADIANS)
            pitch = expr.f32(player.mover.pitch * PITCH_RADIANS)
            held_pitch = max(-PITCH_LIMIT, min(PITCH_LIMIT, pitch))
            vitals.sleep_view = (yaw, pitch, yaw, held_pitch, yaw, held_pitch)

    def clamp_sleep_command(self, player, command):
        vitals = self.vitals.get(player.body)
        if vitals is None or vitals.sleep_view is None or vitals.dead or self.now >= vitals.sleep_until:
            return command
        return clamp_linked_target_command(command, vitals.sleep_view)

    def movement_pause_until(self, actor) -> int:
        """World-tick route gate; a paused route consumes time without catching up."""
        entity = _source_entity(actor)
        vitals = self.vitals.get(entity)
        sleep = math.ceil(vitals.sleep_until / TICK) if vitals is not None and not vitals.dead else 0
        pull = self.match.tick + 1 if entity in self.zarya_gravitons.pulled else 0
        return max(self.stuns.get(entity, 0), sleep, pull)

    def is_stunned(self, actor, *, tick: int | None = None) -> bool:
        entity = _source_entity(actor)
        return (self.is_asleep(actor) or entity in self.pins.values()
                or self.stuns.get(entity, 0) > (self.tick if tick is None else tick))

    def clear_pins(self, entity: int) -> None:
        for source, victim in list(self.pins.items()):
            if entity in (source, victim):
                heavy_charge.release(self, source, cancel=entity == victim)
                self.pins.pop(source, None)

    @staticmethod
    def _protection_active(owner, profile) -> bool:
        hero, graph_index, state_index = profile
        script = getattr(owner, "body_script", None)
        if (getattr(getattr(owner, "hero", None), "guid", None) != hero
                or script is None or script.retired):
            return False
        return any(instance.graph.index == graph_index
                   and (state := instance.states.get(state_index)) is not None and state.active
                   for instance in script.component.instances.values())

    def damage_immune(self, owner) -> bool:
        """Reconstructed damage/control immunity from actual active ability phases."""
        return moira.fading(owner) or any(self._protection_active(owner, profile)
                                          for profile in DAMAGE_IMMUNITY)

    def _hit_effect(self, target: Target, effect: int | None) -> None:
        """Resource-backed stun reconstruction; native immunity/presentation remain unverified."""
        if (effect != FLASHBANG_EFFECT or self.vitals_of(target).dead
                or self.damage_immune(target.owner)):
            return
        variables = graph.mode_variables(self.match.game_map.mode_guid) or {}
        duration = 1.7 if expr.truthy(variables.get(FLASHBANG_MODE_FLAG)) else 0.8
        self._apply_stun(target, duration)

    def _apply_stun(self, target, duration):
        deadline = self.tick + math.ceil(duration / TICK)
        self.stuns[target.entity] = max(deadline, self.stuns.get(target.entity, 0))
        self._interrupt(target)

    def _interrupt(self, target: Target) -> None:
        script = getattr(target.owner, "body_script", None)
        if script is None:
            return
        component = script.component
        if not isinstance(target.owner, bots.Bot):
            self.walls.interrupt(target.owner)
        if isinstance(target.owner, bots.Bot):
            from ow174.game import assassin

            assassin.bind(component.world, component, None)
            heavy_charge.stop(self, target.owner)
        component.edges([(value, False) for value in tuple(component.held)])
        if not isinstance(target.owner, bots.Bot):
            photon_barrier.interrupt(script)
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
        self._sync_barrier(player)
        self.zarya_bubbles.sync(player)
        self._sync_adaptive_shield(player)
        shots, script.component.shots[:] = list(script.component.shots), []
        if self.is_dead(player):
            return
        moira.track_clock(self, player, posed=command is not None)
        if self.is_stunned(player):
            return
        if self.walls.command(player, command):
            return
        moira.transition(player)
        moira.grasp(self, player, command)
        moira.coalescence(self, player, command)
        if command is not None and player.hero.guid == orisa_barrier.HERO:
            self.orisa_barriers.remember_pose(player, command)
        for state, number, time, activation in shots:
            try:
                if command is not None:
                    self._shoot(player, command, state, number, time, activation=activation)
                elif self.orisa_barriers.admits(state):
                    self.orisa_barriers.launch_unposed(player, state, time)
            except Exception:
                log.exception("[game] %s: %s's shot failed", self.match.label(), player.name)
        try:
            self._melee(player, command, script)
        except Exception:
            log.exception("[game] %s: %s's melee failed", self.match.label(), player.name)

    def _sync_adaptive_shield(self, player) -> None:
        """Host 1566/state6 with a one-time enemy capsule query at the owner's eye."""
        if getattr(player.hero, "guid", None) != 0x02E00000000001CA:
            return
        script = player.body_script
        if script is None:
            return
        instance = next((item for item in script.component.instances.values()
                         if item.graph.index == 0x1566), None)
        state = instance.states.get(6) if instance is not None else None
        vitals = self.vitals.get(player.body)
        tag = moira.ADAPTIVE_SHIELD_TAG
        if state is None or not state.active:
            if vitals is not None and vitals.decay_temporary_shields(tag, vitals.total):
                self._sync_body_health(player, vitals)
            return
        if vitals is None:
            own = next((target for target in self._targets() if target.owner is player), None)
            if own is None:
                return
            vitals = self.vitals_of(own)
        activation = (instance.id, state.counter)
        if vitals.dead or vitals.adaptive_activation == activation:
            return

        radius = _number(instance.evaluate(instance.graph.state(9).field("m_radius")), 0.0)
        origin = self._eye(player)
        count = sum(
            segment_distance(origin, *target.segment()) - target.radius <= radius
            for target in self._targets()
            if target.owner is not player
            and (self.match.game_map.free_for_all or target.team != player.team)
            and not self.vitals_of(target).dead
        )
        amount = _number(expr.evaluate(state.node.field("m_amount"),
                                      _AdaptiveShieldContext(instance, count)), 0.0)
        vitals.adaptive_activation = activation
        if vitals.grant_temporary_shields(tag, amount, amount):
            self._sync_body_health(player, vitals)

    def _genji_deflects(self, target, incoming) -> bool:
        """Shared Genji-lane d714f0c candidate: active state10, frontal gate.

        Body-capsule contact and a90-degree gate reconstruct the authored
        boxes. Each caller owns attack eligibility; this is not immunity.
        """
        owner = target.owner
        if (getattr(getattr(owner, 'hero', None), 'guid', None) != 0x02E0000000000029
                or self.vitals_of(target).dead):
            return False
        script = getattr(owner, 'body_script', None)
        if script is None:
            return False
        for instance in script.component.instances.values():
            if instance.graph.index != 0x03A3:
                continue
            state = instance.states.get(10)
            if state is not None and state.active:
                return _dot(aim(owner.mover.yaw, owner.mover.pitch), incoming) < -EPSILON
        return False

    def _npc_melee(self, state, *, health=False):
        """Host Trooper/Heavy's authored sphere with live capsules and cover."""
        instance = state.instance
        spec = NPC_MELEE.get(instance.graph.index)
        if spec is None or state.node.pos != spec[2 if health else 1]:
            return NotImplemented
        actor = next((bot for bot in self.match.bots
                      if bot.entity == instance.component.entity and bot.body == spec[0]), None)
        if actor is None or actor.dead or self.is_stunned(actor):
            return True if health else ()
        tracker = instance.node_state(instance.graph.node(spec[1])) if health else state
        offset = tracker.evaluate(tracker.node.field("m_05BE62DD"), watch=False)
        radius = expr.to_float(tracker.evaluate(tracker.node.field("m_radius"), watch=False))
        x, y, z = actor.position
        forward = aim(round(actor.yaw), 0)
        centre = (x + forward[0] * offset.z, y + offset.y, z + forward[2] * offset.z)
        origin = self._eye(actor)
        collision = self._collision()
        contacts = []
        for target in self._enemies(actor):
            a, b = target.segment()
            if (segment_distance(centre, a, b) <= radius + target.radius
                    and capsule_visible(collision, origin, a, b, target.radius, walls=self.walls)):
                contacts.append(target)
        pillars = [piece for piece in self._melee_pillar_targets(origin, centre, radius)
                   if self.walls.hostile(piece, actor)]
        if not health:
            return tuple(expr.Entity(target.entity) for target in (*contacts, *pillars))
        modify = state.node.field("m_modifyHealth")
        amount = expr.to_float(state.evaluate(modify.get("m_amount"), watch=False))
        loadout = state.evaluate(modify.get("m_loadout"), watch=False)
        loadout_guid = loadout.guid if isinstance(loadout, expr.Asset) else None
        tags = health_tags(state, modify)
        for target in contacts:
            if expr.Entity(target.entity) not in tracker.targets:
                continue
            vitals = self.vitals_of(target)
            before = vitals.total
            self._damage(actor, target, amount, False, loadout_guid, self.tick,
                         world_tick=self.tick, damage_tags=tags)
            if not vitals.dead and vitals.total < before:
                self._melee_impulse(target, actor.position, state)
        self._melee_barriers(actor, ((centre, centre, radius),), amount)
        if amount > 0.0:
            for piece in pillars:
                if expr.Entity(piece.entity) in tracker.targets:
                    self.walls.damage(piece, actor, amount)
        return True

    @staticmethod
    def _melee_impulse(target, origin, state):
        """Apply the authored melee scalars to the reconstructed mover velocity."""
        mover = getattr(target.owner, "mover", None)
        if mover is None:
            return
        modify = state.node.field("m_modifyHealth")
        horizontal = expr.to_float(state.evaluate(modify.get("m_E33EFEE1"), watch=False))
        vertical = expr.to_float(state.evaluate(modify.get("m_583C208B"), watch=False))
        dx, dz = target.feet[0] - origin[0], target.feet[2] - origin[2]
        distance = math.hypot(dx, dz)
        scale = horizontal / distance if distance else 0.0
        vx, vy, vz = mover.velocity
        mover.velocity = (vx + dx * scale, vy + vertical, vz + dz * scale)
        if vertical > 0:
            mover.flags_word |= FELL

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
            self._melee_barriers(player, ((centre, centre, radius),), amount)
            self.winston_barriers.melee(player, eye, centre, radius, amount)
            self.orisa_barriers.melee(player, eye, centre, radius, amount)
            sigma_barrier.melee(self, player, eye, centre, radius, amount)
            self.photon_barriers.melee(player, eye, centre, radius, amount)
            absorbed = self.zarya_bubbles.melee(player, eye, centre, radius, amount)
            view_tick = command.frame - command.view_delay(player.quanta) * 1e6 / player.quanta
            for target in self._enemies(player):
                if target.entity in absorbed:
                    continue
                shown = self.seen(target, view_tick)
                if shown is None:
                    continue
                a, b = shown.segment()
                if segment_distance(centre, a, b) - shown.radius <= radius and amount > 0.0:
                    # Reconstruction: an in-range capsule must be visible through
                    # static geometry. Stop at its near surface so a wall behind
                    # the recipient cannot incorrectly swallow a valid melee hit.
                    nearest = (a[0], min(max(eye[1], a[1]), b[1]), a[2])
                    delta = _sub(nearest, eye)
                    distance = math.sqrt(_dot(delta, delta))
                    surface = max(0.0, distance - shown.radius)
                    if surface > EPSILON:
                        direction = tuple(value / distance for value in delta)
                        wall = ray_world(self._collision(), eye, direction, surface)
                        if ((wall is not None and wall < surface)
                                or self.walls.cast(eye, direction, surface) is not None):
                            continue
                    if not self._genji_deflects(target, aim(*command.aim())):
                        vitals = self.vitals_of(target)
                        before = vitals.total
                        self._damage(player, target, amount, False, loadout, command.frame,
                                     damage_tags=health_tags(state, modify))
                        if not vitals.dead and vitals.total < before:
                            self._melee_impulse(target, player.mover.position, state)

            if amount > 0.0:
                for piece in self._melee_pillar_targets(eye, centre, radius):
                    self.walls.damage(piece, player, amount)

    def update(self, now: float, tick: int, frames: bool = True) -> None:
        """Each tick: keep where the bodies are, fly the rockets, respawn, send health that changed. The
        other players' body frames only on the ticks that send (`frames`)."""
        if self.match.collision_world is None:
            return
        retained = max(self.tick + 1, tick - COMBAT_CATCH_UP + 1)
        first = tick
        if self.tick and tick > self.tick + 1 and (
            self.winston_barriers.actors or self.sigma_barriers or self.photon_barriers.actors
            or self.photon_barriers.casts or self.moira_clocks
            or self.orisa_barriers.actors or self.orisa_barriers.flights
            or self.orisa_barriers.casts or self.zarya_gravitons.fields or any(rocket.gravity for rocket in self.rockets) or any(
            (bot.firing or heavy_charge.active(bot) is not None)
            and not bot.dead and isinstance(bot.body_script, BotScript) for bot in self.match.bots
        )):
            first = retained
        for target in self._targets():
            history = self.history.setdefault(target.entity, deque(maxlen=HISTORY))
            history.append((tick, target.feet, target.height, target.eye))
        # Replay the retained combat frames using the most recent poses. Player input
        # accepted just before the scheduler caught up joins only at the endpoint.
        for at in range(first, tick + 1):
            self.now, self.tick = now - (tick - at) * TICK, at
            for player in self.match.players:
                self._update_sleep_view(player)
                self._sync_barrier(player)
                self.zarya_bubbles.sync(player)
                self._sync_adaptive_shield(player)
            self.walls.update(at)
            self.winston_barriers.update(at)
            self.orisa_barriers.update(at, TICK)
            self.photon_barriers.update(at)
            moira.advance_clocks(self)
            sigma_barrier.update(self)
            cryo.advance(self, at)
            absorption.update_shields(self)
            self.zarya_gravitons.update(at, TICK)
            moira.advance_orbs(self)
            moira.advance_spray_heals(self)
            for rocket in list(self.rockets):
                if at == tick or rocket.last_tick is not None:
                    self._fly(rocket)
            self._bot_weapons(at)
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
        for source, victim in list(self.pins.items()):
            if source not in current or victim not in current:
                heavy_charge.release(self, source)
                self.pins.pop(source, None)
        for entity in [key for key, deadline in self.stuns.items() if deadline <= tick]:
            del self.stuns[entity]
        for table in (self.history, self.vitals, self.replacement_ticks, self.stuns, self.heavy_charge_contacts):
            for entity in [key for key in table if key not in current]:
                del table[entity]

    # --- targets -----------------------------------------------------------------------------------

    @staticmethod
    def _barrier_instance(player):
        script = player.body_script
        if script is None or script.hero != 0x02E0000000000007:
            return None
        return next((i for i in script.component.instances.values() if i.graph.index == 0x95), None)

    def _sync_barrier(self, player):
        geometry = self.barrier_geometry
        if geometry is None or not player.has_body:
            return None
        instance = self._barrier_instance(player)
        if instance is None:
            return None
        state = self.barriers.get(player.body)
        if state is None:
            state = barriers.Barrier(geometry.maximum, geometry.maximum, self.now, self.now)
            self.barriers[player.body] = state
        pet = instance.states.get(17)
        if self.is_dead(player) or player.gone or not player.spawned or player.client is None:
            if pet is not None and pet.active:
                self._lower_barrier(instance)
            state.active, state.last_update = False, self.now
            return state
        raised = pet is not None and pet.active
        if not raised and state.active:
            state.regen_after = self.now + geometry.regen_delay
        if not raised and self.now > max(state.last_update, state.regen_after):
            duration = self.now - max(state.last_update, state.regen_after)
            state.current = expr.f32(min(state.maximum, state.current + geometry.regen_rate * duration))
        if raised and (state.current <= 0.0 or self.now < state.broken_until):
            self._lower_barrier(instance)
            raised = False
        state.active = raised and state.current > 0.0 and self.now >= state.broken_until
        state.last_update = max(self.now, state.last_update)
        current = instance.find_var(0, 586)
        if not expr.same(current.value(), expr.f32(state.current)):
            instance.write((0, 586), expr.f32(state.current))
        return state

    @staticmethod
    def _lower_barrier(instance):
        ability = instance.states.get(59)
        if isinstance(ability, nodes.Ability):
            ability.stop()
        pet = instance.states.get(17)
        if pet is not None and pet.active:
            pet.request(runtime.ABORT)
        instance.component.run_now()

    def _enemy_barriers(self, shooter):
        if self.barrier_geometry is None:
            return
        source = _source_entity(shooter)
        for owner in self.match.players:
            if owner is shooter or owner.body == source:
                continue
            if not self.match.game_map.free_for_all and owner.team == shooter.team:
                continue
            state = self._sync_barrier(owner)
            if state is None or not state.active:
                continue
            yield owner, state

    def _barrier_contacts(self, shooter, origin, direction, length, radius=0.0):
        found = []
        for owner, state in self._enemy_barriers(shooter):
            distance = barriers.intersect(self.barrier_geometry, owner.mover.position,
                                          owner.mover.yaw, owner.mover.pitch,
                                          origin, direction, length, radius,
                                          owner.mover.values.stand_radius, owner.mover.values.stand_height)
            if distance is not None:
                found.append((distance, owner, state))
        return sorted(found, key=lambda hit: (hit[0], hit[1].body))

    def _barrier_contact(self, shooter, origin, direction, length, radius=0.0, *, bubbles=False):
        found = self._barrier_contacts(shooter, origin, direction, length, radius)
        nearest = found[0] if found else None
        dome = self.winston_barriers.trace(shooter, origin, direction, length, radius)
        if dome is not None and (nearest is None or dome[0] < nearest[0]):
            distance, actor = dome
            nearest = (distance, actor.owner, actor)
        shield = self.orisa_barriers.trace(shooter, origin, direction, length, radius)
        if shield is not None and (nearest is None or shield[0] < nearest[0]):
            distance, actor = shield
            nearest = (distance, actor.owner, actor)
        experimental = sigma_barrier.trace(self, shooter, origin, direction, length, radius)
        if experimental is not None and (nearest is None or experimental[0] < nearest[0]):
            distance, actor = experimental
            nearest = (distance, actor.owner, actor)
        photon = self.photon_barriers.trace(shooter, origin, direction, length, radius)
        if photon is not None and (nearest is None or photon[0] < nearest[0]):
            distance, actor = photon
            nearest = (distance, actor.owner, actor)
        if bubbles:
            contact = self.zarya_bubbles.intercept(shooter, origin, direction, length, radius=radius)
            if contact is not None and (nearest is None or contact[0] < nearest[0]):
                distance, bubble = contact
                return distance, bubble.owner, bubble
        return nearest

    def _cryo_contact(self, shooter, origin, direction, length):
        """Selected 090B ice contact from the live 05FE Pet, anchored at Mei's feet."""
        best = None
        for target in self._enemies(shooter):
            if not self._protection_active(target.owner, CRYO_FREEZE):
                continue
            if not any(ice.owner is target.owner and cryo.live(self, ice)
                       for ice in self.cryo_actors.values()):
                continue
            x, y, z = target.feet
            bottom = (x, y + CRYO_RADIUS, z)
            top = (x, y + CRYO_HEIGHT - CRYO_RADIUS, z)
            distance = ray_capsule(origin, direction, bottom, top, CRYO_RADIUS)
            if distance is not None and distance <= length and (best is None or distance < best):
                best = distance
        return best

    def _melee_pillar_targets(self, origin, centre, radius):
        hits = []
        for piece in self.walls.pieces.values():
            point = mei_wall.closest(piece, centre)
            offset = _sub(point, centre)
            if _dot(offset, offset) > radius * radius:
                continue
            delta = _sub(point, origin)
            length = math.sqrt(_dot(delta, delta))
            if length > EPSILON:
                direction = tuple(value / length for value in delta)
                wall = ray_world(self._collision(), origin, direction, length)
                pillar = self.walls.cast(origin, direction, length + EPSILON)
                if ((wall is not None and wall < length - EPSILON)
                        or (pillar is not None and pillar[0] < length - EPSILON
                            and pillar[1] is not piece)):
                    continue
            hits.append(piece)
        return hits

    def _melee_barriers(self, shooter, volumes, amount, seen=()):
        found = []
        for owner, state in self._enemy_barriers(shooter):
            if ("barrier", owner.body) in seen:
                continue
            visible = False
            origin = self._eye(shooter)
            for a, b, radius in volumes:
                for point in barriers.overlap_points(
                    self.barrier_geometry, owner.mover.position, owner.mover.yaw, owner.mover.pitch,
                    a, b, radius, owner.mover.values.stand_radius, owner.mover.values.stand_height,
                ):
                    if capsule_visible(self._collision(), origin, point, point, 0., walls=self.walls):
                        visible = True
                        break
                if visible:
                    break
            if visible:
                self._damage_barrier((0.0, owner, state), amount, shooter=shooter)
                found.append(owner)
        return found

    def _damage_barrier(self, contact, amount, *, shooter=None, source_body=None):
        _, owner, state = contact
        if shooter is not None:
            from ow174.game import nano
            amount *= nano.offense(self, _source_entity(shooter) if source_body is None else source_body)
        if isinstance(state, zarya_barriers.Bubble):
            self.zarya_bubbles.damage(state, amount, shooter, source_entity=source_body)
            return
        if isinstance(state, winston_barrier.Bubble):
            self.winston_barriers.damage(state, amount)
            return
        if isinstance(state, orisa_barrier.Shield):
            self.orisa_barriers.damage(state, amount)
            return
        if isinstance(state, sigma_barrier.Barrier):
            sigma_barrier.damage(state, amount)
            return
        if isinstance(state, photon_barrier.Shield):
            self.photon_barriers.damage(state, amount)
            return
        if amount <= 0.0 or not state.active:
            return
        state.current = expr.f32(max(0.0, state.current - amount))
        instance = self._barrier_instance(owner)
        if instance is None:
            return
        instance.write((0, 586), state.current)
        if state.current <= 0.0:
            geometry = self.barrier_geometry
            state.broken_until = self.now + geometry.break_delay
            state.regen_after = self.now + geometry.regen_delay
            state.active = False
            self._lower_barrier(instance)
            instance.write((0, 61), geometry.break_delay)
            instance.component.run_now()

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
                    target.owner.hero.guid, target.owner.hero.body,
                ))
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
            if self.vitals_of(target).dead or self._protection_active(target.owner, WRAITH):
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
            charge = heavy_charge.active(bot)
            autonomous = npc_control.autonomous(bot)
            if ((not bot.firing and not autonomous and charge is None
                 and heavy_charge.active(bot, include_cooldown=True) is None)
                    or bot.dead
                    or not isinstance(script, BotScript) or script.frame > tick):
                continue
            try:
                if charge is not None and script.frame < tick:
                    heavy_charge.advance(self, bot, charge, tick)
                if autonomous and tick == self.match.tick:
                    npc_control.acquire(self, bot)
                allowed = False
                if bot.attack_target is not None or bot.attack_input:
                    allowed = npc_control.update_input(self, bot)
                snapshot = script.advance_live(tick, bot.position)
                if charge is not None:
                    heavy_charge.finish_frame(self, bot, charge)
                from ow174.game import assassin

                assassin.update_pin(script.component.world, script.component)
                shots, script.component.shots[:] = list(script.component.shots), []
                for state, number, time, activation in shots:
                    if (autonomous and not allowed) or self.is_stunned(bot, tick=tick):
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
                self.heavy_charge_contacts.pop(bot.entity, None)
                self.heavy_charge_impacts.pop(bot.entity, None)
                bot.body_script = None
                log.exception("[game] %s: %s's weapon script failed", self.match.label(), bot.name)

    def _eye(self, player) -> tuple:
        if isinstance(player, bots.Bot):
            x, y, z = player.position
            return x, y + EYE, z
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
        if (not isinstance(shooter, bots.Bot) and shooter.hero.guid == 0x02E000000000013B
                and state.instance.graph.index == 0x0B9E and state.index == 1):
            from ow174.game import nano
            target = state.instance.find_var(expr.INSTANCE, 8070, create=False)
            duration = state.instance.find_var(expr.INSTANCE, 215, create=False)
            nano.apply(self, _source_entity(shooter), target.value() if target is not None else (),
                       _number(duration.value(), 0) if duration is not None else 0)
            return

        def field(name, default=None):
            cfg = node.field(name)
            return default if cfg is None else _number(state.evaluate(cfg, watch=False), default)

        npc = isinstance(shooter, bots.Bot)
        if npc:
            x, y, z = shooter.position
            # Use the server body pose until the animated muzzle transform is available.
            origin, direction = (x, y + EYE, z), aim(round(shooter.yaw), 0)
        else:
            origin, direction = self._eye(shooter), aim(*command.aim())
            if self.orisa_barriers.admits(state):
                self.orisa_barriers.launch(shooter, state, origin, direction, command.aim()[0])
                return
            from ow174.game import mccree_spread

            direction = mccree_spread.fan_direction(shooter, state, direction, number, time, activation)
        direct = hit_def(state, node.field("m_6396149F"))
        speed = field("m_projectileSpeed")
        if speed:
            flash_gravity = None if npc else mccree_spread.flashbang_gravity(shooter, state)
            if flash_gravity is not None:
                direction = mccree_spread.flashbang_direction(state, direction)
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
                if moira.orb_fired(self, shooter, state, definition.guid, speed, lifetime,
                                   origin, direction, shot):
                    return
            damaging = (direct is not None and direct.amount > 0.0) or (
                blast is not None and blast.amount > 0.0
            )
            spawn = None
            if (state.instance.graph.index == 0x025F
                    and graviton.FIELD_ENTITY in projectiles._defs(node.field("m_49CBA3BC"))):
                spawn = graviton.FIELD_ENTITY
            if damaging or shot is not None or spawn is not None:
                self.rockets.append(
                    Rocket(
                        shooter, shooter.team, origin, direction, speed, lifetime, direct, blast, shot,
                        last_tick=self.tick if npc else None,
                        spray=moira.spray_trace(shooter, state, direct),
                        gravity=projectile_gravity(state) if flash_gravity is None else flash_gravity,
                        source_body=_source_entity(shooter),
                        velocity=(tuple(expr.f32(axis * speed) for axis in direction)
                                  if flash_gravity is not None or spawn is not None else None),
                        detonate_on_expiry=flash_gravity is not None,
                        spawn=spawn,
                        definition=definition.guid if isinstance(definition, expr.Asset) else None,
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
        best = None
        for target in self._hit_targets(shooter, direct):
            shown = self.seen(target, view_tick)
            if shown is None:
                continue
            a, b = shown.segment()
            t = ray_capsule(origin, direction, a, b, shown.radius)
            if t is not None and t <= reach and (best is None or t < best[0]):
                best = (t, target, shown)
        limit = best[0] if best is not None else reach
        pillar = self.walls.cast(origin, direction, limit)
        if pillar is not None:
            limit = pillar[0]
        shield = (None if direct.healing else
                  self._barrier_contact(shooter, origin, direction, limit, bubbles=True))
        if shield is not None and shield[0] < limit:
            limit = shield[0]
        else:
            shield = None
        if best is None and pillar is None and shield is None:
            return
        wall = ray_world(self._collision(), origin, direction, limit)
        if wall is not None and wall < limit:
            return
        if shield is not None:
            self._damage_barrier(shield, direct.amount * direct.scale(limit), shooter=shooter)
            return
        if pillar is not None:
            if not direct.healing:
                self.walls.damage(pillar[1], shooter, direct.amount * direct.scale(limit))
            return
        t, target, shown = best
        hit_y = origin[1] + direction[1] * t
        critical = hit_y >= shown.feet[1] + shown.eye - HEAD_BELOW
        amount = direct.amount * direct.scale(t) * (direct.critical if critical else 1.0)
        self._apply_hit(
            shooter, target, direct, amount, critical and direct.critical > 1.0, shot_frame,
            world_tick=self.tick if npc else None,
        )

    def _matrix_hit(self, shooter, origin, direction, reach, definition):
        """Nearest enemy authored Matrix in the declared current aim frame.

        Consumers bound reach by earlier world/defense contacts. Native filter,
        attachment and history semantics remain open.
        """
        if not deflection.absorbable(definition):
            return None
        best = None
        for target in self._enemies(shooter):
            player = target.owner
            if (isinstance(player, bots.Bot)
                    or getattr(getattr(player, "hero", None), "guid", None) != 0x02E000000000007A):
                continue
            distance = deflection.matrix_distance(
                player, origin, direction, reach, self._eye(player),
                aim(player.mover.yaw, player.mover.pitch),
            )
            if distance is not None and (best is None or distance < best):
                best = distance
        return best

    def _fly(self, rocket: Rocket) -> None:
        """Sweep elapsed world time against the current bodies and static collision."""
        # Player input can arrive immediately before the scheduler accounts for a gap.
        elapsed = 1 if rocket.last_tick is None else self.tick - rocket.last_tick
        if elapsed <= 0:
            return
        rocket.last_tick = self.tick
        if rocket.velocity is not None:
            # Preserve fixed flight substeps across scheduler gaps. Target poses
            # and impact timing still come from the current server frame.
            for _ in range(elapsed):
                if rocket not in self.rockets:
                    break
                self._fly_step(rocket, min(TICK, rocket.left))
            return
        self._fly_step(rocket, min(elapsed * TICK, rocket.left))

    def _fly_step(self, rocket: Rocket, duration: float) -> None:
        if duration <= 0.0:
            self._expire(rocket)
            return
        if rocket.spray is not None:
            moira.fly_spray(self, rocket, duration)
            return
        # Sweep the remaining flight even when the range or lifetime ends inside this tick.
        if rocket.velocity is not None:
            from ow174.game import mccree_spread

            rocket.velocity = mccree_spread.gravity_velocity(rocket.velocity, rocket.gravity, duration)
            rocket.speed = math.sqrt(_dot(rocket.velocity, rocket.velocity))
            if rocket.speed > EPSILON:
                rocket.direction = tuple(axis / rocket.speed for axis in rocket.velocity)
        elif rocket.gravity:
            velocity = [expr.f32(axis * rocket.speed) for axis in rocket.direction]
            velocity[1] = expr.f32(velocity[1] - expr.f32(rocket.gravity * expr.f32(duration)))
            rocket.speed = math.sqrt(_dot(velocity, velocity))
            if rocket.speed > EPSILON:
                rocket.direction = tuple(axis / rocket.speed for axis in velocity)
        for _ in range(8):
            duration = self._sweep_rocket(rocket, duration)
            if duration is None:
                return
            if duration <= 0.0:
                if rocket.left <= 0.0:
                    self._expire(rocket)
                return
        # Bound same-step recontacts even when overlapping bodies are at zero distance.
        rocket.flown += duration
        rocket.left -= duration
        if rocket.left <= 0.0:
            self._expire(rocket)

    def _sweep_rocket(self, rocket: Rocket, duration: float) -> float | None:
        """Sweep without another gravity update. Only reflection returns unused time."""
        step = rocket.speed * duration
        best = None
        for target in self._hit_targets(rocket.shooter, rocket.direct):
            a, b = target.segment()
            t = ray_capsule(rocket.position, rocket.direction, a, b, target.radius)
            if t is not None and t <= step and (best is None or t < best[0]):
                best = (t, target)
        wall = ray_world(self._collision(), rocket.position, rocket.direction, step)
        if wall is not None and (best is None or wall < best[0]):
            best = (wall, None)
        limit = best[0] if best is not None else step
        pillar = self.walls.cast(rocket.position, rocket.direction, limit)
        if pillar is not None and (wall is None or pillar[0] < wall):
            limit = wall = pillar[0]
            best = (limit, None)
        else:
            pillar = None
        dart = rocket.direct is not None and rocket.direct.sleep_duration > 0
        damaging = any(hit is not None and not hit.healing and hit.amount > 0
                       for hit in (rocket.direct, rocket.blast))
        barrier = (self._barrier_contact(rocket.shooter, rocket.position, rocket.direction, limit,
                                         bubbles=rocket.blast is None) if damaging else None)
        control_projectile = dart or rocket.detonate_on_expiry
        ice = (self._cryo_contact(rocket.shooter, rocket.position, rocket.direction, limit)
               if damaging or control_projectile else None)
        ice_first = (ice is not None and (best is None or ice < limit)
                     and (barrier is None or ice < barrier[0]))
        if self._absorb_projectile(
            rocket, ice if ice_first else limit, barrier, impact=ice_first or best is not None,
        ):
            return
        if ice_first and control_projectile:
            rocket.flown += ice / rocket.speed
            self._landed(rocket)
            return
        if ice_first:
            wall = ice
            best, pillar, barrier = (ice, None), None, None
        if barrier is not None and (best is None or barrier[0] < limit):
            wall = barrier[0]
            best, pillar = (wall, None), None
        else:
            barrier = None
        if best is None:
            rocket.position = _along(rocket.position, rocket.direction, step)
            rocket.flown += duration
            rocket.left -= duration
            if rocket.left <= 0.0:
                self._expire(rocket)
            return
        t, struck = best
        elapsed = t / rocket.speed
        rocket.flown += elapsed
        point = _along(rocket.position, rocket.direction, t)
        if (struck is not None
                and (self.match.game_map.free_for_all or struck.team != rocket.shooter.team)
                and deflection.absorbable(rocket.definition)
                and self._genji_deflects(struck, rocket.direction)):
            self._reflect_projectile(rocket, struck, point)
            rocket.left = max(0.0, rocket.left - elapsed)
            return duration - elapsed
        if rocket.spawn == graviton.FIELD_ENTITY:
            landing = _along(rocket.position, rocket.direction, max(0.0, t - 0.01)) if struck is None else point
            self._landed(rocket, landing)
        else:
            self._landed(rocket)
        if struck is not None and rocket.direct is not None and rocket.direct.amount > 0.0:
            self._apply_hit(
                rocket.shooter, struck, rocket.direct, rocket.direct.amount, False, self.tick,
                world_tick=self.tick, source_body=rocket.source_body,
            )
        self._explode(rocket, point, wall)
        if pillar is not None or barrier is not None:
            direct, blast = rocket.direct, rocket.blast
            amount = direct.amount if direct is not None and not direct.healing else 0.0
            if blast is not None and not blast.healing:
                amount += blast.amount * blast.splash(0)
            if barrier is not None:
                self._damage_barrier(barrier, amount, shooter=rocket.shooter, source_body=rocket.source_body)
            elif amount > 0.0:
                self.walls.damage(pillar[1], rocket.shooter, amount)

    def _reflect_projectile(self, rocket: Rocket, defender: Target, point: tuple) -> None:
        """Transfer authority using the existing Deflect body contact and aim model."""
        if rocket.shot is not None:
            self.projectiles.absorbed(rocket.shot)
            rocket.shot = None
        owner = defender.owner
        rocket.shooter, rocket.team, rocket.source_body = owner, defender.team, defender.entity
        rocket.position = point
        rocket.direction = aim(owner.mover.yaw, owner.mover.pitch)
        if rocket.velocity is not None:
            rocket.velocity = tuple(expr.f32(axis * rocket.speed) for axis in rocket.direction)

    def _absorb_projectile(self, rocket: Rocket, limit: float, barrier, *, impact: bool) -> bool:
        """Resolve scoped Matrix/Grasp contacts before body, world or shield impact."""
        if not deflection.absorbable(rocket.definition):
            return False
        if barrier is not None:
            limit = min(limit, barrier[0])
            impact = True
        grasp = absorption.grasp_intersection(self, rocket.shooter, rocket.position, rocket.direction, limit)
        if grasp is not None and impact and grasp[0] >= limit:
            grasp = None
        matrix = self._matrix_hit(rocket.shooter, rocket.position, rocket.direction,
                                  grasp[0] if grasp is not None else limit, rocket.definition)
        if matrix is not None and impact and matrix >= limit:
            matrix = None
        if matrix is None and grasp is None:
            return False
        grasp_first = grasp is not None and (matrix is None or grasp[0] <= matrix)
        distance = grasp[0] if grasp_first else matrix
        rocket.flown += distance / rocket.speed
        self.rockets.remove(rocket)
        if rocket.shot is not None:
            self.projectiles.absorbed(rocket.shot)
        if grasp_first:
            # Nominal hit amounts are the server reconstruction for the Grasp
            # input. The authored accumulator and shield conversion run in 1D02.
            amount = sum(max(0.0, hit.amount) for hit in (rocket.direct, rocket.blast) if hit is not None)
            absorption.absorbed(grasp[1], amount)
        return True

    def _expire(self, rocket: Rocket) -> None:
        self._landed(rocket)
        if rocket.detonate_on_expiry:
            # Server reconstruction for Flashbang's authored range/time limit.
            self._explode(rocket, rocket.position, None)

    def _blast_cover_contact(self, shooter, origin, direction, reach):
        return (self._barrier_contact(shooter, origin, direction, reach)
                or self._cryo_contact(shooter, origin, direction, reach))

    def _explode(self, rocket: Rocket, point: tuple, wall: float | None) -> None:
        blast = rocket.blast
        if blast is None or blast.amount <= 0.0:
            return
        if blast.biotic_duration:
            biotic.explode(self, rocket, point, wall)
            return
        def cover_contact(origin, direction, reach):
            return self._blast_cover_contact(rocket.shooter, origin, direction, reach)

        for target in self._hit_targets(rocket.shooter, blast):
            a, b = target.segment()
            distance = max(0.0, segment_distance(point, a, b) - target.radius)
            scalar = blast.splash(distance)
            visible_from = _along(point, rocket.direction, -.0001) if wall is not None else point
            if scalar > 0.0 and capsule_visible(
                self._collision(), visible_from, a, b, target.radius, walls=self.walls,
                cover_contact=None if blast.healing else cover_contact,
            ):
                self._apply_hit(
                    rocket.shooter, target, blast, blast.amount * scalar, False, self.tick,
                    world_tick=self.tick, source_body=rocket.source_body,
                )

    def _landed(self, rocket: Rocket, point=None) -> None:
        """The projectile ended (hit, range, lifetime): the predicted one's release follows."""
        if rocket in self.rockets:
            self.rockets.remove(rocket)
            if rocket.spawn == graviton.FIELD_ENTITY:
                self.zarya_gravitons.landed(rocket, rocket.position if point is None else point, TICK)
        if rocket.shot is not None:
            self.projectiles.flown(rocket.shot, rocket.flown)

    def _hit_targets(self, shooter, definition: HitDef | None) -> list[Target]:
        """Healing volleys acquire living teammates, excluding the source body.

        This preserves the authored health operation; it does not implement a hero's
        separate targeting cone, lingering aura or projectile piercing rules.
        """
        if definition is not None and definition.detonate_allies:
            return [t for t in self._targets() if t.owner is not shooter and not self.vitals_of(t).dead]
        if definition is None or not definition.healing:
            return self._enemies(shooter)
        if self.match.game_map.free_for_all:
            return []
        source = _source_entity(shooter)
        return [target for target in self._targets()
                if target.owner is not shooter and target.entity != source
                and target.team == shooter.team and not self.vitals_of(target).dead]

    def _apply_hit(self, shooter, target, definition, amount, critical, frame, *, world_tick=None,
                   source_body=None):
        if not definition.healing:
            shielded = self.zarya_bubbles.for_target(target.owner) is not None
            self._damage(shooter, target, amount, critical, definition.loadout, frame,
                         world_tick=world_tick, source_body=source_body, damage_tags=definition.tags)
            if shielded:
                return
            vitals = self.vitals_of(target)
            if definition.sleep_duration > 0 and not vitals.dead and not self.damage_immune(target.owner):
                vitals.sleep_until = self.now + definition.sleep_duration
                vitals.sleep_wake_after = self.now + SLEEP_WAKE_DELAY
                vitals.sleep_view = None
                self._interrupt(target)
            self._hit_effect(target, definition.effect)
            return
        # Recheck allegiance at impact: a projectile may outlive a team change.
        if (self.match.game_map.free_for_all or target.team != shooter.team
                or target.owner is shooter or target.entity == _source_entity(shooter)):
            return
        vitals = self.vitals_of(target)
        if vitals.dead:
            return
        moira.spray_hit(self, shooter, target, definition, source_body=source_body)
        given = self._heal_pools(target.owner, vitals, amount)
        if given > 0.0 and not isinstance(shooter, bots.Bot):
            shooter.stats.add(stats.HEALING, given)
            moira.gain_combat_charge(shooter, given, body=source_body, healing_target=target,
                                     healing_tags=definition.tags)

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
            given = self._heal_pools(target.owner, vitals, amount)
            if given <= 0.0:
                continue
            healer.stats.add(stats.HEALING, given)

    def _heal_pools(self, owner, vitals, amount, *, maximum=None):
        """Apply incoming healing status to all consumers before pool accounting.

        Resource budgets/attribution remain at callers. Orb maximum is an actual
        health budget, so amplification cannot overspend its last slice.
        """
        if vitals.dead or self.now < vitals.healing_block_until:
            return 0.0
        if self.now < vitals.healing_boost_until:
            amount *= 1.5
        if maximum is not None:
            amount = min(amount, maximum)
        given = vitals.heal(amount)
        if given > 0:
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
        *, world_tick: int | None = None, source_body: int | None = None,
        maximum_damage: float | None = None, damage_tags: tuple[int, ...] = (),
    ) -> None:
        vitals = self.vitals_of(target)
        if vitals.dead or amount <= 0.0 or self.damage_immune(target.owner):
            return
        from ow174.game import nano
        amount *= nano.offense(self, _source_entity(shooter) if source_body is None else source_body)
        bubble = self.zarya_bubbles.for_target(target.owner)
        if bubble is not None:
            if maximum_damage is not None:
                amount = min(amount, maximum_damage)
            self.zarya_bubbles.damage(bubble, amount, shooter, source_entity=source_body)
            return
        if self.now < vitals.nano_until:
            amount *= 0.5
        if maximum_damage is not None:
            amount = min(amount, maximum_damage)
        adaptive_shield = vitals.temporary_shields.get(moira.ADAPTIVE_SHIELD_TAG)
        shield_before = adaptive_shield.now if adaptive_shield is not None else 0.0
        dealt = vitals.damage(amount)
        if dealt <= 0.0:
            return
        killed = vitals.now[HEALTH_POOL] <= EPSILON
        if not killed and self.now < vitals.sleep_until:
            instigator = _source_entity(shooter) if source_body is None else source_body
            self._wake_sleep(vitals, instigator, damage_tags)
        if killed:
            vitals.sleep_until = vitals.sleep_wake_after = 0.0
            vitals.sleep_view = None
        if killed:
            vitals.now[HEALTH_POOL] = 0.0
            vitals.died_at = self.now
            vitals.death_tick = self.match.tick if world_tick is None else world_tick
            vitals.killer = _source_entity(shooter) if source_body is None else source_body
        if not isinstance(shooter, bots.Bot):
            flags = (CRITICAL if critical else 0) | (KILLING if killed else 0)
            self._tell_hit(shooter, target, dealt, flags, frame)
            shooter.stats.add(stats.HERO_DAMAGE, dealt)
            shield_after = adaptive_shield.now if adaptive_shield is not None else 0.0
            moira.gain_combat_charge(
                shooter, dealt, body=source_body, damage_target=target, damage_tags=damage_tags,
                adaptive_loss=shield_before - shield_after,
            )
        victim = target.owner
        if killed and isinstance(victim, bots.Bot):
            # The NPC death script owns its terminal health change and death event ordering.
            self._bot_died(shooter, victim, critical, loadout, vitals.death_tick, source=source_body)
            return
        self._sync_body_health(victim, vitals)
        if killed:
            self._died(shooter, victim, critical, loadout, source=source_body)

    def _wake_sleep(self, vitals, instigator, damage_tags):
        remaining = vitals.sleep_until - self.now
        if remaining <= 0.5:
            return
        definition = graph.graph(0x224E)
        if definition is not None:
            # The Match owns the active deadline. Run its damage packet through
            # the authored receiver using that deadline's current context.
            component = runtime.Component(vitals.entity, strict=True)
            instance = component.create(definition, init=False)
            instance.initialized = True
            instance.write((0, 27561), expr.Asset(SLEEP_VARIANT))
            instance.write((0, 11310), SLEEP_DART_DURATION)
            instance.write((0, 1903), remaining)
            tags = tuple(expr.Asset(tag) for tag in damage_tags)
            packet = message_receive.Packet(
                SLEEP_WAKE_MESSAGE, instigator, instigator, ((SLEEP_WAKE_TAGS, tags),),
            )
            try:
                message_receive.deliver(component, packet)
                component.run_now()
                updated = expr.to_float(instance.find_var(0, 1903).value(), remaining)
                if updated < remaining:
                    vitals.sleep_until = self.now + updated
                return
            except runtime.UnsupportedScriptError as error:
                expr.warn_once("sleep-wake-resource", "%s", error)
            finally:
                component.destroy(instance)
        if self.now > vitals.sleep_wake_after + EPSILON and 0x0D8000000000191F not in damage_tags:
            vitals.sleep_until = self.now + 0.5

    def _tell_hit(self, shooter, target: Target, dealt: float, flags: int, frame: int) -> None:
        """25001 to the shooter: the hit marker and the target's health bar (frame: the shot's)."""
        value = {
            "+0x78": {"+0x0": {"+0x0": target.entity}, "+0x4": max(1, round(dealt)), "+0x8": flags},
            "+0x84": frame & 0xFFFFFFFF,
        }
        self._queue_notice(shooter, target.owner, target.entity, HIT, value)

    def _kill_notice(self, killer, victim, *, source: int | None = None) -> None:
        """25005 to the killer: "ELIMINATED: <name>" (1126.07C; +0x89 assist would be 01F0.07C, +0x88 a
        score shown when above 0). Its handler (0x7FF7895D2B50) reads only +0x84, +0x88 and +0x89."""
        entity = _source_entity(victim)
        notice = {
            "+0x78": 0,
            "+0x80": {"+0x0": _source_entity(killer) if source is None else source},
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
    def _death_parameters(killer, critical: bool, loadout, *, source: int | None = None) -> dict:
        return {
            KILLER: expr.Entity(_source_entity(killer) if source is None else source),
            CRIT: bool(critical),
            LOADOUT: expr.Asset(loadout) if loadout else None,
            ASSISTS: (),
        }

    def _bot_died(self, killer, bot: bots.Bot, critical: bool, loadout, tick: int,
                  *, source: int | None = None) -> None:
        self.stuns.pop(bot.entity, None)
        self.zarya_bubbles.retire(bot.entity)
        self.zarya_gravitons.release(bot)
        self.heavy_charge_contacts.pop(bot.entity, None)
        self.clear_pins(bot.entity)
        source = _source_entity(killer) if source is None else source
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

    def _died(self, killer, victim, critical: bool, loadout, *, source: int | None = None) -> None:
        """A player died: the notices, his body script's "died" (the kill feed line), and the respawn."""
        self.stuns.pop(victim.body, None)
        self.zarya_gravitons.release(victim)
        self.clear_pins(victim.body)
        cryo.retire_owner(self, victim)
        label = self.match.label()
        log.info("[game] %s: %s killed %s", label, killer.name, victim.name)
        victim.stats.add(stats.DEATHS)
        bot_killer = isinstance(killer, bots.Bot)
        source = _source_entity(killer) if source is None else source
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
                photon_barrier.interrupt(script)
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
