"""Genji returns an actual curved Pharah Barrage rocket."""

import unittest

import test_game_sombra_barrier as barriers
import test_game_sombra_pharah as pharah_fixture
from test_game_combat import aim
from test_game_sombra_orisa_halt import place

from ow174.game import combat, pharah, shot_guards
from ow174.game.content import SOLDIER, heroes
from ow174.game.server_snapshot import SCHEMA, SCOPE, _Graph, snapshot_digest


class SombraBarrageReflectionTests(unittest.TestCase):
    def reflect(self, retire_source=False):
        f, genji = barriers.SombraBarrierTests().projected_arena()
        fight = pharah_fixture.prepare(f)
        genji.spawned = True
        self.assertTrue(f.run.match.switch_hero(genji, heroes()[shot_guards.GENJI]))
        place(genji, (0, 0, 3))
        f.a.team, f.b.team, genji.team = 0, 1, 0
        f.press(0, 50)
        angles = aim(fight._eye(f.b), (0, 1.45, 0))
        f.b.body_script.set_ult_charge(2100)
        for frame in range(24):
            f.run.step({genji: (0, 0, 16 if frame == 0 else 0),
                        f.b: (*angles, 32 if frame == 4 else 0)})
            if retire_source and frame == 5:
                self.assertTrue(f.run.match.switch_hero(f.b, heroes()[SOLDIER]))
                place(f.b, (0, 0, 6))
            rocket = next((r for r in fight.rockets
                           if r.shooter is genji and r.definition == pharah.BARRAGE_ENTITY), None)
            if rocket is not None:
                self.assertEqual(rocket.source_body, genji.body)
                self.assertLess(rocket.flown, combat.TICK)
                self.assertAlmostEqual(rocket.left + rocket.flown, 5)
                self.assertGreater(rocket.range_left, 99)
                self.assertLess(rocket.position[2], 4)
                self.assertAlmostEqual(rocket.position[1], 1.20, delta=.06)
                return f, genji, rocket, angles
        self.fail("No Barrage rocket was reflected")

    def test_restarts_flight_and_resamples_when_source_exists(self):
        f, genji, rocket, angles = self.reflect()
        self.assertNotEqual(rocket.direction, (0., 0., 1.))
        self.assertGreater(rocket.curve_phase, 0)
        for _ in range(16):
            f.run.step({genji: (0, 0, 0), f.b: (*angles, 0)})
        self.assertLess(f.health(f.b), 200)
        self.assertEqual(f.health(f.a), 200)

    def test_restarts_on_raw_aim_after_source_body_replacement(self):
        _, _, rocket, _ = self.reflect(retire_source=True)
        self.assertEqual(rocket.direction, (0., 0., 1.))


    def test_reflection_snapshot_tracks_the_curve_phase_and_saved_source(self):
        _, genji, rocket, _ = self.reflect()

        def digest():
            encoder = _Graph({"owner": genji}, None)
            root = encoder.value(rocket)
            return snapshot_digest({
                "schema": SCHEMA, "scope": SCOPE, "restorable": False,
                "roots": {"rocket": root}, "nodes": encoder.finish(), "assets": {},
            })

        original = digest()
        self.assertEqual(digest(), original)
        rocket.curve_phase = 0
        self.assertNotEqual(digest(), original)
