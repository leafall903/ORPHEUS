"""Assassin pin bridge with reconstructed target context, contact and damage clock."""

import math

from ow174.game import phase_defenses
from ow174.game.script import expr

HERO, GRAPH = 0x02E00000000001BB, 0x1157
BODY, CONTROLLER_BODY = 0x0400000000001EBD, 0x0400000000001EBE
CONTROLLER_GRAPH = 0x1158
TARGET_GRAPH = 0x13C2
CONTROLLER_ANIMATION_MESSAGE = 0x02400000000007C8
PIN_RANGE = 3.0  # manual contact policy, not recovered native acquisition distance


def controller_animation_action(instance, node):
    return (instance.component.world.hero == HERO and instance.graph.index == CONTROLLER_GRAPH
            and expr.guid_of(node.field("m_gameMessage.m_gameMessage")) == CONTROLLER_ANIMATION_MESSAGE)


def scoped(world, instance):
    return (world.hero == HERO and world.controller_components is not None
            and instance is not None and instance.graph.index == GRAPH)


def release_animation_duration(state):
    instance = state.instance
    if not scoped(instance.component.world, instance) or state.node.pos != 31:
        return None
    alias = state.node.field('m_11E0A658.m_animAlias')
    category = state.node.field('m_22326315.m_category')
    if (alias, category) == ('0x0C80000000000561', '0x0280000000000082'):
        # 104319 07C3.021 slot 20 -> 0640.020 category branch 3 -> 508A.006.
        # Using the asset duration as the server completion clock is reconstructed.
        return 1.4666666984558105
    return None


def release_state_active(component, position):
    if component.world.hero != HERO:
        return False
    for instance in component.instances.values():
        if scoped(component.world, instance):
            state = instance.states.get(instance.graph.node(position).state)
            if state is not None and state.active:
                return True
    return False


def releasing(component):
    return release_state_active(component, 37)


def recovering(component):
    return release_state_active(component, 31)


def bind(world, component, target):
    """Retire the old pin before a stop, interruption or target/body replacement."""
    if (getattr(world, 'hero', None) != HERO or getattr(world, 'controller_components', None) is None
            or world.manual_target == target):
        return False
    world.manual_target = target
    return retire_pin(world, component)


def retire_pin(world, component):
    released = False
    if world.match is not None:
        world.match.combat.pins.pop(component.entity, None)
    for instance in list(component.instances.values()):
        if instance.graph.index != GRAPH:
            continue
        for position in (23, 12):
            node = instance.graph.node(position)
            state = instance.states.get(node.state) if node is not None else None
            if state is not None:
                released |= position == 23 and state.active
                state.end(False)
        instance.write((expr.INSTANCE, 536), 0)
    component.edges([(36, False)])
    return released


def native(world, cfg, instance):
    if world.hero == HERO and instance is not None and instance.graph.index == TARGET_GRAPH:
        if cfg.get("$") == "STU_AC829876":
            return controller_target_filter(world, cfg, instance)
        if controller_selector(cfg) and world.match is not None:
            bot = controlled_body(world, instance)
            return expr.Entity(bot.entity if bot is not None else 0)
    if world.hero == HERO and instance is not None and instance.graph.index == CONTROLLER_GRAPH:
        return controller_body_query(world, cfg, instance)
    if not scoped(world, instance):
        return NotImplemented
    kind = cfg.get('$')
    if kind not in ('STU_DB742151', 'STU_9B7648C3'):
        return NotImplemented
    entity = expr.to_entity(instance.evaluate(cfg.get('m_entity')))
    target_id = world.manual_target or autonomous_target(world, instance.component)
    if kind == 'STU_DB742151' and entity == instance.component.entity:
        # A data-only map preserves the authored #1650 lookup on the body.
        target = expr.Entity(target_id) if target_id else None
        return expr.Map(((1650, target),))
    if kind == 'STU_9B7648C3' and world.match is not None:
        if entity not in (instance.component.entity, target_id):
            return NotImplemented
        target = next((t for t in world.match.combat._targets() if t.entity == entity), None)
        return expr.f32(world.match.combat.vitals_of(target).total) if target is not None else 0.0
    return NotImplemented


