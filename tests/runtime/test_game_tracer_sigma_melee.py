"""Actual quick-melee inputs in both directions between Tracer and Sigma."""

import unittest

import test_game_tracer_sigma_accretion as accretion_tests
from test_game_combat import aim

from ow174.game import tracer


class TracerSigmaMeleeTests(unittest.TestCase):
    def setUp(self):
        accretion_tests.TracerSigmaAccretionTests.setUp(self)
        x, y, z = self.tracer.mover.position
        self.sigma.mover.position = [x, y, z + 2]
        self.sigma.mover.body = None
        self.run.step({})
        self.sigma_aim = aim(self.fight._eye(self.sigma), (x, y + 1, z))
        self.tracer_aim = aim(self.fight._eye(self.tracer), (x, y + 1, z + 2))
        target = next(t for t in self.fight._targets() if t.owner is self.sigma)
        self.sigma_vitals = self.fight.vitals_of(target)

    def exchange(self):
        self.run.step({self.tracer: (*self.tracer_aim, 0x800), self.sigma: (*self.sigma_aim, 0x800)})
        for _ in range(65):
            self.run.step({})

    def test_open_path_quick_melee_does_thirty_once_in_both_directions(self):
        self.exchange()
        self.assertEqual(self.vitals.total, 120)
        self.assertEqual(self.sigma_vitals.total, 370)

    def test_wall_blocks_quick_melee_in_both_directions(self):
        x, y, z = self.tracer.mover.position
        accretion_tests.TracerSigmaAccretionTests.wall(self, x, y, z + 0.8, z + 1.2)
        self.exchange()
        self.assertEqual(self.vitals.total, 150)
        self.assertEqual(self.sigma_vitals.total, 400)

    def test_actual_recall_protects_tracer_from_sigma_quick_melee(self):
        self.run.step({self.tracer: (*self.tracer_aim, 16)})
        self.assertTrue(tracer.recalling(self.tracer))
        self.run.step({self.sigma: (*self.sigma_aim, 0x800)})
        for _ in range(28):
            self.run.step({})
        self.assertTrue(tracer.recalling(self.tracer))
        self.assertEqual(self.vitals.total, 150)
