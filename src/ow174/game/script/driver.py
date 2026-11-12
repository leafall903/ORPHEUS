"""A player's hero body run on the server, and the statescript frames that keep the owning client's
prediction.

The client predicts its own body: it runs the networked nodes of the body's instances from its own input
and records undo history per command frame. It does not run the server's part (the Entries of instances
the server made, the instances' first states), and it rolls back whatever the server does not confirm. So
the server runs the same graphs from the same input (runtime.py) and sends, for every command frame K it
has processed, an owner frame with C = 1 and CmFD = K: everything that differs from what the client last
confirmed, and every instance's pending timers.

Frames, in order:
1. the body's create (match.py);
2. a plain full frame (H = 0): every instance and every variable, every state off. The client binds its
   own instances 1..N and makes the server's (the weapon manager, the weapons, the SubScript children);
3. once it arrived: an owner delta with C = 0 at the newest command frame K: every state that is on, the
   variables that have bindings, the timers. Retry the same chunk until it is acknowledged;
4. once acknowledged and SETTLE_FRAMES have passed, an owner delta with C = 1 for every new K.
A correction describes the change since the last chunk the client acknowledged, plus everything the
chunks sent after it carried (the client may have applied them), so it needs no resend.
"""

import json
import logging
import math
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field, replace
from functools import lru_cache
from typing import TYPE_CHECKING

from ow174.game import animation_pose, pools, ult_charge
from ow174.game.bits import BitWriter
from ow174.game.commands import FRAME_HALF, FRAME_MASK, frame_distance
from ow174.game.content import SOLDIER
from ow174.game.script import (
    expr,
    graph,
    grapple,  # noqa: F401 - registers the authored launch trace
    nodes,
    runtime,
    spray_placement,
)
from ow174.game.script.codecs import stec_classes
from ow174.game.script.expr import INSTANCE, Asset, Entity, Vec3, f32
from ow174.game.statescript import (
    PAYLOAD_REQUIRED,
    Binding,
    EventWire,
    InstanceWire,
    StateWire,
    body_full_frame,
    chunk,
    family,
    owner_delta,
    sendable,
)
from ow174.game.world import UNIT_ENTITY_SCALE
from ow174.paths import (
    DATA_DIR,
    EMOTE_ANY_CATEGORY,
    EMOTE_CLIP_DATA,
    EMOTE_DURATION_DATA,
    EMOTE_SCALAR_DATA,
    GREETING_DURATION_DATA,
    WHEEL_POSE_DATA,
)

if TYPE_CHECKING:
    from ow174.game.match import Match
    from ow174.game.mover import VelocityChange
    from ow174.game.script import play
    from ow174.game.world import EntityUpdate

log = logging.getLogger("ow174.script")


@lru_cache(maxsize=2)
def wheel_animation_durations(path):
    try:
        rows = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    return {
        (int(blend, 16), int(alias, 16), int(category, 16)): f32(seconds)
        for blend, aliases in rows.items()
        for alias, categories in aliases.items()
        for category, seconds in categories.items()
        if type(seconds) in (float, int) and math.isfinite(seconds) and 0 < seconds <= 3600
    }


@lru_cache(maxsize=2)
def wheel_animation_scalars(path):
    try:
        rows = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    result = {}
    for blend, aliases in rows.items():
        for alias, categories in aliases.items():
            for category, track in categories.items():
                key = int(blend, 16), int(alias, 16), int(category, 16)
                # An unsupported exact category must shadow the generic default.
                result[key] = None
                if not isinstance(track, dict):
                    continue
                times, values = track.get("times"), track.get("values")
                if times == values == []:
                    result[key] = 0.0
                elif (isinstance(times, list) and isinstance(values, list) and len(times) == len(values) == 1
                      and all(type(x) in (float, int) and math.isfinite(x) for x in (times[0], values[0]))):
                    result[key] = f32(values[0])
    return result


class CommandEpochError(ValueError):
    """A forward uint32 rollover needs an explicit new simulation/owner-stream lifecycle."""

    def __init__(self, previous: int, incoming: int) -> None:
        super().__init__(
            f"command frame rolled over from {previous} to {incoming}; "
            "establish a new body/owner-stream epoch before continuing"
        )
        self.previous = previous
        self.incoming = incoming


PRACTICE_RANGE_MODE = 0x0230000000000018
SETTLE_FRAMES = 16  # 0.25 s after the first owner frame, before the first C = 1 frame
MAX_GAP = 256  # command frames run at most to catch up
MAX_SHOTS = 256  # shots kept on the component's list for combat.py
MAX_REPORTS = 128  # owner frames kept on the reports list for projectiles.py
KILL_FEED_PULSERS = {0x01CF: 108, 0x0C18: 8}  # their client-only plug posts 0765.025 to the mode UI
EDGE_BITS = (  # command button bit -> logical button, in the client's order (0x7FF789C66AA0)
    (0x40, 11), (0x80, 12), (0x01, 20), (0x02, 29), (0x08, 35), (0x10, 36), (0x20, 37), (0x04, 30),
    (0x800, 31), (0x400, 14), (0x01, 111), (0x02, 112), (0x04, 113), (0x08, 114), (0x10, 110), (0x20, 116),
    (0x40, 115),
)  # fmt: skip
ACTION_BUTTONS = {1: 15, 2: 16, 3: 17, 4: 18, 5: 19, 6: 21, 7: 22, 8: 56, 9: 71, 16: 120}
ACTION_BUTTONS.update({17: 97, 18: 98, 19: 99})
ACTION_BUTTONS.update({action: 81 + action for action in range(10, 16)})  # 10..15 -> 91..96
# Entity variables no graph of the body writes: the server's own code sets them.
WEAPON_SLOTS, DEFAULT_SLOT, OWNER_HEALTH, HUD_HEALTH, HUD_MAX_HEALTH = 29, 16515, 253, 1732, 1737
NOT_SENT = {OWNER_HEALTH}  # the client derives it itself (never sent)
BODY_SCALE_OVERRIDE = 31210  # the common body graph's size modifier input
# The world's config vars whose server answer is the client's for a human player's own body (computed, or
# answered null/false). A BooleanSwitch that reads any other one is uncertain and is not sent.
TRUSTED_NATIVES = {
    "STU_1FF67CA6",  # GetLocalPlayerPossessedView: the body
    "STU_310D6529",  # GetThrottle: the command's throttles
    "STU_37F20D24",  # GetGameMode: the match's mode
    "STU_4A101787",  # always false
    "STU_A77AC2AC",  # false
    "STU_A3DEF368",  # the mode entity and its configured initial values, when present in the bundle
    "STU_DB742151",  # Sprint's #2333: GetPrimaryPossessorEntity (CD1940, an EntityID config) ...
    "STU_295D117D",  # ... the AI predicate applied to that possessor is false for a player ...
    "STU_D3EB8D87",  # ... the selected controller's component 92 is absent for a player
}
POLLED_SWITCHES = {"STUStatescriptStateBooleanSwitch", "STU_DEBD057B"}
# STU_C5243F93 action categories from the 104319 .096 records.
WHEEL_ACTION_PREFIX = 0x0A90000000000000
WHEEL_CATEGORY_TYPE = 0x0C880000000000CD
WHEEL_ACTION_CATEGORIES = {
    0x0D8000000000741E: frozenset((0x172, 0x19F, 0x1A0, 0x1A1, 0x1A2)),
    0x0D8000000000741F: frozenset((0x16F, 0x19B, 0x19C, 0x19D, 0x19E)),
    0x0D80000000007420: frozenset((0x173, 0x1A3, 0x1A4, 0x1A5, 0x1A6)),
    0x0D80000000007428: frozenset((*range(0x16B, 0x173), *range(0x1E5, 0x1F5), 0x1F7, 0x1F9)),
}
# Action-to-texture rows from 104319 01A5.054 (44A820).
WHEEL_CALLOUT_TEXTURES = {
    0x16B: 0x0C00000000005A0B,
    0x16C: 0x0C000000000039DD,
    0x16D: 0x0C000000000039DA,
    0x16E: 0x0C000000000039DB,
    0x16F: 0x0C00000000007114,
    0x170: 0x0C00000000005A0E,
    0x171: 0x0C00000000005A0D,
    0x172: 0x0C00000000005A0A,
    0x1E5: 0x0C0000000002A9FB,
    0x1E6: 0x0C0000000002AA02,
    0x1E7: 0x0C0000000002AA01,
    0x1E8: 0x0C0000000002A9F9,
    0x1E9: 0x0C0000000002A9F4,
    0x1EA: 0x0C0000000002A9F1,
    0x1EB: 0x0C0000000002A9FD,
    0x1EC: 0x0C0000000002A9EF,
    0x1ED: 0x0C0000000002A9FC,
    0x1EE: 0x0C0000000002A9F5,
    0x1EF: 0x0C0000000002AA00,
    0x1F0: 0x0C0000000002A9F2,
    0x1F1: 0x0C0000000002A9FA,
    0x1F2: 0x0C0000000002A9EC,
    0x1F3: 0x0C0000000002A9ED,
    0x1F4: 0x0C00000000005A0C,
    0x1F7: 0x0C0000000002A9FE,
    0x1F9: 0x0C00000000003A73,
}
# Static ray categories from the first request word, packed from m_category.
WHEEL_TARGET_CATEGORIES = {0x0690000000000079: 0x1FDF, 0x0690000000000111: 0x1FDF}
ABILITY_RAY_CATEGORIES = {0x069000000000002C: 0x1FDF}  # 104319 static cast profile
TRACK_TARGET_RAY_CATEGORIES = {
    **ABILITY_RAY_CATEGORIES,
    0x069000000000002D: 0x1FDF,  # default 104319 tracker profile, using its static CBC flags
}
DOOMFIST_PASSIVE_GRAPH = 0x0DDD
DOOMFIST_SHIELD_GRAPH = 0x0DF3
UPPERCUT_TARGET_GRAPH = 0x0E7D
BODY_PLAY_GRAPHS = {(DOOMFIST_PASSIVE_GRAPH, 4): DOOMFIST_SHIELD_GRAPH}
TARGET_PLAY_GRAPHS = {
    (0x0DD8, 22): UPPERCUT_TARGET_GRAPH,
    (0x160A, 42): 0x165A,  # Piledriver's per-victim movement reaction
}
WHEEL_HEALING_TAG = 0x0D60000000000308
WHEEL_GREETING_TAG = 0x0D60000000000661
ASHE_BODY = 0x040000000000251C
LEGACY_SYMMETRA_BODY = 0x040000000000020F
# Player body definitions (.003) with the authored 0308.06C target tag in 104319.
WHEEL_HEALING_BODIES = frozenset({
    0x04000000000000E4,  # Mercy
    0x0400000000000216,  # Zenyatta
    0x04000000000003FC,  # Lucio
    0x0400000000001424,  # Ana
    0x0400000000001B1B,  # Brigitte
    0x0400000000001C46,  # Moira
    0x04000000000029A6,  # Baptiste
})
# 104319 44A7D0, 44A980 and 44A950 return the zero-based index in these tables.
WHEEL_EMOTE_ACTIONS = (0x172, 0x1A1, 0x1A0, 0x19F)
WHEEL_VOICE_ACTIONS = (0x16F, 0x19D, 0x19C, 0x19B)
WHEEL_SPRAY_ACTIONS = (0x173, 0x1A5, 0x1A4, 0x1A3)
# The wheel's fixed, self-targeted PlayScript actions.
WHEEL_PLAY_GRAPHS = {
    (0x05AD, 109): 0x069A,
    (0x05AD, 117): 0x0686,
    (0x05AD, 131): 0x0686,
    (0x069A, 9): 0x1040,
}
MAX_WHEEL_CALLOUTS = 32
WHEEL_CALLOUT_MS = 2000
# The ultimate: its charge #342e is server data (no graph adds to it; the ultimate's graph sets it to 0 when
# it starts), its cost #338e comes from the Entries. Combat credits it (combat.py through ult_charge.py);
# the passive fills the rest in ULT_SECONDS (0: never) so a server with no combat rule still charges, and
# nothing charges while the ultimate runs.
ULT_CHARGE, ULT_COST = 342, 338
ULT_SECONDS = 60.0
VOLLEY = "STUStatescriptStateWeaponVolley"
_VOLLEY_INTRODUCTION = "volley_introduction"  # report metadata, not a cumulative wire key
THROTTLE_UNIT = f32(1 / 127)  # GetThrottle: x = right, y = forward (W is +y)


