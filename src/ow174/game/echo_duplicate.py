"""Reconstructed Echo Duplicate body lifecycle; native transition/charge services stay open.

Resource-backed: Echo cost#338 and graph19EB duration#567 / target radius40.
This manager mounts an explicit enemy-player copy using ordinary body ownership.
Preserved health/pose, immediate transform/return and inherited copied-hero behavior
are reconstruction policy. It does not claim native animations, charge watcher scheduling,
transformation protection, all copied abilities or complete cross-hero acceptance.
"""
import math
from dataclasses import dataclass

from ow174.game.content import heroes
from ow174.game.script import graph
from ow174.game.script.driver import ULT_CHARGE, ULT_COST

ECHO = 0x02E0000000000206
Q = 0x20


def _copy_charge_bonus():
    # 19EB/155 plays 1544 on the copied body. Its node 4 adds the bonus
    # multiple of the positive meter delta and caps at the copied cost.
    effect = graph.graph(0x1544)
    if effect is None:  # Older reduced graph bundles omit the child.
        return 0.0
    return float(effect.node(4).field('m_value')['m_expression']['m_D99EF254'][0])


@dataclass
class Copy:
    body: int
    original_most: dict
    original_now: dict
    expires: float
    target_body: int
    copied_hero: int


class Duplicates:
    def __init__(self, combat):
        self.combat = combat
        self.states = {}  # controller -> Copy; never keyed by a body being replaced
        self.held = {}  # successful activation latch, released by actual Q-up
        self.busy = set()  # only the synchronous owned switch/return operation

    def active(self, player):
        state = self.states.get(player.entity)
        return state is not None and state.body == player.body

    def retired(self, player):
        if player.entity not in self.busy:
            self.states.pop(player.entity, None)
            self.held.pop(player.entity, None)

    def _target(self, player, command, reach, angle):
        from ow174.game import combat
        from ow174.game.match import Player

        fight = self.combat
        origin = fight._eye(player)
        direction = combat.aim(*command.aim())
        from ow174.game import echo_clock

        view = echo_clock.view_world_tick(player.body_script, command, player.quanta)
        candidates = []
        collision = fight._collision()
        threshold = math.cos(math.radians(angle))
        for target in fight._enemies(player):
            owner = target.owner
            if not isinstance(owner, Player) or owner.hero.guid == ECHO or self.active(owner):
                continue
            if owner.hero.guid not in heroes():
                continue
            shown = fight.seen(target, view)
            if shown is None or math.dist(player.mover.position, shown.feet) > reach:
                continue
            a, b = shown.segment()
            distance = combat.ray_capsule(origin, direction, a, b, shown.radius)
            if distance is not None and distance <= reach:
                wall = combat.ray_world(collision, origin, direction, distance)
                if wall is None or wall >= distance:
                    candidates.append(((0, distance, 0.0, target.entity), target))
                    continue
            centre = tuple((x + y) * .5 for x, y in zip(a, b, strict=True))
            offset = tuple(x - y for x, y in zip(centre, origin, strict=True))
            length = math.sqrt(sum(axis * axis for axis in offset))
            if not 0 < length <= reach:
                continue
            alignment = sum(x * y for x, y in zip(offset, direction, strict=True)) / length
            if alignment >= threshold and combat.capsule_visible(collision, origin, a, b, shown.radius):
                candidates.append(((1, -alignment, length, target.entity), target))
        return min(candidates, key=lambda candidate: candidate[0])[1] if candidates else None

    def command(self, player, command):
        """Before the old body's VM: successful activation consumes this posed input."""
        if command is None:
            return False
        if not command.buttons & Q:
            self.held[player.entity] = False
        if self.active(player) or player.hero.guid != ECHO:
            return False
        if not command.buttons & Q or self.held.get(player.entity, False):
            return False
        script = player.body_script
        if (script is None or script.component.owner_dead or self.combat.is_dead(player)
                or script.component.disabled.get(37)):
            return False
        cost = float(script.component.entity_var(ULT_COST).value() or 0)
        charge = float(script.component.entity_var(ULT_CHARGE).value() or 0)
        if cost <= 0 or charge < cost:
            return False
        instance = next((i for i in script.component.instances.values() if i.graph.index == 0x19EB), None)
        if instance is None:
            return False
        query = instance.graph.states[19]
        radius = float(instance.evaluate(query.field('m_radius')))
        sensitivity = script.component.entity_var(29053, create=False)
        # The engine's absent-input default is unresolved. Use neutral sensitivity in the sandbox.
        angle = 10.0 if sensitivity is None else float(instance.evaluate(query.field('m_A30B383E')))
        if not math.isfinite(radius) or not math.isfinite(angle) or radius <= 0 or not 0 <= angle <= 180:
            return False
        duration = float(instance.vars[567].value())
        target = self._target(player, command, radius, angle)
        if target is None:
            return False
        charge_bonus = _copy_charge_bonus()
        own = next(t for t in self.combat._targets() if t.owner is player)
        vitals = self.combat.vitals_of(own)
        state = Copy(player.body, dict(vitals.most), dict(vitals.now),
                     self.combat.now + duration, target.entity, target.owner.hero.guid)
        self.busy.add(player.entity)
        try:
            if not self.combat.match.switch_hero(player, target.owner.hero,
                                                 preserve_pose=True, start_frame=command.frame):
                return False
            state.body = player.body
            self.states[player.entity] = state
            self.held[player.entity] = True
            if player.body_script is not None:
                player.body_script.set_ult_charge(0)
                player.body_script.ult_gain_bonus = charge_bonus
            copied = next(t for t in self.combat._targets() if t.owner is player)
            self.combat._sync_body_health(player, self.combat.vitals_of(copied))
            return True
        finally:
            self.busy.discard(player.entity)

    def restore(self, player):
        from ow174.game.combat import Vitals
        from ow174.game.world import ARMOUR_POOL, HEALTH_POOL, SHIELDS_POOL

        if not self.active(player):
            return False
        state = self.states[player.entity]
        self.busy.add(player.entity)
        try:
            if not self.combat.match.switch_hero(player, heroes()[ECHO], preserve_pose=True):
                self.states.pop(player.entity, None)
                return False
            vitals = Vitals(player.body, state.original_most[HEALTH_POOL],
                            state.original_most[ARMOUR_POOL], state.original_most[SHIELDS_POOL])
            vitals.now = dict(state.original_now)
            vitals.changed.update(vitals.now)
            # 19EB state 49 supplies max health / 2 minus current health.
            # Apply the OW1 return floor to Echo's restored pools without combat credit.
            vitals.heal(vitals.maximum / 2 - vitals.total)
            self.combat.vitals[player.body] = vitals
            self.combat._sync_body_health(player, vitals)
            if player.body_script is not None:
                player.body_script.set_ult_charge(0)
            self.states.pop(player.entity, None)
            return True
        finally:
            self.busy.discard(player.entity)

    def update(self, now):
        for controller, state in tuple(self.states.items()):
            player = next((p for p in self.combat.match.players if p.entity == controller), None)
            if player is None or player.gone or player.body != state.body:
                self.states.pop(controller, None)
                self.held.pop(controller, None)
            elif now >= state.expires:
                self.restore(player)
