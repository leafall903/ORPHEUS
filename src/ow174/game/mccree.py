"""Scoped McCree Roll and Deadeye bridges; full native behavior remains partial."""

import math

from ow174.game.script import expr, graph, runtime

HERO = 0x02E0000000000042
ROLL, ROLL_MOVEMENT = 0x01F0, 16
RIALTO = 0x080000000000079F
TALON_AI_SIZE_CAP_BODIES = {
    0x0400000000001D40,  # Trooper
    0x0400000000002279,  # Enforcer
    0x0400000000001E7D,  # Sniper
    0x0400000000001EBD,  # Assassin
    0x0400000000001EBB,  # Heavy Assault
}
ULTIMATE, TARGET_EFFECT = 0x022F, 0x01EE
REAPPLY = 0x024000000000000F


def award_sandbox_charge(match, shooter, bot, damage) -> bool:
    """Rialto sandbox policy: one point for each point of actual NPC damage."""
    if (match.game_map.map_guid == RIALTO
            and bot.spawner.get("manual_spawn") is True
            and shooter.hero.guid == HERO and shooter.body_script is not None):
        shooter.body_script.add_ult_charge(damage)
        return True
    return False


def _owner(world, state):
    match = world.match
    if match is None or match.ended or world.hero != HERO or state.instance.component.entity != world.body:
        return None
    return next((p for p in match.players if p.body == world.body and p.spawned and not p.gone
                 and p.client is not None and not match.combat.is_dead(p)), None)


def _living(match):
    return {t.entity: t for t in match.combat._targets() if not match.combat.vitals_of(t).dead}


def target_point(target):
    """Reconstruct hardpoint1 inside the actual capsule, including short NPCs."""
    _, upper = target.segment()
    return target.feet[0], min(target.feet[1] + target.eye, upper[1]), target.feet[2]


def targets(world, state):
    if state.instance.graph.index != ULTIMATE or state.node.pos not in (26, 86):
        return NotImplemented
    owner = _owner(world, state)
    if owner is None:
        return ()
    if graph.graph(TARGET_EFFECT) is None:
        expr.warn_once('deadeye_effect', 'Deadeye requires external target-effect graph 01EE')
        return ()
    from ow174.game import combat

    fight = world.match.combat
    origin = fight._eye(owner)
    forward = combat.aim(owner.mover.yaw, owner.mover.pitch)
    reach = expr.to_float(state.evaluate(state.node.field('m_radius'), watch=False))
    # Reconstructed geometry: authored105 degrees is interpreted as the full cone.
    angle = expr.to_float(state.evaluate(state.node.field('m_A30B383E'), watch=False))
    threshold = math.cos(math.radians(angle * 0.5))
    found = []
    for target in fight._enemies(owner):
        point = target_point(target)
        offset = tuple(b - a for a, b in zip(origin, point, strict=True))
        distance = math.sqrt(sum(value * value for value in offset))
        if distance <= 0 or distance > reach:
            continue
        if sum(a * b for a, b in zip(forward, offset, strict=True)) / distance < threshold:
            continue
        a, b = target.segment()
        if state.node.pos == 26 or combat.capsule_visible(
                world.match.collision_world, origin, a, b, target.radius):
            found.append((distance, target.entity))
    return tuple(entity for _, entity in sorted(found))


def effects(world, state):
    if state.instance.graph.index != ULTIMATE or state.node.pos != 11 or world.hero != HERO:
        return NotImplemented
    owner = _owner(world, state)
    definition = graph.graph(TARGET_EFFECT)
    if owner is None or definition is None:
        state.clear_children()
        return True
    component = state.instance.component
    live = _living(world.match)
    slot = component.entity_var(2551, create=False)
    selected = slot.value() if slot is not None else ()
    selected = selected if isinstance(selected, tuple) else ()
    desired = {expr.to_entity(value) for value in selected}
    for entity in tuple(state.children):
        if entity not in live or entity not in desired:
            state.remove_child(entity)
    for value in selected:
        entity = expr.to_entity(value)
        if entity not in live:
            continue
        child = state.children.get(entity)
        if child is None or component.instances.get(child.id) is not child:
            child = component.create(definition, values={(expr.INSTANCE, 536): expr.Entity(entity)})
            child.set_source_entity(owner.body)
            state.children[entity] = child
        runtime.send_instance_message(child, REAPPLY, {92: expr.Entity(entity)}, owner.body)
    return True


