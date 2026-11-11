"""104319 Steel Trap host adapted from the Junkrat lane.

The 09BA launch and 09BB capture fields are authored. Ballistic placement,
capsule and line-of-sight tests, and fallback root timing are reconstructed.
"""

import math
from dataclasses import dataclass

from ow174.game import defenses
from ow174.game.orisa_barrier import Flight, advance_thrown_device
from ow174.game.script import expr, graph, runtime

HIT_RADIUS = 0.25
HEALTH = 100.0  # 103A's initial pool override
RELEASE_FLAG, EXCLUDE_FLAG, SHORT_FLAG = 23154, 9588, 9161


@dataclass
class Trap:
    flight: Flight
    instance: runtime.Instance
    output: tuple | None
    radius: float
    arm_delay: float
    duration: float
    short_duration: float
    damage: float
    loadout: int
    health: float = HEALTH
    armed_at: float | None = None
    victim: int | None = None
    release_tick: int = 0
    victim_status: runtime.Instance | None = None
    release_flag_seen: bool = False

    @property
    def body(self):
        return self.flight.body

    def centre(self):
        x, y, z = self.flight.position
        return (x, y + HIT_RADIUS, z)


class Traps:
    def __init__(self, fight):
        self.fight = fight
        self.live: dict[int, Trap] = {}
        self.player_command_cutoffs: dict[int, int] = {}

    def launch(self, owner, state, origin, direction):
        profile = graph.graph(0x09BB)
        if profile is None:
            return
        self.retire_body(owner.body)
        node = state.node

        def number(cfg):
            return expr.to_float(state.evaluate(cfg, watch=False))

        motion, = node.field("m_projectileMotions")
        speed = number(node.field("m_projectileSpeed"))
        lifetime = number(node.field("m_projectileLifetime"))
        gravity = number(motion["m_599AB8D7"])
        modify = profile.node(4).field("m_modifyHealth")
        loadout = state.evaluate(modify["m_loadout"], watch=False)
        entity = self.fight.next_bot
        self.fight.next_bot += 1
        flight = Flight(owner, state.instance.component, entity, origin,
                        tuple(axis * speed for axis in direction), 0, gravity, lifetime, self.fight.tick)
        output = expr.lvalue(node.field("m_505472EF"))
        trap = Trap(flight, state.instance, output,
                    number(profile.node(2).field("m_radius")),
                    number(profile.node(3).field("m_timeout")),
                    number(profile.node(75).field("m_value")),
                    number(profile.node(76).field("m_value")),
                    number(modify["m_amount"]), loadout.guid)
        self.live[entity] = trap
        state.instance.write(output, expr.Entity(entity))

    def land(self, flight, point, tick):
        trap = self.live[flight.body]
        flight.position = point
        trap.armed_at = self.fight.now + trap.arm_delay

    def rooted(self, entity, tick):
        return any(trap.victim == entity and trap.release_tick > tick for trap in self.live.values())

    def enemies(self, shooter):
        for trap in self.live.values():
            owner = trap.flight.owner
            if (trap.armed_at is not None and owner is not shooter
                    and (self.fight.match.game_map.free_for_all or owner.team != shooter.team)):
                yield trap

    def damage(self, trap, amount):
        trap.health = max(0.0, trap.health - max(0.0, amount))
        if not trap.health:
            self.retire(trap)

    def retire_body(self, body):
        for trap in tuple(self.live.values()):
            if trap.flight.source.entity == body or trap.victim == body:
                self.retire(trap)

    def retire(self, trap):
        if self.live.pop(trap.flight.body, None) is not trap:
            return
        status = trap.victim_status
        if status is not None and not status.stopped:
            status.component.destroy(status)
        instance, output = trap.instance, trap.output
        if output is not None and not instance.stopped:
            if instance.find_var(*output).value() == expr.Entity(trap.flight.body):
                instance.write(output, 0)

    @staticmethod
    def flag(owner, key):
        script = getattr(owner, "body_script", None)
        slot = script.component.entity_var(key, create=False) if script is not None else None
        return slot is not None and expr.truthy(slot.value())

    def update(self, tick, dt):
        fight = self.fight
        for trap in tuple(self.live.values()):
            flight = trap.flight
            if trap.armed_at is None:
                if flight.remaining <= 0:
                    self.retire(trap)
                elif tick > flight.tick:
                    advance_thrown_device(fight, flight, tick, min(dt, flight.remaining), self.land)
            elif trap.victim is not None:
                victim = next((t for t in fight._targets() if t.entity == trap.victim), None)
                if victim is None or fight.vitals_of(victim).dead or tick >= trap.release_tick:
                    self.retire(trap)
                    continue
                flag = self.flag(victim.owner, RELEASE_FLAG)
                if ((trap.victim_status is not None and trap.victim_status.stopped)
                        or (trap.release_flag_seen and not flag)):
                    self.retire(trap)
                else:
                    trap.release_flag_seen = flag
                    if victim.owner in fight.match.players:
                        fight.track_control_input(victim.owner, tick, self.player_command_cutoffs)
            elif fight.now >= trap.armed_at:
                self.capture(trap, tick, dt)

    def capture(self, trap, tick, dt):
        from ow174.game.combat import capsule_visible, segment_distance

        fight = self.fight
        owner = trap.flight.owner
        candidates = []
        for target in fight._enemies(owner):
            if defenses.protection(target.owner)[0] or self.flag(target.owner, EXCLUDE_FLAG):
                continue
            a, b = target.segment()
            distance = max(0.0, segment_distance(trap.flight.position, a, b) - target.radius)
            if distance <= trap.radius and capsule_visible(
                fight._collision(), trap.centre(), a, b, target.radius, walls=fight.walls,
            ):
                candidates.append((distance, target.entity, target))
        if not candidates:
            return
        _, _, target = min(candidates, key=lambda row: row[:2])
        bubble = fight.zarya_bubbles.for_target(target.owner)
        fight._damage(owner, target, trap.damage, False, trap.loadout, tick, world_tick=tick)
        if bubble is not None or fight.vitals_of(target).dead:
            self.retire(trap)
            return
        trap.victim = target.entity
        fight.impulses.retire(target.entity, forget=False)
        if target.owner in fight.match.players:
            fight.track_control_input(target.owner, tick, self.player_command_cutoffs)
        duration = trap.short_duration if self.flag(target.owner, SHORT_FLAG) else trap.duration
        trap.release_tick = tick + math.ceil(duration / dt)
        script = getattr(target.owner, "body_script", None)
        profile = graph.graph(0x09BC)
        if script is not None and profile is not None:
            component = script.component
            trap.victim_status = component.create(profile)
            runtime.send_instance_message(trap.victim_status, 0x024000000000000F, {}, trap.flight.body)
            component.run_now()
        trap.release_flag_seen = self.flag(target.owner, RELEASE_FLAG)