def button_edges(previous: int, buttons: int, action: int = 0) -> list[tuple[int, bool]]:
    """The logical-button edges of one command (0x7FF789C66AA0): one per changed bit, pressed = the bit is
    now set; an action byte presses and releases its button."""
    edges = []
    for bit, button in EDGE_BITS:
        if (previous ^ buttons) & bit:
            edges.append((button, bool(buttons & bit)))
    if action in ACTION_BUTTONS:
        edges += [(ACTION_BUTTONS[action], True), (ACTION_BUTTONS[action], False)]
    return edges


@dataclass(frozen=True)
class WheelCallout:
    serial: int
    instance: int
    start: int
    node: int
    time: int
    choice: object
    values: tuple


@dataclass(frozen=True)
class WheelSprayEvent(WheelCallout):
    placement: tuple


WHEEL_LOADOUT_TYPES = {
    "emote_aliases": 0x0C80,
    "emote_blend_trees": 0x0040,
    "spray_unlocks": 0x0250,
    "voice_effects": 0x0300,
}


def valid_wheel_slots(slots: tuple[int, ...], guid_type: int) -> bool:
    return isinstance(slots, tuple) and len(slots) <= 4 and all(
        type(guid) is int and (guid == 0 or (guid >> 48 == guid_type and guid & 0xFFFFFFFFFFFF))
        for guid in slots
    )


