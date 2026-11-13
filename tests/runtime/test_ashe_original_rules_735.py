"""Real Ashe command producers retain resistance/immunity on canonical735."""
import json
import unittest
from pathlib import Path
from unittest.mock import patch

from test_ashe_knockback_r2 import AsheKnockbackR2Tests

from ow174.game import combat, immunity
from ow174.game.commands import Command
from ow174.game.content import heroes

CONTRACT_PATH = Path(__file__).parents[2] / 'fixtures/behavior/ashe-original-rules-735-v1.json'
CONTRACT = json.loads(CONTRACT_PATH.read_text())


class AsheOriginalRules735Tests(unittest.TestCase):
    def scene(self, hero):
        stepper, run, owner, enemy, npcs = AsheKnockbackR2Tests().scene()
        position = list(enemy.mover.position)
        run.match.switch_hero(enemy, heroes()[hero])
        enemy.mover.position = position
        enemy.mover.body = None
        for _ in range(30):
            stepper.step(run, 4000, 9000)
        return stepper, run, owner, enemy, npcs

    def test_real_coach_resistance_scales_once_and_keeps_damage(self):
        stepper, run, _owner, enemy, npcs = self.scene(0x02E0000000000007)
        fight = run.match.combat
        before = fight.vitals[enemy.body].total
        with (patch.object(fight.impulses, 'queue', wraps=fight.impulses.queue) as raw,
              patch.object(fight.knockbacks, 'apply', wraps=fight.knockbacks.apply) as accepted):
            stepper.step(run, 4000, 9000, 8)
            for _ in range(25):
                stepper.step(run, 4000, 9000)
        requested, = (c for c in raw.call_args_list if c.args[0] is enemy)
        applied, = (c for c in accepted.call_args_list if c.args[0].owner is enemy)
        for actual, axis in zip(applied.args[1], requested.args[1], strict=True):
            self.assertAlmostEqual(actual, axis * CONTRACT['expected']['reinhardt_scale'], places=5)
        self.assertEqual(before - fight.vitals[enemy.body].total,
                         CONTRACT['expected']['armoured_coach_damage'])
        self.assertGreater(enemy.mover.position[2], 1002)
        self.assertEqual(tuple(bot.entity for bot in run.match.bots), npcs)

    def test_real_coach_and_bob_fortify_block_only_victim_launch(self):
        for ability in ('coach', 'bob'):
            with self.subTest(ability=ability):
                stepper, run, owner, enemy, npcs = self.scene(immunity.ORISA)
                fight = run.match.combat
                if ability == 'bob':
                    enemy.mover.position[2] = 1008
                    enemy.mover.body = None
                    owner.body_script.set_ult_charge(2240)
                run.frame += 1
                run.match.update(run.frame * combat.TICK, run.frame)
                owner.take_commands([Command(run.frame + 4000)])
                enemy.take_commands([Command(9000 + run.frame - 1000, buttons=8)])
                self.assertTrue(immunity.fortify_active(enemy, enemy.body))
                before = fight.vitals[enemy.body].total
                with patch.object(fight.knockbacks, 'apply', wraps=fight.knockbacks.apply) as applied:
                    stepper.step(run, 4000, 9000, 8 if ability == 'coach' else 32)
                    for _ in range(80 if ability == 'bob' else 25):
                        stepper.step(run, 4000, 9000)
                        if ability == 'bob':
                            actor, = fight.companions.active.values()
                            if actor.phase != 'charge':
                                break
                self.assertLess(fight.vitals[enemy.body].total, before)
                self.assertEqual([c for c in applied.call_args_list if c.args[0].owner is enemy], [])
                if ability == 'coach':
                    self.assertEqual(len(applied.call_args_list), 1)
                    self.assertIs(applied.call_args.args[0].owner, owner)
                else:
                    self.assertEqual(actor.stopped_reason, 'enemy')
                    remaining = tuple(bot.entity for bot in run.match.bots if bot is not actor.bot)
                    self.assertEqual(remaining, npcs)
                instances = enemy.body_script.component.instances.values()
                self.assertFalse(any(i.graph.index == 0x17F6 for i in instances))
