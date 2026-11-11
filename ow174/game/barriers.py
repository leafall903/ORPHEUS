"""Scoped Reinhardt, Zarya and Mei defensive Pet hosts.

This runs the actual self-owned entity/script boundary rather than treating a
held button as an already existing shield. Native actor allocation, attachment
transforms, unit scaling and wire creation still require separate acceptance.
"""

import math
from dataclasses import dataclass

from ow174.game import barrier_geometry
from ow174.game.script import expr, graph, play, play_queue, runtime

REINHARDT = 0x02E0000000000007
ZARYA = 0x02E0000000000068
MEI = 0x02E00000000000DD
ZENYATTA = 0x02E0000000000020
CRYO_RADIUS = 0.5  # definition 090B, STU_BE9A0B3E
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


def track_projected_target(world, state):
    """Feed 025C's cast transition with a visible allied body under the crosshair."""
    if (world.hero, state.instance.graph.index, state.index) != (ZARYA, 0x025C, 7):
        return
    if world.match is None:
        return
    from ow174.game.combat import aim, ray_capsule, ray_world

    fight = world.match.combat
    source = next((p for p in world.match.players if p.body == world.body and p.has_body), None)
    output = expr.lvalue(state.node.field("m_8A2905A0"))
    found = ()
    if (output is not None and source is not None and source.body_script is not None
            and source.body_script.component is state.instance.component
            and world.match.collision_world is not None and not world.match.game_map.free_for_all):
        origin = fight._eye(source)
        direction = aim(source.mover.yaw, source.mover.pitch)
        reach = expr.to_float(state.evaluate(state.node.field("m_radius"), watch=False))
        wall = ray_world(world.match.collision_world, origin, direction, reach)
        closest = math.inf if wall is None else wall
        for target in fight._targets():
            ally = target.owner
            if (ally is source or ally not in world.match.players or target.team != source.team
                    or not _recipient_ready(fight, ally)):
                continue
            a, b = target.segment()
            distance = ray_capsule(origin, direction, a, b, target.radius)
            if distance is not None and distance <= reach and distance < closest:
                closest, found = distance, (expr.Entity(target.entity),)
    if output is not None:
        state.instance.write(output, found)


def _recipient_ready(fight, player):
    script = player.body_script
    return (player.has_body and player.spawned and not player.gone and player.client is not None
            and script is not None and not script.retired and not fight.is_dead(player))


def cryo_heal(world, state, elapsed_ms):
    """Apply 05FE's self-heal budget over its evaluated ModifyHealth duration.

    The authored duration is 567 minus 0.1 seconds. Distributing the
    budget over elapsed VM time is a reconstructed server policy.
    """
    instance = state.instance
    if (world.hero, instance.graph.index, state.node.pos) != (MEI, 0x05FE, 27) or world.match is None:
        return NotImplemented
    if elapsed_ms <= 0 or instance.teardown:
        return True
    fight = world.match.combat
    target = next((t for t in fight._targets() if t.entity == world.body), None)
    if (target is None or not _recipient_ready(fight, target.owner)
            or target.owner.body_script.component is not instance.component
            or expr.to_entity(state.evaluate(state.node.field("m_FA3ECC43"), watch=False)) != world.body):
        return True
    duration = round(expr.to_float(state.evaluate(state.node.field("m_95F1FB0B"), watch=False)) * 1000)
    if duration <= 0:
        return True
    elapsed = max(0, state.now - state.start)
    applied = min(duration, elapsed) - min(duration, max(0, elapsed - elapsed_ms))
    budget = expr.to_float(state.evaluate(state.node.field("m_modifyHealth.m_amount"), watch=False))
    vitals = fight.vitals_of(target)
    fight._heal_target(target.owner, target, vitals, budget * applied / duration)
    return True


def _transcendence_owner(world, instance):
    fight = world.match.combat
    owner = next((p for p in world.match.players if p.body == world.body), None)
    if (owner is not None and _recipient_ready(fight, owner)
            and owner.body_script.component is instance.component):
        return owner
    return None


