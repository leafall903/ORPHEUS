"""Owned D.Va mech creation and reconstructed input routing."""

from ow174.game import bots, combat, deflection, dva, npc, pools
from ow174.game.commands import SECONDARY, Command
from ow174.game.mover import REPEAT, Mover, defined_mover_data, mover_data, statescript_mods, statescript_flight, statescript_mover_data
from ow174.game.script import expr, graph, nodes
from ow174.game.script.bot import BotScript
from ow174.game.script.driver import MAX_GAP, ULT_SECONDS, button_edges, charge_ultimate

DVA = 0x02E000000000007A
MECH = 0x0400000000000407
RETURN_TO_PILOT = 0x024000000000016C


def begin_control(match, instance):
    pilot = match._message_player(instance.component.entity)
    if pilot is not None:
        _bind_control(pilot, instance, 15)


def begin_initial_control(pilot):
    """Bind only after the initial pilot and owned-body scripts have published."""
    for instance in pilot.body_script.component.instances.values():
        if instance.graph.index == 0x26A:
            _bind_control(pilot, instance, 11)
            return


def _bind_control(pilot, instance, scope_index):
    scope = instance.states.get(scope_index)
    if (pilot.body_script is None or pilot.body_script.component is not instance.component
            or scope is None or not scope.active or scope.ending):
        return
    match = pilot.match
    target = instance.evaluate(instance.graph.node(20).field("m_C98661CF"))
    bot = next((b for b in match.bots if target == expr.Entity(b.entity)
                and b.body == MECH and b.owner == pilot.entity and b.source_body == pilot.body
                and not b.dead and b.body_script is not None), None)
    if bot is None or pilot.controlled_mech is bot:
        return
    release_control(pilot)
    match.combat.zarya_gravitons.release(pilot)
    match.combat.zarya_gravitons.release(bot)
    bot.control_mover = Mover(bot.position, round(bot.yaw), match.collision_world,
                              defined_mover_data(MECH), category=pilot.mover.category)
    bot.control_mover.velocity = bot.velocity
    bot.impulse_mover = None
    bot.firing = False
    pilot.controlled_mech = bot


def release_control(pilot):
    bot, pilot.controlled_mech = pilot.controlled_mech, None
    if bot is not None:
        pilot.match.combat.knockbacks.retire_whole_hog(bot.entity, forget=False)
        pilot.match.combat.zarya_gravitons.release(bot)
        bot.control_mover = None
        bot.control_buttons = 0
        if bot.body_script is not None and bot.body_script.component.fault is None:
            component = bot.body_script.component
            component.edges([(button, False) for button in tuple(component.held)])
            component.shots.clear()
        bot.firing = not bot.dead
        fight = pilot.match.combat
        fight.expose_body(pilot.body, fight.tick or pilot.match.tick)


def queue_demech_return(match, bot):
    """Invoke the authored return send on a lethally hit controlled 0407."""
    pilot = match._message_player(bot.source_body)
    if pilot is None or pilot.controlled_mech is not bot or bot.body_script is None:
        return
    for instance in bot.body_script.component.instances.values():
        if instance.graph.index == 0x276:
            nodes.send(instance, instance.graph.node(5), None)
            return


def complete_return(pilot):
    parked_mover = pilot.mover
    bot = pilot.controlled_mech
    component = bot.body_script.component
    bomb = next((i.states.get(6) for i in component.instances.values() if i.graph.index == 0x0B2F), None)
    bot.detached_bomb = not bot.dead and bomb is not None and bomb.active
    if bot.detached_bomb:
        pilot.match.combat.zarya_gravitons.release(bot)
        pilot.match.combat.healing.retire(bot.entity)
        pilot.match.combat._clear_frost(bot)
        pilot.match.combat.barriers.retire(bot.entity)
        pilot.match.combat.clear_pins(bot.entity)
    ejected = bot.dead or bot.detached_bomb
    if ejected:
        # Reconstructed static ejection placement at the defeated mech's feet.
        # If the pilot capsule cannot stand here, keep its last safe pose.
        mover = Mover(bot.position, round(bot.yaw), pilot.match.collision_world,
                      mover_data(pilot.hero.body), category=pilot.mover.category)
        radius, segment = mover._capsule()
        if mover._engine_body().fits(radius, radius + segment):
            pilot.mover = mover
        if bot.detached_bomb and any(bot.velocity):
            bot.impulse_mover = Mover(bot.position, round(bot.yaw), pilot.match.collision_world,
                                      defined_mover_data(MECH), category=pilot.mover.category)
            bot.impulse_mover.apply_impulse(bot.velocity)
            bot.last_tick = pilot.match.tick
    release_control(pilot)

    # Stop Call Mech's retained arbitration scope at the reconstructed handoff.
    for instance in pilot.body_script.component.instances.values():
        if instance.graph.index != 0x26A:
            continue
        scope = instance.states.get(15)
        ability = instance.states.get(5)
        if scope is not None and scope.active and isinstance(ability, nodes.Ability):
            ability.stop()
            instance.component.run_now()
        break

    if ejected and pilot.mover is parked_mover:
        # Message delivery ends the parked scope before the next pilot command.
        match = pilot.match
        active = statescript_mods(pilot.body_script.component)
        parked_mover.track_mods(match.combat.movement_mods(pilot, active), match.tick)


