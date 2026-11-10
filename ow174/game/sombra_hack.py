"""104319 Hack channel and receiver with reconstructed targeting and Doomfist input gates."""

import math
from dataclasses import replace

from ow174.game import bots
from ow174.game.script import expr, graph, nodes, play, play_queue, runtime

SOMBRA = 0x02E000000000012E
DOOMFIST = 0x02E000000000012F
LOCK_BUTTONS = 2 | 8 | 16 | 32
HACK_REJECT = 14679


def player_at(fight, body):
    return next((p for p in fight.match.players
                 if p.has_body and p.body == body and p.spawned and not p.gone and p.body_script is not None),
                None)


def receiver(fight, body):
    return next((t.owner for t in fight._targets() if t.entity == body), None)


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
        if (not can_receive(target.owner) or fight.vitals_of(target).dead
                or fight.protected(target.entity)):
            continue
        a, b = target.segment()
        centre = tuple((lo + hi) * 0.5 for lo, hi in zip(a, b, strict=True))
        distance = math.dist(origin, centre)
        if not 0 < distance <= reach:
            continue
        direction = tuple((v - o) / distance for v, o in zip(centre, origin, strict=True))
        alignment = sum(x * y for x, y in zip(forward, direction, strict=True))
        if (alignment < math.cos(math.radians(30))
                or not capsule_visible(fight._collision(), origin, a, b, target.radius)):
            continue
        if fight.hand_cannon_barriers.ray_contact(caster, caster.team, origin, direction, distance):
            continue
        yield -alignment, distance, target.entity


def query(fight, state):
    index, position = state.instance.graph.index, state.node.pos
    if (index, position) not in ((0xB1B, 50), (0xB1B, 52), (0xB1B, 68)):
        return NotImplemented
    caster = player_at(fight, state.instance.component.entity)
    if caster is None or fight.is_dead(caster):
        return ()
    reach = expr.to_float(state.evaluate(state.node.field("m_radius"), watch=False))
    candidates = sorted(eligible(fight, caster, reach))
    return (expr.Entity(candidates[0][2]),) if candidates else ()


def instance_for(player, index):
    script = getattr(player, "body_script", None)
    if script is None or getattr(script, 'retired', False) or script.component.owner_dead:
        return None
    return next((i for i in script.component.instances.values() if i.graph.index == index), None)


def locked(player, frame=None):
    if player is None:
        return False
    script = getattr(player, "body_script", None)
    if script is None or getattr(script, "retired", False) or script.component.owner_dead:
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
    if player.hero.guid == DOOMFIST and locked(player, command.frame):
        return replace(command, buttons=command.buttons & ~LOCK_BUTTONS)
    return command


def interrupt_doomfist(fight, player):
    if player.hero.guid != DOOMFIST or not locked(player):
        return
    fight.punches.dashes.pop(player.body, None)
    fight.slams.casts.pop(player.body, None)
    script = player.body_script
    for instance in tuple(script.component.instances.values()):
        for state in tuple(instance.states.values()):
            if isinstance(state, nodes.Ability) and (
                instance.graph.index in (0x0D06, 0x0DD8, 0x0B27) or state in script.ultimates
            ):
                state.presses.clear()
                state.stop()
    script.component.run_now()


def predicate(fight, instance, cfg):
    index = instance.graph.index
    if index == 0xB1F and cfg is instance.graph.node(21).field("m_condition"):
        return can_receive(receiver(fight, instance.component.entity))
    if index != 0xB1B or not any(cfg is instance.graph.node(p).field("m_condition") for p in (164, 204)):
        return NotImplemented
    caster = player_at(fight, instance.component.entity)
    target = instance.find_var(0, 8909).value()
    search = instance.states.get(25)
    return (caster is not None and search is not None and target in query(fight, search)
            and not locked(receiver(fight, expr.to_entity(target))))


def invoke(fight, instance, node):
    if (instance.graph.index, node.pos) != (0x0B1B, 141):
        return
    resource = play.resolve_resource(instance, node)
    if resource is None or resource.guid != 0x0580000000000B1F:
        return
    source = instance.component.entity
    request = play.resolve_request(instance, node, resource, context_provider=lambda:
                                   play.InvocationContext(source, source, instance.source_entity()))
    if request is None:
        return
    for entity in request.targets:
        owner = receiver(fight, entity)
        if not can_receive(owner) or locked(owner):
            continue
        component = owner.body_script.component
        for prior in tuple(component.instances.values()):
            if prior.graph.index == 0x0B1F:
                component.destroy(prior)
        queue = play_queue.Queue(component, lambda _: play_queue.Load("ready", graph.graph(0x0B1F)))
        queue.submit(request)
        if component is not instance.component:
            component.run_now()
            if isinstance(owner, bots.Bot):
                from ow174.game import npc_control, npc_melee

                if locked(owner) and npc_control.manual(owner):
                    npc_melee.release_charge(fight, owner, cancel=True)
                    npc_control.update_input(fight, owner)
            else:
                interrupt_doomfist(fight, owner)
