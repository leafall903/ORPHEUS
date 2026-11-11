"""Explicit reconstructed Dragonstrike host using an external authored profile.

Rate units, seven-sphere overlap deduplication and tick sampling are reconstructed
server policy. This does not execute0132/0118 or establish native presentation.
"""

import hashlib
import json
import math
from dataclasses import dataclass
from pathlib import Path

HANZO = 0x02E0000000000005
INITIAL = 0x040000000000034F
LOADOUT = 0x0B90000000000008
STEP = 0.016
EPSILON = 1e-9


@dataclass(frozen=True)
class Profile:
    initial_definition: int
    initial_hit_limit: int
    definition: int
    lifetime: float
    speed: float
    radius: float
    spacing: float
    segments: int
    core_factor: float
    core_rate: float
    outer_rate: float
    forward_gate: float
    source_digest: str

    def __post_init__(self):
        numbers = (self.lifetime, self.speed, self.radius, self.spacing,
                   self.core_factor, self.core_rate, self.outer_rate, self.forward_gate)
        if any(type(v) not in (int, float) or not math.isfinite(v) or v <= 0 for v in numbers):
            raise ValueError("dragon profile needs positive finite literals")
        if (self.initial_definition != INITIAL or self.definition != 0x04000000000002BA
                or type(self.initial_hit_limit) is not int or not 1 <= self.initial_hit_limit <= 255
                or type(self.segments) is not int or not 1 <= self.segments <= 32
                or self.lifetime > 60 or self.core_factor > 1):
            raise ValueError("unsupported dragon profile")
        if (not isinstance(self.source_digest, str) or len(self.source_digest) != 64
                or any(c not in "0123456789abcdef" for c in self.source_digest)):
            raise ValueError("dragon profile needs its source digest")


def load_profile(path: Path, *, reconstructed_rates: bool = False) -> Profile:
    """Require the caller to declare the per-second interpretation of0118 amounts."""
    if not reconstructed_rates:
        raise ValueError("dragon damage-rate units require an explicit reconstructed policy")
    return profile_from_data(json.loads(path.read_text(encoding="utf-8")))


def profile_from_data(data) -> Profile:
    if data.get("format_version") != 1 or data.get("hero") != f"0x{HANZO:016X}":
        raise ValueError("unsupported Hanzo profile")
    initial, dragon = data["initial"], data["dragon"]
    if (dragon["graph"] != "0x0580000000000132" or dragon["status_graph"] != "0x0580000000000118"
            or dragon["status_definition"] != "0x04A0000000000040"):
        raise ValueError("dragon continuation chain changed")
    for field in ("central_FB3F6CA9", "outer_FB3F6CA9"):
        if dragon[field].get("$") != "STUConfigVarBool" or dragon[field].get("m_value") != 1:
            raise ValueError("dragon rate flag changed")
    sources = data.get("source_sha256")
    if not isinstance(sources, dict) or not sources or any(
        not isinstance(k, str) or not isinstance(v, str) or len(v) != 64
        or any(c not in "0123456789abcdef" for c in v) for k, v in sources.items()
    ):
        raise ValueError("profile source fingerprints are missing")
    tracks = dragon["tracking_nodes"]
    if not isinstance(tracks, list) or len(tracks) != len(set(tracks)):
        raise ValueError("dragon trackers are not unique")
    literals = dragon["source_literals"]
    digest = hashlib.sha256(json.dumps(sources, sort_keys=True).encode()).hexdigest()
    return Profile(int(initial["definition"], 16), initial["body_hit_limit_literal"],
                   int(dragon["definition"], 16), literals["215"], literals["681"],
                   literals["422"], literals["479"], len(tracks), dragon["core_radius_factor"],
                   dragon["central_amount_literal"], dragon["outer_amount_literal"],
                   dragon["forward_gate_literal"], digest)


@dataclass
class Flight:
    shooter: object
    source_entity: int
    team: int
    origin: tuple
    direction: tuple
    profile: Profile
    last_tick: float
    age: float = 0.0


def _dot(a, b):
    return sum(x * y for x, y in zip(a, b, strict=True))


def _rate(flight: Flight, target, at: float) -> float:
    from ow174.game.combat import segment_distance

    profile = flight.profile
    offset = tuple(x - y for x, y in zip(target.feet, flight.origin, strict=True))
    projected = _dot(offset, flight.direction)
    if projected <= profile.forward_gate:
        return 0.0
    a, b = target.segment()
    touched = False
    for section in range(profile.segments):
        age = at - section * profile.spacing / profile.speed
        if age < 0:
            continue
        centre = tuple(x + y * profile.speed * age
                       for x, y in zip(flight.origin, flight.direction, strict=True))
        if segment_distance(centre, a, b) <= profile.radius + target.radius:
            touched = True
            break  # one status dose per target, not one per overlapping sphere
    if not touched:
        return 0.0
    radial = math.sqrt(max(0.0, _dot(offset, offset) - projected * projected))
    return profile.core_rate if radial < profile.radius * profile.core_factor else profile.outer_rate


def advance(combat, flight: Flight, tick: int, *, poses) -> bool:
    """Advance bounded16ms doses; return false after expiry, match end or source leave."""
    if combat.match.ended or getattr(flight.shooter, "gone", False):
        return False
    elapsed = tick - flight.last_tick
    if elapsed <= 0:
        return True
    cursor = flight.last_tick
    end = min(tick, cursor + max(0.0, flight.profile.lifetime - flight.age) / STEP)
    flight.last_tick = tick
    while cursor < end - EPSILON:
        # A fractional birth only contributes to the remainder of its world frame.
        boundary = min(end, math.floor(cursor + EPSILON) + 1)
        part = (boundary - cursor) * STEP
        at = flight.age + part / 2
        damage_tick = math.ceil(boundary - EPSILON)
        for target in combat._targets():
            if target.owner is flight.shooter or target.entity == flight.source_entity:
                continue
            if not combat.match.game_map.free_for_all and target.team == flight.team:
                continue
            if combat.vitals_of(target).dead:
                continue
            pose, recall = combat.sample_impact_pose(target, damage_tick, poses)
            rate = _rate(flight, pose, at)
            if rate:
                combat._damage(flight.shooter, target, rate * part, False, LOADOUT, damage_tick,
                               world_tick=damage_tick, damage_over_time=True,
                               source_entity=flight.source_entity, recall_protected=recall)
        flight.age += part
        cursor = boundary
    return flight.age < flight.profile.lifetime - EPSILON
