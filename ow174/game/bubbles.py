"""Selected authored Winston00FA deployment and dynamic sphere collision.

The source sphere D02.00C has radius5. Placement at feet+0.1m, current
world pose and team filtering are reconstructed. No Pet actor is fabricated.
"""

import math
from dataclasses import dataclass

from ow174.game.script import expr, nodes

WINSTON = 0x02E0000000000009
GRAPH = 0x00FA
RADIUS = 5.0
PLACE_HEIGHT = 0.1


@dataclass
class Bubble:
    owner: object
    body: int
    centre: tuple
    health: float
    start: float
    expires: float

    def ray(self, origin, direction):
        offset = tuple(a - b for a, b in zip(origin, self.centre, strict=True))
        projection = sum(a * b for a, b in zip(offset, direction, strict=True))
        c = sum(value * value for value in offset) - RADIUS * RADIUS
        discriminant = projection * projection - c
        if discriminant < 0:
            return None
        root = math.sqrt(discriminant)
        if c < -1e-8:
            return -projection + root
        entry = -projection - root
        if entry < -1e-8 or (entry <= 1e-8 and projection >= 0):
            return None
        return max(0.0, entry)

    def damage(self, amount):
        self.health = max(0.0, self.health - max(0.0, amount))


class Bubbles:
    def __init__(self, combat):
        self.combat = combat
        self.actors = {}
        self.seen = {}

    def retire(self, body):
        self.actors.pop(body, None)
        self.seen.pop(body, None)

    def expire(self, now):
        for body, bubble in tuple(self.actors.items()):
            if bubble.health <= 0 or now >= bubble.expires:
                self.actors.pop(body, None)

    def command(self, player, command):
        if command is None or player.hero.guid != WINSTON:
            return
        for instance in player.body_script.component.instances.values():
            if instance.graph.index != GRAPH:
                continue
            ability = instance.states.get(2)
            deployment = instance.states.get(3)
            if (not isinstance(ability, nodes.Ability) or ability.cur <= 0
                    or not isinstance(deployment, nodes.Wait) or deployment.node.pos != 13
                    or deployment.active or not deployment.counter
                    or deployment.ended_at - deployment.start < deployment.ms):
                continue
            token = (instance.id, deployment.counter)
            if self.seen.get(player.body) == token:
                continue
            self.seen[player.body] = token
            health = expr.to_float(instance.find_var(0, 587).value())
            duration = expr.to_float(instance.component.entity_var(567).value())
            if health > 0 and duration > 0:
                x, y, z = player.mover.position
                start = deployment.ended_at / 1000
                self.actors[player.body] = Bubble(
                    player, player.body, (x, y + PLACE_HEIGHT, z), health, start, start + duration,
                )

    def first(self, shooter, origin, direction, length, *, team=None):
        side = shooter.team if team is None else team
        now = self.combat.now
        best = None
        for actor in self.actors.values():
            if (actor.owner is shooter or actor.health <= 0 or now + .015 + 1e-9 < actor.start
                    or now >= actor.expires
                    or (not self.combat.match.game_map.free_for_all and actor.owner.team == side)):
                continue
            distance = actor.ray(origin, direction)
            if distance is not None and distance <= length and (best is None or distance < best[0]):
                best = distance, actor
        return best


    def melee(self, shooter, origin, centre, radius):
        """The melee sphere can contact the shell independently of body reach."""
        from ow174.game.combat import ray_world

        for actor in self.actors.values():
            if (actor.owner is shooter or actor.health <= 0 or self.combat.now + .015 < actor.start
                    or self.combat.now >= actor.expires
                    or (not self.combat.match.game_map.free_for_all and actor.owner.team == shooter.team)):
                continue
            distance = math.dist(actor.centre, centre)
            if abs(distance - RADIUS) > radius:
                continue
            radial = (tuple((x - y) / distance for x, y in zip(centre, actor.centre, strict=True))
                      if distance > 0 else (0, 1, 0))
            contact = tuple(x + v * RADIUS for x, v in zip(actor.centre, radial, strict=True))
            reach = math.dist(origin, contact)
            if reach > 0:
                direction = tuple((y - x) / reach for x, y in zip(origin, contact, strict=True))
                wall = ray_world(self.combat._collision(), origin, direction, reach)
                if wall is not None and wall + 1e-6 < reach:
                    continue
            yield actor
