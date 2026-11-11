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

from ow174.game import (
    amplification,
    ana,
    baptiste_field,
    bots,
    contacts,
    damage_charge,
    defenses,
    discord,
    dva_matrix,
    echo_duplicate,
    graviton,
    hook,
    lucio_barrier,
    lucio_soundwave,
    mei,
    mei_blizzard,
    mei_frost,
    mei_wall,
    mercy_beam,
    meteor_strike,
    nano,
    npc_context,
    observers,
    orisa_barrier,
    orisa_halt,
    orisa_supercharger,
    pools,
    projectiles,
    reaper,
    reinhardt_charge,
    rocket_punch,
    seismic_slam,
    sigma_accretion,
    sigma_flux,
    sigma_grasp,
    sombra,
    stats,
    steel_trap,
    voicelines,
    whole_hog,
    winston_barrier,
    winston_jump,
    winston_primal,
    winston_tesla,
    world,
    zarya_barriers,
)
from ow174.game.bits import BitWriter
from ow174.game.collision import MOVER, World
from ow174.game.commands import Command
from ow174.game.mover import FELL, mover_data, quantise
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
EYE_STANDING = {0x0400000000000022: 1.55, 0x04000000000002A3: 1.425}
EYE_CROUCHED = {0x04000000000003CF: 1.085, 0x0400000000000022: 1.085,
                0x04000000000002A3: 0.9975}  # explicit FirstPerson XML; unknown bodies use fallback
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
    return EYE_CROUCHED.get(body, EYE_CROUCHED_DEFAULT) if crouched else EYE_STANDING.get(body, EYE)


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
class TemporaryShield:
    pool_id: int
    most: float
    now: float


