"""Resource25/3 Whip impulse through the existing capsule mover, reconstructed."""

import unittest

import test_game_brigitte_npcs as npc_tests
from test_game_mover import box

from ow174.game.collision import StaticCollisionProvider, World
from ow174.game.mover import IN_AIR, mover_data


class WhipMotionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        npc_tests.ActualNpcTests.setUpClass()
        cls.catalog, cls.bundle = npc_tests.ActualNpcTests.catalog, npc_tests.ActualNpcTests.bundle

    setUp = npc_tests.ActualNpcTests.setUp
    restore = npc_tests.ActualNpcTests.restore
    fixture = npc_tests.ActualNpcTests.fixture

    def scene(self, definition, *, wall=False, allied=False):
        run, player, bot, target = self.fixture(definition, allied=allied)
        x, y, z = player.mover.position
        bot.position = (x, y, z + 5)
        triangles = box(x - 30, x + 30, y - 1, y, z - 30, z + 30)
        if wall:
            triangles += box(x - 8, x + 8, y, y + 6, z + 7, z + 7.3)
        collision = World(triangles)
        run.match.collision_provider = StaticCollisionProvider(collision)
        run.match.collision_world = collision
        for p in run.players:
            p.mover.use(world=collision)
        return run, player, bot, target

    def flight(self, run, player, bot, frames=110):
        positions, records = [], []
        for frame in range(frames):
            run.step({player: (0, 0, 8 if frame == 0 else 0)})
            positions.append(bot.position)
            records.append(bot.movement(run.frame))
        return positions, records

    def test_real_whip_lifts_moves_lands_and_stops_all_five_bodies(self):
        for definition in self.catalog.values():
            with self.subTest(npc=definition.name):
                run, player, bot, target = self.scene(definition)
                before = run.match.combat.vitals_of(target).total
                start = bot.position
                positions, records = self.flight(run, player, bot)
                self.assertEqual(run.match.combat.vitals_of(target).total, before - 70)
                self.assertGreater(max(p[1] for p in positions), start[1] + .1)
                self.assertGreater(bot.position[2], start[2] + 2)
                self.assertAlmostEqual(bot.position[1], start[1], delta=.02)
                self.assertEqual(bot.velocity, (0, 0, 0))
                self.assertTrue(any(r.flags & IN_AIR for r in records))
                self.assertIsNone(bot.impulse_mover)
                self.assertEqual(records[-1].flags & IN_AIR, 0)

    def test_real_whip_capsule_stops_before_wall_for_all_five_bodies(self):
        for definition in self.catalog.values():
            with self.subTest(npc=definition.name):
                run, player, bot, target = self.scene(definition, wall=True)
                before = run.match.combat.vitals_of(target).total
                start = bot.position
                positions, _ = self.flight(run, player, bot)
                self.assertEqual(run.match.combat.vitals_of(target).total, before - 70)
                self.assertGreater(bot.position[2], start[2] + .5)
                radius = mover_data(bot.body).stand_radius
                self.assertLessEqual(max(p[2] for p in positions), start[2] + 2 - radius + .02)
                self.assertAlmostEqual(bot.position[1], start[1], delta=.02)
                self.assertEqual(bot.velocity, (0, 0, 0))

    def test_friendly_whip_never_moves_or_damages_all_five_bodies(self):
        for definition in self.catalog.values():
            with self.subTest(npc=definition.name):
                run, player, bot, target = self.scene(definition, allied=True)
                start, before = bot.position, run.match.combat.vitals_of(target).total
                self.flight(run, player, bot)
                self.assertEqual(bot.position, start)
                self.assertEqual(run.match.combat.vitals_of(target).total, before)

    def test_zero_and_double_live_rule_change_npc_motion_without_damage_change(self):
        definition = next(d for d in self.catalog.values() if d.name == 'Trooper')
        distances = []
        for scalar in (0, 1, 2):
            with self.subTest(scalar=scalar):
                run, player, bot, target = self.scene(definition)
                start, before = bot.position, run.match.combat.vitals_of(target).total
                player.body_script.component.set(14131, scalar)
                positions, _ = self.flight(run, player, bot, frames=190)
                self.assertEqual(run.match.combat.vitals_of(target).total, before - 70)
                distances.append(bot.position[2] - start[2])
                if scalar == 0:
                    self.assertTrue(all(p == start for p in positions))
                else:
                    self.assertGreater(max(p[1] for p in positions), start[1] + .1)
        self.assertEqual(distances[0], 0)
        self.assertGreater(distances[2], distances[1])

    def test_player_whip_also_lifts_then_lands_in_actual_command_simulation(self):
        import test_game_brigitte as brigitte_tests

        run, player = brigitte_tests.BrigitteTests().fixture()
        enemy = run.players[1]
        x, y, z = player.mover.position
        enemy.mover.position, enemy.mover.body = [x, y, z + 5], None
        points = []
        for frame in range(110):
            run.step({player: (0, 0, 8 if frame == 0 else 0)})
            points.append(tuple(enemy.mover.position))
        self.assertGreater(max(p[1] for p in points), y + .1)
        self.assertAlmostEqual(enemy.mover.position[1], y, delta=.02)
        self.assertGreater(enemy.mover.position[2], z + 7)

    def test_lethal_whip_stops_body_without_starting_impulse(self):
        for definition in self.catalog.values():
            with self.subTest(npc=definition.name):
                run, player, bot, target = self.scene(definition)
                vitals = run.match.combat.vitals_of(target)
                vitals.damage(vitals.total - 50)
                start = bot.position
                self.flight(run, player, bot)
                self.assertTrue(bot.dead)
                self.assertEqual(bot.position, start)
                self.assertIsNone(bot.impulse_mover)


if __name__ == '__main__':
    unittest.main()
