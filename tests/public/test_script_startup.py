"""Fresh component schemas and placed overrides must precede authored Entries."""

import unittest
from unittest import mock

from ow174.game.script import expr, runtime, startup
from tests.runtime.test_script_runtime_budget import graph, node, plug


def value(number):
    return {'$': 'STUConfigVarInt', 'm_value': number}


def row(identifier, config):
    return {'$': 'STUStatescriptSchemaEntry', 'm_0D09D2D9': f'0x{0x0D80000000000000 | identifier:016X}',
            'm_value': config}


def spec(index, *entries):
    return {'$': 'STUStatescriptGraphWithOverrides',
            'm_graph': f'0x{0x0580000000000000 | index:016X}' if index else None,
            'm_1EB5A024': list(entries), 'm_75408640': 0}


def schema(*entries):
    return {'$': 'STUStatescriptSchema', 'm_entries': list(entries)}


def definition(*graphs, **fields):
    return {'$': 'STUStatescriptComponent', 'm_B634821A': list(graphs), **fields}


def placed(*overrides, extra=(), **fields):
    return {'$': 'STUStatescriptComponentInstanceData', 'm_6D10093E': list(overrides),
            'm_84D0983B': list(extra), **fields}


def model(index=1, *entries):
    result = graph([node(0, 'STUStatescriptEntry', m_outPlug=plug((1, 'm_inPlug'))),
                    node(1, 'ObserveComponentStart')], entries=[0])
    result.index = index
    result.fields['m_publicSchema'] = schema(*entries)
    return result


