"""104319 Roadhog 0240 self-heal and mitigation for Mei's combat world.

The graph supplies the amount, interval and incoming multiplier. Distributing
healing over the interval and interrupting through the ability stop path are
static reconstructions.
"""

from ow174.game.script import expr, nodes

HERO = 0x02E0000000000040
GRAPH = 0x0240
INCOMING_SCALE = 8243


def ability(owner):
    script = getattr(owner, "body_script", None)
    if getattr(script, "hero", None) != HERO or script.component.strict:
        return None
    for instance in script.component.instances.values():
        if instance.graph.index == GRAPH:
            return next((state for state in instance.states.values()
                         if isinstance(state, nodes.Ability)), None)
    return None


def incoming_scale(owner):
    state = ability(owner)
    if state is None or not state.flags & nodes.Ability.RUNNING:
        return 1.0
    slot = state.instance.component.entity_var(INCOMING_SCALE, create=False)
    value = slot.value() if slot is not None else None
    return max(0.0, value) if type(value) in (int, float) else 1.0


def interrupt(owner):
    state = ability(owner)
    if state is not None:
        state.presses.clear()
        state.stop()


def heal(world, state, elapsed_ms):
    component = state.instance.component
    if (world.hero != HERO or world.match is None or component.strict
            or state.instance.graph.index != GRAPH or state.node.pos != 18):
        return NotImplemented
    fields = state.node.fields
    modify = fields.get("m_modifyHealth") or {}
    target = fields.get("m_FA3ECC43") or {}
    if target.get("$") != "STU_B5A0CAF0" or modify.get("m_93CDFD2F") != 0:
        return NotImplemented
    duration = round(1000 * expr.to_float(state.evaluate(fields.get("m_95F1FB0B"), watch=False)))
    amount = expr.to_float(state.evaluate(modify.get("m_amount"), watch=False))
    if duration <= 0 or amount <= 0:
        return NotImplemented
    end = min(state.now, state.start + duration)
    elapsed = max(0, end - max(state.start, state.now - elapsed_ms))
    if elapsed and not component.owner_dead:
        world.match.combat.heal_breather(world.body, amount * elapsed / duration)
    return True

