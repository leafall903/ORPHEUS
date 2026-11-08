"""Prepare an external manual-NPC catalog from body graph health-pool wiring.

Example: --xml DUMP --owlib OWLIB --npc 1=1AC=Trooper --output EXTERNAL.json
Repeated --npc options supply selection key, .075 index and display name.
No difficulty scaling or enemy AI behavior is inferred by this extractor.
"""

import argparse
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from extract_game_graphs import EnumTables, GraphXml, StuXml, body_record, full_guid, guid_index  # noqa: E402
from extract_game_movers import mover_of  # noqa: E402

from ow174.game.mover import MoverData  # noqa: E402
from ow174.game.npc import Definition  # noqa: E402


def numbers(entries):
    return {guid_index(row['m_0D09D2D9']) & 0xFFFFFFFFFFFF: value['m_value']
            for row in entries or [] if isinstance(value := row.get('m_value'), dict)
            and value.get('$') in ('STUConfigVarInt', 'STUConfigVarFloat')}


def extract(folder, enums, selections):
    rows, sources, keys = [], {}, set()

    def fingerprint(path):
        sources[str(path.resolve())] = hashlib.sha256(path.read_bytes()).hexdigest()

    for selection in selections:
        key, index, name = selection.split('=', 2)
        if key not in '123456789' or len(key) != 1 or key in keys:
            raise ValueError('NPC selections need unique keys 1..9')
        keys.add(key)
        hero_path = folder / '075' / f'{int(index, 16):012X}.075.xml'
        xml = StuXml(hero_path, enums)
        ref = xml.field(xml.root, 'm_gameplayEntity')
        body_ref = ref.get('GUID') if ref is not None else None
        if body_ref is None or not body_ref.endswith('.003'):
            raise ValueError('NPC hero has no body reference')
        body_path = folder / '003' / f'{body_ref}.xml'
        record, _ = body_record(body_path, enums, None, name)
        fingerprint(hero_path)
        fingerprint(body_path)
        movement = mover_of(body_path.read_bytes().decode('utf-8'))
        pools, evidence, unapplied = {}, [], []
        for entry in record['graphs']:
            if entry['graph'] is None:
                continue
            path = folder / '01B' / f"{entry['graph']:012X}.01B.xml"
            graph = GraphXml(path, enums).record(entry['graph'])
            health_nodes = [n for n in graph['nodes'] if n['class'] == 'STUStatescriptStateHealthPool']
            if not health_nodes:
                continue
            fingerprint(path)
            values = numbers((graph['fields'].get('m_publicSchema') or {}).get('m_entries'))
            values.update(numbers(entry.get('m_1EB5A024')))
            for node in health_nodes:
                fields = node['fields']
                amount = fields.get('m_amount')
                if not isinstance(amount, dict) or amount.get('$') != 'STUConfigVarDynamic':
                    unapplied.append({'graph': entry['graph'], 'node': node['pos'],
                                      'reason': 'dynamic or conditional pool outside base-schema extraction'})
                    continue
                setters = [n for n in graph['nodes'] if n['class'] == 'STUStatescriptActionSetVar'
                    and n['fields'].get('m_out_Var') == amount
                    and [node['pos'], 'm_beginPlug'] in n['fields'].get('m_outPlug', {}).get('links', [])]
                if len(setters) != 1:
                    raise ValueError('NPC pool needs an unambiguous directly wired amount setter')
                source = setters[0]['fields']['m_value']
                if source.get('$') != 'STUConfigVarDynamic' or source.get('m_60DB8F99') != 0:
                    raise ValueError('NPC pool amount is not a static instance-schema variable')
                var, kind = source['m_identifier'], fields['m_7E8B666C']
                if kind not in (0, 1, 2) or kind in pools or var not in values:
                    raise ValueError('NPC pool type/default is unsupported or ambiguous')
                pools[kind] = values[var]
                evidence.append({'graph': entry['graph'], 'pool_node': node['pos'],
                    'setter_node': setters[0]['pos'], 'variable': var, 'pool_kind': kind})
        definition = Definition(full_guid(f'{int(index, 16):012X}.075'), full_guid(body_ref),
                                name, pools.get(0, 0), pools.get(1, 0), pools.get(2, 0),
                                MoverData(**movement) if movement is not None else None)
        rows.append({'key': key, 'hero': f'0x{definition.hero:016X}', 'body': f'0x{definition.body:016X}',
            'name': name, 'health': definition.health, 'armour': definition.armour,
            'shields': definition.shields, 'mover': movement,
            'pool_evidence': evidence, 'unapplied_pool_nodes': unapplied})
    return {'format_version': 1, 'definitions': rows, 'source_sha256': sources,
            'limits': ['Base body pools only; no mission difficulty scaling, AI or client acceptance.']}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--xml', type=Path, required=True)
    parser.add_argument('--owlib', type=Path, required=True)
    parser.add_argument('--npc', action='append', required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.output.resolve().is_relative_to(ROOT):
        parser.error('NPC data must be written outside the source repository')
    data = extract(args.xml, EnumTables(args.owlib / 'TankLib/STU/Types'), args.npc)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(data, indent=2) + '\n', encoding='utf-8')
    print(f"Prepared {len(data['definitions'])} NPC definitions: {args.output}")


if __name__ == '__main__':
    main()
