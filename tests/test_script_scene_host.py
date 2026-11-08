"""Shared component values/messages, generation retirement and ordinary scene scheduling."""

import unittest
from unittest import mock

from ow174.game.script import expr, runtime, scene, scene_host
from tests.test_script_runtime_budget import graph, node, plug, setter
from tests.test_script_startup import definition, row, schema, spec

A, B = 0x80000001, 0x80000002
MESSAGE = 0x0240000000000001


def literal(value):
    return {'$': 'SceneLiteral', 'value': value}


class ValueWorld(scene.SceneWorld):
    def native(self, cfg, instance):
        if cfg.get('$') == 'SceneLiteral':
            return cfg['value']
        if cfg.get('$') == 'ReadOther':
            return self.entity_var(B, 9)
        return super().native(cfg, instance)


def make_world():
    world = ValueWorld()
    world.bind(A)
    world.bind(B)
    return world


def component(world, entity, model=None):
    result = runtime.Component(entity, world=world, strict=True)
    instance = result.create(graph() if model is None else model, init=False)
    instance.initialized = True
    world.bind_script_component(entity, result)
    return result, instance


def message_model():
    return graph([node(0, 'STUStatescriptEntryGameMessage',
                       m_gameMessage={'$': 'STUConfigVarGameMessage', 'm_gameMessage': f'0x{MESSAGE:016X}'},
                       m_params=[{'m_B5051BCE': '0x0D80000000000007',
                                  'm_out_Var': {'$': 'STUConfigVarDynamic', 'm_identifier': 9}}],
                       m_outPlug=plug((1, 'm_inPlug'))), setter(1, 23)], entries=[0])


def sender_model():
    return graph([node(0, 'STUStatescriptActionSendGameMessage',
                       m_gameMessage={'$': 'STUConfigVarGameMessage', 'm_gameMessage': f'0x{MESSAGE:016X}'},
                       m_targets=literal(expr.Entity(B)),
                       m_params=[{'m_B5051BCE': '0x0D80000000000007',
                                  'm_value': {'$': 'STUConfigVarInt', 'm_value': 17}}],
                       m_outPlug=plug((1, 'm_inPlug'))), setter(1, 31)])


