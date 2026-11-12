"""104319 authored eye offsets in real Moira/McCree commands and target records.

Offsets come from body1C46/02A3 FirstPerson XML; native muzzle animation and
skeletal hit regions are separate acceptance, not implied by these server tests.
"""

import unittest

from test_game_combat import Run

from ow174.game import combat
from ow174.game.collision import World, flat_world
from ow174.game.content import heroes

MOIRA, MCCREE = 0x02E00000000001A2, 0x02E0000000000042


class MoiraMcCreeEyeTests(unittest.TestCase):
    def arena(self, *, crouch=False, cover=None):
        run = Run(names=('McCree', 'Moira'))
        shooter, victim = run.players
        match = run.match
        self.assertTrue(match.switch_hero(shooter, heroes()[MCCREE]))
        self.assertTrue(match.switch_hero(victim, heroes()[MOIRA]))
        match.bots.clear()
        match._pending_bot_scripts.clear()
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
            run.step(dict.fromkeys(run.players, (0, 0, 0x80 if crouch else 0)))
        return run, shooter, victim

    def test_both_authored_stances_reach_emission_and_target_descriptors(self):
        for crouched in (False, True):
            with self.subTest(crouched=crouched):
                run, mccree, moira = self.arena(crouch=crouched)
                fight = run.match.combat
                expected = (0.9975, 1.1) if crouched else (1.425, 1.7)
                for player, offset in zip((mccree, moira), expected, strict=True):
                    self.assertEqual(player.mover.crouched, crouched)
                    self.assertAlmostEqual(fight._eye(player)[1] - player.mover.position[1], offset)
                    target = next(t for t in fight._targets() if t.owner is player)
                    self.assertEqual(target.eye, offset)

    def test_actual_mccree_shot_at_moira_obeys_authored_low_cover_in_both_stances(self):
        for crouched, cover in ((False, 1.5), (True, 1.05)):
            for blocked in (False, True):
                with self.subTest(crouched=crouched, blocked=blocked):
                    run, shooter, victim = self.arena(crouch=crouched, cover=cover if blocked else None)
                    fight = run.match.combat
                    target = next(t for t in fight._targets() if t.owner is victim)
                    health = fight.vitals_of(target)
                    before = health.total
                    run.step({shooter: (0, 0, 1 | (0x80 if crouched else 0)),
                              victim: (0, 0, 0x80 if crouched else 0)})
                    self.assertEqual(health.total < before, not blocked)
                    self.assertEqual(bool(shooter.client.messages(combat.HIT)), not blocked)
