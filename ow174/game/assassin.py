"""Manual contact policy for the authored Assassin pin graph."""

import math

from ow174.game.script import expr, runtime

HERO, GRAPH, BUTTON = 0x02E00000000001BB, 0x1157, 36
PIN_RANGE = 3.0


def manual(world):
    return world.hero == HERO and world.controller_components is not None


def scoped(world, instance):
    return manual(world) and instance is not None and instance.graph.index == GRAPH


def native(world, cfg, instance):
    if cfg.get('$') != 'STU_DB742151' or not scoped(world, instance):
        return NotImplemented
    if expr.to_entity(instance.evaluate(cfg.get('m_entity'))) != instance.component.entity:
        return NotImplemented
    target = expr.Entity(world.manual_target) if world.manual_target is not None else None
    return expr.Map(((1650, target),))


def contact(fight, source, entity):
    if (source.dead or source.entity in fight.sombra_controls.locks
            or fight.is_asleep(source) or fight.is_stunned(source)):
        return None
    target = next((t for t in fight._enemies(source) if t.entity == entity), None)
    if target is None or getattr(target.owner, 'client', None) is None or target.owner.gone:
        return None
    from ow174.game import combat

    x, y, z = source.position
    origin = x, y + combat.EYE, z
    a, b = target.segment()
    if (math.dist(origin, fight._eye(target.owner)) <= PIN_RANGE
            and combat.capsule_visible(fight.match.collision_world, origin, a, b, target.radius)):
        return target
    return None


def bind(component, entity):
    world = component.world
    if world.manual_target == entity:
        return
    world.manual_target = entity
    world.match.combat.pins.pop(component.entity, None)
    for instance in list(component.instances.values()):
        if instance.graph.index != GRAPH:
            continue
        # A fresh manual engagement may interrupt release Anim31.
        for position in ((23, 12, 31) if entity is not None else (23, 12)):
            node = instance.graph.node(position)
            state = instance.states.get(node.state) if node is not None else None
            if state is not None:
                state.end(False)
        instance.write((expr.INSTANCE, 536), 0)
    component.edges([(BUTTON, False)])


def engage(match, bot, player):
    """Start a manual attack against the current body of an explicit opponent."""
    if (match.ended or bot not in match.bots or bot.body_script is None
            or not manual(bot.body_script.component.world) or player not in match.players
            or not player.has_body or not player.spawned
            or contact(match.combat, bot, player.body) is None):
        return False
    component = bot.body_script.component
    if component.world.manual_target != player.body:
        bind(component, player.body)
        component.edges([(BUTTON, True)])
    bot.firing = True
    return True


def acquire(fight, bot, player):
    """Let the authored release wait finish before automatic re-engagement."""
    component = bot.body_script.component
    target = player.body if player is not None else None
    current = component.world.manual_target
    if current is not None and current != target:
        stop(bot)
        return
    if player is None:
        return
    if current is None:
        for instance in component.instances.values():
            if instance.graph.index == GRAPH:
                node = instance.graph.node(37)
                recovery = instance.states.get(node.state) if node is not None else None
                if recovery is not None and (recovery.active or any(
                        event.kind == runtime.STATE_OP and event.op == runtime.BEGIN and event.state is recovery
                        for event in instance.queue)):
                    return
    engage(fight.match, bot, player)


def stop(bot):
    """Release a manual Assassin's held attack without touching detached flights."""
    component = bot.body_script.component
    bind(component, None)
    # Keep advancing the body VM so the release edge and authored exits run.


def refresh(fight, bot):
    component = bot.body_script.component
    world = component.world
    if not manual(world) or world.manual_target is None:
        return
    if (fight.match.ended or contact(fight, bot, world.manual_target) is None
            or (bot.entity in fight.pins and fight._body_barrier_active(world.manual_target))):
        stop(bot)
    elif bot.entity in fight.pins:
        for instance in component.instances.values():
            if instance.graph.index == GRAPH:
                node = instance.graph.node(23)
                pin = instance.states.get(node.state) if node is not None else None
                if pin is not None and pin.active:
                    return
        stop(bot)


def modify_health(world, state, elapsed_ms):
    if not scoped(world, state.instance) or state.node.pos not in (15, 25):
        return NotImplemented
    if world.match is None or world.match.ended:
        return True
    fight = world.match.combat
    component = state.instance.component
    source = next((b for b in fight.match.bots if b.entity == component.entity), None)
    entity = expr.to_entity(state.evaluate(state.node.field('m_FA3ECC43'), watch=False))
    if (source is None or not source.firing or source.body_script is None
            or source.body_script.component is not component or entity != world.manual_target):
        fight.pins.pop(component.entity, None)
        return True
    target = contact(fight, source, entity)
    if target is None:
        fight.pins.pop(component.entity, None)
        return True
    settings = state.node.field('m_modifyHealth') or {}
    amount = expr.to_float(state.evaluate(settings.get('m_amount'), watch=False))
    mode = expr.to_int(state.evaluate(state.node.field('m_95F1FB0B'), watch=False))
    damage = amount * elapsed_ms / 1000.0 if mode == 1 else (amount if elapsed_ms == 0 else 0.0)
    if not math.isfinite(damage) or damage < 0:
        return True
    if fight._absorb_body_barrier(target, damage * fight._offense_scalar(source.entity), source.entity):
        stop(source)
        return True
    if fight.pins.get(source.entity) != target.entity:
        fight.pins[source.entity] = target.entity
        fight._interrupt_body(target.owner)
    if damage:
        fight._damage(source, target, expr.f32(damage), False, None, fight.tick, world_tick=fight.tick)
    return True
