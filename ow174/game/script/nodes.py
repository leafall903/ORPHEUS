"""The node classes the runtime models: state classes (what they do at begin, end, on timers, button edges
and variable changes, what they give linked variables, and their owner-frame payload) and the actions and
conditions.

Semantics: read in the PC client (and the PS4 build where the PC code is obfuscated), field meanings
from the client's STU type objects (field hash -> offset, matched
to the offsets the class code reads). A state class not listed here is a plain State: it begins, follows
its plugs and stays active until something ends it, which is what the presentation classes (UXPresenter,
DataFlowMapping, Effect, ...) do on a server.
"""

import math
from dataclasses import dataclass
from types import NotImplementedType
from typing import ClassVar

from ow174.game.content import JUNKENSTEIN_MODES
from ow174.game.mover import CROUCHED, IN_AIR, JUMPED, NOT_GROUND_UNDER, Snapshot
from ow174.game.script import (
    animation_alias,
    array_actions,
    data_flow,
    effect_parameters,
    effect_routing,
    entity_control,
    expr,
    game_message,
    message_receiver,
    message_state,
    model_look,
    modifiers,
    play,
    scopes,
    shared_vars,
    spray_placement,
    voice_stimulus,
)
from ow174.game.script.expr import (
    EPSILON,
    Asset,
    Entity,
    Handle,
    Vec3,
    equal,
    f32,
    lvalue,
    round_half_away,
    stack_priority,
    to_float,
    to_int,
    truthy,
)
from ow174.game.script.graph import guid
from ow174.game.script.runtime import (
    ABORT,
    ASSIGN,
    GAME_MODE_ENTITY,
    STOP,
    TIMER,
    Component,
    Event,
    Instance,
    RaycastRequest,
    RaycastResult,
    State,
    Var,
    handler,
    send_message,
    state_class,
    would_stack_to_top,
)
from ow174.game.world import MAX_MOVEMENT_AXIS

MS = f32(0.001)  # 0.0010000000475, the client's float constant
REEVALUATE = 1  # BooleanSwitch / Switch / Watch timer param


def _field(state: State, name: str):
    return state.node.fields.get(name)


def shot_time(k: int, rate: float) -> int:
    """When shot k (0-based) leaves the gun, in ms after the volley start: R((k / rate) * 1000) in float32
    (PC 1.74, 0x7FF789C702E0)."""
    return round_half_away(f32(f32(k / rate) * 1000.0))


def seconds_to_ms(value) -> int:
    return round_half_away(f32(to_float(value) * 1000.0))


# --- states --------------------------------------------------------------------------------------


_NUMERIC_ROWS = {
    "STU_4CB0950F": (0, 0.0), "STU_D8CC6B10": (1, 1.0), "STU_56DEE1BB": (2, 0.0),
    "STU_C61711C8": (3, f32(3.4028234663852886e38)),
    "STU_F197CFBF": (4, f32(2 ** -126)), "STU_0A019882": (5, f32(2 ** -126)),
}


# 104319 resolves this fieldless state and its fieldless parent to the base
# state constructor at RVA 1B702C0. It carries no class-specific wire payload.
state_class("STU_90C3BCEA")(State)

# 104319 factory record 160 resolves this state through its parent. The
# native reader has no payload, so the authored base plugs own its lifecycle.
state_class("STU_7F488C3E")(State)

# Factory record 160 also selects base State for 0FAD550D. The PC runtime
# has no class-specific execution hook or synchronized payload for its field.
state_class("STU_0FAD550D")(State)

# The 104319 PC factory also selects record 160 for this authored state.
state_class("STU_F7216D4F")(State)

# Fieldless 0CBB branch inherits the registered base State.
state_class("STU_54BBC96C", strict_only=True)(State)

# The 104319 factory selects base State record 160 for F08E1453 through its
# parent. The runtime does not read the authored m_64C3E07E field.
state_class("STU_F08E1453", strict_only=True)(State)


_BOSS_BASE_STATES = {
    # The Junkrat body state inherits the registered PC base State.
    0x0033: {30: "STU_87621906"},
    0x0AAA: {
        2: "STU_200B27C5", 3: "STU_AF1A4047", 4: "STU_55F72184", 5: "STU_E834E718",
        10: "STU_632B74B9", 17: "STU_D3CDCF04", 24: "STU_D3CDCF04", 28: "STU_D52B9026",
        30: "STU_724DFBC6", 33: "STU_93088159",
    },
    0x0BC6: {
        6: "STU_632B74B9", 7: "STU_E834E718", 8: "STU_55F72184", 9: "STU_200B27C5",
        12: "STU_AF1A4047", 14: "STU_E0D2F3AA", 25: "STU_E0D2F3AA", 27: "STU_D52B9026",
        28: "STU_724DFBC6",
    },
    0x0CB5: {
        2: "STU_200B27C5", 3: "STU_E834E718", 10: "STU_55F72184", 12: "STU_D52B9026",
        15: "STU_B17E88C9", 16: "STU_632B74B9", 19: "STU_724DFBC6", 20: "STU_4531992F",
        31: "STU_724DFBC6",
    },
    0x0CBA: {28: "STU_93088159"},
    0x0CD2: {
        17: "STU_724DFBC6", 18: "STU_632B74B9", 19: "STU_B17E88C9", 21: "STU_D52B9026",
        22: "STU_55F72184", 23: "STU_E834E718", 24: "STU_200B27C5", 26: "STU_B17E88C9",
        27: "STU_724DFBC6", 29: "STU_724DFBC6", 30: "STU_E834E718", 31: "STU_55F72184",
        37: "STU_AF1A4047", 38: "STU_E0D2F3AA", 44: "STU_D3CDCF04", 49: "STU_D3CDCF04",
        51: "STU_724DFBC6", 52: "STU_200B27C5", 53: "STU_632B74B9",
    },
    0x0CE0: {6: "STU_93088159", 40: "STU_93088159", 42: "STU_14611C1B", 43: "STU_14611C1B"},
    0x0D03: {8: "STU_93088159", 10: "STU_93088159", 15: "STU_14611C1B", 16: "STU_14611C1B"},
    0x185D: {
        2: "STU_200B27C5", 3: "STU_E834E718", 8: "STU_55F72184", 15: "STU_D52B9026",
        22: "STU_B17E88C9", 23: "STU_632B74B9", 24: "STU_724DFBC6", 31: "STU_E0D2F3AA",
        34: "STU_724DFBC6", 35: "STU_632B74B9", 36: "STU_B17E88C9", 38: "STU_D52B9026",
        39: "STU_55F72184", 40: "STU_E834E718", 41: "STU_200B27C5", 46: "STU_AF1A4047",
        47: "STU_F4BBA185", 49: "STU_200B27C5", 53: "STU_93088159", 59: "STU_D3CDCF04",
        61: "STU_D3CDCF04",
    },
}


def _boss_base_state(instance, node):
    return (not node.server_only
            and _BOSS_BASE_STATES.get(instance.graph.index, {}).get(node.pos) == node.cls)


_COMMON_CONTROLLER_BASE_STATES = (
    "STU_6A6BBB8D", "STU_918B3FA9", "STU_14611C1B", "STU_200B27C5",
    "STU_D52B9026", "STU_4531992F", "STU_55F72184", "STU_632B74B9",
)


@state_class(
    *_COMMON_CONTROLLER_BASE_STATES,
    "STU_AF1A4047", "STU_E834E718", "STU_D3CDCF04", "STU_93088159", "STU_E0D2F3AA", "STU_B17E88C9",
    "STU_F4BBA185", "STU_87621906",
)
class JunkensteinControllerState(State):
    """The PC runtime's base-state lifecycle for authored Junkenstein placements."""

    def on_enter(self) -> None:
        instance, node = self.instance, self.node
        if instance.component.strict and not (
            (instance.graph.index == 0x0C87 and node.cls in _COMMON_CONTROLLER_BASE_STATES)
            or _boss_base_state(instance, node)
        ):
            instance.component.unsupported(
                instance, "controller state outside authored scope", node.cls, node)
        if instance.component.strict and instance.graph.index == 0x185D and node.pos in (59, 61):
            cast = getattr(instance.component.world, 'summoner_cast_button', None)
            if cast is None or cast(self) is NotImplemented:
                instance.component.unsupported(instance, 'Summoner cast input', node.cls, node)


@state_class("STU_724DFBC6", "STU_531AA630")
class JunkensteinBehaviorBranch(State):
    """The 104319 base State selected for the authored 0CF3 branch."""

    def on_enter(self):
        if (self.instance.component.strict and self.instance.graph.index != 0x0CF3
                and not _boss_base_state(self.instance, self.node)):
            self.instance.component.unsupported(
                self.instance, "behavior branch outside 0CF3", self.node.cls, self.node)


@state_class("STU_B7D2C9C0", strict_only=True)
class JunkensteinDisplayNameOverride(State):
    """Own the localized name override written to component 92 by 8E2EA0."""

    _WAVE_NAMES: ClassVar[dict[int, dict[int, int]]] = {
        0x0CBC: {20: 0x4CCC, 21: 0x4CCD, 22: 0x4CCE, 27: 0x4CD3, 109: 0x6ABF},
        0x0D1D: {6: 0x5768, 11: 0x5769, 73: 0x5E06},
    }

    def __init__(self, *args):
        super().__init__(*args)
        self.remove_override = None

    def on_enter(self) -> None:
        instance, node = self.instance, self.node
        component = instance.component
        graph_index = instance.graph.index
        name_index = self._WAVE_NAMES.get(graph_index, {}).get(node.pos)
        target_class = "STU_DB742151"
        if graph_index == 0x0CF3 and 21 <= node.pos <= 25:
            name_index = 0x7105 + node.pos - 21
            target_class = "STU_B5A0CAF0"
        name = expr.guid_of(node.field("m_overrideName"))
        target_cfg = node.field("m_14EDDDA7")
        if (name_index is None or name != (0x0DE0000000000000 | name_index)
                or not isinstance(target_cfg, dict) or target_cfg.get("$") != target_class):
            component.unsupported(instance, "display name override options", node.cls, node)
        target = self.evaluate(target_cfg, watch=False)
        if not isinstance(target, Entity):
            component.unsupported(instance, "display name override target", node.cls, node)
        if not expr.valid_entity(target.id) or component.world.entity_component92_present(target.id) is False:
            return
        remove = component.world.register_display_name_override(self, target.id, name)
        if remove is NotImplemented:
            component.unsupported(instance, "display name override target", node.cls, node)
        self.remove_override = remove

    def on_cleanup(self) -> None:
        remove, self.remove_override = self.remove_override, None
        if remove is not None:
            remove()

    on_failed = on_cleanup
    on_destroyed = on_cleanup


@state_class("STU_441B50E4", strict_only=True)
class JunkensteinPlayerStatTime(State):
    """Run the authored state lifecycle for a boss without player statistics."""

    def on_enter(self):
        instance, node = self.instance, self.node
        component = instance.component
        placement = instance.graph.index, node.pos
        ultimate_stat = {52: 0x0860000000000466, 66: 0x08600000000005BF}.get(node.pos)
        single = (instance.graph.index == 0x0B8B and ultimate_stat is not None and node.server_only
                  and expr.guid_of(node.field('m_stat')) == ultimate_stat
                  and node.field('m_D32CB484') is None)
        group = (placement == (0x01A3, 14) and not node.server_only
                 and node.field('m_stat') is None
                 and node.field('m_D32CB484') == ['0x086000000000037A'])
        if ((single or group) and node.field('m_stats') is None
                and self.evaluate(node.field('m_targets'), watch=False) == expr.Entity(component.entity)
                and component.world.player_stats_available(instance) is False):
            return
        component.unsupported(instance, 'player stat state outside the verified NPC scope', node.cls, node)


@state_class("STUStatescriptStateHealthPool", strict_only=True)
class AuthoredBodyHealthPool(State):
    """Validate authored base pools and own a Junkenstein wave's supplemental health."""

    def __init__(self, *args):
        super().__init__(*args)
        self.remove_wave_health = None

    def on_begin(self):
        instance = self.instance
        component = instance.component
        if not component.strict:
            return
        placement = (instance.graph.index, self.node.pos)
        common_body_kind = {1: 0, 3: 2, 8: 1}.get(self.node.pos) if placement[0] == 0x0033 else None
        wave = placement[0] == 0x0CBC and placement[1] in (1, 4, 5, 26, 43, 113)
        if common_body_kind is None and not wave and placement != (0x0C89, 5):
            component.unsupported(instance, "health pool state", self.node.cls, self.node)
        entity = expr.to_entity(play.evaluate_scalar(instance, _field(self, "m_FA3ECC43")))
        kind = _field(self, "m_7E8B666C")
        amount = play.evaluate_scalar(instance, _field(self, "m_amount"))
        expected_kind = common_body_kind if common_body_kind is not None else 0
        if (entity != component.entity or type(kind) is not int or kind != expected_kind
                or not _finite_number(amount)):
            component.unsupported(instance, "authored body health pool capacity", self.node.cls, self.node)
        capacity = component.world.health_pool_capacity(entity, kind)
        if common_body_kind is not None:
            initial_cfg = _field(self, "m_BCFDD2E9")
            initial = amount if initial_cfg is None else play.evaluate_scalar(instance, initial_cfg)
            # The allocator starts the pool full. An authored initial amount which differs from
            # capacity needs the actual mutation path, even if a pool of that capacity exists.
            if amount <= 0 or capacity != amount or not _finite_number(initial) or initial != amount:
                component.unsupported(
                    instance, "common body health pool initialization", self.node.cls, self.node)
        elif wave:
            difficulty = component.world.entity_var(GAME_MODE_ENTITY, 9948)
            if (type(difficulty) is not int or not 0 <= difficulty <= 3 or amount < 0
                    or not _finite_number(capacity) or capacity <= 0):
                component.unsupported(instance, "wave health modifier", self.node.cls, self.node)
            if placement == (0x0CBC, 43) and difficulty == 0 and amount == 0:
                return
            selected = instance.find_var(expr.INSTANCE, 9948, create=False)
            if (amount == 0 or selected is None or selected.value() != difficulty
                    or any(self.node.field(key) is not None for key in (
                        "m_7BE02F89", "m_BCFDD2E9", "m_4895B8B6", "m_CEAB19E8", "m_C99FAFDD",
                        "m_FF79E783", "m_AF1104D0", "m_BFA825EA"))):
                component.unsupported(instance, "wave health modifier options", self.node.cls, self.node)
            remove = component.world.add_wave_health(self, amount)
            if remove is NotImplemented:
                component.unsupported(instance, "wave health modifier service", self.node.cls, self.node)
            self.remove_wave_health = remove
        elif amount <= 0 or capacity != amount:
            component.unsupported(instance, "authored body health pool capacity", self.node.cls, self.node)

    def on_cleanup(self):
        remove, self.remove_wave_health = self.remove_wave_health, None
        if remove is not None:
            remove()

    on_failed = on_cleanup
    on_destroyed = on_cleanup


@state_class("STU_E56BA8D8", strict_only=True)
class EncounterTargetTag(State):
    """Publish a tag on the actor while the authored state is active."""

    tags = {
        (0x0234, 53): 0x0D60000000000422,
        (0x01C7, 96): 0x0D60000000000422,
        (0x006B, 29): 0x0D60000000000039,
    }

    def on_begin(self):
        instance = self.instance
        component = instance.component
        expected = self.tags.get((instance.graph.index, self.node.pos))
        if expected is None or _field(self, "m_44D1BE94") is not None:
            component.unsupported(instance, "encounter target tag options", self.node.cls, self.node)
        target = self.evaluate(_field(self, "m_31714AFC"), watch=False)
        tag = self.evaluate(_field(self, "m_targetTags"), watch=False)
        if (target != Entity(component.entity) or tag != Asset(expected)
                or component.world.grant_target_tag(self, tag.guid) is not True):
            component.unsupported(instance, "encounter target tag service", self.node.cls, self.node)

    def on_cleanup(self):
        self.instance.component.world.clear_target_tag(self)

    on_failed = on_cleanup



@state_class("STU_DA41541E", "STU_760F68AF", "STU_988AAC99", strict_only=True)
class SpawnSearchBranch(State):
    """The 104319 base State used by the three 0CBA candidate children."""

    def on_enter(self):
        component = self.instance.component
        if self.instance.graph.index != 0x0CBA:
            component.unsupported(self.instance, "spawn search branch outside 0CBA", self.node.cls, self.node)


@state_class("STU_DC80F7E6", strict_only=True)
class OutlineState(State):
    """AC0470 exits before reading targets or rows without the 978A30 context."""

    def on_enter(self):
        component = self.instance.component
        if component.strict and component.world.outline_context_available is not False:
            component.unsupported(self.instance, "outline context", self.node.cls, self.node)


@state_class("STU_B5C87F3D", strict_only=True)
class LocalCombatAccumulator(State):
    """8EF100 skips tick registration when 979210 cannot resolve the local context."""

    def on_enter(self):
        component = self.instance.component
        if component.world.local_combat_context_available is not False:
            component.unsupported(self.instance, "local combat context", self.node.cls, self.node)


@state_class("STU_76F3EAEF", strict_only=True)
class JunkensteinWaveContinuation(State):
    """104319 record 160 selects the base State for these wave continuations."""

    def on_enter(self):
        if self.instance.graph.index != 0x0CBC or self.node.pos not in (60, 61, 62, 63, 64, 110):
            self.instance.component.unsupported(self.instance, "wave state", self.node.cls, self.node)


@state_class("STU_6CC90892", strict_only=True)
class JunkensteinTeamContact(State):
    """D294C0/D297C0 disable and restore the mover's same-team contact bit."""

    def __init__(self, *args):
        super().__init__(*args)
        self.restore_contact = None

    def on_enter(self) -> None:
        instance, node = self.instance, self.node
        component = instance.component
        target = self.evaluate(node.field("m_D7AC2990"), watch=False)
        if (instance.graph.index, node.pos) != (0x0CBC, 129) or target != Entity(component.entity):
            component.unsupported(instance, "team contact state options", node.cls, node)
        restore = component.world.suppress_same_team_contact(self, target.id)
        if restore is NotImplemented:
            component.unsupported(instance, "team contact mover", node.cls, node)
        self.restore_contact = restore

    def on_cleanup(self) -> None:
        restore, self.restore_contact = self.restore_contact, None
        if restore is not None:
            restore()

    on_failed = on_cleanup
    on_destroyed = on_cleanup


@state_class("STU_D9843704", strict_only=True)
class JunkensteinBodyContinuation(State):
    """The authored 08B6 and 0CB7 states inherit 104319's base lifecycle."""

    def on_enter(self) -> None:
        instance, node = self.instance, self.node
        expected = {(0x08B6, 20): 0, (0x0CB7, 3): 20}.get((instance.graph.index, node.pos))
        if expected is None or self.evaluate(node.field("m_0B528B1E"), watch=False) != expected:
            instance.component.unsupported(instance, "Junkenstein body continuation", node.cls, node)


@state_class('STU_875C9635', 'STU_0DB9E66B', strict_only=True)
class BaseStateContinuation(State):
    """Continue authored state lifetimes through the 104319 base State factory."""

    def on_enter(self) -> None:
        instance, node = self.instance, self.node
        expected = {
            (0x01C9, 64): 'STU_875C9635',
            (0x01C9, 65): 'STU_0DB9E66B',
            (0x006B, 38): 'STU_0DB9E66B',
            (0x0079, 42): 'STU_0DB9E66B',
        }.get((instance.graph.index, node.pos))
        if expected != node.cls:
            instance.component.unsupported(instance, 'base state continuation', node.cls, node)


@state_class('STU_4773DA56', strict_only=True)
class HookVictimRegistration(State):
    """9B0EF0/9B11A0 add and remove this state identity on the victim body."""

    def on_enter(self) -> None:
        instance, node = self.instance, self.node
        component = instance.component
        if (instance.graph.index, node.pos) != (0x01C9, 22):
            component.unsupported(instance, 'hook victim registration', node.cls, node)
        component.state_registries.setdefault(node.cls, []).append(self)

    def on_cleanup(self) -> None:
        registries = self.instance.component.state_registries
        states = registries.get(self.node.cls)
        if states is not None and self in states:
            states.remove(self)
            if not states:
                del registries[self.node.cls]

    on_destroyed = on_cleanup


