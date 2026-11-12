"""Console key semantics; the frontend invokes these under the game server lock.

This does not install native game-console hooks or globally capture game keys.
"""

import math
import os
from pathlib import Path

from ow174.game import bots, combat, npc, npc_control
from ow174.game.collision import MOVER, NOT_GROUND
from ow174.game.contacts import CharacterBody
from ow174.game.mover import mover_data


def configured_catalog():
    source = os.environ.get('ORPHEUS_NPC_CATALOG')
    if not source:
        raise ValueError('NPC catalog is not configured (ORPHEUS_NPC_CATALOG).')
    try:
        return npc.load_catalog(Path(source))
    except (OSError, ValueError, KeyError, TypeError) as error:
        raise ValueError('NPC catalog is unavailable or invalid.') from error


class Console:
    def __init__(self, catalog: dict[str, npc.Definition]):
        self.catalog = dict(catalog)
        self.menu_open = False

    def press(self, key: str, match, player):
        if key == '=':
            self.menu_open = True
            return {'handled': True, 'action': 'menu', 'menu_open': True,
                    'choices': [{'key': k, 'name': d.name} for k, d in sorted(self.catalog.items())]}
        if key == 'Escape':
            self.menu_open = False
            return {'handled': True, 'action': 'close_menu', 'menu_open': False}
        if key not in ('-', '0', '{', '}') and not (self.menu_open and key in self.catalog):
            return {'handled': False, 'menu_open': self.menu_open}
        if (match.ended or player not in match.players or player.gone or not player.spawned
                or player.client is None or not player.has_body):
            raise ValueError('NPC controls require a live player in the match')
        if key == '-':
            return {'handled': True, 'action': 'remove', 'entities': npc.remove_spawned(match)}
        if key == '{':
            return {'handled': True, 'action': 'stop', 'count': npc_control.stop(match)}
        if match.combat.is_dead(player):
            raise ValueError('NPC spawning and engagement require a living player')
        if key == '0':
            return {'handled': True, 'action': 'start', 'entity': npc_control.start(match, player)}
        if key == '}':
            return {'handled': True, 'action': 'strike', 'entity': npc_control.strike(match, player)}
        definition = self.catalog[key]
        position = self.spawn_position(match, player, definition.body)
        team = 1 if player.team == 0 else 0
        bot = npc.spawn(match, definition, position, team=team, yaw=player.mover.yaw + 32768)
        return {'handled': True, 'action': 'spawn', 'entity': bot.entity, 'name': bot.name}

    @staticmethod
    def spawn_position(match, player, body=None):
        """Find supported, unoccupied ground up to five metres ahead."""
        collision = match.collision_world
        if collision is None:
            raise ValueError('NPC spawn requires loaded collision')
        direction = combat.aim(player.mover.yaw, 0)
        origin = match.combat._eye(player)
        data = mover_data(body)
        margin = data.stand_radius + 0.05
        wall = combat.ray_world(collision, origin, direction, 5.0 + margin)
        farthest = 5.0 if wall is None else min(5.0, wall - margin)
        if farthest < 2.0:
            raise ValueError('NPC spawn is blocked by map geometry')
        x, y, z = player.mover.position
        occupied = []
        for target in match.combat._targets():
            owner = target.owner
            if isinstance(owner, bots.Bot):
                if owner.dead and not owner.retain_corpse:
                    continue
            elif owner.gone or match.combat.is_dead(owner):
                continue
            occupied.append((target.segment(), target.radius))
        slope = math.cos(math.radians(data.max_slope))
        radius, segment = data.capsule()
        clearance = CharacterBody(radius, radius + segment, world=collision.engine, category=MOVER)
        steps = math.ceil((farthest - 2.0) / 0.5)
        obstructed = blocked = False
        for step in range(steps + 1):
            distance = max(2.0, farthest - step * 0.5)
            nx, nz = x + direction[0] * distance, z + direction[2] * distance
            ground = collision.ground(nx, nz, y + 0.6, y - 4.0)
            if ground is not None and not ground.surface & NOT_GROUND and ground.normal[1] >= slope:
                clearance.teleport((nx, ground.y + data.stand_pogo, nz))
                if not clearance.fits(clearance.radius, clearance.top):
                    obstructed = True
                    continue
                low = ground.y + data.stand_radius
                high = ground.y + max(data.stand_radius, data.stand_height - data.stand_radius)
                for ((ax, ay, az), (_, by, _)), radius in occupied:
                    dy = max(0.0, ay - high, low - by)
                    if math.dist((nx, dy, nz), (ax, 0.0, az)) < data.stand_radius + radius + 0.05:
                        blocked = True
                        break
                else:
                    return nx, ground.y, nz
        if blocked:
            raise ValueError('The NPC spawn area is occupied')
        if obstructed:
            raise ValueError('The NPC body does not fit in the spawn area')
        raise ValueError('No walkable floor at the NPC spawn point')
