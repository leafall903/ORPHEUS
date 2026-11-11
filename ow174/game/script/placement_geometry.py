"""104319 placement projection math; owner pose, casts and validation are external."""

import math
from dataclasses import dataclass

from ow174.game.script import expr

PI_OVER_TWO = 1.5707963705062866
DEGREES = 0.01745329238474369
CONTACT_NORMAL_SWITCH = 0.7193400263786316
CONTACT_PADDING = 0.014999999664723873
_BACKOFF_DISTANCE_SQUARED = 0.00022499999613501132
_ANGLE_COEFFICIENTS = (
    0.8721975088119507, 2.821105718612671, -21.305187225341797, 88.44007873535156,
    -205.4483642578125, 271.33416748046875, 189.90158081054688, 54.758384704589844,
)


def angle_polynomial(value: float) -> float:
    """5C7970's clamped odd polynomial, with original float32 operation order."""
    x = expr.f32(value)
    if math.isnan(x) or x > 1:
        x = 1.0
    if x < -1:
        x = -1.0
    negative = x < 0
    if negative:
        x = -x
    square = expr.f32(x * x)
    power = x
    result = expr.f32(power * _ANGLE_COEFFICIENTS[0])
    for index, coefficient in enumerate(_ANGLE_COEFFICIENTS[1:], 1):
        power = expr.f32(power * square)
        term = expr.f32(power * coefficient)
        result = expr.f32(result - term if index == 6 else result + term)
    return -result if negative else result


def _vector(value):
    if type(value) is not expr.Vec3:
        raise ValueError("placement projection requires a typed vector")
    try:
        finite = all(type(axis) in (int, float) and math.isfinite(axis) for axis in value)
    except OverflowError:
        finite = False
    if not finite:
        raise ValueError("placement projection requires finite vector components")
    result = expr.Vec3(*(expr.f32(axis) for axis in value))
    if not all(math.isfinite(axis) for axis in result):
        raise ValueError("placement projection vector must fit finite float32")
    return result


def _dot(a, b):
    yx = expr.f32(expr.f32(a.y * b.y) + expr.f32(a.x * b.x))
    return expr.f32(yx + expr.f32(a.z * b.z))


def rotate(vector: expr.Vec3, axis: expr.Vec3, angle: float) -> expr.Vec3:
    """884090's rotation math with an admitted axis and sinf/cosf ABI values."""
    vector, axis = _vector(vector), _vector(axis)
    try:
        finite = type(angle) in (int, float) and math.isfinite(angle)
    except OverflowError:
        finite = False
    if not finite:
        raise ValueError("placement projection angle must be finite")
    angle = expr.f32(angle)
    if not math.isfinite(angle):
        raise ValueError("placement projection angle must be finite")
    sine, cosine = expr.f32(math.sin(angle)), expr.f32(math.cos(angle))
    scalar = expr.f32(expr.f32(1.0 - cosine) * _dot(axis, vector))
    cross = expr.Vec3(
        expr.f32(expr.f32(axis.z * vector.y) - expr.f32(axis.y * vector.z)),
        expr.f32(expr.f32(axis.x * vector.z) - expr.f32(axis.z * vector.x)),
        expr.f32(expr.f32(axis.y * vector.x) - expr.f32(axis.x * vector.y)),
    )
    x = expr.f32(expr.f32(cosine * vector.x) + expr.f32(scalar * axis.x))
    y = expr.f32(expr.f32(scalar * axis.y) + expr.f32(cosine * vector.y))
    z = expr.f32(expr.f32(scalar * axis.z) + expr.f32(cosine * vector.z))
    return expr.Vec3(expr.f32(x - expr.f32(cross.x * sine)),
                     expr.f32(y - expr.f32(cross.y * sine)),
                     expr.f32(z - expr.f32(cross.z * sine)))


