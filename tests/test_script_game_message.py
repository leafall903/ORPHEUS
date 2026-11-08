"""Outgoing native message ordering, typed arrays and explicit transport limits."""

import unittest
from unittest import mock

from ow174.game.script import controller_heroes, expr, play, runtime, scene
from tests.test_script_runtime_budget import graph, node, plug, setter

OWNER, OTHER = 0xA0000100, 0xA0000101
MESSAGE = 0x02400000000006FF
KEY = 0x0A4000000000008D


def literal(value, label=None):
    return {'$': 'MessageLiteral', 'value': value, 'label': label}


def group(*values):
    return {'$': 'STU_06610EBC', 'm_configVars': list(values)}


def parameter(value, key=KEY):
    return {'m_B5051BCE': f'0x{key:016X}' if key is not None else None, 'm_value': value}


class RecordingWorld(scene.SceneWorld):
    def __init__(self):
        super().__init__()
        self.events, self.requests = [], []

    def native(self, cfg, instance):
        if cfg.get('$') == 'MessageLiteral':
            if cfg.get('label'):
                self.events.append(cfg['label'])
            value = cfg['value']
            return value() if callable(value) else value
        return super().native(cfg, instance)

    def game_message_owner(self, instance, node):
        self.events.append('owner')
        return super().game_message_owner(instance, node)

    def game_message_source(self, instance, node):
        self.events.append('source')
        return super().game_message_source(instance, node)

    def submit_game_message(self, instance, node, request):
        self.events.append('submit')
        self.requests.append(request)


def setup(**fields):
    world = RecordingWorld()
    world.bind(OWNER)
    component = runtime.Component(OWNER, world=world, strict=True)
    world.bind_script_component(OWNER, component)
    config = {'m_gameMessage': literal(expr.Asset(MESSAGE), 'message'),
              'm_targets': literal(expr.Entity(OWNER), 'target'), 'm_params': []}
    config.update(fields)
    model = graph([node(0, 'STUStatescriptActionSendGameMessage',
                        m_outPlug=plug((1, 'm_inPlug')), **config), setter(1, 31)])
    instance = component.create(model, init=False)
    instance.initialized = True
    return world, component, instance, model.node(0)


def execute(instance, action):
    instance.run_node(action, None)


