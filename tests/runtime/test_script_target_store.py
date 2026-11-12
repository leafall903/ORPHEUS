"""Second-root slot writes and index binding with declared normal/failing growth."""

import unittest

from ow174.game.script.target_index import Entry, Index, ValueCell
from ow174.game.script.target_store import Buffer, upsert_second


class TargetStoreTests(unittest.TestCase):
    def index(self):
        return Index((-1,) * 8, tuple(Entry(0, -1, 0xFFFFFFFF) for _ in range(8)),
                     tuple(ValueCell(99) for _ in range(8)), 0, -1, 0)

    def test_insert_binds_component24_record_mask_then_key_node(self):
        index, record, bounds, calls = self.index(), object(), object(), []
        payloads, masks = Buffer([None] * 8, 0, 8), Buffer([0] * 8, 0, 8)

        def insert(actual, data, scalar):
            self.assertIs(actual, bounds)
            calls.append((data, scalar))
            return 3

        upsert_second(index, 0xA0000101, 0xFFFF000000000001, bounds, record,
                      payloads=payloads, masks=masks, insert_node=insert, update_node=None)
        self.assertEqual(calls, [(0, 0.0)])
        self.assertEqual((payloads.count, masks.count), (4, 4))
        self.assertIs(payloads.data[3], record)
        self.assertEqual(masks.data[3], 0xFFFF000000000001)
        self.assertEqual(index.find(0xA0000101).value, 3)

    def test_existing_member_does_not_rebind_record_or_grow_counts(self):
        index = self.index()
        cell, _already = index.ensure(1)
        cell.value = 2
        payloads, masks = Buffer(["old"] * 8, 8, 8), Buffer([0] * 8, 8, 8)
        calls = []
        upsert_second(index, 1, 7, "bounds", "new", payloads=payloads, masks=masks,
                      insert_node=None, update_node=lambda *args: calls.append(args))
        self.assertEqual(payloads.data[2], "old")
        self.assertEqual(masks.data[2], 7)
        self.assertEqual(calls, [(2, "bounds", 0.0)])

    def test_payload_count_gates_both_count_extensions(self):
        index = self.index()
        payloads, masks = Buffer([None] * 8, 8, 8), Buffer([0] * 8, 0, 8)
        upsert_second(index, 1, 7, "bounds", "record", payloads=payloads, masks=masks,
                      insert_node=lambda *_: 2, update_node=None)
        self.assertEqual(masks.count, 0)  # native skips both extensions when payload slot is in range
        self.assertEqual(masks.data[2], 7)

    def test_growth_failure_preserves_count_but_requires_explicit_backing(self):
        index = self.index()
        payloads = Buffer([None] * 8, 0, 0, grow=lambda *_: False)
        masks = Buffer([0] * 8, 0, 8)
        upsert_second(index, 1, 7, "bounds", "record", payloads=payloads, masks=masks,
                      insert_node=lambda *_: 2, update_node=None)
        self.assertEqual((payloads.count, masks.count), (0, 3))
        self.assertEqual(index.find(1).value, 2)
        with self.assertRaises(ValueError):
            Buffer([], 0, 0, grow=lambda *_: False).store(2, "unbacked")

    def test_mask_logical_count_can_shrink_when_payload_span_grows(self):
        index = self.index()
        payloads, masks = Buffer([None] * 8, 0, 8), Buffer([0] * 8, 8, 8)
        upsert_second(index, 1, 7, "bounds", "record", payloads=payloads, masks=masks,
                      insert_node=lambda *_: 2, update_node=None)
        self.assertEqual((payloads.count, masks.count), (3, 3))
        self.assertEqual(masks.capacity, 8)


if __name__ == "__main__":
    unittest.main()
