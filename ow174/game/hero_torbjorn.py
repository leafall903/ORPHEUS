"""104319 Torbjorn bridges for reconstructed authoritative combat.

Hammer timing uses the authored swing duration and its0.42 normalized threshold.
This is an explicit substitute for the unimplemented native54FEB21C consumer;
it does not claim a complete native animation-event or TrackTargets backend.
"""

import logging
import math
from dataclasses import dataclass

from ow174.game.script import expr

HERO = 0x02E0000000000006
TURRET = 0x04000000000028B7
log = logging.getLogger("ow174.game")


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


def deploy_turret(fight, owner, position):
    """Prepared-data factory; build/selection/muzzle services are reconstructed."""
    from ow174.game import npc
    from ow174.game.script import graph

    if owner not in fight.match.players or owner.gone or not owner.has_body or owner.hero.guid != HERO:
        raise ValueError("turret deployment needs its present Torbjorn owner")
    position = tuple(position)
    if len(position) != 3 or any(type(v) not in (int, float) or not math.isfinite(v) for v in position):
        raise ValueError("turret placement needs three finite coordinates")
    config = (graph._data().get("deployables") or {}).get("turret")
    if not isinstance(config, dict) or graph.entity_body(TURRET) is None:
        raise ValueError("Torbjorn turret requires the explicit prepared deployable graph bundle")
    destroy_turret(fight, owner)
    config = dict(config)
    actor = npc.spawn(fight.match, npc.Definition(HERO, TURRET, "Torbjorn Turret", config["maximum_health"]),
                      position, team=owner.team, current_health=config["initial_health"])
    actor.spawner["manual_spawn"] = False
    actor.combat_owner = owner
    actor.combat_geometry = (config["height"], config["radius"], config["height"] * 0.75, False)
    actor.retain_corpse = True  # manager owns destruction; never schedule training-bot respawn
    end = fight.now + config["build_seconds"]
    turret = Turret(owner, actor, config, fight.now, end, fight.now, end)
    fight.turrets[owner.entity] = turret
    _owner_turret_var(owner, actor.entity)
    return turret


def _owner_turret_var(owner, entity):
    script = owner.body_script
    if script is not None:
        instance = next((i for i in script.component.instances.values() if i.graph.index == 0x16C5), None)
        if instance is not None:
            instance.write((expr.INSTANCE, 5875), expr.Entity(entity))


def destroy_turret(fight, owner, *, damaged=False):
    from ow174.game import npc

    turret = fight.turrets.pop(owner.entity, None)
    if turret is not None:
        npc.remove_spawned(fight.match, entities={turret.actor.entity})
        _owner_turret_var(owner, 0)
        if damaged and owner.body_script is not None and owner.hero.guid == HERO:
            component = owner.body_script.component
            cooldown = component.entity_var(15787, create=False)
            instance = next((i for i in component.instances.values() if i.graph.index == 0x16C5), None)
            if cooldown is not None and instance is not None:
                instance.write((expr.ENTITY, 6300), cooldown.value())


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
        deploy_turret(fight, rocket.shooter, hit.point)


def update_turrets(fight):
    from ow174.game import combat, npc

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
        until = min(fight.now, turret.build_end)
        if until > turret.last_build:
            heal = ((cfg["maximum_health"] - cfg["initial_health"]) *
                    (until - turret.last_build) / cfg["build_seconds"])
            vitals.heal(heal)
            fight._sync_body_health(actor, vitals)
            turret.last_build = until
        if fight.now + combat.EPSILON < turret.build_end or actor.body_script is None:
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
            continue
        _, turret.target, centre = min(candidates)
        npc.aim_at(fight.match, actor, centre)
        if fight.now + combat.EPSILON >= turret.next_fire:
            if turret.next_fire < fight.now - combat.TICK:
                turret.next_fire = fight.now  # do not replay an unretained burst after a long scheduler gap
            turret.shots += 1
            time = fight.tick * actor.body_script.component.frame_ms
            fight._shoot(actor, None, state, turret.shots - 1, time, activation=(turret.shots, time))
            turret.next_fire += 1.0 / rate


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
    if not vitals.dead and vitals.heal(turret.config["repair"]) > 0:
        fight._sync_body_health(turret.actor, vitals)


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


def hammer(fight, player, command, script):
    from ow174.game import combat

    if command is None or player.hero.guid != HERO:
        return
    instance = next((i for i in script.component.instances.values() if i.graph.index == 0x168C), None)
    if instance is None:
        return
    swing = instance.states.get(13)
    if swing is None or not swing.active:
        return
    duration = combat._number(swing.evaluate(swing.node.field("m_86701C3E"), watch=False), 0.0)
    trigger = instance.graph.node(43)
    fraction = combat._number(swing.evaluate(trigger.field("m_ED97A97C"), watch=False), 0.0)
    if duration <= 0.0 or not 0.0 <= fraction <= 1.0:
        return
    if script.component.now < swing.start + duration * fraction * 1000:
        return
    counters = fight.melee_seen.setdefault(script.entity, {})
    key = (instance.id, 6)
    if counters.get(key, 0) == swing.counter:
        return
    counters[key] = swing.counter
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
    view_tick = command.frame - command.view_delay(player.quanta) * 1e6 / player.quanta
    for target in fight._enemies(player):
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
        fight._damage(player, target, amount, False, loadout, command.frame)
    repair_turret(fight, player, centre, 1.0)
