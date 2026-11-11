"""Owned 0407 factory for the authored 026A creation requests."""

import logging
import math
from dataclasses import replace

from ow174.game import bots, combat, dva_destruct, npc, pools
from ow174.game.commands import Command
from ow174.game.mover import Mover, defined_mover_data, mover_data, statescript_mods
from ow174.game.script import expr, graph, nodes
from ow174.game.script.bot import BotScript
from ow174.game.script.driver import ULT_CHARGE, ULT_COST, ULT_SECONDS, button_edges
from ow174.game.script.nodes import Ability

log = logging.getLogger(__name__)

DVA = 0x02E000000000007A
MECH = 0x0400000000000407
RETURN_TO_PILOT = 0x024000000000016C
CALL_MECH_CHARGE = 0x0240000000000592


def charge_recipient(match, entity):
    mech = next((b for b in match.bots if b.entity == entity and b.body == MECH
                 and b.detached_bomb and not b.dead and b.body_script is not None), None)
    if mech is None or active_fuse(mech):
        return None
    pilot = match._message_player(mech.source_body)
    if (pilot is None or pilot.gone or pilot.entity != mech.owner or pilot.hero.guid != DVA
            or pilot.controlled_mech is not None or match.combat.is_dead(pilot)):
        return None
    return pilot


def charge_sender(pilot, entity):
    match = pilot.match
    if charge_recipient(match, entity) is not pilot:
        return None
    return next(b.body_script for b in match.bots if b.entity == entity)


def active_fuse(mech):
    if mech.body_script is None:
        return False
    return any(state.active and not state.ending for instance in mech.body_script.component.instances.values()
               if instance.graph.index == 0x0B2F and (state := instance.states.get(6)) is not None)


def return_sender(pilot, entity, *, enqueue=False):
    mech = pilot.controlled_mech
    if (mech is None or mech.entity != entity or mech not in pilot.match.bots or mech.body != MECH
            or mech.owner != pilot.entity or mech.source_body != pilot.body
            or pilot.hero.guid != DVA or mech.body_script is None):
        return None
    if mech.dead or active_fuse(mech) or (enqueue and ultimate_running(mech.body_script.component)):
        return mech.body_script
    return None


def ultimate_running(component):
    return any(state.flags & Ability.RUNNING for instance in component.instances.values()
               if instance.graph.index == 0x0B2F and (state := instance.states.get(0)) is not None)


def queue_demech_return(match, mech):
    pilot = match._message_player(mech.source_body)
    script = mech.body_script
    if script is None or pilot is None or return_sender(pilot, mech.entity) is not script:
        return
    for instance in script.component.instances.values():
        if instance.graph.index == 0x0276 and not instance.stopped and not instance.teardown:
            nodes.send(instance, instance.graph.node(5), None)
            return


def complete_return(pilot):
    mech = pilot.controlled_mech
    mover = Mover(mech.position, round(mech.yaw), pilot.match.collision_world,
                  mover_data(pilot.hero.body), category=pilot.mover.category)
    radius, segment = mover._capsule()
    if mover._engine_body().fits(radius, radius + segment):
        pilot.mover = mover
    mech.detached_bomb = not mech.dead and active_fuse(mech)
    if mech.detached_bomb and any(mech.velocity):
        mech.fuse_mover = Mover(mech.position, round(mech.yaw), pilot.match.collision_world,
                               defined_mover_data(MECH), category=pilot.mover.category)
        mech.fuse_mover.add_external_impulse(mech.velocity)
    pilot.match.combat.history.pop(pilot.body, None)
    release(pilot)


def step_fuse(match, mech, tick):
    script = mech.body_script
    frame = tick + mech.clock_offset
    if script is None or mech.dead or frame <= script.frame:
        return
    mover = mech.fuse_mover
    if mover is not None:
        mover.use(world=match.collision_world)
        mover.step_pose(Command(frame, yaw=round(mech.yaw)), combat.TICK, tick)
        mech.position, mech.velocity, mech.moved_tick = tuple(mover.position), mover.velocity, tick
    try:
        snapshot = script.advance_live(frame, mech.position)
    except Exception:
        log.exception("D.Va detached fuse failed for %08X", mech.entity)
        npc.remove_body(match, mech)
        return
    if snapshot is not None and mech in match.bots:
        for viewer in match.players:
            if viewer.spawned and viewer.client is not None:
                viewer.client.queue_bot_live(mech.entity, snapshot)


