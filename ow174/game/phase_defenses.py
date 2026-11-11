"""Scoped reconstruction of three authored phase skills, not a tag engine.

Equivalent phase mapping was reviewed in the isolated Pharah lane. Real skill
inputs,timers and8440/6604 stacks own protection lifetime.10467 also spans
recovery and cannot extend immunity. Damage/collision policy and the zero-delay
54FEB21C continuation are explicit permissive-host adapters.
"""

from ow174.game.script import expr

ZENYATTA = 0x02E0000000000020
TRANSCENDENCE = 0x017B

PHASES = {
    0x02E0000000000002: (0x0020, True, 8440),  # Wraith Form
    0x02E00000000001A2: (0x0F7C, True, 6604),  # Fade
    0x02E00000000000DD: (0x05FE, False, 8440),  # Cryo-Freeze remains solid
}


def phase(owner):
    """(immune,intangible) only for a matching live authored skill stack."""
    spec = PHASES.get(getattr(getattr(owner, "hero", None), "guid", None))
    script = getattr(owner, "body_script", None)
    if spec is None or script is None or script.component.owner_dead or script.component.strict:
        return False, False
    variable = script.component.entity_var(spec[2], create=False)
    if variable is None or not expr.truthy(variable.value()):
        return False, False
    active = any(
        state.active and state.node.cls == "STUStatescriptStateStack"
        and expr.lvalue(state.node.field("m_out_Var")) == (expr.ENTITY, spec[2])
        for instance in script.component.instances.values() if instance.graph.index == spec[0]
        for state in instance.states.values()
    )
    return active, active and spec[1]


def _transcendence_countdown(owner):
    if getattr(getattr(owner, "hero", None), "guid", None) != ZENYATTA:
        return None
    script = getattr(owner, "body_script", None)
    if script is None or script.retired or script.component.owner_dead or script.component.strict:
        return None
    variable = script.component.entity_var(10414, create=False)
    if variable is None or not expr.truthy(variable.value()):
        return None
    for instance in script.component.instances.values():
        if instance.graph.index != TRANSCENDENCE:
            continue
        countdown = instance.states.get(3)
        if countdown is not None and countdown.active and not countdown.ending:
            return countdown
    return None


def _transcendence_duration(countdown):
    return expr.to_float(countdown.evaluate(countdown.node.field("m_EB0FC261"), watch=False))


def transcendence_invulnerable(owner, tick):
    """Keep Zen solid while the authored immunity stack and countdown are live."""
    countdown = _transcendence_countdown(owner)
    if countdown is None:
        return False
    deadline = countdown.start + round(_transcendence_duration(countdown) * 1000)
    return tick * countdown.instance.component.frame_ms < deadline


def heal_transcendence(fight, tick):
    if fight.match.game_map.free_for_all:
        return
    for healer in fight.match.players:
        countdown = _transcendence_countdown(healer)
        if countdown is not None:
            _heal_transcendence_frame(fight, healer, countdown, tick)


def _heal_transcendence_frame(fight, healer, countdown, tick):
    from ow174.game.combat import EPSILON, capsule_visible, segment_distance

    instance = countdown.instance
    track = instance.states.get(0)
    if track is None or not track.active:
        return
    duration = round(_transcendence_duration(countdown) * 1000)
    frame_ms = instance.component.frame_ms
    now = tick * frame_ms
    elapsed = min(now, countdown.start + duration) - max(now - frame_ms, countdown.start)
    if elapsed <= 0:
        return
    effect = instance.state(6)
    budget = expr.to_float(effect.evaluate(effect.node.field("m_modifyHealth.m_amount"), watch=False))
    radius = expr.to_float(track.evaluate(track.node.field("m_radius"), watch=False))
    origin = fight._eye(healer)
    for target in fight._targets():
        if target.owner is healer or target.team != healer.team:
            continue
        start, end = target.segment()
        if segment_distance(origin, start, end) - target.radius > radius:
            continue
        if not capsule_visible(fight._collision(), origin, start, end, target.radius):
            continue
        centre = tuple((a + b) * .5 for a, b in zip(start, end, strict=True))
        delta = tuple(b - a for a, b in zip(origin, centre, strict=True))
        reach = sum(axis * axis for axis in delta) ** .5
        if reach > EPSILON:
            direction = tuple(axis / reach for axis in delta)
            if fight._shield_trace(healer, origin, direction, reach, include_attached=False) is not None:
                continue
        fight._heal_target_ticks(healer, target, budget * elapsed / duration, tick, tick)


def zero_delay_entry(state):
    component = state.instance.component
    hero = getattr(component.world, "hero", None)
    spec = PHASES.get(hero)
    graph_index = state.instance.graph.index
    zen_entry = hero == ZENYATTA and graph_index == TRANSCENDENCE and state.node.pos == 26
    if component.strict or not (zen_entry or (spec is not None and spec[0] == graph_index)):
        return False
    if any(state.node.field(name) is not None for name in (
            "m_7C8AD485", "m_C7A42E2A", "m_F8CE386A", "m_C46403A5")):
        return False
    cfg = state.node.field("m_ED97A97C")
    return cfg is None or state.evaluate(cfg, watch=False) == 0
