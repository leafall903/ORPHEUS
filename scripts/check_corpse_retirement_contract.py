"""Compare corpse-action routing with the pinned normal Python StateScript path."""

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
                        / "fixtures/behavior/statescript-corpse-retirement-v1.json")
    args = parser.parse_args()
    contract = json.loads(args.fixture.read_text(encoding="utf-8"))
    if (contract["schema_version"], contract["case_id"], contract["target_build"]) != (
            1, "statescript.corpse-retirement-action.v1", 104319):
        raise ValueError("unsupported corpse-retirement contract")
    reference = args.python_reference.resolve(strict=True)
    revision = subprocess.check_output(["git", "-C", str(reference), "rev-parse", "HEAD"],
                                       text=True).strip()
    if revision != contract["source"]["revision"]:
        raise ValueError(f"reference differs from pinned source: {revision}")
    sys.path.insert(0, str(reference))
    helper = importlib.import_module("tests.test_script_runtime_budget")
    runtime = importlib.import_module("ow174.game.script.runtime")
    expr = importlib.import_module("ow174.game.script.expr")

    class Host(runtime.World):
        def __init__(self, accept):
            self.accept = accept
            self.requests = []

        def request_corpse_retirement(self, instance, entity, *, phase_exit=False):
            self.requests.append([entity, phase_exit])
            return self.accept

    def dynamic(identifier):
        return {"$": "STUConfigVarDynamic", "m_identifier": identifier, "m_60DB8F99": True}

    cases, expected = contract["input"]["cases"], contract["expected"]["results"]
    if len(cases) != len(expected) or len({case["name"] for case in cases}) != len(cases):
        raise ValueError("invalid contract case count or names")
    for case, result in zip(cases, expected):
        if case["name"] != result["name"]:
            raise ValueError("case order differs from expected order")
        position = case["position"]
        model = helper.graph([
            helper.node(0, "STUStatescriptEntry", m_outPlug=helper.plug((position, "m_inPlug"))),
            helper.node(position, "STU_523EE38D", m_entity=dynamic(1),
                        m_outPlug=helper.plug((99, "m_inPlug"))),
            helper.node(99, "STUStatescriptActionSetVar", m_out_Var=dynamic(90),
                        m_value={"$": "STUConfigVarBool", "m_value": True}),
        ], entries=(0,))
        model.index = case["graph"]
        host = Host(case["accept"])
        component = runtime.Component(contract["input"]["owner"], strict=True, world=host)
        active = component.create(model, init=False)
        active.initialized = True
        target = case["target"]
        component.set(1, expr.Entity(target["entity"]) if isinstance(target, dict) else target)
        failed = False
        try:
            active.run_node(model.node(0), None)
        except runtime.UnsupportedScriptError:
            failed = True
        marker = component.entity_var(90, create=False)
        actual = {"name": case["name"], "requests": host.requests,
                  "followed": marker is not None and marker.value() is True, "failed": failed}
        if actual != result:
            raise AssertionError(f"{case['name']}: {actual!r} != {result!r}")
    print(f"PASS: {len(cases)} shared corpse-action cases against {revision}")


if __name__ == "__main__":
    main()
