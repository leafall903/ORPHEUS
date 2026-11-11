"""Build 104319 Grappling Claw and Fireball host.

14E4 supplies launch reach, speed, release cooldown and attachment outputs.
1203 supplies the high-speed search, hit volume and ModifyHealth operands.
Static anchor admission, pendulum drive, cone/LOS and rehit selection are
server reconstructions.
"""

import math
from dataclasses import dataclass

from ow174.game import defenses
from ow174.game.commands import FIRE, SECONDARY
from ow174.game.mover import WORLD_GRAVITY
from ow174.game.piledriver import HERO
from ow174.game.script import expr, nodes, runtime

GRAPPLE, ROLL = 0x14E4, 0x1203
FIREBALL_LOADOUT = 0x0B900000000001E8


@dataclass
class Grapple:
    activation: tuple[int, int]
    launched: int
    arrives: int
    anchor: tuple | None
    reach: float
    attached: bool = False
    rope: float = 0.0


def instance(player, index):
    if player.hero.guid != HERO or player.body_script is None:
        return None
    component = player.body_script.component
    if component.strict or component.owner_dead:
        return None
    return next((item for item in component.instances.values()
                 if item.graph.index == index and not item.stopped and not item.teardown), None)


def rolling(player):
    roll = instance(player, ROLL)
    state = roll.states.get(18) if roll is not None else None
    return state is not None and state.active


def native(world, cfg, instance):
    if (world.hero != HERO or world.match is None or instance is None or instance.graph.index != ROLL
            or cfg.get("$") not in ("STU_309B0570", "STU_8CBDD81C", "STU_71A87E92")):
        return NotImplemented
    if instance.evaluate(cfg.get("m_entity")) != expr.Entity(world.body):
        return NotImplemented
    if cfg["$"] != "STU_309B0570":
        return world.match.script_body_position(world.body, world.body)
    if cfg.get("m_5B94EDB8") is not None:
        return NotImplemented
    owner = next((player for player in world.match.players
                  if player.body == world.body and player.has_body and not player.gone), None)
    return expr.Vec3(*owner.mover.velocity) if owner is not None else None


def movement(fight, player, seconds, command):
    grapple = fight.ball_grapples.get(player.body)
    if grapple is not None and grapple.attached:
        controller = instance(player, GRAPPLE)
        if controller is None:
            return None
        limit = expr.to_float(controller.evaluate(controller.graph.states[0].field("m_2C40C158")))
        if math.dist(player.mover.position, grapple.anchor) > limit:
            cancel_grapple(fight, player)
            return None
        return _swing(player, seconds, command, grapple)
    if (rolling(player) and player.mover.airborne
            and math.hypot(*player.mover.velocity[::2]) > player.mover.data.run_forward):
        return _swing(player, seconds, command, None)
    return None


def command(fight, player, command):
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
    if owned is not None and (
        not rolling(player) or ability is None or not ability.flags & nodes.Ability.RUNNING
        or (command is not None and (not command.buttons & SECONDARY or command.buttons & FIRE))
    ):
        cancel_grapple(fight, player)
        return
    if command is None:
        return
    from ow174.game.combat import aim

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
        reach = expr.to_float(state.evaluate(state.node.field('m_2AFC083F'), watch=False))
        speed = expr.to_float(state.evaluate(state.node.field('m_speed'), watch=False))
        if reach <= 0 or speed <= 0:
            return
        origin = fight._eye(player)
        hit = fight._collision().cast_ray(origin, aim(*command.aim()), reach, player.mover.category)
        distance = hit.distance if hit is not None else reach
        travel = max(1, math.ceil(distance / (speed * player.quanta / 1e6)))
        owned = Grapple(activation, command.frame, command.frame + travel,
                        tuple(hit.point) if hit is not None else None, reach)
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
    graph.write((expr.INSTANCE, 432), owned.rope)
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
    graph = instance(player, GRAPPLE)
    if graph is None:
        return
    state, ability = graph.states.get(0), graph.states.get(25)
    graph.write((expr.INSTANCE, 435), False)
    if state is not None and state.active:
        state.end(False)
    if ability is not None:
        ability.stop()
    graph.write((expr.INSTANCE, 61), graph.vars[472].value())
    graph.component.run_now()


def retire(fight, body):
    fight.ball_grapples.pop(body, None)
    fight.grapple_seen.pop(body, None)
    fight.ball_contacts.pop(body, None)
    for contacts in fight.ball_contacts.values():
        contacts.discard(body)


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


def fireball(fight, player, command):
    """Apply a hit from 1203's live search, once per outer-proximity crossing."""
    from ow174.game.combat import EPSILON, capsule_visible, segment_distance

    roll = instance(player, ROLL)
    if roll is None or not rolling(player):
        fight.ball_contacts.pop(player.body, None)
        return
    if command is None:
        return
    search = roll.states.get(35)
    damage = roll.states.get(39)
    proximity = roll.graph.node(57)
    offset = roll.evaluate(proximity.field("m_05BE62DD"))
    if not isinstance(offset, expr.Vec3):
        return
    centre = tuple(p + axis for p, axis in zip(player.mover.position, offset, strict=True))
    outer = expr.to_float(roll.evaluate(proximity.field("m_radius")))
    enemies = fight._enemies(player)
    nearby = {t.entity for t in enemies if segment_distance(centre, *t.segment()) - t.radius <= outer}
    contacts = fight.ball_contacts.setdefault(player.body, set())
    contacts.intersection_update(nearby)
    if search is None or not search.active or damage is None or not damage.active:
        return
    radius = expr.to_float(search.evaluate(search.node.field("m_radius"), watch=False))
    angle = expr.to_float(search.evaluate(search.node.field("m_A30B383E"), watch=False))
    velocity = player.mover.velocity
    speed = math.hypot(*velocity)
    if radius <= 0 or speed <= EPSILON or not 0 < angle <= 360:
        return
    direction = tuple(v / speed for v in velocity)
    modify = damage.node.field("m_modifyHealth")

    def number(field):
        return expr.to_float(damage.evaluate(modify[field], watch=False))

    amount, horizontal, vertical = map(number, ("m_amount", "m_E33EFEE1", "m_583C208B"))
    loadout = damage.evaluate(modify["m_loadout"], watch=False)
    planar = math.hypot(*velocity[::2])
    scale = horizontal / planar if planar > EPSILON else 0.0
    push = expr.Vec3(velocity[0] * scale, vertical, velocity[2] * scale)
    for target in enemies:
        if target.entity not in nearby or target.entity in contacts or defenses.protection(target.owner)[0]:
            continue
        a, b = target.segment()
        point = tuple((low + high) * .5 for low, high in zip(a, b, strict=True))
        delta = tuple(p - q for p, q in zip(point, centre, strict=True))
        distance = math.hypot(*delta)
        if segment_distance(centre, a, b) - target.radius > radius:
            continue
        if distance > target.radius:
            margin = math.asin(target.radius / distance)
            minimum = math.cos(min(math.pi, math.radians(angle * .5) + margin))
            if sum(v * d for v, d in zip(delta, direction, strict=True)) / distance < minimum:
                continue
        if not capsule_visible(fight._collision(), centre, a, b, target.radius, walls=fight.walls):
            continue
        contacts.add(target.entity)
        bubble = fight.zarya_bubbles.for_target(target.owner)
        fight._damage(player, target, amount, False, loadout.guid, command.frame)
        if bubble is None:
            fight.impulses.vector(target, push)
