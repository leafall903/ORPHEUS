"""Manual1157 pin bridge; target context, contact and damage clock are reconstructed."""

import math
from dataclasses import dataclass

from ow174.game.script import expr, graph, runtime

HERO, GRAPH = 0x02E00000000001BB, 0x1157
LUNGE_GRAPH = 0x13C3
LUNGE_REQUEST = 0x02400000000009C6
LUNGE_ATTEMPT_SECONDS = 1.5
LUNGE_RETRY_SECONDS = 2.0  # reconstructed recovery before another automatic request


@dataclass
class Lunge:
    target: int
    finish_at: int
    expires: int
    launched: bool = False
VICTIM_GRAPH, PIN_MESSAGE = 0x1177, 0x02400000000008FE
PIN_RANGE = 3.0  # reconstructed contact reach
ACQUIRE_RANGE = 40.0  # 13C3/53 target search radius used for sandbox pursuit


def stop_approach(bot, tick):
    if bot.displacement is None and bot.held != (0, 0):
        bot.velocity = (0.0, 0.0, 0.0)
        bot.held = (0, 0)
        bot.moved_tick = tick


def approach(fight, bot, player, distance):
    """Walk toward a valid pin point, using a grid route when the straight path is blocked."""
    from ow174.game import combat, npc_navigation
    from ow174.game.mover import mover_data

    match = fight.match
    world = match.collision_world
    target = next((row for row in fight._targets() if row.entity == player.body), None)
    if world is None or target is None:
        return False
    data = mover_data(bot.body)

    def lane_clear(start, end):
        return npc_navigation.teammate_clear(match, bot, start, end, data)

    if (distance <= PIN_RANGE
            and npc_navigation.pin_contact(world, bot.position, target, data, PIN_RANGE)
            and lane_clear(bot.position, target.feet)):
        bot.pursuit = None
        return True
    if bot.displacement is not None:
        bot.pursuit = None
        return False
    if fight.junkrat.rooted(bot.entity, fight.tick) or bot.body_script.frame >= fight.tick:
        return False

    nav = bot.pursuit
    if (nav is not None and (nav.world is not world or nav.search.target.entity != target.entity
                             or math.dist(nav.search.target.feet, target.feet) > 1)):
        bot.pursuit = nav = None
    if nav is None and distance > PIN_RANGE:
        if _approach_step(bot, world, target.feet, data, lane_clear, fight.tick):
            return False
    if nav is None or (nav.search.done and not nav.path and fight.tick >= nav.next_plan):
        bot.pursuit = nav = npc_navigation.Pursuit(
            world, npc_navigation.begin(bot.position, target, PIN_RANGE)
        )
    if not nav.search.done:
        npc_navigation.advance(nav.search, world, data, PIN_RANGE, npc_navigation.PLAN_SLICE, lane_clear)
        if nav.search.done:
            nav.path = nav.search.path
            nav.next_plan = fight.tick + npc_navigation.PLAN_INTERVAL
    while nav.path and math.dist(bot.position, nav.path[0]) < .02:
        nav.path = nav.path[1:]
    if nav.path and not _approach_step(bot, world, nav.path[0], data, lane_clear, fight.tick):
        nav.path = ()
        nav.next_plan = fight.tick + npc_navigation.PLAN_INTERVAL
    return False


def _approach_step(bot, world, destination, data, lane_clear, tick):
    from ow174.game import bots, combat, npc_navigation

    x, _, z = bot.position
    dx, dz = destination[0] - x, destination[2] - z
    length = math.hypot(dx, dz)
    if length <= combat.EPSILON:
        return False
    dx, dz = dx / length, dz / length
    stride = min(length, data.run_forward * combat.TICK)
    end = npc_navigation.edge(world, bot.position, x + dx * stride, z + dz * stride, data)
    if end is None or not lane_clear(bot.position, end):
        return False
    bot.velocity = tuple((b - a) / combat.TICK for a, b in zip(bot.position, end, strict=True))
    bot.position = end
    bot.yaw = float(bots.heading(dx, dz))
    bot.held = bot.last_held = bots.throttles(bot.yaw, (dx, dz))
    bot.input_tick = tick
    return True


