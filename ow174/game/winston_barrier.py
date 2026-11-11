"""Authored 00FA deployment with reconstructed sphere contact and lifecycle."""

import math
from dataclasses import dataclass

from ow174.game import barrier_mesh
from ow174.game.script import expr, nodes


HERO = 0x02E0000000000009
GRAPH = 0x00FA
RADIUS = 5.0  # Model D02, referenced by barrier entity 01C3.


@dataclass
class Bubble:
    owner: object
    centre: tuple
    health: float
    started: int
    expires: float


class Barriers:
    def __init__(self, fight):
        self.fight = fight
        self.actors = {}
        self.seen = {}
        self.pending = {}

    def retire(self, body):
        self.actors.pop(body, None)
        self.seen.pop(body, None)
        self.cancel_pending(body)

    def cancel_pending(self, body):
        self.pending.pop(body, None)

    def update(self):
        for body, actor in tuple(self.actors.items()):
            if actor.health <= 0 or self.fight.tick >= actor.expires:
                del self.actors[body]

    def sync(self, player):
        from ow174.game.combat import TICK

        if player.hero.guid != HERO:
            return
        for instance in player.body_script.component.instances.values():
            if instance.graph.index != GRAPH:
                continue
            ability = instance.states.get(2)
            deployment = instance.states.get(3)
            if not isinstance(ability, nodes.Ability) or deployment is None:
                continue
            tick = max(self.fight.match.tick, self.fight.tick)
            if deployment.active:
                self.pending.setdefault(player.body, (tick, player.body_script.frame))
                continue
            anchor = self.pending.pop(player.body, None)
            if anchor is None or ability.cur <= 0 or not deployment.counter or not deployment.ended_at:
                continue
            token = (instance.id, deployment.counter)
            if self.seen.get(player.body) == token:
                continue
            self.seen[player.body] = token
            health = expr.to_float(instance.find_var(0, 587).value())
            duration = expr.to_float(instance.component.entity_var(567).value())
            if health > 0 and duration > 0:
                x, y, z = player.mover.position
                anchor_tick, anchor_frame = anchor
                end_frame = deployment.ended_at // instance.component.frame_ms
                tick = anchor_tick + max(0, end_frame - anchor_frame)
                self.actors[player.body] = Bubble(player, (x, y + 0.1, z), health, tick, tick + duration / TICK)

    def advance_idle(self, tick):
        from ow174.game.combat import TICK
        from ow174.game.projectiles import MAX_LEASE_FRAME

        if not self.pending:
            return
        for player in self.fight.match.players:
            anchor = self.pending.get(player.body)
            script = player.body_script
            if anchor is None or script is None or script.retired or script.component.owner_dead:
                continue
            anchor_tick, anchor_frame = anchor
            elapsed_ms = max(0, tick - anchor_tick) * round(TICK * 1000)
            # Keep this tick's command slot free; collision already precedes posed commands.
            target = min(MAX_LEASE_FRAME - 1, anchor_frame + elapsed_ms // script.frame_ms - 1)
            while player.body in self.pending and script.frame < target:
                previous = script.frame
                player._advance_body_gap(previous + 2)
                if player.body_script is not script or script.frame <= previous or self.fight.is_stunned(player):
                    break

    def enemies(self, shooter):
        for actor in self.actors.values():
            if (actor.health > 0 and actor.started <= self.fight.tick < actor.expires
                    and actor.owner is not shooter
                    and (self.fight.match.game_map.free_for_all or actor.owner.team != shooter.team)):
                yield actor

    @staticmethod
    def intersect(actor, origin, direction, reach):
        offset = tuple(a - b for a, b in zip(origin, actor.centre, strict=True))
        projection = sum(a * b for a, b in zip(offset, direction, strict=True))
        c = sum(axis * axis for axis in offset) - RADIUS * RADIUS
        discriminant = projection * projection - c
        if discriminant < 0:
            return None
        root = math.sqrt(discriminant)
        if c < -1e-8:
            distance = -projection + root  # A shot starting inside can hit the exit.
        else:
            distance = -projection - root
            if distance < -1e-8 or (distance <= 1e-8 and projection >= 0):
                return None
            distance = max(0.0, distance)
        return distance if distance <= reach else None

    def trace(self, shooter, origin, direction, reach):
        return min(((hit, actor) for actor in self.enemies(shooter)
                    if (hit := self.intersect(actor, origin, direction, reach)) is not None),
                   key=lambda item: item[0], default=None)

    @staticmethod
    def damage(actor, amount):
        actor.health = max(0.0, actor.health - max(0.0, amount))

    @staticmethod
    def nearest_surface(actor, point):
        offset = tuple(a - b for a, b in zip(point, actor.centre, strict=True))
        distance = math.hypot(*offset)
        direction = tuple(axis / distance for axis in offset) if distance else (1.0, 0.0, 0.0)
        witness = tuple(axis + normal * RADIUS for axis, normal in zip(actor.centre, direction, strict=True))
        return abs(distance - RADIUS), witness

    def melee(self, shooter, centre, radius, amount, *, origin=None):
        from ow174.game.combat import capsule_visible

        if amount <= 0:
            return
        if origin is None:
            origin = self.fight._eye(shooter)
        for actor in self.enemies(shooter):
            distance, point = self.nearest_surface(actor, centre)
            if distance <= radius and capsule_visible(self.fight._collision(), origin, point, point, 0):
                self.damage(actor, amount)

    def splash(self, shooter, point, origin, blast, impact, direct_amount):
        from ow174.game.combat import capsule_visible

        cover = tuple(self.enemies(shooter))
        for actor in cover:
            if actor is impact:
                self.damage(actor, direct_amount + blast.amount * blast.splash(0))
                continue
            distance, surface = self.nearest_surface(actor, point)
            scalar = blast.splash(distance)
            if scalar > 0 and capsule_visible(self.fight._collision(), origin, surface, surface, 0):
                self.damage(actor, blast.amount * scalar)
        return cover

    def covers(self, cover, origin, a, b):
        return barrier_mesh.covers(self.intersect, cover, origin, a, b)