@dataclass(frozen=True)
class ProjectionAngles:
    additional_attempts: int
    step: float
    first_adjustment: float

    def __post_init__(self):
        if type(self.additional_attempts) is not int or not 0 <= self.additional_attempts <= 10:
            raise ValueError("placement projection retry count must be0..10")
        try:
            finite = all(type(v) in (int, float) and math.isfinite(v)
                         for v in (self.step, self.first_adjustment))
        except OverflowError:
            finite = False
        if not finite:
            raise ValueError("placement projection schedule must be finite")
        step, adjustment = expr.f32(self.step), expr.f32(self.first_adjustment)
        if not step > 0 or not all(math.isfinite(v) for v in (step, adjustment)):
            raise ValueError("placement projection schedule must fit its finite float32 profile")
        object.__setattr__(self, "step", step)
        object.__setattr__(self, "first_adjustment", adjustment)

    def advance(self, current: expr.Vec3, axis: expr.Vec3, retry: int) -> expr.Vec3:
        """C90381/C90415: caller supplies current direction after validation."""
        if type(retry) is not int or retry < 0 or retry >= self.additional_attempts:
            raise ValueError("placement projection retry is outside the admitted schedule")
        current = rotate(current, axis, -self.step)
        return rotate(current, axis, self.first_adjustment if retry == 0 else 0.0)


def projection_angles(direction: expr.Vec3, up: expr.Vec3, step_degrees: float) -> ProjectionAngles:
    """C8FAB3..C8FB6E schedule; callbacks decide whether a retry is actually taken."""
    direction, up = _vector(direction), _vector(up)
    try:
        finite = type(step_degrees) in (int, float) and math.isfinite(step_degrees)
    except OverflowError:
        finite = False
    if not finite:
        raise ValueError("placement projection step must be finite")
    angle = expr.f32(PI_OVER_TWO - angle_polynomial(_dot(up, direction)))
    step = expr.f32(expr.f32(step_degrees) * DEGREES)
    if not step > 0:
        step = angle
    if not step > 0 or not math.isfinite(step):
        raise ValueError("placement projection step is outside the finite admitted profile")
    ratio = expr.f32(angle / step)
    if not math.isfinite(ratio) or abs(ratio) >= 1 << 31:
        raise ValueError("placement projection count is outside the admitted integer profile")
    count = min(10, math.trunc(abs(ratio)))
    remainder = expr.f32(angle - expr.f32(expr.f32(math.trunc(ratio)) * step))
    return ProjectionAngles(count, step, expr.f32(step - remainder))


def _scalar(value):
    try:
        finite = type(value) in (int, float) and math.isfinite(value)
    except OverflowError:
        finite = False
    if not finite:
        raise ValueError("placement contact requires finite scalar inputs")
    value = expr.f32(value)
    if not math.isfinite(value):
        raise ValueError("placement contact scalar must fit finite float32")
    return value


def _subtract(a, b):
    return expr.Vec3(*(expr.f32(x - y) for x, y in zip(a, b, strict=True)))


def _offset(point, direction, distance):
    return expr.Vec3(*(expr.f32(p + expr.f32(axis * distance))
                       for p, axis in zip(point, direction, strict=True)))


@dataclass(frozen=True)
class Contact:
    """An admitted primary-query contact, in the query's resolved coordinate frame."""

    point: expr.Vec3
    normal: expr.Vec3
    fraction: float

    def __post_init__(self):
        fraction = _scalar(self.fraction)
        if not 0 <= fraction <= 1:
            raise ValueError("placement contact fraction must be0..1")
        object.__setattr__(self, "point", _vector(self.point))
        object.__setattr__(self, "normal", _vector(self.normal))
        object.__setattr__(self, "fraction", fraction)


@dataclass(frozen=True)
class Candidates:
    """Two points handed to CA0E50; neither point has been validated here."""

    primary: expr.Vec3
    fallback: expr.Vec3

    def __post_init__(self):
        object.__setattr__(self, "primary", _vector(self.primary))
        object.__setattr__(self, "fallback", _vector(self.fallback))


def range_candidates(origin, candidates, minimum):
    """C90212..C902BC: replace a too-close point, or known status2 if both fail."""
    origin, minimum = _vector(origin), _scalar(minimum)
    if type(candidates) is not Candidates:
        raise ValueError("placement range check requires typed candidates")
    primary, fallback = candidates.primary, candidates.fallback
    threshold = expr.f32(minimum * minimum)
    delta = _subtract(primary, origin)
    if _dot(delta, delta) < threshold:
        primary = fallback
    else:
        delta = _subtract(fallback, origin)
        if _dot(delta, delta) < threshold:
            fallback = primary
    delta = _subtract(primary, origin)
    return None if _dot(delta, delta) < threshold else Candidates(primary, fallback)


