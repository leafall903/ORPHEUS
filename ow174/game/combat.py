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
from dataclasses import dataclass, field, replace

from ow174.game import sigma_accretion, sigma_barrier, sigma_flux, sigma_grasp, sigma_hypersphere
from ow174.game import (
    arrow_deflection,
    bots,
    contacts,
    dragon,
    dva_owned,
    matrix,
    mccree_deadeye,
    observers,
    orisa_barrier,
    pools,
    projectiles,
    reinhardt_barrier,
    sonic,
    stats,
    transcendence,
    voicelines,
    volley_geometry,
    weapon_spread,
    world,
    winston_barrier,
    zarya_barrier,
)
from ow174.game.bits import BitWriter
from ow174.game.collision import MOVER, World
from ow174.game.commands import Command
from ow174.game.mover import mover_data
from ow174.game.script import expr, graph, nodes, runtime
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
# Static FirstPerson fields for unit-scale bodies. Animated camera/muzzle and
# native bone hit areas remain unproven; NPC muzzle origins keep their host policy.
EYE_STANDING = {0x0400000000000141: 1.35, 0x04000000000002A3: 1.425}
EYE_CROUCHED = {0x04000000000003CF: 1.085, 0x0400000000000141: 0.945,
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
TICK = 0.016
EPSILON = 1e-9
FLASHBANG_EFFECT = 0x04A0000000000079  # .053/0079 ->0228 ->224E
FLASHBANG_MODE_FLAG = 13408  # 0228 duration: flag set1.7s, otherwise0.8s


# --- geometry -----------------------------------------------------------------------------------------


def eye_height(body: int, crouched: bool = False) -> float:
    """Static authored player camera height; unknown definitions keep the existing fallback."""
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
        self.bonus_shields = 0.0
        self.bonus_shields_max = 0.0

    @property
    def dead(self) -> bool:
        return self.died_at is not None

    @property
    def total(self) -> float:
        return sum(self.now.values()) + self.bonus_shields

    def damage(self, amount: float) -> float:
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
        if self.bonus_shields_max:
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
    deflectable: bool = False
    knockback: tuple | None = None

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


def hit_def(state, cfg, *, critical_default: float = 1.0) -> HitDef | None:
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
        critical=(critical_default if cfg.get("m_A872C70E") is None
                  else value("m_A872C70E", 1.0)),
        rings=tuple(sorted(rings)),
        effect=effect.guid if isinstance(effect, expr.Asset) else None,
        deflectable=any(
            isinstance(tag, dict)
            and isinstance(value := state.evaluate(tag.get("m_EE3055BD"), watch=False), expr.Asset)
            and value.guid == arrow_deflection.DEFLECTABLE
            for tag in modify.get("m_148F3152") or ()
        ),
    )


# --- who can be hit -------------------------------------------------------------------------------------


def _source_entity(actor) -> int:
    """The combat actor's live entity ID."""
    return actor.entity if isinstance(actor, bots.Bot) else actor.body


def _active_fortify(owner, *, tick=None):
    if getattr(getattr(owner, "hero", None), "guid", None) != 0x02E000000000013E:
        return None
    script = getattr(owner, "body_script", None)
    if script is None or script.retired or script.component.owner_dead:
        return None
    if tick is not None and script.orisa_fortify_pending and tick < owner.match.tick:
        return None
    for instance in script.component.instances.values():
        if instance.graph.index == 0x0CF4:
            state = instance.states.get(4)
            if state is not None and state.active and not state.ending:
                return state
    return None


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
    gravity: float = 0.0  # authored E1A5B579 acceleration; +Y is up (reconstructed motion)
    continuation: dragon.Profile | None = None
    source_entity: int | None = None
    started_tick: float | None = None
    hit_entities: set[int] = field(default_factory=set)
    range_left: float | None = None  # reconstructed path budget, independent of authored lifetime
    distance_flown: float = 0.0  # accumulated <=16ms ballistic chords
    sonar: sonic.Profile | None = None
    matrix_absorbable: bool = False
    accretion_instance: int | None = None
    hypersphere: sigma_hypersphere.Motion | None = None
    storm_ricochets: int = 0


class BombBlast(runtime.InstanceContext):
    """Evaluate the authored Self-Destruct damage curve at a shield surface."""

    amount = 1.0

    def __init__(self, instance, radius):
        super().__init__(instance, None)
        self.radius = radius
        self.distance = 0.0

    def splash(self, distance):
        if distance > self.radius:
            return 0.0
        self.distance = expr.f32(distance)
        return _number(expr.evaluate(self.instance.graph.node(24).field("m_value"), self), 0.0)

    def var(self, scope, var):
        if (scope, var) == (expr.INSTANCE, 680):
            return self.distance
        return super().var(scope, var)


