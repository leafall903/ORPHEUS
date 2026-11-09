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
from types import NotImplementedType

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
    Component,
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


@state_class("STU_9028E035")
class RegisteredState(State):
    """1B72530/1B725C0: the local authoritative linked-state lifecycle.

    The native runtime class has no additional execution hooks. m_type remains
    authored data for native consumers; its enum does not split the type list.
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
    """An animation (OnBegin 0x7FF78981DD90): with m_playOverDuration (m_86701C3E) set, a timer at start + d
    follows m_A3B86BB0 (animation finished), and unless m_clientAnimRemovesSelf (m_154DBA91) the state
    finishes at start + d. Without it the duration is the animation asset's: not known here."""

    family = "anim"

    def on_enter(self) -> None:
        duration = _field(self, "m_86701C3E")
        if duration is None:
            expr.warn_once("anim", "an Anim state without m_playOverDuration does not end on its own")
            return
        ms = seconds_to_ms(self.evaluate(duration, watch=False))
        self.timer_at(max(0, ms), 1)
        removes_self = _field(self, "m_154DBA91")
        if removes_self is None or not truthy(self.evaluate(removes_self, watch=False)):
            self.request_finish(max(0, ms))

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

    def take_press(self, button: int) -> tuple[int, int, int] | None:
        early = _field(self, "m_246438AD")
        limit = seconds_to_ms(self.evaluate(early, watch=False)) if early is not None else 0
        for item in self.presses:
            time, which, activation = item
            if which == button and self.now - time <= limit and activation <= self.counter:
                self.presses.remove(item)
                return item
        return None

    # starting and stopping

    def tick(self) -> None:
        self.activate()

    def timer(self, param: int) -> None:
        if param in (1, 2):
            if param == 2:
                # Cooldown output reaches zero without a variable write.
                if self.remaining() > 0.0:
                    self.timer_at(1, 2)
                    return
                if self.var is not None:
                    self.instance.changed(self.var, self.cur)
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
        pressed = None
        if start:
            pressed = self.take_press(start)
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
            if pressed:
                # Preserve its original age: retrying eligibility must not renew the buffer.
                self.presses.insert(0, pressed)
            return False
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
        """Queue scope cleanup before releasing arbitration (server reconstruction).

        A newly unblocked weapon must not sample old ammo ahead of outgoing
        refill messages. Native cross-object ordering remains unverified.
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


@state_class("STU_CD46AF93")
class ForCodeSynced(State):
    """Strict mission support for the verified zero-sync-flag path.

    Native begin/end/update: D44CA0, D44CD0, D44D00. Nonzero sync flags invoke
    D4F690, whose transport behavior is not yet implemented; strict execution
    stops there. Legacy body prediction retains its former passive state.
    """

    def on_begin(self) -> None:
        component = self.instance.component
        if not component.strict:
            return
        if any(_field(self, name) not in (None, 0) for name in ("m_598E32D5", "m_0582B0E4")):
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
        for item in _field(self, "m_89402D74") or []:
            if isinstance(item, dict):
                target = lvalue(item.get("m_out_Var"))
                if target is not None:
                    values[target] = self.evaluate(item.get("m_value"), watch=False)
        component = self.instance.component
        self.child = component.create(found, parent=self.instance, parent_state=self.index, values=values)

    def on_end(self, finished: bool) -> None:
        if self.child is not None:
            self.instance.component.destroy(self.child)
            self.child = None

    def payload(self) -> dict:
        return {"child": self.child.id if self.child is not None else 0}


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
    """Advance an explicit health service on the state's VM clock."""

    def __init__(self, *args):
        super().__init__(*args)
        self.last_applied = 0

    def on_enter(self):
        self.last_applied = self.now
        if self.instance.component.world.modify_health(self, 0) is not NotImplemented:
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


@state_class("STUStatescriptStateTrackTargets")
class TrackTargets(State):
    """Explicit world-provided target arrays; unimplemented searches still find nothing.

    Array linking/update and zero wire age are reconstructed. Native weighted
    selection, target history/age and full engine-query scheduling stay open.
    """

    family = "targets"

    def __init__(self, *args):
        super().__init__(*args)
        self.targets = ()
        self.var = None

    def on_begin(self):
        targets = self.instance.component.world.track_targets(self)
        if targets is NotImplemented:
            return
        self.targets = tuple(targets)
        target = lvalue(self.node.field('m_8A2905A0'))
        if self.instance.graph.index == 0x022F and self.node.pos in (26, 86):
            # The firing subgraph iterates the baked arrays after its query exits.
            self.instance.write(target, self.targets)
        else:
            self.var = self.link(target) if target is not None else None
        self.ticks()

    def tick(self):
        targets = self.instance.component.world.track_targets(self)
        if targets is NotImplemented:
            targets = ()
        before, self.targets = self.targets, tuple(targets)
        if self.var is not None and before != self.targets:
            self.instance.changed(self.var, before)
        elif self.instance.graph.index == 0x022F and self.node.pos in (26, 86) and before != self.targets:
            self.instance.write(lvalue(self.node.field('m_8A2905A0')), self.targets)

    def on_end(self, finished):
        if self.var is not None:
            var, self.var = self.var, None
            self.unlink(var)
        self.targets = ()

    def output(self, slot):
        return self.targets

    def payload(self) -> dict:
        return {"targets": tuple((target.id, 0) for target in self.targets)}


