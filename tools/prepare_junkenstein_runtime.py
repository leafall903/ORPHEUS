#!/usr/bin/env python3
"""Build a separate 104319 graph bundle with the Junkenstein enemy definitions."""

import argparse
import gzip
import json
from pathlib import Path

from extract_game_graphs import (
    ROOT,
    EnumTables,
    GraphXml,
    StuXml,
    ai_weapon_controls,
    ai_weapon_identifiers,
    body_record,
    target_tags,
)

# The common enemies use the controller named by the body. The bosses
# use the controller overrides in the encounter's CreateHero resources.
ENEMIES = {
    0x175: (0x16BE, 0x16C0),  # Zomnic
    0x176: (0x16D6, 0x16D7),  # Shock-Tire
    0x177: (0x171C, 0x171D),  # Zombardier
    0x002: (0x0022, 0x1958),  # Reaper
    0x004: (0x00E4, 0x172E),  # Mercy / The Witch, authored by 0CC0 and 1063
    0x016: (0x26F0, 0x1DD3),  # Symmetra
    0x040: (0x028F, 0x1959),  # Roadhog
    0x065: (0x0380, 0x16FB),  # Junkrat
}


def prepare(base: dict, xml: Path, enums: EnumTables) -> dict:
    if base.get('version') != 1 or 'junkenstein' in base:
        raise ValueError('expected a version 1 base bundle without an encounter overlay')
    graphs = {record['index']: record for record in base['graphs']}
    if len(graphs) != len(base['graphs']):
        raise ValueError('base bundle has duplicate graph indexes')
    missing = set(base['missing'])
    original = set(graphs)
    entities, pairs = {}, {}
    pending = {
        0x0079,  # 0019.04A, the Witch's staff effect on its selected target.
        0x0CA7,  # The door's own script, authored on the map entity.
        0x156C,  # The Photon Barrier receiver started by 156B/13.
    }
    mode_path = xml / '003' / '0000000016B2.003.xml'
    mode_entity, roots = body_record(mode_path, enums, None, 'Junkenstein mode entity')
    if mode_entity['script_component'] is None or roots:
        raise ValueError(f'{mode_path.name} must supply the empty mode script component')
    entities['0x04000000000016B2'] = mode_entity
    barrier_path = xml / '003' / '0000000026B0.003.xml'
    barrier, roots = body_record(barrier_path, enums, None, 'Summoner Photon Barrier')
    if barrier['script_component'] is None or roots != {0x0033}:
        raise ValueError(f'{barrier_path.name} must supply the authored barrier health graph')
    barrier['target_tags'] = target_tags(StuXml(barrier_path, enums))
    entities['0x04000000000026B0'] = barrier
    pending.update(roots)

    ghost_path = xml / '003' / '000000003C53.003.xml'
    ghost, roots = body_record(ghost_path, enums, None, 'Vengeful Ghost')
    if ghost['script_component'] is None or roots != {0x2B72}:
        raise ValueError(f'{ghost_path.name} must supply the authored ghost graph')
    ghost['target_tags'] = target_tags(StuXml(ghost_path, enums))
    entities['0x0400000000003C53'] = ghost
    pending.update(roots)

    for hero, (body, controller) in ENEMIES.items():
        hero_key = f'0x{0x02E0000000000000 | hero:016X}'
        body_key = f'0x{0x0400000000000000 | body:016X}'
        controller_key = f'0x{0x0400000000000000 | controller:016X}'
        pairs[hero_key] = {'body': body_key, 'controller': controller_key}
        for index, key in ((body, body_key), (controller, controller_key)):
            path = xml / '003' / f'{index:012X}.003.xml'
            record, roots = body_record(path, enums, hero_key if index == body else None,
                                        f'Junkenstein {index:04X}', include_ai_weapons=True)
            if record['script_component'] is None or not roots:
                raise ValueError(f'{path.name} has no authored script roots')
            if (index == body and hero in (0x175, 0x176, 0x177)
                    and record.get('ai_association') != int(controller_key, 16)):
                raise ValueError(f'{path.name} does not declare controller {controller:04X}')
            if index == controller:
                record['ai_weapon_controls'] = ai_weapon_controls(path, enums)
                record['ai_weapon_identifiers'] = {
                    str(slot): sorted(values) for slot, values in ai_weapon_identifiers(path, enums).items()
                }
            entities[key] = record
            pending.update(roots)

    while pending:
        index = min(pending)
        pending.remove(index)
        if index in graphs:
            continue  # A validated base graph and its closure stay intact.
        source = xml / '01B' / f'{index:012X}.01B.xml'
        if not source.is_file():
            if index in missing:
                continue  # Preserve known gaps in the accepted base.
            raise ValueError(f'new encounter dependency is missing: {source.name}')
        authored = GraphXml(source, enums)
        graphs[index] = authored.record(index)
        pending.update(authored.graph_refs())
        missing.discard(index)

    player_tags = {}
    for body_key in base['bodies']:
        index = int(body_key, 16) & 0xFFFFFFFFFFFF
        path = xml / '003' / f'{index:012X}.003.xml'
        player_tags[body_key] = target_tags(StuXml(path, enums))
    encounter = {'pairs': pairs, 'entities': entities, 'added_graphs': sorted(set(graphs) - original),
                 'player_target_tags': player_tags}
    return {**base, 'graphs': [graphs[index] for index in sorted(graphs)], 'missing': sorted(missing),
            'junkenstein': encounter}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base', type=Path, required=True, help='Accepted game_graphs_174.json.gz')
    parser.add_argument('--xml', type=Path, required=True, help='Local 003 and 01B XML dump')
    parser.add_argument('--owlib', type=Path, required=True, help='OWLib checkout used for the dump')
    parser.add_argument('--output', type=Path, required=True, help='New bundle outside the repository')
    args = parser.parse_args()
    if args.output.resolve().is_relative_to(ROOT):
        parser.error('--output must be outside the repository')
    try:
        base = json.loads(gzip.decompress(args.base.read_bytes()))
        result = prepare(base, args.xml, EnumTables(args.owlib / 'TankLib' / 'STU' / 'Types'))
        data = json.dumps(result, separators=(',', ':'), ensure_ascii=False).encode('utf-8')
        payload = gzip.compress(data, mtime=0)
        with args.output.open('xb') as output:
            output.write(payload)
    except (OSError, ValueError, KeyError) as exc:
        parser.error(str(exc))
    enemy = result['junkenstein']
    print(f'{len(enemy["pairs"])} enemy pairs and {len(enemy["added_graphs"])} additional graphs')


if __name__ == '__main__':
    main()
