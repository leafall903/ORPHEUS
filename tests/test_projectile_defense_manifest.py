"""Replay identity follows the actual cached projectile admission table/policy."""

import json
import unittest
from unittest import mock

import test_game_replay_manifest as fixtures

from ow174.game import combat
from ow174.game import projectile_defenses as defense
from ow174.game import replay_manifest as manifest

RIVET = "0x0400000000002A66"
THROW = "0x0400000000002931"
BLOCKED = "0x0D60000000000145"
ORDINARY = "0x0D600000000002F9"


class ProjectileDefenseManifestTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.ReplayManifestTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.path = self.fixture.base / "external-tags.json"
        self.data = {"version": 1, "build": defense.BUILD,
                     "entities": {RIVET: [ORDINARY], THROW: [BLOCKED]}}
        self.path.write_text(json.dumps(self.data), encoding="utf-8")
        patcher = mock.patch.object(defense, "TAGS_PATH", self.path)
        patcher.start()
        self.addCleanup(patcher.stop)
        defense.tags.cache_clear()
        self.addCleanup(defense.tags.cache_clear)

    def capture(self):
        config = manifest.runtime_configuration(quanta_us=16000, run_options={})
        return self.fixture.capture(configuration=config)

    def test_explicit_table_identity_has_no_paths_and_disabled_catalog_reads_nothing(self):
        active = self.capture()
        identity = defense.runtime_identity()
        self.assertEqual(identity["catalog_entries"], 2)
        self.assertTrue(identity["catalog_enabled"])
        self.assertTrue(defense.eligible(int(RIVET, 16)))
        self.assertNotIn(str(self.path), json.dumps(active))
        with mock.patch.object(defense, "TAGS_PATH", None):
            defense.tags.cache_clear()
            with mock.patch.object(defense, "read_tags", side_effect=AssertionError("disabled")):
                absent = self.capture()
                self.assertFalse(defense.runtime_identity()["catalog_enabled"])
                self.assertFalse(defense.eligible(int(RIVET, 16)))
        self.assertNotEqual(active, absent)

    def test_relocation_and_json_order_do_not_change_live_model_identity(self):
        self.data["entities"][RIVET].append("0x0D600000000003BF")
        self.path.write_text(json.dumps(self.data), encoding="utf-8")
        first = self.capture()
        relocated = self.fixture.base / "other-location.json"
        reordered = {"entities": {THROW: [BLOCKED], RIVET: list(reversed(self.data["entities"][RIVET]))},
                     "build": defense.BUILD, "version": 1}
        relocated.write_text(json.dumps(reordered, indent=4), encoding="utf-8")
        self.assertNotEqual(self.path.read_bytes(), relocated.read_bytes())
        defense.TAGS_PATH = relocated
        defense.tags.cache_clear()
        self.assertEqual(first, self.capture())

    def test_admission_change_is_rejected_after_reload_but_file_edits_do_not_rewrite_live_cache(self):
        first = self.capture()
        self.data["entities"][RIVET] = [ORDINARY, BLOCKED]
        self.path.write_text(json.dumps(self.data), encoding="utf-8")
        self.assertTrue(defense.eligible(int(RIVET, 16)))
        self.assertEqual(first, self.capture())
        defense.tags.cache_clear()
        self.assertFalse(defense.eligible(int(RIVET, 16)))
        second = self.capture()
        self.assertEqual(manifest.compare_manifests(first, second), [
            {"path": "configuration/ow174.game.projectile_defenses/catalog_sha256", "kind": "changed"},
        ])
        with self.assertRaises(manifest.ManifestMismatch):
            manifest.assert_compatible(first, second)

    def test_mutable_reflection_limit_exclusion_and_hero_mapping_are_identified(self):
        first = self.capture()
        alternatives = (
            ("MAX_REFLECTIONS_PER_STEP", defense.MAX_REFLECTIONS_PER_STEP + 1, "max_reflections", "changed"),
            ("EXCLUDED_TAG", defense.EXCLUDED_TAG + 1, "excluded_tag", "changed"),
            ("DEFENSES", {
                hero: spec for hero, spec in defense.DEFENSES.items() if hero != defense.MATRIX_HERO
            }, "defenses/0x02E000000000007A", "missing"),
        )
        for field, value, location, kind in alternatives:
            with self.subTest(field=field), mock.patch.object(defense, field, value):
                self.assertEqual(manifest.compare_manifests(first, self.capture()), [
                    {"path": f"configuration/ow174.game.projectile_defenses/{location}", "kind": kind},
                ])

    def test_controlled_journal_rejects_changed_catalog_before_initializing_replay(self):
        from test_server_journal import MAP, ServerJournalTests

        from ow174.game.server_journal import _ReplaySession

        session, _ = ServerJournalTests().make()
        session.create_match(MAP, [])
        document = session.finish()
        matched = _ReplaySession(document, session.registry.resolve).run()
        self.assertEqual(matched["status"], "matched_controlled_roots_and_wire")
        with mock.patch.object(_ReplaySession, "_initialize_server", wraps=lambda: None) as initialize:
            # Compatibility check precedes server construction and root dispatch.
            self.data["entities"][RIVET] = [ORDINARY, BLOCKED]
            self.path.write_text(json.dumps(self.data), encoding="utf-8")
            defense.tags.cache_clear()
            with self.assertRaises(manifest.ManifestMismatch):
                _ReplaySession(document, session.registry.resolve)
            initialize.assert_not_called()

    def test_malformed_or_missing_explicit_catalog_cannot_be_recorded_as_unknown(self):
        bad = (
            {**self.data, "version": 2},
            {**self.data, "build": "other"},
            {**self.data, "entities": {RIVET: [ORDINARY, ORDINARY]}},
        )
        for data in bad:
            with self.subTest(data=data):
                self.path.write_text(json.dumps(data), encoding="utf-8")
                defense.tags.cache_clear()
                with self.assertRaises(ValueError):
                    self.capture()
        self.path.unlink()
        defense.tags.cache_clear()
        with self.assertRaises(FileNotFoundError):
            self.capture()

    def test_live_eye_height_overrides_are_model_inputs(self):
        first = self.capture()
        for field in ("EYE_STANDING", "EYE_CROUCHED"):
            old = getattr(combat, field)
            updated = {**old, 0x04000000000028B6: old[0x04000000000028B6] + .05}
            with self.subTest(field=field), mock.patch.object(combat, field, updated):
                self.assertEqual(manifest.compare_manifests(first, self.capture()), [
                    {"path": f"configuration/ow174.game.combat.{field}/0x04000000000028B6",
                     "kind": "changed"},
                ])
