"""Add the actual mine body/graph closure to an explicitly external graph bundle."""

import argparse
import gzip
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tools.extract_game_graphs import EnumTables, GraphXml, body_record  # noqa: E402

MINE = 0x0400000000002578


def prepare(base, xml_dir, owlib, output):
    if output.resolve().is_relative_to(ROOT):
        raise ValueError('mine metadata must stay outside the source repository')
    if output.resolve() == base.resolve():
        raise ValueError('the base bundle is immutable input')
    document = json.loads(gzip.decompress(base.read_bytes()))
    enums = EnumTables(owlib / 'TankLib/STU/Types')
    body = xml_dir / '003/000000002578.003.xml'
    record, queue = body_record(body, enums, None, 'Wrecking Ball mine')
    records = {row['index']: row for row in document['graphs']}
    sources = [base, body]
    seen = set()
    queue = sorted(queue)
    while queue:
        index = queue.pop(0)
        if index in seen:
            continue
        seen.add(index)
        path = xml_dir / '01B' / f'{index:012X}.01B.xml'
        parsed = GraphXml(path, enums)
        records[index] = parsed.record(index)
        sources.append(path)
        queue.extend(sorted(parsed.graph_refs()))
    document['graphs'] = [records[key] for key in sorted(records)]
    document.setdefault('entities', {})[f'0x{MINE:016X}'] = record
    document['ball_mine_sources'] = {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in sources}
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(gzip.compress(json.dumps(document, separators=(',', ':')).encode(), mtime=0))
    return sorted(seen)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    for key in ('base', 'xml', 'owlib', 'output'):
        parser.add_argument('--' + key, type=Path, required=True)
    args = parser.parse_args()
    print('Prepared mine graph closure:', [f'{i:04X}' for i in prepare(
        args.base, args.xml, args.owlib, args.output,
    )])
