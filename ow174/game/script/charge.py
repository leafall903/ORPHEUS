"""Selected real-health and damage events entering the original Mercy charge graph.

The authoritative health consumer supplies the actual capped amount and tags
evaluated from its source ModifyHealth state. This reconstructs engine message
production; native event/clock/ownership parity and other charge events stay open.
"""

import math

from ow174.game.script import entity_relation, expr, regeneration, runtime, support

HEALED = 0x0240000000000044
DAMAGED = 0x0240000000000012
BOOSTED = 0x0240000000000D9A
BOOST_TAG = expr.Asset(0x0D80000000003EBB)
PASSIVE_GRAPH = 0x0B8B
VALKYRIE_RECOVERY = (5, 17, 18)  # 10DA/14, /31 and /32


def advance_valkyrie_expiry(script, tick: int) -> tuple[int, int, int] | None:
    """Advance the original 10DA expiry and recovery without body input."""
    component = script.component
    owner_frame, owner_time = component.frame, component.now
    end = tick * script.frame_ms - 1
    if end <= owner_time or component.owner_dead:
        return None
    ultimate = next((item for item in component.instances.values() if item.graph.index == 0x10DA), None)
    chase = ultimate.states.get(2) if ultimate is not None and ultimate.initialized else None
    if chase is None:
        return None
    if chase.active:
        rate = expr.to_float(chase.evaluate(chase.node.field("m_9B0385B4"), watch=False))
        if rate <= 0 or end - chase.last < math.floor(chase.cur * 1000 / rate):
            return None
    elif not any(ultimate.states[i].active for i in VALKYRIE_RECOVERY if i in ultimate.states):
        return None

    # Keep scheduled recovery on the 10DA clock even when Owner time is behind.
    recovery = ultimate.states.get(5)
    ability = ultimate.states.get(9)
    clock = max(owner_time, chase.last, chase.ended_at,
                recovery.last if recovery is not None else 0, (ability.ticked_at or 0) if ability else 0)
    first = (clock + 1) // script.frame_ms
    expired = None
    script.world.advancing_valkyrie_clock = True
    try:
        for frame in range(first, tick):
            component.frame = frame
            start = frame * script.frame_ms
            frame_end = start + script.frame_ms - 1
            component.now = max(start, clock)
            for _ in range(runtime.MAX_COMPONENT_UPDATES):
                due = ultimate.queue[0].time if ultimate.queue else frame_end
                component.now = min(frame_end, max(component.now, due))
                was_running = chase.active
                ultimate.update()
                if was_running and not chase.active:
                    expired = frame, ultimate.id, chase.ended_at
                if not chase.active and not any(
                    ultimate.states[i].active for i in VALKYRIE_RECOVERY if i in ultimate.states
                ):
                    return expired
                if component.now == frame_end and (not ultimate.queue or ultimate.queue[0].time > frame_end):
                    break
            else:
                component.budget_exceeded("Valkyrie expiry update budget exhausted")
    finally:
        component.frame, component.now = owner_frame, owner_time
        del script.world.advancing_valkyrie_clock
    return expired


def set_pending_credits(world, credits):
    if credits:
        world.pending_ult_credits = credits
    else:
        vars(world).pop("pending_ult_credits", None)


def advance_passive(script, tick: int) -> None:
    """Advance only 0B8B through the completed world frames."""
    from ow174.game.script.driver import ULT_CHARGE

    component = script.component
    owner_time = component.now
    end = tick * script.frame_ms - 1
    if end <= owner_time:
        return
    instance = next(
        (item for item in component.instances.values() if item.graph.index == PASSIVE_GRAPH), None,
    )
    if instance is None or not instance.initialized:
        return
    slot = component.entity_var(ULT_CHARGE)
    pending = list(getattr(script.world, "pending_ult_credits", ()))
    try:
        # Ordinary command frames still own buttons, pose and the other graphs.
        # A huge idle jump leaves any unprocessed events queued for later updates.
        for _ in range(1024):
            if instance.stopped or not instance.queue or instance.queue[0].time > end:
                break
            component.now = max(owner_time, instance.queue[0].time)
            before = slot.value()
            instance.update()
            if slot.value() > before and component.now > owner_time:
                pending.append((component.now, slot.value() - before))
    finally:
        component.now = owner_time
        set_pending_credits(script.world, tuple(sorted(pending, key=lambda entry: entry[0])))


