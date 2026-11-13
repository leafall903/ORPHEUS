"""Original0068 energy and charge policy on the existing single body state host."""
from ow174.game.script import nodes

SECONDARY_RECOVERY_TICKS = 62  # Original paced0241 grenade recovery, build104319.
ULT_PASSIVE_PER_SECOND = 5.0

HERO = 0x02E0000000000068


def view_world_tick(script, command, quanta):
    """Use the immutable body epoch for target history; keep fractional view time."""
    from ow174.game.combat import TICK

    world, owner = script.world_frame_anchor
    elapsed_ms = (command.frame - owner) * script.frame_ms - command.view_delay(quanta) * 1000
    return world + elapsed_ms / round(TICK * 1000)


def secondary_knockback(fight, rocket, target, point):
    """Consume 0241/19's authored splash force after its damage was accepted."""
    if (rocket.source_graph != 0x0241 or rocket.definition != 0x04000000000003DB
            or rocket.blast is None or fight.vitals_of(target).dead):
        return
    from ow174.game.combat import segment_distance

    distance = max(0.0, segment_distance(point, *target.segment()) - target.radius)
    horizontal, vertical = rocket.blast.impulse_scales(distance)
    source_scale = (rocket.blast.source_impulse_scale
                    if target.entity == rocket.source_entity else 1.0)
    fight._radial_impulse(
        target, point, rocket.blast.horizontal_impulse * horizontal * source_scale,
        rocket.blast.vertical_impulse * vertical * source_scale,
    )


def catch_up_projectile(fight, rocket):
    """Advance this accepted projectile through command frames already elapsed in the world."""
    from ow174.game import projectile_clock

    if rocket not in fight.rockets or rocket.last_tick is None or rocket.last_tick >= fight.tick:
        return
    projectile_clock.advance_accepted(fight, rocket)


def ultimate_charge_ready_frame(script) -> int | None:
    """Use the cast's authored end wait even when input stalls the body VM."""
    ready = 0
    for ability in script.ultimates:
        if not (ability.flags & nodes.Ability.RUNNING):
            continue
        instance = ability.instance
        if instance.graph.index != 0x025F or ability.node.pos != 7:
            return None
        wait = instance.states.get(instance.graph.node(4).state)
        if not isinstance(wait, nodes.Wait) or not wait.active:
            return None
        ready = max(ready, (wait.start + wait.ms) // script.frame_ms + 1)
    return ready


def advance_energy(script, tick: int) -> None:
    from ow174.game.combat import TICK

    component = script.component
    instance = next((i for i in component.instances.values() if i.graph.index == 0x0242), None)
    if instance is None:
        return
    wait = instance.states.get(instance.graph.node(7).state)
    deadline = getattr(wait, "world_deadline", None)
    if deadline is None or tick < deadline:
        return
    tick_ms = round(TICK * 1000)
    if wait.active:
        wait.request_finish()
        component.run_now()
        chase = instance.states.get(instance.graph.node(12).state)
        if chase is not None and chase.active:
            # The first elapsed step starts at the exact authored timeout.
            chase.world_used = deadline * tick_ms - (-wait.ms % tick_ms)
    chase = instance.states.get(instance.graph.node(12).state)
    if chase is not None and chase.active and not chase.reached:
        elapsed = tick * tick_ms - chase.world_used
        if elapsed > 0:
            chase.world_used += elapsed
            chase.advance_to(chase.last + elapsed)
            # Current ChaseVar already supplies the one arithmetic implementation.
            # Retain the delivered accepted-body cursor after external world work.
            chase.last = chase.clock()
            component.run_now()
