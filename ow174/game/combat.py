"""Shots, damage, death and respawn: what the server decides and what it tells the clients.

What the client needs (read in the client unless marked):
- Health is component 51 (world.pool): an op-2 update of a pool's field changes the bar on every client
  that has the body. A body whose health pools (type 0) add up to 0 with a maximum above 0 is dead
  (0x7FF789ADBF00): the client then sets flag 0x2000 on its movement state (0x7FF7896A4F9A) and the 3P
  animation takes the dead pose 004.015 (0x7FF789B24889); flag 0x2000 in the records does the same.
- ClientInCombat 25001 {entity, u32 damage, u8 flags} u32 frame (104319 RVA 6C2D40) tells the shooter
  of a hit: it goes in his component 59's hit list (+0x12D0, records in frame order, not on his own
  body), which shows the target's health bar for about 5 s (RVA 6C88C1: int(1e6 / quanta) * 5 frames) and
  feeds the hit marker and its sound (event 7). The 104319 handler routes bit 0 to the critical counter at
  +0x124 instead of the ordinary counter at +0x120. Bit 1 independently increments the kill counter at +0x128.
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
  command's aim from per-body FirstPerson standing/crouched fields where verified (DATA),
  otherwise the existing fallback; camera/muzzle transforms remain unverified. Projectiles
  (m_projectileSpeed) are flown each tick.
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
from collections.abc import Callable
from dataclasses import dataclass, field, replace

from ow174.game import (
    ashe,
    orisa_fortify,
    sonic,
    dragon,
    absorption,
    barrage_motion,
    actors,
    assassin_victim,
    baptiste,
    barrier,
    deflection,
    defenses,
    earthshatter,
    graviton,
    health_packs,
    healing,
    hero_torbjorn,
    molten_core,
    mei_blizzard,
    sombra_camo,
    sombra_spread,
    sombra_charge,
    sombra_replay,
    translocators,
    torbjorn_mark,
    torbjorn_charge,
    symmetra_charge,
    symmetra_turrets,
    symmetra_portals,
    mei,
    mei_frost,
    symmetra,
    ice_wall,
    reinhardt_charge,
    barriers,
    breather,
    bots,
    contacts,
    deflections,
    echo_duplicate,
    immunity,
    interception,
    mei_wall,
    moira,
    observers,
    orisa_barrier,
    photon_barrier,
    pools,
    projectile_tags,
    projectiles,
    reinhardt_barrier,
    sigma_barrier,
    stats,
    tracer_recall,
    voicelines,
    view_offsets,
    weapon_spread,
    winston_barrier,
    world,
    world_bubbles,
)
from ow174.game.script import support, support_effects
from ow174.game import doomfist, meteor, winston, knockback, lucio, orisa, orisa_charge, reaper, sombra_control, sound_barrier, wall_ride

NPC_MELEE = {0x1422: (0x0400000000001D40, 5, 4), 0x140E: (0x0400000000001EBB, 6, 5)}
from ow174.game.bits import BitWriter
from ow174.game.brigitte import FLAIL_LOADOUT, WHIP_LOADOUT, BrigitteCombat
from ow174.game import ball, ball_mines, hook
from ow174.game.collision import MOVER, RayWorld, World
from ow174.game.commands import Command, JUMP
from ow174.game.contacts import SKIN, closest_segments
from ow174.game.content import JUNKENSTEIN_SHOCKING_SURPRISE, heroes
from ow174.game.mover import FELL, PITCH_LIMIT, PITCH_RADIANS, REPEAT, STEP, Mover, clamp_linked_target_command, mover_data, quantise, statescript_mods
from ow174.game.props import ModelPose
from ow174.game.script import entity_link, entity_queries, expr, graph, message_receive, nodes, regeneration, runtime
from ow174.game.script.bot import DIED, BotScript
from ow174.game.script.driver import BASTION, MAX_GAP, OWNER_HEALTH, ULT_CHARGE, BodyScript, Snapshot
from ow174.game.statescript import InstanceWire, StateWire, remote_full_frame
from ow174.game.world import ARMOUR_POOL, HEALTH_POOL, OP_DESTROY, POOLS, SHIELDS_POOL

log = logging.getLogger("ow174.game")

HIT = 25001  # to the shooter: a hit
DEATH_NOTICE = 25004  # to the dead player: who killed him
KILL_NOTICE = 25005  # to the killer: whom he killed
KILLED_BY = 2  # 25004 +0x78: "you were killed by" (0F7B.025); anything else "you died" (0F7C.025)
CRITICAL, KILLING = 0x01, 0x02  # 25001 hit indicator flags
DEAD = 0x2000  # movement-state flag: dead (the 3P anim's dead pose)
# 003E.025's parameters, by the low 16 bits of their 026 identifiers (01CF rid4).
KILLER, CRIT, LOADOUT, ASSISTS = 0x0012, 0x0095, 0x03E6, 0x03E7
MODE_UI = 0x0CB6  # the mode's client UI graph: the kill feed 0DEA and the notices 1FC0 (client-only)

EYE = 1.55  # m: STUFirstPersonComponent m_E29993A5, its default (OWLib) and Soldier's (DATA; unverified: eye)
# 104319 FirstPerson standing and crouched fields. Unlisted bodies use the fallback.
EYE_STANDING = {
    0x040000000000251C: 1.445,
    0x0400000000000032: 1.35,
    0x0400000000000407: 1.74, 0x040000000000040A: 1.32,
    0x0400000000000001: 1.25,
    0x0400000000001424: 1.25,
    0x0400000000001C46: 1.7,
    0x04000000000029A6: 1.35,
    0x0400000000001B1B: 1.58, 0x04000000000002A3: 1.425,
    0x0400000000002593: 1.7, 0x0400000000000225: 1.2,
    0x0400000000002AB1: 2.1, 0x04000000000003BF: 1.6,
    0x040000000000001C: 1.5,
    0x0400000000000022: 1.55,
    0x0400000000000024: 1.8,
    0x04000000000000E4: 1.45,
    0x0400000000000141: 1.35,
    0x04000000000001C0: 1.5,
    0x04000000000001FD: 1.82,
    0x0400000000000216: 1.55,
    0x040000000000028F: 1.6,
    0x0400000000000380: 1.25,
    0x04000000000003CF: 1.55,
    0x04000000000003FC: 1.175,
    0x04000000000008BA: 1.3,
    0x040000000000121C: 1.3,
    0x0400000000001498: 1.78,
    0x040000000000200B: 1.83,
    0x04000000000026F0: 1.3,
    0x04000000000028B6: 1.0,
}
EYE_CROUCHED = {
    0x040000000000251C: 1.0115,
    0x0400000000000032: .945,
    0x0400000000000407: 1.74, 0x040000000000040A: .8,
    0x0400000000000001: 0.875,
    0x0400000000001424: 0.875,
    0x0400000000001C46: 1.1,
    0x04000000000029A6: .945,
    0x04000000000003CF: 1.085, 0x0400000000001B1B: 1.2, 0x04000000000002A3: 0.9975,
    0x0400000000002593: 1.1, 0x0400000000000225: 0.84,
    0x0400000000002AB1: 1.75, 0x04000000000003BF: 1.12,
    0x040000000000001C: 1.05,
    0x0400000000000022: 1.085,
    0x0400000000000024: 1.26,
    0x04000000000000E4: 1.015,
    0x0400000000000141: 0.945,
    0x04000000000001C0: 1.05,
    0x04000000000001FD: 1.35,
    0x0400000000000216: 1.15,
    0x040000000000028F: 1.12,
    0x0400000000000380: 0.875,
    0x04000000000003FC: 0.8225,
    0x04000000000008BA: 0.91,
    0x040000000000121C: 0.91,
    0x0400000000001498: 1.246,
    0x040000000000200B: 1.5,
    0x04000000000026F0: 0.91,
    0x04000000000028B6: 0.7,
}
EYE_CROUCHED_DEFAULT = 1.15
TRACER_BODY = 0x0400000000000001
TRACER_HEAD = (-5.5390897614415735e-05, 1.5670119524002075, -0.006037640385329723)
TRACER_HEAD_RADIUS = 0.1599999964237213
HEAD_BELOW = 0.2  # m under the eye where the head starts (unverified)
SENTRY_CORE = ((1.26459091, -0.56047679), (1.25363660, -0.65987499))
SENTRY_CORE_RADIUS = 0.07999999
ECHO_TRI_SHOT_LOADOUT = 0x0B90000000000270
BEAM_ARMOR_FACTOR = 0.7  # OW1 retail patch, 2020-08-31.
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
GENJI_ASSIST_TICKS = math.ceil(5.0 / TICK)  # reconstructed damage-contribution credit window
EPSILON = 1e-9
FLASHBANG_EFFECT = 0x04A0000000000079  # .053/0079 ->0228 ->224E
HELIX_LOADOUT = 0x0B9000000000008E
ACCRETION = 0x1BBB
FLASHBANG_MODE_FLAG = 13408  # 0228 duration: flag set1.7s, otherwise0.8s
RIALTO_RETRIBUTION = 0x080000000000079F
TALON_HIT_REACTION = 0x224E
TALON_DAMAGE_REACTION = 0.48  # 224E/0 plays for the supplied duration minus 0.2 s
BIOTIC_GRENADE_EFFECT = 0x04A0000000000267  # 0B97 -> .053/0267 -> 0C71
BIOTIC_HEAL_BOOST = 1.5  # 0B99 node8, incoming healing
BIOTIC_DURATION = 4.0  # 0B99 and 0C72 state1
BIOTIC_BOOST_DELAY = 0.05  # 0B99 state8
BIOTIC_CLEANSE_VARS = (6604, 8440, 478, 33551)  # 0C72 node8
# Graph -> (authority handoff state, protected phase). The graph owns phase timing.
PROTECTED_PHASES = {
    0x0020: (1, 3),    # Wraith Form
    0x0F7C: (1, 3),    # Fade
    0x05FE: (41, 6),   # Cryo-Freeze
    0x017B: (13, 3),   # Transcendence
}
SLEEP_DART_ENTITY = 0x04000000000015E8  # 0BDD state 6
SLEEP_DART_EFFECT = 0x04A000000000026F  # .053/026F -> 0BDE
SLEEP_TICKS = math.ceil(5.0 / TICK)
SLEEP_WAKE_FLOOR_TICKS = math.ceil(1.5 / TICK)
SLEEP_WAKE_RECOVERY_TICKS = math.ceil(0.5 / TICK)
MEI_SPRAY_ENTITY = 0x04000000000008BE  # root target tag 0145, excluded by Genji 03A3 state 10
MEI_SPRAY_LOADOUT = 0x0B900000000000D3  # 05E6 state 13 -> frost effect 0169
FROST_PROGRESS = 2427  # 0B77 aggregate consumed by 05E7
FROST_INITIAL, FROST_STEP = expr.f32(0.2), expr.f32(0.0285)
FROST_GAP_TICKS, FROST_FREEZE_TICKS = math.ceil(1.0 / TICK), math.ceil(1.3 / TICK)
PHASE_STATES = {
    0x02E0000000000002: (0x0020, 5),  # Reaper Wraith, node11 writes entity 530
    0x02E00000000001A2: (0x0F7C, 4),  # Moira Fade, node10 writes entity 530
}
DAMAGE_IMMUNITY_STATES = PHASE_STATES | {
    0x02E0000000000020: (0x017B, 31),  # Transcendence, node62 writes entity 10414
    0x02E00000000000DD: (0x05FE, 35),  # Cryo-Freeze, node58 writes entity 10414
}
BEAM_STATES = {
    0x02E0000000000016: (0x157C, 100, 86),  # Symmetra
    0x02E0000000000068: (0x0241, 81, 10),  # Zarya
}
SOLDIER_WEAPON_LOADOUT = 0x0B9000000000008E
HELIX_BARRIER_PROJECTILES = frozenset((0x040000000000072C, 0x0400000000001C86))  # 0254 pos38


# --- geometry -----------------------------------------------------------------------------------------


REINHARDT = barrier.REINHARDT
HAMMER = 0x0048
HAMMER_TARGETS = {3: (1, 8), 4: (2, 9)}


def segment_pair_distance(a, b, c, d):
    return math.dist(*closest_segments(a, b, c, d))


def eye_height(body: int, crouched: bool = False) -> float:
    known = view_offsets.get(body)
    if known is not None:
        return known[1 if crouched else 0]
    return EYE_CROUCHED.get(body, EYE_CROUCHED_DEFAULT) if crouched else EYE_STANDING.get(body, EYE)


def aim(yaw: int, pitch: int) -> tuple[float, float, float]:
    """The unit vector a command looks along: forward is (sin yaw, 0, cos yaw), the pitch is positive
    looking down (commands.py)."""
    y = yaw * 2 * math.pi / 65536
    p = pitch * 2 * math.pi / 65536
    return (math.sin(y) * math.cos(p), -math.sin(p), math.cos(y) * math.cos(p))


def projectile_directions(state, direction, shot, activation):
    """Echo 19E5/st3 ring with authored radius and reconstructed seed identity."""
    if state.instance.graph.index == 0x1683:
        from ow174.game import projectile_patterns

        return projectile_patterns.directions(state, direction)
    if (state.instance.graph.index, state.index) == (0x0D0A, 2):
        return hand_cannon_directions(state, direction, state.instance.component.entity, shot, activation)
    if (state.instance.graph.index, state.index) != (0x19E5, 3):
        return (direction,)
    cfg = state.node.field("m_5EC1EB38") or {}
    rings = cfg.get("m_0D30E40C") or []
    if (cfg.get("$") != "STU_613BDDB0" or len(rings) != 1
            or cfg.get("m_E2FD6F04") is not None or cfg.get("m_1C78B91C") is not None):
        return (direction,)
    ring = rings[0]
    count = _number(state.evaluate(state.node.field("m_numProjectilesPerShot"), watch=False), 0)
    ring_count = _number(state.evaluate(ring.get("m_B19BD35D"), watch=False), 0)
    if count != 3 or ring_count != count:
        return (direction,)
    radius = _number(state.evaluate(ring.get("m_6425FFB9"), watch=False), 0.0)
    spread = _number(state.evaluate(cfg.get("m_spread"), watch=False), 0.0)
    instance = state.instance
    base = weapon_spread.seed_base(instance.component.entity, instance.id, state.node.pos, activation[0])
    return weapon_spread.single_ring_directions(direction, radius, 3, spread, base, shot)


def hand_cannon_directions(state, direction, owner, shot, activation):
    """Apply the delivered radial pattern and declared server seed binding."""
    if (state.instance.graph.index, state.index) != (0x0D0A, 2):
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
    base = weapon_spread.seed_base(owner, state.instance.id, state.node.pos, activation[0])
    return weapon_spread.radial_directions(direction, spread, jitter, rings, base, shot)


def _dot(a, b) -> float:
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def _sub(a, b):
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def _along(origin, direction, t: float):
    return (origin[0] + direction[0] * t, origin[1] + direction[1] * t, origin[2] + direction[2] * t)


def genji_fan(state, direction):
    """Apply 0304's authored fan rotation and direction order (native D808F0)."""
    cfg = state.node.field("m_5EC1EB38") or {}
    if state.instance.graph.index != 0x0304 or cfg.get("$") != "STU_1145C42E":
        return (direction,)
    count = _number(state.evaluate(state.node.field("m_numProjectilesPerShot"), watch=False), 1)
    if count != 3:
        return (direction,)
    # D5EAA0 converts the authored full angle to radians for D808F0.
    half_spread = math.radians(_number(state.evaluate(cfg.get("m_spread"), watch=False), 0.0)) / 2
    rotation = math.radians(_number(state.evaluate(cfg.get("m_A2A9890A"), watch=False), 90.0))
    x, y, z = direction
    horizontal = math.hypot(x, z)
    if horizontal > EPSILON:
        right = (z / horizontal, 0.0, -x / horizontal)
        up = (-y * x / horizontal, horizontal, -y * z / horizontal)
    else:
        right, up = (-1.0, 0.0, 0.0), (0.0, 0.0, math.copysign(1.0, y))
    tangent = tuple(u * math.cos(rotation) - r * math.sin(rotation) for u, r in zip(up, right))
    return tuple(
        tuple(d * math.cos(offset) + t * math.sin(offset) for d, t in zip(direction, tangent))
        for offset in (-half_spread, 0.0, half_spread)
    )


def _communication_screen_score(target: "Target", eye, forward, right, up) -> float:
    # C97F99 projects opposite corners of an upright player's bounds.
    lower = _sub(_along(target.feet, right, -target.radius), eye)
    upper = _sub(_along(_along(target.feet, right, target.radius), (0, 1, 0), target.height), eye)
    lower_depth, upper_depth = _dot(lower, forward), _dot(upper, forward)
    if min(lower_depth, upper_depth) < 1.1920928955078125e-6:
        return 100.0  # C98185 projection failure

    # The centered coordinates are twice the unit-viewport coordinates.
    x1, x2 = _dot(lower, right) / lower_depth, _dot(upper, right) / upper_depth
    y1, y2 = _dot(lower, up) / lower_depth, _dot(upper, up) / upper_depth
    dx = max(0.0, min(x1, x2), -max(x1, x2))
    dy = max(0.0, min(y1, y2), -max(y1, y2))
    return min(1.0, math.hypot(dx, dy) * math.sqrt(0.5))


def _communication_visible(
    rays: World | RayWorld, origin, target: "Target", category: int, pose: ModelPose | None, actor_shapes=(),
) -> bool:
    # CA408C, CA41F6 and CA4227 query the primary anchor, lower body and secondary anchor in order.
    for key, height_fraction in ((0x0DC0000000000001, None), (None, 0.3), (0x0DC00000000001CD, 0.6)):
        point = pose.hardpoint_position(key) if pose is not None and key is not None else None
        if point is None:
            if height_fraction is None:
                continue
            point = _along(target.feet, (0.0, 1.0, 0.0), target.height * height_fraction)
        delta = _sub(point, origin)
        distance = math.hypot(*delta)
        if distance <= EPSILON:
            return True
        direction = tuple(axis / distance for axis in delta)
        hit = rays.cast_ray(origin, direction, distance, category)
        nearest = hit.distance if hit is not None else distance
        owner = hit.attachment.entity if hit is not None and hit.attachment is not None else None
        for entity, matrix, geometry in actor_shapes:
            crossing = _posed_shape_distance(origin, direction, nearest, matrix, geometry)
            if crossing is not None and (crossing < nearest or (hit is None and owner is None)):
                nearest, owner = crossing, entity
        if owner == target.entity or (owner is None and hit is None):
            return True
    return False


def _posed_shape_distance(origin, direction, reach: float, matrix, geometry) -> float | None:
    """Intersect the world segment against an admitted bone-local shape."""
    x, y, z = matrix[:3], matrix[4:7], matrix[8:11]
    rows = (contacts.cross(y, z), contacts.cross(z, x), contacts.cross(x, y))
    determinant = _dot(x, rows[0])
    if not math.isfinite(determinant) or abs(determinant) <= EPSILON:
        return None
    relative = _sub(origin, matrix[12:15])
    local_origin = tuple(_dot(row, relative) / determinant for row in rows)
    local_direction = tuple(_dot(row, direction) / determinant for row in rows)
    if not all(math.isfinite(value) for value in (*local_origin, *local_direction)):
        return None
    kind = geometry[0]
    if kind == "hull":
        enter, leave = 0.0, reach
        for nx, ny, nz, plane in geometry[4]:
            normal = (nx, ny, nz)
            gap = plane - _dot(normal, local_origin)
            speed = _dot(normal, local_direction)
            if abs(speed) <= EPSILON:
                if gap < 0.0:
                    return None
                continue
            if speed < 0.0:
                enter = max(enter, gap / speed)
            else:
                leave = min(leave, gap / speed)
            if enter > leave:
                return None
        return enter
    if kind not in ("capsule", "sphere"):
        return None
    speed = math.hypot(*local_direction)
    if speed <= EPSILON:
        return None
    axis = tuple(value / speed for value in local_direction)
    a = geometry[2]
    b = geometry[3] if kind == "capsule" else a
    crossing = ray_capsule(local_origin, axis, a, b, geometry[-1])
    return crossing / speed if crossing is not None and crossing <= reach * speed else None


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


def _segment_point(point, a, b):
    """The nearest point on segment a-b."""
    ab, ap = _sub(b, a), _sub(point, a)
    length = _dot(ab, ab)
    t = 0.0 if length <= EPSILON else max(0.0, min(1.0, _dot(ap, ab) / length))
    return _along(a, ab, t)


def segment_distance(point, a, b) -> float:
    """The distance from a point to the segment a-b."""
    d = _sub(point, _segment_point(point, a, b))
    return math.sqrt(_dot(d, d))


def source_blast_geometry(feet, height: float, radius: float, impact, normal) -> tuple[float, tuple]:
    """Impulse distance and direction from D6D6C6, with an upright mover anchor."""
    x, y, z = feet
    half = height * 0.5
    center = (x, y + half, z)
    delta = _sub(center, impact)
    distance = math.hypot(*delta)
    reach = max(0.0, distance - radius)
    if distance <= EPSILON:
        return reach, (0.0, 0.0, 0.0)
    direction = tuple(axis / distance for axis in delta)
    if half * (4.0 / 3.0) <= 0.004 or radius <= 0.0:
        return reach, direction

    lower, upper = center[1] - half / 3.0, center[1] + half
    closest = (x, max(lower, min(upper, impact[1])), z)
    if math.dist(closest, impact) < radius:
        dx, dz = impact[0] - x, impact[2] - z
        length = math.hypot(dx, dz)
        escape = (dx / length, 0.0, dz / length) if length > EPSILON else normal
        impact = _along((x, impact[1], z), escape, radius)
    raised = (x, max(lower, min(upper, impact[1])) + 0.2, z)
    offset = _sub(raised, impact)
    length = math.hypot(*offset)
    if length > EPSILON:
        direction = tuple(axis / length for axis in offset)
    return reach, direction


def capsule_visible(
    collision: World | RayWorld | None, origin, a, b, radius: float, category: int = MOVER,
    *, target_entity: int | None = None, blockers=(), mesh_blocks=None, walls=None, cover_contact=None,
) -> bool:
    """Test the nearest capsule surface, allowing a hit owned by the intended target."""
    ab, ap = _sub(b, a), _sub(origin, a)
    length = _dot(ab, ab)
    share = 0.0 if length <= EPSILON else max(0.0, min(1.0, _dot(ap, ab) / length))
    toward = _sub(_along(a, ab, share), origin)
    distance = math.sqrt(_dot(toward, toward))
    if distance <= radius + EPSILON:
        return True
    reach = distance - radius
    direction = tuple(axis / distance for axis in toward)
    hit = collision.cast_ray(origin, direction, reach, category) if collision is not None else None
    if hit is not None and hit.distance + EPSILON < reach:
        if hit.attachment is None or hit.attachment.entity != target_entity:
            return False
    if walls is not None and walls.cast(origin, direction, reach) is not None:
        return False
    if cover_contact is not None and cover_contact(origin, direction, reach) is not None:
        return False
    if mesh_blocks is not None and mesh_blocks(origin, direction, reach):
        return False
    return all(
        (distance := ray_capsule(origin, direction, *target.segment(), target.radius)) is None
        or distance + EPSILON >= reach for target in blockers
    )


def ring_directions(state, direction):
    """Resource-authored radial projectile layout (613BDDB0), reconstructed phase.

    Ring counts/radii and spread come from the volley. Spread is interpreted as
    degrees and phase starts at local up; native angular phase/muzzle placement
    and multi-pellet client transport are not accepted by this reconstruction.
    """
    config = state.node.field("m_5EC1EB38") or {}
    if config.get("$") != "STU_613BDDB0":
        return None
    count = expr.to_int(state.evaluate(state.node.field("m_numProjectilesPerShot"), watch=False))
    spread = expr.to_float(state.evaluate(config.get("m_spread"), watch=False))
    if not 1 <= count <= 64 or not math.isfinite(spread) or not 0 <= spread < 90:
        raise ValueError("unsupported radial volley count/spread")
    horizontal = math.hypot(direction[0], direction[2])
    right = ((-direction[2]/horizontal, 0, direction[0]/horizontal)
             if horizontal > EPSILON else (-1, 0, 0))
    up = (right[1]*direction[2]-right[2]*direction[1],
          right[2]*direction[0]-right[0]*direction[2],
          right[0]*direction[1]-right[1]*direction[0])
    found = []
    for ring in config.get("m_0D30E40C") or ():
        radius = expr.to_float(state.evaluate(ring.get("m_6425FFB9"), watch=False))
        amount = expr.to_int(state.evaluate(ring.get("m_B19BD35D"), watch=False))
        if not math.isfinite(radius) or radius < 0 or not 1 <= amount <= 64:
            raise ValueError("unsupported radial volley ring")
        if not math.isfinite(spread*radius) or spread*radius >= 90:
            raise ValueError("unsupported radial volley angular extent")
        tangent = math.tan(math.radians(spread*radius))
        for index in range(amount):
            angle = index*2*math.pi/amount
            vector = tuple(direction[k] + tangent*(right[k]*math.sin(angle)+up[k]*math.cos(angle))
                           for k in range(3))
            length = math.sqrt(_dot(vector, vector))
            found.append(tuple(value/length for value in vector))
    if len(found) != count:
        raise ValueError("radial volley ring count does not match authored projectile count")
    return found


def ray_world(
    collision: World | RayWorld | None, origin, direction, length: float, category: int = MOVER,
) -> float | None:
    """The distance to the first static hit, for callers that only need occlusion."""
    hit = collision.cast_ray(origin, direction, length, category) if collision is not None else None
    return hit.distance if hit is not None else None


def ray_cover(collision, origin, direction, length, blockers):
    """Trace static cover plus the delivered active Cryo capsules."""
    closest = ray_world(collision, origin, direction, length)
    for a, b, radius in blockers:
        hit = ray_capsule(origin, direction, a, b, radius)
        if hit is not None and hit <= length and (closest is None or hit < closest):
            closest = hit
    return closest


# --- health -------------------------------------------------------------------------------------------


@dataclass
class WaveHealthPool:
    capacity: float
    current: float


@dataclass
class ConditionalPool:
    pool_id: int
    most: float
    now: float


@dataclass
class OverloadArmour:
    source: tuple | None = None
    amount: float = 0.0
    most: float = 0.0
    pool_id: int = 0


@dataclass
class TemporaryArmour:
    amount: float
    until: float


@dataclass
class TemporaryShield:
    amount: float
    maximum: float
    decay_from: float
    rate: float
    activation: tuple[int, int] | None = None
    pool_id: int = 0xFFFFFFFF
    grant_deadline: float | None = None
    grant_cap: float | None = None


@dataclass
class TemporaryPool:
    identifier: int
    kind: int
    slot: int
    pool_id: int
    most: float
    now: float
    last_tick: int
    removed: bool = False


class _AdaptiveShieldContext(runtime.InstanceContext):
    def __init__(self, instance, enemy_count):
        super().__init__(instance, None)
        self.enemy_count = enemy_count

    def var(self, scope, var):
        if scope == runtime.INSTANCE_SCOPE and var == 11398:
            return self.enemy_count
        return super().var(scope, var)


class Vitals:
    """A body's pools as component 51 has them: health, armour, shields (world.health's ids and fields)."""

    def __init__(self, entity: int, health: float, armour: float = 0.0, shields: float = 0.0) -> None:
        self.entity = entity
        self.most = {HEALTH_POOL: float(health), ARMOUR_POOL: float(armour), SHIELDS_POOL: float(shields)}
        self.now = dict(self.most)
        self.adaptive_activation: tuple[int, int] | None = None
        self.adaptive_until = 0
        self.ids = {}
        pool_id = 1
        for kind in (SHIELDS_POOL, ARMOUR_POOL):  # world.health numbers them so
            if self.most[kind] > 0:
                self.ids[kind] = pool_id
                pool_id += 1
        self.ids[HEALTH_POOL] = pool_id
        self.wave_health: WaveHealthPool | None = None
        self.wave_health_id = 0
        self.conditional_pools: dict[int, ConditionalPool] = {}
        self.extra: dict[tuple, TemporaryPool] = {}
        self.next_pool = 0x10000  # leave ordinary and wave pool IDs in the low range
        self.changed: set[int] = set()  # pools that changed since the last update went out
        self.died_at: float | None = None
        self.last_damage_at: float | None = None
        self.shield_accounted_at: float | None = None
        self.death_tick: int | None = None
        self.killer: int | None = None
        self.ult_healing_deficit = 0.0
        self.sleep_until = 0
        self.sleep_wake_floor = 0
        self.sleep_view = None
        self.nano_until = 0.0
        self.invulnerable_until = 0.0

        self.bonus_armour: list[TemporaryArmour] = []
        self.over_armour = OverloadArmour()
        self.bonus_declared = False
        self.temporary_shield: TemporaryShield | None = None
        self.healing_block = (0.0, 0.0)
        self.healing_boost = (0.0, 0.0)

    def healing_factor(self, now: float) -> float:
        if self.healing_block[0] <= now < self.healing_block[1]:
            return 0.0
        if self.healing_boost[0] <= now < self.healing_boost[1]:
            return BIOTIC_HEAL_BOOST
        return 1.0

    def healing_time(self, start: float, end: float) -> float:
        """Integrate incoming healing across status boundaries on this tick."""
        if end <= start:
            return 0.0
        block_start = max(start, self.healing_block[0])
        block_end = min(end, self.healing_block[1])
        boost_start = max(start, self.healing_boost[0])
        boost_end = min(end, self.healing_boost[1])
        blocked = max(0.0, block_end - block_start)
        boosted = max(0.0, boost_end - boost_start)
        overlap = max(0.0, min(block_end, boost_end) - max(block_start, boost_start))
        return end - start - blocked + (boosted - overlap) * (BIOTIC_HEAL_BOOST - 1.0)

    @property
    def temporary_armour(self) -> float:
        return sum(pool.amount for pool in self.bonus_armour)

    @property
    def maximum(self) -> float:
        shield = self.temporary_shield
        normal = self.normal_health(maximum=True) + self.temporary_armour
        return normal + (shield.maximum if shield else 0.0) + sum(pool.most for pool in self.extra.values())

    @property
    def temporary_shields(self) -> float:
        shield = self.temporary_shield
        return ((shield.amount if shield is not None else 0.0)
                + sum(pool.now for pool in self.extra.values() if pool.kind == SHIELDS_POOL))

    @property
    def temporary_cap(self) -> float:
        shield = self.temporary_shield
        return ((shield.grant_cap if shield.grant_cap is not None else shield.maximum)
                if shield is not None else 0.0)

    @property
    def temporary_decay_at(self) -> float:
        shield = self.temporary_shield
        return ((shield.grant_deadline if shield.grant_deadline is not None else shield.decay_from)
                if shield is not None else 0.0)

    @property
    def temporary_decay_rate(self) -> float:
        return self.temporary_shield.rate if self.temporary_shield is not None else 0.0

    @property
    def temporary_pool_used(self) -> bool:
        return self.temporary_shield is not None

    def _decay_clocked_shields(self, now: float) -> float:
        """Advance the delivered tagged aggregate on the authoritative clock."""
        shield = self.temporary_shield
        if shield is None or now <= shield.decay_from:
            return 0.0
        lost = min(shield.amount, (now - shield.decay_from) * shield.rate)
        shield.decay_from = now
        return self.spend_temporary_shield(lost)

    def add_temporary_shields(self, amount, cap, rate, delay, now, *, count_intrinsic=False):
        """Delivered aggregate policy: a capped gain never rearms its delay."""
        if self.dead or amount <= 0 or cap <= 0:
            return 0.0
        self.decay_temporary_shields(now)
        shield = self.temporary_shield
        old = shield.amount if shield is not None else 0.0
        occupied = old + (self.now[SHIELDS_POOL] if count_intrinsic else 0.0)
        given = min(amount, max(0.0, cap - occupied))
        if given > 0:
            pool_id = (shield.pool_id if shield is not None else
                       max((*self.ids.values(), self.wave_health_id)) + 1)
            self.temporary_shield = TemporaryShield(
                old + given, cap, now + delay, rate, pool_id=pool_id,
                grant_deadline=now + delay, grant_cap=cap,
            )
            self.changed.add(SHIELDS_POOL)
        return given

    def grant_armour(self, amount: float, cap: float, until: float) -> float:
        """Reconstructed cohorts; healing never refills them. Native pool hosting remains open."""
        give = max(0.0, min(amount, cap - self.temporary_armour))
        if give > 0 and not self.dead:
            self.bonus_armour.append(TemporaryArmour(give, until))
            self.bonus_declared = True
            self.changed.add(ARMOUR_POOL)
            return give
        return 0.0

    def expire_armour(self, now: float) -> float:
        before = self.temporary_armour
        self.bonus_armour[:] = [pool for pool in self.bonus_armour if pool.until > now]
        lost = before - self.temporary_armour
        if lost > 0:
            self.changed.add(ARMOUR_POOL)
        return lost
    def clear_sleep(self) -> None:
        self.sleep_until = self.sleep_wake_floor = 0
        self.sleep_view = None

    @property
    def dead(self) -> bool:
        return self.died_at is not None

    @property
    def total(self) -> float:
        shield = self.temporary_shield
        wave = self.wave_health.current if self.wave_health is not None else 0.0
        base = sum(self.now.values()) + sum(pool.now for pool in self.conditional_pools.values()) + wave + self.temporary_armour + self.over_armour.amount
        return base + (shield.amount if shield else 0.0) + sum(pool.now for pool in self.extra.values())

    @property
    def base_total(self) -> float:
        return sum(self.now.values()) + sum(pool.now for pool in self.conditional_pools.values())

    @property
    def base_maximum(self) -> float:
        return sum(self.most.values()) + sum(pool.most for pool in self.conditional_pools.values())

    def set_over_armour(self, source, amount: float = 0.0) -> bool:
        """Grant once per authored activation; expiry discards the unspent remainder."""
        pool = self.over_armour
        if pool.source == source:
            return False
        pool.source = source
        pool.amount = pool.most = max(0.0, amount) if source is not None else 0.0
        if source is not None and not pool.pool_id:
            pool.pool_id = self.next_pool
            self.next_pool += 1
        self.changed.add(ARMOUR_POOL)
        return True

    @property
    def tagged_shields(self):
        """Delivered tagged-shield API view onto existing authoritative extra pools."""
        return {pool.identifier: pool for pool in self.extra.values()
                if pool.kind == SHIELDS_POOL and not pool.removed}

    def grant_temporary_shields(self, tag: int, amount: float, limit: float) -> float:
        if not math.isfinite(amount) or not math.isfinite(limit) or amount < 0 or limit < 0:
            raise ValueError("temporary shields need finite nonnegative amount/cap")
        if self.dead or amount == 0:
            return 0.0
        pool = self.tagged_shields.get(tag)
        current = pool.now if pool is not None else 0.0
        given = min(amount, max(0.0, limit - current))
        if given > 0:
            if pool is None:
                key = ("adaptive", tag) if tag == moira.ADAPTIVE_SHIELD_TAG else ("tagged-shield", tag)
                pool = self.add_temporary(key, tag, SHIELDS_POOL, given, -1)
                if pool is None:
                    raise ValueError("temporary shield slots exhausted")
            else:
                pool.now += given
                pool.most = pool.now
                self.changed.add(SHIELDS_POOL)
        return given

    def decay_temporary_shields(self, tag=None, amount=None, *, now=None) -> float:
        """Retain clocked aggregate and explicit tagged-pool decay consumers."""
        if amount is None:
            if now is not None and tag is not None:
                raise TypeError("select one shield decay clock")
            return self._decay_clocked_shields(now if now is not None else tag)
        if now is not None:
            raise TypeError("tagged shield decay takes an amount, not a clock")
        if not math.isfinite(amount) or amount < 0:
            raise ValueError("temporary shield decay needs finite nonnegative amount")
        pool = self.tagged_shields.get(tag)
        if pool is None:
            return 0.0
        taken = min(amount, pool.now)
        if taken > 0:
            pool.now -= taken
            if pool.now <= EPSILON:
                pool.now = pool.most = 0.0
            self.changed.add(SHIELDS_POOL)
        return taken

    def add_temporary(self, key, identifier, kind, amount, tick):
        occupied = {pool.slot for pool in self.extra.values() if pool.kind == kind and not pool.removed}
        slot = next((slot for slot in range(2, POOLS) if slot not in occupied), None)
        if slot is None or not math.isfinite(amount) or amount <= 0:
            return None
        # Reuse a retired wire slot, while preserving monotonically unique pool IDs.
        self.extra = {key: pool for key, pool in self.extra.items()
                      if pool.kind != kind or pool.slot != slot}
        pool = TemporaryPool(identifier, kind, slot, self.next_pool, amount, amount, tick)
        self.next_pool += 1
        self.extra[key] = pool
        self.changed.add(kind)
        return pool

    def remove_temporary(self, key):
        pool = self.extra[key]
        pool.most = pool.now = 0.0
        pool.removed = True
        self.changed.add(pool.kind)  # keep a zero-valued wire tombstone until slot reuse

    def spend_temporary_shield(self, amount: float) -> float:
        shield = self.temporary_shield
        if shield is None or shield.amount <= 0.0:
            return 0.0
        taken = min(shield.amount, amount)
        if taken > 0.0:
            shield.amount -= taken
            if shield.amount <= EPSILON:
                shield.amount = shield.maximum = 0.0
            self.changed.add(SHIELDS_POOL)
        return taken

    def drain_shields(self, amount: float) -> float:
        """Consume only existing shield-kind records for the authored EMP drain."""
        left = max(0.0, amount)
        taken = self.spend_temporary_shield(left)
        left -= taken
        for pool in self.extra.values():
            if pool.kind == SHIELDS_POOL and not pool.removed and left > 0:
                used = min(left, pool.now)
                pool.now -= used
                left -= used
                taken += used
                self.changed.add(SHIELDS_POOL)
        conditional = self.conditional_pools.get(SHIELDS_POOL)
        if conditional is not None and left > 0:
            used = min(left, conditional.now)
            conditional.now -= used
            left -= used
            taken += used
            self.changed.add(SHIELDS_POOL)
        used = min(left, self.now[SHIELDS_POOL])
        if used > 0:
            self.now[SHIELDS_POOL] -= used
            taken += used
            self.changed.add(SHIELDS_POOL)
        return taken

    def add_wave_health(self, amount: float):
        """Add one state-owned health record without modifying the body's base pools."""
        if self.dead or self.wave_health is not None or not math.isfinite(amount) or amount <= 0:
            raise ValueError("invalid or overlapping wave health pool")
        pool = WaveHealthPool(amount, amount)
        self.wave_health_id = max(self.wave_health_id, self.ids[HEALTH_POOL]) + 1
        self.wave_health = pool
        self.changed.add(HEALTH_POOL)

        def remove():
            if self.wave_health is pool:
                self.wave_health = None
                self.changed.add(HEALTH_POOL)

        return remove

    def pool_current(self, kind: int) -> float:
        bonus = self.wave_health
        pool = self.conditional_pools.get(kind)
        return (self.now.get(kind, 0.0) + (pool.now if pool is not None else 0)
                + (bonus.current if kind == HEALTH_POOL and bonus is not None else 0))

    pool_total = pool_current

    def pool_maximum(self, kind):
        pool = self.conditional_pools.get(kind)
        bonus = self.wave_health
        return (self.most[kind] + (pool.most if pool is not None else 0)
                + (bonus.capacity if kind == HEALTH_POOL and bonus is not None else 0))

    def set_conditional_pool(self, kind, amount):
        if not math.isfinite(amount) or amount < 0:
            raise ValueError("conditional pool amount must be finite and nonnegative")
        pool = self.conditional_pools.get(kind)
        if pool is None:
            if amount == 0:
                return
            pool = self.conditional_pools[kind] = ConditionalPool(5 + len(self.conditional_pools), 0, 0)
        pool.most = pool.now = amount
        self.changed.add(kind)

    def restore_pool(self, kind, amount):
        amount = max(0.0, amount)
        given = min(amount, max(0.0, self.most[kind] - self.now[kind]))
        if given:
            self.now[kind] += given
        pool = self.conditional_pools.get(kind)
        if pool is not None:
            extra = min(amount - given, max(0.0, pool.most - pool.now))
            pool.now += extra
            given += extra
        if given:
            self.changed.add(kind)
        return given

    def normal_health(self, maximum: bool) -> float:
        """Read the normal component-51 records in wire order with float32 accumulation."""
        pools = self.most if maximum else self.now
        total = 0.0
        for kind in (HEALTH_POOL, ARMOUR_POOL, SHIELDS_POOL):
            if kind in self.ids:
                total = expr.f32(total + expr.f32(pools[kind]))
                if kind == HEALTH_POOL and (bonus := self.wave_health) is not None:
                    total = expr.f32(total + expr.f32(bonus.capacity if maximum else bonus.current))
            if (pool := self.conditional_pools.get(kind)) is not None:
                total = expr.f32(total + expr.f32(pool.most if maximum else pool.now))
        return total

    def capped_damage(self, amount: float, minimum_health: float = 0.0, *, armour_scalar=None, beam=False) -> float:
        """Preview the existing pool consumer without mutating live health or cohorts."""
        from copy import copy

        preview = copy(self)
        preview.now = dict(self.now)
        preview.changed = set()
        preview.extra = {key: copy(pool) for key, pool in self.extra.items()}
        preview.conditional_pools = {key: copy(pool) for key, pool in self.conditional_pools.items()}
        preview.bonus_armour = [copy(pool) for pool in self.bonus_armour]
        preview.over_armour = copy(self.over_armour)
        preview.temporary_shield = copy(self.temporary_shield)
        preview.wave_health = copy(self.wave_health)
        return preview.damage(amount, minimum_health, armour_scalar=armour_scalar, beam=beam)

    def damage(self, amount: float, minimum_health: float = 0.0, *, armour_scalar: float | None = None, beam: bool = False) -> float:
        """Take a hit from shields, then armour, then health (unverified, as OW1 did: what reaches armour
        while it has some is ARMOUR_BLOCK less, at most halved). Returns what the pools lost."""
        left = max(0.0, amount)
        armour_reduced = False
        taken = self.spend_temporary_shield(left)
        left -= taken
        # The delivered adaptive shield absorbs hits before Sound Barrier.
        pools = sorted(self.extra.values(), key=lambda pool: pool.identifier == sound_barrier.RECEIVER)
        for kind in (SHIELDS_POOL, ARMOUR_POOL, HEALTH_POOL):
            for pool in pools:
                if pool.kind == kind and pool.now > 0 and left > 0:
                    take = min(left, pool.now)
                    pool.now -= take
                    taken += take
                    left -= take
                    self.changed.add(kind)
            if kind == SHIELDS_POOL and left > 0 and self.over_armour.amount > 0:
                if not beam and armour_scalar is None:
                    left = max(left - ARMOUR_BLOCK, left / 2)
                    armour_reduced = True
                scale = BEAM_ARMOR_FACTOR if beam and armour_scalar is None else 1.0 if armour_scalar is None else armour_scalar
                take = min(left * scale, self.over_armour.amount)
                if take > 0:
                    self.over_armour.amount -= take
                    taken += take
                    left = max(0.0, left - take / scale)
                    self.changed.add(ARMOUR_POOL)
            if left <= 0.0:
                break
            if (pool := self.conditional_pools.get(kind)) is not None:
                take = min(left, pool.now)
                if take > 0:
                    pool.now -= take
                    left -= take
                    taken += take
                    self.changed.add(kind)
            if left <= 0.0:
                break
            if kind == ARMOUR_POOL and self.now[kind] + self.temporary_armour + self.over_armour.amount > 0.0:
                beam_overflow = beam
                beam_factor = BEAM_ARMOR_FACTOR if armour_scalar is None else armour_scalar
                if beam_overflow:
                    if beam_factor <= 0:
                        left = 0.0
                        continue
                    left *= beam_factor
                elif armour_scalar is None:
                    if not armour_reduced:
                        left = max(left - ARMOUR_BLOCK, left / 2)
                else:
                    left *= armour_scalar
                # Reconstruction: consume temporary cohorts oldest first;
                # apply armor reduction once across the combined armor type.
                for pool in self.bonus_armour:
                    take = min(left, pool.amount)
                    if take > 0:
                        pool.amount -= take
                        taken += take
                        left -= take
                        self.changed.add(kind)
                self.bonus_armour[:] = [pool for pool in self.bonus_armour if pool.amount > EPSILON]
                if beam_overflow:
                    take = min(left, self.now[kind])
                    if take > 0:
                        self.now[kind] -= take
                        self.changed.add(kind)
                        taken += take
                    left = max(0.0, (left - take) / beam_factor)
                    continue
            if kind == HEALTH_POOL and (bonus := self.wave_health) is not None:
                take = min(left, bonus.current)
                if take > 0:
                    bonus.current -= take
                    left -= take
                    taken += take
                    self.changed.add(kind)
            available = self.now[kind]
            if kind == HEALTH_POOL:
                available = max(0.0, available - minimum_health)
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
            left -= self.restore_pool(kind, left)
            if kind == HEALTH_POOL and (bonus := self.wave_health) is not None:
                give = min(left, bonus.capacity - bonus.current)
                if give > 0:
                    bonus.current += give
                    left -= give
                    self.changed.add(kind)
        given = max(0.0, amount) - left
        self.ult_healing_deficit = max(
            0.0, min(self.ult_healing_deficit - given, self.base_maximum - self.base_total),
        )
        return given

    def component(self, kinds) -> dict:
        """Component 51 with these pools' fields set (the others are left as they are)."""
        parts: list = [None] * (3 * POOLS)
        for kind in kinds:
            if kind in self.ids:
                parts[kind * POOLS] = world.pool(self.ids[kind], kind, self.most[kind], self.now[kind])
            if (pool := self.conditional_pools.get(kind)) is not None:
                parts[kind * POOLS + 1] = world.pool(pool.pool_id, kind, pool.most, pool.now)
            if kind == HEALTH_POOL and self.wave_health_id:
                bonus = self.wave_health
                parts[1] = (world.pool(self.wave_health_id, kind, bonus.capacity, bonus.current)
                            if bonus is not None else world.pool(0, kind, 0, 0))
        if self.bonus_declared:
            # Extra armor slot; id0 clears it on expiry. Byte18/slot allocation
            # are reconstructed, pending native temporary-pool hosting.
            amount = self.temporary_armour
            # Keep temporary pool IDs separate from authored and wave health IDs.
            part = bytearray(world.pool(0xFFFFFFFE if amount > 0 else 0,
                                        ARMOUR_POOL, amount, amount))
            part[18] = 1
            parts[ARMOUR_POOL * POOLS + 1] = bytes(part)
        over = self.over_armour
        if ARMOUR_POOL in kinds and over.pool_id:
            occupied = {pool.slot for pool in self.extra.values() if pool.kind == ARMOUR_POOL}
            if self.bonus_declared or ARMOUR_POOL in self.conditional_pools:
                occupied.add(1)
            slot = next((slot for slot in range(1, POOLS) if slot not in occupied), None)
            if slot is None:
                raise ValueError("temporary armour wire slots exhausted")
            packed = bytearray(world.pool(over.pool_id, ARMOUR_POOL, over.most, over.amount))
            packed[18] = 1
            parts[ARMOUR_POOL * POOLS + slot] = bytes(packed) if over.source is not None else bytes(20)
        if self.temporary_shield is not None:
            shield = self.temporary_shield
            pool_id = shield.pool_id
            if pool_id != 0xFFFFFFFF and shield.amount <= 0:
                pool_id = 0
            parts[SHIELDS_POOL * POOLS + 1] = world.pool(
                pool_id, SHIELDS_POOL, shield.maximum, shield.amount,
            )
        for pool in self.extra.values():
            if pool.kind in kinds:
                parts[pool.kind * POOLS + pool.slot] = world.pool(
                    pool.pool_id, pool.kind, pool.most, pool.now)
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
    deflectable: bool = False  # reconstructed admission: explicit projectile/bullet tag003BF
    effect: int | None = None  # ModifyHealth's authored hit-effect resource
    tags: tuple[int, ...] = ()
    ally_heal: float = 0.0
    duration: float = 0.0
    projectile_headshot: bool = False
    knockback: tuple[float, float, tuple] | None = None
    projectile_extent: float = 0.0  # m_57DAE1B5, used for flight against bodies and interception boxes
    deflect_override: bool | None = None  # scoped projectile root evidence for Genji
    healing: bool = False
    line_of_sight: bool = False
    health_operation: int | None = 1
    boost_credit: tuple | None = None  # Exact launch caster and outgoing multiplier.
    self_scale: float = 0.0
    horizontal_impulse: float = 0.0
    vertical_impulse: float = 0.0
    impulse_rings: tuple = ()
    vertical_impulse_minimum: float | None = None
    source_impulse_scale: float = 0.0
    source_impulse_ignores_los: bool = False

    @property
    def damage_tags(self):
        """Delivered typed health-event tags backed by the combined tag storage."""
        return tuple(expr.Asset(tag) for tag in self.tags)

    def impulse_scales(self, distance: float) -> tuple[float, float]:
        return (self._ring_scale(tuple((r, x) for r, x, y in self.impulse_rings), distance),
                self._ring_scale(tuple((r, y) for r, x, y in self.impulse_rings), distance))

    def impulse_scale(self, distance: float, *, own: bool = False) -> float:
        column = 2 if own else 1
        rings = tuple((row[0], row[column]) for row in self.impulse_rings)
        return self._ring_scale(rings, distance)

    @staticmethod
    def _ring_scale(rings, distance: float) -> float:
        if not rings:
            return 0.0
        last_radius, last_scalar = rings[0]
        if distance <= last_radius:
            return last_scalar
        for radius, scalar in rings[1:]:
            if distance <= radius:
                share = (distance - last_radius) / max(EPSILON, radius - last_radius)
                return last_scalar + (scalar - last_scalar) * share
            last_radius, last_scalar = radius, scalar
        return 0.0

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


@dataclass
class Frost:
    """Default 0B77 max-across-sources progress and exclusive freeze deadline."""

    sources: dict[int | tuple[int, int], tuple] = field(default_factory=dict)
    frozen_until: int = 0
    frozen_aim: tuple[int, int] | None = None
    sample: tuple | None = None

    def progress(self, tick: int) -> float:
        if self.sample is not None:
            sample = mei.frost_view(self.sample, tick * round(TICK * 1000))
            return sample[0] if sample is not None else 0.0
        if tick < self.frozen_until:
            return 1.0
        return max((value for value, expires in self.sources.values() if tick < expires), default=0.0)


def _number(value, default=None):
    if value is None:
        return default
    try:
        return float(expr.to_float(value))
    except (TypeError, ValueError):
        return default


def health_tags(state, modify) -> tuple[int, ...]:
    """Authored health-operation tags; no generic native filter interpretation."""
    found = []
    for row in modify.get("m_148F3152") or ():
        config = row.get("m_EE3055BD")
        if state is None:
            if not isinstance(config, dict) or config.get("$") != "STUConfigVarIdentifier":
                raise ValueError("dynamic health tags require their original State")
            tag = expr.evaluate(config, expr.Context())
        else:
            tag = state.evaluate(config, watch=False)
        if isinstance(tag, expr.Asset):
            found.append(tag.guid)
    return tuple(found)


def hit_def(state, cfg) -> HitDef | None:
    """Evaluate a volley's hit definition (STU_00D9C7E1) or explosion (STU_4AA6CA44) with the shooter's
    body script."""
    from ow174.game import mccree

    if not isinstance(cfg, dict):
        return None

    def value(name, default=None):
        field = cfg.get(name)
        return default if field is None else _number(state.evaluate(field, watch=False), default)

    modify = cfg.get("m_modifyHealth") or {}
    amount = _number(state.evaluate(modify.get("m_amount"), watch=False), 0.0) if modify else 0.0
    loadout = state.evaluate(modify.get("m_loadout"), watch=False) if modify.get("m_loadout") else None
    effect = state.evaluate(modify.get("m_277A4ECA"), watch=False) if modify.get("m_277A4ECA") else None
    tags = tuple(
        tag.guid
        for entry in modify.get("m_148F3152") or ()
        if isinstance(entry, dict)
        and isinstance(tag := state.evaluate(entry.get("m_EE3055BD"), watch=False), expr.Asset)
    )
    if state.instance.component.world.hero == reaper.HERO:
        from ow174.game import damage_charge

        tags = tuple(tag.guid for tag in damage_charge.event_tags(state, modify))
    graph_index, node_index = state.instance.graph.index, state.index
    icicle = graph_index == 0x05E6 and node_index == 16
    tracer_pistol = graph_index == 0x0016 and node_index == 8
    critical_default = 2.0 if icicle or tracer_pistol or graph_index == 0x0164 else mccree.critical_default(state)
    if graph_index == 0x1514 and isinstance(loadout, expr.Asset) and loadout.guid == 0x0B90000000000189:
        critical_default = 2.0  # Delivered Ball omitted native bullet scalar; explicit value still wins.
    if (state.instance.component.world.hero == 0x02E000000000000A
            and graph_index == 0x001B and node_index in (6, 9)):
        critical_default = value("m_66D29548", 1.0) * max(1.0, value("m_BC54C84B", 0.0))
    if graph_index == 0x0D0A and node_index == 2 and cfg.get("$") == "STU_00D9C7E1":
        critical_default = -1.0
    if isinstance(loadout, expr.Asset) and loadout.guid == ECHO_TRI_SHOT_LOADOUT:
        # 19E5 leaves the field null. Use a scoped reconstructed headshot default.
        critical_default = 2.0
    rings = []
    for ring in cfg.get("m_0D30E40C") or []:  # WeaponProjectileExplosionRing {m_radius, m_B399CACB}
        if isinstance(ring, dict):
            radius = (
                state.evaluate(ring.get("m_radius"), watch=False) if ring.get("m_radius") is not None else 0.0
            )
            scalar = ring.get("m_B399CACB")
            scalar = state.evaluate(scalar, watch=False) if scalar is not None else 0.0
            rings.append((_number(radius, 0.0), _number(scalar, 0.0)))
    impulse_rings = []
    for ring in modify.get("m_knockbackRings") or []:
        if not isinstance(ring, dict):
            continue
        values = tuple(_number(state.evaluate(ring.get(key), watch=False), 0.0)
                       for key in ("m_radius", "m_579D7CFF", "m_10399AE7"))
        impulse_rings.append(values)

    def force(name):
        cfg = modify.get(name)
        return _number(state.evaluate(cfg, watch=False), 0.0) if cfg is not None else 0.0

    return HitDef(
        amount=amount,
        loadout=loadout.guid if isinstance(loadout, expr.Asset) else None,
        falloff_start=value("m_EAD7F104"),
        falloff_end=value("m_157E5BC5"),
        falloff_floor=value("m_72A81154"),
        critical=value("m_A872C70E", 2.0 if graph_index in (0x0304, 0x0BD4) else critical_default),
        rings=tuple(sorted(rings)),
        self_scale=value("m_8A3E3883", 0.0),
        horizontal_impulse=force("m_E33EFEE1"),
        vertical_impulse=force("m_10399AE7" if state.instance.component.world.hero == 0x02E0000000000008
            else "m_583C208B" if modify.get("m_583C208B") is not None else "m_10399AE7"),
        impulse_rings=tuple(sorted(impulse_rings)),
        vertical_impulse_minimum=(force("m_583C208B") if modify.get("m_583C208B") is not None else None),
        source_impulse_scale=value("m_2FE97560", 0.0) if cfg.get("m_49F60FB8") else 0.0,
        source_impulse_ignores_los=bool(cfg.get("m_2A970629")),
        deflectable=any(
            (tag.get("m_EE3055BD") or {}).get("m_identifier") == "0x0D800000000003BF"
            for tag in modify.get("m_148F3152") or [] if isinstance(tag, dict)
        ),
        effect=effect.guid if isinstance(effect, expr.Asset) else None,
        tags=tags,
        # Reconstructed for the two authored Biotic Rifle volleys.
        ally_heal=amount if graph_index == 0x0B94 and node_index in (5, 44) else 0.0,
        duration=0.6 if graph_index == 0x0B94 and node_index in (5, 44) else 0.0,
        projectile_headshot=icicle or graph_index in (0x0304, 0x0BD4, 0x0164, 0x1683) or (graph_index, node_index) == (0x0D0A, 2),
        knockback=_accretion_knockback(state, modify),
        projectile_extent=(0.0 if graph_index in (0x0BD4, 0x16C5, 0x16DA) or (
            state.instance.component.world.hero == BASTION and (graph_index, state.node.pos) == (0x024F, 23)
        ) else value("m_57DAE1B5", 0.0)),
        healing=modify.get("m_93CDFD2F") == 0,
        health_operation=modify.get("m_93CDFD2F") if modify else None,
    )


def _accretion_knockback(state, modify) -> tuple[float, float, tuple] | None:
    if state.instance.graph.index != ACCRETION or state.node.pos != 9:
        return None
    rows = modify.get("m_knockbackRings") or ()
    if not rows:
        return None

    def value(row, key):
        return _number(state.evaluate(row.get(key), watch=False), 0.0)

    rings = tuple((value(row, "m_radius"), value(row, "m_579D7CFF"), value(row, "m_10399AE7"))
                  for row in rows)
    return value(modify, "m_E33EFEE1"), value(modify, "m_583C208B"), rings


def _accretion_launch(state, direction, speed):
    motions = state.node.field("m_projectileMotions") or ()
    bias = state.node.field("m_5EC1EB38")
    if len(motions) != 1 or motions[0].get("$") != "STU_E1A5B579" or not bias:
        return direction, None, 0.0
    gravity = _number(state.evaluate(motions[0].get("m_599AB8D7"), watch=False), 0.0)
    lift = _number(state.evaluate(bias.get("m_22CE7F30"), watch=False), 0.0)
    x, y, z = (expr.f32(axis) for axis in direction)
    y = expr.f32(y + lift)
    squared = expr.f32(expr.f32(expr.f32(x * x) + expr.f32(y * y)) + expr.f32(z * z))
    direction = (tuple(expr.f32(axis / math.sqrt(squared)) for axis in (x, y, z))
                 if squared >= 1.1754943508222875e-36 else (0.0, -1.0, 0.0))
    return direction, tuple(expr.f32(axis * speed) for axis in direction), gravity


# --- who can be hit -------------------------------------------------------------------------------------


@dataclass
class SceneHazard:
    """A damage source with a scene entity and no player or combatant body."""

    entity: int
    team: int
    name: str


def _source_entity(actor) -> int:
    """The combat actor's live entity ID."""
    return actor.entity if isinstance(actor, (bots.Bot, actors.Deployable, SceneHazard)) else actor.body


def _volley_directions(actor, state, direction, *, number=0, counter=0):
    """Resolve authored Fusion and Talon volley counts with a static muzzle pose."""
    from ow174.game import dva, npc_context

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
    head_offset: tuple | None = None
    sentry_yaw: int | None = None
    construct: object = None

    def segment(self, feet=None):
        """The capsule's segment (from the feet to the top, a radius in from both ends)."""
        x, y, z = feet or self.feet
        if self.capsule is not None:
            return tuple(tuple(a + b for a, b in zip((x, y, z), point, strict=True))
                         for point in self.capsule)
        return (x, y + self.radius, z), (x, y + max(self.radius, self.height - self.radius), z)

    def core_segment(self):
        x, y, z = self.feet
        dx, _, dz = aim(self.sentry_yaw, 0)
        return tuple((x + dx * depth, y + height, z + dz * depth) for height, depth in SENTRY_CORE)


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


@dataclass(frozen=True)
class SplitVolley:
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
    left: float  # seconds remaining
    direct: HitDef | None
    blast: HitDef | None
    shot: object = None  # its projectiles.Shot: told when it ends
    flown: float = 0.0  # seconds in the air
    last_tick: int | None = None  # last world tick, or None before player input is simulated
    spray: "moira.SprayTrace | None" = None
    dose_scalar: float | None = None
    gravity: float = 0.0  # authored projectile acceleration; zero preserves linear projectiles
    velocity: tuple | None = None  # stored velocity for accelerated flight
    detonate_on_expiry: bool = False  # declared Flashbang range/lifetime-end policy
    scale_direct: bool = False  # scoped linear pellet volleys use distance falloff
    split_on_expiry: SplitVolley | None = None
    reflected: bool = False
    definition: int | None = None
    accretion_source: tuple[int, int] | None = None
    deploy_body: int | None = None
    sticky_delay: float | None = None  # reconstructed Echo contact fuse, not a visual timer service
    fuse_left: float | None = None
    attached_entity: int | None = None
    attached_offset: tuple = (0.0, 0.0, 0.0)
    boost_credit: tuple | None = None
    damage_charge: tuple | None = None  # Captured Mercy launch body and authored health tags.
    source_body: int | None = None  # Health-event charge belongs to the emitting body.
    target_limit: int = 1
    spawn: int | None = None
    collide_bodies: bool = True
    source_graph: int | None = None
    source_state: object = None
    ended: str | None = None
    entity: int | None = None
    launch_direction: tuple | None = None
    travelled: float = 0.0
    deployment: object = None
    amplified: bool = False
    source_entity: int | None = None
    deflections: int = 0
    distance_falloff: bool = False
    penetration_radius: float = 0.0
    penetrated: set | None = None
    range_left: float | None = None
    field_bounces: int = 0
    launch_tick: int | None = None
    range_limit: float | None = None
    rebound_limit: int | None = None
    rebound_scale: float = 0.0
    hit_entities: set[int] = field(default_factory=set)
    charge_eligible: bool = True  # Tank suppression is frozen at emission, even after mode expiry.
    motion: tuple = ()
    motion_phase_offset: float = 0.0
    segment_start: float = 0.0  # Original Molten ballistic segment clock, retained through rebounds.
    deflect_lifetime: float | None = None
    matrix_absorbable: bool | None = None
    mine: object = None
    terrain_bounces: int = 0
    terrain_bounce_limit: int = 0
    terrain_bounce: tuple | None = None
    storm_ricochets: int = 0
    continuation: dragon.Profile | None = None
    started_tick: float | None = None
    sonar: sonic.Profile | None = None
    barrage_source: tuple | None = None


    @property
    def is_barrage(self):
        """Only the tagged002B payload owns the seven-field curve sampler."""
        return self.source_graph == barrage_motion.GRAPH and self.barrage_source is not None

    @property
    def distance_flown(self):
        return self.travelled

    @distance_flown.setter
    def distance_flown(self, value):
        self.travelled = value

    @property
    def distance(self):
        return self.travelled

    @distance.setter
    def distance(self, value):
        self.travelled = value

    @property
    def rebounds(self):
        return self.field_bounces

    @rebounds.setter
    def rebounds(self, value):
        self.field_bounces = value

    def set_velocity(self, vx, vy, vz):
        self.velocity = (vx, vy, vz)
        self.speed = math.hypot(vx, vy, vz)
        if self.speed:
            self.direction = (vx / self.speed, vy / self.speed, vz / self.speed)

    def travel(self, distance, *, seconds=None):
        if seconds is None:
            seconds = distance / self.speed
        self.travelled += distance
        self.flown += seconds
        self.left -= seconds
        return seconds

    def stamp_launch(self, tick):
        self.launch_tick = tick
        if self.shot is not None and self.shot.spawn == projectiles.FIELD_ENTITY:
            self.shot.field_due = tick + 1 + projectiles.FIELD_DELAY

    def flight_cursor(self):
        return self.launch_tick if self.last_tick is None else self.last_tick

    @property
    def radius(self):
        return self.collision_radius

    @property
    def max_distance(self):
        return self.range_limit

    @max_distance.setter
    def max_distance(self, value):
        self.range_limit = value

    @property
    def explode_on_expiry(self):
        return self.detonate_on_expiry

    @explode_on_expiry.setter
    def explode_on_expiry(self, value):
        self.detonate_on_expiry = value

    @property
    def collision_radius(self):
        return self.direct.projectile_extent if self.direct is not None else 0.0

    def __post_init__(self):
        if self.source_entity is None:
            self.source_entity = getattr(self.shooter, "entity", None) if isinstance(
                self.shooter, (bots.Bot, actors.Deployable)
            ) else getattr(self.shooter, "body", None)


def _active_fortify(owner, *, tick=None):
    """Delivered Fortify-state query over the combined defensive consumer."""
    script = getattr(owner, "body_script", None)
    if (tick is not None and getattr(script, "orisa_fortify_pending", False)
            and tick < owner.match.tick):
        return None
    return defenses.fortify_state(owner)


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
                yaw = self.target.owner.yaw if isinstance(self.target.owner, bots.Bot) else self.target.owner.mover.yaw
            else:
                return super().native(cfg)
            return expr.Vec3(*deflection.basis(yaw, 0)[2])
        if (kind == "STU_AC829876" and self.target is not None
                and (cfg.get("m_filter") or {}).get("m_84065FD1") == 1):
            # This context is created only for an admitted hostile body candidate.
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

@dataclass
class ProjectedBarrier:
    source: int
    until: float
    health: float


@dataclass(frozen=True)
class PunchImpact:
    source: object
    source_body: int
    direction: tuple[float, float, float]
    damage: float
    deadline: int


@dataclass
class ChargeContact:
    hit: set[int] = field(default_factory=set)
    captured: int | None = None


@dataclass
class TimedHit:
    """Ana B95's dose, owned by the original hit body and applied on the world clock."""

    shooter: object
    source_entity: int
    target: int
    team: int
    healing: bool
    amount: float
    started: float
    duration: float
    loadout: int | None
    frame: int
    tags: tuple[int, ...]
    applied: float = 0.0


@dataclass(frozen=True)
class RangedContact:
    hit: HitDef
    origin: tuple
    direction: tuple
    reach: float
    view_tick: float
    charge_context: tuple | None
    definition: int | None
    state: object
    world_tick: int | None = None
    reaper_shell: dict | None = None
    coach_rays: tuple | None = None

@dataclass(frozen=True)
class MeleeContact:
    hit: HitDef
    origin: tuple
    centre: tuple
    radius: float
    view_tick: float
    charge_context: tuple | None
    tags: tuple
    state: object
    ring: object
    hit_reaction: bool
    direction: tuple

@dataclass(frozen=True)
class PendingAttack:
    shooter: object
    source: BodyScript
    frame: int
    deadline: int
    waiting: tuple[BodyScript, ...]
    bonuses: tuple[tuple[BodyScript, float], ...]
    payload: RangedContact | MeleeContact | Rocket


class SelfDestructDistance(runtime.InstanceContext):
    """Evaluate the authored blast curve for a surface outside the body array."""

    def __init__(self, instance, distance):
        super().__init__(instance, None)
        self.distance = expr.f32(distance)

    def var(self, scope, var):
        if (scope, var) == (expr.INSTANCE, 680):
            return self.distance
        return super().var(scope, var)


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

@dataclass
class InputLock:
    effect: int | str
    start: int
    end: int | None
    aim: tuple[int, int] | None = None


class Combat:
    """The match's health, hits, deaths and respawns."""

    def expose_body(self, body: int, tick: int) -> None:
        """Start a targetable interval, separate from the rolling pose history."""
        self.history.pop(body, None)
        self.input_locks.pop(body, None)
        self.replacement_ticks[body] = tick

    def movement_mods(self, actor, active: dict, *, tick: int | None = None) -> dict:
        frost = self.frost_hits.get(_source_entity(actor))
        at = (self.tick if tick is None else tick)
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

    def suppress_input(self, actor, command: Command, *, tick: int, active: bool) -> tuple[Command, bool]:
        """Return admitted input and whether combat reports are fully suppressed."""
        from ow174.game.script.driver import MAX_GAP

        entity = _source_entity(actor)
        movement_locked = entity in self.zarya_gravitons.pulled
        aim = None
        if active:
            frost = self.frost_hits.get(entity)
            if frost is not None and tick < frost.frozen_until:
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

    def controlled_mech_command(self, bot, command, snapshot, stunned, quanta) -> None:
        script = bot.body_script
        shots, script.component.shots[:] = list(script.component.shots), []
        if not stunned:
            for state, number, time, activation, *launch in shots:
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
                    hit_tags=health_tags(modify, modify.node.field("m_modifyHealth")),
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

            self.clear_pins(target.entity)
        self._radial_impulse(target, mover.position, horizontal, vertical)

    def __init__(self, match) -> None:
        self.match = match
        self.defender_damage_scale = 1.0
        self.reconstructed_volley_patterns = False
        self.model_pose_for: Callable[[int], ModelPose | None] = self._wheel_model_pose
        self.wheel_actor_admits: Callable[[int, int, int, int, int], bool] | None = self._wheel_base_actor_admits
        self.vitals: dict[int, Vitals] = {}  # body or bot entity -> its health
        self.history: dict[int, deque] = {}  # entity -> (tick, feet, height, eye) of the last HISTORY ticks
        self.input_locks = {}
        self.booster_contacts = {}
        self.hammer_hits = {}
        self.replacement_ticks: dict[int, int] = {}  # replacement body -> authoritative world tick
        self.rockets: list[Rocket] = []
        self.matrix_definitions: frozenset[int] | None = None
        self.dragon_profile: dragon.Profile | None = None
        self.dragons: list[dragon.Flight] = []
        self.dragon_pose_anchors: dict[int, tuple] = {}
        self.sonic_profile: sonic.Profile | None = None
        self.sonars: list[sonic.Beacon] = []
        self.pending_attacks: list[PendingAttack] = []
        self.timed_hits: list[TimedHit] = []
        self.recalls = tracer_recall.Recalls(self)
        from ow174.game.pulse_bomb import PulseBombs

        self.pulse_bombs = PulseBombs(self)
        from ow174.game import venom

        self.venom = venom.Mines(self)
        from ow174.game import junkrat

        self.hooks = hook.Hooks(self)
        self.ball_mines = ball_mines.Mines(self)
        self.junkrat = junkrat.Junkrat(self)
        from ow174.game import ashe

        self.dynamite = ashe.Dynamite(self)
        self.impulses = ashe.Impulses(self)
        self.companions = ashe.Companions(self)
        from ow174.game.zenyatta import Zenyatta

        self.zenyatta = Zenyatta(self)
        self.breathers = breather.Breathers(self)
        self.sombra_controls = sombra_control.Controls(self)
        self.sound_barriers = sound_barrier.SoundBarriers(self)
        self.lucio_auras = lucio.Auras(self)
        self.knockbacks = knockback.Knockbacks(self)
        self.soundwaves = []
        self.support_effects = support_effects.Effects(match)
        self.health_packs, self.health_pack_digest = health_packs.mount(match, match.tick)
        # Bounded flight results for ability impact/deployable consumers.
        self.ability_impacts: deque[Rocket] = deque(maxlen=64)
        self.shatters: dict[int, earthshatter.Wave] = {}
        self.charges: dict[int, reinhardt_charge.Charge] = {}
        self.ball_grapples = {}
        self.grapple_seen = {}
        self.ball_contacts = {}
        self.pile_air_locks = {}
        self.moira_orbs: list[moira.Orb] = []
        self.moira_clocks = {}
        self.moira_hots: dict[int, moira.SprayHeal] = {}
        self.photon_barriers = photon_barrier.Barriers(self)
        self.zarya_gravitons = graviton.Gravitons(self)
        self.respawns: list[tuple[float, object]] = []  # (when, a player or a bot)
        self.deadeye_notices = []  # (killer, victim, script, root, shot frame, deadline)
        self.next_bot = bots.BOT_ENTITY + len(match.bots)
        self.next_barrier = barriers.FIRST_ENTITY
        self.now = 0.0
        self.tick = 0
        self.molten_pools = {}
        self.molten_burns = {}
        self.torbjorn_marks = {}
        self.hammer_contacts = {}
        self.turrets = {}  # Owner controller -> original actor-backed turret.
        self.sentries = symmetra_turrets.Turrets(self)
        self.portals = symmetra_portals.Portals(self)
        self.blizzard = mei_blizzard.Blizzard(self)
        self.melee_seen: dict[int, dict[tuple[int, int], int]] = {}  # body -> counters by instance/state
        self.tesla_frames: dict[int, int] = {}
        self.genji_strikes: dict[int, tuple] = {}  # body -> (instance, activation, previous centre, victims)
        self.genji_blades: dict[int, dict] = {}  # body -> (instance, tracker) -> (activation, victims)
        self.genji_assists: dict[int, dict] = {}  # victim -> source body -> (Genji player, last damage tick)
        self.stuns: dict[int, int] = {}  # body identity -> exclusive world-tick deadline
        self.controls: dict[tuple[int, int, str], float] = {}  # original independent source clocks
        self.npc_hit_reactions: dict[int, tuple[int, runtime.Instance]] = {}
        self.pins: dict[int, int] = {}  # manual Assassin source body -> controlled victim body
        self.punch_impacts: dict[int, PunchImpact] = {}
        self.punches = doomfist.Punches(self)
        self.slams = doomfist.Slams(self)
        self.meteors = meteor.Meteors(self)
        self.primal_bonus: dict[int, float] = {}
        self.winston_jump = winston.JumpPack(self)
        self.primal_punches = winston.PrimalPunches(self)
        self.charge_contacts: dict[int, ChargeContact] = {}
        self.personal_barriers: dict[int, tuple[int, int, float]] = {}  # body -> instance, Pet activation, HP
        self.projected_barriers: dict[int, ProjectedBarrier] = {}
        self.sigma_barriers: dict[int, sigma_barrier.Barrier] = {}
        self.reinhardt_geometry = reinhardt_barrier.configured_geometry()
        self.reinhardt_barriers: dict[int, reinhardt_barrier.Barrier] = {}
        self.walls = mei_wall.Walls(self)
        self.winston_domes: dict[int, winston_barrier.Dome] = {}
        self.projected_casts: dict[tuple[int, int], int] = {}
        self.brigitte = BrigitteCombat(self)
        self.frost_hits: dict[int, Frost] = {}
        self.frost_sources: dict[int, dict[tuple[int, int], tuple]] = {}
        self.cryo_samples: dict[int, mei.CryoHealing] = {}
        self.assassin_victims: dict[int, assassin_victim.Lease] = {}
        self.projectiles = projectiles.Projectiles(self)  # leases, confirmations, the Biotic Field
        self.echo_beam_seen: dict[int, int] = {}
        self.reaper_blossoms: dict[int, dict] = {}
        self.reaper_shadows: dict[int, dict] = {}
        self.echo_duplicates = echo_duplicate.Duplicates(self)
        self.barriers = barriers.Barriers(self)
        self.observers = observers.Observers(match)  # the bodies' statescript for the other clients
        self.voice = voicelines.VoiceLines(match)  # the hero's spawn voice line (voicelines.py)
        self.orisa = orisa.Orisa(self)
        self.orisa_barriers = self.orisa.barriers
        self.baptiste = baptiste.BaptisteEffects(self)
        self.healing = healing.HealingEffects(self)
        self.projectile_tags = deflection.catalog()
        self.absorption = absorption.Grasp(self)
        self.barrier = barrier.BarrierField(self)
        self.deployables: dict[int, actors.Deployable] = {}
        self.translocators = translocators.Translocators(self)
        self.sombra_charge = sombra_charge.Charge(self)

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
        if entity in self.translocators.entries:
            self.translocators.destroy_at(entity, self.tick)
            return
        construct = self.junkrat.lookup(entity)
        if construct is not None:
            self.junkrat.retire(construct)
            return
        actor = self.deployables.pop(entity, None)
        if actor is None:
            return
        self.sombra_controls.retire(entity)
        self.baptiste.deployable_removed(entity)
        ice_wall.retired(actor)
        self.vitals.pop(entity, None)
        self.stuns.pop(entity, None)
        self._retire_source_controls(entity)
        self.history.pop(entity, None)
        self.replacement_ticks.pop(entity, None)
        for viewer in self.match.players:
            if viewer.spawned and viewer.client is not None:
                viewer.client.queue_entities([world.EntityUpdate(entity, world.OP_DESTROY)])

    # --- hooks (match.py) --------------------------------------------------------------------------

    def is_dead(self, player) -> bool:
        if isinstance(player, bots.Bot):
            vitals = self.vitals.get(player.entity)
            return player.dead or (vitals is not None and vitals.dead)
        vitals = self.vitals.get(player.body) if player.has_body else None
        return vitals is not None and vitals.dead

    def resurrect_player(self, caster, entity, instance):
        if (caster.hero.guid != regeneration.MERCY or self.is_dead(caster)
                or self.match.game_map.free_for_all or caster.body == entity
                or caster.body_script is None or caster.body_script.component is not instance.component):
            return False
        target = next((target for target in self._targets() if target.entity == entity), None)
        if (target is None or target.team != caster.team or target.owner not in self.match.players
                or not self.vitals_of(target).dead):
            return False
        helper = graph.graph(0x00CB)
        if helper is None:
            return False
        immune_seconds = _number(instance.evaluate(helper.node(1).field("m_timeout")), 0.0)
        control_seconds = _number(instance.evaluate(helper.node(3).field("m_timeout")), 0.0)
        vitals = self.vitals_of(target)
        for kind, maximum in vitals.most.items():
            vitals.now[kind] = maximum
            vitals.changed.add(kind)
        vitals.died_at = vitals.death_tick = vitals.killer = None
        vitals.invulnerable_until = self.now + immune_seconds
        vitals.clear_sleep()
        vitals.ult_healing_deficit = 0.0
        self.sombra_controls.retire(entity)
        self.knockbacks.retire(entity)
        self.support_effects.retire(entity)
        self.healing.retire(entity)
        self.respawns[:] = [(when, owner) for when, owner in self.respawns if owner is not target.owner]
        self.record_replacement(entity)
        self.stuns[entity] = self.tick + math.ceil(control_seconds / TICK)
        self._sync_body_health(target.owner, vitals)
        component = getattr(getattr(target.owner, "body_script", None), "component", None)
        if component is not None:
            component.set(478, False)
            component.world.notify_entity_filter_change(entity)
        subject = graph.graph(0x01C7)
        if subject is not None:
            for statistic in subject.node(29).field("m_stats") or ():
                caster.stats.add(expr.guid_of(statistic), 1.0)
        return True

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

    @property
    def mines(self):
        return self.ball_mines

    def retire_player_body(self, player, *, next_hero=None) -> None:
        """Discard body-local combat state when the player's current body retires."""
        from ow174.game import body_entities

        ball.cancel_grapple(self, player)
        self.grapple_seen.pop(player.body, None)
        self.ball_contacts.pop(player.body, None)
        self.pile_air_locks.pop(player.body, None)
        body_entities.retire_source(self.match, player.entity, player.body)
        if player.hero.guid == symmetra_turrets.HERO and next_hero != symmetra_turrets.HERO:
            self.sentries.retire_owner(player)
            self.portals.retire_owner(player)
        if player.hero.guid == sombra_charge.SOMBRA:
            if next_hero is None and player.gone:
                self.sombra_charge.prepare_retirement(player, False)
            self.sombra_charge.retire_body(player.body)
        self.translocators.retire_body(player.body)
        self.hooks.retire(player.body)
        self.ball_mines.retire(player.body)
        self.blizzard.retire(player.body)
        self.cryo_samples.pop(player.body, None)
        self.sentries.release_target(player.body)
        player.mover.retire_impulses()
        player.clear_valkyrie_pose()
        self.pending_attacks[:] = [attack for attack in self.pending_attacks if attack.shooter is not player]
        self.projectiles.retire_fields(player)
        self.timed_hits[:] = [hit for hit in self.timed_hits if hit.target != player.body]
        self.recalls.retire(player.body)
        self.input_locks.pop(player.body, None)
        self.booster_contacts.pop(player.body, None)
        self.reaper_blossoms.pop(player.body, None)
        self.reaper_shadows.pop(player.body, None)
        self.punches.retire(player.body)
        self.slams.casts.pop(player.body, None)
        self.meteors.casts.pop(player.body, None)
        self.primal_bonus.pop(player.body, None)
        self.winston_jump.retire(player.body)
        self.primal_punches.retire(player.body)
        self.zenyatta.retire(player.body)
        self.breathers.retire(player.body)
        self.orisa.retire(player.body)
        self.echo_duplicates.retired(player)
        self.barriers.retire(player.body)
        sigma_barrier.retire_owner(self, player)
        barriers.retire_owner(self, player)
        assassin_victim.retire_target(self, player.body)
        self.clear_pins(player.body)
        self.vitals.pop(player.body, None)
        self.history.pop(player.body, None)
        self.replacement_ticks.pop(player.body, None)
        if player.hero.guid == hero_torbjorn.HERO and next_hero not in (None, hero_torbjorn.HERO):
            molten_core.retire_owner(self, player)
            hero_torbjorn.destroy_turret(self, player)
            for key, mark in tuple(self.torbjorn_marks.items()):
                if mark.owner is player:
                    torbjorn_mark.remove(self, key)
        self.hammer_contacts.pop(player.body, None)
        self.melee_seen.pop(player.body, None)
        self.hammer_hits.pop(player.body, None)
        self.tesla_frames.pop(player.body, None)
        self.stuns.pop(player.body, None)
        self._retire_source_controls(player.body)
        for entity, impact in tuple(self.punch_impacts.items()):
            if player.body in (entity, impact.source_body):
                del self.punch_impacts[entity]
        self.personal_barriers.pop(player.body, None)
        self.reinhardt_barriers.pop(player.body, None)
        self.projected_barriers.pop(player.body, None)
        for entity, barrier in list(self.sigma_barriers.items()):
            if barrier.owner_body == player.body:
                del self.sigma_barriers[entity]
        self.clear_pins(player.body)
        self.brigitte.retire(player.body)
        self.walls.retire(player.body)
        self.venom.retire(player.body)
        self.junkrat.retire_body(player.body)
        self.dynamite.retire(player.body)
        self.companions.retire(player.body)
        self.deadeye_notices[:] = [entry for entry in self.deadeye_notices if entry[0] is not player]
        self.echo_beam_seen.pop(player.body, None)
        self.shatters.pop(player.body, None)
        self.charges.pop(player.body, None)
        for charge in self.charges.values():
            if charge.pinned == player.body:
                charge.pinned = None
        self.sombra_controls.retire(player.body)
        self.sound_barriers.retire(player.body)
        self.support_effects.retire(player.body)
        self.lucio_auras.retire(player.body)
        self.knockbacks.retire(player.body)
        self.soundwaves[:] = [wave for wave in self.soundwaves if wave.body != player.body]
        self.baptiste.retire_body(player.body)
        self.healing.retire(player.body)
        self.absorption.retire(player.body)
        self.barrier.retire(player.body)
        # The owner's leased projectiles and their observer copies retire with the old body.
        # Preserve an already attached sticky's authoritative fuse independently of its lease.
        for rocket in self.rockets:
            if rocket.shooter is player and rocket.fuse_left is not None:
                rocket.shot = None
        self.rockets[:] = [
            rocket for rocket in self.rockets
            if rocket.shooter is not player
            or (rocket.reflected and rocket.shot is None and not getattr(rocket, "pulse_bomb", False))
            or (rocket.shot is None and not rocket.reflected and rocket.accretion_source is None
                and rocket.deploy_body is None and not getattr(rocket, "pulse_bomb", False)
                and not (rocket.source_graph == 0x024F and not rocket.charge_eligible))
        ]
        self.frost_hits.pop(player.body, None)
        self.frost_sources.pop(player.body, None)
        self.genji_strikes.pop(player.body, None)
        self.genji_blades.pop(player.body, None)
        self.genji_assists.pop(player.body, None)
        for victim, sources in list(self.genji_assists.items()):
            sources.pop(player.body, None)
            if not sources:
                del self.genji_assists[victim]
        if player.hero.guid in (0x02E0000000000029, 0x02E000000000007A):
            # Retain foreign reflected rounds and Genji's delivered0304 teardown.
            self.rockets[:] = [
                rocket for rocket in self.rockets
                if rocket.shooter is not player or (
                    rocket.reflected and rocket.source_graph is not None
                    and not (player.hero.guid == 0x02E0000000000029 and rocket.source_graph == 0x0304)
                )
            ]
        self.respawns[:] = [(when, who) for when, who in self.respawns if who is not player]

    def record_replacement(self, body: int) -> None:
        """Keep the creation boundary independent of the rolling pose history."""
        self.replacement_ticks[body] = self.match.tick
        self.input_locks.pop(body, None)
        self.stuns.pop(body, None)
        self._retire_source_controls(body)
        self.frost_hits.pop(body, None)
        self.frost_sources.pop(body, None)
        self.clear_pins(body)

    def cancel_npc_projectiles(self, bot: bots.Bot) -> None:
        self.rockets[:] = [rocket for rocket in self.rockets if rocket.shooter is not bot]
        self.projectiles.cancel_npc(bot, self.match.tick)

    def source_control_deadline(self, entity):
        return max((self.tick + math.ceil((end - self.now) / TICK - 1e-9)
                    for (victim, _, _), end in self.controls.items() if victim == entity), default=0)

    def _retire_source_controls(self, entity):
        for key in tuple(self.controls):
            if key[0] == entity:
                del self.controls[key]

    def control(self, target, source, kind, seconds):
        """Original independently expiring hard-control sources on existing consumers."""
        if self.vitals_of(target).dead or seconds <= 0 or self._control_immune(target):
            return
        self.controls[target.entity, source, kind] = self.now + seconds
        self.interrupt(target)

    def is_stunned(self, actor, *, tick: int | None = None) -> bool:
        body = _source_entity(actor)
        return ((isinstance(actor, bots.Bot) and self.punches.controlled(body))
                or body in self.pins.values() or reinhardt_charge.pinned(self, body)
                or any(c.captured == body for c in self.charge_contacts.values())
                or self.stuns.get(body, 0) > (self.tick if tick is None else tick)
                or self.source_control_deadline(body) > (self.tick if tick is None else tick)
                or self.hooks.controlled(body)
                or self.brigitte.controlled(body) or assassin_victim.controlled(self, body)
                or self.is_asleep(actor, tick=tick)
                or self.frost_progress(actor, tick=self.tick if tick is None else tick) >= 1.0)

    def is_asleep(self, actor, *, tick: int | None = None) -> bool:
        vitals = self.vitals.get(_source_entity(actor))
        return vitals is not None and (self.tick if tick is None else tick) < vitals.sleep_until

    def _update_sleep_view(self, player):
        vitals = self.vitals.get(player.body)
        if vitals is None or not self.is_asleep(player) or vitals.sleep_view is not None:
            return
        definition = graph.graph(0x224E)
        if definition is None or player.body_script is None:
            return
        # Wake recovery can shorten sleep_until; the stored floor retains the
        # actual hit tick. Wait29 still schedules this same authored view branch.
        start = vitals.sleep_wake_floor - SLEEP_WAKE_FLOOR_TICKS
        delay = math.ceil(definition.node(29).field("m_timeout.m_value") / TICK)
        if self.tick < start + delay:
            return
        vitals.sleep_view = player.body_script.world.sample_sleep_view()

    def clamp_sleep_command(self, player, command):
        vitals = self.vitals.get(player.body)
        if vitals is None or vitals.sleep_view is None or not self.is_asleep(player):
            return command
        return clamp_linked_target_command(command, vitals.sleep_view)

    def movement_pause_until(self, actor):
        entity = _source_entity(actor)
        vitals = self.vitals.get(entity)
        sleep = vitals.sleep_until if vitals is not None else 0
        pull = self.match.tick + 1 if entity in self.zarya_gravitons.pulled else 0
        frost = self.frost_hits.get(entity)
        frozen_until = frost.frozen_until if frost is not None else 0
        root = max(self.junkrat.roots.get(entity, {}).values(), default=0)
        return max(sleep, pull, root, frozen_until, self.stuns.get(entity, 0), self.source_control_deadline(entity))

    def damage_immune(self, owner):
        vitals = self.vitals.get(_source_entity(owner))
        return ((vitals is not None and self.now < vitals.invulnerable_until)
                or self._invulnerable(owner) or moira.fading(owner) or self.recalls.active(owner)
                or immunity.wraith_active(owner, _source_entity(owner)))

    def frost_progress(self, actor, *, tick: int) -> float:
        frost = self.frost_hits.get(_source_entity(actor))
        return frost.progress(tick) if frost is not None else 0.0

    @staticmethod
    def _write_frost(component, progress: float) -> None:
        if component.entity_var(FROST_PROGRESS).value() != progress:
            component.set(FROST_PROGRESS, progress)

    def _frost_immune(self, target: Target) -> bool:
        component = target.owner.body_script.component
        return self._has_protection(target, DAMAGE_IMMUNITY_STATES) or any(
            (variable := component.entity_var(key, create=False)) is not None and expr.truthy(variable.value())
            for key in (6604, 8440, 9161)
        )

    def frost(self, actor, *, tick=None):
        at = self.match.tick if tick is None else tick
        frost = self.frost_hits.get(_source_entity(actor))
        if frost is None:
            return None
        if frost.sample is not None:
            return mei.frost_view(frost.sample, at * round(TICK * 1000))
        progress = frost.progress(at)
        if not progress:
            return None
        yaw, pitch = frost.frozen_aim or (0, 0)
        return progress, at * round(TICK * 1000), frost.frozen_until * round(TICK * 1000), yaw, pitch

    def frozen(self, actor, *, tick=None) -> bool:
        sample = self.frost(actor, tick=tick)
        return sample is not None and bool(sample[2])

    def _clear_frost(self, actor):
        entity = _source_entity(actor)
        self.frost_sources.pop(entity, None)
        if self.frost_hits.pop(entity, None) is not None:
            script = getattr(actor, "body_script", None)
            if script is not None:
                self._write_frost(script.component, 0.0)

    def _set_frost_sample(self, actor, frost, sample):
        frost.sample = sample
        frost.frozen_until = math.ceil(sample[2] / round(TICK * 1000)) if sample[2] else 0
        frost.frozen_aim = sample[3:] if sample[2] else None
        self._write_frost(actor.body_script.component, sample[0])

    def _frost_hit(self, target, *, source=None, channel=0, now_ms=None,
                   initial=mei.FROST_INITIAL, step=mei.FROST_STEP):
        actor = target.owner
        if (isinstance(actor, actors.Deployable) or getattr(actor, "body_script", None) is None
                or self._frost_immune(target) or symmetra._attached_barrier(self, actor) is not None):
            return
        yaw, pitch = ((round(actor.yaw), round(actor.pitch)) if isinstance(actor, bots.Bot)
                      else (actor.mover.yaw, actor.mover.pitch))
        frost = self.frost_hits.setdefault(target.entity, Frost())
        sample = frost.sample
        at = self.tick * round(TICK * 1000) if now_ms is None else now_ms
        if sample is not None:
            at = max(at, sample[1])
            if sample[2]:
                if at < sample[2]:
                    return
                self.frost_sources.pop(target.entity, None)
        elif self.tick < frost.frozen_until:
            return
        entries = self.frost_sources.setdefault(target.entity, {})
        if frost.sample is None and frost.sources and frost.sources is not entries:
            # Preserve old default-host partial progress when adopting its live object.
            for body, (progress, expires) in frost.sources.items():
                entries[body, 0] = ((progress, (expires - FROST_GAP_TICKS) * round(TICK * 1000),
                                     0, yaw, pitch), False)
        frost.sources = entries  # One causal source dictionary, shared with the original owner index.
        mei_frost.prune(entries, at)
        minimum, scalar, freeze_ms, stacking = mei_frost.settings(source, weapon=channel == 0)
        key = (_source_entity(source) if source is not None else 0, channel)
        previous = entries.get(key, (None, False))[0]
        entries[key] = (mei.frost_hit(previous, at, yaw, pitch,
                                     initial=initial if minimum is None else minimum,
                                     step=expr.f32(step * scalar), freeze_ms=freeze_ms), stacking)
        sample = mei_frost.combine(entries, at, yaw, pitch, freeze_ms=freeze_ms)
        self._set_frost_sample(actor, frost, sample)
        if sample[2]:
            script = getattr(actor, "body_script", None)
            preserve = tuple(script.ultimates) if script is not None and defenses.soldier_visor_running(actor) else ()
            self._interrupt(target, preserve=preserve)

    def _mei_frost_hit(self, shooter, target: Target, tick: int) -> None:
        self._frost_hit(target, source=shooter, now_ms=tick * round(TICK * 1000))


    def clear_pins(self, entity: int) -> None:
        for source, victim in list(self.pins.items()):
            if entity in (source, victim):
                self.release_pin(source)
        self.charge_contacts.pop(entity, None)
        for contact in self.charge_contacts.values():
            if contact.captured == entity:
                contact.captured = None

    def release_pin(self, source: int) -> None:
        victim = self.pins.pop(source, None)
        if victim is not None:
            from ow174.game import assassin

            assassin.detach_victim(self.match, source, victim)

    def _hit_effect(self, target: Target, effect: int | None, *, shooter=None) -> None:
        if effect == torbjorn_mark.EFFECT:
            torbjorn_mark.apply(self, shooter, target)
            return
        """Apply the reconstructed Flashbang interruption and the scoped Talon reaction."""
        if effect not in (SLEEP_DART_EFFECT, FLASHBANG_EFFECT):
            return
        vitals = self.vitals_of(target)
        if (vitals.dead or self._invulnerable(target.owner) or reaper.intangible(target.owner)
                or immunity.control_immune(target.owner, target.entity)):
            return
        if effect == SLEEP_DART_EFFECT:
            floor = self.tick + SLEEP_WAKE_FLOOR_TICKS
            if floor > max(vitals.sleep_until, self.stuns.get(target.entity, 0)):
                vitals.sleep_wake_floor = floor
                vitals.sleep_until = self.tick + SLEEP_TICKS
                self.record_input_lock(target.entity, effect, vitals.sleep_until)
                self._interrupt(target)
            return
        # Scoped Fortify consumer: require both the actual0CF4 CombatModFilter
        # window and its authored#9161 control flag. Damage was already resolved;
        # this does not make Fortify invulnerable or generalize unknown statuses.
        if self._control_immune(target):
            return
        variables = graph.mode_variables(self.match.game_map.mode_guid) or {}
        duration = 1.7 if expr.truthy(variables.get(FLASHBANG_MODE_FLAG)) else 0.8
        deadline = self.tick + math.ceil(duration / TICK)
        if vitals.sleep_until > self.tick:
            if deadline <= vitals.sleep_until:
                return
            vitals.clear_sleep()
            self.close_input_lock(target.entity, SLEEP_DART_EFFECT)
        self.stuns[target.entity] = max(deadline, self.stuns.get(target.entity, 0))
        self.record_input_lock(target.entity, effect, self.stuns[target.entity])
        self._interrupt(target)
        self._begin_npc_hit_reaction(target, duration, self.stuns[target.entity], shooter=shooter)

    def _begin_npc_hit_reaction(
        self, target: Target, duration: float, deadline: int, *, restart: bool = True, shooter=None,
    ) -> None:
        bot = target.owner
        if (self.match.game_map.map_guid != RIALTO_RETRIBUTION or not isinstance(bot, bots.Bot)
                or bot.spawner.get('manual_spawn') is not True or bot.body_script is None):
            return
        definition = graph.graph(TALON_HIT_REACTION)
        node = definition.node(0) if definition is not None else None
        if node is None or node.cls != 'STUStatescriptStateAnim' or node.remote != 0:
            return
        current = self.npc_hit_reactions.get(target.entity)
        if not restart and current is not None and current[1].node_state(node).active:
            return
        self._end_npc_hit_reaction(target.entity)
        component = bot.body_script.component
        # 224E/12 chooses floor(random(0, 4)). Only the tested presentation
        # branch is activated. The complete 224E entry path owns other effects.
        instance = component.create(definition, values={
            (expr.INSTANCE, 215): duration,
            (expr.INSTANCE, 1934): component.rng.randrange(4),
        }, init=False)
        instance.initialized = True
        instance.initialized_frame = component.frame
        instance.disabled_start_entries.update(entry.pos for entry in definition.entries if entry is not None)
        self.npc_hit_reactions[target.entity] = (deadline, instance)
        instance.node_state(node).begin()
        voicelines.talon_hit(shooter, bot)

    def _end_npc_hit_reaction(self, entity: int) -> None:
        reaction = self.npc_hit_reactions.pop(entity, None)
        if reaction is None:
            return
        _, instance = reaction
        if not instance.stopped:
            instance.component.destroy(instance)

    @staticmethod
    def _fortify_reduction(target: Target, *, tick=None) -> float | None:
        if orisa_fortify.historical_pending(target.owner, tick):
            return None
        if immunity.fortified(target.owner, target.entity):
            return immunity.damage_scale(target.owner, target.entity)
        return None

    def _direct_hit(self, shooter, target: Target, hit: HitDef, distance: float, critical: bool, frame: int,
                    *, world_tick: int | None = None) -> None:
        critical = critical and hit.critical > 1.0 and self._fortify_reduction(target, tick=world_tick if world_tick is not None else self.tick) is None
        amount = hit.amount * hit.scale(distance) * (hit.critical if critical else 1.0)
        self._damage(shooter, target, amount, critical, hit.loadout, frame, world_tick=world_tick,
                     hit_tags=hit.tags, boost_credit=hit.boost_credit)

    def _control_immune(self, target: Target) -> bool:
        return immunity.control_immune(target.owner, target.entity) or self.orisa._immune(target.owner)

    def _interrupt(self, target: Target, *, preserve=()) -> None:
        actor = target.owner
        from ow174.game import sigma_flux

        sigma_flux.interrupt_flux_lift(self, actor)
        if isinstance(actor, bots.Bot):
            from ow174.game import sniper_retreat

            sniper_retreat.interrupt(actor)

        self.hooks.cancel_owner(target.entity)
        if not isinstance(actor, bots.Bot) and actor.hero.guid == ball.HERO:
            ball.interrupt_grapple(self, actor, self.tick)
            self.ball_contacts.pop(actor.body, None)
        self._interrupt_reaper(target.entity, actor)
        mover = actor.control_mover if isinstance(actor, bots.Bot) else getattr(actor, "mover", None)
        if mover is not None and (last := mover.last_command) is not None:
            mover.last_command = Command(last.frame, yaw=last.yaw, pitch=last.pitch)
        self.zenyatta.interrupt(target.owner)
        self.sombra_controls.interrupt(target.owner, hack_only=True)
        self.recalls.interrupt(target.entity)
        self.sound_barriers.interrupt(target.entity)
        if getattr(getattr(target.owner, "hero", None), "guid", None) == lucio.HERO:
            wall_ride.cancel(target.owner)
        self.breathers.retire(target.entity)
        self.orisa.interrupt_supercharger_cast(target.owner)
        self.brigitte.interrupted(target.entity)
        self.walls.cancel_preview(target.owner)
        script = getattr(actor, "body_script", None)
        self.interrupt_controlled(script, actor=actor, preserve=preserve)
        if script is not None and not isinstance(actor, bots.Bot):
            self.primal_punches.interrupt(script)
        cast = self.meteors.casts.get(target.entity)
        if cast is not None and cast.impacted:
            cast.finished = True
        if script is not None and mover is not None and not isinstance(actor, bots.Bot):
            active = self.movement_mods(actor, statescript_mods(script.component))
            mover.track_mods(active, script.frame)

    def _interrupt_reaper(self, entity, player):
        reaper.interrupt_blossom(player)
        channel = self.reaper_blossoms.get(entity)
        if channel is not None and not channel["ended"]:
            channel["end"] = min(channel["end"], self.now)
            channel["ended"] = True
            channel["targets"].clear()
        reaper.interrupt_shadow(player)

    def interrupt_controlled(self, script, *, actor=None, preserve=()) -> None:
        """Reconstructed input/channel interruption, shared by hosted control effects."""
        if script is None:
            return
        component = script.component
        from ow174.game import assassin, hanzo_control, photon_barrier

        ashe.set_viper_reload_stunned(component, True)

        photon_barrier.interrupt(script)
        self.punches.interrupt(actor)
        hanzo_control.cancel_draw(script)
        hero_torbjorn.cancel_hammer(self, component.entity)
        component.discard_button_presses()
        assassin.bind(component.world, component, None)
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
        # Let authored release paths establish their cooldown before forced stop.
        component.run_now()
        hanzo_control.interrupt(script)
        if isinstance(script, BodyScript):
            script.interrupt_control()
        for instance in list(component.instances.values()):
            for state in list(instance.states.values()):
                if instance.graph.index == 0x01DB and isinstance(state, nodes.MoveTo) and state.active:
                    #01DB starts movement from its on-start plug, outside the
                    #Ability subgraph. Stop the phase too: a short control must
                    #not merely pause and then resume the old dash destination.
                    state.end(False)
                    self.genji_strikes.pop(component.entity, None)
                if isinstance(state, nodes.Ability):
                    state.presses.clear()
                    if state in preserve:
                        continue
                    # Genji0317's persistent transform has no continue condition
                    # or continuing stack priority. Preserve its authored timer;
                    # the stunned command gate still blocks drawing new swings.
                    # This scoped server control policy is reconstructed.
                    if ((volume := matrices.get(state)) is not None and volume.active
                            and state.remaining() == 0):
                        instance.run_node(instance.graph.node(97), None)
                    elif (not (instance.graph.index == 0x0317 and state in getattr(script, "ultimates", ()))
                            and (not isinstance(script, BodyScript)
                                 or (not lucio.keep_active_amp_on_stun(script, state)
                                     and support.interrupt_on_stun(script, state)))):
                        if hero_torbjorn.preserve_cast_during_control(state):
                            continue
                        state.stop()  # authored exit/cooldown paths for running channels
        component.run_now()
        if impact > component.now:
            for ability in matrices:
                close = settled.get(ability, impact)
                deflection.anchor_matrix_close(ability, close, recharge=ability in settled)
        component.shots.clear()
        if hasattr(component.world, "velocity_requests"):
            component.world.velocity_requests.clear()

    @staticmethod
    def _charge_instance(player):
        script = player.body_script
        if script is not None and player.hero.guid == 0x02E0000000000007:
            return next((i for i in script.component.instances.values() if i.graph.index == 0x007D), None)
        return None

    def charge_contact(self, player, command: Command, start, seconds: float) -> None:
        """Use the delivered contact consumer on the existing Charge/mover state."""
        reinhardt_charge.advance(self, player, command, start, seconds)

    _counter_duration = staticmethod(reinhardt_charge.profile_duration)

    def charge_blocked(self, player, *, impact=True, frame=None):
        """Delivered obstruction/control exit using the existing Charge record."""
        charge = self.charges.get(player.body)
        if charge is None:
            instance = self._charge_instance(player)
            phase = instance.states.get(40) if instance is not None else None
            if phase is not None and phase.active:
                instance.write((0, 59), 0)
                reinhardt_charge.drain_end(instance)
            return
        target = self._target_entity(charge.pinned)
        if impact and target is not None:
            reinhardt_charge.impact(self, player, target, charge.state.instance, 12,
                                   self.tick if frame is None else frame)
        reinhardt_charge.stop(self, player.body, charge)

    def _legacy_charge_contact(self, player, command: Command, start, seconds: float) -> None:
        """Retained earlier narrow host; the delivered controller owns routing."""
        instance = self._charge_instance(player)
        phase = instance.states.get(3) if instance is not None else None
        if phase is None or not phase.active or self.is_stunned(player):
            self._release_charge(player, instance, phase)
            return
        contact = self.charge_contacts.setdefault(player.body, ChargeContact())
        targets = {t.entity: t for t in self._enemies(player)}
        captured = targets.get(contact.captured)
        if captured is None:
            contact.captured = None
        feet = tuple(player.mover.position)
        delta = _sub(feet, start)
        progress = math.hypot(delta[0], delta[2])
        yaw = player.mover.yaw * math.tau / 65536
        forward = (math.sin(yaw), 0.0, math.cos(yaw))
        if progress > EPSILON:
            forward = (delta[0] / progress, 0.0, delta[2] / progress)
            captured = self._charge_acquire(
                player, command, instance, contact, targets, start, forward, progress,
            )

        speed = _number(instance.find_var(0, 7726).value(), 0) * 60
        blocked = progress < speed * seconds / 2 and ray_world(
            self._collision(), _along(feet, (0, 1, 0), 1), forward,
            player.mover.data.stand_radius + speed * seconds + SKIN,
        ) is not None
        if captured is not None:
            mover = captured.owner.mover
            destination = _along(feet, forward, player.mover.data.stand_radius + captured.radius + 0.02)
            distance = math.dist(mover.position, destination)
            mover._move_to(seconds, destination, distance / seconds + 1)
            if mover.history and mover.history[-1].frame == command.frame:
                mover.history[-1] = mover.snapshot(mover.history[-1].frame)
            blocked |= math.dist(mover.position, destination) > 0.03
        if blocked:
            self.charge_contacts.pop(player.body, None)
            if captured is not None:
                self._charge_damage(player, captured, instance, 12, command.frame)
            instance.write((0, 59), 0)
            instance.component.run_now()

    def _charge_acquire(self, player, command, instance, contact, targets, start, forward, progress):
        origin = _along(start, (0, 1, 0), 1)
        radius = _number(instance.find_var(0, 9149).value(), 0)
        width = _number(instance.find_var(0, 11861).value(), 0)
        hits = []
        for target in targets.values():
            if target.entity in contact.hit or _dot(_sub(target.feet, start), forward) < 0:
                continue
            a, b = target.segment()
            hit = ray_capsule(origin, forward, a, b, radius + target.radius)
            if hit is None or hit > progress:
                continue
            centre = _along(target.feet, (0, 1, 0), 1)
            sight = _sub(centre, origin)
            length = math.hypot(*sight)
            if length > EPSILON and ray_world(
                self._collision(), origin, tuple(v / length for v in sight), length,
            ) is not None:
                continue
            hits.append((hit, target.entity, target, a, b))
        for _, _, target, a, b in sorted(hits):
            if self._invulnerable(target.owner):
                continue
            contact.hit.add(target.entity)
            pin = ray_capsule(origin, forward, a, b, width + target.radius)
            script = getattr(target.owner, "body_script", None)
            immunity = script.component.entity_var(2547, create=False) if script is not None else None
            immune = immunity is not None and expr.truthy(immunity.value())
            if (contact.captured is None and pin is not None and pin <= progress + radius
                    and not isinstance(target.owner, bots.Bot) and not self.is_stunned(target.owner)
                    and not immune):
                contact.captured = target.entity
                self._interrupt(target)
            elif (self._charge_damage(player, target, instance, 15, command.frame)
                  and not immune and not self.vitals_of(target).dead):
                # Side contact throws away from a point behind the charger.
                back = _along(start, forward, -5)
                dx, _, dz = _sub(target.feet, back)
                length = math.hypot(dx, dz)
                if length > EPSILON:
                    self._charge_impulse(target, instance, 15, (dx / length, 0.0, dz / length))
        return targets.get(contact.captured)

    def _release_charge(self, player, instance, phase) -> None:
        contact = self.charge_contacts.pop(player.body, None)
        if (contact is None or contact.captured is None or phase is None or phase.active
                or self.is_dead(player) or self.is_stunned(player) or player.gone):
            return
        target = next((t for t in self._enemies(player) if t.entity == contact.captured), None)
        if target is not None:
            yaw = player.mover.yaw * math.tau / 65536
            self._charge_impulse(target, instance, 49, (math.sin(yaw), 0.0, math.cos(yaw)))

    def _charge_impulse(self, target, instance, index, direction) -> None:
        state = instance.state(index)
        modify = state.node.field("m_modifyHealth") or {}
        strength = _number(state.evaluate(modify.get("m_E33EFEE1"), watch=False), 0)
        lift = _number(state.evaluate(modify.get("m_583C208B"), watch=False), 0)
        impulse = (direction[0] * strength, lift, direction[2] * strength)
        if isinstance(target.owner, bots.Bot):
            target.owner.impulse(impulse, self.tick, self._collision())
        else:
            target.owner.mover.add_external_impulse(impulse)

    def _charge_damage(self, player, target, instance, index, frame):
        state = instance.state(index)
        modify = state.node.field("m_modifyHealth") or {}
        amount = _number(state.evaluate(modify.get("m_amount"), watch=False), 0)
        loadout = state.evaluate(modify.get("m_loadout"), watch=False)
        return self._damage(player, target, amount, False,
                            loadout.guid if isinstance(loadout, expr.Asset) else None, frame)

    def punch_contact(self, player, command: Command, start) -> None:
        """Resolve 0D06 contact over the pose segment already admitted by the mover."""
        if self.is_stunned(player):
            return
        component = player.body_script.component
        instance = next((i for i in component.instances.values() if i.graph.index == 0x0D06), None)
        if instance is None:
            return
        motion, tracker = instance.states.get(7), instance.states.get(6)
        if motion is None or not motion.active or tracker is None or not tracker.active:
            return
        delta = _sub(player.mover.position, start)
        distance = math.sqrt(_dot(delta, delta))
        if distance <= EPSILON:
            return
        direction = tuple(v / distance for v in delta)
        origin = _along(start, (0, 1, 0), self._eye(player)[1] - player.mover.position[1] - 0.18)
        node = instance.graph.node(14)
        radius = _number(tracker.evaluate(node.field("m_radius"), watch=False), 0)
        if radius <= 0:
            return
        contacts = []
        for target in self._enemies(player):
            a, b = target.segment()
            hit = ray_capsule(origin, direction, a, b, target.radius + radius)
            if hit is None or hit > distance:
                continue
            centre = tuple((lo + hi) / 2 for lo, hi in zip(a, b, strict=True))
            sight = _sub(centre, origin)
            length = math.sqrt(_dot(sight, sight))
            if length > EPSILON and ray_world(
                self._collision(), origin, tuple(v / length for v in sight), length,
            ) is not None:
                continue
            contacts.append((hit, target.entity, target))
        if not contacts:
            return
        _, _, target = min(contacts)
        helper = graph.graph(0x0D07)
        duration, low, high = (_number(instance.vars[index].value(), 0) for index in (215, 11271, 567))
        if high <= low:
            return
        charge = min(1.0, max(0.0, (duration - low) / (high - low)))

        def scaled(low_node, high_node):
            a, b = (
                _number(instance.evaluate(helper.node(i).field("m_value")), 0) for i in (low_node, high_node)
            )
            return a + (b - a) * charge

        recovery = instance.states.get(5)
        if recovery is not None and recovery.active:
            recovery.request(runtime.FINISH)
        if not self._damage(player, target, scaled(34, 33), False, 0x0B90000000000107, command.frame):
            return
        self._grant_rocket_punch_shield(player)
        if self.vitals_of(target).dead:
            return
        script = getattr(target.owner, "body_script", None)
        immunity = script.component.entity_var(2547, create=False) if script is not None else None
        if immunity is not None and expr.truthy(immunity.value()):
            return
        timeout = _number(instance.evaluate(helper.node(1).field("m_timeout")), 0)
        deadline = self.tick + math.ceil(timeout / TICK)
        self.stuns[target.entity] = max(deadline, self.stuns.get(target.entity, 0))
        self._interrupt(target)
        flat = math.hypot(direction[0], direction[2])
        if flat > EPSILON and hasattr(target.owner, "mover"):
            heading = direction[0] / flat, 0.0, direction[2] / flat
            push = scaled(32, 31)
            target.owner.mover.add_external_impulse(tuple(axis * push for axis in heading))
            self.punch_impacts[target.entity] = PunchImpact(
                player, player.body, heading, scaled(36, 35), deadline,
            )

    def _grant_rocket_punch_shield(self, player) -> None:
        """Reconstruct 0DDD's tagged pool on a confirmed direct Punch hit."""
        component = player.body_script.component
        passive = next((i for i in component.instances.values() if i.graph.index == 0x0DDD), None)
        if passive is None:
            return
        cap_slot = passive.vars.get(11218)
        cap = _number(cap_slot.value() if cap_slot is not None else None, 0)
        gain = _number(passive.evaluate(passive.graph.node(20).field("m_value")), 0)
        rate = _number(passive.evaluate(passive.graph.node(3).field("m_modifyHealth.m_amount")), 0)
        delay = _number(passive.evaluate(passive.graph.node(2).field("m_timeout")), 0)
        if not all(math.isfinite(value) and value >= 0 for value in (cap, gain, rate, delay)):
            return
        vitals = self.vitals[player.body]
        shield = vitals.temporary_shield
        old = shield.amount if shield is not None else 0.0
        given = min(gain, max(0.0, cap - old))
        if given > 0:
            current = old + given
            vitals.temporary_shield = TemporaryShield(current, current, self.now + delay, rate)
            vitals.changed.add(SHIELDS_POOL)
            self._sync_body_health(player, vitals)

    def punch_wall_contact(self, player, command: Command, tick: int) -> None:
        """Consume one pending wall hit at the victim's admitted pose."""
        impact = self.punch_impacts.get(player.body)
        if impact is None or tick >= impact.deadline:
            return
        target = next((t for t in self._targets() if t.entity == player.body), None)
        if target is None or self.vitals_of(target).dead:
            return
        origin = _along(target.feet, (0, 1, 0), target.height / 2)
        if ray_world(self._collision(), origin, impact.direction, target.radius + SKIN + STEP) is None:
            return
        del self.punch_impacts[player.body]
        self._damage(impact.source, target, impact.damage, False, 0x0B90000000000107, command.frame)

    def _doomfist_shields(self, player, loadout) -> None:
        """Consume the delivered passive request over the retained shield pool."""
        if player.hero.guid != doomfist.HERO or player.body_script is None:
            return
        if loadout not in (0x0B90000000000106, doomfist.PUNCH, 0x0B90000000000108, doomfist.SLAM):
            return
        component = player.body_script.component
        passive = next((i for i in component.instances.values() if i.graph.index == 0x0DDD), None)
        if passive is None:
            return
        gain_node = passive.graph.node(21 if loadout == 0x0B90000000000108 else 20)
        gain = _number(passive.evaluate(gain_node.field("m_value")), 0.0)
        cap = _number(passive.vars[11218].value(), 0.0)
        rate = _number(passive.state(1).evaluate(passive.graph.node(3).field("m_modifyHealth.m_amount")), 0.0)
        delay = _number(passive.state(0).evaluate(passive.graph.node(2).field("m_timeout")), 0.0)
        if not all(math.isfinite(v) and v >= 0 for v in (gain, cap, rate, delay)):
            return
        vitals = self.vitals.get(player.body)
        if vitals is None or vitals.dead:
            return
        if vitals.add_temporary_shields(gain, cap, rate, delay, self.now, count_intrinsic=True) > 0:
            self._sync_body_health(player, vitals)

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
                    and not self._has_protection(target, {barriers.MEI: (0x5FE, 35)})
                    and not isinstance(target.owner, ball_mines.Mine)):
                pull = _number(instance.evaluate(pull_node.field('m_value')), 0.0)
                planar = math.hypot(delta[0], delta[2])
                scale = -pull / planar if planar > EPSILON else 0
                self._ball_launch(target, vertical, (scale * delta[0], scale * delta[2]), command.frame)
                if target.owner in self.match.players:
                    start = self.match.tick
                    self.pile_air_locks[target.entity] = (start, start + math.ceil(0.5 / TICK))

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
            if (not self.vitals_of(target).dead and not self._has_protection(target, {barriers.MEI: (0x5FE, 35)})
                    and (horizontal > 0 or vertical > 0)):
                planar = math.hypot(velocity[0], velocity[2])
                push = ((horizontal * velocity[0] / planar, horizontal * velocity[2] / planar)
                        if planar > EPSILON and horizontal > 0 else (0.0, 0.0))
                self._ball_launch(target, vertical, push, command.frame)

    def _ball_launch(self, target, speed, planar, frame):
        # Original upward maximum, routed through the existing owned impulse host.
        owner = target.owner
        velocity = owner.velocity if isinstance(owner, bots.Bot) else owner.mover.velocity
        self.knockbacks.apply(target, expr.Vec3(planar[0], max(0.0, speed - velocity[1]), planar[1]),
                              impact_frame=frame)

    def air_steering_locked(self, player, tick):
        start, deadline = self.pile_air_locks.get(player.body, (0, 0))
        return start <= tick < deadline and player.mover.airborne

    def _launch(self, entity: int, velocity: tuple) -> None:
        """Route delivered impulse vectors to the existing authoritative mover."""
        target = next((t for t in self._targets() if t.entity == entity), None)
        if target is None or self.vitals_of(target).dead:
            return
        if isinstance(target.owner, bots.Bot):
            target.owner.apply_impulse(velocity, self.tick, self._collision())
        else:
            target.owner.mover.impulse(velocity)
            target.owner.mover.external_impulse = True

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
        ashe.set_viper_reload_stunned(component, True)
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
        if component.world.hero == barrier.REINHARDT:
            instance = next((i for i in component.instances.values() if i.graph.index == 0x7D), None)
            if instance is not None:
                reinhardt_charge.drain_end(instance)
        component.shots.clear()
        self.punches.interrupt(target.owner)

        cast = self.meteors.casts.get(target.entity)
        if not isinstance(target.owner, bots.Bot):
            self.primal_punches.interrupt(script)
        if cast is not None and cast.impacted:
            cast.finished = True

    def spawned(self, player) -> None:
        """The player's client has the world: the game mode entity gets its UI graph (mode_frame). While
        heroes are assembled the countdown's frames carry it."""
        host = self.match.scene_host
        if player.client is not None and not self.match.assembling() and not (host and host.client_ready):
            player.client.queue_mode_frame(mode_frame(
                {}, player.match.game_map.mode_guid, player.match.game_map.map_guid,
            ))
        self.voice.spawned(player, self.now)

    def prepare(self, player, command: Command) -> None:
        """Supply live target services before this posed input advances its script."""
        if self.match.collision_world is None or player.body_script is None or self.is_dead(player):
            return
        try:
            self.brigitte.prepare(player, command, player.body_script)
        except Exception:
            log.exception("[game] %s: %s's combat targets failed", self.match.label(), player.name)

    def command(self, player, command: Command | None, *, defer_effects: bool = False,
                replay_aim: Command | None = None) -> None:
        """Consume posed reports and explicitly replayed accepted ability releases."""
        if self.match.collision_world is None:
            return
        script = player.body_script
        if script is None:
            return
        if ((reaper.is_reaper(player) or script.hero in (winston.HERO, sombra_camo.HERO))
                and command is not None and command.frame == script.frame
                and script.world_frame_anchor is not None):
            script.world_frame_anchor = self.tick, script.frame
        if command is not None:
            self.junkrat.tire.begin(player, command)
        self.barriers.sync(player)
        if script.hero == sigma_barrier.HERO and not defer_effects:
            # The authored owner mode changed on this posed frame. Reuse the
            # existing world host (elapsed zero after its tick), not another drive.
            for actor in tuple(self.sigma_barriers.values()):
                if actor.owner is player:
                    actor.update(self)
        reaper.advance_entry(player)
        self._shadow(player, command)
        self._winston_health(player, script)
        self.winston_jump.command(player, command, dead=self.is_dead(player))
        hero_torbjorn.overload(self, player, script)
        shots, script.component.shots[:] = list(script.component.shots), []
        if self.junkrat.tire.busy(player.body):
            shots = [shot for shot in shots if shot[0].allows_dead()]
        health_reports, script.component.health_reports[:] = list(script.component.health_reports), []
        motion_reports, script.component.motion_reports[:] = list(script.component.motion_reports), []
        if command is not None and reinhardt_charge.counter_frame(self, player):
            return
        self.zenyatta.command(player, command)
        interception.update_pools(self, self.tick)
        barriers.advance(self, self.tick)
        if self.is_dead(player):
            ball.cancel_grapple(self, player)
            self.genji_strikes.pop(player.body, None)
            self.genji_blades.pop(player.body, None)
        healing, script.self_healing = script.self_healing, 0.0
        repairs, script.self_repairs[:] = list(script.self_repairs), []
        support_healing, script.support_healing = script.support_healing, []
        if self.is_dead(player):
            if player.hero.guid == 0x02E0000000000065:
                terminal = command or Command(self.tick, yaw=round(player.mover.yaw), pitch=round(player.mover.pitch))
                for state, number, time, activation, *launch in shots:
                    if state.instance.graph.index == 0x022A and state.allows_dead():
                        self._shoot(player, terminal, state, number, time, activation=activation)
            return
        self._cryo_healing(player)
        if mei.protected(player):
            self._clear_frost(player)
            if not mei.cryo_world_expired(player):
                self.sentries.release_target(player.body)
        if (repairs and not self.is_stunned(player)
                and not (command is not None and script.controlled_at(command.frame))
                and player.body not in self.sombra_controls.locks):
            target = next((target for target in self._targets() if target.owner is player), None)
            if target is not None:
                vitals = self.vitals_of(target)
                given = vitals.heal(sum(repairs) * vitals.healing_factor(self.now))
                player.stats.add(stats.HEALING, given)
                self._sync_body_health(player, vitals)
                script.report_self_healing(given)
        if healing > 0.0:
            target = next((target for target in self._targets() if target.owner is player), None)
            if target is not None:
                vitals = self.vitals_of(target)
                if vitals.heal(healing * vitals.healing_factor(self.now)) > 0.0:
                    self._sync_body_health(player, vitals)
        for frame, entity, amount, tags in support_healing:
            self.heal_target(player, entity, amount, frame, tags=tags)
        if player.hero.guid == 0x02E0000000000068:
            self._sync_projected_barrier(player, script)
        reinhardt_barrier.sync(self, player)
        self.brigitte.passive_command(player, script)
        self.hooks.command(player, command)
        self.breathers.observe(player)
        self.sound_barriers.observe(player)
        if player.hero.guid == lucio.HERO and (command is not None or self.lucio_auras.leases):
            self.lucio_auras.refresh(script.frame)
        self._sync_adaptive_shield(player)
        moira.track_clock(self, player, posed=command is not None)
        if self.is_stunned(player):
            ball.cancel_grapple(self, player)
            self.ball_contacts.pop(player.body, None)
            return
        if script.hero == ball.HERO and not defer_effects:
            ball.update_grapple(self, player, command, aim(*command.aim()) if command is not None else None)
        self.meteors.command(player, command)
        if self.meteors.protected(player.body):
            return
        self.primal_punches.command(player, command)
        if script.hero == ball.HERO and not defer_effects:
            self._piledriver(player, command)
            self._fireball(player, command)
        if not defer_effects:
            self._blossom(player, register=command is not None)
        if command is not None:
            self._advance_protected_phases(script.component)
            for entities, velocity, _ in motion_reports:
                for entity in entities:
                    self._launch(entity, velocity)
            targets = {target.entity: target for target in self._enemies(player)}
            for entities, amount, loadout, time, velocity in health_reports:
                for entity in entities:
                    target = targets.get(entity)
                    if target is None:
                        continue
                    vitals = self.vitals_of(target)
                    before = vitals.total
                    self._damage(player, target, amount, False, loadout, time // script.frame_ms)
                    if vitals.total < before:
                        self._doomfist_shields(player, loadout)
                        if velocity is not None and not vitals.dead:
                            self._launch(entity, velocity)
            self._cleanse_biotic_block(player, self.vitals.get(player.body))
            moira.transition(player)
            hero_torbjorn.hammer(self, player, command, script)
            moira.grasp(self, player, command)
            moira.coalescence(self, player, command)
            if self.walls.command(player, command):
                return
            if player.hero.guid == barrier.REINHARDT and player.body not in self.shatters:
                wave = earthshatter.start(self, player)
                if wave is not None:
                    self.shatters[player.body] = wave
            for state, number, time, activation, *launch in shots:
                try:
                    options = {"viper_launch": launch[0]} if launch else {}
                    self._shoot(player, command, state, number, time, activation=activation, **options)
                except Exception:
                    log.exception("[game] %s: %s's shot failed", self.match.label(), player.name)
        elif replay_aim is not None and script.hero == sombra_camo.HERO:
            for state, number, time, activation, *launch in shots:
                if translocators.accepted_volley(state):
                    self._shoot(player, replay_aim, state, number, time, activation=activation)
        elif player.hero.guid == 0x02E000000000006E:
            for state, number, time, activation, *launch in shots:
                if state.instance.graph.index != 0x0257 or state.node.pos != 4:
                    continue
                anchor = script.world_frame_anchor
                if anchor is not None and player.mover.last_command is not None:
                    anchor_tick, anchor_frame = anchor
                    elapsed = max(0, time // script.frame_ms - anchor_frame) * script.frame_ms
                    release_tick = min(self.tick, anchor_tick + math.ceil(elapsed / (TICK * 1000)))
                    self._shoot(player, player.mover.last_command, state, number, time,
                                activation=activation, launch_tick=release_tick)
        self.baptiste.command(player, command)
        try:
            self._weapon_beam(player, command, script)
            self._melee(player, command, script)
            self._soundwave(player, command, script)
            self.brigitte.command(player, command, script)
            self._genji_strike(player, command, script)
            self._genji_blade(player, command, script)
            self.punches.command(player, command)
            self.slams.command(player, command)
        except Exception:
            log.exception("[game] %s: %s's area attack failed", self.match.label(), player.name)
        if command is not None:
            try:
                self._tesla(player, command, script)
            except Exception:
                log.exception("[game] %s: %s's Tesla Cannon failed", self.match.label(), player.name)
        try:
            self._echo_beam(player, command, script)
            if player.hero.guid != symmetra.HERO:
                self._weapon_beam(player, command, script)
        except Exception:
            log.exception("[game] %s: %s's beam failed", self.match.label(), player.name)
        self._flush_pending_attacks()

    def _winston_health(self, player, script) -> None:
        """Delivered0109 health transform, preserving its authored lifetime."""
        if player.hero.guid != winston.HERO:
            return
        state = winston.primal_health(script.component)
        extra = _number(state.evaluate(state.node.field("m_amount")), 0.0) if state else 0.0
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
            vitals.heal(sum(vitals.most.values()))
        if extra:
            self.primal_bonus[player.body] = extra
        else:
            self.primal_bonus.pop(player.body, None)
        self._sync_body_health(player, vitals)

    def _soundwave(self, player, command, script) -> None:
        """0079's 0776 st11 health operation, once per authored activation.

        Radius and damage come from the target build's st10/st11 configuration.
        Reconstruction: st10's angle 70 is treated as a full cone angle, with
        capsule-centre angular selection, fallback-height visibility and
        authored impulse through the mover. Native target filters remain open.
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
            impulse = state.evaluate(state.node.field("m_modifyHealth.m_1376B4DF"), watch=False)
            origin, direction = self._eye(player), aim(*command.aim())
            threshold = math.cos(math.radians(angle / 2))
            view_tick = command.frame - command.view_delay(player.quanta) * 1e6 / player.quanta
            wave = Soundwave(player, script.entity, command.frame, view_tick, origin, direction, radius,
                             threshold, hit, impulse if isinstance(impulse, expr.Vec3) else None)
            if wave.frame > self.tick:
                self.soundwaves.append(wave)
            else:
                self._resolve_soundwave(wave)

    def _resolve_soundwave(self, wave):
        player = wave.player
        if not player.has_body or player.gone or player.body != wave.body or self.is_dead(player):
            return
        origin, direction = wave.origin, wave.direction
        view_tick, radius, threshold = wave.view_tick, wave.radius, wave.threshold
        hit, impulse = wave.hit, wave.impulse
        for target in self._enemies(player, damageable=True):
            shown = self.seen(target, view_tick)
            if shown is None:
                continue
            a, b = shown.segment()
            if segment_distance(origin, a, b) - shown.radius > radius:
                continue
            x, y, z = shown.feet
            offset = _sub((x, y + shown.height / 2, z), origin)
            length = math.hypot(*offset)
            if length > EPSILON and _dot(offset, direction) < threshold * length:
                continue
            # CA38D5/CA4240 use these fallback points on the historical body.
            for fraction in (0.3, 0.6):
                offset = _sub((x, y + fraction * shown.height, z), origin)
                reach = math.hypot(*offset)
                if reach <= EPSILON:
                    break
                ray = tuple(value / reach for value in offset)
                terrain = self.ray_obstruction(origin, ray, reach)
                if ((terrain is None or terrain >= reach)
                        and self._barrier_contact(player, origin, ray, reach) is None
                        and self.brigitte.barrier_ray(player, origin, ray, reach) is None):
                    break
            else:
                continue
            landed = self._damage(player, target, hit.amount, False, hit.loadout,
                                  wave.frame, hit_tags=hit.tags, world_tick=self.tick, source_body=wave.body)
            if landed and not self.orisa._immune(target.owner):
                self.knockbacks.apply(target, impulse, impact_frame=wave.frame)

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
            bubble_queries = winston.HostedBarriers(self)
            bubbles = bubble_queries.snapshot()
            damaged_bubbles = set()
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
                    obstruction = bubble_queries.first(
                        player, origin, ray, max(0.0, distance - shown.radius), snapshot=bubbles,
                    )
                    if obstruction is not None:
                        bubble = obstruction[1]
                        if bubble.body not in damaged_bubbles:
                            bubble.damage(definition.amount * player.quanta / 1e6 * winston._outgoing(self, player))
                            damaged_bubbles.add(bubble.body)
                        continue
                self._damage(player, target, definition.amount * player.quanta / 1e6,
                             False, definition.loadout, command.frame, beam=True, armour_scalar=.8)
            winston.tesla_walls(self, player, origin, direction, reach, angle,
                                definition.amount * player.quanta / 1e6 * winston._outgoing(self, player),
                                bubble_queries, bubbles)

    def _weapon_beam(self, player, command, script) -> None:
        """Select a beam target and evaluate its authored ModifyHealth.

        Body capsules, eye origin and one damage sample per posed command are
        server reconstruction. Native selection, lock tolerances and cadence remain open.
        """
        if player.hero.guid == symmetra.HERO:
            return symmetra.primary(self, player, command)
        spec = BEAM_STATES.get(player.hero.guid)
        if spec is None:
            return
        graph_index, ray_index, health_index = spec
        instance = next((i for i in script.component.instances.values() if i.graph.index == graph_index), None)
        if instance is None:
            return
        ray = instance.states.get(ray_index)
        if ray is None:
            return
        # Moira's damage state consumes TrackTargets' selection. The separate
        # TrackRay in that graph does not supply the damage target.
        body_only = ray.node.cls == "STUStatescriptStateTrackTargets"
        target_field = "m_8A2905A0" if body_only else "m_target.m_692DFF72"
        range_field = "m_radius" if body_only else "m_F5732403.m_6A79BD98"
        output = expr.lvalue(ray.node.field(target_field))
        if output is None:
            return
        body = shield = None
        if command is not None and ray.active:
            reach = expr.to_float(ray.evaluate(ray.node.field(range_field), watch=False))
            if reach > 0:
                origin = self._eye(player)
                direction = aim(player.mover.yaw, player.mover.pitch)
                wall = ray_world(self._collision(), origin, direction, reach)
                limit = wall if wall is not None else math.inf
                hit = barriers.ray_hit(self, player, origin, direction, min(reach, limit))
                if hit is not None and hit[0] < limit:
                    limit = hit[0]
                    if not body_only:
                        shield = hit[1]
                for target in self._enemies(player):
                    a, b = target.segment()
                    distance = ray_capsule(origin, direction, a, b, target.radius)
                    if distance is not None and distance <= reach and distance < limit:
                        limit, body, shield = distance, target, None
        entity = body.entity if body is not None else shield.entity if shield is not None else 0
        current = instance.find_var(*output).value()
        if expr.to_entity(current) != entity:
            instance.write(output, expr.Entity(entity) if entity else None)
            instance.component.run_now()
        health = instance.states.get(health_index)
        if not entity or health is None or not health.active or expr.to_entity(
            health.evaluate(health.node.field("m_FA3ECC43"), watch=False)
        ) != entity:
            return
        modify = health.node.field("m_modifyHealth")
        rate = expr.to_float(health.evaluate(modify.get("m_amount"), watch=False))
        sample_seconds = script.component.frame_ms / 1000
        amount = rate * sample_seconds
        if shield is not None:
            barriers.damage(self, shield, amount, attacker=player.body)
        elif body is not None:
            loadout = health.evaluate(modify.get("m_loadout"), watch=False)
            self._damage(player, body, amount, False,
                         loadout.guid if isinstance(loadout, expr.Asset) else None, command.frame)

    def _winston_tesla(self, player, command, script):
        """Feed 00FE's multi-target damage state with a reconstructed visible cone."""
        if player.hero.guid != 0x02E0000000000009:
            return
        instance = next((i for i in script.component.instances.values() if i.graph.index == 0x00FE), None)
        if instance is None:
            return
        track = instance.states.get(10)
        if track is None or not track.active:
            return
        output = expr.lvalue(track.node.field("m_8A2905A0"))
        if output is None:
            return
        targets = []
        offset = track.evaluate(track.node.field("m_05BE62DD"), watch=False) if command is not None else None
        if isinstance(offset, expr.Vec3):
            centre = tuple(a + b for a, b in zip(player.mover.position, offset, strict=True))
            facing = aim(*command.aim())
            reach = expr.to_float(track.evaluate(track.node.field("m_radius"), watch=False))
            angle = expr.to_float(track.evaluate(track.node.field("m_A30B383E"), watch=False))
            threshold = math.cos(math.radians(angle))
            collision = self._collision()
            for target in self._enemies(player):
                a, b = target.segment()
                toward = _sub(_segment_point(centre, a, b), centre)
                distance = math.sqrt(_dot(toward, toward))
                if (distance <= reach + target.radius and _dot(facing, toward) + EPSILON >= threshold * distance
                        and capsule_visible(collision, centre, a, b, target.radius)):
                    targets.append(target)
        selected = tuple(expr.Entity(target.entity) for target in targets)
        if instance.find_var(*output).value() != selected:
            instance.write(output, selected)
            instance.component.run_now()
        damage = instance.states.get(13)
        if not targets or damage is None or not damage.active or damage.evaluate(
            damage.node.field("m_FA3ECC43"), watch=False
        ) != selected:
            return
        modify = damage.node.field("m_modifyHealth")
        rate = expr.to_float(damage.evaluate(modify.get("m_amount"), watch=False))
        amount = rate * script.component.frame_ms / 1000
        loadout = damage.evaluate(modify.get("m_loadout"), watch=False)
        for target in targets:
            self._damage(player, target, amount, False,
                         loadout.guid if isinstance(loadout, expr.Asset) else None, command.frame)

    def _genji_strike(self, player, command, script) -> None:
        """Reconstruct 01DB's enemy tracking during actual MoveTo movement.

        The authored body-centred sphere has radius1.5 and amount50. Sweep it
        between poses and admit each live enemy once per activation. Native
        filter/shape semantics are unverified; dynamic shields remain open.
        """
        if player.hero.guid != 0x02E0000000000029 or command is None:
            return
        instance = next((i for i in script.component.instances.values() if i.graph.index == 0x01DB), None)
        movement = instance.states.get(1) if instance is not None else None
        if movement is None:
            return
        previous = self.genji_strikes.get(player.body)
        identity = (instance.id, movement.counter)
        centre = tuple(a + b for a, b in zip(player.mover.position, (0, 1, 0), strict=True))
        if previous is None or previous[:2] != identity:
            if not movement.active:
                self.genji_strikes.pop(player.body, None)
                return
            previous = (*identity, centre, set())
        start, victims = previous[2:]
        track = instance.states.get(33)
        radius = _number(track.evaluate(track.node.field("m_radius"), watch=False), 0) if track else 0
        modify = instance.state(3).node.field("m_modifyHealth") or {}
        amount = _number(instance.evaluate(modify.get("m_amount")), 0)
        loadout = instance.evaluate(modify.get("m_loadout"))
        delta = _sub(centre, start)
        length = math.sqrt(_dot(delta, delta))
        if not movement.active and length <= EPSILON:
            self.genji_strikes.pop(player.body, None)
            return
        direction = tuple(v / length for v in delta) if length > EPSILON else (0, 0, 1)
        for target in self._enemies(player):
            if target.entity in victims:
                continue
            a, b = target.segment()
            distance = ray_capsule(start, direction, a, b, target.radius + radius)
            if distance is None or distance > length:
                continue
            midpoint = tuple((x + y) / 2 for x, y in zip(a, b, strict=True))
            to_target = _sub(midpoint, centre)
            reach = math.sqrt(_dot(to_target, to_target))
            if reach > EPSILON:
                wall = ray_world(self._collision(), centre, tuple(v / reach for v in to_target), reach)
                if wall is not None and wall < reach:
                    continue
            victims.add(target.entity)
            self._damage(player, target, amount, False,
                         loadout.guid if isinstance(loadout, expr.Asset) else None, command.frame)
        if movement.active:
            self.genji_strikes[player.body] = (*identity, centre, victims)
        else:
            self.genji_strikes.pop(player.body, None)

    def _genji_blade(self, player, command, script) -> None:
        """Reconstruct 0304's short-lived sword target windows, once per victim.

        Use authored range5, angle90 and the evaluated amount (110 in 104319).
        Cone/capsule and static LOS rules are server reconstruction; dynamic
        defenses and original-client geometry require separate acceptance.
        """
        if player.hero.guid != 0x02E0000000000029 or command is None:
            return
        instance = next((i for i in script.component.instances.values() if i.graph.index == 0x0304), None)
        if instance is None:
            return
        records = self.genji_blades.setdefault(player.body, {})
        centre = tuple(a + b for a, b in zip(player.mover.position, (0, 1, 0), strict=True))
        facing = aim(*command.aim())
        for tracking, damage in ((66, 21), (67, 22)):
            track = instance.states.get(tracking)
            if track is None or not track.active:
                continue
            key = (instance.id, tracking)
            activation, victims = records.get(key, (0, set()))
            if activation != track.counter:
                victims = set()
                records[key] = (track.counter, victims)
            radius = _number(track.evaluate(track.node.field("m_radius"), watch=False), 0)
            angle = _number(track.evaluate(track.node.field("m_A30B383E"), watch=False), 0)
            threshold = math.cos(math.radians(angle))
            state = instance.state(damage)
            modify = state.node.field("m_modifyHealth") or {}
            amount = _number(state.evaluate(modify.get("m_amount"), watch=False), 0)
            loadout = state.evaluate(modify.get("m_loadout"), watch=False)
            for target in self._enemies(player):
                if target.entity in victims:
                    continue
                a, b = target.segment()
                if segment_distance(centre, a, b) > radius + target.radius:
                    continue
                midpoint = tuple((x + y) / 2 for x, y in zip(a, b, strict=True))
                delta = _sub(midpoint, centre)
                distance = math.sqrt(_dot(delta, delta))
                if distance > EPSILON:
                    direction = tuple(v / distance for v in delta)
                    if _dot(facing, direction) + EPSILON < threshold:
                        continue
                    wall = ray_world(self._collision(), centre, direction, distance)
                    if wall is not None and wall < distance:
                        continue
                victims.add(target.entity)
                if self._genji_deflects(target, facing):
                    continue  # sword damage is blocked, not reflected
                self._damage(player, target, amount, False,
                             loadout.guid if isinstance(loadout, expr.Asset) else None, command.frame)

    def _genji_deflects(self, target, incoming) -> bool:
        """Frontal melee block while the authored Deflect state is active."""
        return self._genji_deflect_state(target, incoming) is not None

    def _genji_deflect_state(self, target, incoming):
        owner = target.owner
        hero = getattr(owner, "hero", None)
        if getattr(hero, "guid", None) != 0x02E0000000000029 or self.vitals_of(target).dead:
            return None
        script = getattr(owner, "body_script", None)
        if script is None or script.retired:
            return None
        facing = aim(owner.mover.yaw, owner.mover.pitch)
        if _dot(facing, incoming) >= -EPSILON:
            return None
        for instance in script.component.instances.values():
            if instance.graph.index == 0x03A3:
                state = instance.states.get(10)
                if state is not None and state.active and deflection.window(instance, self.tick) is not None:
                    return state
        return None

    def _genji_deflect_contact(self, target, hit, origin, direction, length, *, padding=0.0):
        """Clip an eligible shot against the authored boxes at the server pose."""
        if hit is None or not (hit.deflectable if hit.deflect_override is None else hit.deflect_override):
            return None
        state = self._genji_deflect_state(target, direction)
        if state is None:
            return None
        mover = target.owner.mover
        yaw = mover.yaw * math.tau / 65536
        forward = aim(mover.yaw, mover.pitch)
        right = (math.cos(yaw), 0.0, -math.sin(yaw))
        up = (forward[1] * right[2], forward[2] * right[0] - forward[0] * right[2],
              -forward[1] * right[0])
        # World-up reference for the box basis, with a stable right axis at vertical aim.
        basis = (right, up, forward)
        anchor = _along(target.feet, (0.0, 1.0, 0.0), target.eye)
        delta = _sub(origin, anchor)
        local_origin = tuple(_dot(delta, axis) for axis in basis)
        local_direction = tuple(_dot(direction, axis) for axis in basis)
        best = None
        for box in state.node.field("m_boxes") or ():
            low = state.evaluate(box.get("m_CBA118B2"), watch=False)
            high = state.evaluate(box.get("m_AEF67365"), watch=False)
            if isinstance(low, expr.Vec3) and isinstance(high, expr.Vec3):
                distance = interception.ray_box(local_origin, local_direction, low, high, length, padding=padding)
                if distance is not None and (best is None or distance < best):
                    best = distance
        return best

    def _sync_adaptive_shield(self, player) -> None:
        """Host 1566/state6 with a one-time enemy capsule query at the owner's eye."""
        if getattr(player.hero, "guid", None) != 0x02E00000000001CA:
            return
        script = player.body_script
        if script is None:
            return
        instance = next((item for item in script.component.instances.values()
                         if item.graph.index == 0x1566), None)
        state = instance.states.get(6) if instance is not None else None
        vitals = self.vitals.get(player.body)
        tag = moira.ADAPTIVE_SHIELD_TAG
        if state is None or not state.active:
            if vitals is not None:
                vitals.adaptive_until = 0
            if vitals is not None and (pool := vitals.extra.get(("adaptive", tag))) is not None and not pool.removed:
                vitals.remove_temporary(("adaptive", tag))
                self._sync_body_health(player, vitals)
            return
        if vitals is None:
            own = next((target for target in self._targets() if target.owner is player), None)
            if own is None:
                return
            vitals = self.vitals_of(own)
        activation = (instance.id, state.counter)
        if vitals.dead or vitals.adaptive_activation == activation:
            return
        vitals.adaptive_activation = activation
        lifetime = _number(instance.vars[567].value(), 0.0) if 567 in instance.vars else 0.0
        duration_ms = round(lifetime * 1000)
        if duration_ms > 0:
            deadline = (state.start + duration_ms) // script.frame_ms
            vitals.adaptive_until = ball.world_tick_at_body(script, deadline)
            if self._expire_adaptive_shield(player, vitals, self.tick):
                return

        radius = _number(instance.evaluate(instance.graph.state(9).field("m_radius")), 0.0)
        origin = self._eye(player)
        count = sum(
            segment_distance(origin, *target.segment()) - target.radius <= radius
            for target in self._targets()
            if target.owner is not player and not isinstance(target.owner, ball_mines.Mine)
            and (self.match.game_map.free_for_all or target.team != player.team)
            and not self.vitals_of(target).dead
        )
        amount = _number(expr.evaluate(state.node.field("m_amount"),
                                      _AdaptiveShieldContext(instance, count)), 0.0)
        if vitals.add_temporary(("adaptive", tag), tag, SHIELDS_POOL, amount, self.tick):
            self._sync_body_health(player, vitals)

    def _expire_adaptive_shield(self, owner, vitals, tick):
        if not 0 < vitals.adaptive_until <= tick:
            return False
        vitals.adaptive_until = 0
        key = ("adaptive", moira.ADAPTIVE_SHIELD_TAG)
        if key in vitals.extra and not vitals.extra[key].removed:
            vitals.remove_temporary(key)
        self._sync_body_health(owner, vitals)
        return True

    def _npc_melee(self, state, *, health=False):
        """Host Trooper/Heavy's authored sphere with live capsules and cover."""
        instance = state.instance
        spec = NPC_MELEE.get(instance.graph.index)
        if spec is None or state.node.pos != spec[2 if health else 1]:
            return NotImplemented
        actor = next((bot for bot in self.match.bots
                      if bot.entity == instance.component.entity and bot.body == spec[0]), None)
        if actor is None or actor.dead or self.is_stunned(actor):
            return True if health else ()
        tracker = instance.node_state(instance.graph.node(spec[1])) if health else state
        offset = tracker.evaluate(tracker.node.field("m_05BE62DD"), watch=False)
        radius = expr.to_float(tracker.evaluate(tracker.node.field("m_radius"), watch=False))
        x, y, z = actor.position
        forward = aim(round(actor.yaw), 0)
        centre = (x + forward[0] * offset.z, y + offset.y, z + forward[2] * offset.z)
        origin = self._eye(actor)
        collision = self._collision()
        contacts = []
        for target in self._enemies(actor):
            a, b = target.segment()
            if (segment_distance(centre, a, b) <= radius + target.radius
                    and capsule_visible(collision, origin, a, b, target.radius, walls=self.walls)):
                contacts.append(target)
        pillars = [piece for piece in self._melee_pillar_targets(origin, centre, radius)
                   if (self.match.game_map.free_for_all or piece.team != actor.team)]
        if not health:
            return tuple(expr.Entity(target.entity) for target in (*contacts, *pillars))
        modify = state.node.field("m_modifyHealth")
        amount = expr.to_float(state.evaluate(modify.get("m_amount"), watch=False))
        loadout = state.evaluate(modify.get("m_loadout"), watch=False)
        loadout_guid = loadout.guid if isinstance(loadout, expr.Asset) else None
        tags = health_tags(state, modify)
        for target in contacts:
            if target.entity not in tracker.targets:
                continue
            vitals = self.vitals_of(target)
            before = vitals.total
            self._damage(actor, target, amount, False, loadout_guid, self.tick,
                         world_tick=self.tick, hit_tags=tags)
            if not vitals.dead and vitals.total < before:
                self._melee_impulse(target, actor.position, state)
        self._melee_barriers(actor, ((centre, centre, radius),), amount)
        if amount > 0.0:
            for piece in pillars:
                if piece.entity in tracker.targets:
                    self.walls.damage(piece, actor, amount)
        return True

    @staticmethod
    def _melee_impulse(target, origin, state):
        """Apply the authored melee scalars to the reconstructed mover velocity."""
        mover = getattr(target.owner, "mover", None)
        if mover is None:
            return
        modify = state.node.field("m_modifyHealth")
        horizontal = expr.to_float(state.evaluate(modify.get("m_E33EFEE1"), watch=False))
        vertical = expr.to_float(state.evaluate(modify.get("m_583C208B"), watch=False))
        dx, dz = target.feet[0] - origin[0], target.feet[2] - origin[2]
        distance = math.hypot(dx, dz)
        scale = horizontal / distance if distance else 0.0
        vx, vy, vz = mover.velocity
        mover.velocity = (vx + dx * scale, vy + vertical, vz + dz * scale)
        if vertical > 0:
            mover.flags_word |= FELL

    def _melee(self, player, command, script, command_quanta=None) -> None:
        """Quick melee: 0043 st4 (StateModifyHealth) hits what its st5 TrackTargets finds: a sphere of
        m_radius 1.0 at (0, 0, 1.5) from the eye (DATA; unverified: along the aim), #476 = 30 damage (DATA),
        once per activation of st4. The server cannot run TrackTargets itself (the VM finds nothing)."""
        from ow174.game import assassin, mccree

        for instance in script.component.instances.values():
            hero = getattr(player.hero, "guid", player.hero)
            if hero == barrier.REINHARDT and instance.graph.index == 0x48:
                self._hammer(player, command, script, instance)
                continue
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
            from ow174.game.script import charge

            charge_context = charge.damage_context(script, state, modify) if isinstance(script, BodyScript) else None
            origin, direction = self._eye(player), aim(*command.aim())
            ring = knockback.quick_melee_ring(state) if hero == lucio.HERO else None
            centre = _along(origin, direction, reach)
            quanta = player.quanta if command_quanta is None else command_quanta
            view_tick = command.frame - command.view_delay(quanta) * 1e6 / quanta
            self._submit_attack(player, command.frame, MeleeContact(
                HitDef(amount, loadout), origin, centre, radius, view_tick, charge_context,
                health_tags(state, modify), state, ring, hero == mccree.HERO, direction,
            ))

    def _resolve_melee(self, shooter, frame, contact, amount, boost):
        from ow174.game import assassin

        origin, centre, radius = contact.origin, contact.centre, contact.radius
        state = contact.state
        modify = state.node.field("m_modifyHealth") or {}
        protected = self.barriers.area(shooter, centre, radius, amount)
        for target in self._enemies(shooter, damageable=True):
            if target.entity in protected:
                continue
            shown = self.seen(target, contact.view_tick)
            if shown is None:
                continue
            a, b = shown.segment()
            if (segment_distance(centre, a, b) - shown.radius <= radius and amount > 0.0
                    and capsule_visible(self._collision(), origin, a, b, shown.radius,
                        mesh_blocks=lambda point,direction,length: self.photon_barriers.trace(
                            shooter,point,direction,length) is not None)):
                if (deflections.blocks_melee(self, shooter, target, state, modify)
                        or self._genji_deflects(target, contact.direction)):
                    continue
                landed = self._damage(shooter, target, amount, False, contact.hit.loadout, frame,
                                      hit_reaction=contact.hit_reaction, hit_tags=contact.tags,
                                      damage_charge=contact.charge_context, boost_credit=boost)
                if landed and contact.ring is not None and not self.orisa._immune(target.owner):
                    impulse = knockback.quick_melee_impulse(contact.ring, origin, shown, contact.direction)
                    self.knockbacks.apply(target, impulse, impact_frame=frame)
                if landed and getattr(getattr(shooter, "hero", None), "guid", None) == winston.HERO:
                    self.winston_jump.knockback(shooter, target, state, 1.0)
                assassin.rescue_with_melee(self, shooter, target)
        from ow174.game import nano

        world_amount = amount * nano.offense(self, _source_entity(shooter)) * self.orisa.superchargers.scale(shooter)
        self.barriers.world.melee(shooter, origin, centre, radius, world_amount)
        self.photon_barriers.melee(shooter, origin, centre, radius,
                                   amount * self.orisa.superchargers.scale(shooter))
        self.venom.melee(shooter, origin, centre, radius, world_amount)

    def _echo_beam(self, player, command, script) -> None:
        """Reconstructed 1FBD/st17 health consumer, driven by the authored ability lifetime.

        DATA: 50 damage/s, fourfold below half total health, range from #1648 and
        TrackRay radius .2. Native tracing, armour receiver flags and visual target binding remain open.
        """
        if command is None or player.hero.guid != 0x02E0000000000206:
            return
        if self.echo_beam_seen.get(script.entity, -1) >= command.frame:
            return
        self.echo_beam_seen[script.entity] = command.frame
        instance = next((i for i in script.component.instances.values() if i.graph.index == 0x1FBD), None)
        if instance is None:
            return
        state = instance.states.get(17)
        trace = instance.states.get(10)
        if state is None or not state.active or trace is None or not trace.active:
            return
        ray = trace.node.field("m_F5732403") or {}
        reach = _number(trace.evaluate(ray.get("m_6A79BD98"), watch=False), 0.0)
        radius = _number(trace.evaluate(trace.node.field("m_57DAE1B5"), watch=False), 0.0)
        damage = instance.vars.get(11984)
        multiplier = instance.vars.get(22665)
        if reach <= 0 or radius < 0 or damage is None or multiplier is None:
            return
        rate = _number(damage.value(), 0.0)
        multiplier = _number(multiplier.value(), 1.0)
        origin, direction = self._eye(player), aim(*command.aim())
        view_tick = command.frame - command.view_delay(player.quanta) * 1e6 / player.quanta
        best = None
        for target in self._enemies(player, damageable=True):
            shown = self.seen(target, view_tick)
            if shown is None:
                continue
            a, b = shown.segment()
            distance = ray_capsule(origin, direction, a, b, shown.radius + radius)
            if distance is not None and distance <= reach and (best is None or distance < best[0]):
                best = (distance, target)
        limit = reach if best is None else best[0]
        shield = self.barriers.intercept(player, origin, direction, limit, radius=radius)
        if shield is not None:
            distance, bubble = shield
            wall = ray_world(self._collision(), origin, direction, distance)
            if wall is None or wall >= distance:
                if bubble.health < bubble.maximum * .5:
                    rate *= multiplier
                self.barriers.damage(bubble, rate * script.frame_ms * .001, player)
            return
        if best is None:
            return
        distance, target = best
        wall = ray_world(self._collision(), origin, direction, distance)
        if wall is not None and wall < distance:
            return
        vitals = self.vitals_of(target)
        if vitals.total < vitals.maximum * 0.5:
            rate *= multiplier
        modify = state.node.field("m_modifyHealth") or {}
        loadout = state.evaluate(modify.get("m_loadout"), watch=False)
        self._damage(player, target, rate * script.frame_ms * 0.001, False,
                     loadout.guid if isinstance(loadout, expr.Asset) else None, command.frame, beam=True)

    def _hammer(self, player, command, script, instance) -> None:
        """Reconstructed 0048 target search; authored states own swing timing and damage.

        Endpoints/radii are evaluated from the two native queries per swing. Local
        query space is mapped through command aim about the feet. Exact native
        sweep/filter/knockback behavior and defense services remain unaccepted.
        """
        if script.hero != REINHARDT or command is None:
            return
        forward = aim(*command.aim())
        yaw = command.aim()[0] * 2 * math.pi / 65536
        right = (math.cos(yaw), 0.0, -math.sin(yaw))
        up = (forward[1] * right[2], forward[2] * right[0] - forward[0] * right[2],
              -forward[1] * right[0])
        feet = tuple(player.mover.position)
        eye = self._eye(player)

        def position(value):
            return tuple(feet[k] + right[k] * value.x + up[k] * value.y + forward[k] * value.z
                         for k in range(3))

        view_tick = command.frame - command.view_delay(player.quanta) * 1e6 / player.quanta
        records = self.hammer_hits.setdefault(script.entity, {})
        for instance in (instance,):
            if instance.graph.index != HAMMER:
                continue
            for index, queries in HAMMER_TARGETS.items():
                state = instance.states.get(index)
                key = (instance.id, index)
                if state is None or not state.active:
                    records.pop(key, None)
                    continue
                counter, hit = records.get(key, (state.counter, set()))
                if counter != state.counter:
                    hit = set()
                records[key] = (state.counter, hit)
                modify = state.node.field("m_modifyHealth") or {}
                amount = _number(state.evaluate(modify.get("m_amount"), watch=False), 0.0)
                loadout = state.evaluate(modify.get("m_loadout"), watch=False)
                loadout = loadout.guid if isinstance(loadout, expr.Asset) else None
                volumes = []
                for query in queries:
                    tracker = instance.states.get(query)
                    if tracker is None or not tracker.active:
                        continue
                    a, b = (tracker.evaluate(tracker.node.field(name), watch=False)
                            for name in ("m_05BE62DD", "m_B3A77CD0"))
                    radius = _number(tracker.evaluate(tracker.node.field("m_radius"), watch=False), 0.0)
                    if isinstance(a, expr.Vec3) and isinstance(b, expr.Vec3) and radius > 0.0:
                        volumes.append((position(a), position(b), radius))
                from ow174.game import reinhardt_hammer
                reinhardt_hammer.surfaces(self, player, volumes, amount, hit)
                for target in self._enemies(player):
                    victim_key = ("body", (target.entity, self.replacement_ticks.get(target.entity)))
                    if victim_key in hit:
                        continue
                    shown = self.seen(target, view_tick)
                    if shown is None:
                        continue
                    c, d = shown.segment()
                    if (any(segment_pair_distance(a, b, c, d) <= radius + shown.radius
                            for a, b, radius in volumes)
                            and not winston._wall_blocks(self, eye, c, shown.radius)
                            and capsule_visible(self._collision(), eye, c, d, shown.radius)):
                        hit.add(victim_key)
                        if deflection.blocks_melee(self, player, target.owner, modify):
                            continue
                        dealt = self._damage(player, target, amount, False, loadout, command.frame,
                                             hit_tags=health_tags(state, modify))
                        if dealt and not self._control_immune(target):
                            self._hammer_knockback(state, modify, target, command.frame)
                reinhardt_hammer.objects(self, player, volumes, amount, hit)


    def _hammer_knockback(self, state, modify, target, frame):
        # Scoped 0048 reconstruction. The ring value supplies a planar velocity
        # delta because the authored base is zero. Native conversion remains open.
        rings = modify.get("m_knockbackRings") or ()
        origin = state.evaluate(modify.get("m_724CBA57"), watch=False)
        if not rings or not isinstance(origin, expr.Vec3):
            return
        point = (origin.x, origin.y, origin.z)
        ring = rings[0]
        radius = _number(state.evaluate(ring.get("m_radius"), watch=False), 0)
        if math.dist(point, target.feet) <= radius:
            speed = _number(state.evaluate(ring.get("m_9102DA3F"), watch=False), 0)
            dx, dz = target.feet[0] - point[0], target.feet[2] - point[2]
            distance = math.hypot(dx, dz)
            scale = speed / distance if distance else 0.
            self.knockbacks.apply(target, expr.Vec3(dx * scale, 0., dz * scale), impact_frame=frame)

    def _advance_idle_protection(self, player):
        """Admitted Soldier/Reaper body-clock recovery without a new movement pose."""
        from ow174.game import defenses, ultimate
        from ow174.game.script.driver import MAX_GAP

        script = player.body_script
        if (script is not None and script.hero == winston.HERO and script.world_frame_anchor is not None
                and not script.retired and not script.component.owner_dead and player.spawned and player.has_body
                and winston.reload_pending(script)):
            anchor_tick, anchor_frame = script.world_frame_anchor
            elapsed = max(0, self.tick - anchor_tick) * round(TICK * 1000)
            target = min(projectiles.MAX_LEASE_FRAME - 1, anchor_frame + elapsed // script.frame_ms - 1)
            if target > script.frame:
                player._advance_body_gap(target + 1, world_idle=True)
            return
        if (reaper.is_reaper(player) and script is not None and script.world_frame_anchor is not None
                and not script.retired and not script.component.owner_dead and player.spawned and player.has_body):
            anchor_tick, anchor_frame = script.world_frame_anchor
            elapsed_ms = max(0, self.tick - anchor_tick) * round(TICK * 1000)
            target = min(projectiles.MAX_LEASE_FRAME - 1, anchor_frame + elapsed_ms // script.frame_ms - 1)
            if target > script.frame:
                player._advance_body_gap(target + 1, world_idle=True)
            return
        if script is not None and script.hero == sombra_camo.HERO:
            sombra_replay.replay_player(self, player, self.tick - 1, TICK)
            return
        if (script is None or script.hero != defenses.SOLDIER or script.world_frame_anchor is None
                or script.retired or script.component.owner_dead or not player.spawned or not player.has_body):
            return
        visor = defenses.soldier_visor_running(player)
        recovery = defenses.soldier_input_recovery_active(player)
        windup = defenses.soldier_field_windup(player)
        passive = ultimate.practice_passive_clock(script)
        if not (visor or recovery or windup or passive):
            return
        anchor_tick, anchor_frame = script.world_frame_anchor
        elapsed_ms = max(0, self.tick - anchor_tick) * round(TICK * 1000)
        target = min(projectiles.MAX_LEASE_FRAME - 1, anchor_frame + elapsed_ms // script.frame_ms - 1)
        while target > script.frame:
            player._advance_body_gap(min(target, script.frame + MAX_GAP) + 1)
            if player.body_script is not script:
                break
            visor = visor and defenses.soldier_visor_running(player)
            recovery = recovery and defenses.soldier_input_recovery_active(player)
            windup = windup and defenses.soldier_field_windup(player)
            if not (visor or recovery or windup or passive):
                break

    def frozen_sentry_replay(self, tick):
        """Admit the delivered bounded frozen lock through the existing NPC driver."""
        from ow174.game import cryo

        freeze_ticks = math.ceil(mei.FREEZE_MS / round(TICK * 1000))
        if not COMBAT_CATCH_UP < tick - self.tick <= COMBAT_CATCH_UP + freeze_ticks or not self.frost_hits:
            return ()
        if (self.respawns or self.photon_barriers.actors or self.barriers.world.actors
                or self.blizzard.areas or self.walls.pieces
                or any(barrier.component.world.hero == barriers.ZARYA and barriers._live(self, barrier)
                       for barrier in self.barriers.scripted.values())
                or any(cryo.active(player) for player in self.match.players)
                or any(bot.firing or bot.impulse_mover is not None for bot in self.match.bots)):
            return ()
        # Ice-support/projected-impact proofs remain in the delivered owner
        # module; admit them only after their original service consumers map.
        return self.sentries.frozen_history_candidates(tick)

    def update(self, now: float, tick: int, frames: bool = True) -> None:
        """Each tick: keep where the bodies are, fly the rockets, respawn, send health that changed. The
        other players' body frames only on the ticks that send (`frames`)."""
        if self.match.collision_world is None:
            return
        from ow174.game import archives_thunderstorm, defenses, npc_shields

        # Original0016 beam integration consumes cover that ended inside the
        # accepted gap, using the existing actors and their actual clock pairs.
        sentry_damage_starts, cryo_expiries = {}, {}
        for player in self.match.players:
            deadline = mei.cryo_world_deadline(player)
            if deadline is not None and deadline <= tick:
                ended = now - (tick - deadline + 1) * TICK
                sentry_damage_starts[player.body] = ended
                cryo_expiries[player.body] = ended
        photon_cover_ends, sphere_cover_ends = [], []
        barrier_cover_ends = ((self.photon_barriers, photon_cover_ends),
                              (self.barriers.world, sphere_cover_ends))
        old_photon = tuple(self.photon_barriers.actors.values())
        old_spheres = tuple((actor, self.barriers.world.expiry_tick(actor))
                            for actor in self.barriers.world.actors.values())
        def observe_sentry_cover_ends():
            nonlocal old_photon, old_spheres
            for actor in old_photon:
                if self.photon_barriers.actors.get(actor.source_body) is not actor:
                    photon_cover_ends.append((actor, min(self.now, now - (tick - actor.expires) * TICK)))
            for actor, expiry in old_spheres:
                if self.barriers.world.actors.get(actor.entity) is not actor:
                    ended = self.now if expiry is None else min(self.now, now - (tick - expiry) * TICK)
                    sphere_cover_ends.append((actor, ended))
            old_photon = tuple(self.photon_barriers.actors.values())
            old_spheres = tuple((actor, self.barriers.world.expiry_tick(actor))
                                for actor in self.barriers.world.actors.values())

        dragon_poses = self._dragon_gap_poses(tick)
        orisa_fortify.rebase_fortify(self, tick)
        self.echo_duplicates.update(now)
        self.pulse_bombs.prepare_gap(tick, self._targets())
        for player in self.match.players:
            self.barriers.sync(player)
        retained = max(self.tick + 1, tick - COMBAT_CATCH_UP + 1)
        first = tick
        if self.tick and tick > self.tick + 1 and (
            self.dragons or any(r.continuation is not None for r in self.rockets)
            or self.timed_hits or self.recalls.phases or self.zenyatta.owners or self.breathers.channels or self.moira_clocks or self.photon_barriers.casts or self.photon_barriers.actors
            or self.hooks.flights or self.blizzard.flights or self.blizzard.areas or self.translocators.entries
            or self.zarya_gravitons.fields or self.sombra_controls.locks
            or self.sound_barriers.landings or self.sound_barriers.waves or self.sound_barriers.shielded
            or self.sound_barriers.pending
            or self.barriers.world.actors
            or self.lucio_auras.healing or self.soundwaves
            or any(shot.spawn == projectiles.FIELD_ENTITY for shot in self.projectiles.shots)
            or any(player.body_script is not None and player.body_script.world_frame_anchor is not None
                   and (defenses.soldier_visor_running(player) or defenses.soldier_field_windup(player))
                   for player in self.match.players)
            or self.companions.active
            or any(bot.firing and not bot.dead and isinstance(bot.body_script, BotScript)
                for bot in self.match.bots)
            or any(npc_shields.recovery_pending(bot) for bot in self.match.bots)
            or any(isinstance(bot.body_script, BotScript) and not bot.dead
                   and archives_thunderstorm.active_tracker(bot.body_script.component) is not None
                   for bot in self.match.bots)
            or any(r.last_tick is not None and (r.gravity or r.fuse_left is not None) for r in self.rockets)
        ):
            first = retained
        if self.tick and (self.junkrat.tire.actors or self.junkrat.tire.recovery):
            first = retained
        for player in self.match.players:
            if player.hero.guid == symmetra_charge.HERO:
                symmetra_charge.advance_idle(player, first - 1, round(TICK * 1000), before_combat=True)
        bubble_releases = {}
        for player in self.match.players:
            release = winston.HostedBarriers(self).pending_release(player, round(TICK * 1000))
            if release is not None:
                release_tick, frame = release
                at = max(retained, release_tick + 1)
                if at <= tick and frame < projectiles.MAX_LEASE_FRAME:
                    first = min(first, at)
                    bubble_releases.setdefault(at, []).append((player, player.body_script, release))
        for target in self._targets():
            history = self.history.setdefault(target.entity, deque(maxlen=HISTORY))
            history.append(self._target_sample(tick, target))
        if self.zenyatta.owners and first > self.tick + 1:
            # Preserve the delivered pre-retention support integration without
            # consuming the other hosts' independent catch-up interval.
            previous_time, previous_tick = self.now, self.tick
            while self.tick + 1 < first and self.zenyatta.competing_heals(wounded=True):
                self.tick += 1
                self.now = now - (tick - self.tick) * TICK
                self.zenyatta.update()
            self.now, self.tick = now - (tick - first + 1) * TICK, first - 1
            self.zenyatta.update()
            self.now, self.tick = previous_time, previous_tick
        # Replay the retained combat frames using the most recent poses. Player input
        # accepted just before the scheduler caught up joins only at the endpoint.
        self.junkrat.advance_before(first, now, tick)
        field_first = self.tick + 1
        for at in range(field_first, first):
            canisters = [rocket for rocket in self.rockets
                         if rocket.shot is not None and rocket.shot.spawn == projectiles.FIELD_ENTITY]
            phase = any(player.body_script is not None and player.body_script.world_frame_anchor is not None
                        and (defenses.soldier_visor_running(player) or defenses.soldier_field_windup(player))
                        for player in self.match.players)
            dragon_clock = ((self.dragons or any(rocket.continuation is not None for rocket in self.rockets))
                            and any(_active_fortify(owner) is not None for owner in self.match.players))
            if not canisters and not phase and not dragon_clock:
                break
            self.now, self.tick = now - (tick - at) * TICK, at
            orisa_fortify.advance_idle(self, at)
            for player in self.match.players:
                self._advance_idle_protection(player)
            for rocket in list(self.rockets):
                soldier = getattr(getattr(rocket.shooter, "hero", None), "guid", None) == defenses.SOLDIER
                if (rocket in canisters or (soldier and rocket.last_tick is not None)
                        or dragon_clock and rocket.continuation is not None):
                    self._fly(rocket)
            if dragon_clock:
                self.dragons[:] = [flight for flight in self.dragons
                                   if dragon.advance(self, flight, at, poses=dragon_poses)]
            self.projectiles.heal_fields(at, at)
            field_first = at + 1
        if self.dragons and field_first < first:
            self.dragons[:] = [flight for flight in self.dragons
                               if dragon.advance(self, flight, first - 1, poses=dragon_poses)]
        from itertools import chain

        replay_end = max(self.tick + sombra_replay.emp_replay_frames(self),
                         sombra_replay.application_replay_until(self, self.tick))
        early_end = min(first, replay_end + 1)
        for at in chain(range(self.tick + 1, early_end), range(first, tick + 1)):
            if field_first < at:
                self.projectiles.heal_fields(field_first, at - 1)
                field_first = at
            self.now, self.tick = now - (tick - at) * TICK, at
            for player, script, release in bubble_releases.get(at, ()):
                if (player.body_script is not script or self.is_dead(player)
                        or winston.HostedBarriers(self).pending_release(player, round(TICK * 1000)) != release):
                    continue
                release_tick, frame = release
                saved_now, saved_tick = self.now, self.tick
                try:
                    self.now = now - (tick - release_tick) * TICK
                    self.tick = release_tick
                    player._advance_body_frame(Command(frame), posed=False, defer_effects=True)
                finally:
                    self.now, self.tick = saved_now, saved_tick
            self.barriers.world.update(at)
            orisa_fortify.advance_idle(self, at)
            for player in self.match.players:
                self._advance_idle_protection(player)
                winston.charge_world(player, self)
                if player.hero.guid == ball.HERO and (vitals := self.vitals.get(player.body)) is not None:
                    self._expire_adaptive_shield(player, vitals, at)
            sombra_replay.advance_applications(self.match, at - 1)
            self.sombra_controls.update()
            self.recalls.update()
            self.breathers.update()
            self._decay_temporary_shields()
            self.sound_barriers.update()
            sigma_barrier.update_barriers(self)
            self.walls.update(at)
            self.blizzard.update(at)
            self.translocators.update(at)
            self.venom.update(at)
            self.dynamite.update(at)
            self.companions.update(at)
            for body, dome in tuple(self.winston_domes.items()):
                if dome.health <= 0 or self.now >= dome.until:
                    del self.winston_domes[body]
            interception.update_pools(self, at)
            barriers.advance(self, at)
            reinhardt_charge.sync(self)
            self.photon_barriers.update(at)
            for player in self.match.players:
                if player.hero.guid == symmetra_charge.HERO:
                    symmetra_charge.advance_idle(player, at, round(TICK * 1000))
            for player in self.match.players:
                self._update_sleep_view(player)
                self._sync_adaptive_shield(player)
            moira.advance_clocks(self)
            self.zarya_gravitons.update(at, TICK)
            moira.advance_orbs(self)
            moira.advance_spray_heals(self)
            self._flush_pending_attacks()
            for rocket in list(self.rockets):
                if at == tick or rocket.last_tick is not None:
                    self._fly(rocket)
            self.dragons[:] = [flight for flight in self.dragons
                               if dragon.advance(self, flight, at, poses=dragon_poses)]
            self.sonars[:] = [beacon for beacon in self.sonars if sonic.advance(self, beacon, at)]
            self.hooks.update(at)
            self.ball_mines.update(at)
            self.junkrat.update()
            self.orisa.update(at)
            for body, wave in list(self.shatters.items()):
                if earthshatter.advance(self, wave):
                    self.shatters.pop(body, None)
            if self.soundwaves:
                due = sorted((wave for wave in self.soundwaves if wave.frame <= at), key=lambda wave: wave.frame)
                self.soundwaves[:] = [wave for wave in self.soundwaves if wave.frame > at]
                for wave in due:
                    self._resolve_soundwave(wave)
            self._bot_weapons(at)
            self.punches.update()
            for player in self.match.players:
                if player.hero.guid == lucio.HERO:
                    lucio.advance_passive_charge(player, at)
                if player.body in self.reaper_blossoms:
                    self._blossom(player)
                reaper.charge_world(player, self)
            from ow174.game.script import charge as mercy_charge

            for player in self.match.players:
                script = player.body_script
                if script is not None and not script.retired and script.hero == 0x02E0000000000004:
                    if player.spawned and not player.gone and script.component.fault is None:
                        try:
                            expired = mercy_charge.advance_valkyrie_expiry(script, at)
                            if expired is not None:
                                player.finish_valkyrie_pose(*expired)
                                self.support_effects.retire_valkyrie_children(script.entity, expired[0])
                            mercy_charge.advance_passive(script, at)
                        except (runtime.ScriptBudgetError, runtime.UnsupportedScriptError):
                            log.exception("[game] %s: %s's world timers failed", self.match.label(), player.name)
                            self.match.stop_body_script(player, failed=True)
            from ow174.game import widowmaker

            widowmaker.update(self)
            self._advance_timed_hits()
            self.zenyatta.update()
            if self.lucio_auras.leases or self.lucio_auras.healing:
                self.lucio_auras.update(at)
            for target in self._targets():
                self.recalls.observe(target)
            self.brigitte.update()
            assassin_victim.advance(self)
            if at != tick and self.baptiste.fields:
                # Match the normal tick: attacks resolve before Field protection refreshes.
                self.baptiste.refresh_field_protection(at)
            observe_sentry_cover_ends()
            if at < tick:
                sombra_replay.replay(self, at, TICK)
                # A retained tick may send before a later tick retires its VM.
                self.match.deliver_body_messages()
        molten_core.update(self)
        for player in self.match.players:
            if player.body_script is not None and hasattr(player.body_script, "orisa_fortify_pending"):
                player.body_script.orisa_fortify_pending = False
        torbjorn_mark.update(self)
        hero_torbjorn.update_turrets(self)
        observe_sentry_cover_ends()
        self.sentries.update(sentry_damage_starts, cryo_expiries, barrier_cover_ends)
        self.portals.update()
        self.projectiles.update(tick, first_tick=field_first)
        self.knockbacks.update()
        self._flush_deadeye_notices()
        health_packs.advance(self, tick)
        for actor in list(self.deployables.values()):
            if tick >= actor.expires_tick or actor.owner.gone:
                self.destroy_deployable(actor.entity)
            elif isinstance(actor.body_script, actors.PillarScript):
                actor.body_script.component.run_frame(tick)
        self.healing.prepare()
        self.baptiste.update(tick)
        self.healing.prune()
        self.support_effects.prune({target.entity for target in self._targets()})
        self.absorption.update()
        self.match.deliver_body_messages()
        if frames:
            self.observers.update()
        self._regenerate_shields()
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
        current = {target.entity: target for target in self._targets()}
        for entity, locks in list(self.input_locks.items()):
            kept = [lock for lock in locks if lock.end is None or lock.end > tick - MAX_GAP]
            if entity in current and kept:
                self.input_locks[entity] = kept
            else:
                del self.input_locks[entity]
        for entity, frost in list(self.frost_hits.items()):
            target = current.get(entity)
            if target is None or target.owner.body_script is None:
                self.frost_hits.pop(entity, None)
                self.frost_sources.pop(entity, None)
                continue
            if self._frost_immune(target) or symmetra._attached_barrier(self, target.owner) is not None:
                self._clear_frost(target.owner)
                continue
            if frost.sample is not None:
                at = tick * round(TICK * 1000)
                sample = mei.frost_view(frost.sample, at)
                if sample is None:
                    self._clear_frost(target.owner)
                    continue
                if not sample[2]:
                    mei_frost.prune(frost.sources, at)
                    sample = mei_frost.combine(frost.sources, at, *sample[3:])
                    if sample is None:
                        self._clear_frost(target.owner)
                        continue
                self._set_frost_sample(target.owner, frost, sample)
            else:
                progress = frost.progress(tick)
                self._write_frost(target.owner.body_script.component, progress)
                if not progress:
                    self._clear_frost(target.owner)
                else:
                    for source, (_, expires) in list(frost.sources.items()):
                        if tick >= expires:
                            del frost.sources[source]
        for entity, (deadline, _) in tuple(self.npc_hit_reactions.items()):
            if entity not in current or deadline <= tick:
                self._end_npc_hit_reaction(entity)
        for entity, barrier in list(self.projected_barriers.items()):
            if entity not in current or self.now >= barrier.until:
                del self.projected_barriers[entity]
        for key in list(self.projected_casts):
            if key[0] not in current:
                del self.projected_casts[key]
        if self.personal_barriers:
            active = {p.body for p in self.match.players if p.body in self.personal_barriers
                      and self._personal_barrier_pet(p) is not None}
            for body in self.personal_barriers.keys() - active:
                del self.personal_barriers[body]
        for player in self.match.players:
            lock = self.pile_air_locks.get(player.body)
            last = player.mover.last_command
            if lock is not None and last is not None and last.frame >= lock[1]:
                del self.pile_air_locks[player.body]
        expired_viper_controls = {key for key, deadline in self.stuns.items() if deadline <= tick}
        expired_viper_controls.update(key for key, vitals in self.vitals.items()
                                      if vitals.sleep_until and vitals.sleep_until <= tick)
        for entity in expired_viper_controls:
            if self.stuns.get(entity, tick + 1) <= tick:
                del self.stuns[entity]
        for key, end in tuple(self.controls.items()):
            if end <= self.now or key[0] not in current:
                del self.controls[key]
        for player in self.match.players:
            if player.body not in self.charge_contacts:
                continue
            instance = self._charge_instance(player)
            phase = instance.states.get(3) if instance is not None else None
            if phase is None or not phase.active or self.is_dead(player) or self.is_stunned(player):
                self._release_charge(player, instance, phase)
        for entity, impact in tuple(self.punch_impacts.items()):
            if (impact.deadline <= tick or entity not in current or impact.source_body not in current
                    or self.vitals[entity].dead):
                del self.punch_impacts[entity]
        for vitals in self.vitals.values():
            if vitals.sleep_until and tick >= vitals.sleep_until:
                vitals.clear_sleep()
        for entity in sorted(expired_viper_controls):
            target = current.get(entity)
            script = getattr(target.owner, "body_script", None) if target is not None else None
            if script is not None and not self.is_stunned(target.owner):
                ashe.set_viper_reload_stunned(script.component, False)
        for table in (self.history, self.vitals, self.dragon_pose_anchors, self.replacement_ticks, self.genji_assists, self.stuns, self.hammer_hits):
            for entity in [key for key in table if key not in current and self.junkrat.lookup(key) is None]:
                del table[entity]
        for source, victim in list(self.pins.items()):
            if source not in current or victim not in current:
                self.release_pin(source)

    # --- targets -----------------------------------------------------------------------------------

    def _barrier_contact(self, shooter, origin, direction, length, radius=0.0, *, bubbles=False, team=None):
        """Use an explicit launch affiliation without replacing the source owner."""
        candidates = []
        native_raised = [actor for actor in self.barriers.enemies(shooter, team=team)
                         if isinstance(actor, reinhardt_barrier.RaisedBarrier)]
        native_bodies = {actor.body for actor in native_raised}
        for actor in native_raised:
            distance = actor.trace(origin, direction, length, radius)
            if distance is not None:
                candidates.append((distance, actor.owner, actor))
        raised = self.barrier.intersect(shooter, origin, direction, length, team=team)
        if raised is not None and raised[1] not in native_bodies:
            candidates.append((raised[0], raised[2], raised[1]))
        for hit in (
            self.barriers.world.trace(shooter, origin, direction, length, radius, team=team),
            self.orisa_barriers.trace(shooter, origin, direction, length, radius=radius, team=team),
            sigma_barrier.trace(self, shooter, origin, direction, length, radius, team=team),
            self.photon_barriers.trace(shooter, origin, direction, length, radius, team=team),
        ):
            if hit is not None:
                candidates.append((hit[0], hit[1].owner, hit[1]))
        if bubbles:
            hit = self.barriers.intercept(shooter, origin, direction, length, radius=radius, team=team)
            if hit is not None:
                candidates.append((hit[0], None, hit[1]))
        return min(candidates, key=lambda hit: hit[0], default=None)

    def _cryo_contact(self, shooter, origin, direction, length):
        from ow174.game import cryo

        hit = barriers.ray_hit(self, shooter, origin, direction, length)
        contacts = [hit[0]] if hit is not None and hit[1].component.world.hero == mei_wall.MEI else []
        # Original0016 ice is solid cover for either team, not just a hostile
        # shield. Read actual current body capsules; keep the existing native
        # actor proxy above for its other consumers.
        for target in self._targets():
            if (target.owner not in self.match.players or not cryo.active(target.owner)):
                continue
            distance = ray_capsule(origin, direction, *target.segment(), target.radius)
            if distance is not None and distance <= length:
                contacts.append(distance)
        return min(contacts, default=None)

    def _melee_pillar_targets(self, origin, centre, radius):
        hits = []
        for piece in self.walls.pieces.values():
            point = mei_wall.closest(piece, centre)
            offset = _sub(point, centre)
            if _dot(offset, offset) > radius * radius:
                continue
            delta = _sub(point, origin)
            length = math.sqrt(_dot(delta, delta))
            if length > EPSILON:
                direction = tuple(value / length for value in delta)
                wall = ray_world(self._collision(), origin, direction, length)
                pillar = self.walls.cast(origin, direction, length + EPSILON)
                if ((wall is not None and wall < length - EPSILON)
                        or (pillar is not None and pillar[0] < length - EPSILON
                            and pillar[1] is not piece)):
                    continue
            hits.append(piece)
        return hits

    def _melee_barriers(self, shooter, volumes, amount):
        origin = self._eye(shooter)
        for _, centre, radius in volumes:
            self.barriers.area(shooter, centre, radius, amount)
            self.orisa_barriers.melee(shooter, origin, centre, radius, amount)
            self.photon_barriers.melee(shooter, origin, centre, radius, amount)
            sigma_barrier.melee(self, shooter, origin, centre, radius, amount)

    def _damage_contact_barrier(self, contact, amount, *, shooter, source_body=None):
        from ow174.game import nano

        _, owner, actor = contact
        source = _source_entity(shooter) if source_body is None else source_body
        amount *= nano.offense(self, source)
        if isinstance(actor, sigma_barrier.Barrier):
            sigma_barrier.damage_barrier(self, actor, amount)
        elif isinstance(actor, orisa_barrier.Barrier):
            self.orisa_barriers.damage(actor, amount)
        elif isinstance(actor, photon_barrier.Shield):
            self.photon_barriers.damage(actor, amount)
        elif type(actor) is int:
            self.barrier.damage(actor, owner, amount)
        else:
            self.barriers.damage(actor, amount, shooter)

    def observe_dragon_pose(self, player, frame: int, exact: bool) -> None:
        """Track an observed command/world anchor, retaining its first pose during a batch."""
        if not player.mover.history or player.mover.history[-1].frame != frame:
            self.dragon_pose_anchors.pop(player.body, None)
            return
        world = self.match.tick
        previous = self.dragon_pose_anchors.get(player.body)
        latest = frame if exact else None
        if previous is None:
            anchor = (world, frame, False, latest)
        else:
            old_world, old_frame, trusted, old_latest = previous
            if world == old_world:
                continued = exact and old_latest is not None and frame == old_latest + 1
                anchor = (world, old_frame, trusted and continued, latest)
            else:
                aligned = exact and world == old_world + 1 and old_latest == old_frame and frame == old_frame + 1
                anchor = (world, frame, aligned, latest)
        self.dragon_pose_anchors[player.body] = anchor


    def _dragon_gap_poses(self, tick: int) -> dict:
        """Use a complete, explicitly received movement batch for this world gap."""
        if tick <= self.tick + 1 or not (self.dragons or any(r.continuation for r in self.rockets)):
            return {}
        from ow174.game.mover import CROUCHED

        poses = {}
        for target in self._targets():
            anchor = self.dragon_pose_anchors.get(target.entity)
            if anchor is None:
                continue
            world, frame, trusted, latest = anchor
            if not trusted or world != self.tick or latest != frame + tick - world:
                continue
            history = {sample.frame: sample for sample in target.owner.mover.history}
            if latest not in history or history[latest].position != target.feet:
                continue
            frames = range(frame, latest)
            if any(number not in history for number in frames):
                continue
            body = target.owner.hero.body
            data = mover_data(body)
            for step, number in enumerate(frames, 1):
                sample = history[number]
                crouched = bool(sample.flags & CROUCHED)
                height = data.crouch_height if crouched else data.stand_height
                poses[target.entity, world + step] = replace(
                    target, feet=sample.position, height=height, eye=eye_height(body, crouched))
        return poses


    def _regenerate_shields(self) -> None:
        for target in self._targets():
            vitals = self.vitals.get(target.entity)
            if vitals is not None and symmetra.owns_shield_recovery(target.owner):
                self._recover_shields(target.owner, vitals, self.now)

    def _recover_shields(self, owner, vitals: Vitals, through: float) -> None:
        """Integrate 0033's authored shield recovery after its damage-free delay.

        The data supplies the 3 s delay, 30 DPS and their modifiers. Scheduling
        damage-free world time here reconstructs the server's missing health
        service; it does not implement the unknown B591EBFE native State.
        """
        if not symmetra.owns_shield_recovery(owner):
            return
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
            if player.controlled_mech is not None:
                continue
            data = mover_data(player.hero.body)
            crouched = player.mover.crouched
            height = data.crouch_height if crouched else data.stand_height
            eye = eye_height(player.hero.body, crouched)
            head_offset = None
            if player.hero.body == TRACER_BODY and not crouched:
                hx, hy, hz = TRACER_HEAD
                sin_yaw, _, cos_yaw = aim(player.mover.yaw, 0)
                head_offset = (hx * cos_yaw + hz * sin_yaw, hy, hz * cos_yaw - hx * sin_yaw)
            found.append(
                Target(player.body, player.team, tuple(player.mover.position), height, data.stand_radius, eye,
                       player, critical_allowed=not ball.rolling(player), head_offset=head_offset, sentry_yaw=(round(player.mover.yaw)
                           if player.hero.guid == BASTION and player.body_script is not None
                           and player.body_script.component.entity_var(1216).value() == 2 else None))
            )  # fmt: skip
        for bot in self.match.bots:
            if bot.detached_bomb:
                continue
            data = mover_data(bot.body)
            controlled = bot.control_mover
            crouched = controlled is not None and controlled.crouched
            height, radius, eye, critical = (getattr(bot, "combat_geometry", None) or (
                data.crouch_height if crouched else data.stand_height, data.stand_radius,
                EYE if controlled is None else eye_height(bot.body, crouched), True))
            found.append(Target(bot.entity, bot.team, tuple(bot.position), height, radius, eye, bot,
                                critical_allowed=critical))
        for actor in self.deployables.values():
            if actor.health > 0:
                found.append(self._deployable_target(actor))
        found.extend(self.junkrat.targets())
        for mine in self.ball_mines.devices.values():
            if not mine.removing:
                found.append(Target(mine.body, mine.team, mine.position, 0.46, 0.23, 0.23,
                                    mine, critical_allowed=False))
        return [self.pulse_bombs.sampled(target) for target in found]

    def _target_entity(self, entity):
        """Delivered owner contact lookup over live canonical targets."""
        return next((t for t in self._targets() if t.entity == entity and not self.vitals_of(t).dead), None)

    def player_target(self, player):
        """Resolve a player controller to the body currently taking combat."""
        if player is None or player.gone or not self.match._shown(player):
            return None
        actor = player.controlled_mech or player
        return next((target for target in self._targets() if target.owner is actor), None)

    @staticmethod
    def _deployable_target(actor):
        if isinstance(actor, translocators.Translocator):
            x,y,z = actor.position
            radius = translocators.RADIUS
            return Target(actor.entity,actor.team,(x,y-radius,z),2*radius,radius,radius,
                          actor,critical_allowed=False)
        return Target(actor.entity, actor.team, actor.position, actor.height, actor.radius,
                      actor.height * 0.5, actor, critical_allowed=False, capsule=actor.capsule)

    def _ice_wall_blockers(self):
        return tuple(self._deployable_target(actor) for actor in self.deployables.values()
                     if actor.definition == ice_wall.PILLAR and actor.health > 0)

    def area_visible(self, origin, centre):
        return capsule_visible(
            self._collision(), origin, centre, centre, 0., blockers=self._ice_wall_blockers(),
        )

    def _baptiste_shot_targets(self, shooter, definition):
        targets = self._hit_targets(shooter, definition)
        pillars = self._ice_wall_blockers()
        if pillars:
            selected = {target.entity for target in targets}
            targets.extend(pillar for pillar in pillars if pillar.entity not in selected)
        return targets

    def vitals_of(self, target: Target) -> Vitals:
        return self.body_vitals(target.entity, target.owner)

    def body_vitals(self, entity: int, owner) -> Vitals:
        found = self.vitals.get(entity)
        if found is None:
            if isinstance(owner, ball_mines.Mine):
                found = Vitals(entity, 50)
                found.now[HEALTH_POOL] = owner.health
            elif isinstance(owner, (bots.Bot, actors.Deployable)):
                found = Vitals(entity, owner.health)
            else:
                found = Vitals(entity, *pools.active_pools(owner.hero.guid, owner.hero.body))
            self.vitals[entity] = found
        return found

    @staticmethod
    def _has_protection(target: Target, states) -> bool:
        """Read the live authored protection stack for this admission policy."""
        owner = target.owner
        profile = states.get(getattr(getattr(owner, "hero", None), "guid", None))
        if profile is None:
            return False
        graph_index, state_index = profile
        script = getattr(owner, "body_script", None)
        if script is None or script.retired:
            return False
        for instance in script.component.instances.values():
            if instance.graph.index == graph_index:
                if graph_index == mei.CRYO and mei.cryo_world_expired(owner):
                    return False
                if graph_index == 0x017B:
                    service = getattr(getattr(getattr(script.world, "match", None), "combat", None), "zenyatta", None)
                    if service is not None and service.stale_ultimate_flag(owner):
                        return False
                state = instance.states.get(state_index)
                return state is not None and state.active
        return False

    def _wheel_model_pose(self, entity: int) -> ModelPose | None:
        player = self.match._message_player(entity)
        script = player.body_script if player is not None else None
        if script is None or script.retired or script.component.fault is not None:
            return None
        mover = player.mover
        right = aim(mover.yaw + 16384, 0)
        forward = aim(mover.yaw, 0)
        transform = (*right, 0.0, 0.0, 1.0, 0.0, 0.0, *forward, 0.0, *mover.position, 1.0)
        return script.world.wheel_model_pose(script.component, transform)

    def _wheel_base_actor_admits(
        self, profile: int, _source: int, entity: int, category: int, _holder_mask: int,
    ) -> bool:
        # The two wheel queries leave the relation reference empty. Their dynamic masks
        # include category 0100, and catalog base player bodies have no authored receiver.
        if profile not in (0x0690000000000079, 0x0690000000000111) or category != 0x0100:
            return False
        player = self.match._message_player(entity)
        base = heroes().get(player.hero.guid) if player is not None else None
        return base is not None and player.hero.body == base.body

    def communication_target(
        self, entity: int, offset, radius: float, aperture: float, category: int, *, profile: int = 0,
    ) -> int | None:
        collision = self.match.collision_world
        source = self.match._message_player(entity)
        if collision is None or source is None or not isinstance(offset, expr.Vec3):
            return None
        if not (math.isfinite(radius) and radius > 0 and 0 < aperture < 180):
            return None

        mover = source.mover
        x, y, z = mover.position
        pivot = eye_height(source.hero.body, mover.crouched)
        eye = (x, y + pivot, z)
        forward = aim(mover.yaw, mover.pitch)
        right = aim(mover.yaw + 16384, 0)
        up = aim(mover.yaw, mover.pitch - 16384)
        # C9A92F pitches the local offset around the first-person pivot.
        origin = _along(_along(_along(eye, right, offset.x), up, offset.y - pivot), forward, offset.z)
        limit = math.cos(math.radians(aperture * 0.5))
        start, end = _along(origin, forward, -radius), _along(origin, forward, radius)
        rays = collision.ray_geometry or collision
        best = None
        best_score = math.inf
        targets = self._targets()
        poses = {}
        actor_shapes = []
        if self.wheel_actor_admits is not None:
            for actor in targets:
                if actor.entity == entity:
                    continue
                pose = poses[actor.entity] = self.model_pose_for(actor.entity)
                if pose is None:
                    continue
                for bone, actor_category, geometry in pose.actor_shapes:
                    if self.wheel_actor_admits(profile, entity, actor.entity, actor_category, geometry[1]):
                        matrix = pose.bone_matrix(bone)
                        if matrix is not None and len(matrix) == 16:
                            actor_shapes.append((actor.entity, matrix, geometry))
        for target in targets:
            if target.entity == entity or isinstance(target.owner, bots.Bot) or self.is_dead(target.owner):
                continue
            a, b = target.segment()
            if segment_distance(origin, a, b) > radius + target.radius:
                continue
            point, axis = contacts.closest_segments(a, b, start, end)
            gap = math.dist(point, axis)
            if gap > target.radius:
                point = _along(point, _sub(axis, point), target.radius / gap)
            delta = _sub(point, origin)
            depth = _dot(delta, forward)
            if depth < limit * math.hypot(*delta):
                continue
            pose = poses[target.entity] if target.entity in poses else self.model_pose_for(target.entity)
            if not _communication_visible(rays, origin, target, category, pose, actor_shapes):
                continue
            screen = _communication_screen_score(target, eye, forward, right, up)
            # C9753A reads the candidate transform's translation.
            score = screen + 0.01 * min(1.0, math.dist(origin, target.feet) / radius)
            if score < best_score:
                best, best_score = target.entity, score
        return best

    def _enemies(self, shooter, *, damageable: bool = False, team=None) -> list[Target]:
        free_for_all = self.match.game_map.free_for_all
        team = shooter.team if team is None else team
        source = _source_entity(shooter)
        owner = getattr(shooter, "combat_owner", None) if isinstance(shooter, bots.Bot) else shooter
        found = []
        for target in self._targets():
            if (target.owner is shooter or target.entity == source or target.owner is owner
                    or (owner is not None and getattr(target.owner, "combat_owner", None) is owner)):
                continue
            if (isinstance(target.owner, bots.Bot) and target.owner.owner == shooter.entity
                    and target.owner.source_body == source):
                continue
            if isinstance(shooter, bots.Bot) and shooter.owner and target.entity == shooter.source_body:
                continue
            if isinstance(target.owner, actors.Deployable) and target.owner.owner is shooter:
                continue
            if isinstance(target.owner, ball_mines.Mine) and target.owner.source == source:
                continue
            if not free_for_all and target.team == team:
                continue
            if (self.vitals_of(target).dead or reaper.intangible(target.owner) or self._has_protection(target, PHASE_STATES)
                    or self.pulse_bombs.recalling(target)):
                continue
            if damageable and immunity.wraith_active(target.owner, target.entity):
                continue
            found.append(target)
        return found

    def _ally(self, shooter, target: Target) -> bool:
        owner = self._credit_player(shooter)
        return ((owner is not None and self._credit_player(target.owner) is owner)
                or (not self.match.game_map.free_for_all and target.team == shooter.team))

    def _shot_targets(self, shooter, direct: HitDef | None) -> list[Target]:
        targets = self._enemies(shooter)
        if direct is not None and direct.healing:
            return self._baptiste_shot_targets(shooter, direct)
        if direct is None or direct.ally_heal <= 0.0 or self.match.game_map.free_for_all:
            return targets
        for target in self._targets():
            if target.entity == _source_entity(shooter) or not self._ally(shooter, target):
                continue
            vitals = self.vitals_of(target)
            if not vitals.dead and vitals.base_total < vitals.base_maximum - EPSILON:
                targets.append(target)
        return targets

    @staticmethod
    def _target_sample(tick, target, *, feet=None):
        sample = (tick, target.feet if feet is None else feet, target.height, target.eye)
        if getattr(getattr(target.owner, "hero", None), "guid", None) == ball.HERO:
            # Append after existing head-offset/sentry-yaw positions; retain other owner layouts.
            return (*sample, target.head_offset, target.sentry_yaw, target.critical_allowed)
        if (getattr(getattr(target.owner, "hero", None), "guid", None) == BASTION
                or target.sentry_yaw is not None):
            return (*sample, target.head_offset, target.sentry_yaw)
        if (getattr(getattr(target.owner, "hero", None), "body", None) == TRACER_BODY
                or target.head_offset is not None):
            return (*sample, target.head_offset)
        return sample

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
            sample = before
            feet = sample[1]
        else:
            share = (view_tick - before[0]) / (after[0] - before[0])
            feet = tuple(a + (b - a) * share for a, b in zip(before[1], after[1], strict=True))
            sample = before if share < 0.5 else after
        _, _, height, eye, *region = sample
        head_offset = region[0] if region else None
        sentry_yaw = region[1] if len(region) > 1 else None
        critical_allowed = region[2] if len(region) > 2 else target.critical_allowed
        return Target(target.entity, target.team, feet, height, target.radius, eye, target.owner,
                      critical_allowed, target.capsule, head_offset, sentry_yaw)

    def script_targets(self, source: int, state):
        """Reconstructed Mercy staff selection, restricted to its actual query states.

        Radius and cone angle are evaluated from104319 fields. Capsule-centre
        aim ranking/occlusion and half-angle interpretation await native/client
        comparison. Other target geometries and status filters remain unmounted.
        """
        staff = state.instance.graph.index == 0x006B and state.index in (4, 5, 51)
        guardian = state.instance.graph.index == 0x006E and state.index in (5, 14)
        resurrect = state.instance.graph.index == 0x00CA and state.index in (14, 38)
        if not staff and not guardian and not resurrect:
            return NotImplemented
        player = next((p for p in self.match.players if p.has_body and p.body == source), None)
        if player is None or player.hero.guid != regeneration.MERCY:
            return NotImplemented
        if self.is_dead(player) or self.match.game_map.free_for_all:
            return ()
        radius = _number(state.evaluate(state.node.field("m_radius"), watch=False), 0.0)
        angle = _number(state.evaluate(state.node.field("m_A30B383E"), watch=False), 360.0)
        origin = self._eye(player)
        direction = aim(player.mover.yaw, player.mover.pitch)
        retained = None
        if staff and state.index == 5:
            selected = state.instance.find_var(graph.INSTANCE, 375).value()
            retained = {item.id for item in selected if isinstance(item, expr.Entity)} if isinstance(
                selected, tuple) else set()
        elif staff:
            hold = state.instance.states.get(5)
            if hold is not None and hold.active:
                # A retiring hold cancels its queued begins. Let its authored
                # reset finish before the acquisition state hands off again.
                return ()
        component = state.instance.component
        frame = component.frame
        expiry = component.world.historical_valkyrie_deadline() if staff else None
        historical_boost = (expiry is not None and frame < self.tick and state.now < expiry
                            and support_effects.staff_boost_selected(state.instance))
        candidates = []
        for target in self._targets():
            if (target.entity == source or target.team != player.team
                    or isinstance(target.owner, actors.Deployable)):
                continue
            dead = self.vitals_of(target).dead
            if resurrect and (not dead or isinstance(target.owner, bots.Bot)):
                continue
            if historical_boost:
                target = support_effects.boost_target_at(self, target, frame)
                if target is None:
                    continue
            elif dead and not resurrect and (not guardian or isinstance(target.owner, bots.Bot)):
                continue
            if retained is not None and target.entity not in retained:
                continue
            centre = (target.feet[0], target.feet[1] + target.height * 0.5, target.feet[2])
            offset = _sub(centre, origin)
            distance = math.sqrt(_dot(offset, offset))
            if distance > radius or distance <= EPSILON:
                continue
            forward = tuple(value / distance for value in offset)
            alignment = _dot(direction, forward)
            if angle < 360 and alignment < math.cos(math.radians(max(0.0, angle) * 0.5)):
                continue
            wall = self.ray_obstruction(origin, forward, distance)
            if wall is not None and wall < distance:
                continue
            candidates.append((-alignment, distance, target.entity))
        candidates.sort()
        return tuple(row[2] for row in candidates[:1])

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
            and not isinstance(target.owner, (actors.Deployable, ball_mines.Mine))
            and target.team == shooter.team and not self.vitals_of(target).dead
        ]

    def _apply_hit(self, shooter, target, definition, amount, critical, frame, *, world_tick=None,
                   source_entity=None):
        if definition.duration > 0.0:
            self._queue_rifle_dose(shooter, target, definition, amount, frame, source_entity=source_entity)
            return
        if not definition.healing:
            if (isinstance(target.owner, actors.Deployable) and target.owner.definition == ice_wall.PILLAR
                    and (target.owner.owner is shooter or (not self.match.game_map.free_for_all
                                                          and shooter.team == target.team))):
                return
            if self._damage(shooter, target, amount, critical, definition.loadout, frame,
                            world_tick=world_tick, source_body=source_entity, hit_tags=definition.tags):
                self._hit_effect(target, definition.effect, shooter=shooter)
            return
        # Recheck affiliation/lifetime at impact: neither a stale target nor a projectile
        # in flight may heal a new enemy or resurrect a corpse.
        if target.team != shooter.team or not any(
            current.entity == target.entity and current.owner is target.owner
            for current in self._hit_targets(shooter, definition)
        ):
            return
        moira.spray_hit(self, shooter, target, definition, source_body=source_entity)
        self._heal_target(shooter, target, amount, source_entity=source_entity, tags=definition.tags)

    def _queue_rifle_dose(self, shooter, target, definition, amount, frame, *, source_entity=None, team=None,
                          scalar=None):
        from ow174.game import nano

        source = _source_entity(shooter) if source_entity is None else source_entity
        team = shooter.team if team is None else team
        healing = not self.match.game_map.free_for_all and target.team == team
        vitals = self.vitals_of(target)
        if target.entity == source or vitals.dead:
            return
        if healing:
            if definition.ally_heal <= 0:
                return
        else:
            if self.damage_immune(target.owner):
                return
            if scalar is None:
                scalar = nano.offense(self, source) * self.orisa.superchargers.scale(shooter)
            amount = self._team_damage(shooter, amount * scalar)
            if barriers.protect_body(self, target, amount, attacker=source) is not None:
                return
            if self._absorb_personal_barrier(target, amount, source) is not None:
                return
            if self._absorb_projected_barrier(target, amount, source) is not None:
                return
            bubble = self.barriers.for_target(target.owner)
            if bubble is not None:
                self.barriers.damage(bubble, amount, shooter)
                return
        self.timed_hits.append(TimedHit(
            shooter, source, target.entity, team, healing, amount, self.now,
            definition.duration, definition.loadout, frame, definition.tags,
        ))

    def _advance_timed_hits(self):
        if not self.timed_hits:
            return
        targets = {target.entity: target for target in self._targets()}
        for dose in tuple(self.timed_hits):
            if dose not in self.timed_hits:
                continue
            target = targets.get(dose.target)
            if target is None or self.vitals_of(target).dead or (dose.healing and target.team != dose.team):
                self.timed_hits.remove(dose)
                continue
            share = max(0.0, min(1.0, (self.now - dose.started) / dose.duration))
            cumulative = dose.amount if share >= 1 else round(dose.amount * share * 1024) / 1024
            amount = max(0.0, cumulative - dose.applied)
            dose.applied = cumulative
            if amount > 0:
                if dose.healing:
                    self._heal_target(dose.shooter, target, amount, source_entity=dose.source_entity, tags=dose.tags)
                else:
                    self._damage(dose.shooter, target, amount, False, dose.loadout, dose.frame,
                                 world_tick=self.tick, source_body=dose.source_entity,
                                 hit_tags=dose.tags, source_scaled=True, armour_scalar=1.0)
            if share >= 1 or self.vitals_of(target).dead:
                if dose in self.timed_hits:
                    self.timed_hits.remove(dose)

    def cleanse_ana_effects(self, entity):
        self.healing.record_regeneration(entity)
        self.healing.anti.pop(entity, None)
        self.healing.record_regeneration(entity)
        self.timed_hits[:] = [hit for hit in self.timed_hits if hit.target != entity or hit.healing]

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
                       for piece in self.walls.pieces.values())


    def teleport_body(self, target, destination):
        mover = target.owner.mover
        history = self.history.setdefault(target.entity, deque(maxlen=HISTORY))
        # The scheduler records the endpoint pose before replaying retained
        # ticks. Replace that sample if the teleport happened during replay.
        endpoint = history.pop()[0] if history and history[-1][0] > self.tick else None
        history.append(self._target_sample(self.tick, target))
        mover.position[:] = [quantise(value) for value in destination]
        if mover.body is not None:
            x, y, z = mover.position
            mover.body.teleport((x, y + mover._lift(), z))
        history.append(self._target_sample(self.tick, target, feet=tuple(mover.position)))
        if endpoint is not None:
            history.append(self._target_sample(endpoint, target, feet=tuple(mover.position)))


    def body_health(self, entity):
        """Read delivered component51 lifetimes without allocating a pool."""
        owner = next((player for player in self.match.players
                      if not player.gone and player.has_body and player.body == entity), None)
        if owner is None:
            owner = next((bot for bot in self.match.bots if bot.entity == entity), None)
        if owner is None:
            owner = self.deployables.get(entity)
        if owner is None:
            owner = self.junkrat.lookup(entity)
        if owner is None:
            owner = self.ball_mines.devices.get(entity)
        if owner is not None:
            vitals = self.vitals.get(entity)
            if vitals is not None:
                return vitals.total, vitals.maximum
            maximum = (50.0 if isinstance(owner, ball_mines.Mine) else owner.health if isinstance(owner, (bots.Bot, actors.Deployable))
                       else sum(pools.active_pools(owner.hero.guid, owner.hero.body)))
            return float(maximum), float(maximum)
        if (entity == world.GAME_MODE_ENTITY
                or any(not p.gone and p.entity == entity for p in self.match.players)):
            return 0.0, 0.0
        for actor in self.sigma_barriers.values():
            if actor.entity == entity:
                return actor.vitals.total, actor.vitals.maximum
        return None

    def heal_target(self, healer, entity, amount, frame, *, allow_self=False, tags=None):
        if (amount <= 0 or (entity == healer.body and not allow_self)
                or (self.match.game_map.free_for_all and entity != healer.body)
                or self.replacement_ticks.get(entity, frame) > frame):
            return 0.0
        target = next((target for target in self._targets() if target.entity == entity), None)
        if (target is None or target.team != healer.team or self.vitals_of(target).dead
                or isinstance(target.owner, actors.Deployable)):
            return 0.0
        identifiers = tuple(tag.guid for tag in tags or ())
        given = self._heal_target(healer, target, amount, source_entity=healer.body, tags=identifiers)
        from ow174.game.script import charge

        charge.healed(healer.body_script, entity, given, tags, frame=frame)
        return given

    def _charge_ana(self, actor, amount, *, source_entity=None):
        """Delivered effective-health credit; never transfer it across source bodies."""
        if (isinstance(actor, bots.Bot)
                or getattr(getattr(actor, "hero", None), "guid", None) != 0x02E000000000013B):
            return
        if source_entity is not None and actor.body != source_entity:
            return
        script = actor.body_script
        if script is not None:
            script.gain_ult_charge(amount)

    def _cryo_healing(self, player) -> None:
        sample = mei.healing_sample(player)
        if sample is None:
            return
        key, cumulative, rate = sample
        target = next((t for t in self._targets() if t.owner is player), None)
        if target is None:
            return
        vitals = self.vitals_of(target)
        if vitals.dead:
            return
        history = self.cryo_samples.get(player.body)
        def note(at):
            blocked = vitals.healing_block[1] if vitals.healing_block[0] <= at else 0.
            boosted = vitals.healing_boost[1] if vitals.healing_boost[0] <= at else 0.
            received = self.healing.factor(target, end_tick=at / TICK)
            history.note(at, blocked, boosted, received)

        if history is None or history.activation != key:
            start = self.now - cumulative / rate
            history = self.cryo_samples[player.body] = mei.CryoHealing(key, start)
            note(start)
        # Retained status intervals supply their exact boundaries even when body
        # samples arrive after expiry. Settle before a real status is replaced.
        points = {*vitals.healing_block, *vitals.healing_boost}
        for effect in (self.healing.anti.get(target.entity), self.healing.amp.get(target.entity)):
            if effect is not None:
                points.update((effect.start * TICK, effect.end * TICK))
        for at in sorted(point for point in points if history.at < point <= self.now):
            note(at)
        note(self.now)
        due = history.sample(cumulative, rate, player.body_script.component.frame_ms / 1000,
                             BIOTIC_HEAL_BOOST)
        if due > 0.:
            self._heal_target(player, target, due, healing_scaled=True)

    def record_cryo_status(self, entity) -> None:
        player = next((p for p in self.match.players if p.body == entity), None)
        if player is not None:
            self._cryo_healing(player)

    def _heal_target(self, shooter, target, amount, *, source_entity=None, tags=(), seconds=0.0,
                     end_tick=None, healing_scaled=False):
        vitals = self.vitals_of(target)
        if vitals.dead:
            return 0.0
        if not healing_scaled:
            amount *= self.healing.factor(target, tags=tags, seconds=seconds, end_tick=end_tick)
            if seconds > 0.0:
                end = (self.tick if end_tick is None else end_tick) * TICK
                amount *= vitals.healing_time(end - seconds, end) / seconds
            else:
                amount *= vitals.healing_factor(self.now)
        eligible = vitals.ult_healing_deficit
        given = vitals.heal(amount)
        if given > 0.0:
            if not isinstance(shooter, (bots.Bot, SceneHazard)):
                shooter.stats.add(stats.HEALING, given)
                if target.owner is shooter or (not self.match.game_map.free_for_all and target.team == shooter.team):
                    self._charge_ana(shooter, given, source_entity=source_entity)
                    if (shooter.hero.guid == 0x02E000000000006E
                            and (source_entity is None or source_entity == shooter.body)):
                        script = shooter.body_script
                        if script is not None:
                            script.gain_ult_charge(min(given, eligible), kind="healing", recipient=target.entity)
                if shooter.hero.guid == 0x02E00000000000DD and target.owner is shooter:
                    from ow174.game import mei_ultimate

                    mei_ultimate.grant(shooter, given, healing=True)
                self._charge_echo_health_event(shooter, given, source_body=source_entity)
                moira.gain_combat_charge(shooter, given, body=source_entity, healing_target=target, healing_tags=tags)
            self.baptiste.healing_charge(shooter, target, min(given, eligible), source_body=source_entity)
            self._sync_body_health(target.owner, vitals)
        return given

    def _heal_pools(self, owner, vitals, amount, *, maximum=None, tags=()):
        if vitals.dead:
            return 0.0
        if vitals.entity in self.healing.anti or vitals.entity in self.healing.amp:
            target = next((target for target in self._targets()
                           if target.entity == vitals.entity and target.owner is owner), None)
            if target is not None:
                amount *= self.healing.factor(target, tags=tags)
        amount *= vitals.healing_factor(self.now)
        given = vitals.heal(amount if maximum is None else min(amount, maximum))
        if given > 0.0:
            self._sync_body_health(owner, vitals)
        return given

    # --- shots -------------------------------------------------------------------------------------

    def bind_scene_enemy(self, host, bot: bots.Bot) -> None:
        """Give an allocated scene bot's health and death lifecycle to the scene scheduler."""
        if bot not in self.match.bots:
            raise ValueError('scene enemy must be allocated in the match')
        vitals = self.vitals.get(bot.entity)
        if vitals is None:
            vitals = Vitals(bot.entity, bot.health)
        host.bind_combatant(bot, self._retire_scene_enemy)
        self.vitals[bot.entity] = vitals
        component = host.combatant_component(bot)
        self._write_scene_health(component, vitals)
        if component.instances:
            host.bind_body_projection(bot, lambda snapshot, first_death: self._publish_scene_body(
                bot, snapshot, first_death,
            ))

    def _publish_scene_body(self, bot, snapshot, first_death):
        for viewer in self.match.players:
            client = viewer.client
            if client is None or not viewer.spawned:
                continue
            if first_death:
                client.queue_bot_death(bot.entity, snapshot)
            elif bot.dead:
                client.queue_bot_death_update(bot.entity, snapshot)
            else:
                client.queue_bot_live(bot.entity, snapshot)

    def _scene_component(self, bot):
        host = getattr(self.match, 'scene_host', None)
        return host.combatant_component(bot) if host is not None else None

    def consume_scene_boss_shots(self, bot, component, frame):
        from ow174.game import junkenstein_ai

        if self._scene_component(bot) is not component:
            return
        if bot.hero == junkenstein_ai.MERCY:
            weapon_graph, volleys = 0x006D, (10,)
        elif bot.hero == junkenstein_ai.SYMMETRA:
            weapon_graph, volleys = 0x157C, (85,)
        else:
            primary = junkenstein_ai.BOSS_PRIMARY.get(bot.hero)
            if primary is None:
                return
            _, weapon_graph, volley = primary
            volleys = (volley,)
            if bot.hero == junkenstein_ai.ROADHOG:
                volleys += (junkenstein_ai.ROADHOG_SECONDARY_VOLLEY,)
        shots, component.shots[:] = tuple(component.shots), []
        if bot.dead or self.is_stunned(bot, tick=frame):
            return
        for state, number, time, activation, *launch in shots:
            if state.instance.graph.index == weapon_graph and state.node.pos in volleys:
                self._shoot(bot, None, state, number, time, activation=activation, launch_tick=frame)

    def _retire_scene_enemy(self, bot):
        if bot not in self.match.bots:
            return
        self.match.bots.remove(bot)
        self.respawns[:] = [(when, actor) for when, actor in self.respawns if actor is not bot]
        for table in (self.vitals, self.history, self.replacement_ticks, self.melee_seen, self.stuns):
            table.pop(bot.entity, None)
        self.clear_pins(bot.entity)
        for viewer in self.match.players:
            if viewer.client is not None:
                viewer.client.queue_bot_destroy(bot.entity)
        bot.body_script = None

    def bind_scene_zombardier(self, host, control) -> None:
        """Connect an allocated scene enemy to live combat targets and projectile launch."""
        def targets(shooter):
            if shooter.dead or self.is_stunned(shooter, tick=host.frame):
                return ()
            return (target for target in self._enemies(shooter) if target.entity in host.world.entities)

        def launch(shooter, state, number, time, activation, frame):
            if not shooter.dead and not self.is_stunned(shooter, tick=frame):
                projection = host.combatant_projection(shooter)
                snapshot = projection.latest_live if projection is not None else None
                self._shoot(shooter, None, state, number, time, activation=activation,
                            snapshot=snapshot, launch_tick=frame)

        host.bind_zombardier(control, collision=self._collision, targets=targets, launch=launch)

    def _bot_weapons(self, tick: int) -> None:
        """Advance the weapon bodies of the map's firing bots and consume their volley reports."""
        from ow174.game import archives_thunderstorm, npc_control

        host = getattr(self.match, 'scene_host', None)
        for bot in self.match.bots:
            if bot.body in (hero_torbjorn.TURRET, symmetra_turrets.BODY) and bot.combat_owner is not None:
                continue  # Original turret manager advances this VM and is its sole gun driver.
            if bot.control_mover is not None:
                continue
            if host is not None and (bot.entity in host.weapons or host.combatant_component(bot) is not None):
                continue
            script = bot.body_script
            vm_tick = tick + bot.clock_offset if bot.owner else tick
            controlled = npc_control.manual(bot)
            autonomous = npc_control.autonomous(bot)
            authored = isinstance(script, BotScript) and script.has_controller_buttons
            if ((not bot.firing and not controlled and not authored and not autonomous
                 and not bot.spawner.get("acquire_targets")) or bot.dead
                    or not isinstance(script, BotScript) or script.frame > vm_tick):
                continue
            try:
                if bot.spawner.get("acquire_targets"):
                    from ow174.game import npc

                    npc.update_acquisition(self, bot)
                    allowed = not self.is_stunned(bot, tick=tick)
                elif autonomous:
                    allowed = npc_control.acquire(self, bot)
                else:
                    allowed = npc_control.update_input(self, bot) if controlled and not authored else not self.is_stunned(bot)
                advanced = script.frame < vm_tick
                from ow174.game import sigma_flux

                sigma_flux.sync_flux_airborne(script.component, bot.impulse_mover)
                snapshot = script.advance_live(vm_tick, bot.position)
                if bot not in self.match.bots or bot.body_script is not script:
                    continue
                if advanced:
                    archives_thunderstorm.advance(self, bot, script.component)
                    if (bot.spawner.get("acquire_targets") and bot.spawner.get("acquire_specials")
                            and getattr(bot, "wall_mover", None) is None):
                        from ow174.game import sniper_retreat

                        sniper_retreat.advance(self, bot)
                from ow174.game import assassin

                assassin.refresh_control(script.component.world, script.component)
                shots, script.component.shots[:] = list(script.component.shots), []
                if self.is_stunned(bot, tick=tick):
                    shots = []
                for state, number, time, activation, *launch in shots:
                    if not allowed:
                        continue
                    self._shoot(
                        bot, None, state, number, time, activation=activation,
                        snapshot=snapshot or script.latest_live,
                        **({"viper_launch": launch[0]} if launch else {}),
                    )
                if snapshot is not None:
                    for viewer in self.match.players:
                        if viewer.spawned and viewer.client is not None:
                            viewer.client.queue_bot_live(bot.entity, snapshot)
            except Exception:
                self.clear_pins(bot.entity)
                script.retire_controller()
                script.retire()
                bot.body_script = None
                log.exception("[game] %s: %s's weapon script failed", self.match.label(), bot.name)

    def _eye(self, player) -> tuple:
        if isinstance(player, bots.Bot):
            x, y, z = player.position
            height = (player.combat_geometry[2] if getattr(player, "combat_geometry", None) else
                      EYE if player.control_mover is None else eye_height(player.body, player.control_mover.crouched))
            return x, y + height, z
        x, y, z = player.mover.position
        crouched = player.mover.crouched
        height = eye_height(player.hero.body, crouched)
        return (x, y + height, z)

    def damage_scale(self, shooter) -> float:
        boost = self._damage_boost(shooter)
        return boost[1] if boost is not None else 1.0


    def _damage_boost(self, shooter, *, frame=None) -> tuple[BodyScript, float] | None:
        """Capture the largest bonus and its exact source script at attack creation."""
        return self._best_boost(self._boost_candidates(shooter, frame))


    def _boost_candidates(self, shooter, frame):
        if self.match.game_map.free_for_all:
            return
        entity = _source_entity(shooter)
        for healer in self.match.players:
            script = healer.body_script
            if healer is shooter or healer.team != shooter.team or script is None or script.retired:
                continue
            if self.is_dead(healer):
                recorded = self.support_effects.historical_boosts(script.entity, frame)
                if recorded is not None:
                    yield script, recorded.get(entity, 0.0)
            else:
                yield script, support.boost_amount(script, entity, frame=frame)


    @staticmethod
    def _best_boost(candidates):
        winner = max(candidates, key=lambda item: item[1], default=None)
        if winner is None or winner[1] <= 0:
            return None
        source, bonus = winner
        return source, 1.0 + bonus


    def _awaited_boost_sources(self, shooter, frame):
        if self.match.game_map.free_for_all or not self.tick - HISTORY <= frame < self.tick:
            return ()
        waiting = []
        for healer in self.match.players:
            script = healer.body_script
            if (healer is shooter or healer.team != shooter.team or healer.gone
                    or script is None or script.hero != regeneration.MERCY
                    or script.retired or script.frame >= frame or self.is_dead(healer)):
                continue
            retained = getattr(healer, "valkyrie_pose", None)
            if ((retained is not None and (frame + 1) * script.frame_ms - 1 < retained.expires_ms)
                    or self.support_effects.historical_boosts(script.entity, frame) is not None):
                waiting.append(script)
        return tuple(waiting)


    def _submit_attack(self, shooter, frame, payload):
        if not isinstance(shooter, bots.Bot) and (source := shooter.body_script) is not None:
            view = frame if isinstance(payload, Rocket) else min(frame, math.floor(payload.view_tick))
            deadline = view + HISTORY - 1
            waiting = self._awaited_boost_sources(shooter, frame) if self.tick < deadline else ()
            if self.tick == frame and self.tick < deadline and not isinstance(payload, Rocket):
                waiting += self.sound_barriers.awaiting_slam_scripts(shooter, frame)
            if waiting or any(attack.source is source for attack in self.pending_attacks):
                bonuses = tuple(self._boost_candidates(shooter, frame))
                self.pending_attacks.append(PendingAttack(shooter, source, frame, deadline, waiting, bonuses, payload))
                return
        self._commit_attack(shooter, frame, payload, self._damage_boost(shooter, frame=frame))


    def _flush_pending_attacks(self):
        if not self.pending_attacks:
            return
        pending, self.pending_attacks = self.pending_attacks, []
        blocked = set()
        for attack in pending:
            source = attack.source
            if attack.shooter.gone or attack.shooter.body_script is not source or source.retired:
                continue
            if (source in blocked or (self.tick < attack.deadline and any(
                not script.retired and not script.component.owner_dead and script.frame < attack.frame
                for script in attack.waiting
            ))):
                blocked.add(source)
                self.pending_attacks.append(attack)
                continue
            try:
                candidates = []
                for caster, bonus in attack.bonuses:
                    if caster in attack.waiting and not caster.retired:
                        recorded = self.support_effects.historical_boosts(caster.entity, attack.frame, pending=True)
                        if recorded is not None:
                            bonus = recorded.get(source.entity, 0.0)
                        elif caster.component.owner_dead:
                            # The attack was waiting for a frame this caster never simulated.
                            bonus = 0.0
                    candidates.append((caster, bonus))
                self._commit_attack(attack.shooter, attack.frame, attack.payload, self._best_boost(candidates))
            except Exception:
                log.exception("[game] %s: %s's pending attack failed", self.match.label(), attack.shooter.name)


    def configure_volley_geometry(self, *, reconstructed_patterns: bool) -> None:
        """Explicit API opt-in; native spread units, phase and muzzle are unproven."""
        if type(reconstructed_patterns) is not bool:
            raise ValueError("reconstructed pattern geometry needs an explicit bool")
        self.reconstructed_volley_patterns = reconstructed_patterns

    def configure_dragonstrike(self, profile: dragon.Profile) -> None:
        """Mount the delivered explicit external reconstructed Dragon profile."""
        if not isinstance(profile, dragon.Profile) or self.rockets or self.dragons:
            raise ValueError("Dragon profile must be mounted before active projectiles")
        self.dragon_profile = profile

    def configure_sonic(self, profile: sonic.Profile) -> None:
        """Explicitly mount the delivered external reconstructed Sonic profile."""
        if not isinstance(profile, sonic.Profile) or self.rockets or self.sonars:
            raise ValueError("Sonic profile must be mounted before active projectiles")
        self.sonic_profile = profile

    def grapple_cast(self, state):
        from ow174.game import widowmaker

        return widowmaker.grapple_cast(self, state)

    def revealed_to(self, viewer, target) -> bool:
        """Read current team-scoped reconstructed reveal state."""
        from ow174.game import widowmaker

        entity = target if isinstance(target, int) else _source_entity(target)
        return (any(sonic.visible(beacon, viewer, entity, self.match.game_map.free_for_all)
                    for beacon in self.sonars) or widowmaker.revealed_to(self, viewer, entity))

    def _commit_attack(self, shooter, frame, payload, boost):
        scale = boost[1] if boost is not None else 1.0
        if isinstance(payload, Rocket):
            for name in ("direct", "blast"):
                definition = getattr(payload, name)
                if definition is not None and not definition.healing:
                    setattr(payload, name, replace(definition, amount=definition.amount * scale, boost_credit=boost))
            payload.boost_credit = (boost if any(definition is not None and not definition.healing
                                                for definition in (payload.direct, payload.blast)) else None)
            self.rockets.append(payload)
            self.orisa.admit(payload)
        elif isinstance(payload, MeleeContact):
            self._resolve_melee(shooter, frame, payload, payload.hit.amount * scale, boost)
        else:
            hit = payload.hit
            if not hit.healing:
                hit = replace(hit, amount=hit.amount * scale, boost_credit=boost)
            targets = tuple((target, shown) for target in self._shot_targets(shooter, hit)
                            if (shown := self.seen(target, payload.view_tick)) is not None)
            hits = {}
            for direction in payload.coach_rays or (payload.direction,):
                resolved = self._hitscan(shooter, payload.state, payload.origin, direction, payload.reach,
                                        targets, hit, frame, payload.world_tick, view_tick=payload.view_tick,
                                        definition=payload.definition, reaper_shell=payload.reaper_shell)
                if payload.coach_rays is not None and resolved is not None:
                    source, target, distance = resolved
                    source_hits = hits.setdefault(_source_entity(source), (source, {}))[1]
                    previous = source_hits.get(target.entity)
                    source_hits[target.entity] = (target, distance, 1) if previous is None else (
                        target, min(previous[1], distance), previous[2] + 1)
            for source, source_hits in hits.values():
                self.impulses.coach_targets(source, source_hits, payload.state, reflected=source is not shooter)

    def _support_hit(self, shooter, definition):
        if definition is None or definition.healing or self.match.game_map.free_for_all:
            return definition
        boost = self._damage_boost(shooter)
        return replace(definition, amount=definition.amount * boost[1], boost_credit=boost) if boost else definition

    def _shoot(
        self, shooter, command, state, number: int, time: int,
        *, activation: tuple[int, int], snapshot: Snapshot | None = None,
        viper_launch: tuple[float, int] | None = None, launch_tick: int | None = None, command_quanta=None,
    ) -> None:
        if (not isinstance(shooter, bots.Bot) and command is not None
                and self.sentries.fired(shooter, command, state)):
            return
        node = state.node
        shot_frame = time // state.instance.component.frame_ms
        if not isinstance(shooter, bots.Bot) and self.ball_mines.volley(shooter, state, number, shot_frame):
            return
        if (not isinstance(shooter, bots.Bot) and shooter.hero.guid == 0x02E000000000013B
                and state.instance.graph.index == 0x0B9E and state.index == 1):
            from ow174.game import nano
            target = state.instance.find_var(expr.INSTANCE, 8070, create=False)
            duration = state.instance.find_var(expr.INSTANCE, 215, create=False)
            nano.apply(self, _source_entity(shooter), target.value() if target is not None else (),
                       _number(duration.value(), 0) if duration is not None else 0)
            return

        def field(name, default=None):
            cfg = node.field(name)
            return default if cfg is None else _number(state.evaluate(cfg, watch=False), default)

        npc = isinstance(shooter, bots.Bot)
        if npc:
            x, y, z = shooter.position
            # Use the server body pose until the animated muzzle transform is available.
            height = shooter.combat_geometry[2] if getattr(shooter, "combat_geometry", None) else EYE
            origin, direction = (x, y + height, z), aim(round(shooter.yaw), round(shooter.pitch))
        else:
            origin, direction = self._eye(shooter), aim(*command.aim())
            if (shooter.hero.guid == 0x02E0000000000008
                    and (state.instance.graph.index, state.index) in ((0x23, 6), (0x7B, 0), (0x2B, 1))):
                x, y, z = shooter.mover.position
                height = expr.f32(eye_height(shooter.hero.body, shooter.mover.crouched))
                origin = (x, y + expr.f32(height + shooter.mover.spring), z)
            direction = self._copied_visor_direction(shooter, command, state, origin, direction)
            from ow174.game import mccree

            direction = mccree.fan_direction(shooter, state, direction, number, time, activation)
            direction = sombra_spread.direction(shooter, state, direction, number, time, activation)
            direction = ball.cannon_direction(shooter, state, direction, number, time, activation)
            if (viper_launch is not None and shooter.hero.guid == 0x02E0000000000200
                    and state.instance.component is shooter.body_script.component):
                direction = ashe.viper_direction(direction, number, viper_launch)
        if not npc and self.blizzard.fired(shooter, state, shot_frame, origin, direction,
                                          activation=activation, snapshot=snapshot):
            return
        if not npc and state.instance.graph.index == 0x01EE:
            from ow174.game import mccree

            directed = mccree.shot_aim(self, shooter, state)
            if directed is None:
                return
            origin, direction = directed
        if (not npc and state.spread_ramp is not None
                and ((state.instance.graph.index == 0xBD4 and node.pos == 50)
                     or (shooter.hero.guid == BASTION
                         and (state.instance.graph.index, node.pos) in ((0x012E, 30), (0x013A, 26))))):
            from ow174.game import weapon_spread

            launch = node.field("m_5EC1EB38")
            low, high = (expr.to_float(state.evaluate(launch[name], watch=False))
                         for name in ("m_7F0FED02", "m_429A6C85"))
            # AE71E0 passes the emitted shot index, then AE73F2 increments it.
            normalized = state.spread_for_index(number)
            degrees = expr.f32(expr.f32(expr.f32(high - low) * normalized) + expr.f32(low))
            cone = expr.f32(degrees * expr.f32(math.pi / 180))
            # Declared server identity and uniform-radius sampling policy.
            base = weapon_spread.seed_base(shooter.body, state.instance.id, node.pos, activation[0])
            direction = weapon_spread.direction(direction, cone, base, number)
        if not npc and shooter.hero.guid == 0x02E000000000000A:
            from ow174.game import weapon_launch

            direction, = weapon_launch.directions(shooter, state, direction, number, time, activation)
        direct = hit_def(state, node.field("m_6396149F"))
        if (not npc and shooter.hero.guid == BASTION and direct is not None
                and (state.instance.graph.index, node.pos) == (0x012E, 30)
                and (node.field("m_6396149F") or {}).get("m_A872C70E") is None):
            direct = replace(direct, critical=2.0)
        if (not npc and shooter.hero.guid == 0x02E0000000000200 and direct is not None
                and state.instance.graph.index == 0x14FC
                and (node.field("m_6396149F") or {}).get("m_A872C70E") is None):
            direct = replace(direct, critical=2.0)
        if direct is not None:
            reaper.shotgun_critical(shooter, state, direct)
        from ow174.game import dva

        dva_critical = dva.direct_hit_critical(shooter, state)
        if direct is not None and dva_critical is not None:
            direct = replace(direct, critical=dva_critical,
                             projectile_headshot=state.instance.graph.index == dva.LIGHT_GUN)
        charge_context = None
        if (not npc and direct is not None and direct.loadout == 0x0B9000000000003E
                and shooter.hero.guid == regeneration.MERCY
                and state.instance.graph.index == 0x006D and node.pos == 10):
            from ow174.game.script import charge

            hit = node.field("m_6396149F")
            if (hit.get("m_A872C70E") is None
                    and expr.to_int(state.evaluate(hit.get("m_66D29548"), watch=False)) == 2):
                # Delivered mode-2 policy for this volley; native animated
                # head-hitbox/default multiplier parity remains unverified.
                direct = replace(direct, critical=2.0, projectile_headshot=True)
            charge_context = charge.damage_context(shooter.body_script, state, hit.get("m_modifyHealth") or {})

        entity_def = (node.field("m_projectileEntity") or {}).get("m_entityDef")
        asset = state.evaluate(entity_def, watch=False) if entity_def is not None else None
        definition = asset.guid if isinstance(asset, expr.Asset) else None
        hanzo_bow = (not npc and shooter.hero.guid == 0x02E0000000000005
                     and state.instance.graph.index == 0x00B9
                     and (node.pos, definition) in (
                         (5, 0x0400000000000144), (5, 0x040000000000020B), (5, 0x040000000000270F),
                         (114, 0x0400000000000149), (114, 0x040000000000020D)))
        if (hanzo_bow and direct is not None
                and (node.field("m_6396149F") or {}).get("m_A872C70E") is None):
            direct = replace(direct, critical=2.0, projectile_headshot=True)
        storm_arrow = hanzo_bow and (definition == 0x040000000000270F or (
            node.pos == 114 and (enabled := state.instance.find_var(expr.INSTANCE, 18944)) is not None
            and enabled.value() is True
        ))
        dragon_initial = (not npc and shooter.hero.guid == dragon.HANZO
                          and definition == dragon.INITIAL)
        continuation = self.dragon_profile if dragon_initial else None
        field_canister = (not npc and state.instance.graph.index == 0x0257 and state.index == 1
                          and definition == 0x040000000000072D)
        rebound_limit, rebound_scale = None, 0.0
        if field_canister:
            response = node.field("m_E365DB20") or {}
            if state.evaluate(response.get("m_D9EFD182"), watch=False):
                rebound_limit = int(_number(state.evaluate(response.get("m_360294F0"), watch=False)))
                rebound_scale = _number(state.evaluate(response.get("m_0D20DAFD"), watch=False))
            offset = state.evaluate(node.field("m_5EC1EB38.m_1C78B91C"), watch=False)
            launch = state.evaluate(node.field("m_5EC1EB38.m_E2FD6F04"), watch=False)
            if isinstance(offset, expr.Vec3) and isinstance(launch, expr.Vec3):
                length = math.hypot(launch.x, launch.y, launch.z)
                if length:
                    x, y, z = shooter.mover.position
                    forward_x, _, forward_z = aim(round(shooter.mover.yaw), 0)
                    origin = (x + offset.x * forward_z + offset.z * forward_x, y + offset.y,
                              z - offset.x * forward_x + offset.z * forward_z)
                    direction = (launch.x / length, launch.y / length, launch.z / length)
        from ow174.game import pulse_bomb

        pulse = not npc and pulse_bomb.is_pulse(shooter, state)
        speed = field("m_projectileSpeed")
        if not npc and shooter.hero.guid == 0x02E0000000000200 and (state.instance.graph.index, state.index) == (0x14FD, 12):
            fuse = ashe.dynamite_fuse_seconds(state)
            delivery = self.projectiles.fired(shooter, state, shot_frame, definition, fuse,
                                               origin, direction, activation=activation, snapshot=snapshot) if definition else None
            self.dynamite.fired(shooter, origin, direction, speed or 25.0, delivery,
                                fuse_seconds=fuse, absorb_damage=direct.amount if direct is not None else 0.0)
            return
        explicit_directions = None
        actor_hero = getattr(getattr(shooter, "hero", None), "guid", getattr(shooter, "hero", None))
        if self.reconstructed_volley_patterns and actor_hero != dva.HERO:
            from ow174.game import volley_geometry

            try:
                explicit_directions = volley_geometry.directions(state, direction, reconstructed_patterns=True)
                if speed and len(explicit_directions) > 1 and not npc:
                    raise volley_geometry.UnsupportedGeometry("multiple player projectile leases are not implemented")
            except volley_geometry.UnsupportedGeometry as error:
                expr.warn_once(f"volley-geometry:{state.instance.graph.index}:{node.pos}:{error}",
                               "volley geometry refuses graph%04X/node%d: %s",
                               state.instance.graph.index, node.pos, error)
                return
            if not explicit_directions:
                return
        from ow174.game import junkrat_projectile

        junkrat_profile = junkrat_projectile.profile(state) if not npc else None
        if speed:
            flashbang_gravity = None if npc else mccree.flashbang_gravity(shooter, state)
            gravity = flashbang_gravity
            if hanzo_bow:
                gravity = sum(_number(state.evaluate(motion.get("m_599AB8D7"), watch=False), 0.0)
                              for motion in node.field("m_projectileMotions") or ()
                              if motion.get("$") == "STU_E1A5B579"
                              and not expr.truthy(state.evaluate(motion.get("m_D0C4CF49"), watch=False)))
            if field_canister:
                gravity = projectiles.unconditional_gravity(state)
            if flashbang_gravity is not None:
                direction = mccree.flashbang_direction(state, direction)
            if npc and shooter.hero == 0x02E0000000000177 and state.instance.graph.index == 0x0CA1 and node.pos == 14:
                body = self._scene_component(shooter) or getattr(shooter.body_script, 'component', None)
                if state.instance.component is body:
                    gravity = projectiles.unconditional_gravity(state)
            if (npc and shooter.hero == 0x02E0000000000065 and state.instance.graph.index == 0x022B
                    and node.pos == 31 and self._scene_component(shooter) is state.instance.component):
                gravity = projectiles.unconditional_gravity(state)
            if junkrat_profile is not None:
                gravity = junkrat_profile[0]
                if direct is not None:
                    direct = replace(direct, projectile_extent=junkrat_profile[1])
            lifetime = field("m_projectileLifetime", 5.0) or 5.0
            reach = field("m_FEC435C6")
            blast = hit_def(state, node.field("m_D2D11CE9"))
            if not npc and shooter.hero.guid == junkrat_projectile.HERO:
                from ow174.game import junkrat

                if definition == junkrat.MINE:
                    self.junkrat.cast(shooter, state, origin, direction, speed, lifetime,
                                      sum(max(0.0, _number(state.evaluate(m.get("m_599AB8D7"), watch=False), 0.0))
                                          for m in node.field("m_projectileMotions") or ()
                                          if m.get("$") == "STU_E1A5B579"), blast,
                                      max_distance=reach if reach and reach > 0 else None, launch_tick=self.tick)
                    return
                if definition == junkrat.TRAP_PROJECTILE:
                    self.junkrat.cast_trap(shooter, state, origin, direction, speed, lifetime,
                                          sum(max(0.0, _number(state.evaluate(m.get("m_599AB8D7"), watch=False), 0.0))
                                              for m in node.field("m_projectileMotions") or ()
                                              if m.get("$") == "STU_E1A5B579"),
                                          max_distance=reach if reach and reach > 0 else None, launch_tick=self.tick)
                    return

            if not npc and self.translocators.fire(shooter, state, definition, origin, direction, activation):
                return

            biotic_grenade = (state.instance.graph.index == 0x0B97 and blast is not None
                              and blast.effect == BIOTIC_GRENADE_EFFECT)
            if biotic_grenade:
                gravity = projectiles.unconditional_gravity(state)
            field_spec = self.baptiste.field_parameters(state)
            grenade = self.baptiste.grenade_parameters(state)
            if field_spec is not None:
                gravity = field_spec.gravity
            if grenade is not None:
                direction = baptiste.grenade_direction(state, direction)
                radius, amount, gravity = grenade
                blast = HitDef(amount, direct.loadout if direct else None, rings=((radius, 1.0),),
                               healing=True, line_of_sight=True,
                               tags=direct.tags if direct else (), health_operation=0)
            range_left = reach if field_spec is not None or grenade is not None or hanzo_bow or continuation is not None else None
            barrage = (not npc and shooter.hero.guid == 0x02E0000000000008
                       and (state.instance.graph.index, state.index) == (barrage_motion.GRAPH, barrage_motion.STATE))
            curve_motion = ()
            if barrage:
                direction, curve_motion = barrage_motion.launch(state, activation[0], number, direction)
            if reach and range_left is None and not field_canister and not barrage and junkrat_profile is None and not (hanzo_bow and gravity):
                lifetime = min(lifetime, reach / speed)
            fire_strike = (definition == 0x04000000000001EA
                           and state.instance.graph.index == 0x00E2 and node.pos == 3
                           and getattr(shooter.hero, "guid", None) == barrier.REINHARDT)
            penetration_radius = 0.0
            if fire_strike:
                hit_fields = node.field("m_6396149F") or {}
                penetration_radius = max(_number(state.evaluate(hit_fields.get(name), watch=False), 0.0)
                                         for name in ("m_57DAE1B5", "m_61CD1F5B"))
            accretion_source, velocity = None, None
            if state.instance.graph.index == ACCRETION and node.pos == 9 and not npc:
                accretion_source = shooter.body, state.instance.id
                direction, velocity, gravity = _accretion_launch(state, direction, speed)
            deployment = (
                not npc and self.orisa_barriers.geometry is not None
                and state.instance.graph.index == orisa_barrier.GRAPH
                and orisa_barrier.DEFINITION in projectiles._defs(node.field("m_49CBA3BC"))
            )
            if deployment:
                gravity = sum(
                    _number(state.evaluate(motion.get("m_599AB8D7"), watch=False), 0.0)
                    for motion in node.field("m_projectileMotions") or ()
                    if motion.get("$") == "STU_E1A5B579"
                    and not expr.truthy(state.evaluate(motion.get("m_D0C4CF49"), watch=False))
                )
                velocity = tuple(expr.f32(axis * speed) for axis in direction)
            sticky = (state.instance.graph.index, state.index) == (0x1C36, 0)
            if pulse:
                direction = pulse_bomb.launch_direction(state, direction)
                gravity = projectiles.unconditional_gravity(state)
                velocity = tuple(axis * speed for axis in direction)
                if blast is not None:
                    blast = replace(blast, self_scale=1.0)
            if sticky:
                for motion in node.field("m_projectileMotions") or ():
                    if motion.get("$") == "STU_E1A5B579":
                        gravity = _number(state.evaluate(motion.get("m_599AB8D7"), watch=False), 0.0)
            directions = (
                genji_fan(state, direction) if state.instance.graph.index == 0x0304
                else projectile_directions(state, direction, number, activation)
            )
            if getattr(getattr(shooter, "hero", None), "guid", getattr(shooter, "hero", None)) == dva.HERO:
                directions = _volley_directions(shooter, state, direction, number=number, counter=activation[0])
            target_limit = 1
            if direct is not None:
                if definition == MEI_SPRAY_ENTITY:
                    direct.deflect_override = False
                    target_limit = max(1, min(99, int(field("m_6396149F.m_B4454CD7", 1))))
                elif definition == SLEEP_DART_ENTITY:
                    direct.deflect_override = True
            shot = None
            individual_npc_shots = (npc and explicit_directions is not None
                                    and len(explicit_directions) > 1 and definition is not None)
            if definition is not None and not individual_npc_shots:
                shot = self.projectiles.fired(
                    shooter, state, shot_frame, definition, lifetime, origin, direction,
                    activation=activation, snapshot=snapshot,
                )
                if moira.orb_fired(self, shooter, state, definition, speed, lifetime,
                                   origin, direction, shot):
                    return
            if not npc and self.venom.throw(shooter, state, origin, direction, shot):
                return
            damaging = (direct is not None and direct.amount > 0.0) or (
                blast is not None and blast.amount > 0.0
            )
            source_graph = state.instance.graph.index
            server_ability = source_graph in (0x0CCB, 0x0BD7, 0x0CF5) and definition is not None
            spawned = (node.field("m_49CBA3BC") or {}).get("m_entityDefinition") if server_ability else None
            spawned = state.evaluate(spawned, watch=False) if spawned is not None else None
            spawn = spawned.guid if isinstance(spawned, expr.Asset) else None
            if (not npc and shooter.hero.guid == hero_torbjorn.HERO and source_graph == 0x16C5
                    and hero_torbjorn.TURRET in projectiles._defs(node.field("m_49CBA3BC"))):
                spawn = hero_torbjorn.TURRET
                gravity = sum(_number(state.evaluate(motion.get("m_599AB8D7"), watch=False), 0.0)
                              for motion in node.field("m_projectileMotions") or ()
                              if motion.get("$") == "STU_E1A5B579")
            if server_ability and source_graph == 0x0CF5 and gravity is None:
                gravity = sum(
                    _number(state.evaluate(motion.get("m_599AB8D7"), watch=False), 0.0)
                    for motion in node.field("m_projectileMotions") or ()
                    if motion.get("$") == "STU_E1A5B579"
                )
            if source_graph == 0x16DA and molten_core.POOL in projectiles._defs(node.field("m_49CBA3BC")):
                spawn = molten_core.POOL
                gravity = sum(_number(state.evaluate(motion.get("m_599AB8D7"), watch=False), 0.0)
                              for motion in node.field("m_projectileMotions") or ()
                              if motion.get("$") == "STU_E1A5B579")
            if (source_graph == 0x025F
                    and graviton.FIELD_ENTITY in projectiles._defs(node.field("m_49CBA3BC"))):
                spawn = graviton.FIELD_ENTITY
                gravity = projectiles.unconditional_gravity(state)
            pharah_impulse = (not npc and shooter.hero.guid == 0x02E0000000000008
                              and blast is not None and bool(blast.impulse_rings))
            if damaging or shot is not None or deployment or server_ability or field_spec is not None or spawn is not None or pharah_impulse:
                split = None
                if (npc and shooter.hero == 0x02E0000000000040 and state.instance.graph.index == 0x01C6
                        and node.pos in (26, 29) and self._scene_component(shooter) is state.instance.component):
                    from ow174.game import weapon_spread

                    seed = weapon_spread.seed_base(shooter.entity, state.instance.id, node.pos, activation[0])
                    if node.pos == 26:
                        directions = weapon_spread.authored_ring_directions(state, direction, seed, number)
                    else:
                        split = self._split_volley(state, direction, seed, number)
                elif npc and shooter.hero != dva.HERO:
                    from ow174.game import npc_context

                    directions = npc_context.enforcer_directions(shooter, state, direction, number, activation)
                    if len(directions) == 1:
                        directions = ring_directions(state, direction) or directions
                if (not npc and junkrat_profile is not None and state.instance.graph.index == 0x022A):
                    from ow174.game import launch_ring

                    directions = launch_ring.projectile_directions(state, direction, number, activation)
                    if directions is None:
                        raise ValueError("unsupported authored Total Mayhem launch")
                elif explicit_directions is not None:
                    directions = explicit_directions
                dose_scalar = None
                if direct is not None and direct.duration > 0.0:
                    from ow174.game import nano

                    dose_scalar = nano.offense(self, _source_entity(shooter)) * self.orisa.superchargers.scale(shooter)
                projectile_tick = None
                if npc:
                    projectile_tick = self.tick if launch_tick is None else launch_tick
                elif junkrat_profile is not None:
                    projectile_tick = self.tick
                elif biotic_grenade and command is not None:
                    projectile_tick = max(self.tick, command.frame)
                elif (getattr(shooter.hero, "guid", None) == 0x02E0000000000020
                      and state.instance.graph.index == 0x0164
                      and command is not None and command.frame == self.tick):
                    projectile_tick = self.tick
                for pellet, launch in enumerate(directions):
                    pellet_shot = (self.projectiles.fired(
                        shooter, state, shot_frame, definition, lifetime, origin, launch,
                        activation=activation, snapshot=snapshot,
                    ) if individual_npc_shots else shot if pellet == 0 else None)
                    model = pulse_bomb.Bomb if pulse else Rocket
                    rocket = model(
                        shooter, shooter.team, origin, launch, speed, lifetime, direct, blast,
                        pellet_shot,
                        last_tick=projectile_tick,
                        gravity=gravity or 0.0,
                        velocity=(velocity if velocity is not None else
                                  tuple(expr.f32(value * speed) for value in launch) if gravity is not None else None),
                        spray=moira.spray_trace(shooter, state, direct), dose_scalar=dose_scalar,
                        definition=definition, accretion_source=accretion_source,
                        deploy_body=shooter.body if deployment else None,
                        detonate_on_expiry=flashbang_gravity is not None or pulse or junkrat_profile is not None,
                        scale_direct=len(directions) > 1 or bool(direct and direct.projectile_headshot),
                        split_on_expiry=split, sticky_delay=(
                            field("m_55E8761C.m_F969FA7A", 1.0) if pulse else 1.0 if sticky else None),
                        source_body=_source_entity(shooter) if server_ability or not npc else None,
                        damage_charge=charge_context,
                        charge_eligible=not (barrage or (not npc and shooter.hero.guid == BASTION
                                             and source_graph == 0x024F)),
                        motion=curve_motion, deflect_lifetime=lifetime if barrage else None,
                        barrage_source=(shooter, state, number) if barrage else None,
                        matrix_absorbable=(definition in self.matrix_definitions
                                           if self.matrix_definitions is not None
                                           else projectile_tags.absorbable(definition)),
                        terrain_bounce=junkrat_profile[2] if junkrat_profile is not None else None,
                        terrain_bounce_limit=junkrat_profile[3] if junkrat_profile is not None else 0,
                        storm_ricochets=int(storm_arrow),
                        continuation=continuation,
                        started_tick=(self.tick + time % state.instance.component.frame_ms
                                      / state.instance.component.frame_ms if hanzo_bow or continuation is not None else None),
                        sonar=(self.sonic_profile if not npc and shooter.hero.guid == sonic.HANZO
                               and state.instance.graph.index == 0x00B9 and definition in sonic.DEFINITIONS else None),
                        boost_credit=(direct.boost_credit if direct is not None else
                                      blast.boost_credit if blast is not None else None),
                        target_limit=continuation.initial_hit_limit if continuation is not None else target_limit,
                        spawn=spawn, source_graph=source_graph, source_state=state if server_ability or not npc else None,
                        launch_direction=launch, collide_bodies=source_graph not in (0x0CCB, 0x0BD7, 0x0CF5),
                        deployment=field_spec, range_left=range_left, penetration_radius=penetration_radius,
                        penetrated=set() if fire_strike else None,
                        range_limit=reach if field_canister or barrage or hanzo_bow or continuation is not None or junkrat_profile is not None else None,
                        rebound_limit=rebound_limit if field_canister else None,
                        rebound_scale=rebound_scale,
                    )
                    if field_canister and rocket.shot is not None:
                        rocket.shot.field_airborne = True
                        release_tick = self.tick if launch_tick is None else launch_tick
                        if release_tick:
                            rocket.stamp_launch(release_tick)
                    self._submit_attack(shooter, shot_frame, rocket)
            return
        if direct is None or direct.amount <= 0.0:
            return
        reach = field("m_FEC435C6", RANGE) or RANGE
        if (not npc and state.instance.graph.index == 0x1932 and node.pos == 10
                and shooter.hero.guid == baptiste.HERO):
            direction = baptiste.primary_direction(state, direction, shooter.body, activation[0], number)
        # The input supplies this simulation frame's aim and view time. Notices use the volley clock.
        view_tick = self.tick if launch_tick is None else launch_tick
        if command is not None:
            quanta = shooter.quanta if command_quanta is None else command_quanta
            view_tick = command.frame - command.view_delay(quanta) * 1e6 / quanta
            if not npc and shooter.hero.guid == 0x02E000000000006E:
                direction = self._soldier_visor_direction(shooter, state, origin, direction, view_tick)
        directions = ring_directions(state, direction) or (direction,)
        if reaper.is_reaper(shooter) and state.instance.graph.index == reaper.SHOTGUN_GRAPH:
            directions = reaper.shotgun_directions(shooter, state, direction)
        if getattr(getattr(shooter, "hero", None), "guid", getattr(shooter, "hero", None)) == dva.HERO:
            directions = _volley_directions(shooter, state, direction, number=number, counter=activation[0])
        if not npc and shooter.hero.guid == 0x02E0000000000003:
            from ow174.game import tracer_pistol

            if tracer_pistol.active(state):
                count = int(field("m_numProjectilesPerShot", 1))
                if not 1 <= count <= 64:
                    raise ValueError("unsupported Tracer bullet count")
                directions = tracer_pistol.rays(state, number, time, activation, direction, count)
        if (npc and shooter.hero == 0x02E0000000000002 and state.instance.graph.index == 0x0019
                and node.pos == 10 and self._scene_component(shooter) is state.instance.component):
            from ow174.game import weapon_spread

            seed = weapon_spread.seed_base(shooter.entity, state.instance.id, node.pos, activation[0])
            directions = weapon_spread.authored_ring_directions(state, direction, seed, number)
        if explicit_directions is not None:
            directions = explicit_directions
        reaper_shell = None
        if reaper.is_reaper(shooter) and state.instance.graph.index == reaper.SHOTGUN_GRAPH:
            from ow174.game import nano

            reaper_shell = {"amount": direct.amount, "nano_bonus": nano.offense(self, shooter.body) - 1.0,
                            "returns": {}}
        if not npc and shooter.hero.guid == 0x02E0000000000200 and (state.instance.graph.index, state.index) == (0x1513, 16):
            self._submit_attack(shooter, shot_frame, RangedContact(
                direct, origin, direction, reach, view_tick, charge_context, definition, state,
                None, None, ashe.coach_directions(state, direction),
            ))
            return
        for pellet in directions:
            self._submit_attack(shooter, shot_frame, RangedContact(
                direct, origin, pellet, reach, view_tick, charge_context, definition, state,
                view_tick if npc else None, reaper_shell,
            ))

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
        return weapon_spread.jittered_ring_directions(forward, rings, spread, jitter, seed, shot)

    def _deflector(self, shooter, definition, origin, direction, reach, *, view_tick=None, time_span=None,
                   radius=0.0, absorption_admitted=None):
        admitted = self.projectile_tags.admitted(definition)
        absorbable = (absorption_admitted if absorption_admitted is not None else
                      definition in self.matrix_definitions if self.matrix_definitions is not None else admitted)
        if not admitted and not absorbable:
            return None
        closest = None
        for target in self._enemies(shooter):
            owner = target.owner
            script = getattr(owner, 'body_script', None)
            identity = getattr(owner, "hero", None)
            hero = getattr(identity, "guid", None)
            if absorbable and getattr(identity, "guid", identity) == 0x02E000000000007A:
                from ow174.game import matrix as matrix_service

                shown = self.seen(target, view_tick) if view_tick is not None else target
                distance = None if shown is None else matrix_service.trace(shown, origin, direction, reach)
                at_tick = self.tick
                if distance is not None and time_span is not None:
                    at_tick = time_span[0] + (time_span[1] - time_span[0]) * (distance / reach if reach else 0)
                if distance is not None:
                    distance = matrix_service.trace(shown, origin, direction, reach, tick=at_tick)
                if distance is not None and (closest is None or distance < closest[0]):
                    guard = next(deflection._active_states(script, 0x02CC, 3), None)
                    closest = distance, target, None, True, guard
                continue
            if (hero not in (deflection.GENJI, absorption.SIGMA)
                    or script is None or script.retired or getattr(owner, 'mover', None) is None
                    or self.is_stunned(owner)):
                continue
            if not (admitted if hero == deflection.GENJI else absorbable):
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

    def _hitscan(self, shooter, state, origin, direction, reach, targets, direct, shot_frame, world_tick,
                 *, view_tick=None, reflected=frozenset(), definition=None, travelled=0.0, matrix_factor=1.0, bounces=0,
                 reaper_shell=None):
        best = None
        for target, shown in targets:
            if self._has_protection(target, PHASE_STATES) or immunity.wraith_active(target.owner, target.entity):
                continue
            a, b = shown.segment()
            t = ray_capsule(origin, direction, a, b, shown.radius)
            if shown.sentry_yaw is not None:
                core = ray_capsule(origin, direction, *shown.core_segment(), SENTRY_CORE_RADIUS)
                if core is not None and (t is None or core < t):
                    t = core
            if shown.head_offset is not None:
                centre = _along(shown.feet, shown.head_offset, 1.0)
                head = ray_capsule(origin, direction, centre, centre, TRACER_HEAD_RADIUS)
                if head is not None and head <= reach:
                    t = head
            if t is not None and t <= reach and (best is None or t < best[0]):
                best = (t, target, shown)
        limit = min(reach, best[0] if best else reach)
        mine_hit = self.venom.intersect(shooter, origin, direction, limit)
        if mine_hit is not None:
            limit = mine_hit[0]
        grenade_hit = self.dynamite.ray_hit(shooter, origin, direction, limit)
        if grenade_hit is not None:
            limit = grenade_hit[0]
        redirect = None
        tagged = state.instance.graph.index == 0x1932 and self.projectile_tags.blob is not None
        tagged_contact = (self._deflector(shooter, definition, origin, direction, limit, view_tick=view_tick)
                          if tagged else None)
        if tagged_contact is not None:
            limit = tagged_contact[0]
            if not tagged_contact[3]:
                redirect = limit, tagged_contact[1]
        if direct.loadout == 0x0B9000000000008D:
            redirect = self._trace_deflection(shooter, origin, direction, limit, view_tick=view_tick)
            if redirect is not None:
                limit = redirect[0]
        for target, shown in (() if tagged else targets):
            if target.entity in reflected:
                continue
            distance = self._genji_deflect_contact(shown, direct, origin, direction, limit)
            if distance is not None and (redirect is None or distance < limit):
                redirect = distance, target
                limit = distance
        absorption = self._trace_absorption(
            shooter, definition, origin, direction, limit,
            grasp=direct.loadout == 0x0B9000000000008D, view_tick=view_tick,
        )
        if absorption is not None and absorption[0] < limit:
            limit = absorption[0]
            redirect = None
        else:
            absorption = None
        scripted_absorption = interception.absorption(self, shooter, direct, origin, direction, limit, definition=definition)
        if scripted_absorption is not None and scripted_absorption[0] < limit:
            absorption = scripted_absorption
            limit = absorption[0]
            redirect = None
        else:
            scripted_absorption = None
        wall = ray_world(self._collision(), origin, direction, limit)
        ice = self.walls.cast(origin, direction, limit if wall is None else wall)
        if ice is not None and (wall is None or ice[0] < wall):
            wall = ice[0]
        else:
            ice = None
        barrier = self.brigitte.barrier_ray(shooter, origin, direction, wall if wall is not None else limit)
        bound = barrier[0] if barrier is not None else wall if wall is not None else limit
        scene_barrier = self._barrier_hit(shooter, origin, direction, bound)
        if scene_barrier is not None:
            bound = scene_barrier[0]
        echo_shield = self.barriers.intercept(shooter, origin, direction, bound, include_authored_mesh=False)
        if echo_shield is not None and echo_shield[0] < bound:
            bound = echo_shield[0]
        else:
            echo_shield = None
        scripted_shield = barriers.ray_hit(self, shooter, origin, direction, bound)
        if scripted_shield is not None and scripted_shield[0] < bound:
            bound = scripted_shield[0]
        else:
            scripted_shield = None
        ray = Rocket(shooter, shooter.team, origin, direction, 0.0, 0.0, direct, None)
        consumed = deflections.intercept(self, ray, bound, (), modes=("absorb",))
        if consumed is not None and consumed[0] < bound:
            deflections.absorb(consumed[3], direct.amount * direct.scale(consumed[0]))
            return
        deployed = sigma_barrier.barrier_intersection(self, shooter, origin, direction, bound)
        if deployed is not None and (
            deployed[0] < bound or (scene_barrier is None and barrier is None and wall is None and best is None
                                   and redirect is None and absorption is None and echo_shield is None and scripted_shield is None)
        ):
            distance, actor = deployed
            sigma_barrier.damage_barrier(self, actor, direct.amount * direct.scale(distance))
            return
        ability = self._orisa_deployable_hit(shooter, origin, direction, bound)
        if ability is not None and (ability[0] < bound or best is None and wall is None):
            distance, actor, manager = ability
            manager.damage(actor, direct, travelled + distance, multiplier=self.orisa.superchargers.scale(shooter))
            return
        if scripted_shield is not None:
            distance, actor = scripted_shield
            barriers.damage(self, actor, direct.amount * direct.scale(travelled + distance),
                            attacker=_source_entity(shooter))
            return
        if echo_shield is not None:
            distance, actor = echo_shield
            self.barriers.damage(actor, direct.amount * direct.scale(distance), shooter)
            return
        if scene_barrier is not None:
            distance, shield = scene_barrier
            self._damage_barrier(shooter, shield, direct.amount * direct.scale(distance), direct.tags)
            return
        if barrier is not None:
            distance, shield = barrier
            self.brigitte.damage_barrier(shield, direct.amount * direct.scale(distance))
            return
        if ice is not None:
            self.walls.damage(ice[1], shooter, direct.amount * direct.scale(ice[0]))
            return
        if absorption is not None:
            if wall is None:
                amount = direct.amount * direct.scale(travelled + limit)
                if scripted_absorption is not None:
                    interception.absorbed(absorption[1], amount)
                else:
                    self._absorbed(absorption[1], amount, _source_entity(shooter))
            return
        if tagged_contact is not None and tagged_contact[3] and wall is None and limit == tagged_contact[0]:
            scaled = replace(direct, amount=direct.amount * direct.scale(travelled + limit))
            if tagged_contact[4].instance.graph.index == 0x1D02:
                self.absorption.absorbed(tagged_contact[4], scaled, None, _source_entity(shooter))
            elif tagged_contact[4].instance.graph.index == 0x02CC:
                self._absorbed(tagged_contact[4], scaled.amount, _source_entity(shooter))
            return
        if redirect is not None and wall is None:
            distance, target = redirect
            can_return = bounces < deflection.MAX_DEFLECTIONS if tagged else target.entity not in reflected and len(reflected) < 8
            if can_return and reach > distance:
                owner = target.owner
                facing = (tagged_contact[2] if tagged_contact is not None
                          else aim(owner.mover.yaw, owner.mover.pitch))
                targets = tuple((body, body) for body in self._shot_targets(owner, direct))
                contact = _along(origin, direction, distance)
                if matrix_factor == 1.0:
                    matrix_factor = self.baptiste.matrix_multiplier(shooter, origin, contact)
                returned = direct
                if reaper_shell is not None:
                    returns = reaper_shell["returns"]
                    if owner.body not in returns:
                        # Keep the launch Nano contribution; staff and Supercharger
                        # ownership are selected once per deflector for this shell.
                        base_scale = 1.0 + reaper_shell["nano_bonus"] + self.orisa.superchargers.scale(owner) - 1.0
                        staff = self._damage_boost(owner, frame=self.tick)
                        scale = base_scale + (staff[1] - 1.0 if staff is not None else 0.0)
                        credit = (staff[0], scale / base_scale) if staff is not None and base_scale > 0 else None
                        returns[owner.body] = reaper_shell["amount"] * scale, credit
                    amount, credit = returns[owner.body]
                    returned = replace(direct, amount=amount, boost_credit=credit)
                return self._hitscan(
                    owner, state, _along(contact, facing, 1e-4), facing, reach - distance, targets, returned,
                    shot_frame, self.tick, view_tick=self.tick, reflected=reflected | {target.entity},
                    definition=definition, travelled=travelled + distance,
                    matrix_factor=matrix_factor, bounces=bounces + 1, reaper_shell=reaper_shell,
                )
            return
        if grenade_hit is not None and (wall is None or grenade_hit[0] < wall):
            self.dynamite.detonate(grenade_hit[1])
            return
        if mine_hit is not None and (wall is None or mine_hit[0] < wall):
            distance, mine = mine_hit
            self.venom.damage(mine, direct.amount * direct.scale(travelled + distance))
            return
        if best is None or (wall is not None and wall < best[0]):
            return
        t, target, shown = best
        factor = matrix_factor
        if factor == 1.0:
            factor = self.baptiste.matrix_multiplier(shooter, origin, _along(origin, direction, t))
        if direct.duration > 0.0:
            self._queue_rifle_dose(shooter, target, direct, direct.amount * factor * direct.scale(travelled + t),
                                   shot_frame)
            return
        if direct.ally_heal and self._ally(shooter, target):
            self._heal(shooter, target, direct.ally_heal * factor)
            return
        if direct.healing:
            self._apply_hit(shooter, target, direct, direct.amount * factor, False, shot_frame, world_tick=world_tick)
            return
        hit_y = origin[1] + direction[1] * t
        if shown.sentry_yaw is not None:
            core = ray_capsule(origin, direction, *shown.core_segment(), SENTRY_CORE_RADIUS)
            critical = core is not None and core <= t + EPSILON
        elif shown.head_offset is not None:
            centre = _along(shown.feet, shown.head_offset, 1.0)
            head = ray_capsule(origin, direction, centre, centre, TRACER_HEAD_RADIUS)
            critical = head is not None and head <= reach
        else:
            critical = hit_y >= shown.feet[1] + shown.eye - HEAD_BELOW
        critical = (shown.critical_allowed and critical and self._fortify_reduction(target, tick=self.tick) is None)
        amount = factor * direct.amount * direct.scale(travelled + t) * (direct.critical if critical else 1.0)
        landed = self._damage(
            shooter, target, amount, critical and direct.critical > 1.0, direct.loadout, shot_frame,
            world_tick=world_tick, hit_tags=direct.tags, boost_credit=direct.boost_credit,
            award_ult=state.instance.graph.index != 0x01EE,
            deadeye=state.instance.graph.index == 0x01EE and state.node.pos == 14,
            hit_reaction=state.instance.graph.index == 0x01D2 and state.node.pos in (31, 37),
            source_scaled=reaper_shell is not None and bounces > 0,
            reflected_reaper_shell=reaper_shell is not None and bounces > 0,
        )
        if landed:
            self._hit_effect(target, direct.effect, shooter=shooter)
            return shooter, target, travelled + t

    def _soldier_visor_direction(self, shooter, volley, origin, direction, view_tick):
        """Reconstructed Soldier rifle acquisition using active 0259 search data.

        The authored graph gates this search after the activation animation and
        removes it at expiry/death. Radius and cone come from that node. The
        host supplies the view basis, target poses and capsule aim samples.
        """
        if volley.instance.graph.index != 0x0254:
            return direction
        script = shooter.body_script
        if script is None or script.component.owner_dead:
            return direction
        searches = [
            state for instance in script.component.instances.values()
            if instance.graph.index == 0x0259
            for state in instance.states.values()
            if state.active and isinstance(state, nodes.TrackTargets)
            and (state.node.field("m_8A2905A0") or {}).get("m_identifier") == 2337
        ]
        candidates = (
            candidate for search in searches
            for candidate in self._visor_candidates(search, shooter, origin, direction, view_tick)
        )
        best = min(candidates, key=lambda candidate: candidate[0], default=None)
        return direction if best is None else best[1]

    def visor_targets(self, search):
        """Publish the sandbox ranking from current poses for the lock list."""
        component = search.instance.component
        shooter = next(
            (player for player in self.match.players
             if player.has_body and player.spawned and player.body == component.entity),
            None,
        )
        if shooter is None or component.owner_dead:
            return ()
        origin = self._eye(shooter)
        direction = aim(shooter.mover.yaw, shooter.mover.pitch)
        ranked = sorted(self._visor_candidates(search, shooter, origin, direction))
        return tuple(expr.Entity(entity) for (_, entity), _ in ranked)

    def _visor_candidates(self, search, shooter, origin, direction, view_tick=None):
        radius = _number(search.evaluate(search.node.field("m_radius"), watch=False), 0.0)
        cone = _number(search.evaluate(search.node.field("m_A30B383E"), watch=False), 0.0)
        if radius <= 0.0 or not 0.0 < cone <= 180.0:
            return
        threshold = math.cos(math.radians(cone))
        horizontal = math.hypot(direction[0], direction[2])
        right = ((direction[2] / horizontal, 0.0, -direction[0] / horizontal)
                 if horizontal > EPSILON else (1.0, 0.0, 0.0))
        up = (direction[1] * right[2], horizontal, -direction[1] * right[0])
        for target in self._enemies(shooter):
            shown = target if view_tick is None else self.seen(target, view_tick)
            if shown is None:
                continue
            centre = (shown.feet[0], shown.feet[1] + shown.height / 2, shown.feet[2])
            offset = _sub(centre, origin)
            distance = math.sqrt(_dot(offset, offset))
            if distance <= EPSILON or distance > radius:
                continue
            candidate = tuple(value / distance for value in offset)
            alignment = _dot(direction, candidate)
            if alignment < threshold:
                continue
            a, b = shown.segment()
            # Verify the same ray the rifle will fire. Prefer centre mass,
            # then the upper and lower ends of the capsule's axis.
            for point in (centre, b, a):
                offset = _sub(point, origin)
                length = math.sqrt(_dot(offset, offset))
                if length <= EPSILON or length > radius:
                    continue
                candidate = tuple(value / length for value in offset)
                if _dot(direction, candidate) < threshold:
                    continue
                contact = ray_capsule(origin, candidate, a, b, shown.radius)
                if contact is None:
                    continue
                wall = ray_world(self._collision(), origin, candidate, contact)
                if wall is None or wall >= contact:
                    score = self._visor_score(shown, origin, direction, right, up)
                    yield (score, target.entity), candidate
                    break

    @staticmethod
    def _visor_score(target, origin, direction, right, up):
        """0259's two score terms using the host view basis and mover extents."""
        feet = _sub(target.feet, origin)
        if target.radius > 0 and target.height > 0:
            lower = _along(feet, right, -target.radius)
            upper = _along((feet[0], feet[1] + target.height, feet[2]), right, target.radius)
        else:
            lower = upper = feet
        lower_depth, upper_depth = _dot(lower, direction), _dot(upper, direction)
        if min(lower_depth, upper_depth) <= EPSILON:
            screen = 100.0
        else:
            # At 90 degrees the projected offsets from the cursor are x/(2z) and y/(2z).
            gaps = []
            for axis in (right, up):
                lo = _dot(lower, axis) / (2 * lower_depth)
                hi = _dot(upper, axis) / (2 * upper_depth)
                gaps.append(max(0.0, min(lo, hi), -max(lo, hi)))
            screen = min(1.0, math.sqrt(2) * math.hypot(*gaps))
        return screen + .01 * min(1.0, math.sqrt(_dot(feet, feet)) / 15)

    def _copied_visor_direction(self, shooter, command, volley, origin, direction):
        """Reconstruct copied Soldier's active0259/st7 target consumer.

        DATA: active TrackTargets radius45 and angle parameter45. RECONSTRUCTED:
        angle as degrees from aim, angular-first ranking and capsule-centre aim.
        Native weighted ranking, target bindings and observer presentation stay open.
        Only copied0254/st10 rifle fire uses this consumer; Helix keeps manual aim.
        """
        if ((volley.instance.graph.index, volley.index) != (0x254, 10)
                or not self.echo_duplicates.active(shooter)):
            return direction
        script = shooter.body_script
        instance = next((i for i in script.component.instances.values() if i.graph.index == 0x259), None)
        if instance is None:
            return direction
        ability, tracking = instance.states.get(0), instance.states.get(7)
        if (ability is None or not ability.flags & nodes.Ability.RUNNING
                or tracking is None or not tracking.active):
            return direction
        radius = _number(tracking.evaluate(tracking.node.field('m_radius'), watch=False), 0.0)
        degrees = _number(tracking.evaluate(tracking.node.field('m_A30B383E'), watch=False), 0.0)
        minimum = math.cos(math.radians(degrees))
        view = command.frame - command.view_delay(shooter.quanta) * 1e6 / shooter.quanta
        best = None
        for target in self._enemies(shooter, damageable=True):
            shown = self.seen(target, view)
            if shown is None:
                continue
            point = (shown.feet[0], shown.feet[1] + shown.height * 0.5, shown.feet[2])
            delta = _sub(point, origin)
            distance = math.sqrt(_dot(delta, delta))
            if not EPSILON < distance <= radius:
                continue
            ray = tuple(axis / distance for axis in delta)
            alignment = _dot(direction, ray)
            if alignment < minimum:
                continue
            a, b = shown.segment()
            entry = ray_capsule(origin, ray, a, b, shown.radius)
            if entry is None:
                continue
            wall = ray_world(self._collision(), origin, ray, entry)
            if wall is not None and wall + EPSILON < entry:
                continue
            rank = (-alignment, distance, target.entity)
            if best is None or rank < best[0]:
                best = rank, ray
        return direction if best is None else best[1]

    def _matrix_hit(self, shooter, origin, direction, reach, definition):
        """Nearest enemy authored Matrix in the declared current aim frame.

        Consumers bound reach by earlier world/defense contacts. Native filter,
        attachment and history semantics remain open.
        """
        if not (definition in self.matrix_definitions if self.matrix_definitions is not None
                else deflection.absorbable(definition)):
            return None
        best = None
        for target in self._enemies(shooter):
            player = target.owner
            if (isinstance(player, bots.Bot)
                    or getattr(getattr(player, "hero", None), "guid", None) != 0x02E000000000007A):
                continue
            distance = deflection.matrix_distance(
                player, origin, direction, reach, self._eye(player),
                aim(player.mover.yaw, player.mover.pitch),
            )
            if distance is not None and (best is None or distance < best):
                best = distance
        return best

    def _fly(self, rocket: Rocket) -> None:
        previous = self.pulse_bombs.context
        if getattr(rocket, "pulse_bomb", False):
            self.pulse_bombs.context = self.tick
        try:
            self._fly_current(rocket)
            if rocket.source_graph == 0x00B9 and rocket.range_limit is not None:
                rocket.range_left = max(0.0, rocket.range_limit - rocket.travelled)
        finally:
            self.pulse_bombs.context = previous

    def _fly_current(self, rocket: Rocket) -> None:
        """Sweep elapsed world time against the current bodies and static collision."""
        if (rocket.gravity and rocket.velocity is None
                and (rocket.source_graph == 0x00B9
                     or getattr(getattr(rocket.shooter, "hero", None), "guid", None) == 0x02E0000000000005)):
            rocket.velocity = tuple(value * rocket.speed for value in rocket.direction)
        if rocket.shot is not None and rocket.shot.spawn == projectiles.FIELD_ENTITY:
            cursor = rocket.flight_cursor()
            first = self.tick if cursor is None else cursor + 1
            for at in range(first, self.tick + 1):
                if rocket not in self.rockets:
                    break
                rocket.last_tick = at
                self._fly_field_canister(rocket, min(TICK, rocket.left))
            return
        if rocket.mine is not None and rocket.mine.parked:
            return
        if not self.orisa.flying(rocket):
            return
        # Player input can arrive immediately before the scheduler accounts for a gap.
        elapsed = 1 if rocket.last_tick is None else self.tick - rocket.last_tick
        if elapsed <= 0:
            return
        # Each legacy integration advances the pose through its labelled tick.
        # Keep that same contiguous interval when several ticks are swept at once.
        if rocket.started_tick is None:
            rocket.started_tick = self.tick - elapsed
        sweep_tick = self.tick - elapsed + 1
        rocket.last_tick = self.tick
        if (rocket.deployment is not None or rocket.penetrated is not None
                or rocket.range_left is not None and rocket.source_graph != 0x00B9 and rocket.continuation is None):
            duration = elapsed * TICK
            sweep_tick = self.tick - elapsed
            while duration > EPSILON and rocket in self.rockets:
                portion = min(duration, TICK) if rocket.gravity else duration
                self._sweep(rocket, portion, sweep_tick)
                duration -= portion
                sweep_tick += portion / TICK
            return
        if rocket.continuation is not None:
            if getattr(rocket.shooter, "gone", False):
                self._landed(rocket, release=False)
                return
            duration = min(elapsed * TICK, rocket.left,
                           max(0.0, (self.tick - rocket.started_tick) * TICK - rocket.flown))
            if duration > 0:
                self._fly_slice(rocket, duration, self.tick)
            elif rocket.left <= 0:
                self._expire(rocket)
            return
        if rocket.is_barrage:
            registered = rocket in self.rockets
            for at in range(self.tick - elapsed + 1, self.tick + 1):
                if registered and rocket not in self.rockets:
                    break
                rocket.last_tick = at
                continuation = self._fly_slice(rocket, min(TICK, rocket.left), at)
                if isinstance(continuation, Rocket):
                    rocket = continuation
                elif rocket.ended is not None:
                    break
            return
        if rocket.fuse_left is not None:
            if rocket.attached_entity is not None:
                anchor = next((target for target in self._targets()
                               if target.entity == rocket.attached_entity), None)
                if (anchor is not None and getattr(rocket, "pulse_bomb", False)
                        and (self.pulse_bombs.recalling(anchor) or immunity.wraith_active(anchor.owner, anchor.entity))):
                    rocket.attached_entity = None
                    anchor = None
                if anchor is not None:
                    rocket.position = tuple(p + d for p, d in zip(
                        anchor.feet, rocket.attached_offset, strict=True
                    ))
                else:
                    bubble = self.barriers.attached(rocket.attached_entity)
                    if bubble is not None:
                        rocket.position = tuple(p + d for p, d in zip(
                            bubble.centre(), rocket.attached_offset, strict=True
                        ))
            for _ in range(elapsed):
                rocket.fuse_left -= TICK
                if getattr(rocket, "pulse_bomb", False):
                    rocket.left -= TICK
                if rocket.fuse_left <= EPSILON or getattr(rocket, "pulse_bomb", False) and rocket.left <= EPSILON:
                    self._explode(rocket, rocket.position)
                    self._landed(rocket)
                    break
            return
        if rocket.velocity is not None or rocket.detonate_on_expiry:
            # Preserve the fixed16ms accelerated trajectory when the host skips
            # world ticks. Existing target poses/effect clocks remain the host's.
            for _ in range(elapsed):
                if rocket not in self.rockets:
                    break
                self._fly_step(rocket, min(TICK, rocket.left))
            return
        self._fly_step(rocket, min(elapsed * TICK, rocket.left))

    def _fly_field_canister(self, rocket, remaining):
        """Delivered 0257 gravity/rebound clock, using the combined collision services."""
        for _ in range(max(deflection.MAX_DEFLECTIONS, (rocket.rebound_limit or 0) + 1)):
            duration = min(remaining, rocket.left)
            vx, vy, vz = (axis * rocket.speed for axis in rocket.direction)
            rocket.set_velocity(vx, vy - rocket.gravity * remaining, vz)
            if rocket.speed <= EPSILON:
                rocket.travel(0.0, seconds=duration)
                if rocket.left <= 0:
                    self._landed(rocket)
                return
            range_exhausted = False
            if rocket.range_limit is not None:
                range_time = max(0.0, rocket.range_limit - rocket.distance) / rocket.speed
                range_exhausted = range_time <= duration
                duration = min(duration, range_time)
            if duration <= 0:
                self._landed(rocket)
                return
            step = rocket.speed * duration
            best = None
            for target in self._shot_targets(rocket.shooter, rocket.direct):
                a, b = target.segment()
                distance = ray_capsule(rocket.position, rocket.direction, a, b, target.radius)
                if distance is not None and distance <= step and (best is None or distance < best[0]):
                    best = distance, target
            surface = self._collision().cast_ray(rocket.position, rocket.direction, step)
            if surface is not None and (best is None or surface.distance < best[0]):
                best = surface.distance, None
            limit = step if best is None else best[0]
            barrier = self._barrier_contact(rocket.shooter, rocket.position, rocket.direction, limit)
            absorbed = self._deflector(rocket.shooter, rocket.definition, rocket.position, rocket.direction, limit,
                                       absorption_admitted=rocket.matrix_absorbable)
            if (barrier is not None and (best is None or barrier[0] < best[0])
                    and (absorbed is None or not absorbed[3] or barrier[0] < absorbed[0])):
                rocket.travel(barrier[0])
                self._landed(rocket)
                return
            if absorbed is not None and absorbed[3] and (best is None or absorbed[0] < best[0]):
                rocket.travel(absorbed[0])
                self._landed(rocket, release=False)
                return
            if best is None:
                rocket.position = _along(rocket.position, rocket.direction, step)
                rocket.travel(step, seconds=duration)
                if range_exhausted or rocket.left <= EPSILON:
                    self._landed(rocket)
                return
            distance, struck = best
            consumed = rocket.travel(distance)
            ground_point = None
            if struck is None and surface is not None and rocket.rebound_limit is not None:
                rocket.position = _along(rocket.position, rocket.direction, max(0.0, distance - 0.005))
                if surface.normal[1] <= 0:
                    rocket.rebounds += 1
                    if rocket.rebounds <= rocket.rebound_limit:
                        incoming = (vx, vy - rocket.gravity * consumed, vz)
                        normal_speed = _dot(incoming, surface.normal)
                        if normal_speed < 0:
                            incoming = _along(incoming, surface.normal, -(1 + rocket.rebound_scale) * normal_speed)
                            rocket.position = _along(rocket.position, surface.normal, 0.001)
                        rocket.set_velocity(*incoming)
                        remaining -= consumed
                        if remaining <= 0:
                            return
                        continue
                else:
                    ground_point = surface.point
            self._landed(rocket, ground_point=ground_point)
            return
        self._landed(rocket)

    def _split_volley(self, state, direction, seed, number):
        from ow174.game import weapon_spread

        transition = state.node.field("m_747ECB51")
        if not transition or transition.get("$") != "STU_DB5FDC79":
            return None
        volley = transition["m_volley"]
        direct = hit_def(state, volley["m_6396149F"])
        speed = expr.to_float(state.evaluate(volley["m_projectileSpeed"], watch=False))
        reach = expr.to_float(state.evaluate(volley["m_FEC435C6"], watch=False))
        lifetime = expr.to_float(state.evaluate(volley["m_projectileLifetime"], watch=False))
        directions = weapon_spread.authored_ring_directions(state, direction, seed, number, volley=volley)
        if direct is None or direct.amount <= 0.0 or speed <= 0 or lifetime <= 0 or reach <= 0:
            return None
        return SplitVolley(directions, speed, min(lifetime, reach / speed), direct)

    def _expire(self, rocket: Rocket) -> None:
        if rocket.mine is not None:
            if rocket.detonate_on_expiry:
                self.junkrat.detonate(rocket.mine)
            else:
                self.junkrat.retire(rocket.mine)
            return
        rocket.ended = "expired"
        self._landed(rocket)
        if rocket.detonate_on_expiry:
            self._explode(rocket, rocket.position)
        if (split := rocket.split_on_expiry) is not None:
            self.rockets.extend(
                Rocket(rocket.shooter, rocket.team, rocket.position, direction, split.speed, split.lifetime,
                       split.direct, None, last_tick=self.tick, scale_direct=True)
                for direction in split.directions
            )

    def _fly_step(self, rocket: Rocket, duration: float, *, reflected=None) -> None:
        """One linear sweep after any authored acceleration update."""
        if duration <= 0.0:
            self._expire(rocket)
            return
        if rocket.spray is not None:
            moira.fly_spray(self, rocket, duration)
            return
        if rocket.terrain_bounce is not None or rocket.mine is not None:
            # Source0065's direction/speed pair is authoritative for each chord.
            rocket.velocity = tuple(axis * rocket.speed for axis in rocket.direction)
        if rocket.velocity is not None and not molten_core.is_globule(rocket):
            from ow174.game import mccree

            # Apply the recovered velocity update before this frame's sweep.
            # This semi-implicit sweep order is a server reconstruction.
            velocity = rocket.velocity
            hanzo_arrow = (rocket.source_graph == 0x00B9
                           or getattr(getattr(rocket.shooter, "hero", None), "guid", None) == 0x02E0000000000005)
            if ((rocket.sticky_delay is not None and not getattr(rocket, "pulse_bomb", False))
                    or hanzo_arrow or rocket.terrain_bounce is not None or rocket.mine is not None):
                vx, vy, vz = velocity
                rocket.velocity = (vx, vy - rocket.gravity * duration, vz)
                sweep = (vx, vy - rocket.gravity * duration * 0.5, vz)
            else:
                rocket.velocity = sweep = mccree.gravity_velocity(velocity, rocket.gravity, duration)
            rocket.speed = math.sqrt(_dot(sweep, sweep))
            if rocket.speed:
                rocket.direction = tuple(value / rocket.speed for value in sweep)
        self._fly_slice(rocket, duration, self.tick, reflected=reflected)
        if rocket.mine is not None:
            self.junkrat.sync_pose(rocket.mine)
        if (rocket.terrain_bounce is not None or rocket.mine is not None) and rocket.velocity is not None and rocket in self.rockets:
            # The delivered grenade stores the end velocity after sweeping its
            # ballistic chord; the next frame and public direction share it.
            rocket.speed = math.hypot(*rocket.velocity)
            if rocket.speed > EPSILON:
                rocket.direction = tuple(axis / rocket.speed for axis in rocket.velocity)


    def _fly_slice(self, rocket: Rocket, duration: float, tick: int, *, reflected=None) -> Rocket | None:
        if not rocket.collide_bodies or rocket.deploy_body is not None:
            step = rocket.speed * duration
            wall = ray_world(self._collision(), rocket.position, rocket.direction, step)
            if wall is not None:
                rocket.position = _along(rocket.position, rocket.direction, wall)
                rocket.flown += wall / rocket.speed
                rocket.travelled += wall
                rocket.ended = "world"
                self.orisa_barriers.landed(rocket, tick)
                self._landed(rocket)
                return
            rocket.position = _along(rocket.position, rocket.direction, step)
            rocket.flown += duration
            rocket.travelled += step
            rocket.left -= duration
            if rocket.left <= EPSILON:
                self._expire(rocket)
            return
        # Re-sweep the unspent slice after redirects. Bound overlapping
        # deflectors that could repeatedly collide at distance zero.
        reflected = set() if reflected is None else reflected
        for _ in range(8 + rocket.target_limit):
            continuation = self._sweep_rocket(rocket, duration, reflected)
            if isinstance(continuation, Rocket):
                return continuation
            duration = continuation
            if duration <= EPSILON:
                return
        rocket.flown += duration
        rocket.left -= duration
        if rocket.left <= EPSILON:
            self._expire(rocket)

    def _orisa_deployable_hit(self, shooter, origin, direction, length, *, radius=0.0):
        nearest = None
        for manager in (self.orisa_barriers, self.orisa.superchargers, self.photon_barriers):
            hit = manager.trace(shooter, origin, direction, length, radius=radius)
            if hit is not None and (nearest is None or hit[0] < nearest[0]):
                nearest = hit[0], hit[1], manager
        return nearest

    def _bounce_storm(self, rocket, direction, normal, distance, step, duration,
                      integrated_duration, reflected):
        """Mount the delivery's one specular wall bounce and unspent flight slice."""
        rocket.storm_ricochets -= 1
        consumed = duration * distance / step if step > EPSILON else 0.0
        if rocket.velocity is not None:
            vx, vy, vz = rocket.velocity
            incoming = (vx, vy + rocket.gravity * (integrated_duration - consumed), vz)
        else:
            incoming = tuple(axis * rocket.speed for axis in direction)
        normal_speed = _dot(incoming, normal)
        velocity = tuple(expr.f32(axis - 2 * normal_speed * n)
                         for axis, n in zip(incoming, normal, strict=True))
        rocket.position = _along(_along(rocket.position, direction, distance), normal, 0.001)
        rocket.velocity = velocity
        rocket.speed = math.hypot(*velocity)
        if rocket.speed > EPSILON:
            rocket.direction = tuple(axis / rocket.speed for axis in velocity)
        rocket.flown += consumed
        rocket.left -= consumed
        rocket.travelled += distance
        if rocket.range_limit is not None:
            rocket.range_left = max(0.0, rocket.range_limit - rocket.travelled)
        if rocket.left <= EPSILON or rocket.range_left == 0.0:
            self._expire(rocket)
        elif duration - consumed > EPSILON:
            self._fly_step(rocket, duration - consumed, reflected=reflected)

    def _sweep_rocket(self, rocket: Rocket, duration: float, reflected: set[int]) -> float | Rocket:
        """Sweep without another gravity update. Only reflection returns unused time."""
        integrated_duration = duration
        step = rocket.speed * duration
        heading = rocket.direction
        range_ends = False
        if molten_core.is_globule(rocket):
            age = rocket.flown - rocket.segment_start
            displacement = (rocket.speed * heading[0] * duration,
                            rocket.speed * heading[1] * duration - rocket.gravity * (age * duration + .5 * duration * duration),
                            rocket.speed * heading[2] * duration)
            step = math.hypot(*displacement)
            if step > EPSILON:
                heading = tuple(axis / step for axis in displacement)
        if rocket.is_barrage:
            lateral = barrage_motion.displacement(
                rocket.motion, heading, rocket.flown + rocket.motion_phase_offset, duration,
            )
            chord = _along(lateral, heading, step)
            step = math.hypot(*chord)
            if step <= EPSILON:
                return 0.0
            heading = tuple(axis / step for axis in chord)
            if rocket.range_limit is not None:
                remaining = max(0.0, rocket.range_limit - rocket.travelled)
                if remaining <= step:
                    duration *= remaining / step
                    step, range_ends = remaining, True
        if not rocket.is_barrage and rocket.range_limit is not None:
            remaining = max(0.0, rocket.range_limit - rocket.travelled)
            if remaining <= step:
                duration *= remaining / step if step > EPSILON else 0.0
                step, range_ends = remaining, True
        blast = rocket.blast
        padding = rocket.direct.projectile_extent if rocket.direct is not None else 0.0
        biotic = blast is not None and blast.effect == BIOTIC_GRENADE_EFFECT
        candidates = () if rocket.mine is not None else self._targets() if biotic else self._shot_targets(rocket.shooter, rocket.direct)
        best = None
        assisted_contact = (rocket.definition == 0x0400000000001000
                            or rocket.source_graph in (0x23, 0x7B, 0x2B))
        assisted = None
        for target in candidates:
            if target.owner is rocket.shooter or (target.owner is not None and self.vitals_of(target).dead):
                continue
            if (target.entity in rocket.hit_entities or self._has_protection(target, PHASE_STATES)
                    or self.pulse_bombs.recalling(target)):
                continue
            if immunity.wraith_active(target.owner, target.entity):
                continue
            a, b = target.segment()
            t = ray_capsule(rocket.position, heading, a, b, target.radius + (0.0 if assisted_contact else padding))
            if t is not None and t <= step and (best is None or t < best[0]):
                best = (t, target)
            if assisted_contact and padding > 0:
                t = ray_capsule(rocket.position, heading, a, b, target.radius + padding)
                if t is not None and t <= step and (assisted is None or t < assisted[0]):
                    assisted = (t, target)
        scene_barrier = (self._barrier_hit(rocket.shooter, rocket.position, heading, best[0] if best else step)
                         if rocket.mine is None else None)
        if scene_barrier is not None:
            best = (scene_barrier[0], None)
        collision = self._collision()
        assisted_rank = assisted[0] + padding if assisted is not None else 0.0
        wall = ray_world(collision, rocket.position, heading, max(step, assisted_rank))
        terrain_normal = None
        if rocket.terrain_bounce is not None:
            from ow174.game import junkrat_projectile

            wall, terrain_normal = junkrat_projectile.surface(rocket, collision, heading, step)
        storm_normal = None
        if rocket.storm_ricochets:
            hit = collision.cast_ray(rocket.position, heading, step)
            wall = hit.distance if hit is not None else None
            if hit is not None:
                storm_normal = hit.normal
                if _dot(heading, storm_normal) > 0:
                    storm_normal = tuple(-axis for axis in storm_normal)
                if _dot(heading, storm_normal) >= -EPSILON:
                    storm_normal = None
        if (padding > 0 and rocket.terrain_bounce is None and not assisted_contact and rocket.source_graph != 0x00B9
                and (rocket.direct is None or rocket.direct.loadout != SOLDIER_WEAPON_LOADOUT)):
            swept = collision.sweep_sphere(rocket.position, heading, step, padding)
            if swept is not None and (wall is None or swept < wall):
                wall = swept
        if wall is not None and wall <= step and (best is None or wall < best[0]):
            best = (wall, None)
            scene_barrier = None
        ice = self.walls.cast(rocket.position, heading, best[0] if best else step)
        if ice is not None and (best is None or ice[0] < best[0]):
            best = (ice[0], None)
        else:
            ice = None
        if rocket.mine is not None:
            return self.junkrat.flight_step(rocket, duration, heading, step, best, range_ends)
        if (assisted is not None and (best is None or (best[1] is None and assisted_rank < best[0]))
                and (wall is None or assisted_rank < wall)):
            best = assisted
            scene_barrier = ice = None
        redirect = None
        pulse_facing = None
        if getattr(rocket, "pulse_bomb", False) and best is not None:
            pulse_facing = self.pulse_bombs.deflect_aim(best[1], heading)
            if pulse_facing is not None:
                redirect = best
        if (biotic or (blast is not None and blast.effect == FLASHBANG_EFFECT)
                or (rocket.direct is not None and rocket.direct.loadout == HELIX_LOADOUT)):
            limit = best[0] if best else step
            candidate = self._trace_deflection(rocket.shooter, rocket.position, heading, limit)
            if candidate is not None and (best is None or candidate[0] < best[0]):
                redirect = candidate
                best = candidate
        limit = best[0] if best else step
        absorption = self._trace_absorption(
            rocket.shooter, rocket.definition, rocket.position, heading, limit,
            grasp=rocket.direct is not None and rocket.direct.loadout == HELIX_LOADOUT,
            admitted=rocket.matrix_absorbable,
        )
        if absorption is not None and (best is None or absorption[0] < limit):
            best = absorption[0], None
            redirect = None
        else:
            absorption = None
        shield = self.brigitte.barrier_ray(
            rocket.shooter, rocket.position, heading, min(step, best[0] if best else step),
        )
        if shield is not None:
            best = shield[0], None
            redirect = None
            absorption = None
        limit = best[0] if best else step
        deployed = sigma_barrier.barrier_intersection(
            self, rocket.shooter, rocket.position, heading, limit,
        )
        if deployed is not None and (best is None or deployed[0] < limit):
            best = deployed[0], None
            shield = redirect = absorption = None
        else:
            deployed = None
        limit = best[0] if best else step
        echo_shield = self.barriers.intercept(rocket.shooter, rocket.position, heading, limit,
                                              radius=padding, include_authored_mesh=False)
        if echo_shield is not None and (best is None or echo_shield[0] < limit):
            best = echo_shield
            shield = redirect = absorption = deployed = None
        else:
            echo_shield = None
        limit = best[0] if best else step
        defense = deflections.intercept(
            self, rocket, limit, reflected, direction=heading,
            eye_of=self._barrage_defense_eye if rocket.is_barrage else None,
            modes=('absorb',) if getattr(rocket, "pulse_bomb", False) else ('reflect', 'absorb'),
        )
        if defense is not None and (best is None or defense[0] < limit):
            distance, target, forward, state, mode = defense
            best = distance, target
            shield = echo_shield = deployed = None
            if mode == "absorb":
                absorption, redirect = (distance, state), None
            else:
                absorption, redirect = None, (distance, target)
        else:
            defense = None
        scripted_shield = None
        scripted_absorption = None
        limit = best[0] if best else step
        candidate = barriers.ray_hit(self, rocket.shooter, rocket.position, heading, limit, padding=padding)
        if candidate is not None and (best is None or candidate[0] < limit):
            scripted_shield = candidate
            best = candidate[0], None
            shield = echo_shield = deployed = redirect = absorption = defense = None

        limit = best[0] if best else step
        candidate = interception.absorption(
            self, rocket.shooter, rocket.direct, rocket.position, heading, limit, padding=padding,
            definition=rocket.definition, admitted=rocket.matrix_absorbable,
        )
        if candidate is not None and (best is None or candidate[0] < limit):
            scripted_absorption = absorption = candidate
            best = candidate[0], None
            shield = echo_shield = deployed = redirect = defense = scripted_shield = None

        for target in self._enemies(rocket.shooter):
            if target.entity in reflected:
                continue
            limit = best[0] if best else step
            distance = self._genji_deflect_contact(
                target, rocket.direct, rocket.position, heading, limit, padding=padding,
            )
            if distance is not None and (best is None or distance <= limit):
                best = redirect = distance, target
                shield = echo_shield = deployed = absorption = defense = scripted_shield = scripted_absorption = None

        limit = best[0] if best else step
        mine_hit = self.venom.intersect(rocket.shooter, rocket.position, heading, limit)
        if mine_hit is not None:
            limit = mine_hit[0]
        ability = self._orisa_deployable_hit(
            rocket.shooter, rocket.position, heading, limit, radius=padding,
        )
        if ability is not None and (best is None or ability[0] < limit):
            distance, actor, manager = ability
            rocket.ended = "barrier" if manager is self.orisa_barriers else "deployable"
            rocket.travelled += distance
            contact_time = duration * distance / step if step > EPSILON else 0.0
            rocket.flown += contact_time
            point = _along(rocket.position, heading, distance)
            if rocket.sticky_delay is not None:
                if rocket.direct is not None:
                    manager.damage(actor, rocket.direct, rocket.flown * rocket.speed)
                self._stick(rocket, point, duration - contact_time)
                return 0.0
            self._landed(rocket, continuation_point=point)
            self._explode(rocket, point, orisa_impact=(actor, manager), incident_direction=heading)
            return 0.0

        if mine_hit is not None:
            distance, mine = mine_hit
            self._amplify_flight(rocket, heading, distance, self.tick - duration / TICK,
                                 duration / (TICK * step) if step > EPSILON else 0.0)
            rocket.travelled += distance
            rocket.flown += duration * distance / step if step > EPSILON else 0.0
            point = _along(rocket.position, heading, distance)
            if rocket.direct is not None:
                self.venom.damage(mine, rocket.direct.amount)
            rocket.ended = "deployable"
            self._landed(rocket, continuation_point=point)
            self._explode(rocket, point, incident_direction=heading)
            return 0.0

        if (terrain_normal is not None and best == (wall, None)
                and all(item is None for item in (scene_barrier, ice, shield, echo_shield, deployed,
                                                  redirect, absorption, scripted_shield))):
            junkrat_projectile.bounce(self, rocket, heading, terrain_normal, wall, step, duration,
                                      integrated_duration, reflected, range_ends)
            return 0.0
        if (storm_normal is not None and best == (wall, None)
                and all(item is None for item in (scene_barrier, ice, shield, echo_shield, deployed,
                                                  redirect, absorption, scripted_shield))):
            self._bounce_storm(rocket, heading, storm_normal, wall, step, duration,
                               integrated_duration, reflected)
            return 0.0
        flight = step if best is None else best[0]
        self._amplify_flight(rocket, heading, flight, self.tick - duration / TICK,
                             duration / (TICK * step) if step > EPSILON else 0.0)
        if best is None:
            rocket.position = _along(rocket.position, heading, step)
            rocket.travelled += step
            rocket.flown += duration
            rocket.left -= duration
            if range_ends or rocket.left <= 0.0:
                self._expire(rocket)
            return 0.0
        t, struck = best
        contact_time = duration * t / step if step > EPSILON else 0.0
        rocket.flown += contact_time
        rocket.travelled += t
        if absorption is not None:
            self.rockets.remove(rocket)
            if rocket.shot is not None:
                self.projectiles.absorbed(rocket.shot)
                if rocket.source_graph == 0x00B9:
                    rocket.shot = None
            amount = sum(hit.amount for hit in (rocket.direct, blast) if hit is not None)
            if scripted_absorption is not None:
                interception.absorbed(scripted_absorption[1], amount)
            elif defense is not None:
                deflections.absorb(absorption[1], amount)
            else:
                self._absorbed(absorption[1], amount, rocket.source_entity)
            return 0.0
        if redirect is not None:
            if rocket.is_barrage:
                return self._reflect_barrage(rocket, struck, duration, contact_time)
            if rocket.shot is not None:
                self.projectiles.flown(rocket.shot, rocket.flown)
                rocket.shot = None
            point = _along(rocket.position, heading, t)
            if molten_core.is_globule(rocket):
                velocity = [axis * rocket.speed for axis in rocket.direction]
                velocity[1] -= rocket.gravity * (rocket.flown - rocket.segment_start)
                rocket.speed = math.hypot(*velocity)
                rocket.segment_start = rocket.flown
            rocket.shooter, rocket.team = struck.owner, struck.team
            reflected.add(struck.entity)
            rocket.source_body = struck.entity
            rocket.source_entity = struck.entity
            if rocket.is_barrage:
                rocket.charge_eligible = True  # reflected Barrage belongs to the defender's damage route.
            old_scale = rocket.boost_credit[1] if rocket.boost_credit is not None else 1.0
            for name in ("direct", "blast"):
                definition = getattr(rocket, name)
                if definition is not None:
                    definition = replace(definition, amount=definition.amount / old_scale, boost_credit=None)
                    setattr(rocket, name, self._support_hit(rocket.shooter, definition))
            rocket.damage_charge = None
            rocket.boost_credit = (rocket.direct.boost_credit if rocket.direct is not None else
                                   rocket.blast.boost_credit if rocket.blast is not None else None)
            rocket.direction = (pulse_facing if pulse_facing is not None else defense[2]
                                if defense is not None else aim(struck.owner.mover.yaw, struck.owner.mover.pitch))
            separation = min(1e-4, max(0.0, duration - contact_time) * rocket.speed)
            rocket.position = _along(point, rocket.direction, separation)
            if rocket.velocity is not None:
                rocket.velocity = tuple(axis * rocket.speed for axis in rocket.direction)
            rocket.reflected = True
            if rocket.dose_scalar is not None:
                from ow174.game import nano

                rocket.dose_scalar = (nano.offense(self, rocket.source_entity)
                                      * self.orisa.superchargers.scale(rocket.shooter))
            spent = min(duration, contact_time + (separation / rocket.speed if rocket.speed else 0.0))
            rocket.travelled += separation
            rocket.flown += spent - contact_time
            rocket.left -= spent
            if rocket.left <= EPSILON:
                self._landed(rocket, continuation_point=point)
                return 0.0
            return duration - spent
        point = _along(rocket.position, heading, t)
        rocket.ended = "body" if struck is not None else "world"
        if scripted_shield is not None:
            native = scripted_shield[1]
            if native.component.world.hero == barrier.REINHARDT and rocket.sticky_delay is None:
                # Freeze cover using the exact native mesh that was struck,
                # independently from the source collision-descriptor entry point.
                protected = set()
                origin = _along(point, heading, -0.0001)
                for target in self._enemies(rocket.shooter, damageable=True, team=rocket.team):
                    a, b = target.segment()
                    axis = _sub(b, a)
                    squared = _dot(axis, axis)
                    share = max(0., min(1., _dot(_sub(point, a), axis) / squared)) if squared > EPSILON else 0.
                    delta = _sub(_along(a, axis, share), origin)
                    length = math.sqrt(_dot(delta, delta))
                    if length and barriers.mesh_trace(native, origin, tuple(v / length for v in delta), length) is not None:
                        protected.add(target.entity)
                amount = sum(hit.amount for hit in (rocket.direct, blast) if hit is not None)
                barriers.damage(self, native, amount, attacker=_source_entity(rocket.shooter))
                self._explode(rocket, point, protected=protected,
                              native_impact_body=native.parent_body, incident_direction=heading)
                self._landed(rocket, continuation_point=point)
                return 0.0
            amount = sum(hit.amount for hit in (rocket.direct, blast) if hit is not None)
            barriers.damage(self, native, amount, attacker=_source_entity(rocket.shooter))
            if rocket.sticky_delay is not None:
                self._stick(rocket, point, duration - contact_time)
            else:
                self._landed(rocket, continuation_point=point)
            return 0.0
        if echo_shield is not None:
            actor = echo_shield[1]
            if rocket.direct is not None:
                self.barriers.damage(actor, rocket.direct.amount, rocket.shooter)
            if rocket.sticky_delay is not None:
                rocket.attached_entity = actor.entity
                rocket.attached_offset = _sub(point, actor.centre())
                self._stick(rocket, point, duration - contact_time)
                return 0.0
            if isinstance(actor, barriers.Bubble):
                self._explode(rocket, point, protected=(actor.body,))
            else:
                self._explode(rocket, point, world_shields=(actor,))
            self._landed(rocket, continuation_point=point)
            return 0.0
        if struck is None:
            point = _along(rocket.position, heading, max(0.0, t - 1e-4))
        if rocket.sonar is not None:
            attached = struck.entity if struck is not None else None
            offset = _sub(point, struck.feet) if struck is not None else (0.0, 0.0, 0.0)
            self.sonars.append(sonic.Beacon(
                rocket.shooter, rocket.source_entity, rocket.team, point, attached,
                offset, rocket.started_tick + rocket.flown / TICK, rocket.sonar,
            ))
        broken_particle_barriers: set[int] = set()
        if struck is not None and rocket.direct is not None and rocket.direct.amount > 0.0:
            direct = rocket.direct
            if direct.duration > 0.0:
                self._queue_rifle_dose(rocket.shooter, struck, direct, direct.amount, self.tick,
                                       source_entity=rocket.source_entity, team=rocket.team, scalar=rocket.dose_scalar)
            elif direct.ally_heal and self._ally(rocket.shooter, struck):
                self._heal(rocket.shooter, struck, direct.ally_heal, source_body=rocket.source_body)
            else:
                contact_y = point[1]
                if padding:
                    axis_y = _segment_point(point, *struck.segment())[1]
                    contact_y = axis_y + (contact_y - axis_y) * struck.radius / (struck.radius + padding)
                zen_orb = getattr(getattr(rocket.shooter, "hero", None), "guid", None) == 0x02E0000000000020
                critical = (
                    (direct.projectile_headshot or direct.loadout == ECHO_TRI_SHOT_LOADOUT or zen_orb)
                    and direct.critical > 1.0 and contact_y >= struck.feet[1] + struck.eye - HEAD_BELOW
                    and not (immunity.fortified(struck.owner, struck.entity)
                             and not orisa_fortify.historical_pending(struck.owner, self.tick))
                )
                amount = direct.amount * (direct.critical if critical else 1.0)
                if (rocket.scale_direct or zen_orb
                        or (getattr(rocket.shooter.hero, "guid", None) == hero_torbjorn.HERO
                            and direct.projectile_headshot)):
                    amount *= direct.scale(rocket.flown * rocket.speed)
                before = self.vitals_of(struck).total if rocket.accretion_source is not None else 0.0
                landed = self._damage(
                    rocket.shooter, struck, amount, critical, direct.loadout, self.tick,
                    world_tick=self.tick, hit_tags=direct.tags,
                    broken_particle_barriers=broken_particle_barriers, source_body=rocket.source_body,
                    damage_charge=rocket.damage_charge, boost_credit=rocket.boost_credit,
                    award_ult=rocket.charge_eligible,
                )
                if landed:
                    self._hit_effect(struck, direct.effect, shooter=rocket.shooter)
                    if rocket.accretion_source is not None:
                        self._accretion_hit(rocket, struck, before - self.vitals_of(struck).total)
                        self._apply_accretion_impulse(rocket, struck, direct)
        if struck is not None and blast is None and len(rocket.hit_entities) + 1 < rocket.target_limit:
            rocket.ended = None
            rocket.hit_entities.add(struck.entity)
            rocket.position = point
            rocket.left -= contact_time
            return max(0.0, duration - contact_time)
        impact_barrier = scene_barrier[1] if scene_barrier is not None and t == scene_barrier[0] else None
        if impact_barrier is not None:
            if rocket.direct is not None:
                direct = rocket.direct
                amount = direct.amount * (direct.scale(rocket.flown * rocket.speed) if rocket.scale_direct else 1.0)
                self._damage_barrier(rocket.shooter, impact_barrier, amount, direct.tags)
            if blast is not None:
                self._damage_barrier(rocket.shooter, impact_barrier, blast.amount * blast.splash(0.0), blast.tags)
        if ice is not None and t != ice[0]:
            ice = None
        if rocket.sticky_delay is not None:
            if struck is not None:
                rocket.attached_entity = struck.entity
                rocket.attached_offset = _sub(point, struck.feet)
            self._stick(rocket, point, duration - contact_time)
            return 0.0
        molten_surface = None
        if (molten_core.is_globule(rocket) and struck is None and wall is not None
                and abs(t - wall) <= EPSILON and ice is None and shield is None
                and deployed is None and impact_barrier is None):
            molten_surface = collision.cast_ray(rocket.position, heading, t + EPSILON)
            if molten_surface is not None and molten_surface.normal[1] < molten_core.GROUND_NORMAL:
                rocket.left -= contact_time
                if rocket.left <= EPSILON:
                    self._landed(rocket, continuation_point=point)
                    return 0.0
                molten_core.rebound(rocket, point, molten_surface.normal)
                return min(max(0.0, duration - contact_time), rocket.left)
        self._landed(rocket, continuation_point=point)
        molten_core.projectile_impact(self, rocket, molten_surface, struck)
        damage_point = (_along(point, heading, 1e-4)
                        if struck is None and rocket.direct is not None
                        and (rocket.direct.loadout == SOLDIER_WEAPON_LOADOUT
                             or (rocket.source_graph == 0x024F and not rocket.charge_eligible)
                             or rocket.source_graph in (0x23, 0x7B, 0x2B)) else point)
        surface_normal = (0.0, 0.0, 0.0)
        if (rocket.source_graph in (0x23, 0x7B, 0x2B) and struck is None and wall is not None
                and abs(t - wall) <= EPSILON and ice is None and shield is None and deployed is None
                and impact_barrier is None):
            surface = collision.cast_ray(rocket.position, heading, t + EPSILON)
            if surface is not None:
                surface_normal = surface.normal
        hero_torbjorn.projectile_impact(self, rocket, point, struck)
        self._explode(rocket, point, impact_barrier=impact_barrier, ice=ice, shield=shield,
                      deployed=deployed, struck=struck, broken_particle_barriers=broken_particle_barriers,
                      damage_point=damage_point, incident_direction=heading, surface_normal=surface_normal)
        return 0.0

    def _barrage_defense_eye(self, owner):
        heights = {0x0400000000000225: (1.2, .84), 0x0400000000002AB1: (2.1, 1.75)}
        pair = heights.get(owner.hero.body)
        if pair is None:
            return self._eye(owner)
        mover = owner.mover
        x, y, z = mover.position
        height = expr.f32(pair[mover.crouched])
        return (x, expr.f32(y + expr.f32(height + mover.spring)), z)

    def _reflect_barrage(self, rocket, target, duration, contact_time):
        """Retain the delivered eye-origin, seed replay and restarted curve clock."""
        self._landed(rocket)
        if rocket.deflections >= deflection.MAX_DEFLECTIONS:
            return 0.0
        remaining = max(0.0, duration - contact_time)
        direction = aim(target.owner.mover.yaw, target.owner.mover.pitch)
        if rocket.barrage_source is not None:
            owner, volley, shot = rocket.barrage_source
            instance = volley.instance
            script = owner.body_script
            if (script is not None and script.component is instance.component
                    and script.component.instances.get(instance.id) is instance):
                seed = barrage_motion.seed_for_shot(0, instance.id, volley.index, volley.counter, shot)
                direction = barrage_motion.shot_direction(volley, direction, seed)
        origin = self._barrage_defense_eye(target.owner)
        old_scale = rocket.boost_credit[1] if rocket.boost_credit is not None else 1.0
        definitions = {}
        for name in ("direct", "blast"):
            definition = getattr(rocket, name)
            if definition is not None:
                definition = replace(definition, amount=definition.amount / old_scale, boost_credit=None)
                definition = self._support_hit(target.owner, definition)
            definitions[name] = definition
        credit = (definitions["direct"].boost_credit if definitions["direct"] is not None else
                  definitions["blast"].boost_credit if definitions["blast"] is not None else None)
        redirected = replace(
            rocket, shooter=target.owner, team=target.team, position=origin, direction=direction,
            left=rocket.deflect_lifetime, shot=None, flown=0.0, travelled=0.0,
            motion_phase_offset=TICK - remaining, last_tick=rocket.last_tick,
            reflected=True, source_body=target.entity, source_entity=target.entity,
            charge_eligible=True, damage_charge=None, boost_credit=credit,
            hit_entities=set(), deflections=rocket.deflections + 1, ended=None, **definitions,
        )
        self.rockets.append(redirected)
        if remaining > EPSILON:
            continuation = self._fly_slice(redirected, remaining, redirected.last_tick)
            if isinstance(continuation, Rocket):
                return continuation
        return redirected

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
        if rocket.penetrated is not None:
            # Source0007 uses the real static MOVER sphere sweep for wide Fire
            # Strike, including contacts before an exact centre-ray endpoint.
            swept = collision.cast_sphere(rocket.position, direction, step, rocket.penetration_radius, MOVER)
            if swept is not None and (wall is None or swept.distance < wall.distance):
                wall = swept
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
            absorption_admitted=rocket.matrix_absorbable,
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
                if returned[4].instance.graph.index == absorption.GRASP:
                    self.absorption.absorbed(returned[4], rocket.direct, rocket.blast, rocket.source_entity)
                elif returned[4].instance.graph.index == 0x02CC:
                    amount = sum(hit.amount for hit in (rocket.direct, rocket.blast) if hit is not None)
                    self._absorbed(returned[4], amount, rocket.source_entity)
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
        """Delivered native shield/body contacts in physical travel order."""
        contacts = []
        raised = [actor for actor in self.barriers.enemies(rocket.shooter, team=rocket.team)
                  if isinstance(actor, reinhardt_barrier.RaisedBarrier)]
        native_bodies = {actor.body for actor in raised}
        for actor in raised:
            distance = actor.trace(rocket.position, direction, limit, rocket.penetration_radius)
            phase = actor.instance.states.get(17)
            key = (-actor.body, (self.replacement_ticks.get(actor.body), phase.counter if phase is not None else None))
            if (distance is not None and key not in rocket.penetrated
                    and (distance < limit or not stops)):
                contacts.append((distance, 0, actor.body, key, (distance, actor.owner, actor)))
        for actor in self.barriers.world.enemies(rocket.shooter, team=rocket.team):
            contact = self.barriers.world.trace(rocket.shooter, rocket.position, direction, limit,
                                               rocket.penetration_radius, team=rocket.team, bubbles=(actor,))
            key = (-actor.body, None)
            if contact is not None and key not in rocket.penetrated and (contact[0] < limit or not stops):
                contacts.append((contact[0], 0, actor.body, key, (contact[0], actor.owner, actor)))
        for actor in self.orisa_barriers.opponents(rocket.shooter, rocket.team):
            distance = self.orisa_barriers.geometry.cast(actor.position, actor.yaw, rocket.position,
                                                         direction, limit, rocket.penetration_radius)
            key = (-actor.entity, None)
            if distance is not None and key not in rocket.penetrated and (distance < limit or not stops):
                contacts.append((distance, 0, actor.entity, key, (distance, actor.owner, actor)))
        for actor in sigma_barrier.enemies(self, rocket.shooter, team=rocket.team):
            distance = actor.intersection(rocket.position, direction, limit, rocket.penetration_radius)
            key = (-actor.entity, None)
            if distance is not None and key not in rocket.penetrated and (distance < limit or not stops):
                contacts.append((distance, 0, actor.entity, key, (distance, actor.owner, actor)))
        for actor in self.photon_barriers.opponents(rocket.shooter, team=rocket.team):
            distance = self.photon_barriers.contact(actor, rocket.position, direction, limit, rocket.penetration_radius)
            key = (-actor.body, None)
            if distance is not None and key not in rocket.penetrated and (distance < limit or not stops):
                contacts.append((distance, 0, actor.body, key, (distance, actor.owner, actor)))
        for distance, owner, shield in self.brigitte.barrier_contacts(
                rocket.shooter, rocket.position, direction, limit,
                radius=rocket.penetration_radius, team=rocket.team):
            key = (-owner.body, (self.replacement_ticks.get(owner.body), shield.instance.states[30].counter))
            if key not in rocket.penetrated and (distance < limit or not stops):
                contacts.append((distance, 3, owner.body, key, shield))
        devices = self.orisa.superchargers
        if devices.geometry is not None:
            for actor in devices.opponents(rocket.shooter, rocket.team):
                distance = devices.geometry.cast(actor.position, actor.yaw, rocket.position,
                                                 direction, limit, rocket.penetration_radius)
                key = (actor.entity, None)
                if distance is not None and key not in rocket.penetrated and (distance < limit or not stops):
                    contacts.append((distance, 2, actor.entity, key, actor))
        for distance, entity, health in self.barrier.intersections(rocket.shooter, rocket.position, direction, limit, team=rocket.team):
            if entity in native_bodies:
                continue
            key = (entity, self.barrier.children[entity])
            if key not in rocket.penetrated and (distance < limit or not stops):
                contacts.append((distance, 0, entity, key, (distance, health, entity)))
        for target in self._enemies(rocket.shooter, team=rocket.team):
            key = (target.entity, self.replacement_ticks.get(target.entity))
            if key in rocket.penetrated:
                continue
            a, b = target.segment()
            radius = target.radius + rocket.penetration_radius
            distance = ray_capsule(rocket.position, direction, a, b, radius)
            if distance is not None and (distance < limit or (distance == limit and not stops)):
                contacts.append((distance, 1, target.entity, key, target))
        for distance, kind, _, key, target in sorted(contacts, key=lambda row: row[:3]):
            self._amplify_flight(rocket, direction, distance, start_tick, tick_per_distance)
            if kind == 0:
                _, owner, actor = target
                if isinstance(actor, reinhardt_barrier.RaisedBarrier) and self.is_dead(owner):
                    continue  # a nearer body hit can retire its carried shield
                rocket.penetrated.add(key)
                self._damage_contact_barrier(target, rocket.direct.amount, shooter=rocket.shooter)
            elif kind == 3:
                rocket.penetrated.add(key)
                self.brigitte.damage_barrier(target, rocket.direct.amount * self.orisa.superchargers.scale(rocket.shooter))
            elif kind == 2:
                rocket.penetrated.add(key)
                self.orisa.superchargers.damage(target, rocket.direct.amount * self.orisa.superchargers.scale(rocket.shooter))
            else:
                rocket.penetrated.add(key)
                self._apply_hit(
                    rocket.shooter, target, rocket.direct, rocket.direct.amount, False, self.tick,
                    world_tick=self.tick, source_entity=rocket.source_entity,
                )

    def _stick(self, rocket, point, elapsed_after_contact):
        rocket.position = point
        rocket.fuse_left = rocket.sticky_delay - elapsed_after_contact
        if rocket.fuse_left <= EPSILON:
            self._explode(rocket, point)
            self._landed(rocket)

    def _explode(self, rocket, point, *, impact_barrier=None, ice=None, shield=None, deployed=None,
                 struck=None, broken_particle_barriers=None, protected=(), world_shields=(), orisa_impact=None,
                 damage_point=None, incident_direction=None, surface_normal=(0.0, 0.0, 0.0),
                 native_impact_body=None, protected_out=None, victims_out=None):
        damage_point = point if damage_point is None else damage_point
        incident_direction = rocket.direction if incident_direction is None else incident_direction
        blast = rocket.blast
        if blast is not None and blast.healing:
            self._apply_blast_hits(rocket, self._blast_hits(
                rocket, point, self._ice_wall_blockers(), shield_los=False,
            ))
            return 0.0
        boost = self.orisa.superchargers.scale(rocket.shooter)
        source = _along(point, incident_direction, -0.0001)
        blockers = tuple((actor, manager)
                         for manager in (self.orisa_barriers, self.orisa.superchargers)
                         for actor in manager.opponents(rocket.shooter))
        if orisa_impact is not None:
            actor, manager = orisa_impact
            if rocket.direct is not None:
                manager.damage(actor, rocket.direct, rocket.flown * rocket.speed, multiplier=boost)
        if blast is not None and blast.amount > 0.0:
            for actor, manager in blockers:
                contact = point if (actor, manager) == orisa_impact else manager.geometry.closest(
                    actor.position, actor.yaw, point,
                )
                distance = math.dist(point, contact)
                if blast.splash(distance) > 0 and capsule_visible(self._collision(), source, contact, contact, .001):
                    manager.splash(actor, blast, distance, multiplier=boost)
        biotic = blast is not None and blast.effect == BIOTIC_GRENADE_EFFECT
        candidates = self._targets() if biotic else self._enemies(rocket.shooter)
        tank = (rocket.source_graph == 0x024F and not rocket.charge_eligible)
        pharah = blast is not None and blast.loadout in (0x0B9000000000000F, 0x0B9000000000000E, 0x0B9000000000000B)
        if (blast is not None and (pharah or blast.loadout == SOLDIER_WEAPON_LOADOUT
                or getattr(rocket, "pulse_bomb", False)
                or blast.loadout == 0x0B9000000000020B
                or (tank and getattr(getattr(rocket.shooter, "hero", None), "guid", None) == BASTION))
                and blast.self_scale > 0
                and rocket.source_entity == _source_entity(rocket.shooter)):
            candidates = list(candidates)
            candidates.extend(t for t in self._targets() if t.entity == rocket.source_entity)
        ice_damage = rocket.direct.amount if ice is not None and rocket.direct is not None else 0.0
        shield_damage = [(shield[1], rocket.direct.amount)] if shield and rocket.direct else []
        deployed_damage = rocket.direct.amount if deployed is not None and rocket.direct is not None else 0.0
        if blast is None or blast.amount <= 0.0:
            if pharah:
                self._pharah_impulses(rocket, damage_point, point, protected=protected, heading=incident_direction, normal=surface_normal)
            if ice is not None:
                self.walls.damage(ice[1], rocket.shooter, ice_damage, team=rocket.team)
            for barrier, amount in shield_damage:
                self.brigitte.damage_barrier(barrier, amount)
            if deployed is not None:
                sigma_barrier.damage_barrier(self, deployed[1], deployed_damage)
            return 0.0
        # Resolve all cover against the shields present at impact. A shield
        # breaking on this blast does not spill that same blast into its owner.
        shield_damage.extend(self.brigitte.blast_barriers(rocket.shooter, point, blast))
        deployed_splash = sigma_barrier.blast_contacts(
            self, rocket.shooter, point, blast, struck=deployed[1] if deployed is not None else None,
        )
        protected = set(protected)
        visible_from = _along(point, incident_direction, -0.0001)
        self.venom.explode(rocket.shooter, point, blast, visible_from=visible_from)
        fixed = tuple(shield for shield in world_shields if isinstance(shield, world_bubbles.FixedBubble))
        raised = tuple(s for s in world_shields if isinstance(s, barriers.reinhardt_barrier.RaisedBarrier))
        protected.update(self.barriers.world.explosion(
            rocket.shooter, point, blast, visible_from, forced=fixed,
        ))
        protected.update(self.barriers.reinhardt.explosion(
            rocket.shooter, point, blast, visible_from, raised,
            excluded=() if native_impact_body is None else (native_impact_body,),
        ))
        for bubble in self.barriers.enemies(rocket.shooter):
            if not isinstance(bubble, barriers.Bubble):
                continue
            distance = max(0.0, math.dist(point, bubble.centre()) - barriers.RADIUS)
            scalar = blast.splash(distance)
            visible_from = _along(point, incident_direction, -0.0001)
            centre = bubble.centre()
            if (scalar > 0
                    and capsule_visible(self._collision(), visible_from, centre, centre, barriers.RADIUS)):
                protected.add(bubble.body)
                self.barriers.damage(bubble, blast.amount * scalar, rocket.shooter)
        if protected_out is not None:
            protected_out.update(protected)
        victims = []
        for target in candidates:
            if target.entity in protected or immunity.wraith_active(target.owner, target.entity):
                continue
            if target.owner is not None and self.vitals_of(target).dead:
                continue
            a, b = target.segment()
            ab, ap = _sub(b, a), _sub(point, a)
            denominator = _dot(ab, ab)
            factor = max(0.0, min(1.0, _dot(ap, ab) / denominator)) if denominator > EPSILON else 0.0
            centre = _along(a, ab, factor)
            delta = _sub(centre, damage_point)
            centre_distance = math.sqrt(_dot(delta, delta))
            distance = max(0.0, centre_distance - target.radius)
            scalar = blast.splash(distance)
            if target.entity == rocket.source_entity:
                scalar *= blast.self_scale
            if scalar <= 0.0:
                continue
            centre = (a[0], min(b[1], max(a[1], point[1])), a[2])
            delta = _sub(centre, point)
            length = math.sqrt(_dot(delta, delta))
            if length > EPSILON:
                direction = tuple(value / length for value in delta)
                if not capsule_visible(self._collision(), point, a, b, target.radius):
                    continue
                reach = max(0.0, length - target.radius)
                if self.walls.cast(point, direction, reach) is not None:
                    continue
                if ((impact_barrier is not None and impact_barrier.trace(point, direction, length) is not None)
                        or self._barrier_hit(rocket.shooter, point, direction, length) is not None):
                    continue
                toward = _sub(centre, source)
                from_source = math.sqrt(_dot(toward, toward))
                if from_source > EPSILON and any(
                    manager.geometry.cast(actor.position, actor.yaw, source,
                                          tuple(axis / from_source for axis in toward), from_source) is not None
                    for actor, manager in blockers
                ):
                    continue
                if self.brigitte.barrier_ray(rocket.shooter, point, direction, length) is not None:
                    continue
                if sigma_barrier.barrier_intersection(
                    self, rocket.shooter, point, direction, max(0.0, length - target.radius),
                ) is not None:
                    continue
            victims.append((target, blast.amount * scalar))
        if ice is not None:
            self.walls.damage(ice[1], rocket.shooter, ice_damage + blast.amount * blast.splash(0.0),
                              team=rocket.team)
        for barrier, amount in shield_damage:
            self.brigitte.damage_barrier(barrier, amount)
        for barrier, amount in deployed_splash:
            sigma_barrier.damage_barrier(self, barrier, amount)
        if deployed is not None:
            amount = deployed_damage + blast.amount * blast.splash(0.0)
            sigma_barrier.damage_barrier(self, deployed[1], amount)
        if biotic:
            if not self.healing.grenade(rocket, point, victims=victims,
                                        broken_particle_barriers=broken_particle_barriers):
                self._biotic_impact(rocket.shooter, blast, victims, broken_particle_barriers)
        else:
            for target, amount in victims:
                landed = self._damage(
                    rocket.shooter, target, amount, False, blast.loadout, self.tick, world_tick=self.tick,
                    broken_particle_barriers=broken_particle_barriers, hit_tags=blast.tags,
                    source_body=rocket.source_body, boost_credit=rocket.boost_credit,
                    award_ult=rocket.charge_eligible and target.owner is not rocket.shooter,
                )
                if (blast.loadout == SOLDIER_WEAPON_LOADOUT and not self.vitals_of(target).dead
                        and (landed or defenses.invulnerable(target.owner))):
                    self._blast_impulse(target, point, blast, blast.impulse_scale(
                        max(0.0, segment_distance(point, *target.segment()) - target.radius),
                        own=target.entity == rocket.source_entity,
                    ))
                if (tank and landed and not self.vitals_of(target).dead
                        and not defenses.invulnerable(target.owner)
                        and not immunity.fortified(target.owner, target.entity)):
                    self._tank_impulse(target, damage_point, blast)
                if landed:
                    if victims_out is not None:
                        victims_out.add(target.entity)
                    self._hit_effect(target, blast.effect, shooter=rocket.shooter)
                    if rocket.accretion_source is not None and (
                        target is not struck or rocket.direct is None or rocket.direct.knockback is None
                    ):
                        self._apply_accretion_impulse(rocket, target, blast, point=point)

        if pharah:
            self._pharah_impulses(rocket, damage_point, point, protected=protected, heading=incident_direction, normal=surface_normal)

    def _pharah_impulses(self, rocket, impact, sight, *, protected=(), heading=None, normal=(0.0, 0.0, 0.0)):
        blast = rocket.blast
        heading = rocket.direction if heading is None else heading
        candidates = self._enemies(rocket.shooter)
        candidates.extend(t for t in self._targets() if t.owner is rocket.shooter)
        for target in candidates:
            own = target.owner is rocket.shooter
            if not isinstance(target.owner, bots.Bot) and not hasattr(target.owner, "mover"):
                continue  # deployables receive blast damage, not a character velocity response.
            if target.entity in protected or self.vitals_of(target).dead:
                continue
            if (defenses.fortify_scale(target.owner) is not None or immunity.wraith_active(target.owner, target.entity)
                    or moira.fading(target.owner) or self.pulse_bombs.recalling(target)
                    or defenses.cryo_protected(target.owner)):
                continue
            source_scale = blast.source_impulse_scale if own and target.entity == rocket.source_body else 0.0 if own else 1.0
            if not source_scale:
                continue
            distance = max(0.0, segment_distance(impact, *target.segment()) - target.radius)
            direction = None
            vertical_direction = 1.0
            if own:
                distance, direction = source_blast_geometry(target.feet, target.height, target.radius, impact, normal)
                vertical_direction = direction[1]
            horizontal, vertical = blast.impulse_scales(distance)
            if direction is not None and math.hypot(direction[0], direction[2]) <= EPSILON:
                horizontal = 0.0
            upward = blast.vertical_impulse * vertical * vertical_direction * source_scale
            if vertical > 0.0 and blast.vertical_impulse_minimum is not None:
                velocity_y = 0.0 if own else target.owner.velocity[1] if isinstance(target.owner, bots.Bot) else target.owner.mover.velocity[1]
                upward = max(upward, blast.vertical_impulse_minimum - velocity_y)
            flat = blast.horizontal_impulse * horizontal * source_scale
            if not (flat or upward):
                continue
            if own:
                origin = _along(impact, heading, -.2)
                x, y, z = target.feet
                nearest = (x, max(y, min(y + target.height, origin[1])), z)
                if not blast.rings or math.dist(origin, nearest) - target.radius >= blast.rings[-1][0]:
                    continue
                visible = blast.source_impulse_ignores_los or self._pharah_source_impulse_path(origin, target, rocket.shooter)
            else:
                visible = self._pharah_impulse_path(sight, target, rocket.shooter)
            if not visible:
                continue
            if direction is None:
                dx, dz = target.feet[0] - impact[0], target.feet[2] - impact[2]
            else:
                dx, _, dz = direction
            length = math.hypot(dx, dz)
            scale = flat / length if length > EPSILON else 0.0
            impulse = (dx * scale, upward, dz * scale)
            if isinstance(target.owner, bots.Bot):
                from ow174.game.collision import TEAM0, TEAM1

                category = MOVER if self.match.game_map.free_for_all or target.team not in (0, 1) else (TEAM0, TEAM1)[target.team]
                target.owner.apply_impulse(impulse, self.tick, self._collision(), category=category)
            else:
                target.owner.mover.add_impulse(impulse, self.tick)

    def _pharah_source_impulse_path(self, origin, target, shooter):
        """Delivered source prediction independently tests middle and nearest sight points."""
        x, bottom, z = target.feet
        nearest = (x, max(bottom, min(bottom + target.height, origin[1])), z)
        a, b = target.segment()
        for sample in ((x, bottom + target.height / 2, z), nearest):
            delta = _sub(sample, origin)
            reach = math.hypot(*delta)
            if reach <= EPSILON:
                return True
            direction = tuple(axis / reach for axis in delta)
            body = ray_capsule(origin, direction, a, b, target.radius)
            if body is not None:
                reach = min(reach, body)
            if self._pharah_impulse_path_clear(origin, direction, reach, shooter):
                return True
        return False

    def _pharah_impulse_path(self, origin, target, shooter):
        a, b = target.segment()
        centre = (a[0], max(a[1], min(b[1], origin[1])), a[2])
        delta = _sub(centre, origin)
        length = math.hypot(*delta)
        if length <= EPSILON:
            return True
        direction = tuple(axis / length for axis in delta)
        reach = max(0.0, length - target.radius)
        return self._pharah_impulse_path_clear(origin, direction, reach, shooter)

    def _pharah_impulse_path_clear(self, origin, direction, reach, shooter):
        if reach <= EPSILON:
            return True
        wall = ray_world(self._collision(), origin, direction, reach)
        return ((wall is None or wall >= reach - EPSILON)
                and self.walls.cast(origin, direction, reach - EPSILON) is None
                and self._barrier_hit(shooter, origin, direction, reach - EPSILON) is None
                and self._orisa_deployable_hit(shooter, origin, direction, reach - EPSILON) is None
                and self.brigitte.barrier_ray(shooter, origin, direction, reach - EPSILON) is None
                and sigma_barrier.barrier_intersection(self, shooter, origin, direction, reach - EPSILON) is None)

    def _accretion_hit(self, rocket: Rocket, target: Target, dealt: float) -> None:
        """Feed the tagged direct damage to the caster's authored control entry."""
        body, identifier = rocket.accretion_source
        shooter = rocket.shooter
        script = shooter.body_script
        if shooter.body != body or script is None or dealt <= 0 or self.vitals_of(target).dead:
            return
        component = script.component
        instance = component.instances.get(identifier)
        if instance is None or instance.stopped or instance.graph.index != ACCRETION:
            return
        hit = instance.graph.node(9).field("m_6396149F")["m_modifyHealth"]
        tags = tuple(instance.evaluate(row["m_EE3055BD"]) for row in hit["m_148F3152"])
        message = expr.guid_of(instance.graph.node(17).field("m_gameMessage")["m_gameMessage"])
        runtime.send_instance_message(instance, message, {
            0x15: expr.Entity(target.entity), 0x13: dealt, 0x3D: tags,
        }, body)
        component.run_now()

    def _apply_accretion_impulse(self, rocket: Rocket, target: Target, hit: HitDef, *, point=None) -> None:
        strength = hit.knockback
        if strength is None or self.vitals_of(target).dead:
            return
        owner = target.owner
        horizontal, vertical, rings = strength
        dx, _, dz = rocket.direction
        distance = 0.0
        if point is not None:
            dx, dz = target.feet[0] - point[0], target.feet[2] - point[2]
            distance = max(0.0, segment_distance(point, *target.segment()) - target.radius)
        previous_radius = 0.0
        previous_xz, previous_y = rings[0][1:]
        for radius, xz, y in rings:
            if distance <= radius:
                width = radius - previous_radius
                share = (distance - previous_radius) / width if width > 0 else 0
                horizontal *= previous_xz + (xz - previous_xz) * share
                vertical *= previous_y + (y - previous_y) * share
                break
            previous_radius, previous_xz, previous_y = radius, xz, y
        else:
            return
        if not (horizontal or vertical):
            return
        length = math.hypot(dx, dz)
        scale = horizontal / length if length > EPSILON else 0.0
        impulse = dx * scale, vertical, dz * scale
        if isinstance(owner, bots.Bot):
            owner.impulse(
                impulse, self.tick, self._collision(), free_for_all=self.match.game_map.free_for_all,
            )
        else:
            owner.mover.add_external_impulse(impulse)

    def _biotic_impact(self, shooter, blast: HitDef, victims, broken_particle_barriers=None) -> None:
        """Reconstruct 0C71 relation dispatch for the normal hero profile."""
        self.brigitte._heal_effects(self.brigitte.inspire)
        self.brigitte._heal_effects(self.brigitte.repairs)
        for target, amount in victims:
            vitals = self.vitals_of(target)
            ally = target.owner is shooter or (
                not self.match.game_map.free_for_all and target.team == shooter.team
            )
            scalar = amount / blast.amount
            if ally:
                # 0B99 replaces the old boost before the 100-point impact heal.
                vitals.healing_boost = (0.0, 0.0)
                given = vitals.heal(100.0 * scalar * vitals.healing_factor(self.now))
                if given:
                    if not isinstance(shooter, bots.Bot):
                        shooter.stats.add(stats.HEALING, given)
                        self._charge_ana(shooter, given)
                    self._sync_body_health(target.owner, vitals)
                vitals.healing_boost = self.now + BIOTIC_BOOST_DELAY, self.now + BIOTIC_DURATION
            elif self._damage(
                shooter, target, 60.0 * scalar, False, blast.loadout, self.tick, world_tick=self.tick,
                broken_particle_barriers=broken_particle_barriers,
            ) and not vitals.dead:
                # 0C72 owns 60 damage and a zero incoming-healing multiplier.
                vitals.healing_block = self.now, self.now + BIOTIC_DURATION
                self._cleanse_biotic_block(target.owner, vitals)

    def _cleanse_biotic_block(self, owner, vitals: Vitals | None) -> None:
        if getattr(getattr(owner, "hero", None), "guid", None) == 0x02E0000000000068:
            return  # Native Pet application owns Zarya's original cleanse event.
        if vitals is None or self.now >= vitals.healing_block[1]:
            return
        if self._personal_barrier_pet(owner) is not None:
            vitals.healing_block = vitals.healing_block[0], self.now
            return
        component = getattr(getattr(owner, 'body_script', None), 'component', None)
        if not isinstance(component, runtime.Component):
            return
        for key in BIOTIC_CLEANSE_VARS:
            slot = component.entity_var(key, create=False)
            if slot is not None and expr.truthy(slot.value()):
                vitals.healing_block = vitals.healing_block[0], self.now
                break

    def _tank_impulse(self, target: Target, point, blast: HitDef) -> None:
        """Delivered Tank rings resolve independent horizontal and vertical coefficients."""
        a, b = target.segment()
        nearest = (a[0], max(a[1], min(point[1], b[1])), a[2])
        delta = _sub(nearest, point)
        distance = max(0.0, math.sqrt(_dot(delta, delta)) - target.radius)
        previous = None
        for radius, flat, up in blast.impulse_rings:
            if distance < radius:
                if previous is not None:
                    inner_radius, inner_flat, inner_up = previous
                    share = (distance - inner_radius) / max(0.0001, radius - inner_radius)
                    flat = inner_flat + (flat - inner_flat) * share
                    up = inner_up + (up - inner_up) * share
                x, y, z = delta
                length, ground = math.hypot(x, y, z), math.hypot(x, z)
                impulse = (x / ground * blast.horizontal_impulse * flat if ground > EPSILON else 0.0,
                           y / length * blast.vertical_impulse * up if length else 0.0,
                           z / ground * blast.horizontal_impulse * flat if ground > EPSILON else 0.0)
                if any(impulse):
                    if isinstance(target.owner, bots.Bot):
                        from ow174.game.collision import TEAM0, TEAM1

                        category = (MOVER if self.match.game_map.free_for_all or target.team not in (0, 1)
                                    else (TEAM0, TEAM1)[target.team])
                        target.owner.apply_impulse(impulse, self.tick, self._collision(), category=category)
                    else:
                        target.owner.mover.apply_impulse(impulse)
                return
            previous = radius, flat, up

    def _blast_impulse(self, target: Target, point, blast: HitDef, scalar: float) -> None:
        """Scoped radial/upward Helix force; native resistance/order remain open."""
        self._radial_impulse(target, point, blast.horizontal_impulse * scalar, blast.vertical_impulse * scalar)

    def _radial_impulse(self, target: Target, point, horizontal: float, vertical: float) -> None:
        if defenses.fortify_scale(target.owner) is not None:
            return
        dx, dz = target.feet[0] - point[0], target.feet[2] - point[2]
        length = math.hypot(dx, dz)
        scale = horizontal / length if length > EPSILON else 0.0
        response = defenses.knockback_scale(target.owner)
        impulse = tuple(value * response
                        for value in (dx * scale, vertical, dz * scale))
        if not all(math.isfinite(value) for value in impulse):
            raise ValueError("invalid authored blast impulse")
        if not any(impulse):
            return
        if isinstance(target.owner, bots.Bot):
            from ow174.game.collision import TEAM0, TEAM1

            category = MOVER if self.match.game_map.free_for_all or target.team not in (0, 1) else (TEAM0, TEAM1)[target.team]
            target.owner.apply_impulse(impulse, self.tick, self._collision(), category=category)
        else:
            mover = target.owner.mover
            mover.add_external_impulse(impulse)
            if mover.velocity[1] > 0:
                mover.fall = 0.0

    def _landed(self, rocket: Rocket, *, release: bool = True, ground_point=None, continuation_point=None) -> None:
        """Finish authority and release or retire the predicted copy."""
        if rocket in self.rockets:
            self.rockets.remove(rocket)
            if rocket.source_graph == 0x00B9 and continuation_point is not None:
                rocket.position = continuation_point
            if release and rocket.continuation is not None:
                if continuation_point is not None:
                    rocket.position = continuation_point
                born = self.tick if rocket.started_tick is None else rocket.started_tick + rocket.flown / TICK
                self.dragons.append(dragon.Flight(
                    rocket.shooter, rocket.source_entity or _source_entity(rocket.shooter), rocket.team,
                    rocket.position, rocket.direction, rocket.continuation, born,
                ))
            if rocket.source_graph in (0x0CCB, 0x0BD7, 0x0CF5):
                self.ability_impacts.append(rocket)
            self.orisa.landed(rocket)
            if release and rocket.spawn == graviton.FIELD_ENTITY:
                self.zarya_gravitons.landed(rocket, rocket.position, TICK)
        if rocket.shot is not None:
            if release and rocket.shot.spawn == projectiles.FIELD_ENTITY:
                at = self.tick if rocket.last_tick is None else rocket.last_tick
                self.projectiles.finish_field_flight(rocket.shot, at, ground_point)
            elif release:
                self.projectiles.flown(rocket.shot, rocket.flown)
            else:
                self.projectiles.intercepted(rocket.shot, self.tick)

    def heal_area(self, healer, centre, radius: float, amount: float, *, source_body=None, ticks=None) -> None:
        """Heal the healer and his teammates (everyone's own team in a free-for-all is his alone) whose body
        is within `radius` of `centre` (unverified: to the body's capsule)."""
        for target in self._targets():
            if isinstance(target.owner, (actors.Deployable, ball_mines.Mine)):
                continue
            ally = self._ally(healer, target)
            if target.owner is not healer and not ally:
                continue
            a, b = target.segment()
            if segment_distance(centre, a, b) - target.radius > radius:
                continue
            if not capsule_visible(self._collision(), centre, a, b, target.radius):
                continue
            seconds = 0.0 if ticks is None else (ticks[1] - ticks[0] + 1) * TICK
            self._heal_target(healer, target, amount, source_entity=source_body,
                              seconds=seconds, end_tick=None if ticks is None else ticks[1])

    def _heal(self, healer, target: Target, amount: float, *, source_body=None) -> float:
        return self._heal_target(healer, target, amount, source_entity=source_body)

    @staticmethod
    def _write_scene_health(component, vitals: Vitals) -> None:
        component.set(OWNER_HEALTH, float(vitals.total))

    def _sync_body_health(self, owner, vitals: Vitals) -> None:
        component = self._scene_component(owner) if isinstance(owner, bots.Bot) else None
        if component is not None:
            self._write_scene_health(component, vitals)
            return
        if isinstance(owner, (actors.Deployable, ball_mines.Mine)):
            owner.health = vitals.total
        script = getattr(owner, "body_script", None)
        if script is not None:
            maximum = vitals.maximum
            if getattr(getattr(owner, "hero", None), "guid", None) == doomfist.HERO:
                # Delivered0DDD has a separate capped temporary pool; its HUD
                # maximum stays the body's intrinsic declared health.
                maximum = vitals.base_maximum
            script.set_health(vitals.total, maximum)

    def _barrier_hit(self, shooter, origin, direction, length):
        trace = getattr(getattr(self.match, 'scene_host', None), 'trace_barrier', None)
        return trace(shooter, origin, direction, length) if trace is not None else None

    def ray_obstruction(self, origin, direction, length):
        terrain = ray_world(self._collision(), origin, direction, length)
        ice = self.walls.cast(origin, direction, length if terrain is None else terrain)
        return ice[0] if ice is not None else terrain

    def _collision(self):
        from ow174.game.collision import CollisionError

        world = self.match.collision_world
        if world is None:
            raise CollisionError("match collision is not ready")
        return world

    # --- damage and death --------------------------------------------------------------------------

    def _team_damage(self, shooter, amount):
        if self.defender_damage_scale != 1.0 and shooter.team == 0:
            return amount * self.defender_damage_scale
        return amount

    def _damage_barrier(self, shooter, barrier, amount, tags):
        self.match.scene_host.damage_barrier(barrier, self._team_damage(shooter, amount), tags)

    @staticmethod
    def _advance_protected_phases(component: runtime.Component) -> None:
        """Supply a scene-side handoff for four authored zero-delay wrappers."""
        pending = []
        for instance in component.instances.values():
            spec = PROTECTED_PHASES.get(instance.graph.index)
            if spec is None:
                continue
            wrapper = instance.states.get(spec[0])
            if (isinstance(wrapper, nodes.ConditionalTimer) and wrapper.active and not wrapper.ending
                    and wrapper.node.field('m_7C8AD485') is None
                    and not expr.truthy(wrapper.evaluate(wrapper.node.field('m_ED97A97C'), watch=False))):
                pending.append(wrapper)
        for wrapper in pending:
            wrapper.request_finish()
        if pending:
            component.run_now()

    @staticmethod
    def _invulnerable(owner, *, phases: frozenset[int] | None = None) -> bool:
        from ow174.game import defenses

        if (phases is None or 0x026A in phases) and defenses.call_mech_immune(owner):
            return True
        component = getattr(getattr(owner, 'body_script', None), 'component', None)
        if not isinstance(component, runtime.Component) or component.owner_dead:
            return False
        fight = getattr(getattr(component.world, 'match', None), 'combat', None)
        if phases is None and fight is not None and fight.meteors.protected(component.entity):
            return True
        for instance in component.instances.values():
            if instance.graph.index == mei.CRYO and mei.cryo_world_expired(owner):
                continue
            spec = PROTECTED_PHASES.get(instance.graph.index)
            if spec is None or (phases is not None and instance.graph.index not in phases):
                continue
            if instance.graph.index == 0x017B:
                service = getattr(getattr(getattr(component.world, "match", None), "combat", None), "zenyatta", None)
                if service is not None and service.stale_ultimate_flag(owner):
                    continue
            phase = instance.states.get(spec[1])
            if phase is not None and phase.active and not phase.ending:
                return True
        return False

    @staticmethod
    def _deflect_state(owner, *, at_tick=None) -> runtime.State | None:
        if getattr(getattr(owner, 'hero', None), 'guid', None) != 0x02E0000000000029:
            return None
        component = getattr(getattr(owner, 'body_script', None), 'component', None)
        if not isinstance(component, runtime.Component) or component.owner_dead:
            return None
        for instance in component.instances.values():
            if instance.graph.index != 0x03A3:
                continue
            state = instance.states.get(10)
            if state is None or not state.active or state.ending:
                continue
            if at_tick is not None and deflection.window(instance, at_tick) is None:
                continue
            return state
        return None

    @staticmethod
    def _deflect_cosine(state: runtime.State) -> float:
        angle = _number(state.evaluate(state.node.field('m_025D0C39'), watch=False), 0.0)
        return math.cos(math.radians(angle))

    def _deflects(self, target: Target, origin) -> bool:
        """Check facing on the supported melee contacts."""
        owner = target.owner
        state = self._deflect_state(owner, at_tick=self.tick)
        if state is None:
            return False
        toward = _sub(origin, self._eye(owner))
        distance = math.sqrt(_dot(toward, toward))
        facing = aim(owner.mover.yaw, owner.mover.pitch)
        return _dot(facing, toward) >= distance * self._deflect_cosine(state)

    def _trace_deflection(self, shooter, origin, direction, reach, *, view_tick=None):
        from ow174.game.interception import box_contact

        best = None
        for target in self._enemies(shooter):
            owner = target.owner
            state = self._deflect_state(owner, at_tick=self.tick)
            if state is None:
                continue
            facing = aim(owner.mover.yaw, owner.mover.pitch)
            if -_dot(facing, direction) + EPSILON < self._deflect_cosine(state):
                continue
            shown = target if view_tick is None else self.seen(target, view_tick)
            if shown is None:
                continue
            distance = box_contact(target, state, origin, direction, reach, shown=shown)
            if distance is not None and (best is None or distance < best[0]):
                best = distance, target
        return best

    def _trace_absorption(
        self, shooter, definition, origin, direction, reach, *, grasp=False, view_tick=None, admitted=None, matrix_only=False,
    ):
        from ow174.game.interception import box_contact

        matrix = (admitted if admitted is not None else
                  definition in self.matrix_definitions if self.matrix_definitions is not None
                  else projectile_tags.absorbable(definition))
        if admitted is not None or self.matrix_definitions is not None:
            grasp = matrix
        if matrix_only:
            grasp = False
        if not matrix and not grasp:
            return None
        nearest = None
        for target in self._enemies(shooter):
            hero = getattr(target.owner, "hero", None)
            if matrix and (isinstance(target.owner, bots.Bot)
                           or getattr(hero, "body", None) == 0x0400000000000407):
                from ow174.game import matrix as matrix_service

                shown = target if view_tick is None else self.seen(target, view_tick)
                distance = (None if shown is None else
                            matrix_service.trace(shown, origin, direction, reach, tick=self.tick))
                if distance is not None and (nearest is None or distance < nearest[0]):
                    state = next(deflection._active_states(target.owner.body_script, 0x02CC, 3), None)
                    nearest = distance, state
                continue
            if matrix and getattr(hero, "body", None) == 0x0400000000000407:
                graph_index, state_index = 0x02CC, 3
            elif grasp and getattr(hero, "guid", None) == 0x02E000000000023B:
                graph_index, state_index = 0x1D02, 1
            else:
                continue
            script = target.owner.body_script
            if script is None or script.retired or script.component.owner_dead:
                continue
            shown = target if view_tick is None else self.seen(target, view_tick)
            if shown is None:
                continue
            for instance in script.component.instances.values():
                if instance.graph.index != graph_index or instance.stopped:
                    continue
                state = instance.states.get(state_index)
                if (state is not None and state.active and not state.ending
                        and state.node.cls == "STUStatescriptStateDeflectProjectiles"):
                    distance = box_contact(target, state, origin, direction, reach, shown=shown)
                    if distance is not None and (nearest is None or distance < nearest[0]):
                        nearest = distance, state
        return nearest

    @staticmethod
    def _absorbed(state: runtime.State, amount: float, source: int) -> None:
        instance = state.instance
        if instance.graph.index == 0x02CC:
            deflection.reflected_projectile(state, source, amount)
        elif instance.graph.index == 0x1D02:
            instance.write((expr.INSTANCE, 6584), amount)
            instance.follow(state.node, "m_160B5DBE", state)
            instance.component.run_now()

    def grant_grasp_shield(self, instance: runtime.Instance, node) -> None:
        """Host the self-targeted 1D03 request from Grasp's authored exit."""
        entity = instance.component.entity
        if (instance.evaluate(node.field("m_script")) != expr.Asset(0x0580000000001D03)
                or instance.evaluate(node.field("m_targets")) != expr.Entity(entity)):
            return
        state = instance.states.get(1)
        if state is None or not state.counter or instance.component.owner_dead:
            return
        target = next((target for target in self._targets() if target.entity == entity), None)
        script = getattr(target.owner, "body_script", None) if target is not None else None
        if script is None or script.component is not instance.component:
            return
        vitals = self.vitals_of(target)
        activation = instance.id, state.counter
        shield = vitals.temporary_shield
        if vitals.dead or (shield is not None and shield.activation == activation):
            return
        params = {graph.guid(entry["m_B5051BCE"]): expr.to_float(instance.evaluate(entry["m_value"]))
                  for entry in node.field("m_params") or ()}
        amount, cap = params[0x0A4000000000002A], params[0x0A400000000003D1]
        delay = expr.to_float(instance.evaluate(instance.graph.node(71).field("m_timeout")))
        rate = expr.to_float(instance.evaluate(instance.graph.node(70).field("m_modifyHealth")["m_amount"]))
        if not all(math.isfinite(value) and value >= 0.0 for value in (amount, cap, delay, rate)):
            return
        old = shield.amount if shield is not None else 0.0
        current = old + min(amount, max(0.0, cap - old))
        if shield is not None:
            shield.activation = activation
        if current > old:
            vitals.temporary_shield = TemporaryShield(
                current, current, instance.component.now / 1000 + delay, rate, activation,
            )
            vitals.changed.add(SHIELDS_POOL)
            self._sync_body_health(target.owner, vitals)

    def _decay_temporary_shields(self) -> None:
        for player in self.match.players:
            vitals = self.vitals.get(player.body)
            if vitals is None or vitals.dead or vitals.temporary_shield is None:
                continue
            shield = vitals.temporary_shield
            if shield.amount <= 0.0 or self.now <= shield.decay_from:
                continue
            amount = (self.now - shield.decay_from) * shield.rate
            shield.decay_from = self.now
            if vitals.spend_temporary_shield(amount) > 0:
                self._sync_body_health(player, vitals)

    @staticmethod
    def _personal_barrier_pet(owner) -> runtime.State | None:
        if getattr(getattr(owner, 'hero', None), 'guid', None) != 0x02E0000000000068:
            return None
        component = getattr(getattr(owner, 'body_script', None), 'component', None)
        if not isinstance(component, runtime.Component) or component.owner_dead:
            return None
        for instance in component.instances.values():
            if instance.graph.index == 0x0244:
                pet = instance.states.get(2)  # node8 opens Pet13F4 for the authored Chase lifetime
                if pet is not None and pet.active and not pet.ending:
                    return pet
        return None

    def projected_barrier_targets(self, body: int, state: runtime.State) -> tuple:
        """Reconstruct the authored ally search using the current aim and static collision."""
        caster = next((player for player in self.match.players
                       if not player.gone and player.has_body and player.body == body), None)
        if (caster is None or self._collision() is None or self.match.game_map.free_for_all
                or self.is_dead(caster) or self.is_stunned(caster)):
            return ()
        radius = _number(state.evaluate(state.node.field("m_radius"), watch=False), 0.0)
        scale = state.instance.component.entity_var(9595, create=False)
        value = scale.value() if scale is not None else None
        angle = 10.0 * (expr.to_float(value) if value is not None else 1.0)
        if not math.isfinite(radius) or radius <= 0 or not 0 <= angle <= 180:
            return ()
        minimum = math.cos(math.radians(angle))
        origin, direction = self._eye(caster), aim(caster.mover.yaw, caster.mover.pitch)
        candidates = []
        for target in self._targets():
            if (target.owner is caster or target.team != caster.team or self.vitals_of(target).dead
                    or self._active_projected_barrier(target.entity)):
                continue
            script = target.owner.body_script
            if script is not None:
                flags = (script.component.entity_var(key, create=False) for key in (6600, 11264))
                if any(slot is not None and expr.truthy(slot.value()) for slot in flags):
                    continue
            centre = (target.feet[0], target.feet[1] + target.height / 2, target.feet[2])
            delta = _sub(centre, origin)
            distance = math.sqrt(_dot(delta, delta))
            if not EPSILON < distance <= radius:
                continue
            alignment = _dot(direction, delta) / distance
            if alignment < minimum:
                continue
            wall = ray_world(self._collision(), origin, tuple(axis / distance for axis in delta), distance)
            if wall is None or wall >= distance - EPSILON:
                candidates.append((-alignment, distance, target.entity))
        return (expr.Entity(min(candidates)[2]),) if candidates else ()

    def _active_projected_barrier(self, entity):
        barrier = self.projected_barriers.get(entity)
        return barrier if barrier is not None and barrier.health > 0 and self.now < barrier.until else None

    def _sync_projected_barrier(self, caster, script) -> None:
        for instance in script.component.instances.values():
            if instance.graph.index != 0x025C or instance.stopped:
                continue
            pet, effect = instance.states.get(10), instance.states.get(18)
            if pet is None or not pet.active or effect is None or not effect.active:
                continue
            key = caster.body, instance.id
            if self.projected_casts.get(key) == pet.counter:
                continue
            remaining = instance.vars.get(215)
            seconds = expr.to_float(remaining.value()) if remaining is not None else 0.0
            if not math.isfinite(seconds) or seconds <= 0:
                continue
            self.projected_casts[key] = pet.counter
            recipients = instance.evaluate(effect.node.field("m_targets"))
            recipients = (recipients,) if isinstance(recipients, expr.Entity) else recipients
            if not isinstance(recipients, tuple) or self.match.game_map.free_for_all:
                continue
            capacity = self._particle_barrier_capacity()
            if capacity <= 0:
                continue
            for target in self._targets():
                if (target.owner is caster or target.team != caster.team
                        or expr.Entity(target.entity) not in recipients or self.vitals_of(target).dead
                        or self._active_projected_barrier(target.entity)):
                    continue
                self.projected_barriers[target.entity] = ProjectedBarrier(
                    caster.body, self.now + seconds, capacity,
                )
                vitals = self.vitals_of(target)
                # Actual native Pet application owns the recipient cleanse.

    @staticmethod
    def _particle_barrier_capacity() -> float:
        pool = graph.graph(0x0256)
        if pool is None:
            return 0.0
        return _number(expr.evaluate(pool.node(0).field("m_amount"), expr.Context()), 0.0)

    def _barrier_energy(self, caster, loss: float, attacker: int) -> None:
        script = caster.body_script
        if script is None or script.retired or script.component.fault is not None or self.is_dead(caster):
            return
        component = script.component
        params = {0x2A: loss, 0x12: expr.Entity(attacker)}
        if runtime.send_message(component, 0x0240000000000146, params, caster.body):
            component.run_now()

    def _absorb_personal_barrier(self, target: Target, amount: float, attacker: int) -> float | None:
        pet = self._personal_barrier_pet(target.owner)
        if pet is None:
            return None
        activation = pet.instance.id, pet.counter
        prior = self.personal_barriers.get(target.entity)
        if prior is not None and prior[:2] == activation:
            health = prior[2]
        else:
            # 0244 node9 launches0256 on the pet. Its HealthPool node0
            # evaluates to200 with the default controller configuration.
            health = self._particle_barrier_capacity()
            if health <= 0:
                return None
        remaining = max(0.0, health - amount)
        if amount >= health:
            self.personal_barriers.pop(target.entity, None)
            # Pet's onEnd runs node13, which stops the ability and applies
            # cooldown via node7. The breaking hit is absorbed in full.
            pet.request_finish()
            pet.instance.component.run_now()
        else:
            self.personal_barriers[target.entity] = (*activation, remaining)
        self._barrier_energy(target.owner, min(health, amount), attacker)
        return remaining

    def _absorb_projected_barrier(self, target: Target, amount: float, attacker: int) -> float | None:
        barrier = self._active_projected_barrier(target.entity)
        if barrier is None:
            return None
        loss = min(barrier.health, amount)
        barrier.health = max(0.0, barrier.health - amount)
        caster = next((player for player in self.match.players
                       if not player.gone and player.has_body and player.body == barrier.source), None)
        if caster is not None:
            self._barrier_energy(caster, loss, attacker)
        return barrier.health

    @staticmethod
    def _incoming_damage(target: Target, amount: float, *, tick=None) -> float:
        """Host live incoming-damage8243, including actual0CF4 Fortify's0.6 stack.

        Applying it before the shared armor consumer is server reconstruction;
        native combat-mod/armor ordering has not been recovered. Read without
        creating an absent entity variable or changing its authored lifetime.
        """
        script = getattr(target.owner, 'body_script', None)
        component = getattr(script, 'component', None)
        slot = component.entity_var(8243, create=False) if isinstance(component, runtime.Component) else None
        fallback = immunity.damage_scale(target.owner, target.entity)
        if fallback == 0.0:
            return 0.0
        scalar = _number(slot.value() if isinstance(slot, runtime.Var) else None, fallback)
        if not math.isfinite(scalar):
            scalar = fallback
        if orisa_fortify.historical_pending(target.owner, tick):
            # Remove only this endpoint's new Fortify multiplier. Keep other
            # authored incoming8243 factors in the combined consumer.
            fortify = defenses.fortify_scale(target.owner)
            if fortify is not None and fortify > 0:
                scalar /= fortify
        return amount * max(0.0, scalar)

    @staticmethod
    def _bastion_damage_scalar(owner) -> float:
        """Delivered Ironclad query; incoming8243 already applies it once."""
        if isinstance(owner, bots.Bot) or owner.hero.guid != BASTION or owner.body_script is None:
            return 1.0
        for instance in owner.body_script.component.instances.values():
            state = instance.states.get(124) if instance.graph.index == 0x012F else None
            if state is not None and state.active:
                modifier = _number(state.evaluate(state.node.field("m_50530EAB"), watch=False), 0.0)
                return max(0.0, 1.0 + modifier) if math.isfinite(modifier) else 1.0
        return 1.0

    def _charge_echo_health_event(self, player, amount: float, *, source_body=None) -> None:
        """Mount health-event charge for Echo and the hero body currently copied by Echo."""
        script = getattr(player, 'body_script', None)
        if (script is not None and not script.retired
                and (source_body is None or script.component.entity == source_body)
                and (player.hero.guid == 0x02E0000000000206 or self.echo_duplicates.active(player))):
            script.gain_ult(amount)

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

    def _heavy_victim_mover(self, owner):
        if owner in self.match.players:
            return owner.mover
        actor = self.companions.actor_for(getattr(owner, "entity", 0))
        return actor.mover if actor is not None and actor.bot is owner and not owner.dead else None

    def heavy_capture_target(self, state) -> None:
        attacker = self._npc_melee_attacker(state)
        victim = state.evaluate(state.node.field("m_targets"), watch=False)
        resource = state.evaluate(state.node.field("m_script"), watch=False)
        if (attacker is None or not isinstance(victim, expr.Entity)
                or resource != expr.Asset(0x0580000000001407)):
            return
        target = next((target for target in self._enemies(attacker) if target.entity == victim.id), None)
        if target is None or self._heavy_victim_mover(target.owner) is None:
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
        victims = (*self.match.players, *(actor.bot for actor in self.companions.active.values()))
        for player in victims:
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
        if target is None or self._heavy_victim_mover(target.owner) is None:
            return
        context = _HeavyTargetContext(instance, attacker)
        destination = expr.evaluate(node.field("m_AF774F7D"), context)
        yaw = _number(expr.evaluate(node.field("m_E3779CD0"), context))
        if not isinstance(destination, expr.Vec3) or yaw is None or not math.isfinite(yaw):
            return
        mover = self._heavy_victim_mover(target.owner)
        position = tuple(destination)
        distance = math.dist(mover.position, position)
        if not math.isfinite(distance):
            return
        # Sweep the released capsule so the requested pose cannot cross solid cover.
        mover._move_to(TICK, position, distance / TICK + 1)
        mover.yaw = (round(yaw * 65536 / 360) + 32768) % 65536 - 32768

        if isinstance(target.owner, bots.Bot):
            target.owner.position, target.owner.velocity = tuple(mover.position), tuple(mover.velocity)
            target.owner.moved_tick = self.tick

    def _release_heavy_companion(self, source):
        # Original companion carry ends at impact; Player native recovery stays unchanged.
        for actor in tuple(self.companions.active.values()):
            script = actor.bot.body_script
            if script is None:
                continue
            for application in tuple(script.component.instances.values()):
                if application.graph.index == 0x1407 and application.source_entity() == source:
                    script.component.destroy(application)

    def advance_heavy_capture(self, component) -> None:
        """Sweep the source and captured player, then feed contact into the authored transition."""
        instance = next((i for i in component.instances.values() if i.graph.index == 0x1408), None)
        if instance is None:
            return
        phase = instance.find_var(runtime.INSTANCE_SCOPE, 59, create=False)
        if phase is not None and phase.value() == 2:
            self._release_heavy_companion(component.entity)
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
        target = next((target for target in self._enemies(attacker) if target.entity == victim.id), None)
        player = target.owner if target is not None else None
        victim_mover = self._heavy_victim_mover(player) if player is not None else None
        script = player.body_script if victim_mover is not None else None
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
        victim_mover.use(world=self._collision())
        victim_goal = _along(victim_mover.position, travelled, 1.)
        victim_mover._move_to(TICK, victim_goal, speed + 1)
        if isinstance(player, bots.Bot):
            player.position, player.velocity = tuple(victim_mover.position), tuple(victim_mover.velocity)
            player.moved_tick = component.frame
        if max(math.dist(attacker.position, goal), math.dist(victim_mover.position, victim_goal)) > .03:
            attacker.velocity = (0., 0., 0.)
            instance.write(expr.lvalue(contact.node.field("m_26DA7CF8")), True)
            drive.try_complete()
            component.run_now()
            phase = instance.find_var(runtime.INSTANCE_SCOPE, 59, create=False)
            if phase is not None and phase.value() == 2:
                self._release_heavy_companion(component.entity)
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
                if self.companions.actor_for(target.entity) is not None:
                    reinhardt_charge.lateral_impulse(attacker.position, target, state, forward,
                                                     fight=self, frame=frame)
                else:
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
        if query.pos == 96:
            selected = state.instance.find_var(runtime.INSTANCE_SCOPE, 536, create=False)
            selected = expr.to_entity(selected.value()) if selected is not None else 0
            actor = self.companions.actor_for(selected)
            if actor is not None and actor.bot.body_script is not None and any(
                application.graph.index == 0x1407 and application.source_entity() == attacker.entity
                for application in actor.bot.body_script.component.instances.values()
            ):
                return (expr.Entity(selected),)  # Retain the source-owned victim until the drive ends.
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
            companion = self.companions.actor_for(target.entity)
            if (getattr(target.owner, "body_script", None) is None
                    or (target.owner not in self.match.players and companion is None)):
                continue
            if companion is not None:
                if companion.owner.entity != attacker.attack_target:
                    continue
                flag = companion.bot.body_script.component.entity_var(6601, create=False)
                turn = (round(attacker.yaw) - companion.charge_yaw + 32768) % 65536 - 32768
                if ((flag is not None and flag.value() is True)
                        or (companion.phase == "charge" and abs(turn) > 16384)):
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
        *, world_tick: int | None = None, broken_particle_barriers: set[int] | None = None,
        award_ult: bool = True, deadeye: bool = False, hit_reaction: bool = False,
        hit_tags: tuple[int, ...] = (), armour_scalar: float | None = None,
        source_body: int | None = None, beam: bool = False, maximum_damage: float | None = None,
        source_scaled: bool = False, damage_charge=None, boost_credit=None,
        reflected_reaper_shell: bool = False, source_entity: int | None = None, armour_reduction: bool = True,
        shield_drain: bool = False,
    ) -> bool:
        """Apply damage and report whether a body hit can carry an attached effect."""
        if source_entity is not None:
            if source_body is not None and source_body != source_entity:
                raise ValueError("conflicting damage source_body and source_entity")
            source_body = source_entity
        if isinstance(target.owner, ball_mines.Mine):
            mine = target.owner
            if self.ball_mines.devices.get(mine.body) is not mine or mine.removing or amount <= 0:
                return False
            vitals = self.vitals_of(target)
            if vitals.dead or shield_drain:
                return False  # Original Mine EMP disables its controller without health damage.
            if not source_scaled:
                from ow174.game import nano
                amount = self._team_damage(shooter, amount) * self.orisa.superchargers.scale(shooter)
                amount *= nano.offense(self, _source_entity(shooter) if source_body is None else source_body)
            if maximum_damage is not None:
                amount = min(amount, maximum_damage)
            dealt = vitals.damage(amount)
            self._sync_body_health(mine, vitals)
            if dealt and not isinstance(shooter, (bots.Bot, SceneHazard)):
                self._tell_hit(shooter, target, dealt, KILLING if vitals.dead else 0, frame)
            return dealt > 0
        if not armour_reduction:
            armour_scalar = 1.0  # delivered periodic damage bypasses per-tick armour reduction.
        if isinstance(target.owner, bots.Bot) and target.owner not in self.match.bots:
            return False
        vitals = self.vitals_of(target)
        if (vitals.dead or self.now < vitals.invulnerable_until or amount <= 0.0 or self._invulnerable(target.owner)
                or reaper.intangible(target.owner)
                or immunity.wraith_active(target.owner, target.entity) or moira.fading(target.owner)
                or self.pulse_bombs.recalling(target) or self.zenyatta.ultimate(target.owner)
                or self._has_protection(target, DAMAGE_IMMUNITY_STATES)):
            return False
        if loadout in (FLAIL_LOADOUT, WHIP_LOADOUT) and self._deflects(target, self._eye(shooter)):
            return False
        # A broken Particle Barrier still covers this body's remaining
        # contacts from the same rocket, including its splash and status.
        if broken_particle_barriers is not None and target.entity in broken_particle_barriers:
            return False
        from ow174.game import nano

        attacker = _source_entity(shooter) if source_body is None else source_body
        if not source_scaled and not shield_drain:
            amount = self._team_damage(shooter, amount) * self.orisa.superchargers.scale(shooter)
            amount *= nano.offense(self, attacker)
        component = self._scene_component(shooter) if isinstance(shooter, bots.Bot) else None
        if component is not None and not source_scaled and not shield_drain:
            amount = component.world.outgoing_combat_damage(shooter.entity, amount, hit_tags)
        if maximum_damage is not None:
            amount = min(amount, maximum_damage)
        remaining = barriers.protect_body(self, target, amount, attacker=attacker)
        if remaining is None:
            remaining = self._absorb_personal_barrier(target, amount, attacker)
        if remaining is None:
            remaining = self._absorb_projected_barrier(target, amount, attacker)
        if remaining is not None:
            if remaining <= 0 and broken_particle_barriers is not None:
                broken_particle_barriers.add(target.entity)
            return False
        bubble = self.barriers.for_target(target.owner)
        if bubble is not None:
            self.barriers.damage(bubble, amount, shooter)
            return False
        self.zenyatta.regenerate_before_damage(target, vitals)
        if not shield_drain:
            amount *= self.zenyatta.boost(target)
        self._recover_shields(target.owner, vitals, self.now - TICK)
        base_before = vitals.base_total
        temporary_before = vitals.temporary_shields
        if getattr(getattr(target.owner, "hero", None), "guid", None) == lucio.HERO:
            lucio.advance_passive_charge(target.owner, self.tick - 1, due_only=True)
        if self.now < vitals.nano_until and not shield_drain:
            amount *= 0.5
        adaptive = vitals.extra.get(("adaptive", moira.ADAPTIVE_SHIELD_TAG))
        shield_before = adaptive.now if adaptive is not None else 0.0
        sound_pool = vitals.extra.get(sound_barrier.POOL_KEY)
        sound_before = sound_pool.now if sound_pool is not None else 0.0
        minimum = self._minimum_health(target, vitals)
        if shield_drain:
            base = 0.0
            dealt = vitals.drain_shields(amount)
        else:
            base = (vitals.capped_damage(self._incoming_damage(target, amount / boost_credit[1], tick=world_tick if world_tick is not None else self.tick), minimum,
                                        armour_scalar=armour_scalar, beam=beam)
                    if boost_credit is not None else 0.0)
            dealt = vitals.damage(self._incoming_damage(target, amount, tick=world_tick if world_tick is not None else self.tick), minimum,
                                  armour_scalar=armour_scalar, beam=beam)
        if dealt <= 0.0:
            return False
        if symmetra.owns_shield_recovery(target.owner):
            vitals.last_damage_at = vitals.shield_accounted_at = self.now
        self.zenyatta.damaged(target)
        if award_ult:
            self.junkrat.charge.damage(shooter, target, dealt, loadout, attacker)
        if isinstance(target.owner, actors.Deployable):
            self._sync_body_health(target.owner, vitals)
            if vitals.dead or vitals.now[HEALTH_POOL] <= EPSILON:
                if self.junkrat.lookup(target.entity) is target.owner:
                    vitals.died_at = self.now
                    vitals.death_tick = self.tick if world_tick is None else world_tick
                    vitals.killer = attacker
                    if not isinstance(shooter, bots.Bot):
                        self._tell_hit(shooter, target, dealt, KILLING, frame)
                self.destroy_deployable(target.entity)
            elif self.junkrat.lookup(target.entity) is target.owner and not isinstance(shooter, bots.Bot):
                self._tell_hit(shooter, target, dealt, 0, frame)
            return True
        if (not shield_drain and target.owner is not shooter
                and (self.match.game_map.free_for_all or target.team != getattr(shooter, "team", None))):
            vitals.ult_healing_deficit = min(
                vitals.base_maximum - vitals.base_total,
                vitals.ult_healing_deficit + max(0.0, base_before - vitals.base_total),
            )
        hero_torbjorn.turret_damaged(self, target)
        extra = max(0.0, dealt - base) if boost_credit is not None else 0.0
        if isinstance(shooter, bots.Bot) and (credit := self._credit_player(shooter)) is not None:
            if target.owner is not shooter:
                terminal = (vitals.pool_current(HEALTH_POOL) <= EPSILON
                            and (isinstance(target.owner, bots.Bot) or not self.echo_duplicates.active(target.owner)))
                flags = (CRITICAL if critical else 0) | (KILLING if terminal else 0)
                self._tell_hit(credit, target, dealt, flags, frame)
                credit.stats.add(stats.HERO_DAMAGE, dealt)
            if award_ult:
                from ow174.game import dva

                dva.charge_damage(credit, shooter, dealt - extra)
        if award_ult:
            self.sombra_charge.damage(shooter, target, dealt, hit_tags)
        if boost_credit is not None and extra > EPSILON:
            from ow174.game.script import charge

            caster, scale = boost_credit
            impact = frame if world_tick is None else world_tick
            historical = self.support_effects.historical_boosts(caster.entity, impact, pending=True) is not None
            charge.boosted(caster, target.entity, dealt, extra, scale - 1.0,
                           valkyrie_frame=impact if historical else None)
        adaptive_loss = shield_before - (adaptive.now if adaptive is not None else 0.0)
        charge_owner = self._credit_player(shooter)
        if (award_ult and getattr(getattr(charge_owner, "hero", None), "guid", None) == hero_torbjorn.HERO):
            torbjorn_charge.damage(self, charge_owner, target, dealt, loadout, adaptive_loss)
        if (award_ult and getattr(getattr(charge_owner, "hero", None), "guid", None) == symmetra_charge.HERO):
            symmetra_charge.damage(charge_owner, target, dealt)
        if award_ult:
            self.baptiste.damage_charge(shooter, target, dealt, source_body=source_body)
        self.brigitte.damaged(shooter, loadout)
        if award_ult and shooter is not target.owner:
            self.brigitte.charge(shooter, dealt)
            self.zenyatta.charge(shooter, dealt - extra)
        killed = vitals.pool_current(HEALTH_POOL) <= EPSILON
        victim = target.owner
        copy_break = killed and not isinstance(victim, bots.Bot) and self.echo_duplicates.active(victim)
        if killed and not copy_break:
            self.hammer_contacts.pop(target.entity, None)
            vitals.set_over_armour(None)
            vitals.now[HEALTH_POOL] = 0.0
            vitals.died_at = self.now
            vitals.death_tick = self.match.tick if world_tick is None else world_tick
            vitals.killer = attacker
            vitals.clear_sleep()
            self.close_input_lock(target.entity, SLEEP_DART_EFFECT)
        elif self.tick < vitals.sleep_until:
            wake = max(vitals.sleep_wake_floor, self.tick + SLEEP_WAKE_RECOVERY_TICKS)
            vitals.sleep_until = min(vitals.sleep_until, wake)
            self.record_input_lock(target.entity, SLEEP_DART_EFFECT, vitals.sleep_until)
        if not isinstance(shooter, (bots.Bot, SceneHazard)):
            flags = (CRITICAL if critical else 0) | (KILLING if killed and not copy_break else 0)
            if shooter is not target.owner:
                self._tell_hit(shooter, target, dealt, flags, frame)
                shooter.stats.add(stats.HERO_DAMAGE, dealt)
            if award_ult:
                if source_body is None or source_body == shooter.body:
                    orisa_charge.damage(shooter, target, dealt, loadout, adaptive_shield_loss=adaptive_loss)
                    lucio.charge_damage(shooter, target, base_before - vitals.base_total, loadout)
                moira.gain_combat_charge(
                    shooter, dealt, body=source_body, damage_target=target, damage_tags=hit_tags,
                    adaptive_loss=adaptive_loss,
                )
            if (award_ult and victim is not shooter
                    and (self.match.game_map.free_for_all or target.team != shooter.team)):
                self._charge_echo_health_event(shooter, dealt, source_body=source_body)
            enemy = victim is not shooter and (
                self.match.game_map.free_for_all or target.team != shooter.team
            )
            script = getattr(shooter, "body_script", None)
            hero = shooter.hero.guid
            current_source = source_body is None or source_body == shooter.body
            if current_source:
                self._reaping(shooter, target, dealt)
            if award_ult and enemy and current_source:
                source_points = dealt - extra - max(0.0, sound_before - (sound_pool.now if sound_pool is not None else 0.0))
                reaper.charge_damage(shooter, source_points, target,
                                     tuple(expr.Asset(tag) for tag in hit_tags))
                if reflected_reaper_shell and source_points > 0:
                    from ow174.game import damage_charge as authored_charge

                    instance = authored_charge.admitted_source(script)
                    if instance is not None:
                        authored_charge.apply(script, instance, source_points, target,
                                              tuple(expr.Asset(tag) for tag in hit_tags))
            if award_ult and enemy and current_source and hero == regeneration.MERCY:
                from ow174.game.script import charge

                charge.damaged(script, target.entity, dealt, damage_charge, frame=frame,
                               extra=max(0.0, dealt - base) if boost_credit is not None else 0.0)

            if award_ult and enemy and current_source and hero in (0x02E0000000000003, 0x02E0000000000007):
                from ow174.game import ultimate_charge

                ultimate_charge.damage(shooter, target, dealt, hit_tags)

            if award_ult and enemy and current_source and hero == 0x02E00000000000DD:
                from ow174.game import mei_ultimate

                mei_ultimate.grant(shooter, dealt, loadout=loadout)
            sandbox_cassidy = (hero == 0x02E0000000000042 and isinstance(victim, bots.Bot)
                               and self.match.game_map.map_guid == RIALTO_RETRIBUTION)
            if (award_ult and enemy and script is not None and current_source and hero not in (winston.HERO, 0x02E0000000000003, 0x02E0000000000007)
                    and not reflected_reaper_shell
                    and hero not in (0x02E00000000000DD, symmetra_charge.HERO, hero_torbjorn.HERO, reaper.HERO, regeneration.MERCY, 0x02E0000000000206, 0x02E0000000000195, 0x02E000000000013E, 0x02E0000000000020, baptiste.HERO, lucio.HERO, moira.MOIRA)
                    and not (hero == sombra_charge.SOMBRA and self.sombra_charge.enabled(script))
                    and not (hero == ball.HERO and loadout == 0x0B900000000001ED)
                    and not self.echo_duplicates.active(shooter) and not sandbox_cassidy):
                points = dealt - adaptive_loss if hero in (0x02E000000000013B, 0x02E0000000000029) else dealt
                if hero == doomfist.HERO:
                    points = max(0.0, base_before - vitals.base_total)
                if hero == ball.HERO:
                    points = dealt - (temporary_before - vitals.temporary_shields)
                if hero == 0x02E000000000006E:
                    definition = victim.body if isinstance(victim, bots.Bot) else getattr(
                        getattr(victim, "hero", None), "body", getattr(victim, "definition", None))
                    script.gain_ult_charge(points, target_definition=definition, recipient=target.entity)
                else:
                    script.gain_ult_charge(points)
            if award_ult and enemy and current_source and hero == winston.HERO:
                definition = (victim.body if isinstance(victim, bots.Bot)
                              else getattr(getattr(victim, "hero", None), "body", None))
                winston.charge_damage(script, max(0.0, base_before - vitals.base_total),
                                      target.entity, definition, getattr(victim, "body_script", None))
            if enemy and current_source and hero == 0x02E0000000000029:
                tick = self.match.tick if world_tick is None else world_tick
                self.genji_assists.setdefault(target.entity, {})[shooter.body] = (shooter, tick)
            if award_ult and isinstance(victim, bots.Bot):
                from ow174.game import mccree

                mccree.award_sandbox_charge(self.match, shooter, target.owner, dealt)
        elif award_ult and component is not None and target.team == 0:
            from ow174.game import junkenstein_ai

            if shooter.hero == junkenstein_ai.SYMMETRA:
                junkenstein_ai.summoner_damage_event(component, target.entity, dealt, hit_tags)
        if copy_break:
            if self.echo_duplicates.restore(victim):
                return False
            # If allocation failed, preserve ordinary death rather than retaining a zero-health body.
            vitals.died_at = self.now
            vitals.death_tick = self.match.tick if world_tick is None else world_tick
            vitals.killer = attacker
        if killed:
            self._retire_source_controls(target.entity)
            self._genji_eliminations(target, shooter, vitals.death_tick)
        script = getattr(victim, "body_script", None)
        if (not killed and script is not None and getattr(script, "hero", None) == sombra_camo.HERO
                and isinstance(script.component, runtime.Component)):
            impact = self.tick if world_tick is None else world_tick
            if script.world_frame_anchor is not None:
                anchor_tick, anchor_frame = script.world_frame_anchor
                impact = anchor_frame + (impact-anchor_tick)*round(TICK*1000)//script.frame_ms
            params = {0x003D: tuple(expr.Asset(tag) for tag in hit_tags)}
            # Original012E dates Pharah impact messages for the other receivers too.
            at = (impact * script.frame_ms if world_tick is not None
                  and loadout in (0x0B9000000000000F, 0x0B9000000000000B) else None)
            if not sombra_camo.queue_damage(script, params, attacker, impact=impact, default_time=at):
                runtime.send_message(script.component, sombra_camo.RECEIVED_DAMAGE, params, attacker, at=at)
        if killed and isinstance(victim, bots.Bot):
            # The NPC death script owns its terminal health change and death event ordering.
            self._bot_died(shooter, victim, critical, loadout, vitals.death_tick, deadeye=deadeye, source=attacker)
            return True
        self._sync_body_health(victim, vitals)
        if not killed and not isinstance(victim, bots.Bot) and victim.body_script is not None:
            regeneration.damaged(victim.body_script, _source_entity(shooter), self.tick)
        if isinstance(victim, bots.Bot) and isinstance(victim.body_script, BotScript):
            from ow174.game import npc_shields

            npc_shields.received_damage(self, victim, vitals, attacker)
            victim.body_script.show_damage_nameplate(_source_entity(shooter))
            if (hit_reaction or deadeye) and not self.is_stunned(victim):
                # Ordinary hit presentation does not interrupt AI or move the victim.
                deadline = self.tick + math.ceil(TALON_DAMAGE_REACTION / TICK)
                self._begin_npc_hit_reaction(target, TALON_DAMAGE_REACTION, deadline, restart=False, shooter=shooter)
        if killed:
            self._died(shooter, victim, critical, loadout, source=attacker)
        elif loadout == MEI_SPRAY_LOADOUT:
            self._mei_frost_hit(shooter, target, self.tick if world_tick is None else world_tick)
        return True

    def _genji_eliminations(self, target, killer, tick):
        """Deliver authored reset entries for reconstructed kill/damage assists.

        0093/parameter15 and169F/parameter45 both lead through the scoped body
        predicate to SetVar61=0. Full native elimination event fan-out, credit
        windows and payload/filter semantics remain unverified.
        """
        entity = target.entity
        for body, (player, damaged) in self.genji_assists.pop(entity, {}).items():
            if (not 0 <= tick - damaged <= GENJI_ASSIST_TICKS or player.body != body
                    or player.gone or player not in self.match.players
                    or player.hero.guid != 0x02E0000000000029):
                continue
            script = player.body_script
            if script is None or script.retired or script.component.fault is not None:
                continue
            if player is not killer:
                player.stats.add(stats.ELIMINATIONS)
                self._kill_notice(player, target.owner)
            instance = next((i for i in script.component.instances.values() if i.graph.index == 0x01DB), None)
            if instance is not None:
                message, parameter = (
                    (0x0240000000000093, 0x15) if player is killer else (0x024000000000169F, 0x45)
                )
                runtime.send_instance_message(instance, message, {parameter: expr.Entity(entity)}, body)
                script.component.run_now()

    def _dva_blast_shields(self, source, origin, radius):
        """Sample finite shield surfaces before applying the authored blast curve."""
        selected = []
        collision = self._collision()
        for actor in self.barriers.enemies(source):
            if isinstance(actor, reinhardt_barrier.RaisedBarrier):
                def visible(point):
                    return capsule_visible(collision, origin, point, point, .001)
                distance = min(actor.surface_distances(origin, radius, visible), default=None)
                manager = self.barriers.reinhardt
            else:
                centre = actor.centre()
                size = actor.radius if isinstance(actor, world_bubbles.FixedBubble) else barriers.RADIUS
                length = math.dist(origin, centre)
                distance = max(0.0, length - size)
                surface = _along(centre, _sub(origin, centre), size / length) if length > size else origin
                if not capsule_visible(collision, origin, surface, surface, .001):
                    distance = None
                manager = self.barriers.world if isinstance(actor, world_bubbles.FixedBubble) else self.barriers
            if distance is not None and distance <= radius:
                selected.append(((manager, actor), distance))
        return selected

    def _dva_shield_damage(self, shield, amount, source):
        manager, actor = shield
        if manager is self.barriers:
            manager.damage(actor, amount, source)
        else:
            manager.damage(actor, amount)

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
        return shooter is None or self._barrier_contact(shooter, origin, direction, reach, bubbles=True) is None

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
                shields = self._dva_blast_shields(source, origin, radius)
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
                self._dva_shield_damage(shield, _number(amount, 0.0), source)

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
                    hit_tags=health_tags(state, state.node.field("m_modifyHealth")),
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
            self._radial_impulse(target, origin, horizontal * planar, vertical * lift)
            return

    def _credit_player(self, actor):
        if not isinstance(actor, bots.Bot):
            return actor
        if getattr(actor, "combat_owner", None) is not None:
            owner = actor.combat_owner
            return owner if owner in self.match.players and not owner.gone else None
        player = self.match._message_player(actor.source_body) if actor.owner else None
        return player if player is not None and player.entity == actor.owner else None

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
        from ow174.game import reaper_blossom

        reaper_blossom.advance(self, player, register=register)

    def _reaping(self, shooter, target: Target, dealt: float) -> None:
        ratio = reaper.reaping_ratio(shooter)
        if (not ratio or target.owner is shooter
                or (not self.match.game_map.free_for_all and target.team == shooter.team)):
            return
        vitals = self.vitals.get(shooter.body)
        if vitals is None or vitals.dead:
            return
        recipient = self.player_target(shooter)
        if recipient is not None:
            self._heal(shooter, recipient, dealt * ratio)

    def _tell_hit(self, shooter, target: Target, dealt: float, flags: int, frame: int) -> None:
        """Send the shooter's hit marker and target health bar using the hit's source frame."""
        value = {
            "+0x78": {"+0x0": {"+0x0": target.entity}, "+0x4": max(1, round(dealt)), "+0x8": flags},
            "+0x84": frame & 0xFFFFFFFF,
        }
        self._queue_notice(shooter, target.owner, target.entity, HIT, value)

    def _kill_notice(self, killer, victim, *, source: int | None = None) -> None:
        """25005 to the killer: "ELIMINATED: <name>" (1126.07C; +0x89 assist would be 01F0.07C, +0x88 a
        score shown when above 0). Its handler (0x7FF7895D2B50) reads only +0x84, +0x88 and +0x89."""
        entity = _source_entity(victim)
        notice = {
            "+0x78": 0,
            "+0x80": {"+0x0": _source_entity(killer) if source is None else source},
            "+0x84": {"+0x0": entity},
            "+0x88": 0,
            "+0x89": False,
        }
        self._queue_notice(killer, victim, entity, KILL_NOTICE, notice)

    def _queue_notice(self, viewer, actor, entity: int, msg_id: int, value: dict) -> None:
        client = getattr(viewer, "client", None)
        if client is None:
            return
        if not isinstance(actor, (bots.Bot, SceneHazard, ball_mines.Mine)) and actor.body == entity:
            self.match.ensure_remote_body(viewer, actor)
        client.queue_combat_notice(entity, msg_id, value)

    @staticmethod
    def _death_parameters(killer, critical: bool, loadout, *, source: int | None = None) -> dict:
        return {
            KILLER: expr.Entity(_source_entity(killer) if source is None else source),
            CRIT: bool(critical),
            LOADOUT: expr.Asset(loadout) if loadout else None,
            ASSISTS: (),
        }

    def _defer_deadeye_notice(self, killer, victim) -> bool:
        from ow174.game import mccree

        script = killer.body_script
        if (self.match.game_map.map_guid != RIALTO_RETRIBUTION or victim.spawner.get('manual_spawn') is not True
                or killer.hero.guid != mccree.HERO or script is None or killer.client is None):
            return False
        root = next((i for i in script.component.instances.values() if i.graph.index == mccree.ULTIMATE), None)
        if root is None:
            return False
        self.deadeye_notices.append((killer, victim, script, root.id, script.frame, self.match.tick + 125))
        return True

    def _flush_deadeye_notices(self) -> None:
        # The original client can leave its first-person presentation latched if
        # ELIMINATED arrives while Deadeye's exit states are still active.
        pending = self.deadeye_notices[:]
        self.deadeye_notices.clear()
        for killer, victim, script, root_id, shot_frame, deadline in pending:
            if killer.client is None or killer.body_script is not script:
                continue
            base = script.stream.base
            root = script.component.instances.get(root_id)
            ready = root is None or (base is not None and base.k > shot_frame and not any(
                base.states.get((root_id, root.graph.node(pos).state), (None, False))[1]
                for pos in (17, 37, 49, 90)))
            if ready or self.tick >= deadline:
                self._kill_notice(killer, victim)
            else:
                self.deadeye_notices.append((killer, victim, script, root_id, shot_frame, deadline))

    def _bot_died(self, killer, bot: bots.Bot, critical: bool, loadout, tick: int, *, deadeye=False, source=None) -> None:
        self.hooks.retire(bot.entity)
        hero_torbjorn.turret_killed(self, bot)
        source = _source_entity(killer) if source is None else source
        self.sombra_controls.retire(bot.entity)
        self.sound_barriers.retire(bot.entity)
        self.support_effects.retire(bot.entity)
        self.lucio_auras.retire(bot.entity)
        self.knockbacks.retire(bot.entity)
        self.projected_barriers.pop(bot.entity, None)
        component = self._scene_component(bot)
        self.junkrat.release_trap_victim(bot.entity)
        self.clear_pins(bot.entity)
        self.stuns.pop(bot.entity, None)
        self._end_npc_hit_reaction(bot.entity)
        self.zarya_gravitons.release(bot)
        bot.died(tick)
        if component is None and not bot.retain_corpse:
            self.respawns.append((self.now + BOT_RESPAWN, bot))
        if not isinstance(killer, bots.Bot):
            killer.stats.add(stats.ELIMINATIONS)
            if not deadeye or not self._defer_deadeye_notice(killer, bot):
                self._kill_notice(killer, bot, source=source)
        elif (credit := self._credit_player(killer)) is not None:
            credit.stats.add(stats.ELIMINATIONS)
            self._kill_notice(credit, bot, source=source)
        log.info("[game] %s: %s killed %s", self.match.label(), killer.name, bot.name)
        if component is not None:
            component.owner_dead = True
            if self.match.card == JUNKENSTEIN_SHOCKING_SURPRISE:
                # 2B33/10 watches the body death flag before sending 14B6.
                component.set(478, True)
            component.world.notify_entity_filter_change(bot.entity)
            component.discard_button_presses()
            component.shots.clear()
            component.set(OWNER_HEALTH, 0.0)
            runtime.send_message(component, DIED, self._death_parameters(killer, critical, loadout, source=source),
                                 source)
            return
        script = bot.body_script
        if script is None:
            return
        try:
            params = self._death_parameters(killer, critical, loadout, source=source)
            if isinstance(script, BotScript):
                tick = max(tick + bot.clock_offset, script.frame)
            event = script.die(tick, params, source)
            if isinstance(script, BotScript):
                self._consume_bot_death_volleys(bot, script)
            if bot.owner:
                from ow174.game import body_entities

                body_entities.queue_demech_return(self.match, bot)
        except Exception:
            script.retire()
            bot.body_script = None
            log.exception("[game] %s: %s's body script failed on %X", self.match.label(), bot.name, DIED)
            return
        for viewer in self.match.players:
            if viewer.client is not None:
                viewer.client.queue_bot_death(bot.entity, event)

    def _consume_bot_death_volleys(self, bot, script):
        from ow174.game.script.bot import DEATH_VOLLEY_GRAPH

        shots, script.component.shots[:] = list(script.component.shots), []
        for state, number, time, activation, *launch in shots:
            if ((state.instance.graph.index, state.node.pos) == (DEATH_VOLLEY_GRAPH, 0)
                    and state.allows_dead()):
                self._shoot(bot, None, state, number, time, activation=activation)

    def _advance_bot_death(self, bot: bots.Bot, tick: int) -> None:
        if self._scene_component(bot) is not None:
            return
        script = bot.body_script
        if script is None:
            return
        try:
            events = script.advance_death(tick + bot.clock_offset, int(BOT_RESPAWN / TICK))
            if isinstance(script, BotScript):
                self._consume_bot_death_volleys(bot, script)
        except Exception:
            script.retire()
            bot.body_script = None
            log.exception("[game] %s: %s's death script timer failed", self.match.label(), bot.name)
            return
        for event in events:
            for viewer in self.match.players:
                if viewer.client is not None:
                    viewer.client.queue_bot_death_update(bot.entity, event)

    def _died(self, killer, victim, critical: bool, loadout, *, source=None) -> None:
        self.hooks.retire(victim.body)
        self.ball_contacts.pop(victim.body, None)
        ball.cancel_grapple(self, victim)
        self.pile_air_locks.pop(victim.body, None)
        """Dispatch notices, authored death, and the respawn."""
        from ow174.game import body_entities

        if victim.hero.guid == barrier.REINHARDT:
            reinhardt_barrier.sync(self, victim)
            self.barriers.reinhardt.sync(victim)
        self._interrupt_reaper(victim.body, victim)
        from ow174.game import sigma_flux

        sigma_flux.interrupt_flux_lift(self, victim)
        self.translocators.retire_body(victim.body)
        self.winston_jump.cancel(victim.body)
        if victim.body_script is not None:
            photon_barrier.interrupt(victim.body_script)
            self.primal_punches.interrupt(victim.body_script)
            self._winston_health(victim, victim.body_script)

        for charge in self.charges.values():
            if charge.pinned == victim.body:
                charge.pinned = None
        self.charges.pop(victim.body, None)
        self.punches.dashes.pop(victim.body, None)
        for casts in (self.slams.casts, self.meteors.casts):
            cast = casts.get(victim.body)
            if cast is not None:
                cast.finished = True

        body_entities.retire_source(self.match, victim.entity, victim.body, keep_bombs=True)
        self.sombra_controls.retire(victim.body)
        self.sound_barriers.retire(victim.body)
        victim.clear_valkyrie_pose()
        victim.mover.script_data = None
        victim.mover.flight_scale = None
        script = victim.body_script
        pools = self.vitals.get(victim.body)
        death_tick = pools.death_tick if pools is not None and pools.death_tick is not None else self.tick
        boost_end = min(death_tick, script.frame + 1) if script is not None else None
        self.support_effects.retire(victim.body, boost_end_frame=boost_end)
        self.lucio_auras.retire(victim.body)
        self.knockbacks.retire(victim.body)
        self.soundwaves[:] = [wave for wave in self.soundwaves if wave.body != victim.body]
        wall_ride.cancel(victim)
        assassin_victim.retire_target(self, victim.body)
        self.junkrat.release_trap_victim(victim.body)
        self.clear_pins(victim.body)
        self.stuns.pop(victim.body, None)
        self.recalls.retire(victim.body)
        self.zenyatta.retire(victim.body)
        self.breathers.retire(victim.body)
        self.personal_barriers.pop(victim.body, None)
        self.projected_barriers.pop(victim.body, None)
        self.frost_sources.pop(victim.body, None)
        if self.frost_hits.pop(victim.body, None) is not None and victim.body_script is not None:
            self._write_frost(victim.body_script.component, 0.0)
        self.zarya_gravitons.release(victim)
        label = self.match.label()
        log.info("[game] %s: %s killed %s", label, killer.name, victim.name)
        victim.stats.add(stats.DEATHS)
        player_killer = not isinstance(killer, (bots.Bot, SceneHazard))
        source = _source_entity(killer) if source is None else source
        if isinstance(killer, bots.Bot) and (credit := self._credit_player(killer)) is not None:
            credit.stats.add(stats.ELIMINATIONS)
            self._kill_notice(credit, victim, source=source)
        if killer is not victim and player_killer:
            killer.stats.add(stats.ELIMINATIONS)
            self._kill_notice(killer, victim, source=source)
        if victim.client is not None:
            how = KILLED_BY if killer is not victim else 0
            notice = {"+0x78": how, "+0x7C": {"+0x0": source}}
            if how == KILLED_BY:
                self._queue_notice(victim, killer, source, DEATH_NOTICE, notice)
            else:  # the native "you died" event does not resolve a killer
                victim.client.queue_combat_notice(None, DEATH_NOTICE, notice)
        script = victim.body_script
        if script is not None:
            script.world.velocity_requests.clear()
            try:
                component = script.component
                for instance in list(component.instances.values()):
                    if instance.graph.index == 0x1407:
                        component.destroy(instance)
                params = self._death_parameters(killer, critical, loadout, source=source)
                if runtime.send_message(component, DIED, params, source):
                    # The server owns the death event. Run it before the observer snapshot even
                    # if the victim sends no further commands.
                    component.run_now()
                    if victim.hero.guid == 0x02E0000000000065:
                        script._report_shots()
                        self.command(victim, Command(self.tick, yaw=round(victim.mover.yaw),
                                                     pitch=round(victim.mover.pitch)))
            except Exception:
                log.exception("[game] %s: %s's body script failed on %X", label, victim.name, DIED)
        self.brigitte.died(victim)
        self.respawns.append((self.now + respawn_seconds(self.match), victim))

    def _respawn(self, who) -> None:
        if isinstance(who, bots.Bot):
            self._respawn_bot(who)
            return
        player = who
        if player.gone or not player.has_body or not self.is_dead(player):
            return
        if self.junkrat.tire.busy(player.body):
            self.respawns.append((self.now + RESPAWN_RETRY, player))
            return
        old_script = player.body_script
        points, pending = None, ()
        if old_script is not None and player.hero.guid in (0x02E00000000000DD, symmetra_charge.HERO, regeneration.MERCY, 0x02E000000000013B):
            points = old_script.component.entity_var(ULT_CHARGE).value()
            if player.hero.guid == regeneration.MERCY:
                pending = getattr(old_script.world, "pending_ult_credits", ())
        switched = (self.match.switch_hero(player, player.hero, preserve_ultimate=True)
                    if player.hero.guid == sombra_charge.SOMBRA else self.match.switch_hero(player, player.hero))
        if not switched:
            self.respawns.append((self.now + RESPAWN_RETRY, player))
            return
        if points is not None and player.body_script is not None:
            from ow174.game.script import charge

            script = player.body_script
            from ow174.game import mei_ultimate

            if player.hero.guid != 0x02E00000000000DD or not mei_ultimate.spawn_ready(script):
                script.set_ult_charge(points)
            if player.hero.guid == symmetra_charge.HERO:
                script.charge_clock = self.tick, script.component.now
            if player.hero.guid == regeneration.MERCY:
                charge.set_pending_credits(script.world, tuple(credit for credit in pending
                                                              if credit[0] > script.component.now))
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
            components = vitals.component(vitals.most)
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
