"""Actual Widowmaker fire against Tracer's authored Recall entry and exit."""

import unittest

from test_widowmaker_combat import FIRE, SCOPE, _WidowmakerMatch

from ow174.game import combat, server_snapshot, tracer_recall
from ow174.game.commands import Command
from ow174.game.content import heroes


class WidowmakerRecallTests(_WidowmakerMatch, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.match = self.run.match
        self.fight = self.match.combat
        self.assertTrue(self.match.switch_hero(self.b, heroes()[tracer_recall.HERO]))
        self.b.mover.position[:] = (0, 0, 5)
        self.b.mover.body = None
        self.settle(200)

    def settle(self, count):
        for _ in range(count):
            self.run.step({})

    def recall(self):
        self.run.step({self.b: (0, 0, 16)})
        cast = self.fight.recalls.active.get(self.b.body)
        self.assertIsNotNone(cast)
        self.assertTrue(self.fight.recalls.protected(self.b))
        return cast

    def test_actual_rifle_damage_is_restored_and_hits_resume_after_recall(self):
        for _ in range(28):
            self.run.frame += 1
            tick = self.run.frame
            self.match.update(tick * combat.TICK, tick)
            self.a.take_commands([Command(tick)])
            self.b.take_commands([Command(tick, forward=127)])
        displaced = tuple(self.b.mover.position)
        self.assertGreater(displaced[2], 6)
        weapon = next(i for i in self.b.body_script.component.instances.values() if i.graph.index == 0x16)
        self.press(FIRE, frames=8, attacker=self.b, defender=self.a)
        self.assertLess(weapon.vars[53].value(), weapon.vars[198].value())
        self.assertLess(self.health(self.a), 175)
        self.press(FIRE)
        self.assertEqual(self.health(self.b), 137)
        cast = self.recall()
        self.press(FIRE)
        self.assertEqual(self.health(self.b), 137)
        for _ in range(45):
            if cast.playback:
                break
            self.settle(1)
        self.assertTrue(cast.playback)
        widow_before = self.health(self.a)
        ammo_before = weapon.vars[53].value()
        self.press(FIRE, frames=4, attacker=self.b, defender=self.a)
        self.assertEqual(self.health(self.a), widow_before)
        self.assertEqual(weapon.vars[53].value(), ammo_before)
        self.press(FIRE, frames=8)
        self.assertEqual(self.health(self.b), 137)
        self.assertEqual(len(self.a.client.messages(combat.HIT)), 1)
        self.settle(70)
        self.assertTrue(cast.finished)
        self.assertFalse(self.fight.recalls.protected(self.b))
        self.assertEqual(self.health(self.b), 150)
        self.assertAlmostEqual(self.b.mover.position[2], 5, places=2)
        self.assertEqual(weapon.vars[53].value(), weapon.vars[198].value())
        self.press(FIRE)
        self.assertEqual(self.health(self.b), 137)
        instance = self.fight.recalls.instance(self.b)
        self.assertGreater(instance.vars[61].value(), 10)
        self.run.step({self.b: (0, 0, 16)})
        self.assertIs(self.fight.recalls.active[self.b.body], cast)

    def test_scoped_lethal_hit_cannot_apply_during_recall(self):
        self.press(SCOPE, frames=100, height=combat.EYE)
        cast = self.recall()
        self.press(SCOPE | FIRE, height=combat.EYE)
        self.assertEqual(self.health(self.b), 150)
        self.assertEqual(self.a.client.messages(combat.HIT), [])
        self.settle(90)
        self.assertTrue(cast.finished)
        self.press(SCOPE, frames=100, height=combat.EYE)
        self.press(SCOPE | FIRE, height=combat.EYE)
        self.assertEqual(self.health(self.b), 0)
        self.assertNotIn(self.b.body, self.fight.recalls.active)

    def test_world_clock_restores_once_without_tracer_poses(self):
        self.press(FIRE)
        cast = self.recall()
        owner_frame = self.b.body_script.frame
        for _ in range(90):
            self.run.frame += 1
            tick = self.run.frame
            self.match.update(1_700_000_000 + tick * combat.TICK, tick)
            self.a.take_commands([Command(tick)])
        self.assertEqual(self.b.body_script.frame, owner_frame)
        self.assertTrue(cast.finished)
        self.assertEqual(self.health(self.b), 150)
        self.press(FIRE)
        self.assertEqual(self.health(self.b), 137)
        self.settle(75)
        self.assertEqual(self.health(self.b), 137)

    def test_history_and_live_cast_are_stored_and_retired_with_body(self):
        cast = self.recall()
        graph = server_snapshot._Graph({'combat': self.fight}, server_snapshot.WorldRegistry())
        graph.value(self.fight.recalls)
        graph.finish()
        body = self.b.body
        self.assertTrue(self.match.switch_hero(self.b, self.a.hero))
        self.settle(90)
        self.assertNotIn(body, self.fight.recalls.active)
        self.assertNotIn(body, self.fight.recalls.history)
        self.assertFalse(cast.finished)
