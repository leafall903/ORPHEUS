"""Host consumers for Winston 0109 Primal and 00FE's authored fists."""

import math

from ow174.game import bots
from ow174.game.script import expr
from ow174.game.world import HEALTH_POOL

HERO = 0x02E0000000000009
PRIMAL = 0x0109
WEAPON = 0x00FE
WINDUPS = (48, 49)
FISTS = ((35, 18), (36, 17))


def pool(component):
    for instance in component.instances.values():
        if instance.graph.index == PRIMAL:
            state = instance.states.get(4)
            if state is not None and state.active:
                return state
    return None


def sync(fight, player, script, *, dead=False):
    if script.hero != HERO:
        return
    scope = pool(script.component)
    added = (expr.to_float(scope.evaluate(scope.node.field("m_amount"), watch=False))
             if scope is not None and not dead and not script.component.owner_dead else 0.0)
    previous = fight.primal_bonus.get(player.body, 0.0)
    if added == previous:
        return
    target = next((target for target in fight._targets() if target.owner is player), None)
    if target is None:
        return
    health = fight.vitals_of(target)
    health.most[HEALTH_POOL] += added - previous
    health.now[HEALTH_POOL] = min(health.now[HEALTH_POOL], health.most[HEALTH_POOL])
    health.changed.add(HEALTH_POOL)
    if added:
        fight.primal_bonus[player.body] = added
        health.heal(health.maximum)
        if script.world_frame_anchor is None:
            script.world_frame_anchor = fight.tick, script.frame
    else:
        fight.primal_bonus.pop(player.body, None)
        script.world_frame_anchor = None
        if not dead:
            interrupt(script)
    fight._sync_body_health(player, health)


def interrupt(script):
    if script.component.world.hero != HERO:
        return
    component = script.component
    for instance in tuple(component.instances.values()):
        if instance.graph.index != WEAPON:
            continue
        windups = [state for index in (20, 21)
                   if (state := instance.states.get(index)) is not None and state.active]
        if windups:
            for state in windups:
                state.end(False)
            # The windup's shared end plug can enter a fist even on abort.
            component.run_now()
        for index, _ in FISTS:
            state = instance.states.get(index)
            if state is not None and state.active:
                state.end(False)


def contact(fight, player, command, script):
    if script.hero != HERO or pool(script.component) is None:
        return
    for instance in tuple(script.component.instances.values()):
        if instance.graph.index != WEAPON:
            continue
        for index, search_index in FISTS:
            state, search = instance.states.get(index), instance.states.get(search_index)
            if state is None or search is None or not (state.active and search.active):
                continue
            seen = fight.melee_seen.setdefault(player.body, {})
            key = instance.id, index
            if seen.get(key) == state.counter:
                continue
            seen[key] = state.counter
            if command is not None:
                hit(fight, player, command, state, search)


def hit(fight, player, command, state, search):
    from ow174.game.combat import aim, capsule_visible, hit_def, segment_distance

    forward = aim(*command.aim())
    heading = command.aim()[0] * math.tau / 65536
    right = (math.cos(heading), 0.0, -math.sin(heading))
    feet = tuple(player.mover.position)

    def point(field):
        offset = search.evaluate(search.node.field(field), watch=False)
        return tuple(feet[i] + right[i] * offset.x + forward[i] * offset.z
                     + (offset.y if i == 1 else 0.0) for i in range(3))

    a, b = point("m_05BE62DD"), point("m_B3A77CD0")
    radius = expr.to_float(search.evaluate(search.node.field("m_radius"), watch=False))
    modify = state.node.field("m_modifyHealth")
    damage = hit_def(state, {"m_modifyHealth": modify})
    origin = fight._eye(player)
    for target in fight._enemies(player):
        bottom, top = target.segment()
        x, _, z = target.feet
        centre = (x, max(bottom[1], min(top[1], (a[1] + b[1]) / 2)), z)
        if (segment_distance(centre, a, b) > radius + target.radius
                or not capsule_visible(fight._collision(), origin, bottom, top, target.radius)
                or fight.walls.blocked(origin, centre)):
            continue
        if (fight.zarya_bubbles.for_target(target.owner) is None
                and fight._deflect_direction(target.owner, forward)):
            continue
        accepted = fight._damage(player, target, damage.amount * fight.outgoing_scale(player),
                                 False, damage.loadout, command.frame, award_charge=False)
        if accepted:
            push(fight, player, target, state, modify)


def push(fight, player, target, state, modify, *, scale=1.0, origin=None):
    owner = target.owner
    if (fight.vitals_of(target).dead or fight.fortify_control_immune(target)
            or fight.hooks.controlled(target.entity)
            or (isinstance(owner, bots.Bot) and not owner.spawner.get("manual_spawn"))):
        return
    x, _, z = player.mover.position if origin is None else origin
    dx, dz = target.feet[0] - x, target.feet[2] - z
    distance = math.hypot(dx, dz)
    lateral = scale * expr.to_float(state.evaluate(modify["m_E33EFEE1"], watch=False))
    vertical = scale * expr.to_float(state.evaluate(modify["m_583C208B"], watch=False))
    x, z = (dx * lateral / distance, dz * lateral / distance) if distance else (0.0, 0.0)
    fight.npc_impulses[target.entity] = fight.tick, (x, vertical, z)
