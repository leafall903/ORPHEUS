"""Manual1157 pin bridge; target context, contact and damage clock are reconstructed."""

import math

from ow174.game.script import expr

HERO, GRAPH = 0x02E00000000001BB, 0x1157
PIN_RANGE = 3.0  # manual contact policy, not recovered native acquisition distance
SELF_CONTACT_MESSAGE = 0x02400000000009C6


def scoped(world, instance):
    return (world.hero == HERO and world.controller_components is not None
            and instance is not None and instance.graph.index == GRAPH)


def receiver_scope(world, instance):
    return (world.hero == HERO and getattr(world, "controller_components", None) is not None
            and instance is not None and instance.graph.index == 0x13C3)


def self_message_sender(instance, node):
    component = instance.component
    world = component.world
    if not receiver_scope(world, instance) or node.pos != 84 or world.match is None:
        return False
    return any(bot.entity == component.entity and not bot.dead and bot.body_script is not None
               and bot.body_script.component is component for bot in world.match.bots)


def begin_direction_record(world, state):
    instance = state.instance
    priority = state.node.field("m_priority") or {}
    if (not receiver_scope(world, instance) or state.node.pos != 154
            or priority.get("m_priority") != "0x0C40000000000051"):
        return NotImplemented
    # Component86 kind3 record. The verified 0051.024 asset supplies priority30 and flag0.
    match = world.match
    if match is None or not any(bot.entity == instance.component.entity and bot.body_script is not None
                                and bot.body_script.component is instance.component for bot in match.bots):
        return None
    value = state.evaluate(state.node.field("m_7CCD8B4B"), watch=False)
    vector = value if isinstance(value, expr.Vec3) else expr.Vec3(0, 0, 0)
    world.assassin_direction_records[instance.id, state.index] = (3, 30, 0, vector)


def end_direction_record(world, state):
    records = getattr(world, "assassin_direction_records", None)
    if records is not None:
        records.pop((state.instance.id, state.index), None)


def modifier_backend(world, state):
    if not receiver_scope(world, state.instance) or state.node.pos != 106 or world.match is None:
        return None
    component = state.instance.component
    backend = world.assassin_numeric_modifiers
    backend.source_frame = component.frame
    return backend


def velocity_locked(bot, frame):
    """The authored 106 zero-world-velocity row on the manual body clock."""
    if bot.hero != HERO or bot.body_script is None:
        return False
    component = bot.body_script.component
    backend = getattr(component.world, "assassin_numeric_modifiers", None)
    if backend is None:
        return False
    active = {key for key, (start, end, vector) in backend.velocity_sets.items()
              if start <= frame < end and not any(vector)}
    if not active or frame <= component.frame + 1:
        return bool(active)
    from ow174.game.script import modifiers, runtime

    # Route catch-up precedes VM catch-up. Project the queued 105 finish for
    # the enclosing 106 scope without advancing or changing the VM records.
    for instance in component.instances.values():
        if instance.graph.index != 0x13C3:
            continue
        animation = instance.states.get(instance.graph.node(105).state)
        lock = instance.states.get(instance.graph.node(106).state)
        if animation is None or not animation.active or lock is None or not lock.active:
            continue
        if any(event.state is animation and event.kind == runtime.STATE_OP and event.op == runtime.FINISH
               and event.time < frame * component.frame_ms for event in instance.queue):
            # The authored 106 mode0 velocity row occupies slot seven.
            active.discard(modifiers.state_key(instance.id, lock.index, lock.counter, 7, 0))
    return bool(active)


def animation_duration_ms(state):
    if receiver_scope(state.instance.component.world, state.instance) and state.node.pos == 105:
        alias = state.node.field("m_11E0A658") or {}
        category = state.node.field("m_22326315") or {}
        if (alias.get("m_animAlias") == "0x0C80000000000595"
                and category.get("m_category") == "0x0280000000000214"):
            # Blend set07C3 category0214 selects item423. All four clips span six frames at 30 Hz.
            return 200
    if not scoped(state.instance.component.world, state.instance) or state.node.pos != 31:
        return None
    alias = state.node.field("m_11E0A658") or {}
    category = state.node.field("m_22326315") or {}
    if (alias.get("m_animAlias") != "0x0C80000000000561"
            or category.get("m_category") != "0x0280000000000082"):
        return None
    # Body1EBD uses blend set07C3, tree0640 and clip508A for this release.
    return 1467  # 44 frames at 30 Hz, rounded to the VM's millisecond clock


def bind(world, component, target):
    """Retire the old pin before a stop, interruption or target/body replacement."""
    if (getattr(world, 'hero', None) != HERO or getattr(world, 'controller_components', None) is None
            or world.manual_target == target):
        return
    world.manual_target = target
    if world.match is not None:
        world.match.combat.pins.pop(component.entity, None)
    for instance in list(component.instances.values()):
        if instance.graph.index != GRAPH:
            continue
        # A new explicit manual engagement interrupts the release animation through authored exits.
        positions = (23, 12, 31) if target is not None else (23, 12)
        for position in positions:
            node = instance.graph.node(position)
            state = instance.states.get(node.state) if node is not None else None
            if state is not None:
                state.end(False)
        instance.write((expr.INSTANCE, 536), 0)
    component.edges([(36, False)])


def release_from_melee(fight, actor):
    """Reconstructed rescue for the manual contact pin after a damaging melee."""
    if actor.hero != HERO or actor.entity not in fight.pins or actor.body_script is None:
        return
    component = actor.body_script.component
    bind(component.world, component, None)
    # End this manual attempt. The existing scope exit owns release states.
    actor.firing = False
    actor.attack_target = None
    actor.attack_input = False
    component.run_now()


def native(world, cfg, instance):
    receiver = receiver_scope(world, instance)
    if not receiver and not scoped(world, instance):
        return NotImplemented
    query = cfg.get('$')
    if query not in ('STU_DB742151', 'STU_9B7648C3', 'STU_60146072', 'STU_71A87E92', 'STU_2200A66D'):
        return NotImplemented
    entity = expr.to_entity(instance.evaluate(cfg.get('m_entity')))
    if query == 'STU_DB742151' and entity == instance.component.entity:
        # Explicit data-only possessor context; no synthetic controller entity is
        # sent to the client or aliased to an existing body's namespace.
        target = expr.Entity(world.manual_target) if world.manual_target is not None else None
        return expr.Map(((1650, target),))
    if query == 'STU_9B7648C3' and world.match is not None:
        if entity not in (instance.component.entity, world.manual_target):
            return NotImplemented
        target = next((t for t in world.match.combat._targets() if t.entity == entity), None)
        return expr.f32(world.match.combat.vitals_of(target).total) if target is not None else 0.0
    if receiver and world.match is not None:
        fight = world.match.combat
        target = next((t for t in fight._targets() if t.entity == entity), None)
        if query == 'STU_60146072' and entity == instance.component.entity:
            if target is None:
                return 0.0
            vitals = fight.vitals_of(target)
            return expr.f32(sum(vitals.pool_maximum(pool) for pool in vitals.most))
        if target is not None and entity == world.manual_target:
            if query == 'STU_71A87E92':
                return expr.Vec3(*target.feet)
            if query == 'STU_2200A66D':
                from ow174.game.combat import aim

                mover = target.owner.mover
                return expr.Vec3(*(expr.f32(axis) for axis in aim(mover.yaw, mover.pitch)))
    return NotImplemented


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
    # Lane0020 reconstruction: active017B rejects pin control as well as damage.
    # This is not a general native immunity/filter or animation-counterplay rule.
    if fight.zenyatta.ultimate(target.owner):
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
