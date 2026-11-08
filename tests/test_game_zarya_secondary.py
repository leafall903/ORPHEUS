"""Actual Zarya secondary projectile/splash behavior; geometry is reconstructed."""

import unittest
from unittest.mock import patch

from test_game_combat import Run

from ow174.game import combat
from ow174.game.collision import World
from ow174.game.content import heroes


class ZaryaSecondaryTests(unittest.TestCase):
    def setUp(self):
        self.run = Run(names=("Zarya", "Enemy", "Other"))
        self.a, self.b, self.c = self.run.players
        self.fight = self.run.match.combat
        self.run.match.bots.clear()
        self.run.match._pending_bot_scripts.clear()
        self.run.match.switch_hero(self.a, heroes()[0x02E0000000000068])
        for player, position in ((self.a, [0, 1, 0]), (self.b, [0, 1, 5]),
                                 (self.c, [10, 1, 5])):
            player.mover.position[:] = position
        self.c.team = self.b.team

    def health(self, player):
        target = next(t for t in self.fight._targets() if t.owner is player)
        return self.fight.vitals_of(target).total

    def fire(self, *, energy=0, yaw=0, pitch=0):
        self.a.body_script.component.entity_var(87).base = energy
        self.run.step({self.a: (yaw, pitch, 2)})

    def advance(self, frames=60):
        for _ in range(frames):
            self.run.step({self.a: (0, 0, 0)})

    def test_zero_energy_contact_deals_47_after_input_release(self):
        self.fire()
        self.advance()
        self.assertAlmostEqual(self.health(self.b), 153)
        self.assertFalse(self.fight.rockets)
        self.assertFalse(any(m["+0x78"]["+0x8"] & combat.CRITICAL
                             for m in self.a.client.messages(combat.HIT)))

    def test_50_energy_contact_deals_71(self):
        self.fire(energy=50)
        self.advance()
        self.assertAlmostEqual(self.health(self.b), 129)

    def test_100_energy_contact_deals_95(self):
        self.fire(energy=100)
        self.advance()
        self.assertAlmostEqual(self.health(self.b), 105, places=4)

    def test_energy_gained_during_flight_does_not_change_emitted_damage(self):
        self.fire()
        self.a.body_script.component.entity_var(87).base = 100
        self.advance()
        self.assertAlmostEqual(self.health(self.b), 153)

    def test_aimed_miss_has_no_damage_or_hit_feedback(self):
        self.fire(yaw=16384)
        self.advance()
        self.assertEqual(self.health(self.b), 200)
        self.assertEqual(self.health(self.c), 200)
        self.assertFalse(self.a.client.messages(combat.HIT))

    def test_wall_explosion_blocks_far_side_but_damages_near_side(self):
        self.b.mover.position[:] = [0, 1, 6]
        self.c.mover.position[:] = [1.2, 1, 4]
        wall = World([(-10, 0, 5, 10, 0, 5, 10, 10, 5),
                      (-10, 0, 5, 10, 10, 5, -10, 10, 5)])
        with patch.object(self.fight, "_collision", return_value=wall):
            self.fire()
            self.advance()
        self.assertEqual(self.health(self.b), 200)
        self.assertLess(self.health(self.c), 200)
        self.assertGreater(self.health(self.c), 153)

    def test_body_impact_splash_does_not_cross_a_wall(self):
        self.b.mover.position[:] = [0, 1, 4]
        self.c.mover.position[:] = [0, 1, 6]
        wall = World([(-10, 0, 5, 10, 0, 5, 10, 10, 5),
                      (-10, 0, 5, 10, 10, 5, -10, 10, 5)])
        with patch.object(self.fight, "_collision", return_value=wall):
            self.fire()
            self.advance()
        self.assertAlmostEqual(self.health(self.b), 153)
        self.assertEqual(self.health(self.c), 200)

    def test_nearby_enemy_takes_reduced_splash_without_a_contact_hit(self):
        self.c.mover.position[:] = [1.5, 1, 5]
        self.fire()
        self.advance()
        self.assertAlmostEqual(self.health(self.b), 153)
        self.assertGreater(self.health(self.c), 153)
        self.assertLess(self.health(self.c), 200)

    def test_enemy_outside_blast_radius_takes_no_splash(self):
        self.c.mover.position[:] = [3.5, 1, 5]
        self.fire()
        self.advance()
        self.assertAlmostEqual(self.health(self.b), 153)
        self.assertEqual(self.health(self.c), 200)

    def test_nearby_ally_takes_no_splash(self):
        self.c.mover.position[:] = [1.5, 1, 5]
        self.c.team = self.a.team
        self.fire()
        self.advance()
        self.assertAlmostEqual(self.health(self.b), 153)
        self.assertEqual(self.health(self.c), 200)


if __name__ == "__main__":
    unittest.main()