class BodyWorld(runtime.World):
    """What the body's graphs ask outside the body. Config vars the server cannot compute answer what the
    owning client sees in a normal game; the others are null (logged once)."""

    def __init__(self, hero: int, mode: int, body: int, body_definition: int) -> None:
        super().__init__(hero, mode)
        self.body = body
        self.body_definition = body_definition
        self.communication_wheel = False
        self.emote_aliases: tuple[int, ...] = ()
        self.emote_blend_trees: tuple[int, ...] = ()
        self.spray_unlocks: tuple[int, ...] = ()
        self.voice_effects: tuple[int, ...] = ()
        self.wheel_callouts: list[WheelCallout] = []
        self.velocity_requests = []  # sampled one-frame changes for the next posed frame
        self.throttles = (0, 0)  # the command being run: (forward, right), -127..127
        self.match: Match | None = None

    def position(self, entity: int) -> Vec3:
        if self.match is not None:
            position = self.match.script_body_position(entity, self.body)
            if position is not None:
                return position
        return super().position(entity)

    def movement_sample(self) -> int | None:
        if self.match is not None:
            return self.match.script_owner_movement_sample(self.body)
        return None

    def static_ray(
        self, origin: Vec3, direction: Vec3, length: float, category: int | None = None,
    ) -> runtime.RaycastResult | None:
        match = self.match
        if match is None or match.collision_world is None:
            return None
        mover = match._script_body_mover(self.body, self.body)
        norm = math.hypot(*direction)
        if (mover is None or not math.isfinite(norm) or norm == 0 or not math.isfinite(length)
                or length <= 0 or not all(math.isfinite(axis) for axis in origin)):
            return None
        geometry = match.collision_world
        if category is None:
            category = mover.category
        else:
            geometry = geometry.ray_geometry or geometry
        hit = geometry.cast_ray(tuple(origin), tuple(axis / norm for axis in direction), length, category)
        if hit is None:
            return runtime.RaycastResult(False, None, None, None)
        return runtime.RaycastResult(True, None, Vec3(*hit.point), Vec3(*hit.normal))

    def raycast(self, request: runtime.RaycastRequest) -> runtime.RaycastResult | None:
        # The authored 002C ground query uses the static collision service.
        config = request.filter_config or {}
        resource = config.get("m_C1903771")
        if (not isinstance(resource, dict) or resource.get("$") != "STU_20DB8AB5"
                or resource.get("m_resourceKey") is not None
                or request.radius != 0 or request.ignored_entities):
            return None
        category = ABILITY_RAY_CATEGORIES.get(expr.guid_of(resource.get("m_C1903771")))
        fields = {"$", "m_C1903771", "m_EE729DCB"}
        if (category is None
                or any(value not in (None, 0) for key, value in config.items() if key not in fields)):
            return None
        return self.static_ray(request.origin, request.direction, request.length, category)

    def resolve_spray(self, length: float) -> dict | None:
        if self.communication_wheel and self.match is not None:
            return self.match.resolve_body_spray(self.body, length)
        return None

    def queue_velocity_changes(
        self, frame: int, source: tuple[int, int], changes: tuple["VelocityChange", ...],
    ) -> None:
        if self.match is not None:
            self.match.queue_body_velocity_changes(self.body, frame, source, changes)

    def native(self, cfg: dict, instance):
        from ow174.game import mccree

        result = mccree.native(self, cfg, instance)
        if result is not NotImplemented:
            return result
        cls = cfg.get("$")
        if cls == "STU_8CBDD81C" and self.match is not None and instance is not None:
            entity = expr.to_entity(instance.evaluate(cfg.get("m_entity")))
            return self.match.script_body_position(entity, self.body)
        if cls == "STU_21A13145" and self.match is not None and instance is not None:
            entity = expr.to_entity(instance.evaluate(cfg.get("m_entity")))
            return self.match.script_body_eye_position(entity, self.body)
        if (cls == "STU_9B7648C3" and instance is not None and instance.graph.index == 0x1177
                and instance.component.entity == self.body
                and instance.evaluate(cfg.get("m_entity")) == Entity(self.body)):
            health = instance.component.entity_var(OWNER_HEALTH, create=False)
            return expr.to_float(health.value()) if health is not None else 0.0
        if cls == "STU_1FF67CA6":  # GetLocalPlayerPossessedView: the owner's client views its body
            return Entity(self.body)
        if cls == "STU_310D6529":  # GetThrottle (0x7FF789BDA130): the command's throttle bytes * f32(1/127)
            forward, right = self.throttles
            return Vec3(f32(right * THROTTLE_UNIT), f32(forward * THROTTLE_UNIT), 0.0)
        if cls == "STU_37F20D24":  # GetGameMode: the match's mode (what component 114 announces)
            return Asset(self.mode)
        if cls in {"STU_2200A66D", "STU_021D01CD", "STU_C8C4C20D"} and self.match is not None:
            entity = expr.to_entity(instance.evaluate(cfg.get("m_entity")))
            if expr.valid_entity(entity):
                facing = self.match.script_body_facing(entity, self.body, pitched=cls == "STU_2200A66D")
                if facing is not None:
                    if cls == "STU_C8C4C20D":
                        return f32(f32(math.atan2(facing.x, facing.z)) * 57.295780181884766)
                    return facing
        if cls == "STU_FDBB0B67":  # entity size used by the authored movement and effect graphs
            target = cfg.get("m_entity")
            component = instance.component
            if (component.entity == self.body and expr.valid_entity(self.body)
                    and isinstance(target, dict)
                    and target.get("$") in {"STU_B5A0CAF0", "STUConfigVarDynamic"}
                    and instance.evaluate(target) == Entity(self.body)):
                override = component.entity_var(BODY_SCALE_OVERRIDE, create=False)
                scale = override.value() if override is not None else None
                if scale is None or (expr.kind(scale) in ("int", "float") and scale == UNIT_ENTITY_SCALE):
                    return UNIT_ENTITY_SCALE
        if cls == "STU_4A101787":  # its evaluator (0x7FF789BFF730) always answers false
            return False
        if (cls == "STU_F7262B36" and self.match is not None and instance is not None
                and instance.graph.index == 0x0276 and self.body_definition == 0x0400000000000407
                and instance.evaluate(cfg.get("m_801B298F")) == Entity(self.body)):
            # The mech's authored startup lock waits for its component 43 owner.
            owner = next((player for player in self.match.players
                          if player.has_body and not player.gone and player.body == self.body), None)
            return Entity(owner.entity if owner is not None else 0)
        if self.communication_wheel and instance is not None and instance.graph.index == 0x004B:
            # BodyScript is the match-owned human body. 004B's untagged filter
            # and its player check gate the authored 05AD and 02CF wheels.
            gate = instance.graph.node(20)
            if cls == "STU_E18C7DE7" or (gate is not None and cfg == gate.field("m_condition")):
                return True
        if (cls == "STU_4460419A" and self.communication_wheel and instance is not None
                and instance.graph.index in (0x05AD, 0x02CF, 0x0686)):
            return self.wheel_action_has_category(cfg, instance)
        if self.communication_wheel and instance is not None:
            if cls == "STU_25ECB55F":
                tag = expr.guid_of(cfg.get("m_88B0050A"))
                if (tag == WHEEL_HEALING_TAG and instance.graph.index in (0x05AD, 0x0686)
                        and instance.evaluate(cfg.get("m_entity")) == Entity(self.body)):
                    return self.body_definition in WHEEL_HEALING_BODIES
                if (tag == WHEEL_GREETING_TAG and instance.graph.index == 0x0341
                        and instance.evaluate(cfg.get("m_entity")) == Entity(self.body)):
                    return self.body_definition == ASHE_BODY
            if cls == "STU_3E7F59D8" and instance.graph.index in (0x05AD, 0x069A):
                return self.wheel_slot(cfg, instance, WHEEL_EMOTE_ACTIONS)
            if cls == "STU_250DEABD" and instance.graph.index in (0x05AD, 0x023A):
                # CD7490 reads the equipped slot from the target body.
                target = cfg.get("m_target")
                if (isinstance(target, dict) and target.get("$") == "STU_33980609"
                        and instance.evaluate(target.get("m_entity")) == Entity(self.body)):
                    return self.equipped_wheel_asset(self.emote_aliases, instance.evaluate(cfg.get("m_slot")))
            if (cls == "STU_DF7C7C63" and instance.graph.index == 0x023A
                    and instance.evaluate(cfg.get("m_entity")) == Entity(self.body)):
                return self.equipped_wheel_asset(self.emote_blend_trees, instance.evaluate(cfg.get("m_slot")))
            if cls == "STU_1ECB66A0" and instance.graph.index == 0x0686:
                action = instance.evaluate(cfg.get("m_548E5344"))
                if isinstance(action, Asset) and action.guid & ~0xFFFFFFFF == WHEEL_ACTION_PREFIX:
                    return Asset(WHEEL_CALLOUT_TEXTURES.get(action.guid & 0xFFFFFFFF, 0))
                return Asset(0)
            if cls == "STU_955C40AE" and instance.graph.index == 0x0686:
                return self.wheel_slot(cfg, instance, WHEEL_VOICE_ACTIONS)
            if cls == "STU_2977339D" and instance.graph.index == 0x0686:
                # 93D1F0 resolves the equipped voice unlock to its .00D effect.
                target = cfg.get("m_target")
                if (isinstance(target, dict) and target.get("$") == "STU_F7262B36"
                        and instance.evaluate(target.get("m_801B298F")) == Entity(self.body)):
                    return self.equipped_wheel_asset(self.voice_effects, instance.evaluate(cfg.get("m_slot")))
            if (cls == "STU_17B517B9" and instance.graph.index == 0x02CF
                    and instance.evaluate(cfg.get("m_entity")) == Entity(self.body)):
                # 92E7E0 returns the equipped spray unlock itself.
                return self.equipped_wheel_asset(self.spray_unlocks, instance.evaluate(cfg.get("m_slot")))
            if cls == "STU_2E708EC1" and instance.graph.index == 0x02CF:
                return self.wheel_slot(cfg, instance, WHEEL_SPRAY_ACTIONS)
        if (self.communication_wheel and self.match is not None and instance is not None
                and instance.graph.index == 0x05AD):
            if cls == "STU_AC829876":
                gate = instance.graph.node(119)
                if gate is not None and cfg == gate.field("m_condition"):
                    target = instance.evaluate(cfg.get("m_entity"))
                    source = self.match._message_player(self.body)
                    recipient = self.match._message_player(expr.to_entity(target))
                    return (source is not None and recipient is not None
                            and (source is recipient or (not self.match.game_map.free_for_all
                                                         and source.team == recipient.team)))
            if cls == "STU_F76C486C":
                gate = instance.graph.node(120)
                if gate is not None and cfg == gate.field("m_condition")["m_configVars"][0]:
                    target = instance.evaluate(cfg.get("m_D63CBEC2"))
                    if self.match._message_player(expr.to_entity(target)) is not None:
                        # The match does not attach a pet owner to these player bodies.
                        # Node 120 short-circuits before its component43 comparison.
                        return Entity(0)
        if cls == "STU_783B4CF0" and self.match is not None and instance is not None:
            kind = cfg.get("m_7E8B666C") or {}
            pool_id = self._shield_pool_id(cfg)
            if (pool_id is not None and kind.get("$") == "STU_297E1A30"
                    and kind.get("m_7E8B666C") == 2
                    and instance.evaluate(cfg.get("m_entity")) == Entity(self.body)):
                return self.temporary_shield_current(self.body, pool_id)
        if cls == "STU_AC829876":
            target_filter = cfg.get("m_filter") or {}
            fields = {"$", "m_84065FD1", "m_66747E7D"}
            if (self.match is not None and instance is not None
                    and target_filter.get("$") == "STUConfigVarFilter"
                    and target_filter.get("m_84065FD1") == 1 and target_filter.get("m_66747E7D") == 2
                    and all(value in (None, 0) for key, value in target_filter.items() if key not in fields)):
                target = expr.to_entity(instance.evaluate(cfg.get("m_entity")))
                return self.match.script_hostile_body(self.body, target)
            expr.warn_once(cls, "entity filters (STU_AC829876) answer false")
            return False
        return super().native(cfg, instance)

    def nearby_enemy_count(self, owner: int, radius: float) -> int:
        if self.match is None or owner != self.body:
            return 0
        return self.match.combat.nearby_enemy_count(owner, radius)

    @staticmethod
    def _shield_pool_id(cfg: dict) -> int | None:
        identifier = cfg.get("m_7BE02F89") or {}
        guid = expr.guid_of(identifier.get("m_identifier"))
        if identifier.get("$") == "STUConfigVarIdentifier" and guid >> 52 == 0x0D8:
            return guid & 0xFFFFFFFF
        return None

    def set_temporary_shield(
        self, owner: int, pool_id: int, amount: float, *, contributor: tuple[int, int] | None = None,
    ) -> None:
        if self.match is not None and owner == self.body:
            self.match.combat.set_temporary_shield(owner, pool_id, amount, contributor=contributor)

    def temporary_shield_current(
        self, owner: int, pool_id: int, *, contributor: tuple[int, int] | None = None,
    ) -> float:
        if self.match is None or owner != self.body:
            return 0.0
        return self.match.combat.temporary_shield_current(owner, pool_id, contributor=contributor)

    @staticmethod
    def equipped_wheel_asset(slots: tuple[int, ...], slot) -> Asset:
        return Asset(slots[slot] if type(slot) is int and 0 <= slot < len(slots) else 0)

    @staticmethod
    def wheel_action_has_category(cfg: dict, instance) -> bool:
        category = (cfg.get("m_D929182B") or {}).get("m_value") or {}
        if expr.guid_of(category.get("m_type")) != WHEEL_CATEGORY_TYPE:
            return False
        actions = WHEEL_ACTION_CATEGORIES.get(expr.guid_of(category.get("m_id")))
        if actions is None:
            return False
        action = instance.evaluate(cfg.get("m_548E5344"))
        return (isinstance(action, Asset) and action.guid & ~0xFFFFFFFF == WHEEL_ACTION_PREFIX
                and (action.guid & 0xFFFFFFFF) in actions)

    @staticmethod
    def wheel_slot(cfg: dict, instance, actions: tuple[int, ...]) -> int:
        action = instance.evaluate(cfg.get("m_548E5344"))
        # 069A receives the selected action in a one-item PlayScript parameter array.
        if isinstance(action, tuple) and len(action) == 1:
            action = action[0]
        if isinstance(action, Asset) and action.guid & ~0xFFFFFFFF == WHEEL_ACTION_PREFIX:
            index = action.guid & 0xFFFFFFFF
            if index in actions:
                return actions.index(index)
        return -1

    def play_script(self, instance: runtime.Instance, node: graph.Node) -> None:
        from ow174.game.script import play

        key = (instance.graph.index, node.pos)
        target_graph = TARGET_PLAY_GRAPHS.get(key)
        index = target_graph if target_graph is not None else BODY_PLAY_GRAPHS.get(key)
        if index is None and self.communication_wheel:
            index = WHEEL_PLAY_GRAPHS.get(key)
        if index is None:
            super().play_script(instance, node)
            return
        resource = play.resolve_resource(instance, node)
        if resource is None or resource.guid != ((graph.GRAPH_TYPE << 48) | index):
            return
        request = play.resolve_request(
            instance, node, resource,
            context_provider=lambda: play.InvocationContext(self.body, self.body, instance.source_entity()),
        )
        if request is None:
            return
        if target_graph is not None:
            if self.match is not None:
                self.match.queue_body_play(self.body, request)
        elif request.targets == (self.body,):
            self.submit_body_play(instance.component, request)

    @staticmethod
    def submit_body_play(component: runtime.Component, request: "play.Request") -> bool:
        from ow174.game.script import play_queue

        definition = graph.graph(request.resource.guid & 0xFFFFFFFFFFFF)
        if definition is None:
            return False
        play_queue.Queue(component, lambda _: play_queue.Load("ready", definition)).submit(request)
        return True

    def retires_stopped_play(self, instance: runtime.Instance) -> bool:
        index = instance.graph.index
        return index == DOOMFIST_SHIELD_GRAPH or index in TARGET_PLAY_GRAPHS.values()

    def _wheel_animation_key(self, instance: runtime.Instance, node: graph.Node) -> tuple | None:
        parent = instance.parent
        if not self.communication_wheel or parent is None:
            return None
        context = (instance.graph.index, parent.graph.index)
        if context not in ((0x023A, 0x069A), (0x0341, 0x0686)):
            return None
        alias = instance.evaluate(node.field("m_11E0A658"))
        if not expr.truthy(alias):
            # A zero alias skips the native model query.
            return (0, 0, 0)
        category = instance.evaluate(node.field("m_22326315"))
        if not isinstance(alias, Asset) or (category is not None and not isinstance(category, Asset)):
            return None
        if context == (0x0341, 0x0686):
            resource = self.body_definition
        else:
            blend = instance.find_var(INSTANCE, 8908)
            blend = blend.value() if blend is not None else None
            if not isinstance(blend, Asset):
                return None
            resource = blend.guid
            if not resource and self.body_definition == 0x040000000000040A:
                # The pilot's model component supplies its base tree when the equipped tree is empty.
                resource = 0x004000000000048A
        return resource, alias.guid, category.guid if category is not None else 0

    def animation_duration(self, instance: runtime.Instance, node: graph.Node) -> float | None:
        key = self._wheel_animation_key(instance, node)
        if key is None or not key[1]:
            return None if key is None else 0.0
        filename = GREETING_DURATION_DATA if instance.graph.index == 0x0341 else EMOTE_DURATION_DATA
        durations = wheel_animation_durations(DATA_DIR / filename)
        return durations.get(key, durations.get((*key[:2], EMOTE_ANY_CATEGORY)))

    def animation_scalar(self, instance: runtime.Instance, node: graph.Node) -> float | None:
        if instance.graph.index != 0x023A:
            return None
        key = self._wheel_animation_key(instance, node)
        if key is None or not key[1]:
            return None if key is None else 0.0
        scalars = wheel_animation_scalars(DATA_DIR / EMOTE_SCALAR_DATA)
        return scalars.get(key, scalars.get((*key[:2], EMOTE_ANY_CATEGORY)))

    def wheel_replaces_horizontal_velocity(self, component: runtime.Component) -> bool:
        if not self.communication_wheel:
            return False
        for instance in component.instances.values():
            if (instance.stopped or instance.graph.index != 0x023A or instance.parent is None
                    or instance.parent.graph.index != 0x069A):
                continue
            # Native 023A:78 replaces relative X/Z velocity with
            # x(681) * right + z(681) * forward. Both axes of a float are zero.
            if any(state.active and state.node.pos == 78 for state in instance.states.values()):
                value = instance.vars.get(681)
                if value is not None and type(value.value()) is float:
                    return True
        return False

    def wheel_model_pose(self, component, transform):
        active = [
            state for instance in component.instances.values()
            if instance.graph.index == 0x023A and not instance.stopped
            for state in instance.states.values()
            if state.active and state.node.pos in (0, 18, 19, 52)
        ]
        if len(active) != 1:
            return None
        state = active[0]
        if state.node.field("m_speedScalar") is not None:
            return None
        key = self._wheel_animation_key(state.instance, state.node)
        if key is None or not key[1]:
            return None
        clips = animation_pose.wheel_primary_clips(DATA_DIR / EMOTE_CLIP_DATA)
        clip = clips.get(key, clips.get((*key[:2], EMOTE_ANY_CATEGORY)))
        if clip is None:
            return None
        seconds = max(0, state.now - state.start) / 1000
        return animation_pose.wheel_clip_pose(
            DATA_DIR / WHEEL_POSE_DATA, self.body_definition, clip, seconds, state.node.pos == 18, transform,
        )

    def finish_wheel_emote(self, instance: runtime.Instance, node: graph.Node):
        if not self.communication_wheel:
            return NotImplemented
        component = instance.component
        index = instance.graph.index
        if index == 0x069A:
            if node.cls == "STU_3D419B63" and node.pos in (7, 8):
                # 1040 is the wheel helper in authored graph category 5.
                if (expr.guid_of(node.field("m_category")) != 0x01E0000000000005
                        or instance.evaluate(node.field("m_targets")) != Entity(self.body)):
                    return NotImplemented
                for helper in list(component.instances.values()):
                    if helper.graph.index == 0x1040:
                        component.destroy(helper)
                return instance.follow(node, "m_outPlug", None)
            if node.cls == "STU_22926252" and node.pos in (1, 5):
                component.destroy(instance)
                return True
        if node.cls != "STU_22926252":
            return NotImplemented
        if index == 0x0686 and node.pos == 0:
            # Retire the root after its authored wait so the next PlayScript can allocate it again.
            component.destroy(instance)
            return True
        if index == 0x023A and node.pos in (9, 14, 57):
            parent_graph = 0x069A
        elif index == 0x0341 and node.pos in (8, 9):
            parent_graph = 0x0686
        else:
            return NotImplemented
        parent = instance.parent
        if parent is not None and parent.graph.index == parent_graph:
            state = parent.state(instance.parent_state)
            if isinstance(state, nodes.SubScript) and state.active and state.child is instance:
                # End the SubScript state so its authored onEnd path runs.
                state.request_finish()
                return True
        return NotImplemented

    def replicate_wheel_event(self, instance: runtime.Instance, node: graph.Node) -> None:
        if not self.communication_wheel or node.remote < 0 or not node.is_networked:
            return
        index = instance.graph.index
        legacy_symmetra = (
            self.body_definition == LEGACY_SYMMETRA_BODY and index == 0x0169 and node.pos in (82, 86)
        )
        if index not in (0x02CF, 0x05AD, 0x0686) and not legacy_symmetra:
            return
        component = instance.component
        selected = instance.vars.get(29699)
        values = []
        for row in node.field("m_BF5B22B7") or ():
            var = expr.guid_of(row.get("m_0D09D2D9")) & 0xFFFF
            if not var:
                continue
            entity_scope = row.get("m_56341592") == expr.ENTITY
            slot = (component.vars if entity_scope else instance.vars).get(var)
            values.append(((0 if entity_scope else instance.id, var), (slot.value() if slot else None, ())))
        if (index, node.pos) == nodes.WHEEL_VOICE_PULSE and selected is not None:
            values.append(((instance.id, 29699), (selected.value(), ())))
        serial = self.wheel_callouts[-1].serial + 1 if self.wheel_callouts else 1
        if index == 0x02CF and node.pos in (3, 8):
            slot = instance.vars.get(9259)
            values.append(((instance.id, 9259), (slot.value() if slot is not None else None, ())))
            placement = tuple(sorted(instance.state(1).payload().items())) if node.pos == 8 else ()
            event = WheelSprayEvent(serial, instance.id, instance.start, node.pos, component.now,
                                   None, tuple(values), placement)
        else:
            event = WheelCallout(serial, instance.id, instance.start, node.pos, component.now,
                                 selected.value() if selected else None, tuple(values))
        self.wheel_callouts.append(event)
        del self.wheel_callouts[:-MAX_WHEEL_CALLOUTS]

    def velocity_action(self, instance, node):
        from ow174.game import mccree

        return mccree.velocity_action(self, instance, node)

    def tracked_targets(self, state):
        if (self.communication_wheel and self.match is not None
                and state.instance.graph.index == 0x05AD and state.node.pos in (113, 115)):
            collision_filter = state.node.field("m_3D667CE3") or {}
            profile = collision_filter.get("m_C1903771") or {}
            profile_id = expr.guid_of(profile.get("m_C1903771"))
            category = WHEEL_TARGET_CATEGORIES.get(profile_id)
            if category is None:
                return NotImplemented
            offset = state.evaluate(state.node.field("m_05BE62DD"), watch=False)
            radius = expr.to_float(state.evaluate(state.node.field("m_radius"), watch=False))
            aperture = expr.to_float(state.evaluate(state.node.field("m_A30B383E"), watch=False))
            target = self.match.combat.communication_target(
                self.body, offset, radius, aperture, category, profile=profile_id,
            )
            return (target,) if target is not None else ()

        category = TRACK_TARGET_RAY_CATEGORIES.get(state.hostile_body_profile())
        if self.match is not None and category is not None:
            centre = state.evaluate(state.node.field("m_05BE62DD"), watch=False)
            origin_config = state.node.field("m_D59188D5")
            origin = state.evaluate(origin_config, watch=False)
            radius = state.evaluate(state.node.field("m_radius"), watch=False)
            if state.impact_sphere():
                source = self.match._message_player(self.body)
                if not (source is not None and source.mover is not None
                        and nodes._finite_vector(centre)
                        and (origin_config is None or nodes._finite_vector(origin))
                        and nodes._finite_number(radius) and radius > 0):
                    return runtime.TrackedBodySamples(())
                from ow174.game.combat import aim

                mover = source.mover
                right = aim(mover.yaw + 16384, 0)
                forward = aim(mover.yaw, 0)
                # C9AFB0 transforms the local volume centre by the owner's body basis.
                x, y, z = mover.position
                centre = (
                    f32(x + centre.x * right[0] + centre.z * forward[0]),
                    f32(y + centre.y),
                    f32(z + centre.x * right[2] + centre.z * forward[2]),
                )
                # CAF254 defaults an omitted sight origin to the transformed centre.
                origin = centre if origin_config is None else tuple(origin)
                rows = self.match.combat.track_hostile_bodies(self.body, centre, radius, origin, category)
                limit = state.node.field("m_2A0FB8BB")
                if limit is not None:
                    count = state.evaluate(limit, watch=False)
                    if type(count) is int:
                        rows = rows[:max(0, count)]
                return runtime.TrackedBodySamples(rows)
            if not (nodes._finite_vector(centre) and nodes._finite_vector(origin)
                    and nodes._finite_number(radius) and radius > 0):
                return ()
            # This tracker retains prior hits for the cast.
            rows = self.match.combat.track_hostile_bodies(
                self.body, tuple(centre), radius, tuple(origin), category, state.targets,
            )
            return state.targets + tuple(entity for entity, _ in rows)

        from ow174.game import mccree

        return mccree.targets(self, state)

    def modify_health(self, state, elapsed_ms):
        """Apply named shield decay or report damage from a supported target tracker."""
        config = state.node.field("m_modifyHealth") or {}
        if state.instance.graph.index == DOOMFIST_PASSIVE_GRAPH and state.node.field("m_DD792FF0") == 2:
            return self._decay_temporary_shield(state, config, elapsed_ms)
        if self.match is None or config.get("m_93CDFD2F") != 1:
            return NotImplemented
        instance = state.instance
        node_key = (instance.graph.index, state.node.pos)
        mode = state.node.field("m_DD792FF0")
        target_config = state.node.field("m_FA3ECC43")
        discrete = (
            (node_key == (0x160A, 16) and mode == 3)
            or (node_key == (0x0B27, 154) and mode == 1 and expr.lvalue(target_config) == (0, 1182))
        )
        if discrete and state.node.field("m_8B707686") == 1:
            if state.evaluate(config.get("m_instigator"), watch=False) != Entity(self.body):
                return NotImplemented
            if not elapsed_ms:
                selection = state.evaluate(target_config, watch=False)
                if mode == 3:
                    selection = (selection,)
                if isinstance(selection, tuple):
                    victims = tuple(
                        victim.id for victim in selection
                        if isinstance(victim, Entity) and expr.valid_entity(victim.id)
                    )
                    self._queue_tracked_damage(state, config, victims, activation=None)
                # The graph has committed the discrete hit and can advance its victim loop.
                state.request_finish()
            return True
        if mode != 3:
            return NotImplemented
        target = expr.lvalue(target_config)
        paired = expr.lvalue(state.node.field("m_B12BFE8B"))
        if target is None or paired is None:
            return NotImplemented
        tracker = next((
            tracker for tracker in instance.ticking
            if isinstance(tracker, nodes.TrackTargets) and tracker.active and tracker.managed
            and tracker.hostile_body_profile() is not None
            and expr.lvalue(tracker.node.field("m_8A2905A0")) == target
            and expr.lvalue(tracker.node.field("m_24A4AB8A")) == paired
        ), None)
        if tracker is None or state.evaluate(config.get("m_instigator"), watch=False) != Entity(self.body):
            return NotImplemented
        targets = tuple(entity for entity in tracker.targets if entity not in state.hit_entities)
        self._queue_tracked_damage(state, config, targets, activation=state.counter)
        return True

    @staticmethod
    def _queue_tracked_damage(state, config, targets, *, activation):
        if not targets:
            return
        from ow174.game.combat import hit_def

        hit = hit_def(state, {"m_modifyHealth": config})
        if (hit is not None and math.isfinite(hit.amount) and hit.amount > 0
                and hit.loadout is not None and hit.loadout >> 52 == 0x0B9):
            state.instance.component.damage_reports.append(
                (state, activation, targets, hit.amount, hit.loadout, hit.tags),
            )

    def _decay_temporary_shield(self, state, config, elapsed_ms):
        pool_id = self._shield_pool_id(state.node.fields)
        if (self.match is None or pool_id is None
                or config.get("m_93CDFD2F") != 1
                or state.evaluate(state.node.field("m_FA3ECC43"), watch=False) != Entity(self.body)
                or state.evaluate(state.node.field("m_95F1FB0B"), watch=False) != 1):
            return NotImplemented
        rate = state.evaluate(config.get("m_amount"), watch=False)
        if not nodes._finite_number(rate) or rate <= 0:
            return NotImplemented
        if elapsed_ms > 0:
            self.match.combat.drain_temporary_shield(self.body, pool_id, rate * elapsed_ms / 1000.0)
        if self.temporary_shield_current(self.body, pool_id) <= 0:
            state.request_finish()
        return True

    def target_effects(self, state):
        from ow174.game import mccree

        return mccree.effects(self, state)

    def send_message(self, target: int, message: int, params: dict, sender: int) -> None:
        match = self.match
        if (match is not None and sender == self.body
                and match.queue_body_message(target, message, params, sender)):
            return
        super().send_message(target, message, params, sender)


