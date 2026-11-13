"""Compare synthetic victory view routes through normal pinned Python services."""
import argparse
import importlib
import json
from pathlib import Path
import subprocess
import sys


def observe(contract, case):
    runtime = importlib.import_module('ow174.game.script.runtime')
    scene = importlib.import_module('ow174.game.script.scene')
    expr = importlib.import_module('ow174.game.script.expr')
    helper = importlib.import_module('tests.test_script_runtime_budget')
    importlib.import_module('ow174.game.script.nodes')

    class RecordingWorld(scene.SceneWorld):
        def __init__(self):
            super().__init__(mode=0x0230000000000003)
            self.requests = []

        def submit_game_message(self, instance, node, request):
            self.requests.append(request)

    world = RecordingWorld()
    owner, subject = runtime.GAME_MODE_ENTITY, contract['input']['subject']
    world.bind(owner)
    world.bind(subject)
    receiver = runtime.Component(subject, world=world, strict=True)
    world.bind_script_component(subject, receiver)
    receiver_graph = helper.graph([helper.node(0, 'STUStatescriptState', state=0)])
    receiver_graph.index = 0x0CA5
    receiver.create(receiver_graph)
    component = runtime.Component(owner, world=world, strict=True)
    world.bind_script_component(owner, component)
    fields = dict(case['fields'])
    fields['m_outPlug'] = helper.plug((5, 'm_inPlug'))
    following = helper.setter(5, 1)
    following['fields']['m_out_Var']['m_identifier'] = 9
    model = helper.graph([
        helper.node(case['position'], case['class'], **fields), following,
    ])
    model.index = 0x0CEC
    active = component.create(model, init=False)
    active.initialized = True
    active.write((0, 8602), expr.Entity(subject))
    active.run_node(model.node(case['position']), None)
    if not world.requests:
        return {'forwarded': bool(active.find_var(0, 9).value()), 'parameters': None}
    request, = world.requests
    assert request.targets == (subject,) and not request.self_target
    assert request.owner == owner
    def scalar(values):
        value, = values
        return {'entity': value.id} if isinstance(value, expr.Entity) else value
    return {'forwarded': bool(active.find_var(0, 9).value()),
            'parameters': {str(key & 0xFFFF): scalar(values) for key, values in request.params}}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--python-reference', type=Path, required=True)
    args = parser.parse_args()
    reference = args.python_reference.resolve(strict=True)
    path = Path(__file__).resolve().parents[1] / 'fixtures/behavior/statescript-victory-view-route-v1.json'
    contract = json.loads(path.read_text(encoding='utf-8'))
    assert (contract['schema_version'], contract['case_id'], contract['target_build']) == (1, 'statescript.victory-view-route.v1', 104319)
    revision = subprocess.check_output(['git', '-C', str(reference), 'rev-parse', 'HEAD'], text=True).strip()
    assert revision == contract['source']['revision']
    subprocess.check_call(['git', '-C', str(reference), 'diff', '--quiet', 'HEAD', '--', 'ow174/game/script'])
    sys.path.insert(0, str(reference))
    cases = contract['input']['cases']
    for case in cases:
        actual = observe(contract, case)
        assert actual == case['expected'], (case['name'], actual, case['expected'])
    print(f'PASS: {len(cases)} victory view route cases against {revision}')


if __name__ == '__main__':
    main()
