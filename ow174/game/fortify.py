"""Orisa's authored Fortify stacks and their reconstructed combat effects."""

from ow174.game.script import expr, nodes

HERO = 0x02E000000000013E
GRAPH = 0x0CF4


def observe(fight, player):
    if player.hero.guid != HERO:
        return
    instance = next((i for i in player.body_script.component.instances.values()
                     if i.graph.index == GRAPH), None)
    clock = instance.states.get(1) if instance is not None else None  # node 6, remaining duration
    if not isinstance(clock, nodes.ChaseVar) or not clock.active or clock.ending:
        fight.fortifies.pop(player.body, None)
        return
    prior = fight.fortifies.get(player.body)
    if prior is not None and prior[:2] == (clock, clock.counter):
        return
    deadline = clock.expiry_frame()
    if deadline is not None:
        fight.fortifies[player.body] = (clock, clock.counter, deadline)


def active(fight, actor):
    # Silence suppresses protection before the owner graph takes its next step.
    if getattr(getattr(actor, "hero", None), "guid", None) != HERO or actor.body in fight.hacks:
        return False
    scope = fight.fortifies.get(actor.body)
    if scope is None:
        return False
    clock, activation, deadline = scope
    component = clock.instance.component
    immunity = component.entity_var(9161, create=False)
    return (fight.tick <= deadline and clock.active and not clock.ending and clock.counter == activation
            and not component.owner_dead and immunity is not None and expr.truthy(immunity.value()))


def damage_scale(fight, actor):
    if not active(fight, actor):
        return 1.0
    value = actor.body_script.component.entity_var(8243, create=False)
    scale = expr.to_float(value.value()) if value is not None else 1.0
    return scale if 0 < scale <= 1 else 1.0


def advance(fight, tick):
    for body, (clock, activation, deadline) in tuple(fight.fortifies.items()):
        if not clock.active or clock.ending or clock.counter != activation:
            fight.fortifies.pop(body)
            continue
        if tick <= deadline:
            continue
        fight.fortifies.pop(body)
        component = clock.instance.component
        saved_now = component.now
        with component.operation():
            try:
                component.now = max(saved_now, (deadline + 1) * component.frame_ms - 1)
                clock.cur = clock.destination()
                clock.reached = True
                clock.end(True)
            finally:
                component.now = saved_now
