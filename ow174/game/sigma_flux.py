"""Resource-backed static placement and target search for Sigma's 1E41 Flux."""
import math
from dataclasses import replace

from ow174.game.script import expr, graph

HERO = 0x02E000000000023B
FLUX = 0x1E41


def caster(world, instance):
    if (instance.component.strict or world.hero != HERO or world.match is None
            or instance.graph.index != FLUX or instance.component.entity != world.body):
        return None
    return next((p for p in world.match.players if p.has_body and p.body == world.body), None)


def place(world, state, *, confirm):
    """Bind the authored placement outputs to a static upward-facing world hit."""
    instance = state.instance
    if state.node.pos != 23 or (player := caster(world, instance)) is None:
        return NotImplemented
    match = world.match
    if not state.active or instance.component.owner_dead or match.ended:
        return True
    from ow174.game.combat import aim

    reach = expr.to_float(state.evaluate(state.node.field("m_6386E7B4"), watch=False))
    direction = aim(player.mover.yaw, player.mover.pitch)
    hit = match.combat._collision().cast_ray(match.combat._eye(player), direction, reach)
    valid = hit is not None and hit.normal[1] > 1e-6
    if valid:
        instance.write(expr.lvalue(state.node.field("m_30710B76")),
                       expr.Vec3(*(expr.f32(value) for value in hit.point)))
        for field in ("m_1549F0F9", "m_2FECD1E3"):
            instance.write(expr.lvalue(state.node.field(field)), expr.Entity(0))
    instance.write(expr.lvalue(state.node.field("m_2339572B")), valid)
    if confirm:
        instance.follow(state.node, "m_F6955E81" if valid else "m_425337C6", state)
    return True


def native(world, cfg, instance):
    cls = cfg.get("$")
    if cls not in ("STU_C5148EFE", "STU_21A13145", "STU_D32169F6") or caster(world, instance) is None:
        return NotImplemented
    if cls == "STU_D32169F6":
        launch = instance.graph.node(18).field("m_BC5E91CF")[1]["m_9B7A63EA"]
        return expr.Vec3(0.0, 1.0, 0.0) if cfg == launch["m_configVars"][0] else NotImplemented
    if cls == "STU_C5148EFE":
        entity, offset = cfg.get("m_entity"), cfg.get("m_BD631144")
        if (cfg.get("m_bone") is not None or expr.dynamic(entity) != (graph.INSTANCE, 1883)
                or expr.dynamic(offset) != (graph.INSTANCE, 2478) or instance.evaluate(entity) != expr.Entity(0)):
            return NotImplemented
        point = instance.evaluate(offset)
        return point if isinstance(point, expr.Vec3) else expr.EVALUATION_FAILED
    if cfg != instance.graph.node(37).field("m_48837057"):
        return NotImplemented
    entity = expr.to_entity(instance.evaluate(cfg.get("m_entity")))
    target = next((t for t in world.match.combat._targets() if t.entity == entity), None)
    if target is None:
        return expr.EVALUATION_FAILED
    x, y, z = target.feet
    return expr.Vec3(expr.f32(x), expr.f32(expr.f32(y) + expr.f32(target.eye)), expr.f32(z))


def targets(world, state):
    """Bind the authored volume to live hostile capsules and static line of sight."""
    instance = state.instance
    if state.node.pos != 37 or (player := caster(world, instance)) is None:
        return NotImplemented
    match = world.match
    if match.ended or instance.component.owner_dead or not match._shown(player):
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
    half = expr.f32(math.radians(aperture) * 0.5)
    cosine = expr.f32(math.cos(half))
    extent = expr.f32(depth * expr.f32(math.tan(half)))
    volume = Volume(tuple(origin), (0, 1, 0), (cosine, cosine), (extent, extent),
                    (0, 0), (1, 1), depth, parallel=True)
    fight = match.combat
    found = []
    for target in fight._enemies(player):
        accepted, distance = volume.capsule(target.feet, target.radius, target.height)
        if accepted and capsule_visible(fight._collision(), tuple(sight), *target.segment(), target.radius):
            found.append((distance, target.entity))
    return tuple(expr.Entity(entity) for _, entity in sorted(found))


EFFECT = 0x1DBC


def deliver(world, instance, node):
    """Route the authored effect request to the selected bodies' own clocks."""
    if node.pos != 38 or caster(world, instance) is None or world.match.ended:
        return
    from ow174.game.script import play

    resource = play.resolve_resource(instance, node)
    if resource is None or resource.guid != (graph.GRAPH_TYPE << 48) | EFFECT:
        return
    definition = graph.graph(EFFECT)
    request = play.resolve_request(
        instance, node, resource,
        context_provider=lambda: play.InvocationContext(world.body, world.body, instance.source_entity()),
    )
    if definition is None or request is None:
        return
    fight = world.match.combat
    targets = {target.entity: target for target in fight._targets()}
    for entity in request.targets:
        target = targets.get(entity)
        if target is None or fight.vitals_of(target).dead:
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


