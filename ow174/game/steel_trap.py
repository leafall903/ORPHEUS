"""Static Steel Trap projectile, trigger and 09BC victim lifetime."""

from dataclasses import dataclass
import math

from ow174.game import ana_nano, defenses, dva_matrix, zarya_barrier
from ow174.game.script import expr, graph, runtime

HERO = 0x02E0000000000065
CAST, EFFECT, VICTIM = 0x09BA, 0x09BB, 0x09BC
RELEASE_FLAG = 23154


@dataclass
class Trap:
    entity: int
    body: int
    team: int
    flight: object
    instance: runtime.Instance
    output: tuple
    damage: object
    arm_delay: float
    trigger_radius: float
    duration: float
    short_duration: float
    health: float = 100.0
    armed_tick: int | None = None
    victim: int | None = None
    end_tick: int | None = None
    status: runtime.Instance | None = None

    @property
    def owner(self):
        return self.flight.shooter

    @property
    def radius(self):
        return .25

    def segment(self):
        x, y, z = self.flight.position
        return (x, y, z), (x, y + .1, z)

    def ray(self, origin, direction, reach):
        from ow174.game.combat import ray_capsule

        distance = ray_capsule(origin, direction, *self.segment(), self.radius)
        return distance if distance is not None and distance <= reach else None


def launch(fight, owner, state, origin, direction, speed, reach):
    if state.instance.graph.index != CAST or state.node.pos != 7:
        return False
    profile = graph.graph(EFFECT)
    if profile is None or graph.graph(VICTIM) is None:
        return False
    from ow174.game.combat import Rocket, hit_def

    node = state.node
    motion, = node.field("m_projectileMotions")
    gravity = expr.to_float(state.evaluate(motion["m_599AB8D7"], watch=False))
    lifetime = expr.to_float(state.evaluate(node.field("m_projectileLifetime"), watch=False)) or 5.0
    damage = hit_def(state, profile.node(4).fields)
    if damage is None:
        return False
    flight = Rocket(owner, owner.team, origin, direction, speed, lifetime, None, None,
                    last_tick=fight.tick, source_body=owner.body, range_limit=reach,
                    gravity=gravity, velocity=tuple(speed * axis for axis in direction))
    if fight.next_bot >= 0xD0000000:
        raise ValueError("Steel Trap entity allocator exhausted")
    trap = Trap(fight.next_bot, owner.body, owner.team, flight, state.instance,
                expr.lvalue(node.field("m_505472EF")), damage,
                *(expr.to_float(state.evaluate(profile.node(pos).field(key), watch=False))
                  for pos, key in ((3, "m_timeout"), (2, "m_radius"), (75, "m_value"), (76, "m_value"))))
    if (old := fight.steel_traps.get(owner.body)) is not None:
        retire(fight, old)
    fight.next_bot += 1
    fight.steel_traps[owner.body] = trap
    fight.rockets.append(flight)
    if trap.output is not None:
        trap.instance.write(trap.output, expr.Entity(trap.entity))
    return True


def sweep(fight, trap, heading, step, duration, range_ends):
    from ow174.game.combat import TICK, _along, ray_world

    rocket = trap.flight
    static = ray_world(fight._collision(), rocket.position, heading, step)
    ice = fight.walls.cast(rocket.position, heading, step)
    distance = min((hit for hit in (static, ice[0] if ice is not None else None)
                    if hit is not None), default=None)
    matrix_targets = (target for target in fight._enemies(rocket.shooter)
                      if getattr(getattr(target.owner, "hero", None), "guid", None) == dva_matrix.HERO)
    gate = defenses.projectile_gate(rocket.position, heading, step, matrix_targets, matrix_eligible=True)
    if gate is not None and (distance is None or gate[0] < distance):
        rocket.flown += duration * gate[0] / step
        rocket.distance_flown += gate[0]
        retire(fight, trap)
        return None
    if distance is not None:
        rocket.position = _along(rocket.position, heading, max(0.0, distance - 1e-4))
        rocket.flown += duration * distance / step
        rocket.distance_flown += distance
        trap.armed_tick = rocket.last_tick + math.ceil(trap.arm_delay / TICK)
        fight._landed(rocket)
        return None
    rocket.position = _along(rocket.position, heading, step)
    rocket.flown += duration
    rocket.left -= duration
    rocket.distance_flown += step
    if range_ends or rocket.left <= 0:
        retire(fight, trap)
        return None
    return rocket


