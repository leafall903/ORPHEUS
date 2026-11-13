"""Corrections of the owner's own body: the server's state of the body, sent to the player who moves it.

The owner's client moves its own body itself and keeps the state it predicted for each command frame (64
of them, mover +0x1580). A ch3 movement record on the body it possesses goes, past the gate that keeps
other entities' states, to its player's component 59 (0x7FF7896DA7E7 -> 0x7FF7896DA8AC): when
comp59+0x3E0 <= the record's frame, the state is copied to comp59+0x70 and +0x3E0 = frame + 1. On its
next tick (0x7FF78970F660) the client takes its predicted state of that same frame (history vt+0x60 and
vt+0x58, the frame checked at 0x7FF78970FA0D) and compares the two with 0x7FF789B73E00 in mode 2: every
field exactly (flags, frames, throttles, gravity, spring steps and speed, pitch, angles, position,
velocity, +832), all but +384. Equal: nothing happens but the bookkeeping (0x7FF789ACE870 drops mods that
ended before it) and +0x3E0 = 0. Different, or no prediction of that frame: comp59+0x60 = 1 and the
client puts its body in the server's state and runs its later commands again.

So a record must carry the state after the tick of command frame F with frame = F. The native reader
subtracts an unsigned count modulo 2**32 (0x7FF789B76DFE), so an explicitly selected finite owner
correction can encode F after the packet world frame. Ordinary records retain the host policy F <= tick.
The client advances its command frame when a packet frame passes its own frame: it
jumps to that frame plus a lead of at most 16 (0x7FF7896DAF7A..0x7FF7896DB296). The server runs the
commands as they come, often before their frame is the server's tick, so it sends the newest state whose
frame is not after the packet frame and that it did not send yet, one per frame. Retail sent the local
body a ch3 record in every frame (OW2 traffic).

What the client does with a different state (0x7FF789710390, 0x7FF78970FD80, 0x7FF789B7DF20): when its
body's +12 (a discontinuity counter, 0 in our records and in the body's create) equals the server's, it
puts the body in the server's state and runs the commands after F again, from the window it keeps for
sending them (comp59+0xAF0, at most 32 frames); else it takes the server's state with no replay. It keeps
only the commands from ack - 1 on (0x7FF7896DAF4F -> 0x7FF789B3D6E0), and a frame it no longer has runs
with its oldest command (0x7FF789B3D586). So while it corrects, the ack stays at most two past the newest
record (`command_ack`). The spring offsets themselves (+44, +48) are not sent: the decoded state has them
at 0 (0x7FF789B75EC0) and the body takes them so (0x7FF789B4AC10), so after a correction the client's
spring starts again from 0 while ours goes on (a few more corrections on stairs and slopes, unverified).

Only for a body whose statescript the server runs (match.BODY_SCRIPT_HEROES): the mods it has on change the
mover (Sprint), and a body whose abilities move it in ways the server does not know (Genji's double jump,
Lucio's wall ride, Mercy's flight) would be pulled back by every record. Of those, only the heroes in HEROES:
the ones whose movement the server's mover follows. DVa additionally selects its actual possessed
0407 control mover while the primary pilot is parked, then its pilot mover after return. Its own-client
NPC interpolation record must not also enter that native local correction branch. This target selection
does not establish native movement/Boosters or ejection acceptance.
"""

from dataclasses import dataclass

from ow174.game import dva, mccree, movelog
from ow174.game.content import SOLDIER
from ow174.game.mover import Mover, Snapshot
from ow174.game.world import NO_INPUT, EntityUpdate, Movement

CORRECTIONS = True  # False: the owner never gets its own body's state (each client then keeps its own)
HEROES = {SOLDIER}  # the heroes whose movement abilities the mover follows
ACK_LEAD = 2  # the client keeps the commands from ack - 1 on; a replay needs them from the record's frame + 1
IMPULSE_WINDOW_US = 2_000_000  # cover the sandbox knockback and landing


@dataclass(frozen=True)
class SendToken:
    mover: Mover
    body: int
    frame: int
    tick: int
    script: object
    wrapped_frame: bool = False


def snapshot_for(mover: Mover, tick: int, *, allow_future: bool = False) -> Snapshot | None:
    """Newest unsent actual command state; crossing packet time requires explicit opt-in."""
    sent = mover.corrected
    for snapshot in reversed(mover.history):
        if allow_future or snapshot.frame <= tick:
            return snapshot if sent is None or snapshot.frame > sent else None
    return None


