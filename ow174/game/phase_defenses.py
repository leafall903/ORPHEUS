"""Wraith and Fade protection and world clocks for timed defensive states."""

from ow174.game import cryo, deflection, matrix, sigma_grasp, transcendence, zarya_barriers
from ow174.game.projectiles import MAX_LEASE_FRAME
from ow174.game.script import expr, nodes

PHASES = {
    0x02E0000000000002: (0x0020, 8440),
    0x02E00000000001A2: (0x0F7C, 6604),
}
CLOCK_GRAPHS = {hero: (spec[0],) for hero, spec in PHASES.items()} | {
    cryo.HERO: (cryo.GRAPH,),
    deflection.GENJI: (deflection.GRAPH,),
    matrix.DVA: (matrix.GRAPH,),
    sigma_grasp.SIGMA: (sigma_grasp.GRAPH,),
    transcendence.HERO: (transcendence.GRAPH,),
    zarya_barriers.HERO: tuple(zarya_barriers.PETS),
}
MAX_IDLE_STEPS = 1024


def active(actor):
    spec = PHASES.get(getattr(getattr(actor, 'hero', None), 'guid', None))
    script = getattr(actor, 'body_script', None)
    if (spec is None or script is None or script.retired or script.component.strict
            or script.component.owner_dead or script.component.fault is not None
            or script.component.entity != actor.body):
        return False
    variable = script.component.entity_var(spec[1], create=False)
    if variable is None or not expr.truthy(variable.value()):
        return False
    return any(
        state.active and not state.ending and state.node.cls == 'STUStatescriptStateStack'
        and expr.lvalue(state.node.field('m_out_Var')) == (expr.ENTITY, spec[1])
        for instance in script.component.instances.values() if instance.graph.index == spec[0]
        for state in instance.states.values()
    )


def advance_entry(actor):
    script = actor.body_script
    spec = PHASES.get(actor.hero.guid)
    if spec is None or script.component.strict:
        return
    for instance in tuple(script.component.instances.values()):
        if instance.graph.index == spec[0]:
            node = instance.graph.node(2)
            state = instance.states.get(node.state)
            if state is not None and state.active and not state.ending and _complete_entry(state):
                instance.update()


def _timed_protection(player):
    return active(player) or cryo.active(player) or transcendence.invulnerable(player)


def has_active_projectile_cover(player):
    return (matrix.active(player) is not None or sigma_grasp.active(player) is not None
            or deflection.active(player) is not None)


def has_active_cover(player):
    return zarya_barriers.has_active_barrier(player) or has_active_projectile_cover(player)


def advance_idle(fight, player) -> float | None:
    """Advance unposed frames and report the start of the first vulnerable world tick."""
    script = player.body_script
    anchor = fight.phase_clocks.get(player.body)
    if anchor is None or script is None or script.retired or player.gone or not player.spawned:
        return
    from ow174.game.combat import TICK

    tick, frame = anchor
    tick_ms = round(TICK * 1000)
    elapsed = max(0, fight.tick - tick) * tick_ms
    target = min(MAX_LEASE_FRAME, frame + elapsed // script.frame_ms - 1,
                 script.frame + MAX_IDLE_STEPS)
    graphs = CLOCK_GRAPHS[player.hero.guid]
    was_protected = _timed_protection(player)
    damage_start = None
    while script.frame < target:
        # A direct jump would start recovery/cooldown at the catch-up
        # endpoint. Step the authored transitions at their own frame.
        player._advance_body_gap(script.frame + 2)
        if player.body_script is not script:
            fight.phase_clocks.pop(player.body, None)
            break
        protected = _timed_protection(player)
        if was_protected and not protected:
            # The world update leaves its current command frame available.
            # Map the body frame that ended protection back to that world tick.
            expiry_ms = (script.frame - frame + 1) * script.frame_ms
            expiry_tick = tick + (expiry_ms + tick_ms - 1) // tick_ms
            damage_start = fight.now - (fight.tick - expiry_tick + 1) * TICK
        was_protected = protected
        pending = any(
            isinstance(state, nodes.Ability)
            and (state.flags & nodes.Ability.RUNNING or state.remaining() > 0)
            for instance in script.component.instances.values() if instance.graph.index in graphs
            for state in instance.states.values()
        )
        if not pending and not protected and not has_active_cover(player):
            fight.phase_clocks.pop(player.body, None)
            break
    return damage_start


def _complete_entry(state):
    spec = PHASES.get(getattr(state.instance.component.world, 'hero', None))
    if (spec is None or state.instance.component.strict or state.instance.graph.index != spec[0]
            or state.node.pos != 2 or state.node.cls != 'STU_54FEB21C'):
        return False
    if any(state.node.field(key) is not None for key in
           ('m_7C8AD485', 'm_C7A42E2A', 'm_F8CE386A', 'm_C46403A5')):
        return False
    cfg = state.node.field('m_ED97A97C')
    if cfg is not None and state.evaluate(cfg, watch=False) == 0:
        state.request_finish()
        return True
    return False