def refine_contact(origin, end, direction, contact, offset, query_scalar, clearance):
    """C8FE99..C90212 contact offsets/probes, with explicit clearance replies.

    Return unvalidated Candidates, known both-blocked (None), or unknown service.
    Cast shape/filter and coordinate-frame admission remain the owner's contract.
    """
    origin, end, direction = _vector(origin), _vector(end), _vector(direction)
    if type(contact) is not Contact:
        raise ValueError("placement refinement requires a typed contact")
    offset, query_scalar = _scalar(offset), _scalar(query_scalar)
    normal = expr.Vec3(0, 1, 0) if contact.normal.y > CONTACT_NORMAL_SWITCH else contact.normal
    primary = _offset(contact.point, normal, offset)
    fallback = _offset(contact.point, direction, -offset)
    segment = _subtract(end, origin)
    probe_start = _offset(origin, segment, contact.fraction)
    advance = _subtract(probe_start, origin)
    squared = _dot(advance, advance)
    if squared < _BACKOFF_DISTANCE_SQUARED:
        probe_start = origin
    else:
        # Native764460 uses rsqrt/Newton; this finite scalar form is verified
        # against those instructions with a declared component tolerance.
        length = expr.f32(math.sqrt(squared))
        unit = expr.Vec3(*(expr.f32(axis / length) for axis in advance))
        probe_start = _offset(probe_start, unit, -CONTACT_PADDING)
    scalar = expr.f32(expr.f32(query_scalar + CONTACT_PADDING) - CONTACT_PADDING)
    replies = []
    for point in (primary, fallback):
        reply = clearance(probe_start, point, scalar)
        if reply is NotImplemented:
            return NotImplemented
        if type(reply) is not bool:
            raise ValueError("placement clearance requires a boolean reply or explicit unknown")
        replies.append(reply)
    primary_blocked, fallback_blocked = replies
    if primary_blocked and fallback_blocked:
        return None
    if fallback_blocked:
        fallback = primary
    if primary_blocked:
        primary = fallback
    return Candidates(primary, fallback)


@dataclass(frozen=True)
class SampleQuery:
    """C9E800's downward query, without an admitted engine shape/filter."""

    start: expr.Vec3
    end: expr.Vec3
    sweep: expr.Vec3 | None
    scalar: float

    def __post_init__(self):
        object.__setattr__(self, "start", _vector(self.start))
        object.__setattr__(self, "end", _vector(self.end))
        if self.sweep is not None:
            object.__setattr__(self, "sweep", _vector(self.sweep))
        object.__setattr__(self, "scalar", _scalar(self.scalar))


def sample_query(index, origin, direction, reference, *, length, step, drop_limit,
                 angle, sine_scale, offset, sweep, query_scalar):
    """Construct C9E800/C9C760's query from explicit resolved services."""
    if type(index) is not int or not 0 <= index < (1 << 31) - 1:
        raise ValueError("placement sample index is outside the admitted integer profile")
    if type(sweep) is not bool:
        raise ValueError("placement sample sweep flag must be boolean")
    origin, direction, reference = _vector(origin), _vector(direction), _vector(reference)
    length, step, drop_limit, angle, sine_scale, offset, query_scalar = (
        _scalar(v) for v in (length, step, drop_limit, angle, sine_scale, offset, query_scalar))
    distance = expr.f32(expr.f32(index) * step)
    remaining = expr.f32(length - distance)
    dot = _dot(expr.Vec3(0, -1, 0), reference)
    # C9C760 uses this linear dot-to-angle expression, not acos or5C7970.
    angular = expr.f32(expr.f32(3.1415927410125732 - angle)
                       - expr.f32(expr.f32(dot + 1) * PI_OVER_TWO))
    extent = drop_limit
    if angular > 0:
        sine = expr.f32(math.sin(angular))
        if sine == 0:
            raise ValueError("placement sample sine is outside the admitted finite profile")
        extent = min(expr.f32(expr.f32(remaining * sine_scale) / sine), drop_limit)
    extent = _scalar(expr.f32(extent + offset))
    start = _offset(origin, direction, distance)
    end = _offset(start, expr.Vec3(0, -1, 0), extent)
    enabled = sweep and (direction.y >= 0 or index != 0)
    delta = None
    if enabled:
        neighbour = index + (-1 if direction.y < 0 else 1)
        next_point = _offset(origin, direction, expr.f32(expr.f32(neighbour) * step))
        delta = _subtract(next_point, start)
    return SampleQuery(start, end, delta, query_scalar if enabled else 0.0)