def scoped(world, instance):
    return (world.hero == HERO and world.controller_components is not None
            and instance is not None and instance.graph.index == GRAPH)


def bind(world, component, target):
    """Retire the old pin before a stop, interruption or target/body replacement."""
    if (getattr(world, 'hero', None) != HERO or getattr(world, 'controller_components', None) is None
            or world.manual_target == target):
        return
    world.manual_target = target
    if world.match is not None:
        world.match.combat.release_pin(component.entity)
    for instance in list(component.instances.values()):
        if instance.graph.index != GRAPH:
            continue
        # The release Anim31 has no resolved asset duration in this runtime.
        # A new explicit manual engagement interrupts it through authored exits.
        positions = (23, 12, 31) if target is not None else (23, 12)
        for position in positions:
            node = instance.graph.node(position)
            state = instance.states.get(node.state) if node is not None else None
            if state is not None:
                state.end(False)
        instance.write((expr.INSTANCE, 536), 0)
    component.edges([(36, False)])


def lunge_source(instance):
    world = instance.component.world
    if (instance.graph.index != LUNGE_GRAPH or world.hero != HERO
            or getattr(world, "controller_components", None) is None or world.match is None):
        return None
    return next((bot for bot in world.match.bots
                 if bot.entity == instance.component.entity and not bot.dead
                 and bot.body_script is not None and bot.body_script.component is instance.component), None)


def _lunge_graph(bot):
    return next((i for i in bot.body_script.component.instances.values()
                 if i.graph.index == LUNGE_GRAPH and not i.stopped), None)


def _lunge_target(fight, bot, body, radius):
    from ow174.game import combat

    target = next((row for row in fight._enemies(bot) if row.entity == body
                   and getattr(row.owner, "client", None) is not None), None)
    if target is None or fight.barriers.attached_enemy(bot, body) is not None:
        return None
    origin = (bot.position[0], bot.position[1] + combat.EYE, bot.position[2])
    a, b = target.segment()
    if (math.dist(bot.position, target.feet) > radius
            or not combat.capsule_visible(fight.match.collision_world, origin, a, b, target.radius)):
        return None
    return target


def cancel_lunge(bot):
    attempt, bot.lunge = bot.lunge, None
    if attempt is None or bot.body_script is None:
        return
    instance = _lunge_graph(bot)
    if instance is not None:
        instance.write((expr.INSTANCE, 14479), ())
        positions = (105, 108, 155, 156, 157)
        indexes = [instance.graph.node(pos).state for pos in positions]
        for index in indexes:
            state = instance.states.get(index)
            if state is not None and state.active:
                state.end(False)
        instance.drop_begins([instance.states[index] for index in indexes if index in instance.states])
    if attempt.launched and bot.displacement is not None:
        _, vertical, _ = bot.displacement.velocity
        bot.displacement.velocity = (0.0, vertical, 0.0)
        bot.velocity = bot.displacement.velocity


