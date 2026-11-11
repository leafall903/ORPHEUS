"""104319 placement clock, history and explicit confirmation-scope primitives."""

import math
from dataclasses import dataclass, field

from ow174.game.script import expr


@dataclass(frozen=True)
class Clock:
    frame: int
    clock: int
    interval: int

    def __post_init__(self):
        if type(self.frame) is not int or not 0 <= self.frame <= 0xFFFFFFFF:
            raise ValueError("placement frame must be uint32")
        if type(self.clock) is not int or not -(1 << 63) <= self.clock < 1 << 63:
            raise ValueError("placement clock must be int64")
        if type(self.interval) is not int or not 0 < self.interval < 1 << 63:
            raise ValueError("placement interval must be positive int64")

    def ready(self):
        # Native cqo/idiv keeps the dividend's sign, unlike Python's modulo.
        remainder = self.clock % self.interval if self.clock >= 0 else -((-self.clock) % self.interval)
        return remainder == self.interval - 1


@dataclass(frozen=True)
class Record:
    """The value fields of native placement's 0x30-byte history row."""

    frame: int
    entity: expr.Entity
    identifier: expr.Asset
    point: expr.Vec3
    valid: bool

    def __post_init__(self):
        if type(self.frame) is not int or not 0 <= self.frame <= 0xFFFFFFFF:
            raise ValueError("placement history frame must be uint32")
        if type(self.entity) is not expr.Entity or type(self.entity.id) is not int:
            raise ValueError("placement history entity must be a typed entity")
        if not 0 <= self.entity.id <= 0xFFFFFFFF:
            raise ValueError("placement history entity must fit uint32")
        if type(self.identifier) is not expr.Asset or type(self.identifier.guid) is not int:
            raise ValueError("placement history identifier must be a typed id")
        if not 0 <= self.identifier.guid < 1 << 64:
            raise ValueError("placement history identifier must fit uint64")
        if type(self.point) is not expr.Vec3 or type(self.valid) is not bool:
            raise ValueError("placement history requires a typed point and boolean")
        if any(type(value) not in (int, float) for value in self.point):
            raise ValueError("placement history point components must be numeric")
        object.__setattr__(self, "point", expr.Vec3(*(expr.f32(v) for v in self.point)))


@dataclass(frozen=True)
class StaticResult:
    """Admitted C8EB30 outcome with no dynamic hit entity or relative transform."""

    valid: bool
    point: expr.Vec3 = field(default_factory=lambda: expr.Vec3(0, 0, 0))

    def __post_init__(self):
        if type(self.valid) is not bool or type(self.point) is not expr.Vec3:
            raise ValueError("static placement requires boolean validity and a typed point")
        try:
            finite = all(type(v) in (int, float) and math.isfinite(v) for v in self.point)
        except OverflowError:
            finite = False
        if not finite:
            raise ValueError("static placement requires finite point components")
        point = expr.Vec3(*(expr.f32(v) for v in self.point))
        if not all(math.isfinite(v) for v in point):
            raise ValueError("static placement point must fit finite float32")
        object.__setattr__(self, "point", point)


def publish_static(result: StaticResult, *, commit: bool, write, frame, record, follow):
    """CB6BA0's static-world result tail, after admitted owner/geometry lookup.

    Typed writes precede the frame read and history. Unknown history/clock
    admission stops before forwarding any output; preview never forwards it.
    """
    if type(result) is not StaticResult or type(commit) is not bool:
        raise ValueError("placement publication requires a typed result and explicit commit flag")
    if result.valid:
        write("m_2FECD1E3", expr.Entity(0))
        write("m_30710B76", result.point)
    write("m_2339572B", result.valid)
    current = frame()
    if current is NotImplemented:
        return NotImplemented
    point = result.point if result.valid else expr.Vec3(0, 0, 0)
    value = Record(current, expr.Entity(0), expr.Asset(0), point, result.valid)
    if record(value) is NotImplemented:
        return NotImplemented
    if commit:
        follow("m_F6955E81" if result.valid else "m_425337C6")
    return True


def prune(records: list[Record], before: int) -> None:
    """C9FCE0: reverse scan, unsigned cutoff, swap-remove rather than stable erase."""
    if type(before) is not int or not 0 <= before <= 0xFFFFFFFF:
        raise ValueError("placement history cutoff must be uint32")
    for index in range(len(records) - 1, -1, -1):
        if records[index].frame < before:
            records[index] = records[-1]
            records.pop()


def remember(records: list[Record], value: Record, *, enabled: bool) -> None:
    """CAB450 with admitted role and available storage; first row for a frame wins.

    This is storage semantics only. Native role admission, allocator failure,
    debug entity checks and real prediction ownership are separate services.
    """
    if type(enabled) is not bool or type(value) is not Record:
        raise ValueError("placement history requires an explicit role and typed row")
    if not enabled:
        return
    prune(records, (value.frame - 60) & 0xFFFFFFFF)
    if not any(record.frame == value.frame for record in records):
        records.append(value)


@dataclass
class Confirmation:
    """Priority2's admitted special input and one-bit property scope primitive.

    The owner supplies register/unregister services. This is not an implicit
    authoritative input policy, property transport or geometry admission.
    """

    latched: bool = False
    scope_depth: int = 0
    value_bits: int = 0
    dirty_bits: int = 0
    dirty: bool = False
    has_values: bool = False
    touched: bool = False

    def __post_init__(self):
        for value in (self.latched, self.dirty, self.has_values, self.touched):
            if type(value) is not bool:
                raise ValueError("placement confirmation flags must be boolean")
        for value in (self.scope_depth, self.value_bits, self.dirty_bits):
            if type(value) is not int or not 0 <= value <= 0xFFFFFFFF:
                raise ValueError("placement confirmation counters and bitsets must be uint32")

    def clear(self):
        # 1BEB260(false): ceil(1/8) bytes, preserving unused upper storage.
        self.dirty = self.has_values = False
        self.value_bits &= 0xFFFFFF00

    def mark(self):
        # 1BEB2F0(true, index0); +76 stays set across a later clear(false).
        self.dirty = self.has_values = self.touched = True
        self.value_bits |= 1
        self.dirty_bits |= 1

    def enter(self, register):
        if self.scope_depth == 0xFFFFFFFF:
            raise ValueError("placement confirmation scope depth overflow")
        self.scope_depth += 1
        if self.scope_depth == 1:
            self.clear()
            register()

    def leave(self, unregister):
        if self.scope_depth == 0:
            raise ValueError("placement confirmation scope is not entered")
        self.scope_depth -= 1
        if self.scope_depth == 0:
            unregister()

    def request(self, register, unregister):
        """8E8AC0 after admitted A1AAB8C4 type identity; no success plug follows."""
        self.enter(register)
        try:
            if not self.latched:
                self.latched = True
                self.mark()
        finally:
            self.leave(unregister)