def controls_target_pose(component):
    """The target effect owns movement from entry damage through landing recovery."""
    return any(
        (state := instance.node_state(instance.graph.node(12))) is not None and state.active
        for instance in component.instances.values() if instance.graph.index == EFFECT
    )


def effect_source(match, instance):
    entity = expr.to_entity(instance.context().var(graph.INSTANCE, 41))
    return next((p for p in match.players if match._shown(p) and p.body == entity and p.hero.guid == HERO), None)


def effect_native(world, cfg, instance):
    if instance.component.strict or instance.graph.index != EFFECT or world.match is None:
        return NotImplemented
    cls = cfg.get("$")
    if cls == "STU_E72F6348" and cfg == instance.graph.node(60).field("m_condition")["m_configVars"][0]:
        return effect_source(world.match, instance) is not None
    if cls == "STU_60146072":
        amount = instance.graph.node(14).field("m_modifyHealth")["m_amount"]
        if cfg == amount["m_configVars"][0]:
            fight = world.match.combat
            target = next((t for t in fight._targets() if t.entity == world.body), None)
            if target is not None:
                vitals = fight.vitals_of(target)
                return expr.f32(sum(vitals.most.values()) + vitals.bonus_shields_max)
    if cls == "STU_8CBDD81C":
        sample = instance.graph.node(57).field("m_143D4C5B")[0]["m_value"]
        if cfg == sample:
            return world.match.script_body_position(world.body, world.body)
    return NotImplemented


def velocity(world, instance, node):
    caster_action = instance.graph.index == FLUX and node.pos in (18, 124)
    target_action = instance.graph.index == EFFECT and node.pos in (4, 38, 41, 47)
    if (instance.component.strict or world.match is None or instance.component.entity != world.body
            or not (caster_action or target_action)):
        return NotImplemented
    player = next((p for p in world.match.players if world.match._shown(p) and p.body == world.body), None)
    if player is None or instance.component.owner_dead or world.match.ended:
        return True
    if target_action and effect_source(world.match, instance) is None:
        return True
    from ow174.game.mover import FELL

    mover = player.mover
    for row in node.field("m_BC5E91CF"):
        value = instance.evaluate(row["m_9B7A63EA"])
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
            mover.velocity = (x, expr.f32(min(y, value)), z)
        elif (caster_action and row["$"] == "STU_748FC65A" and row["m_A9561CA0"] == 0
              and type(value) in (int, float) and value >= 0):
            speed = math.hypot(*mover.velocity)
            if speed > value:
                mover.velocity = tuple(expr.f32(axis * value / speed) for axis in mover.velocity)
    return True


def modify_health(world, state, elapsed_ms):
    """Apply entry once, and defer the authored slam until the mover lands."""
    instance = state.instance
    match = world.match
    if (instance.component.strict or instance.graph.index != EFFECT
            or state.node.pos not in (12, 14) or match is None):
        return NotImplemented
    slam = state.node.pos == 14
    source = effect_source(match, instance)
    if source is None or match.ended or instance.component.owner_dead:
        instance.component.destroy(instance)
        return True
    if elapsed_ms and not slam:
        return True
    if instance.evaluate(state.node.field("m_FA3ECC43")) != expr.Entity(instance.component.entity):
        return True
    fight = match.combat
    target = next((t for t in fight._enemies(source) if t.entity == world.body), None)
    if target is not None:
        if slam and target.owner.mover.airborne:
            return True
        climb = world.hanzo_climb
        if not slam and climb is not None and (climb.active or climb.mantle is not None):
            # Flux takes over from Hanzo's world-target drive on entry.
            climb.active = False
            climb.mantle = None
            climb.blocked = True
            target.owner.mover.driven = False
            instance.component.set(1204, False)
        config = state.node.field("m_modifyHealth")
        amount = expr.to_float(instance.evaluate(config["m_amount"]))
        asset = instance.evaluate(config["m_loadout"])
        loadout = asset.guid if isinstance(asset, expr.Asset) else None
        if not fight.zarya_barriers.absorb_contact(source, target.owner, amount):
            fight._damage(source, target, amount, False, loadout, match.tick)
        if fight.vitals_of(target).dead:
            instance.component.destroy(instance)
    if slam and state.active:
        state.request_finish()
    return True


def sync_airborne(component, mover):
    if component.strict:
        return
    var = component.entity_var(417, create=False)
    if ((var is not None and var.base is True)
            or any(i.graph.index == EFFECT for i in component.instances.values())):
        component.set(417, mover.airborne)


def flight_data(player):
    """Use authored mover tuning only while the caster's flight state owns the pose."""
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


def interrupt_lift(fight, player):
    """Release pre-descent targets when this cast is interrupted."""
    if getattr(getattr(player, "hero", None), "guid", None) != HERO:
        return
    for target in fight._targets():
        script = target.owner.body_script
        if script is None or script.component.strict:
            continue
        component = script.component
        for instance in tuple(component.instances.values()):
            if (instance.graph.index != EFFECT
                    or expr.to_entity(instance.context().var(graph.INSTANCE, 41)) != player.body):
                continue
            lift = instance.states.get(16)
            if lift is None or lift.active or not lift.counter:
                component.destroy(instance)
