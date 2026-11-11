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
    ashe,
    bots,
    contacts,
    earthshatter,
    immunity,
    mei_blizzard,
    mei_frost,
    observers,
    orisa_barrier,
    pools,
    projectiles,
    reinhardt_barrier,
    sombra_hack,
    stats,
    voicelines,
    world,
    world_bubbles,
)
from ow174.game.bits import BitWriter
from ow174.game.collision import MOVER, World
from ow174.game.commands import Command
from ow174.game.mover import mover_data
from ow174.game.script import expr, graph, nodes, runtime, sleep_view
from ow174.game.script.bot import DIED, BotScript
from ow174.game.script.driver import ULT_CHARGE, ULT_COST, Snapshot
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
# Explicit104319 FirstPerson fields; camera/muzzle animation remains separate.
EYE_STANDING = {0x040000000000251C: 1.445, 0x04000000000002A3: 1.425}
EYE_CROUCHED = {0x04000000000003CF: 1.085, 0x040000000000251C: 1.0115,
                0x04000000000002A3: 0.9975}
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
# Reconstructed hero policy, NOT a native default for all omitted critical fields.
# Both Viper modes omit m_A872C70E; their critical hits use the OW1 2x rule.
CRITICAL_DEFAULTS = {0x14FC: 2.0}
ASHE = 0x02E0000000000200
TICK = 0.016
EPSILON = 1e-9
FLASHBANG_EFFECT = 0x04A0000000000079  # .053/0079 ->0228 ->224E
SLEEP_DART_EFFECT = 0x04A000000000026F  # 0BDD/14 -> 0BDE/51 -> 224E variant 676B
SLEEP_SECONDS = 5.0
SLEEP_DAMAGE_GRACE = 0.5
SLEEP_WAKE_SECONDS = 0.5
SLEEP_NO_WAKE_TAG = 0x0D8000000000191F  # 224E health-message wake exclusion
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


def fixed_pellet_directions(state, direction) -> tuple:
    """Reconstruct the authored fixed-ring volley (613BDDB0), including its centre pellet.

    Ring populations/radii and spread come from build 104319. The interpretation of spread as
    the full cone angle, equal azimuth spacing and ring phase zero are reconstructed, not a
    native random-seed/pattern match. Only Coach Gun1513/state16 and Enforcer13EB/node24
    are enabled; their different pattern modes are not claimed native-equivalent.
    Keep separate rays so armour, occlusion and death are resolved per pellet.
    """
    cfg = state.node.field("m_5EC1EB38") or {}
    supported = ((state.instance.graph.index == 0x1513 and state.index == 16)
                 or (state.instance.graph.index == 0x13EB and state.node.pos == 24))
    if cfg.get("$") != "STU_613BDDB0" or not supported:
        return (direction,)

    def value(cfg, default=0.0):
        return _number(state.evaluate(cfg, watch=False), default) if cfg is not None else default

    count = int(value(state.node.field("m_numProjectilesPerShot"), 1))
    if not 0 <= count <= 256:
        raise ValueError("fixed volley pellet count outside supported bounds")
    spread = value(cfg.get("m_spread"))
    if not math.isfinite(spread) or not 0 <= spread < 180:
        raise ValueError("invalid fixed volley cone")
    rings = []
    for ring in cfg.get("m_0D30E40C") or []:
        radius = value(ring.get("m_6425FFB9"))
        population = int(value(ring.get("m_B19BD35D")))
        if not math.isfinite(radius) or not 0 <= radius <= 1 or not 0 <= population <= 256:
            raise ValueError("invalid fixed volley ring")
        rings.append((radius, population))
    if sum(population for _, population in rings) != count:
        raise ValueError("fixed volley ring population does not match projectile count")
    # A stable orthonormal basis, including a vertical aim.
    horizontal = math.hypot(direction[0], direction[2])
    right = ((direction[2] / horizontal, 0.0, -direction[0] / horizontal)
             if horizontal > EPSILON else (1, 0, 0))
    up = (
        direction[1] * right[2] - direction[2] * right[1],
        direction[2] * right[0] - direction[0] * right[2],
        direction[0] * right[1] - direction[1] * right[0],
    )
    cone = math.tan(math.radians(spread / 2))
    found = []
    for radius, population in rings:
        for index in range(population):
            angle = index * 2 * math.pi / population
            x, y = cone * radius * math.cos(angle), cone * radius * math.sin(angle)
            ray = tuple(direction[k] + x * right[k] + y * up[k] for k in range(3))
            length = math.sqrt(_dot(ray, ray))
            found.append(tuple(component / length for component in ray))
    return tuple(found)


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


def capsule_visible(collision: World | None, origin, a, b, radius: float, *, ice_walls=None) -> bool:
    """Reconstructed visibility to the nearest capsule surface."""
    ab, ap = _sub(b, a), _sub(origin, a)
    length = _dot(ab, ab)
    share = 0.0 if length <= EPSILON else max(0.0, min(1.0, _dot(ap, ab) / length))
    toward = _sub(_along(a, ab, share), origin)
    distance = math.sqrt(_dot(toward, toward))
    if distance <= radius + EPSILON:
        return True
    reach = distance - radius
    hit = ray_world(collision, origin, tuple(axis / distance for axis in toward), reach, ice_walls=ice_walls)
    return hit is None or hit + EPSILON >= reach


def ray_world(
    collision: World | None, origin, direction, length: float, category: int = MOVER, *, ice_walls=None,
) -> float | None:
    """The first terrain hit or live pillar hit when Ice Wall participates."""
    hit = collision.cast_ray(origin, direction, length, category) if collision is not None else None
    distance = hit.distance if hit is not None else None
    if ice_walls is not None:
        pillar = ice_walls.cast(origin, direction, length if distance is None else distance)
        if pillar is not None:
            return pillar[0]
    return distance


# --- health -------------------------------------------------------------------------------------------