@state_class("STU_F5A99B2A", strict_only=True)
class TipsState(State):
    """9FD280 and 9FD300 return when the selected tips service is absent."""

    def on_enter(self):
        component = self.instance.component
        if component.world.tips_context_available is not False:
            component.unsupported(self.instance, "tips context", self.node.cls, self.node)


@state_class("STUStatescriptStateUXPresenter", "STU_E13B30A8", strict_only=True)
class UXPresenter(State):
    """Keep the State lifecycle when the host has no UI presentation consumer."""

    def on_enter(self):
        component = self.instance.component
        if component.world.ux_presenter_available is not False:
            component.unsupported(self.instance, "UX presenter context", self.node.cls, self.node)


@state_class("STU_BD02E168")
class ScopedSubgraph(State):
    """The +1C0 interface enters/exits +E8, separately from ordinary state hooks.

    The component owns dispatch timing. Strict execution requires an explicit
    registration service or a declaration that the component does not register
    this interface. The native game/library component's +198 slot is a nullsub;
    that observation is not made an implicit authoritative-host policy here.
    """

    def __init__(self, *args):
        super().__init__(*args)
        self.scope_registry = None

    def on_created(self):
        component = self.instance.component
        if not component.strict:
            return
        registry = component.world.subgraph_scopes(component.entity)
        if registry is not None and type(registry) not in (scopes.Registry, scopes.ForwardHost):
            component.unsupported(self.instance, "subgraph scope registration", self.node.cls, self.node)
        if registry is not None:
            registry.register(self)
            self.scope_registry = registry

    def on_destroyed(self):
        if self.scope_registry is not None:
            if type(self.scope_registry) is scopes.ForwardHost:
                self.scope_registry.discard(self)
            else:
                self.scope_registry.unregister(self)
            self.scope_registry = None

    def on_beginning(self):
        if type(self.scope_registry) is scopes.ForwardHost:
            self.scope_registry.record(self, True)

    def on_ending(self):
        if (type(self.scope_registry) is scopes.ForwardHost
                and not self.instance.teardown and not self.instance.stopped):
            self.scope_registry.record(self, False)

    def scope_enter(self):
        self.instance.follow(self.node, "m_5B10D01F", self)

    def scope_exit(self):
        self.instance.exit_subgraph(self.node, "m_5B10D01F")


@state_class("STU_582E0D5F")
class AuxiliaryEffect(ScopedSubgraph):
    """Separate scope/observer callbacks; ordinary State hooks stay inherited.

    Native target routing and descriptor initialization are implemented against
    explicit world inputs. Engine consumers, orientation return and native
    frame/reconciliation scheduling are not inferred from State begin/end.
    """

    def __init__(self, *args):
        super().__init__(*args)
        self.effect = None
        self.effect_list = None
        self.owned_effects = []  # Local failure/retirement containment, not native wire storage.

    def context(self):
        component = self.instance.component
        context = component.world.auxiliary_effect_context(component.entity)
        if context is not None and type(context) is not effect_routing.Context:
            component.unsupported(self.instance, "auxiliary effect context", self.node.cls, self.node)
        return context

    def _check_live(self):
        instance = self.instance
        if (instance.stopped or instance.teardown
                or instance.component.instances.get(instance.id) is not instance):
            raise ValueError("auxiliary effect callback needs its live instance")

    def scope_enter(self):
        with self.instance.component.operation():
            self._check_live()
            context = self.context()
            if context is None:  # 9D1C80's engine-context guard precedes target evaluation.
                return
            selected = effect_routing.select(
                self, context, self.instance.component.world.auxiliary_effect_target,
            )
            if selected is None:
                return
            if type(context.enabled) is not bool:
                self.instance.component.unsupported(
                    self.instance, "auxiliary effect capability", self.node.cls, self.node,
                )
            effect = selected.entries.allocate(
                context=selected.context, target=selected.target, observer=self,
            )
            self.owned_effects.append((selected.entries, effect))
            self.effect, self.effect_list = effect, selected.entries
            effect_parameters.initialize(self, effect.parameters, context_enabled=context.enabled)

    def refresh_effect(self):
        with self.instance.component.operation():
            self._check_live()
            if self.effect is None:
                return
            context = self.context()
            if context is None or type(context.enabled) is not bool:
                self.instance.component.unsupported(
                    self.instance, "auxiliary effect capability", self.node.cls, self.node,
                )
            effect_parameters.refresh(self, self.effect.parameters, context_enabled=context.enabled)

    def scope_exit(self):
        with self.instance.component.operation():
            self._check_live()
            effect = self.effect
            if effect is None:
                return
            values = effect.parameters.values
            values[0xC4] = effect_parameters._number(self, "m_F5C94645", 0.5)
            values[0xC8] = effect_parameters._number(self, "m_63FFE8DF", 0.5)
            if self.context() is None:
                self.instance.component.unsupported(
                    self.instance, "auxiliary effect retirement context", self.node.cls, self.node,
                )
            if expr.valid_entity(effect.target) and values[0x2E8]:
                self.instance.component.unsupported(
                    self.instance, "auxiliary effect orientation cleanup", self.node.cls, self.node,
                )
            # 9D2630 clears the observer before list retirement and the current pointer.
            effect.observer = None
            self.effect_list.retire(effect)
            self.on_effect_abandoned(effect)

    def effect_lost(self, effect):
        # +210 observer9D1D20 only releases its matching current object.
        with self.instance.component.operation():
            self._check_live()
            if self.effect is not None and self.effect is effect:
                self.scope_exit()

    def on_effect_abandoned(self, effect):
        self.owned_effects = [(owner, item) for owner, item in self.owned_effects if item is not effect]
        if self.effect is effect:
            self.effect = self.effect_list = None

    def on_failed(self):
        for owner, effect in self.owned_effects:
            effect.observer = None
            owner.retire(effect)
        self.owned_effects.clear()
        self.effect = self.effect_list = None

    def on_destroyed(self):
        self.on_failed()
        super().on_destroyed()


@state_class("STU_316CFEF2")
class NumericPropertyModifiers(State):
    """D39590/D399C0/D3C1E0: numeric rows on an explicit component-31 backend.

    The backend clock is native component +528/vt48's frame, not an assumed
    alias of Component.frame. Typed-value and filter rows remain strict errors.
    Legacy body prediction keeps its previous passive behavior.
    """

    def __init__(self, *args):
        super().__init__(*args)
        self.per_frame = False

    def backend(self):
        component = self.instance.component
        backend = component.world.property_modifiers(component.entity)
        if backend is not None and type(backend) is not modifiers.NumericModifiers:
            component.unsupported(self.instance, "property modifier component", self.node.cls, self.node)
        return backend

    def rows(self):
        rows = _field(self, "m_BC5E91CF") or ()
        if not isinstance(rows, (list, tuple)):
            self.instance.component.unsupported(
                self.instance, "numeric property modifier rows", self.node.cls, self.node,
            )
        for row in rows:
            if row is None:
                continue
            if (not isinstance(row, dict) or row.get("$") not in _NUMERIC_ROWS
                    or type(row.get("m_A9561CA0", 0)) is not int
                    or not 0 <= row.get("m_A9561CA0", 0) <= 0xFFFFFFFF):
                self.instance.component.unsupported(
                    self.instance, "numeric property modifier row", str(row.get("$")
                    if isinstance(row, dict) else type(row)), self.node,
                )
        return rows

    def number(self, cfg, default):
        failed = object()
        value = play.evaluate_scalar(self.instance, cfg, initial=failed, watcher=self)
        return default if value is failed else f32(expr.to_float(value, default))

    def on_begin(self):
        if not self.instance.component.strict:
            return
        value = play.evaluate_scalar(self.instance, _field(self, "m_485109CB"), initial=False, watcher=self)
        self.per_frame = truthy(value)
        self.publish()

    def publish(self):
        if not self.instance.component.strict:
            return
        backend = self.backend()
        if backend is None:
            if self in self.instance.ticking:
                self.instance.ticking.remove(self)
            return
        rows = self.rows()
        frame = backend.source_frame
        offset = frame * len(rows) if self.per_frame else 0
        for index, row in enumerate(rows):
            self.instance.component.consume_work("numeric property modifier")
            if row is None:
                continue
            operation, default = _NUMERIC_ROWS[row["$"]]
            priority = self.number(row.get("m_priority"), 0.0)
            upper = self.number(row.get("m_8213EB22"), f32(3.4028234663852886e38)) if operation == 5 else 0.0
            value = self.number(row.get("m_8AD739CE" if operation == 5 else "m_9B7A63EA"), default)
            key = modifiers.state_key(self.instance.id, self.index, self.counter, index, offset)
            backend.upsert(modifiers.Record(1, key, (frame + 1) & 0xFFFFFFFF,
                row.get("m_A9561CA0", 0), operation, value, priority, upper))
            if self.per_frame:
                backend.retire(1, key, (frame + 2) & 0xFFFFFFFF)
        if self.per_frame:
            self.ticks()

    def dependency_changed(self):
        self.publish()

    def tick(self):
        self.publish()

    def on_cleanup(self):
        if not self.instance.component.strict:
            return
        backend = self.backend()
        if backend is None:
            return
        rows = self.rows()
        frame = backend.source_frame
        offset = frame * len(rows) if self.per_frame else 0
        for index, row in enumerate(rows):
            if row is not None:
                key = modifiers.state_key(self.instance.id, self.index, self.counter, index, offset)
                backend.retire(1, key, (frame + 1) & 0xFFFFFFFF)


@state_class("STU_43994F1B", "STU_93B8758C", strict_only=True)
@state_class("STU_9028E035", "STU_25CBEFB0")
class RegisteredState(State):
    """1B72530/1B725C0: the local authoritative linked-state lifecycle.

    The 104319 classes share the execution hooks and link codec. Lists
    remain separate by authored class, irrespective of class-specific fields.
    Prediction-mode registration and incoming link reconciliation are separate
    client services, not simulated by this server's begin/end requests.
    """

    def on_begin(self) -> None:
        self.instance.component.state_registries.setdefault(self.node.cls, []).append(self)

    def on_cleanup(self) -> None:
        self.instance.component.state_registries[self.node.cls].remove(self)

    def payload(self) -> dict:
        # 1B72A80 skips +C1, copied from authored +7B (m_serverOnly) by 1B91460.
        states = self.instance.component.state_registries.get(self.node.cls, ())
        if self not in states:
            return {}
        for following in states[states.index(self) + 1:]:
            if not following.node.server_only:
                return {"link": (following.instance.id, following.index)}
        return {}


@state_class("STU_9935FBC8", strict_only=True)
class CinematicAimLock(RegisteredState):
    """Capture the zero-width yaw and pitch limits in Junkenstein cinematics."""

    def __init__(self, *args):
        super().__init__(*args)
        self.aim = None

    def on_begin(self):
        instance, node = self.instance, self.node
        component = instance.component
        backend = component.world.property_modifiers(component.entity)
        if ((instance.graph.index, node.pos) not in {(0x01CB, 12), (0x0CE0, 45), (0x0CA5, 70), (0x00CB, 21)}
                or node.field("m_93CDFD2F") != 1
                or node.field("m_4BE66EFF") is not None
                or type(backend) is not modifiers.NumericModifiers):
            component.unsupported(instance, "cinematic aim lock placement", node.cls, node)
        for field in ("m_C33684DE", "m_8FA7FF85", "m_D6119DFB", "m_83221709"):
            if self.evaluate(node.field(field), watch=False) != 0:
                component.unsupported(instance, "cinematic aim lock bounds", node.cls, node)
        yaw = self.evaluate(node.field("m_A53161C8"), watch=False)
        pitch = self.evaluate(node.field("m_AB37A892"), watch=False)
        if any(type(angle) not in (float, int) or not math.isfinite(angle) for angle in (yaw, pitch)):
            component.unsupported(instance, "cinematic aim lock pose", node.cls, node)
        self.aim = tuple((round(angle * 65536 / 360) + 32768) % 65536 - 32768 for angle in (yaw, pitch))
        super().on_begin()

    def payload(self):
        return {"link": super().payload().get("link")}


@state_class("STU_1B93D61B")
class SceneColorState(RegisteredState):
    """Native type-list lifecycle. The renderer callback has no headless consumer."""

    def on_begin(self) -> None:
        if self.instance.component.strict:
            super().on_begin()

    def on_cleanup(self) -> None:
        if self.instance.component.strict:
            super().on_cleanup()


@state_class("STU_33A46033")
class EntityControl(RegisteredState):
    """Native cached type-list rules; engine services require an explicit context.

    Strict begin/cleanup keep the native registration order and refresh consumers.
    Legacy permissive bodies retain their passive behavior. Incoming prediction
    reconciliation and the actual engine implementations remain owner services.
    """

    def __init__(self, *args):
        super().__init__(*args)
        self.control_radius = 0.0
        self.control_bypass = False
        self.control_inverse_a = self.control_inverse_b = False
        self.control_targets = ()
        self.control_previous = False
        self.control_generation = 0

    def on_begin(self):
        if self.instance.component.strict:
            super().on_begin()
            entity_control.refresh(self)

    def on_cleanup(self):
        if self.instance.component.strict:
            super().on_cleanup()
            entity_control.refresh(self)

    def tick(self):
        if self.instance.component.strict:
            entity_control.refresh(self)

    def dependency_changed(self):
        if self.instance.component.strict:
            entity_control.refresh(self)

    def payload(self):
        return super().payload() if self.instance.component.strict else {}


@state_class("STU_9D7BF987")
class SharedVariables(State):
    """Declarative sync variables with the verified common State allocation/lifecycle."""

    def on_created(self):
        if self.instance.component.strict:
            shared_vars.validate(self.instance, self.node)


@state_class("STU_353AF1A9")
class VoiceSetOverride(State):
    """Owner-targeted voice-set override on a declared scene component."""

    def on_begin(self) -> None:
        component = self.instance.component
        if not component.strict:
            return
        target = self.evaluate(_field(self, 'm_target'), watch=False)
        definition = self.evaluate(_field(self, 'm_DA4846A5'), watch=False)
        if (target != Entity(component.entity) or not isinstance(definition, Asset)
                or definition.guid >> 48 != 0x07A0
                or component.world.apply_voice_set(self, definition.guid) is not True):
            component.unsupported(self.instance, 'owner voice set override', self.node.cls, self.node)

    def on_cleanup(self) -> None:
        self.instance.component.world.clear_voice_set(self)

    on_failed = on_cleanup
    on_destroyed = on_cleanup


@state_class("STU_40E05F6B")
class VoiceStimulus(State):
    """Shared voice begin/poll/timer callbacks; legacy spawn barks remain passive."""

    def on_created(self):
        self.voice_handle = 0
        for name in voice_stimulus.CACHES[1:]:
            setattr(self, name, None)  # Native constructor leaves these uninitialized.

    def on_begin(self):
        if self.instance.component.strict:
            voice_stimulus.begin(self)
        else:
            self.instance.component.world.voice_stimulus_began(self)

    def poll_voice(self):
        if self.instance.component.strict:
            voice_stimulus.update(self)

    def timer(self, param):
        if self.instance.component.strict:
            voice_stimulus.timer(self, param)

    def on_cleanup(self):
        if self.instance.component.strict:
            voice_stimulus.detach(self)

    def on_failed(self):
        if self.instance.component.strict:
            voice_stimulus.detach(self)

    def on_destroyed(self):
        if self.instance.component.strict:
            voice_stimulus.detach(self)

    def follow_end_plugs(self, finished):
        if self.instance.component.strict:
            # +A0/+A8/+B0 are passive. The same-named +160 voice plug is
            # dispatched by timer1/2, not by native state completion.
            self.instance.follow(self.node, 'm_onEndPlug', self)
        else:
            super().follow_end_plugs(finished)


@state_class("STU_B1FA9C2A")
class AnimationAlias(State):
    """Native component55 requests; engine/allocator services stay explicit."""

    def __init__(self, *args):
        super().__init__(*args)
        self.alias_handle = 0

    def on_begin(self):
        if self.instance.component.strict:
            animation_alias.begin(self)

    def on_cleanup(self):
        if self.instance.component.strict:
            animation_alias.cleanup(self)

    def on_failed(self):
        self.alias_handle = 0


@state_class("STU_7D71FDF3")
class ModelLook(ScopedSubgraph):
    """Native +1C0 scope enter/exit and +70 tick; begin/end stay separate."""

    def __init__(self, *args):
        super().__init__(*args)
        self.look = 0
        self.look_target = model_look.INVALID_TARGET

    def scope_enter(self):
        if self.instance.component.strict:
            model_look.enter(self)

    def scope_exit(self):
        if self.instance.component.strict:
            model_look.exit(self)

    def tick(self):
        if self.instance.component.strict:
            model_look.refresh(self)


@state_class("STU_BF916E6E")
class GlobalDataFlowValue(State):
    """The literal provider used by the Junkenstein mission root."""

    def on_begin(self) -> None:
        component = self.instance.component
        if not component.strict:
            return
        rows = self.node.field('m_values')
        if (self.instance.graph.index != 0x0C8C or not isinstance(rows, list) or len(rows) != 1
                or not isinstance(rows[0], dict) or rows[0].get('$') != 'STUGlobalDataFlowValue'):
            component.unsupported(self.instance, 'global data-flow rows', self.node.cls, self.node)
        row = rows[0]
        answer = row.get('m_answer')
        resource = self.evaluate(row.get('m_5A6D4FBD'), watch=False)
        if (resource != Asset(0x04800000000005F2) or not isinstance(answer, dict)
                or answer.get('$') != 'STUConfigVarBool'):
            component.unsupported(self.instance, 'global data-flow value', self.node.cls, self.node)
        if component.world.set_global_data_flow(self, resource.guid, self.evaluate(answer)) is not True:
            component.unsupported(self.instance, 'global data-flow provider', self.node.cls, self.node)

    def on_cleanup(self) -> None:
        self.instance.component.world.clear_global_data_flow(self)

    on_failed = on_cleanup
    on_destroyed = on_cleanup


@state_class("STUStatescriptStateDataFlowMapping")
class DataFlowMapping(State):
    """Native ordinary hooks and a separate typed answer-provider interface."""

    def __init__(self, *args):
        super().__init__(*args)
        self.mapping = 0
        self.mapping_target = None  # Native constructor does not initialize +1D0.
        self.mapping_self = False

    def on_begin(self):
        if self.instance.component.strict:
            data_flow.begin(self)

    def dependency_changed(self):
        if self.instance.component.strict:
            data_flow.refresh(self)

    def on_cleanup(self):
        if self.instance.component.strict:
            data_flow.cleanup(self)

    def answer(self, kind):
        return data_flow.answer(self, kind)


@dataclass(frozen=True)
class AIControlRecord:
    kind: int
    limits: tuple[float, float] | None
    priority: float
    prefer_later: bool


@dataclass(frozen=True)
class AIWeaponRecord:
    slot: int
    identifier: int
    flag: bool
    priority: float
    prefer_later: bool


@dataclass
class AIFacingRecord:
    direction: Vec3
    priority: float
    prefer_later: bool


@state_class("STU_FC16FF45", strict_only=True)
class JunkensteinAIFacing(State):
    """Own a direction record in component 86's separate BC2E40 list."""

    def __init__(self, *args):
        super().__init__(*args)
        self.record = self.remove_record = None

    def on_enter(self) -> None:
        instance, node = self.instance, self.node
        component = instance.component
        priority_asset = play._asset(play.evaluate_scalar(instance, node.field("m_priority")))
        if ((instance.graph.index, node.pos) not in ((0x0C95, 5), (0x185D, 57))
                or node.field("m_baseEntity") is not None
                or priority_asset != 0x0C40000000000003):
            component.unsupported(instance, "AI facing state options", node.cls, node)
        target = expr.to_entity(play.evaluate_scalar(instance, node.field("m_00D49978"))) & 0xFFFFFFFF
        priority, prefer_later = expr.STACK_PRIORITIES[priority_asset & 0xFFFFFFFF]
        record = AIFacingRecord(self._direction(), priority, prefer_later)
        remove = component.world.register_ai_facing_record(self, target, record)
        if remove is NotImplemented:
            component.unsupported(instance, "AI facing component", node.cls, node)
        if remove is None:
            self.request(ABORT)
            return
        self.record, self.remove_record = record, remove
        self.ticks()

    def _direction(self) -> Vec3:
        direction = self.evaluate(self.node.field("m_7CCD8B4B"), watch=False)
        if not _finite_vector(direction):
            self.instance.component.unsupported(
                self.instance, "AI facing direction", self.node.cls, self.node)
        return direction

    def tick(self) -> None:
        self.record.direction = self._direction()

    def on_cleanup(self) -> None:
        remove, self.remove_record = self.remove_record, None
        self.record = None
        if remove is not None:
            remove()

    on_failed = on_cleanup
    on_destroyed = on_cleanup