def rooted(fight, body, tick):
    return any(trap.victim == body and trap.end_tick > tick for trap in fight.steel_traps.values())


def hostile(fight, body, team):
    return tuple(trap for trap in fight.steel_traps.values() if trap.body != body
                 and trap.armed_tick is not None and trap.health > 0
                 and (fight.match.game_map.free_for_all or trap.team != team))


def flag(target, identifier):
    script = getattr(target.owner, "body_script", None)
    var = script.component.entity_var(identifier, create=False) if script is not None else None
    return var is not None and expr.truthy(var.value())


def update(fight, tick):
    from ow174.game.combat import TICK, segment_distance

    for trap in tuple(fight.steel_traps.values()):
        owner = trap.flight.shooter
        if owner.gone or owner.body != trap.body or trap.health <= 0:
            retire(fight, trap)
            continue
        if trap.victim is not None:
            target = next((t for t in fight._targets() if t.entity == trap.victim), None)
            if (tick >= trap.end_tick or target is None or fight.vitals_of(target).dead
                    or trap.status is not None and (trap.status.stopped or not flag(target, RELEASE_FLAG))):
                retire(fight, trap)
            continue
        if trap.armed_tick is None or tick < trap.armed_tick:
            continue
        candidates = []
        for target in fight._enemies(owner):
            if rooted(fight, target.entity, tick) or flag(target, 9588) or defenses.phase(target.owner)[1]:
                continue
            distance = max(0.0, segment_distance(trap.flight.position, *target.segment()) - target.radius)
            if distance <= trap.trigger_radius and fight._blast_reaches(trap.flight.position, target):
                candidates.append((distance, target.entity, target))
        if not candidates:
            continue
        target = min(candidates, key=lambda row: row[:2])[2]
        bubble = zarya_barrier.attached_to(fight, target.entity)
        damage = trap.damage
        if bubble is not None:
            scale = ana_nano.outgoing(fight, trap.body, trap.body, tags=damage.tags)
            bubble.damage(damage.amount * scale, trap.body)
            retire(fight, trap)
            continue
        fight._damage(owner, target, damage.amount, False, damage.loadout, tick,
                      world_tick=tick, tags=damage.tags, source_body=trap.body)
        if fight.vitals_of(target).dead:
            retire(fight, trap)
            continue
        trap.victim = target.entity
        duration = trap.short_duration if flag(target, 9161) else trap.duration
        trap.end_tick = tick + math.ceil(duration / TICK)
        script = getattr(target.owner, "body_script", None)
        if script is not None and not script.retired and not script.component.strict:
            component = script.component
            trap.status = component.create(graph.graph(VICTIM))
            runtime.send_instance_message(trap.status, 0x024000000000000F, {}, trap.entity)
            component.run_now()


def retire(fight, trap):
    if fight.steel_traps.get(trap.body) is not trap:
        return
    del fight.steel_traps[trap.body]
    if trap.status is not None and not trap.status.stopped:
        trap.status.component.destroy(trap.status)
    fight._landed(trap.flight)
    if trap.output is not None and not trap.instance.stopped:
        var = trap.instance.find_var(*trap.output, create=False)
        if var is not None and var.value() == expr.Entity(trap.entity):
            trap.instance.write(trap.output, 0)


def retire_body(fight, body, *, source=True):
    for trap in tuple(fight.steel_traps.values()):
        if trap.victim == body or (source and trap.body == body):
            retire(fight, trap)