def track_transcendence(world, state):
    """Feed 017B's live ally tuple with a reconstructed capsule range and LOS rule."""
    instance = state.instance
    if (world.hero, instance.graph.index, state.node.pos) != (ZENYATTA, 0x017B, 5) or world.match is None:
        return False
    from ow174.game.combat import capsule_visible, segment_distance

    fight = world.match.combat
    owner = _transcendence_owner(world, instance)
    output = expr.lvalue(state.node.field("m_8A2905A0"))
    selected = []
    collision = world.match.collision_world
    if owner is not None and not world.match.game_map.free_for_all and collision is not None:
        centre = fight._eye(owner)
        radius = expr.to_float(state.evaluate(state.node.field("m_radius"), watch=False))
        for target in fight._targets():
            if target.owner is owner or target.team != owner.team or fight.vitals_of(target).dead:
                continue
            a, b = target.segment()
            if (segment_distance(centre, a, b) - target.radius <= radius
                    and capsule_visible(collision, centre, a, b, target.radius)):
                selected.append(expr.Entity(target.entity))
    targets = tuple(selected)
    if output is not None and instance.find_var(*output).value() != targets:
        instance.write(output, targets)
    return True


def transcendence_heal(world, state, elapsed_ms):
    """Apply the authored 017B healing budget over VM time to the tracked allies."""
    instance = state.instance
    if (world.hero, instance.graph.index, state.node.pos) != (ZENYATTA, 0x017B, 12) or world.match is None:
        return NotImplemented
    phase = instance.states.get(31)
    if elapsed_ms <= 0 or instance.teardown or phase is None or not phase.active:
        return True
    owner = _transcendence_owner(world, instance)
    targets = state.evaluate(state.node.field("m_FA3ECC43"), watch=False)
    if owner is None or world.match.game_map.free_for_all or not isinstance(targets, tuple):
        return True
    duration = expr.to_float(state.evaluate(state.node.field("m_95F1FB0B"), watch=False))
    budget = expr.to_float(state.evaluate(state.node.field("m_modifyHealth.m_amount"), watch=False))
    if duration <= 0 or budget <= 0:
        return True
    selected = {expr.to_entity(target) for target in targets}
    fight = world.match.combat
    for target in fight._targets():
        if target.entity in selected and target.owner is not owner and target.team == owner.team:
            vitals = fight.vitals_of(target)
            if not vitals.dead:
                fight._heal_target(owner, target, vitals, budget * elapsed_ms / (duration * 1000))
    return True


def create(world, state):
    signature = world.hero, state.instance.graph.index, state.index
    if signature == (REINHARDT, 0x0095, 17):
        entity_definition, startup, pool_graph = 0x040000000000011A, 0x25B4, 0x96
    elif signature == (ZARYA, 0x0244, 2):
        entity_definition, startup, pool_graph = 0x04000000000013F4, None, 0x256
    elif signature == (ZARYA, 0x025C, 10):
        entity_definition, startup, pool_graph = 0x04000000000003DA, None, 0x256
    elif signature == (MEI, 0x05FE, 11):
        entity_definition, startup, pool_graph = 0x040000000000090B, None, 0x623
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
    if signature == (ZARYA, 0x025C, 10):
        variable = state.instance.vars.get(536)
        target = expr.to_entity(variable.value()) if variable is not None else 0
        recipient = next((p for p in world.match.players if p.body == target and p is not owner), None)
        if (recipient is None or world.match.game_map.free_for_all or recipient.team != owner.team
                or not _recipient_ready(fight, recipient)):
            return 0
    from ow174.game.script.driver import BodyWorld

    definition = graph.graph(startup) if startup is not None else None
    if (startup is not None and definition is None) or graph.graph(pool_graph) is None:
        return 0
    occupied = ({p.body for p in world.match.players}
                | {b.entity for b in world.match.bots} | set(fight.barriers))
    while fight.next_barrier in occupied:
        fight.next_barrier += 1
    entity, fight.next_barrier = fight.next_barrier, fight.next_barrier + 1
    child_world = BodyWorld(world.hero, world.mode, entity)
    child_world.match = world.match
    component = runtime.Component(entity, state.instance.component.frame_ms, child_world)
    component.frame, component.now = state.instance.component.frame, state.now
    barrier = Barrier(entity, owner, owner.body, state.instance.component,
                      state.instance.id, state.index, state.counter, component)
    if recipient is not None:
        barrier.recipient = recipient
        barrier.recipient_body = recipient.body
        barrier.recipient_component = recipient.body_script.component
    if world.hero == REINHARDT:
        barrier.mesh, barrier.geometry_digest = barrier_geometry.load()
    fight.barriers[entity] = barrier
    #011A's actual initial script, recovered from the external entity XML.
    if definition is not None:
        initial = component.create(definition, init=False)
        initial.set_source_entity(owner.body)
        initial.init()
        component.run_now()
    return entity


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
    if instance is not None:
        state = instance.states.get(barrier.state)
        if state is not None and state.active and state.counter == barrier.activation:
            state.request(runtime.ABORT)
            barrier.source.run_now()
    retire(fight, barrier.entity)


