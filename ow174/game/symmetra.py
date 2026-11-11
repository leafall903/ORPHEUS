"""104319 Photon Projector world bridge; full hero acceptance remains open.

157C's primary is TrackRay/ModifyHealth, not WeaponVolley. Populate its two
ray results from the combat world and apply the authored damage per second.
Capsule traces, the eye origin and per-command damage integration are server
reconstructions. The charge target queries below are a limited world adapter;
barriers and other defensive services remain separate acceptance items.
"""

import math

from ow174.game.script import expr, graph

HERO = 0x02E0000000000016
WEAPON = 0x157C
ORB_PROJECTILE = 0x04000000000026F1
ORB_LIFETIME = 25.0  # 104319 projectile lifetime in seconds


def placement_direction_query(world, cfg, instance):
    """Expose native direction queries for the owned upright placement body.

    CE8460 and CCDD80 select rows0 and2 of D1B5E0's transform, respectively.
    CC94D0/D18780/C5E040 adds the stored pitch for the view-forward query.
    Arbitrary transforms, parent attachments and the native component contracts
    remain open; this maps only the known upright server mover pose.
    """
    queries = {"STU_66F3B89E": "lateral", "STU_021D01CD": "forward", "STU_2200A66D": "view"}
    kind = queries.get(cfg.get("$"))
    if (kind is None or world.hero != HERO or world.match is None
            or instance.graph.index not in (0x156B, 0x1571)):
        return NotImplemented
    if instance.evaluate(cfg.get("m_entity")) != expr.Entity(world.body):
        return NotImplemented
    owner = next((p for p in world.match.players if p.body == world.body), None)
    if (owner is None or owner.gone or not owner.has_body or not owner.spawned
            or world.match.combat.is_dead(owner)):
        return NotImplemented
    yaw = owner.mover.yaw
    if type(yaw) not in (int, float) or not math.isfinite(yaw) or not -32768 <= yaw <= 32768:
        return NotImplemented
    angle = yaw * math.tau / 65536
    sine, cosine = math.sin(angle), math.cos(angle)
    if kind == "view":
        pitch = owner.mover.pitch
        if type(pitch) not in (int, float) or not math.isfinite(pitch) or not -32768 <= pitch <= 32768:
            return NotImplemented
        # Original C5E040 converts the signed stored pitch with this f32 scale.
        radians = expr.f32(expr.f32(pitch) * 9.58738019107841e-05)
        horizontal = math.cos(radians)
        return expr.Vec3(expr.f32(sine * horizontal), expr.f32(-math.sin(radians)),
                         expr.f32(cosine * horizontal))
    axes = (expr.Vec3(expr.f32(cosine), 0.0, expr.f32(-sine)),
            expr.Vec3(expr.f32(sine), 0.0, expr.f32(cosine)))
    return axes[0 if kind == "lateral" else 1]


def play_transit_script(world, instance, node):
    """Continue only 15F5's authored own-body 1647 invocation.

    Portal eligibility and the intervening 54FEB21C host signal remain external.
    This does not supply the generic native PlayScript target/policy service.
    """
    if world.match is None or instance.graph.index != 0x15F5 or node.pos != 10:
        return NotImplemented
    target = instance.evaluate(node.field("m_targets"))
    definition = instance.evaluate(node.field("m_script"))
    if target != expr.Entity(world.body) or definition != expr.Asset(0x0580000000001647):
        return NotImplemented
    params = tuple((int(row["m_B5051BCE"], 16), instance.evaluate(row["m_value"]))
                   for row in node.field("m_params"))
    destination = next(value for key, value in params if key == 0x0A40000000000078)
    if not isinstance(destination, expr.Vec3):
        return False
    source = expr.to_entity(instance.find_var(graph.INSTANCE, 40).value())
    return instance.component.play_loaded_graph(
        graph.graph(0x1647), source=source, params=params, policy=1, instigator=source) is not None