def _roll_owner(world, instance):
    if (world.hero != HERO or world.match is None or world.match.ended
            or instance.graph.index != ROLL or instance.component.entity != world.body):
        return None
    return next((p for p in world.match.players if p.body == world.body and p.spawned and not p.gone
                 and p.client is not None and not world.match.combat.is_dead(p)), None)


def velocity_action(world, instance, node):
    """Scoped01F0 bridge; next-frame delta/cap timing follows D2B995/C32CCA."""
    if world.hero != HERO or instance.graph.index != ROLL or node.pos not in (8, 11, 17):
        return NotImplemented
    owner = _roll_owner(world, instance)
    if owner is None or world.match.combat.is_stunned(owner):
        return True
    rows = node.field('m_BC5E91CF')
    expected = 'STU_748FC65A' if node.pos == 17 else 'STU_95A10547'
    field = 'm_A9561CA0' if node.pos == 17 else 'm_CFEC5F76'
    if (not isinstance(rows, list) or len(rows) != 1 or not isinstance(rows[0], dict)
            or rows[0].get('$') != expected or rows[0].get(field) != 1
            or rows[0].get('m_priority') is not None):
        return NotImplemented
    value = instance.evaluate(rows[0].get('m_9B7A63EA'))
    if node.pos == 17:
        if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
            return NotImplemented
        change = 'cap', float(value)
    else:
        if not isinstance(value, expr.Vec3) or not all(math.isfinite(v) for v in value):
            return NotImplemented
        # C32D22 stores desired minus sampled velocity, for exactly one frame.
        delta = tuple(expr.f32(v - before) for v, before in zip(value, owner.mover.velocity, strict=True))
        change = 'delta', delta
    world.velocity_requests.append((instance.component.frame + 1, *change))
    return True


def apply_velocity(owner, frame, *, controlled=False):
    """Consume each request once, before the existing collision-aware posed move."""
    script = owner.body_script
    if script is None:
        return
    world = script.component.world
    if controlled or world.body != owner.body or owner.match.combat.is_dead(owner):
        world.velocity_requests.clear()
        return
    pending = []
    for at, operation, value in world.velocity_requests:
        if at > frame:
            pending.append((at, operation, value))
            continue
        vx, vy, vz = owner.mover.velocity
        if operation == 'impulse':
            owner.mover.impulse(value)
            owner.mover.external_impulse = True
        elif operation == 'delta':
            owner.mover.velocity = tuple(expr.f32(v + d) for v, d in zip((vx, vy, vz), value, strict=True))
        else:
            # Reconstructed horizontal-speed clamp; full native phase ordering is open.
            speed = math.hypot(vx, vz)
            if speed > value:
                owner.mover.velocity = (vx * value / speed, vy, vz * value / speed)
    world.velocity_requests[:] = pending


def _roll_active(owner):
    script = owner.body_script
    if owner.hero.guid != HERO or script is None:
        return False
    for instance in script.component.instances.values():
        if instance.graph.index == ROLL:
            node = instance.graph.node(ROLL_MOVEMENT)
            state = instance.states.get(node.state) if node is not None else None
            if state is not None and state.active:
                return True
    return False


def _npc_peer_contacts(owner, reach):
    from ow174.game import contacts
    from ow174.game.collision import MOVER, TEAM0, TEAM1
    from ow174.game.mover import mover_data

    mover = owner.mover
    x, _, z = mover.position
    for bot in owner.match.bots:
        if (bot.dead or bot.spawner.get('manual_spawn') is not True
                or (not owner.match.game_map.free_for_all and bot.team == owner.team)):
            continue
        category = TEAM0 if bot.team == 0 else TEAM1
        if not category & mover.peer_mask:
            continue
        px, py, pz = bot.position
        if bot.navigation is None:
            data = mover_data(bot.body)
            crouched = False
            lift = data.stand_pogo
        else:
            npc_mover = bot.navigation.mover
            data = npc_mover.data
            crouched = npc_mover.crouched
            lift = npc_mover._lift()
        radius, segment = data.capsule(crouched, limit_ai_size=bot.body in TALON_AI_SIZE_CAP_BODIES)
        if math.hypot(px - x, pz - z) > reach + radius:
            continue
        lower = py + lift + radius
        capsule = contacts.Capsule((px, lower, pz), (px, lower + segment, pz), radius, MOVER)
        peer = contacts.make_contact(contacts.PeerShape(capsule, category), mover.category)
        yield peer, any(bot.velocity)


