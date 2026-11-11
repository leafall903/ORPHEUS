"""104319 right-click Hack acquisition and target script routing."""

import math
from dataclasses import replace

from ow174.game import mei, phase_defenses, zarya_shields
from ow174.game.commands import SECONDARY
from ow174.game.script import expr, graph, play

HERO = 0x02E000000000012E
WEAPON = 0x0B1B
EMP = 0x0B31
EMP_TARGET = 0x0B32
TARGET = 0x0B1F
TARGET_GUID = 0x0580000000000B1F
EMP_TARGET_GUID = 0x0580000000000B32
LOCKED_TRACKER = 49
SOURCE_SLOT = 6396
MEI_ABILITY_BUTTONS = frozenset((35, 36, 37, 110, 114, 116))
EMP_SHIELD_DEFINITIONS = frozenset(definition for _, definition in zarya_shields.SPECS.values())


def interrupt_held_hack(script):
    """Record an interrupted right-click before control masks the command."""
    if script.component.world.hero == HERO and script.buttons & SECONDARY:
        script.sombra_hack_wait_release = True


def filter_held_hack(script, command):
    """Only a delivered physical release rearms an interrupted Hack."""
    if not getattr(script, 'sombra_hack_wait_release', False):
        return command
    if command.buttons & SECONDARY:
        return replace(command, buttons=command.buttons & ~SECONDARY)
    del script.sombra_hack_wait_release
    return command


def active_on(component):
    if component.strict:
        return False
    source = component.entity_var(SOURCE_SLOT, create=False)
    state = source.links[-1].state if source is not None and source.links else None
    hacked = (
        state is not None
        and state.instance.graph.index == TARGET
        and state.node.rid == 11
        and state.active
        and isinstance(source.value(), expr.Entity)
    )
    return hacked or emp_active(component)


def mei_command_edges(body, edges):
    """Reconstruct lock and interrupt unreleased actions on Mei's frame clock."""
    component = body.component
    if not active_on(component):
        return edges
    for ability in body.ultimates:
        ability.presses.clear()
        ability.stop()
    match = body.world.match
    if match is not None:
        player = caster_in(match, component.entity)
        if player is not None:
            match.combat.walls.cancel_preview(player)
    # Keep the raw command history so a blocked hold cannot replay after expiry.
    releases = [(button, False) for button in component.held if button in MEI_ABILITY_BUTTONS]
    return releases + [(button, pressed) for button, pressed in edges if button not in MEI_ABILITY_BUTTONS]


def emp_active(component):
    for instance in component.instances.values():
        if instance.graph.index == EMP_TARGET:
            timer = instance.find_var(0, 215, create=False)
            if timer is not None and expr.to_float(timer.value()) > 0:
                return True
    return False


def tracks(state):
    component = state.instance.component
    return (
        not component.strict
        and component.world.hero == HERO
        and state.instance.graph.index == WEAPON
        and state.node.rid in (48, LOCKED_TRACKER, 64)
    )


def emp_tracks(state):
    component = state.instance.component
    return (
        not component.strict
        and component.world.hero == HERO
        and state.instance.graph.index == EMP
        and state.node.rid == 37
    )


def caster_in(match, body):
    return next((player for player in match.players if player.has_body and player.body == body), None)


def eligible(fight, caster, target):
    return (
        target.owner is not caster
        and (fight.match.game_map.free_for_all or target.team != caster.team)
        and not fight.vitals_of(target).dead
        and not mei.protected(target.owner)
        and not phase_defenses.active(target.owner)
        and not fight.shields.shielded(target.owner)
    )


def candidates(state):
    from ow174.game.combat import aim

    instance = state.instance
    match = instance.component.world.match
    if match is None:
        return ()
    fight = match.combat
    caster = caster_in(match, instance.component.entity)
    if caster is None or fight.is_dead(caster) or fight.is_stunned(caster) or fight.frozen(caster):
        return ()
    reach = expr.to_float(state.evaluate(state.node.field("m_radius"), watch=False))
    origin = fight._eye(caster)
    direction = aim(caster.mover.yaw, caster.mover.pitch)
    emp = emp_tracks(state)
    locked = not emp and state.node.rid == LOCKED_TRACKER
    previous = instance.find_var(0, 375, create=False) if locked else None
    # Reconstructed centre-ray selection. B1B supplies both ranges and the
    # continuation angle; the initial acquisition cone remains approximate.
    angle = expr.to_float(state.evaluate(state.node.field("m_A30B383E"), watch=False)) if locked else 12.0
    minimum = math.cos(math.radians(angle))
    found = []
    for target in fight._targets():
        if locked and (previous is None or previous.value() != (expr.Entity(target.entity),)):
            continue
        if not eligible(fight, caster, target):
            continue
        x, y, z = target.feet
        centre = (x, y + target.height / 2, z)
        delta = tuple(point - start for point, start in zip(centre, origin, strict=True))
        distance = math.hypot(*delta)
        if not 0 < distance <= reach:
            continue
        ray = tuple(axis / distance for axis in delta)
        alignment = sum(a * b for a, b in zip(ray, direction, strict=True))
        if not emp and alignment < minimum:
            continue
        if visible(fight, origin, centre, ray, distance):
            found.append((-alignment, distance, target.entity))
    if emp:
        players = tuple(expr.Entity(entity) for _, _, entity in found)
        return players + emp_shields(fight, caster, origin, reach)
    return (expr.Entity(min(found)[2]),) if found else ()


