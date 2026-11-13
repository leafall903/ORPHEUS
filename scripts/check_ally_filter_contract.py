"""Compare shared ally queries through the pinned Python scene evaluator."""
import argparse
import importlib
import json
from pathlib import Path
import subprocess
import sys


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--python-reference', type=Path, required=True)
    args = parser.parse_args()
    fixture = Path(__file__).resolve().parents[1] / 'fixtures/behavior/statescript-ally-filter-v1.json'
    contract = json.loads(fixture.read_text(encoding='utf-8'))
    assert (contract['schema_version'], contract['case_id'], contract['target_build']) == (
        1, 'statescript.ally-filter.v1', 104319)
    revision = subprocess.check_output(['git', '-C', str(args.python_reference),
                                        'rev-parse', 'HEAD'], text=True).strip()
    assert revision == contract['source']['revision']
    sys.path.insert(0, str(args.python_reference.resolve(strict=True)))
    helper = importlib.import_module('tests.test_script_array_insert')
    scene = importlib.import_module('ow174.game.script.scene')
    teams = importlib.import_module('ow174.game.script.entity_team')
    runtime = importlib.import_module('ow174.game.script.runtime')
    expr = importlib.import_module('ow174.game.script.expr')
    cases, results = contract['input']['cases'], contract['expected']['results']
    assert len(cases) == len(results)
    for case, expected in zip(cases, results, strict=True):
        assert case['name'] == expected['name']
        world = scene.SceneWorld(mode=next(iter(scene.JUNKENSTEIN_MODES)))
        world.bind(123, position=(0, 0, 0), target_tags=[])
        component, active, _ = helper.setup(world=world)
        active.graph.index = case['graph']
        world.bind_script_component(123, component)
        flags = {123: case['source_flags']}
        for body in case['bodies']:
            world.bind(body['entity'], position=body['position'], target_tags=None if body['tags'] is None else
                       [0x0D60000000000000 + tag for tag in body['tags']])
            flags[body['entity']] = body['flags']
        world.array_filter_inputs = lambda entity: (True, flags[entity])
        lookup = lambda entity: teams.Target(entity, teams.FilterBits(flags[entity]))
        for entity in flags:
            world.set_entity_team_context(entity, teams.Context(entity, True, True, True, lookup=lookup))
        query = {'$': 'STU_B3E67A36', 'm_filter': case['filter']} if case['kind'] == 'first' else {
            '$': 'STU_AC829876', 'm_filter': case['filter'],
            'm_entity': {'$': 'STUConfigVarDynamic', 'm_identifier': 2, 'm_60DB8F99': False}}
        active.write((0, 2), expr.Entity(case['target']))
        outer = {8: expr.Entity(999), 9: expr.Entity(case['parameter9']), 10: expr.Entity(124)}
        try:
            with component.parameter_scope(outer):
                try:
                    actual = active.evaluate(query)
                finally:
                    assert component.parameter_scopes == [outer], case['name']
        except runtime.UnsupportedScriptError as error:
            assert expected.get('error') in str(error), (case['name'], expected, error)
        else:
            assert 'value' in expected, case['name']
            wanted = expr.Entity(expected['value']) if case['kind'] == 'first' else expected['value']
            assert actual == wanted and type(actual) is type(wanted), (case['name'], actual, wanted)
        assert not component.parameter_scopes, case['name']
    print(f'PASS: {len(cases)} ally-filter cases against {revision}')


if __name__ == '__main__':
    main()
