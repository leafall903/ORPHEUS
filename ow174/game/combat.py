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
from types import MappingProxyType

from ow174.game import (
    accretion,
    adaptive_shield,
    ball_mines,
    baptiste_field,
    bots,
    breather,
    brigitte_shield,
    contacts,
    cryo,
    doomfist_passive,
    fade,
    fortify,
    health_packs,
    hook,
    matrix,
    mei_wall,
    observers,
    orisa_barrier,
    pools,
    projectiles,
    reinhardt,
    sigma,
    sigma_barrier,
    sombra_control,
    sound_barrier,
    stats,
    steel_trap,
    symmetra_barrier,
    symmetra_portal,
    symmetra_sentry,
    torbjorn_turret,
    tracer_recall,
    transcendence,
    voicelines,
    winston_barrier,
    world,
    wraith,
)
from ow174.game.bits import BitWriter
from ow174.game.collision import MOVER, World
from ow174.game.commands import JUMP, Command
from ow174.game.mover import mover_data, quantise
from ow174.game.script import combat_mod_filter, expr, graph, nodes, runtime
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
# Original104319 FirstPerson offsets for the lane's audited player bodies.
BODY_EYE = {0x0400000000001424: 1.25, 0x04000000000003BF: 1.6,
            0x0400000000000225: 1.2, 0x04000000000002A3: 1.425, matrix.MECH: 1.74}
EYE_CROUCHED = {0x04000000000003CF: 1.085, 0x0400000000001424: 0.875,
                0x04000000000003BF: 1.12, 0x0400000000000225: 0.84,
                0x04000000000002A3: 0.9975, matrix.MECH: 1.74}
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
SLEEP_MINIMUM = 1.5
SLEEP_WAKE_RECOVERY = 0.5
ANA_HEAL_BOOST = 1.5  # B99/node8
BASTION_IRONCLAD = (0x02E0000000000015, 0x012F, 124)  # Sentry configuration, node 247
BARRIER_ENERGY_GAIN = 0x0240000000000146  # 0256 Pet damage ->0242 owner energy


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
    """Audited FirstPerson offsets; unlisted bodies retain the existing fallback."""
    if crouched:
        return EYE_CROUCHED.get(body, EYE_CROUCHED_DEFAULT)
    return BODY_EYE.get(body, EYE)


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


