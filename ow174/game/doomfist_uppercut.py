"""Selected Doomfist 0DD8 Uppercut target and impulse authority."""

import math

from ow174.game.doomfist_punch import HERO
from ow174.game.script import expr

GRAPH = 0x0DD8


class Uppercuts:
    def __init__(self, combat):
        self.combat = combat
        self.seen = {}

    def retire(self, body):
        self.seen.pop(body, None)

    def targets(self, state):
        from ow174.game.combat import capsule_visible, segment_distance

        fight = self.combat
        shooter = next((p for p in fight.match.players if p.has_body
                        and p.body == state.instance.component.entity), None)
        if shooter is None or fight.is_dead(shooter):
            return ()
        centre = state.evaluate(state.node.field("m_05BE62DD"), watch=False)
        radius = expr.to_float(state.evaluate(state.node.field("m_radius"), watch=False))
        if not isinstance(centre, expr.Vec3) or radius <= 0:
            return ()
        eye = fight._eye(shooter)
        found = []
        for target in fight._enemies(shooter):
            a, b = target.segment()
            midpoint = tuple((x + y) / 2 for x, y in zip(a, b, strict=True))
            if (segment_distance(tuple(centre), a, b) <= radius + target.radius
                    and not fight.walls.blocked(eye, midpoint)
                    and capsule_visible(fight._collision(), eye, a, b, target.radius)):
                found.append(target.entity)
        return tuple(found)

    def command(self, player, command):
        from ow174.game.combat import aim, hit_def

        if command is None or player.hero.guid != HERO:
            return
        instance = next((i for i in player.body_script.component.instances.values()
                         if i.graph.index == GRAPH), None)
        if instance is None:
            return
        tracker = instance.states.get(9)
        effect = instance.states.get(10)
        lift_seen, damage_seen = self.seen.get(player.body, (0, 0))
        if tracker is not None and tracker.counter != lift_seen:
            lift_seen = tracker.counter
            speed = instance.graph.node(1).field(
                "m_BC5E91CF[2].m_9B7A63EA.m_expression.m_D99EF254[0]",
            )
            if isinstance(speed, (int, float)) and math.isfinite(speed) and speed > 0:
                player.mover.add_external_impulse((0.0, speed, 0.0))
        if effect is not None and effect.active and effect.counter != damage_seen:
            damage_seen = effect.counter
            selected = effect.evaluate(effect.node.field("m_FA3ECC43"), watch=False)
            selected = selected if isinstance(selected, tuple) else (selected,)
            definition = hit_def(effect, {"m_modifyHealth": effect.node.field("m_modifyHealth")})
            if definition is not None:
                fight = self.combat
                dx, _, dz = aim(command.yaw, 0)
                horizontal = expr.to_float(effect.evaluate(
                    effect.node.field("m_modifyHealth.m_E33EFEE1"), watch=False,
                ))
                vertical = expr.to_float(effect.evaluate(
                    effect.node.field("m_modifyHealth.m_583C208B"), watch=False,
                ))
                for target in fight._enemies(player):
                    if expr.Entity(target.entity) not in selected:
                        continue
                    vitals = fight.vitals_of(target)
                    before = vitals.total
                    fight._damage(player, target, definition.amount, False, definition.loadout, command.frame)
                    if vitals.total >= before:
                        continue
                    fight.punches.grant(player)
                    mover = getattr(target.owner, "mover", None)
                    if (mover is not None and not vitals.dead and not fight.control_immune(target.owner)
                            and not fight.captured(target.entity)):
                        mover.add_external_impulse((dx * horizontal, vertical, dz * horizontal))
            effect.request_finish()
            instance.component.run_now()
        self.seen[player.body] = lift_seen, damage_seen
