"""Read-only observation retains Recall history and refuses unknown engine state."""

import dataclasses
import unittest
from unittest.mock import patch

import test_game_tracer as tracer_tests

from ow174.game.body_vm_snapshot import BodyVMSnapshotError, export_body_vm, snapshot_digest
from ow174.game.script import runtime


class TracerStorageTests(tracer_tests.TracerCommandFixture, unittest.TestCase):
    def test_live_recall_export_reads_storage_without_evaluation_and_hashes_history(self):
        self.wait(2)
        self.step(16)
        script = self.player.body_script
        rng = script.component.rng.getstate()
        with (patch.object(runtime.Var, "value", side_effect=AssertionError("no evaluation")),
              patch.object(runtime.State, "payload", side_effect=AssertionError("no payload"))):
            baseline = snapshot_digest(export_body_vm(script))
            self.assertEqual(snapshot_digest(export_body_vm(script)), baseline)
            sample = script.world.recall_history[-1]
            script.world.recall_history[-1] = dataclasses.replace(sample, health=sample.health - 1)
            self.assertNotEqual(snapshot_digest(export_body_vm(script)), baseline)
        self.assertEqual(script.component.rng.getstate(), rng)

    def test_recall_session_extra_unregistered_fields_still_fail_closed(self):
        self.wait(2)
        self.step(16)
        self.player.body_script.world.recall_session.unknown_flag = 1
        with self.assertRaises(BodyVMSnapshotError):
            export_body_vm(self.player.body_script)


if __name__ == "__main__":
    unittest.main()
