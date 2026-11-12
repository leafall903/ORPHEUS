"""Prepared integration input selection precedes the unchanged server entry."""

import json
import os
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

from tools import start_prepared_server as entry


class PreparedServerEntryTests(unittest.TestCase):
    def profile(self, directory, **changes):
        record = {"version": 1, "environment": {"ORPHEUS_RUNTIME_DIR": "original-runtime"},
                  "server_cli_arguments": ["--reconstructed-hanzo-sonic-profile", "original-sonic.json"],
                  "test_environment": {"ORPHEUS_FIXTURE_ONLY": "not-runtime"}}
        record.update(changes)
        path = Path(directory) / "environment.json"
        path.write_text(json.dumps(record), encoding="utf-8")
        return path

    def test_existing_cli_receives_original_inputs_and_arguments_without_fixture_environment(self):
        received = []
        server = types.ModuleType("ow174.cli")
        server.main = lambda arguments: received.append((arguments, dict(os.environ))) or 7
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {}, clear=True):
            profile = self.profile(directory)
            before = profile.read_bytes()
            with patch.dict("sys.modules", {"ow174.cli": server}):
                result = entry.main([str(profile), "--no-launch", "--host", "127.0.0.1"])
            self.assertEqual(result, 7)
            self.assertEqual(received[0][0], ["--reconstructed-hanzo-sonic-profile", "original-sonic.json",
                                               "--no-launch", "--host", "127.0.0.1"])
            self.assertEqual(received[0][1]["ORPHEUS_RUNTIME_DIR"], "original-runtime")
            self.assertNotIn("ORPHEUS_FIXTURE_ONLY", received[0][1])
            self.assertEqual(profile.read_bytes(), before)

    def test_invalid_input_does_not_partially_change_runtime_selection(self):
        for changes in ({"version": 2}, {"environment": {"ORPHEUS_RUNTIME_DIR": 3}},
                        {"server_cli_arguments": [None]}):
            with self.subTest(changes=changes), tempfile.TemporaryDirectory() as directory:
                profile = self.profile(directory, **changes)
                with patch.dict(os.environ, {"ORPHEUS_RUNTIME_DIR": "previous"}):
                    with self.assertRaises(ValueError):
                        entry.prepare_arguments(profile, [])
                    self.assertEqual(os.environ["ORPHEUS_RUNTIME_DIR"], "previous")
