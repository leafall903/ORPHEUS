"""Reconstructed Doomfist combat hosts using authored graph values.

The body graph still owns input, charge, recovery and cooldown. This adapter
supplies missing movement/contact/PlayScript effects. Capsule reach, impulse,
wall contact and control policy are reconstruction, not native filter fidelity.
"""

import math
from dataclasses import dataclass, replace

from ow174.game import bots, contacts, defenses
from ow174.game.mover import STEP
from ow174.game.script import expr, graph, nodes, runtime

HERO = 0x02E000000000012F
PUNCH = 0x0B90000000000107
SLAM = 0x0B9000000000011F


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
        self.controls: dict[int, float] = {}

    def retire(self, entity: int) -> None:
        self.dashes.pop(entity, None)
        self.controls.pop(entity, None)
        self.impacts[:] = [i for i in self.impacts if i.target != entity and i.source_body != entity]

    def controlled(self, entity: int) -> bool:
        return self.controls.get(entity, -math.inf) > self.combat.now

    def command_input(self, player, command):
        if self.controlled(player.body):
            return replace(command, buttons=0, action=0, forward=0, right=0)
        return command

    def _control(self, target, until: float) -> None:
        self.controls[target.entity] = max(self.controls.get(target.entity, until), until)
        owner = target.owner
        script = getattr(owner, "body_script", None)
        if script is None:
            return
        component = script.component
        component.input_suppressed = True
        if isinstance(owner, bots.Bot):
            from ow174.game import assassin

            assassin.bind(component.world, component, None)
        component.edges([(button, False) for button in tuple(component.held)])
        # Scoped channel policy: Doomfist, Deflect and Kinetic Grasp, not every
        # other hero's timed ultimate. Native status filtering remains open.
        hero = getattr(getattr(owner, "hero", None), "guid", None)
        if hero in (HERO, defenses.GENJI, defenses.SIGMA):
            for instance in tuple(component.instances.values()):
                if hero == defenses.GENJI and instance.graph.index != 0x3A3:
                    continue
                if hero == defenses.SIGMA and instance.graph.index != 0x1D02:
                    continue
                for state in tuple(instance.states.values()):
                    if isinstance(state, nodes.Ability) and state.flags & nodes.Ability.RUNNING:
                        state.stop()

    def update(self) -> None:
        from ow174.game.combat import _along, ray_world

        targets = {t.entity: t for t in self.combat._targets()}
        for entity, until in tuple(self.controls.items()):
            target = targets.get(entity)
            if until > self.combat.now and target is not None and not self.combat.vitals_of(target).dead:
                continue
            del self.controls[entity]
            if target is None:
                continue
            owner = target.owner
            script = getattr(owner, "body_script", None)
            if script is not None:
                script.component.input_suppressed = False
                if isinstance(owner, bots.Bot) and not owner.dead:
                    buttons = {s.button_id for i in script.component.instances.values()
                               for s in i.states.values() if isinstance(s, nodes.AIFireWeapon)
                               and s.active and s.button_id is not None}
                    script.component.edges([(button, True) for button in buttons])
        for impact in tuple(self.impacts):
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
            if wall is not None:
                self.combat._damage(impact.source, target, impact.wall_damage, False, PUNCH, self.combat.tick)
                self.impacts.remove(impact)  # one wall bonus; no second passive shield grant

    def movement(self, player, command):
        dash = self.dashes.get(player.body)
        if dash is None or dash.finished or player.body_script is None:
            return None
        instance = player.body_script.component.instances.get(dash.activation[0])
        state = instance.states.get(7) if instance is not None else None
        if state is None or not state.active or self.controlled(player.body):
            dash.finished = True
            return None
        dash.requested_frame = command.frame
        return dash.destination, dash.speed

    def movement_result(self, player, outcome) -> None:
        dash = self.dashes.get(player.body)
        if dash is not None and outcome is not None:
            dash.blocked = not outcome
            dash.arrived = outcome

    def _create(self, player, instance, state) -> Dash | None:
        duration = float(instance.vars[215].value())
        low, high = float(instance.vars[11271].value()), float(instance.vars[567].value())
        charge = max(0.0, min(1.0, (duration - low) / max(1e-9, high - low)))
        speed = float(state.evaluate(state.node.field("m_87A4D0AD"), watch=False))
        direction = state.evaluate(state.node.field("m_B1003775"), watch=False)
        radius = float(instance.state(6).evaluate(instance.graph.node(14).field("m_radius"), watch=False))
        if not isinstance(direction, expr.Vec3):
            return None
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
        instance = next(i for i in player.body_script.component.instances.values() if i.graph.index == 0x0D06)
        state = instance.state(7)
        activation = (instance.id, state.counter, state.start)
        dash = self.dashes.get(player.body)
        if state.active and (dash is None or dash.activation != activation):
            dash = self._create(player, instance, state)
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
            if wall is None or wall >= length:
                found.append((hit, target))
        if found:
            _, target = min(found, key=lambda item: item[0])
            dealt = self.combat._damage(player, target, dash.damage, False, PUNCH, command.frame)
            if dealt > 0:
                self.combat._doomfist_shields(player, PUNCH)
                if not self.combat.vitals_of(target).dead:
                    dx, _, dz = dash.direction
                    flat = math.hypot(dx, dz) or 1.0
                    direction = (dx / flat, 0.0, dz / flat)
                    self.combat._launch(target.entity, tuple(v * dash.knockback for v in direction))
                    until = self.combat.now + dash.control_seconds
                    self._control(target, until)
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
        player.mover.stop_drive()
        wait = instance.state(5)
        if wait.active:
            wait.request(runtime.FINISH)  # retain authored recovery and cooldown branches


