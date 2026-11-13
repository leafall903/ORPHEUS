"""Register missing viewers for a still-live original Echo launch, without firing again."""
from ow174.game import projectiles


def observe_live(fight, rocket):
    shot = rocket.shot
    if shot is None or shot.echo_launch is None or shot.release_at is not None:
        return
    owner = shot.player
    script = owner.body_script
    if owner.body != shot.body or script is None or script.retired:
        return
    original = shot.echo_launch
    if shot.definition == 0x0400000000002CBF:
        if shot.lease_offset != 0:
            return
        members = [item.shot for item in fight.rockets if item.shot is not None
                   and item.shot.body == shot.body and item.shot.frame == shot.frame
                   and (item.shot.instance, item.shot.state, item.shot.activation)
                   == (shot.instance, shot.state, shot.activation)]
        members.sort(key=lambda item: item.lease_offset)
        # Do not replay an incomplete native three-pellet event after a pellet retired.
        if len(members) != 3 or any(item.release_at is not None for item in members):
            return
    elif shot.definition == 0x0400000000002CBA:
        members = [shot]
    else:
        return
    instance = script.component.instances.get(shot.instance)
    state = instance.states.get(shot.state) if instance is not None else None
    if state is None:
        return
    if (state.counter, state.volleys & 0xFF) != (original.activation, original.volleys):
        return  # An older activation needs a qualified historical remote-volley service.
    viewers = [viewer for viewer in fight.match.players
               if viewer is not owner and viewer.client is not None and viewer.spawned and not viewer.gone
               and not any(delivery.viewer is viewer and delivery.client is viewer.client
                           for delivery in shot.observers)]
    if not viewers:
        return
    deadline = min((delivery.deadline for member in members for delivery in member.observers),
                   default=projectiles.owner_to_world(shot.clock_origin, original.frame)
                   + shot.bound + projectiles.DELIVERY_GRACE_TICKS)
    if fight.tick >= deadline:
        return
    fight.projectiles._show_others(
        shot, state, original.origin, original.direction, None, shots=tuple(members), viewers=viewers,
        original_record=original, deadline=deadline,
    )
