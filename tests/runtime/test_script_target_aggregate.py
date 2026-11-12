"""Query copying, root order and collector capacity are distinct from target acceptance."""

import unittest

from ow174.game.script.target_aggregate import Output, RootQuery, aggregate


class TargetAggregateTests(unittest.TestCase):
    def query(self):
        return RootQuery((1, 2, 3, 4), 7.5, 9, 0x20, 0x80, (1, 255), (11,), (22,), ((33, 44),))

    def test_query_copy_category_or_order_null_and_duplicates(self):
        query, output, calls, extra = self.query(), Output(8, [999]), [], object()

        def first(category, prepared, emit):
            self.assertEqual(category, -1)
            self.assertIsNot(prepared, query)
            self.assertEqual(prepared.any_bits, (1 << 64) - 1)
            self.assertEqual(prepared.third_rows, query.third_rows)
            calls.append("first")
            for payload in (0, 11, 11):
                emit(payload)

        def second(category, prepared, emit, argument):
            self.assertIs(argument, extra)
            calls.append("second")
            emit(22)

        self.assertEqual(aggregate(query, -1, first=first, second=second, extra=extra, output=output),
                         (11, 11, 22))
        self.assertEqual(calls, ["first", "second"])
        self.assertEqual(query.any_bits, 0x20)

    def test_growth_failure_drops_only_that_append_and_scanning_continues(self):
        requests = []

        def grow(required):
            requests.append(required)
            return None if len(requests) == 1 else required

        def first(_category, _query, emit):
            emit(11)
            emit(22)

        output = Output(0, grow=grow)
        self.assertEqual(aggregate(self.query(), 1, first=first, second=lambda *args: None,
                                   extra=None, output=output), (22,))
        self.assertEqual(requests, [1, 1])

    def test_unknown_growth_or_roots_fail_instead_of_becoming_empty_results(self):
        with self.assertRaises(ValueError):
            Output(0).emit(1)
        with self.assertRaises(ValueError):
            aggregate(self.query(), 1, first=NotImplemented, second=lambda *args: None,
                      extra=None, output=Output(8))


if __name__ == "__main__":
    unittest.main()
