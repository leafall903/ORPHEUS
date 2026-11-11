"""Scoped 1E41 placement and 1DBC effects on current Match bodies."""

import math
from dataclasses import replace

from ow174.game import bots, mei, phase_defenses, reinhardt_barrier
from ow174.game.collision import MOVER, TEAM0, TEAM1
from ow174.game.commands import CROUCH, JUMP, Command
from ow174.game.mover import FELL, Mover, mover_data, statescript_mods
from ow174.game.script import expr, graph, nodes
from ow174.game.sigma_combat import HERO

FLUX = 0x1E41
FLUX_EFFECT = 0x1DBC
NOT_HANDLED = NotImplemented
REPLAY_LIMIT = 768  # bounds omitted-frame playback beyond a normal complete cast


def body_protection(owner):
    return phase_defenses.active(owner) or mei.protected(owner)


def play_flux_request(world, instance, node):
    """Deliver the authored request on each selected body's existing VM clock."""
    if (instance.component.strict or instance.graph.index != FLUX or node.pos != 38
            or world.match is None or instance.component.entity != world.body or world.match.ended):
        return False
    if world.body in world.match.combat.hacks:
        return True
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
        if target is None or fight.vitals_of(target).dead or body_protection(target.owner):
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
        if not isinstance(target.owner, bots.Bot):
            fight.flux_players.add(entity)
    return True


def _flux_source(match, instance):
    source = expr.to_entity(instance.context().var(graph.INSTANCE, 41))
    return next((p for p in match.players
                 if match._shown(p) and p.body == source and p.hero.guid == HERO), None)


def interrupt_flux_lift(fight, caster):
    """Release this cast's targets until 1DBC has committed their descent."""
    if isinstance(caster, bots.Bot) or caster.hero.guid != HERO:
        return
    script = caster.body_script
    if script is not None:
        for instance in tuple(script.component.instances.values()):
            if instance.graph.index == FLUX:
                state = instance.states.get(0)
                if isinstance(state, nodes.Ability) and state.flags & nodes.Ability.RUNNING:
                    state.stop()
    for target in fight._targets():
        script = getattr(target.owner, "body_script", None)
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


def flux_caster_native(world, cfg, instance):
    """Use upright body up for the exact caster launch direction query."""
    if (instance.component.strict or instance.graph.index != FLUX or world.hero != HERO
            or cfg.get("$") != "STU_D32169F6" or instance.component.entity != world.body):
        return NotImplemented
    launch = instance.graph.node(18).field("m_BC5E91CF")[1]["m_9B7A63EA"]
    return expr.Vec3(0.0, 1.0, 0.0) if cfg == launch["m_configVars"][0] else NotImplemented


def flux_flight_data(player):
    """Expose the authored flight mover tuning while the graph owns node 35."""
    script = player.body_script
    if script is None or player.hero.guid != HERO or script.component.strict:
        return None
    for instance in script.component.instances.values():
        if instance.graph.index != FLUX:
            continue
        state = instance.states.get(13)
        if state is not None and state.active:
            cfg = state.node.field("m_BC5E91CF")[1]["m_9B7A63EA"]
            return replace(player.mover.data, run_forward=cfg["m_CFD3E635"], run_backward=cfg["m_61D6B4E9"],
                           run_strafe=cfg["m_19452F15"], accel_air=cfg["m_E631BA84"],
                           decel_air_base=cfg["m_142DA8D4"], decel_air_base_speed=cfg["m_031FE7EE"],
                           decel_air_per_speed=cfg["m_8895A9C8"])
    return None


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
            mover = fight.flux_npcs.get(target.entity) if isinstance(owner, bots.Bot) else owner.mover
            if mover is not None and mover.airborne:
                return True
        config = state.node.field("m_modifyHealth")
        amount = expr.to_float(instance.evaluate(config["m_amount"]))
        asset = instance.evaluate(config["m_loadout"])
        loadout = asset.guid if isinstance(asset, expr.Asset) else None
        if not reinhardt_barrier.protect_body(fight, target, amount, attacker=caster.body):
            fight._damage(caster, target, amount, False, loadout, fight.tick, source_body=caster.body)
        if fight.vitals_of(target).dead:
            instance.component.destroy(instance)
    if slam and state.active:
        from ow174.game.script.runtime import FINISH

        state.request(FINISH)
    return True


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
        # This capsule bridge hosts player and PvE movement, not fixed summons.
        if target.is_device or (isinstance(target.owner, bots.Bot)
                                and not target.owner.spawner.get("manual_spawn")):
            continue
        if body_protection(target.owner):
            continue
        accepted, distance = volume.capsule(target.feet, target.radius, target.height)
        if accepted and capsule_visible(match.combat._line_collision(), tuple(sight),
                                        *target.segment(), target.radius):
            found.append((distance, target.entity))
    return tuple(entity for _distance, entity in sorted(found))


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
    player = next((p for p in world.match.players if p.has_body and p.body == world.body), None)
    if (not state.active or player is None or instance.component.owner_dead or world.match.ended):
        return True
    reach = expr.to_float(state.evaluate(state.node.field("m_6386E7B4"), watch=False))
    collision = combat._line_collision()
    if collision is None:
        return True
    from ow174.game.combat import aim

    hit = collision.cast_ray(combat._eye(player), aim(player.mover.yaw, player.mover.pitch), reach)
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
        return NOT_HANDLED
    entity, offset = cfg.get("m_entity"), cfg.get("m_BD631144")
    if (expr.dynamic(entity) != (graph.INSTANCE, 1883)
            or expr.dynamic(offset) != (graph.INSTANCE, 2478)):
        return NOT_HANDLED
    if instance.evaluate(entity) != expr.Entity(0):
        return NOT_HANDLED
    point = instance.evaluate(offset)
    return point if isinstance(point, expr.Vec3) else expr.EVALUATION_FAILED


