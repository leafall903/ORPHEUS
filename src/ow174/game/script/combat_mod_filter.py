
"""104319 D275A0 tag-only CombatModFilter callback for explicit combat events."""

from dataclasses import dataclass

from ow174.game.script import expr, graph, play

CLASS = "STUStatescriptStateCombatModFilter"


def active_actor_filter(actor, hero, graph_index, state_index):
    """Find a live authored filter for an explicitly supported hero state."""
    if getattr(getattr(actor, "hero", None), "guid", None) != hero:
        return None
    script = getattr(actor, "body_script", None)
    if script is None or script.retired or script.component.owner_dead or script.component.fault is not None:
        return None
    for instance in script.component.instances.values():
        if instance.graph.index == graph_index:
            state = instance.states.get(state_index)
            if state is not None and state.active and not state.ending and state.node.cls == CLASS:
                return state
    return None


def damage_scale(state):
    value = expr.to_float(state.evaluate(state.node.field("m_50530EAB"), watch=False))
    return max(0.0, min(1.0, 1.0 + value))


@dataclass(frozen=True)
class Result:
    resource: int
    operand: float
    mode: int
    flags: tuple[bool, bool]


def _guid(value):
    return graph.guid(value) if value is not None else 0


def _tags(config):
    if config is None:
        return frozenset()
    if type(config) not in (list, tuple):
        return None
    values = frozenset(_guid(item) for item in config)
    return None if None in values else values


def profile(state):
    """Decode the proven tag-only profile before inserting a live state."""
    instance, node = state.instance, state.node
    for field in ("m_5E6F1B8B", "m_6AA5D70F", "m_893AABFC", "m_F7872851", "m_E9517777"):
        if node.field(field) is not None:
            instance.component.unsupported(instance, "combat modifier filter profile", CLASS, node)
    if node.field("m_029060AC"):
        instance.component.unsupported(instance, "combat modifier tag additions", CLASS, node)
    resource = _guid(node.field("m_60703B1E"))
    allow = _tags(node.field("m_F60AA143"))
    deny = _tags(node.field("m_5CFFCBAE"))
    mode = node.field("m_41B10FF2") or 0
    if resource is None or allow is None or deny is None or type(mode) is not int:
        instance.component.unsupported(instance, "combat modifier filter fields", CLASS, node)
    for field, kinds in (
        ("m_50530EAB", ("STUConfigVarInt", "STUConfigVarFloat", "STUConfigVarBool")),
        ("m_941C56C2", ("STUConfigVarBool",)),
    ):
        cfg = node.field(field)
        if cfg is not None and (type(cfg) is not dict or cfg.get("$") not in kinds):
            instance.component.unsupported(instance, "combat modifier event expression", CLASS, node)
    return resource, allow, deny, mode


def evaluate(state, resource, tags):
    key, allow, deny, mode = state.combat_filter
    if not key or resource != key:
        return None
    tags = frozenset(tags)
    if (allow and allow.isdisjoint(tags)) or not deny.isdisjoint(tags):
        return None
    instance, node = state.instance, state.node
    operand = expr.to_float(play.evaluate_scalar(instance, node.field("m_50530EAB"), initial=0.0))
    first = expr.truthy(play.evaluate_scalar(instance, node.field("m_941C56C2"), initial=False))
    second = expr.truthy(play.evaluate_scalar(instance, node.field("m_941C56C2"), initial=False))
    return Result(0, operand, mode, (first, second))
