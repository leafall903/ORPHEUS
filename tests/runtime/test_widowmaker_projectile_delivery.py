"""Actual encrypted Widowmaker mine command, create/ACK and destroy lifecycle.

Synthetic peers exercise production GameServer/Client paths. Client visibility
remains pending.
"""

import unittest
from types import SimpleNamespace

from ow174.game import venom
from ow174.game.collision import StaticCollisionProvider, World
from ow174.game.link import KIND_FRAME
from ow174.game.server import GameServer
from ow174.game.world import OP_CREATE, OP_DESTROY
from tests.runtime import test_server_journal as journal
from tests.runtime.test_game_packet_budget import MemorySocket


def exercise(late_owner_ack):
    fixture = journal.ServerJournalTests()
    server = GameServer(
        environment=journal.FixedSource(),
        collision_provider_factory=lambda: StaticCollisionProvider(
            World([], name="mine expiry without contacts")
        ),
    )
    server.sock = MemorySocket()
    sent = server.sock.sent
    session = SimpleNamespace(
        server=server,
        create_match=server.create_match,
        datagram=server._datagram,
        tick=server._tick,
        report_callbacks=server._report_leaves,
    )
    peers, players = fixture.connected(session, sent, first_hero=venom.HERO)
    owner, observer = players
    match = session.server.matches[0]
    assert match.switch_hero(owner, owner.hero, preserve_pose=True, start_frame=9000)
    owner.commands.reset(last_frame=9000)
    now = 100.928
    for i in range(24):
        now += 0.016
        session.tick(now)
        peers[0].commands(9001 + i, 1, 0, now=now)
        peers[1].commands(observer.body_script.frame + 1, 1, 0, now=now)
    first = owner.body_script.frame + 1
    clients = [next(c for c in session.server.clients.values() if c.player is p) for p in players]
    all_records = [[], []]
    cursor = len(sent)
    shot = None
    blocked = False
    old_drain = peers[0].drain

    def owner_drain(*, lose=False):
        old_drain(lose=lose or blocked)

    peers[0].drain = owner_drain
    expiry = None
    born = None
    for step in range(365):
        now += 0.016
        session.tick(now)
        if shot is not None:
            own_delivery = next(delivery for delivery in shot.observers if delivery.viewer is owner)
            # Hold owner launch ACK until twenty world ticks after physical expiry.
            if late_owner_ack and own_delivery.creation.received and expiry is None:
                blocked = True
            if not match.combat.venom.flights and expiry is None:
                expiry = match.tick
            if expiry is not None and match.tick >= expiry + 20:
                blocked = False
        for data, address in sent[cursor:]:
            for index, peer in enumerate(peers):
                if address != peer.address:
                    continue
                packet = peer.cipher.open(data)
                assert packet is not None
                if packet.kind == KIND_FRAME:
                    client = clients[index]
                    records = client.in_flight.get(packet.seq, (None, []))[1]
                    all_records[index].extend(records)
        cursor = len(sent)
        peers[0].commands(first + step, 1, 16 if step == 0 else 0, now=now)
        peers[1].commands(observer.body_script.frame + 1, 1, 0, now=now)
        if shot is None and match.combat.venom.flights:
            flight = match.combat.venom.flights[0]
            shot = flight.shot
            born = {
                "world": match.tick,
                "ownerFrame": shot.frame,
                "last_tick": flight.last_tick,
                "anchor": owner.body_script.world_frame_anchor,
            }
    assert shot is not None
    assert born["world"] != born["ownerFrame"]
    assert shot.authoritative and shot.spawn == venom.MINE_DEFINITION
    assert shot.release_at is None
    assert shot not in match.combat.projectiles.shots
    assert not match.combat.venom.flights
    assert not match.combat.projectiles.observed
    deliveries = []
    for delivery in shot.observers:
        index = 0 if delivery.viewer is owner else 1
        records = [record for record in all_records[index] if record.entity == delivery.record.ids[0]]
        ops = [record.op for record in records]
        assert OP_CREATE in ops and OP_DESTROY in ops, ops
        assert ops.index(OP_CREATE) < ops.index(OP_DESTROY)
        assert delivery.creation.received
        if not late_owner_ack or delivery.viewer is not owner:
            assert delivery.launch_arrival.received
        assert all(record.ch2 is None for record in records if record.op == OP_DESTROY)
        assert delivery.finished
        assert not clients[index]._entity_lifecycles[delivery.record.ids[0]][1]
        assert not any(record.entity == delivery.record.ids[0] for record in clients[index].entities)
        assert not any(
            record.entity == delivery.record.ids[0]
            for _, outstanding_records in clients[index].in_flight.values()
            for record in outstanding_records
        )
        deliveries.append(
            {
                "viewer": index,
                "entity": delivery.record.ids[0],
                "ops": list(dict.fromkeys(ops)),
                "createdAck": delivery.creation.received,
                "launchAck": delivery.launch_arrival.received,
                "finished": delivery.finished,
                "liveProxy": clients[index]._entity_lifecycles[delivery.record.ids[0]][1],
                "pendingProxyRecords": 0,
            }
        )
    return {
        "lateOwnerAck": late_owner_ack,
        "born": born,
        "physicalExpiryWorld": expiry,
        "queryWorld": match.tick,
        "shotReleaseAt": shot.release_at,
        "pendingShots": len(match.combat.projectiles.shots),
        "pendingObserved": len(match.combat.projectiles.observed),
        "deliveries": deliveries,
    }


class WidowmakerProjectileDeliveryTests(unittest.TestCase):
    def test_separate_epochs_retire_both_mine_projectile_copies(self):
        result = exercise(False)
        self.assertEqual(result["pendingObserved"], 0)

    def test_owner_launch_ack_after_expiry_leaves_no_projectile_proxy(self):
        result = exercise(True)
        self.assertEqual(result["pendingObserved"], 0)


if __name__ == "__main__":
    unittest.main()
