"""Selected Reinhardt 007D Charge movement and player pin reconstruction."""

import math
from dataclasses import dataclass, field

from ow174.game import bots, earthshatter
from ow174.game.script import expr

GRAPH = 0x7D


@dataclass
class Run:
    owner: object
    body: int
    instance: object
    counter: int
    heading: float
    hit: set = field(default_factory=set)
    victim: int | None = None


class Charges:
    def __init__(self, combat):
        self.combat = combat
        self.live = {}

    def controlled(self, body):
        return any(run.victim == body for run in self.live.values())

    def retire(self, body):
        self.live.pop(body, None)
        for run in self.live.values():
            if run.victim == body:
                run.victim = None

    def interrupt(self, actor):
        run = self.live.pop(getattr(actor, "body", None), None)
        if run is not None:
            run.instance.write((0, 59), 0)
            run.instance.component.run_now()

    def prepare(self, player):
        if player.hero.guid != earthshatter.HERO:
            return
        instance = next((i for i in player.body_script.component.instances.values()
                         if i.graph.index == GRAPH), None)
        marker = instance.states.get(37) if instance is not None else None
        if marker is not None and marker.active and marker.node.cls == "STU_54FEB21C" and all(
            marker.node.field(name) is None for name in (
                "m_ED97A97C", "m_7C8AD485", "m_C7A42E2A", "m_F8CE386A", "m_C46403A5",
            )
        ):
            marker.request_finish()

    def drive(self, player, command, seconds):
        if player.hero.guid != earthshatter.HERO:
            return None
        script = player.body_script
        instance = next((i for i in script.component.instances.values() if i.graph.index == GRAPH), None)
        phase = instance.states.get(3) if instance is not None else None
        if phase is None or not phase.active or self.combat.is_stunned(player):
            return None
        speed = expr.to_float(instance.find_var(0, 7726).value()) * 60
        if speed <= 0:
            return None
        wanted = command.yaw * math.tau / 65536
        run = self.live.get(player.body)
        if run is None or run.counter != phase.counter:
            run = self.live[player.body] = Run(player, player.body, instance, phase.counter, wanted)
        limit = math.radians(expr.to_float(instance.find_var(0, 34534).value())) * seconds
        delta = (wanted - run.heading + math.pi) % math.tau - math.pi
        run.heading += max(-limit, min(limit, delta))
        direction = math.sin(run.heading), 0.0, math.cos(run.heading)
        from ow174.game.combat import _along

        return _along(player.mover.position, direction, speed * seconds + 1), speed

    def update(self):
        if not self.live:
            return
        fight = self.combat
        targets = {t.entity: t for t in fight._targets()}
        for body, run in tuple(self.live.items()):
            phase = run.instance.states.get(3)
            victim = targets.get(run.victim)
            if (run.owner.body != body or body not in targets or fight.is_dead(run.owner)
                    or fight.is_stunned(run.owner)):
                self.live.pop(body, None)
            elif phase is None or not phase.active or phase.counter != run.counter:
                self.live.pop(body, None)
                if victim is not None:
                    self._impulse(run, victim, 49)
            elif run.victim is not None and (victim is None or fight.vitals_of(victim).dead):
                run.victim = None

    def _damage(self, run, target, index, frame):
        state = run.instance.state(index)
        modify = state.node.field("m_modifyHealth") or {}
        amount = expr.to_float(state.evaluate(modify.get("m_amount"), watch=False))
        loadout = state.evaluate(modify.get("m_loadout"), watch=False)
        self.combat._damage(run.owner, target, amount, False,
                            loadout.guid if isinstance(loadout, expr.Asset) else None, frame)

    def _impulse(self, run, target, index):
        fight = self.combat
        if fight.vitals_of(target).dead or fight.control_immune(target.owner):
            return
        state = run.instance.state(index)
        modify = state.node.field("m_modifyHealth") or {}
        horizontal = expr.to_float(state.evaluate(modify.get("m_E33EFEE1"), watch=False))
        vertical = expr.to_float(state.evaluate(modify.get("m_583C208B"), watch=False))
        if horizontal or vertical:
            target.owner.mover.add_external_impulse((
                math.sin(run.heading) * horizontal, vertical, math.cos(run.heading) * horizontal,
            ))

    def contacts(self, player, start, command, seconds, blocked):
        from ow174.game.combat import _along, capsule_visible, ray_capsule

        run = self.live.get(player.body)
        if run is None:
            return
        fight = self.combat
        direction = math.sin(run.heading), 0.0, math.cos(run.heading)
        end = tuple(player.mover.position)
        origin = start[0], start[1] + 1, start[2]
        travelled = math.dist(start, end)
        radius = expr.to_float(run.instance.find_var(0, 9149).value())
        width = expr.to_float(run.instance.find_var(0, 11861).value())
        enemies = [target for target in fight._enemies(player) if not isinstance(target.owner, bots.Bot)]
        for target in sorted(enemies, key=lambda row: (math.dist(row.feet, start), row.entity)):
            if target.entity in run.hit:
                continue
            a, b = target.segment()
            contact = ray_capsule(origin, direction, a, b, target.radius + radius)
            if contact is None or contact > travelled:
                continue
            centre = target.feet[0], target.feet[1] + 1, target.feet[2]
            if (fight.walls.blocked(origin, centre)
                    or not capsule_visible(fight._collision(), origin, a, b, target.radius)):
                continue
            run.hit.add(target.entity)
            pin = ray_capsule(origin, direction, a, b, target.radius + width)
            if (run.victim is None and pin is not None and pin <= travelled + radius
                    and not fight.control_immune(target.owner) and not fight.captured(target.entity)
                    and not fight.zarya_barriers.protects(target.owner)):
                run.victim = target.entity
                fight._interrupt(target)
            else:
                vitals = fight.vitals_of(target)
                before = vitals.total
                self._damage(run, target, 15, command.frame)
                if vitals.total < before:
                    self._impulse(run, target, 15)
        target = next((t for t in enemies if t.entity == run.victim), None)
        if target is not None:
            mover = target.owner.mover
            gap = player.mover.data.stand_radius + target.radius + .02
            destination = _along(end, direction, gap)
            previous = tuple(mover.position)
            mover._move_to(seconds, destination, math.dist(previous, destination) / seconds + 1)
            fight.walls.move_player(target.owner, previous)
            if mover.history:
                mover.history[-1] = mover.snapshot(mover.history[-1].frame)
            blocked = blocked or math.dist(mover.position, destination) > .03
        if blocked:
            if target is not None:
                self._damage(run, target, 12, command.frame)
            run.instance.write((0, 59), 0)
            run.instance.component.run_now()
            self.live.pop(player.body, None)
