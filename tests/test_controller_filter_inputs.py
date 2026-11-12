"""Explicit controller input projection, atomic changes and unsupported services."""

import unittest
from unittest.mock import patch

from test_game_graph_analysis import make_graph

from ow174.game.script import filter_game, runtime
from ow174.game.script.controller_filter import ControllerFilterInputs

MASKS = ((1 << 25) | (1 << 26) | (1 << 47), 1 << (97 - 64))
FILTER = {"$": "STUConfigVarFilter", "m_84065FD1": 0x40000, "m_4EA686DB": 0x130}


def inputs(**changes):
    values = {"owner": 102, "component_masks": MASKS, "current_bits": 0x40021,
              "replicated_bits": 0x40001, "provenance": "explicit test input, not automatic attachment"}
    values.update(changes)
    return ControllerFilterInputs(**values)


class ControllerFilterInputTests(unittest.TestCase):
    def test_projection_prefers_replication_and_missing_reached_payload_rejects(self):
        profile = inputs()
        self.assertEqual(profile.bits(), 0x40001)
        profile.update(replicated_bits=None)
        with self.assertRaisesRegex(NotImplementedError, "component26"):
            profile.bits()
        profile.update(component_masks=(MASKS[0] & ~(1 << 26), MASKS[1]))
        self.assertEqual(profile.bits(), 0x40021)
        profile.update(component_masks=(0, 0))
        self.assertIsNone(profile.bits())

    def test_update_is_atomic_and_copies_input_mask_storage(self):
        words = list(MASKS)
        profile = inputs(component_masks=words)
        words[0] = 0
        self.assertEqual(profile.component_masks, MASKS)
        for changes in ({"component_masks": ()}, {"current_bits": True}, {"owner": 103},
                        {"component_masks": (0, 0)}, {"provenance": ""}, {"flags28": True}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                profile.update(**changes)
            self.assertEqual((profile.component_masks, profile.revision, profile.bits()), (MASKS, 0, 0x40001))
        profile.update(current_bits=0x40000)
        self.assertEqual(profile.revision, 1)

    def test_status_precedence_uses_real_inputs_and_unknown_present_payloads_reject(self):
        profile = inputs()
        profile.update(component_masks=(MASKS[0] | (1 << 28), MASKS[1]))
        with self.assertRaisesRegex(NotImplementedError, "component28"):
            profile.bits()
        profile.update(flags28=0x2000)
        self.assertEqual(profile.bits(), 0x40011)
        profile.update(component_masks=(MASKS[0] | (1 << 28) | (1 << 57), MASKS[1]), primary_status=False)
        self.assertEqual(profile.bits(), 0x40001)
        profile.update(primary_status=True)
        self.assertEqual(profile.bits(), 0x40011)
        profile.update(primary_status=None)
        with self.assertRaisesRegex(NotImplementedError, "component57"):
            profile.bits()

    def test_retired_input_cannot_project_or_update_or_admit_target(self):
        profile = inputs()
        profile.retire()
        self.assertEqual(profile.revision, 1)
        profile.retire()
        self.assertEqual(profile.revision, 1)
        with self.assertRaises(NotImplementedError):
            profile.bits()
        with self.assertRaises(ValueError):
            profile.update(current_bits=0)
        with self.assertRaises(NotImplementedError):
            filter_game.evaluate_target(None, FILTER, 102, inputs=profile)

    def test_source_independence_does_not_substitute_relationship_or_rule_answers(self):
        self.assertTrue(filter_game.source_independent(FILTER))
        for change in ({"m_27CF71D5": 1}, {"m_93FA1642": [{}]}, {"m_C1903771": {}},
                       {"m_F61726FE": {}}, {"m_9C7BD8B8": {}}, {"unknown": 1}):
            with self.subTest(change=change):
                config = dict(FILTER, **change)
                self.assertFalse(filter_game.source_independent(config))
                self.assertIs(filter_game.evaluate_target(None, config, 102, inputs=inputs()), NotImplemented)
        self.assertTrue(filter_game.evaluate_target(None, FILTER, 102, inputs=inputs()))
        self.assertFalse(filter_game.evaluate_target(None, FILTER, 102,
                                                     inputs=inputs(replicated_bits=0x40021)))

    def test_changed_input_during_condition_evaluation_cannot_admit_old_generation(self):
        profile = inputs()
        component = runtime.Component(100, world=runtime.World(1, 1))
        instance = component.create(make_graph(1), init=False)

        def change(_cfg, _instance):
            profile.update(replicated_bits=0x40021)
            return True

        config = dict(FILTER, m_condition={"$": "explicit_generation_change_fixture"})
        with (patch.object(component.world, "native", side_effect=change),
              self.assertRaisesRegex(NotImplementedError, "changed during")):
            filter_game.evaluate_target(instance, config, 102, inputs=profile)
        self.assertEqual(component.parameter_scopes, [])


if __name__ == "__main__":
    unittest.main()
