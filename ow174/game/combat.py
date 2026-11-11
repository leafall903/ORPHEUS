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
    blizzard,
    bots,
    deflection,
    dva,
    frost,
    hacks,
    impulses,
    mei_wall,
    npc_specials,
    observers,
    pools,
    projectiles,
    protections,
    stats,
    talon_hit,
    voicelines,
    weapon_rings,
    winston,
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
EYE_STANDING = {0x04000000000001C0: 1.5, 0x04000000000002A3: 1.425}
EYE_CROUCHED = {0x04000000000003CF: 1.085, 0x04000000000001C0: 1.05,
                0x04000000000002A3: .9975}  # FirstPerson m_816B830F; other bodies retain fallback
EYE_CROUCHED_DEFAULT = 1.15
HEAD_BELOW = 0.2  # m under the eye where the head starts (unverified)
HISTORY = 64  # ticks of body positions kept for the shots' view time (about 1 s; unverified: retail's limit)
COMBAT_CATCH_UP = 64  # at most about a second of retained combat frames per update
RANGE = 200.0  # m a hitscan shot reaches when its volley names none (unverified)
RESPAWN = 10.0  # s a hero waits in the PvP modes (0F4A and 0EC2 send 06FD.025 {10}: DATA)
PRACTICE_RESPAWN = 3.0  # s in the Practice Range (13C0 sends 06FD.025 {3}: DATA)
BOT_RESPAWN = 3.0  # s a training bot lies dead (unverified: OW2 made two killed bots again after 2.98 s)
RESPAWN_RETRY = 0.25  # s between attempts when all player body IDs are still reserved
MELEE = {0x0043: (4, 5)}  # graph -> (ModifyHealth state, TrackTargets state)
MELEE_FORWARD_EXPRESSION = [14, 0, 14, 1, 24, 6, 0, 6, 0, 6, 1, 58, 14, 2, 25, 23, 0]
ARMOUR_BLOCK = 5.0  # damage armour stops per hit (unverified: OW1's rule; the client has no damage code)
TICK = 0.016
EPSILON = 1e-9
FLASHBANG_EFFECT = 0x04A0000000000079  # .053/0079 ->0228 ->224E
SLEEP_DART_EFFECT = 0x04A000000000026F  # 0BDD/node14 direct hit
SLEEP_DART_SECONDS = 5.0  # declared host duration shared with the Ana lane
BIOTIC_GRENADE_EFFECT = 0x04A0000000000267  # .053/0267 -> C71 -> B99/C72
BIOTIC_SECONDS = 4  # B99 and C72 node 1
BIOTIC_HEAL = 100  # allied C71/B99 route
BIOTIC_BOOST = 1.5  # B99 node 8
ANA = 0x02E000000000013B
NANO_GRAPH = 0x0B9E
NANO_HEAL = 250  # 0269 -> B9F
NANO_OUTGOING = 1.5
NANO_INCOMING = 0.5
FLASHBANG_MODE_FLAG = 13408  # resource duration:1.7s when set, otherwise0.8s


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
    """Recorded FirstPerson standing/crouched fields; unspecified bodies retain defaults."""
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


