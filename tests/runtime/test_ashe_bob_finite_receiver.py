"""Actual Q phases use one existing mover for canonical finite effects and force."""
import json
import math
import unittest
from pathlib import Path
from unittest.mock import patch

import test_ashe_bob_charge as bob_fixture

from ow174.game import combat, defenses
from ow174.game.mover import MULTIPLY
from ow174.game.script import expr

CONTRACT_PATH = Path(__file__).parents[2] / 'fixtures/behavior/ashe-bob-finite-receiver-v1.json'
CONTRACT = json.loads(CONTRACT_PATH.read_text())


class AsheBobFiniteReceiverTests(unittest.TestCase):
    def test_actual_charge_gun_and_retire_share_finite_and_force_mover(self):
        self.evidence = []
        for phase in CONTRACT['input']['phases']:
            with self.subTest(phase=phase):
                fixture = bob_fixture.BobChargeTests()
                run, owner, enemy = fixture.setup(distance=150)
                actor = fixture.begin(run, owner)
                deadline = actor.born + 10 if phase == 'charge' else actor.ready
                while run.frame < deadline:
                    run.step({})
                if phase == 'retire':
                    while run.frame < actor.until:
                        run.step({})
                self.assertEqual(actor.phase, phase)
                bot, mover, fight = actor.bot, actor.mover, run.match.combat
                self.assertIs(bot.impulse_mover, mover)
                self.assertIsNone(bot.navigation)
                self.assertFalse(bot.route)
                self.assertEqual(defenses.knockback_scale(bot), 1.)
                target = next(t for t in fight._targets() if t.owner is bot)
                serial = fight.knockbacks.serial
                admitted = fight.knockbacks.admit_modifiers(
                    target, impact_tick=run.frame, source_entity=enemy.body,
                    launch_modifiers=((4, MULTIPLY, .01, .32, 0.), (7, MULTIPLY, 0., .32, 1.)))
                self.assertTrue(admitted)
                self.assertEqual(fight.knockbacks.serial, serial)
                self.assertFalse(mover.velocity_changes)
                self.assertIs(bot.impulse_mover, mover)
                self.assertTrue(fight.knockbacks.apply(target, expr.Vec3(6., 2., 0.),
                    impact_tick=run.frame, source_entity=enemy.body, velocity_mode=4))
                start, initial_x = run.frame, bot.position[0]
                ticks = []
                pose = mover.step_pose

                def observe(command, dt, tick, *, ticks=ticks, pose=pose, **kwargs):
                    ticks.append(tick)
                    return pose(command, dt, tick, **kwargs)

                with patch.object(mover, 'step_pose', side_effect=observe):
                    for _ in range(21):
                        run.step({})
                        self.assertIs(bot.impulse_mover, actor.mover)
                        self.assertEqual(bot.position, tuple(mover.position))
                        self.assertEqual(bot.velocity, mover.velocity)
                        scale = .01 if run.frame < start + 21 else 1.
                        self.assertAlmostEqual(mover.values.decel_ground_base,
                                               mover.data.decel_ground_base * scale, delta=1e-6)
                self.assertEqual(ticks, list(range(start + 1, start + 22)))
                self.assertGreater(bot.position[0], initial_x)
                self.assertFalse(mover.velocity_changes)
                self.assertFalse(any(key[0] == -3 for key in mover.mods))
                self.assertIsNone(bot.body_script.component.fault)
                if phase == 'retire':
                    while run.frame < actor.until + math.ceil(.5 / combat.TICK):
                        run.step({})
                    self.assertNotIn(bot, run.match.bots)
                    self.assertNotIn(bot.entity, fight.knockbacks.active)
                self.evidence.append({'phase': phase, 'admitted': admitted,
                                      'same_mover': True, 'pose_count': len(ticks),
                                      'first_tick': ticks[0], 'last_tick': ticks[-1],
                                      'finite_expired': True, 'x_displacement': bot.position[0] - initial_x})
