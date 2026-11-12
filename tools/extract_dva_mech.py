"""Extract104319 D.Va0407 body and missing graph dependencies outside the repo.

Uses the pinned DataTool XML and existing runtime graphs as immutable input.
This prepares explicit static-test input; it does not activate native mech
possession, change normal hero selection or prove original-client behavior.
"""

import argparse
import hashlib
import json
from pathlib import Path

from ow174.game.script import graph
from tools.extract_game_graphs import EnumTables, GraphXml, body_record

ROOT = Path(__file__).resolve().parents[1]


def extract(folder, enums):
    path = folder / '003/000000000407.003.xml'
    body, pending = body_record(path, enums, '0x02E000000000007A', 'D.Va mech')
    known, records, missing = set(graph.graph_indexes()), {}, []
    sources = {str(path): hashlib.sha256(path.read_bytes()).hexdigest()}
    while pending:
        index = min(pending)
        pending.remove(index)
        if index in known or index in records or index in missing:
            continue
        path = folder / '01B' / f'{index:012X}.01B.xml'
        if not path.exists():
            missing.append(index)
            continue
        record = GraphXml(path, enums).record(index)
        records[index] = record
        sources[str(path)] = hashlib.sha256(path.read_bytes()).hexdigest()
        pending.update(graph.Graph(record).graph_refs() - known - set(records))
    return {'build': 104319, 'body': body, 'graphs': list(records.values()),
            'missing': missing, 'source_sha256': sources,
            'limits': ['Explicit possessed-mech static fixture; native spawning/possession is unaccepted.']}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--xml', type=Path, required=True)
    parser.add_argument('--owlib', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.output.resolve().is_relative_to(ROOT):
        parser.error('Mech supplement must be written outside the source repository')
    result = extract(args.xml, EnumTables(args.owlib / 'TankLib/STU/Types'))
    if result['missing']:
        parser.error(f"Missing graph XML: {result['missing']}")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + '\n', encoding='utf8')
    print(f"Prepared mech0407 and {len(result['graphs'])} missing graphs: {args.output}")


if __name__ == '__main__':
    main()
