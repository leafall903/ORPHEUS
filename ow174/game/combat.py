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
    absorption,
    actors,
    baptiste,
    barrier,
    bots,
    deflection,
    earthshatter,
    healing,
    ice_wall,
    observers,
    pools,
    projectiles,
    reinhardt_charge,
    stats,
    voicelines,
    weapon_spread,
    world,
)
from ow174.game.bits import BitWriter
from ow174.game.collision import MOVER, World
from ow174.game.commands import Command
from ow174.game.contacts import closest_segments
from ow174.game.mover import Mover, mover_data
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
    0x04000000000029A6: 1.35, 0x0400000000002AB1: 2.1,
    0x0400000000000225: 1.2, 0x04000000000002A3: 1.425,
}  # verified104319 FirstPerson m_E29993A5; animated camera/muzzle offsets remain open
EYE_CROUCHED = {
    0x04000000000003CF: 1.085, 0x04000000000029A6: .945,
    0x0400000000002AB1: 1.75, 0x0400000000000225: .84, 0x04000000000002A3: .9975,
}  # m_816B830F
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
FLASHBANG_MODE_FLAG = 13408
HELIX_BARRIER_PROJECTILES = frozenset((0x040000000000072C, 0x0400000000001C86))  # 0254 pos38


# --- geometry -----------------------------------------------------------------------------------------


def aim(yaw: int, pitch: int) -> tuple[float, float, float]:
    """The unit vector a command looks along: forward is (sin yaw, 0, cos yaw), the pitch is positive
    looking down (commands.py)."""
    y = yaw * 2 * math.pi / 65536
    p = pitch * 2 * math.pi / 65536
    return (math.sin(y) * math.cos(p), -math.sin(p), math.cos(y) * math.cos(p))


def eye_height(body: int, crouched: bool = False) -> float:
    return EYE_CROUCHED.get(body, EYE_CROUCHED_DEFAULT) if crouched else EYE_STANDING.get(body, EYE)


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


def capsule_visible(
    collision: World | None, origin, a, b, radius: float, *, blockers=(), mesh_blocks=None,
) -> bool:
    """Use a reconstructed ray to the nearest capsule surface for line of sight."""
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
    if hit is not None and hit + EPSILON < reach:
        return False
    if mesh_blocks is not None and mesh_blocks(origin, direction, reach):
        return False
    for target in blockers:
        cut = ray_capsule(origin, direction, *target.segment(), target.radius)
        if cut is not None and cut + EPSILON < reach:
            return False
    return True


def ray_world(
    collision: World | None, origin, direction, length: float, category: int = MOVER,
) -> float | None:
    """The distance to the first static hit, for callers that only need occlusion."""
    hit = collision.cast_ray(origin, direction, length, category) if collision is not None else None
    return hit.distance if hit is not None else None


# --- health -------------------------------------------------------------------------------------------


@dataclass
class TemporaryShield:
    identifier: int
    slot: int
    pool_id: int
    most: float
    now: float
    removed: bool = False


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
        self.ult_healing_deficit = 0.0  # reconstructed enemy-caused missing HP eligible for charge
        self.extra: dict[tuple, TemporaryShield] = {}

    @property
    def dead(self) -> bool:
        return self.died_at is not None

    @property
    def total(self) -> float:
        return self.base_total + sum(pool.now for pool in self.extra.values())

    @property
    def base_total(self) -> float:
        return sum(self.now.values())

    @property
    def maximum(self) -> float:
        return sum(self.most.values()) + sum(pool.most for pool in self.extra.values())

    def add_temporary(self, key, identifier, amount):
        if key in self.extra:
            return self.extra[key]
        if not math.isfinite(amount) or amount <= 0:
            return None
        occupied = {pool.slot for pool in self.extra.values() if not pool.removed}
        slot = next((slot for slot in range(1, POOLS) if slot not in occupied), None)
        if slot is None:
            raise ValueError('temporary shield pool slots exhausted')
        pool_id = max((*self.ids.values(), *(pool.pool_id for pool in self.extra.values())), default=0) + 1
        self.extra = {key: pool for key, pool in self.extra.items() if pool.slot != slot}
        found = TemporaryShield(identifier, slot, pool_id, amount, amount)
        self.extra[key] = found
        self.changed.add(SHIELDS_POOL)
        return found

    def remove_temporary(self, key):
        found = self.extra.get(key)
        if found is not None and not found.removed:
            found.now = found.most = 0.0
            found.removed = True
            self.changed.add(SHIELDS_POOL)

    def damage(self, amount: float, minimum_health: float = 0.0) -> float:
        """Take a hit from shields, then armour, then health (unverified, as OW1 did: what reaches armour
        while it has some is ARMOUR_BLOCK less, at most halved). Returns what the pools lost."""
        left = max(0.0, amount)
        taken = 0.0
        # Selected temporary overhealth precedes base pools and is unhealable.
        # Native pool priority/flags remain separate acceptance requirements.
        for key, pool in list(self.extra.items()):
            take = min(left, pool.now)
            if take > 0:
                pool.now -= take
                left -= take
                taken += take
                self.changed.add(SHIELDS_POOL)
                if pool.now <= EPSILON:
                    self.remove_temporary(key)
        for kind in (SHIELDS_POOL, ARMOUR_POOL, HEALTH_POOL):
            if left <= 0.0:
                break
            if kind == ARMOUR_POOL and self.now[kind] > 0.0:
                left = max(left - ARMOUR_BLOCK, left / 2)
            available = self.now[kind]
            if kind == HEALTH_POOL:
                available = max(0.0, available - max(0.0, minimum_health))
            take = min(left, available)
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
            0.0, min(self.ult_healing_deficit - given, sum(self.most.values()) - self.base_total),
        )
        return given

    def component(self, kinds) -> dict:
        """Component 51 with these pools' fields set (the others are left as they are)."""
        parts: list = [None] * (3 * POOLS)
        for kind in kinds:
            if kind in self.ids:
                parts[kind * POOLS] = world.pool(self.ids[kind], kind, self.most[kind], self.now[kind])
            if kind == SHIELDS_POOL:
                for pool in self.extra.values():
                    value = bytearray(world.pool(pool.pool_id, kind, pool.most, pool.now))
                    value[17] = 0  # host overhealth classification; native flag mapping remains open
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
    healing: bool = False  # ModifyHealth m_93CDFD2F: 0 heals, 1 damages (data-derived)
    line_of_sight: bool = False  # reconstructed area selection, explicit per supported effect
    effect: int | None = None  # ModifyHealth's authored hit-effect resource
    tags: tuple = ()  # authored ModifyHealth identifiers, including modifier exemptions
    health_operation: int | None = 1  # retained for the typed native absorption selector

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
        healing=modify.get("m_93CDFD2F") == 0,
        effect=effect.guid if isinstance(effect, expr.Asset) else None,
        tags=tuple(v.guid for t in modify.get("m_148F3152") or ()
                   if isinstance(v := state.evaluate(t.get("m_EE3055BD"), watch=False), expr.Asset)),
        health_operation=modify.get("m_93CDFD2F") if modify else None,
    )


# --- who can be hit -------------------------------------------------------------------------------------


def _source_entity(actor) -> int:
    """The combat actor's live entity ID."""
    return actor.entity if isinstance(actor, (bots.Bot, actors.Deployable)) else actor.body


@dataclass
class Target:
    entity: int
    team: int
    feet: tuple
    height: float
    radius: float
    eye: float
    owner: object  # a match.Player or a bots.Bot
    critical_allowed: bool = True
    capsule: tuple | None = None

    def segment(self, feet=None):
        """The capsule's segment (from the feet to the top, a radius in from both ends)."""
        x, y, z = feet or self.feet
        if self.capsule is not None:
            return tuple(tuple(a + b for a, b in zip((x, y, z), point, strict=True))
                         for point in self.capsule)
        return (x, y + self.radius, z), (x, y + max(self.radius, self.height - self.radius), z)


@dataclass
class Rocket:
    """A projectile in flight."""

    shooter: object
    team: int
    position: tuple
    direction: tuple
    speed: float
    left: float  # seconds remaining
    direct: HitDef | None
    blast: HitDef | None
    shot: object = None  # its projectiles.Shot: told when it ends
    flown: float = 0.0  # seconds in the air
    last_tick: int | None = None  # last world tick, or None before player input is simulated
    gravity: float = 0.0  # downward acceleration; only declared reconstructed motions use this
    deployment: object = None
    amplified: bool = False  # one reconstructed Matrix modifier latched by a weapon flight
    source_entity: int | None = None  # captured emission body for charge ownership
    definition: int | None = None  # original003 metadata, retained after ownership transfer
    deflections: int = 0
    distance_falloff: bool = False
    penetration_radius: float = 0.0
    penetrated: set[tuple[int, int | None]] | None = None
    range_left: float | None = None  # remaining path distance for selected Baptiste flights
    field_bounces: int = 0

    def __post_init__(self):
        if self.source_entity is None:
            self.source_entity = _source_entity(self.shooter)


