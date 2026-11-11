"""Scoped Discord consumer for Zenyatta's 104319 graph 0176."""

import math
from dataclasses import dataclass

from ow174.game import (
    barrier_pet, defenses, orisa_deployable, sigma_barrier, sombra_hack, winston_barrier, zarya_barrier,
)
from ow174.game.script import expr

HERO = 0x02E0000000000020
GRAPH = 0x0176
BUTTON = 8
RANGE, RECOVERY, GRACE = 40.0, .75, 3.0  # 0176 nodes 41, 43, 71
SCALE = 1.25  # reconstructed incoming modifier
CONE = math.cos(math.radians(10))


@dataclass
class Orb:
    owner: object
    buttons: int = 0
    ready: float = 0.0
    target: int | None = None
    visible_at: float = 0.0


def instance(player):
    script = player.body_script
    if script is None or script.hero != HERO:
        return None
    return next((i for i in script.component.instances.values() if i.graph.index == GRAPH), None)


def assign(orb, target):
    orb.target = target
    graph = instance(orb.owner)
    if graph is not None:
        graph.write((0, 1606), expr.Entity(target) if target is not None else 0)


def retire(fight, body):
    if (orb := fight.discord.pop(body, None)) is not None:
        assign(orb, None)
    for orb in fight.discord.values():
        if orb.target == body:
            assign(orb, None)


def visible(fight, player, target, *, acquisition=False):
    from ow174.game.combat import deployable_contact, ray_world

    origin = fight._eye(player)
    point = (target.feet[0], target.feet[1] + target.eye / 2, target.feet[2])
    delta = tuple(b - a for a, b in zip(origin, point, strict=True))
    length = math.hypot(*delta)
    if length <= 1e-9:
        return True
    direction = tuple(axis / length for axis in delta)
    wall = ray_world(fight._collision(), origin, direction, length)
    if ((wall is not None and wall < length) or fight.walls.cast(origin, direction, length) is not None):
        return False
    if not acquisition:
        return True
    shields = barrier_pet.active_colliders(fight, fight._enemies(player))
    body, team = player.body, player.team
    devices = (winston_barrier.colliders(fight, body, team)
               + orisa_deployable.colliders(fight, body, team)
               + sigma_barrier.colliders(fight, body, team))
    return (barrier_pet.contact(shields, origin, direction, length) is None
            and deployable_contact(devices, origin, direction, length) is None)


def cast(fight, player, command):
    from ow174.game.combat import aim

    if command is None or instance(player) is None:
        return
    orb = fight.discord.setdefault(player.body, Orb(player))
    pressed = command.buttons & ~orb.buttons
    orb.buttons = command.buttons
    if (not pressed & BUTTON or fight.now < orb.ready or defenses.transcendence(player)
            or sombra_hack.active(fight, player.body, fight.tick)):
        return
    origin, direction = fight._eye(player), aim(*command.aim())
    bubbles = {bubble.body for bubble in zarya_barrier.active(fight)}
    best = None
    for target in fight._enemies(player):
        if target.entity in bubbles or defenses.phase(target.owner)[0]:
            continue
        delta = (target.feet[0] - origin[0], target.feet[1] + target.eye - origin[1],
                 target.feet[2] - origin[2])
        distance = math.hypot(*delta)
        if not 0 < distance <= RANGE:
            continue
        alignment = sum(a * b for a, b in zip(delta, direction, strict=True)) / distance
        if alignment < CONE or not visible(fight, player, target, acquisition=True):
            continue
        key = (-alignment, distance, target.entity)
        if best is None or key < best:
            best = key
    if best is not None:
        assign(orb, best[2])
        orb.ready = fight.now + RECOVERY
        orb.visible_at = fight.now


def update(fight):
    if not fight.discord:
        return
    targets = {target.entity: target for target in fight._targets()}
    bubbles = {bubble.body for bubble in zarya_barrier.active(fight)}
    for orb in fight.discord.values():
        if orb.target is None:
            continue
        target = targets.get(orb.target)
        player = orb.owner
        if (target is None or fight.vitals_of(target).dead or orb.target in bubbles
                or defenses.phase(target.owner)[0]
                or (not fight.match.game_map.free_for_all and target.team == player.team)):
            assign(orb, None)
            continue
        point = (target.feet[0], target.feet[1] + target.eye, target.feet[2])
        if math.dist(fight._eye(player), point) <= RANGE and visible(fight, player, target):
            orb.visible_at = fight.now
        if fight.now - orb.visible_at >= GRACE:
            assign(orb, None)


def multiplier(fight, shooter, target):
    if shooter is target.owner:
        return 1.0
    for orb in fight.discord.values():
        if (orb.target == target.entity and fight.now - orb.visible_at < GRACE
                and (fight.match.game_map.free_for_all or orb.owner.team != target.team)):
            return SCALE
    return 1.0
