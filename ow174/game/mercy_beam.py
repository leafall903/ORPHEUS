"""Selected 104319 Mercy staff beam host.

006B owns input and beam lifetime. Target geometry and additive outgoing
damage are reconstructed from 006C node19 (#174) and node18's consumer.
"""

import math

from ow174.game.script import expr, runtime

HERO = 0x02E0000000000004
STAFF = 0x006B
HEAL_EFFECT = 0x04A0000000000019
BOOST_EFFECT = 0x04A0000000000016
BOOST_FACTOR = 1.3
CHAIN_RADIUS = 10  # 006C state 12 and 0079 state 19
HEALED_MESSAGE = 0x0240000000000044
BOOSTED_HIT_MESSAGE = 0x0240000000000D9A
HEAL_TAG = expr.Asset(0x0D800000000034BF)
CHAIN_HEAL_TAG = expr.Asset(0x0D800000000034C0)


def query(fight, state):
    instance = state.instance
    if instance.graph.index != STAFF or state.index not in (4, 5, 51):
        return NotImplemented
    source = instance.component.entity
    caster = next((p for p in fight.match.players if p.has_body and p.body == source), None)
    if caster is None or caster.hero.guid != HERO:
        return NotImplemented
    if fight.match.game_map.free_for_all or fight.is_dead(caster) or caster.gone:
        return ()
    from ow174.game.combat import aim, ray_world

    retain = None
    if state.index == 5:
        selected = instance.find_var(expr.INSTANCE, 375).value()
        retain = ({item.id for item in selected if isinstance(item, expr.Entity)}
                  if isinstance(selected, tuple) else set())
    else:
        hold = instance.states.get(5)
        if hold is not None and hold.active:
            return ()
    origin = fight._eye(caster)
    forward = aim(caster.mover.yaw, caster.mover.pitch)
    radius = expr.to_float(state.evaluate(state.node.field("m_radius"), watch=False))
    angle_cfg = state.node.field("m_A30B383E")
    angle = expr.to_float(state.evaluate(angle_cfg, watch=False)) if angle_cfg else 360.0
    if angle <= 0 and state.index == 4:
        # The native acquisition predicate returns no angle in this VM.
        # Use the ordinary 40-degree fallback authored on node121.
        angle = expr.to_float(state.evaluate(instance.graph.node(121).field("m_A30B383E"), watch=False))
    threshold = math.cos(math.radians(max(0.0, angle) * 0.5))
    candidates = []
    for target in fight._targets():
        if (target.entity == source or target.team != caster.team or fight.vitals_of(target).dead
                or (retain is not None and target.entity not in retain)):
            continue
        x, y, z = target.feet
        delta = tuple(a - b for a, b in zip((x, y + target.height * 0.5, z), origin, strict=True))
        distance = math.hypot(*delta)
        if not 0 < distance <= radius:
            continue
        direction = tuple(d / distance for d in delta)
        alignment = sum(a * b for a, b in zip(direction, forward, strict=True))
        if angle < 360 and alignment < threshold:
            continue
        wall = ray_world(fight._collision(), origin, direction, distance)
        if wall is None or wall >= distance:
            candidates.append((-alignment, distance, target.entity))
    return (expr.Entity(min(candidates)[2]),) if candidates else ()


def boost_source(fight, shooter):
    from ow174.game.combat import _source_entity

    if fight.match.game_map.free_for_all:
        return None
    target = _source_entity(shooter)
    for caster in fight.match.players:
        if (caster is shooter or caster.hero.guid != HERO or caster.team != shooter.team
                or not caster.has_body or not caster.spawned or caster.gone or fight.is_dead(caster)):
            continue
        script = caster.body_script
        if script is None or script.retired or script.component.owner_dead:
            continue
        for instance in script.component.instances.values():
            if instance.graph.index != STAFF:
                continue
            state = instance.states.get(35)
            if state is None or not state.active or state.ending:
                continue
            selected = beam_target(state, BOOST_EFFECT)
            if selected is None:
                continue
            if selected.id == target or valkyrie_chain(fight, instance, selected, target):
                return instance
    return None


def factor(fight, shooter):
    return BOOST_FACTOR if boost_source(fight, shooter) is not None else 1.0


def beam_target(state, definition):
    effect = state.evaluate(state.node.field("m_A76A8CF6"), watch=False)
    selected = state.evaluate(state.node.field("m_targets"), watch=False)
    if isinstance(effect, expr.Asset) and effect.guid == definition and isinstance(selected, expr.Entity):
        return selected
    return None


def valkyrie(instance):
    phase = instance.component.entity_var(842, create=False)
    return phase is not None and expr.truthy(phase.value())


def holds_target(fight, instance, selected):
    hold = instance.states.get(5)
    return hold is not None and hold.active and not hold.ending and selected in query(fight, hold)


