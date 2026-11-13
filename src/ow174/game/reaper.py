"""Reaper's reconstructed combat consumers for build 104319.

The graph supplies pellet damage, count and four spread rings. Hellfire uses
original104319 ring rotations and jitter with declared server seed identities.
Native handle parity, muzzle transforms and rendered trajectories remain open.
"""

import math

from ow174.game import damage_charge
from ow174.game.script import expr, runtime

HERO = 0x02E0000000000002
SHOTGUN_GRAPH = 0x0019
REAPING_GRAPH = 0x0075
REAPING_RATIO = 0.35000002  # 0075/st5's ModifyHealth expression
WRAITH_GRAPH = 0x0020
BLOSSOM_GRAPH = 0x0044
BLOSSOM_DAMAGE = 34.0  # effect4 (.053) -> graph0045/st0 default; human argument342=0
BLOSSOM_INTERVAL = 0.2  # graph0045's five volleys per second
BLOSSOM_LOADOUT = 0x0B90000000000010
SHOTGUN_CRITICAL = 2.0  # reconstructed OW1 headshot default when0019 leaves multiplier unspecified
SHADOW_GRAPH = 0x00F2


def _charge_instance(script, *, finished_blossom=False):
    """Normal combat eligibility reconstruction; native mode/filter gates stay open.

    01C7's2426 depends on unimplemented mode services. A missing value uses the
    active sandbox body's eligibility; an explicit false still rejects. This
    leaves the native mode admission service reconstructed.
    """
    if (script is None or script.hero != HERO or script.retired or script.component.owner_dead
            or (not finished_blossom and blossom_state(script) is not None)):
        return None
    component = script.component

    def value(key):
        var = component.entity_var(key, create=False)
        return var.value() if var is not None else None

    def disabled(key):
        setting = value(key)
        return setting is not None and not expr.truthy(setting)

    if ((value(842) and not finished_blossom) or disabled(2426) or disabled(9530)
            or (disabled(10482) and disabled(10480))):
        return None
    return next((i for i in component.instances.values() if i.graph.index == damage_charge.GRAPH), None)


def charge_damage(actor, amount: float, target, damage_tags=()) -> None:
    """Apply authored 0B8B target branches to actual enemy body damage.

    Combat supplies enemy eligibility and actual loss after armour and overkill.
    Reaping is separate from damage charge.
    """
    if not is_reaper(actor) or not math.isfinite(amount) or amount <= 0:
        return
    script = getattr(actor, "body_script", None)
    instance = _charge_instance(script)
    if instance is None:
        return
    blocked = script.component.entity_var(12321, create=False)
    if blocked is not None and blocked.value():
        return
    damage_charge.apply(script, instance, amount, target, damage_tags)


def charge_passive(script, elapsed_ms=None, *, finished_blossom=False) -> None:
    """Run0B8B/node4 on its authored one-second cadence, replacing60s fill.

    The normal sandbox eligibility bridge is reconstructed; amount5, global
    multiplier9532, passive multiplier12918 and cap338 come from the graph.
    Positive legacy ult_seconds enables this path; zero disables passive gain.
    The explicit remainder is passive server state and is reset on interruption.
    """
    instance = _charge_instance(script, finished_blossom=finished_blossom)
    component = script.component
    blocked = component.entity_var(11942, create=False)
    if (instance is None or script.ult_seconds <= 0 or (blocked and blocked.value())
            or expr.to_float(component.entity_var(342).value())
            >= expr.to_float(component.entity_var(338).value())):
        script.reaper_charge_ms = 0
        return
    authored = instance.states.get(1)
    if authored is not None and authored.active:
        script.reaper_charge_ms = 0  # never double an active authored timer
        return
    interval = round(expr.to_float(instance.evaluate(instance.graph.nodes[3].field("m_timeout"))) * 1000)
    if interval <= 0:
        raise ValueError("invalid Reaper passive-charge interval")
    elapsed_ms = component.frame_ms if elapsed_ms is None else elapsed_ms
    if type(elapsed_ms) is not int or elapsed_ms < 0:
        raise ValueError("invalid Reaper passive-charge elapsed time")
    periods, script.reaper_charge_ms = divmod(script.reaper_charge_ms + elapsed_ms, interval)
    for _ in range(periods):
        before = component.entity_var(342).value()
        value = instance.evaluate(instance.graph.nodes[4].field("m_value"))
        script.credit_ult_charge(expr.to_float(value))
        after = component.entity_var(342).value()
        if expr.to_float(after) >= expr.to_float(component.entity_var(338).value()):
            script.reaper_charge_ms = 0  # time spent capped cannot fund a later charge interval
            break
        if after == before:
            break  # zero rate cannot change on further identical periods


