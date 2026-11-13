"""Delivered104319 Torbjorn owner consumers, incrementally mounted in the combined host.

Overload, hammer windows and actor-backed turrets are mounted here. Molten Core
is reconciled separately rather than treated as complete from ancestry.
"""

import logging
import math
from dataclasses import dataclass, field, replace

from ow174.game.script import expr

HERO = 0x02E0000000000006
TURRET = 0x04000000000028B7
log = logging.getLogger("ow174.game")


def rivet_targets(fight, rocket, targets):
    """Use retained physical poses for delayed, clocked primary/secondary rivets."""
    primary = (rocket.source_graph, rocket.definition) == (0x1683, 0x0400000000002A66)
    secondary = ((rocket.source_graph, rocket.definition) == (0x1683, 0x04000000000013ED)
                 and rocket.shot is None)
    if (not (primary or secondary)
            or getattr(getattr(rocket.shooter, "hero", None), "guid", None) != HERO
            or rocket.launch_tick is None
            or fight.tick >= fight.match.tick):
        return targets
    if primary and (rocket.shot is None or rocket.shot.clock_origin is None):
        return targets
    return [replace(shown, construct=target.construct) for target in targets
            if (shown := fight.seen(target, fight.tick)) is not None]


def view_world_tick(script, command, quanta):
    """Query target history in world time; leave attack/HIT owner labels intact."""
    from ow174.game.combat import TICK

    world, owner = script.world_frame_anchor or script.torbjorn_birth[:2]
    elapsed_ms = (command.frame - owner) * script.frame_ms - command.view_delay(quanta) * 1000
    return world + elapsed_ms / round(TICK * 1000)


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
    from ow174.game import reinhardt_hammer

    protected = fight.barriers.area(player, centre, 1.0, amount,
                                   contacted=contacts.barrier_hit, include_raised=False)
    reinhardt_hammer.surfaces(fight, player, ((centre, centre, 1.0),), amount, contacts.barrier_hit)
    traps = getattr(fight, "steel_traps", None)
    if traps is not None:
        traps.melee(player, origin, centre, 1.0, amount, contacted=contacts.barrier_hit)
    contacts.hit.update(protected)
    view_tick = view_world_tick(script, command, player.quanta)
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


@dataclass
class Turret:
    owner: object
    actor: object
    config: dict
    spawned_at: float
    build_end: float
    last_build: float
    next_fire: float
    shots: int = 0
    target: int | None = None
    last_fired: float | None = None
    last_damaged: float | None = None


def owned_turret(world):
    """Explicit reconstructed deployable context for this current owner body."""
    match = world.match
    if match is None or world.hero != HERO:
        return None
    for turret in match.combat.turrets.values():
        owner = turret.owner
        if (owner.body == world.body and owner.body_script is not None
                and owner.body_script.world is world and owner.hero.guid == HERO
                and not owner.body_script.component.strict
                and turret.actor in match.bots and not turret.actor.dead):
            return turret
    return None


def foreign_turret_var(world, entity, var):
    turret = owned_turret(world)
    if turret is None or entity != turret.actor.entity or turret.actor.body_script is None:
        return NotImplemented
    slot = turret.actor.body_script.component.entity_var(var, create=False)
    return slot.value() if slot is not None else None


def turret_native(world, cfg, instance):
    if cfg.get("$") != "STU_E72F6348":
        return NotImplemented
    turret = owned_turret(world)
    if turret is not None and instance.evaluate(cfg.get("m_entity")) == expr.Entity(turret.actor.entity):
        return True
    return NotImplemented


def turret_message(world, target, message, sender):
    turret = owned_turret(world)
    if (turret is None or target != turret.actor.entity or sender != world.body
            or message != 0x024000000000003F):
        return False
    # The actual16C5 interact path sends003F to its bound turret. The manager
    # supplies retirement/cooldown rather than claiming the missing1688 native
    # role392/KillEntity services are implemented.
    destroy_turret(world.match.combat, turret.owner, damaged=True)
    return True


def turret_damaged(fight, target):
    for turret in fight.turrets.values():
        if turret.actor is target.owner:
            turret.last_damaged = fight.now
            return


def _control_context(fight, turret):
    script = turret.actor.body_script
    if script is None:
        return
    base = next(i for i in script.component.instances.values() if i.graph.index == 0x1688)
    duration = float(base.evaluate(base.graph.node(145).field("m_timeout")))
    script.component.set(795, turret.target is not None)
    script.component.set(2143, turret.last_damaged is not None and fight.now - turret.last_damaged < duration)
    elapsed = 100.0 if turret.last_fired is None else min(100.0, fight.now - turret.last_fired)
    script.component.set(1769, elapsed)
    _owner_turret_var(turret.owner, turret.actor.entity)


