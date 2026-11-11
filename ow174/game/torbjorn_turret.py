"""Ground deployment and fire for Torbjörn's authored 104319 turret."""

import math
from dataclasses import dataclass

from ow174.game import bots, pools
from ow174.game.script import expr, graph

HERO = 0x02E0000000000006
BODY = 0x04000000000028B7
THROW = 0x0400000000002931
GUN = 0x1689
HEIGHT, RADIUS, EYE = 1.0, 0.5, 0.75
INITIAL_HEALTH, BUILD_SECONDS, RANGE = 75, 3, 40


@dataclass
class Turret:
    owner: object
    actor: bots.Bot
    build_end: float
    last_build: float
    next_shot: float
    shots: int = 0


def bind(owner, entity, *, destroyed=False):
    script = owner.body_script
    if script is None or owner.hero.guid != HERO:
        return
    ability = next((row for row in script.component.instances.values() if row.graph.index == 0x16C5), None)
    if ability is None:
        return
    ability.write((expr.INSTANCE, 5875), expr.Entity(entity))
    script.component.set(679, expr.Entity(entity))
    if destroyed:
        duration = script.component.entity_var(15787, create=False)
        seconds = expr.to_float(duration.value()) if duration is not None else 0.0
        if seconds <= 0.0:
            stock = ability.graph.node(35).field("m_value.m_01AFADB6")
            seconds = expr.to_float(ability.evaluate(stock))
        ability.write((expr.ENTITY, 6300), seconds)


def retire(fight, turret, *, destroyed=False):
    from ow174.game import npc

    fight.torbjorn_turrets.pop(turret.actor.entity, None)
    npc.remove_summoned(fight.match, turret.actor)
    bind(turret.owner, 0, destroyed=destroyed)


def land(fight, rocket, point, normal):
    from ow174.game import npc

    owner = rocket.shooter
    if (normal[1] < .707 or isinstance(owner, bots.Bot) or owner.gone or owner.body != rocket.owner_body
            or owner.hero.guid != HERO):
        return
    record = graph.entity_body(BODY)
    if record is None or any(graph.graph(row["graph"]) is None for row in record["graphs"]):
        return
    health_row = next(row for row in record["graphs"] if row["graph"] == pools.BODY_GRAPH)
    values = pools._numbers(graph.graph(pools.BODY_GRAPH).fields["m_publicSchema"]["m_entries"])
    values.update(pools._numbers(health_row.get("m_1EB5A024")))
    definition = npc.Definition(HERO, BODY, "Torbjörn Turret", values[pools.HEALTH_VAR])
    actor = npc.spawn(fight.match, definition, point, team=owner.team,
                      summoned=True, current_health=INITIAL_HEALTH)
    for turret in tuple(fight.torbjorn_turrets.values()):
        if turret.owner is owner:
            retire(fight, turret)
    end = fight.now + BUILD_SECONDS
    fight.torbjorn_turrets[actor.entity] = Turret(owner, actor, end, fight.now, end)
    bind(owner, actor.entity)


def update(fight):
    from ow174.game import combat

    for turret in tuple(fight.torbjorn_turrets.values()):
        owner, actor = turret.owner, turret.actor
        health = fight.vitals.get(actor.entity)
        if (owner.gone or owner.client is None or owner.hero.guid != HERO
                or actor not in fight.match.bots or health is None or actor.dead):
            retire(fight, turret, destroyed=actor.dead)
            continue
        script = actor.body_script
        if script is None:
            continue
        script.advance_live(fight.tick, actor.position)
        script.component.shots.clear()
        until = min(fight.now, turret.build_end)
        if until > turret.last_build:
            health.heal((health.maximum - INITIAL_HEALTH) * (until - turret.last_build) / BUILD_SECONDS)
            fight._sync_body_health(actor, health)
            turret.last_build = until
        if fight.now + combat.EPSILON < turret.next_shot or fight.is_stunned(actor):
            continue
        x, y, z = actor.position
        muzzle = (x, y + EYE, z)
        candidates = []
        for target in fight._enemies(actor):
            a, b = target.segment()
            point = (target.feet[0], target.feet[1] + target.height * .5, target.feet[2])
            distance = math.dist(muzzle, point)
            if distance <= RANGE and combat.capsule_visible(fight._collision(), muzzle, a, b, target.radius):
                candidates.append((distance, target.entity, point))
        if not candidates:
            continue
        _, _, point = min(candidates)
        dx, dy, dz = combat._sub(point, muzzle)
        actor.yaw = bots.heading(dx, dz)
        actor.pitch = round(-math.atan2(dy, math.hypot(dx, dz)) * 32768 / math.pi)
        instance = next(row for row in script.component.instances.values() if row.graph.index == GUN)
        state = instance.state(1)
        rate = expr.to_float(state.evaluate(state.node.field("m_numShotsPerSecond"), watch=False))
        if rate <= 0:
            continue
        time = fight.tick * script.component.frame_ms
        fight._shoot(actor, None, state, turret.shots, time, activation=(turret.shots, time))
        turret.shots += 1
        turret.next_shot = max(turret.next_shot, fight.now - combat.TICK) + 1 / rate
