"""Authored 104319 projectile-defense states and contact geometry.

Authored states own activation, duration and local box bounds. The
eye-relative pose and full-cone interpretation are server reconstructions.
"""

import math
from dataclasses import dataclass

from ow174.game import defense_matrix
from ow174.game.script import expr, graph

MEI_MELEE_DEFENSE = 0x22F0
MEI_BODY = 0x04000000000008BA

# These direct traveling projectiles have extracted definitions without tag145.
TALON_PROJECTILES = frozenset((
    0x04000000000023F0,  # Enforcer 13EB/24
    0x0400000000001E80,  # Sniper 1122/6
    0x04000000000023BB,  # Sniper 1134/6
))

# The same tag filter applies to these authored immediate weapon shots.
NPC_HITSCAN = {
    (0x102E, 60): 0x0400000000001FE6,  # Trooper
    (0x114F, 47): 0x040000000000206A,  # Heavy Assault
    (0x153B, 14): 0x0400000000002999,  # B.O.B.
}
PLAYER_HITSCAN = {
    (0x0016, 12): frozenset((0x0400000000000018,)),  # Tracer
    (0x0019, 10): frozenset((0x040000000000004A,)),  # Reaper
    (0x001B, 9): frozenset((0x040000000000004C,)),  # Widowmaker unscoped
    (0x001B, 19): frozenset((0x0400000000000107, 0x040000000000004B, 0x0400000000000108)),  # Widowmaker scoped
    (0x012E, 30): frozenset((0x0400000000000205,)),  # Bastion recon
    (0x013A, 26): frozenset((0x0400000000000207,)),  # Bastion sentry
    (0x01C6, 33): frozenset((0x0400000000000405,)),  # Roadhog Whole Hog
    (0x01D2, 31): frozenset((0x04000000000002A7,)),  # Cassidy
    (0x01D2, 37): frozenset((0x04000000000002A7,)),  # Cassidy Fan the Hammer
    (0x0254, 25): frozenset((0x04000000000003D4, 0x0400000000001C85)),  # Soldier and Visor
    (0x0B1B, 10): frozenset((0x0400000000001222,)),  # Sombra
    (0x0B94, 88): frozenset((0x0400000000001615,)),  # Ana scoped rifle
    (0x14FC, 14): frozenset((0x04000000000029A1,)),  # Ashe scoped
    (0x14FC, 220): frozenset((0x040000000000280E,)),  # Ashe unscoped
    (0x1513, 41): frozenset((0x0400000000002B61,)),  # Ashe Coach Gun
    (0x1514, 24): frozenset((0x0400000000002584,)),  # Wrecking Ball
    (0x1932, 10): frozenset((0x0400000000002CFA,)),  # Baptiste
}
ANA_RIFLE_PROJECTILE = 0x04000000000015B7  # 0B94/7 unscoped rifle

# Hero -> graph, node position, state index, authored destroy/absorb mode.
_ROWS = {
    0x02E0000000000029: (0x03A3, 18, 10, False),
    0x02E000000000023B: (0x1D02, 7, 1, True),
    0x02E00000000000DD: (MEI_MELEE_DEFENSE, 14, 6, True),
}


def active_contact(actor, incoming, aim, definition):
    row = _ROWS.get(getattr(getattr(actor, "hero", None), "guid", None))
    script = getattr(actor, "body_script", None)
    if row is None or script is None or script.retired or script.component.owner_dead:
        return None
    graph, position, index, absorb = row
    if graph == MEI_MELEE_DEFENSE and (
        actor.hero.body != MEI_BODY or not defense_matrix.absorbable(definition)
    ):
        return None
    for instance in script.component.instances.values():
        if instance.graph.index != graph:
            continue
        state = instance.states.get(index)
        if (state is None or not state.active or state.ending or state.node.pos != position
                or state.node.cls != "STUStatescriptStateDeflectProjectiles"
                or state.node.field("m_8B251D53") != int(absorb)):
            continue
        angle = expr.to_float(state.evaluate(state.node.field("m_025D0C39"), watch=False))
        if not 0.0 < angle <= 360.0:
            continue
        facing = aim(actor.mover.yaw, actor.mover.pitch)
        alignment = -sum(axis * travel for axis, travel in zip(facing, incoming, strict=True))
        if alignment >= math.cos(math.radians(angle * 0.5)):
            return state, facing, absorb
    return None


def _box_entry(origin, direction, a, b, reach):
    start, stop = 0.0, reach
    for point, delta, bound_a, bound_b in zip(origin, direction, a, b, strict=True):
        low, high = sorted((bound_a, bound_b))
        if abs(delta) < 1e-9:
            if not low <= point <= high:
                return None
            continue
        near, far = sorted(((low - point) / delta, (high - point) / delta))
        start, stop = max(start, near), min(stop, far)
        if start > stop:
            return None
    return start


