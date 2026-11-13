"""Opt Hanzo's admitted arrows into physical event replay."""

from dataclasses import replace

from ow174.game import projectile_clock

FLIGHTS = {
    (0x00B9, 0x0400000000000144),
    (0x00B9, 0x040000000000020B),
    (0x00B9, 0x040000000000270F),
    (0x00B9, 0x0400000000000149),
    (0x00B9, 0x040000000000020D),
    (0x0101, 0x040000000000034F),
}


HISTORY_ARROWS = {(0x00B9, 0x0400000000000144), (0x00B9, 0x0400000000000149)}

def targets_at_impact(fight, rocket, candidates):
    """Use retained physical-tick poses for the proved delayed Hanzo direct hits."""
    if ((rocket.source_graph, rocket.definition) not in HISTORY_ARROWS
            or rocket.shot is None or rocket.shot.clock_origin is None
            or rocket.reflected or fight.tick >= fight.match.tick):
        return candidates
    return [replace(shown, construct=target.construct) for target in candidates
            if (shown := fight.seen(target, fight.tick)) is not None]

def clock_origin(script):
    # Hanzo's anchor is fixed at this body's birth; capture it before a switch
    # can replace the player's current BodyScript.
    if script.hero != 0x02E0000000000005 or script.world_frame_anchor is None:
        return None
    return (*script.world_frame_anchor, script.frame_ms)


def advance(fight, rocket):
    if (rocket.source_graph, rocket.definition) not in FLIGHTS or rocket.last_tick is None:
        return False
    projectile_clock.advance_accepted(fight, rocket)
    if rocket in fight.rockets:
        observe_live(fight, rocket)
    return True


def confirmation_components(shot):
    """0149/020D own a native Pet; bind aliases before their launch event arrives."""
    if shot.definition not in (0x0400000000000149, 0x040000000000020D):
        return {}
    if shot.filter_bits is None:
        return {}  # no declared player filter snapshot; do not invent NPC affiliation
    return {72: [shot.body], 26: [shot.filter_bits]}


def remember_launch(shot, state, origin, direction, deadline):
    """Retain the emitted record even when no remote source body exists yet."""
    if (state.instance.graph.index, shot.definition) not in FLIGHTS or shot.clock_origin is None:
        return
    from ow174.game.projectiles import ShotRecord

    shot.observer_record = ShotRecord(
        shot.frame, shot.instance, shot.state, shot.activation[0],
        tuple(origin), tuple(direction), (), shot=shot.shot_index, volleys=state.volleys & 0xFF,
    )
    shot.observer_deadline = deadline


def observe_live(fight, rocket):
    """Add new connections to this physical arrow using its original source event."""
    shot = rocket.shot
    if (shot is None or shot.observer_record is None or shot not in fight.projectiles.shots
            or rocket.reflected or rocket.source_entity != shot.body):
        return
    owner, original, deadline = shot.player, shot.observer_record, shot.observer_deadline
    script = owner.body_script
    if (owner.gone or owner.body != shot.body or script is None or script.retired
            or deadline is None or fight.tick >= deadline):
        return
    instance = script.component.instances.get(shot.instance)
    state = (instance.states.get(shot.state)
             if instance is not None and (instance.graph.index, shot.definition) in FLIGHTS else None)
    if (state is None or state.counter != shot.activation[0]
            or state.volleys & 0xFF != original.volleys):
        return  # do not create an unlaunchable alias against a superseding source activation
    viewers = [viewer for viewer in fight.match.players
               if viewer is not owner and viewer.client is not None and viewer.spawned and not viewer.gone
               and not any(delivery.viewer is viewer and delivery.client is viewer.client
                           for delivery in shot.observers)]
    if viewers:
        fight.projectiles._show_others(
            shot, state, original.origin, original.direction, None,
            viewers=viewers, original_record=original, deadline=deadline,
        )
