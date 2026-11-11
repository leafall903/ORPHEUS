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
  command's aim from per-body FirstPerson height inputs (standing m_E29993A5,
  crouched m_816B830F; animated camera/muzzle transforms remain unverified), or
  a projectile (m_projectileSpeed) flown each tick.
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
from dataclasses import dataclass

from ow174.game import (
    barriers,
    bots,
    defenses,
    doomfist,
    mei_frost,
    meteor,
    observers,
    pools,
    projectiles,
    sombra_hack,
    stats,
    voicelines,
    world,
)
from ow174.game.bits import BitWriter
from ow174.game.collision import MOVER, World
from ow174.game.commands import Command
from ow174.game.mover import authored_mover_data, mover_data
from ow174.game.script import expr, graph, nodes, runtime
from ow174.game.script.bot import DIED, BotScript
from ow174.game.script.driver import ULT_CHARGE, ULT_COST, BodyScript, Snapshot
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

EYE = 1.55  # Legacy pack/NPC proxy; direct per-body FirstPerson inputs take precedence.
EYE_CROUCHED = {0x04000000000003CF: 1.085}  # Soldier's m_816B830F (DATA); others the default
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
ANA_SLEEP_EFFECT = 0x04A000000000026F
ANA_SLEEP_DURATION = 5.0
ANA_SLEEP_GRACE = 0.5
ANA_SLEEP_WAKE = 0.5
FLASHBANG_EFFECT = 0x04A0000000000079  # .053/0079 ->0228 ->224E
FLASHBANG_MODE_FLAG = 13408  # 0228 duration: flag set1.7s, otherwise0.8s


def eye_height(body: int, *, crouched: bool = False) -> float:
    """Authored FirstPerson stance offset; animated transforms remain open.

    Older packs and NPCs without a declared FirstPerson component retain the
    prior compatible proxy. Do not treat that fallback as native pose evidence.
    """
    data = authored_mover_data(body)
    value = None if data is None else data.crouch_eye_height if crouched else data.eye_height
    if value is not None:
        if type(value) not in (int, float) or not math.isfinite(value) or value <= 0:
            raise ValueError('invalid authored FirstPerson height')
        return value
    return EYE_CROUCHED.get(body, EYE_CROUCHED_DEFAULT) if crouched else EYE


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


def hand_cannon_directions(state, direction, owner, shot, activation):
    """Apply the authored radial pattern with the declared server seed binding."""
    from ow174.game.weapon_spread import radial_directions, seed_base

    if state.instance.graph.index != 0x0D0A or state.index != 2:
        return (direction,)
    cfg = state.node.field("m_5EC1EB38") or {}
    if cfg.get("$") != "STU_613BDDB0":
        return (direction,)
    spread = _number(state.evaluate(cfg.get("m_spread"), watch=False), 0.0)
    jitter = _number(state.evaluate(cfg.get("m_92308789"), watch=False), 0.0)
    count = int(_number(state.evaluate(state.node.field("m_numProjectilesPerShot"), watch=False), 1))
    rings = []
    for ring in cfg.get("m_0D30E40C") or ():
        radius = _number(state.evaluate(ring.get("m_6425FFB9"), watch=False), 0.0)
        pellets = int(_number(state.evaluate(ring.get("m_B19BD35D"), watch=False), 0))
        if not math.isfinite(radius) or radius < 0 or not 0 < pellets <= 64:
            return (direction,)
        rings.append((radius, pellets))
    if not (0 <= spread < 90 and 0 <= jitter < 90) or sum(n for _, n in rings) != count:
        return (direction,)
    base = seed_base(owner, state.instance.id, state.node.pos, activation[0])
    return radial_directions(direction, spread, jitter, rings, base, shot)


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


def capsule_visible(collision: World | None, origin, a, b, radius: float, *, blockers=()) -> bool:
    """Reconstructed visibility ray to the nearest capsule surface."""
    ab, ap = _sub(b, a), _sub(origin, a)
    length = _dot(ab, ab)
    share = 0.0 if length <= EPSILON else max(0.0, min(1.0, _dot(ap, ab) / length))
    toward = _sub(_along(a, ab, share), origin)
    distance = math.sqrt(_dot(toward, toward))
    if distance <= radius + EPSILON:
        return True
    reach = distance - radius
    hit = ray_cover(collision, origin, tuple(axis / distance for axis in toward), reach, blockers)
    return hit is None or hit + EPSILON >= reach