def play_script(world, instance, node):
    signature = world.hero, instance.graph.index, node.pos
    if signature == (REINHARDT, 0x95, 6):
        pool_graph = 0x96
    elif signature in ((ZARYA, 0x244, 9), (ZARYA, 0x25C, 27)):
        pool_graph = 0x256
    elif signature == (MEI, 0x05FE, 22):
        pool_graph = 0x623
    else:
        return
    if world.match is None:
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
    barrier = world.match.combat.barriers.get(request.targets[0])
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
    _init_health(world.match.combat, barrier)


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
            if barrier.recipient is not None and not _recipient_live(fight, barrier):
                _end_bubble(fight, barrier)
            else:
                retire(fight, barrier.entity)
            continue
        component = barrier.component
        if tick > component.frame:
            component.run_frame(tick)
        else:
            component.run_now()
        _init_health(fight, barrier)


def _init_health(fight, barrier):
    from ow174.game.combat import Vitals

    if barrier.vitals is not None or barrier.component.world.hero == MEI:
        return
    component = barrier.component
    bubble = component.world.hero == ZARYA
    pool_graph, index = (0x256, 0) if bubble else (0x96, 1)
    pool = next((i.state(index) for i in component.instances.values() if i.graph.index == pool_graph), None)
    if pool is not None and pool.active:
        protected_body = barrier.recipient_body or barrier.parent_body
        if bubble and expr.to_entity(pool.instance.vars[2390].value()) != protected_body:
            return
        maximum = expr.to_float(pool.evaluate(pool.node.field("m_amount"), watch=False))
        current = maximum
        if not bubble:
            current = expr.to_float(pool.evaluate(pool.node.field("m_BCFDD2E9"), watch=False))
        vitals = barrier.vitals = Vitals(barrier.entity, maximum)
        vitals.now[0] = max(0.0, min(maximum, current))
        component.set(253, vitals.total)
        if barrier.recipient is not None and vitals.total > 0 and _recipient_live(fight, barrier):
            # Reconstructed sleep cleanse on successful projected protection, once per Pet.
            recipient = fight.vitals.get(protected_body)
            if recipient is not None:
                recipient.clear_sleep()


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
    return (not barrier.retired and not owner.gone and owner.has_body
            and owner.body == barrier.parent_body and owner.body_script is not None
            and not owner.body_script.retired and owner.body_script.component is barrier.source
            and not fight.is_dead(owner) and state is not None and state.active
            and state.counter == barrier.activation and _recipient_live(fight, barrier))