@dataclass(frozen=True)
class SampleHit:
    """Resolved query reply with its original entity/identifier metadata."""

    contact: Contact
    entity: expr.Entity
    identifier: int

    def __post_init__(self):
        if type(self.contact) is not Contact or type(self.entity) is not expr.Entity:
            raise ValueError("placement sample requires typed contact/entity metadata")
        if type(self.identifier) is not int or not 0 <= self.identifier <= 0xFFFF:
            raise ValueError("placement sample identifier must fit uint16")


@dataclass(frozen=True)
class SampleResult:
    """C9E800 status0/1/2/3; success remains conditional on explicit services."""

    status: int
    hit: SampleHit | None = None

    def __post_init__(self):
        if type(self.status) is not int or self.status not in (0, 1, 2, 3):
            raise ValueError("placement sample status must be0..3")
        if ((self.status == 0 and type(self.hit) is not SampleHit)
                or (self.status != 0 and self.hit is not None)):
            raise ValueError("placement sample only publishes a hit on status0")


def _service_boolean(reply):
    if reply is NotImplemented or type(reply) is bool:
        return reply
    raise ValueError("placement sample service requires boolean or explicit unknown")


def static_surface_allowed(normal, flags, ignore_flag):
    """CA6125 static/no-body-handle fallback, not the dynamic geometry branch.

    Caller must establish the native no-body sentinel/type branch. Mesh material
    flags and normals are not inferred from a generic triangle collision hit.
    """
    normal = _vector(normal)
    if type(flags) is not int or not 0 <= flags <= 0xFFFF or type(ignore_flag) is not bool:
        raise ValueError("placement static surface requires uint16 flags and boolean policy")
    return not flags & (0 if ignore_flag else 1) and normal.y > CONTACT_NORMAL_SWITCH


def validate_sample(query, reference_point, *, exclusion, limit_height, maximum_y,
                    cast, clipped, surface, final):
    """C9E800 checks; callbacks own actual casts, clipping, surfaces and geometry.

    Cast returns SampleHit, known miss None, or unknown NotImplemented. Clipping
    must supply a resolved reply here; mutating native clipping is not admitted.
    """
    if type(query) is not SampleQuery:
        raise ValueError("placement sample requires a typed query")
    reference_point = _vector(reference_point)
    exclusion, maximum_y = _scalar(exclusion), _scalar(maximum_y)
    if type(limit_height) is not bool:
        raise ValueError("placement sample height flag must be boolean")
    hit = cast(query)
    if hit is NotImplemented:
        return NotImplemented
    if hit is None:
        return SampleResult(3)
    if type(hit) is not SampleHit:
        raise ValueError("placement sample cast requires typed hit, miss or explicit unknown")
    reply = _service_boolean(clipped(hit))
    if reply is NotImplemented:
        return NotImplemented
    if reply:
        return SampleResult(3)
    reply = _service_boolean(surface(hit))
    if reply is NotImplemented:
        return NotImplemented
    if not reply:
        return SampleResult(3)
    if limit_height and hit.contact.point.y > maximum_y:
        return SampleResult(1)
    separation = _subtract(reference_point, hit.contact.point)
    if _dot(separation, separation) < expr.f32(exclusion * exclusion):
        return SampleResult(2)
    segment = _subtract(query.end, query.start)
    interpolated = _offset(query.start, segment, hit.contact.fraction)
    correction = min(expr.f32(query.start.y - interpolated.y), CONTACT_PADDING)
    corrected = _offset(hit.contact.point, expr.Vec3(0, 1, 0), correction)
    reply = _service_boolean(final(hit, corrected))
    if reply is NotImplemented:
        return NotImplemented
    return SampleResult(0, hit) if reply else SampleResult(3)


