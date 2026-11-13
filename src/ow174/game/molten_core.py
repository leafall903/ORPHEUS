"""104319 Molten Core area host with explicitly reconstructed contact/rate policy.

16DD runs its authored preparation/lifetime/FX states;030C.053 resolves16DE.
The ground contact volume, armour-only bonus and same-owner overlap policy are
reconstructed. Native status arbitration, surfaces and protection services remain
separate acceptance requirements.
"""

import logging
import math
from dataclasses import dataclass, field

from ow174.game import observers, projectiles, world
from ow174.game.script import expr, graph, runtime, scopes
from ow174.game.script.bot import BotScript
from ow174.game.statescript import Stream

HERO = 0x02E0000000000006
POOL = 0x0400000000002903
LANDED = 0x024000000000003F
STATUS_START = 0x024000000000000F
GROUND_NORMAL = .707
NORMAL_RESTITUTION = .5  # static approximation of the native rebound response
log = logging.getLogger("ow174.game")


@dataclass
class Pool:
    owner: object
    owner_hero: int
    entity: int
    position: tuple
    config: dict
    script: object
    instance: int
    born: float
    last_update: float
    ground_normal: tuple[float, float, float] = (0.0, 1.0, 0.0)
    arrivals: dict = field(default_factory=dict)
    phase: int = -1
    targets: tuple = ()
    ended: bool = False
    filter_bits: int = 0  # Capture at area birth; late joins must receive the same team metadata.


@dataclass
class Arrival:
    pool: Pool
    viewer: object
    stream: object
    done: bool = False

    def arrived(self, last):
        if self.done or self.pool.ended:
            return
        self.done = True
        self.send()

    def send(self):
        if self.done and not self.pool.ended and self.viewer.client is not None:
            frame = observers.full_frame(self.pool.script, self.pool.script.latest_live)
            self.viewer.client.queue_entities([self.stream.full_frame(frame)])


@dataclass
class Burn:
    owner: object
    target: object
    entity: int
    script: object
    instance: int
    entered: float
    duration: float = 0.0
    scope_entered: bool = False


def create_pool(fight, owner, position, *, ground_normal=(0.0, 1.0, 0.0), source_body=None, born_tick=None):
    cfg = (graph._data().get("deployables") or {}).get("molten_core")
    if not isinstance(cfg, dict) or graph.graph(cfg["status_graph"]) is None:
        raise ValueError("Molten Core requires the explicit prepared053 status bundle")
    position = tuple(position)
    if (len(position) != 3 or any(type(v) not in (int, float) or not math.isfinite(v) for v in position)
            or owner not in fight.match.players or owner.gone or not owner.has_body):
        raise ValueError("Molten Core placement needs a valid owner and finite position")
    entity = projectiles.FIELD_FIRST + fight.projectiles.next_field % projectiles.FIELD_SPAN
    fight.projectiles.next_field += 1
    if entity in fight.molten_pools:
        raise ValueError("Molten Core entity allocation collided with a live pool")
    from ow174.game.combat import TICK

    frame = fight.tick if born_tick is None else born_tick
    born = fight.now - (fight.tick - frame) * TICK
    script = BotScript(POOL, HERO, entity, frame, fight.match.game_map.mode_guid, 1.0)
    instance = script.component.create(graph.graph(cfg["area_graph"]), init=False)
    # The original FX consumer reads Instance+234 through builtin001B.
    # A separately created pool must retain the launcher's source identity.
    instance.set_source_entity(owner.body if source_body is None else source_body, publish_source=True)
    instance.init()
    runtime.send_instance_message(instance, LANDED, {}, owner.body)
    script.component.run_frame(frame)
    script.latest_live = observers.snapshot(script)
    script._produced = script.latest_live
    script._initial_frame = None
    pool = Pool(owner, owner.hero.guid, entity, position, dict(cfg), script, instance.id,
                born, born, ground_normal=ground_normal, filter_bits=owner.team_bit)
    fight.molten_pools[entity] = pool
    _deliver(fight, pool)
    return pool


