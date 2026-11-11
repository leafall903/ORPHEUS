"""Replay Sombra owner timers and accepted actions without inventing input."""

from ow174.game import sombra_camo, translocators
from ow174.game.commands import Command
from ow174.game.projectiles import MAX_LEASE_FRAME
from ow174.game.script import nodes

REPLAY_STEPS = 480  # covers consecutive ability frames and sparse passive-charge deadlines
EMP = 0x0B31
EMP_APPLICATION = 0x0B32
HACK_APPLICATION = 0x0B1F
EMP_ABILITY = 1  # node 3
EMP_TAIL = (6, 16)  # nodes 15 and 38
EMP_CAST_FRAMES = 64  # the authored pulse at +21 and cast end at +40


def _emp(script):
    return next((instance for instance in script.component.instances.values()
                 if instance.graph.index == EMP), None)


def _emp_casting(instance):
    ability = instance.states.get(EMP_ABILITY)
    return isinstance(ability, nodes.Ability) and bool(ability.flags & nodes.Ability.RUNNING)


def _emp_pending(script):
    instance = _emp(script)
    return instance is not None and (
        _emp_casting(instance)
        or any((state := instance.states.get(index)) is not None and state.active for index in EMP_TAIL)
    )


def emp_replay_frames(fight):
    for player in fight.match.players:
        script = player.body_script
        if (script is None or script.hero != sombra_camo.HERO or script.retired
                or not fight.match._shown(player) or fight.is_dead(player)):
            continue
        instance = _emp(script)
        if instance is not None and _emp_casting(instance):
            return EMP_CAST_FRAMES
    return 0


def _shown_sources(match):
    return {player.body for player in match.players
            if match._shown(player) and player.body_script is not None}


def application_replay_until(fight, previous):
    if not fight.hacks:
        return previous
    sources = _shown_sources(fight.match)
    # An authored source check can queue a subsequent frame wait before stopping.
    return max((effect.until_tick for effects in fight.hacks.values() for effect in effects
                if effect.application.source_entity() not in sources), default=previous)


def advance_applications(match, tick):
    """Advance incoming EMP and source retirement without consuming recipient input."""
    sources = _shown_sources(match)
    scripts = [player.body_script for player in match.players if match._shown(player)]
    scripts.extend(
        bot.body_script for bot in match.bots if not bot.dead and any(
            effect.application.source_entity() not in sources
            for effect in match.combat.hacks.get(bot.entity, ())
        )
    )
    for script in scripts:
        if (script is None or script.frame >= tick or getattr(script, "retired", False)
                or script.component.owner_dead):
            continue
        component = script.component
        end = (tick + 1) * component.frame_ms - 1
        for instance in tuple(component.instances.values()):
            orphaned_hack = (instance.graph.index == HACK_APPLICATION
                             and instance.source_entity() not in sources)
            if instance.start > end or (instance.graph.index != EMP_APPLICATION and not orphaned_hack):
                continue
            saved_frame, saved_now = component.frame, component.now
            with component.operation():
                try:
                    component.frame = tick
                    component.now = max(saved_now, end)
                    instance.update()
                    if instance.queue and instance.queue[0].time <= component.now:
                        instance.update()  # the authored ChaseVar can post its finish on this update
                finally:
                    component.frame, component.now = saved_frame, saved_now


def _next_frame(script, fight, player):
    if (player.body in fight.translocators.recall_poses or translocators.replay_pending(script)
            or _emp_pending(script)):
        return script.frame + 1
    camo = sombra_camo.next_frame(script)
    charge = fight.ultimate_charge.next_passive_frame(script)
    if camo is None:
        return charge
    return camo if charge is None else min(camo, charge)


def replay(fight, tick, dt):
    """Advance accepted reactions before the next world contact or real owner frame."""
    match = fight.match
    if match.ended or tick > MAX_LEASE_FRAME:
        return
    scheduled = match.tick
    try:
        for player in match.players:
            script = player.body_script
            if (script is None or script.hero != sombra_camo.HERO or script.retired
                    or script.frame >= tick or not match._shown(player) or fight.is_dead(player)):
                continue
            mover = player.mover
            for step in range(REPLAY_STEPS):
                if player.body_script is not script:
                    break
                frame = _next_frame(script, fight, player)
                if frame is None or frame > tick:
                    break
                if step == 0:
                    frame = script.frame + 1  # release prior input before a sparse deadline
                match.tick = frame
                command = Command(frame, yaw=mover.yaw, pitch=mover.pitch)
                if player.body in fight.translocators.recall_poses:
                    mover.last_command = command
                    player._step_body_pose(command, dt, frame, replay_accepted=True)
                    history = fight.history.get(player.body)
                    if history and history[-1][0] >= frame:
                        at, _, height, eye = history[-1]
                        history[-1] = at, tuple(mover.position), height, eye
                else:
                    player._advance_body_frame(command, posed=False, replay_accepted=True)
    finally:
        match.tick = scheduled
