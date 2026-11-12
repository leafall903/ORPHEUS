"""Prepare owned body tags as external inputs for the disclosed charge host."""

import argparse
import hashlib
import json
import xml.etree.ElementTree as ET
from pathlib import Path

from ow174.game import npc
from ow174.game.script import graph

NAME = '{https://yretenai.com/dragonml/v1}name'


def extract(directory, records):
    rows = []
    for body, record in sorted(records.items()):
        if type(body) is not int or body >> 48 != 0x400 or not body & 0xFFFFFFFFFFFF:
            raise ValueError('expected full nonzero body GUID')
        path = directory / f'{body & 0xFFFFFFFFFFFF:012X}.003.xml'
        raw = path.read_bytes()
        components = [entry for entry in ET.fromstring(raw).iter('STUTargetTagComponent')
                      if entry.get(NAME) == 'dragon:value']
        if len(components) > 1:
            raise ValueError('multiple owned target-tag components')
        tags, nulls = [], 0
        if components:
            for item in components[0].iter():
                guid = item.get('GUID')
                if guid == '000000000000.001':
                    nulls += 1
                elif guid:
                    if not guid.endswith('.06C'):
                        raise ValueError('unexpected target-tag asset type')
                    tags.append(int(guid.split('.')[0], 16))
        if len(tags) != len(set(tags)):
            raise ValueError('duplicate owned tag')
        rows.append({'body': f'{body:016X}', 'hero': record['hero'], 'name': record.get('name'),
            'owned_tag_component_present': bool(components), 'owned_tags': tags, 'null_tag_slots': nulls,
            'source': str(path.resolve()), 'sha256': hashlib.sha256(raw).hexdigest()})
    return {'format_version': 1, 'build': 104319, 'count': len(rows), 'bodies': rows,
        'scope': ('owned raw tags only;native category bits,effective inherited tags '
                  'and live modifiers are not established')}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('xml_directory', type=Path)
    parser.add_argument('output', type=Path)
    parser.add_argument('--npc-catalog', type=Path)
    parser.add_argument('--include-dva-mech', action='store_true',
                        help='include the alternate 104319 D.Va mech body')
    args = parser.parse_args()
    records = dict(graph.bodies())
    if args.include_dva_mech:
        records.setdefault(0x0400000000000407, {'hero': '0x02E000000000007A', 'name': 'D.Va (mech)'})
    if args.npc_catalog is not None:
        for definition in npc.load_catalog(args.npc_catalog).values():
            if definition.body in records:
                raise ValueError('duplicate NPC body')
            records[definition.body] = {'hero': f'0x{definition.hero:016X}', 'name': definition.name}
    data = extract(args.xml_directory, records)
    data['source_runtime_sha256'] = hashlib.sha256(graph.GRAPHS_PATH.read_bytes()).hexdigest()
    if args.npc_catalog is not None:
        data['npc_catalog_sha256'] = hashlib.sha256(args.npc_catalog.read_bytes()).hexdigest()
    args.output.write_text(json.dumps(data, indent=2) + '\n', encoding='utf-8')


if __name__ == '__main__':
    main()
