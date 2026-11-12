"""DB2C10 typed tag-token collection, lifecycle and actual224E resource input."""

import unittest
from unittest.mock import patch

from ow174.game.script import entity_filter, expr, runtime
from ow174.game.script import graph as native_graph
from tests.runtime.test_script_runtime_budget import graph, node


class InputWorld(runtime.World):
    def native(self, cfg, instance):
        if cfg.get('$') == 'ProbeValue':
            return cfg['value']
        return super().native(cfg, instance)


class EntityFilterCollectTests(unittest.TestCase):
    def scene(self, world=None):
        component = runtime.Component(123, strict=True, world=world or InputWorld())
        instance = component.create(graph([node(0, 'STUStatescriptState', state=0)]), init=False)
        instance.initialized = True
        return component, instance

    def test_ordered_group_collection_ignores_nonassets_zero_and_duplicates(self):
        _, instance = self.scene()
        cfg = {'$': 'STU_06610EBC', 'm_configVars': [
            {'$': 'ProbeValue', 'value': (None, True, 7, 1.5, expr.Entity(7), expr.Asset(0),
                                        expr.Asset(7), (expr.Asset(8), expr.Asset(7)))},
            {'$': 'ProbeValue', 'value': expr.Asset(9)}]}
        self.assertEqual(entity_filter.collect_tags(instance, cfg), (7, 8, 9))

    def test_deduplication_uses_all64_bits_before_low32_membership(self):
        _, instance = self.scene()
        cfg = {'$': 'ProbeValue', 'value': (expr.Asset(7), expr.Asset((3 << 32) | 7),
                                          expr.Asset(-1), expr.Asset(0xFFFFFFFFFFFFFFFF))}
        self.assertEqual(entity_filter.collect_tags(instance, cfg), (7, (3 << 32) | 7, 0xFFFFFFFFFFFFFFFF))

    def test_missing_null_and_known_failed_providers_produce_no_tokens(self):
        _, instance = self.scene()
        for cfg in (None, {'$': 'ProbeValue', 'value': None},
                    {'$': 'ProbeValue', 'value': expr.EVALUATION_FAILED}):
            with self.subTest(cfg=cfg):
                self.assertEqual(entity_filter.collect_tags(instance, cfg), ())

    def test_real_watcher_receives_dynamic_variable_subscription(self):
        _, instance = self.scene()
        instance.write((0, 41), (expr.Asset(7), expr.Asset(8)))
        state = instance.state(0)
        state.begin()
        instance.component.run_now()
        cfg = {'$': 'STUConfigVarDynamic', 'm_identifier': 41, 'm_60DB8F99': 0}
        self.assertEqual(entity_filter.collect_tags(instance, cfg, watcher=state), (7, 8))
        self.assertIn(state, instance.find_var(0, 41).watchers)
        state.end(False)
        self.assertNotIn(state, instance.find_var(0, 41).watchers)

    def test_provider_retirement_never_returns_success_even_with_no_yielded_value(self):
        for value in (None, expr.Asset(7)):
            with self.subTest(value=value):
                class RetiringWorld(InputWorld):
                    reply = value

                    def native(self, cfg, instance):
                        instance.component.destroy(instance)
                        return self.reply

                component, instance = self.scene(RetiringWorld())
                with self.assertRaisesRegex(runtime.UnsupportedScriptError, 'owner retired during callback'):
                    entity_filter.collect_tags(instance, {'$': 'ProbeValue'})
                self.assertEqual(component._operation_depth, 0)

    def test_repeated_duplicate_input_still_consumes_shared_work_budget(self):
        component, instance = self.scene()
        cfg = {'$': 'ProbeValue', 'value': (expr.Asset(7),) * 1000}
        with patch.object(runtime, 'MAX_SCRIPT_WORK', 20), self.assertRaises(runtime.ScriptBudgetError):
            entity_filter.collect_tags(instance, cfg)
        self.assertIsNotNone(component.fault)
        self.assertEqual(component._operation_depth, 0)

    def test_empty_retiring_group_child_does_not_evaluate_later_sibling(self):
        for reply in (None, expr.EVALUATION_FAILED):
            with self.subTest(reply=reply):
                class RetiringWorld(InputWorld):
                    first_reply = reply

                    def __init__(self):
                        super().__init__()
                        self.calls = []

                    def native(self, cfg, instance):
                        self.calls.append(cfg['name'])
                        if cfg['name'] == 'first':
                            instance.component.destroy(instance)
                            return self.first_reply
                        return expr.Asset(2)

                world = RetiringWorld()
                component, instance = self.scene(world)
                cfg = {'$': 'STU_06610EBC', 'm_configVars': [
                    {'$': 'ProbeValue', 'name': 'first'}, {'$': 'ProbeValue', 'name': 'second'}]}
                with self.assertRaisesRegex(runtime.UnsupportedScriptError, 'owner retired during callback'):
                    entity_filter.collect_tags(instance, cfg)
                self.assertEqual(world.calls, ['first'])
                self.assertEqual(component._operation_depth, 0)

    def test_actual224e_exclusion_config_collects_all_three_authored_tag_resources(self):
        component, _ = self.scene()
        instance = component.create(native_graph.graph(0x224E), init=False)
        instance.initialized = True  # selected config only, not full native effect host
        cfg = instance.graph.node(9).field('m_condition')['m_filter']
        self.assertEqual((cfg['m_84065FD1'], cfg['m_4EA686DB']), (64, 20))
        self.assertEqual(entity_filter.collect_tags(instance, cfg['m_8B2A4DF3']),
                         (0x0D600000000002CB, 0x0D6000000000003A, 0x0D6000000000093D))
