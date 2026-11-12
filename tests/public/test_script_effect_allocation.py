"""Native auxiliary effect entries retain ownership after retirement."""

import gc
import unittest
import weakref
from unittest import mock

from ow174.game.script import effect_allocation as allocation
from ow174.game.script import effect_parameters as parameters
from ow174.game.script import expr, runtime
from tests.public.test_script_effect_parameters import ValuesWorld, refresh, setup
from tests.public.test_script_play_resource import EMPTY, integer


class EffectAllocationTests(unittest.TestCase):
    def test_allocations_retain_explicit_context_target_and_observer_with_separate_blocks(self):
        owner = allocation.EffectList()
        context, observer = object(), mock.Mock()
        first = owner.allocate(context=context, target=0xA0000101, observer=observer)
        second = owner.allocate(context=context, target=0, observer=observer)
        self.assertIs(first.context, context)
        self.assertIs(first.observer, observer)
        self.assertEqual((first.target, second.target), (0xA0000101, 0))
        self.assertIsNot(first.parameters, second.parameters)
        self.assertEqual([entry.key for entry in owner.entries], [1, 2])
        self.assertTrue(all(entry.live for entry in owner.entries))
        observer.assert_not_called()

    def test_retirement_marks_membership_without_erasing_or_dispatching_the_observer(self):
        owner = allocation.EffectList()
        observer = mock.Mock()
        first = owner.allocate(context=17, target=23, observer=observer)
        second = owner.allocate(context=17, target=23, observer=observer)
        first.parameters.values[0xC4] = 0.75
        retained = weakref.ref(first)
        self.assertTrue(owner.retire(first))
        self.assertTrue(owner.retire(first))
        del first
        gc.collect()
        self.assertIsNotNone(retained())
        self.assertEqual([entry.live for entry in owner.entries], [False, True])
        self.assertIs(owner.entries[1].effect, second)
        self.assertIs(owner.entries[0].effect.observer, observer)
        self.assertEqual(owner.entries[0].effect.parameters.values[0xC4], 0.75)
        observer.assert_not_called()

    def test_retirement_uses_identity_even_when_all_descriptor_values_match(self):
        left, right = allocation.EffectList(), allocation.EffectList()
        one = left.allocate(context=1, target=2, observer=None)
        other = right.allocate(context=1, target=2, observer=None)
        self.assertFalse(left.retire(other))
        self.assertTrue(left.entries[0].live)
        self.assertTrue(right.entries[0].live)
        self.assertTrue(left.retire(one))

    def test_serial_wrap_sorts_unsigned_keys_and_retirement_does_not_reuse_a_key(self):
        owner = allocation.EffectList(0xFFFFFFFD)
        effects = [owner.allocate(context=1, target=2, observer=None) for _ in range(3)]
        self.assertEqual([entry.key for entry in owner.entries], [0, 0xFFFFFFFE, 0xFFFFFFFF])
        self.assertIs(owner.entries[0].effect, effects[-1])
        self.assertTrue(owner.retire(effects[0]))
        next_effect = owner.allocate(context=1, target=2, observer=None)
        self.assertEqual([entry.key for entry in owner.entries], [0, 1, 0xFFFFFFFE, 0xFFFFFFFF])
        self.assertIs(owner.entries[1].effect, next_effect)
        self.assertEqual(owner.counter, 1)

    def test_invalid_identities_and_unknown_serial_ties_do_not_mutate_the_list(self):
        for counter in (-1, 0x100000000, True, None):
            with self.assertRaises(ValueError):
                allocation.EffectList(counter)
        owner = allocation.EffectList()
        for target in (-1, 0x100000000, True, None):
            with self.assertRaises(ValueError):
                owner.allocate(context=1, target=target, observer=None)
        self.assertEqual((owner.counter, owner.entries), (0, []))
        effect = owner.allocate(context=1, target=2, observer=None)
        owner.counter = 0  # Counter restored into an inconsistent retained-entry catalog.
        with self.assertRaises(ValueError):
            owner.allocate(context=1, target=2, observer=None)
        self.assertEqual(owner.counter, 0)
        self.assertEqual([entry.effect for entry in owner.entries], [effect])


