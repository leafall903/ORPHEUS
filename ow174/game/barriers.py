"""Scoped Reinhardt and Zarya Pet hosts with independent shield pools.

This runs the actual self-owned entity/script boundary rather than treating a
held button as an already existing shield. Native actor allocation, attachment
transforms, unit scaling and wire creation still require separate acceptance.
"""

import math
from dataclasses import dataclass

from ow174.game import barrier_geometry, stats
from ow174.game.script import expr, graph, play, play_queue, runtime

REINHARDT = 0x02E0000000000007
ZARYA = 0x02E0000000000068
MEI = 0x02E00000000000DD
WINSTON = 0x02E0000000000009
DOME_RADIUS = 5.0  # 01C3 D02 sphere
CRYO_RADIUS = 0.5  # 090B collider
CRYO_HEIGHT = 2.0
FIRST_ENTITY = 0xC1000001  # reconstructed server proxy namespace; collision checked on allocation
ANCHOR_FORWARD = 1.0  # reconstructed raised pose, not the native bone008C transform
ANCHOR_HEIGHT = 1.8  # actual body0024 FirstPerson height, used as a reconstructed anchor
ANCHOR_CROUCHED_HEIGHT = 1.26


@dataclass
class Barrier:
    entity: int
    parent: object
    parent_body: int
    source: runtime.Component
    instance: int
    state: int
    activation: int
    component: runtime.Component
    mesh: tuple = ()
    geometry_digest: str | None = None
    vitals: object = None
    retired: bool = False
    recipient: object = None
    recipient_body: int = 0
    recipient_component: runtime.Component | None = None
    centre: tuple | None = None
    expires: int | None = None


def _recipient_ready(fight, player):
    script = player.body_script
    return (player.has_body and player.spawned and not player.gone and player.client is not None
            and script is not None and not script.retired and not fight.is_dead(player))


def cryo_expiry(instance):
    # 05FE ChaseVar 6 chases the authored lifetime to zero at the default 1/sec.
    chase = instance.states.get(6)
    if chase is None or not chase.active:
        return None
    lifetime = expr.to_float(chase.evaluate(chase.node.field("m_EB0FC261"), watch=False))
    if math.isfinite(lifetime) and lifetime > 0:
        return (chase.start + round(lifetime * 1000)) // instance.component.frame_ms
    return None


def advance_cryo_heal(fight, previous_tick):
    """Settle the 05FE self-heal over its own authored world-clock window."""
    for player in fight.match.players:
        if player.hero.guid != MEI or not _recipient_ready(fight, player):
            continue
        script = player.body_script
        instance = next((i for i in script.component.instances.values() if i.graph.index == 0x5FE), None)
        if instance is None or instance.teardown:
            continue
        state = instance.states.get(13)
        if state is None or not state.active or state.node.pos != 27:
            continue
        recipient = state.evaluate(state.node.field("m_FA3ECC43"), watch=False)
        if expr.to_entity(recipient) != player.body:
            continue
        seconds = expr.to_float(state.evaluate(state.node.field("m_95F1FB0B"), watch=False))
        if not math.isfinite(seconds) or seconds <= 0:
            continue
        duration = round(seconds * 1000)
        since = max(previous_tick * script.frame_ms, state.start)
        until = min(fight.tick * script.frame_ms, state.start + duration)
        if until <= since:
            continue
        target = next((t for t in fight._targets() if t.owner is player), None)
        if target is None:
            continue
        budget = expr.to_float(state.evaluate(state.node.field("m_modifyHealth.m_amount"), watch=False))
        vitals = fight.vitals_of(target)
        given = vitals.heal(budget * (until - since) / duration)
        if given > 0:
            player.stats.add(stats.HEALING, given)
            fight._sync_body_health(player, vitals)


def track_projected_target(world, state):
    """Supply 025C with the closest visible allied player along the current aim."""
    if (world.hero, state.instance.graph.index, state.index) != (ZARYA, 0x25C, 7):
        return
    match = world.match
    output = expr.lvalue(state.node.field("m_8A2905A0"))
    if match is None or output is None:
        return
    from ow174.game.combat import aim, ray_capsule, ray_world

    fight = match.combat
    source = next((p for p in match.players if p.has_body and p.body == world.body), None)
    found = ()
    if (source is not None and _recipient_ready(fight, source)
            and source.body_script.component is state.instance.component
            and match.collision_world is not None and not match.game_map.free_for_all):
        origin = fight._eye(source)
        direction = aim(source.mover.yaw, source.mover.pitch)
        reach = expr.to_float(state.evaluate(state.node.field("m_radius"), watch=False))
        wall = ray_world(match.collision_world, origin, direction, reach)
        closest = math.inf if wall is None else wall
        for target in fight._targets():
            ally = target.owner
            if (ally is source or ally not in match.players or ally.team != source.team
                    or not _recipient_ready(fight, ally)):
                continue
            a, b = target.segment()
            distance = ray_capsule(origin, direction, a, b, target.radius)
            if distance is not None and distance <= reach and distance < closest:
                closest, found = distance, (expr.Entity(target.entity),)
    state.instance.write(output, found)