@dataclass
class _MercyDamageCredit:
    staff: runtime.Instance
    base_fraction: float
    amplified_targets: set[int] = field(default_factory=set)


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
        self.nano_until = 0.0

    @property
    def dead(self) -> bool:
        return self.died_at is not None

    @property
    def total(self) -> float:
        return sum(self.now.values()) + sum(pool.now for pool in self.temporary_shields.values())

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
            ids = [*self.ids.values(),
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

    def damage(self, amount: float, *, floor: float = 0.0, bypass_armour: bool = False,
               beam: bool = False, preview: bool = False) -> float:
        """Take a hit from shields, then armour, then health. Returns the actual pool loss.

        Per-hit armour and the continuous beam multiplier are reconstructed.
        """
        left = max(0.0, amount)
        taken = 0.0
        available = max(0.0, self.total - floor) if floor else math.inf
        for tag in reversed(self.temporary_shields):
            used = min(left, self.temporary_shields[tag].now, max(0.0, available - taken))
            if not preview:
                self.decay_temporary_shields(tag, used)
            left -= used
            taken += used
        for kind in (SHIELDS_POOL, ARMOUR_POOL, HEALTH_POOL):
            if left <= 0.0:
                break
            if kind == ARMOUR_POOL and self.now[kind] > 0.0 and not bypass_armour:
                # Reconstructed continuous beam mitigation, scoped to callers
                # that supply a per-frame damage slice.
                left = left * 0.8 if beam else max(left - ARMOUR_BLOCK, left / 2)
            take = min(left, self.now[kind], max(0.0, available - taken))
            if take > 0.0:
                if not preview:
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
    damage_tags: tuple[expr.Asset, ...] = ()

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
        critical=value("m_A872C70E", 1.0),
        rings=tuple(sorted(rings)),
        effect=effect.guid if isinstance(effect, expr.Asset) else None,
        damage_tags=damage_charge.event_tags(state, modify),
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


class ChargeQueryContext(runtime.InstanceContext):
    def __init__(self, instance, target):
        super().__init__(instance, None)
        self.target = target

    def param(self, var):
        return expr.Entity(self.target.entity) if var == 8 else super().param(var)

    def member(self, entity, var):
        if entity != self.target.entity:
            return super().member(entity, var)
        script = getattr(self.target.owner, "body_script", None)
        slot = script.component.entity_var(var, create=False) if script is not None else None
        return slot.value() if slot is not None else None


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
    ana_grenade: bool = False  # captured source eligibility, not a later mutable hero lookup
    reflected: bool = False
    definition: int | None = None
    target_limit: int = 1
    hit_entities: set = field(default_factory=set)
    amplified: bool = False
    gravity: float = 0.0
    velocity: tuple | None = None
    accretion_source: tuple[int, int] | None = None
    accretion_force: tuple | None = None
    accretion_splash_force: tuple | None = None
    actor_modifiers: tuple[float, float] = (1.0, 0.0)  # other outgoing scale, Supercharger bonus
    graviton_body: int | None = None
    duplicate_body: int | None = None  # copied Soldier's source body, even when unleased


class Combat:
    """The match's health, hits, deaths and respawns."""

    def __init__(self, match) -> None:
        from ow174.game import brigitte_shield, sigma_barrier, symmetra_barrier

        self.sigma_barriers = sigma_barrier.Barriers(self)
        self.brigitte_shields = brigitte_shield.Shields(self)
        self.hooks = hook.Hooks(self)
        self.traps = steel_trap.Traps(self)
        self.discord = discord.Orbs(self)
        self.amplification = amplification.Matrices(self)
        self.walls = mei_wall.Walls(self)
        self.blizzard = mei_blizzard.Blizzard(self)
        self.frost_hits = {}
        self.frost_sources = {}
        self.symmetra_barriers = symmetra_barrier.Barriers(self)
        self.zarya_bubbles = zarya_barriers.Barriers(self)
        self.orisa_barriers = orisa_barrier.Barriers(self)
        self.winston_bubbles = winston_barrier.Barriers(self)
        self.winston_jumps = {}
        self.winston_jump_seen = {}
        self.winston_landing_pending = {}
        self.primal_bonus = {}
        self.echo_duplicates = echo_duplicate.Duplicates(self)
        self.grasp_grants = {}
        self.grasp_shields = {}
        self.match = match
        self.vitals: dict[int, Vitals] = {}  # body or bot entity -> its health
        self.history: dict[int, deque] = {}  # entity -> (tick, feet, height, eye) of the last HISTORY ticks
        self.replacement_ticks: dict[int, int] = {}  # replacement body -> authoritative world tick
        self.rockets: list[Rocket] = []
        self.respawns: list[tuple[float, object]] = []  # (when, a player or a bot)
        self.next_bot = bots.BOT_ENTITY + len(match.bots)
        self.now = 0.0
        self.tick = 0
        self.melee_seen: dict[int, dict[tuple[int, ...], int]] = {}  # counters by instance/state[/target]
        self.npc_impulses = {}  # body -> (last world tick, velocity)
        self.impulse_landings = {}  # player body -> temporary (world tick, owner frame)
        self.slams = {}
        self.meteors = {}
        self.punch_dashes = {}
        self.punch_walls = {}
        self.doomfist_shields = {}
        self.sound_barriers = lucio_barrier.Barriers(self)
        self.baptiste_fields = baptiste_field.Fields(self)
        self.superchargers = orisa_supercharger.Superchargers(self)
        self.halts = orisa_halt.Halts(self)
        self.gravitons = graviton.Gravitons(self)
        self.charges = {}  # Reinhardt body -> active Charge
        self.pins: dict[int, int] = {}  # declared manual Assassin source -> controlled victim
        self.ana_sleeps: dict[int, dict] = {}  # recipient -> normal sleep/wake world ticks
        self.stuns: dict[int, int] = {}  # body identity -> exclusive world-tick deadline
        self.reaper_blossoms: dict[int, dict] = {}  # body -> channel clock and per-target effect clocks
        self.reaper_shadows: dict[int, dict] = {}  # body -> selector edges and locked cast destination
        self.defense_heals: dict[int, dict] = {}  # caster body -> bounded healing interval and settled time
        self.heal_modifiers: dict[int, dict] = {}  # recipient body -> independent anti/boost intervals
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
        sigma_flux.interrupt_flux_lift(self, player, retiring=True)
        self.echo_duplicates.retire(player)
        self.zarya_bubbles.retire(player.body)
        self.walls.retire(player.body)
        self.sigma_barriers.retire(player.body)
        self.brigitte_shields.retire(player.body)
        self.hooks.retire(player.body)
        self.traps.retire(player.body)
        self.discord.retire(player.body)
        self._clear_frost(player)
        self.blizzard.retire(player.body)
        self.symmetra_barriers.retire(player.body)
        self.baptiste_fields.retire(player.body)
        self.superchargers.retire(player.body)
        self.halts.retire(player.body)
        self.sound_barriers.retire(player.body)
        self.slams.pop(player.body, None)
        self.meteors.pop(player.body, None)
        rocket_punch.retire(self, player.body, replaced=True)
        sigma_grasp.retire(self, player.body)
        self.vitals.pop(player.body, None)
        self.history.pop(player.body, None)
        self.replacement_ticks.pop(player.body, None)
        self.melee_seen.pop(player.body, None)
        self.npc_impulses.pop(player.body, None)
        self.impulse_landings.pop(player.body, None)
        self.winston_jumps.pop(player.body, None)
        self.winston_jump_seen.pop(player.body, None)
        self.winston_landing_pending.pop(player.body, None)
        self.primal_bonus.pop(player.body, None)
        self.stuns.pop(player.body, None)
        self.clear_pins(player.body)
        self.ana_sleeps.pop(player.body, None)
        self.reaper_blossoms.pop(player.body, None)
        self.reaper_shadows.pop(player.body, None)
        self.defense_heals.pop(player.body, None)
        self.heal_modifiers.pop(player.body, None)
        # The owner's leased projectiles and their observer copies retire with the old body.
        self.rockets[:] = [
            rocket for rocket in self.rockets
            if rocket.shooter is not player or (
                rocket.duplicate_body != player.body and rocket.shot is None
                and not rocket.ana_grenade and not rocket.reflected
                and not (rocket.direct and rocket.direct.effect == ana.SLEEP_EFFECT)
            )
        ]
        self.respawns[:] = [(when, who) for when, who in self.respawns if who is not player]

    def record_replacement(self, body: int, *, tick: int | None = None) -> None:
        """Keep the creation boundary independent of the rolling pose history."""
        self.replacement_ticks[body] = self.match.tick if tick is None else tick
        self.stuns.pop(body, None)
        self.clear_pins(body)
        self.ana_sleeps.pop(body, None)

    def is_stunned(self, actor, *, tick: int | None = None) -> bool:
        from ow174.game import npc_navigation

        entity = _source_entity(actor)
        current = self.tick if tick is None else tick
        return (entity in self.pins.values() or self.hooks.controlled(entity)
                or reinhardt_charge.pinned(self, entity)
                or (not isinstance(actor, bots.Bot) and npc_navigation.charge_carries(self.match, entity))
                or self.frozen(actor, tick=current)
                or self.stuns.get(entity, 0) > current
                or self.ana_sleeps.get(entity, {}).get("end", 0) > current)

    def clear_pins(self, entity):
        reinhardt_charge.retire(self, entity)
        for source, victim in list(self.pins.items()):
            if entity in (source, victim):
                del self.pins[source]

    def _hit_effect(self, target: Target, effect: int | None, *, stun_ms: int | None = None) -> None:
        """Resource-backed stun reconstruction; native immunity/presentation remain unverified."""
        if ((stun_ms is None and effect not in (FLASHBANG_EFFECT, ana.SLEEP_EFFECT))
                or target.entity != _source_entity(target.owner)
                or self.echo_duplicates.restored.get(target.entity) == self.tick
                or self.vitals_of(target).dead
                or reaper.intangible(target.owner) or defenses.invulnerable(target.owner)):
            return
        if self.fortify_control_immune(target):
            return
        if effect == ana.SLEEP_EFFECT:
            # Normal224E/676B consumer; native filters/stacking/messages remain open.
            self.ana_sleeps[target.entity] = {
                "start": self.tick, "end": self.tick + math.ceil(ana.SLEEP_DURATION / TICK),
                "waking": False,
            }
        else:
            variables = graph.mode_variables(self.match.game_map.mode_guid) or {}
            duration = 1.7 if expr.truthy(variables.get(FLASHBANG_MODE_FLAG)) else 0.8
            deadline = self.tick + math.ceil((duration if stun_ms is None else stun_ms / 1000) / TICK)
            self.stuns[target.entity] = max(deadline, self.stuns.get(target.entity, 0))
        self._interrupt(target, preserve_dragonblade=effect == ana.SLEEP_EFFECT)

    def fortify_control_immune(self, target: Target) -> bool:
        if self._fortify_scale(target) is None:
            return False
        control = target.owner.body_script.component.entity_var(9161, create=False)
        return control is not None and expr.truthy(control.value())

    def _interrupt(self, target, *, preserve_dragonblade=False):
        """Shared authored exits for accepted stun, pin and hook control."""
        self.hooks.cancel_owner(target.entity)
        sigma_flux.interrupt_flux_lift(self, target.owner)
        script = getattr(target.owner, "body_script", None)
        if script is None:
            return
        winston_primal.interrupt(script)
        component = script.component
        component.edges([(value, False) for value in tuple(component.held)])
        self._interrupt_reaper(target.entity, target.owner)
        sombra.cancel_hack(target.owner)
        orisa_supercharger.interrupt_cast(component)
        reinhardt_charge.interrupt(self, target, script)
        if not isinstance(target.owner, bots.Bot):
            seismic_slam.interrupt(self, target.owner)
            winston_jump.cancel(self, target.owner, keep_fall=True)
        dash = self.punch_dashes.get(target.entity)
        if dash is not None and not dash.finished:
            rocket_punch.finish(target.owner, dash)
        for instance in list(component.instances.values()):
            for state in list(instance.states.values()):
                if isinstance(state, nodes.Ability):
                    state.presses.clear()
                    blade_timer = instance.states.get(2) if instance.graph.index == 0x0317 else None
                    if preserve_dragonblade and blade_timer is not None and blade_timer.active:
                        # Sleep interrupts the swing while the drawn sword's clock keeps running.
                        continue
                    state.stop()  # use authored exit/cooldown paths for running channels
        component.run_now()
        component.shots.clear()

    def _interrupt_reaper(self, entity, player):
        reaper.interrupt_blossom(player)
        channel = self.reaper_blossoms.get(entity)
        if channel is not None and not channel["ended"]:
            channel["end"] = min(channel["end"], self.now)
            channel["ended"] = True
            channel["targets"].clear()
        reaper.interrupt_shadow(player)

    def spawned(self, player) -> None:
        """The player's client has the world: the game mode entity gets its UI graph (mode_frame). While
        heroes are assembled the countdown's frames carry it."""
        if reaper.is_reaper(player) and player.body_script is not None:
            player.body_script.reaper_charge_tick = self.match.tick
            player.body_script.reaper_charge_ms = 0
        if player.client is not None and not self.match.assembling():
            player.client.queue_mode_frame(mode_frame(
                {}, player.match.game_map.mode_guid, player.match.game_map.map_guid,
            ))
        self.voice.spawned(player, self.now)

    def command(self, player, command: Command | None, *, defer_effects: bool = False) -> None:
        """Consume the body's reports. Only a frame with a pose can affect targets."""
        if self.match.collision_world is None:
            return
        script = player.body_script
        if script is None:
            return
        if command is not None and command.frame == script.frame:
            # Pose-bearing input reanchors the owner's command epoch to world time.
            # Synthesized missing frames never reanchor themselves.
            if script.world_frame_anchor is not None:
                script.world_frame_anchor = (self.tick, script.frame)
            elif player.body in self.impulse_landings:
                self.impulse_landings[player.body] = (self.tick, script.frame)
        self.zarya_bubbles.sync(player)
        self.sigma_barriers.sync(player)
        reaper.advance_entry(player)
        defenses.advance_entry(player)
        winston_primal.sync(self, player, script)
        if (player.body in self.frost_hits
                and (reaper.intangible(player) or defenses.invulnerable(player) or mei.frost_immune(player))):
            self._clear_frost(player)
        cleanse = script.component.entity_var(8440, create=False)
        if cleanse is not None and expr.truthy(cleanse.value()):
            self.discord.cleanse(player.body)
        self._healing_factor(player)  # authored hostile-graph cleanse conditions after entry callbacks
        self._shadow(player, command)
        sigma_flux.release_protected_target(player)
        if not defer_effects:
            self._defense_heal(player, register=command is not None)
        shots, script.component.shots[:] = list(script.component.shots), []
        if self.is_dead(player):
            self.walls.cancel_preview(player)
            self.reaper_blossoms.pop(player.body, None)
            return
        meteor_strike.command(self, player, command, defer_impact=defer_effects)
        if self.is_stunned(player):
            self.walls.cancel_preview(player)
            return
        if meteor_strike.protected(player):
            return
        seismic_slam.command(self, player, command)
        winston_jump.command(self, player, command, script)
        if command is not None:
            self.sound_barriers.command(player)
        if self.walls.command(player, command):
            self._melee(player, None, script)
            return
        if command is not None:
            for state, number, time, activation in shots:
                try:
                    self._shoot(player, command, state, number, time, activation=activation)
                except Exception:
                    log.exception("[game] %s: %s's shot failed", self.match.label(), player.name)
        self.hooks.command(player, command)
        self.discord.command(player, command)
        try:
            self._melee(player, command, script)
        except Exception:
            log.exception("[game] %s: %s's melee failed", self.match.label(), player.name)
        lucio_soundwave.contact(self, player, command, script)
        winston_primal.contact(self, player, command, script)
        winston_tesla.contact(self, player, command, script)
        if not defer_effects:
            self._blossom(player, register=command is not None)

    def _shadow_landing(self, player, point) -> bool:
        """Reconstructed standing-capsule clearance and live-actor exclusion."""
        mover = player.mover
        radius, segment = mover._capsule(False)
        body = contacts.CharacterBody(radius, segment + radius, world=self._collision().engine,
                                      category=mover.category)
        body.teleport((point[0], point[1] + mover.data.stand_pogo, point[2]))
        if not body.fits(radius, segment + radius):
            return False
        if not self.walls.fits(point, radius, mover.data.stand_height, tick=self.tick):
            return False
        ground = self._collision().ground(point[0], point[2], point[1] + 0.1, point[1] - 0.1)
        if not self.walls.supports(point, tick=self.tick) and (
            ground is None or ground.normal[1] < mover.slope_cos
        ):
            return False
        for target in self._targets():
            if target.owner is player or self.vitals_of(target).dead:
                continue
            if (abs(point[1] - target.feet[1]) < max(mover.data.stand_height, target.height)
                    and math.hypot(point[0] - target.feet[0], point[2] - target.feet[2])
                    < radius + target.radius):
                return False
        return True

    def _shadow(self, player, command) -> None:
        """Bridge00F2's selector and teleport with declared server geometry policy.

        The native landing adjustment solver and random/context callbacks remain
        unimplemented; require a directly visible walkable surface within the
        authored35m ray and a free standing capsule. Keep authored cast phases,
        recovery and cooldown; lock a confirmed destination until completion.
        """
        if not reaper.is_reaper(player):
            return
        script = player.body_script
        if script.component.owner_dead or self.is_dead(player):
            self.reaper_shadows.pop(player.body, None)
            return
        instance = next((i for i in script.component.instances.values()
                         if i.graph.index == reaper.SHADOW_GRAPH), None)
        if instance is None:
            return
        selector = instance.states.get(0)
        record = self.reaper_shadows.get(player.body)
        if command is not None and not command.buttons & 16:
            idle, targeting, casting = (instance.states.get(index) for index in (1, 6, 19))
            if (idle is not None and not idle.active and not (targeting and targeting.active)
                    and not (casting and casting.active)):
                # The unsupported native reset action on a refused cooldown
                # attempt must not permanently retire the input wait. Re-arm
                # only after release, avoiding an immediate held-button cycle.
                idle.request(runtime.BEGIN)
                script.component.run_now()
        if selector is not None and selector.active and command is not None:
            identity = (instance.id, selector.counter)
            if record is None or record["selector"] != identity:
                record = self.reaper_shadows[player.body] = {
                    "selector": identity, "buttons": command.buttons, "target": None,
                    "cast": None, "moved": False,
                }
            origin = self._eye(player)
            reach = _number(selector.evaluate(selector.node.field("m_6386E7B4"), watch=False), 0.0)
            direction = aim(*command.aim())
            hit = self._collision().cast_ray(origin, direction, reach)
            point = hit.point if hit is not None else None
            normal = hit.normal if hit is not None else None
            pillar = self.walls.cast(origin, direction, hit.distance if hit is not None else reach)
            if pillar is not None:
                point = _along(origin, direction, pillar[0])
                normal = pillar[2]
            valid = (point is not None and normal[1] >= player.mover.slope_cos
                     and self._shadow_landing(player, point))
            record["target"] = tuple(point) if valid else None
            instance.write((0, 983), bool(valid))
            if point is not None:
                instance.write((0, 432), expr.Vec3(*point))
                instance.write((0, 985), expr.Vec3(*point))
            down = command.buttons & ~record["buttons"]
            record["buttons"] = command.buttons
            if down & 2:
                instance.state(6).request(runtime.ABORT)
                script.component.run_now()
                record["target"] = None
            elif down & 1 and valid:
                instance.follow(selector.node, "m_F6955E81", selector)
                script.component.run_now()
                cast = instance.states.get(32)
                if cast is not None and cast.active:
                    record["cast"] = (cast.counter, cast.start)
        if record is None or record["cast"] is None or record["moved"]:
            return
        cast = instance.states.get(32)
        if cast is None or (cast.counter, cast.start) != record["cast"] or cast.active:
            return
        # A same-frame abort is terminal even though the cast did not run.
        record["moved"] = True
        if cast.ended_at <= cast.start:
            return
        # A parent/control abort before the authored completion cannot teleport.
        delay = _number(cast.evaluate(cast.node.field("m_ED97A97C"), watch=False), 0.0) * 1000
        if cast.ended_at - cast.start + EPSILON < delay:
            return
        point = record["target"]
        if point is None or not self._shadow_landing(player, point):
            return
        mover = player.mover
        mover.position[:] = [quantise(axis) for axis in point]
        mover.velocity = (0.0, 0.0, 0.0)
        mover.spring = mover.spring2 = mover.spring_speed = mover.fall = 0.0
        mover.spring_steps = (0, 0)
        mover.body = None
        if mover.history and mover.history[-1].frame == script.frame:
            mover.history[-1] = mover.snapshot(script.frame)

    def _blossom(self, player, *, register=False) -> None:
        """Reconstruct effect4's per-target 5Hz damage while root0044 channels.

        The effect attachment cadence, capsule range and midpoint LOS sample are
        declared reconstructions. Damage34/5Hz/critical1 and loadout come from
        effect graph0045; radius/duration come from the live root0044 nodes.
        World time continues damage without fresh owner input, and a terminal
        record prevents a stalled VM from restarting the same channel.
        """
        state = reaper.blossom_state(player.body_script)
        channel = self.reaper_blossoms.get(player.body)
        if state is None and channel is None:
            return
        if self.is_dead(player) or (state is None and self.now < channel["end"] - EPSILON):
            if channel is not None:
                channel["end"] = min(channel["end"], self.now)
                channel["ended"] = True
                channel["targets"].clear()
            return
        activation = ((state.instance.id, state.counter, state.start) if state is not None
                      else channel["activation"])
        if channel is None or channel["activation"] != activation:
            if not register:
                return
            duration = _number(state.evaluate(state.node.field("m_timeout"), watch=False), 0.0)
            tracker = state.instance.states.get(5)
            if tracker is None:
                return
            radius = _number(tracker.evaluate(tracker.node.field("m_radius"), watch=False), 0.0)
            age = max(0.0, (state.now - state.start) / 1000.0)
            start = self.now - age
            channel = self.reaper_blossoms[player.body] = {
                "activation": activation, "start": start, "end": start + duration,
                "radius": radius, "ended": False, "targets": {}, "last_tick": None,
            }
        if channel["ended"] or channel.get("last_tick") == self.tick:
            return
        channel["last_tick"] = self.tick
        x, y, z = player.mover.position
        origin = (x, y + 1.8, z)  # root0044/st5's authored owner-relative search offset
        from ow174.game import barriers

        available = {}
        for target in self._enemies(player):
            a, b = target.segment()
            if segment_distance(origin, a, b) - target.radius > channel["radius"]:
                continue
            point = (target.feet[0], (a[1] + b[1]) / 2, target.feet[2])
            delta = _sub(point, origin)
            distance = math.sqrt(_dot(delta, delta))
            direction = tuple(value / distance for value in delta) if distance else (0.0, 0.0, 1.0)
            wall = ray_world(self._collision(), origin, direction, distance)
            reach = min(distance, channel["radius"])
            if wall is not None:
                reach = min(reach, wall)
            shield = barriers.intercept(self, player, origin, direction, reach)
            pillar = self.walls.cast(origin, direction, shield[0] if shield is not None else reach)
            if pillar is not None:
                if wall is None or wall >= distance:
                    available[target.entity] = (None, None)
                continue
            if shield is not None:
                _, instance = shield
                # Multiple covered bodies share one damage clock per shield activation.
                key = barriers.clock_key(instance)
                available[key] = (None, instance)
                if wall is None or wall >= distance:
                    # A body behind a shield retains its pulse schedule. The
                    # pulse that breaks the shield cannot also hit that body.
                    available[target.entity] = (None, None)
            elif wall is None or wall >= distance:
                available[target.entity] = (target, None)
        obscured = []
        for instance in barriers.blossom_targets(self, player, origin, channel["radius"], obscured=obscured):
            key = barriers.clock_key(instance)
            available[key] = (None, instance)
        for instance in obscured:
            available.setdefault(barriers.clock_key(instance), (None, None))
        for piece in self.walls.pieces.values():
            if not self.walls.damageable(piece, player):
                continue
            surface = mei_wall.closest(piece, origin)
            distance = math.dist(surface, origin)
            if (distance <= channel["radius"]
                    and capsule_visible(self._collision(), origin, surface, surface, 0.0)):
                available[piece.entity] = (None, None)
                if self.walls.blocked(origin, surface, endpoint=piece):
                    continue
                if distance > EPSILON:
                    direction = tuple(axis / distance for axis in _sub(surface, origin))
                    if barriers.intercept(self, player, origin, direction, distance) is not None:
                        continue
                available[piece.entity] = (None, piece)
        for device in self.baptiste_fields.enemies(player):
            centre = device.centre()
            distance = math.dist(centre, origin)
            if distance - baptiste_field.DEVICE_RADIUS > channel["radius"]:
                continue
            if self._blast_visible(player, origin, centre, centre, baptiste_field.DEVICE_RADIUS):
                available[device.body] = (None, device)
        for device in self.superchargers.enemies(player):
            surface = device.closest(origin)
            if (math.dist(surface, origin) <= channel["radius"]
                    and self._blast_visible(player, origin, surface, surface, 0.0)):
                available[device.entity] = (None, device)
        scale = self.outgoing_scale(player)
        damage = reaper.BLOSSOM_DAMAGE * scale
        mercy_credit = self._reaper_mercy_credit(player, scale)
        clocks = channel["targets"]
        for entity in list(clocks):
            if entity not in available:
                del clocks[entity]
        for entity, (target, shield) in available.items():
            if entity not in clocks:
                if self.now >= channel["end"]:
                    continue  # an actor acquired after expiration cannot receive historical hits
                clocks[entity] = max(channel["start"], self.now)
            due = clocks[entity]
            while due <= self.now + EPSILON and due < channel["end"] - EPSILON:
                due += reaper.BLOSSOM_INTERVAL
                if isinstance(shield, mei_wall.Pillar):
                    self.walls.damage(shield, player, damage, self.tick)
                    if shield.entity not in self.walls.pieces:
                        break
                elif isinstance(shield, baptiste_field.Device):
                    self.baptiste_fields.damage(shield, damage)
                    if not shield.health:
                        break
                elif isinstance(shield, orisa_supercharger.Device):
                    self.superchargers.damage(shield, damage)
                    if not shield.health:
                        break
                elif shield is not None:
                    barriers.damage(self, shield, damage, player)
                    if not barriers.is_live(shield):
                        break
                elif target is not None:
                    if mercy_credit is not None:
                        # Each pulse is an attack even when one update catches up several pulses.
                        mercy_credit.amplified_targets.discard(entity)
                    self._damage(player, target, damage, False,
                                 reaper.BLOSSOM_LOADOUT, self.tick, world_tick=self.tick, award_charge=False,
                                 mercy_credit=mercy_credit)
                    if self.vitals_of(target).dead:
                        break
            clocks[entity] = due
        if self.now >= channel["end"]:
            channel["ended"] = True
            clocks.clear()

    def frost(self, actor, *, tick=None):
        at = self.tick if tick is None else tick
        return mei.frost_view(self.frost_hits.get(_source_entity(actor)), at * 16)

    def frozen(self, actor, *, tick=None) -> bool:
        sample = self.frost(actor, tick=tick)
        return sample is not None and bool(sample[2])

    def _clear_frost(self, actor):
        self.frost_sources.pop(_source_entity(actor), None)
        if self.frost_hits.pop(_source_entity(actor), None) is None:
            return  # Unrelated NPC script wrappers need no frost/VM consumer.
        script = getattr(actor, "body_script", None)
        if script is not None:
            var = script.component.entity_var(2427, create=False)
            if var is not None:
                script.component.set(2427, 0.0)

    def _advance_frost(self):
        if not self.frost_hits:
            return
        current = {target.entity: target for target in self._targets()}
        for entity in tuple(self.frost_hits):
            target = current.get(entity)
            if target is None:
                del self.frost_hits[entity]
                self.frost_sources.pop(entity, None)
            elif (self.vitals_of(target).dead or defenses.invulnerable(target.owner)
                  or reaper.intangible(target.owner) or mei.frost_immune(target.owner)
                  or self.frost(target.owner) is None):
                self._clear_frost(target.owner)
            elif not self.frost_hits[entity][2] and entity in self.frost_sources:
                entries = self.frost_sources[entity]
                at = self.tick * 16
                mei_frost.prune(entries, at)
                sample = mei_frost.combine(entries, at, *self.frost_hits[entity][3:])
                if sample is None:
                    self._clear_frost(target.owner)
                else:
                    self._write_frost(target.owner, sample)

    def _frost_hit(self, target, *, source=None, channel=0, now_ms=None,
                   initial=mei.FROST_INITIAL, step=mei.FROST_STEP):
        actor = target.owner
        if mei.frost_immune(actor) or reaper.intangible(actor) or defenses.invulnerable(actor):
            return
        if isinstance(actor, bots.Bot):
            yaw, pitch = round(actor.yaw), 0
        else:
            yaw, pitch = actor.mover.yaw, actor.mover.pitch
        sample = self.frost_hits.get(target.entity)
        at = self.tick * 16 if now_ms is None else now_ms
        if sample is not None:
            at = max(at, sample[1])  # A second source cannot move aggregate time backwards.
            if sample[2]:
                if at < sample[2]:
                    return
                self.frost_sources.pop(target.entity, None)
        entries = self.frost_sources.setdefault(target.entity, {})
        mei_frost.prune(entries, at)
        minimum, scalar, freeze_ms, stacking = mei_frost.settings(source, weapon=channel == 0)
        key = (_source_entity(source) if source is not None else 0, channel)
        previous = entries.get(key, (None, False))[0]
        entries[key] = (mei.frost_hit(previous, at, yaw, pitch,
                                     initial=initial if minimum is None else minimum,
                                     step=expr.f32(step * scalar), freeze_ms=freeze_ms), stacking)
        sample = mei_frost.combine(entries, at, yaw, pitch, freeze_ms=freeze_ms)
        self._write_frost(actor, sample)
        if sample[2]:
            self._interrupt(target)

    def _write_frost(self, actor, sample):
        self.frost_hits[_source_entity(actor)] = sample
        script = getattr(actor, "body_script", None)
        if script is not None:
            script.component.set(2427, sample[0])

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
            damage_tags = damage_charge.event_tags(state, modify)
            scale = self.outgoing_scale(player)
            amount *= scale
            mercy_credit = self._reaper_mercy_credit(player, scale)
            eye = self._eye(player)
            centre = _along(eye, aim(*command.aim()), reach)
            view_tick = command.frame - command.view_delay(player.quanta) * 1e6 / player.quanta
            devices = self.baptiste_fields.targets(player)
            for target in (*self._enemies(player), *self.traps.targets(player), *devices):
                trap = self.traps.actors.get(target.entity)
                device = target.owner if isinstance(target.owner, baptiste_field.Device) else None
                shown = target if trap is not None or device is not None else self.seen(target, view_tick)
                if shown is None:
                    continue
                a, b = shown.segment()
                if (segment_distance(centre, a, b) - shown.radius <= radius and amount > 0.0
                        and capsule_visible(self._collision(), eye, a, b, shown.radius)
                        and not self.walls.blocked(eye, _along(a, _sub(b, a), 0.5))):
                    if trap is not None:
                        self.traps.damage(trap, amount)
                    elif device is not None:
                        self.baptiste_fields.damage(device, amount)
                    else:
                        self._damage(player, target, amount, False, loadout, command.frame,
                                     damage_tags=damage_tags, mercy_credit=mercy_credit)
            for device in tuple(self.superchargers.enemies(player)):
                if amount > 0.0 and math.dist(centre, device.closest(centre)) <= radius:
                    surface = device.closest(eye)
                    if self._blast_visible(player, eye, surface, surface, 0.0):
                        self.superchargers.damage(device, amount)
            for piece in tuple(self.walls.pieces.values()):
                surface = mei_wall.closest(piece, eye)
                if (math.dist(centre, mei_wall.closest(piece, centre)) <= radius
                        and capsule_visible(self._collision(), eye, surface, surface, 0.0)
                        and not self.walls.blocked(eye, surface, endpoint=piece)):
                    self.walls.damage(piece, player, amount, command.frame)

    def _earthshatter_caster(self, instance, *, released=False):
        return next((p for p in self.match.players if p.body == instance.component.entity
                     and p.has_body and p.spawned and not p.gone
                     and (released or not self.is_dead(p))), None)

    def earthshatter_targets(self, state):
        """Reconstructed horizontal front with one contact per victim per release."""
        instance = state.instance
        # Once launched, keep propagating from the retained pose even if the
        # original owner dies. A retired or replaced body cannot supply a cast.
        caster = self._earthshatter_caster(instance, released=state.origin is not None)
        if caster is None:
            return state.targets
        if state.origin is None:
            state.origin = tuple(caster.mover.position)
            state.forward = aim(caster.mover.yaw, 0)
            state.launch_tick = self.tick
        origin = state.origin
        front = min(state.reach, (self.tick - state.launch_tick) * TICK * state.speed)
        if front <= 0:
            return state.targets
        cone = math.cos(math.radians(expr.to_float(state.evaluate(
            state.node.field("m_A30B383E"), watch=False,
        ))))
        modify = instance.graph.node(3).field("m_modifyHealth")
        amount = expr.to_float(instance.evaluate(modify["m_amount"]))
        hits = list(state.targets)
        for target in self._enemies(caster):
            entity = expr.Entity(target.entity)
            if entity in hits or abs(target.feet[1] - origin[1]) > 2:
                continue
            delta = (target.feet[0] - origin[0], 0, target.feet[2] - origin[2])
            distance = math.hypot(delta[0], delta[2])
            if (distance <= EPSILON or distance - target.radius > front
                    or _dot(delta, state.forward) < distance * cone):
                continue
            if not self._earthshatter_visible(caster, origin, target):
                continue
            hits.append(entity)
            self._earthshatter_hit(caster, target, instance, modify, amount)
        return tuple(hits)

    def _earthshatter_visible(self, caster, origin, target):
        eye = (origin[0], origin[1] + (self._eye(caster)[1] - caster.mover.position[1]), origin[2])
        a, b = target.segment()
        if not capsule_visible(self._collision(), eye, a, b, target.radius) or self.walls.blocked(eye, b):
            return False
        sight = _sub(b, eye)
        length = math.sqrt(_dot(sight, sight))
        from ow174.game import barriers

        return length <= EPSILON or barriers.intercept(
            self, caster, eye, tuple(v / length for v in sight), length,
        ) is None

    def earthshatter_close_targets(self, state):
        instance = state.instance
        caster = self._earthshatter_caster(instance)
        wave = instance.states.get(instance.graph.node(1).state)
        if caster is None or wave is None or wave.origin is None:
            return ()
        # n115 places the close search ahead of the caster. Reconstruct its
        # horizontal contact around that authored offset and n114 radius.
        offset = instance.graph.node(115).field("m_value.m_expression.m_D99EF254")[0]
        centre = _along(wave.origin, wave.forward, offset)
        radius = expr.to_float(state.evaluate(state.node.field("m_radius"), watch=False))
        return tuple(expr.Entity(t.entity) for t in self._enemies(caster)
                     if math.dist(t.feet, centre) <= radius + t.radius
                     and self._earthshatter_visible(caster, wave.origin, t))

    def earthshatter_close_hit(self, state):
        instance = state.instance
        caster = self._earthshatter_caster(instance)
        wave = instance.states.get(instance.graph.node(1).state)
        if caster is None or wave is None:
            return
        selected = instance.find_var(expr.INSTANCE, 468).value() or ()
        modify = state.node.field("m_modifyHealth")
        amount = expr.to_float(state.evaluate(modify["m_amount"], watch=False))
        before = wave.targets
        for target in self._enemies(caster):
            entity = expr.Entity(target.entity)
            if entity in selected and entity not in wave.targets:
                wave.targets += (entity,)
                self._earthshatter_hit(caster, target, instance, modify, amount)
        if wave.var is not None and before != wave.targets:
            instance.changed(wave.var, before)

    def _earthshatter_hit(self, caster, target, instance, modify, amount):
        protected = self.zarya_bubbles.for_target(target.owner) is not None
        loadout = instance.evaluate(modify["m_loadout"]).guid
        self._damage(caster, target, amount, False, loadout, self.tick)
        if not protected:
            stun_ms = nodes.seconds_to_ms(instance.find_var(expr.INSTANCE, 215).value())
            self._hit_effect(target, None, stun_ms=stun_ms)

    def _advance_idle_body(self, player, previous_tick) -> None:
        """Reconstruct missing input windows for bodies with an explicit world-clock anchor.

        Leave the current owner's input window available for an arriving command.
        Older windows use the existing zero-input gap path: release held input,
        advance authored timers/cooldowns, discard unposed shots and update mods.
        This is an explicit server policy, not a native transport/rollback claim.
        """
        script = player.body_script
        if script is None or script.retired or not player.spawned or not player.has_body:
            self.impulse_landings.pop(player.body, None)
            return
        landing = self.impulse_landings.get(player.body)
        flight = self.winston_jumps.get(player.body)
        dash = self.punch_dashes.get(player.body)
        slam = self.slams.get(player.body)
        punch_active = rocket_punch.active(dash)
        slam_moves = seismic_slam.needs_pose(slam)
        if (punch_active or seismic_slam.needs_ticks(slam)) and script.world_frame_anchor is None:
            # A restored cast still has a closed owner frame.
            script.world_frame_anchor = previous_tick, script.frame
        anchor = script.world_frame_anchor or landing or (flight[1:] if flight is not None else None)
        if anchor is None:
            return
        if (player.hero.guid == rocket_punch.HERO and not rocket_punch.needs_clock(dash)
                and not seismic_slam.needs_clock(slam)):
            cast = self.meteors.get(player.body)
            if landing is None and (cast is None or cast.finished):
                return
        tick, frame = anchor
        elapsed = max(0, self.tick - tick) * round(TICK * 1000)
        target = min(projectiles.MAX_LEASE_FRAME - 1, frame + elapsed // script.frame_ms - 1)
        if target > script.frame:
            halt_moves = player.body in self.halts.pulls and not reaper.intangible(player)
            needs_pose = (landing is not None or flight is not None or punch_active or slam_moves
                          or player.body in self.charges or halt_moves or sigma_flux.needs_pose(player))
            if needs_pose and not self.gravitons.controls(player):
                player.advance_idle_control_pose(target, self.tick)
                if flight is not None:
                    self._refresh_impulse_history(player.body, player.mover.position)
            else:
                player._advance_body_gap(target + 1, world_idle=True)

    def update(self, now: float, tick: int, frames: bool = True, *, retained_npcs=()) -> None:
        """Each tick: keep where the bodies are, fly the rockets, respawn, send health that changed. The
        other players' body frames only on the ticks that send (`frames`)."""
        if self.match.collision_world is None:
            return
        from ow174.game import barriers

        waves = [
            state for player in self.match.players
            if player.body_script is not None and not player.body_script.retired
            for instance in player.body_script.component.instances.values()
            if instance.graph.index == 0xBE
            for state in instance.states.values()
            if isinstance(state, nodes.EarthshatterWave) and state.active and state.launch_tick is not None
        ]
        retained = max(self.tick + 1, tick - COMBAT_CATCH_UP + 1)
        first = tick
        if self.tick and tick > self.tick + 1:
            if any(npc_context.retains_weapon(bot) for bot in retained_npcs):
                first = max(self.tick + 1, tick - npc_context.WEAPON_REPLAY_TICKS + 1)
            elif (
                waves or self.punch_walls
                or any(c.wave_tick is not None and not c.finished for c in self.slams.values())
                or any(rocket.accretion_source is not None for rocket in self.rockets)
                or any(bot.firing and not bot.dead and isinstance(bot.body_script, BotScript)
                       for bot in self.match.bots)
            ):
                first = retained
        combat_ticks = set(range(first, tick + 1))
        combat_ticks.update(copy.until for copy in self.echo_duplicates.copies.values()
                            if self.tick < copy.until <= tick)
        if self.winston_jumps and retained_npcs:
            # A landing can create a new NPC impulse during replay. Keep the
            # subsequent navigation ticks as well as the impact tick.
            end = min(tick, self.tick + meteor_strike.MAX_RETAINED_TICKS)
            combat_ticks.update(range(self.tick + 1, end + 1))
        self.halts.retain_ticks(combat_ticks, self.tick + 1, tick, TICK)
        skipped_flight = bool(self.rockets) and tick > self.tick + 1
        defense_transition = skipped_flight and (
            bool(self.zarya_bubbles.live or self.winston_bubbles.actors)
            or bool(self.symmetra_barriers.actors)
            or bool(self.walls.pieces)
            or bool(self.baptiste_fields.devices)
            or bool(self.sound_barriers.leases or self.grasp_shields)
            or bool(self.discord.active)
            or any(nano.active(self, vitals) for vitals in self.vitals.values())
            or any(self._fortify_scale(target) is not None for target in self._targets())
            or any(barriers.active(player) is not None for player in self.match.players)
            or any(shield.active() for shield in self.brigitte_shields.by_body.values())
            or self._shadow_pending()
            or any(reaper.intangible(player) or defenses.invulnerable(player)
                   for player in self.match.players)
            or any(defenses.deflect_state(player) is not None for player in self.match.players)
        )
        matrix_active = skipped_flight and any(dva_matrix.active(player) for player in self.match.players)
        for rocket in self.rockets:
            if rocket.graviton_body is not None:
                end = self.tick + math.ceil(rocket.left / TICK) + graviton.duration_ticks(TICK)
                combat_ticks.update(range(self.tick + 1, min(tick, end) + 1))
            elif rocket.last_tick is not None and (
                rocket.definition == npc_context.PROJECTILE or defense_transition
                or (matrix_active and dva_matrix.eligible(rocket.definition))
                or self.orisa_barriers.flights
                or any(actor.active() for actor in self.orisa_barriers.actors.values())
                or any(actor.active() for actor in self.sigma_barriers.actors.values())
                or any(sigma_grasp.active(player) for player in self.match.players)
            ):
                # Sweep in order with teleports and changes to protection, defense position and lifetime.
                end = rocket.last_tick + math.ceil(rocket.left / TICK)
                combat_ticks.update(range(self.tick + 1, min(tick, end) + 1))
        for well in self.gravitons.fields:
            combat_ticks.update(range(self.tick + 1, min(tick, well.until) + 1))
        for channel in (*self.reaper_blossoms.values(), *self.defense_heals.values()):
            if channel["ended"]:
                continue
            # Retain the active effect's authored interval even across a large
            # scheduler gap. Expiring another body's protection at the endpoint
            # cannot reconstruct when Blossom first acquired that target.
            begin = max(self.tick + 1, tick + math.floor((channel["start"] - now) / TICK))
            end = min(tick, tick + math.ceil((channel["end"] - now) / TICK))
            combat_ticks.update(range(begin, end + 1))
        retained_entities = {bot.entity for bot in retained_npcs}
        for target in self._targets():
            if target.entity in retained_entities:
                continue
            history = self.history.setdefault(target.entity, deque(maxlen=HISTORY))
            history.append((tick, target.feet, target.height, target.eye))
        # Replay the retained combat frames using the most recent poses. Player input
        # accepted just before the scheduler caught up joins only at the endpoint.
        previous_tick = self.tick
        for at in self._retained_cast_ticks(combat_ticks):
            # Match.update normally moves NPCs before that tick's combat work.
            for bot in retained_npcs:
                self.match.step_manual_npc(bot, at)
            if retained_entities:
                for target in self._targets():
                    if target.entity in retained_entities:
                        history = self.history.setdefault(target.entity, deque(maxlen=HISTORY))
                        history.append((at, target.feet, target.height, target.eye))
            self.now, self.tick = now - (tick - at) * TICK, at
            players = self.match.players
            if self.charges or any(map(rocket_punch.active, self.punch_dashes.values())):
                # Close recipient timers before settling active dash contacts.
                players = sorted(players, key=lambda player: (
                    player.body in self.charges or rocket_punch.active(self.punch_dashes.get(player.body))
                ))
            for player in players:
                self._advance_idle_body(player, previous_tick)
                if player.body in self.impulse_landings:
                    self._refresh_impulse_history(player.body, player.mover.position)
                    if (player.body not in self.npc_impulses and not player.mover.airborne
                            and not player.mover.flags & FELL):
                        self.impulse_landings.pop(player.body)
                self.zarya_bubbles.sync(player)
            self.echo_duplicates.update()
            self.sound_barriers.update(at, TICK)
            self.baptiste_fields.update(at, TICK)
            self.superchargers.update(at, TICK)
            self.halts.update(at)
            self.gravitons.update(at)
            if self.meteors:
                meteor_strike.settle_world(self)
            reinhardt_charge.sync(self)
            for target in self._targets():
                self._healing_factor(target.owner)
            self.winston_bubbles.update(at)
            self.orisa_barriers.update(at, TICK)
            self.sigma_barriers.update(at, TICK)
            self.brigitte_shields.update(self.now)
            self.symmetra_barriers.update(at)
            self._advance_frost()
            self.blizzard.update(at)
            self.hooks.update(at)
            self.traps.update(at)
            self.walls.update(at)
            self.discord.update()
            winston_jump.settle(self)
            for wave in waves:
                if wave.active:
                    wave.advance(at, TICK)
            if self.grasp_shields:
                sigma_grasp.update(self)
            for rocket in list(self.rockets):
                if at == tick or rocket.last_tick is not None:
                    self._fly(rocket)
            self.amplification.update(at)
            self._bot_weapons(at)
            self._advance_npc_impulses(at)
            rocket_punch.update(self)
            seismic_slam.update(self)
            if self.doomfist_shields:
                sigma_grasp.decay_shields(self, self.doomfist_shields, rocket_punch.SHIELD_TAG)
            for player in self.match.players:
                if player.body in self.reaper_blossoms:
                    self._blossom(player)
                reaper.charge_world(player, self)
            # Defensive healing follows this combat damage pass for every slot.
            # A target killed by a pulse cannot be revived by later-slot healing.
            for player in self.match.players:
                if player.body in self.defense_heals:
                    self._defense_heal(player)
                if at > previous_tick:
                    mercy_beam.heal(self, player, TICK)
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
        for entity in [key for key, record in self.ana_sleeps.items() if record["end"] <= tick]:
            del self.ana_sleeps[entity]
        for table in (self.history, self.vitals, self.replacement_ticks, self.stuns, self.ana_sleeps,
                      self.reaper_blossoms, self.reaper_shadows, self.defense_heals, self.heal_modifiers):
            for entity in [key for key in table if key not in current]:
                del table[entity]

        for source, victim in list(self.pins.items()):
            if source not in current or victim not in current:
                del self.pins[source]

    # --- targets -----------------------------------------------------------------------------------

    def _shadow_pending(self):
        return any(record["cast"] is not None and not record["moved"]
                   for record in self.reaper_shadows.values())

    def _retained_cast_ticks(self, ticks):
        previous = self.tick
        budget = meteor_strike.MAX_RETAINED_TICKS
        for tick in sorted(ticks):
            while (previous + 1 < tick and budget
                   and (any(not cast.finished for cast in self.meteors.values())
                        or self.npc_impulses or self.impulse_landings
                        or self.winston_jumps
                        or self.hooks.flights or self.charges or self._shadow_pending()
                        or any(rocket_punch.active(dash) or rocket_punch.recovering(dash)
                               for dash in self.punch_dashes.values())
                        or any(map(seismic_slam.needs_ticks, self.slams.values()))
                        or self.blizzard.flights or self.blizzard.areas
                        or any(mercy_beam.healing_state(player) for player in self.match.players)
                        or any(sigma_flux.needs_pose(player) for player in self.match.players)
                        or any(sigma_flux.npc_needs_pose(bot) for bot in self.match.bots))):
                previous += 1
                budget -= 1
                yield previous
            yield tick
            previous = tick

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
        found.extend(self._bot_target(bot) for bot in self.match.bots)
        return found

    @staticmethod
    def _bot_target(bot) -> Target:
        data = mover_data(bot.body)
        return Target(
            bot.entity, bot.team, tuple(bot.position), data.stand_height, data.stand_radius, EYE, bot,
        )

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
            if self.echo_duplicates.restored.get(target.entity) == self.tick:
                continue
            if not free_for_all and target.team == shooter.team:
                continue
            if self.vitals_of(target).dead:
                continue
            if reaper.intangible(target.owner) or meteor_strike.protected(target.owner):
                continue
            found.append(target)
        return found

    def dragonblade_targets(self, player, state, search):
        """Host 0304's normal sword search with a current-aim capsule cone."""
        radius = expr.to_float(state.evaluate(search.field("m_radius"), watch=False))
        angle = expr.to_float(state.evaluate(search.field("m_A30B383E"), watch=False))
        forward = aim(player.mover.yaw, player.mover.pitch)
        origin = self._eye(player)
        threshold = math.cos(math.radians(angle / 2))
        selected = []
        for target in self._enemies(player):
            a, b = target.segment()
            centre = _along(a, _sub(b, a), 0.5)
            delta = _sub(centre, origin)
            if (segment_distance(origin, a, b) - target.radius <= radius
                    and _dot(delta, forward) >= threshold * math.hypot(*delta)
                    and capsule_visible(self._collision(), origin, a, b, target.radius)
                    and not self.walls.blocked(origin, centre)):
                selected.append(target)
        return selected

    def dragonblade_damage(self, state, elapsed_ms):
        if elapsed_ms:
            return True
        player = next((p for p in self.match.players if p.body == state.instance.component.entity), None)
        if player is None or self.is_dead(player) or self.is_stunned(player):
            return True
        # The authored health state starts before the linked search publishes its array.
        search = state.instance.graph.node(132 if state.node.pos == 49 else 133)
        amount = expr.to_float(state.evaluate(state.node.field("m_modifyHealth.m_amount"), watch=False))
        loadout = state.evaluate(state.node.field("m_modifyHealth.m_loadout"), watch=False)
        loadout_id = loadout.guid if isinstance(loadout, expr.Asset) else None
        for target in self.dragonblade_targets(player, state, search):
            self._damage(player, target, amount, False, loadout_id, self.tick)
        return True

    def swift_strike(self, player, state, before, tick):
        """Sweep the realized 01DB move using the authored search radius and damage."""
        delta = _sub(player.mover.position, before)
        distance = math.hypot(*delta)
        if distance <= EPSILON:
            return
        direction = tuple(axis / distance for axis in delta)
        origin = before[0], before[1] + 1.0, before[2]
        definition = state.instance.graph
        radius = expr.to_float(state.evaluate(definition.node(69).field("m_radius"), watch=False))
        damage = definition.node(12).field("m_modifyHealth")
        amount = expr.to_float(state.evaluate(damage["m_amount"], watch=False))
        loadout = state.evaluate(damage["m_loadout"], watch=False)
        loadout_id = loadout.guid if isinstance(loadout, expr.Asset) else None
        for target in self._enemies(player):
            if target.entity in state.swept_targets:
                continue
            a, b = target.segment()
            contact = ray_capsule(origin, direction, a, b, target.radius + radius)
            if (contact is None or contact > distance
                    or not capsule_visible(self._collision(), origin, a, b, target.radius)
                    or self.walls.blocked(_along(origin, direction, contact), _along(a, _sub(b, a), 0.5))):
                continue
            state.swept_targets.add(target.entity)
            self._damage(player, target, amount, False, loadout_id, tick, world_tick=tick)

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
                if controlled and allowed and bot.attack_melee:
                    self._npc_contact(bot, script, npc_control.MELEE_GRAPHS[bot.hero])
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

    def _npc_contact(self, bot, script, spec):
        """Consume one authored health scope using the tracked sphere and static LOS."""
        graph_id, index, track_pos = spec
        instance = next((i for i in script.component.instances.values() if i.graph.index == graph_id), None)
        state = instance.states.get(index) if instance is not None else None
        if state is None or not state.active or state.ending:
            return
        counters = self.melee_seen.setdefault(bot.entity, {})
        key = (instance.id, index)
        if counters.get(key) == state.counter:
            return
        counters[key] = state.counter
        tracking = instance.graph.node(track_pos)
        # Freeze contact selection before a swing can destroy an occluder.
        targets = tuple(self.npc_contact_targets(bot, instance, tracking, pillars=True))
        self._npc_contact_damage(bot, state, targets)

    def npc_charge_damage(self, state, *, fresh):
        """Apply the authored target array at activation, before 1408 clears it."""
        entity = state.instance.component.entity
        bot = next((b for b in self.match.bots if b.entity == entity and b.attack_charge), None)
        if bot is None or bot.dead or not bot.firing or self.is_stunned(bot):
            return
        selected = state.evaluate(state.node.field("m_FA3ECC43"), watch=False)
        selection = selected if isinstance(selected, tuple) else (selected,)
        identities = {value.id for value in selection if isinstance(value, expr.Entity)}
        if state.index == 53:
            # The current direct-contact phase relay can also end an impact.
            # The graph's selected impact body is handled by state 11.
            impact = state.instance.vars.get(536)
            if impact is not None:
                identities.discard(expr.to_entity(impact.value()))
            definition = state.node.field("m_modifyHealth")
            amount = _number(state.evaluate(definition["m_amount"], watch=False), 0)
            for entity in sorted(identities):
                piece = self.walls.pieces.get(entity)
                if piece is not None:
                    self.walls.damage(piece, bot, amount, self.tick)
        targets = [target for target in self._enemies(bot) if target.entity in identities]
        if state.index == 13:
            counters = self.melee_seen.setdefault(bot.entity, {})
            prefix = (state.instance.id, state.index)
            if fresh:
                for key in tuple(counters):
                    if key[:2] == prefix:
                        del counters[key]
            pending = []
            for target in targets:
                key = (*prefix, target.entity)
                if counters.get(key) != state.counter:
                    counters[key] = state.counter
                    pending.append(target)
            targets = pending
        self._npc_contact_damage(bot, state, targets)

    def npc_charge_pillar(self, bot, instance, tracking):
        if not self.walls.pieces:
            return None
        offset = instance.evaluate(tracking.field("m_05BE62DD"))
        reach = _number(instance.evaluate(tracking.field("m_radius")), 0)
        if not isinstance(offset, expr.Vec3) or reach <= 0:
            return None
        direction = aim(round(bot.yaw), 0)
        x, y, z = _along(bot.position, direction, offset.z)
        origin = (x, y + offset.y, z)
        hit = self.walls.cast(origin, direction, reach)
        if hit is not None:
            distance, piece, _ = hit
            if (piece.charge_contact and (self.match.game_map.free_for_all or piece.team != bot.team)
                    and ray_world(self._collision(), origin, direction, distance) is None):
                return piece
        return None

    def npc_charge_sweep_targets(self, bot, instance, tracking):
        """Reconstruct the charge query shape and run the authored candidate condition."""
        offset = instance.evaluate(tracking.field("m_05BE62DD"))
        radius = _number(instance.evaluate(tracking.field("m_radius")), 0)
        arc = _number(instance.evaluate(tracking.field("m_A30B383E")), 0)
        if not isinstance(offset, expr.Vec3) or radius <= 0:
            return ()
        direction = instance.vars.get(3192)
        vector = direction.value() if direction is not None else None
        speed = math.hypot(vector.x, vector.z) if isinstance(vector, expr.Vec3) else 0
        forward = (vector.x / speed, 0, vector.z / speed) if speed > EPSILON else aim(round(bot.yaw), 0)
        right = (forward[2], 0, -forward[0])
        centre = _along(_along(bot.position, forward, offset.z), right, offset.x)
        centre = (centre[0], centre[1] + offset.y, centre[2])
        excluded = expr.to_entity(instance.evaluate(tracking.field("m_544C3E6F")))
        cosine = math.cos(math.radians(arc * 0.5))
        impact = tracking.pos == 96
        condition = tracking.field("m_filter")["m_condition"]
        found = []
        for target in self._enemies(bot):
            dx, _, dz = _sub(target.feet, centre)
            along = dx * forward[0] + dz * forward[2]
            if target.entity == excluded or along < math.hypot(dx, dz) * cosine - EPSILON:
                continue
            a, b = target.segment()
            if (segment_distance(centre, a, b) - target.radius <= radius
                    and capsule_visible(self._collision(), centre, a, b, target.radius)
                    and not self.walls.blocked(centre, _along(a, _sub(b, a), 0.5))
                    and expr.truthy(expr.evaluate(condition, ChargeQueryContext(instance, target)))):
                found.append(target)
        if impact and found:
            return (min(found, key=lambda t: (math.dist(centre, t.feet), t.entity)),)
        return found

    def _npc_contact_damage(self, bot, state, targets):
        definition = state.node.field("m_modifyHealth")
        amount = _number(state.evaluate(definition["m_amount"], watch=False), 0)
        loadout = state.evaluate(definition.get("m_loadout"), watch=False)
        loadout = loadout.guid if isinstance(loadout, expr.Asset) else None
        identity = state.instance.graph.index, state.node.pos
        side_hit = identity == (0x1408, 35)
        knockback = side_hit or identity in ((0x1422, 4), (0x140E, 5))
        for target in targets:
            if isinstance(target, mei_wall.Pillar):
                self.walls.damage(target, bot, amount, self.tick)
                continue
            push = knockback and self.zarya_bubbles.for_target(target.owner) is None
            self._damage(bot, target, amount, False, loadout, self.tick, world_tick=self.tick)
            if push:
                self._npc_impulse(bot, target, state, definition, relative=side_hit)

    def _npc_impulse(self, bot, target, state, definition, *, relative):
        if isinstance(target.owner, bots.Bot) or self.vitals_of(target).dead:
            return
        offset = state.evaluate(definition["m_C0139FA6"], watch=False)
        if not isinstance(offset, expr.Vec3):
            return
        origin = (offset.x, offset.y, offset.z)
        if relative:  # Charge offsets are relative to BotWorld's zero position base.
            origin = tuple(a + b for a, b in zip(bot.position, origin, strict=True))
        dx, dy, dz = _sub(target.feet, origin)
        distance = math.sqrt(dx * dx + dy * dy + dz * dz)
        ring = definition["m_knockbackRings"][0]

        def number(config, key):
            return _number(state.evaluate(config.get(key), watch=False), 0)

        flat = math.hypot(dx, dz)
        if flat <= EPSILON or distance > number(ring, "m_radius"):
            return
        push = min(number(definition, "m_E33EFEE1") * number(ring, "m_9102DA3F"),
                   number(definition, "m_10399AE7") * number(ring, "m_10399AE7"))
        lift = number(definition, "m_583C208B") * number(ring, "m_579D7CFF")
        if push > 0 or lift > 0:
            self.npc_impulses[target.entity] = (self.tick, (dx * push / flat, lift, dz * push / flat))

    def _refresh_impulse_history(self, entity, position):
        history = self.history.get(entity)
        if history:
            frame, _, height, eye = history[-1]
            history[-1] = frame, tuple(position), height, eye

    def _advance_npc_impulses(self, tick):
        if not self.npc_impulses:
            return
        from ow174.game import npc_navigation

        targets = {target.entity: target for target in self._targets()}
        for entity, (last, velocity) in tuple(self.npc_impulses.items()):
            target = targets.get(entity)
            if (target is None or self.vitals_of(target).dead or reaper.intangible(target.owner)
                    or defenses.invulnerable(target.owner) or self._fortify_scale(target) is not None
                    or self.hooks.controlled(entity) or npc_navigation.charge_carries(self.match, entity)):
                self.npc_impulses.pop(entity)
                continue
            owner = target.owner
            npc = isinstance(owner, bots.Bot)
            for at in range(last + 1, tick + 1):
                if npc:
                    mover, clipped, normals = npc_navigation.displace(self.match, owner, velocity, TICK, at)
                else:
                    mover = owner.mover
                    before = tuple(mover.position)
                    clipped = mover.displace(velocity, TICK)
                    normals = self.walls.move_player(owner, before)
                    if velocity[1] > 0.0 and mover.position[1] > before[1] and owner.body_script is not None:
                        self.impulse_landings.setdefault(entity, (at - 1, owner.body_script.frame))
                planes = [(contacts.v3(*normal), 0.0) for normal in normals]
                x, y, z = contacts.clip_velocity(clipped, planes)
                flat = math.hypot(x, z)
                data = mover.values
                drag = data.decel_ground_base * data.run_forward * TICK
                scale = max(0, flat - drag) / flat if flat > EPSILON else 0
                velocity = (x * scale, max(0, y - 10 * data.gravity_scale * TICK), z * scale)
                if npc:
                    owner.velocity = tuple(a + b for a, b in zip(mover.velocity, velocity, strict=True))
                self._refresh_impulse_history(entity, owner.position if npc else mover.position)
                if max(map(abs, velocity)) < EPSILON:
                    self.npc_impulses.pop(entity)
                    break
                self.npc_impulses[entity] = (at, velocity)

    def npc_contact_targets(self, bot, instance, tracking, *, pillars=False, yaw=None):
        """Use the authored offset/radius with a reconstructed level yaw and capsule test."""
        offset = instance.evaluate(tracking.field("m_05BE62DD"))
        radius = _number(instance.evaluate(tracking.field("m_radius")), 0)
        if not isinstance(offset, expr.Vec3) or radius <= 0:
            return
        x, y, z = bot.position
        origin = (x, y + offset.y, z)
        centre = _along(origin, aim(round(bot.yaw) if yaw is None else yaw, 0), offset.z)
        if pillars:
            # Trooper's authored contact gate samples the first selected entity.
            ordered = sorted(self.walls.pieces.values(), key=lambda p: (math.dist(p.feet, origin), p.entity))
            for piece in ordered:
                if not self.walls.damageable(piece, bot):
                    continue
                surface = mei_wall.closest(piece, origin)
                if (math.dist(centre, mei_wall.closest(piece, centre)) <= radius
                        and capsule_visible(self._collision(), origin, surface, surface, 0.0)
                        and not self.walls.blocked(origin, surface, endpoint=piece)):
                    yield piece
        for target in self._enemies(bot):
            a, b = target.segment()
            if (segment_distance(centre, a, b) - target.radius <= radius
                    and capsule_visible(self._collision(), origin, a, b, target.radius)
                    and not self.walls.blocked(origin, _along(a, _sub(b, a), 0.5))):
                yield target

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
        if nano.release(self, shooter, state):
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
            direction = self.echo_duplicates.visor_direction(shooter, command, state, origin, direction)
        if (not npc and orisa_halt.SUPPLEMENT_SHA256
                and (state.instance.graph.index, node.pos) == orisa_halt.CAST):
            speed = field("m_projectileSpeed")
            reach = min(field("m_FEC435C6"), speed * field("m_projectileLifetime"))
            self.halts.launch(shooter, state, origin, direction, speed, reach)
            return
        if (not npc and orisa_supercharger.SUPPLEMENT_SHA256
                and (state.instance.graph.index, node.pos) == (0x0CF5, 29)):
            self.superchargers.launch(shooter, state, origin, direction, command.aim()[0])
            return
        if not npc and (state.instance.graph.index, node.pos) == (0x1761, 21):
            self.baptiste_fields.launch(shooter, state, origin, direction)
            return
        if (not npc and self.orisa_barriers.geometry is not None
                and (state.instance.graph.index, node.pos) == (0xBD7, 1)):
            self.orisa_barriers.launch(shooter, state, origin, direction, command.aim()[0])
            return
        if not npc and self.blizzard.fired(shooter, state, shot_frame, origin, direction,
                                           activation=activation, snapshot=snapshot):
            return
        modifiers = self.outgoing_modifiers(shooter)
        scale = sum(modifiers)
        direct = hit_def(state, node.field("m_6396149F"))
        if direct is not None and scale != 1.0:
            direct = replace(direct, amount=direct.amount * scale)
        speed = field("m_projectileSpeed")
        if speed:
            motion = None if npc else sigma_accretion.accretion_launch(state, direction)
            gravity, velocity, rock_source = 0.0, None, None
            rock_force = rock_splash_force = None
            gravity_field = (state.instance.graph.index, node.pos) == graviton.CAST and not npc
            if gravity_field:
                gravity = graviton.launch_gravity(state)
                velocity = tuple(expr.f32(speed * axis) for axis in direction)
            if motion is not None:
                gravity, direction = motion
                velocity = tuple(expr.f32(speed * axis) for axis in direction)
                rock_source = shooter.body, state.instance.id
                rock_force = sigma_accretion.knockback(state, "m_6396149F")
                rock_splash_force = sigma_accretion.knockback(state, "m_D2D11CE9")
            lifetime = field("m_projectileLifetime", 5.0) or 5.0
            reach = field("m_FEC435C6")
            if reach:
                lifetime = min(lifetime, reach / speed)
            blast = hit_def(state, node.field("m_D2D11CE9"))
            if blast is not None and scale != 1.0:
                blast = replace(blast, amount=blast.amount * scale)
            entity_def = (node.field("m_projectileEntity") or {}).get("m_entityDef")
            definition = state.evaluate(entity_def, watch=False) if entity_def is not None else None
            if not npc and isinstance(definition, expr.Asset) and definition.guid == steel_trap.PROJECTILE:
                self.traps.cast(shooter, state, origin, direction, speed, lifetime)
                return
            directions = npc_context.projectile_directions(shooter, state, direction) if npc else (direction,)
            if not npc and (state.instance.graph.index, node.pos) == (0x0304, 29):
                # Reconstruct 0304's fixed fan as evenly spaced camera-relative rays.
                spread = math.radians(field("m_5EC1EB38.m_spread", 0.0))
                right = aim(command.aim()[0] + 16384, 0)
                count = int(field("m_numProjectilesPerShot", 1))
                fan = []
                for index in range(count):
                    angle = (index - (count - 1) / 2) * spread
                    forward = tuple(axis * math.cos(angle) for axis in direction)
                    fan.append(_along(forward, right, math.sin(angle)))
                directions = fan
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
                if damaging or shot is not None:
                    self.rockets.append(
                        Rocket(
                            shooter, shooter.team, origin, ray, speed, lifetime, direct, blast, shot,
                            last_tick=self.tick if npc or rock_source is not None or gravity_field else None,
                            gravity=gravity, velocity=velocity,
                            accretion_source=rock_source, accretion_force=rock_force,
                            accretion_splash_force=rock_splash_force,
                            target_limit=mei.projectile_target_limit(state),
                            definition=definition.guid if isinstance(definition, expr.Asset) else None,
                            ana_grenade=ana.is_ana(shooter) and blast is not None
                            and blast.effect == ana.GRENADE_EFFECT,
                            actor_modifiers=modifiers,
                            graviton_body=shooter.body if gravity_field else None,
                            duplicate_body=(
                                shooter.body if self.echo_duplicates.copied_soldier(shooter) else None
                            ),
                        )
                    )
            return
        if direct is None or direct.amount <= 0.0:
            return
        reaper.shotgun_critical(shooter, state, direct)
        reach = field("m_FEC435C6", RANGE) or RANGE
        # The input supplies this simulation frame's aim and view time. Notices use the volley clock.
        view_tick = self.tick
        if command is not None:
            view_tick = command.frame - command.view_delay(shooter.quanta) * 1e6 / shooter.quanta
        entity_def = (node.field("m_projectileEntity") or {}).get("m_entityDef")
        definition = state.evaluate(entity_def, watch=False) if entity_def is not None else None
        volley = None
        if (state.instance.graph.index, node.pos) == whole_hog.VOLLEY:
            from ow174.game.shot_patterns import ring_directions

            volley = whole_hog.evaluate(state)
            if node.field("m_6396149F.m_A872C70E") is None:
                direct.critical = 2.0  # reconstructed omitted shotgun critical scalar
            pellets = ring_directions(state, direction)
        else:
            pellets = reaper.shotgun_directions(shooter, state, direction)
        mercy_credit = self._reaper_mercy_credit(shooter, scale)
        reflected_shells = {} if reaper.is_reaper(shooter) else None
        for pellet in pellets:
            self._hitscan(shooter, origin, pellet, direct, reach, view_tick, shot_frame,
                          npc=npc, definition=definition.guid if isinstance(definition, expr.Asset) else None,
                          actor_modifiers=modifiers, volley=volley, mercy_credit=mercy_credit,
                          reflected_shells=reflected_shells)

    def outgoing_scale(self, shooter):
        return sum(self.outgoing_modifiers(shooter))

    def outgoing_modifiers(self, shooter):
        return (mercy_beam.factor(self, shooter) + nano.bonus(self, shooter),
                self.superchargers.scale(shooter) - 1.0)

    def _reaper_mercy_credit(self, shooter, scale):
        if reaper.is_reaper(shooter):
            source = mercy_beam.boost_source(self, shooter)
            if source is not None:
                base_fraction = (scale - (mercy_beam.BOOST_FACTOR - 1.0)) / scale
                return _MercyDamageCredit(source, base_fraction)
        return None

    def _reflected_supercharger_factor(self, defender, modifiers):
        other, launch_bonus = modifiers
        return (other + (self.superchargers.scale(defender) - 1.0)) / (other + launch_bonus)

    def _reflected_reaper_shell_modifiers(self, defender, modifiers, launch_credit):
        other, launch_bonus = modifiers
        base_scale = other + (self.superchargers.scale(defender) - 1.0)
        mercy_bonus = mercy_beam.BOOST_FACTOR - 1.0
        if launch_credit is not None:
            base_scale -= mercy_bonus
        staff = mercy_beam.boost_source(self, defender)
        if staff is None:
            return base_scale / (other + launch_bonus), None
        scale = base_scale + mercy_bonus
        return scale / (other + launch_bonus), _MercyDamageCredit(staff, base_scale / scale)

    def _hitscan(self, shooter, origin, direction, direct, reach, view_tick, shot_frame, *,
                 npc=False, reflected=False, definition=None, amplified=False, actor_modifiers=(1.0, 0.0),
                 volley=None, mercy_credit=None, reflected_shells=None):
        """Resolve one ray; pellets independently meet bodies, walls, falloff and armour."""
        from ow174.game import barriers

        best = None
        devices = self.baptiste_fields.targets(shooter)
        for target in (*self._enemies(shooter), *self.traps.targets(shooter), *devices):
            fixed = target.entity in self.traps.actors or isinstance(target.owner, baptiste_field.Device)
            shown = target if fixed else self.seen(target, view_tick)
            if shown is None:
                continue
            a, b = shown.segment()
            t = ray_capsule(origin, direction, a, b, shown.radius)
            if t is not None and t <= reach and (best is None or t < best[0]):
                best = (t, target, shown)
        limit = best[0] if best else reach
        device_hit = self.superchargers.trace(shooter, origin, direction, limit)
        if device_hit is not None:
            limit = device_hit[0]
        shield = barriers.intercept(self, shooter, origin, direction, limit)
        stop = shield[0] if shield is not None else limit
        absorbed = self._projectile_defense_contact(shooter, origin, direction, stop, definition=definition)
        stop = absorbed[0] if absorbed is not None else stop
        pillar = self.walls.cast(origin, direction, stop)
        if pillar is not None:
            stop = pillar[0]
        wall = ray_world(self._collision(), origin, direction, stop)
        if wall is not None and wall < stop:
            return
        if not amplified:
            factor = self.amplification.multiplier(shooter, origin, _along(origin, direction, stop))
            if factor > 1:
                direct = replace(direct, amount=direct.amount * factor)
                amplified = True
        if pillar is not None:
            distance, piece, _ = pillar
            self.walls.damage(piece, shooter, direct.amount * direct.scale(distance), shot_frame)
            return
        if absorbed is not None:
            distance, _, state, _ = absorbed
            if state.instance.graph.index == sigma_grasp.GRASP:
                self._absorb_hit(state, shooter, direct.amount * direct.scale(distance))
            return
        if shield is not None:
            distance, instance = shield
            barriers.damage(self, instance, direct.amount * direct.scale(distance), shooter)
            return
        if device_hit is not None:
            distance, device = device_hit
            self.superchargers.damage(device, direct.amount * direct.scale(distance))
            return
        if best is None:
            return
        t, target, shown = best
        if isinstance(target.owner, baptiste_field.Device):
            self.baptiste_fields.damage(target.owner, direct.amount * direct.scale(t))
            return
        trap = self.traps.actors.get(target.entity)
        if trap is not None:
            self.traps.damage(trap, direct.amount * direct.scale(t))
            return
        reflected_ray = self._deflect_direction(target.owner, direction)
        if reflected_ray is not None:
            if not reflected:
                defender = target.owner
                return_credit = None
                if reflected_shells is None:
                    factor = self._reflected_supercharger_factor(defender, actor_modifiers)
                else:
                    # All pellets returned by one defender share the shell's attribution.
                    if defender.body not in reflected_shells:
                        reflected_shells[defender.body] = self._reflected_reaper_shell_modifiers(
                            defender, actor_modifiers, mercy_credit,
                        )
                    factor, return_credit = reflected_shells[defender.body]
                returned = replace(direct, amount=direct.amount * factor) if factor != 1.0 else direct
                self._hitscan(defender, self._eye(defender), reflected_ray, returned, reach,
                              self.tick, shot_frame, reflected=True, definition=definition,
                              amplified=amplified, volley=volley, mercy_credit=return_credit,
                              reflected_shells=reflected_shells)
            return
        hit_y = origin[1] + direction[1] * t
        critical = hit_y >= shown.feet[1] + shown.eye - HEAD_BELOW and self._fortify_scale(target) is None
        amount = direct.amount * direct.scale(t) * (direct.critical if critical else 1.0)
        body_hit = self._damage(
            shooter, target, amount, critical and direct.critical > 1.0, direct.loadout, shot_frame,
            world_tick=self.tick if npc else None, award_charge=volley is None or reflected,
            damage_tags=direct.damage_tags, mercy_credit=mercy_credit,
            reflected_reaper_shell=reflected and reflected_shells is not None,
        )
        if volley is not None and body_hit:
            volley.push(self, target, direction, t)
        self._hit_effect(target, direct.effect)

    @staticmethod
    def _absorb_hit(state, shooter, amount):
        instance = state.instance
        instance.write((expr.INSTANCE, 6584), expr.f32(amount))
        instance.write((expr.INSTANCE, 41), expr.Entity(_source_entity(shooter)))
        instance.follow(state.node, "m_160B5DBE", state)
        instance.component.run_now()

    def _projectile_defense_contact(
        self, shooter, origin, direction, reach, *, moving=False, definition=None, accretion=False,
        passed_defenders=(),
    ):
        """Sweep authored defense boxes using a reconstructed current aim basis."""
        nearest = None
        for target in self._enemies(shooter):
            if target.entity in passed_defenders:
                continue
            defender = target.owner
            hero = getattr(getattr(defender, "hero", None), "guid", None)
            if (hero == pools.DVA_HERO and defender.hero.body == pools.DVA_MECH
                    and dva_matrix.eligible(definition)):
                graph_id, state_id = dva_matrix.GRAPH, dva_matrix.STATE
            elif hero == sigma_grasp.HERO and not accretion:
                graph_id, state_id = sigma_grasp.GRASP, 1
            elif moving and hero == defenses.GENJI and not accretion:
                graph_id, state_id = defenses.DEFLECT_GRAPH, 10
            else:
                continue
            script = defender.body_script
            if script is None or script.retired or script.component.owner_dead:
                continue
            instance = next(
                (i for i in script.component.instances.values() if i.graph.index == graph_id), None)
            state = instance.states.get(state_id) if instance is not None else None
            if state is None or not state.active or state.ending:
                continue
            forward = aim(defender.mover.yaw, defender.mover.pitch)
            yaw = defender.mover.yaw * math.pi / 32768
            right = (-math.cos(yaw), 0, math.sin(yaw))
            up = (-forward[1] * right[2], forward[0] * right[2] - forward[2] * right[0],
                  forward[1] * right[0])
            angle = _number(state.evaluate(state.node.field("m_025D0C39"), watch=False), 0)
            if angle <= 0 or -_dot(forward, direction) < math.cos(math.radians(angle / 2)):
                continue
            delta = _sub(origin, self._eye(defender))
            local_o = tuple(_dot(delta, axis) for axis in (right, up, forward))
            local_d = tuple(_dot(direction, axis) for axis in (right, up, forward))
            for box in state.node.field("m_boxes"):
                lo = state.evaluate(box["m_CBA118B2"], watch=False)
                hi = state.evaluate(box["m_AEF67365"], watch=False)
                if not isinstance(lo, expr.Vec3) or not isinstance(hi, expr.Vec3):
                    continue
                entry, leave = 0.0, reach
                for start, velocity, lower, upper in zip(
                    local_o, local_d, (lo.x, lo.y, lo.z), (hi.x, hi.y, hi.z), strict=True
                ):
                    lower, upper = min(lower, upper), max(lower, upper)
                    if abs(velocity) < EPSILON:
                        if not lower <= start <= upper:
                            break
                        continue
                    a, b = sorted(((lower - start) / velocity, (upper - start) / velocity))
                    entry, leave = max(entry, a), min(leave, b)
                    if entry > leave:
                        break
                else:
                    if graph_id == dva_matrix.GRAPH:
                        contact = _along(origin, direction, entry)
                        eye = self._eye(defender)
                        offset = _sub(contact, eye)
                        distance = math.sqrt(_dot(offset, offset))
                        if distance > EPSILON:
                            line = tuple(value / distance for value in offset)
                            wall = ray_world(self._collision(), eye, line, distance)
                            if wall is not None and wall < distance - EPSILON:
                                continue
                    if nearest is None or entry < nearest[0]:
                        nearest = entry, defender, state, forward
        return nearest

    @staticmethod
    def _deflect_direction(defender, incoming):
        """Read the active03A3 scope and reconstruct a current-aim return ray."""
        state = defenses.deflect_state(defender)
        if state is None:
            return None
        angle = _number(state.evaluate(state.node.field("m_025D0C39"), watch=False), 0)
        forward = aim(defender.mover.yaw, defender.mover.pitch)
        # Treat authored90 as a full cone angle. Aim, cone convention and
        # the one-return limit are reconstructed server policy.
        if angle > 0 and -_dot(forward, incoming) >= math.cos(math.radians(angle / 2)):
            return forward
        return None

    def _fly(self, rocket: Rocket) -> None:
        """Sweep elapsed world time against the current bodies and static collision."""
        if (rocket.graviton_body is not None
                and (rocket.shooter.gone or rocket.shooter.body != rocket.graviton_body or self.match.ended)):
            self._landed(rocket)
            return
        # Player input can arrive immediately before the scheduler accounts for a gap.
        elapsed = 1 if rocket.last_tick is None else self.tick - rocket.last_tick
        if elapsed <= 0:
            return
        rocket.last_tick = self.tick
        if rocket.velocity is not None:
            for skipped in range(elapsed - 1, -1, -1):
                self._fly_step(rocket, min(TICK, rocket.left), self.tick - skipped)
                if rocket not in self.rockets:
                    break
            return
        self._fly_step(rocket, min(elapsed * TICK, rocket.left), self.tick - elapsed + 1)

    def _fly_step(self, rocket: Rocket, duration: float, flight_tick: int) -> None:
        from ow174.game import barriers

        if duration <= 0.0:
            self._landed(rocket)
            return
        if rocket.velocity is not None:
            # Build-104319 updates vertical velocity before the sweep.
            vx, vy, vz = rocket.velocity
            vy = expr.f32(vy - expr.f32(expr.f32(rocket.gravity) * expr.f32(duration)))
            rocket.velocity = (vx, vy, vz)
            rocket.speed = math.hypot(vx, vy, vz)
            if not rocket.speed:
                rocket.left -= duration
                return
            rocket.direction = tuple(axis / rocket.speed for axis in rocket.velocity)
        # Sweep the remaining flight even when the range or lifetime ends inside this tick.
        step = rocket.speed * duration
        best = None
        pierced = []
        for target in self._enemies(rocket.shooter):
            a, b = target.segment()
            distance = ray_capsule(rocket.position, rocket.direction, a, b, target.radius)
            if distance is None or distance > step:
                continue
            if rocket.target_limit > 1:
                if target.entity not in rocket.hit_entities:
                    pierced.append((distance, target))
            elif best is None or distance < best[0]:
                best = (distance, target)
        wall = ray_world(self._collision(), rocket.position, rocket.direction, step)
        if wall is not None and (best is None or wall < best[0]):
            best = (wall, None)
        limit = step if best is None else best[0]
        struck_device = None
        if ((rocket.direct is not None and rocket.direct.amount > 0)
                or (rocket.blast is not None and rocket.blast.amount > 0)):
            contact = self.superchargers.trace(rocket.shooter, rocket.position, rocket.direction, limit)
            if contact is not None and (best is None or contact[0] < limit):
                limit, struck_device = contact
                best = (limit, None)
        pillar = self.walls.cast(rocket.position, rocket.direction, limit)
        struck_pillar = None
        if pillar is not None and (best is None or pillar[0] < limit):
            limit, struck_pillar, _ = pillar
            struck_device = None
            best = (limit, None)
        shield = barriers.intercept(self, rocket.shooter, rocket.position, rocket.direction, limit)
        shield_instance = None
        if shield is not None and (best is None or shield[0] < limit):
            limit, shield_instance = shield
            struck_pillar = None
            struck_device = None
            best = (limit, None)
        defense = self._projectile_defense_contact(
            rocket.shooter, rocket.position, rocket.direction, limit, moving=True,
            definition=rocket.definition, accretion=rocket.accretion_source is not None,
        )
        defense_first = defense is not None and (best is None or defense[0] < limit)
        if pierced:
            contact_limit = defense[0] if defense_first else limit
            for distance, target in sorted(pierced, key=lambda hit: hit[0]):
                if (distance > contact_limit
                        or (distance == contact_limit and (best is not None or defense_first))):
                    break
                self._amplify_flight(rocket, distance, flight_tick - 1)
                rocket.hit_entities.add(target.entity)
                amount = rocket.direct.amount * rocket.direct.scale(rocket.flown * rocket.speed + distance)
                self._damage(rocket.shooter, target, amount, False, rocket.direct.loadout,
                             self.tick, world_tick=self.tick, damage_tags=rocket.direct.damage_tags,
                             source_body=rocket.duplicate_body)
                if len(rocket.hit_entities) >= rocket.target_limit:
                    self._landed(rocket)
                    return
        self._amplify_flight(rocket, defense[0] if defense_first else limit, flight_tick - 1)
        if defense_first:
            distance, defender, state, forward = defense
            spent = distance / rocket.speed
            rocket.flown += spent
            if state.instance.graph.index == sigma_grasp.GRASP:
                # Reconstructed nominal value: scaled direct plus authored blast.
                amount = (rocket.direct.amount * rocket.direct.scale(rocket.flown * rocket.speed)
                          if rocket.direct is not None else 0.0)
                if rocket.blast is not None:
                    amount += rocket.blast.amount
                self._absorb_hit(state, rocket.shooter, amount)
            elif state.instance.graph.index == 0x02CC:
                self.rockets.remove(rocket)
                if rocket.shot is not None:
                    self.projectiles.absorbed(rocket.shot)
                return
            elif not rocket.reflected and spent < rocket.left:
                # Start the return next tick with the remaining flight budget.
                factor = self._reflected_supercharger_factor(defender, rocket.actor_modifiers)
                direct, blast = rocket.direct, rocket.blast
                if factor != 1.0:
                    if direct is not None:
                        direct = replace(direct, amount=direct.amount * factor)
                    if blast is not None:
                        blast = replace(blast, amount=blast.amount * factor)
                self.rockets.append(Rocket(
                    defender, defender.team, _along(rocket.position, rocket.direction, distance),
                    forward, rocket.speed, rocket.left - spent, direct, blast,
                    flown=rocket.flown, last_tick=self.tick,
                    ana_grenade=rocket.ana_grenade, reflected=True, definition=rocket.definition,
                    target_limit=rocket.target_limit, amplified=rocket.amplified,
                    graviton_body=defender.body if rocket.graviton_body is not None else None,
                    gravity=rocket.gravity if rocket.graviton_body is not None else 0.0,
                    velocity=(tuple(axis * rocket.speed for axis in forward)
                              if rocket.graviton_body is not None else None),
                ))
            self._landed(rocket)
            return
        if best is None:
            rocket.position = _along(rocket.position, rocket.direction, step)
            rocket.flown += duration
            rocket.left -= duration
            if rocket.left <= 0.0:
                self._landed(rocket)
            return
        t, struck = best
        rocket.flown += t / rocket.speed
        self._landed(rocket)
        point = _along(rocket.position, rocket.direction, t)
        if rocket.graviton_body is not None:
            self.gravitons.land(rocket.shooter, _along(point, rocket.direction, -0.0001))
        direct_amount = (rocket.direct.amount * rocket.direct.scale(rocket.flown * rocket.speed)
                         if rocket.direct is not None else 0.0)
        if struck_device is not None and direct_amount > 0.0:
            self.superchargers.damage(struck_device, direct_amount)
        if struck is not None and direct_amount > 0.0:
            before = self.vitals_of(struck).total if rocket.accretion_source is not None else None
            self._damage(
                rocket.shooter, struck,
                direct_amount,
                False, rocket.direct.loadout, self.tick,
                world_tick=self.tick, award_charge=rocket.graviton_body is None,
                damage_tags=rocket.direct.damage_tags, source_body=rocket.duplicate_body,
            )
            self._hit_effect(struck, rocket.direct.effect)
            if before is not None:
                dealt = before - self.vitals_of(struck).total
                if dealt > 0:
                    sigma_accretion.accretion_hit(self, rocket, struck, dealt)
                    sigma_accretion.impulse(self, rocket, struck)
        blast = rocket.blast
        if blast is not None and blast.amount > 0.0:
            # Resolve splash against the pre-impact shield, including on its break pulse.
            origin = _along(point, rocket.direction, -0.0001) if struck is None else point
            for device in tuple(self.superchargers.enemies(rocket.shooter)):
                surface = device.closest(point)
                scalar = blast.splash(math.dist(point, surface))
                if scalar > 0.0 and self._blast_visible(rocket.shooter, origin, surface, surface, 0.0):
                    self.superchargers.damage(device, blast.amount * scalar)
            if rocket.ana_grenade:
                self._ana_grenade(rocket, point, origin)
            else:
                for target in self._enemies(rocket.shooter):
                    a, b = target.segment()
                    distance = max(0.0, segment_distance(point, a, b) - target.radius)
                    scalar = blast.splash(distance)
                    if scalar > 0.0 and self._blast_visible(rocket.shooter, origin, a, b, target.radius):
                        impulse = rocket.accretion_splash_force is not None and (
                            struck is None or struck.entity != target.entity
                        )
                        before = self.vitals_of(target).total if impulse else None
                        self._damage(
                            rocket.shooter, target, blast.amount * scalar, False, blast.loadout, self.tick,
                            world_tick=self.tick, damage_tags=blast.damage_tags,
                            source_body=rocket.duplicate_body,
                        )
                        self._hit_effect(target, blast.effect)
                        if before is not None and self.vitals_of(target).total < before:
                            sigma_accretion.impulse(self, rocket, target, point=point, distance=distance)
        if shield_instance is not None:
            amount = direct_amount + (blast.amount if blast is not None else 0.0)
            if amount > 0.0:
                barriers.damage(self, shield_instance, amount, rocket.shooter)
        if struck_pillar is not None:
            amount = direct_amount + (blast.amount if blast is not None else 0.0)
            self.walls.damage(struck_pillar, rocket.shooter, amount, self.tick)

    def _amplify_flight(self, rocket, distance, start_tick):
        if rocket.amplified:
            return
        factor = self.amplification.multiplier(
            rocket.shooter, rocket.position, _along(rocket.position, rocket.direction, distance),
            (start_tick, start_tick + distance / rocket.speed / TICK),
        )
        if factor > 1:
            if rocket.direct is not None:
                rocket.direct = replace(rocket.direct, amount=rocket.direct.amount * factor)
            if rocket.blast is not None:
                rocket.blast = replace(rocket.blast, amount=rocket.blast.amount * factor)
            rocket.amplified = True

    def _blast_visible(self, shooter, origin, a, b, radius):
        from ow174.game import barriers

        if not capsule_visible(self._collision(), origin, a, b, radius):
            return False
        # Reconstructed shield LOS samples the capsule midpoint.
        midpoint = _along(a, _sub(b, a), 0.5)
        if self.walls.blocked(origin, midpoint):
            return False
        delta = _sub(midpoint, origin)
        distance = math.sqrt(_dot(delta, delta))
        return distance == 0 or barriers.intercept(
            self, shooter, origin, tuple(axis / distance for axis in delta), distance) is None

    def _ana_grenade(self, rocket, point, origin) -> None:
        """Normal0C71 relation dispatch; geometry/filter/motion remain reconstructed."""
        shooter, blast = rocket.shooter, rocket.blast
        for target in self._targets():
            vitals = self.vitals_of(target)
            if vitals.dead:
                continue
            a, b = target.segment()
            scalar = blast.splash(max(0.0, segment_distance(point, a, b) - target.radius))
            if scalar <= 0 or not self._blast_visible(shooter, origin, a, b, target.radius):
                continue
            friendly = (target.owner is shooter or (not self.match.game_map.free_for_all
                                                    and target.team == shooter.team))
            if friendly:
                modifiers = self.heal_modifiers.setdefault(target.entity, {})
                modifiers.pop("boost", None)  #PlayScript policy3 replaces the previous friendly graph
                self._heal(shooter, target.owner, vitals, ana.HEAL * scalar)
                self.heal_modifiers.setdefault(target.entity, {})["boost"] = {
                    "start": self.now + ana.BOOST_DELAY, "end": self.now + ana.DURATION,
                    "source": _source_entity(shooter), "factor": ana.BOOST,
                }
            elif not (reaper.intangible(target.owner) or defenses.invulnerable(target.owner)):
                self._damage(shooter, target, ana.DAMAGE * scalar, False, blast.loadout,
                             self.tick, world_tick=self.tick)
                if not vitals.dead:
                    self.heal_modifiers.setdefault(target.entity, {})["anti"] = {
                        "start": self.now, "end": self.now + ana.DURATION,
                        "source": _source_entity(shooter), "factor": ana.ANTI,
                    }
                    self._healing_factor(target.owner)

    def _healing_factor(self, owner) -> float:
        entity = _source_entity(owner)
        modifiers = self.heal_modifiers.get(entity)
        if not modifiers:
            return 1.0
        script = getattr(owner, "body_script", None)
        if script is not None:
            for key in ana.CLEANSE_VARS:
                value = script.component.entity_var(key, create=False)
                if value is not None and expr.truthy(value.value()):
                    modifiers.pop("anti", None)
                    break
        for key in list(modifiers):
            if self.now >= modifiers[key]["end"] - EPSILON:
                del modifiers[key]
        if not modifiers:
            self.heal_modifiers.pop(entity, None)
            return 1.0
        if "anti" in modifiers:
            return ana.ANTI
        boost = modifiers.get("boost")
        return boost["factor"] if boost and self.now >= boost["start"] else 1.0

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
            self._heal(healer, target.owner, vitals, amount)

    def _heal(self, healer, owner, vitals: Vitals, amount: float, *, ceiling=None) -> float:
        """Settle actual living-body healing, stats and body health in one place.

        The bounded Ana modifier consumer applies to these normal healing paths.
        Native filter/tag exclusions and healing-charge messages remain open.
        """
        if (vitals.dead or not math.isfinite(amount) or amount <= 0
                or vitals.entity != _source_entity(owner)):
            return 0.0
        amount *= self._healing_factor(owner)
        if ceiling is not None:
            amount = min(amount, ceiling - vitals.total)
        given = vitals.heal(amount)
        if given > 0:
            if not isinstance(healer, bots.Bot):
                healer.stats.add(stats.HEALING, given)
            self._sync_body_health(owner, vitals)
        return given

    def _defense_heal(self, player, *, register=False) -> None:
        """Integrate the declared defensive healing profiles using world time.

        Amount/duration/radius/offset come from the live graph on activation.
        Current capsule poses and static LOS reconstruct Zen's friendly search.
        The cast clock advances even with no eligible targets, so entering range
        never banks prior healing. Missing-window replay retains the interval.
        """
        profile = defenses.healing(player)
        record = self.defense_heals.get(player.body)
        if profile is not None and (record is None or record["activation"] != profile["activation"]):
            if not register:
                return
            record = self.defense_heals[player.body] = {
                **profile, "start": self.now, "end": self.now + profile["duration"],
                "last": self.now, "ended": False,
            }
        if record is None or self.now < record["last"] - EPSILON:
            return
        if profile is None:
            record["end"] = min(record["end"], self.now)
        until = min(self.now, record["end"])
        elapsed = max(0.0, until - record["last"])
        record["last"] = max(record["last"], until)
        record["ended"] = record["ended"] or profile is None or self.now >= record["end"] - EPSILON
        if elapsed <= 0 or self.is_dead(player):
            return
        centre = tuple(a + b for a, b in zip(player.mover.position, record["offset"], strict=True))
        amount = record["rate"] * elapsed
        for target in self._targets():
            if self.replacement_ticks.get(target.entity, self.tick) > self.tick:
                continue  # a replacement body cannot receive healing before its creation
            if (target.owner is not player
                    and (record["self_only"] or self.match.game_map.free_for_all
                         or target.team != player.team)):
                continue
            if not record["self_only"]:
                a, b = target.segment()
                if (segment_distance(centre, a, b) - target.radius > record["radius"]
                        or not capsule_visible(self._collision(), centre, a, b, target.radius)):
                    continue
            self._heal(player, target.owner, self.vitals_of(target), amount)

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

    @staticmethod
    def _fortify_scale(target: Target) -> float | None:
        """Read the authored filter's live amount. Its damage consumers are reconstructed."""
        owner = target.owner
        hero = owner.hero if isinstance(owner, bots.Bot) else owner.hero.guid
        script = getattr(owner, "body_script", None)
        if hero != defenses.ORISA or script is None:
            return None
        for instance in script.component.instances.values():
            if instance.graph.index != 0x0CF4:
                continue
            state = instance.states.get(4)
            if state is not None and state.active:
                amount = state.evaluate(state.node.field("m_50530EAB"), watch=False)
                return max(0.0, min(1.0, 1.0 + _number(amount, 0.0)))
        return None

    def _damage(
        self, shooter, target: Target, amount: float, critical: bool, loadout, frame: int,
        *, world_tick: int | None = None,
        award_charge: bool = True,
        bypass_armour: bool = False,
        beam: bool = False,
        damage_tags=(),
        source_body: int | None = None,
        mercy_credit=None, reflected_reaper_shell=False,
    ) -> bool:
        """Report body contact independently of pool loss at an immortality floor."""
        if (target.entity != _source_entity(target.owner)
                or self.echo_duplicates.restored.get(target.entity) == self.tick):
            return False
        vitals = self.vitals_of(target)
        if (vitals.dead or amount <= 0.0 or reaper.intangible(target.owner)
                or defenses.invulnerable(target.owner)):
            return False
        bubble = self.zarya_bubbles.for_target(target.owner)
        if bubble is not None:
            self.zarya_bubbles.damage(bubble, amount, shooter)
            return False
        amount *= self.discord.scale(target.entity)
        if nano.active(self, vitals):
            amount *= nano.INCOMING
        fortify = self._fortify_scale(target)
        if fortify is not None:
            amount *= fortify
        sound_shield = vitals.temporary_shields.get(lucio_barrier.POOL)
        sound_before = sound_shield.now if sound_shield is not None else 0.0
        floor = self.baptiste_fields.health_floor(target, vitals) if self.baptiste_fields.devices else 0.0
        base = None
        if mercy_credit is not None:
            base = vitals.damage(
                amount * mercy_credit.base_fraction, floor=floor, bypass_armour=bypass_armour,
                beam=beam, preview=True,
            )
        dealt = vitals.damage(amount, floor=floor, bypass_armour=bypass_armour, beam=beam)
        if dealt <= 0.0:
            return floor > 0.0 and amount > 0.0
        if mercy_credit is not None and dealt - base > EPSILON:
            mercy_credit.amplified_targets.add(target.entity)
            mercy_beam.award_boost(
                mercy_credit.staff, _source_entity(shooter), target.entity, dealt, dealt - base,
            )
        sombra.cancel_hack(target.owner)
        if loadout == mei.SPRAY_LOADOUT and vitals.total > 0:
            self._frost_hit(target, source=shooter)
        sleep = self.ana_sleeps.get(target.entity)
        if (sleep and not sleep["waking"] and self.tick < sleep["end"]
                and (self.tick - sleep["start"]) * TICK > ana.SLEEP_DAMAGE_GRACE):
            sleep["end"] = min(sleep["end"], self.tick + math.ceil(ana.SLEEP_WAKE / TICK))
            sleep["waking"] = True
        source_live = source_body is None or source_body == _source_entity(shooter)
        if source_live:
            self._reaping(shooter, target, dealt)
        if (award_charge and source_live and target.owner is not shooter
                and (self.match.game_map.free_for_all or target.team != shooter.team)):
            # The scoped Sound Barrier pool does not feed damage charge.
            shield_loss = sound_before - sound_shield.now if sound_shield is not None else 0.0
            # 0B8B removes Mercy's 3EBB contribution before converting damage to charge.
            base_charge = (dealt if base is None else base) - shield_loss
            reaper.charge_damage(shooter, base_charge, target, damage_tags)
            if reflected_reaper_shell and base_charge > 0:
                script = shooter.body_script
                instance = damage_charge.admitted_source(script)
                if instance is not None:
                    damage_charge.apply(script, instance, base_charge, target, damage_tags)
            if getattr(getattr(shooter, "hero", None), "guid", None) == winston_primal.HERO:
                winston_tesla.charge(shooter.body_script, dealt - shield_loss, target, damage_tags)
            self.echo_duplicates.credit_soldier(shooter, dealt - shield_loss, target, damage_tags)
        victim = target.owner
        killed = vitals.now[HEALTH_POOL] <= EPSILON
        copy_break = killed and not isinstance(victim, bots.Bot) and self.echo_duplicates.restore(victim)
        killed = killed and not copy_break
        if killed:
            vitals.now[HEALTH_POOL] = 0.0
            vitals.nano_until = 0.0
            vitals.died_at = self.now
            vitals.death_tick = self.match.tick if world_tick is None else world_tick
            vitals.killer = _source_entity(shooter)
        if not isinstance(shooter, bots.Bot):
            flags = (CRITICAL if critical else 0) | (KILLING if killed else 0)
            self._tell_hit(shooter, target, dealt, flags, frame)
            shooter.stats.add(stats.HERO_DAMAGE, dealt)
        if copy_break:
            return True
        assists = ()
        if killed and mercy_credit is not None and target.entity in mercy_credit.amplified_targets:
            assists = (expr.Entity(mercy_credit.staff.component.entity),)
        if killed and isinstance(victim, bots.Bot):
            # The NPC death script owns its terminal health change and death event ordering.
            self._bot_died(shooter, victim, critical, loadout, vitals.death_tick, assists)
            return True
        self._sync_body_health(victim, vitals)
        if killed:
            self._died(shooter, victim, critical, loadout, assists)
        return True

    def _reaping(self, shooter, target: Target, dealt: float) -> None:
        ratio = reaper.reaping_ratio(shooter)
        if (not ratio or target.owner is shooter
                or (not self.match.game_map.free_for_all and target.team == shooter.team)):
            return
        vitals = self.vitals.get(shooter.body)
        if vitals is None or vitals.dead:
            return
        self._heal(shooter, shooter, vitals, dealt * ratio)

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
        script = killer.body_script
        # Scope the elimination bridge to Swift Strike's authored reset receiver.
        if (script is not None and not script.component.strict and killer is not victim
                and (self.match.game_map.free_for_all or killer.team != victim.team)):
            for instance in script.component.instances.values():
                if instance.graph.index == 0x01DB:
                    runtime.send_instance_message(
                        instance, 0x0240000000000093, {0x15: expr.Entity(entity)}, entity
                    )
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
    def _death_parameters(killer, critical: bool, loadout, assists) -> dict:
        return {
            KILLER: expr.Entity(_source_entity(killer)),
            CRIT: bool(critical),
            LOADOUT: expr.Asset(loadout) if loadout else None,
            ASSISTS: assists,
        }

    def _bot_died(self, killer, bot: bots.Bot, critical: bool, loadout, tick: int, assists) -> None:
        rocket_punch.retire(self, bot.entity)
        self.sound_barriers.retire(bot.entity)
        self.halts.drop_pull(bot.entity)
        self.stuns.pop(bot.entity, None)
        self.npc_impulses.pop(bot.entity, None)
        self.clear_pins(bot.entity)
        self.ana_sleeps.pop(bot.entity, None)
        self.heal_modifiers.pop(bot.entity, None)
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
            params = self._death_parameters(killer, critical, loadout, assists)
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

    def _died(self, killer, victim, critical: bool, loadout, assists) -> None:
        """A player died: the notices, his body script's "died" (the kill feed line), and the respawn."""
        if victim.body in self.primal_bonus:
            winston_primal.sync(self, victim, victim.body_script, dead=True)
        winston_jump.cancel(self, victim)
        self.winston_jump_seen.pop(victim.body, None)
        sigma_flux.interrupt_flux_lift(self, victim)
        sigma_flux.retire_receiver(victim)
        self.hooks.retire(victim.body)
        self.sound_barriers.retire(victim.body)
        sigma_grasp.retire(self, victim.body)
        self.slams.pop(victim.body, None)
        self.meteors.pop(victim.body, None)
        rocket_punch.retire(self, victim.body)
        self.npc_impulses.pop(victim.body, None)
        self.impulse_landings.pop(victim.body, None)
        self.traps.release(victim.body)
        self.discord.retire(victim.body)
        self._clear_frost(victim)
        self.stuns.pop(victim.body, None)
        self.clear_pins(victim.body)
        self.ana_sleeps.pop(victim.body, None)
        self.heal_modifiers.pop(victim.body, None)
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
                params = self._death_parameters(killer, critical, loadout, assists)
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
