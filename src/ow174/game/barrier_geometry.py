"""Optional external 104319 shield mesh and two-sided triangle intersection.

Asset bytes stay outside the source tree. No bounding rectangle substitutes for
missing geometry; a runtime without this input has no shield ray collider.
"""

import hashlib
import json
import math
from functools import lru_cache

from ow174.game.collision import World
from ow174.paths import DATA_DIR

GEOMETRY_FILE = "barrier_geometry_174.json"
EPSILON = 1e-9


def load():
    path = DATA_DIR / GEOMETRY_FILE
    if not path.is_file():
        return (), None
    raw = path.read_bytes()
    data = json.loads(raw)
    if (data.get("schema") != "ow174.barrier-geometry.v1"
            or data.get("build") != "1.74.0.0.104319"
            or data.get("definition") != "0x040000000000011A"
            or data.get("model") != "0x0D00000000000A3C"):
        raise ValueError("unsupported barrier geometry identity")
    triangles = []
    for mesh in data["meshes"]:
        if mesh["mask"] != 0x1FDF:
            raise ValueError("unsupported barrier mesh mask")
        vertices = mesh["vertices"]
        if not vertices or any(
            len(v) != 3 or any(type(n) not in (int, float) or not math.isfinite(n) for n in v)
            for v in vertices
        ):
            raise ValueError("invalid barrier vertex")
        for row in mesh["triangles"]:
            # Extractor retains the adjacency words after the three vertex indices.
            if len(row) < 3 or any(type(i) is not int or not 0 <= i < len(vertices) for i in row[:3]):
                raise ValueError("invalid barrier triangle")
            triangles.append(tuple(tuple(vertices[i]) for i in row[:3]))
    if not triangles:
        raise ValueError("empty barrier mesh")
    return tuple(triangles), hashlib.sha256(raw).hexdigest()


def _sub(a, b):
    return tuple(x - y for x, y in zip(a, b, strict=True))


def _dot(a, b):
    return sum(x * y for x, y in zip(a, b, strict=True))


def _cross(a, b):
    return (a[1]*b[2] - a[2]*b[1], a[2]*b[0] - a[0]*b[2], a[0]*b[1] - a[1]*b[0])


@lru_cache(maxsize=2)
def _sweep_world(triangles):
    return World(tuple(tuple(axis for vertex in triangle for axis in vertex) for triangle in triangles))


def ray_mesh(triangles, origin, direction, length, *, padding=0.0):
    """Nearest nonnegative distance, with a unit direction and inclusive edge hits."""
    best = None
    for a, b, c in triangles:
        ab, ac = _sub(b, a), _sub(c, a)
        p = _cross(direction, ac)
        determinant = _dot(ab, p)
        if abs(determinant) <= EPSILON:
            continue
        offset = _sub(origin, a)
        u = _dot(offset, p) / determinant
        if not -EPSILON <= u <= 1 + EPSILON:
            continue
        q = _cross(offset, ab)
        v = _dot(direction, q) / determinant
        if v < -EPSILON or u + v > 1 + EPSILON:
            continue
        distance = _dot(ac, q) / determinant
        if -EPSILON <= distance <= length and (best is None or distance < best):
            best = max(0.0, distance)
    if padding > 0:
        swept = _sweep_world(triangles).sweep_sphere(origin, direction, length, padding)
        if swept is not None and (best is None or swept < best):
            return swept
    return best
