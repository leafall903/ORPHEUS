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
    bots,
    brigitte_control,
    brigitte_shield,
    bubbles,
    defenses,
    deflection,
    doomfist_meteor,
    doomfist_punch,
    doomfist_slam,
    doomfist_uppercut,
    earthshatter,
    flashbang,
    hook,
    mei_blizzard,
    mei_frost,
    mei_wall,
    observers,
    orisa_halt,
    orisa_shield,
    pools,
    projectiles,
    pulse_bomb,
    rein_barrier,
    reinhardt_charge,
    sigma,
    sigma_accretion,
    sigma_barrier,
    sombra_control,
    stats,
    tracer_recall,
    venom,
    voicelines,
    world,
    zarya,
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
EYE_STANDING = {0x040000000000001C: 1.5, 0x04000000000002A3: 1.425}
EYE_CROUCHED = {0x04000000000003CF: 1.085, 0x040000000000001C: 1.05,
                0x04000000000002A3: 0.9975}  # authored FirstPerson m_816B830F
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
# Authored normal Ana 0BDD ->0BDE/node51 ->224E profile676B.
# Native filter/presentation and stacking are not hosted by this selected clock.
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


def eye_height(body: int, crouched: bool = False) -> float:
    """Authored static FirstPerson offsets; animated camera/muzzle/bone equivalence is unverified."""
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


