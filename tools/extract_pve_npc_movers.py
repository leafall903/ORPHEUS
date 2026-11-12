"""Prepare an external NPC mover table from an explicit catalog and 104319 XML.

The output is research/runtime input, never a repository artifact. This does not
recover animated hitboxes, navigation, difficulty scaling or enemy AI.
"""

import argparse
import hashlib
import json
import sys
from pathlib import Path

from extract_game_movers import mover_of

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from ow174.game.mover import MoverData  # noqa: E402
from ow174.game.npc import load_catalog  # noqa: E402


def extract(catalog: Path, xml: Path) -> dict:
    records, sources = {}, {}
    for definition in load_catalog(catalog).values():
        path = xml / '003' / f'{definition.body & 0xFFFFFFFFFFFF:012X}.003.xml'
        raw = path.read_bytes()
        values = mover_of(raw.decode('utf-8-sig'))
        if values is None:
            raise ValueError(f'{definition.name} has no authored character mover')
        MoverData(**values)  # Reject fields the runtime cannot represent.
        records[f'0x{definition.body:016X}'] = {'name': definition.name, **values}
        sources[str(path.resolve())] = hashlib.sha256(raw).hexdigest()
    return {'format_version': 1, 'movers': records, 'source_sha256': sources,
            'limits': ['Authored character capsules; no animated hitboxes or AI restoration.']}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--catalog', type=Path, required=True)
    parser.add_argument('--xml', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.output.resolve().is_relative_to(ROOT):
        parser.error('NPC mover data must be written outside the source repository')
    document = extract(args.catalog, args.xml)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(document, indent=2) + '\n', encoding='utf-8')
    print(f"Prepared {len(document['movers'])} NPC mover records: {args.output}")


if __name__ == '__main__':
    main()