class _HeavyTargetFilter(runtime.InstanceContext):
    def __init__(self, instance, target):
        super().__init__(instance, None)
        self.target = target

    def param(self, var):
        return expr.Entity(self.target.entity) if var == 8 and self.target is not None else super().param(var)

    def member(self, entity, var):
        if self.target is None or entity != self.target.entity:
            return super().member(entity, var)
        script = getattr(self.target.owner, "body_script", None)
        slot = script.component.entity_var(var, create=False) if script is not None else None
        return slot.value() if slot is not None else None


class _HeavyTargetContext(_HeavyTargetFilter):
    def __init__(self, instance, attacker, target=None):
        super().__init__(instance, target)
        self.attacker = attacker

    def position(self, entity: int):
        if entity == self.attacker.entity:
            return expr.Vec3(*self.attacker.position)
        return super().position(entity)

    def native(self, cfg):
        kind = cfg.get("$")
        if (kind in ("STU_C8C4C20D", "STU_F7FD94F9")
                and expr.evaluate(cfg.get("m_entity"), self) == expr.Entity(self.attacker.entity)):
            if kind == "STU_C8C4C20D":
                return self.attacker.yaw * 360.0 / 65536
            # Selected static size projection for the authored release expression.
            return mover_data(self.attacker.body).stand_radius
        if kind == "STU_021D01CD":
            owner = expr.evaluate(cfg.get("m_entity"), self)
            if owner == expr.Entity(self.attacker.entity):
                yaw = self.attacker.yaw
            elif self.target is not None and owner == expr.Entity(self.target.entity):
                yaw = self.target.owner.mover.yaw
            else:
                return super().native(cfg)
            return expr.Vec3(*deflection.basis(yaw, 0)[2])
        if (kind == "STU_AC829876" and self.target is not None
                and (cfg.get("m_filter") or {}).get("m_84065FD1") == 1):
            # This context is created only for a hostile player candidate.
            return expr.evaluate(cfg.get("m_entity"), self) == expr.Entity(self.target.entity)
        return super().native(cfg)


class _HeavyMotionContext(runtime.InstanceContext):
    def __init__(self, instance, forward):
        super().__init__(instance, None)
        self.forward = expr.Vec3(*forward)

    def var(self, scope, var):
        value = super().var(scope, var)
        if scope == runtime.INSTANCE_SCOPE and var == 3192 and (
            not isinstance(value, expr.Vec3) or not math.hypot(*value)
        ):
            return self.forward
        return value


