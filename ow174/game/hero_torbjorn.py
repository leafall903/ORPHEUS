"""Delivered104319 Torbjorn owner consumers, incrementally mounted in the combined host.

Overload and hammer windows are mounted here. Turret and Molten Core families
are reconciled in later batches rather than treated as complete from ancestry.
"""

import math
from dataclasses import dataclass, field

from ow174.game.script import expr

HERO = 0x02E0000000000006


def preserve_cast_during_control(state):
    """Selected control policy for an already-applied Overload buff.

    1721/19 owns the short cast wait, while3 and its lifetime live outside that
    scope. Stopping the cast skips its normal cooldown path and permits early
    recasting after the buff expires. Preserve that authored path while the
    control host separately suppresses input/movement. Native CC dispatch and
    animation interruption remain unverified; this is a reconstructed policy.
    """
    instance = state.instance
    buff = instance.states.get(3)
    return instance.graph.index == 0x1721 and state.index == 19 and buff is not None and buff.active


def _overload_ability(owner):
    script = owner.body_script
    if script is not None and owner.hero.guid == HERO:
        for instance in script.component.instances.values():
            if instance.graph.index == 0x1721:
                return instance.state(19)
    return None


def overload_cooldown_remaining(owner):
    """Wall time until reuse when replacing this body."""
    ability = _overload_ability(owner)
    if ability is None:
        return 0.0
    instance = ability.instance
    duration = instance.state(1)
    if duration is not None and duration.active:
        # Graph 1721 chases entity var 9143 to zero, then node 39 writes
        # the configured cooldown from instance var 472.
        buff_left = expr.to_float(instance.find_var(expr.ENTITY, 9143).value())
        cooldown = expr.to_float(instance.find_var(expr.INSTANCE, 472).value())
        rate = ability.rate()
        return buff_left + cooldown / (rate if rate > 0 else 1.0)
    rate = ability.cooldown_rate
    return ability.remaining() / (rate if rate > 0 else 1.0)


def set_overload_cooldown(owner, seconds):
    ability = _overload_ability(owner)
    if ability is not None:
        target = expr.lvalue(ability.node.field("m_out_CooldownVar"))
        if target is not None:
            rate = ability.rate()
            ability.instance.write(target, seconds * (rate if rate > 0 else 1.0))


def overload(fight, player, script):
    """Mount authored1721/st3 as temporary armour while its real state is active."""
    from ow174.game import combat

    if player.hero.guid != HERO:
        return
    target = next((t for t in fight._targets() if t.owner is player), None)
    if target is None:
        return
    vitals = fight.vitals_of(target)
    instance = next((i for i in script.component.instances.values() if i.graph.index == 0x1721), None)
    state = instance.states.get(3) if instance is not None else None
    source, amount = None, 0.0
    if not vitals.dead and state is not None and state.active:
        source = (instance.id, state.counter)
        amount = combat._number(state.evaluate(state.node.field("m_amount"), watch=False), 0.0)
    if vitals.set_over_armour(source, amount):
        fight._sync_body_health(player, vitals)


@dataclass
class HammerContacts:
    instance: int
    counter: int
    begin: float
    end: float
    hit: set[int] = field(default_factory=set)
    barrier_hit: set[int] = field(default_factory=set)
    repaired: set[int] = field(default_factory=set)
    cancelled: bool = False


def cancel_hammer(fight, body):
    contacts = fight.hammer_contacts.get(body)
    if contacts is not None:
        contacts.cancelled = True


def repair_turret(fight, player, centre, radius):
    from ow174.game import combat

    turret = fight.turrets.get(player.entity)
    if turret is None or turret.actor.dead:
        return
    target = next((t for t in fight._targets() if t.owner is turret.actor), None)
    if target is None:
        return
    a, b = target.segment()
    if combat.segment_distance(centre, a, b) - target.radius > radius:
        return
    delta = combat._sub(turret.actor.position, tuple(player.mover.position))
    distance = math.sqrt(combat._dot(delta, delta))
    if distance > combat.EPSILON:
        origin = fight._eye(player)
        goal = (target.feet[0], target.feet[1] + target.height * 0.5, target.feet[2])
        delta = combat._sub(goal, origin)
        distance = math.sqrt(combat._dot(delta, delta))
        direction = tuple(axis / distance for axis in delta)
        wall = combat.ray_world(fight._collision(), origin, direction, distance)
        if wall is not None and wall < distance - target.radius:
            return
    vitals = fight.vitals_of(target)
    if not vitals.dead:
        if vitals.heal(turret.config["repair"]) > 0:
            fight._sync_body_health(turret.actor, vitals)
        return turret.actor.entity


