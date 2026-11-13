"""Bounded 104319 Ball physics bridge; authored graphs still own ability timing.

Reconstruction: #417 is airborne, #545 is grounded, and the authored downward
RayCast supplies #2413. Piledriver's numeric34=-98 is treated as downward
acceleration, its mapping0148=40 as the speed cap, and the authored -5 vector as
initial descent. These physical meanings have not had original-client acceptance.
"""

import math
import os
from dataclasses import dataclass
from pathlib import Path

from ow174.game.mover import WORLD_GRAVITY
from ow174.game.script import expr, nodes, runtime

HERO = 0x02E00000000001CA
PILEDRIVER = 0x160A
GRAPPLE = 0x14E4


def animation_duration(world, graph, node):
    """Resolve the Roll intro in its body/model context, without a time fallback."""
    if (graph.component.strict or world.hero != HERO
            or graph.graph.index != 0x1203 or node.pos != 43):
        return NotImplemented
    alias = graph.evaluate(node.field('m_11E0A658'))
    category = graph.evaluate(node.field('m_22326315'))
    if not isinstance(alias, expr.Asset) or not isinstance(category, expr.Asset):
        return None
    from ow174.game.script.driver import wheel_animation_durations
    from ow174.paths import DATA_DIR

    configured = os.environ.get('ORPHEUS_BALL_ANIMATION_DURATIONS')
    path = Path(configured) if configured else DATA_DIR / 'ball_animation_durations_174.json'
    # The extractor maps STUModelComponent.m_animBlendTreeSet through the body
    # definition. Exact categories only; unsupported branches have no default.
    key = world.body_definition, alias.guid, category.guid
    return wheel_animation_durations(path).get(key)


@dataclass
class Grapple:
    activation: tuple[int, int]
    launched: int
    arrives: int
    anchor: tuple | None
    reach: float
    normal: tuple = (0.0, 0.0, 0.0)
    attached: bool = False
    rope: float = 0.0


def body_frame_at_world(script, tick):
    """Map the actual creation pair, without enabling/changing input leases."""
    from ow174.game.combat import TICK
    world, body = script.ball_birth
    return body + (tick - world) * round(TICK * 1000) // script.frame_ms


def world_tick_at_body(script, frame):
    """First real world tick whose mapped body cursor reaches this frame."""
    from ow174.game.combat import TICK
    world, body = script.ball_birth
    quantum = round(TICK * 1000)
    elapsed = (frame - body) * script.frame_ms
    return world + (elapsed + quantum - 1) // quantum


def view_world_tick(script, command, quanta):
    """Sample target world history at the owner's reported view time, without rounding."""
    from ow174.game.combat import TICK

    world, owner = script.ball_birth
    elapsed_ms = (command.frame - owner) * script.frame_ms - command.view_delay(quanta) * 1000
    return world + elapsed_ms / round(TICK * 1000)


def body_pitch(world, cfg, instance):
    """104319 CC8200 -> D1C220 -> C61F70 body pitch query, in degrees."""
    if (cfg.get('$') != 'STU_40AC5AE4' or world.hero != HERO
            or instance is None or instance.component.strict
            or instance.graph.index not in (0x1203, GRAPPLE)
            or world.match is None or instance.component.entity != world.body):
        return NotImplemented
    entity = instance.evaluate(cfg.get('m_entity'))
    if entity != expr.Entity(world.body):
        return NotImplemented
    player = world.match.script_player_body(world.body, world.body)
    if (player is None or player.body_script is None
            or player.body_script.component is not instance.component):
        return NotImplemented
    units = player.mover.pitch
    if type(units) is not int or not -32768 <= units <= 32767:
        return NotImplemented
    # Native C61F70 and D1C972 round these two multiplications separately.
    radians = expr.f32(units * 9.58738019107841e-05)
    return expr.f32(radians * 57.295780181884766)


