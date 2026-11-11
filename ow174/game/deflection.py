"""Authored Deflect and Grasp boxes in a reconstructed eye-relative basis."""

import math

from ow174.game.script import expr, graph

GENJI = 0x02E0000000000029
SIGMA = 0x02E000000000023B
BULLET_TAG = 0x0D800000000003BF


def _dot(left, right):
    return sum(a * b for a, b in zip(left, right, strict=True))


def _box_distance(origin, direction, low, high, reach):
    enter, leave = 0.0, reach
    for point, ray, a, b in zip(origin, direction, low, high, strict=True):
        lower, upper = sorted((a, b))
        if abs(ray) < 1e-9:
            if not lower <= point <= upper:
                return None
            continue
        first, last = sorted(((lower - point) / ray, (upper - point) / ray))
        enter, leave = max(enter, first), min(leave, last)
        if enter > leave:
            return None
    return enter


def _active(fight, owner, graph, index, cls):
    if fight.is_stunned(owner):
        return None
    script = owner.body_script
    if script is None:
        return None
    instance = next((i for i in script.component.instances.values() if i.graph.index == graph), None)
    state = instance.states.get(index) if instance is not None else None
    if state is None or not state.active or state.node.cls != cls:
        return None
    if graph == 0x03A3:
        # Wait 1 owns both Deflect's projectile box and its melee filter.
        timer = instance.states.get(1)
        if timer is not None and timer.active:
            expiry = (timer.start + timer.ms) // script.frame_ms
            if fight.tick >= expiry:
                return None
    if graph == 0x1D02:
        end = _grasp_end_ms(instance)
        if end is not None and fight.tick >= end // script.frame_ms:
            return None
    if graph == 0x02CC and _matrix_depleted(instance, fight.tick):
        return None
    return state


def _matrix_depleted(instance, tick):
    """Bound an active Matrix by the remaining resource on its authored drain."""
    drain = instance.states.get(45)
    if drain is None or not drain.active:
        return False
    rate = expr.to_float(drain.evaluate(drain.node.field('m_9B0385B4'), watch=False))
    remaining = expr.to_float(drain.cur)
    if not math.isfinite(rate) or not math.isfinite(remaining) or rate <= 0:
        return False
    end = drain.last + round(1000 * remaining / rate)
    return tick >= end // instance.component.frame_ms


def matrix_held_in_gap(owner):
    """Keep draining a held Matrix through a gap in movement poses."""
    script = owner.body_script
    if owner.hero.guid != graph.DVA or script.component.owner_dead:
        return False
    last = owner.mover.last_command
    if last is None or not last.buttons & 2:
        return False
    return any(
        instance.graph.index == 0x02CC and (state := instance.states.get(3)) is not None and state.active
        for instance in script.component.instances.values()
    )


def blocks_melee(fight, attacker, target, state, modify):
    """Apply Genji's zero scalar to matching frontal melee tags."""
    from ow174.game.combat import aim

    owner = target.owner
    if getattr(getattr(owner, 'hero', None), 'guid', None) != GENJI:
        return False
    filtering = _active(fight, owner, 0x03A3, 59, 'STUStatescriptStateCombatModFilter')
    if filtering is None:
        return False
    authored = {expr.Asset(int(tag, 16)) for tag in filtering.node.field('m_F60AA143') or ()}
    matches = any(state.evaluate(row.get('m_EE3055BD'), watch=False) in authored
                  for row in modify.get('m_148F3152') or ())
    if not matches or filtering.evaluate(filtering.node.field('m_50530EAB'), watch=False) != 0:
        return False
    toward = tuple(a - b for a, b in zip(fight._eye(attacker), fight._eye(owner), strict=True))
    return _dot(toward, aim(owner.mover.yaw, owner.mover.pitch)) > 0


def intercept(fight, shooter, origin, direction, reach, *, matrix_eligible=False):
    """Return the nearest active enemy Deflect, Grasp or Matrix contact."""
    from ow174.game.combat import aim

    best = None
    for target in fight._enemies(shooter):
        owner = target.owner
        hero = getattr(getattr(owner, 'hero', None), 'guid', None)
        if hero == GENJI:
            graph_id, index = 0x03A3, 10
        elif hero == SIGMA:
            graph_id, index = 0x1D02, 1
        elif hero == graph.DVA and matrix_eligible:
            graph_id, index = 0x02CC, 3
        else:
            continue
        state = _active(fight, owner, graph_id, index, 'STUStatescriptStateDeflectProjectiles')
        if state is None:
            continue
        absorbing = hero in (SIGMA, graph.DVA)
        if absorbing and state.node.field('m_8B251D53') != 1:
            continue
        yaw, pitch = owner.mover.yaw, owner.mover.pitch
        forward = aim(yaw, pitch)
        angle = state.evaluate(state.node.field('m_025D0C39'), watch=False)
        if not isinstance(angle, (int, float)) or -_dot(forward, direction) <= math.cos(math.radians(angle)):
            continue
        y, p = yaw * math.tau / 65536, pitch * math.tau / 65536
        right = (math.cos(y), 0, -math.sin(y))
        up = (math.sin(y) * math.sin(p), math.cos(p), math.cos(y) * math.sin(p))
        axes = (right, up, forward)
        delta = tuple(a - b for a, b in zip(origin, fight._eye(owner), strict=True))
        local_origin = tuple(_dot(delta, axis) for axis in axes)
        local_direction = tuple(_dot(direction, axis) for axis in axes)
        for row in state.node.field('m_boxes') or ():
            low = state.evaluate(row.get('m_CBA118B2'), watch=False)
            high = state.evaluate(row.get('m_AEF67365'), watch=False)
            if isinstance(low, expr.Vec3) and isinstance(high, expr.Vec3):
                hit = _box_distance(local_origin, local_direction, (low.x, low.y, low.z),
                                    (high.x, high.y, high.z), reach)
                if hit is not None and (best is None or hit < best[0]):
                    best = hit, owner, forward, state if absorbing else None
    return best