def step_rialto_pose(owner, command, seconds, tick, *, controlled=False, speed_scale=1.0):
    """Include manual enemy NPC contacts in McCree's existing movement sweep.

    The native owner collides with these bodies in Practice Range too. Restricting
    the server contacts to Rialto let it pass through an NPC while the client
    stopped, leaving subsequent visible attacks behind the authoritative target.
    """
    mover = owner.mover
    if controlled or owner.hero.guid != HERO:
        mover.step_pose(command, seconds, tick, speed_scale=speed_scale)
        return
    vx, _, vz = mover.velocity
    data = mover.values
    speed = max(math.hypot(vx, vz), data.run_forward, data.run_backward, data.run_strafe)
    reach = max(data.stand_radius, data.crouch_radius) + speed * seconds + 0.04
    candidates = list(_npc_peer_contacts(owner, reach))
    if not candidates:
        mover.step_pose(command, seconds, tick, speed_scale=speed_scale)
        return
    peers = [peer for peer, _ in candidates]
    body = mover._engine_body()
    body.contacts[:0] = peers
    try:
        mover.step_pose(command, seconds, tick, speed_scale=speed_scale)
        nx, _, nz = mover.velocity
        roll_contact = math.hypot(nx - vx, nz - vz) > 0.05 and _roll_active(owner)
        # Remote interpolation can place a moving NPC differently during local prediction.
        if any((moving or roll_contact) and peer.manifolds(body) for peer, moving in candidates):
            from ow174.game import correction

            correction.force_impulse(owner, first_frame=command.frame)
    finally:
        for peer in peers:
            if peer in body.contacts:
                body.contacts.remove(peer)


def native(world, cfg, instance):
    if instance is not None and cfg.get('$') in {'STU_021D01CD', 'STU_66F3B89E', 'STU_309B0570'}:
        owner = _roll_owner(world, instance)
        if owner is not None and expr.to_entity(instance.evaluate(cfg.get('m_entity'))) == world.body:
            angle = owner.mover.yaw * math.tau / 65536
            if cfg['$'] == 'STU_309B0570':
                return expr.Vec3(*owner.mover.velocity)
            if cfg['$'] == 'STU_021D01CD':
                return expr.Vec3(expr.f32(math.sin(angle)), 0.0, expr.f32(math.cos(angle)))
            return expr.Vec3(expr.f32(math.cos(angle)), 0.0, expr.f32(-math.sin(angle)))
    if (world.hero != HERO or world.match is None or instance is None or instance.graph.index != TARGET_EFFECT
            or cfg.get('$') != 'STU_9B7648C3'):
        return NotImplemented
    entity = expr.to_entity(instance.evaluate(cfg.get('m_entity')))
    target = _living(world.match).get(entity)
    # Reconstruct the native component51 aggregate getter from authoritative pools.
    return expr.f32(world.match.combat.vitals_of(target).total) if target is not None else 0.0


def critical_default(state):
    """Declared McCree primary implicit2x policy; not a recovered global default."""
    world = state.instance.component.world
    if (getattr(world, 'hero', None) == HERO
            and getattr(world, 'body', None) == state.instance.component.entity
            and state.instance.graph.index == 0x01D2 and state.node.pos == 31):
        return 2.0
    return 1.0


def flashbang_gravity(shooter, state):
    """Resolve01E9/7's unconditional E1A5B579 motion; other paths stay separate."""
    if (shooter.hero.guid != HERO or shooter.body_script is None
            or state.instance.component is not shooter.body_script.component
            or state.instance.graph.index != 0x01E9 or state.node.pos != 7):
        return None
    from ow174.game import projectiles

    return projectiles.unconditional_gravity(state)


