"""Scoped 104319 Immortality Field deployment and 1763 protection."""

import math
from dataclasses import dataclass, field

from ow174.game.orisa_deployable import Flight
from ow174.game.script import expr, graph

HERO = 0x02E0000000000221
DEVICE_RADIUS = .46099997  # 9EBA.00C capsule


@dataclass
class Field:
    entity: int
    body: int
    team: int
    position: tuple
    zone_radius: float
    active_tick: int
    expires: int
    health: float = 150.0
    bindings: dict = field(default_factory=dict)
    restoration_seen: dict[int, int] = field(default_factory=dict)

    @property
    def radius(self):
        return DEVICE_RADIUS

    def segment(self):
        x, y, z = self.position
        return (x, y + .08403006, z), (x, y + .18003005, z)

    def ray(self, origin, direction, reach):
        from ow174.game.combat import ray_capsule

        distance = ray_capsule(origin, direction, *self.segment(), self.radius)
        return distance if distance is not None and distance <= reach else None

    def eligible(self, combat, target):
        if (not self.active_tick <= combat.tick < self.expires or self.health <= 0
                or math.dist(self.position, target.feet) > self.zone_radius
                or combat.vitals_of(target).dead):
            return False
        if combat.match.game_map.free_for_all:
            if target.entity != self.body:
                return False
        elif self.team != target.team:
            return False
        return combat._blast_reaches(self.position, target)

    def remove(self, entity):
        self.restoration_seen.pop(entity, None)
        component, instance = self.bindings.pop(entity)
        if component.instances.get(instance.id) is instance:
            component.destroy(instance)

    def retire(self):
        for entity in tuple(self.bindings):
            self.remove(entity)

    def restore(self, combat, target):
        _, instance = self.bindings[target.entity]
        state = instance.states.get(1)
        if (state is None or not state.active
                or self.restoration_seen.get(target.entity) == state.counter):
            return
        vitals = combat.vitals_of(target)
        if any(start <= combat.tick < end for start, end in vitals.healing_blocks):
            return
        modify = state.node.field("m_modifyHealth") or {}
        if modify.get("m_93CDFD2F") != 0:
            return
        amount = expr.to_float(state.evaluate(modify.get("m_amount"), watch=False))
        if not math.isfinite(amount) or amount <= 0:
            return
        self.restoration_seen[target.entity] = state.counter
        if vitals.heal(amount) > 0:
            combat._sync_body_health(target.owner, vitals)


def launch(combat, shooter, state, origin, direction, speed, lifetime):
    if state.instance.graph.index != 0x1761 or state.index != 12 or graph.graph(0x1762) is None:
        return False
    if graph.graph(0x1763) is None:
        return False
    motion, = state.node.field("m_projectileMotions")
    gravity = expr.to_float(state.evaluate(motion["m_599AB8D7"], watch=False))
    combat.baptiste_flights.append(Flight(
        shooter.body, shooter.team, origin, tuple(axis * speed for axis in direction), 0.0,
        gravity, lifetime, combat.tick,
    ))
    return True


def deploy(combat, flight, dt):
    metadata = graph.graph(0x1762)
    radius = next(row["m_value"] for row in metadata.node(29).field("m_143D4C5B")
                  if row["m_out_Var"]["m_identifier"] == 1165)
    values = radius, metadata.node(16).field("m_timeout"), metadata.node(28).field("m_timeout")
    radius, duration, arming = (expr.to_float(expr.evaluate(value, expr.Context())) for value in values)
    hover = expr.to_float(expr.evaluate(metadata.node(50).field("m_value"), expr.Context()))
    x, y, z = flight.position
    entity = combat.next_bot
    if entity >= 0xD0000000:
        raise ValueError("Immortality Field entity allocator exhausted")
    combat.next_bot += 1
    old = combat.baptiste_fields.pop(flight.body, None)
    if old is not None:
        old.retire()
    combat.baptiste_fields[flight.body] = Field(entity, flight.body, flight.team, (x, y + hover, z), radius,
                                                flight.due + math.ceil(arming / dt),
                                                flight.due + math.ceil(duration / dt))


def update(combat, tick, dt):
    for flight in tuple(combat.baptiste_flights):
        flight.advance(combat._collision(), tick, dt, delay=0)
        if flight.due is not None:
            deploy(combat, flight, dt)
        if flight.due is not None or flight.left <= 0:
            combat.baptiste_flights.remove(flight)
    if not combat.baptiste_fields:
        return
    targets = combat._targets()
    for body, ward in tuple(combat.baptiste_fields.items()):
        if ward.health <= 0 or tick >= ward.expires:
            ward.retire()
            del combat.baptiste_fields[body]
            continue
        eligible = {}
        for target in targets:
            script = getattr(target.owner, "body_script", None)
            if script is not None and not script.retired and ward.eligible(combat, target):
                eligible[target.entity] = script.component
        for entity, (component, _) in tuple(ward.bindings.items()):
            if eligible.get(entity) is not component:
                ward.remove(entity)
        for target in targets:
            entity = target.entity
            component = eligible.get(entity)
            if component is None:
                continue
            if entity not in ward.bindings:
                instance = component.play_loaded_graph(
                    graph.graph(0x1763), source=ward.entity, instigator=ward.body, policy=1)
                if instance is None:
                    continue
                component.run_now()
                ward.bindings[entity] = component, instance
            ward.restore(combat, target)


def floor(combat, target, vitals):
    result = 0.0
    for ward in combat.baptiste_fields.values():
        binding = ward.bindings.get(target.entity)
        if binding is None or not ward.eligible(combat, target):
            continue
        component, instance = binding
        state = instance.states.get(0)
        if (component.instances.get(instance.id) is instance and state is not None and state.active
                and state.evaluate(state.node.field("m_target"), watch=False) == expr.Entity(target.entity)):
            scale = expr.to_float(state.evaluate(state.node.field("m_9516110B"), watch=False))
            if 0 < scale <= 1:
                result = max(result, expr.f32(scale * vitals.maximum))
    return result


def hostile(combat, body, team):
    return tuple(ward for ward in combat.baptiste_fields.values()
                 if ward.body != body and ward.health > 0 and combat.tick < ward.expires
                 and (combat.match.game_map.free_for_all or ward.team != team))


def retire(combat, body):
    combat.baptiste_flights[:] = [flight for flight in combat.baptiste_flights if flight.body != body]
    ward = combat.baptiste_fields.pop(body, None)
    if ward is not None:
        ward.retire()
    for ward in combat.baptiste_fields.values():
        if body in ward.bindings:
            ward.remove(body)
