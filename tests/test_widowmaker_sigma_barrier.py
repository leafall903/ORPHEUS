"""Actual Sigma barrier casts over the verified C015 mesh and Widow shot inputs."""

import os
import unittest

from test_widowmaker_combat import FIRE, SCOPE, _WidowmakerMatch

from ow174.game import combat, props, sigma_barrier
from ow174.game.content import heroes


@unittest.skipUnless(os.environ.get('ORPHEUS_SIGMA_BARRIER_MODEL'), 'requires authored shield model')
class WidowmakerSigmaBarrierTests(_WidowmakerMatch, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.assertTrue(self.run.match.switch_hero(self.b, heroes()[sigma_barrier.HERO]))
        self.b.mover.position[:] = (0, 0, 9)
        self.b.mover.body = None
        self.fight = self.run.match.combat
        self.barriers = self.fight.sigma_barriers

    def deploy(self):
        for n in range(50):
            self.run.step({self.b: (-32768, 0, 2 if n < 13 else 0)})
        actor, = self.barriers.actors.values()
        self.assertTrue(actor.active)
        self.assertEqual(actor.phase, 3)
        self.assertEqual(actor.health, 700)
        self.assertTrue(actor.child_request_seen)
        return actor

    def test_actual_scoped_shot_hits_verified_dynamic_shape_without_body_damage(self):
        self.assertEqual(props.model_shapes(self.barriers.geometry, 0xFFFF), [])
        actor = self.deploy()
        before = self.health(self.b)
        self.press(SCOPE, 100)
        self.press(SCOPE | FIRE)
        self.assertEqual(actor.health, 580)
        self.assertEqual(self.health(self.b), before)
        self.assertEqual(self.a.client.messages(combat.HIT), [])

    def test_source_body_replacement_retires_actor_and_binding(self):
        actor = self.deploy()
        self.assertIn(actor.entity, self.barriers.actors)
        self.assertTrue(self.run.match.switch_hero(self.b, heroes()[sigma_barrier.HERO]))
        self.assertNotIn(actor.entity, self.barriers.actors)

    def test_actual_recall_exposes_body_and_recharges_with_owner_input_paused(self):
        actor = self.deploy()
        self.press(FIRE)
        self.assertEqual(actor.health, 687)
        self.run.step({self.b: (-32768, 0, 2)})
        self.run.step({})
        self.assertFalse(actor.active)
        self.assertAlmostEqual(actor.recharge_at - self.fight.now, 2)
        recall = actor.instance.states[111]
        self.assertGreater(recall.remaining(), 1.9)
        owner_frame = self.b.body_script.frame
        self.run.frame += 110
        self.run.match.update(self.run.frame * combat.TICK, self.run.frame)
        self.assertEqual(actor.health, 687)
        self.run.frame += 30
        self.run.match.update(self.run.frame * combat.TICK, self.run.frame)
        self.assertEqual(actor.health, 700)
        self.assertEqual(self.b.body_script.frame, owner_frame)
        before = self.health(self.b)
        self.press(FIRE)
        self.assertLess(self.health(self.b), before)
