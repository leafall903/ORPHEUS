"""Match host for the authored 104319 Molten Core ground and victim graphs."""

import math
from dataclasses import dataclass

from ow174.game import bots, projectiles
from ow174.game.script import expr, graph, runtime
from ow174.game.script.bot import BotScript
from ow174.game.world import ARMOUR_POOL, HEALTH_POOL, SHIELDS_POOL

BODY = 0x0400000000002903
DEATH_GRAPH = 0x2535
DEATH_GLOB = 0x0400000000003900
LAUNCHES = {(0x16DA, 36): 0x0400000000002902, (DEATH_GRAPH, 0): DEATH_GLOB}
PROJECTILES = frozenset(LAUNCHES.values())
AREA, STATUS = 0x16DD, 0x16DE
LOADOUT = 0x0B90000000000227
GROUND_NORMAL = .707
ARMING, LIFETIME = .5, 10
RADIUS, HEIGHT = 2.4, 1
BASE_DPS, ARMOUR_DPS = 160, 250


@dataclass
class Pool:
    owner: object
    owner_hero: int
    entity: int
    position: tuple
    normal: tuple
    script: BotScript
    instance_id: int
    born: float
    previous: float
    armed: bool = False


def play_death_script(instance, node):
    """Handle the authored NPC self-targeted PlayScript for the death effect."""
    if (instance.graph.index, node.pos) != (0x0E62, 117):
        return False
    resource = instance.evaluate(node.field("m_script"))
    target = instance.evaluate(node.field("m_targets"))
    component = instance.component
    definition = graph.graph(DEATH_GRAPH)
    if (resource != expr.Asset(0x0580000000000000 | DEATH_GRAPH)
            or target != expr.Entity(component.entity) or definition is None):
        return False
    component.create(definition)
    return True


def _owner_body(owner):
    return owner.entity if isinstance(owner, bots.Bot) else owner.body


def _owner_hero(owner):
    return owner.hero if isinstance(owner, bots.Bot) else owner.hero.guid


def _owner_present(fight, owner):
    if isinstance(owner, bots.Bot):
        return owner in fight.match.bots
    return owner in fight.match.players and not owner.gone and owner.client is not None


def land(fight, rocket, point, normal):
    owner = rocket.shooter
    if (not _owner_present(fight, owner) or (not isinstance(owner, bots.Bot) and not owner.has_body)
            or _owner_body(owner) != rocket.owner_body
            or graph.graph(AREA) is None or graph.graph(STATUS) is None):
        return
    entity = projectiles.FIELD_FIRST + fight.projectiles.next_field % projectiles.FIELD_SPAN
    fight.projectiles.next_field += 1
    if entity in fight.molten_pools:
        raise ValueError("Molten Core pool identity already in use")
    hero = _owner_hero(owner)
    script = BotScript(BODY, hero, entity, fight.tick, fight.match.game_map.mode_guid, 1.0)
    instance = script.component.create(graph.graph(AREA))
    runtime.send_instance_message(instance, 0x024000000000003F, {}, rocket.owner_body)
    script.component.run_frame(fight.tick)
    fight.molten_pools[entity] = Pool(owner, hero, entity, point, normal,
                                    script, instance.id, fight.now, fight.now)


def _retire_pool(fight, pool):
    del fight.molten_pools[pool.entity]
    instance = pool.script.component.instances.get(pool.instance_id)
    if instance is not None:
        pool.script.component.destroy(instance)


def _retire_burn(fight, key):
    script, instance = fight.molten_burns.pop(key)
    if script.component.instances.get(instance.id) is instance:
        script.component.destroy(instance)


def release_target(fight, entity):
    for key in tuple(fight.molten_burns):
        if key[1] == entity:
            _retire_burn(fight, key)


