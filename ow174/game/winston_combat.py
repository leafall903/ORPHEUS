"""Combat adapters for Winston's weapon, Barrier, Jump Pack and Primal Rage."""

import math
from dataclasses import dataclass

from ow174.game.script import expr, runtime

HERO = 0x02E0000000000009
DEFINITION = 0x04000000000001C3
RADIUS = 5.0  # 0D02 MSTU dynamic sphere, field ED61D926.
BASIS = ((1, 0, 0), (0, 1, 0), (0, 0, 1))


@dataclass
class Dome:
    owner: object
    team: int
    component: runtime.Component
    position: tuple
    health: float
    expiry_tick: int
    mesh = ()

    @property
    def entity(self):
        return self.component.entity

    def active(self):
        return self.health > 0

    def pose(self):
        return self.position, BASIS

    def damage(self, amount):
        self.health = max(0.0, self.health - max(0.0, amount))

    def ray(self, origin, direction, limit):
        offset = tuple(a - b for a, b in zip(origin, self.position, strict=True))
        projection = sum(a * b for a, b in zip(offset, direction, strict=True))
        c = sum(value * value for value in offset) - RADIUS * RADIUS
        discriminant = projection * projection - c
        if discriminant <= 0:
            return None
        root = math.sqrt(discriminant)
        if c < -1e-8:
            hit = -projection + root
        elif projection < 0:
            hit = max(0.0, -projection - root)
        else:
            return None
        return hit if hit <= limit else None

    def capsule_contact(self, a, b, radius):
        from ow174.game.combat import EPSILON, _along, _dot, _sub

        axis = _sub(b, a)
        length_sq = _dot(axis, axis)
        fraction = _dot(_sub(self.position, a), axis) / length_sq if length_sq > EPSILON else 0.
        closest = _along(a, axis, min(1., max(0., fraction)))
        near = math.dist(self.position, closest)
        farthest = max((a, b), key=lambda point: math.dist(self.position, point))
        far = math.dist(self.position, farthest)
        if near > RADIUS + radius or far < RADIUS - radius:
            return None
        point, distance = (closest, near) if near > EPSILON else (farthest, far)
        if distance <= EPSILON:
            return None
        return _along(self.position, _sub(point, self.position), RADIUS / distance)


def create_dome(world, instance, node):
    if (getattr(world, "hero", None), instance.graph.index, node.pos) != (HERO, 0xFA, 26):
        return NotImplemented
    if world.match is None or expr.guid_of(node.field("m_entityDefinition.m_entityDef")) != DEFINITION:
        return None
    fight = world.match.combat
    owner = next((p for p in world.match.players if p.has_body and p.body == world.body), None)
    if owner is None or fight.is_dead(owner):
        return None
    health = expr.to_float(instance.context().var(expr.INSTANCE, 587))
    duration = expr.to_float(instance.context().var(expr.ENTITY, 567))
    if health <= 0 or duration <= 0:
        return None
    from ow174.game.combat import TICK

    entity = fight.projectiles.allocate_seen_entity()
    child_world = type(world)(world.hero, world.mode, entity)
    child_world.match = world.match
    component = runtime.Component(entity, instance.component.frame_ms, child_world)
    component.frame, component.now = instance.component.frame, instance.component.now
    x, y, z = owner.mover.position
    dome = Dome(owner, owner.team, component, (x, y + .1, z), health,
                world.match.tick + math.ceil(duration / TICK))
    fight.barriers[entity] = dome
    return expr.Entity(entity)