def controller_body_query(world, cfg, instance):
    """Read the possessed Assassin's live position and health pools for 1158."""
    kind = cfg.get("$")
    if kind == "STU_AC829876":
        return controller_target_filter(world, cfg, instance)
    if kind == "STU_D379F515":
        return controller_reachability(world, cfg, instance)
    if controller_selector(cfg):
        if world.match is None:
            return NotImplemented
        bot = controlled_body(world, instance)
        return expr.Entity(bot.entity if bot is not None else 0)
    if kind not in ("STU_9B7648C3", "STU_60146072", "STU_71A87E92") or world.match is None:
        return NotImplemented
    selector = cfg.get("m_entity")
    if kind == "STU_71A87E92" and expr.dynamic(selector) == (expr.INSTANCE, 536):
        if controlled_body(world, instance) is not None:
            return world.position(expr.to_entity(instance.evaluate(selector)))
        return NotImplemented
    if not controller_selector(selector):
        return NotImplemented
    bot = controlled_body(world, instance)
    if bot is None:
        return NotImplemented
    if kind == "STU_71A87E92":
        return expr.Vec3(*bot.position)
    fight = world.match.combat
    target = next((target for target in fight._targets() if target.owner is bot), None)
    if target is None:
        return NotImplemented
    vitals = fight.vitals_of(target)
    # Authored health-ratio checks (e.g. 01CF node 51) use 60146072 as the denominator.
    return expr.f32(vitals.total if kind == "STU_9B7648C3" else sum(vitals.most.values()))


def controller_selector(cfg):
    if not isinstance(cfg, dict):
        return False
    kind = cfg.get("$")
    if kind not in ("STU_0AC15F78", "STU_00707E1E"):
        return False
    # CE7660 reads component 44 +D0 through BD4A90. The reconstructed
    # controller has a single body association for both native fields.
    owner = cfg.get("m_entity" if kind == "STU_0AC15F78" else "m_00903303")
    return isinstance(owner, dict) and owner.get("$") == "STU_B5A0CAF0"


def controller_target_filter(world, cfg, instance):
    """Reconstructed enemy eligibility, with the authored 1158 filter condition."""
    selector, rule = cfg.get("m_entity"), cfg.get("m_filter")
    field = expr.dynamic(selector)
    if (world.match is None or not isinstance(rule, dict) or rule.get("$") != "STUConfigVarFilter"):
        return NotImplemented
    flags = rule.get("m_84065FD1"), rule.get("m_4EA686DB")
    if instance.graph.index == TARGET_GRAPH:
        if field not in ((expr.ENTITY, 1650), (expr.ENTITY, 14365)) or flags != (262144, 304):
            return NotImplemented
    elif field != (expr.INSTANCE, 536) or flags not in ((1, 264), (262144, 316)):
        return NotImplemented
    _, target = controller_enemy(world, instance, selector)
    if target is not None and flags == (262144, 316):
        condition = rule.get("m_condition")
        if expr.dynamic(condition) == (expr.INSTANCE, 14653) and instance.evaluate(condition) is None:
            # Node 9's first true edge can run before its queued stack child 69.
            # Use the authored initializer for that first read.
            return expr.truthy(instance.evaluate(instance.graph.node(69).field("m_value")))
    return target is not None and candidate_condition(instance, rule, target)


def candidate_condition(instance, rule, target):
    config = rule.get("m_condition")
    if config is None:
        return True
    with instance.component.parameter_scope({8: expr.Entity(target.entity)}):
        return expr.truthy(instance.evaluate(config))


def target_candidates(state):
    """Reconstruct 13C2 search from live enemies, authored radius and predicate."""
    instance = state.instance
    world = instance.component.world
    bot = controlled_body(world, instance)
    if bot is None:
        return ()
    radius = expr.to_float(state.evaluate(state.node.field("m_radius"), watch=False))
    rule = state.node.field("m_filter")
    return tuple(expr.Entity(target.entity) for target in world.match.combat._enemies(bot)
                 if math.dist(bot.position, target.feet) <= radius and candidate_condition(instance, rule, target))


def advance_controller(controller, frame):
    attack = next((i for i in controller.instances.values() if i.graph.index == CONTROLLER_GRAPH), None)
    previous = expr.to_entity(attack.context().var(expr.INSTANCE, 536)) if attack is not None else 0
    controller.run_frame(frame)
    for instance in list(controller.instances.values()):
        index = instance.graph.index
        if index not in (TARGET_GRAPH, CONTROLLER_GRAPH):
            continue
        state = instance.states.get(instance.graph.node(1).state)
        if state is None or not state.active:
            continue
        if index == TARGET_GRAPH:
            tracker = instance.state(instance.graph.node(4).state)
            if not tracker.active and controlled_body(controller.world, instance) is not None:
                instance.follow(state.node, "m_6F92F4BD", state)
                controller.run_now()
        else:
            slot = controller.entity_var(1650, create=False)
            target = expr.to_entity(slot.value()) if slot is not None else 0
            receiver = controller_message_receiver(controller.world, instance)
            changed_target = previous and target != previous
            latch = instance.states.get(instance.graph.node(12).state)
            # The body drains a newly queued press after the controller tick.
            # Only an older latch can represent a rejected or released input.
            stale_latch = (
                target and receiver is not None and receiver.world.manual_target is None
                and latch is not None and latch.active and latch.start // controller.frame_ms < frame
                and latch.target_entity in (None, target) and 36 not in receiver.held
                and not recovering(receiver) and controller_contact(instance, target)
            )
            if changed_target or stale_latch:
                root = instance.states.get(instance.graph.node(7).state)
                if root is not None and root.active:
                    root.end(False)
                instance.write((expr.INSTANCE, 59), None)
                if (receiver is not None and receiver.world.manual_target is None
                        and receiver.entity in controller.world.match.combat.pins):
                    retire_pin(receiver.world, receiver)
                # The body drains the release and recovery begin events after
                # this controller update, before admitting another attack.
                continue
            phase = instance.context().var(expr.INSTANCE, 59)
            if target and phase is None and receiver is not None and not recovering(receiver):
                instance.follow(state.node, "m_6F92F4BD", state)
                controller.run_now()


