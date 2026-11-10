#!/usr/bin/env python3
"""Compose prepared Junkenstein and Rialto runtime packs outside the repository."""

import argparse
import filecmp
import gzip
import hashlib
import json
import shutil
import tempfile
from pathlib import Path

GRAPHS = Path('data/game_graphs_174.json.gz')
MOVERS = Path('data/game_movers_174.json')
PROVENANCE = Path('provenance.json')


def union(left, right, name):
    conflicts = sorted(key for key in left.keys() & right.keys() if left[key] != right[key])
    if conflicts:
        raise ValueError(f'{name} has conflicting keys: {conflicts}')
    return left | right


def graph_index(records):
    result = {row['index']: row for row in records}
    if len(result) != len(records):
        raise ValueError('input graph bundle contains duplicate indexes')
    return result


def build(junkenstein, rialto, output):
    if output.exists() or output.is_relative_to(Path(__file__).resolve().parents[1]):
        raise ValueError('output must be a new directory outside the repository')
    if any(output.is_relative_to(source) for source in (junkenstein, rialto)):
        raise ValueError('output must be outside both input packs')

    left, right = (json.loads(gzip.decompress((root / GRAPHS).read_bytes())) for root in (junkenstein, rialto))
    if left.get('version') != 1 or right.get('version') != 1:
        raise ValueError('both inputs must use graph bundle version 1')
    if 'junkenstein' not in left or 'map_scripts' not in right or 'entities' not in right:
        raise ValueError('expected a prepared Junkenstein pack and a prepared Rialto pack')
    left_graphs, right_graphs = (graph_index(bundle.pop('graphs')) for bundle in (left, right))
    graphs = union(left_graphs, right_graphs, 'graphs')
    left_roots, right_roots = (bundle.pop('roots') for bundle in (left, right))
    graph_roots = sorted(set(left_roots.pop('graphs')) | set(right_roots.pop('graphs')))
    missing = (set(left.pop('missing')) | set(right.pop('missing'))) - graphs.keys()
    bundle = union(left, right, 'graph metadata')
    bundle.update(graphs=[graphs[index] for index in sorted(graphs)], missing=sorted(missing),
                  roots=union(left_roots, right_roots, 'roots') | {'graphs': graph_roots})
    if not {0x0CA7, 0x0BC6} <= graphs.keys():
        raise ValueError('the Junkenstein gate and Roadhog encounter graphs are required')
    movers = union(*(json.loads((root / MOVERS).read_bytes()) for root in (junkenstein, rialto)), 'movers')

    excluded = {GRAPHS, MOVERS, PROVENANCE}
    sources = [{path.relative_to(root): path for path in root.rglob('*')
                if path.is_file() and path.relative_to(root) not in excluded} for root in (junkenstein, rialto)]
    for relative in sources[0].keys() & sources[1].keys():
        if not filecmp.cmp(sources[0][relative], sources[1][relative], shallow=False):
            raise ValueError(f'conflicting prepared file: {relative}')

    payload = gzip.compress(json.dumps(bundle, separators=(',', ':'), ensure_ascii=False).encode(), mtime=0)
    provenance = {'graph_count': len(graphs), 'graph_sha256': hashlib.sha256(payload).hexdigest(),
                  'junkenstein_source': junkenstein.name, 'rialto_source': rialto.name}
    with tempfile.TemporaryDirectory(prefix=f'.{output.name}-', dir=output.parent) as directory:
        staged = Path(directory) / 'pack'
        for relative, source in (sources[0] | sources[1]).items():
            target = staged / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
        (staged / GRAPHS).write_bytes(payload)
        (staged / MOVERS).write_text(json.dumps(movers, separators=(',', ':')) + '\n', encoding='utf-8')
        (staged / PROVENANCE).write_text(json.dumps(provenance, indent=2) + '\n', encoding='utf-8')
        staged.rename(output)
    return provenance


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('junkenstein', 'rialto', 'output'):
        parser.add_argument(f'--{name}', required=True, type=Path)
    args = parser.parse_args()
    try:
        print(json.dumps(build(args.junkenstein.resolve(), args.rialto.resolve(), args.output.resolve())))
    except (OSError, ValueError, KeyError) as error:
        parser.error(str(error))


if __name__ == '__main__':
    main()
