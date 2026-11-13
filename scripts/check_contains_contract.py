"""Compare container-membership branches with the pinned normal Python VM."""

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
                        / "fixtures/behavior/statescript-contains-v1.json")
    args = parser.parse_args()
    contract = json.loads(args.fixture.read_text(encoding="utf-8"))
    if (contract["schema_version"], contract["case_id"], contract["target_build"]) != (
            1, "statescript.container-contains-condition.v1", 104319):
        raise ValueError("unsupported contains contract")
    reference = args.python_reference.resolve(strict=True)
    revision = subprocess.check_output(["git", "-C", str(reference), "rev-parse", "HEAD"],
                                       text=True).strip()
    if revision != contract["source"]["revision"]:
        raise ValueError(f"reference differs from pinned source: {revision}")
    sys.path.insert(0, str(reference))
    helper = importlib.import_module("tests.test_script_runtime_budget")
    runtime = importlib.import_module("ow174.game.script.runtime")
    expr = importlib.import_module("ow174.game.script.expr")

    def decode(value):
        if isinstance(value, list):
            return tuple(decode(item) for item in value)
        return expr.Entity(value["entity"]) if isinstance(value, dict) else value

    def dynamic(identifier):
        return {"$": "STUConfigVarDynamic", "m_identifier": identifier, "m_60DB8F99": True}

    def setter(position, value):
        return helper.node(position, "STUStatescriptActionSetVar", m_out_Var=dynamic(90),
                           m_value={"$": "STUConfigVarInt", "m_value": value})

    cases, expected = contract["input"]["cases"], contract["expected"]["results"]
    if len(cases) != len(expected) or len({case["name"] for case in cases}) != len(cases):
        raise ValueError("invalid case count or names")
    for case, result in zip(cases, expected):
        if case["name"] != result["name"]:
            raise ValueError("case order differs from expected order")
        model = helper.graph([
            helper.node(0, "STUStatescriptEntry", m_outPlug=helper.plug((1, "m_inPlug"))),
            helper.node(1, "STU_821116A6", m_814F8EC3=dynamic(100), m_value=dynamic(101),
                        m_truePlug=helper.plug(*([(2, "m_inPlug")] if case["links"] else [])),
                        m_falsePlug=helper.plug(*([(3, "m_inPlug")] if case["links"] else []))),
            setter(2, 19), setter(3, 23),
        ], entries=(0,))
        component = runtime.Component(123, strict=True)
        active = component.create(model, init=False)
        active.initialized = True
        component.set(100, decode(case["container"]))
        component.set(101, decode(case["value"]))
        active.run_node(model.node(0), None)
        marker = component.entity_var(90, create=False)
        actual = marker.value() if marker is not None else None
        if actual != result["marker"]:
            raise AssertionError(f"{case['name']}: {actual!r} != {result['marker']!r}")
    print(f"PASS: {len(cases)} shared contains cases against {revision}")


if __name__ == "__main__":
    main()