@state_class("STU_473549CA", "STU_CF8C7CC5", "STU_066B4605")
class AIControlOverride(State):
    """Own a record in the target component 86's two separate override lists."""

    def __init__(self, *args):
        super().__init__(*args)
        self.remove_record = None

    def on_begin(self):
        instance = self.instance
        component = instance.component
        if not component.strict:
            return
        priority_cfg = _field(self, "m_priority")
        priority, prefer_later = 9999.0, False
        if priority_cfg is not None:
            priority_asset = play._asset(play.evaluate_scalar(instance, priority_cfg))
            if priority_asset >> 48 != 0x0C40 or (priority_asset & 0xFFFFFFFF) not in expr.STACK_PRIORITIES:
                component.unsupported(instance, "AI control priority resource", self.node.cls, self.node)
            priority, prefer_later = expr.STACK_PRIORITIES[priority_asset & 0xFFFFFFFF]
        target = expr.to_entity(play.evaluate_scalar(instance, _field(self, "m_00D49978"))) & 0xFFFFFFFF
        if self.node.cls == "STU_066B4605":
            slot_cfg = _field(self, "m_D66E7D68")
            slot_value = play.evaluate_scalar(instance, slot_cfg, initial=9999)
            slot = 9999 if slot_value is None else to_int(slot_value, 9999)
            identifier = play._asset(play.evaluate_scalar(instance, _field(self, "m_F2810377")))
            flag = truthy(play.evaluate_scalar(instance, _field(self, "m_F8585ABF")))
            record = AIWeaponRecord(slot, identifier, flag, priority, prefer_later)
            self.remove_record = component.world.register_ai_weapon_record(self, target, record)
            if self.remove_record is NotImplemented:
                self.remove_record = None
                component.unsupported(instance, "AI weapon control component", self.node.cls, self.node)
            return
        limits = None
        if self.node.cls == "STU_473549CA":
            default = f32(math.pi)
            limits = tuple(
                to_float(play.evaluate_scalar(instance, _field(self, key), initial=default), default)
                if _field(self, key) is not None else default
                for key in ("m_49EEBB65", "m_FD4D6C19")
            )
        record = AIControlRecord(1 if limits is not None else 2, limits, priority, prefer_later)
        remove = component.world.register_ai_control_record(self, target, record)
        if remove is NotImplemented:
            component.unsupported(instance, "AI control component", self.node.cls, self.node)
        self.remove_record = remove

    def on_cleanup(self):
        remove, self.remove_record = self.remove_record, None
        if remove is not None:
            remove()

    on_failed = on_cleanup


@state_class("STU_97B1B3EC")
class AIControlSetting(State):
    """C83560 writes a scalar and flag directly on the owner's component 86."""

    def on_begin(self):
        self.dependency_changed()

    def dependency_changed(self):
        instance = self.instance
        component = instance.component
        if not component.strict:
            return
        value = play.evaluate_scalar(instance, _field(self, "m_0468C6D4"), watcher=self)
        flag = play.evaluate_scalar(instance, _field(self, "m_D6E83CD4"), watcher=self)
        result = component.world.set_ai_control_setting(self, max(f32(0.1), to_float(value)), truthy(flag))
        if result is NotImplemented:
            component.unsupported(instance, "AI control setting component", self.node.cls, self.node)

    def on_cleanup(self):
        if self.instance.component.strict:
            self.instance.component.world.set_ai_control_setting(self, 5.0, False)

    on_failed = on_cleanup


@dataclass
class AIBehaviorRequest:
    kind: str
    priority: float
    prefer_later: bool
    shared: tuple[bool, float, float]
    inputs: tuple[float, ...]


@state_class("STU_B61E6681", "STU_CFA338CB", "STU_600E3C59")
class AIBehaviorState(State):
    """Maintain the component 88 request owned by this state."""

    def __init__(self, *args):
        super().__init__(*args)
        self.record = None
        self.remove_record = None

    def _inputs(self):
        def value(key, default=0.0):
            result = play.evaluate_scalar(self.instance, _field(self, key), initial=default, watcher=self)
            return default if result is None else result

        shared = (truthy(value("m_88881C38")), to_float(value("m_36998F8A")), to_float(value("m_44064F45")))
        if self.node.cls == "STU_B61E6681":
            inputs = (to_float(value("m_203D6B00")), to_float(value("m_weight")))
        elif self.node.cls == "STU_CFA338CB":
            inputs = (to_float(value("m_DC8A3A89")), to_float(value("m_503D6044")),
                      f32(to_float(value("m_157D72D1")) * f32(math.pi / 180)))
        else:
            interval = {0: 1 / 60, 1: 0.05, 2: 0.1, 4: 0.5}.get(_field(self, "m_DD792FF0"), 0.2)
            inputs = (to_float(value("m_D655C99A", 2.0)), to_float(value("m_width", 2.0)),
                      to_float(value("m_weight")), f32(interval), to_float(value("m_B802CBBA", 1.0)))
        return shared, inputs

    def on_begin(self):
        instance = self.instance
        component = instance.component
        if not component.strict:
            return
        cfg = _field(self, "m_priority")
        priority, prefer_later = 0.0, False
        if cfg is not None:
            asset = play._asset(play.evaluate_scalar(instance, cfg))
            if asset >> 48 != 0x0C40 or (asset & 0xFFFFFFFF) not in expr.STACK_PRIORITIES:
                component.unsupported(instance, "AI behavior priority resource", self.node.cls, self.node)
            priority, prefer_later = expr.STACK_PRIORITIES[asset & 0xFFFFFFFF]
        record = AIBehaviorRequest(self.node.cls, priority, prefer_later, *self._inputs())
        remove = component.world.register_ai_behavior_request(self, record)
        if remove is NotImplemented:
            component.unsupported(instance, "AI behavior component", self.node.cls, self.node)
        if remove is not None:
            self.record, self.remove_record = record, remove

    def dependency_changed(self):
        if self.record is not None:
            self.record.shared, self.record.inputs = self._inputs()

    def on_cleanup(self):
        self.record = None
        remove, self.remove_record = self.remove_record, None
        if remove is not None:
            remove()

    on_failed = on_cleanup


@dataclass(frozen=True)
class SpawnSearchRequest:
    position: Vec3
    direction: Vec3
    radius: float
    half_angle: float
    min_candidates: int
    max_candidates: int
    search_scale: float
    flag: bool
    extra: int


@state_class("STU_BA2FCDEB")
class SpawnSearch(State):
    """Submit C82B40 inputs and wait for the host's buffered positions."""

    def __init__(self, *args):
        super().__init__(*args)
        self.pending = None
        self.cancel_search = None
        self.positions = None

    def on_begin(self):
        instance = self.instance
        component = instance.component
        if not component.strict:
            return
        if (lvalue(_field(self, "m_8FEA3023")) is None
                or any(_field(self, key) is not None for key in ("m_priority", "m_2B717EC8", "m_1BCC45B9"))
                or self.node.links("m_7D31C943")):
            component.unsupported(instance, "spawn search configuration", self.node.cls, self.node)

        def scalar(key, default):
            value = instance.evaluate(_field(self, key), initial=default)
            return default if value is None else value

        position = scalar("m_position", Vec3(0, 0, 0))
        if isinstance(position, Entity):
            position = component.world.position(position.id)
        direction = scalar("m_ADC02DA1", Vec3(0, 0, 0))
        if not _finite_vector(position) or not _finite_vector(direction):
            component.unsupported(instance, "spawn search transform", self.node.cls, self.node)
        length = math.hypot(*direction)
        if length:
            direction = Vec3(*(f32(axis / length) for axis in direction))
        minimum = max(1, to_int(scalar("m_EC1B83C7", 1), 1))
        request = SpawnSearchRequest(
            position, direction,
            min(1_000_000.0, to_float(scalar("m_radius", 1_000_000.0), 1_000_000.0)),
            f32(to_float(scalar("m_A30B383E", 360.0), 360.0) * f32(math.pi / 360)),
            minimum, max(minimum, to_int(scalar("m_7084DA43", 1), 1)),
            max(0.5, to_float(scalar("m_F649CC6C", 1.0), 1.0)),
            truthy(scalar("m_DAD5978F", False)), to_int(scalar("m_7762F430", -1), -1),
        )
        token = self.pending = object()

        def complete(positions):
            if (self.pending is not token or self.positions is not None or instance.stopped
                    or not self.active or self.ending or component.fault is not None):
                return False
            if type(positions) is not tuple or not all(_finite_vector(point) for point in positions):
                raise ValueError("spawn search results must be a tuple of finite positions")
            self.positions = positions
            self.timer_at(0, param=2)
            return True

        cancel = component.world.submit_spawn_search(self, request, complete)
        if not callable(cancel):
            component.unsupported(instance, "spawn search service", self.node.cls, self.node)
        self.cancel_search = cancel

    def timer(self, param):
        if param == 2 and self.positions is not None:
            self.instance.write(lvalue(_field(self, "m_8FEA3023")), self.positions)
            self.instance.exit_subgraph(self.node, "m_F9D59C3A")
            self.request_finish()

    def on_cleanup(self):
        self.pending = None
        self.positions = None
        cancel, self.cancel_search = self.cancel_search, None
        if cancel is not None:
            cancel()

    on_failed = on_cleanup


@state_class("STU_0A6BE10E")
class FilterArray(State):
    """104319 1B9E450 collects candidates before queueing a changed array."""

    def on_begin(self):
        self.refresh()

    def dependency_changed(self):
        self.refresh()

    def refresh(self):
        instance = self.instance
        component = instance.component
        if not component.strict:
            return
        source = _field(self, 'm_814F8EC3')
        destination = _field(self, 'm_F2805C84')
        if source is None or destination is None:
            return
        target = lvalue(destination)
        if (target is None or _field(self, 'm_17A88AEF') is not None
                or _field(self, 'm_C485751C') or self.node.links('m_A70DB860')):
            component.unsupported(instance, 'array filter configuration', self.node.cls, self.node)
        config = _field(self, 'm_filter')
        condition = config.get('m_condition') if config else None
        result = []
        for candidate in play._iter_values(instance, source, watcher=self):
            with component.parameter_scope({142: candidate}):
                if isinstance(candidate, Entity):
                    accepted = component.world.filter_array_candidate(self, candidate, config)
                else:
                    accepted = condition is None or truthy(self.evaluate(condition))
            component.ensure_valid()
            if type(accepted) is not bool:
                component.unsupported(instance, 'array filter predicate', self.node.cls, self.node)
            if accepted:
                result.append(candidate)
        output = tuple(result)
        if instance.find_var(*target).value() != output:
            instance.post(Event(component.now, 0, ASSIGN, assignment=(*target, output)))


@state_class("STUStatescriptStateBooleanSwitch")
class BooleanSwitch(State):
    """OnBegin 0x7FF78AAA8F50: evaluate m_condition (its variables become watched), follow the true or the
    false plug and subgraph. A watched change queues one re-evaluation; the branch switches only when
    the result changed."""

    family = "switch"

    def __init__(self, *args) -> None:
        super().__init__(*args)
        self.current = False
        self.pending = False

    def condition(self) -> bool:
        return truthy(self.evaluate(_field(self, "m_condition")))

    def on_begin(self) -> None:
        self.pending = False
        self.current = self.condition()
        self.enter()

    def enter(self) -> None:
        side = "True" if self.current else "False"
        self.instance.follow(self.node, f"m_on{side}Plug", self)
        if self.active:
            self.instance.follow(self.node, f"m_{side.lower()}SubgraphPlug", self)

    def dependency_changed(self) -> None:
        if not self.pending:
            self.pending = True
            self.timer_at(0, REEVALUATE, transient=True)

    def tick(self) -> None:
        if self.polled:  # a condition on something that is no variable: re-evaluated every update
            self.reevaluate()

    def timer(self, param: int) -> None:
        if param == REEVALUATE:
            self.pending = False
            self.reevaluate()

    def reevaluate(self) -> None:
        current = self.condition()
        if current == self.current:
            return
        self.exit()
        self.current = current
        self.enter()

    def exit(self) -> None:
        side = "m_trueSubgraphPlug" if self.current else "m_falseSubgraphPlug"
        self.instance.exit_subgraph(self.node, side)

    def on_end(self, finished: bool) -> None:
        self.exit()

    def payload(self) -> dict:
        return {"current": self.current}


@state_class("STU_B591EBFE")
class DelayedBooleanSwitch(State):
    """104319 1B99670/1B99820: a boolean switch with independent entry delays."""

    def __init__(self, *args):
        super().__init__(*args)
        self.current = False

    def on_begin(self):
        self.current = None
        if not truthy(self.evaluate(_field(self, "m_308B8A80"), watch=False)):
            initial = _field(self, "m_418D9D9D")
            self.current = (
                truthy(self.evaluate(initial, watch=False)) if initial is not None else self._condition()
            )
            self._enter()
        if self.active:
            self._refresh()

    def _condition(self):
        return truthy(self.evaluate(_field(self, "m_BD83C8E6")))

    def _reverse_condition(self, condition=None):
        reverse = _field(self, "m_E8FC060D")
        if reverse is not None:
            return truthy(self.evaluate(reverse))
        return not (self._condition() if condition is None else condition)

    def _enter(self):
        side = "True" if self.current else "False"
        self.instance.follow(self.node, f"m_on{side}Plug", self)
        if self.active:
            self.instance.follow(self.node, f"m_{side.lower()}SubgraphPlug", self)

    def _refresh(self):
        if self.current is True:
            if self._reverse_condition():
                self._delay(2, "m_ED60694A")
            else:
                self._cancel_delay(2)
            return

        condition = self._condition()
        if condition:
            self._delay(1, "m_7130456B")
        elif self.current is False:
            self._cancel_delay(1)
        elif self._reverse_condition(condition):
            self._delay(2, "m_ED60694A")

    def _find_delay(self, param):
        return next((event for event in self.instance.queue
                     if event.kind == TIMER and event.state is self and event.param == param), None)

    def _cancel_delay(self, param):
        event = self._find_delay(param)
        if event is not None:
            self.instance.queue.remove(event)

    def _delay(self, param, field):
        seconds = to_float(self.evaluate(_field(self, field), watch=False))
        if seconds > 0:
            if self._find_delay(param) is None:
                ms = f32(seconds * 1000)
                self.timer_at(int(ms) if ms < 2**63 else 0, param)
        else:
            self._switch(param == 1)
        self._cancel_delay(3 - param)

    def _switch(self, current):
        if current != self.current:
            self.on_cleanup()
            if not self.active:
                return
            self.current = current
            self._enter()
            self.try_complete()

    def dependency_changed(self):
        if self._find_delay(3) is None:
            self.timer_at(0, 3, transient=True)

    def tick(self):
        self._refresh()

    def timer(self, param):
        if param == 3:
            self._refresh()
        elif param in (1, 2):
            if self.polled:
                # Polling sources can change since the previous frame, including on the due frame.
                condition = self._condition() if param == 1 else self._reverse_condition()
                if not condition:
                    self._refresh()
                    return
            self._switch(param == 1)

    def on_cleanup(self):
        if self.current is not None:
            side = "true" if self.current else "false"
            self.instance.exit_subgraph(self.node, f"m_{side}SubgraphPlug")

    def payload(self):
        return {"a": self.current is True, "b": self.current is None}


@state_class("STU_96F1DBCF", strict_only=True)
class MovementFlags(State):
    """D3D420 publishes movement edges from the bound body's ordinary mover record."""

    OUTPUTS = (
        "m_105DF1D6", "m_49F4B33C", "m_56AC7B36", "m_16BC4759", "m_F620FC2A", "m_6306AA29",
        "m_103A8581", "m_958FF5C0", "m_4C55BB03", "m_2EB48682", "m_45EB431F", "m_BF152E27",
    )

    def __init__(self, *args):
        super().__init__(*args)
        self.value = 0
        self.last_frame = None

    def on_begin(self):
        self.tick()
        self.ticks()

    @staticmethod
    def ordinary_flags(sample):
        if sample is None:
            return 0
        x, y, z = map(f32, sample.velocity)
        speed_squared = f32(f32(f32(y * y) + f32(x * x)) + f32(z * z))
        airborne = bool(sample.flags & IN_AIR)
        return (int(airborne) | int(not airborne) << 1
                | int(y > f32(1.1920928955078125e-5)) << 2
                | int(y < f32(-1.1920928955078125e-5)) << 3
                | int(speed_squared > f32(1.1920928955078125e-6)) << 4
                | int(any(sample.throttles)) << 5
                | int(bool(sample.flags & CROUCHED)) << 6
                | int(bool(sample.flags & NOT_GROUND_UNDER)) << 9
                | int(bool(sample.flags & JUMPED)) << 11)

    def tick(self):
        instance = self.instance
        component = instance.component
        if self.last_frame == component.frame:
            return
        self.last_frame = component.frame
        if _field(self, "m_entity") is not None:
            component.unsupported(instance, "movement flags foreign subject", self.node.cls, self.node)
        sample = component.world.movement_snapshot(instance)
        component.ensure_valid()
        # The host's ordinary mover has no timed event or the two extension records.
        if sample is not None and (type(sample) is not Snapshot or sample.flags & 0xA0):
            component.unsupported(instance, "movement flags snapshot", self.node.cls, self.node)

        previous = self.value
        value = self.ordinary_flags(sample)
        changed = previous ^ value
        for bit, key in enumerate(self.OUTPUTS):
            if changed & (1 << bit):
                target = _field(self, key)
                slot = lvalue(target)
                if target is not None and slot is None:
                    component.unsupported(instance, "movement flags output", self.node.cls, self.node)
                instance.write(slot, bool(value & (1 << bit)))
        self.value = value

        # A zero-air-tick record while still airborne is a fresh takeoff after a landing.
        restarted_air = bool(previous & value & 1 and sample.air_ticks == 0)
        if previous & 1 and (not value & 1 or restarted_air):
            instance.follow(self.node, "m_2102C541", self)
        if value & 1 and (not previous & 1 or restarted_air) and self.active:
            instance.follow(self.node, "m_E37BC6C2", self)

    def on_cleanup(self):
        if not self.instance.teardown:
            self.tick()

    def payload(self):
        return {"value": self.value}


@state_class("STUStatescriptStateStack", "STU_A97E6999")
class Stack(State):
    """OnBegin 0x7FF78AAA5FF0: push an entry {state, priority} on m_out_Var. The last entry is the top: its
    state follows m_topSubgraphPlug, every other m_underSubgraphPlug (only on a change). The variable reads
    the top's m_value. A modifier that reads its output variable uses the value below its own entry
    (for example Doomfist 0D06 state 77 adds 0.5 to entity var 5A90). The entry is popped at the end."""

    family = "stack"

    def __init__(self, *args) -> None:
        super().__init__(*args)
        self.top = False
        self.under = False
        self.value = None
        self.var: Var | None = None

    def on_begin(self) -> None:
        self.top = self.under = False
        self.value = self.evaluate(_field(self, "m_value")) if _field(self, "m_value") is not None else None
        target = lvalue(_field(self, "m_out_Var"))
        if target is None:
            return
        priority, above = stack_priority(_field(self, "m_priority"))
        self.var = self.instance.find_var(*target)
        self.link(target, 0, priority, above, stack=True)

    def stack_changed(self, var: Var) -> None:
        if not self.active or self.ending or var is not self.var or not var.links:
            return
        if var.links[-1].state is self:
            if not self.top:
                self.top, self.under = True, False
                self.instance.exit_subgraph(self.node, "m_underSubgraphPlug")
                self.instance.follow(self.node, "m_topSubgraphPlug", self)
        elif not self.under:
            self.top, self.under = False, True
            self.instance.exit_subgraph(self.node, "m_topSubgraphPlug")
            self.instance.follow(self.node, "m_underSubgraphPlug", self)

    def reads_below(self, var: Var) -> bool:
        return var is self.var

    def dependency_changed(self) -> None:
        if self.var is None or _field(self, "m_value") is None:
            return
        value = self.evaluate(_field(self, "m_value"))
        if not expr.same(value, self.value):
            before = self.var.value()
            self.value = value
            self.instance.changed(self.var, before, stack_source=self)

    def on_end(self, finished: bool) -> None:
        self.instance.exit_subgraph(self.node, "m_topSubgraphPlug")
        self.instance.exit_subgraph(self.node, "m_underSubgraphPlug")
        if self.var is not None:
            var, self.var = self.var, None
            self.unlink(var)
        self.top = self.under = False

    def output(self, slot: int):
        return self.value

    def payload(self) -> dict:
        return {"top": self.top, "under": self.under}