def tesla(fight, player, command, script):
    if script.hero != HERO or command is None:
        return
    instance = weapon_instance(script)
    if instance is None:
        return
    search, damage = instance.states.get(10), instance.states.get(13)
    if search is None or damage is None or not (search.active and damage.active):
        return

    from ow174.game import reinhardt_barrier
    from ow174.game.combat import EPSILON, _dot, _sub, aim, hit_def, ray_world

    reach = expr.to_float(search.evaluate(search.node.field("m_radius"), watch=False))
    angle = expr.to_float(search.evaluate(search.node.field("m_A30B383E"), watch=False))
    hit = hit_def(damage, {"m_modifyHealth": damage.node.field("m_modifyHealth")})
    if reach <= 0 or not 0 < angle < 90 or hit.amount <= 0:
        return
    origin, direction = fight._eye(player), aim(*command.aim())
    view_tick = command.frame - command.view_delay(player.quanta) * 1e6 / player.quanta
    slope = math.tan(math.radians(angle))
    amount = hit.amount * script.frame_ms / 1000
    shields, pillars, bodies = {}, {}, []

    def blocked(heading, length):
        wall = ray_world(fight._collision(), origin, heading, length)
        limit = length if wall is None else wall
        shield = reinhardt_barrier.ray_hit(fight, player, origin, heading, limit)
        pillar = fight.walls.cast(origin, heading, shield[0] if shield else limit)
        if pillar is not None:
            pillars[pillar[1].entity] = pillar[1]
        elif shield is not None:
            shields[shield[1].entity] = shield[1]
        return pillar is not None or shield is not None or wall is not None

    blocked(direction, reach)
    for target in fight._enemies(player):
        shown = fight.seen(target, view_tick)
        if shown is None:
            continue
        lower, upper = shown.segment()
        point = lower[0], max(lower[1], min(upper[1], origin[1])), lower[2]
        offset = _sub(point, origin)
        distance = math.hypot(*offset)
        length = max(0.0, distance - shown.radius)
        forward = _dot(offset, direction)
        lateral = math.sqrt(max(0.0, distance * distance - forward * forward))
        if length > reach or forward < 0 or lateral > forward * slope + shown.radius:
            continue
        if distance <= EPSILON or not blocked(tuple(axis / distance for axis in offset), length):
            bodies.append(target)
    scaled = amount * fight.outgoing_scale(player.body)
    for shield in shields.values():
        reinhardt_barrier.damage(shield, scaled, attacker=player.body)
    for pillar in pillars.values():
        fight.walls.damage(pillar, player, scaled, command.frame)
    for target in bodies:
        fight._damage(player, target, amount, False, hit.loadout, command.frame,
                      tags=hit.tags, continuous_armour=True)


@dataclass
class Jump:
    instance: runtime.Instance
    activation: int
    frame: int
    flying: bool = True


JUMP_REPLAY_LIMIT = 240
PRIMAL_REPLAY_LIMIT = 660
IDLE_REPLAY_LIMIT = 120  # Covers the authored reload recovery and the 50 ms deployment.


def cancel_jump(fight, player):
    jump = fight.winston_jumps.pop(player.body, None)
    if jump is not None and jump.flying:
        finish_jump(jump)


def finish_jump(jump):
    jump.flying = False
    movement = jump.instance.states.get(2)
    if movement is not None and movement.active:
        movement.end(False)
        jump.instance.component.run_now()


def land(fight, player, command):
    jump = fight.winston_jumps.get(player.body)
    if jump is not None and jump.flying and command.frame > jump.frame and player.mover.landed:
        finish_jump(jump)
        jump_impact(fight, player, jump.instance, command.frame, landing=True)


def jump_command(fight, player, command, script):
    if script.hero != HERO or command is None:
        return
    instance = next((i for i in script.component.instances.values() if i.graph.index == 0x10A), None)
    if instance is None:
        return
    jump = fight.winston_jumps.get(player.body)
    cast = instance.states.get(26)
    if cast is None or not cast.counter:
        return
    if jump is not None and jump.instance is instance and jump.activation == cast.start:
        return
    if jump is not None and jump.flying:
        finish_jump(jump)
    movement = instance.state(2)
    if not movement.active:
        movement.begin()
        instance.component.run_now()

    from ow174.game.mover import FELL, JUMPED, REAL_JUMP

    config = instance.graph.node(85).field("m_value")["m_configVars"][0]
    minimum = expr.to_float(instance.evaluate(config["m_E26E5E09"]))
    maximum = expr.to_float(instance.evaluate(config["m_5023B9B0"]))
    speed = expr.to_float(instance.find_var(expr.INSTANCE, 1262).value())
    yaw, pitch = command.aim()
    elevation = math.radians(max(minimum, min(maximum, -pitch * 360 / 65536)))
    heading = yaw * math.tau / 65536
    horizontal = speed * math.cos(elevation)
    mover = player.mover
    mover.velocity = (math.sin(heading) * horizontal, speed * math.sin(elevation),
                      math.cos(heading) * horizontal)
    mover.flags_word = mover.flags_word & ~FELL | JUMPED | REAL_JUMP
    mover.air_ticks = 0
    mover.landed = False
    fight.winston_jumps[player.body] = Jump(instance, cast.start, command.frame)
    jump_impact(fight, player, instance, command.frame, landing=False)


def idle_pending(script):
    if script is None or script.hero != HERO or script.retired or script.component.owner_dead:
        return False
    for instance in script.component.instances.values():
        if instance.graph.index == 0xFA:
            deployment = instance.states.get(3)
            if deployment is not None and deployment.active:
                return True
        elif instance.graph.index == 0xFE:
            reload_gate = instance.vars.get(51)
            if reload_gate is not None and expr.truthy(reload_gate.value()):
                return True
    return False