def capsule_visible(collision: World | None, origin, a, b, radius: float, *, walls=None) -> bool:
    """Test occlusion to the nearest capsule surface against the supplied geometry."""
    ab, ap = _sub(b, a), _sub(origin, a)
    length = _dot(ab, ab)
    share = 0.0 if length <= EPSILON else max(0.0, min(1.0, _dot(ap, ab) / length))
    point = _along(a, ab, share)
    toward = _sub(point, origin)
    distance = math.sqrt(_dot(toward, toward))
    if distance <= radius + EPSILON:
        return True
    reach = distance - radius
    hit = ray_world(collision, origin, tuple(axis / distance for axis in toward), reach)
    return ((hit is None or hit + EPSILON >= reach)
            and (walls is None or not walls.blocks(origin, point, radius)))


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
        self.healing_blocks: list[tuple[int, int]] = []
        self.healing_boosts: list[tuple[int, int]] = []
        self.nano_until = 0
        self.frost_sources: dict[int, tuple[float, int]] = {}
        self.freeze_until = 0
        self.freeze_aim = (0, 0)

    def biotic(self, start: int, end: int, *, block: bool) -> None:
        windows = self.healing_blocks if block else self.healing_boosts
        if windows and start <= windows[-1][1]:
            previous_start, previous_end = windows.pop()
            start, end = previous_start, max(previous_end, end)
        windows.append((start, end))

    def healing_scalar(self, tick: int) -> float:
        if any(start <= tick < end for start, end in self.healing_blocks):
            return 0.0
        if any(start <= tick < end for start, end in self.healing_boosts):
            return BIOTIC_BOOST
        return 1.0

    @property
    def dead(self) -> bool:
        return self.died_at is not None

    @property
    def total(self) -> float:
        return sum(self.now.values())

    def damage(self, amount: float, *, beam: bool = False) -> float:
        """Take a hit from shields, then armour, then health (unverified, as OW1 did: what reaches armour
        while it has some is ARMOUR_BLOCK less, at most halved). Returns what the pools lost."""
        left = max(0.0, amount)
        taken = 0.0
        for kind in (SHIELDS_POOL, ARMOUR_POOL, HEALTH_POOL):
            if left <= 0.0:
                break
            if kind == ARMOUR_POOL and self.now[kind] > 0.0:
                # Reconstructed OW1 beam rule; do not apply the per-hit five-point
                # subtraction to each simulation frame of continuous damage.
                left = left * 0.8 if beam else max(left - ARMOUR_BLOCK, left / 2)
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
    effect: int | None = None  # ModifyHealth authored hit-effect resource

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
    definition: int | None = None  #003 target-tag input; unknown attacks cannot be classified
    damage_scale: float = 1.0  # outgoing multiplier captured at release
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
        self.heavy_charge_contacts: dict[int, set[int]] = {}
        self.heavy_charge_pins = {}
        self.sniper_repositions = {}
        self.heavy_charge_impact_requests: dict[int, tuple[tuple[int, ...], float]] = {}
        self.stuns: dict[int, int] = {}  # body identity -> exclusive world-tick deadline
        self.sleeps: dict[int, int] = {}  # separate from stun because damage wakes sleep
        self.tesla_frames: dict[int, int] = {}  # body -> last consumed command frame
        self.primal_bonus: dict[int, float] = {}  # body -> added maximum health
        self.cryo_samples: dict[int, tuple[tuple[int, int, int], float]] = {}
        self.blizzard = blizzard.Blizzard(self)
        self.walls = mei_wall.Walls(self)
        self.winston_jump = winston.JumpPack(self)
        self.barriers = winston.Barriers(self)
        self.impulses = impulses.Impulses(self)
        self.primal_punches = winston.PrimalPunches(self)
        self.assassins = npc_specials.Assassins(self)
        self.hacks = hacks.Hacks(self)
        self.zarya_barriers = zarya.Barriers(self)
        self.zarya_shields = zarya.ShieldRecovery(self)
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
        from ow174.game import heavy_charge

        heavy_charge.retire_victim(self, player)
        self.assassins.retire_victim(player)
        self.vitals.pop(player.body, None)
        self.history.pop(player.body, None)
        self.replacement_ticks.pop(player.body, None)
        self.melee_seen.pop(player.body, None)
        self.hacks.retire(player.body)
        self.stuns.pop(player.body, None)
        self.sleeps.pop(player.body, None)
        self.tesla_frames.pop(player.body, None)
        self.primal_bonus.pop(player.body, None)
        self.cryo_samples.pop(player.body, None)
        self.blizzard.retire(player.body)
        self.walls.retire(player.body)
        self.winston_jump.retire(player.body)
        self.barriers.retire(player.body)
        self.primal_punches.retire(player.body)
        self.zarya_barriers.retire(player.body)
        self.zarya_shields.retire(player.body)
        # The owner's leased projectiles and their observer copies retire with the old body.
        self.rockets[:] = [
            rocket for rocket in self.rockets if rocket.shooter is not player or rocket.shot is None
        ]
        self.respawns[:] = [(when, who) for when, who in self.respawns if who is not player]

    def record_replacement(self, body: int) -> None:
        """Keep the creation boundary independent of the rolling pose history."""
        self.replacement_ticks[body] = self.match.tick
        self.stuns.pop(body, None)
        self.sleeps.pop(body, None)

    def is_stunned(self, actor, *, tick: int | None = None) -> bool:
        return self.stuns.get(_source_entity(actor), 0) > (self.tick if tick is None else tick)

    def is_asleep(self, actor, *, tick: int | None = None) -> bool:
        return self.sleeps.get(_source_entity(actor), 0) > (self.tick if tick is None else tick)

    def frozen_aim(self, actor, *, tick: int | None = None):
        vitals = self.vitals.get(_source_entity(actor))
        at = self.tick if tick is None else tick
        if vitals is not None and at * 16 < vitals.freeze_until:
            return vitals.freeze_aim
        return None

    def movement_pause_until(self, actor) -> int:
        entity = _source_entity(actor)
        vitals = self.vitals.get(entity)
        freeze_end = (vitals.freeze_until + 15) // 16 if vitals is not None else 0
        return max(self.stuns.get(entity, 0), self.sleeps.get(entity, 0), freeze_end)

    def frost_slow(self, actor) -> float:
        vitals = self.vitals.get(_source_entity(actor))
        if vitals is None:
            return 1.0
        if self.frozen_aim(actor) is not None:
            return .3
        progress = max((value for value, until in vitals.frost_sources.values() if until > self.tick * 16),
                       default=0.0)
        return max(.3, 1.0 - progress)

    def _frost_hit(self, source, target, vitals, *, at=None,
                   initial=frost.INITIAL, step=frost.STEP) -> None:
        actor = target.owner
        if frost.immune(actor) or self.frozen_aim(actor) is not None:
            return
        at = self.tick * 16 if at is None else at
        progress, until = vitals.frost_sources.get(source, (0.0, 0))
        progress = max(initial, expr.f32((progress if at < until else 0.0) + step))
        if progress < 1.0:
            vitals.frost_sources[source] = progress, at + frost.GAP_MS
            return
        vitals.frost_sources.clear()
        vitals.freeze_until = at + frost.FREEZE_MS
        vitals.freeze_aim = ((round(actor.yaw), round(actor.pitch)) if isinstance(actor, bots.Bot)
                             else (actor.mover.yaw, actor.mover.pitch))
        self.interrupt_actions(actor)

    def actions_blocked(self, actor, *, tick: int | None = None):
        from ow174.game import heavy_charge

        return (self.is_stunned(actor, tick=tick) or self.is_asleep(actor, tick=tick)
                or self.frozen_aim(actor, tick=tick) is not None
                or self.assassins.controlled(actor) or heavy_charge.controlled(self, actor))

    def interrupt_actions(self, actor):
        script = getattr(actor, 'body_script', None)
        if script is None:
            return
        self.walls.interrupt(actor)
        if isinstance(actor, bots.Bot):
            from ow174.game import heavy_charge, sniper_reposition

            heavy_charge.stop(actor)
            sniper_reposition.cancel(self, actor)
        component = script.component
        component.edges([(value, False) for value in tuple(component.held)])
        for instance in list(component.instances.values()):
            for state in list(instance.states.values()):
                if isinstance(state, nodes.Ability):
                    state.presses.clear()
                    state.stop()
        component.run_now()
        component.shots.clear()
        if not isinstance(actor, bots.Bot):
            self.primal_punches.interrupt(script)
        self.assassins.interrupt(actor)


    def _hit_effect(self, target: Target, effect: int | None) -> None:
        """Apply reconstructed control after the authored hit reaches a living body."""
        if effect not in (FLASHBANG_EFFECT, SLEEP_DART_EFFECT) or self.vitals_of(target).dead:
            return
        if effect == SLEEP_DART_EFFECT:
            deadlines, duration = self.sleeps, SLEEP_DART_SECONDS
        else:
            variables = graph.mode_variables(self.match.game_map.mode_guid) or {}
            deadlines = self.stuns
            duration = 1.7 if expr.truthy(variables.get(FLASHBANG_MODE_FLAG)) else 0.8
        self._apply_control(target, deadlines, duration)

    def _apply_control(self, target, deadlines, duration):
        if protections.force_immune(target.owner):
            return
        deadline = self.tick + math.ceil(duration / TICK)
        deadlines[target.entity] = max(deadline, deadlines.get(target.entity, 0))
        script = getattr(target.owner, "body_script", None)
        if script is None:
            return
        self.interrupt_actions(target.owner)


    def spawned(self, player) -> None:
        """The player's client has the world: the game mode entity gets its UI graph (mode_frame). While
        heroes are assembled the countdown's frames carry it."""
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
        if (command is not None and command.frame == script.frame
                and script.world_frame_anchor is not None):
            script.world_frame_anchor = (self.tick, script.frame)
        shots, script.component.shots[:] = list(script.component.shots), []
        protections.advance_entry(player)
        if script.hero == protections.MEI:
            self._cryo_healing(player, world_idle=defer_effects)
        if defer_effects:
            return
        self._winston_health(player, script)
        dead = self.is_dead(player)
        self.winston_jump.command(player, command, dead=dead)
        if command is not None:
            self.barriers.release(player, self.now)
        if dead or self.actions_blocked(player) or protections.cryo_active(player):
            self.walls.interrupt(player)
            return
        if self.walls.command(player, command):
            return
        self.primal_punches.command(player, command)
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
        try:
            self._tesla(player, command, script)
        except Exception:
            log.exception("[game] %s: %s's Tesla Cannon failed", self.match.label(), player.name)

    def _cryo_healing(self, player, *, world_idle: bool) -> None:
        sample = protections.cryo_healing_sample(player)
        if sample is None:
            return
        key, elapsed, rate = sample
        total = elapsed * rate
        previous_key, previous = self.cryo_samples.get(player.body, (None, 0.0))
        self.cryo_samples[player.body] = key, total
        due = total - (previous if key == previous_key else 0.0)
        target = next((target for target in self._targets() if target.owner is player), None)
        if target is not None:
            vitals = self.vitals_of(target)
            if protections.cryo_active(player) and frost.immune(player):
                vitals.frost_sources.clear()
            if due <= 0 or vitals.dead:
                return
            if not world_idle or not (vitals.healing_blocks or vitals.healing_boosts):
                self._heal(player, target, due)
                return
            # Dense idle updates settle each body frame one world tick later.
            # Split the cumulative sample at those frame boundaries before
            # applying the original grenade windows, including partial frames.
            anchor_tick, anchor_frame = player.body_script.world_frame_anchor
            frame_ms = player.body_script.frame_ms
            tick_ms = round(TICK * 1000)
            end = key[2] + elapsed
            start = end - due / rate
            amount = 0.0
            for frame in range(math.floor((start + 1) / frame_ms), math.ceil((end + 1) / frame_ms)):
                lo = max(start, frame * frame_ms - 1)
                hi = min(end, (frame + 1) * frame_ms - 1)
                at = anchor_tick + (frame - anchor_frame) * frame_ms // tick_ms + 1
                amount += (hi - lo) * rate * vitals.healing_scalar(at)
            self._apply_healing(player, target, vitals, amount)

    def _winston_health(self, player, script) -> None:
        """Bridge 0109 st4's real lifetime to replicated pools without resurrecting.

        The authored HealthPool adds 500 maximum health; its ModifyHealth st5
        restores the new maximum. Ending the temporary pool clamps current HP.
        Pool-removal/clamp semantics are reconstructed pending native comparison.
        """
        if player.hero.guid != winston.HERO:
            return
        state = winston.primal_health(script.component)
        extra = _number(state.evaluate(state.node.field("m_amount"), watch=False), 0.0) if state else 0.0
        old = self.primal_bonus.get(player.body, 0.0)
        target = next((target for target in self._targets() if target.owner is player), None)
        if target is None:
            return
        vitals = self.vitals_of(target)
        if vitals.dead:
            extra = 0.0
        if old == extra:
            return
        vitals.most[HEALTH_POOL] += extra - old
        vitals.now[HEALTH_POOL] = min(vitals.now[HEALTH_POOL], vitals.most[HEALTH_POOL])
        vitals.changed.add(HEALTH_POOL)
        if extra > old and not vitals.dead:
            # Interpret C72/node17's tag293E filter as an exclusion for the
            # 0109/node8 transform refill. Ordinary healing uses _heal.
            vitals.heal(sum(vitals.most.values()))
        if extra:
            self.primal_bonus[player.body] = extra
        else:
            self.primal_bonus.pop(player.body, None)
        self._sync_body_health(player, vitals)

    def _tesla(self, player, command, script) -> None:
        """Reconstruct 00FE's continuous ModifyHealth using the actual active states.

        DATA: st10 searches eight metres, angle var243=40; st13 amount=60.
        Inference: angle is a cone half-angle and amount is damage per second.
        Native target payloads, barriers and status filters are still unaccepted.
        """
        if player.hero.guid != 0x02E0000000000009 or command is None:
            return
        if command.frame <= self.tesla_frames.get(player.body, -1):
            return
        self.tesla_frames[player.body] = command.frame
        for instance in script.component.instances.values():
            if instance.graph.index != 0x00FE:
                continue
            search, damage = instance.states.get(10), instance.states.get(13)
            if search is None or damage is None or not (search.active and damage.active):
                continue
            reach = _number(search.evaluate(search.node.field("m_radius"), watch=False), 0.0)
            angle = _number(search.evaluate(search.node.field("m_A30B383E"), watch=False), 0.0)
            definition = hit_def(damage, {"m_modifyHealth": damage.node.field("m_modifyHealth")})
            if definition is None or definition.amount <= 0 or reach <= 0 or not 0 < angle < 90:
                continue
            origin, direction = self._eye(player), aim(*command.aim())
            view_tick = command.frame - command.view_delay(player.quanta) * 1e6 / player.quanta
            barriers = self.barriers.snapshot()
            damaged_barriers = set()
            for target in self._enemies(player):
                shown = self.seen(target, view_tick)
                if shown is None:
                    continue
                a, b = shown.segment()
                # Aim at the closest capsule-axis point in height. Capsule radius
                # gives targets at the cone boundary their physical width.
                point = (a[0], max(a[1], min(b[1], origin[1])), a[2])
                offset = _sub(point, origin)
                distance = math.sqrt(_dot(offset, offset))
                if distance - shown.radius > reach:
                    continue
                forward = _dot(offset, direction)
                lateral = math.sqrt(max(0.0, distance * distance - forward * forward))
                if forward < 0 or lateral > forward * math.tan(math.radians(angle)) + shown.radius:
                    continue
                if distance > EPSILON:
                    ray = tuple(value / distance for value in offset)
                    wall = ray_world(self._collision(), origin, ray, distance)
                    if wall is not None and wall < max(0.0, distance - shown.radius):
                        continue
                    if self.walls.cast(origin, ray, max(0.0, distance - shown.radius)) is not None:
                        continue
                    obstruction = self.barriers.first(player, origin, ray, max(0.0, distance - shown.radius),
                                                       snapshot=barriers)
                    if obstruction is not None:
                        _, barrier = obstruction
                        if barrier.body not in damaged_barriers:
                            amount = definition.amount * player.quanta / 1e6
                            barrier.damage(amount * self.outgoing_scale(player))
                            damaged_barriers.add(barrier.body)
                        continue
                self._damage(player, target, definition.amount * player.quanta / 1e6,
                             False, definition.loadout, command.frame, beam=True)
            self.walls.tesla(player, origin, direction, reach, angle,
                             definition.amount * player.quanta / 1e6 * self.outgoing_scale(player), barriers)

    def _genji_deflects(self, target, incoming) -> bool:
        """Reviewed Genji d714f0c frontal melee block, scoped to actual st10.

        The hemisphere/body-capsule admission is reconstructed. Callers decide
        eligibility: Tesla and Jump impact do not use this melee-only query.
        """
        owner = target.owner
        if getattr(getattr(owner, "hero", None), "guid", None) != 0x02E0000000000029:
            return False
        if self.vitals_of(target).dead:
            return False
        script = owner.body_script
        if script is None:
            return False
        for instance in script.component.instances.values():
            if instance.graph.index == 0x03A3:
                state = instance.states.get(10)
                if state is not None and state.active:
                    return _dot(aim(owner.mover.yaw, owner.mover.pitch), incoming) < -EPSILON
        return False

    def _melee(self, player, command, script) -> None:
        """Apply 0043's health change once per activation with reconstructed eye/aim projection."""
        for instance in script.component.instances.values():
            spec = MELEE.get(instance.graph.index)
            if spec is None:
                continue
            index, search_index = spec
            state = instance.states.get(index)
            if state is None:
                continue
            counters = self.melee_seen.setdefault(script.entity, {})
            key = (instance.id, index)
            seen = counters.get(key, 0)
            counters[key] = state.counter
            if state.counter == seen or not state.active or command is None:
                continue
            search = instance.state(search_index)
            if search is None:
                continue
            centre_config = search.node.field("m_05BE62DD") or {}
            centre_expression = centre_config.get("m_expression") or {}
            literals = centre_expression.get("m_D99EF254") or ()
            # 0043 builds a local (0, 0, forward) offset before the owner-pose
            # operations. Reconstruct that pose with the combat eye and aim.
            if (centre_expression.get("m_opcodes") != MELEE_FORWARD_EXPRESSION
                    or len(literals) != 2 or literals[0] != 0
                    or not isinstance(literals[1], (int, float))):
                continue
            reach = literals[1]
            radius = _number(search.evaluate(search.node.field("m_radius"), watch=False), 0.0)
            if not (0 <= reach < math.inf and 0 < radius < math.inf):
                continue
            modify = state.node.field("m_modifyHealth") or {}
            amount = _number(state.evaluate(modify.get("m_amount"), watch=False), 0.0) if modify else 0.0
            loadout = (
                state.evaluate(modify.get("m_loadout"), watch=False) if modify.get("m_loadout") else None
            )
            loadout = loadout.guid if isinstance(loadout, expr.Asset) else None
            origin = self._eye(player)
            direction = aim(*command.aim())
            reaction_direction = direction if player.hero.guid == winston.HERO else None
            centre = _along(origin, direction, reach)
            view_tick = command.frame - command.view_delay(player.quanta) * 1e6 / player.quanta
            for target in self._enemies(player):
                shown = self.seen(target, view_tick)
                if shown is None:
                    continue
                a, b = shown.segment()
                point = (a[0], max(a[1], min(b[1], origin[1])), a[2])
                if (segment_distance(centre, a, b) - shown.radius <= radius and amount > 0.0
                        and capsule_visible(self._collision(), origin, a, b, shown.radius)
                        and not self.walls.blocks(origin, point, shown.radius)):
                    affected = self._damage(player, target, amount, False, loadout, command.frame,
                                            melee_direction=direction, reaction_direction=reaction_direction)
                    if affected and player.hero.guid == winston.HERO and not self.vitals_of(target).dead:
                        self.impulses.radial_health(target, player.mover.position, state)
            self.barriers.hit_capsule(player, centre, centre, radius, amount, origin)
            if amount > 0:
                self.walls.hit_volume(
                    player, origin, centre, centre, radius, amount * self.outgoing_scale(player),
                )

    def _advance_idle_body(self, player) -> None:
        """Commit older no-input windows for explicitly supported defense bodies.

        Retain the current window for an arriving command. The ordinary gap path
        releases input, advances authored timers/cooldowns and updates modifiers;
        no missing pose may create combat damage. This is reconstructed server
        policy, not native input transport/rollback equivalence.
        """
        script = player.body_script
        if (script is None or script.retired or not player.spawned or not player.has_body
                or script.world_frame_anchor is None):
            return
        if script.hero not in protections.IDLE_CLOCK_HEROES and not winston.reload_pending(script):
            return
        at, frame = script.world_frame_anchor
        elapsed = max(0, self.tick - at) * round(TICK * 1000)
        target = min(projectiles.MAX_LEASE_FRAME - 1, frame + elapsed // script.frame_ms - 1)
        if target > script.frame:
            player._advance_body_gap(target + 1, world_idle=True)

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
        combat_ticks = set(range(first, tick + 1))
        combat_ticks.update(self.blizzard.pending_ticks(self.tick, tick))
        barrier_releases = {}
        for player in self.match.players:
            release = self.barriers.pending_release(player, round(TICK * 1000))
            if release is not None:
                release_tick, frame = release
                # Leave the release frame available for real input. On the following
                # tick an idle release precedes projectile collisions.
                at = max(self.tick + 1, release_tick + 1)
                if at <= tick and frame < projectiles.MAX_LEASE_FRAME:
                    combat_ticks.add(at)
                    barrier_releases.setdefault(at, []).append((player, release_tick, frame))
        for rocket in self.rockets:
            if rocket.last_tick is None:
                continue  # new player input joins at the endpoint, as before
            # A pre-existing flight can meet a defense before its endpoint
            # expiry. Replay only its finite remaining flight, not the entire
            # scheduler gap, with the existing latest-pose reconstruction.
            begin = max(self.tick + 1, rocket.last_tick + 1)
            end = min(tick, rocket.last_tick + math.ceil(rocket.left / TICK))
            combat_ticks.update(range(begin, end + 1))
        for bot in self.match.bots:
            if npc_specials.clocked(bot):
                # Replay NPC special phases in chronological order. A late
                # run_frame starts chained waits at the gap's endpoint.
                combat_ticks.update(range(max(self.tick + 1, bot.body_script.frame + 1), tick))
        self.impulses.advance(tick)
        for target in self._targets():
            history = self.history.setdefault(target.entity, deque(maxlen=HISTORY))
            history.append((tick, target.feet, target.height, target.eye))
        # Replay the retained combat frames using the most recent poses. Player input
        # accepted just before the scheduler caught up joins only at the endpoint.
        for at in sorted(combat_ticks):
            self.now, self.tick = now - (tick - at) * TICK, at
            for player in self.match.players:
                if not player.gone and player.controller_component.frame < at:
                    player.controller_component.run_frame(at)
                self._advance_idle_body(player)
            for player, release_tick, frame in barrier_releases.get(at, ()):
                if not self.is_dead(player):
                    player._advance_body_gap(frame + 1, world_idle=True)
                    self.barriers.release(player, now - (tick - release_tick) * TICK)
            self.barriers.update()
            self.walls.update(at)
            self.hacks.update()
            self.blizzard.update(at)
            for rocket in list(self.rockets):
                if at == tick or rocket.last_tick is not None:
                    self._fly(rocket)
            self._bot_weapons(at)
            self.assassins.update()
            self.zarya_shields.update(at)
        self.projectiles.update(tick, first_tick=retained)
        frost_changed = set()
        for vitals in self.vitals.values():
            if vitals.frost_sources:
                for source, (_, until) in tuple(vitals.frost_sources.items()):
                    if until <= tick * 16:
                        del vitals.frost_sources[source]
                        frost_changed.add(vitals.entity)
            if vitals.freeze_until and vitals.freeze_until <= tick * 16:
                vitals.freeze_until = 0
                vitals.freeze_aim = (0, 0)
                frost_changed.add(vitals.entity)
            # Retire expired windows after fields settle their retained ticks.
            for windows in (vitals.healing_blocks, vitals.healing_boosts):
                if windows and windows[0][1] <= tick:
                    windows[:] = [(start, end) for start, end in windows if end > tick]
        for player in self.match.players:
            if player.body in frost_changed and player.body_script is not None:
                player._track_movement_mods(player.body_script.frame)
            winston.charge_world(player, self)
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
        for deadlines in (self.stuns, self.sleeps):
            for entity in [key for key, deadline in deadlines.items() if deadline <= tick]:
                del deadlines[entity]
        for table in (self.history, self.vitals, self.replacement_ticks, self.stuns, self.sleeps):
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
            data = bot.movement_data
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
            if protections.intangible(target.owner):
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
        for bot in self.match.bots:
            script = bot.body_script
            if bot.dead or not isinstance(script, BotScript) or script.frame > tick:
                continue
            auto_fire = bot.body == npc_specials.ENFORCER_BODY and bot.spawner.get('auto_fire', False)
            if bot.firing or auto_fire:
                frame = tick
            elif npc_specials.clocked(bot):
                # Reserve this tick's explicit input window while advancing
                # earlier graph-owned special phases.
                frame = tick - 1
            else:
                continue
            try:
                direction = npc_specials.enforcer_fire_direction(self, bot) if auto_fire else None
                weapon_input = (0, direction is not None) if auto_fire else None
                snapshot = script.advance_live(frame, bot.position, weapon_input=weapon_input)
                shots, script.component.shots[:] = list(script.component.shots), []
                for state, number, time, activation in shots:
                    enforcer_shot = auto_fire and state.instance.graph.index == npc_specials.ENFORCER_WEAPON
                    if enforcer_shot and direction is None:
                        continue
                    self._shoot(
                        bot, None, state, number, time, activation=activation,
                        snapshot=snapshot or script.latest_live,
                        npc_direction=direction if enforcer_shot else None,
                    )
                if snapshot is not None:
                    for viewer in self.match.players:
                        if viewer.spawned and viewer.client is not None:
                            viewer.client.queue_bot_live(bot.entity, snapshot)
            except Exception:
                from ow174.game import heavy_charge

                bot.body_script = None
                self.heavy_charge_contacts.pop(bot.entity, None)
                self.sniper_repositions.pop(bot.entity, None)
                self.heavy_charge_impact_requests.pop(bot.entity, None)
                heavy_charge.release(self, bot.entity)
                log.exception("[game] %s: %s's weapon script failed", self.match.label(), bot.name)

    def _eye(self, player) -> tuple:
        x, y, z = player.mover.position
        crouched = player.mover.crouched
        height = eye_height(player.hero.body, crouched)
        return (x, y + height, z)

    def nano_targets(self, body: int, state) -> tuple:
        """Host search for the authored Nano target states."""
        caster = next((p for p in self.match.players if p.has_body and p.body == body), None)
        if (caster is None or self.match.game_map.free_for_all or self.match.collision_world is None
                or self.is_dead(caster) or self.actions_blocked(caster)):
            return ()
        radius = _number(state.evaluate(state.node.field("m_radius"), watch=False), 0.0)
        origin, direction = self._eye(caster), aim(caster.mover.yaw, caster.mover.pitch)
        candidates = []
        for target in self._targets():
            if target.owner is caster or target.team != caster.team or self.vitals_of(target).dead:
                continue
            centre = (target.feet[0], target.feet[1] + target.height / 2, target.feet[2])
            delta = _sub(centre, origin)
            distance = math.sqrt(_dot(delta, delta))
            if not EPSILON < distance <= radius:
                continue
            alignment = _dot(direction, delta) / distance
            if alignment < math.cos(math.radians(10)):
                continue
            wall = ray_world(self._collision(), origin, tuple(v / distance for v in delta), distance)
            if wall is None or wall >= distance - EPSILON:
                candidates.append((-alignment, distance, target.entity))
        return (min(candidates)[2],) if candidates else ()

    def _nano_release(self, caster, instance) -> None:
        targets = instance.find_var(expr.INSTANCE, 8070, create=False)
        duration = instance.find_var(expr.INSTANCE, 215, create=False)
        seconds = _number(duration.value(), 0.0) if duration is not None else 0.0
        if targets is None or not math.isfinite(seconds) or seconds <= 0 or self.match.game_map.free_for_all:
            return
        selected = targets.value()
        if isinstance(selected, expr.Entity):
            selected = (selected,)
        if type(selected) is not tuple:
            return
        for target in self._targets():
            if (expr.Entity(target.entity) not in selected
                    or target.owner is caster or target.team != caster.team):
                continue
            vitals = self.vitals_of(target)
            if not vitals.dead:
                self._heal(caster, target, NANO_HEAL)
                vitals.nano_until = max(vitals.nano_until, self.tick + math.ceil(seconds / TICK))

    def outgoing_scale(self, actor) -> float:
        vitals = self.vitals.get(_source_entity(actor))
        boosted = vitals is not None and not vitals.dead and self.tick < vitals.nano_until
        return NANO_OUTGOING if boosted else 1.0

    def _shoot(
        self, shooter, command, state, number: int, time: int,
        *, activation: tuple[int, int], snapshot: Snapshot | None = None,
        npc_direction: tuple[float, float, float] | None = None,
    ) -> None:
        if self.actions_blocked(shooter):
            return
        node = state.node
        shot_frame = time // state.instance.component.frame_ms
        if state.instance.graph.index == NANO_GRAPH and state.index == 1:
            self._nano_release(shooter, state.instance)
            return

        def field(name, default=None):
            cfg = node.field(name)
            return default if cfg is None else _number(state.evaluate(cfg, watch=False), default)

        npc = isinstance(shooter, bots.Bot)
        if npc:
            x, y, z = shooter.position
            # Use the server body pose until the animated muzzle transform is available.
            origin = (x, y + EYE, z)
            direction = npc_direction if npc_direction is not None else aim(round(shooter.yaw), 0)
        else:
            origin, direction = self._eye(shooter), aim(*command.aim())
        direct = hit_def(state, node.field("m_6396149F"))
        entity_def = (node.field("m_projectileEntity") or {}).get("m_entityDef")
        asset = state.evaluate(entity_def, watch=False) if entity_def is not None else None
        definition = asset.guid if isinstance(asset, expr.Asset) else None
        directions = weapon_rings.directions(shooter, state, direction)
        speed = field("m_projectileSpeed")
        if speed:
            damage_scale = self.outgoing_scale(shooter)
            lifetime = field("m_projectileLifetime", 5.0) or 5.0
            if (definition == blizzard.DEFINITION and state.instance.graph.index == 0x064A
                    and state.index == 3):
                self.blizzard.launch(shooter, state, shot_frame, origin, direction, speed, lifetime,
                                     activation=activation, snapshot=snapshot)
                return
            reach = field("m_FEC435C6")
            if reach:
                lifetime = min(lifetime, reach / speed)
            blast = hit_def(state, node.field("m_D2D11CE9"))
            for ray in directions:
                shot = None
                if definition is not None:
                    shot = self.projectiles.fired(
                        shooter, state, shot_frame, definition, lifetime, origin, ray,
                        activation=activation, snapshot=snapshot,
                    )
                damaging = (direct is not None and direct.amount > 0.0) or (
                    blast is not None and blast.amount > 0.0
                )
                if damaging or shot is not None:
                    self.rockets.append(
                        Rocket(
                            shooter, shooter.team, origin, ray, speed, lifetime, direct, blast, shot,
                            last_tick=self.tick if npc else None, definition=definition,
                            damage_scale=damage_scale,
                            target_limit=frost.projectile_target_limit(state),
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
        for ray in directions:
            self._hitscan(shooter, origin, ray, reach, direct, view_tick, shot_frame,
                          world_tick=self.tick if npc else None, definition=definition)

    def _hitscan(self, shooter, origin, direction, reach, direct, view_tick, shot_frame,
                 *, world_tick=None, definition=None, reflections=0, travelled=0.0) -> None:
        """Resolve one ray with independent armour, barriers and Matrix ordering."""
        best = None
        for target in self._enemies(shooter):
            shown = self.seen(target, view_tick)
            if shown is None:
                continue
            a, b = shown.segment()
            t = ray_capsule(origin, direction, a, b, shown.radius)
            if t is not None and t <= reach and (best is None or t < best[0]):
                best = (t, target, shown)
        distance = best[0] if best else reach
        wall = ray_world(self._collision(), origin, direction, distance)
        limit = min(distance, wall) if wall is not None else distance
        pillar = self.walls.cast(origin, direction, limit)
        if pillar is not None:
            limit = pillar[0]
        obstruction = self.barriers.first(shooter, origin, direction, limit)
        limit = obstruction[0] if obstruction is not None else limit
        reflected = (deflection.genji_contact(self, shooter, definition, origin, direction, limit)
                     if reflections < 8 else None)
        if reflected is not None and reflected[0] >= limit:
            reflected = None
        absorbed = self._matrix_hit(shooter, origin, direction,
                                    reflected[0] if reflected is not None else limit, definition)
        if absorbed is not None:
            return
        if reflected is not None:
            contact, owner, facing = reflected
            point = _along(origin, direction, contact)
            self._hitscan(owner, point, facing, reach - contact, direct, self.tick, shot_frame,
                          world_tick=world_tick, definition=definition, reflections=reflections + 1,
                          travelled=travelled + contact)
            return
        if obstruction is not None:
            distance, barrier = obstruction
            barrier.damage(direct.amount * direct.scale(travelled + distance) * self.outgoing_scale(shooter))
            return
        if pillar is not None:
            distance, piece = pillar
            amount = direct.amount * direct.scale(travelled + distance) * self.outgoing_scale(shooter)
            self.walls.damage(piece, shooter, amount)
            return
        if best is None:
            return
        t, target, shown = best
        if wall is not None and wall < t:
            return
        hit_y = origin[1] + direction[1] * t
        critical = hit_y >= shown.feet[1] + shown.eye - HEAD_BELOW
        amount = direct.amount * direct.scale(travelled + t) * (direct.critical if critical else 1.0)
        if self._damage(
            shooter, target, amount, critical and direct.critical > 1.0, direct.loadout, shot_frame,
            world_tick=world_tick,
        ):
            self._hit_effect(target, direct.effect)

    def _matrix_hit(self, shooter, origin, direction, reach, definition):
        """Nearest live enemy Matrix; current authoritative pose (reconstructed).

        Callers compare this with the first body/static collision. Hitscan Matrix
        history and retail attachment/filter semantics still require acceptance.
        """
        if not deflection.absorbable(definition):
            return None
        best = None
        for target in self._enemies(shooter):
            player = target.owner
            if (isinstance(player, bots.Bot)
                    or getattr(getattr(player, "hero", None), "guid", None) != dva.HERO):
                continue
            distance = deflection.matrix_distance(
                player, origin, direction, reach, self._eye(player),
                aim(player.mover.yaw, player.mover.pitch),
            )
            if distance is not None and (best is None or distance < best):
                best = distance
        return best


    def _fly(self, rocket: Rocket, *, duration=None, reflections=0) -> None:
        """Sweep elapsed world time against the current bodies and static collision."""
        # Player input can arrive immediately before the scheduler accounts for a gap.
        if duration is None:
            elapsed = 1 if rocket.last_tick is None else self.tick - rocket.last_tick
            if elapsed <= 0:
                return
            rocket.last_tick = self.tick
            duration = min(elapsed * TICK, rocket.left)
        if duration <= 0.0:
            self._landed(rocket)
            return
        # Sweep the remaining flight even when the range or lifetime ends inside this tick.
        step = rocket.speed * duration
        biotic = rocket.blast is not None and rocket.blast.effect == BIOTIC_GRENADE_EFFECT
        piercing = rocket.target_limit > 1 and rocket.direct is not None and rocket.blast is None
        contacts = []
        best = None
        candidates = self._targets() if biotic else self._enemies(rocket.shooter)
        for target in candidates:
            if biotic and (target.owner is rocket.shooter or self.vitals_of(target).dead):
                continue
            a, b = target.segment()
            t = ray_capsule(rocket.position, rocket.direction, a, b, target.radius)
            if t is not None and t <= step:
                if piercing:
                    if target.entity not in rocket.hit_entities:
                        contacts.append((t, target))
                elif best is None or t < best[0]:
                    best = (t, target)
        wall = ray_world(self._collision(), rocket.position, rocket.direction, step)
        if wall is not None and (best is None or wall < best[0]):
            best = (wall, None)
        pillars = tuple(self.walls.pieces.values()) if rocket.blast is not None else None
        wall_hit = self.walls.cast(rocket.position, rocket.direction, step, pieces=pillars)
        pillar = None
        if wall_hit is not None and (best is None or wall_hit[0] < best[0]):
            distance, pillar = wall_hit
            best = (distance, None)
        barriers = self.barriers.snapshot()
        obstruction = self.barriers.first(rocket.shooter, rocket.position, rocket.direction, step,
                                          snapshot=barriers)
        barrier = None
        if obstruction is not None and (best is None or obstruction[0] <= best[0]):
            distance, barrier = obstruction
            best = (distance, None)
            pillar = None
        limit = best[0] if best is not None else step
        reflected = (deflection.genji_contact(self, rocket.shooter, rocket.definition, rocket.position,
                                              rocket.direction, limit) if reflections < 8 else None)
        if reflected is not None and best is not None and reflected[0] >= limit:
            reflected = None
        defense_limit = reflected[0] if reflected is not None else limit
        absorbed = self._matrix_hit(rocket.shooter, rocket.position, rocket.direction,
                                    defense_limit, rocket.definition)
        if piercing:
            for distance, target in sorted(contacts, key=lambda hit: (hit[0], hit[1].entity)):
                if ((best is not None and distance >= best[0])
                        or (absorbed is not None and distance >= absorbed)
                        or (reflected is not None and distance >= reflected[0])):
                    break
                rocket.hit_entities.add(target.entity)
                direct = rocket.direct
                amount = direct.amount * direct.scale(rocket.flown * rocket.speed + distance)
                if self._damage(rocket.shooter, target, amount, False, direct.loadout, self.tick,
                                world_tick=self.tick, source_scale=rocket.damage_scale):
                    self._hit_effect(target, direct.effect)
                if len(rocket.hit_entities) >= rocket.target_limit:
                    rocket.flown += distance / rocket.speed
                    self._landed(rocket)
                    return
        if absorbed is not None:
            rocket.flown += absorbed / rocket.speed
            self.rockets.remove(rocket)
            if rocket.shot is not None:
                self.projectiles.absorbed(rocket.shot)
            return
        if reflected is not None:
            contact, owner, facing = reflected
            spent = contact / rocket.speed
            rocket.position = _along(rocket.position, rocket.direction, contact)
            rocket.direction = facing
            rocket.shooter, rocket.team = owner, owner.team
            rocket.flown += spent
            rocket.left -= spent
            rocket.damage_scale = self.outgoing_scale(owner)
            rocket.hit_entities.clear()
            if rocket.shot is not None:
                self.projectiles.absorbed(rocket.shot)
                rocket.shot = None
            if rocket.left <= 0:
                self._landed(rocket)
            elif duration > spent:
                self._fly(rocket, duration=duration - spent, reflections=reflections + 1)
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
        if barrier is not None or pillar is not None:
            # The triggering impact consumes the projectile, even when it breaks
            # the shield. Splash is separately occluded against this tick's shape.
            direct_damage = (rocket.direct.amount * rocket.direct.scale(rocket.flown * rocket.speed)
                             if rocket.direct else 0.0)
            blast_damage = rocket.blast.amount * rocket.blast.splash(0.0) if rocket.blast else 0.0
            amount = (direct_damage + blast_damage) * rocket.damage_scale
            if barrier is not None:
                barrier.damage(amount)
            else:
                self.walls.damage(pillar, rocket.shooter, amount)
            point = _along(rocket.position, rocket.direction, max(0.0, t - .001))
        elif struck is None and wall is not None:
            # Query splash from the approach side. Starting exactly on the
            # contact triangle loses its zero-distance static occlusion.
            point = _along(point, rocket.direction, -.0001)
        if (struck is not None and rocket.direct is not None and rocket.direct.amount > 0.0
                and self._damage(rocket.shooter, struck,
                                 rocket.direct.amount * rocket.direct.scale(rocket.flown * rocket.speed),
                                 False,
                                 rocket.direct.loadout, self.tick, world_tick=self.tick,
                                 source_scale=rocket.damage_scale)):
            self._hit_effect(struck, rocket.direct.effect)
        blast = rocket.blast
        if blast is None or blast.amount <= 0.0:
            return
        biotic_deadline = self.tick + math.ceil(BIOTIC_SECONDS / TICK)
        candidates = self._targets() if biotic else self._enemies(rocket.shooter)
        for target in candidates:
            vitals = self.vitals_of(target)
            if vitals.dead:
                continue
            a, b = target.segment()
            distance = max(0.0, segment_distance(point, a, b) - target.radius)
            scalar = blast.splash(distance)
            if scalar > 0.0:
                centre = (target.feet[0], target.feet[1] + target.height / 2, target.feet[2])
                delta = _sub(centre, point)
                length = math.sqrt(_dot(delta, delta))
                if length > EPSILON:
                    direction = tuple(value / length for value in delta)
                    wall = ray_world(self._collision(), point, direction, length)
                    blocked = self.barriers.first(rocket.shooter, point, direction, length,
                                                   snapshot=barriers)
                    reach = max(0.0, length - target.radius)
                    pillar_block = self.walls.cast(point, direction, reach, pieces=pillars)
                    if (wall is not None and wall < reach) or blocked or pillar_block is not None:
                        continue
                if biotic and (target.owner is rocket.shooter or (
                    not self.match.game_map.free_for_all and target.team == rocket.shooter.team
                )):
                    self._heal(rocket.shooter, target, BIOTIC_HEAL * scalar)
                    vitals.biotic(self.tick, biotic_deadline, block=False)
                elif self._damage(
                    rocket.shooter, target, blast.amount * scalar, False, blast.loadout, self.tick,
                    world_tick=self.tick, source_scale=rocket.damage_scale,
                ):
                    if biotic and not vitals.dead:
                        vitals.biotic(self.tick, biotic_deadline, block=True)
                    self._hit_effect(target, blast.effect)

    def _landed(self, rocket: Rocket) -> None:
        """The projectile ended (hit, range, lifetime): the predicted one's release follows."""
        if rocket in self.rockets:
            self.rockets.remove(rocket)
        if rocket.shot is not None:
            self.projectiles.flown(rocket.shot, rocket.flown)

    def heal_area(self, healer, centre, radius: float, amount: float, *, ticks: range | None = None) -> None:
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
            self._heal(healer, target, amount, ticks=ticks)

    def _heal(self, healer, target: Target, amount: float, *, ticks: range | None = None) -> None:
        vitals = self.vitals_of(target)
        if vitals.dead:
            return
        if vitals.healing_blocks or vitals.healing_boosts:
            if ticks is None:
                amount *= vitals.healing_scalar(self.tick)
            else:
                amount *= sum(vitals.healing_scalar(tick) for tick in ticks) / len(ticks)
        self._apply_healing(healer, target, vitals, amount)

    def _apply_healing(self, healer, target: Target, vitals: Vitals, amount: float) -> None:
        given = vitals.heal(amount)
        if given > 0:
            if not isinstance(healer, bots.Bot):
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

    def _damage(
        self, shooter, target: Target, amount: float, critical: bool, loadout, frame: int,
        *, world_tick: int | None = None, beam: bool = False, melee_direction=None, source_scale=None,
        reaction_direction=None,
    ) -> bool:
        """Return whether the body contact permits its associated knockback.

        Absorbed/parried/intangible contacts return false before health/stats.
        """
        vitals = self.vitals_of(target)
        if vitals.dead or amount <= 0.0 or protections.intangible(target.owner):
            return False
        amount *= self.outgoing_scale(shooter) if source_scale is None else source_scale
        if self.zarya_barriers.intercept(target.owner, amount, _source_entity(shooter)):
            return False
        if melee_direction is not None and self._genji_deflects(target, melee_direction):
            return False
        if self.tick < vitals.nano_until:
            amount *= NANO_INCOMING
        amount *= protections.incoming_scale(target.owner)
        dealt = vitals.damage(amount, beam=beam)
        if dealt <= 0.0:
            return False
        self.sleeps.pop(target.entity, None)
        self.hacks.received_damage(target.owner, _source_entity(shooter))
        if melee_direction is not None:
            self.assassins.interrupt(target.owner)
        self.zarya_shields.damaged(target.owner, self.tick if world_tick is None else world_tick)
        killed = vitals.now[HEALTH_POOL] <= EPSILON
        if killed:
            vitals.now[HEALTH_POOL] = 0.0
            vitals.healing_blocks.clear()
            vitals.healing_boosts.clear()
            vitals.nano_until = 0
            vitals.frost_sources.clear()
            vitals.freeze_until = 0
            vitals.freeze_aim = (0, 0)
            vitals.died_at = self.now
            vitals.death_tick = self.match.tick if world_tick is None else world_tick
            vitals.killer = _source_entity(shooter)
        elif loadout == frost.SPRAY_LOADOUT:
            self._frost_hit(_source_entity(shooter), target, vitals)
        if not isinstance(shooter, bots.Bot):
            flags = (CRITICAL if critical else 0) | (KILLING if killed else 0)
            self._tell_hit(shooter, target, dealt, flags, frame)
            shooter.stats.add(stats.HERO_DAMAGE, dealt)
            if (target.owner is not shooter
                    and (self.match.game_map.free_for_all or target.team != shooter.team)):
                self._winston_charge(shooter, dealt, target)
        victim = target.owner
        if killed and isinstance(victim, bots.Bot):
            # The NPC death script owns its terminal health change and death event ordering.
            self._bot_died(shooter, victim, critical, loadout, vitals.death_tick)
            return True
        self._sync_body_health(victim, vitals)
        if killed:
            self._died(shooter, victim, critical, loadout)
        elif reaction_direction is not None and isinstance(victim, bots.Bot):
            tick = self.match.tick if world_tick is None else world_tick
            talon_hit.notify_damage(victim, _source_entity(shooter), dealt, reaction_direction, tick)
        return True

    def _winston_charge(self, player, dealt: float, target: Target) -> None:
        """Original0B8B arithmetic; native target/tag/mode admission remains open."""
        owner = target.owner
        body_definition = owner.body if isinstance(owner, bots.Bot) else owner.hero.body
        winston.charge_damage(player.body_script, dealt, target.entity, body_definition, owner.body_script)

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
        from ow174.game import heavy_charge

        self.heavy_charge_contacts.pop(bot.entity, None)
        self.sniper_repositions.pop(bot.entity, None)
        self.heavy_charge_impact_requests.pop(bot.entity, None)
        heavy_charge.release(self, bot.entity)
        self.hacks.retire(bot.entity)
        self.stuns.pop(bot.entity, None)
        self.sleeps.pop(bot.entity, None)
        self.assassins.release(bot.entity)
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
        from ow174.game import heavy_charge

        heavy_charge.retire_victim(self, victim)
        self.hacks.retire(victim.body)
        self.cryo_samples.pop(victim.body, None)
        self.assassins.retire_victim(victim)
        self.stuns.pop(victim.body, None)
        self.sleeps.pop(victim.body, None)
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
        try:
            self.winston_jump.cancel(victim.body)
            if script is not None:
                component = script.component
                params = self._death_parameters(killer, critical, loadout)
                if runtime.send_message(component, DIED, params, source):
                    # The server owns the death event. Run it before the observer snapshot even
                    # if the victim sends no further commands.
                    component.run_now()
                if script.hero == winston.HERO:
                    self.primal_punches.interrupt(script)
                    self._winston_health(victim, script)
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
