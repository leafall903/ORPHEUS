#!/usr/bin/env python3
"""Extract authored NPC AI associations and only graphs missing from an external base.

Outputs are prepared research inputs, not a runtime ownership/AI implementation.
Never rewrite the base bundle or put generated game data in the source repository.
"""

import argparse
import gzip
import hashlib
import json
import re
from pathlib import Path

from tools.extract_game_graphs import EnumTables, GraphXml, StuXml, entity_script_component, guid_index, tag

ROOT = Path(__file__).resolve().parents[1]
ENTITY = re.compile(r'0x0400[0-9A-Fa-f]{12}')
REFERENCE = re.compile(r'[0-9A-Fa-f]{1,12}\.003')
GRAPH = re.compile(r'0x0580[0-9A-Fa-f]{12}')


def fingerprint(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def extract(xml_dir, enums, definitions, base):
    """Keep authored associations and ordered component definitions, without flattening ownership."""
    if (not isinstance(definitions, list) or not definitions
            or any(not isinstance(row, dict) for row in definitions)):
        raise ValueError('NPC catalog has no definitions')
    bodies = [row.get('body') for row in definitions]
    if (any(not isinstance(body, str) or not ENTITY.fullmatch(body)
            or not int(body, 16) & 0xFFFFFFFFFFFF for body in bodies)
            or len({int(body, 16) for body in bodies}) != len(bodies)):
        raise ValueError('NPC bodies must be unique nonzero full .003 GUIDs')
    records = base.get('graphs') if isinstance(base, dict) else None
    if not isinstance(records, list) or any(not isinstance(row, dict) for row in records):
        raise ValueError('base graph catalog must have explicit graph records')
    indexes = [row.get('index') for row in records]
    if (any(type(index) is not int or not 0 < index <= 0xFFFF for index in indexes)
            or len(set(indexes)) != len(indexes)):
        raise ValueError('base graph indexes must be unique nonzero uint16 values')
    base_indexes = set(indexes)
    associations, controllers, roots = [], {}, set()
    for row in definitions:
        body = int(row['body'], 16)
        path = xml_dir / '003' / f'{body & 0xFFFFFFFFFFFF:012X}.003.xml'
        xml = StuXml(path, enums)
        if tag(xml.root) != 'STUEntityDefinition':
            raise ValueError(f'{path.name}: expected entity definition')
        association = xml.field(xml.root, 'm_8B9A461F')
        reference = association.get('GUID', '') if association is not None else None
        if (xml.root.get('m_8B9A461F') not in (None, '{null}')
                or (association is not None and (tag(association) != 'ref'
                    or not REFERENCE.fullmatch(reference) or not guid_index(reference)))):
            raise ValueError(f'{path.name}: invalid associated AI .003 definition')
        controller = f'0x{0x0400000000000000 | guid_index(reference):016X}' if reference else None
        associations.append({'body': f'0x{body:016X}', 'name': row.get('name'),
                             'hero': row.get('hero'),
                             'associated_ai_definition': controller, 'body_sha256': fingerprint(path)})
        if controller is None or controller in controllers:
            continue
        controller_path = xml_dir / '003' / f'{guid_index(reference):012X}.003.xml'
        controller_xml = StuXml(controller_path, enums)
        if tag(controller_xml.root) != 'STUEntityDefinition':
            raise ValueError(f'{controller_path.name}: expected associated entity definition')
        component, referenced = entity_script_component(controller_xml)
        entries = component.get('m_B634821A') if component is not None else None
        if entries is not None and not isinstance(entries, list):
            raise ValueError('controller component graph array is malformed')
        ordered = []
        for entry in entries or ():
            if not isinstance(entry, dict):
                raise ValueError('controller graph row is malformed')
            value = entry.get('m_graph')
            index = None
            if value is not None:
                if not isinstance(value, str) or not GRAPH.fullmatch(value):
                    raise ValueError('controller graph reference must be a .01B GUID')
                index = int(value, 16) & 0xFFFFFFFFFFFF
                if not 0 < index <= 0xFFFF:
                    raise ValueError('controller graph index is outside uint16')
            ordered.append({'graph': index, **{key: value for key, value in entry.items()
                                               if key not in ('$', 'm_graph')}})
        record = {'hero': None, 'name': f'AI definition {controller}', 'graphs': ordered,
                  'manager': None, 'weapons': [], 'script_component': component}
        controllers[controller] = {'record': record, 'source': str(controller_path),
                                   'source_sha256': fingerprint(controller_path)}
        roots.update(referenced)
    pending, visited, reused, missing, graphs = set(roots), set(), set(), set(), []
    while pending:
        index = min(pending)
        pending.remove(index)
        if index in visited:
            continue
        visited.add(index)
        if index in base_indexes:
            reused.add(index)
            continue
        if not 0 < index <= 0xFFFF:
            raise ValueError('associated controller graph index is outside uint16')
        path = xml_dir / '01B' / f'{index:012X}.01B.xml'
        if not path.is_file():
            missing.add(index)
            continue
        xml = GraphXml(path, enums)
        graphs.append({'record': xml.record(index), 'source': str(path), 'source_sha256': fingerprint(path)})
        pending.update(xml.graph_refs() - visited)
    return {'format_version': 1, 'scope': 'authored AI associations and additive graph inputs only',
            'associations': associations, 'controllers': controllers, 'roots': sorted(roots),
            'required_base_graphs': sorted(reused), 'missing': sorted(missing),
            'graphs': graphs, 'base_missing': base.get('missing', []),
            'runtime_ownership_accepted': False, 'autonomous_ai_accepted': False}


def check_output(output, xml_dir, owlib, npcs, base_graphs):
    target = output.resolve()
    protected = (ROOT, xml_dir, owlib, npcs.parent, base_graphs.parent)
    if any(target.is_relative_to(path.resolve()) for path in protected):
        raise ValueError('generated controller inputs must remain outside source and input directories')
    if target.exists() and target.stat().st_nlink > 1:
        raise ValueError('generated controller inputs cannot overwrite a shared hardlinked file')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--xml', type=Path, required=True)
    parser.add_argument('--owlib', type=Path, required=True)
    parser.add_argument('--npcs', type=Path, required=True)
    parser.add_argument('--base-graphs', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    try:
        check_output(args.output, args.xml, args.owlib, args.npcs, args.base_graphs)
    except ValueError as error:
        parser.error(str(error))
    base_bytes = args.base_graphs.read_bytes()
    base = json.loads(gzip.decompress(base_bytes) if base_bytes.startswith(b'\x1f\x8b') else base_bytes)
    definitions = json.loads(args.npcs.read_text(encoding='utf-8'))
    if definitions.get('format_version') != 1:
        parser.error('unsupported NPC catalog')
    data = extract(args.xml, EnumTables(args.owlib / 'TankLib/STU/Types'), definitions['definitions'], base)
    data['base_graphs'] = {'path': str(args.base_graphs), 'sha256': hashlib.sha256(base_bytes).hexdigest()}
    data['npc_catalog'] = {'path': str(args.npcs), 'sha256': fingerprint(args.npcs)}
    args.output.write_text(json.dumps(data, indent=2) + '\n', encoding='utf-8')
    print(f"{len(data['controllers'])} AI definitions;{len(data['graphs'])} additive graphs;"
          f"{len(data['required_base_graphs'])} reused base graphs;{len(data['missing'])} missing")


if __name__ == '__main__':
    main()