PERSONAL_BARRIER_RADIUS = 1.5  # model 10D7 sphere
GRASP_SHIELDS = 3  # internal key for the separate temporary shield pool


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

    @property
    def dead(self) -> bool:
        return self.died_at is not None

    @property
    def total(self) -> float:
        return sum(self.now.values())

    def damage(self, amount: float) -> float:
        """Take a hit from shields, then armour, then health (unverified, as OW1 did: what reaches armour
        while it has some is ARMOUR_BLOCK less, at most halved). Returns what the pools lost."""
        left = max(0.0, amount)
        taken = 0.0
        for kind in (GRASP_SHIELDS, SHIELDS_POOL, ARMOUR_POOL, HEALTH_POOL):
            if left <= 0.0:
                break
            if kind == ARMOUR_POOL and self.now[kind] > 0.0:
                left = max(left - ARMOUR_BLOCK, left / 2)
            take = min(left, self.now.get(kind, 0.0))
            if take > 0.0:
                self.now[kind] -= take
                self.changed.add(kind)
                left -= take
                taken += take
                if kind == GRASP_SHIELDS and self.now[kind] <= EPSILON:
                    self.now[kind] = self.most[kind] = 0.0
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
            if kind == GRASP_SHIELDS:
                parts[SHIELDS_POOL * POOLS + 1] = world.pool(
                    self.ids[kind], SHIELDS_POOL, self.most[kind], self.now[kind], temporary=True,
                )
            elif kind in self.ids:
                parts[kind * POOLS] = world.pool(self.ids[kind], kind, self.most[kind], self.now[kind])
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
    context = expr.Context() if state is None else None

    def evaluate(config):
        if state is not None:
            return state.evaluate(config, watch=False)
        # Static hosts without the child VM can resolve its unkeyed literal identifiers.
        if (isinstance(config, dict) and config.get("$") == "STUConfigVarIdentifier"
                and config.get("m_resourceKey") is None):
            return expr.evaluate(config, context)
        return None

    tags = (evaluate(row.get("m_EE3055BD"))
            for row in (modify.get("m_148F3152") or ()) if isinstance(row, dict))
    return tuple(tag.guid for tag in tags if isinstance(tag, expr.Asset))


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
        critical=value("m_A872C70E", CRITICAL_DEFAULTS.get(state.instance.graph.index, 1.0)),
        rings=tuple(sorted(rings)),
        effect=effect.guid if isinstance(effect, expr.Asset) else None,
        tags=health_tags(state, modify),
    )


# --- who can be hit -------------------------------------------------------------------------------------


def _source_entity(actor) -> int:
    """The combat actor's live entity ID."""
    return actor.entity if isinstance(actor, bots.Bot) else actor.body


def _controller(actor):
    """The owning player for a summoned combat actor, otherwise the actor itself."""
    return getattr(actor, "combat_owner", None) or actor


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
    reflections: int = 0
    target_limit: int = 1
    hit_entities: set[int] = field(default_factory=set)
    strike_radius: float | None = None
    strike_barriers: set[tuple[int, int]] = field(default_factory=set)


