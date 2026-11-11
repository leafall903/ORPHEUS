"""Scoped defensive casts adapted from Reaper/Pharah lanes.

Native server filters and phase collision are not recovered. Immune solid
bodies intercept shots; mapped ghost phases let them pass. These reconstructed
policies assign no generic meaning to D07EBF01 or10467 outside four casts.
"""

import math
from dataclasses import dataclass

from ow174.game import deflection
from ow174.game.script import expr, graph

# hero -> (graph, entry state, protection state or10467 stack, intangible)
PROTECTIONS = {
    0x02E0000000000002: (0x0020, 1, None, True),
    0x02E00000000001A2: (0x0F7C, 1, None, True),
    0x02E00000000000DD: (0x05FE, 41, 17, False),
    0x02E0000000000020: (0x017B, 13, 8, False),
}
FORTIFY_HERO = 0x02E000000000013E
FORTIFY_GRAPH = 0x0CF4
FORTIFY_CONTROL = 9161


def _scope(actor):
    hero = getattr(getattr(actor, "hero", None), "guid", None)
    spec = PROTECTIONS.get(hero)
    script = getattr(actor, "body_script", None)
    if (spec is None or script is None or script.retired or script.component.owner_dead
            or script.hero != hero):
        return None, None
    return next((i for i in script.component.instances.values() if i.graph.index == spec[0]), None), spec


def advance_entry(actor) -> None:
    """Bridge four zero-delay entries without auxiliary/output scopes."""
    instance, spec = _scope(actor)
    if instance is None or instance.component.strict:
        return
    state = instance.states.get(spec[1])
    if state is None or not state.active or state.node.cls != "STU_54FEB21C":
        return
    if (expr.to_float(state.evaluate(state.node.field("m_ED97A97C"), watch=False)) != 0
            or any(state.node.field(name) is not None
                   for name in ("m_7C8AD485", "m_C7A42E2A", "m_F8CE386A", "m_C46403A5"))):
        raise ValueError("unsupported defensive entry callback configuration")
    auxiliary = state.node.field("m_E5790891")
    if auxiliary and (auxiliary.get("links") or auxiliary.get("m_FF3DAF1E")):
        raise ValueError("unsupported defensive entry auxiliary scope")
    state.request_finish()
    instance.update()


def protection(actor) -> tuple[bool, bool]:
    """(immune, intangible) from the exact live authored protection scope."""
    if getattr(getattr(actor, "hero", None), "guid", None) == 0x02E000000000012F:
        from ow174.game import meteor_strike

        if meteor_strike.protected(actor):
            return True, True
    instance, spec = _scope(actor)
    if instance is None:
        return False, False
    if spec[2] is not None:
        state = instance.states.get(spec[2])
        active = bool(state is not None and state.active and state.node.cls == "STU_D07EBF01"
                      and state.node.field("m_target") == {"$": "STU_B5A0CAF0", "m_EE729DCB": 0})
    else:
        slot = instance.component.entity_var(10467, create=False)
        active = bool(slot is not None and expr.truthy(slot.value()) and any(
            state.active and state.node.cls == "STUStatescriptStateStack"
            and expr.lvalue(state.node.field("m_out_Var")) == (1, 10467)
            for state in instance.states.values()
        ))
    return active, active and spec[3]


def control_immune(actor) -> bool:
    """Mapped protection, finite Zarya bubbles and scoped Fortify control policy.

    0CF4's actual9161 Stack also excludes Orisa Halt targets in the authored
    resources. Native control-tag/filter combination and cleanse remain open.
    """
    if protection(actor)[0]:
        return True
    fight = getattr(getattr(actor, "match", None), "combat", None)
    bubbles = getattr(fight, "zarya_bubbles", None)
    if bubbles is not None and bubbles.for_target(actor) is not None:
        return True  # finite bubble control policy; no generic native cleanse claim
    return fortified(actor)


def fortified(actor) -> bool:
    """The current Orisa body has the active authored Fortify stack."""
    hero = getattr(getattr(actor, "hero", None), "guid", None)
    script = getattr(actor, "body_script", None)
    if (hero != FORTIFY_HERO or script is None or script.hero != hero
            or script.retired or script.component.owner_dead):
        return False
    value = script.component.entity_var(FORTIFY_CONTROL, create=False)
    if value is None or not expr.truthy(value.value()):
        return False
    return any(
        state.active and state.node.cls == "STUStatescriptStateStack"
        and expr.lvalue(state.node.field("m_out_Var")) == (1, FORTIFY_CONTROL)
        for instance in script.component.instances.values() if instance.graph.index == FORTIFY_GRAPH
        for state in instance.states.values()
    )


def _box_hit(origin, direction, low, high, reach):
    enter, leave = 0.0, reach
    for p, v, a, b in zip(origin, direction, low, high, strict=True):
        a, b = min(a, b), max(a, b)
        if abs(v) < 1e-9:
            if p < a or p > b:
                return None
            continue
        near, far = (a - p) / v, (b - p) / v
        enter, leave = max(enter, min(near, far)), min(leave, max(near, far))
        if enter > leave:
            return None
    return enter


