"""Collision geometry for the external 104319 Experimental Barrier model."""

import hashlib
import json
from functools import cache
from pathlib import Path

from ow174.game import props
from ow174.game.collision import World


@cache
def barrier_geometry(path: str):
    """Load the verified external C015 physics mesh, including its dynamic holder.

    No asset data is bundled in source and no rectangle fallback is fabricated.
    The sidecar is produced by the manifest-verifying local export step.
    """
    source = Path(path)
    record = json.loads(source.with_name("model-integrity.json").read_text(encoding="utf-8-sig"))
    raw = source.read_bytes()
    if (record.get("guid") != "00000000C015.00C" or record.get("build") != 104319
            or record.get("bytes") != len(raw)
            or record.get("sha256") != hashlib.sha256(raw).hexdigest()):
        raise ValueError("Barrier model does not match its verified 104319 input record")
    shapes = props.model_shapes(raw, 0xFFFF, include_dynamic=True)
    triangles, masks = [], []
    for shape in shapes:
        if shape[0] != "mesh":
            raise ValueError("unsupported Barrier physics shape")
        _kind, _mask, vertices, faces = shape
        for a, b, c, _w0, _w1, _w2, mask, _surface in faces:
            triangles.append((*vertices[a], *vertices[b], *vertices[c]))
            masks.append(mask)
    if not triangles:
        raise ValueError("Barrier model has no physics triangles")
    return World(triangles, masks=masks, name="Sigma C015 dynamic physics mesh")
