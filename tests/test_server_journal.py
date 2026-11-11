"""Controlled roots replay the actual encrypted server and combat paths, without sockets."""

import copy
import dataclasses
import logging
import math
import sys
import unittest
from pathlib import Path
from threading import RLock
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from test_game_combat import aim

from ow174.game import (
    barrier_mesh,
    combat,
    cryo,
    deflection,
    messages,
    reinhardt_barrier,
    replay_values,
    stats,
    symmetra,
    world,
)
from ow174.game.bits import BitWriter
from ow174.game.collision import StaticCollisionProvider, World, flat_world
from ow174.game.content import PRACTICE_RANGE, SOLDIER, Spawn
from ow174.game.environment import SystemEnvironment
from ow174.game.environment_tape import EnvironmentTapeError
from ow174.game.link import (
    ACK,
    FIN,
    KIND_FRAME,
    SYN,
    LinkCipher,
    Packet,
    ReceiveWindow,
    delivery,
    peek_connection,
)
from ow174.game.match import SEND_EVERY
from ow174.game.server import (
    FIRST_TICK,
    HANDOFF_SECONDS,
    PLAYER_BODY_REUSE_SECONDS,
    SILENCE_SECONDS,
    GameServer,
)
from ow174.game.server_journal import (
    RecordingServerSession,
    ServerJournalError,
    _ReplaySession,
    replay_server_session,
)
from ow174.lobby.matchmaker import Found, Matchmaker, Ticket

REAPER = 0x02E0000000000002
MAP = dataclasses.replace(
    PRACTICE_RANGE, name="controlled two-team scene", map_guid=0x0800000000FFF901,
    placeables=(), spawns=(Spawn(0, (0.0, 0.0, 0.0), 0.0), Spawn(1, (0.0, 0.0, 5.0), 180.0)),
    team_sizes=(1, 1),
)


class FixedSource:
    def __init__(self):
        self.counter = 0
        self.monotonic_now = 100.0

    def wall_time(self, *, callsite):
        return 100.0

    def wall_time_ns(self, *, callsite):
        self.counter += 1
        return 100_000_000_000 + self.counter

    def monotonic(self, *, callsite):
        return self.monotonic_now

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
        if callsite.startswith("server.run."):
            return self.scheduler_read(callsite)
        return super().monotonic(callsite=callsite)


def wall_world():
    return World([
        (-20, 0, -20, -20, 0, 20, 20, 0, 20), (-20, 0, -20, 20, 0, 20, 20, 0, -20),
        (-10, 0, 2, 10, 0, 2, 10, 5, 2), (-10, 0, 2, 10, 5, 2, -10, 5, 2),
    ], name="controlled floor and wall")