def lunge_windup(fight, bot, player, distance):
    """Run authored launch admission while delaying ordinary approach during windup."""
    from ow174.game import combat, npc_navigation
    from ow174.game.mover import mover_data

    attempt = bot.lunge
    if attempt is None and (distance <= PIN_RANGE or fight.tick < bot.lunge_ready
                            or bot.displacement is not None):
        return False
    instance = _lunge_graph(bot)
    if (instance is None or fight.match.collision_world is None
            or fight.junkrat.rooted(bot.entity, fight.tick)):
        cancel_lunge(bot)
        return False
    radius = expr.to_float(instance.evaluate(instance.graph.node(87).field("m_radius")))
    target = _lunge_target(fight, bot, player.body, radius)
    if attempt is not None:
        if target is None or attempt.target != player.body or fight.tick >= attempt.expires:
            cancel_lunge(bot)
            return False
        if attempt.launched:
            if bot.displacement is None:
                bot.lunge = None
            return False
        animation = instance.states.get(instance.graph.node(105).state)
        if fight.tick >= attempt.finish_at and animation is not None and animation.active:
            animation.request_finish()
        return True
    if target is None:
        return False
    data = mover_data(bot.body)
    world = fight.match.collision_world
    end = npc_navigation.edge(world, bot.position, target.feet[0], target.feet[2], data)
    if (end is None or abs(end[1] - target.feet[1]) > data.step_down
            or not npc_navigation.teammate_clear(fight.match, bot, bot.position, end, data)):
        return False
    windup = expr.to_float(instance.evaluate(instance.graph.node(108).field("m_timeout")))
    bot.lunge = Lunge(player.body, fight.tick + math.ceil(windup / combat.TICK),
                      fight.tick + math.ceil(LUNGE_ATTEMPT_SECONDS / combat.TICK))
    bot.lunge_ready = fight.tick + math.ceil(LUNGE_RETRY_SECONDS / combat.TICK)
    if not runtime.send_instance_message(instance, LUNGE_REQUEST, {}, bot.entity):
        bot.lunge = None
        return False
    bot.pursuit = None
    return True


def track_lunge(world, state):
    if state.node.pos != 87:
        return NotImplemented
    bot = lunge_source(state.instance)
    if bot is None or bot.lunge is None:
        return NotImplemented
    radius = expr.to_float(state.evaluate(state.node.field("m_radius"), watch=False))
    target = _lunge_target(world.match.combat, bot, bot.lunge.target, radius)
    targets = (expr.Entity(target.entity),) if target is not None else ()
    # Preserve the selection for 115 after the transient tracker scope ends.
    state.instance.write((expr.INSTANCE, 14479), targets)
    return targets


def lunge_velocity(instance, node):
    """Compute the scoped 13C3 launch vector using its explicit-time configuration."""
    from ow174.game.mover import WORLD_GRAVITY, mover_data

    bot = lunge_source(instance) if node.pos == 13 else None
    if bot is None:
        return NotImplemented
    origin = instance.evaluate(node.field("m_B4FC39B6"))
    target = instance.evaluate(node.field("m_FCC616FC"))
    duration = expr.to_float(instance.evaluate(node.field("m_CBC2605E.m_B3EC7449")))
    output = expr.lvalue(node.field("m_2624BE5A"))
    if (not isinstance(origin, expr.Vec3) or not isinstance(target, expr.Vec3)
            or not math.isfinite(duration) or duration <= 0):
        instance.write(output, None)
        return True
    # Use the mover's gravity for the explicit-time branch. Its optional extra
    # vector input and exact native gravity sampling remain outside this scope.
    gravity = WORLD_GRAVITY * mover_data(bot.body).gravity_scale
    velocity = expr.Vec3(expr.f32((target.x - origin.x) / duration),
                         expr.f32((target.y - origin.y) / duration + .5 * gravity * duration),
                         expr.f32((target.z - origin.z) / duration))
    instance.write(output, velocity if all(math.isfinite(axis) for axis in velocity) else None)
    return True


def apply_lunge_velocity(instance, node):
    """Replace the launch velocity on the existing collision-driven bot mover."""
    bot = lunge_source(instance) if node.pos == 19 else None
    if bot is None:
        return NotImplemented
    fight = instance.component.world.match.combat
    world = fight.match.collision_world
    if world is None or fight.is_stunned(bot) or fight.junkrat.rooted(bot.entity, fight.tick):
        return True
    rows = node.field("m_BC5E91CF") or ()
    if len(rows) != 1 or rows[0].get("$") != "STU_95A10547" or rows[0].get("m_CFEC5F76") != 1:
        return NotImplemented
    velocity = instance.evaluate(rows[0].get("m_9B7A63EA"))
    if isinstance(velocity, expr.Vec3) and all(math.isfinite(axis) for axis in velocity):
        previous = bot.displacement.velocity if bot.displacement is not None else bot.velocity
        bot.add_impulse(tuple(new - old for old, new in zip(previous, velocity, strict=True)),
                        world, tick=fight.tick)
        bot.pursuit = None
        if bot.lunge is not None:
            bot.lunge.launched = True
            instance.write((expr.INSTANCE, 14479), ())
    return True