class Combat:
    """The match's health, hits, deaths and respawns."""

    def __init__(self, match) -> None:
        self.match = match
        self.vitals: dict[int, Vitals] = {}  # body or bot entity -> its health
        self.history: dict[int, deque] = {}  # entity -> (tick, feet, height, eye) of the last HISTORY ticks
        self.replacement_ticks: dict[int, int] = {}  # replacement body -> authoritative world tick
        self.rockets: list[Rocket] = []
        self.earthshatters: dict[int, earthshatter.Wave] = {}
        self.personal_barriers = {}
        from ow174.game import sigma_barrier

        self.sigma_barriers = sigma_barrier.Barriers(self)
        self.world_barriers = world_bubbles.WorldBubbles(self)
        self.orisa_barriers = orisa_barrier.Barriers(self)
        self.reinhardt_barriers = reinhardt_barrier.Barriers(self)
        from ow174.game import mei_wall

        self.ice_walls = mei_wall.Walls(self)
        self.dynamite = ashe.Dynamite(self)
        self.companions = ashe.Companions(self)
        self.impulses = ashe.Impulses(self)
        self.respawns: list[tuple[float, object]] = []  # (when, a player or a bot)
        self.next_bot = bots.BOT_ENTITY + len(match.bots)
        self.now = 0.0
        self.tick = 0
        self.melee_seen: dict[int, dict[tuple[int, int], int]] = {}  # body -> counters by instance/state
        self.stuns: dict[int, int] = {}  # body identity -> exclusive world-tick deadline
        self.sleeps: dict[int, tuple[int, int]] = {}  # body identity -> impact tick, exclusive deadline
        self.sleep_views: dict[int, tuple[float, ...] | None] = {}
        self.frost_hits: dict[int, dict[tuple[int, int], tuple[float, int, bool]]] = {}
        self.blizzard = mei_blizzard.Blizzard(self)
        self.freezes: dict[int, tuple[int, int, int]] = {}
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
        self.sleeps.pop(player.body, None)
        self.sleep_views.pop(player.body, None)
        self._clear_frost(player.body)
        for body, (phase, _activation, _health) in list(self.personal_barriers.items()):
            if body == player.body or phase.instance.component.entity == player.body:
                self.personal_barriers.pop(body)
        self.world_barriers.retire(player.body)
        self.ice_walls.retire(player.body)
        self.sigma_barriers.retire(player.body)
        self.reinhardt_barriers.retire(player.body)
        self.dynamite.retire(player.body)
        self.blizzard.retire(player.body)
        self.companions.retire(player.body)
        self.impulses.retire(player.body)
        # The owner's leased projectiles and their observer copies retire with the old body.
        self.rockets[:] = [
            rocket for rocket in self.rockets if rocket.shooter is not player or rocket.shot is None
        ]
        self.respawns[:] = [(when, who) for when, who in self.respawns if who is not player]

    def record_replacement(self, body: int) -> None:
        """Keep the creation boundary independent of the rolling pose history."""
        self.replacement_ticks[body] = self.match.tick
        self.stuns.pop(body, None)
        self.sleeps.pop(body, None)
        self.sleep_views.pop(body, None)
        self._clear_frost(body)

    def _clear_frost(self, body):
        self.frost_hits.pop(body, None)
        self.freezes.pop(body, None)

    def freeze_angles(self, body, tick):
        sample = self.freezes.get(body)
        return sample[1:] if sample is not None and tick < sample[0] else None

    def frost_movement_mods(self, body, active, tick):
        hits = self.frost_hits.get(body, {})
        progress = mei_frost.combined_progress(hits, tick)
        if progress:
            active[(-1, 0, 0)] = (0, 1, max(0.3, 1.0 - progress), 99.0)
        return active

    def _frost_hit(self, shooter, target, tick, *, channel=0, initial=mei_frost.INITIAL, step=mei_frost.STEP):
        body, actor = target.entity, target.owner
        if mei_frost.immune(actor) or self.freezes.get(body, (0,))[0] > tick:
            return
        hits = self.frost_hits.setdefault(body, {})
        source = _source_entity(shooter), channel
        initial, step, freeze_ms, stacking = mei_frost.settings(
            shooter, initial=initial, step=step, channel=channel,
        )
        previous = hits.get(source, (0.0, tick, False))[:2]
        progress, touched = mei_frost.hit(previous, tick, initial=initial, step=step)
        hits[source] = progress, touched, stacking
        if mei_frost.combined_progress(hits, tick) >= 1.0:
            mover = getattr(actor, "mover", None)
            yaw = mover.yaw if mover is not None else round(actor.yaw)
            pitch = mover.pitch if mover is not None else round(actor.pitch)
            self.freezes[body] = mei_frost.thaw_tick(tick, freeze_ms), yaw, pitch
            self.frost_hits.pop(body)

    def _advance_frost(self, tick):
        if not self.frost_hits and not self.freezes:
            return
        live = {target.entity: target for target in self._targets()}
        for body in self.frost_hits.keys() | self.freezes.keys():
            target = live.get(body)
            if target is None or self.vitals_of(target).dead or mei_frost.immune(target.owner):
                self._clear_frost(body)
                continue
            if body in self.freezes and self.freezes[body][0] <= tick:
                del self.freezes[body]
            hits = self.frost_hits.get(body)
            if hits is not None:
                for source, (_, touched, _) in tuple(hits.items()):
                    if mei_frost.expired(touched, tick):
                        del hits[source]
                if not hits:
                    del self.frost_hits[body]

    def control_until(self, entity: int) -> int:
        return max(self.stuns.get(entity, 0), self.sleeps.get(entity, (0, 0))[1],
                   self.freezes.get(entity, (0,))[0])

    def is_stunned(self, actor, *, tick: int | None = None) -> bool:
        """Shared movement and input gate for stun, sleep and freeze."""
        return self.control_until(_source_entity(actor)) > (self.tick if tick is None else tick)

    def sleep_view_angles(self, body):
        if self.sleeps.get(body, (0, 0))[1] <= self.tick:
            return None
        return self.sleep_views.get(body)

    def _sample_sleep_views(self, tick):
        if not self.sleeps:
            return
        delay = sleep_view.delay_ticks(TICK)
        if delay is None:
            return
        for player in self.match.players:
            body = player.body
            started, deadline = self.sleeps.get(body, (0, 0))
            if started + delay <= tick < deadline and body not in self.sleep_views:
                mover = player.mover
                self.sleep_views[body] = sleep_view.sample(body, mover.yaw, mover.pitch)

    def _hit_effect(self, target: Target, effect: int | None) -> None:
        """Resource-backed stun reconstruction; native immunity/presentation remain unverified."""
        if (effect not in (FLASHBANG_EFFECT, SLEEP_DART_EFFECT) or self.vitals_of(target).dead
                or immunity.untargetable(target.owner, target.entity)):
            return
        if effect == SLEEP_DART_EFFECT:
            if immunity.damage_immune(target.owner, target.entity):
                return
            self.sleeps[target.entity] = (self.tick, self.tick + math.ceil(SLEEP_SECONDS / TICK))
            self.sleep_views.pop(target.entity, None)
        else:
            variables = graph.mode_variables(self.match.game_map.mode_guid) or {}
            duration = 1.7 if expr.truthy(variables.get(FLASHBANG_MODE_FLAG)) else 0.8
            self.apply_stun(target, duration)
            return
        self._interrupt_control(target)

    def apply_stun(self, target: Target, duration: float) -> None:
        deadline = self.tick + math.ceil(duration / TICK)
        self.stuns[target.entity] = max(deadline, self.stuns.get(target.entity, 0))
        self._interrupt_control(target)

    def _interrupt_control(self, target: Target) -> None:
        script = getattr(target.owner, "body_script", None)
        if script is None:
            return
        component = script.component
        component.edges([(value, False) for value in tuple(component.held)])
        ashe.set_viper_reload_stunned(component, True)
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
            self.ice_walls.cancel_preview(player)
            return
        if self.ice_walls.command(player, command):
            return
        if command is not None:
            for state, number, time, activation, *launch in shots:
                try:
                    options = {"viper_launch": launch[0]} if launch else {}
                    self._shoot(player, command, state, number, time, activation=activation, **options)
                except Exception:
                    log.exception("[game] %s: %s's shot failed", self.match.label(), player.name)
        earthshatter.start(self, player)
        try:
            self._melee(player, command, script)
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
            eye = self._eye(player)
            centre = _along(eye, aim(*command.aim()), reach)
            view_tick = command.frame - command.view_delay(player.quanta) * 1e6 / player.quanta
            for target in self._enemies(player):
                shown = self.seen(target, view_tick)
                if shown is None:
                    continue
                a, b = shown.segment()
                if (segment_distance(centre, a, b) - shown.radius <= radius and amount > 0.0
                        and capsule_visible(self._collision(), eye, a, b, shown.radius)
                        and not self.ice_walls.blocks(eye, _along(a, _sub(b, a), 0.5))):
                    self._damage(player, target, amount, False, loadout, command.frame,
                                 damage_tags=health_tags(state, modify))
            if self.ice_walls.pieces and amount > 0:
                self.ice_walls.melee(player, eye, centre, radius, amount)
            if self.orisa_barriers.actors and amount > 0:
                self.orisa_barriers.melee(player, eye, centre, radius, amount)
            if self.reinhardt_barriers.geometry is not None and amount > 0:
                self.reinhardt_barriers.melee(player, eye, centre, radius, amount)

    def update(self, now: float, tick: int, frames: bool = True) -> None:
        """Each tick: keep where the bodies are, fly the rockets, respawn, send health that changed. The
        other players' body frames only on the ticks that send (`frames`)."""
        if self.match.collision_world is None:
            return
        retained = max(self.tick + 1, tick - COMBAT_CATCH_UP + 1)
        first = tick
        if self.tick and tick > self.tick + 1:
            from ow174.game import bob_victim

            # A companion can enter its gun phase during the gap even if no
            # bot was firing. Its NPC victim may still need poses after it retires.
            if (self.earthshatters or self.companions.active or self.blizzard.flights
                    or self.blizzard.areas or any(
                not bot.dead and isinstance(bot.body_script, BotScript)
                and (bot.firing or bob_victim.active(bot.body_script.component))
                for bot in self.match.bots
            )):
                first = retained
        for target in self._targets():
            history = self.history.setdefault(target.entity, deque(maxlen=HISTORY))
            history.append((tick, target.feet, target.height, target.eye))
        # Replay the retained combat frames using the most recent poses. Player input
        # accepted just before the scheduler caught up joins only at the endpoint.
        for at in range(first, tick + 1):
            self.now, self.tick = now - (tick - at) * TICK, at
            self.ice_walls.update(at)
            self._advance_frost(at)
            for rocket in list(self.rockets):
                if at == tick or rocket.last_tick is not None:
                    self._fly(rocket)
            self.blizzard.update(at)
            earthshatter.update(self)
            self._sample_sleep_views(at)
            self.impulses.update(at)
            self.companions.update(at)
            self._bot_weapons(at)
            self.orisa_barriers.update(at, TICK)
            self.reinhardt_barriers.update()
            self.world_barriers.update(at)
            self.sigma_barriers.update(at, TICK)
            self.dynamite.update(at)
        for body, (phase, _activation, _health) in list(self.personal_barriers.items()):
            if not phase.active:
                self.personal_barriers.pop(body)
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
        current = {target.entity: target.owner for target in self._targets()}
        expired = {key for key, deadline in self.stuns.items() if deadline <= tick}
        for entity in expired:
            del self.stuns[entity]
        expired_sleep = {key for key, (_, deadline) in self.sleeps.items() if deadline <= tick}
        for entity in expired_sleep:
            del self.sleeps[entity]
            self.sleep_views.pop(entity, None)
        for entity in sorted(expired | expired_sleep):
            script = getattr(current.get(entity), "body_script", None)
            if script is not None and self.control_until(entity) <= tick:
                ashe.set_viper_reload_stunned(script.component, False)
        for table in (self.history, self.vitals, self.replacement_ticks, self.stuns, self.sleeps, self.sleep_views):
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

    def is_enemy(self, shooter, target) -> bool:
        source = _source_entity(shooter)
        if (target.owner is shooter or target.entity == source
                or _controller(shooter) is _controller(target.owner)):
            return False
        return self.match.game_map.free_for_all or target.team != shooter.team

    def _enemies(self, shooter) -> list[Target]:
        found = []
        for target in self._targets():
            if not self.is_enemy(shooter, target):
                continue
            if self.vitals_of(target).dead or immunity.untargetable(target.owner, target.entity):
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
        from ow174.game import bob_victim, heavy_charge, npc_control

        for bot in self.match.bots:
            script = bot.body_script
            controlled = npc_control.manual(bot)
            if controlled and bot.hero == heavy_charge.HERO and not isinstance(script, BotScript):
                heavy_charge.sync_victim(self.match, bot)
            if (bot.dead or not isinstance(script, BotScript) or script.frame > tick
                    or (not bot.firing and not controlled and not bob_victim.active(script.component))):
                continue
            try:
                allowed = npc_control.update_input(self, bot) if controlled else not self.is_stunned(bot)
                bob_victim.sample(bot, script.component)
                snapshot = script.advance_live(tick, bot.position)
                if controlled and bot.hero == heavy_charge.HERO:
                    heavy_charge.sync_victim(self.match, bot, script.component)
                shots, script.component.shots[:] = list(script.component.shots), []
                for state, number, time, activation, *_launch in shots:
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
                if controlled:
                    heavy_charge.stop(self.match, bot)
                log.exception("[game] %s: %s's weapon script failed", self.match.label(), bot.name)

    def _eye(self, player) -> tuple:
        x, y, z = player.mover.position
        crouched = player.mover.crouched
        height = eye_height(player.hero.body, crouched)
        return (x, y + height, z)

    def _shoot(
        self, shooter, command, state, number: int, time: int,
        *, activation: tuple[int, int], snapshot: Snapshot | None = None,
        viper_launch: tuple[float, int] | None = None,
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
            if (viper_launch is not None and shooter.hero.guid == 0x02E0000000000200
                    and state.instance.component is shooter.body_script.component):
                direction = ashe.viper_direction(direction, number, viper_launch)
        if not npc and self.orisa_barriers.admits(state):
            self.orisa_barriers.launch(shooter, state, origin, direction, command.yaw,
                                       frame=shot_frame, activation=activation, snapshot=snapshot)
            return
        if not npc and self.blizzard.fired(
            shooter, state, shot_frame, origin, direction, activation=activation, snapshot=snapshot,
        ):
            return
        direct = hit_def(state, node.field("m_6396149F"))
        speed = field("m_projectileSpeed")
        if not npc and state.instance.graph.index == 0x14FD and state.index == 12:
            cfg = (node.field("m_projectileEntity") or {}).get("m_entityDef")
            definition = state.evaluate(cfg, watch=False)
            fuse_seconds = ashe.dynamite_fuse_seconds(state)
            shot = None
            if isinstance(definition, expr.Asset):
                shot = self.projectiles.fired(shooter, state, shot_frame, definition.guid, fuse_seconds,
                                              origin, direction, activation=activation, snapshot=snapshot)
            # The reconstructed absorption payload uses authored volley damage.
            self.dynamite.fired(shooter, origin, direction, speed or 25.0, shot, fuse_seconds=fuse_seconds,
                               absorb_damage=direct.amount if direct is not None else 0.0)
            return
        directions = fixed_pellet_directions(state, direction)
        if speed:
            strike_radius = None
            if not npc and direct is not None and (state.instance.graph.index, node.pos) == (0x00E2, 3):
                strike_radius = max(field(f"m_6396149F.{key}", 0.0) for key in ("m_57DAE1B5", "m_61CD1F5B"))
            lifetime = field("m_projectileLifetime", 5.0) or 5.0
            reach = field("m_FEC435C6")
            if reach:
                lifetime = min(lifetime, reach / speed)
            blast = hit_def(state, node.field("m_D2D11CE9"))
            entity_def = (node.field("m_projectileEntity") or {}).get("m_entityDef")
            definition = state.evaluate(entity_def, watch=False) if entity_def is not None else None
            damaging = (direct is not None and direct.amount > 0.0) or (
                blast is not None and blast.amount > 0.0
            )
            for pellet in directions:
                shot = None
                if isinstance(definition, expr.Asset):
                    shot = self.projectiles.fired(
                        shooter, state, shot_frame, definition.guid, lifetime, origin, pellet,
                        activation=activation, snapshot=snapshot,
                    )
                if damaging or shot is not None:
                    self.rockets.append(
                        Rocket(shooter, shooter.team, origin, pellet, speed, lifetime, direct, blast, shot,
                               last_tick=self.tick if npc or strike_radius is not None else None,
                               target_limit=mei_frost.spray_target_limit(state), strike_radius=strike_radius)
                    )
            return
        if direct is None or direct.amount <= 0.0:
            return
        reach = field("m_FEC435C6", RANGE) or RANGE
        # The input supplies this simulation frame's aim and view time. Notices use the volley clock.
        view_tick = self.tick
        if command is not None:
            view_tick = command.frame - command.view_delay(shooter.quanta) * 1e6 / shooter.quanta
        hits = {}
        viper = (not npc and shooter.hero.guid == 0x02E0000000000200
                 and state.instance.graph.index == 0x14FC and node.pos in (14, 220))
        coach = not npc and state.instance.graph.index == 0x1513 and state.index == 16
        bob_gun = npc and shooter.hero == 0x02E000000000021D and (
            state.instance.graph.index == 0x153B and state.index == 11
        )
        for pellet in directions:
            resolved = self._hitscan(shooter, origin, pellet, reach, direct, view_tick, shot_frame,
                                     npc=npc, projectile_guards=viper or coach or bob_gun)
            if coach and resolved is not None:
                source, target, distance = resolved
                source_hits = hits.setdefault(_source_entity(source), (source, {}))[1]
                previous = source_hits.get(target.entity)
                # Reconstruct one target ring distance without depending on pellet order.
                if previous is None:
                    source_hits[target.entity] = (target, distance, 1)
                else:
                    source_hits[target.entity] = (target, min(previous[1], distance), previous[2] + 1)
        for source, source_hits in hits.values():
            self.impulses.coach_targets(source, source_hits, state, reflected=source is not shooter)

    def _projectile_guard(self, shown, origin, direction, reach, *, margin=0.0):
        """Test authored projectile guard boxes using the reconstructed eye and yaw."""
        actor = shown.owner
        if isinstance(actor, bots.Bot):
            return None
        if actor.hero.guid == 0x02E0000000000029:
            graph_index, node_pos, absorbs = 0x03A3, 18, False
        elif actor.hero.guid == 0x02E000000000023B:
            graph_index, node_pos, absorbs = 0x1D02, 7, True
        elif actor.hero.guid == 0x02E000000000007A and actor.hero.body == 0x0400000000000407:
            graph_index, node_pos, absorbs = 0x02CC, 9, True
        else:
            return None
        script = actor.body_script
        if script is None or script.component.owner_dead or self.is_stunned(actor):
            return None
        yaw = actor.mover.yaw
        horizontal = aim(yaw, 0)
        right = (horizontal[2], 0, -horizontal[0])
        forward, up = horizontal, (0, 1, 0)
        if graph_index == 0x02CC:
            # Reconstructed Matrix attachment follows both mech aim angles.
            pitch = actor.mover.pitch
            forward, up = aim(yaw, pitch), aim(yaw, pitch - 16384)
        eye = (shown.feet[0], shown.feet[1] + shown.eye, shown.feet[2])
        axes = (right, up, forward)
        offset = tuple(a - b for a, b in zip(origin, eye, strict=True))
        local_origin = tuple(_dot(offset, axis) for axis in axes)
        local_ray = tuple(_dot(direction, axis) for axis in axes)
        nearest = None
        for instance in script.component.instances.values():
            if instance.graph.index != graph_index:
                continue
            node = instance.graph.node(node_pos)
            state = instance.states.get(node.state)
            if state is None or not state.active:
                continue
            angle = _number(state.evaluate(node.field("m_025D0C39"), watch=False))
            if angle is None or _dot(direction, forward) > -math.cos(math.radians(angle)) + EPSILON:
                continue
            for box in node.field("m_boxes"):
                a = state.evaluate(box["m_CBA118B2"], watch=False)
                b = state.evaluate(box["m_AEF67365"], watch=False)
                if not isinstance(a, expr.Vec3) or not isinstance(b, expr.Vec3):
                    continue
                low, high = 0.0, reach
                for start, delta, x, y in zip(local_origin, local_ray, a, b, strict=True):
                    lower, upper = min(x, y) - margin, max(x, y) + margin
                    if abs(delta) <= EPSILON:
                        if not lower <= start <= upper:
                            break
                    else:
                        enter, leave = sorted(((lower - start) / delta, (upper - start) / delta))
                        low, high = max(low, enter), min(high, leave)
                        if low > high:
                            break
                else:
                    if nearest is None or low < nearest[0]:
                        nearest = low, state if absorbs else None
        return nearest

    @staticmethod
    def absorb_projectile(absorber, shooter, amount):
        if amount <= 0:
            return
        instance = absorber.instance
        with instance.component.operation():
            instance.write(expr.dynamic(absorber.node.field("m_F6CDC81C")), amount)
            instance.write(expr.dynamic(absorber.node.field("m_0A2CABA3")), expr.Entity(_source_entity(shooter)))
            instance.follow(absorber.node, "m_160B5DBE", absorber)

    def projected_barrier_targets(self, state):
        component = state.instance.component
        caster = next((player for player in self.match.players if player.body == component.entity), None)
        if caster is None or component.owner_dead or self.match.game_map.free_for_all:
            return ()
        origin = self._eye(caster)
        forward = aim(caster.mover.yaw, caster.mover.pitch)
        reach = _number(state.evaluate(state.node.field("m_radius"), watch=False), 0.0)
        factor = component.entity_var(9595, create=False)
        # The absent engine angle multiplier defaults to one in the reconstruction.
        angle = (10.0 if factor is None or factor.value() is None else
                 _number(state.evaluate(state.node.field("m_A30B383E"), watch=False), 0.0))
        threshold = math.cos(math.radians(angle))
        candidates = []
        for target in self._targets():
            if (target.owner is caster or target.team != caster.team or self.vitals_of(target).dead
                    or self.barrier_phase(target) is not None):
                continue
            a, b = target.segment()
            centre = _along(target.feet, (0, 1, 0), target.height / 2)
            distance = math.dist(centre, origin)
            if not 0 < distance <= reach:
                continue
            alignment = _dot(_sub(centre, origin), forward) / distance
            if alignment >= threshold and capsule_visible(self._collision(), origin, a, b, target.radius):
                candidates.append((-alignment, distance, target.entity))
        return (expr.Entity(min(candidates)[2]),) if candidates else ()

    def barrier_phase(self, target):
        for caster in self.match.players:
            script = caster.body_script
            if (script is None or caster.team != target.team or caster.hero is None
                    or caster.hero.guid != 0x02E0000000000068):
                continue
            for instance in script.component.instances.values():
                if instance.graph.index == 0x0244 and caster is target.owner and not script.component.owner_dead:
                    phase = instance.states.get(2)
                elif (instance.graph.index == 0x025C and not self.match.game_map.free_for_all
                      and expr.to_entity(instance.context().var(expr.INSTANCE, 536)) == target.entity):
                    phase = instance.states.get(10)
                else:
                    continue
                if phase is not None and phase.active:
                    return phase
        return None

    def _personal_barrier(self, target, origin, direction, reach):
        phase = self.barrier_phase(target)
        if phase is None:
            return None
        # Capsule-centre attachment is reconstructed.
        centre = _along(target.feet, (0, 1, 0), target.height / 2)
        radius = PERSONAL_BARRIER_RADIUS
        delta = _sub(origin, centre)
        distance = (0.0 if _dot(delta, delta) <= radius * radius else
                    ray_capsule(origin, direction, centre, centre, radius))
        return (distance, phase) if distance is not None and distance <= reach else None

    def damage_personal_barrier(self, body, phase, shooter, amount):
        activation = phase.counter, phase.start
        stored = self.personal_barriers.get(body)
        if stored is not None and stored[:2] == (phase, activation):
            health = stored[2]
        else:
            cfg = graph.graph(0x0256).states[0].field("m_amount")
            default = cfg["m_configVars"][1]["m_A53100D5"]["m_value"]
            rule = phase.instance.component.world.ruleset(0x1D43)
            health = _number(rule, default)
        dealt = min(health, max(0.0, amount))
        if dealt <= 0:
            return
        health -= dealt
        component = phase.instance.component
        if health:
            self.personal_barriers[body] = phase, activation, health
        else:
            self.personal_barriers.pop(body, None)
        # 0256/node10 forwards pool loss to 0242. The owner body stands in
        # for the pet sender while the barrier is represented only in combat.
        runtime.send_message(component, 0x0240000000000146,
                             {0x2A: expr.f32(dealt), 0x12: expr.Entity(_source_entity(shooter))}, component.entity)
        if not health:
            phase.end(True)
        component.run_now()

    def _hitscan(self, shooter, origin, direction, reach, direct, view_tick, shot_frame, *, npc=False, projectile_guards=False):
        """Resolve one ray; each pellet sees health/death left by the previous one."""
        best = None
        intercept = None
        barrier = None
        for target in self._enemies(shooter):
            shown = self.seen(target, view_tick)
            if shown is None:
                continue
            if projectile_guards:
                bubble = self._personal_barrier(shown, origin, direction, reach)
                if bubble is not None and (barrier is None or bubble[0] < barrier[0]):
                    barrier = bubble[0], target.entity, bubble[1]
                guard = self._projectile_guard(shown, origin, direction, reach)
                if guard is not None and (intercept is None or guard[0] < intercept[0]):
                    intercept = guard[0], target.owner, guard[1]
            a, b = shown.segment()
            t = ray_capsule(origin, direction, a, b, shown.radius)
            if t is not None and t <= reach and (best is None or t < best[0]):
                best = (t, target, shown)
        barrier_host = None
        world_barrier = None
        if projectile_guards:
            for host in self.deployed_barrier_hosts():
                contact = host.intercept(shooter, origin, direction, reach)
                if contact is not None and (world_barrier is None or contact[0] < world_barrier[0]):
                    world_barrier, barrier_host = contact, host
        ice_wall = self.ice_walls.cast(origin, direction, reach)
        if (best is None and intercept is None and barrier is None and world_barrier is None
                and ice_wall is None and not self.dynamite.grenades):
            return
        limit = reach if best is None else best[0]
        if intercept is not None:
            limit = min(limit, intercept[0])
        if barrier is not None:
            limit = min(limit, barrier[0])
        if world_barrier is not None:
            limit = min(limit, world_barrier[0])
        if ice_wall is not None:
            limit = min(limit, ice_wall[0])
        wall = ray_world(self._collision(), origin, direction, limit)
        grenade = self.dynamite.ray_hit(shooter, origin, direction,
                                      min(limit, wall) if wall is not None else limit)
        if grenade is not None:
            self.dynamite.detonate(grenade[1])
            return
        if ice_wall is not None and ice_wall[0] == limit and (wall is None or wall >= limit):
            self.ice_walls.damage(ice_wall[1], shooter, direct.amount * direct.scale(limit))
            return
        if world_barrier is not None and world_barrier[0] == limit and (wall is None or wall >= limit):
            barrier_host.damage(world_barrier[1], direct.amount * direct.scale(limit))
            return
        if barrier is not None and barrier[0] == limit and (wall is None or wall >= limit):
            self.damage_personal_barrier(barrier[1], barrier[2], shooter, direct.amount * direct.scale(limit))
            return
        if intercept is not None and intercept[0] == limit and (wall is None or wall >= limit):
            _, defender, absorber = intercept
            if absorber is not None:
                # Reconstructed payload before target mitigation.
                self.absorb_projectile(absorber, shooter, direct.amount * direct.scale(limit))
                return
            contact = tuple(a + b * limit for a, b in zip(origin, direction, strict=True))
            # Reconstructed redirect uses the defender's aim and remaining range.
            return self._hitscan(defender, contact, aim(defender.mover.yaw, defender.mover.pitch),
                                 reach - limit, direct, self.tick, shot_frame, npc=npc)
        if best is None:
            return
        t, target, shown = best
        if wall is not None and wall < t:
            return
        hit_y = origin[1] + direction[1] * t
        critical = (hit_y >= shown.feet[1] + shown.eye - HEAD_BELOW
                    and not immunity.blocks_headshot(target.owner, target.entity))
        amount = direct.amount * direct.scale(t) * (direct.critical if critical else 1.0)
        self._damage(
            shooter, target, amount, critical and direct.critical > 1.0, direct.loadout, shot_frame,
            world_tick=self.tick if npc else None, damage_tags=direct.tags,
        )
        self._hit_effect(target, direct.effect)
        return shooter, target, t

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
        while duration > 0.0:
            if rocket.strike_radius is not None:
                duration = self._fly_strike(rocket, duration)
            else:
                duration = self._fly_segment(rocket, duration)

    def _fly_strike(self, rocket, duration):
        origin, direction, radius = rocket.position, rocket.direction, rocket.strike_radius
        reach = rocket.speed * duration
        terrain = contacts.scene_sweep(self._collision().engine, origin, _along(origin, direction, reach),
                                       ((0.0, 0.0, 0.0),), radius, MOVER)
        wall = terrain is not None and _dot(direction, terrain[2]) < -EPSILON
        limit = terrain[0] * reach if wall else reach
        ice = self.ice_walls.cast(origin, direction, limit, radius=radius)
        if ice is not None:
            limit = ice[0]
        targets = self._enemies(rocket.shooter)
        guards = []
        for target in targets:
            hit = self._projectile_guard(target, origin, direction, limit, margin=radius)
            if hit is not None and (hit[0] < limit or (hit[0] == limit and not wall and ice is None)):
                guards.append((hit[0], "guard", (target.owner, hit[1])))
        guard = min(guards, key=lambda hit: hit[0], default=None)
        if guard is not None:
            limit = guard[0]
        stopped = wall or ice is not None or guard is not None

        crossed = []
        for index, host in enumerate(self.deployed_barrier_hosts()):
            for distance, actor, entity in host.projectile_contacts(rocket.shooter, origin, direction, limit):
                key = index, entity
                if key not in rocket.strike_barriers:
                    crossed.append((distance, 0, key, (host, actor)))
        for target in targets:
            if target.entity in rocket.hit_entities:
                continue
            a, b = target.segment()
            width = target.radius + radius
            distance = (0.0 if segment_distance(origin, a, b) <= width
                        else ray_capsule(origin, direction, a, b, width))
            if distance is not None and distance <= limit:
                crossed.append((distance, 1, target.entity, target))
        direct = rocket.direct
        for distance, kind, key, target in sorted(crossed, key=lambda hit: hit[:3]):
            if stopped and distance >= limit:
                break
            amount = direct.amount * direct.scale(rocket.flown * rocket.speed + distance)
            if kind == 0:
                rocket.strike_barriers.add(key)
                host, actor = target
                host.damage(actor, amount)
                continue
            rocket.hit_entities.add(key)
            phase = self.barrier_phase(target)
            if phase is not None:
                self.damage_personal_barrier(key, phase, rocket.shooter, amount)
            else:
                self._damage(rocket.shooter, target, amount, False, direct.loadout, self.tick,
                             world_tick=self.tick, damage_tags=direct.tags)

        if guard is not None:
            return self._resolve_projectile_defense(rocket, duration, guard)
        travel = limit / rocket.speed
        rocket.flown += travel
        rocket.left = max(0.0, rocket.left - travel)
        rocket.position = _along(origin, direction, limit)
        if ice is not None:
            amount = direct.amount * direct.scale(rocket.flown * rocket.speed)
            self.ice_walls.damage(ice[1], rocket.shooter, amount)
        if stopped or rocket.left <= EPSILON:
            self._landed(rocket)
        return 0.0

    def _fly_segment(self, rocket: Rocket, duration: float) -> float:
        # Sweep the remaining flight even when the range or lifetime ends inside this tick.
        step = rocket.speed * duration
        best = None
        targets = self._enemies(rocket.shooter)
        for target in targets:
            if target.entity in rocket.hit_entities:
                continue
            a, b = target.segment()
            t = ray_capsule(rocket.position, rocket.direction, a, b, target.radius)
            if t is not None and t <= step and (best is None or t < best[0]):
                best = (t, target)
        wall = ray_world(self._collision(), rocket.position, rocket.direction, step)
        if wall is not None and (best is None or wall < best[0]):
            best = (wall, None)
        direct = rocket.direct
        spray = direct is not None and direct.loadout == mei_frost.SPRAY_LOADOUT
        dart = direct is not None and direct.effect == SLEEP_DART_EFFECT
        if dart or spray:
            contact = self._projectile_defense(rocket, targets, step if best is None else best[0],
                                               guards=dart)
            if contact is not None and (wall is None or contact[0] < wall):
                return self._resolve_projectile_defense(rocket, duration, contact)
        if best is None:
            rocket.position = _along(rocket.position, rocket.direction, step)
            rocket.flown += duration
            rocket.left -= duration
            if rocket.left <= 0.0:
                self._landed(rocket)
            return 0.0
        t, struck = best
        travel = t / rocket.speed
        rocket.flown += travel
        point = _along(rocket.position, rocket.direction, t)
        if spray and struck is not None:
            rocket.hit_entities.add(struck.entity)
            self._damage(
                rocket.shooter, struck, direct.amount, False, direct.loadout, self.tick,
                world_tick=self.tick, damage_tags=direct.tags,
            )
            rocket.left -= travel
            if len(rocket.hit_entities) < rocket.target_limit and rocket.left > 0.0:
                rocket.position = point
                return duration - travel
            self._landed(rocket)
            return 0.0
        self._landed(rocket)
        if struck is not None and rocket.direct is not None and rocket.direct.amount > 0.0:
            amount = rocket.direct.amount * rocket.direct.scale(rocket.flown * rocket.speed)
            self._damage(
                rocket.shooter, struck, amount, False, rocket.direct.loadout, self.tick,
                world_tick=self.tick, damage_tags=rocket.direct.tags,
            )
            self._hit_effect(struck, rocket.direct.effect)
        blast = rocket.blast
        if blast is None or blast.amount <= 0.0:
            return 0.0
        for target in self._enemies(rocket.shooter):
            a, b = target.segment()
            distance = max(0.0, segment_distance(point, a, b) - target.radius)
            scalar = blast.splash(distance)
            # Start just outside an impact wall, on the projectile's approach side.
            visible_from = _along(point, rocket.direction, -0.0001) if wall is not None else point
            if scalar > 0.0 and capsule_visible(self._collision(), visible_from, a, b, target.radius):
                self._damage(
                    rocket.shooter, target, blast.amount * scalar, False, blast.loadout, self.tick,
                    world_tick=self.tick, damage_tags=blast.tags,
                )
                self._hit_effect(target, blast.effect)
        return 0.0

    def deployed_barrier_hosts(self):
        return self.world_barriers, self.sigma_barriers, self.orisa_barriers, self.reinhardt_barriers

    def _projectile_defense(self, rocket, targets, reach, *, guards):
        origin, direction = rocket.position, rocket.direction
        contacts = []
        ice = self.ice_walls.cast(origin, direction, reach)
        if ice is not None:
            contacts.append((ice[0], "ice", (ice[1],)))
        for host in self.deployed_barrier_hosts():
            hit = host.intercept(rocket.shooter, origin, direction, reach)
            if hit is not None:
                contacts.append((hit[0], "world", (host, hit[1])))
        for target in targets:
            bubble = self._personal_barrier(target, origin, direction, reach)
            if bubble is not None:
                contacts.append((bubble[0], "personal", (target.entity, bubble[1])))
            guard = self._projectile_guard(target, origin, direction, reach) if guards else None
            if guard is not None:
                contacts.append((guard[0], "guard", (target.owner, guard[1])))
        order = ("ice", "world", "personal", "guard")
        return min(contacts, key=lambda contact: (contact[0], order.index(contact[1])), default=None)

    def _resolve_projectile_defense(self, rocket, duration, contact):
        distance, kind, payload = contact
        travel = distance / rocket.speed
        rocket.flown += travel
        direct = rocket.direct
        amount = direct.amount * direct.scale(rocket.flown * rocket.speed)
        if kind == "guard":
            defender, absorber = payload
            if absorber is not None:
                if rocket.shot is not None:
                    self.projectiles.cancel(rocket.shot)
                    rocket.shot = None
                self._landed(rocket)
                self.absorb_projectile(absorber, rocket.shooter, amount)
                return 0.0
            rocket.left -= travel
            if rocket.left <= 0.0 or rocket.reflections >= 8:
                self._landed(rocket)
                return 0.0
            if rocket.shot is not None:
                self.projectiles.flown(rocket.shot, rocket.flown)
                rocket.shot = None
            point = _along(rocket.position, rocket.direction, distance)
            rocket.direction = aim(defender.mover.yaw, defender.mover.pitch)
            rocket.position = _along(point, rocket.direction, 0.0001)
            rocket.shooter, rocket.team = defender, defender.team
            rocket.reflections += 1
            return min(duration - travel, rocket.left)

        self._landed(rocket)
        if kind == "ice":
            self.ice_walls.damage(payload[0], rocket.shooter, amount)
        elif kind == "world":
            host, actor = payload
            host.damage(actor, amount)
        else:
            entity, phase = payload
            self.damage_personal_barrier(entity, phase, rocket.shooter, amount)
        return 0.0

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
            ally = (_controller(target.owner) is _controller(healer)
                    or (not self.match.game_map.free_for_all and target.team == healer.team))
            if target.owner is not healer and not ally:
                continue
            vitals = self.vitals_of(target)
            if vitals.dead:
                continue
            a, b = target.segment()
            if segment_distance(centre, a, b) - target.radius > radius:
                continue
            given = vitals.heal(amount)
            if given <= 0.0:
                continue
            healer.stats.add(stats.HEALING, given)
            self._sync_body_health(target.owner, vitals)

    @staticmethod
    def _sync_body_health(owner, vitals: Vitals) -> None:
        script = getattr(owner, "body_script", None)
        if script is not None:
            script.set_health(vitals.total, sum(vitals.most.values()))

    def change_grasp_shields(self, body, amount, cap=None):
        vitals = self.vitals.get(body)
        if vitals is None or vitals.dead or not math.isfinite(amount):
            return 0.0
        current = vitals.now.get(GRASP_SHIELDS, 0.0)
        remaining = max(0.0, current + amount)
        if cap is not None:
            if not math.isfinite(cap):
                return current
            remaining = max(current, min(cap, remaining))
        if remaining <= EPSILON:
            remaining = 0.0
        if remaining != current:
            if GRASP_SHIELDS not in vitals.ids:
                vitals.ids[GRASP_SHIELDS] = max(vitals.ids.values()) + 1
            vitals.now[GRASP_SHIELDS] = remaining
            if cap is not None or remaining == 0.0:
                vitals.most[GRASP_SHIELDS] = remaining
            vitals.changed.add(GRASP_SHIELDS)
            owner = next((player for player in self.match.players if player.body == body), None)
            if owner is not None:
                self._sync_body_health(owner, vitals)
        return remaining

    def _collision(self):
        from ow174.game.collision import CollisionError

        world = self.match.collision_world
        if world is None:
            raise CollisionError("match collision is not ready")
        return world

    # --- damage and death --------------------------------------------------------------------------

    def _damage(
        self, shooter, target: Target, amount: float, critical: bool, loadout, frame: int,
        *, world_tick: int | None = None, damage_tags: tuple[int, ...] = (),
    ) -> float:
        vitals = self.vitals_of(target)
        if vitals.dead or amount <= 0.0 or immunity.damage_immune(target.owner, target.entity):
            return 0.0
        dealt = vitals.damage(amount * immunity.damage_scale(target.owner, target.entity))
        if dealt <= 0.0:
            return 0.0
        sombra_hack.cancel_hack(target.owner)
        sleep = self.sleeps.get(target.entity)
        if sleep is not None and SLEEP_NO_WAKE_TAG not in damage_tags:
            started, deadline = sleep
            at = self.tick if world_tick is None else world_tick
            if (at - started) * TICK > SLEEP_DAMAGE_GRACE and at < deadline:
                wake = at + math.ceil(SLEEP_WAKE_SECONDS / TICK)
                self.sleeps[target.entity] = (started, min(deadline, wake))
        killed = vitals.now[HEALTH_POOL] <= EPSILON
        if loadout == mei_frost.SPRAY_LOADOUT and not killed:
            self._frost_hit(shooter, target, self.tick if world_tick is None else world_tick)
        if killed:
            self._clear_frost(target.entity)
            vitals.now[HEALTH_POOL] = 0.0
            vitals.died_at = self.now
            vitals.death_tick = self.match.tick if world_tick is None else world_tick
            vitals.killer = _source_entity(shooter)
        beneficiary = _controller(shooter)
        if not isinstance(beneficiary, bots.Bot) and target.owner is not beneficiary:
            flags = (CRITICAL if critical else 0) | (KILLING if killed else 0)
            self._tell_hit(beneficiary, target, dealt, flags, frame)
            beneficiary.stats.add(stats.HERO_DAMAGE, dealt)
            # Reconstructed Ashe policy: one ultimate point per actual enemy pool
            # damage, capped at cost. B.O.B. is a separate actor and earns no charge here.
            # Scope this until other heroes' generation modifiers are established.
            if (shooter is beneficiary and shooter.hero.guid == ASHE and shooter.body_script is not None
                    and not self.companions.active_for(shooter.body)):
                script = shooter.body_script
                component = script.component
                charge = _number(component.entity_var(ULT_CHARGE).value(), 0.0)
                cost = _number(component.entity_var(ULT_COST).value(), 0.0)
                if cost > 0:
                    script.set_ult_charge(min(cost, charge + dealt))
        victim = target.owner
        if killed and isinstance(victim, bots.Bot):
            # The NPC death script owns its terminal health change and death event ordering.
            self._bot_died(shooter, victim, critical, loadout, vitals.death_tick)
        else:
            self._sync_body_health(victim, vitals)
            if killed:
                self._died(shooter, victim, critical, loadout)
        return dealt

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
        from ow174.game import heavy_charge

        self.stuns.pop(bot.entity, None)
        self.sleeps.pop(bot.entity, None)
        self.sleep_views.pop(bot.entity, None)
        heavy_charge.stop(self.match, bot)
        bot.died(tick)
        if not bot.retain_corpse:
            self.respawns.append((self.now + BOT_RESPAWN, bot))
        beneficiary = _controller(killer)
        if not isinstance(beneficiary, bots.Bot):
            beneficiary.stats.add(stats.ELIMINATIONS)
            self._kill_notice(beneficiary, bot)
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
        from ow174.game import heavy_charge

        self.stuns.pop(victim.body, None)
        self.sleeps.pop(victim.body, None)
        self.sleep_views.pop(victim.body, None)
        label = self.match.label()
        log.info("[game] %s: %s killed %s", label, killer.name, victim.name)
        victim.stats.add(stats.DEATHS)
        source = _source_entity(killer)
        beneficiary = _controller(killer)
        if beneficiary is not victim and not isinstance(beneficiary, bots.Bot):
            beneficiary.stats.add(stats.ELIMINATIONS)
            self._kill_notice(beneficiary, victim)
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
                heavy_charge.retire_victim(component)
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