def _deliver(fight, pool):
    theme = tuple(pool.owner.skin)
    where = pool.position
    components = {26: [pool.filter_bits]}
    for viewer in fight.match.players:
        if viewer.client is None or not viewer.spawned or viewer.slot in pool.arrivals:
            continue
        arrival = Arrival(pool, viewer, Stream(pool.entity))
        pool.arrivals[viewer.slot] = arrival
        create = world.EntityUpdate(
            pool.entity, world.OP_CREATE,
            lambda origin: world.create(origin, POOL, components, position=where, skin=theme),
        )
        create.stream = arrival
        viewer.client.queue_entities([create])


def end_pool(fight, pool):
    if pool.ended:
        return
    pool.ended = True
    fight.molten_pools.pop(pool.entity, None)
    for viewer in fight.match.players:
        if viewer.slot in pool.arrivals and viewer.client is not None:
            viewer.client.queue_entities([world.EntityUpdate(pool.entity, world.OP_DESTROY)])
    for instance in list(pool.script.component.instances.values()):
        pool.script.component.destroy(instance)


def retire_owner(fight, owner):
    """Retire the departing hero's areas and their ability to create new pools."""
    for pool in tuple(fight.molten_pools.values()):
        if pool.owner is owner:
            end_pool(fight, pool)
    for key, burn in tuple(fight.molten_burns.items()):
        if burn.owner is owner:
            _remove_burn(fight, key)
    for rocket in fight.rockets:
        if rocket.shooter is owner and rocket.shot is None and rocket.spawn == POOL:
            rocket.spawn = None


def is_globule(rocket):
    """Keep launched globule physics after hero retirement disables new pool creation."""
    return rocket.spawn == POOL or rocket.source_graph == 0x16DA


def rebound(rocket, point, normal, *, normal_scale=NORMAL_RESTITUTION, tangent_scale=1.0):
    """Scale the tangential velocity and reflect the normal component at a surface."""
    velocity = [axis * rocket.speed for axis in rocket.direction]
    velocity[1] -= rocket.gravity * (rocket.flown - rocket.segment_start)
    normal_speed = sum(v * n for v, n in zip(velocity, normal, strict=True))
    outgoing = tuple(tangent_scale * v - (tangent_scale + normal_scale) * normal_speed * n
                     for v, n in zip(velocity, normal, strict=True))
    rocket.speed = math.hypot(*outgoing)
    if rocket.speed:
        rocket.direction = tuple(axis / rocket.speed for axis in outgoing)
    rocket.position = tuple(p + n * .0001 for p, n in zip(point, normal, strict=True))
    rocket.segment_start = rocket.flown


def projectile_impact(fight, rocket, surface, struck):
    spawn = rocket.spawn if rocket.spawn is not None else getattr(rocket.shot, "spawn", None)
    if spawn != POOL or struck is not None or surface is None or surface.normal[1] < GROUND_NORMAL:
        return
    cfg = (graph._data().get("deployables") or {}).get("molten_core")
    if not isinstance(cfg, dict):
        log.warning("[game] Molten Core area unavailable: selected bundle lacks053 status data")
        return
    if rocket.shooter.gone or rocket.shooter not in fight.match.players:
        return
    from ow174.game.combat import TICK

    born_tick = (rocket.launch_tick + math.ceil(rocket.flown / TICK)
                 if rocket.launch_tick is not None else None)
    create_pool(fight, rocket.shooter, surface.point, ground_normal=surface.normal,
                source_body=rocket.source_body, born_tick=born_tick)


def _contact(fight, pool, target):
    from ow174.game import combat

    if fight.damage_immune(target.owner):
        return False
    x, y, z = pool.position
    tx, ty, tz = target.feet
    # Keep the authored height above the struck surface. The horizontal radius
    # and straight ground visibility remain a reconstruction of 16DD.
    nx, ny, nz = pool.ground_normal
    surface_y = y - (nx * (tx - x) + nz * (tz - z)) / ny
    if not -0.05 <= ty - surface_y <= pool.config["height"]:
        return False
    if math.hypot(tx - x, tz - z) - target.radius > pool.config["radius"]:
        return False
    origin, destination = (x, y + 0.05, z), (tx, ty + 0.05, tz)
    delta = combat._sub(destination, origin)
    distance = math.sqrt(combat._dot(delta, delta))
    if distance <= combat.EPSILON:
        return True
    direction = tuple(axis / distance for axis in delta)
    reach = distance - target.radius
    if reach <= combat.EPSILON:
        return True
    wall = combat.ray_world(fight._collision(), origin, direction, reach)
    if wall is not None and wall < reach:
        return False
    # World cover can cut off the shallow ground path. An attached barrier
    # instead absorbs the protected body's burn interval during settlement.
    return fight._barrier_contact(pool.owner, origin, direction, reach) is None