def controller_reachability(world, cfg, instance):
    selector, owner = cfg.get("m_target"), cfg.get("m_7826B991")
    if (world.match is None or expr.dynamic(selector) != (expr.INSTANCE, 536)
            or not isinstance(owner, dict) or owner.get("$") != "STU_B5A0CAF0"):
        return NotImplemented
    bot, target = controller_enemy(world, instance, selector)
    if target is None or world.match.collision_world is None:
        return False
    from ow174.game.npc_context import controller_line_clear

    return controller_line_clear(world.match.combat, bot, target)


def pursue(fight, bot, tick, seconds, collision):
    """Reconstruct direct grounded travel for the authored 1158 approach branches."""
    from ow174.game.npc_context import controller_line_clear

    if bot.hero != HERO or bot.body_script is None or bot.route:
        return False
    script = bot.body_script
    controller = script.controller
    if (controller is None or controller.owner_dead or script.component.world.manual_target is not None):
        return False
    instance = next((i for i in controller.instances.values() if i.graph.index == CONTROLLER_GRAPH), None)
    if instance is None:
        return False
    if bot.last_tick is not None and bot.last_tick >= tick:
        return True
    bot.last_tick = tick
    target_id = expr.to_entity(instance.context().var(expr.INSTANCE, 536))
    target = next((t for t in fight._enemies(bot) if t.entity == target_id), None)
    threshold = expr.to_float(instance.context().var(expr.INSTANCE, 12018))
    phase = instance.context().var(expr.INSTANCE, 59)
    approach = any(state.active and state.node.pos in (42, 102) for state in instance.states.values())
    if (not approach or phase != 1 or target is None or threshold <= 0 or collision is None
            or fight.is_stunned(bot, tick=tick) or not controller_line_clear(fight, bot, target)):
        if bot.target is not None:
            bot._pause(tick)
            bot._stand()
        return True
    x, y, z = bot.position
    tx, ty, tz = target.feet
    dx, dz = tx - x, tz - z
    distance = math.hypot(dx, dz)
    if math.dist(bot.position, target.feet) <= threshold or distance <= 0.05:
        if bot.target is not None:
            bot._pause(tick)
            bot._stand()
        return True
    step = min(distance, bot.speed * seconds)
    nx, nz = x + dx * step / distance, z + dz * step / distance
    if collision.ground(nx, nz, y + 0.5, y - 0.5) is None:
        bot._pause(tick)
        bot._stand()
        return True
    bot.start = bot.position
    bot.target = (tx, ty, tz)
    bot._walk(tick, seconds, collision)
    bot.moved_tick = tick
    return True


def controller_enemy(world, instance, selector):
    bot = controlled_body(world, instance)
    entity = expr.to_entity(instance.evaluate(selector))
    if bot is None or not expr.valid_entity(entity):
        return bot, None
    return bot, next((target for target in world.match.combat._enemies(bot) if target.entity == entity), None)


def controlled_body(world, instance):
    if world.match is None or instance.component.owner_dead:
        return None
    controller = instance.component.entity
    return next((bot for bot in world.match.bots if bot.hero == HERO and not bot.dead
                 and bot.spawner.get("controller_entity") == controller), None)


def controller_message_receiver(world, instance):
    bot = controlled_body(world, instance)
    script = bot.body_script if bot is not None else None
    if script is None or script.controller is not instance.component:
        return None
    receiver = script.component
    if receiver.entity != bot.entity or receiver.owner_dead:
        return None
    return receiver


def controller_contact(instance, entity):
    """Check whether the controlled body can start a fresh autonomous pin."""
    world = instance.component.world
    bot = controlled_body(world, instance)
    if bot is None or world.match.collision_world is None:
        return False
    fight = world.match.combat
    if fight.is_stunned(bot) or fight.frozen(bot):
        return False
    target = next((target for target in fight._enemies(bot) if target.entity == entity), None)
    return (target is not None and getattr(target.owner, 'client', None) is not None
            and not target.owner.gone and contact_clear(fight, bot, target))