def replay_frames(fight):
    if fight.winston_primal:
        return PRIMAL_REPLAY_LIMIT
    if any(jump.flying for jump in fight.winston_jumps.values()):
        return JUMP_REPLAY_LIMIT
    return IDLE_REPLAY_LIMIT if any(
        fight.match._shown(player) and idle_pending(player.body_script) for player in fight.match.players
    ) else 0


def replay(fight, tick, dt):
    from ow174.game.commands import Command
    from ow174.game.projectiles import MAX_LEASE_FRAME

    if fight.match.ended or tick > MAX_LEASE_FRAME or not replay_frames(fight):
        return

    match = fight.match
    scheduled = match.tick
    try:
        match.tick = tick
        for player in match.players:
            jump = fight.winston_jumps.get(player.body)
            active = (jump is not None and jump.flying) or player.body in fight.winston_primal
            script = player.body_script
            if (not match._shown(player) or script is None or script.frame >= tick
                    or fight.is_dead(player) or not (active or idle_pending(script))):
                continue
            mover = player.mover
            command = Command(tick, yaw=mover.yaw, pitch=mover.pitch)
            mover.last_command = command
            if not active:
                # Timers advance with released input, without granting another pose or attack.
                player._advance_body_frame(command, posed=False)
                continue
            player._step_body_pose(command, dt, tick)
            history = fight.history.get(player.body)
            if history and history[-1][0] >= tick:
                at, _, height, eye = history[-1]
                history[-1] = at, tuple(mover.position), height, eye
    finally:
        match.tick = scheduled


def jump_impact(fight, player, instance, frame, *, landing):
    from ow174.game import mei_wall, reinhardt_barrier
    from ow174.game.combat import EPSILON, _sub, hit_def, ray_world, segment_distance

    search = instance.state(4 if landing else 6)
    health = instance.state(3 if landing else 7)
    radius = expr.to_float(search.evaluate(search.node.field("m_radius"), watch=False))
    definition = hit_def(health, {"m_modifyHealth": health.node.field("m_modifyHealth")})
    base = expr.to_float(instance.find_var(expr.INSTANCE, 476).value()) if landing else definition.amount
    if radius <= 0 or base <= 0:
        return
    centre = tuple(player.mover.position)
    origin = centre[0], centre[1] + .1, centre[2]
    shields, hits = {}, []
    for target in fight._enemies(player):
        distance = max(0.0, segment_distance(centre, *target.segment()) - target.radius)
        if distance >= radius:
            continue
        scale = 1 - distance / radius if landing else 1.
        point = target.feet[0], target.feet[1] + target.height / 2, target.feet[2]
        delta = _sub(point, origin)
        length = math.hypot(*delta)
        shield = reinhardt_barrier.body_bubble(fight, target.owner)
        if length > EPSILON:
            direction = tuple(v / length for v in delta)
            limit = max(0.0, length - target.radius)
            if ray_world(fight._line_collision(), origin, direction, limit) is not None:
                continue
            intercepted = reinhardt_barrier.ray_hit(fight, player, origin, direction, limit,
                                                     include_bubbles=False)
            if intercepted is not None:
                shield = intercepted[1]
        if shield is None:
            hits.append((target, scale))
        else:
            prior = shields.get(shield.entity)
            if prior is None or scale > prior[1]:
                shields[shield.entity] = shield, scale
    for shield, scale in shields.values():
        reinhardt_barrier.damage(shield, base * scale * fight.outgoing_scale(player.body),
                                 attacker=player.body)
    for target, scale in hits:
        vitals = fight.vitals_of(target)
        before = vitals.total
        fight._damage(player, target, base * scale, False, definition.loadout, frame, tags=definition.tags)
        if vitals.total < before and not vitals.dead:
            radial_impulse(fight, target, centre, health, scale)
    scaled = base * fight.outgoing_scale(player.body)
    for piece in tuple(fight.walls.pieces.values()):
        surface = mei_wall.closest(piece, centre)
        distance = math.dist(centre, surface)
        if distance < radius and fight.walls.reaches(piece, origin, surface):
            amount = scaled * (1 - distance / radius) if landing else scaled
            fight.walls.damage(piece, player, amount, frame)


def radial_impulse(fight, target, origin, state, scale):
    from ow174.game.knockback import Force

    horizontal = expr.to_float(state.evaluate(
        state.node.field("m_modifyHealth.m_E33EFEE1"), watch=False,
    )) * scale
    vertical = expr.to_float(state.evaluate(
        state.node.field("m_modifyHealth.m_583C208B"), watch=False,
    )) * scale
    if horizontal <= 0 and vertical <= 0:
        return
    dx, dz = target.feet[0] - origin[0], target.feet[2] - origin[2]
    if math.hypot(dx, dz) <= 1e-4:
        horizontal, dz = 0., 1.
    force = Force(horizontal, max(horizontal, 1.), vertical, vertical, ((0., 1., 1., 1.),))
    fight.impulses.hit(target, (dx, 0., dz), 0., force, ballistic=True)


