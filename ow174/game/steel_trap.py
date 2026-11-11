"""Server hosting for the 104319 Steel Trap launch and capture resources."""
import math
from dataclasses import dataclass

from ow174.game import fortify
from ow174.game.script import expr, graph, runtime

DEVICE_RADIUS = 0.25  # Reconstructed 103A model hit volume.
LAUNCH = (0x09BA, 7)
RELEASE_FLAG = 23154


@dataclass
class Trap:
    owner: object
    source: int
    team: int
    body: int
    instance: runtime.Instance
    output: tuple
    position: tuple
    velocity: tuple
    gravity: float
    remaining: float
    launch_tick: int
    arm_ticks: int
    radius: float
    duration: float
    short_duration: float
    amount: float
    loadout: int
    health: float = 100.0  # 103A's 0033 schema override C7.
    armed_at: int | None = None
    victim: int | None = None
    release_tick: int = 0
    status: runtime.Instance | None = None

    def centre(self):
        x, y, z = self.position
        return x, y + DEVICE_RADIUS, z


class Traps:
    def __init__(self, fight):
        self.fight = fight
        self.devices = {}

    def launch(self, owner, state, origin, direction):
        from ow174.game.combat import TICK, _number

        node = state.node
        impact = node.field("m_49CBA3BC")
        resources = (node.field("m_projectileEntity")["m_entityDef"]["m_entityDef"],
                     impact["m_entityDefinition"]["m_entityDef"], impact["m_410BF8BD"]["m_graph"])
        if resources != ("0x04000000000011A9", "0x040000000000103A", "0x05800000000009BB"):
            raise ValueError("Steel Trap launch resources changed")
        profile = graph.graph(0x09BB)
        if profile is None or graph.graph(0x09BC) is None:
            return

        def number(config):
            return expr.to_float(state.evaluate(config, watch=False))

        entity = self.fight.next_bot
        if entity >= 0xD0000000:
            raise ValueError("trap entity allocator exhausted")
        self.fight.next_bot += 1
        modify = profile.node(4).field("m_modifyHealth")
        output = expr.dynamic(node.field("m_505472EF"))
        speed = number(node.field("m_projectileSpeed"))
        trap = Trap(
            owner=owner, source=owner.body, team=owner.team, body=entity,
            instance=state.instance, output=output,
            position=origin, velocity=tuple(speed * value for value in direction),
            gravity=sum(
                _number(state.evaluate(motion.get("m_599AB8D7"), watch=False), 0.0)
                for motion in node.field("m_projectileMotions") or ()
                if isinstance(motion, dict) and motion.get("$") == "STU_E1A5B579"
            ),
            remaining=number(node.field("m_projectileLifetime")),
            launch_tick=self.fight.tick,
            arm_ticks=math.ceil(number(profile.node(3).field("m_timeout")) / TICK),
            radius=number(profile.node(2).field("m_radius")),
            duration=number(profile.node(75).field("m_value")),
            short_duration=number(profile.node(76).field("m_value")),
            amount=number(modify["m_amount"]),
            loadout=state.evaluate(modify["m_loadout"], watch=False).guid,
        )
        self.retire(owner.body)
        self.devices[owner.body] = trap
        state.instance.write(output, expr.Entity(entity))

    def retire(self, source):
        trap = self.devices.pop(source, None)
        if trap is None:
            return
        if (trap.status is not None and not trap.status.stopped
                and trap.status.component.fault is None):
            trap.status.component.destroy(trap.status)
        instance = trap.instance
        if not instance.stopped and instance.component.fault is None:
            value = instance.find_var(*trap.output, create=False)
            if value is not None and value.value() == expr.Entity(trap.body):
                instance.write(trap.output, expr.Entity(0))

    def retire_victim(self, entity):
        for trap in list(self.devices.values()):
            if trap.victim == entity:
                self.retire(trap.source)

    def root_until(self, entity):
        return max((trap.release_tick for trap in self.devices.values() if trap.victim == entity), default=0)

    def pull_destination(self, victim, body):
        for trap in self.devices.values():
            if trap.body == body and trap.victim == victim:
                return expr.Vec3(*trap.position)
        return None

    def enemies(self, shooter, *, team=None):
        team = shooter.team if team is None else team
        for trap in self.devices.values():
            if trap.armed_at is not None and trap.owner is not shooter and (
                self.fight.match.game_map.free_for_all or trap.team != team
            ):
                yield trap

    def damage(self, trap, amount):
        if self.devices.get(trap.source) is trap and amount > 0:
            trap.health = expr.f32(max(0.0, trap.health - amount))
            if not trap.health:
                self.retire(trap.source)

    def trace(self, shooter, origin, direction, reach, *, team=None):
        from ow174.game.combat import ray_capsule

        best = None
        for trap in self.enemies(shooter, team=team):
            centre = trap.centre()
            distance = ray_capsule(origin, direction, centre, centre, DEVICE_RADIUS)
            if distance is not None and distance <= reach and (best is None or distance < best[0]):
                best = distance, trap
        return best

    def melee(self, shooter, origin, centre, radius, amount, *, contacted=None):
        from ow174.game.combat import capsule_visible

        for trap in tuple(self.enemies(shooter)):
            if contacted is not None and trap.body in contacted:
                continue
            point = trap.centre()
            if (math.dist(centre, point) <= radius + DEVICE_RADIUS
                    and capsule_visible(self.fight._collision(), origin, point, point, DEVICE_RADIUS)):
                if contacted is not None:
                    contacted.add(trap.body)
                self.damage(trap, amount * self.fight.superchargers.scale(shooter))

    def advance(self, trap, tick, dt):
        from ow174.game.combat import EPSILON, ray_world

        if tick <= trap.launch_tick:
            return
        dt = min(dt, trap.remaining)
        vx, vy, vz = trap.velocity
        trap.velocity = vx, expr.f32(vy - trap.gravity * dt), vz
        speed = math.hypot(*trap.velocity)
        travel = speed * dt
        if travel > EPSILON:
            direction = tuple(value / speed for value in trap.velocity)
            distance = ray_world(self.fight._collision(), trap.position, direction, travel)
            step = travel if distance is None else max(0.0, distance - 1e-4)
            trap.position = tuple(pos + axis * step for pos, axis in zip(trap.position, direction, strict=True))
            if distance is not None:
                trap.armed_at = tick + trap.arm_ticks
                return
        trap.remaining -= dt
        if trap.remaining <= 0:
            self.retire(trap.source)

    @staticmethod
    def flag(target, key):
        script = target.owner.body_script
        value = script.component.entity_var(key, create=False) if script is not None else None
        return value is not None and expr.truthy(value.value())

    def update(self, tick, dt):
        from ow174.game.combat import TICK, capsule_visible, segment_distance
        from ow174.game.phase_defenses import phase, transcendence_invulnerable

        fight = self.fight
        for trap in list(self.devices.values()):
            if self.devices.get(trap.source) is not trap:
                continue
            if trap.owner.gone or trap.owner.body != trap.source:
                self.retire(trap.source)
                continue
            if trap.armed_at is None:
                self.advance(trap, tick, dt)
                continue
            if trap.victim is not None:
                target = next((item for item in fight._targets() if item.entity == trap.victim), None)
                if (tick >= trap.release_tick or target is None or fight.vitals_of(target).dead
                        or trap.status is not None and (trap.status.stopped or not self.flag(target, RELEASE_FLAG))):
                    self.retire(trap.source)
                continue
            if tick < trap.armed_at:
                continue
            candidates = []
            for target in fight._targets():
                if (target.owner is trap.owner or (not fight.match.game_map.free_for_all and target.team == trap.team)
                        or fight.vitals_of(target).dead or (phase(target.owner)[0] or transcendence_invulnerable(target.owner, tick)) or self.flag(target, 9588)):
                    continue
                a, b = target.segment()
                distance = max(0.0, segment_distance(trap.position, a, b) - target.radius)
                if distance <= trap.radius and capsule_visible(
                    fight._collision(), trap.centre(), a, b, target.radius,
                ):
                    candidates.append((distance, target.entity, target))
            if not candidates:
                continue
            _, _, target = min(candidates, key=lambda row: row[:2])
            bubble = next((bubble for bubble in fight.bubbles.enemies(trap.owner)
                           if bubble.owner is target.owner), None)
            if bubble is not None:
                fight._shield_damage(trap.owner, bubble, trap.amount)
                self.retire(trap.source)
                continue
            fight._damage(trap.owner, target, trap.amount, False, trap.loadout, tick, world_tick=tick)
            if fight.vitals_of(target).dead:
                self.retire(trap.source)
                continue
            duration = trap.short_duration if self.flag(target, 9161) or fortify.control_flag_active(target.owner) else trap.duration
            trap.victim, trap.release_tick = target.entity, tick + math.ceil(duration / TICK)
            script = target.owner.body_script
            if script is not None and (status := graph.graph(0x09BC)) is not None:
                trap.status = script.component.create(status)
                sender = expr.dynamic(status.node(0).field("m_B60ABDA4"))
                trap.status.write(sender, expr.Entity(trap.body))
                runtime.send_instance_message(trap.status, 0x024000000000000F, {}, trap.body)
                script.component.run_now()
