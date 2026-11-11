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

from ow174.game import (
    bots,
    deflection,
    experimental_barrier,
    mercy_boost,
    grasp,
    knockback,
    lucio,
    observers,
    pools,
    projectiles,
    stats,
    voicelines,
    wall_ride,
    world,
    wraith,
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
EYE_STANDING = {
    0x04000000000003FC: 1.175,  # Lucio's m_E29993A5
    0x04000000000002A3: 1.425,  # McCree's m_E29993A5
}
EYE_CROUCHED = {
    0x04000000000002A3: 0.9975,  # McCree's m_816B830F
    0x04000000000003CF: 1.085,  # Soldier's m_816B830F
    0x04000000000003FC: 0.8225,  # Lucio's m_816B830F
}
EYE_CROUCHED_DEFAULT = 1.15
LUCIO_PRIMARY = 0x04000000000005FF
PROJECTILE_HEAD_BAND = 0.55  # reconstructed top section of the current target capsule
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
ANA_GRENADE_EFFECT = 0x04A0000000000267  # .053/0267 -> 0C71 -> C72 enemy modifier
ANA_HEAL_DENIAL_TICKS = math.ceil(4.0 / TICK)  # C72 wait, with incoming healing multiplier 0
ANA_HEAL_BOOST = 1.5  # B99 incoming healing modifier; same authored four-second wait
ANA_GRENADE_HEAL = 50.0  # B99 default, before ruleset scaling
FLASHBANG_MODE_FLAG = 13408  # 0228 duration: flag set1.7s, otherwise0.8s


def eye_height(body: int, crouched: bool = False) -> float:
    """Known first-person component heights; retain component defaults elsewhere."""
    return (EYE_CROUCHED.get(body, EYE_CROUCHED_DEFAULT) if crouched
            else EYE_STANDING.get(body, EYE))


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
class TemporaryShield:
    pool_id: int
    slot: int
    maximum: float
    current: float


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
        self.next_pool_id = pool_id + 1
        self.temporary_shields: dict[object, TemporaryShield] = {}
        self.temporary_used = False
        self.changed: set[int] = set()  # pools that changed since the last update went out
        self.died_at: float | None = None
        self.death_tick: int | None = None
        self.killer: int | None = None

    @property
    def dead(self) -> bool:
        return self.died_at is not None

    @property
    def total(self) -> float:
        return sum(self.now.values()) + sum(pool.current for pool in self.temporary_shields.values())

    @property
    def maximum(self) -> float:
        return sum(self.most.values()) + sum(pool.maximum for pool in self.temporary_shields.values())

    def grant_temporary_shields(self, key, amount: float) -> TemporaryShield | None:
        """A separate non-healable shield pool; same-key grants replace, not stack."""
        if type(amount) not in (int, float) or not math.isfinite(amount) or amount <= 0:
            raise ValueError("temporary shields need a finite positive amount")
        if self.dead:
            return None
        found = self.temporary_shields.get(key)
        if found is None:
            occupied = {pool.slot for pool in self.temporary_shields.values()}
            slot = next((slot for slot in range(1, POOLS) if slot not in occupied), None)
            if slot is None or self.next_pool_id > 0xFFFFFFFF:
                raise ValueError("temporary shield pool capacity exhausted")
            found = self.temporary_shields[key] = TemporaryShield(self.next_pool_id, slot, amount, amount)
            self.next_pool_id += 1
        else:
            found.maximum = found.current = float(amount)
        self.temporary_used = True
        self.changed.add(SHIELDS_POOL)
        return found

    def remove_temporary_shields(self, key) -> None:
        if self.temporary_shields.pop(key, None) is not None:
            self.changed.add(SHIELDS_POOL)

    def drain_temporary_shields(self, key, amount: float) -> float:
        """Absorption/decay never spills into base pools and is never healing."""
        pool = self.temporary_shields.get(key)
        if pool is None or amount <= 0:
            return 0.0
        taken = min(pool.current, amount)
        pool.current -= taken
        if taken > 0:
            self.changed.add(SHIELDS_POOL)
        if pool.current <= EPSILON:
            self.remove_temporary_shields(key)
        return taken

    def damage(self, amount: float, *, preview: bool = False) -> float:
        """Take a hit from temporary shields, base shields, armour, then health (unverified: armour
        while it has some is ARMOUR_BLOCK less, at most halved). Returns what the pools lost."""
        left = max(0.0, amount)
        taken = 0.0
        for key in reversed(tuple(self.temporary_shields)):
            absorb = (min(left, self.temporary_shields[key].current) if preview
                      else self.drain_temporary_shields(key, left))
            taken += absorb
            left -= absorb
        for kind in (SHIELDS_POOL, ARMOUR_POOL, HEALTH_POOL):
            if left <= 0.0:
                break
            if kind == ARMOUR_POOL and self.now[kind] > 0.0:
                left = max(left - ARMOUR_BLOCK, left / 2)
            take = min(left, self.now[kind])
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
        if self.temporary_used:
            # A complete shield-array replacement clears expired slots on every
            # viewer, including a receiver retransmitting an older pool snapshot.
            if SHIELDS_POOL not in self.ids:
                parts[SHIELDS_POOL * POOLS] = world.pool(0, SHIELDS_POOL, 0, 0)
            active = {pool.slot: pool for pool in self.temporary_shields.values()}
            for slot in range(1, POOLS):
                pool = active.get(slot)
                parts[SHIELDS_POOL * POOLS + slot] = (
                    world.pool(pool.pool_id, SHIELDS_POOL, pool.maximum, pool.current, overhealth=True)
                    if pool else world.pool(0, SHIELDS_POOL, 0, 0)
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
    boost_credit: tuple[int, float] | None = None

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


INCOMING_DAMAGE_SCALE = 8243
MITIGATION_CLOCKS = {
    0x02E0000000000040: (0x0240, 1),  # Take a Breather wait
    0x02E000000000013E: (0x0CF4, 1),  # Fortify chase
}


def _defense_clock_current(timer, frame_ms: int, tick: int) -> bool:
    now = tick * frame_ms
    if now + frame_ms - 1 < timer.start:
        return False
    if isinstance(timer, nodes.Wait):
        end = timer.start + timer.ms
    elif isinstance(timer, nodes.ChaseVar):
        rate = _number(timer.evaluate(timer.node.field("m_9B0385B4"), watch=False), 1.0)
        end = timer.last + (abs(timer.cur - timer.destination()) * 1000 / rate if rate > 0 else math.inf)
    else:
        return True
    # World impacts on the deadline can precede the owner's end command.
    return now <= end


def incoming_damage_scale(owner, tick: int) -> float:
    """Read the live damage stack, with world expiry for the recovered timed defenses."""
    script = getattr(owner, "body_script", None)
    component = getattr(script, "component", None)
    if not isinstance(component, runtime.Component) or getattr(script, "retired", False):
        return 1.0
    slot = component.entity_var(INCOMING_DAMAGE_SCALE, create=False)
    scale = _number(slot.value(), 1.0) if slot is not None else 1.0
    if not math.isfinite(scale):
        return 1.0
    clock = MITIGATION_CLOCKS.get(getattr(script, "hero", None))
    if scale < 1.0 and clock is not None:
        graph_index, index = clock
        for instance in component.instances.values():
            if instance.graph.index != graph_index or instance.stopped:
                continue
            timer = instance.states.get(index)
            if timer is None or not timer.active:
                continue
            if not _defense_clock_current(timer, script.frame_ms, tick):
                return 1.0
            break
    return max(0.0, scale)


def _fortify_active(script, tick: int) -> bool:
    """Check the live 0CF4 combat filter against its retained world window."""
    if getattr(script, "hero", None) != 0x02E000000000013E or script.retired:
        return False
    for instance in script.component.instances.values():
        if instance.graph.index != 0x0CF4 or instance.stopped:
            continue
        timer, policy = instance.states.get(1), instance.states.get(4)
        if (isinstance(timer, nodes.ChaseVar) and timer.active and policy is not None and policy.active
                and policy.node.cls == "STUStatescriptStateCombatModFilter"
                and _defense_clock_current(timer, script.frame_ms, tick)):
            return True
    return False


def fortify_blocks_force(owner, tick: int) -> bool:
    """Apply Fortify's authored control flag to Lucio's reconstructed impulses."""
    script = getattr(owner, "body_script", None)
    if not _fortify_active(script, tick):
        return False
    flag = script.component.entity_var(9161, create=False)
    return flag is not None and expr.truthy(flag.value())


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
    definition: int | None = None
    reflections: int = 0
    outgoing_scale: float = 1.0


@dataclass(frozen=True)
class ProjectileImpact:
    shooter: object
    body: int
    frame: int
    struck: Target | None
    point: tuple
    visible_from: tuple
    direct: HitDef | None
    blast: HitDef | None
    critical: bool
    incoming_scale: float | None = None
    outgoing_scale: float = 1.0


@dataclass(frozen=True)
class NpcWeaponImpact:
    shooter: bots.Bot
    body: int
    frame: int
    notice_frame: int
    target: Target
    amount: float
    critical: bool
    loadout: int | None
    effect: int | None
    pin: tuple[int, int, int] | None = None
    boost_credit: tuple[int, float] | None = None


LandedImpact = ProjectileImpact | NpcWeaponImpact


@dataclass(frozen=True)
class Hitscan:
    player: object
    body: int
    frame: int
    notice_frame: int
    view_tick: float
    origin: tuple
    direction: tuple
    reach: float
    hit: HitDef


@dataclass(frozen=True)
class Melee:
    player: object
    body: int
    frame: int
    view_tick: float
    origin: tuple
    direction: tuple
    centre: tuple
    radius: float
    hit: HitDef
    feet: tuple
    tags: frozenset[int]
    ring: tuple[float, float, float] | None
    wraith_protected: frozenset[int] | None = None
    mitigation: dict[int, tuple[float, bool]] | None = None
    deflected: frozenset[int] | None = None


@dataclass(frozen=True)
class Soundwave:
    player: object
    body: int
    frame: int
    view_tick: float
    origin: tuple
    direction: tuple
    radius: float
    threshold: float
    hit: HitDef
    impulse: expr.Vec3 | None
    wraith_protected: frozenset[int] | None = None
    mitigation: dict[int, tuple[float, bool]] | None = None
    occluders: tuple[experimental_barrier.BarrierPose, ...] | None = None


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
        self.stuns: dict[int, int] = {}  # body identity -> exclusive world-tick deadline
        self.heal_denials: dict[int, tuple[int, int]] = {}  # body -> [impact tick, exclusive deadline)
        self.heal_boosts: dict[int, tuple[int, int]] = {}
        self.pins: dict[int, int] = {}  # declared manual Assassin body -> controlled victim body
        self.projectiles = projectiles.Projectiles(self)  # leases, confirmations, the Biotic Field
        self.observers = observers.Observers(match)  # the bodies' statescript for the other clients
        self.voice = voicelines.VoiceLines(match)  # the hero's spawn voice line (voicelines.py)
        self.lucio_auras = lucio.Auras(self)
        self.lucio_barriers = lucio.Barriers(self)
        self.grasp_shields = {}
        self.barriers = {}
        self.knockbacks = knockback.Knockbacks(self)

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
        self._discard_impacts(player.body, retiring=True)
        self.lucio_auras.retire(player.body)
        self.lucio_barriers.retire(player.body)
        self.grasp_shields.pop(player.body, None)
        for entity, barrier in list(self.barriers.items()):
            if barrier.owner_body == player.body:
                del self.barriers[entity]
        self.vitals.pop(player.body, None)
        self.history.pop(player.body, None)
        self.replacement_ticks.pop(player.body, None)
        self.melee_seen.pop(player.body, None)
        self.stuns.pop(player.body, None)
        self.clear_pins(player.body)
        self._discard_stun_poses(player.body)
        self.heal_denials.pop(player.body, None)
        self.heal_boosts.pop(player.body, None)
        self.knockbacks.retire(player.body)
        # The owner's leased projectiles and their observer copies retire with the old body.
        self.rockets[:] = [
            rocket for rocket in self.rockets
            if rocket.shooter is not player or (rocket.shot is None and player.hero.guid != lucio.HERO)
        ]
        self.respawns[:] = [(when, who) for when, who in self.respawns if who is not player]

    def record_replacement(self, body: int) -> None:
        """Keep the creation boundary independent of the rolling pose history."""
        self.replacement_ticks[body] = self.match.tick
        self.stuns.pop(body, None)
        self.clear_pins(body)
        self._discard_stun_poses(body)
        self.heal_denials.pop(body, None)
        self.heal_boosts.pop(body, None)

    def is_stunned(self, actor, *, tick: int | None = None) -> bool:
        entity = _source_entity(actor)
        return (entity in self.pins.values()
                or self.stuns.get(entity, 0) > (self.tick if tick is None else tick))

    def pose_stunned(self, player, frame: int, tick: int) -> bool:
        """Apply past control to recovered poses after its world effect ends."""
        windows = getattr(self, "stun_poses", None)
        intervals = windows.get(player.body, ()) if windows is not None else ()
        if intervals and intervals[0][1] <= frame:
            intervals = [interval for interval in intervals if interval[1] > frame]
            if intervals:
                windows[player.body] = intervals
            else:
                self._discard_stun_poses(player.body)
        return self.is_stunned(player, tick=tick) or any(start <= frame < end for start, end in intervals)

    def _discard_stun_poses(self, entity: int) -> None:
        windows = getattr(self, "stun_poses", None)
        if windows is not None:
            windows.pop(entity, None)
            if not windows:
                del self.stun_poses

    def _remember_stun_poses(self, entity: int, start: int, end: int) -> None:
        if end <= start:
            return
        if not hasattr(self, "stun_poses"):
            self.stun_poses = {}
        intervals = self.stun_poses.get(entity, ())
        merged = []
        for first, last in sorted((*intervals, (start, end))):
            if merged and merged[-1][1] >= first:
                previous_start, previous_end = merged[-1]
                merged[-1] = (previous_start, max(previous_end, last))
            else:
                merged.append((first, last))
        self.stun_poses[entity] = merged

    def begin_pin(self, source: int, victim: int) -> bool:
        if self.pins.get(source) == victim:
            return False
        self.end_pin(source)
        if not hasattr(self, "pin_pose_starts"):
            self.pin_pose_starts = {}
        self.pin_pose_starts[source] = self.tick
        self.pins[source] = victim
        return True

    def end_pin(self, source: int) -> None:
        victim = self.pins.pop(source, None)
        starts = getattr(self, "pin_pose_starts", None)
        if starts is not None:
            start = starts.pop(source, None)
            if victim is not None and start is not None:
                self._remember_stun_poses(victim, start, self.tick)
            if not starts:
                del self.pin_pose_starts

    def clear_pins(self, entity: int) -> None:
        for source, victim in list(self.pins.items()):
            if entity in (source, victim):
                self.end_pin(source)

    def _hit_effect(self, target: Target, effect: int | None) -> None:
        """Apply reconstructed host effects delivered by the weapon hit path."""
        if effect not in (FLASHBANG_EFFECT, ANA_GRENADE_EFFECT):
            return
        if self.vitals_of(target).dead or wraith.protects(target.owner, self.tick):
            return
        if effect == ANA_GRENADE_EFFECT:
            start, end = self.heal_denials.get(target.entity, (self.tick, self.tick))
            if end < self.tick:
                start = self.tick
            self.heal_denials[target.entity] = (start, max(end, self.tick + ANA_HEAL_DENIAL_TICKS))
            return
        variables = graph.mode_variables(self.match.game_map.mode_guid) or {}
        duration = 1.7 if expr.truthy(variables.get(FLASHBANG_MODE_FLAG)) else 0.8
        deadline = self.tick + math.ceil(duration / TICK)
        self.stuns[target.entity] = max(deadline, self.stuns.get(target.entity, 0))
        if not isinstance(target.owner, bots.Bot):
            self._remember_stun_poses(target.entity, self.tick, deadline)
        self._interrupt(target)

    def healing_ticks(self, entity: int, previous: int, tick: int) -> float:
        """Weight healing samples in (previous, tick], giving denial precedence over boost."""
        start, end = self.heal_denials.get(entity, (tick, tick))
        boost_start, boost_end = self.heal_boosts.get(entity, (tick, tick))
        first, last = previous + 1, tick + 1
        blocked = max(0, min(last, end) - max(first, start))
        boosted = max(0, min(last, boost_end) - max(first, boost_start))
        both = max(0, min(last, end, boost_end) - max(first, start, boost_start))
        return tick - previous - blocked + (ANA_HEAL_BOOST - 1) * (boosted - both)

    def _interrupt(self, target: Target) -> None:
        self._discard_impacts(target.entity)
        script = getattr(target.owner, "body_script", None)
        if script is None:
            return
        component = script.component
        if isinstance(target.owner, bots.Bot):
            from ow174.game import assassin

            assassin.bind(component.world, component, None)
        else:
            wall_ride.cancel(target.owner)
        component.edges([(value, False) for value in tuple(component.held)])
        for instance in list(component.instances.values()):
            for state in list(instance.states.values()):
                if isinstance(state, nodes.Ability):
                    state.presses.clear()
                    if not lucio.keep_active_amp_on_stun(target.owner, state):
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
        if self.is_dead(player):
            return
        stunned = self.is_stunned(player)
        if not stunned:
            wraith.finish_entry(player)
            if command is not None:
                for state, number, time, activation in shots:
                    try:
                        self._shoot(player, command, state, number, time, activation=activation)
                    except Exception:
                        log.exception("[game] %s: %s's shot failed", self.match.label(), player.name)
        try:
            if stunned:
                # Crossfade and an already-applied Amp still resolve while
                # stunned. Sample timer changes before world catch-up.
                if script.hero == lucio.HERO:
                    self.lucio_auras.refresh(script.frame)
                return
            self._melee(player, command, script)
            self._soundwave(player, command, script)
            self.lucio_auras.refresh(script.frame)
            self.lucio_barriers.command(player)
            self.publish_impacts(self.tick)
        except Exception:
            log.exception("[game] %s: %s's area attack failed", self.match.label(), player.name)

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
            direction = aim(*command.aim())
            centre = _along(origin, direction, reach)
            view_tick = command.frame - command.view_delay(player.quanta) * 1e6 / player.quanta
            if not amount > 0.0:
                continue
            is_lucio = script.hero == lucio.HERO
            credit = mercy_boost.credit(self, player) if is_lucio else None
            if credit is not None:
                amount *= credit[1]
            melee = Melee(player, script.entity, command.frame, view_tick, origin, direction,
                          centre, radius, HitDef(amount, loadout, boost_credit=credit), tuple(player.mover.position),
                          deflection.melee_tags(state), knockback.quick_melee_ring(state) if is_lucio else None)
            if self._impact_waits(melee, self.tick):
                self._queue_impact(melee)
            else:
                self._resolve_melee(melee)

    def _resolve_melee(self, melee: Melee, *, world_tick: int | None = None) -> None:
        for target in self._enemies(melee.player, wraith_protected=melee.wraith_protected):
            shown = self.seen(target, melee.view_tick)
            if shown is None:
                continue
            a, b = shown.segment()
            if (segment_distance(melee.centre, a, b) - shown.radius <= melee.radius
                    and capsule_visible(self._collision(), melee.origin, a, b, shown.radius)):
                blocked = (deflection.blocks_melee(melee.feet, target, melee.tags, self.tick)
                           if melee.deflected is None else target.entity in melee.deflected)
                if blocked:
                    continue
                scale, force_blocked = self._area_mitigation(melee, target.entity)
                self._damage(melee.player, target, melee.hit.amount, False, melee.hit.loadout, melee.frame,
                             world_tick=world_tick, incoming_scale=scale, boost_credit=melee.hit.boost_credit,
                             wraith_checked=True)
                if melee.ring is not None:
                    impulse = knockback.quick_melee_impulse(melee.ring, melee.origin, target, melee.direction)
                    self._apply_knockback(target, impulse, melee.frame, world_tick, force_blocked=force_blocked)

    def _soundwave(self, player, command, script) -> None:
        """0079's 0776 st11 health operation, once per authored activation.

        Radius and damage come from the target build's st10/st11 configuration.
        Reconstruction: st10's angle 70 is treated as a full cone angle, with
        capsule-centre angular selection. Visibility uses the native fallback
        height fractions on the server's historical capsule and enemy
        Experimental Barrier poses sampled at impact eligibility. Authored
        attachment anchors and native barrier registration remain unresolved. The impulse uses the
        reconstructed mover with scoped Fortify resistance.
        """
        if player.hero.guid != 0x02E0000000000079:
            return
        for instance in script.component.instances.values():
            if instance.graph.index != 0x0776:
                continue
            state, search = instance.states.get(11), instance.states.get(10)
            if state is None or search is None:
                continue
            counters = self.melee_seen.setdefault(script.entity, {})
            key = (instance.id, state.index)
            seen = counters.get(key, 0)
            counters[key] = state.counter
            if state.counter == seen or not state.active or command is None:
                continue
            hit = hit_def(state, {"m_modifyHealth": state.node.field("m_modifyHealth")})
            radius = _number(search.evaluate(search.node.field("m_radius"), watch=False), 0.0)
            angle = _number(search.evaluate(search.node.field("m_A30B383E"), watch=False), 0.0)
            if hit is None or hit.amount <= 0 or radius <= 0 or not 0 < angle <= 360:
                continue
            credit = mercy_boost.credit(self, player)
            if credit is not None:
                hit = replace(hit, amount=hit.amount * credit[1], boost_credit=credit)
            impulse = state.evaluate(state.node.field("m_modifyHealth.m_1376B4DF"), watch=False)
            origin, direction = self._eye(player), aim(*command.aim())
            threshold = math.cos(math.radians(angle / 2))
            view_tick = command.frame - command.view_delay(player.quanta) * 1e6 / player.quanta
            wave = Soundwave(player, script.entity, command.frame, view_tick, origin, direction,
                             radius, threshold, hit, impulse if isinstance(impulse, expr.Vec3) else None)
            if self._impact_waits(wave, self.tick):
                self._queue_impact(wave)
            else:
                self._resolve_soundwave(wave)

    def _resolve_soundwave(self, wave: Soundwave, *, world_tick: int | None = None) -> None:
        occluders = wave.occluders
        if occluders is None:
            occluders = experimental_barrier.snapshot_enemy_barriers(self, wave.player)
        for target in self._enemies(wave.player, wraith_protected=wave.wraith_protected):
            shown = self.seen(target, wave.view_tick)
            if shown is None:
                continue
            a, b = shown.segment()
            if segment_distance(wave.origin, a, b) - shown.radius > wave.radius:
                continue
            centre = (shown.feet[0], shown.feet[1] + shown.height / 2, shown.feet[2])
            offset = _sub(centre, wave.origin)
            length = math.sqrt(_dot(offset, offset))
            if length > EPSILON:
                toward = tuple(value / length for value in offset)
                if _dot(toward, wave.direction) < wave.threshold:
                    continue
            # 104319 CA38D5 and CA4240 sample at 0.3 and 0.6 of the body height
            # when the geometry service uses fallback positions.
            x, y, z = shown.feet
            for fraction in (0.3, 0.6):
                sample = _sub((x, y + shown.height * fraction, z), wave.origin)
                reach = math.sqrt(_dot(sample, sample))
                if reach <= EPSILON:
                    break
                direction = tuple(axis / reach for axis in sample)
                wall = ray_world(self._collision(), wave.origin, direction, reach)
                if (wall is None or wall >= reach) and not any(
                    barrier.intersection(wave.origin, direction, reach) is not None for barrier in occluders
                ):
                    break
            else:
                continue
            scale, force_blocked = self._area_mitigation(wave, target.entity)
            self._damage(wave.player, target, wave.hit.amount, False, wave.hit.loadout, wave.frame,
                         world_tick=world_tick, incoming_scale=scale, boost_credit=wave.hit.boost_credit,
                         wraith_checked=True)
            self._apply_knockback(target, wave.impulse, wave.frame, world_tick, force_blocked=force_blocked)

    @staticmethod
    def _area_mitigation(attack: Melee | Soundwave, entity: int) -> tuple[float | None, bool | None]:
        return (None, None) if attack.mitigation is None else attack.mitigation.get(entity, (1.0, False))

    def _apply_knockback(self, target, impulse, frame: int, world_tick: int | None, *, force_blocked=None) -> None:
        if force_blocked is None:
            tick = self.tick if world_tick is None else world_tick
            force_blocked = fortify_blocks_force(target.owner, tick)
        if not force_blocked:
            self.knockbacks.apply(target, impulse, impact_frame=frame)

    def _queue_impact(self, attack: Hitscan | Melee | Soundwave | LandedImpact) -> None:
        if not hasattr(self, "pending_impacts"):
            self.pending_impacts = []
        self.pending_impacts.append(self._sample_area_defenses(attack, self.tick))

    def _sample_area_defenses(self, attack, tick):
        # Future inputs first become eligible at their retained world frame.
        # A same-frame Sound Barrier may still delay damage and impulse settlement.
        if (isinstance(attack, (Melee, Soundwave)) and attack.frame <= tick
                and attack.mitigation is None):
            protected = frozenset(player.body for player in self.match.players if wraith.protects(player, tick))
            mitigation = {}
            deflected = set()
            for target in self._targets():
                scale = incoming_damage_scale(target.owner, tick)
                force_blocked = fortify_blocks_force(target.owner, tick)
                if scale != 1.0 or force_blocked:
                    mitigation[target.entity] = (scale, force_blocked)
                if isinstance(attack, Melee) and deflection.blocks_melee(attack.feet, target, attack.tags, tick):
                    deflected.add(target.entity)
            attack = replace(attack, wraith_protected=protected, mitigation=mitigation)
            if isinstance(attack, Melee):
                attack = replace(attack, deflected=frozenset(deflected))
            else:
                occluders = experimental_barrier.snapshot_enemy_barriers(self, attack.player)
                attack = replace(attack, occluders=occluders)
        return attack

    def world_frames(self, tick: int) -> list[tuple[int, bool]]:
        """Plan retained world ticks and mark when unswept flights may begin."""
        first = max(self.tick + 1, tick - COMBAT_CATCH_UP + 1)
        if self.tick and first < tick and self.lucio_auras.has_moving_npc_recipients():
            # Route motion uses the previous frame's aura state. Refresh the
            # graph before the next pose can consume it.
            return [(at, True) for at in range(first, tick + 1)]
        frames = {tick}
        for attack in getattr(self, "pending_impacts", ()):
            if attack.frame <= tick:
                frames.add(min(tick, max(first, attack.frame)))
        frames.update(grant.tick for grant in self.lucio_barriers.pending if first <= grant.tick <= tick)
        if self.tick and first < tick and any(
            rocket.definition == LUCIO_PRIMARY and rocket.last_tick is not None for rocket in self.rockets
        ) and any(not bot.dead and (bot.route or bot.impulse_mover is not None) for bot in self.match.bots):
            # An in-flight shot needs the NPC pose at each sweep. Added samples
            # must not start other player flights before their regular slice.
            return [(at, at in frames) for at in range(first, tick + 1)]
        return [(at, True) for at in sorted(frames)]

    def _impact_waits(self, attack: Hitscan | Melee | Soundwave | LandedImpact, tick: int) -> bool:
        attacker = attack.shooter if isinstance(attack, LandedImpact) else attack.player
        return attack.frame > tick or (
            attack.frame == tick and self.lucio_barriers.awaiting_slam(attacker, tick)
        )

    def publish_impacts(self, tick: int, *, closing: bool = False, landed_only: bool = False) -> None:
        pending = getattr(self, "pending_impacts", None)
        if pending is None:
            return
        due, waiting = [], []
        for attack in pending:
            if not landed_only:
                attack = self._sample_area_defenses(attack, tick)
            keep = (
                (landed_only and not isinstance(attack, LandedImpact))
                or (attack.frame > tick if closing else self._impact_waits(attack, tick))
            )
            (waiting if keep else due).append(attack)
        pending[:] = waiting
        if not pending:
            del self.pending_impacts
        for attack in sorted(due, key=lambda attack: attack.frame):
            if isinstance(attack, LandedImpact):
                actors = self.match.bots if isinstance(attack.shooter, bots.Bot) else self.match.players
                if attack.shooter in actors and _source_entity(attack.shooter) == attack.body:
                    if isinstance(attack, ProjectileImpact):
                        self._resolve_projectile_impact(attack)
                    elif self._current_impact_target(attack.target):
                        self._resolve_npc_impact(attack, queued=True)
                continue
            player = attack.player
            if (player.body == attack.body and self.match._shown(player)
                    and not self.is_dead(player) and not self.is_stunned(player)):
                if isinstance(attack, Hitscan):
                    self._resolve_hitscan(attack, world_tick=tick)
                elif isinstance(attack, Melee):
                    self._resolve_melee(attack, world_tick=tick)
                else:
                    self._resolve_soundwave(attack, world_tick=tick)

    def _discard_impacts(self, body: int, *, retiring: bool = False) -> None:
        pending = getattr(self, "pending_impacts", None)
        if pending is not None:
            pending[:] = [attack for attack in pending
                          if attack.body != body or (not retiring and isinstance(attack, LandedImpact))]
            if not pending:
                del self.pending_impacts

    def update(
        self, now: float, tick: int, frames: bool = True, *, retained_from: int = 0, start_flights: bool = True,
    ) -> None:
        """Each tick: keep where the bodies are, fly the rockets, respawn, send health that changed. The
        other players' body frames only on the ticks that send (`frames`)."""
        if self.match.collision_world is None:
            return
        next_tick = self.tick + 1
        retained = max(next_tick, tick - COMBAT_CATCH_UP + 1, retained_from)
        catch_up = self.tick != 0 and tick > next_tick
        firing_bots = catch_up and any(
            bot.firing and not bot.dead and isinstance(bot.body_script, BotScript) for bot in self.match.bots
        )
        barrier_due = self.lucio_barriers.due(tick)
        barrier_steps = catch_up and (
            barrier_due or bool(self.lucio_barriers.leases and (self.rockets or firing_bots))
        )
        grasp_steps = catch_up and bool(self.rockets or firing_bots) and any(
            grasp.SHIELD_KEY in vitals.temporary_shields for vitals in self.vitals.values()
        )
        grenade_steps = catch_up and any(
            rocket.blast is not None and rocket.blast.effect == ANA_GRENADE_EFFECT
            and rocket.last_tick is not None and rocket.last_tick < tick
            for rocket in self.rockets
        )
        first = retained if firing_bots or barrier_steps or grasp_steps or grenade_steps else tick
        for target in self._targets():
            history = self.history.setdefault(target.entity, deque(maxlen=HISTORY))
            history.append((tick, target.feet, target.height, target.eye))
        # Replay retained combat frames using the most recent poses. Flights
        # without a launch tick join at the endpoint. A queued Barrier enters
        # before damage on its slam frame.
        for at in range(first, tick + 1):
            if at > self.tick:
                self.publish_impacts(self.tick, closing=True, landed_only=True)
            self.now, self.tick = now - (tick - at) * TICK, at
            if barrier_due:
                self.lucio_barriers.publish(at)
            if at == first and at > next_tick:
                # Settle decay through the frame before retained damage.
                if barrier_steps:
                    self.lucio_barriers.update(at - 1)
                if grasp_steps:
                    grasp.decay_shields(self, self.now - TICK)
            for rocket in list(self.rockets):
                if rocket.last_tick is not None or (at == tick and start_flights):
                    self._fly(rocket)
            self._bot_weapons(at)
            if at < tick:
                if barrier_steps:
                    self.lucio_barriers.update(at)
                if grasp_steps:
                    grasp.decay_shields(self, self.now)
        self.projectiles.update(tick, first_tick=retained)
        self.match.deliver_body_messages()
        self.lucio_auras.update(tick)
        self.lucio_barriers.update(tick)
        grasp.decay_shields(self, now)
        experimental_barrier.update_barriers(self)
        for player in self.match.players:
            if self.match._shown(player):
                lucio.advance_passive_charge(player, tick)
        self.knockbacks.update()
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
        for table in (self.heal_denials, self.heal_boosts):
            for entity in [key for key, (_, deadline) in table.items() if deadline <= tick]:
                del table[entity]
        for table in (self.history, self.vitals, self.replacement_ticks, self.stuns,
                      self.heal_denials, self.heal_boosts):
            for entity in [key for key in table if key not in current]:
                del table[entity]
        for source, victim in list(self.pins.items()):
            if source not in current or victim not in current:
                self.end_pin(source)
        for entity in set(getattr(self, "stun_poses", ())) - current:
            self._discard_stun_poses(entity)

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
                    bot.entity, bot.team, tuple(bot.position), data.stand_height, data.stand_radius,
                    eye_height(bot.body), bot
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

    def _enemies(self, shooter, *, wraith_protected: frozenset[int] | None = None) -> list[Target]:
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
            protected = (wraith.protects(target.owner, self.tick) if wraith_protected is None
                         else target.entity in wraith_protected)
            if not protected:
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
                self.clear_pins(bot.entity)
                bot.body_script = None
                log.exception("[game] %s: %s's weapon script failed", self.match.label(), bot.name)

    def hack_line_clear(self, owner, origin, target, offset, distance: float) -> bool:
        # Reconstructed static capsule and dynamic eye-ray visibility.
        if not capsule_visible(self._collision(), origin, *target.segment(), target.radius):
            return False
        if not distance:
            return True
        direction = tuple(axis / distance for axis in offset)
        return experimental_barrier.barrier_intersection(self, owner, origin, direction, distance) is None

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
            origin = (x, y + eye_height(shooter.body), z)
            direction = aim(round(shooter.yaw), round(shooter.pitch))
        else:
            origin, direction = self._eye(shooter), aim(*command.aim())
        direct = hit_def(state, node.field("m_6396149F"))
        speed = field("m_projectileSpeed")
        if speed:
            lifetime = field("m_projectileLifetime", 5.0) or 5.0
            reach = field("m_FEC435C6")
            if reach:
                lifetime = min(lifetime, reach / speed)
            blast = hit_def(state, node.field("m_D2D11CE9"))
            entity_def = (node.field("m_projectileEntity") or {}).get("m_entityDef")
            definition = state.evaluate(entity_def, watch=False) if entity_def is not None else None
            if (definition == expr.Asset(LUCIO_PRIMARY) and state.instance.graph.index == 0x0776
                    and node.pos == 36 and direct is not None
                    and node.field("m_6396149F.m_A872C70E") is None):
                # The native compiler emits -1 for the omitted field. The
                # selected projectile uses a declared host multiplier.
                direct = replace(direct, critical=2.0)
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
                from ow174.game import npc_control

                credit = (mercy_boost.credit(self, shooter)
                          if direct is not None and ((npc and npc_control.manual(shooter))
                                                    or definition == expr.Asset(LUCIO_PRIMARY)) else None)
                if credit is not None:
                    direct = replace(direct, boost_credit=credit)
                launch_tick = self.tick if npc else None
                if blast is not None and blast.effect == ANA_GRENADE_EFFECT:
                    launch_tick = max(self.tick, shot_frame)
                self.rockets.append(
                    Rocket(
                        shooter, shooter.team, origin, direction, speed, lifetime, direct, blast, shot,
                        last_tick=launch_tick,
                        definition=definition.guid if isinstance(definition, expr.Asset) else None,
                        outgoing_scale=credit[1] if credit is not None else 1.0,
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
        frame = command.frame if command is not None else self.tick
        ray = Hitscan(shooter, _source_entity(shooter), frame, shot_frame, view_tick, origin, direction, reach, direct)
        if not npc and self._impact_waits(ray, self.tick):
            self._queue_impact(ray)
        else:
            self._resolve_hitscan(ray)

    def _resolve_hitscan(self, ray: Hitscan, *, world_tick: int | None = None) -> None:
        shooter, direct = ray.player, ray.hit
        best = None
        for target in self._enemies(shooter):
            shown = self.seen(target, ray.view_tick)
            if shown is None:
                continue
            a, b = shown.segment()
            t = ray_capsule(ray.origin, ray.direction, a, b, shown.radius)
            if t is not None and t <= ray.reach and (best is None or t < best[0]):
                best = (t, target, shown)
        if best is None:
            return
        t, target, shown = best
        wall = ray_world(self._collision(), ray.origin, ray.direction, t)
        if wall is not None and wall < t:
            return
        hit_y = ray.origin[1] + ray.direction[1] * t
        critical = hit_y >= shown.feet[1] + shown.eye - HEAD_BELOW
        amount = direct.amount * direct.scale(t) * (direct.critical if critical else 1.0)
        if isinstance(shooter, bots.Bot):
            self.npc_weapon_hit(shooter, target, amount, critical and direct.critical > 1.0,
                                direct.loadout, ray.notice_frame, direct.effect)
            return
        self._damage(
            shooter, target, amount, critical and direct.critical > 1.0, direct.loadout, ray.notice_frame,
            world_tick=world_tick,
        )
        self._hit_effect(target, direct.effect)

    def _deflector(self, rocket, reach, start_tick, targets):
        if not deflection.admitted(rocket.definition):
            return None
        closest = None
        for target in targets:
            owner = target.owner
            hero = getattr(getattr(owner, "hero", None), "guid", None)
            if hero == deflection.GENJI:
                graph_index, state_index, window = deflection.GRAPH, 10, deflection.window
            elif hero == grasp.HERO:
                graph_index, state_index, window = grasp.GRAPH, 1, grasp.window
            else:
                continue
            script = owner.body_script
            if script is None or script.retired or self.is_stunned(owner):
                continue
            instance = next((i for i in script.component.instances.values()
                             if i.graph.index == graph_index and not i.stopped), None)
            if instance is None:
                continue
            state = instance.states.get(state_index)
            if state is None or not state.active:
                continue
            found = deflection.intercept(state, self._eye(owner), owner.mover.yaw, owner.mover.pitch,
                                         rocket.position, rocket.direction, reach)
            if found is None:
                continue
            distance, outgoing = found
            if distance >= reach - EPSILON or (closest is not None and distance >= closest[0]):
                continue
            at_tick = start_tick + distance / (rocket.speed * TICK)
            if window(instance, at_tick) is not None:
                closest = (distance, target, outgoing, state if hero == grasp.HERO else None)
        return closest

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
        self._sweep(rocket, duration, self.tick - elapsed + 1)

    def _sweep(self, rocket: Rocket, duration: float, start_tick: float) -> None:
        # Sweep the remaining flight even when the range or lifetime ends inside this tick.
        step = rocket.speed * duration
        # Accumulated flight can leave the final endpoint a few ulps short.
        reach = step + EPSILON if duration == rocket.left else step
        best = None
        targets = self._enemies(rocket.shooter)
        for target in targets:
            a, b = target.segment()
            t = ray_capsule(rocket.position, rocket.direction, a, b, target.radius)
            if t is not None and t <= reach and (best is None or t < best[0]):
                best = (t, target)
        if best is not None:
            best = (min(best[0], step), best[1])
        wall = ray_world(self._collision(), rocket.position, rocket.direction, step)
        if wall is not None and (best is None or wall < best[0]):
            best = (wall, None)
        limit = best[0] if best is not None else step
        barrier = (experimental_barrier.barrier_intersection(
            self, rocket.shooter, rocket.position, rocket.direction, limit,
        ) if rocket.definition == LUCIO_PRIMARY else None)
        if barrier is not None and best is not None and barrier[0] >= limit - EPSILON:
            barrier = None
        intercepted = self._deflector(rocket, barrier[0] if barrier is not None else limit, start_tick, targets)
        if barrier is not None and intercepted is None:
            distance, actor = barrier
            rocket.flown += distance / rocket.speed
            if rocket.shot is not None:
                self.projectiles.intercepted(rocket.shot, self.tick)
                rocket.shot = None
            self._landed(rocket)
            if rocket.direct is not None:
                experimental_barrier.damage_barrier(actor, rocket.direct.amount * rocket.outgoing_scale)
            return
        if intercepted is not None:
            distance, defender, outgoing, absorber = intercepted
            spent = distance / rocket.speed
            rocket.flown += spent
            if rocket.shot is not None:
                self.projectiles.intercepted(rocket.shot, self.tick)
                rocket.shot = None
            if absorber is not None:
                self._landed(rocket)
                grasp.absorbed(absorber, rocket.direct, _source_entity(rocket.shooter))
                return
            if rocket.reflections >= deflection.MAX_REFLECTIONS:
                self._landed(rocket)
                return
            point = _along(rocket.position, rocket.direction, distance)
            rocket.position = _along(point, outgoing, 1e-6)
            rocket.direction = outgoing
            rocket.shooter, rocket.team = defender.owner, defender.team
            rocket.outgoing_scale = 1.0
            if rocket.direct is not None and rocket.direct.boost_credit is not None:
                rocket.direct = replace(rocket.direct, boost_credit=None)
            rocket.reflections += 1
            rocket.left = max(0.0, rocket.left - spent)
            remaining = min(duration - spent, rocket.left)
            if remaining > EPSILON:
                self._sweep(rocket, remaining, start_tick + spent / TICK)
            elif rocket.left <= EPSILON:
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
        if struck is None and rocket.blast is None:
            return
        point = _along(rocket.position, rocket.direction, t)
        # Start just outside an impact wall, on the projectile's approach side.
        visible_from = _along(point, rocket.direction, -0.0001) if wall is not None else point
        critical = (
            struck is not None and rocket.direct is not None and rocket.definition == LUCIO_PRIMARY
            and rocket.direct.critical > 1.0 and point[1] >= struck.feet[1] + struck.height - PROJECTILE_HEAD_BAND
            and not _fortify_active(getattr(struck.owner, "body_script", None), self.tick)
        )
        # Retain direct-hit mitigation with the contact's critical classification.
        # A same-frame Barrier still joins the target's pools before settlement.
        incoming_scale = (
            incoming_damage_scale(struck.owner, self.tick)
            if struck is not None and rocket.direct is not None and rocket.definition == LUCIO_PRIMARY else None
        )
        impact = ProjectileImpact(rocket.shooter, _source_entity(rocket.shooter), self.tick, struck,
                                  point, visible_from, rocket.direct, rocket.blast, critical, incoming_scale,
                                  rocket.outgoing_scale)
        if self._impact_waits(impact, self.tick):
            self._queue_impact(impact)
        else:
            self._resolve_projectile_impact(impact)

    def _current_impact_target(self, struck: Target) -> bool:
        return any(target.entity == struck.entity and target.owner is struck.owner for target in self._targets())

    def npc_weapon_hit(self, shooter, target, amount, critical, loadout, notice_frame, effect=None, *, pin=None) -> None:
        from ow174.game import npc_control

        credit = mercy_boost.credit(self, shooter) if amount > 0 and npc_control.manual(shooter) else None
        if credit is not None:
            amount *= credit[1]
        impact = NpcWeaponImpact(shooter, shooter.entity, self.tick, notice_frame, target, amount,
                                 critical, loadout, effect, pin, boost_credit=credit)
        if self._impact_waits(impact, self.tick):
            self._queue_impact(impact)
        else:
            self._resolve_npc_impact(impact)

    def _resolve_npc_impact(self, impact: NpcWeaponImpact, *, queued: bool = False) -> None:
        if impact.pin is not None:
            from ow174.game import assassin

            if ((not queued or assassin.pin_still_active(impact.shooter, impact.target, impact.pin))
                    and self.begin_pin(impact.body, impact.target.entity)):
                self._interrupt(impact.target)
        if impact.amount > 0:
            self._damage(impact.shooter, impact.target, impact.amount, impact.critical,
                         impact.loadout, impact.notice_frame, world_tick=impact.frame,
                         boost_credit=impact.boost_credit, wraith_checked=True)
        self._hit_effect(impact.target, impact.effect)

    def _resolve_projectile_impact(self, impact: ProjectileImpact) -> None:
        blast, struck, direct = impact.blast, impact.struck, impact.direct
        if blast is not None and blast.effect == ANA_GRENADE_EFFECT:
            # Settle prior aura samples before grenade damage, healing or
            # replacement of a modifier window. Observe poses at world time.
            self.lucio_auras.update(self.tick - 1, observe=False)
        if struck is not None and direct is not None and direct.amount > 0.0 and self._current_impact_target(struck):
            self._damage(
                impact.shooter, struck,
                direct.amount * impact.outgoing_scale * (direct.critical if impact.critical else 1.0),
                impact.critical, direct.loadout, impact.frame, world_tick=impact.frame,
                incoming_scale=impact.incoming_scale,
                boost_credit=direct.boost_credit,
                wraith_checked=True,  # The sweep already admitted the target before queuing.
            )
            self._hit_effect(struck, direct.effect)
        if blast is None or blast.amount <= 0.0:
            return
        if blast.effect == ANA_GRENADE_EFFECT:
            self._heal_grenade_allies(impact.shooter, blast, impact.point, impact.visible_from)
        for target in self._enemies(impact.shooter):
            a, b = target.segment()
            distance = max(0.0, segment_distance(impact.point, a, b) - target.radius)
            scalar = blast.splash(distance)
            if scalar > 0.0 and capsule_visible(self._collision(), impact.visible_from, a, b, target.radius):
                self._damage(
                    impact.shooter, target, blast.amount * scalar, False, blast.loadout, impact.frame,
                    world_tick=impact.frame,
                )
                self._hit_effect(target, blast.effect)

    def _heal_grenade_allies(self, healer, blast, point, visible_from) -> None:
        """Reconstructed B99 default heal and incoming boost at a grenade impact."""
        for target in self._targets():
            ally = not self.match.game_map.free_for_all and target.team == healer.team
            if target.owner is not healer and not ally:
                continue
            vitals = self.vitals_of(target)
            if vitals.dead:
                continue
            a, b = target.segment()
            distance = max(0.0, segment_distance(point, a, b) - target.radius)
            scalar = blast.splash(distance)
            if scalar <= 0 or not capsule_visible(self._collision(), visible_from, a, b, target.radius):
                continue
            if self.healing_ticks(target.entity, self.tick - 1, self.tick):
                given = vitals.heal(ANA_GRENADE_HEAL * scalar)
                if given > 0:
                    healer.stats.add(stats.HEALING, given)
                    self._sync_body_health(target.owner, vitals)
            start, end = self.heal_boosts.get(target.entity, (self.tick, self.tick))
            if end < self.tick:
                start = self.tick
            self.heal_boosts[target.entity] = (start, self.tick + ANA_HEAL_DENIAL_TICKS)

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
            multiplier = self.healing_ticks(target.entity, self.tick - 1, self.tick)
            if not multiplier:
                continue
            a, b = target.segment()
            if segment_distance(centre, a, b) - target.radius > radius:
                continue
            given = vitals.heal(amount * multiplier)
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
        *, world_tick: int | None = None, incoming_scale: float | None = None,
        boost_credit: tuple[int, float] | None = None, wraith_checked: bool = False,
    ) -> None:
        vitals = self.vitals_of(target)
        if vitals.dead or amount <= 0.0 or (not wraith_checked and wraith.protects(target.owner, self.tick)):
            return
        victim = target.owner
        if world_tick is not None:
            # Earlier samples settle before impact. This frame's healing
            # and passive charge still follow damage.
            if self.lucio_auras.healing:
                self.lucio_auras.update(world_tick - 1, observe=False)
            if not isinstance(victim, bots.Bot):
                lucio.advance_passive_charge(victim, world_tick - 1, due_only=True)
        lucio_source = getattr(getattr(shooter, "hero", None), "guid", None) == lucio.HERO
        base_before = sum(vitals.now.values()) if lucio_source else 0.
        tick = self.tick if world_tick is None else world_tick
        scale = incoming_damage_scale(victim, tick) if incoming_scale is None else incoming_scale
        scaled = amount * scale
        base = vitals.damage(scaled / boost_credit[1], preview=True) if boost_credit is not None else 0.0
        dealt = vitals.damage(scaled)
        if dealt <= 0.0:
            return
        if boost_credit is not None and dealt > base:
            mercy_boost.award(self.match, boost_credit, target.entity, dealt, dealt - base)
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
        if lucio_source:
            lucio.charge_damage(shooter, target, base_before - sum(vitals.now.values()), loadout)
        if killed and isinstance(victim, bots.Bot):
            # The NPC death script owns its terminal health change and death event ordering.
            self._bot_died(shooter, victim, critical, loadout, vitals.death_tick)
            return
        self._sync_body_health(victim, vitals)
        if getattr(getattr(victim, "hero", None), "guid", None) == 0x02E000000000012E:
            self._interrupt_hack(victim, _source_entity(shooter))
        if killed:
            self._died(shooter, victim, critical, loadout)

    @staticmethod
    def _interrupt_hack(victim, sender: int) -> None:
        """Reconstruct damage interruption through Hack's authored channel abort."""
        script = victim.body_script
        if script is None or script.retired:
            return
        component = script.component
        with component.operation():
            for instance in list(component.instances.values()):
                if instance.graph.index == 0x0B1B:
                    wait = instance.states.get(instance.graph.node(100).state)
                    if wait is not None and wait.active:
                        runtime.send_instance_message(instance, 0x0240000000000011, {}, sender)

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
        self.stuns.pop(bot.entity, None)
        self.heal_denials.pop(bot.entity, None)
        self.heal_boosts.pop(bot.entity, None)
        self.knockbacks.retire(bot.entity)
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
            sender = _source_entity(killer)
            if isinstance(script, BotScript):
                tick = max(tick, script.frame)
                event = script.die(tick, params, sender, manual_attack=bot.spawner.get("manual_spawn") is True)
            else:
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

    def _died(self, killer, victim, critical: bool, loadout) -> None:
        """A player died: the notices, his body script's "died" (the kill feed line), and the respawn."""
        self._discard_impacts(victim.body)
        self.stuns.pop(victim.body, None)
        self.clear_pins(victim.body)
        self._discard_stun_poses(victim.body)
        self.heal_denials.pop(victim.body, None)
        self.heal_boosts.pop(victim.body, None)
        self.knockbacks.retire(victim.body)
        wall_ride.cancel(victim)
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
                # A queued or active Hack belongs to the living victim body.
                for receiver in list(component.instances.values()):
                    if receiver.graph.index == 0x0B1F:
                        component.destroy(receiver)
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
