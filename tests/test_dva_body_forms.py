"""Actual prepared0407 selection through Match, distinct from Q possession."""

import os
import unittest
from dataclasses import replace
from unittest.mock import patch

import test_dva_factory as factory

from ow174.game import combat, dva, pools
from ow174.game.commands import FIRE
from ow174.game.content import heroes
from ow174.game.script import expr, graph
from ow174.game.script.driver import HUD_HEALTH, BodyScript


@unittest.skipUnless(os.environ.get('ORPHEUS_DVA_RUNTIME'), 'requires actual0407 metadata/mover')
class DvaBodyFormTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        factory.DvaFactoryTests.setUpClass()

    def arena(self):
        _, run, pilot, zen = factory.DvaFactoryTests().arena(cast=False)
        previous = pilot.body_script
        self.assertTrue(run.match.switch_hero(pilot, replace(pilot.hero, body=dva.MECH)))
        self.assertTrue(previous.retired)
        return run, pilot, zen

    def test_actual_mech_selection_uses_own_graphs_pools_and_mover(self):
        run, mech, _ = self.arena()
        script = mech.body_script
        self.assertIs(script.record, graph.entity_body(dva.MECH))
        indexes = {i.graph.index for i in script.component.instances.values()}
        self.assertTrue({0x276, 0x2CC, 0x50A} <= indexes)
        self.assertNotIn(0x26A, indexes)
        self.assertEqual(pools.active_pools(dva.HERO, dva.MECH), (300, 300, 0))
        self.assertEqual(script.component.entity_var(HUD_HEALTH).value(), 600)
        self.assertEqual(mech.mover.data.stand_radius, .7)
        target = next(t for t in run.match.combat._targets() if t.owner is mech)
        self.assertEqual(run.match.combat.vitals_of(target).total, 600)

    def test_direct_owner_query_is_actual_controller_and_not_current_body(self):
        run, mech, _ = self.arena()
        instance = next(i for i in mech.body_script.component.instances.values() if i.graph.index == 0x2CC)
        owner = instance.evaluate({'$': 'STU_F7262B36', 'm_801B298F': {'$': 'STU_B5A0CAF0'}})
        self.assertEqual(owner, expr.Entity(mech.entity))
        self.assertNotEqual(owner, expr.Entity(mech.body))
        registry = mech.body_script.world.dva_queries
        self.assertEqual(registry.lookup(mech.entity).possession.current, mech.body)
        self.assertIsNone(registry.lookup_owner(0xDEADBEEF))
        run.match.stop_body_script(mech)
        self.assertTrue(registry.owner_context.retired)

    def test_actual_ruleset_initializes_matrix_capacity_and_held_command_drains_it(self):
        run, mech, _ = self.arena()
        matrix = next(i for i in mech.body_script.component.instances.values() if i.graph.index == 0x2CC)
        self.assertEqual(matrix.vars[87].value(), 100)
        self.assertEqual(matrix.vars[971].value(), 100)
        for _ in range(100):
            run.step({})  # let the real0407 initial body scopes settle before accepting ability input
        for _ in range(60):
            run.step({mech: (0, 0, 2)})
        self.assertTrue(matrix.state(3).active)
        self.assertAlmostEqual(matrix.vars[87].value(), 52, delta=.1)
        run.step({})
        self.assertFalse(matrix.state(3).active)

    def test_actual_zen_orb_damages_selected_mech_armour_and_updates_received_health(self):
        run, mech, zen = self.arena()
        floor = mech.mover.position[1]
        mech.mover.position[:] = (0, floor, 8)
        zen.mover.position[:] = (0, floor, 0)
        for _ in range(3):
            run.step({})
        angles = factory.aim(run.match.combat._eye(zen), (0, floor+1, 8))
        run.step({zen: (*angles, FIRE)})
        for _ in range(20):
            run.step({})
        target = next(t for t in run.match.combat._targets() if t.owner is mech)
        vitals = run.match.combat.vitals_of(target)
        self.assertEqual(vitals.total, 557)
        self.assertEqual(mech.body_script.component.entity_var(HUD_HEALTH).value(), 557)
        self.assertEqual(zen.client.messages(combat.HIT)[0]['+0x78']['+0x4'], 43)

    def test_selecting_pilot_again_restores_its_definition_and_resources(self):
        run, mech, _ = self.arena()
        previous = mech.body_script
        self.assertTrue(run.match.switch_hero(mech, heroes()[dva.HERO]))
        self.assertTrue(previous.retired)
        self.assertEqual(mech.hero.body, dva.PILOT)
        self.assertEqual(mech.body_script.component.entity_var(HUD_HEALTH).value(), 150)
        self.assertNotEqual(mech.mover.data.stand_radius, .7)

    def test_missing_alternate_record_preserves_old_body_script_pose_and_health(self):
        _, run, pilot, _ = factory.DvaFactoryTests().arena(cast=False)
        before = (pilot.body, pilot.hero, pilot.mover, pilot.body_script)
        with patch.object(graph, 'entity_body', return_value=None):
            self.assertFalse(run.match.switch_hero(pilot, replace(pilot.hero, body=dva.MECH)))
            with self.assertRaises(ValueError):
                BodyScript(dva.HERO, pilot.body, 1000, body_definition=dva.MECH)
        self.assertEqual((pilot.body, pilot.hero, pilot.mover, pilot.body_script), before)
        self.assertFalse(pilot.body_script.retired)

    def test_foreign_hero_body_is_rejected_before_retiring_current_script(self):
        _, run, pilot, zen = factory.DvaFactoryTests().arena(cast=False)
        before = pilot.body_script
        self.assertFalse(run.match.switch_hero(pilot, replace(pilot.hero, body=zen.hero.body)))
        self.assertIs(pilot.body_script, before)
        with self.assertRaises(ValueError):
            pools.active_pools(dva.HERO, zen.hero.body)