@dataclass(frozen=True)
class SamplingPlan:
    primary_count: int
    fallback_count: int
    primary_step: float
    fallback_step: float

    def __post_init__(self):
        if (type(self.primary_count) is not int or not 0 <= self.primary_count <= 2
                or type(self.fallback_count) is not int or not 0 <= self.fallback_count < (1 << 31)):
            raise ValueError("placement sample counts are outside the admitted profile")
        for name in ('primary_step', 'fallback_step'):
            value = _scalar(getattr(self, name))
            if value <= 0:
                raise ValueError("placement sampling requires positive steps")
            object.__setattr__(self, name, value)


def sampling_plan(primary_length, fallback_length, offset, spacing):
    """CA1262..129E/CA1924..1943 counts, for finite nonnegative distances."""
    primary_length, fallback_length, offset, spacing = (
        _scalar(v) for v in (primary_length, fallback_length, offset, spacing))
    spacing = abs(spacing)
    if primary_length < 0 or fallback_length < 0 or offset <= 0 or spacing <= 1.1920928955078125e-7:
        raise ValueError("placement sampling distances/steps are outside the admitted profile")

    def count(length, step):
        value = expr.f32(expr.f32(length / step) + .5)
        if not math.isfinite(value) or value >= 1 << 31:
            raise ValueError("placement sample count must fit a signed native integer")
        return math.trunc(value)

    fallback_count = count(fallback_length, spacing)
    fallback_step = expr.f32(fallback_length / expr.f32(fallback_count)) if fallback_count else spacing
    return SamplingPlan(min(2, count(primary_length, offset)), fallback_count, offset, fallback_step)


@dataclass(frozen=True)
class Candidate:
    """A sample admitted by explicit services, before frame-dependent selection."""

    fallback: bool
    index: int
    hit: SampleHit

    def __post_init__(self):
        if (type(self.fallback) is not bool or type(self.index) is not int
                or not 0 <= self.index < (1 << 31) or type(self.hit) is not SampleHit):
            raise ValueError("placement candidate requires a phase, native index and typed hit")


def collect_candidates(plan, *, stride, start, preferred, shorten, probe, accept):
    """CA18BD..CA1CF3 admitted sampling loops, without allocation failure policy.

    probe(fallback, index) supplies a SampleResult. accept is evaluated only on
    status0, after the native result has been installed as the evaluation context.
    Resolved nonnegative counters do not take the native cached-index detour;
    preferred is retained/validated here rather than assigned invented effects.
    """
    if type(plan) is not SamplingPlan:
        raise ValueError("placement collection requires a typed sampling plan")
    if (type(stride) is not int or not 0 < stride < (1 << 31)
            or type(start) is not int or not 0 <= start < (1 << 31)
            or type(preferred) is not int or not -(1 << 31) <= preferred < (1 << 31)
            or type(shorten) is not bool):
        raise ValueError("placement sample controls are outside the admitted profile")
    if (max(plan.primary_count, plan.fallback_count) + stride > 1 << 31
            or (shorten and plan.fallback_count > (1 << 31) - 3)):
        raise ValueError("placement sample counters must not overflow native signed increments")
    candidates = []
    for fallback in (False, True):
        index, stop = (start, plan.fallback_count) if fallback else (0, plan.primary_count)
        while index < stop:
            result = probe(fallback, index)
            if result is NotImplemented:
                return NotImplemented
            if type(result) is not SampleResult:
                raise ValueError("placement probe requires a typed result or explicit unknown")
            if result.status == 0:
                allowed = _service_boolean(accept(fallback, index, result.hit))
                if allowed is NotImplemented:
                    return NotImplemented
                if allowed:
                    candidates.append(Candidate(fallback, index, result.hit))
                    if shorten:
                        if not fallback:
                            break
                        stop = min(stop, index + 3)
            index += stride
    return tuple(candidates)


def select_candidate(candidates, reference, transform):
    """CA21CD..CA22B1: closest transformed screen-y, first strict winner retained."""
    reference = _vector(reference)
    transformed = transform(reference)
    if transformed is NotImplemented:
        return NotImplemented
    transformed = _vector(transformed)
    best, distance = None, 3.4028234663852886e38
    for candidate in candidates:
        if type(candidate) is not Candidate:
            raise ValueError("placement selection requires typed candidates")
        point = transform(candidate.hit.contact.point)
        if point is NotImplemented:
            return NotImplemented
        point = _vector(point)
        difference = abs(expr.f32(point.y - transformed.y))
        if difference < distance:
            best, distance = candidate, difference
    return best