def deploy_turret(fight, owner, position, *, born_tick=None):
    """Prepared-data factory; build/selection/muzzle services are reconstructed."""
    from ow174.game import npc
    from ow174.game.script import graph

    if owner not in fight.match.players or owner.gone or not owner.has_body or owner.hero.guid != HERO:
        raise ValueError("turret deployment needs its present Torbjorn owner")
    position = tuple(position)
    if len(position) != 3 or any(type(v) not in (int, float) or not math.isfinite(v) for v in position):
        raise ValueError("turret placement needs three finite coordinates")
    if born_tick is not None and (type(born_tick) is not int or not 0 <= born_tick <= fight.match.tick):
        raise ValueError("turret birth tick must be an observed world tick")
    config = (graph._data().get("deployables") or {}).get("turret")
    if not isinstance(config, dict) or graph.entity_body(TURRET) is None:
        raise ValueError("Torbjorn turret requires the explicit prepared deployable graph bundle")
    destroy_turret(fight, owner)
    config = dict(config)
    actor = npc.spawn(fight.match, npc.Definition(HERO, TURRET, "Torbjorn Turret", config["maximum_health"]),
                      position, team=owner.team, current_health=config["initial_health"], born_tick=born_tick,
                      source_filter_bits=owner.team_bit)
    actor.spawner["manual_spawn"] = False
    actor.combat_owner = owner
    if actor.body_script is not None:
        actor.body_script.component.world.match = fight.match
    actor.combat_geometry = (config["height"], config["radius"], config["height"] * 0.75, False)
    actor.retain_corpse = True  # manager owns destruction; never schedule training-bot respawn
    from ow174.game.combat import TICK

    born = fight.now if born_tick is None else fight.now - (fight.tick - born_tick) * TICK
    end = born + config["build_seconds"]
    turret = Turret(owner, actor, config, born, end, born, end)
    fight.turrets[owner.entity] = turret
    _owner_turret_var(owner, actor.entity)
    return turret


def _owner_turret_var(owner, entity):
    script = owner.body_script
    if script is not None:
        instance = next((i for i in script.component.instances.values() if i.graph.index == 0x16C5), None)
        if instance is not None:
            value = expr.Entity(entity)
            if instance.find_var(expr.INSTANCE, 5875).value() != value:
                instance.write((expr.INSTANCE, 5875), value)
            if script.component.entity_var(679).value() != value:
                instance.write((expr.ENTITY, 679), value)
            # Foreign reads return sampled values, without installing cross-VM
            # variable watchers. Poll the authored combat-lock condition instead.
            state = instance.states.get(25)
            if state is not None and state.active:
                state.polled = True
                state.ticks()


def destroy_turret(fight, owner, *, damaged=False):
    from ow174.game import npc

    turret = fight.turrets.pop(owner.entity, None)
    if turret is not None:
        fight.sombra_controls.retire(turret.actor.entity)
        npc.remove_spawned(fight.match, entities={turret.actor.entity})
        _owner_turret_var(owner, 0)
        if damaged:
            hp = fight.vitals.get(turret.actor.entity)
            elapsed = max(0.0, fight.now - hp.died_at) if hp is not None and hp.died_at is not None else 0.0
            _full_cooldown(owner, elapsed)


def _full_cooldown(owner, elapsed=0.0):
    ability = _turret_ability(owner)
    if ability is not None:
        cooldown = ability.instance.component.entity_var(15787, create=False)
        if cooldown is not None:
            amount = expr.to_float(cooldown.value()) - elapsed * max(0.0, ability.rate())
            ability.instance.write((expr.ENTITY, 6300), max(0.0, amount))


def _turret_ability(owner):
    if owner.body_script is not None and owner.hero.guid == HERO:
        for instance in owner.body_script.component.instances.values():
            if instance.graph.index == 0x16C5:
                return instance.state(13)
    return None


def turret_cooldown_remaining(owner):
    ability = _turret_ability(owner)
    if ability is None:
        return 0.0
    rate = ability.cooldown_rate
    return ability.remaining() / (rate if rate > 0 else 1.0)


def set_turret_cooldown(owner, seconds):
    ability = _turret_ability(owner)
    if ability is not None:
        rate = ability.rate()
        ability.instance.write((expr.ENTITY, 6300), seconds * (rate if rate > 0 else 1.0))


def turret_killed(fight, actor):
    # Input packets may arrive between world updates. Publish the destruction
    # cooldown now, before another player's command can start a fresh throw.
    for turret in fight.turrets.values():
        if turret.actor is actor:
            if actor.entity in fight.sombra_controls.locks and actor.body_script is not None:
                actor.body_script.initial_payload()
                fight.sombra_controls.retire(actor.entity)
            _full_cooldown(turret.owner)
            return


