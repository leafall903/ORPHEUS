"""Matrix combat reconstruction using authored0407 and external projectile tags."""

import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

import test_game_dva_mech as mech_tests

from ow174.game import bots, deflection, projectiles
from ow174.game.collision import World
from ow174.game.combat import HitDef, Rocket, Target, Vitals
from tools.extract_projectile_tags import entity_tags

RIFLE = 0x04000000000003D4
HELIX = 0x040000000000072C
FROST = 0x04000000000008BE


class InputTests(unittest.TestCase):
    def test_tag_catalog_rejects_wrong_build_alias_and_wrong_tag_type(self):
        data = {"version": 1, "build": deflection.BUILD, "entities": {"0x04000000000003D4": []}}
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "tags.json"
            path.write_text(json.dumps(data), encoding="utf-8")
            self.assertEqual(deflection.read_tags(path), {RIFLE: frozenset()})
            for field, value in (("build", "other"), ("entities", {"03D4": []}),
                                 ("entities", {"0x04000000000003D4": ["0x0400000000000145"]})):
                bad = {**data, field: value}
                path.write_text(json.dumps(bad), encoding="utf-8")
                with self.assertRaises(ValueError):
                    deflection.read_tags(path)

    def test_extractor_resolves_shared_array_and_ignores_explicit_null_tag(self):
        xml = '''<STUEntityDefinition xmlns:d="https://yretenai.com/dragonml/v1">
          <d:map d:name="m_componentMap">
          <STUTargetTagComponent><d:ref d:name="m_targetTags" d:ref="tags"/></STUTargetTagComponent>
          </d:map>
          <d:array d:id="tags"><d:ref GUID="000000000145.06C"/>
          <d:ref GUID="000000000000.001"/></d:array>
          <child><STUTargetTagComponent><d:array d:name="m_targetTags">
          <d:ref GUID="0000000002F9.06C"/></d:array></STUTargetTagComponent></child>
          </STUEntityDefinition>'''
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "entity.xml"
            path.write_text(xml, encoding="utf-8")
            self.assertEqual(entity_tags(path), ["0x0D60000000000145"])

    def test_unknown_and_excluded_tags_do_not_absorb(self):
        with patch.object(deflection, "tags", return_value={RIFLE: frozenset(),
                                                           FROST: {deflection.EXCLUDED_TAG}}):
            self.assertTrue(deflection.absorbable(RIFLE))
            self.assertFalse(deflection.absorbable(FROST))
            self.assertFalse(deflection.absorbable(None))
            self.assertFalse(deflection.absorbable(123))

    def test_box_sweep_parallel_inside_outside_and_finite_reach(self):
        low, high = (-1, -1, 1), (1, 1, 10)
        self.assertEqual(deflection.ray_box((0, 0, 15), (0, 0, -1), low, high, 20), 5)
        self.assertEqual(deflection.ray_box((0, 0, 5), (0, 0, -1), low, high, 20), 0)
        self.assertIsNone(deflection.ray_box((2, 0, 15), (0, 0, -1), low, high, 20))
        self.assertIsNone(deflection.ray_box((0, 0, 15), (0, 0, 1), low, high, 20))
        self.assertIsNone(deflection.ray_box((0, 0, 15), (0, 0, -1), low, high, 4))