def _ensure_burn(fight, owner, target, cfg):
    key = owner.entity, target.entity
    burn = fight.molten_burns.get(key)
    script = getattr(target.owner, "body_script", None)
    if burn is None and script is not None:
        instance = script.component.create(graph.graph(cfg["status_graph"]), values={
            (expr.INSTANCE, 27): expr.Entity(owner.body),
            (expr.INSTANCE, 8154): expr.Entity(target.entity),
        })
        runtime.send_instance_message(instance, STATUS_START, {}, owner.body)
        burn = Burn(owner, target.owner, target.entity, script, instance.id, fight.now)
        fight.molten_burns[key] = burn
    return burn


def _remove_burn(fight, key):
    burn = fight.molten_burns.pop(key)
    instance = burn.script.component.instances.get(burn.instance)
    if instance is not None:
        burn.script.component.destroy(instance)


def _activate_burn_scope(burn):
    """Explicit legacy status-host dispatch of the verified BD02E168 interface.

    This chooses a server dispatch policy rather than changing authored plugs or
    pretending the ordinary State begin invokes an auxiliary callback. A real
    registered host retains ownership of its own dispatch.
    """
    if burn is None or burn.scope_entered:
        return False
    instance = burn.script.component.instances.get(burn.instance)
    state = instance.states.get(7) if instance is not None else None
    if state is None or not state.active or state.scope_registry is not None:
        return False
    registry = scopes.Registry()
    registry.register(state)
    state.scope_registry = registry
    registry.enter(state)
    burn.script.component.run_now()
    burn.scope_entered = True
    return True


def _next_pool(vitals):
    """Follow the combined Vitals order so armour-only bonus cannot spill to shields."""
    from ow174.game import combat

    shield = vitals.temporary_shield
    if shield is not None and shield.amount > combat.EPSILON:
        return shield.amount, False
    for kind in (world.SHIELDS_POOL, world.ARMOUR_POOL, world.HEALTH_POOL):
        extra = sum(pool.now for pool in vitals.extra.values() if pool.kind == kind and not pool.removed)
        if extra > combat.EPSILON:
            return extra, kind == world.ARMOUR_POOL
        if kind == world.SHIELDS_POOL and vitals.over_armour.amount > combat.EPSILON:
            return vitals.over_armour.amount, True
        conditional = vitals.conditional_pools.get(kind)
        if conditional is not None and conditional.now > combat.EPSILON:
            return conditional.now, kind == world.ARMOUR_POOL
        if kind == world.ARMOUR_POOL and vitals.temporary_armour > combat.EPSILON:
            return vitals.temporary_armour, True
        if (kind == world.HEALTH_POOL and vitals.wave_health is not None
                and vitals.wave_health.current > combat.EPSILON):
            return vitals.wave_health.current, False
        if vitals.now[kind] > combat.EPSILON:
            return vitals.now[kind], kind == world.ARMOUR_POOL
    return 0.0, False


def _damage_duration(fight, owner, target, cfg, duration):
    from ow174.game import combat

    vitals = fight.vitals_of(target)
    from ow174.game import nano

    scale = nano.offense(fight, owner.body) * fight.orisa.superchargers.scale(owner)
    left = duration
    # Split at actual pool boundaries: bonus damage never leaks into ordinary
    # health or shields when the last armour fraction expires inside a tick.
    while left > combat.EPSILON and not vitals.dead:
        available, armour = _next_pool(vitals)
        rate = cfg["base_dps"] + (cfg["armour_bonus_dps"] if armour else 0.0)
        effective_rate = fight._incoming_damage(target, rate * scale)
        if available <= combat.EPSILON or effective_rate <= 0:
            break
        # Combat applies the scale to damage. Use it here only to time pool exhaustion.
        step = min(left, available / effective_rate)
        fight._damage(owner, target, rate * step, False, int(cfg["loadout"], 16), fight.tick,
                      world_tick=fight.tick, armour_reduction=False)
        left -= step


