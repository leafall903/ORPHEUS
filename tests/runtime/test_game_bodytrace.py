"""The live command path records observational traces without changing gameplay."""

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import test_game_combat as combat_tests

from ow174.game import bodytrace, native_replay, projectiles
from ow174.game.commands import FIRE, Command


class BodyTraceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name) / "traces"
        for key, value in (("ENABLED", True), ("FOLDER", self.folder)):
            patch = mock.patch.object(bodytrace, key, value)
            patch.start()
            self.addCleanup(patch.stop)

    def read(self):
        paths = sorted(self.folder.glob("*.jsonl"))
        return [
