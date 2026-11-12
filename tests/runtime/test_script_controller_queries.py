"""Component43 relation and selected-controller predicate; native36-case contract."""
import unittest

from test_game_graph_analysis import make_graph, node, output

from ow174.game.script import controller_queries as query
from ow174.game.script import expr, runtime, scene

BODY, CONTROLLER, OTHER = 0xA0000101, 0xA0000100, 0xA0000102
BODY_VALUE = expr.Entity(BODY)


def record(entity, components, related=None):
    return query.Record(entity, frozenset(components), related)


def config(cls=query.PREDICATE, entity=BODY_VALUE):
    return {'$': cls, 'm_801B298F' if cls == query.RELATED else 'm_entity': entity}


class QueryWorld(runtime.World):
    def __init__(self, records):
        super().__init__()
        self.records = records
        self.calls = []
        self.callback = None

    def controller_record(self, entity):
        self.calls.append(entity)
        if self.callback is not None:
            self.callback()
        return self.records.get(entity)


class ControllerQueryTests(unittest.TestCase):
    def test_scene_record_tracks_entity_binding_lifetime(self):
        world = scene.SceneWorld()
        world.bind(BODY)
        self.assertIs(world.controller_record(BODY), NotImplemented)
        world.set_controller_record(record(BODY, (43,), CONTROLLER))
        component = runtime.Component(BODY, world=world, strict=True)
        instance = component.create(make_graph(0xF003))
        self.assertEqual(instance.evaluate(config(query.RELATED)), expr.Entity(CONTROLLER))
        world.unbind(BODY)
        self.assertIsNone(world.controller_record(BODY))
        world.bind(BODY)
        self.assertIs(world.controller_record(BODY), NotImplemented)

    def test_scene_model_gate_distinguishes_setter_from_component_absence(self):
        for cls, fields in (
            ("STU_EEA6FD79", {"m_4F64C1D3": BODY_VALUE, "m_40B6706F": False}),
            ("STU_680F6816", {"m_entity": BODY_VALUE}),
        ):
            with self.subTest(cls=cls):
                definition = make_graph(0xF003, [node(0, cls, fields=fields)])
                world = scene.SceneWorld()
                world.bind(BODY)
                calls = []
                world.set_model_gate_consumer(BODY, calls.append)
                instance = runtime.Component(BODY, world=world, strict=True).create(definition)
                self.assertTrue(instance.run_node(definition.node(0), None))
                self.assertEqual(calls, [False])
                world.set_model_gate_consumer(BODY, None)
                self.assertTrue(instance.run_node(definition.node(0), None))
                self.assertEqual(calls, [False])
                world.unbind(BODY)
                world.bind(BODY)
                with self.assertRaisesRegex(runtime.UnsupportedScriptError, "component1 model gate"):
                    instance.run_node(definition.node(0), None)

    def setup(self, records=None):
        world = QueryWorld({} if records is None else records)
        component = runtime.Component(BODY, world=world, strict=True)
        instance = component.create(make_graph(0xF002))
        return world, component, instance

    def test_related_and_controller_tail_native_matrix(self):
        # Literal outcomes from original D15520/DA6910/CC9F61 executions.
        cases = (
            ((43,), (44, 75), CONTROLLER, CONTROLLER, True),
            ((43,), (44, 75, 92), CONTROLLER, CONTROLLER, False),
            ((43,), (44,), CONTROLLER, CONTROLLER, False),
            ((43,), (75,), CONTROLLER, CONTROLLER, False),
            ((43,), None, OTHER, OTHER, False),
            ((43,), None, 0, 0, False),
            ((44, 75), None, None, 0, True),
            ((43, 44, 75), (44, 75, 92), CONTROLLER, CONTROLLER, False),
            ((43, 44, 75), None, CONTROLLER, CONTROLLER, False),
            ((), None, None, 0, False),
        )
        for kinds, related_kinds, link, related_result, predicate in cases:
            with self.subTest(body=kinds, related=related_kinds, link=link):
                records = {BODY: record(BODY, kinds, link)}
                if related_kinds is not None:
                    records[CONTROLLER] = record(CONTROLLER, related_kinds)
                _, _, instance = self.setup(records)
                self.assertEqual(instance.evaluate(config(query.RELATED)), expr.Entity(related_result))
                self.assertIs(instance.evaluate(config()), predicate)

    def test_missing_and_invalid_source_preserve_related_destination_but_predicate_false(self):
        world, _, instance = self.setup()
        sentinel = object()
        for value in (None, 5, (), expr.Entity(0), expr.Entity(0xFFFFFFFF), expr.Entity(OTHER)):
            with self.subTest(value=value):
                self.assertIs(instance.evaluate(config(query.RELATED, value), initial=sentinel), sentinel)
                self.assertIs(instance.evaluate(config(entity=value)), False)
        self.assertEqual(world.calls[:2], [0, 0])

    def test_scalar_array_first_entity_and_authored_field_are_distinct(self):
        world, _, instance = self.setup({BODY: record(BODY, (43,), CONTROLLER)})
        cfg = config(query.RELATED, ((expr.Entity(BODY), expr.Entity(OTHER)),))
        cfg['m_entity'] = expr.Entity(OTHER)  # wrong field must be ignored
        self.assertEqual(instance.evaluate(cfg), expr.Entity(CONTROLLER))
        self.assertEqual(world.calls, [BODY])

    def test_related_identity_is_raw_and_does_not_require_target_existence(self):
        for target in (0, 0xFFFFFFFF, 0xBFFFFFFF, OTHER):
            with self.subTest(target=target):
                world, _, instance = self.setup({BODY: record(BODY, (43,), target)})
                self.assertEqual(instance.evaluate(config(query.RELATED)), expr.Entity(target))
                self.assertEqual(world.calls, [BODY])

    def test_related_entity_is_not_recursively_followed(self):
        world, _, instance = self.setup({BODY: record(BODY, (43,), CONTROLLER),
                                         CONTROLLER: record(CONTROLLER, (43, 75), OTHER),
                                         OTHER: record(OTHER, (44, 75))})
        self.assertIs(instance.evaluate(config()), False)
        self.assertEqual(world.calls, [BODY, CONTROLLER])

    def test_unknown_source_and_unknown_selected_record_remain_unimplemented(self):
        world, _, instance = self.setup({BODY: NotImplemented})
        self.assertIs(query.evaluate(instance.context(), config()), NotImplemented)
        world.records[BODY] = record(BODY, (43,), CONTROLLER)
        world.records[CONTROLLER] = NotImplemented
        self.assertIs(query.evaluate(instance.context(), config()), NotImplemented)
        bare = runtime.Component(BODY, world=runtime.World()).create(make_graph(0xF003))
        self.assertIs(query.evaluate(bare.context(), config()), NotImplemented)

    def test_dependency_poll_observes_replacement_or_removed_relation(self):
        world = QueryWorld({BODY: record(BODY, (43,), CONTROLLER),
                            CONTROLLER: record(CONTROLLER, (44, 75))})
        graph = make_graph(0xF002, [
            node(0, 'STUStatescriptEntry', fields={'m_outPlug': output([1, 'm_beginPlug'])}),
            node(1, 'STUStatescriptStateBooleanSwitch', state=0,
                 fields={'m_condition': config()}),
        ], entries=[0], states=[1])
        component = runtime.Component(BODY, world=world, strict=True)
        instance = component.create(graph)
        component.run_frame(1)
        state = instance.states[0]
        self.assertTrue(state.current)
        self.assertIn(query.PREDICATE, state.natives)
        del world.records[CONTROLLER]
        component.run_frame(2)
        self.assertFalse(state.current)
        world.records[BODY] = record(BODY, (44, 75))
        component.run_frame(3)
        self.assertTrue(state.current)

    def test_lookup_cannot_retire_the_evaluating_instance(self):
        world, component, instance = self.setup({BODY: record(BODY, (44, 75))})
        world.callback = lambda: component.destroy(instance)
        with self.assertRaises(runtime.UnsupportedScriptError):
            instance.evaluate(config())

    def test_mismatched_callback_record_is_rejected(self):
        _, _, instance = self.setup({BODY: record(OTHER, (44, 75))})
        with self.assertRaises(runtime.UnsupportedScriptError):
            instance.evaluate(config())

    def test_record_requires_explicit_valid_membership_and_relation(self):
        for entity, kinds, related in ((0, (), None), (BODY, (256,), None),
                                        (BODY, (True,), None), (BODY, (43,), None),
                                        (BODY, (), CONTROLLER), (BODY, (43,), -1)):
            with (self.subTest(entity=entity, kinds=kinds, related=related),
                  self.assertRaises(ValueError)):
                record(entity, kinds, related)


if __name__ == '__main__':
    unittest.main()
