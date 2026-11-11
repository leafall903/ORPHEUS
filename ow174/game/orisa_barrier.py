"""Scoped Orisa barrier deployment from the authored volley and real mesh."""

import json
import math
import os
from dataclasses import dataclass
from pathlib import Path

from ow174.game import barrier_mesh, bots
from ow174.game.collision import World
from ow174.game.script import expr, graph, runtime

GRAPH = 0x0BD7
DEPLOY_SECONDS = 0.1


@dataclass
class Barrier:
    entity: int
    owner: object
    team: int
    geometry: World
    position: tuple
    basis: tuple
    until: int
    health: float

    def intersection(self, origin, direction, reach, *, radius=0.0):
        return barrier_mesh.intersect_pose(self.geometry, self.position, self.basis,
                                           origin, direction, reach, radius)


def geometry(path):
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if (data.get("schema") != "orpheus-orisa-geometry-v1"
            or data.get("build") != "1.74.0.0.104319"
            or data.get("model") != "0x0D00000000004062"):
        raise ValueError("invalid Orisa 4062 geometry identity")
    triangles, masks = data["triangles"], data["masks"]
    if (not triangles or len(triangles) != len(masks)
            or any(len(t) != 9 or not all(math.isfinite(v) for v in t) for t in triangles)
            or any(type(mask) is not int or not 0 <= mask <= 0xFFFF for mask in masks)):
        raise ValueError("invalid Orisa 4062 geometry records")
    return World(triangles, masks, name="Orisa 4062 physics mesh")


def deployment_delay_ticks():
    from ow174.game.combat import TICK

    return math.ceil(DEPLOY_SECONDS / TICK)


def land(fight, rocket, hit):
    if hit.normal[1] <= 1e-6 or not os.environ.get("ORPHEUS_ORISA_GEOMETRY"):
        return
    due = fight.tick + deployment_delay_ticks()
    fight.reinhardt_barriers.orisa_pending.append((due, rocket, tuple(hit.point)))


def update(barriers, tick):
    for entity, shield in tuple(barriers.orisa.items()):
        if shield.health <= 0 or tick >= shield.until or barriers.fight.match.ended:
            del barriers.orisa[entity]


def advance_deployments(barriers, tick):
    from ow174.game.combat import TICK

    fight = barriers.fight
    for due, rocket, position in tuple(barriers.orisa_pending):
        if tick < due:
            continue
        barriers.orisa_pending.remove((due, rocket, position))
        path = os.environ.get("ORPHEUS_ORISA_GEOMETRY")
        if (not path or rocket.shooter.gone or rocket.shooter.body != rocket.source_entity
                or fight.match.ended):
            continue
        definition = graph.graph(0x0BD8)
        if definition is None:
            raise ValueError("Orisa barrier requires 0BD8")
        entity = fight.next_bot
        if not bots.BOT_ENTITY <= entity < 0xD0000000:
            raise ValueError("Orisa barrier entity allocator exhausted")
        component = runtime.Component(entity, world=runtime.World(mode=fight.match.game_map.mode_guid))
        component.now, component.frame = due * 16, due
        instance = component.create(definition)
        instance.start_entries((), rocket.source_entity)
        component.run_now()
        health = expr.to_float(instance.find_var(graph.INSTANCE, 587).value())
        seconds = expr.to_float(component.entity_var(567).value())
        if health <= 0 or seconds <= 0:
            raise ValueError("0BD8 failed to initialize barrier health or lifetime")
        until = due + math.ceil(seconds / TICK)
        if tick >= until:
            continue
        x, _, z = rocket.orisa_launch
        length = math.hypot(x, z)
        x, z = (x / length, z / length) if length else (0.0, 1.0)
        basis = ((z, 0.0, -x), (0.0, 1.0, 0.0), (x, 0.0, z))
        mesh = geometry(path)
        for old, shield in tuple(barriers.orisa.items()):
            if shield.owner is rocket.shooter:
                del barriers.orisa[old]
        fight.next_bot += 1
        barriers.orisa[entity] = Barrier(entity, rocket.shooter, rocket.team, mesh,
                                         position, basis, until, health)