def projectile_gate(origin, direction, reach, targets, definition=None):
    """Reconstructed Genji/Sigma boxes, adapted from the Pharah lane.

    Live authored boxes and mode with eye-relative yaw placement. Native
    pitch, attack filters, and muzzle geometry remain unverified.
    """
    best = None
    supported = {0x02E0000000000029: (0x03A3, 0), 0x02E000000000023B: (0x1D02, 1)}
    for target in targets:
        owner = target.owner
        hero = getattr(getattr(owner, "hero", None), "guid", None)
        if hero == 0x02E000000000007A and deflection.absorbable(definition):
            yaw = owner.mover.yaw * 2 * math.pi / 65536
            pitch = owner.mover.pitch * 2 * math.pi / 65536
            forward = (math.sin(yaw) * math.cos(pitch), -math.sin(pitch),
                       math.cos(yaw) * math.cos(pitch))
            anchor = (target.feet[0], target.feet[1] + target.eye, target.feet[2])
            distance = deflection.matrix_distance(owner, origin, direction, reach, anchor, forward)
            if distance is not None and (best is None or distance < best[0]):
                best = (distance, target, 1)
        spec = supported.get(hero)
        script = getattr(owner, "body_script", None)
        if spec is None or script is None or script.retired or script.component.owner_dead:
            continue
        yaw = owner.mover.yaw * 2 * math.pi / 65536
        sine, cosine = math.sin(yaw), math.cos(yaw)
        x, y, z = target.feet
        delta = (origin[0] - x, origin[1] - y - target.eye, origin[2] - z)

        def local(vector, sine=sine, cosine=cosine):
            return (cosine * vector[0] - sine * vector[2], vector[1],
                    sine * vector[0] + cosine * vector[2])

        for instance in script.component.instances.values():
            if instance.graph.index != spec[0]:
                continue
            for state in instance.states.values():
                if (not state.active or state.node.cls != "STUStatescriptStateDeflectProjectiles"
                        or state.node.field("m_8B251D53") != spec[1]):
                    continue
                for box in state.node.field("m_boxes") or []:
                    low = state.evaluate(box.get("m_CBA118B2"), watch=False)
                    high = state.evaluate(box.get("m_AEF67365"), watch=False)
                    if not isinstance(low, expr.Vec3) or not isinstance(high, expr.Vec3):
                        continue
                    distance = _box_hit(local(delta), local(direction), low, high, reach)
                    if distance is not None and (best is None or distance < best[0]):
                        best = (distance, target, spec[1])
    return best


def grasp_absorbed(owner, amount):
    if getattr(getattr(owner, "hero", None), "guid", None) != 0x02E000000000023B:
        return
    for instance in owner.body_script.component.instances.values():
        if instance.graph.index != 0x1D02:
            continue
        state = next((state for state in instance.states.values()
                      if state.active and state.node.cls == "STUStatescriptStateDeflectProjectiles"), None)
        if state is not None:
            # Sigma lane's 1D02 accumulator bridge. The end graph owns the grant.
            instance.write((graph.INSTANCE, 6584), float(amount))
            instance.follow(state.node, "m_160B5DBE", state)
            instance.component.run_now()
            return


SHIELD_TAG = 0x5847


@dataclass
class ShieldDecay:
    starts: float
    last: float
    rate: float


def grant_grasp_shields(combat, entity, instance, node):
    """Explicit reconstruction of 1D02/node34's self-only 1D03 shield request.

    Permissive bodies otherwise skip PlayScript. Consume its actual amount/cap
    arguments and authored decay fields; do not pretend the native engine host
    or 1D03 HealthPool State has executed. Native pool queries, cap stacking and
    presentation still need comparison with the original engine.
    """
    if instance.graph.index != 0x1D02 or node.pos != 34:
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


def update_temporary_shields(combat, active, tag):
    """Elapsed-time decay without hit notices/healing credit or base shield loss."""
    if not active:
        return
    owners = {target.entity: target.owner for target in combat._targets()}
    for entity, decay in list(active.items()):
        vitals = combat.vitals.get(entity)
        if vitals is None or vitals.dead or entity not in owners:
            del active[entity]
            continue
        duration = max(0.0, combat.now - max(decay.last, decay.starts))
        decay.last = max(decay.last, combat.now)
        if vitals.decay_temporary_shields(tag, duration * decay.rate) > 0:
            combat._sync_body_health(owners[entity], vitals)
        if vitals.temporary_shields[tag].now <= 0:
            del active[entity]


def parries_melee(target, origin):
    low, high = target.segment()
    point = (low[0], min(max(origin[1], low[1]), high[1]), low[2])
    toward = tuple(a - b for a, b in zip(point, origin, strict=True))
    length = math.sqrt(sum(v * v for v in toward))
    if length < 1e-9:
        return False
    hit = projectile_gate(
        origin, tuple(v / length for v in toward), max(0, length - target.radius), (target,),
    )
    return hit is not None and hit[2] == 0
