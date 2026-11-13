"""Original Hack rejection must precede channel/application and cooldown."""

import unittest

import test_game_sombra_combat as scenes

from ow174.game.script import filter_condition, nodes


class HackRejectionTests(unittest.TestCase):
    def test_actual_recipient_rejection_prevents_acquisition_and_paid_application(self):
        scene = scenes.SombraCombatTests()
        scene.setUp()
        weapon = scene.weapon()
        recipient = scene.b.body_script.component
        condition = weapon.graph.node(204).field("m_condition")["m_filter"]["m_condition"]

        recipient.set(14679, True)
        self.assertFalse(filter_condition.evaluate(weapon, condition, scene.b.body))
        scene.press(2, 100)
        self.assertFalse(weapon.vars[375].value())
        self.assertEqual(scene.hack_applications(), [])
        self.assertEqual(weapon.state(53).remaining(), 0)

        # Release only the actual authored recipient flag; the same input and
        # target must still reach the real application and its paid cooldown.
        scene.press(0, 2)
        recipient.set(14679, False)
        self.assertTrue(filter_condition.evaluate(weapon, condition, scene.b.body))
        scene.press(2, 100)
        application, = scene.hack_applications()
        self.assertEqual(application.source_entity(), scene.a.body)
        self.assertGreater(weapon.state(53).remaining(), 0)

    def test_recipient_becoming_rejected_during_channel_cancels_without_cooldown(self):
        scene = scenes.SombraCombatTests()
        scene.setUp()
        scene.press(2, 20)
        weapon = scene.weapon()
        self.assertTrue(weapon.state(53).flags & nodes.Ability.RUNNING)
        self.assertEqual(scene.hack_applications(), [])

        scene.b.body_script.component.set(14679, True)
        scene.press(2, 80)
        self.assertFalse(weapon.vars[375].value())
        self.assertEqual(scene.hack_applications(), [])
        self.assertEqual(weapon.state(53).remaining(), 0)
