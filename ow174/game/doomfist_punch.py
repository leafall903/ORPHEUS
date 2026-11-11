"""Collision-backed travel for the authored Rocket Punch running state."""

from dataclasses import dataclass
import math

from ow174.game.contacts import SKIN
from ow174.game.mover import STEP
from ow174.game.script import expr, graph, runtime

HERO = 0x02E000000000012F
GRAPH = 0x0D06
LOADOUT = 0x0B90000000000107


@dataclass(frozen=True)
class Dash:
    activation: tuple[int, int, int]
    destination: tuple[float, float, float]
    speed: float


@dataclass(frozen=True)
class WallImpact:
    source: object
    source_body: int
    direction: tuple[float, float, float]
    damage: float
    deadline: float


class Punches:
    def __init__(self):
        self.dashes: dict[int, Dash] = {}
        self.impacts: dict[int, WallImpact] = {}

    def cancel(self, body):
        self.dashes.pop(body, None)

    def retire(self, body):
        self.cancel(body)
        self.impacts.pop(body, None)
        for target, impact in tuple(self.impacts.items()):
            if impact.source_body == body:
                self.impacts.pop(target)

    @staticmethod
    def instance(player):
        script = player.body_script
        if script is None or script.hero != HERO:
            return None
        return next((i for i in script.component.instances.values() if i.graph.index == GRAPH), None)

    def sync(self, player):
        if player.body not in self.dashes:
            return
        instance = self.instance(player)
        motion = instance.states.get(7) if instance is not None else None
        if motion is None or not motion.active:
            self.cancel(player.body)

    def movement(self, player):
        instance = self.instance(player)
        state = instance.states.get(7) if instance is not None else None
        if state is None or not state.active:
            return None
        activation = instance.id, state.counter, state.start
        dash = self.dashes.get(player.body)
        if dash is None or dash.activation != activation:
            duration = expr.to_float(instance.find_var(expr.INSTANCE, 215).value())
            speed = expr.to_float(state.evaluate(state.node.field("m_87A4D0AD"), watch=False))
            direction = state.evaluate(state.node.field("m_B1003775"), watch=False)
            if not isinstance(direction, expr.Vec3):
                return None
            length = math.hypot(*direction)
            if not all(math.isfinite(v) and v > 0 for v in (duration, speed, length)):
                return None
            distance = speed * duration / length
            destination = tuple(p + d * distance for p, d in zip(player.mover.position, direction, strict=True))
            dash = self.dashes[player.body] = Dash(activation, destination, speed)
        return dash.destination, dash.speed

    def contact(self, fight, player, command, start):
        from ow174.game.combat import _along, _dot, _sub, ray_capsule, ray_world

        if fight._charge_counter(player):
            return True
        instance = self.instance(player)
        tracker = instance.states.get(6) if instance is not None else None
        if tracker is None or not tracker.active:
            return False
        delta = _sub(player.mover.position, start)
        distance = math.hypot(*delta)
        radius = expr.to_float(tracker.evaluate(tracker.node.field("m_radius"), watch=False))
        if distance <= expr.EPSILON or radius <= 0:
            return False
        direction = tuple(axis / distance for axis in delta)
        origin = _along(start, (0, 1, 0), fight._eye(player)[1] - player.mover.position[1] - 0.18)
        contacts = []
        for target in fight._enemies(player):
            a, b = target.segment()
            hit = ray_capsule(origin, direction, a, b, target.radius + radius)
            if hit is None or hit > distance:
                continue
            point = (a[0], max(a[1], min(b[1], origin[1])), a[2])
            sight = _sub(point, origin)
            if _dot(sight, direction) < -expr.EPSILON:
                continue
            length = math.hypot(*sight)
            if length > expr.EPSILON:
                toward = tuple(axis / length for axis in sight)
                if (ray_world(fight._collision(), origin, toward, length) is not None
                        or fight.walls.blocked(origin, point)):
                    continue
            contacts.append((hit, target.entity, target))
        if not contacts:
            return False
        _, _, target = min(contacts)
        helper = graph.graph(0x0D07)
        duration, low, high = (expr.to_float(instance.find_var(expr.INSTANCE, index).value())
                               for index in (215, 11271, 567))
        if helper is None or high <= low:
            return False
        charge = max(0.0, min(1.0, (duration - low) / (high - low)))

        def scaled(low_node, high_node):
            a, b = (expr.to_float(instance.evaluate(helper.node(index).field("m_value")))
                    for index in (low_node, high_node))
            return a + (b - a) * charge

        if fight._damage(player, target, scaled(34, 33), False, LOADOUT, command.frame):
            grant_shields(fight, player)
            timeout = expr.to_float(instance.evaluate(helper.node(1).field("m_timeout")))
            fight.control(target, player.body, "rocket_punch", timeout)
            flat = math.hypot(direction[0], direction[2])
            if flat > expr.EPSILON:
                heading = (direction[0] / flat, 0.0, direction[2] / flat)
                speed = scaled(32, 31)
                applied = fight.knockback(target, tuple(axis * speed for axis in heading), command.frame)
                if any(applied) and getattr(target.owner, "mover", None) is not None:
                    self.impacts[target.entity] = WallImpact(
                        player, player.body, heading, scaled(36, 35), fight.now + timeout,
                    )
        return True

    def wall_contact(self, fight, player, command, prior_pose, seconds):
        from ow174.game.combat import _along, _dot, _sub, ray_world

        impact = self.impacts.get(player.body)
        if impact is None:
            return
        target = fight._target_entity(player.body)
        if target is None or fight.now >= impact.deadline:
            self.impacts.pop(player.body, None)
            return
        start, velocity = prior_pose
        mover = player.mover
        unconstrained = tuple(mover.position)
        ice = fight.walls.move_player(player, start)
        approach = _dot(velocity, impact.direction) * seconds
        if approach <= STEP:
            return
        ice_correction = _sub(unconstrained, mover.position)
        if ice is None or _dot(ice_correction, impact.direction) <= expr.EPSILON:
            progress = _dot(_sub(mover.position, start), impact.direction)
            # Static impacts require a blocked step and a wall within the collision skin.
            if progress >= approach / 2:
                return
            origin = _along(mover.position, (0, 1, 0), target.height / 2)
            radius, _ = mover._capsule()
            if ray_world(mover.world, origin, impact.direction, radius + SKIN + STEP, mover.category) is None:
                return
        self.impacts.pop(player.body)
        fight._damage(impact.source, target, impact.damage, False, LOADOUT, command.frame)

    def finish(self, player):
        self.cancel(player.body)
        instance = self.instance(player)
        wait = instance.states.get(5) if instance is not None else None
        if wait is not None and wait.active:
            wait.request(runtime.FINISH)


def grant_shields(fight, player):
    """Use the direct-hit gain and decay values from Doomfist's passive graph."""
    passive = next((instance for instance in player.body_script.component.instances.values()
                    if instance.graph.index == 0x0DDD), None)
    target = fight._target_entity(player.body)
    if passive is None or target is None:
        return
    cap = expr.to_float(passive.find_var(expr.INSTANCE, 11218).value())
    amount, rate, delay = (
        expr.to_float(passive.evaluate(passive.graph.node(pos).field(field)))
        for pos, field in ((20, "m_value"), (3, "m_modifyHealth.m_amount"), (2, "m_timeout"))
    )
    if not all(math.isfinite(value) and value >= 0 for value in (cap, amount, rate, delay)):
        return
    vitals = fight.vitals_of(target)
    if vitals.grant_temporary_shields(amount, cap, fight.now + delay, rate):
        fight._sync_body_health(player, vitals)
