"""Reconstructed defensive windows tied to owned, authored ability states."""

import math

from ow174.game.script import expr

# Hero, graph, missing presentation entry, ability, protective subgraph, mode.
PROFILES = (
    (0x02E0000000000002, 0x0020, 2, 18, 6, "phase"),
    (0x02E00000000000DD, 0x05FE, 64, 3, 13, "cryo"),
    (0x02E00000000001A2, 0x0F7C, 2, 16, 6, "phase"),
)


# Target-build 090B.003 CharacterMover and STU_BE9A0B3E shape fields.
CRYO_RADIUS = 0.5
CRYO_HEIGHT = 2.0


def cryo_capsule(owner, *, tick=None):
    """Reconstruct the active pet at the owner's feet, independent of body stance."""
    if (not (owner.has_body and owner.spawned and owner.client is not None)
            or protection(owner, tick=tick) != "cryo"):
        return None
    x, y, z = owner.mover.position
    return (x, y + CRYO_RADIUS, z), (x, y + CRYO_HEIGHT - CRYO_RADIUS, z), CRYO_RADIUS


def cryo_blockers(players, *, exclude=None, tick=None):
    return tuple(capsule for owner in players if owner is not exclude
                 and (capsule := cryo_capsule(owner, tick=tick)) is not None)


def begin_compatible_state(world, state) -> None:
    """Advance the three unresolved entries into their authored combat branches."""
    if state.node.cls != "STU_54FEB21C" or state.instance.component.entity != world.body:
        return
    key = (world.hero, state.instance.graph.index, state.node.pos)
    profile = next((row for row in PROFILES if key == row[:3]), None)
    if profile is None:
        return
    if world.match is not None:
        from ow174.game.combat import TICK

        window = state.instance.graph.node(profile[4])

        def number(field):
            return expr.to_float(state.evaluate(window.field(field), watch=False))

        if profile[5] == "cryo":
            # The authored scalar ChaseVar runs from the initial value to zero
            # at its default rate of one unit per second.
            seconds = max(0.0, number("m_EB0FC261") - number("m_destination"))
        else:
            seconds = max(0.0, number("m_timeout"))
        world.phase_started = world.match.tick
        world.phase_deadline = world.phase_started + math.ceil(seconds / TICK)
    state.request_finish()


def end_compatible_state(world, state, finished: bool) -> None:
    key = (world.hero, state.instance.graph.index, state.node.pos)
    if any(key == (hero, graph, window) for hero, graph, _, _, window, _ in PROFILES):
        end_world_window(world, state, finished)


def end_world_window(world, state, finished: bool) -> None:
    if (world.match is None or world.phase_started is None
            or state.instance.component.entity != world.body):
        return
    tick = world.match.tick
    elapsed = (state.now - state.start) // state.instance.component.frame_ms
    if not finished or elapsed <= tick - world.phase_started:
        world.phase_deadline = min(world.phase_deadline, tick)


def clock_edges(players, previous: int, tick: int) -> set[int]:
    """Keep both sides of an authored protection boundary during catch-up."""
    edges = set()
    if tick <= previous + 1:
        return edges
    for owner in players:
        world = getattr(getattr(owner, "body_script", None), "world", None)
        for boundary in (getattr(world, "phase_started", None), getattr(world, "phase_deadline", None)):
            if boundary and previous < boundary <= tick:
                edges.add(boundary)
                if previous < boundary - 1:
                    edges.add(boundary - 1)
    return edges


def expire(players, tick: int) -> None:
    """Retire combat protection at the world deadline even if input stalls."""
    for owner in players:
        script = getattr(owner, "body_script", None)
        world = getattr(script, "world", None)
        deadline = getattr(world, "phase_deadline", None)
        if deadline and tick >= deadline:
            world.phase_deadline = 0
            world.phase_started = None


def window_at(world, tick=None) -> bool:
    deadline = world.phase_deadline
    if deadline == 0:
        return False
    if tick is None and world.match is not None:
        tick = world.match.tick
    if tick is None:
        return True
    started = world.phase_started
    return (started is None or tick >= started) and (deadline is None or tick < deadline)


def protection(owner, *, tick=None) -> str | None:
    hero = getattr(getattr(owner, "hero", None), "guid", None)
    profile = next((row for row in PROFILES if row[0] == hero), None)
    if profile is None:
        return None
    script = getattr(owner, "body_script", None)
    if (script is None or script.retired or script.entity != owner.body or owner.gone
            or script.component.owner_dead or script.component.fault is not None):
        return None
    _, graph, _, ability, window, mode = profile
    world = script.world
    if not window_at(world, tick):
        return None
    if world.phase_started is not None:
        # A speculative finish cannot retire world protection, and an admitted
        # end cuts off the window without changing earlier combat ticks.
        return mode
    from ow174.game.script.nodes import Ability

    for instance in script.component.instances.values():
        if instance.graph.index == graph:
            cast = instance.states.get(instance.graph.node(ability).state)
            active = instance.states.get(instance.graph.node(window).state)
            if (isinstance(cast, Ability) and cast.flags & Ability.RUNNING
                    and active is not None and active.active and not active.ending):
                return mode
    return None
