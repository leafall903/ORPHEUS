"""Host Reinhardt's and Zarya's authored defensive Pets for Sombra combat."""

import math
from dataclasses import dataclass

from ow174.game import barrier_geometry, bots, orisa_barrier, sigma_barrier, winston_combat
from ow174.game.script import expr, graph, play, runtime
from ow174.game.world import HEALTH_POOL

HERO = 0x02E0000000000007
ZARYA = 0x02E0000000000068
DEFINITION = 0x040000000000011A
TARGET_TAG = 0x0D6000000000003A  # runtime GUID for 003A.06C
ZARYA_LIFETIME_STATES = {0x244: 1, 0x25C: 11}
BUBBLE_RECIPIENT_PARAM = 0x0A400000000000F3
WORLD_BARRIERS = (orisa_barrier.Shield, sigma_barrier.Barrier, winston_combat.Dome)


@dataclass
class Barrier:
    owner: object
    pet: object
    component: runtime.Component
    mesh: tuple
    geometry_digest: str | None
    vitals: object = None
    recipient: object = None
    recipient_body: int = 0
    expiry_tick: int | None = None

    @property
    def entity(self):
        return self.component.entity

    @property
    def protected(self):
        return self.recipient if self.recipient is not None else self.owner

    @property
    def protected_body(self):
        return self.recipient_body if self.recipient is not None else self.owner.body


def recipient_entity(recipient):
    return recipient.entity if isinstance(recipient, bots.Bot) else recipient.body


def bubble_expiry(clock):
    """Predict the frame ending the authored scalar chase at normal frame cadence."""
    return clock.expiry_frame() if clock is not None else None


def recipient_ready(fight, owner, recipient):
    if (recipient is owner or recipient.team != owner.team or fight.match.game_map.free_for_all
            or recipient.body_script is None or recipient.body_script.component.owner_dead
            or recipient.body_script.component.entity != recipient_entity(recipient)):
        return False
    if isinstance(recipient, bots.Bot):
        return recipient in fight.match.bots and not recipient.dead and not recipient.dva_shell
    return (recipient.has_body and recipient.spawned and not recipient.gone
            and not recipient.body_script.retired and not fight.is_dead(recipient))


def projected_targets(fight, owner, state):
    if (owner.hero.guid, state.instance.graph.index, state.index) != (ZARYA, 0x25C, 7):
        return NotImplemented
    from ow174.game.combat import aim, ray_capsule, ray_world

    origin = fight._eye(owner)
    direction = aim(owner.mover.yaw, owner.mover.pitch)
    reach = expr.to_float(state.evaluate(state.node.field("m_radius"), watch=False))
    wall = ray_world(fight._line_collision(), origin, direction, reach)
    closest = reach + 1e-6 if wall is None else wall
    selected = ()
    for target in fight._targets():
        if (isinstance(target.owner, bots.Bot) or target.owner in fight.match.players) and recipient_ready(
                fight, owner, target.owner):
            distance = ray_capsule(origin, direction, *target.segment(), target.radius)
            if distance is not None and distance <= reach and distance < closest:
                closest, selected = distance, (target.entity,)
    return selected


