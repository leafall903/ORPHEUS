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
    ana_biotic,
    ana_sleep,
    assassin,
    barriers,
    brigitte_barrier,
    doomfist_punch,
    bots,
    contacts,
    defenses,
    deflection,
    earthshatter,
    graviton,
    halt,
    heavy_charge,
    hook,
    lucio_soundwave,
    mei_blizzard,
    mei_frost,
    mei_wall,
    observers,
    orisa_barrier,
    orisa_supercharger,
    pools,
    projectiles,
    sigma_barrier,
    torbjorn_turret,
    sigma_grasp,
    stats,
    ultimate_charge,
    voicelines,
    winston_barrier,
    winston_jump,
    winston_primal,
    winston_weapons,
    world,
    zarya_barrier,
    zarya_weapons,
)
from ow174.game.bits import BitWriter
from ow174.game.collision import MOVER, World
from ow174.game.commands import Command
from ow174.game.mover import Mover, mover_data, statescript_mods
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
EYE_STANDING = {0x0400000000000024: 1.8, 0x04000000000002A3: 1.425, 0x04000000000003BF: 1.6}
EYE_CROUCHED = {0x04000000000003CF: 1.085, 0x0400000000000024: 1.26,
                0x04000000000002A3: 0.9975, 0x04000000000003BF: 1.12}  # STUFirstPersonComponent (DATA)
EYE_CROUCHED_DEFAULT = 1.15
def eye_height(body: int, crouched: bool = False) -> float:
    """Authored FirstPerson height where audited; preserve existing fallback elsewhere."""
    return (EYE_CROUCHED.get(body, EYE_CROUCHED_DEFAULT) if crouched
            else EYE_STANDING.get(body, EYE))


HEAD_BELOW = 0.2  # m under the eye where the head starts (unverified)
HISTORY = 64  # ticks of body positions kept for the shots' view time (about 1 s; unverified: retail's limit)
COMBAT_CATCH_UP = 64  # at most about a second of retained combat frames per update
RANGE = 200.0  # m a hitscan shot reaches when its volley names none (unverified)
RESPAWN = 10.0  # s a hero waits in the PvP modes (0F4A and 0EC2 send 06FD.025 {10}: DATA)
PRACTICE_RESPAWN = 3.0  # s in the Practice Range (13C0 sends 06FD.025 {3}: DATA)
BOT_RESPAWN = 3.0  # s a training bot lies dead (unverified: OW2 made two killed bots again after 2.98 s)
RESPAWN_RETRY = 0.25  # s between attempts when all player body IDs are still reserved
MELEE = {0x0043: (4, 1.5, 1.0)}  # graph -> (its ModifyHealth state, metres ahead of the eye, radius): DATA
REINHARDT = 0x02E0000000000007
HAMMER = 0x0048
# ModifyHealth state -> main and near TrackTargets states, from the 104319 graph.
HAMMER_TARGETS = {3: (1, 8), 4: (2, 9)}
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
        self.last_damage_source: int | None = None
        # Preserve the legacy snapshot keys for the temporary ability shield pool.
        self.grasp_shields = 0.0
        self.grasp_max = 0.0
        self.grasp_decay: tuple[float, float] | None = None
        self.grasp_activation: tuple[int, int] | None = None
        self.healing_block: list[tuple[float, float]] = []
        self.healing_boost: list[tuple[float, float]] = []

    @property
    def dead(self) -> bool:
        return self.died_at is not None

    @property
    def total(self) -> float:
        return sum(self.now.values()) + self.grasp_shields

    def damage(self, amount: float, *, bypass_armour=False, beam=False) -> float:
        """Take a hit from shields, then armour, then health (unverified, as OW1 did: what reaches armour
        while it has some is ARMOUR_BLOCK less, at most halved). Returns what the pools lost."""
        left = max(0.0, amount)
        taken = min(left, self.grasp_shields)
        if taken > 0:
            self.grasp_shields -= taken
            self.changed.add(SHIELDS_POOL)
            left -= taken
        for kind in (SHIELDS_POOL, ARMOUR_POOL, HEALTH_POOL):
            if left <= 0.0:
                break
            if kind == ARMOUR_POOL and self.now[kind] > 0.0 and not bypass_armour:
                left = left * .8 if beam else max(left - ARMOUR_BLOCK, left / 2)
            take = min(left, self.now[kind])
            if take > 0.0:
                self.now[kind] -= take
                self.changed.add(kind)
                left -= take
                taken += take
        return taken

    def grant_temporary_shields(self, amount, cap, decay_at, rate):
        grant = min(cap - self.grasp_shields, amount)
        if grant <= 0:
            return False
        self.grasp_shields += grant
        self.grasp_max = max(self.grasp_max, self.grasp_shields)
        self.grasp_decay = decay_at, rate
        self.changed.add(SHIELDS_POOL)
        return True

    def decay_temporary_shields(self, now):
        if self.grasp_decay is None:
            return False
        previous, rate = self.grasp_decay
        if self.dead or self.grasp_shields <= 0:
            self.grasp_decay = None
        elif now > previous:
            self.grasp_shields = max(0.0, self.grasp_shields - rate * (now - previous))
            self.grasp_decay = (now, rate) if self.grasp_shields > 0 else None
            self.changed.add(SHIELDS_POOL)
            return True
        return False

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
            if kind == SHIELDS_POOL and self.grasp_max > 0:
                parts[kind * POOLS + 1] = world.pool(4, kind, self.grasp_max, self.grasp_shields)
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
    tags: tuple = ()  # authored ModifyHealth identifier GUIDs, including charge exclusions
    knockback_rings: tuple = ()  # (radius, horizontal velocity, vertical velocity)

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


def damage_tags(state, modify):
    """Evaluate the authored ModifyHealth tags without guessing them from a loadout."""
    found = []
    for tag in modify.get("m_148F3152") or ():
        if isinstance(tag, dict):
            value = state.evaluate(tag.get("m_EE3055BD"), watch=False)
            if isinstance(value, expr.Asset):
                found.append(value.guid)
    return tuple(found)


