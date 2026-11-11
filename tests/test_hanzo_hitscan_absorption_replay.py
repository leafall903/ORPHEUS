"""Launch catalog through match creation, actual encrypted shots and registered state replay."""

import os
import unittest
from dataclasses import replace
from unittest.mock import patch

import test_server_journal as journal_tests
from test_game_combat import aim

from ow174 import cli
from ow174.game import matrix, sigma_grasp
from ow174.game.link import SYN
from ow174.game.server_journal import ServerJournalError, _ReplaySession
from ow174.game.weapon_spread import MCCREE

HANZO = 0x02E0000000000005
STARTUP = "configure_startup_projectile_absorption"


class HitscanAbsorptionReplayTests(unittest.TestCase):
    def test_startup_root_refuses_invalid_or_late_definitions(self):
        fixture = journal_tests.ServerJournalTests()
        guid = 0x04000000000002A7
        for definitions in ((guid, guid), (0x0D600000000002F9,), [guid]):
            with self.subTest(definitions=definitions):
                session, _ = fixture.make()
                with self.assertRaisesRegex(ServerJournalError, "sorted unique 003 GUIDs"):
                    session.apply(STARTUP, {"definitions": definitions})
                self.assertIsNone(session.server.combat_profiles)
        session, _ = fixture.make()
        session.create_match(journal_tests.MAP, [])
        with self.assertRaisesRegex(ServerJournalError, "before the first match"):
            session.apply(STARTUP, {"definitions": (guid,)})
        session, _ = fixture.make()
        session.apply(STARTUP, {"definitions": ()})
        with self.assertRaisesRegex(ServerJournalError, "configured once"):
            session.apply(STARTUP, {"definitions": (guid,)})

    def _actual_round(self, *, stored):
        path = os.environ.get("ORPHEUS_MATRIX_TAGS")
        if not path:
            self.skipTest("prepared build 104319 projectile tags required")
        options = cli.parse_args(["--mode", "server", "--reconstructed-projectile-tags", path])
        definitions = cli._combat_profiles(options).projectile_absorption
        base = journal_tests.MAP
        arena = replace(base, free_for_all=False, team_sizes=(1, 2), spawns=(
            base.spawns[0], replace(base.spawns[1], position=(0, 0, 8)),
            replace(base.spawns[1], position=(0.8, 0, 4)),
        ))
        session, sent = journal_tests.ServerJournalTests().make(verify_stored_state=stored)
        session.apply(STARTUP, {"definitions": tuple(sorted(definitions))})
        heroes = (MCCREE, HANZO, sigma_grasp.SIGMA)
        handoffs = session.create_match(arena, [(1, "McCree", MCCREE, 0, False),
                                                (2, "Hanzo", HANZO, 1, False),
                                                (3, "Sigma", sigma_grasp.SIGMA, 1, False)])
        peers = [journal_tests.Peer(session, h, sent, ("127.0.0.1", 47590 + i))
                 for i, h in enumerate(handoffs)]
        for peer in peers:
            peer.send(flags=SYN)
            peer.send()
            peer.message(21601, {})
        journal_tests.acknowledge_ownership(session, peers)
        session.tick(100.8)
        match = session.server.matches[0]
        shooter, hanzo, sigma = match.players
        for peer, player, hero in zip(peers, match.players, heroes, strict=True):
            peer.message(21616, journal_tests.pick(player.entity, hero), now=100.8)
        fight = match.combat
        self.assertEqual(fight.matrix_definitions, definitions)
        self.assertEqual(tuple(hanzo.mover.position), (0, 0, 8))
        self.assertAlmostEqual(sigma.mover.position[0], 0.8, delta=0.002)
        yaw, pitch = aim(fight._eye(shooter), fight._eye(hanzo))
        peers[0].commands(shooter.body_script.frame + 1, 70, 0, yaw=yaw, pitch=pitch)
        peers[2].commands(sigma.body_script.frame + 1, 70, 0, yaw=-32768)
        peers[2].commands(sigma.body_script.frame + 1, 1, 8, yaw=-32768)
        grasp = next(i for i in sigma.body_script.component.instances.values()
                     if i.graph.index == sigma_grasp.GRAPH)
        self.assertTrue(grasp.states[1].active)
        peers[0].commands(shooter.body_script.frame + 1, 1, 1, yaw=yaw, pitch=pitch)
        peers[0].commands(shooter.body_script.frame + 1, 8, 0, yaw=yaw, pitch=pitch)
        self.assertEqual(grasp.find_var(0, 21312).value(), 70)
        self.assertEqual(fight.vitals[hanzo.body].total, 200)
        self.assertEqual(fight.vitals[sigma.body].total, 400)
        peers[2].commands(sigma.body_script.frame + 1, 130, 0, yaw=-32768)
        self.assertEqual(fight.vitals[sigma.body].bonus_shields, 42)

        document = session.finish()
        self.assertEqual(document["roots"][0]["operation"], STARTUP)
        self.assertFalse(any(root["operation"] == "configure_projectile_absorption" for root in document["roots"]))
        with patch.object(matrix, "load_admitted_definitions", side_effect=AssertionError("catalog reopened")):
            replay = _ReplaySession(document, session.registry.resolve)
            result = replay.run()
        self.assertEqual(result["stored_state_verified"], stored)
        copied = replay.server.matches[0]
        self.assertEqual(copied.combat.matrix_definitions, definitions)
        self.assertEqual(copied.combat.vitals[copied.players[1].body].total, 200)
        self.assertEqual(copied.combat.vitals[copied.players[2].body].bonus_shields, 42)

    def test_raw_wire_replay_preserves_startup_catalog_and_hanzo_protection(self):
        self._actual_round(stored=False)

    def test_registered_state_replay_preserves_startup_catalog_and_hanzo_protection(self):
        self._actual_round(stored=True)