def drive_mech(pilot, command, seconds):
    """Run one Match command on the existing owned VM after entry has completed."""
    match, bot = pilot.match, pilot.controlled_mech
    script, mover = bot.body_script, bot.control_mover
    if bot.dead and script is not None and any(
        pending[0] is script and pending[1] is pilot and pending[3] == RETURN_TO_PILOT
        for pending in match._body_messages
    ):
        match.body_command(pilot, Command(command.frame))
        return True
    if (bot.dead or script is None or script.component.fault is not None
            or mover is None or bot not in match.bots
            or pilot.body_script is None or match.combat.is_dead(pilot)):
        release_control(pilot)
        return False
    if command.frame <= script.frame:
        return True
    mover.use(world=match.collision_world)
    first_pose = mover.first_step_frame(command.frame)
    if script.frame + 1 < first_pose:
        # Contact has already projected the active Matrix's held window.
        # Keep that one button in the retained VM frames without posing them.
        matrix_buttons = 0
        if first_pose - script.frame > MAX_GAP:
            deflection.recover_matrix_close(script, first_pose * script.component.frame_ms - 1)
        elif bot.control_buttons & SECONDARY and deflection.matrix_active(script):
            matrix_buttons = SECONDARY
        matrix_release = script.frame + REPEAT + 1
        first = Command(script.frame + 1, buttons=matrix_buttons)
        _advance_mech(pilot, bot, first, seconds, match.tick, posed=False)
        # Match BodyScript's retained tail after the first release frame.
        last_unposed = first_pose - 1
        first_retained = max(script.frame + 1, last_unposed - MAX_GAP)
        if pilot.controlled_mech is bot and bot.body_script is script and first_retained > script.frame + 1:
            deflection.recover_matrix_recharge(script, first_retained * script.component.frame_ms)
        for frame in range(first_retained, first_pose):
            if pilot.controlled_mech is not bot:
                break
            buttons = matrix_buttons if frame < matrix_release else 0
            _advance_mech(pilot, bot, Command(frame, buttons=buttons), seconds, match.tick,
                          posed=False, endpoint=frame == last_unposed)
    mover.step(command, seconds, match.tick,
               simulate_pose=lambda frame, dt, tick: _advance_mech(pilot, bot, frame, dt, tick))
    return True


def _advance_mech(pilot, bot, command, seconds, tick, *, posed=True, endpoint=True):
    match = pilot.match
    script, mover = bot.body_script, bot.control_mover
    if pilot.controlled_mech is not bot or bot.dead or script is None or command.frame <= script.frame:
        return
    if endpoint:
        # The parked pilot already replays its own bounded gap between endpoints.
        neutral = Command(command.frame)
        match.body_command(pilot, neutral)
        if pilot.body_script is None or pilot.controlled_mech is not bot:
            return
        pilot.body_script.component.shots.clear()
        pilot.mover.last_command = neutral
    hooked = (match.combat.hooks.controlled(bot.entity)
              and match.combat.hooks.drive_allowed(bot.entity, bot) and not mover.external_impulse)
    stunned = match.combat.is_stunned(bot, tick=tick)
    pulled = bot.entity in match.combat.zarya_gravitons.pulled
    received = mover.last_command is command
    admitted, blocked = match.combat.suppress_input(bot, command, tick=tick, active=stunned)
    if received and admitted is not command:
        mover.last_command = admitted
    command = admitted
    if blocked:
        active = match.combat.movement_mods(bot, statescript_mods(script.component), tick=tick)
        mover.track_mods(active, command.frame - 1)
        if not pulled and not hooked:
            mover.velocity = (0.0, mover.velocity[1], 0.0)
    if posed:
        if not hooked and not (stunned and getattr(match.scene_host, 'step_hook_victim', lambda *_: False)(bot, command, seconds, tick)):
            mover.step_pose(command, seconds, tick, world_driven=pulled)
        bot.position = tuple(mover.position)
        bot.velocity = tuple(a + b for a, b in zip(mover.velocity,
            match.combat.knockbacks.whole_hog_velocity(bot.entity), strict=True))
        bot.yaw, bot.pitch = mover.yaw, mover.pitch
        bot.moved_tick = tick
    else:
        mover.throttles = (0, 0)
    edges = button_edges(bot.control_buttons, command.buttons, command.action)
    bot.control_buttons = command.buttons
    bot.clock_offset = command.frame - match.tick
    snapshot = script.advance_live(command.frame, bot.position, edges=edges, publish=endpoint)
    if bot.body_script is script:
        charge_ultimate(script.component, ULT_SECONDS, dva.self_destruct_running(script.component))
        match.combat.controlled_mech_command(bot, command, snapshot, blocked or not posed, pilot.quanta)
        active = match.combat.movement_mods(bot, statescript_mods(script.component), tick=tick)
        mover.track_mods(active, command.frame, posed=posed)
        mover.script_data = statescript_mover_data(script.component)
        mover.flight_scale = statescript_flight(script.component)


