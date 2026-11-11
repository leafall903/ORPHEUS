"""Correlate map ENTITY transforms, component instances and full definition defaults.

Exports external research inputs, not an executable scene. Definition components
and placed overrides remain separate until their native merge rules are known.
"""

import argparse
import hashlib
import json
import math
import re
import struct
import xml.etree.ElementTree as ET
from pathlib import Path

from tools.extract_game_graphs import NAME, NS, ROOT, EnumTables, StuXml, full_guid, scalar, tag


class WorldTypes(EnumTables):
    def __init__(self, types_dir: Path):
        super().__init__(types_dir)
        self.hashes = {
            name: int(value, 16)
            for path in sorted(types_dir.rglob('*.cs'))
            for value, name in re.findall(
                r'\[STU\(0x([0-9A-Fa-f]{8})[^\]]*\)\]\s*public class (\w+)',
                path.read_text(encoding='utf-8'),
            )
        }


class WorldXml(StuXml):
    """Retain DragonML map entries and their keys, including nested maps."""

    def value(self, element):
        element = self.resolve(element)
        if tag(element) != 'map':
            return super().value(element)
        if id(element) in self.encoding:
            raise ValueError('reference cycle through map')
        self.encoding.add(id(element))
        try:
            entries = []
            for entry in element:
                key = entry.get(NS + 'key')
                if key is None:
                    key_field = self.field(entry, 'dragon:key')
                    if key_field is None:
                        raise ValueError('map entry has no key')
                    key = self.value(key_field)
                else:
                    key = scalar(key)
                value = self.field(entry, 'dragon:value')
                if value is None:
                    null = next((child for child in entry if child.get(NAME) == 'dragon:value'), None)
                    if null is None or tag(null) != 'null':
                        raise ValueError('map entry has no value')
                entries.append({'key': key, 'value': self.value(value) if value is not None else None})
            return {'$': 'map', 'key_type': element.get(NS + 'key'),
                    'value_type': element.get(NS + 'value'), 'entries': entries}
        finally:
            self.encoding.discard(id(element))


def source(path: Path) -> dict:
    return {'name': path.name, 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}


def definition(path: Path, enums: EnumTables) -> dict:
    xml = WorldXml(path, enums)
    if tag(xml.root) != 'STUEntityDefinition':
        raise ValueError(f'{path.name}: expected STUEntityDefinition')
    components = xml.field(xml.root, 'm_componentMap')
    if components is not None and tag(components) != 'map':
        raise ValueError(f'{path.name}: invalid component map')
    values = xml.value(components) if components is not None else None
    keys = [entry['key'] for entry in values['entries']] if values else []
    if any(type(key) is not int or not 0 <= key <= 0xFFFFFFFFFFFFFFFF for key in keys):
        raise ValueError(f'{path.name}: invalid component key')
    if len(set(keys)) != len(keys):
        raise ValueError(f'{path.name}: duplicate component key')
    return {'source': source(path), 'fields': xml.fields(xml.root, skip={'m_componentMap'}),
            'components': values}


def add_animation_sets(data: dict, paths: list[Path]) -> None:
    catalogs, sources = {}, {}
    for path in paths:
        root = ET.parse(path).getroot()
        guid = full_guid(path.stem)
        if tag(root) != 'STUAnimBlendTreeSet' or guid >> 48 != 0x0040 or int(root.attrib['m_guid']) != guid:
            raise ValueError(f'{path.name}: animation set identity mismatch')
        key = f'0x{guid:016X}'
        if key in catalogs:
            raise ValueError(f'{path.name}: duplicate animation set')
        rows = next((child for child in root if child.get(NAME) == 'm_253EE7C8'), None)
        if rows is None or tag(rows) != 'array':
            raise ValueError(f'{path.name}: missing animation alias array')
        aliases = [int(row.text or '') for row in rows]
        if any(not 0x0C80000000000001 <= alias <= 0x0C80FFFFFFFFFFFF for alias in aliases):
            raise ValueError(f'{path.name}: invalid animation alias')
        catalogs[key] = [f'0x{alias:016X}' for alias in aliases]
        sources[key] = source(path)
    if catalogs:
        data['animation_sets'] = catalogs
        data['sources']['animation_sets'] = sources


