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
  m_EAD7F104 to m_157E5BC5 metres down to m_72A81154 (30 m, 50 m, 30 %), the legacy critical field
  interpretation for other heroes, splash scaled between the explosion rings (1 m: 1.0, 3 m: 0.5).
  Shields, then armour (5 less a hit, at most half:
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
    ana_grenade,
    bots,
    defenses,
    deflection,
    dva,
    graviton,
    hook,
    npc_context,
    observers,
    pools,
    projectiles,
    reinhardt_barrier,
    sigma_grasp,
    stats,
    voicelines,
    world,
    zarya_barriers,
)
from ow174.game.bits import BitWriter
from ow174.game.collision import MOVER, TEAM0, TEAM1, World
from ow174.game.commands import JUMP, Command
from ow174.game.mover import REPEAT, mover_data, statescript_mods
from ow174.game.script import entity_link, entity_queries, expr, graph, nodes, runtime
from ow174.game.script.bot import DIED, BotScript
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
#104319 FirstPerson: standing m_E29993A5, crouched m_816B830F.
EYE_STANDING = {0x04000000000002A3: 1.425, 0x0400000000000407: 1.74,
                0x040000000000040A: 1.32}
EYE_CROUCHED = {0x04000000000003CF: 1.085, 0x04000000000002A3: .9975,
                0x0400000000000407: 1.74, 0x040000000000040A: .8}
EYE_CROUCHED_DEFAULT = 1.15
HEAD_BELOW = 0.2  # m under the eye where the head starts (unverified)
HISTORY = 64  # ticks of body positions kept for the shots' view time (about 1 s; unverified: retail's limit)
GENJI = 0x02E0000000000029
COMBAT_CATCH_UP = 64  # at most about a second of retained combat frames per update
PROJECTILE_REFLECTION_LIMIT = 8
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
SLEEP_EFFECT = 0x04A000000000026F  # 0BDE/node51 -> 224E profile676B
SLEEP_DURATION = 5.0  # 0BDE/node51 overrides #215
SLEEP_DAMAGE_GRACE = 0.5  # 224E/node166
SLEEP_WAKE = 0.5  # 224E/node145 and final wake animation
FLASHBANG_MODE_FLAG = 13408  # 0228 duration: flag set1.7s, otherwise0.8s
ASSASSIN_PIN_LOCK = "assassin_pin"


def eye_height(body: int, crouched: bool = False) -> float:
    """Authored FirstPerson scalar; mapping it to combat eye is reconstructed."""
    if crouched:
        return EYE_CROUCHED.get(body, EYE_CROUCHED_DEFAULT)
    return EYE_STANDING.get(body, EYE)


# --- geometry -----------------------------------------------------------------------------------------


def aim(yaw: int, pitch: int) -> tuple[float, float, float]:
    """The unit vector a command looks along: forward is (sin yaw, 0, cos yaw), the pitch is positive
    looking down (commands.py)."""
    y = yaw * 2 * math.pi / 65536
    p = pitch * 2 * math.pi / 65536
    return (math.sin(y) * math.cos(p), -math.sin(p), math.cos(y) * math.cos(p))


class DvaImpulseContext(runtime.InstanceContext):
    """Resolve the impulse's pet selector for a directly controlled mech."""

    def __init__(self, instance):
        super().__init__(instance, None)

    def native(self, cfg: dict):
        result = super().native(cfg)
        if cfg.get("$") == entity_queries.POSSESSED and not expr.valid_entity(expr.to_entity(result)):
            linked = cfg.get("m_entity") or {}
            owner = linked.get("m_D63CBEC2") or {}
            if linked.get("$") == entity_link.CLASS and owner.get("$") == "STU_B5A0CAF0":
                # A directly selected 0407 is already the controller's body
                # for the ruleset lookup. Keep this substitution local.
                return expr.Entity(self.instance.component.entity)
        return result


class SelfDestructDistance(runtime.InstanceContext):
    """Evaluate the authored blast curve for a surface outside the body array."""

    def __init__(self, instance, distance):
        super().__init__(instance, None)
        self.distance = expr.f32(distance)

    def var(self, scope, var):
        if (scope, var) == (expr.INSTANCE, 680):
            return self.distance
        return super().var(scope, var)


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
    """Reconstructed melee policy: a static ray to the nearest capsule surface.

    Native hitbox and filter equivalence remain unverified.
    """
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

    def damage(self, amount: float, *, armour_scalar: float | None = None) -> float:
        """Take a hit from shields, then armour, then health (unverified, as OW1 did: what reaches armour
        while it has some is ARMOUR_BLOCK less, at most halved). Returns what the pools lost."""
        left = max(0.0, amount)
        taken = 0.0
        for tag in reversed(self.temporary_shields):
            used = self.decay_temporary_shields(tag, left)
            left -= used
            taken += used
        for kind in (SHIELDS_POOL, ARMOUR_POOL, HEALTH_POOL):
            if left <= 0.0:
                break
            if kind == ARMOUR_POOL and self.now[kind] > 0.0:
                left = (max(left - ARMOUR_BLOCK, left / 2) if armour_scalar is None
                        else left * armour_scalar)
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
    damage_tags: tuple[int, ...] = ()

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


def _damage_tags(modify) -> tuple[int, ...]:
    identifiers = (expr.guid_of((tag.get("m_EE3055BD") or {}).get("m_identifier"))
                   for tag in modify.get("m_148F3152") or () if tag)
    return tuple(identifier for identifier in identifiers if identifier)


def hit_def(state, cfg, *, critical_override=None) -> HitDef | None:
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
        critical=value("m_A872C70E", 1.0) if critical_override is None else critical_override,
        rings=tuple(sorted(rings)),
        effect=effect.guid if isinstance(effect, expr.Asset) else None,
        damage_tags=_damage_tags(modify),
    )


def _volley_directions(actor, state, direction, *, number=0, counter=0):
    """Resolve authored Fusion and Talon volley counts with a static muzzle pose."""
    hero = getattr(actor, "hero", None)
    hero = getattr(hero, "guid", hero)
    site = state.instance.graph.index, state.node.pos
    if hero == dva.HERO and site in ((dva.LIGHT_GUN, 10), (0x1128, 10)):
        from ow174.game import weapon_aim

        return (weapon_aim.normalize_direction(weapon_aim.quantized_direction(direction)),)
    if hero == 0x02E00000000001BA and site == (0x114F, 47):
        count = state.evaluate(state.node.field("m_numProjectilesPerShot"), watch=False)
        if count != 2:
            raise ValueError("unexpected Heavy Assault dual-muzzle volley count")
        return (direction,) * count
    if ((hero, site) != (dva.HERO, (dva.FUSION_CANNONS, 6))
            and (hero, site) != (npc_context.ENFORCER, (npc_context.WEAPON, 24))):
        return (direction,)
    cfg = state.node.field("m_5EC1EB38") or {}
    if cfg.get("$") != "STU_613BDDB0":
        raise ValueError("weapon volleys require their authored ring pattern")
    count = state.evaluate(state.node.field("m_numProjectilesPerShot"), watch=False)
    spread = state.evaluate(cfg.get("m_spread"), watch=False)
    rings = []
    for ring in cfg.get("m_0D30E40C") or ():
        radius = state.evaluate(ring.get("m_6425FFB9"), watch=False)
        size = state.evaluate(ring.get("m_B19BD35D"), watch=False)
        if (type(radius) not in (float, int) or not math.isfinite(radius) or radius < 0
                or type(size) is not int or size <= 0):
            raise ValueError("invalid weapon volley ring")
        rings.append((radius, size))
    if (type(count) is not int or not 1 <= count <= 256
            or type(spread) not in (float, int) or not math.isfinite(spread) or spread < 0
            or sum(size for _, size in rings) != count
            or any(spread * radius >= 90 for radius, _ in rings)):
        raise ValueError("invalid weapon volley count/spread")
    if hero == dva.HERO:
        from ow174.game import fusion_spread, weapon_aim

        jitter_field = cfg.get("m_92308789")
        jitter = state.evaluate(jitter_field, watch=False) if jitter_field is not None else 0
        phase_field = cfg.get("m_391F15AF")
        degrees = state.evaluate(phase_field, watch=False) if phase_field is not None else None
        if (type(jitter) not in (int, float) or not math.isfinite(jitter) or jitter < 0
                or (phase_field is not None and
                    (type(degrees) not in (int, float) or not math.isfinite(degrees)))):
            raise ValueError("invalid Fusion Cannon jitter or phase")
        phase = -1 if degrees is None else expr.f32(expr.f32(degrees) * expr.f32(math.pi / 180))
        if degrees is not None and phase < 0:
            phase = math.fmod(phase, math.tau) + math.tau
        # D74B68 takes the m_states index. The body and instance identities
        # remain host mappings for the native entity and script words.
        seed = fusion_spread.seed_base(_source_entity(actor), state.instance.id, state.index, counter)
        forward = weapon_aim.quantized_direction(direction)
        rays = fusion_spread.directions(forward, spread, jitter, phase, rings, seed, number)
        return tuple(weapon_aim.normalize_direction(ray) for ray in rays)
    # Enforcer still uses the existing tangent projection and zero ring phase.
    x, y, z = direction
    right = (z, 0.0, -x) if abs(y) < 0.99 else (0.0, -z, y)
    length = math.sqrt(sum(v * v for v in right))
    right = tuple(v / length for v in right)
    rx, ry, rz = right
    up = (ry * z - rz * y, rz * x - rx * z, rx * y - ry * x)
    rays = []
    for radius, size in rings:
        offset = math.tan(math.radians(spread * radius))
        for pellet in range(size):
            phase = 2 * math.pi * pellet / size
            horizontal, vertical = offset * math.cos(phase), offset * math.sin(phase)
            ray = tuple(d + horizontal * r + vertical * u
                        for d, r, u in zip(direction, right, up, strict=True))
            length = math.sqrt(sum(v * v for v in ray))
            rays.append(tuple(v / length for v in ray))
    return tuple(rays)


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
    definition: int | None = None  #003 target-tag input; unknown attacks cannot be classified
    target_limit: int = 1
    hit_entities: set[int] = field(default_factory=set)
    spawn: int | None = None
    gravity: float = 0.0
    source_entity: int | None = None
    collision_radius: float = 0.0


