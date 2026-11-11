"""Scoped0095 barrier mesh/HP host, not native pet attachment/replication.

Actual external triangles and authored resource expressions are consumed. Raised
pose, receiver/penetration and regeneration integration remain reconstructions.
"""

import math
from dataclasses import dataclass, field

from ow174.game import barrier_mesh, contacts, orisa_barrier, sigma_barrier, winston_barrier
from ow174.game.script import expr, nodes, runtime

HERO = 0x02E0000000000007
GRAPH = 0x0095


@dataclass
class BarrierState:
    current: float
    maximum: float
    last_update: float
    regen_after: float
    broken_until: float = 0.0
    active: bool = False
    request: tuple[bool, int] = (False, 0)
    requests: dict[tuple[int, int], tuple[bool, int]] = field(default_factory=dict)


class ReinhardtBarriers:
    def __init__(self, fight):
        self.fight = fight
        self.geometry = barrier_mesh.configured_geometry()
        self.states = {}
        self.experimental = {}
        self.orisa = {}
        self.orisa_pending = []
        self.winston = {}
        self.winston_pending = {}

    def retire(self, body):
        self.states.pop(body, None)
        self.winston_pending.pop(body, None)
        self.orisa_pending[:] = [row for row in self.orisa_pending if row[1].source_entity != body]
        for entity, actor in tuple(self.experimental.items()):
            if actor.owner_body == body:
                self.experimental.pop(entity)

    @staticmethod
    def instance(owner):
        script = getattr(owner, "body_script", None)
        if script is None or script.retired or script.hero != HERO:
            return None
        return next((i for i in script.component.instances.values() if i.graph.index == GRAPH), None)

    @staticmethod
    def lower(instance):
        ability = instance.states.get(59)
        if isinstance(ability, nodes.Ability):
            ability.stop()
        pet = instance.states.get(17)
        if pet is not None and pet.active:
            pet.request(runtime.ABORT)
        instance.component.run_now()

    def sync(self, owner, *, admitted_tick=None, request=None):
        if self.geometry is None or not getattr(owner, "has_body", False):
            return None
        instance = self.instance(owner)
        if instance is None:
            return None
        maximum = max(0.0, expr.to_float(instance.find_var(0, 587).value()))
        state = self.states.get(owner.body)
        now = self.fight.now
        if state is None:
            state = self.states[owner.body] = BarrierState(maximum, maximum, now, now)
        state.maximum = maximum
        state.current = min(state.current, maximum)
        pet = instance.states.get(17)
        live_request = (pet.active, pet.counter) if pet is not None else (False, 0)
        if admitted_tick is not None and admitted_tick > self.fight.tick:
            previous = state.requests[max(state.requests)] if state.requests else state.request
            if live_request != previous:
                # Requests between attacks take effect before the next queued contact.
                contact_count = len(self.fight.pending_contacts.get(admitted_tick, ()))
                state.requests[admitted_tick, contact_count] = live_request
        if request is None:
            request = state.request if state.requests else live_request
        raised, generation = request
        unavailable = (owner.gone or not owner.spawned or owner.client is None
                       or self.fight.is_dead(owner) or self.fight.is_stunned(owner))
        if raised and (unavailable or state.current <= 0 or now < state.broken_until):
            if live_request == request:
                self.lower(instance)
            raised = False
        state.request = raised, generation
        delay = max(0.0, expr.to_float(instance.evaluate(instance.graph.node(17).field("m_timeout"))))
        if state.active and not raised:
            state.regen_after = now + delay
        if not raised and not unavailable and now > max(state.last_update, state.regen_after):
            rate = max(0.0, expr.to_float(instance.evaluate(instance.graph.node(16).field("m_9B0385B4"))))
            duration = now - max(state.last_update, state.regen_after)
            state.current = expr.f32(min(maximum, state.current + rate * duration))
        state.active = raised and not unavailable and state.current > 0 and now >= state.broken_until
        state.last_update = max(now, state.last_update)
        value = expr.f32(state.current)
        if not expr.same(instance.find_var(0, 586).value(), value):
            instance.write((0, 586), value)
        return state

    def request_edges(self, tick):
        return {at for state in self.states.values() for at, _ in state.requests
                if self.fight.tick < at <= tick}

    def advance_requests(self, contact_count):
        key = self.fight.tick, contact_count
        for owner in self.fight.match.players:
            state = self.states.get(owner.body)
            if state is not None and key in state.requests:
                # The input follows the contacts of its admission frame.
                self.sync(owner, request=state.requests.pop(key))

    def update(self):
        sigma_barrier.update_barriers(self.fight)
        orisa_barrier.update(self, self.fight.tick)
        for entity, actor in tuple(self.winston.items()):
            if (actor.health <= 0 or actor.expires <= self.fight.now
                    or actor.owner.gone or self.fight.match.ended):
                actor.retire()
                self.winston.pop(entity)
        current = set()
        for owner in self.fight.match.players:
            if self.instance(owner) is not None and owner.has_body:
                current.add(owner.body)
                self.sync(owner)
        for body in set(self.states) - current:
            self.retire(body)

    def reinhardt_enemies(self, shooter, *, team=None):
        if self.geometry is None:
            return ()
        team = shooter.team if team is None else team
        found = []
        for owner in self.fight.match.players:
            if (owner is shooter
                    or (not self.fight.match.game_map.free_for_all and owner.team == team)):
                continue
            state = self.sync(owner)
            if state is not None and state.active:
                found.append(owner)
        return tuple(found)

    def enemies(self, shooter, *, team=None):
        team = shooter.team if team is None else team
        found = list(self.reinhardt_enemies(shooter, team=team))
        for actor in self.experimental.values():
            actor.update(self.fight)
            if (actor.active and actor.owner is not shooter
                    and (self.fight.match.game_map.free_for_all or actor.owner.team != team)):
                found.append(actor)
        found.extend(actor for actor in self.orisa.values()
                     if actor.health > 0 and self.fight.tick < actor.until and actor.owner is not shooter
                     and (self.fight.match.game_map.free_for_all or actor.team != team))
        found.extend(actor for actor in self.winston.values()
                     if actor.health > 0 and actor.expires > self.fight.now
                     and actor.starts_tick <= self.fight.tick
                     and not actor.owner.gone and actor.owner is not shooter
                     and (self.fight.match.game_map.free_for_all or actor.team != team))
        return tuple(found)

    def intersect(self, owner, origin, direction, reach, *, radius=0.0):
        if isinstance(owner, (sigma_barrier.Barrier, orisa_barrier.Barrier, winston_barrier.Bubble)):
            return owner.intersection(origin, direction, reach, radius=radius)
        data = owner.mover.values
        return barrier_mesh.intersect(self.geometry, owner.mover.position, owner.mover.yaw, owner.mover.pitch,
                                      origin, direction, reach, radius=radius, owner_radius=data.stand_radius,
                                      owner_height=data.stand_height)

    def trace(self, shooter, origin, direction, reach, *, team=None, radius=0.0):
        best = None
        for owner in self.enemies(shooter, team=team):
            distance = self.intersect(owner, origin, direction, reach, radius=radius)
            if distance is not None and (best is None or distance < best[0]):
                best = distance, owner
        return best

    def damage(self, owner, amount):
        if isinstance(owner, (orisa_barrier.Barrier, winston_barrier.Bubble)):
            owner.health = max(0.0, owner.health - max(0.0, amount))
            return
        if isinstance(owner, sigma_barrier.Barrier):
            if owner.active and amount > 0:
                sigma_barrier.damage_barrier(owner, amount)
            return
        state = self.states.get(owner.body)
        if state is None or not state.active or amount <= 0:
            return
        state.current = expr.f32(max(0.0, state.current - amount))
        instance = self.instance(owner)
        instance.write((0, 586), state.current)
        if state.current <= 0:
            delay = max(0.0, expr.to_float(instance.find_var(0, 472).value()))
            state.broken_until = self.fight.now + delay
            state.regen_after = self.fight.now + max(
                0.0, expr.to_float(instance.evaluate(instance.graph.node(17).field("m_timeout"))))
            state.active = False
            pet = instance.states.get(17)
            if pet is not None and pet.active and pet.counter == state.request[1]:
                self.lower(instance)
            state.request = False, state.request[1]
            instance.write((0, 61), delay)
            instance.component.run_now()

    def melee(self, shooter, volumes, amount, seen=None, *, eye=None, tick=None):
        if eye is None:
            eye = self.fight._eye(shooter)
        def visible(point):
            return self.fight.combat_visible(eye, point, point, 0, exclude=shooter, tick=tick)

        for owner in self.enemies(shooter):
            placed = isinstance(owner, (sigma_barrier.Barrier, orisa_barrier.Barrier, winston_barrier.Bubble))
            key = ("barrier", owner.entity if placed else owner.body)
            if seen is not None and key in seen:
                continue
            if isinstance(owner, winston_barrier.Bubble):
                hit = any(owner.overlaps(a, b, radius, visible) for a, b, radius in volumes)
            else:
                triangles, position, axes = self.mesh_pose(owner)
                hit = any(barrier_mesh.overlaps(triangles, position, axes, a, b, radius, visible=visible)
                          for a, b, radius in volumes)
            if hit:
                self.damage(owner, amount)
                if seen is not None:
                    seen.add(key)

    def blocked(self, origin, point, owners):
        delta = tuple(point[k] - origin[k] for k in range(3))
        reach = math.sqrt(sum(value * value for value in delta))
        if reach <= 1e-9:
            return False
        direction = tuple(value / reach for value in delta)
        return any((distance := self.intersect(owner, origin, direction, reach)) is not None
                   and distance < reach - 1e-6 for owner in owners)

    def mesh_pose(self, owner):
        if isinstance(owner, (sigma_barrier.Barrier, orisa_barrier.Barrier)):
            triangles = tuple(owner.geometry._triangle(n)[6:15] for n in range(len(owner.geometry)))
            return triangles, owner.position, owner.basis
        data = owner.mover.values
        position, axes = barrier_mesh.frame(self.geometry, owner.mover.position,
                                            owner.mover.yaw, owner.mover.pitch,
                                            data.stand_radius, data.stand_height)
        return self.geometry.triangles, position, axes

    def nearest(self, owner, point):
        if isinstance(owner, winston_barrier.Bubble):
            return owner.nearest(point)
        triangles, position, axes = self.mesh_pose(owner)
        local = tuple(sum((point[k] - position[k]) * axis[k] for k in range(3)) for axis in axes)
        best = None
        for triangle in triangles:
            corners = tuple(tuple(triangle[n:n + 3]) for n in (0, 3, 6))
            found = contacts.gjk(corners, (local,))
            if found.point_a is not None and (best is None or found.distance < best[0]):
                world = tuple(position[k] + sum(axes[n][k] * found.point_a[n] for n in range(3))
                              for k in range(3))
                best = found.distance, world
        return best

    def covers(self, origin, a, b, radius, owners):
        if not owners:
            return False
        axis = tuple(b[k] - a[k] for k in range(3))
        offset = tuple(origin[k] - a[k] for k in range(3))
        squared = sum(v * v for v in axis)
        share = 0 if squared <= 1e-9 else max(0.0, min(1.0, sum(axis[k] * offset[k]
                                                               for k in range(3)) / squared))
        delta = tuple(a[k] + share * axis[k] - origin[k] for k in range(3))
        distance = math.sqrt(sum(v * v for v in delta))
        if distance <= radius:
            return False
        surface = tuple(origin[k] + delta[k] * (distance - radius) / distance for k in range(3))
        return self.blocked(origin, surface, owners)

    def splash(self, shooter, centre, hit, *, team, impact_owner=None):
        """Pin cover before damage so this explosion cannot spill through a break."""
        owners = self.enemies(shooter, team=team)
        cover = owners if impact_owner is None or impact_owner in owners else (*owners, impact_owner)
        for owner in owners:
            if owner is impact_owner:
                continue  # the direct impact already consumed both payloads
            nearest = self.nearest(owner, centre)
            if nearest is None:
                continue
            distance, point = nearest
            amount = hit.amount * hit.splash(distance)
            if (amount > 0 and self.fight.combat_visible(centre, point, point, 0, exclude=shooter)
                    and not self.blocked(centre, point, tuple(p for p in cover if p is not owner))):
                self.damage(owner, amount)
        return cover
