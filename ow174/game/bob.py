"""Host Ashe's authored pet creation and script dispatch in a Match."""

import math

from ow174.game import bots, combat, npc
from ow174.game.collision import MOVER, TEAM0, TEAM1
from ow174.game.commands import Command
from ow174.game.mover import Mover, mover_data, statescript_mover_data
from ow174.game.script import expr, graph, play, play_queue

BODY = 0x04000000000025C3
AI_BODY = 0x04000000000025CB
SCRIPT = 0x0580000000001537


def create(world, instance, node):
    match = world.match
    record = graph.entity_body(BODY)
    behavior = graph.graph(0x1537)
    if match is None or match.ended or record is None or behavior is None or graph.graph(0x153B) is None:
        return NotImplemented
    definition = instance.evaluate(node.field("m_entityDefinition"))
    owner = instance.evaluate((node.field("m_D749D7D8") or {}).get("m_entity"))
    position = instance.evaluate(node.field("m_position"))
    if (definition != expr.Asset(BODY) or owner != expr.Entity(world.body)
            or not isinstance(position, expr.Vec3)):
        return NotImplemented
    caster = next((p for p in match.players if p.body == world.body and p.spawned and not p.gone
                   and p.body_script is not None and p.body_script.component is instance.component), None)
    if caster is None or match.combat.is_dead(caster):
        return NotImplemented
    health = next(
        (entry["m_value"]["m_value"] for row in record["graphs"] if row["graph"] == 0x0033
         for entry in row.get("m_1EB5A024") or () if entry["m_0D09D2D9"] == "0x0D800000000000C7"),
        None,
    )
    if type(health) not in (int, float) or health <= 0:
        return NotImplemented
    retire(world)
    pet = npc.spawn(match, npc.Definition(0x02E000000000021D, BODY, "B.O.B.", health),
                    (position.x, position.y, position.z), team=caster.team, yaw=caster.mover.yaw)
    if pet.body_script is None:
        npc.remove_spawned(match, selected=pet)
        return NotImplemented
    pet.spawner["owned_pet"] = world.body
    component = pet.body_script.component
    component.world.match = match
    component.world.pet_body = pet.entity
    component.world.pet_play_queue = play_queue.Queue(
        component, lambda guid: play_queue.Load("ready", behavior) if guid == SCRIPT else None,
    )
    world.bob = pet
    return expr.Entity(pet.entity)


def spatial_query(world, cfg, instance):
    """Read the current unparented pet pose for the authored 1537 expressions."""
    if world.match is None or instance.evaluate(cfg.get("m_entity")) != expr.Entity(world.pet_body):
        return expr.EVALUATION_FAILED
    pet = next((bot for bot in world.match.bots if bot.entity == world.pet_body
                and bot.body_script is not None and bot.body_script.component is instance.component), None)
    if pet is None:
        return expr.EVALUATION_FAILED
    cls = cfg["$"]
    if cls == "STU_021D01CD":
        return expr.Vec3(*(expr.f32(axis) for axis in combat.aim(round(pet.yaw), 0)))
    x, y, z = pet.position
    if cls == "STU_8CBDD81C":
        y = expr.f32(expr.f32(y) + expr.f32(mover_data(pet.body).stand_pogo))
    return expr.Vec3(expr.f32(x), expr.f32(y), expr.f32(z))


def queue_launch(instance, node):
    component = instance.component
    world = component.world
    if (instance.graph.index != 0x1537 or node.pos != 196
            or getattr(world, "pet_body", None) != component.entity):
        return NotImplemented
    row = node.field("m_BC5E91CF")[0]
    delta = instance.evaluate(row["m_9B7A63EA"])
    if isinstance(delta, expr.Vec3):
        world.pet_launch = (component.frame + 1, (delta.x, delta.y, delta.z))
    return True


