"""Native three-mode receiver caches and cross-instance dependency ownership."""

import unittest
from unittest import mock

from ow174.game.script import controller_heroes, expr, message_parameters, play, runtime
from tests.public.test_script_message_scope import KEY, fixture, row
from tests.public.test_script_message_state import literal


def setup(rows=(), outputs=(KEY,), *, shared=True):
    world, source, sender, target, receiver, _scope = fixture(rows, shared=shared)
    receiver.node.fields['m_params'] = [None if key is None else {'m_B5051BCE': f'0x{key:016X}'}
                                       for key in outputs]
    return world, source, sender, target, receiver


class MessageParameterTests(unittest.TestCase):
    def test_unmatched_single_sender_resets_or_preserves_the_stacked_placeholder(self):
        _, _, sender, _, receiver = setup()
        previous = (((0, 7),),)
        self.assertEqual(message_parameters.collect(receiver, (sender,), 0, previous=previous),
                         (((0, None),),))
        self.assertEqual(message_parameters.collect(receiver, (sender,), 0, previous=previous, stacked=True),
                         previous)
        self.assertEqual(message_parameters.collect(receiver, (), 0), (((0, None),),))
        self.assertEqual(message_parameters.collect(receiver, (), 1), ((),))
        self.assertEqual(message_parameters.collect(receiver, (), 2), ((),))

    def test_scalar_mode_maps_missing_failed_and_null_values_to_int_zero(self):
        for cfg in (None, literal(expr.EVALUATION_FAILED), literal(None)):
            _, _, sender, _, receiver = setup([row(value=cfg)])
            self.assertEqual(message_parameters.collect(receiver, (sender,), 0), (((0, 0),),))
        value = (3, None)
        _, _, sender, _, receiver = setup([row(value=literal(value))])
        self.assertEqual(message_parameters.collect(receiver, (sender,), 0), (((0, value),),))

    def test_iterator_mode_flattens_arrays_and_value_mode_retains_each_whole_value(self):
        value = (3, (4, None))
        _, _, sender, _, receiver = setup([row(value=literal(value))])
        self.assertEqual(message_parameters.collect(receiver, (sender, sender), 1),
                         (((0, 3), (0, 4), (0, 0), (0, 3), (0, 4), (0, 0)),))
        self.assertEqual(message_parameters.collect(receiver, (sender, sender), 2),
                         (((0, value), (0, value)),))
        _, _, sender, _, receiver = setup([row(value=literal(None))])
        self.assertEqual(message_parameters.collect(receiver, (sender,), 1), ((),))
        self.assertEqual(message_parameters.collect(receiver, (sender,), 2), (((0, None),),))

    def test_map_values_stay_scalar_in_the_native_generic_iterator(self):
        value = expr.Map(((expr.Asset(7), 3),))
        _, _, sender, _, receiver = setup([row(value=literal(value))])
        for mode in range(3):
            self.assertEqual(message_parameters.collect(receiver, (sender,), mode), (((0, value),),))

    def test_duplicate_output_keys_use_the_first_sender_row_and_key_zero_is_not_positional(self):
        _, _, sender, _, receiver = setup([None, row(value=None), row(value=literal(9)), row(0, literal(13))],
                                          outputs=(KEY, None, KEY, 0))
        self.assertEqual(message_parameters.collect(receiver, (sender,), 0),
                         (((0, 0),), ((0, None),), ((0, 0),), ((0, None),)))
        self.assertEqual(message_parameters.collect(receiver, (sender,), 1), ((), (), (), ()))
        self.assertEqual(message_parameters.collect(receiver, (sender,), 2), (((0, 0),), (), ((0, 0),), ()))

    def test_value_interface_watches_source_variables_from_the_receiving_state(self):
        cfg = {'$': 'STUConfigVarDynamic', 'm_identifier': 41, 'm_60DB8F99': 0}
        _, source, sender, target, receiver = setup([row(value=cfg)], shared=False)
        source.write((0, 41), (7, 8))
        target.write((0, 41), 99)
        self.assertEqual(message_parameters.collect(receiver, (sender,), 2), (((0, (7, 8)),),))
        self.assertIn(receiver, source.find_var(0, 41).watchers)
        self.assertNotIn(sender, source.find_var(0, 41).watchers)

    def test_controller_hero_value_collection_keeps_the_receiver_watcher(self):
        cfg = {'$': controller_heroes.CLASS, 'm_controller': {'$': 'STU_B5A0CAF0'}}
        world, source, sender, _, receiver = setup([row(value=cfg)])
        watched = []
        world.set_controller_hero_context(source.component.entity, controller_heroes.Context(
            source.component.entity, True, True,
            lookup=lambda identifier: controller_heroes.Controller(identifier),
            catalog=lambda: (0x02E0000000000002,), available=lambda hero, controller: True,
            watch_environment=lambda state, kind: watched.append(state)))
        self.assertEqual(message_parameters.collect(receiver, (sender,), 2),
                         (((0, (expr.Asset(0x02E0000000000002),)),),))
        self.assertTrue(watched)
        self.assertTrue(all(state is receiver for state in watched))

    def test_iterator_pair_keys_are_preserved_and_a_null_value_resets_the_whole_pair(self):
        _, _, sender, _, receiver = setup([row(value=literal(7))])
        with mock.patch.object(play, '_iter_pairs', return_value=iter(
                [(expr.Asset(1), 7), (expr.Asset(2), None)])):
            self.assertEqual(message_parameters.collect(receiver, (sender,), 1),
                             (((expr.Asset(1), 7), (0, 0)),))

    def test_budget_and_unknown_modes_fail_the_owner_without_returning_partial_cache(self):
        _, _, sender, target, receiver = setup([row(value=literal(tuple(range(30))))])
        with self.assertRaises(runtime.ScriptBudgetError), target.component.operation():
            target.component._work_left = 10
            message_parameters.collect(receiver, (sender,), 1)
        _, _, sender, _, receiver = setup()
        with self.assertRaises(runtime.UnsupportedScriptError):
            message_parameters.collect(receiver, (sender,), 3)


if __name__ == '__main__':
    unittest.main()
