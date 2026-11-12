"""Bind original support statuses/lift inputs without changing a selected pack."""
import argparse
import gzip
import hashlib
import json
import sys
from pathlib import Path

sys.path[:0] = [str(Path(__file__).resolve().parents[1] / "src"),
                str(Path(__file__).resolve().parents[1])]
from ow174.game.script import graph


def read(path):
    raw = path.read_bytes()
    return json.loads(gzip.decompress(raw) if raw[:2] == b'\x1f\x8b' else raw)


def extend(profile, statuses, output, *, platform=None):
    if output.exists():
        raise ValueError('profile output must be a new directory')
    manifest = read(profile)
    environment = manifest['environment']
    base_path = Path(environment['ORPHEUS_RUNTIME_DIR']) / 'data/game_graphs_174.json.gz'
    previous_path = Path(environment['ORPHEUS_GRAPH_SUPPLEMENT'])
    sources = [profile, base_path, previous_path, statuses]
    if platform is not None:
        sources.append(platform)
    hashes = {str(path): hashlib.sha256(path.read_bytes()).hexdigest() for path in sources}
    base, supplement, supplied = read(base_path), read(previous_path), read(statuses)
    merged = graph.add_supplement(base, supplement)
    required = (0x0C71, 0x0B99, 0x0C72)
    known = {row['index']: row for row in merged['graphs']}
    incoming = {row['index']: row for row in supplied['graphs']}
    added = []
    for index in required:
        row = incoming[index]
        if index in known:
            if known[index] != row:
                raise ValueError(f'original status graph conflicts with selected {index:04X}')
        else:
            added.append(row)
    key = '0x04A0000000000267'
    binding = supplied['effect_scripts'][key]
    if binding['graph'] != 0x0C71:
        raise ValueError('unexpected original0267 status binding')
    addition = {'version': 1, 'graphs': added, 'effect_scripts': {key: binding}}
    graph.add_supplement(merged, addition)
    extra = dict(supplement)
    extra['graphs'] = [*supplement['graphs'], *added]
    extra['effect_scripts'] = {**supplement.get('effect_scripts', {}), key: binding}
    graph.add_supplement(base, extra)
    if platform is not None:
        lift = read(platform)
        if (lift['version'], lift['build'], int(lift['map'], 16),
                lift['platform']['flat_index'], lift['graph']['index']) != (
                    1, 104319, 0x08000000000001DB, 1931, 0x2ABA):
            raise ValueError('unexpected original Volskaya platform identity')
        environment['ORPHEUS_VOLSKAYA_PLATFORM'] = str(platform.resolve())
    environment['ORPHEUS_GRAPH_SUPPLEMENT'] = str((output / 'hero-graphs.json.gz').resolve())
    manifest['source_sha256'].update(hashes)
    manifest['additional_graphs'] = len(extra['graphs'])
    manifest['additional_effect_bindings'] = len(extra['effect_scripts'])
    manifest['profile_revision'] = output.name
    manifest['status'] = 'original support status binding and immutable lift input admitted'
    manifest['original_status_admission'] = {
        'source': str(statuses.resolve()), 'effect': key, 'graph': binding['graph'],
        'checked_graphs': list(required), 'added_graphs': [row['index'] for row in added],
        'previous_profile': str(profile.resolve()),
    }
    if platform is not None:
        manifest['volskaya_admission'] = {
            'source': str(platform.resolve()), 'map': '0x08000000000001DB',
            'controller': 0x2ABA, 'flat': 1931,
            'binding': 'existing original map-select factory; no runtime pack writes',
        }
    if any(hashlib.sha256(Path(path).read_bytes()).hexdigest() != digest
           for path, digest in hashes.items()):
        raise ValueError('profile input changed while preparing')
    output.mkdir(parents=True)
    (output / 'hero-graphs.json.gz').write_bytes(
        gzip.compress(json.dumps(extra, separators=(',', ':')).encode(), mtime=0))
    (output / 'environment.json').write_text(json.dumps(manifest, indent=2) + '\n', encoding='utf-8')
    return manifest['original_status_admission']


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--profile', required=True, type=Path)
    parser.add_argument('--support-statuses', required=True, type=Path)
    parser.add_argument('--volskaya-platform', type=Path)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    print(json.dumps(extend(args.profile.resolve(), args.support_statuses.resolve(),
                            args.output.resolve(), platform=args.volskaya_platform.resolve()
                            if args.volskaya_platform else None), indent=2))


if __name__ == '__main__':
    main()
