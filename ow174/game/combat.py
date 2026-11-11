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
from itertools import chain

from ow174.game import (
    barrage_motion,
    bots,
    deadeye,
    dva,
    earthshatter,
    flashbang,
    fortify,
    genji_combat,
    graviton,
    health_packs,
    heavy_assault,
    hero_charge,
    hook,
    knockback,
    mei,
    mei_blizzard,
    mei_wall,
    moira,
    moira_biotics,
    npc_melee,
    observers,
    orisa_barrier,
    orisa_halt,
    orisa_supercharger,
    pharah,
    phase_defenses,
    pools,
    projectiles,
    pulse_bomb,
    reaper_blossom,
    reinhardt_barrier,
    reinhardt_charge,
    reinhardt_offense,
    roadhog,
    shadow_step,
    shot_guards,
    sigma_barrier,
    sigma_combat,
    sigma_flux,
    sigma_shields,
    sniper_special,
    sombra_camo,
    sombra_replay,
    sombra_spread,
    stats,
    tracer_combat,
    translocators,
    ultimate_charge,
    voicelines,
    weapon_spread,
    winston_combat,
    world,
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
RECEIVED_DAMAGE = sombra_camo.RECEIVED_DAMAGE  # authored body entries
DAMAGE_TAGS = 0x003D
MODE_UI = 0x0CB6  # the mode's client UI graph: the kill feed 0DEA and the notices 1FC0 (client-only)

EYE = 1.55  # m: STUFirstPersonComponent m_E29993A5, its default (OWLib) and Soldier's (DATA; unverified: eye)
EYE_STANDING = {0x040000000000121C: 1.3, 0x04000000000002A3: 1.425}  # authored camera; muzzle open
EYE_CROUCHED = {0x04000000000003CF: 1.085, 0x040000000000121C: 0.91,
                0x04000000000002A3: 0.9975}  # m_816B830F
EYE_CROUCHED_DEFAULT = 1.15
CRITICAL_CROUCHED_REFERENCE = {0x04000000000003CF: 1.085}  # legacy heuristic, not camera or native bones
HEAD_BELOW = 0.2  # m under the eye where the head starts (unverified)
HISTORY = 64  # ticks of body positions kept for the shots' view time (about 1 s; unverified: retail's limit)
COMBAT_CATCH_UP = 64  # at most about a second of retained combat frames per update
NPC_CATCH_UP = 480  # bounded replay of NPC engagements and committed specials
RANGE = 200.0  # m a hitscan shot reaches when its volley names none (unverified)
RESPAWN = 10.0  # s a hero waits in the PvP modes (0F4A and 0EC2 send 06FD.025 {10}: DATA)
PRACTICE_RESPAWN = 3.0  # s in the Practice Range (13C0 sends 06FD.025 {3}: DATA)
BOT_RESPAWN = 3.0  # s a training bot lies dead (unverified: OW2 made two killed bots again after 2.98 s)
RESPAWN_RETRY = 0.25  # s between attempts when all player body IDs are still reserved
MELEE = {0x0043: (4, 1.5, 1.0)}  # graph -> (its ModifyHealth state, metres ahead of the eye, radius): DATA
ARMOUR_BLOCK = 5.0  # damage armour stops per hit (unverified: OW1's rule; the client has no damage code)
TICK = 0.016
EPSILON = 1e-9
EARLY_CONTACT_PROJECTILES = (
    dva.MICRO_MISSILE_ENTITY,
    0x040000000000072C,  # Helix Rockets
    0x040000000000037D,  # Flashbang
    0x04000000000015E8,  # Sleep Dart
    0x0400000000001426,  # Biotic Grenade
)
FLASHBANG_EFFECT = 0x04A0000000000079  # .053/0079 ->0228 ->224E
SLEEP_DART_EFFECT = 0x04A000000000026F  # Ana 0BDD/14 ModifyHealth
ANA_RIFLE_DURATION = 0.6  # 0B95 rifle effect
ANA = 0x02E000000000013B
NANO_GRAPH = 0x0B9E
SLEEP_DURATION, SLEEP_ENTRY_FLOOR, SLEEP_WAKE_RECOVERY = 5.0, 1.5, 0.5
GRENADE_DURATION, GRENADE_ALLY_HEAL, GRENADE_HEALING_SCALE = 4.0, 100.0, 1.5
FLASHBANG_MODE_FLAG = 13408  # 0228 duration: flag set1.7s, otherwise0.8s


# --- geometry -----------------------------------------------------------------------------------------


def eye_height(body: int, crouched: bool = False) -> float:
    """Authored first-person offsets for known bodies; reconstructed combat origin."""
    return (EYE_CROUCHED.get(body, EYE_CROUCHED_DEFAULT) if crouched
            else EYE_STANDING.get(body, EYE))


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


def capsule_visible(collision: World | None, origin, a, b, radius: float, *, barriers=()) -> bool:
    """Reconstructed visibility: a ray to the nearest capsule surface."""
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
            and not any(reinhardt_barrier.mesh_ray(barrier, origin, direction, reach) is not None
                        for barrier in barriers))


def ray_world(
    collision: World | None, origin, direction, length: float, category: int = MOVER,
) -> float | None:
    """The distance to the first static hit, for callers that only need occlusion."""
    hit = collision.cast_ray(origin, direction, length, category) if collision is not None else None
    return hit.distance if hit is not None else None


# --- health -------------------------------------------------------------------------------------------


@dataclass
class TemporaryShield:
    slot: int
    pool_id: int
    maximum: float
    current: float


class Vitals:
    """A body's pools as component 51 has them: health, armour, shields (world.health's ids and fields)."""

    def __init__(self, entity: int, health: float, armour: float = 0.0, shields: float = 0.0) -> None:
        self.entity = entity
        self.most = {HEALTH_POOL: float(health), ARMOUR_POOL: float(armour), SHIELDS_POOL: float(shields)}
        self.now = dict(self.most)
        self.temporary_shields: dict[tuple, TemporaryShield] = {}
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
        self.sleep_until = 0
        self.sleep_wake_floor = 0
        self.healing_block_until = 0
        self.nano_until = 0
        self.healing_boost_until = 0
        self.ult_healing_deficit = 0.0  # enemy-caused permanent pool loss eligible for Breather credit
        self.shield_recovery_tick = None

    @property
    def dead(self) -> bool:
        return self.died_at is not None

    @property
    def total(self) -> float:
        return sum(self.now.values()) + self.temporary_shield_total

    @property
    def missing_permanent(self) -> float:
        return max(0.0, sum(self.most.values()) - sum(self.now.values()))

    @property
    def maximum(self) -> float:
        return sum(self.most.values()) + sum(pool.maximum for pool in self.temporary_shields.values())

    @property
    def temporary_shield_total(self) -> float:
        return sum(pool.current for pool in self.temporary_shields.values())

    def add_temporary_shield(self, key, amount):
        if key in self.temporary_shields or not math.isfinite(amount) or amount <= 0:
            return
        used = {pool.slot for pool in self.temporary_shields.values() if pool.maximum > 0}
        slot = next((i for i in range(1, POOLS) if i not in used), None)
        if slot is None:
            raise ValueError("temporary shield pool slots exhausted")
        pool_id = max((*self.ids.values(), *(p.pool_id for p in self.temporary_shields.values()))) + 1
        self.temporary_shields = {k: p for k, p in self.temporary_shields.items() if p.slot != slot}
        self.temporary_shields[key] = TemporaryShield(slot, pool_id, amount, amount)
        self.changed.add(SHIELDS_POOL)

    def spend_temporary_shields(self, amount):
        left = amount
        for pool in self.temporary_shields.values():
            taken = min(left, pool.current)
            if taken > 0:
                pool.current -= taken
                left -= taken
                if pool.current <= EPSILON:
                    pool.current = pool.maximum = 0.0
                self.changed.add(SHIELDS_POOL)
        return amount - left

    def damage(
        self, amount: float, *, bypass_armour=False, maximum=math.inf, continuous_armour=False,
    ) -> float:
        """Take a hit from shields, then armour, then health (unverified, as OW1 did: what reaches armour
        while it has some is ARMOUR_BLOCK less, at most halved). Returns what the pools lost."""
        left = max(0.0, amount)
        taken = self.spend_temporary_shields(min(left, maximum))
        left -= taken
        for kind in (SHIELDS_POOL, ARMOUR_POOL, HEALTH_POOL):
            if left <= 0.0 or taken >= maximum:
                break
            if kind == ARMOUR_POOL and self.now[kind] > 0.0 and not bypass_armour:
                left = left * .8 if continuous_armour else max(left - ARMOUR_BLOCK, left / 2)
            take = min(left, self.now[kind], maximum - taken)
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
            if kind == SHIELDS_POOL:
                for pool in self.temporary_shields.values():
                    value = bytearray(world.pool(pool.pool_id, kind, pool.maximum, pool.current))
                    value[17] = 0
                    parts[kind * POOLS + pool.slot] = bytes(value)
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
    tags: tuple[int, ...] = ()  # ModifyHealth's identifier tags, for authored damage reactions
    effect: int | None = None  # ModifyHealth's authored hit-effect resource
    ana_rifle: bool = False

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


def damage_tags(state, modify: dict) -> tuple[int, ...]:
    """Evaluate the authored ModifyHealth tag array (0011.025 parameter003D).

    The event's production timing is reconstructed; the receiver/tag tests and
    the filtering actions are the extracted body's unchanged graph semantics.
    """
    result = []
    for item in modify.get("m_148F3152") or ():
        if isinstance(item, dict):
            value = state.evaluate(item.get("m_EE3055BD"), watch=False)
            if isinstance(value, expr.Asset):
                result.append(value.guid)
    return tuple(result)


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
        critical=value("m_A872C70E", critical_default),
        rings=tuple(sorted(rings)),
        tags=damage_tags(state, modify),
        effect=effect.guid if isinstance(effect, expr.Asset) else None,
        ana_rifle=(state.instance.graph.index == 0x0B94 and state.index in (5, 44)),
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
    owner: object

    @property
    def is_device(self):
        return isinstance(self.owner, (translocators.Translocator, orisa_supercharger.Device))

    def ray(self, origin, direction, reach):
        if isinstance(self.owner, orisa_supercharger.Device):
            return self.owner.ray(origin, direction, reach)
        return ray_capsule(origin, direction, *self.segment(), self.radius)

    def segment(self, feet=None):
        """The capsule's segment (from the feet to the top, a radius in from both ends)."""
        x, y, z = feet or self.feet
        return (x, y + self.radius, z), (x, y + max(self.radius, self.height - self.radius), z)


@dataclass(frozen=True)
class ScrapBurst:
    directions: tuple
    speed: float
    lifetime: float
    direct: HitDef


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
    gravity: float = 0.0
    velocity: tuple[float, float, float] | None = None
    detonate_on_expiry: bool = False
    spawn: int | None = None
    source_body: int = 0
    definition: int = 0
    damage_scale: float = 1.0
    burst: ScrapBurst | None = None
    spray_limit: int = 0
    spray_width: float = 0.0
    spray_hits: tuple[int, ...] = ()
    sigma_instance: int | None = None
    direct_force: knockback.Force | None = None
    blast_force: knockback.Force | None = None
    body_radius: float = 0.0
    wall_radius: float = 0.0
    bounces: int = 0
    bounce_normal: float = 0.0
    bounce_tangent: float = 0.0
    owner_blast: float = 0.0
    owner_force: bool = False
    strike_hits: set = field(default_factory=set)
    pulse: pulse_bomb.ImpactFuse | None = None
    concussion: pharah.Concussion | None = None
    curve: tuple = ()
    range_left: float | None = None
    curve_phase: float = 0.
    barrage_source: barrage_motion.LaunchSource | None = None
    pharah_primary: bool = False
    contact_extent: float = 0.
    primary_impulse: pharah.RocketImpulse | None = None


@dataclass
class BodyHack:
    application: object
    until_tick: int


@dataclass
class AnaRifleDose:
    shooter: object
    source: int
    target: int
    direct: HitDef
    amount: float
    healing: bool
    frame: int
    start_tick: int
    applied: float = 0.0


