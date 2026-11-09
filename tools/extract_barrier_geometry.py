"""Prepare build104319 Reinhardt barrier ray geometry from an external verified model.

The input is the selectively exported A3C.00C and its manifest provenance, not
an archive copy. Output remains external game data and must not enter Git.
"""

import argparse
import hashlib
import json
import struct
from pathlib import Path

from ow174.game import props


def extract(model, provenance):
    digest = hashlib.sha256(model).hexdigest()
    if (provenance.get('guid') != '000000000A3C.00C' or provenance.get('build') != 104319
            or provenance.get('verified') is not True or provenance.get('sha256') != digest
            or provenance.get('size') != len(model)):
        raise ValueError('requires manifest-verified104319 A3C model')
    payload = props.chunks(model)['MSTU']
    offset, size = struct.unpack_from('<qq', payload)
    stu = props.Structured(payload[offset:offset+size])
    meshes = []
    for item in stu.array(0, props.SHAPES, 8):
        wrapper = struct.unpack_from('<i', item)[0]
        shape = stu.ref(wrapper, props.SHAPE)
        # This model's motion1 query shape is deliberately excluded by the
        # static mover extractor. It is the barrier's ray mesh, not map collision.
        if shape is None or stu.hashes[shape] != props.MESH:
            continue
        vertices = [props._vector(raw) for raw in stu.array(shape, props.VERTICES, 16)]
        raw = b''.join(stu.array(shape, props.TRIANGLES, 1))
        triangles = [struct.unpack_from('<3I', raw, at) for at in range(0, len(raw)-31, 32)]
        node = stu.raw(stu.fields[wrapper], props.NODE)
        frame = props.node_frame(props.nodes(stu), struct.unpack_from('<I', node)[0])
        if frame is not None:
            vertices = [props._moved(point, frame) for point in vertices]
        if any(max(tri) >= len(vertices) for tri in triangles):
            raise ValueError('bad mesh index')
        meshes.append({'vertices': vertices, 'triangles': triangles})
    if len(meshes) != 1:
        raise ValueError('expected one barrier ray mesh')
    geometry = meshes[0]
    encoded = json.dumps(geometry, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()
    return {'schema': 'ow174.reinhardt-barrier-mesh.v1', 'build': 104319,
            'source_sha256': digest, 'geometry_sha256': hashlib.sha256(encoded).hexdigest(), **geometry}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--model', type=Path, required=True)
    parser.add_argument('--provenance', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    result = extract(args.model.read_bytes(), json.loads(args.provenance.read_text(encoding='utf-8-sig')))
    args.output.write_text(json.dumps(result, indent=2)+'\n', encoding='utf-8')
    print(result['geometry_sha256'], len(result['vertices']), len(result['triangles']))


if __name__ == '__main__':
    main()
