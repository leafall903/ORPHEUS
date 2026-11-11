"""Initial 026A ownership and Matrix control for a prepared D.Va mech."""

import math

from ow174.game import matrix

ROOT = 0x026A
DVA = matrix.DVA
PILOT = 0x040000000000040A
LIGHT_GUN = 0x02B8
LIGHT_GUN_PROJECTILE = 0x0400000000001000
RETURN_TO_PILOT = 0x024000000000016C
SELF_DESTRUCT = 0x0B2F
BOMB_FINISHED = 0x0240000000000592
BOMB_DETONATED = 0x02400000000005FD


def available():
    from ow174.game import mover
    from ow174.game.script import graph

    return (graph.entity_body(matrix.MECH) is not None and graph.graph(0x02CC) is not None
            and matrix.MECH in mover._movers())


def owned_body(match, entity):
    return next((bot for bot in match.bots if bot.entity == entity and bot.control_owner is not None), None)


def owns_volley(shooter, state, graph, node):
    return (getattr(shooter, "control_owner", None) is not None
            and shooter.control_owner.controlled_mech is shooter
            and shooter.body == matrix.MECH and shooter.body_script is not None
            and state.instance.component is shooter.body_script.component
            and (state.instance.graph.index, state.node.pos) == (graph, node))


def fusion_directions(shooter, state, forward, shot, activation):
    from ow174.game import volley_geometry, weapon_spread
    from ow174.game.script.expr import f32

    launch = state.node.field("m_5EC1EB38") or {}
    if launch.get("$") != "STU_613BDDB0":
        raise volley_geometry.UnsupportedGeometry("Fusion requires its authored ring launch")
    count = state.evaluate(state.node.field("m_numProjectilesPerShot"), watch=False)
    spread = state.evaluate(launch.get("m_spread"), watch=False)
    jitter = state.evaluate(launch.get("m_92308789"), watch=False)
    phase_field = launch.get("m_391F15AF")
    phase = state.evaluate(phase_field, watch=False) if phase_field is not None else None
    rings = []
    for row in launch.get("m_0D30E40C") or ():
        radius = state.evaluate(row.get("m_6425FFB9"), watch=False)
        size = state.evaluate(row.get("m_B19BD35D"), watch=False)
        if (type(radius) not in (int, float) or not math.isfinite(radius) or radius < 0
                or type(size) is not int or size <= 0):
            raise volley_geometry.UnsupportedGeometry("invalid Fusion ring")
        rings.append((radius, size))
    if (type(count) is not int or not 1 <= count <= 256 or sum(size for _, size in rings) != count
            or type(spread) not in (int, float) or not math.isfinite(spread) or spread < 0
            or any(spread * radius >= 90 for radius, _ in rings)
            or type(jitter) not in (int, float) or not math.isfinite(jitter) or not 0 <= jitter < 90
            or (phase_field is not None and
                (type(phase) not in (int, float) or not math.isfinite(phase)))):
        raise volley_geometry.UnsupportedGeometry("invalid Fusion count, spread, jitter or phase")
    if phase is not None:
        phase = f32(f32(phase) * f32(math.pi / 180))
        if phase < 0:
            phase = math.fmod(phase, math.tau) + math.tau
    # D74B68 uses the authored state index. Body and script IDs are host mappings.
    base = weapon_spread.seed_base(shooter.entity, state.instance.id, state.index, activation[0])
    return weapon_spread.ring_directions(forward, spread, jitter, phase, rings, base, shot)


def gain_mech_damage(mech, amount):
    from ow174.game.script import nodes

    pilot = mech.control_owner
    script = mech.body_script
    if pilot.controlled_mech is not mech or mech.dead or script is None or script.component.owner_dead:
        return
    component = script.component
    if any((state := instance.states.get(0)) is not None and state.flags & nodes.Ability.RUNNING
           for instance in component.instances.values() if instance.graph.index == 0x0B2F):
        return
    _gain_damage_charge(component, amount)


def gain_pilot_damage(pilot, amount):
    from ow174.game.script import nodes

    script = pilot.body_script
    if (pilot.hero.body != PILOT or pilot.controlled_mech is not None or script.retired
            or script.component.owner_dead or pending_bomb(script)
            or any(state.flags & nodes.Ability.RUNNING for state in script.ultimates)):
        return
    _gain_damage_charge(script.component, amount)


def _gain_damage_charge(component, amount):
    from ow174.game.script import expr
    from ow174.game.script.driver import ULT_CHARGE, ULT_COST

    charge = expr.to_float(component.entity_var(ULT_CHARGE).value())
    cost = expr.to_float(component.entity_var(ULT_COST).value())
    if charge < cost:
        component.set(ULT_CHARGE, expr.f32(min(cost, charge + amount)))