def update(fight):
    from ow174.game import bots, combat

    contacts = {}
    for pool in list(fight.molten_pools.values()):
        owner, cfg = pool.owner, pool.config
        if (owner.gone or owner.client is None or owner not in fight.match.players
                or owner.hero.guid != pool.owner_hero):
            end_pool(fight, pool)
            continue
        pool.script.advance_live(fight.tick)
        instance = pool.script.component.instances.get(pool.instance)
        state = instance.states.get(5) if instance is not None else None
        armed = pool.born + cfg["arming_seconds"]
        expires = armed + cfg["lifetime_seconds"]
        start, end = max(pool.last_update, armed), min(fight.now, expires)
        was_active = pool.phase == 1
        active = state is not None and state.active
        # The VM can end its scope a tick before the continuous damage deadline.
        # Preserve contact until the final fractional interval has been settled.
        if was_active and expires <= fight.now + combat.TICK + combat.EPSILON:
            active = True
        phase = 1 if active and fight.now < expires else 0
        if phase != pool.phase:
            pool.phase = phase
            for arrival in pool.arrivals.values():
                arrival.send()
        _deliver(fight, pool)
        targets = []
        # Integrate the interval ending at the VM's retirement frame, even
        # though that frame's timer processing has already turned the state off.
        if (active or was_active) and end > start:
            for target in fight._enemies(owner):
                if not _contact(fight, pool, target):
                    continue
                key = owner.entity, target.entity
                targets.append(target.entity)
                row = contacts.setdefault(key, (owner, target, cfg, []))
                row[3].append((start, end))
        pool.targets = tuple(targets)
        if instance is not None:
            instance.write((expr.INSTANCE, 375), tuple(expr.Entity(entity) for entity in targets))
        pool.last_update = fight.now
        if fight.now + combat.EPSILON >= expires or (was_active and not active):
            end_pool(fight, pool)
    for key in tuple(fight.molten_burns):
        if key not in contacts:
            _remove_burn(fight, key)
    for key, (owner, target, cfg, intervals) in contacts.items():
        # An earlier caster can kill a target after the contact list was built.
        if fight.vitals_of(target).dead:
            continue
        merged = []
        for start, end in sorted(intervals):
            if merged and start <= merged[-1][1]:
                merged[-1] = merged[-1][0], max(end, merged[-1][1])
            else:
                merged.append((start, end))
        duration = sum(end - start for start, end in merged)
        bubble = fight.barriers.for_target(target.owner)
        if bubble is not None:
            if key in fight.molten_burns:
                _remove_burn(fight, key)
            fight.barriers.damage(bubble, cfg["base_dps"] * duration, owner)
            continue
        burn = _ensure_burn(fight, owner, target, cfg)
        _activate_burn_scope(burn)
        _damage_duration(fight, owner, target, cfg, duration)
        if fight.vitals_of(target).dead:
            for burn_key in tuple(fight.molten_burns):
                if burn_key[1] == target.entity:
                    _remove_burn(fight, burn_key)
            continue
        if burn is not None:
            burn.duration += duration
            if isinstance(target.owner, bots.Bot) and not target.owner.firing and not target.owner.dead:
                snapshot = burn.script.advance_live(fight.tick, target.owner.position)
                if _activate_burn_scope(burn):
                    snapshot = observers.snapshot(burn.script)
                    burn.script.latest_live = burn.script._produced = snapshot
                    burn.script._initial_frame = None
                burn.script.component.shots.clear()  # a paused NPC must not accumulate stale attack reports
                if snapshot is not None:
                    for viewer in fight.match.players:
                        if viewer.spawned and viewer.client is not None:
                            viewer.client.queue_bot_live(target.entity, snapshot)
        still_contact = any(p.owner is owner and target.entity in p.targets
                            for p in fight.molten_pools.values())
        if not still_contact and key in fight.molten_burns:
            _remove_burn(fight, key)
