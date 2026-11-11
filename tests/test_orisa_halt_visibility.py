"""Actual Halt casts use authored samples with explicit root-yaw/static geometry."""

import math
import os
import unittest
from unittest.mock import patch

import test_orisa_halt as fixture
import test_orisa_npc as npc_fixture

from ow174.game import orisa_halt
from ow174.game.collision import World
from ow174.game.combat import ray_world


class OrisaHaltVisibilityTests(unittest.TestCase):
    @staticmethod
    def cast(run, caster, target=None, yaw=0):
        for index in range(26):
            commands = {caster: (0, 0, 2 if index in (0, 25) else 0)}
            if target is not None:
                commands[target] = (yaw, 0, 0)
            run.step(commands)
        orb, = run.match.combat.orisa.orbs.values()
        return orb

    @staticmethod
    def geometry(run, caster, orb, feet, *, half=False, rod=True, legs=False):
        x, y, z = caster.mover.position
        faces = [(x - 30, y, z - 30, x - 30, y, z + 30, x + 30, y, z + 30),
                 (x - 30, y, z - 30, x + 30, y, z + 30, x + 30, y, z - 30)]

        def quad(a, b, c, d):
            for points in ((a, b, c), (a, c, d), (a, c, b), (a, d, c)):
                faces.append(tuple(axis for point in points for axis in point))

        ox, oy, oz = orb.rocket.position
        if rod:
            middle = (oz + feet[2]) / 2
            low, high = (x - 4, x + 0.05) if half else (x - 0.05, x + 0.05)
            quad((low, y - 2, middle), (high, y - 2, middle),
                 (high, y + 8, middle), (low, y + 8, middle))
        if legs:
            # Block the configured positive root X/Y/Z offset legs. Their main
            # segments can still be clear after the offset, so ignoring the
            # authored leg check would incorrectly permit the corner path.
            quad((ox + 0.15, oy - 0.06, oz - 0.06), (ox + 0.15, oy + 0.06, oz - 0.06),
                 (ox + 0.15, oy + 0.06, oz + 0.06), (ox + 0.15, oy - 0.06, oz + 0.06))
            quad((ox - 0.06, oy + 0.15, oz - 0.06), (ox + 0.06, oy + 0.15, oz - 0.06),
                 (ox + 0.06, oy + 0.15, oz + 0.06), (ox - 0.06, oy + 0.15, oz + 0.06))
            quad((ox - 0.06, oy - 0.06, oz + 0.15), (ox + 0.06, oy - 0.06, oz + 0.15),
                 (ox + 0.06, oy + 0.06, oz + 0.15), (ox - 0.06, oy + 0.06, oz + 0.15))
        run.match.collision_world = World(faces)

    @staticmethod
    def central_blocked(run, orb, feet):
        end = (feet[0], feet[1] + 1.3, feet[2])
        delta = tuple(b - a for a, b in zip(orb.rocket.position, end, strict=True))
        distance = math.sqrt(sum(v * v for v in delta))
        hit = ray_world(run.match.collision_world, orb.rocket.position,
                        tuple(v / distance for v in delta), distance)
        return hit is not None and hit < distance - 1e-6

    def test_authored_sample_finds_player_behind_thin_central_occluder(self):
        run, caster, target = fixture.HaltTests().arena()
        original = tuple(target.mover.position)
        orb = self.cast(run, caster, target)
        self.geometry(run, caster, orb, original)
        self.assertTrue(self.central_blocked(run, orb, original))
        run.step({})
        self.assertIn(target.body, run.match.combat.orisa.pulls)
        for _ in range(65):
            run.step({})
        self.assertNotEqual(tuple(target.mover.position), original)
        self.assertEqual(run.match.combat.vitals[target.body].total, 200)

    def test_authored_candidate_basis_tracks_actual_player_yaw(self):
        for yaw, expected in ((0, True), (-32768, False)):
            with self.subTest(yaw=yaw):
                run, caster, target = fixture.HaltTests().arena()
                original = tuple(target.mover.position)
                orb = self.cast(run, caster, target, yaw)
                self.geometry(run, caster, orb, original, half=True)
                self.assertTrue(self.central_blocked(run, orb, original))
                run.step({target: (yaw, 0, 0)})
                self.assertEqual(target.body in run.match.combat.orisa.pulls, expected)

    def test_offset_leg_obstructions_prevent_false_clear_corner_path(self):
        run, caster, target = fixture.HaltTests().arena()
        original = tuple(target.mover.position)
        orb = self.cast(run, caster, target)
        self.geometry(run, caster, orb, original, legs=True)
        self.assertTrue(self.central_blocked(run, orb, original))
        for _ in range(65):
            run.step({})
        self.assertEqual(tuple(target.mover.position), original)
        self.assertNotIn(target.body, run.match.combat.orisa.pulls)

    def test_failed_configured_legs_do_not_remove_existing_outer_fallback(self):
        run, caster, target = fixture.HaltTests().arena()
        orb = self.cast(run, caster, target)
        self.geometry(run, caster, orb, tuple(target.mover.position), rod=False, legs=True)
        self.assertFalse(self.central_blocked(run, orb, target.mover.position))
        run.step({})
        self.assertIn(target.body, run.match.combat.orisa.pulls)

    def test_missing_original_query_does_not_silently_use_legacy_targeting(self):
        with patch.object(orisa_halt, "query_graph", return_value=None):
            run, caster, target = fixture.HaltTests().arena()
        original = tuple(target.mover.position)
        self.cast(run, caster, target)
        for _ in range(65):
            run.step({})
        self.assertFalse(run.match.combat.orisa.pulls)
        self.assertEqual(tuple(target.mover.position), original)


@unittest.skipUnless(os.environ.get("ORPHEUS_NPC_RUNTIME"), "actual prepared Talon inputs required")
class OrisaHaltNpcVisibilityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        npc_fixture.OrisaTalonTests.setUpClass()
        cls.catalog = npc_fixture.OrisaTalonTests.catalog

    @classmethod
    def tearDownClass(cls):
        npc_fixture.OrisaTalonTests.tearDownClass()

    def test_actual_talon_cover_queries_preserve_heavy_authored_immunity(self):
        helper = OrisaHaltVisibilityTests()
        for definition in self.catalog.values():
            with self.subTest(npc=definition.name):
                run, caster, bot = npc_fixture.OrisaTalonTests().arena(definition, ahead=5)
                original = bot.position
                initial = run.match.combat.vitals[bot.entity].total
                orb = helper.cast(run, caster)
                helper.geometry(run, caster, orb, original)
                self.assertTrue(helper.central_blocked(run, orb, original))
                run.step({})
                manager = run.match.combat.orisa
                self.assertIn(bot.entity, manager.results[-1][1])
                immune = definition.name == "Heavy Assault"
                if immune:
                    variable = bot.body_script.component.entity_var(6601)
                    self.assertTrue(variable.value())
                    self.assertTrue(any(link.state.instance.graph.index == 0x1254
                                        and link.state.node.pos == 29 for link in variable.links))
                self.assertEqual(bot.entity in manager.pulls, not immune)
                for _ in range(65):
                    run.step({})
                self.assertEqual(bot.position == original, immune)
                self.assertEqual(run.match.combat.vitals[bot.entity].total, initial)
                self.assertIsNotNone(bot.body_script)


if __name__ == "__main__":
    unittest.main()