def visible(fight, origin, centre, ray, distance):
    from ow174.game.combat import ray_world

    contact = ray_world(fight.match.collision_world, origin, ray, distance)
    return (contact is None or contact >= distance) and not fight.walls.blocked(origin, centre)


def emp_shields(fight, caster, origin, reach):
    """Select live 03A-tagged Zarya replicas using reconstructed centre LOS."""
    shields = fight.shields
    found = []
    for bubble in shields.bubbles.values():
        if (
            bubble.definition not in EMP_SHIELD_DEFINITIONS
            or not shields.valid(bubble)
            or not shields.enemy(bubble, caster)
        ):
            continue
        x, y, z = zarya_shields.feet(bubble.target)
        centre = (x, y + 1.0, z)
        delta = tuple(point - start for point, start in zip(centre, origin, strict=True))
        distance = math.hypot(*delta)
        if 0 < distance <= reach:
            ray = tuple(axis / distance for axis in delta)
            if visible(fight, origin, centre, ray, distance):
                found.append(expr.Entity(bubble.entity))
    return tuple(found)


def final_filter(world, cfg, instance):
    if (
        instance.component.strict
        or instance.graph.index != WEAPON
        or world.match is None
        or cfg.get("$") != "STU_AC829876"
        or (cfg.get("m_filter") or {}).get("m_4EA686DB") != 20
    ):
        return NotImplemented
    caster = caster_in(world.match, world.body)
    selected = instance.evaluate(cfg.get("m_entity"))
    fight = world.match.combat
    return caster is not None and any(
        expr.Entity(target.entity) == selected and eligible(fight, caster, target)
        for target in fight._targets()
    )


def play_target(world, instance, node):
    if instance.component.strict or world.match is None:
        return
    origin = (instance.graph.index, node.rid)
    if world.hero == HERO and origin == (WEAPON, 130):
        index, guid = TARGET, TARGET_GUID
    elif world.hero == HERO and origin == (EMP, 7):
        index, guid = EMP_TARGET, EMP_TARGET_GUID
    elif origin == (EMP_TARGET, 5):
        index, guid = TARGET, TARGET_GUID
    else:
        return
    resource = play.resolve_resource(instance, node)
    definition = graph.graph(index) if resource is not None and resource.guid == guid else None
    if definition is None:
        return
    request = play.resolve_request(
        instance,
        node,
        resource,
        context_provider=lambda: play.InvocationContext(
            world.body,
            world.body,
            instance.source_entity() if origin[0] == EMP_TARGET else world.body,
        ),
    )
    if request is None:
        return
    if index == EMP_TARGET:
        damage_emp_shields(world, instance, definition, request.targets)
    for player in world.match.players:
        if not player.has_body or player.body not in request.targets or player.body_script is None:
            continue
        player.body_script.component.play_loaded_graph(
            definition,
            source=request.context.caller,
            instigator=request.context.instigator,
            params=request.params,
            overrides=resource.overrides,
            policy=request.policy,
        )


def damage_emp_shields(world, instance, definition, targets):
    """Route B32's 03A ModifyHealth branch to the existing shield health host."""
    caster = caster_in(world.match, world.body)
    if caster is None:
        return
    shields = world.match.combat.shields
    modify = definition.by_rid(2).field("m_modifyHealth")
    amount = expr.to_float(instance.evaluate(modify["m_amount"]))
    frame = instance.component.now // caster.body_script.frame_ms
    for bubble in tuple(shields.bubbles.values()):
        if bubble.entity in targets and bubble.definition in EMP_SHIELD_DEFINITIONS:
            shields.damage(bubble, caster, amount, frame)


def stops(instance, node):
    return (
        not instance.component.strict
        and (instance.graph.index, node.rid) in ((TARGET, 9), (EMP_TARGET, 4))
        and node.field("m_67B81D25") is None
        and node.field("m_EB35D2F1") is None
    )
