"""Movement states the heroes' own graphs run on the server: Sombra's Stealth, Mercy's Angelic Descent.

The numbers are those measured in the original client against another service: Stealth's 65 % after its
0.75 s fade, and a fall held to 2 m/s.
"""

import math
import unittest

from test_game_combat import Run

from ow174.game import combat
from ow174.game.collision import flat_world
from ow174.game.commands import JUMP, Command
from ow174.game.content import heroes

SOMBRA, MERCY = 0x02E000000000012E, 0x02E0000000000004
ABILITY_1 = 8  # the command's Shift bit
WALK = 5.5  # m/s


class Arena:
    def __init__(self, hero: int) -> None:
        self.run = Run()
        self.match, self.player = self.run.match, self.run.players[0]
        assert self.match.switch_hero(self.player, heroes()[hero])
        self.match.bots.clear()
        world = flat_world(0)
        self.match.collision_world = world
        self.player.mover.use(world=world)
        self.player.mover.position[:] = (0, 0, 0)
        self.steps(30)

    def steps(self, count: int, buttons: int = 0, forward: int = 0) -> None:
        for _ in range(count):
            self.run.frame += 1
            self.match.update(self.run.frame * combat.TICK, self.run.frame)
            self.player.take_commands([Command(self.run.frame, buttons=buttons, forward=forward)])

    @property
    def speed(self) -> float:
        return math.hypot(*self.player.mover.velocity[::2])


class SombraStealthTests(unittest.TestCase):
    def test_stealth_adds_its_speed_after_the_fade_and_shift_ends_it(self):
        arena = Arena(SOMBRA)
        arena.steps(60, forward=127)
        self.assertAlmostEqual(arena.speed, WALK, delta=0.01)
        arena.steps(1, ABILITY_1, forward=127)
        arena.steps(40, forward=127)  # 0.64 s: still fading
        self.assertAlmostEqual(arena.speed, WALK, delta=0.01)
        arena.steps(20, forward=127)  # past 0.75 s
        self.assertAlmostEqual(arena.speed, WALK * 1.65, delta=0.01)
        arena.steps(1, ABILITY_1, forward=127)
        arena.steps(60, forward=127)
        self.assertAlmostEqual(arena.speed, WALK, delta=0.01)


class MercyDescentTests(unittest.TestCase):
    def fall(self, held: bool) -> tuple[float, float]:
        """(seconds in the air, fastest fall) of one jump, with the jump button held or let go."""
        arena = Arena(MERCY)
        arena.steps(1, JUMP)
        frames, fastest = 0, 0.0
        while frames < 200:
            arena.steps(1, JUMP if held else 0)
            frames += 1
            fastest = min(fastest, arena.player.mover.velocity[1])
            if frames > 5 and arena.player.mover.position[1] <= 0.001:
                break
        return frames * combat.TICK, fastest

    def test_a_held_jump_falls_at_two_metres_a_second(self):
        tapped, free = self.fall(held=False)
        held, slowed = self.fall(held=True)
        self.assertLess(free, -5.0)
        self.assertAlmostEqual(slowed, -2.0, delta=0.01)
        self.assertGreater(held, tapped + 0.2)


if __name__ == "__main__":
    unittest.main()
