"""Selected Doomfist Rocket Punch authority from build104319 0D06/0D07."""

import math
from dataclasses import dataclass

from ow174.game import contacts
from ow174.game.mover import STEP
from ow174.game.script import expr, graph, runtime

HERO = 0x02E000000000012F
PUNCH = 0x0B90000000000107
SHIELD_TAG = 0x0580000000000DDD


@dataclass
class Dash:
    activation: tuple
    last: tuple
    direction: tuple
    destination: tuple
    speed: float
    radius: float
    damage: float
    wall_damage: float
    knockback: float
    control_seconds: float
    finished: bool = False
    blocked: bool = False
    arrived: bool = False
    requested_frame: int | None = None


@dataclass
class Impact:
    source: object
    source_body: int
    target: int
    direction: tuple
    wall_damage: float
    until: float


class Punches:
    def __init__(self, combat):
        self.combat = combat
        self.dashes: dict[int, Dash] = {}
        self.impacts: list[Impact] = []
        self.shield_decay = {}

    def retire(self, entity: int) -> None:
        self.dashes.pop(entity, None)
        self.shield_decay.pop(entity, None)
        self.impacts[:] = [i for i in self.impacts if i.target != entity and i.source_body != entity]

    def interrupt(self, player):
        dash = self.dashes.get(getattr(player, "body", None))
        if dash is not None and not dash.finished:
            dash.finished = True

    def pushes(self, entity):
        return any(impact.target == entity for impact in self.impacts)

    def grant(self, player, *, ultimate=False):
        from ow174.game.combat import _number

        passive = next((i for i in player.body_script.component.instances.values()
                        if i.graph.index == 0x0DDD), None)
        fight = self.combat
        target = next((target for target in fight._targets() if target.owner is player), None)
        if passive is None or target is None:
            return
        gain_node = passive.graph.node(21 if ultimate else 20)
        gain = _number(passive.evaluate(gain_node.field("m_value")), 0.0)
        cap = _number(passive.find_var(0, 11218).value(), 0.0)
        rate = _number(passive.state(1).evaluate(passive.graph.node(3).field("m_modifyHealth.m_amount")), 0.0)
        delay = _number(passive.state(0).evaluate(passive.graph.node(2).field("m_timeout")), 0.0)
        if min(gain, cap, rate, delay) <= 0 or not all(math.isfinite(v) for v in (gain, cap, rate, delay)):
            return
        vitals = fight.vitals_of(target)
        vitals.grant_temporary_shields(SHIELD_TAG, gain, cap)
        self.shield_decay[player.body] = player, fight.now + delay, rate
        fight._sync_body_health(player, vitals)

    def update(self) -> None:
        fight = self.combat
        for body, (player, last, rate) in tuple(self.shield_decay.items()):
            if fight.now <= last:
                continue
            vitals = fight.vitals.get(body)
            if vitals is None or vitals.dead or SHIELD_TAG not in vitals.temporary_shields:
                self.shield_decay.pop(body, None)
                continue
            vitals.decay_temporary_shields(SHIELD_TAG, (fight.now - last) * rate)
            fight._sync_body_health(player, vitals)
            self.shield_decay[body] = player, fight.now, rate
        if not self.impacts:
            return
        from ow174.game.combat import _along, ray_world

        targets = {t.entity: t for t in self.combat._targets()}
        for impact in tuple(self.impacts):
            if impact not in self.impacts:
                continue
            target = targets.get(impact.target)
            expired = (impact.until <= self.combat.now or target is None
                       or impact.source.body != impact.source_body)
            if not expired and self.combat.vitals_of(target).dead:
                expired = True
            if expired:
                self.impacts.remove(impact)
                continue
            origin = _along(target.feet, (0.0, 1.0, 0.0), target.height * 0.5)
            # Use the engine's contact skin plus the mover's1/1024m rounding.
            # The old0.12m look-ahead awarded damage before any skin contact.
            reach = target.radius + contacts.SKIN + STEP
            wall = ray_world(self.combat._collision(), origin, impact.direction, reach)
            wall = self.combat.walls.distance(origin, impact.direction, reach, wall)
            if wall is not None:
                self.impacts.remove(impact)  # death can retire the target's other impacts
                self.combat._damage(impact.source, target, impact.wall_damage, False, PUNCH, self.combat.tick)

    def movement(self, player, command):
        dash = self.dashes.get(player.body)
        if dash is None or dash.finished or player.body_script is None:
            return None
        instance = player.body_script.component.instances.get(dash.activation[0])
        state = instance.states.get(7) if instance is not None else None
        if state is None or not state.active or self.combat.is_stunned(player):
            dash.finished = True
            return None
        dash.requested_frame = command.frame
        return dash.destination, dash.speed

    def movement_result(self, player, outcome) -> None:
        dash = self.dashes.get(player.body)
        if dash is not None and outcome is not None:
            dash.blocked = not outcome
            dash.arrived = outcome

    def _create(self, player, instance, state, command) -> Dash | None:
        duration = float(instance.vars[215].value())
        low, high = float(instance.vars[11271].value()), float(instance.vars[567].value())
        charge = max(0.0, min(1.0, (duration - low) / max(1e-9, high - low)))
        speed = float(state.evaluate(state.node.field("m_87A4D0AD"), watch=False))
        direction = state.evaluate(state.node.field("m_B1003775"), watch=False)
        radius = float(instance.state(6).evaluate(instance.graph.node(14).field("m_radius"), watch=False))
        if not isinstance(direction, expr.Vec3):
            from ow174.game.combat import aim

            dx, _, dz = aim(command.yaw, 0)
            direction = expr.Vec3(dx, 0.0, dz)
        length = math.sqrt(sum(v * v for v in direction))
        if not all(math.isfinite(v) and v > 0 for v in (speed, duration, radius, length)):
            return None
        direction = tuple(v / length for v in direction)
        helper = graph.graph(0x0D07)

        def literal(position):
            return float(instance.evaluate(helper.node(position).field("m_value")))

        def interpolate(low_node, high_node):
            a, b = literal(low_node), literal(high_node)
            return a + (b - a) * charge

        feet = tuple(player.mover.position)
        return Dash(
            (instance.id, state.counter, state.start), feet, direction,
            tuple(p + d * speed * duration for p, d in zip(feet, direction, strict=True)),
            speed, radius, interpolate(34, 33), interpolate(36, 35), interpolate(32, 31),
            float(instance.evaluate(helper.node(1).field("m_timeout"))),
        )

    def command(self, player, command) -> None:
        from ow174.game.combat import _along, _dot, _sub, ray_capsule, ray_world

        if player.hero.guid != HERO or player.body_script is None or command is None:
            return
        instance = next((i for i in player.body_script.component.instances.values()
                         if i.graph.index == 0x0D06), None)
        if instance is None:
            return
        state = instance.state(7)
        activation = (instance.id, state.counter, state.start)
        dash = self.dashes.get(player.body)
        if state.active and (dash is None or dash.activation != activation):
            dash = self._create(player, instance, state, command)
            if dash is not None:
                self.dashes[player.body] = dash
        if dash is None or dash.finished:
            return
        if not state.active and dash.requested_frame != command.frame:
            dash.finished = True
            return
        current = tuple(player.mover.position)
        delta = _sub(current, dash.last)
        distance = math.sqrt(_dot(delta, delta))
        heading = tuple(v / distance for v in delta) if distance > 1e-9 else dash.direction
        origin = _along(dash.last, (0, 1, 0), self.combat._eye(player)[1] - current[1] - 0.18)
        found = []
        for target in self.combat._enemies(player):
            a, b = target.segment()
            hit = ray_capsule(origin, heading, a, b, target.radius + dash.radius)
            if hit is None or hit > distance + 1e-9:
                continue
            midpoint = tuple((x + y) * 0.5 for x, y in zip(a, b, strict=True))
            toward = _sub(midpoint, origin)
            length = math.sqrt(_dot(toward, toward))
            sight = tuple(v / length for v in toward) if length > 1e-9 else heading
            wall = ray_world(self.combat._collision(), origin, sight, length)
            if (wall is None or wall >= length) and not self.combat.walls.blocked(origin, midpoint):
                found.append((hit, target))
        if found:
            _, target = min(found, key=lambda item: item[0])
            fight = self.combat
            vitals = fight.vitals_of(target)
            before = vitals.total
            fight._damage(player, target, dash.damage, False, PUNCH, command.frame)
            if vitals.total < before:
                self.grant(player)
            mover = getattr(target.owner, "mover", None)
            if (mover is not None and not vitals.dead and vitals.total < before
                    and not fight.control_immune(target.owner) and not fight.captured(target.entity)):
                dx, _, dz = dash.direction
                flat = math.hypot(dx, dz) or 1.0
                direction = (dx / flat, 0.0, dz / flat)
                mover.add_external_impulse(tuple(v * dash.knockback for v in direction))
                until = fight.now + dash.control_seconds
                fight._apply_stun(target, dash.control_seconds, extend_only=True)
                self.impacts.append(
                    Impact(player, player.body, target.entity, direction, dash.wall_damage, until),
                )
            self._finish(player, instance, dash)
        elif dash.blocked or dash.arrived or not state.active:
            self._finish(player, instance, dash)
        dash.last = current

    @staticmethod
    def _finish(player, instance, dash) -> None:
        dash.finished = True
        player.mover.driven = False
        player.mover.velocity = (0.0, 0.0, 0.0)
        wait = instance.state(5)
        if wait.active:
            wait.request(runtime.FINISH)  # retain authored recovery and cooldown branches