class StartupTests(unittest.TestCase):
    def boot(self, defaults, instance=None, *, models=None, world=None):
        models = models or {1: model()}
        component = runtime.Component(0x800001A8, world=world, strict=True)
        boot = startup.ComponentStartup(component, defaults, instance, graph_loader=models.get)
        return component, boot

    def observe(self, component):
        seen = []

        def record(instance, *_):
            seen.append((instance.id, instance.find_var(0, 7).value(), instance.find_var(0, 8).value()))
            return True

        with mock.patch.dict(runtime.HANDLERS, ObserveComponentStart=record):
            component.run_now()
        return seen

    def test_public_definition_and_placed_values_are_ready_when_the_entry_runs(self):
        component, boot = self.boot(definition(spec(1, row(7, value(20)))),
                                    placed(spec(1, row(7, value(30)))),
                                    models={1: model(1, row(7, value(10)))})
        component.now = 37
        instance, = boot.start()
        self.assertEqual([event.kind for event in instance.queue], [runtime.ASSIGN] * 3)
        self.assertTrue(instance.initialization_pending)
        self.assertFalse(instance.initialized)
        self.assertTrue(all(event.time == 37 for event in instance.queue))
        self.assertIsNone(instance.find_var(0, 7).value())
        self.assertEqual(self.observe(component), [(1, 30, None)])
        self.assertFalse(instance.initialization_pending)

    def test_schema_reads_the_pre_assignment_callee_values_and_owner(self):
        dynamic = {'$': 'STUConfigVarDynamic', 'm_identifier': 7, 'm_60DB8F99': 0}
        component, boot = self.boot(definition(spec(1, row(8, dynamic),
                                                        row(9, {'$': 'STU_B5A0CAF0'}))),
                                    models={1: model(1, row(7, value(10)))})
        instance, = boot.start()
        self.assertEqual(self.observe(component), [(1, 10, None)])
        self.assertEqual(instance.find_var(0, 9).value(), expr.Entity(component.entity))

    def test_public_schema_accepts_its_own_graph_reference(self):
        for index in (1, 2):
            with self.subTest(index=index):
                source = model(1, row(7, value(10)))
                source.fields['m_publicSchema'].update(
                    {'$': 'STU_77990ED7', 'm_89EAAC40': spec(index)['m_graph']},
                )
                component, boot = self.boot(definition(spec(1, row(7, value(20)))), models={1: source})
                if index == 1:
                    boot.start()
                    self.assertEqual(self.observe(component), [(1, 20, None)])
                else:
                    with self.assertRaisesRegex(ValueError, 'unsupported component startup schema'):
                        boot.start()
                    self.assertFalse(component.instances)

    def test_mid_frame_component_entries_observe_queued_startup_values(self):
        component, boot = self.boot(definition(spec(1)), models={1: model(1, row(7, value(200)))})
        component.frame = 20
        component.now = 20 * component.frame_ms + 4
        instance, = boot.start()
        seen = []

        def record(instance, *_):
            seen.append((component.now, instance.find_var(runtime.INSTANCE, 7).value()))
            return True

        with mock.patch.dict(runtime.HANDLERS, ObserveComponentStart=record):
            component.run_frame(20)
        self.assertEqual(seen, [(324, 200)])
        self.assertEqual(instance.start, 324)
        self.assertEqual(component.now, 335)

    def test_empty_override_preserves_definition_values(self):
        component, boot = self.boot(definition(spec(1, row(7, value(99)))), placed(spec(1)))
        boot.start()
        self.assertEqual(self.observe(component), [(1, 99, None)])

    def test_component_float_schemas_write_entity_values_before_ordinary_entries(self):
        key = 0x1A55

        def config(number):
            return {'$': 'STUConfigVarFloat', 'm_value': number}

        dynamic = {'$': 'STUConfigVarDynamic', 'm_identifier': key, 'm_60DB8F99': 1}
        component, boot = self.boot(
            definition(spec(1), m_2D9815BA=schema(row(key, config(0.85)))),
            placed(m_2D9815BA=schema(row(key, config(0.5)))),
            models={1: model(1, row(7, dynamic))},
        )
        instance, = boot.start()
        self.assertIsNone(component.entity_var(key, create=False))
        seen = []
        with mock.patch.dict(runtime.HANDLERS, ObserveComponentStart=lambda *_: (
            seen.append(component.entity_var(key).value()) or True
        )):
            component.run_now()
        self.assertEqual(seen, [0.5])
        self.assertIsNone(instance.find_var(runtime.INSTANCE, 7).value())
        self.assertFalse(component.startup_entity_values)

    def test_duplicate_graph_overrides_only_the_first_instance_without_reapplying_defaults(self):
        component, boot = self.boot(definition(spec(1, row(7, value(10))), spec(1, row(7, value(20)))),
                                    placed(spec(1, row(7, value(30))), spec(1, row(8, value(40)))))
        boot.start()
        self.assertEqual(self.observe(component), [(1, 30, 40), (2, 20, None)])

    def test_unmatched_override_does_not_create_or_evaluate_and_added_graph_comes_last(self):
        component, boot = self.boot(definition(spec(1, row(7, value(10)))),
                                    placed(spec(2, row(7, {'$': 'UnknownUnusedConfig'})),
                                           extra=[spec(2, row(7, value(20)))]),
                                    models={1: model(1), 2: model(2)})
        instances = boot.start()
        self.assertEqual([instance.graph.index for instance in instances], [1, 2])
        self.assertEqual(self.observe(component), [(1, 10, None), (2, 20, None)])

    def test_zero_graph_does_not_evaluate_its_rows_or_consume_an_instance_id(self):
        component, boot = self.boot(definition(spec(0, row(7, {'$': 'UnknownUnusedConfig'})), spec(1)))
        instances = boot.start()
        self.assertEqual([instance.id for instance in instances], [1])
        self.assertEqual(self.observe(component), [(1, None, None)])

    def test_missing_identifier_and_config_are_skipped_before_evaluation(self):
        component, boot = self.boot(definition(spec(1,
            {'m_0D09D2D9': None, 'm_value': {'$': 'UnknownUnusedConfig'}}, row(7, None),
            row(0, value(50)))))
        instance, = boot.start()
        self.observe(component)
        # A nonzero full identifier whose low16 are zero still writes variable0.
        self.assertEqual(instance.find_var(0, 0).value(), 50)
        self.assertIsNone(instance.find_var(0, 7).value())

    def test_failed_value_keeps_the_previous_queued_default_and_successful_null_clears_it(self):
        class FailedWorld(runtime.World):
            def native(self, cfg, instance):
                if cfg['$'] == 'FailedConfig':
                    return expr.EVALUATION_FAILED
                if cfg['$'] == 'SuccessfulNullConfig':
                    return None
                return super().native(cfg, instance)

        for config, expected in (({'$': 'FailedConfig'}, 10), ({'$': 'SuccessfulNullConfig'}, None)):
            with self.subTest(config=config):
                component, boot = self.boot(definition(spec(1, row(7, config))),
                    models={1: model(1, row(7, value(10)))}, world=FailedWorld())
                boot.start()
                self.assertEqual(self.observe(component), [(1, expected, None)])

    def test_unknown_used_config_faults_the_component_before_any_entry_runs(self):
        component, boot = self.boot(definition(spec(1), spec(1, row(7, {'$': 'UnknownUsedConfig'}))))
        with self.assertRaises(runtime.UnsupportedScriptError):
            boot.start()
        self.assertIsNone(boot.created)
        self.assertTrue(all(item.stopped and not item.queue for item in component.instances.values()))
        with self.assertRaises(runtime.UnsupportedScriptError):
            boot.start()

    def test_second_start_does_not_replay_schema_or_entries(self):
        component, boot = self.boot(definition(spec(1, row(7, value(10)))))
        instances = boot.start()
        self.observe(component)
        instances[0].write((0, 7), 99)
        self.assertIs(boot.start(), instances)
        self.assertFalse(instances[0].queue)
        self.assertEqual(instances[0].find_var(0, 7).value(), 99)

    def test_delayed_first_frame_initializes_at_that_frames_start_not_allocation_time(self):
        component, boot = self.boot(definition(spec(1)))
        instance, = boot.start()
        observed = []
        with mock.patch.dict(runtime.HANDLERS,
                             ObserveComponentStart=lambda *_: observed.append(component.now)):
            component.run_frame(17)
        self.assertEqual(instance.start, 272)
        self.assertEqual(observed, [272])

    def test_all_pending_instances_initialize_before_the_first_authored_callback(self):
        component, boot = self.boot(definition(spec(1), spec(1)))
        instances = boot.start()
        seen = []
        with mock.patch.dict(runtime.HANDLERS, ObserveComponentStart=lambda *_: seen.append(
                [item.initialized and not item.initialization_pending for item in instances])):
            component.run_now()
        self.assertEqual(seen, [[True, True], [True, True]])

    def test_destroying_pending_instance_discards_its_startup_work(self):
        component, boot = self.boot(definition(spec(1, row(7, value(10)))))
        instance, = boot.start()
        component.destroy(instance)
        self.assertFalse(instance.initialization_pending)
        self.assertFalse(instance.queue)
        component.run_now()
        self.assertFalse(instance.initialized)

    def test_manual_uninitialized_instance_keeps_its_explicit_policy(self):
        component = runtime.Component(123, strict=True)
        instance = component.create(model(), init=False)
        component.run_frame(4)
        self.assertFalse(instance.initialized)
        self.assertFalse(instance.initialization_pending)

    def test_deferred_scope_frame_prepares_entries_before_choosing_the_first_due_time(self):
        component, boot = self.boot(definition(spec(1)))
        instance, = boot.start()
        phases = []
        with mock.patch.dict(runtime.HANDLERS, ObserveComponentStart=lambda *_: phases.append(component.now)):
            component.run_frame(3, end_phase=lambda: phases.append(('end', component.now)))
        self.assertEqual(instance.start, 48)
        self.assertEqual(phases, [48, ('end', 63)])

    def test_existing_instances_are_not_replaced_by_startup(self):
        component, boot = self.boot(definition(spec(1)))
        existing = component.create(model(), values={(0, 7): 99}, init=False)
        with self.assertRaises(ValueError):
            boot.start()
        self.assertEqual(list(component.instances.values()), [existing])
        self.assertEqual(existing.find_var(0, 7).value(), 99)

    def test_missing_later_resource_is_rejected_before_allocating_the_first(self):
        component, boot = self.boot(definition(spec(1), spec(2)))
        with self.assertRaisesRegex(ValueError, 'not loaded'):
            boot.start()
        self.assertFalse(component.instances)
        self.assertIsNone(component.fault)

    def test_unknown_flags_and_component_schemas_are_rejected_before_allocation(self):
        flagged = spec(1)
        flagged['m_75408640'] = 1
        for defaults, instance in (
            (definition(flagged), None), (definition(spec(1), m_clientOnly=1), None),
            (definition(spec(1), m_2D9815BA=schema(row(7, value(1)))), None),
            (definition(spec(1)), placed(m_2D9815BA=schema(row(7, value(1))))),
        ):
            with self.subTest(defaults=defaults, instance=instance):
                component, boot = self.boot(defaults, instance)
                with self.assertRaises(ValueError):
                    boot.start()
                self.assertFalse(component.instances)

    def test_resource_work_and_schema_work_share_one_bounded_startup_operation(self):
        for graphs in ([spec(1) for _ in range(20)],
                       [spec(1, *(row(7, value(number)) for number in range(20)))]):
            with self.subTest(graphs=len(graphs)):
                component, boot = self.boot(definition(*graphs))
                with (mock.patch.object(runtime, 'MAX_SCRIPT_WORK', 10),
                      self.assertRaises(runtime.ScriptBudgetError)):
                    boot.start()
                self.assertIsNotNone(component.fault)
                self.assertIsNone(boot.created)
                self.assertTrue(all(item.stopped and not item.queue for item in component.instances.values()))


if __name__ == '__main__':
    unittest.main()
