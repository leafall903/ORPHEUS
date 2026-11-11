"""Reconstructed Tracer Blink and Recall, driven by actual 104319 body graphs.

Distance and charge/timer flow come from 104319 resources. Horizontal throttle
selection, capsule sweep clipping and momentum preservation are reconstructed
server policies; this is not an emulation of the native A807207A action.
Recall's warmup completion uses its fade-in interval, playback uses the authored
duration, and recorded poses/health replace missing engine rewind services.
Cleanse, native animation-event timing and full cross-hero statuses remain open.
"""

import math
from collections import deque
from dataclasses import dataclass

from ow174.game import contacts
from ow174.game.mover import turned_pitch
from ow174.game.script import expr, nodes
from ow174.game.world import HEALTH_POOL

HERO = 0x02E0000000000003
BLINK_GRAPH = 0x0024
BLINK_ACTION = "STU_A807207A"
RECALL_GRAPH = 0x001A
MOBILITY_BUTTONS = (29, 35, 36, 110, 112, 114)


@dataclass(frozen=True)
class RecallSample:
    time: int
    position: tuple
    health: float


@dataclass
class RecallSession:
    component: object
    sample: RecallSample
    state: object
    later_positions: tuple[tuple, ...]


def _player(world, component=None):
    if world.hero != HERO or world.match is None:
        return None
    player = next((p for p in world.match.players if p.has_body and p.body == world.body), None)
    if (player is None or player.body_script is None
            or (component is not None and player.body_script.component is not component)):
        return None
    return player


def remember(world, frame):
    """Retain real posed history per body; replacing the body replaces its world."""
    player = _player(world)
    if player is None or world.match.collision_world is None:
        return
    component = player.body_script.component
    if component.owner_dead or not player.mover.history or player.mover.history[-1].frame != frame:
        return
    recall = next((i for i in component.instances.values() if i.graph.index == RECALL_GRAPH), None)
    if recall is None:
        return
    lookback = expr.to_float(recall.find_var(expr.INSTANCE, 7125).value())
    now = frame * component.frame_ms
    target = next((t for t in world.match.combat._targets() if t.owner is player), None)
    if target is None:
        return
    vitals = world.match.combat.vitals_of(target)
    history = getattr(world, "recall_history", None)
    if history is None:
        history = world.recall_history = deque()
    if history and history[-1].time >= now:
        return
    history.append(RecallSample(now, tuple(player.mover.position), vitals.now[HEALTH_POOL]))
    cutoff = now - max(0.0, lookback) * 1000
    while len(history) > 1 and history[1].time <= cutoff:
        history.popleft()


def begin_recall(world, state, playback):
    """Resource-driven warmup/playback; engine behavior is reconstructed here."""
    component, instance = state.instance.component, state.instance
    player = _player(world, component)
    if (player is None or instance.graph.index != RECALL_GRAPH or component.owner_dead
            or state.node.pos != (1 if playback else 4)):
        return None
    if playback:
        session = getattr(world, "recall_session", None)
        if session is None or session.component is not component:
            return None
        session.state = state
        seconds = expr.to_float(state.evaluate(state.node.field("m_FAF3B2D3"), watch=False))
    else:
        history = getattr(world, "recall_history", ())
        if not history:
            return None
        seconds_back = expr.to_float(instance.find_var(expr.INSTANCE, 7125).value())
        cutoff = component.now - seconds_back * 1000
        sample = next((s for s in reversed(history) if s.time <= cutoff), history[0])
        later_positions = tuple(pose.position for pose in history if pose.time > sample.time)
        world.recall_session = RecallSession(component, sample, state, later_positions)
        player.match.combat.zenyatta_orbs.clear_discord(player.body)
        # The authored fade-in ChaseVar supplies the preparatory interval. This
        # timing policy substitutes for the missing native 54FEB21C completion.
        seconds = expr.to_float(state.evaluate(instance.graph.node(16).field("m_5BEFF040"),
                                              watch=False))
    return max(0, round(seconds * 1000))