def hit_def(state, cfg, *, blast_knockback=False) -> HitDef | None:
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
    knockback_rings = []
    if blast_knockback:
        horizontal = _number(state.evaluate(modify.get("m_E33EFEE1"), watch=False), 0.0)
        vertical = _number(state.evaluate(modify.get("m_10399AE7"), watch=False), 0.0)
        # Reconstructed Helix velocity delta using the first covering authored ring.
        for ring in modify.get("m_knockbackRings") or ():
            radius = _number(state.evaluate(ring.get("m_radius"), watch=False), 0.0)
            planar_scale = _number(state.evaluate(ring.get("m_9102DA3F"), watch=False), 0.0)
            lift_scale = _number(state.evaluate(ring.get("m_10399AE7"), watch=False), 0.0)
            knockback_rings.append((radius, horizontal * planar_scale, vertical * lift_scale))
    return HitDef(
        amount=amount,
        loadout=loadout.guid if isinstance(loadout, expr.Asset) else None,
        falloff_start=value("m_EAD7F104"),
        falloff_end=value("m_157E5BC5"),
        falloff_floor=value("m_72A81154"),
        critical=value("m_A872C70E", 2.0 if state.instance.graph.index == 0x0BD4 else 1.0),
        rings=tuple(sorted(rings)),
        effect=effect.guid if isinstance(effect, expr.Asset) else None,
        tags=damage_tags(state, modify),
        knockback_rings=tuple(sorted(knockback_rings)),
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
class ChargeContact:
    """Reconstructed capture service state for one authored Charge activation."""

    activation: int
    pinned: int | None = None
    hit: set[tuple[int, int | None]] = field(default_factory=set)


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
    radius: float = 0.0
    penetrating: bool = False
    hit_targets: set[tuple[int, int | tuple[int | None, int] | None]] = field(default_factory=set)
    gravity: float = 0.0
    velocity: tuple[float, float, float] | None = None
    detonate_on_expiry: bool = False
    charge_origin: tuple[int, int | None] | None = None
    definition: int | None = None
    reflected: bool = False
    deploy_body: int | None = None
    supercharger: bool = False
    halt_body: int | None = None
    halt_entity: int | None = None
    turret_instance: object = None


class Combat:
    """The match's health, hits, deaths and respawns."""

    def __init__(self, match) -> None:
        self.match = match
        self.vitals: dict[int, Vitals] = {}  # body or bot entity -> its health
        self.history: dict[int, deque] = {}  # entity -> (tick, feet, height, eye) of the last HISTORY ticks
        self.replacement_ticks: dict[int, int] = {}  # replacement body -> authoritative world tick
        self.rockets: list[Rocket] = []
        self.frost: dict[int, dict[tuple[int, int | None, int], tuple[float, float]]] = {}
        self.blizzard = mei_blizzard.Blizzard(self)
        self.gravitons = graviton.Gravitons(self)
        self.winston_bubbles = winston_barrier.Barriers(self)
        self.orisa_barriers = orisa_barrier.Barriers(self)
        self.sigma_barriers = sigma_barrier.Barriers(self)
        self.brigitte_barriers = brigitte_barrier.Barriers(self)
        self.punches = doomfist_punch.Punches()
        self.superchargers = orisa_supercharger.Superchargers(self)
        self.torbjorn_turrets = torbjorn_turret.Turrets(self)
        self.halt = halt.Halt(self)
        self.hooks = hook.Hooks(self)
        self.winston_jump = winston_jump.JumpPack(self)
        self.winston_primal = winston_primal.Primal(self)
        self.walls = mei_wall.Walls(self)
        self.respawns: list[tuple[float, object]] = []  # (when, a player or a bot)
        self.next_bot = bots.BOT_ENTITY + len(match.bots)
        self.now = 0.0
        self.tick = 0
        self.melee_seen: dict[int, dict[tuple[int, int], int]] = {}  # body -> counters by instance/state
        self.stuns: dict[int, int] = {}  # body identity -> exclusive world-tick deadline
        self.sleeps: dict[int, ana_sleep.Sleep] = {}
        self.zarya_bubbles: dict[int, zarya_barrier.Bubble] = {}
        self.zarya_casts: dict[tuple[int, int], int] = {}
        self.zarya_beam_frames: dict[int, int] = {}
        self.tesla_frames: dict[int, int] = {}
        self.hammer_hits: dict[
            int, dict[tuple[int, int], tuple[int, set[tuple[str, int | tuple[int, int | None]]]]]
        ] = {}
        self.charge_drives: dict[int, tuple[int, float]] = {}  # body -> activation, heading radians
        self.charge_contacts: dict[int, ChargeContact] = {}
        self.controls: dict[tuple[int, int, str], float] = {}  # (victim, source, kind) -> expiry seconds
        self.knockbacks: dict[int, Mover] = {}  # NPC physics only; players already own their mover
        self.shatters: dict[int, earthshatter.Wave] = {}
        self.barrier_geometry = barriers.configured_geometry()
        self.barriers: dict[int, barriers.Barrier] = {}
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
        from ow174.game import npc_control

        assassin.retire_victim(self.match, player.body)
        npc_control.retire_player_target(self.match, player)
        self.hooks.retire(player.body)
        self._release_charge(player.body)
        zarya_barrier.retire(self, player.body)
        self.zarya_beam_frames.pop(player.body, None)
        self.tesla_frames.pop(player.body, None)
        self.winston_jump.retire(player.body)
        self.orisa_barriers.retire(player.body)
        self.sigma_barriers.retire(player.body)
        brig_shield = self.brigitte_barriers.shields.pop(player.body, None)
        self.punches.retire(player.body)
        self.superchargers.retire(player.body)
        self.torbjorn_turrets.retire(player.body)
        self.halt.retire(player.body)
        self.winston_primal.retire(player.body)
        self.walls.retire(player.body)
        self.blizzard.retire(player.body)
        self.gravitons.retire(player.body)
        self.frost.pop(player.body, None)
        self.shatters.pop(player.body, None)
        self._release_captured(player.body)
        self.controls = {key: end for key, end in self.controls.items() if key[0] != player.body}
        self.vitals.pop(player.body, None)
        self.history.pop(player.body, None)
        self.replacement_ticks.pop(player.body, None)
        self.melee_seen.pop(player.body, None)
        self.stuns.pop(player.body, None)
        self.sleeps.pop(player.body, None)
        self.hammer_hits.pop(player.body, None)
        self.charge_drives.pop(player.body, None)
        if self.barriers.pop(player.body, None) is not None or brig_shield is not None:
            # A later body can reuse the ID while another player's swing is active.
            key = ("barrier", player.body)
            for swings in self.hammer_hits.values():
                for _, hits in swings.values():
                    hits.discard(key)
        # Spray frost, leased projectiles and redirected flights belong to this body.
        self.rockets[:] = [
            rocket for rocket in self.rockets
            if rocket.shooter is not player or (
                rocket.shot is None and not rocket.reflected
                and rocket.definition != graviton.DEFINITION and rocket.deploy_body is None
                and getattr(rocket.direct, "loadout", None) != mei_frost.SPRAY
            )
        ]
        self.respawns[:] = [(when, who) for when, who in self.respawns if who is not player]

    def record_replacement(self, body: int) -> None:
        """Keep the creation boundary independent of the rolling pose history."""
        self.replacement_ticks[body] = self.match.tick
        self.stuns.pop(body, None)

    def is_stunned(self, actor, *, tick: int | None = None) -> bool:
        return self.controlled(_source_entity(actor), tick=tick)

    def _hit_effect(self, target: Target, effect: int | None) -> None:
        """Resource-backed stun reconstruction; native immunity/presentation remain unverified."""
        if effect == ana_sleep.EFFECT:
            if not self.vitals_of(target).dead and not defenses.control_immune(target.owner):
                self.sleeps[target.entity] = ana_sleep.Sleep(
                    self.tick, self.tick + math.ceil(ana_sleep.DURATION / TICK))
                self._interrupt_control(target)
            return
        if effect != FLASHBANG_EFFECT or self.vitals_of(target).dead:
            return
        variables = graph.mode_variables(self.match.game_map.mode_guid) or {}
        duration = 1.7 if expr.truthy(variables.get(FLASHBANG_MODE_FLAG)) else 0.8
        deadline = self.tick + math.ceil(duration / TICK)
        self.stuns[target.entity] = max(deadline, self.stuns.get(target.entity, 0))
        self._interrupt_control(target)

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
        self.sigma_barriers.sync(player)
        self._sync_barrier(player)
        self.brigitte_barriers.sync(player)
        self.punches.sync(player)
        self._sync_charge(player)
        zarya_barrier.sync(self, player)
        if script.hero == winston_weapons.HERO:
            self.winston_primal.sync_health(player, dead=self.is_dead(player))
        shots, script.component.shots[:] = list(script.component.shots), []
        if self.is_dead(player):
            self.charge_drives.pop(player.body, None)
            self.walls.cancel_preview(player)
            return
        if self.controlled(player.body):
            self.walls.cancel_preview(player)
            return
        if self.walls.command(player, command):
            return
        if command is not None:
            self._start_shatter(player)
            if script.hero == hook.ROADHOG:
                self.hooks.command(player, command)
        if command is not None and self._charge_counter(player):
            return
        if command is not None:
            if script.hero == brigitte_barrier.HERO:
                self.brigitte_barriers.bash(player, command)
            for state, number, time, activation in shots:
                try:
                    self._shoot(player, command, state, number, time, activation=activation)
                except Exception:
                    log.exception("[game] %s: %s's shot failed", self.match.label(), player.name)
        try:
            self._melee(player, command, script)
            self._hammer(player, command, script)
            if command is not None and script.hero == lucio_soundwave.HERO:
                lucio_soundwave.consume(self, player, command, script)
        except Exception:
            log.exception("[game] %s: %s's melee failed", self.match.label(), player.name)
        if command is not None and script.hero == zarya_barrier.HERO:
            zarya_weapons.beam(self, player, command, script)
        elif command is not None and script.hero == winston_weapons.HERO:
            self.winston_jump.command(player, command)
            if self.winston_primal.health_state(player) is None:
                winston_weapons.tesla(self, player, command, script)
            else:
                self.winston_primal.punches(player, command)

    def shatter_ground(self, body):
        """Declared BE/37 downward1.5m ground query, using the posed owner's feet."""
        player = next((p for p in self.match.players if p.body == body), None)
        if player is None or self.match.collision_world is None:
            return None
        x, y, z = player.mover.position
        return self._collision().ground(x, z, y + .05, y - 1.5, player.mover.category)

    def _start_shatter(self, player):
        script = player.body_script
        if script.hero != REINHARDT or player.body in self.shatters:
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
            player, player.body, instance.id, state.counter, (origin.x, origin.y, origin.z),
            player.mover.yaw * math.tau / 65536, number("m_speed"), number("m_6A79BD98"),
            number("m_A30B383E"), number("m_525E5A40"),
            _number(instance.find_var(0, 215).value(), 0),
            _number(state.evaluate(base.get("m_amount"), watch=False), 0),
            _number(state.evaluate(close.get("m_amount"), watch=False), 0),
            (near.x, near.y, near.z),
            _number(state.evaluate(instance.state(40).node.field("m_radius"), watch=False), 0),
            loadout.guid if isinstance(loadout, expr.Asset) else None, self.now,
            tags=damage_tags(state, base), near_tags=damage_tags(state, close),
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
        if script is None or script.hero != REINHARDT:
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

    def charge_drive(self, player, command, seconds):
        """Reconstructed Charge drive, scoped to 007D's authored running state.

        16.5/60 is the graph's step length; #34534 is its 30 degree steering
        parameter. Native phase hooks remain unknown. The authored graph owns
        windup, three-second expiry, interrupts and cooldown, not this service.
        """
        script = player.body_script
        instance = next((i for i in script.component.instances.values() if i.graph.index == 0x7D), None
                        ) if script is not None and script.hero == REINHARDT else None
        state = instance.states.get(3) if instance is not None else None
        if (state is None or not state.active or self.is_dead(player) or self.controlled(player.body)
                or player.gone or not player.spawned or player.client is None):
            self._release_charge(player.body)
            return None
        contact = self.charge_contacts.get(player.body)
        if contact is not None and contact.pinned is not None and not self.match.game_map.free_for_all:
            captured = self._target_entity(contact.pinned)
            if captured is not None and captured.team == player.team:
                self.charge_blocked(player, impact=False)
                return None
        wanted = command.yaw * 2 * math.pi / 65536
        counter, heading = self.charge_drives.get(player.body, (state.counter, wanted))
        if counter != state.counter:
            heading = wanted
        delta = (wanted - heading + math.pi) % (2 * math.pi) - math.pi
        limit = math.radians(_number(instance.find_var(0, 34534).value(), 30)) * seconds
        heading += max(-limit, min(limit, delta))
        self.charge_drives[player.body] = (state.counter, heading)
        if contact is None or contact.activation != state.counter:
            self._release_charge(player.body)
            self.charge_drives[player.body] = (state.counter, heading)
            self.charge_contacts[player.body] = ChargeContact(state.counter)
        speed = _number(instance.find_var(0, 7726).value(), 0) * 60
        if speed <= 0:
            return None
        return math.sin(heading) * speed, math.cos(heading) * speed

    def charge_blocked(self, player, *, impact=True, frame=None):
        """Feed a static obstacle back through the graph's ordinary end phase."""
        script = player.body_script
        if script is None:
            return
        instance = next((i for i in script.component.instances.values() if i.graph.index == 0x7D), None)
        phase = instance.states.get(40) if instance is not None else None
        if (instance is None
                or (player.body not in self.charge_drives and (phase is None or not phase.active))):
            return
        contact = self.charge_contacts.get(player.body)
        target = self._target_entity(contact.pinned) if contact is not None else None
        if impact and target is not None:
            self._charge_damage(player, target, instance, 12, self.match.tick if frame is None else frame)
        instance.write((0, 59), 0)
        self._drain_charge_end(instance)
        self._release_charge(player.body)

    @staticmethod
    def _drain_charge_end(instance):
        """Drain 007D/1408 phase exit, including begins queued by aborted windups."""
        instance.component.run_now()
        drive = instance.states.get(3)
        phase = instance.states.get(1)
        running_case = "m_05706D1C[1]"
        if drive is not None and drive.active and phase is not None and phase.case != running_case:
            instance.exit_subgraph(phase.node, running_case)
            instance.component.run_now()

    def controlled(self, entity, *, tick=None):
        """Authoritative reconstructed hard control; independent sources expire separately."""
        return self.control_deadline(entity) > (self.tick if tick is None else tick)

    def control_deadline(self, entity):
        deadlines = [self.stuns.get(entity, 0)]
        sleep = self.sleeps.get(entity)
        if sleep is not None:
            deadlines.append(sleep.end)
        deadlines.extend(self.tick + math.ceil((end - self.now) / TICK - 1e-9)
                         for (victim, _, _), end in self.controls.items() if victim == entity)
        return max(deadlines)

    def pinned(self, entity, *, tick=None):
        clock = self.now + ((self.tick if tick is None else tick) - self.tick) * TICK
        return any(victim == entity and kind == "pin" and until > clock
                   for (victim, _, kind), until in self.controls.items())

    def knockback(self, target, velocity, frame):
        """Explicit server impulse policy, with authored receiver scalar and real collision."""
        if (self.vitals_of(target).dead or self.pinned(target.entity) or self.hooks.pulling(target.entity)
                or defenses.control_immune(target.owner) or not any(velocity)):
            return (0.0, 0.0, 0.0)
        owner = target.owner
        fresh = False
        if isinstance(owner, bots.Bot):
            mover = self.knockbacks.get(target.entity)
            if mover is None:
                fresh = True
                mover = Mover(target.feet, world=self._collision(), data=mover_data(owner.body))
                self.knockbacks[target.entity] = mover
            script = owner.body_script
            if script is not None:
                mover.track_mods(statescript_mods(script.component), frame - 1)
        else:
            mover = owner.mover
        applied = mover.add_impulse(velocity, frame)
        if fresh and not any(applied):
            self.knockbacks.pop(target.entity, None)
        return applied

    def radial_knockback(self, target, origin, horizontal, vertical, frame):
        dx, _, dz = _sub(target.feet, origin)
        distance = math.hypot(dx, dz)
        x, z = (dx / distance, dz / distance) if distance else (0.0, 0.0)
        return self.knockback(target, (x * horizontal, vertical, z * horizontal), frame)

    def step_npc_knockback(self, bot, tick, seconds):
        mover = self.knockbacks.get(bot.entity)
        if mover is None:
            return False
        if bot.dead or self.pinned(bot.entity, tick=tick):
            self.knockbacks.pop(bot.entity, None)
            return False
        mover.use(world=self._collision())
        if tuple(mover.position) != tuple(bot.position):
            # A higher-priority pull can move the body while this impulse waits.
            mover.position[:] = bot.position
            mover.body = None
        if bot.body_script is not None:
            mover.track_mods(statescript_mods(bot.body_script.component), tick - 1)
        mover.step(Command(tick, yaw=round(bot.yaw)), seconds, tick)
        bot.position, bot.velocity = tuple(mover.position), mover.velocity
        bot.last_tick = bot.moved_tick = tick
        bot.pace, bot.held = 0.0, (0, 0)
        if not mover.airborne and math.hypot(*mover.velocity) < .01:
            self.knockbacks.pop(bot.entity, None)
        return True

    def control(self, target, source, kind, seconds):
        if self.vitals_of(target).dead or seconds <= 0 or defenses.control_immune(target.owner):
            return
        self.controls[target.entity, source, kind] = self.now + seconds
        if kind == "pin":
            self.knockbacks.pop(target.entity, None)
            self.halt.drop_pull(target.entity)
        self._interrupt_control(target)

    def _interrupt_control(self, target):
        self.hooks.cancel_owner(target.entity)
        self.punches.cancel(target.entity)
        owner = target.owner
        script = getattr(owner, "body_script", None)
        if script is not None:
            self.superchargers.interrupt_cast(script)
            if isinstance(owner, bots.Bot):
                owner.attack_input = False
                assassin.bind(script.component.world, script.component, None)
                if owner.attack_action == "charge":
                    heavy_charge.finish(self, owner, heavy_charge.instance_of(owner))
            script.component.edges([(value, False) for value in tuple(script.component.held)])
            # Interrupt existing actions as well as denying new input. Native
            # target-control status graphs/animation replication remain separate.
            for instance in list(script.component.instances.values()):
                for state in list(instance.states.values()):
                    if isinstance(state, nodes.Ability):
                        state.presses.clear()
                        state.stop()
                if instance.graph.index == hook.GRAPH:
                    # Ending Chain Hook's windup also queues its normal launch branches.
                    launches = [state for state in instance.states.values() if state.node.pos in hook.LAUNCH_NODES]
                    instance.drop_begins(launches)
            script.component.shots.clear()
            script.component.run_now()
        if not isinstance(owner, bots.Bot):
            barrier = self._barrier_instance(owner)
            if barrier is not None:
                self._lower_barrier(barrier)
            self.charge_blocked(owner, impact=False)
            self.winston_jump.cancel(owner.body)
            self.winston_primal.interrupt(owner)

    def _target_entity(self, entity):
        return next((t for t in self._targets() if t.entity == entity and not self.vitals_of(t).dead), None)

    def _release_charge(self, entity):
        self.charge_drives.pop(entity, None)
        self.charge_contacts.pop(entity, None)
        self.controls = {key: end for key, end in self.controls.items()
                         if not (key[1] == entity and key[2] == "pin")}

    def _end_charge_on_tracker_hit(self, source, tracker, entity):
        instance = tracker.instance
        output = expr.lvalue(tracker.node.field("m_8A2905A0"))
        instance.write(output, (expr.Entity(entity),))
        self._drain_charge_end(instance)
        instance.write(output, ())
        self._release_charge(source)

    def _charge_deployable_impact(self, shooter, tracker, contact, start, end, forward, ice_blocker):
        if tracker is None or not tracker.active:
            return False
        device = self.superchargers.charge_contact(shooter, tracker, start, end, forward)
        if device is None:
            turret = self.torbjorn_turrets.charge_contact(shooter, tracker, start, end, forward)
            if turret is not None:
                self._end_charge_on_tracker_hit(_source_entity(shooter), tracker, turret.body)
                return True
        if (device is None and contact.pinned is None and ice_blocker is not None
                and self.walls.hostile(ice_blocker, shooter)):
            # Each 0932 pillar runs 0636, which sets the tracker's 961 flag.
            device = ice_blocker
        if device is None:
            return False
        self._end_charge_on_tracker_hit(_source_entity(shooter), tracker, device.entity)
        return True

    def charge_impact_damage(self, state, elapsed_ms):
        if (state.instance.graph.index, state.node.pos) not in ((0x007D, 125), (0x1408, 102)):
            return NotImplemented
        if elapsed_ms:
            return True
        component = state.instance.component
        source = self._target_entity(component.entity)
        targets = state.evaluate(state.node.field("m_FA3ECC43"), watch=False)
        if (source is None or source.owner.body_script is None
                or source.owner.body_script.component is not component or not isinstance(targets, tuple)):
            return True
        shooter = source.owner
        amount = expr.to_float(state.evaluate(state.node.field("m_modifyHealth.m_amount"), watch=False))
        if amount <= 0:
            return True
        amount *= self.superchargers.scale(shooter)
        for target in targets:
            piece = self.walls.pieces.get(expr.to_entity(target))
            if piece is not None:
                self.walls.damage(piece, shooter, amount)
        for device in tuple(self.superchargers.opponents(shooter, shooter.team)):
            if expr.Entity(device.entity) in targets:
                self.superchargers.damage(device, amount)
        for turret in tuple(self.torbjorn_turrets.enemies(shooter)):
            if expr.Entity(turret.body) in targets:
                self.torbjorn_turrets.damage(turret, amount)
        return True

    def _release_captured(self, entity):
        for contact in self.charge_contacts.values():
            if contact.pinned == entity:
                contact.pinned = None
        self.controls = {key: end for key, end in self.controls.items()
                         if not (key[0] == entity and key[2] == "pin")}

    def _sync_charge(self, player):
        script = player.body_script
        running = next((i.states.get(3) for i in script.component.instances.values()
                        if i.graph.index == 0x7D), None) if script is not None else None
        if (running is None or not running.active or self.is_dead(player)
                or player.gone or not player.spawned or player.client is None):
            contact = self.charge_contacts.get(player.body)
            target = self._target_entity(contact.pinned) if contact is not None else None
            heading = self.charge_drives.get(player.body, (0, 0.0))[1]
            release = (target is not None and running is not None and not running.active
                       and not self.is_dead(player) and not self.controlled(player.body)
                       and player.spawned and player.client is not None and not player.gone)
            self._release_charge(player.body)
            if release:
                instance = next(i for i in script.component.instances.values() if i.graph.index == 0x7D)
                self._charge_knockback(player, target, instance, 49, script.frame, heading)

    def _charge_damage(self, player, target, instance, state_index, frame):
        state = instance.state(state_index)
        modify = state.node.field("m_modifyHealth") or {}
        amount = _number(state.evaluate(modify.get("m_amount"), watch=False), 0)
        loadout = state.evaluate(modify.get("m_loadout"), watch=False)
        self._damage(player, target, amount, False,
                     loadout.guid if isinstance(loadout, expr.Asset) else None, frame,
                     tags=damage_tags(state, modify))

    def _charge_knockback(self, player, target, instance, index, frame, heading):
        """Resource strengths as velocity deltas; native force conversion remains unverified."""
        state = instance.state(index)
        modify = state.node.field("m_modifyHealth") or {}
        horizontal = _number(state.evaluate(modify.get("m_E33EFEE1"), watch=False), 0)
        vertical = _number(state.evaluate(modify.get("m_583C208B"), watch=False), 0)
        behind = 5.0 if index == 15 else 2.0
        origin = (player.mover.position[0] - math.sin(heading) * behind,
                  player.mover.position[2] - math.cos(heading) * behind)
        dx, dz = target.feet[0] - origin[0], target.feet[2] - origin[1]
        length = math.hypot(dx, dz)
        if length > 0:
            self.knockback(target, (horizontal * dx / length, vertical, horizontal * dz / length), frame)

    @staticmethod
    def _counter_duration(script, *, heavy=False):
        """The body's inline224E counter-knockdown profile, not its ordinary hit stun."""
        for instance in script.component.instances.values():
            if heavy and instance.graph.index != heavy_charge.GRAPH:
                continue
            for node in instance.graph.nodes:
                if node is None or node.cls != "STUStatescriptActionPlayScript":
                    continue
                inline = (node.field("m_69F4B52C") or {}).get("m_6525E98B") or {}
                if expr.guid_of(inline.get("m_graph")) != 0x058000000000224E:
                    continue
                rows = {expr.guid_of(r.get("m_0D09D2D9")): r.get("m_value")
                        for r in inline.get("m_1EB5A024") or () if isinstance(r, dict)}
                kind = instance.evaluate(rows.get(0x0D80000000006BD6))
                if not isinstance(kind, expr.Asset) or kind.guid != 0x0D80000000006BBE:
                    continue
                if heavy and node.pos != 132:
                    continue
                if not heavy and not expr.truthy(instance.evaluate(rows.get(0x0D80000000007312))):
                    continue
                return _number(instance.evaluate(rows.get(0x0D800000000000D7)), 0)
        return 0.0

    @staticmethod
    def _bob_counter_duration(resource):
        # 1537/105 chooses 104 only on the ordinary dual-knockdown branch.
        action = resource.node(104) if resource is not None else None
        inline = ((action.field("m_69F4B52C") or {}).get("m_6525E98B") or {}) if action else {}
        if expr.guid_of(inline.get("m_graph")) != 0x058000000000224E:
            return 0.0
        rows = {expr.guid_of(row.get("m_0D09D2D9")): row.get("m_value")
                for row in inline.get("m_1EB5A024") or ()}
        context = expr.Context()
        kind = expr.evaluate(rows.get(0x0D80000000006BD6), context)
        if not isinstance(kind, expr.Asset) or kind.guid != 0x0D80000000006BBE:
            return 0.0
        return _number(expr.evaluate(rows.get(0x0D800000000000D7), context), 0)

    def _charge_counter(self, actor):
        """Resolve reconstructed counter-charge before a pin or an uppercut.

        Trackers 007D/155, 0D06/14, 0EE3/118, 1408 and 1537/102 supply the geometry.
        The B.O.B. host owns the charge phase represented by 1537/106.
        """
        script = actor.body_script
        source = _source_entity(actor)
        if script is None or self.controlled(source):
            return False
        companions = getattr(self, "companions", None)
        bob = companions.actor_for(source) if companions is not None else None
        heavy = isinstance(actor, bots.Bot) and bob is None
        bash = script.hero == brigitte_barrier.HERO and not isinstance(actor, bots.Bot)
        punch = script.hero == doomfist_punch.HERO and not isinstance(actor, bots.Bot)
        aim_tracker = bash or punch
        if bob is not None:
            if bob.phase != "charge" or not bob.born <= self.tick < bob.charge_until:
                return False
            resource = graph.graph(0x1537)
            query = resource.node(102) if resource is not None else None
            if query is None:
                return False
            context = expr.Context()
            local, radius, cone = (expr.evaluate(query.field(key), context)
                                   for key in ("m_05BE62DD", "m_radius", "m_A30B383E"))
            yaw = bob.charge_yaw
            x, y, z = actor.position
        elif heavy:
            tracker = heavy_charge.counter_tracker(actor)
            yaw = self.charge_drives.get(source, (0, actor.yaw))[1]
            x, y, z = actor.position
        elif script.hero == REINHARDT:
            instance = next((i for i in script.component.instances.values() if i.graph.index == 0x7D), None)
            phase = instance.states.get(40) if instance is not None else None
            tracker = instance.states.get(74) if phase is not None and phase.active else None
            yaw = actor.mover.yaw
            x, y, z = actor.mover.position
        elif bash:
            shield = self.brigitte_barriers.shields.get(source)
            tracker = shield.instance.states.get(75) if shield is not None else None
            yaw = actor.mover.yaw
            x, y, z = actor.mover.position
        elif punch:
            instance = self.punches.instance(actor)
            tracker = instance.states.get(6) if instance is not None else None
            yaw = actor.mover.yaw
            x, y, z = actor.mover.position
        else:
            return False
        if bob is None:
            if tracker is None or not tracker.active:
                return False
            local, radius, cone = (tracker.evaluate(tracker.node.field(key), watch=False)
                                   for key in ("m_05BE62DD", "m_radius", "m_A30B383E"))
        own = self._target_entity(source)
        if own is None:
            return False
        if punch and not isinstance(local, expr.Vec3):
            local = expr.Vec3(0, self._eye(actor)[1] - y - 0.18, 0)
        direction = aim(yaw, actor.mover.pitch) if aim_tracker else None
        yaw *= math.tau / 65536
        forward = (math.sin(yaw), math.cos(yaw))
        radius = _number(radius, 0)
        half_angle = math.radians(_number(cone, 0)) / 2
        if not isinstance(local, expr.Vec3) or radius <= 0:
            return False
        centre = (x + math.cos(yaw) * local.x + forward[0] * local.z,
                  y + local.y, z - math.sin(yaw) * local.x + forward[1] * local.z)
        targets = self._enemies(actor)
        if aim_tracker:
            targets = sorted(targets, key=lambda target: (segment_distance(centre, *target.segment()), target.entity))
        for target in targets:
            other = target.owner
            other_script = getattr(other, "body_script", None)
            captured = self.charge_contacts.get(source)
            if captured is not None and captured.pinned == target.entity:
                continue
            if other_script is None:
                continue
            other_bob = companions.actor_for(target.entity) if companions is not None else None
            other_heavy = isinstance(other, bots.Bot) and other_bob is None
            if other_heavy and (heavy or bob is not None or heavy_charge.instance_of(other) is None):
                continue
            if (heavy or bob is not None) and (other_bob is not None or other_script.hero != REINHARDT):
                continue
            if aim_tracker and isinstance(other, bots.Bot):
                continue
            impact = not (heavy or aim_tracker) and any(
                expr.truthy(slot.value())
                for key in (9161, 6601)
                if (slot := other_script.component.entity_var(key, create=False)) is not None
            )
            flag = other_script.component.entity_var(596, create=False)
            charging = (other_bob.phase == "charge" and other_bob.born <= self.tick < other_bob.charge_until
                        if other_bob is not None else flag is not None and expr.truthy(flag.value()))
            if not impact and (self.controlled(target.entity) or not charging):
                continue
            if other_bob is not None:
                other_yaw = other_bob.charge_yaw
            elif other_heavy:
                other_yaw = self.charge_drives.get(target.entity, (0, other.yaw))[1]
            else:
                other_yaw = other.mover.yaw
            facing = other_yaw * math.tau / 65536
            facing_dot = forward[0] * math.sin(facing) + forward[1] * math.cos(facing)
            # The authored facing expression uses the VM's tolerant less-than comparison.
            if not impact and facing_dot >= -expr.EPSILON:
                continue
            a, b = target.segment()
            if segment_distance(centre, a, b) > radius + target.radius:
                continue
            if aim_tracker:
                if not self._aim_contact_visible(actor, centre, direction, half_angle, target, a, b, solid_cryo=bash):
                    continue
            else:
                dx, dz = target.feet[0] - centre[0], target.feet[2] - centre[2]
                distance = math.hypot(dx, dz)
                if distance > 0:
                    cosine = (dx * forward[0] + dz * forward[1]) / distance
                    padding = math.asin(min(1.0, target.radius / distance))
                    if math.acos(max(-1.0, min(1.0, cosine))) > half_angle + padding:
                        continue
                if (self.walls.blocked(centre, a)
                        or not capsule_visible(self._collision(), centre, a, b, target.radius)):
                    continue
            if impact:
                if bob is not None:
                    companions._stop_charge(bob, self.tick, "charge_impact")
                    return True
                instance.write((expr.INSTANCE, 559), True)
                instance.write((expr.INSTANCE, 11401), True)
                script.component.run_now()
                self.charge_blocked(actor)
                return True
            duration = (self._bob_counter_duration(resource) if bob is not None
                        else self._counter_duration(script, heavy=heavy))
            other_duration = (self._bob_counter_duration(graph.graph(0x1537)) if other_bob is not None
                              else self._counter_duration(other_script, heavy=other_heavy))
            if duration <= 0 or other_duration <= 0:
                return False
            companion = bob if bob is not None else other_bob
            if companion is not None:
                companions._stop_charge(companion, self.tick, "opposing_charge")
            self.control(own, target.entity, "charge_knockdown", duration)
            self.control(target, source, "charge_knockdown", other_duration)
            return True
        return False

    def _aim_contact_visible(self, actor, centre, direction, half_angle, target, a, b, *, solid_cryo=False):
        # These trackers test aim against the nearest point on the body axis.
        point = (a[0], max(a[1], min(b[1], centre[1])), a[2])
        delta = _sub(point, centre)
        distance = math.sqrt(_dot(delta, delta))
        if distance <= 1e-9:
            return True
        toward = tuple(component / distance for component in delta)
        if _dot(direction, toward) < math.cos(half_angle):
            return False
        limit = distance - target.radius
        if limit <= 0:
            return True
        wall = ray_world(self._collision(), centre, toward, limit)
        return (wall is None and self.walls.cast(centre, toward, limit) is None
                and self._barrier_contact(actor, centre, toward, limit) is None
                and (not solid_cryo or self.brigitte_barriers.cryo_contact(centre, toward, limit) is None))

    def charge_contacts_step(self, player, command, start, drive, seconds, *, ice_blocker=None):
        """Swept capsule acquisition and collision-backed captured-target transport.

        The original queries supply broad radius and pin half-width. Their
        native filters/cones are not implemented: use forward, enemy, alive,
        capsule overlap and static line of sight as explicit server policy.
        """
        if self._charge_counter(player):
            return
        contact = self.charge_contacts.get(player.body)
        if contact is None:
            return
        script = player.body_script
        instance = next(i for i in script.component.instances.values() if i.graph.index == 0x7D)
        speed = math.hypot(*drive)
        forward = (drive[0] / speed, 0.0, drive[1] / speed)
        radius = _number(instance.find_var(0, 9149).value(), 0)
        width = _number(instance.find_var(0, 11861).value(), 0)
        feet = tuple(player.mover.position)
        tracker = instance.states.get(56)
        if self._charge_deployable_impact(player, tracker, contact, start, feet, forward, ice_blocker):
            return
        a = (start[0], start[1] + 1, start[2])
        b = (feet[0], feet[1] + 1, feet[2])
        tip = _along(b, forward, radius)
        candidates = sorted(self._enemies(player), key=lambda t: (math.dist(feet, t.feet), t.entity))
        for target in candidates:
            key = (target.entity, self.replacement_ticks.get(target.entity))
            if key in contact.hit:
                continue
            relative = tuple(target.feet[k] - start[k] for k in range(3))
            if sum(relative[k] * forward[k] for k in range(3)) < 0:
                continue
            c, d = target.segment()
            if segment_pair_distance(a, b, c, d) > radius + target.radius:
                continue
            centre = (target.feet[0], target.feet[1] + 1, target.feet[2])
            vector = tuple(centre[k] - a[k] for k in range(3))
            length = math.hypot(*vector)
            if self.walls.blocked(a, centre):
                continue
            if (length > 0 and ray_world(self._collision(), a,
                                         tuple(v / length for v in vector), length) is not None):
                continue
            contact.hit.add(key)
            captured = any(other.pinned == target.entity for other in self.charge_contacts.values())
            if (contact.pinned is None and not captured and not defenses.control_immune(target.owner)
                    and segment_pair_distance(a, tip, c, d) <= width + target.radius):
                contact.pinned = target.entity
                self.control(target, player.body, "pin", 3.2)
            else:
                self._charge_damage(player, target, instance, 15, command.frame)
                self._charge_knockback(player, target, instance, 15, command.frame,
                                       self.charge_drives[player.body][1])
        target = self._target_entity(contact.pinned)
        if target is None:
            self._release_captured(contact.pinned)
            return
        # Keep capsules separated while transporting through the actual static
        # collision engine. A blocked target ends the charge even before the
        # attacker's capsule reaches the wall.
        distance = player.mover.data.stand_radius + target.radius + .02
        destination = _along(feet, forward, distance)
        owner = target.owner
        mover = (Mover(target.feet, world=self._collision(), data=mover_data(owner.body))
                 if isinstance(owner, bots.Bot) else owner.mover)
        previous = tuple(mover.position)
        mover._move_to(seconds, destination, math.dist(previous, destination) / seconds + 1)
        if isinstance(owner, bots.Bot):
            after, _, _ = self.walls.constrain(previous, mover.position, target.radius, target.height)
            mover.position = list(after)
            owner.position = tuple(mover.position)
            owner.velocity = mover.velocity
            owner.moved_tick = self.match.tick
        else:
            self.walls.move_player(owner, previous)
            if mover.history and mover.history[-1].frame == command.frame:
                mover.history[-1] = mover.snapshot(command.frame)
        if math.dist(mover.position, destination) > .03:
            self.charge_blocked(player, frame=command.frame)

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
            if amount <= 0:
                continue
            origin = self._eye(player)
            centre = _along(origin, aim(*command.aim()), reach)
            volumes = ((centre, centre, radius),)
            self._melee_barriers(player, volumes, amount)
            view_tick = command.frame - command.view_delay(player.quanta) * 1e6 / player.quanta
            for target in self._enemies(player):
                shown = self.seen(target, view_tick)
                if shown is None:
                    continue
                a, b = shown.segment()
                point = (a[0], max(a[1], min(b[1], origin[1])), a[2])
                if (segment_distance(centre, a, b) - shown.radius <= radius
                        and not self.walls.blocked(origin, point)
                        and capsule_visible(self._collision(), origin, a, b, shown.radius)):
                    self._damage(player, target, amount, False, loadout, command.frame,
                                 tags=damage_tags(state, modify))
            # Body visibility is resolved before this swing can remove a pillar.
            self.walls.swing(player, volumes, amount, set())

    def _hammer(self, player, command, script) -> None:
        """Reconstructed 0048 target search; authored states own swing timing and damage.

        Endpoints/radii are evaluated from the two native queries per swing. Local
        query space is mapped through command aim about the feet. Exact native
        sweep/filter/knockback behavior and defense services remain unaccepted.
        """
        if script.hero != REINHARDT or command is None:
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
        records = self.hammer_hits.setdefault(script.entity, {})
        for instance in script.component.instances.values():
            if instance.graph.index != HAMMER:
                continue
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
                for owner in self._melee_barriers(player, volumes, amount, hit):
                    hit.add(("barrier", owner.body))
                self.superchargers.swing(player, volumes, amount, hit,
                                         lambda p: not self.walls.blocked(eye, p)
                                         and capsule_visible(self._collision(), eye, p, p, 0))
                self.torbjorn_turrets.swing(player, volumes, amount, hit, eye)
                for target in self._enemies(player):
                    victim_key = ("body", (target.entity, self.replacement_ticks.get(target.entity)))
                    if victim_key in hit:
                        continue
                    shown = self.seen(target, view_tick)
                    if shown is None:
                        continue
                    c, d = shown.segment()
                    if (any(segment_pair_distance(a, b, c, d) <= radius + shown.radius
                            for a, b, radius in volumes)
                            and not self.walls.blocked(eye, c)
                            and capsule_visible(self._collision(), eye, c, d, shown.radius)):
                        hit.add(victim_key)
                        incoming = _sub(shown.feet, feet)
                        distance = math.hypot(*incoming)
                        direction = tuple(axis / distance for axis in incoming) if distance else forward
                        if deflection.frontal(target, direction) is not None:
                            continue
                        dealt = self._damage(player, target, amount, False, loadout, command.frame,
                                             tags=damage_tags(state, modify))
                        if dealt and not defenses.control_immune(target.owner):
                            self._hammer_knockback(state, modify, target, command.frame)
                self.walls.swing(player, volumes, amount, hit)

    def _hammer_knockback(self, state, modify, target, frame):
        # Scoped 0048 reconstruction. The ring value supplies a planar velocity
        # delta because the authored base is zero. Native conversion remains open.
        rings = modify.get("m_knockbackRings") or ()
        origin = state.evaluate(modify.get("m_724CBA57"), watch=False)
        if not rings or not isinstance(origin, expr.Vec3):
            return
        point = (origin.x, origin.y, origin.z)
        ring = rings[0]
        radius = _number(state.evaluate(ring.get("m_radius"), watch=False), 0)
        if math.dist(point, target.feet) <= radius:
            speed = _number(state.evaluate(ring.get("m_9102DA3F"), watch=False), 0)
            self.radial_knockback(target, point, speed, 0.0, frame)

    def update(self, now: float, tick: int, frames: bool = True) -> None:
        """Each tick: keep where the bodies are, fly the rockets, respawn, send health that changed. The
        other players' body frames only on the ticks that send (`frames`)."""
        if self.match.collision_world is None:
            return
        retained = max(self.tick + 1, tick - COMBAT_CATCH_UP + 1)
        first = tick
        if self.tick and tick > self.tick + 1 and (self.hooks.flights or self.gravitons.fields
                or self.torbjorn_turrets.devices or any(
            bot.firing and not bot.dead and isinstance(bot.body_script, BotScript) for bot in self.match.bots
        )):
            first = retained
        for target in self._targets():
            history = self.history.setdefault(target.entity, deque(maxlen=HISTORY))
            history.append((tick, target.feet, target.height, target.eye))
        # Replay the retained combat frames using the most recent poses. Player input
        # accepted just before the scheduler caught up joins only at the endpoint.
        for at in range(first, tick + 1):
            self.now, self.tick = now - (tick - at) * TICK, at
            self.controls = {key: end for key, end in self.controls.items() if end > self.now}
            mei_frost.expire(self)
            self.walls.update(at)
            self.blizzard.update(at)
            self.gravitons.update(at)
            self.winston_bubbles.update(at)
            self.orisa_barriers.update(at)
            self.sigma_barriers.update(at, TICK)
            self.superchargers.update(at)
            self.torbjorn_turrets.update(at)
            self.halt.update(at)
            for player in self.match.players:
                self._sync_barrier(player)
                self._sync_charge(player)
                self.winston_primal.update(player)
            self.hooks.update(at)
            for player in self.match.players:
                vitals = self.vitals.get(player.body)
                if vitals is not None and vitals.decay_temporary_shields(self.now):
                    self._sync_body_health(player, vitals)
            for rocket in list(self.rockets):
                if at == tick or rocket.last_tick is not None:
                    self._fly(rocket)
            for wave in list(self.shatters.values()):
                if wave.shooter.body != wave.body or earthshatter.advance(self, wave):
                    self._finish_shatter(wave)
            self._bot_weapons(at)
        self.projectiles.update(tick, first_tick=retained)
        for vitals in self.vitals.values():
            for windows in (vitals.healing_block, vitals.healing_boost):
                if windows and windows[0][1] <= self.now:
                    windows[:] = [window for window in windows if window[1] > self.now]
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
        for entity, bubble in tuple(self.zarya_bubbles.items()):
            if entity not in current or bubble.until <= self.now:
                del self.zarya_bubbles[entity]
        for entity in [key for key, deadline in self.stuns.items() if deadline <= tick]:
            del self.stuns[entity]
        for entity in [key for key, sleep in self.sleeps.items() if sleep.end <= tick or key not in current]:
            del self.sleeps[entity]
        for contact in list(self.charge_contacts.values()):
            if contact.pinned is not None and contact.pinned not in current:
                self._release_captured(contact.pinned)
        self.controls = {key: end for key, end in self.controls.items() if key[0] in current}
        for table in (self.history, self.vitals, self.replacement_ticks, self.knockbacks, self.stuns):
            for entity in [key for key in table if key not in current]:
                del table[entity]

    # --- targets -----------------------------------------------------------------------------------

    @staticmethod
    def _barrier_instance(player):
        script = player.body_script
        if script is None or script.hero != REINHARDT:
            return None
        return next((i for i in script.component.instances.values() if i.graph.index == 0x95), None)

    def _barrier_activation(self, owner):
        # A replacement body can restart the same shield state counter.
        shield = self.brigitte_barriers.shields.get(owner.body)
        if shield is not None:
            return self.replacement_ticks.get(owner.body), shield.instance.states[30].counter
        instance = self._barrier_instance(owner)
        return self.replacement_ticks.get(owner.body), instance.states[17].counter

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
        if self.controlled(player.body) and pet is not None and pet.active:
            self._lower_barrier(instance)
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

    def _enemy_barriers(self, shooter, *, team=None):
        if self.barrier_geometry is None:
            return
        source = _source_entity(shooter)
        team = shooter.team if team is None else team
        for owner in self.match.players:
            if owner is shooter or owner.body == source:
                continue
            if not self.match.game_map.free_for_all and owner.team == team:
                continue
            state = self._sync_barrier(owner)
            if state is None or not state.active:
                continue
            yield owner, state

    def _barrier_contacts(self, shooter, origin, direction, length, radius=0.0, *, team=None):
        found = []
        for owner, state in self._enemy_barriers(shooter, team=team):
            distance = barriers.intersect(self.barrier_geometry, owner.mover.position,
                                          owner.mover.yaw, owner.mover.pitch,
                                          origin, direction, length, radius,
                                          owner.mover.values.stand_radius, owner.mover.values.stand_height)
            if distance is not None:
                found.append((distance, owner, state))
        found.extend(self.brigitte_barriers.contacts(
            shooter, shooter.team if team is None else team, origin, direction, length, radius,
        ))
        for dome in self.winston_bubbles.opponents(shooter, team):
            distance = dome.ray(origin, direction, radius)
            if distance is not None and distance <= length:
                found.append((distance, dome, dome))
        for actor in self.sigma_barriers.enemies(shooter, team):
            distance = self.sigma_barriers.ray(actor, origin, direction, length, radius)
            if distance is not None:
                found.append((distance, actor, actor))
        for actor in self.orisa_barriers.opponents(shooter, team):
            distance = self.orisa_barriers.geometry.cast(
                actor.position, actor.yaw, origin, direction, length, radius)
            if distance is not None:
                found.append((distance, actor, actor))
        return sorted(found, key=lambda hit: (hit[0], hit[1].body))

    def _barrier_contact(self, shooter, origin, direction, length, radius=0.0, *, team=None):
        found = self._barrier_contacts(shooter, origin, direction, length, radius, team=team)
        return found[0] if found else None

    def _melee_barriers(self, shooter, volumes, amount, seen=()):
        found = []
        amount *= self.superchargers.scale(shooter)
        eye = ((shooter.position[0], shooter.position[1] + EYE, shooter.position[2])
               if isinstance(shooter, bots.Bot) else self._eye(shooter))
        for owner, state in self._enemy_barriers(shooter):
            if ("barrier", owner.body) in seen:
                continue
            if any(barriers.overlaps(self.barrier_geometry, owner.mover.position,
                                     owner.mover.yaw, owner.mover.pitch, a, b, radius,
                                     owner.mover.values.stand_radius, owner.mover.values.stand_height,
                                     visible=lambda p: not self.walls.blocked(eye, p)
                                     and capsule_visible(self._collision(), eye, p, p, 0))
                   for a, b, radius in volumes):
                self._damage_barrier((0.0, owner, state), amount)
                found.append(owner)
        for owner, shield in self.brigitte_barriers.melee_contacts(
            shooter, volumes, seen,
            lambda p: not self.walls.blocked(eye, p) and capsule_visible(self._collision(), eye, p, p, 0),
        ):
            self.brigitte_barriers.damage(shield, amount)
            found.append(owner)
        for dome in self.winston_bubbles.opponents(shooter):
            if ("barrier", dome.body) in seen:
                continue
            if any(dome.overlaps(a, b, radius,
                                 lambda p: not self.walls.blocked(eye, p)
                                 and capsule_visible(self._collision(), eye, p, p, 0))
                   for a, b, radius in volumes):
                self.winston_bubbles.damage(dome, amount)
                found.append(dome)
        for actor in self.sigma_barriers.enemies(shooter):
            if ("barrier", actor.body) in seen:
                continue
            if any(barriers.overlaps(self.sigma_barriers.geometry, actor.position, actor.yaw, actor.pitch,
                                     a, b, radius, 0, 0, visible=lambda p: not self.walls.blocked(eye, p)
                                     and capsule_visible(self._collision(), eye, p, p, 0))
                   for a, b, radius in volumes):
                self.sigma_barriers.damage(actor, amount)
                found.append(actor)
        for actor in self.orisa_barriers.opponents(shooter):
            if ("barrier", actor.body) in seen:
                continue
            if any(self.orisa_barriers.geometry.overlaps(
                    actor.position, actor.yaw, a, b, radius,
                    lambda p: not self.walls.blocked(eye, p)
                    and capsule_visible(self._collision(), eye, p, p, 0))
                   for a, b, radius in volumes):
                self.orisa_barriers.damage(actor, amount)
                found.append(actor)
        return found

    def _damage_barrier(self, contact, amount, *, shooter=None):
        if shooter is not None:
            amount *= self.superchargers.scale(shooter)
        _, owner, state = contact
        if isinstance(state, brigitte_barrier.Shield):
            self.brigitte_barriers.damage(state, amount)
            return
        if isinstance(state, sigma_barrier.Shield):
            self.sigma_barriers.damage(state, amount)
            return
        if isinstance(state, orisa_barrier.Barrier):
            self.orisa_barriers.damage(state, amount)
            return
        if isinstance(state, winston_barrier.Bubble):
            self.winston_bubbles.damage(state, amount)
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
                hero = target.owner.hero
                found = Vitals(target.entity, *pools.body_pools(hero.guid, hero.body))
            self.vitals[target.entity] = found
        return found

    def body_health(self, entity: int, *, pool_kind: int | None = None) -> tuple[float, float] | None:
        """Read existing combat pools, independently of collision or visibility."""
        for player in self.match.players:
            if player.gone:
                continue
            if player.has_body and player.body == entity:
                break
            if player.entity == entity:
                return 0.0, 0.0
        else:
            player = None
            if entity == world.GAME_MODE_ENTITY:
                return 0.0, 0.0
            bot = next((bot for bot in self.match.bots if bot.entity == entity), None)
            if bot is None:
                return None

        vitals = self.vitals.get(entity)
        if vitals is not None:
            if pool_kind is None:
                return vitals.total, sum(vitals.most.values())
            current, maximum = vitals.now[pool_kind], vitals.most[pool_kind]
            if pool_kind == SHIELDS_POOL:
                current += vitals.grasp_shields
                maximum += vitals.grasp_max
            return current, maximum
        initial = (pools.body_pools(player.hero.guid, player.hero.body) if player is not None
                   else (bot.health, 0.0, 0.0))
        maximum = sum(initial) if pool_kind is None else initial[pool_kind]
        return maximum, maximum

    def filtered_body_health(self, entity: int, kind: int):
        if kind not in (HEALTH_POOL, ARMOUR_POOL, SHIELDS_POOL):
            return NotImplemented
        amounts = self.body_health(entity, pool_kind=kind)
        return None if amounts is None else amounts[0]

    def _enemies(self, shooter, *, team=None) -> list[Target]:
        free_for_all = self.match.game_map.free_for_all
        source = _source_entity(shooter)
        team = shooter.team if team is None else team
        found = []
        for target in self._targets():
            if target.owner is shooter or target.entity == source:
                continue
            if not free_for_all and target.team == team:
                continue
            if defenses.phased(target.owner) or self.vitals_of(target).dead:
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
        """Advance active NPC bodies and consume authorized volley reports."""
        from ow174.game import npc_control

        for bot in self.match.bots:
            script = bot.body_script
            controlled = npc_control.manual(bot)
            combat_active = bot.firing or controlled or bot.entity in self.halt.pulls
            if ((not combat_active and not bot.route) or bot.dead
                    or not isinstance(script, BotScript) or script.frame > tick):
                continue
            try:
                allowed = npc_control.update_input(self, bot) if controlled else (
                    combat_active and not self.is_stunned(bot)
                )
                snapshot = script.advance_live(tick, bot.position)
                if (controlled and bot.attack_action == "charge" and bot.entity not in self.gravitons.pulled
                        and not self.halt.moving(bot.entity)):
                    heavy_charge.advance(self, bot, TICK)
                shots, script.component.shots[:] = list(script.component.shots), []
                if self.controlled(bot.entity):
                    shots.clear()
                for state, number, time, activation in shots:
                    if not allowed:
                        continue
                    if controlled and not npc_control.spend_sniper_round(bot, state):
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
                script.retire_assassin_controller()
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
        if not npc and self.blizzard.fired(shooter, state, shot_frame, origin, direction,
                                           activation=activation, snapshot=snapshot):
            return
        direct = hit_def(state, node.field("m_6396149F"))
        speed = field("m_projectileSpeed")
        definition = None
        if speed or npc or (state.instance.graph.index, node.pos) == deflection.SOLDIER_RIFLE_VOLLEY:
            entity_def = (node.field("m_projectileEntity") or {}).get("m_entityDef")
            if entity_def is not None:
                definition = state.evaluate(entity_def, watch=False)
        if speed:
            lifetime = field("m_projectileLifetime", 5.0) or 5.0
            reach = field("m_FEC435C6")
            if reach:
                lifetime = min(lifetime, reach / speed)
            helix = isinstance(definition, expr.Asset) and definition.guid in deflection.SOLDIER_HELIX
            blast = hit_def(state, node.field("m_D2D11CE9"), blast_knockback=helix)
            from ow174.game import flashbang_flight, npc_context

            gravity = None
            if not npc:
                direction, gravity = flashbang_flight.launch(shooter, state, direction)
            graviton_shot = isinstance(definition, expr.Asset) and definition.guid == graviton.DEFINITION
            if graviton_shot:
                motion = node.field("m_projectileMotions")[0]
                gravity = _number(state.evaluate(motion["m_599AB8D7"], watch=False), 0)
            deployment = (not npc and self.orisa_barriers.geometry is not None
                          and state.instance.graph.index == orisa_barrier.GRAPH
                          and orisa_barrier.DEFINITION in projectiles._defs(node.field("m_49CBA3BC")))
            supercharger = False
            if (not npc and self.superchargers.geometry is not None
                    and state.instance.graph.index == orisa_supercharger.GRAPH):
                spawn = (node.field("m_49CBA3BC") or {}).get("m_entityDefinition")
                asset = state.evaluate(spawn, watch=False) if spawn is not None else None
                supercharger = isinstance(asset, expr.Asset) and asset.guid == orisa_supercharger.DEFINITION
            turret = (not npc and (state.instance.graph.index, node.pos) == torbjorn_turret.LAUNCH
                      and isinstance(definition, expr.Asset) and definition.guid == torbjorn_turret.CARRIER
                      and torbjorn_turret.DEVICE in projectiles._defs(node.field("m_49CBA3BC")))
            if deployment or supercharger or turret:
                gravity = sum(_number(state.evaluate(motion.get("m_599AB8D7"), watch=False), 0.0)
                              for motion in node.field("m_projectileMotions") or ()
                              if motion.get("$") == "STU_E1A5B579"
                              and not expr.truthy(state.evaluate(motion.get("m_D0C4CF49"), watch=False)))
            directions = npc_context.projectile_directions(shooter, state, direction) if npc else (direction,)
            for ray in directions:
                shot = None
                if isinstance(definition, expr.Asset):
                    shot = self.projectiles.fired(
                        shooter, state, shot_frame, definition.guid, lifetime, origin, ray,
                        activation=activation, snapshot=snapshot,
                    )
                damaging = (direct is not None and direct.amount > 0.0) or (
                    blast is not None and blast.amount > 0.0
                )
                halt_cast = not npc and state.instance.graph.index == halt.HALT_GRAPH
                if damaging or shot is not None or deployment or halt_cast or supercharger or turret:
                    fire_strike = (state.instance.component.world.hero == REINHARDT
                                   and state.instance.graph.index == 0x00E2 and state.index == 0)
                    spray = state.instance.graph.index == 0x05E6 and state.index == 13
                    grenade = state.instance.graph.index == ana_biotic.GRAPH and state.index == 0
                    radius = 0.0
                    if fire_strike or spray:
                        # Reconstructed spherical sweep from the authored direct-hit extents.
                        hit_fields = node.field("m_6396149F") or {}
                        radius = max(_number(state.evaluate(hit_fields.get(name), watch=False), 0.0)
                                     for name in ("m_57DAE1B5", "m_61CD1F5B"))
                    self.rockets.append(
                        Rocket(
                            shooter, shooter.team, origin, ray, speed, lifetime, direct, blast, shot,
                            last_tick=self.tick if npc else None, radius=radius,
                            deploy_body=shooter.body if deployment or supercharger or turret else None,
                            supercharger=supercharger,
                            turret_instance=state.instance if turret else None,
                            halt_body=shooter.body if halt_cast else None,
                            penetrating=fire_strike or spray,
                            gravity=0.0 if gravity is None else gravity,
                            velocity=None if gravity is None else tuple(expr.f32(v * speed) for v in ray),
                            detonate_on_expiry=gravity is not None
                            and not (graviton_shot or deployment or supercharger or turret),
                            charge_origin=(shooter.body, self.replacement_ticks.get(shooter.body))
                            if fire_strike else None,
                            definition=definition.guid if isinstance(definition, expr.Asset)
                            and (fire_strike or grenade or graviton_shot or halt_cast
                                 or definition.guid in (ana_sleep.PROJECTILE, zarya_weapons.SECONDARY,
                                                        *deflection.TALON_PROJECTILES,
                                                        *deflection.SOLDIER_HELIX))
                            else None,
                        )
                    )
                    if halt_cast:
                        self.halt.admit(self.rockets[-1], state)
            return
        if direct is None or direct.amount <= 0.0:
            return
        reach = field("m_FEC435C6", RANGE) or RANGE
        # The input supplies this simulation frame's aim and view time. Notices use the volley clock.
        view_tick = self.tick
        if command is not None:
            view_tick = command.frame - command.view_delay(shooter.quanta) * 1e6 / shooter.quanta
        self._fire_ray(
            shooter, origin, direction, reach, direct, shot_frame, view_tick,
            world_tick=self.tick if npc else None,
            definition=definition.guid if isinstance(definition, expr.Asset) else None,
        )

    def _fire_ray(self, shooter, origin, direction, reach, direct, shot_frame, view_tick, *,
                  world_tick, definition=None, travelled=0.0, redirections=0, instigator=None):
        targets = self._enemies(shooter)
        best = None
        for target in targets:
            shown = self.seen(target, view_tick)
            if shown is None:
                continue
            a, b = shown.segment()
            t = ray_capsule(origin, direction, a, b, shown.radius)
            if t is not None and t <= reach and (best is None or t < best[0]):
                best = (t, target, shown)
        limit = best[0] if best is not None else reach
        wall = ray_world(self._collision(), origin, direction, limit)
        if wall is not None:
            limit = min(limit, wall)
        ice = self.walls.cast(origin, direction, limit)
        if ice is not None and wall is not None and ice[0] >= wall:
            ice = None
        if ice is not None:
            limit = ice[0]
        shield = self._barrier_contact(shooter, origin, direction, limit)
        if shield is not None and ((wall is not None and shield[0] >= wall)
                                   or (ice is not None and shield[0] >= ice[0])):
            shield = None
        if shield is not None:
            limit = shield[0]
        if definition in deflection.RAYS:
            contact = deflection.projectile_contact(definition, targets, origin, direction, limit)
            unobstructed = best is None and wall is None and ice is None and shield is None
            if contact is not None and (unobstructed or contact[0] < limit):
                distance, _, target, reflect, state = contact
                if reflect and redirections < deflection.MAX_REDIRECTIONS:
                    mover = target.owner.mover
                    self._fire_ray(
                        target.owner, _along(origin, direction, distance), aim(mover.yaw, mover.pitch),
                        reach - distance, direct, shot_frame, self.tick, world_tick=self.tick,
                        definition=definition, travelled=travelled + distance,
                        redirections=redirections + 1,
                    )
                elif not reflect and state.instance.graph.index == sigma_grasp.GRAPH:
                    sigma_grasp.absorb(state, shooter, direct.amount * direct.scale(travelled + distance))
                return
        if shield is not None:
            self._damage_barrier(shield, direct.amount * direct.scale(travelled + shield[0]), shooter=shooter)
            return
        if ice is not None:
            self.walls.damage(ice[1], shooter, direct.amount * direct.scale(travelled + ice[0]))
            return
        if best is None or (wall is not None and wall < best[0]):
            return
        t, target, shown = best
        hit_y = origin[1] + direction[1] * t
        critical = hit_y >= shown.feet[1] + shown.eye - HEAD_BELOW
        amount = direct.amount * direct.scale(travelled + t) * (direct.critical if critical else 1.0)
        damaged = self._damage(
            shooter, target, amount, critical and direct.critical > 1.0, direct.loadout, shot_frame,
            world_tick=world_tick,
            tags=direct.tags,
            instigator=instigator,
        )
        if damaged:
            self._hit_effect(target, direct.effect)

    def _credits_rocket_charge(self, rocket: Rocket) -> bool:
        if rocket.charge_origin is None:
            return False
        body = rocket.shooter.body
        return rocket.charge_origin == (body, self.replacement_ticks.get(body))

    def _fly(self, rocket: Rocket) -> None:
        """Sweep elapsed world time against the current bodies and static collision."""
        # Player input can arrive immediately before the scheduler accounts for a gap.
        if not self.halt.flying(rocket):
            return
        elapsed = 1 if rocket.last_tick is None else self.tick - rocket.last_tick
        if elapsed <= 0:
            return
        rocket.last_tick = self.tick
        if rocket.velocity is not None:
            for _ in range(elapsed):
                if rocket not in self.rockets:
                    break
                self._fly_step(rocket, min(TICK, rocket.left))
            return
        self._fly_step(rocket, min(elapsed * TICK, rocket.left))

    def _fly_step(self, rocket: Rocket, duration: float, *, redirections=0) -> None:
        if duration <= 0.0:
            self._landed(rocket)
            if rocket.detonate_on_expiry:
                self._explode(rocket, rocket.position)
            return
        if rocket.velocity is not None:
            from ow174.game import flashbang_flight

            # The server applies the velocity update before sweeping this substep.
            rocket.velocity = flashbang_flight.gravity_velocity(rocket.velocity, rocket.gravity, duration)
            rocket.speed = math.sqrt(sum(value * value for value in rocket.velocity))
            if rocket.speed:
                rocket.direction = tuple(value / rocket.speed for value in rocket.velocity)
        # Sweep the remaining flight even when the range or lifetime ends inside this tick.
        step = rocket.speed * duration
        if rocket.penetrating:
            for _ in range(deflection.MAX_REDIRECTIONS):
                duration = self._fly_penetrating(rocket, duration)
                if duration <= 0:
                    return
            self._landed(rocket)
            return
        best = None
        loadout = rocket.direct.loadout if rocket.direct is not None else None
        interceptible = deflection.direct_projectile(rocket.definition, loadout)
        grenade = rocket.definition == ana_biotic.PROJECTILE
        # Preserve live allegiance for outbound Talon and Helix shots. Deflect captures its launch team.
        live_team = rocket.definition in (*deflection.TALON_PROJECTILES, *deflection.SOLDIER_HELIX)
        team = rocket.team if interceptible and (rocket.reflected or not live_team) else None
        targets = self._enemies(rocket.shooter, team=team)
        collision_targets = targets
        if grenade and not self.match.game_map.free_for_all:
            collision_targets = targets + [
                target for target in self._targets() if target.owner is not rocket.shooter
                and target.team == team and not self.vitals_of(target).dead
            ]
        for target in collision_targets:
            a, b = target.segment()
            t = ray_capsule(rocket.position, rocket.direction, a, b, target.radius)
            if t is not None and t <= step and (best is None or t < best[0]):
                best = (t, target)
        collision = self._collision()
        terrain = None
        if rocket.turret_instance is not None and collision is not None:
            terrain = collision.cast_ray(rocket.position, rocket.direction, step)
            wall = terrain.distance if terrain is not None else None
        else:
            wall = ray_world(collision, rocket.position, rocket.direction, step)
        if wall is not None and (best is None or wall < best[0]):
            best = (wall, None)
        limit = best[0] if best is not None else step
        ice = self.walls.cast(rocket.position, rocket.direction, limit, radius=rocket.radius)
        if ice is not None and (wall is None or ice[0] < wall):
            best = (ice[0], None)
        else:
            ice = None
        shield = self._barrier_contact(rocket.shooter, rocket.position, rocket.direction,
                                       best[0] if best is not None else step, rocket.radius, team=team)
        if shield is not None and (best is None or shield[0] < best[0]):
            best = (shield[0], None)
            ice = None
        else:
            shield = None
        if interceptible:
            intercepted = deflection.projectile_contact(
                rocket.definition, targets, rocket.position, rocket.direction,
                best[0] if best is not None else step, rocket.radius, loadout=loadout,
            )
            if intercepted is not None and (best is None or intercepted[0] < best[0]):
                distance, _, target, reflect, state = intercepted
                travelled = distance / rocket.speed
                rocket.position = _along(rocket.position, rocket.direction, distance)
                rocket.flown += travelled
                rocket.left = max(0.0, rocket.left - travelled)
                self._intercept_rocket(rocket, target, reflect, state)
                if reflect and rocket.left > EPSILON and redirections + 1 < deflection.MAX_REDIRECTIONS:
                    remaining = min(duration - travelled, rocket.left)
                    if remaining > EPSILON:
                        self._fly_step(rocket, remaining, redirections=redirections + 1)
                elif reflect:
                    self._landed(rocket)
                return
        if best is None:
            rocket.position = _along(rocket.position, rocket.direction, step)
            rocket.flown += duration
            rocket.left -= duration
            if rocket.left <= 0.0 and rocket.halt_entity is not None:
                self.halt.landed(rocket)
                return
            if rocket.left <= 0.0:
                self._landed(rocket)
                if rocket.detonate_on_expiry:
                    self._explode(rocket, rocket.position)
            return
        t, struck = best
        rocket.flown += t / rocket.speed
        if rocket.halt_entity is not None:
            rocket.position = _along(rocket.position, rocket.direction, t)
            self.halt.landed(rocket)
            return
        self._landed(rocket)
        point = _along(rocket.position, rocket.direction, t)
        if rocket.deploy_body is not None:
            if wall is not None and shield is None and struck is None and ice is None:
                if rocket.turret_instance is not None:
                    self.torbjorn_turrets.land(rocket, terrain)
                else:
                    rocket.position = point
                    manager = self.superchargers if rocket.supercharger else self.orisa_barriers
                    manager.landed(rocket)
            return
        blast = rocket.blast
        shield_damage = set()
        # Direct-hit distance curves apply at flight distance, including shield
        # contacts. Native curve interpolation remains the documented linear policy.
        direct_amount = (rocket.direct.amount * rocket.direct.scale(rocket.flown * rocket.speed)
                         if rocket.direct is not None else 0.0)
        if ice is not None:
            point = _along(point, rocket.direction, -contacts.SKIN)
        if shield is not None:
            amount = direct_amount
            amount += blast.amount * blast.splash(0.0) if blast is not None else 0.0
            self._damage_barrier(shield, amount, shooter=rocket.shooter)
            shield_damage.add(shield[1].body)
            # Keep the radial rays on the incoming side of the contact surface.
            point = _along(point, rocket.direction, -contacts.SKIN)
        if struck is not None and rocket.direct is not None and rocket.direct.amount > 0.0:
            critical = (rocket.direct.critical > 1.0
                        and point[1] >= struck.feet[1] + struck.eye - HEAD_BELOW)
            amount = direct_amount * rocket.direct.critical if critical else direct_amount
            damaged = self._damage(
                rocket.shooter, struck, amount, critical, rocket.direct.loadout, self.tick,
                world_tick=self.tick,
                tags=rocket.direct.tags,
                credit_charge=self._credits_rocket_charge(rocket),
            )
            if damaged:
                self._hit_effect(struck, rocket.direct.effect)
        self._explode(rocket, point, wall=wall is not None, shield=shield, shield_damage=shield_damage)
        if rocket.definition == graviton.DEFINITION:
            landing = _along(point, rocket.direction, -contacts.SKIN) if wall is not None else point
            self.gravitons.landed(rocket, landing)
        if ice is not None:
            amount = direct_amount + (blast.amount * blast.splash(0) if blast is not None else 0)
            self.walls.damage(ice[1], rocket.shooter, amount, team=rocket.team)

    def _explode(self, rocket: Rocket, point: tuple, *, wall=False, shield=None, shield_damage=None) -> None:
        blast = rocket.blast
        if blast is None or blast.amount <= 0.0:
            return
        if shield_damage is None:
            shield_damage = set()
        grenade = rocket.definition == ana_biotic.PROJECTILE
        team = (rocket.team if rocket.definition in deflection.DIRECT_PROJECTILES
                and (rocket.reflected or rocket.definition not in deflection.SOLDIER_HELIX) else None)
        targets = self._targets() if grenade else self._enemies(rocket.shooter, team=team)
        for target in targets:
            vitals = self.vitals_of(target) if grenade else None
            if vitals is not None and vitals.dead:
                continue
            a, b = target.segment()
            distance = max(0.0, segment_distance(point, a, b) - target.radius)
            scalar = blast.splash(distance)
            # Start just outside an impact wall, on the projectile's approach side.
            visible_from = _along(point, rocket.direction, -0.0001) if wall else point
            if scalar > 0.0 and capsule_visible(self._collision(), visible_from, a, b, target.radius):
                axis = _sub(b, a)
                squared = _dot(axis, axis)
                share = max(0.0, min(1.0, _dot(_sub(point, a), axis) / squared)) if squared > EPSILON else 0.0
                closest = _along(a, axis, share)
                if self.walls.blocked(visible_from, closest):
                    continue
                delta = _sub(closest, point)
                length = math.sqrt(_dot(delta, delta))
                if length > EPSILON:
                    toward = tuple(v / length for v in delta)
                    cover = self._barrier_contact(rocket.shooter, point, toward, length, team=team)
                    # A projectile that breaks a shield still ends at that shield;
                    # do not let this same impact spill through to actors behind it.
                    if shield is not None:
                        owner = shield[1]
                        blocked = barriers.intersect(
                            self.barrier_geometry, owner.mover.position, owner.mover.yaw, owner.mover.pitch,
                            point, toward, length, owner_radius=owner.mover.values.stand_radius,
                            owner_height=owner.mover.values.stand_height,
                        )
                        if blocked is not None and (cover is None or blocked < cover[0]):
                            cover = (blocked, owner, shield[2])
                    if cover is not None:
                        if cover[1].body not in shield_damage:
                            self._damage_barrier(
                                cover, blast.amount * blast.splash(cover[0]), shooter=rocket.shooter)
                            shield_damage.add(cover[1].body)
                        continue
                if grenade:
                    allied = (target.owner is rocket.shooter or
                              (not self.match.game_map.free_for_all and target.team == team))
                    if allied:
                        self._heal(rocket.shooter, target, ana_biotic.HEAL * scalar)
                        ana_biotic.refresh(vitals.healing_boost, self.now)
                        continue
                damaged = self._damage(
                    rocket.shooter, target, blast.amount * scalar, False, blast.loadout, self.tick,
                    world_tick=self.tick,
                    tags=blast.tags,
                    credit_charge=self._credits_rocket_charge(rocket),
                )
                if damaged:
                    if grenade and not vitals.dead:
                        ana_biotic.refresh(vitals.healing_block, self.now)
                    self._hit_effect(target, blast.effect)
                    for radius, horizontal, vertical in blast.knockback_rings:
                        if distance <= radius:
                            self.radial_knockback(target, point, horizontal, vertical, self.tick)
                            break

    def _fly_penetrating(self, rocket: Rocket, duration: float) -> float:
        """Reconstructed Fire Strike and spray penetration, deduplicated per body lifetime.

        Sweep the reconstructed sphere against the real static collision engine.
        Return the remaining time for the redirected flight, if any.
        """
        step = rocket.speed * duration
        end = _along(rocket.position, rocket.direction, step)
        contact = contacts.scene_sweep(self._collision().engine, rocket.position, end,
                                       ((0.0, 0.0, 0.0),), rocket.radius, MOVER)
        wall = contact[0] * step if contact is not None else None
        limit = wall if wall is not None else step
        ice = self.walls.cast(rocket.position, rocket.direction, limit, radius=rocket.radius)
        if ice is not None and (wall is None or ice[0] < wall):
            wall = limit = ice[0]
        else:
            ice = None
        targets = self._enemies(rocket.shooter, team=rocket.team)
        intercepted = deflection.projectile_contact(
            rocket.definition, targets, rocket.position, rocket.direction, limit, rocket.radius,
        )
        if intercepted is not None and (wall is None or intercepted[0] < wall):
            limit = intercepted[0]
        else:
            intercepted = None
        stopped = wall is not None or intercepted is not None
        spray = rocket.direct is not None and rocket.direct.loadout == mei_frost.SPRAY
        absorbed_by_barrier = False
        hits = []
        for contact in self._barrier_contacts(rocket.shooter, rocket.position, rocket.direction,
                                              limit, rocket.radius, team=rocket.team):
            distance, owner, _ = contact
            if isinstance(owner, (winston_barrier.Bubble, orisa_barrier.Barrier, sigma_barrier.Shield)):
                key = (-owner.body, None)
            else:
                key = (-owner.body, self._barrier_activation(owner))
            if key not in rocket.hit_targets and (not stopped or distance < limit):
                if not spray:
                    hits.append((distance, 0, owner.body, key, contact))
                    continue
                rocket.hit_targets.add(key)
                self._damage_barrier(contact, rocket.direct.amount, shooter=rocket.shooter)
                limit, stopped, absorbed_by_barrier = distance, True, True
                break
        for target in targets:
            key = (target.entity, self.replacement_ticks.get(target.entity))
            if key in rocket.hit_targets:
                continue
            a, b = target.segment()
            distance = ray_capsule(rocket.position, rocket.direction, a, b, target.radius + rocket.radius)
            if distance is not None and distance <= limit and (not stopped or distance < limit):
                hits.append((distance, 1, target.entity, key, target))
        if not spray and rocket.direct is not None:
            for distance, device in self.superchargers.projectile_contacts(rocket, limit):
                key = (device.entity, None)
                if key not in rocket.hit_targets and (not stopped or distance < limit):
                    hits.append((distance, 1, device.entity, key, device))
            for distance, device in self.torbjorn_turrets.projectile_contacts(rocket, limit):
                key = (device.body, None)
                if key not in rocket.hit_targets and (not stopped or distance < limit):
                    hits.append((distance, 1, device.body, key, device))
        available = max(0, 99 - len(rocket.hit_targets)) if spray else len(hits)
        reflected_strike = rocket.reflected and rocket.definition == deflection.FIRE_STRIKE
        for _, kind, _, key, target in sorted(hits)[:available]:
            if kind == 0:
                _, owner, state = target
                # A nearer body contact can drop a carried shield in this step.
                if isinstance(state, barriers.Barrier) and self.is_dead(owner):
                    continue
                rocket.hit_targets.add(key)
                if rocket.direct is not None:
                    self._damage_barrier(target, rocket.direct.amount, shooter=rocket.shooter)
                continue
            rocket.hit_targets.add(key)
            if isinstance(target, orisa_supercharger.Device):
                amount = rocket.direct.amount * self.superchargers.scale(rocket.shooter)
                self.superchargers.damage(target, amount)
            elif isinstance(target, torbjorn_turret.Device):
                amount = rocket.direct.amount * self.superchargers.scale(rocket.shooter)
                self.torbjorn_turrets.damage(target, amount)
            elif rocket.direct is not None:
                self._damage(rocket.shooter, target, rocket.direct.amount, False, rocket.direct.loadout,
                             self.tick, world_tick=self.tick, tags=rocket.direct.tags,
                             credit_charge=self._credits_rocket_charge(rocket),
                             reflected_strike=reflected_strike, source_team=rocket.team)
        travelled = limit / rocket.speed
        rocket.position = _along(rocket.position, rocket.direction, limit)
        rocket.flown += travelled
        rocket.left = max(0.0, rocket.left - travelled)
        if ice is not None and intercepted is None and not absorbed_by_barrier and rocket.direct is not None:
            self.walls.damage(ice[1], rocket.shooter, rocket.direct.amount, team=rocket.team)
        if intercepted is not None:
            _, _, target, reflect, state = intercepted
            if not state.active or self.vitals_of(target).dead:
                if rocket.left <= EPSILON:
                    self._landed(rocket)
                    return 0.0
                return max(0.0, duration - travelled)
            self._intercept_rocket(rocket, target, reflect, state)
            if not reflect:
                return 0.0
            if rocket.left > EPSILON:
                return max(0.0, duration - travelled)
        if wall is not None or absorbed_by_barrier or rocket.left <= EPSILON:
            self._landed(rocket)
        return 0.0

    def _intercept_rocket(self, rocket, target, reflect, state):
        if rocket.shot is not None:
            self.projectiles.intercepted(rocket.shot)
            rocket.shot = None
        if not reflect:
            self.halt.orbs.pop(rocket.halt_entity, None)
            self._landed(rocket)
            if state.instance.graph.index == sigma_grasp.GRAPH:
                explosive = rocket.definition in (
                    ana_biotic.PROJECTILE, zarya_weapons.SECONDARY, *deflection.SOLDIER_HELIX,
                )
                payload = rocket.blast if explosive else rocket.direct
                if payload is not None:
                    sigma_grasp.absorb(state, rocket.shooter, payload.amount)
            return
        rocket.shooter, rocket.team = target.owner, target.team
        rocket.reflected = True
        if rocket.halt_entity is not None:
            rocket.halt_body = target.entity
        if rocket.definition != graviton.DEFINITION:
            rocket.charge_origin = (target.entity, self.replacement_ticks.get(target.entity))
        mover = target.owner.mover
        rocket.direction = aim(mover.yaw, mover.pitch)
        if rocket.velocity is not None:
            rocket.velocity = tuple(expr.f32(axis * rocket.speed) for axis in rocket.direction)

    def _landed(self, rocket: Rocket) -> None:
        """The projectile ended (hit, range, lifetime): the predicted one's release follows."""
        if rocket in self.rockets:
            self.rockets.remove(rocket)
        if rocket.shot is not None:
            self.projectiles.flown(rocket.shot, rocket.flown)

    def heal_area(self, healer, centre, radius: float, amount: float, *, ticks: range | None = None) -> None:
        """Heal the healer and his teammates (everyone's own team in a free-for-all is his alone) whose body
        is within `radius` of `centre` (unverified: to the body's capsule).
        A field supplies retained ticks and the heal per tick."""
        for target in self._targets():
            ally = not self.match.game_map.free_for_all and target.team == healer.team
            if target.owner is not healer and not ally:
                continue
            a, b = target.segment()
            if segment_distance(centre, a, b) - target.radius > radius:
                continue
            self._heal(healer, target, amount, ticks=ticks)

    def _heal(self, healer, target, amount, *, ticks=None):
        vitals = self.vitals_of(target)
        if vitals.dead:
            return
        if ticks is None:
            amount *= ana_biotic.healing_scale(vitals, self.now)
        elif vitals.healing_block or vitals.healing_boost:
            amount *= sum(ana_biotic.healing_scale(vitals, self.now - (self.tick - at) * TICK)
                          for at in ticks)
        else:
            amount *= len(ticks)
        given = vitals.heal(amount)
        if given > 0:
            healer.stats.add(stats.HEALING, given)
            self._sync_body_health(target.owner, vitals)

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

    def _damage(
        self, shooter, target: Target, amount: float, critical: bool, loadout, frame: int,
        *, world_tick: int | None = None, tags: tuple = (), credit_charge: bool = True,
        bypass_armour: bool = False, beam: bool = False, reflected_strike: bool = False,
        source_team: int | None = None, instigator: int | None = None,
    ) -> bool:
        vitals = self.vitals_of(target)
        if vitals.dead or amount <= 0.0:
            return False
        amount *= self.superchargers.scale(shooter)
        source = _source_entity(shooter) if instigator is None else instigator
        if zarya_barrier.absorb(self, target.entity, amount, source):
            return False
        dealt = vitals.damage(amount * defenses.damage_scale(target.owner),
                              bypass_armour=bypass_armour, beam=beam)
        if dealt <= 0.0:
            return False
        vitals.last_damage_source = source
        killed = vitals.now[HEALTH_POOL] <= EPSILON
        if killed:
            self.hooks.retire(target.entity)
            self.punches.retire(target.entity)
            self.frost.pop(target.entity, None)
            self.gravitons.pulled.discard(target.entity)
            self.gravitons.npc_movers.pop(target.entity, None)
            self.sleeps.pop(target.entity, None)
            zarya_barrier.retire(self, target.entity)
            self.zarya_beam_frames.pop(target.entity, None)
            self.tesla_frames.pop(target.entity, None)
            vitals.healing_block.clear()
            vitals.healing_boost.clear()
            self.knockbacks.pop(target.entity, None)
            self._release_charge(target.entity)
            self._release_captured(target.entity)
            self.controls = {key: end for key, end in self.controls.items() if key[0] != target.entity}
            vitals.now[HEALTH_POOL] = 0.0
            vitals.died_at = self.now
            vitals.death_tick = self.match.tick if world_tick is None else world_tick
            vitals.killer = _source_entity(shooter)
        else:
            sleep = self.sleeps.get(target.entity)
            if (sleep is not None and sleep.active_at(self.tick) and not sleep.waking
                    and (self.tick - sleep.start) * TICK > ana_sleep.DAMAGE_GRACE):
                sleep.end = min(sleep.end, self.tick + math.ceil(ana_sleep.WAKE / TICK))
                sleep.waking = True
        if not isinstance(shooter, bots.Bot):
            flags = (CRITICAL if critical else 0) | (KILLING if killed else 0)
            self._tell_hit(shooter, target, dealt, flags, frame)
            shooter.stats.add(stats.HERO_DAMAGE, dealt)
        victim = target.owner
        if killed and isinstance(victim, bots.Bot):
            # The NPC death script owns its terminal health change and death event ordering.
            self._bot_died(shooter, victim, critical, loadout, vitals.death_tick)
            if credit_charge:
                ultimate_charge.damage(shooter, target, dealt, tags, reflected_strike=reflected_strike,
                                       source_team=source_team)
            return True
        self._sync_body_health(victim, vitals)
        if killed:
            self._died(shooter, victim, critical, loadout)
        elif loadout == mei_frost.SPRAY and getattr(getattr(victim, "hero", None), "guid", None) == REINHARDT:
            mei_frost.hit(self, shooter, target)
        if credit_charge:
            ultimate_charge.damage(shooter, target, dealt, tags, reflected_strike=reflected_strike,
                                       source_team=source_team)
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
        self.stuns.pop(bot.entity, None)
        charging = bot.attack_action == "charge"
        bot.died(tick)
        if not bot.retain_corpse:
            self.respawns.append((self.now + BOT_RESPAWN, bot))
        if not isinstance(killer, bots.Bot):
            killer.stats.add(stats.ELIMINATIONS)
            self._kill_notice(killer, bot)
        log.info("[game] %s: %s killed %s", self.match.label(), killer.name, bot.name)
        script = bot.body_script
        if script is None:
            self.halt.drop_pull(bot.entity, terminal=True)
            return
        try:
            self.halt.drop_pull(bot.entity, terminal=True)
            if charging:
                heavy_charge.finish(self, bot, heavy_charge.instance_of(bot))
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
        self.stuns.pop(victim.body, None)
        self.winston_jump.retire(victim.body)
        self.halt.retire(victim.body)
        self.winston_primal.sync_health(victim, dead=True)
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
        self._sync_barrier(victim)
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
