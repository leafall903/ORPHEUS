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
  command's aim from per-body STUFirstPersonComponent offsets (m_E29993A5 standing,
  m_816B830F crouched, DATA; animated camera/muzzle equivalence unverified), or a projectile
  (m_projectileSpeed) flown each tick. Bodies without recorded offsets retain the existing fallback.
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
    barrage_motion,
    bots,
    cryo,
    deflection,
    fortify,
    matrix,
    mei_blizzard,
    mei_frost,
    mei_wall,
    observers,
    pharah_concussion,
    phase_defenses,
    pools,
    projectiles,
    reinhardt_barrier,
    sigma_grasp,
    stats,
    symmetra,
    symmetra_charge,
    transcendence,
    voicelines,
    winston_barrier,
    world,
    zarya_barriers,
)
from ow174.game.bits import BitWriter
from ow174.game.collision import MOVER, World
from ow174.game.commands import Command
from ow174.game.mover import mover_data
from ow174.game.script import expr, graph, nodes, runtime
from ow174.game.script.bot import DIED, BotScript
from ow174.game.script.driver import Snapshot
from ow174.game.statescript import InstanceWire, StateWire, remote_full_frame
from ow174.game.symmetra_turrets import Turrets
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
# Pinned104319 FirstPerson fields for Symmetra26F0, McCree02A3 and Soldier03CF.
EYE_STANDING = {0x04000000000026F0: 1.3, 0x04000000000002A3: 1.425}
EYE_CROUCHED = {0x04000000000003CF: 1.085, 0x04000000000026F0: .91, 0x04000000000002A3: .9975}
EYE_CROUCHED_DEFAULT = 1.15
MEI_ICICLE_LOADOUT = 0x0B900000000000D4
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
BEAM_ARMOUR_SCALAR = 0.7  # OW1 live patch 2020-08-31; explicit beam callers only
TICK = 0.016
EPSILON = 1e-9
FLASHBANG_EFFECT = 0x04A0000000000079  # .053/0079 ->0228 ->224E
FLASHBANG_MODE_FLAG = 13408  # 0228 duration: flag set1.7s, otherwise0.8s


def _tick_at_or_after(now, tick, deadline):
    """Map a deadline to the first world tick whose reconstructed time reaches it."""
    at = tick - math.floor((now - deadline) / TICK)
    if now - (tick - at) * TICK < deadline:
        return at + 1
    if now - (tick - at + 1) * TICK >= deadline:
        return at - 1
    return at


# --- geometry -----------------------------------------------------------------------------------------


def eye_height(body: int, crouched: bool = False) -> float:
    """104319 authored first-person offsets; animated camera/muzzle equivalence is open."""
    return EYE_CROUCHED.get(body, EYE_CROUCHED_DEFAULT) if crouched else EYE_STANDING.get(body, EYE)


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


def capsule_ray(origin, target):
    """Return the ray to the nearest surface of a target's combat capsule."""
    a, b = target.segment()
    ab = _sub(b, a)
    length = _dot(ab, ab)
    t = 0.0 if length <= EPSILON else max(0.0, min(1.0, _dot(_sub(origin, a), ab) / length))
    delta = _sub(_along(a, ab, t), origin)
    distance = math.sqrt(_dot(delta, delta))
    reach = max(0.0, distance - target.radius)
    if reach <= EPSILON:
        return (0.0, 0.0, 1.0), 0.0
    return tuple(axis / distance for axis in delta), reach


