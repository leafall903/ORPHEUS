"""The chunk sweep (tests/chunk_sweep.py): what it counts, lists and leaves unread."""

import unittest

from ow174.game import combat, server, world
from ow174.game.bits import BitWriter
from ow174.game.content import PRACTICE_RANGE
from ow174.game.statescript import Stream
from tests import chunk_sweep

ENTITY = 0x80000001


class _Client:
    player = None


def _garbage() -> BitWriter:
    frame = BitWriter()
    frame.bits(0b1011, 4)  # ends inside its instance list
    return frame


class ReportTests(unittest.TestCase):
    def setUp(self):
        self.report = chunk_sweep.Report()
        self.report.test = "a.test"
        self.client = _Client()
        self.stream = Stream(ENTITY)

    def test_a_good_frame_is_read_and_a_repeat_is_not_read_twice(self):
        item = self.stream.full_frame(combat.mode_frame({}, PRACTICE_RANGE.mode_guid))
        self.report.read(self.client, item)
        self.report.read(self.client, item)
        self.assertEqual(self.report.counts, {"ok": 1, "repeated": 1})
        self.assertEqual(self.report.failures, [])

    def test_a_misread_chunk_is_listed_and_the_entity_is_unread_until_a_full_frame(self):
        self.report.read(self.client, self.stream.full_frame(_garbage()))
        self.assertEqual([failure[:3] for failure in self.report.failures], [("a.test", "misread", ENTITY)])
        self.stream.arrived(self.stream.last)
        self.report.read(self.client, self.stream.ack_delta(_garbage()))
        self.assertEqual(self.report.counts, {"misread": 1, "unchecked": 1})
        self.report.read(self.client, self.stream.full_frame(combat.mode_frame({}, PRACTICE_RANGE.mode_guid)))
        self.assertEqual(self.report.counts["ok"], 1)
        self.assertEqual(len(self.report.failures), 1)
        self.assertIn("misread x1", self.report.summary())

    def test_a_fragment_is_unchecked(self):
        frame = combat.mode_frame({}, PRACTICE_RANGE.mode_guid)
        parts = self.stream.reliable_frames(frame, 40, full=True)
        self.assertGreater(len(parts), 1)
        self.report.read(self.client, parts[0])
        self.assertEqual(self.report.counts, {"unchecked": 1})
        self.assertEqual(self.report.failures, [])

    def test_a_destroy_forgets_the_entity(self):
        self.report.read(self.client, self.stream.full_frame(combat.mode_frame({}, PRACTICE_RANGE.mode_guid)))
        self.report.read(self.client, world.EntityUpdate(ENTITY, world.OP_DESTROY))
        self.assertEqual(dict(self.report.models[self.client]), {})


class WatchTests(unittest.TestCase):
    def test_the_hooks_are_put_back(self):
        queue, run = server.Client._queue_current_entity, unittest.TestCase.run
        with chunk_sweep.watch() as report:
            self.assertIsNot(server.Client._queue_current_entity, queue)
            self.assertEqual(report.failures, [])
        self.assertIs(server.Client._queue_current_entity, queue)
        self.assertIs(unittest.TestCase.run, run)


if __name__ == "__main__":
    unittest.main()
