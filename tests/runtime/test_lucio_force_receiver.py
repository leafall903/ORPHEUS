"""Actual Lucio producers feed the single candidate force entry, not a native PASS."""

import math
import unittest
from typing import ClassVar
from unittest.mock import patch

from ow174.game import combat, defenses, lucio
from ow174.game.collision import flat_world
from ow174.game.commands import SECONDARY, Command
from ow174.game.content import heroes
from tests.runtime.test_game_combat import Run


class LucioForceReceiverTests(unittest.TestCase):
    evidence: ClassVar[list[dict]] = []

    def scene(self, victim_hero, *, source_birth=1000, victim_birth=1000):
        run = Run(names=("Lucio", "Victim", "Ally"))
        source, victim, ally = run.players
        original_npcs = tuple(bot.entity for bot in run.match.bots)
        self.assertEqual(len(original_npcs), 15)
        source.commands.last_frame = source_birth
        victim.commands.last_frame = victim_birth
        run.match.switch_hero(source, heroes()[lucio.HERO])
        run.match.switch_hero(victim, heroes()[victim_hero])
        source.team, victim.team, ally.team = 0, 1, 0
        run.match.collision_world = flat_world(0)
        for actor, point in ((source, (1000, 0, 1000)), (victim, (1000, 0, 1002)),
                             (ally, (1020, 0, 1004))):
            actor.mover.position[:] = point
            actor.mover.body = None
            actor.body_script.create_arrived = True
        return run, source, victim, ally, original_npcs

    def trigger(self, scene, button, source_birth, victim_birth):
        run, source, victim, ally, original_npcs = scene
        fight = run.match.combat
        target = next(t for t in fight._targets() if t.owner is victim)
        before = fight.vitals_of(target).total
        with patch.object(fight.knockbacks, "apply", wraps=fight.knockbacks.apply) as apply:
            for offset in range(1, 21):
                run.frame += 1
                run.match.update(run.frame * combat.TICK, run.frame)
                source.take_commands([Command(source_birth + offset,
                                              buttons=button if offset == 12 else 0)])
                victim.take_commands([Command(victim_birth + offset)])
                ally.take_commands([Command(run.frame)])
        self.assertEqual(apply.call_count, 1)
        self.assertEqual(fight.knockbacks.serial, 1)
        call = apply.call_args
        self.assertEqual(call.kwargs["source_entity"], source.body)
        self.assertEqual(call.kwargs["impact_tick"], 1012)
        self.assertNotIn("impact_frame", call.kwargs)
        notices = source.client.messages(combat.HIT)
        self.assertEqual(len(notices), 1)
        self.assertEqual(notices[0]["+0x84"], source_birth + 12)
        damage = before - fight.vitals_of(target).total
        self.assertGreater(damage, 0)
        self.assertEqual(round(damage), notices[0]["+0x78"]["+0x4"])
        self.assertIsNotNone(victim.impulse_correction)
        self.assertGreater(victim.mover.position[2], 1002)
        self.assertEqual(tuple(bot.entity for bot in run.match.bots), original_npcs)
        row = {"button": button, "source_birth": source_birth, "victim_birth": victim_birth,
               "impact_tick": call.kwargs["impact_tick"], "hit_owner_frame": notices[0]["+0x84"],
               "force_calls": apply.call_count, "serial": fight.knockbacks.serial, "damage": damage,
               "force": list(call.args[1]), "victim_position": list(victim.mover.position),
               "correction_requested": victim.impulse_correction is not None,
               "retained_npcs": len(original_npcs), "native_adoption": False}
        self.evidence.append(row)
        return tuple(call.args[1])

    def test_soundwave_uses_live_authored_resistance_once(self):
        scene = self.scene(0x02E0000000000007)
        scale = defenses.knockback_scale(scene[2])
        self.assertAlmostEqual(scale, .7, places=6)  # Actual00A4 movement modifier26.
        force = self.trigger(scene, SECONDARY, 1000, 1000)
        self.assertAlmostEqual(math.hypot(*force), 17 * scale, delta=2e-5)
        run, source, victim, ally, _ = scene
        for _ in range(80):
            run.frame += 1
            run.match.update(run.frame * combat.TICK, run.frame)
            for actor in (source, victim, ally):
                actor.take_commands([Command(run.frame)])
            if (not victim.mover.airborne and not victim.mover.external_impulse
                    and victim.body not in run.match.combat.knockbacks.active):
                break
        self.assertFalse(victim.mover.airborne)
        self.assertFalse(victim.mover.external_impulse)
        self.assertNotIn(victim.body, run.match.combat.knockbacks.active)
        landing_tick = run.frame
        before = victim.mover.position[2]
        run.frame += 1
        run.match.update(run.frame * combat.TICK, run.frame)
        victim.take_commands([Command(run.frame, forward=127)])
        self.assertGreater(victim.mover.position[2], before)
        self.assertGreater(victim.mover.throttles[1], 0)  # (right, forward)
        self.assertEqual(run.match.combat.knockbacks.serial, 1)
        self.evidence[-1].update({"landing_tick": landing_tick, "force_host_retired": True,
                                 "voluntary_forward_recovered": True})

    def test_actual_melee_enters_canonical_once_with_unequal_source_and_receiver_epochs(self):
        scene = self.scene(lucio.HERO, source_birth=9000, victim_birth=7000)
        force = self.trigger(scene, 0x800, 9000, 7000)
        self.assertAlmostEqual(math.hypot(force[0], force[2]), 1, places=6)
        self.assertEqual(force[1], 0)

    def test_actual_fortify_blocks_soundwave_force_without_erasing_damage(self):
        run, source, victim, ally, original_npcs = self.scene(0x02E000000000013E)
        fight = run.match.combat
        target = next(t for t in fight._targets() if t.owner is victim)
        before = fight.vitals_of(target).total
        with patch.object(fight.knockbacks, "apply", wraps=fight.knockbacks.apply) as apply:
            for offset in range(1, 21):
                run.frame += 1
                run.match.update(run.frame * combat.TICK, run.frame)
                victim.take_commands([Command(run.frame, buttons=8 if offset == 1 else 0)])
                source.take_commands([Command(run.frame, buttons=SECONDARY if offset == 12 else 0)])
                ally.take_commands([Command(run.frame)])
        self.assertTrue(fight.orisa._immune(victim))  # Actual active entity-variable9161.
        self.assertEqual(apply.call_count, 0)
        self.assertEqual(fight.knockbacks.serial, 0)
        self.assertLess(fight.vitals_of(target).total, before)
        self.assertEqual(len(source.client.messages(combat.HIT)), 1)
        self.assertEqual(tuple(victim.mover.position), (1000, 0, 1002))
        self.assertEqual(tuple(bot.entity for bot in run.match.bots), original_npcs)
        self.evidence.append({"case": "actual_fortify", "live_immunity_9161": True,
                              "force_calls": 0, "damage": before - fight.vitals_of(target).total,
                              "retained_npcs": 15, "native_adoption": False})
