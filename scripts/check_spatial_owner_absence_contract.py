"""Compare the explicit spatial-owner absence path with pinned Python."""
import argparse
import importlib
import json
from pathlib import Path
import subprocess
import sys


def observe(contract, case):
    runtime = importlib.import_module('ow174.game.script.runtime')
    scene = importlib.import_module('ow174.game.script.scene')
    scopes = importlib.import_module('ow174.game.script.scopes')
    helper = importlib.import_module('tests.test_script_runtime_budget')
    importlib.import_module('ow174.game.script.nodes')
    class Host(scene.SceneWorld):
        def spatial_binding_owner(self, entity):
            if case['service'] == 'present':
                return self
            return super().spatial_binding_owner(entity)
    world = Host()
    owner = 0xA0000101
    world.bind(owner)
    registry = scopes.Registry()
    world.set_subgraph_scopes(owner, registry)
    if case['service'] == 'absent':
        world.declare_spatial_binding_owner_absent(owner)
    component = runtime.Component(owner, strict=True, world=world)
    world.bind_script_component(owner, component)
    model = helper.graph([helper.node(case['position'], contract['input']['class'],
                                     state=0, **contract['input']['fields'])])
    model.index = 0x0CED
    active = component.create(model, init=False)
    active.initialized = True
    state = active.state(0)
    state.begin()
    try:
        registry.enter(state)
    except runtime.UnsupportedScriptError:
        return {'status': 'unavailable'}
    before = [state.active, state.counter]
    registry.exit(state)
    state.end(False)
    return {'status': 'ok', 'begin': before, 'end': [state.active, state.counter]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--python-reference', type=Path, required=True)
    args = parser.parse_args()
    reference = args.python_reference.resolve(strict=True)
    contract = json.loads((Path(__file__).resolve().parents[1] / 'fixtures/behavior/statescript-spatial-owner-absence-v1.json').read_text(encoding='utf-8'))
    assert (contract['schema_version'], contract['case_id'], contract['target_build']) == (1, 'statescript.spatial-owner-absence.v1', 104319)
    revision = subprocess.check_output(['git', '-C', str(reference), 'rev-parse', 'HEAD'], text=True).strip()
    assert revision == contract['source']['revision']
    subprocess.check_call(['git', '-C', str(reference), 'diff', '--quiet', 'HEAD', '--', 'ow174/game/script'])
    sys.path.insert(0, str(reference))
    for case in contract['input']['cases']:
        actual = observe(contract, case)
        assert actual == case['expected'], (case['name'], actual, case['expected'])
    print(f"PASS: {len(contract['input']['cases'])} spatial-owner cases against {revision}; E8D976BE is native-only")


if __name__ == '__main__':
    main()
