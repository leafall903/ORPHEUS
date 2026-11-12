"""Actual Moira inputs bake the native normalized launch-spread state.

This does not implement angular shot sampling or native muzzle animation.
"""

import unittest

import test_game_moira as fixtures
import test_game_moira_orbs as orb_fixtures

from ow174.game.body_vm_snapshot import export_component_vm, snapshot_digest


class MoiraVolleySpreadTests(unittest.TestCase):
    setup_fight = fixtures.MoiraSprayTests.setup_fight
    launch = orb_fixtures.MoiraOrbTests.launch

    def test_actual_spray_and_both_orbs_bake_zero_ramp_and_serialize_it(self):
        for kind in ('spray', 'healing-orb', 'damage-orb'):
            with self.subTest(kind=kind):
                run, source, _, _ = self.setup_fight(ally=kind != 'damage-orb')
                if kind == 'spray':
                    for _ in range(20):
                        run.step({source: (0, 0, 1)})
                    graph_index, state_index = 0xF76, 6
                else:
                    self.launch(run, source, healing=kind == 'healing-orb')
                    graph_index, state_index = 0x10E4, 10 if kind == 'healing-orb' else 11
                component = source.body_script.component
                instance = next(item for item in component.instances.values()
                                if item.graph.index == graph_index)
                state = instance.states[state_index]
                launch = state.node.field('m_5EC1EB38')
                self.assertEqual(launch['$'], 'STU_239B571E')
                self.assertIsNone(launch['m_AC20B1C1'])
                self.assertIsNone(launch['m_23C72C36'])
                self.assertEqual(state.spread_ramp, 0)
                self.assertEqual(state.output(2), 1)
                before = snapshot_digest(export_component_vm(component))
                state.spread_ramp = 6.0
                self.assertNotEqual(before, snapshot_digest(export_component_vm(component)))
                state.spread_ramp = 0.0
                self.assertEqual(before, snapshot_digest(export_component_vm(component)))