@dataclass
class Snapshot:
    """The body as the client has it (or will, after a chunk): instances, variables, states, timers."""

    k: int = 0
    instances: dict = field(default_factory=dict)  # id -> (graph index, parent)
    vars: dict = field(default_factory=dict)  # (instance or 0, var) -> (value, bindings)
    states: dict = field(default_factory=dict)  # (instance, state) -> (cls, on, payload)
    events: dict = field(default_factory=dict)  # instance -> ((time, state, finish, param), ...)
    callout: int = 0  # newest event in a full projection, or last event carried by a delta
    callouts: tuple[WheelCallout, ...] = ()

    def next_callout(self, baseline: int) -> WheelCallout | None:
        return next((event for event in self.callouts if event.serial > baseline), None)

    def is_voice_pulse(self, event: WheelCallout) -> bool:
        index, _ = self.instances[event.instance]
        return (index, event.node) == nodes.WHEEL_VOICE_PULSE

    def for_delta(self, base: "Snapshot", keys: set) -> "Snapshot":
        """Project this stream's next event with its variables and state payloads."""
        serial = next((key[1] for key in keys if key[0] == "callout"), base.callout)
        if not (self.callout or base.callout or self.callouts):
            return self
        if not self.callouts and self.callout == serial and serial > base.callout:
            return self  # owner delta already projected the selected event
        event = next(
            (item for item in self.callouts if item.serial == serial and serial > base.callout), None,
        )
        values = self.vars.copy()
        if event is not None:
            values.update(event.values)
        voice_owner = event.instance if event is not None and self.is_voice_pulse(event) else None
        states = self.states.copy()
        pending_sprays = {item.instance for item in self.callouts
                          if isinstance(item, WheelSprayEvent) and item.serial > base.callout}
        for instance, (index, _) in self.instances.items():
            if index == 0x02CF:
                self._project_spray(instance, event, instance in pending_sprays, base, states, values)
                continue
            if index != nodes.WHEEL_VOICE_PULSE[0]:
                continue
            key = (instance, 13)  # 0686:72
            previous = base.states.get(key)
            if instance == voice_owner:
                states[key] = self._next_pulse(previous)
            elif previous is not None:
                active = key in self.states and self.states[key][1]
                states[key] = (previous[0], active, previous[2])
            else:
                states.pop(key, None)
        return replace(self, callout=serial, callouts=(), vars=values, states=states)

    @staticmethod
    def _next_pulse(previous):
        count = (dict(previous[2])["count"] + 1) & 0xFF if previous is not None else 1
        return ("STU_B9898052", True, (("count", count), ("fresh", True)))

    @staticmethod
    def _project_spray(instance, event, pending, base, states, values) -> None:
        placement_key, pulse_key, slot_key = (instance, 1), (instance, 14), (instance, 9259)
        previous = base.states.get(pulse_key)
        if isinstance(event, WheelSprayEvent) and event.instance == instance:
            if event.placement:
                states[placement_key] = (spray_placement.STATE_CLASS, True, event.placement)
                states[pulse_key] = Snapshot._next_pulse(previous)
                return
            states.pop(placement_key, None)
        elif pending:
            # A different queued action precedes this placement on the stream.
            if placement_key in base.states:
                states[placement_key] = base.states[placement_key]
            else:
                states.pop(placement_key, None)
            if slot_key in base.vars:
                values[slot_key] = base.vars[slot_key]
            else:
                values.pop(slot_key, None)
        if previous is not None:
            states[pulse_key] = previous
        else:
            states.pop(pulse_key, None)


