"""Compare bounded early-press lifetime through the normal pinned Python VM."""
import argparse
import importlib
import json
from pathlib import Path
import subprocess
import sys


def observe(contract, case):
    runtime = importlib.import_module('ow174.game.script.runtime')
    helper = importlib.import_module('tests.test_script_runtime_budget')
    importlib.import_module('ow174.game.script.nodes')
    def var(identifier):
        return {'$': 'STUConfigVarDynamic', 'm_identifier': identifier, 'm_60DB8F99': 1}
    model = helper.graph([
        helper.node(0, 'STUStatescriptEntry', m_outPlug=helper.plug((1, 'm_beginPlug'))),
        helper.node(1, 'STUStatescriptStateAbility', state=0,
                    m_A5F1A77B=36, m_246438AD=contract['input']['window_seconds'],
                    m_150F0D92=var(900), m_EF3C5C73=[{'m_out_Var': var(902), 'm_value': True}]),
    ], entries=(0,))
    model.fields['m_680A2CB2'] = [0]
    component = runtime.Component(42, strict=True)
    active = component.create(model)
    active.write((1, 900), False)
    active.write((1, 902), False)
    component.run_now()
    previous, observed = False, []
    for frame in range(1, contract['input']['frames'] + 1):
        if frame == case['ready_frame']:
            active.write((1, 900), True)
        pressed = frame in case['press_frames']
        component.run_frame(frame, [(36, pressed)] if pressed != previous else [])
        previous = pressed
        if frame in contract['input']['sample_frames']:
            observed.append(bool(active.find_var(1, 902).value()))
    return observed


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--python-reference', type=Path, required=True)
    reference = parser.parse_args().python_reference.resolve(strict=True)
    path = Path(__file__).resolve().parents[1] / 'fixtures/behavior/statescript-ability-press-window-v1.json'
    contract = json.loads(path.read_text(encoding='utf8'))
    assert (contract['schema_version'], contract['case_id'], contract['target_build']) == (1, 'statescript.ability-press-window.v1', 104319)
    revision = subprocess.check_output(['git', '-C', str(reference), 'rev-parse', 'HEAD'], text=True).strip()
    assert revision == contract['source']['revision']
    subprocess.check_call(['git', '-C', str(reference), 'diff', '--quiet', 'HEAD', '--', 'ow174/game/script'])
    sys.path.insert(0, str(reference))
    for case in contract['input']['cases']:
        actual = observe(contract, case)
        assert actual == case['expected'], (case['name'], actual, case['expected'])
    print(f'PASS: 4 ability press-window cases against {revision}')


if __name__ == '__main__':
    main()
