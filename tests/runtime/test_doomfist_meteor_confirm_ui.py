"""Meteor's actual confirm restores the original countdown writer before exit."""

import json
import unittest
from pathlib import Path
from unittest.mock import patch

from test_doomfist_meteor import MeteorFixture, instance_for

from ow174.game import combat
from ow174.game.commands import Command


class MeteorConfirmUITests(MeteorFixture, unittest.TestCase):
    def test_actual_confirm_zeroes_countdown_without_extra_key_or_ammo(self):
        spec = json.loads((Path(__file__).resolve().parents[2] /
                           "fixtures/behavior/doomfist-meteor-confirm-ui-v1.json").read_text())
        self.ready(spec["input"]["distance"])
        self.run.step({self.a: (0, 0, 32)})
        self.settle(70)
        instance = instance_for(self.a, 0xC51)
        weapon = instance_for(self.a, 0xD0A)
        ammo = weapon.vars[53].value()
        self.assertGreater(instance.vars[215].value(), 0)
        self.assertTrue(instance.state(18).active)
        seen = []
        original = instance.run_node

        def observe(node, caller):
            if node.pos == 22:
                seen.append((instance.state(18).active, instance.vars[215].value()))
            return original(node, caller)

        with patch.object(instance, "run_node", side_effect=observe):
            self.run.step({self.a: (0, 0, 1)})
            self.a.take_commands([Command(self.run.frame, buttons=1)])
            self.settle(150)
        self.assertEqual(len(seen), 1)
        self.assertTrue(seen[0][0])
        self.assertEqual(instance.vars[215].value(), spec["expected"]["countdownValue"])
        self.assertFalse(instance.state(18).active)
        self.assertFalse(instance.state(90).active)
        self.assertFalse(instance.state(24).active)
        for index in spec["expected"]["persistentPresenterStates"]:
            self.assertTrue(instance.state(index).active)
        snapshot = self.a.body_script.snapshot()
        self.assertEqual(snapshot.vars[(instance.id, 215)][0], 0)
        self.assertEqual(weapon.vars[53].value(), ammo)
        self.assertEqual(len(self.a.client.messages(combat.HIT)), 1)
        self.assertTrue(self.fight.meteors.casts[self.a.body].impacted)
        self.assertTrue(self.fight.meteors.casts[self.a.body].finished)
