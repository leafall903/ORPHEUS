"""Wrecking Ball's authored mover data at the Roll transition."""

import math
import unittest

from ow174.game.collision import StaticCollisionProvider, flat_world
from ow174.game.commands import CROUCH, Command
from ow174.game.content import PRACTICE_RANGE, heroes
from ow174.game.match import Match
from tests.runtime.test_game_combat import in_world

WRECKING_BALL = 0x02E00000000001CA
ABILITY_1 = 0x08


class RollTests(unittest.TestCase):
    def test_roll_uses_its_authored_mover_on_the_next_frame_and_restores_gun_form(self):
        hero = heroes()[WRECKING_BALL]
        floor = flat_world(PRACTICE_RANGE.spawns[0].position[1])
        match = Match(PRACTICE_RANGE, collision_provider=StaticCollisionProvider(floor))
        player = match.add_player(1, "Hammond", hero.guid, 0, False)
        in_world(match, player)
        match.tick = 1000
        match.switch_hero(player, hero)

        def frame(number, buttons=0, forward=127):
            match.tick = number
            player.take_commands([Command(number, buttons=buttons, forward=forward)])

        def speed():
            vx, _, vz = player.mover.velocity
            return math.hypot(vx, vz)

        for number in range(1001, 1080):
            frame(number, forward=0)
        frame(1080, ABILITY_1)
        self.assertEqual((player.mover.data.run_forward, player.mover.data.stand_radius), (5.5, 0.7))

        frame(1081)
        self.assertEqual((player.mover.values.run_forward, player.mover.values.stand_radius), (10.0, 0.5))
        for number in range(1082, 1130):
            frame(number, CROUCH if number >= 1090 else 0)
        self.assertAlmostEqual(speed(), 10.0, delta=0.01)
        self.assertFalse(player.mover.crouched)

        frame(1130, ABILITY_1)
        self.assertEqual(player.mover.values.stand_radius, 0.5)
        frame(1131)
        self.assertEqual((player.mover.values.run_forward, player.mover.values.stand_radius), (5.5, 0.7))
        for number in range(1132, 1140):
            frame(number)
        self.assertAlmostEqual(speed(), 5.5, delta=0.01)