def shield_enemy_count(fight, player, radius):
    """Use the existing E grant's eye/capsule query for its graph count too.

    This removes the separate feet-distance query; native filter completeness
    and the reported training-range count still require client acceptance.
    """
    from ow174.game.ball_mines import Mine
    from ow174.game.combat import segment_distance

    origin = fight._eye(player)
    return sum(
        segment_distance(origin, *target.segment()) - target.radius <= radius
        for target in fight._targets()
        if target.owner is not player and not isinstance(target.owner, Mine)
        and (fight.match.game_map.free_for_all or target.team != player.team)
        and not fight.vitals_of(target).dead
    )


def instance(player, index=PILEDRIVER):
    script = player.body_script
    if player.hero.guid != HERO or script is None:
        return None
    return next((i for i in script.component.instances.values() if i.graph.index == index), None)


def rolling(player):
    """1203's active component-modifier state is the authored Roll transform lifetime."""
    graph = instance(player, 0x1203)
    state = graph.states.get(18) if graph is not None else None
    return state is not None and state.active


def grapple_gap_buttons(script, buttons):
    """Retain confirmed Grapple and Roll holds across missing command frames."""
    if (script is None or script.hero != HERO or script.component.strict
            or buttons & 1):
        return 0
    return script.buttons & buttons & (2 | 8)


def cannon_direction(shooter, state, forward, number: int, time: int, activation):
    """1514's authored cone with native table math and declared server identities.

    Native handle/counter mapping, radial mode and animated muzzle transforms
    remain unproved. Uniform radius/aspect1 and body/script/node/counter seed
    identities are explicit reconstruction policies for this scoped bridge.
    """
    if (shooter.hero.guid != HERO or shooter.body_script is None
            or state.instance.component is not shooter.body_script.component
            or state.instance.graph.index != 0x1514 or state.node.pos != 24):
        return forward
    launch = state.node.field('m_5EC1EB38')
    if (not isinstance(launch, dict) or launch.get('$') != 'STU_239B571E'
            or state.spread_ramp is None):
        return forward
    from ow174.game import weapon_spread

    low, high = (expr.f32(expr.to_float(state.evaluate(launch.get(name), watch=False)))
                 for name in ('m_7F0FED02', 'm_429A6C85'))
    normalized = state.spread_at(time - state.start - state.offset)
    degrees = expr.f32(expr.f32(expr.f32(high - low) * normalized) + low)
    cone = expr.f32(degrees * expr.f32(math.pi / 180))
    base = weapon_spread.seed_base(shooter.body, state.instance.id, state.node.pos, activation[0])
    return weapon_spread.direction(forward, cone, base, number)


def generation_factor(script, key):
    value = script.world.ruleset(key)
    if value is None:
        return 1.0
    factor = expr.to_float(value)
    return factor if 0 <= factor < float("inf") else 0.0


def add_ult_charge(script, points):
    """Delivered Ball combat award, including exact general/category settings."""
    from ow174.game.script.driver import ULT_CHARGE, ULT_COST
    if (script.retired or script.component.owner_dead
            or any(state.flags & nodes.Ability.RUNNING for state in script.ultimates)):
        return 0.0
    points *= generation_factor(script, 0x253C) * generation_factor(script, 0x32DE)
    cost = expr.to_float(script.component.entity_var(ULT_COST).value())
    charge = expr.to_float(script.component.entity_var(ULT_CHARGE).value())
    if not 0 < points < float("inf") or cost <= charge:
        return 0.0
    target = min(cost, expr.f32(charge + points))
    script.set_ult_charge(target)
    return target - charge


def prepare_passive(script):
    enabled = script.ult_seconds is None and not script.retired and not script.component.owner_dead
    gate = script.component.entity_var(2426, create=False)
    if enabled or (gate is not None and expr.truthy(gate.value())):
        script.component.set(2426, enabled)
    if enabled:
        script.component.set(9532, generation_factor(script, 0x253C))
        script.component.set(12918, generation_factor(script, 0x3276))