@state_class("STUStatescriptStateLogicalButton")
class LogicalButton(State):
    """A held button: edge handler 0x7FF78AAAC570, OnEnter 0x7FF78AAACA80. Down:
    m_onGoingDownPlug then the down subgraph (m_80AF45FB); up: m_onComingUpPlug then the up subgraph
    (m_1C9EF058). A press while inactive is kept for the next activation (m_246438AD, the early press)."""

    family = "button"

    def __init__(self, *args) -> None:
        super().__init__(*args)
        self.down = False
        self.pressed_at: int | None = None
        self.early_counter = -1

    def button_id(self) -> int:
        return to_int(self.evaluate(_field(self, "m_logicalButton"), watch=False))

    def blocked(self) -> bool:
        if not self.instance.component.disabled.get(self.button_id()):
            return False
        ignore = _field(self, "m_53C4B6A6")
        return ignore is None or not truthy(self.evaluate(ignore, watch=False))

    def held(self) -> bool:
        return self.button_id() in self.instance.component.held

    def allowed_dead(self) -> bool:
        """m_allowWhenOwnerDead (m_5AE05408, node +248, the PS4 build's name): the edge handler evaluates it
        before it asks the component whether its owner is dead (vt+488)."""
        allow = _field(self, "m_5AE05408")
        return allow is not None and truthy(self.evaluate(allow, watch=False))

    def on_enter(self) -> None:
        blocked = self.blocked()
        early = False
        duration = _field(self, "m_246438AD")
        if self.early_counter == self.counter and self.pressed_at is not None and duration is not None:
            waited = f32((self.now - self.pressed_at) / 1000.0)
            early = waited <= to_float(self.evaluate(duration, watch=False)) and not blocked
        if early:
            self.instance.follow(self.node, "m_onGoingDownPlug", self)
        if not self.active:
            return
        if not blocked and self.held():
            self.down = True
            self.instance.follow(self.node, "m_80AF45FB", self)
        elif early:
            self.down = True
            self.instance.follow(self.node, "m_80AF45FB", self)
            self.timer_at(1, 1)
        else:
            self.down = False
            self.instance.follow(self.node, "m_1C9EF058", self)

    def button(self, button: int, pressed: bool) -> None:
        if button != self.button_id():
            return
        if pressed and self.instance.component.owner_dead and not self.allowed_dead():
            return  # the edge handler drops a press while the owner is dead (0x7FF78AAAC5FC)
        if self.blocked():
            return
        if pressed:
            self.pressed_at = self.now
            self.early_counter = (self.counter + (0 if self.active else 1)) & 0xFFFF
        if self.active:
            self.go_down() if pressed else self.go_up()

    def go_down(self) -> None:
        self.down = True
        self.instance.exit_subgraph(self.node, "m_1C9EF058")
        self.instance.follow(self.node, "m_onGoingDownPlug", self)
        if self.active:
            self.instance.follow(self.node, "m_80AF45FB", self)

    def go_up(self) -> None:
        self.down = False
        self.instance.exit_subgraph(self.node, "m_80AF45FB")
        self.instance.follow(self.node, "m_onComingUpPlug", self)
        if self.active:
            self.instance.follow(self.node, "m_1C9EF058", self)

    def timer(self, param: int) -> None:
        if param == 1 and not self.held():
            self.go_up()

    def dependency_changed(self) -> None:
        down = self.held() and not self.blocked()
        if down != self.down:
            self.go_down() if down else self.go_up()

    def on_end(self, finished: bool) -> None:
        self.instance.exit_subgraph(self.node, "m_80AF45FB")
        self.instance.exit_subgraph(self.node, "m_1C9EF058")
        self.down = False

    def payload(self) -> dict:
        return {"counter": self.counter}


@state_class("STU_91EF24A9")
class AIBehavior(State):
    """A behavior candidate. The world decides when its selected subgraph runs."""

    def on_enter(self) -> None:
        if self.instance.component.world.selects_ai_behavior(self.instance):
            self.instance.follow(self.node, "m_6F92F4BD", self)

    def on_end(self, finished: bool) -> None:
        if self.instance.component.world.selects_ai_behavior(self.instance):
            self.instance.exit_subgraph(self.node, "m_6F92F4BD")


@state_class("STU_D7F9E1C3")
class AIFireWeapon(State):
    """Hold the authored fire-control button for the self-targeted 0B92 action."""

    def __init__(self, *args) -> None:
        super().__init__(*args)
        self.button_id: int | None = None

    def on_enter(self) -> None:
        component = self.instance.component
        if component.owner_dead or _field(self, "m_234135C3") is not None:
            return
        target = self.evaluate(_field(self, "m_14EDDDA7"), watch=False)
        enabled = self.evaluate(_field(self, "m_20BCEE62"), watch=False)
        slot = self.evaluate(_field(self, "m_A60150E2"), watch=False)
        if target != expr.Entity(component.entity) or enabled is not True or type(slot) is not int:
            return
        self.button_id = component.world.ai_weapon_button(slot)
        if self.button_id is not None:
            component.edges([(self.button_id, True)])

    def on_end(self, finished: bool) -> None:
        if self.button_id is not None:
            self.instance.component.edges([(self.button_id, False)])
            self.button_id = None


@state_class("STU_E4A30BCF")
class DisableLogicalButton(State):
    """While active, the entity's LogicalButtons for this button are blocked (OnBegin 0x7FF78AA82530)."""

    family = "link"

    def on_begin(self) -> None:
        self.button_id = to_int(self.evaluate(_field(self, "m_logicalButton"), watch=False))
        self.instance.component.disabled.setdefault(self.button_id, []).append(self)
        self.resync()

    def on_end(self, finished: bool) -> None:
        states = self.instance.component.disabled.get(self.button_id, [])
        if self in states:
            states.remove(self)
        self.resync()

    def resync(self) -> None:
        for instance in list(self.instance.component.instances.values()):
            for state in list(instance.states.values()):
                if isinstance(state, LogicalButton) and state.active and state.button_id() == self.button_id:
                    state.dependency_changed()


@state_class("STUStatescriptStateWait")
class Wait(State):
    """OnEnter 0x7FF78AAA7570: `ms = R(timeout * 1000)`; 0 or less finishes now, else a timer (param 1) at
    now + ms. m_0DBAFD7F (+256) re-arms on a re-entry; m_BF1A93B0 (+257) re-evaluates the timeout every
    tick."""

    def on_enter(self) -> None:
        self.arm()

    def dynamic(self) -> bool:
        return bool(_field(self, "m_BF1A93B0"))

    def arm(self) -> None:
        self.ms = seconds_to_ms(self.evaluate(_field(self, "m_timeout")))
        if self.dynamic():
            self.ticks()
            self.tick()
        elif self.ms <= 0:
            self.request_finish()
        else:
            self.timer_at(self.ms, 1)

    def reentry(self) -> None:
        if _field(self, "m_0DBAFD7F"):
            self.instance.cancel(self)
            self.start = self.now
            self.arm()

    def timer(self, param: int) -> None:
        if param == 1:
            self.request_finish()

    def tick(self) -> None:
        self.ms = seconds_to_ms(self.evaluate(_field(self, "m_timeout")))
        if self.now - self.start >= self.ms:
            self.request_finish()


@state_class("STU_E3CEF6B9")
class WaitForServerFrames(State):
    """Finishes after its number of server frames (unverified: one frame = one command frame)."""

    def on_enter(self) -> None:
        count = 1
        for value in self.node.fields.values():
            if isinstance(value, dict) and value.get("$") in ("STUConfigVarInt", "STU_919FD47C"):
                count = to_int(self.evaluate(value, watch=False))
                break
        self.request_finish(max(0, count) * self.instance.component.frame_ms)


@state_class("STU_4BD2E886", strict_only=True)
class PhaseTransition(State):
    """AD73D0/AD76D0: progress in, dispatch at full, hold, and release."""

    def on_begin(self) -> None:
        instance = self.instance
        if (instance.graph.index, self.node.pos) != (0x180A, 46):
            instance.component.unsupported(instance, "phase transition placement", self.node.cls, self.node)
        self.progress = 0.0 if self.seconds("m_AD8C28BA") > 0 else 1.0
        self.hold = self.seconds("m_C69C2A73")
        self.releasing = False
        self.dispatched = False
        self.last_update = self.now
        self.ticks()

    def seconds(self, field: str) -> float:
        return to_float(play.evaluate_scalar(self.instance, self.node.field(field), initial=0))

    def timer(self, param: int) -> None:
        if param == 1:
            self.releasing = True

    def tick(self) -> None:
        delta = f32((self.now - self.last_update) * MS)
        self.last_update = self.now
        if self.node.field("m_C69C2A73") is not None and self.progress >= 1.0:
            self.hold = f32(self.hold - delta)
            if self.hold <= 0.0:
                self.releasing = True

        if self.releasing:
            fade = self.seconds("m_D7ED647E")
            self.progress = f32(self.progress - f32(delta / fade)) if fade > 0 else 0.0
            if self.progress <= 0.0:
                self.request(ABORT)
        else:
            if self.progress < 1.0:
                fade = self.seconds("m_AD8C28BA")
                self.progress = f32(self.progress + f32(delta / fade)) if fade > 0 else 1.0
            if self.progress >= 1.0 and not self.dispatched:
                self.dispatched = True
                self.instance.follow(self.node, "m_82A8A67E", self)
        self.progress = min(1.0, max(0.0, self.progress))

    def on_cleanup(self) -> None:
        self.progress = self.hold = 0.0
        self.releasing = self.dispatched = False


@state_class("STU_2F07848F", strict_only=True)
class CinematicTimeScale(State):
    """Stamp the client time-scale envelope with the script component's frame."""

    def __init__(self, *args):
        super().__init__(*args)
        self.boundary_frame = 0

    def on_begin(self):
        instance = self.instance
        if (instance.graph.index, self.node.pos) not in ((0x0CEC, 6), (0x0CE0, 3), (0x181E, 0)):
            instance.component.unsupported(
                instance, "cinematic time-scale placement", self.node.cls, self.node,
            )
        self._stamp_boundary()

    def on_cleanup(self):
        self._stamp_boundary()

    def _stamp_boundary(self):
        delay = to_int(self.evaluate(self.node.field("m_17D12F6B"), watch=False))
        self.boundary_frame = (self.instance.component.frame + max(0, delay)) & 0xFFFFFFFF

    def payload(self):
        return {"which": self.active, "value": self.boundary_frame}


@state_class("STU_3716E1D4", strict_only=True)
class CorpseTimeScale(State):
    """The phase-exit state scales component 43, while the script clock continues."""

    def on_enter(self):
        instance, node = self.instance, self.node
        component = instance.component
        if ((instance.graph.index, node.pos) == (0x01CB, 19)
                and self.evaluate(node.field('m_69A20070'), watch=False) == 0.375):
            if component.owner_dead:
                return
            world = component.world
            phase_node = instance.graph.node(18)
            phase = instance.states.get(phase_node.state) if phase_node is not None else None
            if (getattr(world, 'mode', None) in JUNKENSTEIN_MODES and phase is not None and phase.active
                    and component.entity in getattr(world, 'linked_controllers', ())):
                # The 0045 cue also reaches surviving enemies. 8F8820 changes
                # only the model pose-rate channels, which the server does not animate.
                return
        component.unsupported(instance, 'corpse time-scale placement', node.cls, node)


@state_class("STU_D74D4F47")
class WaitFrames(State):
    """The game's class for this node class (vtable 0x7FF78B5836C8, over the library's plain state):
    OnEnter (0x7FF789C54760) sets the frame it finishes in, the component frame plus m_3016B9A1 (node
    +0xE8, none = 0), and checks it at once; the tick (0x7FF789C54BC0) asks for the finish once that frame
    is reached (unsigned); a re-entry starts it again only with m_0DBAFD7F (node +0x100). That frame is
    its payload (a u32 of its property block)."""

    family = "frames"

    def __init__(self, *args) -> None:
        super().__init__(*args)
        self.target = 0

    def on_enter(self) -> None:
        count = _field(self, "m_3016B9A1")
        frames = to_int(self.evaluate(count, watch=False)) if count is not None else 0
        self.target = (self.instance.component.frame + frames) & 0xFFFFFFFF
        self.ticks()
        self.tick()

    def reentry(self) -> None:
        if _field(self, "m_0DBAFD7F"):
            self.on_enter()

    def tick(self) -> None:
        if self.target <= self.instance.component.frame & 0xFFFFFFFF:
            self.request_finish()

    def payload(self) -> dict:
        return {"target": self.target}


@state_class("STUStatescriptStateChaseVar")
class ChaseVar(State):
    """Moves m_out_Var to m_destination at m_rate per second (step = rate * 0.001 * dt +
    eps), linked to the variable while active and written into it at the end. Fields (type object):
    m_EB0FC261 initial value, m_9B0385B4 rate, m_5BEFF040 fixed duration, m_1E10B2B1 on a write by others,
    m_CB671004 stays active when reached, m_9FF96195 resets to the initial value on a re-entry."""

    family = "chase"

    def __init__(self, *args) -> None:
        super().__init__(*args)
        self.cur = 0.0
        self.reached = False
        self.remaining: int | None = None
        self.last = 0
        self.var: Var | None = None
        self.interrupted = False

    def on_enter(self) -> None:
        target = lvalue(_field(self, "m_out_Var"))
        initial = _field(self, "m_EB0FC261")
        if initial is not None:
            value = self.evaluate(initial, watch=False)
        else:
            value = self.instance.find_var(*target).value() if target else None
        self.cur = value if isinstance(value, Vec3) else to_float(value)
        self.reached = False
        self.interrupted = False
        duration = _field(self, "m_5BEFF040")
        self.remaining = int(to_float(self.evaluate(duration, watch=False)) * 1000) if duration else None
        self.last = self.now
        if target is not None:
            var = self.instance.find_var(*target)
            before = var.value()
            var.base = self.cur
            self.var = self.link(target)
            self.instance.changed(var, before)
        self.ticks()

    def reentry(self) -> None:
        if _field(self, "m_9FF96195"):
            self.on_end(False)
            self.on_enter()

    def destination(self):
        value = self.evaluate(_field(self, "m_destination"))
        return value if isinstance(value, Vec3) else to_float(value)

    def tick(self) -> None:
        dt = self.now - self.last
        if dt <= 0 or self.reached:
            self.last = self.now
            return
        before = self.var.value() if self.var else None
        dest = self.destination()
        if self.remaining is not None:
            if self.remaining <= dt:
                self.cur = dest
                self.reached = True
            else:
                share = f32(dt / self.remaining)
                self.cur = self._lerp(self.cur, dest, share)
                self.remaining -= dt
        else:
            rate = to_float(self.evaluate(_field(self, "m_9B0385B4"))) if _field(self, "m_9B0385B4") else 1.0
            if rate > 0:
                step = f32(f32(f32(rate * MS) * dt) + EPSILON)
                self.cur, self.reached = self._step(self.cur, dest, step)
        self.last = self.now
        if self.var is not None:
            self.instance.changed(self.var, before)
        if self.reached:
            self.timer_at(0, 3, transient=True)
            if not _field(self, "m_CB671004"):
                self.request_finish()

    @staticmethod
    def _lerp(cur, dest, share: float):
        if isinstance(cur, Vec3) or isinstance(dest, Vec3):
            a = cur if isinstance(cur, Vec3) else Vec3(cur, 0.0, 0.0)
            b = dest if isinstance(dest, Vec3) else Vec3(dest, 0.0, 0.0)
            return Vec3(*(f32(x + (y - x) * share) for x, y in zip(a, b, strict=True)))
        return f32(cur + (dest - cur) * share)

    @staticmethod
    def _step(cur, dest, step: float):
        if isinstance(cur, Vec3) or isinstance(dest, Vec3):
            a = cur if isinstance(cur, Vec3) else Vec3(cur, 0.0, 0.0)
            b = dest if isinstance(dest, Vec3) else Vec3(dest, 0.0, 0.0)
            d = Vec3(*(f32(y - x) for x, y in zip(a, b, strict=True)))
            length = f32(math.sqrt(d.x * d.x + d.y * d.y + d.z * d.z))
            if length <= step:
                return b, True
            return Vec3(*(f32(x + v * step / length) for x, v in zip(a, d, strict=True))), False
        if abs(f32(dest - cur)) <= step:
            return dest, True
        return f32(cur + step if dest > cur else cur - step), False

    def var_written(self, var: Var) -> None:
        mode = to_int(_field(self, "m_1E10B2B1") or 0)
        if mode == 0:
            self.interrupted = True
            self.unlink(var)
            self.var = None
        elif mode == 1:
            self.cur = to_float(var.base) if not isinstance(var.base, Vec3) else var.base

    def on_end(self, finished: bool) -> None:
        if self.var is not None:
            var, self.var = self.var, None
            self.unlink(var, bake=not self.interrupted)

    def output(self, slot: int):
        return self.cur

    def payload(self) -> dict:
        return {"cur": self.cur, "reached": self.reached, "remaining": self.remaining, "last": self.last}


