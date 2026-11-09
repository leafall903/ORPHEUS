"""104319 authored eye offsets reach player rays, descriptors and low cover."""

import unittest

from test_game_combat import Run

from ow174.game import combat
from ow174.game.collision import World, flat_world
from ow174.game.content import heroes
from ow174.game.script.driver import BASTION


class BastionEyeTests(unittest.TestCase):
    def arena(self, hero=BASTION, *, crouch=False, cover=None):
        run = Run(names=('Shooter', 'Target'))
        shooter, victim = run.players
        match = run.match
        match.switch_hero(shooter, heroes()[hero])
        match.switch_hero(victim, heroes()[BASTION])
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

    def test_authored_offsets_reach_actual_eye_and_target_descriptor(self):
        for hero, heights in ((BASTION, (1.82, 1.35)), (0x02E0000000000042, (1.425, 0.9975))):
            for crouched, expected in zip((False, True), heights, strict=True):
                with self.subTest(hero=hero, crouched=crouched):
                    run, shooter, _ = self.arena(hero, crouch=crouched)
                    self.assertEqual(shooter.mover.crouched, crouched)
                    fight = run.match.combat
                    self.assertAlmostEqual(fight._eye(shooter)[1] - shooter.mover.position[1], expected)
                    self.assertEqual(next(t for t in fight._targets() if t.owner is shooter).eye, expected)

    def test_bastion_horizontal_fire_passes_below_eye_cover_and_stops_above_eye(self):
        # Both targets use the actual tall Bastion capsule. Horizontal rays are
        # deliberate here; shorter heroes are aimed at explicitly elsewhere.
        for crouched, covers in ((False, (1.7, 1.9)), (True, (1.25, 1.4))):
            for cover, blocked in zip(covers, (False, True), strict=True):
                with self.subTest(crouched=crouched, cover=cover):
                    run, shooter, victim = self.arena(crouch=crouched, cover=cover)
                    fight = run.match.combat
                    target = next(t for t in fight._targets() if t.owner is victim)
                    before = fight.vitals_of(target).total
                    run.step({shooter: (0, 0, 1 | (0x80 if crouched else 0))})
                    self.assertEqual(fight.vitals_of(target).total < before, not blocked)
                    self.assertEqual(bool(shooter.client.messages(combat.HIT)), not blocked)

    def test_enemy_mccree_low_cover_stops_its_actual_standing_and_crouched_rays(self):
        for crouched, cover in ((False, 1.5), (True, 1.05)):
            for blocked in (False, True):
                with self.subTest(crouched=crouched, blocked=blocked):
                    run, shooter, victim = self.arena(0x02E0000000000042, crouch=crouched,
                                                       cover=cover if blocked else None)
                    fight = run.match.combat
                    target = next(t for t in fight._targets() if t.owner is victim)
                    before = fight.vitals_of(target).total
                    run.step({shooter: (0, 0, 1 | (0x80 if crouched else 0))})
                    self.assertEqual(fight.vitals_of(target).total < before, not blocked)