def ray_world(
    collision: World | None, origin, direction, length: float, category: int = MOVER,
) -> float | None:
    """The distance to the first static hit, for callers that only need occlusion."""
    hit = collision.cast_ray(origin, direction, length, category) if collision is not None else None
    return hit.distance if hit is not None else None


def ray_cover(collision, origin, direction, length, blockers):
    """Trace static cover plus the supplied active Cryo capsules."""
    closest = ray_world(collision, origin, direction, length)
    for a, b, radius in blockers:
        hit = ray_capsule(origin, direction, a, b, radius)
        if hit is not None and hit <= length and (closest is None or hit < closest):
            closest = hit
    return closest


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
        self.temporary_shields = 0.0
        self.temporary_cap = 0.0
        self.temporary_decay_at = 0.0
        self.temporary_decay_rate = 0.0
        self.temporary_updated_at = 0.0
        self.temporary_pool_id = max(self.ids.values()) + 1
        self.temporary_pool_used = False

    @property
    def dead(self) -> bool:
        return self.died_at is not None

    @property
    def total(self) -> float:
        return sum(self.now.values()) + self.temporary_shields

    def decay_temporary_shields(self, now: float) -> float:
        """Advance the tagged aggregate pool without frame-rate-dependent loss."""
        if now <= self.temporary_updated_at:
            return 0.0
        elapsed = max(0.0, now - max(self.temporary_updated_at, self.temporary_decay_at))
        self.temporary_updated_at = now
        lost = min(self.temporary_shields, elapsed * self.temporary_decay_rate)
        if lost > 0:
            self.temporary_shields -= lost
            self.changed.add(SHIELDS_POOL)
        return lost

    def add_temporary_shields(
        self, amount: float, cap: float, rate: float, delay: float, now: float,
        *, count_intrinsic: bool = False,
    ) -> float:
        """Declared aggregate of resource-tagged temporary shield pools.

        Healing does not refill these pools. A capped hit adds nothing and does
        not restart the decay delay, matching the room-before-PlayScript gates
        in Doomfist0DDD and Sigma1D03. Native separate pool identity remains open.
        """
        if self.dead or amount <= 0 or cap <= 0:
            return 0.0
        self.decay_temporary_shields(now)
        occupied = self.temporary_shields
        if count_intrinsic:
            occupied += self.now[SHIELDS_POOL]
        given = min(amount, max(0.0, cap - occupied))
        if given > 0:
            self.temporary_shields += given
            self.temporary_cap = cap
            self.temporary_decay_rate = rate
            self.temporary_decay_at = now + delay
            self.temporary_updated_at = now
            self.temporary_pool_used = True
            self.changed.add(SHIELDS_POOL)
        return given

    def damage(self, amount: float) -> float:
        """Take a hit from shields, then armour, then health (unverified, as OW1 did: what reaches armour
        while it has some is ARMOUR_BLOCK less, at most halved). Returns what the pools lost."""
        left = max(0.0, amount)
        taken = min(left, self.temporary_shields)
        if taken > 0:
            self.temporary_shields -= taken
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
        if self.temporary_pool_used:
            # Keep the ordinary shield slot intact. A zero-ID entry explicitly
            # retires the temporary slot when its final shields disappear.
            live = self.temporary_shields > 0
            parts[SHIELDS_POOL * POOLS + 1] = world.pool(
                self.temporary_pool_id if live else 0, SHIELDS_POOL,
                self.temporary_cap if live else 0.0, self.temporary_shields,
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
    critical: float = 1.0  # Hand Cannon's native null sentinel is retained as -1
    rings: tuple = ()  # (radius, scalar) of an explosion
    effect: int | None = None  # ModifyHealth's authored hit-effect resource

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
    # The 104319 Hand Cannon initializer preserves the null critical sentinel.
    # Projectile resolution currently applies only positive explicit bonuses.
    critical_default = -1.0 if (
        state.instance.graph.index == 0x0D0A and state.index == 2 and cfg.get("$") == "STU_00D9C7E1"
    ) else 1.0
    return HitDef(
        amount=amount,
        loadout=loadout.guid if isinstance(loadout, expr.Asset) else None,
        falloff_start=value("m_EAD7F104"),
        falloff_end=value("m_157E5BC5"),
        falloff_floor=value("m_72A81154"),
        critical=value("m_A872C70E", critical_default),
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
    source_body: int | None = None  # body that released or most recently reflected the projectile
    npc_direct_projectile: bool = False  # observed manual NPC direct-weapon projectile


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
        self.frost: dict[int, mei_frost.Frost] = {}
        self.ana_sleeps: dict[int, dict] = {}
        self.stuns: dict[int, int] = {}  # body identity -> exclusive world-tick deadline
        self.projectiles = projectiles.Projectiles(self)  # leases, confirmations, the Biotic Field
        self.observers = observers.Observers(match)  # the bodies' statescript for the other clients
        self.voice = voicelines.VoiceLines(match)  # the hero's spawn voice line (voicelines.py)
        self.punches = doomfist.Punches(self)
        self.slams = doomfist.Slams(self)
        self.meteors = meteor.Meteors(self)
        self.hand_cannon_barriers = barriers.HandCannonBarriers(self)
        self.pins: dict[int, int] = {}  # manual pin source -> victim body

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
        self.ana_sleeps.pop(player.body, None)
        self.frost.pop(player.body, None)
        self.clear_pins(player.body)
        self.punches.retire(player.body)
        self.slams.casts.pop(player.body, None)
        self.meteors.casts.pop(player.body, None)
        self.hand_cannon_barriers.states.pop(player.body, None)
        # The owner's leased projectiles and their observer copies retire with the old body.
        self.rockets[:] = [
            rocket for rocket in self.rockets if rocket.shooter is not player or rocket.shot is None
        ]
        self.respawns[:] = [(when, who) for when, who in self.respawns if who is not player]

    def record_replacement(self, body: int) -> None:
        """Keep the creation boundary independent of the rolling pose history."""
        self.replacement_ticks[body] = self.match.tick
        self.stuns.pop(body, None)
        self.ana_sleeps.pop(body, None)
        self.frost.pop(body, None)
        self.clear_pins(body)

    def stun_until(self, entity: int) -> int:
        sleep = self.ana_sleeps.get(entity)
        frost = self.frost.get(entity)
        return max(self.stuns.get(entity, 0), sleep["end"] if sleep else 0,
                   frost.until if frost else 0)

    def is_stunned(self, actor, *, tick: int | None = None) -> bool:
        entity = _source_entity(actor)
        return (entity in self.pins.values()
                or self.stun_until(entity) > (self.tick if tick is None else tick))

    def clear_pins(self, entity: int) -> None:
        for source, victim in list(self.pins.items()):
            if entity in (source, victim):
                del self.pins[source]

    def protected(self, entity: int) -> bool:
        return self.meteors.protected(entity) or defenses.protected(self.match, entity)

    def _hit_effect(self, target: Target, effect: int | None, source=None) -> None:
        """Resource-backed stun reconstruction; native immunity/presentation remain unverified."""
        if effect not in (FLASHBANG_EFFECT, ANA_SLEEP_EFFECT, mei_frost.SPRAY_EFFECT):
            return
        if self.vitals_of(target).dead or self.protected(target.entity):
            return
        if effect == mei_frost.SPRAY_EFFECT:
            if source is not None:
                mei_frost.hit(self, target, source)
            return
        if effect == ANA_SLEEP_EFFECT:
            self.ana_sleeps[target.entity] = {
                "start": self.tick,
                "end": self.tick + math.ceil(ANA_SLEEP_DURATION / TICK),
                "waking": False,
            }
            self._interrupt(target)
            return
        variables = graph.mode_variables(self.match.game_map.mode_guid) or {}
        duration = 1.7 if expr.truthy(variables.get(FLASHBANG_MODE_FLAG)) else 0.8
        deadline = self.tick + math.ceil(duration / TICK)
        self.stuns[target.entity] = max(deadline, self.stuns.get(target.entity, 0))
        self._interrupt(target)

    def _interrupt(self, target: Target) -> None:
        script = getattr(target.owner, "body_script", None)
        if script is None:
            return
        component = script.component
        if isinstance(target.owner, bots.Bot):
            from ow174.game import assassin, npc_melee

            assassin.bind(component.world, component, None)
            npc_melee.release_charge(self, target.owner, cancel=True)
        component.edges([(value, False) for value in tuple(component.held)])
        for instance in list(component.instances.values()):
            for state in list(instance.states.values()):
                if isinstance(state, nodes.Ability):
                    state.presses.clear()
                    state.stop()  # use authored exit/cooldown paths for running channels
        component.run_now()
        component.shots.clear()
        cast = self.meteors.casts.get(target.entity)
        if cast is not None and cast.impacted:
            # Stunned commands skip Meteor processing, so retire the interrupted recovery here.
            cast.finished = True

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
        defenses.advance(player, command)
        self.hand_cannon_barriers.sync(player)
        shots, script.component.shots[:] = list(script.component.shots), []
        health_reports, script.component.health_reports[:] = list(script.component.health_reports), []
        motion_reports, script.component.motion_reports[:] = list(script.component.motion_reports), []
        pool_reports, script.component.pool_reports[:] = list(script.component.pool_reports), []
        if self.is_dead(player) or self.is_stunned(player):
            return
        self.meteors.command(player, command)
        if self.meteors.protected(player.body):
            return
        if defenses.protected(self.match, player.body):
            return
        if command is not None:
            if pool_reports:
                own = next((t for t in self._targets() if t.entity == player.body), None)
                if own is not None:
                    vitals = self.vitals_of(own)
                    for amount, cap, rate, delay in pool_reports:
                        vitals.add_temporary_shields(amount, cap, rate, delay, self.now)
                    self._sync_body_health(player, vitals)
            for entities, velocity, _ in motion_reports:
                for entity in entities:
                    self._launch(entity, velocity)
            targets = {target.entity: target for target in self._enemies(player)}
            for entities, amount, loadout, time, velocity in health_reports:
                for entity in entities:
                    if (target := targets.get(entity)) is not None:
                        dealt = self._damage(player, target, amount, False, loadout, time // script.frame_ms)
                        if dealt > 0:
                            self._doomfist_shields(player, loadout)
                            if velocity is not None and not self.vitals_of(target).dead:
                                self._launch(entity, velocity)
            for state, number, time, activation in shots:
                try:
                    self._shoot(player, command, state, number, time, activation=activation)
                except Exception:
                    log.exception("[game] %s: %s's shot failed", self.match.label(), player.name)
        try:
            self._melee(player, command, script)
        except Exception:
            log.exception("[game] %s: %s's melee failed", self.match.label(), player.name)
        self.punches.command(player, command)
        self.slams.command(player, command)

    def script_targets(self, source: int, state) -> tuple[int, ...] | None:
        """Reconstruct Uppercut's authored sphere and default static visibility rays.

        Cryo cover uses the authored pet capsule. Native socket transforms and body tags remain separate.
        """
        if state.instance.graph.index != 0x0DD8 or state.index != 9:
            return None
        shooter = next((p for p in self.match.players if p.has_body and p.body == source), None)
        if shooter is None or self.is_dead(shooter):
            return ()
        centre = state.evaluate(state.node.field("m_05BE62DD"), watch=False)
        reference = state.evaluate(state.node.field("m_D59188D5"), watch=False)
        radius = _number(state.evaluate(state.node.field("m_radius"), watch=False), 0.0)
        if (not isinstance(centre, expr.Vec3) or not all(math.isfinite(v) for v in centre)
                or not isinstance(reference, expr.Vec3) or not all(math.isfinite(v) for v in reference)
                or not math.isfinite(radius) or radius <= 0):
            return ()
        centre = tuple(centre)
        origin = tuple(reference)
        collision = self._collision()
        cryo_cover = defenses.cryo_blockers(self.match.players, exclude=shooter)
        found = []
        for target in self._enemies(shooter):
            a, b = target.segment()
            if segment_distance(centre, a, b) > radius + target.radius:
                continue
            x, y, z = target.feet
            # The original default trace tries the lower body before its upper fallback.
            for fraction in (.3, .6):
                delta = _sub((x, y + fraction * target.height, z), origin)
                length = math.sqrt(_dot(delta, delta))
                heading = tuple(v / length for v in delta) if length > EPSILON else (0.0, 0.0, 1.0)
                hit = ray_cover(collision, origin, heading, length, cryo_cover)
                if hit is not None and hit < length:
                    continue
                found.append(target.entity)
                break
        return tuple(found)

    def _doomfist_shields(self, player, loadout) -> None:
        """Apply 0DDD's resource-derived gain/cap/delay/decay to a confirmed hit.

        The tagged-pool aggregate is reconstructed. The native damage message,
        filter/tag transport and 0DF3 PlayScript lifetime are not implemented.
        """
        if player.hero.guid != 0x02E000000000012F or player.body_script is None:
            return
        if loadout not in (0x0B90000000000106, 0x0B90000000000107, 0x0B90000000000108,
                           0x0B9000000000011F):
            return
        passive = next((i for i in player.body_script.component.instances.values()
                        if i.graph.index == 0x0DDD), None)
        target = next((t for t in self._targets() if t.owner is player), None)
        if passive is None or target is None:
            return
        gain_node = passive.graph.node(21 if loadout == 0x0B90000000000108 else 20)
        gain = _number(passive.evaluate(gain_node.field("m_value")), 0.0)
        cap_var = passive.vars.get(11218)
        cap = _number(cap_var.value() if cap_var is not None else None, 0.0)
        decay = _number(
            passive.state(1).evaluate(passive.graph.node(3).field("m_modifyHealth.m_amount")), 0.0,
        )
        delay = _number(passive.state(0).evaluate(passive.graph.node(2).field("m_timeout")), 0.0)
        if not all(math.isfinite(v) and v >= 0 for v in (gain, cap, decay, delay)):
            return
        vitals = self.vitals_of(target)
        # 0DDD and 0DF3 use the native +0x70 query, which sums current shield pools by kind.
        vitals.add_temporary_shields(gain, cap, decay, delay, self.now, count_intrinsic=True)
        self._sync_body_health(player, vitals)

    def script_health_effect(self, source: int, state) -> None:
        """Uppercut's authored 50-damage effect, once per State activation.

        Victim horizontal7/vertical14 velocity mapping is reconstructed. These
        magnitudes are authored ModifyHealth fields; native air-control and
        impulse-combination semantics remain open.
        """
        if state.instance.graph.index != 0x0DD8 or state.index != 10:
            return
        component = state.instance.component
        if source != component.entity or component.owner_dead:
            return
        targets = state.evaluate(state.node.field("m_FA3ECC43"), watch=False)
        targets = targets if isinstance(targets, tuple) else (targets,)
        entities = tuple(dict.fromkeys(t.id for t in targets if isinstance(t, expr.Entity)))
        definition = hit_def(state, {"m_modifyHealth": state.node.field("m_modifyHealth")})
        if entities and definition is not None and definition.amount > 0:
            horizontal = _number(state.evaluate(state.node.field("m_modifyHealth.m_E33EFEE1")), 0.0)
            vertical = _number(state.evaluate(state.node.field("m_modifyHealth.m_583C208B")), 0.0)
            shooter = next((p for p in self.match.players if p.has_body and p.body == source), None)
            velocity = None
            if shooter is not None and all(math.isfinite(v) and v >= 0 for v in (horizontal, vertical)):
                dx, _, dz = aim(shooter.mover.yaw, 0)
                velocity = (dx * horizontal, vertical, dz * horizontal)
            component.health_reports.append(
                (entities, definition.amount, definition.loadout, state.now, velocity),
            )

    def script_velocity_action(self, source: int, instance, node) -> None:
        """Reconstruct Uppercut's first lift and Slam's forward ground hop.

        Native node1 direction rotation and node8/node15's later modifiers are
        not restored. In particular PhysicsExplosion20 is NOT used as a jump.
        """
        if instance.component.owner_dead:
            return
        if instance.graph.index == 0x0B27 and node.pos == 75:
            shooter = next((p for p in self.match.players if p.has_body and p.body == source), None)
            if shooter is not None:
                # Ground-hop direction is a declared forward/up approximation
                # of native0762B33A interpolation, not a native getter result.
                speed = float(node.field('m_BC5E91CF[2].m_9B7A63EA.m_expression.m_D99EF254[0]'))
                scalar = float(instance.evaluate(node.field('m_BC5E91CF[2].m_9B7A63EA.m_configVars[1]')))
                dx, _, dz = aim(shooter.mover.yaw, 0)
                length = math.sqrt(1 + .3 * .3)
                velocity = tuple(v * speed * scalar / length for v in (dx, .3, dz))
                instance.component.motion_reports.append(((source,), velocity, instance.component.now))
            return
        if instance.graph.index != 0x0DD8 or node.pos != 1:
            return
        speed = _number(node.field("m_BC5E91CF[2].m_9B7A63EA.m_expression.m_D99EF254[0]"), 0.0)
        if math.isfinite(speed) and speed > 0:
            instance.component.motion_reports.append(((source,), (0.0, speed, 0.0), instance.component.now))

    def _launch(self, entity: int, velocity: tuple) -> None:
        for target in self._targets():
            if target.entity != entity or self.vitals_of(target).dead:
                continue
            if isinstance(target.owner, bots.Bot):
                target.owner.launch(velocity, self.match.tick, self._collision())
            else:
                target.owner.mover.launch(velocity)
            return

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
            view_tick = command.frame - command.view_delay(player.quanta) * 1e6 / player.quanta
            cryo_cover = defenses.cryo_blockers(self.match.players, exclude=player)
            for target in self._enemies(player):
                shown = self.seen(target, view_tick)
                if shown is None:
                    continue
                a, b = shown.segment()
                if (segment_distance(centre, a, b) - shown.radius <= radius and amount > 0.0
                        and capsule_visible(self._collision(), self._eye(player), a, b, shown.radius,
                                            blockers=cryo_cover)):
                    if defenses.blocks_melee(self, player, target, state):
                        continue
                    self._damage(player, target, amount, False, loadout, command.frame)

    def update(self, now: float, tick: int, frames: bool = True) -> None:
        """Each tick: keep where the bodies are, fly the rockets, respawn, send health that changed. The
        other players' body frames only on the ticks that send (`frames`)."""
        if self.match.collision_world is None:
            return
        defenses.expire_cryo(self.match.players, tick)
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
        for at in range(first, tick + 1):
            self.now, self.tick = now - (tick - at) * TICK, at
            self.punches.update()
            self.hand_cannon_barriers.update()
            for target in self._targets():
                vitals = self.vitals.get(target.entity)
                if vitals is not None and vitals.decay_temporary_shields(self.now) > 0:
                    self._sync_body_health(target.owner, vitals)
            for rocket in list(self.rockets):
                if at == tick or rocket.last_tick is not None:
                    self._fly(rocket)
            self._bot_weapons(at)
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
        for entity, sleep in list(self.ana_sleeps.items()):
            if sleep["end"] <= tick or entity not in current:
                del self.ana_sleeps[entity]
        mei_frost.update(self)
        for table in (self.history, self.vitals, self.replacement_ticks, self.stuns, self.frost):
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
            eye = eye_height(player.hero.body, crouched=crouched)
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

    def _enemies(self, shooter) -> list[Target]:
        free_for_all = self.match.game_map.free_for_all
        source = _source_entity(shooter)
        found = []
        for target in self._targets():
            if self.protected(target.entity):
                continue
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
                # Input release alone cannot stop every already-running weapon
                # phase. Reconstructed control also suppresses new emissions;
                # projectiles released before the hit keep their own lifetime.
                allowed = allowed and not bot.controlled
                snapshot = script.advance_live(tick, bot.position)
                from ow174.game import assassin, npc_melee

                assassin.refresh_control(script.component.world, script.component)
                npc_melee.refresh_charge_pin(self, bot)
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
                script.component.world.manual_target = None
                script.component.world.match = None
                bot.body_script = None
                log.exception("[game] %s: %s's weapon script failed", self.match.label(), bot.name)

    def _eye(self, player) -> tuple:
        x, y, z = player.mover.position
        crouched = player.mover.crouched
        height = eye_height(player.hero.body, crouched=crouched)
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
        npc_barrier = npc and (state.instance.graph.index, node.pos) in (
            (0x102E, 60), (0x13EB, 24), (0x1122, 6), (0x114F, 47),
        )
        speed = field("m_projectileSpeed")
        if speed:
            lifetime = field("m_projectileLifetime", 5.0) or 5.0
            reach = field("m_FEC435C6")
            if reach:
                lifetime = min(lifetime, reach / speed)
            blast = hit_def(state, node.field("m_D2D11CE9"))
            entity_def = (node.field("m_projectileEntity") or {}).get("m_entityDef")
            definition = state.evaluate(entity_def, watch=False) if entity_def is not None else None
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
                owner = _source_entity(shooter)
                headings = hand_cannon_directions(state, direction, owner, number, activation)
                for pellet, heading in enumerate(headings):
                    self.rockets.append(
                        Rocket(
                            shooter, shooter.team, origin, heading, speed, lifetime, direct, blast,
                            shot if pellet == 0 else None,
                            last_tick=self.tick if npc else None,
                            source_body=_source_entity(shooter),
                            npc_direct_projectile=npc_barrier,
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
        self._hitscan(shooter, origin, direction, reach, direct, shot_frame, view_tick,
                      npc_ray=npc_barrier, world_tick=self.tick if npc else None)

    def _hitscan(self, shooter, origin, direction, reach, direct, shot_frame, view_tick, *,
                 npc_ray=False, world_tick=None, travelled=0.0, reflections=0):
        """Resolve a direct ray and the scoped manual NPC defense policy."""
        best = None
        for target in self._enemies(shooter):
            shown = self.seen(target, view_tick)
            if shown is None:
                continue
            a, b = shown.segment()
            t = ray_capsule(origin, direction, a, b, shown.radius)
            if t is not None and t <= reach and (best is None or t < best[0]):
                best = (t, target, shown)
        if best is None and not npc_ray:
            return
        limit = best[0] if best is not None else reach
        wall = ray_world(self._collision(), origin, direction, limit)
        if npc_ray:
            visible = min(limit, wall) if wall is not None else limit
            ray = (shooter, shooter.team, origin, direction, visible)
            shield = self.hand_cannon_barriers.ray_contact(*ray)
            reflected = defenses.ray_interception(self, *ray, absorbing=False)
            absorbed = defenses.ray_interception(self, *ray, absorbing=True)
            contacts = [(hit[0], priority, hit) for priority, hit in enumerate((shield, reflected, absorbed))
                        if hit is not None and (wall is None or hit[0] < wall)]
            if contacts:
                distance, priority, contact = min(contacts)
                amount = direct.amount * direct.scale(travelled + distance)
                if priority == 0:
                    self.hand_cannon_barriers.damage(contact, amount)
                elif priority == 2:
                    defenses.absorbed(contact[3], amount)
                elif reflections < 8 and distance < reach:
                    _, owner, heading, _ = contact
                    point = _along(origin, direction, distance)
                    self._hitscan(owner, point, heading, reach - distance, direct, shot_frame, self.tick,
                                  npc_ray=True, world_tick=world_tick, travelled=travelled + distance,
                                  reflections=reflections + 1)
                return
        if best is None:
            return
        t, target, shown = best
        if wall is not None and wall < t:
            return
        hit_y = origin[1] + direction[1] * t
        critical = hit_y >= shown.feet[1] + shown.eye - HEAD_BELOW
        amount = direct.amount * direct.scale(travelled + t) * (direct.critical if critical else 1.0)
        self._damage(
            shooter, target, amount, critical and direct.critical > 1.0, direct.loadout, shot_frame,
            world_tick=world_tick,
        )
        self._hit_effect(target, direct.effect, shooter)

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
        self._sweep_rocket(rocket, duration)

    def _sweep_rocket(self, rocket: Rocket, duration: float, bounces: int = 0) -> None:
        # Sweep the remaining flight even when the range or lifetime ends inside this tick.
        step = rocket.speed * duration
        best = None
        for target in self._enemies(rocket.shooter):
            a, b = target.segment()
            t = ray_capsule(rocket.position, rocket.direction, a, b, target.radius)
            if t is not None and t <= step and (best is None or t < best[0]):
                best = (t, target)
        wall = ray_world(self._collision(), rocket.position, rocket.direction, step)
        if wall is not None and (best is None or wall < best[0]):
            best = (wall, None)
        reflected = defenses.reflection(self, rocket, step)
        absorbed = defenses.absorption(self, rocket, step)
        barrier = self.hand_cannon_barriers.contact(rocket, step)
        if (barrier is not None and (best is None or barrier[0] <= best[0])
                and (reflected is None or barrier[0] <= reflected[0])
                and (absorbed is None or barrier[0] <= absorbed[0])):
            rocket.flown += barrier[0] / rocket.speed
            self._landed(rocket)
            self.hand_cannon_barriers.damage(
                barrier, rocket.direct.amount * rocket.direct.scale(rocket.flown * rocket.speed),
            )
            return
        if (absorbed is not None and (best is None or absorbed[0] <= best[0])
                and (reflected is None or absorbed[0] < reflected[0])):
            distance, _, state = absorbed
            rocket.flown += distance / rocket.speed
            direct = rocket.direct
            self._landed(rocket)
            defenses.absorbed(state, direct.amount * direct.scale(rocket.flown * rocket.speed))
            return
        if reflected is not None and (best is None or reflected[0] <= best[0]):
            distance, owner, direction = reflected
            # Preserve actual flight age/range across ownership transfer. The
            # tiny separation is charged to both time and distance; overlapping
            # reflectors cannot create a zero-time infinite traversal.
            separation = min(.0001, max(0.0, step - distance))
            used = min(duration, (distance + separation) / rocket.speed)
            point = _along(rocket.position, rocket.direction, distance)
            rocket.position = _along(point, direction, separation)
            rocket.shooter, rocket.team, rocket.direction = owner, owner.team, direction
            rocket.source_body = owner.body
            rocket.flown += used
            rocket.left -= used
            if rocket.left <= 0 or bounces >= 8:
                self._landed(rocket)
            elif duration > used:
                self._sweep_rocket(rocket, duration - used, bounces + 1)
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
        if struck is not None and rocket.direct is not None and rocket.direct.amount > 0.0:
            direct = rocket.direct
            critical = point[1] >= struck.feet[1] + struck.eye - HEAD_BELOW and direct.critical > 1.0
            amount = direct.amount * direct.scale(rocket.flown * rocket.speed)
            if critical:
                amount *= direct.critical
            self._damage(
                rocket.shooter, struck, amount, critical, direct.loadout, self.tick,
                world_tick=self.tick, source_body=rocket.source_body,
            )
            self._hit_effect(struck, rocket.direct.effect, rocket.shooter)
        blast = rocket.blast
        if blast is None or blast.amount <= 0.0:
            return
        for target in self._enemies(rocket.shooter):
            a, b = target.segment()
            distance = max(0.0, segment_distance(point, a, b) - target.radius)
            scalar = blast.splash(distance)
            # Start just outside an impact wall, on the projectile's approach side.
            visible_from = _along(point, rocket.direction, -0.0001) if wall is not None else point
            if scalar > 0.0 and capsule_visible(self._collision(), visible_from, a, b, target.radius):
                self._damage(
                    rocket.shooter, target, blast.amount * scalar, False, blast.loadout, self.tick,
                    world_tick=self.tick, source_body=rocket.source_body,
                )
                self._hit_effect(target, blast.effect, rocket.shooter)

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

    def _collision(self):
        from ow174.game.collision import CollisionError

        world = self.match.collision_world
        if world is None:
            raise CollisionError("match collision is not ready")
        return world

    # --- damage and death --------------------------------------------------------------------------

    def _doomfist_damage_charge(self, shooter, target: Target, dealt: float, loadout, source_body) -> None:
        """Reconstruct one charge point per permanent pool point lost to a living Doomfist."""
        hero = getattr(shooter, "hero", None)
        script = getattr(shooter, "body_script", None)
        if (getattr(hero, "guid", None) != doomfist.HERO or not isinstance(script, BodyScript)
                or script.retired or script.component.fault is not None or shooter.gone
                or self.is_dead(shooter) or loadout == meteor.LOADOUT
                or (source_body is not None and source_body != shooter.body)
                or target.owner is shooter or dealt <= 0
                or (not self.match.game_map.free_for_all and target.team == shooter.team)
                or any(state.flags & nodes.Ability.RUNNING for state in script.ultimates)):
            return
        cost = expr.to_float(script.component.entity_var(ULT_COST).value())
        charge = expr.to_float(script.component.entity_var(ULT_CHARGE).value())
        if 0 <= charge < cost:
            script.set_ult_charge(min(cost, charge + dealt))

    def _damage(
        self, shooter, target: Target, amount: float, critical: bool, loadout, frame: int,
        *, world_tick: int | None = None, source_body: int | None = None,
    ) -> float:
        vitals = self.vitals_of(target)
        if vitals.dead or amount <= 0.0 or self.protected(target.entity):
            return 0.0
        temporary_before = vitals.temporary_shields
        dealt = vitals.damage(amount)
        if dealt <= 0.0:
            return 0.0
        sombra_hack.cancel_hack(target.owner)
        sleep = self.ana_sleeps.get(target.entity)
        if (sleep and not sleep["waking"] and self.tick < sleep["end"]
                and (self.tick - sleep["start"]) * TICK > ANA_SLEEP_GRACE):
            sleep["end"] = min(sleep["end"], self.tick + math.ceil(ANA_SLEEP_WAKE / TICK))
            sleep["waking"] = True
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
            permanent_loss = dealt - (temporary_before - vitals.temporary_shields)
            self._doomfist_damage_charge(shooter, target, permanent_loss, loadout, source_body)
        victim = target.owner
        if killed and isinstance(victim, bots.Bot):
            # The NPC death script owns its terminal health change and death event ordering.
            self._bot_died(shooter, victim, critical, loadout, vitals.death_tick)
            return dealt
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
        self.stuns.pop(bot.entity, None)
        self.ana_sleeps.pop(bot.entity, None)
        self.frost.pop(bot.entity, None)
        self.clear_pins(bot.entity)
        bot.died(tick)
        if isinstance(bot.body_script, BotScript):
            from ow174.game import assassin

            assassin.bind(bot.body_script.component.world, bot.body_script.component, None)
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
        self.ana_sleeps.pop(victim.body, None)
        self.frost.pop(victim.body, None)
        self.clear_pins(victim.body)
        self.punches.dashes.pop(victim.body, None)
        cast = self.meteors.casts.get(victim.body)
        if cast is not None:
            cast.finished = True
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
