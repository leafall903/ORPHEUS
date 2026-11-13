"""Actual throw/landing with a synthetic delayed owner-report schedule."""

import json
import os
import unittest
from pathlib import Path

import test_game_torbjorn_turret as turret_tests
from test_game_packet_budget import MemorySocket, decoded

from ow174.game import combat, hero_torbjorn, projectiles, server_snapshot, world
from ow174.game.link import ACK, KIND_CONTROL, LinkCipher, Packet, ReceiveWindow
from ow174.game.script.driver import OwnerReport
from ow174.game.server import Client, GameServer


@unittest.skipUnless(os.environ.get("ORPHEUS_TORBJORN_GRAPH_BUNDLE"), "needs prepared Torbjorn bundle")
class RetiredTurretAliasesTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        path = (Path(__file__).resolve().parents[2]
                / "fixtures/behavior/torbjorn-retired-throw-aliases-v1.json")
        cls.contract = json.loads(path.read_text())

    def throw(self):
        run, owner, _, _ = turret_tests.TorbjornTurretTests().arena(100, deploy=False)
        run.step({owner: (0, 4000, 8)})
        for _ in range(35):
            run.step({})
            if run.match.combat.rockets:
                shot = run.match.combat.rockets[0].shot
                self.assertIn(shot, run.match.combat.projectiles.shots)
                return run, owner, shot
        self.fail("actual input did not produce the turret carrier")

    @staticmethod
    def report(owner, shot, offset, *, received, activation=None):
        script = owner.body_script
        script.report_count += 1
        key = shot.instance, shot.state
        frame = shot.frame + offset
        report = OwnerReport(script.report_count, frame, frame,
                             {key: shot.activation if activation is None else activation},
                             frozenset({key}), received)
        script.reports.append(report)
        return report

    def land(self, run, owner):
        for _ in range(100):
            if owner.entity in run.match.combat.turrets:
                return
            run.step({})
        self.fail("actual throw did not land")

    @staticmethod
    def updates(owner, entity, operation):
        return [update for update in owner.client.entities
                if update.entity == entity and update.op == operation]

    def test_late_ack_and_introduction_destroy_each_alias_once_without_new_actor(self):
        run, owner, shot = self.throw()
        manager = run.match.combat.projectiles
        inputs, expected = self.contract["input"], self.contract["expected"]
        offset = inputs["late_introduction_frame_offset"]
        pending = self.report(owner, shot, offset, received=None)
        alias = manager.lease_id(owner, shot.definition, pending.cmfd)
        self.land(run, owner)
        self.assertNotIn(shot, manager.shots)
        self.assertNotIn(alias, shot.created)
        actor = run.match.combat.turrets[owner.entity].actor
        pending.received = True
        run.step({})
        destroys = self.updates(owner, alias, world.OP_DESTROY)
        self.assertEqual(len(destroys), expected["destroy_count_per_late_alias"])
        self.assertIsNone(destroys[0].ch2)
        self.assertFalse(self.updates(owner, alias, world.OP_CREATE))
        second = self.report(owner, shot, inputs["second_introduction_frame_offset"], received=True)
        second_alias = manager.lease_id(owner, shot.definition, second.cmfd)
        run.step({})
        for _ in range(inputs["repeated_report_count"]):
            self.report(owner, shot, offset, received=True)
            run.step({})
        self.assertEqual(len(self.updates(owner, alias, world.OP_DESTROY)), 1)
        self.assertEqual(len(self.updates(owner, second_alias, world.OP_DESTROY)), 1)
        self.assertFalse(self.updates(owner, second_alias, world.OP_CREATE))
        self.assertEqual([bot for bot in run.match.bots if bot.body == hero_torbjorn.TURRET], [actor])
        boundaries = {"fight": run.match.combat, "match": run.match,
                      "owner": owner, "launch-vm": owner.body_script}
        boundaries.update({f"observer/{index}": delivery for index, delivery in enumerate(shot.observers)})
        stored = server_snapshot._Graph(boundaries, None, {})
        reference = stored.value(manager)
        document = stored.finish()
        self.assertIn("retired_turrets", document[reference[1]]["state"]["fields"])
        self.assertIn('"launch-vm"', json.dumps(document))

    def test_invalid_reports_are_ignored_and_pending_ack_cannot_extend_expiry(self):
        run, owner, shot = self.throw()
        manager = run.match.combat.projectiles
        self.land(run, owner)
        wrong = self.report(owner, shot, 4, received=True,
                            activation=(shot.activation[0] + 1, shot.activation[1]))
        outside = self.report(owner, shot, projectiles.REPORT_WINDOW + 1, received=True)
        pending = self.report(owner, shot, 5, received=None)
        for _ in range(projectiles.REPORT_WINDOW + projectiles.DELIVERY_GRACE_TICKS + 2):
            run.step({})
        self.assertFalse(manager.retired_turrets)
        for report in (wrong, outside, pending):
            alias = manager.lease_id(owner, shot.definition, report.cmfd)
            self.assertFalse(self.updates(owner, alias, world.OP_DESTROY))
        pending.received = True
        manager.update(run.match.tick + 1)
        alias = manager.lease_id(owner, shot.definition, pending.cmfd)
        self.assertFalse(self.updates(owner, alias, world.OP_DESTROY))

    def test_replaced_script_cannot_retire_an_old_throws_alias_on_the_new_body(self):
        run, owner, shot = self.throw()
        manager = run.match.combat.projectiles
        pending = self.report(owner, shot, 2, received=None)
        alias = manager.lease_id(owner, shot.definition, pending.cmfd)
        self.land(run, owner)
        original = owner.body_script
        try:
            owner.body_script = object()  # Same body id, different incarnation of its source VM.
            pending.received = True
            manager.update(run.match.tick + 1)
            self.assertFalse(manager.retired_turrets)
            self.assertFalse(self.updates(owner, alias, world.OP_DESTROY))
        finally:
            owner.body_script = original

    def test_late_alias_destroy_retries_through_the_real_client_delivery_queue(self):
        run, owner, shot = self.throw()
        manager = run.match.combat.projectiles
        self.land(run, owner)
        server = GameServer()
        server.sock = MemorySocket()
        key_in, key_out = b"i" * 32, b"o" * 32
        client = Client(server, ("memory.test", 40001), 1, LinkCipher(key_in, key_out), owner)
        server.clients[1] = owner.client = client
        client.open = True
        client._ack_entity_create(owner.entity)
        peer, window = LinkCipher(key_out, key_in), ReceiveWindow()
        report = self.report(owner, shot, 2, received=True)
        alias = manager.lease_id(owner, shot.definition, report.cmfd)
        tick = run.match.tick + 1
        manager.update(tick)

        def send(frame):
            client.send_frame(frame, frame * combat.TICK)
            packet, *_ = decoded(self, peer, server.sock.sent[-1][0])
            records = [(item.op, item.ch2 is not None) for item in client.in_flight[packet.seq][1]
                       if item.entity == alias]
            return packet, records

        _, first = send(tick)  # Drop the destroy datagram.
        self.assertEqual(first, [(world.OP_DESTROY, False)])
        packet, _ = send(tick + 1)
        self.assertTrue(window.accept(packet.seq))
        ack = Packet(client.conn, KIND_CONTROL, 0, window.expected, window.bits, ACK)
        server._datagram(peer.seal(ack), client.address, (tick + 1) * combat.TICK)
        _, retry = send(tick + 2)
        self.assertEqual(retry, [(world.OP_DESTROY, False)])
