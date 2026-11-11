"""Manual1157 pin bridge; target context, contact and damage clock are reconstructed."""

import math

from ow174.game import immunity
from ow174.game.script import expr, runtime
from ow174.game.script.graph import graph

HERO, GRAPH = 0x02E00000000001BB, 0x1157
VICTIM_GRAPH, PIN_MESSAGE = 0x1177, 0x02400000000008FE
PIN_RANGE = 3.0  # manual contact policy, not recovered native acquisition distance


def scoped(world, instance):
    return (world.hero == HERO and world.controller_components is not None
            and instance is not None and instance.graph.index == GRAPH)


def bind(world, component, target, *, dying=False):
    """Retire the old pin before a stop, interruption or target/body replacement."""
    if (getattr(world, 'hero', None) != HERO or getattr(world, 'controller_components', None) is None
            or (world.manual_target == target and not dying)):
        return
    world.manual_target = target
    if world.match is not None:
        world.match.combat.release_pin(component.entity)
    for instance in list(component.instances.values()):
        if instance.graph.index != GRAPH:
            continue
        # Ending the pin queues the ordinary release animation. A corpse
        # takes the death pose instead, even if it was already releasing.
        positions = (23, 12, 31) if target is not None or dying else (23, 12)
        for position in positions:
            node = instance.graph.node(position)
            state = instance.states.get(node.state) if node is not None else None
            if state is not None:
                if dying:
                    instance.drop_begins([state])
                state.end(False)
        instance.write((expr.INSTANCE, 536), 0)
    component.edges([(36, False)])


def native(world, cfg, instance):
    if not scoped(world, instance) or cfg.get('$') not in {'STU_DB742151', 'STU_9B7648C3'}:
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


def contact(match, source, target):
    from ow174.game import npc_navigation
    from ow174.game.mover import mover_data

    data = mover_data(source.body)
    return (npc_navigation.pin_contact(match.collision_world, source.position, target, data)
            and npc_navigation.teammate_clear(match, source, source.position, target.feet, data))


def attach_victim(source, target):
    """Use the authored 1157/45 graph and 1157/18 message for the active pin."""
    script = getattr(target.owner, 'body_script', None)
    definition = graph(VICTIM_GRAPH) if script is not None else None
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
# Wraith Form, Fade, and Cryo-Freeze protect against the pin itself.
PIN_EXCLUDED_PHASES = frozenset((0x0020, 0x0F7C, 0x05FE))


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
    if (fight._invulnerable(target.owner, phases=PIN_EXCLUDED_PHASES)
            or immunity.control_immune(target.owner, target.entity)
            or not contact(match, source, target)):
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
    if fight.barriers.for_target(target.owner) is not None:
        fight.release_pin(source.entity)
    elif fight.pins.get(source.entity) != target.entity:
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


def rescue_with_melee(fight, rescuer, target):
    """A sandbox teammate's quick melee interrupts the active pin."""
    actor = target.owner
    victim = fight.pins.get(target.entity)
    if victim is None or fight.match.game_map.free_for_all or fight.vitals_of(target).dead:
        return
    if not any(player.body == victim and player is not rescuer and player.team == rescuer.team
               and not player.gone for player in fight.match.players):
        return
    script = getattr(actor, "body_script", None)
    if script is None or actor.hero != HERO or actor.spawner.get("manual_spawn") is not True:
        return
    for instance in script.component.instances.values():
        if instance.graph.index != GRAPH:
            continue
        recovery = instance.graph.node(37)
        if recovery is None:
            return
        duration = expr.to_float(instance.evaluate(recovery.field("m_timeout")))
        if not math.isfinite(duration) or duration <= 0:
            return
        from ow174.game import combat

        deadline = fight.tick + math.ceil(duration / combat.TICK)
        fight.stuns[target.entity] = max(deadline, fight.stuns.get(target.entity, 0))
        fight._interrupt(target)
        return
