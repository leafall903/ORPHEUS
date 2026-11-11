"""Server movement service for Tracer's authored Blink action."""

import math
from collections import deque

from ow174.game import contacts, weapon_spread
from ow174.game.script import expr

HERO = 0x02E0000000000003
BLINK = 0x0024
BLINK_BUTTONS = (29, 35)
PISTOLS = (0x0016, 12)
MAX_SHOT_CONES = 256


def pistol_volley(state):
    return (state.instance.component.world.hero == HERO
            and (state.instance.graph.index, state.node.pos) == PISTOLS)


def pistol_cone(state, number):
    low, high = (expr.to_float(state.evaluate(state.node.field(f"m_5EC1EB38.{field}"), watch=False))
                 for field in ("m_7F0FED02", "m_429A6C85"))
    normalized = state.normalized_spread(number)
    degrees = expr.f32(low + expr.f32(expr.f32(high - low) * normalized))
    return max(0., expr.f32(degrees * expr.f32(math.pi / 180)))


def remember_pistol(state, number, time, activation):
    cones = getattr(state, "shot_cones", None)
    if cones is None:
        cones = state.shot_cones = {}
    cones[(*activation, number, time)] = pistol_cone(state, number)
    if len(cones) > MAX_SHOT_CONES:
        del cones[next(iter(cones))]


def pistol_rays(state, forward, body, number, time, activation, count):
    activation = activation or (state.counter, state.start)
    key = (*activation, number, time)
    cones = getattr(state, "shot_cones", {})
    cone = cones[key] if key in cones else pistol_cone(state, number)
    if cone == 0:
        return (forward,) * count
    seed = weapon_spread.seed_base(body, state.instance.id, state.node.pos, activation[0])
    return tuple(weapon_spread.direction(forward, cone, seed, number, i) for i in range(count))


def blink(world, instance, node):
    if (getattr(world, "hero", None), instance.graph.index, node.pos) != (HERO, BLINK, 3):
        return NotImplemented
    match = world.match
    if match is None or match.collision_world is None:
        return NotImplemented
    player = next((p for p in match.players if p.has_body and p.body == world.body), None)
    component = instance.component
    if (player is None or player.body_script is None or player.body_script.component is not component
            or component.owner_dead):
        return False
    mover = player.mover
    if not mover.history or mover.history[-1].frame != component.frame:
        return False
    distance = expr.to_float(instance.evaluate(node.field("m_6A79BD98")))
    if not math.isfinite(distance) or distance <= 0:
        return False
    forward, right = world.throttles
    if not (forward or right):
        forward = 127
    scale = distance / math.hypot(forward, right)
    yaw = mover.yaw * math.tau / 65536
    sine, cosine = math.sin(yaw), math.cos(yaw)
    dx = (sine * forward - cosine * right) * scale
    dz = (cosine * forward + sine * right) * scale
    body = mover._engine_body()
    x, y, z = body.position
    end = (x + dx, y, z + dz)
    hit = contacts.scene_sweep(mover.world.engine, body.position, end, body.local_points(),
                               body.radius, mover.category)
    share = max(0., hit[0] - .001 / distance) if hit is not None else 1.
    before = tuple(mover.position)
    mover._place(body, (x + dx * share, y, z + dz * share), hit is not None)
    match.combat.walls.move_player(player, before)
    mover.history[-1] = mover.snapshot(component.frame)
    return True


RECALL = 0x001A
RECALL_REPLAY_LIMIT = 100
RECALL_BLOCKED_BUTTONS = (14, 20, 29, 31, 35, 36, 37, 110, 111, 112, 114, 116)


def recall_instance(script):
    return next((i for i in script.component.instances.values() if i.graph.index == RECALL), None)


def remembering(player, frame):
    script = player.body_script
    if script.hero != HERO or script.component.owner_dead:
        return
    instance = recall_instance(script)
    if instance is None:
        return
    from ow174.game.world import HEALTH_POOL

    fight = player.match.combat
    target = next((t for t in fight._targets() if t.owner is player), None)
    if target is None:
        return
    now = frame * script.frame_ms
    history = fight.tracer_history.setdefault(player.body, deque())
    if history and history[-1][0] >= now:
        return
    hp = fight.vitals_of(target).now[HEALTH_POOL]
    history.append((now, tuple(player.mover.position), hp))
    seconds = expr.to_float(instance.find_var(expr.INSTANCE, 7125).value())
    cutoff = now - seconds * 1000
    while len(history) > 1 and history[1][0] <= cutoff:
        history.popleft()