def capsule_visible(collision: World | None, origin, a, b, radius: float) -> bool:
    """Reconstructed melee policy: a static ray to the nearest capsule surface."""
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
    effect: int | None = None  # ModifyHealth's authored hit-effect resource
    deflectable: bool = False  # explicit003BF hit tag; selected Genji admission
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
    critical = value("m_A872C70E")
    if critical is None:
        critical = 1.0
        if (
            state.instance.component.world.hero == 0x02E000000000000A
            and state.instance.graph.index == 0x001B
            and state.index in (6, 9)
        ):
            # Explicit Widowmaker reconstruction, pending native server proof.
            # These two target-build volleys omit A872C70E, retain 66D29548=2,
            # and the scoped volley has BC54C84B=1.25. Do not generalize these
            # field meanings to other hit definitions (including the mine).
            critical = value("m_66D29548", 1.0) * max(1.0, value("m_BC54C84B", 0.0))
        elif (state.instance.component.world.hero == 0x02E0000000000042
              and getattr(state.instance.component.world, "body", None) == state.instance.component.entity
              and state.instance.graph.index == 0x01D2 and state.node.pos == 31):
            # Selected parent's declared owned-McCree primary policy; native proof remains open.
            critical = 2.0
    return HitDef(
        amount=amount,
        loadout=loadout.guid if isinstance(loadout, expr.Asset) else None,
        falloff_start=value("m_EAD7F104"),
        falloff_end=value("m_157E5BC5"),
        falloff_floor=value("m_72A81154"),
        critical=critical,
        rings=tuple(sorted(rings)),
        effect=effect.guid if isinstance(effect, expr.Asset) else None,
        knockback=sigma_accretion.accretion_knockback(state, modify),
        deflectable=any(
            (tag.get("m_EE3055BD") or {}).get("m_identifier") == "0x0D800000000003BF"
            for tag in modify.get("m_148F3152") or [] if isinstance(tag, dict)
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
    definition: int | None = None  # immutable emitted003 identity for defensive admission
    gravity: float = 0.0
    velocity: tuple | None = None
    detonate_on_expiry: bool = False
    accretion_source: tuple[int, int] | None = None  # launch body and instance
    target_limit: int = 1
    hit_entities: set[int] = field(default_factory=set)


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
        self.sleeps: dict[int, tuple[int, int, bool]] = {}  # body -> start, exclusive end, waking
        self.projectiles = projectiles.Projectiles(self)  # leases, confirmations, the Biotic Field
        self.observers = observers.Observers(match)  # the bodies' statescript for the other clients
        self.voice = voicelines.VoiceLines(match)  # the hero's spawn voice line (voicelines.py)
        self.venom = venom.Mines(self)
        self.sombra_hacks = sombra_control.Hacks(self)
        self.uppercuts = doomfist_uppercut.Uppercuts(self)
        self.pulse_bombs = pulse_bomb.PulseBombs(self)
        self.recalls = tracer_recall.Recalls(self)
        self.meteors = doomfist_meteor.Meteors(self)
        self.slams = doomfist_slam.Slams(self)
        self.punches = doomfist_punch.Punches(self)
        self.charges = reinhardt_charge.Charges(self)
        self.earthshatter = earthshatter.Shatters(self)
        self.frost = mei_frost.Frost(self)
        self.blizzard = mei_blizzard.Blizzard(self)
        self.walls = mei_wall.Walls(self)
        self.bubbles = bubbles.Bubbles(self)
        self.brigitte_control = brigitte_control.BrigitteControl(self)
        self.brigitte_shields = brigitte_shield.Shields(self)
        self.orisa_shields = orisa_shield.Shields(self)
        self.halt = orisa_halt.Halt(self)
        self.hooks = hook.Hooks(self)
        self.sigma_barriers = sigma_barrier.Barriers(self)
        self.rein_geometry = rein_barrier.geometry_bytes()
        self.zarya_barriers = zarya.Barriers(self)
        self.zarya_recovery = zarya.ShieldRecovery(self)
        self.projectile_catalog = deflection.catalog()
        self.grasp_shields: dict[int, sigma.ShieldDecay] = {}
        self.grasp_grants: dict[int, tuple[int, int]] = {}

    # --- hooks (match.py) --------------------------------------------------------------------------

    def is_dead(self, player) -> bool:
        vitals = self.vitals.get(player.body) if player.has_body else None
        return vitals is not None and vitals.dead

    def tracked_targets(self, state) -> tuple[int, ...]:
        """Selected Infra-Sight and projected-barrier searches from build104319.

        Its authored radius is evaluated normally; terrain does not occlude a
        reveal. Projected protection uses its separately declared ally/LOS search.
        Full TrackTargets filters and per-target graph contexts remain
        outside this narrowly selected host service.
        """
        if (state.instance.graph.index == doomfist_uppercut.GRAPH and state.index == 9
                and state.instance.component.world.hero == doomfist_punch.HERO):
            return self.uppercuts.targets(state)
        selected = sombra_control.targets(self, state)
        if selected is not NotImplemented:
            return selected
        if state.instance.graph.index == 0x025C and state.node.pos in (11, 18):
            owner = next((p for p in self.match.players
                          if p.body == state.instance.component.entity), None)
            if (owner is None or owner.body_script is None
                    or owner.body_script.component is not state.instance.component):
                return ()
            selected = self.zarya_barriers.track_targets(state, (owner.mover.yaw, owner.mover.pitch))
            return () if selected is NotImplemented else selected
        if (
            state.instance.graph.index != 0x0067 or state.index != 0
            or state.instance.component.world.hero != 0x02E000000000000A
        ):
            return ()
        source = next((p for p in self.match.players if p.body == state.instance.component.entity), None)
        if (source is None or not source.spawned or self.is_dead(source)
                or source.body_script is None
                or source.body_script.component is not state.instance.component):
            return ()
        return self._infra_targets(source, state.instance)

    def _infra_targets(self, source, instance) -> tuple[int, ...]:
        if (source.body_script is None or not source.spawned or self.is_dead(source)
                or instance.component is not source.body_script.component):
            return ()
        window = source.body_script.infra_window()
        now = round(self.now * 1000)
        if (window is None or window[1] > now + instance.component.frame_ms - 1 or now >= window[2]):
            return ()
        radius = _number(instance.evaluate(instance.graph.node(1).field("m_radius")), 0.0)
        return tuple(
            target.entity for target in self._enemies(source, damageable=False)
            if math.dist(source.mover.position, target.feet) <= radius
        )

    def grapple_cast(self, state):
        """Reconstructed hook against static surfaces and selected Ice Wall pillars.

        005A's initial search range20 and travel speed40 are authored. Native
        4BBC5D09 virtual14 (RVA C93AB0) selects field170 unless the existing
        static-contact output field140 is true, then selects field1E8 (22).
        This host performs only the initial cast, not that contact recheck.
        Surface filters, animated muzzle origin and entity/bone attachment need
        native confirmation. The existing swept MoveTo handles the pull.
        """
        if (state.instance.graph.index != 0x005A or state.index != 0
                or state.instance.component.world.hero != 0x02E000000000000A):
            return None
        source = next((p for p in self.match.players if p.body == state.instance.component.entity), None)
        if (source is None or self.is_dead(source) or source.body_script is None
                or source.body_script.component is not state.instance.component):
            return None
        reach = _number(state.evaluate(state.node.field("m_2AFC083F"), watch=False), 0.0)
        speed = _number(state.evaluate(state.node.field("m_speed"), watch=False), 0.0)
        if reach <= 0 or speed <= 0:
            return None
        direction = aim(round(source.mover.yaw), source.mover.pitch)
        origin = self._eye(source)
        static_hit = self._collision().cast_ray(origin, direction, reach)
        hit, piece = self.walls.contact(origin, direction, reach, static_hit)
        self.walls.anchor(source.body, piece, state.instance)
        result = runtime.RaycastResult(
            hit is not None, None,
            expr.Vec3(*hit.point) if hit else None, expr.Vec3(*hit.normal) if hit else None,
        )
        return result, (hit.distance if hit else reach) / speed

    def revealed_to(self, viewer, entity: int) -> bool:
        """Authoritative Infra-Sight status, shared with the source's teammates.

        Presentation consumes the same tracked rows. This query does not grant
        damage penetration, disable stealth inputs, or change target teams.
        """
        target = next((t for t in self._targets() if t.entity == entity), None)
        if target is None or self.vitals_of(target).dead:
            return False
        if self.venom.poisoned_by(viewer, entity):
            return True
        for source in self.match.players:
            ally = not self.match.game_map.free_for_all and source.team == viewer.team
            if source is not viewer and not ally:
                continue
            if source.body_script is None or self.is_dead(source):
                continue
            for instance in source.body_script.component.instances.values():
                if instance.graph.index == 0x0067 and entity in self._infra_targets(source, instance):
                    return True
        return False

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
        self.sombra_hacks.retire(player.body)
        self.frost.retire(player.body, player)
        self.sleeps.pop(player.body, None)
        from ow174.game import dva_body

        dva_body.retire_source(self.match, player.entity, player.body)
        self.clear_pins(player.body)
        self.grasp_shields.pop(player.body, None)
        self.grasp_grants.pop(player.body, None)
        self.venom.retire(player.body)
        self.bubbles.retire(player.body)
        self.brigitte_shields.retire(player.body)
        self.brigitte_control.retire(player.body)
        self.orisa_shields.retire(player.body)
        self.halt.retire(player.body)
        self.blizzard.retire(player.body)
        self.walls.retire(player.body)
        self.hooks.retire(player.body)
        self.charges.retire(player.body)
        self.punches.retire(player.body)
        self.pulse_bombs.retire(player.body)
        self.recalls.retire(player.body)
        self.meteors.retire(player.body)
        self.slams.retire(player.body)
        self.uppercuts.retire(player.body)
        self.sigma_barriers.retire_body(player.body)
        self.zarya_barriers.retire(player.body)
        self.zarya_recovery.retire(player.body)
        # The owner's leased projectiles and their observer copies retire with the old body.
        self.rockets[:] = [
            rocket for rocket in self.rockets if rocket.shooter is not player or (
                rocket.shot is None and rocket.accretion_source is None
                and not (rocket.direct and rocket.direct.effect == ANA_SLEEP_EFFECT)
            )
        ]
        self.respawns[:] = [(when, who) for when, who in self.respawns if who is not player]

    def record_replacement(self, body: int) -> None:
        """Keep the creation boundary independent of the rolling pose history."""
        self.replacement_ticks[body] = self.match.tick
        self.stuns.pop(body, None)
        self.sombra_hacks.retire(body)
        self.frost.retire(body)
        self.sleeps.pop(body, None)
        self.clear_pins(body)

    def captured(self, entity: int) -> bool:
        return (self.hooks.controlled(entity) or self.charges.controlled(entity)
                or entity in self.pins.values())

    def is_stunned(self, actor, *, tick: int | None = None) -> bool:
        entity = _source_entity(actor)
        current = self.tick if tick is None else tick
        sleep = self.sleeps.get(entity)
        frost = self.frost.sample(actor, current)
        return (self.captured(entity) or self.stuns.get(entity, 0) > current
                or (sleep is not None and sleep[1] > current) or (frost is not None and bool(frost[2])))

    def clear_pins(self, entity: int) -> None:
        for source, victim in list(self.pins.items()):
            if entity in (source, victim):
                del self.pins[source]

    def _hit_effect(self, target: Target, effect: int | None) -> None:
        """Resource-backed stun reconstruction; native immunity/presentation remain unverified."""
        if (effect not in (FLASHBANG_EFFECT, ANA_SLEEP_EFFECT) or self.vitals_of(target).dead
                or self.control_immune(target.owner)):
            return
        if effect == ANA_SLEEP_EFFECT:
            self.sleeps[target.entity] = (self.tick, self.tick + math.ceil(ANA_SLEEP_DURATION / TICK), False)
            self._interrupt(target)
            return
        variables = graph.mode_variables(self.match.game_map.mode_guid) or {}
        duration = 1.7 if expr.truthy(variables.get(FLASHBANG_MODE_FLAG)) else 0.8
        self._apply_stun(target, duration)

    def _apply_stun(self, target, duration, *, extend_only=False):
        deadline = self.tick + math.ceil(duration / TICK)
        if extend_only and deadline <= self.stuns.get(target.entity, 0):
            return
        self.stuns[target.entity] = max(deadline, self.stuns.get(target.entity, 0))
        self._interrupt(target)

    def _interrupt(self, target: Target) -> None:
        self.hooks.cancel_owner(target.entity)
        self.charges.interrupt(target.owner)
        self.punches.interrupt(target.owner)
        self.slams.interrupt(target.owner)
        self.walls.cancel_preview(target.owner)
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
        if hasattr(component.world, "velocity_requests"):
            component.world.velocity_requests.clear()

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
        self.recalls.command(player, command)
        if self.recalls.protected(player) or self.is_dead(player) or self.is_stunned(player):
            return
        if self.walls.command(player, command):
            return
        self.bubbles.command(player, command)
        self.hooks.command(player, command)
        if command is not None:
            self.earthshatter._start_shatter(player)
        self.brigitte_control.command(player, command)
        self.punches.command(player, command)
        self.uppercuts.command(player, command)
        self.slams.command(player, command)
        self.meteors.command(player, command)
        if self.meteors.protected(player):
            return
        if command is not None:
            for state, number, time, activation in shots:
                try:
                    self._shoot(player, command, state, number, time, activation=activation)
                except Exception:
                    log.exception("[game] %s: %s's shot failed", self.match.label(), player.name)
        try:
            self._melee(player, command, script)
        except Exception:
            log.exception("[game] %s: %s's melee failed", self.match.label(), player.name)

    def _genji_deflects(self, target, incoming) -> bool:
        """Reconstruct a frontal block while the authored Deflect State is on.

        Body-capsule admission and the 90-degree directional gate are a server
        approximation of the authored boxes. Eligibility belongs to each caller:
        bullet/projectile transfer, melee block, no blanket damage immunity.
        """
        owner = target.owner
        hero = getattr(owner, "hero", None)
        if getattr(hero, "guid", None) != 0x02E0000000000029 or self.vitals_of(target).dead:
            return False
        script = getattr(owner, "body_script", None)
        if script is None:
            return False
        for instance in script.component.instances.values():
            if instance.graph.index != 0x03A3:
                continue
            state = instance.states.get(10)
            if state is not None and state.active:
                deadline = deflection.state_deadline(instance.states.get(1))
                if not deflection.world_active(state, self.now, deadline):
                    continue
                facing = aim(owner.mover.yaw, owner.mover.pitch)
                return _dot(facing, incoming) < -EPSILON
        return False


    def _melee(self, player, command, script, *, view_tick: float | None = None) -> None:
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
            counters = self.melee_seen.setdefault(script.component.entity, {})
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
            origin = self._eye(player)
            centre = _along(origin, aim(*command.aim()), reach)
            self.venom.melee(player, origin, centre, radius, amount)
            for shield in self.bubbles.melee(player, origin, centre, radius):
                shield.damage(amount)
            for shield in rein_barrier.melee(self, player, origin, centre, radius):
                shield.damage(amount)
            if view_tick is None:
                view_tick = command.frame - command.view_delay(player.quanta) * 1e6 / player.quanta
            for target in self._enemies(player):
                shown = self.seen(target, view_tick)
                if shown is None:
                    continue
                a, b = shown.segment()
                if segment_distance(centre, a, b) - shown.radius > radius or amount <= 0.0:
                    continue
                # Reuse the rewound capsule for occlusion as well as reach. The exact
                # retail melee sweep is unverified; cast toward its nearest surface
                # instead of permitting a hit through solid map collision.
                if (self.walls.blocked(origin, tuple((x + y) / 2 for x, y in zip(a, b, strict=True)))
                        or not capsule_visible(self._collision(), origin, a, b, shown.radius)):
                    continue
                if self._genji_deflects(target, aim(*command.aim())):
                    continue
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
        for at in range(retained, tick + 1):
            self.now, self.tick = now - (tick - at) * TICK, at
            self._defense_healing_tick()
            self.orisa_shields.update(at)
            self.halt.update(at)
            self.walls.update(at)
            self.blizzard.update(at)
            self.sigma_barriers.update()
            self.hooks.update(at)
            self.charges.update()
            self.punches.update()
            self.slams.update()
            self.meteors.update()
            self.recalls.update()
            self.pulse_bombs.update(at)
            self.earthshatter.update()
            self.brigitte_control.update()
            if at < first:
                continue
            sigma.update_shields(self)  # expire defenses/grant shields before this world's incoming hits
            for rocket in list(self.rockets):
                if at == tick or rocket.last_tick is not None:
                    self._fly(rocket)
            self._bot_weapons(at)
            self.venom.update(at)
        self.bubbles.expire(now)
        self.brigitte_shields.update(now)
        self.sombra_hacks.update(tick)
        self.frost.update(tick)
        self.zarya_recovery.update(tick)
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
        for entity in [key for key, sleep in self.sleeps.items() if sleep[1] <= tick]:
            del self.sleeps[entity]
        for table in (self.history, self.vitals, self.replacement_ticks, self.stuns, self.sleeps):
            for entity in [key for key in table if key not in current]:
                del table[entity]

        for source, victim in list(self.pins.items()):
            if source not in current or victim not in current:
                del self.pins[source]

    # --- targets -----------------------------------------------------------------------------------

    def _targets(self) -> list[Target]:
        found = []
        for player in self.match.players:
            if (not (player.has_body and player.spawned and player.client is not None)
                    or player.controlled_mech is not None):
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

    def invulnerable(self, actor) -> bool:
        return (self.recalls.protected(actor) or self.meteors.protected(actor)
                or defenses.invulnerable(actor, self.now))

    def control_immune(self, actor) -> bool:
        if self.invulnerable(actor):
            return True
        fortify = defenses.fortify(actor, self.now)
        return fortify is not None and fortify[1]

    def _enemies(self, shooter, *, damageable: bool = True) -> list[Target]:
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
            if damageable and (self.recalls.protected(target.owner) or self.meteors.protected(target.owner)
                               or defenses.intangible(target.owner, self.now)):
                continue
            found.append(target)
        return found

    def record_discontinuity(self, player) -> None:
        """Replace the current sample and mark it as a hard boundary for rewind."""
        target = next((entry for entry in self._targets() if entry.owner is player), None)
        if target is None:
            return
        history = self.history.setdefault(target.entity, deque(maxlen=HISTORY))
        if history and history[-1][0] == self.tick:
            history.pop()
        history.append((self.tick, target.feet, target.height, target.eye, True))

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
        if after is None or after is before or len(after) == 5:
            _, feet, height, eye = before[:4]
        else:
            share = (view_tick - before[0]) / (after[0] - before[0])
            feet = tuple(a + (b - a) * share for a, b in zip(before[1], after[1], strict=True))
            height, eye = (before[2], before[3]) if share < 0.5 else (after[2], after[3])
        return Target(target.entity, target.team, feet, height, target.radius, eye, target.owner)

    # --- shots -------------------------------------------------------------------------------------

    def _bot_weapons(self, tick: int) -> None:
        """Advance the weapon bodies of the map's firing bots and consume their volley reports."""
        from ow174.game import npc_control

        for bot in tuple(self.match.bots):
            script = bot.body_script
            if bot.detached_bomb:
                from ow174.game import dva_body

                dva_body.step_fuse(self.match, bot, tick)
                continue
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
        if isinstance(player, bots.Bot):
            x, y, z = player.position
            mover = player.control_mover
            height = eye_height(player.body, mover.crouched) if mover is not None else EYE
        else:
            x, y, z = player.mover.position
            height = eye_height(player.hero.body, player.mover.crouched)
        return (x, y + height, z)

    def _shoot(
        self, shooter, command, state, number: int, time: int,
        *, activation: tuple[int, int], snapshot: Snapshot | None = None, view_tick: float | None = None,
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
            if command is None:
                origin, direction = (x, y + EYE, z), aim(round(shooter.yaw), round(shooter.pitch))
            else:
                origin, direction = self._eye(shooter), aim(*command.aim())
        else:
            origin, direction = self._eye(shooter), aim(*command.aim())
        from ow174.game import weapon_launch

        directions = weapon_launch.directions(shooter, state, direction, number, time, activation)
        direction = directions[0]
        direct = hit_def(state, node.field("m_6396149F"))
        entity_def = (node.field("m_projectileEntity") or {}).get("m_entityDef")
        asset = state.evaluate(entity_def, watch=False) if entity_def is not None else None
        definition = asset.guid if isinstance(asset, expr.Asset) else None
        speed = field("m_projectileSpeed")
        if speed:
            pulse = pulse_bomb.is_pulse(shooter, state)
            if pulse:
                direction = pulse_bomb.launch_direction(state, direction)
            flash_gravity = None if npc else flashbang.flashbang_gravity(shooter, state)
            motion = (sigma_accretion.accretion_launch(state, direction)
                      if not npc and shooter.hero.guid == sigma_accretion.HERO
                      and shooter.body_script.component is state.instance.component else None)
            gravity = flash_gravity
            if flash_gravity is not None:
                direction = flashbang.flashbang_direction(state, direction)
                directions = (direction,)
            elif motion is not None:
                gravity, direction = motion
                directions = (direction,)
            lifetime = field("m_projectileLifetime", 5.0) or 5.0
            reach = field("m_FEC435C6")
            if reach:
                lifetime = min(lifetime, reach / speed)
            blast = hit_def(state, node.field("m_D2D11CE9"))
            shot = None
            if definition is not None:
                shot = self.projectiles.fired(
                    shooter, state, shot_frame, definition, lifetime, origin, direction,
                    activation=activation, snapshot=snapshot,
                )
            if pulse:
                self.pulse_bombs.launch(
                    shooter, state, origin, direction, speed, lifetime, direct, blast, shot,
                    definition=definition, frame=shot_frame,
                )
                return
            if self.blizzard.launch(shooter, state, origin, direction, definition, speed, lifetime, shot):
                return
            if self.halt.launch(shooter, state, origin, direction, definition, speed, lifetime, shot):
                return
            if self.orisa_shields.throw(shooter, state, origin, direction, definition, speed, lifetime, shot):
                return
            if not npc and self.venom.throw(shooter, state, origin, direction, shot):
                return
            damaging = (direct is not None and direct.amount > 0.0) or (
                blast is not None and blast.amount > 0.0
            )
            if damaging or shot is not None:
                if len(directions) > 1 and shot is not None:
                    raise ValueError('multi-pellet prediction is unsupported for this authored sync mode')
                for direction in directions:
                    self.rockets.append(
                        Rocket(
                            shooter, shooter.team, origin, direction, speed, lifetime, direct, blast, shot,
                            last_tick=self.tick if npc else None,
                            definition=definition,
                            gravity=gravity if gravity is not None else 0.0,
                            velocity=(tuple(expr.f32(value * speed) for value in direction)
                                      if gravity is not None else None),
                            detonate_on_expiry=flash_gravity is not None,
                            target_limit=mei_frost.projectile_target_limit(state),
                            accretion_source=(
                                (shooter.body, state.instance.id) if motion is not None else None
                            ),
                        )
                    )
            return
        if direct is None or direct.amount <= 0.0:
            return
        reach = field("m_FEC435C6", RANGE) or RANGE
        # The input supplies this simulation frame's aim and view time. Notices use the volley clock.
        if view_tick is None:
            view_tick = self.tick
            if command is not None:
                view_tick = command.frame - command.view_delay(shooter.quanta) * 1e6 / shooter.quanta
        travelled = 0.0
        for bounce in range(8):  # selected bounded multi-Deflect policy
            best = None
            for target in self._enemies(shooter):
                shown = self.seen(target, view_tick)
                if shown is None:
                    continue
                a, b = shown.segment()
                t = ray_capsule(origin, direction, a, b, shown.radius)
                if t is not None and t <= reach and (best is None or t < best[0]):
                    best = (t, target, shown)
            mine_hit = self.venom.intersect(shooter, origin, direction, reach)
            limit = min(reach, best[0] if best else reach, mine_hit[0] if mine_hit else reach)
            solid = ray_world(self._collision(), origin, direction, limit)
            ice = self.walls.cast(origin, direction, solid if solid is not None else limit)
            if ice is not None and (solid is None or ice[0] < solid):
                solid = ice[0]
            else:
                ice = None
            barrier = self._shield_hit(shooter, origin, direction, limit)
            if barrier is not None and solid is not None and solid <= barrier[0]:
                barrier = None
            cutoff = min(limit if solid is None else solid, barrier[0] if barrier else limit)
            absorbed = self._absorption_hit(shooter, origin, direction, cutoff, definition)
            if (absorbed is not None and (solid is None or absorbed[0] + EPSILON < solid)
                    and (barrier is None or absorbed[0] + EPSILON < barrier[0])):
                self._absorbed(absorbed, direct.amount * direct.scale(travelled + absorbed[0]))
                return
            if barrier is not None:
                distance, shield = barrier
                shield.damage(direct.amount * direct.scale(travelled + distance))
                return
            if ice is not None:
                self.walls.damage(ice[1], shooter, direct.amount * direct.scale(travelled + ice[0]))
                return
            if mine_hit is not None and (best is None or mine_hit[0] < best[0]):
                distance, mine = mine_hit
                wall = ray_world(self._collision(), origin, direction, distance)
                if wall is None or wall >= distance:
                    self.venom.damage(mine, direct.amount * direct.scale(travelled + distance))
                return
            if best is None:
                return
            t, target, shown = best
            wall = ray_world(self._collision(), origin, direction, t)
            if wall is not None and wall < t:
                return
            if direct.deflectable and self._genji_deflects(target, direction):
                shooter = target.owner
                origin = _along(origin, direction, t)
                direction = aim(shooter.mover.yaw, shooter.mover.pitch)
                origin = _along(origin, direction, 1e-4)
                travelled += t
                reach -= t
                view_tick, shot_frame = self.tick, self.tick
                npc = isinstance(shooter, bots.Bot)
                continue
            hit_y = origin[1] + direction[1] * t
            critical = (hit_y >= shown.feet[1] + shown.eye - HEAD_BELOW
                        and defenses.fortify(target.owner, self.now) is None)
            amount = direct.amount * direct.scale(travelled + t) * (direct.critical if critical else 1.0)
            self._damage(
                shooter, target, amount, critical and direct.critical > 1.0, direct.loadout, shot_frame,
                world_tick=self.tick if npc or bounce else None,
                effect=direct.effect,
            )
            return

    def _genji_flight_hit(self, shooter, origin, direction, reach, definition, *, team=None):
        """Selected tag145/capsule reconstruction for non-damaging projectile flight."""
        if not self.projectile_catalog.absorbable(definition):
            return None
        side = shooter.team if team is None else team
        best = None
        for target in self._targets():
            if (target.owner is shooter or self.vitals_of(target).dead
                    or (not self.match.game_map.free_for_all and target.team == side)
                    or not self._genji_deflects(target, direction)):
                continue
            distance = ray_capsule(origin, direction, *target.segment(), target.radius)
            if distance is not None and distance <= reach and (best is None or distance < best[0]):
                best = (distance, target)
        return best

    def _absorption_hit(self, shooter, origin, direction, reach, definition, *, team=None):
        matrix = self._matrix_hit(shooter, origin, direction, reach, definition, team=team)
        grasp = (sigma.grasp_intersection(self, shooter, origin, direction, reach, team=team)
                 if self.projectile_catalog.absorbable(definition) else None)
        if grasp is not None and (matrix is None or grasp[0] < matrix):
            return grasp
        return None if matrix is None else (matrix, None)

    @staticmethod
    def _absorbed(contact, amount):
        if contact[1] is not None:
            sigma.absorbed(contact[1], amount)

    def _shield_hit(self, shooter, origin, direction, reach, *, team=None, radius=0.0):
        probe = reach + radius
        bubble = self.bubbles.first(shooter, origin, direction, probe, team=team)
        rein = rein_barrier.intercept(self, shooter, origin, direction, probe, team=team)
        brig = self.brigitte_shields.first(shooter, origin, direction, reach, team=team, radius=radius)
        orisa = self.orisa_shields.first(shooter, origin, direction, probe, team=team)
        sig = self.sigma_barriers.first(shooter, origin, direction, probe, team=team)
        contacts = ((bubble, radius), (rein, radius), (brig, 0), (orisa, radius), (sig, radius))
        hits = ((max(0.0, hit[0] - correction), hit[1])
                for hit, correction in contacts
                if hit is not None)
        return min(hits, key=lambda hit: hit[0], default=None)

    def _matrix_hit(self, shooter, origin, direction, reach, definition, *, team=None):
        """Selected current-pose Matrix box host; native attachment/filter semantics remain open."""
        from ow174.game import dva_body

        if not self.projectile_catalog.absorbable(definition):
            return None
        side = shooter.team if team is None else team
        best = None
        for target in self._targets():
            if (target.owner is shooter or self.vitals_of(target).dead
                    or (not self.match.game_map.free_for_all and target.team == side)):
                continue
            actor = target.owner
            now = self.now
            if isinstance(actor, bots.Bot):
                mover = actor.control_mover
                if (actor.body != dva_body.MECH or mover is None or not actor.owner
                        or actor.body_script is None):
                    continue
                now += actor.clock_offset * actor.body_script.component.frame_ms / 1000
            else:
                if actor.hero.guid != dva_body.DVA or actor.hero.body != dva_body.MECH:
                    continue
                mover = actor.mover
            anchor = self._eye(actor)
            distance = deflection.matrix_distance(
                actor.body_script, origin, direction, reach, anchor, aim(mover.yaw, mover.pitch), now=now,
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
            # Preserve the parent's fixed16ms acceleration order on host catch-up.
            for _ in range(elapsed):
                if rocket not in self.rockets:
                    break
                self._fly_step(rocket, min(TICK, rocket.left))
            return
        self._fly_step(rocket, min(elapsed * TICK, rocket.left))

    def _fly_step(self, rocket: Rocket, duration: float) -> None:
        if duration <= 0.0:
            self._landed(rocket)
            if rocket.detonate_on_expiry:
                self._explode(rocket, rocket.position)
            return
        if rocket.velocity is not None:
            rocket.velocity = flashbang.gravity_velocity(rocket.velocity, rocket.gravity, duration)
            rocket.speed = math.sqrt(sum(value * value for value in rocket.velocity))
            if rocket.speed:
                rocket.direction = tuple(value / rocket.speed for value in rocket.velocity)
        # Sweep the remaining flight even when the range or lifetime ends inside this tick.
        step = rocket.speed * duration
        best = None
        for target in self._enemies(rocket.shooter):
            if target.entity in rocket.hit_entities:
                continue
            a, b = target.segment()
            t = ray_capsule(rocket.position, rocket.direction, a, b, target.radius)
            if t is not None and t <= step and (best is None or t < best[0]):
                best = (t, target)
        struck_mine = None
        if rocket.direct is not None and rocket.direct.amount > 0:
            mine_hit = self.venom.intersect(rocket.shooter, rocket.position, rocket.direction, step)
            if mine_hit is not None and (best is None or mine_hit[0] < best[0]):
                distance, struck_mine = mine_hit
                best = (distance, None)
        wall = ray_world(self._collision(), rocket.position, rocket.direction, step)
        ice = self.walls.cast(rocket.position, rocket.direction, step if wall is None else wall)
        if ice is not None and (wall is None or ice[0] < wall):
            wall = ice[0]
        else:
            ice = None
        hit_wall = wall is not None and (best is None or wall < best[0])
        if hit_wall:
            best = (wall, None)
            struck_mine = None
        reach = best[0] if best else step
        barrier = self._shield_hit(
            rocket.shooter, rocket.position, rocket.direction, reach, team=rocket.team,
        )
        if barrier is not None and hit_wall and wall <= barrier[0]:
            barrier = None
        absorbed = self._absorption_hit(
            rocket.shooter, rocket.position, rocket.direction, barrier[0] if barrier else reach,
            rocket.definition, team=rocket.team,
        ) if rocket.accretion_source is None else None
        if (absorbed is not None and (not hit_wall or absorbed[0] + EPSILON < wall)
                and (barrier is None or absorbed[0] + EPSILON < barrier[0])):
            amount = ((rocket.direct.amount if rocket.direct else 0)
                      + (rocket.blast.amount if rocket.blast else 0))
            self._absorbed(absorbed, amount)
            rocket.flown += absorbed[0] / rocket.speed
            self.rockets.remove(rocket)
            if rocket.shot is not None:
                self.projectiles.absorbed(rocket.shot)
            return
        if barrier is not None:
            distance, shield = barrier
            shield.damage((rocket.direct.amount if rocket.direct else 0.0)
                          + (rocket.blast.amount if rocket.blast else 0.0))
            rocket.flown += distance / rocket.speed
            self._landed(rocket)
            return
        if best is None:
            rocket.position = _along(rocket.position, rocket.direction, step)
            rocket.flown += duration
            rocket.left -= duration
            if rocket.left <= 0.0:
                self._landed(rocket)
                if rocket.detonate_on_expiry:
                    self._explode(rocket, rocket.position)
            return
        t, struck = best
        rocket.flown += t / rocket.speed
        point = _along(rocket.position, rocket.direction, t)
        if (struck is not None and rocket.direct is not None and rocket.direct.deflectable
                and rocket.accretion_source is None and self._genji_deflects(struck, rocket.direction)):
            if rocket.shot is not None:
                self.projectiles.absorbed(rocket.shot)
                rocket.shot = None
            rocket.shooter = struck.owner
            rocket.team = struck.team
            rocket.direction = aim(struck.owner.mover.yaw, struck.owner.mover.pitch)
            if rocket.velocity is not None:
                rocket.velocity = tuple(expr.f32(axis * rocket.speed) for axis in rocket.direction)
            rocket.position = _along(point, rocket.direction, 1e-4)
            rocket.left -= t / rocket.speed
            return
        piercing = struck is not None and len(rocket.hit_entities) + 1 < rocket.target_limit
        if not piercing:
            self._landed(rocket)
        if struck_mine is not None:
            self.venom.damage(struck_mine, rocket.direct.amount)
        if struck is not None and rocket.direct is not None and rocket.direct.amount > 0.0:
            old_hp = self.vitals_of(struck).total
            self._damage(
                rocket.shooter, struck, rocket.direct.amount, False, rocket.direct.loadout, self.tick,
                world_tick=self.tick,
                effect=rocket.direct.effect,
            )
            dealt = old_hp - self.vitals_of(struck).total
            if rocket.accretion_source is not None and dealt > 0:
                sigma_accretion.accretion_hit(self, rocket, struck, dealt)
                sigma_accretion.apply_accretion_impulse(self, rocket, struck, rocket.direct)
        if piercing:
            rocket.hit_entities.add(struck.entity)
            rocket.position = _along(point, rocket.direction, 1e-4)
            elapsed = t / rocket.speed
            rocket.left -= elapsed
            if duration > elapsed and rocket.left > 0:
                self._fly_step(rocket, min(duration - elapsed, rocket.left))
            return
        self._explode(rocket, point, hit_wall=hit_wall, struck=struck)
        if hit_wall and ice is not None and rocket.direct is not None:
            self.walls.damage(ice[1], rocket.shooter, rocket.direct.amount)

    def _explode(self, rocket: Rocket, point: tuple, *, hit_wall: bool = False, struck=None) -> None:
        blast = rocket.blast
        if blast is None or blast.amount <= 0.0:
            return
        # A ray starting exactly on the impact plane can miss that same plane.
        # Keep mine splash visibility on the incoming side of static collision.
        visible_from = _along(point, rocket.direction, -0.001) if hit_wall else point
        self.venom.explode(rocket.shooter, point, blast, visible_from=visible_from)
        for target in self._enemies(rocket.shooter):
            a, b = target.segment()
            distance = max(0.0, segment_distance(point, a, b) - target.radius)
            scalar = blast.splash(distance)
            centre = tuple((x + y) / 2 for x, y in zip(a, b, strict=True))
            if (scalar > 0.0 and not self.walls.blocked(visible_from, centre)
                    and capsule_visible(self._collision(), visible_from, a, b, target.radius)):
                old_hp = self.vitals_of(target).total
                self._damage(
                    rocket.shooter, target, blast.amount * scalar, False, blast.loadout, self.tick,
                    world_tick=self.tick,
                    effect=blast.effect,
                )
                if (rocket.accretion_source is not None and target != struck
                        and self.vitals_of(target).total < old_hp):
                    sigma_accretion.apply_accretion_impulse(self, rocket, target, blast, point, distance)

        self.walls.splash(rocket.shooter, point, blast, visible_from)

    def _landed(self, rocket: Rocket) -> None:
        """The projectile ended (hit, range, lifetime): the predicted one's release follows."""
        if rocket in self.rockets:
            self.rockets.remove(rocket)
        if rocket.shot is not None:
            self.projectiles.flown(rocket.shot, rocket.flown)

    def _defense_healing_tick(self) -> None:
        for healer in self.match.players:
            if (healer.hero.guid not in (defenses.ZENYATTA, defenses.MEI) or not healer.spawned
                    or not healer.has_body or healer.gone or self.is_dead(healer)):
                continue
            profile = defenses.healing(healer, self.now - TICK)
            if profile is None:
                continue
            start, end, rate, self_only, radius, offset = profile
            seconds = min(self.now, end) - max(self.now - TICK, start)
            if seconds <= 0:
                continue
            centre = tuple(x + y for x, y in zip(healer.mover.position, offset, strict=True))
            self.heal_area(healer, centre, radius, seconds * rate, self_only=self_only, los=not self_only)

    def heal_area(
        self, healer, centre, radius: float, amount: float, *, self_only: bool = False, los: bool = False,
    ) -> None:
        """Heal the healer and his teammates (everyone's own team in a free-for-all is his alone) whose body
        is within `radius` of `centre` (unverified: to the body's capsule)."""
        for target in self._targets():
            ally = not self.match.game_map.free_for_all and target.team == healer.team
            if target.owner is not healer and (self_only or not ally):
                continue
            vitals = self.vitals_of(target)
            if vitals.dead:
                continue
            a, b = target.segment()
            if segment_distance(centre, a, b) - target.radius > radius:
                continue
            if los and target.owner is not healer and not capsule_visible(
                self._collision(), centre, a, b, target.radius,
            ):
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
        *, world_tick: int | None = None, effect: int | None = None,
    ) -> None:
        vitals = self.vitals_of(target)
        if vitals.dead or amount <= 0.0 or self.invulnerable(target.owner):
            return
        if self.zarya_barriers.intercept(target.owner, amount, _source_entity(shooter)):
            return
        fortify = defenses.fortify(target.owner, self.now)
        if fortify is not None:
            amount *= fortify[0]
        dealt = vitals.damage(amount)
        if dealt <= 0.0:
            return
        self.zarya_recovery.damaged(target.owner, self.match.tick if world_tick is None else world_tick)
        sleep = self.sleeps.get(target.entity)
        if sleep is not None:
            start, end, waking = sleep
            if not waking and self.tick < end and (self.tick - start) * TICK > ANA_SLEEP_DAMAGE_GRACE:
                wake = self.tick + math.ceil(ANA_SLEEP_WAKE / TICK)
                self.sleeps[target.entity] = (start, min(end, wake), True)
        killed = vitals.now[HEALTH_POOL] <= EPSILON
        if killed:
            self.clear_pins(target.entity)
            vitals.now[HEALTH_POOL] = 0.0
            vitals.died_at = self.now
            vitals.death_tick = self.match.tick if world_tick is None else world_tick
            vitals.killer = _source_entity(shooter)
        if not isinstance(shooter, bots.Bot):
            flags = (CRITICAL if critical else 0) | (KILLING if killed else 0)
            self._tell_hit(shooter, target, dealt, flags, frame)
            shooter.stats.add(stats.HERO_DAMAGE, dealt)
            if shooter.hero.guid == venom.HERO and shooter.body_script is not None:
                shooter.body_script.award_damage_charge(dealt)
        victim = target.owner
        if (not killed and loadout == mei_frost.SPRAY_LOADOUT
                and getattr(getattr(shooter, "hero", None), "guid", None) == mei_frost.HERO):
            self.frost.apply(shooter, target, tick=self.tick)
        sombra_control.received_damage(victim, source=_source_entity(shooter), now=self.now)
        if killed and isinstance(victim, bots.Bot):
            # The NPC death script owns its terminal health change and death event ordering.
            self._bot_died(shooter, victim, critical, loadout, vitals.death_tick)
            return
        self._sync_body_health(victim, vitals)
        if killed:
            self._died(shooter, victim, critical, loadout)
        # A fully intercepted event cannot apply its body's hit effect.
        self._hit_effect(target, effect)

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
        self.clear_pins(bot.entity)
        self.hooks.retire(bot.entity)
        self.stuns.pop(bot.entity, None)
        self.frost.retire(bot.entity, bot)
        self.sleeps.pop(bot.entity, None)
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
                tick = max(tick + bot.clock_offset, script.frame)
            event = script.die(tick, params, _source_entity(killer))
            if bot.owner:
                from ow174.game import dva_body

                dva_body.queue_demech_return(self.match, bot)
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
            events = script.advance_death(tick + bot.clock_offset, int(BOT_RESPAWN / TICK))
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
        self.sombra_hacks.retire(victim.body)
        self.frost.retire(victim.body, victim)
        self.sleeps.pop(victim.body, None)
        self.halt.retire(victim.body)
        self.walls.cancel_preview(victim)
        self.hooks.retire(victim.body)
        self.charges.retire(victim.body)
        self.punches.retire(victim.body)
        self.recalls.retire(victim.body)
        self.meteors.retire(victim.body)
        self.slams.retire(victim.body)
        self.uppercuts.retire(victim.body)
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