def intersection(targets, origin, direction, reach, aim, definition):
    best = None
    for target in targets:
        defense = active_contact(target.owner, direction, aim, definition)
        if defense is None:
            continue
        state, forward, _ = defense
        yaw = target.owner.mover.yaw * math.tau / 65536
        right = (math.cos(yaw), 0.0, -math.sin(yaw))
        up = (forward[1] * right[2], forward[2] * right[0] - forward[0] * right[2],
              -forward[1] * right[0])
        eye = (target.feet[0], target.feet[1] + target.eye, target.feet[2])
        relative = tuple(p - e for p, e in zip(origin, eye, strict=True))
        axes = (right, up, forward)
        local_origin = tuple(sum(p * v for p, v in zip(relative, axis, strict=True)) for axis in axes)
        local_direction = tuple(sum(d * v for d, v in zip(direction, axis, strict=True)) for axis in axes)
        for box in state.node.field("m_boxes") or ():
            low = state.evaluate(box.get("m_CBA118B2"), watch=False)
            high = state.evaluate(box.get("m_AEF67365"), watch=False)
            if not isinstance(low, expr.Vec3) or not isinstance(high, expr.Vec3):
                continue
            entry = _box_entry(local_origin, local_direction, low, high, reach)
            if entry is not None and (best is None or (entry, target.entity) < (best[0], best[1].entity)):
                best = entry, target, defense
    return best


def report_absorption(state, source: int, amount: float) -> None:
    """Publish authored absorption outputs for the active defense."""
    instance = state.instance
    source_slot = expr.lvalue(state.node.field("m_0A2CABA3"))
    amount_slot = expr.lvalue(state.node.field("m_F6CDC81C"))
    mei_contact = instance.graph.index == MEI_MELEE_DEFENSE and amount_slot is None
    if source_slot == (expr.INSTANCE, 41) and (mei_contact or amount_slot == (expr.INSTANCE, 6584)):
        with instance.component.operation():
            instance.write(source_slot, expr.Entity(source))
            if amount_slot is not None:
                instance.write(amount_slot, expr.f32(amount))
            instance.follow(state.node, "m_160B5DBE", state)
            instance.component.run_now()


GRASP = 0x1D02
SHIELD_TAG = 0x5847


@dataclass
class ShieldDecay:
    starts: float
    last: float
    rate: float



def grant_shields(combat, entity, instance, node):
    """Explicit reconstruction of 1D02/node34's self-only 1D03 shield request.

    Permissive bodies otherwise skip PlayScript. Consume its actual amount/cap
    arguments and authored decay fields; do not pretend the native engine host
    or 1D03 HealthPool State has executed. Native pool queries, cap stacking and
    presentation still need comparison with the original engine.
    """
    if instance.graph.index != GRASP or node.pos != 34:
        return False
    resource = instance.evaluate(node.field("m_script"))
    target = instance.evaluate(node.field("m_targets"))
    if resource != expr.Asset(0x0580000000001D03) or target != expr.Entity(entity):
        return False
    owner = next((target for target in combat._targets() if target.entity == entity), None)
    if owner is None:
        return True
    script = owner.owner.body_script
    if instance.component.entity != entity or script is None or script.component is not instance.component:
        return False
    absorption = instance.states.get(1)
    if absorption is None or absorption.counter == 0:
        return True
    activation = instance.id, absorption.counter
    if combat.grasp_grants.get(entity) == activation:
        return True
    combat.grasp_grants[entity] = activation
    vitals = combat.vitals_of(owner)
    if vitals.dead:
        return True
    params = {graph.guid(entry["m_B5051BCE"]): expr.to_float(instance.evaluate(entry["m_value"]))
              for entry in node.field("m_params") or []}
    amount, limit = params.get(0x0A4000000000002A, 0.0), params.get(0x0A400000000003D1, 0.0)
    delay = expr.to_float(instance.evaluate(instance.graph.node(71).field("m_timeout")))
    modify = instance.graph.node(70).field("m_modifyHealth")
    rate = expr.to_float(instance.evaluate(modify["m_amount"]))
    if vitals.grant_temporary_shields(SHIELD_TAG, amount, limit) > 0:
        now = instance.component.now / 1000
        combat.grasp_shields[entity] = ShieldDecay(now + delay, now, rate)
        combat._sync_body_health(owner.owner, vitals)
    return True


def update_shields(combat):
    """Elapsed-time decay without hit notices/healing credit or base shield loss."""
    if not combat.grasp_shields:
        return
    owners = {target.entity: target.owner for target in combat._targets()}
    for entity, decay in list(combat.grasp_shields.items()):
        vitals = combat.vitals.get(entity)
        if vitals is None or vitals.dead or entity not in owners:
            del combat.grasp_shields[entity]
            continue
        duration = max(0.0, combat.now - max(decay.last, decay.starts))
        decay.last = max(decay.last, combat.now)
        if vitals.decay_temporary_shields(SHIELD_TAG, duration * decay.rate) > 0:
            combat._sync_body_health(owners[entity], vitals)
        if vitals.temporary_shields[SHIELD_TAG].now <= 0:
            del combat.grasp_shields[entity]
