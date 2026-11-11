"""Static placement and target graph delivery ported from the 104319 Sigma lane."""

import math

from ow174.game import bots, immunity
from ow174.game.script import expr, graph

HERO = 0x02E000000000023B
FLUX = 0x1E41
FLUX_EFFECT = 0x1DBC


def play_flux_request(world, instance, node):
    """Deliver the authored request on each selected body's existing VM clock."""
    if (instance.component.strict or instance.graph.index != FLUX or node.pos != 38
            or instance.component.entity != world.body or world.match.ended):
        return False
    from ow174.game.script import play

    resource = play.resolve_resource(instance, node)
    if resource is None or resource.guid != ((graph.GRAPH_TYPE << 48) | FLUX_EFFECT):
        return False
    definition = graph.graph(FLUX_EFFECT)
    if definition is None:
        return False
    request = play.resolve_request(
        instance, node, resource,
        context_provider=lambda: play.InvocationContext(world.body, world.body, instance.source_entity()),
    )
    if request is None:
        return True
    fight = world.match.combat
    targets = {target.entity: target for target in fight._targets()}
    for entity in request.targets:
        target = targets.get(entity)
        if (target is None or not _hosted_target(target)
                or fight.vitals_of(target).dead
                or immunity.control_immune(target.owner, target.entity)):
            continue
        script = target.owner.body_script
        if script is None:
            continue
        receiver = script.component
        if (receiver.entity != entity or receiver.fault is not None or receiver.owner_dead
                or receiver.strict or getattr(receiver.world, "match", None) is not world.match):
            continue
        receiver.play_loaded_graph(
            definition, source=request.context.caller, instigator=request.context.instigator,
            params=request.params, overrides=resource.overrides, policy=request.policy,
        )
    return True


def _hosted_target(target):
    owner = target.owner
    return (target.construct is None and (not isinstance(owner, bots.Bot)
            or owner.spawner.get("manual_spawn") is True))


def flux_targets(world, state):
    """Scoped reconstructed 1E41/node37 query over current Match body capsules.

    Authored origin/depth/aperture feed the native-backed Volume kernel. Upright
    axes, living hostile Match membership, nearest-capsule static LOS and stable
    distance/ID ordering are server policies. Native index/mask/tag population,
    target ages, barrier LOS and special immunity admission remain unproven;
    this bridge does not supply native filter contexts or alias controllers.
    """
    instance = state.instance
    if (instance.component.strict or world.hero != HERO or world.match is None
            or instance.component.entity != world.body or instance.graph.index != FLUX
            or state.node.pos != 37):
        return NotImplemented
    match = world.match
    player = next((p for p in match.players if p.has_body and p.body == world.body), None)
    if (match.ended or instance.component.owner_dead or player is None or not player.spawned
            or player.client is None or match.combat.is_dead(player)):
        return ()
    from ow174.game.combat import capsule_visible
    from ow174.game.script.target_volume import Volume

    origin = state.evaluate(state.node.field("m_05BE62DD"), watch=False)
    sight = state.evaluate(state.node.field("m_D59188D5"), watch=False)
    depth = expr.to_float(state.evaluate(state.node.field("m_radius"), watch=False))
    aperture = expr.to_float(state.evaluate(state.node.field("m_A30B383E"), watch=False))
    if (not isinstance(origin, expr.Vec3) or not isinstance(sight, expr.Vec3)
            or not all(math.isfinite(v) for v in (*origin, *sight, depth, aperture))
            or depth <= 0 or not 0 <= aperture < 180):
        return ()
    # C9A430's parallel volume: extent = depth * tan(half aperture).
    half = expr.f32(math.radians(aperture) * 0.5)
    cosine = expr.f32(math.cos(half))
    extent = expr.f32(depth * expr.f32(math.tan(half)))
    volume = Volume(tuple(origin), (0, 1, 0), (cosine, cosine), (extent, extent),
                    (0, 0), (1, 1), depth, parallel=True)
    found = []
    for target in match.combat._enemies(player):
        if not _hosted_target(target):
            continue
        if immunity.control_immune(target.owner, target.entity):
            continue
        accepted, distance = volume.capsule(target.feet, target.radius, target.height)
        if accepted and capsule_visible(match.combat._collision(), tuple(sight),
                                        *target.segment(), target.radius):
            found.append((distance, target.entity))
    return tuple(expr.Entity(entity) for _distance, entity in sorted(found))


def place_flux(world, state, *, confirm):
    """Reconstruct the static-ground placement boundary of1E41/node23.

    Native4C96470A refreshCB6BA0 writes the position/valid outputs before
    following success188 or failure190 on confirmation. The actual authored
    range is evaluated here. Static view-ray placement, upward-surface policy
    and ordering against the mover are reconstructed; native shape/filter,
    dynamic surfaces and client prediction are not established by this host.
    """
    instance = state.instance
    if (instance.component.strict or world.hero != HERO or world.match is None
            or instance.graph.index != FLUX or state.node.pos != 23):
        return NotImplemented
    return place_on_static_ground(world, state, confirm=confirm)