def create(world, state):
    signature = world.hero, state.instance.graph.index, state.index
    if signature == (REINHARDT, 0x95, 17):
        entity_definition, pool_graph = 0x040000000000011A, 0x96
    elif signature == (ZARYA, 0x244, 2):
        entity_definition, pool_graph = 0x04000000000013F4, 0x256
    elif signature == (ZARYA, 0x25C, 10):
        entity_definition, pool_graph = 0x04000000000003DA, 0x256
    elif signature == (MEI, 0x5FE, 11):
        entity_definition, pool_graph = 0x040000000000090B, 0x623
    else:
        return 0
    if (world.match is None
            or expr.guid_of(state.node.field("m_entityDefinition.m_entityDef")) != entity_definition):
        return 0
    fight = world.match.combat
    owner = next((p for p in world.match.players if p.has_body and p.body == world.body), None)
    if (owner is None or owner.gone or owner.body_script is None or owner.body_script.retired
            or owner.body_script.component is not state.instance.component or fight.is_dead(owner)):
        return 0
    recipient = None
    if signature == (ZARYA, 0x25C, 10):
        variable = state.instance.vars.get(536)
        target = expr.to_entity(variable.value()) if variable is not None else 0
        recipient = next((p for p in world.match.players if p.body == target and p is not owner), None)
        if (recipient is None or world.match.game_map.free_for_all or recipient.team != owner.team
                or not _recipient_ready(fight, recipient)):
            return 0
    definition = graph.graph(0x25B4) if world.hero == REINHARDT else None
    if (world.hero == REINHARDT and definition is None) or graph.graph(pool_graph) is None:
        return 0
    mesh, digest = barrier_geometry.load() if definition is not None else ((), None)
    expires = None
    if world.hero == ZARYA:
        # Both authored bubble chases start with the lifetime in variable 567.
        lifetime = state.instance.vars.get(567)
        duration = expr.to_float(lifetime.value()) if lifetime is not None else 0
        if math.isfinite(duration) and duration > 0:
            expires = (state.start + round(duration * 1000)) // state.instance.component.frame_ms
    elif world.hero == MEI:
        expires = cryo_expiry(state.instance)
    if expires is not None and expires <= fight.tick:
        return 0
    barrier = _allocate(world, state.instance, owner, state.index, state.counter)
    component = barrier.component
    barrier.mesh, barrier.geometry_digest, barrier.expires = mesh, digest, expires
    if recipient is not None:
        barrier.recipient = recipient
        barrier.recipient_body = recipient.body
        barrier.recipient_component = recipient.body_script.component
    if definition is not None:
        # 011A's initial script comes from the external entity XML.
        initial = component.create(definition, init=False)
        initial.set_source_entity(owner.body)
        initial.init()
        component.run_now()
    return barrier.entity


def _allocate(world, instance, owner, state=-1, activation=0):
    from ow174.game.script.driver import BodyWorld

    fight = world.match.combat
    occupied = ({p.body for p in world.match.players}
                | {b.entity for b in world.match.bots} | set(fight.barriers) | set(fight.mines.entities))
    while fight.next_barrier in occupied:
        fight.next_barrier += 1
    entity, fight.next_barrier = fight.next_barrier, fight.next_barrier + 1
    child_world = BodyWorld(world.hero, world.mode, entity)
    child_world.match = world.match
    source = instance.component
    component = runtime.Component(entity, source.frame_ms, child_world)
    component.frame, component.now = source.frame, source.now
    barrier = Barrier(entity, owner, owner.body, source, instance.id, state, activation, component)
    fight.barriers[entity] = barrier
    return barrier