def end_recall(world, state, playback, finished):
    session = getattr(world, "recall_session", None)
    if session is None or session.component is not state.instance.component:
        return
    if not playback and finished:
        return  # the authored end plug begins playback in the same event drain
    world.recall_session = None
    if not finished:
        # Cancellation cannot be used to avoid this cast's authored cooldown.
        state.instance.write((expr.INSTANCE, 61),
                             state.instance.find_var(expr.INSTANCE, 472).value())
    player = _player(world, session.component)
    if not playback or not finished or player is None or session.component.owner_dead:
        return
    target = next((t for t in world.match.combat._targets() if t.owner is player), None)
    if target is None:
        return
    vitals = world.match.combat.vitals_of(target)
    if vitals.dead:
        return
    # Recall never reduces current health or resurrects a retired/dead body.
    vitals.healing_block_until = 0
    restored = min(session.sample.health, vitals.most[HEALTH_POOL])
    vitals.heal(max(0.0, restored - vitals.now[HEALTH_POOL]))
    world.match.combat._sync_body_health(player, vitals)
    mover = player.mover
    body = mover._engine_body()
    destination = session.sample.position
    walls = world.match.combat.walls
    if walls.occupies(mover, destination):
        # A newly raised wall can occupy the rewind point. Check the path
        # recorded at cast time, from the requested point toward the present.
        destination = next((p for p in session.later_positions if not walls.occupies(mover, p)),
                           tuple(mover.position))
    x, y, z = destination
    mover._place(body, (x, y + mover._lift(), z), False)
    mover.velocity = (0.0, 0.0, 0.0)
    mover.fall = 0.0
    if mover.history and mover.history[-1].frame == session.component.frame:
        mover.history[-1] = mover.snapshot(session.component.frame)
    for weapon in session.component.instances.values():
        if weapon.graph.index == 0x0016:
            capacity = weapon.find_var(expr.INSTANCE, 198).value()
            weapon.write((expr.INSTANCE, 53), capacity)


def recalling(owner):
    script = getattr(owner, "body_script", None)
    world = getattr(script, "world", None)
    session = getattr(world, "recall_session", None)
    return (session is not None and session.component is script.component
            and not script.component.owner_dead and session.state.active)


def hold_pose(player, command):
    """Freeze translation during the reconstructed invulnerable recall interval."""
    mover = player.mover
    mover.yaw = command.yaw
    mover.pitch = turned_pitch(command.pitch)[0]
    mover.history.append(mover.snapshot(command.frame))


def discard_mobility_input(player):
    script = player.body_script
    if script is None or script.hero != HERO:
        return
    component = script.component
    released = [(button, False) for button in component.held if button in MOBILITY_BUTTONS]
    for button, _ in released:
        component.held.remove(button)
    component.edges(released)
    for instance in component.instances.values():
        if instance.graph.index not in (BLINK_GRAPH, RECALL_GRAPH):
            continue
        for state in instance.states.values():
            if isinstance(state, nodes.LogicalButton):
                state.pressed_at = None
            elif isinstance(state, nodes.Ability):
                state.presses.clear()


def filter_edges(world, edges):
    player = _player(world)
    if player is not None and player.body in world.match.combat.gravitons.player_pulls:
        # Reject new Blink and Recall presses while the world has captured this body.
        # Preserve releases so a rejected press cannot start when the pull expires.
        return [(button, down) for button, down in edges if not (down and button in MOBILITY_BUTTONS)]
    if player is not None and recalling(player):
        # Blink's logical-button nodes do not consult the ordinary ability
        # stack. Reconstruct its Recall exclusion before a press enters them.
        return [(button, down) for button, down in edges if not (down and button in (29, 35))]
    return edges


def blink(world, instance, node):
    """Handle only a currently posed, live Tracer's actual Blink action."""
    match = world.match
    if world.hero != HERO or instance.graph.index != BLINK_GRAPH or node.pos != 3:
        return NotImplemented
    if match is None or match.collision_world is None:
        return NotImplemented
    player = next((p for p in match.players if p.has_body and p.body == world.body), None)
    component = instance.component
    if (player is None or player.body_script is None
            or player.body_script.component is not component or component.owner_dead
            or recalling(player)):
        return False
    mover = player.mover
    if not mover.history or mover.history[-1].frame != component.frame:
        return False  # no authoritative pose for a recovered/unposed script frame
    distance = expr.to_float(instance.evaluate(node.field("m_6A79BD98")))
    if not math.isfinite(distance) or distance <= 0:
        return False
    forward, right = world.throttles
    if not (forward or right):
        forward = 127
    length = math.hypot(forward, right)
    angle = mover.yaw * 2 * math.pi / 65536
    dx = (math.sin(angle) * forward - math.cos(angle) * right) / length * distance
    dz = (math.cos(angle) * forward + math.sin(angle) * right) / length * distance
    body = mover._engine_body()
    start = body.position
    end = (start[0] + dx, start[1], start[2] + dz)
    hit = contacts.scene_sweep(mover.world.engine, start, end, body.local_points(),
                               body.radius, mover.category)
    share = max(0.0, hit[0] - 0.001 / distance) if hit is not None else 1.0
    final = (start[0] + dx * share, start[1], start[2] + dz * share)
    before = tuple(mover.position)
    mover._place(body, final, hit is not None)
    match.combat.walls.move_player(player, before)
    # The action runs after the normal pose. Replace its same-frame correction
    # snapshot instead of leaving the owning client at the pre-Blink position.
    mover.history[-1] = mover.snapshot(component.frame)
    return True