def movement(snapshot: Snapshot) -> Movement:
    """The record's state: the mover's snapshot at its own frame."""
    if snapshot.input_frame is None:
        input_frame = NO_INPUT
    elif snapshot.input_frame == snapshot.frame:
        input_frame = None  # the state's own frame: +34 and +35 are then +32 and +33
    else:
        input_frame = snapshot.input_frame
    return Movement(
        snapshot.position,
        snapshot.yaw,
        snapshot.pitch,
        snapshot.velocity,
        snapshot.flags,
        throttles=snapshot.throttles,
        input_frame=input_frame,
        input_throttles=snapshot.input_throttles,
        air_ticks=snapshot.air_ticks,
        gravity=snapshot.gravity,
        frame=snapshot.frame,
        crouch_frame=snapshot.crouch_end,
        spring=snapshot.spring,
        spring_speed=snapshot.spring_speed,
        fall=snapshot.fall,
    )


def force_impulse(player, *, first_frame: int | None = None) -> None:
    """Replicate a server-only velocity change for McCree's current body."""
    if player.hero.guid != mccree.HERO:
        return
    _force_motion(player, first_frame=first_frame)


def force_charge(player, *, first_frame: int) -> None:
    """Deliver the reconstructed Reinhardt Charge drive to its owning client."""
    if player.hero.guid != 0x02E0000000000007:
        return
    _force_motion(player, first_frame=first_frame)


def _force_motion(player, *, first_frame: int | None = None) -> None:
    if not CORRECTIONS or player.body_script is None:
        return
    last = player.commands.last_frame
    first = first_frame if first_frame is not None else (player.match.tick if last is None else last + 1)
    through = first + (IMPULSE_WINDOW_US - 1) // player.quanta
    previous = getattr(player, 'impulse_correction', None)
    player.impulse_correction = (previous[0] if previous is not None else first, through)


def _target(player) -> tuple[int, Mover] | None:
    """The actual body this connection currently predicts; DVa parks its pilot."""
    if getattr(getattr(player, "hero", None), "guid", None) == dva.HERO:
        bot = player.controlled_mech
        if bot is not None:
            if (bot not in player.match.bots or bot.hero != dva.HERO or bot.body != dva.MECH
                    or bot.owner != player.entity or bot.source_body != player.body
                    or bot.dead or bot.detached_bomb or bot.control_mover is None
                    or bot.body_script is None
                    or bot.body_script.component.fault is not None):
                return None  # wait for the return handoff; the parked pilot is not current
            return bot.entity, bot.control_mover
    return player.body, player.mover


def request_motion(player, *, first_frame: int, through_frame: int | None = None,
                   body: int | None = None, expires_tick: int | None = None,
                   first_valid_frame: int | None = None) -> bool:
    """A finite recipient-command-frame lease, never a permanent hero opt-in.

    Producers map physical time before calling. This only enables the existing
    prediction record/ACK path; it does not apply a second force or teleport.
    """
    from ow174.game.combat import TICK

    target = _target(player)
    if (not CORRECTIONS or not player.has_body or player.body_script is None
            or player.body_script.retired
            or target is None or (body is not None and target[0] != body)
            or type(first_frame) is not int or not 0 <= first_frame <= 0xFFFFFFFF
            or player.quanta <= 0):
        return False
    if through_frame is None:
        through_frame = first_frame + (IMPULSE_WINDOW_US - 1) // player.quanta
    if type(through_frame) is not int or not first_frame <= through_frame <= 0xFFFFFFFF:
        return False
    if (first_valid_frame is not None and (type(first_valid_frame) is not int
            or not first_frame <= first_valid_frame <= through_frame)):
        return False
    now = player.match.tick
    if expires_tick is None:
        expires_tick = now + (IMPULSE_WINDOW_US - 1) // round(TICK * 1_000_000)
    if type(expires_tick) is not int or not now <= expires_tick <= 0xFFFFFFFF:
        return False
    previous = _motion_window(player)
    floor = getattr(player, 'motion_correction_floor', None)
    player.impulse_correction = (
        min(previous[0], first_frame) if previous is not None else first_frame,
        max(previous[1], through_frame) if previous is not None else through_frame,
    )
    previous_deadline = getattr(player, 'motion_correction_deadline', now)
    player.motion_correction_deadline = max(previous_deadline, expires_tick)
    player.motion_correction_target = (*target, player.body_script)
    if first_valid_frame is not None:
        # Authored relocation invalidates older endpoints without relabelling
        # an old snapshot or changing ordinary overlapping-force admission.
        player.motion_correction_floor = max(floor, first_valid_frame) if floor is not None else first_valid_frame
    return True


