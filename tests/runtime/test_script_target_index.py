"""Member IDs, preserved value storage, and existing versus missing upsert paths."""

import unittest

from ow174.game.script.target_index import Entry, Index, ValueCell, bucket, remove, update_existing


class TargetIndexTests(unittest.TestCase):
    def index(self):
        return Index((0,), (Entry(0xA0000101, 1), Entry(0xA0000201, -1)),
                     (ValueCell(4), ValueCell(9)))

    def test_full_uint32_collision_chain_and_unknown_storage(self):
        index = self.index()
        self.assertEqual(index.find(0xA0000201).value, 9)
        self.assertIsNone(index.find(0xA0000301))
        self.assertIsNone(Index(None).find(0))
        for unknown in (Index(), Index((0,), (Entry(0, -1),)),
                        Index((0,), (Entry(1, 0),))):
            with self.subTest(index=unknown), self.assertRaises(ValueError):
                unknown.find(0)
        with self.assertRaises(ValueError):
            bucket(-1, 8)
        with self.assertRaises(ValueError):
            bucket(0, 1 << 32)

    def test_existing_update_writes_mask_before_geometry_and_miss_requires_insert(self):
        calls, bounds = [], object()
        result = update_existing(
            self.index(), 0xA0000201, 0xFFFF000000000001, bounds,
            write_mask=lambda node, bits: calls.append(("mask", node, bits)),
            update_node=lambda node, b, scalar: calls.append(("node", node, b, scalar)))
        self.assertTrue(result)
        self.assertEqual(calls, [("mask", 9, 0xFFFF000000000001), ("node", 9, bounds, 0.0)])
        self.assertFalse(update_existing(self.index(), 3, 1, bounds, write_mask=None, update_node=None))

    def test_remove_reads_saved_value_cell_after_key_callback(self):
        index, calls = self.index(), []
        cell = index.values[1]

        def erase(actual, entity):
            self.assertIs(actual, index)
            self.assertEqual(entity, 0xA0000201)
            cell.value = 12
            actual.values = (ValueCell(99), ValueCell(99))
            calls.append("erase")

        self.assertTrue(remove(index, 0xA0000201, remove_key=erase,
                               remove_node=lambda node: calls.append(node)))
        self.assertEqual(calls, ["erase", 12])
        self.assertFalse(remove(index, 3, remove_key=None, remove_node=None))

    def empty(self, count=4):
        return Index(tuple([-1] * count), tuple(Entry(0, -1, 0xFFFFFFFF) for _ in range(count)),
                     tuple(ValueCell(100 + i) for i in range(count)), 0, -1, 0)

    def test_ensure_duplicate_does_not_initialize_or_replace_value_cell(self):
        index = self.empty()
        cell, already = index.ensure(0xA0000101)
        self.assertFalse(already)
        self.assertEqual(cell.value, 100)
        same, already = index.ensure(0xA0000101)
        self.assertTrue(already)
        self.assertIs(same, cell)
        self.assertEqual(index.used_count, 1)

    def test_erase_keeps_value_and_reuses_slot_before_growing(self):
        index = self.empty(1)
        cell, _already = index.ensure(1)
        cell.value = 17
        self.assertTrue(index.erase(1))
        self.assertIsNone(index.find(1))
        self.assertEqual((index.used_count, index.free_head, index.free_count), (1, 0, 1))
        reused, already = index.ensure(2)
        self.assertFalse(already)
        self.assertIs(reused, cell)
        self.assertEqual(reused.value, 17)
        self.assertEqual((index.used_count, index.free_head, index.free_count), (1, -1, 0))

    def test_initial_reservation_request_is_three_and_is_explicit(self):
        index, requests = Index(None, (), (), 0, -1, 0), []

        def reserve(actual, needed):
            self.assertIs(actual, index)
            requests.append(needed)
            backed = self.empty()
            actual.buckets, actual.entries, actual.values = backed.buckets, backed.entries, backed.values

        cell, already = index.ensure(1, reserve=reserve)
        self.assertEqual(requests, [3])
        self.assertFalse(already)
        self.assertEqual(cell.value, 100)
        with self.assertRaises(ValueError):
            Index(None, (), (), 0, -1, 0).ensure(1)

    def test_key_erase_in_real_removal_wrapper_preserves_saved_node_value(self):
        index = self.empty()
        cell, _already = index.ensure(0xFFFFFFFF)
        cell.value = 8
        nodes = []
        self.assertTrue(remove(index, 0xFFFFFFFF, remove_key=lambda actual, key: actual.erase(key),
                               remove_node=nodes.append))
        self.assertEqual(nodes, [8])
        self.assertEqual(cell.value, 8)


if __name__ == "__main__":
    unittest.main()
