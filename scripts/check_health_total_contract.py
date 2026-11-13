"""Compare the shared total-health fixture through the pinned Python evaluator."""

import argparse
import importlib
import json
from pathlib import Path
import struct
import subprocess
import sys


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--python-reference", type=Path, required=True)
    parser.add_argument("--fixture", type=Path, default=Path(__file__).resolve().parents[1]
                        / "fixtures/behavior/statescript-health-total-v1.json")
    args = parser.parse_args()
    contract = json.loads(args.fixture.read_text(encoding="utf-8"))
    if (contract["schema_version"], contract["case_id"], contract["target_build"]) != (
            1, "statescript.total-health-read.v1", 104319):
        raise ValueError("unsupported total-health contract")
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
        if isinstance(value, list):
            return tuple(decode(item) for item in value)
        if isinstance(value, dict):
            if set(value) == {"entity"}:
                return expr.Entity(value["entity"])
            if set(value) == {"vector"}:
                return expr.Vec3(*value["vector"])
            if set(value) == {"bits64"}:
                return struct.unpack(">d", bytes.fromhex(value["bits64"]))[0]
            if set(value) == {"float"}:
                return float(value["float"])
            raise ValueError(f"invalid contract value {value}")
        return value

    def encode(value):
        if isinstance(value, expr.Vec3):
            return {"vector": list(value)}
        if isinstance(value, tuple):
            return [encode(item) for item in value]
        return value

    cases, expected = contract["input"]["cases"], contract["expected"]["results"]
    if len(cases) != len(expected) or len({case["name"] for case in cases}) != len(cases):
        raise ValueError("invalid case count or names")
    for case, result in zip(cases, expected):
        if case["name"] != result["name"]:
            raise ValueError("case order differs from expected order")
        world = runtime.World() if case["table"] is None else scene.SceneWorld()
        for entity, health in case["table"] or ():
            callback = None if health is None else lambda v=decode(health): v
            world.bind(entity, health=callback)
        _, active, _ = helper.setup(world=world, current=decode(case["target"]))
        config = {"$": "STU_9B7648C3", "m_EE729DCB": 0,
                  "m_entity": case.get("selector", {"$": "STUConfigVarDynamic",
                               "m_identifier": 1, "m_60DB8F99": False})}
        try:
            actual = active.evaluate(config, initial=decode(case["initial"]))
        except runtime.UnsupportedScriptError as error:
            if result.get("error") != "STU_9B7648C3" or "STU_9B7648C3" not in str(error):
                raise
        else:
            if "error" in result:
                raise AssertionError(f"{case['name']}: expected unsupported health")
            if json.dumps(encode(actual), sort_keys=True) != json.dumps(encode(decode(result["value"])), sort_keys=True):
                raise AssertionError(f"{case['name']}: {actual!r} != {result['value']!r}")
    print(f"PASS: {len(cases)} shared total-health cases against {revision}")


if __name__ == "__main__":
    main()
