"""Winston Primal transform and punches through actual player inputs."""

import unittest
from dataclasses import replace

import test_game_sombra_npc as npc_fixture
import test_game_sombra_winston as winston_fixture
from test_game_sombra_orisa_halt import place
from test_game_sombra_reaper_blossom import charge
from test_game_sombra_reinhardt_charge import surface
from test_game_sombra_winston_jump import jump_state, step

from ow174.game import combat, winston_combat


def rage(fixture):
    fixture.b.body_script.set_ult_charge(1540)
    winston_fixture.drive(fixture, 32)


def first_punch(fixture, health, point=(0, 1.4, 0)):
    for _ in range(75):
        winston_fixture.drive(fixture, 1, point=point)
        if health():
            return
    raise AssertionError("authored punch did not make contact")


class SombraWinstonPrimalTests(unittest.TestCase):
    def arena(self, distance=1.5, wall=None):
        fixture, fight = winston_fixture.SombraWinstonTests().arena(distance)
        surface(fixture, wall)
        return fixture, fight

    def test_actual_primal_cost_refill_and_forty_damage_with_impulse(self):
        fixture, fight = self.arena()
        fixture.press(1, 12)
        self.assertLess(fixture.health(fixture.b), 500)
        fixture.b.body_script.set_ult_charge(1539)
        winston_fixture.drive(fixture, 32)
        self.assertNotIn(fixture.b.body, fight.winston_primal)
        winston_fixture.drive(fixture, 0)
        rage(fixture)
        self.assertEqual(fixture.health(fixture.b), 1000)
        self.assertEqual(charge(fixture.b), 0)
        winston_fixture.drive(fixture, 1, 10)
        self.assertEqual(fixture.health(fixture.a), 200)
        first_punch(fixture, lambda: fixture.health(fixture.a) < 200)
        self.assertEqual(fixture.health(fixture.a), 160)
        self.assertEqual(charge(fixture.b), 0)
        weapon = winston_combat.weapon_instance(fixture.b.body_script)
        self.assertEqual(weapon.vars[53].value(), 100)
        winston_fixture.drive(fixture, 0, 12)
        self.assertLess(fixture.a.mover.position[2], -1)

    def test_primal_expires_without_input_and_restores_tesla(self):
        fixture, fight = self.arena()
        rage(fixture)
        self.assertEqual(fixture.health(fixture.b), 1000)
        activated = fixture.run.frame
        fixture.run.frame = activated + 624
        fixture.run.match.update(fixture.run.frame * combat.TICK, fixture.run.frame, frames=False)
        self.assertEqual(fixture.health(fixture.b), 1000)
        fixture.run.frame = activated + 630
        fixture.run.match.update(fixture.run.frame * combat.TICK, fixture.run.frame, frames=False)
        self.assertEqual(fixture.health(fixture.b), 500)
        self.assertNotIn(fixture.b.body, fight.winston_primal)
        self.assertEqual(fixture.health(fixture.a), 200)
        winston_fixture.drive(fixture, 1, 25)
        self.assertAlmostEqual(200 - fixture.health(fixture.a), charge(fixture.b), places=4)
        self.assertLess(fixture.health(fixture.a), 200)

    def test_primal_jump_resets_existing_cooldown_and_uses_two_seconds(self):
        fixture, fight = self.arena(8)
        step(fixture, 8)
        root = jump_state(fixture).instance
        self.assertGreater(root.state(19).remaining(), 5.9)
        rage(fixture)
        self.assertIn(fixture.b.body, fight.winston_primal)
        self.assertLess(root.state(19).remaining(), .1)
        first = jump_state(fixture).activation
        step(fixture, 8)
        self.assertEqual(jump_state(fixture).activation, first)
        for _ in range(35):
            step(fixture)
        step(fixture, 8)
        self.assertGreater(jump_state(fixture).activation, first)
        self.assertGreater(fixture.b.mover.velocity[1], 29)
        self.assertGreater(root.state(19).remaining(), 1.9)
        self.assertLessEqual(root.state(19).remaining(), 2)
        second = jump_state(fixture).activation
        for _ in range(128):
            step(fixture)
        self.assertTrue(jump_state(fixture).flying)
        self.assertLess(fixture.b.mover.velocity[1], 0)
        step(fixture, 8)
        self.assertGreater(jump_state(fixture).activation, second)
        self.assertGreater(fixture.b.mover.velocity[1], 29)
        self.assertEqual(charge(fixture.b), 0)

    def test_paid_emp_blocks_primal_jump_but_preserves_transform_and_punch(self):
        fixture, fight = self.arena()
        rage(fixture)
        fixture.a.body_script.set_ult_charge(1400)
        fixture.press(32, 55)
        self.assertIn(fixture.b.body, fight.hacks)
        self.assertEqual(fixture.health(fixture.b), 1000)
        step(fixture, 8)
        self.assertNotIn(fixture.b.body, fight.winston_jumps)
        first_punch(fixture, lambda: fixture.health(fixture.a) < 200)
        self.assertEqual(fixture.health(fixture.a), 160)

    def test_primal_punch_misses_beyond_range_behind_cover_and_for_ally(self):
        for case in ("range", "wall", "ally"):
            with self.subTest(case=case):
                fixture, _ = self.arena(5 if case == "range" else 1.8,
                                        .85 if case == "wall" else None)
                if case == "ally":
                    match = fixture.run.match
                    match.game_map = replace(match.game_map, free_for_all=False)
                    fixture.a.team = fixture.b.team
                rage(fixture)
                winston_fixture.drive(fixture, 1, 62)
                self.assertEqual(fixture.health(fixture.a), 200)


class SombraWinstonPrimalNpcTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        npc_fixture.SombraActualNpcTests.setUpClass.__func__(cls)

    @classmethod
    def restore_graphs(cls, path):
        npc_fixture.SombraActualNpcTests.restore_graphs(path)

    def test_tesla_earns_all_primal_charge_against_original_heavy_and_pays_for_cast(self):
        fixture, bot = npc_fixture.SombraActualNpcTests.make(self, "5")
        fight = winston_fixture.prepare(fixture, 3)
        surface(fixture)
        place(fixture.a, (10, 0, 0))
        bot.position = (0, 0, 0)
        vitals = fight.vitals[bot.entity]
        self.assertEqual(vitals.total, 4000)
        self.assertEqual(charge(fixture.b), 0)
        for _ in range(2800):
            winston_fixture.drive(fixture, 1)
            if charge(fixture.b) >= 1540:
                break
        self.assertEqual(charge(fixture.b), 1540)
        self.assertGreater(4000 - vitals.total, 1539)
        winston_fixture.drive(fixture, 0)
        winston_fixture.drive(fixture, 32)
        self.assertEqual(charge(fixture.b), 0)
        self.assertEqual(fixture.health(fixture.b), 1000)

    def test_actual_primal_punch_hits_all_five_original_npcs(self):
        self.assertEqual(len(self.catalog), 5)
        for key, definition in self.catalog.items():
            with self.subTest(npc=definition.name):
                fixture, bot = npc_fixture.SombraActualNpcTests.make(self, key)
                fight = winston_fixture.prepare(fixture, 1.15)
                surface(fixture)
                place(fixture.a, (10, 0, 0))
                bot.position = (0, 0, 0)
                vitals = fight.vitals[bot.entity]
                before = vitals.total
                rage(fixture)
                target = next(t for t in fight._targets() if t.owner is bot)
                first_punch(fixture, lambda v=vitals, hp=before: v.total < hp, (0, target.height * .55, 0))
                self.assertAlmostEqual(before - vitals.total, 40)
                self.assertEqual(charge(fixture.b), 0)
