"""Controlled roots replay the actual encrypted server and combat paths, without sockets."""

import copy
import dataclasses
import logging
import sys
import unittest
from pathlib import Path
from struct import unpack_from
from threading import RLock
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ow174.game import combat, messages, npc, replay_values, stats, world
from ow174.game.bits import BitReader, BitWriter
from ow174.game.collision import StaticCollisionProvider, World, flat_world
from ow174.game.content import PRACTICE_RANGE, SOLDIER, Spawn
from ow174.game.environment import SystemEnvironment
from ow174.game.environment_tape import EnvironmentTapeError
from ow174.game.link import ACK, FIN, KIND_FRAME, SYN, LinkCipher, Packet, ReceiveWindow, peek_connection
from ow174.game.match import SEND_EVERY
from ow174.game.server import FIRST_TICK, HANDOFF_SECONDS, GameServer
from ow174.game.server_journal import (
    RecordingServerSession,
    ServerJournalError,
    _ReplaySession,
    replay_server_session,
)
from ow174.lobby.matchmaker import Found, Matchmaker, Ticket
from ow174.paths import DATA_DIR
from test_game_combat import aim
from test_game_repeated_lifecycles import wire_frame

REAPER = 0x02E0000000000002
MAP = dataclasses.replace(
    PRACTICE_RANGE, name="controlled two-team scene", map_guid=0x0800000000FFF901,
    placeables=(), spawns=(Spawn(0, (0.0, 0.0, 0.0), 0.0), Spawn(1, (0.0, 0.0, 5.0), 180.0)),
    team_sizes=(1, 1),
)


class FixedSource:
    def __init__(self):
        self.counter = 0

    def wall_time(self, *, callsite):
        return 100.0

    def wall_time_ns(self, *, callsite):
        self.counter += 1
        return 100_000_000_000 + self.counter

    def monotonic(self, *, callsite):
        return 100.0

    def randbits(self, bits, *, callsite):
        self.counter += 1
        return self.counter & ((1 << bits) - 1)

    def randbelow(self, upper, *, callsite):
        self.counter += 1
        return self.counter % upper

    def token_bytes(self, length, *, callsite):
        self.counter += 1
        return bytes([self.counter % 256]) * length


class SchedulerSource(FixedSource):
    def __init__(self, *reads):
        super().__init__()
        self.reads = iter(reads)

    def scheduler_read(self, callsite):
        expected, value = next(self.reads)
        if callsite != "server.run." + expected:
            raise AssertionError(f"expected {expected}, got {callsite}")
        return value

    def wall_time(self, *, callsite):
        if callsite.startswith("server.run."):
            return self.scheduler_read(callsite)
        return super().wall_time(callsite=callsite)

    def monotonic(self, *, callsite):
        return self.scheduler_read(callsite)


def wall_world(*, depth=2, height=5):
    return World([
        (-20, 0, -20, -20, 0, 20, 20, 0, 20), (-20, 0, -20, 20, 0, 20, 20, 0, -20),
        (-10, 0, depth, 10, 0, depth, 10, height, depth),
        (-10, 0, depth, 10, height, depth, -10, height, depth),
    ], name="controlled floor and wall")


class Peer:
    def __init__(self, session, handoff, sent, address):
        self.session, self.handoff, self.sent, self.address = session, handoff, sent, address
        self.cipher = LinkCipher(handoff.key_out, handoff.key_in)
        self.window = ReceiveWindow()
        self.seq = self.reliable = self.cursor = 0
        self.last_raw = None

    def drain(self, *, lose=False):
        for data, address in self.sent[self.cursor:]:
            if peek_connection(data) != self.handoff.conn:
                continue
            if not lose:
                packet = self.cipher.open(data)
                assert packet is not None and address == self.address, (len(data), address, self.address)
                self.window.accept(packet.seq)
        self.cursor = len(self.sent)

    def hit_notices(self, first_packet):
        return self.combat_notices(first_packet, combat.HIT)

    def combat_notices(self, first_packet, identifier):
        notices = {}
        for data, address in self.sent[first_packet:]:
            if peek_connection(data) != self.handoff.conn:
                continue
            assert address == self.address
            packet = self.cipher.open(data)
            assert packet is not None
            if packet.kind != KIND_FRAME:
                continue
            reader = BitReader(packet.payload)
            reader.var_a()
            reliable, _ = messages.read_channel(reader)
            notices.update((seq, value) for seq, msg_id, value in reliable if msg_id == identifier)
        return list(notices.values())

    def health_updates(self, first_packet, body):
        updates = set()
        for data, address in self.sent[first_packet:]:
            if peek_connection(data) != self.handoff.conn:
                continue
            assert address == self.address
            packet = self.cipher.open(data)
            assert packet is not None
            if packet.kind == KIND_FRAME:
                _, records = wire_frame(packet)
                for record, fields in records:
                    if record.entity == body and 51 in fields:
                        _, maximum, current = unpack_from("<Iff", fields[51][0])
                        updates.add((record.op, maximum, current))
        return updates

    def send(self, *, flags=ACK, payload=b"", now=100.0, address=None):
        packet = Packet(self.handoff.conn, KIND_FRAME if payload else 0, self.seq,
                        self.window.expected, self.window.bits, flags, payload)
        self.seq += 1
        self.last_raw = self.cipher.seal(packet)
        self.session.datagram(self.last_raw, self.address if address is None else address, now)
        self.drain()

    def message(self, identifier, value, now=100.0):
        payload = BitWriter()
        messages.write_channel(payload, [(self.reliable, messages.encode(identifier, value))])
        self.reliable += 1
        self.send(payload=payload.getvalue(), now=now)

    def commands(self, first, count, buttons, *, forward=0, yaw=0, pitch=0, now=100.8):
        if count > 32:
            self.commands(first, 32, buttons, forward=forward, yaw=yaw, pitch=pitch, now=now)
            return self.commands(first + 32, count - 32, buttons, forward=forward, yaw=yaw, pitch=pitch, now=now)
        body, payload = BitWriter(), BitWriter()
        body.bits(0, 24)
        body.bits(count, 8)
        body.bits(first, 32)
        body.bit(bool(forward))
        if forward:
            body.bit(0)  # arbitrary throttles
            body.bit(0)  # unchanged right
            body.bit(1)  # forward follows
            body.signed(forward, 8)
        body.signed(yaw, 16)
        body.signed(pitch, 16)
        body.bits(buttons, 24)
        body.bits(0, 24)
        for _ in range(count - 1):
            body.bit(0)
            body.bit(0)
        messages.write_channel(payload)
        payload.bit(1)
        payload.bits(body.count, 32)
        payload.append(body)
        self.send(payload=payload.getvalue(), now=now)


def pick(entity, hero):
    return {"+0x78": {"+0x0": entity},
            "+0x80": bytes.fromhex("0000000001c15d04") + hero.to_bytes(8, "little") + bytes(8),
            "+0xA8": 0x0240000000001361, "+0xB0": 3}


def acknowledge_ownership(session, peers):
    for index in range(SEND_EVERY):
        now = 100.5 + index * 0.016
        session.tick(now)
        for peer in peers:
            peer.drain()
            peer.send(now=now)


class ServerJournalTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        logging.getLogger("ow174.script").setLevel(logging.ERROR)

    def make(self, world=None, *, source=None, skin_of=None, sendto=None, verify_stored_state=False):
        sent = []

        def send(data, address):
            sent.append((data, address))
            return len(data)

        args = {"environment": FixedSource() if source is None else source,
                "collision_provider": StaticCollisionProvider(flat_world(0.0) if world is None else world),
                "sendto": send if sendto is None else sendto,
                "verify_stored_state": verify_stored_state}
        if skin_of is not None:
            args["skin_of"] = skin_of
        return RecordingServerSession(**args), sent

    def connected(self, session, sent, *, two=True, first_hero=SOLDIER, second_hero=SOLDIER, ally=False, scene=MAP):
        entries = [(1, "Alpha", first_hero, 0, False), (2, "Beta", second_hero, 1, False)]
        entries = entries if two else entries[:1]
        if ally:
            entries.append((3, "Charlie", SOLDIER, 0, False))
            scene = dataclasses.replace(scene, team_sizes=(2, 1), free_for_all=False)
        handoffs = session.create_match(scene, entries)
        peers = [Peer(session, h, sent, ("127.0.0.1", 42000 + i)) for i, h in enumerate(handoffs)]
        for peer in peers:
            peer.send(flags=SYN)
            peer.send()
            peer.message(21601, {})
        session.report_callbacks()
        acknowledge_ownership(session, peers)
        session.tick(100.8)
        players = session.server.matches[0].players
        self.assertTrue(all(player.spawned for player in players))
        for peer, player, entry in zip(peers, players, entries[:len(players)], strict=True):
            peer.message(21616, pick(player.entity, entry[2]), now=100.8)
            self.assertIsNotNone(player.body_script)
        # Actual create/full ACK processing goes through the raw datagram router.
        for tick in range(8):
            session.tick(100.816 + tick * 0.016)
            for peer in peers:
                peer.drain()
                peer.send(now=100.816 + tick * 0.016)
        return peers, players

    def verify(self, session):
        document = session.finish()
        replay = _ReplaySession(document, session.registry.resolve)
        result = replay.run()
        self.assertEqual(result["status"], "matched_controlled_roots_and_wire")
        self.assertFalse(result["complete_battle_replay"])
        self.assertFalse(result["full_state_verified"])
        return document, replay, result

    def test_factory_is_per_match_and_defaults_remain_optional(self):
        provider = StaticCollisionProvider(flat_world(0.0))
        factory = mock.Mock(return_value=provider)
        server = GameServer(environment=FixedSource(), collision_provider_factory=factory)
        server.create_match(MAP, [])
        server.create_match(MAP, [])
        self.assertEqual(factory.call_count, 2)
        self.assertTrue(all(match.collision_world is provider.state.world for match in server.matches))

    def test_encrypted_input_ack_loss_duplicate_and_wall_replay_without_socket_or_game(self):
        with mock.patch("socket.socket", side_effect=AssertionError("must not create sockets")):
            session, sent = self.make(wall_world())
            peers, players = self.connected(session, sent)
            shooter, victim = players
            peers[0].commands(shooter.body_script.frame + 1, 50, 1, forward=127)
            self.assertLess(shooter.mover.position[2], 2.0)
            health = session.server.matches[0].combat.vitals[victim.body].total
            self.assertEqual(health, 200.0)
            newest = shooter.commands.last_frame
            session.datagram(peers[0].last_raw, peers[0].address, 101.0)  # duplicate wire seq
            self.assertEqual(shooter.commands.last_frame, newest)
            for index in range(6):
                session.tick(101.016 + index * 0.016)
                peers[0].drain(lose=True)
                peers[1].drain()
            peers[0].send(now=101.2)  # loss ACK triggers ordinary reliable retransmission
            session.tick(101.216, missed=3)
            document, replay, result = self.verify(session)
        self.assertGreater(result["send_attempts"], 10)
        copied = replay.server.matches[0].players
        self.assertEqual(copied[0].mover.position, shooter.mover.position)
        self.assertEqual(replay.server.matches[0].combat.vitals[copied[1].body].total, health)
        self.assertEqual(replay.server.tick, session.server.tick)
        self.assertEqual(document["scope"], "controlled-roots-and-wire-only")

    def test_genji_and_soldier_trade_damage_and_replay_both_hit_notices(self):
        session, sent = self.make()
        peers, players = self.connected(session, sent, first_hero=0x02E0000000000029)
        genji, soldier = players
        fight = genji.match.combat
        first_packet = len(sent)
        peers[0].commands(genji.body_script.frame + 1, 16, 2, now=100.944)
        peers[1].commands(soldier.body_script.frame + 1, 1, 1, yaw=-32768, pitch=900, now=100.944)
        for tick in range(12):
            now = 100.96 + tick * 0.016
            session.tick(now)
            for peer in peers:
                peer.drain()
                peer.send(now=now)

        expected_health = [160, 113]
        self.assertEqual([fight.vitals[player.body].total for player in players], expected_health)
        expected_hits = ((3, 29, 0), (1, 40, combat.CRITICAL))
        for peer, target, (count, damage, flags) in zip(peers, reversed(players), expected_hits, strict=True):
            hit = {"+0x0": {"+0x0": target.body}, "+0x4": damage, "+0x8": flags}
            self.assertEqual([notice["+0x78"] for notice in peer.hit_notices(first_packet)], [hit] * count)

        _, replay, _ = self.verify(session)
        match = replay.server.matches[0]
        self.assertEqual([match.combat.vitals[player.body].total for player in match.players], expected_health)

    def test_reaper_wraith_blocks_genji_shuriken_until_cancel_in_wire_replay(self):
        session, sent = self.make()
        peers, players = self.connected(session, sent, first_hero=0x02E0000000000029,
                                        second_hero=REAPER)
        genji, reaper = players
        genji_peer, reaper_peer = peers
        fight = genji.match.combat
        wraith = next(i for i in reaper.body_script.component.instances.values() if i.graph.index == 0x0020)
        weapon = next(i for i in genji.body_script.component.instances.values() if i.graph.index == 0x0304)
        now = 100.944

        def step(count):
            nonlocal now
            for _ in range(count):
                now += 0.016
                session.tick(now)
                for peer in peers:
                    peer.drain()
                    peer.send(now=now)

        def wraith_input(button, count=1):
            reaper_peer.commands(reaper.body_script.frame + 1, count, button, yaw=-32768, now=now)

        first_packet = len(sent)
        wraith_input(8)
        self.assertTrue(wraith.state(5).active)
        genji_peer.commands(genji.body_script.frame + 1, 30, 1, now=now)
        wraith_input(0, 30)
        step(30)
        self.assertTrue(wraith.state(5).active)
        self.assertEqual(fight.vitals[reaper.body].total, 250)
        self.assertEqual(weapon.vars[53].value(), 27)
        self.assertEqual(genji.stats.values.get(stats.HERO_DAMAGE, 0), 0)
        self.assertFalse(genji_peer.hit_notices(first_packet))

        wraith_input(8)
        self.assertFalse(wraith.state(5).active)
        wraith_input(0, 30)
        genji_peer.commands(genji.body_script.frame + 1, 35, 0, now=now)
        step(30)
        cooldown = wraith.vars[61].value()
        self.assertGreater(cooldown, 0)
        activations = wraith.state(5).counter
        wraith_input(8)
        self.assertFalse(wraith.state(5).active)
        self.assertEqual(wraith.state(5).counter, activations)

        genji_peer.commands(genji.body_script.frame + 1, 25, 1, now=now)
        step(12)
        self.assertEqual(fight.vitals[reaper.body].total, 163)
        self.assertEqual(weapon.vars[53].value(), 24)
        self.assertEqual(genji.stats.values[stats.HERO_DAMAGE], 87)
        expected_hit = {"+0x0": {"+0x0": reaper.body}, "+0x4": 29, "+0x8": 0}
        self.assertEqual([hit["+0x78"] for hit in genji_peer.hit_notices(first_packet)], [expected_hit] * 3)

        _, replay, _ = self.verify(session)
        copied_match = replay.server.matches[0]
        copied_genji, copied_reaper = copied_match.players
        copied_wraith = next(i for i in copied_reaper.body_script.component.instances.values()
                             if i.graph.index == 0x0020)
        self.assertEqual(copied_match.combat.vitals[copied_reaper.body].total, 163)
        self.assertEqual(copied_genji.stats.values[stats.HERO_DAMAGE], 87)
        self.assertEqual(copied_wraith.state(5).counter, activations)
        self.assertFalse(copied_wraith.state(5).active)
        self.assertEqual(copied_wraith.vars[61].value(), wraith.vars[61].value())

    def test_sigma_grasp_absorbs_genji_shuriken_but_not_melee_in_wire_replay(self):
        session, sent = self.make()
        peers, players = self.connected(session, sent, first_hero=0x02E0000000000029,
                                        second_hero=0x02E000000000023B)
        genji, sigma = players
        genji_peer, sigma_peer = peers
        fight = genji.match.combat
        grasp = next(i for i in sigma.body_script.component.instances.values() if i.graph.index == 0x1D02)
        weapon = next(i for i in genji.body_script.component.instances.values() if i.graph.index == 0x0304)
        first_packet = len(sent)
        now = 100.944

        def step(count):
            nonlocal now
            for _ in range(count):
                now += 0.016
                session.tick(now)
                for peer in peers:
                    peer.drain()
                    peer.send(now=now)

        sigma_peer.commands(sigma.body_script.frame + 1, 1, 8, yaw=-32768, now=now)
        self.assertTrue(grasp.state(1).active)
        x, y, z = sigma.mover.position
        yaw, pitch = aim(fight._eye(genji), (x, y + 1, z))
        genji_peer.commands(genji.body_script.frame + 1, 32, 1, yaw=yaw, pitch=pitch, now=now)
        sigma_peer.commands(sigma.body_script.frame + 1, 32, 0, yaw=-32768, now=now)
        step(30)
        self.assertEqual(grasp.vars[21312].value(), 87)
        self.assertEqual(fight.vitals[sigma.body].total, 400)
        self.assertEqual(weapon.vars[53].value(), 27)
        self.assertEqual(genji.stats.values.get(stats.HERO_DAMAGE, 0), 0)

        genji_peer.commands(genji.body_script.frame + 1, 45, 0, forward=127, now=now)
        sigma_peer.commands(sigma.body_script.frame + 1, 45, 0, yaw=-32768, now=now)
        genji_peer.commands(genji.body_script.frame + 1, 32, 0x800, now=now)
        self.assertTrue(grasp.state(1).active)
        self.assertEqual(grasp.vars[21312].value(), 87)
        self.assertEqual(fight.vitals[sigma.body].total, 370)
        self.assertEqual(weapon.vars[53].value(), 27)
        self.assertEqual(genji.stats.values[stats.HERO_DAMAGE], 30)
        self.assertFalse(fight.vitals[sigma.body].extra)
        step(12)

        sigma_peer.commands(sigma.body_script.frame + 1, 80, 0, yaw=-32768, now=now)
        step(12)
        self.assertFalse(grasp.state(1).active)
        pool, = fight.vitals[sigma.body].extra.values()
        self.assertEqual(pool.identifier, 0x5847)
        self.assertAlmostEqual(pool.now, 52.2, places=4)
        self.assertAlmostEqual(fight.vitals[sigma.body].total, 422.2, places=4)

        hit, = genji_peer.hit_notices(first_packet)
        self.assertEqual(hit["+0x78"], {"+0x0": {"+0x0": sigma.body}, "+0x4": 30, "+0x8": 0})
        self.assertFalse(sigma_peer.hit_notices(first_packet))
        _, replay, _ = self.verify(session)
        copied_match = replay.server.matches[0]
        copied_genji, copied_sigma = copied_match.players
        copied_grasp = next(i for i in copied_sigma.body_script.component.instances.values()
                            if i.graph.index == 0x1D02)
        self.assertFalse(copied_grasp.state(1).active)
        self.assertEqual(copied_grasp.vars[21312].value(), 87)
        copied_vitals = copied_match.combat.vitals[copied_sigma.body]
        copied_pool, = copied_vitals.extra.values()
        self.assertEqual(copied_pool.identifier, 0x5847)
        self.assertAlmostEqual(copied_pool.now, 52.2, places=4)
        self.assertAlmostEqual(copied_vitals.total, 422.2, places=4)
        self.assertEqual(copied_genji.stats.values[stats.HERO_DAMAGE], 30)

    def test_genji_melee_breaks_zarya_barrier_without_spill_in_wire_replay(self):
        session, sent = self.make()
        peers, (genji, zarya) = self.connected(session, sent, first_hero=0x02E0000000000029,
                                              second_hero=0x02E0000000000068)
        genji_peer, zarya_peer = peers
        fight = genji.match.combat
        now = 100.944

        def command(count, buttons=0, *, forward=0):
            genji_peer.commands(genji.body_script.frame + 1, count, buttons, forward=forward, now=now)

        def step(count):
            nonlocal now
            for _ in range(count):
                now += 0.016
                session.tick(now)
            for peer in peers:
                peer.drain()
                peer.send(now=now)

        command(45, forward=127)
        first_packet = len(sent)
        zarya_peer.commands(zarya.body_script.frame + 1, 1, 8, yaw=-32768, now=now)
        bubble, = fight.barriers.values()
        self.assertIsNone(bubble.recipient)
        self.assertEqual(bubble.vitals.total, 200)
        energy = zarya.body_script.component.entity_var(87)
        weapon = next(i for i in genji.body_script.component.instances.values() if i.graph.index == 0x0304)
        command(49, 2)
        command(6)
        step(16)
        self.assertEqual(bubble.vitals.total, 26)
        self.assertAlmostEqual(energy.value(), 34.8, places=4)
        self.assertEqual(weapon.vars[53].value(), 24)

        command(1, 0x800)
        command(36)
        step(16)
        self.assertTrue(bubble.retired)
        self.assertFalse(fight.barriers)
        self.assertEqual(bubble.vitals.total, 0)
        self.assertEqual(zarya.body_script.component.entity_var(253).value(), 400)
        self.assertAlmostEqual(energy.value(), 40, places=4)
        self.assertFalse(genji_peer.hit_notices(first_packet))
        self.assertEqual(genji.stats.values.get(stats.HERO_DAMAGE, 0), 0)
        graph = next(i for i in zarya.body_script.component.instances.values() if i.graph.index == 0x0244)
        self.assertGreater(graph.vars[61].value(), 0)

        command(32)
        command(1, 0x800)
        command(24)
        step(12)
        self.assertEqual(fight.vitals[zarya.body].total, 370)
        self.assertEqual(genji.stats.values[stats.HERO_DAMAGE], 30)
        self.assertEqual(weapon.vars[53].value(), 24)
        hit, = genji_peer.hit_notices(first_packet)
        self.assertEqual(hit["+0x78"], {"+0x0": {"+0x0": zarya.body}, "+0x4": 30, "+0x8": 0})
        self.assertFalse(zarya_peer.hit_notices(first_packet))

        _, replay, _ = self.verify(session)
        copied, = replay.server.matches
        copied_genji, copied_zarya = copied.players
        self.assertFalse(copied.combat.barriers)
        self.assertEqual(copied.combat.vitals[copied_zarya.body].total, 370)
        self.assertEqual(copied_genji.stats.values[stats.HERO_DAMAGE], 30)
        self.assertEqual(copied_zarya.body_script.component.entity_var(87).value(), energy.value())

    def test_genji_and_moira_trade_damage_and_replay_grasp_healing(self):
        session, sent = self.make()
        peers, players = self.connected(session, sent, first_hero=0x02E0000000000029,
                                        second_hero=0x02E00000000001A2)
        genji, moira = players
        fight = genji.match.combat
        self.assertNotEqual(genji.team, moira.team)
        first_packet = len(sent)
        peers[0].commands(genji.body_script.frame + 1, 16, 2, now=100.944)
        for tick in range(12):
            now = 100.96 + tick * 0.016
            session.tick(now)
            for peer in peers:
                peer.drain()
                peer.send(now=now)
        self.assertEqual(fight.vitals[moira.body].total, 113)
        self.assertEqual(genji.body_script.component.entity_var(342).value(), 87)

        peers[0].commands(genji.body_script.frame + 1, 5, 16, now=now)
        deflect = next(i for i in genji.body_script.component.instances.values() if i.graph.index == 0x03A3)
        self.assertTrue(deflect.state(10).active)
        peers[1].commands(moira.body_script.frame + 1, 21, 2, yaw=-32768, pitch=500, now=now)
        grasp = next(i for i in moira.body_script.component.instances.values() if i.graph.index == 0x0F76)
        self.assertTrue(grasp.state(22).active)
        self.assertTrue(grasp.state(45).active)
        self.assertTrue(deflect.state(10).active)
        self.assertAlmostEqual(fight.vitals[genji.body].total, 184)
        self.assertAlmostEqual(fight.vitals[moira.body].total, 120.68)
        self.assertAlmostEqual(moira.stats.values[stats.HEALING], 7.68)

        peers[1].commands(moira.body_script.frame + 1, 6, 0, yaw=-32768, pitch=500, now=now)
        for _ in range(12):
            now += 0.016
            session.tick(now)
            for peer in peers:
                peer.drain()
                peer.send(now=now)
        self.assertFalse(grasp.state(22).active)
        self.assertFalse(grasp.state(45).active)
        self.assertAlmostEqual(fight.vitals[genji.body].total, 184)
        self.assertAlmostEqual(fight.vitals[moira.body].total, 120.68)
        for peer, target, count, damage in ((peers[0], moira, 3, 29), (peers[1], genji, 20, 1)):
            hit = {"+0x0": {"+0x0": target.body}, "+0x4": damage, "+0x8": 0}
            self.assertEqual([notice["+0x78"] for notice in peer.hit_notices(first_packet)], [hit] * count)

        _, replay, _ = self.verify(session)
        match = replay.server.matches[0]
        copied_genji, copied_moira = match.players
        self.assertAlmostEqual(match.combat.vitals[copied_genji.body].total, 184)
        self.assertAlmostEqual(match.combat.vitals[copied_moira.body].total, 120.68)
        self.assertAlmostEqual(copied_moira.stats.values[stats.HEALING], 7.68)
        self.assertEqual(copied_genji.body_script.component.entity_var(342).value(), 87)

    def test_genji_and_winston_trade_damage_and_replay_tesla_release(self):
        session, sent = self.make()
        peers, players = self.connected(session, sent, first_hero=0x02E0000000000029,
                                        second_hero=0x02E0000000000009)
        genji, winston = players
        fight = genji.match.combat
        first_packet = len(sent)
        peers[0].commands(genji.body_script.frame + 1, 16, 2, now=100.944)
        for tick in range(12):
            now = 100.96 + tick * 0.016
            session.tick(now)
            for peer in peers:
                peer.drain()
                peer.send(now=now)
        self.assertEqual(fight.vitals[winston.body].total, 428)
        self.assertEqual(genji.body_script.component.entity_var(342).value(), 72)

        peers[0].commands(genji.body_script.frame + 1, 5, 16, now=now)
        peers[1].commands(winston.body_script.frame + 1, 20, 1, yaw=-32768, now=now)
        tesla = next(i for i in winston.body_script.component.instances.values() if i.graph.index == 0x00FE)
        deflect = next(i for i in genji.body_script.component.instances.values() if i.graph.index == 0x03A3)
        self.assertTrue(tesla.state(13).active)
        self.assertTrue(deflect.state(10).active)
        self.assertAlmostEqual(fight.vitals[genji.body].total, 180.8)
        ammo = tesla.vars[53].value()
        self.assertLess(ammo, 100)

        peers[1].commands(winston.body_script.frame + 1, 6, 0, yaw=-32768, now=now)
        for _ in range(12):
            now += 0.016
            session.tick(now)
            for peer in peers:
                peer.drain()
                peer.send(now=now)
        self.assertFalse(tesla.state(13).active)
        self.assertFalse(tesla.vars[468].value())
        self.assertEqual(tesla.vars[53].value(), ammo)
        self.assertAlmostEqual(fight.vitals[genji.body].total, 180.8)
        for peer, target, count, damage in ((peers[0], winston, 3, 24), (peers[1], genji, 20, 1)):
            hit = {"+0x0": {"+0x0": target.body}, "+0x4": damage, "+0x8": 0}
            self.assertEqual([notice["+0x78"] for notice in peer.hit_notices(first_packet)], [hit] * count)

        _, replay, _ = self.verify(session)
        match = replay.server.matches[0]
        copied_genji, copied_winston = match.players
        self.assertAlmostEqual(match.combat.vitals[copied_genji.body].total, 180.8)
        self.assertEqual(match.combat.vitals[copied_winston.body].total, 428)
        copied_tesla = next(i for i in copied_winston.body_script.component.instances.values()
                            if i.graph.index == 0x00FE)
        self.assertEqual(copied_tesla.vars[53].value(), ammo)
        self.assertFalse(copied_tesla.vars[468].value())

    def test_mei_freeze_interrupts_genji_deflect_and_replays_thaw(self):
        session, sent = self.make()
        peers, players = self.connected(session, sent, first_hero=0x02E0000000000029,
                                        second_hero=0x02E00000000000DD)
        genji, mei = players
        fight = genji.match.combat
        first_packet = len(sent)
        now = 100.944

        def step(genji_buttons=0, mei_buttons=0, forward=0):
            nonlocal now
            now += combat.TICK
            session.tick(now)
            peers[0].commands(genji.body_script.frame + 1, 1, genji_buttons, forward=forward, now=now)
            peers[1].commands(mei.body_script.frame + 1, 1, mei_buttons, yaw=-32768, pitch=500, now=now)

        deflect = next(i for i in genji.body_script.component.instances.values() if i.graph.index == 0x03A3)
        for frame in range(130):
            step(genji_buttons=16 if frame == 35 else 0, mei_buttons=1)
            if frame == 35:
                self.assertTrue(deflect.state(10).active)
            if fight.is_stunned(genji):
                break
        self.assertTrue(fight.is_stunned(genji))
        self.assertEqual(fight.vitals[genji.body].total, 117.5)
        self.assertFalse(deflect.state(10).active)
        self.assertEqual(deflect.state(10).counter, 1)
        deadline = fight.frost_hits[genji.body].frozen_until
        self.assertEqual(deadline - fight.tick, combat.FROST_FREEZE_TICKS)
        position = tuple(genji.mover.position)
        release_at = fight.tick + 10
        while fight.tick < deadline - 1:
            step(genji_buttons=1 | 8 | 16 | 0x40, mei_buttons=1 if fight.tick < release_at else 0,
                 forward=127)
            self.assertEqual(fight.frost_hits[genji.body].frozen_until, deadline)
            self.assertEqual(tuple(genji.mover.position), position)
        self.assertTrue(fight.is_stunned(genji))
        self.assertEqual(genji.stats.values.get(stats.HERO_DAMAGE, 0), 0)

        step(genji_buttons=16)
        self.assertEqual(fight.tick, deadline)
        self.assertFalse(fight.is_stunned(genji))
        self.assertNotIn(genji.body, fight.frost_hits)
        self.assertFalse(deflect.state(10).active)
        self.assertGreater(deflect.state(0).var.value(), 0)
        charge_var = genji.body_script.component.entity_var(342)
        charge_at_thaw = charge_var.value()
        step(genji_buttons=1)
        for _ in range(50):
            step()

        self.assertEqual(fight.vitals[mei.body].total, 163)
        self.assertEqual(genji.stats.values[stats.HERO_DAMAGE], 87)
        charge = charge_var.value()
        self.assertIn(charge - charge_at_thaw, (87, 92))  # At most one 5-point passive tick.
        genji_hits = [notice["+0x78"] for notice in peers[0].hit_notices(first_packet)]
        self.assertEqual(genji_hits, [{"+0x0": {"+0x0": mei.body}, "+0x4": 29, "+0x8": 0}] * 3)
        mei_hits = [notice["+0x78"] for notice in peers[1].hit_notices(first_packet)]
        self.assertGreaterEqual(len(mei_hits), 30)
        self.assertEqual(mei_hits, [{"+0x0": {"+0x0": genji.body}, "+0x4": 3, "+0x8": 0}] * len(mei_hits))
        genji_health = fight.vitals[genji.body].total
        self.assertEqual(genji_health, 200 - 2.75 * len(mei_hits))

        _, replay, _ = self.verify(session)
        copied_genji, copied_mei = replay.server.matches[0].players
        copied_fight = copied_genji.match.combat
        self.assertEqual(copied_fight.vitals[copied_genji.body].total, genji_health)
        self.assertEqual(copied_fight.vitals[copied_mei.body].total, 163)
        self.assertNotIn(copied_genji.body, copied_fight.frost_hits)
        self.assertEqual(copied_genji.body_script.component.entity_var(342).value(), charge)
        copied_deflect = next(i for i in copied_genji.body_script.component.instances.values()
                              if i.graph.index == 0x03A3)
        self.assertEqual(copied_deflect.state(10).counter, 1)
        self.assertFalse(copied_deflect.state(10).active)

    def test_ana_sleep_dart_replays_reflection_rear_hit_and_wake(self):
        for frontal in (True, False):
            with self.subTest(frontal=frontal):
                session, sent = self.make()
                peers, players = self.connected(session, sent, first_hero=0x02E0000000000029,
                                                second_hero=0x02E000000000013B)
                genji, ana = players
                fight = genji.match.combat
                first_packet = len(sent)
                now = 100.944

                def step(genji_buttons=0, ana_buttons=0, *, facing=0, forward=0):
                    nonlocal now
                    now += combat.TICK
                    session.tick(now)
                    peers[0].commands(genji.body_script.frame + 1, 1, genji_buttons, yaw=facing,
                                      forward=forward if not frontal else 0, now=now)
                    peers[1].commands(ana.body_script.frame + 1, 1, ana_buttons, yaw=-32768, pitch=500,
                                      forward=forward if frontal else 0, now=now)

                target = ana if frontal else genji
                for frame in range(70):
                    step(genji_buttons=16 if frame == 0 else 0, ana_buttons=8 if frame == 5 else 0,
                         facing=0 if frontal else -32768)
                    if fight.is_asleep(target):
                        break
                self.assertTrue(fight.is_asleep(target))
                vitals = fight.vitals[target.body]
                self.assertEqual(vitals.total, 195)
                self.assertEqual(vitals.sleep_until - fight.tick, combat.SLEEP_TICKS)
                deflect = next(i for i in genji.body_script.component.instances.values() if i.graph.index == 0x03A3)
                self.assertEqual(deflect.state(10).active, frontal)
                position = tuple(target.mover.position)

                if frontal:
                    floor = vitals.sleep_wake_floor
                    step(genji_buttons=16)
                    for _ in range(15):
                        step(ana_buttons=1, forward=127)
                    self.assertFalse(deflect.state(10).active)
                    step(genji_buttons=1, ana_buttons=1, forward=127)
                    while fight.tick < floor - 1:
                        step(ana_buttons=1, forward=127)
                        self.assertEqual(tuple(target.mover.position), position)
                    self.assertEqual(vitals.sleep_until, floor)
                else:
                    deadline = vitals.sleep_until
                    while fight.tick < deadline - 1:
                        step(genji_buttons=1 | 8 | 16 | 0x40, forward=127)
                        self.assertEqual(tuple(target.mover.position), position)
                    self.assertEqual(vitals.sleep_until, deadline)
                self.assertTrue(fight.is_asleep(target))
                step()
                self.assertFalse(fight.is_asleep(target))
                self.assertEqual(vitals.sleep_wake_floor, 0)
                self.assertFalse(deflect.state(10).active)
                if not frontal:
                    step(genji_buttons=1)
                for _ in range(40):
                    step()

                genji_hp, ana_hp = (200, 108) if frontal else (195, 113)
                self.assertEqual(fight.vitals[genji.body].total, genji_hp)
                self.assertEqual(fight.vitals[ana.body].total, ana_hp)
                damage = [5, 29, 29, 29] if frontal else [29, 29, 29]
                self.assertEqual(genji.stats.values[stats.HERO_DAMAGE], sum(damage))
                for peer, victim, amounts in ((peers[0], ana, damage), (peers[1], genji, [] if frontal else [5])):
                    hits = [notice["+0x78"] for notice in peer.hit_notices(first_packet)]
                    self.assertEqual(hits, [
                        {"+0x0": {"+0x0": victim.body}, "+0x4": amount, "+0x8": 0} for amount in amounts
                    ])
                _, replay, _ = self.verify(session)
                copied_genji, copied_ana = replay.server.matches[0].players
                copied_fight = copied_genji.match.combat
                self.assertEqual(copied_fight.vitals[copied_genji.body].total, genji_hp)
                self.assertEqual(copied_fight.vitals[copied_ana.body].total, ana_hp)
                self.assertFalse(copied_fight.is_asleep(copied_ana if frontal else copied_genji))
                self.assertEqual(copied_genji.stats.values[stats.HERO_DAMAGE], sum(damage))

    def test_genji_double_jump_with_primary_and_landing_reset_in_wire_replay(self):
        session, sent = self.make()
        peers, (genji, soldier) = self.connected(session, sent, first_hero=0x02E0000000000029)
        fight = genji.match.combat
        now = 100.944
        first_packet = len(sent)

        def step(buttons=0, pitch=0):
            nonlocal now
            now += combat.TICK
            session.tick(now)
            peers[0].commands(genji.body_script.frame + 1, 1, buttons, pitch=pitch, now=now)
            peers[1].send(now=now)

        for frame in range(20):
            step(64 if frame < 4 else 0)
        self.assertTrue(genji.mover.airborne)
        self.assertFalse(genji.genji_mobility.air_jump_used)
        x, y, z = soldier.mover.position
        before = genji.mover.position[1]
        for frame in range(24):
            _, pitch = aim(fight._eye(genji), (x, y + .85, z))
            step(65 if frame == 0 else 0, pitch)
            if frame == 0:
                self.assertGreater(genji.mover.position[1], before)
                self.assertGreater(genji.mover.velocity[1], 5)
        self.assertTrue(genji.genji_mobility.air_jump_used)
        self.assertTrue(genji.mover.airborne)
        previous_velocity = genji.mover.velocity[1]
        step(64)
        self.assertLess(genji.mover.velocity[1], previous_velocity)
        for _ in range(120):
            step()
            if not genji.mover.airborne and not genji.genji_mobility.air_jump_used:
                break
        self.assertFalse(genji.mover.airborne)
        self.assertFalse(genji.genji_mobility.air_jump_used)
        for frame in range(20):
            step(64 if frame == 0 else 0)
        step(64)
        self.assertTrue(genji.genji_mobility.air_jump_used)
        self.assertGreater(genji.mover.velocity[1], 5)

        self.assertEqual(fight.vitals[soldier.body].total, 113)
        hits = [notice["+0x78"] for notice in peers[0].hit_notices(first_packet)]
        self.assertEqual(hits, [{"+0x0": {"+0x0": soldier.body}, "+0x4": 29, "+0x8": 0}] * 3)
        _, replay, _ = self.verify(session)
        match = replay.server.matches[0]
        copied_genji, copied_soldier = match.players
        self.assertEqual(match.combat.vitals[copied_soldier.body].total, 113)
        self.assertEqual(copied_genji.mover.position, genji.mover.position)
        self.assertEqual(copied_genji.mover.velocity, genji.mover.velocity)
        self.assertEqual(copied_genji.genji_mobility, genji.genji_mobility)

    def test_swift_strike_takes_over_genji_climb_in_wire_replay(self):
        for away_from_wall in (True, False):
            with self.subTest(away_from_wall=away_from_wall):
                session, sent = self.make(wall_world(depth=-.8, height=10))
                peers, (genji, enemy) = self.connected(session, sent, first_hero=0x02E0000000000029)
                fight = genji.match.combat
                mobility = genji.genji_mobility
                now = 100.944

                def step(buttons, yaw):
                    nonlocal now
                    now += combat.TICK
                    session.tick(now)
                    peers[0].commands(genji.body_script.frame + 1, 1, buttons, forward=127, yaw=yaw, now=now)
                    peers[1].send(now=now)

                for _ in range(20):
                    step(0x40, -32768)
                    if mobility.climbing and genji.mover.position[1] >= .7:
                        break
                self.assertTrue(mobility.climbing)
                strike = next(i for i in genji.body_script.component.instances.values() if i.graph.index == 0x01DB)
                first_packet = len(sent)
                step(0x40 | 8, 0 if away_from_wall else -32768)
                strike_height = genji.mover.position[1]
                self.assertEqual(strike.state(1).counter, 1)
                for _ in range(30):
                    step(0x40, -32768)
                    self.assertFalse(mobility.climbing)
                    if away_from_wall and strike.state(1).active:
                        self.assertEqual(genji.mover.position[1], strike_height)
                    if not away_from_wall:
                        self.assertGreater(genji.mover.position[2], -.8)
                self.assertTrue(mobility.climb_spent)
                self.assertFalse(strike.state(1).active)
                self.assertEqual(strike.state(1).counter, 1)
                self.assertGreater(strike.state(0).var.value(), 0)
                expected_hits = [{"+0x0": {"+0x0": enemy.body}, "+0x4": 50, "+0x8": 0}] if away_from_wall else []
                self.assertEqual([notice["+0x78"] for notice in peers[0].hit_notices(first_packet)], expected_hits)
                health = 150 if away_from_wall else 200
                self.assertEqual(fight.vitals[enemy.body].total, health)

                _, replay, _ = self.verify(session)
                copied, = replay.server.matches
                self.assertEqual(copied.players[0].genji_mobility, mobility)
                self.assertEqual(copied.players[0].mover.position, genji.mover.position)
                self.assertEqual(copied.combat.vitals[enemy.body].total, health)

    def test_flashbang_interrupts_genji_climb_and_landing_restores_it_in_wire_replay(self):
        session, sent = self.make(wall_world(depth=-.8, height=10))
        peers, (genji, caster) = self.connected(
            session, sent, first_hero=0x02E0000000000029, second_hero=0x02E0000000000042,
        )
        fight = genji.match.combat
        mobility = genji.genji_mobility
        now = 100.944

        def step(*, cast=False, held=True):
            nonlocal now
            now += combat.TICK
            session.tick(now)
            peers[0].commands(genji.body_script.frame + 1, 1, 0x40 if held else 0,
                              forward=127 if held else 0, yaw=-32768, now=now)
            x, y, z = genji.mover.position
            yaw, pitch = aim(fight._eye(caster), (x, y + 1, z))
            peers[1].commands(caster.body_script.frame + 1, 1, 16 if cast else 0,
                              yaw=yaw, pitch=pitch, now=now)

        def start_climb():
            for _ in range(20):
                step()
                if mobility.climbing:
                    break
            self.assertTrue(mobility.climbing)

        start_climb()
        step(cast=True)
        for _ in range(35):
            if fight.is_stunned(genji):
                break
            self.assertTrue(mobility.climbing)
            step()
        self.assertTrue(fight.is_stunned(genji))
        self.assertEqual(fight.vitals[genji.body].total, 175)
        step()
        self.assertFalse(mobility.climbing)
        self.assertTrue(mobility.climb_spent)
        height = genji.mover.position[1]
        for _ in range(16):
            step()
            self.assertTrue(fight.is_stunned(genji))
            self.assertFalse(mobility.climbing)
            self.assertLessEqual(genji.mover.position[1], height)

        for _ in range(100):
            step(held=False)
            if not fight.is_stunned(genji) and not genji.mover.airborne:
                break
        self.assertFalse(fight.is_stunned(genji))
        self.assertFalse(genji.mover.airborne)
        start_climb()
        self.assertFalse(mobility.climb_spent)
        _, replay, _ = self.verify(session)
        copied, = replay.server.matches
        self.assertEqual(copied.players[0].genji_mobility, mobility)
        self.assertEqual(copied.players[0].mover.position, genji.mover.position)
        self.assertEqual(copied.combat.vitals[genji.body].total, 175)

    def test_genji_climb_opens_low_cover_and_landing_resets_budget_in_wire_replay(self):
        session, sent = self.make(wall_world(depth=.5, height=1.6))
        peers, (genji, soldier) = self.connected(session, sent, first_hero=0x02E0000000000029)
        fight = genji.match.combat
        now = 100.944
        first_packet = len(sent)
        x, y, z = soldier.mover.position
        point = x, y + .85, z

        def step(buttons=0, forward=0, pitch=0):
            nonlocal now
            now += combat.TICK
            session.tick(now)
            peers[0].commands(genji.body_script.frame + 1, 1, buttons, forward=forward, pitch=pitch, now=now)
            peers[1].send(now=now)

        _, pitch = aim(fight._eye(genji), point)
        for frame in range(60):
            step(1 if frame == 0 else 0, pitch=pitch)
        self.assertFalse(peers[0].hit_notices(first_packet))
        self.assertFalse(fight.rockets)
        for _ in range(25):
            step(64, forward=127)
            if genji.genji_mobility.climbing and genji.mover.position[1] >= .55:
                break
        self.assertTrue(genji.genji_mobility.climbing)
        self.assertAlmostEqual(genji.mover.velocity[1], 7.5, places=2)
        spent_observed = False
        for frame in range(24):
            _, pitch = aim(fight._eye(genji), point)
            step(65 if frame == 0 else 64, forward=127, pitch=pitch)
            if frame == 0:
                self.assertTrue(genji.genji_mobility.climbing)
            spent_observed |= genji.genji_mobility.climb_spent

        step()
        self.assertTrue(spent_observed)
        self.assertFalse(genji.genji_mobility.climbing)
        self.assertFalse(genji.mover.airborne)
        self.assertFalse(genji.genji_mobility.climb_spent)
        self.assertEqual(genji.genji_mobility.climb_elapsed, 0)
        step(64)
        self.assertTrue(genji.mover.airborne)
        self.assertFalse(genji.genji_mobility.air_jump_used)
        self.assertGreater(genji.mover.velocity[1], 5)
        self.assertEqual(fight.vitals[soldier.body].total, 113)
        weapon = next(i for i in genji.body_script.component.instances.values() if i.graph.index == 0x0304)
        self.assertEqual(weapon.vars[53].value(), 24)
        hits = [notice["+0x78"] for notice in peers[0].hit_notices(first_packet)]
        self.assertEqual(hits, [{"+0x0": {"+0x0": soldier.body}, "+0x4": 29, "+0x8": 0}] * 3)

        _, replay, _ = self.verify(session)
        match = replay.server.matches[0]
        copied_genji, copied_soldier = match.players
        self.assertEqual(match.combat.vitals[copied_soldier.body].total, 113)
        self.assertEqual(copied_genji.mover.position, genji.mover.position)
        self.assertEqual(copied_genji.genji_mobility, genji.genji_mobility)

    def test_genji_rejects_unearned_dragonblade_in_wire_replay(self):
        session, sent = self.make()
        peers, players = self.connected(session, sent, first_hero=0x02E0000000000029)
        genji, soldier = players
        component = genji.body_script.component
        blade = next(i for i in component.instances.values() if i.graph.index == 0x0317).state(3)
        peers[0].commands(genji.body_script.frame + 1, 1, 32, now=100.944)
        self.assertFalse(blade.flags & 1)
        self.assertEqual(component.entity_var(342).value(), 0)
        peers[0].commands(genji.body_script.frame + 1, 24, 1, pitch=-900, now=100.944)
        for tick in range(16):
            session.tick(100.96 + tick * 0.016)
        self.assertEqual(genji.match.combat.vitals[soldier.body].total, 26)
        self.assertEqual(component.entity_var(342).value(), 174)

        _, replay, _ = self.verify(session)
        match = replay.server.matches[0]
        copied_genji, copied_soldier = match.players
        self.assertEqual(match.combat.vitals[copied_soldier.body].total, 26)
        self.assertEqual(copied_genji.body_script.component.entity_var(342).value(), 174)

    def test_genji_earned_dragonblade_and_weapon_return_in_wire_replay(self):
        self._verify_genji_dragonblade()

    def test_genji_earned_dragonblade_respects_deflect_facing_in_wire_replay(self):
        self._verify_genji_dragonblade(mirror_deflect=True)

    def test_genji_earned_dragonblade_selects_two_of_five_npcs_in_wire_replay(self):
        self._verify_genji_dragonblade(npc_positions={
            "1": (-1.4, 0, 4), "2": (1.4, 0, 4), "3": (-.9, 0, 8),
            "5": (7, 0, 2), "4": (-2.4, 0, -3.5),
        })

    def _verify_genji_dragonblade(self, *, npc_positions=None, mirror_deflect=False):
        catalog = {}
        if npc_positions is not None:
            catalog_path = DATA_DIR / "pve_npcs_174.json"
            if not catalog_path.is_file():
                self.skipTest("requires the external 1.74 five-NPC catalog")
            catalog = npc.load_catalog(catalog_path)
        session, sent = self.make()
        hero = 0x02E0000000000029
        peers, (genji, opponent) = self.connected(session, sent, first_hero=hero,
                                                  second_hero=hero if mirror_deflect else SOLDIER)
        fight = genji.match.combat
        component = genji.body_script.component
        blade = next(i for i in component.instances.values() if i.graph.index == 0x0317).state(3)
        charge = component.entity_var(342)
        cost = component.entity_var(338).value()
        now = 100.944

        def command(count, buttons=0, *, pitch=0, forward=0):
            peers[0].commands(genji.body_script.frame + 1, count, buttons, pitch=pitch, forward=forward, now=now)

        def advance(ticks):
            nonlocal now
            for _ in range(ticks):
                now += 0.016
                session.tick(now)
            for peer in peers:
                peer.drain()
                peer.send(now=now)

        def respawn_if_dead():
            nonlocal now
            if fight.is_dead(opponent):
                now += 4.5
                session.tick(now, missed=280)
                advance(8)
                self.assertFalse(fight.is_dead(opponent))

        def health():
            vitals = fight.vitals.get(opponent.body)
            return vitals.total if vitals is not None else 200

        npc_entities = [session.apply("spawn_manual_npc", {
            "match_id": genji.match.id, "definition": dataclasses.asdict(catalog[key]),
            "position": position, "team": 1, "yaw": 0,
        }) for key, position in (npc_positions or {}).items()]
        if npc_entities:
            advance(24)
            self.assertTrue(all(genji.client.bot_create_acked(entity) for entity in npc_entities))

        def npc_health():
            return [fight.vitals[entity].total for entity in npc_entities]

        initial_npc_health = npc_health()
        self.assertEqual(charge.value(), 0)
        for _ in range(100):
            command(32, 1, pitch=-900)
            advance(20)
            if charge.value() == cost:
                break
            respawn_if_dead()
        self.assertEqual(charge.value(), cost)
        respawn_if_dead()
        if mirror_deflect:
            for _ in range(6):
                if health() > 110:
                    break
                command(32, 1, pitch=-900)
                advance(20)
                respawn_if_dead()
            self.assertGreater(health(), 110)
        self.assertEqual(npc_health(), initial_npc_health)
        before = health()
        first_blade_packet = len(sent)
        command(1, 32)
        self.assertTrue(blade.flags & 1)
        self.assertEqual(charge.value(), 0)
        command(20, forward=127)
        command(60)
        if mirror_deflect:
            deflect = next(i for i in opponent.body_script.component.instances.values()
                           if i.graph.index == 0x03A3)
            weapon = next(i for i in component.instances.values() if i.graph.index == 0x0304)

            def swings():
                return sum(weapon.states[index].counter for index in (66, 67) if index in weapon.states)

            peers[1].commands(opponent.body_script.frame + 1, 8, 16, yaw=-32768, now=now)
            self.assertTrue(deflect.state(10).active)
            before_swings = swings()
            command(1, 1)
            command(64)
            advance(12)
            self.assertEqual(swings(), before_swings + 1)
            self.assertEqual(health(), before)
            self.assertEqual(charge.value(), 0)
            self.assertFalse(peers[0].hit_notices(first_blade_packet))
            self.assertFalse(peers[1].hit_notices(first_blade_packet))
            peers[1].commands(opponent.body_script.frame + 1, 1, 0, yaw=0, now=now)
            self.assertTrue(deflect.state(10).active)
        command(1, 1)
        command(35)
        advance(12)
        self.assertEqual(fight.vitals[opponent.body].total, max(0, before - 110))
        self.assertEqual(charge.value(), 0)
        if mirror_deflect:
            self.assertEqual(swings(), before_swings + 2)
            self.assertTrue(deflect.state(10).active)
            hit, = peers[0].hit_notices(first_blade_packet)
            self.assertEqual(hit["+0x78"], {"+0x0": {"+0x0": opponent.body}, "+0x4": 110, "+0x8": 0})
            self.assertFalse(peers[1].hit_notices(first_blade_packet))
            peers[1].commands(opponent.body_script.frame + 1, 150, 0, yaw=0, now=now)
            self.assertFalse(deflect.state(10).active)
        if npc_entities:
            self.assertEqual(npc_health(), [initial_npc_health[0] - 110, initial_npc_health[1] - 110,
                                           *initial_npc_health[2:]])
            hits = [notice["+0x78"] for notice in peers[0].hit_notices(first_blade_packet)]
            self.assertCountEqual([hit for hit in hits if hit["+0x0"]["+0x0"] in npc_entities], [
                {"+0x0": {"+0x0": entity}, "+0x4": 110, "+0x8": 0} for entity in npc_entities[:2]
            ])

        for _ in range(20):
            command(32)
            advance(32)
            if not blade.flags & 1:
                break
        self.assertFalse(blade.flags & 1)
        respawn_if_dead()
        command(64)
        before = health()
        command(1, 1)
        command(18)
        advance(16)
        final_health = health()
        self.assertLess(final_health, before)
        self.assertGreater(charge.value(), 0)

        genji_health = None
        if npc_entities:
            command(76, forward=-127)
            command(16)
            advance(76)
            by_entity = {bot.entity: bot for bot in genji.match.bots}
            enemies = {key: by_entity[entity] for key, entity in zip(npc_positions, npc_entities, strict=True)}
            after_outbound = npc_health()
            genji_health = self.assert_five_npc_replies(genji, peers, enemies, (20, 38, 100, 5, 25), now)
            self.assertEqual(npc_health(), after_outbound)

        _, replay, _ = self.verify(session)
        match = replay.server.matches[0]
        copied_genji, copied_opponent = match.players
        self.assertEqual(match.combat.vitals[copied_opponent.body].total, final_health)
        self.assertEqual(copied_genji.body_script.component.entity_var(342).value(), charge.value())
        self.assertEqual([match.combat.vitals[entity].total for entity in npc_entities], npc_health())
        if genji_health is not None:
            self.assertEqual(copied_genji.body, genji.body)
            self.assertEqual(match.combat.vitals[copied_genji.body].total, genji_health)
            self.assertFalse(match.combat.pins or match.combat.assassin_victims)

    def test_genji_quick_melee_damage_and_release_in_wire_replay(self):
        session, sent = self.make()
        peers, players = self.connected(session, sent, first_hero=0x02E0000000000029)
        genji, soldier = players
        peer = peers[0]
        first_packet = len(sent)
        peer.commands(genji.body_script.frame + 1, 24, 0x800, now=100.944)
        fight = genji.match.combat
        self.assertEqual(fight.vitals[soldier.body].total, 200)

        peer.commands(genji.body_script.frame + 1, 45, 0, forward=127, now=100.944)
        melee = next(i for i in genji.body_script.component.instances.values() if i.graph.index == 0x0043)
        self.assertEqual(melee.state(4).counter, 1)
        peer.commands(genji.body_script.frame + 1, 32, 0x800, now=100.944)
        self.assertEqual(melee.state(4).counter, 2)
        self.assertEqual(fight.vitals[soldier.body].total, 170)
        for tick in range(12):
            now = 100.96 + tick * 0.016
            session.tick(now)
            for client in peers:
                client.drain()
                client.send(now=now)

        hit, = peer.hit_notices(first_packet)
        self.assertEqual(hit["+0x78"], {"+0x0": {"+0x0": soldier.body}, "+0x4": 30, "+0x8": 0})
        self.assertFalse(peers[1].hit_notices(first_packet))
        charge = genji.body_script.component.entity_var(342).value()
        self.assertGreaterEqual(charge, 30)
        _, replay, _ = self.verify(session)
        match = replay.server.matches[0]
        copied_genji, copied_soldier = match.players
        self.assertEqual(match.combat.vitals[copied_soldier.body].total, 170)
        self.assertEqual(copied_genji.body_script.component.entity_var(342).value(), charge)

    def test_genji_strike_crosses_two_of_five_enemies_and_reuses_in_wire_replay(self):
        catalog_path = DATA_DIR / "pve_npcs_174.json"
        if not catalog_path.is_file():
            self.skipTest("requires the external 1.74 five-NPC catalog")
        catalog = npc.load_catalog(catalog_path)
        session, sent = self.make()
        (peer,), (player,) = self.connected(session, sent, two=False, first_hero=0x02E0000000000029)
        match = player.match
        fight = match.combat
        # Keep each reply separately selectable from the origin. Assassin stays
        # outside the Strike sweep but within the manual pin range.
        positions = {"1": (1.25, 0, 3), "2": (-1.25, 0, 8), "3": (0, 0, 18),
                     "5": (4, 0, -4), "4": (-2.5, 0, 0)}
        enemies = {}
        for key, position in positions.items():
            entity = session.apply("spawn_manual_npc", {
                "match_id": match.id, "definition": dataclasses.asdict(catalog[key]),
                "position": position, "team": 1, "yaw": 0,
            })
            enemies[key] = next(bot for bot in match.bots if bot.entity == entity)
        now = 100.944

        def step():
            nonlocal now
            now += .016
            session.tick(now)
            peer.drain()
            peer.send(now=now)

        for _ in range(24):
            step()
        self.assertTrue(all(player.client.bot_create_acked(bot.entity) for bot in enemies.values()))
        strike = next(i for i in player.body_script.component.instances.values() if i.graph.index == 0x01DB)
        initial = {key: fight.vitals[bot.entity].total for key, bot in enemies.items()}

        def damage():
            return {key: initial[key] - fight.vitals[bot.entity].total for key, bot in enemies.items()}

        def send(count, buttons, yaw=0):
            peer.commands(player.body_script.frame + 1, count, buttons, yaw=yaw, now=now)

        def advance(count):
            for _ in range(count):
                step()

        def assert_hits(first_packet):
            rows = [notice['+0x78'] for notice in peer.hit_notices(first_packet)]
            actual = [(row['+0x0']['+0x0'], row['+0x4'], row['+0x8']) for row in rows]
            self.assertCountEqual(actual, [(enemies[key].entity, 50, 0) for key in ('1', '2')])

        first_packet = len(sent)
        send(1, 8)
        send(30, 0)
        advance(31)
        self.assertEqual(player.mover.position, [0, 0, 15])
        self.assertEqual(strike.state(1).counter, 1)
        self.assertFalse(strike.state(1).active)
        self.assertGreater(strike.state(0).var.value(), 0)
        self.assertEqual(damage(), {'1': 50, '2': 50, '3': 0, '5': 0, '4': 0})
        assert_hits(first_packet)

        first_packet = len(sent)
        send(1, 8, -32768)
        send(30, 0, -32768)
        advance(31)
        self.assertEqual(strike.state(1).counter, 1)
        self.assertEqual(player.mover.position, [0, 0, 15])
        self.assertEqual(damage(), {'1': 50, '2': 50, '3': 0, '5': 0, '4': 0})
        self.assertFalse(peer.hit_notices(first_packet))
        for _ in range(35):
            if strike.state(0).var.value() == 0:
                break
            send(16, 0, -32768)
            advance(16)
        self.assertEqual(strike.state(0).var.value(), 0)
        self.assertEqual(strike.state(1).counter, 1)

        first_packet = len(sent)
        send(1, 8, -32768)
        send(30, 0, -32768)
        advance(31)
        self.assertEqual(player.mover.position, [0, 0, 0])
        self.assertEqual(strike.state(1).counter, 2)
        self.assertFalse(strike.state(1).active)
        self.assertEqual(damage(), {'1': 100, '2': 100, '3': 0, '5': 0, '4': 0})
        assert_hits(first_packet)

        body = player.body
        health = self.assert_five_npc_replies(player, (peer,), enemies, (20, 18, 100, 5, 25), now)
        _, replay, _ = self.verify(session)
        replay_match, = replay.server.matches
        self.assertEqual(replay_match.players[0].mover.position, [0, 0, 0])
        self.assertEqual(replay_match.players[0].body, body)
        self.assertEqual(replay_match.combat.vitals[body].total, health)
        self.assertFalse(replay_match.combat.pins or replay_match.combat.assassin_victims)
        self.assertEqual([replay_match.combat.vitals[bot.entity].total for bot in replay_match.bots],
                         [fight.vitals[bot.entity].total for bot in enemies.values()])

    def assert_five_npc_replies(self, player, peers, enemies, damage, now):
        peer = peers[0]
        fight = player.match.combat

        def step():
            nonlocal now
            now += .016
            peer.session.tick(now)
            for viewer in peers:
                viewer.drain()
                viewer.send(now=now)

        def advance(count):
            for _ in range(count):
                step()

        body = player.body
        health = player.body_script.component.entity_var(253).value()
        self.assertEqual(health, 200)
        for key, expected_damage in zip(("1", "2", "3", "5", "4"), damage, strict=True):
            bot = enemies[key]
            x, y, z = bot.position
            yaw, pitch = aim(fight._eye(player), (x, y + (.75 if key == "4" else 1), z))
            peer.commands(player.body_script.frame + 1, 1, 0, yaw=yaw, pitch=pitch, now=now)
            start = peer.session.apply("manual_npc_key", {"account": player.account_lo, "key": "0"})
            self.assertEqual(start["entity"], bot.entity, bot.name)
            first_packet = len(peer.sent)
            for _ in range(220):
                step()
                if player.body_script.component.entity_var(253).value() < health:
                    break
            health -= expected_damage
            self.assertEqual(fight.vitals[body].total, health, (bot.name, player.mover.position))
            self.assertTrue(bot.attack_input)
            self.assertTrue(all(not other.attack_input for other in enemies.values() if other is not bot))
            if key == "4":
                self.assertEqual(fight.pins.get(bot.entity), body)
                peer.commands(player.body_script.frame + 1, 8, 0, yaw=yaw, pitch=pitch, now=now)
                advance(8)
                self.assertEqual(fight.pins.get(bot.entity), body)

            stop = peer.session.apply("manual_npc_key", {"account": player.account_lo, "key": "{"})
            self.assertEqual(stop["count"], 5)
            for _ in range(2):
                peer.commands(player.body_script.frame + 1, 20, 0, yaw=yaw, pitch=pitch, now=now)
                advance(20)
            self.assertFalse(fight.pins)
            for _ in range(6):
                if not fight.assassin_victims:
                    break
                peer.commands(player.body_script.frame + 1, 20, 0, yaw=yaw, pitch=pitch, now=now)
                advance(20)
            self.assertFalse(fight.pins or fight.assassin_victims)
            self.assertEqual(fight.vitals[body].total, health)
            self.assertEqual(peer.health_updates(first_packet, body), {(world.OP_UPDATE, 200, health)}, bot.name)
        return health

    def test_genji_strike_damage_movement_and_cooldown_in_wire_replay(self):
        session, sent = self.make()
        peers, players = self.connected(session, sent, first_hero=0x02E0000000000029)
        genji, soldier = players
        first_packet = len(sent)
        for count, buttons in ((1, 8), (30, 0), (1, 8)):
            peers[0].commands(genji.body_script.frame + 1, count, buttons, now=100.944)
        strike = next(i for i in genji.body_script.component.instances.values() if i.graph.index == 0x01DB)
        self.assertEqual(strike.state(1).counter, 1)
        self.assertFalse(strike.state(1).active)
        self.assertGreater(strike.state(0).var.value(), 7)
        self.assertEqual(genji.mover.position, [0, 0, 15])
        for tick in range(12):
            now = 100.96 + tick * 0.016
            session.tick(now)
            for peer in peers:
                peer.drain()
                peer.send(now=now)

        self.assertEqual(genji.match.combat.vitals[soldier.body].total, 150)
        self.assertEqual(genji.body_script.component.entity_var(342).value(), 50)
        hit, = peers[0].hit_notices(first_packet)
        self.assertEqual(hit["+0x78"], {"+0x0": {"+0x0": soldier.body}, "+0x4": 50, "+0x8": 0})
        self.assertFalse(peers[1].hit_notices(first_packet))

        for tick in range(560):
            now = 101.152 + tick * 0.016
            session.tick(now)
            if tick % 16 == 0:
                for peer in peers:
                    peer.drain()
                    peer.send(now=now)
                peers[0].commands(genji.body_script.frame + 1, 16, 0, now=now)
            if strike.state(0).var.value() == 0:
                break
        self.assertEqual(strike.state(0).var.value(), 0)
        first_packet = len(sent)
        peers[0].commands(genji.body_script.frame + 1, 1, 8, yaw=-32768, now=now)
        peers[0].commands(genji.body_script.frame + 1, 30, 0, yaw=-32768, now=now)
        self.assertEqual(strike.state(1).counter, 2)
        self.assertGreater(strike.state(0).var.value(), 7)
        self.assertEqual(genji.mover.position, [0, 0, 0])
        for tick in range(1, 13):
            shot_time = now + tick * 0.016
            session.tick(shot_time)
            for peer in peers:
                peer.drain()
                peer.send(now=shot_time)
        self.assertEqual(genji.match.combat.vitals[soldier.body].total, 100)
        hit, = peers[0].hit_notices(first_packet)
        self.assertEqual(hit["+0x78"], {"+0x0": {"+0x0": soldier.body}, "+0x4": 50, "+0x8": 0})
        self.assertFalse(peers[1].hit_notices(first_packet))

        _, replay, _ = self.verify(session)
        match = replay.server.matches[0]
        copied_genji, copied_soldier = match.players
        self.assertEqual(copied_genji.mover.position, [0, 0, 0])
        self.assertEqual(match.combat.vitals[copied_soldier.body].total, 100)
        copied_strike = next(i for i in copied_genji.body_script.component.instances.values()
                             if i.graph.index == 0x01DB)
        self.assertEqual(copied_strike.state(0).var.value(), strike.state(0).var.value())
        self.assertEqual(copied_strike.state(1).counter, 2)

    def test_genji_strike_bypasses_deflect_and_lethal_hit_resets_in_wire_replay(self):
        hero = 0x02E0000000000029
        for lethal in (False, True):
            with self.subTest(lethal=lethal):
                session, sent = self.make()
                peers, (attacker, defender) = self.connected(session, sent, first_hero=hero, second_hero=hero)
                attack_peer, defense_peer = peers
                fight = attacker.match.combat
                now = 100.944

                def command(count, buttons, *, yaw=0, pitch=0):
                    attack_peer.commands(attacker.body_script.frame + 1, count, buttons, yaw=yaw, pitch=pitch, now=now)

                def step(count):
                    nonlocal now
                    for _ in range(count):
                        now += 0.016
                        session.tick(now)
                    for peer in peers:
                        peer.drain()
                        peer.send(now=now)

                if lethal:
                    x, y, z = defender.mover.position
                    yaw, pitch = aim(fight._eye(attacker), (x, y + 0.8, z))
                    for remaining in (113, 26):
                        command(1, 1, yaw=yaw, pitch=pitch)
                        command(59, 0, yaw=yaw, pitch=pitch)
                        step(24)
                        self.assertEqual(fight.vitals[defender.body].total, remaining)

                first_packet = len(sent)
                damage_before = attacker.stats.values.get(stats.HERO_DAMAGE, 0)
                charge = attacker.body_script.component.entity_var(342)
                charge_before = charge.value()
                deflect = next(i for i in defender.body_script.component.instances.values()
                               if i.graph.index == 0x03A3).state(10)
                strike = next(i for i in attacker.body_script.component.instances.values()
                              if i.graph.index == 0x01DB)
                defense_peer.commands(defender.body_script.frame + 1, 8, 16, yaw=-32768, now=now)
                self.assertTrue(deflect.active)
                command(1, 8)
                command(30, 0)
                self.assertEqual(strike.state(1).counter, 1)
                self.assertEqual(attacker.mover.position, [0, 0, 15])
                damage = 26 if lethal else 50
                self.assertEqual(fight.vitals[defender.body].total, 0 if lethal else 150)
                self.assertEqual(attacker.stats.values[stats.HERO_DAMAGE] - damage_before, damage)
                self.assertIn(charge.value() - charge_before, (damage, damage + 5))
                self.assertEqual(fight.is_dead(defender), lethal)
                if lethal:
                    self.assertFalse(deflect.active)
                    self.assertEqual(fight.vitals[defender.body].killer, attacker.body)
                    self.assertEqual(strike.state(0).var.value(), 0)
                else:
                    self.assertTrue(deflect.active)
                    self.assertGreater(strike.state(0).var.value(), 7)

                command(1, 8)
                command(30, 0)
                step(12)
                self.assertEqual(strike.state(1).counter, 2 if lethal else 1)
                self.assertGreater(strike.state(0).var.value(), 7)
                self.assertEqual(attacker.mover.position, [0, 0, 30 if lethal else 15])
                hit, = attack_peer.hit_notices(first_packet)
                self.assertEqual(hit["+0x78"], {"+0x0": {"+0x0": defender.body}, "+0x4": damage,
                                                "+0x8": combat.KILLING if lethal else 0})
                self.assertFalse(defense_peer.hit_notices(first_packet))
                self.assertNotIn(attacker.body, fight.vitals)
                self.assertEqual(len(attack_peer.combat_notices(first_packet, combat.KILL_NOTICE)), int(lethal))
                self.assertEqual(len(defense_peer.combat_notices(first_packet, combat.DEATH_NOTICE)), int(lethal))

                _, replay, _ = self.verify(session)
                copied_match = replay.server.matches[0]
                copied_attacker, copied_defender = copied_match.players
                copied_strike = next(i for i in copied_attacker.body_script.component.instances.values()
                                     if i.graph.index == 0x01DB)
                self.assertEqual(copied_attacker.mover.position, attacker.mover.position)
                self.assertEqual(copied_strike.state(1).counter, strike.state(1).counter)
                self.assertEqual(copied_strike.state(0).var.value(), strike.state(0).var.value())
                self.assertEqual(copied_match.combat.vitals[copied_defender.body].total, 0 if lethal else 150)
                self.assertEqual(copied_attacker.stats.values[stats.HERO_DAMAGE], damage_before + damage)
                self.assertEqual(copied_attacker.body_script.component.entity_var(342).value(), charge.value())

    def test_genji_strike_assist_window_and_reuse_in_wire_replay(self):
        for expired in (False, True):
            with self.subTest(expired=expired):
                session, sent = self.make()
                peers, players = self.connected(session, sent, first_hero=0x02E0000000000029, ally=True)
                genji, victim, ally = players
                self.assertEqual([player.team for player in players], [0, 1, 0])
                fight = genji.match.combat
                victim_body = victim.body
                first_packet = len(sent)
                now = 100.944
                for count, buttons in ((1, 8), (30, 0)):
                    peers[0].commands(genji.body_script.frame + 1, count, buttons, now=now)
                strike = next(i for i in genji.body_script.component.instances.values() if i.graph.index == 0x01DB)
                self.assertEqual(strike.state(1).counter, 1)
                self.assertGreater(strike.state(0).var.value(), 7)
                self.assertEqual(genji.mover.position, [0, 0, 15])

                wait_ticks = combat.GENJI_ASSIST_TICKS + 16 if expired else 12
                for index in range(1, wait_ticks + 1):
                    now = 100.944 + index * 0.016
                    session.tick(now)
                    if index % 8 == 0:
                        for peer in peers:
                            peer.drain()
                            peer.send(now=now)
                        peers[0].commands(genji.body_script.frame + 1, 8, 0, now=now)
                self.assertEqual(fight.vitals[victim_body].total, 150)
                self.assertNotIn(ally.body, fight.vitals)
                contribution_tick = fight.genji_assists[victim_body][genji.body][1]
                self.assertGreater(strike.state(0).var.value(), 0)

                x, y, z = victim.mover.position
                yaw, pitch = aim(fight._eye(ally), (x, y + 1.3, z))
                for _ in range(100):
                    now += 0.016
                    peers[2].commands(ally.body_script.frame + 1, 1, 1, yaw=yaw, pitch=pitch, now=now)
                    session.tick(now)
                    if fight.vitals[victim_body].dead:
                        break
                self.assertTrue(fight.vitals[victim_body].dead,
                                (fight.vitals[victim_body].total, ally.mover.position, victim.mover.position))
                age = fight.vitals[victim_body].death_tick - contribution_tick
                self.assertEqual(age > combat.GENJI_ASSIST_TICKS, expired)
                self.assertEqual(genji.stats.values.get(stats.ELIMINATIONS, 0), int(not expired))
                self.assertEqual(ally.stats.values[stats.ELIMINATIONS], 1)
                self.assertEqual(strike.state(0).var.value() == 0, not expired)

                for count, buttons in ((1, 8), (30, 0)):
                    peers[0].commands(genji.body_script.frame + 1, count, buttons, yaw=-32768, now=now)
                expected_count = 1 if expired else 2
                expected_position = [0, 0, 15 if expired else 0]
                self.assertEqual(strike.state(1).counter, expected_count)
                self.assertEqual(genji.mover.position, expected_position)
                for _ in range(12):
                    now += 0.016
                    session.tick(now)
                    for peer in peers:
                        peer.drain()
                        peer.send(now=now)
                hit, = peers[0].hit_notices(first_packet)
                self.assertEqual(hit["+0x78"], {"+0x0": {"+0x0": victim_body}, "+0x4": 50, "+0x8": 0})
                notices = peers[0].combat_notices(first_packet, combat.KILL_NOTICE)
                self.assertEqual([notice["+0x84"]["+0x0"] for notice in notices],
                                 [] if expired else [victim_body])

                _, replay, _ = self.verify(session)
                match = replay.server.matches[0]
                copied_genji, copied_victim, copied_ally = match.players
                self.assertTrue(match.combat.vitals[copied_victim.body].dead)
                self.assertEqual(copied_ally.stats.values[stats.ELIMINATIONS], 1)
                self.assertEqual(copied_genji.stats.values.get(stats.ELIMINATIONS, 0), int(not expired))
                self.assertEqual(copied_genji.mover.position, expected_position)
                copied_strike = next(i for i in copied_genji.body_script.component.instances.values()
                                     if i.graph.index == 0x01DB)
                self.assertEqual(copied_strike.state(1).counter, expected_count)
                self.assertEqual(copied_strike.state(0).var.value(), strike.state(0).var.value())

    def test_genji_enforcer_pellet_deflect_in_five_npc_wire_replay(self):
        path = DATA_DIR / "pve_npcs_174.json"
        if not path.is_file():
            self.skipTest("requires the external 1.74 five-NPC catalog")
        catalog = npc.load_catalog(path)
        charges = []
        for yaw in (0, -32768):
            with self.subTest(yaw=yaw):
                session, sent = self.make()
                (peer,), (genji,) = self.connected(session, sent, two=False, first_hero=0x02E0000000000029)
                fight = genji.match.combat
                body = genji.body
                component = genji.body_script.component
                state = next(i for i in component.instances.values() if i.graph.index == 0x03A3).state(10)
                health, charge = component.entity_var(253), component.entity_var(342)
                positions = {"1": (4, 0, 0), "2": (0, 0, 8), "3": (-4, 0, 0),
                             "5": (0, 0, -4), "4": (3, 0, -3)}
                enemies = {key: session.apply("spawn_manual_npc", {
                    "match_id": genji.match.id, "definition": dataclasses.asdict(catalog[key]),
                    "position": position, "team": 1, "yaw": 0,
                }) for key, position in positions.items()}
                enforcer = enemies["2"]
                initial = {entity: fight.vitals[entity].total for entity in enemies.values()}
                now = 100.944

                def step():
                    nonlocal now
                    now += .016
                    session.tick(now)
                    peer.drain()
                    peer.send(now=now)

                for _ in range(24):
                    step()
                self.assertTrue(all(genji.client.bot_create_acked(entity) for entity in enemies.values()))
                peer.commands(genji.body_script.frame + 1, 1, 0, now=now)
                start = session.apply("manual_npc_key", {"account": genji.account_lo, "key": "0"})
                self.assertEqual(start["entity"], enforcer)
                first_packet = len(sent)
                max_pellets = 0
                for frame in range(100):
                    peer.commands(genji.body_script.frame + 1, 1, 16 if frame == 0 else 0, yaw=yaw, now=now)
                    step()
                    max_pellets = max(max_pellets, len(fight.rockets))
                self.assertEqual(max_pellets, 25)
                self.assertTrue(state.active)
                self.assertEqual(state.counter, 1)
                reflected = yaw == 0
                expected_health = 200 if reflected else 162
                self.assertEqual(health.value(), expected_health)
                expected = initial.copy()
                expected[enforcer] -= 50 if reflected else 0
                self.assertEqual({entity: fight.vitals[entity].total for entity in expected}, expected)
                stop = session.apply("manual_npc_key", {"account": genji.account_lo, "key": "{"})
                self.assertEqual(stop["count"], 5)
                for _ in range(32):
                    step()
                self.assertFalse(fight.rockets)
                self.assertEqual(health.value(), expected_health)
                self.assertEqual(genji.stats.values.get(stats.HERO_DAMAGE, 0), 50 if reflected else 0)
                hits = [notice["+0x78"] for notice in peer.hit_notices(first_packet)]
                hit = {"+0x0": {"+0x0": enforcer}, "+0x4": 2, "+0x8": 0}
                self.assertEqual(hits, [hit] * 25 if reflected else [])
                updates = peer.health_updates(first_packet, body)
                if reflected:
                    self.assertFalse(updates)
                else:
                    self.assertIn((world.OP_UPDATE, 200, 162), updates)
                    self.assertTrue(all(op == world.OP_UPDATE and maximum == 200 and 162 <= hp < 200
                                        for op, maximum, hp in updates))
                charges.append(charge.value())

                _, replay, _ = self.verify(session)
                copied, = replay.server.matches
                copied_genji, = copied.players
                self.assertEqual(copied_genji.body, body)
                self.assertEqual(copied_genji.body_script.component.entity_var(253).value(), expected_health)
                self.assertEqual(copied_genji.body_script.component.entity_var(342).value(), charge.value())
                self.assertEqual({entity: copied.combat.vitals[entity].total for entity in expected}, expected)
        if len(charges) == 2:
            self.assertEqual(charges[0] - charges[1], 50)

    def test_genji_sniper_deflect_facing_and_expiry_in_five_npc_wire_replay(self):
        self._verify_first_npc_shot_against_deflect(
            "3", 100, (("front", 50, 0), ("rear", 50, -32768), ("expired", 0, 0)),
        )

    def test_genji_heavy_assault_deflect_facing_in_five_npc_wire_replay(self):
        self._verify_first_npc_shot_against_deflect("5", 5, (("front", 0, 0), ("rear", 0, -32768)))

    def _verify_first_npc_shot_against_deflect(self, attacker, amount, cases):
        path = DATA_DIR / "pve_npcs_174.json"
        if not path.is_file():
            self.skipTest("requires the external 1.74 five-NPC catalog")
        catalog = npc.load_catalog(path)
        for name, press, yaw in cases:
            with self.subTest(case=name):
                session, sent = self.make()
                (peer,), (genji,) = self.connected(session, sent, two=False, first_hero=0x02E0000000000029)
                fight = genji.match.combat
                body = genji.body
                component = genji.body_script.component
                state = next(i for i in component.instances.values() if i.graph.index == 0x03A3).state(10)
                health, charge = component.entity_var(253), component.entity_var(342)
                positions = {"1": (4, 0, 0), "2": (-8, 0, 0), "3": (0, 0, 4),
                             "5": (0, 0, -4), "4": (3, 0, -3)}
                if attacker == "5":
                    positions["3"], positions["5"] = positions["5"], positions["3"]
                enemies = {key: session.apply("spawn_manual_npc", {
                    "match_id": genji.match.id, "definition": dataclasses.asdict(catalog[key]),
                    "position": position, "team": 1, "yaw": 0,
                }) for key, position in positions.items()}
                target = enemies[attacker]
                initial = {entity: fight.vitals[entity].total for entity in enemies.values()}
                now = 100.944

                def step():
                    nonlocal now
                    now += .016
                    session.tick(now)
                    peer.drain()
                    peer.send(now=now)

                for _ in range(24):
                    step()
                self.assertTrue(all(genji.client.bot_create_acked(entity) for entity in enemies.values()))
                peer.commands(genji.body_script.frame + 1, 1, 0, now=now)
                start = session.apply("manual_npc_key", {"account": genji.account_lo, "key": "0"})
                self.assertEqual(start["entity"], target)
                first_packet = len(sent)
                for frame in range(155):
                    before = charge.value()
                    peer.commands(genji.body_script.frame + 1, 1, 16 if frame == press else 0, yaw=yaw, now=now)
                    step()
                    if health.value() < 200 or fight.vitals[target].total < initial[target]:
                        break
                reflected = name == "front"
                self.assertEqual(state.counter, 1)
                self.assertEqual(state.active, name != "expired")
                self.assertEqual(charge.value() - before, amount if reflected else 0)
                expected = initial.copy()
                expected[target] -= amount if reflected else 0
                self.assertEqual({entity: fight.vitals[entity].total for entity in expected}, expected)
                stop = session.apply("manual_npc_key", {"account": genji.account_lo, "key": "{"})
                self.assertEqual(stop["count"], 5)
                for _ in range(32):
                    step()
                expected_health = 200 if reflected else 200 - amount
                self.assertEqual(health.value(), expected_health)
                self.assertEqual({entity: fight.vitals[entity].total for entity in expected}, expected)
                self.assertEqual(genji.stats.values.get(stats.HERO_DAMAGE, 0), amount if reflected else 0)
                hits = [notice["+0x78"] for notice in peer.hit_notices(first_packet)]
                self.assertEqual(hits, [{"+0x0": {"+0x0": target}, "+0x4": amount, "+0x8": 0}] if reflected else [])
                self.assertEqual(peer.health_updates(first_packet, body),
                                 set() if reflected else {(world.OP_UPDATE, 200, expected_health)})

                _, replay, _ = self.verify(session)
                copied, = replay.server.matches
                copied_genji, = copied.players
                self.assertEqual(copied_genji.body, body)
                self.assertEqual(copied_genji.body_script.component.entity_var(253).value(), health.value())
                self.assertEqual(copied_genji.body_script.component.entity_var(342).value(), charge.value())
                self.assertEqual({entity: copied.combat.vitals[entity].total for entity in expected}, expected)

    def test_genji_deflect_cancels_and_reuses_against_npc_in_wire_replay(self):
        path = DATA_DIR / "pve_npcs_174.json"
        if not path.is_file():
            self.skipTest("requires the external 1.74 five-NPC catalog")
        catalog = npc.load_catalog(path)
        session, sent = self.make()
        (peer,), (genji,) = self.connected(session, sent, two=False, first_hero=0x02E0000000000029)
        match = genji.match
        positions = {"1": (0, 0, 4), "2": (8, 0, 0), "3": (0, 0, -4),
                     "5": (-4, 0, 0), "4": (1.4, 0, 1.4)}
        enemies = [session.apply("spawn_manual_npc", {
            "match_id": match.id, "definition": dataclasses.asdict(catalog[key]),
            "position": position, "team": 1, "yaw": 0,
        }) for key, position in positions.items()]
        trooper = enemies[0]
        fight = match.combat
        body = genji.body
        initial = [fight.vitals[entity].total for entity in enemies]
        component = genji.body_script.component
        deflect = next(i for i in component.instances.values() if i.graph.index == 0x03A3)
        state, cooldown = deflect.state(10), deflect.state(0)
        charge = component.entity_var(342)
        now = 100.944

        def step():
            nonlocal now
            now += .016
            session.tick(now)
            peer.drain()
            peer.send(now=now)

        def health():
            vitals = fight.vitals.get(body)
            return vitals.total if vitals is not None else component.entity_var(253, create=False).value()

        def reply_until(condition):
            start = session.apply("manual_npc_key", {"account": genji.account_lo, "key": "0"})
            self.assertEqual(start["entity"], trooper)
            for _ in range(100):
                before = charge.value()
                step()
                if condition():
                    break
            self.assertTrue(condition())
            stop = session.apply("manual_npc_key", {"account": genji.account_lo, "key": "{"})
            self.assertEqual(stop["count"], 5)
            return charge.value() - before

        for _ in range(24):
            step()
        self.assertTrue(all(genji.client.bot_create_acked(entity) for entity in enemies))
        yaw, pitch = aim(fight._eye(genji), (0, 1, 4))

        def command(count, buttons=0):
            peer.commands(genji.body_script.frame + 1, count, buttons, yaw=yaw, pitch=pitch, now=now)
            for _ in range(count):
                step()

        def reflect(expected_count, expected_damage):
            first = len(sent)
            command(1, 16)
            command(12)
            self.assertTrue(state.active)
            self.assertEqual(state.counter, expected_count)
            self.assertEqual(reply_until(lambda: fight.vitals[trooper].total <= initial[0] - expected_damage), 20)
            command(8)
            self.assertEqual([notice["+0x78"] for notice in peer.hit_notices(first)],
                             [{"+0x0": {"+0x0": trooper}, "+0x4": 20, "+0x8": 0}])
            self.assertEqual([fight.vitals[entity].total for entity in enemies],
                             [initial[0] - expected_damage, *initial[1:]])

        command(8)
        reflect(1, 20)
        self.assertEqual(health(), 200)
        self.assertEqual(charge.value(), 20)
        for _ in range(2):
            command(1, 16)
            command(16)
            self.assertFalse(state.active)
            self.assertEqual(state.counter, 1)
        self.assertGreater(cooldown.var.value(), 0)
        self.assertEqual(reply_until(lambda: health() < 200), 0)
        self.assertEqual(health(), 180)
        command(16)
        for _ in range(40):
            if cooldown.var.value() == 0:
                break
            command(16)
        self.assertEqual(cooldown.var.value(), 0)
        self.assertEqual(state.counter, 1)
        reflect(2, 40)
        self.assertEqual(health(), 180)

        _, replay, _ = self.verify(session)
        copied, = replay.server.matches
        self.assertEqual(copied.combat.vitals[body].total, 180)
        self.assertEqual([copied.combat.vitals[entity].total for entity in enemies],
                         [initial[0] - 40, *initial[1:]])
        self.assertEqual(copied.players[0].body_script.component.entity_var(342).value(), charge.value())

    def test_assassin_pin_interrupts_deflect_and_releases_genji_weapon_in_wire_replay(self):
        path = DATA_DIR / "pve_npcs_174.json"
        if not path.is_file():
            self.skipTest("requires the external 1.74 five-NPC catalog")
        catalog = npc.load_catalog(path)
        session, sent = self.make()
        (peer,), (genji,) = self.connected(session, sent, two=False, first_hero=0x02E0000000000029)
        match, body = genji.match, genji.body
        fight = match.combat
        positions = {"1": (0, 0, 4), "2": (8, 0, 0), "3": (0, 0, -4),
                     "5": (-4, 0, 0), "4": (1.4, 0, 1.4)}
        enemies = {key: session.apply("spawn_manual_npc", {
            "match_id": match.id, "definition": dataclasses.asdict(catalog[key]),
            "position": position, "team": 1, "yaw": 0,
        }) for key, position in positions.items()}
        assassin = enemies["4"]
        before = {entity: fight.vitals[entity].total for entity in enemies.values()}
        component = genji.body_script.component
        deflect = next(i for i in component.instances.values() if i.graph.index == 0x03A3)
        weapon = next(i for i in component.instances.values() if i.graph.index == 0x0304)
        yaw, pitch = aim(fight._eye(genji), (1.4, 0.75, 1.4))
        now = 100.944

        def step(count):
            nonlocal now
            for _ in range(count):
                now += 0.016
                session.tick(now)
                peer.drain()
                peer.send(now=now)

        def command(count, buttons=0, *, forward=0):
            peer.commands(genji.body_script.frame + 1, count, buttons, forward=forward,
                          yaw=yaw, pitch=pitch, now=now)
            step(count)

        step(24)
        self.assertTrue(all(genji.client.bot_create_acked(entity) for entity in enemies.values()))
        first_packet = len(sent)
        command(1, 16)
        command(8)
        self.assertTrue(deflect.state(10).active)
        start = session.apply("manual_npc_key", {"account": genji.account_lo, "key": "0"})
        self.assertEqual(start["entity"], assassin)
        for _ in range(60):
            step(1)
            if fight.pins.get(assassin) == body:
                break
        self.assertEqual(fight.pins.get(assassin), body)
        self.assertFalse(deflect.state(10).active)
        self.assertEqual(fight.vitals[body].total, 175)
        pinned_position = tuple(genji.mover.position)
        command(8, 1 | 8 | 16, forward=127)
        self.assertEqual(tuple(genji.mover.position), pinned_position)
        self.assertEqual(weapon.vars[53].value(), 30)
        self.assertFalse(peer.hit_notices(first_packet))
        self.assertEqual({entity: fight.vitals[entity].total for entity in enemies.values()}, before)

        stop = session.apply("manual_npc_key", {"account": genji.account_lo, "key": "{"})
        self.assertEqual(stop["count"], 5)
        command(1)
        command(1, 16)
        command(30)
        self.assertFalse(deflect.state(10).active)
        self.assertEqual(deflect.state(10).counter, 1)
        self.assertGreater(deflect.state(0).var.value(), 0)
        self.assertFalse(fight.pins)
        for _ in range(4):
            if not fight.assassin_victims:
                break
            command(20)
        self.assertFalse(fight.pins or fight.assassin_victims)
        command(1, 1)
        command(38)
        self.assertEqual(weapon.vars[53].value(), 27)
        self.assertEqual(fight.vitals[body].total, 175)
        expected = {**before, assassin: before[assassin] - 87}
        self.assertEqual({entity: fight.vitals[entity].total for entity in enemies.values()}, expected)
        self.assertEqual(genji.stats.values[stats.HERO_DAMAGE], 87)
        hit = {"+0x0": {"+0x0": assassin}, "+0x4": 29, "+0x8": 0}
        self.assertEqual([notice["+0x78"] for notice in peer.hit_notices(first_packet)], [hit] * 3)

        _, replay, _ = self.verify(session)
        copied, = replay.server.matches
        self.assertFalse(copied.combat.pins or copied.combat.assassin_victims)
        self.assertEqual(copied.combat.vitals[body].total, 175)
        self.assertEqual({entity: copied.combat.vitals[entity].total for entity in enemies.values()}, expected)
        copied_genji, = copied.players
        self.assertEqual(copied_genji.stats.values[stats.HERO_DAMAGE], 87)
        copied_deflect = next(i for i in copied_genji.body_script.component.instances.values()
                              if i.graph.index == 0x03A3)
        self.assertEqual(copied_deflect.state(10).counter, 1)
        self.assertEqual(copied_deflect.state(0).var.value(), deflect.state(0).var.value())

    def test_genji_deflect_blocks_only_frontal_quick_melee_in_wire_replay(self):
        hero = 0x02E0000000000029
        for facing, damage in ((-32768, 0), (0, 30)):
            with self.subTest(facing=facing):
                session, sent = self.make()
                peers, players = self.connected(session, sent, first_hero=hero, second_hero=hero)
                attacker, defender = players
                attack_peer, defense_peer = peers
                fight = attacker.match.combat
                now = 100.944
                first_packet = len(sent)

                attack_peer.commands(attacker.body_script.frame + 1, 45, 0, forward=127, now=now)
                defense_peer.commands(defender.body_script.frame + 1, 8, 16, yaw=facing, now=now)
                deflect = next(i for i in defender.body_script.component.instances.values()
                               if i.graph.index == 0x03A3)
                self.assertTrue(deflect.state(10).active)
                attack_peer.commands(attacker.body_script.frame + 1, 32, 0x800, now=now)
                melee = next(i for i in attacker.body_script.component.instances.values()
                             if i.graph.index == 0x0043)
                self.assertEqual(melee.state(4).counter, 1)
                for _ in range(12):
                    now += 0.016
                    session.tick(now)
                    for peer in peers:
                        peer.drain()
                        peer.send(now=now)

                self.assertTrue(deflect.state(10).active)
                self.assertNotIn(attacker.body, fight.vitals)
                self.assertEqual(attacker.body_script.component.entity_var(253).value(), 200)
                self.assertEqual(defender.body_script.component.entity_var(253).value(), 200 - damage)
                self.assertEqual(attacker.stats.values.get(stats.HERO_DAMAGE, 0), damage)
                self.assertEqual(defender.stats.values.get(stats.HERO_DAMAGE, 0), 0)
                hit = {"+0x0": {"+0x0": defender.body}, "+0x4": 30, "+0x8": 0}
                self.assertEqual([notice["+0x78"] for notice in attack_peer.hit_notices(first_packet)],
                                 [hit] if damage else [])
                self.assertFalse(defense_peer.hit_notices(first_packet))

                _, replay, _ = self.verify(session)
                copied_match = replay.server.matches[0]
                copied_attacker, copied_defender = copied_match.players
                self.assertNotIn(copied_attacker.body, copied_match.combat.vitals)
                self.assertEqual(copied_attacker.body_script.component.entity_var(253).value(), 200)
                self.assertEqual(copied_defender.body_script.component.entity_var(253).value(), 200 - damage)
                self.assertEqual(copied_attacker.stats.values.get(stats.HERO_DAMAGE, 0), damage)

    def test_genji_double_deflect_preserves_one_projectile_in_wire_replay(self):
        session, sent = self.make()
        hero = 0x02E0000000000029
        scene = dataclasses.replace(MAP, spawns=(MAP.spawns[0], Spawn(1, (0, 0, 16), 180.0)))
        peers, (first, second) = self.connected(session, sent, first_hero=hero, second_hero=hero, scene=scene)
        fight = first.match.combat
        now = 100.944
        first_packet = len(sent)

        def commands(first_buttons, second_buttons):
            peers[0].commands(first.body_script.frame + 1, 1, first_buttons, now=now)
            peers[1].commands(second.body_script.frame + 1, 1, second_buttons, yaw=-32768, now=now)

        def step():
            nonlocal now
            now += 0.016
            session.tick(now)
            for peer in peers:
                peer.drain()
                peer.send(now=now)

        commands(2, 16)
        self.assertEqual(len(fight.rockets), 3)
        centre = max(fight.rockets, key=lambda shot: shot.direction[2])
        self.assertIs(centre.shooter, first)
        owner, remaining = first, centre.left
        transfers = []
        step()
        for frame in range(1, 75):
            commands(16 if frame == 8 else 0, 16 if frame == 36 else 0)
            step()
            if centre.shooter is not owner:
                owner = centre.shooter
                transfers.append((owner, centre.team))
                self.assertLess(centre.left, remaining)
                remaining = centre.left

        self.assertEqual(transfers, [(second, second.team), (first, first.team)])
        self.assertTrue(all(shot is not centre for shot in fight.rockets))
        self.assertEqual(first.body_script.component.entity_var(253).value(), 200)
        self.assertEqual(fight.vitals[second.body].total, 142)
        hit, = peers[0].hit_notices(first_packet)
        self.assertEqual(hit["+0x78"], {"+0x0": {"+0x0": second.body}, "+0x4": 58, "+0x8": combat.CRITICAL})
        self.assertFalse(peers[1].hit_notices(first_packet))
        self.assertEqual(first.stats.values[stats.HERO_DAMAGE], 58)
        self.assertEqual(second.stats.values.get(stats.HERO_DAMAGE, 0), 0)
        charge = first.body_script.component.entity_var(342).value()
        self.assertEqual(charge - second.body_script.component.entity_var(342).value(), 58)

        _, replay, _ = self.verify(session)
        copied, = replay.server.matches
        copied_first, copied_second = copied.players
        self.assertEqual(copied_first.body_script.component.entity_var(342).value(), charge)
        self.assertEqual(copied.combat.vitals[copied_second.body].total, 142)

    def test_genji_reflected_shuriken_retires_with_its_current_body_in_wire_replay(self):
        hero = 0x02E0000000000029
        scene = dataclasses.replace(MAP, spawns=(MAP.spawns[0], Spawn(1, (0, 0, 16), 180.0)))
        for replacing_owner in (False, True):
            with self.subTest(replacing_owner=replacing_owner):
                session, sent = self.make()
                peers, (source, owner) = self.connected(
                    session, sent, first_hero=hero, second_hero=hero, scene=scene,
                )
                fight = source.match.combat
                now = 100.944
                first_packet = len(sent)
                peers[0].commands(source.body_script.frame + 1, 1, 2, now=now)
                peers[1].commands(owner.body_script.frame + 1, 1, 16, yaw=-32768, now=now)
                self.assertEqual(len(fight.rockets), 3)
                centre = max(fight.rockets, key=lambda shot: shot.direction[2])
                self.assertIs(centre.shooter, source)

                for _ in range(36):
                    now += 0.016
                    session.tick(now)
                    for peer in peers:
                        peer.drain()
                        peer.send(now=now)
                    if centre.shooter is owner:
                        break
                self.assertIs(centre.shooter, owner)
                self.assertTrue(any(shot is centre for shot in fight.rockets))

                player = owner if replacing_owner else source
                peer = peers[1 if replacing_owner else 0]
                old_body, old_script = player.body, player.body_script
                peer.message(21616, pick(player.entity, SOLDIER), now=now)
                self.assertNotEqual(player.body, old_body)
                self.assertEqual(player.hero.guid, SOLDIER)
                self.assertTrue(old_script.retired)
                self.assertEqual(any(shot is centre for shot in fight.rockets), not replacing_owner)
                self.assertEqual(source.body_script.component.entity_var(253).value(), 200)

                for _ in range(45):
                    now += 0.016
                    session.tick(now)
                    for peer in peers:
                        peer.drain()
                        peer.send(now=now)

                self.assertFalse(peers[0].hit_notices(first_packet))
                hits = [notice["+0x78"] for notice in peers[1].hit_notices(first_packet)]
                damage = sum(hit["+0x4"] for hit in hits)
                if replacing_owner:
                    self.assertEqual(hits, [])
                else:
                    hit, = hits
                    self.assertEqual(hit, {"+0x0": {"+0x0": source.body}, "+0x4": 29, "+0x8": 0})
                self.assertEqual(source.body_script.component.entity_var(253).value(), 200 - damage)
                self.assertEqual(owner.stats.values.get(stats.HERO_DAMAGE, 0), damage)
                charge = owner.body_script.component.entity_var(342).value()

                _, replay, _ = self.verify(session)
                match, = replay.server.matches
                copied_source, copied_owner = match.players
                copied_player = copied_owner if replacing_owner else copied_source
                self.assertEqual(copied_player.body, player.body)
                self.assertEqual(copied_source.body_script.component.entity_var(253).value(), 200 - damage)
                self.assertEqual(copied_owner.stats.values.get(stats.HERO_DAMAGE, 0), damage)
                self.assertEqual(copied_owner.body_script.component.entity_var(342).value(), charge)

    def test_genji_deflect_transfers_rifle_hit_and_credit_in_wire_replay(self):
        session, sent = self.make()
        peers, players = self.connected(session, sent, first_hero=0x02E0000000000029)
        genji, soldier = players
        fight = genji.match.combat
        first_packet = len(sent)
        peers[0].commands(genji.body_script.frame + 1, 8, 16, now=100.944)
        deflect = next(i for i in genji.body_script.component.instances.values() if i.graph.index == 0x03A3)
        self.assertTrue(deflect.state(10).active)
        peers[1].commands(soldier.body_script.frame + 1, 1, 1, yaw=-32768, pitch=900, now=100.944)
        for tick in range(12):
            now = 100.96 + tick * 0.016
            session.tick(now)
            for peer in peers:
                peer.drain()
                peer.send(now=now)

        expected_health = [200, 180]
        self.assertEqual([fight.vitals[player.body].total for player in players], expected_health)
        self.assertEqual(genji.body_script.component.entity_var(342).value(), 20)
        for peer in peers:
            hits = peer.hit_notices(first_packet)
            if peer is peers[0]:
                hit, = hits
                self.assertEqual(hit["+0x78"], {
                    "+0x0": {"+0x0": soldier.body}, "+0x4": 20, "+0x8": 0,
                })
            else:
                self.assertFalse(hits)

        first_packet = len(sent)
        for count, buttons in ((25, 0), (1, 16), (12, 0)):
            peers[0].commands(genji.body_script.frame + 1, count, buttons, now=101.16)
        self.assertFalse(deflect.state(10).active)
        self.assertGreater(deflect.state(0).var.value(), 0)
        peers[0].commands(genji.body_script.frame + 1, 1, 16, now=101.16)
        self.assertEqual(deflect.state(10).counter, 1)
        peers[1].commands(soldier.body_script.frame + 1, 12, 0, now=101.16)
        peers[1].commands(soldier.body_script.frame + 1, 1, 1, yaw=-32768, pitch=900, now=101.16)
        for tick in range(12):
            now = 101.176 + tick * 0.016
            session.tick(now)
            for peer in peers:
                peer.drain()
                peer.send(now=now)

        expected_health = [160, 180]
        self.assertEqual([fight.vitals[player.body].total for player in players], expected_health)
        self.assertFalse(peers[0].hit_notices(first_packet))
        hit, = peers[1].hit_notices(first_packet)
        self.assertEqual(hit["+0x78"], {
            "+0x0": {"+0x0": genji.body}, "+0x4": 40, "+0x8": combat.CRITICAL,
        })

        for tick in range(560):
            now = 101.368 + tick * 0.016
            session.tick(now)
            if tick % 16 == 0:
                for peer in peers:
                    peer.drain()
                    peer.send(now=now)
                peers[0].commands(genji.body_script.frame + 1, 16, 0, now=now)
            if deflect.state(0).var.value() == 0:
                break
        self.assertEqual(deflect.state(0).var.value(), 0)
        self.assertFalse(deflect.state(10).active)
        first_packet = len(sent)
        peers[0].commands(genji.body_script.frame + 1, 1, 0, now=now)
        peers[0].commands(genji.body_script.frame + 1, 1, 16, now=now)
        self.assertTrue(deflect.state(10).active)
        self.assertEqual(deflect.state(10).counter, 2)
        peers[1].commands(soldier.body_script.frame + 1, 12, 0, now=now)
        peers[1].commands(soldier.body_script.frame + 1, 1, 1, yaw=-32768, pitch=900, now=now)
        for tick in range(1, 13):
            shot_time = now + tick * 0.016
            session.tick(shot_time)
            for peer in peers:
                peer.drain()
                peer.send(now=shot_time)

        expected_health = [160, 160]
        self.assertEqual([fight.vitals[player.body].total for player in players], expected_health)
        hit, = peers[0].hit_notices(first_packet)
        self.assertEqual(hit["+0x78"], {"+0x0": {"+0x0": soldier.body}, "+0x4": 20, "+0x8": 0})
        self.assertFalse(peers[1].hit_notices(first_packet))
        _, replay, _ = self.verify(session)
        match = replay.server.matches[0]
        self.assertEqual([match.combat.vitals[player.body].total for player in match.players], expected_health)
        charge = genji.body_script.component.entity_var(342).value()
        self.assertGreaterEqual(charge, 40)
        self.assertEqual(match.players[0].body_script.component.entity_var(342).value(), charge)

    def test_genji_damage_charge_survives_rifle_death_and_owner_respawn_in_wire_replay(self):
        session, sent = self.make()
        peers, players = self.connected(session, sent, first_hero=0x02E0000000000029)
        genji, soldier = players
        genji_peer, soldier_peer = peers
        fight = genji.match.combat
        old_body = genji.body
        old_script = genji.body_script
        first_packet = len(sent)
        now = 100.944

        def step(count):
            nonlocal now
            for _ in range(count):
                now += 0.016
                session.tick(now)
            for peer in peers:
                peer.drain()
                peer.send(now=now)

        def charge(player):
            return player.body_script.component.entity_var(342).value()

        self.assertEqual(charge(genji), 0)
        genji_peer.commands(genji.body_script.frame + 1, 24, 1, now=now)
        step(30)
        self.assertEqual(fight.vitals[soldier.body].total, 113)
        self.assertEqual(charge(genji), 87)

        soldier_peer.commands(soldier.body_script.frame + 1, 60, 1, yaw=-32768, pitch=900, now=now)
        step(12)
        self.assertTrue(fight.is_dead(genji))
        self.assertEqual(fight.vitals[old_body].killer, soldier.body)
        self.assertEqual(charge(genji), 87)
        death, = genji_peer.combat_notices(first_packet, combat.DEATH_NOTICE)
        self.assertEqual(death["+0x7C"], {"+0x0": soldier.body})
        self.assertEqual(len(soldier_peer.combat_notices(first_packet, combat.KILL_NOTICE)), 1)

        now += 4.5
        session.tick(now, missed=280)
        step(8)
        self.assertNotEqual(genji.body, old_body)
        self.assertTrue(old_script.retired)
        self.assertFalse(fight.is_dead(genji))
        self.assertEqual(genji.body_script.component.entity_var(253).value(), 200)
        self.assertEqual(charge(genji), 87)
        passive = next(i for i in genji.body_script.component.instances.values() if i.graph.index == 0x0B8B)
        self.assertTrue(passive.state(passive.graph.node(3).state).active)

        genji_peer.commands(genji.body_script.frame + 1, 75, 0, now=now)
        step(8)
        self.assertEqual(charge(genji), 92)
        self.assertEqual(len(genji_peer.hit_notices(first_packet)), 3)

        _, replay, _ = self.verify(session)
        copied_genji, copied_soldier = replay.server.matches[0].players
        self.assertEqual(copied_genji.body, genji.body)
        self.assertEqual(charge(copied_genji), 92)
        self.assertEqual(copied_genji.body_script.component.entity_var(253).value(), 200)
        self.assertEqual(copied_soldier.stats.values[stats.ELIMINATIONS], 1)

    def test_death_respawn_hero_skin_and_same_address_reentry_use_production_paths(self):
        skins = {}
        session, sent = self.make(skin_of=lambda account, hero: skins.get((account, hero), (0, False)))
        peers, players = self.connected(session, sent)
        shooter, victim = players
        peers[0].commands(shooter.body_script.frame + 1, 60, 1)
        fight = victim.match.combat
        self.assertTrue(fight.is_dead(victim))
        old_body = victim.body
        session.tick(105.0, missed=200)
        self.assertNotEqual(victim.body, old_body)
        self.assertFalse(fight.is_dead(victim))
        self.assertTrue(session.apply("switch_hero", {"account": 1, "hero": REAPER}))
        skins[(1, REAPER)] = (0x123, True)
        self.assertTrue(session.apply("skin_changed", {"account": 1, "hero": REAPER}))
        old = peers[0]
        old.send(flags=FIN, now=105.01)
        session.report_callbacks()
        (handoff,) = session.create_match(MAP, [(1, "Alpha", SOLDIER, 0, False)])
        new = Peer(session, handoff, sent, old.address)
        new.send(flags=SYN, now=105.02)
        new.send(now=105.02)
        # A late, authenticated old packet cannot affect the new connection on that address.
        session.datagram(old.last_raw, old.address, 105.03)
        session.report_callbacks()
        document, replay, result = self.verify(session)
        self.assertIn(handoff.conn, replay.server.clients)
        self.assertNotIn(old.handoff.conn, replay.server.clients)
        self.assertTrue(any(replay_values.decode(effect)[1]["kind"] == "left"
                            for root in document["roots"] for effect in root["effects"]))
        self.assertGreater(result["roots"], 30)

    def test_expired_handoff_leaves_on_the_next_tick_and_replays_the_callback(self):
        session, sent = self.make()
        entries = [(1, "Alpha", SOLDIER, 0, False), (2, "Beta", SOLDIER, 1, False)]
        first, second = session.create_match(MAP, entries)
        alpha, beta = session.server.matches[0].players
        second_deadline = session.server.handoffs[second.conn][2]

        self.assertTrue(session.apply("expire_handoff", {"conn": first.conn}))
        self.assertEqual(session.server.handoffs[first.conn], (first, alpha, 0.0))
        self.assertEqual(session.server.handoffs[second.conn][2], second_deadline)
        self.assertFalse(alpha.gone)
        cancelled = Peer(session, first, sent, ("127.0.0.1", 43000))
        cancelled.send(flags=SYN)
        self.assertNotIn(first.conn, session.server.clients)
        self.assertIn(first.conn, session.server.handoffs)
        self.assertEqual(sent, [])
        session.report_callbacks()

        session.tick(100.0)
        self.assertTrue(alpha.gone)
        self.assertFalse(beta.gone)
        self.assertNotIn(first.conn, session.server.handoffs)
        self.assertFalse(session.apply("expire_handoff", {"conn": first.conn}))
        session.report_callbacks()

        peer = Peer(session, second, sent, ("127.0.0.1", 43000))
        peer.send(flags=SYN)
        self.assertFalse(session.apply("expire_handoff", {"conn": second.conn}))

        document, replay, _ = self.verify(session)
        self.assertIn(second.conn, replay.server.clients)
        self.assertNotIn(first.conn, replay.server.handoffs)
        callback_rows = [row for row in document["roots"] if row["operation"] == "report_callbacks"]
        self.assertEqual(
            [[replay_values.decode(effect)[1] for effect in row["effects"]] for row in callback_rows],
            [[], [{"kind": "left", "player": (first.match_id, 0)}]],
        )

    def test_controlled_lobby_cancellation_replays_against_authentic_syn_order(self):
        session, sent = self.make()
        first, second = session.create_match(
            MAP, [(1, "Alpha", SOLDIER, 0, False), (2, "Beta", SOLDIER, 1, False)],
        )
        alpha, beta = session.server.matches[0].players
        # The real lobby handler uses the explicit game root while the
        # surrounding lobby state and timing remain controlled inputs.
        matchmaker = Matchmaker(SimpleNamespace(game=session, session_of=lambda _: None))

        def cancel(account_lo, handoff):
            account = SimpleNamespace(account_lo=account_lo)
            lobby_session = SimpleNamespace(account=account, logged_in=False, log=lambda *_: None)
            party = SimpleNamespace(members=[account])
            ticket = Ticket(party, {}, 0, 100.0)
            matchmaker.found[account_lo] = Found(lobby_session, ticket, handoff, MAP.name)
            matchmaker.cancel(party)
            self.assertNotIn(account_lo, matchmaker.found)

        cancel(alpha.account_lo, first)
        self.assertEqual(session.server.handoffs[first.conn], (first, alpha, 0.0))
        Peer(session, first, sent, ("127.0.0.1", 43000)).send(flags=SYN)
        self.assertNotIn(first.conn, session.server.clients)
        self.assertEqual(sent, [])
        self.assertFalse(alpha.gone)
        session.report_callbacks()

        connected = Peer(session, second, sent, ("127.0.0.1", 43000))
        connected.send(flags=SYN)
        connected.send()
        cancel(beta.account_lo, second)
        self.assertIs(session.server.clients[second.conn], beta.client)
        self.assertTrue(beta.client.open)
        session.report_callbacks()

        session.tick(100.0)
        session.report_callbacks()
        document, replay, _ = self.verify(session)
        cancellations = [row for row in document["roots"] if row["operation"] == "expire_handoff"]
        self.assertEqual(
            [
                (replay_values.decode(row["arguments"]), replay_values.decode(row["result"]))
                for row in cancellations
            ],
            [({"conn": first.conn}, True), ({"conn": second.conn}, False)],
        )
        callbacks = [row for row in document["roots"] if row["operation"] == "report_callbacks"]
        self.assertEqual(
            [[replay_values.decode(effect)[1] for effect in row["effects"]] for row in callbacks],
            [[], [{"kind": "joined", "player": (first.match_id, 1)}],
             [{"kind": "left", "player": (first.match_id, 0)}]],
        )
        for server in (session.server, replay.server):
            self.assertEqual(server.handoffs, {})
            self.assertEqual(set(server.clients), {second.conn})
            self.assertTrue(server.clients[second.conn].open)

    def test_delayed_game_found_replays_the_reservation_check_and_admission(self):
        deadline = 100.0 + HANDOFF_SECONDS
        for now, pending in ((deadline - 0.001, True), (deadline, False)):
            with self.subTest(pending=pending):
                source = FixedSource()
                session, sent = self.make(source=source, verify_stored_state=True)
                (handoff,) = session.create_match(MAP, [(1, "Alpha", SOLDIER, 0, False)])
                lobby = SimpleNamespace(
                    account=SimpleNamespace(account_lo=1),
                    logged_in=True,
                    send=mock.Mock(),
                    sock=SimpleNamespace(getsockname=lambda: ("127.0.0.1", 3724)),
                    log=lambda *_: None,
                )
                game = SimpleNamespace(
                    port=session.server.port,
                    handoff_pending=session.handoff_pending,
                    expire_handoff=session.expire_handoff,
                )
                matchmaker = Matchmaker(SimpleNamespace(
                    game=game, state_lock=RLock(), session_of={1: lobby}.get,
                    settings=SimpleNamespace(game_host=""),
                    social=SimpleNamespace(general={}, open_match_chat=lambda *_: {}),
                ))
                found = Found(lobby, Ticket(SimpleNamespace(), {}, 0, 100.0), handoff, MAP.name)
                matchmaker.found[1] = found

                with mock.patch.object(source, "wall_time", return_value=now) as wall_time:
                    matchmaker._found_over([found])
                wall_time.assert_called_once_with(callsite="server.handoff.pending")
                self.assertEqual(matchmaker.found, {})
                lobby_output = [call.args[1:] for call in lobby.send.call_args_list]
                self.assertEqual([identifier for identifier, _ in lobby_output],
                                 [20404, 20402, 20600] if pending else [])
                self.assertEqual(session.server.handoffs[handoff.conn][2], deadline if pending else 0.0)

                if pending:
                    payload = lobby_output[-1][1]["+0x80"]
                    handoff = dataclasses.replace(
                        handoff, conn=payload["+0x28"],
                        key_in=bytes(payload["+0xAE"]), key_out=bytes(payload["+0xCE"]),
                    )
                peer = Peer(session, handoff, sent, ("127.0.0.1", 43000))
                peer.send(flags=SYN, now=now)
                if pending:
                    self.assertEqual(peer.cipher.open(sent[0][0]).flags, SYN | ACK)
                    peer.send(now=now)
                else:
                    self.assertEqual(sent, [])
                session.tick(deadline)
                session.report_callbacks()
                document, replay, result = self.verify(session)

                self.assertTrue(result["stored_state_verified"])
                check = document["roots"][1]
                self.assertEqual(check["operation"], "handoff_pending")
                self.assertEqual(replay_values.decode(check["arguments"]), {"conn": handoff.conn})
                self.assertIs(replay_values.decode(check["result"]), pending)
                self.assertEqual(check["state_before"], check["state_after"])
                self.assertEqual(replay_values.decode(document["roots"][-1]["effects"][0])[1], {
                    "kind": "joined" if pending else "left", "player": (handoff.match_id, 0),
                })
                for server in (session.server, replay.server):
                    self.assertNotIn(handoff.conn, server.handoffs)
                    if pending:
                        self.assertTrue(server.clients[handoff.conn].open)
                    else:
                        self.assertNotIn(handoff.conn, server.clients)
                        self.assertIsNone(server.player_of(1))

    def test_bounded_loop_replays_syn_before_the_due_tick_and_early_receives(self):
        deadline = 100.0 + HANDOFF_SECONDS
        source = SchedulerSource(
            ("start", 10.0), ("timeout", 10.0), ("datagram", deadline - 0.001),
            ("due", 10.030), ("next_tick", 10.034), ("tick", deadline),
            ("timeout", 10.040), ("due", 10.045), ("timeout", 10.046), ("due", 10.047),
        )
        with (
            mock.patch("socket.socket", side_effect=AssertionError("must not create sockets")),
            self.assertLogs("ow174.game", level="WARNING") as logged,
        ):
            session, sent = self.make(source=source)
            (handoff,) = session.create_match(MAP, [(1, "Alpha", SOLDIER, 0, False)])
            peer = Peer(session, handoff, sent, ("127.0.0.1", 43000))
            syn = peer.cipher.seal(Packet(handoff.conn, flags=SYN))
            socket_before = session.server.sock
            receives = [(syn, peer.address), ("error", (10054, "controlled receive failure")), None]
            session.run_loop(receives)
            document, replay, result = self.verify(session)
        self.assertIs(session.server.sock, socket_before)
        self.assertFalse(session.server.running)
        self.assertEqual(result["send_attempts"], 1)
        [(reply, address)] = sent
        self.assertEqual(address, peer.address)
        self.assertEqual(peer.cipher.open(reply).flags, SYN | ACK)
        self.assertEqual(sum("controlled receive failure" in line for line in logged.output), 2)
        for server in (session.server, replay.server):
            self.assertIn(handoff.conn, server.clients)
            self.assertNotIn(handoff.conn, server.handoffs)
            self.assertEqual(server.tick, FIRST_TICK + 3)
            self.assertEqual(server.clients[handoff.conn].heard, deadline - 0.001)
        self.assertEqual([row["operation"] for row in document["roots"]], ["create_match", "run_loop"])
        self.assertEqual(
            [row["callsite"] for row in document["environment"]["reads"]
             if row["callsite"].startswith("server.run.")],
            ["server.run." + name for name in (
                "start", "timeout", "datagram", "due", "next_tick", "tick",
                "timeout", "due", "timeout", "due",
            )],
        )
        effects = [replay_values.decode(effect)[1] for effect in document["roots"][-1]["effects"]]
        socket_effects = [effect for effect in effects if effect["kind"].startswith("socket_")]
        self.assertEqual(
            [effect["kind"] for effect in socket_effects],
            ["socket_timeout", "socket_receive"] * 3 + ["socket_close"],
        )
        self.assertEqual(socket_effects[0]["timeout"], 0.0001)
        self.assertAlmostEqual(socket_effects[2]["timeout"], 0.008)
        self.assertAlmostEqual(socket_effects[4]["timeout"], 0.002)
        self.assertEqual([effect["size"] for effect in socket_effects[1:6:2]], [4096] * 3)
        for replacement in (receives[:-1], [*receives, None]):
            with self.subTest(receive_count=len(replacement)):
                changed = copy.deepcopy(document)
                args = replay_values.decode(changed["roots"][-1]["arguments"])
                args["receives"] = replacement
                changed["roots"][-1]["arguments"] = replay_values.encode(args)
                with (
                    self.assertLogs("ow174.game", level="WARNING"),
                    self.assertRaises((EnvironmentTapeError, ServerJournalError)),
                ):
                    replay_server_session(changed, resolver=session.registry.resolve)

    def test_bounded_loop_replays_callback_reentry_after_releasing_the_server_lock(self):
        source = SchedulerSource(
            ("start", 10.0), ("timeout", 10.0), ("due", 10.0), ("next_tick", 10.0), ("tick", 100.1),
            ("timeout", 10.005), ("due", 10.010),
        )
        session, sent = self.make(source=source, verify_stored_state=True)
        entries = [(1, "Alpha", SOLDIER, 0, False), (2, "Beta", SOLDIER, 1, False)]
        first, second = session.create_match(MAP, entries)
        self.assertTrue(session.apply("expire_handoff", {"conn": first.conn}))
        alpha = (first.match_id, 0)
        beta = (second.match_id, 1)
        args = {"player": beta, "reason": "callback removed the second player"}
        take_out = GameServer.take_out

        def checked_take_out(server, player, reason):
            self.assertFalse(server.lock._is_owned(), "callback reentry must start outside the game lock")
            return take_out(server, player, reason)

        with (
            mock.patch("socket.socket", side_effect=AssertionError("must not create sockets")),
            mock.patch.object(GameServer, "take_out", checked_take_out),
        ):
            session.run_loop([None, None], callback_actions=[("left", alpha, "take_out", args)])
            document, replay, result = self.verify(session)
        self.assertTrue(result["stored_state_verified"])
        self.assertEqual(result["send_attempts"], 0)
        self.assertEqual(sent, [])
        for server in (session.server, replay.server):
            self.assertEqual(server.tick, FIRST_TICK + 1)
            self.assertIsNone(server.player_of(1))
            self.assertIsNone(server.player_of(2))
        effects = [replay_values.decode(effect)[1] for effect in document["roots"][-1]["effects"]]
        kinds = ["left", "callback_action", "callback_result", "left"]
        lifecycle = [effect for effect in effects if effect["kind"] in kinds]
        self.assertEqual([effect["kind"] for effect in lifecycle], kinds)
        self.assertEqual([lifecycle[0]["player"], lifecycle[-1]["player"]], [alpha, beta])
        self.assertEqual(lifecycle[1]["callback"], "left")
        self.assertEqual(lifecycle[1]["player"], alpha)
        self.assertEqual(lifecycle[1]["operation"], "take_out")
        self.assertEqual(lifecycle[1]["arguments"], args)
        self.assertEqual(lifecycle[2]["result"], "left before its game connected")

    def test_bounded_receive_can_expire_handoff_on_either_side_of_syn(self):
        expire_handoff = GameServer.expire_handoff

        def expire_without_outer_lock(server, conn):
            self.assertFalse(server.lock._is_owned())
            self.assertTrue(server.running)
            return expire_handoff(server, conn)

        def expire_on_receive(outcomes, index, args):
            receives = outcomes.copy()
            receives[index] = {
                "outcome": receives[index], "actions": [("expire_handoff", args)],
            }
            return receives

        for receive_index in (0, 1):
            with self.subTest(receive_index=receive_index):
                source = SchedulerSource(
                    ("start", 10.0), ("timeout", 10.0), ("datagram", 100.1),
                    ("due", 10.0), ("next_tick", 10.0), ("tick", 100.1),
                    ("timeout", 10.005), ("due", 10.01),
                )
                session, sent = self.make(source=source, verify_stored_state=True)
                (handoff,) = session.create_match(MAP, [(1, "Alpha", SOLDIER, 0, False)])
                peer = Peer(session, handoff, sent, ("127.0.0.1", 43000))
                syn = peer.cipher.seal(Packet(handoff.conn, flags=SYN | ACK))
                original_socket = session.server.sock
                outcomes = [(syn, peer.address), None]
                args = {"conn": handoff.conn}

                with (
                    mock.patch("socket.socket", side_effect=AssertionError("must not create sockets")),
                    mock.patch.object(GameServer, "expire_handoff", expire_without_outer_lock),
                ):
                    session.run_loop(expire_on_receive(outcomes, receive_index, args))
                    document, replay, result = self.verify(session)
                    changed = copy.deepcopy(document)
                    root = changed["roots"][-1]
                    arguments = replay_values.decode(root["arguments"])
                    arguments["receives"] = expire_on_receive(outcomes, 1 - receive_index, args)
                    root["arguments"] = replay_values.encode(arguments)
                    with self.assertRaises(ServerJournalError):
                        replay_server_session(changed, resolver=session.registry.resolve)

                self.assertTrue(result["stored_state_verified"])
                self.assertIs(session.server.sock, original_socket)
                admitted = receive_index == 1
                self.assertEqual(result["send_attempts"], int(admitted))
                if admitted:
                    [(reply, address)] = sent
                    self.assertEqual(address, peer.address)
                    self.assertEqual(peer.cipher.open(reply).flags, SYN | ACK)
                else:
                    self.assertEqual(sent, [])
                for server in (session.server, replay.server):
                    self.assertFalse(server.running)
                    self.assertEqual(server.tick, FIRST_TICK + 1)
                    self.assertNotIn(handoff.conn, server.handoffs)
                    if admitted:
                        self.assertTrue(server.clients[handoff.conn].open)
                    else:
                        self.assertNotIn(handoff.conn, server.clients)
                        self.assertIsNone(server.player_of(1))
                effects = [replay_values.decode(row)[1] for row in document["roots"][-1]["effects"]]
                action = {"kind": "receive_action", "operation": "expire_handoff", "arguments": args}
                result_effect = {"kind": "receive_result", "result": not admitted}
                action_index = effects.index(action)
                self.assertEqual(effects[action_index - 1]["kind"], "socket_receive")
                self.assertEqual(effects[action_index + 1], result_effect)
                callback = {"kind": "joined" if admitted else "left", "player": (handoff.match_id, 0)}
                if admitted:
                    self.assertLess(effects.index(callback), action_index)
                else:
                    self.assertGreater(effects.index(callback), action_index)

    def test_bounded_loop_rejects_malformed_receives_before_running(self):
        bad_actions = ([], [("take_out", {"player": None, "reason": "callback only"})],
                       [("expire_handoff", {"conn": True})], [("expire_handoff", {"conn": 1, "extra": 0})])
        cases = [[], [b"not an outcome"], [("error", (True, "not a valid errno"))],
                 [{"outcome": None}], [{"outcome": {}, "actions": [("expire_handoff", {"conn": 1})]}]]
        cases.extend([{"outcome": None, "actions": actions}] for actions in bad_actions)
        for receives in cases:
            with self.subTest(receives=receives):
                session, _ = self.make()
                with mock.patch.object(GameServer, "_run") as run, self.assertRaises(ServerJournalError):
                    session.run_loop(receives)
                run.assert_not_called()
                self.assertEqual(session.server.tick, FIRST_TICK)
                self.assertFalse(session.server.running)

    def test_bounded_loop_receive_action_fault_is_not_a_socket_error(self):
        session, sent = self.make()
        (handoff,) = session.create_match(MAP, [(1, "Alpha", SOLDIER, 0, False)])
        socket_before = session.server.sock
        waiting = session.server.handoffs[handoff.conn]
        receives = [{"outcome": None, "actions": [("expire_handoff", {"conn": handoff.conn})]}]
        with (
            mock.patch.object(GameServer, "expire_handoff", side_effect=OSError("action failure")),
            self.assertRaisesRegex(ServerJournalError, "receive action failed: OSError") as caught,
        ):
            session.run_loop(receives)
        self.assertIs(session.server.sock, socket_before)
        self.assertFalse(session.server.running)
        self.assertEqual(session.server.tick, FIRST_TICK)
        self.assertEqual(session.server.handoffs[handoff.conn], waiting)
        self.assertEqual(sent, [])
        with self.assertRaises(ServerJournalError) as finish:
            session.finish()
        self.assertIs(finish.exception, caught.exception)

    def test_bounded_loop_rejects_unmatched_callback_actions(self):
        session, sent = self.make()
        (handoff,) = session.create_match(MAP, [(1, "Alpha", SOLDIER, 0, False)])
        waiting = session.server.handoffs[handoff.conn]
        actions = [("left", (handoff.match_id, 0), "expire_handoff", {"conn": handoff.conn})]
        with self.assertRaisesRegex(ServerJournalError, "unconsumed callback actions") as caught:
            session.run_loop([None], callback_actions=actions)
        self.assertEqual(session.server.handoffs[handoff.conn], waiting)
        self.assertFalse(session.server.running)
        self.assertEqual(sent, [])
        with self.assertRaises(ServerJournalError) as finish:
            session.finish()
        self.assertIs(finish.exception, caught.exception)

    def test_bounded_loop_faults_if_the_production_tick_raises(self):
        session, _ = self.make()
        socket_before = session.server.sock
        with (
            mock.patch.object(GameServer, "_tick", side_effect=RuntimeError("controlled tick failure")),
            self.assertRaises(ServerJournalError) as caught,
        ):
            session.run_loop([None])
        self.assertIs(session.fault, caught.exception)
        self.assertIs(session.server.sock, socket_before)
        self.assertFalse(session.server.running)
        with self.assertRaises(ServerJournalError) as finish:
            session.finish()
        self.assertIs(finish.exception, caught.exception)

    def test_bounded_loop_fault_survives_a_callback_error_caught_by_the_server(self):
        session, _ = self.make()
        entries = [(1, "Alpha", SOLDIER, 0, False), (2, "Beta", SOLDIER, 1, False)]
        first, second = session.create_match(MAP, entries)
        self.assertTrue(session.apply("expire_handoff", {"conn": first.conn}))
        socket_before = session.server.sock
        action_args = {"player": (second.match_id, 1), "reason": "callback failed"}
        actions = [("left", (first.match_id, 0), "take_out", action_args)]
        with (
            mock.patch.object(GameServer, "take_out", side_effect=RuntimeError("callback failure")),
            self.assertLogs("ow174.game", level="ERROR") as logged,
            self.assertRaisesRegex(ServerJournalError, "callback action failed") as caught,
        ):
            session.run_loop([None], callback_actions=actions)
        self.assertIn("join or leave handler failed", "\n".join(logged.output))
        self.assertIsNotNone(session.server.player_of(2))
        self.assertIs(session.server.sock, socket_before)
        self.assertFalse(session.server.running)
        with self.assertRaises(ServerJournalError) as finish:
            session.finish()
        self.assertIs(finish.exception, caught.exception)

    def test_os_send_failure_is_replayed_as_an_attempt_not_as_packet_delivery(self):
        def refused(data, address):
            raise OSError(10054, "controlled send failure")

        session, sent = self.make(sendto=refused)
        (handoff,) = session.create_match(MAP, [(1, "Alpha", SOLDIER, 0, False)])
        peer = Peer(session, handoff, sent, ("127.0.0.1", 43000))
        with self.assertLogs("ow174.game", level="WARNING"):
            peer.send(flags=SYN)
        with self.assertLogs("ow174.game", level="WARNING"):
            _, _, result = self.verify(session)
        self.assertEqual(result["send_attempts"], 1)
        self.assertFalse(session.server.clients[handoff.conn].open)

    def test_wire_result_root_sequence_read_positions_and_closed_tail_tampering_fail(self):
        session, sent = self.make()
        self.connected(session, sent, two=False)
        document = session.finish()
        variants = []
        changed = copy.deepcopy(document)
        changed["roots"][0]["seq"] = 2
        variants.append(changed)
        changed = copy.deepcopy(document)
        changed["roots"][0]["after"] = replay_values.encode((999, (0, 0)))
        variants.append(changed)
        changed = copy.deepcopy(document)
        changed["roots"][0]["result"] = replay_values.encode([])
        variants.append(changed)
        changed = copy.deepcopy(document)
        send_root = next(row for row in changed["roots"] if any(
            replay_values.decode(effect)[1]["kind"] == "send" for effect in row["effects"]
        ))
        index = next(i for i, effect in enumerate(send_root["effects"])
                     if replay_values.decode(effect)[1]["kind"] == "send")
        position, effect = replay_values.decode(send_root["effects"][index])
        effect["data"] = b"tampered wire"
        send_root["effects"][index] = replay_values.encode((position, effect))
        variants.append(changed)
        changed = copy.deepcopy(document)
        del changed["end"]
        variants.append(changed)
        for changed in variants:
            with self.subTest(case=len(changed)), self.assertRaises(ServerJournalError):
                replay_server_session(changed, resolver=session.registry.resolve)

    def test_unsupported_or_failed_roots_permanently_prevent_successful_finish(self):
        for operation, args in (("start", {}), ("datagram", {"data": b"", "address": [], "now": 1.0})):
            session, _ = self.make()
            with self.assertRaises(ServerJournalError):
                session.apply(operation, args)
            first = session.fault
            self.assertIsInstance(first, ServerJournalError)
            with self.assertRaises(ServerJournalError) as caught:
                session.finish()
            self.assertIs(caught.exception, first)

    def test_real_secure_environment_is_opt_in_and_tape_is_private_in_memory(self):
        session, _ = self.make(source=SystemEnvironment())
        session.create_match(MAP, [])
        document, _, result = self.verify(session)
        self.assertIn("environment", document)
        self.assertEqual(result["send_attempts"], 0)
        document["roots"].clear()
        self.assertEqual(len(session.finish()["roots"]), 1)


if __name__ == "__main__":
    unittest.main()
