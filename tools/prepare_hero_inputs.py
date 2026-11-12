#!/usr/bin/env python3
"""Compose existing hero inputs as a small supplement; leave source packs intact."""

import argparse
import gzip
import hashlib
import json
import sys
from pathlib import Path

sys.path[:0] = [str(Path(__file__).resolve().parents[1] / "src"),
                str(Path(__file__).resolve().parents[1])]
from ow174.game.script import graph
from ow174.game.projectile_tags import read_catalog


def read_graphs(path):
    raw = path.read_bytes()
    data = json.loads(gzip.decompress(raw) if raw.startswith(b'\x1f\x8b') else raw)
    if type(data.get('version')) is not int or data['version'] != 1:
        raise ValueError(f'graph input must use version1: {path}')
    indexes = [row['index'] for row in data.get('graphs', [])]
    if len(indexes) != len(set(indexes)):
        raise ValueError(f'duplicate graph indexes: {path}')
    return data


def build(runtime, mercy_effects, supplements, movers, projectile_tags, output, delivered_graphs=()):
    repository = Path(__file__).resolve().parents[1]
    if output.exists() or output.is_relative_to(repository) or output.is_relative_to(runtime):
        raise ValueError('output must be a new directory outside the repository and source runtime')
    base_path = runtime / 'data/game_graphs_174.json.gz'
    base = read_graphs(base_path)
    source = read_graphs(mercy_effects)
    if not isinstance(source.get('effect_scripts'), dict) or not source['effect_scripts']:
        raise ValueError('Mercy effects input has no effect bindings')
    for key in base.keys() | source.keys():
        if key not in ('graphs', 'effect_scripts') and base.get(key) != source.get(key):
            raise ValueError(f'Mercy input conflicts with base metadata: {key}')
    base_graphs = {row['index']: row for row in base['graphs']}
    added = []
    for row in source['graphs']:
        index = row['index']
        if index in base_graphs:
            if row != base_graphs[index]:
                raise ValueError(f'Mercy input conflicts with base graph {index:04X}')
        else:
            added.append(row)
    merged = graph.add_supplement(base, {
        'version': 1, 'graphs': added, 'effect_scripts': source.get('effect_scripts', {}),
    })
    for path in supplements:
        merged = graph.add_supplement(merged, read_graphs(path))
    unselected_metadata = {}
    for path in delivered_graphs:
        delivered = read_graphs(path)
        collections = ('graphs', 'entities', 'effect_scripts', 'deployables', 'npc_controllers')
        for key in delivered.keys() & base.keys():
            if key not in collections and delivered.get(key) != base.get(key):
                raise ValueError(f'delivered input conflicts with base metadata: {key}: {path}')
        unselected_metadata[str(path)] = sorted(delivered.keys() - set(collections) - {'version'})
        known = {row['index']: row for row in merged['graphs']}
        additions = []
        for row in delivered['graphs']:
            if row['index'] in known:
                if row != known[row['index']]:
                    raise ValueError(f'delivered input conflicts with graph {row["index"]:04X}: {path}')
            else:
                additions.append(row)
        extra_input = {'version': 1, 'graphs': additions}
        for key in collections[1:]:
            if key in delivered:
                extra_input[key] = delivered[key]
        merged = graph.add_supplement(merged, extra_input)
    extra = {
        'version': 1,
        'graphs': [row for row in merged['graphs'] if row['index'] not in base_graphs],
        'entities': {key: row for key, row in merged.get('entities', {}).items()
                     if key not in (base.get('entities') or {})},
        'effect_scripts': {key: row for key, row in merged.get('effect_scripts', {}).items()
                           if key not in (base.get('effect_scripts') or {})},
    }
    for collection in ('deployables', 'npc_controllers'):
        if collection in merged:
            extra[collection] = {key: value for key, value in merged[collection].items()
                                 if key not in (base.get(collection) or {})}
    graph.add_supplement(base, extra)
    catalog = read_catalog(projectile_tags.read_bytes())
    if movers is not None and not isinstance(json.loads(movers.read_bytes()), dict):
        raise ValueError('mover supplement must be a mapping')
    sources = [base_path, mercy_effects, *supplements, *delivered_graphs, projectile_tags]
    if movers is not None:
        sources.append(movers)
    hashes = {str(path): hashlib.sha256(path.read_bytes()).hexdigest() for path in sources}
    supplement_path = output / 'hero-graphs.json.gz'
    environment = {
        'ORPHEUS_RUNTIME_DIR': str(runtime),
        'ORPHEUS_GRAPH_SUPPLEMENT': str(supplement_path),
        'ORPHEUS_PROJECTILE_TAGS': str(projectile_tags),
    }
    if movers is not None:
        environment['ORPHEUS_MOVERS_SUPPLEMENT'] = str(movers)
    manifest = {
        'version': 1, 'environment': environment, 'source_sha256': hashes,
        'additional_graphs': len(extra['graphs']), 'additional_entities': len(extra['entities']),
        'additional_effect_bindings': len(extra['effect_scripts']), 'projectile_definitions': len(catalog),
    }
    if 'deployables' in extra:
        manifest['additional_deployable_keys'] = sorted(extra['deployables'])
    if 'npc_controllers' in extra:
        manifest['additional_npc_associations'] = len(extra['npc_controllers'])
    if delivered_graphs:
        manifest['unselected_delivered_metadata'] = unselected_metadata
    payload = gzip.compress(json.dumps(extra, separators=(',', ':')).encode(), mtime=0)
    output.mkdir(parents=True)
    supplement_path.write_bytes(payload)
    (output / 'environment.json').write_text(json.dumps(manifest, indent=2) + '\n', encoding='utf-8')
    if any(hashlib.sha256(Path(path).read_bytes()).hexdigest() != digest for path, digest in hashes.items()):
        raise ValueError('a source input changed during composition; do not use this output')
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('runtime', 'mercy-effects', 'projectile-tags', 'output'):
        parser.add_argument(f'--{name}', required=True, type=Path)
    parser.add_argument('--graph-supplement', action='append', default=[], type=Path)
    parser.add_argument('--movers-supplement', type=Path)
    parser.add_argument('--delivered-graphs', action='append', default=[], type=Path,
                        help='original delivered graph pack; retain shared records and extract only additions')
    args = parser.parse_args()
    try:
        manifest = build(args.runtime.resolve(), args.mercy_effects.resolve(),
                         [path.resolve() for path in args.graph_supplement],
                         args.movers_supplement.resolve() if args.movers_supplement else None,
                         args.projectile_tags.resolve(), args.output.resolve(),
                         [path.resolve() for path in args.delivered_graphs])
    except (OSError, ValueError, KeyError) as error:
        parser.error(str(error))
    print(json.dumps({key: value for key, value in manifest.items() if key != 'source_sha256'}))


if __name__ == '__main__':
    main()
