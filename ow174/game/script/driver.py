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

from ow174.game import animation_pose, baptiste, barrier, ice_wall, immunity, pools, ultimate
from ow174.game.bits import BitWriter
from ow174.game.commands import FRAME_HALF, FRAME_MASK, frame_distance
from ow174.game.content import SOLDIER
from ow174.game.script import (
    body_owner,
    entity_queries,
    expr,
    graph,
    nodes,
    play,
    regeneration,
    runtime,
    spray_placement,
)
from ow174.game.script.codecs import stec_classes
from ow174.game.script.expr import INSTANCE, Asset, Entity, Vec3, f32
from ow174.game.statescript import (
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
OWNER_MAX_HEALTH = 1762  # authored 0071/node89 input
NOT_SENT = {OWNER_HEALTH, OWNER_MAX_HEALTH}  # derived from component 51
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
# Ultimate charge #342e and cost #338e are initialized by the body graphs.
# Brigitte uses 0B8B expressions with reconstructed combat/clock host inputs.
# Other heroes retain the unverified 60s passive fallback. Explicit 0 disables
# passive charging; a positive override retains the legacy test/GM curve.
ULT_CHARGE, ULT_COST = 342, 338
ULT_GAIN_ENABLED = 2426
ULT_GAIN_GRAPH = 0x0B8B
ULT_SECONDS = 60.0
GENJI = 0x02E0000000000029
GENJI_IN_AIR, GENJI_ON_GROUND = 417, 545
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


WHEEL_LOADOUT_TYPES = {"emote_aliases": 0x0C80, "emote_blend_trees": 0x0040, "spray_unlocks": 0x0250, "voice_effects": 0x0300}


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
        self._body_owner_context = body_owner.Context(
            body, True, True, lookup=self._lookup_body_owner,
            watch_environment=self._poll_entity_query, watch_owner=self._poll_entity_query,
        )
        # Server warm-up constructs a script with the null entity sentinel.
        self._entity_query_context = None
        if expr.valid_entity(body):
            self._entity_query_context = entity_queries.Context(
                body, True, True, True, lookup=self._lookup_entity_query,
                watch_environment=self._poll_entity_query, watch_possession=self._poll_entity_query,
            )

    def _poll_entity_query(self, *_):
        """Native dependency consumers are re-evaluated by the frame poll."""

    def _lookup_entity_query(self, entity):
        match = self.match
        if match is None:
            return None
        for player in match.players:
            if player.gone:
                continue
            if player.entity == entity:
                current = player.body if player.has_body else 0
                return entity_queries.Target(entity, entity_queries.Possession(current))
            if player.has_body and player.body == entity:
                return entity_queries.Target(entity, None)
        if entity == runtime.GAME_MODE_ENTITY or any(bot.entity == entity for bot in match.bots):
            return entity_queries.Target(entity, None)
        ability = getattr(match.combat, "orisa", None)
        if ability is not None and (entity in ability.orbs or entity in ability.superchargers.active):
            return entity_queries.Target(entity, None)
        return None

    def _lookup_body_owner(self, entity):
        for player in self.match.players:
            if not player.gone and player.has_body and player.body == entity:
                return body_owner.Target(entity, player.entity)
        if self._lookup_entity_query(entity) is not None:
            return body_owner.Target(entity, None)
        return None

    def linked_tracked_targets(self, state):
        if self.hero == regeneration.MERCY and self.match is not None and not state.instance.component.strict:
            targets = self.match.combat.script_targets(self.body, state)
            if targets is not NotImplemented:
                return tuple(expr.Entity(entity) for entity in targets)
        from ow174.game import sound_barrier

        if (self.hero == sound_barrier.HERO and self.match is not None and not state.instance.component.strict
                and (state.instance.graph.index, state.node.pos) == (sound_barrier.GRAPH, 16)):
            return self.match.combat.sound_barriers.tracked_targets(state)
        if self.hero == 0x02E000000000012E and self.match is not None and not state.instance.component.strict:
            controls = self.match.combat.sombra_controls
            player = controls.player(self.body)
            if player is not None:
                targets = controls.targets(player, state)
                if targets is not NotImplemented:
                    return targets
        if (self.hero == 0x02E0000000000068 and state.instance.graph.index == 0x025C
                and state.index in (4, 7)):
            return () if self.match is None else self.match.combat.projected_barrier_targets(self.body, state)
        if self.hero == 0x02E00000000001A2:
            entry = state.instance.graph.index, state.index
            if entry in ((0x1007, 17), (0x1007, 19)):
                return None  # Coalescence publishes from posed command frames.
            if entry == (0x0F76, 106):
                from ow174.game import moira

                return moira.grasp_targets(self, state)
        if self.hero == 0x02E000000000013B and state.instance.graph.index == 0x0B9E and state.index in (3, 6):
            from ow174.game import nano

            return () if self.match is None else nano.targets(self.match.combat, self.body, state)
        return immunity.transcendence_targets(self, state)

    def respawn_body(self, instance, node):
        if (self.match is None or self.hero != regeneration.MERCY or instance.component.strict
                or (instance.graph.index, node.pos) != (0x00CA, 20)):
            return NotImplemented
        target = instance.evaluate(node.field("m_targets"))
        while isinstance(target, tuple):
            target = target[0] if target else None
        caster = next((p for p in self.match.players if p.has_body and p.body == self.body), None)
        if caster is None or not isinstance(target, Entity):
            return False
        return self.match.combat.resurrect_player(caster, target.id, instance)

    def subgraph_scopes(self, entity):
        if entity == self.body and hasattr(self, "scope_host"):
            return self.scope_host
        return NotImplemented

    def ability_resource_available(self, state) -> bool:
        if self.hero != regeneration.MERCY or state.instance.graph.index != 0x10DA or state.index != 9:
            return True
        if getattr(self, "advancing_valkyrie_clock", False):
            return False
        pending = getattr(self, "pending_ult_credits", ())
        return not pending or state.now >= pending[-1][0]

    def entity_value(self, instance, state, identifier: int, value):
        from ow174.game.script import charge

        if identifier != 842:
            return value
        index = instance.graph.index
        if state is None and index == charge.PASSIVE_GRAPH and getattr(self, "historical_boost_award", False):
            return True
        if state is None or (index != 0x006D and (index, state.node.pos) != (0x006B, 169)):
            return value
        expiry = self.historical_valkyrie_deadline()
        if expiry is None or state.now >= expiry:
            return value
        # An observer first bound by late input missed the original 842 change.
        # Queue the same rebind at expiry without changing the live entity slot.
        if not any(event.kind == runtime.CHANGED and event.state is state and event.time == expiry
                   for event in state.instance.queue):
            state.instance.post(runtime.Event(expiry, 0, runtime.CHANGED, state=state))
        return True

    def historical_valkyrie_deadline(self):
        if self.hero != regeneration.MERCY or self.match is None:
            return None
        player = next((p for p in self.match.players if p.has_body and p.body == self.body), None)
        history = getattr(player, "valkyrie_pose", None)
        return history.expires_ms if history is not None else None

    def ability_start_allowed(self, state) -> bool:
        if not self.ability_resource_available(state):
            return False
        # The hosted projection supplies its overlap gate while the native
        # 025C recipient effect/tag system is unimplemented.
        if (self.match is not None and self.hero == 0x02E0000000000068
                and state.instance.graph.index == 0x0244 and state.index == 0):
            return self.match.combat._active_projected_barrier(self.body) is None
        return True

    def body_owner_context(self, entity):
        if self.match is None or entity != self.body:
            return NotImplemented
        return self._body_owner_context

    def entity_query_context(self, entity):
        if self.match is None or entity != self.body or self._entity_query_context is None:
            return NotImplemented
        return self._entity_query_context

    def _body_component(self, entity):
        if self.match is None:
            return None
        for player in self.match.players:
            if not player.gone and player.has_body and player.body == entity:
                script = player.body_script
                return script.component if script is not None and not script.retired else None
        for bot in self.match.bots:
            if bot.entity == entity:
                return getattr(bot.body_script, 'component', None)
        return None

    def script_component_present(self, entity):
        if self.match is None:
            return NotImplemented
        return self._body_component(entity) is not None

    def entity_var_slot(self, entity, var, *, create=False):
        """Resolve the live foreign slot from the delivered Brigitte body host."""
        if self.match is None or entity == runtime.GAME_MODE_ENTITY:
            return NotImplemented
        component = self._body_component(entity)
        return component.entity_var(var, create=create) if component is not None else None

    def entity_var(self, entity, var):
        from ow174.game import orisa_charge

        value = orisa_charge.foreign_var(self, entity, var)
        if value is not None:
            return value
        if self.match is None or entity == runtime.GAME_MODE_ENTITY:
            return super().entity_var(entity, var)
        # Foreign reads are sampled by the body frame poll. The other VM
        # owns its slots and must not acquire watchers from this component.
        component = self._body_component(entity)
        slot = component.entity_var(var, create=False) if component is not None else None
        value = slot.value() if slot is not None else None
        if (self.hero == regeneration.MERCY and var == 653 and value is None
                and component is not None and entity != self.body):
            return component.owner_dead
        return value

    def entity_ruleset(self, entity, key):
        if self.match is None:
            return NotImplemented
        component = self._body_component(entity)
        return component.world.ruleset(key) if component is not None else None

    def position(self, entity: int) -> Vec3:
        if self.match is not None:
            position = self.match.script_body_position(entity, self.body)
            if position is not None:
                return position
        return super().position(entity)

    def component76_keys(self, entity):
        if self.match is None:
            return NotImplemented
        from ow174.game import health_packs

        return health_packs.tag_keys(getattr(self.match.combat, "health_packs", {}).get(entity))


    def body_health(self, entity):
        lookup = getattr(self.match.combat, "body_health", None) if self.match is not None else None
        return lookup(entity) if lookup is not None else NotImplemented


    def controller_record(self, entity):
        lookup = getattr(self.match, "controller_record", None) if self.match is not None else None
        return lookup(entity) if lookup is not None else NotImplemented


    def confirm_portal(self, state):
        portals = getattr(self.match.combat, "symmetra_portals", None) if self.match is not None else None
        if portals is not None and (state.instance.graph.index, state.node.pos) == (0x1571, 69):
            portals.confirm(state)
            return True
        return NotImplemented


    def relocate_through_portal(self, instance, node):
        portals = getattr(self.match.combat, "symmetra_portals", None) if self.match is not None else None
        return portals.relocate(instance, node) if portals is not None else NotImplemented


    def skill_locked(self, state):
        controls = getattr(self.match.combat, "sombra_controls", None) if self.match is not None else None
        return controls is not None and not state.instance.component.strict and controls.blocked(state)


    def tracked_ray(self, state):
        if (self.match is not None and self.hero == 0x02E000000000012E
                and not state.instance.component.strict
                and (state.instance.graph.index, state.node.pos) == (0x0B1B, 59)):
            controls = getattr(self.match.combat, "sombra_controls", None)
            player = controls.player(self.body) if controls is not None else None
            if player is not None:
                return controls.ray(player, state)
        if (self.hero == 0x02E0000000000068 and state.instance.graph.index == 0x0241
                and state.index in (43, 81)):
            service = getattr(self.match.combat, "zarya_beam_ray", None) if self.match is not None else None
            return service(self.body, state) if service is not None else NotImplemented
        return NotImplemented


    def play_script_selected(self, instance, node):
        from ow174.game import doomfist_passive

        if self.match is None or instance.component.strict:
            return NotImplemented
        fight = self.match.combat
        if (self.hero == doomfist_passive.HERO and hasattr(fight, "doomfist_decay")
                and (instance.graph.index, node.pos) == (doomfist_passive.PASSIVE, 4)):
            return doomfist_passive.grant(self, instance, node)
        controls = getattr(fight, "sombra_controls", None)
        return controls.play(instance, node) if controls is not None else NotImplemented


    def resolve_spray(self, length: float) -> dict | None:
        if self.communication_wheel and self.match is not None:
            return self.match.resolve_body_spray(self.body, length)
        return None

    def play_compatible_script(self, instance, node) -> None:
        if self.match is None:
            return
        fight = self.match.combat
        if self.hero == 0x02E0000000000009 and instance.graph.index == 0x00FA and node.pos == 2:
            world_barriers = fight.barriers.world
            if any(actor.source_component is instance.component for actor in world_barriers.actors.values()):
                self.play_static_actor(instance, node)
                return
            from ow174.game import winston_barrier

            winston_barrier.deploy(fight, self.body, instance, node)
            return
        if self.hero != 0x02E000000000023B:
            return
        if instance.graph.index == 0x1B55 and node.pos == 8:
            from ow174.game import sigma_barrier

            sigma_barrier.play_barrier_request(fight, self.body, instance, node)
        if instance.graph.index == 0x1BBB and node.pos == 18:
            caster = next((player for player in self.match.players
                           if player.has_body and not player.gone and player.body == self.body), None)
            if caster is None:
                return
            entity = expr.to_entity(instance.evaluate(node.field("m_targets")))
            target = next((target for target in fight._enemies(caster) if target.entity == entity), None)
            if target is not None:
                fight.brigitte.host_control(caster, target, instance, node)

    def create_compatible_pet(self, state):
        from ow174.game import sigma_barrier

        if self.match is not None:
            return sigma_barrier.create_barrier(self.match.combat, self, state)
        return NotImplemented

    def retire_compatible_pet(self, state, entity: int) -> None:
        if self.match is not None:
            barrier_actor = self.match.combat.sigma_barriers.pop(entity, None)
            if barrier_actor is not None:
                barrier_actor.retire_child()

    def create_world_entity(self, instance, node):
        if self.match is not None:
            fight = self.match.combat
            result = fight.barriers.world.create(self, instance, node)
            if result is not NotImplemented:
                return result
            portals = getattr(fight, "symmetra_portals", None)
            if portals is not None:
                return portals.create(instance, node)
        return NotImplemented

    def play_static_actor(self, instance, node):
        if self.match is not None:
            return self.match.combat.barriers.world.host(self, instance, node)
        return NotImplemented

    def compatibility_play_script(self, instance, node):
        from ow174.game import barriers

        if self.hero == 0x02E000000000023B and self.match is not None:
            if instance.graph.index == 0x1D02 and node.pos == 34:
                from ow174.game import interception

                if not interception.play_shield_script(self, instance, node):
                    self.match.combat.grant_grasp_shield(instance, node)
                return
        barriers.play_script(self, instance, node)

    def create_pet(self, state):
        from ow174.game import barriers

        if state.instance.component.strict:
            return super().create_pet(state)
        from ow174.game import photon_barrier

        if (self.match is not None and state.instance.graph.index == photon_barrier.GRAPH
                and state.node.pos == 17):
            entity = self.match.combat.photon_barriers.create(self, state)
            return 0 if entity is NotImplemented else entity
        return barriers.create(self, state)

    def track_targets(self, state):
        from ow174.game import barriers

        barriers.track_projected_target(self, state)
        return barriers.track_transcendence(self, state)

    def modify_health(self, state, elapsed_ms):
        from ow174.game import barriers

        controls = getattr(self.match.combat, "sombra_controls", None) if self.match is not None else None
        if (controls is not None and not state.instance.component.strict
                and (state.instance.graph.index, state.node.pos) == (0x0B32, 9)):
            if elapsed_ms == 0:
                controls.drain(state)
                state.request_finish()
            return True
        value = immunity.transcendence_heal(self, state, elapsed_ms)
        if value is not NotImplemented:
            return value
        value = barriers.cryo_heal(self, state, elapsed_ms)
        return barriers.transcendence_heal(self, state, elapsed_ms) if value is NotImplemented else value

    def end_health(self, state):
        self.modify_health(state, max(0, state.now - state.last_applied))

    def compatibility_message_receiver(self, state):
        from ow174.game import assassin_victim

        return assassin_victim.receiver(self, state)

    def compatibility_stop_script(self, instance, node):
        from ow174.game import assassin_victim

        return assassin_victim.stop_script(self, instance, node)

    def retire_pet(self, entity):
        if self.match is not None:
            from ow174.game import barriers

            barriers.retire(self.match.combat, entity)

    def movement_flag_projection(self, instance, node):
        # Reconstructed only for Baptiste1983's single crouch output. The other
        # 96F1DBCF projections remain unknown; do not synthesize their flags.
        if (self.match is not None and self.hero == 0x02E0000000000221
                and instance.graph.index == 0x1983 and node.state == 8):
            for player in self.match.players:
                if player.has_body and player.body == self.body:
                    crouched = player.mover.crouched and not instance.component.owner_dead
                    return 0x40, 0x40 if crouched else 0
        return None

    def linked_target_sample(self, entity: int):
        match = self.match
        if (match is None or entity != self.body or entity in match.combat.pins.values()
                or match.script_player_body(entity, self.body) is None):
            return NotImplemented
        # The reconstructed Match owns this unparented player pose. Pin links
        # need the native linked transform rather than this unlinked sample.
        return None, 0.0

    def sample_sleep_view(self):
        """Evaluate the authored 224E view branch using the current Match pose."""
        definition = graph.graph(0x224E)
        if definition is None or self.linked_target_sample(self.body) is NotImplemented:
            return None
        component = runtime.Component(self.body, strict=True, world=self)
        instance = component.create(definition, init=False)
        instance.initialized = True
        try:
            # Node 29's wait is scheduled by the combat clock. Its output
            # enters 30, evaluates both owner queries and starts node 34.
            instance.run_node(definition.node(30), None)
            component.run_now()
            return instance.node_state(definition.node(34)).mover_angles
        except runtime.UnsupportedScriptError as error:
            expr.warn_once("sleep-view-resource", "%s", error)
            return None
        finally:
            component.destroy(instance)

    def native(self, cfg: dict, instance):
        from ow174.game import lucio, lucio_movement, mccree, sombra_control

        if self.hero == regeneration.MERCY and self.match is not None and instance is not None:
            cls = cfg.get("$")
            if instance.graph.index == 0x01C7:
                if cls == "STU_4A101787":
                    return True
                if cls == "STU_9622CAD6":
                    return Entity(runtime.GAME_MODE_ENTITY)
                if cls == "STU_ABB6C23E":
                    return Asset(0)
            if cls == "STU_F7262B36" and instance.graph.index in (0x0B8B, 0x006B):
                entity = expr.to_entity(instance.evaluate(cfg.get("m_801B298F")))
                owner = next((p for p in self.match.players
                              if p.has_body and not p.gone and p.body == entity), None)
                return Entity(owner.entity if owner is not None else 0)
            from ow174.game.script import charge

            result = charge.damage_native(self, cfg, instance)
            if result is not NotImplemented:
                return result

        if (self.match is not None and self.hero == regeneration.MERCY and instance is not None
                and instance.graph.index == 0x00CA and not instance.component.strict):
            cls = cfg.get("$")
            if cls == "STU_4A101787":
                return True
            if cls in ("STU_9B7648C3", "STU_E72F6348", "STU_71A87E92", "STU_21A13145",
                       "STU_F7FD94F9", "STU_FDBB0B67"):
                value = instance.evaluate(cfg.get("m_entity"))
                while isinstance(value, tuple):
                    value = value[0] if value else None
                entity = expr.to_entity(value)
                target = next((t for t in self.match.combat._targets() if t.entity == entity), None)
                if cls == "STU_E72F6348":
                    return target is not None
                if cls == "STU_9B7648C3":
                    return self.match.combat.vitals_of(target).total if target is not None else 0.0
                if target is not None:
                    if cls == "STU_71A87E92":
                        return Vec3(*target.feet)
                    if cls == "STU_21A13145":
                        return Vec3(target.feet[0], target.feet[1] + target.eye, target.feet[2])
                    if cls == "STU_F7FD94F9":
                        return f32(target.radius)
                    script = target.owner.body_script
                    override = script.component.entity_var(BODY_SCALE_OVERRIDE, create=False) if script else None
                    scale = override.value() if override is not None else None
                    return UNIT_ENTITY_SCALE if scale is None else scale
        if self.hero == lucio.HERO:
            charge_value = lucio.damage_charge_query(self, cfg, instance)
            if charge_value is not NotImplemented:
                return charge_value

        movement_value = lucio_movement.query(self, cfg, instance)
        if movement_value is not NotImplemented:
            return movement_value

        if (self.match is not None and self.hero == sombra_control.HERO
                and instance is not None and not instance.component.strict
                and instance.graph.index == sombra_control.HACK):
            result = self.match.combat.sombra_controls.native(self.body, cfg, instance)
            if result is not NotImplemented:
                return result
        result = mccree.native(self, cfg, instance)
        if result is not NotImplemented:
            return result
        if self.match is not None and instance is not None and instance.graph.index == 0x1B55:
            from ow174.game import sigma_barrier

            health = sigma_barrier.barrier_health_query(self, cfg, instance)
            if health is not NotImplemented:
                return health
        if (self.match is not None and self.hero == 0x02E000000000023B
                and instance is not None and instance.graph.index == 0x1BBB):
            caster = next((player for player in self.match.players
                           if player.has_body and not player.gone and player.body == self.body), None)
            if caster is not None and cfg == instance.graph.node(16).field("m_condition"):
                entity = expr.to_entity(instance.evaluate(cfg["m_entity"]))
                return any(target.entity == entity for target in self.match.combat._enemies(caster))
            if caster is not None and cfg == instance.graph.node(19).field("m_value"):
                return Vec3(*caster.mover.position)
        from ow174.game import orisa_charge, orisa_halt

        halt = orisa_halt.native(self, cfg, instance)
        if halt is not NotImplemented:
            return halt

        charge = orisa_charge.native(self, cfg, instance)
        if charge is not NotImplemented:
            return charge
        cls = cfg.get("$")
        if instance is not None and instance.graph.index == 0x156B:
            from ow174.game import photon_barrier

            result = photon_barrier.native(self, cfg, instance)
            if result is not NotImplemented:
                return result
        if cls == "STU_DB742151" and self.match is not None:
            match = self.match
            value = play.evaluate_scalar(instance, cfg.get("m_entity"), initial=Entity(0))
            instance.component.ensure_valid()
            if (self.match is not match or instance.stopped or instance.teardown
                    or instance.component.instances.get(instance.id) is not instance):
                instance.component.unsupported(instance, "primary possessor query retired context", cls)
            entity = expr.to_entity(value) & 0xFFFFFFFF
            for player in match.players:
                if not player.gone and (
                    player.entity == entity or (player.has_body and player.body == entity)
                ):
                    return Entity(player.entity)
            return Entity(0)
        if cls in ("STU_9B7648C3", "STU_AC829876") and self.match is not None:
            result = self.match.combat.brigitte.native(cfg, instance)
            if result is not NotImplemented:
                return result
        if (cls == "STU_9B7648C3" and instance is not None and instance.graph.index == 0x1177
                and instance.component.entity == self.body
                and instance.evaluate(cfg.get("m_entity")) == Entity(self.body)):
            health = instance.component.entity_var(OWNER_HEALTH, create=False)
            return expr.to_float(health.value()) if health is not None else 0.0
        if (cls == 'STU_8CBDD81C' and self.match is not None and instance is not None
                and self.hero == 0x02E0000000000009 and instance.graph.index in (0xFA,0xFC)
                and instance.component.entity == self.body):
            entity = expr.to_entity(instance.evaluate(cfg.get('m_entity')))
            if entity == self.body:
                return self.position(entity)  # scoped authoritative creation position
        if cls == "STU_9B7648C3" and instance is not None:
            from ow174.game import assassin_victim

            value = assassin_victim.health(self, cfg, instance)
            if value is not NotImplemented:
                return value
        if (cls == "STU_295F1CDE" and self.hero == 0x02E0000000000007 and instance is not None
                and instance.graph.index in (0x95, 0x96)):
            return 1.0  # reconstructed normal-mode health scale; native/mode mapping remains open
        if cls == "STU_783B4CF0" and instance is not None:
            from ow174.game import barriers, interception

            value = interception.shield_amount(self, instance, cfg)
            if value is NotImplemented:
                value = barriers.health_query(self, instance, cfg)
            if value is not NotImplemented:
                return value
        if (self.hero == GENJI and self.match is not None and instance is not None
                and cls in {"STU_21A13145", "STU_2200A66D"}
                and instance.evaluate(cfg.get("m_entity")) == Entity(self.body)):
            # Reconstructed Genji cast inputs. The native engine query's full
            # transform/attachment rules remain unverified; only own live body.
            owner = next((p for p in self.match.players
                          if p.body == self.body and p.body_script is not None), None)
            if owner is not None:
                from ow174.game.combat import Combat, aim

                return Vec3(*(Combat._eye(self.match.combat, owner) if cls == "STU_21A13145"
                              else aim(owner.mover.yaw, owner.mover.pitch)))
        if (self.hero == GENJI and self.match is not None and instance is not None
                and instance.graph.index == 0x0B8B and cls == "STU_F7262B36"
                and (cfg.get("m_801B298F") or {}).get("$") == "STU_B5A0CAF0"
                and instance.component.entity == self.body):
            # Explicit normal-player server profile. Native association details
            # are OPEN; scope this related-entity gate to owned Genji0B8B only.
            owner = next((p for p in self.match.players if p.body == self.body and not p.gone), None)
            return Entity(owner.entity if owner is not None and owner.spawned and owner.has_body
                          and not instance.component.owner_dead and not self.match.ended else 0)
        if self.match is not None and cls in ("STU_E72F6348", "STU_2B2E2F6D"):
            entity = expr.to_entity(instance.evaluate(cfg.get("m_entity") or cfg.get("m_81A364DE")))
            orb = self.match.combat.orisa.orbs.get(entity)
            device = self.match.combat.orisa.superchargers.active.get(entity)
            if device is not None:
                if cls == "STU_E72F6348":
                    return True
                if cfg.get("m_7BDB02D1") == 0:
                    return Entity(device.source_body)
            if orb is not None:
                if cls == "STU_E72F6348":
                    return True  # actual currently registered server ability entity
                if cfg.get("m_7BDB02D1") == 0:
                    # Reconstruct only the declared pet-owner relation used by
                    # Halt's own-body gate, never arbitrary entity ownership.
                    return Entity(orb.rocket.source_body)
        if (self.match is not None and self.hero == barrier.REINHARDT and instance is not None
                and instance.component.entity == self.body and instance.graph.index == 0x00BE):
            # Reconstructed owner pose services for the authored Earthshatter release expressions.
            if cls == "STU_71A87E92":
                return self.position(self.body)
            if cls in ("STU_C8C4C20D", "STU_021D01CD"):
                owner = next((player for player in self.match.players if player.body == self.body), None)
                if owner is not None:
                    yaw = owner.mover.yaw
                    if cls == "STU_C8C4C20D":
                        return yaw * 360.0 / 65536
                    from ow174.game.combat import aim

                    return Vec3(*aim(yaw, 0))
        if self.match is not None and self.hero == 0x02E0000000000007 and instance is not None:
            value = self.match.combat.barrier.native(instance, cfg)
            if value is not NotImplemented:
                return value
        if self.match is not None and self.hero == 0x02E000000000023B and instance is not None:
            value = self.match.combat.absorption.pool_amount(instance, cfg)
            if value is not NotImplemented:
                return value
        if self.match is not None and self.hero == 0x02E0000000000221 and instance is not None:
            value = self.match.combat.baptiste.matrix_native(instance, cfg)
            if value is not NotImplemented:
                return value
        if self.match is not None and self.hero == ice_wall.MEI and instance is not None:
            value = ice_wall.native(self.match.combat, instance, cfg)
            if value is not NotImplemented:
                return value
        victim_health = cls == "STU_9B7648C3" and instance is not None and instance.graph.index == 0x1177
        if instance is not None and (victim_health or cls in {"STU_32E6E4D8", "STU_7D7D2470"}):
            # Data-derived current/max-health queries used by Burst and1763.
            target = instance.evaluate(cfg.get("m_entity"))
            if target == Entity(self.body):
                if cls == "STU_7D7D2470":
                    return getattr(instance.component, "maximum_health", None)
                health = instance.component.entity_var(OWNER_HEALTH, create=False)
                value = health.value() if health is not None else None
                return f32(value) if victim_health and type(value) in (int, float) else value
        if cls == "STU_1FF67CA6":  # GetLocalPlayerPossessedView: the owner's client views its body
            return Entity(self.body)
        if cls == "STU_310D6529":  # GetThrottle (0x7FF789BDA130): the command's throttle bytes * f32(1/127)
            forward, right = self.throttles
            return Vec3(f32(right * THROTTLE_UNIT), f32(forward * THROTTLE_UNIT), 0.0)
        if cls == "STU_37F20D24":  # GetGameMode: the match's mode (what component 114 announces)
            return Asset(self.mode)
        if cls == "STU_9B7648C3":
            # Native CEB6C0 -> D177C0 resolves component51 (BCC870) and its
            # numeric getter at vtable+40. Reconstructed aggregate-health
            # consumer, using the same authoritative input as the body HUD.
            # Unknown/foreign entities retain the ordinary unsupported path.
            target = cfg.get("m_entity")
            if (instance.component.entity == self.body and isinstance(target, dict)
                    and instance.evaluate(target) == Entity(self.body)):
                health = instance.component.entity_var(OWNER_HEALTH, create=False)
                value = health.value() if health is not None else None
                if type(value) in (int, float):
                    return f32(value)
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
        if instance is not None and self.hero == 0x02E00000000001A2 and instance.graph.index == ULT_GAIN_GRAPH and cls in {
            "STU_AC829876", "STU_A58C1E63", "STU_25ECB55F",
        }:
            from ow174.game import moira

            result = moira.damage_charge_query(self, cfg, instance)
            if result is not NotImplemented:
                return result
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
        if (self.hero == 0x02E00000000001A2 and self.match is not None
                and instance is not None and instance.graph.index == 0x0F76 and cls in {"STU_E72F6348", "STU_AC829876"}):
            selected = cfg.get("m_entity") or {}
            fight = self.match.combat
            if cls == "STU_E72F6348" and expr.lvalue(selected) == (expr.INSTANCE, 375):
                entity = expr.to_entity(instance.evaluate(selected))
                return bool(entity and any(target.entity == entity for target in fight._targets()))
            if cls == "STU_AC829876":
                signature = {k: v for k, v in (cfg.get("m_filter") or {}).items() if v not in (None, 0)}
                source = (selected.get("m_expression") or {}).get("m_dynamicVars") or []
                # State 99 wraps its tracked entity in an expression before applying the body filter.
                if (signature == {"$": "STUConfigVarFilter", "m_84065FD1": 1}
                        and len(source) == 1 and expr.lvalue(source[0]) == (expr.INSTANCE, 375)):
                    entity = expr.to_entity(instance.evaluate(selected))
                    owner = next((p for p in self.match.players if p.body == self.body and not p.gone), None)
                    return bool(entity and owner is not None
                                and any(target.entity == entity for target in fight._enemies(owner)))
        if (self.hero == 0x02E0000000000016 and self.match is not None
                and instance is not None and instance.graph.index == 0x157C and cls in {"STU_E72F6348", "STU_AC829876"}):
            # Reconstruct node 48's live-body branch. Barrier/tag membership
            # and the alternate target source remain outside this binding.
            selected = cfg.get("m_entity") or {}
            if selected.get("$") == "STUConfigVarDynamic" and selected.get("m_identifier") in (38, 536):
                entity = expr.to_entity(instance.evaluate(selected))
                if cls == "STU_E72F6348":
                    return bool(entity and any(target.entity == entity for target in self.match.combat._targets()))
                target_filter = cfg.get("m_filter") or {}
                if (selected["m_identifier"] == 536 and target_filter.get("m_84065FD1") == 64
                        and target_filter.get("m_4EA686DB") == 128):
                    owner = next((p for p in self.match.players if p.body == self.body and not p.gone), None)
                    return bool(entity and owner is not None
                                and any(target.entity == entity for target in self.match.combat._enemies(owner)))
        if cls == "STU_AC829876":  # does an entity pass a filter (teams, tags): not modelled
            cfg_filter = cfg.get("m_filter") or {}
            signature = {k: v for k, v in cfg_filter.items() if v not in (None, 0)}
            if (self.hero == 0x02E0000000000029 and self.match is not None
                    and instance.graph.index == 0x01DB
                    and signature == {"$": "STUConfigVarFilter", "m_84065FD1": 1}):
                # Scoped reconstruction of the elimination target's hero/body
                # predicate. Do not generalize unknown filters or tag masks.
                target = instance.evaluate(cfg.get("m_entity"))
                if isinstance(target, Entity) and target.id != self.body:
                    return any(p.has_body and p.body == target.id for p in self.match.players) or any(
                        b.entity == target.id for b in self.match.bots
                    )
            expr.warn_once(cls, "entity filters (STU_AC829876) answer false")
            return False
        return super().native(cfg, instance)

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
        from ow174.game.script import play, play_queue

        index = WHEEL_PLAY_GRAPHS.get((instance.graph.index, node.pos))
        if not self.communication_wheel or index is None:
            if instance.component.strict:
                super().play_script(instance, node)
            else:
                self.play_compatible_script(instance, node)
                self.compatibility_play_script(instance, node)
            return
        resource = play.resolve_resource(instance, node)
        if resource is None or resource.guid != ((graph.GRAPH_TYPE << 48) | index):
            return
        definition = graph.graph(index)
        if definition is None:
            return
        component = instance.component
        request = play.resolve_request(
            instance, node, resource,
            context_provider=lambda: play.InvocationContext(self.body, self.body, instance.source_entity()),
        )
        if request is not None and request.targets == (self.body,):
            # The bundle already holds the resource, so submission can flush synchronously.
            play_queue.Queue(component, lambda _: play_queue.Load("ready", definition)).submit(request)

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
        if index not in (0x02CF, 0x05AD, 0x0686):
            if self.body_definition != LEGACY_SYMMETRA_BODY or index != 0x0169 or node.pos not in (82, 86):
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
        from ow174.game import lucio_movement, mccree

        if self.hero == lucio_movement.HERO:
            return lucio_movement.action(instance, node)
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

        from ow174.game import mccree

        return mccree.targets(self, state)

    def target_effects(self, state):
        from ow174.game import mccree

        return mccree.effects(self, state)

    def raycast(self, request):
        """Reconstruct Genji's static dash cast using current collision geometry.

        Only the authored 0029 filter and own-body ignore list are supported.
        Dynamic barriers and the native filter resource semantics remain open.
        """
        cfg = request.filter_config or {}
        resource = cfg.get("m_C1903771") or {}
        if (self.hero != 0x02E0000000000029 or self.match is None
                or self.match.collision_world is None
                or resource.get("m_C1903771") != "0x0690000000000029"
                or request.ignored_entities != (self.body,)):
            return None
        hit = self.match.collision_world.cast_ray(
            tuple(request.origin), tuple(request.direction), request.length,
        )
        if hit is None:
            return runtime.RaycastResult(False, None, None, None)
        return runtime.RaycastResult(True, None, Vec3(*hit.point), Vec3(*hit.normal))

    def assassin_capture_sender(self, instance):
        if self.match is not None and self.hero == baptiste.HERO:
            return self.match.combat.assassin_capture_sender(instance)
        return None

    def play_script_host(self, instance, node):
        from ow174.game import photon_barrier

        if self.match is not None and self.hero == photon_barrier.HERO:
            return self.match.combat.photon_barriers.play(instance, node) is True
        if self.match is not None and self.hero == 0x02E000000000023B:
            return self.match.combat.absorption.play(instance, node)
        if self.match is not None and self.hero == ice_wall.MEI:
            return ice_wall.play_pillar(self.match.combat, instance, node)
        return False

    def completes_empty_marker(self, state: runtime.State) -> bool:
        # Reconstructed completion for the selected ability gates.
        instance = state.instance
        if (self.match is None or instance.component.entity != self.body
                or any(state.node.field(name) is not None for name in (
                    "m_ED97A97C", "m_7C8AD485", "m_C7A42E2A", "m_F8CE386A", "m_C46403A5",
                ))):
            return False
        if self.hero == ice_wall.MEI:
            return (instance.graph.index, state.node.pos) == (0x05FE, 64)
        if self.hero != barrier.REINHARDT:
            return False
        if (instance.graph.index, state.node.pos) in ((0x00E2, 10), (0x007D, 89)):
            return True
        if (instance.graph.index, state.node.pos) != (0x00BE, 92):
            return False
        player = next((player for player in self.match.players if player.body == self.body), None)
        world = self.match.collision_world
        if player is None or world is None or player.mover.airborne:
            return False
        x, y, z = player.mover.position
        return world.ground(x, z, y + 0.05, y - 1.5, player.mover.category) is not None

    def pet_lifecycle(self, state, entering):
        if self.match is not None and self.hero == 0x02E0000000000007:
            self.match.combat.barrier.pet(state, entering)

    def placement_probe(self, state):
        return self.match is not None and ice_wall.probe(self.match.combat, state)

    def refresh_compatible_placement(self, state, *, confirm):
        from ow174.game import photon_barrier

        if self.match is None or self.match.combat.photon_barriers.geometry is None:
            return NotImplemented
        return photon_barrier.placement(self, state, confirm=confirm)

    def placement_confirmation(self, state):
        if self.match is not None:
            if self.hero == ice_wall.MEI:
                return ice_wall.confirm(self.match.combat, state)
            return self.match.combat.baptiste.matrix_confirm(state)
        return NotImplemented

    def spawn_deployable(self, instance, node):
        if self.match is not None:
            if self.hero == ice_wall.MEI:
                return ice_wall.spawn(self.match.combat, instance, node)
            return self.match.combat.baptiste.matrix_spawn(instance, node)
        return NotImplemented

    def send_message(self, target: int, message: int, params: dict, sender: int) -> None:
        from ow174.game import barriers

        if barriers.relay_bubble_energy(self, target, message, params, sender):
            return
        match = self.match
        if (match is not None and sender == self.body
                and match.combat.orisa.receive(target, message, sender)):
            return
        if match is not None and sender == self.body:
            if self.hero == ice_wall.MEI and ice_wall.cancel(match.combat, target, message, params, sender):
                return
            if match.queue_body_message(target, message, params, sender):
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
        event = next((item for item in self.callouts if item.serial == serial and serial > base.callout), None)
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
        self.component.maximum_health = float(most)  # server query input, not a guessed entity variable

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
            if instance.graph.index == 0x224E:
                # Reconstructed host owns the pending/selected status records.
                # Neither is an authored sync variable or remote-state input.
                # Native record-map wire transport is not implemented.
                excluded |= {31528, 26550}
        return ((var_id, var) for var_id, var in variables.items() if var_id not in excluded)


class BodyScript(ReplicatedBody):
    """The statescript of one hero body: `spawn_frames()` once the body exists on the client, then
    `command()` for every new client command frame and `frames()` every server tick."""

    def replicated_vars(self, instance=None):
        values = super().replicated_vars(instance)
        if instance is not None and instance.graph.index == 0xFA and self.world.match is not None:
            # Private static actor38 has no native creation transport yet. Keep it
            # in the authoritative VM/snapshot, never send a dangling client ID.
            return ((key,var) for key,var in values if key != 38)
        return values

    def __init__(
        self,
        hero: int,
        entity: int,
        start_frame: int,
        quanta: int = 16000,
        mode: int = PRACTICE_RANGE_MODE,
        health: float | None = None,
        ult_seconds: float | None = None,
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
            body_definition = next((key for key, item in records.items() if int(item["hero"], 16) == hero), None)
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
        if hero == regeneration.MERCY:
            from ow174.game.script import scopes

            self.world.scope_host = scopes.ForwardHost(self.component)
            self.world.scope_host.set_frame(start_frame)
        self.frame = start_frame  # the last command frame run
        self.buttons = 0
        self.stream = BodyStream(entity)
        self.create_arrived = False  # the client has the body (see `created`)
        self.retired = False
        self.first_owner_frame: int | None = None
        if ult_seconds is None and hero not in (0x02E0000000000195, baptiste.HERO):
            ult_seconds = 0.0 if hero == GENJI else ULT_SECONDS
        self.ult_seconds = ult_seconds
        self.passive_charge_ms = 0
        self.ult_gain_bonus = 0.0
        self.ultimates: list = []  # the Ability states that start on #342e (not charged while they run)
        self.reports: list[OwnerReport] = []
        self.report_count = 0  # owner frames sent so far
        self.self_healing = 0.0
        self.support_healing = []
        initial_pools = (pools.body_pools(hero) if body_definition is None
                         else pools.active_pools(hero, body_definition))
        self._spawn(start_frame, sum(initial_pools) if health is None else health)

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
        if self.hero == regeneration.MERCY:
            # The delivered targeting graphs multiply their authored cones by these inputs.
            component.set(9583, 1.0)
            component.set(9582, 1.0)
        from ow174.game import lucio

        if self.hero == lucio.HERO and self.world.match is not None:
            for identifier, value in lucio.SETTING_DEFAULTS.items():
                component.set(identifier, value)
            if self.world.ruleset(lucio.ULTIMATE_ENABLED) is False:
                component.set(lucio.ULTIMATE_ENABLED, False)
            for key in (lucio.ULTIMATE_GENERATION, lucio.PASSIVE_GENERATION, lucio.COMBAT_GENERATION):
                value = self.world.ruleset(key)
                if value is not None and value != 1:
                    component.set(key, value)
        if self.hero == GENJI and self.world.match is not None:
            # Normal server profile, not a claim about original server-only
            #01C7 initialization/mode flags. Authored mode/ultimate gates still run.
            component.set(2426, self.ult_seconds <= 0 and health > 0)
            component.set(842, False)
            component.set(11942, False)
        for instance in create_body_graphs(component, self.record):
            instance.init()
            if self.hero == lucio.HERO and self.world.match is not None and instance.graph.index == ULT_GAIN_GRAPH:
                # init queues ordinary entries. The world clock owns the passive
                # entry, while combat, restore and spawn messages stay available.
                instance.queue[:] = [
                    event for event in instance.queue
                    if not (event.kind == runtime.ENTRY and event.node.pos == 2)
                ]
        if hasattr(self.world, "scope_host"):
            self.world.scope_host.run_frame(frame)
        else:
            component.run_frame(frame)
        if self.hero == lucio.HERO and self.world.match is not None:
            lucio.stop_passive_timer(component)
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
        if self.hero == regeneration.MERCY:
            self.component.set(OWNER_MAX_HEALTH, float(most))
        self.component.set(HUD_HEALTH, float(health))
        self.component.set(HUD_MAX_HEALTH, float(most))
        if self.hero == GENJI and health <= 0:
            self.component.set(2426, False)  # retire this body's authored passive wait on death
        self.component.owner_dead = health <= 0  # as the client sees its owner (comp 51, 0x7FF789ADBF00)

    # --- running -----------------------------------------------------------------------------------

    def command(
        self, frame: int, buttons: int, action: int = 0, forward: int = 0, right: int = 0,
        *, grounded: bool | None = None, airborne: bool | None = None,
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
        if self.hero == GENJI and airborne is not None:
            # 01C7 state 4 publishes the IN_AIR mask and its inverse (D3D7EA/D3D9D6/D3D9EF).
            # Its initial packed state is zero, so an initially false output has no write.
            for var, value in ((GENJI_IN_AIR, airborne), (GENJI_ON_GROUND, not airborne)):
                previous = self.component.entity_var(var).base
                if (value or previous is not None) and previous is not value:
                    self.component.set(var, value)
        self.world.throttles = (forward, right)
        if self.world.communication_wheel:
            # 01C7:7 reads the mover's flags and the command axes separately.
            if grounded is not None:
                self.component.set(545, grounded)
            self.component.set(2070, bool(forward or right))
        self._run(frame, button_edges(self.buttons, buttons, action))
        if self.hero == 0x02E0000000000079 and self.world.match is not None:
            self.passive_ult_last_command_tick = self.world.match.tick
        self.buttons = buttons
        self.frame = frame

    def _run(self, frame: int, edges: list) -> None:
        from ow174.game import lucio

        if self.hero == lucio.HERO:
            lucio.stop_passive_timer(self.component)
            player = lucio.charge_player(self.world, self.component)
            if player is not None:
                common = next((i for i in self.component.instances.values() if i.graph.index == 0x01C7), None)
                if common is not None and not common.node_state(common.graph.node(77)).active:
                    runtime.send_instance_message(common, lucio.CHARGE_ACTIVE, {}, player.entity)
        if hasattr(self.world, "scope_host"):
            self.world.scope_host.set_frame(frame)
            self.world.scope_host.run_frame(frame, edges)
        else:
            self.component.run_frame(frame, edges)
        self.self_healing += regeneration.report(self)
        from ow174.game.script import support

        self.support_healing.extend(support.healing_reports(self, frame))
        immunity.advance_startup(self.component)
        self._report_shots()
        self._charge_ult()

    def set_ult_charge(self, points: float) -> None:
        """Set the ultimate's charge (#342e; the cost is #338e)."""
        self.component.set(ULT_CHARGE, f32(points))

    def add_ult_charge(self, points: float) -> None:
        if any(state.flags & nodes.Ability.RUNNING for state in self.ultimates):
            return
        cost = expr.to_float(self.component.entity_var(ULT_COST).value())
        charge = expr.to_float(self.component.entity_var(ULT_CHARGE).value())
        if cost > 0 and charge < cost:
            self.set_ult_charge(min(cost, charge + points))

    def gain_ult(self, amount: float) -> float:
        """Reconstructed health-event conversion: one point per actual HP, scaled by2819.

        Cost/cap and the ruleset scalar are authored inputs. The one-point conversion
        is explicit server reconstruction. Ultimate-sourced damage/heal tag policy
        still requires a native-backed service.
        """
        if not math.isfinite(amount) or amount < 0:
            raise ValueError("ultimate health-event amount must be finite and nonnegative")
        if not amount or any(state.flags & nodes.Ability.RUNNING for state in self.ultimates):
            return 0.0
        cost = expr.to_float(self.component.entity_var(ULT_COST).value())
        charge = expr.to_float(self.component.entity_var(ULT_CHARGE).value())
        scalar = expr.to_float(self.world.ruleset(0x2819))
        if cost <= 0 or scalar <= 0 or not math.isfinite(scalar) or charge >= cost:
            return 0.0
        after = self._gain_ult_points(cost, max(0.0, charge), f32(amount * scalar))
        return after - charge

    def _gain_ult_points(self, cost: float, charge: float, points: float) -> float:
        after = min(cost, f32(charge + points))
        if self.ult_gain_bonus:
            after = min(cost, f32(after + f32((after - charge) * self.ult_gain_bonus)))
        self.set_ult_charge(after)
        return after

    def ult_fraction(self) -> float:
        """Capture the current unspent fraction before this body is retired."""
        if self.retired or self.component.fault is not None:
            return 0.0
        cost = expr.to_float(self.component.entity_var(ULT_COST).value())
        charge = expr.to_float(self.component.entity_var(ULT_CHARGE).value())
        if not math.isfinite(cost) or cost <= 0 or not math.isfinite(charge):
            return 0.0
        return f32(max(0.0, min(1.0, charge / cost)))

    def restore_genji_ult(self, fraction: float, controller: int) -> None:
        """Send the authored 14F2/0951 fraction to the new Genji body."""
        if self.hero != GENJI or not 0 < fraction <= 1:
            return
        charge = next((i for i in self.component.instances.values() if i.graph.index == 0x0B8B), None)
        if charge is not None:
            runtime.send_instance_message(charge, 0x02400000000014F2, {0x0951: fraction}, controller)
            self.component.run_now()

    def gain_ult_charge(self, points: float) -> float:
        """Reconstructed one-point-per-effective-damage credit, capped at cost.

        The active ultimate generates no charge. Passive debug fill remains a
        separate configured policy; this does not establish native charge rates.
        """
        if (self.retired or self.component.fault is not None
                or (self.hero == 0x02E000000000013B and self.component.owner_dead)
                or type(points) not in (int, float) or not math.isfinite(points) or points <= 0
                or any(state.flags & nodes.Ability.RUNNING for state in self.ultimates)):
            return 0.0
        cost = expr.to_float(self.component.entity_var(ULT_COST).value())
        before = expr.to_float(self.component.entity_var(ULT_CHARGE).value())
        if not math.isfinite(cost) or not math.isfinite(before) or before < 0 or cost <= 0 or before >= cost:
            return 0.0
        after = min(cost, f32(before + min(points, cost - before)))
        self.set_ult_charge(after)
        return after - before

    def _charge_ult(self) -> None:
        if self.hero == regeneration.MERCY and self.world.match is not None:
            return  # The delivered 0B8B graph owns Mercy passive charge.
        if self.hero == 0x02E0000000000079 and self.world.match is not None:
            return
        if self.hero == baptiste.HERO and self.ult_seconds is None:
            if self.retired or self.component.owner_dead or any(state.flags & nodes.Ability.RUNNING for state in self.ultimates):
                self.passive_charge_ms = 0
            else:
                baptiste.passive_charge(self)
            return
        if self.hero == 0x02E0000000000195:
            from ow174.game.brigitte import BrigitteCombat

            if not BrigitteCombat.charge_allowed(self):
                self.passive_charge_ms = 0
                return
            if self.ult_seconds is None:
                ultimate.passive_points(self)
                return
        if self.hero == GENJI and expr.truthy(self.component.entity_var(2426).value()):
            return  # The authored profile owns passive charge; never add debug fill on top.
        from ow174.game import orisa_charge

        if orisa_charge.activate(self):
            return
        blocked = self.component.entity_var(842, create=False)
        if (self.ult_seconds <= 0 or (blocked is not None and expr.truthy(blocked.value()))
                or any(state.flags & nodes.Ability.RUNNING for state in self.ultimates)):
            return
        if self.hero == 0x02E00000000001A2 and self.world.match is not None and self.world.match.assembling():
            return
        # A supplied shared-graph gate owns passive charge, even when disabled.
        # The fallback only covers hosts that have not supplied that gate.
        enabled = self.component.entity_var(ULT_GAIN_ENABLED, create=False)
        if (enabled is not None and enabled.value() is not None
                and any(instance.graph.index == ULT_GAIN_GRAPH
                        for instance in self.component.instances.values())):
            return
        cost = expr.to_float(self.component.entity_var(ULT_COST).value())
        charge = expr.to_float(self.component.entity_var(ULT_CHARGE).value())
        if cost > 0 and charge < cost:
            step = f32(cost * self.frame_ms / (1000.0 * self.ult_seconds))
            self._gain_ult_points(cost, charge, step)

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
        if codec in ("value_slot", "linked_target", spray_placement.CODEC) and not payload:
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
        from ow174.game.script import charge

        charge.set_pending_credits(self.world, ())
        if hasattr(self.world, "scope_host"):
            self.world.scope_host.clear()
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


def create_weapon_graphs(component: runtime.Component, record: dict) -> list[runtime.Instance]:
    """Allocate an entity's declared manager/weapon slots before running any entries."""
    weapons = record.get("weapons") or []
    component.set(WEAPON_SLOTS, tuple(item is not None for item in weapons))
    component.set(DEFAULT_SLOT, next((slot for slot, item in enumerate(weapons) if item is not None), 0))
    created = []
    manager = graph.graph(record["manager"]) if record.get("manager") is not None else None
    if manager is not None:
        created.append(component.create(manager, init=False))
    for slot, number in enumerate(weapons):
        weapon = graph.graph(number) if number is not None else None
        if weapon is not None:
            created.append(component.create(weapon, values={(INSTANCE, 28): slot}, init=False))
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