@state_class("STUStatescriptStateWeaponVolley")
class WeaponVolley(State):
    """A burst of shots: shot k at start + R((k / rate) * 1000);
    n = min(ammo / per shot, max shots); a finish timer (param 2) at the last shot's time and a minimum-shots
    timer (param 1). While active m_out_Ammo reads `max(0, ammo0 - perShot * (shots + 1))`; at the end the
    variable keeps the value one ms later (the +1 ms rule, 0x7FF78A07F310)."""

    family = "volley"

    def __init__(self, *args) -> None:
        super().__init__(*args)
        self.offset = 0
        self.volleys = 0  # the volley counter (the record's low byte); 1 after the first StartFiring
        self.per_shot = 1
        self.ammo0 = 0
        self.rate = 1.0
        self.spread0 = 0.0
        self.spread_ramp: float | None = None
        self.links: list[Var] = []
        self.reported = 0  # shots of this activation already put on the component's shot list

    def on_enter(self) -> None:
        self.offset = 0
        self.volleys = max(1, self.volleys)
        self.reported = 0
        self.params()
        self.arm()

    def report(self, until: int) -> None:
        """Put the shots fired up to `until` (ms) that are not on the component's list yet on it: shot k
        leaves at start + offset + shot_time(k), at most the planned shots (combat.py hits with them)."""
        if self.instance.component.owner_dead:
            # unverified (PS4 build): a dead owner's volley shoots no more (allowShotsWhileDead: not read)
            return
        start = self.start + self.offset
        activation = (self.counter, self.start)
        planned = self.shots_planned()
        while self.reported < planned:
            time = start + shot_time(self.reported, self.rate)
            if time > until:
                break
            self.instance.component.shots.append((self, self.reported, time, activation))
            self.reported += 1

    def _config(self, name: str, default):
        cfg = _field(self, name)
        return default if cfg is None else self.evaluate(cfg, watch=False)

    def params(self) -> None:
        self.max_shots = to_int(self._config("m_A17BE89B", 1))
        self.min_shots = to_int(self._config("m_A55BC904", 1))
        self.per_shot = max(0, to_int(self._config("m_5F40D9D3", 1)))
        rate = self._config("m_numShotsPerSecond", 1.0)
        self.rate = max(1e-5, to_float(rate)) if rate is not None else 1.0
        ammo = self._out("m_24E351A4")
        self.ammo0 = max(0, to_int(ammo.value())) if ammo else self.max_shots * self.per_shot
        spread = self._out("m_3FFC9EB9")
        self.spread0 = min(1.0, max(0.0, to_float(spread.value()))) if spread else 0.0
        launch = _field(self, "m_5EC1EB38")
        self.spread_ramp = None
        if (isinstance(launch, dict) and launch.get("$") == "STU_239B571E"
                and launch.get("m_23C72C36") is None):
            cfg = launch.get("m_AC20B1C1")
            self.spread_ramp = f32(to_float(self.evaluate(cfg, watch=False))) if cfg else 0.0
        self.links = []
        for slot, (name, value) in enumerate(
            (("m_24E351A4", self.ammo0), ("m_17770E7E", self.rate), ("m_3FFC9EB9", self.spread0))
        ):
            target = lvalue(_field(self, name))
            if target is None:
                continue
            var = self.instance.find_var(*target)
            before = var.value()
            var.base = value
            self.links.append(self.link(target, slot))
            self.instance.changed(var, before)

    def _out(self, name: str) -> Var | None:
        target = lvalue(_field(self, name))
        return self.instance.find_var(*target) if target else None

    def shots_at(self, elapsed: int) -> int:
        """The float-based shot index used by the ammunition output (-1 before the start)."""
        if elapsed < 0:
            return -1
        return math.floor(f32(f32(f32(elapsed) * MS) * self.rate))

    def shots_planned(self) -> int:
        if self.per_shot <= 0:
            return self.max_shots
        return min(self.ammo0 // self.per_shot, self.max_shots)

    def arm(self) -> None:
        delay = self.offset - (self.now - self.start)
        if self.per_shot == 0 or self.min_shots < self.ammo0 // self.per_shot:
            self.timer_at(delay + shot_time(max(self.min_shots, 1) - 1, self.rate), 1)
        self.timer_at(delay + shot_time(max(self.shots_planned(), 1) - 1, self.rate), 2)

    def timer(self, param: int) -> None:
        if param == 1:
            self.instance.follow(self.node, "m_onMinShotsPlug", self)
        elif param == 2:
            self.request_finish()

    def ammo_at(self, elapsed: int) -> int:
        if elapsed < 0:
            return self.ammo0
        return max(0, self.ammo0 - self.per_shot * (self.shots_at(elapsed) + 1))

    def output(self, slot: int):
        if slot == 0:
            return self.ammo_at(self.now - self.start - self.offset)
        if slot == 1:
            return self.rate
        return self.spread_at(self.now - self.start - self.offset)

    def spread_at(self, elapsed: int) -> float:
        """239B571E normalized spread, captured PC104319 D80A50-D80A99.

        The ramp is baked when firing starts; the output is not an angle.
        D5F979-D5F996 passes completed shots plus one to the ramp function.
        Optional between-shot decay and other launch classes remain unmodeled.
        """
        if self.spread_ramp is None:
            return self.spread0
        if self.spread_ramp < f32(1e-7):
            return 1.0
        increase = f32(max(0, self.shots_at(elapsed) + 1) / self.spread_ramp)
        return min(1.0, f32(increase + f32(self.spread0)))

    def on_end(self, finished: bool) -> None:
        self.report(self.now)
        elapsed = self.now - self.start - self.offset
        values = {0: self.ammo_at(elapsed + 1), 1: self.rate, 2: self.spread_at(elapsed + 1)}
        for var in self.links:
            link = next((item for item in var.links if item.state is self), None)
            if link is not None:
                before = var.value()
                var.base = values.get(link.slot)
                self.unlink(var)
                self.instance.changed(var, before)
        self.links = []

    def payload(self) -> dict:
        return {"start": self.start, "offset": self.offset, "volleys": self.volleys, "counter": self.counter}


def report_active_volleys(component):
    """Report shots due now. Ending volleys report their last shots in on_end."""
    component.ensure_valid()
    for instance in component.instances.values():
        for state in instance.states.values():
            if isinstance(state, WeaponVolley) and state.active:
                state.report(component.now)


@state_class("STUStatescriptStateMoveTo")
class MoveTo(State):
    """Expose a resolved fixed world target and speed for one state activation."""

    def __init__(self, *args) -> None:
        super().__init__(*args)
        self.movement: tuple[Vec3, float] | None = None

    def on_begin(self) -> None:
        speed_config = _field(self, "m_speed")
        unsupported = ("m_destinationEntity", "m_destinationBone", "m_5BEFF040")
        if _field(self, "m_B060F688") != 0 or speed_config is None or any(
            _field(self, field) is not None for field in unsupported
        ):
            return
        destination = self.evaluate(_field(self, "m_865F3D01"), watch=False)
        speed = self.evaluate(speed_config, watch=False)
        if not isinstance(destination, Vec3) or type(speed) not in (int, float):
            return
        # The world movement record must be able to carry the pose and velocity.
        if 0 < speed <= MAX_MOVEMENT_AXIS and all(
            -MAX_MOVEMENT_AXIS <= axis <= MAX_MOVEMENT_AXIS for axis in destination
        ):
            self.movement = destination, float(speed)

    def move_request(self) -> tuple[Vec3, float] | None:
        return self.movement if self.active else None

    def on_end(self, finished: bool) -> None:
        self.movement = None


@state_class("STUStatescriptStateAnim")
class Anim(State):
    """Animation state timing (104319 CC19A0 / CC1E70).

    Non-looping states use an authored or model-resolved duration. Timer 1
    follows m_A3B86BB0, and m_154DBA91 suppresses automatic state finish.
    """

    family = "anim"
    scalar_value: float | None = None

    def on_begin(self) -> None:
        target = lvalue(_field(self, "m_EBEFB4A7"))
        if target is not None:
            scalar = self.instance.component.world.animation_scalar(self.instance, self.node)
            if scalar is not None:
                self.scalar_value = scalar
                self.link(target, slot=2)

    def output(self, slot: int):
        return self.scalar_value if slot == 2 else super().output(slot)

    def on_cleanup(self) -> None:
        if self.scalar_value is not None:
            target = lvalue(_field(self, "m_EBEFB4A7"))
            self.unlink(self.instance.find_var(*target))
            del self.scalar_value

    def on_enter(self) -> None:
        if truthy(self.evaluate(_field(self, "m_37EE887D"), watch=False)):
            return
        authored = _field(self, "m_86701C3E")
        duration = to_float(self.evaluate(authored, watch=False))
        if duration < 0.00011920928955078125:
            resolved = self.instance.component.world.animation_duration(self.instance, self.node)
            if resolved is not None:
                speed = _field(self, "m_speedScalar")
                scalar = 1.0 if speed is None else to_float(self.evaluate(speed, watch=False))
                duration = f32(resolved * scalar)
            elif authored is None:
                expr.warn_once("anim", "model-resolved Anim durations are unavailable")
                return
        delay = seconds_to_ms(duration) - (self.now - self.start)
        self.timer_at(delay, 1)
        if not truthy(self.evaluate(_field(self, "m_154DBA91"), watch=False)):
            self.request_finish(max(0, delay))

    def timer(self, param: int) -> None:
        if param == 1:
            self.instance.follow(self.node, "m_A3B86BB0", self)

    def payload(self) -> dict:
        return {"counter": self.counter}


@state_class("STUStatescriptStateAbility")
class Ability(State):
    """An ability on a button, as the PC client runs it: update 0x7FF789B95560, keep 0x7FF789BB87E0, start
    0x7FF789BA9460, stop 0x7FF789BA6BC0, push 0x7FF789BA6D40, stack hook 0x7FF789B94DC0, output
    0x7FF789B94D00, field offsets from the class's field list. The start check (0x7FF789BB8D20) is
    obfuscated; it follows the PS4 build's code (unverified): a buffered press of the start button (within the
    early-press time, for this activation) or the start button held, the cooldown at 0, the start and
    continue conditions true, and the stack variable taking the "starting" priority.

    Every update while active: a running ability checks whether it keeps running (else it stops); one that is
    not running checks whether it can start. Keep: with no hold button (or m_755F9921 set) a press of the stop
    button stops it, and when that is the start button the press is latched (flag 4) so that it does not start
    it again while it is down; with a hold button, letting it go stops it; then the continue condition and the
    stack variable's "continuing" priority. An ability with a stack variable and a priority to set
    (m_5E561E77) does not start at once: it pushes itself onto the variable (flag 2, slot 1, value true) and
    starts when its entry is the top; when its entry is no longer the top it stops. Running (flag 1): the
    ability subgraph, the on-start plug and its variables. The cooldown variable is linked as slot 0: it falls
    by `rate` per second from the value last written to it."""

    family = "ability"
    RUNNING, PUSHED, HELD = 1, 2, 4

    def __init__(self, *args) -> None:
        super().__init__(*args)
        self.flags = 0
        self.presses: list[tuple[int, int, int]] = []  # (time, button, activation)
        self.cur = 0.0
        self.cooldown_rate = 1.0
        self.last = 0
        self.var: Var | None = None
        self.stack_var: Var | None = None

    # the cooldown

    def on_begin(self) -> None:
        self.flags = 0
        self.restart()
        self.ticks()

    def rate(self) -> float:
        modifier = self.instance.component.entity_var(3652, create=False)
        value = modifier.value() if modifier is not None else None
        rate = f32(to_float(value) + 1.0) if value is not None else 1.0
        scalar = _field(self, "m_F7CAF887")
        return f32(rate * to_float(self.evaluate(scalar, watch=False))) if scalar is not None else rate

    def restart(self) -> None:
        """The cooldown from the variable's value now (a write to it restarts it)."""
        target = lvalue(_field(self, "m_out_CooldownVar"))
        if target is None:
            return
        var = self.instance.find_var(*target)
        if self.var is var:
            var.links = [item for item in var.links if item.state is not self]
        self.cur = max(0.0, to_float(var.base))
        self.cooldown_rate = self.rate()
        self.last = self.now
        self.var = self.link(target)
        queue = self.instance.queue
        self.instance.queue = [event for event in queue if not (event.state is self and event.param == 2)]
        if self.cur > 0 and self.cooldown_rate > 0:
            self.timer_at(max(1, round_half_away(f32(f32(self.cur * 1000.0) / self.cooldown_rate))), 2)

    def remaining(self) -> float:
        return max(0.0, f32(f32(f32(f32(self.now - self.last) * -MS) * self.cooldown_rate) + self.cur))

    def output(self, slot: int):
        """Slot 0: the cooldown left (the cooldown variable); slot 1: true (the stack variable)."""
        return self.remaining() if slot == 0 else True

    def var_written(self, var: Var) -> None:
        if var is self.var:
            self.restart()

    # buttons

    def button_id(self, name: str) -> int:
        cfg = _field(self, name)
        return to_int(self.evaluate(cfg, watch=False)) if cfg is not None else 0

    def buttons(self) -> tuple[int, int, int]:
        return self.button_id("m_A5F1A77B"), self.button_id("m_A368407D"), self.button_id("m_8CBD15DA")

    def held(self, button: int) -> bool:
        component = self.instance.component
        return button in component.held and not component.disabled.get(button)

    def button(self, button: int, pressed: bool) -> None:
        if not pressed or button == 0 or self.instance.component.disabled.get(button):
            return
        if button in self.buttons():
            self.presses = [item for item in self.presses if self.now - item[0] < 2000]
            self.presses.append((self.now, button, (self.counter + (0 if self.active else 1)) & 0xFFFF))
            if self.active:
                self.timer_at(0, 1, transient=True)

    def take_press(self, button: int, *, retain: bool = False) -> bool:
        early = _field(self, "m_246438AD")
        limit = seconds_to_ms(self.evaluate(early, watch=False)) if early is not None else 0
        for item in self.presses:
            time, which, activation = item
            if which == button and self.now - time <= limit and activation <= self.counter:
                if not retain:
                    self.presses.remove(item)
                return True
        return False

    # starting and stopping

    def tick(self) -> None:
        self.activate()

    def timer(self, param: int) -> None:
        if param in (1, 2):
            self.activate()

    def plug_enter(self, input_path: str) -> bool:
        if input_path == "m_3B7BED2E":  # m_stopAbility
            self.stop()
            return True
        return super().plug_enter(input_path)

    def condition(self, name: str) -> bool:
        cfg = _field(self, name)
        return cfg is None or truthy(self.evaluate(cfg))

    def stack_allows(self, name: str) -> bool:
        target = lvalue(_field(self, "m_out_StackVar"))
        if target is None or _field(self, name) is None:
            return True
        var = self.instance.find_var(*target)
        self.watch(var)
        return would_stack_to_top(var, stack_priority(_field(self, name)), exclude=self)

    def stacked(self) -> bool:
        return _field(self, "m_out_StackVar") is not None and _field(self, "m_5E561E77") is not None

    def activate(self) -> None:
        on = self.flags & (self.PUSHED if self.stacked() else self.RUNNING)
        if on:
            if not self.keep():
                self.stop()
        elif self.can_start():
            if self.stacked():
                self.push()
            else:
                self.start_ability()

    def can_start(self) -> bool:
        if self.instance.component.owner_dead:
            return False  # unverified (PS4 build): no start while the owner is dead ("allowed": not read)
        start, hold, _ = self.buttons()
        pressed = False
        if start:
            pressed = self.take_press(start, retain=True)
            if not pressed:
                if not self.held(start):
                    self.flags &= ~self.HELD
                    return False
                if self.flags & self.HELD:
                    return False
        elif hold and not self.held(hold):
            return False
        ready = (
            self.remaining() <= 0
            and self.condition("m_150F0D92")
            and self.condition("m_F495731F")
            and self.stack_allows("m_3C0EB4A6")
        )
        if not ready:
            return False
        if pressed:
            self.take_press(start)  # consume only after start gates pass; retain the original timestamp
        if start:
            self.flags |= self.HELD
        return True

    def keep(self) -> bool:
        if self.instance.component.owner_dead:
            return False  # unverified (the PS4 build): a running ability stops when its owner dies
        start, hold, stop = self.buttons()
        if self.flags & self.HELD and not self.held(start):
            self.flags &= ~self.HELD
        keep_held = _field(self, "m_755F9921")
        if not hold or (keep_held is not None and truthy(self.evaluate(keep_held))):
            if stop and self.take_press(stop):
                if stop == start:
                    self.flags |= self.HELD  # the stopping press must not start it again while it is down
                return False
        elif not self.held(hold):
            return False
        return self.condition("m_F495731F") and self.stack_allows("m_343E99EE")

    def push(self) -> None:
        """Onto the stack variable at the priority to set; the stack hook starts it when it is the top."""
        target = lvalue(_field(self, "m_out_StackVar"))
        priority, above = stack_priority(_field(self, "m_5E561E77"))
        self.flags |= self.PUSHED
        self.stack_var = self.instance.find_var(*target)
        self.link(target, 1, priority, above, stack=True)

    def stack_changed(self, var: Var) -> None:
        if var is not self.stack_var or not self.active or not self.flags & self.PUSHED:
            return
        top = var.links[-1] if var.links else None
        if top is not None and top.state is self and top.slot == 1:
            if not self.flags & self.RUNNING:
                self.start_ability()
        else:
            self.stop()

    def set_vars(self, name: str) -> None:
        for item in _field(self, name) or []:
            if isinstance(item, dict):
                value = self.evaluate(item.get("m_value"), watch=False)
                self.instance.write(lvalue(item.get("m_out_Var")), value)

    def start_ability(self) -> None:
        self.flags |= self.RUNNING
        self.instance.follow(self.node, "m_B969AFC9", self)
        self.instance.follow(self.node, "m_4E22B5C4", self)
        self.set_vars("m_EF3C5C73")

    def stop(self) -> None:
        """Queue the running scope's cleanup before releasing its arbitration link.

        This server ordering prevents a newly unblocked weapon from sampling old
        ammo before the outgoing scope's refill message. Native inter-object
        ordering is not yet proven by the static stop/delta-guard instructions.
        """
        var = None
        if self.flags & self.PUSHED:
            self.flags &= ~self.PUSHED
            var, self.stack_var = self.stack_var, None
        if self.flags & self.RUNNING:
            self.flags &= ~self.RUNNING
            self.instance.exit_subgraph(self.node, "m_B969AFC9")
            self.instance.follow(self.node, "m_6EEEC0AA", self)
            self.set_vars("m_19899EAC")
        if var is not None:
            self.unlink(var)

    def on_end(self, finished: bool) -> None:
        self.flags &= ~self.HELD
        self.stop()
        if self.var is not None:
            var, self.var = self.var, None
            before = var.value()
            var.base = self.remaining()
            var.links = [item for item in var.links if item.state is not self]
            self.instance.changed(var, before)

    def payload(self) -> dict:
        return {"flags": self.flags, "cur": self.cur, "rate": self.cooldown_rate, "last": self.last}


@state_class("STU_9C9A422A")
class Defer(State):
    """Runs its out plug (m_FD0C0646) and subgraph (m_8E484B2E) in the deferred phase of the update, after
    the ticks."""

    def on_begin(self) -> None:
        priority = to_float(self.evaluate(_field(self, "m_priority"), watch=False))
        self.instance.deferred.append((priority, self.counter, self))
        self.instance.deferred.sort(key=lambda item: item[0])

    def timer(self, param: int) -> None:
        if param == 1:
            self.instance.follow(self.node, "m_FD0C0646", self)
            if self.active:
                self.instance.follow(self.node, "m_8E484B2E", self)

    def on_end(self, finished: bool) -> None:
        self.instance.deferred = [item for item in self.instance.deferred if item[2] is not self]
        self.instance.exit_subgraph(self.node, "m_8E484B2E")


@state_class("STU_2F4E2E3F")
class Watch(State):
    """Follows m_onChangedPlug (m_A70DB860) when the watched value (m_B7A3CF78) changes, after writing the
    old value into m_48A8CDEC."""

    def on_begin(self) -> None:
        self.seen = self.evaluate(_field(self, "m_B7A3CF78"))

    def dependency_changed(self) -> None:
        if not equal(self.evaluate(_field(self, "m_B7A3CF78")), self.seen):
            self.timer_at(0, REEVALUATE, transient=True)

    def timer(self, param: int) -> None:
        if param != REEVALUATE:
            return
        previous = lvalue(_field(self, "m_48A8CDEC"))
        if previous is not None:
            self.instance.write(previous, self.seen)
        self.instance.follow(self.node, "m_A70DB860", self)
        self.seen = self.evaluate(_field(self, "m_B7A3CF78"))


@state_class("STU_6A926B21", strict_only=True)
@state_class("STU_CD46AF93")
class ForCodeSynced(State):
    """Local ForCode registration and notifications with explicit transport policy.

    Native begin/end/update: D44CA0, D44CD0, D44D00. Nonzero sync flags invoke
    D4F690 only when the VM role permits transport. That transport remains
    unimplemented. Legacy body prediction retains its former passive state.
    """

    def on_begin(self) -> None:
        component = self.instance.component
        if not component.strict:
            return
        sync_flags = (_field(self, "m_598E32D5"), _field(self, "m_0582B0E4"))
        # D44E0E checks the VM role before dispatching D4F690.
        if (any(flag not in (None, 0) for flag in sync_flags)
                and component.world.for_code_transport is not False):
            component.unsupported(self.instance, "for-code synchronization", self.node.cls, self.node)
        if _field(self, "m_DC897C87") not in (None, 0, 1):
            component.unsupported(self.instance, "for-code notification channel", self.node.cls, self.node)
        for row in _field(self, "m_values") or []:
            if not isinstance(row, dict):
                component.unsupported(self.instance, "for-code value row", self.node.cls, self.node)
        component.code_states.append(self)
        self.ticks()

    def on_enter(self) -> None:
        # DoBeginState calls the native E0 update before the authored begin plugs
        # (1B70A6E, followed by 1B70A9E/1B73E00).
        self.publish()

    def tick(self) -> None:
        self.publish()

    def reentry(self) -> None:
        # The already-active DoBegin branch also invokes E0 (1B708FA).
        self.publish()

    def publish(self) -> None:
        component = self.instance.component
        if not component.strict:
            return
        channel = _field(self, "m_DC897C87")
        channel = 1 if channel is None else channel  # STU default x61315314 = 1.
        for row in _field(self, "m_values") or []:
            component.consume_work("native code value update")
            if row.get("m_value") is not None:
                self.evaluate(row["m_value"])
            identifier = expr.guid_of(row.get("m_identifier"))
            if identifier:
                component.code_pending[channel].append(identifier)
        if channel == 0:
            component.notify_code(0)

    def on_end(self, finished: bool) -> None:
        if self.instance.component.strict:
            self.instance.component.unregister_code_state(self)
            self.publish()


@state_class("STU_DEBD057B")
class Switch(State):
    """A switch over subgraphs (m_05706D1C cases, m_A5EF385D otherwise), re-evaluated like a BooleanSwitch."""

    def __init__(self, *args) -> None:
        super().__init__(*args)
        self.case: str | None = None

    def pick(self) -> str:
        value = self.evaluate(_field(self, "m_262AE982"))
        for number, case in enumerate(_field(self, "m_05706D1C") or []):
            if isinstance(case, dict) and equal(value, self.evaluate(case.get("m_value"), watch=False)):
                return f"m_05706D1C[{number}]"
        return "m_A5EF385D"

    def on_begin(self) -> None:
        self.case = self.pick()
        self.instance.follow(self.node, self.case, self)

    def dependency_changed(self) -> None:
        self.timer_at(0, REEVALUATE, transient=True)

    def timer(self, param: int) -> None:
        case = self.pick()
        if param == REEVALUATE and case != self.case:
            self.instance.exit_subgraph(self.node, self.case)
            self.case = case
            self.instance.follow(self.node, case, self)

    def on_end(self, finished: bool) -> None:
        if self.case:
            self.instance.exit_subgraph(self.node, self.case)


@state_class("STU_77E13026", strict_only=True)
class PhaseScriptList(State):
    """The authored 180A list branch, with one local namespace per item occurrence."""

    def __init__(self, *args) -> None:
        super().__init__(*args)
        self.children: list[tuple[object, Instance]] = []

    def on_enter(self) -> None:
        component = self.instance.component
        if (self.instance.graph.index, self.node.pos) != (0x180A, 42):
            component.unsupported(self.instance, "script list state", self.node.cls, self.node)
        self.refresh()

    def reentry(self) -> None:
        self.refresh()

    def dependency_changed(self) -> None:
        self.refresh()

    def refresh(self) -> None:
        component = self.instance.component
        values = play.evaluate_value(self.instance, _field(self, "m_814F8EC3"), initial=None, watcher=self)
        remaining = list(self.children)
        children = []
        added = []
        # D422B0 reads only the outer array and ignores null rows. D50F70
        # reconciles occurrences, including repeats of the same graph resource.
        for value in values if isinstance(values, tuple) else ():
            component.consume_work("phase script list item")
            if value is None:
                continue
            for index, (previous, _) in enumerate(remaining):
                component.consume_work("phase script list match")
                if equal(value, previous):
                    children.append(remaining.pop(index))
                    break
            else:
                added.append(value)
        for _, child in remaining:
            component.destroy(child)

        output = lvalue(_field(self, "m_3DC6FAAA"))
        if output is None or output[0] != expr.INSTANCE:
            component.unsupported(self.instance, "script list item destination", self.node.cls, self.node)
        for value in added:
            child = component.create(self.instance.graph, parent=self.instance, parent_state=self.index,
                                     values={output: value}, init=False)
            child.set_source_entity(self.instance.source_entity())
            child.init(run_entries=False)
            children.append((value, child))
            child.follow(self.node, "m_C41A9BE2")
        self.children = children

    def on_end(self, finished: bool) -> None:
        for _, child in self.children:
            self.instance.component.destroy(child)
        self.children.clear()


@state_class("STU_CB4905E9", strict_only=True)
class PhaseScript(State):
    """D4C080/D50870: the same-owner resource invocation authored in 180A."""

    def __init__(self, *args) -> None:
        super().__init__(*args)
        self.child: Instance | None = None

    def on_enter(self) -> None:
        from ow174.game.script import graph as graphs

        instance = self.instance
        component = instance.component
        if (instance.graph.index, self.node.pos) != (0x180A, 41) or instance.parent is None:
            component.unsupported(instance, "scoped script state", self.node.cls, self.node)
        resource = play.resolve_resource(instance, self.node)
        if resource is None:
            return
        request = play.resolve_request(
            instance, self.node, resource,
            context_provider=lambda: component.world.play_script_context(instance, self.node),
        )
        if request is None:
            return
        if request.targets != (component.entity,):
            component.unsupported(instance, "scoped script targets", self.node.cls, self.node)
        definition = graphs.graph(resource.guid & 0xFFFFFFFFFFFF)
        if definition is None:
            component.unsupported(instance, "scoped script resource", f"{resource.guid:016X}", self.node)
        child = component.create(definition, parent=instance, parent_state=self.index, init=False)
        self.child = child
        child.set_source_entity(request.context.instigator)
        child.play_schema(resource.overrides, reorder_entries=False)
        child.init()
        child.start_entries(request.params, request.context.caller)

    def on_end(self, finished: bool) -> None:
        if self.child is not None:
            self.instance.component.destroy(self.child)
            self.child = None


@state_class("STU_37D754C8")
class SubScript(State):
    """Runs a child instance of another graph while active (OnBegin 0x7FF78AAB1F30): its variables from
    m_89402D74 (evaluated here), destroyed at the end. The child gets the next free instance id."""

    family = "subscript"

    def __init__(self, *args) -> None:
        super().__init__(*args)
        self.child: Instance | None = None

    def graph_index(self) -> int | None:
        number = guid(self.node.field("m_36F7B64C.m_36F7B64C.m_C974E369.m_graph"))
        return number & 0xFFFF if number else None

    def on_begin(self) -> None:
        from ow174.game.script import graph as graphs

        index = self.graph_index()
        found = graphs.graph(index) if index is not None else None
        if found is None:
            if self.instance.component.strict:
                self.instance.component.unsupported(
                    self.instance, "SubScript graph", f"{index or 0:04X}", self.node,
                )
            expr.warn_once(f"sub{index}", "SubScript graph %s is not in the data", f"{index or 0:04X}")
            return
        values = {}
        component = self.instance.component
        for item in _field(self, "m_89402D74") or []:
            if isinstance(item, dict):
                target = lvalue(item.get("m_out_Var"))
                if target is not None:
                    cfg = item.get("m_value")
                    if component.strict and isinstance(cfg, dict) and cfg.get("$") == "STU_06610EBC":
                        values[target] = play.evaluate_value(self.instance, cfg)
                    else:
                        values[target] = self.evaluate(cfg, watch=False)
        self.child = component.create(found, parent=self.instance, parent_state=self.index, values=values)

    def on_end(self, finished: bool) -> None:
        if self.child is not None:
            self.instance.component.destroy(self.child)
            self.child = None

    def payload(self) -> dict:
        return {"child": self.child.id if self.child is not None else 0}

    def child_stopped(self, child: Instance) -> None:
        if self.child is child:
            self.child = None
            if not _field(self, 'm_24AB04BB'):
                self.request_finish()


@state_class("STUStatescriptStateGameMessageEntry", "STU_9D6CF8AC")
class GameMessageEntry(State):
    """Waits for its game message: the idle subgraph (m_33DA5163) until it comes, then the receive subgraph
    (m_452862E6)."""

    family = "message"

    def __init__(self, *args):
        super().__init__(*args)
        self.message_context = None
        self.message_sender = 0
        self.message_stacked = False
        self.message_key = 0
        self.message_flags = None
        self.message_selected = None
        self.message_generation = -1
        self.message_count = 0
        self.message_sources = ()
        self.message_cache = ()
        self.message_refresh = False
        self.message_updating = False
        self.message_getting = False
        self.message_epoch = 0
        self.message_last_epoch = 0
        self.message_bindings = {}
        self.message_observed = {}

    def on_begin(self) -> None:
        if self.instance.component.strict:
            message_receiver.begin(self)
            return
        self.received = False
        self.instance.follow(self.node, "m_33DA5163", self)

    def message(self, message: int, params: dict, sender: int) -> None:
        if self.instance.component.strict:
            self.instance.component.unsupported(self.instance, "packet delivered to persistent receiver",
                                                self.node.cls, self.node)
        self.instance.exit_subgraph(self.node, "m_33DA5163")
        self.received = True
        for item in _field(self, "m_params") or []:
            if isinstance(item, dict):
                target = lvalue(item.get("m_out_Var"))
                if target is not None:
                    self.instance.write(target, params.get(expr.guid_of(item.get("m_B5051BCE")) & 0xFFFF))
        self.instance.follow(self.node, "m_452862E6", self)

    def on_end(self, finished: bool) -> None:
        if self.instance.component.strict:
            return
        self.instance.exit_subgraph(self.node, "m_33DA5163")
        self.instance.exit_subgraph(self.node, "m_452862E6")

    def payload(self) -> dict:
        if self.instance.component.strict:
            return {"sender": self.message_sender, "stacked": self.message_stacked}
        return {"sender": None, "stacked": False}

    def native_dependency(self, cfg):
        if self.instance.component.strict:
            message_receiver.native_dependency(self)

    def dependency_changed(self):
        if self.instance.component.strict:
            message_receiver.refresh(self)

    def tick(self):
        if self.instance.component.strict:
            message_receiver.tick(self)
        else:
            super().tick()

    def output(self, slot):
        return message_receiver.output(self, slot) if self.instance.component.strict else None

    def on_cleanup(self):
        if self.instance.component.strict:
            message_receiver.cleanup(self)

    def on_failed(self):
        message_receiver.abandon(self)

    def on_destroyed(self):
        message_receiver.abandon(self)

    def message_context_retired(self):
        message_receiver.abandon(self, retired=True)


@state_class("STU_38EE1100")
class SendGameMessageState(State):
    """Persistent sender registration; ordinary Entry packets use another path."""

    family = "send"

    def __init__(self, *args):
        super().__init__(*args)
        self.message_context = None
        self.message_frame = self.message_sequence = None
        self.message_keys = []
        self.message_notified = None

    def on_created(self):
        if self.instance.component.strict:
            message_state.created(self)

    def on_begin(self) -> None:
        if self.instance.component.strict:
            message_state.begin(self)
        else:
            send(self.instance, self.node, self)

    def on_enter(self):
        # Base 1B70790 invokes virtualE0 after the class's begin/enter hooks.
        if self.instance.component.strict:
            message_state.refresh(self)

    def reentry(self):
        if self.instance.component.strict:
            message_state.refresh(self)

    def tick(self):
        if self.instance.component.strict:
            message_state.refresh(self)

    def dependency_changed(self):
        if self.instance.component.strict:
            message_state.refresh(self)

    def timer(self, param):
        if self.instance.component.strict:
            message_state.timer(self, param)

    def on_cleanup(self):
        if self.instance.component.strict:
            message_state.cleanup(self)

    def on_failed(self):
        message_state.abandon(self)

    def on_destroyed(self):
        message_state.abandon(self)

    def payload(self):
        if self.instance.component.strict and self.message_sequence is not None:
            return {'reply': self.message_sequence & 0xFFFF}
        return {}


@state_class("STUStatescriptStateCosmeticEntity")
class CosmeticEntity(State):
    """A cosmetic child entity (the gun model): its out variable (m_1DEF59FA) holds a handle to this state
    while active, {kind 1, instance, state, 0} (0254 #49)."""

    def on_begin(self) -> None:
        target = lvalue(_field(self, "m_1DEF59FA"))
        self.var = self.link(target) if target is not None else None

    def on_end(self, finished: bool) -> None:
        if self.var is not None:
            var, self.var = self.var, None
            self.unlink(var)

    def output(self, slot: int):
        return Handle(1, self.instance.id, self.index, 0)


@state_class("STUStatescriptStateGamePadVibration")
class GamePadVibration(State):
    """Ends itself; with no gamepad at once."""

    def on_enter(self) -> None:
        self.request_finish()


@state_class("STUStatescriptStateModifyHealth")
class ModifyHealth(State):
    """Explicit world health service; supported manual1157 clock is reconstructed."""

    def __init__(self, *args):
        super().__init__(*args)
        self.last_applied = 0

    def on_enter(self):
        self.last_applied = self.now
        value = self.instance.component.world.modify_health(self, 0)
        if value is not NotImplemented:
            self.ticks()
        elif self.instance.component.strict:
            self.instance.component.unsupported(self.instance, "health service", self.node.cls, self.node)

    def tick(self):
        elapsed = max(0, self.now - self.last_applied)
        if elapsed:
            self.last_applied = self.now
            self.instance.component.world.modify_health(self, elapsed)

    def on_end(self, finished):
        self.instance.component.world.end_health(self)


@state_class("STUStatescriptStateCombatModFilter", strict_only=True)
class CombatDamageFilter(State):
    def on_enter(self):
        component = self.instance.component
        if component.world.bind_combat_damage_filter(self) is not True:
            component.unsupported(self.instance, "combat damage filter", self.node.cls, self.node)

    def on_end(self, finished):
        self.instance.component.world.unbind_combat_damage_filter(self)


@state_class("STUStatescriptStateTrackTargets")
class TrackTargets(State):
    """Target queries supplied by an explicit world bridge; other searches remain empty."""

    family = "targets"

    def __init__(self, *args):
        super().__init__(*args)
        self.targets = ()
        self.managed = False

    def on_enter(self) -> None:
        self.refresh()
        if self.managed:
            self.ticks()

    def refresh(self) -> None:
        values = self.instance.component.world.tracked_targets(self)
        if values is NotImplemented:
            return
        self.managed = True
        self.targets = tuple(values)
        self.instance.write(lvalue(self.node.field("m_8A2905A0")), tuple(Entity(e) for e in self.targets))

    def tick(self) -> None:
        self.refresh()

    def on_end(self, finished: bool) -> None:
        # Deadeye stops searching before iterating the baked target arrays.
        # Its authored ability exit resets those entity variables.
        self.targets = ()

    def payload(self) -> dict:
        return {"targets": tuple((entity, 0) for entity in self.targets)}  # age policy unverified


@state_class("STUStatescriptStatePhysicsExplosion", strict_only=True)
class PhysicsExplosion(State):
    def on_enter(self):
        component = self.instance.component
        if component.world.physics_explosion(self) is not True:
            component.unsupported(self.instance, "physics explosion service", self.node.cls, self.node)


@state_class("STUStatescriptStateTrackRay", strict_only=True)
class TrackRay(State):
    def on_enter(self):
        self.tick()
        self.ticks()

    def tick(self):
        instance, node = self.instance, self.node
        component = instance.component
        world = component.world
        provider = world.raycast_provider(instance, node)
        if provider is None:
            component.unsupported(instance, "track ray options", node.cls, node)
        origin = self.evaluate(node.field('m_3BC31C57'), watch=False)
        segment = node.field('m_F5732403') or {}
        if (instance.graph.index, node.pos) == (0x01C9, 47):
            body = self.evaluate(node.field('m_56B8547E'), watch=False)
            feet = world.position(body.id) if isinstance(body, Entity) else None
            if not _finite_vector(feet) or not _finite_vector(origin):
                component.unsupported(instance, "track ray origin", node.cls, node)
            origin = Vec3(*(f32(a + b) for a, b in zip(feet, origin, strict=True)))
        if not _finite_vector(origin):
            component.unsupported(instance, "track ray inputs", node.cls, node)
        radius = 0.0
        if segment.get('$') == 'STU_9E9028E6':
            direction = self.evaluate(segment.get('m_direction'), watch=False)
            length = self.evaluate(segment.get('m_6A79BD98'), watch=False)
            config = node.field('m_57DAE1B5')
            if config is not None:
                radius = self.evaluate(config, watch=False)
            if (not _finite_vector(direction)
                    or any(type(v) not in (float, int) or not math.isfinite(v) or v < 0
                           for v in (length, radius))):
                component.unsupported(instance, "track ray inputs", node.cls, node)
            endpoint = Vec3(*(a + b * length for a, b in zip(origin, direction, strict=True)))
        else:
            endpoint = self.evaluate(segment.get('m_BD631144'), watch=False)
            if not _finite_vector(endpoint):
                component.unsupported(instance, "track ray inputs", node.cls, node)
            length = math.dist(origin, endpoint)
            if length:
                direction = Vec3(*((b - a) / length for a, b in zip(origin, endpoint, strict=True)))
        result = RaycastResult(False, None, None, None)
        if length:
            result = provider(RaycastRequest(origin, direction, length, (), node.field('m_filter'), radius))
            if not isinstance(result, RaycastResult):
                component.unsupported(instance, "track ray service", node.cls, node)
        for field, value in (
            ('m_692DFF72', result.entity), ('m_7BA44A69', result.hit),
            ('m_750377AA', result.hit),
            ('m_A7C2505B', result.normal), ('m_F2F8606D', result.point or endpoint),
        ):
            instance.write(lvalue(node.field('m_target.' + field)), value)


@state_class("STU_BA4C2296", strict_only=True)
class BeamContact(State):
    """9875E0 returns before reading contact targets when 978A30 finds no presentation context."""

    def on_begin(self):
        instance = self.instance
        component = instance.component
        if ((instance.graph.index, self.node.pos) != (0x157C, 143)
                or component.world.outline_context_available is not False):
            component.unsupported(instance, "beam contact presentation context", self.node.cls, self.node)
        play.evaluate_scalar(instance, self.node.field("m_6EFD110C"), initial=0)


@state_class('STU_23A00A53', strict_only=True)
class ExplosionContact(State):
    """Forward the authored contact effect and refresh it when its targets change."""

    def on_enter(self):
        self.refresh_contacts()

    def reentry(self):
        self.refresh_contacts()

    def dependency_changed(self):
        self.refresh_contacts()

    def refresh_contacts(self):
        instance, node = self.instance, self.node
        component = instance.component
        if (instance.graph.index, node.pos) == (0x0C89, 49):
            expected = 0x0120000000000015
        elif instance.graph.index == 0x01C8 and node.pos in (28, 78, 89, 97):
            expected = 0x012000000000017D
        else:
            component.unsupported(instance, 'contact state options', node.cls, node)
        targets = tuple(play._iter_values(instance, node.field('m_53FCEDBF'), watcher=self))
        resource = self.evaluate(node.field('m_contactSet'), watch=False)
        source = self.evaluate(node.field('m_63CB0D07'), watch=False)
        if (source != Entity(component.entity) or resource != Asset(expected)
                or any(not isinstance(target, Entity) for target in targets)
                or component.world.emit_contact_effect(self, source.id, resource.guid, targets) is not True):
            component.unsupported(instance, 'contact effect service', node.cls, node)


@state_class("STU_431F3FCC")
class TargetEffects(State):
    """Scoped effect-instance bridge; not the complete native effect manager."""

    def __init__(self, *args):
        super().__init__(*args)
        self.children = {}

    def on_enter(self) -> None:
        result = self.instance.component.world.target_effects(self)
        if result is not NotImplemented:
            self.ticks()
        elif self.instance.component.strict:
            self.instance.component.unsupported(
                self.instance, "target effects service", self.node.cls, self.node)

    def tick(self) -> None:
        self.instance.component.world.target_effects(self)

    def on_end(self, finished: bool) -> None:
        self.clear_children()

    def clear_children(self) -> None:
        for entity in tuple(self.children):
            self.remove_child(entity)

    def remove_child(self, entity) -> None:
        child = self.children.pop(entity)
        if child.component.instances.get(child.id) is child:
            child.component.destroy(child)


@state_class("STU_691BFA55", "STU_7CA2FBAF")
class Effect(State):
    """An effect (StEC wire form): a plain state whose payload is its activation counter & 3 (reader
    0x7FF789894540; the client keeps it as its own counter when the state is on)."""

    def payload(self) -> dict:
        return {"counter": self.counter & 3}


@state_class(spray_placement.STATE_CLASS)
class SprayPlacement(State):
    """Resolve the selected spray and keep its geometry for replication."""

    def __init__(self, *args) -> None:
        super().__init__(*args)
        self.placement = {}

    def on_enter(self) -> None:
        self.reentry()

    def reentry(self) -> None:
        unlock = self.evaluate(self.node.field("m_E441DC25"), watch=False)
        length = expr.to_float(self.evaluate(self.node.field("m_7F47658A"), watch=False))
        hit = None
        if isinstance(unlock, Asset) and unlock.guid >> 48 == 0x0250 and math.isfinite(length) and length > 0:
            hit = self.instance.component.world.resolve_spray(length)
        if hit is None:
            self.instance.follow(self.node, "m_9254FB21", self)
            self.request(ABORT)
            return
        # The client resolves the look when it reads the placement.
        self.placement = dict(hit, unlock=unlock.guid)
        self.instance.follow(self.node, "m_A39D2005", self)

    def on_end(self, finished: bool) -> None:
        self.placement = {}

    def payload(self) -> dict:
        return self.placement


WHEEL_VOICE_PULSE = (0x0686, 72)


@state_class("STU_B9898052")
class ClientOnlyPulser(State):
    """Pulses its client-only subgraph (vtable 0x7FF78B549DE8): OnEnter and every begin while active
    (0x7FF78990D6D0) pulse once, and the client follows its plug m_B6A27F9A once per pulse
    (0x7FF78990D872). Its payload (reader 0x7FF78990D760) is a bit and the 8-bit count of pulses: a count
    that moved on pulses the client as often (mod 256); a state the client reads for the first time pulses
    once only with the bit set (or the node's m_C85E2CBA, node +0xF0), else it takes the count as seen.
    The server marks recent pulses after the instance's first simulation frame as fresh. The wheel
    creates its voice instance in response to the action, so that first pulse also requests playback.
    The original server's freshness policy is unverified."""

    FRESH_MS = 500

    def __init__(self, *args) -> None:
        super().__init__(*args)
        self.pulses = 0
        self.pulsed_at: int | None = None

    def on_enter(self) -> None:
        self.pulse()

    def reentry(self) -> None:
        self.pulse()

    def pulse(self) -> None:
        self.pulses = (self.pulses + 1) & 0xFF
        frame_ms = self.instance.component.frame_ms
        if (self.instance.graph.index, self.node.pos) == WHEEL_VOICE_PULSE:
            self.pulsed_at = self.now
            self.instance.component.world.replicate_wheel_event(self.instance, self.node)
        elif self.now // frame_ms > self.instance.start // frame_ms:
            self.pulsed_at = self.now

    def payload(self) -> dict:
        fresh = self.pulsed_at is not None and self.now - self.pulsed_at <= self.FRESH_MS
        return {"fresh": fresh, "count": self.pulses}


@state_class("STU_01178155")
class ActiveQuery(State):
    """A link into its m_inPlug (an STU_A1AAB8C4 input plug) does not begin it: it follows m_7273C81B when
    the state is on and m_C6F3744E when it is off (vt+0x198 0x7FF78AAA8920, called by the base plug enter
    0x7FF78AA83D80 for every input plug that is not the abort plug). 01CF st70 is one: on while 01CF waits
    for 0A43.025, it pulses st108 (the kill feed line) when 003E.025 comes."""

    def plug_enter(self, input_path: str) -> bool:
        if input_path == "m_inPlug":
            self.instance.follow(self.node, "m_7273C81B" if self.active else "m_C6F3744E", self)
            return True
        return super().plug_enter(input_path)


@state_class("STU_4C96470A", strict_only=True)
class SummonerPlacement(State):
    """Confirm the map-authored placement sent by the Summoner's controller."""

    def on_created(self):
        self.confirmed = False
        if (self.instance.graph.index, self.node.pos) != (0x156B, 57):
            self.instance.component.unsupported(self.instance, "state", self.node.cls, self.node)

    def on_enter(self):
        self.ticks()

    def plug_enter(self, input_path: str) -> bool:
        if input_path != "m_6647ED22":
            return super().plug_enter(input_path)
        if self.active:
            self.confirmed = True
        return True

    def tick(self):
        if not self.confirmed:
            return
        instance, node = self.instance, self.node
        place = getattr(instance.component.world, 'summoner_placement', None)
        point = place(instance) if place is not None else NotImplemented
        if point is NotImplemented:
            instance.component.unsupported(instance, "Summoner placement source", node.cls, node)
        valid = point is not None
        if valid:
            instance.write(expr.dynamic(node.field('m_2FECD1E3')), expr.Entity(0))
            instance.write(expr.dynamic(node.field('m_1549F0F9')), expr.Entity(0))
            instance.write(expr.dynamic(node.field('m_30710B76')), point)
        instance.write(expr.dynamic(node.field('m_2339572B')), valid)
        self.confirmed = False
        instance.follow(node, 'm_F6955E81' if valid else 'm_425337C6', self)

    def on_cleanup(self):
        self.confirmed = False

    def payload(self):
        return {'m_B4802CE3': int(self.confirmed)}


@state_class("STUStatescriptStatePet", strict_only=True)
class SummonerBarrierPet(State):
    """Own the lifetime of the Summoner's authored Photon Barrier entity."""

    def on_created(self):
        self.retire = None
        if (self.instance.graph.index, self.node.pos) != (0x156B, 17):
            self.instance.component.unsupported(self.instance, "state", self.node.cls, self.node)

    def on_enter(self):
        instance = self.instance
        create = getattr(instance.component.world, 'create_summoner_barrier', None)
        if create is None:
            instance.component.unsupported(instance, "Summoner barrier host", self.node.cls, self.node)
        self.retire = create(self)

    def on_cleanup(self):
        retire, self.retire = self.retire, None
        if retire is not None:
            retire()

    on_failed = on_cleanup
    on_destroyed = on_cleanup


@state_class("STU_5E805B5A")
class RecentInputState(State):
    """D43000 starts with an empty input history. D4F130 handles active input."""

    def on_enter(self) -> None:
        self.history = []

    def plug_enter(self, input_path: str) -> bool:
        if input_path != 'm_F4DD996D':
            return super().plug_enter(input_path)
        component = self.instance.component
        if self.active and component.strict:
            if self.instance.graph.index != 0x0CBA:
                component.unsupported(self.instance, 'input history update', self.node.cls, self.node)
            self.select_spawn_position()
        return True

    def select_spawn_position(self) -> None:
        instance = self.instance
        output = lvalue(self.node.field('m_C51AF683'))
        lifetime = self.evaluate(self.node.field('m_82317B5F'), watch=False)
        positions = tuple(play._iter_values(instance, self.node.field('m_814F8EC3')))
        if (output is None or not _finite_number(lifetime)
                or any(not _finite_vector(pos) for pos in positions)):
            instance.component.unsupported(instance, 'spawn history inputs', self.node.cls, self.node)
        lifetime_ms = math.floor(lifetime * 1000 + 0.5) if lifetime >= 0 else math.ceil(lifetime * 1000 - 0.5)
        history = self.history
        for index in reversed(range(len(history))):
            if self.now - history[index][1] > lifetime_ms:
                history[index] = history[-1]
                history.pop()
        selected = None
        if positions:
            if len(history) >= len(positions):
                oldest = min(range(len(history)), key=lambda index: history[index][1])
                history[oldest] = history[-1]
                history.pop()
            used = {slot: index for index, (slot, _) in enumerate(history)}
            first = instance.component.rng.randrange(len(positions))
            slot = first
            # The candidate array can shrink while older slot indices are still recorded.
            while slot in used:
                slot = (slot + 1) % len(positions)
                if slot == first:
                    break
            if lifetime_ms > 0:
                if slot in used:
                    history[used[slot]] = (slot, self.now)
                else:
                    history.append((slot, self.now))
            selected = positions[slot]
        instance.write(output, selected)
        instance.follow(self.node, 'm_1405CDCB', self)


@state_class("STU_7B9E8909")
class ActiveOutput(State):
    """The 104319 input hook at 1B97E10 consumes input and forwards only while active."""

    def plug_enter(self, input_path: str) -> bool:
        if input_path == "m_inPlug":
            if self.active:
                self.instance.follow(self.node, "m_outPlug", self)
            return True
        return super().plug_enter(input_path)


@state_class("STU_160F2578")
class StaticTagTarget(State):
    """Publish the scene query's entity array."""

    def on_enter(self) -> None:
        component = self.instance.component
        output = lvalue(self.node.field("m_4AEEE6A1"))
        filter_config = self.node.field('m_filter')
        if (isinstance(filter_config, dict) and filter_config.get('$') == 'STUConfigVarFilter'
                and all(self.node.field(key) is None for key in ('m_9C7BD8B8', 'm_B486603E', 'm_8B2A4DF3'))):
            required_cfg = filter_config.get('m_B486603E')
            required = self.evaluate(required_cfg, watch=False) if required_cfg is not None else None
            targets = NotImplemented
            if required_cfg is None or (isinstance(required, Asset) and required.guid >> 48 == 0x0D60):
                targets = component.world.query_participants(
                    self.instance, filter_config, required_tag=required)
        elif ((self.instance.graph.index, self.node.pos) in ((0x00CA, 33), (0x2ECF, 29))
              and filter_config is None and self.node.field('m_B486603E') is None):
            tag = self.evaluate(self.node.field('m_9C7BD8B8'), watch=False)
            excluded = self.evaluate(self.node.field('m_8B2A4DF3'), watch=False)
            targets = NotImplemented
            if all(isinstance(value, Asset) and value.guid >> 48 == 0x0D60 for value in (tag, excluded)):
                targets = component.world.targets_with_tag(self.instance, tag.guid, excluded_tag=excluded.guid)
        elif (self.instance.graph.index == 0x0C8F and filter_config is None
              and self.node.field('m_8B2A4DF3') is None):
            tag = self.evaluate(self.node.field('m_9C7BD8B8'), watch=False)
            required = self.evaluate(self.node.field('m_B486603E'), watch=False)
            targets = NotImplemented
            if required is None or required == ():
                if tag is None:
                    targets = component.world.static_tag_targets(None)
                elif isinstance(tag, Asset) and tag.guid >> 48 == 0x0D60:
                    targets = component.world.static_tag_targets(tag.guid)
        else:
            target = component.world.evaluate_static_target_tag(
                self.node.field, lambda cfg: self.evaluate(cfg, watch=False))
            targets = (target,) if isinstance(target, Entity) else NotImplemented
        if output is None or targets is NotImplemented:
            if component.strict:
                component.unsupported(self.instance, 'bound target query', self.node.cls, self.node)
            return
        self.instance.write(output, targets)


# --- actions and conditions ------------------------------------------------------------------------


@handler('STU_42578342')
def collect_targets(instance: Instance, node, caller) -> bool:
    """Write the collected entity array before following the action output."""
    component = instance.component
    if not component.strict:
        return NotImplemented
    filter_config = node.field('m_filter')
    output = lvalue(node.field('m_4AEEE6A1'))
    if (output is None or node.field('m_ADEB6E05') != 1 or node.field('m_2BBEEAB8') not in (None, 0)
            or any(node.field(key) is not None for key in ('m_9C7BD8B8', 'm_8B2A4DF3'))):
        return NotImplemented
    required = node.field('m_B486603E')
    if (required is None and isinstance(filter_config, dict)
            and filter_config.get('$') == 'STUConfigVarFilter'):
        targets = component.world.query_participants(instance, filter_config)
    elif (instance.graph.index, node.pos) == (0x0C95, 9) and filter_config is None:
        tag = instance.evaluate(required)
        if tag != Asset(0x0D60000000000321):
            return NotImplemented
        # CB8EC0 uses the required tag list at DB3850 even without a participant filter.
        targets = component.world.static_tag_targets(tag.guid)
    else:
        return NotImplemented
    if targets is NotImplemented:
        component.unsupported(instance, 'target collection service', node.cls, node)
    instance.write(output, targets)
    instance.follow(node, 'm_outPlug', None)
    return True


_RAYCAST_OUTPUTS = ("m_out_HitSomething", "m_out_HitEntity", "m_out_HitPointWS", "m_out_HitNormal")


def _finite_number(value) -> bool:
    try:
        return type(value) in (int, float) and math.isfinite(value)
    except OverflowError:
        return False


def _finite_vector(value) -> bool:
    return isinstance(value, Vec3) and all(_finite_number(axis) for axis in value)


def _ray_entity(value) -> bool:
    return isinstance(value, Entity) and type(value.id) is int and 0 <= value.id <= 0xFFFFFFFF


def _raycast_request(instance: Instance, node) -> RaycastRequest | None:
    kind = node.field("m_type")
    unsupported = ("m_castRadius", "m_3B0AD973", "m_2158DFDE")
    if type(kind) is not int or kind != 0 or any(node.field(field) is not None for field in unsupported):
        return None
    option = node.field("m_8CBE558B")
    if option is not None and (
        not isinstance(option, dict) or option.get("$") != "STUConfigVarBool" or option.get("m_value") != 0
    ):
        return None
    filter_config = node.field("m_castFilter")
    if filter_config is not None and (
        not isinstance(filter_config, dict) or filter_config.get("$") != "STUConfigVarFilter"
    ):
        return None
    origin = instance.evaluate(node.field("m_startPosWS"))
    direction = instance.evaluate(node.field("m_dirWS"))
    length = instance.evaluate(node.field("m_castLength"))
    if not _finite_vector(origin) or not _finite_vector(direction) or not any(direction):
        return None
    if not _finite_number(length) or length <= 0:
        return None
    ignores = node.field("m_ignoreEntities")
    if ignores is not None and not isinstance(ignores, list):
        return None
    entities = [instance.evaluate(config) for config in ignores or ()]
    if any(not _ray_entity(entity) for entity in entities):
        return None
    return RaycastRequest(
        origin, direction, float(length), tuple(entity.id for entity in entities), filter_config
    )


@handler("STU_08D6BA6E")
def raycast_action(instance: Instance, node, caller) -> bool | NotImplementedType:
    """Run the authored type-0 cast when a provider can complete it."""
    provider = instance.component.world.raycast_provider(instance, node)
    if provider is None:
        return NotImplemented
    targets = []
    for field in _RAYCAST_OUTPUTS:
        config = node.field(field)
        try:
            target = lvalue(config)
        except (IndexError, TypeError, ValueError):
            return NotImplemented
        if config is not None and (target is None or target in targets):
            return NotImplemented
        targets.append(target)
    request = _raycast_request(instance, node)
    if request is None:
        return NotImplemented
    result = provider(request)
    if not isinstance(result, RaycastResult) or type(result.hit) is not bool:
        return NotImplemented
    if result.entity is not None and not _ray_entity(result.entity):
        return NotImplemented
    if any(value is not None and not _finite_vector(value) for value in (result.point, result.normal)):
        return NotImplemented
    for target, value in zip(targets, (result.hit, result.entity, result.point, result.normal), strict=True):
        if target is not None:
            instance.write(target, value)
    instance.follow(node, "m_outPlug", None)
    return True


@handler("STU_FF3471E9")
def velocity_action(instance: Instance, node, caller) -> bool | NotImplementedType:
    result = instance.component.world.velocity_action(instance, node)
    if result is NotImplemented:
        return NotImplemented
    if result is True:
        instance.follow(node, "m_outPlug", None)
    return result


@handler("STUStatescriptActionSetVar")
def set_var(instance: Instance, node, caller) -> bool:
    """SetVar (0x7FF78AA7D300): the value (failure: int 0), then a keyed insert, an element write or a
    plain assignment with the value's own type; then m_outPlug."""
    config = node.fields.get("m_value")
    target = node.fields.get("m_out_Var")
    # Native 1B6D325..353 skips assignment when either pointer is absent. The
    # value wrapper at 1B6D381 resets a failed evaluation to type 2 (int 0).
    if config is not None and target is not None:
        component = instance.component
        difficulty = component.world.junkenstein_difficulty
        if (difficulty is not None and component.strict and component.entity == GAME_MODE_ENTITY
                and instance.graph.index == 0x0C8C and node.pos == 263
                and config == {'$': 'STU_7BD2CA19', 'm_resourceKey': None, 'm_EE729DCB': 0}):
            value = difficulty  # Supply the queue input at the original encounter assignment.
        else:
            value = play.evaluate_value(instance, config, initial=0)
        store(instance, target, value, node.fields.get("m_index"), node.fields.get("m_key"))
    instance.follow(node, "m_outPlug", None)
    return True


def store(instance: Instance, out_cfg, value, index_cfg=None, key_cfg=None) -> None:
    target = lvalue(out_cfg)
    if target is None:
        return
    if key_cfg is not None:
        key = instance.evaluate(key_cfg)
        current = instance.find_var(*target).value()
        items = list(current.items) if isinstance(current, expr.Map) else []
        items = [(k, v) for k, v in items if not equal(k, key)] + [(key, value)]
        instance.write(target, expr.Map(tuple(items)))
        return
    if index_cfg is not None:
        index = to_int(instance.evaluate(index_cfg)) if isinstance(index_cfg, dict) else int(index_cfg)
        if index >= 0:
            current = instance.find_var(*target).value()
            items = list(current) if isinstance(current, tuple) else ([] if current is None else [current])
            while len(items) <= index:
                items.append(None)
            items[index] = value
            instance.write(target, tuple(items))
            return
    instance.write(target, value)


@handler("STU_12F5D52D")
def set_vars(instance: Instance, node, caller) -> bool:
    """1B6B560: each present target gets its +28 value, or int0 for missing/failure."""
    for pair in node.fields.get("m_143D4C5B") or []:
        if isinstance(pair, dict) and pair.get("m_out_Var") is not None:
            config = pair.get("m_value")
            value = play.evaluate_value(instance, config, initial=0) if config is not None else 0
            store(instance, pair["m_out_Var"], value)
    instance.follow(node, "m_outPlug", None)
    return True


@handler("STU_4C3509FB", "STU_CBE8BDAA")
def insert_array(instance: Instance, node, caller) -> bool | NotImplementedType:
    """Native unkeyed insertion/merge; keyed storage and checkpoints stay explicit."""
    if not instance.component.strict:
        return NotImplemented
    array_actions.insert(instance, node, caller)
    instance.follow(node, 'm_outPlug', caller)
    return True


@handler("STU_1C2CC6D5")
def clear_array(instance: Instance, node, caller) -> bool | NotImplementedType:
    """Assign a fresh empty array with the normal typed-value writer."""
    if not instance.component.strict:
        return NotImplemented
    array_actions.clear(instance, node)
    instance.follow(node, 'm_outPlug', caller)
    return True


@handler("STU_BC5E4622")
def cycle_index(instance: Instance, node, caller) -> bool:
    """CycleIndex: from the current index (m_339A63DB) step forward or back (m_C78954CB) through
    [0, count) and keep the first candidate whose condition holds; the candidate is the parameter the
    condition reads. None: the fail index (m_A53FE9DA, -1)."""
    count = to_int(instance.evaluate(node.fields.get("m_D10618D1")))
    target = lvalue(node.fields.get("m_339A63DB"))
    backwards = truthy(instance.evaluate(node.fields.get("m_C78954CB")))
    fail = node.fields.get("m_A53FE9DA")
    result = to_int(instance.evaluate(fail)) if fail is not None else -1
    if count > 0 and target is not None:
        current = to_int(instance.find_var(*target).value())
        current = min(max(current, 0), count - 1)
        step = -1 if backwards else 1
        for k in range(1, count + 1):
            candidate = (current + step * k) % count
            instance.params.append({None: candidate})
            try:
                ok = truthy(instance.evaluate(node.fields.get("m_condition")))
            finally:
                instance.params.pop()
            if ok:
                result = candidate
                break
    if target is not None:
        instance.write(target, result)
    instance.follow(node, "m_outPlug", None)
    return True


def branch(instance: Instance, node, caller, result: bool) -> bool:
    """Condition execute (0x7FF78AA83CB0): a false result is taken when a false link is; a true one when a
    true link is, or when neither plug has links."""
    if not result:
        return instance.follow(node, "m_falsePlug", caller)
    if instance.follow(node, "m_truePlug", caller):
        return True
    return not node.links("m_truePlug") and not node.links("m_falsePlug")


@handler("STU_1113EBD5")
def join_inputs(instance: Instance, node, caller) -> bool:
    return instance.join(node, caller)


@handler("STU_3387AB5D")
def condition_bool(instance: Instance, node, caller) -> bool:
    """Condition_Bool: m_condition, evaluated with the calling state as the watcher."""
    if instance.graph.index == 0x0C87 and node.pos == 21:
        # A server-owned route can supply the guard that falls back to a
        # no-output B40ACFBA config on the 104319 client.
        active = instance.component.world.objective_route_active(instance)
        if type(active) is bool:
            return branch(instance, node, caller, active)
    return branch(instance, node, caller, truthy(instance.evaluate(node.fields.get("m_condition"), caller)))


@handler('STU_8502CA73')
def mystery_swap_count(instance: Instance, node, caller) -> bool | NotImplementedType:
    """104319 1B6D010 draws from cumulative weights, then follows the common output."""
    if (instance.graph.index, node.pos) != (0x0C8C, 307):
        return NotImplemented
    limits = []
    total = 0.0
    for row in node.field('m_D84D6E03'):
        instance.component.consume_work('Mystery Swap weight')
        weight = play.evaluate_scalar(instance, row.get('m_weight'), initial=0)
        total = expr.f32(total + expr.to_float(weight, 0.0))
        limits.append(total)
    draw = expr.f32(instance.component.rng.uniform(0, total))
    for index, limit in enumerate(limits):
        if draw < limit:
            instance.follow(node, f'm_D84D6E03[{index}]', caller)
            break
    instance.follow(node, 'm_outPlug', caller)
    return True


@handler('STU_DA54F8F7')
def mystery_swap_remove_candidate(instance: Instance, node, caller) -> bool | NotImplementedType:
    """104319 1B6C00B/D56020 remove one row at the evaluated index in order."""
    if (instance.graph.index, node.pos) != (0x0C8C, 305):
        return NotImplemented
    target = lvalue(node.field('m_26E3B366'))
    if target is None:
        return NotImplemented
    slot = instance.find_var(*target, create=False)
    if slot is not None and isinstance(values := slot.value(), tuple):
        index = expr.to_int(play.evaluate_scalar(instance, node.field('m_index'), initial=-1), -1)
        if 0 <= index < len(values):
            instance.write(target, values[:index] + values[index + 1:])
    instance.follow(node, 'm_outPlug', caller)
    return True


@handler("STU_821116A6")
def contains(instance: Instance, node, caller) -> bool:
    """The container m_814F8EC3 has an element equal to m_value (eval 0x7FF78AA85A30)."""
    container = instance.evaluate(node.fields.get("m_814F8EC3"), caller)
    value = instance.evaluate(node.fields.get("m_value"), caller)
    return branch(instance, node, caller, expr.contains_value(container, value))


@handler("STU_7DEB522D")
def button_held(instance: Instance, node, caller) -> bool:
    """The held condition checks active button blocks first (104319 1B75954)."""
    button = to_int(instance.evaluate(node.fields.get("m_logicalButton")))
    component = instance.component
    return branch(instance, node, caller, button in component.held and not component.disabled.get(button))


@handler("STU_4E2BCAAB")
def state_elapsed(instance: Instance, node, caller) -> bool:
    """104319 1B75730 compares state age and queues a timer when it is too early."""
    seconds = instance.evaluate(node.field('m_AA6E611E'), caller, initial=-1.0)
    if not _finite_number(seconds) or not 0 <= seconds < 2_147_483:
        return branch(instance, node, caller, False)
    state = caller
    if state is None and instance.parent is not None:
        state = instance.parent.states.get(instance.parent_state)
    delay = seconds_to_ms(seconds)
    ready = state is not None and max(0, state.now - state.start) >= delay
    if not ready and caller is not None:
        queue = caller.instance.queue
        queue[:] = [event for event in queue if not (event.kind == TIMER and event.state is caller
                                                     and event.param == 0)]
        caller.timer_at(caller.start + delay - caller.now, param=0)
    return branch(instance, node, caller, ready)


@handler("STUStatescriptActionSwitch")
def action_switch(instance: Instance, node, caller) -> bool:
    """Follow the indexed or matching case, then the common output."""
    value = instance.evaluate(node.fields.get("m_262AE982"))
    cases = node.fields.get("m_FDD34C25") or []
    case_number = None
    if node.fields.get("m_F0F0720B") == 1:
        if type(value) is int and 0 <= value < len(cases):
            case_number = value
    else:
        for number, case in enumerate(cases):
            if isinstance(case, dict) and equal(value, instance.evaluate(case.get("m_value"))):
                case_number = number
                break
    branch_plug = "m_CD38D5DB" if case_number is None else f"m_FDD34C25[{case_number}]"
    instance.follow(node, branch_plug, None)
    instance.follow(node, "m_outPlug", None)
    return True


@handler('STU_0F647E55')
def assign_entity_team(instance: Instance, node, caller) -> bool:
    """Forward the authored TeamIndex and target entities to the scene owner."""
    component = instance.component
    if component.strict:
        team, success = play.evaluate_scalar_result(instance, node.field('m_67465E75'))
        if not success or type(team) is not int or not -1 <= team <= 4:
            component.unsupported(instance, 'entity team assignment index', node.cls, node)
        for value in tuple(play._iter_values(instance, node.field('m_targets'))):
            target = expr.to_entity(value) & 0xFFFFFFFF
            if expr.valid_entity(target):
                if component.world.set_script_entity_team(instance, target, team) is not True:
                    component.unsupported(instance, 'entity team assignment service', node.cls, node)
                component.ensure_valid()
    instance.follow(node, 'm_outPlug', None)
    return True


@handler('STU_6E18DEA0')
def stop_gate_animation(instance: Instance, node, caller) -> bool:
    """CBB670/90C830 have no animation to stop when the handle is null."""
    if instance.graph.index != 0x0CA7 or node.field('m_A9220BE5') is not None:
        return NotImplemented
    handle = play.evaluate_scalar(instance, node.field('m_EDB2BD2A'))
    if handle is not None and handle != 0:
        return NotImplemented
    instance.follow(node, 'm_outPlug', None)
    return True


@handler('STU_523EE38D')
def retire_junkenstein_corpse(instance: Instance, node, caller) -> bool:
    component = instance.component
    if (component.strict and component.entity == GAME_MODE_ENTITY
            and (instance.graph.index, node.pos) == (0x0C8C, 327)):
        retire = getattr(component.world, 'retire_vengeful_ghost', None)
        entity = play.evaluate_scalar(instance, node.field('m_entity'))
        if retire is not None and isinstance(entity, Entity) and retire(entity.id):
            return no_op(instance, node, caller)
        return NotImplemented
    if (component.strict and instance.graph.index == 0x01C7 and node.pos in (33, 92)
            and expr.dynamic(node.field('m_entity')) == (expr.INSTANCE, 669)):
        entity = play.evaluate_scalar(instance, node.field('m_entity'))
        # The weapon cleanup can run before it owns a temporary entity.
        if entity is None or entity == Entity(0):
            return no_op(instance, node, caller)
        return NotImplemented
    if component.strict and (instance.graph.index, node.pos) == (0x181D, 1):
        query = node.field('m_entity') or {}
        tag = play.evaluate_scalar(instance, query.get('m_B486603E'))
        if (query.get('$') == 'STU_B3E67A36' and isinstance(tag, Asset) and tag.guid == 0x0D60000000000044
                and all(query.get(key) is None for key in ('m_9C7BD8B8', 'm_8B2A4DF3', 'm_filter'))
                and component.world.static_tag_targets(tag.guid) == ()):
            # This helper inherits DD856C32. The authored tag has no bound map target.
            return no_op(instance, node, caller)
        return NotImplemented
    if not component.strict or (instance.graph.index, node.pos) not in (
        (0x0C89, 43), (0x0C93, 11), (0x0CBC, 36), (0x0CBC, 38), (0x0CBC, 57), (0x0CBC, 58),
    ):
        return NotImplemented
    entity = play.evaluate_scalar(instance, node.field('m_entity'))
    # The 06BE phase-change branch clears its wave pair while the body can still be alive.
    phase_exit = instance.graph.index == 0x0CBC and node.pos in (57, 58)
    if (not isinstance(entity, Entity)
            or instance.component.world.request_corpse_retirement(
                instance, entity.id, phase_exit=phase_exit,
            ) is not True):
        instance.component.unsupported(instance, 'enemy retirement service', node.cls, node)
    instance.follow(node, 'm_outPlug', None)
    return True


@handler('STU_E0687628')
def run_effect_entity_action(instance: Instance, node, caller) -> bool:
    # 104319: 97EA70 resolves an asset and creates an effect entity.
    component = instance.component
    if not component.strict or component.world.run_effect_entity_action(instance, node) is not True:
        return NotImplemented
    instance.follow(node, 'm_outPlug', None)
    return True


@handler('STU_100EF8C2')
def send_client_only_game_message(instance: Instance, node, caller) -> bool:
    """Project the authored Junkenstein outcome messages to the client VM."""
    component = instance.component
    if (component.strict and instance.graph.index == 0x0C8C and node.pos in (265, 267, 268)
            and callable(component.world.queue_mode_action)):
        component.world.queue_mode_action(instance, node)
    return no_op(instance, node, caller)


@handler('STU_22926252')
def request_instance_stop(instance: Instance, node, caller) -> bool:
    """1B6C8F0 queues operation 7 for the default self-stop form."""
    handled = instance.component.world.finish_wheel_emote(instance, node)
    if handled is not NotImplemented:
        return handled
    if node.field('m_67B81D25') is not None or node.field('m_EB35D2F1') is not None:
        return NotImplemented
    instance.post(Event(instance.component.now, 0, STOP))
    instance.follow(node, 'm_outPlug', caller)
    return True


@handler('STU_3D419B63')
def voice_category_action(instance: Instance, node, caller) -> bool:
    """CB9C70 resolves the voice service before collecting authored targets."""
    component = instance.component
    if instance.graph.index == 0x069A and node.pos in (7, 8):
        return component.world.finish_wheel_emote(instance, node)
    if not component.strict:
        instance.follow(node, 'm_outPlug', None)
        return True
    service = component.world.voice_category_service(instance)
    if service is None:
        return False
    if not callable(service):
        component.unsupported(instance, 'voice category service', node.cls, node)
    raw = node.field('m_category')
    category = guid(raw) if raw is not None else 0
    if category is None:
        component.unsupported(instance, 'voice category asset', node.cls, node)
    targets = []
    for value in play._iter_values(instance, node.field('m_targets')):
        target = expr.to_entity(value) & 0xFFFFFFFF
        if expr.valid_entity(target):
            targets.append(target)
    for target in targets:
        component.consume_work('voice category dispatch')
        if component.world.voice_category_service(instance) is not service:
            component.unsupported(instance, 'voice category service retired', node.cls, node)
        service(target, category)
        component.ensure_valid()
    if component.world.voice_category_service(instance) is not service:
        component.unsupported(instance, 'voice category service retired', node.cls, node)
    instance.follow(node, 'm_outPlug', None)
    return True


@handler("STU_1470EF32")
def phase_client_message(instance: Instance, node, caller) -> bool:
    component = instance.component
    if component.strict:
        publish = component.world.queue_mode_action
        message = play.evaluate_scalar(instance, node.field("m_gameMessage"))
        if ((instance.graph.index, node.pos) != (0x180A, 47) or play._asset(message) != 0x02400000000007F1
                or node.field("m_params") or node.field("m_8B34E9C3") is not None
                or not callable(publish)):
            component.unsupported(instance, "scene client message", node.cls, node)
        publish(instance, node)
    return no_op(instance, node, caller)


@handler('STU_57692020')
def routed_game_message(instance: Instance, node, caller) -> bool:
    if instance.component.strict:
        game_message.execute(instance, node, routed=True)
        instance.follow(node, 'm_outPlug', None)
    return True


def message_params(instance: Instance, node) -> dict:
    params = {}
    for item in node.fields.get("m_params") or []:
        if isinstance(item, dict):
            params[expr.guid_of(item.get("m_B5051BCE")) & 0xFFFF] = instance.evaluate(item.get("m_value"))
    return params


def send(instance: Instance, node, caller) -> None:
    """Send a game message to each target, through the world for other entities."""
    message = play._asset(play.evaluate_scalar(instance, node.fields.get("m_gameMessage")))
    if not message:
        return
    params = message_params(instance, node)
    component: Component = instance.component
    targets = node.fields.get("m_targets", node.fields.get("m_target"))
    target = instance.evaluate(targets) if targets is not None else expr.Entity(component.entity)
    if isinstance(target, tuple):
        entities = (
            item.id for item in target if isinstance(item, expr.Entity) and expr.valid_entity(item.id)
        )
    else:
        entities = (expr.to_entity(target),)
    for entity in entities:
        if entity == component.entity:
            send_message(component, message, params, component.entity)
        elif entity:
            if component.strict:
                component.world.send_script_message(instance, node, entity, message, params, component.entity)
            else:
                component.world.send_message(entity, message, params, component.entity)


@handler("STU_187D5125")
def stop_ability(instance: Instance, node, caller) -> bool:
    """ActionStopAbility: the running abilities of the instance stop (one per graph in the hero
    data)."""
    for state in list(instance.states.values()):
        if isinstance(state, Ability) and state.active:
            state.stop()
    instance.follow(node, "m_outPlug", None)
    return True


@handler("STUStatescriptActionSendGameMessage")
def send_game_message(instance: Instance, node, caller) -> bool:
    if instance.component.strict:
        game_message.execute(instance, node)
    else:
        send(instance, node, caller)
    instance.follow(node, "m_outPlug", None)
    return True


@handler("STU_BB4AC733")
def script_start_entry(instance: Instance, node, caller) -> bool:
    # Native 1B6EC40 only follows the entry's output. Bindings were queued by
    # the invocation path before this event, not evaluated while following it.
    instance.follow(node, "m_outPlug", None)
    return True


@handler("STUStatescriptActionPlayScript")
def play_script(instance: Instance, node, caller) -> bool:
    instance.component.world.play_script(instance, node)
    instance.follow(node, "m_outPlug", None)
    return True


@handler("STUStatescriptActionCreateEntity")
def create_entity(instance: Instance, node, caller) -> bool:
    component = instance.component
    if not component.strict:
        return NotImplemented
    hero_cfg = node.field("m_A1A715A6")
    extra_configs = (
        "m_F7621CAB", "m_40AF7E2D", "m_EC1E473E", "m_3E3E203A", "m_888BCD8B", "m_AA26E7DD",
        "m_A3A50DF2", "m_373DBF32", "m_E2BF1226", "m_0FDD45BB",
        "m_481C68E7",
    )
    if hero_cfg is None:
        extra_configs += ("m_1C558638", "m_3EBF988B", "m_F8E56B94", "m_DE3B4F01", "m_D7F85181", "m_246C47AF")
    else:
        extra_configs += ("m_entityDef",)
    output = node.field("m_E95B313B")
    target = lvalue(output)
    if (any(node.field(key) is not None for key in extra_configs)
            or any(node.field(key) for key in ("m_A086DB58", "m_294B652A", "m_607F6292"))
            or node.field("m_51AB9D21") not in (None, 5)
            or (output is not None and target is None)):
        component.unsupported(instance, "entity creation options", node.cls, node)
    position = play.evaluate_scalar(instance, node.field("m_position"))
    if isinstance(position, Entity):
        position = component.world.position(position.id)
    if not _finite_vector(position):
        component.unsupported(instance, "entity creation transform", node.cls, node)
    if hero_cfg is None:
        definition = play.evaluate_scalar(instance, node.field("m_entityDef"), initial=Asset(0))
        if (not isinstance(definition, Asset) or definition.guid >> 48 != 0x0400
                or not definition.guid & 0xFFFFFFFFFFFF):
            component.unsupported(instance, "entity creation resource", node.cls, node)
        created = component.world.create_script_entity(instance, node, definition.guid, position)
    else:
        hero = play.evaluate_scalar(instance, hero_cfg, initial=Asset(0))
        team = play.evaluate_scalar(instance, node.field("m_3EBF988B"))
        yaw = play.evaluate_scalar(instance, node.field("m_1C558638"), initial=0.0)
        flags = tuple(play.evaluate_scalar(instance, node.field(key)) for key in ("m_F8E56B94", "m_DE3B4F01"))
        if (not isinstance(hero, Asset) or hero.guid >> 48 != 0x02E0 or not hero.guid & 0xFFFFFFFFFFFF
                or type(team) is not int or team not in (0, 1) or not _finite_number(yaw)):
            component.unsupported(instance, "hero creation identity or transform", node.cls, node)
        resources = {}
        for key, name, kind in (('m_D7F85181', 'controller_definition', 0x0400),
                                ('m_246C47AF', 'skin_theme', 0x0A50)):
            config = node.field(key)
            if config is None:
                continue
            resource = play.evaluate_scalar(instance, config, initial=Asset(0))
            if resource is None:
                continue
            if not isinstance(resource, Asset) or (resource.guid and resource.guid >> 48 != kind):
                component.unsupported(instance, 'hero creation resource', key, node)
            if resource.guid:
                resources[name] = resource.guid
        created = component.world.create_script_hero(instance, node, hero.guid, position, team, yaw, flags,
                                                     **resources)
    if created is not None and target is not None:
        instance.write(target, created)
    instance.follow(node, "m_outPlug", None)
    return True


def no_op(instance: Instance, node, caller) -> bool:
    """Actions with no effect on the server's graph state (effects, stats, client-only messages)."""
    instance.follow(node, "m_outPlug", None)
    return True


@handler("STU_9F3FE778")
def forward_trace_action(instance: Instance, node, caller) -> bool:
    # 104319 dispatch 9095A0 calls D34A80, which succeeds without reading the config.
    instance.follow(node, "m_outPlug", caller)
    return True


@handler("STU_631D36C3", "STU_94771561")
def wheel_base_action(instance: Instance, node, caller) -> bool:
    authored = (instance.graph.index, node.pos)
    if node.cls == "STU_631D36C3":
        supported = authored == (0x05AD, 165)
    else:
        supported = authored in ((0x023A, 108), (0x02CF, 39), (0x0686, 70))
    if not supported or not node.is_action or node.remote != -1:
        return NotImplemented
    # The PC factory selects the base Action. Its prediction record contains
    # only the raw node index and instance ID, and its rewind hook is empty.
    return no_op(instance, node, caller)


@handler("STU_43B63AD0")
def ultimate_base_action(instance: Instance, node, caller) -> bool:
    if instance.graph.index != 0x0B8B or node.pos not in (14, 16) or not node.is_action or node.remote != -1:
        return NotImplemented
    # Typeinfo 2DE3548 inherits the Action factory at 1B72650 in 104319.
    return no_op(instance, node, caller)


@handler("STU_7106C356", "STUStatescriptActionEffect")
def wheel_presentation_action(instance: Instance, node, caller) -> bool:
    if (node.cls == "STU_7106C356"
            or (instance.graph.index, node.pos) in (
                (0x02CF, 3), (0x02CF, 8), (0x05AD, 14),
                (0x0686, 14), (0x0686, 15), (0x0686, 40), (0x0686, 41),
            )):
        instance.component.world.replicate_wheel_event(instance, node)
    return no_op(instance, node, caller)


@handler('STU_008D30B1')
def hook_hit_telemetry(instance: Instance, node, caller) -> bool:
    if ((instance.graph.index, node.pos) != (0x01C8, 189)
            or expr.guid_of(node.field('m_616BEF5B')) != 0x0D80000000002052):
        return NotImplemented
    # Hook hit telemetry follows TrackStat and has no output used by combat.
    return no_op(instance, node, caller)


for _name in (
    "STUStatescriptActionCriteria",
    "STU_CEBDAF17",  # TrackStat
    "STU_6E5C5F66",  # 1B6EBA0 records optional trace data, then follows m_outPlug.
    "STU_D38D0035",  # 104319 inherits DD856C32's 1B6EBA0 base action.
    "STU_88DE7AD3",  # Same registered base action at the Junkenstein phase handoff.
    "STU_147B34C6",  # The 180A phase cleanup uses the same DD856C32 base action.
    "STU_7947FBE4",  # The 181D helper action inherits DD856C32.
    # The 0D94 helper's four action types resolve to DD856C32.
    "STU_3218BAEB",
    "STU_5EAA04BB",
    "STU_5B025A5D",
    "STU_8A8255AF",
    "STU_0FC6DAEA",  # POTG data has no dedicated 104319 action; inherits DD856C32.
    "STU_545AB3B8",  # The 104319 base action follows m_outPlug without applying the loadout.
    "STU_A1DF3B2D",  # No dedicated 104319 action. DD856C32 leaves the destination unchanged.
    "STUStatescriptActionSendClientGlobalGameMessage",
    "STU_C43F9944",
):
    handler(_name)(no_op)


__all__ = ["ABORT", "Asset"]
