"""Original placement byte on Mei's actual0622/3 command path; native UI remains open."""
import unittest

import test_mei_wall as walls

from ow174.game import statescript
from ow174.game.bits import BitWriter


class MeiPreviewByteTests(unittest.TestCase):
    def assert_byte(self, state, expected):
        payload = state.payload()
        self.assertEqual(payload, {'m_B4802CE3': expected})
        self.assertIs(type(payload['m_B4802CE3']), int)
        for owner in (True, False):
            out = BitWriter()
            if owner:
                statescript.write_payload(out, state.node.cls, payload, 0, 16, full=False)
            else:
                statescript.write_remote_payload(out, state.node.cls, payload, False)
            self.assertEqual(out.count, 8)
            self.assertEqual(out.getvalue(), bytes([expected]))

    def test_actual_wall_preview_confirm_and_original_rebegin_byte(self):
        fixture = walls.WallTests()
        run, owner, _enemy, _target = fixture.scene()
        instance = run.match.combat.walls.instance(owner)
        state = instance.state(0)
        self.assertEqual((state.node.pos, state.node.remote), (3, 0))
        self.assert_byte(state, 0)  # original constructor, before first begin
        x, y, z = owner.mover.position
        aim = walls.fixtures.aim(run.match.combat._eye(owner), (x, y, z + 3))
        with self.assertNoLogs('ow174.game', level='ERROR'):
            fixture.tap(run, owner, 16, aim)
            self.assertTrue(state.active)
            self.assert_byte(state, 0)
            fixture.tap(run, owner, 1, aim)
            self.assertEqual(len(run.match.combat.walls.pieces), 5)
            self.assertFalse(state.active)
            self.assert_byte(state, 1)
            # Exercise this original state's rebegin lifecycle without bypassing
            # or shortening the independent12-second ability cooldown.
            state.begin()
            self.assert_byte(state, 0)
            state.end(False)


if __name__ == '__main__':
    unittest.main()