def diagnostic_charge(script):
    from ow174.game.script.driver import ULT_CHARGE, ULT_COST
    if (script.retired or script.component.owner_dead or not script.ult_seconds
            or script.ult_seconds < 0
            or any(state.flags & nodes.Ability.RUNNING for state in script.ultimates)):
        return
    cost = expr.to_float(script.component.entity_var(ULT_COST).value())
    charge = expr.to_float(script.component.entity_var(ULT_CHARGE).value())
    if cost > 0 and charge < cost:
        factor = generation_factor(script, 0x253C) * generation_factor(script, 0x3276)
        step = expr.f32(cost * script.frame_ms / (1000.0 * script.ult_seconds) * factor)
        if step > 0:
            script.set_ult_charge(min(cost, expr.f32(charge + step)))


def passive_owner(world, cfg, instance):
    """Original0B8B own-body possessor query; retirement cannot rebind an old VM."""
    if (world.match is None or instance.graph.index != 0x0B8B
            or cfg.get("$") != "STU_F7262B36"
            or (cfg.get("m_801B298F") or {}).get("$") != "STU_B5A0CAF0"
            or instance.component.entity != world.body):
        return NotImplemented
    if instance.stopped or instance.teardown:
        return None
    owner = next((p for p in world.match.players
                  if p.has_body and p.body == world.body and not p.gone), None)
    return expr.Entity(owner.entity) if owner is not None else None


def feedback(player):
    """Feed actual collision/mover state into the bounded Piledriver graph contract."""
    graph = instance(player)
    if graph is None:
        return
    component = graph.component
    component.set(417, player.mover.airborne)
    component.set(545, not player.mover.airborne)
    node = graph.graph.states[38]
    origin = graph.evaluate(node.field('m_startPosWS'))
    direction = graph.evaluate(node.field('m_dirWS'))
    length = expr.to_float(graph.evaluate(node.field('m_castLength')))
    if not isinstance(origin, expr.Vec3) or not isinstance(direction, expr.Vec3):
        return
    magnitude = math.sqrt(sum(axis * axis for axis in direction))
    if magnitude <= 0 or length <= 0:
        return
    direction = tuple(axis / magnitude for axis in direction)
    hit = player.mover.world.cast_ray(tuple(origin), direction, length, player.mover.category)
    component.set(2413, hit is not None)


def motion(player, dt, command=None):
    """Use collision sweeps for a live graph's windup/descent, without a second cooldown clock."""
    graph = instance(player)
    if graph is None or graph.component.owner_dead:
        return None
    windup = graph.states.get(18)
    if windup is not None and windup.active:
        player.mover.velocity = (0.0, 0.0, 0.0)
        # The graph's gravity-zero modifier takes effect on the next movement tick.
        return tuple(player.mover.position), 1.0
    descending = graph.states.get(3)
    if descending is None or not descending.active or not player.mover.airborne:
        grapple = player.match.combat.ball_grapples.get(player.body)
        if grapple is not None and grapple.attached:
            controller = instance(player, GRAPPLE)
            limit = expr.to_float(controller.evaluate(controller.graph.states[0].field('m_2C40C158')))
            if math.dist(player.mover.position, grapple.anchor) > limit:
                cancel_grapple(player.match.combat, player)
                return None
            return _swing(player, dt, command, grapple)
        # Preserve airborne Roll momentum after releasing a driven tether. The
        # ordinary mover otherwise clears driven velocity and caps it immediately.
        if rolling(player) and player.mover.airborne and math.hypot(
            player.mover.velocity[0], player.mover.velocity[2],
        ) > player.mover.data.run_forward:
            return _swing(player, dt, command, None)
        return None
    # Upstream consumes authored numeric channel 34 as vertical velocity, not
    # the older reconstructed acceleration/cap bridge. Keep windup and grapple
    # hosting above; the ordinary mover now owns the descending collision sweep.
    return None