def controller_ground_state(world, instance):
    """Reconstruct NPC contact from its authoritative feet and static floor."""
    if world.match is None or world.match.collision_world is None:
        return NotImplemented
    bot = controlled_body(world, instance)
    if bot is None:
        return False, False
    x, y, z = bot.position
    # The NPC walker snaps its feet to floor height and has no native mover flags.
    floor = world.match.collision_world.ground(x, z, y + 0.001, y - 0.001)
    grounded = floor is not None
    return not grounded, grounded


def autonomous_target(world, component):
    controller = world.controller
    if controller is None or controller.owner_dead:
        return None
    for instance in controller.instances.values():
        if instance.graph.index == CONTROLLER_GRAPH:
            state = instance.states.get(instance.graph.node(12).state)
            if state is not None and state.active and state.receiver_entity == component.entity:
                selected = expr.to_entity(instance.context().var(expr.INSTANCE, 536))
                if selected == state.target_entity:
                    return selected
    return None


def victim(world, state):
    match = world.match
    if match is None or match.ended:
        return None
    fight = match.combat
    source = next((b for b in match.bots if b.entity == state.instance.component.entity), None)
    if (source is None or source.dead or fight.is_stunned(source) or fight.frozen(source)
            or source.body_script is None or source.body_script.component is not state.instance.component):
        return None
    if world.manual_target is not None:
        if not source.firing:
            return None
        selected = world.manual_target
    else:
        selected = autonomous_target(world, state.instance.component)
    entity = expr.to_entity(state.evaluate(state.node.field('m_FA3ECC43'), watch=False))
    if not selected or entity != selected:
        return None
    target = next((t for t in fight._enemies(source) if t.entity == entity), None)
    if (target is None or getattr(target.owner, 'client', None) is None or target.owner.gone
            or (world.manual_target is not None and target.owner.entity != source.attack_target)):
        return None
    if not contact_clear(fight, source, target):
        return None
    return source, target


def contact_clear(fight, source, target):
    from ow174.game import combat, mei

    origin = (source.position[0], source.position[1] + combat.EYE, source.position[2])
    point = fight._eye(target.owner)
    a, b = target.segment()
    return (math.dist(origin, point) <= PIN_RANGE and not mei.protected(target.owner)
            and not fight.shields.shielded(target.owner) and not fight.walls.blocked(origin, point)
            and combat.capsule_visible(fight.match.collision_world, origin, a, b, target.radius))


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
    settings = state.node.field('m_modifyHealth') or {}
    amount = expr.to_float(state.evaluate(settings.get('m_amount'), watch=False))
    mode = expr.to_int(state.evaluate(state.node.field('m_95F1FB0B'), watch=False))
    # Reconstructed state clock: mode1 is damage/second; default mode applies
    # once on entry. Original1157 supplies amounts, delay and scope lifetime.
    damage = amount * elapsed_ms / 1000.0 if mode == 1 else (amount if elapsed_ms == 0 else 0.0)
    if math.isfinite(damage) and damage > 0:
        fight._damage(source, target, expr.f32(damage), False, None, fight.tick)
    return True


def validate_binding(world, component):
    """Retire invalid contact before VM ticks for both input sources."""
    if getattr(world, 'hero', None) != HERO or world.match is None:
        return
    fight = world.match.combat
    source = next((b for b in world.match.bots if b.entity == component.entity), None)
    if world.manual_target is None:
        pinned = fight.pins.get(component.entity)
        if pinned is not None:
            target = next((t for t in fight._enemies(source) if t.entity == pinned), None) if source else None
            if (target is None or source.dead or fight.is_stunned(source) or fight.frozen(source)
                    or autonomous_target(world, component) != pinned or not contact_clear(fight, source, target)):
                retire_pin(world, component)
        return
    target = next((t for t in fight._targets() if t.entity == world.manual_target), None)

    valid = (source is not None and target is not None and not source.dead and source.firing
             and not fight.is_stunned(source) and not fight.frozen(source)
             and not fight.vitals_of(target).dead
             and not phase_defenses.active(target.owner)
             and target.owner.entity == source.attack_target
             and getattr(target.owner, 'client', None) is not None and not target.owner.gone
             and (world.match.game_map.free_for_all or source.team != target.team))
    if not valid or not contact_clear(fight, source, target):
        bind(world, component, None)


def end_health(world, state, *, force=False):
    if not scoped(world, state.instance) or world.match is None:
        return
    node = state.instance.graph.node(23)
    pin = state.instance.states.get(node.state) if node is not None else None
    if force or pin is None or not pin.active or pin.ending:
        world.match.combat.pins.pop(state.instance.component.entity, None)


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
    world.match.combat.pins.pop(component.entity, None)
