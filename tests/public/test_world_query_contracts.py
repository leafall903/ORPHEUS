"""Synthetic World queries evaluated through the production strict script VM."""

import json
import os
import unittest
from pathlib import Path

from ow174.game.script import expr, runtime
from ow174.game.script.graph import Graph

FIXTURES = Path(
    os.environ.get("ORPHEUS_BEHAVIOR_FIXTURES")
    or Path(__file__).resolve().parents[2] / "fixtures/behavior"
)


def fixture_value(value):
    if value is None or type(value) in (bool, int, float):
        return value
    if isinstance(value, dict) and set(value) == {"entity"}:
        return expr.Entity(value["entity"])
    raise ValueError(f"unsupported fixture value: {value!r}")


class FixtureWorld(runtime.World):
    """Supply explicit fixture facts; expression execution stays in the VM."""

    def __init__(self, inputs):
        super().__init__()
        self.positions = {
            int(entity): expr.Vec3(*position) for entity, position in inputs["positions"].items()
        }
        self.members = {
            int(entity): {int(identifier): fixture_value(value) for identifier, value in variables.items()}
            for entity, variables in inputs["members"].items()
        }

    def position(self, entity):
        return self.positions.get(entity)

    def entity_var(self, entity, var):
        variables = self.members.get(entity)
        return NotImplemented if variables is None else variables.get(var)


def fixture_instance(inputs):
    model = Graph({
        "index": 1, "header": {}, "fields": {}, "items": 0, "containers": {},
        "nodes": [], "unknown_nodes": [], "states": [], "entries": [],
        "remote": [], "sync_vars": [],
    })
    component = runtime.Component(inputs["owner"], world=FixtureWorld(inputs), strict=True)
    instance = component.create(model, init=False)
    for identifier, value in inputs.get("instance_variables", {}).items():
        instance.write((expr.INSTANCE, int(identifier)), fixture_value(value))
    return instance


class WorldQueryContractTests(unittest.TestCase):
    def test_shared_world_queries_use_strict_production_context(self):
        contract = json.loads((FIXTURES / "world-queries-v1.json").read_text(encoding="utf-8"))
        self.assertEqual(contract["schema_version"], 1)
        self.assertEqual(contract["case_id"], "world-queries.synthetic-strict-host.v1")
        self.assertEqual(contract["target_build"], 104319)
        self.assertEqual(contract["evidence"], "synthetic-strict-host-reference")
        cases = contract["input"]["cases"]
        self.assertTrue(cases, "the contract must exercise at least one case")
        identifiers = [case["id"] for case in cases]
        self.assertEqual(len(identifiers), len(set(identifiers)), "duplicate case IDs")
        self.assertEqual(set(contract["expected"]), set(identifiers))

        for case in cases:
            with self.subTest(case=case["id"]):
                instance = fixture_instance(case["context"])
                expected = contract["expected"][case["id"]]
                if "error" in expected:
                    self.assertEqual(set(expected), {"error"})
                    error = expected["error"]
                    if error["kind"] == "unavailable_position":
                        feature = "entity position"
                        query = f"{error['entity']:08X}"
                    else:
                        self.assertEqual(error["kind"], "unavailable_entity_variable")
                        feature = "entity variable service"
                        query = f"{error['entity']:08X}:{error['id']}"
                    with self.assertRaises(runtime.UnsupportedScriptError) as raised:
                        instance.evaluate(case["config"])
                    diagnostic = raised.exception.diagnostic
                    self.assertEqual(diagnostic["feature"], feature)
                    self.assertEqual(diagnostic["class"], query)
                    self.assertEqual(diagnostic["entity"], case["context"]["owner"])
                    self.assertIs(instance.component.fault, raised.exception)
                    self.assertTrue(instance.stopped)
                else:
                    self.assertEqual(set(expected), {"value"})
                    value = instance.evaluate(case["config"])
                    output = expected["value"]
                    if output["kind"] == "null":
                        self.assertEqual(set(output), {"kind"})
                        self.assertIsNone(value)
                    else:
                        self.assertIn(output["kind"], ("float", "integer"))
                        value_type = float if output["kind"] == "float" else int
                        self.assertIs(type(value), value_type)
                        self.assertEqual(value, output["value"])
                    self.assertIsNone(instance.component.fault)


if __name__ == "__main__":
    unittest.main()
