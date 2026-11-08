"""Persistent message-State arbitration, registration and explicit owner limits."""

import math
import unittest

from ow174.game.script import message_manager as native
from ow174.game.script import runtime

LOCAL = native.Key(0xA0000100, 1, 0x6FF)
GLOBAL = native.Key(0, 1, 0x6FF)


def setup():
    events = []
    manager = native.Manager(notify=lambda state: events.append(state),
                             consume_work=lambda operation: None, validate=lambda: None)
    return manager, events


def sender(state=None, priority=0, later=False, frame=10, sequence=-1):
    return native.Sender(object() if state is None else state, priority, later, frame, sequence)


class MessageManagerTests(unittest.TestCase):
    def test_highest_priority_wins_even_if_all_priorities_are_negative(self):
        manager, _ = setup()
        values = [sender(priority=p) for p in (-5, -2, -3)]
        for record in values:
            manager.register_sender(LOCAL, record)
        self.assertIs(manager.select(LOCAL).state, values[1].state)

    def test_equal_priority_flag_and_frame_sequence_order(self):
        for later, expected in ((False, 1), (True, 2)):
            manager, _ = setup()
            values = [sender(later=later, frame=f, sequence=s)
                      for f, s in ((10, 5), (9, 6), (11, 1))]
            for record in values:
                manager.register_sender(LOCAL, record)
            self.assertIs(manager.select(LOCAL).state, values[expected].state)
        manager, _ = setup()
        first, preferred = sender(sequence=0), sender(later=True, sequence=0)
        manager.register_sender(LOCAL, first)
        manager.register_sender(LOCAL, preferred)
        self.assertIs(manager.select(LOCAL).state, preferred.state)

    def test_duplicate_order_preserves_first_or_replaces_with_last_by_flag(self):
        for later, expected in ((False, 0), (True, 1)):
            manager, _ = setup()
            values = [sender(later=later, sequence=7) for _ in range(2)]
            for record in values:
                manager.register_sender(LOCAL, record)
            self.assertIs(manager.select(LOCAL).state, values[expected].state)

    def test_negative_frame_and_signed_low_word_use_native_unsigned_or_order(self):
        manager, _ = setup()
        first = sender(later=True, frame=-1, sequence=0)
        second = sender(later=True, frame=0, sequence=-2)
        manager.register_sender(LOCAL, first)
        manager.register_sender(LOCAL, second)
        self.assertIs(manager.select(LOCAL).state, second.state)
        self.assertEqual(second.order, 0xFFFFFFFFFFFFFFFE)

    def test_nan_priority_uses_unordered_equal_branch_and_float32_rounding(self):
        manager, _ = setup()
        first, second = sender(priority=math.nan), sender(priority=100, later=True)
        manager.register_sender(LOCAL, first)
        manager.register_sender(LOCAL, second)
        self.assertIs(manager.select(LOCAL).state, second.state)
        self.assertEqual(sender(priority=16777217).priority, 16777216)

    def test_local_then_global_fallback_preserves_duplicates_and_exact_channel_keys(self):
        manager, _ = setup()
        shared, other = object(), object()
        manager.register_sender(LOCAL, sender(shared, priority=2))
        manager.register_sender(GLOBAL, sender(shared, priority=3))
        manager.register_sender(native.Key(LOCAL.entity, 2, LOCAL.message), sender(other, priority=99))
        manager.register_sender(native.Key(LOCAL.entity, 1, LOCAL.message+1), sender(other, priority=99))
        self.assertEqual(manager.collect(LOCAL), (shared, shared))
        self.assertIs(manager.select(LOCAL).state, shared)
        self.assertEqual(manager.collect(GLOBAL), (shared,))
        for entity in (0xFFFFFFFF, 0x1FFFFFFF, 0xBFFFFFFF):
            self.assertEqual(manager.collect(native.Key(entity, 1, LOCAL.message)), ())

    def test_worse_candidates_skip_filter_unless_count_is_requested(self):
        manager, _ = setup()
        first, worse, better = sender(priority=5), sender(priority=1), sender(priority=7)
        for record in (first, worse, better):
            manager.register_sender(LOCAL, record)
        calls = []
        def predicate(record):
            calls.append(record.state)
            return record.state is not better.state

        selected = manager.select(LOCAL, predicate=predicate)
        self.assertIs(selected.state, first.state)
        self.assertIsNone(selected.matches)
        self.assertEqual(calls, [first.state, better.state])
        calls.clear()
        selected = manager.select(LOCAL, predicate=predicate, count_matches=True)
        self.assertIs(selected.state, first.state)
        self.assertEqual(selected.matches, 2)
        self.assertEqual(calls, [first.state, worse.state, better.state])
        calls.clear()
        self.assertEqual(manager.collect(LOCAL, predicate=predicate), (first.state, worse.state))
        self.assertEqual(calls, [first.state, worse.state, better.state])

    def test_filters_receive_copies_and_unknown_results_are_refused(self):
        manager, _ = setup()
        record = sender(priority=3)
        manager.register_sender(LOCAL, record)

        def predicate(copy):
            copy.priority = 100
            return True

        manager.select(LOCAL, predicate=predicate)
        self.assertEqual(manager.channels[LOCAL].senders[0].priority, 3)
        for invalid in (NotImplemented, False, 7):
            with self.assertRaisesRegex(ValueError, 'filter must be resolved'):
                manager.select(LOCAL, predicate=invalid)
        for invalid in (None, 0, NotImplemented):
            with self.assertRaisesRegex(ValueError, 'filter result'):
                manager.collect(LOCAL, predicate=lambda record, result=invalid: result)

    def test_sequence_assignment_is_shared_across_channels_and_reset_by_frame(self):
        manager, _ = setup()
        first = sender()
        manager.register_sender(LOCAL, first)
        self.assertEqual((first.sequence, manager.next_sequence), (0, 1))
        manager.register_sender(GLOBAL, first)
        self.assertEqual((first.sequence, manager.next_sequence), (0, 2))
        second = sender()
        manager.register_sender(LOCAL, second)
        self.assertEqual(second.sequence, 2)
        preserved = sender(sequence=8)
        manager.register_sender(LOCAL, preserved)
        self.assertEqual(manager.next_sequence, 9)
        next_frame = sender(frame=11)
        manager.register_sender(LOCAL, next_frame)
        self.assertEqual((next_frame.sequence, manager.next_sequence), (0, 1))
        wrapped = sender(frame=11, sequence=0x7FFFFFFF)
        manager.register_sender(LOCAL, wrapped)
        self.assertEqual(manager.next_sequence, -0x80000000)

    def test_state_identity_upsert_copies_metadata_and_keeps_position(self):
        manager, events = setup()
        receiver = object()
        manager.register_receiver(LOCAL, receiver)
        first, second = sender(priority=1), sender(priority=2)
        manager.register_sender(LOCAL, first)
        manager.register_sender(LOCAL, second)
        first.priority = 5
        self.assertIs(manager.select(LOCAL).state, second.state)
        manager.register_sender(LOCAL, first)
        self.assertEqual(manager.collect(LOCAL), (first.state, second.state))
        self.assertIs(manager.select(LOCAL).state, first.state)
        self.assertEqual(events, [receiver]*3)

    def test_receiver_duplicates_swap_removal_and_notification_order(self):
        manager, events = setup()
        a, b = object(), object()
        for receiver in (a, b, a):
            manager.register_receiver(LOCAL, receiver)
        record = sender()
        manager.register_sender(LOCAL, record)
        self.assertEqual(events, [a, b, a])
        events.clear()
        manager.unregister_receiver(LOCAL, a)
        manager.unregister_sender(LOCAL, record.state)
        self.assertEqual(events, [a, b])
        self.assertEqual(manager.channels[LOCAL].receivers, [a, b])
        manager.unregister_receiver(LOCAL, a)
        manager.unregister_receiver(LOCAL, b)
        self.assertFalse(manager.channels)

    def test_sender_swap_removal_and_absent_sender_do_not_change_other_channels(self):
        manager, events = setup()
        values = [sender() for _ in range(3)]
        for record in values:
            manager.register_sender(LOCAL, record)
        manager.register_sender(GLOBAL, values[0])
        manager.unregister_sender(LOCAL, values[0].state)
        self.assertEqual(manager.collect(LOCAL), (values[2].state, values[1].state, values[0].state))
        manager.unregister_sender(LOCAL, object())
        self.assertFalse(events)
        manager.unregister_sender(GLOBAL, values[0].state)
        self.assertNotIn(GLOBAL, manager.channels)

    def test_missing_receiver_in_existing_channel_is_explicit_invalid_operation(self):
        manager, _ = setup()
        record = sender()
        manager.register_sender(LOCAL, record)
        with self.assertRaisesRegex(ValueError, 'not registered'):
            manager.unregister_receiver(LOCAL, object())
        manager.unregister_receiver(GLOBAL, object())
        self.assertEqual(manager.collect(LOCAL), (record.state,))

    def test_budget_failure_stops_selection_and_releases_callback_guard(self):
        manager, _ = setup()
        manager.register_sender(LOCAL, sender())
        manager.register_sender(LOCAL, sender())
        calls = []

        def consume(operation):
            calls.append(operation)
            if len(calls) == 3:
                raise RuntimeError('budget exhausted')

        manager.consume_work = consume
        with self.assertRaisesRegex(RuntimeError, 'budget exhausted'):
            manager.collect(LOCAL)
        self.assertEqual(manager._busy, 0)
        manager.consume_work = lambda operation: None
        manager.unregister_sender(LOCAL, object())

    def test_retirement_from_filter_or_notification_aborts_remaining_callbacks(self):
        for source in ('filter', 'notify'):
            manager, _ = setup()
            first, second = sender(), sender()
            manager.register_sender(LOCAL, first)
            manager.register_sender(LOCAL, second)
            calls = []

            def retire(value, calls=calls, manager=manager):
                calls.append(value)
                manager.retire()
                return True

            if source == 'filter':
                def operation(manager=manager, retire=retire):
                    return manager.collect(LOCAL, predicate=retire)
            else:
                manager.register_receiver(LOCAL, object())
                manager.register_receiver(LOCAL, object())
                manager.notify = retire
                def operation(manager=manager, first=first):
                    manager.register_sender(LOCAL, first)
            with self.assertRaisesRegex(ValueError, 'retired'):
                operation()
            self.assertEqual(len(calls), 1)
            self.assertFalse(manager.channels)
            self.assertEqual(manager._busy, 0)

    def test_callback_mutation_and_owner_failure_remain_errors(self):
        manager, _ = setup()
        record = sender()
        manager.register_sender(LOCAL, record)
        with self.assertRaisesRegex(ValueError, 'mutation during callback'):
            manager.collect(LOCAL, predicate=lambda row: manager.unregister_sender(LOCAL, row.state))
        self.assertEqual(manager._busy, 0)
        checks = []

        def validate():
            checks.append(True)
            if len(checks) == 2:
                raise RuntimeError('owner failed')

        manager.validate = validate
        with self.assertRaisesRegex(RuntimeError, 'owner failed'):
            manager.collect(LOCAL)

    def test_actual_vm_operation_budget_and_fault_are_shared_with_the_owner(self):
        component = runtime.Component(LOCAL.entity, strict=True)
        manager = native.Manager(notify=lambda receiver: None, consume_work=component.consume_work,
                                 validate=component.ensure_valid)
        with component.operation():
            manager.register_sender(LOCAL, sender())
        with (self.assertRaisesRegex(runtime.ScriptBudgetError, 'message manager candidate'),
              component.operation()):
            component._work_left = 1
            manager.collect(LOCAL)
        self.assertEqual(manager._busy, 0)
        with self.assertRaises(runtime.ScriptBudgetError) as caught:
            manager.select(LOCAL)
        self.assertIs(caught.exception, component.fault)
        manager.retire()
        self.assertFalse(manager.channels)

    def test_retire_is_idempotent_and_never_calls_owners_or_filters(self):
        manager, _ = setup()
        manager.register_sender(LOCAL, sender())
        manager.register_receiver(LOCAL, object())

        def fail(*args):
            self.fail('terminal cleanup ran an owner callback')

        manager.notify = manager.consume_work = manager.validate = fail
        manager.retire()
        manager.retire()
        self.assertFalse(manager.channels)
        with self.assertRaisesRegex(ValueError, 'retired'):
            manager.select(LOCAL)

    def test_channel_and_sender_descriptors_reject_implicit_or_invalid_policies(self):
        for args in ((-1, 1, 2), (0, True, 2), (0, 1, 1 << 32)):
            with self.assertRaises(ValueError):
                native.Key(*args)
        for args in ((None, 0, False, 0), (object(), False, False, 0),
                     (object(), 0, 1, 0), (object(), 0, False, 1 << 31)):
            with self.assertRaises(ValueError):
                native.Sender(*args)
        manager, _ = setup()
        with self.assertRaises(ValueError):
            manager.register_sender((LOCAL.entity, 1, LOCAL.message), sender())
        with self.assertRaises(ValueError):
            manager.select(LOCAL, count_matches=1)
        self.assertEqual(manager.select(LOCAL, count_matches=True), native.Selection(None, 0))


if __name__ == '__main__':
    unittest.main()
