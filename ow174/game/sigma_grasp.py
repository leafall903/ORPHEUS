"""Reconstructed Sigma combat services over the extracted build-104319 graphs.

Grasp's box and absorption amount output come from 1D02 state1. The server's
box transform, interception ordering and nominal damage accounting are rebuilt;
this adapter is not proof of native engine services or client presentation.
"""

from dataclasses import dataclass

from ow174.game import bots, defense_boxes
from ow174.game.script import expr, graph

HERO = 0x02E000000000023B
GRASP = 0x1D02
SHIELD_TAG = 0x5847
# Authored 1D02 excludes target tag 0145. The Sniper rounds and Enforcer
# pellets have projectile tag 02F9 and no excluded tag.
NPC_PROJECTILES = frozenset((0x0400000000001E80, 0x04000000000023BB, 0x04000000000023F0))
# Verified 02F9-tagged hitscan entities without the excluded tag, including both
# Soldier rifle definitions and the Trooper and Heavy Assault primary definitions.
HITSCAN_PROJECTILES = frozenset((0x04000000000003D4, 0x0400000000001C85,
                                 0x0400000000001FE6, 0x040000000000206A))


@dataclass
class ShieldDecay:
    starts: float
    last: float
    rate: float


def grasp_intersection(combat, shooter, origin, direction, reach):
    """Nearest live enemy Grasp box before the caller's nearer body/wall bound.

    The local box is reconstructed as view-oriented and eye-relative. Bounds
    themselves are evaluated from the actual active State, including body scale.
    """
    best = None
    for target in combat._enemies(shooter):
        if target.construct is not None:
            continue
        actor = target.owner
        npc = isinstance(actor, bots.Bot)
        hero = actor.hero if npc else actor.hero.guid
        script = actor.body_script
        if hero != HERO or script is None or script.component.owner_dead:
            continue
        yaw, pitch = (actor.yaw, 0) if npc else (actor.mover.yaw, actor.mover.pitch)
        eye = (target.feet[0], target.feet[1] + target.eye, target.feet[2])
        for instance in script.component.instances.values():
            if instance.graph.index != GRASP:
                continue
            state = instance.states.get(1)
            if state is None or not state.active:
                continue
            hit = defense_boxes.intersect(state, eye, yaw, pitch, origin, direction, reach)
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
