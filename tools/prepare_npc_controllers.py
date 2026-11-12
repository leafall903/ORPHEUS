"""Prepare additive associated-controller initial graphs; never modify runtime inputs.

The host currently reconstructs only the controller namespace and script clock.
This extraction does not establish native AI acquisition or controller replication.
"""

import argparse
import gzip
import hashlib
import json
from pathlib import Path

from tools.extract_game_graphs import EnumTables, GraphXml, StuXml, body_record, guid_index, tag


def prepare(xml_dir, types_dir, catalog, names, existing=None):
    enums = EnumTables(types_dir)
    data = json.loads(json.dumps(catalog))
    selected = set(names)
    rows = [row for row in data['definitions'] if row['name'] in selected]
    if {row['name'] for row in rows} != selected:
        raise ValueError('requested NPC names must be present in the input catalog')
    extra = existing or {'version': 1, 'graphs': [], 'entities': {}}
    if extra.get('version') != 1 or set(extra) - {'version', 'graphs', 'entities'}:
        raise ValueError('existing supplement requires version1 and only graphs/entities')
    graphs = {row['index']: row for row in extra.get('graphs', [])}
    entities = dict(extra.get('entities', {}))
    unexpanded = set()
    for row in rows:
        body_path = xml_dir / '003' / f"{int(row['body'], 16) & 0xFFFFFFFFFFFF:012X}.003.xml"
        body = StuXml(body_path, enums)
        association = body.field(body.root, 'm_8B9A461F')
        reference = (association.get('GUID', '')
                     if association is not None and tag(association) == 'ref' else '')
        if not reference.endswith('.003') or not guid_index(reference):
            raise ValueError(f"{row['name']} has no associated003 controller definition")
        index = guid_index(reference)
        controller_path = xml_dir / '003' / f'{index:012X}.003.xml'
        definition, roots = body_record(controller_path, enums, None, row['name'] + 'Controller')
        key = f'0x{0x0400000000000000 | index:016X}'
        if key in entities and entities[key] != definition:
            raise ValueError(f'controller supplement conflicts with entity {key}')
        entities[key] = definition
        for root in sorted(roots):
            source = GraphXml(xml_dir / '01B' / f'{root:012X}.01B.xml', enums)
            record = source.record(root)
            if root in graphs and graphs[root] != record:
                raise ValueError(f'controller supplement conflicts with graph {root:04X}')
            graphs[root] = record
            unexpanded.update(source.graph_refs())
        row['controller_definition'] = key
        row['controller_evidence'] = {
            'association_field': 'm_8B9A461F', 'initial_graphs': sorted(roots),
            'body_xml_sha256': hashlib.sha256(body_path.read_bytes()).hexdigest(),
            'controller_xml_sha256': hashlib.sha256(controller_path.read_bytes()).hexdigest(),
        }
    supplement = {'version': 1, 'entities': entities,
                  'graphs': [graphs[index] for index in sorted(graphs)]}
    report = {'names': sorted(selected), 'entity_count': len(entities), 'graph_count': len(graphs),
              'unexpanded_graph_references': sorted(unexpanded - graphs.keys()),
              'scope': 'associated-controller initial graphs only; not complete native AI'}
    return data, supplement, report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('xml_dir', type=Path)
    parser.add_argument('owlib_dir', type=Path)
    parser.add_argument('catalog', type=Path)
    parser.add_argument('--name', action='append', required=True)
    parser.add_argument('--merge-supplement', type=Path)
    parser.add_argument('--output-catalog', type=Path, required=True)
    parser.add_argument('--output-supplement', type=Path, required=True)
    args = parser.parse_args()
    existing = (json.loads(gzip.decompress(args.merge_supplement.read_bytes()))
                if args.merge_supplement is not None else None)
    catalog, supplement, report = prepare(
        args.xml_dir, args.owlib_dir / 'TankLib' / 'STU' / 'Types',
        json.loads(args.catalog.read_text(encoding='utf-8')), args.name, existing,
    )
    args.output_catalog.write_text(json.dumps(catalog, indent=2) + '\n', encoding='utf-8')
    args.output_supplement.write_bytes(gzip.compress(json.dumps(supplement).encode(), mtime=0))
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