def _vector(value):
    return isinstance(value, expr.Vec3) and all(math.isfinite(axis) for axis in value)


def create_mech(match, instance, node):
    """Execute the two canonical026A creation requests using actual prepared definitions.

    B6E3C77D's server implementation is absent from the client capture. This
    factory scopes its reconstructed semantics to026A/7 and026A/15, with upright
    orientation, direct owner, authored pools and explicit source retirement.
    """
    if instance.graph.index != 0x26A or node.pos not in (7, 15):
        return NotImplemented
    owner = instance.evaluate(node.field("m_D749D7D8"))
    definition = instance.evaluate(node.field("m_entityDefinition"))
    point = instance.evaluate(node.field("m_position"))
    forward = instance.evaluate(node.field("m_AD8E614B"))

    instance.component.ensure_valid()
    if (instance.stopped or instance.teardown
            or instance.component.instances.get(instance.id) is not instance):
        return expr.Entity(0)
    if (definition != expr.Asset(MECH) or type(owner) is not expr.Entity or not _vector(point)
            or not _vector(forward) or forward.y != 0 or not (forward.x or forward.z)):
        return NotImplemented
    player = next((p for p in match.players if not p.gone and p.has_body and p.entity == owner.id
                   and p.body == instance.component.entity and p.hero.guid == DVA), None)
    if player is None or match.ended or instance.component.owner_dead:
        return expr.Entity(0)
    if (graph.entity_body(MECH) is None or defined_mover_data(MECH) is None
            or graph.graph(0x303B) is None):
        return expr.Entity(0)
    health, armour, shields = pools.active_pools(DVA, MECH)
    entity = match.combat.next_bot
    if not bots.BOT_ENTITY <= entity < 0xD0000000 or any(b.entity == entity for b in match.bots):
        return expr.Entity(0)
    data = {"hero": f"0x{DVA:016X}", "team": player.team, "position": tuple(point),
            "rotation": (0, 0, 0, 1), "graphs": [], "vars": {}}
    bot = bots.Bot(entity, data, {"name": "D.Va mech", "body": f"0x{MECH:016X}", "health": health}, {})
    bot.owner, bot.source_body = owner.id, player.body
    bot.yaw = float(bots.heading(forward.x, forward.z))
    bot.retain_corpse = True  # ownership cleanup below; never the training-bot respawn policy.
    # A created body keeps its creating VM's frame domain. World ticks and
    # command frames can have different origins; changing only the frame later
    # would invalidate all the already-created absolute timers/state starts.
    bot.clock_offset = instance.component.frame - match.tick
    match.bots.append(bot)  # native ownership lookup must work during graph initialization.
    try:
        script = BotScript(MECH, DVA, entity, instance.component.frame, match.game_map.mode_guid,
                           health+armour+shields, match=match)
    except Exception:
        match.bots.remove(bot)  # no ID/health/create record has been published yet.
        raise
    bot.body_script = script
    bot.firing = False
    from ow174.game.match import BOT_RECORD_DELAY

    bot.records_from = match.tick+BOT_RECORD_DELAY
    match.combat.next_bot += 1
    match.combat.vitals[entity] = combat.Vitals(entity, health, armour, shields)
    match.combat.record_replacement(entity)
    for viewer in match.players:
        if viewer.client is not None and viewer.spawned:
            viewer.client.queue_entities([match._bot_create_update(bot)])
            match.bots_shown.setdefault(viewer.slot, match.tick)
    return expr.Entity(entity)


def play_cleanup(match, instance, node):
    if instance.graph.index != 0x026A or node.pos not in (211, 212):
        return False
    resource = node.field("m_69F4B52C.m_6525E98B")
    if not isinstance(resource, dict) or resource.get("m_graph") != "0x058000000000303B":
        return False
    pilot = match._message_player(instance.component.entity)
    definition = graph.graph(0x303B)
    if pilot is None or definition is None:
        return False
    entity = expr.to_entity(instance.evaluate(node.field("m_targets")))
    mech = next((b for b in match.bots if b.entity == entity and b.body == MECH
                 and b.owner == pilot.entity and b.source_body == pilot.body), None)
    if mech is not None and mech.body_script is not None:
        component = mech.body_script.component
        component.play_loaded_graph(definition, source=pilot.body, policy=0)
        component.run_now()
    return True


def retire_source(match, controller, source_body):
    for player in match.players:
        if player.entity == controller and player.body == source_body:
            release(player)
            break
    for bot in tuple(match.bots):
        if bot.owner == controller and bot.source_body == source_body and not bot.detached_bomb:
            npc.remove_body(match, bot)


