"""Actual Sigma launch classes and causal normalized-spread state, not shot-angle sampling."""

import unittest
from unittest.mock import patch

import test_sigma_combat as fixtures

from ow174.game.body_vm_snapshot import export_component_vm, snapshot_digest
from ow174.game.commands import FIRE


class SigmaVolleySpreadTests(unittest.TestCase):
    def cast_rock(self):
        run, player, _enemy, _health = fixtures.SigmaCombatTests().arena(offset=0, distance=6)
        run.step({player: (0, 0, 0x10)})
        for _ in range(100):
            run.step({})
        instance = next(i for i in player.body_script.component.instances.values() if i.graph.index == 0x1BBB)
        state = instance.node_state(instance.graph.node(9))
        self.assertGreater(state.counter, 0)
        return run, player, state

    def test_actual_accretion_no_decay_launch_bakes_zero_ramp_and_full_output(self):
        _run, _player, state = self.cast_rock()
        self.assertEqual(state.node.field("m_5EC1EB38")["$"], "STU_239B571E")
        self.assertEqual(state.spread_ramp, 0)
        self.assertEqual(state.spread_at(0), 1)
        self.assertEqual(state.spread_at(1000), 1)

    def test_actual_primary_launch_class_preserves_existing_output(self):
        run, player, _enemy, _health = fixtures.SigmaCombatTests().arena()
        run.step({player: (0, 0, FIRE)})
        for _ in range(60):
            run.step({})
        instance = next(i for i in player.body_script.component.instances.values() if i.graph.index == 0x187F)
        for node in (45, 56):
            state = instance.node_state(instance.graph.node(node))
            self.assertGreater(state.counter, 0)
            self.assertEqual(state.node.field("m_5EC1EB38")["$"], "STU_613BDDB0")
            self.assertIsNone(state.spread_ramp)
            self.assertEqual(state.spread_at(1000), state.spread0)

    def test_actual_accretion_baked_ramp_changes_read_only_vm_snapshot(self):
        _run, player, state = self.cast_rock()
        component = player.body_script.component
        before = snapshot_digest(export_component_vm(component))
        with patch.object(state, "spread_ramp", 6.0):
            after = snapshot_digest(export_component_vm(component))
        self.assertNotEqual(before, after)
        self.assertEqual(before, snapshot_digest(export_component_vm(component)))


if __name__ == "__main__":
    unittest.main()