class Combat:
    """The match's health, hits, deaths and respawns."""

    def __init__(self, match) -> None:
        self.match = match
        self.vitals: dict[int, Vitals] = {}  # body or bot entity -> its health
        self.history: dict[int, deque] = {}  # entity -> (tick, feet, height, eye) of the last HISTORY ticks
        self.replacement_ticks: dict[int, int] = {}  # replacement body -> authoritative world tick
        self.rockets: list[Rocket] = []
        self.hammer_swings = {}
        self.shatters = {}
        self.rein_charges = {}
        self.flux_npcs = {}
        self.flux_players: set[int] = set()
        self.ana_rifle_doses: list[AnaRifleDose] = []
        self.gravitons = graviton.Gravitons(self)
        self.hooks = hook.Hooks(self)
        self.impulses = knockback.Impulses(self)
        self.halt = orisa_halt.Halt(self)
        self.mei = mei.MeiCombat(self)
        self.walls = mei_wall.Walls(self)
        self.blizzard = mei_blizzard.Blizzard(self)
        self.blossom = reaper_blossom.Blossom(self)
        self.shadow = shadow_step.ShadowStep(self)
        self.coalescence = moira.Coalescence(self)
        self.biotics = moira_biotics.Biotics(self)
        self.genji = genji_combat.Combat(self)
        self.superchargers = orisa_supercharger.Superchargers(self)
        self.breathers: dict[int, roadhog.Breather] = {}
        self.fortifies: dict[int, tuple] = {}
        self.orisa_flights: dict[int, orisa_barrier.Flight] = {}
        self.tracer_history = {}
        self.tracer_recalls = {}
        self.pharah_airborne: set[int] = set()
        self.winston_jumps: dict[int, winston_combat.Jump] = {}
        self.winston_primal: dict[int, float] = {}
        self.winston_punches: dict[int, dict[int, int]] = {}
        self.hacks: dict[int, list[BodyHack]] = {}
        self.hack_bases: dict[int, object] = {}
        self.respawns: list[tuple[float, object]] = []  # (when, a player or a bot)
        self.next_bot = bots.BOT_ENTITY + len(match.bots)
        self.now = 0.0
        self.tick = 0
        self.melee_seen: dict[int, dict[tuple[int, int], int]] = {}  # body -> counters by instance/state
        self.stuns: dict[int, int] = {}  # body identity -> exclusive world-tick deadline
        self.pins: dict[int, int] = {}  # controlling source body -> controlled victim body
        self.projectiles = projectiles.Projectiles(self)  # leases, confirmations, the Biotic Field
        self.translocators = translocators.Translocators(self)
        self.barriers = {}
        self.health_packs, self.health_pack_digest = health_packs.mount(match, 0)
        self.ultimate_charge = ultimate_charge.UltimateCharge(self)
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
        dva.retire_source(self.match, player)
        self.tracer_history.pop(player.body, None)
        self.tracer_recalls.pop(player.body, None)
        self.pharah_airborne.discard(player.body)
        self.winston_jumps.pop(player.body, None)
        self.winston_primal.pop(player.body, None)
        self.winston_punches.pop(player.body, None)
        self.hooks.retire(player.body)
        self.impulses.retire(player.body)
        sigma_flux.interrupt_flux_lift(self, player)
        self.flux_players.discard(player.body)
        self.halt.retire(player.body)
        self.mei.retire(player.body)
        self.walls.retire(player.body)
        self.blizzard.retire(player.body)
        self.blossom.retire(player.body)
        self.shadow.retire(player.body)
        self.genji.retire(player.body)
        self.shatters.pop(player.body, None)
        reinhardt_charge.release(self, player)
        for index in reinhardt_offense.SWINGS:
            self.hammer_swings.pop((player.body, index), None)
        self.coalescence.retire(player.body)
        self.superchargers.retire_body(player.body)
        self.breathers.pop(player.body, None)
        self.fortifies.pop(player.body, None)
        self.orisa_flights.pop(player.body, None)
        self.vitals.pop(player.body, None)
        self.history.pop(player.body, None)
        self.replacement_ticks.pop(player.body, None)
        self.melee_seen.pop(player.body, None)
        self.translocators.retire_body(player.body)
        reinhardt_barrier.retire_owner(self, player)
        self.ultimate_charge.retire_body(player.body)
        self.retire_body_hacks(player.body)
        self.retire_ana_rifle_target(player.body)
        self.stuns.pop(player.body, None)
        self.clear_pins(player.body)
        # Keep Graviton's authoritative flight while retiring the old body's prediction.
        for rocket in self.rockets:
            if rocket.shooter is player and rocket.spawn == graviton.FIELD_ENTITY and rocket.shot is not None:
                self.projectiles._end(rocket.shot, self.tick, explode=False)
                rocket.shot = None
        # Other leased projectiles and their observer copies retire with the old body.
        self.rockets[:] = [
            rocket for rocket in self.rockets if rocket.shooter is not player
            or (rocket.shot is None and rocket.definition not in (
                *roadhog.SCRAP_PROJECTILES, *sigma_combat.PROJECTILES,
                reinhardt_offense.FIRE_STRIKE_ENTITY, pulse_bomb.DEFINITION))
        ]
        self.respawns[:] = [(when, who) for when, who in self.respawns if who is not player]

    def record_replacement(self, body: int) -> None:
        """Keep the creation boundary independent of the rolling pose history."""
        self.replacement_ticks[body] = self.match.tick
        self.stuns.pop(body, None)
        self.clear_pins(body)

    def is_stunned(self, actor, *, tick: int | None = None) -> bool:
        entity = _source_entity(actor)
        return (entity in self.pins.values() or self.hooks.controlled(entity)
                or self.control_deadline(entity) > (self.tick if tick is None else tick))

    def control_deadline(self, entity: int) -> int:
        vitals = self.vitals.get(entity)
        return max(self.stuns.get(entity, 0), self.mei.freeze_deadline(entity),
                   vitals.sleep_until if vitals is not None else 0)

    def cleanse_ana_effects(self, entity: int) -> None:
        vitals = self.vitals.get(entity)
        if vitals is not None:
            vitals.sleep_until = vitals.sleep_wake_floor = 0
            vitals.healing_block_until = 0
        self.ana_rifle_doses[:] = [dose for dose in self.ana_rifle_doses
                                  if dose.target != entity or dose.healing]

    def retire_ana_rifle_target(self, entity: int) -> None:
        self.ana_rifle_doses[:] = [dose for dose in self.ana_rifle_doses if dose.target != entity]

    def clear_pins(self, entity: int) -> None:
        for source, victim in list(self.pins.items()):
            if entity in (source, victim):
                del self.pins[source]
                contact = self.rein_charges.get(source)
                if contact is not None and contact.pinned == victim:
                    contact.pinned = contact.mover = None

    def _hit_effect(self, target: Target, effect: int | None) -> None:
        """Apply resource-backed stun or sleep after a resolved character hit."""
        if (mei.protected(target.owner) or phase_defenses.active(target.owner)
                or fortify.active(self, target.owner)):
            return
        if target.is_device:
            return  # character control is not a device effect
        if effect not in (FLASHBANG_EFFECT, SLEEP_DART_EFFECT):
            return
        vitals = self.vitals_of(target)
        if vitals.dead:
            return
        if effect == SLEEP_DART_EFFECT:
            floor = self.tick + math.ceil(SLEEP_ENTRY_FLOOR / TICK)
            if floor > vitals.sleep_until:
                vitals.sleep_until = self.tick + math.ceil(SLEEP_DURATION / TICK)
                vitals.sleep_wake_floor = floor
                if isinstance(target.owner, bots.Bot):
                    heavy_assault.interrupt(target.owner)
                    sniper_special.interrupt(target.owner)
                self._interrupt(target)
            return
        variables = graph.mode_variables(self.match.game_map.mode_guid) or {}
        duration = 1.7 if expr.truthy(variables.get(FLASHBANG_MODE_FLAG)) else 0.8
        self.apply_stun(target, duration)

    def apply_stun(self, target, duration, *, extend_only=False):
        deadline = self.tick + math.ceil(duration / TICK)
        if extend_only and deadline <= self.stuns.get(target.entity, 0):
            return
        if isinstance(target.owner, bots.Bot):
            heavy_assault.interrupt(target.owner)
            sniper_special.interrupt(target.owner)
        self.stuns[target.entity] = max(deadline, self.stuns.get(target.entity, 0))
        self._interrupt(target)

    def _interrupt(self, target: Target) -> None:
        sigma_flux.interrupt_flux_lift(self, target.owner)
        reinhardt_charge.cancel(self, target.owner)
        if not isinstance(target.owner, bots.Bot):
            winston_combat.interrupt_punch(self, target.owner)
        self.hooks.cancel_owner(target.entity)
        roadhog.cancel(self, target.entity)
        script = getattr(target.owner, "body_script", None)
        if script is None:
            return
        orisa_supercharger.cancel_cast(script.component)
        mei_blizzard.cancel_cast(script.component)
        self.blossom.interrupt(target.owner, target.entity)
        self.shadow.interrupt(target.owner, target.entity)
        self.coalescence.interrupt(target.owner, target.entity)
        self.genji.interrupt(target.owner, target.entity)
        moira_biotics.cancel_selection(target.owner)
        if not isinstance(target.owner, bots.Bot):
            self.walls.cancel_preview(target.owner)
        component = script.component
        if isinstance(target.owner, bots.Bot):
            from ow174.game import assassin

            assassin.bind(component.world, component, None)
        # Resolve the hit's queued damage reactions before releasing input.
        # Otherwise a held channel exits as a release and loses its authored
        # damage-interruption cooldown (Sombra Hack is one such channel).
        component.run_now()
        if component.world.hero == pharah.HERO:
            pharah.interrupt_barrage(component)
        component.edges([(value, False) for value in tuple(component.held)])
        for instance in list(component.instances.values()):
            for state in list(instance.states.values()):
                if isinstance(state, nodes.Ability):
                    state.presses.clear()
                    if instance.graph.index != genji_combat.BLADE:
                        state.stop()  # Dragonblade's transform clock continues during temporary control.
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

    def command(self, player, command: Command | None, *, replay_aim: Command | None = None) -> None:
        """Consume posed reports or explicitly accepted projectile releases."""
        if self.match.collision_world is None:
            return
        script = player.body_script
        if script is None:
            return
        self.mei.observe(player)
        winston_combat.sync_primal(self, player, script)
        shots, script.component.shots[:] = list(script.component.shots), []
        dead = self.is_dead(player)
        if not dead and script.hero == winston_combat.HERO and command is not None:
            winston_combat.land(self, player, command)
        if dead or self.is_stunned(player) or tracer_combat.recalling(player):
            self.walls.cancel_preview(player)
            return
        if self.walls.command(player, command):
            return
        self.shadow.command(player, command)
        if command is not None or replay_aim is not None:
            for state, number, time, activation in shots:
                if command is None and not (
                    (script.hero == pharah.HERO and pharah.accepted_volley(state))
                    or (script.hero == sombra_camo.HERO and translocators.accepted_volley(state))
                    or (script.hero == dva.HERO
                        and (state.instance.graph.index, state.node.pos) == (dva.MICRO_MISSILES, 10))
                ):
                    continue
                try:
                    self._shoot(player, command or replay_aim, state, number, time, activation=activation)
                except Exception:
                    log.exception("[game] %s: %s's shot failed", self.match.label(), player.name)
        try:
            self.hooks.command(player, command)
            if command is not None:
                self.blossom.observe(player)
                self.coalescence.observe(player, command)
                self.genji.strike(player, command)
                self.genji.blade(player, command)
            roadhog.observe(self, player)
            fortify.observe(self, player)
            self._zarya_beam(player, command, script)
            moira.grasp(self, player, command)
            winston_combat.tesla(self, player, command, script)
            winston_combat.jump_command(self, player, command, script)
            winston_combat.punch(self, player, command, script)
            if script.hero != reinhardt_offense.HERO:
                self._melee(player, command, script)
            elif command is not None:
                reinhardt_offense.hammer(self, player, command, script)
                earthshatter.start(self, player)
                reinhardt_charge.sync(self, player)
        except Exception:
            log.exception("[game] %s: %s's posed combat failed", self.match.label(), player.name)

    def _zarya_beam(self, player, command, script):
        if player.hero.guid != reinhardt_barrier.ZARYA:
            return
        instance = next((item for item in script.component.instances.values()
                         if item.graph.index == 0x241), None)
        if instance is None:
            return
        ray, health = instance.states.get(81), instance.states.get(10)
        if ray is None or health is None:
            return
        output = expr.lvalue(ray.node.field("m_target.m_692DFF72"))
        if output is None:
            return
        body = barrier = None
        if command is not None and ray.active and health.active:
            reach = expr.to_float(ray.evaluate(ray.node.field("m_F5732403.m_6A79BD98"), watch=False))
            origin, direction = self._eye(player), aim(player.mover.yaw, player.mover.pitch)
            wall = ray_world(self._line_collision(), origin, direction, reach)
            limit = wall if wall is not None else reach + EPSILON
            shield_hit = reinhardt_barrier.ray_hit(self, player, origin, direction, reach)
            if shield_hit is not None and shield_hit[0] < limit:
                limit, barrier = shield_hit
            for target in self._enemies(player):
                distance = target.ray(origin, direction, reach)
                if distance is not None and distance <= reach and distance < limit:
                    limit, body, barrier = distance, target, None
        target = body or barrier
        entity = target.entity if target is not None else 0
        if expr.to_entity(instance.find_var(*output).value()) != entity:
            instance.write(output, expr.Entity(entity) if entity else None)
            instance.component.run_now()
        if not entity or not health.active or expr.to_entity(
            health.evaluate(health.node.field("m_FA3ECC43"), watch=False)
        ) != entity:
            return
        modify = health.node.field("m_modifyHealth")
        rate = expr.to_float(health.evaluate(modify.get("m_amount"), watch=False))
        amount = rate * script.component.frame_ms / 1000
        if barrier is not None:
            reinhardt_barrier.damage(barrier, amount * self.outgoing_scale(player.body), attacker=player.body)
        elif body is not None:
            loadout = expr.guid_of(health.evaluate(modify.get("m_loadout"), watch=False)) or None
            self._damage(player, body, amount, False, loadout, command.frame,
                         tags=damage_tags(health, modify))

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
            origin = self._eye(player)
            centre = _along(origin, aim(*command.aim()), reach)
            view_tick = command.frame - command.view_delay(player.quanta) * 1e6 / player.quanta
            if amount > 0:
                for barrier in reinhardt_barrier.enemy_barriers(self, player):
                    if (isinstance(barrier, orisa_barrier.Shield)
                            and barrier.overlaps_melee(origin, centre, radius, self._line_collision())):
                        barrier.damage(amount * self.outgoing_scale(player.body))
            for target in self._enemies(player):
                shown = self.seen(target, view_tick)
                if shown is None:
                    continue
                a, b = shown.segment()
                if (segment_distance(centre, a, b) - shown.radius <= radius and amount > 0.0
                        and capsule_visible(self._line_collision(), origin, a, b, shown.radius)):
                    tags = damage_tags(state, modify)
                    if (player.hero.guid == sombra_spread.HERO
                            and shot_guards.parries_melee(self, shown, player.mover.position, tags)):
                        continue
                    self._damage(
                        player, target, amount, False, loadout, command.frame,
                        tags=tags, quick_melee=True,
                    )
            if amount > 0 and self.walls.pieces:
                contacts = []
                for piece in self.walls.pieces.values():
                    surface = mei_wall.closest(piece, centre)
                    if math.dist(centre, surface) <= radius and self.walls.reaches(piece, origin, surface):
                        contacts.append(piece)
                damage = amount * self.outgoing_scale(player.body)
                for piece in contacts:
                    self.walls.damage(piece, player, damage, command.frame)

    def update(self, now: float, tick: int, frames: bool = True, *, replay_npc_moves=()) -> None:
        """Each tick: keep where the bodies are, fly the rockets, respawn, send health that changed. The
        other players' body frames only on the ticks that send (`frames`)."""
        if self.match.collision_world is None:
            return
        previous = self.tick
        if tick > previous:
            self.impulses.replay_launches(previous, TICK)
            sigma_flux.replay_player_poses(self, previous, TICK)
            reinhardt_charge.replay(self, previous, TICK)
            winston_combat.replay(self, previous, TICK)
            pharah.replay(self, previous, TICK)
            sombra_replay.replay(self, previous, TICK)
            tracer_combat.replay(self, previous, TICK)
            phase_defenses.replay(self, previous, TICK)
        retained = max(self.tick + 1, tick - COMBAT_CATCH_UP + 1)
        replay_walls = bool(self.walls.pieces)
        for rocket in self.rockets:
            # Pending reports must sweep active walls. Older snapshots can hold
            # a newly released contact projectile without its launch tick.
            if rocket.last_tick is None and (replay_walls or rocket.definition in EARLY_CONTACT_PROJECTILES):
                rocket.last_tick = previous
        first = tick
        if self.tick and tick > self.tick + 1 and (self.gravitons.fields or any(
            bot.firing and not bot.dead and isinstance(bot.body_script, BotScript) for bot in self.match.bots
        )):
            first = retained
        for target in self._targets():
            if not isinstance(target.owner, translocators.Translocator):
                history = self.history.setdefault(target.entity, deque(maxlen=HISTORY))
                history.append((tick, target.feet, target.height, target.eye))
        # Replay the retained combat frames using the most recent poses. Player input
        # accepted just before the scheduler caught up joins only at the endpoint.
        # Keep expiry boundaries even when the ordinary replay window omits those frames.
        expiry_frames = {
            barrier.expiry_tick for barrier in self.barriers.values()
            if isinstance(barrier, (reinhardt_barrier.Barrier, winston_combat.Dome))
            and barrier.expiry_tick is not None
            and previous < barrier.expiry_tick < first
        }
        for vitals in self.vitals.values():
            if vitals.nano_until:
                expiry_frames.update(at for at in (vitals.nano_until - 1, vitals.nano_until)
                                     if previous < at < first)
        # Keep pending rifle impacts/doses on their original ticks even in an omitted prefix.
        dose_ticks = math.ceil(ANA_RIFLE_DURATION / TICK)
        rifle_end = max(
            chain(
                (dose.start_tick + dose_ticks for dose in self.ana_rifle_doses),
                (rocket.last_tick + math.ceil(rocket.left / TICK) + dose_ticks
                 for rocket in self.rockets if rocket.last_tick is not None
                 and rocket.direct is not None and rocket.direct.ana_rifle),
            ),
            default=previous,
        )
        scrap_end = previous
        for rocket in self.rockets:
            if rocket.last_tick is None or rocket.definition not in roadhog.SCRAP_PROJECTILES:
                continue
            duration = rocket.left + (rocket.burst.lifetime if rocket.burst else 0)
            scrap_end = max(scrap_end, rocket.last_tick + math.ceil(duration / TICK))
        emp_replay_end = previous + sombra_replay.emp_replay_frames(self)
        shell_replay_end = previous + dva.self_destruct_replay_frames(self.match)
        from ow174.game.npc_control import engaged

        replay_npcs = set(replay_npc_moves)
        if tick > previous + 1:
            replay_npcs.update(bot for bot in self.match.bots if engaged(bot))
        engagement_replay_end = previous + NPC_CATCH_UP if replay_npcs else previous
        npc_replay_end = max(emp_replay_end, engagement_replay_end)
        replay_end = max(
            rifle_end, scrap_end, self.hooks.replay_until(previous, TICK),
            roadhog.replay_until(self, previous),
            orisa_barrier.replay_until(self, previous, TICK),
            self.superchargers.replay_until(previous, TICK),
            self.mei.replay_until(previous),
            self.walls.replay_until(previous),
            self.blizzard.replay_until(previous),
            self.blossom.replay_until(previous),
            self.shadow.replay_until(previous),
            phase_defenses.replay_until(self, previous),
            self.coalescence.replay_until(previous),
            self.biotics.replay_until(previous, TICK),
            max((r.last_tick + math.ceil(r.left / TICK)
                 + (math.ceil(moira_biotics.TAIL_SECONDS / TICK) if r.spray_width else 0)
                 for r in self.rockets
                 if r.spray_limit or r.spray_width or r.pulse is not None
                 or r.definition in (*EARLY_CONTACT_PROJECTILES, shot_guards.ICICLE, genji_combat.SHURIKEN,
                                     reinhardt_offense.FIRE_STRIKE_ENTITY)),
                default=previous),
            max((deadline + 1 for _, _, deadline in self.fortifies.values()), default=previous),
            previous + sigma_flux.REPLAY_LIMIT if sigma_flux.replay_pending(self) else previous,
            previous + reinhardt_charge.REPLAY_LIMIT if reinhardt_charge.replay_pending(self) else previous,
            previous + winston_combat.replay_frames(self),
            pharah.replay_until(self, previous, TICK),
            previous + translocators.replay_frames(self),
            npc_replay_end, shell_replay_end,
            sombra_replay.application_replay_until(self, previous),
            previous + (tracer_combat.RECALL_REPLAY_LIMIT if self.tracer_recalls else 0),
            max((w.started_tick + math.ceil(w.reach / w.speed / TICK) + 1
                 for w in self.shatters.values()), default=previous),
            previous + knockback.REPLAY_LIMIT if self.impulses.live or self.impulses.launches else previous,
            max((r.last_tick + math.ceil(r.left / TICK) + knockback.REPLAY_LIMIT
                 for r in self.rockets if r.definition in sigma_combat.PROJECTILES), default=previous),
            previous + orisa_halt.REPLAY_LIMIT if self.halt.orbs or self.halt.pulls else previous,
        )
        early_frames = sorted(expiry_frames.union(range(previous + 1, min(first, replay_end + 1))))
        sigma_shield_tick = previous
        has_sigma = any(
            player.hero.guid == sigma_shields.HERO and self.match._shown(player)
            for player in self.match.players
        )
        for at in chain(early_frames, range(first, tick + 1)):
            if at > self.tick + 1:
                deadlines = {actor.until_tick for actor in self.translocators.entries.values()
                             if actor.until_tick < at}
                for deadline in sorted(deadlines):
                    sombra_replay.replay(self, deadline - 1, TICK)
                    self.translocators.expire_before(deadline + 1)
                sombra_replay.replay(self, at - 1, TICK)
            self.now, self.tick = now - (tick - at) * TICK, at
            if self.hacks or at == tick:
                # Retire completed recipient graphs before checking this world's skill gates.
                sombra_replay.advance_applications(self.match, at - 1)
            if self.hacks:
                self._update_body_hacks(at)
            if replay_npc_moves:
                carried = {self.pins.get(bot.entity) for bot in replay_npc_moves}
                for bot in replay_npc_moves:
                    self.match._step_bot(bot, at)
                for target in self._targets():
                    if target.owner in replay_npc_moves or target.entity in carried:
                        history = self.history.get(target.entity)
                        if history and history[-1][0] == tick:
                            history[-1] = (tick, target.feet, target.height, target.eye)
            if replay_walls:
                self.walls.update(at)
            # A beacon can complete an authored recycle within this omitted interval.
            if self.translocators.update(at):
                sombra_replay.replay(self, at - 1, TICK)
            orisa_barrier.advance(self, at, TICK)
            self.superchargers.update(at, TICK)
            reinhardt_barrier.advance(self, at)
            fortify.advance(self, at)
            self.shadow.update(at)
            for rocket in list(self.rockets):
                if at == tick or rocket.last_tick is not None:
                    self._fly(rocket)
            self.hooks.update(at)
            sigma_flux.update_npcs(self, at, TICK)
            if at >= first or at <= emp_replay_end:
                self._bot_weapons(at)
            elif at <= npc_replay_end or at <= shell_replay_end:
                self._bot_weapons(
                    at, replay_npcs=replay_npcs if at <= npc_replay_end else (),
                    replay_shells=at <= shell_replay_end,
                )
            elif self.flux_npcs or sigma_flux.replay_pending(self):
                self._bot_weapons(at, replay_npcs=())
            self._advance_ana_rifle()
            self.gravitons.update(at, TICK)
            self.halt.update(at, TICK)
            self.impulses.update(at, TICK)
            roadhog.advance(self)
            reinhardt_barrier.expire_bubbles(self, at)
            self.blizzard.update(at)
            self.mei.update(at)
            self.blossom.update(at)
            self.coalescence.update(at)
            self.biotics.update(at, TICK)
            earthshatter.update(self)
            if at < tick:
                if has_sigma:
                    sigma_shields.update(self, sigma_shield_tick, TICK)
                    sigma_shield_tick = at
                if at <= npc_replay_end:
                    heavy_assault.sync_captures(self)
                self.impulses.replay_launches(at, TICK)
                sigma_flux.replay_player_poses(self, at, TICK)
                reinhardt_charge.replay(self, at, TICK)
                winston_combat.replay(self, at, TICK)
                pharah.replay(self, at, TICK)
                sombra_replay.replay(self, at, TICK)
                tracer_combat.replay(self, at, TICK)
                phase_defenses.replay(self, at, TICK)
                if at <= shell_replay_end:
                    dva.finish_replayed_shell_frame(self.match, at)
        for actor in self.translocators.entries.values():
            x, y, z = actor.position
            radius = translocators.RADIUS
            history = self.history.setdefault(actor.entity, deque(maxlen=HISTORY))
            history.append((tick, (x, y - radius, z), 2 * radius, radius))
        self.projectiles.update(tick, first_tick=retained)
        self.match.deliver_body_messages()
        dva.retire_spent_shells(self.match)
        heavy_assault.sync_captures(self)
        health_packs.advance(self, tick)
        sigma_shields.update(self, sigma_shield_tick, TICK)
        sigma_shields.recover_base(self, TICK)
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
        for vitals in self.vitals.values():
            if vitals.sleep_until and vitals.sleep_until <= tick:
                vitals.sleep_until = vitals.sleep_wake_floor = 0
            if vitals.healing_block_until and vitals.healing_block_until <= tick:
                vitals.healing_block_until = 0
            if vitals.healing_boost_until and vitals.healing_boost_until <= tick:
                vitals.healing_boost_until = 0
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
            if dva.controlled_mech(player) is not None:
                continue
            data = mover_data(player.hero.body)
            crouched = player.mover.crouched
            height = data.crouch_height if crouched else data.stand_height
            # Target.eye belongs to the old critical-region heuristic. A camera
            # offset is not evidence for a critical volume; do not conflate them.
            eye = CRITICAL_CROUCHED_REFERENCE.get(player.hero.body, EYE_CROUCHED_DEFAULT) if crouched else EYE
            found.append(
                Target(player.body, player.team, tuple(player.mover.position), height, data.stand_radius, eye,
                       player)
            )  # fmt: skip
        for bot in self.match.bots:
            if bot.dva_shell:
                continue
            data = mover_data(bot.body)
            found.append(
                Target(
                    bot.entity, bot.team, tuple(bot.position), data.stand_height, data.stand_radius, EYE, bot
                )
            )
        for device in self.superchargers.active.values():
            found.append(Target(device.entity, device.team, device.position, device.height,
                                device.radius, device.height / 2, device))
        for actor in self.translocators.entries.values():
            x, y, z = actor.position
            radius = translocators.RADIUS
            found.append(Target(actor.entity, actor.team, (x, y - radius, z),
                                2 * radius, radius, radius, actor))
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

    def _enemies(self, shooter, *, team=None) -> list[Target]:
        free_for_all = self.match.game_map.free_for_all
        team = shooter.team if team is None else team
        source = _source_entity(shooter)
        found = []
        for target in self._targets():
            if target.owner is shooter or target.entity == source:
                continue
            if target.is_device and target.owner.owner is shooter:
                continue
            if not free_for_all and target.team == team:
                continue
            if self.vitals_of(target).dead or phase_defenses.active(target.owner):
                continue
            found.append(target)
        return found

    def _ana_rifle_allies(self, shooter, team):
        if self.match.game_map.free_for_all:
            return
        for target in self._targets():
            if (target.owner is shooter or target.team != team
                    or target.is_device):
                continue
            vitals = self.vitals_of(target)
            if not vitals.dead and sum(vitals.now.values()) < sum(vitals.most.values()) - EPSILON:
                yield target

    def _queue_ana_rifle(self, shooter, source, target, direct, amount, frame, team):
        healing = not self.match.game_map.free_for_all and target.team == team
        self.ana_rifle_doses.append(
            AnaRifleDose(shooter, source, target.entity, direct, amount, healing, frame, self.tick),
        )

    def _advance_ana_rifle(self):
        if not self.ana_rifle_doses:
            return
        targets = {target.entity: target for target in self._targets()}
        doses, self.ana_rifle_doses = self.ana_rifle_doses, []
        for dose in doses:
            target = targets.get(dose.target)
            if target is None or self.vitals_of(target).dead:
                continue
            share = min(1.0, (self.tick - dose.start_tick) * TICK / ANA_RIFLE_DURATION)
            # Quantize cumulative delivery so all shares sum to the authored amount.
            cumulative = dose.amount if share >= 1 else round(dose.amount * share * 1024) / 1024
            amount = cumulative - dose.applied
            dose.applied = cumulative
            if amount > 0.0:
                if dose.healing:
                    self.heal_target(target, amount, healer=dose.shooter, source_body=dose.source)
                else:
                    self._damage(dose.shooter, target, amount, False, dose.direct.loadout, dose.frame,
                                 world_tick=self.tick, tags=dose.direct.tags, source_body=dose.source,
                                 source_scaled=True, bypass_armour=True)
            if share < 1 and not self.vitals_of(target).dead:
                self.ana_rifle_doses.append(dose)

    def opportunist_contacts(self, viewer) -> tuple[dict, ...]:
        """Derive Sombra's health-bar and outline eligibility for match inspection."""
        if (viewer.hero.guid != sombra_spread.HERO or not viewer.has_body or not viewer.spawned
                or viewer.client is None or viewer.gone or self.is_dead(viewer)
                or self.match.collision_world is None):
            return ()
        origin = self._eye(viewer)
        found = []
        for target in self._targets():
            if target.owner is viewer or target.is_device:
                continue
            if not self.match.game_map.free_for_all and target.team == viewer.team:
                continue
            vitals = self.vitals.get(target.entity)
            if vitals is None or vitals.dead or vitals.total >= vitals.maximum:
                continue
            if (phase_defenses.active(target.owner)
                    or not self.visible_for_acquisition(target, viewer.mover.position)):
                continue
            outline = 2 * vitals.total <= vitals.maximum
            a, b = target.segment()
            health_bar = capsule_visible(self._line_collision(), origin, a, b, target.radius)
            if outline or health_bar:
                found.append({"entity": target.entity, "outline": outline, "health_bar": health_bar})
        return tuple(found)

    @staticmethod
    def _inside_camo_radius(state, origin, feet):
        radius = _number(state.evaluate(state.node.field("m_radius"), watch=False), 0.0)
        offset = _sub(feet, origin)
        return radius > 0.0 and _dot(offset, offset) <= radius * radius

    def visible_for_acquisition(self, target, observer_position):
        """Reconstruct acquisition during the original Camo proximity scope."""
        script = getattr(target.owner, "body_script", None)
        if script is None:
            return True
        for instance in script.component.instances.values():
            if instance.graph.index != 0x0C9B:
                continue
            tracker = instance.states.get(instance.graph._by_pos[128].state)
            if tracker is not None and tracker.active and not tracker.ending:
                return self._inside_camo_radius(tracker, target.feet, observer_position)
        return True

    def _aimed_targets(self, player, state, *, visor=False):
        """Host Deadeye and Tactical Visor's live capsule searches."""
        if self.is_dead(player):
            return ()
        radius = _number(state.evaluate(state.node.field("m_radius"), watch=False), 0.0)
        angle = _number(state.evaluate(state.node.field("m_A30B383E"), watch=False), 0.0)
        facing = aim(player.mover.yaw, player.mover.pitch)
        minimum_dot = math.cos(math.radians(angle * 0.5))
        origin = self._eye(player)
        found = []
        for target in self._enemies(player):
            if target.is_device or mei.protected(target.owner):
                continue
            if not self.visible_for_acquisition(target, player.mover.position):
                continue
            a, b = target.segment()
            toward = _sub(tuple((lower + upper) / 2 for lower, upper in zip(a, b, strict=True)), origin)
            projection = _dot(facing, toward)
            length = math.sqrt(_dot(toward, toward))
            if projection < minimum_dot * length:
                continue
            distance = max(0.0, segment_distance(origin, a, b) - target.radius)
            if distance > radius:
                continue
            if state.node.pos == 26 or capsule_visible(self._line_collision(), origin, a, b, target.radius):
                # Visor's first list entry drives rifle aim. Approximate its
                # native scoring with angular alignment and distance as tiebreak.
                alignment = projection / max(length, EPSILON)
                found.append((-alignment if visor else distance, distance, target.entity))
        return tuple(entity for _, _, entity in sorted(found))

    def track_body_targets(self, player, state):
        if state.instance.graph.index == sigma_flux.FLUX:
            return sigma_flux.flux_targets(state.instance.component.world, state)
        if state.instance.graph.index == dva.BOOSTERS:
            return dva.boost_targets(self, player, state)
        """Reconstructed capsule/LOS acquisition for Sombra's three weapon searches.

        Bodies and mounted health packs use their reconstructed collision shapes.
        """
        if (player.hero.guid == moira.HERO and state.instance.graph.index == moira.GRASP
                and state.index == 106):
            return moira.grasp_targets(self, player, state)
        if (player.hero.guid == moira.HERO and state.instance.graph.index == moira.COALESCENCE
                and state.index in (17, 19)):
            return self.coalescence.track(player, state)
        if (player.hero.guid == ANA and state.instance.graph.index == NANO_GRAPH
                and state.node.pos in (8, 14)):
            return self._nano_targets(player, state)
        if (player.hero.guid == 0x02E0000000000042 and state.instance.graph.index == 0x022F
                and state.node.pos in (26, 86)):
            return self._aimed_targets(player, state)
        if (player.hero.guid == 0x02E000000000006E and state.instance.graph.index == 0x0259
                and state.node.pos in (11, 36)):
            return self._aimed_targets(player, state, visor=True)
        if (player.hero.guid == sombra_spread.HERO and state.instance.graph.index == 0x0C9B
                and state.node.pos == 128):
            if self.is_dead(player):
                return ()
            return tuple(target.entity for target in self._enemies(player)
                         if not target.is_device
                         and self._inside_camo_radius(state, player.mover.position, target.feet))
        if (player.hero.guid == 0x02E000000000012E and state.instance.graph.index == 0x0B31
                and state.node.pos == 40):
            return self._emp_body_targets(player, state)
        if (player.hero.guid != 0x02E000000000012E or state.instance.graph.index != 0x0B1B
                or state.node.pos not in (50, 52, 68)):
            return NotImplemented
        if self.is_dead(player):
            return ()
        radius = _number(state.evaluate(state.node.field("m_radius"), watch=False), 0.0)
        origin, direction = self._eye(player), aim(player.mover.yaw, player.mover.pitch)
        locked = state.instance.find_var(graph.INSTANCE, 375).value()
        locked_ids = {expr.to_entity(value) for value in locked} if isinstance(locked, tuple) else set()
        found = []
        for target in self._hack_targets(player):
            if target.is_device:
                continue
            if (reinhardt_barrier.body_bubble(self, target.owner) is not None
                    or not self.visible_for_acquisition(target, player.mover.position)):
                continue
            if state.node.pos == 52 and target.entity not in locked_ids:
                continue
            a, b = target.segment()
            nearest = (a[0], max(a[1], min(b[1], origin[1])), a[2])
            offset = _sub(nearest, origin)
            distance = math.sqrt(_dot(offset, offset))
            if distance - target.radius > radius:
                continue
            if state.node.pos == 52:
                if _dot(offset, direction) <= 0 or distance <= EPSILON:
                    continue
                ray = tuple(axis / distance for axis in offset)
                hit = max(0.0, distance - target.radius)
            else:
                ray = direction
                hit = ray_capsule(origin, ray, a, b, target.radius)
                if hit is None or hit > radius:
                    continue
            wall = ray_world(self._line_collision(), origin, ray, hit)
            if ((wall is None or wall >= hit)
                    and reinhardt_barrier.ray_hit(self, player, origin, ray, hit) is None):
                found.append((hit, target.entity))
        return tuple(entity for _, entity in sorted(found)[:1])

    def _hack_targets(self, player):
        yield from (target for target in self._enemies(player) if not mei.protected(target.owner))
        for pack in self.health_packs.values():
            if not health_packs.available_to(self, pack, player):
                continue
            # A spherical approximation of the pickup selection volume.
            yield Target(pack.entity, None, pack.position, 0.7, 0.35, 0.35, pack)

    def _emp_body_targets(self, player, state):
        """Reconstructed radial EMP search with static LOS.

        Bodies and attached Zarya barriers use the covered body's capsule.
        Reinhardt's barrier uses its own anchor.
        """
        if self.is_dead(player):
            return ()
        origin = self._eye(player)
        radius = _number(state.evaluate(state.node.field("m_radius"), watch=False), 0.0)
        found = []
        barriers = tuple(reinhardt_barrier.enemy_barriers(self, player))
        bubbles = {}
        for barrier in barriers:
            if barrier.component.world.hero == reinhardt_barrier.ZARYA:
                bubbles.setdefault(barrier.protected_body, []).append(barrier.entity)
        for target in self._hack_targets(player):
            if target.is_device:
                continue  # 1575 declares 0145/08C3, outside the hosted 003A branch
            a, b = target.segment()
            point = (a[0], max(a[1], min(b[1], origin[1])), a[2])
            offset = _sub(point, origin)
            distance = math.sqrt(_dot(offset, offset))
            hit = max(0.0, distance - target.radius)
            if hit > radius:
                continue
            ray = tuple(axis / distance for axis in offset) if distance > EPSILON else (0.0, 0.0, 1.0)
            wall = ray_world(self._line_collision(), origin, ray, hit)
            if wall is None or wall >= hit:
                found.append(target.entity)
                found.extend(bubbles.get(target.entity, ()))
        for barrier in barriers:
            if barrier.component.world.hero not in (
                reinhardt_barrier.HERO, orisa_barrier.HERO, sigma_barrier.HERO, winston_combat.HERO,
            ):
                continue
            anchor, _ = reinhardt_barrier.pose(barrier)
            offset = _sub(anchor, origin)
            distance = math.sqrt(_dot(offset, offset))
            if distance <= radius and (distance <= EPSILON or ray_world(
                self._line_collision(), origin, tuple(axis / distance for axis in offset), distance,
            ) is None):
                found.append(barrier.entity)
        return tuple(found)

    def _nano_targets(self, player, state):
        if self.match.game_map.free_for_all or self.is_dead(player) or self.is_stunned(player):
            return ()
        radius = _number(state.evaluate(state.node.field("m_radius"), watch=False), 0.0)
        origin = self._eye(player)
        facing = aim(player.mover.yaw, player.mover.pitch)
        minimum_dot = math.cos(math.radians(10))  # Base cone in 0B9E/8 and /14.
        candidates = []
        for target in self._targets():
            if (target.owner is player or target.team != player.team
                    or target.is_device
                    or self.vitals_of(target).dead):
                continue
            centre = (target.feet[0], target.feet[1] + target.height / 2, target.feet[2])
            toward = _sub(centre, origin)
            distance = math.hypot(*toward)
            if not EPSILON < distance <= radius:
                continue
            alignment = _dot(facing, toward) / distance
            if alignment < minimum_dot:
                continue
            ray = tuple(value / distance for value in toward)
            wall = ray_world(self._line_collision(), origin, ray, distance)
            if wall is None or wall >= distance - EPSILON:
                candidates.append((-alignment, distance, target.entity))
        return (min(candidates)[2],) if candidates else ()

    def _release_nano(self, player, state):
        if self.match.game_map.free_for_all or self.is_dead(player) or self.is_stunned(player):
            return
        selected = state.instance.find_var(expr.INSTANCE, 8070, create=False)
        lifetime = state.instance.find_var(expr.INSTANCE, 215, create=False)
        targets = selected.value() if selected is not None else ()
        targets = (targets,) if isinstance(targets, expr.Entity) else targets
        duration = _number(lifetime.value(), 0) if lifetime is not None else 0
        if not isinstance(targets, tuple) or not math.isfinite(duration) or duration <= 0:
            return
        for target in self._targets():
            if (expr.Entity(target.entity) not in targets or target.owner is player
                    or target.team != player.team or target.is_device):
                continue
            vitals = self.vitals_of(target)
            if not vitals.dead:
                self.heal_target(target, 250.0, healer=player)  # 0B9F
                vitals.nano_until = max(vitals.nano_until, self.tick + math.ceil(duration / TICK))

    def prepare_body_targets(self, player, command):
        """Reconstruct Sombra's client-owned preselection from authoritative aim.

        0B1B/node68 is client-only. The legacy input protocol has no implemented
        target-intent consumer, so only its body/capsule selection is reconstructed
        here. The server's channel/cooldown/application plugs remain authored.
        """
        if (player.body_script is not None and player.hero.guid == reinhardt_offense.HERO
                and not self.is_dead(player)):
            earthshatter.prepare(player.body_script.world, player)
        if player.hero.guid != 0x02E000000000012E or self.is_dead(player):
            return
        script = player.body_script
        if script is None or not command.buttons & 2:
            return
        for instance in script.component.instances.values():
            if instance.graph.index != 0x0B1B:
                continue
            ability = instance.state(53)
            selected = instance.find_var(graph.INSTANCE, 375).value()
            if ability.flags & nodes.Ability.RUNNING and selected:
                return
            search = instance.node_state(instance.graph._by_pos[68])
            entities = self.track_body_targets(player, search)
            instance.write((graph.INSTANCE, 375), tuple(expr.Entity(entity) for entity in entities))

    def track_body_ray(self, player, state):
        """Reconstructed Hack beam visibility; animated hardpoint selection remains open."""
        if (player.hero.guid != 0x02E000000000012E or state.instance.graph.index != 0x0B1B
                or state.node.pos != 59):
            return NotImplemented
        entity = expr.to_entity(state.instance.find_var(graph.INSTANCE, 8909).value())
        target = next((target for target in self._hack_targets(player) if target.entity == entity), None)
        if (target is None or reinhardt_barrier.body_bubble(self, target.owner) is not None
                or not self.visible_for_acquisition(target, player.mover.position)):
            return False, None
        origin = self._eye(player)
        point = (target.feet[0], target.feet[1] + target.eye, target.feet[2])
        offset = _sub(point, origin)
        distance = math.sqrt(_dot(offset, offset))
        if distance <= EPSILON:
            return True, expr.Vec3(*point)
        direction = tuple(axis / distance for axis in offset)
        reach = max(0.0, distance - target.radius)
        wall = ray_world(self._line_collision(), origin, direction, reach)
        clear = wall is None and reinhardt_barrier.ray_hit(self, player, origin, direction, reach) is None
        return clear, expr.Vec3(*point)

    def apply_body_hack(self, target, application):
        """Reconstructed character silence, using the authored five-second branch value.

        Native tag-set assembly and the associated engine control properties are
        not mounted. This policy suppresses skills while retaining weapon fire,
        reload and quick melee; application frames/ownership remain authored.
        """
        duration = _number(application.evaluate(
            application.graph._by_pos[96].field("m_value"),
        ), 0.0)
        if (duration <= 0 or self.vitals_of(target).dead or phase_defenses.active(target.owner)
                or mei.protected(target.owner)):
            return
        component = application.component
        if target.entity not in self.hacks:
            self.hack_bases[target.entity] = component.entity_var(888).base
        self.hacks.setdefault(target.entity, []).append(
            BodyHack(application, self.match.tick + math.ceil(duration / TICK)),
        )
        component.set(888, True)
        if component.world.hero == pharah.HERO:
            pharah.silence_hover(component)
            pharah.interrupt_barrage(component)
        if component.world.hero == sigma_barrier.HERO and 29 in component.held:
            sigma_barrier.silence(component)
        sigma_flux.interrupt_flux_lift(self, target.owner)
        reinhardt_charge.cancel(self, target.owner)
        if not isinstance(target.owner, bots.Bot):
            self.walls.cancel_preview(target.owner)
        orisa_supercharger.cancel_cast(component)
        mei_blizzard.cancel_cast(component)
        self.blossom.interrupt(target.owner, target.entity)
        self.shadow.interrupt(target.owner, target.entity)
        self.coalescence.interrupt(target.owner, target.entity)
        self.genji.interrupt(target.owner, target.entity)
        moira_biotics.cancel_selection(target.owner)
        self.hooks.cancel_owner(target.entity)
        roadhog.cancel(self, target.entity)
        if getattr(getattr(target.owner, "hero", None), "guid", None) == reinhardt_barrier.HERO:
            reinhardt_barrier.silence(component)
        if isinstance(target.owner, bots.Bot):
            heavy_assault.interrupt(target.owner)
            sniper_special.interrupt(target.owner)
            from ow174.game import assassin

            assassin.bind(component.world, component, None)

    def apply_emp_health(self, state):
        """Apply EMP's tagged barrier damage or capped character-shield drain."""
        if state.instance.graph.index != 0x0B32 or state.node.pos not in (3, 9):
            return
        source = state.instance.source_entity()
        shooter = next((p for p in self.match.players if p.has_body and p.body == source), None)
        if shooter is None:
            return
        entity = state.instance.component.entity
        config = state.node.field("m_modifyHealth")
        amount = _number(state.evaluate(config.get("m_amount"), watch=False), 0.0)
        if state.node.pos == 3:
            barrier = self.barriers.get(entity)
            if barrier is not None and barrier.component is state.instance.component:
                reinhardt_barrier.damage(barrier, amount)
            return
        target = next((t for t in self._targets() if t.entity == entity), None)
        if target is None or self.vitals_of(target).dead:
            return
        vitals = self.vitals_of(target)
        amount = min(amount, vitals.now[SHIELDS_POOL] + vitals.temporary_shield_total)
        self._damage(shooter, target, amount, False, None, self.match.tick,
                     tags=damage_tags(state, config), shield_drain=True)

    def retire_body_hacks(self, entity):
        effects = self.hacks.pop(entity, ())
        for effect in effects:
            application = effect.application
            if application.component.instances.get(application.id) is application:
                application.component.destroy(application)
        if effects:
            effects[-1].application.component.set(888, self.hack_bases.pop(entity, None))

    def _update_body_hacks(self, tick):
        targets = {target.entity: target for target in self._targets()}
        for entity, effects in list(self.hacks.items()):
            target = targets.get(entity)
            if target is None or self.vitals_of(target).dead:
                self.retire_body_hacks(entity)
                continue
            keep = []
            for effect in effects:
                application = effect.application
                if tick < effect.until_tick and not application.stopped:
                    keep.append(effect)
                elif application.component.instances.get(application.id) is application:
                    application.component.destroy(application)
            if keep:
                self.hacks[entity] = keep
            else:
                self.retire_body_hacks(entity)

    def body_ability_allowed(self, state):
        if state.instance.component.entity not in self.hacks:
            return True
        instance = state.instance
        if instance.graph.index == genji_combat.BLADE and state.flags & nodes.Ability.RUNNING:
            return True  # The drawn weapon persists through silence. The initial cast is gated below.
        if (instance.graph.index == 0x0043
                or npc_melee.graph_entry(instance.component.world, state, npc_melee.ABILITY)):
            return True
        if instance.graph.index == 0x0B1B and state.index == 53:  # Sombra's weapon-hosted Hack
            return False
        if instance.graph.index == 0x0095 and state.node.pos == 136:  # Reinhardt's weapon-hosted barrier
            return False
        target = next((t for t in self._targets() if t.entity == instance.component.entity), None)
        if target is None:
            return False
        body = target.owner.body if isinstance(target.owner, bots.Bot) else target.owner.hero.body
        record = graph.entity_body(body) or graph.bodies().get(body, {})
        weapons = set(record.get("weapons") or ()) - {None}
        if (not isinstance(target.owner, bots.Bot) and target.owner.hero.guid == 0x02E000000000006E
                and 29 in state.buttons()):
            return False  # Soldier's weapon-hosted secondary is Helix, not another ordinary shot.
        parent = instance
        while parent is not None:
            if parent.graph.index in weapons:
                return True
            parent = parent.parent
        # Only active input abilities are silenced; passive control/UI states
        # without a start/hold/stop input keep their ordinary graph lifetimes.
        return not any(state.buttons())

    def body_button_allowed(self, component, button):
        if component.entity in self.tracer_recalls and button in tracer_combat.RECALL_BLOCKED_BUTTONS:
            return False
        if button == 36 and self.translocators.recall_blocked_by_graviton(component.entity):
            return False  # 0C19's deployed recall uses a LogicalButton.
        if component.entity not in self.hacks or button in (14, 20, 31, 111):
            return True
        if component.world.hero == tracer_combat.HERO and button in tracer_combat.BLINK_BUTTONS:
            return False  # Both Blink bindings are logical-button branches.
        if (component.world.hero == pharah.HERO
                and (button == pharah.BARRAGE_BUTTON or button in pharah.HOVER_BUTTONS)):
            return False
        if button == 29 and component.world.hero == sigma_barrier.HERO:
            return False  # 1B55's deploy and recall are logical button branches.
        if button == 36 and component.world.hero == 0x02E00000000001BB:
            return False  #1157 uses a LogicalButton rather than an Ability state.
        for instance in component.instances.values():
            for state in instance.states.values():
                if (isinstance(state, nodes.Ability) and state.active and button in state.buttons()
                        and not self.body_ability_allowed(state)):
                    return False
        return True

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

    def _bot_weapons(self, tick: int, *, replay_npcs=None, replay_shells=False) -> None:
        """Advance the weapon bodies of the map's firing bots and consume their volley reports."""
        from ow174.game import npc_control

        for bot in tuple(self.match.bots):
            script = bot.body_script
            if (replay_npcs is not None and bot not in replay_npcs
                    and not (replay_shells and bot.dva_shell)
                    and not sigma_flux.replaying_npc(self, bot)):
                continue
            controlled = npc_control.manual(bot)
            if ((not bot.firing and not controlled and not bot.dva_shell) or bot.dead or bot.dva_shell_spent
                    or not isinstance(script, BotScript) or script.frame > tick):
                continue
            try:
                allowed = npc_control.update_input(self, bot) if controlled else not self.is_stunned(bot)
                self.advance_bot_weapon(bot, script, tick, allowed and not bot.dva_shell)
            except Exception:
                self.clear_pins(bot.entity)
                bot.body_script = None
                log.exception("[game] %s: %s's weapon script failed", self.match.label(), bot.name)

    def advance_bot_weapon(self, bot, script, frame, allowed):
        snapshot = script.advance_live(frame, bot.position)
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

    def _eye(self, player) -> tuple:
        x, y, z = player.mover.position
        crouched = player.mover.crouched
        height = eye_height(player.hero.body, crouched)
        return (x, y + height, z)

    def _visor_direction(self, shooter, component, origin, view_tick):
        slot = component.entity_var(2337, create=False)
        selected = slot.value() if slot is not None else None
        if not selected:
            return None
        entity = expr.to_entity(selected[0])
        target = next((target for target in self._enemies(shooter) if target.entity == entity), None)
        if target is None or not self.visible_for_acquisition(target, shooter.mover.position):
            return None
        a, b = target.segment()
        if not capsule_visible(self._line_collision(), origin, a, b, target.radius):
            return None
        # Keep acquisition live, but aim at the same sampled capsule that the
        # rifle's lag-compensated collision will use.
        shown = self.seen(target, view_tick)
        if shown is None:
            return None
        _, upper = shown.segment()
        x, y, z = shown.feet
        toward = _sub((x, min(y + shown.eye, upper[1]), z), origin)
        length = math.hypot(*toward)
        return tuple(value / length for value in toward) if length > EPSILON else None

    def _shoot(
        self, shooter, command, state, number: int, time: int,
        *, activation: tuple[int, int], snapshot: Snapshot | None = None,
    ) -> None:
        node = state.node
        shot_frame = time // state.instance.component.frame_ms
        if (state.instance.graph.index, node.pos) == (NANO_GRAPH, 4):
            self._release_nano(shooter, state)
            return

        def field(name, default=None):
            cfg = node.field(name)
            return default if cfg is None else _number(state.evaluate(cfg, watch=False), default)

        npc = isinstance(shooter, bots.Bot)
        deadeye_volley = not npc and state.instance.graph.index == deadeye.TARGET_EFFECT and node.pos == 14
        if npc:
            x, y, z = shooter.position
            # Use the server body pose until the animated muzzle transform is available.
            origin, direction = (x, y + EYE, z), aim(round(shooter.yaw), round(shooter.pitch))
        else:
            origin, direction = self._eye(shooter), aim(*command.aim())
            if (state.instance.graph.index, node.pos) == (mei_blizzard.CAST, mei_blizzard.VOLLEY):
                self.blizzard.launch(shooter, state, shot_frame, origin, direction,
                                     activation=activation, snapshot=snapshot)
                return
            if (state.instance.graph.index, node.pos) == (orisa_halt.HALT, 8):
                self.halt.fired(shooter, state, origin, direction)
                return
            if (state.instance.graph.index, node.pos) == orisa_supercharger.THROW:
                self.superchargers.launch(shooter, state, origin, direction, command.aim()[0])
                return
            if (state.instance.graph.index, node.pos) == orisa_barrier.THROW:
                orisa_barrier.launch(self, shooter, state, origin, direction, command.aim()[0])
                return
            direction = sombra_spread.direction(shooter, state, direction, number, time, activation)
            if deadeye_volley:
                directed = deadeye.shot_aim(self, shooter, state)
                if directed is None:
                    return
                origin, direction = directed
        sombra_pistol = (
            not npc and shooter.hero.guid == sombra_spread.HERO
            and state.instance.graph.index == 0x0B1B
        )
        tracer_pistol = not npc and tracer_combat.pistol_volley(state)
        roadhog_weapon = not npc and state.instance.graph.index == roadhog.SCRAP_GUN
        scrap_primary = roadhog_weapon and node.pos == roadhog.PRIMARY
        scrap_secondary = roadhog_weapon and node.pos == roadhog.SECONDARY
        whole_hog = roadhog_weapon and node.pos == knockback.WHOLE_HOG[1]
        reaper_shotgun = not npc and state.instance.graph.index == reaper_blossom.SHOTGUN
        genji_weapon = not npc and state.instance.graph.index == genji_combat.WEAPON
        icicle = not npc and (state.instance.graph.index, node.pos) == mei.ICICLE
        # Use the scoped 2x critical profile where the native multiplier is unbound.
        critical_profile = (sombra_pistol or tracer_pistol or whole_hog or scrap_primary or icicle
                            or reaper_shotgun or genji_weapon)
        critical_default = 2.0 if critical_profile else 1.0
        direct = hit_def(state, node.field("m_6396149F"), critical_default=critical_default)
        speed = field("m_projectileSpeed")
        if speed:
            launch = None if npc else flashbang.launch(shooter, state, direction)
            sigma_launch = None if npc else sigma_combat.launch(shooter, state, direction)
            sphere = sigma_launch is not None and state.instance.graph.index != sigma_combat.ACCRETION
            rock = sigma_launch is not None and not sphere
            pulse = not npc and pulse_bomb.is_pulse(shooter, state)
            if pulse:
                motion, = node.field("m_projectileMotions")
                gravity = _number(state.evaluate(motion["m_599AB8D7"], watch=False), 0.)
                launch = gravity, pulse_bomb.launch_direction(state, direction)
            detonate_on_expiry = launch is not None or sphere
            if sigma_launch is not None:
                launch = sigma_launch
            volley = state.instance.graph.index, node.pos
            concussion = None if npc else pharah.concussion(shooter, state)
            fire_strike = not npc and volley == (reinhardt_offense.FIRE_STRIKE, 3)
            spray_limit = int(field("m_6396149F.m_B4454CD7", 0)) if volley == mei.SPRAY else 0
            spray_width = field("m_6396149F.m_71A4B759", 0.) if volley == moira_biotics.SPRAY else 0.
            if not npc and (volley == (0x0B97, 1) or (
                    shooter.hero.guid == reinhardt_barrier.ZARYA and volley in ((0x0241, 41), (0x025F, 1)))):
                motion, = node.field("m_projectileMotions")
                gravity = _number(state.evaluate(motion["m_599AB8D7"], watch=False), 0.0)
                launch = gravity, direction
            gravity = 0.0
            if launch is not None:
                gravity, direction = launch
            lifetime = field("m_projectileLifetime", 5.0) or 5.0
            reach = field("m_FEC435C6")
            if reach:
                lifetime = min(lifetime, reach / speed)
            blast = hit_def(state, node.field("m_D2D11CE9"))
            entity_def = (node.field("m_projectileEntity") or {}).get("m_entityDef")
            definition = state.evaluate(entity_def, watch=False) if entity_def is not None else None
            pharah_primary = not npc and shooter.hero.guid == pharah.HERO and volley == pharah.PRIMARY
            barrage = isinstance(definition, expr.Asset) and definition.guid == pharah.BARRAGE_ENTITY
            curve = ()
            barrage_source = None
            if (barrage and not npc
                    and (volley[0], state.index) == (barrage_motion.GRAPH, barrage_motion.STATE)):
                direction, curve = barrage_motion.launch(state, activation[0], number, direction)
                if number == 0 and shooter.mover.airborne:
                    self.pharah_airborne.add(shooter.body)
                lifetime = field("m_projectileLifetime", 5.0) or 5.0
                barrage_source = barrage_motion.LaunchSource(
                    shooter.body, state.instance.id, state.index, number, lifetime, reach or None,
                )
            if (isinstance(definition, expr.Asset) and not npc
                    and self.translocators.fire(
                        shooter, state, definition.guid, origin, direction, activation,
                    )):
                return
            shot = None
            if isinstance(definition, expr.Asset):
                shot = self.projectiles.fired(
                    shooter, state, shot_frame, definition.guid, lifetime, origin, direction,
                    activation=activation, snapshot=snapshot,
                )
                if not npc and self.biotics.launch(
                    shooter, state, definition.guid, speed, lifetime, origin, direction, direct, shot,
                ):
                    return
            damaging = (direct is not None and direct.amount > 0.0) or (
                blast is not None and blast.amount > 0.0
            )
            if damaging or shot is not None:
                spawned = projectiles._defs(node.field("m_49CBA3BC")) if not npc else ()
                headings = (direction,)
                if scrap_primary:
                    headings = weapon_spread.pellet_directions(
                        state, direction, shooter.body, number, activation,
                    )
                elif genji_weapon:
                    headings = genji_combat.fan(state, direction)
                burst = (self._scrap_burst(state, direction, shooter.body, number, activation)
                         if scrap_secondary else None)
                sphere_motion = sigma_combat.sphere(state) if sphere else (0., 0., 0, 0., 0.)
                body_radius, wall_radius, bounces, bounce_normal, bounce_tangent = sphere_motion
                if fire_strike:
                    body_radius = reinhardt_offense.strike_radius(state)
                if pulse:
                    body_radius = wall_radius = field("m_6396149F.m_57DAE1B5", 0.)
                projectile = definition.guid if isinstance(definition, expr.Asset) else 0
                tracks_source = (pharah_primary or barrage or concussion is not None or pulse or fire_strike
                                 or sigma_launch is not None
                                 or spray_limit or spray_width or scrap_primary
                                 or scrap_secondary or icicle or genji_weapon
                                 or projectile == dva.MICRO_MISSILE_ENTITY
                                 or graviton.FIELD_ENTITY in spawned or (direct and direct.ana_rifle)
                                 or volley in ((0x0B97, 1), (0x0BDD, 14)))
                for index, heading in enumerate(headings):
                    self.rockets.append(Rocket(
                        shooter, shooter.team, origin, heading, speed, lifetime, direct, blast,
                        shot if index == 0 else None,
                        last_tick=(self.tick if (npc or pulse or spray_limit or spray_width or icicle
                                                 or genji_weapon or sigma_launch or fire_strike
                                                 or pharah_primary or barrage or concussion is not None
                                                 or projectile in EARLY_CONTACT_PROJECTILES)
                                   else None),
                        gravity=gravity,
                        velocity=(tuple(expr.f32(value * speed) for value in direction)
                                  if launch is not None else None),
                        detonate_on_expiry=detonate_on_expiry,
                        spray_limit=spray_limit,
                        spray_width=spray_width,
                        spawn=graviton.FIELD_ENTITY if graviton.FIELD_ENTITY in spawned else None,
                        source_body=_source_entity(shooter) if tracks_source else 0,
                        definition=projectile,
                        damage_scale=self.outgoing_scale(_source_entity(shooter)),
                        burst=burst,
                        sigma_instance=state.instance.id if rock else None,
                        direct_force=sigma_combat.impact(state, "m_6396149F") if sigma_launch else None,
                        blast_force=sigma_combat.impact(state, "m_D2D11CE9") if sigma_launch else None,
                        body_radius=body_radius, wall_radius=wall_radius, bounces=bounces,
                        bounce_normal=bounce_normal, bounce_tangent=bounce_tangent,
                        owner_blast=(1. if pulse else field("m_D2D11CE9.m_8A3E3883", 0.)
                                     if rock or pharah_primary else 0.),
                        pulse=pulse_bomb.ImpactFuse(field("m_55E8761C.m_F969FA7A", 0.)) if pulse else None,
                        owner_force=rock and bool(node.field("m_D2D11CE9.m_49F60FB8")),
                        concussion=concussion,
                        curve=curve, range_left=reach if curve and reach else None,
                        barrage_source=barrage_source, pharah_primary=pharah_primary,
                        contact_extent=field("m_6396149F.m_57DAE1B5", 0.) if pharah_primary else 0.,
                        primary_impulse=pharah.primary_impulse(state) if pharah_primary else None,
                    ))
            return
        if direct is None or direct.amount <= 0.0:
            return
        reach = field("m_FEC435C6", RANGE) or RANGE
        # The input supplies this simulation frame's aim and view time. Notices use the volley clock.
        view_tick = self.tick
        if command is not None:
            view_tick = command.frame - command.view_delay(shooter.quanta) * 1e6 / shooter.quanta
        visor_locked = False
        if (not npc and shooter.hero.guid == 0x02E000000000006E
                and state.instance.graph.index == 0x0254 and node.pos == 25):
            directed = self._visor_direction(shooter, state.instance.component, origin, view_tick)
            if directed is not None:
                direction, visor_locked = directed, True
        force = knockback.evaluate(state) if whole_hog else None
        impulse = (force, (shooter.body, state.instance.id, activation, time)) if force else None
        if tracer_pistol:
            count = max(0, int(field("m_numProjectilesPerShot", 1)))
            directions = tracer_combat.pistol_rays(
                state, direction, shooter.body, number, time, activation, count,
            )
        elif whole_hog or reaper_shotgun:
            directions = weapon_spread.pellet_directions(state, direction, shooter.body, number, activation)
        else:
            directions = (direction,)
        for pellet in directions:
            self._hitscan(shooter, origin, pellet, direct, reach, view_tick, shot_frame,
                          sombra_pistol or tracer_pistol or deadeye_volley or visor_locked or direct.ana_rifle
                          or whole_hog or reaper_shotgun,
                          world_tick=self.tick if npc else None, impulse=impulse)

    @staticmethod
    def _scrap_burst(state, direction, body, number, activation):
        callback = state.node.field("m_747ECB51") or {}
        child = callback.get("m_volley")
        if callback.get("$") != "STU_DB5FDC79" or not isinstance(child, dict):
            return None

        def field(name):
            return _number(state.evaluate(child.get(name), watch=False), 0.0)

        definition = state.evaluate((child.get("m_projectileEntity") or {}).get("m_entityDef"), watch=False)
        speed = field("m_projectileSpeed")
        reach = field("m_FEC435C6")
        lifetime = field("m_projectileLifetime")
        direct = hit_def(state, child.get("m_6396149F"), critical_default=2.0)
        if (not isinstance(definition, expr.Asset) or definition.guid != roadhog.PELLET
                or min(speed, reach, lifetime) <= 0 or direct is None or direct.amount <= 0):
            return None
        headings = weapon_spread.pellet_directions(state, direction, body, number, activation, fields=child)
        return ScrapBurst(headings, speed, min(lifetime, reach / speed), direct)

    def _hitscan(self, shooter, origin, direction, direct, reach, view_tick, shot_frame, guarded_ray,
                 *, world_tick=None, travelled=0.0, returns=0, impulse=None):
        best = None
        guard = None
        manual_npc = isinstance(shooter, bots.Bot) and shooter.spawner.get("manual_spawn") is True
        candidates = self._enemies(shooter)
        if direct.ana_rifle:
            candidates.extend(self._ana_rifle_allies(shooter, shooter.team))
        for target in candidates:
            shown = self.seen(target, view_tick)
            if shown is None:
                continue
            t = shown.ray(origin, direction, reach)
            if t is not None and t <= reach and (best is None or t < best[0]):
                best = (t, target, shown)
            if ((guarded_ray or manual_npc) and target.entity not in self.hacks
                    and (not direct.ana_rifle or self.match.game_map.free_for_all
                         or target.team != shooter.team)):
                state = shot_guards.active_guard(target.owner, self.tick * TICK * 1000)
                if state is not None and not self.is_stunned(target.owner):
                    eye, yaw, pitch = self._guard_pose(target, shown.feet)
                    impact = shot_guards.intercept(
                        state, eye, yaw, pitch, origin, direction, reach,
                    )
                    if impact is not None and (guard is None or impact[0] < guard[0]):
                        guard = (impact[0], target, state, impact[1])
        barrier_hit = reinhardt_barrier.ray_hit(
            self, shooter, origin, direction, reach, include_bubbles=guarded_ray,
            fixed_only=not (guarded_ray or manual_npc),
        )
        stop = best[0] if best is not None else reach
        wall = ray_world(self._collision(), origin, direction, stop)
        pillar = self.walls.cast(origin, direction, stop)
        if pillar is not None:
            if wall is None or pillar[0] < wall:
                wall = pillar[0]
            else:
                pillar = None
        limit = min(stop, wall) if wall is not None else stop
        if (barrier_hit is not None and barrier_hit[0] <= stop + EPSILON
                and (wall is None or barrier_hit[0] < wall - EPSILON)
                and (guard is None or barrier_hit[0] <= guard[0])):
            distance, barrier = barrier_hit
            source = _source_entity(shooter)
            amount = direct.amount * direct.scale(travelled + distance) * self.outgoing_scale(source)
            reinhardt_barrier.damage(barrier, amount, attacker=source)
            return
        if guard is not None and guard[0] < limit - EPSILON:
            distance, defender, state, outgoing = guard
            if state.node.field("m_8B251D53") == 1:
                if shot_guards.guard_hero(defender.owner) == shot_guards.SIGMA:
                    source = _source_entity(shooter)
                    shot_guards.absorb(state, direct.amount * self.outgoing_scale(source), source)
            elif returns < shot_guards.MAX_RETURNS:
                point = _along(_along(origin, direction, distance), outgoing, EPSILON)
                self._hitscan(defender.owner, point, outgoing, direct, reach - distance,
                              self.tick, shot_frame, True, world_tick=self.tick,
                              travelled=travelled + distance, returns=returns + 1, impulse=impulse)
            return
        if pillar is not None:
            scale = self.outgoing_scale(_source_entity(shooter))
            amount = direct.amount * direct.scale(travelled + pillar[0]) * scale
            self.walls.damage(pillar[1], shooter, amount, shot_frame)
            return
        if best is None:
            return
        t, target, shown = best
        if wall is not None and wall < t:
            return
        amount = direct.amount * direct.scale(travelled + t)
        source = _source_entity(shooter)
        if self.match.game_map.free_for_all or target.team != shooter.team:
            amount *= self.outgoing_scale(source)
        if direct.ana_rifle and not target.is_device:
            self._queue_ana_rifle(shooter, source, target, direct, amount, shot_frame, shooter.team)
            return
        if not guarded_ray and not manual_npc and reinhardt_barrier.protect_body(
                self, target, amount, attacker=_source_entity(shooter)):
            return
        hit_y = origin[1] + direction[1] * t
        critical = (hit_y >= shown.feet[1] + shown.eye - HEAD_BELOW
                    and not fortify.active(self, target.owner))
        if target.is_device and (guarded_ray or isinstance(target.owner, orisa_supercharger.Device)):
            critical = False
        if critical:
            amount *= direct.critical
        before = self.vitals_of(target).total if impulse is not None else 0.0
        self._damage(
            shooter, target, amount, critical and direct.critical > 1.0, direct.loadout, shot_frame,
            world_tick=world_tick,
            tags=direct.tags,
            source_scaled=True,
        )
        if impulse is not None and self.vitals_of(target).total < before:
            force, packet = impulse
            self.impulses.hit(target, direction, travelled + t, force, packet)
        self._hit_effect(target, direct.effect)

    def _fly(self, rocket: Rocket) -> None:
        if rocket.spawn == graviton.FIELD_ENTITY and (rocket.shooter.gone or self.match.ended):
            self._landed(rocket)
            return
        """Sweep elapsed world time against the current bodies and static collision."""
        # Player input can arrive immediately before the scheduler accounts for a gap.
        elapsed = 1 if rocket.last_tick is None else self.tick - rocket.last_tick
        if elapsed <= 0:
            return
        rocket.last_tick = self.tick
        if (rocket.curve or rocket.velocity is not None
                or rocket.definition == reinhardt_offense.FIRE_STRIKE_ENTITY):
            # Preserve the fixed-step accelerated path during skipped world ticks.
            for _ in range(elapsed):
                if rocket not in self.rockets:
                    break
                self._fly_step(rocket, min(TICK, rocket.left))
            return
        duration = elapsed * TICK
        self._fly_step(rocket, duration if rocket.burst is not None else min(duration, rocket.left))

    def _fly_step(self, rocket: Rocket, duration: float, returns=0, *, accelerated=False) -> None:
        pulse = rocket.pulse is not None
        if pulse and rocket.pulse.stuck:
            pulse_bomb.advance(self, rocket, duration)
            return
        available = duration
        duration = min(available, rocket.left)
        if duration <= 0.0:
            self._expire_rocket(rocket, available, returns)
            return
        if rocket.definition == reinhardt_offense.FIRE_STRIKE_ENTITY:
            reinhardt_offense.fly_strike(self, rocket, duration, returns)
            return
        if rocket.spray_limit:
            self._fly_mei_spray(rocket, duration)
            return
        if rocket.spray_width:
            self.biotics.fly_spray(rocket, duration)
            return
        previous_velocity = rocket.velocity
        if previous_velocity is not None and not accelerated:
            # The host applies acceleration before each collision sweep.
            rocket.velocity = flashbang.accelerate(rocket.velocity, rocket.gravity, duration)
            rocket.speed = math.sqrt(_dot(rocket.velocity, rocket.velocity))
            if rocket.speed:
                rocket.direction = tuple(value / rocket.speed for value in rocket.velocity)
        # Sweep the remaining flight even when the range or lifetime ends inside this tick.
        heading = rocket.direction
        step = rocket.speed * duration
        if rocket.curve:
            lateral = barrage_motion.displacement(
                rocket.curve, heading, rocket.flown + rocket.curve_phase, duration,
            )
            chord = _along(lateral, heading, step)
            step = math.hypot(*chord)
            heading = tuple(axis / step for axis in chord)
        if rocket.range_left is not None and step >= rocket.range_left:
            duration *= rocket.range_left / step
            step = rocket.range_left

        def travel_time(distance):
            return distance * duration / step if rocket.curve else distance / rocket.speed

        best = None
        grenade = rocket.definition == shot_guards.BIOTIC_GRENADE and rocket.blast is not None
        scrap = rocket.definition in roadhog.SCRAP_PROJECTILES
        ana_rifle = rocket.direct is not None and rocket.direct.ana_rifle
        team = rocket.team if grenade or rocket.source_body else None
        candidates = self._enemies(rocket.shooter, team=team)
        if ana_rifle:
            candidates.extend(self._ana_rifle_allies(rocket.shooter, rocket.team))
        if grenade and not self.match.game_map.free_for_all:
            candidates.extend(
                t for t in self._targets() if t.owner is not rocket.shooter and t.team == rocket.team
                and not t.is_device
                and not self.vitals_of(t).dead and not phase_defenses.active(t.owner)
            )
        extent_hit = None
        for target in candidates:
            t = (ray_capsule(rocket.position, heading, *target.segment(),
                             target.radius + rocket.body_radius)
                 if rocket.body_radius and not target.is_device
                 else target.ray(rocket.position, heading, step))
            if t is not None and t <= step and (best is None or t < best[0]):
                best = (t, target)
            if not rocket.contact_extent or target.is_device:
                continue
            if isinstance(target.owner, bots.Bot) and target.owner.spawner.get("manual_spawn") is not True:
                continue
            radius = target.radius + rocket.contact_extent
            t = ray_capsule(rocket.position, heading, *target.segment(), radius)
            if t is not None and t <= step and (extent_hit is None or t < extent_hit[0]):
                extent_hit = (t, target)
        collision = self._collision()
        normal = None
        if rocket.primary_impulse is not None and collision is not None:
            hit = collision.cast_ray(rocket.position, heading, step, MOVER)
            wall, normal = (hit.distance, hit.normal) if hit is not None else (None, None)
        else:
            wall = ray_world(collision, rocket.position, heading, step)
        if rocket.wall_radius > 0 and collision is not None:
            hit = sigma_combat.wall_sweep(collision, rocket, step)
            if hit is not None and (wall is None or hit[0] <= wall):
                wall, normal = hit
        pillar = self.walls.cast(rocket.position, heading, step,
                                 radius=rocket.wall_radius if pulse else 0.)
        if pillar is not None:
            if wall is None or pillar[0] < wall:
                wall, normal = pillar[0], None
            else:
                pillar = None
        if wall is not None and (best is None or wall < best[0]):
            best = (wall, None)
        barrier_hit = None
        sleep_dart = rocket.definition == shot_guards.SLEEP_DART
        icicle = rocket.definition == shot_guards.ICICLE
        shuriken = rocket.definition == genji_combat.SHURIKEN
        sphere = rocket.definition in sigma_combat.SPHERES
        barrage = rocket.definition == pharah.BARRAGE_ENTITY
        pharah_primary = rocket.pharah_primary
        bubble_ray = (pharah_primary or barrage or rocket.concussion is not None or pulse or sleep_dart
                      or ana_rifle or scrap or icicle or shuriken or sphere
                      or rocket.sigma_instance is not None)
        barrier_eligible = bubble_ray or rocket.definition in shot_guards.NPC_GUARDED_PROJECTILES
        if grenade or (rocket.direct is not None and (barrier_eligible or rocket.direct.amount > 0)):
            barrier_hit = reinhardt_barrier.ray_hit(
                self, rocket.shooter, rocket.position, heading, step,
                include_bubbles=bubble_ray, team=team, fixed_only=not (grenade or barrier_eligible),
            )
            if barrier_hit is not None and best is not None:
                distance = barrier_hit[0]
                # A projected bubble shares its recipient's capsule boundary.
                if distance > best[0] or (distance == best[0] and (not bubble_ray or best[1] is None)):
                    barrier_hit = None
        if extent_hit is not None:
            # D7633C compares the hit distance plus its query radius. Use
            # the unadjusted distance only after picking the winning contact.
            ordering_distance = extent_hit[0] + rocket.contact_extent
            if ((best is None or ordering_distance < best[0])
                    and (barrier_hit is None or ordering_distance < barrier_hit[0])):
                best, barrier_hit = extent_hit, None
        guarded = (pharah_primary or barrage or rocket.definition in shot_guards.GUARDED_PROJECTILES
                   or rocket.definition == pharah.CONCUSSIVE_ENTITY)
        if pulse or sphere or scrap or ana_rifle or shuriken or guarded:
            limit = best[0] if best is not None else step + EPSILON
            if barrier_hit is not None:
                limit = min(limit, barrier_hit[0])
            interception = self._projectile_guard(
                rocket, limit, returns < shot_guards.MAX_RETURNS,
                heading=heading, margin=rocket.contact_extent,
            )
            if interception is not None:
                distance, guard, target, direction = interception
                if rocket.shot is not None:
                    self.projectiles._end(rocket.shot, self.tick, explode=False)
                    rocket.shot = None
                if guard.instance.graph.index == 0x03A3:
                    travelled = min(duration, travel_time(distance))
                    rocket.flown += travelled
                    rocket.left -= travelled
                    if rocket.range_left is not None:
                        rocket.range_left -= distance
                    point = _along(rocket.position, heading, distance)
                    rocket.shooter, rocket.team, rocket.source_body = target.owner, target.team, target.entity
                    if pharah_primary or barrage or pulse or scrap or icicle or shuriken or sphere:
                        rocket.damage_scale = self.outgoing_scale(target.entity)
                    if rocket.burst is not None:
                        headings = roadhog.reorient_pellets(
                            rocket.burst.directions, heading, direction,
                        )
                        rocket.burst = replace(rocket.burst, directions=headings)
                    if rocket.barrage_source is not None:
                        source = rocket.barrage_source
                        direction = source.reflected_direction(self.match, direction)
                        rocket.position = barrage_motion.reflected_origin(target.owner)
                        rocket.flown, rocket.left, rocket.range_left = 0., source.lifetime, source.reach
                        rocket.curve_phase = TICK - (available - travelled)
                    else:
                        rocket.position = _along(point, direction, 0.0001)
                    rocket.direction = direction
                    if previous_velocity is not None:
                        velocity = previous_velocity if accelerated else flashbang.accelerate(
                            previous_velocity, rocket.gravity, travelled,
                        )
                        rocket.speed = math.sqrt(_dot(velocity, velocity))
                        rocket.velocity = tuple(value * rocket.speed for value in direction)
                    if available > travelled:
                        self._fly_step(rocket, available - travelled, returns + 1, accelerated=accelerated)
                    return
                self.rockets.remove(rocket)
                if guard.instance.graph.index == 0x1D02:
                    amount = sum(hit.amount for hit in (rocket.direct, rocket.blast) if hit is not None)
                    shot_guards.absorb(guard, amount * rocket.damage_scale,
                                       rocket.source_body or _source_entity(rocket.shooter))
                return
        if barrier_hit is not None:
            distance, barrier = barrier_hit
            if pulse:
                pulse_bomb.stick(self, rocket, distance, duration, barrier=barrier)
                return
            rocket.flown += travel_time(distance)
            if grenade:
                self._landed(rocket)
                point = _along(rocket.position, heading, distance)
                self._biotic_explosion(rocket, point, wall=True, impact_barrier=barrier)
                return
            if rocket.shot is not None:
                self.projectiles._end(rocket.shot, self.tick, explode=False)
                rocket.shot = None
            self._landed(rocket)
            amount = rocket.direct.amount
            if scrap:
                amount *= rocket.direct.scale(rocket.flown * rocket.speed)
            fixed = isinstance(barrier, reinhardt_barrier.WORLD_BARRIERS)
            if (rocket.blast is not None
                    and (fixed or pharah_primary or barrage
                         or rocket.definition in sigma_combat.PROJECTILES)):
                amount += rocket.blast.amount * rocket.blast.splash(0)
            reinhardt_barrier.damage(
                barrier, amount * rocket.damage_scale,
                attacker=rocket.source_body or _source_entity(rocket.shooter),
            )
            if fixed:
                point = _along(rocket.position, heading, distance)
                if rocket.spawn == graviton.FIELD_ENTITY:
                    self.gravitons.landed(rocket, _along(point, heading, -0.01), TICK)
                self._explode(rocket, point, wall=True, impact_barrier=barrier)
            return
        if best is None:
            rocket.position = _along(rocket.position, heading, step)
            rocket.flown += duration
            rocket.left -= duration
            if rocket.range_left is not None:
                rocket.range_left -= step
            if rocket.left <= 0.0 or rocket.range_left == 0.0:
                self._expire_rocket(rocket, available - duration, returns)
            return
        t, struck = best
        if pulse:
            surface = pillar[1] if struck is None and pillar is not None and pillar[0] == t else None
            pulse_bomb.stick(self, rocket, t, duration, target=struck, pillar=surface)
            return
        if struck is None and normal is not None and rocket.bounces and rocket.speed > EPSILON:
            remaining = sigma_combat.bounce(rocket, normal, t, duration)
            if remaining > EPSILON:
                self._fly_step(rocket, remaining, returns, accelerated=True)
            elif rocket.left <= EPSILON:
                self._expire_rocket(rocket, 0., returns)
            return
        rocket.flown += travel_time(t)
        self._landed(rocket)
        point = _along(rocket.position, heading, t)
        if struck is None and pillar is not None and pillar[0] == t:
            if rocket.spawn == graviton.FIELD_ENTITY:
                self.gravitons.landed(rocket, _along(point, heading, -0.01), TICK)
            self._explode(rocket, point, wall=True, impact_pillar=pillar[1])
            amount = (rocket.direct.amount * rocket.direct.scale(rocket.flown * rocket.speed)
                      if rocket.direct is not None else 0)
            if rocket.blast is not None:
                amount += rocket.blast.amount * rocket.blast.splash(0)
            self.walls.damage(pillar[1], rocket.shooter, amount * rocket.damage_scale, self.tick)
            return
        if rocket.spawn == graviton.FIELD_ENTITY:
            field_point = _along(point, heading, -0.01) if struck is None else point
            self.gravitons.landed(rocket, field_point, TICK)
        shielded = None
        if struck is not None and rocket.direct is not None and rocket.direct.amount > 0.0:
            if ana_rifle and not struck.is_device:
                amount = rocket.direct.amount * rocket.direct.scale(rocket.flown * rocket.speed)
                if self.match.game_map.free_for_all or struck.team != rocket.team:
                    amount *= rocket.damage_scale
                self._queue_ana_rifle(rocket.shooter, rocket.source_body, struck, rocket.direct,
                                      amount, self.tick, rocket.team)
                return
            splash = rocket.blast.amount * rocket.blast.splash(0) if rocket.blast is not None else 0.0
            if not grenade and reinhardt_barrier.protect_body(
                    self, struck, (rocket.direct.amount + splash) * rocket.damage_scale,
                    attacker=rocket.source_body or _source_entity(rocket.shooter)):
                # Impact and splash are one shield contact for this body, even if it breaks.
                shielded = struck.entity
            else:
                critical = ((icicle or shuriken or rocket.definition == roadhog.PELLET)
                            and rocket.direct.critical > 1
                            and point[1] >= struck.feet[1] + struck.eye - HEAD_BELOW
                            and not fortify.active(self, struck.owner)
                            and not struck.is_device)
                amount = rocket.direct.amount
                if scrap:
                    amount *= rocket.direct.scale(rocket.flown * rocket.speed)
                if critical:
                    amount *= rocket.direct.critical
                before = self.vitals_of(struck).total
                self._damage(
                    rocket.shooter, struck, amount * rocket.damage_scale, critical,
                    rocket.direct.loadout, self.tick,
                    world_tick=self.tick,
                    tags=rocket.direct.tags,
                    source_body=rocket.source_body or None,
                    source_scaled=True,
                )
                self._hit_effect(struck, rocket.direct.effect)
                if rocket.sigma_instance is not None:
                    sigma_combat.damage_contact(self, rocket, struck, before - self.vitals_of(struck).total)
                if rocket.direct_force is not None:
                    self.impulses.hit(struck, heading, 0., rocket.direct_force, ballistic=True)
        surface = wall is not None and (
            struck is None or (rocket.concussion is None
                               and rocket.definition not in sigma_combat.PROJECTILES)
        )
        self._explode(rocket, point, wall=surface, shielded=shielded, direct_target=struck,
                      impact_normal=normal if struck is None else None)

    def _fly_mei_spray(self, rocket, duration):
        reach = rocket.speed * duration
        origin, direction = rocket.position, rocket.direction
        wall = ray_world(self._collision(), origin, direction, reach)
        pillar = self.walls.cast(origin, direction, reach)
        if pillar is not None:
            if wall is None or pillar[0] < wall:
                wall = pillar[0]
            else:
                pillar = None
        limit = reach if wall is None else wall
        barrier = reinhardt_barrier.ray_hit(self, rocket.shooter, origin, direction, limit, team=rocket.team)
        if barrier is not None:
            limit = barrier[0]
        contacts = []
        for target in self._enemies(rocket.shooter, team=rocket.team):
            if target.entity in rocket.spray_hits:
                continue
            distance = target.ray(origin, direction, limit)
            if distance is not None and distance <= limit and (barrier is None or distance < limit):
                contacts.append((distance, target.entity, target))
        direct = rocket.direct
        for _, entity, target in sorted(contacts):
            if mei.protected(target.owner):
                self._landed(rocket)
                return
            self._damage(rocket.shooter, target, direct.amount * rocket.damage_scale, False,
                         direct.loadout, self.tick, world_tick=self.tick, tags=direct.tags,
                         source_body=rocket.source_body, source_scaled=True)
            rocket.spray_hits += (entity,)
            if len(rocket.spray_hits) >= rocket.spray_limit:
                self._landed(rocket)
                return
        if barrier is not None:
            reinhardt_barrier.damage(barrier[1], direct.amount * rocket.damage_scale,
                                     attacker=rocket.source_body)
        elif pillar is not None:
            self.walls.damage(pillar[1], rocket.shooter, direct.amount * rocket.damage_scale, self.tick)
        rocket.position = _along(origin, direction, limit)
        rocket.flown += limit / rocket.speed
        rocket.left -= duration
        if barrier is not None or wall is not None or rocket.left <= EPSILON:
            self._landed(rocket)

    def _expire_rocket(self, rocket, remaining, returns):
        self._landed(rocket)
        if rocket.detonate_on_expiry:
            self._explode(rocket, rocket.position)
        burst = rocket.burst
        if burst is None:
            return
        for direction in burst.directions:
            fragment = Rocket(
                rocket.shooter, rocket.team, rocket.position, direction, burst.speed, burst.lifetime,
                burst.direct, None, last_tick=self.tick, source_body=rocket.source_body,
                definition=roadhog.PELLET, damage_scale=rocket.damage_scale,
            )
            self.rockets.append(fragment)
            if remaining > 0:
                self._fly_step(fragment, remaining, returns)

    @staticmethod
    def _guard_pose(target, feet):
        owner = target.owner
        if isinstance(owner, bots.Bot):
            height = eye_height(owner.body, owner.dva_mover.crouched)
            return _along(feet, (0, 1, 0), height), owner.yaw, owner.pitch
        mover = owner.mover
        return _along(feet, (0, 1, 0), eye_height(owner.hero.body, mover.crouched)), mover.yaw, mover.pitch

    def _projectile_guard(self, rocket, limit, reflect, *, margin=0., heading=None):
        closest = None
        for target in self._enemies(rocket.shooter, team=rocket.team):
            owner = target.owner
            hero = shot_guards.guard_hero(owner)
            if (hero not in (shot_guards.DVA, shot_guards.SIGMA, shot_guards.GENJI)
                    or (hero == shot_guards.GENJI and not reflect)
                    or target.entity in self.hacks or self.is_stunned(owner)):
                continue
            state = shot_guards.active_guard(owner, self.tick * TICK * 1000)
            if state is None:
                continue
            eye, yaw, pitch = self._guard_pose(target, target.feet)
            hit = shot_guards.intercept(
                state, eye, yaw, pitch, rocket.position, heading or rocket.direction, limit, margin=margin,
            )
            if hit is not None and hit[0] < limit:
                limit = hit[0]
                closest = limit, state, target, hit[1]
        return closest

    def _explode(self, rocket: Rocket, point: tuple, *, wall: bool = False,
                 shielded=None, impact_barrier=None, impact_pillar=None, direct_target=None,
                 impact_normal=None) -> None:
        if rocket.concussion is not None:
            self._concussive_blast(rocket, point, wall=wall, impact_barrier=impact_barrier)
            return
        blast = rocket.blast
        if blast is None or blast.amount <= 0.0:
            return
        # Start just outside an impact wall, on the projectile's approach side.
        visible_from = _along(point, rocket.direction, -0.0001) if wall else point
        if rocket.definition == shot_guards.BIOTIC_GRENADE:
            self._biotic_explosion(rocket, point, wall=wall)
            self.walls.splash(rocket.shooter, point, visible_from, blast, rocket.damage_scale,
                              self.tick, impact=impact_pillar)
            return
        source = rocket.source_body or _source_entity(rocket.shooter)
        team = rocket.team if rocket.source_body else None
        screens = tuple(b for b in reinhardt_barrier.enemy_barriers(self, rocket.shooter, team=team)
                        if isinstance(b, reinhardt_barrier.WORLD_BARRIERS) and b is not impact_barrier)
        if impact_barrier is not None:
            screens += (impact_barrier,)
        targets = self._enemies(rocket.shooter, team=team)
        if rocket.owner_blast or rocket.owner_force:
            targets.extend(t for t in self._targets() if t.entity == source and t.owner is rocket.shooter)
        for target in targets:
            if target.entity == shielded:
                continue
            a, b = target.segment()
            distance = max(0.0, segment_distance(point, a, b) - target.radius)
            if target is direct_target and rocket.definition in sigma_combat.PROJECTILES:
                distance = 0.
            scalar = blast.splash(distance)
            visible = capsule_visible(
                self._line_collision(), visible_from, a, b, target.radius, barriers=screens,
            ) if scalar > 0 or rocket.primary_impulse is not None else False
            if scalar > 0.0 and visible:
                self_hit = target.entity == source
                scale = rocket.owner_blast if self_hit else 1.
                damage = blast.amount * scalar * rocket.damage_scale * scale
                if reinhardt_barrier.protect_body(self, target, damage, attacker=source):
                    continue
                self._damage(
                    rocket.shooter, target, damage, False, blast.loadout, self.tick,
                    world_tick=self.tick,
                    tags=blast.tags,
                    source_body=rocket.source_body or None,
                    source_scaled=True,
                )
                self._hit_effect(target, blast.effect)
                if (rocket.blast_force is not None and (not self_hit or rocket.owner_force)
                        and (target is not direct_target or rocket.definition in sigma_combat.SPHERES)):
                    radial = _sub(target.feet, point)
                    self.impulses.hit(target, radial, distance, rocket.blast_force, ballistic=True)
            if rocket.primary_impulse is not None:
                self._primary_blast_impulse(
                    rocket, target, point, visible_from, distance, visible, screens, impact_normal,
                )
        self.walls.splash(rocket.shooter, point, visible_from, blast, rocket.damage_scale,
                          self.tick, impact=impact_pillar)

    def _primary_blast_impulse(self, rocket, target, point, origin, distance, visible, screens, normal):
        if reinhardt_barrier.body_bubble(self, target.owner) is not None:
            return
        force = rocket.primary_impulse
        own = target.owner is rocket.shooter and target.entity == rocket.source_body
        if own:
            distance, direction = pharah.source_impulse_geometry(target, point, normal or (0., 0., 0.))
            center = _along(target.feet, (0., 1., 0.), target.height * .5)
            visible = visible or capsule_visible(
                self._line_collision(), origin, center, center, target.radius, barriers=screens,
            )
        else:
            direction = _sub(target.feet, point)
        if not visible:
            return
        horizontal, vertical = force.scales(distance)
        scale = force.owner_scale if own else 1.
        upward = force.vertical * vertical * scale * (direction[1] if own else 1.)
        outward = force.horizontal * horizontal * scale
        if outward or upward:
            self.impulses.push(target, direction, outward, 0., vertical=upward)

    def _concussive_blast(self, rocket, point, *, wall, impact_barrier):
        force = rocket.concussion
        origin = _along(point, rocket.direction, -0.0001) if wall else point
        barriers = tuple(b for b in reinhardt_barrier.enemy_barriers(self, rocket.shooter, team=rocket.team)
                         if isinstance(b, reinhardt_barrier.WORLD_BARRIERS) and b is not impact_barrier)
        if impact_barrier is not None:
            barriers += (impact_barrier,)
        targets = self._enemies(rocket.shooter, team=rocket.team)
        if force.owner_scale:
            targets.extend(t for t in self._targets()
                           if t.owner is rocket.shooter and t.entity == rocket.source_body)
        collision = self._line_collision()
        for target in targets:
            if reinhardt_barrier.body_bubble(self, target.owner) is not None:
                continue
            own = target.entity == rocket.source_body
            a, b = target.segment()
            distance = (math.dist(point, _along(target.feet, (0., 1., 0.), target.height * .5))
                        if own else segment_distance(point, a, b))
            if distance - target.radius > force.radius:
                continue
            if capsule_visible(collision, origin, a, b, target.radius, barriers=barriers):
                scale = force.owner_scale if own else 1.
                self.impulses.push(target, _sub(target.feet, point), force.horizontal * scale,
                                   force.minimum_up * scale, source=own)

    def _biotic_explosion(self, rocket, point, *, wall, impact_barrier=None):
        """Resolve the authored blast radius with reconstructed healing and anti-heal."""
        blast = rocket.blast
        origin = _along(point, rocket.direction, -0.0001) if wall else point
        deadline = self.tick + math.ceil(GRENADE_DURATION / TICK)
        source = rocket.source_body or _source_entity(rocket.shooter)
        barriers = tuple(b for b in reinhardt_barrier.enemy_barriers(self, rocket.shooter, team=rocket.team)
                         if b.mesh)
        if impact_barrier is not None:
            # Retain the struck mesh for this blast even if its pool breaks on impact.
            reinhardt_barrier.damage(impact_barrier, blast.amount * rocket.damage_scale, attacker=source)
        for target in self._targets():
            vitals = self.vitals_of(target)
            if vitals.dead:
                continue
            a, b = target.segment()
            scalar = blast.splash(max(0.0, segment_distance(point, a, b) - target.radius))
            if scalar <= 0 or not capsule_visible(self._line_collision(), origin, a, b, target.radius,
                                                   barriers=barriers):
                continue
            ally = target.owner is rocket.shooter or (
                not self.match.game_map.free_for_all and target.team == rocket.team
            )
            device = target.is_device
            if ally:
                if not device:
                    self.heal_target(target, GRENADE_ALLY_HEAL * scalar, healer=rocket.shooter,
                                     source_body=source)
                    vitals.healing_boost_until = max(vitals.healing_boost_until, deadline)
                continue
            if phase_defenses.active(target.owner) or mei.protected(target.owner):
                continue
            damage = blast.amount * scalar * rocket.damage_scale
            if reinhardt_barrier.protect_body(self, target, damage, attacker=source):
                continue
            if not device:
                vitals.healing_block_until = max(vitals.healing_block_until, deadline)
            self._damage(rocket.shooter, target, damage, False, blast.loadout, self.tick,
                         world_tick=self.tick, tags=blast.tags, source_body=rocket.source_body or None,
                         source_scaled=True)

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
            if target.is_device:
                continue
            ally = not self.match.game_map.free_for_all and target.team == healer.team
            if target.owner is not healer and not ally:
                continue
            a, b = target.segment()
            if segment_distance(centre, a, b) - target.radius > radius:
                continue
            self.heal_target(target, amount, healer=healer)

    def heal_target(self, target, amount, *, healer=None, source_body=None, maximum=None):
        vitals = self.vitals_of(target)
        if vitals.dead or vitals.healing_block_until > self.tick:
            return 0.0
        if vitals.healing_boost_until > self.tick:
            amount *= GRENADE_HEALING_SCALE
        if maximum is not None:
            amount = min(maximum, amount)
        given = vitals.heal(amount)
        if given > 0.0:
            eligible = min(given, vitals.ult_healing_deficit)
            vitals.ult_healing_deficit = min(
                vitals.ult_healing_deficit - eligible, vitals.missing_permanent,
            )
            if healer is not None:
                healer.stats.add(stats.HEALING, given)
                self._charge_ana(healer, given, source_body)
                script = getattr(healer, "body_script", None)
                if (healer is target.owner and getattr(script, "hero", None) == roadhog.HERO
                        and not healer.gone and source_body in (None, healer.body)):
                    ultimate_charge.roadhog_credit(script, eligible, healing=True)
            self._sync_body_health(target.owner, vitals)
        return given

    def _charge_ana(self, actor, effective, source_body=None):
        """Reconstruct one point per effective health point for the original Ana body."""
        if getattr(getattr(actor, "hero", None), "guid", None) != ANA or actor.gone:
            return
        if source_body is not None and actor.body != source_body:
            return
        script = actor.body_script
        if script is not None and script.entity == actor.body:
            script.gain_ult_charge(effective)

    @staticmethod
    def _sync_body_health(owner, vitals: Vitals) -> None:
        script = getattr(owner, "body_script", None)
        if script is not None:
            script.set_health(vitals.total, vitals.maximum)

    def _line_collision(self):
        return self.walls if self.walls.pieces else self._collision()

    def _collision(self):
        from ow174.game.collision import CollisionError

        world = self.match.collision_world
        if world is None:
            raise CollisionError("match collision is not ready")
        return world

    # --- damage and death --------------------------------------------------------------------------

    def outgoing_scale(self, source: int) -> float:
        vitals = self.vitals.get(source)
        nano = 1.5 if vitals is not None and self.tick < vitals.nano_until else 1.0
        return nano * self.superchargers.scale(source)

    def _damage(
        self, shooter, target: Target, amount: float, critical: bool, loadout, frame: int,
        *, world_tick: int | None = None, tags: tuple[int, ...] = (), source_body: int | None = None,
        quick_melee: bool = False, bypass_armour: bool = False, source_scaled: bool = False,
        shield_drain: bool = False, maximum: float = math.inf, continuous_armour: bool = False,
    ) -> None:
        vitals = self.vitals_of(target)
        if vitals.dead or amount <= 0.0 or phase_defenses.active(target.owner) or mei.protected(target.owner):
            return
        source = _source_entity(shooter) if source_body is None else source_body
        if not source_scaled and not shield_drain:
            amount *= self.outgoing_scale(source)
        if isinstance(target.owner, orisa_supercharger.Device):
            self.superchargers.damage(target.owner, amount)
            return
        # Delayed Graviton damage belongs to the cast, not the current hero.
        manual_npc = isinstance(shooter, bots.Bot) and shooter.spawner.get("manual_spawn") is True
        is_sombra = getattr(getattr(shooter, "hero", None), "guid", None) == sombra_spread.HERO
        if (source_body is None and (manual_npc or is_sombra or continuous_armour)
                and reinhardt_barrier.protect_body(self, target, amount, attacker=source)):
            return
        if self.tick < vitals.nano_until and not shield_drain:
            amount *= 0.5
        if not shield_drain:
            amount *= roadhog.incoming_scale(self, target) * fortify.damage_scale(self, target.owner)
        temporary_before = vitals.temporary_shield_total
        dealt = vitals.damage(amount, bypass_armour=bypass_armour, maximum=maximum,
                             continuous_armour=continuous_armour)
        if dealt <= 0.0:
            return
        if getattr(getattr(target.owner, "hero", None), "guid", None) == sigma_combat.HERO:
            vitals.shield_recovery_tick = self.tick + sigma_shields.RECOVERY_DELAY / TICK
        if vitals.sleep_until > self.tick:
            wake = max(vitals.sleep_wake_floor, self.tick + math.ceil(SLEEP_WAKE_RECOVERY / TICK))
            vitals.sleep_until = min(vitals.sleep_until, wake)
        killed = vitals.now[HEALTH_POOL] <= EPSILON
        if isinstance(target.owner, translocators.Translocator):
            self._sync_body_health(target.owner, vitals)
            if killed:
                impact = self.match.tick if world_tick is None else world_tick
                self.translocators.destroy_at(target.entity, impact)
            return  # devices do not award hero damage/death/respawn statistics
        if source_body is None:
            self.ultimate_charge.damage(shooter, target, dealt, tags)
        if not shield_drain and source == getattr(shooter, "body", None) and loadout != mei_blizzard.LOADOUT:
            eligible = dealt
            if getattr(getattr(shooter, "hero", None), "guid", None) == sigma_combat.HERO:
                eligible -= temporary_before - vitals.temporary_shield_total
            hero_charge.damage(shooter, target, eligible, loadout)
        self._charge_ana(shooter, dealt, source)
        hostile = target.owner is not shooter and (
            self.match.game_map.free_for_all or target.team != shooter.team
        )
        permanent = dealt - (temporary_before - vitals.temporary_shield_total)
        if hostile and not shield_drain and permanent > 0:
            victim_script = getattr(target.owner, "body_script", None)
            if getattr(victim_script, "hero", None) == roadhog.HERO:
                vitals.ult_healing_deficit = min(
                    vitals.ult_healing_deficit + permanent, vitals.missing_permanent,
                )
            if (getattr(getattr(shooter, "hero", None), "guid", None) == roadhog.HERO
                    and not shooter.gone and source == shooter.body):
                points = permanent * ultimate_charge.roadhog_victim_multiplier(victim_script)
                ultimate_charge.roadhog_credit(shooter.body_script, points)
        if killed:
            self.retire_ana_rifle_target(target.entity)
            vitals.now[HEALTH_POOL] = 0.0
            vitals.ult_healing_deficit = 0.0
            vitals.sleep_until = vitals.sleep_wake_floor = 0
            vitals.healing_block_until = vitals.healing_boost_until = 0
            vitals.nano_until = 0
            vitals.died_at = self.now
            vitals.death_tick = self.match.tick if world_tick is None else world_tick
            vitals.killer = source
        self.genji.damage(shooter, target, source, self.tick if world_tick is None else world_tick, killed)
        if not shield_drain:
            reaper_blossom.reaping(shooter, self, target, dealt, source)
        credit = dva.pilot_for_mech(self.match, shooter) if isinstance(shooter, bots.Bot) else shooter
        self_projectile = source == target.entity and (
            loadout in sigma_combat.CHARGE_LOADOUTS or loadout in (pharah.PRIMARY_LOADOUT, pulse_bomb.LOADOUT)
        )
        if credit is not None and not self_projectile:
            flags = (CRITICAL if critical else 0) | (KILLING if killed else 0)
            self._tell_hit(credit, target, dealt, flags, frame)
            credit.stats.add(stats.HERO_DAMAGE, dealt)
        victim = target.owner
        script = getattr(victim, "body_script", None)
        component = getattr(script, "component", None)
        if isinstance(component, runtime.Component):
            damage_at = None
            if (not killed and world_tick is not None and getattr(script, "hero", None) == sombra_spread.HERO
                    and loadout in (pharah.PRIMARY_LOADOUT, pharah.BARRAGE_LOADOUT)):
                # The projectile can hit while Sombra's owner clock is still behind the world.
                damage_at = world_tick * component.frame_ms
            params = {DAMAGE_TAGS: tuple(expr.Asset(tag) for tag in tags)}
            impact = self.tick if world_tick is None else world_tick
            if killed or not sombra_camo.queue_damage(
                script, params, source, impact=impact, default_time=damage_at,
            ):
                runtime.send_message(component, RECEIVED_DAMAGE, params, source, at=damage_at)
        if killed and isinstance(victim, bots.Bot):
            # The NPC death script owns its terminal health change and death event ordering.
            self._bot_died(shooter, victim, critical, loadout, vitals.death_tick, source)
            return
        self._sync_body_health(victim, vitals)
        if not killed and loadout == mei.SPRAY_LOADOUT:
            self.mei.add_frost(target, shooter, source)
        if quick_melee and isinstance(victim, bots.Bot) and victim.entity in self.pins:
            from ow174.game import assassin

            assassin.rescue_from_melee(self, shooter, victim)
        if killed:
            self._died(shooter, victim, critical, loadout, source=source)

    def _tell_hit(self, shooter, target: Target, dealt: float, flags: int, frame: int) -> None:
        """25001 to the shooter: the hit marker and the target's health bar (frame: the shot's)."""
        value = {
            "+0x78": {"+0x0": {"+0x0": target.entity}, "+0x4": max(1, round(dealt)), "+0x8": flags},
            "+0x84": frame & 0xFFFFFFFF,
        }
        self._queue_notice(shooter, target.owner, target.entity, HIT, value)

    def _kill_notice(self, killer, victim, source) -> None:
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
    def _death_parameters(source, critical: bool, loadout) -> dict:
        return {
            KILLER: expr.Entity(source),
            CRIT: bool(critical),
            LOADOUT: expr.Asset(loadout) if loadout else None,
            ASSISTS: (),
        }

    def _bot_died(self, killer, bot: bots.Bot, critical: bool, loadout, tick: int, source: int) -> None:
        self.hooks.retire(bot.entity)
        self.impulses.retire(bot.entity)
        self.flux_npcs.pop(bot.entity, None)
        self.halt.retire(bot.entity)
        self.mei.retire(bot.entity)
        self.superchargers.retire_body(bot.entity)
        self.clear_pins(bot.entity)
        self.stuns.pop(bot.entity, None)
        self.gravitons.npc_movers.pop(bot.entity, None)
        bot.died(tick)
        reinhardt_barrier.retire_owner(self, bot)
        self.retire_body_hacks(bot.entity)
        if not bot.retain_corpse:
            self.respawns.append((self.now + BOT_RESPAWN, bot))
        credit = dva.pilot_for_mech(self.match, killer) if isinstance(killer, bots.Bot) else killer
        if credit is not None:
            credit.stats.add(stats.ELIMINATIONS)
            self._kill_notice(credit, bot, source)
        log.info("[game] %s: %s killed %s", self.match.label(), killer.name, bot.name)
        script = bot.body_script
        if script is None:
            return
        try:
            params = self._death_parameters(source, critical, loadout)
            if isinstance(script, BotScript):
                tick = max(tick, script.frame)
            event = script.die(tick, params, source)
            dva.queue_demech_return(self.match, bot)
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

    def _died(self, killer, victim, critical: bool, loadout, *, source=None) -> None:
        """A player died: the notices, his body script's "died" (the kill feed line), and the respawn."""
        self.hooks.retire(victim.body)
        self.tracer_history.pop(victim.body, None)
        self.tracer_recalls.pop(victim.body, None)
        self.pharah_airborne.discard(victim.body)
        winston_combat.cancel_jump(self, victim)
        if victim.body_script is not None:
            winston_combat.sync_primal(self, victim, victim.body_script)
        self.impulses.retire(victim.body)
        sigma_flux.interrupt_flux_lift(self, victim)
        self.flux_players.discard(victim.body)
        if victim.hero.guid == sigma_barrier.HERO:
            reinhardt_barrier.retire_owner(self, victim)
        self.halt.retire(victim.body)
        self.mei.retire(victim.body)
        self.blossom.retire(victim.body)
        self.shadow.retire(victim.body)
        self.genji.retire(victim.body)
        reinhardt_charge.release(self, victim)
        for index in reinhardt_offense.SWINGS:
            self.hammer_swings.pop((victim.body, index), None)
        self.coalescence.retire(victim.body)
        self.superchargers.retire_body(victim.body)
        self.breathers.pop(victim.body, None)
        self.fortifies.pop(victim.body, None)
        self.orisa_flights.pop(victim.body, None)
        self.clear_pins(victim.body)
        self.stuns.pop(victim.body, None)
        self.gravitons.release_player(victim)
        self.retire_body_hacks(victim.body)
        label = self.match.label()
        log.info("[game] %s: %s killed %s", label, killer.name, victim.name)
        victim.stats.add(stats.DEATHS)
        credit = dva.pilot_for_mech(self.match, killer) if isinstance(killer, bots.Bot) else killer
        source = _source_entity(killer) if source is None else source
        if credit is not None and credit is not victim:
            credit.stats.add(stats.ELIMINATIONS)
            self._kill_notice(credit, victim, source)
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
                if runtime.send_message(component, DIED, params, source):
                    # The server owns the death event. Run it before the observer snapshot even
                    # if the victim sends no further commands.
                    component.run_now()
            except Exception:
                log.exception("[game] %s: %s's body script failed on %X", label, victim.name, DIED)
        self.translocators.retire_body(victim.body)
        dva.retire_source(self.match, victim, keep_shells=True)
        self.respawns.append((self.now + respawn_seconds(self.match), victim))

    def _respawn(self, who) -> None:
        if isinstance(who, bots.Bot):
            self._respawn_bot(who)
            return
        player = who
        if player.gone or not player.has_body or not self.is_dead(player):
            return
        if not self.match.switch_hero(player, player.hero, preserve_ultimate=True):
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
