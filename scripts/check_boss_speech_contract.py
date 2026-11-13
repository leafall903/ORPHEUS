"""Compare Boss speech policy through the pinned normal Python backend."""
import argparse
import copy
import importlib
import json
from pathlib import Path
from random import Random
import struct
import subprocess
import sys
from types import SimpleNamespace


def run_case(contract, case):
    ai = importlib.import_module('ow174.game.junkenstein_ai')
    scene = importlib.import_module('ow174.game.script.scene')
    runtime = importlib.import_module('ow174.game.script.runtime')
    graph = importlib.import_module('ow174.game.script.graph')
    expr = importlib.import_module('ow174.game.script.expr')
    owner = contract['input']['owner']
    integer = lambda key: None if case.get(key) is None else int(case[key], 16)
    world = scene.SceneWorld()
    world.bind(owner, hero=integer('hero'))
    world.bind(runtime.GAME_MODE_ENTITY)
    world.bind_voice_set(runtime.GAME_MODE_ENTITY, integer('mode_set'))
    killer = contract['input']['killer']
    if integer('killer') is not None:
        world.bind(killer, hero=integer('killer'))
    component = runtime.Component(owner, strict=True, world=world)
    world.bind_script_component(owner, component)
    model = copy.deepcopy(contract['input']['graph'])
    model['index'] = case['graph']
    model['nodes'][0]['pos'] = case['node']
    model['states'] = [case['node']]
    active = component.create(graph.Graph(model), init=False)
    active.initialized = True
    active.write((0, 1), expr.Entity(killer) if integer('killer') is not None else None)
    state = active.state(0)
    notices = []
    def notice(anchor, kind, fields):
        assert kind == 27500
        notices.append({'anchor': anchor, 'speaker': fields['+0x78']['+0x0'],
                        'stimulus': f"0x{fields['+0x88']:016X}", 'line': f"0x{fields['+0x90']:016X}"})
    player = SimpleNamespace(spawned=True, gone=False, has_body=True, team=0,
                             body=contract['input']['listener'],
                             client=SimpleNamespace(queue_combat_notice=notice))
    class Draw(Random):
        def random(self):
            return struct.unpack('>d', bytes.fromhex(case['draw_bits']))[0]
        def choice(self, rows):
            return rows[int(self.random() * len(rows))]
    original = ai.Random
    try:
        ai.Random = Draw
        ai.bind_boss_voice(SimpleNamespace(players=[player]), component, integer('hero'), integer('skin'))
    finally:
        ai.Random = original
    manager = world.voice_context(owner).lookup()
    definition = world.voice_set(owner)
    result = {'body_set': None if definition is None else f'0x{definition:016X}'}
    cue = integer('stimulus')
    stimulus = manager.load(cue)
    if stimulus is NotImplemented:
        return result | {'loaded': False}
    result.update(loaded=True, playable=stimulus.playable, mode=stimulus.mode,
                  allowed=manager.allow(stimulus))
    if not result['allowed']:
        return result
    try:
        handle = manager.submit(state, stimulus, tuple(case['targets']))
    except runtime.UnsupportedScriptError as error:
        return result | {'error': error.diagnostic['feature']}
    duration = manager.duration(handle, cue, stimulus.mode)
    assert duration is not NotImplemented
    return result | {'handle': handle, 'notices': notices,
                     'duration_bits': struct.pack('>d', duration).hex(), 'part': manager.part(state, 1)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--python-reference', type=Path, required=True)
    args = parser.parse_args()
    path = Path(__file__).resolve().parents[1] / 'fixtures/behavior/junkenstein-boss-speech-v1.json'
    contract = json.loads(path.read_text(encoding='utf-8'))
    assert (contract['schema_version'], contract['case_id'], contract['target_build']) == (1, 'junkenstein.boss-speech.v1', 104319)
    revision = subprocess.check_output(['git', '-C', str(args.python_reference), 'rev-parse', 'HEAD'], text=True).strip()
    assert revision == contract['source']['revision']
    subprocess.check_call(['git', '-C', str(args.python_reference), 'diff', '--quiet', 'HEAD', '--',
                           'ow174/game/junkenstein_ai.py', 'ow174/game/voicelines.py', 'ow174/game/script'])
    sys.path.insert(0, str(args.python_reference.resolve(strict=True)))
    cases, expected = contract['input']['cases'], contract['expected']['results']
    assert len(cases) == len(expected)
    for case, result in zip(cases, expected, strict=True):
        assert case['name'] == result['name']
        actual = run_case(contract, case)
        assert actual == result['output'], (case['name'], actual, result['output'])
    print(f'PASS: {len(cases)} Boss speech cases against {revision}')


if __name__ == '__main__':
    main()
