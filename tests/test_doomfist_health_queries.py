"""Declared component51 owner-health bridge, actual damage and strict refusal."""

import unittest

from test_doomfist_combat import DoomfistFixture
from test_game_combat import aim
from test_script_runtime_budget import graph, node, plug

from ow174.game.script import expr, runtime
from ow174.game.script.bot import BotWorld
from ow174.game.script.driver import BodyWorld, owner_health_query

QUERY = {'$': 'STU_9B7648C3', 'm_entity': {'$': 'STU_B5A0CAF0'}}


class OwnerHealthTests(DoomfistFixture, unittest.TestCase):
    def owner_instance(self):
        return next(iter(self.a.body_script.component.instances.values()))

    def health(self):
        return self.owner_instance().evaluate(QUERY)

    def test_real_rifle_damage_updates_owner_health_query(self):
        self.assertEqual(self.health(), 250)
        yaw, pitch = aim(self.fight._eye(self.b), self.fight._eye(self.a))
        self.run.step({self.b: (yaw, pitch, 1)})
        # Eye-height rifle hit uses its existing2x critical multiplier.
        self.assertEqual(self.health(), 210)

    def test_real_uppercut_passive_and_decay_update_aggregate_query(self):
        self.prepare(1.5)
        self.assertEqual(self.health(), 250)
        self.run.step({self.a: (0, 0, 8)})
        self.assertEqual(self.health(), 280)
        self.settle(100)
        self.assertLess(self.health(), 280)
        self.assertGreater(self.health(), 250)

    def test_real_rifle_kill_returns_zero_before_respawn(self):
        for _ in range(200):
            yaw, pitch = aim(self.fight._eye(self.b), self.fight._eye(self.a))
            self.run.step({self.b: (yaw, pitch, 1)})
            if self.fight.is_dead(self.a):
                break
        self.assertTrue(self.fight.is_dead(self.a))
        self.assertEqual(self.health(), 0)

    def test_foreign_entity_is_not_silently_read_as_owner(self):
        instance = self.owner_instance()
        foreign = {**QUERY, 'm_entity': {'$': 'STUConfigVarDynamic',
                                       'm_identifier': 9001, 'm_60DB8F99': 0}}
        instance.write((0, 9001), expr.Entity(self.b.body))
        self.assertIs(owner_health_query(foreign, instance), NotImplemented)
        self.assertIsNone(instance.evaluate(foreign))


class StrictHealthQueryTests(unittest.TestCase):
    def test_reconstructed_health_service_cannot_complete_strict_mission(self):
        for world in (BodyWorld(0, 0, 0), BotWorld(0, 0, {})):
            with self.subTest(world=type(world).__name__):
                component = runtime.Component(123, world=world, strict=True)
                entry = node(0, 'STUStatescriptEntry', m_outPlug=plug((1, 'm_inPlug')))
                action = node(1, 'STUStatescriptActionSetVar',
                              m_out_Var={'$': 'STUConfigVarDynamic',
                                         'm_identifier': 99, 'm_60DB8F99': 0},
                              m_value=QUERY)
                instance = component.create(graph([entry, action], entries=[0]))
                with self.assertRaises(runtime.UnsupportedScriptError):
                    component.run_frame(0)
                self.assertNotIn(99, instance.vars)


if __name__ == '__main__':
    unittest.main()
