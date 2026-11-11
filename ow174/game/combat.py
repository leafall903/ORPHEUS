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
    bots,
    defenses,
    deflection,
    heavy_charge,
    matrix,
    observers,
    orisa_barrier,
    pools,
    projectiles,
    reinhardt_barrier,
    sigma_grasp,
    stats,
    ultimate,
    view_offsets,
    voicelines,
    winston_barrier,
    world,
    zarya_barrier,
)
from ow174.game.bits import BitWriter
from ow174.game.collision import MOVER, World
from ow174.game.commands import Command
from ow174.game.mover import mover_data
from ow174.game.script import expr, graph, nodes, runtime
from ow174.game.script.bot import DIED, ENFORCER, ENFORCER_PRIMARY, BotScript
from ow174.game.script.driver import MAX_GAP, Snapshot
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
EYE_STANDING = {0x04000000000003CF: 1.55, 0x04000000000002A3: 1.425}
EYE_CROUCHED = {0x04000000000003CF: 1.085, 0x04000000000002A3: .9975}
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
BLAST_SURFACE_OFFSET = 1e-4  # m: keep a wall impact's query on the incoming side (reconstructed)
FLASHBANG_EFFECT = 0x04A0000000000079  # .053/0079 ->0228 ->224E
FLASHBANG_MODE_FLAG = 13408  # 0228 duration: flag set1.7s, otherwise0.8s
SLEEP_EFFECT = 0x04A000000000026F  # 0BDE/node51 -> 224E profile 676B
SLEEP_DURATION = 5.0
SLEEP_DAMAGE_GRACE = 0.5  # 224E/node166
SLEEP_WAKE_DURATION = 0.5  # 224E/node145
ANA_GRENADE_EFFECT = 0x04A0000000000267  # 0C71 -> friendly 0B99 / hostile 0C72
ANA_GRENADE_DURATION = 4.0
ANA_GRENADE_DAMAGE = 60.0
ANA_GRENADE_HEAL = 100.0
ANA_GRENADE_BOOST = 1.5
ANA_GRENADE_BOOST_DELAY = 0.05
ANA_RIFLE_DURATION = 0.6  # 0B95, reached by the rifle's indirect hit effects
SOLDIER_WEAPON_LOADOUT = 0x0B9000000000008E


# --- geometry -----------------------------------------------------------------------------------------


def eye_height(body: int, crouched: bool = False) -> float:
    """Authored FirstPerson offsets; animated camera/muzzle equivalence is open."""
    known = view_offsets.get(body)
    if known is not None:
        return known[1 if crouched else 0]
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


def _enforcer_pellets(shooter, state, forward, count):
    """Use the authored ring sizes/counts with a reconstructed fixed azimuth."""
    pattern = state.node.field("m_5EC1EB38") or {}
    spread = _number(state.evaluate(pattern.get("m_spread"), watch=False), 0.0)
    if not math.isfinite(spread) or not 0.0 <= spread < 90.0 or not 1 <= count <= 64:
        return ()
    yaw = round(shooter.yaw) * math.tau / 65536
    right = (math.cos(yaw), 0.0, -math.sin(yaw))
    x, y, z = forward
    up = (-y * math.sin(yaw), z * right[0] - x * right[2], -y * math.cos(yaw))
    result = []
    for ring in pattern.get("m_0D30E40C") or ():
        radius = state.evaluate(ring.get("m_6425FFB9"), watch=False)
        size = state.evaluate(ring.get("m_B19BD35D"), watch=False)
        if (type(radius) not in (int, float) or not 0.0 <= radius <= 1.0
                or type(size) is not int or not 0 < size <= count - len(result)):
            return ()
        angle = math.radians(spread * radius)
        for index in range(size):
            azimuth = index * math.tau / size
            a, b = math.cos(azimuth) * math.sin(angle), math.sin(azimuth) * math.sin(angle)
            result.append(tuple(f * math.cos(angle) + r * a + u * b
                                for f, r, u in zip(forward, right, up, strict=True)))
    return tuple(result) if len(result) == count else ()


def _sub(a, b):
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def _along(origin, direction, t: float):
    return (origin[0] + direction[0] * t, origin[1] + direction[1] * t, origin[2] + direction[2] * t)


