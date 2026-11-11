"""Reconstructed projectile admission for Sigma's authored Grasp boxes.

View-local box anchoring and eligibility are explicit approximations. The
authored absorbed-damage event is delivered; temporary shield-pool hosting and
native visual/transport behavior require separate acceptance.
"""

import math

from ow174.game.script import expr

EPSILON = 1e-9


def play_shield_script(world, instance, node):
    """Scope a live Sigma self-target call to the actual loaded native queue.

    Runtime graph readiness is explicit and synchronous, so this queue cannot
    retain pending resource work after the call. Other body PlayScript routes
    remain outside this reconstructed host's declared scope.
    """
    from ow174.game.script import graph, play, play_queue

    if (world.hero != 0x02E000000000023B or world.match is None
            or instance.graph.index != 0x1D02 or node.pos != 34):
        return
    owner = next((p for p in world.match.players if p.has_body and p.body == world.body), None)
    if (owner is None or owner.gone or owner.body_script is None or owner.body_script.retired
            or owner.body_script.component is not instance.component
            or world.match.combat.is_dead(owner)):
        return
    resource = play.resolve_resource(instance, node)
    definition = graph.graph(0x1D03)
    if resource is None or resource.guid != 0x0580000000001D03 or definition is None:
        return
    request = play.resolve_request(
        instance, node, resource,
        context_provider=lambda: play.InvocationContext(world.body, world.body, instance.source_entity()),
    )
    if request is None or request.targets != (world.body,):
        return
    queue = play_queue.Queue(instance.component, lambda guid: play_queue.Load("ready", definition))
    queue.submit(request)
    if queue.pending:
        raise ValueError("ready self-target Sigma script left pending work")


def shield_amount(world, instance, cfg):
    """The authored named-pool query used to cap subsequent self grants."""
    if (world.hero != 0x02E000000000023B or world.match is None
            or instance.graph.index != 0x1D03
            or instance.evaluate(cfg.get("m_entity")) != expr.Entity(world.body)
            or (cfg.get("m_7E8B666C") or {}).get("m_7E8B666C") != 2
            or expr.guid_of((cfg.get("m_7BE02F89") or {}).get("m_identifier"))
            != 0x0D80000000005847):
        return NotImplemented
    vitals = world.match.combat.vitals.get(world.body)
    return expr.f32(sum(pool.now for pool in vitals.extra.values()
                        if pool.identifier == 0x5847 and pool.kind == 2)) if vitals else 0.0


def update_pools(fight, tick):
    """Host actual1D03 pool lifetimes; decay cadence/priority are reconstructed.

    The grant amount is evaluated by the actual played graph, including its
    existing-pool cap query. Temporary shields precede base shields, cannot be
    healed and retire with their source state/body. Named1D02 ModifyHealth28
    supplies the decay amount after its authored delay; interpreted per second.
    Native pool getter slots, scale and presentation are not accepted here.
    """
    from ow174.game.script import runtime

    for target in fight._targets():
        owner = target.owner
        script = getattr(owner, "body_script", None)
        component = getattr(script, "component", None)
        if (not isinstance(component, runtime.Component) or getattr(script, "retired", False)
                or getattr(component.world, "hero", None) != 0x02E000000000023B):
            continue
        vitals = fight.vitals_of(target)
        active = {}
        empty = []
        if not vitals.dead:
            for instance in list(script.component.instances.values()):
                if instance.graph.index != 0x1D03:
                    continue
                has_pool = False
                for state in list(instance.states.values()):
                    if state.active and state.node.cls == "STUStatescriptStateHealthPool":
                        active[(instance.id, state.index, state.counter)] = state
                        has_pool = True
                if not has_pool and instance.initialized and not instance.queue:
                    empty.append(instance)
        changed = False
        for key in list(vitals.extra):
            if key not in active and not vitals.extra[key].removed:
                vitals.remove_temporary(key)
                changed = True
        for key, state in active.items():
            pool = vitals.extra.get(key)
            if pool is None:
                amount = expr.to_float(state.evaluate(state.node.field("m_amount"), watch=False))
                pool = vitals.add_temporary(key, 0x5847, 2, amount, tick)
                changed = changed or pool is not None
            if pool is None:
                # Zero grants and exhausted host capacity must not leave an
                # inert lease graph alive or turn into a delayed future grant.
                state.request(runtime.FINISH)
                state.instance.component.run_now()
                state.instance.component.destroy(state.instance)
                continue
        live = [pool for key, pool in vitals.extra.items() if key in active and not pool.removed]
        if live:
            last = max(pool.last_tick for pool in live)
            for pool in live:
                pool.last_tick = max(pool.last_tick, tick)
            for instance in script.component.instances.values():
                decay = instance.states.get(28) if instance.graph.index == 0x1D02 else None
                if decay is not None and decay.active:
                    amount = expr.to_float(decay.evaluate(
                        (decay.node.field("m_modifyHealth") or {}).get("m_amount"), watch=False))
                    elapsed = max(0.0, tick - max(last, decay.start / script.frame_ms)) * 0.016
                    remaining = max(0.0, amount) * elapsed
                    for pool in live:
                        lost = min(pool.now, remaining)
                        if lost:
                            pool.now -= lost
                            remaining -= lost
                            vitals.changed.add(2)
                            changed = True
        for key, state in active.items():
            pool = vitals.extra.get(key)
            if pool is None:
                continue
            if pool.now <= EPSILON:
                vitals.remove_temporary(key)
                state.request(runtime.FINISH)
                state.instance.component.run_now()
                # The permissive VM does not host1D03's stop-script action yet.
                # Retire this completed lease graph explicitly to bound lifetime.
                state.instance.component.destroy(state.instance)
                changed = True
        if changed:
            fight._sync_body_health(owner, vitals)
        for instance in empty:
            script.component.destroy(instance)  # completed/declined self-grant stop-action reconstruction