def gravity_velocity(velocity, gravity: float, seconds: float):
    """PC104319 D71ACD-D71ADE: unconditional Y velocity change, float32."""
    return (velocity[0], expr.f32(expr.f32(velocity[1]) - expr.f32(
        expr.f32(gravity) * expr.f32(seconds))), velocity[2])


def flashbang_direction(state, forward):
    """Apply the authored0.05 upward launch bias before speed/gravity."""
    from ow174.game import weapon_spread

    cfg = (state.node.field('m_5EC1EB38') or {}).get('m_22CE7F30')
    lift = expr.to_float(state.evaluate(cfg, watch=False)) if cfg is not None else 0.0
    return weapon_spread.launch_direction(forward, lift)


def flashbang_launch_tick(shooter, shot_frame, world_tick):
    """Map the authored shot frame using the body's immutable birth epoch.

    Packet endpoints and receipt times can jitter independently of the command
    clock. This explicit server reconstruction retains the actual birth pair,
    without changing the owner VM or another hero's clock.
    """
    from ow174.game import projectiles

    clock = flashbang_clock(shooter)
    if clock is None:
        return world_tick
    return projectiles.owner_to_world(clock, shot_frame)


def flashbang_clock(shooter):
    script = shooter.body_script
    birth = getattr(script, 'mccree_birth', None)
    return (*birth, script.frame_ms) if birth is not None else None


def advance_flashbang(fight, rocket):
    """Opt one admitted Cassidy flight into the physical impact-clock helper."""
    if (getattr(getattr(rocket.shooter, 'hero', None), 'guid', None) != HERO
            or rocket.source_graph != 0x01E9 or rocket.definition != 0x040000000000037D
            or rocket.last_tick is None):
        return False
    from ow174.game import projectile_clock

    projectile_clock.advance_accepted(fight, rocket)
    return True


def fan_direction(shooter, state, forward, number: int, time: int, activation):
    """Scoped Fan cone; native math with a declared server seed-identity mapping."""
    if (shooter.hero.guid != HERO or shooter.body_script is None
            or state.instance.component is not shooter.body_script.component
            or state.instance.graph.index != 0x01D2 or state.node.pos != 37):
        return forward
    launch = state.node.field('m_5EC1EB38')
    if (not isinstance(launch, dict) or launch.get('$') != 'STU_239B571E'
            or state.spread_ramp is None):
        return forward
    from ow174.game import weapon_spread

    low, high = (expr.to_float(state.evaluate(launch.get(name), watch=False))
                 for name in ('m_7F0FED02', 'm_429A6C85'))
    normalized = state.spread_at(time - state.start - state.offset)
    degrees = expr.f32(expr.f32(expr.f32(high - low) * normalized) + expr.f32(low))
    cone = expr.f32(degrees * expr.f32(math.pi / 180))
    # Native handle98, script/state word and six-bit counter identities are
    # not fully mapped. Use stored server identities, with no mutable RNG or
    # wall clock. This mapping is a reconstruction, not native seed parity.
    base = weapon_spread.seed_base(shooter.body, state.instance.id, state.node.pos, activation[0])
    # The inspected D74C0E and D79BB9 callers use uniform radius (modefalse).
    # Binding that mode to this scoped Fan remains a declared reconstruction.
    return weapon_spread.direction(forward, cone, base, number)


def shot_aim(fight, shooter, state):
    """Use01EE's target536 direction instead of the command's crosshair direction."""
    from ow174.game import combat

    if (shooter.hero.guid != HERO or shooter.body_script is None
            or state.instance.component is not shooter.body_script.component):
        return None
    slot = state.instance.vars.get(536)
    entity = expr.to_entity(slot.value()) if slot is not None else 0
    target = _living(fight.match).get(entity)
    selected = state.instance.component.entity_var(5874, create=False)
    if target is None or selected is None or expr.Entity(entity) not in (selected.value() or ()):
        return None
    origin = fight._eye(shooter)
    point = target_point(target)
    offset = tuple(b - a for a, b in zip(origin, point, strict=True))
    distance = math.sqrt(sum(value * value for value in offset))
    if distance <= 0:
        return None
    a, b = target.segment()
    if not combat.capsule_visible(fight.match.collision_world, origin, a, b, target.radius):
        return None
    return origin, tuple(value / distance for value in offset)