def valkyrie_chain(fight, instance, selected, target):
    """Reconstruct the secondary boost from the current live primary beam."""
    if not valkyrie(instance) or not holds_target(fight, instance, selected):
        return False
    targets = {item.entity: item for item in fight._targets()}
    anchor, recipient = targets.get(selected.id), targets.get(target)
    return anchor is not None and recipient is not None and chain_visible(fight, anchor, recipient)


def chain_visible(fight, anchor, recipient):
    from ow174.game.combat import capsule_visible

    x, y, z = anchor.feet
    centre = (x, y + anchor.height * 0.5, z)
    x, y, z = recipient.feet
    point = (x, y + recipient.height * 0.5, z)
    a, b = recipient.segment()
    return (recipient.team == anchor.team and not fight.vitals_of(recipient).dead
            and math.dist(centre, point) <= CHAIN_RADIUS and not fight.walls.blocked(centre, point)
            and capsule_visible(fight._collision(), centre, a, b, recipient.radius))


def healing_state(player):
    script = player.body_script
    if (player.hero.guid != HERO or not player.spawned or player.gone or script is None
            or script.retired or script.component.owner_dead):
        return None
    for instance in script.component.instances.values():
        if instance.graph.index == STAFF:
            state = instance.states.get(33)
            if state is not None and state.active and not state.ending:
                return state
    return None


def settle_healing(fight, caster, recipient, amount, tags):
    given = fight._heal(caster, recipient.owner, fight.vitals_of(recipient), amount)
    if given <= 0:
        return
    params = {0x3F: expr.Entity(recipient.entity), 0x40: expr.f32(given), 0x41: tags}
    charge_message(caster, HEALED_MESSAGE, params)


def charge_message(caster, message, params):
    charge = charge_graph(caster.body_script.component)
    if charge is not None and runtime.send_instance_message(charge, message, params, caster.body):
        charge.update()


def charge_graph(component):
    return next((item for item in component.instances.values() if item.graph.index == 0x0B8B), None)


def award_boost(staff, shooter, victim, dealt, extra):
    # 006B tests 74C for a positive award. Use the settled body loss in this bridge.
    params = {0x74D: expr.f32(extra), 0x13: expr.f32(dealt),
              0x5C: expr.Entity(victim), 0x74C: expr.f32(dealt)}
    if runtime.send_instance_message(staff, BOOSTED_HIT_MESSAGE, params, shooter):
        staff.update()
        charge = charge_graph(staff.component)
        if charge is not None:
            charge.update()


def boosted_victim(fight, instance, cfg):
    if instance.graph.index != STAFF or instance.component.strict or cfg.get("$") != "STU_AC829876":
        return NotImplemented
    filters = instance.graph.node(176).field("m_condition")["m_configVars"]
    if cfg is not filters[0] and cfg is not filters[2]:
        return NotImplemented
    # Resolve the ordinary sandbox combat bodies used by both filters.
    caster = next((p for p in fight.match.players if p.body == instance.component.entity), None)
    victim = expr.to_entity(instance.evaluate(cfg["m_entity"]))
    hostile = caster is not None and any(
        target.entity == victim and target.team != caster.team
        for target in fight._targets()
    )
    gate = cfg["m_filter"]["m_condition"]
    return hostile and (gate is None or expr.truthy(instance.evaluate(gate)))


def track_boost(instance, node):
    component = instance.component
    match = getattr(component.world, "match", None)
    if component.strict or match is None or instance.graph.index != STAFF or node.pos != 172:
        return
    caster = next((p for p in match.players if p.body == component.entity), None)
    if caster is None:
        return
    amount = expr.to_float(instance.evaluate(node.field("m_amount")))
    for stat in node.field("m_stats"):
        caster.stats.add(int(stat, 16), amount)


def heal(fight, caster, seconds):
    """Settle one world tick of a live yellow beam through the normal healing path."""
    state = healing_state(caster)
    if state is None:
        return
    script = caster.body_script
    if script.world_frame_anchor is not None:
        tick, frame = script.world_frame_anchor
        boundary = frame * script.frame_ms + (fight.tick - tick) * round(seconds * 1000)
        if state.start >= boundary:
            return
    instance = state.instance
    selected = beam_target(state, HEAL_EFFECT)
    if selected is None or not holds_target(fight, instance, selected):
        return
    targets = {item.entity: item for item in fight._targets()
               if fight.replacement_ticks.get(item.entity, fight.tick) <= fight.tick}
    anchor = targets.get(selected.id)
    if anchor is None:
        return
    rate = expr.to_float(instance.vars[6578].value())  # 006B state 69
    amount = rate * seconds
    settle_healing(fight, caster, anchor, amount, (HEAL_TAG,))
    if valkyrie(instance):
        for recipient in targets.values():
            if recipient.owner is caster or recipient is anchor:
                continue
            if chain_visible(fight, anchor, recipient):
                settle_healing(fight, caster, recipient, amount, (HEAL_TAG, CHAIN_HEAL_TAG))