class SharedSceneTests(unittest.TestCase):
    def test_binding_requires_an_actual_component_on_the_bound_entity_and_world(self):
        world = make_world()
        for candidate in (runtime.Component(123, world=world), runtime.Component(A), object()):
            with self.subTest(candidate=type(candidate)), self.assertRaises(ValueError):
                world.bind_script_component(A, candidate)
        with self.assertRaises(ValueError):
            world.bind_script_component(123, None)

    def test_live_foreign_values_include_effective_links_and_subscribe_to_changes(self):
        world = make_world()
        _, caller = component(world, A, graph([node(0, 'STUStatescriptState', state=0)]))
        target, provider = component(world, B, graph([node(0, 'STUStatescriptState', state=0)]))
        watcher, output = caller.state(0), provider.state(0)
        watcher.active = output.active = True
        output.output = lambda slot: expr.Asset(23)
        variable = target.entity_var(9)
        variable.base = 17
        variable.links.append(runtime.Link(output))
        self.assertEqual(caller.context(watcher).member(B, 9), expr.Asset(23))
        self.assertIn(watcher, variable.watchers)
        variable.links.clear()
        target.changed(variable, expr.Asset(23))
        self.assertTrue(watcher.flags_changed)
        self.assertEqual(len(caller.queue), 1)
        self.assertEqual(caller.context().member(B, 9), 17)

    def test_unknown_foreign_components_fail_but_known_absence_and_missing_entities_do_not(self):
        world = make_world()
        _, caller = component(world, A)
        with self.assertRaisesRegex(runtime.UnsupportedScriptError, 'entity variable service'):
            caller.context().member(B, 9)
        world.unbind_script_component(A)
        _, caller = component(world, A)
        world.bind_script_component(B, None)
        self.assertIsNone(caller.context().member(B, 9))
        self.assertIsNone(caller.context().member(0x80000003, 9))

    def test_retiring_a_foreign_component_invalidates_its_watchers(self):
        world = make_world()
        _, caller = component(world, A, graph([node(0, 'STUStatescriptState', state=0)]))
        target, _ = component(world, B)
        watcher = caller.state(0)
        watcher.active = True
        caller.context(watcher).member(B, 9)
        variable = target.entity_var(9)
        world.unbind(B)
        self.assertFalse(variable.watchers)
        self.assertNotIn(variable, watcher.watched)
        self.assertTrue(watcher.flags_changed)
        self.assertIsNone(caller.context().member(B, 9))

    def test_retiring_the_watching_component_unsubscribes_it_from_foreign_storage(self):
        world = make_world()
        _, caller = component(world, A, graph([node(0, 'STUStatescriptState', state=0)]))
        target, _ = component(world, B)
        watcher = caller.state(0)
        watcher.active = True
        caller.context(watcher).member(B, 9)
        variable = target.entity_var(9)
        world.unbind(A)
        self.assertFalse(variable.watchers)
        self.assertFalse(watcher.watched)

    def test_messages_reach_actual_receiver_instances_at_the_receiver_clock(self):
        world = make_world()
        _, sender = component(world, A, sender_model())
        target, receiver = component(world, B, message_model())
        target.now = 400
        sender.run_node(sender.graph.node(0), None)
        self.assertEqual(sender.find_var(0, 7).value(), 31)
        self.assertEqual(len(receiver.queue), 2)
        self.assertEqual(receiver.queue[0].time, 400)
        self.assertEqual([event.kind for event in receiver.queue], [runtime.ASSIGN, runtime.ENTRY])
        self.assertEqual(receiver.queue[0].assignment, (0, 9, (17,)))
        self.assertIsNone(receiver.find_var(0, 7, create=False))
        target.run_now()
        self.assertEqual(receiver.find_var(0, 7).value(), 23)
        self.assertEqual(receiver.find_var(0, 9).value(), (17,))

    def test_unknown_message_transport_stops_before_following_success(self):
        source = runtime.Component(A, world=runtime.World(), strict=True)
        sender = source.create(sender_model(), init=False)
        with self.assertRaisesRegex(runtime.UnsupportedScriptError, 'game message owner'):
            sender.run_node(sender.graph.node(0), None)
        self.assertIsNone(sender.find_var(0, 7, create=False))

    def test_unknown_target_component_stops_instead_of_dropping_authored_messages(self):
        world = make_world()
        _, sender = component(world, A, sender_model())
        with self.assertRaisesRegex(runtime.UnsupportedScriptError, 'scene message target component'):
            sender.run_node(sender.graph.node(0), None)
        self.assertIsNone(sender.find_var(0, 7, create=False))

    def test_known_missing_message_receiver_preserves_the_native_drop_path(self):
        for missing_entity in (False, True):
            world = make_world()
            _, sender = component(world, A, sender_model())
            if missing_entity:
                world.unbind(B)
            else:
                world.bind_script_component(B, None)
            sender.run_node(sender.graph.node(0), None)
            self.assertEqual(sender.find_var(0, 7).value(), 31)

    def test_stale_source_cannot_send_after_component_replacement(self):
        world = make_world()
        old, sender = component(world, A, sender_model())
        _, receiver = component(world, B, message_model())
        world.unbind_script_component(A, old)
        replacement, _ = component(world, A)
        with self.assertRaisesRegex(runtime.UnsupportedScriptError, 'scene message source lifecycle'):
            sender.run_node(sender.graph.node(0), None)
        self.assertIs(world.script_components[A], replacement)
        self.assertFalse(receiver.queue)
        with self.assertRaises(ValueError):
            world.unbind_script_component(A, old)

    def test_failed_target_variables_are_not_read_as_live_world_state(self):
        world = make_world()
        _, caller = component(world, A)
        target, receiver = component(world, B, graph([node(0, 'UnknownAction')]))
        target.set(9, 17)
        with self.assertRaises(runtime.UnsupportedScriptError):
            receiver.run_node(receiver.graph.node(0), None)
        with self.assertRaisesRegex(runtime.UnsupportedScriptError, 'entity variable service'):
            caller.context().member(B, 9)

    def test_failed_mode_component_cannot_fall_back_to_immutable_initial_values(self):
        world = make_world()
        world.bind(runtime.GAME_MODE_ENTITY)
        _, caller = component(world, A)
        _, mode = component(world, runtime.GAME_MODE_ENTITY, graph([node(0, 'UnknownAction')]))
        with self.assertRaises(runtime.UnsupportedScriptError):
            mode.run_node(mode.graph.node(0), None)
        with (mock.patch.object(runtime, 'mode_variables', return_value={9: 17}),
              self.assertRaisesRegex(runtime.UnsupportedScriptError, 'entity variable service')):
            caller.context().member(runtime.GAME_MODE_ENTITY, 9)