def create_dome(world, instance, node):
    if world.match is None or (world.hero, instance.graph.index, node.pos) != (WINSTON, 0xFA, 26):
        return NotImplemented
    if expr.guid_of(node.field("m_entityDefinition.m_entityDef")) != 0x04000000000001C3:
        return NotImplemented
    output = expr.lvalue(node.field("m_B3019338"))
    if output is None:
        return NotImplemented
    owner = next((p for p in world.match.players if p.has_body and p.body == world.body), None)
    fight = world.match.combat
    entity = 0
    if (graph.graph(0xFC) is not None and owner is not None
            and _recipient_ready(fight, owner) and owner.body_script.component is instance.component):
        dome = _allocate(world, instance, owner)
        x, y, z = owner.mover.position
        dome.centre = (x, y + 0.1, z)
        entity = dome.entity
    instance.write(output, expr.Entity(entity))
    return True


def destroy_dome(world, instance, node):
    if (world.match is None or (world.hero, instance.graph.index) != (WINSTON, 0xFC)
            or node.pos not in (12, 28)):
        return NotImplemented
    dome = world.match.combat.barriers.get(world.body)
    if dome is None or dome.component is not instance.component or dome.centre is None:
        return NotImplemented
    dome.retired = True  # retire outside child dispatch
    return True


def retire(fight, entity):
    barrier = fight.barriers.pop(entity, None)
    if barrier is None:
        return
    barrier.retired = True
    for instance in list(barrier.component.instances.values()):
        barrier.component.destroy(instance)


def retire_owner(fight, owner):
    for barrier in list(fight.barriers.values()):
        if barrier.parent is owner and barrier.parent_body == owner.body:
            retire(fight, barrier.entity)
        elif barrier.recipient is owner and barrier.recipient_body == owner.body:
            _end_bubble(fight, barrier)


def _end_bubble(fight, barrier):
    instance = barrier.source.instances.get(barrier.instance)
    state = instance.states.get(barrier.state) if instance is not None else None
    if state is not None and state.active and state.counter == barrier.activation:
        state.request(runtime.ABORT)
        barrier.source.run_now()
    retire(fight, barrier.entity)


def play_script(world, instance, node):
    signature = world.hero, instance.graph.index, node.pos
    if world.match is None:
        return
    if signature == (REINHARDT, 0x95, 6):
        pool_graph = 0x96
    elif signature in ((ZARYA, 0x244, 9), (ZARYA, 0x25C, 27)):
        pool_graph = 0x256
    elif signature == (MEI, 0x5FE, 22):
        pool_graph = 0x623
    elif signature == (WINSTON, 0xFA, 2):
        pool_graph = 0xFC
    else:
        return
    resource = play.resolve_resource(instance, node)
    definition = graph.graph(pool_graph)
    if resource is None or resource.guid != (0x0580000000000000 | pool_graph) or definition is None:
        return
    request = play.resolve_request(
        instance, node, resource,
        context_provider=lambda: play.InvocationContext(world.body, world.body, instance.source_entity()),
    )
    if request is None or len(request.targets) != 1:
        return
    fight = world.match.combat
    barrier = fight.barriers.get(request.targets[0])
    if (barrier is None or barrier.retired or barrier.parent_body != world.body
            or barrier.source is not instance.component):
        return
    component = barrier.component
    component.frame, component.now = instance.component.frame, instance.component.now
    queue = play_queue.Queue(component, lambda guid: play_queue.Load("ready", definition))
    queue.submit(request)
    if queue.pending:
        raise ValueError("ready barrier call left pending work")
    component.run_now()
    if pool_graph == 0xFC and component.frame < fight.tick:
        # A late cast starts the independent dome VM on its original clock.
        component.run_frame(fight.tick)
        if barrier.retired:
            retire(fight, barrier.entity)
            return
    # Player commands arrive after the current world tick's healing.
    _init_health(fight, barrier, fight.tick + 1)


def health_query(world, instance, cfg):
    if (world.match is None or world.hero != REINHARDT or instance.graph.index != 0x95
            or (cfg.get("m_7E8B666C") or {}).get("m_7E8B666C") != 0):
        return NotImplemented
    target = instance.evaluate(cfg.get("m_entity"))
    barrier = world.match.combat.barriers.get(expr.to_entity(target))
    if barrier is None or barrier.parent_body != world.body:
        return NotImplemented
    return barrier.vitals.total if barrier.vitals is not None else 0.0


