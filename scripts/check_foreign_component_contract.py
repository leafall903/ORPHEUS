"""Compare foreign component reads through the pinned Python scene evaluator."""

import argparse
import importlib
import json
from pathlib import Path
import subprocess
import sys


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--python-reference", type=Path, required=True)
    parser.add_argument("--fixture", type=Path, default=Path(__file__).resolve().parents[1]
                        / "fixtures/behavior/statescript-foreign-component-v1.json")
    args = parser.parse_args()
    contract = json.loads(args.fixture.read_text(encoding="utf-8"))
    if (contract["schema_version"], contract["case_id"], contract["target_build"]) != (
            1, "statescript.foreign-component-read.v1", 104319):
        raise ValueError("unsupported foreign-component contract")
    reference = args.python_reference.resolve(strict=True)
    revision = subprocess.check_output(["git", "-C", str(reference), "rev-parse", "HEAD"],
                                       text=True).strip()
    if revision != contract["source"]["revision"]:
        raise ValueError(f"reference differs from pinned source: {revision}")
    sys.path.insert(0, str(reference))
    helper = importlib.import_module("tests.test_script_array_insert")
    scene = importlib.import_module("ow174.game.script.scene")
    runtime = importlib.import_module("ow174.game.script.runtime")
    expr = importlib.import_module("ow174.game.script.expr")

    def decode(value):
        return tuple(decode(item) for item in value) if isinstance(value, list) else value

    def encode(value):
        return [encode(item) for item in value] if isinstance(value, tuple) else value

    cases, expected = contract["input"]["cases"], contract["expected"]["results"]
    if len(cases) != len(expected) or len({case["name"] for case in cases}) != len(cases):
        raise ValueError("invalid case count or names")
    for case, result in zip(cases, expected):
        if case["name"] != result["name"]:
            raise ValueError("case order differs from expected order")
        world = scene.SceneWorld()
        target = contract["input"]["target"]
        world.bind(target)
        state = case["state"]
        if state == "absent":
            world.bind_script_component(target, None)
        elif state in ("running", "faulted"):
            component = runtime.Component(target, strict=True, world=world)
            for key, value in case["variables"].items():
                component.set(int(key), decode(value))
            world.bind_script_component(target, component)
            if state == "faulted":
                component.fault = RuntimeError("synthetic faulted component")
        elif state != "unavailable":
            raise ValueError(f"unknown component state {state}")
        _, active, _ = helper.setup(world=world, current=expr.Entity(target))
        try:
            actual = active.evaluate(contract["input"]["config"])
        except runtime.UnsupportedScriptError as error:
            if result.get("error") != "unavailable_entity_variable" or "entity variable service" not in str(error):
                raise
        else:
            if "error" in result:
                raise AssertionError(f"{case['name']}: expected unavailable variable service")
            if json.dumps(encode(actual)) != json.dumps(result["value"]):
                raise AssertionError(f"{case['name']}: {actual!r} != {result['value']!r}")
    print(f"PASS: {len(cases)} shared foreign-component cases against {revision}")


if __name__ == "__main__":
    main()