class GameMessageTests(unittest.TestCase):
    def test_missing_failed_null_nonasset_and_zero_message_skip_all_later_inputs(self):
        for cfg in (None, literal(expr.EVALUATION_FAILED), literal(None), literal(17),
                    literal(expr.Entity(MESSAGE)), literal(expr.Asset(0))):
            world, _, instance, action = setup(m_gameMessage=cfg, m_targets={'$': 'Unknown'})
            execute(instance, action)
            self.assertFalse(world.events)
            self.assertFalse(world.requests)
            self.assertEqual(instance.find_var(0, 7).value(), 31)

    def test_message_uses_scalar_first_array_conversion(self):
        world, _, instance, action = setup(m_gameMessage=group(
            literal(((expr.Asset(MESSAGE),), expr.Asset(13))), {'$': 'Unknown'}))
        execute(instance, action)
        self.assertEqual(world.requests[0].message, MESSAGE)

    def test_targets_collect_all_groups_wrap_ids_preserve_duplicates_and_coalesce_self(self):
        values = group(literal((expr.Entity(OTHER), (None, expr.Entity(OWNER)), OTHER)),
                       group(literal(expr.Entity(OWNER)), literal(expr.Entity(OTHER+(1 << 32)))),
                       literal(expr.Entity(0xFFFFFFFF)))
        world, _, instance, action = setup(m_targets=values)
        execute(instance, action)
        request = world.requests[0]
        self.assertEqual(request.targets, (OTHER, OTHER))
        self.assertTrue(request.self_target)

    def test_no_usable_targets_skip_source_overrides_and_parameters_without_default_self(self):
        for targets in (None, literal(None), literal(()), literal((0, expr.Asset(OWNER),
                         expr.Entity(0), expr.Entity(0x1FFFFFFF)))):
            world, _, instance, action = setup(m_targets=targets, m_8B34E9C3={'$': 'Unknown'},
                                               m_params=[parameter({'$': 'Unknown'})])
            execute(instance, action)
            self.assertEqual(world.events, ['message', 'owner'])
            self.assertFalse(world.requests)

    def test_evaluation_order_is_message_owner_targets_source_override_then_params(self):
        world, _, instance, action = setup(m_8B34E9C3=literal(expr.Entity(OTHER), 'override'),
            m_params=[parameter(literal(0, 'param1')), parameter(literal(17, 'param2'), KEY+1)])
        execute(instance, action)
        self.assertEqual(world.events, ['message', 'owner', 'target', 'source', 'override',
                                       'param1', 'param2', 'submit'])
        self.assertEqual(world.requests[0].instigator, OTHER)

    def test_each_parameter_is_a_collected_array_including_empty_and_null_members(self):
        configs = [literal(0), literal(None), None, literal(expr.EVALUATION_FAILED),
                   literal(((1, None), (expr.Asset(7),))), group(literal(3), literal(5))]
        world, _, instance, action = setup(m_params=[parameter(cfg, KEY+i) for i, cfg in enumerate(configs)])
        execute(instance, action)
        self.assertEqual(world.requests[0].params, tuple(zip(
            range(KEY, KEY+6), ((0,), (), (), (), (1, None, expr.Asset(7)), (3, 5)), strict=True)))

    def test_parameter_rows_skip_null_rows_or_identifiers_before_evaluating_values(self):
        world, _, instance, action = setup(m_params=[None, parameter({'$': 'Unknown'}, None),
                                                     parameter(literal(11))])
        execute(instance, action)
        self.assertEqual(world.requests[0].params, ((KEY, (11,)),))

    def test_ordered_repeated_keys_survive_construction(self):
        world, _, instance, action = setup(m_params=[parameter(literal(3)), parameter(literal(5))])
        execute(instance, action)
        self.assertEqual(world.requests[0].params, ((KEY, (3,)), (KEY, (5,))))

    def test_unknown_or_malformed_inputs_fail_before_submission_and_success(self):
        for fields in ({'m_gameMessage': {'$': 'Unknown'}}, {'m_targets': {'$': 'Unknown'}},
                       {'m_params': [True]}, {'m_params': [{'m_B5051BCE': 'bad'}]},
                       {'m_params': [parameter({'$': 'Unknown'})]}):
            world, _, instance, action = setup(**fields)
            with self.assertRaises(runtime.UnsupportedScriptError):
                execute(instance, action)
            self.assertFalse(world.requests)
            self.assertIsNone(instance.find_var(0, 7, create=False))

    def test_source_is_read_after_target_callbacks_and_uses_existing_native_cache(self):
        world, _, instance, action = setup()

        def select():
            instance.set_source_entity(OTHER)
            return expr.Entity(OWNER)

        action.fields['m_targets'] = literal(select)
        execute(instance, action)
        self.assertEqual(world.requests[0].instigator, OTHER)
        instance.write((0, 0x1B), expr.Entity(OWNER))
        execute(instance, action)
        self.assertEqual(world.requests[1].instigator, OTHER)

    def test_failed_source_override_keeps_inherited_source_but_invalid_success_falls_back_to_owner(self):
        for value, expected in ((expr.EVALUATION_FAILED, OTHER), (None, OWNER),
                                (17, OWNER), (expr.Entity(0xFFFFFFFF), OWNER),
                                (((expr.Entity(OTHER+(1 << 32)),),), OTHER)):
            world, _, instance, action = setup(m_8B34E9C3=literal(value))
            instance.set_source_entity(OTHER)
            execute(instance, action)
            self.assertEqual(world.requests[0].instigator, expected)

    def test_written_zero_with_failure_does_not_override_the_inherited_message_source(self):
        cfg = {'$': controller_heroes.CLASS, 'm_controller': {'$': 'STU_B5A0CAF0'}}
        world, _, instance, action = setup(m_8B34E9C3=group(cfg, {'$': 'Unknown'}))
        instance.set_source_entity(OTHER)
        world.set_controller_hero_context(OWNER, controller_heroes.Context(OWNER, True, True,
            lookup=lambda identifier: controller_heroes.Controller(identifier), catalog=lambda:(),
            available=lambda guid, target:True, watch_environment=None))
        self.assertEqual(play.evaluate_scalar_result(instance, cfg, initial=expr.Asset(7)), (0, False))
        execute(instance, action)
        self.assertEqual(world.requests[0].instigator, OTHER)

    def test_scalar_status_keeps_successful_null_and_no_output_failure_distinct(self):
        _, _, instance, _ = setup()
        previous = expr.Asset(7)
        self.assertEqual(play.evaluate_scalar_result(instance, literal(None), initial=previous), (None, True))
        for cfg in (None, group(), group(None, {'$': 'Unknown'}), literal(expr.EVALUATION_FAILED)):
            self.assertEqual(play.evaluate_scalar_result(instance, cfg, initial=previous), (previous, False))

    def test_retirement_during_target_override_or_parameters_stops_following_evaluations(self):
        for field in ('m_targets', 'm_8B34E9C3', 'm_params'):
            world, component, instance, action = setup()

            def retire(world=world, component=component):
                world.unbind_script_component(OWNER, component)
                return expr.Entity(OWNER)

            if field == 'm_params':
                action.fields[field] = [parameter(literal(retire)), parameter({'$': 'Unknown'})]
            else:
                action.fields[field] = literal(retire)
            with self.assertRaisesRegex(runtime.UnsupportedScriptError, 'source lifecycle'):
                execute(instance, action)
            self.assertFalse(world.requests)
            self.assertEqual(component._operation_depth, 0)

    def test_request_iteration_uses_the_shared_budget_and_submits_nothing_after_exhaustion(self):
        targets = tuple(expr.Entity(OTHER) for _ in range(50))
        world, component, instance, action = setup(m_targets=literal(targets))
        with (mock.patch.object(runtime, 'MAX_SCRIPT_WORK', 12),
              self.assertRaises(runtime.ScriptBudgetError)):
            execute(instance, action)
        self.assertFalse(world.requests)
        self.assertIsNotNone(component.fault)

    def test_stale_instance_cannot_borrow_replacement_message_context(self):
        world, component, instance, action = setup()
        world.unbind_script_component(OWNER, component)
        replacement = runtime.Component(OWNER, world=world, strict=True)
        world.bind_script_component(OWNER, replacement)
        with self.assertRaises(runtime.UnsupportedScriptError):
            execute(instance, action)
        self.assertIs(world.script_components[OWNER], replacement)
        self.assertIsNone(replacement.fault)

    def test_unknown_owner_or_submission_service_stays_strict(self):
        for method in ('game_message_owner', 'submit_game_message'):
            world, _, instance, action = setup()
            setattr(world, method, getattr(runtime.World, method).__get__(world))
            with self.assertRaises(runtime.UnsupportedScriptError):
                execute(instance, action)
            self.assertIsNone(instance.find_var(0, 7, create=False))

    def test_scene_transport_preserves_distinct_instigator_and_repeated_parameter_assignments(self):
        world, component, instance, action = setup(m_8B34E9C3=literal(expr.Entity(OTHER)),
            m_params=[parameter(literal(1)), parameter(literal(2))])
        world.submit_game_message = scene.SceneWorld.submit_game_message.__get__(world)

        def variable(identifier):
            return {'$': 'STUConfigVarDynamic', 'm_identifier': identifier}

        receiver = component.create(graph([node(0, 'STUStatescriptEntryGameMessage',
            m_gameMessage=literal(expr.Asset(MESSAGE)), m_B60ABDA4=variable(41),
            m_FF703F20=variable(42), m_params=[{'m_B5051BCE': f'0x{KEY:016X}',
                                              'm_out_Var': variable(43)}])], entries=[0]), init=False)
        receiver.initialized = True
        execute(instance, action)
        self.assertEqual([event.assignment for event in receiver.queue[:-1]], [
            (0, 43, (1,)), (0, 43, (2,)), (0, 41, expr.Entity(OWNER)), (0, 42, expr.Entity(OTHER))])
        component.run_now()
        self.assertEqual(receiver.find_var(0, 43).value(), (2,))
        self.assertEqual(receiver.find_var(0, 41).value(), expr.Entity(OWNER))
        self.assertEqual(receiver.find_var(0, 42).value(), expr.Entity(OTHER))


if __name__ == '__main__':
    unittest.main()
