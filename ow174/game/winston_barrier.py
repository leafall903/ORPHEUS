"""Authored Winston deployment with a reconstructed combat sphere."""

import math
from dataclasses import dataclass

from ow174.game.script import expr, nodes

HERO = 0x02E0000000000009
DEFINITION = 0x04000000000001C3


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
        expired = []
        for body, actor in tuple(self.actors.items()):
            if actor.health <= 0:
                self.actors.pop(body, None)
            elif self.combat.now >= actor.expires:
                expired.append(self.actors.pop(body))
        return expired

    def command(self, player, command):
        if player.hero.guid != HERO or command is None:
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

    @staticmethod
    def beam_identity(actor):
        return DEFINITION, actor.body

    def damage(self, actor, amount, *, beam=False):
        actor.damage(amount)
        if actor.health <= 0:
            self.actors.pop(actor.body, None)

    def trace(self, shooter, origin, direction, length):
        found = None
        for actor in self.hostile(shooter):
            distance = actor.ray(origin, direction)
            if distance is not None and distance <= length and (found is None or distance < found[0]):
                found = distance, actor
        return found

    def _opposes(self, actor, shooter):
        owner = getattr(shooter, 'combat_owner', shooter)
        return (actor.owner is not owner
                and (self.combat.match.game_map.free_for_all or actor.owner.team != shooter.team))

    def hostile(self, shooter):
        return tuple(actor for actor in self.actors.values()
                     if actor.health > 0 and self.combat.now < actor.expires
                     and self._opposes(actor, shooter))

    def cover_end(self, shooter, origin, target, ended):
        from ow174.game.combat import capsule_ray

        if not ended:
            return None
        direction, reach = capsule_ray(origin, target)
        if not reach:
            return None
        return max(
            (ended_at for actor, ended_at in ended if self._opposes(actor, shooter)
             and (distance := actor.ray(origin, direction)) is not None and distance <= reach),
            default=None,
        )

    def splash_contacts(self, shooter, point, reach):
        from ow174.game.combat import _along, _sub

        for actor in self.hostile(shooter):
            distance = math.dist(actor.centre, point)
            if abs(distance - actor.radius) <= reach:
                if distance > 1e-8:
                    surface = _along(actor.centre, _sub(point, actor.centre), actor.radius / distance)
                else:
                    surface = _along(actor.centre, (0, 0, 1), actor.radius)
                yield actor, surface

    def melee(self, shooter, origin, centre, radius, amount):
        from ow174.game.combat import _along, _sub, ray_world

        for actor in self.hostile(shooter):
            distance = math.dist(actor.centre, centre)
            if abs(distance - actor.radius) > radius or distance <= 1e-8:
                continue
            point = _along(actor.centre, _sub(centre, actor.centre), actor.radius / distance)
            reach = math.dist(origin, point)
            if reach > 1e-8:
                direction = tuple(v / reach for v in _sub(point, origin))
                wall = ray_world(self.combat._collision(), origin, direction, reach)
                if wall is not None and wall < reach:
                    continue
            self.damage(actor, amount)