def create(world, state):
    instance = state.instance
    signature = world.hero, instance.graph.index, state.index
    if signature == (HERO, 0x95, 17):
        definition, startup, pool = DEFINITION, graph.graph(0x25B4), 0x96
        if startup is None:
            return 0
    elif signature == (ZARYA, 0x244, 2):
        definition, startup, pool = 0x04000000000013F4, None, 0x256
    elif signature == (ZARYA, 0x25C, 10):
        definition, startup, pool = 0x04000000000003DA, None, 0x256
    else:
        return 0
    if expr.guid_of(state.node.field("m_entityDefinition.m_entityDef")) != definition:
        return 0
    fight = world.match.combat
    owner = next((p for p in fight.match.players if p.has_body and p.body == world.body), None)
    if owner is None or fight.is_dead(owner) or graph.graph(pool) is None:
        return 0
    recipient, body = None, 0
    if signature == (ZARYA, 0x25C, 10):
        body = expr.to_entity(instance.context().var(expr.INSTANCE, 536))
        recipient = next((t.owner for t in fight._targets() if t.entity == body
                          and (isinstance(t.owner, bots.Bot) or t.owner in fight.match.players)), None)
        if recipient is None or not recipient_ready(fight, owner, recipient):
            return 0
    entity = fight.projectiles.allocate_seen_entity()
    child_world = type(world)(world.hero, world.mode, entity)
    child_world.match = world.match
    component = runtime.Component(entity, instance.component.frame_ms, child_world)
    component.frame, component.now = instance.component.frame, state.now
    mesh, digest = barrier_geometry.load() if startup is not None else ((), None)
    clock = instance.states.get(ZARYA_LIFETIME_STATES.get(instance.graph.index))
    fight.barriers[entity] = Barrier(owner, state, component, mesh, digest, recipient=recipient,
                                     recipient_body=body, expiry_tick=bubble_expiry(clock))
    if startup is not None:
        initial = component.create(startup, init=False)
        initial.set_source_entity(owner.body)
        initial.init()
        component.run_now()
    return entity


def retire(fight, entity):
    barrier = fight.barriers.pop(entity, None)
    if barrier is not None:
        for instance in list(barrier.component.instances.values()):
            barrier.component.destroy(instance)


def retire_owner(fight, owner):
    for barrier in tuple(fight.barriers.values()):
        if isinstance(barrier, (orisa_barrier.Shield, winston_combat.Dome)):
            continue  # deployed barrier has its own lifetime
        if barrier.owner is owner:
            retire(fight, barrier.entity)
        elif isinstance(barrier, Barrier) and barrier.recipient is owner:
            end_pet(barrier)
            retire(fight, barrier.entity)


def silence(component):
    for instance in tuple(component.instances.values()):
        if instance.graph.index != 0x0095:
            continue
        button = instance.states.get(31)  # node89 drives the held barrier branch
        if button is not None and button.active and button.down:
            button.go_up()


def play_pool(world, instance, node):
    signature = world.hero, instance.graph.index, node.pos
    if signature == (HERO, 0x95, 6):
        pool_graph, pool_state = 0x96, 1
    elif signature in ((ZARYA, 0x244, 9), (ZARYA, 0x25C, 27)):
        pool_graph, pool_state = 0x256, 0
    else:
        return
    resource = play.resolve_resource(instance, node)
    if resource is None or resource.guid != (0x0580000000000000 | pool_graph):
        return
    request = play.resolve_request(
        instance, node, resource,
        context_provider=lambda: play.InvocationContext(world.body, world.body, instance.source_entity()),
    )
    if request is None or len(request.targets) != 1:
        return
    barrier = world.match.combat.barriers.get(request.targets[0])
    if not isinstance(barrier, Barrier) or barrier.pet.instance is not instance:
        return
    component = barrier.component
    component.frame, component.now = instance.component.frame, instance.component.now
    params = request.params
    if pool_graph == 0x256:
        # Give the pet VM its own wrappers for the authored recipient entities.
        params = [
            (key, tuple(expr.Entity(item.id) if isinstance(item, expr.Entity) else item
                        for item in value))
            if key == BUBBLE_RECIPIENT_PARAM and isinstance(value, tuple) else (key, value)
            for key, value in params
        ]
    pool = component.play_loaded_graph(
        graph.graph(pool_graph), source=request.context.caller, instigator=request.context.instigator,
        params=params, overrides=resource.overrides, policy=request.policy,
    )
    component.run_now()
    if pool is not None and pool.state(pool_state).active:
        from ow174.game.combat import Vitals

        state = pool.state(pool_state)
        if (world.hero == ZARYA
                and expr.to_entity(pool.context().var(expr.INSTANCE, 2390)) != barrier.protected_body):
            return
        maximum = expr.to_float(state.evaluate(state.node.field("m_amount"), watch=False))
        current = maximum if world.hero == ZARYA else expr.to_float(
            state.evaluate(state.node.field("m_BCFDD2E9"), watch=False),
        )
        barrier.vitals = Vitals(barrier.entity, maximum)
        barrier.vitals.now[HEALTH_POOL] = max(0.0, min(maximum, current))
        component.set(253, barrier.vitals.total)
        if world.hero == ZARYA and barrier.vitals.total > 0:
            world.match.combat.cleanse_ana_effects(barrier.protected_body)


