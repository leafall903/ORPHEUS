"""Authored sentry launch/health/beam/slow inputs with a reconstructed world host.

This owns forward flight, attachment, acquisition and effect lifetimes. Client
Pet/projectile reconciliation and surface-oriented hit geometry remain separate
acceptance items. External graph/entity shards supply the missing actor inputs.
"""

import math
from dataclasses import dataclass

from ow174.game import pools
from ow174.game.mover import MoverData
from ow174.game.script import expr, graph, runtime
from ow174.game.symmetra import HERO

BODY = 0x0400000000002658
LAUNCH_GRAPH, ATTACH_GRAPH, BEAM_GRAPH, SLOW_GRAPH = 0x157E, 0x157F, 0x1590, 0x01E7


@dataclass
class Sentry:
    owner: object
    bot: object
    direction: tuple
    speed: float
    last_tick: int
    attached_at: float | None = None
    target: int | None = None
    slow: object = None  # (target component, actual aura graph instance)


class Turrets:
    def __init__(self, combat):
        self.combat = combat
        self.sentries: list[Sentry] = []

    def fired(self, player, command, state) -> bool:
        if (getattr(player.hero, "guid", None) != HERO
                or state.instance.graph.index != LAUNCH_GRAPH or state.index != 12):
            return False
        from ow174.game import npc
        from ow174.game.combat import aim

        record = graph.entity_body(BODY)
        if record is None or graph.graph(BEAM_GRAPH) is None or graph.graph(SLOW_GRAPH) is None:
            raise ValueError("sentry deployment needs its external actor and aura graph definitions")
        health = next(row for row in record["graphs"] if row["graph"] == pools.BODY_GRAPH)
        values = pools._numbers(graph.graph(pools.BODY_GRAPH).fields["m_publicSchema"]["m_entries"])
        values.update(pools._numbers(health.get("m_1EB5A024")))
        definition = npc.Definition(HERO, BODY, "Sentry Turret", values[pools.HEALTH_VAR],
                                    values.get(pools.ARMOUR_VAR, 0), values.get(pools.SHIELDS_VAR, 0),
                                    MoverData(stand_height=1.0, stand_radius=.2,
                                              crouch_height=1.0, crouch_radius=.2,
                                              run_forward=0, run_backward=0, run_strafe=0))
        bot = npc.spawn(self.combat.match, definition, self.combat._eye(player), team=player.team)
        bot.spawner.update(manual_spawn=False, summoned=True)
        bot.combat_owner = player
        speed = expr.to_float(state.evaluate(state.node.field("m_projectileSpeed"), watch=False))
        sentry = Sentry(player, bot, aim(*command.aim()), speed, self.combat.tick)
        self.sentries.append(sentry)
        # Reconstructed ownership limit includes in-flight sentries, so a missed
        # infinite-lifetime launch cannot leave an unbounded actor collection.
        maximum = expr.to_int(state.instance.find_var(graph.INSTANCE, 181).value())
        owned = [item for item in self.sentries if item.owner is player]
        for old in owned[:-max(1, maximum)]:
            self._retire(old)
        return True

    def _clear_slow(self, sentry):
        if sentry.slow is not None:
            component, instance = sentry.slow
            if component.instances.get(instance.id) is instance:
                component.destroy(instance)
                component.run_now()
            sentry.slow = None
        sentry.target = None

    def _retire(self, sentry):
        from ow174.game import npc

        self._clear_slow(sentry)
        npc.remove_summoned(self.combat.match, sentry.bot)
        self.sentries.remove(sentry)

    def retire_owner(self, owner):
        for sentry in tuple(self.sentries):
            if sentry.owner is owner:
                self._retire(sentry)

    def _target(self, sentry, origin, reach):
        from ow174.game.combat import segment_distance, visible_capsule

        best = None
        for target in self.combat._enemies(sentry.owner):
            a, b = target.segment()
            distance = max(0.0, segment_distance(origin, a, b) - target.radius)
            if (distance <= reach and (best is None or distance < best[0])
                    and visible_capsule(self.combat._collision(), origin, target)):
                best = distance, target
        return None if best is None else best[1]

    def update(self):
        from ow174.game.combat import COMBAT_CATCH_UP, TICK, _along

        for sentry in tuple(self.sentries):
            bot, owner = sentry.bot, sentry.owner
            if (bot not in self.combat.match.bots or bot.dead or owner.gone or owner.hero.guid != HERO):
                self._retire(sentry)
                continue
            elapsed = self.combat.tick - sentry.last_tick
            if elapsed <= 0:
                continue
            sentry.last_tick = self.combat.tick
            start = self.combat.now - elapsed * TICK
            if sentry.attached_at is None:
                distance = sentry.speed * elapsed * TICK
                hit = self.combat._collision().cast_ray(bot.position, sentry.direction, distance)
                if hit is None:
                    bot.position = _along(bot.position, sentry.direction, distance)
                else:
                    # Small normal offset avoids casting future beams from inside the wall.
                    dot = sum(a * b for a, b in zip(hit.normal, sentry.direction, strict=True))
                    facing = -1 if dot > 0 else 1
                    bot.position = tuple(p + facing * n * .01
                                         for p, n in zip(hit.point, hit.normal, strict=True))
                    sentry.attached_at = start + hit.distance / sentry.speed
                    bot.body_script.component.set(478, True)  # reconstructed deployed flag
                bot.velocity = (0, 0, 0) if hit else tuple(v * sentry.speed for v in sentry.direction)
                bot.moved_tick = self.combat.tick
            script = bot.body_script
            if script is None:
                raise ValueError("deployed sentry lost its authored body script")
            snapshot = script.advance_live(self.combat.tick, bot.position)
            if snapshot is not None:
                for viewer in self.combat.match.players:
                    if viewer.client is not None and viewer.spawned:
                        viewer.client.queue_bot_live(bot.entity, snapshot)
            if sentry.attached_at is None:
                continue
            attach = next(i for i in script.component.instances.values() if i.graph.index == ATTACH_GRAPH)
            charge = attach.state(4)
            fixed = charge.node.field("m_5BEFF040")
            if fixed is not None:
                delay = expr.to_float(charge.evaluate(fixed, watch=False))
            else:
                rate_cfg = charge.node.field("m_9B0385B4")
                rate = expr.to_float(charge.evaluate(rate_cfg, watch=False)) if rate_cfg is not None else 1.0
                destination = expr.to_float(charge.evaluate(charge.node.field("m_destination"), watch=False))
                delay = destination / rate if rate > 0 else math.inf
            ready = sentry.attached_at + delay
            if self.combat.now <= ready:
                continue
            beam = next(i for i in script.component.instances.values() if i.graph.index == BEAM_GRAPH)
            reach = expr.to_float(beam.find_var(graph.INSTANCE, 1648).value())
            origin = bot.position
            target = self._target(sentry, origin, reach)
            if target is None:
                self._clear_slow(sentry)
                continue
            target_script = getattr(target.owner, "body_script", None)
            if sentry.target != target.entity:
                self._clear_slow(sentry)
                sentry.target = target.entity
                if target_script is not None:
                    component = target_script.component
                    aura = component.create(graph.graph(SLOW_GRAPH))
                    percent_cfg = beam.graph.state(9).field("m_params")[0]["m_value"]
                    percent = beam.evaluate(percent_cfg)
                    if runtime.send_instance_message(aura, 0x024000000000000F,
                                                     {0x002A: percent}, bot.entity) != 1:
                        component.destroy(aura)
                        raise ValueError("sentry slow aura has no authored activation entry")
                    component.run_now()
                    sentry.slow = component, aura
            # Only retained combat time has usable target poses after a scheduler gap.
            duration = max(0.0, self.combat.now - max(
                start, ready, self.combat.now - COMBAT_CATCH_UP * TICK))
            modify = beam.graph.state(1).field("m_modifyHealth")
            amount = expr.to_float(beam.evaluate(modify["m_amount"])) * duration
            loadout = beam.evaluate(modify["m_loadout"])
            self.combat._damage(owner, target, amount, False,
                                loadout.guid if isinstance(loadout, expr.Asset) else None,
                                self.combat.tick, world_tick=self.combat.tick)
            if self.combat.vitals_of(target).dead:
                self._clear_slow(sentry)
