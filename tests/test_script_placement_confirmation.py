"""Explicit placement property scopes, independent of server role/transport."""

import unittest

from ow174.game.script import placement


class PlacementConfirmationTests(unittest.TestCase):
    def test_first_request_registers_before_latching_and_unregisters_after_dirtying(self):
        confirmation = placement.Confirmation(value_bits=0xAAAAAAAA, dirty_bits=0x10)
        seen = []

        def register():
            seen.append(("register", confirmation.scope_depth, confirmation.latched,
                         confirmation.value_bits, confirmation.dirty))

        def unregister():
            seen.append(("unregister", confirmation.scope_depth, confirmation.latched,
                         confirmation.value_bits, confirmation.dirty))

        confirmation.request(register, unregister)
        self.assertEqual(seen, [("register", 1, False, 0xAAAAAA00, False),
                                ("unregister", 0, True, 0xAAAAAA01, True)])
        self.assertEqual(confirmation.dirty_bits, 0x11)
        self.assertTrue(confirmation.has_values)
        self.assertTrue(confirmation.touched)

    def test_repeated_latched_request_clears_values_without_new_dirty_write(self):
        confirmation = placement.Confirmation(True, 0, 0xFFFFFFFF, 0xAAAAAAAA, True, True, True)
        events = []
        confirmation.request(lambda: events.append("register"), lambda: events.append("unregister"))
        self.assertEqual(events, ["register", "unregister"])
        self.assertEqual((confirmation.value_bits, confirmation.dirty_bits), (0xFFFFFF00, 0xAAAAAAAA))
        self.assertTrue(confirmation.latched)
        self.assertFalse(confirmation.dirty)
        self.assertFalse(confirmation.has_values)
        self.assertTrue(confirmation.touched)

    def test_nested_requests_preserve_pending_values_and_register_only_outer_scope(self):
        confirmation = placement.Confirmation()
        events = []
        def register():
            events.append("register")

        def unregister():
            events.append("unregister")
        confirmation.enter(register)
        confirmation.request(register, unregister)
        self.assertEqual(confirmation.scope_depth, 1)
        self.assertTrue(confirmation.dirty)
        self.assertEqual(confirmation.value_bits, 1)
        confirmation.request(register, unregister)
        self.assertEqual(confirmation.value_bits, 1)
        self.assertTrue(confirmation.dirty)
        confirmation.leave(unregister)
        self.assertEqual(events, ["register", "unregister"])
        self.assertEqual(confirmation.scope_depth, 0)

    def test_explicit_service_inputs_reject_wrong_types_ranges_and_unbalanced_scope(self):
        for field, value in (("latched", 1), ("dirty", 0), ("has_values", None), ("touched", 1),
                             ("scope_depth", True), ("scope_depth", -1), ("value_bits", -1),
                             ("dirty_bits", 1 << 32)):
            with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                placement.Confirmation(**{field: value})
        with self.assertRaises(ValueError):
            placement.Confirmation().leave(lambda: None)
        confirmation = placement.Confirmation(scope_depth=0xFFFFFFFF)
        with self.assertRaises(ValueError):
            confirmation.enter(lambda: None)
        self.assertEqual(confirmation.scope_depth, 0xFFFFFFFF)


if __name__ == "__main__":
    unittest.main()
