"""Bind Orisa's live 0CF4 Fortify filter to the static combat host."""

import math

from ow174.game.script import expr

HERO = 0x02E000000000013E


def _instance_for(owner):
    if getattr(getattr(owner, "hero", None), "guid", None) != HERO:
        return None
    script = owner.body_script
    if script is None or script.retired or script.component.strict or script.component.owner_dead:
        return None
    return next((instance for instance in script.component.instances.values()
                 if instance.graph.index == 0x0CF4), None)


def state_for(owner):
    instance = _instance_for(owner)
    if instance is not None:
        state = instance.states.get(4)
        if state is not None and state.active and not state.ending:
            return state
    return None


def cooldown_remaining(owner):
    """Return the time before Fortify can be used on a replacement body."""
    instance = _instance_for(owner)
    if instance is None:
        return 0.0
    ability = instance.state(0)
    duration = instance.states.get(1)
    if duration is not None and duration.active:
        # The duration chase ends by writing the authored cooldown from var 472.
        cooldown = expr.to_float(instance.find_var(expr.INSTANCE, 472).value())
        rate = ability.rate()
        return expr.to_float(duration.cur) + cooldown / (rate if rate > 0 else 1.0)
    rate = ability.cooldown_rate
    return ability.remaining() / (rate if rate > 0 else 1.0)


def set_cooldown(owner, seconds):
    instance = _instance_for(owner)
    if instance is not None:
        ability = instance.state(0)
        target = expr.lvalue(ability.node.field("m_out_CooldownVar"))
        if target is not None:
            rate = ability.rate()
            instance.write(target, seconds * (rate if rate > 0 else 1.0))


def control_flag_active(owner):
    state = state_for(owner)
    if state is None:
        return False
    flag = state.instance.component.entity_var(9161, create=False)
    return flag is not None and expr.truthy(flag.value())


def damage_scale(state):
    reduction = state.evaluate(state.node.field("m_50530EAB"), watch=False)
    if expr.kind(reduction) not in ("int", "float") or not math.isfinite(reduction):
        return 1.0
    return max(0.0, min(1.0, 1.0 + reduction))