def _motion_window(player, tick=None):
    window = getattr(player, 'impulse_correction', None)
    binding = getattr(player, 'motion_correction_target', None)
    if binding is not None:
        target = _target(player)
        deadline = getattr(player, 'motion_correction_deadline', None)
        now = player.match.tick if tick is None else max(player.match.tick, tick)
        if (target is None or binding[0] != target[0] or binding[1] is not target[1]
                or binding[2] is not player.body_script or player.body_script.retired
                or (deadline is not None and now > deadline)):
            player.impulse_correction = None
            del player.motion_correction_target
            if hasattr(player, 'motion_correction_deadline'):
                del player.motion_correction_deadline
            if hasattr(player, 'motion_correction_floor'):
                del player.motion_correction_floor
            return None
    return window


def _eligible(player) -> bool:
    return (CORRECTIONS and player.has_body and player.body_script is not None
            and (player.hero.guid in HEROES or player.hero.guid == dva.HERO
                 or _motion_window(player) is not None)
            and _target(player) is not None)


def correcting(player) -> bool:
    """The server can correct this body and the client has acknowledged its create."""
    if not _eligible(player) or not player.body_script.create_arrived:
        return False
    body, _ = _target(player)
    return body == player.body or (player.client is not None and player.client.bot_create_acked(body))


def own_record(player, tick: int) -> EntityUpdate | None:
    """This frame's record of the player's own body, if it gets one."""
    _motion_window(player, tick)
    if not correcting(player):
        return None
    body, mover = _target(player)
    finite = getattr(player, 'motion_correction_target', None) is not None
    snapshot = snapshot_for(mover, tick, allow_future=finite)
    if snapshot is None:
        return None
    impulse = _motion_window(player)
    first = max(impulse[0], getattr(player, 'motion_correction_floor', impulse[0])) if impulse is not None else None
    if first is not None and snapshot.frame < first:
        return None
    update = EntityUpdate(body, movement=movement(snapshot))
    update.correction = SendToken(mover, body, snapshot.frame, tick, player.body_script, finite)
    return update


def wrapped_record(update, tick: int | None) -> bool:
    """Only an explicitly selected finite owner's record may cross packet time.

    The original C66D00/C66DFE reader subtracts a uint32 count modulo 2**32.
    This changes neither the packet world frame nor observer record selection.
    """
    token = update.correction
    return (isinstance(token, SendToken) and token.wrapped_frame
            and token.body == update.entity and token.tick == tick
            and update.movement is not None and token.frame == update.movement.frame
            and not token.script.retired)


def _current(player, token) -> bool:
    window = _motion_window(player)
    target = _target(player)
    return (isinstance(token, SendToken) and target is not None
            and token.mover is target[1] and token.body == target[0]
            and token.script is player.body_script and not token.script.retired
            and (not token.wrapped_frame or window is not None)
            and token.frame >= getattr(player, 'motion_correction_floor', 0))


def sent(player, update) -> None:
    """Commit a selected correction only after the containing datagram was sent successfully."""
    token = update.correction
    if not _current(player, token):
        return
    previous = token.mover.corrected
    if previous is not None and previous >= token.frame:
        return
    token.mover.corrected = token.frame
    impulse = _motion_window(player)
    if impulse is not None and token.frame >= impulse[1]:
        player.impulse_correction = None
        if hasattr(player, 'motion_correction_target'):
            del player.motion_correction_target
        if hasattr(player, 'motion_correction_deadline'):
            del player.motion_correction_deadline
        if hasattr(player, 'motion_correction_floor'):
            del player.motion_correction_floor
    if movelog.ENABLED:
        movelog.sent(player, token.tick, token.frame, command_ack(player, token.tick))


def command_ack(player, tick: int, updates=()) -> int:
    """Acknowledge received input while keeping the commands a movement correction replays."""
    frame = player.commands.last_frame
    newest = tick if frame is None else frame
    if not _eligible(player):
        return newest
    _, mover = _target(player)
    sent = mover.corrected
    for update in updates:
        token = update.correction
        if _current(player, token) and (sent is None or token.frame > sent):
            sent = token.frame
    impulse = _motion_window(player)
    if impulse is not None:
        # Earlier commands were already eligible for ACK before the impulse.
        first = max(impulse[0], getattr(player, 'motion_correction_floor', impulse[0]))
        sent = first - 1 if sent is None else max(sent, first - 1)
    if sent is None:
        # While create ACK or packet budget delays the first record, its eligible frame
        # already bounds which commands the owner can drop. Future states cannot go yet.
        snapshot = snapshot_for(mover, tick)
        sent = tick if snapshot is None else snapshot.frame
    return min(newest, sent + ACK_LEAD)
