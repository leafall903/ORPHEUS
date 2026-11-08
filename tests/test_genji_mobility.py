"""Command/collision checks for explicitly reconstructed Genji passive mobility."""

import unittest

from test_game_mover import FLOOR, box, wall
from test_genji_combat import GENJI, GenjiFixture

from ow174.game.collision import World
from ow174.game.commands import Command
from ow174.game.content import SOLDIER, heroes
from ow174.game.mover import JUMP_INTENT, SET, Mod


class GenjiMobilityTests(GenjiFixture):
    def step(self, run, player, *, buttons=0, forward=0, yaw=0):
        run.frame += 1
        run.match.update(run.frame * 0.016, run.frame)
        player.take_commands([Command(run.frame, buttons=buttons, forward=forward, yaw=yaw)])

    def airborne(self, run, player):
        for frame in range(20):
            self.step(run, player, buttons=64 if frame == 0 else 0)
        self.assertTrue(player.mover.airborne)

    def wall_scene(self, ceiling=False):
        run, player = self.spawn()
        geometry = FLOOR + wall(-5, 0.8, 5, 0.8, y1=20)
        if ceiling:
            geometry += box(-5, 5, 4, 5, -5, 5)
        run.match.collision_world = World(geometry)
        return run, player

    def test_second_press_adds_one_authored_jump_impulse(self):
        run, player = self.spawn()
        self.airborne(run, player)
        before = player.mover.position[1]
        self.step(run, player, buttons=64)
        self.assertTrue(player.genji_mobility.air_jump_used)
        self.assertGreater(player.mover.velocity[1], 5)
        self.assertGreater(player.mover.position[1], before)

    def test_holding_jump_does_not_repeat_in_air(self):
        run, player = self.spawn()
        highest = 0
        for _ in range(40):
            self.step(run, player, buttons=64)
            highest = max(highest, player.mover.position[1])
        self.assertFalse(player.genji_mobility.air_jump_used)
        self.assertLess(highest, 2.2)

    def test_third_press_cannot_add_another_impulse(self):
        run, player = self.spawn()
        self.airborne(run, player)
        self.step(run, player, buttons=64)
        for _ in range(14):
            self.step(run, player)
        self.step(run, player, buttons=64)
        self.assertLess(player.mover.velocity[1], 2)
        self.assertTrue(player.genji_mobility.air_jump_used)

    def test_landing_refreshes_double_jump(self):
        run, player = self.spawn()
        self.airborne(run, player)
        self.step(run, player, buttons=64)
        for _ in range(120):
            self.step(run, player)
        self.assertFalse(player.mover.airborne)
        self.assertFalse(player.genji_mobility.air_jump_used)
        self.airborne(run, player)
        self.step(run, player, buttons=64)
        self.assertGreater(player.mover.velocity[1], 5)

    def test_other_hero_does_not_get_genji_air_jump(self):
        run, player = self.spawn()
        run.match.switch_hero(player, heroes()[SOLDIER])
        player.mover.position[:] = [0, 1, 0]
        self.airborne(run, player)
        self.step(run, player, buttons=64)
        self.assertLess(player.mover.velocity[1], 1)

    def test_jump_intent_block_does_not_queue_a_later_air_jump(self):
        run, player = self.spawn()
        self.airborne(run, player)
        key = (999, 1, 0)
        player.mover.mods[key] = Mod(JUMP_INTENT, SET, 0, run.frame + 1, key=key)
        self.step(run, player, buttons=64)
        self.assertFalse(player.genji_mobility.air_jump_used)
        self.step(run, player, buttons=64)
        self.assertFalse(player.genji_mobility.air_jump_used)

    def test_new_body_resets_passive_resources(self):
        run, player = self.spawn()
        self.airborne(run, player)
        self.step(run, player, buttons=64)
        run.match.switch_hero(player, heroes()[GENJI])
        self.assertFalse(player.genji_mobility.air_jump_used)
        self.assertFalse(player.genji_mobility.jump_held)
        self.assertFalse(player.genji_mobility.climb_spent)

    def test_death_stops_air_mobility(self):
        run, player = self.spawn()
        self.airborne(run, player)
        fight = run.match.combat
        target = next(t for t in fight._targets() if t.owner is player)
        fight._damage(run.players[1], target, 200, False, None, run.frame)
        position = tuple(player.mover.position)
        self.step(run, player, buttons=64)
        self.assertEqual(tuple(player.mover.position), position)

    def test_wall_climb_uses_authored_speed_and_exhausts(self):
        run, player = self.wall_scene()
        velocities = []
        for _ in range(80):
            self.step(run, player, buttons=64, forward=127)
            if player.genji_mobility.climbing:
                velocities.append(player.mover.velocity[1])
        self.assertGreater(len(velocities), 40)
        self.assertTrue(all(abs(v - 7.5) < 0.01 for v in velocities))
        self.assertTrue(player.genji_mobility.climb_spent)
        self.assertFalse(player.genji_mobility.climbing)
        self.assertGreaterEqual(player.genji_mobility.climb_elapsed, 0.75)
        self.assertLess(player.genji_mobility.climb_elapsed, 0.77)

    def test_without_wall_jump_does_not_climb(self):
        run, player = self.spawn()
        for _ in range(40):
            self.step(run, player, buttons=64, forward=127)
        self.assertFalse(player.genji_mobility.climbing)
        self.assertEqual(player.genji_mobility.climb_elapsed, 0)

    def test_wall_behind_or_no_forward_does_not_climb(self):
        for forward, yaw in ((0, 0), (127, -32768)):
            with self.subTest(forward=forward, yaw=yaw):
                run, player = self.wall_scene()
                for _ in range(40):
                    self.step(run, player, buttons=64, forward=forward, yaw=yaw)
                self.assertEqual(player.genji_mobility.climb_elapsed, 0)

    def test_release_cancels_climb_and_refreshes_one_air_jump(self):
        run, player = self.wall_scene()
        for _ in range(20):
            self.step(run, player, buttons=64, forward=127)
        self.assertTrue(player.genji_mobility.climbing)
        self.step(run, player)
        self.assertFalse(player.genji_mobility.climbing)
        self.assertTrue(player.genji_mobility.climb_spent)
        self.step(run, player, buttons=64)
        self.assertTrue(player.genji_mobility.air_jump_used)
        self.assertGreater(player.mover.velocity[1], 5)

    def test_ceiling_collision_prevents_climbing_through_geometry(self):
        run, player = self.wall_scene(ceiling=True)
        highest = 0
        for _ in range(80):
            self.step(run, player, buttons=64, forward=127)
            highest = max(highest, player.mover.position[1])
        self.assertLess(highest, 2.2)

    def test_missing_command_gap_cannot_extend_climb_duration(self):
        run, player = self.wall_scene()
        for _ in range(20):
            self.step(run, player, buttons=64, forward=127)
        self.assertTrue(player.genji_mobility.climbing)
        run.frame += 200
        self.step(run, player, buttons=64, forward=127)
        self.assertFalse(player.genji_mobility.climbing)
        self.assertTrue(player.genji_mobility.climb_spent)


if __name__ == "__main__":
    unittest.main()