def accepts_initial_return(match, pilot, source):
    """Validate the sender against the active host binding or authored scope.

    The 026A receiver in the static host can end this scope for any sender.
    Bind the queued return to the actual 0407 named by the live scope before
    invoking that receiver.
    """
    if pilot.controlled_mech is not None:
        return pilot.controlled_mech.body_script is source
    component = pilot.body_script.component
    for instance in component.instances.values():
        if instance.graph.index != 0x26A or instance.stopped or instance.teardown:
            continue
        node = instance.graph.node(20)
        state = instance.states.get(node.state)
        if state is None or not state.active or state.ending:
            continue
        target = instance.evaluate(node.field("m_C98661CF"))
        return any(
            target == expr.Entity(bot.entity)
            and bot.body == MECH
            and bot.owner == pilot.entity
            and bot.source_body == component.entity
            and bot.body_script is source
            for bot in match.bots
        )
    return True


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
    from ow174.game.script.placement import _vector

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
    # Reconstructed B6 factory policy for0407's authored STUPetComponent.
    # Keep its field independent from direct component43 ownership.
    bot.pet_owner = owner.id
    bot.yaw = float(bots.heading(forward.x, forward.z))
    bot.retain_corpse = True  # ownership cleanup below; never the training-bot respawn policy.
    # A created body keeps its creating VM's frame domain. World ticks and
    # command frames can have different origins; changing only the frame later
    # would invalidate all the already-created absolute timers/state starts.
    bot.clock_offset = instance.component.frame-match.tick
    match.bots.append(bot)  # native ownership lookup must work during graph initialization.
    try:
        script = BotScript(MECH, DVA, entity, instance.component.frame, match.game_map.mode_guid,
                           health+armour+shields, match=match, firing_position=bot.position)
    except Exception:
        match.bots.remove(bot)  # no ID/health/create record has been published yet.
        raise
    bot.body_script = script
    bot.firing = True  # advance its graphs; no logical fire input or AI behavior is synthesized.
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


def retire_source(match, controller, source_body, *, keep_bombs=False):
    for bot in tuple(match.bots):
        if (bot.owner == controller and bot.source_body == source_body
                and not (keep_bombs and bot.detached_bomb)):
            npc.remove_body(match, bot)


def play_cleanup(match, instance, node):
    """Invoke the authored303B predecessor of the two creation actions."""
    if instance.graph.index != 0x26A or node.pos not in (211, 212):
        return NotImplemented
    resource = node.field("m_69F4B52C.m_6525E98B")
    if not isinstance(resource, dict) or resource.get("m_graph") != "0x058000000000303B":
        return NotImplemented
    definition = graph.graph(0x303B)
    if definition is None:
        return NotImplemented
    entity = expr.to_entity(instance.evaluate(node.field("m_targets")))
    bot = next((b for b in match.bots if b.entity == entity
                and b.source_body == instance.component.entity and b.owner), None)
    if bot is not None and bot.body_script is not None:
        pilot = match._message_player(bot.source_body)
        if pilot is not None and pilot.controlled_mech is bot:
            release_control(pilot)
        component = bot.body_script.component
        component.play_loaded_graph(definition, source=instance.component.entity, policy=0)
        component.run_now()
    return True


def primary_query(world, cfg, instance):
    """CD1940/DA7F10 relation policy: component44 first, then current-body registry.

    A created body's direct owner is not sufficient to make it possessed.
    Engine observation is supplied by the runtime's existing native polling.
    """
    if cfg.get("$") != "STU_DB742151" or world.match is None:
        return NotImplemented
    if instance is None:
        return expr.EVALUATION_FAILED
    from ow174.game.script import play

    match = world.match
    value = play.evaluate_scalar(instance, cfg.get("m_entity"), initial=expr.Entity(0))
    instance.component.ensure_valid()
    if (world.match is not match or instance.stopped or instance.teardown
            or instance.component.instances.get(instance.id) is not instance):
        instance.component.unsupported(instance, "primary possessor query retired context", cfg["$"])
    entity = expr.to_entity(value) & 0xFFFFFFFF
    for player in match.players:
        if not player.gone and (player.entity == entity or (player.has_body and player.body == entity)):
            return expr.Entity(player.entity)
    return expr.Entity(0)