def place_on_static_ground(world, state, *, confirm):
    instance = state.instance
    combat = world.match.combat
    if world.match.collision_world is None:
        return NotImplemented
    player = next((p for p in world.match.players if p.has_body and p.body == world.body), None)
    if (not state.active or player is None or instance.component.owner_dead or world.match.ended):
        return True
    reach = expr.to_float(state.evaluate(state.node.field("m_6386E7B4"), watch=False))
    from ow174.game.combat import aim

    direction = aim(player.mover.yaw, player.mover.pitch)
    hit = combat._collision().cast_ray(combat._eye(player), direction, reach)
    valid = hit is not None and hit.normal[1] > 1e-6
    if valid:
        # Static world triangles have no dynamic entity/bone transform. Explicit
        # Entity0 is known absence, rather than an unimplemented query's null.
        point = expr.Vec3(*(expr.f32(value) for value in hit.point))
        instance.write(expr.lvalue(state.node.field("m_30710B76")), point)
        instance.write(expr.lvalue(state.node.field("m_1549F0F9")), expr.Entity(0))
        instance.write(expr.lvalue(state.node.field("m_2FECD1E3")), expr.Entity(0))
    instance.write(expr.lvalue(state.node.field("m_2339572B")), valid)
    if confirm:
        instance.follow(state.node, "m_F6955E81" if valid else "m_425337C6", state)
    return True


def flux_position_query(world, cfg, instance):
    """Static absent-entity arm of C5148EFE/CEB190: preserve the supplied point.

    D16C80's absent-object branchD173E4 writes an identity transform. Dynamic
    entity/bone transforms and the full native lookup context remain unhandled.
    """
    if (instance.component.strict or world.hero != HERO or world.match is None
            or instance.graph.index != FLUX or cfg.get("$") != "STU_C5148EFE"
            or cfg.get("m_bone") is not None):
        return NotImplemented
    entity, offset = cfg.get("m_entity"), cfg.get("m_BD631144")
    if (expr.dynamic(entity) != (graph.INSTANCE, 1883)
            or expr.dynamic(offset) != (graph.INSTANCE, 2478)):
        return NotImplemented
    if instance.evaluate(entity) != expr.Entity(0):
        return NotImplemented
    point = instance.evaluate(offset)
    return point if isinstance(point, expr.Vec3) else expr.EVALUATION_FAILED


def _flux_source(match, instance):
    source = expr.to_entity(instance.context().var(graph.INSTANCE, 41))
    return next((p for p in match.players
                 if match._shown(p) and p.body == source and p.hero.guid == HERO), None)


def interrupt_flux_lift(fight, caster):
    """Release this cast's targets until 1DBC has committed their descent."""
    if isinstance(caster, bots.Bot) or caster.hero.guid != HERO:
        return
    for target in fight._targets():
        script = target.owner.body_script
        if script is None or script.component.strict:
            continue
        component = script.component
        for instance in tuple(component.instances.values()):
            if (instance.graph.index != FLUX_EFFECT
                    or expr.to_entity(instance.context().var(graph.INSTANCE, 41)) != caster.body):
                continue
            lift = instance.states.get(16)  # 1DBC node 33 owns the pre-descent phase
            if lift is None or lift.active or not lift.counter:
                component.destroy(instance)


def flux_effect_native(world, cfg, instance):
    """Bind 1DBC's source and maximum-health queries to the current Match bodies."""
    if instance.component.strict or instance.graph.index != FLUX_EFFECT or world.match is None:
        return NotImplemented
    if cfg.get("$") == "STU_E72F6348":
        condition = instance.graph.node(60).field("m_condition")
        if cfg == condition["m_configVars"][0]:
            return _flux_source(world.match, instance) is not None
    if cfg.get("$") == "STU_60146072":
        amount = instance.graph.node(14).field("m_modifyHealth")["m_amount"]
        if cfg == amount["m_configVars"][0]:
            fight = world.match.combat
            for target in fight._targets():
                if target.entity == instance.component.entity:
                    return expr.f32(fight.vitals_of(target).maximum)
    if cfg.get("$") == "STU_8CBDD81C":
        sample = instance.graph.node(57).field("m_143D4C5B")[0]["m_value"]
        if cfg == sample:
            return world.match.script_body_position(instance.component.entity, instance.component.entity)
    return NotImplemented


def sync_flux_airborne(component, mover):
    """Publish the target pose before 1DBC evaluates its airborne branches."""
    if component.strict:
        return
    var = component.entity_var(417, create=False)
    # Keep updating a true value through landing if the effect ends early.
    if ((var is not None and var.base is True)
            or any(i.graph.index == FLUX_EFFECT for i in component.instances.values())):
        component.set(417, mover is not None and mover.airborne)


