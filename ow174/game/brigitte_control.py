"""Selected authored Brigitte Bash and Whip contacts for Widowmaker combat."""

import math
from dataclasses import dataclass

from ow174.game import sigma_accretion
from ow174.game.brigitte_shield import GRAPH as BARRIER
from ow174.game.brigitte_shield import HERO
from ow174.game.script import expr, nodes, play

WHIP = 0x1259


@dataclass
class Whip:
    owner: object
    body: int
    instance: object
    position: tuple
    direction: tuple
    speed: float
    remaining: float
    amount: float
    loadout: int
    last: float


class BrigitteControl:
    def __init__(self, combat):
        self.combat = combat
        self.whips = []
        self.seen = {}

    def retire(self, body):
        self.whips[:] = [flight for flight in self.whips if flight.body != body]
        for key in tuple(self.seen):
            if key[0] == body:
                del self.seen[key]

    def prepare(self, player, command, script):
        if player.hero.guid != HERO:
            return
        for instance in script.component.instances.values():
            if instance.graph.index == BARRIER:
                self._prepare_bash(player, command, instance)

    def _prepare_bash(self, player, command, instance) -> None:
        """Reconstruct TrackTargets12's nearest enemy capsule/cone and static LOS.

        The actual BooleanSwitch11 observes375 and starts ModifyHealth10.
        Exact native filter/animated contact and rewind policy remain open.
        """
        from ow174.game.combat import _dot, _number, _sub, aim, ray_world, segment_distance

        tracker = instance.states.get(12)
        found = []
        if tracker is not None and tracker.active:
            radius = _number(instance.vars[9149].value(), 0.0)
            angle = _number(instance.evaluate(tracker.node.field("m_A30B383E")), 0.0)
            x, y, z = player.mover.position
            origin, direction = (x, y + 1, z), aim(*command.aim())
            for target in self.combat._enemies(player):
                a, b = target.segment()
                if segment_distance(origin, a, b) - target.radius > radius:
                    continue
                centre = (a[0], min(b[1], max(a[1], origin[1])), a[2])
                delta = _sub(centre, origin)
                distance = math.sqrt(_dot(delta, delta))
                if distance > 1e-9:
                    toward = tuple(value / distance for value in delta)
                    # TrackTargets12's opcode49 atan2 expression returns degrees.
                    if _dot(direction, toward) < math.cos(math.radians(angle / 2)):
                        continue
                    wall = ray_world(self.combat._collision(), origin, toward, distance)
                    if ((wall is not None and wall < distance - target.radius)
                            or self.combat.walls.blocked(origin, centre)
                            or self.combat._shield_hit(player, origin, toward,
                                                       max(0, distance - target.radius)) is not None):
                        continue
                found.append((distance, target.entity))
        entity = min(found)[1] if found else None
        instance.write((expr.INSTANCE, 375), (expr.Entity(entity),) if entity is not None else ())

    def command(self, player, command):
        if command is None or player.hero.guid != HERO:
            return
        for instance in tuple(player.body_script.component.instances.values()):
            if instance.graph.index == BARRIER:
                self._bash(player, command, instance)
            elif instance.graph.index == WHIP:
                self._launch_whip(player, command, instance)

    def _bash(self, player, command, instance):
        from ow174.game.combat import _number

        state = instance.states.get(10)
        if state is None or not state.active:
            return
        modify = state.node.field("m_modifyHealth") or {}
        amount = _number(state.evaluate(modify.get("m_amount"), watch=False), 0.0)
        loadout = state.evaluate(modify.get("m_loadout"), watch=False)
        selected = state.evaluate(state.node.field("m_FA3ECC43"), watch=False)
        resource = play.resolve_resource(instance, instance.graph.node(13))
        state.request_finish()
        instance.component.run_now()
        if not isinstance(selected, tuple) or not isinstance(loadout, expr.Asset):
            return
        fight = self.combat
        for target in fight._enemies(player):
            if expr.Entity(target.entity) not in selected:
                continue
            vitals = fight.vitals_of(target)
            before = vitals.total
            fight._damage(player, target, amount, False, loadout.guid, command.frame)
            if vitals.total < before:
                sigma_accretion.apply_224e_control(fight, target, resource)

    def _launch_whip(self, player, command, instance):
        from ow174.game.combat import _number, aim

        phase = instance.states.get(6)
        ability = instance.states.get(0)
        if phase is None or not phase.active or ability is None or not ability.flags & nodes.Ability.RUNNING:
            return
        key = player.body, instance.id
        if self.seen.get(key) == phase.counter:
            return
        self.seen[key] = phase.counter
        node = next(node for node in instance.graph.nodes if node.state == 17)
        modify = node.field("m_modifyHealth") or {}
        amount = _number(instance.evaluate(modify.get("m_amount")), 0.0)
        loadout = instance.evaluate(modify.get("m_loadout"))
        speed = _number(instance.vars[681].value(), 0.0)
        reach = _number(instance.vars[1648].value(), 0.0)
        if min(amount, speed, reach) > 0 and isinstance(loadout, expr.Asset):
            fight = self.combat
            self.whips.append(Whip(player, player.body, instance, fight._eye(player), aim(*command.aim()),
                                   speed, reach, amount, loadout.guid, fight.now))

    def update(self):
        from ow174.game.combat import _along, _number, ray_capsule, ray_world

        fight = self.combat
        for flight in tuple(self.whips):
            player = flight.owner
            ability = flight.instance.states.get(0)
            if (player.body != flight.body or fight.is_dead(player)
                    or ability is None or not ability.flags & nodes.Ability.RUNNING):
                self.whips.remove(flight)
                continue
            step = min(flight.remaining, flight.speed * max(0.0, fight.now - flight.last))
            flight.last = fight.now
            if step <= 0:
                continue
            best = None
            for target in fight._enemies(player):
                distance = ray_capsule(
                    flight.position, flight.direction, *target.segment(), target.radius + 0.5,
                )
                if distance is not None and distance <= step and (best is None or distance < best[0]):
                    best = distance, target
            wall = ray_world(fight._collision(), flight.position, flight.direction, step)
            wall = fight.walls.distance(flight.position, flight.direction, step, wall)
            reach = min(step, wall if wall is not None else step, best[0] if best is not None else step)
            barrier = fight._shield_hit(player, flight.position, flight.direction, reach)
            if barrier is not None and (wall is None or barrier[0] < wall):
                barrier[1].damage(flight.amount)
            elif best is not None and (wall is None or best[0] < wall):
                target = best[1]
                if not fight._genji_deflects(target, flight.direction):
                    vitals = fight.vitals_of(target)
                    before = vitals.total
                    fight._damage(player, target, flight.amount, False, flight.loadout, fight.tick)
                    mover = getattr(target.owner, "mover", None)
                    if (mover is not None and not vitals.dead and vitals.total < before
                            and not fight.control_immune(target.owner)):
                        settings = flight.instance.graph.node(32).field("m_modifyHealth")
                        horizontal = _number(flight.instance.evaluate(settings.get("m_E33EFEE1")), 0.0)
                        vertical = _number(flight.instance.evaluate(settings.get("m_583C208B")), 0.0)
                        dx, _, dz = flight.direction
                        length = math.hypot(dx, dz)
                        if length > 1e-9 and (horizontal or vertical):
                            scale = horizontal / length
                            mover.add_external_impulse((dx * scale, vertical, dz * scale))
            elif wall is None and flight.remaining > step:
                flight.position = _along(flight.position, flight.direction, step)
                flight.remaining -= step
                continue
            self.whips.remove(flight)
