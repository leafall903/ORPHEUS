"""Manual1157 pin bridge; target context, contact and damage clock are reconstructed."""

import math

from ow174.game.script import expr

HERO, GRAPH = 0x02E00000000001BB, 0x1157
PIN_RANGE = 3.0  # manual contact policy, not recovered native acquisition distance


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
        world.match.combat.clear_pin(component.entity)
    component.edges([(36, False)])
    for instance in list(component.instances.values()):
        if instance.graph.index != GRAPH:
            continue
        for position in (23, 12):
            node = instance.graph.node(position)
            state = instance.states.get(node.state) if node is not None else None
            if state is not None:
                state.end(False)
    # Outgoing exits queue Anim31 BEGIN. Drain those events before aborting the
    # release scope for a fresh manual engagement. Otherwise its not-yet-active
    # state cannot be canceled and subsequently locks the new target forever.
    # This explicit inter-object ordering is a server reconstruction.
    component.run_now()
    for instance in list(component.instances.values()):
        if instance.graph.index != GRAPH:
            continue
        if target is not None:
            node = instance.graph.node(31)
            state = instance.states.get(node.state) if node is not None else None
            if state is not None:
                state.end(False)
        instance.write((expr.INSTANCE, 536), 0)
    component.run_now()


def bound_target(world, source_entity):
    match = world.match
    if match is None or world.manual_target is None:
        return None, None
    source = next((bot for bot in match.bots if bot.entity == source_entity), None)
    if source is None or source.attack_target is None:
        return None, None
    player = next((player for player in match.players if player.entity == source.attack_target), None)
    target = match.combat.player_target(player)
    if target is None or target.entity != world.manual_target:
        return None, None
    return player, target


def play_victim_effect(world, instance, node):
    if not scoped(world, instance) or node.pos != 45:
        return NotImplemented
    player, target = bound_target(world, instance.component.entity)
    if target is None:
        return NotImplemented

    from ow174.game import assassin_effect, dva

    if player.hero.guid != dva.HERO:
        return NotImplemented
    fight = world.match.combat
    host = fight.assassin_effects.get(player.entity)
    if host is None:
        # Reconstructed 000E controller profile. Health and movement are
        # stored on the independently possessed body in this match model.
        host = assassin_effect.VictimEffect(
            player.entity, fight.tick, health_absent=True, mover_sample=None,
        )
        from ow174.game.heroselect import controller_wire_ids

        reserved = controller_wire_ids(world.match.controller)
        host.component.next_id = max(max(reserved, default=0) + 1, player.controller_effect_next)
    if not host.submit(instance, node):
        return NotImplemented
    player.controller_effect_next = host.component.next_id
    fight.assassin_effects[player.entity] = host
    return True


def native(world, cfg, instance):
    if not scoped(world, instance):
        return NotImplemented
    entity = expr.to_entity(instance.evaluate(cfg.get('m_entity')))
    kind = cfg.get('$')
    if kind == 'STU_DB742151' and entity == instance.component.entity:
        # The authored selection names the player controller. The body
        # binding stays separate so a pending contact cannot follow ejection.
        player, _ = bound_target(world, instance.component.entity)
        return expr.Map(((1650, expr.Entity(player.entity) if player is not None else None),))
    if kind == 'STU_9B7648C3' and world.match is not None:
        if entity == instance.component.entity:
            # Manual source is not yet in match.bots during initialization.
            from ow174.game.script.driver import OWNER_HEALTH

            slot = instance.component.entity_var(OWNER_HEALTH, create=False)
            return expr.f32(slot.value()) if slot is not None else 0.0
        player, target = bound_target(world, instance.component.entity)
        if player is not None and entity == player.entity:
            return expr.f32(world.match.combat.vitals_of(target).total)
    return NotImplemented


def protected(fight, target):
    from ow174.game import defenses

    return (defenses.protection(target.owner) is not None
            or fight.zarya_bubbles.for_target(target.owner) is not None)


def release_victim(fight, entity):
    sources = {source for source, victim in fight.pins.items() if victim == entity}
    for bot in fight.match.bots:
        if bot.entity not in sources or bot.hero != HERO or bot.body_script is None:
            continue
        component = bot.body_script.component
        bind(component.world, component, None)
        bot.attack_input = False


def melee_rescue(fight, rescuer, source):
    if fight.match.game_map.free_for_all:
        return
    pinned = fight.pins.get(source.entity)
    if pinned is None:
        return
    controller = next((player for player in fight.match.players
                       if player.entity == source.owner.attack_target), None)
    target = fight.player_target(controller)
    if target is None or target.entity != pinned or target.team != rescuer.team:
        return
    interrupt_pin(fight, source)


def interrupt_pin(fight, source):
    if (source.entity not in fight.pins or source.owner.hero != HERO
            or source.owner.body_script is None):
        return
    component = source.owner.body_script.component
    for instance in component.instances.values():
        if instance.graph.index == GRAPH:
            # The authored release wait bounds the reconstructed interruption lockout.
            duration = expr.to_float(instance.evaluate(instance.graph.node(37).field('m_timeout')))
            from ow174.game.combat import TICK

            deadline = fight.tick + math.ceil(duration / TICK)
            fight.stuns[source.entity] = max(deadline, fight.stuns.get(source.entity, 0))
            fight._interrupt(source)
            return


def contact_sample(fight, source, target):
    """Use the same reconstructed contact geometry for selection and damage."""
    from ow174.game import combat

    x, y, z = source.position
    origin = x, y + combat.EYE, z
    x, y, z = target.feet
    delta = tuple(b - a for a, b in zip(origin, (x, y + target.eye, z), strict=True))
    distance = math.hypot(*delta)
    if distance > PIN_RANGE:
        return None
    a, b = target.segment()
    visible = combat.capsule_visible(fight.match.collision_world, origin, a, b, target.radius)
    return distance, delta, visible


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
    player, target = bound_target(world, source.entity)
    if (player is None or player.entity != entity or fight.vitals_of(target).dead
            or protected(fight, target)
            or (not match.game_map.free_for_all and target.team == source.team)):
        return None
    contact = contact_sample(fight, source, target)
    if contact is None or not contact[2]:
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
    fight.pin_target(source.entity, target)
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
        world.match.combat.clear_pin(state.instance.component.entity)


def refresh_control(world, component):
    if getattr(world, 'hero', None) != HERO or world.match is None:
        return
    if component.entity not in world.match.combat.pins:
        world.match.combat.clear_pin(component.entity)
        return
    for instance in component.instances.values():
        if instance.graph.index == GRAPH:
            node = instance.graph.node(23)
            pin = instance.states.get(node.state) if node is not None else None
            if pin is not None and pin.active:
                return
    world.match.combat.clear_pin(component.entity)