FROST_SPRAY = 0x04000000000008BE
# Defaults from 0B77/node 31 and 05E7 in the target build.
FROST_INITIAL = expr.f32(.2)
FROST_STEP = expr.f32(.0285)
FROST_GAP_MS = 1000
FREEZE_MS = 1300
# Source entity slots read by the extracted 0B77/05E7 settings path.
FROST_RATE, FROST_MINIMUM, FROST_DURATION, FROST_STACKING = 0x2FE6, 0x2FEA, 0x300D, 0x301F


def _frost_settings(shooter) -> tuple[float, float, int, bool]:
    component = getattr(getattr(shooter, "body_script", None), "component", None)

    def value(key, default):
        slot = component.entity_var(key, create=False) if component is not None else None
        configured = slot.value() if slot is not None else None
        return default if configured is None else configured

    def bounded(key, default, low, high):
        number = expr.to_float(value(key, default))
        return expr.f32(max(low, min(high, number))) if math.isfinite(number) else default

    minimum = bounded(FROST_MINIMUM, FROST_INITIAL, 0.0, 1.0)
    step = expr.f32(FROST_STEP * bounded(FROST_RATE, 1.0, 0.0, 5.0))
    duration = round(FREEZE_MS * bounded(FROST_DURATION, 1.0, .2, 5.0))
    return minimum, step, duration, expr.truthy(value(FROST_STACKING, False))


@dataclass
class Frost:
    sources: dict[int, tuple[float, int, bool]] = field(default_factory=dict)
    frozen_until: int = 0
    frozen_aim: tuple[int, int] = (0, 0)

    def progress(self, now: int) -> float:
        if self.frozen_until:
            return 1.0 if now < self.frozen_until else 0.0
        strongest = additive = 0.0
        for _, (value, at, stacking) in sorted(self.sources.items()):
            if now >= at + FROST_GAP_MS:
                continue
            if stacking:
                additive = expr.f32(additive + value)
            else:
                strongest = max(strongest, value)
        return min(1.0, expr.f32(strongest + additive))


@dataclass
class InputLock:
    effect: int | str
    start: int
    end: int | None
    aim: tuple[int, int] | None = None


