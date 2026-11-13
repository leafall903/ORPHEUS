"""Uprising's first enemy wave: 0D4A spawns eight Slicers through 0D1C eight seconds into its defend phase."""

import math
import unittest

from ow174.game import pve
from ow174.game.collision import StaticCollisionProvider, World, cache_path, flat_world
from ow174.game.content import PRACTICE_RANGE, SOLDIER, heroes
from ow174.game.match import Match
from ow174.game.props import mode_identifiers
from tests.runtime.test_game_combat import in_world
from tests.runtime.test_uprising_mission import MAP, MODE, UPRISING

SLICER = 0x02E0000000000178
START = 100.0  # when the test's defend phase starts


def needs_pack(test):
    if pve.first_wave() is None or pve.wave_definition(pve.first_wave()) is None:
        test.skipTest("requires a pack with map_tags_174.json and uprising-npcs.json")
    if not cache_path(MAP).is_file():
        test.skipTest("requires the 06FE collision cache")


class FirstWaveDataTests(unittest.TestCase):
    def setUp(self):
        needs_pack(self)

    def test_the_wave_is_read_from_the_defend_director_and_the_spawner(self):
        wave = pve.first_wave()
        self.assertEqual(wave.hero, SLICER)  # 0D4A [50] v8398
        self.assertEqual((wave.total, wave.alive), (8, 8))  # v8744, v8397
        self.assertAlmostEqual(wave.delay, 8.1, places=5)  # Wait [33] 0.1 s into state 46, Wait [42] 8 s
        self.assertEqual(wave.team, 1)  # 0D1C [13] m_3EBF988B
        self.assertEqual((wave.arc, wave.near, wave.far), (225, 35, 70))
        self.assertEqual((wave.min_candidates, wave.max_candidates), (10, 300))  # 0D1C [7]
        for got, want in zip(wave.centre, (-19.2568, 0.1286, -34.2255), strict=True):
            self.assertAlmostEqual(got, want, places=4)  # the placeable tagged 0x7CA (record 973)
        self.assertGreater(wave.facing[2], 0.99)  # 0D4A [149]: toward the tag 339 marker

    def test_the_slicer_has_its_body_graph_health(self):
        definition = pve.wave_definition(pve.first_wave())
        self.assertEqual((definition.name, definition.health), ("Slicer", 200))


class FirstWaveTests(unittest.TestCase):
    def setUp(self):
        needs_pack(self)
        self.world = World.load(cache_path(MAP), mode_identifiers(MODE))
        self.match = Match(UPRISING, collision_provider=StaticCollisionProvider(self.world))
        self.player = self.match.add_player(1, "Alpha", 0, 0, False)
        in_world(self.match, self.player)
        self.match.tick = 100
        self.match.switch_hero(self.player, heroes()[SOLDIER])
        self.match.mission_ends = START + pve.DEFEND_SECONDS
        self.wave = pve.first_wave()

    def run_to(self, now):
        self.match.update(now, self.match.tick + 1)

    def test_nothing_spawns_before_the_wave_time(self):
        self.run_to(START + self.wave.delay - 0.05)
        self.assertIsNone(self.match.wave)
        self.assertEqual(self.match.wave_alive(), 0)

    def test_eight_slicers_on_the_enemy_team_at_valid_positions(self):
        self.run_to(START + self.wave.delay)
        bots = self.match.wave
        self.assertEqual(len(bots), 8)
        self.assertEqual(self.match.wave_alive(), 8)
        for bot in bots:
            self.assertIn(bot, self.match.bots)
            self.assertEqual((bot.hero, bot.team), (SLICER, 1))
            self.assertEqual(self.match.combat.vitals[bot.entity].total, 200)
            distance = math.dist(bot.position, self.wave.centre)
            self.assertGreaterEqual(distance, self.wave.near - 1e-3)
            self.assertLessEqual(distance, self.wave.far + 1e-3)
            x, y, z = bot.position
            floor = self.world.ground(x, z, y + 0.5, y - 0.5)
            self.assertIsNotNone(floor, bot.position)  # on the map's own walkable floor
            self.assertAlmostEqual(floor.y, y, places=3)
        self.assertEqual(len({bot.position for bot in bots}), 8)
        self.assertEqual(len({bot.entity for bot in bots}), 8)
        self.run_to(START + self.wave.delay + 1)
        self.assertEqual(len(self.match.wave), 8)  # created once

    def test_damage_hurts_and_a_kill_drops_the_alive_count(self):
        self.run_to(START + self.wave.delay)
        first = self.match.wave[0]
        target = next(t for t in self.match.combat._enemies(self.player) if t.owner is first)
        self.match.combat._damage(self.player, target, 50, False, None, self.match.tick)
        self.assertEqual(self.match.combat.vitals[first.entity].total, 150)
        self.assertEqual(self.match.wave_alive(), 8)
        self.match.combat._damage(self.player, target, 150, False, None, self.match.tick)
        self.assertTrue(first.dead)
        self.assertEqual(self.match.wave_alive(), 7)
        self.assertFalse(any(who is first for _, who in self.match.combat.respawns))

    def test_a_pack_without_the_wave_inputs_spawns_nothing(self):
        self.match.wave_spec = None
        self.run_to(START + self.wave.delay + 1)
        self.assertIsNone(self.match.wave)

    def test_the_spawn_points_are_the_shared_search(self):
        points = pve.wave_positions(self.world, self.wave)
        self.assertEqual(len(points), 8)
        self.assertEqual(points, pve.wave_positions(self.world, self.wave))  # deterministic
        self.assertTrue(all(isinstance(v, float) and math.isfinite(v) for p in points for v in p))


class OtherModesTests(unittest.TestCase):
    def test_no_wave_outside_uprising(self):
        match = Match(PRACTICE_RANGE, collision_provider=StaticCollisionProvider(flat_world(1.0)))
        self.assertIsNone(match.wave_spec)


if __name__ == "__main__":
    unittest.main()