def native(world, cfg, instance):
    if instance is not None and instance.graph.index == LUNGE_GRAPH:
        bot = lunge_source(instance)
        kind = cfg.get("$")
        if (bot is not None and bot.lunge is not None
                and kind in ("STU_DB742151", "STU_71A87E92", "STU_60146072")):
            entity = expr.to_entity(instance.evaluate(cfg.get("m_entity")))
            if entity == bot.entity:
                if kind == "STU_DB742151":
                    return expr.Map(((1650, expr.Entity(bot.lunge.target)),))
                if kind == "STU_71A87E92":
                    return expr.Vec3(*bot.position)
                if kind == "STU_60146072":
                    return world.match.combat.vitals[bot.entity].maximum
            if kind == "STU_71A87E92" and entity == bot.lunge.target:
                target = next((row for row in world.match.combat._targets() if row.entity == entity), None)
                if target is not None:
                    return expr.Vec3(*target.feet)
    if not scoped(world, instance):
        return NotImplemented
    entity = expr.to_entity(instance.evaluate(cfg.get('m_entity')))
    if cfg.get('$') == 'STU_DB742151' and entity == instance.component.entity:
        # Explicit data-only possessor context; no synthetic controller entity is
        # sent to the client or aliased to an existing body's namespace.
        target = expr.Entity(world.manual_target) if world.manual_target is not None else None
        return expr.Map(((1650, target),))
    if cfg.get('$') == 'STU_9B7648C3' and world.match is not None:
        if entity not in (instance.component.entity, world.manual_target):
            return NotImplemented
        target = next((t for t in world.match.combat._targets() if t.entity == entity), None)
        return expr.f32(world.match.combat.vitals_of(target).total) if target is not None else 0.0
    return NotImplemented


def attach_victim(source, target):
    """Use the authored 1157/45 graph and 1157/18 message for the active pin."""
    script = getattr(target.owner, 'body_script', None)
    definition = graph.graph(VICTIM_GRAPH) if script is not None else None
    if definition is None or script.component.entity != target.entity:
        return
    component = script.component
    victim_script = component.play_loaded_graph(definition, source=source, instigator=source, policy=1)
    component.run_now()
    if runtime.send_instance_message(victim_script, PIN_MESSAGE, {}, source):
        # A confirmed manual pin starts immediately. The receiver's waiting
        # branch otherwise aborts its release animation into the graph's
        # self-stop on the first 08FE, before the pin animation can start.
        release = victim_script.node_state(definition.node(4))
        if release is not None and release.active:
            victim_script.exit_subgraph(release.node, 'm_subgraphPlug')
            victim_script.teardown = True
            try:
                release.end(False)
            finally:
                victim_script.teardown = False
        component.run_now()
    else:
        component.destroy(victim_script)


def detach_victim(match, source, body):
    for player in match.players:
        script = player.body_script
        if script is None or script.component.entity != body:
            continue
        component = script.component
        for instance in list(component.instances.values()):
            if instance.graph.index == VICTIM_GRAPH and instance.source_entity() == source:
                component.destroy(instance)
        break