class Combat:
    """The match's health, hits, deaths and respawns."""

    def __init__(self, match) -> None:
        self.match = match
        self.vitals: dict[int, Vitals] = {}  # body or bot entity -> its health
        self.history: dict[int, deque] = {}  # entity -> (tick, feet, height, eye) of the last HISTORY ticks
        self.replacement_ticks: dict[int, int] = {}  # body -> first targetable tick of this exposure
        self.beam_seen: dict[int, tuple[tuple[int, int, int], int]] = {}
        self.frost: dict[int, Frost] = {}
        self.rockets: list[Rocket] = []
        self.respawns: list[tuple[float, object]] = []  # (when, a player or a bot)
        self.next_bot = bots.BOT_ENTITY + len(match.bots)
        self.now = 0.0
        self.tick = 0
        self.melee_seen: dict[int, dict[tuple[int, int], int]] = {}  # body -> counters by instance/state
        # Body identity -> graph instance, activation counter, contacted bodies.
        self.booster_contacts: dict[int, tuple[int, int, set[int]]] = {}
        # Recipient entity -> independent half-open world-tick intervals.
        self.heal_modifiers: dict[int, dict[str, list[tuple[int, int]]]] = {}
        # Body -> (impact tick, exclusive deadline, damage-triggered wake).
        self.ana_sleeps: dict[int, tuple[int, int, bool]] = {}
        self.stuns: dict[int, int] = {}  # body identity -> exclusive world-tick deadline
        self.input_locks: dict[int, list[InputLock]] = {}
        self.pins: dict[int, int] = {}  # manual Assassin source body -> pinned victim body
        self.assassin_effects = {}  # victim controller -> independent 1177 host
        self.zarya_bubbles = zarya_barriers.Barriers(self)
        self.zarya_gravitons = graviton.Gravitons(self)
        self.hooks = hook.Hooks(self)
        self.grasp_grants: dict[int, tuple[int, int]] = {}
        self.grasp_shields: dict[int, sigma_grasp.ShieldDecay] = {}
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
        from ow174.game import body_entities

        body_entities.retire_source(self.match, player.entity, player.body)
        self.vitals.pop(player.body, None)
        self.history.pop(player.body, None)
        self.replacement_ticks.pop(player.body, None)
        self.melee_seen.pop(player.body, None)
        self.beam_seen.pop(player.body, None)
        self.booster_contacts.pop(player.body, None)
        self.stuns.pop(player.body, None)
        self.input_locks.pop(player.body, None)
        self.ana_sleeps.pop(player.body, None)
        self.frost.pop(player.body, None)
        self.heal_modifiers.pop(player.body, None)
        self.zarya_bubbles.retire(player.body)
        self.hooks.retire(player.body)
        self.zarya_gravitons.release(player)
        self.grasp_grants.pop(player.body, None)
        self.grasp_shields.pop(player.body, None)
        self.clear_pins(player.body)
        # Projectile damage belongs to the firing body even without an observer lease.
        self.rockets[:] = [rocket for rocket in self.rockets if rocket.shooter is not player]
        self.respawns[:] = [(when, who) for when, who in self.respawns if who is not player]

    def expose_body(self, body: int, tick: int) -> None:
        """Start a targetable interval, separate from the rolling pose history."""
        self.history.pop(body, None)
        self.input_locks.pop(body, None)
        self.replacement_ticks[body] = tick

    def record_replacement(self, body: int) -> None:
        """Keep the creation boundary independent of the rolling pose history."""
        self.expose_body(body, self.match.tick)
        self.stuns.pop(body, None)
        self.ana_sleeps.pop(body, None)
        self.frost.pop(body, None)
        self.hooks.retire(body)
        self.clear_pins(body)

    def is_stunned(self, actor, *, tick: int | None = None) -> bool:
        entity = _source_entity(actor)
        current = self.tick if tick is None else tick
        sleep = self.ana_sleeps.get(entity)
        frost = self.frost.get(entity)
        return (entity in self.pins.values() or self.hooks.controlled(entity)
                or self.stuns.get(entity, 0) > current
                or (frost is not None and frost.frozen_until > current * 16)
                or (sleep is not None and sleep[1] > current))

    def movement_mods(self, actor, active: dict, *, tick: int | None = None) -> dict:
        frost = self.frost.get(_source_entity(actor))
        at = (self.tick if tick is None else tick) * 16
        progress = frost.progress(at) if frost is not None else 0.0
        if progress > 0:
            active[(-1, 0, 0)] = (0, 1, max(.3, 1.0 - progress), 99.0)
        return active

    def record_input_lock(self, entity, effect, end, aim=None) -> None:
        history = self.input_locks.setdefault(entity, [])
        for lock in reversed(history):
            if lock.effect == effect and (lock.end is None or self.tick < lock.end):
                lock.end = end
                return
        history.append(InputLock(effect, self.tick, end, aim))

    def close_input_lock(self, entity, effect) -> None:
        for lock in reversed(self.input_locks.get(entity, ())):
            if lock.effect == effect and (lock.end is None or self.tick < lock.end):
                lock.end = self.tick
                break

    def cleanse_frost(self, entity) -> None:
        self.frost.pop(entity, None)
        self.close_input_lock(entity, FROST_SPRAY)

    def suppress_input(self, actor, command: Command, *, tick: int, active: bool) -> tuple[Command, bool]:
        """Return admitted input and whether combat reports are fully suppressed."""
        entity = _source_entity(actor)
        movement_locked = entity in self.zarya_gravitons.pulled
        aim = None
        if active:
            frost = self.frost.get(entity)
            if frost is not None and tick * 16 < frost.frozen_until:
                aim = frost.frozen_aim
        elif command.frame >= tick - MAX_GAP:
            # Command frames share the packet frame epoch. This gates input
            # only; pose effects still use the live world tick.
            locked = []
            if command.frame < tick:
                for lock in self.input_locks.get(entity, ()):
                    if lock.start <= command.frame and (lock.end is None or command.frame < lock.end):
                        if lock.effect == graviton.INPUT_LOCK:
                            movement_locked = True
                        else:
                            locked.append(lock)
            if not locked:
                if movement_locked:
                    buttons = JUMP | (graviton.BOOSTERS_BUTTON if isinstance(actor, bots.Bot) else 0)
                    if command.buttons & buttons or command.forward or command.right:
                        command = replace(command, buttons=command.buttons & ~buttons, forward=0, right=0)
                return command, False
            aim = next((lock.aim for lock in reversed(locked) if lock.aim is not None), None)
        # Frames older than the retained history also advance without input.
        if aim is not None:
            yaw, pitch = aim
            return replace(command, yaw=yaw, pitch=pitch, click_yaw=None, click_pitch=None,
                           buttons=0, action=0, forward=0, right=0), True
        return replace(command, buttons=0, action=0, forward=0, right=0), True

    def _apply_frost(self, shooter, target: Target) -> None:
        if self.vitals_of(target).dead:
            return
        script = getattr(target.owner, "body_script", None)
        if script is not None and any(
            (var := script.component.entity_var(key, create=False)) is not None and expr.truthy(var.value())
            for key in (6604, 8440, 9161)
        ):
            return
        now = self.tick * 16
        frost = self.frost.get(target.entity)
        if frost is None or not frost.progress(now):
            frost = self.frost[target.entity] = Frost()
        if frost.frozen_until:
            return
        frost.sources = {source: hit for source, hit in frost.sources.items() if now < hit[1] + FROST_GAP_MS}
        source = _source_entity(shooter)
        prior, _, _ = frost.sources.get(source, (0.0, now, False))
        minimum, step, duration, stacking = _frost_settings(shooter)
        progress = min(1.0, max(minimum, expr.f32(prior + step)))
        frost.sources[source] = progress, now, stacking
        if frost.progress(now) >= 1.0:
            frost.frozen_until = now + duration
            actor = target.owner
            mover = (actor.control_mover or actor) if isinstance(actor, bots.Bot) else actor.mover
            frost.frozen_aim = round(mover.yaw), round(mover.pitch)
            deadline = (frost.frozen_until + 15) // 16
            self.record_input_lock(target.entity, FROST_SPRAY, deadline, frost.frozen_aim)
            self._interrupt(target)

    def pin_target(self, source: int, target: Target) -> None:
        previous = self.pins.get(source)
        if previous == target.entity:
            return
        if previous is not None:
            self.clear_pin(source)
        self.pins[source] = target.entity
        self.record_input_lock(target.entity, ASSASSIN_PIN_LOCK, None)
        self._interrupt(target)

    def clear_pin(self, source: int) -> None:
        victim = self.pins.pop(source, None)
        if victim is not None and victim not in self.pins.values():
            self.close_input_lock(victim, ASSASSIN_PIN_LOCK)
        for controller, host in tuple(self.assassin_effects.items()):
            host.retire(source)
            if not host.component.instances:
                del self.assassin_effects[controller]

    def clear_pins(self, entity: int) -> None:
        self.clear_pin(entity)
        for source, victim in tuple(self.pins.items()):
            if entity == victim:
                self.clear_pin(source)

    def _hit_effect(self, target: Target, effect: int | None) -> None:
        """Resource-backed stun reconstruction; native immunity/presentation remain unverified."""
        if (effect not in (FLASHBANG_EFFECT, SLEEP_EFFECT) or self.vitals_of(target).dead
                or defenses.protection(target.owner) is not None):
            return
        if effect == SLEEP_EFFECT:
            deadline = self.tick + math.ceil(SLEEP_DURATION / TICK)
            self.ana_sleeps[target.entity] = (self.tick, deadline, False)
        else:
            variables = graph.mode_variables(self.match.game_map.mode_guid) or {}
            duration = 1.7 if expr.truthy(variables.get(FLASHBANG_MODE_FLAG)) else 0.8
            deadline = self.tick + math.ceil(duration / TICK)
            deadline = max(deadline, self.stuns.get(target.entity, 0))
            self.stuns[target.entity] = deadline
        self.record_input_lock(target.entity, effect, deadline)
        self._interrupt(target)

    def _interrupt(self, target: Target) -> None:
        """Run existing release/stop semantics for a stun or reconstructed pin."""
        self.hooks.cancel_owner(target.entity)
        actor = target.owner
        mover = actor.control_mover if isinstance(actor, bots.Bot) else actor.mover
        if mover is not None and (last := mover.last_command) is not None:
            # A later command gap must not replay input held before forced control.
            mover.last_command = Command(last.frame, yaw=last.yaw, pitch=last.pitch)
        script = getattr(target.owner, "body_script", None)
        if script is None:
            return
        component = script.component
        if isinstance(target.owner, bots.Bot):
            from ow174.game import assassin, npc_charge

            if target.owner.spawner.get("manual_charge") is True:
                npc_charge.stop(target.owner)
            assassin.bind(component.world, component, None)
        # Remember live Matrix volumes before releasing input, since the
        # release can close the box and start its cooldown on its own.
        matrices = dict(deflection.matrix_abilities(script))
        impact = component.now
        if matrices and isinstance(actor, bots.Bot) and actor.control_mover is not None:
            impact = max(impact, (self.tick + actor.clock_offset) * component.frame_ms - 1)
        settled = {}
        if impact > component.now:
            for ability in matrices:
                natural = deflection.matrix_close_before(ability.instance, impact)
                if natural is None and impact - component.now > 2 * REPEAT * component.frame_ms:
                    continue
                close = impact if natural is None else natural
                deflection.settle_matrix_drain(ability, close, natural=natural is not None)
                settled[ability] = close
        component.edges([(value, False) for value in tuple(component.held)])
        # Run authored release paths before force-stopping remaining channels,
        # so active release handlers can start their own cooldowns.
        component.run_now()
        for instance in list(component.instances.values()):
            for state in list(instance.states.values()):
                if isinstance(state, nodes.Ability):
                    state.presses.clear()
                    if ((volume := matrices.get(state)) is not None and volume.active
                            and state.remaining() == 0):
                        # Forced control can end the live box before the release
                        # branch exists. Reuse the authored cooldown-and-stop path.
                        instance.run_node(instance.graph.node(97), None)
                    else:
                        state.stop()
        component.run_now()
        if impact > component.now:
            for ability in matrices:
                close = settled.get(ability, impact)
                deflection.anchor_matrix_close(ability, close, recharge=ability in settled)
        component.shots.clear()
        if not isinstance(target.owner, bots.Bot):
            # End forced-throttle/typed movement rows before the next pose;
            # otherwise an interrupted Boosters row can move one extra frame.
            active = self.movement_mods(target.owner, statescript_mods(component))
            target.owner.mover.track_mods(active, script.frame)

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
        if player.hero.guid == zarya_barriers.HERO:
            self.zarya_bubbles.sync(player)
        shots, script.component.shots[:] = list(script.component.shots), []
        if self.is_dead(player) or self.is_stunned(player):
            return
        if command is not None:
            self.hooks.command(player, command)
            for state, number, time, activation in shots:
                try:
                    self._shoot(player, command, state, number, time, activation=activation)
                except Exception:
                    log.exception("[game] %s: %s's shot failed", self.match.label(), player.name)
            if player.hero.guid == zarya_barriers.HERO:
                try:
                    self._zarya_beam(player, command, script)
                except Exception:
                    log.exception("[game] %s: %s's beam failed", self.match.label(), player.name)
        try:
            self._melee(player, command, script, player.quanta)
        except Exception:
            log.exception("[game] %s: %s's melee failed", self.match.label(), player.name)
        if command is not None:
            self._booster_contact(player, command, script)

    def _zarya_beam(self, player, command, script) -> None:
        """Consume the authored 0241 damage state without projectile interception."""
        instance = next((i for i in script.component.instances.values() if i.graph.index == 0x0241), None)
        state = instance.states.get(10) if instance is not None else None
        if state is None or not state.active or state.ending:
            return
        frame_ms = script.component.frame_ms
        now = command.frame * frame_ms
        activation = instance.id, state.counter, state.start
        previous = self.beam_seen.get(player.body)
        since = previous[1] if previous is not None and previous[0] == activation else state.start
        if now <= since:
            return
        self.beam_seen[player.body] = activation, now
        # A posed command spends only its own interval. Unposed time cannot hit.
        direct = hit_def(state, {"m_modifyHealth": state.node.field("m_modifyHealth")})
        if direct is None or direct.amount <= 0:
            return
        amount = direct.amount * min(now - since, frame_ms) / 1000
        ray = instance.graph.node(81)
        reach = _number(state.evaluate(ray.field("m_F5732403.m_6A79BD98"), watch=False), 0)
        radius = _number(state.evaluate(ray.field("m_57DAE1B5"), watch=False), 0)
        origin, direction = self._eye(player), aim(*command.aim())
        view_tick = command.frame - command.view_delay(player.quanta) * 1e6 / player.quanta
        best = None
        limit = reach
        for target in self._enemies(player):
            shown = self.seen(target, view_tick)
            if shown is None:
                continue
            distance = ray_capsule(origin, direction, *shown.segment(), shown.radius + radius)
            if distance is not None and distance <= limit:
                best, limit = target, distance
        shield = self._shield_hit(player, origin, direction, limit, radius=radius)
        wall = ray_world(self._collision(), origin, direction, limit)
        if shield is not None and (wall is None or shield[0] < wall):
            self._shield_damage(shield[1], amount, player)
        elif best is not None and wall is None:
            # Blizzard's 2020-08-31 retail rules reduce beam damage to armour by 30%.
            self._damage(player, best, amount, False, direct.loadout, command.frame, armour_scalar=.7)

    def stop_boosters(self, mech) -> None:
        """End Boosters through its authored cleanup while leaving other abilities active."""
        component = mech.body_script.component
        for instance in component.instances.values():
            if instance.graph.index == 0x047E:
                ability = instance.states.get(0)
                if isinstance(ability, nodes.Ability):
                    ability.presses.clear()
                    ability.stop()
                    component.run_now()
                    active = self.movement_mods(mech, statescript_mods(component))
                    mech.control_mover.track_mods(active, component.frame)
                break

    def _booster_contact(self, player, command, script) -> None:
        """Consume 047E's live tracking and damage states using current capsules and static LOS."""
        mover = player.control_mover if isinstance(player, bots.Bot) else player.mover
        entity = script.component.entity
        for instance in script.component.instances.values():
            if instance.graph.index != 0x047E:
                continue
            track, modify = instance.states.get(6), instance.states.get(7)
            if track is None or modify is None or not (track.active and modify.active):
                continue
            offset = track.evaluate(track.node.field("m_05BE62DD"), watch=False)
            radius = _number(track.evaluate(track.node.field("m_radius"), watch=False), 0.0)
            amount = _number(modify.evaluate(modify.node.field("m_modifyHealth.m_amount"), watch=False), 0.0)
            loadout = modify.evaluate(modify.node.field("m_modifyHealth.m_loadout"), watch=False)
            loadout = loadout.guid if isinstance(loadout, expr.Asset) else None
            if not isinstance(offset, expr.Vec3) or radius <= 0 or amount <= 0:
                continue
            # The graph provides a scaled local offset. Resolve its orientation
            # from the current mover look until the engine transform is available.
            yaw, pitch = mover.yaw, mover.pitch
            centre = _along(mover.position, aim(yaw - 16384, 0), offset.x)
            centre = _along(centre, aim(yaw, pitch - 16384), offset.y)
            centre = _along(centre, aim(yaw, pitch), offset.z)
            activation = instance.id, modify.counter
            contacts = self.booster_contacts.get(entity)
            if contacts is None or contacts[:2] != activation:
                contacts = self.booster_contacts[entity] = (*activation, set())
            hit = contacts[2]
            for target in self._enemies(player):
                if target.entity in hit:
                    continue
                a, b = target.segment()
                if (segment_distance(centre, a, b) > radius + target.radius
                        or not capsule_visible(self._collision(), self._eye(player), a, b, target.radius)):
                    continue
                hit.add(target.entity)
                dealt = self._damage(
                    player, target, amount, False, loadout, command.frame,
                    damage_tags=_damage_tags(modify.node.field("m_modifyHealth")),
                )
                vitals = self.vitals.get(target.entity)
                if dealt and vitals is not None and not vitals.dead:
                    self._booster_impulse(mover, target, modify)
            return
        self.booster_contacts.pop(entity, None)

    def _booster_impulse(self, mover, target: Target, modify) -> None:
        context = DvaImpulseContext(modify.instance)
        horizontal = _number(expr.evaluate(modify.node.field("m_modifyHealth.m_E33EFEE1"), context), 0.0)
        vertical = _number(expr.evaluate(modify.node.field("m_modifyHealth.m_583C208B"), context), 0.0)
        if horizontal or vertical:
            from ow174.game import assassin

            assassin.interrupt_pin(self, target)
        self._radial_impulse(mover.position, mover.yaw, target, horizontal, vertical)

    def _radial_impulse(self, origin, yaw, target: Target, horizontal, vertical) -> None:
        if (not (horizontal or vertical) or defenses.protection(target.owner) is not None
                or defenses.active_fortify(target.owner) is not None):
            return
        # Reconstructed interpretation: horizontal speed away from the source
        # and a separate upward speed. Overlapping feet use the source heading.
        dx = target.feet[0] - origin[0]
        dz = target.feet[2] - origin[2]
        distance = math.hypot(dx, dz)
        direction = (dx / distance, 0, dz / distance) if distance > EPSILON else aim(yaw, 0)
        impulse = (direction[0] * horizontal, vertical, direction[2] * horizontal)
        owner = target.owner
        if isinstance(owner, bots.Bot):
            if owner.spawner.get("manual_charge") is True:
                from ow174.game import npc_charge

                npc_charge.stop(owner)
            category = MOVER
            if not self.match.game_map.free_for_all and owner.team in (0, 1):
                category = (TEAM0, TEAM1)[owner.team]
            owner.apply_impulse(impulse, self.match.collision_world, category, self.match.tick)
        else:
            owner.mover.apply_impulse(impulse)

    def _melee(self, player, command, script, quanta) -> None:
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
            view_tick = command.frame - command.view_delay(quanta) * 1e6 / quanta
            for target in self._enemies(player):
                shown = self.seen(target, view_tick)
                if shown is None:
                    continue
                a, b = shown.segment()
                if (segment_distance(centre, a, b) - shown.radius <= radius and amount > 0.0
                        and capsule_visible(self._collision(), origin, a, b, shown.radius)):
                    actor = target.owner
                    if (not isinstance(actor, bots.Bot) and actor.hero.guid == GENJI
                            and deflection.genji_blocks_melee(
                                actor.body_script, origin, target.feet,
                                aim(actor.mover.yaw, actor.mover.pitch),
                            )):
                        continue
                    if self._damage(player, target, amount, False, loadout, command.frame,
                                    damage_tags=_damage_tags(modify)):
                        from ow174.game import assassin

                        assassin.melee_rescue(self, player, target)

    def update(self, now: float, tick: int, frames: bool = True) -> None:
        """Each tick: keep where the bodies are, fly the rockets, respawn, send health that changed. The
        other players' body frames only on the ticks that send (`frames`)."""
        if self.match.collision_world is None:
            return
        retained = max(self.tick + 1, tick - COMBAT_CATCH_UP + 1)
        first = tick
        if self.tick and tick > self.tick + 1 and (
            self.hooks.flights
            or self.zarya_gravitons.fields
            or any(rocket.last_tick is not None for rocket in self.rockets)
            or any(bot.firing and not bot.dead and isinstance(bot.body_script, BotScript)
                   for bot in self.match.bots)
        ):
            first = retained
        for target in self._targets():
            history = self.history.setdefault(target.entity, deque(maxlen=HISTORY))
            history.append((tick, target.feet, target.height, target.eye))
        # Replay the retained combat frames using the most recent poses. Player input
        # accepted just before the scheduler caught up joins only at the endpoint.
        for at in range(first, tick + 1):
            self.now, self.tick = now - (tick - at) * TICK, at
            for rocket in list(self.rockets):
                if at == tick or rocket.last_tick is not None:
                    self._fly(rocket)
            self.hooks.update(at)
            self.zarya_gravitons.update(at, TICK)
            self._bot_weapons(at)
            for host in self.assassin_effects.values():
                host.advance(at)
            if at < tick:
                # A body can send a message and retire on different retained ticks.
                # Deliver before advancing to a tick that may retire the sender.
                self.match.deliver_body_messages()
        for caster in self.match.players:
            if caster.hero.guid == zarya_barriers.HERO:
                self.zarya_bubbles.sync(caster)
        sigma_grasp.update(self)
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
        for table in (self.history, self.vitals, self.replacement_ticks, self.stuns):
            for entity in [key for key in table if key not in current]:
                del table[entity]
        for entity, (_, deadline, _) in list(self.ana_sleeps.items()):
            if entity not in current or deadline <= tick:
                del self.ana_sleeps[entity]
        for entity, frost in list(self.frost.items()):
            if entity not in current or not frost.progress(tick * 16):
                del self.frost[entity]
        for entity, locks in list(self.input_locks.items()):
            kept = [lock for lock in locks if lock.end is None or lock.end > tick - MAX_GAP]
            if entity in current and kept:
                self.input_locks[entity] = kept
            else:
                del self.input_locks[entity]
        for entity, modifiers in list(self.heal_modifiers.items()):
            retained = {
                key: live for key, intervals in modifiers.items()
                if (live := [interval for interval in intervals if tick < interval[1]])
            }
            if entity in current and retained:
                self.heal_modifiers[entity] = retained
            else:
                del self.heal_modifiers[entity]
        for source, victim in list(self.pins.items()):
            if source not in current or victim not in current:
                self.clear_pin(source)

    # --- targets -----------------------------------------------------------------------------------

    def _targets(self) -> list[Target]:
        found = []
        for player in self.match.players:
            if not (player.has_body and player.spawned and player.client is not None):
                continue
            if player.controlled_mech is not None:
                continue  # the retained pilot is parked while its mech takes input.
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
            controlled = bot.control_mover
            crouched = controlled is not None and controlled.crouched
            height = data.crouch_height if crouched else data.stand_height
            eye = EYE if controlled is None else eye_height(bot.body, crouched)
            found.append(
                Target(bot.entity, bot.team, tuple(bot.position), height, data.stand_radius, eye, bot)
            )
        return found

    def player_target(self, player) -> Target | None:
        """Resolve a player controller to the body currently taking combat."""
        if player is None or player.gone or not self.match._shown(player):
            return None
        actor = player.controlled_mech or player
        return next((target for target in self._targets() if target.owner is actor), None)

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

    def _enemies(self, shooter, *, team=None) -> list[Target]:
        free_for_all = self.match.game_map.free_for_all
        team = shooter.team if team is None else team
        source = _source_entity(shooter)
        found = []
        for target in self._targets():
            if target.owner is shooter or target.entity == source:
                continue
            if (isinstance(target.owner, bots.Bot) and target.owner.owner == shooter.entity
                    and target.owner.source_body == source):
                continue
            if isinstance(shooter, bots.Bot) and shooter.owner and target.entity == shooter.source_body:
                continue
            if not free_for_all and target.team == team:
                continue
            if self.vitals_of(target).dead or defenses.protection(target.owner) == "intangible":
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
        from ow174.game import npc_charge, npc_control

        for bot in tuple(self.match.bots):
            script = bot.body_script
            vm_frame = tick+bot.clock_offset
            manual = npc_control.manual(bot)
            if ((not bot.firing and not manual) or bot.dead
                    or not isinstance(script, BotScript) or script.frame > vm_frame):
                continue
            try:
                autonomous = manual and bot.spawner.get("autonomous_targeting") is True
                if autonomous and tick == self.match.tick:
                    npc_control.acquire(self, bot)
                controlled = manual and (autonomous or bot.attack_target is not None or bot.attack_input)
                allowed = (npc_control.update_input(self, bot) if controlled
                           else bot.firing and not bot.detached_bomb and not self.is_stunned(bot))
                charging = controlled and bot.spawner.get("manual_charge") is True
                if charging:
                    npc_charge.step(self, bot, tick)
                snapshot = script.advance_live(vm_frame, bot.position)
                if bot not in self.match.bots or bot.body_script is not script:
                    continue
                shots, script.component.shots[:] = list(script.component.shots), []
                melee = controlled and bot.spawner.get("manual_melee") is True
                if melee:
                    self._npc_melee(bot, script, npc_control.MELEE_GRAPHS[bot.hero], allowed)
                for state, number, time, activation in shots:
                    if not allowed or melee or charging:
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
                log.exception("[game] %s: %s's weapon script failed", self.match.label(), bot.name)
                if bot.detached_bomb:
                    from ow174.game import npc

                    npc.remove_body(self.match, bot)
                else:
                    script.retire()
                    bot.body_script = None

    def npc_charge_damage(self, bot, state):
        if bot.dead or not bot.firing or self.is_stunned(bot) or bot.entity in self.zarya_gravitons.pulled:
            return
        selected = state.evaluate(state.node.field("m_FA3ECC43"), watch=False)
        ids = {value.id for value in selected or () if isinstance(value, expr.Entity)}
        amount = _number(state.evaluate(state.node.field("m_modifyHealth.m_amount"), watch=False), 0.0)
        loadout = state.evaluate(state.node.field("m_modifyHealth.m_loadout"), watch=False)
        loadout = loadout.guid if isinstance(loadout, expr.Asset) else None
        for target in self._enemies(bot):
            if target.entity in ids:
                self._damage(bot, target, amount, False, loadout, self.tick, world_tick=self.tick)

    def _npc_melee(self, bot, script, spec, allowed):
        graph_id, index, track_pos = spec
        instance = next((i for i in script.component.instances.values() if i.graph.index == graph_id), None)
        state = instance.states.get(index) if instance is not None else None
        if state is None or not state.active or state.ending:
            return
        counters = self.melee_seen.setdefault(bot.entity, {})
        key = instance.id, index
        if counters.get(key) == state.counter:
            return
        counters[key] = state.counter
        if not allowed:
            return
        amount = _number(state.evaluate(state.node.field("m_modifyHealth.m_amount"), watch=False), 0.0)
        loadout = state.evaluate(state.node.field("m_modifyHealth.m_loadout"), watch=False)
        loadout = loadout.guid if isinstance(loadout, expr.Asset) else None
        for target in self.npc_melee_targets(bot, instance, instance.graph.node(track_pos)):
            self._damage(bot, target, amount, False, loadout, self.tick, world_tick=self.tick)

    def npc_melee_targets(self, bot, instance, tracking):
        offset = instance.evaluate(tracking.field("m_05BE62DD"))
        radius = _number(instance.evaluate(tracking.field("m_radius")), 0.0)
        if not isinstance(offset, expr.Vec3) or radius <= 0:
            return
        x, y, z = bot.position
        origin = x, y + offset.y, z
        centre = _along(origin, aim(round(bot.yaw), 0), offset.z)
        for target in self._enemies(bot):
            a, b = target.segment()
            if (segment_distance(centre, a, b) - target.radius <= radius
                    and capsule_visible(self._collision(), origin, a, b, target.radius)):
                yield target

    def _eye(self, player) -> tuple:
        if isinstance(player, bots.Bot):
            x, y, z = player.position
            height = eye_height(player.body, player.control_mover.crouched)
        else:
            x, y, z = player.mover.position
            height = eye_height(player.hero.body, player.mover.crouched)
        return (x, y + height, z)

    def controlled_mech_command(self, bot, command, snapshot, stunned, quanta) -> None:
        script = bot.body_script
        shots, script.component.shots[:] = list(script.component.shots), []
        if not stunned:
            for state, number, time, activation in shots:
                try:
                    self._shoot(bot, command, state, number, time, activation=activation,
                                snapshot=snapshot or script.latest_live, command_quanta=quanta)
                except Exception:
                    log.exception("[game] %s: %s's controlled shot failed", self.match.label(), bot.name)
            self._melee(bot, command, script, quanta)
            if bot.entity not in self.zarya_gravitons.pulled:
                self._booster_contact(bot, command, script)
        if snapshot is not None:
            for viewer in self.match.players:
                if viewer.spawned and viewer.client is not None:
                    viewer.client.queue_bot_live(bot.entity, snapshot)

    def _shoot(
        self, shooter, command, state, number: int, time: int,
        *, activation: tuple[int, int], snapshot: Snapshot | None = None, command_quanta: int | None = None,
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
            origin = (x, y + EYE, z) if command is None else self._eye(shooter)
            angles = (round(shooter.yaw), round(shooter.pitch)) if command is None else command.aim()
            direction = aim(*angles)
        else:
            origin, direction = self._eye(shooter), aim(*command.aim())
        direct = hit_def(
            state, node.field("m_6396149F"), critical_override=dva.direct_hit_critical(shooter, state),
        )
        entity_def = (node.field("m_projectileEntity") or {}).get("m_entityDef")
        asset = state.evaluate(entity_def, watch=False) if entity_def is not None else None
        definition = asset.guid if isinstance(asset, expr.Asset) else None
        rays = _volley_directions(shooter, state, direction, number=number, counter=activation[0])
        speed = field("m_projectileSpeed")
        if speed:
            lifetime = field("m_projectileLifetime", 5.0) or 5.0
            # The native sweep at D72C80 clips by both lifetime and distance.
            reach = field("m_FEC435C6")
            if reach:
                lifetime = min(lifetime, reach / speed)
            blast = hit_def(state, node.field("m_D2D11CE9"))
            collision = node.field("m_6396149F") or {}
            radius = _number(state.evaluate(collision.get("m_57DAE1B5"), watch=False), 0.0)
            if not math.isfinite(radius):
                radius = 0.0
            target_limit = 1
            if (state.instance.graph.index, state.index) == (0x05E6, 13):
                configured = state.evaluate(collision.get("m_B4454CD7"), watch=False)
                target_limit = max(1, min(99, expr.to_int(configured)))
            shot = None
            if definition is not None:
                shot = self.projectiles.fired(
                    shooter, state, shot_frame, definition, lifetime, origin, direction,
                    activation=activation, snapshot=snapshot,
                )
            damaging = (direct is not None and direct.amount > 0.0) or (
                blast is not None and blast.amount > 0.0
            )
            spawn = None
            gravity = 0.0
            if (state.instance.graph.index == 0x025F
                    and graviton.FIELD_ENTITY in projectiles._defs(node.field("m_49CBA3BC"))):
                spawn = graviton.FIELD_ENTITY
                for motion in node.field("m_projectileMotions") or ():
                    if motion.get("$") == "STU_E1A5B579":
                        gravity = _number(state.evaluate(motion.get("m_599AB8D7"), watch=False), 0.0)
            if damaging or shot is not None or spawn is not None:
                # Keep the existing projectile record for the centre ray. Other
                # pellets have independent server collision and no wire record.
                self.rockets.extend(
                    Rocket(
                        shooter, shooter.team, origin, ray, speed, lifetime, direct, blast,
                        shot if index == 0 else None,
                        last_tick=self.tick if npc else None,
                        definition=definition,
                        target_limit=target_limit,
                        spawn=spawn,
                        gravity=gravity,
                        collision_radius=radius,
                        source_entity=_source_entity(shooter) if spawn is not None else None,
                    )
                    for index, ray in enumerate(rays)
                )
            return
        if direct is None or direct.amount <= 0.0:
            return
        reach = field("m_FEC435C6", RANGE) or RANGE
        # The input supplies this simulation frame's aim and view time. Notices use the volley clock.
        view_tick = self.tick
        if command is not None:
            quanta = command_quanta if npc else shooter.quanta
            view_tick = command.frame - command.view_delay(quanta) * 1e6 / quanta
        for ray in rays:
            self._hitscan(shooter, origin, ray, reach, direct, view_tick, shot_frame,
                          world_tick=self.tick if npc else None, definition=definition)

    def _shield_hit(self, shooter, origin, direction, reach, *, radius=0.0, team=None):
        solid = reinhardt_barrier.intercept(self, shooter, origin, direction, reach, team=team)
        bubble = self.zarya_bubbles.intercept(shooter, origin, direction, reach, radius=radius, team=team)
        if bubble is not None and (solid is None or bubble[0] < solid[0]):
            return bubble
        return solid

    def _shield_damage(self, shield, amount, shooter):
        if isinstance(shield, zarya_barriers.Bubble):
            self.zarya_bubbles.damage(shield, amount, shooter)
        else:
            reinhardt_barrier.damage(shield, amount)

    def _hitscan(self, shooter, origin, direction, reach, direct, view_tick, shot_frame,
                 *, world_tick=None, definition=None, reflected=False) -> None:
        """Resolve one ray, including independent per-pellet armour and occlusion."""
        best = None
        for target in self._enemies(shooter):
            shown = self.seen(target, view_tick)
            if shown is None:
                continue
            a, b = shown.segment()
            t = ray_capsule(origin, direction, a, b, shown.radius)
            if t is not None and t <= reach and (best is None or t < best[0]):
                best = (t, target, shown)
        t = best[0] if best is not None else reach
        wall = ray_world(self._collision(), origin, direction, t)
        limit = min(t, wall) if wall is not None else t
        shield = self._shield_hit(shooter, origin, direction, limit)
        if shield is not None and shield[0] < limit:
            limit = shield[0]
        else:
            shield = None
        absorbed = self._absorb_contact(shooter, origin, direction, limit, definition)
        reflection = self._genji_hit(shooter, origin, direction, limit, definition)
        if reflection is not None and (absorbed is None or reflection[0] < absorbed[0]):
            distance, reflector, forward, _ = reflection
            if not reflected and reach - distance > EPSILON:
                point = _along(origin, direction, distance)
                self._hitscan(reflector, point, forward, reach - distance, direct, view_tick, shot_frame,
                              world_tick=world_tick, definition=definition, reflected=True)
            return
        if absorbed is not None:
            distance, grasp = absorbed
            if grasp is not None:
                sigma_grasp.absorb(grasp, _source_entity(shooter), direct.amount * direct.scale(distance))
            return
        if shield is not None:
            distance, instance = shield
            self._shield_damage(instance, direct.amount * direct.scale(distance), shooter)
            return
        if best is None or (wall is not None and wall < t):
            return
        _, target, shown = best
        hit_y = origin[1] + direction[1] * t
        critical = (hit_y >= shown.feet[1] + shown.eye - HEAD_BELOW
                    and defenses.active_fortify(target.owner) is None)
        amount = direct.amount * direct.scale(t) * (direct.critical if critical else 1.0)
        if self._damage(
            shooter, target, amount, critical and direct.critical > 1.0, direct.loadout, shot_frame,
            world_tick=world_tick, damage_tags=direct.damage_tags,
        ):
            self._hit_effect(target, direct.effect)

    def _defense_visibility(self, eye, origin, direction):
        collision = self._collision()

        def visible(contact):
            delta = _sub(_along(origin, direction, contact), eye)
            distance = math.sqrt(_dot(delta, delta))
            if distance <= EPSILON:
                return True
            ray = tuple(axis / distance for axis in delta)
            wall = ray_world(collision, eye, ray, distance)
            return wall is None or wall >= distance - 1e-6

        return visible

    def _absorb_contact(self, shooter, origin, direction, reach, definition, team=None, radius=0.0):
        matrix = self._matrix_hit(shooter, origin, direction, reach, definition, team, radius)
        best = (matrix, None) if matrix is not None else None
        if not deflection.absorbable(definition):
            return best
        for target in self._enemies(shooter, team=team):
            actor = target.owner
            if getattr(getattr(actor, "hero", None), "guid", None) != sigma_grasp.HERO:
                continue
            mover = actor.mover
            eye = self._eye(actor)
            contact = deflection.grasp_contact(
                actor.body_script, origin, direction, reach, eye, aim(mover.yaw, mover.pitch),
                visible=self._defense_visibility(eye, origin, direction), radius=radius,
            )
            if contact is not None and (best is None or contact[0] < best[0]):
                best = contact
        return best

    def _matrix_hit(self, shooter, origin, direction, reach, definition, team=None, radius=0.0):
        """Nearest live enemy Matrix; current authoritative pose (reconstructed).

        Callers compare this with the first body/static collision. Hitscan Matrix
        history and retail attachment/filter semantics still require acceptance.
        """
        if not deflection.absorbable(definition):
            return None
        best = None
        for target in self._enemies(shooter, team=team):
            actor = target.owner
            at = None
            if isinstance(actor, bots.Bot):
                if actor.hero != dva.HERO or actor.body != dva.MECH or actor.control_mover is None:
                    continue
                mover = actor.control_mover
                if actor.body_script is not None:
                    vm = actor.body_script.component
                    # Contact precedes input for this world tick.
                    at = (self.tick + actor.clock_offset) * vm.frame_ms - 1
            else:
                if actor.hero.guid != dva.HERO or actor.hero.body != dva.MECH:
                    continue
                mover = actor.mover
            x, y, z = target.feet
            eye = x, y + target.eye, z
            distance = deflection.matrix_distance(
                actor.body_script, origin, direction, reach, eye, aim(mover.yaw, mover.pitch),
                visible=self._defense_visibility(eye, origin, direction), at=at, radius=radius,
            )
            if distance is not None and (best is None or distance < best):
                best = distance
        return best

    def _genji_hit(self, shooter, origin, direction, reach, definition, team=None, radius=0.0):
        """Return the first active enemy reflector and its current aim."""
        if not deflection.absorbable(definition):
            return None
        best = None
        for target in self._enemies(shooter, team=team):
            actor = target.owner
            if isinstance(actor, bots.Bot) or actor.hero.guid != GENJI:
                continue
            forward = aim(actor.mover.yaw, actor.mover.pitch)
            eye = self._eye(actor)
            contact = deflection.genji_contact(
                actor.body_script, origin, direction, reach, eye, forward,
                visible=self._defense_visibility(eye, origin, direction), radius=radius,
            )
            if contact is not None and (best is None or contact[0] < best[0]):
                distance, state = contact
                best = distance, actor, forward, state
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
        self._sweep_projectile(rocket, duration)

    def _sweep_projectile(
        self, rocket: Rocket, duration: float, *, reflections_left: int = PROJECTILE_REFLECTION_LIMIT,
    ) -> None:
        # Sweep the remaining flight even when the range or lifetime ends inside this tick.
        step = rocket.speed * duration
        direction = rocket.direction
        if rocket.gravity:
            dx, dy, dz = (value * step for value in direction)
            dy -= .5 * rocket.gravity * duration * duration
            step = math.hypot(dx, dy, dz)
            if step > EPSILON:
                direction = (dx / step, dy / step, dz / step)
        best = assisted = None
        radius = rocket.collision_radius
        for target in self._enemies(rocket.shooter, team=rocket.team):
            if target.entity in rocket.hit_entities:
                continue
            a, b = target.segment()
            t = ray_capsule(rocket.position, direction, a, b, target.radius)
            if t is not None and t <= step and (best is None or t < best[0]):
                best = (t, target)
            if radius > 0:
                t = ray_capsule(rocket.position, direction, a, b, target.radius + radius)
                if t is not None and t <= step and (assisted is None or t < assisted[0]):
                    assisted = (t, target)
        assisted_rank = assisted[0] + radius if assisted is not None else 0.0
        # Check cover through the ranked contact even if the tick ends first.
        wall = ray_world(self._collision(), rocket.position, direction, max(step, assisted_rank))
        if wall is not None and wall <= step and (best is None or wall < best[0]):
            best = (wall, None)
        limit = best[0] if best is not None else step
        shield = self._shield_hit(rocket.shooter, rocket.position, direction, limit, team=rocket.team)
        shield_instance = None
        if shield is not None and (best is None or shield[0] < limit):
            limit, shield_instance = shield
            best = (limit, None)
        # Ordinary actor hits qualify in the host capsule model. The native
        # early exit keeps them before D7633C ranks alternate contacts.
        if (assisted is not None and (best is None or (best[1] is None and assisted_rank < limit))
                and (wall is None or assisted_rank < wall)):
            best = assisted
            limit = assisted[0]
            shield_instance = None
        absorbed = self._absorb_contact(
            rocket.shooter, rocket.position, direction, limit, rocket.definition, rocket.team,
            rocket.collision_radius,
        )
        reflection = self._genji_hit(
            rocket.shooter, rocket.position, direction, limit, rocket.definition, rocket.team,
            rocket.collision_radius,
        )
        if reflection is not None and (absorbed is None or reflection[0] < absorbed[0]):
            distance, reflector, forward, state = reflection
            if rocket.shot is not None:
                self.projectiles.absorbed(rocket.shot)
                rocket.shot = None
            if reflections_left <= 0:
                # Coincident hostile defense volumes can reflect without advancing time.
                self.rockets.remove(rocket)
                return
            source = rocket.source_entity
            if source is None:
                source = _source_entity(rocket.shooter)
            rocket.position = _along(rocket.position, direction, distance)
            rocket.direction = forward
            rocket.shooter, rocket.team = reflector, reflector.team
            elapsed = duration * distance / step if step > EPSILON else 0.0
            rocket.flown += elapsed
            rocket.left -= elapsed
            if rocket.spawn == graviton.FIELD_ENTITY:
                rocket.source_entity = _source_entity(reflector)
            direct = (rocket.direct.amount * rocket.direct.scale(rocket.flown * rocket.speed)
                      if rocket.direct is not None else 0.0)
            blast = rocket.blast.amount if rocket.blast is not None else 0.0
            deflection.reflected_projectile(state, source, direct + blast)
            remaining = duration - elapsed
            if remaining > EPSILON:
                self._sweep_projectile(rocket, remaining, reflections_left=reflections_left - 1)
            elif rocket.left <= EPSILON:
                self._landed(rocket)
            return
        if absorbed is not None:
            distance, grasp = absorbed
            rocket.flown += duration * distance / step if step > EPSILON else 0.0
            if grasp is not None:
                direct = (rocket.direct.amount * rocket.direct.scale(rocket.flown * rocket.speed)
                          if rocket.direct is not None else 0.0)
                blast = rocket.blast.amount if rocket.blast is not None else 0.0
                source = rocket.source_entity
                if source is None:
                    source = _source_entity(rocket.shooter)
                sigma_grasp.absorb(grasp, source, direct + blast)
            self.rockets.remove(rocket)
            if rocket.shot is not None:
                self.projectiles.absorbed(rocket.shot)
            return
        if best is None:
            rocket.position = _along(rocket.position, direction, step)
            if rocket.gravity:
                dx, dy, dz = rocket.direction
                rocket.direction = (dx, dy - rocket.gravity * duration / rocket.speed, dz)
            rocket.flown += duration
            rocket.left -= duration
            if rocket.left <= 0.0:
                self._landed(rocket)
            return
        t, struck = best
        rocket.flown += duration * t / step if step > EPSILON else 0.0
        point = _along(rocket.position, direction, t)
        direct_amount = (rocket.direct.amount * rocket.direct.scale(rocket.flown * rocket.speed)
                         if rocket.direct is not None else 0.0)
        blast = rocket.blast
        piercing = struck is not None and rocket.target_limit > 1 and blast is None
        if not piercing:
            if rocket.spawn == graviton.FIELD_ENTITY:
                landing = (_along(rocket.position, direction, max(0.0, t - 0.01))
                           if struck is None else point)
                self._landed(rocket, landing)
            else:
                self._landed(rocket)
        if blast is not None and blast.effect == ana_grenade.EFFECT:
            origin = point
            if struck is None:
                origin = _along(rocket.position, rocket.direction, max(0.0, t - 0.001))
            self._ana_grenade(rocket, point, origin)
            if shield_instance is not None:
                self._shield_damage(shield_instance, blast.amount, rocket.shooter)
            return
        if struck is not None and rocket.direct is not None and direct_amount > 0.0:
            critical = (rocket.definition == dva.LIGHT_GUN_PROJECTILE and rocket.direct.critical > 1.0
                        and point[1] >= struck.feet[1] + struck.eye - HEAD_BELOW
                        and defenses.active_fortify(struck.owner) is None)
            amount = direct_amount * (rocket.direct.critical if critical else 1.0)
            dealt = self._damage(
                rocket.shooter, struck, amount, critical, rocket.direct.loadout, self.tick,
                world_tick=self.tick, source_entity=rocket.source_entity, source_team=rocket.team,
                damage_tags=rocket.direct.damage_tags,
            )
            if dealt:
                self._hit_effect(struck, rocket.direct.effect)
                if rocket.definition == FROST_SPRAY:
                    self._apply_frost(rocket.shooter, struck)
        if piercing:
            rocket.hit_entities.add(struck.entity)
            spent = t / rocket.speed
            rocket.position = point
            rocket.left -= spent
            remaining = duration - spent
            if rocket.left <= EPSILON or len(rocket.hit_entities) >= rocket.target_limit:
                self._landed(rocket)
            elif remaining > EPSILON:
                self._sweep_projectile(rocket, remaining, reflections_left=reflections_left)
            return
        if blast is None or blast.amount <= 0.0:
            if shield_instance is not None and rocket.direct is not None:
                self._shield_damage(shield_instance, direct_amount, rocket.shooter)
            return
        # A world hit lies on its collision face. Keep the blast on the incoming
        # side so a ray starting on the face cannot leak damage through the wall.
        if struck is None:
            point = _along(rocket.position, rocket.direction, max(0.0, t - 0.001))
        for target in self._enemies(rocket.shooter, team=rocket.team):
            a, b = target.segment()
            distance = max(0.0, segment_distance(point, a, b) - target.radius)
            scalar = blast.splash(distance)
            if scalar > 0.0 and self._blast_visible(point, target, shooter=rocket.shooter, team=rocket.team):
                dealt = self._damage(
                    rocket.shooter, target, blast.amount * scalar, False, blast.loadout, self.tick,
                    world_tick=self.tick, source_team=rocket.team, damage_tags=blast.damage_tags,
                )
                if dealt:
                    self._hit_effect(target, blast.effect)
        if shield_instance is not None:
            self._shield_damage(shield_instance, direct_amount + blast.amount, rocket.shooter)

    def _ana_grenade(self, rocket, point, origin) -> None:
        shooter, blast = rocket.shooter, rocket.blast
        deadline = self.tick + math.ceil(ana_grenade.DURATION / TICK)
        for target in self._targets():
            vitals = self.vitals_of(target)
            if vitals.dead:
                continue
            a, b = target.segment()
            distance = max(0.0, segment_distance(point, a, b) - target.radius)
            scalar = blast.splash(distance)
            if scalar <= 0 or not self._blast_visible(origin, target, shooter=shooter, team=rocket.team):
                continue
            friendly = (target.owner is shooter
                        or (not self.match.game_map.free_for_all and target.team == rocket.team))
            if friendly:
                modifiers = self.heal_modifiers.setdefault(target.entity, {})
                modifiers["boost"] = [
                    (start, min(end, self.tick)) for start, end in modifiers.get("boost", ())
                    if start < self.tick
                ]
                self._heal(shooter, target, ana_grenade.HEAL * scalar, self.tick, self.tick)
                starts = self.tick + math.ceil(ana_grenade.BOOST_DELAY / TICK)
                modifiers["boost"].append((starts, deadline))
            elif defenses.protection(target.owner) is None:
                dealt = self._damage(shooter, target, ana_grenade.DAMAGE * scalar, False, blast.loadout,
                                     self.tick, world_tick=self.tick, source_team=rocket.team)
                if dealt and not vitals.dead:
                    intervals = self.heal_modifiers.setdefault(target.entity, {}).setdefault("anti", [])
                    intervals.append((self.tick, deadline))

    def _blast_visible(self, origin, target: Target, *, shooter=None, team=None) -> bool:
        """Static cover to the nearest capsule axis point (reconstructed).

        This uses mover collision categories and optionally live Reinhardt
        geometry. Retail damage masks and partial-cover sampling remain open.
        """
        a, b = target.segment()
        ab, ap = _sub(b, a), _sub(origin, a)
        length = _dot(ab, ab)
        share = 0.0 if length <= EPSILON else max(0.0, min(1.0, _dot(ap, ab) / length))
        destination = _along(a, ab, share)
        delta = _sub(destination, origin)
        distance = math.sqrt(_dot(delta, delta))
        reach = max(0.0, distance - target.radius)
        if reach <= EPSILON:
            return True
        direction = tuple(value / distance for value in delta)
        wall = ray_world(self._collision(), origin, direction, reach)
        if wall is not None and wall < reach:
            return False
        return shooter is None or self._shield_hit(shooter, origin, direction, reach, team=team) is None

    def dva_ultimate_targets(self, state) -> None:
        """Fill the authored target arrays at detonation or mech landing."""
        instance = state.instance
        source = self.match._message_actor(instance.component.entity)
        centre = state.evaluate(state.node.field("m_05BE62DD"), watch=False)
        radius = _number(state.evaluate(state.node.field("m_radius"), watch=False), 0.0)
        targets, distances, shields = [], [], ()
        if source is not None and isinstance(centre, expr.Vec3) and radius > 0:
            origin = tuple(centre)
            blast = instance.graph.index == 0x0B2F
            if blast:
                position = source.position if isinstance(source, bots.Bot) else source.mover.position
                origin = tuple(a + b for a, b in zip(position, origin, strict=True))
                shields = (
                    *reinhardt_barrier.blast_targets(self, source, origin, radius),
                    *self.zarya_bubbles.blast_targets(source, origin, radius),
                )
            controller = source.owner if isinstance(source, bots.Bot) else source.entity
            for target in self._enemies(source):
                if isinstance(target.owner, bots.Bot) and target.owner.owner == controller:
                    continue
                a, b = target.segment()
                distance = max(0.0, segment_distance(origin, a, b) - target.radius)
                if distance <= radius and self._blast_visible(
                    origin, target, shooter=source if blast else None,
                ):
                    targets.append(expr.Entity(target.entity))
                    distances.append(expr.f32(distance))
        instance.write(expr.dynamic(state.node.field("m_8A2905A0")), tuple(targets))
        instance.write(expr.dynamic(state.node.field("m_CEE20F6F")), tuple(distances))

        # Snapshot body and shield visibility before any barrier breaks.
        if shields:
            value = instance.graph.node(24).field("m_value")
            for shield, distance in shields:
                amount = expr.evaluate(value, SelfDestructDistance(instance, distance))
                self._shield_damage(shield, _number(amount, 0.0), source)

    def dva_ultimate_damage(self, state) -> None:
        source = self.match._message_actor(state.instance.component.entity)
        if source is None:
            return
        selected = state.evaluate(state.node.field("m_FA3ECC43"), watch=False)
        entities = selected if isinstance(selected, tuple) else (selected,)
        ids = {entity.id for entity in entities if isinstance(entity, expr.Entity)}
        amount = _number(state.evaluate(state.node.field("m_modifyHealth.m_amount"), watch=False), 0.0)
        loadout = state.evaluate(state.node.field("m_modifyHealth.m_loadout"), watch=False)
        loadout = loadout.guid if isinstance(loadout, expr.Asset) else None
        centre = state.evaluate(state.node.field("m_modifyHealth.m_C0139FA6"), watch=False)
        context = DvaImpulseContext(state.instance)
        horizontal = _number(expr.evaluate(state.node.field("m_modifyHealth.m_E33EFEE1"), context), 0.0)
        vertical = _number(expr.evaluate(state.node.field("m_modifyHealth.m_583C208B"), context), 0.0)
        rings = state.node.field("m_modifyHealth.m_knockbackRings") or ()
        for target in self._enemies(source):
            if target.entity in ids:
                dealt = self._damage(
                    source, target, amount, False, loadout, state.instance.component.frame,
                    damage_tags=_damage_tags(state.node.field("m_modifyHealth")),
                )
                vitals = self.vitals.get(target.entity)
                if dealt and isinstance(centre, expr.Vec3) and vitals is not None and not vitals.dead:
                    yaw = round(source.yaw) if isinstance(source, bots.Bot) else source.mover.yaw
                    self._ultimate_impulse(state, tuple(centre), yaw, target, rings, horizontal, vertical)

    def _ultimate_impulse(self, state, origin, yaw, target, rings, horizontal, vertical) -> None:
        a, b = target.segment()
        distance = segment_distance(origin, a, b) - target.radius
        for ring in rings:
            radius = _number(state.evaluate(ring.get("m_radius"), watch=False), 0.0)
            if distance > radius:
                continue
            # Reconstructed ring mapping: the first field scales planar
            # velocity and the third scales lift. Damage is authored separately.
            planar = _number(state.evaluate(ring.get("m_9102DA3F"), watch=False), 0.0)
            lift = _number(state.evaluate(ring.get("m_10399AE7"), watch=False), 0.0)
            self._radial_impulse(origin, yaw, target, horizontal * planar, vertical * lift)
            return

    def _landed(self, rocket: Rocket, point=None) -> None:
        """The projectile ended (hit, range, lifetime): the predicted one's release follows."""
        if rocket in self.rockets:
            self.rockets.remove(rocket)
            if rocket.spawn == graviton.FIELD_ENTITY:
                self.zarya_gravitons.landed(rocket, rocket.position if point is None else point, TICK)
        if rocket.shot is not None:
            self.projectiles.flown(rocket.shot, rocket.flown)

    def heal_area(self, healer, centre, radius: float, amount: float,
                  *, first_tick=None, last_tick=None) -> None:
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
            self._heal(healer, target, amount, first_tick if first_tick is not None else self.tick,
                       last_tick if last_tick is not None else self.tick)

    def _heal(self, healer, target, amount, first_tick, last_tick):
        modifiers = self.heal_modifiers.get(target.entity)
        if modifiers:
            # A field can replay retained ticks across either modifier boundary.
            anti = modifiers.get("anti", ())
            boost = modifiers.get("boost", ())
            factor = 0.0
            for tick in range(first_tick, last_tick + 1):
                if any(start <= tick < end for start, end in anti):
                    continue
                factor += ana_grenade.BOOST if any(start <= tick < end for start, end in boost) else 1.0
            amount *= factor / (last_tick - first_tick + 1)
        vitals = self.vitals_of(target)
        given = vitals.heal(amount)
        if given > 0.0:
            if not isinstance(healer, bots.Bot):
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

    def _credit_player(self, actor):
        if not isinstance(actor, bots.Bot):
            return actor
        player = self.match._message_player(actor.source_body) if actor.owner else None
        return player if player is not None and player.entity == actor.owner else None

    def _damage(
        self, shooter, target: Target, amount: float, critical: bool, loadout, frame: int,
        *, world_tick: int | None = None, armour_scalar: float | None = None,
        source_entity: int | None = None, source_team: int | None = None,
        damage_tags: tuple[int, ...] | None = None,
    ) -> bool:
        if isinstance(target.owner, bots.Bot) and target.owner not in self.match.bots:
            return False  # an earlier damage/death callback retired an owned body in this target batch.
        vitals = self.vitals_of(target)
        if vitals.dead or amount <= 0.0 or defenses.protection(target.owner) is not None:
            return False
        bubble = self.zarya_bubbles.for_target(target.owner)
        if bubble is not None:
            self.zarya_bubbles.damage(bubble, amount, shooter, source_entity=source_entity)
            return False
        fortify = defenses.active_fortify(target.owner)
        if fortify is not None:
            modifier = _number(fortify.evaluate(fortify.node.field("m_50530EAB"), watch=False), 0.0)
            amount *= max(0.0, min(1.0, 1.0 + modifier))
        dealt = vitals.damage(amount, armour_scalar=armour_scalar)
        if dealt <= 0.0:
            return False
        sleep = self.ana_sleeps.get(target.entity)
        if sleep is not None:
            start, deadline, waking = sleep
            if not waking and self.tick < deadline and (self.tick - start) * TICK > SLEEP_DAMAGE_GRACE:
                wake_end = min(deadline, self.tick + math.ceil(SLEEP_WAKE / TICK))
                self.ana_sleeps[target.entity] = (start, wake_end, True)
                self.record_input_lock(target.entity, SLEEP_EFFECT, wake_end)
        killed = vitals.now[HEALTH_POOL] <= EPSILON
        if killed:
            self.frost.pop(target.entity, None)
            vitals.now[HEALTH_POOL] = 0.0
            vitals.died_at = self.now
            vitals.death_tick = self.match.tick if world_tick is None else world_tick
            vitals.killer = _source_entity(shooter) if source_entity is None else source_entity
        credit = self._credit_player(shooter)
        if credit is not None:
            flags = (CRITICAL if critical else 0) | (KILLING if killed else 0)
            self._tell_hit(credit, target, dealt, flags, frame)
            credit.stats.add(stats.HERO_DAMAGE, dealt)
            team = shooter.team if source_team is None else source_team
            if ((source_entity is None or source_entity == _source_entity(shooter))
                    and (self.match.game_map.free_for_all or target.team != team)):
                dva.charge_damage(credit, shooter, dealt)
        victim = target.owner
        if killed and isinstance(victim, bots.Bot):
            # The NPC death script owns its terminal health change and death event ordering.
            self._bot_died(shooter, victim, critical, loadout, vitals.death_tick, source_entity=vitals.killer)
            return True
        self._sync_body_health(victim, vitals)
        if killed:
            self._died(shooter, victim, critical, loadout, source_entity=vitals.killer)
        elif damage_tags is not None:
            from ow174.game import sombra_emp

            sombra_emp.notify_damage(self.match, shooter, victim, damage_tags, source_entity)
        return True

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
    def _death_parameters(killer, critical: bool, loadout, *, source_entity=None) -> dict:
        return {
            KILLER: expr.Entity(_source_entity(killer) if source_entity is None else source_entity),
            CRIT: bool(critical),
            LOADOUT: expr.Asset(loadout) if loadout else None,
            ASSISTS: (),
        }

    def _bot_died(
        self, killer, bot: bots.Bot, critical: bool, loadout, tick: int, *, source_entity=None,
    ) -> None:
        self.clear_pins(bot.entity)
        self.stuns.pop(bot.entity, None)
        self.ana_sleeps.pop(bot.entity, None)
        self.heal_modifiers.pop(bot.entity, None)
        self.zarya_bubbles.retire(bot.entity)
        self.hooks.retire(bot.entity)
        self.zarya_gravitons.release(bot)
        bot.died(tick)
        if not bot.retain_corpse:
            self.respawns.append((self.now + BOT_RESPAWN, bot))
        source = _source_entity(killer) if source_entity is None else source_entity
        credit = self._credit_player(killer)
        if credit is not None:
            credit.stats.add(stats.ELIMINATIONS)
            self._kill_notice(credit, bot, source_entity=source)
        log.info("[game] %s: %s killed %s", self.match.label(), killer.name, bot.name)
        script = bot.body_script
        if script is None:
            return
        try:
            params = self._death_parameters(killer, critical, loadout, source_entity=source_entity)
            if isinstance(script, BotScript):
                tick = max(tick+bot.clock_offset, script.frame)
            event = script.die(tick, params, source)
            if bot.owner:
                from ow174.game import body_entities

                body_entities.queue_demech_return(self.match, bot)
        except Exception:
            if isinstance(script, BotScript):
                script.retire()
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
            events = script.advance_death(tick+bot.clock_offset, int(BOT_RESPAWN / TICK))
        except Exception:
            if isinstance(script, BotScript):
                script.retire()
            bot.body_script = None
            log.exception("[game] %s: %s's death script timer failed", self.match.label(), bot.name)
            return
        for event in events:
            for viewer in self.match.players:
                if viewer.client is not None:
                    viewer.client.queue_bot_death_update(bot.entity, event)

    def _died(self, killer, victim, critical: bool, loadout, *, source_entity=None) -> None:
        """A player died: the notices, his body script's "died" (the kill feed line), and the respawn."""
        from ow174.game import body_entities, sombra_emp

        body_entities.retire_source(self.match, victim.entity, victim.body, keep_bombs=True)
        self.clear_pins(victim.body)
        self.stuns.pop(victim.body, None)
        self.ana_sleeps.pop(victim.body, None)
        self.hooks.retire(victim.body)
        self.zarya_gravitons.release(victim)
        self.grasp_grants.pop(victim.body, None)
        self.grasp_shields.pop(victim.body, None)
        self.heal_modifiers.pop(victim.body, None)
        label = self.match.label()
        log.info("[game] %s: %s killed %s", label, killer.name, victim.name)
        victim.stats.add(stats.DEATHS)
        source = _source_entity(killer) if source_entity is None else source_entity
        credit = self._credit_player(killer)
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
                sombra_emp.retire_applications(component)
                params = self._death_parameters(killer, critical, loadout, source_entity=source_entity)
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
