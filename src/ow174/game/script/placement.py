"""Reconstructed static Call Mech placement; native preview/filter semantics remain open."""

import math
from dataclasses import dataclass, field

from ow174.game import contacts
from ow174.game.collision import MOVER, NOT_GROUND
from ow174.game.script import expr


@dataclass(frozen=True)
class Result:
    point: expr.Vec3 | None  # None is a completed, rejected placement.


def _vector(value):
    try:
        return type(value) is expr.Vec3 and all(type(axis) in (int, float) and math.isfinite(axis)
                                              for axis in value)
    except OverflowError:
        return False


def place(match, state):
    """Ground ray, slope, static capsule clearance and upright body overlap policy.

    This supports the actual026A/node38 primary downward cast. It does not
    interpret opaque .097 profiles or synthesize the client's preview outputs.
    """
    if state.instance.graph.index != 0x26A or state.node.pos != 38:
        return NotImplemented
    if state.node.field("m_D14637AE") is not None or match.collision_world is None:
        return NotImplemented
    cast = state.node.field("m_75198C98")
    if not isinstance(cast, dict) or cast.get("$") != "STU_70C754CD":
        return NotImplemented
    origin = state.evaluate(cast.get("m_11EFE48E"), watch=False)
    direction = state.evaluate(cast.get("m_35961153"), watch=False)
    length = state.evaluate(cast.get("m_castLength"), watch=False)
    radius = state.evaluate(state.node.field("m_BA42208A"), watch=False)
    height = state.evaluate(state.node.field("m_C72356CD"), watch=False)
    slope = state.evaluate(state.node.field("m_5EF7CDF0"), watch=False)
    if (not _vector(origin) or direction != expr.Vec3(0, -1, 0)
            or any(type(x) not in (int, float) or not math.isfinite(x)
                   for x in (length, radius, height, slope))
            or length <= 0 or radius <= 0 or height < 2*radius or not 0 <= slope < 90):
        return NotImplemented
    world = match.collision_world
    hit = world.cast_ray(tuple(origin), tuple(direction), length, MOVER)
    if hit is None or hit.surface & NOT_GROUND or hit.normal[1] < math.cos(math.radians(slope)):
        return Result(None)
    body = contacts.CharacterBody(radius, height-radius, world=world.engine, category=MOVER)
    body.teleport(hit.point)
    if not body.fits(radius, height-radius):
        return Result(None)
    ignores = {state.instance.component.entity}
    for cfg in state.node.field("m_ignoreEntities") or ():
        identifier = expr.to_entity(state.evaluate(cfg, watch=False))
        if expr.valid_entity(identifier):
            ignores.add(identifier)
    x, y, z = hit.point
    low, high = y+radius, y+height-radius
    for target in match.combat._targets():
        if target.entity in ignores:
            continue
        a, b = target.segment()
        dy = max(a[1]-high, low-b[1], 0)
        distance2 = (target.feet[0]-x)**2 + (target.feet[2]-z)**2 + dy*dy
        if distance2 < (radius+target.radius)**2:
            return Result(None)
    return Result(expr.Vec3(*(expr.f32(axis) for axis in hit.point)))


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