def update_grapple(fight, player, command, direction):
    """Bridge native launch/hit scopes to a static-map anchor; not a general hook backend.

    Reach/speed/cooldown are evaluated from14E4. Eye origin, static-map filtering,
    arrival quantization and rope constraint semantics are reconstructed.
    """
    graph = instance(player, GRAPPLE)
    if graph is None:
        return
    state = graph.states.get(0)
    ability = graph.states.get(25)
    owned = fight.ball_grapples.get(player.body)
    if command is None:
        return
    if owned is not None and (not command.buttons & 2 or command.buttons & 1 or not rolling(player)
                              or ability is None or not ability.flags & nodes.Ability.RUNNING):
        cancel_grapple(fight, player)
        return
    if owned is not None and owned.attached:
        limit = expr.to_float(graph.evaluate(graph.graph.states[0].field('m_2C40C158')))
        if math.dist(player.mover.position, owned.anchor) > limit:
            cancel_grapple(fight, player)
            return
    if owned is None and state is not None and state.active:
        activation = graph.id, state.counter
        if fight.grapple_seen.get(player.body) == activation:
            return
        fight.grapple_seen[player.body] = activation
        if state.start // graph.component.frame_ms != command.frame:
            return  # a retained activation without its live aim cannot create an anchor
        script = player.body_script
        interrupt = _grapple_interrupt_after(script, command.frame, fight.tick)
        if interrupt is not None:
            # Replay the graph and cooldown without restoring an interrupted world tether.
            _defer_grapple_interrupt(script, interrupt)
            return
        reach = expr.to_float(state.evaluate(state.node.field('m_2AFC083F'), watch=False))
        speed = expr.to_float(state.evaluate(state.node.field('m_speed'), watch=False))
        if reach <= 0 or speed <= 0:
            return
        origin = fight._eye(player)
        hit = fight._collision().cast_ray(origin, direction, reach, player.mover.category)
        distance = hit.distance if hit is not None else reach
        travel = max(1, math.ceil(distance / (speed * player.quanta / 1e6)))
        owned = Grapple(activation, command.frame, command.frame + travel,
                        tuple(hit.point) if hit is not None else None, reach,
                        tuple(hit.normal) if hit is not None else (0.0, 0.0, 0.0))
        graph.write((expr.INSTANCE, 435), False)
        fight.ball_grapples[player.body] = owned
    if owned is None or owned.attached or command.frame < owned.arrives:
        return
    if owned.anchor is None:
        cancel_grapple(fight, player)
        return
    owned.rope = math.dist(player.mover.position, owned.anchor)
    if owned.rope <= 0:
        cancel_grapple(fight, player)
        return
    owned.attached = True
    graph.write((expr.INSTANCE, 431), expr.Vec3(*owned.anchor))
    # 14E4's cast outputs separate contact (431/1220) and hook position (432).
    # CosmeticEntity93 and its transform/aim expressions consume 432 as a point,
    # including after attachment. The pendulum's scalar rope stays in Grapple.
    graph.write((expr.INSTANCE, 1220), expr.Vec3(*owned.anchor))
    graph.write((expr.INSTANCE, 434), expr.Vec3(*owned.normal))
    graph.write((expr.INSTANCE, 38), expr.Entity(0))  # static map, no actor/bone
    graph.write((expr.INSTANCE, 8695), 0)
    graph.write((expr.INSTANCE, 1221), True)
    graph.write((expr.INSTANCE, 432), expr.Vec3(*owned.anchor))
    graph.write((expr.INSTANCE, 435), True)
    state.request(runtime.FINISH)
    graph.component.run_now()  # actual hit branch retains the authored running ability


def cancel_grapple(fight, player):
    """Release owned motion and start the authored cooldown without clearing momentum.

    Applying the full cooldown to misses/early release is a reconstruction policy;
    exact native failed-cast/retract timing remains an open acceptance row.
    """
    if fight.ball_grapples.pop(player.body, None) is None:
        return
    player.mover.driven = False
    _end_grapple(player)