class Combat:
    """The match's health, hits, deaths and respawns."""

    def __init__(self, match) -> None:
        self.match = match
        self.vitals: dict[int, Vitals] = {}  # body or bot entity -> its health
        self.history: dict[int, deque] = {}  # entity -> (tick, feet, height, eye) of the last HISTORY ticks
        self.dragon_pose_anchors: dict[int, tuple[int, int, bool, int | None]] = {}
        self.replacement_ticks: dict[int, int] = {}  # replacement body -> authoritative world tick
        self.rockets: list[Rocket] = []
        self.dragon_profile: dragon.Profile | None = None
        self.dragons: list[dragon.Flight] = []
        self.sonic_profile: sonic.Profile | None = None
        self.sonars: list[sonic.Beacon] = []
        self.reconstructed_volley_patterns = False
        self.matrix_definitions: frozenset[int] = frozenset()
        self.grasp = sigma_grasp.SigmaGrasp(self)
        self.sigma_barriers = sigma_barrier.Barriers(self)
        self.barriers = reinhardt_barrier.ReinhardtBarriers(self)
        self.orisa_barriers = orisa_barrier.Barriers(self)
        self.winston_barriers = winston_barrier.Barriers(self)
        self.zarya_barriers = zarya_barrier.Barriers(self)
        self.respawns: list[tuple[float, object]] = []  # (when, a player or a bot)
        self.next_bot = bots.BOT_ENTITY + len(match.bots)
        self.now = 0.0
        self.tick = 0
        self.melee_seen: dict[int, dict[tuple[int, int], int]] = {}  # body -> counters by instance/state
        self.stuns: dict[int, int] = {}  # body identity -> exclusive world-tick deadline
        self.pins: dict[int, int] = {}  # manual Assassin source -> controlled victim body
        self.projectiles = projectiles.Projectiles(self)  # leases, confirmations, the Biotic Field
        self.observers = observers.Observers(match)  # the bodies' statescript for the other clients
        self.voice = voicelines.VoiceLines(match)  # the hero's spawn voice line (voicelines.py)

    # --- hooks (match.py) --------------------------------------------------------------------------

    def configure_volley_geometry(self, *, reconstructed_patterns: bool) -> None:
        """Explicit API opt-in; native spread units, phase and muzzle are unproven."""
        if type(reconstructed_patterns) is not bool:
            raise ValueError("reconstructed pattern geometry needs an explicit bool")
        self.reconstructed_volley_patterns = reconstructed_patterns

    def configure_sonic(self, profile: sonic.Profile) -> None:
        """Explicitly mount the external reconstructed Sonic profile before firing."""
        if not isinstance(profile, sonic.Profile) or self.rockets or self.sonars:
            raise ValueError("Sonic profile must be mounted before active projectiles")
        self.sonic_profile = profile

    def revealed_to(self, viewer, target) -> bool:
        """Read the current team-scoped reconstructed reveal state; no visibility replication claim."""
        return any(sonic.visible(beacon, viewer, _source_entity(target), self.match.game_map.free_for_all)
                   for beacon in self.sonars)

    def configure_dragonstrike(self, profile: dragon.Profile) -> None:
        """Mount an explicit external reconstructed profile before combat starts."""
        if not isinstance(profile, dragon.Profile) or self.rockets or self.dragons:
            raise ValueError("configure a validated dragon profile before active projectiles")
        self.dragon_profile = profile

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
        self.barriers.retire(player.body)
        self.winston_barriers.retire(player.body)
        self.zarya_barriers.retire(player.body)
        self.grasp.retire(player.body)
        self.sigma_barriers.retire(player.body)
        self.vitals.pop(player.body, None)
        self.history.pop(player.body, None)
        self.dragon_pose_anchors.pop(player.body, None)
        self.replacement_ticks.pop(player.body, None)
        self.melee_seen.pop(player.body, None)
        self.stuns.pop(player.body, None)
        self.clear_pins(player.body)
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

    def is_stunned(self, actor, *, tick: int | None = None) -> bool:
        entity = _source_entity(actor)
        return (entity in self.pins.values()
                or self.stuns.get(entity, 0) > (self.tick if tick is None else tick))

    def clear_pins(self, entity: int) -> None:
        for source, victim in list(self.pins.items()):
            if entity in (source, victim):
                del self.pins[source]

    def _hit_effect(self, target: Target, effect: int | None) -> None:
        """Resource-backed stun reconstruction; native immunity/presentation remain unverified."""
        if effect != FLASHBANG_EFFECT:
            return
        variables = graph.mode_variables(self.match.game_map.mode_guid) or {}
        duration = 1.7 if expr.truthy(variables.get(FLASHBANG_MODE_FLAG)) else 0.8
        self._apply_stun(target, duration)

    def _apply_stun(self, target, duration, *, extend_only=False):
        deadline = self.tick + math.ceil(duration / TICK)
        previous = self.stuns.get(target.entity, 0)
        if (self.vitals_of(target).dead or _active_fortify(target.owner, tick=self.tick) is not None
                or (extend_only and deadline <= previous)):
            return
        self.stuns[target.entity] = max(deadline, previous)
        self._interrupt(target)

    def _interrupt(self, target: Target) -> None:
        sigma_flux.interrupt_lift(self, target.owner)
        script = getattr(target.owner, "body_script", None)
        if script is None:
            return
        component = script.component
        if isinstance(target.owner, bots.Bot):
            from ow174.game import assassin, heavy_assault, sniper_special

            assassin.bind(component.world, component, None)
            heavy_assault.interrupt(target.owner)
            sniper_special.interrupt(target.owner)
        from ow174.game import hanzo_control

        hanzo_control.cancel_draw(script)
        component.edges([(value, False) for value in tuple(component.held)])
        if not isinstance(target.owner, bots.Bot):
            self.winston_barriers.cancel_pending(target.entity)
            # The masked player command must not release these same buttons again.
            script.buttons = 0
        hanzo_control.interrupt(script)
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
        """Consume the body's reports, including committed delayed Orisa throws."""
        if self.match.collision_world is None:
            return
        script = player.body_script
        if script is None:
            return
        transcendence.advance_entry(player)
        self.barriers.sync(player)
        self.zarya_barriers.sync(player)
        shots, script.component.shots[:] = list(script.component.shots), []
        if self.is_dead(player) or self.is_stunned(player):
            return
        self.winston_barriers.sync(player)
        for state, number, time, activation in shots:
            try:
                if command is not None:
                    self._shoot(player, command, state, number, time, activation=activation)
                else:
                    self.orisa_barriers.launch_unposed(player, state, time)
            except Exception:
                log.exception("[game] %s: %s's shot failed", self.match.label(), player.name)
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
            direction = aim(*command.aim())
            centre = _along(self._eye(player), direction, reach)
            # Reconstructed melee admission: the sphere can independently hit
            # a barrier and a body. Frontal Deflect blocks body damage.
            self.barriers.melee(player, centre, radius, amount)
            self.orisa_barriers.melee(player, centre, radius, amount)
            self.winston_barriers.melee(player, centre, radius, amount)
            self.sigma_barriers.melee(player, centre, radius, amount)
            protected = self.zarya_barriers.melee(player, centre, radius, amount, player.body)
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
                        and arrow_deflection.frontal(target, direction) is None):
                    self._damage(player, target, amount, False, loadout, command.frame)

    def update(self, now: float, tick: int, frames: bool = True) -> None:
        """Each tick: keep where the bodies are, fly the rockets, respawn, send health that changed. The
        other players' body frames only on the ticks that send (`frames`)."""
        if self.match.collision_world is None:
            return
        from ow174.game import assassin, enforcer

        retained = max(self.tick + 1, tick - COMBAT_CATCH_UP + 1)
        catching_up = tick > self.tick + 1
        first = tick
        if self.tick and catching_up and any(
            bot.firing and not bot.dead and isinstance(bot.body_script, BotScript) for bot in self.match.bots
        ):
            first = retained
        combat_ticks = set(range(first, tick + 1))
        pinned_bots = ()
        if catching_up and self.pins and self.zarya_barriers.projected:
            protected = {target for _, hp, target in self.zarya_barriers.projected.values() if hp > 0}
            pinned_bots = tuple(bot for bot in self.match.bots
                                if bot.hero == assassin.HERO and self.pins.get(bot.entity) in protected)
        recovery_bots = ()
        if catching_up and (self.rockets or self.dragons):
            recovery_bots = tuple(bot for bot in self.match.bots if enforcer.has_recovery(bot))
        fortify_active = (catching_up and (self.rockets or self.dragons)
                          and any(_active_fortify(player) is not None for player in self.match.players))
        if catching_up and self.rockets and (
            self.orisa_barriers.flights or self.orisa_barriers.actors
            or self.winston_barriers.actors or self.winston_barriers.pending or self.sigma_barriers.actors
            or self.zarya_barriers.pools or self.zarya_barriers.projected
            or any(self.orisa_barriers.pending(player) for player in self.match.players)
            or any(rocket.accretion_instance is not None for rocket in self.rockets)
            or fortify_active or recovery_bots
        ):
            # A deployed mesh can appear or expire before a travelling shot
            # arrives. Sweep its remaining flight before skipping the idle tail.
            flight_ticks = max((math.floor(rocket.left / TICK) + 1 for rocket in self.rockets
                                if rocket.last_tick is not None), default=0)
            combat_ticks.update(range(self.tick + 1, min(tick, self.tick + flight_ticks) + 1))
        if fortify_active or recovery_bots:
            # Dragons born during this gap also sample recovery and Fortify at each dose.
            dragon_ticks = max(
                [math.ceil((flight.profile.lifetime - flight.age) / TICK) for flight in self.dragons]
                + [math.ceil((rocket.left + rocket.continuation.lifetime) / TICK)
                   for rocket in self.rockets if rocket.last_tick is not None and rocket.continuation is not None],
                default=0,
            )
            combat_ticks.update(range(self.tick + 1, min(tick, self.tick + dragon_ticks) + 1))
        for target in self._targets():
            history = self.history.setdefault(target.entity, deque(maxlen=HISTORY))
            history.append((tick, target.feet, target.height, target.eye))
        # The moving Dragon can use an explicitly mapped command batch for its doses.
        # Other catch-up interactions use the current target poses.
        dragon_poses = self._dragon_gap_poses(tick)
        rebased_barriers = self.zarya_barriers.rebase_overlapping_casts(tick)
        self.orisa_barriers.rebase_fortify(tick)
        for at in self._scheduled_ticks(combat_ticks, pinned_bots):
            self.now, self.tick = now - (tick - at) * TICK, at
            self.barriers.update()
            self.winston_barriers.advance_idle(at)
            self.winston_barriers.update()
            self.orisa_barriers.advance_idle(at)
            self.zarya_barriers.advance_idle(at, rebased_barriers)
            self.orisa_barriers.update(at, TICK)
            self.grasp.update()
            self.sigma_barriers.update(sample_phase=at == tick)
            for rocket in list(self.rockets):
                if at == tick or rocket.last_tick is not None:
                    self._fly(rocket)
            self.dragons[:] = [flight for flight in self.dragons if dragon.advance(self, flight, at, poses=dragon_poses)]
            self.sonars[:] = [beacon for beacon in self.sonars if sonic.advance(self, beacon, at)]
            for bot in tuple(self.match.bots):
                if bot.detached_bomb:
                    dva_owned.advance_bomb(self.match, bot, at)
            if at >= first:
                self._bot_weapons(at)
            else:
                # Keep recovery ordered with historical contacts before the usual NPC input window.
                historical = recovery_bots + tuple(bot for bot in pinned_bots if bot.entity in self.pins)
                if historical:
                    self._bot_weapons(at, historical)
        self.zarya_barriers.new_activations.clear()
        for player in self.match.players:
            if player.body_script is not None:
                player.body_script.orisa_fortify_pending = False
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
                del self.pins[source]
        for entity in [key for key, deadline in self.stuns.items() if deadline <= tick]:
            del self.stuns[entity]
        for table in (self.history, self.vitals, self.dragon_pose_anchors, self.replacement_ticks, self.stuns):
            for entity in [key for key in table if key not in current]:
                del table[entity]

    def _scheduled_ticks(self, combat_ticks, pinned_bots):
        previous = self.tick
        for scheduled in sorted(combat_ticks):
            # Resolve established pin doses before advancing a projected shell to expiry.
            while previous + 1 < scheduled and (
                any(bot.entity in self.pins for bot in pinned_bots)
                or any(bot.detached_bomb for bot in self.match.bots)
            ):
                previous += 1
                yield previous
            previous = scheduled
            yield scheduled

    # --- targets -----------------------------------------------------------------------------------

    def observe_dragon_pose(self, player, frame: int, exact: bool) -> None:
        """Track an observed command/world anchor, retaining its first pose during a batch."""
        if not player.mover.history or player.mover.history[-1].frame != frame:
            self.dragon_pose_anchors.pop(player.body, None)
            return
        world = self.match.tick
        previous = self.dragon_pose_anchors.get(player.body)
        latest = frame if exact else None
        if previous is None:
            anchor = (world, frame, False, latest)
        else:
            old_world, old_frame, trusted, old_latest = previous
            if world == old_world:
                continued = exact and old_latest is not None and frame == old_latest + 1
                anchor = (world, old_frame, trusted and continued, latest)
            else:
                aligned = exact and world == old_world + 1 and old_latest == old_frame and frame == old_frame + 1
                anchor = (world, frame, aligned, latest)
        self.dragon_pose_anchors[player.body] = anchor

    def _dragon_gap_poses(self, tick: int) -> dict:
        """Use a complete, explicitly received movement batch for this world gap."""
        if tick <= self.tick + 1 or not (self.dragons or any(r.continuation for r in self.rockets)):
            return {}
        from ow174.game.mover import CROUCHED

        poses = {}
        for target in self._targets():
            anchor = self.dragon_pose_anchors.get(target.entity)
            if anchor is None:
                continue
            world, frame, trusted, latest = anchor
            if not trusted or world != self.tick or latest != frame + tick - world:
                continue
            history = {sample.frame: sample for sample in target.owner.mover.history}
            if latest not in history or history[latest].position != target.feet:
                continue
            frames = range(frame, latest)
            if any(number not in history for number in frames):
                continue
            body = target.owner.hero.body
            data = mover_data(body)
            for step, number in enumerate(frames, 1):
                sample = history[number]
                crouched = bool(sample.flags & CROUCHED)
                height = data.crouch_height if crouched else data.stand_height
                poses[target.entity, world + step] = replace(
                    target, feet=sample.position, height=height, eye=eye_height(body, crouched))
        return poses

    def _targets(self) -> list[Target]:
        found = []
        for player in self.match.players:
            if not (player.has_body and player.spawned and player.client is not None):
                continue
            if player.controlled_mech is not None:
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
            if bot.detached_bomb:
                continue
            data = mover_data(bot.body)
            eye = eye_height(bot.body) if bot.control_owner is not None else EYE
            found.append(
                Target(
                    bot.entity, bot.team, tuple(bot.position), data.stand_height, data.stand_radius, eye, bot
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

    def _bot_weapons(self, tick: int, only: tuple[bots.Bot, ...] | None = None) -> None:
        """Advance the weapon bodies of the map's firing bots and consume their volley reports."""
        from ow174.game import heavy_assault, npc_control

        actors = self.match.bots if only is None else only
        for bot in actors:
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
                self.clear_pins(bot.entity)
                log.exception("[game] %s: %s's weapon script failed", self.match.label(), bot.name)
        if only is None:
            heavy_assault.sync_captures(self)

    def _eye(self, player) -> tuple:
        x, y, z = player.mover.position
        crouched = player.mover.crouched
        height = eye_height(player.hero.body, crouched)
        return (x, y + height, z)

    def controlled_mech_shots(self, mech, command, snapshot):
        script = mech.body_script
        shots, script.component.shots[:] = list(script.component.shots), []
        for state, number, time, activation in shots:
            self._shoot(mech, command, state, number, time, activation=activation,
                        snapshot=snapshot or script.latest_live)
        if snapshot is not None:
            for viewer in self.match.players:
                if viewer.spawned and viewer.client is not None:
                    viewer.client.queue_bot_live(mech.entity, snapshot)

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
            eye = eye_height(shooter.body) if shooter.control_owner is not None else EYE
            angles = command.aim() if command is not None else (round(shooter.yaw), round(shooter.pitch))
            origin, direction = (x, y + eye, z), aim(*angles)
        else:
            origin, direction = self._eye(shooter), aim(*command.aim())
            direction = weapon_spread.fan_direction(shooter, state, direction, number, time, activation)
        if not npc and state.instance.graph.index == mccree_deadeye.TARGET_EFFECT:
            directed = mccree_deadeye.shot_aim(self, shooter, state)
            if directed is None:
                return
            origin, direction = directed
        if not npc and self.orisa_barriers.admits(state):
            self.orisa_barriers.launch(shooter, state, origin, direction, command.aim()[0])
            return
        accretion = (not npc and shooter.hero.guid == sigma_accretion.HERO
                     and state.instance.graph.index == sigma_accretion.ACCRETION and node.pos == 9)
        if accretion:
            direction = sigma_accretion.launch(state, direction)
        entity_def = (node.field("m_projectileEntity") or {}).get("m_entityDef")
        definition = state.evaluate(entity_def, watch=False) if entity_def is not None else None
        # Scoped reconstruction for null weapon multipliers. The native -1 resolution is open.
        hanzo_bow = (
            not npc and shooter.hero.guid == dragon.HANZO and state.instance.graph.index == 0x00B9
            and isinstance(definition, expr.Asset)
            and (node.pos, definition.guid) in (
                (5, 0x0400000000000144), (5, 0x040000000000020B), (5, 0x040000000000270F),
                (114, 0x0400000000000149), (114, 0x040000000000020D),
            )
        )
        # Sonic keeps its own projectile definition during the shared Storm window.
        storm = hanzo_bow and (definition.guid == 0x040000000000270F or (
            node.pos == 114 and (enabled := state.instance.find_var(expr.INSTANCE, 18944)) is not None
            and enabled.value() is True
        ))
        peacekeeper_primary = not npc and node.pos == 31 and weapon_spread.owns_peacekeeper(shooter, state)
        fusion = npc and dva_owned.owns_volley(shooter, state, 0x050A, 6)
        missiles = npc and dva_owned.owns_volley(shooter, state, 0x1128, 10)
        light_gun = (not npc and shooter.hero.guid == dva_owned.DVA
                     and state.instance.graph.index == dva_owned.LIGHT_GUN and node.pos == 10
                     and isinstance(definition, expr.Asset) and definition.guid == dva_owned.LIGHT_GUN_PROJECTILE)
        direct = hit_def(state, node.field("m_6396149F"), critical_default=(
            2.0 if hanzo_bow or peacekeeper_primary or fusion or light_gun else 1.0
        ))
        speed = field("m_projectileSpeed")
        try:
            if fusion:
                directions = dva_owned.fusion_directions(shooter, state, direction, number, activation)
            else:
                directions = volley_geometry.directions(
                    state, direction, reconstructed_patterns=self.reconstructed_volley_patterns)
            if speed and len(directions) > 1 and not npc:
                raise volley_geometry.UnsupportedGeometry(
                    "multiple player projectile leases are not implemented")
        except volley_geometry.UnsupportedGeometry as error:
            expr.warn_once(f"volley-geometry:{state.instance.graph.index}:{node.pos}:{error}",
                           "volley geometry refuses graph%04X/node%d: %s",
                           state.instance.graph.index, node.pos, error)
            return
        if not directions:
            return
        if missiles or light_gun:
            directions = tuple(weapon_spread.single_launch_direction(ray) for ray in directions)
        if speed:
            admitted = isinstance(definition, expr.Asset) and definition.guid in self.matrix_definitions
            if missiles and direct is not None:
                # The same-build owned missile uses the projectile catalog for Deflect.
                direct.deflectable = admitted
            hypersphere = (sigma_hypersphere.motion(state)
                           if not npc and shooter.hero.guid == sigma_hypersphere.HERO else None)
            lifetime = field("m_projectileLifetime", 5.0) or 5.0
            reach = field("m_FEC435C6")
            gravity = 0.0
            for motion in node.field("m_projectileMotions") or ():
                if motion.get("$") == "STU_E1A5B579":
                    gravity += _number(state.evaluate(motion.get("m_599AB8D7"), watch=False), 0.0)
            if reach and not gravity:
                lifetime = min(lifetime, reach / speed)
            blast = hit_def(state, node.field("m_D2D11CE9"))
            # Scope the projectile flag interpretation to the two authored Sigma volleys.
            if hypersphere is not None and direct is not None and node.field("m_45271E8C") == 1:
                direct.deflectable = True
            if accretion:
                direct.knockback = sigma_accretion.knockback(state, node.field("m_6396149F"))
            if accretion or hypersphere is not None:
                blast.knockback = sigma_accretion.knockback(state, node.field("m_D2D11CE9"))
            for direction in directions:
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
                    continuation = self.dragon_profile
                    if (continuation is None or not isinstance(definition, expr.Asset)
                            or definition.guid != continuation.initial_definition or npc
                            or shooter.hero.guid != dragon.HANZO):
                        continuation = None
                    self.rockets.append(
                        Rocket(
                            shooter, shooter.team, origin, direction, speed, lifetime, direct, blast, shot,
                            last_tick=self.tick if npc else None,
                            gravity=gravity,
                            continuation=continuation, source_entity=_source_entity(shooter),
                            started_tick=self.tick + time % state.instance.component.frame_ms
                            / state.instance.component.frame_ms,
                            range_left=reach if reach and reach > 0 else None,
                            sonar=(self.sonic_profile if not npc and shooter.hero.guid == sonic.HANZO
                                   and isinstance(definition, expr.Asset)
                                   and definition.guid in sonic.DEFINITIONS else None),
                            matrix_absorbable=not accretion and admitted,
                            accretion_instance=state.instance.id if accretion else None,
                            hypersphere=hypersphere,
                            storm_ricochets=int(storm),
                        )
                    )
            return
        if direct is None or direct.amount <= 0.0:
            return
        reach = field("m_FEC435C6", RANGE) or RANGE
        # Instant rounds can name a projectile entity even though they have no flight speed.
        absorbable = isinstance(definition, expr.Asset) and definition.guid in self.matrix_definitions
        for direction in directions:
            self._hitscan(shooter, command, origin, direction, direct, reach, shot_frame,
                          absorbable=absorbable)

    def _hitscan(
        self, shooter, command, origin, direction, direct, reach, shot_frame,
        *, absorbable=False, travelled=0.0, reflections=0,
    ):
        """Resolve one pellet, including occlusion, falloff and independent pool damage."""
        npc = isinstance(shooter, bots.Bot)
        # The input supplies this simulation frame's aim and view time. Notices use the volley clock.
        view_tick = self.tick
        if command is not None:
            quanta = shooter.control_owner.quanta if npc else shooter.quanta
            view_tick = command.frame - command.view_delay(quanta) * 1e6 / quanta
        best = None
        reflection = None
        absorption = None
        for target in self._enemies(shooter):
            shown = self.seen(target, view_tick)
            if shown is None:
                continue
            a, b = shown.segment()
            t = ray_capsule(origin, direction, a, b, shown.radius)
            if t is not None and t <= reach and (best is None or t < best[0]):
                best = (t, target, shown)
            if direct.deflectable:
                entry = arrow_deflection.trace(shown, origin, direction, reach)
                if entry is not None and (reflection is None or entry < reflection[0]):
                    reflection = (entry, target)
            if absorbable:
                entry = matrix.trace(shown, origin, direction, reach, tick=self.tick)
                if entry is not None and (absorption is None or entry <= absorption[0]):
                    absorption = (entry, None)
                grasp = self.grasp.trace(shown, origin, direction, reach)
                if grasp is not None and (absorption is None or grasp[0] < absorption[0]):
                    absorption = grasp
        limit = best[0] if best is not None else reach
        if reflection is not None:
            if reflection[0] <= limit:
                limit = reflection[0]
            else:
                reflection = None
        wall = ray_world(self._collision(), origin, direction, limit)
        shield = None
        # Reconstructed: a nearer hostile defense takes the ray before solid contact.
        for service in (self.barriers, self.orisa_barriers, self.winston_barriers, self.sigma_barriers, self.zarya_barriers):
            contact = service.trace(shooter, origin, direction, limit)
            if (contact is not None and (wall is None or contact[0] < wall)
                    and ((best is None and reflection is None) or contact[0] < limit)
                    and (shield is None or contact[0] < shield[0])):
                shield = (*contact, service)
        if absorption is not None:
            distance, grasp = absorption
            if (((best is None and reflection is None) or distance < limit)
                    and (wall is None or distance < wall)
                    and (shield is None or distance <= shield[0])):
                if grasp is not None:
                    self.grasp.absorb(grasp, _source_entity(shooter), direct, None, travelled + distance)
                return
        if shield is not None:
            distance, owner, service = shield
            amount = direct.amount * direct.scale(travelled + distance)
            if service is self.zarya_barriers:
                service.damage(owner, amount, _source_entity(shooter))
            else:
                service.damage(owner, amount)
            return
        if wall is not None and wall < limit:
            return
        if reflection is not None:
            distance, defender = reflection
            # As with projectiles, Deflect transfers the shot at contact and keeps its range budget.
            if reflections < arrow_deflection.MAX_REFLECTIONS - 1 and reach - distance > 0.0001:
                owner = defender.owner
                forward = aim(owner.mover.yaw, owner.mover.pitch)
                point = _along(_along(origin, direction, distance), forward, 0.0001)
                used = distance + 0.0001
                self._hitscan(owner, None, point, forward, direct, reach - used, self.tick,
                              absorbable=absorbable, travelled=travelled + used, reflections=reflections + 1)
            return
        if best is None:
            return
        t, target, shown = best
        hit_y = origin[1] + direction[1] * t
        critical = (hit_y >= shown.feet[1] + shown.eye - HEAD_BELOW
                    and _active_fortify(target.owner, tick=self.tick) is None)
        amount = direct.amount * direct.scale(travelled + t) * (direct.critical if critical else 1.0)
        self._damage(
            shooter, target, amount, critical and direct.critical > 1.0, direct.loadout, shot_frame,
            world_tick=self.tick if npc or reflections else None,
        )
        self._hit_effect(target, direct.effect)

    def _fly(self, rocket: Rocket) -> None:
        """Sweep elapsed world time against the current bodies and static collision."""
        # Player input can arrive immediately before the scheduler accounts for a gap.
        elapsed = 1 if rocket.last_tick is None else self.tick - rocket.last_tick
        if elapsed <= 0:
            return
        rocket.last_tick = self.tick
        if rocket.continuation is not None and getattr(rocket.shooter, "gone", False):
            self._landed(rocket, continue_flight=False)
            return
        duration = min(elapsed * TICK, rocket.left)
        if rocket.started_tick is None:
            rocket.started_tick = self.tick - elapsed
        if rocket.continuation is not None:
            duration = min(duration, max(0.0, (self.tick - rocket.started_tick) * TICK - rocket.flown))
        if duration <= 0.0:
            if rocket.left <= 0.0:
                self._expire_rocket(rocket)
            return
        # Approximate an authored ballistic path with at most one world tick per
        # chord, also across scheduler gaps. Constant acceleration uses the exact
        # endpoint; collision and impact time within each chord are reconstructed.
        while duration > EPSILON:
            part = min(duration, TICK) if rocket.gravity else duration
            self._fly_step(rocket, part)
            if rocket not in self.rockets:
                break
            duration -= part

    def _fly_step(self, rocket: Rocket, duration: float, reflections: int = 0) -> None:
        # Sweep the remaining flight even when the range or lifetime ends inside this tick.
        if rocket.range_left is not None and rocket.range_left <= EPSILON:
            self._expire_rocket(rocket)
            return
        sphere = rocket.hypersphere
        budget = duration
        velocity = tuple(value * rocket.speed for value in rocket.direction)

        def offset(seconds):
            return (velocity[0] * seconds,
                    velocity[1] * seconds - 0.5 * rocket.gravity * seconds**2,
                    velocity[2] * seconds)

        displacement = offset(duration)
        step = math.sqrt(_dot(displacement, displacement))
        if rocket.range_left is not None and step > rocket.range_left:
            # Clip the last ballistic chord by distance, rather than by the
            # launch speed. Small chords approximate the curved path (unverified
            # native range semantics); their endpoints integrate gravity exactly.
            low, high = 0.0, duration
            for _ in range(32):
                middle = (low + high) / 2
                delta = offset(middle)
                if math.sqrt(_dot(delta, delta)) < rocket.range_left:
                    low = middle
                else:
                    high = middle
            duration = (low + high) / 2
            displacement = offset(duration)
            step = math.sqrt(_dot(displacement, displacement))
        direction = tuple(value / step for value in displacement) if step > EPSILON else rocket.direction
        if rocket.range_left is not None:
            step = min(step, rocket.range_left)
        best = None
        reflection = None
        absorption = None
        grasp = None
        for target in self._enemies(rocket.shooter):
            if target.entity in rocket.hit_entities:
                continue
            a, b = target.segment()
            radius = target.radius + (sphere.body_radius if sphere is not None else 0.0)
            t = ray_capsule(rocket.position, direction, a, b, radius)
            if t is not None and t <= step and (best is None or t < best[0]):
                best = (t, target)
            if rocket.direct is not None and rocket.direct.deflectable:
                entry = arrow_deflection.trace(target, rocket.position, direction, step)
                if entry is not None and (reflection is None or entry < reflection[0]):
                    reflection = (entry, target)
            if rocket.matrix_absorbable:
                entry = matrix.trace(target, rocket.position, direction, step, tick=self.tick)
                if entry is not None and (absorption is None or entry < absorption):
                    absorption, grasp = entry, None
                contact = self.grasp.trace(target, rocket.position, direction, step)
                if contact is not None and (absorption is None or contact[0] < absorption):
                    absorption, grasp = contact
        normal = None
        if sphere is not None and sphere.wall_radius > 0.0:
            hit = contacts.scene_sweep(
                self._collision().engine, rocket.position, _along(rocket.position, direction, step),
                ((0.0, 0.0, 0.0),), sphere.wall_radius, MOVER,
            )
            wall = None
            if hit is not None and _dot(direction, hit[2]) < -EPSILON:
                wall, normal = hit[0] * step, hit[2]
        else:
            world = self._collision()
            hit = world.cast_ray(rocket.position, direction, step) if world is not None else None
            wall = hit.distance if hit is not None else None
            if rocket.storm_ricochets and hit is not None:
                normal = hit.normal
                if _dot(direction, normal) > 0:
                    normal = tuple(-axis for axis in normal)
                if _dot(direction, normal) >= -EPSILON:
                    normal = None
        if wall is not None and (best is None or wall < best[0]):
            best = (wall, None)
        blast_impact = None
        if rocket.blast is not None:
            services = (self.barriers, self.orisa_barriers, self.winston_barriers, self.sigma_barriers)
            if rocket.direct is not None and rocket.direct.amount > 0.0:
                services += (self.zarya_barriers,)
            for service in services:
                contact = service.trace(rocket.shooter, rocket.position, direction, step)
                if contact is not None and (best is None or contact[0] < best[0]):
                    distance, owner = contact
                    best = (distance, None)
                    blast_impact = (service, owner)
        shield = None
        if rocket.direct is not None and rocket.blast is None:
            for service in (self.barriers, self.orisa_barriers, self.winston_barriers, self.sigma_barriers, self.zarya_barriers):
                contact = service.trace(rocket.shooter, rocket.position, direction, step)
                if contact is not None and (absorption is None or contact[0] < absorption):
                    absorption, target = contact
                    shield = service, target
        if (absorption is not None and (best is None or absorption < best[0])
                and (reflection is None or absorption < reflection[0])):
            rocket.position = _along(rocket.position, direction, absorption)
            travelled = duration * absorption / step if step > EPSILON else 0.0
            rocket.flown += travelled
            rocket.left -= travelled
            rocket.distance_flown += absorption
            if rocket.range_left is not None:
                rocket.range_left = max(0.0, rocket.range_left - absorption)
            if rocket.shot is not None:
                self.projectiles._end(rocket.shot, self.tick, explode=False)
                rocket.shot = None
            if shield is not None:
                service, target = shield
                amount = rocket.direct.amount * rocket.direct.scale(rocket.distance_flown)
                if service is self.zarya_barriers:
                    service.damage(target, amount, rocket.source_entity)
                else:
                    service.damage(target, amount)
            elif grasp is not None:
                self.grasp.absorb(grasp, rocket.source_entity, rocket.direct, rocket.blast, rocket.distance_flown)
            # A barrier is a solid contact for the initial Dragon phase.
            self._landed(rocket, continue_flight=shield is not None)
            return
        # Existing body and world contacts keep priority at equal distances.
        if reflection is not None and (best is None or reflection[0] < best[0]):
            distance, defender = reflection
            travelled = duration * distance / step if step > EPSILON else 0.0
            rocket.position = _along(rocket.position, direction, distance)
            rocket.flown += travelled
            rocket.left -= travelled
            rocket.distance_flown += distance
            if rocket.range_left is not None:
                rocket.range_left = max(0.0, rocket.range_left - distance)
            if rocket.shot is not None:
                self.projectiles._end(rocket.shot, self.tick, explode=False)
                rocket.shot = None
            rocket.shooter, rocket.team = defender.owner, defender.team
            rocket.source_entity = defender.entity
            rocket.direction = aim(defender.owner.mover.yaw, defender.owner.mover.pitch)
            rocket.speed = math.hypot(velocity[0], velocity[1] - rocket.gravity * travelled, velocity[2])
            remaining = min(budget - travelled, rocket.left)
            if reflections >= arrow_deflection.MAX_REFLECTIONS - 1:
                self._landed(rocket, continue_flight=False)
            elif rocket.left <= EPSILON or (rocket.range_left is not None and rocket.range_left <= EPSILON):
                self._landed(rocket)
            elif remaining > EPSILON:
                self._fly_step(rocket, remaining, reflections + 1)
            return
        if (normal is not None and blast_impact is None and best == (wall, None)
                and (rocket.storm_ricochets or (sphere is not None and sphere.bounces_left > 0))):
            self._bounce_surface(rocket, direction, velocity, normal, wall, step, duration, reflections)
            return
        if best is None:
            rocket.position = _along(rocket.position, direction, step)
            velocity = (velocity[0], velocity[1] - rocket.gravity * duration, velocity[2])
            rocket.speed = math.sqrt(_dot(velocity, velocity))
            if rocket.speed > EPSILON:
                rocket.direction = tuple(value / rocket.speed for value in velocity)
            rocket.flown += duration
            rocket.left -= duration
            rocket.distance_flown += step
            if rocket.range_left is not None:
                rocket.range_left = max(0.0, rocket.range_left - step)
            if rocket.left <= EPSILON or (rocket.range_left is not None
                                         and rocket.range_left <= EPSILON):
                self._expire_rocket(rocket)
            return
        t, struck = best
        travelled = duration * t / step if step > EPSILON else 0.0
        rocket.flown += travelled
        rocket.distance_flown += t
        if rocket.range_left is not None:
            rocket.range_left = max(0.0, rocket.range_left - t)
        point = _along(rocket.position, direction, t)
        rocket.position = point
        pierce = (struck is not None and rocket.continuation is not None
                  and len(rocket.hit_entities) + 1 < rocket.continuation.initial_hit_limit)
        if rocket.sonar is not None:
            attached = struck.entity if struck is not None else None
            offset = (tuple(a - b for a, b in zip(point, struck.feet, strict=True))
                      if struck is not None else (0.0, 0.0, 0.0))
            born = rocket.started_tick + rocket.flown / TICK
            self.sonars.append(sonic.Beacon(
                rocket.shooter, rocket.source_entity, rocket.team, point, attached,
                offset, born, rocket.sonar,
            ))
        if not pierce:
            self._landed(rocket)
        accretion_direct = False
        if struck is not None and rocket.direct is not None and rocket.direct.amount > 0.0:
            critical = (point[1] >= struck.feet[1] + struck.eye - HEAD_BELOW
                        and _active_fortify(struck.owner, tick=self.tick) is None)
            amount = rocket.direct.amount * rocket.direct.scale(rocket.distance_flown)
            impact_tick = (self.tick if rocket.continuation is None else min(
                self.tick, math.ceil(rocket.started_tick + rocket.flown / TICK - EPSILON),
            ))
            before = self.vitals_of(struck).total if rocket.accretion_instance is not None else None
            self._damage(
                rocket.shooter, struck,
                amount * (rocket.direct.critical if critical else 1.0),
                critical and rocket.direct.critical > 1.0, rocket.direct.loadout, impact_tick,
                world_tick=impact_tick,
                source_entity=rocket.source_entity,
            )
            self._hit_effect(struck, rocket.direct.effect)
            if before is not None:
                dealt = before - self.vitals_of(struck).total
                accretion_direct = dealt > 0.0
                sigma_accretion.hit(self, rocket, struck, dealt)
        if pierce:
            rocket.hit_entities.add(struck.entity)
            rocket.left -= travelled
            remaining = duration - travelled
            if remaining > EPSILON:
                self._fly_step(rocket, remaining, reflections)
            return
        # Start just outside the impact surface, on the projectile's approach side.
        if blast_impact is not None:
            visible_from = _along(point, direction, -0.0001)
        else:
            visible_from = _along(point, rocket.direction, -0.0001) if wall is not None else point
        self._explode(rocket, point, visible_from, blast_impact, struck)
        if accretion_direct:
            self._projectile_impulse(struck, rocket.direct.knockback, rocket.direction)

    def _bounce_surface(self, rocket, direction, velocity, normal, wall, step, duration, reflections):
        sphere = rocket.hypersphere
        if sphere is None:
            # One specular Storm Arrow bounce. The native restitution is unverified.
            tangent_scale = normal_scale = 1.0
            rocket.storm_ricochets -= 1
        else:
            tangent_scale, normal_scale = sphere.tangent, sphere.normal
            rocket.hypersphere = replace(sphere, bounces_left=sphere.bounces_left - 1)
        travelled = duration * wall / step if step > EPSILON else 0.0
        impact_velocity = (velocity[0], velocity[1] - rocket.gravity * travelled, velocity[2])
        normal_speed = _dot(impact_velocity, normal)
        reflected = tuple(
            expr.f32((axis - normal_speed * n) * tangent_scale - normal_speed * n * normal_scale)
            for axis, n in zip(impact_velocity, normal, strict=True)
        )
        rocket.speed = math.hypot(*reflected)
        rocket.position = _along(_along(rocket.position, direction, wall), normal, 0.001)
        rocket.flown += travelled
        rocket.left -= travelled
        rocket.distance_flown += wall
        if rocket.range_left is not None:
            rocket.range_left = max(0.0, rocket.range_left - wall)
        if rocket.speed > EPSILON:
            rocket.direction = tuple(axis / rocket.speed for axis in reflected)
        if rocket.left <= EPSILON or rocket.range_left == 0.0:
            self._expire_rocket(rocket)
        elif duration - travelled > EPSILON:
            self._fly_step(rocket, duration - travelled, reflections)

    def _expire_rocket(self, rocket):
        self._landed(rocket)
        if rocket.hypersphere is not None:
            self._explode(rocket, rocket.position, rocket.position, None, None)

    def _explode(self, rocket, point, visible_from, blast_impact, struck):
        blast = rocket.blast
        if blast is None or (blast.amount <= 0.0 and blast_impact is None):
            return
        direct_amount = (rocket.direct.amount * rocket.direct.scale(rocket.distance_flown)
                         if rocket.direct is not None else 0.0)
        impact_service, impact_owner = blast_impact or (None, None)
        mesh_cover = [(service, service.splash(
            rocket.shooter, point, visible_from, blast, impact_owner if impact_service is service else None,
            direct_amount,
        )) for service in (self.barriers, self.orisa_barriers, self.winston_barriers, self.sigma_barriers)]
        impact_zarya = impact_owner if impact_service is self.zarya_barriers else None
        if impact_zarya is not None:
            # The struck bubble covers its recipient for the full blast even if it breaks.
            self.zarya_barriers.damage(
                impact_zarya, direct_amount + blast.amount * blast.splash(0), rocket.source_entity,
            )
        if blast.amount <= 0.0:
            return
        bubble_cover = {self.zarya_barriers.body(owner) for owner in self.zarya_barriers.enemies(rocket.shooter)}
        for target in self._enemies(rocket.shooter):
            if target.owner is impact_zarya:
                continue
            protected = target.entity in bubble_cover
            if protected and blast.effect != FLASHBANG_EFFECT:
                # Reconstructed splash uses the nearest sphere surface and
                # one visibility test from the explosion's approach side.
                centre = self.zarya_barriers.centre(target.owner)
                reach = math.dist(point, centre)
                distance = max(0.0, reach - zarya_barrier.RADIUS)
                scalar = blast.splash(distance)
                witness = (tuple(p + (c - p) * distance / reach for p, c in zip(point, centre, strict=True))
                           if distance > 0.0 else point)
                if (scalar > 0.0 and capsule_visible(self._collision(), visible_from, witness, witness, 0)
                        and not any(service.covers(cover, visible_from, witness, witness)
                                    for service, cover in mesh_cover)):
                    self.zarya_barriers.damage(target.owner, blast.amount * scalar, rocket.source_entity)
                    continue
            a, b = target.segment()
            distance = max(0.0, segment_distance(point, a, b) - target.radius)
            scalar = blast.splash(distance)
            if (scalar > 0.0 and capsule_visible(self._collision(), visible_from, a, b, target.radius)
                    and not any(service.covers(cover, visible_from, a, b) for service, cover in mesh_cover)):
                # Flashbang keeps its reconstructed per-victim blast policy.
                if protected and blast.effect == FLASHBANG_EFFECT:
                    self.zarya_barriers.damage(target.owner, blast.amount * scalar, rocket.source_entity)
                    continue
                push = blast.knockback is not None and (
                    struck is None or target.entity != struck.entity
                    or rocket.direct is None or rocket.direct.knockback is None
                )
                before = self.vitals_of(target).total if push else None
                self._damage(
                    rocket.shooter, target, blast.amount * scalar, False, blast.loadout, self.tick,
                    world_tick=self.tick, source_entity=rocket.source_entity,
                )
                self._hit_effect(target, blast.effect)
                if before is not None and self.vitals_of(target).total < before:
                    radial = tuple(a - b for a, b in zip(target.feet, point, strict=True))
                    self._projectile_impulse(target, blast.knockback, radial, distance)

    def _projectile_impulse(self, target, strengths, direction, distance=0.0):
        if not self.vitals_of(target).dead and _active_fortify(target.owner, tick=self.tick) is None:
            sigma_accretion.impulse(target, strengths, direction, distance)

    def _landed(self, rocket: Rocket, *, continue_flight: bool = True) -> None:
        """The projectile ended (hit, range, lifetime): the predicted one's release follows."""
        if rocket in self.rockets:
            self.rockets.remove(rocket)
            if continue_flight and rocket.continuation is not None:
                born = (self.tick if rocket.started_tick is None
                        else rocket.started_tick + rocket.flown / TICK)
                self.dragons.append(dragon.Flight(
                    rocket.shooter, rocket.source_entity or _source_entity(rocket.shooter), rocket.team,
                    rocket.position, rocket.direction, rocket.continuation, born,
                    # Fractional birth tick lets a late scheduler catch up the
                    # continuation instead of restarting its lifetime now.
                ))
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
            given = vitals.heal(amount)
            if given <= 0.0:
                continue
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

    def dva_call_targets(self, world, state):
        pilot = self.match._message_player(world.body)
        centre = state.evaluate(state.node.field("m_05BE62DD"), watch=False)
        radius = _number(state.evaluate(state.node.field("m_radius"), watch=False), 0.0)
        if pilot is None or not isinstance(centre, expr.Vec3) or radius <= 0:
            return ()
        origin = tuple(centre)
        selected = []
        for target in self._enemies(pilot):
            if getattr(target.owner, "control_owner", None) is pilot:
                continue
            a, b = target.segment()
            if (segment_distance(origin, a, b) <= radius + target.radius
                    and capsule_visible(self._collision(), origin, a, b, target.radius)):
                selected.append(expr.Entity(target.entity))
        return selected

    def dva_call_damage(self, world, state):
        pilot = self.match._message_player(world.body)
        if pilot is None:
            return
        selected = state.evaluate(state.node.field("m_FA3ECC43"), watch=False)
        ids = {entity.id for entity in selected or () if isinstance(entity, expr.Entity)}
        config = state.node.field("m_modifyHealth")
        amount = _number(state.evaluate(config.get("m_amount"), watch=False), 0.0)
        loadout = state.evaluate(config.get("m_loadout"), watch=False)
        loadout = loadout.guid if isinstance(loadout, expr.Asset) else None
        centre = state.evaluate(config.get("m_C0139FA6"), watch=False)
        horizontal = _number(state.evaluate(config.get("m_E33EFEE1"), watch=False), 0.0)
        vertical = _number(state.evaluate(config.get("m_583C208B"), watch=False), 0.0)
        rings = config.get("m_knockbackRings") or ()
        for target in self._enemies(pilot):
            if target.entity not in ids:
                continue
            if self.zarya_barriers.absorb_contact(pilot, target.owner, amount):
                continue
            vitals = self.vitals_of(target)
            before = vitals.total
            self._damage(pilot, target, amount, False, loadout, state.instance.component.frame)
            if vitals.total < before and not vitals.dead and isinstance(centre, expr.Vec3):
                self._dva_radial_impulse(state, target, tuple(centre), rings, horizontal, vertical)

    def dva_bomb_targets(self, bomb, state):
        # Keep the first query's cover even if the detonation breaks a shield.
        if state.targets is not None:
            return state.targets
        centre = state.evaluate(state.node.field("m_05BE62DD"), watch=False)
        radius = _number(state.evaluate(state.node.field("m_radius"), watch=False), 0.0)
        targets, distances = [], []
        if isinstance(centre, expr.Vec3) and radius > 0:
            origin = tuple(base + offset for base, offset in zip(bomb.position, centre, strict=True))
            blast = BombBlast(state.instance, radius)
            services = (self.barriers, self.orisa_barriers, self.winston_barriers, self.sigma_barriers)
            mesh_cover = [(service, service.splash(bomb, origin, origin, blast, None, 0.0))
                          for service in services]
            bubbles = set(self.zarya_barriers.enemies(bomb))
            for target in self._enemies(bomb):
                if target.owner is bomb.control_owner:
                    continue
                a, b = target.segment()
                distance = max(0.0, segment_distance(origin, a, b) - target.radius)
                if any(service.covers(cover, origin, a, b) for service, cover in mesh_cover):
                    continue
                if target.owner in bubbles:
                    centre = self.zarya_barriers.centre(target.owner)
                    reach = math.dist(origin, centre)
                    distance = max(0.0, reach - zarya_barrier.RADIUS)
                    surface = _along(origin, _sub(centre, origin), distance / reach) if reach else origin
                    if capsule_visible(self._collision(), origin, surface, surface, 0):
                        self.zarya_barriers.damage(target.owner, blast.splash(distance), bomb.entity)
                    continue
                if distance <= radius and capsule_visible(self._collision(), origin, a, b, target.radius):
                    targets.append(expr.Entity(target.entity))
                    distances.append(expr.f32(distance))
        state.instance.write(expr.dynamic(state.node.field("m_CEE20F6F")), tuple(distances))
        return targets

    def dva_bomb_damage(self, bomb, state):
        selected = state.evaluate(state.node.field("m_FA3ECC43"), watch=False)
        entities = selected if isinstance(selected, tuple) else (selected,)
        ids = {entity.id for entity in entities if isinstance(entity, expr.Entity)}
        amount = _number(state.evaluate(state.node.field("m_modifyHealth.m_amount"), watch=False), 0.0)
        loadout = state.evaluate(state.node.field("m_modifyHealth.m_loadout"), watch=False)
        loadout = loadout.guid if isinstance(loadout, expr.Asset) else None
        config = state.node.field("m_modifyHealth")
        centre = state.evaluate(config.get("m_C0139FA6"), watch=False)
        horizontal = _number(state.evaluate(config.get("m_E33EFEE1"), watch=False), 0.0)
        vertical = _number(state.evaluate(config.get("m_583C208B"), watch=False), 0.0)
        rings = config.get("m_knockbackRings") or ()
        for target in self._enemies(bomb):
            if target.entity not in ids:
                continue
            vitals = self.vitals_of(target)
            before = vitals.total
            self._damage(bomb, target, amount, False, loadout, state.instance.component.frame,
                         world_tick=self.tick)
            if vitals.total < before and not vitals.dead and isinstance(centre, expr.Vec3):
                self._dva_radial_impulse(state, target, tuple(centre), rings, horizontal, vertical)

    def _dva_radial_impulse(self, state, target, origin, rings, horizontal, vertical):
        a, b = target.segment()
        distance = max(0.0, segment_distance(origin, a, b) - target.radius)
        for ring in rings:
            radius = _number(state.evaluate(ring.get("m_radius"), watch=False), 0.0)
            if distance > radius:
                continue
            planar = _number(state.evaluate(ring.get("m_9102DA3F"), watch=False), 0.0)
            lift = _number(state.evaluate(ring.get("m_10399AE7"), watch=False), 0.0)
            # The same-build reconstruction applies the first containing ring.
            self._projectile_impulse(target, (horizontal, vertical, ((radius, planar, lift),)),
                                     _sub(target.feet, origin), distance)
            break

    # --- damage and death --------------------------------------------------------------------------

    def _credit_player(self, actor):
        if not isinstance(actor, bots.Bot):
            return actor
        owner = actor.control_owner
        return owner if owner is not None and not owner.gone and actor in self.match.bots else None

    def _damage(
        self, shooter, target: Target, amount: float, critical: bool, loadout, frame: int,
        *, world_tick: int | None = None, source_entity: int | None = None,
    ) -> None:
        vitals = self.vitals_of(target)
        if vitals.dead or amount <= 0.0 or transcendence.invulnerable(target.owner):
            return
        fortify = _active_fortify(target.owner, tick=self.tick if world_tick is None else world_tick)
        if fortify is not None:
            # Reconstructed host ordering: the authored Fortify filter applies before armour.
            amount *= 1.0 + expr.to_float(fortify.evaluate(fortify.node.field("m_50530EAB"), watch=False))
        dealt = vitals.damage(amount)
        if dealt <= 0.0:
            return
        killed = vitals.now[HEALTH_POOL] <= EPSILON
        if killed:
            vitals.now[HEALTH_POOL] = 0.0
            vitals.died_at = self.now if world_tick is None else self.now - (self.tick - world_tick) * TICK
            vitals.death_tick = self.match.tick if world_tick is None else world_tick
            vitals.killer = _source_entity(shooter) if source_entity is None else source_entity
        credit = self._credit_player(shooter)
        if credit is not None:
            flags = (CRITICAL if critical else 0) | (KILLING if killed else 0)
            self._tell_hit(credit, target, dealt, flags, frame)
            credit.stats.add(stats.HERO_DAMAGE, dealt)
            script = shooter.body_script
            if (script is not None and target.owner is not shooter and not credit.gone
                    and (source_entity is None or source_entity == _source_entity(shooter))
                    and (self.match.game_map.free_for_all or shooter.team != target.team)):
                if credit is shooter and shooter.hero.guid == dva_owned.DVA:
                    dva_owned.gain_pilot_damage(shooter, dealt)
                elif credit is shooter:
                    script.gain_ult_damage(dealt)
                else:
                    dva_owned.gain_mech_damage(shooter, dealt)
        victim = target.owner
        if killed and isinstance(victim, bots.Bot):
            # The NPC death script owns its terminal health change and death event ordering.
            self._bot_died(shooter, victim, critical, loadout, vitals.death_tick, source_entity=source_entity)
            return
        self._sync_body_health(victim, vitals)
        if isinstance(victim, bots.Bot):
            from ow174.game import enforcer

            source = _source_entity(shooter) if source_entity is None else source_entity
            enforcer.received_damage(self, victim, vitals, source)
        if killed:
            self._died(shooter, victim, critical, loadout, source_entity=source_entity)

    def _tell_hit(self, shooter, target: Target, dealt: float, flags: int, frame: int) -> None:
        """25001 to the shooter: the hit marker and the target's health bar (frame: the shot's)."""
        value = {
            "+0x78": {"+0x0": {"+0x0": target.entity}, "+0x4": max(1, round(dealt)), "+0x8": flags},
            "+0x84": frame & 0xFFFFFFFF,
        }
        self._queue_notice(shooter, target.owner, target.entity, HIT, value)

    def _kill_notice(self, killer, victim, *, source_entity: int | None = None) -> None:
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
    def _death_parameters(killer, critical: bool, loadout, source_entity: int | None = None) -> dict:
        return {
            KILLER: expr.Entity(_source_entity(killer) if source_entity is None else source_entity),
            CRIT: bool(critical),
            LOADOUT: expr.Asset(loadout) if loadout else None,
            ASSISTS: (),
        }

    def _bot_died(self, killer, bot: bots.Bot, critical: bool, loadout, tick: int,
                  *, source_entity: int | None = None) -> None:
        self.stuns.pop(bot.entity, None)
        self.clear_pins(bot.entity)
        bot.died(tick)
        if not bot.retain_corpse:
            died_at = self.now - (self.tick - tick) * TICK
            self.respawns.append((died_at + BOT_RESPAWN, bot))
        credit = self._credit_player(killer)
        if credit is not None:
            credit.stats.add(stats.ELIMINATIONS)
            self._kill_notice(credit, bot, source_entity=_source_entity(killer) if source_entity is None else source_entity)
        log.info("[game] %s: %s killed %s", self.match.label(), killer.name, bot.name)
        script = bot.body_script
        if script is None:
            return
        try:
            params = self._death_parameters(killer, critical, loadout, source_entity)
            if isinstance(script, BotScript):
                tick = max(tick, script.frame)
            source = _source_entity(killer) if source_entity is None else source_entity
            event = script.die(tick, params, source)
        except Exception:
            bot.body_script = None
            log.exception("[game] %s: %s's body script failed on %X", self.match.label(), bot.name, DIED)
            return
        dva_owned.queue_return(bot)
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

    def _died(self, killer, victim, critical: bool, loadout, *, source_entity: int | None = None) -> None:
        """A player died: the notices, his body script's "died" (the kill feed line), and the respawn."""
        self.stuns.pop(victim.body, None)
        sigma_flux.interrupt_lift(self, victim)
        self.clear_pins(victim.body)
        self.winston_barriers.cancel_pending(victim.body)
        if victim.hero.guid == dva_owned.DVA:
            dva_owned.retire_unbound(victim)
        label = self.match.label()
        log.info("[game] %s: %s killed %s", label, killer.name, victim.name)
        victim.stats.add(stats.DEATHS)
        credit = self._credit_player(killer)
        source = _source_entity(killer) if source_entity is None else source_entity
        if credit is not None and credit is not victim:
            credit.stats.add(stats.ELIMINATIONS)
            self._kill_notice(credit, victim, source_entity=source)
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
                params = self._death_parameters(killer, critical, loadout, source_entity)
                if runtime.send_message(component, DIED, params, source):
                    # The server owns the death event. Run it before the observer snapshot even
                    # if the victim sends no further commands.
                    component.run_now()
            except Exception:
                log.exception("[game] %s: %s's body script failed on %X", label, victim.name, DIED)
        vitals = self.vitals.get(victim.body)
        died_at = vitals.died_at if vitals is not None and vitals.died_at is not None else self.now
        self.respawns.append((died_at + respawn_seconds(self.match), victim))

    def _respawn(self, who) -> None:
        if isinstance(who, bots.Bot):
            self._respawn_bot(who)
            return
        player = who
        if player.gone or not player.has_body or not self.is_dead(player):
            return
        # Reconstructed Hanzo policy: a death respawn keeps earned
        # charge points. The fresh body starts a new passive timer.
        old = player.body_script
        retained = None
        if old is not None and old.hero == dragon.HANZO and old.ult_seconds is None:
            retained = expr.to_float(old.component.entity_var(ULT_CHARGE).value())
        if not self.match.switch_hero(player, player.hero):
            self.respawns.append((self.now + RESPAWN_RETRY, player))
            return
        fresh = player.body_script
        if retained is not None and fresh is not None:
            cost = expr.to_float(fresh.component.entity_var(ULT_COST).value())
            fresh.set_ult_charge(max(0.0, min(cost, retained)))
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
