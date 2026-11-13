"""Compare the typed engine-setting contract with the pinned Python scene evaluator."""

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
                        / "fixtures/behavior/statescript-engine-setting-v1.json")
    args = parser.parse_args()
    contract = json.loads(args.fixture.read_text(encoding="utf-8"))
    if (contract["schema_version"], contract["case_id"], contract["target_build"]) != (
            1, "statescript.engine-setting-read.v1", 104319):
        raise ValueError("unsupported engine-setting contract schema/case/build")
    reference = args.python_reference.resolve(strict=True)
    revision = subprocess.check_output(["git", "-C", str(reference), "rev-parse", "HEAD"],
                                       text=True).strip()
    if revision != contract["source"]["revision"]:
        raise ValueError(f"reference differs from pinned source: {revision}")
    sys.path.insert(0, str(reference))
    helper = importlib.import_module("tests.test_script_array_insert")
    scene = importlib.import_module("ow174.game.script.scene")
    runtime = importlib.import_module("ow174.game.script.runtime")

    def decode(value):
        return tuple(decode(item) for item in value) if isinstance(value, list) else value

    def encode(value):
        return [encode(item) for item in value] if isinstance(value, tuple) else value

    cases, expected = contract["input"]["cases"], contract["expected"]["results"]
    if len(cases) != len(expected) or len({case["name"] for case in cases}) != len(cases):
        raise ValueError("invalid contract case/expected count or names")
    for case, result in zip(cases, expected):
        if case["name"] != result["name"]:
            raise ValueError("contract case order differs from expected order")
        world = scene.SceneWorld()
        settings = case["settings"]
        world.engine_settings = (None if settings is None else
                                 {key: decode(value) for key, value in settings})
        _, active, _ = helper.setup(world=world)
        config = {"$": "STU_A753DC79", "m_EE729DCB": 0, "m_74A2248F": case["hash"]}
        try:
            actual = active.evaluate(config, initial=decode(case["initial"]))
        except runtime.UnsupportedScriptError as error:
            if result.get("error") != "STU_A753DC79" or "STU_A753DC79" not in str(error):
                raise
        else:
            if "error" in result:
                raise AssertionError(f"{case['name']}: expected an unsupported getter")
            if json.dumps(encode(actual), sort_keys=True) != json.dumps(result["value"], sort_keys=True):
                raise AssertionError(f"{case['name']}: {actual!r} != {result['value']!r}")
    print(f"PASS: {len(cases)} shared engine-setting cases against {revision}")


if __name__ == "__main__":
    main()