def fuse_active(bot):
    if bot.body_script is None:
        return False
    return any((state := instance.states.get(6)) is not None and state.active
               for instance in bot.body_script.component.instances.values()
               if instance.graph.index == SELF_DESTRUCT)


def pending_bomb(script):
    match = script.world.match
    return (script.hero == DVA and match is not None
            and any(bot.detached_bomb and bot.control_owner.body_script is script and fuse_active(bot)
                    for bot in match.bots))


def bomb_actor(world, instance):
    if world.match is None or instance.graph.index != SELF_DESTRUCT or world.hero != DVA:
        return None
    bot = owned_body(world.match, world.body)
    if bot is not None and bot.body_script is not None and bot.body_script.component is instance.component:
        return bot
    return None


def bomb_native(world, cfg, instance):
    from ow174.game.script import expr

    bot = bomb_actor(world, instance)
    if bot is None:
        return NotImplemented
    cls = cfg.get("$")
    pilot = bot.control_owner
    if cls in ("STU_F7262B36", "STU_F76C486C"):
        field = "m_801B298F" if cls == "STU_F7262B36" else "m_D63CBEC2"
        if instance.evaluate(cfg.get(field)) == expr.Entity(bot.entity):
            return expr.Entity(pilot.entity)
    if cls == "STU_0AC15F78" and instance.evaluate(cfg.get("m_entity")) == expr.Entity(pilot.entity):
        return expr.Entity(pilot.body)
    if cls == "STU_3832D36C":
        selector = cfg.get("m_value") or {}
        code = selector.get("m_expression") or {}
        variables = code.get("m_dynamicVars") or ()
        if (code.get("m_opcodes") == [14, 0, 7, 0, 15, 0] and len(variables) == 1
                and variables[0].get("m_identifier") == 0x2990):
            value = instance.evaluate(selector)
            # D.Va ruleset 0015 supplies the absent blast impulse multiplier.
            return 1.0 if value is None else value
    if cls == "STU_71A87E92" and instance.evaluate(cfg.get("m_entity")) == expr.Entity(bot.entity):
        return expr.Vec3(*bot.position)
    return NotImplemented


def native(world, cfg, instance):
    """Resolve initial creation pose in the selecting pilot's frame."""
    if instance is not None and instance.graph.index == SELF_DESTRUCT:
        return bomb_native(world, cfg, instance)
    if (world.hero != matrix.DVA or world.match is None or instance is None
            or instance.graph.index != ROOT):
        return NotImplemented
    from ow174.game.combat import aim
    from ow174.game.script import expr

    cls = cfg.get("$")
    if cls not in ("STU_8CBDD81C", "STU_0BA27A07", "STU_021D01CD", "STU_2EDEE064",
                   "STU_DB742151", "STU_E72F6348", "STU_71A87E92", "STU_3832D36C"):
        return NotImplemented
    pilot = next((player for player in world.match.players if player.body == world.body and not player.gone), None)
    if pilot is None:
        return NotImplemented
    if cls == "STU_3832D36C":
        selector = cfg.get("m_value") or {}
        if expr.dynamic(selector) == (expr.ENTITY, 0x2A7C):
            value = instance.evaluate(selector)
            return 1.0 if value is None else value
        return NotImplemented
    entity = instance.evaluate(cfg.get("m_entity"))
    if cls == "STU_E72F6348":
        if cfg is instance.graph.node(150).field("m_condition"):
            return entity == expr.Entity(pilot.entity) and available()
        if expr.dynamic(cfg.get("m_entity")) == (expr.INSTANCE, 38):
            bot = owned_body(world.match, expr.to_entity(entity))
            return bot is not None and bot.control_owner is pilot and not bot.dead
        return NotImplemented
    if cls in ("STU_71A87E92", "STU_021D01CD") and entity != expr.Entity(world.body):
        bot = owned_body(world.match, expr.to_entity(entity))
        if bot is None or bot.control_owner is not pilot or bot.dead:
            return NotImplemented
        return expr.Vec3(*bot.position) if cls == "STU_71A87E92" else expr.Vec3(*aim(bot.yaw, 0))
    if entity != expr.Entity(world.body):
        return NotImplemented
    if cls in ("STU_8CBDD81C", "STU_0BA27A07", "STU_71A87E92"):
        return expr.Vec3(*pilot.mover.position)
    if cls in ("STU_021D01CD", "STU_2EDEE064"):
        return expr.Vec3(*aim(pilot.mover.yaw, 0))
    return expr.Entity(pilot.entity)


