"""Read explicit attached shield meshes without treating them as static map props.

This is geometry only. Actor/hardpoint placement, lifecycle, team filters and
shield health remain host services; no rectangle or guessed barrier is created.
"""

import hashlib
import math
import struct
from dataclasses import dataclass

from ow174.game import props
from ow174.game.collision import MOVER, RayWorld

ATTACHED_MOTION = 1
ATTACHED_HOLDER = 0x00066F4C


class DefenseGeometryError(ValueError):
    pass


@dataclass(frozen=True)
class AttachedMesh:
    source_sha256: str
    node: int
    vertices: tuple
    triangles: tuple  # native a,b,c,wing0,wing1,wing2,mask,surface records

    def world(self) -> RayWorld:
        """Explicit shape-local query world; the attached node still needs a pose."""
        faces, masks, surfaces = [], [], []
        for a, b, c, _w0, _w1, _w2, mask, surface in self.triangles:
            faces.append((*self.vertices[a], *self.vertices[b], *self.vertices[c]))
            masks.append(mask)
            surfaces.append(surface)
        return RayWorld(faces, masks, surfaces, name="attached shield shape-local space")


def attached_meshes(data: bytes, category: int = MOVER) -> tuple[AttachedMesh, ...]:
    """Decode the known motion1/00066F4C attached mesh path, with native masks.

    Unknown attached shapes fail explicitly. The ordinary static-map reader and
    its exclusion of moving parts are unchanged. Source bytes stay external.
    """
    try:
        payload = props.chunks(data).get("MSTU")
        if payload is None or len(payload) < 16:
            raise DefenseGeometryError("model has no attached-geometry STU")
        offset, size = struct.unpack_from("<qq", payload)
        if offset < 0 or size < 0 or offset + size > len(payload):
            raise DefenseGeometryError("invalid model STU bounds")
        stu = props.Structured(payload[offset:offset + size])
        meshes = []
        fingerprint = hashlib.sha256(data).hexdigest()
        for item in stu.array(0, props.SHAPES, 8):
            wrapper = struct.unpack_from("<i", item)[0]
            if not 0 <= wrapper < len(stu.hashes):
                raise DefenseGeometryError("invalid attached shape reference")
            if props._byte(stu, stu.fields[wrapper], props.MOTION, 0) != ATTACHED_MOTION:
                continue
            holder, shape = stu.ref(wrapper, props.HOLDER), stu.ref(wrapper, props.SHAPE)
            if (holder is None or shape is None or stu.hashes[holder] != ATTACHED_HOLDER
                    or stu.hashes[shape] != props.MESH):
                raise DefenseGeometryError("unknown attached holder or shape")
            node_data = stu.raw(stu.fields[wrapper], props.NODE)
            if node_data is None or len(node_data) != 4:
                raise DefenseGeometryError("attached mesh has no node identity")
            node = struct.unpack("<I", node_data)[0]
            points = [props._vector(raw) for raw in stu.array(shape, props.VERTICES, 16)]
            if any(not math.isfinite(axis) for point in points for axis in point):
                raise DefenseGeometryError("attached mesh has nonfinite vertices")
            raw = b"".join(stu.array(shape, props.TRIANGLES, 1))
            if not points or len(raw) % 32:
                raise DefenseGeometryError("invalid attached mesh arrays")
            triangles = []
            for at in range(0, len(raw), 32):
                a, b, c, w0, w1, w2, _material, surface, mask, _ = struct.unpack_from("<3I3i4H", raw, at)
                if max(a, b, c) >= len(points):
                    raise DefenseGeometryError("attached triangle leaves vertex array")
                if mask & category:
                    triangles.append((a, b, c, w0, w1, w2, mask, surface))
            if triangles:
                meshes.append(AttachedMesh(fingerprint, node, tuple(points), tuple(triangles)))
        if not meshes:
            raise DefenseGeometryError("no supported attached mesh for this category")
        return tuple(meshes)
    except (props.PropError, struct.error, IndexError, KeyError) as error:
        raise DefenseGeometryError("invalid attached geometry data") from error
