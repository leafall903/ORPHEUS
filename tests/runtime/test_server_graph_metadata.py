"""Full-server storage retains and validates the compiled VM graph indexes."""
import unittest

import test_server_snapshot as snapshots
from ow174.game.body_vm_snapshot import BodyVMSnapshotError
from ow174.game.server_snapshot import ServerSnapshotError, snapshot_digest


class ServerGraphMetadataTests(unittest.TestCase):
    def test_full_capture_preserves_indexes_and_rejects_wrong_order_and_unknown_fields(self):
        owner = snapshots.ServerSnapshotTests()
        session, _peers, players = owner.scene()
        graph = next(iter(players[0].body_script.component.instances.values())).graph
        before = snapshot_digest(owner.capture(session))
        original = graph._message_nodes
        self.assertGreater(len(original), 1)
        graph._message_nodes = tuple(reversed(original))
        with self.assertRaisesRegex((ServerSnapshotError, BodyVMSnapshotError), 'derived cache'):
            owner.capture(session)
        graph._message_nodes = original
        self.assertEqual(snapshot_digest(owner.capture(session)), before)
        graph._unexpected_metadata = ()
        with self.assertRaisesRegex((ServerSnapshotError, BodyVMSnapshotError), 'unknown or missing stored fields|unregistered stored fields'):
            owner.capture(session)
        del graph._unexpected_metadata
        self.assertEqual(snapshot_digest(owner.capture(session)), before)
