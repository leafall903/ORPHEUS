"""Export original003 FirstPerson offsets outside Git for build104319 runtime use."""

import argparse
import hashlib
import json
import math
import xml.etree.ElementTree as ET
from pathlib import Path

BUILD = '1.74.0.0.104319'
ROOT = Path(__file__).resolve().parents[1]
NAME = '{https://yretenai.com/dragonml/v1}name'


def body_offsets(path):
    components = [e for e in ET.parse(path).iter()
                  if e.tag.split('}')[-1] == 'STUFirstPersonComponent' and e.get(NAME) == 'dragon:value']
    if len(components) != 1:
        raise ValueError(f'{path.name}: expected exactly one FirstPerson component')
    component = components[0]
    # Original7afe5f52 STUFirstPersonComponent.cs defaults, when an attribute is absent.
    values = [float(component.get(field, default).replace(',', '.'))
              for field, default in (('m_E29993A5', '1.55'), ('m_816B830F', '1.15'))]
    if any(not math.isfinite(v) or v <= 0 for v in values):
        raise ValueError(f'{path.name}: invalid FirstPerson offsets')
    return dict(zip(('standing', 'crouched'), values, strict=True))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--xml', type=Path, required=True, help='original003 XML directory')
    parser.add_argument('--heroes', type=Path, required=True, help='prepared game_heroes_174.json')
    parser.add_argument('--extra-body', action='append', default=[], help='additional hexadecimal003 index')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    output = args.output.resolve()
    if output.is_relative_to(ROOT):
        parser.error('generated game data must stay outside the repository')
    heroes = json.loads(args.heroes.read_text(encoding='utf-8-sig'))
    bodies = {int(row['body'], 16) for row in heroes.values()}
    bodies.update(0x0400000000000000 | int(value, 16) for value in args.extra_body)
    result, sources = {}, []
    for body in sorted(bodies):
        path = args.xml / f'{body & 0xFFFFFFFFFFFF:012X}.003.xml'
        result[f'0x{body:016X}'] = body_offsets(path)
        sources.append({'Path': str(path.resolve()),
                        'Hash': hashlib.sha256(path.read_bytes()).hexdigest().upper()})
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps({'version': 1, 'build': BUILD, 'bodies': result}, indent=2) + '\n',
                      encoding='utf-8')
    output.with_suffix('.provenance.json').write_text(json.dumps({
        'build': BUILD, 'sources': sources, 'native_camera_muzzle_bones_executed': False,
        'heroes': {'Path': str(args.heroes.resolve()),
                   'Hash': hashlib.sha256(args.heroes.read_bytes()).hexdigest().upper()},
        'output_sha256': hashlib.sha256(output.read_bytes()).hexdigest().upper(),
    }, indent=2) + '\n', encoding='utf-8')
    print(f'Exported {len(result)} body view offsets to {output}')


if __name__ == '__main__':
    main()