def _contact(fight, pool, target):
    from ow174.game import combat, sigma

    x, y, z = pool.position
    tx, ty, tz = target.feet
    nx, ny, nz = pool.normal
    surface_y = y - (nx * (tx - x) + nz * (tz - z)) / ny
    if (not -.05 <= ty - surface_y <= HEIGHT
            or math.hypot(tx - x, tz - z) - target.radius > RADIUS
            or sigma.body_protection(target.owner)):
        return False
    source = (x, y + .05, z)
    delta = (tx - x, ty - y, tz - z)
    distance = math.hypot(*delta)
    if distance <= target.radius:
        return True
    direction = tuple(axis / distance for axis in delta)
    reach = distance - target.radius
    return (combat.ray_world(fight._collision(), source, direction, reach) is None
            and sigma.barrier_intersection(fight, pool.owner, source, direction, reach) is None)


def _damage_interval(fight, owner, target, duration):
    from ow174.game import combat, sigma

    if sigma.particle_barrier(target.owner) is not None:
        fight._damage(owner, target, BASE_DPS * duration, False, LOADOUT, fight.tick, world_tick=fight.tick)
        return
    health = fight.vitals_of(target)
    fortify = sigma.fortify(target.owner)
    scale = 1.0 if fortify is None else 1.0 + expr.to_float(fortify.evaluate(fortify.node.field("m_50530EAB")))
    while duration > combat.EPSILON and not health.dead and scale > 0:
        shields = health.now[SHIELDS_POOL] + sum(pool.now for pool in health.temporary_shields.values())
        armour = health.now[ARMOUR_POOL]
        available, rate = ((shields, BASE_DPS) if shields > combat.EPSILON else
                           (armour, ARMOUR_DPS) if armour > combat.EPSILON else
                           (health.now[HEALTH_POOL], BASE_DPS))
        if available <= combat.EPSILON:
            break
        step = min(duration, available / (rate * scale))
        fight._damage(owner, target, rate * step, False, LOADOUT, fight.tick,
                      world_tick=fight.tick, armour_reduction=False)
        duration -= step


def update(fight):
    from ow174.game import sigma

    contacts = {}
    for pool in tuple(fight.molten_pools.values()):
        owner = pool.owner
        if not _owner_present(fight, owner) or _owner_hero(owner) != pool.owner_hero:
            _retire_pool(fight, pool)
            continue
        pool.script.advance_live(fight.tick)
        instance = pool.script.component.instances.get(pool.instance_id)
        state = instance.states.get(5) if instance is not None else None
        armed = bool(state and state.active)
        start = max(pool.previous, pool.born + ARMING)
        expiry = pool.born + ARMING + LIFETIME
        end = min(fight.now, expiry)
        targets = []
        if end > start and (armed or pool.armed):
            for target in fight._enemies(owner):
                if _contact(fight, pool, target):
                    targets.append(expr.Entity(target.entity))
                    row = contacts.setdefault((owner.entity, target.entity), (owner, target, []))
                    row[2].append((start, end))
        if instance is not None:
            instance.write((expr.INSTANCE, 375), tuple(targets))
        if fight.now >= expiry or (pool.armed and not armed):
            _retire_pool(fight, pool)
        pool.armed, pool.previous = armed, fight.now
    for key in tuple(fight.molten_burns):
        if key not in contacts:
            _retire_burn(fight, key)
    for key, (owner, target, intervals) in contacts.items():
        if fight.vitals_of(target).dead:
            continue
        script = target.owner.body_script
        if sigma.particle_barrier(target.owner) is not None:
            if key in fight.molten_burns:
                _retire_burn(fight, key)
        elif key not in fight.molten_burns and script is not None:
            instance = script.component.create(graph.graph(STATUS), values={
                (expr.INSTANCE, 27): expr.Entity(_owner_body(owner)),
                (expr.INSTANCE, 8154): expr.Entity(target.entity),
            })
            runtime.send_instance_message(instance, 0x024000000000000F, {}, _owner_body(owner))
            fight.molten_burns[key] = script, instance
        duration = 0.0
        covered_until = -math.inf
        for start, end in sorted(intervals):
            duration += max(0, end - max(start, covered_until))
            covered_until = max(end, covered_until)
        _damage_interval(fight, owner, target, duration)
        if fight.vitals_of(target).dead:
            release_target(fight, target.entity)
