"""Compare foreign-slot notifications through the normal pinned Python VM."""
import argparse
import importlib
import json
from pathlib import Path
import subprocess
import sys
from unittest import mock


def observe(contract, case):
    scene = importlib.import_module('ow174.game.script.scene')
    runtime = importlib.import_module('ow174.game.script.runtime')
    graphs = importlib.import_module('ow174.game.script.graph')
    expr = importlib.import_module('ow174.game.script.expr')
    importlib.import_module('ow174.game.script.nodes')
    world = scene.SceneWorld()
    owner, target = contract['input']['owner'], contract['input']['target']
    world.bind(owner)
    world.bind(target)
    foreign = runtime.Component(target, strict=True, world=world)
    foreign.set(6604, case['initial'])
    world.bind_script_component(target, foreign)
    component = runtime.Component(owner, strict=True, world=world)
    world.bind_script_component(owner, component)
    records = {record['index']:record for record in contract['input']['graphs']}
    # Supply only synthetic input records. The normal loader, SubScript,
    # live foreign Var slots and runtime change/abort handling remain intact.
    with mock.patch.object(graphs, '_records', lambda: records):
        active = component.create(graphs.graph(0x0C8C), values={(0,1):expr.Entity(target)})
        component.run_now()
        def snapshot():
            return {'active':active.state(0).active,
                    'complete':bool(component.entity_var(9).value()),
                    'children':sum(item.graph.index==0x7000 for item in component.instances.values()),
                    'subscriptions':len(foreign.entity_var(6604).watchers)}
        results = [snapshot()]
        for step in case['steps']:
            if step['op']=='set':
                foreign.set(6604, step['value'])
            elif step['op']=='abort':
                active.state(0).request(runtime.ABORT)
            else:
                assert step['op']=='poll'
            component.run_frame(step['frame'])
            results.append(snapshot())
        return results


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--python-reference',type=Path,required=True)
    args = parser.parse_args()
    reference = args.python_reference.resolve(strict=True)
    path = Path(__file__).resolve().parents[1]/'fixtures/behavior/statescript-foreign-notification-v1.json'
    contract = json.loads(path.read_text(encoding='utf-8'))
    assert (contract['schema_version'],contract['case_id'],contract['target_build'])==(1,'statescript.foreign-notification.v1',104319)
    revision = subprocess.check_output(['git','-C',str(reference),'rev-parse','HEAD'],text=True).strip()
    assert revision==contract['source']['revision']
    subprocess.check_call(['git','-C',str(reference),'diff','--quiet','HEAD','--','ow174/game/script'])
    sys.path.insert(0,str(reference))
    cases, results = contract['input']['cases'], contract['expected']['results']
    assert len(cases)==len(results)
    for case,result in zip(cases,results,strict=True):
        assert case['name']==result['name']
        actual = observe(contract,case)
        assert actual==result['output'],(case['name'],actual,result['output'])
    print(f'PASS: {len(cases)} foreign notification lifecycle flows against {revision}')


if __name__=='__main__':
    main()