def deliver_award(script, message, params, sender, frame):
    """Resolve the authored award at its frame before reapplying newer credit."""
    from ow174.game.script.driver import ULT_CHARGE, ULT_COST

    component, world = script.component, script.world
    if not runtime.send_message(component, message, params, sender):
        return
    time = frame * script.frame_ms
    pending = getattr(world, "pending_ult_credits", ())
    later = tuple((due, amount) for due, amount in pending if due > time)
    earlier = tuple((due, amount) for due, amount in pending if component.now < due <= time)
    slot = component.entity_var(ULT_CHARGE)
    visible = slot.value()
    before = visible - sum(amount for _, amount in later)
    if later:
        component.set(ULT_CHARGE, before)
    # Hold the gate during delivery too, before the authored credit is known.
    preview = earlier + (((time, 0.0),) if time > component.now else ())
    set_pending_credits(world, preview)
    component.run_now()
    balance = slot.value()
    if time > component.now and balance > before:
        earlier += ((time, balance - before),)
    # A new historical gain can take some or all of the room that was
    # previously assigned to a newer event. Only retain its effective credit.
    cost = component.entity_var(ULT_COST).value()
    rebased = []
    for due, amount in later:
        total = expr.f32(min(cost, balance + amount))
        if total > balance:
            rebased.append((due, total - balance))
            balance = total
    set_pending_credits(world, earlier + tuple(rebased))
    if balance != slot.value():
        component.set(ULT_CHARGE, balance)


def healed(script, target: int, amount: float, tags, *, frame: int) -> None:
    if (script is None or script.hero != regeneration.MERCY or script.retired
            or script.world.match is None or tags is None or amount <= 0):
        return
    expiry = script.world.historical_valkyrie_deadline()
    if expiry is not None and (frame + 1) * script.frame_ms - 1 < expiry:
        # 0B8B/35 rejects healing credit while the source command is in Valkyrie.
        return
    params = {0x3F: expr.Entity(target), 0x40: expr.f32(amount), 0x41: tuple(tags)}
    deliver_award(script, HEALED, params, script.entity, frame)


def damage_context(script, state, modify):
    """Freeze the owning body and authored tags before an attack can outlive it."""
    if (script is None or script.hero != regeneration.MERCY or script.retired
            or script.world.match is None or script.component is not state.instance.component):
        return None
    return script.entity, support.modify_health_tags(state, modify)


def damaged(script, target: int, amount: float, context, *, frame: int, extra: float = 0.0) -> None:
    if (context is None or script is None or script.hero != regeneration.MERCY or script.retired
            or script.world.match is None or context[0] != script.entity or amount <= 0):
        return
    # Let 0B8B subtract the source's extra applied amount with its authored
    # 0624 fold. The server reconstructs the producer for this selected row.
    metadata = (expr.Map(((16056, BOOST_TAG), (499, expr.f32(extra)))),) if extra > 0 else ()
    params = {0x13: expr.f32(amount), 0x15: expr.Entity(target), 0x3D: context[1], 0x624: metadata}
    deliver_award(script, DAMAGED, params, script.entity, frame)


def boosted(
    script, target: int, applied: float, extra: float, coefficient: float, *, valkyrie_frame=None,
) -> None:
    """Deliver to the exact launch caster; 006B decides whether to send 00EB."""
    if (script.hero != regeneration.MERCY or script.retired or script.world.match is None
            or extra <= 0 or coefficient <= 0):
        return
    params = {0x74D: expr.f32(extra), 0x74C: expr.f32(coefficient), 0x13: expr.f32(applied),
              0x5C: expr.Entity(target)}
    script.world.match.queue_body_message(
        script.entity, BOOSTED, params, script.entity, valkyrie_frame=valkyrie_frame,
    )


