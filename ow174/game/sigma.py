"""Reconstructed Sigma combat services over the extracted build-104319 graphs.

Grasp's box and absorption amount output come from 1D02 state1. The server's
box transform, interception ordering and nominal damage accounting are rebuilt;
this adapter is not proof of native engine services or client presentation.
"""

import math
from dataclasses import dataclass

from ow174.game import bots
from ow174.game.script import expr, graph

HERO = 0x02E000000000023B
GRASP = 0x1D02
# Classified ordinary bullets with executed fixtures: Soldier, Trooper, Heavy.
# Other hitscan/beam families stay open until their eligibility is established.
GRASP_HITSCAN_VOLLEYS = {(0x0254, 10), (0x102E, 23), (0x114F, 25)}
UNABSORBABLE_PROJECTILES = {0x1BBB}  # Accretion is not an ordinary absorbable projectile.
SHIELD_TAG = 0x5847


@dataclass
class ShieldDecay:
    starts: float
    last: float
    rate: float


def ray_box(origin, direction, low, high, reach):
    """First nonnegative intersection with a local axis-aligned box, or None."""
    start, stop = 0.0, reach
    for point, vector, minimum, maximum in zip(origin, direction, low, high, strict=True):
        if abs(vector) < 1e-9:
            if point < minimum or point > maximum:
                return None
            continue
        enter, leave = (minimum - point) / vector, (maximum - point) / vector
        start, stop = max(start, min(enter, leave)), min(stop, max(enter, leave))
        if start > stop:
            return None
    return start


def grasp_intersection(combat, shooter, origin, direction, reach):
    """Nearest live enemy Grasp box before the caller's nearer body/wall bound.

    The local box is reconstructed as view-oriented and eye-relative. Bounds
    themselves are evaluated from the actual active State, including body scale.
    """
    best = None
    for target in combat._enemies(shooter):
        actor = target.owner
        npc = isinstance(actor, bots.Bot)
        hero = actor.hero if npc else actor.hero.guid
        script = actor.body_script
        if hero != HERO or script is None or script.component.owner_dead:
            continue
        yaw, pitch = (actor.yaw, 0) if npc else (actor.mover.yaw, actor.mover.pitch)
        yaw, pitch = yaw * math.tau / 65536, pitch * math.tau / 65536
        sy, cy, sp, cp = math.sin(yaw), math.cos(yaw), math.sin(pitch), math.cos(pitch)
        basis = ((cy, 0, -sy), (sy * sp, cp, cy * sp), (sy * cp, -sp, cy * cp))
        eye = (target.feet[0], target.feet[1] + target.eye, target.feet[2])
        delta = tuple(a - b for a, b in zip(origin, eye, strict=True))
        local_origin = tuple(sum(a * b for a, b in zip(delta, axis, strict=True)) for axis in basis)
        local_direction = tuple(sum(a * b for a, b in zip(direction, axis, strict=True)) for axis in basis)
        for instance in script.component.instances.values():
            if instance.graph.index != GRASP:
                continue
            state = instance.states.get(1)
            if state is None or not state.active:
                continue
            for box in state.node.field("m_boxes") or []:
                low = state.evaluate(box.get("m_CBA118B2"), watch=False)
                high = state.evaluate(box.get("m_AEF67365"), watch=False)
                if not isinstance(low, expr.Vec3) or not isinstance(high, expr.Vec3):
                    continue
                hit = ray_box(local_origin, local_direction, low, high, reach)
                if hit is not None and (best is None or hit < best[0]):
                    best = hit, state
    return best


def absorbed(state, amount):
    """Feed the actual 1D02 absorption output and its authored accumulator plug.

    6584 is the per-hit amount; the authored node32 adds it to21312. Do not grant
    health or bypass the authored end/PlayScript path here. The declared
    reconstructed shield request is handled separately; native pool services
    and original-client presentation remain open.
    """
    state.instance.write((graph.INSTANCE, 6584), float(amount))
    state.instance.follow(state.node, "m_160B5DBE", state)
    state.instance.component.run_now()


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
    amount, limit = params[0x0A4000000000002A], params[0x0A400000000003D1]
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