def visible_capsule(collision, origin, target) -> bool:
    """Reconstructed LOS to the nearest surface of a target's combat capsule."""
    direction, reach = capsule_ray(origin, target)
    if reach <= EPSILON:
        return True
    wall = ray_world(collision, origin, direction, reach)
    return wall is None or wall >= reach - EPSILON


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
        self.last_damage_at: float | None = None
        self.shield_accounted_at: float | None = None
        self.bonus_shields = 0.0
        self.bonus_shields_max = 0.0

    @property
    def dead(self) -> bool:
        return self.died_at is not None

    @property
    def total(self) -> float:
        return sum(self.now.values()) + self.bonus_shields

    def damage(self, amount: float, *, beam: bool = False) -> float:
        """Take damage through shields, armour, health; return the actual pool loss.

        Explicit beams use OW1's 30% armour reduction (live patch 2020-08-31).
        Other hits retain ARMOUR_BLOCK, at most half. A sample beginning in
        armour keeps its reduced remainder on spillover, as the existing hit
        policy does; native continuous-beam exhaustion boundaries remain open.
        """
        left = max(0.0, amount)
        taken = min(left, self.bonus_shields)
        if taken > 0.0:
            self.bonus_shields -= taken
            self.changed.add(SHIELDS_POOL)
            left -= taken
        for kind in (SHIELDS_POOL, ARMOUR_POOL, HEALTH_POOL):
            if left <= 0.0:
                break
            if kind == ARMOUR_POOL and self.now[kind] > 0.0:
                left = left * BEAM_ARMOUR_SCALAR if beam else max(left - ARMOUR_BLOCK, left / 2)
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
        if self.bonus_shields_max:
            # Reconstructed extra pool slot and transport flag, independent of base shields.
            value = world.pool(4, SHIELDS_POOL, self.bonus_shields_max, self.bonus_shields)
            parts[SHIELDS_POOL * POOLS + 1] = value[:17] + bytes((0,)) + value[18:]
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
    deflectable: bool = False

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
        # The extracted Mei icicle omits its multiplier. Use the scoped
        # reconstruction for 05E6 state 16 when the field is absent.
        critical=value("m_A872C70E", 2.0 if (
            state.instance.graph.index == 0x05E6 and state.index == 16
        ) else 1.0),
        rings=tuple(sorted(rings)),
        effect=effect.guid if isinstance(effect, expr.Asset) else None,
        deflectable=any(
            isinstance(value := state.evaluate(tag.get("m_EE3055BD"), watch=False), expr.Asset)
            and value.guid == deflection.DEFLECTABLE
            for tag in modify.get("m_148F3152") or () if isinstance(tag, dict)
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
    distance_falloff: bool = False
    definition: int | None = None
    target_limit: int = 1
    hit_entities: set[int] = field(default_factory=set)
    implicit_absorbable: bool = False
    motion: tuple = ()
    concussion: tuple = ()
    reflected: bool = False

    def impact_damage(self) -> float:
        if self.direct is None:
            return 0.0
        scale = self.direct.scale(self.flown * self.speed) if self.distance_falloff else 1.0
        return self.direct.amount * scale

    def deflectable(self) -> bool:
        return any(hit is not None and hit.deflectable for hit in (self.direct, self.blast))


class Combat:
    """The match's health, hits, deaths and respawns."""

    def __init__(self, match) -> None:
        self.match = match
        self.frost_sources: dict[int, dict[tuple[int, int], tuple[float, int, bool]]] = {}
        self.frost_hits: dict[int, tuple[float, int, int, int, int]] = {}
        self.cryo_samples: dict[int, tuple[tuple[int, int, int], float]] = {}
        self.vitals: dict[int, Vitals] = {}  # body or bot entity -> its health
        self.history: dict[int, deque] = {}  # entity -> (tick, feet, height, eye) of the last HISTORY ticks
        self.replacement_ticks: dict[int, int] = {}  # replacement body -> authoritative world tick
        self.rockets: list[Rocket] = []
        self.respawns: list[tuple[float, object]] = []  # (when, a player or a bot)
        self.early_projectile_respawns: set[int] = set()  # dead bodies with a historical impact
        self.next_bot = bots.BOT_ENTITY + len(match.bots)
        self.now = 0.0
        self.tick = 0
        self.melee_seen: dict[int, dict[tuple[int, int], int]] = {}  # body -> counters by instance/state
        self.stuns: dict[int, int] = {}  # body identity -> exclusive world-tick deadline
        self.heavy_charge_pins: dict[int, runtime.Instance] = {}
        self.pins: dict[int, int] = {}  # Assassin body -> controlled victim body
        self.phase_clocks: dict[int, tuple[int, int]] = {}
        self.grasp = sigma_grasp.SigmaGrasp(self)
        self.zarya_barriers = zarya_barriers.Barriers(self)
        self.projectiles = projectiles.Projectiles(self)  # leases, confirmations, the Biotic Field
        self.observers = observers.Observers(match)  # the bodies' statescript for the other clients
        self.voice = voicelines.VoiceLines(match)  # the hero's spawn voice line (voicelines.py)
        self.sentries = Turrets(self)
        self.blizzard = mei_blizzard.Blizzard(self)
        self.ice_wall = mei_wall.Walls(self)
        from ow174.game.symmetra_barriers import Barriers

        self.barriers = Barriers(self)
        self.reinhardt_barriers = reinhardt_barrier.ReinhardtBarriers(self)
        self.winston_barriers = winston_barrier.Barriers(self)
        from ow174.game.symmetra_portals import Portals

        self.portals = Portals(self)

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
        self.clear_pins(player.body)
        self.phase_clocks.pop(player.body, None)
        self.cryo_samples.pop(player.body, None)
        self.clear_frost(player)
        self.blizzard.retire(player.body)
        self.ice_wall.retire(player.body)
        self.grasp.retire(player.body)
        self.zarya_barriers.retire(player.body)
        self.reinhardt_barriers.retire(player.body)
        self.winston_barriers.retire(player.body)
        # The owner's leased projectiles and their observer copies retire with the old body.
        self.rockets[:] = [
            rocket for rocket in self.rockets if rocket.shooter is not player or rocket.shot is None
        ]
        self.respawns[:] = [(when, who) for when, who in self.respawns if who is not player]
        if tracked := getattr(self, "early_projectile_respawns", None):
            tracked.discard(player.body)

    def record_replacement(self, body: int) -> None:
        """Keep the creation boundary independent of the rolling pose history."""
        self.replacement_ticks[body] = self.match.tick
        self.stuns.pop(body, None)

    def is_stunned(self, actor, *, tick: int | None = None) -> bool:
        entity = _source_entity(actor)
        sample = self.frost(actor, tick=tick)
        return (bool(sample is not None and sample[2]) or entity in self.pins.values()
                or self.stuns.get(entity, 0) > (self.tick if tick is None else tick))

    def frost(self, actor, *, tick: int | None = None):
        at = self.tick if tick is None else tick
        return mei_frost.live(self.frost_hits.get(_source_entity(actor)), at * round(TICK * 1000))

    def clear_frost(self, actor) -> None:
        entity = _source_entity(actor)
        self.frost_sources.pop(entity, None)
        if self.frost_hits.pop(entity, None) is not None and actor.body_script is not None:
            actor.body_script.component.set(mei_frost.PROGRESS_VAR, 0.0)

    def _advance_frost(self, now: int) -> None:
        for player in (*self.match.players, *self.match.bots):
            entity = _source_entity(player)
            previous = self.frost_hits.get(entity)
            if previous is None:
                continue
            script = player.body_script
            if (mei_frost.live(previous, now) is None or script is None
                    or mei_frost.immune(script.component) or phase_defenses.active(player)):
                self.clear_frost(player)
                continue
            sources = self.frost_sources.get(entity)
            if sources is None:
                continue
            count = len(sources)
            mei_frost.prune(sources, now)
            if len(sources) != count:
                sample = mei_frost.aggregate(sources, now, *previous[3:])
                self.frost_hits[entity] = sample
                script.component.set(mei_frost.PROGRESS_VAR, sample[0])

    def apply_mei_frost(
        self, shooter, target: Target, *, channel=0, now_ms=None,
        initial=mei_frost.INITIAL, step=mei_frost.STEP,
    ) -> None:
        player = target.owner
        bot = isinstance(player, bots.Bot)
        if (player.body_script is None or (bot and player.combat_owner is not None)
                or fortify.scale(player) is not None):
            return
        component = player.body_script.component
        if mei_frost.immune(component):
            return
        now = self.tick * round(TICK * 1000) if now_ms is None else now_ms
        entity = target.entity
        previous = mei_frost.live(self.frost_hits.get(entity), now)
        if previous is not None and previous[2]:
            return
        sources = self.frost_sources.setdefault(entity, {})
        mei_frost.prune(sources, now)
        source = (_source_entity(shooter), channel)
        progress, _, _ = sources.get(source, (0.0, 0, False))
        sources[source] = mei_frost.source_hit(shooter, progress, now, initial=initial, step=step)
        pose = player if bot else player.mover
        sample = mei_frost.aggregate(sources, now, round(pose.yaw), round(pose.pitch))
        self.frost_hits[entity] = sample
        component.set(mei_frost.PROGRESS_VAR, sample[0])
        if sample[2]:
            self.frost_sources.pop(entity, None)
            self.pins.pop(entity, None)
            self._interrupt(target)

    def clear_pins(self, entity: int) -> None:
        from ow174.game import heavy_charge

        for source, victim in tuple(self.pins.items()):
            if entity in (source, victim):
                heavy_charge.release(self, source)
                self.pins.pop(source, None)

    def _hit_effect(self, target: Target, effect: int | None) -> None:
        """Resource-backed stun reconstruction; native immunity/presentation remain unverified."""
        if (effect != FLASHBANG_EFFECT or self.vitals_of(target).dead
                or fortify.scale(target.owner) is not None):
            return
        variables = graph.mode_variables(self.match.game_map.mode_guid) or {}
        duration = 1.7 if expr.truthy(variables.get(FLASHBANG_MODE_FLAG)) else 0.8
        deadline = self.tick + math.ceil(duration / TICK)
        self.stuns[target.entity] = max(deadline, self.stuns.get(target.entity, 0))
        self.pins.pop(target.entity, None)
        self._interrupt(target)

    def _interrupt(self, target: Target) -> None:
        script = getattr(target.owner, "body_script", None)
        if script is None:
            return
        component = script.component
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

    def command(self, player, command: Command | None) -> None:
        """Consume the body's reports. Only a frame with a pose can affect targets."""
        if self.match.collision_world is None:
            return
        script = player.body_script
        if script is None:
            return
        shots, script.component.shots[:] = list(script.component.shots), []
        if self.is_dead(player) or self.is_stunned(player):
            self.ice_wall.cancel_preview(player)
            script.world.beam_barrier = None
            return
        transcendence.advance_entry(player)
        phase_defenses.advance_entry(player)
        cryo.advance_entry(player)
        self._heal_cryo(player)
        frozen = cryo.active(player)
        if frozen:
            self.ice_wall.cancel_preview(player)
            self.clear_frost(player)
            self.sentries.release_target(player.body)
        phased = frozen or phase_defenses.active(player)
        if command is not None and (
                phased or transcendence.invulnerable(player) or phase_defenses.has_active_cover(player)):
            self.phase_clocks.setdefault(player.body, (self.tick, script.frame))
        if phased:
            return
        if self.ice_wall.command(player, command):
            return
        self.winston_barriers.command(player, command)
        if command is not None:
            for state, number, time, activation in shots:
                try:
                    self._shoot(player, command, state, number, time, activation=activation)
                except Exception:
                    log.exception("[game] %s: %s's shot failed", self.match.label(), player.name)
        symmetra.primary(self, player, command)
        try:
            self._melee(player, command, script)
        except Exception:
            log.exception("[game] %s: %s's melee failed", self.match.label(), player.name)

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
            self.reinhardt_barriers.melee(player, origin, centre, radius, amount)
            self.winston_barriers.melee(player, origin, centre, radius, amount)
            view_tick = command.frame - command.view_delay(player.quanta) * 1e6 / player.quanta
            for target in self._enemies(player):
                shown = self.seen(target, view_tick)
                if shown is None:
                    continue
                a, b = shown.segment()
                # Preserve tangent contact when the measured distance rounds one float upward.
                contact = math.nextafter(radius + shown.radius, math.inf)
                if (segment_distance(centre, a, b) <= contact and amount > 0.0
                        and self.visible_target(origin, shown)):
                    self._damage(player, target, amount, False, loadout, command.frame)
            self.ice_wall.melee(player, origin, centre, radius, amount)

    def _advance_symmetra_charge(self, players, tick, *, before_combat=False):
        for player in players:
            try:
                symmetra_charge.advance_idle(player, tick, round(TICK * 1000), before_combat=before_combat)
            except Exception:
                log.exception("[game] %s: %s's charge statescript failed", self.match.label(), player.name)
                self.match.stop_body_script(player, failed=True)

    def _advance_phase_clocks(self, damage_starts, cryo_expiries):
        if not self.phase_clocks:
            return
        for player in self.match.players:
            damage_start = phase_defenses.advance_idle(self, player)
            if damage_start is not None:
                damage_starts[player.body] = damage_start
                if player.hero.guid == cryo.HERO:
                    cryo_expiries[player.body] = damage_start

    def replays_orb_routes(self, tick: int) -> bool:
        return self.tick + 1 < tick and any(bot.route for bot in self.match.bots) and any(
            rocket.definition == symmetra.ORB_PROJECTILE and rocket.last_tick is not None
            for rocket in self.rockets
        )

    def _step_route_poses(self):
        for bot in self.match.bots:
            if bot.route:
                self.match.step_bot_pose(bot, self.tick)

    def _step_impulse_poses(self):
        for bot in self.match.bots:
            if bot.impulse_mover is not None:
                collision = self.ice_wall.movement_world(self.match.collision_world)
                bot.step_impulse(self.tick, TICK, collision)

    def _fly_omitted_projectiles(
        self, now, tick, first, charging_players, damage_starts, cryo_expiries,
        photon_cover_ends, sphere_cover_ends, respawn_owners, allocation_clock, *, replay_routes,
    ):
        """Preserve the timing of admitted orbs and flights across omitted Deflect transitions."""
        flights = [rocket for rocket in self.rockets if rocket.last_tick is not None]
        # Symmetra's orb can outlive the defensive clock budget. Retain
        # its authored remaining flight so a distant hit keeps its tick.
        orb_ticks = max((
            math.ceil(min(rocket.left, symmetra.ORB_LIFETIME) / TICK) + 1
            for rocket in flights if rocket.definition == symmetra.ORB_PROJECTILE
        ), default=0)
        if not orb_ticks and not (
            any(rocket.deflectable() for rocket in flights)
            and any(deflection.active(player) is not None for player in self.match.players)
        ):
            return
        flight_budget = max(phase_defenses.MAX_IDLE_STEPS, orb_ticks)
        previous_now, previous_tick = self.now, self.tick
        try:
            for at in range(previous_tick + 1, min(first, previous_tick + 1 + flight_budget)):
                if not flights and not any(bot.impulse_mover is not None for bot in self.match.bots):
                    break
                self.now, self.tick = now - (tick - at) * TICK, at
                if replay_routes:
                    self._step_route_poses()
                photon_walls = tuple(self.barriers.walls)
                spheres = tuple(self.winston_barriers.actors.values())
                if self.frost_hits:
                    self._advance_frost(at * round(TICK * 1000))
                self._advance_phase_clocks(damage_starts, cryo_expiries)
                self.ice_wall.update(at)
                self._step_impulse_poses()
                self.barriers.retire_ended()
                pending = set(self.respawns)
                for rocket in flights:
                    if rocket in self.rockets:
                        self._fly(rocket)
                victims = {
                    player for when, player in self.respawns
                    if not isinstance(player, bots.Bot) and (when, player) not in pending
                }
                if victims:
                    if not hasattr(self, "early_projectile_respawns"):
                        self.early_projectile_respawns = set()
                    self.early_projectile_respawns.update(player.body for player in victims)
                    respawn_owners.update(victims)
                flights = [rocket for rocket in flights if rocket in self.rockets]
                photon_cover_ends.extend(
                    (wall, min(wall.expires_at, self.now)) for wall in photon_walls
                    if wall not in self.barriers.walls
                )
                sphere_cover_ends.extend(
                    (sphere, min(sphere.expires, self.now)) for sphere in spheres
                    if self.winston_barriers.actors.get(sphere.body) is not sphere
                )
                self._advance_symmetra_charge(charging_players, at)
                if respawn_owners:
                    self._respawn_retained_owners(
                        respawn_owners, sphere_cover_ends, damage_starts, include_sphere_owners=False,
                        allocation_clock=allocation_clock,
                    )
        finally:
            self.now, self.tick = previous_now, previous_tick

    def frozen_sentry_replay(self, tick: int) -> tuple[int, ...]:
        # Extend history only for a proven frozen lock, and retain at most
        # one freeze duration plus the usual window for movement after thaw.
        freeze_ticks = math.ceil(mei_frost.FREEZE_MS / round(TICK * 1000))
        if not COMBAT_CATCH_UP < tick - self.tick <= COMBAT_CATCH_UP + freeze_ticks or not self.frost_hits:
            return ()
        if (
            self.respawns or self.barriers.walls or self.winston_barriers.actors
            or self.zarya_barriers.active or self.blizzard.areas
            or (self.zarya_barriers.projected and not self.rockets)
            or any(cryo.active(player) for player in self.match.players)
            or any(bot.firing or bot.impulse_mover is not None for bot in self.match.bots)
        ):
            return ()
        return self.sentries.frozen_history_candidates(tick)

    def update(
        self, now: float, tick: int, frames: bool = True, *,
        replay_routes: bool = False, replay_frozen: tuple[int, ...] = (),
    ) -> None:
        """Each tick: keep where the bodies are, fly the rockets, respawn, send health that changed. The
        other players' body frames only on the ticks that send (`frames`)."""
        if self.match.collision_world is None:
            return
        from ow174.game import heavy_charge

        # Anchor historical allocations to the same checkpoint even when
        # processing several retries takes additional real time.
        allocation_clock = None
        if self.tick and tick > self.tick + 1 and (self.rockets or self.respawns):
            allocation_clock = self.match.environment.monotonic(callsite="combat.respawn_clock")
        retained = max(self.tick + 1, tick - COMBAT_CATCH_UP + 1)
        first = tick
        replay_bots = False
        replay_sphere_respawns = False
        sentry_respawns = set()
        replay_ice_sentries = False
        replay_sentry_owners = set()
        tracked = getattr(self, "early_projectile_respawns", ())
        retained_respawns = {
            player for _, player in self.respawns
            if not isinstance(player, bots.Bot) and player.body in tracked
        }
        if self.tick and tick > self.tick + 1:
            # Step in-flight attacks across cover and bonus-shield changes
            # instead of resolving the whole sweep with endpoint defenses.
            particle_cover = any(zarya_barriers.has_active_barrier(player) for player in self.match.players)
            replay_flight = any(rocket.last_tick is not None for rocket in self.rockets) and (
                self.barriers.walls or self.winston_barriers.actors or particle_cover
                or self.grasp.has_bonus_shields()
                or any(phase_defenses.has_active_projectile_cover(player) for player in self.match.players)
                or any(when < now and player in retained_respawns for when, player in self.respawns)
            )
            replay_ice_sentries = bool(self.ice_wall.pieces) and any(
                sentry.support_pillar is not None
                or (sentry.attached_at is None and sentry.bot.moved_tick is not None)
                for sentry in self.sentries.sentries
            )
            replay_bots = any(
                not bot.dead and isinstance(bot.body_script, BotScript)
                and (bot.firing or heavy_charge.phase(heavy_charge.instance(bot)))
                for bot in self.match.bots
            )
            replay_impulses = any(bot.impulse_mover is not None for bot in self.match.bots) or any(
                rocket.concussion and rocket.last_tick is not None for rocket in self.rockets
            )
            photon_owners = {
                wall.owner for wall in self.barriers.walls
                if wall.expires_at <= now or replay_flight or replay_bots
            }
            due_players = {who for when, who in self.respawns if when < now and not isinstance(who, bots.Bot)}
            sentry_respawns = {
                sentry.owner for sentry in self.sentries.sentries
                if sentry.bot.moved_tick is not None and sentry.owner in due_players
            }
            replay_sentry_owners = {
                sentry.owner for sentry in self.sentries.sentries
                if sentry.bot.moved_tick is not None
                and (particle_cover or replay_impulses or sentry_respawns or sentry.owner in photon_owners)
            }
            replay_sphere_respawns = any(
                not isinstance(who, bots.Bot)
                and (sphere := self.winston_barriers.actors.get(who.body)) is not None
                and when < min(now, sphere.expires)
                for when, who in self.respawns
            )
            if (replay_flight or replay_bots or replay_sphere_respawns
                    or replay_ice_sentries or replay_sentry_owners or replay_impulses):
                first = retained
        if replay_frozen:
            first = self.tick + 1
        # Replay the retained combat frames using the most recent poses. Player input
        # accepted just before the scheduler caught up joins only at the endpoint.
        sentry_damage_starts = {}
        cryo_expiries = {}
        photon_cover_ends = []
        sphere_cover_ends = []
        barrier_cover_ends = ((self.barriers, photon_cover_ends), (self.winston_barriers, sphere_cover_ends))
        charging_players = [
            player for player in self.match.players if player.hero.guid in symmetra_charge.AUTHORED_HEROES
        ]
        # Passive recovery can span more than the retained attack window. Run
        # it on each side of a known wall expiry while leaving combat bounded.
        idle_expiries = {}
        for wall in self.barriers.walls:
            expiry_tick = _tick_at_or_after(now, tick, wall.expires_at)
            if self.tick < expiry_tick < first:
                idle_expiries[wall.owner] = expiry_tick
        retained_respawns.update(sentry_respawns)
        if first > self.tick + 1:
            self._fly_omitted_projectiles(
                now, tick, first, charging_players, sentry_damage_starts, cryo_expiries,
                photon_cover_ends, sphere_cover_ends, retained_respawns, allocation_clock,
                replay_routes=replay_routes,
            )
            if retained_respawns:
                self._respawn_owners_before_window(
                    retained_respawns, now, tick, first, sentry_damage_starts, allocation_clock,
                )
                replay_sentry_owners.update(
                    sentry.owner for sentry in self.sentries.sentries if sentry.owner in retained_respawns
                    and sentry.bot.moved_tick is not None
                )
        for player in charging_players:
            self._advance_symmetra_charge((player,), idle_expiries.get(player, first) - 1, before_combat=True)
        for at in range(first, tick + 1):
            self.now, self.tick = now - (tick - at) * TICK, at
            if replay_routes:
                self._step_route_poses()
            photon_walls = tuple(self.barriers.walls)
            spheres = tuple(self.winston_barriers.actors.values())
            if self.frost_hits:
                self._advance_frost(at * round(TICK * 1000))
            self._advance_phase_clocks(sentry_damage_starts, cryo_expiries)
            self.ice_wall.update(at)
            self._step_impulse_poses()
            if at == tick:
                for target in self._targets():
                    history = self.history.setdefault(target.entity, deque(maxlen=HISTORY))
                    history.append((tick, target.feet, target.height, target.eye))
            # Retire Photon Barrier before impacts so its authored charge
            # suppression ends on the same world tick as its cover.
            self.barriers.retire_ended()
            if at == first and idle_expiries:
                self._advance_symmetra_charge(idle_expiries, first - 1, before_combat=True)
            for rocket in list(self.rockets):
                if at == tick or rocket.last_tick is not None:
                    self._fly(rocket)
            if at == tick or replay_bots:
                self._bot_weapons(at, accept_charge_input=at == tick)
            if at < tick:
                self.grasp.update()
            # Retain the plane when a projectile or NPC attack removes it
            # before sentries integrate damage at the end of the update.
            photon_cover_ends.extend(
                (wall, min(wall.expires_at, self.now)) for wall in photon_walls
                if wall not in self.barriers.walls
            )
            sphere_cover_ends.extend(
                (sphere, min(sphere.expires, self.now)) for sphere in spheres
                if self.winston_barriers.actors.get(sphere.body) is not sphere
            )
            if at < tick and (replay_ice_sentries or replay_sentry_owners or replay_frozen):
                self.sentries.update(sentry_damage_starts, cryo_expiries, barrier_cover_ends,
                                     replay_ice=replay_ice_sentries, replay_owners=replay_sentry_owners,
                                     replay_frozen=replay_frozen)
            if at < tick:
                self._advance_symmetra_charge(charging_players, at)
                if replay_sphere_respawns or retained_respawns:
                    self._respawn_retained_owners(
                        retained_respawns, sphere_cover_ends, sentry_damage_starts,
                        allocation_clock=allocation_clock,
                    )
        self.projectiles.update(tick, first_tick=retained)
        self.grasp.update()
        photon_cover_ends.extend((wall, wall.expires_at) for wall in self.barriers.update())
        self.reinhardt_barriers.update()
        sphere_cover_ends.extend((sphere, sphere.expires) for sphere in self.winston_barriers.update())
        self.portals.update()
        self.sentries.update(sentry_damage_starts, cryo_expiries, barrier_cover_ends)
        self.blizzard.update(tick)
        self._advance_symmetra_charge(charging_players, tick)
        self._regenerate_shields()
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
                self._respawn(who, allocation_clock=allocation_clock)
        self.voice.expire(now)
        current = {target.entity for target in self._targets()}
        current.update(wall.bot.entity for wall in self.barriers.walls)
        for entity in [key for key, deadline in self.stuns.items() if deadline <= tick]:
            del self.stuns[entity]
        for table in (self.history, self.vitals, self.replacement_ticks, self.stuns):
            for entity in [key for key in table if key not in current]:
                del table[entity]

    # --- targets -----------------------------------------------------------------------------------

    def _regenerate_shields(self) -> None:
        for target in self._targets():
            vitals = self.vitals.get(target.entity)
            if vitals is not None:
                self._recover_shields(target.owner, vitals, self.now)

    def _recover_shields(self, owner, vitals: Vitals, through: float) -> None:
        """Integrate 0033's authored shield recovery after its damage-free delay.

        The data supplies the 3 s delay, 30 DPS and their modifiers. Scheduling
        damage-free world time here reconstructs the server's missing health
        service; it does not implement the unknown B591EBFE native State.
        """
        if vitals.dead or vitals.last_damage_at is None or vitals.most[SHIELDS_POOL] <= 0:
            return
        previous = vitals.shield_accounted_at
        if previous is not None and through <= previous + EPSILON:
            return
        script = getattr(owner, "body_script", None)
        if script is None:
            return
        instance = next((i for i in script.component.instances.values()
                         if i.graph.index == pools.BODY_GRAPH), None)
        if instance is None:
            return
        wait, heal = instance.state(27), instance.state(2)
        delay = _number(wait.evaluate(wait.node.field("m_7130456B"), watch=False), 0.0)
        start = max(previous if previous is not None else through, vitals.last_damage_at + delay)
        vitals.shield_accounted_at = through
        if through <= start or vitals.now[SHIELDS_POOL] >= vitals.most[SHIELDS_POOL]:
            return
        modify = heal.node.field("m_modifyHealth")
        rate = _number(heal.evaluate(modify.get("m_amount"), watch=False), 0.0)
        given = min(max(0.0, rate) * (through - start),
                    vitals.most[SHIELDS_POOL] - vitals.now[SHIELDS_POOL])
        if given > 0:
            vitals.now[SHIELDS_POOL] += given
            vitals.changed.add(SHIELDS_POOL)
            self._sync_body_health(owner, vitals)

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
            if bot.spawner.get('photon_barrier'):
                continue  # The wall plane, not an NPC capsule, owns shield hits.
            data = bot.movement_data or mover_data(bot.body)
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

    def _enemies(self, shooter) -> list[Target]:
        free_for_all = self.match.game_map.free_for_all
        source = _source_entity(shooter)
        found = []
        for target in self._targets():
            if target.owner is shooter or target.entity == source:
                continue
            if getattr(target.owner, "combat_owner", None) is shooter:
                continue
            if not free_for_all and target.team == shooter.team:
                continue
            if (self.vitals_of(target).dead or phase_defenses.active(target.owner)
                    or cryo.active(target.owner)):
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

    def _bot_weapons(self, tick: int, *, accept_charge_input: bool = True) -> None:
        """Advance the weapon bodies of the map's firing bots and consume their volley reports."""
        from ow174.game import assassin, heavy_charge, npc_control

        for bot in self.match.bots:
            script = bot.body_script
            controlled = npc_control.manual(bot)
            if ((not bot.firing and not controlled) or bot.dead
                    or not isinstance(script, BotScript) or script.frame > tick):
                continue
            try:
                allowed = npc_control.update_input(self, bot) if controlled else not self.is_stunned(bot)
                charge = heavy_charge.instance(bot) if script.frame < tick else None
                was_charging = heavy_charge.phase(charge)
                if charge is not None:
                    heavy_charge.step(self, bot, charge, tick, accept_input=accept_charge_input)
                snapshot = script.advance_live(tick, bot.position)
                if charge is not None:
                    heavy_charge.body_impact(self, bot, charge)
                    heavy_charge.front_contact(self, bot, charge)
                    heavy_charge.side_contacts(self, bot, charge)
                if was_charging and not heavy_charge.phase(charge):
                    bot.velocity = (0.0, 0.0, 0.0)
                assassin.refresh_control(script.component.world, script.component)
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

    def _eye(self, player) -> tuple:
        x, y, z = player.mover.position
        crouched = player.mover.crouched
        height = eye_height(player.hero.body, crouched)
        return (x, y + height, z)

    def _shoot(
        self, shooter, command, state, number: int, time: int,
        *, activation: tuple[int, int], snapshot: Snapshot | None = None,
    ) -> None:
        if command is not None and self.sentries.fired(shooter, command, state):
            return
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
        if not npc and self.blizzard.fired(
                shooter, state, shot_frame, origin, direction, activation=activation, snapshot=snapshot):
            return
        direct = hit_def(state, node.field("m_6396149F"))
        speed = field("m_projectileSpeed")
        if speed:
            lifetime = field("m_projectileLifetime", 5.0) or 5.0
            reach = field("m_FEC435C6")
            if reach:
                lifetime = min(lifetime, reach / speed)
            motion = ()
            if (not npc and shooter.hero.guid == 0x02E0000000000008
                    and state.instance.graph.index == barrage_motion.GRAPH
                    and state.index == barrage_motion.STATE):
                direction, motion = barrage_motion.launch(state, activation[0], number, direction)
            blast = hit_def(state, node.field("m_D2D11CE9"))
            concussion = ()
            if (not npc and shooter.hero.guid == pharah_concussion.HERO
                    and state.instance.graph.index == pharah_concussion.GRAPH
                    and state.index == pharah_concussion.STATE):
                concussion = pharah_concussion.payload(state)
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
            if damaging or shot is not None or concussion:
                from ow174.game import enforcer

                # Pharah's 0023/state 6 launches a damaging rocket with an
                # explicitly null entity reference. Retain its scoped host
                # eligibility on the rocket across reflection and catch-up.
                implicit_absorbable = (
                    not npc and definition is None
                    and (shooter.hero.guid, state.instance.graph.index, state.index)
                    == (0x02E0000000000008, 0x0023, 6)
                )
                rings = (enforcer.directions(shooter, state, number, activation, direction)
                         if npc else NotImplemented)
                for pellet, ray in enumerate((direction,) if rings is NotImplemented else rings):
                    self.rockets.append(
                        Rocket(
                            shooter, shooter.team, origin, ray, speed, lifetime, direct, blast,
                            shot if pellet == 0 else None,
                            last_tick=self.tick if npc else None,
                            distance_falloff=rings is not NotImplemented,
                            definition=definition.guid if isinstance(definition, expr.Asset) else None,
                            target_limit=mei_frost.projectile_target_limit(state),
                            implicit_absorbable=implicit_absorbable,
                            motion=motion,
                            concussion=concussion,
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
                      world_tick=self.tick if npc else None)

    def _hitscan(self, shooter, origin, direction, reach, direct, frame, view_tick, *, world_tick):
        traveled = 0.0
        for _ in range(deflection.MAX_SEGMENTS):
            best = None
            for target in self._enemies(shooter):
                shown = target if view_tick is None else self.seen(target, view_tick)
                if shown is None:
                    continue
                a, b = shown.segment()
                distance = ray_capsule(origin, direction, a, b, shown.radius)
                if distance is not None and distance <= reach and (best is None or distance < best[0]):
                    best = (distance, target, shown)
            wall = self.cover_ray(origin, direction, reach)
            limit = min(reach, best[0] if best else reach, wall if wall is not None else reach)
            barrier = self.trace_barrier(shooter, origin, direction, limit)
            reflected = (
                deflection.trace(self, shooter, origin, direction, limit) if direct.deflectable else None
            )
            if (reflected is not None and (barrier is None or reflected[0] < barrier[0])
                    and (best is None or reflected[0] < best[0])
                    and (wall is None or reflected[0] < wall)):
                distance, reflector = reflected
                shooter = reflector.owner
                point = _along(origin, direction, distance)
                direction = aim(shooter.mover.yaw, shooter.mover.pitch)
                origin = _along(point, direction, deflection.OFFSET)
                traveled += distance + deflection.OFFSET
                reach -= distance + deflection.OFFSET
                if reach <= 0.0:
                    return
                frame = world_tick = self.tick
                view_tick = None  # the redirected segment uses the reflector's current world pose
                continue
            if barrier is not None:
                distance, shield, service = barrier
                service.damage(shield, direct.amount * direct.scale(traveled + distance))
                return
            if best is None or (wall is not None and wall < best[0]):
                return
            distance, target, shown = best
            hit_y = origin[1] + direction[1] * distance
            critical = hit_y >= shown.feet[1] + shown.eye - HEAD_BELOW
            amount = direct.amount * direct.scale(traveled + distance)
            amount *= direct.critical if critical else 1.0
            self._damage(shooter, target, amount, critical and direct.critical > 1.0, direct.loadout,
                         frame, world_tick=world_tick, effect=direct.effect)
            return

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
        if not rocket.motion:
            self._sweep_rocket(rocket, duration)
            return
        # Curved flight uses the same chords during normal and retained updates.
        for _ in range(elapsed):
            if not self._sweep_rocket(rocket, min(TICK, rocket.left)):
                break

    def _sweep_rocket(self, rocket: Rocket, duration: float) -> bool:
        """Resolve a segment and report whether the rocket remains in flight."""
        # Reflections consume the current sweep and keep the original lifetime budget.
        tagged = rocket.deflectable()
        reflections = 0
        while True:
            speed = rocket.speed
            step = speed * duration
            heading = rocket.direction
            if rocket.motion:
                lateral = barrage_motion.displacement(rocket.motion, heading, rocket.flown, duration)
                chord = _along(lateral, heading, step)
                step = math.hypot(*chord)
                heading = tuple(axis / step for axis in chord)
                speed = step / duration
            best = None
            for target in self._enemies(rocket.shooter):
                if target.entity in rocket.hit_entities:
                    continue
                a, b = target.segment()
                t = ray_capsule(rocket.position, heading, a, b, target.radius)
                if t is not None and t <= step and (best is None or t < best[0]):
                    best = (t, target)
            wall = self.cover_ray(rocket.position, heading, step)
            limit = min(step, best[0] if best else step, wall if wall is not None else step)
            barrier = self.trace_barrier(rocket.shooter, rocket.position, heading, limit)
            reflected = deflection.trace(
                self, rocket.shooter, rocket.position, heading, limit,
            ) if tagged else None
            absorbed = matrix.trace(self, rocket.shooter, rocket.definition, rocket.position,
                                    heading, limit, implicit=rocket.implicit_absorbable)
            grasp = self.grasp.trace(rocket.shooter, rocket.definition, rocket.position,
                                     heading, limit, implicit=rocket.implicit_absorbable)
            grasp_state = None
            if grasp is not None and (absorbed is None or grasp[0] < absorbed[0]):
                distance, target, grasp_state = grasp
                absorbed = distance, target
            if absorbed is not None:
                distance, _target = absorbed
                if ((barrier is None or distance < barrier[0])
                        and (reflected is None or distance < reflected[0])
                        and (best is None or distance < best[0])
                        and (wall is None or distance < wall)):
                    rocket.flown += distance / speed
                    if grasp_state is not None:
                        self.grasp.absorb(grasp_state, rocket.shooter, rocket.direct, rocket.blast,
                                          rocket.flown * rocket.speed)
                    self._landed(rocket)
                    return False
            if (reflected is not None and (barrier is None or reflected[0] < barrier[0])
                    and (best is None or reflected[0] < best[0])
                    and (wall is None or reflected[0] < wall)):
                distance, target = reflected
                consumed = distance / speed
                rocket.position = _along(rocket.position, heading, distance)
                rocket.flown += consumed
                rocket.left -= consumed
                duration -= consumed
                if rocket.shot is not None:
                    self.projectiles.flown(rocket.shot, rocket.flown)
                    rocket.shot = None
                rocket.shooter, rocket.team = target.owner, target.team
                rocket.reflected = True
                rocket.direction = aim(target.owner.mover.yaw, target.owner.mover.pitch)
                shift = min(deflection.OFFSET / rocket.speed, duration)
                rocket.position = _along(rocket.position, rocket.direction, shift * rocket.speed)
                rocket.flown += shift
                rocket.left -= shift
                duration -= shift
                if duration <= 0:
                    if rocket.left <= 0:
                        self._landed(rocket)
                        return False
                    return True
                reflections += 1
                if reflections >= deflection.MAX_SEGMENTS:
                    self._landed(rocket)
                    return False
                continue
            if barrier is not None:
                distance, shield, service = barrier
                rocket.flown += distance / speed
                self._landed(rocket)
                point = _along(rocket.position, heading, distance)
                self._explode(rocket, point, heading, surface=True, impact=(shield, service))
                return False
            if wall is not None and (best is None or wall < best[0]):
                best = (wall, None)
            if best is None:
                rocket.position = _along(rocket.position, heading, step)
                rocket.flown += duration
                rocket.left -= duration
                if rocket.left <= 0.0:
                    self._landed(rocket)
                    return False
                return True
            distance, struck = best
            spent = distance / speed
            rocket.flown += spent
            point = _along(rocket.position, heading, distance)
            particle_barrier = self.zarya_barriers.contact(struck.owner) if struck is not None else None
            pierces = (struck is not None and rocket.blast is None
                       and len(rocket.hit_entities) + 1 < rocket.target_limit
                       and particle_barrier is None)
            if not pierces:
                self._landed(rocket)
            if struck is not None:
                self._projectile_body_impact(rocket, struck, point)
            if not pierces:
                # Direct damage can break the barrier, but its recipient is
                # still protected from the same projectile's explosion.
                protected = None
                if particle_barrier is not None and particle_barrier[2].remaining <= 0:
                    protected = struck.entity
                self._explode(rocket, point, heading, surface=struck is None, protected_entity=protected)
                return False
            rocket.hit_entities.add(struck.entity)
            rocket.position = point
            rocket.left -= spent
            duration -= spent
            if duration <= 0:
                if rocket.left <= 0:
                    self._landed(rocket)
                    return False
                return True

    def _projectile_body_impact(self, rocket, struck, point):
        direct = rocket.direct
        if direct is None or direct.amount <= 0.0:
            return
        critical = (direct.loadout == MEI_ICICLE_LOADOUT and direct.critical > 1.0
                    and point[1] >= struck.feet[1] + struck.eye - HEAD_BELOW)
        amount = rocket.impact_damage() * (direct.critical if critical else 1.0)
        self._damage(
            rocket.shooter, struck, amount, critical, direct.loadout, self.tick,
            world_tick=self.tick, effect=direct.effect,
        )

    def _splash_targets(self, rocket, point, visible_from, *, targets=None):
        blast = rocket.blast
        if targets is None:
            targets = self._enemies(rocket.shooter)
        for target in targets:
            a, b = target.segment()
            distance = max(0.0, segment_distance(point, a, b) - target.radius)
            scalar = blast.splash(distance)
            if (scalar > 0.0 and self.visible_target(visible_from, target)
                    and not self.barriers.occludes(rocket.shooter, visible_from, target)):
                yield target, blast.amount * scalar

    def _splash_barriers(self, rocket, point, visible_from, impact):
        blast = rocket.blast
        reach = blast.rings[-1][0] if blast.rings else 0
        for service in (self.barriers, self.reinhardt_barriers, self.winston_barriers, self.ice_wall):
            for shield, contact in service.splash_contacts(rocket.shooter, point, reach):
                if impact is not None and shield is impact[0] and service is impact[1]:
                    continue
                scalar = blast.splash(math.dist(point, contact))
                if scalar <= 0:
                    continue
                delta = _sub(contact, visible_from)
                distance = math.sqrt(_dot(delta, delta))
                if distance > 1e-6:
                    direction = tuple(v / distance for v in delta)
                    limit = distance - 1e-6
                    blocked = self.cover_ray(visible_from, direction, limit)
                    if blocked is not None or self.trace_barrier(
                            rocket.shooter, visible_from, direction, limit) is not None:
                        continue
                yield shield, service, blast.amount * scalar

    def _explode(self, rocket, point, heading, *, surface, impact=None, protected_entity=None):
        blast = rocket.blast
        targets, barriers = (), ()
        visible_from = _along(point, heading, -0.0001) if surface else point
        if rocket.concussion:
            pharah_concussion.apply(self, rocket, point, visible_from)
        if blast is not None and blast.amount > 0:
            # Sample all exposure before any shield can break in this explosion.
            targets = tuple(self._splash_targets(rocket, point, visible_from))
            barriers = tuple(self._splash_barriers(rocket, point, visible_from, impact))
        if impact is not None:
            shield, service = impact
            splash = blast.amount * blast.splash(0) if blast is not None else 0
            service.damage(shield, rocket.impact_damage() + splash)
        for shield, service, amount in barriers:
            service.damage(shield, amount)
        for target, amount in targets:
            if target.entity == protected_entity:
                continue
            self._damage(
                rocket.shooter, target, amount, False, blast.loadout, self.tick,
                world_tick=self.tick, effect=blast.effect,
            )

    def _landed(self, rocket: Rocket) -> None:
        """The projectile ended (hit, range, lifetime): the predicted one's release follows."""
        if rocket in self.rockets:
            self.rockets.remove(rocket)
        if rocket.shot is not None:
            self.projectiles.flown(rocket.shot, rocket.flown)

    def trace_barrier(self, shooter, origin, direction, limit):
        photon = self.barriers.intersection(shooter, origin, direction, limit)
        ice = self.ice_wall.cast(origin, direction, limit)
        held = self.reinhardt_barriers.trace(shooter, origin, direction, limit)
        sphere = self.winston_barriers.trace(shooter, origin, direction, limit)
        if ice is not None:
            distance, piece, _ = ice
            if self.ice_wall.hostile(piece, shooter) and all(
                    hit is None or distance < hit[0] for hit in (photon, held, sphere)):
                return distance, piece, self.ice_wall
        if sphere is not None and (photon is None or sphere[0] < photon[0]) and (
                held is None or sphere[0] < held[0]):
            return *sphere, self.winston_barriers
        if held is not None and (photon is None or held[0] < photon[0]):
            return *held, self.reinhardt_barriers
        if photon is not None:
            return *photon, self.barriers
        return None

    def cover_ray(self, origin, direction, reach):
        """Static cover plus the current capsule proxy for an active ice block."""
        nearest = ray_world(self._collision(), origin, direction, reach)
        ice = self.ice_wall.cast(origin, direction, reach)
        if ice is not None and (nearest is None or ice[0] < nearest):
            nearest = ice[0]
        blocks = {p.body for p in self.match.players if cryo.active(p)}
        for _, distance in self._cryo_contacts(origin, direction, reach, blocks):
            if nearest is None or distance < nearest:
                nearest = distance
        return nearest

    def _cryo_contacts(self, origin, direction, reach, bodies):
        if not bodies:
            return
        for target in self._targets():
            if target.entity in bodies:
                a, b = target.segment()
                distance = ray_capsule(origin, direction, a, b, target.radius)
                if distance is not None and distance <= reach:
                    yield target.entity, distance

    def cryo_cover_expiry(self, origin, target, expiries, start):
        if not expiries:
            return start
        direction, reach = capsule_ray(origin, target)
        # Use the same capsule ray as current visibility, over the expired cover
        # at retained poses. Multiple blockers keep the ray covered until the last thaws.
        return max(
            (expiries[body] for body, distance in self._cryo_contacts(origin, direction, reach, expiries)
             if distance < reach - EPSILON),
            default=start,
        )

    def visible_target(self, origin, target):
        direction, reach = capsule_ray(origin, target)
        if reach <= EPSILON:
            return True
        wall = self.cover_ray(origin, direction, reach)
        return wall is None or wall >= reach - EPSILON

    def _heal_cryo(self, player) -> None:
        sample = cryo.healing_sample(player)
        if sample is None:
            return
        key, cumulative = sample
        previous_key, previous = self.cryo_samples.get(player.body, (None, 0.0))
        accounted = previous if key == previous_key else 0.0
        self.cryo_samples[player.body] = (key, max(accounted, cumulative))
        vitals = self.vitals.get(player.body)
        if vitals is not None and cumulative > accounted:
            given = vitals.heal(cumulative - accounted)
            if given > 0.0:
                player.stats.add(stats.HEALING, given)
                self._sync_body_health(player, vitals)

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

    def _damage(
        self, shooter, target: Target, amount: float, critical: bool, loadout, frame: int,
        *, world_tick: int | None = None, beam: bool = False, effect: int | None = None,
    ) -> bool:
        """Resolve a hit and report whether it removed points from the target's body pools."""
        vitals = self.vitals_of(target)
        if (vitals.dead or amount <= 0.0 or transcendence.invulnerable(target.owner)
                or phase_defenses.active(target.owner) or cryo.active(target.owner)):
            return False
        if shooter is not target.owner and self.zarya_barriers.intercept(
                target.owner, amount, _source_entity(shooter)):
            return False
        # Retained world hits can arrive before the endpoint's recovery pass.
        # Preserve recovery through the preceding tick, then apply this hit.
        self._recover_shields(target.owner, vitals, self.now - TICK)
        reduction = fortify.scale(target.owner)
        dealt = vitals.damage(amount if reduction is None else amount * reduction, beam=beam)
        if dealt <= 0.0:
            return False
        vitals.last_damage_at = vitals.shield_accounted_at = self.now
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
        victim = target.owner
        if killed and isinstance(victim, bots.Bot):
            # The NPC death script owns its terminal health change and death event ordering.
            self._bot_died(shooter, victim, critical, loadout, vitals.death_tick)
        else:
            self._sync_body_health(victim, vitals)
            if killed:
                self.clear_frost(victim)
                self._died(shooter, victim, critical, loadout)
        if not killed and loadout == mei_frost.SPRAY_LOADOUT:
            self.apply_mei_frost(shooter, target)
        symmetra_charge.damage(shooter, target, dealt)
        if effect is not None:
            self._hit_effect(target, effect)
        return True

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
        self.clear_frost(bot)
        bot.charge_contacts.clear()
        self.stuns.pop(bot.entity, None)
        self.clear_pins(bot.entity)
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
        self.clear_pins(victim.body)
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

    def _respawn_owners_before_window(self, owners, now, tick, first, damage_starts, allocation_clock):
        """Apply due replacements without replaying attacks from the omitted ticks."""
        previous_now, previous_tick = self.now, self.tick
        try:
            while pending := min(
                (entry for entry in self.respawns if entry[1] in owners),
                key=lambda entry: entry[0], default=None,
            ):
                when, player = pending
                at = max(previous_tick + 1, _tick_at_or_after(now, tick, when))
                if at >= first:
                    break
                self.now, self.tick = now - (tick - at) * TICK, at
                # Allocation failure schedules another deadline relative to this attempt.
                self._replace_retained_player(when, player, damage_starts, allocation_clock)
        finally:
            self.now, self.tick = previous_now, previous_tick

    def _respawn_retained_owners(
        self, owners, sphere_cover_ends, damage_starts, *, allocation_clock, include_sphere_owners=True,
    ):
        """Replace an owner after combat at the retained respawn boundary."""
        for when, player in tuple(self.respawns):
            if when > self.now or isinstance(player, bots.Bot):
                continue
            old_body = player.body
            sphere = self.winston_barriers.actors.get(old_body)
            if sphere is not None and self.now >= sphere.expires:
                sphere = None
            if player not in owners and (sphere is None or not include_sphere_owners):
                continue
            replaced = self._replace_retained_player(when, player, damage_starts, allocation_clock)
            if replaced and sphere is not None:
                sphere_cover_ends.append((sphere, min(sphere.expires, self.now + TICK)))

    def _replace_retained_player(self, when, player, damage_starts, allocation_clock):
        old_body = player.body
        self._send_health(body=old_body)
        self.respawns.remove((when, player))
        self._respawn(player, allocation_clock=allocation_clock)
        if player.body == old_body:
            return False
        # Contiguous combat runs before respawn in the same tick.
        self.replacement_ticks[player.body] = self.tick
        damage_starts[player.body] = self.now
        return True

    def _respawn(self, who, *, allocation_clock) -> None:
        if isinstance(who, bots.Bot):
            self._respawn_bot(who)
            return
        player = who
        if player.gone or not player.has_body or not self.is_dead(player):
            return
        saved_charge = None
        script = player.body_script
        if player.hero.guid in symmetra_charge.AUTHORED_HEROES and script is not None:
            saved_charge = script.component.entity_var(342).value()
        allocation_at = None
        if allocation_clock is not None:
            allocation_at = allocation_clock - max(0, self.match.tick - self.tick) * TICK
        if not self.match.switch_hero(player, player.hero, body_id_at=allocation_at):
            self.respawns.append((self.now + RESPAWN_RETRY, player))
            return
        if saved_charge is not None and player.body_script is not None:
            player.body_script.set_ult_charge(saved_charge)
            player.body_script.charge_clock = self.tick, player.body_script.component.now
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

    def _send_health(self, *, body=None) -> None:
        """Send the latest complete pool snapshot for each body that changed."""
        for entity, vitals in list(self.vitals.items()):
            if not vitals.changed or (body is not None and entity != body):
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