def hammer(fight, player, command, script):
    from ow174.game import combat

    if command is None or player.hero.guid != HERO:
        return
    instance = next((i for i in script.component.instances.values() if i.graph.index == 0x168C), None)
    if instance is None:
        return
    swing = instance.states.get(13)
    if swing is None or not swing.active:
        fight.hammer_contacts.pop(script.entity, None)
        return
    contacts = fight.hammer_contacts.get(script.entity)
    if contacts is None or (contacts.instance, contacts.counter) != (instance.id, swing.counter):
        duration = combat._number(swing.evaluate(swing.node.field("m_86701C3E"), watch=False), 0.0)
        boosted = swing.evaluate(instance.graph.node(47).field("m_condition"), watch=False)
        trigger = instance.graph.node(48 if boosted else 43)
        fraction = combat._number(swing.evaluate(trigger.field("m_ED97A97C"), watch=False), 0.0)
        window = combat._number(swing.evaluate(instance.graph.node(46).field("m_timeout"), watch=False), 0.0)
        if duration <= 0.0 or not 0.0 <= fraction <= 1.0 or window <= 0.0:
            return
        # Branch47 chooses at swing entry. Expiry of Overload midway through
        # the animation cannot move this already-started contact interval.
        begin = swing.start + duration * fraction * 1000
        contacts = HammerContacts(instance.id, swing.counter, begin, begin + window * 1000)
        fight.hammer_contacts[script.entity] = contacts
    if contacts.cancelled or not contacts.begin <= script.component.now < contacts.end:
        return
    state = instance.state(6)
    modify = state.node.field("m_modifyHealth") or {}
    amount = combat._number(state.evaluate(modify.get("m_amount"), watch=False), 0.0)
    loadout = state.evaluate(modify.get("m_loadout"), watch=False)
    loadout = loadout.guid if isinstance(loadout, expr.Asset) else None
    # Authored local centre(0,1.2,1.5), radius1. Geometry follows command aim;
    # animated pose/hitboxes are not reconstructed by the capsule solver.
    x, y, z = player.mover.position
    origin = (x, y + 1.2, z)
    centre = combat._along(origin, combat.aim(*command.aim()), 1.5)
    protected = fight.barriers.area(player, centre, 1.0, amount, contacted=contacts.barrier_hit)
    fight.orisa_barriers.melee(player, origin, centre, 1.0, amount, contacted=contacts.barrier_hit)
    combat.sigma_barrier.melee(fight, player, origin, centre, 1.0, amount, contacted=contacts.barrier_hit)
    fight.photon_barriers.melee(player, origin, centre, 1.0, amount, contacted=contacts.barrier_hit)
    traps = getattr(fight, "steel_traps", None)
    if traps is not None:
        traps.melee(player, origin, centre, 1.0, amount, contacted=contacts.barrier_hit)
    contacts.hit.update(protected)
    view_tick = command.frame - command.view_delay(player.quanta) * 1e6 / player.quanta
    for target in fight._enemies(player):
        if target.entity in contacts.hit:
            continue
        shown = fight.seen(target, view_tick)
        if shown is None:
            continue
        a, b = shown.segment()
        if combat.segment_distance(centre, a, b) - shown.radius > 1.0:
            continue
        middle = (shown.feet[0], shown.feet[1] + shown.height * 0.5, shown.feet[2])
        delta = combat._sub(middle, origin)
        distance = math.sqrt(combat._dot(delta, delta))
        if distance > combat.EPSILON:
            direction = tuple(axis / distance for axis in delta)
            wall = combat.ray_world(fight._collision(), origin, direction, distance)
            if wall is not None and wall < distance - shown.radius:
                continue
        contacts.hit.add(target.entity)
        if not blocks_melee(player, target, state, modify):
            fight._damage(player, target, amount, False, loadout, command.frame)
    # RepairTrack33 has an independent owner-centred sphere, rather than the
    # forward damage sphere of Track11. Its filter accepts only bound actor679.
    repair_track = instance.graph.node(33)
    offset = swing.evaluate(repair_track.field("m_05BE62DD"), watch=False)
    radius = combat._number(swing.evaluate(repair_track.field("m_radius"), watch=False), 0.0)
    turret = fight.turrets.get(player.entity)
    if (isinstance(offset, expr.Vec3) and radius > 0.0 and turret is not None
            and turret.actor.entity not in contacts.repaired):
        repaired = repair_turret(fight, player, (x + offset.x, y + offset.y, z + offset.z), radius)
        if repaired is not None:
            contacts.repaired.add(repaired)


def blocks_melee(attacker, target, attack_state, modify):
    """Host the authored 03A3/107 zero scalar for the two mapped melee attacks.

    Resolve the front-facing predicate with live feet and aim. The native
    combat callback's position operands and tag matching remain unverified.
    """
    from ow174.game.combat import aim

    script = getattr(target.owner, "body_script", None)
    if (attacker.hero.guid != 0x02E0000000000006
            or (attack_state.instance.graph.index, attack_state.node.pos) not in ((0x43, 10), (0x168C, 12))
            or getattr(script, "hero", None) != 0x02E0000000000029
            or script.retired or script.component.strict or script.component.owner_dead):
        return False
    delta = tuple(a - b for a, b in zip(attacker.mover.position, target.feet, strict=True))
    if sum(a * b for a, b in zip(delta, aim(target.owner.mover.yaw, target.owner.mover.pitch), strict=True)) <= 0:
        return False
    for instance in script.component.instances.values():
        if instance.graph.index != 0x03A3:
            continue
        for state in instance.states.values():
            node = state.node
            if not state.active or node.pos != 107 or node.cls != "STUStatescriptStateCombatModFilter":
                continue
            if node.field("m_60703B1E") != "0x0D80000000000318":
                continue
            condition = (node.field("m_6AA5D70F") or {}).get("m_condition") or {}
            expression = condition.get("m_expression") or {}
            if expression.get("m_opcodes") != [13, 0, 14, 0, 24, 14, 1, 55, 6, 0, 36, 0]:
                continue
            scalar = state.evaluate(node.field("m_50530EAB"), watch=False)
            if type(scalar) not in (int, float) or scalar != 0:
                continue
            tags = {expr.guid_of(tag) for tag in node.field("m_F60AA143") or ()}
            for row in modify.get("m_148F3152") or ():
                tag = attack_state.evaluate(row.get("m_EE3055BD"), watch=False)
                if isinstance(tag, expr.Asset) and tag.guid in tags:
                    return True
    return False