class Combat:
    """The match's health, hits, deaths and respawns."""

    def __init__(self, match) -> None:
        self.match = match
        self.vitals: dict[int, Vitals] = {}  # body or bot entity -> its health
        self.history: dict[int, deque] = {}  # entity -> (tick, feet, height, eye) of the last HISTORY ticks
        self.replacement_ticks: dict[int, int] = {}  # replacement body -> authoritative world tick
        self.rockets: list[Rocket] = []
        self.shatters: dict[int, earthshatter.Wave] = {}
        self.charges: dict[int, reinhardt_charge.Charge] = {}
        self.respawns: list[tuple[float, object]] = []  # (when, a player or a bot)
        self.next_bot = bots.BOT_ENTITY + len(match.bots)
        self.now = 0.0
        self.tick = 0
        self.melee_seen: dict[int, dict[tuple[int, int], int]] = {}  # body -> counters by instance/state
        self.stuns: dict[int, int] = {}  # body -> exclusive world-tick deadline
        self.projectiles = projectiles.Projectiles(self)  # leases, confirmations, the Biotic Field
        self.observers = observers.Observers(match)  # the bodies' statescript for the other clients
        self.voice = voicelines.VoiceLines(match)  # the hero's spawn voice line (voicelines.py)
        self.baptiste = baptiste.BaptisteEffects(self)
        self.healing = healing.HealingEffects(self)
        self.projectile_tags = deflection.catalog()
        self.absorption = absorption.Grasp(self)
        self.barrier = barrier.BarrierField(self)
        self.deployables: dict[int, actors.Deployable] = {}

    def allocate_combat_entity(self):
        entity = self.next_bot
        if (not bots.BOT_ENTITY <= entity < 0xD0000000 or entity in self.deployables
                or any(bot.entity == entity for bot in self.match.bots)):
            raise ValueError("deployable entity allocator collided or exhausted")
        self.next_bot += 1
        return entity

    def create_deployable(self, source, definition, kind, position, *, health, radius, height, seconds,
                          rotation=(0.0, 0.0, 0.0, 1.0), capsule=None):
        """Create a transient damage target without borrowing the NPC respawn policy."""
        entity = self.allocate_combat_entity()
        actor = actors.Deployable(
            entity, definition, kind, source.team, tuple(position), health, radius, height,
            source, source.body, self.tick, self.tick + math.ceil(seconds / TICK),
        )
        from ow174.game.match import TEAM_BIT

        actor.filter_bits = getattr(source, "team_bit", TEAM_BIT << actor.team)
        actor.rotation = rotation
        actor.capsule = capsule
        self.deployables[entity] = actor
        self.replacement_ticks[entity] = self.tick
        if health > 0:
            self.vitals[entity] = Vitals(entity, health)
        for viewer in self.match.players:
            if viewer.spawned and viewer.client is not None:
                viewer.client.queue_entities([self.deployable_create(actor)])
        return actor

    def deployable_create(self, actor):
        vitals = self.vitals.get(actor.entity)
        components = vitals.component(vitals.ids) if vitals is not None else {}
        components[26] = [actor.filter_bits]
        position = actor.position
        return world.EntityUpdate(
            actor.entity, world.OP_CREATE,
            lambda origin: world.create(origin, actor.definition, components,
                                        flags=world.WITH_TRANSFORM | world.WITH_SCALE, position=position,
                                        rotation=actor.rotation),
        )

    def destroy_deployable(self, entity):
        actor = self.deployables.pop(entity, None)
        if actor is None:
            return
        self.baptiste.deployable_removed(entity)
        ice_wall.retired(actor)
        self.vitals.pop(entity, None)
        self.stuns.pop(entity, None)
        self.history.pop(entity, None)
        self.replacement_ticks.pop(entity, None)
        for viewer in self.match.players:
            if viewer.spawned and viewer.client is not None:
                viewer.client.queue_entities([world.EntityUpdate(entity, world.OP_DESTROY)])

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
        self.shatters.pop(player.body, None)
        self.charges.pop(player.body, None)
        for charge in self.charges.values():
            if charge.pinned == player.body:
                charge.pinned = None
        self.baptiste.retire_body(player.body)
        self.healing.retire(player.body)
        self.absorption.retire(player.body)
        self.barrier.retire(player.body)
        # The owner's leased projectiles and their observer copies retire with the old body.
        self.rockets[:] = [
            rocket for rocket in self.rockets if rocket.shooter is not player or rocket.shot is None
        ]
        self.respawns[:] = [(when, who) for when, who in self.respawns if who is not player]

    def record_replacement(self, body: int) -> None:
        """Keep the creation boundary independent of the rolling pose history."""
        self.replacement_ticks[body] = self.match.tick
        self.stuns.pop(body, None)

    def is_stunned(self, actor, *, tick=None):
        entity = _source_entity(actor)
        return (self.stuns.get(entity, 0) > (self.tick if tick is None else tick)
                or reinhardt_charge.pinned(self, entity))

    def _hit_effect(self, target, effect):
        """Reuse parent64bf71c's resource-backed control policy, not full224E execution."""
        if effect != FLASHBANG_EFFECT or self.vitals_of(target).dead:
            return
        variables = graph.mode_variables(self.match.game_map.mode_guid) or {}
        duration = 1.7 if expr.truthy(variables.get(FLASHBANG_MODE_FLAG)) else 0.8
        self.stun(target, duration)
        script = getattr(target.owner, "body_script", None)
        if isinstance(script, BotScript):
            script.suspend_flashbang_weapon()

    def stun(self, target, duration):
        deadline = self.tick + math.ceil(duration / TICK)
        self.stuns[target.entity] = max(deadline, self.stuns.get(target.entity, 0))
        self.interrupt(target)

    def apply_heavy_counter_stuns(self, pending: list[tuple[int, float]]) -> None:
        if not pending:
            return
        selected = dict(pending)
        pending.clear()
        for target in self._targets():
            if target.entity in selected and not self.vitals_of(target).dead:
                self.stun(target, selected[target.entity])

    def interrupt(self, target):
        script = getattr(target.owner, "body_script", None)
        if script is None:
            return
        reinhardt_charge.interrupt(self, target, script)
        if isinstance(script, BotScript):
            script.interrupt_attack()
        component = script.component
        component.edges([(value, False) for value in tuple(component.held)])
        for instance in list(component.instances.values()):
            if instance.graph.index == 0x19A5:
                # Selected host control policy: abort Burst's detached startup wait.
                # Its authored abort plug stops the ability without starting TrackTargets.
                startup = instance.states.get(instance.graph.node(14).state)
                if startup is not None and startup.active:
                    startup.request(runtime.ABORT)
            for state in list(instance.states.values()):
                if isinstance(state, nodes.Ability):
                    state.presses.clear()
                    state.stop()
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
            return
        if command is not None:
            if player.hero.guid == barrier.REINHARDT and player.body not in self.shatters:
                wave = earthshatter.start(self, player)
                if wave is not None:
                    self.shatters[player.body] = wave
            for state, number, time, activation in shots:
                try:
                    self._shoot(player, command, state, number, time, activation=activation)
                except Exception:
                    log.exception("[game] %s: %s's shot failed", self.match.label(), player.name)
        try:
            self._melee(player, command, script)
        except Exception:
            log.exception("[game] %s: %s's melee failed", self.match.label(), player.name)
        self.baptiste.command(player, command)

    def _melee(self, player, command, script) -> None:
        """Quick melee: 0043 st4 (StateModifyHealth) hits what its st5 TrackTargets finds: a sphere of
        m_radius 1.0 at (0, 0, 1.5) from the eye (DATA; unverified: along the aim), #476 = 30 damage (DATA),
        once per activation of st4. The server cannot run TrackTargets itself (the VM finds nothing)."""
        for instance in script.component.instances.values():
            if player.hero.guid == barrier.REINHARDT and instance.graph.index == 0x48:
                self._hammer(player, command, script, instance)
                continue
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
            blockers = self._ice_wall_blockers()
            view_tick = command.frame - command.view_delay(player.quanta) * 1e6 / player.quanta
            for target in self._enemies(player):
                shown = self.seen(target, view_tick)
                if shown is None:
                    continue
                a, b = shown.segment()
                other_pillars = (pillar for pillar in blockers if pillar.entity != target.entity)
                if (segment_distance(centre, a, b) - shown.radius <= radius and amount > 0.0
                        and capsule_visible(
                            self._collision(), eye, a, b, shown.radius, blockers=other_pillars
                        )):
                    if deflection.blocks_melee(player, target, state):
                        continue
                    self._damage(player, target, amount, False, loadout, command.frame)

    def _hammer(self, player, command, script, instance):
        """Selected 0048 swing. Sample the authored capsule once per hit activation."""
        counters = self.melee_seen.setdefault(script.entity, {})
        for index, query_position in ((3, 5), (4, 7)):
            state = instance.states.get(index)
            if state is None:
                continue
            key = (instance.id, index)
            seen = counters.get(key, 0)
            counters[key] = state.counter
            if state.counter == seen or not state.active or command is None:
                continue
            query = instance.graph.node(query_position)
            axes = deflection.basis(command.aim()[0], 0)

            def endpoint(field):
                offset = state.evaluate(query.field(field), watch=False)
                return tuple(value + sum(coordinate * axis[i] for coordinate, axis in zip(offset, axes))
                             for i, value in enumerate(player.mover.position))

            start, end = endpoint("m_05BE62DD"), endpoint("m_B3A77CD0")
            radius = _number(state.evaluate(query.field("m_radius"), watch=False), 0.0)
            modify = state.node.field("m_modifyHealth")
            amount = _number(state.evaluate(modify["m_amount"], watch=False), 0.0)
            loadout = state.evaluate(modify["m_loadout"], watch=False)
            loadout = loadout.guid if isinstance(loadout, expr.Asset) else None
            eye = self._eye(player)
            view_tick = command.frame - command.view_delay(player.quanta) * 1e6 / player.quanta
            for target in self._enemies(player):
                shown = self.seen(target, view_tick)
                if shown is None:
                    continue
                a, b = shown.segment()
                if (math.dist(*closest_segments(start, end, a, b)) <= radius + shown.radius
                        and capsule_visible(self._collision(), eye, a, b, shown.radius)
                        and not deflection.blocks_melee(player, target, state)):
                    self._damage(player, target, amount, False, loadout, command.frame)

    def update(self, now: float, tick: int, frames: bool = True) -> None:
        """Each tick: keep where the bodies are, fly the rockets, respawn, send health that changed. The
        other players' body frames only on the ticks that send (`frames`)."""
        if self.match.collision_world is None:
            return
        retained = max(self.tick + 1, tick - COMBAT_CATCH_UP + 1)
        first = tick
        if self.tick and tick > self.tick + 1 and (
            self.shatters or any(self._runs_bot_script(bot) for bot in self.match.bots)
        ):
            first = retained
        for target in self._targets():
            history = self.history.setdefault(target.entity, deque(maxlen=HISTORY))
            history.append((tick, target.feet, target.height, target.eye))
        # Replay the retained combat frames using the most recent poses. Player input
        # accepted just before the scheduler caught up joins only at the endpoint.
        for at in range(first, tick + 1):
            self.now, self.tick = now - (tick - at) * TICK, at
            reinhardt_charge.sync(self)
            for rocket in list(self.rockets):
                if at == tick or rocket.last_tick is not None:
                    self._fly(rocket)
            for body, wave in list(self.shatters.items()):
                if earthshatter.advance(self, wave):
                    self.shatters.pop(body, None)
            self._bot_weapons(at)
            if at != tick and self.baptiste.fields:
                # Match the normal tick: attacks resolve before Field protection refreshes.
                self.baptiste.refresh_field_protection(at)
        self.projectiles.update(tick, first_tick=retained)
        for actor in list(self.deployables.values()):
            if tick >= actor.expires_tick or actor.owner.gone:
                self.destroy_deployable(actor.entity)
            elif isinstance(actor.body_script, actors.PillarScript):
                actor.body_script.component.run_frame(tick)
        self.healing.prepare()
        self.baptiste.update(tick)
        self.healing.prune()
        self.absorption.update()
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
        for actor in self.deployables.values():
            if actor.health > 0:
                found.append(self._deployable_target(actor))
        return found

    @staticmethod
    def _deployable_target(actor):
        return Target(actor.entity, actor.team, actor.position, actor.height, actor.radius,
                      actor.height * 0.5, actor, critical_allowed=False, capsule=actor.capsule)

    def _ice_wall_blockers(self):
        return tuple(self._deployable_target(actor) for actor in self.deployables.values()
                     if actor.definition == ice_wall.PILLAR and actor.health > 0)

    def area_visible(self, origin, centre):
        return capsule_visible(
            self._collision(), origin, centre, centre, 0., blockers=self._ice_wall_blockers(),
        )

    def _shot_targets(self, shooter, definition):
        targets = self._hit_targets(shooter, definition)
        pillars = self._ice_wall_blockers()
        if pillars:
            selected = {target.entity for target in targets}
            targets.extend(pillar for pillar in pillars if pillar.entity not in selected)
        return targets

    def vitals_of(self, target: Target) -> Vitals:
        found = self.vitals.get(target.entity)
        if found is None:
            if isinstance(target.owner, (bots.Bot, actors.Deployable)):
                found = Vitals(target.entity, target.owner.health)
            else:
                found = Vitals(target.entity, *pools.body_pools(target.owner.hero.guid))
            self.vitals[target.entity] = found
        return found

    def _enemies(self, shooter) -> list[Target]:
        free_for_all = self.match.game_map.free_for_all
        source = _source_entity(shooter)
        found = []
        for target in self._targets():
            if target.owner is shooter or target.entity == source:
                continue
            if isinstance(target.owner, actors.Deployable) and target.owner.owner is shooter:
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
        return Target(target.entity, target.team, feet, height, target.radius, eye, target.owner,
                      target.critical_allowed, target.capsule)

    def _hit_targets(self, shooter, definition: HitDef | None) -> list[Target]:
        """Direct healing selects living teammates, excluding the source and FFA opponents.

        This restores the basic health-operation polarity, not the authored filter service.
        Area self-healing remains explicit in heal_area.
        """
        if definition is None or not definition.healing:
            return self._enemies(shooter)
        if self.match.game_map.free_for_all:
            return []
        source = _source_entity(shooter)
        return [
            target for target in self._targets()
            if target.entity != source and target.owner is not shooter
            and not isinstance(target.owner, actors.Deployable)
            and target.team == shooter.team and not self.vitals_of(target).dead
        ]

    def _apply_hit(self, shooter, target, definition, amount, critical, frame, *, world_tick=None,
                   source_entity=None):
        if not definition.healing:
            if (isinstance(target.owner, actors.Deployable) and target.owner.definition == ice_wall.PILLAR
                    and (target.owner.owner is shooter or (not self.match.game_map.free_for_all
                                                          and shooter.team == target.team))):
                return
            self._damage(shooter, target, amount, critical, definition.loadout, frame,
                         world_tick=world_tick, source_entity=source_entity)
            self._hit_effect(target, definition.effect)
            return
        # Recheck affiliation/lifetime at impact: neither a stale target nor a projectile
        # in flight may heal a new enemy or resurrect a corpse.
        if not any(current.entity == target.entity and current.owner is target.owner
                   for current in self._hit_targets(shooter, definition)):
            return
        self._heal_target(shooter, target, amount, source_entity=source_entity, tags=definition.tags)

    def _heal_target(self, shooter, target, amount, *, source_entity=None, tags=(), seconds=0.0,
                     end_tick=None):
        vitals = self.vitals_of(target)
        if vitals.dead:
            return 0.0
        amount *= self.healing.factor(target, tags=tags, seconds=seconds, end_tick=end_tick)
        eligible = vitals.ult_healing_deficit
        given = vitals.heal(amount)
        if given > 0.0:
            if not isinstance(shooter, bots.Bot):
                shooter.stats.add(stats.HEALING, given)
            self.baptiste.healing_charge(shooter, target, min(given, eligible), source_body=source_entity)
            self._sync_body_health(target.owner, vitals)
        return given

    # --- shots -------------------------------------------------------------------------------------

    @staticmethod
    def _runs_bot_script(bot: bots.Bot) -> bool:
        script = bot.body_script
        return (not bot.dead and isinstance(script, BotScript)
                and (bot.firing or script.has_controller_buttons))

    def _bot_weapons(self, tick: int) -> None:
        """Advance combat NPC scripts and consume their volley reports."""
        for bot in self.match.bots:
            script = bot.body_script
            if not self._runs_bot_script(bot) or script.frame > tick:
                continue
            try:
                snapshot = script.advance_live(tick, bot.position)
                shots, script.component.shots[:] = list(script.component.shots), []
                if self.is_stunned(bot, tick=tick):
                    shots = []
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
        direct = hit_def(state, node.field("m_6396149F"))
        entity_def = (node.field("m_projectileEntity") or {}).get("m_entityDef")
        definition = state.evaluate(entity_def, watch=False) if entity_def is not None else None
        definition = definition.guid if isinstance(definition, expr.Asset) else None
        speed = field("m_projectileSpeed")
        if speed:
            lifetime = field("m_projectileLifetime", 5.0) or 5.0
            reach = field("m_FEC435C6")
            blast = hit_def(state, node.field("m_D2D11CE9"))
            fire_strike = (definition == 0x04000000000001EA
                           and state.instance.graph.index == 0x00E2 and node.pos == 3
                           and getattr(shooter.hero, "guid", None) == barrier.REINHARDT)
            penetration_radius = 0.0
            if fire_strike:
                hit_fields = node.field("m_6396149F") or {}
                penetration_radius = max(_number(state.evaluate(hit_fields.get(name), watch=False), 0.0)
                                         for name in ("m_57DAE1B5", "m_61CD1F5B"))
            gravity = 0.0
            deployment = self.baptiste.field_parameters(state)
            if deployment is not None:
                gravity = deployment.gravity
            grenade = self.baptiste.grenade_parameters(state)
            if grenade is not None:
                direction = baptiste.grenade_direction(state, direction)
                radius, amount, gravity = grenade
                blast = HitDef(amount, direct.loadout if direct else None,
                               rings=((radius, 1.0),), healing=True, line_of_sight=True,
                               tags=direct.tags if direct else (), health_operation=0)
            range_left = None
            if reach:
                if deployment is not None or grenade is not None:
                    range_left = reach
                else:
                    lifetime = min(lifetime, reach / speed)
            damaging = (direct is not None and direct.amount > 0.0) or (
                blast is not None and blast.amount > 0.0
            )
            enforcer = (npc and shooter.body == 0x0400000000002279
                        and state.instance.graph.index == 0x13EB and node.pos == 24)
            directions = (direction,)
            if enforcer:
                seed = weapon_spread.seed_base(shooter.entity, state.instance.id, node.pos, activation[0])
                directions = self._enforcer_pattern(state, direction, seed, number)
            for trajectory in directions:
                shot = None
                if definition is not None:
                    shot = self.projectiles.fired(
                        shooter, state, shot_frame, definition, lifetime, origin, trajectory,
                        activation=activation, snapshot=snapshot,
                    )
                if damaging or shot is not None or deployment is not None:
                    self.rockets.append(
                        Rocket(
                            shooter, shooter.team, origin, trajectory, speed, lifetime, direct, blast, shot,
                            last_tick=self.tick if npc else None, gravity=gravity, deployment=deployment,
                            definition=definition, distance_falloff=enforcer,
                            penetration_radius=penetration_radius, penetrated=set() if fire_strike else None,
                            range_left=range_left,
                        )
                    )
            return
        if direct is None or direct.amount <= 0.0:
            return
        reach = field("m_FEC435C6", RANGE) or RANGE
        if (not npc and state.instance.graph.index == 0x1932 and node.pos == 10
                and shooter.hero.guid == baptiste.HERO):
            direction = baptiste.primary_direction(state, direction, shooter.body, activation[0], number)
        # The input supplies this simulation frame's aim and view time. Notices use the volley clock.
        view_tick = self.tick
        if command is not None:
            view_tick = command.frame - command.view_delay(shooter.quanta) * 1e6 / shooter.quanta
        self._hitscan(shooter, origin, direction, direct, reach, view_tick, shot_frame,
                      definition=definition, world_tick=self.tick if npc else None)

    @staticmethod
    def _enforcer_pattern(state, forward, seed, shot):
        """Selected 13EB launch. Server identities supply the seed; aspect is one."""
        launch = state.node.field("m_5EC1EB38")
        if (not isinstance(launch, dict) or launch.get("$") != "STU_613BDDB0"
                or launch.get("m_391F15AF") is not None):
            raise ValueError("unexpected Enforcer launch profile")
        spread = expr.to_float(state.evaluate(launch["m_spread"], watch=False))
        jitter = expr.to_float(state.evaluate(launch["m_92308789"], watch=False))
        if not (0 <= spread <= 180 and 0 <= jitter <= 180):
            raise ValueError("invalid Enforcer spread")
        rings = []
        for ring in launch["m_0D30E40C"]:
            fraction = expr.to_float(state.evaluate(ring["m_6425FFB9"], watch=False))
            count = expr.to_int(state.evaluate(ring["m_B19BD35D"], watch=False))
            if not 0 <= fraction <= 1 or not 1 <= count <= 25:
                raise ValueError("invalid Enforcer ring")
            rings.append((fraction, count))
        authored_count = expr.to_int(state.evaluate(state.node.field("m_numProjectilesPerShot"), watch=False))
        if authored_count != 25 or sum(count for _, count in rings) != authored_count:
            raise ValueError("Enforcer ring counts do not match the authored volley")
        return weapon_spread.ring_directions(forward, rings, spread, jitter, seed, shot)

    def _deflector(self, shooter, definition, origin, direction, reach, *, view_tick=None, time_span=None,
                   radius=0.0):
        if not self.projectile_tags.admitted(definition):
            return None
        closest = None
        for target in self._enemies(shooter):
            owner = target.owner
            script = getattr(owner, 'body_script', None)
            hero = getattr(getattr(owner, 'hero', None), 'guid', None)
            if (hero not in (deflection.GENJI, absorption.SIGMA)
                    or script is None or script.retired or getattr(owner, 'mover', None) is None
                    or self.is_stunned(owner)):
                continue
            graph_index, state_index = (0x03A3, 10) if hero == deflection.GENJI else (0x1D02, 1)
            instance = next((i for i in script.component.instances.values()
                             if i.graph.index == graph_index), None)
            guard = instance.states.get(state_index) if instance else None
            if guard is None or not guard.active:
                continue
            shown = self.seen(target, view_tick) if view_tick is not None else target
            if shown is None:
                continue
            eye = (shown.feet[0], shown.feet[1] + shown.eye, shown.feet[2])
            found = deflection.intercept(guard, eye, owner.mover.yaw, owner.mover.pitch,
                                         origin, direction, reach, radius=radius)
            at_tick = self.tick
            if found is not None and time_span is not None:
                at_tick = time_span[0] + (time_span[1] - time_span[0]) * (found[0] / reach if reach else 0)
            window = deflection.window if hero == deflection.GENJI else absorption.window
            if (found is not None and window(instance, at_tick) is not None
                    and (closest is None or found[0] < closest[0])):
                closest = (found[0], target, found[1], hero == absorption.SIGMA, guard)
        return closest

    def _hitscan(self, shooter, origin, direction, direct, reach, view_tick, shot_frame, *,
                 definition=None, world_tick=None, travelled=0.0, amplified=False, reflections=0):
        best = None
        for target in self._shot_targets(shooter, direct):
            shown = self.seen(target, view_tick)
            if shown is None:
                continue
            a, b = shown.segment()
            t = ray_capsule(origin, direction, a, b, shown.radius)
            if t is not None and t <= reach and (best is None or t < best[0]):
                best = (t, target, shown)
        stop = best[0] if best is not None else reach
        wall = ray_world(self._collision(), origin, direction, stop)
        limit = min(stop, wall) if wall is not None else stop
        shield = None if direct.healing else self.barrier.intersect(shooter, origin, direction, limit)
        if shield is not None:
            limit = shield[0]
        returned = self._deflector(shooter, definition, origin, direction, limit, view_tick=view_tick)
        if returned is not None and returned[0] < limit - EPSILON:
            limit = returned[0]
        else:
            returned = None
        if not amplified:
            factor = self.baptiste.matrix_multiplier(shooter, origin, _along(origin, direction, limit))
            if factor > 1:
                direct = replace(direct, amount=direct.amount * factor)
                amplified = True
        if returned is not None:
            if returned[3]:
                self.absorption.absorbed(returned[4], direct, None, _source_entity(shooter))
                return
            if reflections >= deflection.MAX_DEFLECTIONS:
                return  # bounded cycle retirement must not turn an active guard into a damage bypass
            distance, defender, outgoing = returned[:3]
            return self._hitscan(
                defender.owner, _along(_along(origin, direction, distance), outgoing, 1e-6), outgoing,
                direct, reach - distance, self.tick, shot_frame, definition=definition,
                world_tick=self.tick, travelled=travelled + distance, amplified=amplified,
                reflections=reflections + 1,
            )
        if shield is not None:
            distance, entity, remaining = shield
            self.barrier.damage(entity, remaining, direct.amount * direct.scale(travelled + distance))
            return
        if best is None or (wall is not None and wall < best[0]):
            return
        t, target, shown = best
        hit_y = origin[1] + direction[1] * t
        critical = shown.critical_allowed and hit_y >= shown.feet[1] + shown.eye - HEAD_BELOW
        amount = direct.amount * direct.scale(travelled + t) * (direct.critical if critical else 1.0)
        self._apply_hit(
            shooter, target, direct, amount, critical and direct.critical > 1.0, shot_frame,
            world_tick=world_tick,
        )

    def _fly(self, rocket: Rocket) -> None:
        """Sweep elapsed world time against the current bodies and static collision."""
        # Player input can arrive immediately before the scheduler accounts for a gap.
        elapsed = 1 if rocket.last_tick is None else self.tick - rocket.last_tick
        if elapsed <= 0:
            return
        # Each legacy integration advances the pose through its labelled tick.
        # Keep that same contiguous interval when several ticks are swept at once.
        sweep_tick = self.tick - elapsed + 1
        rocket.last_tick = self.tick
        duration = elapsed * TICK
        if rocket.range_left is None:
            duration = min(duration, rocket.left)
        if min(duration, rocket.left) <= EPSILON:
            self._landed(rocket, release=rocket.range_left is None)
            return
        # Ballistic trajectories use bounded one-tick chords so skipped scheduler
        # frames do not turn an arc into one long line through nearby geometry.
        while duration > EPSILON and rocket in self.rockets:
            portion = min(duration, TICK) if rocket.gravity else duration
            self._sweep(rocket, portion, sweep_tick)
            duration -= portion
            sweep_tick += portion / TICK

    def _sweep(self, rocket: Rocket, duration: float, start_tick: float) -> None:
        direction = rocket.direction
        requested_duration = duration
        # Sweep the remaining flight even when the range or lifetime ends inside this tick.
        step = rocket.speed * duration
        velocity = None
        if rocket.gravity:
            vx, vy, vz = (value * rocket.speed for value in direction)
            # D71ACD updates vertical velocity before D70477 advances position.
            velocity = (vx, vy - rocket.gravity * duration, vz)
            displacement = tuple(value * duration for value in velocity)
            step = math.sqrt(sum(value * value for value in displacement))
            direction = tuple(value / step for value in displacement) if step > EPSILON else direction
        if rocket.range_left is not None:
            # D72C80 clips the proposed chord and interpolates its terminal velocity.
            fraction = min(1.0, rocket.left / duration)
            if step > 0.0:
                fraction = min(fraction, rocket.range_left / step)
            duration *= fraction
            step *= fraction
            if velocity is not None:
                velocity = (vx, vy - rocket.gravity * duration, vz)
        best = None
        targets = () if rocket.penetrated is not None else self._shot_targets(rocket.shooter, rocket.direct)
        if self.healing.accepts(rocket):
            targets = (t for t in self._targets() if t.owner is not rocket.shooter
                       and not self.vitals_of(t).dead)
        for target in targets:
            a, b = target.segment()
            t = ray_capsule(rocket.position, direction, a, b, target.radius)
            if t is not None and t <= step and (best is None or t < best[0]):
                best = (t, target)
        collision = self._collision()
        wall = collision.cast_ray(rocket.position, direction, step)
        if wall is not None and (best is None or wall.distance < best[0]):
            best = (wall.distance, None)
        field = rocket.deployment if isinstance(rocket.deployment, baptiste.FieldSpec) else None
        auxiliary = None
        if field is not None and isinstance(collision, World):
            radius = field.sweep_radius
            if radius > 0.0 and step > 0.0:
                auxiliary = collision.cast_sphere(rocket.position, direction, step, radius)
                # D76191 compares the auxiliary travel plus its radius with the primary hit.
                if auxiliary is not None and (best is None or auxiliary.distance + radius < best[0]):
                    wall = auxiliary
                    best = (wall.distance, None)
        limit = best[0] if best is not None else step
        shield = None
        helix = rocket.definition in HELIX_BARRIER_PROJECTILES
        if (rocket.penetrated is None and rocket.direct is not None
                and not rocket.direct.healing and rocket.direct.amount > 0
                and (rocket.blast is None or helix) and rocket.deployment is None):
            # The flight reaches its endpoint. An existing body or world hit
            # still owns an exact tie at the shortened limit.
            shield = self.barrier.intersect(
                rocket.shooter, rocket.position, direction, limit, include_endpoint=best is None,
            )
            if shield is not None:
                limit = shield[0]
        returned = self._deflector(
            rocket.shooter, rocket.definition, rocket.position, direction, limit,
            radius=rocket.penetration_radius,
            time_span=(start_tick, start_tick + duration / TICK
                       * (limit / step if step > EPSILON else 0)),
        )
        if returned is not None and returned[0] >= limit - EPSILON:
            returned = None
        crossing = returned[0] if returned is not None else limit
        tick_per_distance = duration / (TICK * step) if step > EPSILON else 0
        if rocket.penetrated is not None:
            self._fire_strike_contacts(
                rocket, direction, crossing, start_tick, tick_per_distance,
                stops=returned is not None or best is not None,
            )
        self._amplify_flight(rocket, direction, crossing, start_tick, tick_per_distance)
        if returned is not None:
            if returned[3]:
                self.absorption.absorbed(returned[4], rocket.direct, rocket.blast, rocket.source_entity)
                rocket.flown += duration * returned[0] / step if step > EPSILON else 0
                self.rockets.remove(rocket)
                if rocket.shot is not None:
                    self.projectiles.intercepted(rocket.shot, self.tick)
                return
            if rocket.deflections >= deflection.MAX_DEFLECTIONS:
                self.rockets.remove(rocket)
                if rocket.shot is not None:
                    self.projectiles.intercepted(rocket.shot, self.tick)
                return  # explicit host cycle bound, with no synthetic impact/splash
            distance, defender, outgoing = returned[:3]
            spent = duration * distance / step if step > EPSILON else 0
            rocket.position = _along(_along(rocket.position, direction, distance), outgoing, 1e-6)
            if rocket.gravity:
                rocket.speed = math.sqrt(vx * vx + (vy - rocket.gravity * spent) ** 2 + vz * vz)
            rocket.direction = outgoing
            rocket.flown += spent
            rocket.left = max(0.0, rocket.left - spent)
            if rocket.range_left is not None:
                rocket.range_left = max(0.0, rocket.range_left - distance)
            rocket.shooter, rocket.team, rocket.source_entity = defender.owner, defender.team, defender.entity
            rocket.deflections += 1
            if rocket.shot is not None:
                self.projectiles.intercepted(rocket.shot, self.tick)
                rocket.shot = None  # reflected client prediction/visual ownership is not implemented
            remaining = max(0.0, requested_duration - spent)
            if rocket.range_left is None:
                remaining = min(remaining, rocket.left)
            if remaining > EPSILON:
                self._sweep(rocket, remaining, start_tick + spent / TICK)
            return
        if shield is not None:
            distance, entity, remaining = shield
            rocket.flown += duration * distance / max(step, EPSILON)
            amount = rocket.direct.amount
            if rocket.distance_falloff:
                amount *= rocket.direct.scale(rocket.speed * rocket.flown)
            splash_hits = ()
            if helix and rocket.blast is not None:
                # Reconstructed: detonate on the incident side and select splash
                # recipients while the barrier still exists, even on a breaking hit.
                point = _along(rocket.position, direction, max(0.0, distance - 1e-4))
                splash_hits = tuple(
                    self._blast_hits(rocket, point, self._ice_wall_blockers(), shield_los=True)
                )
                amount += rocket.blast.amount * rocket.blast.splash(0.0)
            self.barrier.damage(entity, remaining, amount)
            self._landed(rocket)
            self._apply_blast_hits(rocket, splash_hits)
            return
        if best is None:
            rocket.position = _along(rocket.position, direction, step)
            if velocity is not None:
                rocket.speed = math.sqrt(sum(value * value for value in velocity))
                rocket.direction = (tuple(value / rocket.speed for value in velocity)
                                    if rocket.speed > EPSILON else (0.0, -1.0, 0.0))
            rocket.flown += duration
            rocket.left -= duration
            if rocket.range_left is not None:
                rocket.range_left = max(0.0, rocket.range_left - step)
                if rocket.left <= EPSILON or rocket.range_left <= EPSILON:
                    self._landed(rocket, release=False)
            elif rocket.left <= 0.0:
                self._landed(rocket)
            return
        t, struck = best
        spent = duration * t / max(step, EPSILON)
        rocket.flown += spent
        if struck is None and field is not None and field.bounce_limit is not None and wall.normal[1] <= 0:
            rocket.field_bounces += 1
            rocket.left = max(0.0, rocket.left - spent)
            if rocket.range_left is not None:
                rocket.range_left = max(0.0, rocket.range_left - t)
            if rocket.field_bounces > field.bounce_limit:
                self._landed(rocket, release=False)
                return
            # D7065D interpolates the velocity to contact before D77B80 reflects it.
            vx, vy, vz = (axis * rocket.speed for axis in rocket.direction)
            incoming = (vx, vy - rocket.gravity * spent, vz)
            normal = wall.normal
            dot = sum(a * b for a, b in zip(incoming, normal, strict=True))
            outgoing = tuple(-field.bounce_scale * dot * axis for axis in normal) if dot < 0 else incoming
            rocket.speed = math.hypot(*outgoing)
            if rocket.speed > EPSILON:
                rocket.direction = tuple(axis / rocket.speed for axis in outgoing)
            centre = _along(rocket.position, direction, t)
            impact_distance = field.sweep_radius if wall is auxiliary else 0.0
            correction = max(0.001, impact_distance - math.dist(centre, wall.point) - 0.0025)
            rocket.position = _along(centre, normal, correction)
            if rocket.left <= EPSILON or (rocket.range_left is not None and rocket.range_left <= EPSILON):
                self._landed(rocket, release=False)
            elif requested_duration - spent > EPSILON:
                self._sweep(rocket, requested_duration - spent, start_tick + spent / TICK)
            return
        self._landed(rocket)
        point = _along(rocket.position, direction, t)
        if struck is None:
            from ow174.game.static_ray import MIN_SURFACE_DISTANCE

            # Host policy: offset along the contact normal beyond the ray's positive
            # distance cutoff. Backing up by that cutoff along travel can leave an
            # oblique impact below it, hiding the wall from outgoing area LOS rays.
            point = _along(wall.point, wall.normal, 2 * MIN_SURFACE_DISTANCE)
        elif isinstance(struck.owner, actors.Deployable) and struck.owner.definition == ice_wall.PILLAR:
            # Keep area rays on the incident side of the selected pillar capsule.
            point = _along(point, direction, -1e-4)
        blockers = self._ice_wall_blockers()
        if self.healing.grenade(rocket, point, blockers=blockers):
            return
        if struck is not None and rocket.direct is not None and rocket.direct.amount > 0.0:
            amount = rocket.direct.amount
            if rocket.distance_falloff:
                amount *= rocket.direct.scale(rocket.speed * rocket.flown)
            self._apply_hit(
                rocket.shooter, struck, rocket.direct, amount, False, self.tick,
                world_tick=self.tick, source_entity=rocket.source_entity,
            )
        if rocket.deployment is not None:
            self.baptiste.projectile_impact(rocket, point)
        self._apply_blast_hits(rocket, self._blast_hits(rocket, point, blockers, shield_los=helix))

    def _blast_hits(self, rocket, point, blockers, *, shield_los):
        blast = rocket.blast
        if blast is None or blast.amount <= 0.0:
            return

        def mesh_blocks(origin, direction, reach):
            return self.barrier.intersect(rocket.shooter, origin, direction, reach) is not None

        for target in self._hit_targets(rocket.shooter, blast):
            a, b = target.segment()
            distance = max(0.0, segment_distance(point, a, b) - target.radius)
            scalar = blast.splash(distance)
            if scalar > 0.0 and (not blast.healing or blast.line_of_sight):
                other_pillars = (pillar for pillar in blockers if pillar.entity != target.entity)
                collision = None if blast.healing else self._collision()
                if not capsule_visible(
                    collision, point, a, b, target.radius, blockers=other_pillars,
                    mesh_blocks=mesh_blocks if shield_los else None,
                ):
                    continue
            if scalar > 0.0 and blast.line_of_sight:
                centre = _along(a, _sub(b, a), 0.5)
                delta = _sub(centre, point)
                length = math.sqrt(_dot(delta, delta))
                direction = tuple(value / length for value in delta) if length > EPSILON else (0, 0, 0)
                wall = ray_world(self._collision(), point, direction, length)
                if wall is not None and wall < length:
                    continue
            if scalar > 0.0:
                yield target, blast.amount * scalar

    def _apply_blast_hits(self, rocket, hits):
        for target, amount in hits:
            self._apply_hit(
                rocket.shooter, target, rocket.blast, amount, False, self.tick,
                world_tick=self.tick, source_entity=rocket.source_entity,
            )

    def _amplify_flight(self, rocket, direction, distance, start_tick, tick_per_distance):
        if rocket.amplified or not any(h is not None and h.amount > 0 for h in (rocket.direct, rocket.blast)):
            return
        factor = self.baptiste.matrix_multiplier(
            rocket.shooter, rocket.position, _along(rocket.position, direction, distance),
            team=rocket.team, time_span=(start_tick, start_tick + distance * tick_per_distance),
        )
        if factor > 1:
            rocket.amplified = True
            if rocket.direct is not None:
                rocket.direct = replace(rocket.direct, amount=rocket.direct.amount * factor)
            if rocket.blast is not None:
                rocket.blast = replace(rocket.blast, amount=rocket.blast.amount * factor)

    def _fire_strike_contacts(self, rocket, direction, limit, start_tick, tick_per_distance, *, stops):
        """Reconstructed capsule contacts and mesh crossings in travel order."""
        contacts = []
        meshes = self.barrier.intersections(rocket.shooter, rocket.position, direction, limit)
        for distance, entity, health in meshes:
            key = (entity, self.barrier.children[entity])
            if key not in rocket.penetrated:
                contacts.append((distance, key, None, health))
        for target in self._enemies(rocket.shooter):
            key = (target.entity, self.replacement_ticks.get(target.entity))
            if key in rocket.penetrated:
                continue
            a, b = target.segment()
            radius = target.radius + rocket.penetration_radius
            distance = ray_capsule(rocket.position, direction, a, b, radius)
            if distance is not None and (distance < limit or (distance == limit and not stops)):
                contacts.append((distance, key, target, None))
        for distance, key, target, health in sorted(contacts, key=lambda row: row[0]):
            self._amplify_flight(rocket, direction, distance, start_tick, tick_per_distance)
            rocket.penetrated.add(key)
            if target is None:
                self.barrier.damage(key[0], health, rocket.direct.amount)
            else:
                self._apply_hit(
                    rocket.shooter, target, rocket.direct, rocket.direct.amount, False, self.tick,
                    world_tick=self.tick, source_entity=rocket.source_entity,
                )

    def _landed(self, rocket: Rocket, *, release: bool = True) -> None:
        """Finish authority and release or retire the predicted copy."""
        if rocket in self.rockets:
            self.rockets.remove(rocket)
        if rocket.shot is not None:
            if release:
                self.projectiles.flown(rocket.shot, rocket.flown)
            else:
                self.projectiles.intercepted(rocket.shot, self.tick)

    def heal_area(self, healer, centre, radius: float, amount: float) -> None:
        """Heal the healer and his teammates (everyone's own team in a free-for-all is his alone) whose body
        is within `radius` of `centre` (unverified: to the body's capsule)."""
        for target in self._targets():
            if isinstance(target.owner, actors.Deployable):
                continue
            ally = not self.match.game_map.free_for_all and target.team == healer.team
            if target.owner is not healer and not ally:
                continue
            vitals = self.vitals_of(target)
            if vitals.dead:
                continue
            a, b = target.segment()
            if segment_distance(centre, a, b) - target.radius > radius:
                continue
            self._heal_target(healer, target, amount)

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

    def _minimum_health(self, target: Target, vitals: Vitals) -> float:
        """Consume actual active1763 health-floor State descriptors.

        This is a reconstructed combat adapter, not field membership/deployment.
        The originating script's lifetime owns removal of its protection.
        """
        script = getattr(target.owner, "body_script", None)
        if script is None or not isinstance(getattr(script, "component", None), runtime.Component):
            return 0.0
        floor = 0.0
        for instance in script.component.instances.values():
            for state in instance.states.values():
                if not state.active or state.node.cls != "STU_61FE0F01":
                    continue
                if not self.baptiste.protection_valid(target, instance.id):
                    continue
                if state.evaluate(state.node.field("m_target"), watch=False) != expr.Entity(target.entity):
                    continue
                scalar = _number(state.evaluate(state.node.field("m_9516110B"), watch=False), 0.0)
                if math.isfinite(scalar) and 0.0 < scalar <= 1.0:
                    floor = max(floor, expr.f32(scalar * vitals.maximum))
        return min(floor, vitals.most[HEALTH_POOL])

    def _assassin_clear_ray(self, attacker, target: Target) -> bool:
        x, y, z = attacker.position
        origin = (x, y + eye_height(attacker.body), z)
        a, b = target.segment()
        return capsule_visible(self._collision(), origin, a, b, target.radius)

    def assassin_candidates(self, attacker_entity: int, reach: float) -> tuple[expr.Entity, ...]:
        attacker = next((bot for bot in self.match.bots if bot.entity == attacker_entity), None)
        if attacker is None or attacker.dead:
            return ()
        candidates = []
        for target in self._enemies(attacker):
            if isinstance(target.owner, actors.Deployable):
                continue
            offset = _sub(target.feet, attacker.position)
            distance_squared = _dot(offset, offset)
            if distance_squared <= reach * reach:
                candidates.append((distance_squared, target.entity))
        return tuple(expr.Entity(entity) for _, entity in sorted(candidates))

    def assassin_visible(self, attacker_entity: int, target_entity: int) -> bool:
        attacker = next((bot for bot in self.match.bots if bot.entity == attacker_entity), None)
        if attacker is None or attacker.dead:
            return False
        target = next((target for target in self._enemies(attacker) if target.entity == target_entity), None)
        return target is not None and self._assassin_clear_ray(attacker, target)

    def scripted_body_var(self, entity: int, var: int):
        target = next((target for target in self._targets() if target.entity == entity), None)
        script = getattr(target.owner, "body_script", None) if target is not None else None
        if script is None:
            return None
        slot = script.component.entity_var(var, create=False)
        return slot.value() if slot is not None else None

    def assassin_capture_sender(self, instance: runtime.Instance) -> int | None:
        # Scope the host bridge to the sender authored for this victim graph.
        if instance.graph.index != 0x1177:
            return None
        source = instance.source_entity()
        bot = next((bot for bot in self.match.bots if bot.entity == source and not bot.dead), None)
        if bot is None or bot.body_script is None:
            return None
        victim = expr.Entity(instance.component.entity)
        for attack in bot.body_script.component.instances.values():
            if attack.graph.index == 0x1157:
                sender = attack.states.get(attack.graph.node(18).state)
                target = attack.find_var(runtime.INSTANCE_SCOPE, 536)
                if sender is not None and sender.active and target is not None and target.value() == victim:
                    return source
        return None

    def assassin_play_capture(self, instance: runtime.Instance, node: graph.Node) -> bool:
        if (instance.graph.index != 0x1157 or node.pos != 45
                or instance.evaluate(node.field("m_script")) != expr.Asset(0x0580000000001177)):
            return False
        victim = instance.evaluate(node.field("m_targets"))
        source = instance.component.entity
        if not isinstance(victim, expr.Entity) or not self.assassin_visible(source, victim.id):
            return True
        player = next((p for p in self.match.players if p.body == victim.id), None)
        if player is None or player.hero.guid != baptiste.HERO or player.body_script is None:
            return True
        definition = graph.graph(0x1177)
        if definition is None:
            raise ValueError("selected Assassin capture requires 1177")
        player.body_script.component.play_loaded_graph(definition, source=source, instigator=source)
        return True

    def heavy_capture_target(self, state) -> None:
        attacker = self._npc_melee_attacker(state)
        victim = state.evaluate(state.node.field("m_targets"), watch=False)
        resource = state.evaluate(state.node.field("m_script"), watch=False)
        if (attacker is None or not isinstance(victim, expr.Entity)
                or resource != expr.Asset(0x0580000000001407)):
            return
        target = next((target for target in self._enemies(attacker) if target.entity == victim.id), None)
        if target is None or target.owner not in self.match.players:
            return
        player = target.owner
        if player.body_script is None:
            return
        definition = graph.graph(0x1407)
        if definition is None:
            raise ValueError("selected Heavy Assault capture requires 1407")
        component = player.body_script.component
        component.play_loaded_graph(
            definition, source=attacker.entity, instigator=attacker.entity,
        )

    def release_heavy_capture(self, source: int) -> None:
        """Retire the source-owned capture on its current victim body."""
        for player in self.match.players:
            script = player.body_script
            if script is None:
                continue
            component = script.component
            for instance in list(component.instances.values()):
                if instance.graph.index == 0x1407 and instance.source_entity() == source:
                    component.destroy(instance)

    def place_heavy_release_target(self, instance, node) -> None:
        source = instance.component.entity
        attacker = next((bot for bot in self.match.bots if bot.entity == source), None)
        if attacker is None or attacker.dead or self.is_stunned(attacker):
            return
        selected = instance.evaluate(node.field("m_target"))
        if not isinstance(selected, expr.Entity):
            return
        target = next((t for t in self._enemies(attacker) if t.entity == selected.id), None)
        if target is None or target.owner not in self.match.players:
            return
        context = _HeavyTargetContext(instance, attacker)
        destination = expr.evaluate(node.field("m_AF774F7D"), context)
        yaw = _number(expr.evaluate(node.field("m_E3779CD0"), context))
        if not isinstance(destination, expr.Vec3) or yaw is None or not math.isfinite(yaw):
            return
        mover = target.owner.mover
        position = tuple(destination)
        distance = math.dist(mover.position, position)
        if not math.isfinite(distance):
            return
        # Sweep the released capsule so the requested pose cannot cross solid cover.
        mover._move_to(TICK, position, distance / TICK + 1)
        mover.yaw = (round(yaw * 65536 / 360) + 32768) % 65536 - 32768

    def advance_heavy_capture(self, component) -> None:
        """Sweep the source and captured player, then feed contact into the authored transition."""
        instance = next((i for i in component.instances.values() if i.graph.index == 0x1408), None)
        if instance is None:
            return
        recovery = instance.states.get(instance.graph.node(10).state)
        if recovery is not None and recovery.active:
            gate = instance.graph.node(15)
            delay = expr.to_float(recovery.evaluate(gate.field("m_AA6E611E"), watch=False))
            # Reconstructed elapsed-state predicate, following the original phase-reset plug.
            if component.now - recovery.start >= delay * 1000:
                instance.follow(gate, "m_truePlug", recovery)
                component.run_now()
                # Drop the target while the impact flag still selects the no-release branch.
                instance.write((runtime.INSTANCE_SCOPE, 562), ())
                component.run_now()
                instance.write((runtime.INSTANCE_SCOPE, 559), False)
                component.run_now()
            return
        drive = instance.states.get(instance.graph.node(71).state)
        if drive is None:
            return
        attacker = self._npc_melee_attacker(drive)
        if attacker is None:
            return
        if not drive.active:
            if drive.ended_at == component.now:
                attacker.velocity = (0., 0., 0.)
            return
        contact = instance.states.get(instance.graph.node(9).state)
        if contact is None or not contact.active:
            return
        selected = instance.find_var(runtime.INSTANCE_SCOPE, 536, create=False)
        victim = selected.value() if selected is not None else None
        if not isinstance(victim, expr.Entity):
            return
        player = next((p for p in self.match.players if p.has_body and p.body == victim.id), None)
        script = player.body_script if player is not None else None
        if (script is None or self.is_dead(player) or not any(
            i.graph.index == 0x1407 and i.source_entity() == attacker.entity
            for i in script.component.instances.values()
        )):
            return
        row = next(r for r in drive.node.field("m_BC5E91CF") if r["$"] == "STU_09F9B1FE")
        context = _HeavyMotionContext(instance, deflection.basis(attacker.yaw, 0)[2])
        velocity = expr.evaluate(row["m_9B7A63EA"], context)
        if not isinstance(velocity, expr.Vec3) or not math.isfinite(speed := math.hypot(*velocity)) or speed <= 0:
            return
        start = attacker.position
        mover = Mover(start, world=self._collision(), data=mover_data(attacker.body))
        goal = _along(start, tuple(velocity), TICK)
        mover._move_to(TICK, goal, speed + 1)
        attacker.position = tuple(mover.position)
        travelled = _sub(attacker.position, start)
        attacker.velocity = tuple(axis / TICK for axis in travelled)
        attacker.moved_tick = component.frame
        # The static host preserves the contact offset and sweeps both capsules.
        # A lost stride larger than the contact tolerance stands in for the native sensor.
        victim_mover = player.mover
        victim_mover.use(world=self._collision())
        victim_goal = _along(victim_mover.position, travelled, 1.)
        victim_mover._move_to(TICK, victim_goal, speed + 1)
        if max(math.dist(attacker.position, goal), math.dist(victim_mover.position, victim_goal)) > .03:
            attacker.velocity = (0., 0., 0.)
            instance.write(expr.lvalue(contact.node.field("m_26DA7CF8")), True)
            drive.try_complete()
            component.run_now()
            return
        # The close trackers belong to the drive. Sample them before the separate body tracker.
        for pos in (85, 100, 134):
            tracker = instance.states.get(instance.graph.node(pos).state)
            if tracker is not None and tracker.active:
                component.world.track_targets(tracker)
        component.run_now()
        if not drive.active:
            attacker.velocity = (0., 0., 0.)

    def _npc_melee_attacker(self, state):
        component = state.instance.component
        attacker = next((bot for bot in self.match.bots if bot.entity == component.entity), None)
        if (component.owner_dead or attacker is None or attacker.dead
                or self.is_stunned(attacker, tick=component.frame)):
            return None
        return attacker

    def _npc_melee_candidates(self, attacker, state, query, *, eye=None):
        """Project an authored melee sphere onto current hostile capsules with solid-world LOS."""
        offset = state.evaluate(query.field("m_05BE62DD"), watch=False)
        radius = _number(state.evaluate(query.field("m_radius"), watch=False), 0.0)
        if not isinstance(offset, expr.Vec3) or radius <= 0.0:
            return
        right, up, forward = deflection.basis(attacker.yaw, 0)
        centre = tuple(value + offset.x * x + offset.y * y + offset.z * z
                       for value, x, y, z in zip(attacker.position, right, up, forward, strict=True))
        # Reconstructed hostile selection and solid-world LOS. Native filters and force are open.
        if eye is None:
            x, y, z = attacker.position
            eye = (x, y + EYE, z)
        for target in self._enemies(attacker):
            a, b = target.segment()
            if (segment_distance(centre, a, b) - target.radius <= radius
                    and capsule_visible(self._collision(), eye, a, b, target.radius)):
                yield target

    def _npc_melee_damage(self, attacker, state, targets, *, lateral_force=False) -> None:
        health = state.node.field("m_modifyHealth")
        if health is None or health.get("m_93CDFD2F") != 1:
            return
        amount = _number(state.evaluate(health.get("m_amount"), watch=False), 0.0)
        if amount <= 0.0 and not lateral_force:
            return
        frame = state.instance.component.frame
        loadout = state.evaluate(health.get("m_loadout"), watch=False)
        loadout = loadout.guid if isinstance(loadout, expr.Asset) else None
        for target in targets:
            if amount > 0.0:
                self._damage(attacker, target, amount, False, loadout, frame, world_tick=frame)
            if lateral_force and not self.vitals_of(target).dead:
                forward = deflection.basis(attacker.yaw, 0)[2]
                reinhardt_charge.lateral_impulse(attacker.position, target, state, forward)

    def heavy_melee(self, state) -> None:
        attacker = self._npc_melee_attacker(state)
        if attacker is not None:
            query = state.instance.graph.node(6)
            self._npc_melee_damage(attacker, state, self._npc_melee_candidates(attacker, state, query))

    def trooper_melee_target(self, state):
        """Nominate the nearest visible candidate for the authored single-target distance guard."""
        attacker = self._npc_melee_attacker(state)
        if attacker is None:
            return None
        return min(
            self._npc_melee_candidates(attacker, state, state.node),
            key=lambda target: (math.dist(target.feet, attacker.position), target.entity),
            default=None,
        )

    def heavy_cone_targets(self, state):
        """Select from the authored sphere and a reconstructed horizontal cone."""
        attacker = self._npc_melee_attacker(state)
        if attacker is None:
            return ()
        query = state.node
        context = _HeavyTargetContext(state.instance, attacker)
        eye = expr.evaluate(query.field("m_D59188D5"), context)
        width = _number(state.evaluate(query.field("m_A30B383E"), watch=False), 0.0)
        limit = state.evaluate(query.field("m_C39691D9"), watch=False)
        if width <= 0.0 or (limit is not None and (type(limit) is not int or limit <= 0)):
            return ()
        if query.pos == 96 and (not isinstance(eye, expr.Vec3) or limit is None):
            return ()
        forward = deflection.basis(attacker.yaw, 0)[2]
        threshold = math.cos(math.radians(width / 2))
        condition = query.field("m_filter")["m_condition"]
        candidates = []
        eye_position = tuple(eye) if isinstance(eye, expr.Vec3) else None
        for target in self._npc_melee_candidates(attacker, state, query, eye=eye_position):
            if target.owner not in self.match.players or target.owner.body_script is None:
                continue
            x, y, z = _sub(target.feet, attacker.position)
            if x * forward[0] + z * forward[2] < math.hypot(x, z) * threshold:
                continue
            context = _HeavyTargetContext(state.instance, attacker, target)
            if expr.truthy(expr.evaluate(condition, context)):
                candidates.append((x * x + y * y + z * z, target.entity))
        return tuple(expr.Entity(entity) for _, entity in sorted(candidates)[:limit])

    def heavy_filtered_targets(self, state):
        """Use the side-hit or contact sphere and evaluate its authored filter expression."""
        attacker = self._npc_melee_attacker(state)
        if attacker is None:
            return ()
        condition = state.node.field("m_filter")["m_condition"]
        return tuple(
            expr.Entity(target.entity)
            for target in self._npc_melee_candidates(attacker, state, state.node)
            if expr.truthy(expr.evaluate(condition, _HeavyTargetFilter(state.instance, target)))
        )

    def npc_selected_damage(self, state, *, lateral_force=False) -> None:
        attacker = self._npc_melee_attacker(state)
        selected = state.evaluate(state.node.field("m_FA3ECC43"), watch=False)
        if isinstance(selected, expr.Entity):
            selected = (selected,)
        if attacker is None or not isinstance(selected, tuple) or not selected:
            return
        targets = (target for target in self._enemies(attacker) if expr.Entity(target.entity) in selected)
        self._npc_melee_damage(attacker, state, targets, lateral_force=lateral_force)

    def assassin_damage(self, state, reach: float) -> None:
        """Apply the selected 1157 hit once on entry under the host damage policy."""
        instance = state.instance
        component = instance.component
        if component.owner_dead:
            return
        attacker = next((bot for bot in self.match.bots if bot.entity == component.entity), None)
        if attacker is None or attacker.dead or self.is_stunned(attacker, tick=component.frame):
            return
        health = state.node.field("m_modifyHealth")
        if health is None or health.get("m_93CDFD2F") != 1:
            return
        source = instance.evaluate(health.get("m_instigator"))
        victim = instance.evaluate(state.node.field("m_FA3ECC43"))
        if source != expr.Entity(attacker.entity) or not isinstance(victim, expr.Entity):
            return
        target = next((target for target in self._enemies(attacker) if target.entity == victim.id), None)
        amount = instance.evaluate(health.get("m_amount"))
        if target is None or type(amount) not in (int, float) or not math.isfinite(amount) or amount <= 0:
            return
        # Reconstructed 1158 distance policy: current body origins, checked per hit.
        offset = _sub(target.feet, attacker.position)
        if math.sqrt(_dot(offset, offset)) > reach + expr.EPSILON:
            return
        # Reconstructed static cover check at each hit from the current attacker pose.
        if not self._assassin_clear_ray(attacker, target):
            return
        self._damage(attacker, target, amount, False, None, component.frame, world_tick=component.frame)

    def _damage(
        self, shooter, target: Target, amount: float, critical: bool, loadout, frame: int,
        *, world_tick: int | None = None, source_entity: int | None = None,
    ) -> None:
        if (isinstance(target.owner, actors.Deployable)
                and self.deployables.get(target.entity) is not target.owner):
            return
        vitals = self.vitals_of(target)
        if vitals.dead or amount <= 0.0:
            return
        base_before = vitals.base_total
        dealt = vitals.damage(amount, self._minimum_health(target, vitals))
        if dealt <= 0.0:
            return
        if (target.owner is not shooter and not isinstance(target.owner, actors.Deployable)
                and (self.match.game_map.free_for_all or target.team != shooter.team)):
            vitals.ult_healing_deficit = min(
                sum(vitals.most.values()) - vitals.base_total,
                vitals.ult_healing_deficit + max(0.0, base_before - vitals.base_total),
            )
        self.baptiste.damage_charge(shooter, target, dealt, source_body=source_entity)
        killed = vitals.now[HEALTH_POOL] <= EPSILON
        if killed:
            vitals.now[HEALTH_POOL] = 0.0
            vitals.died_at = self.now
            vitals.death_tick = self.match.tick if world_tick is None else world_tick
            vitals.killer = _source_entity(shooter)
        if not isinstance(shooter, bots.Bot):
            flags = (CRITICAL if critical else 0) | (KILLING if killed else 0)
            self._tell_hit(shooter, target, dealt, flags, frame)
            if not isinstance(target.owner, actors.Deployable):
                shooter.stats.add(stats.HERO_DAMAGE, dealt)
        victim = target.owner
        if killed and isinstance(victim, actors.Deployable):
            self.destroy_deployable(victim.entity)
            return
        if killed and isinstance(victim, bots.Bot):
            # The NPC death script owns its terminal health change and death event ordering.
            self._bot_died(shooter, victim, critical, loadout, vitals.death_tick)
            return
        self._sync_body_health(victim, vitals)
        if killed:
            self._died(shooter, victim, critical, loadout)

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
                for instance in list(component.instances.values()):
                    if instance.graph.index == 0x1407:
                        component.destroy(instance)
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
