"""Steel Trap host for 09BA/11A9/103A/09BB.

Ballistic chords, capsule contact and movement restraint are reconstructed.
"""

import math
from dataclasses import dataclass

from ow174.game import defenses
from ow174.game.script import expr, graph

PROJECTILE = 0x04000000000011A9
HEALTH = 100.0  # 103A/0033 override199
HIT_RADIUS = 0.25


@dataclass
class Trap:
    entity: int
    owner: object
    body: int
    team: int
    position: tuple
    velocity: tuple
    gravity: float
    left: float
    last_tick: int
    radius: float
    arm_delay: float
    duration: float
    short_duration: float
    damage: float
    loadout: int | None
    health: float = HEALTH
    armed_at: float | None = None
    victim: int | None = None
    release_tick: int = 0


class Traps:
    def __init__(self, fight):
        self.fight = fight
        self.actors = {}

    def cast(self, owner, state, origin, direction, speed, lifetime):
        if (state.instance.graph.index, state.node.pos) != (0x09BA, 7):
            return
        profile = graph.graph(0x09BB)
        modify = profile.node(4).field("m_modifyHealth")
        loadout = state.evaluate(modify["m_loadout"], watch=False)
        motion, = state.node.field("m_projectileMotions")
        gravity = expr.to_float(state.evaluate(motion["m_599AB8D7"], watch=False))
        self.retire(owner.body)
        entity = self.fight.next_bot
        self.fight.next_bot += 1

        def number(pos, field):
            return expr.to_float(state.evaluate(profile.node(pos).field(field), watch=False))

        trap = Trap(entity, owner, owner.body, owner.team, origin, tuple(speed * d for d in direction),
                    gravity, lifetime, self.fight.tick, number(2, "m_radius"), number(3, "m_timeout"),
                    number(75, "m_value"), number(76, "m_value"),
                    expr.to_float(state.evaluate(modify["m_amount"], watch=False)),
                    loadout.guid if isinstance(loadout, expr.Asset) else None)
        self.actors[entity] = trap
        output = expr.lvalue(state.node.field("m_505472EF"))
        if output is not None:
            state.instance.write(output, expr.Entity(entity))

    def retire(self, body):
        for entity, trap in tuple(self.actors.items()):
            if body in (trap.body, trap.victim):
                del self.actors[entity]

    def release(self, victim):
        for entity, trap in tuple(self.actors.items()):
            if trap.victim == victim:
                del self.actors[entity]

    def rooted(self, body, tick):
        return any(t.victim == body and tick < t.release_tick for t in self.actors.values())

    def targets(self, shooter):
        from ow174.game.combat import Target

        for trap in self.actors.values():
            if (trap.armed_at is not None and trap.owner is not shooter
                    and (self.fight.match.game_map.free_for_all or trap.team != shooter.team)):
                yield Target(trap.entity, trap.team, trap.position, HIT_RADIUS * 2,
                             HIT_RADIUS, HIT_RADIUS, trap.owner)

    def damage(self, trap, amount):
        trap.health = max(0, trap.health - amount)
        if not trap.health:
            self.actors.pop(trap.entity, None)

    @staticmethod
    def flag(actor, key):
        script = getattr(actor, "body_script", None)
        value = script.component.entity_var(key, create=False) if script is not None else None
        return value is not None and expr.truthy(value.value())

    def fly(self, trap, tick):
        from ow174.game.combat import TICK, _along, ray_world

        for at in range(trap.last_tick + 1, tick + 1):
            dt = min(TICK, trap.left)
            vx, vy, vz = trap.velocity
            delta = (vx * dt, vy * dt - trap.gravity * dt**2 / 2, vz * dt)
            distance = math.hypot(*delta)
            if distance:
                direction = tuple(d / distance for d in delta)
                hit = ray_world(self.fight._collision(), trap.position, direction, distance)
                if hit is not None:
                    trap.position = _along(trap.position, direction, hit)
                    trap.armed_at = self.fight.now - (tick - at) * TICK + trap.arm_delay
                    return
            trap.position = tuple(p + d for p, d in zip(trap.position, delta, strict=True))
            trap.velocity = (vx, vy - trap.gravity * dt, vz)
            trap.left -= dt
            trap.last_tick = at
            if trap.left <= 0:
                self.actors.pop(trap.entity, None)
                return

    def update(self, tick):
        from ow174.game.combat import TICK, capsule_visible, segment_distance

        fight = self.fight
        for trap in tuple(self.actors.values()):
            if trap.armed_at is None:
                self.fly(trap, tick)
                continue
            if trap.victim is not None:
                if tick >= trap.release_tick:
                    self.actors.pop(trap.entity, None)
                continue
            if fight.now < trap.armed_at:
                continue
            origin = (trap.position[0], trap.position[1] + 0.02, trap.position[2])
            candidates = []
            for target in fight._enemies(trap.owner):
                if (defenses.invulnerable(target.owner) or self.flag(target.owner, 9588)
                        or fight.zarya_bubbles.for_target(target.owner) is not None):
                    continue
                a, b = target.segment()
                distance = segment_distance(origin, a, b) - target.radius
                if (distance <= trap.radius
                        and capsule_visible(fight._collision(), origin, a, b, target.radius)):
                    candidates.append((distance, target.entity, target))
            if not candidates:
                continue
            _, _, target = min(candidates)
            fight._damage(trap.owner, target, trap.damage, False, trap.loadout, tick, world_tick=tick)
            if fight.vitals_of(target).dead:
                self.actors.pop(trap.entity, None)
                continue
            trap.victim = target.entity
            duration = trap.short_duration if self.flag(target.owner, 9161) else trap.duration
            trap.release_tick = tick + math.ceil(duration / TICK)