@dataclass
class Slam:
    activation: tuple
    direction: tuple
    airborne: bool = False
    origin: tuple | None = None
    damage: float = 0.0
    wave_at: int | None = None
    hit: tuple = ()
    finished: bool = False
    kind: str = 'ground'
    destination: tuple | None = None
    speed: float = 0.0


class Slams:
    """Ground-hop and aerial Slam hosts with declared spatial approximations.

    Ground observations, launch direction, landing transition and wave geometry
    are reconstructed. Damage charge and recovery/cooldown stay in graph0B27.
    """

    def __init__(self, combat):
        self.combat = combat
        self.casts: dict[int, Slam] = {}

    @staticmethod
    def instance(player):
        if player.hero.guid != HERO or player.body_script is None:
            return None
        component = player.body_script.component
        if component.strict:
            return None
        return next((i for i in component.instances.values() if i.graph.index == 0xB27), None)

    def observe(self, player) -> None:
        from ow174.game.combat import ray_world

        instance = self.instance(player)
        if instance is None:
            return
        airborne = player.mover.airborne
        instance.component.set(417, airborne)  # explicit host interpretation of the ground predicate
        feet = tuple(player.mover.position)
        length = float(instance.graph.node(17).field('m_castLength.m_value'))
        origin = (feet[0], feet[1] + .25, feet[2])
        hit = ray_world(self.combat._collision(), origin, (0, -1, 0), length)
        instance.write((0, 2413), hit is not None)
        cast = self.casts.get(player.body)
        if cast is None or cast.finished or cast.origin is not None:
            return
        cast.airborne |= airborne
        ability = instance.state(0)
        if not ability.flags & nodes.Ability.RUNNING:
            cast.finished = True
        elif cast.airborne and not airborne:
            cast.origin = feet
            player.mover.stop_drive()
            # Native modifier transition/condition7 is not reproduced. Use the
            # collision landing to enter the authored impact/recovery phase.
            instance.write((0, 59), 3)

    def _target(self, player, instance):
        from ow174.game.collision import NOT_GROUND
        from ow174.game.combat import _along, aim, ray_world

        query = instance.states.get(6)
        if query is None or not query.active:
            return
        # Native4C96470A uses a swept/curved target query. This host uses the
        # current downward view ray, a walkable static floor and head clearance.
        # It never accepts a target merely because E is held.
        direction = aim(player.mover.yaw, player.mover.pitch)
        origin = self.combat._eye(player)
        reach = float(query.evaluate(query.node.field('m_6386E7B4'), watch=False))
        distance = (ray_world(self.combat._collision(), origin, direction, reach)
                    if direction[1] < -1e-6 and reach > 0 else None)
        destination = None
        if distance is not None:
            point = _along(origin, direction, distance)
            world = self.combat._collision()
            ground = world.ground(point[0], point[2], point[1] + .1, point[1] - .1,
                                  player.mover.category)
            if (ground is not None and ground.normal[1] > player.mover.slope_cos
                    and not ground.surface & NOT_GROUND):
                floor = (point[0], ground.y, point[2])
                height = player.mover.data.stand_height
                ceiling = ray_world(world, (floor[0], floor[1] + .05, floor[2]), (0, 1, 0), height)
                if ceiling is None:
                    destination = floor
        instance.write((0, 1627), destination is not None)
        if destination is None:
            return
        instance.write((0, 431), expr.Vec3(*destination))
        instance.write((0, 1883), None)  # static world target; no moving entity attachment
        phase = instance.vars.get(59)
        held = instance.states.get(64)
        ability = instance.state(0)
        if (phase is None or phase.value() != 2 or held is None or not held.active or not held.down
                or not ability.flags & nodes.Ability.RUNNING):
            return
        # Run the authored validity/held gate155 and10473 gate76; the host
        # supplies query results, not a forced ability or cooldown reset.
        instance.follow(query.node, 'm_transitionPlug[0]', query)
        instance.follow(query.node, 'm_F6955E81', query)
        instance.component.run_now()
        move = instance.states.get(9)
        if move is not None and move.active:
            speed = float(move.evaluate(move.node.field('m_speed'), watch=False))
            self.casts[player.body] = Slam(
                (instance.id, move.index, move.counter, move.start), aim(player.mover.yaw, 0),
                airborne=player.mover.airborne, kind='aerial', destination=destination, speed=speed,
            )

    def movement(self, player):
        instance = self.instance(player)
        cast = self.casts.get(player.body)
        if instance is None or cast is None or cast.finished or cast.kind != 'aerial':
            return None
        move = instance.states.get(9)
        if (move is not None and move.active and cast.destination is not None and cast.speed > 0
                and instance.state(0).flags & nodes.Ability.RUNNING):
            return move, (expr.Vec3(*cast.destination), cast.speed)
        return None

    def command(self, player, command) -> None:
        from ow174.game.combat import _dot, _sub, aim, ray_capsule, ray_world

        instance = self.instance(player)
        if instance is None or command is None:
            return
        self._target(player, instance)
        phase = instance.state(1)
        ability = instance.state(0)
        cast = self.casts.get(player.body)
        activation = (instance.id, phase.index, phase.counter, phase.start)
        if phase.active and ability.flags & nodes.Ability.RUNNING and (
            cast is None or cast.activation != activation
        ):
            cast = Slam(activation, aim(player.mover.yaw, 0))
            # Never turn an old unposed activation into a fresh endpoint effect.
            cast.finished = phase.start < instance.component.now - instance.component.frame_ms
            self.casts[player.body] = cast
        if cast is None or cast.finished:
            return
        if cast.kind == 'aerial' and cast.origin is None and instance.vars[59].value() == 3:
            cast.origin = tuple(player.mover.position)
            player.mover.stop_drive()
        if cast.origin is None:
            cast.airborne |= player.mover.airborne
            if not ability.flags & nodes.Ability.RUNNING:
                cast.finished = True
            return
        recovery = instance.states.get(37)
        if cast.wave_at is None:
            if recovery is None or not recovery.active:
                return
            # Unknown native54FEB21C completion is approximated by ending this
            # impact gate after the authored0.1s pre-impact wait has elapsed.
            recovery.request(runtime.FINISH)
            cast.damage = float(instance.vars[476].value())
            cast.wave_at = instance.component.now
        wave = instance.graph.node(95)
        speed = float(instance.evaluate(wave.field('m_speed')))
        reach = float(instance.vars[422].value())
        half_angle = math.radians(float(instance.evaluate(wave.field('m_A30B383E'))) / 2)
        radius = min(reach, speed * (instance.component.now - cast.wave_at) / 1000)
        origin = (cast.origin[0], cast.origin[1] + 1, cast.origin[2])
        hit_entities = set(cast.hit)
        for target in self.combat._enemies(player):
            if target.entity in hit_entities:
                continue
            offset = _sub(target.feet, cast.origin)
            distance = math.hypot(offset[0], offset[2])
            if distance > radius + target.radius or abs(offset[1]) > 1.45:
                continue
            direction = (offset[0] / distance, 0, offset[2] / distance) if distance > 1e-9 else cast.direction
            if _dot(direction, cast.direction) < math.cos(half_angle):
                continue
            a, b = target.segment()
            toward = _sub(tuple((x + y) / 2 for x, y in zip(a, b, strict=True)), origin)
            length = math.sqrt(_dot(toward, toward))
            sight = tuple(v / length for v in toward) if length > 1e-9 else cast.direction
            wall = ray_world(self.combat._collision(), origin, sight, length)
            if wall is not None and wall < length:
                continue
            if ray_capsule(origin, sight, a, b, target.radius) is None:
                continue
            hit_entities.add(target.entity)
            dealt = self.combat._damage(player, target, cast.damage, False, SLAM, command.frame)
            if dealt > 0:
                self.combat._doomfist_shields(player, SLAM)
                if not self.combat.vitals_of(target).dead:
                    # Authored wave effect: horizontal-3 and vertical6. Native
                    # initial close-contact impulse and wave filters stay open.
                    self.combat._launch(target.entity, (-3 * direction[0], 6, -3 * direction[2]))
        cast.hit = tuple(sorted(hit_entities))
        if radius >= reach:
            cast.finished = True