def collect(map_index: int, entities_path: Path, chunk_path: Path,
            definitions_dir: Path, enums: WorldTypes) -> dict:
    if type(map_index) is not int or not 0 <= map_index <= 0xFFFFFFFFFFFF:
        raise ValueError('invalid map index')
    entities = ET.parse(entities_path).getroot()
    raw = chunk_path.read_bytes()
    if len(raw) < 16:
        raise ValueError('ENTITY header is truncated')
    count, _, offset, _ = struct.unpack_from('<4I', raw)
    if count != len(entities):
        raise ValueError('ENTITY and XML entity counts disagree')
    if offset < 16 or offset > len(raw):
        raise ValueError('ENTITY records offset is invalid')
    records, definitions = [], {}
    for index, entity in enumerate(entities):
        if entity.get('index') != str(index):
            raise ValueError(f'entity {index}: XML indexes must be contiguous and ordered')
        if offset + 24 > len(raw):
            raise ValueError(f'entity {index}: record header is truncated')
        size = struct.unpack_from('<I', raw, offset + 20)[0]
        if size < 136 or offset + size > len(raw):
            raise ValueError(f'entity {index}: invalid record size')
        body = offset + 24
        definition_guid, first, second = struct.unpack_from('<3Q', raw, body)
        if definition_guid != full_guid(entity.get('definition', '')):
            raise ValueError(f'entity {index}: definition GUIDs disagree')
        if definition_guid >> 48 != 0x0400:
            raise ValueError(f'entity {index}: definition is not a 003 asset')
        position = struct.unpack_from('<3f', raw, body + 24)
        scale = struct.unpack_from('<3f', raw, body + 36)
        rotation = struct.unpack_from('<4f', raw, body + 48)
        if not all(math.isfinite(value) for value in (*position, *scale, *rotation)):
            raise ValueError(f'entity {index}: nonfinite transform')
        component_count = struct.unpack_from('<I', raw, body + 64)[0]
        if component_count != len(entity):
            raise ValueError(f'entity {index}: component counts disagree')
        if 136 + component_count * 8 > size:
            raise ValueError(f'entity {index}: component table is truncated')
        components = []
        for number, component in enumerate(entity):
            entry = body + 112 + number * 8
            kind, relative = struct.unpack_from('<II', raw, entry)
            blob = entry + relative
            if blob + 12 > len(raw) or raw[blob:blob+4] != b'DUTS':
                raise ValueError(f'entity {index}: invalid component blob')
            # TankLib STUHeaderV1 + STUInstanceRecordV1: DUTS marks the
            # immutable V1 format; +4 is an instance count, not a version.
            instance_count, relative_instance = struct.unpack_from('<II', raw, blob + 4)
            table_size = 8 + instance_count * 8
            if not instance_count or blob + table_size > len(raw):
                raise ValueError(f'entity {index}: invalid immutable STU instance table')
            instance = blob + relative_instance
            if relative_instance < table_size or instance + 8 > len(raw):
                raise ValueError(f'entity {index}: invalid immutable STU instance offset')
            stu_hash = struct.unpack_from('<I', raw, instance)[0]
            if stu_hash != enums.hashes.get(tag(component)):
                raise ValueError(f'entity {index}: XML and binary component types disagree')
            # Every independently serialized component restarts DragonML IDs.
            xml = WorldXml(entities_path, enums, root=component)
            components.append({'class': tag(component), 'fields': xml.value(component),
                               'source': {'type': kind, 'blob_offset': blob,
                                          'stu_format': 'V1', 'instance_count': instance_count,
                                          'instance_offset': instance, 'stu_hash': f'{stu_hash:08X}'}})
        key = f'0x{definition_guid:016X}'
        if key not in definitions:
            path = definitions_dir / f'{definition_guid & 0xFFFFFFFFFFFF:012X}.003.xml'
            definitions[key] = definition(path, enums)
        records.append({'index': index, 'definition': key,
                        'placeable_header': raw[offset:offset+20].hex(),
                        'record_metadata': raw[body+68:body+112].hex(),
                        'identifiers': [f'0x{value:016X}' for value in (first, second)],
                        'position': position, 'scale': scale, 'rotation': rotation,
                        'components': components})
        offset += size
    return {'version': 1, 'map': f'0x{0x0800000000000000 | map_index:016X}',
            'sources': {'entities': source(entities_path), 'chunk': source(chunk_path)},
            'entities': records, 'definitions': definitions,
            'entity_records_end': offset, 'chunk_size': len(raw),
            'executable': False,
            'limitations': [
                'Definition defaults and placed overrides are separate; no merge semantics inferred',
                'Native component behavior, entity lifetimes and network allocation are not implemented',
            ]}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--map', required=True, type=lambda value: int(value, 16), dest='map_index')
    parser.add_argument('--entities', required=True, type=Path)
    parser.add_argument('--chunk', required=True, type=Path)
    parser.add_argument('--definitions', required=True, type=Path)
    parser.add_argument('--owlib', required=True, type=Path)
    parser.add_argument('--animation-set', action='append', type=Path, default=[])
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args(argv)
    if args.output.resolve().is_relative_to(ROOT) or args.output.suffix.lower() != '.json':
        parser.error('--output must be a JSON file outside the repository')
    enums = WorldTypes(args.owlib / 'TankLib' / 'STU' / 'Types')
    try:
        data = collect(args.map_index, args.entities, args.chunk, args.definitions, enums)
        add_animation_sets(data, args.animation_set)
    except (OSError, ValueError, KeyError, ET.ParseError) as error:
        parser.error(str(error))
    args.output.write_text(json.dumps(data, ensure_ascii=False, separators=(',', ':')), encoding='utf-8')
    print(f"{len(data['entities'])} entities, {len(data['definitions'])} full definitions -> {args.output}")
    print('Research inputs only; world instantiation is not implemented.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
