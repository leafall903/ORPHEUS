"""Original0005 five-definition lease check; keep existing transport checks intact."""
import unittest
from types import SimpleNamespace
from test_game_combat import Run
from ow174.game import projectiles
from ow174.game.content import heroes

class DeliveredHanzoLeaseTests(unittest.TestCase):
    def test_hanzo_can_lease_all_five_predicted_definitions_without_id_overlap(self):
        run = Run()
        player, = run.players
        self.assertTrue(run.match.switch_hero(player, heroes()[0x02E0000000000005]))
        service = run.match.combat.projectiles
        expected = [0x0400000000000000 | guid for guid in (0x0144, 0x020B, 0x270F, 0x0149, 0x020D)]
        self.assertEqual(projectiles.definitions(player.body_script), expected)
        clock = SimpleNamespace(last_frame=projectiles.MAX_LEASE_FRAME - 1)
        ranges = []
        for slot in range(32):
            owner = SimpleNamespace(slot=slot, body_script=player.body_script, commands=clock)
            blocks = service.lease(owner)
            self.assertEqual(len(blocks), 10)
            self.assertEqual([block[0] for block in blocks[::2]], expected)
            for index, (first, second) in enumerate(zip(blocks[::2], blocks[1::2], strict=True)):
                guid, start, stop, base, per_frame = first
                span = stop - start
                self.assertEqual(second, (guid, stop, stop + span, base, 1))
                self.assertLessEqual(second[2], 0xFFFFFFFF)
                self.assertEqual(per_frame, 1)
                self.assertEqual(span, projectiles.LEASE_SPAN)
                if index < 4:
                    self.assertEqual(base, 0x00100000 + (slot * 4 + index) * (1 << 22))
                self.assertEqual(service.lease_id(owner, guid, start), 0xA0000000 | base)
                self.assertEqual(service.lease_id(owner, guid, stop - 1), 0xA0000000 | (base + span - 1))
                self.assertEqual(service.lease_id(owner, guid, stop), 0xA0000000 | base)
                ranges.append((base, base + span))
        ranges.sort()
        self.assertGreaterEqual(ranges[0][0], 0x00100000)
        self.assertLess(ranges[-1][1], 0x1FFFFFFF)
        for (_, end), (start, _) in zip(ranges, ranges[1:]):
            self.assertLessEqual(end, start)
        owner.slot = 32
        self.assertIsNone(service.lease(owner))
        self.assertIsNone(service.lease_id(owner, expected[-1], clock.last_frame))
