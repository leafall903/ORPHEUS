"""Authored Sigma 1D02 Grasp with reconstructed interception and shield storage."""
from dataclasses import dataclass

from ow174.game import bots, deflection
from ow174.game.script import expr, graph, nodes
from ow174.game.sigma_barrier import view_basis

HERO = 0x02E000000000023B
GRASP = 0x1D02
SHIELD_TAG = 0x5847
TRACER_ROUND = 0x0400000000000018
GRASP_CREDIT = 21312
SHIELD_AMOUNT = 0x0A4000000000002A
SHIELD_LIMIT = 0x0A400000000003D1


@dataclass
class ShieldDecay:
    starts: float
    last: float
    rate: float


@dataclass
class Conversion:
    starts: int
    tick: int
    credit: float
    scale: float
    limit: float
    delay: float
    rate: float


def _activation(state):
    return state.instance.id, state.counter


def _active_states(actor):
    hero = actor.hero if isinstance(actor, bots.Bot) else actor.hero.guid
    script = actor.body_script
    if (hero != HERO or script is None or script.component.owner_dead
            or script.component.fault is not None):
        return
    for instance in script.component.instances.values():
        if instance.graph.index == GRASP:
            state = instance.states.get(1)
            if state is not None and state.active:
                yield state


def _boxes(state):
    boxes = []
    for box in state.node.field("m_boxes") or ():
        low = state.evaluate(box.get("m_CBA118B2"), watch=False)
        high = state.evaluate(box.get("m_AEF67365"), watch=False)
        if isinstance(low, expr.Vec3) and isinstance(high, expr.Vec3):
            boxes.append((tuple(low), tuple(high)))
    return tuple(boxes)


def grasp_boxes(actor):
    """Capture authored bounds with the activation that owns their absorption output."""
    return tuple((_activation(state), _boxes(state)) for state in _active_states(actor))


def grasp_intersection(combat, shooter, origin, direction, reach, *, team=None, poses=None, tick=None):
    """Nearest enemy Grasp box before the caller's nearer body/wall bound."""
    best = None
    for target in combat._enemies(shooter, team=team, poses=poses, tick=tick):
        actor = target.owner
        sample = poses.get((target.entity, tick)) if poses else None
        if sample is not None:
            if not sample.grasp:
                continue
            basis, captured = sample.grasp
            live = {_activation(state): state for state in _active_states(actor)}
            candidates = []
            for activation, boxes in captured:
                receiver = live.get(activation)
                if receiver is None:
                    conversion = combat.grasp_conversions.get((target.entity, activation))
                    if conversion is not None and tick <= conversion.tick:
                        receiver = conversion
                if receiver is not None:
                    candidates.append((receiver, boxes))
        else:
            if combat.is_stunned(actor):
                continue
            candidates = [(state, _boxes(state)) for state in _active_states(actor)]
            if isinstance(actor, bots.Bot):
                basis = view_basis(actor.yaw, 0)
            else:
                basis = view_basis(actor.mover.yaw, actor.mover.pitch)
        if not candidates:
            continue
        eye = (target.feet[0], target.feet[1] + target.eye, target.feet[2])
        delta = tuple(a - b for a, b in zip(origin, eye, strict=True))
        local_origin = tuple(sum(a * b for a, b in zip(delta, axis, strict=True)) for axis in basis)
        local_direction = tuple(sum(a * b for a, b in zip(direction, axis, strict=True)) for axis in basis)
        for receiver, boxes in candidates:
            for low, high in boxes:
                hit = deflection.ray_box(local_origin, local_direction, low, high, reach)
                if hit is not None and (best is None or hit < best[0]):
                    best = hit, receiver
    return best


