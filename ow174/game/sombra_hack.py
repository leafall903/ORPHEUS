"""Original Sombra 0B1B Hack routed to a live enemy body."""

import math

from ow174.game import barriers, sigma_barrier
from ow174.game.script import expr, graph, play, runtime

HERO, WEAPON, EFFECT = 0x02E000000000012E, 0x0B1B, 0x0B1F
EMP, EMP_HIT = 0x0B31, 0x0B32
MERCY = 0x02E0000000000004
MERCY_SKILLS = frozenset((0x006E, 0x00CA, 0x10DA))


def damaged(script, sender, tags):
    """Supply 0B1B/106 with the tags of an admitted hit."""
    if script.hero != HERO or script.retired or script.component.owner_dead:
        return
    for instance in tuple(script.component.instances.values()):
        if instance.graph.index == WEAPON and runtime.send_instance_message(
            instance, 0x0240000000000011, {0x3D: tags}, sender,
        ):
            script.component.run_now()


def caster(fight, entity):
    return next((p for p in fight.match.players
                 if p.has_body and p.body == entity and p.hero.guid == HERO and not p.gone), None)


def clear(fight, owner, origin, ray, hit):
    from ow174.game.combat import ray_world

    wall = ray_world(fight._collision(), origin, ray, hit)
    return (wall is None or wall >= hit) and fight._barrier_contact(owner, origin, ray, hit) is None


def track(fight, state):
    from ow174.game.combat import EPSILON, aim, ray_capsule

    pos = state.node.pos
    owner = caster(fight, state.instance.component.entity)
    if owner is None or pos not in (50, 52, 68):
        return NotImplemented
    if fight.is_dead(owner) or fight.is_stunned(owner):
        return ()
    origin, direction = fight._eye(owner), aim(owner.mover.yaw, owner.mover.pitch)
    radius = expr.to_float(state.evaluate(state.node.field("m_radius"), watch=False))
    locked = state.instance.find_var(graph.INSTANCE, 375).value()
    locked_ids = {expr.to_entity(value) for value in locked} if isinstance(locked, tuple) else set()
    found = []
    for target in fight._enemies(owner):
        a, b = target.segment()
        ray = direction
        if pos == 52:
            if target.entity not in locked_ids:
                continue
            nearest = (a[0], max(a[1], min(b[1], origin[1])), a[2])
            offset = tuple(p - q for p, q in zip(nearest, origin, strict=True))
            distance = math.hypot(*offset)
            if distance <= EPSILON or sum(a * b for a, b in zip(offset, direction, strict=True)) <= 0:
                continue
            ray = tuple(v / distance for v in offset)
            hit = max(0.0, distance - target.radius)
        else:
            hit = ray_capsule(origin, direction, a, b, target.radius)
        if hit is not None and hit <= radius and clear(fight, owner, origin, ray, hit):
            found.append((hit, target.entity))
    return (min(found)[1],) if found else ()


def emp_barriers(fight, owner, instance, hit_graph):
    """Consume the EMP barrier packet for hosted shield actors."""
    from ow174.game.combat import ray_world

    radius = expr.to_float(instance.evaluate(instance.graph.node(40).field("m_radius")))
    amount = expr.to_float(instance.evaluate(hit_graph.node(3).field("m_modifyHealth.m_amount")))
    origin = fight._eye(owner)
    for sphere in fight.winston_barriers.enemies(owner):
        delta = tuple(end - start for start, end in zip(origin, sphere.centre, strict=True))
        distance = math.hypot(*delta)
        reach = max(0.0, distance - sphere.radius)
        if reach <= radius and (reach == 0 or ray_world(
            fight._collision(), origin, tuple(axis / distance for axis in delta), reach,
        ) is None):
            sphere.damage(amount)
    fight.winston_barriers.update()
    for deployable in tuple(fight.orisa_barriers.enemies(owner)):
        point = fight.orisa_barriers.geometry.closest(deployable.position, deployable.yaw, origin)
        delta = tuple(end - start for start, end in zip(origin, point, strict=True))
        distance = math.hypot(*delta)
        if distance <= radius and (distance == 0 or ray_world(
            fight._collision(), origin, tuple(axis / distance for axis in delta), distance,
        ) is None):
            fight.orisa_barriers.damage(deployable, amount)
    for deployable in sigma_barrier.enemies(fight, owner):
        delta = tuple(end - start for start, end in zip(origin, deployable.position, strict=True))
        distance = math.hypot(*delta)
        if distance <= radius and (distance == 0 or ray_world(
            fight._collision(), origin, tuple(axis / distance for axis in delta), distance,
        ) is None):
            sigma_barrier.damage_barrier(fight, deployable, amount)
    for defender, barrier in fight._enemy_barriers(owner):
        mover = defender.mover
        centre, _ = barriers.frame(
            fight.barrier_geometry, mover.position, mover.yaw, mover.pitch,
            mover.values.stand_radius, mover.values.stand_height,
        )
        delta = tuple(end - start for start, end in zip(origin, centre, strict=True))
        distance = math.hypot(*delta)
        if distance <= radius and (
            distance == 0 or ray_world(
                fight._collision(), origin, tuple(axis / distance for axis in delta), distance,
            ) is None
        ):
            fight._damage_barrier((distance, defender, barrier), amount)


