"""Selected 104319 Sombra combat bridge.

The cast, cooldown, receiver invocation, health amount and lock timer are authored.
Target ranking, visibility, shield-pool and barrier targeting, damage cancellation
and Roadhog input masking are reconstructed.
"""

import math
from dataclasses import replace

from ow174.game import defenses
from ow174.game.script import expr, graph, nodes, play, play_queue, runtime

SOMBRA = 0x02E000000000012E
ROADHOG = 0x02E0000000000040
SIGMA = 0x02E000000000023B
LOCK_BUTTONS = 8 | 16 | 32
CASTS = {(0xB1B, 141): 0xB1F, (0xB31, 8): 0xB32, (0xB32, 6): 0xB1F}


def player_at(fight, body):
    return next((p for p in fight.match.players
                 if p.has_body and p.body == body and p.spawned and not p.gone and p.body_script is not None),
                None)


def eligible(fight, caster, *, reach, cone, barrier_blocks):
    from ow174.game.combat import aim, capsule_visible

    origin = fight._eye(caster)
    forward = aim(caster.mover.yaw, caster.mover.pitch)
    for target in fight._enemies(caster):
        if (player_at(fight, target.entity) is None or fight.vitals_of(target).dead
                or defenses.protection(target.owner)[0]
                or fight.zarya_bubbles.for_target(target.owner) is not None):
            continue
        a, b = target.segment()
        centre = tuple((lo + hi) * 0.5 for lo, hi in zip(a, b, strict=True))
        distance = math.dist(origin, centre)
        if not 0 < distance <= reach:
            continue
        direction = tuple((v - o) / distance for v, o in zip(centre, origin, strict=True))
        alignment = sum(x * y for x, y in zip(forward, direction, strict=True))
        if alignment < cone or not capsule_visible(fight._collision(), origin, a, b, target.radius,
                                                   walls=fight.walls):
            continue
        if barrier_blocks and fight._barrier_contact(caster, origin, direction, distance) is not None:
            continue
        yield -alignment, distance, target.entity


def query(fight, state):
    index, position = state.instance.graph.index, state.node.pos
    if (index, position) not in ((0xB1B, 50), (0xB1B, 52), (0xB1B, 68), (0xB31, 40)):
        return NotImplemented
    caster = player_at(fight, state.instance.component.entity)
    if caster is None or fight.is_dead(caster):
        return ()
    reach = expr.to_float(state.evaluate(state.node.field("m_radius"), watch=False))
    direct = index == 0xB1B
    candidates = sorted(eligible(fight, caster, reach=reach,
                                 cone=math.cos(math.radians(30)) if direct else -1.0,
                                 barrier_blocks=direct))
    if direct:
        return tuple(expr.Entity(row[2]) for row in candidates[:1])
    origin = fight._eye(caster)
    # 2578 carries tag 0626 in flight and after landing. Route live mines
    # to 0B32 with the sandbox's point range and terrain visibility.
    from ow174.game.combat import ray_world

    mines = []
    for mine in fight.mines.enemies(caster):
        if mine.body_script.emp_scope() is not None:
            continue
        point = mine.centre()
        delta = tuple(p - o for p, o in zip(point, origin, strict=True))
        distance = math.hypot(*delta)
        if distance > reach or fight.walls.blocked(origin, point):
            continue
        if distance:
            direction = tuple(v / distance for v in delta)
            if ray_world(fight._collision(), origin, direction, distance) is not None:
                continue
        mines.append(expr.Entity(mine.entity))
    return tuple(expr.Entity(row[2]) for row in candidates) + tuple(mines)


def instance_for(player, index):
    script = getattr(player, "body_script", None)
    if script is None or getattr(script, 'retired', False) or script.component.owner_dead:
        return None
    return next((i for i in script.component.instances.values() if i.graph.index == index), None)


def locked(player, frame=None):
    script = getattr(player, "body_script", None)
    if script is None or script.retired or script.component.owner_dead:
        return False
    for instance in script.component.instances.values():
        if instance.graph.index != 0xB1F:
            continue
        wait = instance.states.get(58)
        if (isinstance(wait, nodes.Wait) and wait.active
                and (frame is None or frame * instance.component.frame_ms < wait.start + wait.ms)):
            return True
    return False


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
            search = instance.states.get(25)
            if search is not None:
                choices = query(fight, search)
                wait = instance.states.get(54)
                if wait is not None and wait.active:
                    held = instance.find_var(0, 8909).value()
                    if held not in choices:
                        cancel_hack(player)
                        choices = ()
                instance.component.set(9061, choices)
    return mask_locked_input(player, command)


def mask_locked_input(player, command):
    # Store the admitted buttons in the mover too, so recovery cannot repeat
    # an old ability press into the first frame after the lock.
    if player.hero.guid in (ROADHOG, SIGMA) and locked(player, command.frame):
        buttons = LOCK_BUTTONS if player.hero.guid == ROADHOG else 8  # Sigma's Grasp input
        return replace(command, buttons=command.buttons & ~buttons)
    return command


