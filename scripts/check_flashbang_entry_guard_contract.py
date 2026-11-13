"""Compare the authored Boolean entry guard through the pinned normal Python VM."""
import argparse
import importlib
import json
from pathlib import Path
import subprocess
import sys


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--python-reference', type=Path, required=True)
    reference = parser.parse_args().python_reference.resolve(strict=True)
    fixture = Path(__file__).resolve().parents[1] / 'fixtures/behavior/flashbang-entry-guard-v1.json'
    contract = json.loads(fixture.read_text(encoding='utf-8'))
    assert (contract['schema_version'], contract['case_id'], contract['target_build']) == (1, 'flashbang.entry-guard.v1', 104319)
    revision = subprocess.check_output(['git', '-C', str(reference), 'rev-parse', 'HEAD'], text=True).strip()
    assert revision == contract['source']['revision']
    subprocess.check_call(['git', '-C', str(reference), 'diff', '--quiet', 'HEAD', '--',
                           'ow174/game/script', 'tests/test_script_runtime_budget.py'])
    sys.path.insert(0, str(reference))
    runtime = importlib.import_module('ow174.game.script.runtime')
    helper = importlib.import_module('tests.test_script_runtime_budget')
    importlib.import_module('ow174.game.script.nodes')
    count = 0
    for case in contract['cases']:
        if case.get('error'):
            continue  # Rust adapter availability boundaries, not Boolean VM semantics.
        linked = case['stack_value'] is not None
        nodes = [helper.node(0, 'STUStatescriptEntry',
                             m_outPlug=helper.plug(*([(1, 'm_beginPlug')] if linked else [])))]
        if linked:
            nodes.append(helper.node(1, 'STUStatescriptStateStack', state=0,
                m_out_Var={'$':'STUConfigVarDynamic','m_60DB8F99':1,'m_identifier':2547},
                m_value={'$':'STUConfigVarBool','m_value':case['stack_value']},
                m_priority={'$':'STUConfigVarFloat','m_value':0}))
        component = runtime.Component(42, strict=True)
        active = component.create(helper.graph(nodes, entries=(0,)))
        active.write((1, 2547), case['base'])
        active.write((0, 29458), case['force_default'])
        component.run_now()
        actual = active.evaluate(contract['condition'])
        assert actual == case['accepted'], (case['name'], actual, case['accepted'])
        count += 1
    print(f'PASS: {count} Boolean/linked-binding cases against {revision}; two Rust-only availability boundaries')


if __name__ == '__main__':
    main()
