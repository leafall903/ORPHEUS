"""Resource-scoped server reconstruction of Echo's 19EB body copy."""

import math
from dataclasses import dataclass, replace

from ow174.game import damage_charge
from ow174.game.content import SOLDIER, heroes
from ow174.game.script import expr, graph, nodes

HERO = 0x02E0000000000206
GRAPH = 0x19EB
EFFECT = 0x1544
Q = 32


@dataclass
class Copy:
    body: int
    target: int
    until: int
    most: dict
    now: dict
    return_floor: float
    skin: tuple[int, bool]


class Duplicates:
    def __init__(self, fight):
        self.fight = fight
        self.copies = {}
        self.held = set()
        self.switching = set()
        self.restored = {}

    def retire(self, player):
        self.restored.pop(player.body, None)
        if player.entity not in self.switching:
            self.copies.pop(player.entity, None)
            self.held.discard(player.entity)

    def copied_soldier(self, player):
        copy = self.copies.get(player.entity)
        return (copy is not None and player.hero.guid == SOLDIER
                and copy.body == player.body and self.fight.tick < copy.until)

    def credit_soldier(self, player, amount, target, damage_tags):
        if amount <= 0 or not self.copied_soldier(player):
            return
        script = player.body_script
        instance = damage_charge.admitted_source(script)
        if instance is not None and not any(
            state.flags & nodes.Ability.RUNNING for state in script.ultimates
        ):
            damage_charge.apply(script, instance, amount, target, damage_tags)

    def visor_direction(self, player, command, volley, origin, direction):
        from ow174.game import combat, defenses

        if (volley.instance.graph.index, volley.index) != (0x0254, 10) or not self.copied_soldier(player):
            return direction
        component = player.body_script.component
        visor = next((i for i in component.instances.values() if i.graph.index == 0x0259), None)
        if visor is None:
            return direction
        tracking = visor.states.get(7)
        if tracking is None or not tracking.active or not visor.states[0].flags & nodes.Ability.RUNNING:
            return direction
        reach = expr.to_float(tracking.evaluate(tracking.node.field("m_radius"), watch=False))
        angle = expr.to_float(tracking.evaluate(tracking.node.field("m_A30B383E"), watch=False))
        minimum = math.cos(math.radians(angle))
        view_tick = command.frame - command.view_delay(player.quanta) * 1e6 / player.quanta
        candidates = []
        for target in self.fight._enemies(player):
            if defenses.invulnerable(target.owner):
                continue
            shown = self.fight.seen(target, view_tick)
            if shown is None:
                continue
            a, b = shown.segment()
            centre = tuple((lo + hi) / 2 for lo, hi in zip(a, b, strict=True))
            offset = tuple(point - start for point, start in zip(centre, origin, strict=True))
            distance = math.hypot(*offset)
            if not combat.EPSILON < distance <= reach:
                continue
            ray = tuple(axis / distance for axis in offset)
            alignment = sum(a * b for a, b in zip(direction, ray, strict=True))
            if alignment < minimum:
                continue
            entry = combat.ray_capsule(origin, ray, a, b, shown.radius)
            if (entry is not None and combat.ray_world(self.fight._collision(), origin, ray, entry) is None
                    and self.fight.walls.cast(origin, ray, entry) is None):
                candidates.append(((-alignment, distance, target.entity), ray))
        return min(candidates)[1] if candidates else direction

    def prepare(self, player, command):
        copied = player.entity in self.copies
        if not copied and player.hero.guid != HERO:
            return command
        if not command.buttons & Q:
            self.held.discard(player.entity)
            return command
        if player.entity in self.held:
            return replace(command, buttons=command.buttons & ~Q)
        if copied:
            return command  # a fresh press belongs to the copied hero
        self.held.add(player.entity)
        if self.cast(player, command):
            return None
        return replace(command, buttons=command.buttons & ~Q)

    def select(self, player, command, reach, angle):
        from ow174.game import combat
        from ow174.game.match import Player

        fight = self.fight
        origin, direction = fight._eye(player), combat.aim(*command.aim())
        view_tick = command.frame - command.view_delay(player.quanta) * 1e6 / player.quanta
        threshold = math.cos(math.radians(angle))
        candidates = []
        for target in fight._enemies(player):
            owner = target.owner
            if (not isinstance(owner, Player) or owner.hero.guid == HERO or owner.entity in self.copies
                    or owner.body_script is None or owner.body_script.retired):
                continue
            shown = fight.seen(target, view_tick)
            if shown is None or math.dist(player.mover.position, shown.feet) > reach:
                continue
            bottom, top = shown.segment()
            centre = tuple((a + b) / 2 for a, b in zip(bottom, top, strict=True))
            if fight.walls.blocked(origin, centre):
                continue
            ray = combat.ray_capsule(origin, direction, bottom, top, shown.radius)
            if (ray is not None and ray <= reach
                    and combat.ray_world(fight._collision(), origin, direction, ray) is None):
                candidates.append(((0, ray, 0, target.entity), target))
                continue
            offset = tuple(a - b for a, b in zip(centre, origin, strict=True))
            distance = math.hypot(*offset)
            if 0 < distance <= reach:
                alignment = sum(a * b for a, b in zip(offset, direction, strict=True)) / distance
                if (alignment >= threshold
                        and combat.capsule_visible(fight._collision(), origin, bottom, top, shown.radius)):
                    candidates.append(((1, -alignment, distance, target.entity), target))
        return min(candidates, key=lambda item: item[0])[1] if candidates else None

    def cast(self, player, command):
        from ow174.game import sombra
        from ow174.game.combat import TICK
        from ow174.game.script.driver import ULT_CHARGE, ULT_COST

        script = player.body_script
        fight = self.fight
        if (script is None or script.component.owner_dead or fight.is_stunned(player)
                or script.component.disabled.get(37) or sombra.locked(player, command.frame)):
            return False
        component = script.component
        cost = expr.to_float(component.entity_var(ULT_COST).value())
        if cost <= 0 or expr.to_float(component.entity_var(ULT_CHARGE).value()) < cost:
            return False
        instance = next((i for i in component.instances.values() if i.graph.index == GRAPH), None)
        effect = graph.graph(EFFECT)
        if instance is None or effect is None:
            return False
        query = instance.graph.states[19]
        reach = expr.to_float(instance.evaluate(query.field("m_radius")))
        sensitivity = component.entity_var(29053, create=False)
        angle = (10.0 if sensitivity is None or sensitivity.value() is None
                 else expr.to_float(instance.evaluate(query.field("m_A30B383E"))))
        duration = expr.to_float(instance.vars[567].value())
        if not (math.isfinite(duration) and duration > 0 and math.isfinite(reach)
                and reach > 0 and 0 <= angle <= 180):
            return False
        target = self.select(player, command, reach, angle)
        if target is None:
            return False
        own = next(t for t in fight._targets() if t.owner is player)
        health = fight.vitals_of(own)
        floor = instance.graph.states[49].field("m_modifyHealth")["m_amount"]
        divisor, = floor["m_expression"]["m_D99EF254"]
        copy = Copy(player.body, target.entity, fight.tick + math.ceil(duration / TICK),
                    dict(health.most), dict(health.now), sum(health.most.values()) / divisor, player.skin)
        if not self.switch(player, target.owner.hero, command.frame, (0, False)):
            return False
        copy.body = player.body
        self.copies[player.entity] = copy
        script = player.body_script
        if script is None:
            self.restore(player, frame=command.frame)
            return True
        script.set_ult_charge(0)
        if script.world_frame_anchor is None:
            script.world_frame_anchor = fight.tick, command.frame
        script.ult_gain_bonus = effect.node(4).field("m_value")["m_expression"]["m_D99EF254"][0]
        marker = effect.node(66)
        scope, identifier = expr.dynamic(marker.field("m_out_Var"))
        if scope == expr.ENTITY:
            # Project the resource marker. Its native copy-message subtree is
            # deliberately outside this body-switch reconstruction.
            script.component.set(identifier, bool(marker.field("m_value")["m_value"]))
        copied = next(t for t in fight._targets() if t.owner is player)
        fight._sync_body_health(player, fight.vitals_of(copied))
        return True

    def switch(self, player, hero, frame, skin):
        self.switching.add(player.entity)
        try:
            return self.fight.match.switch_hero(
                player, hero, preserve_pose=True, start_frame=frame, skin=skin,
            )
        finally:
            self.switching.discard(player.entity)

    def restore(self, player, *, frame=None):
        from ow174.game.combat import Vitals
        from ow174.game.world import ARMOUR_POOL, HEALTH_POOL, SHIELDS_POOL

        copy = self.copies.get(player.entity)
        if copy is None or copy.body != player.body:
            return False
        if frame is None and player.body_script is not None:
            frame = player.body_script.frame
        if not self.switch(player, heroes()[HERO], frame, copy.skin):
            return False
        self.copies.pop(player.entity)
        vitals = Vitals(player.body, copy.most[HEALTH_POOL], copy.most[ARMOUR_POOL],
                        copy.most[SHIELDS_POOL])
        vitals.now = copy.now
        vitals.changed.update(vitals.now)
        vitals.heal(copy.return_floor - vitals.total)
        self.fight.vitals[player.body] = vitals
        self.restored[player.body] = self.fight.tick
        self.fight._sync_body_health(player, vitals)
        return True

    def update(self):
        self.restored = {body: tick for body, tick in self.restored.items() if tick == self.fight.tick}
        for player in self.fight.match.players:
            copy = self.copies.get(player.entity)
            if copy is not None and self.fight.tick >= copy.until:
                self.restore(player)
