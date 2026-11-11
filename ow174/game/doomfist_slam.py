"""Doomfist 0B27 Seismic Slam ground and aerial host projection."""

import math
from dataclasses import dataclass

from ow174.game.doomfist_punch import HERO
from ow174.game.script import expr, nodes, runtime

SLAM = 0x0B9000000000011F


@dataclass
class Slam:
    activation: tuple
    direction: tuple
    airborne: bool = False
    origin: tuple | None = None
    damage: float = 0.0
    wave_at: float | None = None
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

    def retire(self, body):
        self.casts.pop(body, None)

    def interrupt(self, player):
        cast = self.casts.get(getattr(player, "body", None))
        if cast is not None and cast.wave_at is None:
            cast.finished = True

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
        from ow174.game.combat import aim

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
            if not cast.finished:
                launch = instance.graph.node(75)
                speed = expr.to_float(launch.field(
                    'm_BC5E91CF[2].m_9B7A63EA.m_expression.m_D99EF254[0]',
                ))
                scalar = expr.to_float(instance.evaluate(
                    launch.field('m_BC5E91CF[2].m_9B7A63EA.m_configVars[1]'),
                ))
                dx, _, dz = cast.direction
                velocity = speed * scalar / math.hypot(1, .3)
                if velocity > 0:
                    player.mover.add_external_impulse((dx * velocity, .3 * velocity, dz * velocity))
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
            cast.wave_at = self.combat.now

    def update(self):
        for body, cast in tuple(self.casts.items()):
            if cast.finished or cast.wave_at is None:
                continue
            player = next((p for p in self.combat.match.players if p.body == body and p.has_body), None)
            instance = self.instance(player) if player is not None else None
            if instance is None or self.combat.is_dead(player):
                self.casts.pop(body, None)
                continue
            self._wave(player, instance, cast)

    def _wave(self, player, instance, cast):
        from ow174.game.combat import _dot, _sub, ray_capsule, ray_world

        wave = instance.graph.node(95)
        speed = float(instance.evaluate(wave.field('m_speed')))
        reach = float(instance.vars[422].value())
        half_angle = math.radians(float(instance.evaluate(wave.field('m_A30B383E'))) / 2)
        radius = min(reach, speed * max(0.0, self.combat.now - cast.wave_at))
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
            midpoint = tuple((x + y) / 2 for x, y in zip(a, b, strict=True))
            toward = _sub(midpoint, origin)
            length = math.sqrt(_dot(toward, toward))
            sight = tuple(v / length for v in toward) if length > 1e-9 else cast.direction
            wall = ray_world(self.combat._collision(), origin, sight, length)
            if ((wall is not None and wall < length)
                    or self.combat.walls.blocked(origin, midpoint)):
                continue
            if ray_capsule(origin, sight, a, b, target.radius) is None:
                continue
            hit_entities.add(target.entity)
            fight = self.combat
            vitals = fight.vitals_of(target)
            before = vitals.total
            fight._damage(player, target, cast.damage, False, SLAM, fight.tick, world_tick=fight.tick)
            if vitals.total < before:
                fight.punches.grant(player)
                mover = getattr(target.owner, "mover", None)
                if (mover is not None and not vitals.dead and not fight.control_immune(target.owner)
                        and not fight.captured(target.entity)):
                    mover.add_external_impulse((-3 * direction[0], 6, -3 * direction[2]))
        cast.hit = tuple(sorted(hit_entities))
        if radius >= reach:
            cast.finished = True