def flux_actor_point_query(world, cfg, instance):
    """1E41/node37's candidate point, not a candidate entity-ID mapping.

    CE47D0/D1A410 writes a Vec3. Its actor arm uses C5E3C0's transform and
    D9F3F0's height along local up. Match bodies are upright; the existing
    Combat pose/eye input is the declared host binding. Native transform/height
    storage, non-actor fallback and complete target filters remain unverified.
    """
    if (instance.component.strict or world.hero != HERO or world.match is None
            or instance.component.entity != world.body or instance.graph.index != FLUX
            or cfg.get("$") != "STU_21A13145"):
        return NOT_HANDLED
    expected = instance.graph.node(37).field("m_48837057")
    if cfg != expected:
        return NOT_HANDLED
    entity = expr.to_entity(instance.evaluate(cfg.get("m_entity")))
    target = next((target for target in world.match.combat._targets() if target.entity == entity), None)
    if target is None:
        return expr.EVALUATION_FAILED
    x, y, z = target.feet
    return expr.Vec3(expr.f32(x), expr.f32(expr.f32(y) + expr.f32(target.eye)), expr.f32(z))


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
    # Scoped reconstruction of the rows used by Flux. Movement still passes
    # through the existing capsule collision, gravity and modifier integration.
    if isinstance(target, bots.Bot):
        mover = match.combat.flux_npcs.get(target.entity)
        if mover is None:
            if node.pos not in (38, 47):
                if node.pos == 4:
                    target.velocity = (0.0, 0.0, 0.0)
                return True
            category = MOVER if match.game_map.free_for_all else (TEAM0, TEAM1)[target.team]
            mover = Mover(target.position, round(target.yaw), match.combat._collision(),
                          mover_data(target.body), category=category)
            match.combat.flux_npcs[target.entity] = mover
    else:
        mover = target.mover
        match.combat.flux_players.add(component.entity)
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
                if target_action and isinstance(target, bots.Bot):
                    from ow174.game import assassin, heavy_assault, sniper_special

                    if target.hero == assassin.HERO:
                        assassin.bind(component.world, component, None)
                    heavy_assault.interrupt(target)
                    sniper_special.interrupt(target)
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

def flight_vertical(command):
    return 7.15 * (bool(command.buttons & JUMP) - bool(command.buttons & CROUCH))


def update_npcs(fight, tick, dt):
    for bot in fight.match.bots:
        mover = fight.flux_npcs.get(bot.entity)
        if mover is None:
            continue
        script = bot.body_script
        if script is None or bot.dead:
            fight.flux_npcs.pop(bot.entity, None)
            continue
        component = script.component
        mover.use(world=fight._collision())
        mover.track_mods(statescript_mods(component), tick - 1)
        mover.step_pose(Command(tick, yaw=round(bot.yaw), pitch=round(bot.pitch)), dt, tick)
        bot.position = tuple(mover.position)
        bot.velocity = mover.velocity
        bot.moved_tick = tick
        sync_replayed_pose(fight, bot.entity, bot.position, tick)
        sync_flux_airborne(component, mover)
        if not mover.airborne and not any(i.graph.index == FLUX_EFFECT for i in component.instances.values()):
            fight.flux_npcs.pop(bot.entity, None)


def _casting(actor):
    script = actor.body_script
    if script is None or script.hero != HERO:
        return False
    for instance in script.component.instances.values():
        if instance.graph.index == FLUX:
            state = instance.states.get(0)
            if state is not None and state.flags & nodes.Ability.RUNNING:
                return True
    return False


def _has_effect(script):
    return any(i.graph.index == FLUX_EFFECT for i in script.component.instances.values())


def replay_pending(fight):
    return bool(fight.flux_players or fight.flux_npcs
                or any(_casting(actor) for actor in fight.match.players))


def replaying_npc(fight, bot):
    return bot.body_script is not None and (bot.entity in fight.flux_npcs or _has_effect(bot.body_script))


def sync_replayed_pose(fight, entity, position, tick):
    history = fight.history.get(entity)
    if history and history[-1][0] >= tick:
        frame, _, height, eye = history[-1]
        history[-1] = frame, tuple(position), height, eye


def replay_player_poses(fight, tick, dt):
    """Finish omitted neutral poses on the graph clock before the next input frame."""
    if not replay_pending(fight) or fight.match.ended:
        return
    match = fight.match
    scheduled = match.tick
    try:
        match.tick = tick
        for actor in match.players:
            entity = actor.body
            casting = _casting(actor)
            if entity not in fight.flux_players and not casting:
                continue
            script = actor.body_script
            if script is None or not match._shown(actor) or fight.is_dead(actor):
                fight.flux_players.discard(entity)
                continue
            if not casting and not actor.mover.airborne and not _has_effect(script):
                fight.flux_players.discard(entity)
                continue
            if script.frame < tick:
                mover = actor.mover
                command = Command(tick, yaw=mover.yaw, pitch=mover.pitch)
                mover.last_command = command
                actor._step_body_pose(command, dt, tick)
                sync_replayed_pose(fight, entity, mover.position, tick)
    finally:
        match.tick = scheduled
