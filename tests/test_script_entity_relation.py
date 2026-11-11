"""CE5020 front end; complete relationship backend is explicitly supplied."""

import unittest

from ow174.game.script import entity_relation as relation
from ow174.game.script import expr, play, runtime
from ow174.game.script import graph as graphs
from tests.test_script_runtime_budget import graph, node

OWNER, OTHER = 0xA0000100, 0xA0000101


def literal(value, name="value"):
    return {"$": "Literal", "value": value, "name": name}


def config(target=None, source=None, flag=None):
    return {"$": relation.CLASS, "m_target": target, "m_source": source, "m_AFBBE9C3": flag}


class World(runtime.World):
    def __init__(self):
        super().__init__()
        self.context = NotImplemented
        self.bound = None
        self.events = []
        self.children = None

    def entity_relation_context(self, entity, *, component=None):
        return self.context if component is self.bound and entity == OWNER else NotImplemented

    def native(self, cfg, instance):
        if cfg.get("$") == "Literal":
            self.events.append(("child", cfg["name"]))
            if self.children:
                self.children(cfg)
            return cfg["value"]
        return super().native(cfg, instance)


def setup():
    world = World()
    component = runtime.Component(OWNER, world=world, strict=True)
    world.bound = component
    instance = component.create(graph([node(0, "STUStatescriptState", state=0)]), init=False)
    instance.initialized = True
    targets = {OWNER: relation.Target(OWNER, "owner"), OTHER: relation.Target(OTHER, "other")}

    def lookup(identifier):
        world.events.append(("lookup", identifier))
        return targets.get(identifier)

    def relate(target, source, flag, watcher):
        world.events.append(("relation", target, source, flag, watcher))
        return True

    context = relation.Context(OWNER, True, lookup, relate,
        lambda watcher, kind: world.events.append(("watch", watcher, kind)))
    world.context = context
    return world, component, instance, context, targets


