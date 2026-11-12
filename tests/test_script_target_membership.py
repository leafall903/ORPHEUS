"""Member publication gates do not invent physical/filter metadata."""

import unittest

from ow174.game.script.target_membership import (
    CATEGORIES,
    Version,
    filter_changed,
    publish,
    route_remove,
    route_upsert,
)


class TargetMembershipTests(unittest.TestCase):
    def scene(self, *, dynamic=True, static=True, flags=0, check=True, same=False,
              geometry=True, bits=0x150041):
        calls, bounds, version = [], object(), Version(8, 8 if same else 7)

        def has(entity, index):
            calls.append(("has", index))
            return dynamic if index == 24 else static

        def geometry24(entity):
            calls.append(("bounds24",))
            return bounds if geometry else None

        def geometry4(entity):
            calls.append(("bounds4",))
            return bounds if geometry else None

        def version24(entity):
            calls.append(("version",))
            return version

        def transform(entity):
            calls.append(("bits",))
            return bits

        def upsert(entity, category, actual_bounds, mask, first):
            self.assertIs(actual_bounds, bounds)
            calls.append(("publish", category, mask, first))

        result = publish("actor", flags, check_revision=check, has_component=has,
                         bounds24=geometry24, bounds4=geometry4, version24=version24,
                         transform_bits=transform, upsert=upsert)
        return result, calls, version

    def test_component24_priority_and_ordered_categories_with_full_mask(self):
        bits = (0xA55A << 48) | 0x150041
        result, calls, version = self.scene(bits=bits)
        self.assertEqual(result, (*CATEGORIES, 0x150041))
        self.assertEqual(calls[:4], [("has", 24), ("bounds24",), ("version",), ("bits",)])
        self.assertTrue(all(call[2:] == (bits, False) for call in calls[4:]))
        self.assertEqual(version.published, 8)

    def test_revision_gate_runs_after_geometry_but_before_mask(self):
        result, calls, _version = self.scene(same=True)
        self.assertEqual((result, calls), ((), [("has", 24), ("bounds24",), ("version",)]))
        result, calls, _version = self.scene(check=False, same=True, bits=1)
        self.assertEqual(result, (1, 0x150041))
        self.assertNotIn(("version",), calls)

    def test_static_path_has_no_revision_gate_and_uses_first_root(self):
        result, calls, version = self.scene(dynamic=False, bits=0x40)
        self.assertEqual(result, (0x40, 0x150041))
        self.assertEqual(calls[:4], [("has", 24), ("has", 4), ("bounds4",), ("bits",)])
        self.assertEqual(calls[-1][-1], True)
        self.assertEqual(version.published, 7)

    def test_entity_guard_failed_bounds_and_zero_mask_do_not_publish(self):
        self.assertEqual(self.scene(flags=4)[1], [])
        self.assertEqual(self.scene(geometry=False)[1], [("has", 24), ("bounds24",)])
        result, calls, version = self.scene(bits=0)
        self.assertEqual(result, ())
        self.assertEqual(version.published, 8)
        self.assertFalse(any(call[0] == "publish" for call in calls))

    def test_unknown_components_fail_instead_of_becoming_absent(self):
        with self.assertRaises(ValueError):
            publish("actor", 0, check_revision=False, has_component=lambda *_: NotImplemented,
                    bounds24=None, bounds4=None, version24=None, transform_bits=None, upsert=None)

    def test_upsert_uses_manager65_and_matching_ensure_update_family(self):
        for first in (False, True):
            with self.subTest(first=first):
                calls, manager, root, bounds = [], object(), object(), object()

                def get_manager(entity, slot, calls=calls, manager=manager):
                    calls.append(("manager", entity, slot))
                    return manager

                def ensure(actual, category, create, calls=calls, manager=manager, root=root):
                    self.assertIs(actual, manager)
                    calls.append(("ensure", category, create))
                    return root

                def update(actual, entity, actual_bounds, bits, calls=calls, root=root, bounds=bounds):
                    self.assertIs(actual, root)
                    self.assertIs(actual_bounds, bounds)
                    calls.append(("update", entity, bits))

                def unused(*args):
                    self.fail("wrong root family invoked")

                self.assertTrue(route_upsert("actor", 1, bounds, 0xFFFF000000000001, first,
                                             manager=get_manager,
                                             ensure_first=ensure if first else unused,
                                             ensure_second=unused if first else ensure,
                                             update_first=update if first else unused,
                                             update_second=unused if first else update))
                self.assertEqual(calls, [("manager", "actor", 65), ("ensure", 1, True),
                                         ("update", "actor", 0xFFFF000000000001)])

    def test_known_ensure_failure_skips_update_and_unknown_is_error(self):
        for result in (None, 0, NotImplemented):
            with self.subTest(result=result):
                args = {"manager": lambda *_: "manager", "ensure_first": lambda *_, result=result: result,
                        "ensure_second": None, "update_first": None, "update_second": None}
                if result is not NotImplemented:
                    self.assertFalse(route_upsert("actor", 1, object(), 1, True, **args))
                else:
                    with self.assertRaises(ValueError):
                        route_upsert("actor", 1, object(), 1, True, **args)

    def test_filter_change_publishes_before_removing_missing_categories(self):
        calls = []
        missing = filter_changed("actor", 1, has_component=lambda *_: True,
                                 publish_now=lambda actor, check: calls.append(("publish", check)),
                                 remove=lambda actor, category: calls.append(("remove", category)))
        self.assertEqual(missing, (0x10000, 0x40, 0x40000, 0x100000))
        self.assertEqual(calls[0], ("publish", False))
        all_missing = filter_changed("actor", 0, has_component=lambda *_: True,
                                     publish_now=lambda *_: None, remove=lambda *_: None)
        self.assertEqual(all_missing, (*CATEGORIES, 0x150041))

    def test_remove_find_never_requests_creation_and_skips_known_null_root(self):
        calls = []

        def find(manager, category, create):
            calls.append((category, create))
            return None

        self.assertFalse(route_remove("actor", 1, has_component=lambda *_: True,
                                      manager=lambda *_: "manager", find_first=None,
                                      find_second=find, erase=None))
        self.assertEqual(calls, [(1, False)])


if __name__ == "__main__":
    unittest.main()