def charge_world(actor, fight) -> None:
    """Advance bound Reaper bodies by world ticks, independent of input frames.

    This is an explicit server-clock bridge. An expired matching world channel
    can outlive its owner's stalled VM; only that channel's ultimate gate is
    bypassed, and time spent channeling never becomes passive-charge credit.
    """
    if not is_reaper(actor):
        return
    script = getattr(actor, "body_script", None)
    if script is None or script.world.match is not fight.match:
        return
    previous = script.reaper_charge_tick
    if previous is not None and fight.tick <= previous:
        return
    script.reaper_charge_tick = fight.tick
    if previous is None or not actor.spawned or not actor.has_body:
        script.reaper_charge_ms = 0
        return
    elapsed = (fight.tick - previous) * 16  # the match uses 16ms server ticks
    channel = fight.reaper_blossoms.get(actor.body)
    finished = False
    if channel is not None:
        start = fight.now - elapsed / 1000
        if channel["start"] < fight.now and channel["end"] > start:
            script.reaper_charge_ms = 0
            elapsed = max(0, min(elapsed, round((fight.now - channel["end"]) * 1000)))
        state = blossom_state(script)
        finished = (channel["ended"] and fight.now >= channel["end"]
                    and state is not None
                    and channel["activation"] == (state.instance.id, state.counter, state.start))
    charge_passive(script, elapsed, finished_blossom=finished)


def is_reaper(actor) -> bool:
    return getattr(getattr(actor, "hero", None), "guid", None) == HERO


def blossom_state(script):
    """The authored channel wait, independent of its 16Hz visual-only volley."""
    if (script is None or script.hero != HERO or script.retired
            or script.component.owner_dead):
        return None
    for instance in script.component.instances.values():
        if instance.graph.index == BLOSSOM_GRAPH:
            state = instance.states.get(3)
            if state is not None and state.active:
                return state
    return None


def interrupt_blossom(actor) -> bool:
    """Cancel the authored channel scope, for an authoritative control consumer."""
    if not is_reaper(actor):
        return False
    script = getattr(actor, "body_script", None)
    state = blossom_state(script)
    if state is None:
        return False
    state.request_finish()
    script.component.run_now()
    return True


def interrupt_shadow(actor) -> bool:
    """Abort the authored targeting/casting roots through their own end paths."""
    if not is_reaper(actor):
        return False
    script = getattr(actor, "body_script", None)
    if script is None or script.retired or script.component.owner_dead:
        return False
    interrupted = False
    for instance in list(script.component.instances.values()):
        if instance.graph.index != SHADOW_GRAPH:
            continue
        for index in (6, 19):
            state = instance.states.get(index)
            if state is not None and state.active:
                state.request(runtime.ABORT)
                interrupted = True
    if interrupted:
        script.component.run_now()
    return interrupted


def advance_entry(actor) -> None:
    """Reconstruct Reaper's zero-duration entry gate; leave all other nodes alone.

    0020/st1 is 54FEB21C with duration zero and no output variables. The native
    callback also publishes engine random/context outputs not needed by this
    authored node. Finishing at this command's end is an explicit reconstruction,
    not a general implementation of 54FEB21C.
    """
    if not is_reaper(actor):
        return
    script = getattr(actor, "body_script", None)
    if script is None or script.retired or script.component.owner_dead:
        return
    for instance in list(script.component.instances.values()):
        if instance.graph.index == SHADOW_GRAPH:
            state = instance.states.get(32)
            if state is not None and state.active and not any(
                event.state is state and event.kind == runtime.STATE_OP and event.op == runtime.FINISH
                for event in instance.queue
            ):
                # Reconstructed duration callback, scoped to this authored cast.
                duration = state.evaluate(state.node.field("m_ED97A97C"), watch=False)
                state.request_finish(max(0, round(float(duration) * 1000) - (state.now - state.start)))
        if instance.graph.index != WRAITH_GRAPH:
            continue
        state = instance.states.get(1)
        if state is not None and state.active and state.node.cls == "STU_54FEB21C":
            delay = state.evaluate(state.node.field("m_ED97A97C"), watch=False)
            if delay != 0:
                raise ValueError("unsupported Reaper Wraith entry duration")
            state.request_finish()
            instance.update()


