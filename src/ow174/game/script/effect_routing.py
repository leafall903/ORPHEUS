"""Explicit native context and component-43 inputs for 582E0D5F routing."""

from dataclasses import dataclass, field
from weakref import WeakKeyDictionary

from ow174.game.script import expr, play
from ow174.game.script.effect_allocation import EffectList


def _entity(value):
    if type(value) is not int or not 0 <= value <= 0xFFFFFFFF or not expr.valid_entity(value):
        raise ValueError("expected a valid native entity index")


@dataclass(frozen=True, eq=False)
class Target:
    owner: int
    context: object
    entries: EffectList

    def __post_init__(self):
        _entity(self.owner)
        if self.context is None or self.context is NotImplemented or type(self.entries) is not EffectList:
            raise ValueError("expected explicit target context and effect list")


@dataclass(frozen=True, eq=False)
class Context:
    owner: int
    engine: object
    enabled: object
    fallback: object = NotImplemented
    _flags: WeakKeyDictionary = field(default_factory=WeakKeyDictionary, init=False, repr=False)

    def __post_init__(self):
        _entity(self.owner)
        if (self.engine is None or self.engine is NotImplemented
                or (type(self.enabled) is not bool and self.enabled is not NotImplemented)
                or (type(self.fallback) is not EffectList and self.fallback is not NotImplemented)):
            raise ValueError("expected explicit engine context, capability and optional fallback list")

    def set_flags(self, instance, flags):
        if type(flags) is not int or not 0 <= flags <= 0xFFFFFFFF:
            raise ValueError("native instance flags must be uint32")
        # Instance object identity prevents a retired numeric ID from transferring
        # its declaration to a replacement instance.
        self._flags[instance] = flags

    def flags(self, instance):
        return self._flags.get(instance, NotImplemented)


@dataclass(frozen=True)
class Selection:
    entries: EffectList
    context: object
    target: int


def select(state, context: Context, lookup) -> Selection | None:
    """9D2140: valid authored target, otherwise native owner, then component43.

    The caller supplies the actual owner and context. Unknown entity/component,
    flag or fallback services remain errors, distinct from known absence.
    """
    component = state.instance.component
    value = play.evaluate_scalar(state.instance, state.node.field("m_481B15D1"), watcher=state)
    entity = expr.to_entity(value)
    if not expr.valid_entity(entity):
        entity = context.owner
    target = lookup(entity)
    if target is not None and type(target) is not Target:
        component.unsupported(state.instance, "auxiliary effect target", state.node.cls, state.node)
    if target is not None:
        return Selection(target.entries, target.context, target.owner)
    flags = context.flags(state.instance)
    if flags is NotImplemented:
        component.unsupported(state.instance, "auxiliary effect instance flags", state.node.cls, state.node)
    if flags & 8:
        return None
    if type(context.fallback) is not EffectList:
        component.unsupported(state.instance, "auxiliary effect fallback", state.node.cls, state.node)
    return Selection(context.fallback, context.engine, 0)