def frame_callout(base: Snapshot, snap: Snapshot, touched: set, keys: set, next_chunk: int):
    """Choose the next action for this stream and retain its original stamp until ACK."""
    callout = snap.next_callout(base.callout)
    if callout is None:
        return None
    previous = (key[2] for key in touched if key[0] == "callout" and key[1] == callout.serial)
    stamp = min(previous, default=next_chunk)
    keys.add(("callout", callout.serial, stamp))
    if snap.is_voice_pulse(callout):
        return None
    if isinstance(callout, WheelSprayEvent) and callout.placement:
        keys.add(("state", callout.instance, 1))  # repeat even when the geometry and slot match
    return stamp, callout.instance, callout.node


@dataclass
class OwnerReport:
    """A transmitted body delta and the outcome of its own datagram."""

    number: int
    cmfd: int
    last: int
    volleys: dict[tuple[int, int], tuple[int, int]]  # (instance, state) -> (counter, start)
    introductions: frozenset[tuple[int, int]]
    received: bool | None = None
    sprays: frozenset[tuple[int, int, int]] = frozenset()  # instance, start, unlock


def _same_var(a, b) -> bool:
    a = a or (None, ())
    b = b or (None, ())
    return expr.same(a[0], b[0]) and a[1] == b[1]


class ReplicatedBody:
    component: runtime.Component
    server_only: set[int]

    def callout_snapshot(self) -> Snapshot:
        return Snapshot(self.frame)

    def set_health(self, health: float, most: float) -> None:
        """Update the shared health input. Player bodies also use the maximum for their HUD."""
        self.component.set(OWNER_HEALTH, float(health))

    def initial_exclusions(self) -> set[int]:
        """Collect the existing entity-scope exclusions from the initialized graph tree."""
        return {
            var_id
            for instance in self.component.instances.values()
            for var_id in _server_only_vars(instance.graph)
        }

    def replicated_instances(self) -> Iterable[runtime.Instance]:
        return self.component.instances.values()

    def graphs(self) -> dict[int, object]:
        return {instance.id: instance.graph for instance in self.replicated_instances()}

    def _report_shots(self) -> None:
        """Collect shots due in active volleys. Volleys that ended reported their own shots."""
        nodes.report_active_volleys(self.component)
        shots = self.component.shots
        if len(shots) > MAX_SHOTS:
            del shots[: len(shots) - MAX_SHOTS]

    def replicated_vars(self, instance: runtime.Instance | None = None) -> Iterator[tuple[int, runtime.Var]]:
        """Variables eligible for both wire snapshots. Instance variable IDs are local to their graph."""
        if instance is None:
            variables = self.component.vars
            excluded = self.server_only | NOT_SENT
        else:
            variables = instance.vars
            excluded = _server_only_vars(instance.graph)
        return ((var_id, var) for var_id, var in variables.items() if var_id not in excluded)


