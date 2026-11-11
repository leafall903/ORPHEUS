"""Console key semantics; the frontend invokes these under the game server lock.

This does not install native game-console hooks or globally capture game keys.
"""

from ow174.game import combat, npc, npc_control


class Console:
    def __init__(self, catalog: dict[str, npc.Definition]):
        self.catalog = dict(catalog)
        self.menu_open = False

    def press(self, key: str, match, player, *, attack_action="primary"):
        if key == '=':
            self.menu_open = True
            return {'handled': True, 'action': 'menu', 'menu_open': True,
                    'choices': [{'key': k, 'name': d.name} for k, d in sorted(self.catalog.items())]}
        if key == 'Escape':
            self.menu_open = False
            return {'handled': True, 'action': 'close_menu', 'menu_open': False}
        if key not in ('-', '0', '{') and not (self.menu_open and key in self.catalog):
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
            entity = npc_control.start(match, player, action=attack_action)
            return {'handled': True, 'action': 'start', 'entity': entity}
        position = self.spawn_position(match, player)
        team = 1 if player.team == 0 else 0
        bot = npc.spawn(match, self.catalog[key], position, team=team, yaw=player.mover.yaw + 32768)
        return {'handled': True, 'action': 'spawn', 'entity': bot.entity, 'name': bot.name}

    @staticmethod
    def spawn_position(match, player):
        """Use a visible floor five metres ahead; no through-wall or floating spawn fallback."""
        collision = match.collision_world
        if collision is None:
            raise ValueError('NPC spawn requires loaded collision')
        direction = combat.aim(player.mover.yaw, 0)
        origin = match.combat._eye(player)
        wall = combat.ray_world(collision, origin, direction, 5.0)
        if wall is not None and wall < 5.0:
            raise ValueError('NPC spawn is blocked by map geometry')
        x, y, z = player.mover.position
        x, z = x + direction[0] * 5.0, z + direction[2] * 5.0
        ground = collision.ground(x, z, y + 0.6, y - 4.0)
        if ground is None:
            raise ValueError('No walkable floor at the NPC spawn point')
        return x, ground.y, z
