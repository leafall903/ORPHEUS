"""Explicit reconstructed Sonic attachment/search host with authored010E flag stacks.

Only010E/node9 is executed, not the complete status graph. Sphere/capsule search,
through-wall filtering, continuous refresh, attachment and viewer projection are
host policy; native cadence, filter flags, bones and presentation remain open.
"""

import hashlib
import json
import math
from dataclasses import dataclass, field

from ow174.game.script import expr, graph, runtime

HANZO = 0x02E0000000000005
DEFINITIONS = (0x0400000000000149, 0x040000000000020D)
REVEALED = 7982
TICK = 0.016


@dataclass(frozen=True)
class Profile:
    duration: float
    radius: float
    status: dict
    source_digest: str


def load_profile(path, *, reconstructed_geometry=False):
    if not reconstructed_geometry:
        raise ValueError("Sonic attachment/search requires explicit reconstructed policy")
    return profile_from_data(json.loads(path.read_text(encoding="utf-8")))


def profile_from_data(data) -> Profile:
    if data.get("format_version") != 1 or data.get("hero") != f"0x{HANZO:016X}":
        raise ValueError("unsupported Sonic profile")
    duration, radius, record = data["duration"], data["radius"], data["status_graph"]
    if (any(type(v) not in (int, float) or not math.isfinite(v) or not 0 < v <= 60
            for v in (duration, radius)) or record.get("index") != 0x10E):
        raise ValueError("invalid Sonic profile quantities")
    node = graph.Graph(record).node(9)
    if (node is None or node.cls != "STUStatescriptStateStack"
            or expr.lvalue(node.field("m_out_Var")) != (expr.ENTITY, REVEALED)
            or (node.field("m_value") or {}).get("$") != "STUConfigVarBool"
            or (node.field("m_value") or {}).get("m_value") != 1
            or node.field("m_priority") is not None
            or any(isinstance(v, dict) and v.get("links") for v in node.fields.values())):
        raise ValueError("Sonic reveal flag changed")
    sources = data.get("source_sha256")
    if not isinstance(sources, dict) or len(sources) != 5 or any(
        not isinstance(v, str) or len(v) != 64 or any(c not in "0123456789abcdef" for c in v)
        for v in sources.values()
    ):
        raise ValueError("Sonic profile needs source fingerprints")
    digest = hashlib.sha256(json.dumps(sources, sort_keys=True).encode()).hexdigest()
    return Profile(float(duration), float(radius), record, digest)


@dataclass
class Beacon:
    source: object
    team: int
    position: tuple
    attached: int | None
    offset: tuple
    born: float
    profile: Profile
    statuses: dict = field(default_factory=dict)  # target body -> owned010E Instance


def remove(beacon, entity):
    instance = beacon.statuses.pop(entity)
    component = instance.component
    if component.fault is None and not instance.stopped:
        component.destroy(instance)  # unlink this provider; overlapping stacks remain


def advance(fight, beacon, tick):
    if getattr(beacon.source, "gone", False) or (tick - beacon.born) * TICK >= beacon.profile.duration:
        for entity in list(beacon.statuses):
            remove(beacon, entity)
        return False
    if tick < beacon.born:
        return True
    targets = {t.entity: t for t in fight._targets()}
    attached = targets.get(beacon.attached)
    if attached is not None:
        beacon.position = tuple(a + b for a, b in zip(attached.feet, beacon.offset, strict=True))
    elif beacon.attached is not None:
        beacon.attached = None  # missing/replaced body freezes the last point, never follows its replacement
    selected = set()
    for target in targets.values():
        if target.owner is beacon.source or fight.vitals_of(target).dead:
            continue
        if not fight.match.game_map.free_for_all and target.team == beacon.team:
            continue
        a, b = target.segment()
        from ow174.game.combat import segment_distance

        if max(0.0, segment_distance(beacon.position, a, b) - target.radius) > beacon.profile.radius:
            continue
        script = getattr(target.owner, "body_script", None)
        if script is None or script.component.fault is not None:
            continue
        component = script.component
        if component.strict:
            continue  # no reconstructed geometry/status bridge in strict engine execution
        selected.add(target.entity)
        old = beacon.statuses.get(target.entity)
        if old is not None and old.component is script.component and not old.stopped:
            continue
        if old is not None:
            remove(beacon, target.entity)
        definitions = [*component.instances.values(), *component.destroyed]
        definition = next((i.graph for i in definitions if i.graph.index == 0x10E), None)
        if definition is None:
            definition = graph.Graph(beacon.profile.status)
        instance = component.create(definition)
        instance.disabled_message_entries.update(
            index for index, node in enumerate(definition.entries)
            if node is not None and node.cls == "STUStatescriptEntryGameMessage"
        )
        # Directly activate the authored flag stack. Do not claim status start/
        # stop messages, score, effects, owner/filter services or full010E execution.
        instance.state(definition.node(9).state).request(runtime.BEGIN)
        component.run_now()
        beacon.statuses[target.entity] = instance
    for entity in set(beacon.statuses) - selected:
        remove(beacon, entity)
    return True


def visible(beacon, viewer, entity, free_for_all):
    if entity not in beacon.statuses:
        return False
    return viewer is beacon.source if free_for_all else viewer.team == beacon.team