def ray_box(origin, direction, low, high, length, *, padding=0.0):
    first, last = 0.0, length
    for o, d, lo, hi in zip(origin, direction, low, high, strict=True):
        lo, hi = min(lo, hi) - padding, max(lo, hi) + padding
        if lo > hi:
            return None
        if abs(d) < EPSILON:
            if not lo <= o <= hi:
                return None
            continue
        a, b = (lo - o) / d, (hi - o) / d
        first, last = max(first, min(a, b)), min(last, max(a, b))
        if first > last:
            return None
    return first


def absorption(fight, shooter, hit, origin, direction, length, *, padding=0.0):
    """Nearest eligible enemy Grasp volume before a body or wall is considered."""
    if hit is None or not hit.deflectable:
        return None
    best = None
    for target in fight._enemies(shooter):
        owner = target.owner
        mover = getattr(owner, "mover", None)
        script = getattr(owner, "body_script", None)
        if mover is None or script is None:
            continue
        yaw, pitch = mover.yaw * math.tau / 65536, mover.pitch * math.tau / 65536
        sy, cy, sp, cp = math.sin(yaw), math.cos(yaw), math.sin(pitch), math.cos(pitch)
        basis = ((cy, 0, -sy), (sy * sp, cp, cy * sp), (sy * cp, -sp, cy * cp))
        eye = fight._eye(owner)
        delta = tuple(a - b for a, b in zip(origin, eye, strict=True))
        local_origin = tuple(sum(a * b for a, b in zip(delta, axis, strict=True)) for axis in basis)
        local_direction = tuple(sum(a * b for a, b in zip(direction, axis, strict=True)) for axis in basis)
        for instance in script.component.instances.values():
            if instance.graph.index != 0x1D02:
                continue
            for state in instance.states.values():
                if (not state.active or state.node.cls != "STUStatescriptStateDeflectProjectiles"
                        or state.node.field("m_8B251D53") != 1):
                    continue
                for box in state.node.field("m_boxes") or ():
                    low = state.evaluate(box.get("m_CBA118B2"), watch=False)
                    high = state.evaluate(box.get("m_AEF67365"), watch=False)
                    if not isinstance(low, expr.Vec3) or not isinstance(high, expr.Vec3):
                        continue
                    distance = ray_box(local_origin, local_direction, low, high, length, padding=padding)
                    if distance is not None and (best is None or distance < best[0]):
                        best = distance, state
    return best


def absorbed(state, amount):
    """Write the per-projectile amount and run the authored accumulation plug."""
    output = expr.lvalue(state.node.field("m_F6CDC81C"))
    if output is not None:
        state.instance.write(output, expr.f32(amount))
    state.instance.follow(state.node, "m_160B5DBE", state)
    state.instance.component.run_now()