def health_query(fight, instance, cfg):
    if (instance.graph.index != 0x95
            or (cfg.get("m_7E8B666C") or {}).get("m_7E8B666C") != HEALTH_POOL):
        return NotImplemented
    target = expr.to_entity(instance.evaluate(cfg.get("m_entity")))
    barrier = fight.barriers.get(target)
    if not isinstance(barrier, Barrier) or barrier.pet.instance is not instance:
        return NotImplemented
    return barrier.vitals.total if barrier.vitals is not None else 0.0


def live(fight, barrier):
    owner = barrier.owner
    source = barrier.pet.instance.component
    return ((barrier.recipient is None or (recipient_entity(barrier.recipient) == barrier.recipient_body
                                         and recipient_ready(fight, owner, barrier.recipient)))
            and not owner.gone and owner.has_body and owner.body == source.entity
            and owner.body_script is not None and not owner.body_script.retired
            and owner.body_script.component is source and barrier.pet.active and not fight.is_dead(owner))


def advance(fight, tick):
    for barrier in tuple(fight.barriers.values()):
        if isinstance(barrier, winston_combat.Dome):
            if not barrier.active() or tick >= barrier.expiry_tick:
                retire(fight, barrier.entity)
            elif tick > barrier.component.frame:
                barrier.component.run_frame(tick)
            continue
        if isinstance(barrier, sigma_barrier.Barrier):
            if not barrier.current(fight):
                retire(fight, barrier.entity)
            else:
                barrier.update(fight)
                if tick > barrier.component.frame:
                    barrier.component.run_frame(tick)
            continue
        if isinstance(barrier, orisa_barrier.Shield):
            if not barrier.retired and tick > barrier.component.frame:
                barrier.component.run_frame(tick)
            if barrier.retired or tick > barrier.end_tick:
                retire(fight, barrier.entity)
            continue
        if not live(fight, barrier):
            if barrier.recipient is not None:
                end_pet(barrier)
            retire(fight, barrier.entity)
        elif tick > barrier.component.frame:
            barrier.component.run_frame(tick)


