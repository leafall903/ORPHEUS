"""Actual00FA deployment with reconstructed sphere interception; native filters/wire remain open."""

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

    def ray(self, origin, direction, *, radius=0.0):
        offset = tuple(a - b for a, b in zip(origin, self.centre, strict=True))
        projection = sum(a * b for a, b in zip(offset, direction, strict=True))
        distance2 = sum(value * value for value in offset)
        c = distance2 - self.radius * self.radius
        inside = c < -1e-8
        if radius > 0:
            surface = max(0.0, self.radius - radius) if inside else self.radius + radius
            if inside and distance2 >= surface * surface and projection >= 0:
                return 0.0
            if not inside and distance2 <= surface * surface:
                return 0.0 if projection < 0 else None
            c = distance2 - surface * surface
        discriminant = projection * projection - c
        if discriminant < 0:
            return None
        root = math.sqrt(discriminant)
        if inside:
            return -projection + root  # from inside: only crossing the exit blocks
        entry = -projection - root
        if entry < -1e-8 or (entry <= 1e-8 and projection >= 0):
            return None  # outward/tangent at a surface is not a crossing
        return max(0.0, entry)

    def damage(self, amount):
        lost = min(self.health, max(0.0, amount))
        self.health -= lost
        return lost


@dataclass
class PendingDeployment:
    cast_tick: int
    release_tick: int
    activation: tuple[int, int]
    bubble: Bubble
    accepted: bool


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
        self.pending = {}
        self.seen = {}

    def retire(self, body):
        self.actors.pop(body, None)
        self.pending.pop(body, None)
        self.seen.pop(body, None)

    def update(self):
        for body, actor in tuple(self.actors.items()):
            if actor.health <= 0 or self.combat.now >= actor.expires:
                self.actors.pop(body, None)

    def deploy_due(self, tick):
        for body, pending in tuple(self.pending.items()):
            owner = pending.bubble.owner
            if owner.body != body or self.combat.is_dead(owner):
                del self.pending[body]
                continue
            if not pending.accepted and pending.cast_tick <= tick:
                if self.combat.is_stunned(owner, tick=pending.cast_tick):
                    self._reject_cast(owner, pending.activation)
                    del self.pending[body]
                    continue
                pending.accepted = True
            if pending.release_tick <= tick:
                del self.pending[body]
                self.actors[body] = pending.bubble

    @staticmethod
    def _reject_cast(owner, activation):
        """Undo the cooldown of a buffered cast whose input was disabled on its world tick."""
        script = owner.body_script
        if script is None:
            return
        instance = script.component.instances.get(activation[0])
        if instance is None or instance.graph.index != 0x00FA:
            return
        deployment, ability = instance.states.get(3), instance.states.get(2)
        if (deployment is not None and deployment.counter == activation[1]
                and isinstance(ability, nodes.Ability)):
            instance.write(expr.lvalue(ability.node.field("m_out_CooldownVar")), 0.0)

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
                from ow174.game.combat import TICK

                at = self.combat.future_command_tick(player.body, command.frame)
                delay = 0.0 if at is None else (at - self.combat.tick) * TICK
                bubble = Bubble(player, player.body, (x, y + .1, z), health,
                                self.combat.now + delay + duration)
                if at is None:
                    self.actors[player.body] = bubble
                else:
                    # A world tick resolves impacts before taking its commands.
                    cast_frame = deployment.start // instance.component.frame_ms
                    cast_tick = at + cast_frame - command.frame
                    self.pending[player.body] = PendingDeployment(
                        cast_tick, at, token, bubble, cast_tick <= self.combat.tick,
                    )

    def snapshot(self):
        self.update()
        return tuple(self.actors.values())

    def first(self, shooter, origin, direction, length, *, snapshot=None, radius=0.0, excluded=()):
        actors = self.snapshot() if snapshot is None else snapshot
        found = None
        for actor in actors:
            if actor.owner is shooter or actor.body in excluded:
                continue
            if not self.combat.match.game_map.free_for_all and actor.owner.team == shooter.team:
                continue
            distance = actor.ray(origin, direction, radius=radius)
            if distance is not None and distance <= length and (found is None or distance < found[0]):
                found = distance, actor
        return found

    def within(self, shooter, centre, reach, *, visible):
        for actor in self.snapshot():
            if actor.owner is shooter or (not self.combat.match.game_map.free_for_all
                                           and actor.owner.team == shooter.team):
                continue
            delta = tuple(a - b for a, b in zip(centre, actor.centre, strict=True))
            distance = math.hypot(*delta)
            separation = abs(distance - actor.radius)
            if separation > reach:
                continue
            direction = tuple(axis / distance for axis in delta) if distance else (1, 0, 0)
            point = tuple(axis + normal * actor.radius
                          for axis, normal in zip(actor.centre, direction, strict=True))
            if visible(point, actor.body):
                yield actor, separation

    def hit_capsule(self, player, a, b, radius, amount, origin, *, seen=None):
        """Reconstruct melee volume contact, independently of body penetration."""
        from ow174.game import combat

        for barrier in self.snapshot():
            key = ("barrier", barrier.body)
            if seen is not None and key in seen:
                continue
            if barrier.owner is player or (not self.combat.match.game_map.free_for_all
                                           and barrier.owner.team == player.team):
                continue
            near = combat.segment_distance(barrier.centre, a, b)
            far = max(math.dist(barrier.centre, a), math.dist(barrier.centre, b))
            if near > barrier.radius + radius or far < barrier.radius - radius:
                continue
            ab = combat._sub(b, a)
            length2 = combat._dot(ab, ab)
            along = (max(0.0, min(1.0, combat._dot(combat._sub(barrier.centre, a), ab) / length2))
                     if length2 > combat.EPSILON else 0.0)
            query = combat._along(a, ab, along)
            normal = combat._sub(query, barrier.centre)
            normal_length = math.sqrt(combat._dot(normal, normal))
            if normal_length > combat.EPSILON:
                contact = combat._along(barrier.centre, normal, barrier.radius / normal_length)
                if not combat.capsule_visible(
                    self.combat._collision(), origin, contact, contact, 0, walls=self.combat.walls,
                ):
                    continue
            barrier.damage(amount)
            if seen is not None:
                seen.add(key)