def sphere_surface(point, centre, radius):
    offset = _sub(point, centre)
    length = math.sqrt(_dot(offset, offset))
    return _along(centre, offset, radius / length) if length > EPSILON else point


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
        self.ult_healing_deficit = 0.0  # enemy-caused missing HP eligible for healing charge (reconstructed)
        self.bonus_shields = 0.0  # scoped unhealable Sigma extra pool, not ordinary shields
        self.bonus_shields_max = 0.0

    @property
    def dead(self) -> bool:
        return self.died_at is not None

    @property
    def total(self) -> float:
        return self.base_total + self.bonus_shields

    @property
    def base_total(self) -> float:
        return sum(self.now.values())

    @property
    def base_missing(self) -> float:
        return max(0.0, sum(self.most.values()) - self.base_total)

    def damage(self, amount: float, *, reduce_armour: bool = True) -> float:
        """Take a hit from shields, then armour, then health (unverified, as OW1 did: what reaches armour
        while it has some is ARMOUR_BLOCK less, at most halved). Returns what the pools lost."""
        left = max(0.0, amount)
        taken = min(left, self.bonus_shields)
        if taken > 0:
            self.bonus_shields -= taken
            left -= taken
            self.changed.add(SHIELDS_POOL)
        for kind in (SHIELDS_POOL, ARMOUR_POOL, HEALTH_POOL):
            if left <= 0.0:
                break
            if reduce_armour and kind == ARMOUR_POOL and self.now[kind] > 0.0:
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
        given = max(0.0, amount) - left
        self.ult_healing_deficit = max(
            0.0, min(self.ult_healing_deficit - given, self.base_missing),
        )
        return given

    def component(self, kinds) -> dict:
        """Component 51 with these pools' fields set (the others are left as they are)."""
        parts: list = [None] * (3 * POOLS)
        for kind in kinds:
            if kind in self.ids:
                parts[kind * POOLS] = world.pool(self.ids[kind], kind, self.most[kind], self.now[kind])
        if self.bonus_shields_max:
            # Extra pool id/slot and over-health byte17 are reconstructed transport.
            value = world.pool(4, SHIELDS_POOL, self.bonus_shields_max, self.bonus_shields)
            parts[SHIELDS_POOL * POOLS + 1] = value[:17] + bytes((0,)) + value[18:]
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
    self_scale: float = 0.0  # scoped Helix consumer of explosion m_8A3E3883 (reconstructed meaning)
    horizontal_impulse: float = 0.0
    vertical_impulse: float = 0.0
    impulse_rings: tuple = ()  # radius, enemy scale, own scale (reconstructed consumers)
    deflectable: bool = False
    biotic_rifle: bool = False

    def impulse_scale(self, distance: float, *, own: bool = False) -> float:
        column = 2 if own else 1
        rings = tuple((row[0], row[column]) for row in self.impulse_rings)
        return self._ring_scale(rings, distance)

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
    def _ring_scale(rings, distance: float) -> float:
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
    impulse_rings = []
    for ring in modify.get("m_knockbackRings") or []:
        if not isinstance(ring, dict):
            continue
        values = tuple(_number(state.evaluate(ring.get(key), watch=False), 0.0)
                       for key in ("m_radius", "m_579D7CFF", "m_10399AE7"))
        impulse_rings.append(values)

    def force(name):
        cfg = modify.get(name)
        return _number(state.evaluate(cfg, watch=False), 0.0) if cfg is not None else 0.0

    return HitDef(
        amount=amount,
        loadout=loadout.guid if isinstance(loadout, expr.Asset) else None,
        falloff_start=value("m_EAD7F104"),
        falloff_end=value("m_157E5BC5"),
        falloff_floor=value("m_72A81154"),
        critical=value("m_A872C70E", 1.0),
        rings=tuple(sorted(rings)),
        effect=effect.guid if isinstance(effect, expr.Asset) else None,
        self_scale=value("m_8A3E3883", 0.0),
        horizontal_impulse=force("m_E33EFEE1"),
        vertical_impulse=force("m_583C208B" if modify.get("m_583C208B") is not None else "m_10399AE7"),
        impulse_rings=tuple(sorted(impulse_rings)),
        deflectable=any(
            isinstance(value := state.evaluate(tag.get("m_EE3055BD"), watch=False), expr.Asset)
            and value.guid == deflection.DEFLECTABLE
            for tag in modify.get("m_148F3152") or () if isinstance(tag, dict)
        ),
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
    source_entity: int | None = None  # body identity at emission; a replacement is not its own source
    definition: int | None = None  # original003 definition, independent of prediction/ownership
    launch_tick: int | None = None  # world boundary before the first flight step
    gravity: float | None = None
    range_limit: float | None = None
    distance: float = 0.0
    rebound_limit: int | None = None
    rebound_scale: float = 0.0
    rebounds: int = 0

    def set_velocity(self, vx: float, vy: float, vz: float) -> None:
        self.speed = math.hypot(vx, vy, vz)
        if self.speed:
            self.direction = (vx / self.speed, vy / self.speed, vz / self.speed)

    def travel(self, distance: float, *, seconds: float | None = None) -> float:
        if seconds is None:
            seconds = distance / self.speed
        self.distance += distance
        self.flown += seconds
        self.left -= seconds
        return seconds

    def stamp_launch(self, tick: int) -> None:
        self.launch_tick = tick
        if self.shot is not None and self.shot.spawn == projectiles.FIELD_ENTITY:
            # The host starts the canister's deployment delay on its first world step.
            self.shot.field_due = tick + 1 + projectiles.FIELD_DELAY

    def flight_cursor(self) -> int | None:
        return self.launch_tick if self.last_tick is None else self.last_tick


@dataclass
class Sleep:
    start: int
    end: int
    waking: bool = False


@dataclass
class BioticDose:
    shooter: object
    source: int
    target: int
    healing: bool
    start: int
    amount: float
    loadout: int | None
    frame: int
    applied: float = 0.0


@dataclass
class HealingModifier:
    anti_from: int = 0
    anti_until: int = 0
    boost_from: int = 0
    boost_until: int = 0

    def factor(self, first, last):
        """Mean multiplier for inclusive healing ticks, with anti-heal taking precedence."""
        end = last + 1
        count = end - first
        anti = max(0, min(end, self.anti_until) - max(first, self.anti_from))
        boost = max(0, min(end, self.boost_until) - max(first, self.boost_from))
        overlap = max(0, min(end, self.anti_until, self.boost_until)
                      - max(first, self.anti_from, self.boost_from))
        return (count - anti + (ANA_GRENADE_BOOST - 1) * (boost - overlap)) / count


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
        self.cryo_samples: dict[int, tuple[tuple[int, int, int], float]] = {}
        self.stuns: dict[int, int] = {}  # body identity -> exclusive world-tick deadline
        self.sleeps: dict[int, Sleep] = {}
        self.heal_modifiers: dict[int, HealingModifier] = {}
        self.biotic_doses: list[BioticDose] = []
        self.pins: dict[int, int] = {}  # manual NPC source -> current victim body
        self.grasp = sigma_grasp.SigmaGrasp(self)
        self.barriers = reinhardt_barrier.ReinhardtBarriers(self)
        self.zarya_barriers = zarya_barrier.Barriers(self)
        self.winston_barriers = winston_barrier.Barriers(self)
        self.orisa_barriers = orisa_barrier.Barriers(self)
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
        self.cryo_samples.pop(player.body, None)
        self.stuns.pop(player.body, None)
        self.sleeps.pop(player.body, None)
        self.heal_modifiers.pop(player.body, None)
        self.biotic_doses[:] = [dose for dose in self.biotic_doses if dose.target != player.body]
        self.release_pins(player.body)
        self.grasp.retire(player.body)
        self.barriers.retire(player.body)
        self.zarya_barriers.retire(player.body)
        self.projectiles.retire_fields(player)
        # The owner's leased projectiles and their observer copies retire with the old body.
        self.rockets[:] = [
            rocket for rocket in self.rockets if rocket.shooter is not player
            or (rocket.shot is None and rocket.source_entity != player.body)
        ]
        self.respawns[:] = [(when, who) for when, who in self.respawns if who is not player]

    def record_replacement(self, body: int) -> None:
        """Keep the creation boundary independent of the rolling pose history."""
        self.replacement_ticks[body] = self.match.tick
        self.stuns.pop(body, None)
        self.sleeps.pop(body, None)
        self.heal_modifiers.pop(body, None)

    def is_stunned(self, actor, *, tick: int | None = None) -> bool:
        entity = _source_entity(actor)
        now = self.tick if tick is None else tick
        sleep = self.sleeps.get(entity)
        return self.stuns.get(entity, 0) > now or (sleep is not None and sleep.end > now)

    def is_controlled(self, actor, *, tick: int | None = None) -> bool:
        return self.is_stunned(actor, tick=tick) or _source_entity(actor) in self.pins.values()

    def release_pins(self, entity: int) -> None:
        for source, target in tuple(self.pins.items()):
            if entity in (source, target):
                del self.pins[source]

    def _hit_effect(self, target: Target, effect: int | None) -> None:
        """Resource-backed stun reconstruction; native immunity/presentation remain unverified."""
        if (effect not in (FLASHBANG_EFFECT, SLEEP_EFFECT) or self.vitals_of(target).dead
                or defenses.invulnerable(target.owner)
                or defenses.fortify_scale(target.owner) is not None):
            return
        if effect == SLEEP_EFFECT:
            self.sleeps[target.entity] = Sleep(self.tick, self.tick + math.ceil(SLEEP_DURATION / TICK))
        else:
            variables = graph.mode_variables(self.match.game_map.mode_guid) or {}
            duration = 1.7 if expr.truthy(variables.get(FLASHBANG_MODE_FLAG)) else 0.8
            deadline = self.tick + math.ceil(duration / TICK)
            self.stuns[target.entity] = max(deadline, self.stuns.get(target.entity, 0))
        self.release_pins(target.entity)
        self._interrupt(target)

    def _interrupt(self, target: Target) -> None:
        """Shared scoped host interruption, retaining the existing active-Visor policy."""
        if not isinstance(target.owner, bots.Bot):
            # Stop existing voluntary horizontal motion at onset, rather than
            # deleting every later external impulse during each stunned pose.
            mover = target.owner.mover
            mover.velocity = (0.0, mover.velocity[1], 0.0)
        script = getattr(target.owner, "body_script", None)
        if script is None:
            return
        component = script.component
        # Soldier0259's active transformation has no running-condition/stack
        # dependency on control. Preserve its authored countdown/search lifetime;
        # stun still suppresses movement and weapon input. This scoped policy
        # is reconstruction, not a generic native interruption classification.
        preserve_visor = getattr(script, "hero", None) == 0x02E000000000006E and any(
            instance.graph.index == 0x0259 and isinstance(state, nodes.TrackTargets)
            and state.active and (state.node.field("m_8A2905A0") or {}).get("m_identifier") == 2337
            for instance in component.instances.values() for state in instance.states.values()
        )
        component.edges([(value, False) for value in tuple(component.held)])
        if isinstance(script, BotScript):
            script.cancel_primary_windup()
        for instance in list(component.instances.values()):
            for state in list(instance.states.values()):
                if instance.graph.index == 0x0257 and state.node.pos == 8 and state.active:
                    # The Field launch timer starts outside the ability's running
                    # subgraph, so stopping the ability leaves the throw queued.
                    state.request(runtime.ABORT)
                elif isinstance(state, nodes.Ability):
                    state.presses.clear()
                    if not (preserve_visor and state in script.ultimates):
                        state.stop()  # use authored exit/cooldown paths for running channels
        component.run_now()
        component.shots.clear()
        if isinstance(target.owner, bots.Bot):
            heavy_charge.interrupt(self, target.owner)

    def spawned(self, player) -> None:
        """The player's client has the world: the game mode entity gets its UI graph (mode_frame). While
        heroes are assembled the countdown's frames carry it."""
        if player.client is not None and not self.match.assembling():
            player.client.queue_mode_frame(mode_frame(
                {}, player.match.game_map.mode_guid, player.match.game_map.map_guid,
            ))
        self.voice.spawned(player, self.now)

    def command(self, player, command: Command | None) -> None:
        """Consume posed attacks and already admitted delayed throws from missing windows."""
        if self.match.collision_world is None:
            return
        script = player.body_script
        if script is None:
            return
        shots, script.component.shots[:] = list(script.component.shots), []
        self.barriers.sync(player)
        self.zarya_barriers.sync(player)
        if self.is_dead(player) or self.is_controlled(player):
            return
        defenses.advance_entry(player)
        self._settle_cryo_healing(player)
        for state, number, time, activation in shots:
            try:
                if command is not None:
                    self._shoot(player, command, state, number, time, activation=activation)
                elif (state.instance.graph.index, state.node.pos) in ((0x0257, 4), (0x0BDD, 14)):
                    anchor = script.world_frame_anchor
                    if anchor is not None and player.mover.last_command is not None:
                        anchor_tick, anchor_frame = anchor
                        elapsed = max(0, time // script.frame_ms - anchor_frame) * script.frame_ms
                        release_tick = min(self.tick, anchor_tick + math.ceil(elapsed / (TICK * 1000)))
                        self._shoot(player, None, state, number, time, activation=activation,
                                    unposed_release_tick=release_tick)
                else:
                    self.orisa_barriers.launch_unposed(player, state, time)
            except Exception:
                log.exception("[game] %s: %s's shot failed", self.match.label(), player.name)
        try:
            self._melee(player, command, script)
        except Exception:
            log.exception("[game] %s: %s's melee failed", self.match.label(), player.name)

    def _settle_cryo_healing(self, player) -> None:
        sample = defenses.cryo_healing_sample(player)
        if sample is None or self.cryo_samples.get(player.body) == sample:
            return
        key, cumulative = sample
        previous_key, previous = self.cryo_samples.get(player.body, (None, 0.0))
        if key != previous_key:
            # Cryo-Freeze clears an active hostile grenade debuff on entry.
            # Keep its elapsed window and any independent friendly boost.
            modifier = self.heal_modifiers.get(player.body)
            if modifier is not None and modifier.anti_from <= self.tick < modifier.anti_until:
                modifier.anti_until = self.tick
        self.cryo_samples[player.body] = sample
        due = cumulative - (previous if key == previous_key else 0.0)
        if due <= 0:
            return
        target = next((target for target in self._targets() if target.owner is player), None)
        if target is None:
            return
        vitals = self.vitals_of(target)
        if not vitals.dead and (given := vitals.heal(due * self._healing_factor(target.entity))) > 0:
            player.stats.add(stats.HEALING, given)
            self._sync_body_health(player, vitals)

    def _npc_attack_source(self, state):
        component = state.instance.component
        return next((bot for bot in self.match.bots
                     if bot.entity == component.entity and bot.body_script is not None
                     and bot.body_script.component is component and not bot.dead
                     and not self.is_stunned(bot)), None)

    def heavy_charge_wall_probe(self, state):
        """Evaluate the 1408 wall probes against static mover collision on entry."""
        source = self._npc_attack_source(state)
        collision = self._collision()
        if source is None or collision is None or self.match.ended:
            return
        start = state.evaluate(state.node.field("m_startPosWS"), watch=False)
        direction = state.evaluate(state.node.field("m_dirWS"), watch=False)
        length = _number(state.evaluate(state.node.field("m_castLength"), watch=False), 0.0)
        output = expr.dynamic(state.node.field("m_out_HitSomething"))
        if (not isinstance(direction, expr.Vec3)
                or not any(direction) or length <= 0 or output is None):
            return
        if start == expr.Entity(source.entity):
            origin = source.position
        elif isinstance(start, expr.Vec3):
            # The permissive expression yields the vector offset in self + offset.
            origin = tuple(a + b for a, b in zip(source.position, start, strict=True))
        else:
            return
        hit = ray_world(collision, origin, tuple(direction), length)
        state.instance.write(output, hit is not None)

    def npc_contact_targets(self, state, *, required_target_flag=None):
        """Host a scoped NPC contact search with a yaw-relative sphere and static LOS."""
        source = self._npc_attack_source(state)
        if source is None or self.match.ended:
            return ()
        offset = state.evaluate(state.node.field("m_05BE62DD"), watch=False)
        radius = _number(state.evaluate(state.node.field("m_radius"), watch=False), 0.0)
        if not isinstance(offset, expr.Vec3) or radius <= 0.0:
            return ()
        x, y, z = source.position
        forward_x, _, forward_z = aim(source.yaw, 0)
        centre = (x + offset.x * forward_z + offset.z * forward_x, y + offset.y,
                  z - offset.x * forward_x + offset.z * forward_z)
        origin = (x, y + EYE, z)
        recipients = []
        for target in self._enemies(source):
            if (state.instance.graph.index == heavy_charge.GRAPH and state.node.pos == 32
                    and target.entity == self.pins.get(source.entity)):
                continue
            if required_target_flag is not None:
                script = target.owner.body_script
                flag = script.component.entity_var(required_target_flag, create=False) if script else None
                if flag is None or not expr.truthy(flag.value()):
                    continue
            a, b = target.segment()
            if (segment_distance(centre, a, b) <= radius + target.radius
                    and capsule_visible(self._collision(), origin, a, b, target.radius)):
                recipients.append(expr.Entity(target.entity))
        return tuple(recipients)

    def npc_contact_hit(self, state, *, knockback=False):
        """Apply the authored melee or charge contact once per activation."""
        source = self._npc_attack_source(state)
        if source is None or self.match.ended:
            return
        recipients = state.evaluate(state.node.field("m_FA3ECC43"), watch=False)
        if not isinstance(recipients, tuple) or not recipients:
            return
        health = state.node.field("m_modifyHealth")
        amount = _number(state.evaluate(health["m_amount"], watch=False), 0.0)
        origin = state.evaluate(health["m_C0139FA6"], watch=False) if knockback else None
        if isinstance(origin, expr.Vec3):
            horizontal = _number(state.evaluate(health["m_E33EFEE1"], watch=False), 0.0)
            vertical = _number(state.evaluate(health["m_10399AE7"], watch=False), 0.0)
        for target in self._enemies(source):
            if expr.Entity(target.entity) in recipients and not self.zarya_barriers.intercept_target(
                source, target.owner, amount,
            ):
                self._damage(source, target, amount, False, None, self.tick)
                if isinstance(origin, expr.Vec3) and not self.vitals_of(target).dead:
                    self._radial_impulse(target, tuple(origin), horizontal, vertical)


    def _melee(self, player, command, script) -> None:
        """Settle 0043's damage and contact force once using the host's historical capsule search."""
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
            origin = state.evaluate(modify.get("m_C0139FA6"), watch=False)
            if isinstance(origin, expr.Vec3):
                horizontal = _number(state.evaluate(modify.get("m_E33EFEE1"), watch=False), 0.0)
                vertical = _number(state.evaluate(modify.get("m_10399AE7"), watch=False), 0.0)
            loadout = (
                state.evaluate(modify.get("m_loadout"), watch=False) if modify.get("m_loadout") else None
            )
            loadout = loadout.guid if isinstance(loadout, expr.Asset) else None
            centre = _along(self._eye(player), aim(*command.aim()), reach)
            view_tick = command.frame - command.view_delay(player.quanta) * 1e6 / player.quanta
            self.barriers.melee(player, centre, radius, amount, view_tick=view_tick)
            protected = self.zarya_barriers.melee(
                player, centre, radius, amount, _source_entity(player), view_tick=view_tick,
            )
            for target in self._enemies(player):
                if target.entity in protected:
                    continue
                shown = self.seen(target, view_tick)
                if shown is None:
                    continue
                a, b = shown.segment()
                if (segment_distance(centre, a, b) - shown.radius <= radius and amount > 0.0
                        and capsule_visible(self._collision(), self._eye(player), a, b, shown.radius)
                        and deflection.frontal(target, aim(*command.aim())) is None):
                    self._damage(player, target, amount, False, loadout, command.frame)
                    if isinstance(origin, expr.Vec3) and not self.vitals_of(target).dead:
                        self._radial_impulse(target, tuple(origin), horizontal, vertical)

    def _advance_idle_protection(self, player) -> None:
        """Fill expired command windows before resolving impacts."""
        script = player.body_script
        if (script is None or script.world_frame_anchor is None or script.retired
                or script.component.owner_dead or not player.spawned or not player.has_body):
            return
        visor_running = defenses.soldier_visor_running(player)
        input_recovery = defenses.soldier_input_recovery_active(player)
        practice_charge = ultimate.practice_passive_clock(script)
        field_windup = defenses.soldier_field_windup(player)
        dart_windup = defenses.ana_sleep_dart_windup(player)
        world_clock = defenses.uses_world_clock(player)
        if not (visor_running or input_recovery or field_windup or dart_windup or practice_charge or world_clock):
            return
        anchor_tick, anchor_frame = script.world_frame_anchor
        elapsed_ms = max(0, self.tick - anchor_tick) * round(TICK * 1000)
        target = min(projectiles.MAX_LEASE_FRAME - 1, anchor_frame + elapsed_ms // script.frame_ms - 1)
        while target > script.frame:
            end = target
            # Keep recurring awards and phase deadlines in the VM's retained frame window.
            if (practice_charge or visor_running or input_recovery or field_windup or dart_windup
                    or defenses.projectile_protection_active(player)
                    or self.zarya_barriers.has_phase(player)
                    or self.orisa_barriers.pending(player)
                    or defenses.active_state(player, defenses.REAPER, 0x0020, 7) is not None):
                end = min(end, script.frame + MAX_GAP)
            player._advance_body_gap(end + 1)
            if player.body_script is not script:
                break
            visor_running = visor_running and defenses.soldier_visor_running(player)
            input_recovery = input_recovery and defenses.soldier_input_recovery_active(player)
            field_windup = field_windup and defenses.soldier_field_windup(player)
            dart_windup = dart_windup and defenses.ana_sleep_dart_windup(player)
            if not (visor_running or input_recovery or field_windup or dart_windup or practice_charge or world_clock):
                break

    def _combat_ticks_with_sleep_darts(self, combat_ticks):
        """Fill gaps through admitted dart release and flight to time control at contact."""
        for tick in sorted(combat_ticks):
            while self.tick + 1 < tick and (
                any(rocket.direct is not None and rocket.direct.effect == SLEEP_EFFECT
                    and rocket.flight_cursor() is not None for rocket in self.rockets)
                or any(player.spawned and player.has_body and defenses.ana_sleep_dart_windup(player)
                       and player.body_script.world_frame_anchor is not None
                       and player.body_script.frame < projectiles.MAX_LEASE_FRAME - 1
                       for player in self.match.players)
            ):
                yield self.tick + 1
            yield tick

    def update(self, now: float, tick: int, frames: bool = True,
               *, charge_obstructions: dict[int, bool] | None = None) -> None:
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
        combat_ticks = set(range(first, tick + 1))
        dose_flight = any(rocket.direct is not None and rocket.direct.biotic_rifle for rocket in self.rockets)
        healing_replay = False
        if tick > self.tick + 1:
            for player in self.match.players:
                barrier = defenses.reinhardt_barrier_state(player)
                if barrier is None or player.body_script.world_frame_anchor is None:
                    continue
                script = player.body_script
                anchor_tick, anchor_frame = script.world_frame_anchor
                # The first neutral frame starts the shield's regeneration delay.
                elapsed = (script.frame + 2 - anchor_frame) * script.frame_ms
                neutral_tick = anchor_tick + math.ceil(elapsed / (TICK * 1000))
                if self.tick < neutral_tick < tick:
                    combat_ticks.add(neutral_tick)
            for rocket in self.rockets:
                cursor = rocket.flight_cursor()
                if rocket.gravity is not None and cursor is not None:
                    start = max(self.tick, cursor)
                    end = min(tick, start + math.ceil(rocket.left / TICK))
                    combat_ticks.update(range(start + 1, end + 1))
            pending_fields = {
                player for player in self.match.players if defenses.soldier_field_windup(player)
            }
            fields = [
                shot for shot in self.projectiles.shots
                if shot.spawn == projectiles.FIELD_ENTITY
                and shot.field_due is not None and shot.field_due < tick
            ]
            # A Field can change whether an intervening hit is lethal.
            healing_replay = bool(dose_flight or self.biotic_doses or pending_fields or fields or any(
                rocket.blast is not None and rocket.blast.effect == ANA_GRENADE_EFFECT
                for rocket in self.rockets
            ))
            if pending_fields:
                # Admit pending releases in the retained command window.
                combat_ticks.update(range(retained, tick + 1))
            visor_fields = [shot for shot in fields if defenses.soldier_visor_running(shot.player)]
            if visor_fields:
                # Advance the charge gate through each remaining Field tick, even before retained.
                last = min(tick, self.projectiles.last_field_heal_tick(visor_fields))
                combat_ticks.update(range(self.tick + 1, last + 1))
        if tick > self.tick + 1 and (
            healing_replay or self.winston_barriers.actors or self.orisa_barriers.actors
            or self.orisa_barriers.flights or any(
                rocket.flight_cursor() is not None and (rocket.direct is not None or rocket.blast is not None)
                and defenses.soldier_visor_running(rocket.shooter)
                for rocket in self.rockets
            ) or any(
                defenses.projectile_protection_active(player) or self.zarya_barriers.has_phase(player)
                or self.orisa_barriers.pending(player)
                for player in self.match.players
            )
        ):
            # Sweep already admitted rockets while the phase can still change
            # their contact. Skip the empty tail of a long scheduler gap.
            dose_tail = math.ceil(ANA_RIFLE_DURATION / TICK) if dose_flight or self.biotic_doses else 0
            if self.biotic_doses:
                combat_ticks.update(range(self.tick + 1, min(tick, self.tick + dose_tail) + 1))
            for rocket in self.rockets:
                cursor = rocket.flight_cursor()
                if cursor is None:
                    continue
                start = max(self.tick, cursor)
                flight = math.floor(rocket.left / TICK) + 1 + dose_tail
                combat_ticks.update(range(start + 1, min(tick, start + flight) + 1))
        for target in self._targets():
            history = self.history.setdefault(target.entity, deque(maxlen=HISTORY))
            history.append((tick, target.feet, target.height, target.eye))
        # Replay the retained combat frames using the most recent poses.
        # A projectile with a known launch boundary joins after that boundary.
        # Deployed Fields have a bounded lifetime of their own. The NPC firing catch-up cap
        # must not discard healing from the earlier part of a long world update.
        field_first = self.tick + 1
        for at in self._combat_ticks_with_sleep_darts(combat_ticks):
            if healing_replay and field_first < at:
                # Settle the earlier healing before an impact can change health or
                # apply a modifier. Healing on the impact tick still follows combat.
                self.projectiles.heal_fields(field_first, at - 1)
                field_first = at
            self.now, self.tick = now - (tick - at) * TICK, at
            for player in self.match.players:
                self._advance_idle_protection(player)
            self.winston_barriers.update(at)
            self.orisa_barriers.update(at, TICK)
            self._advance_biotic_doses()
            for rocket in list(self.rockets):
                if at == tick or rocket.flight_cursor() is not None:
                    self._fly(rocket)
            if at >= first:
                # Only the endpoint ran this world movement sweep.
                self._bot_weapons(at, charge_obstructions if at == tick else None)
        self.projectiles.update(tick, first_tick=field_first)
        self.grasp.update()
        self.barriers.update()
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
        for entity in [key for key, modifier in self.heal_modifiers.items()
                       if max(modifier.anti_until, modifier.boost_until) <= tick]:
            del self.heal_modifiers[entity]
        for source, target in tuple(self.pins.items()):
            if source not in current or target not in current:
                del self.pins[source]
        for table in (self.history, self.vitals, self.replacement_ticks, self.stuns, self.sleeps,
                      self.heal_modifiers):
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
                hero = target.owner.hero
                found = Vitals(target.entity, *pools.active_pools(hero.guid, hero.body))
            self.vitals[target.entity] = found
        return found

    def _enemies(self, shooter) -> list[Target]:
        return self._shot_targets(shooter, False)

    def _shot_targets(self, shooter, biotic_rifle: bool) -> list[Target]:
        free_for_all = self.match.game_map.free_for_all
        source = _source_entity(shooter)
        found = []
        for target in self._targets():
            if target.owner is shooter or target.entity == source:
                continue
            friendly = not free_for_all and target.team == shooter.team
            if (friendly and not biotic_rifle) or defenses.intangible(target.owner):
                continue
            vitals = self.vitals_of(target)
            if vitals.dead or (friendly and vitals.base_missing <= EPSILON):
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

    def _bot_weapons(self, tick: int, charge_obstructions: dict[int, bool] | None) -> None:
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
                if bot.entity in self.pins:
                    instance = next((i for i in script.component.instances.values()
                                     if i.graph.index == heavy_charge.GRAPH), None)
                    if instance is not None:
                        blocked = charge_obstructions.get(bot.entity) if charge_obstructions else None
                        heavy_charge.move_victim(self, bot, instance, tick, TICK, blocked)
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
                self.pins.pop(bot.entity, None)
                bot.body_script = None
                log.exception("[game] %s: %s's weapon script failed", self.match.label(), bot.name)

    def _absorption(self, shooter, definition, origin, direction, reach, *, view_tick=None):
        grasp = self.grasp.trace(shooter, definition, origin, direction, reach, view_tick=view_tick)
        contact = matrix.trace(self, shooter, definition, origin, direction, reach, view_tick=view_tick)
        if contact is not None and (grasp is None or contact[0] < grasp[0]):
            distance, target = contact
            return distance, target, None  # Matrix consumes the shot without a Grasp conversion.
        return grasp

    def _eye(self, player) -> tuple:
        x, y, z = player.mover.position
        crouched = player.mover.crouched
        height = eye_height(player.hero.body, crouched)
        return (x, y + height, z)

    def _shoot(
        self, shooter, command, state, number: int, time: int,
        *, activation: tuple[int, int], snapshot: Snapshot | None = None,
        unposed_release_tick: int | None = None,
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
        elif command is None:
            origin = self._eye(shooter)
            direction = aim(round(shooter.mover.yaw), round(shooter.mover.pitch))
        else:
            origin, direction = self._eye(shooter), aim(*command.aim())
        if not npc and self.orisa_barriers.admits(state):
            self.orisa_barriers.launch(shooter, state, origin, direction, command.aim()[0])
            return
        direct = hit_def(state, node.field("m_6396149F"))
        if direct is not None and state.instance.graph.index == 0x0B94 and state.index in (5, 44):
            direct.biotic_rifle = True
        entity_def = (node.field("m_projectileEntity") or {}).get("m_entityDef")
        definition = state.evaluate(entity_def, watch=False) if entity_def is not None else None
        definition_guid = definition.guid if isinstance(definition, expr.Asset) else None
        field_canister = (not npc and state.instance.graph.index == 0x0257 and state.index == 1
                          and definition_guid == 0x040000000000072D)
        gravity = rebound_limit = None
        rebound_scale = 0.0
        if field_canister:
            response = node.field("m_E365DB20") or {}
            if state.evaluate(response.get("m_D9EFD182"), watch=False):
                rebound_limit = int(_number(state.evaluate(response.get("m_360294F0"), watch=False)))
                rebound_scale = _number(state.evaluate(response.get("m_0D20DAFD"), watch=False))
            for motion in node.field("m_projectileMotions") or ():
                if motion.get("$") == "STU_E1A5B579":
                    gravity = _number(state.evaluate(motion.get("m_599AB8D7"), watch=False), None)
                    break
            offset = state.evaluate(node.field("m_5EC1EB38.m_1C78B91C"), watch=False)
            launch = state.evaluate(node.field("m_5EC1EB38.m_E2FD6F04"), watch=False)
            if isinstance(offset, expr.Vec3) and isinstance(launch, expr.Vec3):
                length = math.hypot(launch.x, launch.y, launch.z)
                if length:
                    x, y, z = shooter.mover.position
                    forward_x, _, forward_z = aim(round(shooter.mover.yaw), 0)
                    origin = (x + offset.x * forward_z + offset.z * forward_x, y + offset.y,
                              z - offset.x * forward_x + offset.z * forward_z)
                    direction = (launch.x / length, launch.y / length, launch.z / length)
        speed = field("m_projectileSpeed")
        if speed:
            lifetime = field("m_projectileLifetime", 5.0) or 5.0
            reach = field("m_FEC435C6")
            bound = min(lifetime, reach / speed) if reach else lifetime
            if gravity is None:
                lifetime = bound
            blast = hit_def(state, node.field("m_D2D11CE9"))
            damaging = (direct is not None and direct.amount > 0.0) or (
                blast is not None and blast.amount > 0.0
            )
            directions = (direction,)
            if npc and shooter.hero == ENFORCER and state.instance.graph.index == ENFORCER_PRIMARY:
                directions = _enforcer_pellets(shooter, state, direction, field("m_numProjectilesPerShot", 1))
            for direction in directions:
                shot = None
                if definition_guid is not None:
                    shot = self.projectiles.fired(
                        shooter, state, shot_frame, definition_guid, bound, origin, direction,
                        activation=activation, snapshot=snapshot,
                    )
                if damaging or shot is not None:
                    rocket = Rocket(
                        shooter, shooter.team, origin, direction, speed, lifetime, direct, blast, shot,
                        last_tick=self.tick if npc else None,
                        source_entity=_source_entity(shooter),
                        definition=definition_guid,
                        gravity=gravity,
                        range_limit=reach if gravity is not None else None,
                        rebound_limit=rebound_limit if gravity is not None else None,
                        rebound_scale=rebound_scale,
                    )
                    if shot is not None and shot.spawn == projectiles.FIELD_ENTITY:
                        shot.field_airborne = True
                    release_tick = (self.tick or None) if command is not None else unposed_release_tick
                    if release_tick is not None:
                        rocket.stamp_launch(release_tick)
                    self.rockets.append(rocket)
            return
        if direct is None or direct.amount <= 0.0:
            return
        reach = field("m_FEC435C6", RANGE) or RANGE
        # The input supplies this simulation frame's aim and view time. Notices use the volley clock.
        view_tick = self.tick
        if command is not None:
            view_tick = command.frame - command.view_delay(shooter.quanta) * 1e6 / shooter.quanta
            direction = self._soldier_visor_direction(shooter, state, origin, direction, view_tick)
        travelled = 0.0
        for bounce in range(deflection.MAX_SEGMENTS):
            best = None
            for target in self._shot_targets(shooter, direct.biotic_rifle):
                shown = self.seen(target, view_tick)
                if shown is None:
                    continue
                a, b = shown.segment()
                t = ray_capsule(origin, direction, a, b, shown.radius)
                if t is not None and t <= reach and (best is None or t < best[0]):
                    best = (t, target, shown)
            reflected = deflection.trace(self, shooter, direct, origin, direction, reach, view_tick=view_tick)
            wall = ray_world(self._collision(), origin, direction, reach)
            defense_view_tick = view_tick if command is not None and not bounce else None
            absorbed = self._absorption(shooter, definition_guid, origin, direction, reach, view_tick=defense_view_tick)
            barrier = self._barrier_contact(shooter, origin, direction, reach, view_tick=defense_view_tick)
            if (barrier is not None and (best is None or barrier[0] < best[0])
                    and (wall is None or barrier[0] < wall)
                    and (reflected is None or barrier[0] < reflected[0])
                    and (absorbed is None or barrier[0] < absorbed[0])):
                self._barrier_damage(barrier, direct.amount * direct.scale(travelled + barrier[0]), shooter)
                return
            if (absorbed is not None and (best is None or absorbed[0] <= best[0])
                    and (wall is None or absorbed[0] < wall)
                    and (reflected is None or absorbed[0] < reflected[0])):
                if absorbed[2] is not None:
                    self.grasp.absorb(absorbed[2], shooter, direct, None, travelled + absorbed[0])
                return
            if (reflected is not None and (best is None or reflected[0] <= best[0])
                    and (wall is None or reflected[0] < wall)):
                t, target = reflected
                point = _along(origin, direction, t)
                travelled += t
                reach -= t
                shooter = target.owner
                direction = aim(shooter.mover.yaw, shooter.mover.pitch)
                shift = min(deflection.OFFSET, reach)
                origin = _along(point, direction, shift)
                travelled += shift
                reach -= shift
                view_tick, shot_frame = self.tick, self.tick
                continue
            if best is None or (wall is not None and wall < best[0]):
                return
            t, target, shown = best
            critical = origin[1] + direction[1] * t >= shown.feet[1] + shown.eye - HEAD_BELOW
            self._direct_hit(shooter, target, direct, travelled + t, critical, shot_frame,
                             world_tick=self.tick if npc or bounce else None)
            if not direct.biotic_rifle:
                self._hit_effect(target, direct.effect)
            return

    def _soldier_visor_direction(self, shooter, volley, origin, direction, view_tick):
        """Reconstructed Soldier rifle acquisition using active 0259 search data.

        The authored graph gates this search after the activation animation and
        removes it at expiry/death. Radius and cone come from that node. The
        host supplies the view basis, target poses and capsule aim samples.
        """
        if volley.instance.graph.index != 0x0254:
            return direction
        script = shooter.body_script
        if script is None or script.component.owner_dead:
            return direction
        searches = [
            state for instance in script.component.instances.values()
            if instance.graph.index == 0x0259
            for state in instance.states.values()
            if state.active and isinstance(state, nodes.TrackTargets)
            and (state.node.field("m_8A2905A0") or {}).get("m_identifier") == 2337
        ]
        candidates = (
            candidate for search in searches
            for candidate in self._visor_candidates(search, shooter, origin, direction, view_tick)
        )
        best = min(candidates, key=lambda candidate: candidate[0], default=None)
        return direction if best is None else best[1]

    def visor_targets(self, search):
        """Publish the sandbox ranking from current poses for the lock list."""
        component = search.instance.component
        shooter = next(
            (player for player in self.match.players
             if player.has_body and player.spawned and player.body == component.entity),
            None,
        )
        if shooter is None or component.owner_dead:
            return ()
        origin = self._eye(shooter)
        direction = aim(shooter.mover.yaw, shooter.mover.pitch)
        ranked = sorted(self._visor_candidates(search, shooter, origin, direction))
        return tuple(expr.Entity(entity) for (_, entity), _ in ranked)

    def _visor_candidates(self, search, shooter, origin, direction, view_tick=None):
        radius = _number(search.evaluate(search.node.field("m_radius"), watch=False), 0.0)
        cone = _number(search.evaluate(search.node.field("m_A30B383E"), watch=False), 0.0)
        if radius <= 0.0 or not 0.0 < cone <= 180.0:
            return
        threshold = math.cos(math.radians(cone))
        horizontal = math.hypot(direction[0], direction[2])
        right = ((direction[2] / horizontal, 0.0, -direction[0] / horizontal)
                 if horizontal > EPSILON else (1.0, 0.0, 0.0))
        up = (direction[1] * right[2], horizontal, -direction[1] * right[0])
        for target in self._enemies(shooter):
            shown = target if view_tick is None else self.seen(target, view_tick)
            if shown is None:
                continue
            centre = (shown.feet[0], shown.feet[1] + shown.height / 2, shown.feet[2])
            offset = _sub(centre, origin)
            distance = math.sqrt(_dot(offset, offset))
            if distance <= EPSILON or distance > radius:
                continue
            candidate = tuple(value / distance for value in offset)
            alignment = _dot(direction, candidate)
            if alignment < threshold:
                continue
            a, b = shown.segment()
            # Verify the same ray the rifle will fire. Prefer centre mass,
            # then the upper and lower ends of the capsule's axis.
            for point in (centre, b, a):
                offset = _sub(point, origin)
                length = math.sqrt(_dot(offset, offset))
                if length <= EPSILON or length > radius:
                    continue
                candidate = tuple(value / length for value in offset)
                if _dot(direction, candidate) < threshold:
                    continue
                contact = ray_capsule(origin, candidate, a, b, shown.radius)
                if contact is None:
                    continue
                wall = ray_world(self._collision(), origin, candidate, contact)
                if wall is None or wall >= contact:
                    score = self._visor_score(shown, origin, direction, right, up)
                    yield (score, target.entity), candidate
                    break

    @staticmethod
    def _visor_score(target, origin, direction, right, up):
        """0259's two score terms using the host view basis and mover extents."""
        feet = _sub(target.feet, origin)
        if target.radius > 0 and target.height > 0:
            lower = _along(feet, right, -target.radius)
            upper = _along((feet[0], feet[1] + target.height, feet[2]), right, target.radius)
        else:
            lower = upper = feet
        lower_depth, upper_depth = _dot(lower, direction), _dot(upper, direction)
        if min(lower_depth, upper_depth) <= EPSILON:
            screen = 100.0
        else:
            # At 90 degrees the projected offsets from the cursor are x/(2z) and y/(2z).
            gaps = []
            for axis in (right, up):
                lo = _dot(lower, axis) / (2 * lower_depth)
                hi = _dot(upper, axis) / (2 * upper_depth)
                gaps.append(max(0.0, min(lo, hi), -max(lo, hi)))
            screen = min(1.0, math.sqrt(2) * math.hypot(*gaps))
        return screen + .01 * min(1.0, math.sqrt(_dot(feet, feet)) / 15)

    def _barrier_contact(self, shooter, origin, direction, reach, *, view_tick=None):
        ray = shooter, origin, direction, reach
        candidates = (
            (self.barriers.trace(*ray, view_tick=view_tick), self.barriers),
            (self.zarya_barriers.trace(*ray, view_tick=view_tick), self.zarya_barriers),
            (self.winston_barriers.trace(*ray), self.winston_barriers),
            (self.orisa_barriers.trace(*ray), self.orisa_barriers),
        )
        contacts = ((*hit, service) for hit, service in candidates if hit is not None)
        return min(contacts, key=lambda hit: hit[0], default=None)

    def _barrier_damage(self, contact, amount, shooter):
        _, owner, service = contact
        if service is self.zarya_barriers:
            service.damage(owner, amount, _source_entity(shooter))
        else:
            service.damage(owner, amount)

    def _splash_barriers(self, shooter, point, origin, blast, impacts):
        """Keep the cover set for the entire blast, including a broken impact shield."""
        mesh_hit, bubble_hit, dome_hit, orisa_hit = impacts

        def with_impact(owners, impact):
            owners = tuple(owners)
            return (*owners, impact) if impact is not None and all(o is not impact for o in owners) else owners

        mesh = with_impact(self.barriers.enemies(shooter), mesh_hit)
        bubbles = {owner: self.zarya_barriers.centre(owner)
                   for owner in with_impact(self.zarya_barriers.enemies(shooter), bubble_hit)}
        domes = with_impact(self.winston_barriers.enemies(shooter), dome_hit)
        orisa = with_impact(self.orisa_barriers.enemies(shooter), orisa_hit)

        def bubble_ray(owner, origin, direction, reach):
            return self.zarya_barriers.intersect(origin, direction, bubbles[owner])

        def dome_ray(actor, origin, direction, reach):
            return actor.ray(origin, direction)

        layers = (
            (self.barriers, mesh, self.barriers.intersect),
            (self.zarya_barriers, bubbles, bubble_ray),
            (self.winston_barriers, domes, dome_ray),
            (self.orisa_barriers, orisa, self.orisa_barriers.intersect),
        )

        def blocked(service, recipient, witness):
            offset = _sub(witness, origin)
            reach = math.sqrt(_dot(offset, offset))
            if reach <= EPSILON:
                return False
            direction = tuple(value / reach for value in offset)
            return any((layer is not service or owner is not recipient)
                       and (distance := intersect(owner, origin, direction, reach)) is not None
                       and distance < reach - EPSILON
                       for layer, owners, intersect in layers for owner in owners)

        self.barriers.splash(point, origin, blast, mesh, mesh_hit, blocked)
        self.zarya_barriers.splash(shooter, point, origin, blast, bubbles, bubble_hit, blocked)
        self.winston_barriers.splash(point, origin, blast, domes, dome_hit, blocked)
        self.orisa_barriers.splash(point, origin, blast, orisa, orisa_hit, blocked)
        return mesh, {self.zarya_barriers.body(owner): centre for owner, centre in bubbles.items()}, domes, orisa

    def _fly(self, rocket: Rocket) -> None:
        """Sweep elapsed world time against the current bodies and static collision."""
        cursor = rocket.flight_cursor()
        elapsed = 1 if cursor is None else self.tick - cursor
        if elapsed <= 0:
            return
        rocket.last_tick = self.tick
        remaining = elapsed * TICK
        # Reflection consumes this very step's remainder. It does not add a
        # hidden tick of lifetime or reset range when ownership transfers.
        impact_barrier = None
        impact_bubble = None
        impact_dome = None
        impact_orisa = None
        biotic_rifle = rocket.direct is not None and rocket.direct.biotic_rifle
        max_segments = max(deflection.MAX_SEGMENTS, (rocket.rebound_limit or 0) + 1)
        for _ in range(max_segments):
            duration = min(remaining, rocket.left)
            range_exhausted = False
            if rocket.gravity is not None and duration > 0.0:
                vx, vy, vz = (axis * rocket.speed for axis in rocket.direction)
                rocket.set_velocity(vx, vy - rocket.gravity * remaining, vz)
                if rocket.speed == 0:
                    rocket.travel(0.0, seconds=duration)
                    if rocket.left <= 0.0:
                        self._landed(rocket)
                    return
                if rocket.range_limit is not None:
                    range_time = max(0.0, rocket.range_limit - rocket.distance) / rocket.speed
                    range_exhausted = range_time <= duration
                    duration = min(duration, range_time)
            if duration <= 0.0:
                self._landed(rocket)
                return
            step = rocket.speed * duration
            best = None
            for target in self._shot_targets(rocket.shooter, biotic_rifle):
                a, b = target.segment()
                t = ray_capsule(rocket.position, rocket.direction, a, b, target.radius)
                if t is not None and t <= step and (best is None or t < best[0]):
                    best = (t, target)
            collision = self._collision()
            surface = collision.cast_ray(rocket.position, rocket.direction, step, MOVER) if collision else None
            wall = surface.distance if surface is not None else None
            if wall is not None and (best is None or wall < best[0]):
                best = (wall, None)
            hit = rocket.direct if rocket.direct is not None and rocket.direct.deflectable else rocket.blast
            reflected = deflection.trace(
                self, rocket.shooter, hit, rocket.position, rocket.direction, step,
                definition=rocket.definition,
            )
            absorbed = self._absorption(
                rocket.shooter, rocket.definition, rocket.position, rocket.direction, step,
            )
            barrier = self._barrier_contact(rocket.shooter, rocket.position, rocket.direction, step)
            if (barrier is not None and (best is None or barrier[0] < best[0])
                    and (reflected is None or barrier[0] < reflected[0])
                    and (absorbed is None or barrier[0] < absorbed[0])):
                t, impact_owner, service = barrier
                impact_barrier = impact_owner if service is self.barriers else None
                impact_bubble = impact_owner if service is self.zarya_barriers else None
                impact_dome = impact_owner if service is self.winston_barriers else None
                impact_orisa = impact_owner if service is self.orisa_barriers else None
                rocket.travel(t)
                self._landed(rocket)
                point = _along(rocket.position, rocket.direction, t)
                struck = None
                amount = 0.0 if rocket.direct is None else rocket.direct.amount * rocket.direct.scale(0.0)
                if rocket.blast is not None:
                    amount += rocket.blast.amount
                self._barrier_damage(barrier, amount, rocket.shooter)
                break
            if (absorbed is not None and (best is None or absorbed[0] < best[0])
                    and (reflected is None or absorbed[0] < reflected[0])):
                t, _, state = absorbed
                rocket.travel(t)
                rocket.position = _along(rocket.position, rocket.direction, t)
                if state is not None:
                    self.grasp.absorb(state, rocket.shooter, rocket.direct, rocket.blast,
                                      rocket.flown * rocket.speed)
                if rocket.shot is not None:
                    self.projectiles.intercepted(rocket.shot)
                    rocket.shot = None
                self._landed(rocket)
                return
            if (reflected is not None and (best is None or reflected[0] < best[0])
                    and (wall is None or reflected[0] < wall)):
                t, target = reflected
                point = _along(rocket.position, rocket.direction, t)
                consumed = rocket.travel(t)
                duration -= consumed
                if rocket.shot is not None:
                    self.projectiles.intercepted(rocket.shot)
                    rocket.shot = None  # original prediction retires; reflected visuals remain open
                rocket.shooter, rocket.team = target.owner, target.team
                rocket.source_entity = target.entity
                rocket.direction = aim(target.owner.mover.yaw, target.owner.mover.pitch)
                shift_time = min(deflection.OFFSET / rocket.speed, duration, rocket.left)
                shift = shift_time * rocket.speed
                rocket.position = _along(point, rocket.direction, shift)
                rocket.travel(shift, seconds=shift_time)
                duration -= shift_time
                if rocket.left <= 0.0:
                    self._landed(rocket)
                    return
                if duration <= 0.0:
                    return
                remaining = duration
                continue
            if best is None:
                rocket.position = _along(rocket.position, rocket.direction, step)
                rocket.travel(step, seconds=duration)
                if rocket.left <= 0.0 or range_exhausted:
                    self._landed(rocket)
                return
            t, struck = best
            consumed = rocket.travel(t)
            point = _along(rocket.position, rocket.direction, t)
            ground_point = None
            if struck is None and surface is not None and rocket.rebound_limit is not None:
                rocket.position = _along(rocket.position, rocket.direction, max(0.0, t - 0.005))
                if surface.normal[1] <= 0:
                    rocket.rebounds += 1
                    if rocket.rebounds <= rocket.rebound_limit:
                        # Interpolate the incoming velocity at contact, then sweep the
                        # remaining time from the rebound instead of adding a tick.
                        incoming = (vx, vy - rocket.gravity * consumed, vz)
                        normal_speed = _dot(incoming, surface.normal)
                        if normal_speed < 0:
                            incoming = _along(incoming, surface.normal, -(1 + rocket.rebound_scale) * normal_speed)
                            rocket.position = _along(rocket.position, surface.normal, 0.001)
                        rocket.set_velocity(*incoming)
                        remaining -= consumed
                        if remaining <= 0.0:
                            return
                        continue
                else:
                    ground_point = surface.point
            self._landed(rocket, ground_point=ground_point)
            break
        else:
            self._landed(rocket)  # bounded unresolved zero-distance/multi-redirect policy
            return
        blast = rocket.blast
        grenade = blast is not None and blast.effect == ANA_GRENADE_EFFECT
        if not grenade and struck is not None and rocket.direct is not None and rocket.direct.amount > 0.0:
            self._direct_hit(
                rocket.shooter, struck, rocket.direct, 0.0, False, self.tick,
                world_tick=self.tick,
            )
            if not rocket.direct.biotic_rifle:
                self._hit_effect(struck, rocket.direct.effect)
        if blast is None or blast.amount <= 0.0:
            return
        # A wall impact lies on the collision surface. Start just before it so the
        # same face blocks targets behind it without hiding targets in front.
        blast_origin = (
            _along(rocket.position, rocket.direction, max(0.0, t - BLAST_SURFACE_OFFSET))
            if struck is None else point
        )
        cover, bubbles, domes, orisa_cover = self._splash_barriers(
            rocket.shooter, point, blast_origin, blast, (impact_barrier, impact_bubble, impact_dome, impact_orisa),
        )
        candidates = self._targets() if grenade else self._enemies(rocket.shooter)
        # Do not assign self-damage semantics to every explosion. Soldier's
        # authored Helix payload supplies .5; native field dispatch is unproven.
        if (blast.loadout == SOLDIER_WEAPON_LOADOUT and blast.self_scale > 0.0
                and rocket.source_entity == _source_entity(rocket.shooter)):
            candidates.extend(t for t in self._targets() if t.entity == rocket.source_entity)
        for target in candidates:
            if self.vitals_of(target).dead:
                continue
            a, b = target.segment()
            if self.zarya_barriers.covers(bubbles, blast_origin, a, b, target.entity):
                continue
            if self.winston_barriers.covers(domes, blast_origin, a, b):
                continue
            if self.orisa_barriers.covers(orisa_cover, blast_origin, a, b):
                continue
            distance = max(0.0, segment_distance(point, a, b) - target.radius)
            scalar = blast.splash(distance)
            own = target.entity == rocket.source_entity
            if own:
                scalar *= blast.self_scale
            impulse = (blast.impulse_scale(distance, own=own)
                       if blast.loadout == SOLDIER_WEAPON_LOADOUT else 0.0)
            if ((scalar > 0.0 or impulse > 0.0)
                    and capsule_visible(self._collision(), blast_origin, a, b, target.radius)
                    and not self.barriers.covers(blast_origin, a, b, target.radius, cover)):
                if grenade:
                    self._ana_grenade(rocket.shooter, target, blast, scalar)
                    continue
                self._damage(
                    rocket.shooter, target, blast.amount * scalar, False, blast.loadout, self.tick,
                    world_tick=self.tick,
                )
                self._hit_effect(target, blast.effect)
                if impulse > 0 and not self.vitals_of(target).dead:
                    self._blast_impulse(target, point, blast, impulse)

    def _blast_impulse(self, target: Target, point, blast: HitDef, scalar: float) -> None:
        """Scoped radial/upward Helix force; native resistance/order remain open."""
        self._radial_impulse(target, point, blast.horizontal_impulse * scalar, blast.vertical_impulse * scalar)

    def _radial_impulse(self, target: Target, point, horizontal: float, vertical: float) -> None:
        if defenses.fortify_scale(target.owner) is not None:
            return
        from ow174.game.collision import MOVER, TEAM0, TEAM1
        from ow174.game.mover import FELL

        dx, dz = target.feet[0] - point[0], target.feet[2] - point[2]
        length = math.hypot(dx, dz)
        scale = horizontal / length if length > EPSILON else 0.0
        response = defenses.knockback_scale(target.owner)
        impulse = tuple(value * response
                        for value in (dx * scale, vertical, dz * scale))
        if not all(math.isfinite(value) for value in impulse):
            raise ValueError("invalid authored blast impulse")
        if not any(impulse):
            return
        if isinstance(target.owner, bots.Bot):
            category = MOVER
            if not self.match.game_map.free_for_all and target.team in (0, 1):
                category = (TEAM0, TEAM1)[target.team]
            target.owner.apply_impulse(impulse, self.tick, self._collision(), category=category)
        else:
            mover = target.owner.mover
            mover.velocity = tuple(a + b for a, b in zip(mover.velocity, impulse, strict=True))
            if mover.velocity[1] > 0:
                mover.flags_word |= FELL
                mover.fall = 0.0

    def _blast_visible(self, origin, target: Target) -> bool:
        """Reconstructed splash LOS to the nearest point on the target capsule's axis.

        This shares the current combat collision category. Retail's splash probe
        points and collision mask still require target-build verification.
        """
        a, b = target.segment()
        axis = _sub(b, a)
        length = _dot(axis, axis)
        share = 0.0 if length <= EPSILON else max(0.0, min(1.0, _dot(_sub(origin, a), axis) / length))
        offset = _sub(_along(a, axis, share), origin)
        distance = math.sqrt(_dot(offset, offset))
        if distance <= EPSILON:
            return True
        direction = tuple(value / distance for value in offset)
        wall = ray_world(self._collision(), origin, direction, distance)
        return wall is None or wall >= distance

    def _landed(self, rocket: Rocket, *, ground_point=None) -> None:
        """The projectile ended (hit, range, lifetime): the predicted one's release follows."""
        if rocket in self.rockets:
            self.rockets.remove(rocket)
        if rocket.shot is not None:
            if rocket.shot.spawn == projectiles.FIELD_ENTITY:
                self.projectiles.finish_field_flight(rocket.shot, self.tick, ground_point)
            else:
                self.projectiles.flown(rocket.shot, rocket.flown)

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
            if not capsule_visible(self._collision(), centre, a, b, target.radius):
                continue
            self._heal_target(healer, target, vitals, amount, ticks=ticks)

    def _healing_factor(self, entity: int, ticks=None) -> float:
        modifier = self.heal_modifiers.get(entity)
        return 1.0 if modifier is None else modifier.factor(*(ticks or (self.tick, self.tick)))

    def _heal_target(self, healer, target, vitals, amount, *, ticks=None, source=None):
        eligible = vitals.ult_healing_deficit
        given = vitals.heal(amount * self._healing_factor(target.entity, ticks))
        if given <= 0.0:
            return
        if not isinstance(healer, bots.Bot):
            healer.stats.add(stats.HEALING, given)
            if source is None or source == _source_entity(healer):
                self._gain_ult_charge(healer, min(given, eligible), kind="healing", recipient=target.entity)
        self._sync_body_health(target.owner, vitals)

    def _advance_biotic_doses(self) -> None:
        if not self.biotic_doses:
            return
        targets = {target.entity: target for target in self._targets()}
        pending = []
        for dose in self.biotic_doses:
            target = targets.get(dose.target)
            if target is None:
                continue
            vitals = self.vitals_of(target)
            if vitals.dead:
                continue
            elapsed = (self.tick - dose.start) * TICK
            complete = elapsed >= ANA_RIFLE_DURATION
            share = min(1.0, elapsed / ANA_RIFLE_DURATION)
            cumulative = dose.amount if complete else round(dose.amount * share * 1024) / 1024
            amount = cumulative - dose.applied
            if amount > 0:
                dose.applied = cumulative
                if dose.healing:
                    self._heal_target(dose.shooter, target, vitals, amount, source=dose.source)
                else:
                    self._damage(dose.shooter, target, amount, False, dose.loadout, dose.frame,
                                 world_tick=self.tick, source=dose.source, reduce_armour=False)
            if not complete and not vitals.dead:
                pending.append(dose)
        self.biotic_doses = pending

    def _ana_grenade(self, shooter, target, blast, scalar):
        vitals = self.vitals_of(target)
        friendly = target.owner is shooter or (
            not self.match.game_map.free_for_all and target.team == shooter.team
        )
        deadline = self.tick + math.ceil(ANA_GRENADE_DURATION / TICK)
        if friendly:
            modifier = self.heal_modifiers.setdefault(target.entity, HealingModifier())
            modifier.boost_until = 0
            self._heal_target(shooter, target, vitals, ANA_GRENADE_HEAL * scalar)
            modifier.boost_from = self.tick + math.ceil(ANA_GRENADE_BOOST_DELAY / TICK)
            modifier.boost_until = deadline
        elif not defenses.invulnerable(target.owner):
            self._damage(shooter, target, ANA_GRENADE_DAMAGE * scalar, False, blast.loadout,
                         self.tick, world_tick=self.tick)
            if not vitals.dead:
                modifier = self.heal_modifiers.setdefault(target.entity, HealingModifier())
                if modifier.anti_until <= self.tick:
                    modifier.anti_from = self.tick
                modifier.anti_until = deadline

    @staticmethod
    def _gain_ult_charge(
        owner, points: float, *, kind: str = "damage", target_definition=None, recipient=None,
    ) -> None:
        if isinstance(owner, bots.Bot):
            return
        script = getattr(owner, "body_script", None)
        gain = getattr(script, "gain_ult_charge", None)
        if callable(gain):
            gain(points, kind=kind, target_definition=target_definition, recipient=recipient)

    @staticmethod
    def _sync_body_health(owner, vitals: Vitals) -> None:
        script = getattr(owner, "body_script", None)
        if script is not None:
            script.set_health(vitals.total, sum(vitals.most.values()))

    def _collision(self):
        from ow174.game.collision import CollisionError

        world = self.match.collision_world
        if world is None:
            raise CollisionError("match collision is not ready")
        return world

    # --- damage and death --------------------------------------------------------------------------

    def _direct_hit(
        self, shooter, target: Target, hit: HitDef, distance: float, critical: bool, frame: int,
        *, world_tick: int | None = None,
    ) -> None:
        critical = critical and hit.critical > 1.0 and defenses.fortify_scale(target.owner) is None
        amount = hit.amount * hit.scale(distance) * (hit.critical if critical else 1.0)
        if hit.biotic_rifle:
            friendly = not self.match.game_map.free_for_all and target.team == shooter.team
            if friendly or not defenses.invulnerable(target.owner):
                self.biotic_doses.append(BioticDose(
                    shooter, _source_entity(shooter), target.entity, friendly, self.tick,
                    amount, hit.loadout, frame,
                ))
            return
        self._damage(shooter, target, amount, critical, hit.loadout, frame, world_tick=world_tick)

    def _damage(
        self, shooter, target: Target, amount: float, critical: bool, loadout, frame: int,
        *, world_tick: int | None = None, source: int | None = None, reduce_armour: bool = True,
    ) -> None:
        vitals = self.vitals_of(target)
        if vitals.dead or amount <= 0.0 or defenses.invulnerable(target.owner):
            return
        reduction = defenses.fortify_scale(target.owner)
        if reduction is not None:
            amount *= reduction
        base_before = vitals.base_total
        dealt = vitals.damage(amount, reduce_armour=reduce_armour)
        if dealt <= 0.0:
            return
        sleep = self.sleeps.get(target.entity)
        if (sleep is not None and not sleep.waking and self.tick < sleep.end
                and (self.tick - sleep.start) * TICK > SLEEP_DAMAGE_GRACE):
            sleep.end = min(sleep.end, self.tick + math.ceil(SLEEP_WAKE_DURATION / TICK))
            sleep.waking = True
        hostile = target.owner is not shooter and (
            self.match.game_map.free_for_all or target.team != shooter.team
        )
        source = _source_entity(shooter) if source is None else source
        if hostile:
            vitals.ult_healing_deficit = min(
                vitals.base_missing, vitals.ult_healing_deficit + base_before - vitals.base_total,
            )
            victim = target.owner
            definition = victim.body if isinstance(victim, bots.Bot) else victim.hero.body
            if source == _source_entity(shooter):
                self._gain_ult_charge(shooter, dealt, target_definition=definition, recipient=target.entity)
        killed = vitals.now[HEALTH_POOL] <= EPSILON
        if killed:
            vitals.now[HEALTH_POOL] = 0.0
            vitals.died_at = self.now
            vitals.death_tick = self.match.tick if world_tick is None else world_tick
            vitals.killer = source
        if hostile and not isinstance(shooter, bots.Bot):
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
        self.stuns.pop(bot.entity, None)
        self.sleeps.pop(bot.entity, None)
        self.heal_modifiers.pop(bot.entity, None)
        self.release_pins(bot.entity)
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

    def _died(self, killer, victim, critical: bool, loadout, source: int | None = None) -> None:
        """A player died: the notices, his body script's "died" (the kill feed line), and the respawn."""
        self.stuns.pop(victim.body, None)
        self.sleeps.pop(victim.body, None)
        self.heal_modifiers.pop(victim.body, None)
        self.release_pins(victim.body)
        label = self.match.label()
        log.info("[game] %s: %s killed %s", label, killer.name, victim.name)
        victim.stats.add(stats.DEATHS)
        bot_killer = isinstance(killer, bots.Bot)
        source = _source_entity(killer) if source is None else source
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
                visor_phase = next(
                    (state for instance in component.instances.values() if instance.graph.index == 0x0259
                     for number in (6, 43)  # countdown or startup
                     if (state := instance.states.get(number)) is not None and state.active),
                    None,
                )
                if visor_phase is not None:
                    # End Visor even if the victim already ran its command this frame.
                    visor_phase.request(runtime.ABORT)
                if runtime.send_message(component, DIED, params, source) or visor_phase is not None:
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
