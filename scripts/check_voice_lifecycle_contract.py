"""Compare strict voice phases through the separately supplied pinned Python VM."""
import argparse
import copy
import importlib
import json
from pathlib import Path
import struct
import subprocess
import sys


def number(value):
    if isinstance(value, dict) and 'float' in value:
        return float(value['float'])
    return value


def encoded(value):
    if type(value) is float:
        return {'bits64': struct.pack('>d', value).hex()}
    return value


def run_case(contract, case):
    scene = importlib.import_module('ow174.game.script.scene')
    runtime = importlib.import_module('ow174.game.script.runtime')
    importlib.import_module('ow174.game.script.nodes')
    voice = importlib.import_module('ow174.game.script.voice_stimulus')
    expr = importlib.import_module('ow174.game.script.expr')
    graph = importlib.import_module('ow174.game.script.graph')
    owner = contract['input']['owner']
    world = scene.SceneWorld()
    world.bind(owner)
    component = runtime.Component(owner, strict=True, world=world)
    world.bind_script_component(owner, component)
    model = copy.deepcopy(contract['input']['graph'])
    model['nodes'][0]['fields'].update(case.get('fields', {}))
    active = component.create(graph.Graph(model), init=False)
    active.initialized = True
    for key in (80, 81, 82, 83):
        active.write((0, key), 0)
    def value(item):
        if isinstance(item, list):
            return tuple(value(v) for v in item)
        if isinstance(item, dict) and 'asset' in item:
            return expr.Asset(int(item['asset'], 16))
        if isinstance(item, dict) and 'entity' in item:
            return expr.Entity(item['entity'])
        return item
    active.write((0, 1), value(case.get('targets', [{'entity': owner}])))
    for key, selected in case.get('variables', {}).items():
        active.write((0, int(key)), value(selected))
    state = active.state(0)
    events, now, cursors = [], [number(case.get('clock', 100.0))], {}
    def record(event, result):
        events.append(event)
        return result
    def selected(key, sequence):
        index = cursors.get(key, 0)
        cursors[key] = index + 1
        return sequence[min(index, len(sequence)-1)]
    def clock():
        return record(['clock'], NotImplemented if now[0] is None else now[0])
    def load(guid):
        availability = selected('resource', case.get('resource', ['present']))
        result = (voice.Stimulus(guid, case.get('playable', True), case.get('mode', False))
                  if availability == 'present' else None if availability == 'absent' else NotImplemented)
        return record(['load', f'0x{guid:016X}'], result)
    def duration(handle, guid, mode):
        result = number(selected('duration', case.get('duration', [0.25])))
        return record(['duration', handle, mode], NotImplemented if result is None else result)
    def part(st, index):
        result = number(selected(f'part-{index}', case.get('parts', {}).get(str(index), [0.0])))
        return record(['part', index], NotImplemented if result is None else result)
    manager = voice.Manager(load=load,
        allow=lambda stimulus: record(['allow', stimulus.mode], case.get('allow', True)),
        submit=lambda st, stimulus, targets: record(['submit', list(targets)], case.get('handle', 17)),
        duration=duration, part=part)
    availability = case.get('manager', 'present')
    context = voice.Context(component, clock=clock, lookup=lambda: record(['manager'],
        manager if availability == 'present' else None if availability == 'absent' else NotImplemented))
    host = case.get('context', 'present')
    if host != 'unavailable':
        world.set_voice_context(owner, context if host == 'present' else None)
    original_context = world.voice_context
    world.voice_context = lambda entity: record(['context'], original_context(entity))
    result = []
    for step in case['steps']:
        if 'clock' in step:
            now[0] = number(step['clock'])
        outcome = None
        try:
            op = step['op']
            if op == 'begin':
                state.begin()
            elif op == 'advance':
                component.frame = step['frame']
                component.now = step['frame'] * 16
                component.update()
            elif op == 'drain':
                component.run_now()
            elif op == 'poll':
                context.poll()
            elif op == 'failure':
                outcome = context.notify_failure(state)
            elif op in ('abort', 'finish'):
                state.end(op == 'finish')
            elif op == 'destroy':
                component.destroy(active)
            elif op == 'set_output':
                active.write((0, 79), float(step['value']))
            elif op == 'timer':
                state.timer(step['parameter'])
            else:
                raise ValueError(f'unknown step {op}')
        except runtime.UnsupportedScriptError as error:
            result.append({'error': error.diagnostic['feature'], 'class': error.diagnostic['class'], 'events': events[:]})
            break
        exists = active.id in component.instances
        cache = None
        if exists and state.voice_started_at is not None:
            cache = {name.removeprefix('voice_'): encoded(getattr(state, name)) for name in voice.CACHES}
        values = None
        if exists:
            values = {}
            for key in (79, 80, 81, 82, 83):
                variable = active.find_var(0, key, create=False)
                values[str(key)] = None if variable is None else encoded(variable.value())
        result.append({'events': events[:], 'exists': exists,
                       'active': state.active if exists else None,
                       'registered': len(context.registered), 'voice': cache,
                       'values': values, 'outcome': outcome})
        events.clear()
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--python-reference', type=Path, required=True)
    args = parser.parse_args()
    path = Path(__file__).resolve().parents[1] / 'fixtures/behavior/statescript-voice-lifecycle-v1.json'
    contract = json.loads(path.read_text(encoding='utf-8'))
    assert (contract['schema_version'], contract['case_id'], contract['target_build']) == (
        1, 'statescript.voice-lifecycle.v1', 104319)
    revision = subprocess.check_output(['git', '-C', str(args.python_reference),
                                        'rev-parse', 'HEAD'], text=True).strip()
    assert revision == contract['source']['revision']
    subprocess.check_call(['git', '-C', str(args.python_reference), 'diff',
                           '--quiet', 'HEAD', '--', 'ow174/game/script'])
    sys.path.insert(0, str(args.python_reference.resolve(strict=True)))
    cases, results = contract['input']['cases'], contract['expected']['results']
    assert len(cases) == len(results)
    for case, expected in zip(cases, results, strict=True):
        assert case['name'] == expected['name']
        actual = run_case(contract, case)
        assert actual == expected['steps'], (case['name'], actual, expected['steps'])
    print(f'PASS: {len(cases)} voice-lifecycle cases against {revision}')


if __name__ == '__main__':
    main()
