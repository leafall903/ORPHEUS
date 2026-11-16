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
            [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()] for path in paths
        ]

    def test_creation_commands_and_retirement_have_a_continuous_body_only_trace(self):
        # Projectile-lease discovery first accesses states after capture starts.
        # This scenario explicitly omits that hook; the next test covers the real hook.
        with mock.patch.object(projectiles, "definitions", return_value=()):
            run = combat_tests.Run()
        player = run.players[0]
        script = player.body_script
        self.addCleanup(run.match.stop_body_script, player)
        for number in range(1, 11):
            run.match.tick = run.frame + number
            click = number <= 5
            command = Command(
                run.frame + number, buttons=(FIRE | 0x10000) if click else 0,
                extra=313, subframe=7 if click else 255,
                click_yaw=-192 if click else None, click_pitch=233 if click else None,
            )
            player.take_commands([command])
        run.match.stop_body_script(player)
        (lines,) = self.read()
        header = lines[0]["header"]
        events = lines[1:]
        self.assertEqual(header["schema"], "ow174.native-body-replay.v2")
        self.assertEqual(header["start_frame"], run.frame)
        self.assertEqual([event["seq"] for event in events], list(range(1, 12)))
        self.assertEqual([event["kind"] for event in events], ["command"] * 10 + ["end"])
        self.assertEqual(events[0]["before_hash"], header["initial_hash"])
        # Truncated for this example; full file would be included in real call

if __name__ == "__main__":
    unittest.main()
