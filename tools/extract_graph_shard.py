"""Prepare only missing actor/graph inputs beside an immutable canonical bundle.

Example: --base RUNTIME/data/game_graphs_174.json.gz --xml XML --owlib OWLIB
         --entity 2658 --graph 01E7 --output EXTERNAL.json
The output is read with ORPHEUS_EXTRA_GRAPH_BUNDLES; no runtime files are rewritten.
"""

import argparse
import gzip
import hashlib
import json
import sys
from collections import deque
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(Path(__file__).resolve().parents[1] / "src"),
                str(Path(__file__).resolve().parents[1])]

from extract_game_graphs import EnumTables, GraphXml, body_record, entity_index  # noqa: E402

from ow174.game.script.graph import merge_extra_bundle  # noqa: E402


def prepare(base_path, xml, enums, entities, roots):
    raw = base_path.read_bytes()
    base = json.loads(gzip.decompress(raw) if raw.startswith(b'\x1f\x8b') else raw)
    base_graphs = {row['index'] for row in base['graphs']}
    result = {'version': 1, 'entities': {}, 'graphs': [], 'source_sha256': {
        str(base_path.resolve()): hashlib.sha256(raw).hexdigest()}}
    queue, seen, missing = deque(roots), set(), []
    for index in sorted(set(entities)):
        path = xml / '003' / f'{index:012X}.003.xml'
        record, used = body_record(path, enums, None, f'actor {index:04X}')
        result['entities'][f'0x{0x0400000000000000 | index:016X}'] = record
        result['source_sha256'][str(path.resolve())] = hashlib.sha256(path.read_bytes()).hexdigest()
        queue.extend(sorted(used))
    while queue:
        index = queue.popleft()
        if index in seen or index in base_graphs:
            continue
        seen.add(index)
        path = xml / '01B' / f'{index:012X}.01B.xml'
        if not path.is_file():
            missing.append(index)
            continue
        parsed = GraphXml(path, enums)
        result['graphs'].append(parsed.record(index))
        result['source_sha256'][str(path.resolve())] = hashlib.sha256(path.read_bytes()).hexdigest()
        queue.extend(sorted(parsed.graph_refs()))
    if missing:
        raise ValueError('missing actor graph inputs: ' + ', '.join(f'{index:04X}' for index in missing))
    result['graphs'].sort(key=lambda row: row['index'])
    # Validate conflicts against exactly the supplied canonical input, never an
    # already loaded runtime process whose environment might contain other shards.
    merge_extra_bundle(base, result)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base', type=Path, required=True)
    parser.add_argument('--xml', type=Path, required=True)
    parser.add_argument('--owlib', type=Path, required=True)
    parser.add_argument('--entity', action='append', type=entity_index, default=[])
    parser.add_argument('--graph', action='append', type=entity_index, default=[])
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if not args.entity and not args.graph:
        parser.error('select at least one entity or graph')
    if args.output.resolve().is_relative_to(ROOT) or args.output.resolve() == args.base.resolve():
        parser.error('write the shard outside the source repository and canonical input')
    try:
        result = prepare(args.base, args.xml, EnumTables(args.owlib / 'TankLib/STU/Types'),
                         args.entity, args.graph)
    except (OSError, ValueError) as exc:
        parser.error(str(exc))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, separators=(',', ':')) + '\n', encoding='utf-8')
    print(f"Prepared {len(result['entities'])} entity records and {len(result['graphs'])} missing graphs: "
          f'{args.output}')


if __name__ == '__main__':
    main()