def absorb(state, amount):
    """Deliver one intercepted round's damage to the authored Grasp plug."""
    if state.instance.graph.index == 0x02CC:
        return  # Matrix spends its own graph resource and has no Grasp accumulator.
    output = expr.lvalue(state.node.field('m_F6CDC81C'))
    if output is not None:
        state.instance.write(output, expr.f32(amount))
    state.instance.follow(state.node, 'm_160B5DBE', state)
    state.instance.component.run_now()


def _grasp_end_ms(instance):
    chase = instance.states.get(20)
    if chase is None or not chase.active:
        return None
    initial = expr.to_float(chase.evaluate(chase.node.field('m_EB0FC261'), watch=False))
    rate = expr.to_float(chase.evaluate(chase.node.field('m_9B0385B4'), watch=False))
    if not math.isfinite(initial) or not math.isfinite(rate) or rate <= 0:
        return None
    return chase.start + round(1000 * initial / rate)


def advance_grasp(fight):
    """Settle conversion and decay before damage on each simulated world tick."""
    for owner in fight.match.players:
        if owner.hero.guid != SIGMA or owner.body_script is None:
            continue
        instances = owner.body_script.component.instances.values()
        instance = next((i for i in instances if i.graph.index == 0x1D02), None)
        if instance is None:
            continue
        vitals = fight.vitals.get(owner.body)
        if vitals is not None:
            _settle_grasp_decay(fight, owner, vitals, instance)
        state = instance.states.get(1)
        if state is None or not state.active:
            continue
        if vitals is not None and (vitals.dead or vitals.grasp_activation == (instance.id, state.counter)):
            continue
        end = _grasp_end_ms(instance)
        if end is not None and fight.tick >= end // instance.component.frame_ms:
            grant_grasp(fight, instance, instance.graph.node(34), granted_at=end)


def grant_grasp(fight, instance, node, *, granted_at=None):
    """Host the authored self-only shield request in the temporary pool."""
    if instance.graph.index != 0x1D02 or node.pos != 34:
        return
    entity = instance.component.entity
    if (instance.evaluate(node.field('m_script')) != expr.Asset(0x0580000000001D03)
            or instance.evaluate(node.field('m_targets')) != expr.Entity(entity)):
        return
    target = next((target for target in fight._targets() if target.entity == entity), None)
    state = instance.states.get(1)
    if (target is None or target.owner.body_script is None
            or target.owner.body_script.component is not instance.component
            or state is None or not state.counter):
        return
    vitals = fight.vitals_of(target)
    activation = instance.id, state.counter
    if vitals.dead or vitals.grasp_activation == activation:
        return
    vitals.grasp_activation = activation
    params = {graph.guid(row['m_B5051BCE']): expr.to_float(instance.evaluate(row['m_value']))
              for row in node.field('m_params') or ()}
    amount, cap = params.get(0x0A4000000000002A, 0), params.get(0x0A400000000003D1, 0)
    updated = min(cap, vitals.temporary_shields + amount)
    if updated > vitals.temporary_shields:
        vitals.set_temporary_shields(updated)
        fight._sync_body_health(target.owner, vitals)
    if amount > 0 and vitals.temporary_maximum:
        delay = expr.to_float(instance.evaluate(instance.graph.node(71).field('m_timeout')))
        start = instance.component.now if granted_at is None else granted_at
        if math.isfinite(delay):
            vitals.grasp_decay = start + round(delay * 1000)
            _settle_grasp_decay(fight, target.owner, vitals, instance)


def _settle_grasp_decay(fight, owner, vitals, instance):
    since = vitals.grasp_decay
    if since is None:
        return
    if vitals.dead or not vitals.temporary_maximum:
        vitals.grasp_decay = None
        return
    now = (fight.tick + 1) * owner.body_script.frame_ms - 1
    if now <= since:
        return
    vitals.grasp_decay = now
    modify = instance.graph.node(70).field('m_modifyHealth')
    rate = expr.to_float(instance.evaluate(modify.get('m_amount')))
    if not math.isfinite(rate) or rate <= 0:
        return
    remaining = max(0, vitals.temporary_shields - rate * (now - since) / 1000)
    if remaining:
        from ow174.game.world import SHIELDS_POOL

        vitals.temporary_shields = remaining
        vitals.changed.add(SHIELDS_POOL)
    else:
        vitals.grasp_decay = None
        vitals.set_temporary_shields(0)
    fight._sync_body_health(owner, vitals)


def decay_grasp(state):
    # The authored state still runs on the body VM; the world settles its pool.
    return True if state.instance.graph.index == 0x1D02 and state.node.pos == 70 else NotImplemented
