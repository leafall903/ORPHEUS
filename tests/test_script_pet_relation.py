"""Component72 entity query guards,typed defaults,observer order and lifetimes."""

import unittest

from test_game_graph_analysis import make_graph, node

from ow174.game.script import expr, runtime
from ow174.game.script import pet_relation as relation

OWNER, BODY = 0xA0000100, 0xA0000101
BODY_VALUE = expr.Entity(BODY)


class World(runtime.World):
    def __init__(self):
        super().__init__()
        self.calls = []
        self.target = relation.Target(BODY, relation.Pet(OWNER))
        self.context = relation.Context(OWNER, True, True, True, lookup=self.lookup,
            watch_environment=self.watch_environment, watch_pet=self.watch_pet)
        self.callback = None

    def pet_relation_context(self, owner, *, component=None):
        return self.context

    def lookup(self, identifier):
        self.calls.append(('lookup', identifier))
        return self.target if self.target is not None and self.target.entity == identifier else None

    def watch_environment(self, watcher, kind):
        self.calls.append(('environment', kind))

    def watch_pet(self, watcher, target, pet):
        self.calls.append(('pet', target.entity))
        if self.callback is not None:
            self.callback(target, pet)

    def native(self, cfg, instance):
        if cfg.get('$') == 'TEST_VALUE':
            self.calls.append(('selector', cfg['value']))
            return cfg['value']
        return super().native(cfg, instance)


def config(value=BODY_VALUE):
    return {'$': relation.CLASS, 'm_D63CBEC2': {'$': 'TEST_VALUE', 'value': value}}


class PetRelationTests(unittest.TestCase):
    def setup_query(self):
        self.world = World()
        self.component = runtime.Component(OWNER, world=self.world, strict=True)
        self.instance = self.component.create(make_graph(1, [node(0, 'STUStatescriptState', state=0)],
                                                         states=[0]), init=False)
        self.watcher = self.instance.state(0)

    def test_unknown_backend_remains_strict(self):
        instance = runtime.Component(OWNER, strict=True).create(make_graph(1), init=False)
        with self.assertRaises(runtime.UnsupportedScriptError):
            instance.evaluate(config())

    def test_missing_context_or_namespace_preserves_output_without_selector(self):
        for field in ('context_present', 'namespace_present'):
            self.setup_query()
            setattr(self.world.context, field, False)
            marker = object()
            self.assertIs(self.instance.evaluate(config(), self.watcher, initial=marker), marker)
            self.assertEqual(self.world.calls, [])

    def test_missing_target_observes_environment_and_succeeds_entity_zero(self):
        for present, kind in ((False, 46), (True, 0)):
            self.setup_query()
            self.world.target = None
            self.world.context.environment_present = present
            self.assertEqual(self.instance.evaluate(config(), self.watcher, initial=17), expr.Entity(0))
            self.assertEqual(self.world.calls, [('selector', expr.Entity(BODY)), ('lookup', BODY),
                                              ('environment', kind)])

    def test_missing_target_without_watcher_needs_no_hub(self):
        self.setup_query()
        self.world.target = None
        self.world.context.watch_environment = NotImplemented
        self.assertEqual(self.instance.evaluate(config(), initial=17), expr.Entity(0))

    def test_existing_target_without_component_succeeds_zero_without_observation(self):
        self.setup_query()
        self.world.target.pet = None
        self.world.context.watch_pet = NotImplemented
        self.assertEqual(self.instance.evaluate(config(), self.watcher), expr.Entity(0))
        self.assertEqual(self.world.calls, [('selector', expr.Entity(BODY)), ('lookup', BODY)])

    def test_observation_reads_fresh_field_from_captured_pet(self):
        self.setup_query()
        self.world.callback = lambda target, pet: setattr(pet, 'entity', BODY)
        self.assertEqual(self.instance.evaluate(config(), self.watcher), expr.Entity(BODY))
        self.assertEqual(self.world.calls[-1], ('pet', BODY))

    def test_observation_keeps_old_component_pointer_if_target_slot_replaced(self):
        self.setup_query()
        self.world.callback = lambda target, pet: setattr(target, 'pet', relation.Pet(BODY))
        self.assertEqual(self.instance.evaluate(config(), self.watcher), expr.Entity(OWNER))

    def test_stored_invalid_identity_is_still_typed_entity(self):
        self.setup_query()
        self.world.target.pet.entity = 0xFFFFFFFF
        self.assertEqual(self.instance.evaluate(config(), self.watcher), expr.Entity(0xFFFFFFFF))

    def test_absent_failed_nonentity_or_array_first_selector_uses_original_conversion(self):
        for value in (expr.EVALUATION_FAILED, 123, expr.Asset(BODY), (expr.Entity(BODY), expr.Entity(0))):
            self.setup_query()
            expected_id = BODY if isinstance(value, tuple) else 0
            self.world.target = relation.Target(expected_id, relation.Pet(OWNER))
            self.assertEqual(self.instance.evaluate(config(value), self.watcher), expr.Entity(OWNER))
            self.assertIn(('lookup', expected_id), self.world.calls)
        self.setup_query()
        self.world.target = relation.Target(0, None)
        self.assertEqual(self.instance.evaluate({'$': relation.CLASS}, self.watcher), expr.Entity(0))

    def test_observer_target_retirement_poison_is_not_success(self):
        self.setup_query()
        self.world.callback = lambda target, pet: setattr(target, 'retired', True)
        with self.assertRaises(runtime.UnsupportedScriptError):
            self.instance.evaluate(config(), self.watcher)
        self.assertIsNotNone(self.component.fault)

    def test_unknown_component_and_mismatched_lookup_stay_strict(self):
        for mismatch in (False, True):
            self.setup_query()
            if mismatch:
                self.world.context.lookup = lambda entity: relation.Target(OWNER, None)
            else:
                self.world.target.pet = NotImplemented
            with self.subTest(mismatch=mismatch), self.assertRaises(runtime.UnsupportedScriptError):
                self.instance.evaluate(config(), self.watcher)

    def test_context_replacement_during_selector_rejects_before_lookup(self):
        self.setup_query()
        original = self.world.native
        def native(cfg, instance):
            if cfg.get('$') == 'TEST_VALUE':
                self.world.context.retire()
            return original(cfg, instance)
        self.world.native = native
        with self.assertRaises(runtime.UnsupportedScriptError):
            self.instance.evaluate(config(), self.watcher)
        self.assertFalse(any(row[0] == 'lookup' for row in self.world.calls))