def bind_initial(player):
    script = player.body_script
    if script is None or player.hero.guid != DVA or player.hero.body == MECH:
        return
    instance = next((i for i in script.component.instances.values() if i.graph.index == 0x026A), None)
    if instance is not None:
        bind_control(player, instance, 11)


def bind_control(player, instance, scope_index):
    if player.body_script is None or player.body_script.component is not instance.component:
        return
    scope = instance.states.get(scope_index)
    if scope is None or not scope.active or scope.ending:
        return
    target = instance.evaluate(instance.graph.node(20).field("m_C98661CF"))
    mech = next((bot for bot in player.match.bots if target == expr.Entity(bot.entity)
                 and bot.body == MECH and bot.owner == player.entity
                 and bot.source_body == player.body), None)
    if mech is not None and mech.body_script is not None and not mech.dead:
        mech.control_mover = Mover(mech.position, round(mech.yaw), player.match.collision_world,
                                   defined_mover_data(MECH), category=player.mover.category)
        player.controlled_mech = mech


def release(player):
    mech, player.controlled_mech = player.controlled_mech, None
    if mech is not None:
        mech.control_mover = None
        mech.control_buttons = 0


def drive(player, command, seconds):
    match = player.match
    mech = player.controlled_mech
    script = mech.body_script
    mover = mech.control_mover
    if mech.dead and script is not None and any(
        source is script and recipient is player and message == RETURN_TO_PILOT
        for source, recipient, _, message, _ in match._body_messages
    ):
        return True
    if mech.dead or script is None or mover is None or mech not in match.bots:
        release(player)
        return False
    if command.frame <= script.frame:
        return True
    mover.use(world=match.collision_world)
    fight = match.combat

    def advance(pose, dt, tick):
        if player.controlled_mech is not mech or pose.frame <= script.frame:
            return
        match.body_command(player, Command(pose.frame))
        if player.body_script is None:
            release(player)
            return
        if player.controlled_mech is not mech:
            return
        player.body_script.component.shots.clear()
        if fight.is_stunned(mech, tick=tick):
            pose = replace(pose, buttons=0, action=0, forward=0, right=0)
        before = tuple(mover.position)
        mover.step_pose(pose, dt, tick)
        mech.position, mech.velocity = tuple(mover.position), mover.velocity
        match.combat.walls.move_bot(mech, before)
        if tuple(mover.position) != mech.position:
            x, y, z = mech.position
            mover._place(mover._engine_body(), (x, y + mover._lift(), z), True)
            mover.stop_drive()
            mech.position, mech.velocity = tuple(mover.position), mover.velocity
        mech.yaw, mech.pitch, mech.moved_tick = mover.yaw, mover.pitch, tick
        edges = button_edges(mech.control_buttons, pose.buttons, pose.action)
        mech.control_buttons = pose.buttons
        mech.clock_offset = pose.frame - tick
        snapshot = script.advance_live(pose.frame, mech.position, edges=edges)
        component = script.component
        cost = expr.to_float(component.entity_var(ULT_COST).value())
        charge = expr.to_float(component.entity_var(ULT_CHARGE).value())
        if not ultimate_running(component) and cost > 0 and charge < cost:
            step = expr.f32(cost * component.frame_ms / (1000 * ULT_SECONDS))
            component.set(ULT_CHARGE, min(cost, expr.f32(charge + step)))
        shots, component.shots[:] = list(component.shots), []
        if not fight.is_stunned(mech, tick=tick):
            # Recovered commands precede the received endpoint on the world timeline.
            pose_tick = tick + pose.frame - command.frame
            view_tick = pose_tick - pose.view_delay(player.quanta) * 1e6 / player.quanta
            for state, number, time, activation in shots:
                fight._shoot(mech, pose, state, number, time, activation=activation,
                             snapshot=snapshot or script.latest_live, view_tick=view_tick)
            fight._melee(mech, pose, script, view_tick=view_tick)
            dva_destruct.booster_contact(fight, mech, pose.frame)
        if snapshot is not None:
            for viewer in match.players:
                if viewer.spawned and viewer.client is not None:
                    viewer.client.queue_bot_live(mech.entity, snapshot)
        mover.track_mods(statescript_mods(component), pose.frame)

    mover.step(command, seconds, match.tick, simulate_pose=advance)
    return True
