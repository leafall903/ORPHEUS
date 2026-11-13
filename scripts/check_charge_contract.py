"""Compare synthetic charge flow, hostility and damage tags through pinned normal Python services."""
import argparse, copy, importlib, json, struct, subprocess, sys
from pathlib import Path
from types import SimpleNamespace


def run_case(contract, case):
    scene = importlib.import_module('ow174.game.script.scene')
    runtime = importlib.import_module('ow174.game.script.runtime')
    graph = importlib.import_module('ow174.game.script.graph')
    expr = importlib.import_module('ow174.game.script.expr')
    teams = importlib.import_module('ow174.game.script.entity_team')
    combat = importlib.import_module('ow174.game.combat')
    importlib.import_module('ow174.game.script.nodes')
    owner, target = contract['input']['owner'], contract['input']['target']
    world = scene.SceneWorld()
    world.mode = int(contract['input']['mode'], 16)
    if case['kind'] == 'corpse':
        host_module = importlib.import_module('ow174.game.junkenstein_scene')
        controller = target + 1
        world.bind(target)
        world.bind(controller)
        world.possessors = set()
        world.set_possessor(controller, True)
        component = runtime.Component(target, strict=True, world=world)
        component.owner_dead = case['death_frame'] is not None
        world.bind_script_component(target, component)
        world.set_linked_controller(target, controller)
        world.set_current_possession(controller, target)
        world.ai_behavior_requests[controller] = {}
        actor = SimpleNamespace(entity=target, hero=int(case['hero'], 16), dead=component.owner_dead)
        host = SimpleNamespace(world=world, frame=case['frame'], frame_ms=16,
            player_bindings={}, combatants={target:(actor, None)},
            match=SimpleNamespace(players=[], combat=SimpleNamespace(vitals={target:SimpleNamespace(death_tick=case['death_frame'])})),
            combatant_component=lambda actor: component)
        host_module.JunkensteinSceneHost.before_units(host)
        return {'retire':target in world.corpse_retirements}
    # The pinned normal scene exposes the evidenced C1CF60 branch for this
    # declared body definition; the fixture is a synthetic service contract.
    world.bind(owner, definition=0x04000000000026F0)
    world.bind(target, target_tags=frozenset(int(v,16) for v in case.get('target_tags', [])))
    if case.get('context', True):
        flags = {owner: case.get('source_flags', 0x800001), target: case.get('target_flags', 0x1000001)}
        context = teams.Context(owner, True, True, True,
            lookup=lambda entity: teams.Target(entity, teams.FilterBits(flags[entity])) if entity in flags else None,
            watch_filter=None, watch_environment=None)
        world.set_entity_team_context(owner, context)
    component = runtime.Component(owner, strict=True, world=world)
    world.bind_script_component(owner, component)
    model = copy.deepcopy(contract['input']['graph'])
    initial = {338: 200.0, 342: 0.0, 2426: False, 842: False}
    initial.update({int(k):v for k,v in case.get('initial', {}).items()})
    values = {(1,k):v for k,v in initial.items()} | {(0,393):expr.Entity(target)}
    active = component.create(graph.Graph(model), values=values)
    victim = runtime.Component(target, strict=True, world=world)
    world.bind_script_component(target, victim)
    victim.set(602, case.get('no_charge', False))
    component.run_now()
    def value(item):
        if isinstance(item, dict) and 'asset' in item: return expr.Asset(int(item['asset'],16))
        if isinstance(item, dict) and 'entity' in item: return expr.Entity(item['entity'])
        return item
    def bits():
        return struct.pack('>d',float(component.entity_var(342).value())).hex()
    if case['kind'] == 'tags':
        active.write((0,1),value(case.get('variable')))
        hit = combat.hit_def(active.state(0),case['hit'])
        return {'tags':[f'0x{v:016X}' for v in hit.tags]}
    if case['kind'] == 'query':
        try: result=active.evaluate(case['query'])
        except runtime.UnsupportedScriptError as error:
            assert 'STU_A58C1E63' in str(error)
            return {'error':'STU_A58C1E63'}
        return {'hostile':result}
    results=[]
    last_frame=0
    for step in case['steps']:
        if step['op']=='set':
            for key,selected in step['values'].items(): component.set(int(key),selected)
            component.run_now()
        elif step['op']=='advance':
            for frame in range(last_frame+1,step['frame']+1): component.run_frame(frame)
            if step['frame']==last_frame: component.run_frame(last_frame)
            last_frame=step['frame']
        elif step['op']=='poll': component.run_now()
        elif step['op']=='damage':
            runtime.send_instance_message(active,0x0240000000000012,{0x13:float(step['amount']),0x15:expr.Entity(target)},owner)
            component.run_now()
        else: assert step['op']=='observe'
        results.append({'charge_bits':bits(),'waiting':active.state(4).active})
    return {'steps':results}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--python-reference',type=Path,required=True)
    args=parser.parse_args()
    fixture=Path(__file__).resolve().parents[1]/'fixtures/behavior/statescript-charge-v1.json'
    contract=json.loads(fixture.read_text(encoding='utf-8'))
    assert (contract['schema_version'],contract['case_id'],contract['target_build'])==(1,'statescript.charge.v1',104319)
    reference=args.python_reference.resolve(strict=True)
    revision=subprocess.check_output(['git','-C',str(reference),'rev-parse','HEAD'],text=True).strip()
    assert revision==contract['source']['revision']
    subprocess.check_call(['git','-C',str(reference),'diff','--quiet','HEAD','--','ow174/game/script','ow174/game/combat.py','ow174/game/junkenstein_scene.py'])
    sys.path.insert(0,str(reference))
    cases,expected=contract['input']['cases'],contract['expected']['results']
    assert len(cases)==len(expected)
    for case,result in zip(cases,expected,strict=True):
        assert case['name']==result['name']
        actual=run_case(contract,case)
        assert actual==result['output'],(case['name'],actual,result['output'])
    print(f'PASS: {len(cases)} charge/hostility/tag/corpse cases against {revision}')


if __name__=='__main__': main()
