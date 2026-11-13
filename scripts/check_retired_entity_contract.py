"""Compare explicit enemy retirement facts with normal pinned Python services."""
import argparse
import importlib
import json
from pathlib import Path
import subprocess
import sys


def observe(contract, case):
    scene = importlib.import_module('ow174.game.script.scene')
    runtime = importlib.import_module('ow174.game.script.runtime')
    expr = importlib.import_module('ow174.game.script.expr')
    helper = importlib.import_module('tests.test_script_array_insert')
    entity_team = importlib.import_module('ow174.game.script.entity_team')
    world = scene.SceneWorld()
    world.possessors = set()
    owner, target = 123, case['entity']
    world.bind(owner)
    world.bind(target)
    state = case['state']
    if state in ('running', 'faulted', 'retired'):
        component = runtime.Component(target, strict=True, world=world)
        if 'value' in case:
            component.set(6604, case['value'])
        world.bind_script_component(target, component)
        if state == 'faulted':
            component.fault = RuntimeError('synthetic unavailable backend')
    elif state == 'absent':
        world.bind_script_component(target, None)
    if state == 'retired':
        world.unbind(target)
    if state != 'unavailable':
        world.set_entity_query_context(owner)
        world.set_entity_team_context(owner, entity_team.Context(
            owner, True, True, True,
            lookup=lambda identifier: None if state == 'retired' else entity_team.Target(
                identifier, entity_team.FilterBits(1 << (23 + case['team']))),
        ))
    component, active, _ = helper.setup(world=world, current=expr.Entity(target))
    world.bind_script_component(owner, component)
    output = {}
    for key, config in contract['input']['queries'].items():
        try:
            output[key] = active.evaluate(config)
        except runtime.UnsupportedScriptError:
            output[key] = 'unavailable'
    return output


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--python-reference', type=Path, required=True)
    args = parser.parse_args()
    reference = args.python_reference.resolve(strict=True)
    fixture = Path(__file__).resolve().parents[1] / 'fixtures/behavior/statescript-retired-entity-v1.json'
    contract = json.loads(fixture.read_text(encoding='utf-8'))
    assert (contract['schema_version'], contract['case_id'], contract['target_build']) == (1, 'statescript.retired-entity.v1', 104319)
    revision = subprocess.check_output(['git', '-C', str(reference), 'rev-parse', 'HEAD'], text=True).strip()
    assert revision == contract['source']['revision']
    subprocess.check_call(['git', '-C', str(reference), 'diff', '--quiet', 'HEAD', '--', 'ow174/game/script'])
    sys.path.insert(0, str(reference))
    cases, results = contract['input']['cases'], contract['expected']['results']
    assert len(cases) == len(results)
    for case, result in zip(cases, results, strict=True):
        assert case['name'] == result['name']
        actual = observe(contract, case)
        assert actual == result['output'], (case['name'], actual, result['output'])
    print(f'PASS: {len(cases)} retired/live/unavailable entity cases against {revision}')


if __name__ == '__main__':
    main()
