"""Compare synthetic hostile filters against the pinned scene implementation."""
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
    contract = json.loads((Path(__file__).resolve().parents[1] /
                           'fixtures/behavior/statescript-hostile-filter-v1.json').read_text(encoding='utf-8'))
    assert (contract['schema_version'], contract['case_id'], contract['target_build']) == (
        1, 'statescript.hostile-filter.v1', 104319)
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
        world.bind(123)
        world.bind(124, target_tags=None if case['tags'] is None else
                   [0x0D60000000000000 + tag for tag in case['tags']])
        component, active, _ = helper.setup(world=world, current=case['predicate'])
        active.graph.index = 0x01A3
        world.bind_script_component(123, component)
        flags = {123: case['source_flags'], 124: case['target_flags']}
        world.array_filter_inputs = lambda entity: (True, flags[entity])
        world.set_entity_team_context(123, teams.Context(123, True, True, True,
            lookup=lambda entity: teams.Target(entity, teams.FilterBits(flags[entity]))))
        query = {'$': 'STU_AC829876', 'm_entity': {'$': 'STUConfigVarDynamic',
                 'm_identifier': 2, 'm_60DB8F99': False}, 'm_filter': case['filter']}
        active.write((0, 2), expr.Entity(124))
        try:
            actual = active.evaluate(query)
        except runtime.UnsupportedScriptError as error:
            assert expected.get('error') == 'STU_AC829876' and 'STU_AC829876' in str(error), case['name']
        else:
            assert 'value' in expected and actual is expected['value'], (case['name'], actual, expected)
    print(f'PASS: {len(cases)} hostile-filter cases against {revision}')

    contract = json.loads((Path(__file__).resolve().parents[1] /
                           'fixtures/behavior/statescript-combat-target-v1.json').read_text(encoding='utf-8'))
    assert (contract['schema_version'], contract['case_id'], contract['target_build']) == (
        1, 'statescript.combat-target.v1', 104319)
    assert contract['source']['revision'] == revision
    cases, results = contract['input']['cases'], contract['expected']['results']
    assert len(cases) == len(results)
    for case, expected in zip(cases, results, strict=True):
        assert case['name'] == expected['name']
        world = scene.SceneWorld(mode=next(iter(scene.JUNKENSTEIN_MODES)))
        world.bind(123, target_tags=[])
        component, active, _ = helper.setup(world=world)
        active.graph.index = 0x0CCD
        world.bind_script_component(123, component)
        flags = {123: 8388609}
        for body in case['bodies']:
            entity = body['entity']
            world.bind(entity, target_tags=None if body['tags'] is None else
                       [0x0D60000000000000 + tag for tag in body['tags']])
            flags[entity] = body['flags']
            world.set_entity_team_context(entity, teams.Context(entity, True, True, True))
        if case['roster_available']:
            world.array_filter_inputs = lambda entity: (True, flags[entity])
        else:
            # A registered candidate makes the missing scene callback observable.
            world.set_entity_team_context(123, teams.Context(123, True, True, True))
        query = {'$': 'STU_B3E67A36', 'm_9C7BD8B8': {'$': 'STU_357DABBB',
                 'm_7D7C86A1': '0x0D60000000000001', 'm_resourceKey': None},
                 'm_filter': {'$': 'STUConfigVarFilter', 'm_84065FD1': 1, 'm_4EA686DB': 16}}
        try:
            actual = active.evaluate(query)
        except runtime.UnsupportedScriptError as error:
            assert expected.get('error') == 'STU_B3E67A36' and 'STU_B3E67A36' in str(error), case['name']
        else:
            assert 'entity' in expected and actual == expr.Entity(expected['entity']), (case['name'], actual, expected)
    print(f'PASS: {len(cases)} combat-target cases against {revision}')


if __name__ == '__main__':
    main()