def emp_targets(fight, state):
    from ow174.game.combat import ray_world

    owner = caster(fight, state.instance.component.entity)
    if owner is None or state.node.pos != 40:
        return NotImplemented
    if fight.is_dead(owner):
        return ()
    origin = fight._eye(owner)
    radius = expr.to_float(state.evaluate(state.node.field("m_radius"), watch=False))
    found = []
    for target in fight._enemies(owner):
        a, b = target.segment()
        point = (a[0], max(a[1], min(b[1], origin[1])), a[2])
        delta = tuple(p - q for p, q in zip(point, origin, strict=True))
        distance = math.hypot(*delta)
        reach = max(0.0, distance - target.radius)
        if reach > radius:
            continue
        if reach == 0 or ray_world(
            fight._collision(), origin, tuple(v / distance for v in delta), reach,
        ) is None:
            found.append(target.entity)
    return tuple(found)


def emp_barrier_predicate(fight, instance, cfg):
    if cfg is not instance.graph.node(8).field("m_condition"):
        return NotImplemented
    target = next((t for t in fight._targets() if t.entity == instance.component.entity), None)
    return target is not None and fight.zarya_barriers.protects(target.owner)


def submit_emp_health(fight, state, elapsed_ms):
    from ow174.game.combat import SHIELDS_POOL

    instance = state.instance
    if instance.graph.index != EMP_HIT or state.node.pos not in (3, 9):
        return NotImplemented
    if elapsed_ms:
        return True
    owner = caster(fight, instance.source_entity())
    target = next((t for t in fight._targets() if t.entity == instance.component.entity), None)
    if owner is not None and target is not None and not fight.damage_immune(target):
        vitals = fight.vitals_of(target)
        cfg = state.node.field("m_modifyHealth.m_amount")
        amount = expr.to_float(state.evaluate(cfg, watch=False))
        if state.node.pos == 3:
            fight.zarya_barriers.intercept(target.owner, amount, owner.body)
            return True
        shields = vitals.now[SHIELDS_POOL] + sum(s.now for s in vitals.temporary_shields.values())
        if amount > 0 and shields > 0:
            fight._damage(owner, target, min(amount, shields), False, None, fight.tick)
    return True


def track_ray(fight, state):
    instance = state.instance
    owner = caster(fight, instance.component.entity)
    if owner is None or instance.graph.index != WEAPON or state.node.pos != 59:
        return NotImplemented
    entity = expr.to_entity(instance.find_var(graph.INSTANCE, 8909).value())
    target = next((t for t in fight._enemies(owner) if t.entity == entity), None)
    if target is None:
        return False, None
    origin = fight._eye(owner)
    point = target.feet[0], target.feet[1] + target.eye, target.feet[2]
    delta = tuple(p - q for p, q in zip(point, origin, strict=True))
    distance = math.hypot(*delta)
    visible = distance <= 1e-6 or clear(
        fight, owner, origin, tuple(v / distance for v in delta), max(0.0, distance - target.radius),
    )
    return visible, expr.Vec3(*point)


def target_filter(fight, instance, cfg):
    if instance.graph.index != WEAPON or cfg is not instance.graph.node(204).field("m_condition"):
        return NotImplemented
    owner = caster(fight, instance.component.entity)
    entity = expr.to_entity(instance.evaluate(cfg.get("m_entity")))
    if owner is None or not any(target.entity == entity for target in fight._enemies(owner)):
        return False
    with instance.component.parameter_scope({8: expr.Entity(entity)}):
        return expr.truthy(instance.evaluate(cfg["m_filter"].get("m_condition")))


def submit(fight, instance, node):
    origin = instance.graph.index
    if origin not in (WEAPON, EMP, EMP_HIT):
        return
    source = instance.source_entity() if origin == EMP_HIT else instance.component.entity
    owner = caster(fight, source)
    resource = play.resolve_resource(instance, node)
    if owner is None or resource is None:
        return
    destination = resource.guid & 0xFFFFFFFFFFFF
    if (origin, destination) not in ((WEAPON, EFFECT), (EMP, EMP_HIT), (EMP_HIT, EFFECT)):
        return
    context = play.InvocationContext(source, source, source)
    request = play.resolve_request(instance, node, resource, context_provider=lambda: context)
    definition = graph.graph(destination)
    if definition is None:
        return
    if origin == EMP and node.pos == 8 and not fight.is_dead(owner):
        emp_barriers(fight, owner, instance, definition)
    if request is None:
        return
    for target in fight._enemies(owner):
        if target.entity not in request.targets or fight.damage_immune(target):
            continue
        script = getattr(target.owner, "body_script", None)
        if script is None:
            continue
        application = script.component.play_loaded_graph(
            definition, source=request.context.caller, params=request.params, overrides=resource.overrides,
            policy=request.policy, instigator=request.context.instigator,
        )
        if (destination == EFFECT and application is not None
                and getattr(getattr(target.owner, "hero", None), "guid", None) == MERCY):
            duration = expr.to_float(application.evaluate(application.graph.node(96).field("m_value")))
            if duration > 0:
                fight.apply_hack(target, duration)