def _matrix(value):
    if type(value) is not tuple or len(value) != 4:
        raise ValueError("placement projection requires four resolved matrix columns")
    if any(type(column) is not tuple or len(column) != 4 for column in value):
        raise ValueError("placement projection columns require four components")
    return tuple(tuple(_scalar(v) for v in column) for column in value)


def _matrix_point(matrix, point):
    values = []
    for row in range(4):
        value = expr.f32(expr.f32(matrix[1][row] * point.y) + expr.f32(matrix[0][row] * point.x))
        value = expr.f32(value + expr.f32(matrix[2][row] * point.z))
        values.append(expr.f32(value + matrix[3][row]))
    return tuple(values)


def _cross(a, b):
    return expr.Vec3(expr.f32(expr.f32(a.y * b.z) - expr.f32(a.z * b.y)),
                     expr.f32(expr.f32(a.z * b.x) - expr.f32(a.x * b.z)),
                     expr.f32(expr.f32(a.x * b.y) - expr.f32(a.y * b.x)))


def view_matrix(position, forward, up, near):
    """1A77779..1A778F3 view construction from admitted decoded pose/basis.

    Preserve raw basis values and the inverse's float32 operation order. The
    scalar reciprocal seed is compared with emulated974260; hardware estimate
    and FP-environment parity, input normalization and owner binding are external.
    """
    position, forward, up = _vector(position), _vector(forward), _vector(up)
    near = _scalar(near)
    right = _cross(up, forward)
    a, b, c = _cross(up, forward), _cross(forward, right), _cross(right, up)
    determinant = _dot(right, a)
    if determinant == 0 or not math.isfinite(determinant):
        raise ValueError("placement view requires a finite nonsingular decoded basis")
    reciprocal = _scalar(1 / determinant)
    reciprocal = expr.f32(expr.f32(2 - expr.f32(determinant * reciprocal)) * reciprocal)
    rows = tuple(expr.Vec3(*(expr.f32(v * reciprocal) for v in row)) for row in (a, b, c))
    eye = _subtract(position, expr.Vec3(*(expr.f32(near * v) for v in forward)))
    columns = tuple((*column, 0) for column in zip(*rows, strict=True))
    translation = (*(-_dot(eye, row) for row in rows), 1)
    return _matrix((*columns, translation))


@dataclass(frozen=True)
class ProjectedPoint:
    """1A77F80 returns undivided clip-z; below the view-z guard writes nothing."""

    point: expr.Vec3 | None
    clip_depth: float

    def __post_init__(self):
        if self.point is not None:
            object.__setattr__(self, 'point', _vector(self.point))
        object.__setattr__(self, 'clip_depth', _scalar(self.clip_depth))


@dataclass(frozen=True)
class CameraProjection:
    """Resolved 1A77F80 view/clip matrices and viewport; construction is external.

    Matrices are four columns of four finite float32 components. The native
    encoded matrix transport, key services, camera basis/FOV and viewport lookup
    are separate contracts. No arbitrary world-height substitution is made.
    """

    view: tuple
    clip: tuple
    perspective: bool
    viewport: tuple

    def __post_init__(self):
        object.__setattr__(self, 'view', _matrix(self.view))
        object.__setattr__(self, 'clip', _matrix(self.clip))
        if type(self.perspective) is not bool or type(self.viewport) is not tuple or len(self.viewport) != 4:
            raise ValueError("placement camera requires a boolean mode and four viewport bounds")
        object.__setattr__(self, 'viewport', tuple(_scalar(v) for v in self.viewport))

    def project(self, point):
        """Original matrix order, depth guard, optional divide and viewport mapping."""
        point = _vector(point)
        camera = _matrix_point(self.view, point)
        if camera[2] < 1.1920928955078125e-6:
            return ProjectedPoint(None, -1)
        projected = _matrix_point(self.clip, expr.Vec3(*camera[:3]))
        clip_depth = projected[2]
        if self.perspective:
            if projected[3] == 0:
                raise ValueError("placement perspective divide is outside the finite admitted profile")
            inverse = expr.f32(1 / projected[3])
            projected = tuple(expr.f32(v * inverse) for v in projected)
        left, top, right, bottom = self.viewport
        x = expr.f32(expr.f32(expr.f32(right - left)
                              * expr.f32(expr.f32(projected[0] + 1) * .5)) + left)
        y = expr.f32(expr.f32(expr.f32(bottom - top)
                              * expr.f32(.5 - expr.f32(projected[1] * .5))) + top)
        return ProjectedPoint(expr.Vec3(x, y, projected[2]), clip_depth)

    def selection_point(self, point):
        """Admit only written outputs; unknown caller-buffer state stays unknown."""
        result = self.project(point)
        return result.point if result.point is not None else NotImplemented