def interrupt_grapple(fight, player, frame):
    """Release world motion now, but start the graph cooldown at the impact frame."""
    if player.body not in fight.ball_grapples:
        return
    script = player.body_script
    if script.frame >= frame:
        cancel_grapple(fight, player)
        _latch_interrupted_grapple(player)
    else:
        fight.ball_grapples.pop(player.body)
        player.mover.driven = False
        _defer_grapple_interrupt(script, frame)


def _defer_grapple_interrupt(script, frame):
    script.grapple_interrupt_frame = min(frame, getattr(script, 'grapple_interrupt_frame', frame))


def _grapple_interrupt_after(script, frame, tick):
    controls = script.world.match.combat.sombra_controls
    body_tick = body_frame_at_world(script, tick)
    skill_lock = controls.first_lock_after(script, frame, body_tick)
    latest = body_tick if skill_lock is None else skill_lock
    windows = (script.control_windows, script.sleep_windows, script.frost_windows)
    return min((start for group in windows for start, _ in group if frame < start <= latest),
               default=skill_lock)


def settle_grapple_interrupt(fight, player, frame):
    script = player.body_script
    pending = getattr(script, 'grapple_interrupt_frame', None)
    if pending is not None and frame >= pending:
        del script.grapple_interrupt_frame
        fight.ball_grapples.pop(player.body, None)
        player.mover.driven = False
        _end_grapple(player)
        _latch_interrupted_grapple(player)


def _latch_interrupted_grapple(player):
    # Synthetic gap frames must not release the last real secondary press.
    last = player.mover.last_command
    if last is not None and last.buttons & 2:
        script = player.body_script
        controls = script.world.match.combat.sombra_controls
        controls.held[player.body] = controls.held.get(player.body, 0) | 2


def _end_grapple(player):
    graph = instance(player, GRAPPLE)
    if graph is None:
        return
    state, ability = graph.states.get(0), graph.states.get(25)
    if state is not None and state.active:
        graph.write((expr.INSTANCE, 435), False)
        state.end(False)
    if ability is not None:
        ability.stop()
    graph.write((expr.INSTANCE, 61), graph.vars[472].value())
    graph.component.run_now()


def _swing(player, dt, command, grapple):
    """Resource-driven pendulum reconstruction with the mover's collision sweep.

    Uses body gravity/drag/air acceleration, not invented native grapple force
    constants. Exact native steering, reeling, duration and sphere collision stay open.
    """
    mover = player.mover
    vx, vy, vz = mover.velocity
    mover._friction(dt, (vx, 0.0, vz), vy, False)
    vx, vy, vz = mover.velocity
    data = mover.values
    vy -= WORLD_GRAVITY * data.gravity_scale * dt
    if command is not None:
        yaw = command.yaw * 2 * math.pi / 65536
        forward, right = command.forward / 127.0, command.right / 127.0
        wx = forward * math.sin(yaw) - right * math.cos(yaw)
        wz = forward * math.cos(yaw) + right * math.sin(yaw)
        length = math.hypot(wx, wz)
        if length > 0:
            wx, wz = wx / length, wz / length
            add = min(max(0.0, data.run_forward - vx * wx - vz * wz),
                      data.accel_air * data.run_forward * min(length, 1.0) * dt)
            vx, vz = vx + wx * add, vz + wz * add
    position = tuple(mover.position)
    desired = tuple(p + v * dt for p, v in zip(position, (vx, vy, vz), strict=True))
    if grapple is not None:
        radial = tuple(p - a for p, a in zip(desired, grapple.anchor, strict=True))
        length = math.sqrt(sum(v * v for v in radial))
        if length > grapple.rope:
            desired = tuple(a + v * grapple.rope / length
                            for a, v in zip(grapple.anchor, radial, strict=True))
    delta = tuple(p - old for p, old in zip(desired, position, strict=True))
    speed = math.sqrt(sum(v * v for v in delta)) / dt
    if speed <= 1e-9:
        return position, 1.0
    target = tuple(p + 2 * v for p, v in zip(position, delta, strict=True))
    return target, speed