def create_mech(world, instance, node):
    if world.hero != matrix.DVA or instance.graph.index != ROOT or node.pos not in (7, 15) or world.match is None:
        return NotImplemented
    from ow174.game import bots, combat, pools
    from ow174.game.match import BOT_RECORD_DELAY
    from ow174.game.script import expr
    from ow174.game.script.bot import BotScript
    from ow174.game.script.driver import BodyWorld

    match = world.match
    if not available():
        return expr.Entity(0)
    owner = instance.evaluate(node.field("m_D749D7D8"))
    definition = instance.evaluate(node.field("m_entityDefinition"))
    position = instance.evaluate(node.field("m_position"))
    forward = instance.evaluate(node.field("m_AD8E614B"))
    if (definition != expr.Asset(matrix.MECH) or type(position) is not expr.Vec3
            or type(forward) is not expr.Vec3 or forward.y != 0 or not (forward.x or forward.z)):
        return NotImplemented
    pilot = next((player for player in match.players if owner == expr.Entity(player.entity)
                  and player.body == instance.component.entity and player.has_body and not player.gone), None)
    if pilot is None or match.ended or instance.component.owner_dead:
        return expr.Entity(0)
    if (pilot.controlled_mech is not None
            or any(bot.control_owner is pilot and (node.pos == 7 or not bot.detached_bomb)
                   for bot in match.bots)):
        return expr.Entity(0)
    entity = match.combat.next_bot
    if not bots.BOT_ENTITY <= entity < 0xD0000000 or any(bot.entity == entity for bot in match.bots):
        return expr.Entity(0)
    health, armour, shields = pools.body_pools(matrix.DVA, matrix.MECH)
    data = {"hero": f"0x{matrix.DVA:016X}", "team": pilot.team, "position": tuple(position),
            "rotation": (0, 0, 0, 1), "graphs": [], "vars": {}}
    bot = bots.Bot(entity, data, {"name": "D.Va mech", "body": f"0x{matrix.MECH:016X}", "health": health}, {})
    bot.control_owner = pilot
    bot.yaw = float(bots.heading(forward.x, forward.z))
    bot.retain_corpse = True
    bot.records_from = match.tick + BOT_RECORD_DELAY
    host = BodyWorld(matrix.DVA, match.game_map.mode_guid, entity)
    host.match = match
    # The child has to resolve its owner while its graphs initialize.
    match.bots.append(bot)
    try:
        bot.body_script = BotScript(matrix.MECH, matrix.DVA, entity, instance.component.frame,
                                    match.game_map.mode_guid, health + armour + shields,
                                    body_world=host, frame_ms=instance.component.frame_ms)
    except Exception:
        match.bots.remove(bot)
        raise
    match.combat.next_bot += 1
    vitals = match.combat.vitals[entity] = combat.Vitals(entity, health, armour, shields)
    match.combat._sync_body_health(bot, vitals)
    match.combat.record_replacement(entity)
    for viewer in match.players:
        if viewer.client is not None and viewer.spawned:
            viewer.client.queue_entities([match._bot_create_update(bot)])
            match.bots_shown.setdefault(viewer.slot, match.tick)
    return expr.Entity(entity)


def bind_initial(pilot):
    for instance in pilot.body_script.component.instances.values():
        if instance.graph.index == ROOT:
            bind_control(pilot, instance, 11)
            return


def bind_replacement(instance):
    match = instance.component.world.match
    if match is not None and (pilot := match._message_player(instance.component.entity)) is not None:
        bind_control(pilot, instance, 15)


def bind_control(pilot, instance, scope_index):
    from ow174.game.mover import Mover, mover_data
    from ow174.game.script import expr

    scope = instance.states.get(scope_index)
    if (pilot.body_script is None or pilot.body_script.component is not instance.component
            or pilot.controlled_mech is not None or scope is None or not scope.active or scope.ending):
        return
    entity = expr.to_entity(instance.evaluate(instance.graph.node(20).field("m_C98661CF")))
    bot = owned_body(pilot.match, entity)
    if bot is None or bot.control_owner is not pilot or bot.dead or bot.detached_bomb:
        return
    bot.control_mover = Mover(bot.position, round(bot.yaw), pilot.match.collision_world,
                              mover_data(bot.body), category=pilot.mover.category)
    pilot.controlled_mech = bot
    pilot.mover.body = None


