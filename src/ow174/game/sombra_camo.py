"""Advance the authored Thermoptic Camo exit and cooldown across absent owner input."""

from ow174.game.script import nodes, runtime

HERO = 0x02E000000000012E
GRAPH = 0x0C9B
ABILITY = 13  # node 28
EXIT_BLEND = 18  # node 40
RECEIVED_DAMAGE = 0x0240000000000011


def owns_source_rule(state):
    """Only the current Match body's original Camo85 authoritative source list.

    This qualifies registration/retirement, not dynamic caches, engine context,
    candidate classification or entity/model consumers.
    """
    instance = state.instance
    component = instance.component
    world = component.world
    match = getattr(world, "match", None)
    if (match is None or getattr(world, "hero", None) != HERO
            or (instance.graph.index, state.node.pos) != (GRAPH, 85)):
        return False
    player = match.combat.sombra_controls.player(component.entity)
    script = getattr(player, "body_script", None)
    return (script is not None and not script.retired and script.component is component
            and not component.owner_dead)


def retire_source_rule(script):
    """Unlink the retained body's source before its Match namespace detaches."""
    if script.hero != HERO or script.component.strict:
        return
    for state in tuple(script.component.state_registries.get("STU_33A46033", ())):
        if (state.instance.graph.index, state.node.pos) == (GRAPH, 85):
            state.on_cleanup()


def _instance(script):
    if getattr(script, "hero", None) != HERO or script.retired or script.component.owner_dead:
        return None
    return next((instance for instance in script.component.instances.values()
                 if instance.graph.index == GRAPH), None)


def next_frame(script):
    instance = _instance(script)
    if instance is None:
        return None
    ability = instance.states.get(ABILITY)
    if not isinstance(ability, nodes.Ability) or not ability.active:
        return None
    following = script.frame + 1
    if ability.remaining() > 0:
        return following
    if not ability.flags & nodes.Ability.RUNNING:
        return None
    blend = instance.states.get(EXIT_BLEND)
    if blend is not None and blend.active:
        return following
    damage = next((event for event in instance.queue
                   if event.kind == runtime.MESSAGE and event.message[0] == RECEIVED_DAMAGE), None)
    return max(following, damage.time // script.frame_ms) if damage is not None else None


def queue_damage(script, params, sender, *, impact, default_time):
    """Date damage while Camo is running and preserve the timing of other receivers."""
    camo = _instance(script)
    if camo is None:
        return False
    ability = camo.states.get(ABILITY)
    if not isinstance(ability, nodes.Ability) or not ability.flags & nodes.Ability.RUNNING:
        return False
    impact_time = impact * script.frame_ms
    camo_time = impact_time if impact_time > script.component.now + 1 else default_time
    for instance in list(script.component.instances.values()):
        when = camo_time if instance is camo else default_time
        runtime.send_instance_message(instance, RECEIVED_DAMAGE, params, sender, at=when)
    return True