def interrupt_locked_ability(fight, player):
    if player.hero.guid not in (ROADHOG, SIGMA) or not locked(player):
        return
    script = player.body_script
    if player.hero.guid == SIGMA:
        for instance in script.component.instances.values():
            if instance.graph.index == 0x1D02:
                ability = next((state for state in instance.states.values()
                                if isinstance(state, nodes.Ability) and state.node.pos == 3), None)
                if ability is not None:
                    ability.presses.clear()
                    ability.stop()
                grasp = instance.states.get(1)
                if grasp is not None and grasp.active:
                    # The authored end timer can still run after the box closes.
                    instance.write((graph.INSTANCE, 21312), 0.0)
                    grasp.end(False)
        script.component.run_now()
        return
    fight.hooks.cancel_owner(player.body)
    for instance in tuple(script.component.instances.values()):
        for state in tuple(instance.states.values()):
            if isinstance(state, nodes.Ability) and (
                instance.graph.index in (0x1C8, 0x240) or state in script.ultimates
            ):
                state.presses.clear()
                state.stop()
    script.component.run_now()


def predicate(fight, instance, cfg):
    index = instance.graph.index
    if index == 0xB1F and cfg is instance.graph.node(21).field("m_condition"):
        return player_at(fight, instance.component.entity) is not None
    if index != 0xB1B or not any(cfg is instance.graph.node(p).field("m_condition") for p in (164, 204)):
        return NotImplemented
    caster = player_at(fight, instance.component.entity)
    target = instance.find_var(0, 8909).value()
    search = instance.states.get(25)
    return (caster is not None and search is not None and target in query(fight, search)
            and not locked(player_at(fight, expr.to_entity(target))))


def modify_health(fight, state, elapsed_ms):
    if (state.instance.graph.index, state.node.pos) != (0xB32, 9):
        return NotImplemented
    if elapsed_ms:
        return True
    from ow174.game.combat import ARMOUR_POOL, HEALTH_POOL, SHIELDS_POOL

    body = state.instance.component.entity
    owner = player_at(fight, body)
    vitals = fight.vitals.get(body)
    if owner is not None and vitals is not None and not vitals.dead:
        amount = expr.to_float(state.evaluate(state.node.field("m_modifyHealth.m_amount"), watch=False))
        shields = vitals.now[SHIELDS_POOL] + sum(pool.now for pool in vitals.temporary_shields.values())
        unshielded = vitals.now[HEALTH_POOL] + vitals.now[ARMOUR_POOL]
        if vitals.damage(min(amount, shields), floor=unshielded):
            fight._sync_body_health(owner, vitals)
    return True


def invoke(fight, instance, node):
    index = CASTS.get((instance.graph.index, node.pos))
    if index is None:
        return NotImplemented
    resource = play.resolve_resource(instance, node)
    if resource is None or resource.guid != 0x0580000000000000 | index:
        raise ValueError("Sombra selected invocation resource changed")
    source = instance.component.entity
    request = play.resolve_request(instance, node, resource, context_provider=lambda:
                                   play.InvocationContext(source, source, instance.source_entity()))
    if index == 0xB32:
        # Reconstruct the 0B32 tag 003A branch separately from the player queue.
        # Preserve the player selection made while projected bubbles were still live.
        caster = player_at(fight, source)
        if caster is not None and not fight.is_dead(caster):
            search = instance.graph.node(40)
            reach = expr.to_float(instance.evaluate(search.field("m_radius")))
            amount_cfg = graph.graph(index).node(3).field("m_modifyHealth.m_amount")
            amount = expr.to_float(instance.evaluate(amount_cfg))
            origin = fight._eye(caster)
            contacts = tuple(fight.touching_energy_barriers(caster, origin, reach, include_zarya=False))
            for contact in contacts:
                fight._damage_barrier(contact, amount, shooter=caster)
            fight.zarya_bubbles.area(caster, origin, reach, amount, walls=fight.walls)
    if request is None:
        return True
    for entity in request.targets:
        owner = player_at(fight, entity)
        if owner is None:
            mine = fight.mines.entities.get(entity)
            if (mine is None or mine.removing or mine.triggered or mine.health <= 0
                    or (index == 0xB1F and instance.graph.index != 0xB32)):
                continue
            component = mine.body_script.component
        else:
            if fight.is_dead(owner) or (index == 0xB1F and locked(owner)):
                continue
            component = owner.body_script.component
        if index == 0xB1F:
            for prior in tuple(component.instances.values()):
                if prior.graph.index == index:
                    component.destroy(prior)
        queue = play_queue.Queue(component, lambda _: play_queue.Load("ready", graph.graph(index)))
        queue.submit(request)
        if component is not instance.component:
            component.run_now()
            if owner is not None:
                interrupt_locked_ability(fight, owner)
            else:
                mine.body_script.schedule_emp_end()
    return True
