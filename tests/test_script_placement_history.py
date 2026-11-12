"""Native placement history storage only; prediction admission remains external."""

import unittest

from ow174.game.script import expr, placement


def row(frame, *, entity=0, identifier=0, point=None, valid=False):
    return placement.Record(frame, expr.Entity(entity), expr.Asset(identifier),
                            point or expr.Vec3(0, 0, 0), valid)


class PlacementHistoryTests(unittest.TestCase):
    def test_reverse_removal_preserves_native_swap_order_and_whole_row_values(self):
        records = [row(frame, entity=frame + 1, identifier=frame + 100,
                       point=expr.Vec3(frame, -frame, frame / 7), valid=bool(frame % 2))
                   for frame in (10, 100, 20, 200)]
        expected = [records[3], records[1]]
        placement.prune(records, 50)
        self.assertEqual(records, expected)

    def test_cutoff_is_inclusive_and_unsigned(self):
        records = [row(0), row(59), row(60), row(0xFFFFFFFF)]
        placement.prune(records, 60)
        self.assertEqual([record.frame for record in records], [60, 0xFFFFFFFF])
        placement.prune(records, 0xFFFFFFFF)
        self.assertEqual(records, [row(0xFFFFFFFF)])

    def test_recording_disabled_does_not_prune_or_append(self):
        records = [row(0), row(1)]
        before = list(records)
        placement.remember(records, row(200), enabled=False)
        self.assertEqual(records, before)

    def test_record_trims_before_duplicate_check_and_first_row_wins(self):
        old, retained, duplicate = row(20), row(140), row(200, entity=17)
        records = [old, retained, duplicate]
        placement.remember(records, row(200, entity=99, valid=True), enabled=True)
        self.assertEqual(records, [duplicate, retained])
        placement.remember(records, row(201), enabled=True)
        self.assertEqual(records, [duplicate, row(201)])

    def test_small_frame_uses_native_unsigned_subtraction_without_clamping(self):
        records = [row(0), row(1), row(0xFFFFFFFF)]
        placement.remember(records, row(1, entity=7), enabled=True)
        self.assertEqual(records, [row(0xFFFFFFFF), row(1, entity=7)])

    def test_row_retains_typed_identifiers_and_rounds_point_to_float32(self):
        record = row(60, entity=0xFFFFFFFF, identifier=0xFFFFFFFFFFFFFFFF,
                     point=expr.Vec3(1 / 7, -1 / 3, 1000 / 9), valid=True)
        self.assertEqual(record.point, expr.Vec3(expr.f32(1 / 7), expr.f32(-1 / 3), expr.f32(1000 / 9)))
        with self.assertRaises(AttributeError):
            record.frame = 17

    def test_rejects_wrong_word_ranges_and_untyped_fields(self):
        args = [60, expr.Entity(0), expr.Asset(0), expr.Vec3(0, 0, 0), False]
        for index, invalid in (
            (0, True), (0, -1), (0, 1 << 32),
            (1, 0), (1, expr.Entity(True)), (1, expr.Entity(-1)), (1, expr.Entity(1 << 32)),
            (2, 0), (2, expr.Asset(True)), (2, expr.Asset(-1)), (2, expr.Asset(1 << 64)),
            (3, (0, 0, 0)), (3, expr.Vec3(True, 0, 0)), (3, expr.Vec3("0", 0, 0)),
            (4, 0),
        ):
            with self.subTest(index=index, invalid=invalid), self.assertRaises(ValueError):
                changed = list(args)
                changed[index] = invalid
                placement.Record(*changed)
        for cutoff in (True, -1, 1 << 32):
            with self.subTest(cutoff=cutoff), self.assertRaises(ValueError):
                placement.prune([], cutoff)
        with self.assertRaises(ValueError):
            placement.remember([], row(60), enabled=1)
        with self.assertRaises(ValueError):
            placement.remember([], None, enabled=True)


if __name__ == "__main__":
    unittest.main()