def advance(fight, tick):
    for barrier in list(fight.barriers.values()):
        if not _live(fight, barrier):
            if not _recipient_live(fight, barrier):
                _end_bubble(fight, barrier)
            else:
                retire(fight, barrier.entity)
            continue
        component = barrier.component
        if tick > component.frame:
            component.run_frame(tick)
        else:
            component.run_now()
        if barrier.retired:
            retire(fight, barrier.entity)
            continue
        _init_health(fight, barrier, tick)


def _init_health(fight, barrier, tick):
    from ow174.game.combat import Vitals

    component = barrier.component
    if barrier.vitals is not None or component.world.hero == MEI:
        return
    bubble = component.world.hero == ZARYA
    if barrier.centre is not None:
        graph_index, state_index = 0xFC, 0
    else:
        graph_index, state_index = (0x256, 0) if bubble else (0x96, 1)
    pool = next(
        (i.state(state_index) for i in component.instances.values() if i.graph.index == graph_index), None,
    )
    if pool is not None and pool.active:
        if bubble and expr.to_entity(pool.instance.vars[2390].value()) != _protected_body(barrier):
            return
        maximum = expr.to_float(pool.evaluate(pool.node.field("m_amount"), watch=False))
        current = maximum
        if not bubble and barrier.centre is None:
            current = expr.to_float(pool.evaluate(pool.node.field("m_BCFDD2E9"), watch=False))
        vitals = barrier.vitals = Vitals(barrier.entity, maximum)
        vitals.now[0] = max(0.0, min(maximum, current))
        component.set(253, vitals.total)
        if bubble and vitals.total > 0 and barrier.recipient is not None:
            fight.rifle_doses[:] = [dose for dose in fight.rifle_doses
                                    if dose.healing or dose.recipient != barrier.recipient_body]
            recipient_vitals = fight.vitals.get(barrier.recipient_body)
            if recipient_vitals is not None:
                recipient_vitals.cleanse_healing_block(tick)


def _protected_body(barrier):
    return barrier.recipient_body or barrier.parent_body


def _recipient_live(fight, barrier):
    player = barrier.recipient
    return (player is None or (player in fight.match.players and player.body == barrier.recipient_body
                              and player.team == barrier.parent.team and not fight.match.game_map.free_for_all
                              and _recipient_ready(fight, player)
                              and player.body_script.component is barrier.recipient_component))


def _live(fight, barrier):
    owner = barrier.parent
    instance = barrier.source.instances.get(barrier.instance)
    state = instance.states.get(barrier.state) if instance is not None else None
    return (not barrier.retired and (barrier.expires is None or fight.tick < barrier.expires)
            and not owner.gone and owner.has_body
            and owner.body == barrier.parent_body and owner.body_script is not None
            and not owner.body_script.retired and owner.body_script.component is barrier.source
            and not fight.is_dead(owner)
            and (barrier.centre is not None or (state is not None and state.active
                                               and state.counter == barrier.activation))
            and _recipient_live(fight, barrier))


def _dome_ray(centre, origin, direction):
    offset = tuple(a - b for a, b in zip(origin, centre, strict=True))
    projection = barrier_geometry._dot(offset, direction)
    radial = barrier_geometry._dot(offset, offset) - DOME_RADIUS ** 2
    discriminant = projection ** 2 - radial
    if discriminant < 0:
        return None
    root = math.sqrt(discriminant)
    distance = -projection + root if radial < 0 else -projection - root
    return distance if distance >= 0 else None


def ray_hit(fight, shooter, origin, direction, length):
    """Closest live enemy shield mesh.

    Current owner pose is used for both hitscan and projectiles; native shield
    history/attachment, scale, beam and splash receiver filters remain open.
    """
    best = None
    for barrier in fight.barriers.values():
        owner = barrier.parent
        if (owner is shooter or not _live(fight, barrier)
                or (not fight.match.game_map.free_for_all and owner.team == shooter.team)):
            continue
        if barrier.component.world.hero == MEI:
            from ow174.game.combat import ray_capsule

            x, y, z = owner.mover.position
            a, b = (x, y + CRYO_RADIUS, z), (x, y + CRYO_HEIGHT - CRYO_RADIUS, z)
            distance = ray_capsule(origin, direction, a, b, CRYO_RADIUS)
            if distance is not None and distance <= length and (best is None or distance < best[0]):
                best = distance, barrier
            continue
        if barrier.vitals is None or barrier.vitals.total <= 0:
            continue
        if barrier.centre is not None:
            distance = _dome_ray(barrier.centre, origin, direction)
            if distance is not None and distance <= length and (best is None or distance < best[0]):
                best = distance, barrier
            continue
        if not barrier.mesh:
            continue
        mover = owner.mover
        yaw, pitch = mover.yaw * math.tau / 65536, mover.pitch * math.tau / 65536
        sy, cy, sp, cp = math.sin(yaw), math.cos(yaw), math.sin(pitch), math.cos(pitch)
        basis = ((cy, 0, -sy), (sy*sp, cp, cy*sp), (sy*cp, -sp, cy*cp))
        height = ANCHOR_CROUCHED_HEIGHT if mover.crouched else ANCHOR_HEIGHT
        anchor = tuple(mover.position[k] + (height if k == 1 else 0)
                       + basis[2][k] * ANCHOR_FORWARD for k in range(3))
        delta = tuple(origin[k] - anchor[k] for k in range(3))
        local_origin = tuple(barrier_geometry._dot(delta, axis) for axis in basis)
        local_direction = tuple(barrier_geometry._dot(direction, axis) for axis in basis)
        distance = barrier_geometry.ray_mesh(barrier.mesh, local_origin, local_direction, length)
        if distance is not None and (best is None or distance < best[0]):
            best = distance, barrier
    return best