def sync_npc_mods(bot, component, frame):
    mover = bot.displacement
    if mover is None:
        return
    active = {i.id for i in component.instances.values() if i.graph.index == FLUX_EFFECT}
    if active or mover.mods:
        from ow174.game.mover import statescript_mods

        mods = statescript_mods(component) if active else {}
        mover.track_mods({key: value for key, value in mods.items() if key[0] in active}, frame)


def flux_velocity(instance, node):
    """Apply the authored target velocity rows before the next body pose."""
    component = instance.component
    match = getattr(component.world, "match", None)
    caster_action = instance.graph.index == FLUX and node.pos in (18, 124)
    target_action = instance.graph.index == FLUX_EFFECT and node.pos in (4, 38, 41, 47)
    if component.strict or match is None or not (caster_action or target_action):
        return False
    target = next((p for p in match.players if match._shown(p) and p.body == component.entity), None)
    if target is None and target_action:
        target = next((bot for bot in match.bots if bot.entity == component.entity
                       and bot.spawner.get("manual_spawn") is True and not bot.dead), None)
    if target is None or target.body_script is None or target.body_script.component is not component:
        return False
    if component.owner_dead or match.ended or (target_action and _flux_source(match, instance) is None):
        return True
    from ow174.game.mover import FELL

    # Scoped reconstruction of the rows used by Flux. Movement still passes
    # through the existing capsule collision, gravity and modifier integration.
    if isinstance(target, bots.Bot):
        mover = target.displacement
        if mover is None:
            if node.pos not in (38, 47) or target.rooted_until_tick > match.tick:
                if node.pos == 4:
                    target.velocity = (0.0, 0.0, 0.0)
                return True
            mover = target.ensure_displacement(match.collision_world)
    else:
        mover = target.mover
    for row in node.field("m_BC5E91CF"):
        config = row["m_9B7A63EA"]
        value = instance.evaluate(config)
        if row["$"] == "STU_9AF0FA80" and value == 0:
            if row["m_A9561CA0"] == 0:
                mover.velocity = (0.0, 0.0, 0.0)
            elif caster_action and row["m_A9561CA0"] == 2:
                x, _, z = mover.velocity
                mover.velocity = (x, 0.0, z)
        elif row["$"] == "STU_4635C5E2" and row["m_CFEC5F76"] == 1 and isinstance(value, expr.Vec3):
            mover.velocity = tuple(expr.f32(a + b) for a, b in zip(mover.velocity, value, strict=True))
            if mover.velocity[1] > 0:
                mover.flags_word |= FELL
        elif row["$"] == "STU_748FC65A" and row["m_A9561CA0"] == 2 and type(value) in (int, float):
            x, y, z = mover.velocity
            # Native C32860 maps selector 2 to signed vertical field 34.
            # The 748FC65A row selects MINIMUM, so downward speed is preserved.
            mover.velocity = (x, expr.f32(min(y, value)), z)
        elif (caster_action and row["$"] == "STU_748FC65A" and row["m_A9561CA0"] == 0
              and type(value) in (int, float) and value >= 0):
            length = math.sqrt(sum(axis * axis for axis in mover.velocity))
            if length > value:
                mover.velocity = tuple(expr.f32(axis * value / length) for axis in mover.velocity)
    return True


def flux_modify_health(world, state, elapsed_ms):
    """Apply the authored entry hit and finish a slam after ground contact."""
    instance = state.instance
    match = world.match
    if (instance.component.strict or instance.graph.index != FLUX_EFFECT
            or state.node.pos not in (12, 14) or match is None):
        return NotImplemented
    slam = state.node.pos == 14
    caster = _flux_source(match, instance)
    if caster is None or match.ended or instance.component.owner_dead:
        instance.component.destroy(instance)
        return True
    if elapsed_ms and not slam:
        return True
    if instance.evaluate(state.node.field("m_FA3ECC43")) != expr.Entity(instance.component.entity):
        return True
    fight = match.combat
    target = next((t for t in fight._enemies(caster) if t.entity == instance.component.entity), None)
    if target is not None:
        if slam:
            owner = target.owner
            mover = owner.displacement if isinstance(owner, bots.Bot) else owner.mover
            if mover is not None and mover.airborne:
                return True
        config = state.node.field("m_modifyHealth")
        amount = expr.to_float(instance.evaluate(config["m_amount"]))
        asset = instance.evaluate(config["m_loadout"])
        loadout = asset.guid if isinstance(asset, expr.Asset) else None
        fight._damage(caster, target, amount, False, loadout, match.tick)
        if fight.vitals_of(target).dead:
            instance.component.destroy(instance)
    if slam and state.active:
        from ow174.game.script.runtime import FINISH

        state.request(FINISH)
    return True
