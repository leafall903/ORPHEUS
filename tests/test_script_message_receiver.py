"""Persistent receiver lifetimes, row-ID bindings and native lazy outputs."""

import unittest
from dataclasses import replace
from types import SimpleNamespace
from unittest import mock

from ow174.game import statescript
from ow174.game.bits import BitReader, BitWriter
from ow174.game.script import decode, expr, message_bindings, message_manager, message_receiver, runtime
from ow174.game.script.driver import BodyScript
from tests.test_script_message_state import OTHER, OWNER, begin, global_target, literal
from tests.test_script_message_state import setup as sender_setup
from tests.test_script_runtime_budget import graph, node, plug, setter

KEY = 0x0A4000000000008D
MESSAGE = 0x02400000000006FF
POLICY = message_receiver.Policy(False, False, False, False, False, False, True, False, False, False)


def integer(value):
    return {'$': 'STUConfigVarInt', 'm_value': value}


def variable(identifier):
    return {'$': 'STUConfigVarDynamic', 'm_identifier': identifier, 'm_60DB8F99': 0}


def message(value=MESSAGE):
    return {'$': 'STUConfigVarGameMessage', 'm_gameMessage': f'0x{value:016X}'}


def parameter(cfg=None, *, key=KEY, output=None):
    return {'m_B5051BCE': f'0x{key:016X}', 'm_value': cfg, 'm_out_Var': output}


def setup(value=None, *, policy=POLICY, source=True, **fields):
    world, component, source_instance, sender, context, _, _ = sender_setup(
        m_gameMessage=message(), m_params=[parameter(integer(7) if value is None else value)])
    context.receiver_policy = lambda state: policy
    context.receiver_generation = lambda state: 17
    context.host.manager.notify = message_receiver.notify
    if source:
        begin(sender)
    data = {'m_gameMessage': message(), 'm_params': [parameter(output=variable(41))], 'm_39B47960': 0}
    data.update(fields)
    model = graph([node(0, 'STUStatescriptStateGameMessageEntry', state=0, **data),
                   setter(1, 31), setter(2, 32), node(3, 'STUStatescriptState', state=1)])
    target = component.create(model, init=False)
    target.initialized = True
    return world, source_instance, sender, target, target.state(0), context


