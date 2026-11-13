"""Compare the shared synthetic array fixture with a pinned Python implementation."""

import argparse
import importlib
import json
import math
from pathlib import Path
import subprocess
import sys


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--python-reference", type=Path, required=True)
    parser.add_argument("--fixture", type=Path, default=Path(__file__).resolve().parents[1]
                        / "fixtures/behavior/statescript-array-insert-v1.json")
    args = parser.parse_args()
    contract = json.loads(args.fixture.read_text(encoding="utf-8"))
    if (contract["schema_version"], contract["case_id"], contract["target_build"]) != (
            1, "statescript.unkeyed-array-insert.v1", 104319):
        raise ValueError("unsupported array contract schema/case/build")
    if contract["input"]["output_scope"] != "entity":
        raise ValueError("unsupported output scope")
    reference = args.python_reference.resolve(strict=True)
    revision = subprocess.check_output(["git", "-C", str(reference), "rev-parse", "HEAD"],
                                       text=True).strip()
    if revision != contract["source"]["revision"]:
        raise ValueError(f"reference differs from pinned source: {revision}")
    sys.path.insert(0, str(reference))
    implementation = importlib.import_module("tests.test_script_array_insert")
    expr = importlib.import_module("ow174.game.script.expr")

    def decode(value):
        if isinstance(value, list):
            return tuple(decode(item) for item in value)
        if isinstance(value, dict):
            if set(value) == {"entity"}:
                return expr.Entity(value["entity"])
            if set(value) == {"float"} and value["float"] in ("NaN", "Infinity"):
                return float(value["float"])
            raise ValueError(f"unsupported contract value: {value}")
        return value

    def encode(value):
        if isinstance(value, tuple):
            return [encode(item) for item in value]
        if isinstance(value, expr.Entity):
            return {"entity": value.id}
        if isinstance(value, float) and not math.isfinite(value):
            return {"float": "NaN" if math.isnan(value) else "Infinity"}
        if value is None or type(value) in (bool, int, float):
            return value
        raise ValueError(f"unsupported implementation result: {value}")

    cases = contract["input"]["cases"]
    expected = contract["expected"]["states"]
    if len(cases) != len(expected) or len({case["name"] for case in cases}) != len(cases):
        raise ValueError("contract case/expected count or names are invalid")
    for case, result in zip(cases, expected):
        if case["name"] != result["name"]:
            raise ValueError("contract case order differs from expected order")
        options = {"value": decode(case["source"]), "unique": case["unique"],
                   "output": implementation.dynamic(100, scope=1),
                   "action_class": "STU_CBE8BDAA" if case["single"] else "STU_4C3509FB"}
        if case["index_present"]:
            options["index"] = decode(case["index"])
        for field, option in (("source_config", "source"),):
            if field in case:
                options[option] = case[field]
        component, active, action = implementation.setup(**options)
        for field, native_field in (("index_config", "m_index"), ("key_config", "m_key")):
            if field in case:
                action.fields[native_field] = case[field]
        active.write((1, 100), decode(case["current"]))
        active.run_node(action, None)
        actual = encode(component.entity_var(100).value())
        marker = active.find_var(0, 90, create=False)
        followed = marker is not None and marker.value() == 19
        if (json.dumps(actual, sort_keys=True) != json.dumps(result["value"], sort_keys=True)
                or followed != result["followed"]):
            raise AssertionError(f"{case['name']}: value={actual}, followed={followed}")
    print(f"PASS: {len(cases)} shared array cases against {revision}")


if __name__ == "__main__":
    main()