def capsule_visible(collision: World | None, origin, a, b, radius: float, *, pillars=()) -> bool:
    """Reconstructed melee policy: a static ray to the nearest capsule surface."""
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
    return (hit is None or hit + EPSILON >= reach) and not any(
        (ice := mei_wall.ray(piece, origin, direction, reach)) is not None and ice[0] + EPSILON < reach
        for piece in pillars
    )


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
        self.healing_boost_until = 0.0
        self.healing_block_until = 0.0
        self.sleep_until = 0.0
        self.sleep_wake_floor = 0.0
        self.nano_until = 0.0
        self.bonus_shields = 0.0
        self.bonus_shields_max = 0.0
        self.sound_shield = None
        self.adaptive_shield = None

    @property
    def dead(self) -> bool:
        return self.died_at is not None

    @property
    def total(self) -> float:
        sound = self.sound_shield.amount if self.sound_shield is not None else 0.0
        adaptive = self.adaptive_shield.amount if self.adaptive_shield is not None else 0.0
        return sum(self.now.values()) + self.bonus_shields + sound + adaptive

    def clear_sleep(self) -> None:
        self.sleep_until = self.sleep_wake_floor = 0.0

    def damage(self, amount: float, *, bypass_armour: bool = False, floor: float = 0.0) -> float:
        """Take a hit from shields, then armour, then health (unverified, as OW1 did: what reaches armour
        while it has some is ARMOUR_BLOCK less, at most halved). Returns what the pools lost."""
        left = max(0.0, amount)
        available = max(0.0, self.total - floor)
        taken = 0.0
        for pool in (self.adaptive_shield, self.sound_shield):
            if pool is None:
                continue
            take = min(left, pool.amount, available - taken)
            if take:
                pool.amount -= take
                left -= take
                taken += take
                self.changed.add(SHIELDS_POOL)
        bonus = min(left, self.bonus_shields, available - taken)
        if bonus:
            self.bonus_shields -= bonus
            left -= bonus
            taken += bonus
            self.changed.add(SHIELDS_POOL)
        for kind in (SHIELDS_POOL, ARMOUR_POOL, HEALTH_POOL):
            if left <= 0.0:
                break
            if kind == ARMOUR_POOL and self.now[kind] > 0.0 and not bypass_armour:
                left = max(left - ARMOUR_BLOCK, left / 2)
            take = min(left, self.now[kind], available - taken)
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
            extra = world.pool(4, SHIELDS_POOL, self.bonus_shields_max, self.bonus_shields)
            parts[SHIELDS_POOL * POOLS + 1] = extra[:17] + bytes((0,)) + extra[18:]
        if self.sound_shield is not None:
            extra = world.pool(5, SHIELDS_POOL, sound_barrier.CAPACITY, self.sound_shield.amount)
            parts[SHIELDS_POOL * POOLS + 2] = extra[:17] + bytes((0,)) + extra[18:]
        if self.adaptive_shield is not None:
            pool = self.adaptive_shield
            extra = world.pool(6, SHIELDS_POOL, pool.capacity, pool.amount)
            parts[SHIELDS_POOL * POOLS + 3] = extra[:17] + bytes((0,)) + extra[18:]
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
    ally_heal: float = 0.0  # explicit reconstructed support-weapon policy; not a native filter decoder
    detonate_allies: bool = False
    biotic_duration: float = 0.0
    sleep_duration: float = 0.0
    duration: float = 0.0
    bypass_armour: bool = False
    source_scaled: bool = False
    effect: int | None = None  # authored ModifyHealth hit-effect resource
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
    return HitDef(
        amount=amount,
        loadout=loadout.guid if isinstance(loadout, expr.Asset) else None,
        falloff_start=value("m_EAD7F104"),
        falloff_end=value("m_157E5BC5"),
        falloff_floor=value("m_72A81154"),
        critical=value("m_A872C70E", 1.0),
        rings=tuple(sorted(rings)),
        # 0B94 is Ana's Biotic Rifle; its hip/scoped volleys both carry amount 70.
        # The native combat filters/effects are not yet hosted. Keep this reconstruction
        # local to these two nodes rather than treating every weapon as ally healing.
        # Delivery timing, anti-heal, amplification and visual feedback remain open.
        ally_heal=(amount if state.instance.graph.index == 0x0B94 and state.index in (5, 44)
                   else 100.0 if state.instance.graph.index == 0x0B97 and cfg.get("$") == "STU_4AA6CA44"
                   else 0.0),
        detonate_allies=state.instance.graph.index == 0x0B97,
        # 0267.053 -> C71 -> B99/C72: 100 ally heal, 1.5/0 healing scalar, 4 seconds.
        # Explicit server reconstruction; native combat-effect hosting remains separate.
        biotic_duration=4.0 if state.instance.graph.index == 0x0B97 else 0.0,
        # 026F.053 -> BDE; its 224E override supplies5 seconds. Reconstructed
        # gameplay gate only: native falling/wake animation and recovery are open.
        sleep_duration=5.0 if state.instance.graph.index == 0x0BDD else 0.0,
        duration=0.6 if state.instance.graph.index == 0x0B94 and state.index in (5, 44) else 0.0,
        bypass_armour=state.instance.graph.index == 0x0B94 and state.index in (5, 44),
        effect=effect.guid if isinstance(effect, expr.Asset) else None,
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
    source_entity: int | None = None
    gravity: float = 0.0  # native unconditional E1A5B579 motion scalar
    distance: float = 0.0  # accumulated modeled path length, including reflected segments
    definition: int | None = None  # authored projectile identity, independent of observer lease
    deploy_direction: tuple | None = None  # scoped Orisa carrier, original orientation
    accretion_instance: int | None = None
    turret_instance: runtime.Instance | None = None


@dataclass
class TimedHit:
    """A rifle dose bound to the hit body and original source identity.

    B95 supplies0.6s and70. Linear world-clock dosing on a1/1024-health grid is
    an explicit reconstruction; exact native tick cadence/feedback remains open.
    """

    shooter: object
    source_entity: int
    target: int
    healing: bool
    amount: float
    started: float
    duration: float
    loadout: int | None
    frame: int
    bypass_armour: bool
    applied: float = 0.0


@dataclass
class BodyBarrier:
    """Zarya's actual personal/projected Pet activation, with body protection.

    Lifetime comes from its authored remaining-duration variable215. Capacity200
    follows historical OW1 balance; native Pet geometry/health/presentation and
    native energy transfer are separate open contracts. The reconstructed loss
    bridge feeds the actual0242 graph; no energy clock is stored here.
    """

    target: int
    source: int
    cast: tuple[int, int, int]
    until: float
    health: float = 200.0


class Combat:
    """The match's health, hits, deaths and respawns."""

    health_packs = MappingProxyType({})
    health_pack_digest = None

    def __init__(self, match) -> None:
        self.match = match
        self.vitals: dict[int, Vitals] = {}  # body or bot entity -> its health
        self.history: dict[int, deque] = {}  # entity -> (tick, feet, height, eye) of the last HISTORY ticks
        self.replacement_ticks: dict[int, int] = {}  # replacement body -> authoritative world tick
        self.rockets: list[Rocket] = []
        self.timed_hits: list[TimedHit] = []
        self.respawns: list[tuple[float, object]] = []  # (when, a player or a bot)
        self.next_bot = bots.BOT_ENTITY + len(match.bots)
        self.now = 0.0
        self.tick = 0
        self.melee_seen: dict[int, dict[tuple[int, int], int]] = {}  # body -> counters by instance/state
        self.sigma = sigma.Sigma(self)
        self.sigma_barriers = sigma_barrier.Barriers(self)
        self.baptiste_fields = baptiste_field.Fields(self)
        self.steel_traps = steel_trap.Traps(self)
        self.ball_mines = ball_mines.Mines(self)
        self.torbjorn_turrets = torbjorn_turret.Turrets(self)
        self.symmetra_sentries = symmetra_sentry.Sentries(self)
        self.symmetra_portals = symmetra_portal.Portals(self)
        self.reinhardt = reinhardt.Barriers(self)
        self.brigitte_shields = brigitte_shield.Shields(self)
        self.hooks = hook.Hooks(self)
        self.breathers = breather.Breathers(self)
        self.orisa_barriers = orisa_barrier.Barriers(self)
        self.winston_bubbles = winston_barrier.Barriers(self)
        self.symmetra_barriers = symmetra_barrier.Barriers(self)
        self.pins: dict[int, int] = {}  # controlling NPC body -> controlled player body
        self.cryo_samples = {}
        self.recalls = tracer_recall.Recalls(self)
        self.sound_barriers = sound_barrier.SoundBarriers(self)
        self.sombra_controls = sombra_control.Controls(self)
        packs, digest = health_packs.mount(match, match.tick)
        if digest is not None:
            self.health_packs, self.health_pack_digest = packs, digest
        self.external_impulses = {}
        self.ice_wall = mei_wall.Walls(self)
        self.stuns: dict[int, int] = {}  # body identity -> exclusive world-tick deadline
        self.body_barriers: dict[int, BodyBarrier] = {}
        self.barrier_casts: dict[tuple[int, int, int], int] = {}  # body/graph/instance -> Pet activation
        self.beam_frames: dict[int, int] = {}  # body -> last consumed posed command frame
        self.projectiles = projectiles.Projectiles(self)  # leases, confirmations, the Biotic Field
        self.observers = observers.Observers(match)  # the bodies' statescript for the other clients
        self.voice = voicelines.VoiceLines(match)  # the hero's spawn voice line (voicelines.py)

    # --- hooks (match.py) --------------------------------------------------------------------------

    def is_dead(self, player) -> bool:
        vitals = self.vitals.get(player.body) if player.has_body else None
        return vitals is not None and vitals.dead

    def is_asleep(self, actor, *, now: float | None = None) -> bool:
        vitals = self.vitals.get(_source_entity(actor))
        return (vitals is not None and not vitals.dead
                and (self.now if now is None else now) < vitals.sleep_until)

    def is_stunned(self, actor, *, tick: int | None = None) -> bool:
        entity = _source_entity(actor)
        return (entity in self.pins.values()
                or self.hooks.controlled(entity)
                or self.stuns.get(entity, 0) > (self.tick if tick is None else tick))

    def clear_pins(self, entity: int) -> None:
        for source, victim in list(self.pins.items()):
            if entity in (source, victim):
                del self.pins[source]

    def _interrupt_body(self, actor) -> None:
        """Release actual held input and stop authored Ability channels/buffers."""
        self.hooks.cancel_owner(_source_entity(actor))
        self.breathers.retire(_source_entity(actor))
        self.recalls.interrupt(_source_entity(actor))
        script = getattr(actor, "body_script", None)
        if script is None:
            return
        component = script.component
        self.brigitte_shields.interrupt_bash(actor)
        if isinstance(actor, bots.Bot):
            from ow174.game import sniper_retreat

            sniper_retreat.interrupt(actor)
        component.edges([(value, False) for value in tuple(component.held)])
        for instance in list(component.instances.values()):
            for state in list(instance.states.values()):
                if isinstance(state, nodes.Ability):
                    state.presses.clear()
                    state.stop()
        component.run_now()
        component.shots.clear()

    def _hit_effect(self, target: Target, effect: int | None) -> None:
        """Parent64bf71c resource-backed Flashbang policy; native immunity stays open."""
        if effect != FLASHBANG_EFFECT:
            return
        variables = graph.mode_variables(self.match.game_map.mode_guid) or {}
        duration = 1.7 if expr.truthy(variables.get(FLASHBANG_MODE_FLAG)) else 0.8
        self.stun(target, duration)

    def stun(self, target: Target, duration: float, *, extend_only=False) -> None:
        vitals = self.vitals_of(target)
        if (duration <= 0 or vitals.dead or self._protected(target.owner)
                or fortify.control_immune(target.owner)):
            return
        ticks = math.ceil(duration / TICK)
        if vitals.sleep_until > self.now:
            if self.now + ticks * TICK <= vitals.sleep_until:
                return
            vitals.clear_sleep()
        deadline = self.tick + ticks
        if extend_only and deadline <= self.stuns.get(target.entity, 0):
            return
        self.stuns[target.entity] = max(deadline, self.stuns.get(target.entity, 0))
        self._interrupt_body(target.owner)

    def _sleep(self, target: Target, duration: float) -> None:
        vitals = self.vitals_of(target)
        if vitals.dead or fortify.control_immune(target.owner):
            return
        floor = self.now + min(SLEEP_MINIMUM, duration)
        stun = self.now + (self.stuns.get(target.entity, self.tick) - self.tick) * TICK
        if floor <= max(vitals.sleep_until, stun):
            return
        self.stuns.pop(target.entity, None)
        vitals.sleep_until = self.now + duration
        vitals.sleep_wake_floor = floor
        if not isinstance(target.owner, bots.Bot):
            self._interrupt_body(target.owner)

    def _phased(self, actor):
        return wraith.active(actor) or fade.active(actor) or self.recalls.active(actor)

    def _protected(self, actor):
        return self._phased(actor) or cryo.active(actor) or transcendence.active(actor)

    def cleanse_ana_effects(self, entity):
        vitals = self.vitals.get(entity)
        if vitals is not None:
            vitals.healing_block_until = 0.0
        self.timed_hits[:] = [hit for hit in self.timed_hits if hit.target != entity or hit.healing]

    def constrain_command(self, player, command: Command) -> Command:
        """Keep aim/clock metadata while suppressing voluntary sleep/stun input.

        Use authoritative world time, including for recovered/future command frames.
        Physics, in-flight projectiles and body/cooldown timers continue to advance.
        """
        if self.is_asleep(player) or self.is_stunned(player) or self.recalls.locked(player):
            return replace(command, forward=0, right=0, buttons=0, action=0)
        if self.steel_traps.rooted(player.body):
            return replace(command, forward=0, right=0, buttons=command.buttons & ~JUMP)
        return command

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
        from ow174.game import npc_navigation

        self.sombra_controls.retire(player.body)
        self.cryo_samples.pop(player.body, None)
        self.recalls.retire(player.body)
        self.sound_barriers.retire(player.body)
        self.external_impulses.pop(player.body, None)
        self.ice_wall.retire(player.body)
        self.sigma.retire(player.body)
        self.sigma_barriers.retire(player.body)
        self.baptiste_fields.retire(player.body)
        self.steel_traps.retire(player.body)
        self.steel_traps.retire_victim(player.body)
        self.ball_mines.retire(player.body)
        self.torbjorn_turrets.retire(player.body)
        self.symmetra_sentries.retire(player.body)
        self.symmetra_portals.retire(player.body)
        self.reinhardt.retire(player.body)
        self.brigitte_shields.retire(player.body)
        self.hooks.retire(player.body)
        self.breathers.retire(player.body)
        self.orisa_barriers.retire(player.body)
        self.vitals.pop(player.body, None)
        self.history.pop(player.body, None)
        self.replacement_ticks.pop(player.body, None)
        self.melee_seen.pop(player.body, None)
        # Stop a carry before the replacement can enter the next scripted sweep.
        for bot in self.match.bots:
            if self.pins.get(bot.entity) == player.body:
                npc_navigation.retire_carried_body(self, bot, player.body)
        self.clear_pins(player.body)
        self.stuns.pop(player.body, None)
        self.body_barriers.pop(player.body, None)
        self.beam_frames.pop(player.body, None)
        for key in [key for key in self.barrier_casts if key[0] == player.body]:
            del self.barrier_casts[key]
        self.timed_hits[:] = [hit for hit in self.timed_hits if hit.target != player.body]
        # Leased projectiles and body-owned deployments retire with the old body.
        self.rockets[:] = [
            rocket for rocket in self.rockets if rocket.shooter is not player
            or (rocket.shot is None and rocket.accretion_instance is None and rocket.turret_instance is None)
        ]
        self.respawns[:] = [(when, who) for when, who in self.respawns if who is not player]

    def record_replacement(self, body: int) -> None:
        """Keep the creation boundary independent of the rolling pose history."""
        self.replacement_ticks[body] = self.match.tick
        self.stuns.pop(body, None)

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
        self._sync_body_barriers()
        self.sigma_barriers.sync(player)
        self.brigitte_shields.enforce_lockout(player)
        shots, script.component.shots[:] = list(script.component.shots), []
        if self.is_dead(player) or self.is_asleep(player) or self.is_stunned(player):
            self.ice_wall.cancel_preview(player)
            self.symmetra_portals.command(player, command, blocked=True)
            return
        self.breathers.observe(player)
        adaptive_shield.observe(self, player)
        self.sound_barriers.observe(player)
        cryo.advance_entry(player)
        self._observe_cryo(player)
        if self.ice_wall.command(player, command) or cryo.active(player) or self.recalls.locked(player):
            return
        self.symmetra_portals.command(player, command)
        if command is not None:
            self.hooks.command(player, command)
            for state, number, time, activation in shots:
                try:
                    self._shoot(player, command, state, number, time, activation=activation)
                except Exception:
                    log.exception("[game] %s: %s's shot failed", self.match.label(), player.name)
        try:
            self._melee(player, command, script)
        except Exception:
            log.exception("[game] %s: %s's melee failed", self.match.label(), player.name)
        if command is not None:
            self._zarya_beam(player, command, script)

    def zarya_beam_ray(self, body, state):
        """Declared0241 body-capsule/static-ray reconstruction, not native filters."""
        from ow174.game.script.runtime import RaycastResult

        miss = RaycastResult(False, expr.Entity(0), None, None)
        caster = next((p for p in self.match.players if not p.gone and p.has_body and p.body == body), None)
        if (caster is None or self.match.collision_world is None or self.is_dead(caster)
                or self.is_asleep(caster) or self.is_stunned(caster)):
            return miss
        reach = _number(state.evaluate(state.node.field("m_F5732403.m_6A79BD98"), watch=False), 0.0)
        radius = _number(state.evaluate(state.node.field("m_57DAE1B5"), watch=False), 0.0)
        if not math.isfinite(reach) or reach <= 0 or not math.isfinite(radius) or radius < 0:
            return miss
        origin, direction = self._eye(caster), aim(caster.mover.yaw, caster.mover.pitch)
        wall = ray_world(self._collision(), origin, direction, reach)
        closest, struck = reach if wall is None else wall, None
        for target in self._enemies(caster):
            distance = ray_capsule(origin, direction, *target.segment(), target.radius + radius)
            if distance is not None and distance < closest:
                closest, struck = distance, target
        return RaycastResult(struck is not None or wall is not None,
                             expr.Entity(struck.entity if struck is not None else 0),
                             expr.Vec3(*_along(origin, direction, closest)), None)

    def _zarya_beam(self, caster, command, script):
        """0241/state10 rate per accepted16ms pose; native engine cadence remains open."""
        if caster.hero.guid != 0x02E0000000000068:
            return
        if command.frame <= self.beam_frames.get(caster.body, -1):
            return
        self.beam_frames[caster.body] = command.frame
        instance = next((i for i in script.component.instances.values() if i.graph.index == 0x0241), None)
        state = instance.states.get(10) if instance is not None else None
        if state is None or not state.active:
            return
        selected = instance.vars.get(536)
        entity = expr.to_entity(selected.value()) if selected is not None else 0
        target = next((t for t in self._enemies(caster) if t.entity == entity), None)
        if target is None:
            return
        rate = _number(state.evaluate(state.node.field("m_modifyHealth.m_amount"), watch=False), 0.0)
        if not math.isfinite(rate) or rate <= 0:
            return
        loadout = state.evaluate(state.node.field("m_modifyHealth.m_loadout"), watch=False)
        self._damage(caster, target, rate * script.component.frame_ms / 1000, False,
                     loadout.guid if isinstance(loadout, expr.Asset) else None, command.frame)

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
            pillars = tuple(self.ice_wall.pieces.values())
            if amount > 0:
                origin = self._eye(player)
                shells = self.winston_bubbles
                photon = self.symmetra_barriers
                surfaces = (
                    ((dome, shells.nearest_shell(dome, centre)) for dome in shells.opponents(player)),
                    ((wall, photon.geometry.closest(wall.position, wall.yaw, centre))
                     for wall in photon.opponents(player)),
                    ((piece, mei_wall.closest(piece, centre)) for piece in pillars
                     if self.ice_wall.hostile(piece, player)),
                )
                for contacts_on_barrier in surfaces:
                    for barrier, nearest in contacts_on_barrier:
                        if math.dist(centre, nearest) > radius:
                            continue
                        if not capsule_visible(self._collision(), origin, nearest, nearest, 0.0,
                                               pillars=(piece for piece in pillars if piece is not barrier)):
                            continue
                        if isinstance(barrier, mei_wall.Pillar):
                            self.ice_wall.damage(barrier, amount)
                        else:
                            self._damage_deployable(barrier, amount)
                for manager, device_radius in self._damageable_devices():
                    for device in list(manager.enemies(player)):
                        point = device.centre()
                        if (math.dist(centre, point) <= radius + device_radius
                                and capsule_visible(self._collision(), origin, point, point,
                                                    device_radius, pillars=pillars)):
                            manager.damage(device, amount * self._offense_scalar(player.body))
            view_tick = command.frame - command.view_delay(player.quanta) * 1e6 / player.quanta
            for target in self._enemies(player):
                shown = self.seen(target, view_tick)
                if shown is None:
                    continue
                a, b = shown.segment()
                if (segment_distance(centre, a, b) - shown.radius <= radius and amount > 0.0
                        and capsule_visible(self._collision(), self._eye(player), a, b, shown.radius,
                                            pillars=pillars)):
                    if self._deflects(target, aim(*command.aim())):
                        continue
                    self._damage(player, target, amount, False, loadout, command.frame)

    def update(self, now: float, tick: int, frames: bool = True) -> None:
        """Each tick: keep where the bodies are, fly the rockets, respawn, send health that changed. The
        other players' body frames only on the ticks that send (`frames`)."""
        if self.match.collision_world is None:
            return
        retained = max(self.tick + 1, tick - COMBAT_CATCH_UP + 1)
        previous_tick = self.tick
        first = tick
        if self.tick and tick > self.tick + 1 and (self.timed_hits or self.external_impulses
                or self.baptiste_fields.flights or self.baptiste_fields.devices
                or self.steel_traps.devices
                or self.ball_mines.devices
                or self.torbjorn_turrets.devices
                or self.symmetra_sentries.devices
                or self.hooks.flights
                or self.breathers.active_healing(self.now)
                or self.recalls.phases
                or self.sound_barriers.landings or self.sound_barriers.waves or self.sound_barriers.shielded
                or any(self.now < phase.end + phase.exit_delay for phase in self.cryo_samples.values())
                or any(transcendence.active(player) for player in self.match.players)
                or any(rocket.gravity for rocket in self.rockets) or any(
            bot.firing and not bot.dead and isinstance(bot.body_script, BotScript) for bot in self.match.bots
        )):
            first = retained
        targets = self._targets()
        frozen = [target for target in targets if target.entity in self.cryo_samples]
        adaptive = [target for target in targets
                    if (vitals := self.vitals.get(target.entity)) is not None
                    and vitals.adaptive_shield is not None and vitals.adaptive_shield.amount > 0]
        for target in targets:
            history = self.history.setdefault(target.entity, deque(maxlen=HISTORY))
            history.append((tick, target.feet, target.height, target.eye))
        # Replay the retained combat frames using the most recent poses. Player input
        # accepted just before the scheduler caught up joins only at the endpoint.
        for at in range(first, tick + 1):
            self.now, self.tick = now - (tick - at) * TICK, at
            self.recalls.update()
            self.sound_barriers.update()
            self.sombra_controls.update()
            adaptive_shield.expire(self, adaptive)
            doomfist_passive.advance(self, targets)
            if at == first:
                for target in frozen:
                    self._heal_cryo(target, self.now - TICK)
            if at == tick:
                self._sync_body_barriers()
            self.orisa_barriers.update(at)
            self.sigma_barriers.update(at, TICK)
            self.brigitte_shields.update(self.now)
            self.winston_bubbles.update(at)
            self.symmetra_barriers.update(at)
            self.ice_wall.update(at)
            self.baptiste_fields.update(at, TICK)
            if at > previous_tick:
                self.steel_traps.update(at, TICK)
                self.ball_mines.update(at)
                self.torbjorn_turrets.update(at)
                self.symmetra_sentries.update(at, TICK)
            for rocket in list(self.rockets):
                if at == tick or rocket.last_tick is not None:
                    self._fly(rocket)
            if at > previous_tick:
                self._advance_external_impulses()
            self.hooks.update()
            self._bot_weapons(at)
            self._advance_timed_hits()
            if at > previous_tick:
                self.breathers.update()
                for player in self.match.players:
                    transcendence.heal_tick(self, player, TICK)
            for target in frozen:
                self._heal_cryo(target, self.now)
        for target in self._targets():
            self.recalls.observe(target)
            if (vitals := self.vitals.get(target.entity)) is not None:
                if vitals.sleep_until and vitals.sleep_until <= self.now:
                    vitals.clear_sleep()
        self.sigma.update()
        self.reinhardt.update()
        self.symmetra_portals.update()
        self.projectiles.update(tick, first_tick=retained)
        health_packs.advance(self, tick)
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
        for entity in self.recalls.history.keys() - current:
            self.recalls.retire(entity)
        for entity in [key for key, deadline in self.stuns.items() if deadline <= tick]:
            del self.stuns[entity]
        for table in (self.history, self.vitals, self.replacement_ticks, self.stuns):
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

    def body_clearance(self, player, point, *, radius=None):
        mover = player.mover
        current_radius, segment = mover._capsule()
        radius = current_radius if radius is None else radius
        probe = contacts.CharacterBody(radius, radius + segment, world=self._collision().engine,
                                       category=mover.category)
        x, y, z = point
        probe.teleport((x, y + mover._lift(), z))
        if not probe.fits(probe.radius, probe.top):
            return False
        height = probe.top + radius
        return not any(mei_wall.ray(piece, point, (0, 1, 0), height, radius=radius) is not None
                       for piece in self.ice_wall.pieces.values())

    def teleport_body(self, target, destination):
        mover = target.owner.mover
        history = self.history.setdefault(target.entity, deque(maxlen=HISTORY))
        # The scheduler records the endpoint pose before replaying retained
        # ticks. Replace that sample if the teleport happened during replay.
        endpoint = history.pop()[0] if history and history[-1][0] > self.tick else None
        history.append((self.tick, target.feet, target.height, target.eye))
        mover.position[:] = [quantise(value) for value in destination]
        if mover.body is not None:
            x, y, z = mover.position
            mover.body.teleport((x, y + mover._lift(), z))
        history.append((self.tick, tuple(mover.position), target.height, target.eye))
        if endpoint is not None:
            history.append((endpoint, tuple(mover.position), target.height, target.eye))

    def body_health(self, entity: int):
        """Read component51 totals without allocating mutable combat state."""
        owner = next((player for player in self.match.players
                      if not player.gone and player.has_body and player.body == entity), None)
        if owner is None:
            owner = next((bot for bot in self.match.bots if bot.entity == entity), None)
        if owner is not None:
            vitals = self.vitals.get(entity)
            if vitals is not None:
                return vitals.total, sum(vitals.most.values())
            maximum = (owner.health if isinstance(owner, bots.Bot)
                       else sum(pools.active_pools(owner.hero.guid, owner.hero.body)))
            return float(maximum), float(maximum)
        if (entity == world.GAME_MODE_ENTITY
                or any(not p.gone and p.entity == entity for p in self.match.players)):
            return 0.0, 0.0  # known controller/mode entity without health component
        return self.sigma_barriers.body_health(entity) or self.brigitte_shields.body_health(entity)

    def _sync_body_barriers(self):
        """Consume actual Pet+effect activations, never Ability.RUNNING guesses.

        This is a body-protection reconstruction, not native Pet allocation or
        collision geometry. Keep depleted/expired casts until a new activation
        so continuing Pet activity cannot refill their pool.
        """
        targets = {target.entity: target for target in self._targets()}
        for player in self.match.players:
            if (player.gone or not player.has_body or player.hero.guid != 0x02E0000000000068
                    or self.is_dead(player) or player.body_script is None):
                continue
            for instance in player.body_script.component.instances.values():
                if instance.graph.index not in (0x0244, 0x025C):
                    continue
                personal = instance.graph.index == 0x0244
                state = instance.states.get(2 if personal else 10)
                effect = instance.states.get(5 if personal else 18)
                if state is None or not state.active or effect is None or not effect.active:
                    continue
                key = player.body, instance.graph.index, instance.id
                if self.barrier_casts.get(key) == state.counter:
                    continue
                remaining = instance.vars.get(215)
                seconds = expr.to_float(remaining.value()) if remaining is not None else 0.0
                if not math.isfinite(seconds) or seconds <= 0:
                    continue
                recipients = instance.evaluate(effect.node.field('m_targets'))
                recipients = (recipients,) if isinstance(recipients, expr.Entity) else recipients
                self.barrier_casts[key] = state.counter
                if type(recipients) is not tuple:
                    continue
                for recipient in recipients:
                    if type(recipient) is not expr.Entity:
                        continue
                    target = targets.get(recipient.id)
                    if target is None or self.vitals_of(target).dead:
                        continue
                    if personal:
                        if target.owner is not player:
                            continue
                    elif (target.owner is player or self.match.game_map.free_for_all
                          or target.team != player.team):
                        continue
                    if self._body_barrier_active(target.entity):
                        continue  # reconstructed nonstacking selection policy
                    cast = instance.graph.index, instance.id, state.counter
                    self.body_barriers[target.entity] = BodyBarrier(target.entity, player.body, cast,
                                                                    self.now + seconds)
                    self.hooks.release_target(target.entity)
                    vitals = self.vitals_of(target)
                    vitals.healing_block_until = 0.0
                    self._note_cryo_healing(target, vitals)
                    vitals.clear_sleep()
                    self.stuns.pop(target.entity, None)
                    self.timed_hits[:] = [hit for hit in self.timed_hits
                                          if hit.target != target.entity or hit.healing]

    def _body_barrier_active(self, entity):
        barrier = self.body_barriers.get(entity)
        return barrier is not None and barrier.health > 0 and self.now < barrier.until

    def projected_barrier_targets(self, body: int, state) -> tuple:
        """Resource30m/10-degree allied search, with explicit reconstructed ranking.

        Match the authored6600/11264 target variable predicates. Other native
        filters, target age, camera/aim and nonstacking semantics remain open.
        """
        caster = next((player for player in self.match.players
                       if not player.gone and player.has_body and player.body == body), None)
        if (caster is None or self.match.collision_world is None or self.match.game_map.free_for_all
                or self.is_dead(caster) or self.is_asleep(caster) or self.is_stunned(caster)):
            return ()
        radius = _number(state.evaluate(state.node.field('m_radius'), watch=False), 0.0)
        if not math.isfinite(radius) or radius <= 0:
            return ()
        scale = state.instance.component.entity_var(9595, create=False)
        value = scale.value() if scale is not None else None
        half_angle = 10.0 * (expr.to_float(value) if value is not None else 1.0)
        if not math.isfinite(half_angle) or not 0 <= half_angle <= 180:
            return ()
        origin, direction = self._eye(caster), aim(caster.mover.yaw, caster.mover.pitch)
        candidates = []
        for target in self._targets():
            if (target.owner is caster or target.team != caster.team or self.vitals_of(target).dead
                    or self._body_barrier_active(target.entity)):
                continue
            script = target.owner.body_script
            if script is not None:
                flags = [script.component.entity_var(identifier, create=False)
                         for identifier in (6600, 11264)]
                if any(slot is not None and expr.truthy(slot.value()) for slot in flags):
                    continue
            centre = (target.feet[0], target.feet[1] + target.height / 2, target.feet[2])
            delta = _sub(centre, origin)
            distance = math.sqrt(_dot(delta, delta))
            if not EPSILON < distance <= radius:
                continue
            alignment = _dot(direction, delta) / distance
            if alignment < math.cos(math.radians(half_angle)):
                continue
            wall = ray_world(self._collision(), origin, tuple(axis / distance for axis in delta), distance)
            if wall is not None and wall < distance - EPSILON:
                continue
            candidates.append((-alignment, distance, target.entity))
        return (expr.Entity(min(candidates)[2]),) if candidates else ()

    def _absorb_body_barrier(self, target: Target, amount: float, source_entity: int) -> bool:
        barrier = self.body_barriers.get(target.entity)
        if (barrier is None or barrier.health <= 0 or self.now >= barrier.until or amount <= 0):
            return False
        # The whole contact is consumed, including a hit that breaks the bubble.
        # Bridge effective reconstructed Pet health loss into the authored0146
        # receiver. Its0242 graph owns energy87, cap, delay and decay. Native
        # Pet damage/event classification and damage-flow consumers remain open.
        absorbed = min(barrier.health, amount)
        barrier.health = max(0.0, barrier.health - amount)
        caster = next((player for player in self.match.players
                       if not player.gone and player.has_body and player.body == barrier.source), None)
        if caster is not None and not self.is_dead(caster):
            script = caster.body_script
            if script is not None and not script.retired and script.component.fault is None:
                component = script.component
                if runtime.send_message(component, BARRIER_ENERGY_GAIN,
                                        {0x2A: absorbed, 0x12: expr.Entity(source_entity)}, barrier.source):
                    component.run_now()
        return True

    def _enemies(self, shooter, *, include_phased=False) -> list[Target]:
        free_for_all = self.match.game_map.free_for_all
        source = _source_entity(shooter)
        found = []
        for target in self._targets():
            if target.owner is shooter or target.entity == source:
                continue
            if not free_for_all and target.team == shooter.team:
                continue
            if self.vitals_of(target).dead or (not include_phased and self._phased(target.owner)):
                continue
            found.append(target)
        return found

    def _shot_targets(self, shooter, direct: HitDef | None) -> list[Target]:
        """Enemy bodies plus injured allies for an explicitly supported healing shot.

        Full-health allies are transparent unless solid during Cryo-Freeze.
        FFA has no teammates. The source body and corpses are never eligible.
        """
        found = self._enemies(shooter)
        if self.match.game_map.free_for_all:
            return found
        for target in self._targets():
            if target.owner is shooter or target.entity == _source_entity(shooter):
                continue
            if target.team != shooter.team:
                continue
            vitals = self.vitals_of(target)
            if vitals.dead:
                continue
            if cryo.active(target.owner) or (direct is not None and (
                direct.detonate_allies or (direct.ally_heal > 0.0
                    and sum(vitals.now.values()) < sum(vitals.most.values()) - EPSILON)
            )):
                found.append(target)
        return found

    def _direct_hit(self, shooter, target, direct, amount, critical, frame, *, world_tick=None,
                    source_entity=None):
        healing = not self.match.game_map.free_for_all and target.team == shooter.team
        source = _source_entity(shooter) if source_entity is None else source_entity
        if not healing:
            if self._protected(target.owner):
                return
            shield_amount = amount if direct.source_scaled else amount * self._offense_scalar(source)
            if self._absorb_body_barrier(target, shield_amount, source):
                return
        if direct.duration > 0:
            dose = direct.ally_heal if healing else amount
            if not healing and not direct.source_scaled:
                dose *= self._offense_scalar(source)
            self.timed_hits.append(TimedHit(shooter, source, target.entity, healing, dose, self.now,
                                           direct.duration, direct.loadout, frame, direct.bypass_armour))
        elif healing:
            self._heal(shooter, target, direct.ally_heal, source_entity=source)
        else:
            self._damage(shooter, target, amount, critical, direct.loadout, frame, world_tick=world_tick,
                         source_entity=source, source_scaled=direct.source_scaled,
                         bypass_armour=direct.bypass_armour)
            if direct.sleep_duration > 0:
                self._sleep(target, direct.sleep_duration)
            self._hit_effect(target, direct.effect)

    def _offense_scalar(self, source_entity):
        source = self.vitals.get(source_entity)
        return 1.5 if source is not None and not source.dead and self.now < source.nano_until else 1.0

    def _advance_timed_hits(self):
        targets = {target.entity: target for target in self._targets()}
        pending = []
        for hit in self.timed_hits:
            target = targets.get(hit.target)
            if target is None or self.vitals_of(target).dead:
                continue
            share = max(0.0, min(1.0, (self.now - hit.started) / hit.duration))
            cumulative = hit.amount if share >= 1 else round(hit.amount * share * 1024) / 1024
            amount = max(0.0, cumulative - hit.applied)
            hit.applied = cumulative
            if amount > 0:
                if hit.healing:
                    self._heal(hit.shooter, target, amount, source_entity=hit.source_entity)
                else:
                    self._damage(hit.shooter, target, amount, False, hit.loadout, hit.frame,
                                 world_tick=self.tick, source_entity=hit.source_entity,
                                 source_scaled=True, bypass_armour=hit.bypass_armour)
            if share < 1 and not self.vitals_of(target).dead:
                pending.append(hit)
        self.timed_hits = pending

    def nano_targets(self, body: int, state) -> tuple:
        """Reconstruct the base allied Nano search using live bodies and static LOS.

        Range40 comes from0B9E #1648. The base cone10 is from its target State;
        native ranking, zoom modifiers and engine filters are not claimed here.
        """
        shooter = next((player for player in self.match.players
                        if player.has_body and player.body == body), None)
        if (shooter is None or self.match.collision_world is None or self.match.game_map.free_for_all
                or self.is_dead(shooter) or self.is_asleep(shooter) or self.is_stunned(shooter)):
            return ()
        radius = _number(state.evaluate(state.node.field("m_radius"), watch=False), 0)
        if radius <= 0:
            return ()
        origin = self._eye(shooter)
        direction = aim(shooter.mover.yaw, shooter.mover.pitch)
        candidates = []
        for target in self._targets():
            if target.owner is shooter or target.team != shooter.team or self.vitals_of(target).dead:
                continue
            centre = (target.feet[0], target.feet[1] + target.height / 2, target.feet[2])
            delta = _sub(centre, origin)
            distance = math.sqrt(_dot(delta, delta))
            if not EPSILON < distance <= radius:
                continue
            alignment = _dot(direction, delta) / distance
            if alignment < math.cos(math.radians(10)):
                continue
            ray = tuple(v / distance for v in delta)
            wall = ray_world(self._collision(), origin, ray, distance)
            if wall is not None and wall < distance - EPSILON:
                continue
            ice = self.ice_wall.cast(origin, ray, distance)
            if ice is not None and ice[0] < distance - EPSILON:
                continue
            candidates.append((-alignment, distance, target.entity))
        return (expr.Entity(min(candidates)[2]),) if candidates else ()

    def apply_nano(self, body: int, targets, duration: float) -> bool:
        """Apply0B9E's released0269 effect to its selected living ally.

        B9F defines250 healing and additive+0.5 outgoing/-0.5 incoming damage.
        The authored source graph supplies duration8. This reconstructed target
        deadline survives caster death/retirement; native presentation is separate.
        """
        shooter = next((player for player in self.match.players
                        if player.has_body and player.body == body), None)
        if (shooter is None or self.match.game_map.free_for_all or self.is_dead(shooter)
                or self.is_asleep(shooter) or self.is_stunned(shooter)
                or not math.isfinite(duration) or duration <= 0):
            return False
        targets = (targets,) if isinstance(targets, expr.Entity) else targets
        if type(targets) is not tuple:
            return False
        applied = False
        for target in self._targets():
            if (expr.Entity(target.entity) not in targets or target.owner is shooter
                    or target.team != shooter.team):
                continue
            vitals = self.vitals_of(target)
            if vitals.dead:
                continue
            self._heal(shooter, target, 250.0)
            vitals.nano_until = max(vitals.nano_until, self.now + duration)
            applied = True
        return applied

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
        from ow174.game import assassin, enforcer_controller, npc, npc_navigation, sniper_retreat

        for bot in self.match.bots:
            script = bot.body_script
            acquire = bot.spawner.get("acquire_targets", False)
            if (not (bot.firing or acquire) or bot.dead
                    or not isinstance(script, BotScript) or script.frame > tick):
                continue
            try:
                if acquire:
                    npc.update_acquisition(self, bot)
                if bot.spawner.get("authored_trooper"):
                    from ow174.game import trooper_coordination

                    if not hasattr(self, "trooper_coordination"):
                        self.trooper_coordination = trooper_coordination.Coordination(self.match)
                    self.trooper_coordination.admit(bot, tick)
                assassin.refresh(self, bot)
                snapshot = script.advance_live(tick, bot.position)
                if getattr(bot, "wall_mover", None) is None:
                    npc_navigation.advance_heavy_charge(self, bot, script)
                    if acquire and bot.spawner.get("acquire_specials"):
                        sniper_retreat.advance(self, bot)
                assassin.refresh(self, bot)
                melee = npc.MELEE_GRAPHS.get(bot.hero)
                if melee is not None and not script.component.strict:
                    self._npc_melee(bot, script, melee)
                shots, script.component.shots[:] = list(script.component.shots), []
                for state, number, time, activation in shots:
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
                self._retire_trooper_coordination(bot.entity)
                enforcer_controller.retire(bot)
                bot.body_script = None
                log.exception("[game] %s: %s's weapon script failed", self.match.label(), bot.name)
        if hasattr(self, "trooper_coordination"):
            self.trooper_coordination.advance(tick)

    def _retire_trooper_coordination(self, entity):
        coordination = getattr(self, "trooper_coordination", None)
        if coordination is not None:
            coordination.retire(entity)
            if not coordination.components and not coordination.faults:
                del self.trooper_coordination

    def _heavy_charge_source(self, state, *, require_motion=True):
        entity = state.instance.component.entity
        bot = next((body for body in self.match.bots if body.entity == entity and not body.dead), None)
        if bot is None or self.is_asleep(bot) or self.is_stunned(bot):
            return None
        motion = state.instance.states.get(33)
        if require_motion and (motion is None or not motion.active or motion.ending):
            return None
        return bot

    def heavy_charge_targets(self, state):
        bot = self._heavy_charge_source(state)
        if bot is None:
            return ()
        instance, tracking = state.instance, state.node
        victim = instance.vars.get(536)
        latched = expr.to_entity(victim.value()) if victim is not None else expr.INVALID_ENTITY
        if tracking.pos in (96, 100) and expr.valid_entity(latched):
            return ()
        offset = state.evaluate(tracking.field("m_05BE62DD"), watch=False)
        radius = _number(state.evaluate(tracking.field("m_radius"), watch=False), 0)
        arc = _number(state.evaluate(tracking.field("m_A30B383E"), watch=False), 0)
        if not isinstance(offset, expr.Vec3) or radius <= 0:
            return ()
        direction = aim(round(bot.yaw), 0)
        right = direction[2], 0, -direction[0]
        x, y, z = _along(_along(bot.position, direction, offset.z), right, offset.x)
        centre = x, y + offset.y, z
        if tracking.pos == 100 and self.ice_wall.pieces:
            # Check the pillar surface from the front of the moving capsule.
            reach = radius + mover_data(bot.body).stand_radius
            hit = self.ice_wall.cast(centre, direction, reach)
            if hit is not None:
                distance, piece, _ = hit
                if (piece.charge_contact and self.ice_wall.hostile(piece, bot)
                        and ray_world(self._collision(), centre, direction, distance) is None):
                    return (expr.Entity(piece.entity),)
        excluded = expr.to_entity(state.evaluate(tracking.field("m_544C3E6F"), watch=False))
        cosine = math.cos(math.radians(arc * 0.5))
        condition = tracking.field("m_filter")["m_condition"]
        found = []
        for target in self._enemies(bot):
            dx, _, dz = _sub(target.feet, centre)
            along = dx * direction[0] + dz * direction[2]
            if target.entity in (excluded, latched) or along < math.hypot(dx, dz) * cosine - EPSILON:
                continue
            if tracking.pos == 96 and (isinstance(target.owner, bots.Bot) or self._protected(target.owner)
                                       or fortify.control_immune(target.owner)
                                       or self.is_stunned(target.owner)
                                       or self._body_barrier_active(target.entity)):
                continue
            a, b = target.segment()
            if (segment_distance(centre, a, b) - target.radius <= radius
                    and capsule_visible(self._collision(), centre, a, b, target.radius,
                                        pillars=self.ice_wall.pieces.values())
                    and (condition is None or expr.truthy(expr.evaluate(
                        condition, ChargeQueryContext(instance, target))))):
                found.append(target)
        if tracking.pos == 96 and found:
            found = [min(found, key=lambda target: (math.dist(centre, target.feet), target.entity))]
        return tuple(expr.Entity(target.entity) for target in found)

    def heavy_charge_damage(self, state, *, fresh):
        sweep = state.node.pos == 35
        bot = self._heavy_charge_source(state, require_motion=sweep)
        if bot is None:
            return
        counters = self.melee_seen.setdefault(bot.entity, {})
        prefix = state.instance.id, state.index
        if sweep and fresh:
            for key in tuple(counters):
                if key[:2] == prefix:
                    del counters[key]
        selected = state.evaluate(state.node.field("m_FA3ECC43"), watch=False)
        selection = selected if isinstance(selected, tuple) else (selected,)
        identities = {value.id for value in selection if isinstance(value, expr.Entity)}
        definition = state.node.field("m_modifyHealth")
        amount = _number(state.evaluate(definition["m_amount"], watch=False), 0)
        loadout = state.evaluate(definition.get("m_loadout"), watch=False)
        loadout = loadout.guid if isinstance(loadout, expr.Asset) else None
        if state.node.pos == 102:
            for entity in sorted(identities):
                piece = self.ice_wall.pieces.get(entity)
                if piece is not None and self.ice_wall.hostile(piece, bot):
                    self.ice_wall.damage(piece, amount * self._offense_scalar(bot.entity))
        for target in self._enemies(bot):
            if target.entity not in identities:
                continue
            if not sweep:
                self._damage(bot, target, amount, False, loadout, self.tick, world_tick=self.tick)
                continue
            key = (*prefix, target.entity)
            if counters.get(key) != state.counter:
                counters[key] = state.counter
                # The sweep origin expression uses BotWorld's zero position base.
                self._npc_damage_with_impulse(bot, target, amount, loadout, state, definition,
                                              origin_base=bot.position)

    def npc_melee_targets(self, bot, instance, tracking):
        if self.is_asleep(bot) or self.is_stunned(bot):
            return
        offset = instance.evaluate(tracking.field("m_05BE62DD"))
        radius = _number(instance.evaluate(tracking.field("m_radius")), 0)
        if not isinstance(offset, expr.Vec3) or radius <= 0:
            return
        x, y, z = bot.position
        origin = (x, y + offset.y, z)
        centre = _along(origin, aim(round(bot.yaw), 0), offset.z)
        for target in self._enemies(bot):
            a, b = target.segment()
            if (segment_distance(centre, a, b) - target.radius <= radius
                    and capsule_visible(self._collision(), origin, a, b, target.radius,
                                        pillars=self.ice_wall.pieces.values())):
                yield target

    def _npc_melee(self, bot, script, spec):
        graph_id, index, tracking, _ = spec
        instance = next((i for i in script.component.instances.values() if i.graph.index == graph_id), None)
        state = instance.states.get(index) if instance is not None else None
        if state is None or not state.active or state.ending:
            return
        counters = self.melee_seen.setdefault(bot.entity, {})
        key = (instance.id, index)
        if counters.get(key) == state.counter:
            return
        counters[key] = state.counter
        definition = state.node.field("m_modifyHealth")
        amount = _number(state.evaluate(definition["m_amount"], watch=False), 0)
        loadout = state.evaluate(definition.get("m_loadout"), watch=False)
        loadout = loadout.guid if isinstance(loadout, expr.Asset) else None
        for target in self.npc_melee_targets(bot, instance, instance.graph.node(tracking)):
            self._npc_damage_with_impulse(bot, target, amount, loadout, state, definition)

    def _npc_damage_with_impulse(self, bot, target, amount, loadout, state, definition,
                                 *, origin_base=(0.0, 0.0, 0.0)):
        vitals = self.vitals_of(target)
        before = vitals.total
        self._damage(bot, target, amount, False, loadout, self.tick, world_tick=self.tick)
        if (vitals.total < before and not vitals.dead and not isinstance(target.owner, bots.Bot)
                and not fortify.control_immune(target.owner)):
            self._queue_npc_melee_impulse(target, state, definition, origin_base)

    def _queue_npc_melee_impulse(self, target, state, definition, origin_base):
        rings = definition.get("m_knockbackRings")
        origin = state.evaluate(definition.get("m_C0139FA6"), watch=False)
        if not rings or not isinstance(origin, expr.Vec3):
            return
        ring = rings[0]
        x, y, z = origin_base
        dx, dy, dz = _sub(target.feet, (x + origin.x, y + origin.y, z + origin.z))
        horizontal = math.hypot(dx, dz)

        def number(config, key):
            return _number(state.evaluate(config.get(key), watch=False), 0)

        if horizontal <= EPSILON or math.hypot(dx, dy, dz) > number(ring, "m_radius"):
            return
        push = min(number(definition, "m_E33EFEE1") * number(ring, "m_9102DA3F"),
                   number(definition, "m_10399AE7") * number(ring, "m_10399AE7"))
        lift = number(definition, "m_583C208B") * number(ring, "m_579D7CFF")
        if push > 0 or lift > 0:
            self.external_impulses[target.entity] = (dx * push / horizontal, lift, dz * push / horizontal)

    def _advance_external_impulses(self):
        if not self.external_impulses:
            return
        targets = {target.entity: target for target in self._targets()}
        for entity, velocity in tuple(self.external_impulses.items()):
            target = targets.get(entity)
            if (target is None or self.vitals_of(target).dead or entity in self.pins.values()
                    or self.hooks.controlled(entity)
                    or fortify.control_immune(target.owner) or self._protected(target.owner)):
                del self.external_impulses[entity]
                continue
            mover = target.owner.mover
            constrain = self.ice_wall.constrain_displacement if self.ice_wall.pieces else None
            x, y, z = mover.displace(velocity, TICK, constrain=constrain)
            data = mover.values
            flat = math.hypot(x, z)
            drag = data.decel_ground_base * data.run_forward * TICK
            scale = max(0.0, flat - drag) / flat if flat > EPSILON else 0.0
            velocity = (x * scale, max(0.0, y - 10 * data.gravity_scale * TICK), z * scale)
            if max(map(abs, velocity)) <= EPSILON:
                del self.external_impulses[entity]
            else:
                self.external_impulses[entity] = velocity

    def _eye(self, player) -> tuple:
        x, y, z = player.mover.position
        height = eye_height(player.hero.body, player.mover.crouched)
        return (x, y + height, z)

    def _deflects(self, target, incoming) -> bool:
        """Genji03A3/st10: current body capsule plus authored facing angle.

        This uses the existing reconstructed body collision, not native m_boxes.
        Only the actual running DeflectProjectiles State enables the policy.
        """
        owner = target.owner
        script = getattr(owner, "body_script", None)
        if (script is None or self.vitals_of(target).dead
                or self.is_asleep(owner) or self.is_stunned(owner)):
            return False
        state = next((i.states.get(10) for i in script.component.instances.values()
                      if i.graph.index == 0x03A3), None)
        if state is None or not state.active:
            return False
        angle = _number(state.evaluate(state.node.field("m_025D0C39"), watch=False), 0)
        facing = (aim(round(owner.yaw), 0) if isinstance(owner, bots.Bot)
                  else aim(owner.mover.yaw, owner.mover.pitch))
        return _dot(facing, tuple(-axis for axis in incoming)) > math.cos(math.radians(angle))

    def _deflect_ray(self, reflector, direct, amount, frame, reach):
        """Return an instantaneous shot along the reflector's current aim.

        Remaining range bounds repeated reflections. Overlapping zero-distance
        contacts stop rather than creating an unbounded reflection loop.
        """
        direct = replace(direct, amount=amount, source_scaled=True)
        while reach > EPSILON:
            origin = self._eye(reflector)
            direction = aim(reflector.mover.yaw, reflector.mover.pitch)
            best = None
            for target in self._shot_targets(reflector, direct):
                a, b = target.segment()
                t = ray_capsule(origin, direction, a, b, target.radius)
                if t is not None and t <= reach and (best is None or t < best[0]):
                    best = t, target
            if best is None:
                return
            t, target = best
            wall = ray_world(self._collision(), origin, direction, t)
            if wall is not None and wall < t:
                return
            if ((not self.match.game_map.free_for_all and target.team == reflector.team)
                    or not self._deflects(target, direction)):
                self._direct_hit(reflector, target, direct, amount, False, frame)
                return
            if t <= EPSILON or isinstance(target.owner, bots.Bot):
                return
            reach -= t
            reflector = target.owner

    def _shoot(
        self, shooter, command, state, number: int, time: int,
        *, activation: tuple[int, int], snapshot: Snapshot | None = None,
    ) -> None:
        if self.is_asleep(shooter) or self.is_stunned(shooter):
            return
        node = state.node
        if (state.instance.graph.index == ball_mines.VOLLEY
                and self.ball_mines.launch(shooter, state, number)):
            return
        shot_frame = time // state.instance.component.frame_ms
        if state.instance.graph.index == 0x0B9E and state.index == 1:
            # The authored0.13s release reports one actual Nano volley per cast.
            # Use that event for the reconstructed0269 effect, including a new
            # charged cast while the previous native source-side effect group
            # still exists. No effect is inferred merely from the Q button.
            target = state.instance.find_var(expr.INSTANCE, 8070, create=False)
            duration = state.instance.find_var(expr.INSTANCE, 215, create=False)
            self.apply_nano(_source_entity(shooter), target.value() if target is not None else (),
                            _number(duration.value(), 0) if duration is not None else 0)
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
            if (state.instance.graph.index, node.pos) == (0x1761, 21):
                self.baptiste_fields.launch(shooter, state, origin, direction)
                return
            if (state.instance.graph.index, node.pos) == steel_trap.LAUNCH:
                self.steel_traps.launch(shooter, state, origin, direction)
                return
            if (state.instance.graph.index, node.pos) == symmetra_sentry.LAUNCH:
                self.symmetra_sentries.launch(shooter, state, origin, direction)
                return
        direct = hit_def(state, node.field("m_6396149F"))
        rock_direction = None if npc else accretion.launch(state, direction)
        if rock_direction is not None:
            direction = rock_direction
        from ow174.game import enforcer

        rays = enforcer.directions(shooter, state, number, activation, direction) if npc else NotImplemented
        if rays is NotImplemented:
            rays = (direction,)
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
            if rock_direction is not None:
                for hit, field_name in ((direct, "m_6396149F"), (blast, "m_D2D11CE9")):
                    if hit is not None:
                        hit.knockback = accretion.knockback(state, node.field(field_name)["m_modifyHealth"])
            # Retail2019-03-19: projectile damage boost is captured at firing.
            for hit in (direct, blast):
                if hit is not None:
                    hit.amount *= self._offense_scalar(_source_entity(shooter))
                    hit.source_scaled = True
            damaging = (direct is not None and direct.amount > 0.0) or (
                blast is not None and blast.amount > 0.0
            )
            deployment = (self.orisa_barriers.geometry is not None and not npc
                          and shooter.hero.guid == orisa_barrier.HERO
                          and state.instance.graph.index == orisa_barrier.GRAPH
                          and orisa_barrier.DEFINITION in projectiles._defs(node.field("m_49CBA3BC")))
            turret = not npc and (state.instance.graph.index, node.pos) == torbjorn_turret.LAUNCH
            if turret and (definition != torbjorn_turret.CARRIER
                           or torbjorn_turret.DEVICE not in projectiles._defs(node.field("m_49CBA3BC"))):
                raise ValueError("Torbjorn turret deployment resources changed")
            for ray in rays:
                shot = None
                if definition is not None:
                    shot = self.projectiles.fired(
                        shooter, state, shot_frame, definition, lifetime, origin, ray,
                        activation=activation, snapshot=snapshot,
                    )
                if damaging or shot is not None or deployment or turret:
                    self.rockets.append(
                        Rocket(
                            shooter, shooter.team, origin, ray, speed, lifetime, direct, blast, shot,
                            last_tick=self.tick if npc else None, source_entity=_source_entity(shooter),
                            gravity=projectile_gravity(state), definition=definition,
                            deploy_direction=ray if deployment or turret else None,
                            accretion_instance=state.instance.id if rock_direction is not None else None,
                            turret_instance=state.instance if turret else None,
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
        for ray in rays:
            self._hitscan(shooter, direct, origin, ray, reach, shot_frame, view_tick, npc, definition)

    def _hitscan(
        self, shooter, direct, origin, direction, reach, shot_frame, view_tick, npc, definition=None,
        *, source_entity=None, beam=False,
    ):
        """One physical ray/contact; separate pellets retain separate defense/damage resolution."""
        source = _source_entity(shooter) if source_entity is None else source_entity
        best = None
        for target in self._shot_targets(shooter, direct):
            shown = target if beam else self.seen(target, view_tick)
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
        device = self._device_contact(shooter, origin, direction, limit)
        if device is not None and device[0] < limit:
            limit = device[0]
        else:
            device = None
        barrier = self._barrier_contact(shooter, origin, direction, limit)
        defense = self._projectile_defense(shooter, origin, direction, limit, definition)
        if barrier is not None and barrier[0] < limit and (defense is None or barrier[0] <= defense[0]):
            distance, shield = barrier
            amount = direct.amount * direct.scale(distance) * self._offense_scalar(source)
            if isinstance(shield, mei_wall.Pillar):
                if self.ice_wall.hostile(shield, shooter):
                    self.ice_wall.damage(shield, amount)
            else:
                self._damage_deployable(shield, amount)
            return
        if defense is not None and defense[0] < limit:
            distance, grasp = defense
            if grasp is not None:
                amount = direct.amount * direct.scale(distance) * self._offense_scalar(source)
                self.sigma.absorb(grasp, amount, source)
            return
        if device is not None:
            distance, manager, target_device = device
            amount = direct.amount * direct.scale(distance) * self._offense_scalar(source)
            manager.damage(target_device, amount)
            return
        if best is None or (wall is not None and wall < best[0]):
            return
        t, target, shown = best
        if (not beam and (self.match.game_map.free_for_all or target.team != shooter.team)
                and self._deflects(target, direction) and not isinstance(target.owner, bots.Bot)):
            self._deflect_ray(target.owner, direct,
                              direct.amount * direct.scale(t) * self._offense_scalar(source),
                              shot_frame, reach - t)
            return
        hit_y = origin[1] + direction[1] * t
        critical = (hit_y >= shown.feet[1] + shown.eye - HEAD_BELOW
                    and fortify.active_filter(target.owner) is None)
        amount = direct.amount * direct.scale(t) * (direct.critical if critical else 1.0)
        before = self.vitals_of(target).total if beam else 0
        self._direct_hit(
            shooter, target, direct, amount, critical and direct.critical > 1.0, shot_frame,
            world_tick=self.tick if npc else None, source_entity=source,
        )
        if beam and not self.vitals_of(target).dead and self.vitals_of(target).total < before:
            return target.entity

    def _projectile_defense(self, shooter, origin, direction, reach, definition, *, team=None):
        if not matrix.absorbable(definition):
            return None
        if team is None:
            team = shooter.team
        best = None
        for player in self.match.players:
            if (player is shooter or not player.spawned or not player.has_body or player.gone
                    or player.client is None or player.hero.body not in (matrix.MECH, sigma.BODY)
                    or (not self.match.game_map.free_for_all and player.team == team)
                    or self.is_dead(player) or self.is_asleep(player) or self.is_stunned(player)):
                continue
            anchor = self._eye(player)
            forward = aim(player.mover.yaw, player.mover.pitch)
            if player.hero.body == matrix.MECH:
                distance = matrix.matrix_distance(player, origin, direction, reach, anchor, forward)
                hit = (distance, None) if distance is not None else None
            else:
                hit = sigma.intercept(player, origin, direction, reach, anchor, forward)
            if hit is not None and (best is None or hit[0] < best[0]):
                best = hit
        return best

    def _barrier_contact(self, shooter, origin, direction, reach, *, team=None):
        raised = self.reinhardt.ray(shooter, origin, direction, reach, team=team)
        deployed = self.orisa_barriers.trace(shooter, origin, direction, reach, team=team)
        dome = self.winston_bubbles.trace(shooter, origin, direction, reach, team=team)
        photon = self.symmetra_barriers.trace(shooter, origin, direction, reach, team=team)
        experimental = self.sigma_barriers.trace(shooter, origin, direction, reach, team=team)
        brigitte = self.brigitte_shields.trace(shooter, origin, direction, reach, team=team)
        ice = self.ice_wall.cast(origin, direction, reach)
        return min((hit for hit in (raised, deployed, dome, photon, experimental, brigitte,
                                    ice[:2] if ice else None)
                    if hit is not None),
                   default=None, key=lambda hit: hit[0])

    def _damage_deployable(self, barrier, amount):
        if isinstance(barrier, winston_barrier.Bubble):
            self.winston_bubbles.damage(barrier, amount)
        elif isinstance(barrier, symmetra_barrier.Shield):
            self.symmetra_barriers.damage(barrier, amount)
        elif isinstance(barrier, orisa_barrier.Barrier):
            self.orisa_barriers.damage(barrier, amount)
        elif isinstance(barrier, sigma_barrier.Shield):
            self.sigma_barriers.damage(barrier, amount)
        elif isinstance(barrier, brigitte_shield.Shield):
            self.brigitte_shields.damage(barrier, amount)
        else:
            self.reinhardt.damage(barrier, amount)

    def _damageable_devices(self):
        return ((self.baptiste_fields, baptiste_field.DEVICE_RADIUS),
                (self.steel_traps, steel_trap.DEVICE_RADIUS),
                (self.ball_mines, ball_mines.DEVICE_RADIUS),
                (self.torbjorn_turrets, torbjorn_turret.DEVICE_RADIUS),
                (self.symmetra_sentries, symmetra_sentry.DEVICE_RADIUS),
                (self.symmetra_portals, symmetra_portal.DEVICE_RADIUS))

    def _device_contact(self, shooter, origin, direction, reach, *, team=None):
        best = None
        for manager, _ in self._damageable_devices():
            hit = manager.trace(shooter, origin, direction, reach, team=team)
            if hit is not None and (best is None or hit[0] < best[0]):
                best = hit[0], manager, hit[1]
        return best

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
        # Sweep the remaining flight even when the range or lifetime ends inside this tick.
        if rocket.gravity:
            # D71A70 updates vertical velocity before D70477/D704A8 advance
            # position. Collision uses one chord per authoritative world tick.
            velocity = [expr.f32(axis * rocket.speed) for axis in rocket.direction]
            velocity[1] = expr.f32(velocity[1] - expr.f32(rocket.gravity * expr.f32(duration)))
            rocket.speed = math.sqrt(_dot(velocity, velocity))
            if rocket.speed > EPSILON:
                rocket.direction = tuple(axis / rocket.speed for axis in velocity)
        step = rocket.speed * duration
        best = None
        targets = () if rocket.deploy_direction is not None else self._shot_targets(
            rocket.shooter, rocket.direct,
        )
        for target in targets:
            a, b = target.segment()
            t = ray_capsule(rocket.position, rocket.direction, a, b, target.radius)
            if t is not None and t <= step and (best is None or t < best[0]):
                best = (t, target)
        collision = self._collision()
        terrain = None
        if rocket.turret_instance is not None:
            terrain = collision.cast_ray(rocket.position, rocket.direction, step)
            wall = terrain.distance if terrain is not None else None
        else:
            wall = ray_world(collision, rocket.position, rocket.direction, step)
        if wall is not None and (best is None or wall < best[0]):
            best = (wall, None)
        barrier = self._barrier_contact(
            rocket.shooter, rocket.position, rocket.direction, step, team=rocket.team,
        )
        shield = None
        if barrier is not None and (best is None or barrier[0] < best[0]):
            shield = barrier[1]
            best = barrier[0], None
        device = None
        if rocket.deploy_direction is None:
            contact = self._device_contact(rocket.shooter, rocket.position, rocket.direction,
                                           step, team=rocket.team)
            if contact is not None and (best is None or contact[0] < best[0]):
                distance, manager, target_device = contact
                device = manager, target_device
                best, shield = (distance, None), None
        rock = rocket.accretion_instance is not None
        defense = None if rock else self._projectile_defense(
            rocket.shooter, rocket.position, rocket.direction, step, rocket.definition, team=rocket.team,
        )
        if defense is not None and (best is None or defense[0] < best[0]):
            distance, grasp = defense
            self.rockets.remove(rocket)
            if rocket.shot is not None:
                self.projectiles._end(rocket.shot, self.tick, explode=False)
            if grasp is not None:
                amount = 0.0
                if rocket.direct:
                    amount = rocket.direct.amount * rocket.direct.scale(rocket.distance + distance)
                if rocket.blast:
                    amount += rocket.blast.amount
                source = rocket.source_entity
                if source is None:
                    source = _source_entity(rocket.shooter)
                self.sigma.absorb(grasp, amount, source)
            return
        if best is None:
            rocket.position = _along(rocket.position, rocket.direction, step)
            rocket.flown += duration
            rocket.distance += step
            rocket.left -= duration
            if rocket.left <= 0.0:
                self._landed(rocket)
            return
        t, struck = best
        rocket.flown += t / rocket.speed
        rocket.distance += t
        if (not rock and struck is not None and not isinstance(struck.owner, bots.Bot)
                and (self.match.game_map.free_for_all or struck.team != rocket.team)
                and self._deflects(struck, rocket.direction)):
            point = _along(rocket.position, rocket.direction, t)
            if rocket.shot is not None:
                self.projectiles.flown(rocket.shot, rocket.flown)
                rocket.shot = None
            rocket.shooter, rocket.team = struck.owner, struck.team
            rocket.source_entity = struck.entity
            rocket.direction = aim(struck.owner.mover.yaw, struck.owner.mover.pitch)
            rocket.position = _along(point, rocket.direction, 1e-4)
            rocket.left -= t / rocket.speed
            return
        self._landed(rocket)
        if rocket.deploy_direction is not None:
            if shield is None and struck is None:
                if rocket.turret_instance is not None:
                    self.torbjorn_turrets.land(rocket, terrain)
                else:
                    rocket.position = _along(rocket.position, rocket.direction, t)
                    self.orisa_barriers.landed(rocket)
            return
        deployables = tuple((manager, manager.opponents(rocket.shooter, rocket.team))
                            for manager in (self.orisa_barriers, self.symmetra_barriers, self.sigma_barriers))
        domes = self.winston_bubbles.opponents(rocket.shooter, rocket.team)
        if shield is not None:
            amount = rocket.direct.amount * rocket.direct.scale(rocket.distance) if rocket.direct else 0.0
            if rocket.blast:
                amount += rocket.blast.amount
            if isinstance(shield, mei_wall.Pillar):
                if self.ice_wall.hostile(shield, rocket.shooter, team=rocket.team):
                    self.ice_wall.damage(shield, amount)
            else:
                self._damage_deployable(shield, amount)
        # Keep a wall explosion on the incoming side of the surface. A LOS ray
        # starting exactly on the triangle can otherwise occlude every victim.
        surface_blast = (rocket.blast is not None and (rock or rocket.blast.biotic_duration > 0)
                       and (struck is None or cryo.active(struck.owner)))
        point = _along(rocket.position, rocket.direction, max(0.0, t - 1e-4) if surface_blast else t)
        blocked_body = None
        if struck is not None and rocket.direct is not None and rocket.direct.amount > 0.0:
            amount = rocket.direct.amount * rocket.direct.scale(rocket.distance)
            if rock and self._absorb_body_barrier(
                struck, amount + (rocket.blast.amount if rocket.blast else 0.0), rocket.source_entity,
            ):
                blocked_body = struck.entity
            else:
                before = self.vitals_of(struck).total if rock else 0.0
                self._direct_hit(rocket.shooter, struck, rocket.direct, amount, False, self.tick,
                                 world_tick=self.tick, source_entity=rocket.source_entity)
                if rock:
                    dealt = before - self.vitals_of(struck).total
                    if dealt > 0:
                        accretion.hit(self, rocket, struck, dealt)
                        accretion.impulse(self, struck, rocket.direct.knockback, rocket.direction, 0.0)
        if device is not None and rocket.direct is not None:
            manager, target_device = device
            manager.damage(target_device, rocket.direct.amount * rocket.direct.scale(rocket.distance))
        blast = rocket.blast
        if blast is None or blast.amount <= 0.0:
            return
        if blast.biotic_duration:
            for manager, barriers in deployables:
                for other in barriers:
                    if other is shield:
                        continue
                    nearest = manager.closest(other, point)
                    delta = _sub(nearest, point)
                    length = math.sqrt(_dot(delta, delta))
                    amount = blast.amount * blast.splash(length)
                    if amount <= 0:
                        continue
                    if length > EPSILON:
                        wall = ray_world(self._collision(), point, tuple(v / length for v in delta), length)
                        if wall is not None and wall < length - EPSILON:
                            continue
                    manager.damage(other, amount)
            for dome in domes:
                if dome is shield:
                    continue
                nearest = self.winston_bubbles.nearest_shell(dome, point)
                delta = _sub(nearest, point)
                length = math.sqrt(_dot(delta, delta))
                amount = blast.amount * blast.splash(length)
                if amount <= 0:
                    continue
                wall = (ray_world(self._collision(), point, tuple(v / length for v in delta), length)
                        if length > EPSILON else None)
                if wall is None or wall >= length - EPSILON:
                    self.winston_bubbles.damage(dome, amount)
        pillars = tuple(self.ice_wall.pieces.values())
        if isinstance(shield, mei_wall.Pillar) and shield.entity not in self.ice_wall.pieces:
            pillars += (shield,)
        candidates = self._targets() if blast.biotic_duration else self._enemies(rocket.shooter)
        ice_cover = [(target.entity, *target.segment(), target.radius) for target in candidates
                     if blast.biotic_duration and cryo.active(target.owner)]

        def blast_visible(centre, entity=None):
            delta = _sub(centre, point)
            length = math.sqrt(_dot(delta, delta))
            if length <= EPSILON:
                return True
            ray = tuple(v / length for v in delta)
            wall = ray_world(self._collision(), point, ray, length)
            if wall is not None and wall < length - EPSILON:
                return False
            if any(other != entity and (hit := ray_capsule(point, ray, a, b, radius))
                   is not None and hit < length - EPSILON for other, a, b, radius in ice_cover):
                return False
            if any((hit := mei_wall.ray(piece, point, ray, length)) is not None
                   and hit[0] < length - EPSILON for piece in pillars):
                return False
            extra = shield if isinstance(shield, reinhardt.Barrier) else None
            if self.reinhardt.ray(rocket.shooter, point, ray, length, team=rocket.team,
                                  extra=extra) is not None:
                return False
            extra = shield if isinstance(shield, brigitte_shield.Shield) else None
            if self.brigitte_shields.trace(rocket.shooter, point, ray, length, team=rocket.team,
                                           extra=extra) is not None:
                return False
            if any(manager.trace(rocket.shooter, point, ray, length, barriers=barriers) is not None
                   for manager, barriers in deployables):
                return False
            return self.winston_bubbles.trace(rocket.shooter, point, ray, length, bubbles=domes) is None

        for target in candidates:
            vitals = self.vitals_of(target)
            if vitals.dead or target.entity == blocked_body:
                continue
            a, b = target.segment()
            distance = max(0.0, segment_distance(point, a, b) - target.radius)
            scalar = blast.splash(distance)
            if scalar > 0.0:
                if blast.biotic_duration:
                    centre = (target.feet[0], target.feet[1] + target.height / 2, target.feet[2])
                    if not blast_visible(centre, target.entity):
                        continue
                    ally = target.owner is rocket.shooter or (
                        not self.match.game_map.free_for_all and target.team == rocket.team
                    )
                    if ally:
                        # Apply impact healing before installing this grenade's amplification.
                        self._heal(rocket.shooter, target, blast.ally_heal * scalar,
                                   source_entity=rocket.source_entity)
                        vitals.healing_boost_until = max(vitals.healing_boost_until,
                                                        self.now + blast.biotic_duration)
                        self._note_cryo_healing(target, vitals)
                        continue
                elif rock:
                    centre = _along(a, _sub(b, a), 0.5)
                    if not blast_visible(centre, target.entity):
                        continue
                elif not capsule_visible(
                    self._collision(), _along(point, rocket.direction, -0.0001)
                    if struck is None else point, a, b, target.radius,
                ):
                    continue
                if self._protected(target.owner):
                    continue
                shield_amount = blast.amount * scalar
                if not blast.source_scaled:
                    shield_amount *= self._offense_scalar(rocket.source_entity)
                if self._absorb_body_barrier(target, shield_amount, rocket.source_entity):
                    continue
                if blast.biotic_duration:
                    vitals.healing_block_until = max(vitals.healing_block_until,
                                                    self.now + blast.biotic_duration)
                before = vitals.total if rock else 0.0
                self._damage(
                    rocket.shooter, target, blast.amount * scalar, False, blast.loadout, self.tick,
                    world_tick=self.tick, source_entity=rocket.source_entity,
                    source_scaled=blast.source_scaled,
                )
                self._hit_effect(target, blast.effect)
                if rock and target is not struck and vitals.total < before:
                    accretion.impulse(self, target, blast.knockback, _sub(target.feet, point), distance)

        # Body effects resolve before removing a field destroyed by this same blast.
        for manager, device_radius in self._damageable_devices():
            for target_device in list(manager.enemies(rocket.shooter, team=rocket.team)):
                centre = target_device.centre()
                distance = max(0.0, math.dist(point, centre) - device_radius)
                amount = blast.amount * blast.splash(distance)
                if amount <= 0:
                    continue
                visible = (blast_visible(centre) if blast.biotic_duration or rock else capsule_visible(
                    self._collision(), point, centre, centre, device_radius,
                ))
                if visible:
                    if not blast.source_scaled:
                        amount *= self._offense_scalar(rocket.source_entity)
                    manager.damage(target_device, amount)

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
            self._heal(healer, target, amount)

    def _charge_ana(self, actor, amount, *, source_entity=None):
        """Reconstructed1 effective health unit ->1 charge unit; cost is native.

        No credit is transferred across a source-body replacement. Exact native
        generator/ruleset scalars and passive rate remain a separate open gate.
        """
        if (isinstance(actor, bots.Bot)
                or getattr(getattr(actor, "hero", None), "guid", None) != 0x02E000000000013B):
            return
        if source_entity is not None and actor.body != source_entity:
            return
        script = actor.body_script
        if script is not None:
            script.gain_ult_charge(amount)

    def _observe_cryo(self, player) -> None:
        state = cryo.healing_state(player)
        if state is None:
            return
        key = cryo.activation(state)
        phase = self.cryo_samples.get(player.body)
        duration, amount = cryo.healing_inputs(state)
        if duration <= 0 or amount <= 0:
            return
        if phase is not None and phase.activation == key:
            if (not state.active and state.ended_at - state.start < duration * 1000 - 1
                    and self.now < phase.end):
                offset = state.ended_at % state.instance.component.frame_ms
                phase.end = min(phase.end, self.now + offset / 1000.0)
            return
        if not state.active or not cryo.active(player):
            return
        target = next((target for target in self._targets() if target.owner is player), None)
        if target is None:
            return
        vitals = self.vitals_of(target)
        # The healing state starts within its command frame. Keeping that
        # offset preserves the authored amount when a later input cancels it.
        offset = state.start % state.instance.component.frame_ms
        started = self.now + offset / 1000.0
        phase = cryo.Healing(key, started, started + duration, cryo.exit_delay(state), amount / duration, started)
        self.cryo_samples[player.body] = phase
        phase.note(self.now, vitals.healing_block_until, vitals.healing_boost_until)

    def _heal_cryo(self, target: Target, through: float) -> None:
        phase = self.cryo_samples[target.entity]
        if phase.at >= phase.end or cryo.healing_state(target.owner) is None:
            return
        vitals = self.vitals_of(target)
        if vitals.dead:
            return
        amount = phase.sample(through, TICK, ANA_HEAL_BOOST)
        if amount > 0:
            self._give_healing(target.owner, target, vitals, amount)

    def _note_cryo_healing(self, target: Target, vitals: Vitals) -> None:
        history = self.cryo_samples.get(target.entity)
        if history is not None and cryo.active(target.owner):
            history.note(self.now, vitals.healing_block_until, vitals.healing_boost_until)

    def _heal(self, healer, target: Target, amount: float, *, source_entity=None, ceiling=None) -> float:
        """Apply healing to a living body and synchronize its script health."""
        vitals = self.vitals_of(target)
        if vitals.dead:
            return 0.0
        if self.now < vitals.healing_block_until:
            return 0.0
        if self.now < vitals.healing_boost_until:
            amount *= ANA_HEAL_BOOST
        if ceiling is not None:
            amount = min(amount, max(0.0, ceiling - vitals.total))
        return self._give_healing(healer, target, vitals, amount, source_entity=source_entity)

    def _give_healing(self, healer, target, vitals, amount, *, source_entity=None):
        given = vitals.heal(amount)
        if given > 0.0:
            if healer is not None and not isinstance(healer, bots.Bot):
                healer.stats.add(stats.HEALING, given)
                if target.owner is healer or (
                    not self.match.game_map.free_for_all and target.team == healer.team
                ):
                    self._charge_ana(healer, given, source_entity=source_entity)
            self._sync_body_health(target.owner, vitals)
        return given

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

    def incoming_damage_scale(self, owner) -> float:
        state = fortify.active_filter(owner)
        if state is None:
            state = combat_mod_filter.active_actor_filter(owner, *BASTION_IRONCLAD)
        if state is not None:
            return combat_mod_filter.damage_scale(state)
        return self.breathers.scale(owner, _source_entity(owner))

    def _damage(
        self, shooter, target: Target, amount: float, critical: bool, loadout, frame: int,
        *, world_tick: int | None = None, source_entity: int | None = None,
        source_scaled: bool = False, bypass_armour: bool = False,
    ) -> None:
        vitals = self.vitals_of(target)
        if vitals.dead or amount <= 0.0 or self._protected(target.owner):
            return
        source = _source_entity(shooter) if source_entity is None else source_entity
        if not source_scaled:
            amount *= self._offense_scalar(source)
        if self._absorb_body_barrier(target, amount, source):
            return
        if self.now < vitals.nano_until:
            amount *= 0.5
        amount *= self.incoming_damage_scale(target.owner)
        floor = self.baptiste_fields.health_floor(target, vitals) if self.baptiste_fields.devices else 0.0
        adaptive = vitals.adaptive_shield
        adaptive_before = adaptive.amount if adaptive is not None else 0.0
        doomfist = getattr(getattr(target.owner, "hero", None), "guid", None) == doomfist_passive.HERO
        passive_before = vitals.bonus_shields if doomfist else 0.0
        dealt = vitals.damage(amount, bypass_armour=bypass_armour, floor=floor)
        if dealt <= 0.0:
            return
        killed = vitals.now[HEALTH_POOL] <= EPSILON
        if killed:
            vitals.clear_sleep()
            vitals.now[HEALTH_POOL] = 0.0
            vitals.died_at = self.now
            vitals.death_tick = self.match.tick if world_tick is None else world_tick
            vitals.killer = source
        elif self.now < vitals.sleep_until:
            # Damage can shorten the lock once. The first wake cannot be
            # accelerated below the entry floor or extended by later hits.
            wake = max(vitals.sleep_wake_floor, self.now + SLEEP_WAKE_RECOVERY)
            vitals.sleep_until = min(vitals.sleep_until, wake)
        if not isinstance(shooter, bots.Bot):
            flags = (CRITICAL if critical else 0) | (KILLING if killed else 0)
            self._tell_hit(shooter, target, dealt, flags, frame)
            shooter.stats.add(stats.HERO_DAMAGE, dealt)
            if target.owner is not shooter and (
                self.match.game_map.free_for_all or target.team != shooter.team
            ):
                excluded = adaptive_before - adaptive.amount if adaptive is not None else 0.0
                if doomfist:
                    excluded += passive_before - vitals.bonus_shields
                self._charge_ana(shooter, max(0.0, dealt - excluded), source_entity=source)
        victim = target.owner
        if getattr(getattr(victim, "hero", None), "guid", None) == sombra_control.HERO:
            self.sombra_controls.interrupt(victim, hack_only=True)
        if killed and isinstance(victim, bots.Bot):
            # The NPC death script owns its terminal health change and death event ordering.
            self._bot_died(shooter, victim, critical, loadout, vitals.death_tick, source_entity=source)
            return
        self._sync_body_health(victim, vitals)
        if killed:
            self._died(shooter, victim, critical, loadout, source_entity=source)

    def _tell_hit(self, shooter, target: Target, dealt: float, flags: int, frame: int) -> None:
        """25001 to the shooter: the hit marker and the target's health bar (frame: the shot's)."""
        value = {
            "+0x78": {"+0x0": {"+0x0": target.entity}, "+0x4": max(1, round(dealt)), "+0x8": flags},
            "+0x84": frame & 0xFFFFFFFF,
        }
        self._queue_notice(shooter, target.owner, target.entity, HIT, value)

    def _kill_notice(self, killer, victim, *, source_entity=None) -> None:
        """25005 to the killer: "ELIMINATED: <name>" (1126.07C; +0x89 assist would be 01F0.07C, +0x88 a
        score shown when above 0). Its handler (0x7FF7895D2B50) reads only +0x84, +0x88 and +0x89."""
        entity = _source_entity(victim)
        notice = {
            "+0x78": 0,
            "+0x80": {"+0x0": _source_entity(killer) if source_entity is None else source_entity},
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
    def _death_parameters(killer, critical: bool, loadout, *, source_entity=None) -> dict:
        return {
            KILLER: expr.Entity(_source_entity(killer) if source_entity is None else source_entity),
            CRIT: bool(critical),
            LOADOUT: expr.Asset(loadout) if loadout else None,
            ASSISTS: (),
        }

    def _bot_died(self, killer, bot: bots.Bot, critical: bool, loadout, tick: int,
                  *, source_entity=None) -> None:
        self.hooks.retire(bot.entity)
        self.steel_traps.retire_victim(bot.entity)
        self.clear_pins(bot.entity)
        self.stuns.pop(bot.entity, None)
        self.body_barriers.pop(bot.entity, None)
        from ow174.game import enforcer_controller

        self.sombra_controls.retire(bot.entity)
        self._retire_trooper_coordination(bot.entity)
        enforcer_controller.retire(bot)
        bot.died(tick)
        if not bot.retain_corpse:
            self.respawns.append((self.now + BOT_RESPAWN, bot))
        if not isinstance(killer, bots.Bot):
            killer.stats.add(stats.ELIMINATIONS)
            self._kill_notice(killer, bot, source_entity=source_entity)
        log.info("[game] %s: %s killed %s", self.match.label(), killer.name, bot.name)
        script = bot.body_script
        if script is None:
            return
        try:
            params = self._death_parameters(killer, critical, loadout, source_entity=source_entity)
            if isinstance(script, BotScript):
                from ow174.game import sniper_retreat

                sniper_retreat.interrupt(bot)
                tick = max(tick, script.frame)
            sender = _source_entity(killer) if source_entity is None else source_entity
            event = script.die(tick, params, sender)
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

    def _died(self, killer, victim, critical: bool, loadout, *, source_entity=None) -> None:
        """A player died: the notices, his body script's "died" (the kill feed line), and the respawn."""
        self.sigma.retire(victim.body)
        self.sigma_barriers.retire(victim.body)
        self.reinhardt.retire(victim.body)
        self.brigitte_shields.retire(victim.body)
        self.hooks.retire(victim.body)
        self.breathers.retire(victim.body)
        self.steel_traps.retire_victim(victim.body)
        self.symmetra_portals.end_transit(victim.body)
        self.clear_pins(victim.body)
        self.stuns.pop(victim.body, None)
        self.body_barriers.pop(victim.body, None)
        label = self.match.label()
        log.info("[game] %s: %s killed %s", label, killer.name, victim.name)
        victim.stats.add(stats.DEATHS)
        bot_killer = isinstance(killer, bots.Bot)
        source = _source_entity(killer) if source_entity is None else source_entity
        if killer is not victim and not bot_killer:
            killer.stats.add(stats.ELIMINATIONS)
            self._kill_notice(killer, victim, source_entity=source)
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
                params = self._death_parameters(killer, critical, loadout, source_entity=source)
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
        charge = None
        if player.hero.guid == 0x02E000000000013B and player.body_script is not None:
            charge = player.body_script.component.entity_var(342).value()
        if not self.match.switch_hero(player, player.hero):
            self.respawns.append((self.now + RESPAWN_RETRY, player))
            return
        if charge is not None and player.body_script is not None:
            player.body_script.set_ult_charge(expr.to_float(charge))
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