class EntityRelationTests(unittest.TestCase):
    def test_selected_children_lookup_and_backend_order(self):
        w, _, i, _, t = setup()
        self.assertIs(i.evaluate(config(literal(expr.Entity(OTHER), "target"),
                                      literal(expr.Entity(OWNER), "source"), literal(True, "flag"))), True)
        self.assertEqual(w.events, [("child", "target"), ("child", "source"), ("child", "flag"),
            ("lookup", OWNER), ("lookup", OTHER), ("relation", t[OTHER], t[OWNER], True, None)])

    def test_missing_selectors_still_lookup_zero_and_call_backend_with_null_objects(self):
        w, _, i, _, _ = setup()
        self.assertIs(i.evaluate(config()), True)
        self.assertEqual(w.events, [("lookup", 0), ("lookup", 0), ("relation", None, None, False, None)])

    def test_failed_and_nonentity_children_convert_to_zero(self):
        for value in (expr.EVALUATION_FAILED, None, 7, expr.Asset(7)):
            w, _, i, _, _ = setup()
            self.assertIs(i.evaluate(config(literal(value), literal(value),
                                            literal(expr.EVALUATION_FAILED))), True)
            self.assertEqual(w.events[-3:], [("lookup", 0), ("lookup", 0),
                                           ("relation", None, None, False, None)])

    def test_nested_first_entity_and_uint32_wrap(self):
        w, _, i, _, t = setup()
        i.evaluate(config(literal(((expr.Entity(OTHER + (1 << 32)),),)), literal(expr.Entity(OWNER))))
        self.assertEqual(w.events[-3:], [("lookup", OWNER), ("lookup", OTHER),
                                        ("relation", t[OTHER], t[OWNER], False, None)])

    def test_watch_kind_two_precedes_children(self):
        w, _, i, _, _ = setup()
        watcher = i.state(0)
        i.evaluate(config(literal(expr.Entity(OTHER))), watcher)
        self.assertEqual(w.events[0], ("watch", watcher, 2))
        self.assertEqual(w.events[1], ("child", "value"))
        self.assertIs(w.events[-1][-1], watcher)

    def test_known_missing_dependency_hub_is_distinct_from_unknown(self):
        w, _, i, c, _ = setup()
        c.watch_context = None
        self.assertIs(i.evaluate(config(), i.state(0)), True)
        self.assertFalse(any(e[0] == "watch" for e in w.events))
        _, _, i, c, _ = setup()
        c.watch_context = NotImplemented
        with self.assertRaises(runtime.UnsupportedScriptError):
            i.evaluate(config(), i.state(0))

    def test_absent_native_context_preserves_destination_and_reports_failure(self):
        w, _, i, c, _ = setup()
        c.context_present = False
        self.assertEqual(play.evaluate_scalar_result(i, config(), initial=expr.Asset(17)),
                         (expr.Asset(17), False))
        self.assertEqual(w.events, [])

    def test_backend_false_is_successful_false(self):
        _, _, i, c, _ = setup()
        c.relate = lambda *args: False
        self.assertEqual(play.evaluate_scalar_result(i, config(), initial=expr.Asset(17)), (False, True))

    def test_unknown_backend_and_bad_return_never_infer_faction(self):
        for backend in (NotImplemented, lambda *args: None, lambda *args: 1):
            _, _, i, c, _ = setup()
            c.relate = backend
            with self.assertRaises(runtime.UnsupportedScriptError):
                i.evaluate(config(literal(expr.Entity(OTHER)), literal(expr.Entity(OWNER))))

    def test_malformed_lookup_result_rejects_wrong_identity(self):
        for result in (NotImplemented, object(), relation.Target(OTHER)):
            _, _, i, c, _ = setup()
            c.lookup = lambda identifier, result=result: result
            with self.assertRaises(runtime.UnsupportedScriptError):
                i.evaluate(config())

    def test_same_identity_is_looked_up_twice_and_callback_mutation_is_observed(self):
        w, _, i, c, _ = setup()
        old, fresh = relation.Target(OWNER, "old"), relation.Target(OWNER, "fresh")
        answers = iter((old, fresh))
        c.lookup = lambda identifier: next(answers)
        i.evaluate(config(literal(expr.Entity(OWNER)), literal(expr.Entity(OWNER))))
        self.assertEqual(w.events[-1], ("relation", fresh, old, False, None))

    def test_retirement_at_dependency_child_lookup_or_backend_stops_evaluation(self):
        for phase in ("watch", "child", "lookup", "backend"):
            with self.subTest(phase=phase):
                w, _, i, c, _ = setup()
                if phase == "watch":
                    c.watch_context = lambda *args, context=c: context.retire()
                elif phase == "child":
                    w.children = lambda *args, context=c: context.retire()
                elif phase == "lookup":
                    c.lookup = lambda *args, context=c: context.retire()
                else:
                    c.relate = lambda *args, context=c: context.retire() or True
                with self.assertRaises(runtime.UnsupportedScriptError):
                    i.evaluate(config(literal(expr.Entity(OWNER))), i.state(0))

    def test_replaced_context_and_stale_component_cannot_borrow_services(self):
        w, _, i, c, _ = setup()
        c.lookup = lambda identifier: setattr(w, "context", relation.Context(OWNER, True))
        with self.assertRaises(runtime.UnsupportedScriptError):
            i.evaluate(config())
        w, _, i, _, _ = setup()
        w.bound = runtime.Component(OWNER, world=w, strict=True)
        with self.assertRaises(runtime.UnsupportedScriptError):
            i.evaluate(config())

    def test_lookup_work_consumes_shared_budget(self):
        w, component, i, c, _ = setup()

        def exhaust(identifier):
            w.events.append(("lookup", identifier))
            component._work_left = 0
            return None

        c.lookup = exhaust
        with self.assertRaises(runtime.ScriptBudgetError):
            i.evaluate(config())
        self.assertEqual(w.events, [("lookup", 0)])

    def test_original_mercy_damage_gate_selects_target393_and_owner_without_faction_guess(self):
        w, component, _, c, targets = setup()
        model = graphs.graph(0xB8B)
        self.assertIsNotNone(model, "original104319 charge graph required")
        i = component.create(model, init=False)
        i.initialized = True
        i.write((0, 393), expr.Entity(OTHER))
        cfg = model.node(28).field("m_condition")["m_configVars"][0]
        for value in (False, True):
            c.relate = lambda *args, value=value: value
            self.assertIs(i.evaluate(cfg), value)
            self.assertEqual(w.events[-2:], [("lookup", OWNER), ("lookup", OTHER)])
        # Missing object is passed to the required backend,not mapped to false.
        targets.pop(OTHER)
        self.assertIs(i.evaluate(cfg), True)
