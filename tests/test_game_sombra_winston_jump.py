"""Jump Pack launch and collision landing through normal commands and world ticks."""

import unittest
from dataclasses import replace

import test_game_sombra_npc as npc_fixture
import test_game_sombra_winston as winston_fixture
from test_game_sombra_orisa_halt import place
from test_game_sombra_reaper_blossom import charge
from test_game_sombra_reinhardt_charge import surface

from ow174.game import combat
from ow174.game.world import ARMOUR_POOL, HEALTH_POOL


def step(fixture, buttons=0, *, vertical=True):
    fixture.run.step({fixture.b: (0, -16384 if vertical else 0, buttons)})


def jump_state(fixture):
    return fixture.run.match.combat.winston_jumps[fixture.b.body]


def finish(fixture):
    for _ in range(160):
        step(fixture)
        if not jump_state(fixture).flying:
            return
    raise AssertionError("Jump Pack did not land within the fixture")


class SombraWinstonJumpTests(unittest.TestCase):
    def arena(self, distance=8, wall=None):
        fixture, fight = winston_fixture.SombraWinstonTests().arena(distance)
        surface(fixture, wall)
        return fixture, fight

    def test_authored_takeoff_and_landing_damage_charge_and_cooldown(self):
        fixture, _ = self.arena(2)
        step(fixture, 8)
        self.assertTrue(jump_state(fixture).flying)
        self.assertAlmostEqual(fixture.b.mover.velocity[1], 30)
        self.assertEqual(fixture.health(fixture.a), 199)
        root = jump_state(fixture).instance
        self.assertGreater(root.state(19).remaining(), 5.9)
        place(fixture.a, (0, 0, 2))
        finish(fixture)
        health = fixture.health(fixture.a)
        self.assertGreater(health, 149)
        self.assertLess(health, 199)
        self.assertGreater(abs(fixture.a.mover.position[2] - 2), .5)
        self.assertAlmostEqual(charge(fixture.b), 200 - health, places=4)
        self.assertFalse(root.state(2).active)
        step(fixture)
        step(fixture, 8)
        self.assertFalse(jump_state(fixture).flying)
        self.assertEqual(fixture.health(fixture.a), health)

    def test_horizontal_cast_moves_toward_aim_then_lands(self):
        fixture, _ = self.arena()
        place(fixture.a, (8, 0, 0))
        step(fixture, 8, vertical=False)
        self.assertGreater(fixture.b.mover.velocity[1], 12)
        self.assertGreater(fixture.b.mover.velocity[2], 27)
        for _ in range(20):
            step(fixture)
        self.assertGreater(fixture.b.mover.position[1], 2)
        self.assertGreater(fixture.b.mover.position[2], 12)
        finish(fixture)
        self.assertEqual(fixture.health(fixture.a), 200)

    def test_world_only_replay_matches_collision_landing_once(self):
        results = []
        for gap in (False, True):
            fixture, _ = self.arena()
            step(fixture, 8)
            place(fixture.a, (0, 0, 8))
            if gap:
                fixture.run.frame += 155
                fixture.run.match.update(fixture.run.frame * combat.TICK, fixture.run.frame, frames=False)
                self.assertFalse(jump_state(fixture).flying)
            else:
                finish(fixture)
            results.append((fixture.health(fixture.a), charge(fixture.b), tuple(fixture.b.mover.position)))
            for _ in range(6):
                step(fixture)
            self.assertEqual(fixture.health(fixture.a), 150)
        self.assertEqual(results[0][:2], (150, 50))
        self.assertEqual(results[0], results[1])

    def test_allied_sombra_is_ignored_at_takeoff_and_landing(self):
        fixture, _ = self.arena(1)
        match = fixture.run.match
        match.game_map = replace(match.game_map, free_for_all=False)
        fixture.a.team = fixture.b.team
        step(fixture, 8)
        finish(fixture)
        self.assertEqual(fixture.health(fixture.a), 200)
        self.assertEqual(charge(fixture.b), 0)

    def test_hack_blocks_takeoff_until_it_expires(self):
        fixture, fight = self.arena(4)
        fixture.press(2, 60)
        self.assertIn(fixture.b.body, fight.hacks)
        step(fixture, 8)
        self.assertNotIn(fixture.b.body, fight.winston_jumps)
        for _ in range(375):
            step(fixture)
        self.assertNotIn(fixture.b.body, fight.hacks)
        step(fixture, 8)
        self.assertTrue(jump_state(fixture).flying)

    def test_actual_pistol_kill_retires_airborne_jump_without_landing_damage(self):
        fixture, fight = self.arena()
        victim = next(t for t in fight._targets() if t.owner is fixture.b)
        vitals = fight.vitals_of(victim)
        vitals.now[ARMOUR_POOL] = 0
        vitals.now[HEALTH_POOL] = 1
        step(fixture, 8)
        self.assertTrue(jump_state(fixture).flying)
        fixture.press(1, 20)
        self.assertTrue(fight.is_dead(fixture.b))
        self.assertNotIn(fixture.b.body, fight.winston_jumps)
        self.assertEqual(vitals.killer, fixture.a.body)
        place(fixture.a, (0, 0, 8))
        for _ in range(160):
            step(fixture)
        self.assertEqual(fixture.health(fixture.a), 200)

    def test_wall_blocks_landing_damage(self):
        fixture, _ = self.arena(8, wall=7)
        step(fixture, 8)
        place(fixture.a, (0, 0, 6))
        finish(fixture)
        self.assertEqual(fixture.health(fixture.a), 200)
        self.assertEqual(charge(fixture.b), 0)


class SombraWinstonJumpNpcTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        npc_fixture.SombraActualNpcTests.setUpClass.__func__(cls)

    @classmethod
    def restore_graphs(cls, path):
        npc_fixture.SombraActualNpcTests.restore_graphs(path)

    def test_all_five_actual_npcs_take_launch_and_landing_damage(self):
        self.assertEqual(len(self.catalog), 5)
        for key, definition in self.catalog.items():
            with self.subTest(npc=definition.name):
                fixture, bot = npc_fixture.SombraActualNpcTests.make(self, key)
                fight = winston_fixture.prepare(fixture, 8)
                surface(fixture)
                place(fixture.a, (10, 0, 0))
                bot.position = (0, 0, 7)
                vitals = fight.vitals[bot.entity]
                before = vitals.total
                step(fixture, 8)
                self.assertAlmostEqual(before - vitals.total, 1)
                bot.position = (0, 0, 8)
                finish(fixture)
                damage = before - vitals.total
                self.assertGreater(damage, 1)
                self.assertLess(damage, 51)
                self.assertNotEqual(bot.position, (0, 0, 8))
                self.assertAlmostEqual(charge(fixture.b), damage, places=4)