def teleport_body(world, instance, node):
    """Execute 1647's own-body relocation with explicit graph entry inputs.

    The portal's placement, pair membership and transit admission are separate
    services. Preserve current orientation/velocity as this authored action asks.
    Discontinuity/history handling is a reconstructed server policy.
    """
    if world.match is None or instance.graph.index != 0x1647 or node.pos != 1:
        return NotImplemented
    if world.match.ended:
        return False
    target = node.field("m_target")
    rotation, velocity = node.field("m_E3779CD0"), node.field("m_C0EDD7EC")
    if (not isinstance(target, dict) or target.get("$") != "STU_B5A0CAF0"
            or not isinstance(rotation, dict) or rotation.get("$") != "STU_C8C4C20D"
            or not isinstance(velocity, dict) or velocity.get("$") != "STU_40AC5AE4"
            or rotation.get("m_entity") != target or velocity.get("m_entity") != target):
        return NotImplemented
    destination = instance.evaluate(node.field("m_AF774F7D"))
    if (not isinstance(destination, expr.Vec3)
            or not all(math.isfinite(expr.f32(v)) for v in (destination.x, destination.y, destination.z))):
        return False
    player = next((p for p in world.match.players if p.body == world.body), None)
    if (player is None or player.gone or not player.spawned or not player.has_body
            or player.client is None or world.match.combat.is_dead(player)):
        return False
    from ow174.game.mover import quantise

    mover = player.mover
    try:
        position = [quantise(v) for v in (destination.x, destination.y, destination.z)]
    except (OverflowError, ValueError):
        return False
    mover.position = position
    if mover.body is not None:
        x, y, z = mover.position
        mover.body.teleport((x, expr.f32(y + mover._lift()), z))
    frame = instance.component.now // instance.component.frame_ms
    snapshot = mover.snapshot(frame)
    if mover.history and mover.history[-1].frame == frame:
        mover.history[-1] = snapshot
    else:
        mover.history.append(snapshot)
    # Never interpolate an ordinary hit trace across the teleport distance.
    fight = world.match.combat
    fight.history.pop(world.body, None)
    fight.record_replacement(world.body)
    return True


def target_query(world, cfg, instance):
    """Supply 157C's charge and shield-ammo target queries.

    This reconstructs the exact state-25 target/filter profile, not arbitrary
    native tag/filter evaluation. Unknown entities and other profiles retain
    the ordinary world's unsupported answer. The graph owns charge and decay.
    """
    if world.hero != HERO or world.match is None or instance.graph.index != WEAPON:
        return NotImplemented
    if cfg.get("$") == "STU_25ECB55F":
        if cfg != instance.graph.node(185).field("m_condition"):
            return NotImplemented
        target = instance.evaluate(cfg.get("m_entity"))
        contact = world.beam_barrier
        return contact is not None and target == expr.Entity(contact)
    queries = instance.graph.state(25).field("m_condition")["m_configVars"]
    if cfg not in queries:
        return NotImplemented
    target = instance.evaluate(cfg.get("m_entity"))
    if not isinstance(target, expr.Entity):
        return False
    fight = world.match.combat
    if world.beam_barrier is not None and target.id == world.beam_barrier:
        return True
    if cfg.get("$") == "STU_E72F6348":
        return any(body.entity == target.id for body in fight._targets())
    if cfg.get("$") == "STU_AC829876":
        owner = next((p for p in world.match.players if p.body == world.body), None)
        return owner is not None and any(body.entity == target.id for body in fight._enemies(owner))
    return NotImplemented