def projected_on(fight, body):
    """Yield live projected bubbles attached to this exact recipient body."""
    for barrier in fight.barriers.values():
        if barrier.recipient_body == body and is_live_projected(fight, barrier):
            yield barrier


def is_live_projected(fight, barrier):
    return (barrier.recipient is not None and barrier.component.world.hero == ZARYA
            and barrier.vitals is not None and barrier.vitals.total > 0 and _live(fight, barrier))


def protect_body(fight, target, amount, *, attacker, source_scale=None):
    """Consume damage addressed to a body protected by a live Zarya bubble."""
    for barrier in fight.barriers.values():
        protected = barrier.recipient if barrier.recipient is not None else barrier.parent
        if (protected is target.owner and _protected_body(barrier) == target.entity
                and barrier.component.world.hero == ZARYA and barrier.vitals is not None
                and barrier.vitals.total > 0 and _live(fight, barrier)):
            scale = fight.outgoing_damage_scale(attacker) if source_scale is None else source_scale
            damage(fight, barrier, amount * scale, attacker=attacker)
            return True
    return False


def relay_bubble_energy(world, target, message, params, sender):
    """Dispatch the 0256 energy message to the caster's live 0242 instance."""
    if world.match is None or world.hero != ZARYA or sender != world.body or message != 0x0240000000000146:
        return False
    fight = world.match.combat
    bubble = fight.barriers.get(sender)
    if (bubble is None or bubble.component.world is not world or bubble.parent_body != target
            or not _live(fight, bubble)):
        return False
    receiver = next((i for i in bubble.source.instances.values() if i.graph.index == 0x242), None)
    if receiver is not None:
        runtime.send_instance_message(receiver, message, params, sender)
    return True


def damage(fight, barrier, amount, *, attacker=0):
    """Bridge independent HealthPool damage into the authored owner or child graph.

    Reinhardt receives 0053 on break. Zarya's 0256 receives effective damage,
    then the death flag and a scoped Pet abort on break. Native damage categories,
    cross-component dispatch order and wire remain open.
    """
    if not _live(fight, barrier) or barrier.vitals is None or amount <= 0:
        return 0.0
    dealt = barrier.vitals.damage(amount)
    remaining = barrier.vitals.total
    barrier.component.set(253, remaining)
    if barrier.centre is not None:
        if remaining <= 0:
            barrier.component.set(478, True)
        barrier.component.run_now()
        return dealt
    owner_script = barrier.source.instances[barrier.instance]
    if barrier.component.world.hero == ZARYA:
        child = next((i for i in barrier.component.instances.values() if i.graph.index == 0x256), None)
        if child is not None and dealt > 0 and attacker:
            params = {0x13: expr.f32(dealt), 0x12: expr.Entity(attacker)}
            runtime.send_instance_message(child, 0x0240000000000011, params, attacker)
            barrier.component.run_now()
            barrier.source.run_now()
        if remaining <= 0:
            barrier.component.set(478, True)
            barrier.component.run_now()
            _end_bubble(fight, barrier)
        return dealt
    owner_script.write((runtime.INSTANCE_SCOPE, 586), expr.f32(remaining))
    if remaining <= 0:
        runtime.send_instance_message(owner_script, 0x0240000000000053, {}, barrier.entity)
    barrier.source.run_now()
    if not _live(fight, barrier):
        retire(fight, barrier.entity)
    return dealt
