"""Check the Rust aim-ray policy against normal pinned Python collision kernels."""
import argparse
import importlib
import json
import math
from pathlib import Path
import subprocess
import sys


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--python-reference', type=Path, required=True)
    args = parser.parse_args()
    reference = args.python_reference.resolve(strict=True)
    contract = json.loads((Path(__file__).resolve().parents[1] / 'fixtures/behavior/deadeye-aim-ray-visibility-v1.json').read_text(encoding='utf-8'))
    assert (contract['schema_version'], contract['case_id'], contract['target_build']) == (1, 'deadeye.aim-ray-visibility.v1', 104319)
    revision = subprocess.check_output(['git', '-C', str(reference), 'rev-parse', 'HEAD'], text=True).strip()
    assert revision == contract['source']['revision']
    subprocess.check_call(['git', '-C', str(reference), 'diff', '--quiet', 'HEAD', '--', 'ow174/game'])
    sys.path.insert(0, str(reference))
    combat = importlib.import_module('ow174.game.combat')
    collision = importlib.import_module('ow174.game.collision')
    origin, target = contract['input']['origin'], contract['input']['target']
    x, y, z = target['feet']
    radius, height = target['radius'], target['height']
    a, b = (x, y + radius, z), (x, y + height - radius, z)
    point = (x, min(y + target['eye'], b[1]), z)
    delta = tuple(p - q for p, q in zip(point, origin, strict=True))
    length = math.hypot(*delta)
    direction = tuple(value / length for value in delta)
    reach = combat.ray_capsule(origin, direction, a, b, radius)
    assert reach is not None
    cases = contract['input']['cases']
    for case in cases:
        triangles = [tuple(value for point in triangle for value in point)
                     for triangle in case['triangles']]
        world = collision.RayWorld(triangles, [0x18] * len(triangles))
        wall = combat.ray_world(world, origin, direction, reach, 0x18)
        actual = {'visible': wall is None or wall + 1e-9 >= reach,
                  'legacy_visible': combat.capsule_visible(world, origin, a, b, radius, 0x18)}
        assert actual == case['expected'], (case['name'], actual, case['expected'])
    print(f'PASS: {len(cases)} aim-ray collision cases against {revision}; this is an explicit Rust host policy')


if __name__ == '__main__':
    main()
