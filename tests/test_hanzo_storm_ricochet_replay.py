"""Registered-state replay of a Storm Arrow from encrypted commands."""

import json
import os
import unittest
from dataclasses import replace
from pathlib import Path

import test_server_journal as journal_tests
from test_game_combat import aim
from test_game_mover import box, wall

from ow174.game import combat, hanzo_control
from ow174.game.collision import World
from ow174.game.content import SOLDIER
from ow174.game.link import SYN
from ow174.game.server_journal import _ReplaySession


class StormRicochetReplayTests(unittest.TestCase):
    def test_encrypted_ricochet_preserves_in_flight_and_terminal_registered_state(self):
        self.check_replay()

    def test_encrypted_sonic_storm_ricochet_preserves_registered_beacon(self):
        path = os.environ.get("ORPHEUS_HANZO_SONIC_PROFILE")
        if not path:
            self.skipTest("requires explicitly extracted Sonic profile")
        sonic_data = json.loads(Path(path).read_text(encoding="utf-8"))
        self.check_replay(sonic_data=sonic_data)

    def check_replay(self, *, sonic_data=None):
        combined = sonic_data is not None
        arena = replace(journal_tests.MAP, free_for_all=False, spawns=(
            journal_tests.MAP.spawns[0],
            replace(journal_tests.MAP.spawns[1], position=(7, 0, 1)),
        ))
        world = World(box(-25, 25, -1, 0, -25, 25) + wall(-10, 4, 15, 4),
                      name="Storm Arrow bank shot")
        session, sent = journal_tests.ServerJournalTests().make(world, verify_stored_state=True)
        picks = hanzo_control.HANZO, SOLDIER
        handoffs = session.create_match(arena, [
            (1, "Hanzo", picks[0], 0, False), (2, "Target", picks[1], 1, False),
        ])
        match, = session.server.matches
        if combined:
            session.apply("configure_hanzo_profiles", {"match_id": match.id, "sonic": sonic_data, "dragon": None})
        peers = [journal_tests.Peer(session, handoff, sent, ("127.0.0.1", 47440 + index))
                 for index, handoff in enumerate(handoffs)]
        for peer in peers:
            peer.send(flags=SYN)
            peer.send()
            peer.message(21601, {})
        journal_tests.acknowledge_ownership(session, peers)
        now = 100.8
        session.tick(now)
        hanzo, target = match.players
        for peer, player, hero in zip(peers, match.players, picks, strict=True):
            peer.message(21616, journal_tests.pick(player.entity, hero), now=now)
        fight = match.combat
        yaw, pitch = aim(fight._eye(hanzo), (4, 1.25, 4))
        peer = peers[0]
        ability_buttons = 24 if combined else 16
        for count, buttons in ((20, 0), (1, ability_buttons), (1, 0), (28, 1), (1, 0)):
            peer.commands(hanzo.body_script.frame + 1, count, buttons, yaw=yaw, pitch=pitch, now=now)
        rocket, = fight.rockets
        definition = 0x0400000000000149 if combined else 0x040000000000270F
        self.assertEqual(rocket.shot.definition, definition)
        self.assertEqual(rocket.direct.amount, 70)
        self.assertEqual(rocket.sonar is not None, combined)
        self.assertEqual(rocket.storm_ricochets, 1)
        self.assertEqual(rocket.range_left, 1000)
        shot = rocket.shot

        for _ in range(8):
            now += combat.TICK
            session.tick(now)
            if rocket.storm_ricochets == 0:
                break
        self.assertIn(rocket, fight.rockets)
        self.assertEqual(rocket.storm_ricochets, 0)
        self.assertIs(rocket.shot, shot)
        self.assertLess(rocket.direction[2], 0)
        self.assertAlmostEqual(rocket.range_left + rocket.distance_flown, 1000)
        self.assertFalse(fight.sonars)

        now += 12 * combat.TICK
        session.tick(now, missed=11)
        self.assertFalse(fight.rockets)
        self.assertEqual(fight.vitals[target.body].total, 130)
        self.assertEqual(rocket.source_entity, hanzo.body)

        if combined:
            beacon, = fight.sonars
            self.assertEqual(beacon.source_entity, hanzo.body)
            self.assertEqual(beacon.attached, target.body)
            self.assertTrue(fight.revealed_to(hanzo, target))
        else:
            self.assertFalse(fight.sonars)

        document = session.finish()
        self.assertEqual(document["state_profile"], "registered-domain-v1")
        replay = _ReplaySession(document, session.registry.resolve)
        self.assertTrue(replay.run()["stored_state_verified"])
        copied = replay.server.matches[0]
        self.assertFalse(copied.combat.rockets)
        copied_hanzo, copied_target = copied.players
        self.assertEqual(copied.combat.vitals[copied_target.body].total, 130)
        if combined:
            copied_beacon, = copied.combat.sonars
            self.assertEqual(copied_beacon.source_entity, copied_hanzo.body)
            self.assertEqual(copied_beacon.attached, copied_target.body)
            self.assertEqual(copied_beacon.offset, beacon.offset)
            self.assertTrue(copied.combat.revealed_to(copied_hanzo, copied_target))
