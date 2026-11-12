"""Cold Practice Range collision loading must prepare usable training patrols."""

import unittest
from unittest.mock import patch
from weakref import WeakKeyDictionary

from ow174.game import bots, collision
from ow174.game.content import PRACTICE_RANGE


@unittest.skipUnless(
    collision.cache_path(PRACTICE_RANGE.map_guid).is_file(),
    "requires the external 104319 Practice Range collision cache",
)
class PracticePatrolBootstrapTests(unittest.TestCase):
    def test_cold_map_load_prepares_patrols_and_training_bots_can_walk(self):
        # Use a fresh cache so an already-loaded match cannot hide bootstrap errors.
        with patch.object(bots, "_PATROL_PLANS", WeakKeyDictionary()):
            world = collision._read_cache(
                PRACTICE_RANGE.map_guid, PRACTICE_RANGE.name, False, frozenset(),
            )
            training = bots.make(PRACTICE_RANGE.map_guid)
            walkers = [bot for bot in training if bot.route]
            self.assertTrue(walkers)
            before = [tuple(bot.position) for bot in walkers]
            for tick in range(1, 251):
                for bot in walkers:
                    bot.step(tick, 0.016, world)
            self.assertTrue(any(tuple(bot.position) != start
                                for bot, start in zip(walkers, before, strict=True)))