def primary(combat, player, command):
    # Import locally: combat owns geometry and calls this bridge after body input.
    from ow174.game import projectile_tags
    from ow174.game.combat import _number, aim, ray_capsule

    if player.hero.guid != HERO:
        return
    script = player.body_script
    for instance in tuple(script.component.instances.values()):
        if instance.graph.index != WEAPON:
            continue
        search = instance.states.get(76)
        active = command is not None and search is not None and search.active
        origin = combat._eye(player)
        direction = aim(*command.aim()) if active else (0.0, 0.0, 1.0)
        reach = _number(instance.evaluate({"$": "STUConfigVarDynamic",
                                          "m_identifier": 1648, "m_60DB8F99": 0}), 0.0)
        view_tick = (command.frame - command.view_delay(player.quanta) * 1e6 / player.quanta
                     if active else combat.tick)
        selected = None
        beam_barrier = None
        barrier_id = None
        with script.component.operation():
            for index in (100, 101):
                state = instance.states.get(index)
                target = None
                barrier_target = None
                hit = False
                if active and state is not None and state.active and reach > 0:
                    radius_cfg = state.node.field("m_57DAE1B5")
                    radius = _number(state.evaluate(radius_cfg, watch=False), 0.0)
                    best = None
                    for candidate in combat._enemies(player):
                        shown = combat.seen(candidate, view_tick)
                        if shown is None:
                            continue
                        a, b = shown.segment()
                        distance = ray_capsule(origin, direction, a, b, shown.radius + radius)
                        if distance is not None and distance <= reach and (
                            best is None or distance < best[0]
                        ):
                            best = distance, candidate
                    wall = combat.cover_ray(origin, direction, reach)
                    barrier = combat.trace_barrier(
                        player, origin, direction, min(reach, wall if wall is not None else reach))
                    if barrier is not None:
                        if best is None or barrier[0] <= best[0]:
                            _, shield, service = barrier
                            definition, entity = service.beam_identity(shield)
                            if projectile_tags.has_tag(definition, 0x0D6000000000003A):
                                barrier_target = entity
                            if selected is None and beam_barrier is None:
                                beam_barrier = barrier
                                barrier_id = barrier_target
                        wall = min(wall, barrier[0]) if wall is not None else barrier[0]
                    if best is not None and (wall is None or best[0] < wall):
                        target, hit = best[1], True
                    elif wall is not None:
                        hit = True
                # TrackRay 100 is preferred; 101 supplies the authored 0.2 m fallback.
                if selected is None and beam_barrier is None and target is not None:
                    selected = target
                output = instance.graph.state(index).field("m_target")
                for name, value in (
                    ("m_692DFF72", expr.Entity(target.entity if target else
                                               barrier_target or expr.INVALID_ENTITY)),
                    ("m_750377AA", hit),
                ):
                    cfg = output.get(name)
                    if cfg is not None and cfg.get("$") == "STUConfigVarDynamic":
                        instance.write((cfg.get("m_60DB8F99", graph.INSTANCE), cfg["m_identifier"]), value)
            if selected is not None:
                bubble = combat.zarya_barriers.contact(selected.owner)
                if bubble is not None:
                    _, pet, _ = bubble
                    definition = expr.guid_of(pet.node.field("m_entityDefinition.m_entityDef"))
                    if projectile_tags.has_tag(definition, 0x0D6000000000003A):
                        barrier_id = selected.entity
            if script.world.beam_barrier != barrier_id:
                script.world.beam_barrier = barrier_id
                for index in (25, 94):
                    switch = instance.states.get(index)
                    if switch is not None and switch.active:
                        switch.dependency_changed()
        if not active:
            continue
        damage = instance.state(86)
        modify = damage.node.field("m_modifyHealth")
        amount = _number(damage.evaluate(modify.get("m_amount"), watch=False), 0.0)
        loadout = damage.evaluate(modify.get("m_loadout"), watch=False)
        if selected is None:
            if beam_barrier is not None:
                _, shield, service = beam_barrier
                service.damage(shield, amount * player.quanta / 1e6, beam=True)
            continue
        combat._damage(player, selected, amount * player.quanta / 1e6, False,
                       loadout.guid if isinstance(loadout, expr.Asset) else None, command.frame, beam=True)
