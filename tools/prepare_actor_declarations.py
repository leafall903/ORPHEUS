"""Prepare authored actor declarations without inventing native runtime state.

Reads explicit XML component-map entries and local target tags. References are
recorded, never treated as proven inheritance or flattened into effective data.
The output remains external research input, not an installable Match catalog.
"""

import argparse
import hashlib
import json
import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from ow174.game.script.component_dependencies import declaration_mask  # noqa: E402
from ow174.game.script.definition_components import runtime_index  # noqa: E402

NS = '{https://yretenai.com/dragonml/v1}'


def reference(element, kind):
    value = element.get('GUID', '')
    if not re.fullmatch(r'[0-9A-Fa-f]{12}\.' + kind, value):
        raise ValueError(f'expected explicit .{kind} reference')
    return value.upper()


def declaration(path):
    path = Path(path).resolve()
    if not re.fullmatch(r'[0-9A-Fa-f]{12}\.003\.xml', path.name):
        raise ValueError('expected a named .003.xml entity definition')
    raw = path.read_bytes()
    root = ET.fromstring(raw)
    if root.tag != 'STUEntityDefinition':
        raise ValueError('expected STUEntityDefinition root')
    maps = [item for item in root if item.get(NS + 'name') == 'm_componentMap']
    if len(maps) != 1:
        raise ValueError('expected one explicit component map')
    components, local_tags, seen = [], [], set()
    for wrapper in maps[0]:
        key = int(wrapper.get(NS + 'key', '-1'))
        index = runtime_index(key)
        if key in seen or len(wrapper) != 1 or wrapper[0].tag != wrapper.tag:
            raise ValueError('duplicate, missing or polymorphic component value requires native resolution')
        seen.add(key)
        components.append({'schema_hash': f'{key:08X}', 'class': wrapper.tag, 'runtime_index': index})
        if index == 76:
            arrays = [item for item in wrapper[0] if item.get(NS + 'name') == 'm_targetTags']
            if len(arrays) > 1:
                raise ValueError('duplicate local target-tag array')
            for array in arrays:
                for item in array:
                    local_tags.append(reference(item, '06C'))
    refs = {}
    for field in ('m_04A8896C', 'm_8B9A461F'):
        values = [item for item in root if item.get(NS + 'name') == field]
        if len(values) > 1 or root.get(field) not in (None, '{null}'):
            raise ValueError('ambiguous entity reference')
        refs[field] = reference(values[0], '003') if values else None
    required = declaration_mask(int(item['schema_hash'], 16) for item in components)
    return {'definition': path.name[:-4].upper(), 'source': str(path),
            'sha256': hashlib.sha256(raw).hexdigest(), 'components': components,
            'resource_kind_enum': root.get('m_87916047'),
            'resource_reference_hash': root.get('m_BE8E52CD'),
            'declared_indices': sorted({item['runtime_index'] for item in components}),
            'compiled_requirement_mask': f'{required:064X}',
            'compiled_required_indices': [index for index in range(256) if required & (1 << index)],
            'component97_compile_required': bool(required & (1 << 97)),
            'local_target_tags': local_tags, 'references': refs,
            'effective_components': 'unknown', 'effective_target_tags': 'unknown',
            'filter_bits': 'unknown', 'component97_runtime_presence': 'unknown',
            'component50_runtime_presence': 'unknown', 'native_registry_groups': 'unknown'}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--definition', type=Path, action='append', required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args(argv)
    destination = args.output.resolve()
    if destination.is_relative_to(ROOT):
        raise ValueError('generated actor research must remain outside source')
    rows = [declaration(path) for path in args.definition]
    if len({row['definition'] for row in rows}) != len(rows):
        raise ValueError('duplicate entity definition')
    data = {'build': 104319, 'scope': 'authored declarations only; no effective native actor catalog',
            'installable_match_catalog': False, 'rows': rows}
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(data, indent=2) + '\n', encoding='utf-8')
    print(f'Prepared {len(rows)} declaration rows with compile requirements; runtime state remains unknown')


if __name__ == '__main__':
    main()
