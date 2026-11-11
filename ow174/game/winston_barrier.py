"""Static Winston barrier from the authored 00FA deployment and D02 shape."""

import math
from dataclasses import dataclass

from ow174.game.script import expr

HERO = 0x02E0000000000009
GRAPH = 0xFA
RADIUS = 5.0  # D02.00C MSTU sphere ED61D926, attached to 01C3.003.


@dataclass
class Bubble:
    body: int
    team: int
    activation: tuple
    centre: tuple
    health: float
    expires: float

    def ray(self, origin, direction, reach):
        offset = tuple(a - b for a, b in zip(origin, self.centre, strict=True))
        projection = sum(a * b for a, b in zip(offset, direction, strict=True))
        radial = sum(value * value for value in offset) - RADIUS * RADIUS
        discriminant = projection * projection - radial
        if discriminant < 0:
            return None
        root = math.sqrt(discriminant)
        if radial < -1e-8:
            return -projection + root
        entry = -projection - root
        if entry < -1e-8 or (entry <= 1e-8 and projection >= 0):
            return None
        return max(0.0, entry)

    def damage(self, amount):
        self.health = expr.f32(max(0.0, self.health - amount))


def deploy(combat, player):
    if player.hero.guid != HERO:
        return
    script = player.body_script
    for instance in script.component.instances.values():
        if instance.graph.index != GRAPH:
            continue
        ability, wait = instance.states.get(2), instance.states.get(3)
        if (ability is None or ability.cur <= 0 or wait is None or wait.active
                or wait.ended_at is None or ability.last != wait.ended_at):
            return
        # Keep the last activation after break and expiry so an ended Wait
        # cannot redeploy a shield on a later command.
        activation = (instance.id, wait.counter, wait.ended_at)
        prior = combat.winston_bubbles.get(player.body)
        if prior is not None and prior.activation == activation:
            return
        health = expr.to_float(instance.find_var(0, 587).value())
        duration = expr.to_float(script.component.entity_var(567).value())
        if health > 0 and duration > 0 and math.isfinite(health) and math.isfinite(duration):
            x, y, z = player.mover.position
            combat.winston_bubbles[player.body] = Bubble(
                player.body, player.team, activation, (x, y + .1, z), health, combat.now + duration,
            )
        return


def colliders(combat, body, team):
    ffa = combat.match.game_map.free_for_all
    return tuple(bubble for bubble in combat.winston_bubbles.values()
                 if bubble.body != body and (ffa or bubble.team != team)
                 and bubble.health > 0 and combat.now < bubble.expires)
