"""Register missing viewers for a still-live original Echo launch, without firing again."""
from ow174.game import projectiles


def volley_activation(state, activation):
    """Freeze only an original Echo emission, at WeaponVolley's report boundary."""
    if (getattr(state.instance.component.world, 'hero', None) != 0x02E0000000000206
            or (state.instance.graph.index, state.node.pos) not in ((0x19E5, 5), (0x1C36, 1))
            or activation != (state.counter, state.start)):
        return None
    payload = state.payload()
    if (set(payload) != {'start', 'offset', 'volleys', 'counter'}
            or any(type(value) is not int for value in payload.values()) or type(state.active) is not bool
            or payload['counter'] != activation[0] or payload['start'] != activation[1]):
        return None
    return state.node.cls, state.active, tuple(sorted(payload.items()))


def remember_launch(shot, state, origin, direction, deadline, captured):
    """Bind a genuine report capture to its original accepted physical shot."""
    if (type(captured) is not tuple or len(captured) != 3
            or captured[0] != state.node.cls or type(captured[1]) is not bool
            or type(captured[2]) is not tuple or len(captured[2]) != 4
            or any(type(item) is not tuple or len(item) != 2
                   or type(item[0]) is not str or type(item[1]) is not int for item in captured[2])):
        return
    payload = dict(captured[2])
    if (set(payload) != {'start', 'offset', 'volleys', 'counter'}
            or (payload['counter'], payload['start']) != shot.activation):
        return
    shot.echo_launch = projectiles.ShotRecord(
        shot.frame, shot.instance, shot.state, shot.activation[0], tuple(origin), tuple(direction), (),
        shot=shot.shot_index, volleys=payload['volleys'] & 0xFF,
    )
    shot.observer_record = shot.echo_launch
    shot.observer_activation = captured
    shot.observer_deadline = deadline


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
    if (shot.observer_activation is None
            or any(member.observer_activation != shot.observer_activation for member in members)):
        return  # Missing authentic emission state cannot be reconstructed from the current State.
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
        original_record=original, deadline=deadline, original_activation=shot.observer_activation,
    )
