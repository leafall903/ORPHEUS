"""Living NPC capsule peers for the ordinary player movement solver."""

import math

from ow174.game import contacts, correction
from ow174.game.collision import MOVER, TEAM0, TEAM1
from ow174.game.mccree import TALON_AI_SIZE_CAP_BODIES
from ow174.game.mover import mover_data


def step_pose(owner, command, seconds, tick, *, speed_scale=1.0):
    mover = owner.mover
    data = mover.values
    speed = max(math.hypot(mover.velocity[0], mover.velocity[2]),
                data.run_forward, data.run_backward, data.run_strafe)
    reach = max(data.stand_radius, data.crouch_radius) + speed * seconds + 0.04
    x, _, z = mover.position
    candidates = []
    for bot in owner.match.bots:
        category = TEAM0 if bot.team == 0 else TEAM1
        if (bot.dead or not category & mover.peer_mask
                or (not owner.match.game_map.free_for_all and bot.team == owner.team)):
            continue
        active = bot.control_mover or bot.impulse_mover
        if active is None and bot.navigation is not None:
            active = bot.navigation.mover
        values = active.data if active is not None else bot.movement_data or mover_data(bot.body)
        crouched = active is not None and active.crouched
        lift = active._lift() if active is not None else values.stand_pogo
        radius, segment = values.capsule(crouched, limit_ai_size=bot.body in TALON_AI_SIZE_CAP_BODIES)
        px, py, pz = bot.position
        if math.hypot(px - x, pz - z) > reach + radius:
            continue
        bottom = py + lift + radius
        capsule = contacts.Capsule((px, bottom, pz), (px, bottom + segment, pz), radius, MOVER)
        peer = contacts.make_contact(contacts.PeerShape(capsule, category), mover.category)
        candidates.append((peer, any(bot.velocity)))
    if not candidates:
        return mover.step_pose(command, seconds, tick, speed_scale=speed_scale)
    body = mover._engine_body()
    peers = [peer for peer, _ in candidates]
    body.contacts[:0] = peers
    try:
        result = mover.step_pose(command, seconds, tick, speed_scale=speed_scale)
        if any(moving and peer.manifolds(body) for peer, moving in candidates):
            correction.force_impulse(owner, first_frame=command.frame)
        return result
    finally:
        for peer in peers:
            if peer in body.contacts:
                body.contacts.remove(peer)