class MessageReceiverTests(unittest.TestCase):
    def test_begin_resolves_persistent_sender_and_context_outputs_before_authored_begin_plug(self):
        _, source, sender, target, receiver, _ = setup(
            m_FF703F20=variable(42), m_B60ABDA4=variable(43), m_82B03EC1=variable(44),
            m_onBeginPlug=plug((1, 'm_inPlug')))
        source.set_source_entity(OTHER)
        begin(receiver)
        self.assertIs(receiver.message_selected, sender)
        self.assertEqual(target.find_var(0, 41).value(), 7)
        self.assertEqual(target.find_var(0, 42).value(), expr.Entity(OTHER))
        self.assertEqual(target.find_var(0, 43).value(), expr.Entity(OWNER))
        self.assertEqual(target.find_var(0, 44).value(), 1)
        self.assertEqual(target.find_var(0, 7).value(), 31)
        self.assertEqual(receiver.payload(), {'sender': OWNER, 'stacked': False})

    def test_empty_begin_enters_idle_and_sender_registration_queues_receive_at_receiver_clock(self):
        _, _, sender, target, receiver, _ = setup(source=False,
            m_33DA5163=plug((1, 'm_inPlug')), m_452862E6=plug((2, 'm_inPlug')))
        begin(receiver)
        self.assertEqual(target.find_var(0, 7).value(), 31)
        begin(sender)
        self.assertTrue(any(event.kind == runtime.CHANGED for event in target.queue))
        with target.component.operation():
            target.update()
        self.assertEqual(target.find_var(0, 7).value(), 32)
        self.assertEqual(receiver.message_sender, OWNER)
        with target.component.operation():
            sender.end(False)
            target.update()
        self.assertEqual(target.find_var(0, 7).value(), 31)
        self.assertEqual(receiver.message_sender, 0)

    def test_key_change_clears_flags_with_old_nonzero_key(self):
        _, _, _, _, receiver, context = setup()
        begin(receiver)
        receiver.node.fields['m_gameMessage'] = message(MESSAGE+1)
        with receiver.instance.component.operation():
            receiver.dependency_changed()
        self.assertEqual(receiver.message_flags, 0)
        self.assertEqual(receiver.message_key, (MESSAGE+1) & 0xFFFFFFFF)
        old = message_manager.Key(OWNER, 1, MESSAGE & 0xFFFFFFFF)
        self.assertNotIn(old, context.host.manager.channels)
        self.assertIn(message_manager.Key(OWNER, 0, (MESSAGE+1) & 0xFFFFFFFF), context.host.manager.channels)

    def test_native_parameter_queries_enable_stacked_links_and_cache_stage_ticks(self):
        _, _, _, target, receiver, _ = setup(literal(7))
        begin(receiver)
        self.assertTrue(receiver.message_stacked)
        self.assertFalse(receiver.message_refresh)
        self.assertIn(receiver, target.ticking)
        var = target.find_var(0, 41)
        self.assertEqual(var.value(), 7)
        self.assertEqual([(link.state, link.slot, link.stack) for link in var.links], [(receiver, 0, True)])

    def test_duplicate_output_targets_keep_distinct_rows_and_refresh_preserves_order(self):
        _, source, _, target, receiver, _ = setup(literal(7), m_params=[
            parameter(output=variable(41)), parameter(key=KEY+1, output=variable(41))])
        source.graph.node(0).fields['m_params'].append(parameter(literal(8), key=KEY+1))
        begin(receiver)
        var = target.find_var(0, 41)
        self.assertEqual([link.slot for link in var.links], [0, 1])
        self.assertEqual(var.value(), 8)
        with target.component.operation():
            receiver.dependency_changed()
        self.assertEqual([link.slot for link in var.links], [0, 1])
        message_bindings.remove(receiver, 1)
        self.assertEqual([link.slot for link in var.links], [0])
        self.assertEqual(var.value(), 7)

    def test_live_scalar_outputs_reselect_and_never_watch_the_source_as_a_getter(self):
        _, source, _, target, receiver, _ = setup(literal(7))
        begin(receiver)
        queried = []
        source.graph.node(0).fields['m_params'][0]['m_value'] = literal(
            lambda instance: queried.append(instance) or 9)
        receiver.unwatch()
        receiver.polled = False
        self.assertEqual(target.find_var(0, 41).value(), 9)
        self.assertEqual(queried, [source])
        self.assertFalse(receiver.polled)
        self.assertFalse(receiver.watched)
        self.assertEqual(receiver.message_generation, -1)

    def test_failed_output_falls_through_but_successful_null_overrides_the_base(self):
        _, source, _, target, receiver, _ = setup(literal(7))
        target.write((0, 41), 99)
        begin(receiver)
        source.graph.node(0).fields['m_params'][0]['m_value'] = literal(expr.EVALUATION_FAILED)
        self.assertEqual(target.find_var(0, 41).value(), 99)
        source.graph.node(0).fields['m_params'][0]['m_value'] = literal(None)
        self.assertIsNone(target.find_var(0, 41).value())
        self.assertFalse(receiver.message_getting)

    def test_recursive_output_falls_through_without_recursive_source_evaluation(self):
        _, source, _, target, receiver, _ = setup(literal(7))
        target.write((0, 41), 99)
        begin(receiver)
        source.graph.node(0).fields['m_params'][0]['m_value'] = literal(
            lambda instance: target.find_var(0, 41).value())
        self.assertEqual(target.find_var(0, 41).value(), 99)
        self.assertFalse(receiver.message_getting)

    def test_multi_sender_modes_collect_in_order_and_unregistered_tick_uses_one_live_sender(self):
        _, source, _, target, receiver, context = setup(literal((7, 8)), m_39B47960=1)
        other = target.component.create(graph([node(0, 'STU_38EE1100', state=0,
            m_gameMessage=message(), m_target=global_target(), m_params=[parameter(integer(9))])]),
            init=False)
        other.initialized = True
        begin(other.state(0))
        begin(receiver)
        self.assertEqual(target.find_var(0, 41).value(), (7, 8, 9))
        target.ticking.remove(receiver)
        self.assertEqual(target.find_var(0, 41).value(), (7, 8))
        self.assertIs(receiver.message_selected.instance, source)
        self.assertIs(receiver.message_context, context)

    def test_baking_a_stacked_row_uses_its_previous_target_and_only_removes_its_id(self):
        _, source, _, target, receiver, _ = setup(literal(7))
        begin(receiver)
        source.graph.node(0).fields['m_params'][0]['m_value'] = integer(9)
        receiver.node.fields['m_params'][0]['m_out_Var'] = variable(42)
        with target.component.operation():
            receiver.dependency_changed()
        self.assertFalse(receiver.message_stacked)
        self.assertEqual(target.find_var(0, 41).value(), 9)
        self.assertFalse(target.find_var(0, 41).links)
        self.assertIsNone(target.find_var(0, 42).value())

    def test_filter_scope_uses_source_parameters_and_is_popped_on_failure(self):
        _, _, _, target, receiver, context = setup(integer(7), m_filter={'$': 'DeclaredFilter'})
        seen = []
        context.receiver_filter = lambda receiving, sender, cfg: seen.append(
            target.context().param(KEY & 0xFFFF)) or True
        begin(receiver)
        self.assertEqual(seen, [7])
        self.assertFalse(target.component.parameter_scopes)
        context.receiver_filter = lambda *args: context.retire() or True
        with self.assertRaises(runtime.UnsupportedScriptError), target.component.operation():
            receiver.dependency_changed()
        self.assertFalse(target.component.parameter_scopes)

    def test_skip_application_still_writes_instigator_but_not_params_caller_or_count(self):
        _, source, _, target, receiver, _ = setup(policy=replace(POLICY, skip_application=True),
            m_FF703F20=variable(42), m_B60ABDA4=variable(43), m_82B03EC1=variable(44))
        source.set_source_entity(OTHER)
        begin(receiver)
        self.assertEqual(target.find_var(0, 42).value(), expr.Entity(OTHER))
        self.assertIsNone(target.find_var(0, 41).value())
        self.assertIsNone(target.find_var(0, 43).value())
        self.assertIsNone(target.find_var(0, 44).value())
        self.assertEqual(receiver.message_sender, 0)

    def test_deferred_begin_and_missing_engine_services_do_not_invent_roles(self):
        _, _, _, _, receiver, context = setup(policy=replace(POLICY, deferred_begin=True))
        begin(receiver)
        self.assertEqual(receiver.message_key, 0)
        self.assertIsNone(receiver.message_selected)
        context.receiver_generation = None
        with self.assertRaises(runtime.UnsupportedScriptError), receiver.instance.component.operation():
            receiver.dependency_changed()

    def test_retirement_discards_channels_and_links_without_borrowing_replacement(self):
        _, _, _, target, receiver, context = setup(literal(7))
        begin(receiver)
        var = target.find_var(0, 41)
        with mock.patch.object(type(receiver), 'output', side_effect=AssertionError('getter during retire')):
            context.retire()
        self.assertFalse(var.links)
        self.assertIs(receiver.message_context, context)
        self.assertFalse(context.host.manager.channels)

    def test_destroy_a_live_receiver_has_no_remaining_links_or_channels(self):
        _, _, _, target, receiver, context = setup(literal(7))
        begin(receiver)
        var = target.find_var(0, 41)
        with target.component.operation():
            target.component.destroy(target)
        self.assertFalse(var.links)
        self.assertNotIn(receiver, context.states)
        self.assertTrue(all(receiver not in channel.receivers
                            for channel in context.host.manager.channels.values()))

    def test_cached_source_changes_notify_output_watchers_even_when_getter_already_reads_new_value(self):
        _, source, _, target, receiver, _ = setup(literal(7))
        begin(receiver)
        watcher = target.state(1)
        begin(watcher)
        var = target.find_var(0, 41)
        watcher.watch(var)
        target.queue.clear()
        source.graph.node(0).fields['m_params'][0]['m_value'] = literal(8)
        self.assertEqual(var.value(), 8)
        with target.component.operation():
            receiver.dependency_changed()
        self.assertTrue(any(event.state is watcher and event.kind == runtime.CHANGED
                            for event in target.queue))

    def test_recursive_selection_filter_reads_the_lower_value_without_reentering_the_link(self):
        _, _, _, target, receiver, context = setup(literal(7), m_filter={'$': 'DeclaredFilter'})
        target.write((0, 41), 99)
        context.receiver_filter = lambda *args: True
        begin(receiver)
        seen = []
        context.receiver_filter = lambda *args: seen.append(target.find_var(0, 41).value()) or True
        self.assertEqual(target.find_var(0, 41).value(), 7)
        self.assertEqual(seen, [99])
        self.assertFalse(target.find_var(0, 41).resolving)

    def test_filter_dependency_uses_a_different_epoch_from_parameter_dependency(self):
        _, _, _, target, receiver, context = setup(integer(7), m_filter={'$': 'DeclaredFilter'})

        def predicate(*args):
            receiver.native_dependency({'$': 'DeclaredFilterQuery'})
            return True

        context.receiver_filter = predicate
        begin(receiver)
        self.assertTrue(receiver.message_refresh)
        self.assertFalse(receiver.message_stacked)
        self.assertIn(receiver, target.ticking)
        self.assertEqual((receiver.message_epoch, receiver.message_last_epoch), (2, 1))

    def test_generation_minus_one_retains_the_initial_null_selection(self):
        _, _, _, target, receiver, context = setup(m_82B03EC1=variable(44))
        context.receiver_generation = lambda state: -1
        begin(receiver)
        self.assertIsNone(receiver.message_selected)
        self.assertEqual(receiver.message_sender, 0)
        self.assertEqual(target.find_var(0, 44).value(), 0)

    def test_non_authoritative_cleanup_retains_inactive_links_until_physical_destruction(self):
        _, _, _, target, receiver, context = setup(literal(7), policy=replace(POLICY, authoritative=False))
        target.write((0, 41), 99)
        begin(receiver)
        var = target.find_var(0, 41)
        with target.component.operation():
            receiver.end(False)
        self.assertFalse(receiver.active)
        self.assertTrue(receiver.message_stacked)
        self.assertTrue(var.links)
        self.assertEqual(var.value(), 99)
        with target.component.operation():
            target.component.destroy(target)
        self.assertFalse(var.links)
        self.assertNotIn(receiver, context.states)

    def test_retired_state_cannot_borrow_a_replacement_context_on_the_same_component(self):
        world, _, _, target, receiver, context = setup(literal(7))
        begin(receiver)
        context.retire()
        replacement = type(context)(target.component, context.host, policy=context.policy,
            frame=context.frame, priority=context.priority, receiver_policy=context.receiver_policy,
            receiver_generation=context.receiver_generation)
        world.set_message_state_context(OWNER, replacement)
        with self.assertRaises(runtime.UnsupportedScriptError):
            receiver.output(0)
        self.assertIsNot(receiver.message_context, replacement)

    def test_parameter_work_fault_clears_binding_resolution_guards_and_links(self):
        _, source, _, target, receiver, _ = setup(literal(7))
        begin(receiver)
        var = target.find_var(0, 41)

        def expensive(instance):
            for _ in range(100):
                instance.component.consume_work('fixture parameter work')
            return 8

        source.graph.node(0).fields['m_params'][0]['m_value'] = literal(expensive)
        with self.assertRaises(runtime.ScriptBudgetError), target.component.operation():
            target.component._work_left = 20
            var.value()
        self.assertFalse(var.resolving)
        self.assertIsNone(var.partial)
        self.assertFalse(var.links)

    def test_dynamic_binding_policy_survives_driver_snapshot_and_wire(self):
        _, _, _, target, receiver, _ = setup(literal(7))
        begin(receiver)
        var = target.find_var(0, 41)
        snapshot = SimpleNamespace(vars={})
        BodyScript._add_var(None, snapshot, target.id, 41, var)
        value, bindings = snapshot.vars[(target.id, 41)]
        self.assertEqual(value, 7)
        self.assertTrue(bindings[0].dynamic)
        self.assertFalse(bindings[0].frame_cached)
        target.graph.header['m_statesBitCount'] = 1
        out = BitWriter()
        statescript.write_bindings(out, bindings, target.id, {target.id: target.graph})
        reader = BitReader(out.getvalue())
        with mock.patch.object(decode, 'graph_of', return_value=target.graph):
            result = decode.read_bindings(reader, target.id, {target.id: target.graph.index})
        self.assertTrue(result[0].dynamic)
        self.assertFalse(result[0].frame_cached)
        self.assertEqual(reader.pos, out.count)

    def test_array_checkpoint_refuses_other_links_and_null_destination_skips_storage(self):
        _, _, _, target, receiver, _ = setup(integer(7), m_39B47960=2)
        message_bindings.array(receiver, None, ((expr.Asset(1), 7),))
        other = target.state(1)
        begin(other)
        var = target.find_var(0, 41)
        var.base = (99,)
        var.links.append(runtime.Link(other))
        with self.assertRaises(runtime.UnsupportedScriptError):
            begin(receiver)
        self.assertEqual(var.base, (99,))

    def test_wire_payload_encodes_sender_kind_and_stacked_flag(self):
        for sender in (0, 0x10000101, 0x80000101, 0xA0000101, 0xFFFFFFFF):
            for stacked in (False, True):
                out = BitWriter()
                statescript.write_payload(out, 'STUStatescriptStateGameMessageEntry',
                    {'sender': sender, 'stacked': stacked}, 0, 16)
                reader = BitReader(out.getvalue())
                valid = expr.valid_entity(sender) and bool(sender & 0x80000000)
                self.assertEqual(bool(reader.bit()), valid)
                if valid:
                    self.assertEqual(bool(reader.bit()), sender & 0xA0000000 == 0xA0000000)
                    self.assertEqual(reader.var_b(), sender & 0x1FFFFFFF)
                self.assertEqual(bool(reader.bit()), stacked)


if __name__ == '__main__':
    unittest.main()