def ray_hit(fight, shooter, origin, direction, length, *, padding=0.0):
    """Closest live enemy defensive Pet using its reconstructed collision.

    Current owner pose is used for both hitscan and projectiles; native shield
    history/attachment, scale, beam and splash receiver filters remain open.
    """
    best = None
    for barrier in fight.barriers.values():
        owner = barrier.recipient if barrier.recipient is not None else barrier.parent
        if (owner is shooter or not _live(fight, barrier)
                or (not fight.match.game_map.free_for_all and owner.team == shooter.team)):
            continue
        if barrier.component.world.hero == MEI:
            from ow174.game.combat import ray_capsule

            x, y, z = owner.mover.position
            a, b = (x, y + CRYO_RADIUS, z), (x, y + CRYO_HEIGHT - CRYO_RADIUS, z)
            distance = ray_capsule(origin, direction, a, b, CRYO_RADIUS + padding)
            if distance is not None and distance <= length and (best is None or distance < best[0]):
                best = distance, barrier
            continue
        if barrier.vitals is None or barrier.vitals.total <= 0:
            continue
        if barrier.component.world.hero == ZARYA:
            from ow174.game.combat import ray_capsule

            target = next((t for t in fight._targets() if t.owner is owner), None)
            if target is not None:
                a, b = target.segment()
                distance = ray_capsule(origin, direction, a, b, target.radius + padding)
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
        distance = barrier_geometry.ray_mesh(barrier.mesh, local_origin, local_direction, length,
                                            padding=padding)
        if distance is not None and (best is None or distance < best[0]):
            best = distance, barrier
    return best


def protect_body(fight, target, amount, *, attacker):
    """Route body-directed damage to a live bubble, consuming the impact."""
    for barrier in fight.barriers.values():
        protected = barrier.recipient if barrier.recipient is not None else barrier.parent
        if (protected is target.owner and (barrier.recipient_body or barrier.parent_body) == target.entity
                and barrier.component.world.hero == ZARYA and barrier.vitals is not None
                and barrier.vitals.total > 0 and _live(fight, barrier)):
            damage(fight, barrier, amount, attacker=attacker)
            return True
    return False


def relay_bubble_energy(world, target, message, params, sender):
    """Route the child 0256 energy message to the live caster's 0242 entry."""
    if world.match is None or world.hero != ZARYA or sender != world.body or message != 0x0240000000000146:
        return False
    fight = world.match.combat
    bubble = fight.barriers.get(sender)
    if (bubble is None or bubble.component.world is not world or bubble.parent_body != target
            or not _live(fight, bubble)):
        return False
    receiver = next((i for i in bubble.source.instances.values() if i.graph.index == 0x0242), None)
    if receiver is not None:
        runtime.send_instance_message(receiver, message, params, sender)
    return True


def damage(fight, barrier, amount, *, attacker=0):
    """Consume shield health and retire the Pet on its scoped break path.

    Reinhardt receives the saved meter and 0053 entry. Zarya's child receives
    the death flag, followed by an explicit Pet abort on the source graph.
    The host reports effective bubble loss to 0256 before retiring it.
    Native damage categories and cross-component message ordering remain open.
    """
    if not _live(fight, barrier) or barrier.vitals is None or amount <= 0:
        return 0.0
    dealt = barrier.vitals.damage(amount)
    remaining = barrier.vitals.total
    barrier.component.set(253, remaining)
    owner_script = barrier.source.instances[barrier.instance]
    if barrier.component.world.hero == ZARYA:
        child = next((i for i in barrier.component.instances.values() if i.graph.index == 0x0256), None)
        if child is not None and dealt > 0 and attacker:
            params = {0x13: expr.f32(dealt), 0x12: expr.Entity(attacker)}
            runtime.send_instance_message(child, 0x0240000000000011, params, attacker)
            barrier.component.run_now()
            barrier.source.run_now()
        if remaining <= 0:
            barrier.component.set(478, True)
            barrier.component.run_now()
            # Scoped break bridge. The child is not a player message sender.
            # End the Pet so its authored cooldown and ability-stop links run.
            _end_bubble(fight, barrier)
        return dealt
    owner_script.write((runtime.INSTANCE_SCOPE, 586), expr.f32(remaining))
    if remaining <= 0:
        runtime.send_instance_message(owner_script, 0x0240000000000053, {}, barrier.entity)
    barrier.source.run_now()
    if not _live(fight, barrier):
        retire(fight, barrier.entity)
    return dealt
