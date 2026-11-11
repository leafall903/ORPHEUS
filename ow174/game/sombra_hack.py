"""104319 ordinary Hack and EMP targeting and receiver hosting."""

import math
from dataclasses import replace

from ow174.game import earthshatter, reinhardt_barrier
from ow174.game.commands import SECONDARY
from ow174.game.script import expr, graph, nodes, play, play_queue, runtime

SOMBRA = 0x02E000000000012E
ASHE = 0x02E0000000000200
ASHE_LOCK_BUTTONS = 8 | 16 | 32
REINHARDT_LOCK_BUTTONS = SECONDARY | 16 | 32
HACK_REJECT = 14679
EMP, EMP_CHILD, HACK = 0x0B31, 0x0B32, 0x0B1F


def player_at(fight, body):
    return next((p for p in fight.match.players
                 if p.has_body and p.body == body and p.spawned and not p.gone and p.body_script is not None),
                None)


def recipient(fight, body):
    player = player_at(fight, body)
    if player is not None:
        return player
    actor = fight.companions.actor_for(body)
    return actor.bot if actor is not None and not actor.bot.dead else None


def can_receive(owner):
    script = getattr(owner, "body_script", None)
    if script is None or getattr(script, "retired", False) or script.component.owner_dead:
        return False
    reject = script.component.entity_var(HACK_REJECT, create=False)
    return reject is None or not expr.truthy(reject.value())


def eligible(fight, caster, reach):
    from ow174.game.combat import aim, capsule_visible

    origin = fight._eye(caster)
    forward = aim(caster.mover.yaw, caster.mover.pitch)
    for target in fight._enemies(caster):
        owner = player_at(fight, target.entity)
        if not can_receive(owner) or locked(owner):
            continue
        a, b = target.segment()
        centre = tuple((lo + hi) * 0.5 for lo, hi in zip(a, b, strict=True))
        distance = math.dist(origin, centre)
        if not 0 < distance <= reach:
            continue
        direction = tuple((v - o) / distance for v, o in zip(centre, origin, strict=True))
        alignment = sum(x * y for x, y in zip(forward, direction, strict=True))
        if (alignment < math.cos(math.radians(30))
                or not capsule_visible(fight._collision(), origin, a, b, target.radius,
                                       ice_walls=fight.ice_walls)):
            continue
        if fight._personal_barrier(target, origin, direction, distance):
            continue
        if any(host.intercept(caster, origin, direction, distance)
               for host in fight.deployed_barrier_hosts()):
            continue
        yield -alignment, distance, target.entity


def query(fight, state):
    index, position = state.instance.graph.index, state.node.pos
    if (index, position) == (EMP, 40):
        return emp_targets(fight, state)
    if (index, position) not in ((0xB1B, 50), (0xB1B, 52), (0xB1B, 68)):
        return NotImplemented
    caster = player_at(fight, state.instance.component.entity)
    if caster is None or fight.is_dead(caster):
        return ()
    reach = expr.to_float(state.evaluate(state.node.field("m_radius"), watch=False))
    candidate = min(eligible(fight, caster, reach), default=None)
    return (expr.Entity(candidate[2]),) if candidate is not None else ()


def emp_targets(fight, state):
    from ow174.game.combat import capsule_visible, segment_distance
    from ow174.game.immunity import damage_immune

    caster = player_at(fight, state.instance.component.entity)
    if caster is None or fight.is_dead(caster):
        return ()
    origin = fight._eye(caster)
    radius = expr.to_float(state.evaluate(state.node.field("m_radius"), watch=False))
    selected = []
    for target in fight._enemies(caster):
        owner = recipient(fight, target.entity)
        if not can_receive(owner) or damage_immune(owner, target.entity):
            continue
        a, b = target.segment()
        if segment_distance(origin, a, b) - target.radius > radius:
            continue
        if capsule_visible(fight._collision(), origin, a, b, target.radius, ice_walls=fight.ice_walls):
            selected.append(expr.Entity(target.entity))
    selected.extend(expr.Entity(entity) for entity in emp_barriers(fight, caster, origin, radius))
    return tuple(selected)


def emp_barriers(fight, caster, origin, radius):
    from ow174.game.combat import capsule_visible

    for actor in fight.world_barriers.enemies(caster):
        distance = math.dist(origin, actor.point)
        if abs(distance - actor.radius) > radius:
            continue
        outward = (1.0, 0.0, 0.0) if distance == 0 else tuple(
            (a - b) / distance for a, b in zip(origin, actor.point, strict=True)
        )
        surface = tuple(axis + unit * actor.radius for axis, unit in zip(actor.point, outward, strict=True))
        if capsule_visible(fight._collision(), origin, surface, surface, 0.0, ice_walls=fight.ice_walls):
            yield actor.entity
    for host in (fight.sigma_barriers, fight.orisa_barriers):
        for actor in host.enemies(caster):
            if host.blast_distance(actor, origin, radius, ice_walls=fight.ice_walls) is not None:
                yield actor.body
    field = fight.reinhardt_barriers
    for owner, state in field.enemies(caster):
        if field.blast_distance(owner, origin, radius) is not None:
            yield state.entity


def pulse_barriers(fight, instance, definition, targets):
    caster = player_at(fight, instance.component.entity)
    if caster is None:
        return
    amount = expr.to_float(instance.evaluate(definition.node(3).field("m_modifyHealth.m_amount")))
    for actor in fight.world_barriers.enemies(caster):
        if actor.entity in targets:
            fight.world_barriers.damage(actor, amount)
    for host in (fight.sigma_barriers, fight.orisa_barriers):
        for actor in tuple(host.enemies(caster)):
            if actor.body in targets:
                host.damage(actor, amount)
    field = fight.reinhardt_barriers
    for owner, state in field.enemies(caster):
        if state.entity in targets:
            field.damage(owner, amount)