def absorbed(state, amount):
    """Credit 1D02's accumulator or its pending end request.

    For a live activation the authored node32 adds the per-hit amount in 6584
    to 21312. A deferred request retains that accumulator until world catch-up
    reaches the activation's end.
    """
    if isinstance(state, Conversion):
        state.credit = expr.f32(state.credit + amount)
        return
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
    activation = _activation(absorption)
    if combat.grasp_grants.get(entity) == activation:
        return True
    combat.grasp_grants[entity] = activation
    vitals = combat.vitals_of(owner)
    if vitals.dead:
        return True
    params = {graph.guid(entry["m_B5051BCE"]): entry["m_value"] for entry in node.field("m_params") or ()}
    limit = expr.to_float(instance.evaluate(params[SHIELD_LIMIT]))
    delay = expr.to_float(instance.evaluate(instance.graph.node(71).field("m_timeout")))
    modify = instance.graph.node(70).field("m_modifyHealth")
    rate = expr.to_float(instance.evaluate(modify["m_amount"]))
    at = combat.future_command_tick(entity, instance.component.frame) if combat.pulse_bombs.bombs else None
    scale = _batch_conversion_scale(params[SHIELD_AMOUNT]) if at is not None else None
    if scale is not None:
        credit = expr.to_float(instance.context().var(graph.INSTANCE, GRASP_CREDIT))
        component = instance.component
        starts = at + absorption.start // component.frame_ms - component.frame + 1
        combat.grasp_conversions[entity, activation] = Conversion(
            starts, at, credit, scale, limit, delay, rate,
        )
    else:
        amount = expr.to_float(instance.evaluate(params[SHIELD_AMOUNT]))
        _grant(combat, owner, amount, limit, delay, rate, combat.now)
    return True


def _batch_conversion_scale(cfg):
    """Recognize the authored node34 expression: #21312 multiplied by its literal scale."""
    data = cfg.get("m_expression") or {}
    variables = data.get("m_dynamicVars") or ()
    factors = data.get("m_D99EF254") or ()
    if (data.get("m_opcodes") == [8, 0, 6, 0, 25, 0] and len(factors) == len(variables) == 1
            and expr.dynamic(variables[0]) == (graph.INSTANCE, GRASP_CREDIT)):
        return expr.f32(factors[0])
    return None


def _grant(combat, target, amount, limit, delay, rate, now):
    vitals = combat.vitals_of(target)
    if vitals.grant_temporary_shields(SHIELD_TAG, amount, limit) > 0:
        combat.grasp_shields[target.entity] = ShieldDecay(now + delay, now, rate)
        combat._sync_body_health(target.owner, vitals)


def prepare_interrupt(combat, entity, component):
    """Cancel a future shield request and identify the interrupted cooldown clock."""
    cancelled = {}
    for key, conversion in tuple(combat.grasp_conversions.items()):
        if key[0] == entity and conversion.starts <= combat.tick <= conversion.tick:
            cancelled[key[1]] = combat.grasp_conversions.pop(key)

    anchor = combat.command_pose_anchors.get(entity)
    if anchor is None or not anchor.trusted:
        return None
    gap = combat.match.tick - anchor.world
    if (not 0 < combat.tick - anchor.world <= gap or anchor.latest != anchor.first + gap
            or component.frame != anchor.latest):
        return None
    hit_frame = anchor.first + combat.tick - anchor.world
    # A combat tick runs before the command for that tick.
    grasp = dict(anchor.grasp_frames).get(hit_frame - 1, ())
    for activation, _ in grasp:
        instance = component.instances.get(activation[0])
        if instance is None or instance.graph.index != GRASP:
            continue
        state, ability = instance.states.get(1), instance.states.get(0)
        if state is None or _activation(state) != activation or not isinstance(ability, nodes.Ability):
            continue
        conversion = cancelled.get(activation)
        if conversion is not None:
            instance.write((graph.INSTANCE, GRASP_CREDIT), conversion.credit)
        if state.active or conversion is not None:
            return ability, hit_frame * component.frame_ms - 1
    return None


def finish_conversions(combat, through, tick_seconds):
    """Apply each authored end request after the last possible interception."""
    for key, conversion in sorted(combat.grasp_conversions.items(), key=lambda item: item[1].tick):
        if conversion.tick > through:
            break
        del combat.grasp_conversions[key]
        entity, _ = key
        target = next((target for target in combat._targets() if target.entity == entity), None)
        if target is not None:
            now = combat.now - (combat.tick - conversion.tick) * tick_seconds
            amount = expr.f32(conversion.credit * conversion.scale)
            _grant(combat, target, amount, conversion.limit, conversion.delay, conversion.rate, now)


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
