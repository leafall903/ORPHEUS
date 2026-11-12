"""Temporal ownership decisions require explicit component/fallback services."""

import unittest

from ow174.game.script.target_relation import TemporalRelationRecord, related


class TemporalRelationTests(unittest.TestCase):
    def check(self, candidate, subject, record, owner=None, invalid=0):
        return related(candidate, subject, component50=lambda _: record,
                       fallback_owner=lambda _, flag: owner if flag is True else NotImplemented,
                       identity_of=lambda _: 0xA0000101, invalid_identity=invalid)

    def test_null_subject_and_pointer_identity(self):
        actor = object()
        self.assertTrue(self.check(None, None, NotImplemented))
        self.assertFalse(self.check(actor, None, NotImplemented))
        self.assertTrue(self.check(actor, actor, TemporalRelationRecord(0, 0, 0)))
        self.assertFalse(self.check(object(), actor, TemporalRelationRecord(0, 0xA0000101, 0)))
        with self.assertRaises(ValueError):
            self.check(None, actor, None)

    def test_absent_component_compares_fallback_actor_by_identity(self):
        actor, subject = object(), object()
        self.assertTrue(self.check(actor, subject, None, owner=subject))
        self.assertFalse(self.check(actor, subject, None, owner=object()))
        self.assertFalse(self.check(actor, subject, None))
        self.assertTrue(self.check(actor, actor, None))

    def test_override_validity_and_count_gate(self):
        actor, subject = object(), object()
        self.assertTrue(self.check(actor, subject, TemporalRelationRecord(1, 0xA0000101, 0)))
        self.assertTrue(self.check(actor, subject, TemporalRelationRecord(1, 0xA0000101, 0xFFFFFFFF)))
        self.assertTrue(self.check(actor, subject, TemporalRelationRecord(1, 9, 0xA0000101)))
        self.assertFalse(self.check(actor, subject, TemporalRelationRecord(0, 9, 0xA0000101)))
        self.assertFalse(self.check(actor, subject, TemporalRelationRecord(1, 0xA0000101, 9)))

    def test_unknown_presence_and_fallback_fail_instead_of_defaulting(self):
        actor, subject = object(), object()
        with self.assertRaises(ValueError):
            self.check(actor, subject, NotImplemented)
        with self.assertRaises(ValueError):
            self.check(actor, subject, None, owner=NotImplemented)


if __name__ == "__main__":
    unittest.main()
