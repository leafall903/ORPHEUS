"""Pharah primary through production encrypted owner and observer delivery."""

import json
import os
import unittest
from pathlib import Path

from ow174.game import combat, pharah, projectiles, world
from ow174.game.bits import BitReader
from ow174.game.commands import FIRE, Command
from ow174.game.content import heroes
from ow174.game.link import ACK, KIND_CONTROL, LinkCipher, Packet, ReceiveWindow
from ow174.game.server import Client, GameServer
from ow174.game.world import OP_CREATE, OP_DESTROY
from tests.runtime import test_game_projectiles as delivery_tests
from tests.runtime import test_pharah_combat as primary_tests
from tests.runtime.test_game_packet_budget import MemorySocket, decoded


class PharahProjectileDeliveryTests(unittest.TestCase):
    def test_primary_command_world_clock_is_independent_of_packet_batch_size(self):
        root = Path(os.environ.get("ORPHEUS_BEHAVIOR_FIXTURES")
                    or Path(__file__).resolve().parents[2] / "fixtures/behavior")
        contract = json.loads((root / "pharah-projectile-batch-clock-v1.json").read_text(encoding="utf-8"))
        inputs, expected = contract["input"], contract["expected"]
        samples = []
        impacts = []
        for batch in inputs["batches"]:
            run, player, enemy = primary_tests.PharahCombatTests().encounter()
            enemy.mover.position[2] = inputs["enemy_distance"]
            enemy.mover.body = None
            start = run.frame
            for first in range(1, inputs["commands"] + 1, batch):
                last = min(first + batch - 1, inputs["commands"])
                run.frame = start + last
                run.match.update(run.frame * combat.TICK, run.frame)
                player.take_commands([
                    Command(start + step, buttons=FIRE if step == inputs["fire_offset"] else 0)
                    for step in range(first, last + 1)
                ])
            rocket, = run.match.combat.rockets
            self.assertEqual(rocket.shot.frame, start + inputs["fire_offset"])
            self.assertAlmostEqual(rocket.flown, expected["flight_seconds"])
            self.assertAlmostEqual(rocket.position[2], expected["distance"])
            samples.append(rocket.position)
            for _ in range(50):
                run.step({})
                target = next(t for t in run.match.combat._targets() if t.owner is enemy)
                health = run.match.combat.vitals_of(target).total
                if health < 200:
                    break
            self.assertEqual(health, expected["enemy_health_after_impact"])
            self.assertFalse(run.match.combat.rockets)
            impacts.append(run.frame)
        self.assertEqual(impacts, [impacts[0]] * len(inputs["batches"]))
        for position in samples[1:]:
            for actual, expected in zip(position, samples[0], strict=True):
                self.assertAlmostEqual(actual, expected)

    def test_owner_acknowledged_volley_confirms_the_native_default_rocket(self):
        fixture = delivery_tests.OwnerVolleyDeliveryTests()
        self.addCleanup(fixture.doCleanups)
        player, reports, created = fixture.play(
            pharah.HERO,
            [Command(frame, buttons=FIRE if frame == 1022 else 0) for frame in range(1017, 1120)],
        )
        self.assertIn(pharah.PRIMARY_ENTITY, projectiles.definitions(player.body_script))
        self.assertTrue(reports)
        self.assertEqual(len(set(created)), 1)

    def test_observer_create_launch_ack_impact_and_retirement_keep_damage(self):
        fixture = delivery_tests.ObserverProjectileDeliveryTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        self.assertTrue(fixture.match.switch_hero(fixture.shooter, heroes()[pharah.HERO]))
        for _ in range(12):
            fixture.advance(3)
            fixture.send()
            fixture.ack()
        y = fixture.shooter.mover.position[1]
        fixture.shooter.mover.position = [0, y, 0]
        fixture.viewer.mover.position = [0, y, 8]
        fixture.shooter.mover.body = fixture.viewer.mover.body = None
        for _ in range(3):
            fixture.advance(3)
            fixture.send()
            fixture.ack()
        fixture.shooter.take_commands([Command(fixture.tick + 1, buttons=FIRE)])
        rocket, = fixture.fight.rockets
        self.assertEqual(rocket.definition, pharah.PRIMARY_ENTITY)
        self.assertIsNotNone(rocket.shot)
        delivery, = rocket.shot.observers
        creates = []
        destroys = []
        for _ in range(45):
            fixture.advance(3)
            for record in fixture.send():
                if record.entity == delivery.record.ids[0]:
                    if record.op == world.OP_CREATE:
                        creates.append(record)
                    if record.op == world.OP_DESTROY:
                        destroys.append(record)
            fixture.ack()
        self.assertEqual(len(creates), 1)
        payload = delivery_tests.bits_of(creates[0].build(0))
        self.assertEqual((payload.bit(), payload.bit(), payload.bits(12)), (0, 1, 0x33))
        self.assertTrue(delivery.launch_queued and delivery.launch_arrival.received and delivery.finished)
        self.assertEqual(len(destroys), 1)
        self.assertIsNotNone(destroys[0].ch2)
        target = next(t for t in fixture.fight._targets() if t.owner is fixture.viewer)
        self.assertEqual(fixture.fight.vitals_of(target).total, 80)
        self.assertEqual([notice["+0x78"]["+0x4"]
                          for notice in fixture.shooter.client.messages(combat.HIT)], [40, 80])
        self.assertFalse(fixture.fight.rockets)

    def test_different_owner_epoch_late_ack_retires_all_proxies_and_keeps_old_birth(self):
        caster, shot = self._epoch_delivery(owner_ack_delay=20, owner_origin=9000)
        clock, body = shot.clock_origin, shot.body
        self.assertTrue(caster.match.switch_hero(caster, caster.hero, preserve_pose=True, start_frame=12000))
        self.assertNotEqual(caster.body, body)
        self.assertEqual(shot.clock_origin, clock)
        self.assertNotEqual((*caster.body_script.world_frame_anchor, caster.body_script.frame_ms), clock)

    def test_dropped_owner_confirmation_ack_does_not_leak_a_proxy(self):
        self._epoch_delivery(owner_origin=9000, owner_no_ack=True)

    def _epoch_delivery(self, *, owner_ack_delay=0, owner_origin=None, owner_frame_ms=16, owner_no_ack=False):
        helper = primary_tests.PharahCombatTests()
        run, caster, target = helper.encounter()
        match = run.match
        before = helper.health(run, target).total
        if owner_origin is not None:
            caster.quanta = owner_frame_ms * 1000
            self.assertTrue(match.switch_hero(caster, caster.hero, preserve_pose=True,
                                              start_frame=owner_origin))
            caster.commands.reset(last_frame=owner_origin)
            # Complete the replacement's normal observer introduction before
            # substituting encrypted peers for the setup model.
            sent = 0
            for at in range(1, 21):
                run.frame += 1
                match.update(run.frame * combat.TICK, run.frame)
                through = at * 16 // owner_frame_ms
                if through > sent:
                    caster.take_commands([Command(owner_origin + frame)
                                          for frame in range(sent + 1, through + 1)])
                    sent = through
                target.take_commands([Command(run.frame)])
            owner_origin += sent
        else:
            owner_origin = run.frame
        server = GameServer()
        server.sock = MemorySocket()
        server.matches.append(match)
        peers = []
        for number, player in enumerate(run.players, 1):
            incoming, outgoing = bytes([number]) * 32, bytes([number + 10]) * 32
            client = Client(server, ('memory.test', 40000 + number), number,
                            LinkCipher(incoming, outgoing), player)
            server.clients[number] = client
            player.client = client
            client.open = True
            for other in run.players:
                client._track_body_create(other.body)
                client._ack_entity_create(other.entity)
                client._ack_entity_create(other.body)
            peers.append((client, LinkCipher(outgoing, incoming), ReceiveWindow(), [], []))
        cast_frame = owner_origin + 1
        shot = None
        owner_sent = 0
        owner_blocked = False
        for step in range(110):
            run.frame += 1
            now = run.frame * combat.TICK
            match.update(now, run.frame)
            for client, cipher, window, records, notices in peers:
                client.send_frame(run.frame, now)
                packet, _, reliable, _, _, _ = decoded(self, cipher, server.sock.sent[-1][0])
                sent_records = client.in_flight.get(packet.seq, (None, []))[1]
                records.extend(sent_records)
                notices.extend(reliable)
                self.assertTrue(window.accept(packet.seq))
                ack = Packet(client.conn, KIND_CONTROL, step, window.expected, window.bits, ACK)
                if (owner_no_ack and client.player is caster and shot is not None
                        and any(record.op == OP_CREATE and record.entity in shot.created
                                for record in sent_records)):
                    owner_blocked = True
                if client.player is not caster or (not owner_blocked and step >= owner_ack_delay):
                    server._datagram(cipher.seal(ack), client.address, now)
            owner_through = (step + 1) * 16 // owner_frame_ms
            if owner_through > owner_sent:
                caster.take_commands([Command(owner_origin + at, buttons=1 if at == 1 else 0)
                                      for at in range(owner_sent + 1, owner_through + 1)])
                owner_sent = owner_through
            target.take_commands([Command(run.frame)])
            if shot is None and match.combat.rockets:
                shot = match.combat.rockets[0].shot
        self.assertIsNotNone(shot)
        self.assertEqual(shot.definition, 0x0400000000000033)
        birth = caster.body_script.world_frame_anchor
        self.assertEqual(shot.clock_origin, (*birth, owner_frame_ms))

        self.assertEqual(before - helper.health(run, target).total, 120)

        self.assertFalse(match.combat.rockets)
        self.assertNotIn(shot, match.combat.projectiles.shots)
        self.assertFalse(match.combat.projectiles.observed)
        owner_records, owner_notices = peers[0][3:]
        lease = match.combat.projectiles.lease_id(caster, shot.definition, cast_frame)
        ops = [r.op for r in owner_records if r.entity == lease]
        logical_ops = [op for index, op in enumerate(ops) if index == 0 or op != ops[index - 1]]
        self.assertEqual(logical_ops, [OP_CREATE, OP_DESTROY])
        destroyed = {record.entity for record in owner_records if record.op == OP_DESTROY}
        self.assertTrue(set(shot.created).issubset(destroyed))
        owner_release = next(r for r in owner_records if r.entity == lease and r.op == OP_DESTROY)
        if owner_no_ack:
            self.assertIsNone(owner_release.ch2)
        else:
            self.assert_release_frame(owner_release, shot.frame)
        hits = {ordinal: value for ordinal, msg_id, value in owner_notices if msg_id == combat.HIT}.values()
        self.assertEqual([hit['+0x78']['+0x4'] for hit in hits], [40, 80])
        observer_records = peers[1][3]
        projectile_records = [
            record for record in observer_records
            if projectiles.SEEN_FIRST <= record.entity < projectiles.SEEN_FIRST + projectiles.SEEN_SPAN
        ]
        self.assertEqual([r.op for r in projectile_records], [OP_CREATE, OP_DESTROY])
        launches = [r for r in observer_records if r.entity == caster.body and r.ch2 is not None]
        self.assertEqual(len(launches), 1)
        if not owner_no_ack:
            self.assert_release_frame(projectile_records[-1], shot.frame)
        return caster, shot


    def assert_release_frame(self, record, owner_frame):
        self.assertIsNotNone(record.ch2)
        bits = BitReader(record.ch2.getvalue())
        self.assertEqual((bits.bit(), bits.bit()), (0, 1))
        baseline = bits.var_b()
        self.assertEqual(baseline, projectiles.NO_BASELINE)
        self.assertGreater((baseline + bits.var_b()) & 0xFFFFFFFF, owner_frame,
                           'native release must be ahead of this owner epoch, not the world epoch')
