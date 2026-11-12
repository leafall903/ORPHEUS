"""Original absorption activation cannot mint another grant after its lease ends."""
import unittest

import test_sigma_grasp as fixtures
from ow174.game import interception
from ow174.game.script import expr
from ow174.game.commands import FIRE


class SigmaGrantReplayTests(unittest.TestCase):
    def test_real_expired_grant_cannot_be_replayed_from_the_same_absorption(self):
        run, player, enemy, vitals, instance = fixtures.SigmaGraspTests().arena()
        for frame in range(160):
            run.step({enemy: (-32768, 0, FIRE if frame < 20 else 0)})
        self.assertEqual(vitals.total, 436)
        node = instance.graph.node(34)
        before = set(instance.component.instances)
        self.assertTrue(interception.play_shield_script(player.body_script.world, instance, node))
        self.assertEqual(set(instance.component.instances), before)
        self.assertEqual(vitals.total, 436)
        for _ in range(1000):
            run.step({})
        self.assertEqual(vitals.total, 400)
        self.assertFalse(any(i.graph.index == 0x1D03 for i in instance.component.instances.values()))
        self.assertTrue(interception.play_shield_script(player.body_script.world, instance, node))
        run.match.combat.absorption.update()
        self.assertEqual(vitals.total, 400)
        self.assertFalse(any(i.graph.index == 0x1D03 for i in instance.component.instances.values()))

    def test_zero_absorption_also_consumes_the_same_real_activation(self):
        run, player, _enemy, vitals, instance = fixtures.SigmaGraspTests().arena()
        for _ in range(160):
            run.step({})
        self.assertEqual(vitals.total, 400)
        activation = instance.id, instance.states[1].counter
        self.assertEqual(run.match.combat.absorption.converted[player.body], activation)
        instance.write((expr.INSTANCE, 21312), 60.0)
        self.assertTrue(interception.play_shield_script(player.body_script.world, instance, instance.graph.node(34)))
        run.match.combat.absorption.update()
        self.assertEqual(vitals.total, 400)
        self.assertFalse(any(i.graph.index == 0x1D03 for i in instance.component.instances.values()))
        old = player.body
        run.match.switch_hero(player, player.hero)
        self.assertNotIn(old, run.match.combat.absorption.converted)
