"""Winston 00FA deployments in the static combat host."""

import math
from dataclasses import dataclass

from ow174.game.script import expr, nodes

HERO = 0x02E0000000000009


@dataclass
class Bubble:
    owner: object
    body: int
    centre: tuple
    health: float
    expires: float
    radius: float = 5.0  # DATA: D02.00C MSTU sphere ED61D926, referenced by 01C3.003.

    def ray(self, origin, direction):
        offset = tuple(a - b for a, b in zip(origin, self.centre, strict=True))
        projection = sum(a * b for a, b in zip(offset, direction, strict=True))
        c = sum(value * value for value in offset) - self.radius * self.radius
        discriminant = projection * projection - c
        if discriminant < 0:
            return None
        root = math.sqrt(discriminant)
        if c < -1e-8:
            return -projection + root  # from inside: only crossing the exit blocks
        entry = -projection - root
        if entry < -1e-8 or (entry <= 1e-8 and projection >= 0):
            return None  # outward/tangent at a surface is not a crossing
        return max(0.0, entry)

    def damage(self, amount):
        lost = min(self.health, max(0.0, amount))
        self.health -= lost
        return lost


class Barriers:
    """Combat-side sphere actors; native entity/graph replication remains open.

    DATA: radius5 in D02's dynamic sphere (wrapper motion1), graph00FA HP700,
    duration9 in entity var567, and 50ms deployment -> cooldown13. Ordinary
    static-map collision does not include this dynamic shape. Sphere crossings,
    no damage spill, owner-team pass-through and placement at feet+.1 are
    reconstructed services awaiting native comparison.
    """

    def __init__(self, combat):
        self.combat = combat
        self.actors = {}
        self.seen = {}

    def retire(self, body):
        self.actors.pop(body, None)
        self.seen.pop(body, None)

    def update(self):
        for body, actor in tuple(self.actors.items()):
            if actor.health <= 0 or self.combat.now >= actor.expires:
                self.actors.pop(body, None)

    def command(self, player, command, *, dead=False):
        if player.hero.guid != HERO or command is None or dead:
            return
        for instance in player.body_script.component.instances.values():
            if instance.graph.index != 0x00FA:
                continue
            ability = instance.states.get(2)
            if not isinstance(ability, nodes.Ability) or ability.cur <= 0:
                continue
            deployment = instance.states.get(3)
            if deployment is None or deployment.active or deployment.ended_at is None:
                continue
            token = (instance.id, deployment.counter)
            if self.seen.get(player.body) == token:
                continue
            self.seen[player.body] = token
            health = expr.to_float(instance.find_var(0, 587).value())
            duration = expr.to_float(instance.component.entity_var(567).value())
            x, y, z = player.mover.position
            if health > 0 and duration > 0:
                self.actors[player.body] = Bubble(player, player.body, (x, y + .1, z), health,
                                                  self.combat.now + duration)

    def snapshot(self):
        self.update()
        return tuple(self.actors.values())

    def enemies(self, shooter):
        for actor in self.snapshot():
            if actor.owner is shooter:
                continue
            if not self.combat.match.game_map.free_for_all and actor.owner.team == shooter.team:
                continue
            yield actor

    def contacts(self, shooter, origin, direction, length):
        for actor in self.enemies(shooter):
            distance = actor.ray(origin, direction)
            if distance is not None and distance <= length:
                yield distance, actor.owner, actor