class EffectInitializationTests(unittest.TestCase):
    def test_initial_writes_then_refresh_keep_lifetime_fields_separate(self):
        _, _, state, _ = setup()
        owner = allocation.EffectList()
        effect = owner.allocate(context=17, target=23, observer=state)
        parameters.initialize(state, effect.parameters, context_enabled=True)
        values = effect.parameters.values
        self.assertEqual((values[0x208], values[0x209], values[0x20C], values[0x22C]),
                         (False, False, 0.0, False))
        self.assertEqual((values[0xF4], values[0xF8], values[0xFC]), (2.0, 2.0, 2.0))
        self.assertEqual((values[0xC4], values[0xC8]), (0.5, 0.5))
        self.assertIs(effect.observer, state)
        self.assertFalse(state.instance.queue)
        self.assertEqual(len(values), 25)

    def test_initialization_and_refresh_read_the_shared_scalar_independently_in_native_order(self):
        class ChangingWorld(ValuesWorld):
            def native(self, cfg, instance):
                result = super().native(cfg, instance)
                if cfg['$'] == 'Scale':
                    self.values['Scale'] += 2
                return result

        world = ChangingWorld({'InitialA': True, 'InitialB': True, 'InitialNumber': 1.25,
                               'InitialC': True, 'Scale': 7})
        _, _, state, block = setup({'m_0C4BE468': {'$': 'InitialA'}, 'm_F4669F53': {'$': 'InitialB'},
                                   'm_1A76C130': {'$': 'InitialNumber'}, 'm_494A6B1F': {'$': 'InitialC'},
                                   'm_6A79BD98': {'$': 'Scale'}}, world)
        parameters.initialize(state, block, context_enabled=False)
        self.assertEqual(world.seen, ['InitialA', 'InitialB', 'InitialNumber', 'InitialC', 'Scale', 'Scale'])
        self.assertEqual((block.values[0xF4], block.values[0xF8], block.values[0xFC]), (9, 7, 7))
        self.assertEqual((block.values[0x208], block.values[0x209], block.values[0x20C], block.values[0x22C]),
                         (True, True, 1.25, True))

    def test_regular_refresh_does_not_rewrite_initial_only_flags_or_baseline_scalars(self):
        world = ValuesWorld({'Initial': True, 'Scale': 5})
        _, _, state, block = setup({'m_0C4BE468': {'$': 'Initial'}, 'm_6A79BD98': {'$': 'Scale'}}, world)
        parameters.initialize(state, block, context_enabled=True)
        world.values.update({'Initial': False, 'Scale': 17})
        refresh(state, block)
        self.assertTrue(block.values[0x208])
        self.assertEqual((block.values[0xF4], block.values[0xF8], block.values[0xFC]), (17, 5, 5))

    def test_failed_initial_values_retain_defaults_while_successful_null_casts_to_zero(self):
        world = ValuesWorld({'Null': None})
        _, _, state, block = setup({'m_0C4BE468': EMPTY, 'm_F4669F53': EMPTY,
                                   'm_1A76C130': {'$': 'Null'}, 'm_494A6B1F': EMPTY,
                                   'm_6A79BD98': EMPTY}, world)
        parameters.initialize(state, block, context_enabled=True)
        self.assertFalse(block.values[0x208])
        self.assertFalse(block.values[0x209])
        self.assertFalse(block.values[0x22C])
        self.assertEqual(block.values[0x20C], 0)
        self.assertEqual((block.values[0xF4], block.values[0xF8], block.values[0xFC]), (2, 2, 2))

    def test_unknown_initial_config_stays_strict_and_a_valid_init_does_not_construct_a_state(self):
        component, _, state, block = setup({'m_1A76C130': {'$': 'UnknownEffectCreationInput'}})
        with self.assertRaises(runtime.UnsupportedScriptError):
            parameters.initialize(state, block, context_enabled=True)
        self.assertIsNotNone(component.fault)
        _, _, state, block = setup({'m_6A79BD98': integer(5)})
        parameters.initialize(state, block, context_enabled=True)
        self.assertEqual(list(state.instance.states), [0])
        self.assertEqual(block.values[0xF8], expr.f32(5))


if __name__ == '__main__':
    unittest.main()