class SceneHostTests(unittest.TestCase):
    def host(self, models):
        return scene_host.SceneHost(make_world(), graph_loader=models.get)

    def test_all_components_are_bound_before_any_public_schema_is_evaluated(self):
        first, second = graph(), graph()
        first.index, second.index = 1, 2
        first.fields['m_publicSchema'] = schema(row(5, {'$': 'ReadOther'}))
        host = self.host({1: first, 2: second})
        a = host.add(A, definition(spec(1)))
        host.add(B, definition(spec(2)), values={9: 23})
        self.assertTrue(host.advance(20))
        self.assertEqual(a.instances[1].find_var(0, 5).value(), 23)
        self.assertEqual(a.now, 20 * 16 + 15)

    def test_duplicate_frames_do_not_run_again_and_gaps_require_a_new_epoch(self):
        model = graph([node(0, 'STUStatescriptEntry', m_outPlug=plug((1, 'm_inPlug'))),
                       setter(1, 17)], entries=[0])
        host = self.host({1: model})
        component = host.add(A, definition(spec(1)))
        self.assertTrue(host.advance(20))
        sequence = component.seq.next_value
        self.assertTrue(host.advance(20))
        self.assertEqual(component.seq.next_value, sequence)
        for bad in (19, 22, True, -1, 0x80000000):
            with self.subTest(frame=bad), self.assertRaises(ValueError):
                host.advance(bad)
        self.assertTrue(host.advance(21))

    def test_strict_failure_stops_the_scene_before_later_components_run(self):
        first = graph([node(0, 'STUStatescriptEntry', m_outPlug=plug((1, 'm_inPlug'))),
                       node(1, 'UnknownAction')], entries=[0])
        second = graph([node(0, 'STUStatescriptEntry', m_outPlug=plug((1, 'm_inPlug'))),
                        setter(1, 23)], entries=[0])
        first.index, second.index = 1, 2
        host = self.host({1: first, 2: second})
        host.add(A, definition(spec(1)))
        b = host.add(B, definition(spec(2)))
        self.assertFalse(host.advance(0))
        self.assertEqual(host.error['diagnostic']['class'], 'UnknownAction')
        self.assertIsNone(b.instances[1].find_var(0, 7, create=False))
        self.assertFalse(host.advance(1))

    def test_close_cancels_future_scheduling_and_retires_shared_world_bindings(self):
        host = self.host({1: graph()})
        component = host.add(A, definition(spec(1)))
        self.assertTrue(host.advance(0))
        host.close()
        host.close()
        self.assertFalse(host.advance(1))
        self.assertFalse(component.instances)
        self.assertFalse(host.world.entities)
        self.assertFalse(host.world.script_components)

    def test_catalog_is_sealed_after_initial_startup(self):
        host = self.host({1: graph()})
        host.add(A, definition(spec(1)))
        host.advance(0)
        with self.assertRaises(ValueError):
            host.add(B, definition(spec(1)))

    def test_startup_validation_errors_are_terminal_and_not_a_successful_empty_world(self):
        host = self.host({1: graph()})
        host.add(A, definition(spec(1), m_clientOnly=1))
        self.assertFalse(host.advance(0))
        self.assertIn('client-only', host.error['error'])
        self.assertFalse(host.prepared)


if __name__ == '__main__':
    unittest.main()
