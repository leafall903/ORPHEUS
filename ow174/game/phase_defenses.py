"""Wraith/Fade protection and world replay for scoped defense abilities.

Reviewed phase helpers from the isolated Torbjorn/Winston lanes. Zero-delay
entry continuation, immunity and intangible target policy are reconstructions.
The original ability graph owns input, stack lifetime and cooldown.10467 alone
is not protection: it also covers recovery. No synthetic immunity timer.
"""

from ow174.game import fortify, shadow_step, shot_guards, tracer_combat
from ow174.game.commands import Command
from ow174.game.script import expr, nodes

PHASES = {
    0x02E0000000000002: (0x0020, 8440),
    0x02E00000000001A2: (0x0F7C, 6604),
}


PHASE_REPLAY_LIMIT = 768
CRYO_REPLAY_LIMIT = 1024  # four seconds of protection plus the twelve-second cooldown
FORTIFY_REPLAY_LIMIT = 896  # four seconds of protection plus the ten-second cooldown
BREATHER_REPLAY_LIMIT = 640  # two seconds of treatment plus the eight-second cooldown
GRASP_REPLAY_LIMIT = 1024  # Grasp and its twelve-second cooldown
DEFLECT_REPLAY_LIMIT = 672  # Deflect and its eight-second cooldown
MATRIX_REPLAY_LIMIT = 480  # depletion, recharge delay and full recharge
BOOSTERS_REPLAY_LIMIT = 480  # flight and four-second cooldown
MICRO_MISSILES_REPLAY_LIMIT = 640  # volley and eight-second cooldown
ABILITY_NODES = {0x0020: 18, 0x0F7C: 16}


def _running_or_cooling(instance, node):
    if instance is None:
        return False
    ability = instance.states.get(instance.graph.node(node).state)
    return (isinstance(ability, nodes.Ability) and ability.active
            and (ability.flags & nodes.Ability.RUNNING or ability.remaining() > 0))


def _replay_frames(player, mech):
    from ow174.game import dva, mei, roadhog

    script = player.body_script
    if script is None or script.retired:
        return 0
    hero = script.hero
    if mech is not None:
        if mech.dead:
            return 0
        script = mech.body_script
    if script.component.strict or script.component.owner_dead:
        return 0
    spec = PHASES.get(hero)
    if spec is not None:
        graph = spec[0]
        node, limit = ABILITY_NODES[graph], PHASE_REPLAY_LIMIT
    elif hero == mei.HERO:
        graph, node, limit = mei.CRYO, 3, CRYO_REPLAY_LIMIT
    elif hero == fortify.HERO:
        graph, node, limit = fortify.GRAPH, 3, FORTIFY_REPLAY_LIMIT
    elif hero == roadhog.HERO:
        graph, node, limit = roadhog.BREATHER, 1, BREATHER_REPLAY_LIMIT
    elif hero == shot_guards.SIGMA:
        graph, node, limit = 0x1D02, 3, GRASP_REPLAY_LIMIT
    elif hero == shot_guards.GENJI:
        graph, node, limit = 0x03A3, 3, DEFLECT_REPLAY_LIMIT
    elif hero == shot_guards.DVA and (mech is not None or player.hero.body == shot_guards.MECH):
        mech_abilities = {i.graph.index: i for i in script.component.instances.values()}
        if _running_or_cooling(mech_abilities.get(dva.MICRO_MISSILES), 3):
            return MICRO_MISSILES_REPLAY_LIMIT
        if _running_or_cooling(mech_abilities.get(dva.BOOSTERS), 4):
            return BOOSTERS_REPLAY_LIMIT
        graph, node, limit = 0x02CC, 4, MATRIX_REPLAY_LIMIT
    else:
        return 0
    instance = next((i for i in script.component.instances.values() if i.graph.index == graph), None)
    if instance is None:
        return 0
    if _running_or_cooling(instance, node):
        return limit
    if graph == 0x02CC:
        fuel, capacity = instance.vars.get(87), instance.vars.get(971)
        if (fuel is not None and capacity is not None
                and expr.to_float(fuel.value()) < expr.to_float(capacity.value())):
            return limit
    return 0


def replay_until(fight, previous):
    from ow174.game import dva

    return previous + max((
        _replay_frames(player, dva.controlled_mech(player)) for player in fight.match.players
        if fight.match._shown(player) and not fight.is_dead(player)
    ), default=0)


def replay(fight, tick, dt):
    from ow174.game import dva
    from ow174.game.projectiles import MAX_LEASE_FRAME

    match = fight.match
    if match.ended or tick > MAX_LEASE_FRAME:
        return
    scheduled = match.tick
    try:
        match.tick = tick
        for player in match.players:
            if not match._shown(player) or fight.is_dead(player):
                continue
            mech = dva.controlled_mech(player)
            if not _replay_frames(player, mech) or player.body_script.frame >= tick:
                continue
            mover = mech.dva_mover if mech is not None else player.mover
            command = Command(tick, yaw=mover.yaw, pitch=mover.pitch)
            if mech is not None:
                dva.drive_mech(player, mech, command, dt)
            else:
                mover.last_command = command
                player._step_body_pose(command, dt, tick, replay_accepted=True)
            history = fight.history.get(mech.entity if mech is not None else player.body)
            if history and history[-1][0] >= tick:
                at, _, height, eye = history[-1]
                history[-1] = at, tuple(mover.position), height, eye
    finally:
        match.tick = scheduled


def _protection(state, var):
    return (state.node.cls == 'STUStatescriptStateStack'
            and expr.lvalue(state.node.field('m_out_Var')) == (expr.ENTITY, var))


def active(actor):
    if shadow_step.immune(actor) or tracer_combat.recalling(actor):
        return True
    spec = PHASES.get(getattr(getattr(actor, 'hero', None), 'guid', None))
    script = getattr(actor, 'body_script', None)
    if (spec is None or script is None or script.component.strict or script.component.owner_dead
            or script.component.entity != actor.body):
        return False
    variable = script.component.entity_var(spec[1], create=False)
    if variable is None or not expr.truthy(variable.value()):
        return False
    return any(
        state.active and not state.ending and _protection(state, spec[1])
        for instance in script.component.instances.values() if instance.graph.index == spec[0]
        for state in instance.states.values()
    )


def enter(state):
    component = state.instance.component
    world = component.world
    spec = PHASES.get(getattr(world, 'hero', None))
    if spec is None or component.strict or state.instance.graph.index != spec[0]:
        return
    if _protection(state, spec[1]):
        if (world.match is not None and state.var is not None and state.top
                and expr.truthy(state.var.value())):
            world.match.combat.cleanse_ana_effects(component.entity)
        return
    if state.node.pos != 2 or state.node.cls != 'STU_54FEB21C':
        return
    if any(state.node.field(key) is not None for key in
           ('m_7C8AD485', 'm_C7A42E2A', 'm_F8CE386A', 'm_C46403A5')):
        return
    cfg = state.node.field('m_ED97A97C')
    if cfg is not None and state.evaluate(cfg, watch=False) == 0:
        state.request_finish()