class Peer:
    def __init__(self, session, handoff, sent, address):
        self.session, self.handoff, self.sent, self.address = session, handoff, sent, address
        self.cipher = LinkCipher(handoff.key_out, handoff.key_in)
        self.window = ReceiveWindow()
        self.seq = self.reliable = self.cursor = 0
        self.last_raw = None

    def drain(self, *, lose=False, omit=()):
        for data, address in self.sent[self.cursor:]:
            if peek_connection(data) != self.handoff.conn:
                continue
            if not lose:
                packet = self.cipher.open(data)
                assert packet is not None and address == self.address, (len(data), address, self.address)
                if packet.seq not in omit:
                    self.window.accept(packet.seq)
        self.cursor = len(self.sent)

    def seal(self, *, flags=ACK, payload=b""):
        packet = Packet(self.handoff.conn, KIND_FRAME if payload else 0, self.seq,
                        self.window.expected, self.window.bits, flags, payload)
        self.seq += 1
        return self.cipher.seal(packet)

    def send(self, *, flags=ACK, payload=b"", now=100.0, address=None):
        self.last_raw = self.seal(flags=flags, payload=payload)
        self.session.datagram(self.last_raw, self.address if address is None else address, now)
        self.drain()

    def message(self, identifier, value, now=100.0):
        payload = BitWriter()
        messages.write_channel(payload, [(self.reliable, messages.encode(identifier, value))])
        self.reliable += 1
        self.send(payload=payload.getvalue(), now=now)

    def commands(self, first, count, buttons, *, forward=0, right=0, yaw=0, pitch=0, now=100.8):
        if count > 32:
            self.commands(first, 32, buttons, forward=forward, right=right, yaw=yaw, pitch=pitch, now=now)
            return self.commands(
                first + 32, count - 32, buttons, forward=forward, right=right, yaw=yaw, pitch=pitch, now=now,
            )
        self.send(payload=self.command_payload(
            first, count, buttons, forward=forward, right=right, yaw=yaw, pitch=pitch,
        ), now=now)

    @staticmethod
    def command_payload(first, count, buttons, *, forward=0, right=0, yaw=0, pitch=0):
        body, payload = BitWriter(), BitWriter()
        body.bits(0, 24)
        body.bits(count, 8)
        body.bits(first, 32)
        body.bit(bool(forward or right))
        if forward or right:
            body.bit(0)  # arbitrary throttles
            body.bit(bool(right))
            if right:
                body.signed(right, 8)
            body.bit(bool(forward))
            if forward:
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
        return payload.getvalue()


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

    def connected(self, session, sent, *, two=True, selected=(SOLDIER, SOLDIER), game_map=MAP):
        names = ("Alpha", "Beta", "Gamma")[:len(selected)]
        entries = [(i + 1, name, hero, int(i != 0), False)
                   for i, (name, hero) in enumerate(zip(names, selected, strict=True))]
        handoffs = session.create_match(game_map, entries if two else entries[:1])
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
        for peer, player in zip(peers, players, strict=True):
            peer.message(21616, pick(player.entity, player.hero.guid), now=100.8)
            self.assertIsNotNone(player.body_script)
        # Actual create/full ACK processing goes through the raw datagram router.
        for tick in range(8):
            session.tick(100.816 + tick * 0.016)
            for peer in peers:
                peer.drain()
                peer.send(now=100.816 + tick * 0.016)
        return peers, players

    def test_late_encrypted_ice_cancel_follows_orb_contact_at_world_checkpoint(self):
        for offset in (-1, 0, 1):
            with self.subTest(cancel_offset_from_contact=offset):
                self.check_late_ice_cancel(offset)

    def ice_orb_peers(self):
        source = SchedulerSource()
        session, sent = self.make(source=source)
        peers, players = self.connected(session, sent, selected=(symmetra.HERO, cryo.HERO))
        return source, session, peers, players

    def release_charged_orb(self, session, sym_peer, sym, *, now=101.0, pitch=0):
        first = sym.body_script.frame + 1
        sym_peer.commands(first, 90, 2, pitch=pitch, now=now)
        sym_peer.commands(first + 90, 1, 0, pitch=pitch, now=now)
        [orb] = sym.match.combat.rockets
        session.tick(now + combat.TICK)
        self.assertIn(orb, sym.match.combat.rockets)
        self.assertEqual(orb.last_tick, session.server.tick)
        return orb

    def receive_late_command(
        self, source, session, peer, frame, button, yaw, pitch, steps, *, now=102.0, count=1, right=0,
    ):
        self.assertGreater(steps, 1)
        payload = peer.command_payload(frame, count, button, yaw=yaw, pitch=pitch, right=right)
        packet = peer.seal(payload=payload)
        due = 10.0 + (steps - 0.5) * combat.TICK
        source.reads = iter((
            ("start", 10.0), ("timeout", 10.0), ("datagram", now),
            ("due", due), ("next_tick", due), ("tick", now),
        ))
        session.run_loop([(packet, peer.address)])

    def check_late_ice_cancel(self, offset):
        source, session, (sym_peer, mei_peer), (sym, mei) = self.ice_orb_peers()
        fight = mei.match.combat
        yaw, pitch = aim(fight._eye(mei), (0, 0, 2.5))
        first = mei.body_script.frame + 1
        for index, button in enumerate((16, 0, 1, 0)):
            mei_peer.commands(first + index, 1, button, yaw=yaw, pitch=pitch, now=101.0)
        self.assertEqual(len(fight.ice_wall.pieces), 5)
        orb = self.release_charged_orb(session, sym_peer, sym)
        distance, pillar, _ = fight.ice_wall.cast(orb.position, orb.direction, 100)
        self.assertEqual(pillar.health, 400)
        contact = orb.last_tick + math.ceil(distance / (orb.speed * combat.TICK))
        checkpoint = contact + offset
        steps = checkpoint - session.server.tick
        cancel = mei.commands.last_frame + 1
        self.receive_late_command(source, session, mei_peer, cancel, 16, yaw, pitch, steps)
        self.assertEqual(pillar.health, 400 if offset < 0 else 280)
        _, replay, _ = self.verify(session)
        for server in (session.server, replay.server):
            match = server.matches[0]
            self.assertEqual(server.tick, checkpoint)
            self.assertEqual(match.players[1].commands.last_frame, cancel)
            self.assertFalse(match.combat.ice_wall.pieces)
            self.assertEqual(len(match.combat.rockets), int(offset < 0))

    def test_late_encrypted_ice_confirmation_does_not_block_an_earlier_orb_hit(self):
        source, session, (sym_peer, mei_peer), (sym, mei) = self.ice_orb_peers()
        fight = mei.match.combat
        yaw, pitch = aim(fight._eye(mei), (0, 0, 2.5))
        first = mei.body_script.frame + 1
        for index, button in enumerate((16, 0)):
            mei_peer.commands(first + index, 1, button, yaw=yaw, pitch=pitch, now=101.0)
        self.assertFalse(fight.ice_wall.pieces)
        self.release_charged_orb(session, sym_peer, sym)
        self.assertEqual(fight.vitals[mei.body].total, 250)
        confirm = mei.commands.last_frame + 1
        self.receive_late_command(source, session, mei_peer, confirm, 1, yaw, pitch, 40)
        _, replay, _ = self.verify(session)
        for server in (session.server, replay.server):
            match = server.matches[0]
            defender = match.players[1]
            fight = match.combat
            self.assertEqual(defender.commands.last_frame, confirm)
            self.assertEqual(fight.vitals[defender.body].total, 130)
            self.assertEqual([piece.health for piece in fight.ice_wall.pieces.values()], [400] * 5)
            self.assertFalse(fight.rockets)

    def test_late_encrypted_ice_input_preserves_omitted_deflect_contacts(self):
        for action in ("early cancel", "late cancel", "late confirmation"):
            with self.subTest(action=action):
                stepped = self.ice_deflect_schedule(action, gap=False)
                self.assertEqual(self.ice_deflect_schedule(action, gap=True), stepped)

    def ice_deflect_schedule(self, action, *, gap):
        source = SchedulerSource()
        session, sent = self.make(source=source)
        game_map = dataclasses.replace(
            MAP, team_sizes=(1, 2), spawns=(
                Spawn(0, (0.0, 0.0, 0.0), 0.0),
                Spawn(1, (5.0, 0.0, 12.0), 180.0),
                Spawn(1, (0.0, 0.0, 20.0), 180.0),
            ),
        )
        (sym_peer, mei_peer, genji_peer), (sym, mei, genji) = self.connected(
            session, sent, selected=(symmetra.HERO, cryo.HERO, deflection.GENJI), game_map=game_map,
        )
        fight = sym.match.combat
        yaw, pitch = aim(fight._eye(mei), (0, 0, 12))
        confirmation = action == "late confirmation"
        first = mei.body_script.frame + 1
        for index, button in enumerate((16, 0) if confirmation else (16, 0, 1, 0)):
            mei_peer.commands(first + index, 1, button, yaw=yaw, pitch=pitch, now=101.0)
        genji_peer.commands(genji.body_script.frame + 1, 1, 16, yaw=-32768, now=101.0)
        self.assertIsNotNone(deflection.active(genji))
        orb = self.release_charged_orb(session, sym_peer, sym)
        start, start_time = fight.tick, fight.now
        end = start + 160
        omitted_end = end - combat.COMBAT_CATCH_UP
        pieces = tuple(fight.ice_wall.pieces.values())
        self.assertEqual(len(pieces), 0 if confirmation else 5)
        checkpoint = end
        early = action == "early cancel"
        if not confirmation:
            distance, pillar, _ = fight.ice_wall.cast(orb.position, orb.direction, 100)
            contact = start + math.ceil(distance / (orb.speed * combat.TICK))
            self.assertLessEqual(contact, omitted_end)
            if early:
                checkpoint = contact - 1
        command = mei.commands.last_frame + 1
        button = 1 if confirmation else 16
        checkpoint_time = start_time + (checkpoint - start) * combat.TICK
        if gap:
            self.receive_late_command(
                source, session, mei_peer, command, button, yaw, pitch, checkpoint - start,
                now=checkpoint_time,
            )
        else:
            for at in range(start + 1, checkpoint + 1):
                session.tick(start_time + (at - start) * combat.TICK)
            mei_peer.commands(command, 1, button, yaw=yaw, pitch=pitch, now=checkpoint_time)
        if early:
            if gap:
                session.tick(start_time + (end - start) * combat.TICK, missed=end - checkpoint - 1)
            else:
                for at in range(checkpoint + 1, end + 1):
                    session.tick(start_time + (at - start) * combat.TICK)
        if not confirmation:
            self.assertEqual(pillar.health, 400 if early else 280)
        self.assertIs(orb.shooter, sym if action == "late cancel" else genji)
        _, replay, _ = self.verify(session)
        reflected = action != "late cancel"
        damage_ticks = []
        for server in (session.server, replay.server):
            owner, wall_owner, defender = server.matches[0].players
            battle = owner.match.combat
            self.assertEqual(server.tick, end)
            self.assertEqual(wall_owner.commands.last_frame, command)
            self.assertIsNone(deflection.active(defender))
            vitals = {target.owner: battle.vitals_of(target) for target in battle._targets()}
            self.assertEqual(vitals[owner].total, 105 if reflected else 225)
            self.assertEqual(vitals[wall_owner].total, 250)
            self.assertEqual(vitals[defender].total, 200)
            self.assertEqual(defender.stats.value(stats.HERO_DAMAGE), 120 if reflected else 0)
            self.assertEqual([piece.health for piece in battle.ice_wall.pieces.values()],
                             [400] * 5 if confirmation else [])
            self.assertFalse(battle.rockets)
            last_damage = vitals[owner].last_damage_at
            if reflected:
                damage_tick = start + round((last_damage - start_time) / combat.TICK)
                self.assertLessEqual(damage_tick, omitted_end)
                damage_ticks.append(damage_tick)
            else:
                self.assertIsNone(last_damage)
        if reflected:
            recorded_tick, replayed_tick = damage_ticks
            self.assertEqual(replayed_tick, recorded_tick)
            return recorded_tick
        return None

    def test_encrypted_orb_release_around_catchup_keeps_the_new_flight_separate(self):
        released_before = self.orb_release_around_catchup("before")
        released_during = self.orb_release_around_catchup("during")
        self.assertEqual(self.orb_release_around_catchup("after"), released_during)
        older_impact, newer_impact = released_during
        self.assertEqual(released_before, (older_impact, newer_impact - 1))

    def orb_release_around_catchup(self, arrival):
        source = SchedulerSource()
        session, sent = self.make(source=source)
        game_map = dataclasses.replace(MAP, spawns=(MAP.spawns[0], Spawn(1, (0, 0, 20), 180.0)))
        (sym_peer, genji_peer), (sym, genji) = self.connected(
            session, sent, selected=(symmetra.HERO, deflection.GENJI), game_map=game_map,
        )
        genji_peer.commands(genji.body_script.frame + 1, 1, 16, yaw=-32768, now=101.0)
        self.assertIsNotNone(deflection.active(genji))
        old_orb = self.release_charged_orb(session, sym_peer, sym)
        fight = sym.match.combat
        start, start_time = fight.tick, fight.now
        steps = 160
        endpoint = start + steps
        endpoint_time = start_time + steps * combat.TICK
        first = sym.commands.last_frame + 1
        sym_peer.commands(first, 32, 0, now=start_time)
        sym_peer.commands(first + 32, 90, 2, now=start_time)
        self.assertEqual(fight.rockets, [old_orb])
        release = sym.commands.last_frame + 1
        if arrival == "before":
            sym_peer.commands(release, 1, 0, now=start_time)
            self.assertEqual(len(fight.rockets), 2)
            self.assertIsNone(fight.rockets[-1].last_tick)
        if arrival == "during":
            self.receive_late_command(source, session, sym_peer, release, 0, 0, 0, steps, now=endpoint_time)
        else:
            session.tick(endpoint_time, missed=steps - 1)
            if arrival == "after":
                self.assertFalse(fight.rockets)
                sym_peer.commands(release, 1, 0, now=endpoint_time)

        self.assertEqual(len(fight.rockets), 1, "the new orb should still be in flight at the checkpoint")
        [new_orb] = fight.rockets
        self.assertIs(old_orb.shooter, genji)
        self.assertIs(new_orb.shooter, sym)
        self.assertIsNone(deflection.active(genji))
        first_flight_tick = endpoint if arrival == "before" else endpoint + 1
        if arrival == "before":
            self.assertEqual(new_orb.last_tick, endpoint)
        else:
            self.assertIsNone(new_orb.last_tick)
            self.assertEqual(new_orb.position, fight._eye(sym))
            session.tick(endpoint_time + combat.TICK)
        self.assertEqual(new_orb.last_tick, first_flight_tick)
        self.assertAlmostEqual(math.dist(new_orb.position, fight._eye(sym)), new_orb.speed * combat.TICK)
        self.assertEqual(fight.vitals[sym.body].total, 105)
        self.assertEqual(genji.stats.value(stats.HERO_DAMAGE), 120)
        self.assertEqual(sym.stats.value(stats.HERO_DAMAGE), 0)
        remaining_distance = math.dist(new_orb.position, fight._eye(genji))
        remaining_ticks = math.ceil(remaining_distance / (new_orb.speed * combat.TICK))
        for at in range(first_flight_tick + 1, first_flight_tick + remaining_ticks + 2):
            session.tick(start_time + (at - start) * combat.TICK)
            if not fight.rockets:
                break
        self.assertFalse(fight.rockets)
        _, replay, _ = self.verify(session)
        impacts = []
        for server in (session.server, replay.server):
            owner, defender = server.matches[0].players
            battle = owner.match.combat
            self.assertEqual(owner.commands.last_frame, release)
            self.assertFalse(battle.rockets)
            self.assertEqual(battle.vitals[owner.body].total, 105)
            self.assertEqual(battle.vitals[defender.body].total, 80)
            self.assertEqual(owner.stats.value(stats.HERO_DAMAGE), 120)
            self.assertEqual(defender.stats.value(stats.HERO_DAMAGE), 120)
            hit_ticks = tuple(
                start + round((battle.vitals[player.body].last_damage_at - start_time) / combat.TICK)
                for player in (owner, defender)
            )
            self.assertLess(hit_ticks[0], endpoint - combat.COMBAT_CATCH_UP + 1)
            self.assertGreater(hit_ticks[1], first_flight_tick)
            impacts.append(hit_ticks)
        self.assertEqual(impacts[0], impacts[1])
        return impacts[0]

    def test_long_scheduler_stall_preserves_old_orb_and_defers_encrypted_release(self):
        for distance, keepalive in ((624, True), (626, True), (624, False)):
            with self.subTest(distance=distance, keepalive=keepalive):
                self.orb_across_long_scheduler_stall(distance, keepalive=keepalive)

    def test_encrypted_player_strafe_orders_historical_orb_body_contact(self):
        for entering in (False, True):
            for before_contact in (False, True):
                with self.subTest(entering=entering, before_contact=before_contact):
                    args = (entering, before_contact)
                    stepped = self.orb_across_player_strafe(*args, gap=False)
                    self.assertEqual(self.orb_across_player_strafe(*args, gap=True), stepped)

    def orb_across_player_strafe(self, entering, before_contact, *, gap):
        source = SchedulerSource()
        session, sent = self.make(source=source)
        initial_x = 2.0 if entering else 0.0
        game_map = dataclasses.replace(
            MAP, spawns=(MAP.spawns[0], Spawn(1, (initial_x, 0, 12), 180.0)),
        )
        (sym_peer, target_peer), (sym, target) = self.connected(
            session, sent, selected=(symmetra.HERO, SOLDIER), game_map=game_map,
        )
        fight = sym.match.combat
        _, pitch = aim(fight._eye(sym), (0, 0.7, 12))
        orb = self.release_charged_orb(session, sym_peer, sym, pitch=pitch)
        start, start_time = fight.tick, fight.now
        starting_charge = sym.body_script.component.entity_var(342).value()
        end = start + 180
        checkpoint = start + 20 if before_contact else end
        checkpoint_time = start_time + (checkpoint - start) * combat.TICK
        first = target.body_script.frame + 1
        count = 24
        right = -127 if entering else 127
        if gap:
            self.receive_late_command(
                source, session, target_peer, first, 0, -32768, 0, checkpoint - start,
                now=checkpoint_time, count=count, right=right,
            )
            if checkpoint < end:
                session.tick(start_time + (end - start) * combat.TICK, missed=end - checkpoint - 1)
        else:
            for at in range(start + 1, end + 1):
                session.tick(start_time + (at - start) * combat.TICK)
                if at == checkpoint:
                    target_peer.commands(first, count, 0, yaw=-32768, right=right, now=checkpoint_time)

        self.assertFalse(fight.rockets)
        self.assertIs(orb.shooter, sym)
        self.assertLess(orb.last_tick, end - combat.COMBAT_CATCH_UP + 1)
        pose = tuple(target.mover.position)
        self.assertGreater(abs(pose[0] - initial_x), 1.5)
        self.assertAlmostEqual(pose[2], 12.0)
        body = next(body for body in fight._targets() if body.owner is target)
        a, b = body.segment()
        ray = combat.ray_capsule(fight._eye(sym), orb.direction, a, b, body.radius)
        self.assertIs(ray is not None, entering)
        hit = entering == before_contact
        damage = 120 if hit else 0
        charge_gain = sym.body_script.component.entity_var(342).value() - starting_charge
        self.assertLessEqual(damage + 10, charge_gain)
        self.assertLessEqual(charge_gain, damage + 15)
        replay = self.replay_with_landing(session, orb.last_tick)
        for server in (session.server, replay.server):
            owner, defender = server.matches[0].players
            battle = owner.match.combat
            self.assertEqual(server.tick, end)
            self.assertEqual(defender.commands.last_frame, first + count - 1)
            self.assertEqual(tuple(defender.mover.position), pose)
            self.assertEqual(owner.stats.value(stats.HERO_DAMAGE), damage)
            replayed_charge = owner.body_script.component.entity_var(342).value() - starting_charge
            self.assertEqual(replayed_charge, charge_gain)
            self.assertEqual(defender.stats.value(stats.HERO_DAMAGE), 0)
            body = next(body for body in battle._targets() if body.owner is defender)
            vitals = battle.vitals_of(body)
            self.assertEqual(vitals.total, 80 if hit else 200)
            if hit:
                impact_time = start_time + (orb.last_tick - start) * combat.TICK
                self.assertAlmostEqual(vitals.last_damage_at, impact_time)
            else:
                self.assertIsNone(vitals.last_damage_at)
        return orb.last_tick, pose, charge_gain

    def orb_across_long_scheduler_stall(self, distance, *, keepalive):
        source = SchedulerSource()
        session, sent = self.make(source=source)
        game_map = dataclasses.replace(MAP, spawns=(MAP.spawns[0], Spawn(1, (0, 0, distance), 180.0)))
        (sym_peer, genji_peer), (sym, genji) = self.connected(
            session, sent, selected=(symmetra.HERO, deflection.GENJI), game_map=game_map,
        )
        genji_peer.commands(genji.body_script.frame + 1, 1, 16, yaw=-32768, now=101.0)
        self.assertIsNotNone(deflection.active(genji))
        old_orb = self.release_charged_orb(session, sym_peer, sym)
        fight = sym.match.combat
        start, start_time = fight.tick, fight.now
        target = next(target for target in fight._targets() if target.owner is genji)
        a, b = target.segment()
        contact = combat.ray_capsule(old_orb.position, old_orb.direction, a, b, target.radius)
        self.assertIsNotNone(contact)
        hit = contact < old_orb.left * old_orb.speed
        self.assertEqual(hit, distance == 624)
        flight_time = min(contact / old_orb.speed, old_orb.left)
        flight_end = start + math.ceil(flight_time / combat.TICK)

        steps = 1880
        checkpoint = start + steps
        checkpoint_time = start_time + steps * combat.TICK
        self.assertGreater(steps * combat.TICK, SILENCE_SECONDS)
        self.assertLess(flight_end, checkpoint - combat.COMBAT_CATCH_UP + 1)
        first = sym.commands.last_frame + 1
        sym_peer.commands(first, 32, 0, now=start_time)
        sym_peer.commands(first + 32, 90, 2, now=start_time)
        self.assertEqual(fight.rockets, [old_orb])
        release = sym.commands.last_frame + 1
        if keepalive:
            genji_peer.drain()
            genji_peer.send(now=checkpoint_time)
        self.receive_late_command(source, session, sym_peer, release, 0, 0, 0, steps, now=checkpoint_time)
        self.assertEqual(session.server.tick, checkpoint)
        self.assertEqual(old_orb.last_tick, flight_end)
        self.assertIs(old_orb.shooter, sym)
        self.assertNotIn(old_orb, fight.rockets)
        [fresh] = fight.rockets
        self.assertIs(fresh.shooter, sym)
        self.assertIsNone(fresh.last_tick)
        self.assertEqual(fresh.position, fight._eye(sym))
        session.tick(checkpoint_time + combat.TICK)
        self.assertEqual(fresh.last_tick, checkpoint + 1)
        self.assertAlmostEqual(math.dist(fresh.position, fight._eye(sym)), fresh.speed * combat.TICK)

        replay = self.replay_with_landing(session, flight_end)
        for server in (session.server, replay.server):
            owner, defender = server.matches[0].players
            battle = owner.match.combat
            self.assertEqual(server.tick, checkpoint + 1)
            self.assertEqual(owner.commands.last_frame, release)
            self.assertIsNotNone(owner.client)
            self.assertEqual(owner.client.heard, checkpoint_time)
            self.assertEqual(defender.gone, not keepalive)
            self.assertEqual(genji_peer.handoff.conn in server.clients, keepalive)
            self.assertIsNone(deflection.active(defender))
            self.assertEqual(owner.stats.value(stats.HERO_DAMAGE), 120 if hit else 0)
            self.assertEqual(defender.stats.value(stats.HERO_DAMAGE), 0)
            [replayed_flight] = battle.rockets
            self.assertIs(replayed_flight.shooter, owner)
            self.assertEqual(replayed_flight.last_tick, checkpoint + 1)
            self.assertEqual(replayed_flight.position, fresh.position)
            if keepalive:
                defender_target = next(target for target in battle._targets() if target.owner is defender)
                vitals = battle.vitals_of(defender_target)
                self.assertEqual(vitals.total, 80 if hit else 200)
                if hit:
                    impact_time = start_time + (flight_end - start) * combat.TICK
                    self.assertAlmostEqual(vitals.last_damage_at, impact_time)
                else:
                    self.assertIsNone(vitals.last_damage_at)
        return flight_end, checkpoint + 1

    def replay_with_landing(self, session, expected_tick):
        landings = []
        land = combat.Combat._landed

        def observe_landing(battle, orb):
            landings.append(orb.last_tick)
            return land(battle, orb)

        with mock.patch.object(combat.Combat, "_landed", observe_landing):
            _, replay, _ = self.verify(session)
        self.assertEqual(landings, [expected_tick])
        return replay

    @unittest.skipIf(barrier_mesh.DESCRIPTOR_SHA256 is None, "requires the build 104319 shield mesh")
    def test_encrypted_reinhardt_rotation_orders_omitted_orb_and_deflect_contacts(self):
        for turn_before_contact in (True, False):
            for open_after_turn in (True, False):
                with self.subTest(turn_before_contact=turn_before_contact, open_after_turn=open_after_turn):
                    args = (turn_before_contact, open_after_turn)
                    stepped = self.orb_across_reinhardt_cover(*args, gap=False)
                    self.assertEqual(self.orb_across_reinhardt_cover(*args, gap=True), stepped)

    @unittest.skipIf(barrier_mesh.DESCRIPTOR_SHA256 is None, "requires the build 104319 shield mesh")
    def test_encrypted_reinhardt_strafe_orders_omitted_orb_and_deflect_contacts(self):
        for move_before_contact in (True, False):
            for open_after_move in (True, False):
                with self.subTest(move_before_contact=move_before_contact, open_after_move=open_after_move):
                    args = (move_before_contact, open_after_move)
                    stepped = self.orb_across_reinhardt_cover(*args, gap=False, translate=True)
                    skipped = self.orb_across_reinhardt_cover(*args, gap=True, translate=True)
                    self.assertEqual(skipped, stepped)

    def orb_across_reinhardt_cover(self, before_contact, open_after, *, gap, translate=False):
        source = SchedulerSource()
        session, sent = self.make(source=source)
        closed_x = 3.45 if translate else 1.6
        initial_x = 4.2 if translate and not open_after else closed_x
        game_map = dataclasses.replace(
            MAP, team_sizes=(1, 2), spawns=(
                MAP.spawns[0], Spawn(1, (initial_x, 0, 12), 180.0), Spawn(1, (0, 0, 20), 180.0),
            ),
        )
        (sym_peer, rein_peer, genji_peer), (sym, rein, genji) = self.connected(
            session, sent, selected=(symmetra.HERO, reinhardt_barrier.HERO, deflection.GENJI),
            game_map=game_map,
        )
        closed_yaw, open_yaw = -32768, 16384
        if translate:
            initial_yaw = final_yaw = closed_yaw
        else:
            initial_yaw, final_yaw = ((closed_yaw, open_yaw) if open_after else (open_yaw, closed_yaw))
        first = rein.body_script.frame + 1
        rein_peer.commands(first, 20, 0, yaw=initial_yaw, now=101.0)
        rein_peer.commands(first + 20, 10, 2, yaw=initial_yaw, now=101.0)
        genji_peer.commands(genji.body_script.frame + 1, 1, 16, yaw=closed_yaw, now=101.0)
        self.assertIsNotNone(deflection.active(genji))
        orb = self.release_charged_orb(session, sym_peer, sym)
        fight = sym.match.combat
        shields = fight.reinhardt_barriers
        state = shields.states[rein.body]
        self.assertTrue(state.active)
        self.assertEqual(state.current, 1600)
        ray_origin, ray_direction = orb.position, orb.direction
        closed_feet = (closed_x, *rein.mover.position[1:])
        closed_distance = barrier_mesh.intersect(
            shields.geometry, closed_feet, closed_yaw, 0, ray_origin, ray_direction, 100,
            owner_radius=rein.mover.values.stand_radius, owner_height=rein.mover.values.stand_height,
        )
        self.assertIsNotNone(closed_distance)
        self.assertIs(shields.intersect(rein, ray_origin, ray_direction, 100) is not None, open_after)
        start, start_time = fight.tick, fight.now
        end = start + 160
        first_retained = end - combat.COMBAT_CATCH_UP + 1
        contact = start + math.ceil(closed_distance / (orb.speed * combat.TICK))
        self.assertLess(contact, first_retained)
        lead = 3 if translate else 1
        checkpoint = contact - lead if before_contact else end
        checkpoint_time = start_time + (checkpoint - start) * combat.TICK
        command = rein.commands.last_frame + 1
        count = 24 if translate else 1
        right = (127 if open_after else -127) if translate else 0
        if gap:
            self.receive_late_command(
                source, session, rein_peer, command, 2, final_yaw, 0, checkpoint - start, now=checkpoint_time,
                count=count, right=right,
            )
            if checkpoint < end:
                session.tick(start_time + (end - start) * combat.TICK, missed=end - checkpoint - 1)
        else:
            for at in range(start + 1, end + 1):
                session.tick(start_time + (at - start) * combat.TICK)
                if at == checkpoint:
                    rein_peer.commands(command, count, 2, right=right, yaw=final_yaw, now=checkpoint_time)

        reflected = before_contact == open_after
        self.assertIs(orb.shooter, genji if reflected else sym)
        _, replay, _ = self.verify(session)
        impact_ticks = []
        final_pose = tuple(rein.mover.position)
        for server in (session.server, replay.server):
            owner, tank, defender = server.matches[0].players
            battle = owner.match.combat
            vitals = {target.owner: battle.vitals_of(target) for target in battle._targets()}
            barriers = battle.reinhardt_barriers
            self.assertEqual(tank.commands.last_frame, command + count - 1)
            self.assertEqual(tank.mover.yaw, final_yaw)
            self.assertEqual(tuple(tank.mover.position), final_pose)
            if translate:
                delta = tank.mover.position[0] - initial_x
                self.assertGreater(delta if open_after else -delta, 0.5)
                self.assertAlmostEqual(tank.mover.position[2], 12.0)
            self.assertIsNone(deflection.active(defender))
            self.assertTrue(barriers.states[tank.body].active)
            self.assertEqual(barriers.states[tank.body].current, 1600 if reflected else 1480)
            self.assertIs(barriers.intersect(tank, ray_origin, ray_direction, 100) is None, open_after)
            self.assertEqual(vitals[tank].total, 500)
            self.assertEqual(vitals[defender].total, 200)
            self.assertEqual(vitals[owner].total, 105 if reflected else 225)
            self.assertEqual(defender.stats.value(stats.HERO_DAMAGE), 120 if reflected else 0)
            self.assertFalse(battle.rockets)
            if reflected:
                tick = start + round((vitals[owner].last_damage_at - start_time) / combat.TICK)
                self.assertLess(tick, first_retained)
                impact_ticks.append(tick)
        if reflected:
            recorded, replayed = impact_ticks
            self.assertEqual(recorded, replayed)
            return recorded, final_pose
        return None, final_pose

    def test_omitted_lethal_orb_respawn_holds_old_body_until_encrypted_destroy_ack(self):
        source, session, peers, players = self.respawn_peers()
        sym_peer, genji_peer = peers
        sym, genji = players
        old_body = genji.body
        self.assertTrue(all(not player.client.body_id_available(old_body) for player in players))
        start, start_time, replacement_tick = self.omitted_lethal_orb(session, peers, players, now=101.0)
        fight = sym.match.combat
        end = replacement_tick + 72
        now = start_time + (end - start) * combat.TICK
        session.tick(now, missed=end - fight.tick - 1)
        new_body = genji.body
        self.assertNotEqual(new_body, old_body)
        self.assertEqual(fight.replacement_ticks[new_body], replacement_tick)
        self.assertNotIn(old_body, fight.early_projectile_respawns)
        self.assertNotIn(old_body, fight.vitals)
        self.assertFalse(fight.respawns)
        self.deliver_body_destroy_after_loss(source, session, sym_peer, genji_peer, old_body, now)
        self.check_respawn_destroy_ack(source, session, sym_peer, genji_peer, old_body, new_body)

    def respawn_peers(self):
        source = FixedSource()
        session, sent = self.make(source=source)
        game_map = dataclasses.replace(MAP, spawns=(MAP.spawns[0], Spawn(1, (0, 0, 54), 180.0)))
        peers, players = self.connected(
            session, sent, selected=(symmetra.HERO, deflection.GENJI), game_map=game_map,
        )
        return source, session, peers, players

    def omitted_lethal_orb(self, session, peers, players, *, now):
        sym_peer, genji_peer = peers
        sym, genji = players
        fight = sym.match.combat
        old_body = genji.body
        self.release_charged_orb(session, sym_peer, sym, now=now)
        primed_at = fight.now + 140 * combat.TICK
        session.tick(primed_at, missed=139)
        corpse = fight.vitals[old_body]
        self.assertEqual(corpse.total, 80)
        self.assertFalse(fight.rockets)
        genji_peer.commands(max(genji.body_script.frame, fight.tick) + 1, 1, 16, yaw=-32768, now=primed_at)
        self.assertIsNotNone(deflection.active(genji))
        first = sym.commands.last_frame + 1
        sym_peer.commands(first, 32, 0, now=primed_at)
        sym_peer.commands(first + 32, 90, 2, now=primed_at)
        sym_peer.commands(sym.commands.last_frame + 1, 1, 0, now=primed_at)
        [orb] = fight.rockets
        session.tick(primed_at + combat.TICK)
        start, start_time = fight.tick, fight.now
        self.assertEqual(orb.last_tick, start)
        death_checkpoint = start + 220
        death_checkpoint_time = start_time + 220 * combat.TICK
        session.tick(death_checkpoint_time, missed=219)
        self.assertIsNone(deflection.active(genji))
        self.assertIs(orb.shooter, sym)
        self.assertEqual(corpse.total, 0)
        self.assertEqual(corpse.killer, sym.body)
        self.assertIn(old_body, fight.early_projectile_respawns)
        self.assertGreater(corpse.death_tick, start + 125)
        self.assertLess(corpse.death_tick, death_checkpoint - combat.COMBAT_CATCH_UP + 1)
        self.assertEqual(genji.body, old_body)
        [(due, victim)] = fight.respawns
        self.assertIs(victim, genji)
        self.assertAlmostEqual(due - corpse.died_at, combat.respawn_seconds(genji.match))
        replacement_tick = start + math.ceil((due - start_time) / combat.TICK - 1e-9)
        for peer in peers:
            peer.drain()
            peer.send(now=death_checkpoint_time)
        return start, start_time, replacement_tick

    def deliver_body_destroy_after_loss(self, source, session, observer, owner, old_body, now, *, delay=0.0):
        observer_link = session.server.clients[observer.handoff.conn]
        lost = set()
        withheld = set()
        delivered = set()
        reported_loss = False
        accept_after = source.monotonic_now + delay
        for _ in range(40):
            source.monotonic_now += combat.TICK
            destroy = {seq for seq, (_, updates) in observer_link.in_flight.items()
                       if any(item.entity == old_body and item.op == world.OP_DESTROY for item in updates)}
            if not lost and destroy:
                lost = destroy
            if source.monotonic_now < accept_after:
                withheld.update(destroy)
            omitted = lost | withheld
            delivered.update(destroy - omitted)
            observer.drain(omit=omitted)
            owner.drain()
            reported_loss |= any(
                delivery(seq, observer.window.expected, observer.window.bits) is False for seq in lost
            )
            self.assertIsNone(observer_link._player_body_reuse_after[old_body])
            observer.send(now=now)
            owner.send(now=now)
            if observer_link._player_body_reuse_after[old_body] is not None:
                break
            now += combat.TICK
            session.tick(now)

        self.assertEqual(len(lost), 1)
        self.assertTrue(reported_loss)
        self.assertTrue(delivered)
        self.assertTrue(all(seq > max(lost) for seq in delivered))
        return now

    def check_respawn_destroy_ack(self, source, session, observer, owner, old_body, new_body):
        ready = source.monotonic_now + PLAYER_BODY_REUSE_SECONDS
        _, replay, _ = self.verify(session)
        for server in (session.server, replay.server):
            match = server.matches[0]
            genji = match.players[1]
            self.assertEqual(genji.body, new_body)
            self.assertIsNotNone(genji.body_script)
            target = next(target for target in match.combat._targets() if target.owner is genji)
            self.assertEqual(match.combat.vitals_of(target).total, 200)
            observed, owned = (server.clients[peer.handoff.conn] for peer in (observer, owner))
            self.assertEqual(observed._player_body_reuse_after[old_body], ready)
            self.assertIsNotNone(owned._player_body_reuse_after[old_body])
            self.assertLess(owned._player_body_reuse_after[old_body], ready)
            self.assertFalse(observed.body_id_available(new_body))
            self.assertFalse(owned.body_id_available(new_body))
            for clock, available in ((ready - combat.TICK, False), (ready, True)):
                with mock.patch.object(server.environment, "monotonic", return_value=clock):
                    self.assertIs(observed.body_id_available(old_body), available)

    def test_omitted_respawn_retry_waits_for_both_peers_to_release_wrapped_id(self):
        source, session, peers, players = self.respawn_peers()
        observer, owner = peers
        sym, genji = players
        fight = sym.match.combat
        recyclable = genji.body
        switch = {"account": genji.account_lo, "hero": deflection.GENJI}
        self.assertTrue(session.apply("switch_hero", switch))
        ack_time = self.deliver_body_destroy_after_loss(
            source, session, observer, owner, recyclable, 101.0, delay=0.5,
        )
        ack_monotonic = source.monotonic_now
        ready = ack_monotonic + PLAYER_BODY_REUSE_SECONDS
        self.assertEqual(sym.client._player_body_reuse_after[recyclable], ready)
        owner_ready = genji.client._player_body_reuse_after[recyclable]
        self.assertLess(owner_ready + combat.RESPAWN_RETRY + combat.TICK, ready)
        for _ in range(253):
            self.assertTrue(session.apply("switch_hero", switch))
        reserved = {entity & 0xFF for entity in genji.client._player_body_reuse_after
                    if entity & ~0xFF == genji.entity}
        self.assertEqual(reserved, set(range(1, 256)))
        self.assertFalse(session.apply("switch_hero", switch))
        corpse = genji.body

        source.monotonic_now = ack_monotonic + combat.TICK
        start, start_time, first_attempt = self.omitted_lethal_orb(
            session, peers, players, now=ack_time + combat.TICK,
        )
        seconds_to_expiry = ack_time + PLAYER_BODY_REUSE_SECONDS - start_time
        expiry_tick = start + math.ceil(seconds_to_expiry / combat.TICK - 1e-9)
        retry_ticks = math.ceil(combat.RESPAWN_RETRY / combat.TICK)
        eligible = first_attempt + math.ceil((expiry_tick - first_attempt) / retry_ticks) * retry_ticks
        previous_retry_time = start_time + (eligible - retry_ticks - start) * combat.TICK
        previous_retry_clock = ack_monotonic + (previous_retry_time - ack_time)
        self.assertTrue(genji.client.body_id_available(recyclable, at=previous_retry_clock))
        self.assertFalse(sym.client.body_id_available(recyclable, at=previous_retry_clock))
        last_before = expiry_tick - 55
        keepalive_checkpoints = range(first_attempt + 350, last_before, 350)
        for checkpoint in (first_attempt, *keepalive_checkpoints, last_before, eligible + 72):
            now = start_time + (checkpoint - start) * combat.TICK
            source.monotonic_now = ack_monotonic + (now - ack_time)
            for peer in peers:
                peer.drain()
                peer.send(now=now)
            session.tick(now, missed=checkpoint - fight.tick - 1)
            if checkpoint < expiry_tick:
                self.assertEqual(genji.body, corpse)
                self.assertTrue(fight.is_dead(genji))
                self.assertIn(corpse, fight.early_projectile_respawns)
                [(retry_at, retrying)] = fight.respawns
                self.assertIs(retrying, genji)
                self.assertGreater(retry_at, now)

        self.assertEqual(genji.body, recyclable)
        self.assertEqual(fight.replacement_ticks[recyclable], eligible)
        _, replay, _ = self.verify(session)
        for server in (session.server, replay.server):
            match = server.matches[0]
            new_genji = match.players[1]
            self.assertEqual(new_genji.body, recyclable)
            self.assertEqual(match.combat.replacement_ticks[recyclable], eligible)
            self.assertFalse(match.combat.respawns)
            self.assertNotIn(corpse, match.combat.early_projectile_respawns)
            target = next(target for target in match.combat._targets() if target.owner is new_genji)
            self.assertEqual(match.combat.vitals_of(target).total, 200)

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
