"""20802: the settings a player gets at login, with defaults for the blocks never saved."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from ow174.accounts.profile import Profile
from ow174.catalog.items import ItemDB
from ow174.catalog.templates import RetailTemplates
from ow174.content import Content
from ow174.jam.codec import Schemas
from ow174.jam.groups import LOBBY


class PlayerSettingsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.schemas = Schemas()
        cls.content = Content(cls.schemas, RetailTemplates(), ItemDB())

    def gameplay_options(self, profile):
        value = self.content.player.settings(profile)
        body = self.schemas.encode(LOBBY, 20802, value)
        return self.schemas.decode(LOBBY, 20802, body)["+0x120"]

    def test_a_new_profile_gets_the_clients_gameplay_option_defaults(self):
        # The kill feed's presenter shows only while byte +0x4 is set (docs/research/kill-feed.md).
        options = self.gameplay_options(Profile())
        self.assertTrue(options["+0x4"])  # Kill Feed Display
        self.assertFalse(options["+0x5"])  # Always Skip Kill Cam
        self.assertTrue(options["+0x7"])  # Tips Display

    def test_saved_gameplay_options_are_sent_as_saved(self):
        saved = dict(self.schemas.empty(LOBBY, 20802)["+0x120"], **{"+0x5": True})
        options = self.gameplay_options(Profile(settings={"+0x120": saved}))
        self.assertFalse(options["+0x4"])  # the player turned the feed off
        self.assertTrue(options["+0x5"])
        self.assertFalse(options["+0x7"])


if __name__ == "__main__":
    unittest.main()
