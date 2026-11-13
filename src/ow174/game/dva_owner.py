"""Owner projection of the separately created D.Va mech body."""

from ow174.game import observers
from ow174.game.script.driver import HUD_HEALTH, HUD_MAX_HEALTH, BodyScript, ReplicatedBody


def create_movement(bot, viewer):
    """Seed the owner's0407 network state before44 can hand it local control."""
    from ow174.game import correction, dva
    from ow174.game.mover import Mover, defined_mover_data

    if (viewer is None or bot.hero != dva.HERO or bot.body != dva.MECH
            or bot.owner != viewer.entity or bot.source_body != viewer.body
            or bot.dead or bot.detached_bomb):
        return None
    mover = bot.control_mover
    if mover is None:
        #026A publishes its create before its Possess node installs control_mover.
        data = defined_mover_data(dva.MECH)
        if data is None:
            raise ValueError("Owned DVa mech movement definition is unavailable")
        mover = Mover(bot.position, round(bot.yaw), viewer.match.collision_world,
                      data, category=viewer.mover.category)
        mover.velocity = bot.velocity
        mover.pitch = round(bot.pitch)
    state = correction.movement(mover.snapshot(bot.body_script.frame))
    state.frame = None  # create's native frame domain: CREATE_BASE minus one
    state.spring = mover.movement_spring()  # constructor spring_steps has not been posed yet
    return state


class _Projection:
    """Reuse the normal player-body owner codec against the same mech VM."""

    _add_var = BodyScript._add_var
    _add_state = staticmethod(BodyScript._add_state)
    _volley_start = BodyScript._volley_start
    delta = BodyScript.delta
    callout_snapshot = ReplicatedBody.callout_snapshot

    def __init__(self, source, stream):
        self.source = source
        self.stream = stream
        self.frame_ms = source.component.frame_ms

    def __getattr__(self, name):
        return getattr(self.source, name)

    def snapshot(self):
        snapshot = BodyScript.snapshot(self)
        # BotScript publishes51-derived health but has no player HUD setters.
        # Only this current-mech owner projection needs the normal HUD numbers.
        vitals = self.component.world.match.combat.vitals.get(self.component.entity)
        if vitals is not None:
            snapshot.vars[(0, HUD_HEALTH)] = (float(vitals.total), ())
            snapshot.vars[(0, HUD_MAX_HEALTH)] = (float(vitals.maximum), ())
        return snapshot


def flush(client, delivery):
    """Return true when this delivery needs the controlled-body owner path."""
    source, stream = delivery.source, delivery.stream
    if not hasattr(source, "dva_birth"):
        return False
    bot = client.player.controlled_mech
    controlled = bot is not None and bot.body_script is source and bot.entity == stream.entity
    if not controlled and not stream.cmfd:
        return False
    if stream.base is None or stream.pending:
        return True
    if controlled and stream.entity in client._body_destroy_wait:
        return False  # let the existing terminal stream drain before the held destroy
    if not controlled:
        # Control ended. Replace the owner projection with an acknowledged plain
        # full baseline before continuing the ordinary detached/death stream.
        snapshot = (delivery.death_frames[0] if delivery.death_frames else
                    delivery.death_snapshot or delivery.live or source.latest_live)
        snapshot = client._project_bot_snapshot(snapshot, known=stream.base)
        update = stream.full(observers.full_frame(source, snapshot), snapshot)
        stream.cmfd = 0
        delivery.live = None
        client.queue_entities([update])
        return True
    if source.frame <= stream.cmfd:
        return True
    frame, keys, snapshot = _Projection(source, stream).delta(
        stream.base, stream.touched(), source.frame, bool(stream.cmfd), kill_feed=False,
    )
    update = stream.delta(frame, snapshot, keys, source.frame)
    update.resend = True
    delivery.live = None
    client.queue_entities([update])
    return True


def poll(client):
    """Owner-only switches and HUD changes need not change an observer snapshot."""
    for delivery in client._bot_scripts.values():
        flush(client, delivery)


def park_pilot(player, client):
    """A primary pilot with no native43 possession must not keep its owner HUD on."""
    if player.controlled_mech is None:
        return False
    script = player.body_script
    if not hasattr(script, "dva_birth"):
        return False
    stream = script.stream
    if not stream.last:
        if script.create_arrived:
            snapshot = observers.snapshot(script)
            client.queue_entities([stream.full(observers.full_frame(script, snapshot), snapshot)])
    elif stream.base is not None and not stream.pending:
        snapshot = observers.snapshot(script)
        # A Call Mech handoff can park a pilot which already had owner states.
        # Clear only the owner projection; retain its authored remote states.
        if stream.cmfd:
            client.queue_entities([stream.full(observers.full_frame(script, snapshot), snapshot)])
            stream.cmfd = 0
            stream.initial_owner = None
            script.first_owner_frame = None
        else:
            frame, keys = observers.delta(script, stream, snapshot)
            if frame is not None:
                update = stream.delta(frame, snapshot, keys, 0)
                update.resend = True
                client.queue_entities([update])
    return True