def move(fight, pet, tick):
    """Consume the graph's preceding-frame movement state before advancing it."""
    component = pet.body_script.component
    phase = next((i for i in component.instances.values() if i.graph.index == 0x1537), None)
    if phase is None:
        return
    charge = phase.node_state(phase.graph.node(195))
    horizontal = None
    if charge is not None and charge.active:
        row = charge.node.field("m_BC5E91CF")[0]
        velocity = charge.evaluate(row["m_9B7A63EA"], watch=False)
        if isinstance(velocity, expr.Vec3):
            horizontal = (velocity.x, velocity.z)
    launch_frame, impulse = vars(component.world).pop("pet_launch", (None, None))
    if launch_frame != tick:
        impulse = None
    mover = getattr(pet, "bob_mover", None)
    if mover is None:
        if horizontal is None and impulse is None:
            return
        category = (TEAM0, TEAM1)[pet.team] if pet.team in (0, 1) else MOVER
        mover = pet.bob_mover = Mover(
            pet.position, round(pet.yaw), fight.match.collision_world, mover_data(pet.body), category,
        )
    before = pet.position, pet.velocity
    mover.use(world=fight.match.collision_world)
    mover.script_data = statescript_mover_data(component)
    mover.step_pose(
        Command(tick, yaw=round(pet.yaw), pitch=round(pet.pitch)), combat.TICK, tick,
        horizontal_velocity=horizontal, impulse=impulse,
    )
    pet.position, pet.velocity = tuple(mover.position), mover.velocity
    if (pet.position, pet.velocity) != before:
        pet.moved_tick = tick


def update_input(fight, pet):
    """Feed the authored weapon using static Match target selection."""
    component = pet.body_script.component
    ai = graph.graph(0x153A)
    body = graph.entity_body(AI_BODY)
    target = None
    phase = next((i for i in component.instances.values() if i.graph.index == 0x1537), None)
    weapon = graph.graph(0x153B)
    button = weapon.node(2).field("m_logicalButton.m_logicalButton")
    if (ai is not None and body is not None and phase is not None and not fight.is_stunned(pet)
            and expr.to_int(component.entity_var(1735).value()) == 2
            and phase.node_state(phase.graph.node(35)).active):
        reach = ai.node(3).field("m_64507AA8.m_value")
        target = visible_target(fight, pet, reach)
    enabled = target is not None
    if enabled:
        dx, dy, dz = target
        yaw = float(bots.heading(dx, dz))
        pitch = float(round(-math.atan2(dy, math.hypot(dx, dz)) * 65536 / (2 * math.pi)))
        if (pet.yaw, pet.pitch) != (yaw, pitch):
            pet.yaw, pet.pitch = yaw, pitch
            pet.moved_tick = fight.tick
    if pet.attack_input != enabled or (button in component.held) != enabled:
        component.edges([(button, enabled)])
    pet.attack_input = enabled
    return enabled


def visible_target(fight, pet, reach):
    x, y, z = pet.position
    origin = (x, y + combat.EYE, z)
    nearest = reach
    selected = None
    for target in fight._enemies(pet):
        if target.entity == pet.spawner["owned_pet"]:
            continue
        tx, ty, tz = target.feet
        delta = (tx - x, ty + target.height * 0.5 - origin[1], tz - z)
        distance = math.hypot(*delta)
        if not 0 < distance <= nearest:
            continue
        direction = tuple(axis / distance for axis in delta)
        wall = combat.ray_world(fight._collision(), origin, direction, distance)
        if wall is None or wall >= distance:
            nearest, selected = distance, delta
    return selected


def route(world, instance, node):
    pet = getattr(world, "bob", None)
    if pet is None or pet not in world.match.bots or pet.dead or pet.body_script is None:
        return
    resource = play.resolve_resource(instance, node)
    if resource is None or resource.guid != SCRIPT:
        return
    request = play.resolve_request(
        instance, node, resource,
        context_provider=lambda: play.InvocationContext(world.body, world.body, world.body),
    )
    if request is not None and request.targets == (pet.entity,):
        pet.body_script.component.world.pet_play_queue.submit(request)


def retire(world):
    pet = vars(world).pop("bob", None)
    if pet is not None:
        npc.remove_spawned(world.match, selected=pet)


def finish_retirement(match, pet):
    for player in match.players:
        script = player.body_script
        if script is not None and getattr(script.world, "bob", None) is pet:
            retire(script.world)
            return
    npc.remove_spawned(match, selected=pet)
