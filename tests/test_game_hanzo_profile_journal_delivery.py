"""Original0005 profile journal root against the current recording fixture."""
import json
import os
import unittest
from pathlib import Path
import test_server_journal as journal_tests
from ow174.game.server_journal import ServerJournalError, _ReplaySession
MAP = journal_tests.MAP

class DeliveredHanzoProfileJournalTests(unittest.TestCase):
    make = journal_tests.ServerJournalTests.make

    def test_hanzo_profile_root_replays_both_and_rejects_partial_mount(self):
        paths = tuple(os.environ.get(key) for key in ("ORPHEUS_HANZO_SONIC_PROFILE", "ORPHEUS_HANZO_PROFILE"))
        if not all(paths):
            self.skipTest("explicitly extracted Hanzo profiles required")
        sonic_data, dragon_data = (json.loads(Path(path).read_text(encoding="utf-8")) for path in paths)
        session, _ = self.make()
        session.create_match(MAP, [])
        match, = session.server.matches
        args = {"match_id": match.id, "sonic": sonic_data, "dragon": dragon_data}
        session.apply("configure_hanzo_profiles", args)
        self.assertIsNotNone(match.combat.sonic_profile)
        self.assertIsNotNone(match.combat.dragon_profile)
        replay = _ReplaySession(session.finish(), session.registry.resolve)
        replay.run()
        copied, = replay.server.matches
        self.assertEqual(copied.combat.sonic_profile, match.combat.sonic_profile)
        self.assertEqual(copied.combat.dragon_profile, match.combat.dragon_profile)

        bad, _ = self.make()
        bad.create_match(MAP, [])
        match, = bad.server.matches
        with self.assertRaisesRegex(ServerJournalError, "invalid Hanzo profile"):
            bad.apply("configure_hanzo_profiles", {**args, "match_id": match.id, "dragon": {}})
        self.assertIsNone(match.combat.sonic_profile)
        self.assertIsNone(match.combat.dragon_profile)

