"""The authored 1158 movement-sample outputs covered by the 104319 writer."""

import math
from dataclasses import dataclass

from ow174.game.script import expr

CLASS = "STU_96F1DBCF"
GRAPH = 0x1158
NODE = 19
FIELDS = ("m_105DF1D6", "m_49F4B33C")
IDS = (417, 545)
FLAGS = 0x80000C  # D3D7EA and D3D8DB.
THRESHOLD = expr.f32(1.1920928955078125e-05)  # D3D808.


@dataclass(frozen=True)
class Sample:
    """Explicit inputs corresponding to snapshot offsets 0 and 94."""

    flags: int
    axis: float

    def __post_init__(self):
        if type(self.flags) is not int or not 0 <= self.flags <= 0xFFFFFFFF:
            raise ValueError("movement sample flags must be uint32")
        if type(self.axis) not in (int, float) or not math.isfinite(self.axis):
            raise ValueError("movement sample axis must be finite")

    def outputs(self):
        return not bool(self.flags & FLAGS), expr.f32(self.axis) > THRESHOLD