def instance_for(player, index):
    script = getattr(player, "body_script", None)
    if script is None or getattr(script, "retired", False) or script.component.owner_dead:
        return None
    return next((i for i in script.component.instances.values() if i.graph.index == index), None)


def locked(player, frame=None):
    instance = instance_for(player, HACK)
    wait = instance.states.get(58) if instance is not None else None
    if not isinstance(wait, nodes.Wait) or not wait.active:
        return False
    frame_ms = instance.component.frame_ms
    return frame is None or (wait.start // frame_ms <= frame and frame * frame_ms < wait.start + wait.ms)


def stop_reinhardt_casts(component):
    for instance in tuple(component.instances.values()):
        if instance.graph.index in (0x00E2, earthshatter.GRAPH):
            for cast in tuple(instance.states.values()):
                if isinstance(cast, nodes.Ability):
                    cast.presses.clear()
                    cast.stop()


def cancel_hack(player):
    if getattr(getattr(player, "hero", None), "guid", None) != SOMBRA:
        return
    instance = instance_for(player, 0xB1B)
    wait = instance.states.get(54) if instance is not None else None
    if wait is not None and wait.active:
        wait.request(runtime.ABORT)
        instance.component.run_now()


def prepare_input(fight, player, command):
    if player.hero.guid == SOMBRA:
        instance = instance_for(player, 0xB1B)
        if instance is not None:
            search = instance.states.get(25)  # Data-flow search using the authored radius.
            if search is not None:
                choices = query(fight, search)
                wait = instance.states.get(54)
                if wait is not None and wait.active:
                    held = instance.find_var(0, 8909).value()
                    if held not in choices:
                        cancel_hack(player)
                        choices = ()
                instance.component.set(9061, choices)
    if player.hero.guid == ASHE and locked(player, command.frame):
        return replace(command, buttons=command.buttons & ~ASHE_LOCK_BUTTONS)
    if player.hero.guid == reinhardt_barrier.HERO and locked(player, command.frame):
        return replace(command, buttons=command.buttons & ~REINHARDT_LOCK_BUTTONS)
    return command


def predicate(fight, instance, cfg):
    index = instance.graph.index
    if index == HACK and cfg is instance.graph.node(21).field("m_condition"):
        return can_receive(recipient(fight, instance.component.entity))
    if index == EMP_CHILD and cfg is instance.graph.node(8).field("m_condition"):
        # The receiver here is the body. Attached barriers use the pool bridge.
        return False
    if index != 0xB1B or not any(cfg is instance.graph.node(p).field("m_condition") for p in (164, 204)):
        return NotImplemented
    caster = player_at(fight, instance.component.entity)
    target = instance.find_var(0, 8909).value()
    search = instance.states.get(25)
    return (caster is not None and search is not None and target in query(fight, search)
            and not locked(player_at(fight, expr.to_entity(target))))


def invoke(fight, instance, node):
    signature = instance.graph.index, node.pos
    if signature == (EMP, 8):
        destination = EMP_CHILD
    elif signature in ((0x0B1B, 141), (EMP_CHILD, 6)):
        destination = HACK
    else:
        return False
    resource = play.resolve_resource(instance, node)
    definition = graph.graph(destination)
    if resource is None or resource.guid != (0x0580000000000000 | destination) or definition is None:
        return False
    source = instance.source_entity() if signature == (EMP_CHILD, 6) else instance.component.entity
    request = play.resolve_request(
        instance, node, resource, context_provider=lambda: play.InvocationContext(source, source, source),
    )
    if request is None:
        return True
    if signature == (EMP, 8):
        pulse_barriers(fight, instance, definition, request.targets)
    for entity in request.targets:
        owner = recipient(fight, entity)
        if not can_receive(owner) or (signature == (0x0B1B, 141) and locked(owner)):
            continue
        component = owner.body_script.component
        for prior in tuple(component.instances.values()):
            if prior.graph.index == destination:
                component.destroy(prior)
        queue = play_queue.Queue(component, lambda _: play_queue.Load("ready", definition))
        now = component.now
        if signature != (0x0B1B, 141):
            component.now = max(now, instance.component.now)
        try:
            queue.submit(request)
            if component is not instance.component:
                component.run_now()
        finally:
            component.now = now
    return True


def emp_health(fight, state, elapsed_ms):
    instance = state.instance
    if (instance.graph.index, state.node.pos) != (EMP_CHILD, 9):
        return NotImplemented
    if elapsed_ms:
        return True
    from ow174.game.combat import GRASP_SHIELDS, SHIELDS_POOL

    body = instance.component.entity
    caster = player_at(fight, instance.source_entity())
    target = next((target for target in fight._targets() if target.entity == body), None)
    if caster is None or target is None:
        return True
    phase = fight.barrier_phase(target)
    if phase is not None:
        amount = expr.to_float(instance.evaluate(instance.graph.node(3).field("m_modifyHealth.m_amount")))
        fight.damage_personal_barrier(body, phase, caster, amount)
    amount = expr.to_float(state.evaluate(state.node.field("m_modifyHealth.m_amount"), watch=False))
    vitals = fight.vitals_of(target)
    for pool in (GRASP_SHIELDS, SHIELDS_POOL):
        lost = min(vitals.now.get(pool, 0.0), amount)
        if lost > 0:
            vitals.now[pool] -= lost
            vitals.changed.add(pool)
            amount -= lost
            if pool == GRASP_SHIELDS and not vitals.now[pool]:
                vitals.most[pool] = 0.0
    fight._sync_body_health(target.owner, vitals)
    return True