def projectile_impact(fight, rocket, point, struck):
    from ow174.game.script import graph

    spawn = rocket.spawn if rocket.spawn is not None else getattr(rocket.shot, "spawn", None)
    if spawn != TURRET or struck is not None:
        return
    if (rocket.shooter.gone or rocket.shooter not in fight.match.players
            or not rocket.shooter.has_body or rocket.shooter.hero.guid != HERO):
        return
    if not (graph._data().get("deployables") or {}).get("turret") or graph.entity_body(TURRET) is None:
        log.warning("[game] Torbjorn turret deployment unavailable: selected bundle lacks deployable data")
        return
    # Reconstructed valid-surface gate. Wall/ceiling impacts do not create a
    # floating turret. Other projectile collision modes still need native work.
    origin = (point[0], point[1] + 0.05, point[2])
    hit = fight._collision().cast_ray(origin, (0.0, -1.0, 0.0), 0.1)
    if hit is not None and abs(hit.normal[1]) >= 0.707:
        from ow174.game.combat import TICK

        born_tick = (rocket.launch_tick + math.ceil(rocket.flown / TICK)
                     if rocket.launch_tick is not None else None)
        deploy_turret(fight, rocket.shooter, hit.point, born_tick=born_tick)


def update_turrets(fight):
    from ow174.game import combat, npc, torbjorn_mark

    for turret in list(fight.turrets.values()):
        owner, actor, cfg = turret.owner, turret.actor, turret.config
        if (owner.gone or owner.client is None or owner not in fight.match.players or owner.hero.guid != HERO
                or actor not in fight.match.bots or actor.dead):
            destroy_turret(fight, owner, damaged=actor.dead)
            continue
        vitals = fight.vitals.get(actor.entity)
        if vitals is None or vitals.dead:
            destroy_turret(fight, owner, damaged=vitals is not None and vitals.dead)
            continue
        if actor.body_script is not None:
            snapshot = actor.body_script.advance_live(fight.tick, actor.position)
            # Native construction/timers/observer states run; the explicit gun
            # bridge below still owns damage, so reports cannot double-fire.
            actor.body_script.component.shots.clear()
            if snapshot is not None:
                for viewer in fight.match.players:
                    if viewer.spawned and viewer.client is not None:
                        viewer.client.queue_bot_live(actor.entity, snapshot)
        _control_context(fight, turret)
        until = min(fight.now, turret.build_end)
        if until > turret.last_build:
            heal = ((cfg["maximum_health"] - cfg["initial_health"]) *
                    (until - turret.last_build) / cfg["build_seconds"])
            vitals.heal(heal)
            fight._sync_body_health(actor, vitals)
            turret.last_build = until
        if fight.now + combat.EPSILON < turret.build_end or actor.body_script is None:
            continue
        if fight.is_stunned(actor) or actor.entity in fight.sombra_controls.locks:
            turret.target = None
            _control_context(fight, turret)
            continue
        instance = next(i for i in actor.body_script.component.instances.values()
                        if i.graph.index == cfg["gun_graph"])
        state = instance.state(cfg["gun_state"])
        rate = combat._number(state.evaluate(state.node.field("m_numShotsPerSecond"), watch=False), 0.0)
        if rate <= 0:
            continue
        x, y, z = actor.position
        muzzle = (x, y + actor.combat_geometry[2], z)
        candidates = []
        for target in fight._enemies(actor):
            centre = (target.feet[0], target.feet[1] + target.height * 0.5, target.feet[2])
            delta = combat._sub(centre, muzzle)
            distance = math.sqrt(combat._dot(delta, delta))
            if not combat.EPSILON < distance <= cfg["range"]:
                continue
            direction = tuple(axis / distance for axis in delta)
            wall = combat.ray_world(fight._collision(), muzzle, direction, distance)
            if wall is None or wall >= distance - target.radius:
                candidates.append((distance, target.entity, centre))
        turret.target = None
        if not candidates:
            turret.next_fire = max(turret.next_fire, fight.now)
            _control_context(fight, turret)
            continue
        preferred = torbjorn_mark.preferred(fight, owner, candidates)
        _, turret.target, centre = next((row for row in candidates if row[1] == preferred), min(candidates))
        npc.aim_at(fight.match, actor, centre)
        if fight.now + combat.EPSILON >= turret.next_fire:
            if turret.next_fire < fight.now - combat.TICK:
                turret.next_fire = fight.now  # do not replay an unretained burst after a long scheduler gap
            turret.shots += 1
            turret.last_fired = fight.now
            time = fight.tick * actor.body_script.component.frame_ms
            fight._shoot(actor, None, state, turret.shots - 1, time, activation=(turret.shots, time))
            turret.next_fire += 1.0 / rate
        _control_context(fight, turret)
