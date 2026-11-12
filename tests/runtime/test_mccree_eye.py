"""McCree104319 first-person offsets affect actual shots and target descriptors."""

import unittest

from ow174.game import combat
from ow174.game.collision import World, flat_world
from ow174.game.content import heroes
from tests.runtime.test_game_combat import Run


class McCreeEyeTests(unittest.TestCase):
    def arena(self, *, crouch=False, cover=None):
        run = Run(names=('McCree', 'Target'))
        shooter, victim = run.players
        match = run.match
        self.assertTrue(match.switch_hero(shooter, heroes()[0x02E0000000000042]))
        match.bots.clear()
        y = shooter.mover.position[1]
        shooter.mover.position[:] = (0, y, 0)
        victim.mover.position[:] = (0, y, 10)
        world = flat_world(y)
        if cover is not None:
            height = y + cover
            world = World([(-50, y, -50, -50, y, 50, 50, y, 50),
                           (-50, y, -50, 50, y, 50, 50, y, -50),
                           (-5, y, 2, 5, y, 2, 5, height, 2),
                           (-5, y, 2, 5, height, 2, -5, height, 2),
                           (-5, y, 2, 5, height, 2, 5, y, 2),
                           (-5, y, 2, -5, height, 2, 5, height, 2)])
        match.collision_world = world
        for player in run.players:
            player.mover.use(world=world)
        for _ in range(40):
            run.step({shooter: (0, 0, 0x80 if crouch else 0)})
        return run, shooter, victim

    def test_authored_standing_and_crouched_offsets_reach_shots_and_target_descriptors(self):
        for crouched, expected in ((False, 1.425), (True, 0.9975)):
            with self.subTest(crouched=crouched):
                run, shooter, _ = self.arena(crouch=crouched)
                self.assertEqual(shooter.mover.crouched, crouched)
                eye = run.match.combat._eye(shooter)[1]
                self.assertAlmostEqual(eye - shooter.mover.position[1], expected)
                target = next(t for t in run.match.combat._targets() if t.owner is shooter)
                self.assertEqual(target.eye, expected)

    def test_low_cover_blocks_the_authored_eye_ray_in_both_stances(self):
        for crouched, cover in ((False, 1.5), (True, 1.05)):
            for blocked in (False, True):
                with self.subTest(crouched=crouched, blocked=blocked):
                    run, shooter, victim = self.arena(crouch=crouched, cover=cover if blocked else None)
                    fight = run.match.combat
                    target = next(t for t in fight._targets() if t.owner is victim)
                    health = fight.vitals_of(target)
                    before = health.total
                    run.step({shooter: (0, 0, 1 | (0x80 if crouched else 0))})
                    self.assertEqual(health.total < before, not blocked)
                    self.assertEqual(bool(shooter.client.messages(combat.HIT)), not blocked)