@dataclass(frozen=True)
class PerspectiveMatrix:
    """1A86910 matrix and separately clamped half-angle metadata."""

    matrix: tuple
    horizontal_half: float
    vertical_half: float

    def __post_init__(self):
        object.__setattr__(self, 'matrix', _matrix(self.matrix))
        object.__setattr__(self, 'horizontal_half', _scalar(self.horizontal_half))
        object.__setattr__(self, 'vertical_half', _scalar(self.vertical_half))


def perspective_matrix(horizontal_half, vertical_half, viewport, near, far):
    """1A86910/1A86F30, default shift/scale and forward depth profile.

    The sin/cos ABI is admitted as float32-rounded values. Derived angles are
    checked against original2500A04. Camera basis, viewport service and nondefault
    shift/scale/reverse-depth profiles are external.
    """
    horizontal_half, vertical_half, near, far = (
        _scalar(v) for v in (horizontal_half, vertical_half, near, far))
    if type(viewport) is not tuple or len(viewport) != 4:
        raise ValueError("placement perspective requires four viewport bounds")
    left, top, right, bottom = (_scalar(v) for v in viewport)
    width, height = expr.f32(right - left), expr.f32(bottom - top)
    if (not math.isfinite(width) or not math.isfinite(height)
            or width <= 0 or height <= 0 or near <= 0 or far <= near
            or not 0 <= horizontal_half < PI_OVER_TWO or not 0 <= vertical_half < PI_OVER_TWO):
        raise ValueError("placement perspective is outside the finite forward-depth profile")
    minimum = 0.02500000037252903

    def tangent_extent(angle):
        sine, cosine = expr.f32(math.sin(angle)), expr.f32(math.cos(angle))
        if cosine == 0:
            raise ValueError("placement perspective requires finite tangent extents")
        return expr.f32(expr.f32(sine * near) / cosine)

    if horizontal_half != 0:
        horizontal_half = max(horizontal_half, minimum)
        x = tangent_extent(horizontal_half)
        if vertical_half != 0:
            y = tangent_extent(vertical_half)
        else:
            y = expr.f32(expr.f32(height / width) * x)
            vertical_half = expr.f32(math.atan2(y, near))
    else:
        vertical_half = max(vertical_half, minimum)
        y = tangent_extent(vertical_half)
        x = expr.f32(expr.f32(width / height) * y)
        horizontal_half = expr.f32(math.atan2(x, near))
    if not all(math.isfinite(v) and v > 0 for v in (x, y)):
        raise ValueError("placement perspective requires positive finite frustum extents")
    # Native's horizontal bounds are stored positive then negative; keep its
    # mirrored-x coefficient rather than substituting a conventional matrix.
    inv_x, inv_y = expr.f32(1 / expr.f32(-x - x)), expr.f32(1 / expr.f32(y - -y))
    inv_depth = expr.f32(1 / expr.f32(far - near))
    twice_near = expr.f32(near + near)
    matrix = (
        (expr.f32(twice_near * inv_x), 0, 0, 0),
        (0, expr.f32(twice_near * inv_y), 0, 0),
        (0, 0, expr.f32(far * inv_depth), 1),
        (0, 0, expr.f32(expr.f32(-far * near) * inv_depth), 0),
    )
    return PerspectiveMatrix(matrix, max(min(horizontal_half, 1.5), minimum),
                             max(min(vertical_half, 1.5), minimum))