def primal_pool(component):
    for instance in component.instances.values():
        if instance.graph.index == 0x109:
            state = instance.states.get(4)
            if state is not None and state.active:
                return state
    return None


def sync_primal(fight, player, script):
    if script.hero != HERO:
        return
    from ow174.game.world import HEALTH_POOL

    state = primal_pool(script.component)
    previous = fight.winston_primal.get(player.body, 0.)
    if state is None and not previous:
        return
    target = next((target for target in fight._targets() if target.owner is player), None)
    if target is None:
        return
    vitals = fight.vitals_of(target)
    bonus = expr.to_float(state.evaluate(state.node.field("m_amount"), watch=False)) if state else 0.
    if vitals.dead:
        bonus = 0.
    if previous == bonus:
        return
    vitals.most[HEALTH_POOL] += bonus - previous
    if bonus > previous:
        vitals.heal(vitals.maximum)
    else:
        vitals.now[HEALTH_POOL] = min(vitals.now[HEALTH_POOL], vitals.most[HEALTH_POOL])
    vitals.changed.add(HEALTH_POOL)
    if bonus:
        fight.winston_primal[player.body] = bonus
    else:
        fight.winston_primal.pop(player.body, None)
        fight.winston_punches.pop(player.body, None)
    fight._sync_body_health(player, vitals)


def weapon_instance(script):
    return next((i for i in script.component.instances.values() if i.graph.index == 0xFE), None)


def interrupt_punch(fight, player):
    if player.body not in fight.winston_primal or player.body_script is None:
        return
    instance = weapon_instance(player.body_script)
    if instance is None:
        return
    for index in (20, 21, 35, 36):
        state = instance.states.get(index)
        if state is not None and state.active:
            state.end(False)
            instance.component.run_now()


def punch(fight, player, command, script):
    if script.hero != HERO or command is None or player.body not in fight.winston_primal:
        return
    instance = weapon_instance(script)
    if instance is None:
        return
    for index in (20, 21):
        state = instance.states.get(index)
        if state is not None and state.active:
            duration = expr.to_float(state.evaluate(state.node.field("m_ED97A97C"), watch=False))
            if state.now - state.start >= round(duration * 1000):
                state.end(True)
                instance.component.run_now()
    seen = fight.winston_punches.setdefault(player.body, {})
    for health_index, search_index in ((35, 18), (36, 17)):
        health = instance.states.get(health_index)
        search = instance.states.get(search_index)
        if health is None or search is None or not (health.active and search.active):
            continue
        if seen.get(health_index) == health.counter:
            continue
        seen[health_index] = health.counter
        punch_impact(fight, player, command, health, search)


def punch_impact(fight, player, command, health, search):
    from ow174.game import reinhardt_barrier, shot_guards
    from ow174.game.combat import aim, capsule_visible, hit_def
    from ow174.game.reinhardt_offense import segment_pair_distance, sweep_surfaces

    yaw, pitch = command.aim()
    direction = aim(yaw, pitch)
    right = aim(yaw + 16384, 0)
    feet = tuple(player.mover.position)

    def point(field):
        offset = search.evaluate(search.node.field(field), watch=False)
        return tuple(feet[k] + right[k] * offset.x + direction[k] * offset.z
                     + (offset.y if k == 1 else 0.) for k in range(3))

    a, b = point("m_05BE62DD"), point("m_B3A77CD0")
    radius = expr.to_float(search.evaluate(search.node.field("m_radius"), watch=False))
    definition = hit_def(health, {"m_modifyHealth": health.node.field("m_modifyHealth")})
    origin = fight._eye(player)
    for target in fight._enemies(player):
        lower, upper = target.segment()
        if (segment_pair_distance(a, b, lower, upper) > radius + target.radius
                or not capsule_visible(fight._line_collision(), origin, lower, upper, target.radius)
                or shot_guards.parries_melee(fight, target, feet, definition.tags)):
            continue
        if reinhardt_barrier.protect_body(
            fight, target, definition.amount * fight.outgoing_scale(player.body), attacker=player.body,
        ):
            continue
        vitals = fight.vitals_of(target)
        before = vitals.total
        fight._damage(player, target, definition.amount, False, definition.loadout, command.frame,
                      tags=definition.tags)
        if vitals.total < before and not vitals.dead:
            radial_impulse(fight, target, feet, health, 1.)
    amount = definition.amount * fight.outgoing_scale(player.body)
    sweep_surfaces(fight, player, origin, ((a, b, radius),), amount, set())
