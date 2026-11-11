"""Authored Symmetra primary with reconstructed beam collision."""

import math

from ow174.game.barriers import Barrier, Bubble, Dome
from ow174.game.commands import FIRE
from ow174.game.script import expr
from ow174.game.script.combat_pose import active_pose

HERO = 0x02E0000000000016
PRIMARY = 0x157C


def stage_predicate(world, cfg, instance):
    if world.match is None or instance is None or instance.graph.index != PRIMARY:
        return NotImplemented
    exists, eligible, _ = instance.graph.node(48).field("m_condition")["m_configVars"]
    if cfg is not exists and cfg is not eligible:
        return NotImplemented
    target = expr.to_entity(instance.evaluate(cfg["m_entity"]))
    fight = world.match.combat
    barrier = fight.barriers.live_target(target)
    if cfg is exists:
        return barrier is not None or any(body.entity == target for body in fight._targets())
    source = next((p for p in world.match.players if p.body == world.body), None)
    if source is None:
        return False
    if barrier is not None:
        owner = barrier.caster if isinstance(barrier, Bubble) else barrier.player
        return owner is not source and (world.match.game_map.free_for_all or owner.team != source.team)
    return any(body.entity == target for body in fight._enemies(source))


def ammo_predicate(world, cfg, instance):
    if (world.match is None or instance is None or instance.graph.index != PRIMARY
            or cfg is not instance.graph.node(185).field("m_condition")):
        return NotImplemented
    entity = expr.to_entity(instance.evaluate(cfg["m_entity"]))
    # The hosted 011A, 01C3, 03DA and 13F4 barrier definitions carry tag 03A.
    return world.match.combat.barriers.live_target(entity) is not None


def _command(world, state):
    pose = active_pose.get()
    if pose is None or pose[0] is not world:
        return None
    _, player, command = pose
    fight = world.match.combat
    if (world.match.collision_world is None or fight.is_dead(player) or fight.is_stunned(player)
            or not command.buttons & FIRE or state.instance.component.frame != command.frame):
        return None
    return fight, player, command


def _trace(fight, player, command, instance, node):
    reach = expr.to_float(instance.evaluate(node.field("m_F5732403.m_6A79BD98")))
    radius = expr.to_float(instance.evaluate(node.field("m_57DAE1B5")))
    target, barrier = fight.beam_target(player, command, reach, radius)
    return barrier if barrier is not None and barrier.entity else target


def track_ray(world, state, active):
    instance, node = state.instance, state.node
    if instance.graph.index != PRIMARY or node.pos not in (202, 203):
        return NotImplemented
    output = expr.dynamic(node.field("m_target.m_692DFF72"))
    if not active:
        instance.write(output, None)
        return True
    current = _command(world, state)
    if current is not None:
        fight, player, command = current
        target = _trace(fight, player, command, instance, node)
        instance.write(output, expr.Entity(target.entity) if target is not None else None)
    return True


def modify_health(world, state, elapsed_ms):
    if state.instance.graph.index != PRIMARY or state.node.pos != 170:
        return NotImplemented
    current = _command(world, state)
    if current is None or not elapsed_ms:
        return True
    fight, player, command = current
    selected = state.evaluate(state.node.field("m_FA3ECC43"), watch=False)
    if not isinstance(selected, expr.Entity):
        return True
    settings = state.node.field("m_modifyHealth")
    rate = expr.to_float(state.evaluate(settings["m_amount"], watch=False))
    damage = rate * min(elapsed_ms, state.instance.component.frame_ms) / 1000
    if math.isfinite(damage) and damage > 0:
        # Stack204 can still expose its previous output on the first tick after aim changes.
        instance = state.instance
        target = _trace(fight, player, command, instance, instance.graph.node(202))
        if target is None:
            target = _trace(fight, player, command, instance, instance.graph.node(203))
        if target is not None and target.entity == selected.id:
            if isinstance(target, (Barrier, Bubble, Dome)):
                fight.barriers.damage(target, expr.f32(damage), player.body)
            else:
                loadout = state.evaluate(settings["m_loadout"], watch=False)
                fight._damage(player, target, expr.f32(damage), False, loadout.guid, command.frame)
    return True
