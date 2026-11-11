"""1157 contact/pin consumer with manual and controller input.

The body reads the applied target through the typed controller's variable1650.
The contact and damage clock are server reconstruction.
"""

import math

from ow174.game.script import expr

HERO, GRAPH = 0x02E00000000001BB, 0x1157
CONTROLLER_ATTACK = 0x1158
PIN_BUTTON = 36
PIN_RANGE = 3.0  # Server contact policy. Native acquisition is unknown.


def scoped(world, instance):
    return (world.hero == HERO and getattr(world, 'manual_controller_context', None) is not None
            and not world.manual_controller_context.retired and instance is not None
            and instance.graph.index == GRAPH)


def release(world, source):
    if world.match is not None:
        fight = world.match.combat
        fight.controls = {key: end for key, end in fight.controls.items()
                          if not (key[1] == source and key[2] == 'pin')}


def bind(world, component, target):
    if (world.hero != HERO or getattr(world, 'manual_controller_context', None) is None
            or world.manual_target == target):
        return
    world.manual_target = target
    release(world, component.entity)
    for instance in list(component.instances.values()):
        if instance.graph.index != GRAPH:
            continue
        # The release animation has no recovered native duration. A fresh
        # manual engagement ends it through the graph's usual state exit.
        for position in ((23, 12, 31) if target is not None else (23, 12)):
            node = instance.graph.node(position)
            state = instance.states.get(node.state) if node is not None else None
            if state is not None:
                state.end(False)
        instance.write((expr.INSTANCE, 536), 0)
    component.edges([(PIN_BUTTON, False)])


def retire_victim(match, body):
    for bot in match.bots:
        if bot.hero != HERO or bot.body_script is None:
            continue
        controller = bot.body_script.assassin_controller
        if controller is not None and not controller.owner_dead and controller.fault is None:
            pin = controller.world.pin_input
            for instance in controller.instances.values():
                if instance.graph.index != CONTROLLER_ATTACK:
                    continue
                target = instance.find_var(expr.INSTANCE, 536, create=False)
                if target is None or expr.to_entity(target.value()) != body:
                    continue
                # The attack's target stack masks writes to the underlying variable.
                target_stack = instance.node_state(instance.graph.node(53))
                if target_stack is not None:
                    target_stack.end(False)
                    instance.drop_begins([target_stack])
                instance.write((expr.INSTANCE, 536), 0)
                if pin is not None and pin.instance is instance:
                    pin.end(False)
            selected = controller.entity_var(1650, create=False)
            if selected is not None and expr.to_entity(selected.value()) == body:
                controller.set(1650, 0)
        component = bot.body_script.component
        if component.world.manual_target == body:
            bind(component.world, component, None)
            # Consume release before another body can bind the same numeric ID.
            component.run_now()
            bot.attack_input = False


def controller_pin_target(bot):
    if bot.hero != HERO or bot.body_script is None:
        return None
    controller = bot.body_script.assassin_controller
    if controller is None or controller.owner_dead or controller.fault is not None:
        return None
    state = controller.world.pin_input
    if state is None or not state.active:
        return None
    target = state.instance.find_var(expr.INSTANCE, 536, create=False)
    value = target.value() if target is not None else None
    return value.id if isinstance(value, expr.Entity) and expr.valid_entity(value.id) else None


def native(world, cfg, instance):
    if not scoped(world, instance):
        return NotImplemented
    cls = cfg.get('$')
    if cls not in ('STU_DB742151', 'STU_9B7648C3'):
        return NotImplemented
    entity = expr.to_entity(instance.evaluate(cfg.get('m_entity')))
    if cls == 'STU_DB742151' and entity == instance.component.entity:
        return expr.Entity(world.manual_controller_descriptor.entity)
    if cls == 'STU_9B7648C3' and entity == instance.component.entity:
        from ow174.game.script.driver import OWNER_HEALTH

        slot = instance.component.entity_var(OWNER_HEALTH, create=False)
        return expr.f32(slot.value()) if slot is not None else 0.0
    if cls == 'STU_9B7648C3' and world.match is not None:
        if entity not in (instance.component.entity, world.manual_target):
            return NotImplemented
        target = next((t for t in world.match.combat._targets() if t.entity == entity), None)
        return expr.f32(world.match.combat.vitals_of(target).total) if target is not None else 0.0
    return NotImplemented


def victim(world, state):
    if world.match is None or world.match.ended:
        return None
    fight = world.match.combat
    source = next((b for b in world.match.bots if b.entity == state.instance.component.entity), None)
    if (source is None or source.dead or fight.is_stunned(source)
            or source.body_script is None or source.body_script.component is not state.instance.component):
        return None
    selector = state.node.field('m_FA3ECC43')
    value = (state.evaluate(selector, watch=False) if selector is not None
             else state.instance.find_var(0, 536).value())
    entity = expr.to_entity(value)
    if entity != world.manual_target:
        return None
    target = next((t for t in fight._enemies(source) if t.entity == entity), None)
    if target is None or getattr(target.owner, 'client', None) is None or target.owner.gone:
        return None
    if source.firing:
        if source.attack_action != 'pin' or target.owner.entity != source.attack_target:
            return None
    elif controller_pin_target(source) != entity:
        return None
    from ow174.game import combat, defenses

    if defenses.control_immune(target.owner):
        return None

    origin = (source.position[0], source.position[1] + combat.EYE, source.position[2])
    eye = fight._eye(target.owner)
    a, b = target.segment()
    if (math.dist(origin, eye) > PIN_RANGE or fight.walls.blocked(origin, eye)
            or not combat.capsule_visible(world.match.collision_world, origin, a, b, target.radius)):
        return None
    return source, target


def hold(world, state, source, target):
    fight = world.match.combat
    key = (target.entity, source.entity, 'pin')
    window = 2 * state.instance.component.frame_ms / 1000
    if key not in fight.controls:
        fight.control(target, source.entity, 'pin', window)
    else:
        fight.controls[key] = fight.now + window


def modify_health(world, state, elapsed_ms):
    if not scoped(world, state.instance) or state.node.pos not in (15, 25):
        return NotImplemented
    pair = victim(world, state)
    if pair is None:
        release(world, state.instance.component.entity)
        return True
    source, target = pair
    fight = world.match.combat
    hold(world, state, source, target)
    settings = state.node.field('m_modifyHealth') or {}
    amount = expr.to_float(state.evaluate(settings.get('m_amount'), watch=False))
    mode = expr.to_int(state.evaluate(state.node.field('m_95F1FB0B'), watch=False))
    damage = amount * elapsed_ms / 1000 if mode == 1 else (amount if elapsed_ms == 0 else 0.0)
    if math.isfinite(damage) and damage > 0:
        from ow174.game.combat import damage_tags

        fight._damage(source, target, expr.f32(damage), False, None, fight.tick,
                      tags=damage_tags(state, settings))
    return True


def end_health(world, state):
    if not scoped(world, state.instance):
        return
    node = state.instance.graph.node(23)
    phase = state.instance.states.get(node.state) if node is not None else None
    if phase is None or not phase.active or phase.ending:
        release(world, state.instance.component.entity)


def refresh(world, component):
    if world.hero != HERO or getattr(world, 'manual_controller_context', None) is None:
        return
    for instance in component.instances.values():
        if instance.graph.index == GRAPH:
            node = instance.graph.node(23)
            phase = instance.states.get(node.state) if node is not None else None
            if phase is not None and phase.active:
                pair = victim(world, phase)
                if pair is not None:
                    hold(world, phase, *pair)
                    return
    release(world, component.entity)
