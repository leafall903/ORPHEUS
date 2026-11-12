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


def _next_frame(script, fight, player):
    if (player.body in fight.translocators.recall_poses or translocators.replay_pending(script)
            or _emp_pending(script)):
        return script.frame + 1
    camo = sombra_camo.next_frame(script)
    charge = fight.sombra_charge.next_passive_frame(script)
    if camo is None:
        return charge
    return camo if charge is None else min(camo, charge)


def body_frame(script, world_tick, world_ms=16):
    """Translate using the actual accepted pose anchor, never equal epochs."""
    anchor = script.world_frame_anchor
    if anchor is None:
        return None
    at, frame = anchor
    return frame + (world_tick - at) * world_ms // script.frame_ms


def world_frame(script, frame, world_ms=16):
    anchor = script.world_frame_anchor
    if anchor is None:
        return None
    at, body = anchor
    return at + (frame - body) * script.frame_ms // world_ms


def replay_player(fight, player, tick, dt):
    """Replay accepted owner reactions through a world tick, without new input."""
    match = fight.match
    script = player.body_script
    if (match.ended or script is None or script.hero != sombra_camo.HERO
            or script.retired or script.component.owner_dead
            or not player.spawned or not player.has_body or not match._shown(player)
            or fight.is_dead(player)):
        return
    target = body_frame(script, tick, round(dt * 1000))
    if target is None or target >= MAX_LEASE_FRAME or target <= script.frame:
        return
    scheduled = match.tick, fight.tick, fight.now
    mover = player.mover
    try:
        for step in range(REPLAY_STEPS):
            if player.body_script is not script or fight.is_dead(player):
                break
            frame = _next_frame(script, fight, player)
            if frame is None or frame > target:
                break
            if step == 0:
                frame = script.frame + 1  # release prior input before a sparse deadline
            at = world_frame(script, frame, round(dt * 1000))
            match.tick = fight.tick = at
            fight.now = scheduled[2] + (at - scheduled[1]) * dt
            command = Command(frame, yaw=mover.yaw, pitch=mover.pitch)
            if player.body in fight.translocators.recall_poses:
                mover.last_command = command
                player._step_body_pose(command, dt, at, replay_accepted=True)
                history = fight.history.get(player.body)
                if history and history[-1][0] >= at:
                    when, _, *shape = history[-1]
                    history[-1] = (when, tuple(mover.position), *shape)
            else:
                player._advance_body_frame(command, posed=False, replay_accepted=True)
    finally:
        match.tick, fight.tick, fight.now = scheduled


def replay(fight, tick, dt):
    for player in fight.match.players:
        replay_player(fight, player, tick, dt)