def recall_session(player):
    script = getattr(player, "body_script", None)
    match = getattr(getattr(script, "world", None), "match", None)
    if getattr(script, "hero", None) != HERO or match is None or script.component.owner_dead:
        return None
    session = match.combat.tracer_recalls.get(player.body)
    if session is not None and session[0].instance.component is script.component and session[0].active:
        return session
    return None


def recalling(player):
    return recall_session(player) is not None


def begin_recall(state, *, playback):
    instance = state.instance
    component = instance.component
    world = component.world
    if (component.strict or component.owner_dead
            or (getattr(world, "hero", None), instance.graph.index, state.node.pos)
            != (HERO, RECALL, 1 if playback else 4) or world.match is None):
        return NotImplemented
    fight = world.match.combat
    body = component.entity
    if playback:
        session = fight.tracer_recalls.get(body)
        if session is None:
            return NotImplemented
        _, health, positions = session
        config = state.node.field("m_FAF3B2D3")
    else:
        history = fight.tracer_history.get(body)
        if not history:
            return NotImplemented
        cutoff = state.now - expr.to_float(instance.find_var(expr.INSTANCE, 7125).value()) * 1000
        index = next((i for i in range(len(history) - 1, -1, -1) if history[i][0] <= cutoff), 0)
        health = history[index][2]
        positions = tuple(point for i, (_, point, _) in enumerate(history) if i >= index)
        config = instance.graph.node(16).field("m_5BEFF040")
    fight.tracer_recalls[body] = state, health, positions
    return round(expr.to_float(state.evaluate(config, watch=False)) * 1000)


def end_recall(state, finished, *, playback):
    instance = state.instance
    component = instance.component
    match = getattr(component.world, "match", None)
    if match is None or instance.graph.index != RECALL:
        return
    fight = match.combat
    session = fight.tracer_recalls.get(component.entity)
    if session is None or session[0] is not state or (finished and not playback):
        return
    del fight.tracer_recalls[component.entity]
    if not finished:
        if not component.owner_dead and not instance.teardown:
            instance.write((expr.INSTANCE, 61), instance.find_var(expr.INSTANCE, 472).value())
        return
    if component.owner_dead:
        return
    target = next((t for t in fight._targets() if t.entity == component.entity), None)
    if target is None:
        return
    player = target.owner
    vitals = fight.vitals_of(target)
    if vitals.dead:
        return

    from ow174.game.world import HEALTH_POOL

    _, health, positions = session
    vitals.healing_block_until = 0
    vitals.heal(max(0., min(health, vitals.most[HEALTH_POOL]) - vitals.now[HEALTH_POOL]))
    fight._sync_body_health(player, vitals)
    mover = player.mover
    point = next((p for p in positions if fight.translocators.fits_destination(player, expr.Vec3(*p))), None)
    if point is not None:
        mover.teleport_grounded(point)
        if mover.history and mover.history[-1].frame == component.frame:
            mover.history[-1] = mover.snapshot(component.frame)
    for weapon in component.instances.values():
        if weapon.graph.index == 0x16:
            weapon.write((expr.INSTANCE, 53), weapon.find_var(expr.INSTANCE, 198).value())


def hold_pose(player, command):
    from ow174.game.mover import turned_pitch

    mover = player.mover
    mover.yaw, mover.pitch = command.yaw, turned_pitch(command.pitch)[0]
    mover.history.append(mover.snapshot(command.frame))


def replay(fight, tick, dt):
    if not fight.tracer_recalls or fight.match.ended:
        return
    from ow174.game.commands import Command
    from ow174.game.projectiles import MAX_LEASE_FRAME

    if tick > MAX_LEASE_FRAME:
        return
    match = fight.match
    scheduled = match.tick
    try:
        match.tick = tick
        for player in match.players:
            if not match._shown(player) or not recalling(player) or player.body_script.frame >= tick:
                continue
            mover = player.mover
            command = Command(tick, yaw=mover.yaw, pitch=mover.pitch)
            mover.last_command = command
            player._step_body_pose(command, dt, tick)
            history = fight.history.get(player.body)
            if history and history[-1][0] >= tick:
                at, _, height, eye = history[-1]
                history[-1] = at, tuple(mover.position), height, eye
    finally:
        match.tick = scheduled