class BodyScript(ReplicatedBody):
    """The statescript of one hero body: `spawn_frames()` once the body exists on the client, then
    `command()` for every new client command frame and `frames()` every server tick."""

    def __init__(
        self,
        hero: int,
        entity: int,
        start_frame: int,
        quanta: int = 16000,
        mode: int = PRACTICE_RANGE_MODE,
        health: float | None = None,
        ult_seconds: float = ULT_SECONDS,
        *,
        match: "Match | None" = None,
        communication_wheel: bool = False,
        body_definition: int | None = None,
        emote_aliases: tuple[int, ...] = (),
        emote_blend_trees: tuple[int, ...] = (),
        spray_unlocks: tuple[int, ...] = (),
        voice_effects: tuple[int, ...] = (),
    ) -> None:
        if type(start_frame) is not int or not 0 <= start_frame <= FRAME_MASK:
            raise ValueError("start_frame must be uint32")
        records = graph.bodies()
        if body_definition is None:
            body_definition = next(
                (key for key, item in records.items() if int(item["hero"], 16) == hero), None,
            )
        record = records.get(body_definition)
        if record is None and (hero, body_definition) in (
            (0x02E000000000007A, 0x0400000000000407),
            (0x02E0000000000016, LEGACY_SYMMETRA_BODY),
        ):
            record = graph.entity_body(body_definition)
        if record is None or (record.get("hero") is not None and int(record["hero"], 16) != hero):
            raise ValueError(f"no statescript data for hero {hero:016X} and body {body_definition!r}")
        self.hero = hero
        self.entity = entity
        self.record = record
        self.frame_ms = max(1, quanta // 1000)
        self.world = BodyWorld(hero, mode, entity, body_definition)
        self.world.match = match
        self.world.communication_wheel = communication_wheel or match is not None
        for name, slots in (("emote_aliases", emote_aliases), ("emote_blend_trees", emote_blend_trees),
                            ("spray_unlocks", spray_unlocks), ("voice_effects", voice_effects)):
            if not valid_wheel_slots(slots, WHEEL_LOADOUT_TYPES[name]):
                raise ValueError(f"{name} must contain up to four matching asset GUIDs or empty slots")
            setattr(self.world, name, slots)
        self.component = runtime.Component(entity, self.frame_ms, self.world)
        self.frame = start_frame  # the last command frame run
        self.buttons = 0
        self.stream = BodyStream(entity)
        self.create_arrived = False  # the client has the body (see `created`)
        self.retired = False
        self.first_owner_frame: int | None = None
        self.ult_seconds = ult_seconds
        self.ultimates: list = []  # the Ability states that start on #342e (not charged while they run)
        self.reports: list[OwnerReport] = []
        self.report_count = 0  # owner frames sent so far
        self._spawn(start_frame, sum(pools.body_pools(hero)) if health is None else health)

    def __repr__(self) -> str:
        return f"<BodyScript {self.entity:08X} {self.record.get('name')} frame {self.frame}>"

    # --- the instances -----------------------------------------------------------------------------

    def _spawn(self, frame: int, health: float) -> None:
        """The body's instances: its initial graphs 1..N in definition order with
        their schema overrides, the weapon manager, the weapon scripts (#28 = the slot), all run from their
        Entries in the start frame."""
        component = self.component
        component.frame = frame
        component.now = frame * self.frame_ms
        self.set_health(health, health)
        component.set(ULT_CHARGE, 0.0)
        for instance in create_body_graphs(component, self.record):
            instance.init()
        component.run_frame(frame)
        self.server_only = self.initial_exclusions()
        for instance in component.instances.values():
            for node in instance.graph.states:
                if node is None or node.cls != "STUStatescriptStateAbility":
                    continue
                if _reads(node.field("m_150F0D92"), ULT_CHARGE):  # the ultimate: starts on its charge
                    self.ultimates.append(instance.state(node.state))

    def set_health(self, health: float, most: float) -> None:
        """The owner's health: #253e for the graphs (01CF st13: 0 or less is dead), #1732e/#1737e for the
        HUD panel's number (no graph writes them: current and maximum). The
        body's pools added up, health, armour and shields (unverified: as OW1's HUD showed one number)."""
        super().set_health(health, most)
        self.component.set(HUD_HEALTH, float(health))
        self.component.set(HUD_MAX_HEALTH, float(most))
        self.component.owner_dead = health <= 0  # as the client sees its owner (comp 51, 0x7FF789ADBF00)

    # --- running -----------------------------------------------------------------------------------

    def command(
        self, frame: int, buttons: int, action: int = 0, forward: int = 0, right: int = 0,
        *, grounded: bool | None = None,
    ) -> None:
        """Run the command frames up to `frame`: frames the client skipped keep the last buttons (a long gap
        runs only its last MAX_GAP frames; what was due before runs in the first of them).

        Old/duplicate uint32 frames are ignored. A genuine forward rollover is distinct:
        the current component's absolute timers and owner CmFD belong to the old epoch.
        Raise before changing anything, so the caller can establish a fresh lifecycle;
        resetting self.frame would silently keep incompatible timers and client history.
        """
        self.component.ensure_valid()
        if type(frame) is not int or not 0 <= frame <= FRAME_MASK:
            raise ValueError("command frame must be uint32")
        distance = frame_distance(frame, self.frame)
        if distance == 0 or distance >= FRAME_HALF:
            return
        if frame < self.frame:
            raise CommandEpochError(self.frame, frame)
        for missing in range(max(self.frame + 1, frame - MAX_GAP), frame):
            self._run(missing, [])
        self.world.throttles = (forward, right)
        if self.world.communication_wheel:
            # 01C7:7 reads the mover's flags and the command axes separately.
            if grounded is not None:
                self.component.set(545, grounded)
            self.component.set(2070, bool(forward or right))
        self._run(frame, button_edges(self.buttons, buttons, action))
        self.buttons = buttons
        self.frame = frame

    def _run(self, frame: int, edges: list) -> None:
        self.component.run_frame(frame, edges)
        self._report_shots()
        self._charge_ult()

    def set_ult_charge(self, points: float) -> None:
        """Set the ultimate's charge (#342e; the cost is #338e)."""
        self.component.set(ULT_CHARGE, f32(points))

    def credit_ult(self, kind: str, amount: float) -> None:
        """A combat event of this body (combat.py): dealt damage or done healing in HP, or an elimination
        (amount unused). The hero's rule in ult_charge.py turns it into points of the meter's own scale;
        a rule that does not charge gives nothing, and the meter never passes its cost."""
        cost = expr.to_float(self.component.entity_var(ULT_COST).value())
        points = ult_charge.points(kind, self.hero, amount, cost)
        if points <= 0.0:
            return
        charge = expr.to_float(self.component.entity_var(ULT_CHARGE).value())
        self.set_ult_charge(max(0.0, min(cost, f32(charge + points))))

    def add_ult_charge(self, points: float) -> None:
        if any(state.flags & nodes.Ability.RUNNING for state in self.ultimates):
            return
        cost = expr.to_float(self.component.entity_var(ULT_COST).value())
        charge = expr.to_float(self.component.entity_var(ULT_CHARGE).value())
        if cost > 0 and charge < cost:
            self.set_ult_charge(min(cost, charge + points))

    def _charge_ult(self) -> None:
        blocked = self.ult_seconds <= 0 or any(
            state.flags & nodes.Ability.RUNNING for state in self.ultimates
        )
        if blocked != getattr(self, "_ult_passive_blocked", None):
            log.info(
                "[game] %X: ultimate passive %s", self.entity, "paused" if blocked else "running"
            )
            self._ult_passive_blocked = blocked
        if blocked:
            return
        cost = expr.to_float(self.component.entity_var(ULT_COST).value())
        charge = expr.to_float(self.component.entity_var(ULT_CHARGE).value())
        if cost > 0 and charge < cost:
            step = f32(cost * self.frame_ms / (1000.0 * self.ult_seconds))
            self.set_ult_charge(min(cost, f32(charge + step)))
        self._note_ult_stall(charge)

    _ULT_STALL_SECONDS = 10.0

    def _note_ult_stall(self, charge: float) -> None:
        """A stalled meter with a running rule means the meter's frames do not reach the client (or the
        client's commands stop the script); say so once per interval instead of leaving it silent."""
        now = self.component.now / 1000.0
        seen = getattr(self, "_ult_stall", None)
        if seen is None:
            self._ult_stall = seen = [charge, now]
        elif charge > seen[0]:
            seen[0], seen[1] = charge, now
        elif seen[1] + self._ULT_STALL_SECONDS < now:
            base = self.stream.base
            acked = next((v for (owner, var), (v, _) in base.vars.items() if var == ULT_CHARGE), None)
            log.info(
                "[game] %X: ultimate meter stuck at %.1f for %.0f s (client has %s; frames %d acked %d,"
                " pending %d, cmfd %d, commands %d)",
                self.entity, charge, now - seen[1], acked, self.stream.last, self.stream.base_last,
                len(self.stream.pending), self.stream.cmfd, self.frame,
            )
            seen[1] = now  # report again after the next interval if it stays stuck

    # --- what the client gets ----------------------------------------------------------------------

    def snapshot(self) -> Snapshot:
        """The body as the client should have it after the last frame run."""
        self.component.ensure_valid()
        snap = self.callout_snapshot()
        for instance in self.component.instances.values():
            parent = (instance.parent.id, instance.parent_state) if instance.parent is not None else None
            snap.instances[instance.id] = (instance.graph.index, parent)
            for var_id, var in self.replicated_vars(instance):
                self._add_var(snap, instance.id, var_id, var)
            for index, state in instance.states.items():
                self._add_state(snap, instance.id, index, state)
            snap.events[instance.id] = tuple(
                (event.time, event.state.index, event.kind == runtime.STATE_OP, event.param)
                for event in instance.timers()
                if sendable(event.state.node.cls, True)
            )
        for var_id, var in self.replicated_vars():
            self._add_var(snap, 0, var_id, var)
        return snap

    def callout_snapshot(self) -> Snapshot:
        history = self.world.wheel_callouts
        if self.retired or not history:
            return Snapshot(self.frame)
        pending = []
        for event in history:
            instance = self.component.instances.get(event.instance)
            if instance is None or instance.stopped or instance.start != event.start:
                continue
            retained = (isinstance(event, WheelSprayEvent)
                        or (instance.graph.index, event.node) == nodes.WHEEL_VOICE_PULSE)
            if not retained and self.component.now - event.time > WHEEL_CALLOUT_MS:
                continue
            if instance.graph.index == 0x0686:
                # Re-entry reuses the instance. Pending actions carry their original sync values.
                root = instance.states.get(0)
                if root is None or not root.active:
                    continue
            pending.append(event)
        return Snapshot(self.frame, callout=history[-1].serial, callouts=tuple(pending))

    def _add_var(self, snap: Snapshot, owner: int, var_id: int, var: runtime.Var) -> None:
        bindings = tuple(
            Binding(link.state.instance.id, link.state.index, link.slot, link.priority, link.above,
                    link.dynamic, link.frame_cached)
            for link in var.links
            if link.stack and link.state.active and link.state.networked
        )
        value = var.value()
        if value is not None or bindings:
            snap.vars[(owner, var_id)] = (value, bindings)

    @staticmethod
    def _add_state(snap: Snapshot, owner: int, index: int, state: runtime.State) -> None:
        cls = state.node.cls
        if not state.networked or not (state.active or (cls in stec_classes() and state.counter)):
            return
        if not sendable(cls, state.active):
            expr.warn_once(f"send{cls}", "state class %s is on but cannot be sent", cls)
            return
        if cls in POLLED_SWITCHES and state.natives - TRUSTED_NATIVES:
            # Uncertain: the client would re-check it every update with its own answer and flip it against
            # ours in every frame. Not sent, it stays off there (its live subgraph is sent).
            return
        codec = family(cls)
        payload = state.payload() if codec != "none" else {}
        if codec in PAYLOAD_REQUIRED and not payload:
            return
        snap.states[(owner, index)] = (cls, state.active, tuple(sorted(payload.items())))

    def tree_frame(self) -> tuple[BitWriter, Snapshot]:
        """The plain full frame (step 2) and what the client has after it: the instances and the values,
        every networked state off, no timers."""
        snap = self.snapshot()
        graphs = self.graphs()
        wires = {}
        for item, (_, parent) in snap.instances.items():
            wires[item] = InstanceWire(item, graphs[item], parent=parent)
        entity_vars = {}
        base = Snapshot(snap.k, dict(snap.instances))
        for (owner, var), (value, _) in snap.vars.items():
            if owner == 0:
                entity_vars[var] = (value, ())
            else:
                wires[owner].vars[var] = (value, ())
            base.vars[(owner, var)] = (value, ())
        return body_full_frame(list(wires.values()), entity_vars, graphs), base

    def delta(
        self, base: Snapshot, touched: set, cmfd: int, correction: bool, *, kill_feed: bool | None = None,
    ):
        """An owner delta at CmFD from `base`; `touched` are the keys sent since `base`. Returns the frame,
        the keys it carries and the snapshot it leaves the client with."""
        snap = self.snapshot()
        if kill_feed is not None:
            snap = self.stream.kill_feed_snapshot(snap, allowed=kill_feed)
        graphs = self.graphs()
        wires: dict[int, InstanceWire] = {}

        def wire(item: int) -> InstanceWire:
            if item not in wires:
                graph_index, parent = snap.instances[item]
                wires[item] = InstanceWire(item, graphs.get(item) or graph.graph(graph_index), parent=parent)
            return wires[item]

        keys = set()
        callout = frame_callout(base, snap, touched, keys, self.stream.last + 1)
        snap = snap.for_delta(base, keys)
        for item in snap.instances:
            if item not in base.instances:  # new (a repeated descriptor is taken as the same instance)
                wire(item).descriptor = True
                keys.add(("inst", item))
        for item in set(base.instances) | {key[1] for key in touched if key[0] == "inst"}:
            if item not in snap.instances:  # gone, or made and gone again since the base
                wires[item] = InstanceWire(item, None, gone=True)
                keys.add(("inst", item))
        entity_vars = {}
        touched_vars = {key[1:] for key in touched if key[0] == "var"}
        for key in set(snap.vars) | set(base.vars) | touched_vars:
            owner, var = key
            if owner and owner not in snap.instances:
                continue
            now = snap.vars.get(key)
            if _same_var(now, base.vars.get(key)) and key not in touched_vars:
                continue
            keys.add(("var", *key))
            if owner == 0:
                entity_vars[var] = now or (None, ())
            else:
                wire(owner).vars[var] = now or (None, ())
        touched_states = {key[1:] for key in touched | keys if key[0] == "state"}
        for key in set(snap.states) | set(base.states) | touched_states:
            owner, index = key
            if owner not in snap.instances:
                continue
            now, was = snap.states.get(key), base.states.get(key)
            if now == was and key not in touched_states:
                continue
            if now is None:
                cls = graphs[owner].states[index].cls
                if cls in stec_classes():
                    continue  # it never reached the client as on
                state = StateWire(cls, False)
            else:
                state = StateWire(now[0], now[1], dict(now[2]))
            keys.add(("state", *key))
            wire(owner).states[index] = state
            if state.cls != VOLLEY:
                continue
            previous = dict(was[2]) if was is not None else {}
            counter = state.payload["counter"]
            if (counter, state.payload["start"]) != (previous.get("counter"), previous.get("start")):
                # A single-shot volley can finish in the frame that introduces it.
                keys.add((_VOLLEY_INTRODUCTION, *key))
            # The last acknowledged frame can still show a previous activation as on.
            if state.active and (was is None or not was[1] or previous["counter"] != counter):
                self._volley_start(snap, owner, index, wire, entity_vars, keys)
        for item in snap.instances:
            wire(item).events = [EventWire(*event) for event in snap.events.get(item, ())]
        frame = owner_delta(cmfd, correction, list(wires.values()), entity_vars, graphs, self.frame_ms,
                            callout=callout)
        return frame, keys, snap

    def _volley_start(self, snap: Snapshot, owner: int, index: int, wire, entity_vars: dict, keys: set):
        """A volley the client begins from this frame takes its ammunition from m_out_Ammo once, after the
        frame (its deferred StartFiring) and never links it: so that frame carries the volley's starting
        ammunition, the later ones the count."""
        state = self.component.instances[owner].states[index]
        target = expr.lvalue(state.node.fields.get("m_24E351A4"))
        if target is None:
            return
        scope, var = target
        key = (0, var) if scope else (owner, var)
        value = (state.ammo0, ())
        snap.vars[key] = value
        keys.add(("var", *key))
        if scope:
            entity_vars[var] = value
        else:
            wire(owner).vars[var] = value

    # --- frames for the world ------------------------------------------------------------------------

    def created(self, update):
        """Hook the body's create record (a world.EntityUpdate): the transport tells us when the client
        acknowledged it, and the full frame goes out after that."""
        update.stream = _CreateArrived(self)
        return update

    def retire(self) -> None:
        """The body is gone (a hero switch, the player left): no more frames, and a lost full frame is not
        sent again."""
        self.retired = True
        if self.world.match is not None:
            self.world.match.combat.clear_temporary_shields(self.entity)
        self.world.velocity_requests.clear()
        for instance in list(self.component.instances.values()):
            for state in list(instance.states.values()):
                if isinstance(state, nodes.TargetEffects):
                    state.clear_children()
        self.world.match = None
        if self.stream.full_update is not None:
            self.stream.full_update.resend = False
        if self.stream.initial_owner is not None:
            self.stream.initial_owner.resend = False
            self.stream.initial_owner = None

    def spawn_frames(self) -> list:
        """The full frame that puts the body's statescript on the client (send it once the client has the
        body; it goes again when its datagram is lost)."""
        frame, base = self.tree_frame()
        return [self.stream.full(frame, base)]

    def _unsent_health_changed(self, queued: "Iterable[EntityUpdate]") -> bool:
        stream = self.stream
        pending = stream.pending.get(stream.last)
        if pending is None or stream.last in stream.sent_reports:
            return False
        snapshot, _ = pending
        changed = any(
            not expr.same(snapshot.vars.get((0, var), (None, ()))[0], self.component.vars[var].value())
            for var in (HUD_HEALTH, HUD_MAX_HEALTH)
        )
        return changed and any(
            item.stream is stream and item.chunk_last == stream.last and not item.resend for item in queued
        )

    def frames(
        self, *, kill_feed: bool | None = None, replace_initial: bool = False,
        queued: "Iterable[EntityUpdate]" = (),
    ) -> list:
        """This tick's body frames: the full frame once the body's create arrived (see `created`), the first
        owner frame (C = 0) once the full frame arrived, then, after SETTLE_FRAMES, a C = 1 frame whenever the
        client's command frame moved on. Health can also refresh a queued owner frame at the same
        command frame before its first send."""
        self.component.ensure_valid()
        stream = self.stream
        if self.retired:
            return []
        if not stream.last:
            return self.spawn_frames() if self.create_arrived else []
        if stream.base is None or self.frame < stream.cmfd:
            return []
        if stream.initial_owner is not None and stream.initial_owner.resend:
            return []  # transport is delivering the exact first owner frame
        if self.frame == stream.cmfd and not self._unsent_health_changed(queued):
            return []
        if self.first_owner_frame is None or replace_initial:
            self.first_owner_frame = self.frame
            correction = False
        elif self.frame < self.first_owner_frame + SETTLE_FRAMES:
            return []
        else:
            correction = True
        frame, keys, snap = self.delta(
            stream.base, stream.touched(), self.frame, correction, kill_feed=kill_feed,
        )
        update = stream.delta(frame, snap, keys, self.frame)
        if not correction:
            stream.initial_owner = update
        return [update]

    def matches_sent_spray(self, instance: runtime.Instance, unlock: int) -> bool:
        key = (instance.id, instance.start, unlock)
        return any(
            0 <= (self.frame - report.cmfd) * self.frame_ms <= WHEEL_CALLOUT_MS and key in report.sprays
            for report in self.reports
        )

    def matches_sent_voice_effect(self, instance: runtime.Instance, effect: int) -> bool:
        stream = self.stream
        for event in self.world.wheel_callouts:
            if (event.instance != instance.id or event.start != instance.start
                    or (instance.graph.index, event.node) != nodes.WHEEL_VOICE_PULSE
                    or not isinstance(event.choice, Asset)):
                continue
            action = event.choice.guid - WHEEL_ACTION_PREFIX
            if action not in WHEEL_VOICE_ACTIONS:
                continue
            slot = WHEEL_VOICE_ACTIONS.index(action)
            if (self.world.equipped_wheel_asset(self.world.voice_effects, slot).guid == effect
                    and stream.sent_voice_pulse(event.serial)):
                return True
        return False

    def report_sent(self, last: int) -> None:
        """Record the owner delta actually transmitted."""
        stream = self.stream
        pending = stream.pending.get(last)
        if pending is None or last in stream.sent_reports:
            return
        snapshot, keys = pending
        serial = next((key[1] for key in keys if key[0] == "callout"), None)
        if serial is not None and any(event.serial == serial and snapshot.is_voice_pulse(event)
                                      for event in self.world.wheel_callouts):
            keys.add(("voice_sent", serial))
        volleys = {}
        sprays = set()
        for key in keys:
            if key[0] != "state":
                continue
            state_key = key[1:]
            state = snapshot.states.get(state_key)
            if state is not None and state[0] == VOLLEY:
                values = dict(state[2])
                volleys[state_key] = (values["counter"], values["start"])
            elif state is not None and state[0] == spray_placement.STATE_CLASS and state[1]:
                instance = self.component.instances.get(state_key[0])
                unlock = dict(state[2]).get("unlock")
                if instance is not None and instance.graph.index == 0x02CF and unlock:
                    sprays.add((instance.id, instance.start, unlock))
        self.report_count += 1
        introductions = frozenset(key[1:] for key in keys if key[0] == _VOLLEY_INTRODUCTION)
        report = OwnerReport(self.report_count, snapshot.k, last, volleys, introductions,
                             sprays=frozenset(sprays))
        stream.sent_reports[last] = report
        self.reports.append(report)
        del self.reports[:-MAX_REPORTS]
        if stream.initial_owner is not None and stream.initial_owner.chunk_last == last:
            # Before the first actual send, the queued initial can still coalesce.
            # After send, retain its original chunk and report through transport retries.
            stream.initial_owner.resend = True


def create_body_graphs(
    component: runtime.Component, record: dict, *, include_initial: bool = True,
) -> list[runtime.Instance]:
    """Allocate the weapon manager and slots, plus any initial graphs not already mounted."""
    weapons = record.get("weapons") or []
    component.set(WEAPON_SLOTS, tuple(item is not None for item in weapons))
    component.set(DEFAULT_SLOT, next((slot for slot, item in enumerate(weapons) if item is not None), 0))
    created = create_initial_graphs(component, record) if include_initial else []
    manager = graph.graph(record["manager"]) if record.get("manager") is not None else None
    if component.strict and record.get("manager") is not None and manager is None:
        raise ValueError("body weapon manager graph is unavailable")
    if manager is not None:
        created.append(component.create(manager, init=False))
    for slot, item in enumerate(weapons):
        weapon = graph.graph(item) if item is not None else None
        if component.strict and item is not None and weapon is None:
            raise ValueError(f"body weapon graph is unavailable for slot {slot}")
        if weapon is not None:
            created.append(component.create(weapon, values={(INSTANCE, 28): slot}, init=False))
    return created


def create_initial_graphs(component: runtime.Component, record: dict) -> list[runtime.Instance]:
    """Allocate the definition's instances in asset order, before running their entries."""
    created = []
    for number, item in enumerate(record["graphs"], 1):
        definition = graph.graph(item["graph"])
        if definition is None:
            continue
        values = {}
        for entry in item.get("m_1EB5A024") or ():
            var = expr.guid_of(entry.get("m_0D09D2D9")) & 0xFFFF
            values[(INSTANCE, var)] = expr.evaluate(entry.get("m_value"), expr.Context())
        created.append(component.create(definition, number, values=values, init=False))
    return created


def _server_only_vars(definition: graph.Graph) -> set[int]:
    return {expr.guid_of(value) & 0xFFFF for value in definition.fields.get("m_9CEC6985") or ()}


def _reads(cfg, var: int) -> bool:
    """Whether a config var reads the entity variable `var`."""
    if isinstance(cfg, dict):
        if cfg.get("$") == "STUConfigVarDynamic":
            return cfg.get("m_identifier") == var and cfg.get("m_60DB8F99") == 1
        return any(_reads(value, var) for value in cfg.values())
    if isinstance(cfg, list):
        return any(_reads(value, var) for value in cfg)
    return False


def warm_up() -> None:
    """Load shared graph and codec data before the first match update."""
    BodyScript(SOLDIER, 0, 0)
    stec_classes()


class _CreateArrived:
    """Stands in for a stream on the body's create record: the transport's ack calls `arrived`."""

    def __init__(self, script: BodyScript) -> None:
        self.script = script

    def arrived(self, last: int) -> None:
        self.script.create_arrived = True


class BodyStream:
    """The body entity's chunks: their numbers, what the client acknowledged and the snapshot each chunk
    leaves it with. A delta goes from the last acknowledged chunk; the client takes it when
    `first <= applied + 1` and `last > applied`."""

    def __init__(self, entity: int) -> None:
        self.entity = entity
        self.last = 0  # the last frame number sent
        self.cmfd = 0
        self.base: Snapshot | None = None  # what the client has after the chunk ending at base_last
        self.base_last = 0
        self.pending: dict[int, tuple[Snapshot, set]] = {}  # last -> (snapshot, keys), not acknowledged
        self.sent_reports: dict[int, OwnerReport] = {}  # sent owner deltas with unknown transport outcomes
        self.lost_keys: dict[tuple, int] = {}  # each key's newest lost owner chunk
        self.latest_lost: Snapshot | None = None  # newest owner projection, for kill-feed pulse freshness
        self.full_update = None  # the full frame's record (sent again when lost)
        self.initial_owner = None  # first C = 0 update, retried once actually sent

    def _update(self, frame: BitWriter, first: int, resend: bool):
        from ow174.game.world import EntityUpdate

        update = EntityUpdate(self.entity, chunk=chunk(frame, first, self.last - first))
        update.chunk_last = self.last
        update.stream = self
        update.resend = resend
        return update

    def full(self, frame: BitWriter, snapshot: Snapshot):
        self.last += 1
        self.pending[self.last] = (replace(snapshot, callouts=()) if snapshot.callouts else snapshot, set())
        self.latest_lost = None
        self.full_update = self._update(frame, 0, resend=True)
        return self.full_update

    def delta(self, frame: BitWriter, snapshot: Snapshot, keys: set, cmfd: int):
        self.last += 1
        self.cmfd = cmfd
        if self.base is not None:
            snapshot = snapshot.for_delta(self.base, keys)
        self.pending[self.last] = (snapshot, keys)
        self.latest_lost = None
        return self._update(frame, self.base_last + 1, resend=False)

    def lost(self, last: int) -> None:
        """An owner delta's packet is retired without a positive ACK. Keep its keys cumulative."""
        self._report_delivery(last, False)
        pending = self.pending.pop(last, None)
        if pending is None:
            return
        snapshot, keys = pending
        for key in keys:
            if key[0] != _VOLLEY_INTRODUCTION:
                self.lost_keys[key] = max(last, self.lost_keys.get(key, 0))
        if last == self.last:
            self.latest_lost = snapshot

    def sent_voice_pulse(self, serial: int) -> bool:
        # Acknowledgments advance in queue order. A retained voice pulse stays
        # eligible until its instance ends, including across lost datagrams.
        if self.base is not None and self.base.callout >= serial:
            return True
        key = ("voice_sent", serial)
        return key in self.lost_keys or any(key in keys for _, keys in self.pending.values())

    def touched(self) -> set:
        found = set(self.lost_keys)
        base = self.base
        for snapshot, keys in self.pending.values():
            for key in keys - found:
                if key[0] == _VOLLEY_INTRODUCTION:
                    continue
                if key[0] == "state" and base is not None:
                    state = key[1:]
                    if snapshot.states.get(state) == base.states.get(state):
                        # This later packet only repeated a state the client has
                        # now acknowledged. Keep the original key for future
                        # baselines and possible loss, but don't propagate it.
                        continue
                found.add(key)
        return found

    def kill_feed_snapshot(self, snap: Snapshot, *, allowed: bool) -> Snapshot:
        """Gate the first new death pulse and keep its wire value until acknowledged."""
        latest = self.pending.get(self.last)
        pending = latest[0] if latest is not None else self.latest_lost
        states = None
        for owner, (graph_index, _) in snap.instances.items():
            index = KILL_FEED_PULSERS.get(graph_index)
            if index is None:
                continue
            key = (owner, index)
            before = self.base.states.get(key) if self.base is not None else None
            sent = pending.states.get(key) if pending is not None else None
            current = snap.states.get(key)
            count = dict(before[2])["count"] if before is not None and before[1] else None
            current_count = dict(current[2])["count"] if current is not None and current[1] else None
            if sent is not None and sent[1] and dict(sent[2])["count"] != count:
                value = sent
            elif current_count is not None and current_count != count:
                value = before
                if allowed:
                    value = (current[0], True, (("count", current_count), ("fresh", True)))
            else:
                continue
            if value == current:
                continue
            if states is None:
                states = dict(snap.states)
            if value is None:
                del states[key]
            else:
                states[key] = value
        return snap if states is None else replace(snap, states=states)

    def arrived(self, last: int) -> None:
        """The client acknowledged the datagram with the chunk that ends at `last`."""
        self._report_delivery(last, True)
        if last <= self.base_last or last not in self.pending:
            return
        self.base = self.pending[last][0]
        self.base_last = last
        self.pending = {key: value for key, value in self.pending.items() if key > last}
        self.lost_keys = {key: number for key, number in self.lost_keys.items() if number > last}
        if self.initial_owner is not None and self.initial_owner.chunk_last <= last:
            self.initial_owner.resend = False
            self.initial_owner = None

    def _report_delivery(self, last: int, received: bool) -> None:
        # A newer cumulative acknowledgement can discard a snapshot before the
        # transport learns what happened to this report's own datagram.
        report = self.sent_reports.pop(last, None)
        if report is not None:
            report.received = received


__all__ = ["BodyScript", "BodyStream", "CommandEpochError", "Snapshot", "button_edges", "nodes", "warm_up"]
