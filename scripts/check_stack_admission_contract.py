"""Compare synthetic entity stack admission through the pinned normal VM."""
import argparse
import importlib
import json
from pathlib import Path
import subprocess
import sys


def observe(contract, case):
    runtime=importlib.import_module('ow174.game.script.runtime')
    helper=importlib.import_module('tests.test_script_runtime_budget')
    importlib.import_module('ow174.game.script.nodes')
    target={'$':'STUConfigVarDynamic','m_60DB8F99':1,'m_identifier':900}
    nodes=[helper.node(1,'STUStatescriptState',state=0)]
    starts,stops=[(1,'m_beginPlug')],[]
    for index,priority in enumerate(case['priorities']):
        pos=index+2
        nodes.append(helper.node(pos,'STUStatescriptStateStack',state=pos-1,
                     m_out_Var=target,m_value=case['values'][index],m_priority={'m_priority':priority}))
        starts.append((pos,'m_beginPlug'))
        stops.append((pos,'m_F198FD3A'))
    nodes.append(helper.node(0,'STUStatescriptEntry',m_outPlug=helper.plug(*starts)))
    message=len(nodes)
    nodes.append(helper.node(message,'STUStatescriptEntryGameMessage',
                 m_gameMessage={'m_gameMessage':'0x0240000000000001'},m_outPlug=helper.plug(*stops)))
    model=helper.graph(nodes,entries=(0,message))
    component=runtime.Component(42,strict=True)
    active=component.create(model)
    component.run_now()
    if case['release']:
        runtime.send_message(component,0x0240000000000001,{},42)
        component.run_now()
    return active.evaluate({'$':'STU_3ACE35FB','m_E50CF556':target,
                            'm_012B7AF7':{'m_priority':contract['input']['candidate']}})


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--python-reference',type=Path,required=True)
    reference=parser.parse_args().python_reference.resolve(strict=True)
    path=Path(__file__).resolve().parents[1]/'fixtures/behavior/statescript-stack-admission-v1.json'
    contract=json.loads(path.read_text(encoding='utf8'))
    assert (contract['schema_version'],contract['case_id'],contract['target_build'])==(1,'statescript.stack-admission.v1',104319)
    revision=subprocess.check_output(['git','-C',str(reference),'rev-parse','HEAD'],text=True).strip()
    assert revision==contract['source']['revision']
    subprocess.check_call(['git','-C',str(reference),'diff','--quiet','HEAD','--','ow174/game/script','tests/test_script_runtime_budget.py'])
    sys.path.insert(0,str(reference))
    for case in contract['input']['cases']:
        actual=observe(contract,case)
        assert actual==case['expected'],(case['name'],actual,case['expected'])
    print(f'PASS: 7 stack admission cases against {revision}')


if __name__=='__main__':
    main()