def intangible(actor) -> bool:
    """Reconstructed Wraith/Shadow immunity is bounded by their authored scopes."""
    if not is_reaper(actor):
        return False
    script = getattr(actor, "body_script", None)
    if script is None or script.retired or script.component.owner_dead:
        return False
    return any(
        (index := {WRAITH_GRAPH: 3, SHADOW_GRAPH: 34}.get(instance.graph.index)) is not None
        and (state := instance.states.get(index)) is not None and state.active
        for instance in script.component.instances.values()
    )


def shotgun_directions(actor, state, direction, *, number=0, counter=None):
    """Original D80310 on the authored0019/10 ring profile, aspect one."""
    if (not is_reaper(actor)
            or (state.instance.graph.index, state.node.pos, state.index) != (SHOTGUN_GRAPH, 10, 6)):
        return (direction,)
    from ow174.game import fusion_spread, weapon_aim

    cfg = state.node.field("m_5EC1EB38") or {}
    if cfg.get("$") != "STU_613BDDB0":
        raise ValueError("unexpected Reaper shotgun launch profile")
    spread = state.evaluate(cfg.get("m_spread"), watch=False)
    jitter_field, phase_field = cfg.get("m_92308789"), cfg.get("m_391F15AF")
    jitter = state.evaluate(jitter_field, watch=False) if jitter_field is not None else 0
    degrees = state.evaluate(phase_field, watch=False) if phase_field is not None else None
    count = state.evaluate(state.node.field("m_numProjectilesPerShot"), watch=False)
    rings = []
    for ring in cfg.get("m_0D30E40C") or ():
        radius = state.evaluate(ring.get("m_6425FFB9"), watch=False)
        size = state.evaluate(ring.get("m_B19BD35D"), watch=False)
        if (type(radius) not in (int, float) or not math.isfinite(radius) or radius < 0
                or type(size) is not int or size < 1):
            raise ValueError("invalid Reaper shotgun ring")
        rings.append((radius, size))
    if (type(spread) not in (int, float) or not math.isfinite(spread) or spread < 0
            or type(jitter) not in (int, float) or not math.isfinite(jitter) or jitter < 0
            or (phase_field is not None and
                (type(degrees) not in (int, float) or not math.isfinite(degrees)))
            or type(count) is not int or not 1 <= count <= 256
            or sum(size for _, size in rings) != count
            or any(spread * radius >= 90 for radius, _ in rings)):
        raise ValueError("invalid Reaper shotgun count/spread")
    phase = -1 if degrees is None else expr.f32(expr.f32(degrees) * expr.f32(math.pi / 180))
    if degrees is not None and phase < 0:
        phase = math.fmod(phase, math.tau) + math.tau
    # Same declared host mappings as the reviewed Fusion adapter. Native entity
    # and script handle parity is separate from the executed ring arithmetic.
    counter = state.counter if counter is None else counter
    seed = fusion_spread.seed_base(state.instance.component.entity, state.instance.id, state.index, counter)
    rays = fusion_spread.directions(weapon_aim.quantized_direction(direction), spread, jitter,
                                    phase, rings, seed, number)
    return tuple(weapon_aim.normalize_direction(ray) for ray in rays)


def shotgun_critical(actor, state, direct) -> None:
    """Scope the reconstructed omitted headshot default to Hellfire Shotguns."""
    if (is_reaper(actor) and state.instance.graph.index == SHOTGUN_GRAPH
            and (cfg := state.node.field("m_6396149F")) is not None
            and cfg.get("m_A872C70E") is None):
        direct.critical = SHOTGUN_CRITICAL


def reaping_ratio(actor) -> float:
    """Only the current living Reaper body with its passive can steal life."""
    if not is_reaper(actor):
        return 0.0
    script = getattr(actor, "body_script", None)
    if script is None or script.retired or script.component.owner_dead:
        return 0.0
    return REAPING_RATIO if any(
        instance.graph.index == REAPING_GRAPH for instance in script.component.instances.values()
    ) else 0.0


def owns_shadow_placement(state):
    """Only the original00F2/3/state0 typed-confirm placement context."""
    node, instance = state.node, state.instance
    typed = node.field('m_6647ED22')
    return (not instance.component.strict
            and instance.component.world.hero == HERO
            and (instance.graph.index, node.pos, state.index, node.remote) == (SHADOW_GRAPH, 3, 0, 0)
            and node.cls == 'STU_4C96470A'
            and isinstance(typed, dict) and typed.get('$') == 'STU_A1AAB8C4')


def shadow_placement_confirm(state):
    # Preserve the existing Shadow Step input-consumption route. The common
    # producer already records the dispatched typed byte; do not rebegin here.
    return owns_shadow_placement(state)