class MatrixCombatTests(unittest.TestCase):
    scenario = mech_tests.AuthoredMechTests.scenario
    step = mech_tests.AuthoredMechTests.step
    matrix = mech_tests.AuthoredMechTests.matrix

    @classmethod
    def setUpClass(cls):
        mech_tests.AuthoredMechTests.setUpClass()
        if any(key not in deflection.tags() for key in (RIFLE, HELIX, FROST)):
            raise unittest.SkipTest("requires external003 projectile tags; Matrix combat acceptance OPEN")

    def prepared(self, enabled=True, distance=15):
        run, mech, enemy, _ = self.scenario()
        x, y, _ = enemy.mover.position
        enemy.mover.position = [x, y, distance]
        enemy.mover.body = None  # external fixture placement must also rebind the cached physics body
        self.step(run, mech, 2, 2 if enabled else 0)
        self.assertAlmostEqual(enemy.mover.position[2], distance, delta=0.01)
        target = next(t for t in run.match.combat._targets() if t.owner is mech)
        return run, mech, enemy, run.match.combat.vitals_of(target)

    def test_authored_rifle_is_absorbed_but_release_reopens_damage(self):
        run, mech, enemy, vitals = self.prepared()
        for _ in range(40):
            run.step({mech: (0, 0, 2), enemy: (-32768, 0, 1)})
        self.assertEqual(vitals.total, 600)
        self.assertEqual(enemy.client.messages(25001), [])
        for _ in range(20):
            run.step({enemy: (-32768, 0, 1)})
        self.assertLess(vitals.total, 600)

    def test_authored_helix_is_removed_without_direct_or_splash_damage(self):
        run, mech, enemy, vitals = self.prepared()
        with patch.object(run.match.combat.projectiles, "absorbed",
                          wraps=run.match.combat.projectiles.absorbed) as absorbed:
            for frame in range(45):
                run.step({mech: (0, 0, 2), enemy: (-32768, 0, 2 if frame == 0 else 0)})
            self.assertEqual(absorbed.call_count, 1)
        self.assertEqual(vitals.total, 600)
        self.assertEqual(run.match.combat.rockets, [])
        self.assertEqual(run.match.combat.projectiles.shots, [])
        self.assertEqual(enemy.client.messages(25001), [])

    def test_rocket_hits_when_matrix_is_off_or_aims_away(self):
        for enabled in (False, True):
            run, mech, enemy, vitals = self.prepared(enabled)
            for frame in range(45):
                run.step({mech: (32768 if enabled else 0, 0, 2 if enabled else 0),
                          enemy: (32768, 0, 2 if frame == 0 else 0)})
            self.assertLess(vitals.total, 600)

    def test_exhaustion_stops_intercepting_held_incoming_rifle_fire(self):
        run, mech, enemy, vitals = self.prepared()
        for _ in range(100):
            run.step({mech: (0, 0, 2), enemy: (-32768, 0, 1)})
        self.assertEqual(vitals.total, 600)
        for _ in range(45):
            run.step({mech: (0, 0, 2), enemy: (-32768, 0, 1)})
        self.assertFalse(self.matrix(mech).state(3).active)
        self.assertLess(vitals.total, 600)

    def test_early_release_keeps_authored_minimum_hold_then_stops(self):
        run, mech, enemy, vitals = self.prepared()
        for _ in range(10):
            run.step({enemy: (-32768, 0, 1)})
        self.assertTrue(self.matrix(mech).state(3).active)
        self.assertEqual(vitals.total, 600)
        for _ in range(35):
            run.step({enemy: (-32768, 0, 1)})
        self.assertFalse(self.matrix(mech).state(3).active)
        self.assertLess(vitals.total, 600)

    def test_world_or_nearer_body_wins_before_matrix_volume(self):
        for wall in (False, True):
            run, mech, enemy, vitals = self.prepared()
            fight = run.match.combat
            origin = (0, fight._eye(mech)[1], 15)
            bot = bots.Bot(0xA123, {
                "hero": "0x02E00000000001AC", "team": 7,
                "position": (0, 1, 13), "rotation": (0, 0, 0, 1), "graphs": [],
            }, {"name": "Collision fixture", "body": "0x0400000000001403", "health": 200}, {})
            extra = Target(bot.entity, bot.team, (0, 1, 13), 2, 0.5, 1.55, bot)
            extra_vitals = Vitals(extra.entity, 200)
            fight.vitals[extra.entity] = extra_vitals
            targets = fight._targets()
            if wall:
                run.match.collision_world = World([
                    (-10, -10, 12, 10, -10, 12, 10, 10, 12),
                    (-10, -10, 12, 10, 10, 12, -10, 10, 12),
                ])
            else:
                targets.append(extra)
            rocket = Rocket(enemy, enemy.team, origin, (0, 0, -1), 1000, 1,
                            HitDef(amount=20), None, definition=HELIX)
            fight.rockets.append(rocket)
            with patch.object(fight, "_targets", return_value=targets), \
                    patch.object(fight, "_landed", wraps=fight._landed) as landed:
                fight._fly(rocket)
                landed.assert_called_once_with(rocket)
            self.assertEqual(vitals.total, 600)
            self.assertEqual(extra_vitals.total, 200 if wall else 180)

    def test_authored_boxes_rotate_with_aim_and_keep_width_height_range(self):
        run, mech, _, _ = self.prepared()
        anchor = run.match.combat._eye(mech)
        def distance(origin, forward):
            return deflection.matrix_distance(mech, origin, (0, 0, -1), 15, anchor, forward)
        self.assertAlmostEqual(distance((0, anchor[1], 15), (0, 0, 1)), 4.75)
        self.assertIsNone(distance((1.26, anchor[1], 15), (0, 0, 1)))
        self.assertIsNone(distance((0, anchor[1] + 1.01, 15), (0, 0, 1)))
        self.assertIsNone(distance((0, anchor[1], 15), (0, 0, -1)))
        self.assertAlmostEqual(deflection.matrix_distance(
            mech, (15, anchor[1], 0), (-1, 0, 0), 30, anchor, (1, 0, 0)), 4.75)
        self.assertAlmostEqual(deflection.matrix_distance(
            mech, (0, anchor[1] + 15, 0), (0, -1, 0), 30, anchor, (0, 1, 0)), 4.75)

    def test_allied_matrix_cannot_absorb_shot_and_dead_matrix_is_ignored(self):
        run, mech, enemy, vitals = self.prepared()
        fight = run.match.combat
        origin = (0, fight._eye(mech)[1], 15)
        self.assertIsNotNone(fight._matrix_hit(enemy, origin, (0, 0, -1), 20, RIFLE))
        run.match.game_map = replace(run.match.game_map, free_for_all=False)
        enemy.team = mech.team
        self.assertIsNone(fight._matrix_hit(enemy, origin, (0, 0, -1), 20, RIFLE))
        enemy.team += 1
        vitals.died_at = fight.now
        self.assertIsNone(fight._matrix_hit(enemy, origin, (0, 0, -1), 20, RIFLE))

    def test_excluded_projectile_and_melee_bypass_matrix(self):
        run, mech, enemy, vitals = self.prepared()
        fight = run.match.combat
        self.assertTrue(self.matrix(mech).state(3).active)
        rocket = Rocket(enemy, enemy.team, (0, fight._eye(mech)[1], 2), (0, 0, -1),
                        200, 1, HitDef(amount=20), None, definition=FROST)
        fight.rockets.append(rocket)
        fight._fly(rocket)
        self.assertLess(vitals.total, 600)
        before = vitals.total
        enemy.mover.position = [0, 1, 1]
        enemy.mover.body = None
        for _ in range(25):
            run.step({mech: (0, 0, 2), enemy: (-32768, 0, 0x800)})
        self.assertLess(vitals.total, before)

    def test_absorption_destroys_confirmed_copy_without_explosion_release(self):
        run, _, enemy, _ = self.prepared()
        shot = projectiles.Shot(enemy, enemy.body, HELIX, run.frame, 1, 1, (1, 1))
        arrival = projectiles._ProjectileArrival(received=True)
        shot.created[0xA0001234] = arrival
        run.match.combat.projectiles.shots.append(shot)
        enemy.client.entities.clear()
        run.match.combat.projectiles.absorbed(shot)
        self.assertNotIn(shot, run.match.combat.projectiles.shots)
        [destroy] = enemy.client.entities
        self.assertEqual(destroy.entity, 0xA0001234)
        self.assertIsNone(destroy.ch2)


if __name__ == "__main__":
    unittest.main()
