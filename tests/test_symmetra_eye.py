"""104319 authored first-person offsets on Symmetra and opposing McCree inputs."""

import unittest

from test_game_combat import Run

from ow174.game import combat, symmetra
from ow174.game.collision import World, flat_world
from ow174.game.commands import CROUCH
from ow174.game.content import heroes


class SymmetraEyeTests(unittest.TestCase):
    def arena(self, *, crouch=False, cover=None, incoming=False):
        run = Run(names=('Symmetra', 'McCree'))
        owner, other = run.players
        self.assertTrue(run.match.switch_hero(owner, heroes()[symmetra.HERO]))
        self.assertTrue(run.match.switch_hero(other, heroes()[0x02E0000000000042]))
        shooter, victim = (other, owner) if incoming else (owner, other)
        run.match.bots.clear()
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
        run.match.collision_world = world
        for player in run.players:
            player.mover.use(world=world)
        for _ in range(40):
            run.step({shooter: (0, 0, CROUCH if crouch else 0)})
        return run, shooter, victim

    def test_authored_offsets_reach_shot_origins_and_both_player_target_descriptors(self):
        for incoming, offsets in ((False, (1.3, .91)), (True, (1.425, .9975))):
            for crouched, height in zip((False, True), offsets, strict=True):
                with self.subTest(incoming=incoming, crouched=crouched):
                    run, shooter, victim = self.arena(incoming=incoming, crouch=crouched)
                    fight = run.match.combat
                    self.assertEqual(shooter.mover.crouched, crouched)
                    self.assertAlmostEqual(fight._eye(shooter)[1] - shooter.mover.position[1], height)
                    targets = {t.owner: t for t in fight._targets()}
                    self.assertEqual(targets[shooter].eye, height)
                    self.assertEqual(targets[victim].eye, 1.3 if incoming else 1.425)

    def cover_cases(self, incoming, heights):
        for crouched, height in zip((False, True), heights, strict=True):
            for blocked in (False, True):
                with self.subTest(incoming=incoming, crouched=crouched, blocked=blocked):
                    run, shooter, victim = self.arena(incoming=incoming, crouch=crouched,
                                                     cover=height if blocked else None)
                    fight = run.match.combat
                    target = next(t for t in fight._targets() if t.owner is victim)
                    health = fight.vitals_of(target)
                    before = health.total
                    run.step({shooter: (0, 0, 1 | (CROUCH if crouched else 0))})
                    self.assertEqual(health.total < before, not blocked)
                    self.assertEqual(bool(shooter.client.messages(combat.HIT)), not blocked)

    def test_symmetra_beam_is_blocked_by_low_cover_in_both_stances(self):
        self.cover_cases(False, (1.4, 1.0))

    def test_opposing_mccree_shot_obeys_his_authored_eye_and_symmetra_cover(self):
        self.cover_cases(True, (1.5, 1.05))


if __name__ == '__main__':
    unittest.main()
