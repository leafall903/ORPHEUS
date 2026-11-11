"""Sigma's actual Accretion input against Pharah."""

import unittest
from dataclasses import replace
from unittest.mock import patch

import test_pharah_defenses as defense
from test_game_combat import aim

from ow174.game.collision import World
from ow174.game.physics_snapshot import snapshot_mover


class PharahAccretionTests(unittest.TestCase):
    health = defense.PharahDefenseTests.health

    def arena(self):
        run, pharah, sigma, third = defense.PharahDefenseTests().encounter("Sigma")
        sigma.mover.position[2] = 6
        third.mover.position[0] = 30
        for player in (sigma, third):
            player.mover.body = None
        run.step({})
        return run, pharah, sigma

    def cast(self, run, pharah, sigma):
        fight = run.match.combat
        yaw, pitch = aim(fight._eye(sigma), fight._eye(pharah))
        run.step({sigma: (yaw, pitch, 16), pharah: (-32768, 0, 0)})
        for _ in range(100):
            if self.health(run, pharah).total < 200:
                return
            run.step({sigma: (yaw, pitch, 0), pharah: (-32768, 0, 0)})
        self.fail("Accretion did not hit Pharah")

    def test_direct_hit_interrupts_barrage_and_jump_jet_recovers_at_expiry(self):
        run, pharah, sigma = self.arena()
        fight = run.match.combat
        script = pharah.body_script
        script.set_ult_charge(script.component.entity_var(338).value())
        run.step({pharah: (-32768, 0, 32)})
        self.assertTrue(script.ultimate_running())
        self.cast(run, pharah, sigma)
        self.assertEqual(self.health(run, pharah).total, 130)
        self.assertEqual(sigma.body_script.component.entity_var(342).value(), 70)
        self.assertEqual(fight.stuns.get(pharah.body, 0) - fight.tick, 50)
        self.assertFalse(script.ultimate_running())
        self.assertLess(pharah.mover.velocity[2], -4.5)
        self.assertGreater(pharah.mover.velocity[1], 0)
        self.assertTrue(pharah.mover.external_impulse)
        captured = snapshot_mover(pharah.mover)
        with patch.object(pharah.mover, "external_impulse", False):
            self.assertNotEqual(snapshot_mover(pharah.mover), captured)
        volley = next(i for i in script.component.instances.values() if i.graph.index == 0x2B).states[1]
        reported = volley.reported
        deadline = fight.stuns[pharah.body]
        z = pharah.mover.position[2]
        for _ in range(5):
            run.step({pharah: (-32768, 0, 32 | 8)})
        self.assertLess(pharah.mover.position[2], z - 0.2)
        self.assertLess(pharah.mover.velocity[1], 10)
        while run.frame < deadline - 1:
            run.step({pharah: (-32768, 0, 32 | 8)})
        self.assertTrue(fight.is_stunned(pharah))
        run.step({pharah: (-32768, 0, 8)})
        self.assertFalse(fight.is_stunned(pharah))
        self.assertEqual(pharah.mover.velocity[1], 20)
        self.assertEqual(volley.reported, reported)
        self.assertFalse(script.ultimate_running())


    def test_wall_splash_pushes_without_stun_and_cover_blocks_the_effect(self):
        for cover in (False, True):
            with self.subTest(cover=cover):
                run, pharah, sigma = self.arena()
                fight = run.match.combat
                pharah.mover.position[0] = 1.25
                pharah.mover.body = None
                y, z = pharah.mover.position[1], 0.8 if cover else -0.8
                wall = World([(-4, y, z, -4, y + 5, z, 4, y + 5, z),
                              (-4, y, z, 4, y + 5, z, 4, y, z)])
                start = tuple(pharah.mover.position)
                with patch.object(fight, "_collision", return_value=wall):
                    run.step({sigma: (-32768, 0, 16)})
                    for _ in range(70):
                        run.step({sigma: (-32768, 0, 0)})
                self.assertNotIn(pharah.body, fight.stuns)
                if cover:
                    self.assertEqual(self.health(run, pharah).total, 200)
                    self.assertEqual(tuple(pharah.mover.position), start)
                else:
                    self.assertEqual(self.health(run, pharah).total, 160)
                    self.assertGreater(pharah.mover.position[0], start[0])
                    self.assertGreater(pharah.mover.position[2], start[2])

    def test_allied_pharah_is_ignored_and_rock_uses_authored_gravity(self):
        run, pharah, sigma = self.arena()
        run.match.game_map = replace(run.match.game_map, free_for_all=False)
        pharah.team = sigma.team = 0
        fight = run.match.combat
        yaw, pitch = aim(fight._eye(sigma), fight._eye(pharah))
        run.step({sigma: (yaw, pitch, 16)})
        for _ in range(55):
            run.step({sigma: (yaw, pitch, 0)})
            if fight.rockets:
                break
        rock, = fight.rockets
        self.assertAlmostEqual(rock.speed, 37, places=4)
        self.assertEqual(rock.gravity, 18)
        velocity_y = rock.velocity[1]
        self.assertGreater(velocity_y, 0)
        run.step({sigma: (yaw, pitch, 0)})
        self.assertAlmostEqual(rock.velocity[1], velocity_y - 18 * 0.016, places=6)
        for _ in range(20):
            run.step({sigma: (yaw, pitch, 0)})
        self.assertLess(rock.position[2], pharah.mover.position[2] - 1)
        self.assertEqual(self.health(run, pharah).total, 200)
        self.assertEqual(sigma.body_script.component.entity_var(342).value(), 0)
        self.assertNotIn(pharah.body, fight.stuns)
