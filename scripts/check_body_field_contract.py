"""Compare component44 body-field selection with the pinned Python query service."""
import argparse
import copy
import importlib
import json
from pathlib import Path
import subprocess
import sys


def run_case(contract, case):
    scene = importlib.import_module('ow174.game.script.scene')
    runtime = importlib.import_module('ow174.game.script.runtime')
    graph = importlib.import_module('ow174.game.script.graph')
    expr = importlib.import_module('ow174.game.script.expr')
    query = importlib.import_module('ow174.game.script.entity_queries')
    importlib.import_module('ow174.game.script.nodes')
    owner = contract['input']['owner']
    world = scene.SceneWorld()
    world.bind(owner)
    if case.get('available', True):
        def lookup(entity):
            row = case['targets'].get(str(entity), 'unavailable')
            if row == 'absent':
                return None
            if row == 'unavailable':
                return query.Target(entity)
            body = row['body']
            return query.Target(entity, body_field=None if body is None else query.BodyField(body))
        world.set_entity_query_context(owner, query.Context(owner, True, True, True,
            lookup=lookup, watch_environment=None, watch_possession=None))
    component = runtime.Component(owner, strict=True, world=world)
    world.bind_script_component(owner, component)
    active = component.create(graph.Graph(copy.deepcopy(contract['input']['graph'])), init=False)
    active.initialized = True
    def decode(value):
        if isinstance(value, list):
            return tuple(decode(v) for v in value)
        if isinstance(value, dict):
            if 'entity' in value:
                return expr.Entity(value['entity'])
            if 'asset' in value:
                return expr.Asset(value['asset'])
        return value
    active.write((0, 1), decode(case.get('value')))
    try:
        value = active.evaluate(case['config'])
    except runtime.UnsupportedScriptError as error:
        assert 'STU_00707E1E' in str(error)
        return {'error': 'STU_00707E1E'}
    assert isinstance(value, expr.Entity), value
    return {'entity': value.id}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--python-reference', type=Path, required=True)
    args = parser.parse_args()
    contract = json.loads((Path(__file__).resolve().parents[1] / 'fixtures/behavior/statescript-body-field-v1.json').read_text(encoding='utf-8'))
    assert (contract['schema_version'], contract['case_id'], contract['target_build']) == (1, 'statescript.body-field.v1', 104319)
    revision = subprocess.check_output(['git', '-C', str(args.python_reference), 'rev-parse', 'HEAD'], text=True).strip()
    assert revision == contract['source']['revision']
    subprocess.check_call(['git', '-C', str(args.python_reference), 'diff', '--quiet', 'HEAD', '--', 'ow174/game/script'])
    sys.path.insert(0, str(args.python_reference.resolve(strict=True)))
    cases, results = contract['input']['cases'], contract['expected']['results']
    assert len(cases) == len(results)
    for case, result in zip(cases, results, strict=True):
        assert case['name'] == result['name']
        actual = run_case(contract, case)
        assert actual == result['output'], (case['name'], actual, result['output'])
    print(f'PASS: {len(cases)} body-field cases against {revision}')


if __name__ == '__main__':
    main()