def victim(world, state):
    match = world.match
    if match is None or match.ended:
        return None
    fight = match.combat
    source = next((b for b in match.bots if b.entity == state.instance.component.entity), None)
    if (source is None or source.dead or not source.firing or fight.is_stunned(source)
            or source.body_script is None or source.body_script.component is not state.instance.component):
        return None
    entity = expr.to_entity(state.evaluate(state.node.field('m_FA3ECC43'), watch=False))
    if entity != world.manual_target:
        return None
    target = next((t for t in fight._enemies(source) if t.entity == entity), None)
    if (target is None or getattr(target.owner, 'client', None) is None or target.owner.gone
            or target.owner.entity != source.attack_target):
        return None
    from ow174.game import combat

    origin = (source.position[0], source.position[1] + combat.EYE, source.position[2])
    point = fight._eye(target.owner)
    distance = math.dist(origin, point)
    a, b = target.segment()
    if distance > PIN_RANGE or not combat.capsule_visible(match.collision_world, origin, a, b, target.radius):
        return None
    return source, target


def modify_health(world, state, elapsed_ms):
    if not scoped(world, state.instance) or state.node.pos not in (15, 25):
        return NotImplemented
    pair = victim(world, state)
    if pair is None:
        end_health(world, state, force=True)
        return True
    source, target = pair
    fight = world.match.combat
    if fight.pins.get(source.entity) != target.entity:
        fight.pins[source.entity] = target.entity
        fight._interrupt(target)
        attach_victim(source.entity, target)
    settings = state.node.field('m_modifyHealth') or {}
    amount = expr.to_float(state.evaluate(settings.get('m_amount'), watch=False))
    mode = expr.to_int(state.evaluate(state.node.field('m_95F1FB0B'), watch=False))
    # Reconstructed state clock: mode1 is damage/second; default mode applies
    # once on entry. Original1157 supplies amounts, delay and scope lifetime.
    damage = amount * elapsed_ms / 1000.0 if mode == 1 else (amount if elapsed_ms == 0 else 0.0)
    if math.isfinite(damage) and damage > 0:
        fight._damage(source, target, expr.f32(damage), False, None, fight.tick)
    return True


def end_health(world, state, *, force=False):
    if not scoped(world, state.instance) or world.match is None:
        return
    node = state.instance.graph.node(23)
    pin = state.instance.states.get(node.state) if node is not None else None
    if force or pin is None or not pin.active or pin.ending:
        world.match.combat.release_pin(state.instance.component.entity)


def rescue_with_melee(fight, rescuer, target):
    """A damaging quick melee by the victim's teammate interrupts an Assassin pin."""
    from ow174.game import bots, combat

    actor = target.owner
    victim = fight.pins.get(target.entity)
    if (victim is None or fight.match.game_map.free_for_all
            or not isinstance(actor, bots.Bot) or actor.hero != HERO or actor.dead
            or actor.body_script is None or actor.spawner.get("manual_spawn") is not True):
        return
    if not any(player.body == victim and player is not rescuer and player.team == rescuer.team
               and not player.gone for player in fight.match.players):
        return
    component = actor.body_script.component
    instance = next((i for i in component.instances.values() if i.graph.index == GRAPH), None)
    recovery = instance.graph.node(37) if instance is not None else None
    if recovery is None:
        return
    duration = expr.to_float(instance.evaluate(recovery.field("m_timeout")))
    if not math.isfinite(duration) or duration <= 0:
        return
    deadline = fight.tick + math.ceil(duration / combat.TICK)
    fight.stuns[target.entity] = max(deadline, fight.stuns.get(target.entity, 0))
    cancel_lunge(actor)
    stop_approach(actor, fight.tick)
    bind(component.world, component, None)
    fight.release_pin(actor.entity)
    fight._interrupt(target)


def refresh_control(world, component):
    if (getattr(world, 'hero', None) != HERO or world.match is None
            or component.entity not in world.match.combat.pins):
        return
    for instance in component.instances.values():
        if instance.graph.index == GRAPH:
            node = instance.graph.node(23)
            pin = instance.states.get(node.state) if node is not None else None
            if pin is not None and pin.active:
                return
    world.match.combat.release_pin(component.entity)