def deliver_boost(script, params, sender, tick, valkyrie_frame):
    if valkyrie_frame is None:
        deliver_award(script, BOOSTED, params, sender, tick)
        return
    # The staff still runs its damage statistic before the passive's charge gate.
    script.world.historical_boost_award = True
    try:
        deliver_award(script, BOOSTED, params, sender, valkyrie_frame)
    finally:
        del script.world.historical_boost_award


def track_boost_stat(instance, node) -> None:
    if instance.graph.index != 0x006B or node.pos != 172:
        return
    component = instance.component
    world = component.world
    match = getattr(world, "match", None)
    if match is None or world.hero != regeneration.MERCY:
        return
    if instance.evaluate(node.field("m_targets")) != expr.Entity(component.entity):
        return
    owner = next((p for p in match.players if p.has_body and p.body == component.entity), None)
    if owner is None or owner.body_script is None or owner.body_script.component is not component:
        return
    amount = expr.to_float(instance.evaluate(node.field("m_amount")))
    for stat in node.field("m_stats") or ():
        owner.stats.add(expr.guid_of(stat), amount)


def damage_native(world, cfg, instance):
    """Declared ordinary-actor host for the selected 0B8B and 006B branches.

    This fallback is scoped to the authored graph. It does not install a
    generic native relationship backend, filter table or dynamic tag catalog.
    """
    if (world.hero != regeneration.MERCY or world.match is None or instance is None
            or instance.graph.index not in {0x0B8B, 0x006B} or instance.component.entity != world.body):
        return NotImplemented
    staff = instance.graph.index == 0x006B
    cls = cfg.get("$")
    if cls == entity_relation.CLASS and not staff:
        target = expr.to_entity(instance.evaluate(cfg.get("m_target")))
        source = expr.to_entity(instance.evaluate(cfg.get("m_source")))
        if source != world.body or expr.truthy(instance.evaluate(cfg.get("m_AFBBE9C3"))):
            return NotImplemented
        actors = {actor.entity: actor for actor in world.match.combat._targets()}
        return (source != target and source in actors and target in actors
                and (world.match.game_map.free_for_all or actors[source].team != actors[target].team))
    if cls == "STU_AC829876":
        if expr.dynamic(cfg.get("m_entity")) != (expr.INSTANCE, 536 if staff else 393):
            return NotImplemented
        spec = cfg.get("m_filter") or {}
        target = expr.to_entity(instance.evaluate(cfg.get("m_entity")))
        mask = spec.get("m_84065FD1")
        if mask == 1 and not any(value for key, value in spec.items() if key not in {"$", "m_84065FD1"}):
            return any(actor.entity == target for actor in world.match.combat._targets())
        if (staff and mask == 64 and spec.get("m_66747E7D") == 2
                and expr.guid_of((spec.get("m_8B2A4DF3") or {}).get("m_7D7C86A1")) == 0x0D6000000000003A):
            # For the selected ordinary victim profile, no dynamic3A tag is
            # supplied. Evaluate the authored victim marker condition.
            actors = {actor.entity: actor for actor in world.match.combat._targets()}
            return (target in actors and world.body in actors and target != world.body
                    and actors[target].team != actors[world.body].team
                    and expr.truthy(instance.evaluate(spec.get("m_condition"))))
        return NotImplemented
    if not staff and cls == "STU_25ECB55F" and expr.guid_of(cfg.get("m_88B0050A")) in {
            0x0D600000000002ED, 0x0D600000000003C3, 0x0D6000000000011E}:
        # The selected ordinary actor host does not supply these special
        # dynamic categories. Direct NPC tags do not justify a blanket 0.75.
        return False
    return NotImplemented