def drive(pilot, command, seconds):
    """Use the child's movement and input clocks while the pilot VM receives neutral input."""
    from ow174.game.commands import Command
    from ow174.game.mover import statescript_mods
    from ow174.game.script.driver import button_edges

    match, bot = pilot.match, pilot.controlled_mech
    script, mover = bot.body_script, bot.control_mover
    if bot.dead:
        match.deliver_body_messages()
        if pilot.controlled_mech is not bot:
            return False
    if bot.dead or script is None or mover is None:
        return True
    mover.use(world=match.collision_world)
    pilot._advance_body_gap(command.frame)
    pilot._advance_body_frame(Command(command.frame), posed=False)

    def advance(frame, delta, tick):
        if frame.frame <= script.frame or bot.dead:
            return
        if match.combat.is_stunned(bot, tick=tick):
            frame = Command(frame.frame, yaw=mover.yaw, pitch=mover.pitch)
        mover.step_pose(frame, delta, tick)
        bot.position, bot.velocity = tuple(mover.position), mover.velocity
        bot.yaw, bot.pitch = mover.yaw, mover.pitch
        bot.moved_tick = tick
        world = script.component.world
        world.throttles = mover.throttles
        edges = button_edges(bot.control_buttons, frame.buttons, frame.action)
        bot.control_buttons = frame.buttons
        snapshot = script.advance_live(frame.frame, edges=edges)
        match.combat.controlled_mech_shots(bot, frame, snapshot)
        mover.track_mods(statescript_mods(script.component), frame.frame)

    mover.step(command, seconds, match.tick, simulate_pose=advance)
    return True


def queue_return(bot):
    """Send the authored 0276/5 return from the actual defeated mech."""
    from ow174.game.script import nodes

    pilot = bot.control_owner
    if pilot is None or pilot.controlled_mech is not bot or bot.body_script is None:
        return
    for instance in bot.body_script.component.instances.values():
        if instance.graph.index == 0x0276:
            nodes.send(instance, instance.graph.node(5), None)
            return


def return_to_pilot(pilot, bot):
    from ow174.game.mover import Mover, mover_data

    mover = Mover(bot.position, round(bot.yaw), pilot.match.collision_world,
                  mover_data(pilot.hero.body), category=pilot.mover.category)
    radius, segment = mover._capsule()
    if mover._engine_body().fits(radius, radius + segment):
        pilot.mover = mover
    if not bot.dead and fuse_active(bot):
        pilot.body_script.set_ult_charge(0)
        bot.detached_bomb = True
        bot.last_tick = pilot.match.tick
        # The ejected body keeps momentum and collision, but drops controlled thrust.
        bot.control_mover = Mover(bot.position, round(bot.yaw), pilot.match.collision_world,
                                  mover_data(bot.body), category=pilot.mover.category)
        if any(bot.velocity):
            bot.control_mover.add_external_impulse(bot.velocity)
    else:
        bot.control_mover = None
    pilot.controlled_mech = None
    pilot.match.combat.record_replacement(pilot.body)
    for instance in pilot.body_script.component.instances.values():
        if instance.graph.index != ROOT:
            continue
        scope = instance.states.get(15)
        ability = instance.states.get(5)
        if scope is not None and scope.active and ability is not None:
            ability.stop()
            instance.component.run_now()
        break


def advance_bomb(match, bot, tick):
    from ow174.game import npc
    from ow174.game.commands import Command

    script, mover = bot.body_script, bot.control_mover
    while bot.last_tick < tick and not bot.bomb_cleanup:
        bot.last_tick += 1
        frame = script.frame + 1
        if mover is not None:
            mover.use(world=match.collision_world)
            mover.step(Command(frame, yaw=round(bot.yaw), pitch=round(bot.pitch)),
                       script.component.frame_ms / 1000, bot.last_tick)
            bot.position, bot.velocity = tuple(mover.position), mover.velocity
        snapshot = script.advance_live(frame, bot.position)
        script.component.shots.clear()
        if script.component.fault is not None or script.component.owner_dead:
            bot.bomb_cleanup = True
        if snapshot is not None and not bot.bomb_cleanup:
            for viewer in match.players:
                if viewer.spawned and viewer.client is not None:
                    viewer.client.queue_bot_live(bot.entity, snapshot)
    if bot.bomb_cleanup:
        npc.remove_body(match, bot)


def cleanup_predecessor(world, instance, node):
    from ow174.game import npc
    from ow174.game.script import expr

    if (world.hero != DVA or world.match is None or instance.graph.index != ROOT
            or node.pos not in (211, 212)
            or expr.guid_of(node.field("m_69F4B52C.m_6525E98B.m_graph")) != 0x058000000000303B):
        return
    entity = expr.to_entity(instance.evaluate(node.field("m_targets")))
    bot = owned_body(world.match, entity)
    if (bot is not None and bot.control_owner.body == instance.component.entity
            and bot.control_owner.controlled_mech is not bot and not fuse_active(bot)):
        # The hosted 303B predecessor retires the previous body before factory dispatch.
        npc.remove_body(world.match, bot)


def retire_unbound(pilot):
    from ow174.game import npc

    for bot in tuple(pilot.match.bots):
        if bot.control_owner is pilot and pilot.controlled_mech is not bot and not bot.detached_bomb:
            npc.remove_body(pilot.match, bot)


def retire(pilot):
    from ow174.game import npc

    pilot.controlled_mech = None
    for bot in tuple(pilot.match.bots):
        if bot.control_owner is pilot:
            npc.remove_body(pilot.match, bot)
