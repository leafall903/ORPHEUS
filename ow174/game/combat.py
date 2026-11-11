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
from dataclasses import dataclass

from ow174.game import (
    assassin,
    ball,
    ball_mines,
    barriers,
    bots,
    deflection,
    npc_context,
    observers,
    pools,
    projectiles,
    sombra_emp,
    stats,
    transcendence,
    voicelines,
    world,
)
from ow174.game.bits import BitWriter
from ow174.game.collision import MOVER, World
from ow174.game.commands import Command
from ow174.game.mover import FELL, Mover, mover_data
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
# 104319 STUFirstPersonComponent: m_E29993A5 standing, m_816B830F crouched.
EYE_STANDING = {
    0x0400000000000225: 1.2, 0x04000000000002A3: 1.425,
    0x040000000000200B: 1.83, 0x0400000000002AB1: 2.1,
}
EYE_CROUCHED = {
    0x0400000000000225: 0.84, 0x04000000000003CF: 1.085,
    0x04000000000002A3: 0.9975, 0x040000000000200B: 1.5, 0x0400000000002AB1: 1.75,
}
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
PHASE_STATES = {
    0x02E0000000000002: (0x20, 5),  # Reaper Wraith Form
    0x02E00000000001A2: (0xF7C, 4),  # Moira Fade
}
CRYO_STATES = {barriers.MEI: (0x5FE, 35)}
DAMAGE_IMMUNITY_STATES = PHASE_STATES | CRYO_STATES | {
    0x02E0000000000020: (0x17B, 31),  # Zenyatta Transcendence
}
FLASHBANG_EFFECT = 0x04A0000000000079  # .053/0079 ->0228 ->224E
SLEEP_EFFECT = 0x04A000000000026F  # 0BDD -> 0BDE/node51 -> 224E/676B
ANA_GRENADE_EFFECT = 0x04A0000000000267  # 0B97/node1 -> 0267 -> 0C71 -> 0B99/0C72
ANA_GRENADE_TICKS = 250
ANA_GRENADE_HEAL = 100.0
ANA_RIFLE_DURATION = 0.6  # 0B95 treatment window
ANA_HEAL_BOOST = 1.5
SLEEP_DURATION = 5.0
SLEEP_DAMAGE_GRACE = 0.5
SLEEP_WAKE = 0.5
PILE_AIR_LOCK = 0.5  # Blizzard February 2020 live Piledriver air-control duration
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
        # Adaptive Shield is a separate, non-healable pool, consumed before permanent shields.
        self.temporary_shields = 0.0
        self.temporary_maximum = 0.0
        self.temporary_registered = False
        self.adaptive_activation: tuple[int, int] | None = None
        self.adaptive_until = 0
        self.grasp_activation: tuple[int, int] | None = None
        self.grasp_decay: int | None = None
        self.healing_block: tuple[tuple[int, int], ...] = ()
        self.healing_boost: tuple[tuple[int, int], ...] = ()
        self.nano_until = 0

    def apply_biotic_grenade(self, tick: int, *, allied: bool) -> None:
        spans = self.healing_boost if allied else self.healing_block
        start = tick
        if spans and spans[-1][1] >= tick:
            start = spans[-1][0]
            spans = spans[:-1]
        spans += ((start, tick + ANA_GRENADE_TICKS),)
        if allied:
            self.healing_boost = spans
        else:
            self.healing_block = spans

    def cleanse_healing_block(self, tick: int) -> None:
        """Keep the blocked part of any pending healing interval before the cleanse."""
        self.healing_block = tuple(
            (start, min(end, tick)) for start, end in self.healing_block if start < tick
        )

    @property
    def dead(self) -> bool:
        return self.died_at is not None

    @property
    def total(self) -> float:
        return sum(self.now.values()) + self.temporary_shields

    @property
    def maximum(self) -> float:
        return sum(self.most.values()) + self.temporary_maximum

    def set_temporary_shields(self, amount: float) -> None:
        amount = max(0.0, float(amount))
        self.temporary_registered = True
        self.temporary_shields = self.temporary_maximum = amount
        self.changed.add(SHIELDS_POOL)

    def drain_shields(self, amount: float) -> float:
        """Spend temporary and base shields without spilling into other pools."""
        temporary = min(amount, self.temporary_shields)
        permanent = min(amount - temporary, self.now[SHIELDS_POOL])
        if temporary or permanent:
            self.temporary_shields -= temporary
            self.now[SHIELDS_POOL] -= permanent
            self.changed.add(SHIELDS_POOL)
        return temporary + permanent

    def damage(self, amount: float, *, bypass_armour=False) -> float:
        """Take a hit from shields, then armour, then health (unverified, as OW1 did: what reaches armour
        while it has some is ARMOUR_BLOCK less, at most halved). Returns what the pools lost."""
        left = max(0.0, amount)
        taken = min(left, self.temporary_shields)
        if taken:
            self.temporary_shields -= taken
            self.changed.add(SHIELDS_POOL)
            left -= taken
        for kind in (SHIELDS_POOL, ARMOUR_POOL, HEALTH_POOL):
            if left <= 0.0:
                break
            if kind == ARMOUR_POOL and self.now[kind] > 0.0 and not bypass_armour:
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
        if self.temporary_registered:
            # Keep slot 0 for a hero's permanent shields. Send the zero pool on expiry too.
            parts[SHIELDS_POOL * POOLS + 1] = world.pool(
                max(self.ids.values()) + 1, SHIELDS_POOL,
                self.temporary_maximum, self.temporary_shields,
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
    biotic_rifle: bool = False

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
        # Ball's omitted native scalar is reconstructed as the build's common
        # 2x bullet critical rule. Explicit graph scalars still take precedence.
        critical=value("m_A872C70E", 2.0 if isinstance(loadout, expr.Asset)
                       and loadout.guid == 0x0B90000000000189 else 1.0),
        rings=tuple(sorted(rings)),
        effect=effect.guid if isinstance(effect, expr.Asset) else None,
        biotic_rifle=state.instance.graph.index == 0x0B94 and state.index in (5, 44),
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
    critical_allowed: bool = True  # discrete form policy, retained at each view-time pose

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
    deflectable: bool = False
    reflections: int = 0
    gravity: float = 0.0
    reach: float | None = None
    travelled: float = 0.0
    source_scale: float = 1.0
    npc_launch: bool = False  # original clock, independent of reflected damage ownership


@dataclass
class RifleDose:
    shooter: object
    source: int
    recipient: int
    amount: float
    start: float
    healing: bool
    loadout: int | None
    reflected: bool


class Combat:
    """The match's health, hits, deaths and respawns."""

    def __init__(self, match) -> None:
        self.match = match
        self.vitals: dict[int, Vitals] = {}  # body or bot entity -> its health
        self.history: dict[int, deque] = {}  # entity -> (tick, feet, height, eye, critical_allowed)
        self.replacement_ticks: dict[int, int] = {}  # replacement body -> authoritative world tick
        self.rockets: list[Rocket] = []
        self.rifle_doses: list[RifleDose] = []
        self.respawns: list[tuple[float, object]] = []  # (when, a player or a bot)
        self.next_bot = bots.BOT_ENTITY + len(match.bots)
        self.now = 0.0
        self.tick = 0
        self.melee_seen: dict[int, dict[tuple[int, int], int]] = {}  # body -> counters by instance/state
        self.stuns: dict[int, int] = {}  # body identity -> exclusive world-tick deadline
        self.sleeps: dict[int, tuple[int, int]] = {}  # body identity -> (start, exclusive deadline)
        self.pile_air_locks: dict[int, tuple[int, int]] = {}
        self.pins: dict[int, int] = {}  # manual Assassin source body -> controlled victim body
        self.npc_impulses: dict[int, tuple[Mover, int]] = {}  # live NPC collision motion and last world tick
        self.ball_grapples: dict[int, ball.Grapple] = {}
        self.grapple_seen: dict[int, tuple[int, int]] = {}
        self.ball_contacts: dict[int, set[int]] = {}
        self.mines = ball_mines.Minefield(self)
        self.barriers: dict[int, barriers.Barrier] = {}
        self.next_barrier = barriers.FIRST_ENTITY
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
        barriers.retire_owner(self, player)
        self.vitals.pop(player.body, None)
        self.rifle_doses[:] = [dose for dose in self.rifle_doses if dose.recipient != player.body]
        self.history.pop(player.body, None)
        self.replacement_ticks.pop(player.body, None)
        self.melee_seen.pop(player.body, None)
        self.stuns.pop(player.body, None)
        self.sleeps.pop(player.body, None)
        self.pile_air_locks.pop(player.body, None)
        self.clear_pins(player.body)
        self.ball_grapples.pop(player.body, None)
        self.grapple_seen.pop(player.body, None)
        self.ball_contacts.pop(player.body, None)
        self.mines.retire(player.body)
        # Leased shots and reflected flight belong to the retiring body.
        self.rockets[:] = [
            rocket for rocket in self.rockets
            if rocket.shooter is not player
            or (rocket.shot is None and not rocket.reflections
                and (rocket.direct is None
                     or (rocket.direct.effect != SLEEP_EFFECT and not rocket.direct.biotic_rifle)))
        ]
        self.respawns[:] = [(when, who) for when, who in self.respawns if who is not player]

    def record_replacement(self, body: int) -> None:
        """Keep the creation boundary independent of the rolling pose history."""
        self.replacement_ticks[body] = self.match.tick
        self.stuns.pop(body, None)
        self.sleeps.pop(body, None)
        self.pile_air_locks.pop(body, None)
        self.clear_pins(body)

    def is_stunned(self, actor, *, tick: int | None = None) -> bool:
        entity = _source_entity(actor)
        current = self.tick if tick is None else tick
        return entity in self.pins.values() or self.timed_control_until(entity) > current

    def timed_control_until(self, entity: int) -> int:
        return max(self.stuns.get(entity, 0), self.sleeps.get(entity, (0, 0))[1])

    def advance_command_control(self, player, frame: int, tick: int) -> bool:
        """Apply control on its command frames while keeping live control authoritative for future input."""
        script = player.body_script
        controls = getattr(script, "timed_control_commands", None)
        locked = frame >= tick and self.timed_control_until(player.body) > tick
        if player.body in self.pins.values():
            # An older snapshot may have a live pin without command history.
            locked |= frame >= tick or not controls or assassin.PIN_MESSAGE not in controls
        if controls is None:
            return locked
        interrupt = False
        pin_starts = False
        for effect, windows in tuple(controls.items()):
            end = windows[0][1]
            if end is not None and end <= frame:
                windows = tuple(window for window in windows if window[1] is None or frame < window[1])
                if not windows:
                    del controls[effect]
                    continue
                controls[effect] = windows
            locked |= windows[0][0] <= frame
            starts = windows[0][0] == frame
            interrupt |= starts
            pin_starts |= starts and effect == assassin.PIN_MESSAGE and windows[0][1] is None
        if not controls:
            del script.timed_control_commands
        if interrupt:
            self._interrupt(player)
        if pin_starts:
            source = next((owner for owner, body in self.pins.items() if body == player.body), None)
            if source is not None:
                assassin.attach_victim(source, player)
        return locked

    def _record_command_control(self, target: Target, effect: int, deadline: int | None) -> None:
        script = getattr(target.owner, "body_script", None)
        if script is None or isinstance(target.owner, bots.Bot):
            return
        controls = getattr(script, "timed_control_commands", None)
        if controls is None:
            controls = script.timed_control_commands = {}
        windows = controls.get(effect, ())
        start = self.tick
        if deadline is not None and windows and windows[-1][1] is not None and start <= windows[-1][1]:
            start = windows[-1][0]
            windows = windows[:-1]
        controls[effect] = (*windows, (start, deadline))

    def air_steering_locked(self, player, tick: int) -> bool:
        start, deadline = self.pile_air_locks.get(player.body, (0, 0))
        return start <= tick < deadline and player.mover.airborne

    def clear_pins(self, entity: int) -> None:
        for source, victim in list(self.pins.items()):
            if entity in (source, victim):
                self.release_pin(source, animate=False)
        assassin.detach_victim(self.match, None, entity)

    def release_pin(self, source: int, *, animate=True) -> None:
        victim = self.pins.pop(source, None)
        if victim is not None:
            next_source = next((owner for owner, body in self.pins.items() if body == victim), None)
            if next_source is None:
                player = next((p for p in self.match.players if p.body == victim), None)
                controls = getattr(getattr(player, "body_script", None), "timed_control_commands", {})
                windows = controls.get(assassin.PIN_MESSAGE, ())
                if windows and windows[-1][1] is None:
                    start, _ = windows[-1]
                    controls[assassin.PIN_MESSAGE] = (*windows[:-1], (start, max(start, self.tick)))
            assassin.detach_victim(self.match, source, victim, animate=animate, next_source=next_source)

    def _hit_effect(self, target: Target, effect: int | None) -> None:
        """Resource-backed stun reconstruction; native immunity/presentation remain unverified."""
        if (effect not in (FLASHBANG_EFFECT, SLEEP_EFFECT) or self.vitals_of(target).dead
                or isinstance(target.owner, ball_mines.Mine)
                or self._has_protection(target, CRYO_STATES)):
            return
        if effect == SLEEP_EFFECT:
            if self._has_protection(target, DAMAGE_IMMUNITY_STATES):
                return
            deadline = self.tick + math.ceil(SLEEP_DURATION / TICK)
            self.sleeps[target.entity] = (self.tick, deadline)
            self._record_command_control(target, effect, deadline)
            self._interrupt(target.owner, impact_frame=self.tick)
            return
        variables = graph.mode_variables(self.match.game_map.mode_guid) or {}
        duration = 1.7 if expr.truthy(variables.get(FLASHBANG_MODE_FLAG)) else 0.8
        deadline = self.tick + math.ceil(duration / TICK)
        deadline = max(deadline, self.stuns.get(target.entity, 0))
        self.stuns[target.entity] = deadline
        self._record_command_control(target, effect, deadline)
        self._interrupt(target.owner, impact_frame=self.tick)

    def _interrupt(self, owner, *, impact_frame=None) -> None:
        is_bot = isinstance(owner, bots.Bot)
        if not is_bot:
            if impact_frame is None:
                ball.cancel_grapple(self, owner)
            else:
                ball.interrupt_grapple(self, owner, impact_frame)
            self.ball_contacts.pop(owner.body, None)
        script = getattr(owner, "body_script", None)
        if script is None:
            return
        component = script.component
        if is_bot:
            assassin.bind(component.world, component, None)
        component.edges([(value, False) for value in tuple(component.held)])
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

    def command(self, player, command: Command | None, *, stunned: bool | None = None) -> None:
        """Consume reports. Minefield can deploy at the last pose during command gaps."""
        if self.match.collision_world is None:
            return
        script = player.body_script
        if script is None:
            return
        shots, script.component.shots[:] = list(script.component.shots), []
        if self.is_dead(player):
            ball.cancel_grapple(self, player)
            return
        # Existing timed pools still expire while control suppresses new attacks.
        self._adaptive_shield(player, script)
        if stunned is None:
            stunned = self.is_stunned(player)
        if stunned:
            ball.cancel_grapple(self, player)
            return
        ball.update_grapple(self, player, command, aim(*command.aim()) if command is not None else None)
        self._piledriver(player, command)
        self._fireball(player, command)
        for state, number, time, activation in shots:
            try:
                self._shoot(player, command, state, number, time, activation=activation)
            except Exception:
                log.exception("[game] %s: %s's shot failed", self.match.label(), player.name)
        try:
            self._melee(player, command, script)
        except Exception:
            log.exception("[game] %s: %s's melee failed", self.match.label(), player.name)

    def _adaptive_shield(self, player, script) -> None:
        """Back Ball's authored 1566/st6 lifetime with an actual health pool.

        Reconstructed target search: live enemy capsules within st9's radius of the owner's eye,
        sampled once when st6 activates. The graph supplies the amounts, cooldown and expiry.
        This host search is specific to Adaptive Shield.
        """
        if player.hero.guid != 0x02E00000000001CA:
            return
        instance = next((i for i in script.component.instances.values() if i.graph.index == 0x1566), None)
        if instance is None:
            return
        state = instance.states.get(6)
        own = next((t for t in self._targets() if t.owner is player), None)
        if own is None:
            return
        vitals = self.vitals_of(own)
        if state is None or not state.active:
            if vitals.temporary_maximum:
                vitals.adaptive_until = 0
                vitals.set_temporary_shields(0.0)
                self._sync_body_health(player, vitals)
            return
        activation = (instance.id, state.counter)
        if vitals.adaptive_activation == activation:
            return
        # 1566 schema: #2E55 radius, #3B95 per enemy, #3BAA base. These are authored inputs.
        def value(key):
            return _number(instance.vars[key].value(), 0.0) if key in instance.vars else 0.0

        vitals.adaptive_activation = activation
        # State 11 chases the authored lifetime (#567) to zero. The body VM
        # consumes a frame through its last millisecond.
        duration_ms = round(value(567) * 1000)
        if duration_ms > 0:
            vitals.adaptive_until = (state.start + duration_ms) // script.frame_ms
            if self._expire_adaptive_shield(player, vitals, self.tick):
                return
        radius = value(11861)
        # State 9 takes the owner's STU_21A13145 position. Use the server eye transform.
        origin = self._eye(player)
        count = sum(
            segment_distance(origin, *target.segment()) - target.radius <= radius
            for target in self._enemies(player, include_phased=True)
            if not isinstance(target.owner, ball_mines.Mine)
        )
        vitals.set_temporary_shields(value(15274) + value(15253) * count)
        pulse = getattr(script, "emp_shield_pulse", None)
        if pulse is not None and state.start <= pulse[0]:
            # A late command can materialize this activation after the
            # transient EMP graph has already retired.
            vitals.temporary_shields = max(0.0, vitals.temporary_shields - pulse[1])
        self._sync_body_health(player, vitals)

    @staticmethod
    def _expire_adaptive_shield(owner, vitals, tick: int) -> bool:
        if not 0 < vitals.adaptive_until <= tick:
            return False
        vitals.adaptive_until = 0
        vitals.set_temporary_shields(0.0)
        Combat._sync_body_health(owner, vitals)
        return True

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
            for target in self._enemies(player):
                shown = self.seen(target, view_tick)
                if shown is None:
                    continue
                a, b = shown.segment()
                if (segment_distance(centre, a, b) - shown.radius <= radius and amount > 0.0
                        and capsule_visible(self._collision(), self._eye(player), a, b, shown.radius)):
                    if player.hero.guid == ball.HERO and deflection.blocks_melee(
                        self, player, target, state, modify
                    ):
                        continue
                    if self.barriers and barriers.protect_body(self, target, amount, attacker=player.body):
                        continue
                    pinned = self.vitals_of(target) if target.entity in self.pins else None
                    before = pinned.total if pinned is not None else 0.0
                    self._damage(player, target, amount, False, loadout, command.frame)
                    if pinned is not None and not pinned.dead and pinned.total < before:
                        from ow174.game import assassin

                        assassin.rescue_melee(self, target.owner, player)

    def _piledriver(self, player, command) -> None:
        """Consume one authored landing search; native target/impulse services are reconstructed.

        Search uses live capsules and static LOS. Damage evaluates the actual 160A
        distance expression; node 12 supplies inward speed and ModifyHealth supplies lift.
        Barrier/invulnerability/filter and control-immunity policies remain open.
        """
        instance = ball.instance(player)
        state = instance.states.get(7) if instance is not None else None
        if state is None:
            return
        counters = self.melee_seen.setdefault(player.body, {})
        key = instance.id, state.index
        seen = counters.get(key, 0)
        counters[key] = state.counter
        if state.counter == seen or command is None:
            return
        if state.start // instance.component.frame_ms != command.frame:
            return
        origin = instance.vars.get(1175)
        origin = origin.value() if origin is not None else None
        if not isinstance(origin, expr.Vec3):
            return
        centre = tuple(origin)  # authored impact centre: feet + 0.6m
        radius = _number(instance.vars[6252].value(), 0.0)
        damage_node = next(n for n in instance.graph.nodes if n.pos == 11)
        pull_node = instance.graph.node(12)
        modify = instance.graph.states[5].field('m_modifyHealth')
        loadout = instance.evaluate(modify['m_loadout'])
        vertical = _number(instance.evaluate(modify['m_583C208B']), 0.0)
        for target in self._enemies(player):
            a, b = target.segment()
            distance = max(0.0, segment_distance(centre, a, b) - target.radius)
            if distance > radius:
                continue
            destination = (target.feet[0], max(a[1], min(b[1], centre[1])), target.feet[2])
            delta = _sub(destination, centre)
            length = math.sqrt(_dot(delta, delta))
            if length > EPSILON:
                obstruction = ray_world(self._collision(), centre, tuple(v / length for v in delta), length)
                if obstruction is not None and obstruction < length - target.radius:
                    continue
            instance.write((expr.INSTANCE, 680), distance)
            amount = _number(instance.evaluate(damage_node.field('m_value')), 0.0)
            if self.barriers and barriers.protect_body(self, target, amount, attacker=player.body):
                continue
            self._damage(player, target, amount, False,
                         loadout.guid if isinstance(loadout, expr.Asset) else None, command.frame)
            if (not self.vitals_of(target).dead and vertical > 0
                    and not self._has_protection(target, CRYO_STATES)
                    and not isinstance(target.owner, ball_mines.Mine)):
                pull = _number(instance.evaluate(pull_node.field('m_value')), 0.0)
                planar = math.hypot(delta[0], delta[2])
                scale = -pull / planar if planar > EPSILON else 0
                self._knock_up(target, vertical, (scale * delta[0], scale * delta[2]))
                if target.owner in self.match.players:
                    start = self.match.tick
                    self.pile_air_locks[target.entity] = (start, start + math.ceil(PILE_AIR_LOCK / TICK))

    def _fireball(self, player, command):
        """Bridge 1203's live high-speed search to enemy capsule damage/control.

        The graph supplies speed gates, radii, angle, damage and impulse values.
        Capsule/cone/LOS geometry and one hit until leaving the outer proximity
        sphere are reconstruction policies; exact native filters/rehit remain open.
        """
        instance = ball.instance(player, 0x1203)
        if instance is None or not ball.rolling(player):
            self.ball_contacts.pop(player.body, None)
            return
        if command is None:
            return
        search = instance.states.get(35)
        damage = instance.states.get(39)
        proximity = instance.graph.node(57)
        offset = instance.evaluate(proximity.field('m_05BE62DD'))
        if not isinstance(offset, expr.Vec3):
            return
        centre = tuple(p + axis for p, axis in zip(player.mover.position, offset, strict=True))
        outer = _number(instance.evaluate(proximity.field('m_radius')), 0.0)
        enemies = self._enemies(player)
        nearby = {target.entity for target in enemies
                  if segment_distance(centre, *target.segment()) - target.radius <= outer}
        contacts = self.ball_contacts.setdefault(player.body, set())
        contacts.intersection_update(nearby)
        if search is None or not search.active or damage is None or not damage.active:
            return
        radius = _number(search.evaluate(search.node.field('m_radius'), watch=False), 0.0)
        angle = _number(search.evaluate(search.node.field('m_A30B383E'), watch=False), 0.0)
        velocity = player.mover.velocity
        speed = math.sqrt(_dot(velocity, velocity))
        if radius <= 0 or speed <= EPSILON or not 0 < angle <= 360:
            return
        direction = tuple(v / speed for v in velocity)
        # Interpret the authored cone angle as its full aperture, aligned to
        # actual velocity. Native orientation configs are not generally restored.
        modify = damage.node.field('m_modifyHealth')
        amount = _number(damage.evaluate(modify.get('m_amount'), watch=False), 0.0)
        horizontal = _number(damage.evaluate(modify.get('m_E33EFEE1'), watch=False), 0.0)
        vertical = _number(damage.evaluate(modify.get('m_583C208B'), watch=False), 0.0)
        loadout = damage.evaluate(modify.get('m_loadout'), watch=False)
        for target in enemies:
            if target.entity not in nearby or target.entity in contacts:
                continue
            if isinstance(target.owner, ball_mines.Mine) and target.owner.armed:
                # Let the armed mine's proximity search resolve the crossing.
                continue
            a, b = target.segment()
            point = tuple((low + high) * 0.5 for low, high in zip(a, b, strict=True))
            delta = _sub(point, centre)
            distance = math.sqrt(_dot(delta, delta))
            if segment_distance(centre, a, b) - target.radius > radius:
                continue
            # Cone aims at the capsule midpoint; its angular radius admits edge
            # overlap. Exact native hit-volume/filter correspondence stays open.
            margin = math.asin(min(1.0, target.radius / distance)) if distance > EPSILON else math.pi
            minimum = math.cos(min(math.pi, math.radians(angle * 0.5) + margin))
            if distance > target.radius and _dot(delta, direction) / distance < minimum:
                continue
            if not capsule_visible(self._collision(), centre, a, b, target.radius):
                continue
            contacts.add(target.entity)
            if self.barriers and barriers.protect_body(self, target, amount, attacker=player.body):
                continue
            self._damage(player, target, amount, False,
                         loadout.guid if isinstance(loadout, expr.Asset) else None, command.frame)
            if (not self.vitals_of(target).dead and not self._has_protection(target, CRYO_STATES)
                    and (horizontal > 0 or vertical > 0)):
                self._knock_up(target, vertical)
                owner = target.owner
                mover = self.npc_impulses[target.entity][0] if isinstance(owner, bots.Bot) else owner.mover
                planar = math.hypot(velocity[0], velocity[2])
                if planar > EPSILON and horizontal > 0:
                    vx, vy, vz = mover.velocity
                    mover.velocity = (vx + horizontal * velocity[0] / planar, vy,
                                      vz + horizontal * velocity[2] / planar)
                    if isinstance(owner, bots.Bot):
                        owner.velocity = mover.velocity

    def _knock_up(self, target, speed, planar=(0, 0)):
        owner = target.owner
        if isinstance(owner, bots.Bot):
            entry = self.npc_impulses.get(target.entity)
            mover = entry[0] if entry is not None else Mover(
                owner.position, round(owner.yaw), world=self._collision(), data=mover_data(owner.body),
            )
            if entry is None:
                mover.velocity = owner.velocity
                owner.pace = 0.0
            self.npc_impulses[target.entity] = mover, self.match.tick
            owner.airborne = True
        else:
            mover = owner.mover
        vx, vy, vz = mover.velocity
        mover.velocity = (vx + planar[0], max(vy, speed), vz + planar[1])
        mover.flags_word |= FELL
        if isinstance(owner, bots.Bot):
            owner.velocity = mover.velocity

    def _advance_npc_impulses(self, tick):
        for entity, (mover, last) in list(self.npc_impulses.items()):
            bot = next((b for b in self.match.bots if b.entity == entity), None)
            if bot is None or bot.dead:
                self.npc_impulses.pop(entity, None)
                continue
            for at in range(max(last + 1, tick - COMBAT_CATCH_UP + 1), tick + 1):
                mover.step_pose(Command(at, yaw=round(bot.yaw)), TICK, at)
            bot.position, bot.velocity, bot.airborne = tuple(mover.position), mover.velocity, mover.airborne
            bot.moved_tick = tick
            bot.last_tick = tick  # route walking resumes without replaying controlled-airborne ticks
            if not bot.airborne:
                self.npc_impulses.pop(entity, None)
            else:
                self.npc_impulses[entity] = mover, tick

    def update(self, now: float, tick: int, frames: bool = True) -> None:
        """Each tick: keep where the bodies are, fly the rockets, respawn, send health that changed. The
        other players' body frames only on the ticks that send (`frames`)."""
        if self.match.collision_world is None:
            return
        retained = max(self.tick + 1, tick - COMBAT_CATCH_UP + 1)
        pulse_previous_tick = self.tick
        if self.rifle_doses and retained > self.tick + 1:
            pulse_previous_tick = self._replay_rifle_prefix(now, tick, retained)
        self._advance_npc_impulses(tick)
        catching_up = self.tick and tick > self.tick + 1
        replay_npc = catching_up and any(
            bot.firing and not bot.dead and isinstance(bot.body_script, BotScript) for bot in self.match.bots
        )
        replay_projectiles = catching_up and any(
            rocket.last_tick is not None and (rocket.npc_launch or rocket.gravity) for rocket in self.rockets
        )
        replay_heals = catching_up and bool(self.rifle_doses)
        first = retained if replay_npc or replay_projectiles or replay_heals else tick
        self._record_history(tick, self._body_targets())
        # Replay the retained combat frames using the most recent poses. Player input
        # accepted just before the scheduler caught up joins only at the endpoint.
        previous_world_tick = self.tick
        for at in range(first, tick + 1):
            self.now, self.tick = now - (tick - at) * TICK, at
            deflection.advance_grasp(self)
            barriers.advance_cryo_heal(self, pulse_previous_tick)
            # Finish an expired aura's prior healing interval before new damage.
            transcendence.heal_transcendence(self, pulse_previous_tick, expiring=True)
            if at == tick:
                # Mine proximity uses the current poses and resolves at the endpoint.
                self.mines.update(tick)
                self._record_history(tick, self._mine_targets())
            if self.barriers:
                barriers.advance(self, at)
            for rocket in list(self.rockets):
                if at == tick or (rocket.last_tick is not None
                                  and (replay_npc or rocket.npc_launch or rocket.gravity)):
                    self._fly(rocket)
            if at == tick or replay_npc:
                self._bot_weapons(at)
            if self.rifle_doses:
                self._advance_rifle_doses(pulse_previous_tick)
            transcendence.heal_transcendence(self, pulse_previous_tick)
            if replay_heals:
                self.projectiles.heal_admitted_fields(at)
            pulse_previous_tick = at
        self.projectiles.update(tick, first_tick=retained)
        for player in self.match.players:
            vitals = self.vitals.get(player.body)
            if vitals is not None:
                self._expire_adaptive_shield(player, vitals, tick)
        # Continuous healers have consumed this tick's retained history.
        for vitals in self.vitals.values():
            if vitals.healing_block:
                vitals.healing_block = tuple(span for span in vitals.healing_block if span[1] > tick)
            if vitals.healing_boost:
                vitals.healing_boost = tuple(span for span in vitals.healing_boost if span[1] > tick)
        sombra_emp.expire(self, previous_world_tick)
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
        for entity in [key for key, (_, deadline) in self.sleeps.items() if deadline <= tick]:
            del self.sleeps[entity]
        # Recovered or delayed command frames can still be inside an expired world window.
        for player in self.match.players:
            lock = self.pile_air_locks.get(player.body)
            if lock is None:
                continue
            last_command = player.mover.last_command
            if last_command is not None and last_command.frame >= lock[1]:
                del self.pile_air_locks[player.body]
        for table in (self.history, self.vitals, self.replacement_ticks, self.stuns, self.sleeps):
            for entity in [key for key in table if key not in current]:
                del table[entity]
        for source, victim in list(self.pins.items()):
            if source not in current or victim not in current:
                self.release_pin(source, animate=False)

    # --- targets -----------------------------------------------------------------------------------

    def _targets(self) -> list[Target]:
        found = self._body_targets()
        found.extend(self._mine_targets())
        return found

    def _record_history(self, tick, targets):
        for target in targets:
            history = self.history.setdefault(target.entity, deque(maxlen=HISTORY))
            history.append((tick, target.feet, target.height, target.eye, target.critical_allowed))

    def _body_targets(self) -> list[Target]:
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
                       player, critical_allowed=not ball.rolling(player))
            )  # fmt: skip
        for bot in self.match.bots:
            data = mover_data(bot.body)
            found.append(
                Target(
                    bot.entity, bot.team, tuple(bot.position), data.stand_height, data.stand_radius, EYE, bot
                )
            )
        return found

    def _mine_targets(self):
        for mine in self.mines.entities.values():
            yield Target(mine.entity, mine.team, mine.position, 0.46, 0.23, 0.23, mine,
                         critical_allowed=False)

    def vitals_of(self, target: Target) -> Vitals:
        found = self.vitals.get(target.entity)
        if found is None:
            if isinstance(target.owner, ball_mines.Mine):
                found = Vitals(target.entity, 50)
                found.now[HEALTH_POOL] = target.owner.health
            elif isinstance(target.owner, bots.Bot):
                found = Vitals(target.entity, target.owner.health)
            else:
                hero = target.owner.hero
                found = Vitals(target.entity, *pools.body_pools(hero.guid, hero.body))
            self.vitals[target.entity] = found
        return found

    def _has_protection(self, target: Target, profiles) -> bool:
        owner = target.owner
        hero = getattr(getattr(owner, "hero", None), "guid", None)
        profile = profiles.get(hero)
        script = getattr(owner, "body_script", None)
        if profile is None or script is None or script.retired:
            return False
        graph_index, state_index = profile
        for instance in script.component.instances.values():
            if instance.graph.index == graph_index:
                state = instance.states.get(state_index)
                if state is not None and state.active:
                    if hero in PHASE_STATES:
                        # Both phase scopes end with their authored Wait 3.
                        timer = instance.states.get(3)
                        if timer is not None and timer.active:
                            deadline = (timer.start + timer.ms) // script.frame_ms
                            if self.tick >= deadline:
                                continue
                    if hero == barriers.MEI:
                        deadline = barriers.cryo_expiry(instance)
                        if deadline is not None and self.tick >= deadline:
                            continue
                    if hero == transcendence.HERO and not transcendence.immunity_live(instance, self.tick):
                        continue
                    return True
        return False

    def _enemies(self, shooter, *, include_phased=False) -> list[Target]:
        free_for_all = self.match.game_map.free_for_all
        source = _source_entity(shooter)
        found = []
        for target in self._targets():
            if target.owner is shooter or target.entity == source:
                continue
            if isinstance(target.owner, ball_mines.Mine) and target.owner.source == source:
                continue
            if not free_for_all and target.team == shooter.team:
                continue
            if (self.vitals_of(target).dead
                    or (not include_phased and self._has_protection(target, PHASE_STATES))):
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
            _, feet, height, eye = before[:4]
        else:
            share = (view_tick - before[0]) / (after[0] - before[0])
            feet = tuple(a + (b - a) * share for a, b in zip(before[1], after[1], strict=True))
            height, eye = (before[2], before[3]) if share < 0.5 else (after[2], after[3])
        # Discrete form changes take effect at their recorded tick, never halfway
        # through an interpolation interval. Older four-field fixtures imply normal form.
        critical_allowed = before[4] if len(before) > 4 else True
        return Target(target.entity, target.team, feet, height, target.radius, eye, target.owner,
                      critical_allowed=critical_allowed)

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
                npc_control.retire_acquisition(bot)
                bot.body_script = None
                log.exception("[game] %s: %s's weapon script failed", self.match.label(), bot.name)

    def _eye(self, player) -> tuple:
        x, y, z = player.mover.position
        crouched = player.mover.crouched
        height = eye_height(player.hero.body, crouched)
        return (x, y + height, z)

    def track_nano_target(self, body: int, state) -> None:
        """Supply the authored 0B9E allied search from live capsules and cover."""
        instance = state.instance
        caster = next((player for player in self.match.players if player.body == body), None)
        output = expr.lvalue(state.node.field("m_8A2905A0"))
        if output is None:
            return
        candidates = []
        if (caster is not None and barriers._recipient_ready(self, caster)
                and not self.is_stunned(caster) and not self.match.game_map.free_for_all
                and self.match.collision_world is not None):
            radius = expr.to_float(state.evaluate(state.node.field("m_radius"), watch=False))
            origin = self._eye(caster)
            direction = aim(caster.mover.yaw, caster.mover.pitch)
            for target in self._targets():
                ally = target.owner
                if (ally is caster or ally not in self.match.players or ally.team != caster.team
                        or not barriers._recipient_ready(self, ally)):
                    continue
                delta = _sub((target.feet[0], target.feet[1] + target.height / 2, target.feet[2]), origin)
                distance = math.sqrt(_dot(delta, delta))
                if not EPSILON < distance <= radius:
                    continue
                alignment = _dot(direction, delta) / distance
                if alignment < math.cos(math.radians(10)):
                    continue
                ray = tuple(axis / distance for axis in delta)
                if (ray_world(self._collision(), origin, ray, distance) is None
                        and barriers.ray_hit(self, caster, origin, ray, distance) is None):
                    candidates.append((-alignment, distance, target.entity))
        instance.write(output, (expr.Entity(min(candidates)[2]),) if candidates else ())

    def _release_nano(self, shooter, state) -> None:
        instance = state.instance
        selected = instance.find_var(expr.INSTANCE, 8070, create=False)
        duration = instance.find_var(expr.INSTANCE, 215, create=False)
        if (selected is None or duration is None or not barriers._recipient_ready(self, shooter)
                or self.is_stunned(shooter)
                or self.match.game_map.free_for_all):
            return
        recipients = selected.value()
        if isinstance(recipients, expr.Entity):
            recipients = (recipients,)
        seconds = expr.to_float(duration.value())
        if not isinstance(recipients, tuple) or not math.isfinite(seconds) or seconds <= 0:
            return
        ticks = math.ceil(seconds / TICK)
        for target in self._targets():
            ally = target.owner
            if (expr.Entity(target.entity) not in recipients or ally is shooter
                    or ally not in self.match.players or ally.team != shooter.team
                    or not barriers._recipient_ready(self, ally)):
                continue
            self.heal_target(shooter, target, 250.0)
            vitals = self.vitals_of(target)
            vitals.nano_until = max(vitals.nano_until, self.tick + ticks)

    def outgoing_damage_scale(self, entity: int) -> float:
        vitals = self.vitals.get(entity)
        return 1.5 if vitals is not None and not vitals.dead and self.tick < vitals.nano_until else 1.0

    def _shoot(
        self, shooter, command, state, number: int, time: int,
        *, activation: tuple[int, int], snapshot: Snapshot | None = None,
    ) -> None:
        node = state.node
        shot_frame = time // state.instance.component.frame_ms
        if (state.instance.graph.index, node.pos) == (0x0B9E, 4):
            self._release_nano(shooter, state)
            return
        if not isinstance(shooter, bots.Bot) and self.mines.volley(shooter, state, number, shot_frame):
            return

        def field(name, default=None):
            cfg = node.field(name)
            return default if cfg is None else _number(state.evaluate(cfg, watch=False), default)

        npc = isinstance(shooter, bots.Bot)
        if not npc and command is None:
            return
        if npc:
            x, y, z = shooter.position
            # Use the server body pose until the animated muzzle transform is available.
            origin, direction = (x, y + EYE, z), aim(round(shooter.yaw), round(shooter.pitch))
        else:
            origin, direction = self._eye(shooter), aim(*command.aim())
            direction = ball.cannon_direction(shooter, state, direction, number, time, activation)
        direct = hit_def(state, node.field("m_6396149F"))
        speed = field("m_projectileSpeed")
        if speed:
            lifetime = field("m_projectileLifetime", 5.0) or 5.0
            reach = field("m_FEC435C6")
            blast = hit_def(state, node.field("m_D2D11CE9"))
            entity_def = (node.field("m_projectileEntity") or {}).get("m_entityDef")
            definition = state.evaluate(entity_def, watch=False) if entity_def is not None else None
            biotic_grenade = (state.instance.graph.index == 0x0B97 and blast is not None
                              and blast.effect == ANA_GRENADE_EFFECT
                              and definition == expr.Asset(0x0400000000001426))
            if reach and not biotic_grenade:
                lifetime = min(lifetime, reach / speed)
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
                directions = (
                    npc_context.projectile_directions(shooter, state, direction) if npc else (direction,)
                )
                gravity = 0.0
                if biotic_grenade:
                    # 0B97 supplies the unconditional E1A5B579 vertical acceleration.
                    for motion in node.field("m_projectileMotions") or ():
                        if (motion.get("$") == "STU_E1A5B579"
                                and not expr.truthy(state.evaluate(motion.get("m_D0C4CF49"), watch=False))):
                            gravity += expr.to_float(state.evaluate(motion.get("m_599AB8D7"), watch=False))
                # These verified projectile definitions lack exclusion tag 0145.
                deflectable = (
                    (npc and state.instance.graph.index == 0x13EB
                     and definition == expr.Asset(0x04000000000023F0))
                    or (state.instance.graph.index == 0x0BDD and direct is not None
                        and direct.effect == SLEEP_EFFECT and definition == expr.Asset(0x04000000000015E8))
                    or (state.instance.graph.index == 0x01E9 and blast is not None
                        and blast.effect == FLASHBANG_EFFECT and definition == expr.Asset(0x040000000000037D))
                    or biotic_grenade
                    or (direct is not None and direct.biotic_rifle
                        and definition == expr.Asset(0x04000000000015B7))
                )
                for index, ray in enumerate(directions):
                    self.rockets.append(
                        Rocket(
                            shooter, shooter.team, origin, ray, speed, lifetime, direct, blast,
                            shot if index == 0 else None, last_tick=self.tick if npc else None,
                            deflectable=deflectable, gravity=gravity, reach=reach if biotic_grenade else None,
                            source_scale=self.outgoing_damage_scale(_source_entity(shooter)), npc_launch=npc,
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
        reflectable = False
        if not npc and shooter.hero.guid == ball.HERO and state.instance.graph.index == 0x1514:
            modify = node.field("m_6396149F").get("m_modifyHealth") or {}
            reflectable = any(
                state.evaluate(row.get('m_EE3055BD'), watch=False) == expr.Asset(deflection.BULLET_TAG)
                for row in modify.get('m_148F3152') or ()
            )
        matrix_eligible = False
        if reflectable:
            entity_def = (node.field("m_projectileEntity") or {}).get("m_entityDef")
            # Authored 2584 has target tag 02F9 and lacks Matrix exclusion tag 0145.
            matrix_eligible = state.evaluate(entity_def, watch=False) == expr.Asset(0x0400000000002584)
        elif direct.biotic_rifle:
            # The scoped 1615 delivery is instantaneous but has no 0145 exclusion.
            entity_def = (node.field("m_projectileEntity") or {}).get("m_entityDef")
            reflectable = matrix_eligible = state.evaluate(entity_def, watch=False) == expr.Asset(
                0x0400000000001615
            )
        travelled = 0.0
        for bounce in range(8 if reflectable else 1):
            best = None
            candidates = self._enemies(shooter)
            if direct.biotic_rifle:
                candidates.extend(self._rifle_allies(shooter))
            for target in candidates:
                shown = self.seen(target, view_tick)
                if shown is None:
                    continue
                a, b = shown.segment()
                t = ray_capsule(origin, direction, a, b, shown.radius)
                if t is not None and t <= reach and (best is None or t < best[0]):
                    best = (t, target, shown)
            limit = reach if best is None else best[0]
            shield = barriers.ray_hit(self, shooter, origin, direction, limit) if self.barriers else None
            intercept_limit = shield[0] if shield is not None else limit
            intercept = (
                deflection.intercept(self, shooter, origin, direction, intercept_limit,
                                     matrix_eligible=matrix_eligible)
                if reflectable else None
            )
            if best is None and intercept is None and shield is None:
                return
            distance = intercept[0] if intercept is not None else intercept_limit
            wall = ray_world(self._collision(), origin, direction, distance)
            if wall is not None and wall < distance:
                return
            travelled += distance
            if intercept is not None:
                _, defender, return_direction, grasp = intercept
                if grasp is not None:
                    amount = direct.amount * direct.scale(travelled)
                    deflection.absorb(grasp, amount * self.outgoing_damage_scale(_source_entity(shooter)))
                    return
                point = _along(origin, direction, distance)
                shooter, direction = defender, return_direction
                origin = _along(point, direction, 1e-4)
                reach -= distance + 1e-4
                if reach <= 0:
                    return
                view_tick = shot_frame = self.tick
                continue
            if shield is not None:
                amount = direct.amount * direct.scale(travelled)
                barriers.damage(self, shield[1], amount * self.outgoing_damage_scale(_source_entity(shooter)))
                return
            _, target, shown = best
            if direct.biotic_rifle and not self.match.game_map.free_for_all and target.team == shooter.team:
                self._queue_rifle_dose(shooter, target, direct, healing=True)
                return
            base = direct.amount * direct.scale(travelled)
            if self.barriers and barriers.protect_body(self, target, base, attacker=_source_entity(shooter)):
                return
            if direct.biotic_rifle:
                self._queue_rifle_dose(shooter, target, direct, reflected=bool(bounce))
                return
            hit_y = origin[1] + direction[1] * distance
            critical = shown.critical_allowed and hit_y >= shown.feet[1] + shown.eye - HEAD_BELOW
            amount = base * (direct.critical if critical else 1.0)
            self._damage(
                shooter, target, amount, critical and direct.critical > 1.0, direct.loadout, shot_frame,
                world_tick=self.tick if npc or bounce else None, reflected=bool(bounce),
            )
            self._hit_effect(target, direct.effect)
            return

    def _fly(self, rocket: Rocket) -> None:
        """Sweep elapsed world time against the current bodies and static collision."""
        # Player input can arrive immediately before the scheduler accounts for a gap.
        elapsed = 1 if rocket.last_tick is None else self.tick - rocket.last_tick
        if elapsed <= 0:
            return
        rocket.last_tick = self.tick
        steps, step_time = (elapsed, TICK) if rocket.gravity else (1, elapsed * TICK)
        for _ in range(steps):
            if rocket.left <= 0.0:
                self._landed(rocket)
                return
            duration = step_time if rocket.reach is not None else min(step_time, rocket.left)
            endpoint = None
            if rocket.gravity:
                velocity = tuple(axis * rocket.speed for axis in rocket.direction)
                (vx, vy, vz), _, endpoint = ball_mines.flight_step(
                    rocket.position, velocity, rocket.gravity, duration,
                )
                rocket.speed = math.sqrt(vx * vx + vy * vy + vz * vz)
                if rocket.speed > 0.0:
                    rocket.direction = (vx / rocket.speed, vy / rocket.speed, vz / rocket.speed)
            while duration > 0.0:
                duration = self._sweep_rocket(rocket, duration, endpoint=endpoint) or 0.0
                endpoint = None
            if rocket.gravity and rocket not in self.rockets:
                return

    def _sweep_rocket(self, rocket: Rocket, duration: float, *, endpoint=None) -> float | None:
        """Resolve one segment and return its remaining time only on reflection."""
        # Sweep the remaining flight even when the range or lifetime ends inside this tick.
        step = rocket.speed * duration
        ray, speed = rocket.direction, rocket.speed
        swept, clipped, travelled = duration, False, 0.0
        if rocket.reach is not None:
            velocity = tuple(axis * speed for axis in ray)
            if endpoint is None:
                _, _, endpoint = ball_mines.flight_step(rocket.position, velocity, 0.0, duration)
            endpoint, _, swept, travelled, clipped = ball_mines.clip_flight(
                rocket.position, velocity, endpoint, velocity, rocket.flown + rocket.left,
                rocket.reach, rocket.flown, rocket.travelled, duration,
            )
            chord = _sub(endpoint, rocket.position)
            step = math.sqrt(_dot(chord, chord))
            if step == 0.0 or swept <= 0.0:
                rocket.flown += swept
                rocket.left -= swept
                if clipped or rocket.left <= 0.0:
                    self._landed(rocket)
                return
            ray = tuple(axis / step for axis in chord)
            speed = step / swept
        best = None
        blast = rocket.blast
        grenade = blast is not None and blast.effect == ANA_GRENADE_EFFECT
        enemies = self._enemies(rocket.shooter)
        candidates = enemies.copy() if grenade else enemies
        allies = []
        if grenade:
            for target in self._targets():
                allied = not self.match.game_map.free_for_all and target.team == rocket.team
                if target.owner is not rocket.shooter and not allied:
                    continue
                if (isinstance(target.owner, ball_mines.Mine) or self.vitals_of(target).dead
                        or self._has_protection(target, PHASE_STATES)):
                    continue
                allies.append(target)
                if target.owner is not rocket.shooter:
                    candidates.append(target)
        elif rocket.direct is not None and rocket.direct.biotic_rifle:
            candidates.extend(self._rifle_allies(rocket.shooter))
        for target in candidates:
            a, b = target.segment()
            t = ray_capsule(rocket.position, ray, a, b, target.radius)
            if t is not None and t <= step and (best is None or t < best[0]):
                best = (t, target)
        wall = ray_world(self._collision(), rocket.position, ray, step)
        if wall is not None and (best is None or wall < best[0]):
            best = (wall, None)
        sleep_dart = rocket.direct is not None and rocket.direct.effect == SLEEP_EFFECT
        barrier_projectile = rocket.direct is not None and blast is None and (
            isinstance(rocket.shooter, bots.Bot) or rocket.deflectable or rocket.direct.biotic_rifle
        )
        flashbang = blast is not None and blast.effect == FLASHBANG_EFFECT
        barrier_impact = None
        limit = step if best is None else best[0]
        shield = None
        if (sleep_dart or barrier_projectile or flashbang or grenade) and self.barriers:
            shield = barriers.ray_hit(self, rocket.shooter, rocket.position, ray, limit)
            if shield is not None and wall is not None and shield[0] >= wall:
                shield = None
        payload = blast if flashbang or grenade else rocket.direct
        if rocket.deflectable and payload is not None:
            intercept = deflection.intercept(
                self, rocket.shooter, rocket.position, ray,
                shield[0] if shield is not None else limit, matrix_eligible=True,
            )
            if intercept is not None and (wall is None or intercept[0] < wall):
                distance, defender, direction, absorber = intercept
                travel = distance / speed
                point = _along(rocket.position, ray, distance)
                if rocket.reach is not None:
                    travelled = ball_mines.flight_distance(rocket.position, point)
                    rocket.travelled = expr.f32(rocket.travelled + travelled)
                rocket.flown += travel
                if absorber is not None:
                    self._landed(rocket)
                    amount = payload.amount * payload.scale(rocket.flown * rocket.speed)
                    deflection.absorb(absorber, amount * rocket.source_scale)
                    return
                rocket.left -= travel
                if rocket.reflections >= 8 or rocket.left <= 0:
                    self._landed(rocket)
                    return
                if rocket.shot is not None:
                    # End the original predicted shot when server ownership changes.
                    self.projectiles.flown(rocket.shot, rocket.flown)
                    rocket.shot = None
                rocket.position = _along(point, direction, 0.0001)
                rocket.direction = direction
                rocket.shooter, rocket.team = defender, defender.team
                rocket.source_scale = self.outgoing_damage_scale(_source_entity(defender))
                rocket.reflections += 1
                return duration - travel
        if shield is not None:
            if sleep_dart or barrier_projectile:
                rocket.flown += shield[0] / speed
                self._landed(rocket)
                amount = rocket.direct.amount * rocket.direct.scale(rocket.flown * rocket.speed)
                barriers.damage(self, shield[1], amount * rocket.source_scale,
                                attacker=_source_entity(rocket.shooter))
                return
            best = (shield[0], None)
            barrier_impact = shield[1]
        if best is None:
            rocket.position = endpoint if endpoint is not None else _along(rocket.position, ray, step)
            rocket.flown += swept
            rocket.left -= swept
            if rocket.reach is not None:
                rocket.travelled = expr.f32(rocket.travelled + travelled)
            if clipped or rocket.left <= 0.0:
                self._landed(rocket)
            return
        t, struck = best
        rocket.flown += t / speed
        self._landed(rocket)
        point = _along(rocket.position, ray, t)
        if rocket.reach is not None:
            distance = ball_mines.flight_distance(rocket.position, point)
            rocket.travelled = expr.f32(rocket.travelled + distance)
        if struck is not None and rocket.direct is not None and rocket.direct.amount > 0.0:
            if (rocket.direct.biotic_rifle and not self.match.game_map.free_for_all
                    and struck.team == rocket.team):
                self._queue_rifle_dose(rocket.shooter, struck, rocket.direct, healing=True)
                return
            amount = rocket.direct.amount * rocket.direct.scale(rocket.flown * rocket.speed)
            if (sleep_dart or barrier_projectile) and self.barriers and barriers.protect_body(
                self, struck, amount, attacker=_source_entity(rocket.shooter),
                source_scale=rocket.source_scale,
            ):
                return
            if rocket.direct.biotic_rifle:
                self._queue_rifle_dose(
                    rocket.shooter, struck, rocket.direct, source_scale=rocket.source_scale,
                    reflected=bool(rocket.reflections),
                )
                return
            self._damage(
                rocket.shooter, struck, amount, False, rocket.direct.loadout, self.tick,
                world_tick=self.tick, reflected=bool(rocket.reflections), source_scale=rocket.source_scale,
            )
            self._hit_effect(struck, rocket.direct.effect)
        if blast is None or blast.amount <= 0.0:
            return
        # Resolve occlusion against the impact shield before its own damage can break it.
        visible_from = (_along(point, ray, -0.0001)
                        if wall is not None or barrier_impact is not None else point)
        for target in enemies + allies:
            a, b = target.segment()
            distance = max(0.0, segment_distance(point, a, b) - target.radius)
            scalar = blast.splash(distance)
            if scalar > 0.0 and capsule_visible(self._collision(), visible_from, a, b, target.radius):
                amount = blast.amount * scalar
                allied = target in allies
                if (flashbang or grenade) and self.barriers:
                    nearest = (a[0], max(a[1], min(b[1], visible_from[1])), a[2])
                    delta = _sub(nearest, visible_from)
                    length = math.sqrt(_dot(delta, delta))
                    if length > target.radius and barriers.ray_hit(
                        self, rocket.shooter, visible_from,
                        tuple(axis / length for axis in delta), length - target.radius,
                    ) is not None:
                        continue
                    if not allied and barriers.protect_body(
                        self, target, amount, attacker=_source_entity(rocket.shooter),
                        source_scale=rocket.source_scale,
                    ):
                        continue
                if allied:
                    self.heal_target(rocket.shooter, target, ANA_GRENADE_HEAL * scalar)
                    self.vitals_of(target).apply_biotic_grenade(self.tick, allied=True)
                    continue
                if (grenade and not isinstance(target.owner, ball_mines.Mine)
                        and not self._has_protection(target, DAMAGE_IMMUNITY_STATES)):
                    self.vitals_of(target).apply_biotic_grenade(self.tick, allied=False)
                self._damage(
                    rocket.shooter, target, amount, False, blast.loadout, self.tick,
                    world_tick=self.tick, reflected=bool(rocket.reflections),
                    source_scale=rocket.source_scale,
                )
                self._hit_effect(target, blast.effect)
        if barrier_impact is not None:
            barriers.damage(self, barrier_impact, blast.amount * rocket.source_scale,
                            attacker=_source_entity(rocket.shooter))

    def _landed(self, rocket: Rocket) -> None:
        """The projectile ended (hit, range, lifetime): the predicted one's release follows."""
        if rocket in self.rockets:
            self.rockets.remove(rocket)
        if rocket.shot is not None:
            self.projectiles.flown(rocket.shot, rocket.flown)

    def heal_area(self, healer, centre, radius: float, amount: float, *, interval=None) -> None:
        """Heal the healer and his teammates (everyone's own team in a free-for-all is his alone) whose body
        is within `radius` of `centre` (unverified: to the body's capsule)."""
        for target in self._targets():
            if isinstance(target.owner, ball_mines.Mine):
                continue
            ally = not self.match.game_map.free_for_all and target.team == healer.team
            if target.owner is not healer and not ally:
                continue
            a, b = target.segment()
            if segment_distance(centre, a, b) - target.radius > radius:
                continue
            self.heal_target(healer, target, amount, interval=interval)

    def _rifle_allies(self, shooter):
        if self.match.game_map.free_for_all:
            return ()
        targets = []
        for target in self._targets():
            if (target.owner is shooter or target.team != shooter.team
                    or isinstance(target.owner, ball_mines.Mine)):
                continue
            vitals = self.vitals_of(target)
            if not vitals.dead and any(vitals.now[kind] < maximum for kind, maximum in vitals.most.items()):
                targets.append(target)
        return targets

    def _queue_rifle_dose(
        self, shooter, target: Target, direct: HitDef, *, healing=False, source_scale=None, reflected=False,
    ):
        source = _source_entity(shooter)
        if not healing and self._has_protection(target, DAMAGE_IMMUNITY_STATES):
            return
        if source_scale is None:
            source_scale = self.outgoing_damage_scale(source)
        amount = direct.amount if healing else direct.amount * source_scale
        self.rifle_doses.append(
            RifleDose(shooter, source, target.entity, amount, self.tick * TICK,
                      healing, direct.loadout, reflected)
        )

    def _replay_rifle_prefix(self, now: float, tick: int, retained: int) -> int:
        """Settle bound doses and scheduled healing in their normal damage-then-heal order."""
        previous_now, previous_tick = self.now, self.tick
        settled = previous_tick
        try:
            for at in range(previous_tick + 1, retained):
                if not self.rifle_doses:
                    break  # An admitted dose has at most 0.6 seconds left.
                self.now, self.tick = now - (tick - at) * TICK, at
                deflection.advance_grasp(self)
                barriers.advance_cryo_heal(self, at - 1)
                transcendence.heal_transcendence(self, at - 1, expiring=True)
                self._advance_rifle_doses(at - 1)
                transcendence.heal_transcendence(self, at - 1)
                self.projectiles.heal_admitted_fields(at)
                settled = at
        finally:
            self.now, self.tick = previous_now, previous_tick
        return settled

    def _advance_rifle_doses(self, previous_tick: int) -> None:
        targets = {target.entity: target for target in self._targets()}
        pending = []
        for dose in self.rifle_doses:
            target = targets.get(dose.recipient)
            if target is None or self.vitals_of(target).dead:
                continue
            deadline = dose.start + ANA_RIFLE_DURATION
            since = max(previous_tick * TICK, dose.start)
            until = min(self.tick * TICK, deadline)
            if until > since:
                amount = dose.amount * (until - since) / ANA_RIFLE_DURATION
                if dose.healing:
                    self.heal_target(dose.shooter, target, amount, interval=(since, until))
                else:
                    self._damage(dose.shooter, target, amount, False, dose.loadout, self.tick,
                                 world_tick=self.tick, source_scale=1.0, source_entity=dose.source,
                                 bypass_armour=True, reflected=dose.reflected)
            if self.tick * TICK < deadline and not self.vitals_of(target).dead:
                pending.append(dose)
        self.rifle_doses = pending

    def heal_target(self, healer, target: Target, amount: float, *, interval=None) -> None:
        vitals = self.vitals_of(target)
        if vitals.dead:
            return
        block, boost = vitals.healing_block, vitals.healing_boost
        if interval is None:
            if block and block[-1][0] <= self.tick < block[-1][1]:
                return
            if boost and boost[-1][0] <= self.tick < boost[-1][1]:
                amount *= ANA_HEAL_BOOST
        elif block or boost:
            start, end = interval

            def blocked_between(left, right):
                return sum(
                    max(0.0, min(right, (until - 1) * TICK) - max(left, (first - 1) * TICK))
                    for first, until in block
                )

            boosted = 0.0
            for first, until in boost:
                # The pulse ending on the impact tick uses the newly applied effect.
                left = max(start, (first - 1) * TICK)
                right = min(end, (until - 1) * TICK)
                if right > left:
                    boosted += right - left - blocked_between(left, right)
            elapsed = end - start
            amount *= (elapsed - blocked_between(start, end) + (ANA_HEAL_BOOST - 1) * boosted) / elapsed
        given = vitals.heal(amount)
        if given > 0.0:
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
        *, world_tick: int | None = None, reflected: bool = False, source_scale: float | None = None,
        source_entity: int | None = None, bypass_armour: bool = False,
    ) -> None:
        vitals = self.vitals_of(target)
        if vitals.dead or amount <= 0.0 or self._has_protection(target, DAMAGE_IMMUNITY_STATES):
            return
        self._expire_adaptive_shield(target.owner, vitals, self.tick if world_tick is None else world_tick)
        source = _source_entity(shooter) if source_entity is None else source_entity
        if source_scale is None:
            source_scale = self.outgoing_damage_scale(source)
        amount *= source_scale
        if self.tick < vitals.nano_until:
            amount *= 0.5
        temporary_before = vitals.temporary_shields
        dealt = vitals.damage(amount, bypass_armour=bypass_armour)
        if dealt <= 0.0:
            return
        sleep = self.sleeps.get(target.entity)
        if sleep is not None:
            start, deadline = sleep
            if (self.tick - start) * TICK > SLEEP_DAMAGE_GRACE and self.tick < deadline:
                wake = min(deadline, self.tick + math.ceil(SLEEP_WAKE / TICK))
                self.sleeps[target.entity] = (start, wake)
                if wake < deadline:
                    self._record_command_control(target, SLEEP_EFFECT, wake)
        killed = vitals.now[HEALTH_POOL] <= EPSILON
        if killed:
            vitals.now[HEALTH_POOL] = 0.0
            vitals.died_at = self.now
            vitals.death_tick = self.match.tick if world_tick is None else world_tick
            vitals.killer = source
        if isinstance(target.owner, ball_mines.Mine):
            # Deployable destruction is not a hero elimination or earned charge.
            target.owner.health = vitals.total
            self._sync_body_health(target.owner, vitals)
            if not isinstance(shooter, bots.Bot):
                self._tell_hit(shooter, target, dealt, KILLING if killed else 0, frame)
            return
        if not isinstance(shooter, bots.Bot):
            flags = (CRITICAL if critical else 0) | (KILLING if killed else 0)
            self._tell_hit(shooter, target, dealt, flags, frame)
            shooter.stats.add(stats.HERO_DAMAGE, dealt)
            # A reflected cannon round credits its final owner. Both paths
            # award effective damage to permanent pools at one point per damage.
            if reflected or (shooter.body == source and shooter.hero.guid == ball.HERO
                             and loadout != 0x0B900000000001ED):
                script = shooter.body_script
                if script is not None:
                    script.add_ult_charge(dealt - (temporary_before - vitals.temporary_shields))
        victim = target.owner
        if killed and isinstance(victim, bots.Bot):
            # The NPC death script owns its terminal health change and death event ordering.
            self._bot_died(shooter, victim, critical, loadout, vitals.death_tick, source=source)
            return
        self._sync_body_health(victim, vitals)
        if killed:
            self._died(shooter, victim, critical, loadout, source=source)

    def _tell_hit(self, shooter, target: Target, dealt: float, flags: int, frame: int) -> None:
        """25001 to the shooter: the hit marker and the target's health bar (frame: the shot's)."""
        value = {
            "+0x78": {"+0x0": {"+0x0": target.entity}, "+0x4": max(1, round(dealt)), "+0x8": flags},
            "+0x84": frame & 0xFFFFFFFF,
        }
        self._queue_notice(shooter, target.owner, target.entity, HIT, value)

    def _kill_notice(self, killer, victim, source: int) -> None:
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
        if not isinstance(actor, (bots.Bot, ball_mines.Mine)) and actor.body == entity:
            self.match.ensure_remote_body(viewer, actor)
        client.queue_combat_notice(entity, msg_id, value)

    @staticmethod
    def _death_parameters(source: int, critical: bool, loadout) -> dict:
        return {
            KILLER: expr.Entity(source),
            CRIT: bool(critical),
            LOADOUT: expr.Asset(loadout) if loadout else None,
            ASSISTS: (),
        }

    def _bot_died(self, killer, bot: bots.Bot, critical: bool, loadout, tick: int, *, source: int) -> None:
        self.stuns.pop(bot.entity, None)
        self.sleeps.pop(bot.entity, None)
        self.npc_impulses.pop(bot.entity, None)
        self.clear_pins(bot.entity)
        bot.died(tick)
        if not bot.retain_corpse:
            self.respawns.append((self.now + BOT_RESPAWN, bot))
        if not isinstance(killer, bots.Bot):
            killer.stats.add(stats.ELIMINATIONS)
            self._kill_notice(killer, bot, source)
        log.info("[game] %s: %s killed %s", self.match.label(), killer.name, bot.name)
        script = bot.body_script
        if script is None:
            return
        try:
            params = self._death_parameters(source, critical, loadout)
            if isinstance(script, BotScript):
                from ow174.game import npc_charge, npc_reposition

                npc_charge.stop(bot)
                npc_reposition.stop(bot)
                tick = max(tick, script.frame)
            event = script.die(tick, params, source)
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

    def _died(self, killer, victim, critical: bool, loadout, *, source: int | None = None) -> None:
        """A player died: the notices, his body script's "died" (the kill feed line), and the respawn."""
        self.stuns.pop(victim.body, None)
        self.sleeps.pop(victim.body, None)
        self.pile_air_locks.pop(victim.body, None)
        barriers.retire_owner(self, victim)
        self.clear_pins(victim.body)
        ball.cancel_grapple(self, victim)
        self.ball_contacts.pop(victim.body, None)
        label = self.match.label()
        log.info("[game] %s: %s killed %s", label, killer.name, victim.name)
        victim.stats.add(stats.DEATHS)
        bot_killer = isinstance(killer, bots.Bot)
        source = _source_entity(killer) if source is None else source
        if killer is not victim and not bot_killer:
            killer.stats.add(stats.ELIMINATIONS)
            self._kill_notice(killer, victim, source)
        if victim.client is not None:
            how = KILLED_BY if killer is not victim else 0
            notice = {"+0x78": how, "+0x7C": {"+0x0": source}}
            if how == KILLED_BY:
                self._queue_notice(victim, killer, source, DEATH_NOTICE, notice)
            else:  # the native "you died" event does not resolve a killer
                victim.client.queue_combat_notice(None, DEATH_NOTICE, notice)
        script = victim.body_script
        if script is not None:
            if hasattr(script, "timed_control_commands"):
                del script.timed_control_commands
            if hasattr(script, "grapple_interrupt_frame"):
                del script.grapple_interrupt_frame
            try:
                component = script.component
                params = self._death_parameters(source, critical, loadout)
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