@state_class("STU_431F3FCC")
class TargetEffects(State):
    """Track the instances created by an explicit effect provider."""

    def __init__(self, *args):
        super().__init__(*args)
        self.children = {}

    def on_enter(self):
        if self.instance.component.world.target_effects(self) is not NotImplemented:
            self.ticks()
        elif self.instance.component.strict:
            self.instance.component.unsupported(
                self.instance, "target effects service", self.node.cls, self.node,
            )

    def tick(self):
        self.instance.component.world.target_effects(self)

    def on_end(self, finished):
        self.clear_children()

    def clear_children(self):
        component = self.instance.component
        for number in self.children.values():
            child = component.instances.get(number)
            if child is not None:
                component.destroy(child)
        self.children.clear()


@state_class("STU_691BFA55", "STU_7CA2FBAF")
class Effect(State):
    """An effect (StEC wire form): a plain state whose payload is its activation counter & 3 (reader
    0x7FF789894540; the client keeps it as its own counter when the state is on)."""

    def payload(self) -> dict:
        return {"counter": self.counter & 3}


@state_class("STU_B9898052")
class ClientOnlyPulser(State):
    """Pulses its client-only subgraph (vtable 0x7FF78B549DE8): OnEnter and every begin while active
    (0x7FF78990D6D0) pulse once, and the client follows its plug m_B6A27F9A once per pulse
    (0x7FF78990D872). Its payload (reader 0x7FF78990D760) is a bit and the 8-bit count of pulses: a count
    that moved on pulses the client as often (mod 256); a state the client reads for the first time pulses
    once only with the bit set (or the node's m_C85E2CBA, node +0xF0), else it takes the count as seen.
    The server marks recent pulses after the instance's first simulation frame as fresh. Pulses during
    that first frame keep their count without requesting playback beyond what the node's flag allows.
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
        if self.now // frame_ms > self.instance.start // frame_ms:
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


# --- actions and conditions ------------------------------------------------------------------------


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
    provider = instance.component.world.raycast
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


@handler("STU_4C3509FB")
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
    return branch(instance, node, caller, truthy(instance.evaluate(node.fields.get("m_condition"), caller)))


@handler("STU_821116A6")
def contains(instance: Instance, node, caller) -> bool:
    """The container m_814F8EC3 has an element equal to m_value (eval 0x7FF78AA85A30)."""
    container = instance.evaluate(node.fields.get("m_814F8EC3"), caller)
    value = instance.evaluate(node.fields.get("m_value"), caller)
    return branch(instance, node, caller, expr.contains_value(container, value))


@handler("STU_7DEB522D")
def button_held(instance: Instance, node, caller) -> bool:
    """A logical-button condition: the button is held."""
    button = to_int(instance.evaluate(node.fields.get("m_logicalButton")))
    return branch(instance, node, caller, button in instance.component.held)


@handler("STU_4E2BCAAB")
def unknown_condition(instance: Instance, node, caller) -> bool:
    if instance.component.strict:
        instance.component.unsupported(instance, "condition", node.cls, node)
    expr.warn_once(node.cls, "condition %s is not modelled: false", node.cls)
    return branch(instance, node, caller, False)


@handler("STUStatescriptActionSwitch")
def action_switch(instance: Instance, node, caller) -> bool:
    """Follow the case (m_FDD34C25) whose value equals the switch value, else the default (m_CD38D5DB)."""
    value = instance.evaluate(node.fields.get("m_262AE982"))
    for number, case in enumerate(node.fields.get("m_FDD34C25") or []):
        if isinstance(case, dict) and equal(value, instance.evaluate(case.get("m_value"))):
            instance.follow(node, f"m_FDD34C25[{number}]", None)
            break
    else:
        instance.follow(node, "m_CD38D5DB", None)
    instance.follow(node, "m_outPlug", None)
    return True


def message_params(instance: Instance, node) -> dict:
    params = {}
    for item in node.fields.get("m_params") or []:
        if isinstance(item, dict):
            params[expr.guid_of(item.get("m_B5051BCE")) & 0xFFFF] = instance.evaluate(item.get("m_value"))
    return params


def send(instance: Instance, node, caller) -> None:
    """Send a game message to each target, through the world for other entities."""
    cfg = node.fields.get("m_gameMessage")
    message = expr.guid_of(cfg.get("m_gameMessage")) if isinstance(cfg, dict) else 0
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
    if instance.component.strict:
        instance.component.world.play_script(instance, node)
    else:
        instance.component.world.play_static_actor(instance,node)
    instance.follow(node, "m_outPlug", None)
    return True


@handler("STU_B6E3C77D")
def create_world_entity(instance: Instance,node,caller) -> bool | NotImplementedType:
    result = instance.component.world.create_world_entity(instance,node)
    if result is NotImplemented:
        return NotImplemented
    if result is True:
        instance.follow(node,"m_outPlug",None)
    return result


def no_op(instance: Instance, node, caller) -> bool:
    """Actions with no effect on the server's graph state (effects, stats, client-only messages)."""
    instance.follow(node, "m_outPlug", None)
    return True


for _name in (
    "STUStatescriptActionEffect",
    "STUStatescriptActionCriteria",
    "STU_CEBDAF17",  # TrackStat
    "STU_100EF8C2",  # SendClientOnlyGameMessage
    "STUStatescriptActionSendClientGlobalGameMessage",
    "STU_7106C356",
    "STU_C43F9944",
):
    handler(_name)(no_op)


__all__ = ["ABORT", "Asset"]
