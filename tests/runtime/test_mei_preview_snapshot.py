"""Mei's exact preview class remains observable in body and full server storage."""
import unittest

import test_server_journal as journal
import test_server_snapshot as snapshots

from ow174.game import mei
from ow174.game.body_vm_snapshot import BodyVMSnapshotError, export_body_vm, snapshot_digest
from ow174.game.server_snapshot import snapshot_digest as server_digest


class MeiPreviewSnapshotTests(unittest.TestCase):
    def test_real_mei_byte_and_placement_clocks_change_both_exports(self):
        fixture = snapshots.ServerSnapshotTests()
        session, peers, players = fixture.scene()
        owner, peer = players[0], peers[0]
        now = 101.1
        peer.commands(owner.body_script.frame + 1, 40, 0, now=now)
        peer.message(21616, journal.pick(owner.entity, mei.HERO), now=now)
        for _ in range(8):
            now += .016
            session.tick(now)
            peer.drain()
            peer.send(now=now)
        script = owner.body_script
        state = owner.match.combat.walls.instance(owner).state(0)
        self.assertEqual(type(state).__name__, 'MeiPlacementPreview')

        def capture():
            return (snapshot_digest(export_body_vm(script)),
                    server_digest(fixture.capture(session)))

        baseline = capture()
        for field in ('placement_byte', 'last_frame', 'latest_frame'):
            with self.subTest(field=field):
                original = getattr(state, field)
                setattr(state, field, 1)
                changed = capture()
                self.assertNotEqual(changed[0], baseline[0])
                self.assertNotEqual(changed[1], baseline[1])
                setattr(state, field, original)
                self.assertEqual(capture(), baseline)
        state.unregistered_preview_field = 1
        try:
            with self.assertRaises(BodyVMSnapshotError):
                export_body_vm(script)
        finally:
            del state.unregistered_preview_field


if __name__ == '__main__':
    unittest.main()
