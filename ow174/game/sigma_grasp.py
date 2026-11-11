"""The 104319 Sigma 1D02 projectile interception and temporary shield request."""

from dataclasses import dataclass

from ow174.game import genji_deflect, sigma_barrier
from ow174.game.script import expr, graph

GRASP = 0x1D02
SHIELD_TAG = 0x5847


@dataclass
class ShieldDecay:
    starts: float
    last: float
    rate: float


def cast(fight, shooter, definition, origin, direction, reach):
    """Sweep supported Mei missiles through active, eye-relative Grasp boxes."""
    from ow174.game.roadhog_projectiles import SCRAP, WHOLE_HOG
    from ow174.game.symmetra_beam import ORB

    if (definition not in (genji_deflect.ICICLE, genji_deflect.BLIZZARD, ORB, WHOLE_HOG)
            and definition not in SCRAP):
        return None
    best = None
    for target in fight._enemies(shooter):
        owner = target.owner
        script = getattr(owner, "body_script", None)
        if (script is None or getattr(script, "hero", None) != sigma_barrier.HERO
                or script.component.owner_dead or script.component.strict
                or fight.frozen(owner) or fight.is_stunned(owner)):
            continue
        instance = next((i for i in script.component.instances.values() if i.graph.index == GRASP), None)
        state = instance.states.get(1) if instance else None
        if state is None or not state.active:
            continue
        eye = fight._eye(owner)
        basis = sigma_barrier.view_basis(owner.mover.yaw, owner.mover.pitch)
        delta = tuple(a - b for a, b in zip(origin, eye, strict=True))
        local_origin = tuple(sum(a * b for a, b in zip(delta, axis, strict=True)) for axis in basis)
        local_direction = tuple(sum(a * b for a, b in zip(direction, axis, strict=True)) for axis in basis)
        for box in state.node.field("m_boxes") or ():
            low = state.evaluate(box["m_CBA118B2"], watch=False)
            high = state.evaluate(box["m_AEF67365"], watch=False)
            if not isinstance(low, expr.Vec3) or not isinstance(high, expr.Vec3):
                continue
            distance = genji_deflect.box_ray(local_origin, local_direction, low, high, reach)
            if distance is not None and (best is None or distance < best[0]):
                best = distance, state
    return best


def absorbed(state, amount):
    """Feed the hit output and let authored node 32 accumulate it."""
    state.instance.write((graph.INSTANCE, 6584), float(amount))
    state.instance.follow(state.node, "m_160B5DBE", state)
    state.instance.component.run_now()


def interrupt(owner):
    script = getattr(owner, "body_script", None)
    if script is None or getattr(script, "hero", None) != sigma_barrier.HERO:
        return
    instance = next((i for i in script.component.instances.values() if i.graph.index == GRASP), None)
    ability = instance.states.get(0) if instance else None
    if ability is not None:
        ability.stop()


def grant_shields(combat, entity, instance, node):
    """Explicit reconstruction of 1D02/node34's self-only 1D03 shield request.

    Permissive bodies otherwise skip PlayScript. Consume its actual amount/cap
    arguments and authored decay fields; do not pretend the native engine host
    or 1D03 HealthPool State has executed. Native pool queries, cap stacking and
    presentation still need comparison with the original engine.
    """
    if instance.component.strict or instance.graph.index != GRASP or node.pos != 34:
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
        now = max(combat.now, instance.component.now / 1000)
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
