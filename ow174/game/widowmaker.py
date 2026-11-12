"""Delivered Widowmaker rifle/Infra-Sight consumers on the combined body host.

The frozen delivery carries these functions in Combat/BodyScript. Keep their
resource-driven policy without installing a second combat or script manager.
"""
import math
from ow174.game.script import expr, graph, nodes

HERO = 0x02E000000000000A


def infra_window(script) -> tuple[int, int, int] | None:
    """Committed cast/effect milliseconds derived from0067's retained Wait record.

    OnStart starts Wait9 outside Ability's empty running scope. Project that
    authored commitment onto world time without advancing the owner VM.
    """
    if script.hero != 0x02E000000000000A:
        return None
    for instance in script.component.instances.values():
        if instance.graph.index != 0x0067:
            continue
        cast = instance.states.get(9)
        duration = instance.find_var(graph.INSTANCE, 567, create=False)
        if (not isinstance(cast, nodes.Wait) or cast.node.pos != 12
                or (cast.counter == 0 and cast.start == 0) or duration is None):
            return None
        begins = cast.start + cast.ms
        if not cast.active and cast.ended_at is not None and cast.ended_at < begins:
            return None  # an explicitly aborted cast has no finished-effect branch
        return cast.start, begins, begins + nodes.seconds_to_ms(duration.value())
    return None


def ultimate_active(script, *, at_time: int | None = None) -> bool:
    if script.hero == 0x02E000000000000A:
        now = script.component.now if at_time is None else at_time
        if at_time is None and script.world.match is not None:
            now = max(now, script.world.match.tick * script.frame_ms)
        window = infra_window(script)
        return bool(window is not None and window[0] <= now + script.frame_ms - 1 and now < window[2])
    return any(state.flags & nodes.Ability.RUNNING for state in script.ultimates)


def award_damage_charge(script, damage):
    """Preserve the delivered committed-window and stale-Q-buffer charge gate."""
    if (script.retired or script.component.fault is not None or script.component.owner_dead or type(damage) not in (int, float)
            or not math.isfinite(damage) or damage <= 0):
        return 0.0
    awarded_at = (round(script.world.match.combat.now * 1000) if script.world.match is not None
                  else script.component.now)
    if ultimate_active(script, at_time=awarded_at):
        return 0.0
    if script.world.match is not None:
        now = max(script.component.now, script.world.match.tick * script.frame_ms)
        for ability in script.ultimates:
            ability.expire_presses(now)
    cost = expr.to_float(script.component.entity_var(338).value())
    charge = expr.to_float(script.component.entity_var(342).value())
    if cost <= 0:
        return 0.0
    after = min(cost, charge + damage)
    script.set_ult_charge(after)
    return expr.to_float(script.component.entity_var(342).value()) - charge


def tracked_targets(world, state):
    if (world.match is None or world.hero != HERO
            or state.instance.graph.index != 0x0067 or state.index != 0):
        return NotImplemented
    fight = world.match.combat
    source = next((p for p in fight.match.players if p.body == state.instance.component.entity), None)
    if (source is None or not source.spawned or fight.is_dead(source)
            or source.body_script is None or source.body_script.component is not state.instance.component):
        return ()
    return infra_targets(fight, source, state.instance)


def infra_targets(fight, source, instance):
    if (source.body_script is None or not source.spawned or fight.is_dead(source)
            or instance.component is not source.body_script.component):
        return ()
    window = infra_window(source.body_script)
    now = round(fight.now * 1000)
    if (window is None or window[1] > now + instance.component.frame_ms - 1 or now >= window[2]):
        return ()
    radius = expr.to_float(instance.evaluate(instance.graph.node(1).field("m_radius")))
    return tuple(target.entity for target in fight._enemies(source, damageable=False)
                 if math.dist(source.mover.position, target.feet) <= radius)


def revealed_to(fight, viewer, entity):
    target = next((t for t in fight._targets() if t.entity == entity), None)
    if target is None or fight.vitals_of(target).dead:
        return False
    venom = getattr(fight, "venom", None)
    if venom is not None and venom.poisoned_by(viewer, entity):
        return True
    for source in fight.match.players:
        ally = not fight.match.game_map.free_for_all and source.team == viewer.team
        if source is not viewer and not ally:
            continue
        if source.body_script is None or fight.is_dead(source):
            continue
        for instance in source.body_script.component.instances.values():
            if instance.graph.index == 0x0067 and entity in infra_targets(fight, source, instance):
                return True
    return False


def update(fight):
    """Refresh the delivered world-clock reveal outside observation/export."""
    for source in fight.match.players:
        script = source.body_script
        if script is None or script.hero != HERO or script.retired:
            continue
        for instance in tuple(script.component.instances.values()):
            if instance.graph.index != 0x0067:
                continue
            state = instance.states.get(0)
            if isinstance(state, nodes.TrackTargets) and state.active and not state.ending:
                state.refresh()
