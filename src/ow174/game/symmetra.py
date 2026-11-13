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


def view_world_tick(script, command, quanta):
    """Query world history through this body's actual owner/world epoch pair."""
    from ow174.game.combat import TICK

    world, owner = script.world_frame_anchor or script.symmetra_birth
    elapsed_ms = (command.frame - owner) * script.frame_ms - command.view_delay(quanta) * 1000
    return world + elapsed_ms / round(TICK * 1000)


def release_tick(player, authored_ms, world_tick):
    """Map this body's authored volley frame through its existing world epoch."""
    script = player.body_script
    if player.hero.guid != HERO or script is None or not hasattr(script, "symmetra_birth"):
        return None
    anchor_tick, anchor_frame = script.symmetra_birth
    elapsed_ms = (authored_ms // script.frame_ms - anchor_frame) * script.frame_ms
    return min(world_tick, anchor_tick + elapsed_ms // 16)


def catch_up_orb(fight, rocket):
    """Replay an admitted late orb through the existing flight and hit register."""
    if (rocket.definition != ORB_PROJECTILE
            or getattr(getattr(rocket.shooter, "hero", None), "guid", None) != HERO
            or rocket.last_tick is None or rocket.last_tick >= fight.tick):
        return
    from ow174.game.projectile_clock import advance_accepted

    advance_accepted(fight, rocket)



def target_query(world, cfg, instance):
    """Supply 157C's charge and shield-ammo target queries.

    This reconstructs the exact state-25 target/filter profile, not arbitrary
    native tag/filter evaluation. Unknown entities and other profiles retain
    the ordinary world's unsupported answer. The graph owns charge and decay.
    """
    if world.hero != HERO or world.match is None or instance.graph.index != WEAPON:
        return NotImplemented
    if cfg.get("$") not in ("STU_25ECB55F", "STU_E72F6348", "STU_AC829876"):
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
        view_tick = (view_world_tick(script, command, player.quanta)
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
                    wall = _cover_ray(combat, player, origin, direction, reach)
                    barrier = _trace_barrier(combat, player, origin, direction, min(reach, wall if wall is not None else reach))
                    if barrier is not None:
                        if best is None or barrier[0] <= best[0]:
                            definition, entity = _barrier_identity(combat, barrier)
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
                attached = _attached_barrier(combat, selected.owner)
                if attached is not None:
                    definition, _ = _barrier_identity(combat, (0.0, selected.owner, attached))
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
                _damage_barrier(combat, player, beam_barrier, amount * player.quanta / 1e6)
            continue
        combat._damage(player, selected, amount * player.quanta / 1e6, False,
                       loadout.guid if isinstance(loadout, expr.Asset) else None, command.frame, beam=True)


def _cover_ray(fight, player, origin, direction, reach):
    from ow174.game.combat import ray_world

    hits = [ray_world(fight._collision(), origin, direction, reach)]
    ice = fight.walls.cast(origin, direction, reach)
    if ice is not None:
        hits.append(ice[0])
    hits.append(fight._cryo_contact(player, origin, direction, reach))
    return min((hit for hit in hits if hit is not None), default=None)


def _trace_barrier(fight, player, origin, direction, reach):
    from ow174.game import barriers

    contacts = [fight._barrier_contact(player, origin, direction, reach)]
    native = barriers.ray_hit(fight, player, origin, direction, reach)
    if native is not None and native[1].component.world.hero != barriers.MEI:
        contacts.append((native[0], native[1].parent, native[1]))
    return min((hit for hit in contacts if hit is not None), key=lambda hit: hit[0], default=None)


def _attached_barrier(fight, owner):
    from ow174.game import barriers

    for actor in fight.barriers.scripted.values():
        recipient = actor.recipient if actor.recipient is not None else actor.parent
        if recipient is owner and barriers._live(fight, actor) and actor.component.world.hero == barriers.ZARYA:
            return actor
    return fight.barriers.for_target(owner)


def _pet_definition(instance, state):
    cfg = state.node.field("m_entityDefinition.m_entityDef")
    value = state.evaluate(cfg, watch=False) if cfg is not None else None
    return value.guid if isinstance(value, expr.Asset) else expr.guid_of(cfg)


def _barrier_identity(fight, contact):
    from ow174.game import barriers, orisa_barrier, photon_barrier, reinhardt_barrier, sigma_barrier, world_bubbles

    _, owner, actor = contact
    if isinstance(actor, barriers.Barrier):
        instance = actor.source.instances.get(actor.instance)
        state = instance.states.get(actor.state) if instance is not None else None
        return (_pet_definition(instance, state) if state is not None else None), actor.entity
    if isinstance(actor, reinhardt_barrier.RaisedBarrier):
        return _pet_definition(actor.instance, actor.instance.state(17)), actor.body
    if isinstance(actor, world_bubbles.FixedBubble):
        return 0x04000000000001C3, actor.body
    if isinstance(actor, barriers.Bubble):
        return actor.definition, actor.entity
    if isinstance(actor, orisa_barrier.Barrier):
        return orisa_barrier.DEFINITION, actor.entity
    if isinstance(actor, sigma_barrier.Barrier):
        return sigma_barrier.BARRIER_DEFINITION, actor.entity
    if isinstance(actor, photon_barrier.Shield):
        return photon_barrier.BODY, actor.body
    if type(actor) is int:
        player = next((p for p in fight.match.players if p.has_body and p.body == actor), None)
        script = getattr(player, "body_script", None)
        instance = next((i for i in script.component.instances.values() if i.graph.index == 0x95), None) if script else None
        return (_pet_definition(instance, instance.state(17)) if instance else None), actor
    return None, getattr(actor, "entity", 0)


def _damage_barrier(fight, player, contact, amount):
    from ow174.game import barriers, nano

    actor = contact[2]
    amount *= fight.orisa.superchargers.scale(player)
    if isinstance(actor, barriers.Barrier):
        return barriers.damage(fight, actor, amount * nano.offense(fight, player.body), attacker=player.body)
    return fight._damage_contact_barrier(contact, amount, shooter=player)


def owns_shield_recovery(owner):
    hero = getattr(owner, "hero", None)
    return getattr(hero, "guid", hero) == HERO


def pool_query(world, cfg, instance):
    """Original0033 own-body current/maximum kind query for shield recovery."""
    if (world.hero not in (HERO, 0x02E0000000000068, 0x02E000000000023B) or world.match is None or instance is None
            or instance.component.strict or instance.graph.index != 0x0033
            or cfg.get("$") not in ("STU_783B4CF0", "STU_68883EAC")):
        return NotImplemented
    target = instance.evaluate(cfg.get("m_entity"))
    pool = cfg.get("m_7E8B666C") or {}
    kind = pool.get("m_7E8B666C")
    vitals = world.match.combat.vitals.get(world.body)
    if (target == expr.Entity(world.body) and pool.get("$") == "STU_297E1A30"
            and type(kind) is int and kind in (0, 1, 2) and vitals is not None):
        values = vitals.now if cfg["$"] == "STU_783B4CF0" else vitals.most
        return values[kind]
    return NotImplemented


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
    services. The authored E3779CD0/C0EDD7EC providers read current scalar yaw
    and pitch; neither is velocity. This narrow host preserves the current
    orientation and velocity. Velocity preservation and discontinuity/history
    handling are server policies, not a decoded native velocity parameter.
    """
    if world.match is None or instance.graph.index != 0x1647 or node.pos != 1:
        return NotImplemented
    if world.match.ended:
        return False
    target = node.field("m_target")
    yaw, pitch = node.field("m_E3779CD0"), node.field("m_C0EDD7EC")
    if (not isinstance(target, dict) or target.get("$") != "STU_B5A0CAF0"
            or not isinstance(yaw, dict) or yaw.get("$") != "STU_C8C4C20D"
            or not isinstance(pitch, dict) or pitch.get("$") != "STU_40AC5AE4"
            or yaw.get("m_entity") != target or pitch.get("m_entity") != target):
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


def transit_position_query(world, cfg, instance):
    if (world.match is None or instance is None or instance.component.strict
            or instance.graph.index != 0x15F5 or cfg.get("$") != "STU_71A87E92"):
        return NotImplemented
    target = instance.evaluate(cfg.get("m_entity"))
    if isinstance(target, expr.Entity):
        point = world.match.script_body_position(target.id, world.body)
        if point is not None:
            return expr.Vec3(*point)
    return None


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
