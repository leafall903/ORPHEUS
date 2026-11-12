"""Native request collection and observable handoff to a required world service."""

import unittest
from unittest import mock

from ow174.game.script import expr, play, runtime, scene
from tests.public.test_script_play_loaded import KEY, definition, dynamic
from tests.public.test_script_play_resource import EMPTY, array, inline, integer, resource, row
from tests.script_fixtures import graph, node, plug, setter

CONTEXT = play.InvocationContext(caller=123, owner=456, instigator=789)
OWNER = {'$': 'STU_B5A0CAF0'}


def parameter(value, identifier=KEY):
    return {'m_B5051BCE': f'0x{identifier:016X}', 'm_value': value}


def requesting(*, world=None, **fields):
    action = node(0, 'STUStatescriptActionPlayScript', m_script=resource(),
                  m_targets=OWNER, m_outPlug=plug((1, 'm_inPlug')))
    action['fields'].update(fields)
    model = graph([action, setter(1, 99)])
    component = runtime.Component(123, world=world, strict=True)
    return component.create(model, init=False), model.node(0)


def resolve(caller, action, context=CONTEXT):
    selected = play.resolve_resource(caller, action)
    return play.resolve_request(caller, action, selected, context_provider=lambda: context)


class PlayRequestTests(unittest.TestCase):
    def test_authored_map_preserves_nested_values_and_replaces_duplicate_keys(self):
        caller, _ = requesting()
        values = {'$': 'STU_45B0D2B2', 'm_configVars': [integer(4), None, integer(7)]}
        inner = {'$': 'STU_7042F028', 'm_92829780': [
            {'m_identifier': '0x0D80000000000004', 'm_value': values},
        ]}
        cfg = {'$': 'STU_7042F028', 'm_92829780': [
            {'m_identifier': '0x0D80000000000002', 'm_value': integer(3)},
            {'m_identifier': '0x0D80000000000001', 'm_value': inner},
            {'m_identifier': '0x0D80000000000002', 'm_value': integer(8)},
        ]}
        self.assertEqual(dict(play.evaluate_value(caller, cfg).items),
                         {1: expr.Map(((4, (4, 0, 7)),)), 2: 8})
        self.assertEqual(play.evaluate_scalar_result(caller, values), (4, True))

    def test_mode_score_thresholds_scalar_and_collection_use_the_difficulty_mask(self):
        world = scene.SceneWorld(mode=3)
        difficulty = 0x0D8000000000195E
        world.mode_score_thresholds = {3: ((3000, 6000, 10000, 15000, 20000), {difficulty: 3})}
        caller, _ = requesting(world=world)
        cfg = {'$': 'STU_0C37A184', 'm_5B9DD0CB': dynamic(7)}
        caller.write((0, 7), expr.Asset(difficulty))
        expected = (3000.0, 6000.0, 10000.0, -1.0, -1.0)
        self.assertEqual(play.evaluate_value(caller, cfg), expected)
        self.assertEqual(play.evaluate_value(caller, array(cfg)), expected)
        self.assertEqual(play.evaluate_scalar_result(caller, cfg), (3000.0, True))
        caller.write((0, 7), 0)
        self.assertEqual(play.evaluate_value(caller, cfg)[-2:], (15000.0, 20000.0))
        world.mode_score_thresholds = {}
        self.assertEqual(play.evaluate_value(caller, cfg), ())
        self.assertEqual(play.evaluate_scalar_result(caller, cfg), (0, False))

    def test_minimum_config_defaults_for_an_empty_iterator(self):
        caller, _ = requesting()
        cfg = {'$': 'STU_17330930', 'm_A53100D5': integer(2),
               'm_814F8EC3': array(EMPTY, dynamic(7))}
        for values, expected in ((None, 2.0), ((9, (7, 8)), 7.0), ((0, 7), 0.0),
                                 ((expr.Asset(1), 7), 2.0), ((None, 7), 0.0)):
            with self.subTest(values=values):
                caller.write((0, 7), values)
                result = caller.evaluate(cfg)
                self.assertIs(type(result), float)
                self.assertEqual(result, expected)

    def test_targets_flatten_typed_arrays_preserve_duplicates_and_reject_non_entities(self):
        caller, action = requesting(m_targets=array(dynamic(7), OWNER, None, EMPTY))
        caller.write((0, 7), (expr.Entity(7), (expr.Entity(8), expr.Entity(7)),
                              None, 9, expr.Asset(10), expr.Entity(0), expr.Entity(0x1FFFFFFF)))
        request = resolve(caller, action)
        self.assertEqual(request.targets, (7, 8, 7, 123))
        self.assertEqual(request.context, CONTEXT)
        self.assertEqual((request.policy, request.wire_flag), (0, 1))

    def test_empty_targets_skip_context_instigator_parameters_and_submit_but_follow_output(self):
        caller, action = requesting(m_targets=array(EMPTY, integer(7)),
                                    m_6D7701B7={'$': 'Never'}, m_params=[parameter({'$': 'Never'})])
        with (mock.patch.object(caller.component.world, 'play_script_context',
                                side_effect=AssertionError('context evaluated')),
              mock.patch.object(caller.component.world, 'submit_script_request',
                                side_effect=AssertionError('request submitted'))):
            caller.run_node(action, None)
        self.assertEqual(caller.find_var(0, 7).value(), 99)
        self.assertIsNone(caller.component.fault)

    def test_instigator_scalar_failure_inherits_but_successful_invalid_falls_back_to_caller(self):
        for config, expected in ((None, 789), (EMPTY, 789), (integer(789), 123),
                                 (dynamic(7), 123), (array(EMPTY, OWNER), 789),
                                 (array(None, OWNER), 789), (array(OWNER, EMPTY), 123)):
            with self.subTest(config=config):
                caller, action = requesting(m_6D7701B7=config)
                self.assertEqual(resolve(caller, action).context.instigator, expected)
        caller, action = requesting(m_6D7701B7=dynamic(7))
        caller.write((0, 7), (expr.Entity(999), expr.Entity(888)))
        self.assertEqual(resolve(caller, action).context.instigator, 999)

    def test_invalid_inherited_instigator_uses_exact_caller_including_invalid_caller(self):
        for source in (0, 0x1FFFFFFF):
            caller, action = requesting()
            context = play.InvocationContext(0x1FFFFFFF, 456, source)
            self.assertEqual(resolve(caller, action, context).context.instigator, context.caller)

    def test_parameters_always_collect_arrays_keep_duplicate_keys_and_skip_missing_identifier(self):
        caller, action = requesting(m_params=[None, {'m_B5051BCE': None, 'm_value': {'$': 'Never'}},
                                              parameter(integer(17)), parameter(EMPTY),
                                              parameter(array(integer(1), array(integer(2), None)), KEY + 1),
                                              parameter(None, 0)])
        request = resolve(caller, action)
        self.assertEqual(request.params, ((KEY, (17,)), (KEY, ()), (KEY + 1, (1, 2)), (0, ())))

    def test_scalar_null_is_omitted_but_array_null_members_are_preserved(self):
        caller, action = requesting(m_params=[parameter(array(dynamic(7), dynamic(8)))])
        caller.write((0, 7), None)
        caller.write((0, 8), (None, 7))
        self.assertEqual(resolve(caller, action).params, ((KEY, (None, 7)),))

    def test_evaluation_order_is_resource_targets_context_instigator_then_parameter_arrays(self):
        seen = []

        class Backend(runtime.World):
            def native(self, cfg, instance):
                seen.append(cfg['$'])
                return {'Target': expr.Entity(123), 'Source': expr.Entity(789),
                        'Parameter': 17, 'Override': 88}[cfg['$']]

            def play_script_context(self, caller, action):
                seen.append('Context')
                return CONTEXT

            def submit_script_request(self, caller, action, request):
                seen.append('Submit')
                self.request = request
                self.before_output = caller.find_var(0, 7, create=False)

        world = Backend()
        caller, action = requesting(world=world, m_69F4B52C=inline([row(7, {'$': 'Override'})]),
                                    m_targets={'$': 'Target'}, m_6D7701B7={'$': 'Source'},
                                    m_params=[parameter({'$': 'Parameter'})])
        caller.run_node(action, None)
        self.assertEqual(seen, ['Override', 'Target', 'Context', 'Source', 'Parameter', 'Submit'])
        self.assertEqual(world.request.resource.overrides, ((play.IDENTIFIER_TYPE | 7, 88),))
        self.assertEqual(world.request.params, ((KEY, (17,)),))
        self.assertIsNone(world.before_output)
        self.assertEqual(caller.find_var(0, 7).value(), 99)

    def test_query_bindings_resolve_targets_but_do_not_grant_context_or_transport(self):
        world = scene.SceneWorld()
        world.bind(123)
        caller, action = requesting(world=world)
        with self.assertRaisesRegex(runtime.UnsupportedScriptError, 'PlayScript source lifecycle'):
            caller.run_node(action, None)
        self.assertNotIn(7, caller.vars)
        caller, action = requesting()
        with (mock.patch.object(caller.component.world, 'play_script_context', return_value=CONTEXT),
              self.assertRaisesRegex(runtime.UnsupportedScriptError, 'native PlayScript request')):
            caller.run_node(action, None)
        self.assertNotIn(7, caller.vars)

    def test_explicit_submitter_can_feed_collected_parameters_to_loaded_start_entries(self):
        model = definition()
        model.index = 2

        class Backend(runtime.World):
            def play_script_context(self, caller, action):
                return CONTEXT

            def submit_script_request(self, caller, action, request):
                # This test backend owns this loaded, local component only.
                assert request.targets == (caller.component.entity,) and request.wire_flag == 0
                self.started = caller.component.play_loaded_graph(
                    model, source=request.context.caller, params=request.params,
                    overrides=request.resource.overrides, policy=request.policy,
                )

        world = Backend()
        caller, action = requesting(world=world, m_params=[parameter(integer(17))], m_7ABA409C=0)
        caller.initialized = True
        caller.run_node(action, None)
        with mock.patch.dict(runtime.HANDLERS, AuditStart=lambda *_: True):
            caller.component.run_now()
        self.assertEqual(world.started.find_var(0, 41).value(), (17,))
        self.assertEqual(caller.find_var(0, 7).value(), 99)
        self.assertIsNone(world.started.parent)

    def test_invalid_context_rows_flags_and_unknown_queries_stop_strict_callers(self):
        cases = [{'m_params': [7]}, {'m_params': [parameter(integer(1)) | {'m_B5051BCE': 'bad'}]},
                 {'m_AA4DE426': 4}, {'m_AA4DE426': True}, {'m_7ABA409C': -1},
                 {'m_targets': {'$': 'UnknownTarget'}}, {'m_6D7701B7': {'$': 'UnknownSource'}},
                 {'m_params': [parameter({'$': 'UnknownParameter'})]}]
        for fields in cases:
            caller, action = requesting(**fields)
            with self.subTest(fields=fields), self.assertRaises(runtime.UnsupportedScriptError):
                resolve(caller, action)
            self.assertIsNotNone(caller.component.fault)
        for context in (None, play.InvocationContext(True, 1, 2), play.InvocationContext(1, -1, 2)):
            caller, action = requesting()
            with self.subTest(context=context), self.assertRaises(runtime.UnsupportedScriptError):
                resolve(caller, action, context)

    def test_parameter_collection_and_scalar_config_depth_share_work_limits(self):
        caller, action = requesting(m_params=[parameter(integer(i)) for i in range(100)])
        with mock.patch.object(runtime, 'MAX_SCRIPT_WORK', 20), self.assertRaises(runtime.ScriptBudgetError):
            resolve(caller, action)
        self.assertIsNotNone(caller.component.fault)
        config = OWNER
        for _ in range(play.MAX_CONFIG_DEPTH + 1):
            config = array(config)
        caller, action = requesting(m_6D7701B7=config)
        with self.assertRaises(runtime.ScriptBudgetError):
            resolve(caller, action)
        self.assertIsNotNone(caller.component.fault)


if __name__ == '__main__':
    unittest.main()