def expire_bubbles(fight, tick):
    for barrier in tuple(fight.barriers.values()):
        if not isinstance(barrier, Barrier) or barrier.expiry_tick is None or tick < barrier.expiry_tick:
            continue
        instance = barrier.pet.instance
        clock = instance.states[ZARYA_LIFETIME_STATES[instance.graph.index]]
        component = instance.component
        frame_ms = component.frame_ms
        saved_now = component.now
        # Catch up only the lifetime state. Advancing the body here would consume input frames.
        with component.operation():
            try:
                for frame in range(clock.last // frame_ms, tick + 1):
                    end = (frame + 1) * frame_ms - 1
                    if end <= clock.last:
                        continue
                    component.now = end
                    clock.tick()
                    if clock.reached:
                        clock.end(True)
                        break
            finally:
                component.now = saved_now


def enemy_barriers(fight, shooter, *, team=None):
    team = shooter.team if team is None else team
    for barrier in fight.barriers.values():
        if isinstance(barrier, WORLD_BARRIERS):
            if isinstance(barrier, sigma_barrier.Barrier):
                if not barrier.current(fight):
                    continue
                barrier.update(fight)
            if (barrier.owner is not shooter and barrier.active()
                    and (fight.match.game_map.free_for_all or barrier.team != team)):
                yield barrier
            continue
        if (barrier.vitals is not None and not barrier.vitals.dead and live(fight, barrier)
                and barrier.owner is not shooter
                and (fight.match.game_map.free_for_all or barrier.owner.team != team)):
            yield barrier


def pose(barrier):
    if isinstance(barrier, WORLD_BARRIERS):
        return barrier.pose()
    mover = barrier.owner.mover
    yaw, pitch = mover.yaw * math.tau / 65536, mover.pitch * math.tau / 65536
    sy, cy, sp, cp = math.sin(yaw), math.cos(yaw), math.sin(pitch), math.cos(pitch)
    basis = ((cy, 0, -sy), (sy * sp, cp, cy * sp), (sy * cp, -sp, cy * cp))
    height = 1.26 if mover.crouched else 1.8
    anchor = tuple(mover.position[k] + (height if k == 1 else 0) + basis[2][k] for k in range(3))
    return anchor, basis


def mesh_ray(barrier, origin, direction, length):
    if isinstance(barrier, winston_combat.Dome):
        return barrier.ray(origin, direction, length)
    anchor, basis = pose(barrier)
    delta = barrier_geometry._sub(origin, anchor)
    local_origin = tuple(barrier_geometry._dot(delta, axis) for axis in basis)
    local_direction = tuple(barrier_geometry._dot(direction, axis) for axis in basis)
    return barrier_geometry.ray_mesh(barrier.mesh, local_origin, local_direction, length)


def ray_hit(fight, shooter, origin, direction, length, *, include_bubbles=True, team=None, fixed_only=False):
    best = None
    for barrier in enemy_barriers(fight, shooter, team=team):
        if fixed_only and not isinstance(barrier, WORLD_BARRIERS):
            continue
        if barrier.component.world.hero == ZARYA:
            if not include_bubbles:
                continue
            from ow174.game.combat import ray_capsule

            target = next((item for item in fight._targets() if item.owner is barrier.protected), None)
            distance = ray_capsule(origin, direction, *target.segment(), target.radius) if target else None
            if distance is not None and distance > length:
                continue
        else:
            distance = mesh_ray(barrier, origin, direction, length)
        if distance is not None and (best is None or distance < best[0]):
            best = distance, barrier
    return best


def damage(barrier, amount, *, attacker=0):
    if isinstance(barrier, WORLD_BARRIERS):
        barrier.damage(amount)
        return
    dealt = barrier.vitals.damage(amount)
    remaining = barrier.vitals.total
    barrier.component.set(253, remaining)
    instance = barrier.pet.instance
    if barrier.component.world.hero == ZARYA:
        child = next((item for item in barrier.component.instances.values()
                      if item.graph.index == 0x256), None)
        if child is not None and dealt > 0 and attacker:
            params = {0x13: expr.f32(dealt), 0x12: expr.Entity(attacker)}
            runtime.send_instance_message(child, 0x0240000000000011, params, attacker)
            barrier.component.run_now()
            instance.component.run_now()
        if remaining <= 0:
            barrier.component.set(478, True)
            barrier.component.run_now()
            end_pet(barrier)
        return
    instance.write((runtime.INSTANCE_SCOPE, 586), expr.f32(remaining))
    if remaining <= 0:
        runtime.send_instance_message(instance, 0x0240000000000053, {}, barrier.entity)
    instance.component.run_now()


def end_pet(barrier):
    if barrier.pet.active:
        barrier.pet.request(runtime.ABORT)
        barrier.pet.instance.component.run_now()


def relay_bubble_energy(world, target, message, params, sender):
    if world.match is None or world.hero != ZARYA or sender != world.body or message != 0x0240000000000146:
        return False
    fight = world.match.combat
    bubble = fight.barriers.get(sender)
    if (bubble is None or bubble.component.world is not world
            or bubble.owner.body != target or not live(fight, bubble)):
        return False
    source = bubble.pet.instance.component
    receiver = next((item for item in source.instances.values() if item.graph.index == 0x242), None)
    if receiver is not None:
        runtime.send_instance_message(receiver, message, params, sender)
    return True


def body_bubble(fight, owner):
    for barrier in fight.barriers.values():
        if (barrier.component.world.hero == ZARYA and barrier.protected is owner
                and barrier.vitals is not None and not barrier.vitals.dead and live(fight, barrier)):
            return barrier
    return None


def protect_body(fight, target, amount, *, attacker):
    barrier = body_bubble(fight, target.owner)
    if barrier is None:
        return False
    damage(barrier, amount, attacker=attacker)
    return True
