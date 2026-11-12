"""Original FirstPerson offsets and actual low-cover commands in the Ana lane."""
import unittest

from test_game_ana import TEAM_MAP
from test_game_combat import Run

from ow174.game import combat
from ow174.game.collision import World, flat_world
from ow174.game.content import find_hero


class AnaEyeTests(unittest.TestCase):
    def arena(self, hero, *, crouch=False, cover=None):
        run = Run(TEAM_MAP, ('Shooter', 'Target'))
        shooter, victim = run.players
        match = run.match
        self.assertTrue(match.switch_hero(shooter, find_hero(hero)))
        match.bots.clear()
        match._pending_bot_scripts.clear()
        y = shooter.mover.position[1]
        shooter.mover.position = [0, y, 0]
        victim.mover.position = [0, y, 6]
        collision = flat_world(y)
        if cover is not None:
            h = y + cover
            collision = World([(-30, y, -30, -30, y, 30, 30, y, 30),
                               (-30, y, -30, 30, y, 30, 30, y, -30),
                               (-3, y, 2, 3, y, 2, 3, h, 2),
                               (-3, y, 2, 3, h, 2, -3, h, 2)])
        match.collision_world = collision
        for player in run.players:
            player.mover.use(world=collision)
        for _ in range(40):
            run.step({shooter: (0, 0, 0x80 if crouch else 0)})
        return run, shooter, victim

    def test_audited_standing_and_crouched_offsets_match_both_combat_paths(self):
        for hero, standing, crouched in (('Ana', 1.25, .875), ('Zarya', 1.6, 1.12),
                                         ('Genji', 1.2, .84), ('Cassidy', 1.425, .9975),
                                         ('Soldier', 1.55, 1.085)):
            for stance, expected in ((False, standing), (True, crouched)):
                with self.subTest(hero=hero, crouched=stance):
                    run, shooter, _ = self.arena(hero, crouch=stance)
                    self.assertEqual(shooter.mover.crouched, stance)
                    fight = run.match.combat
                    self.assertAlmostEqual(fight._eye(shooter)[1] - shooter.mover.position[1], expected)
                    entry = next(t for t in fight._targets() if t.owner is shooter)
                    self.assertEqual(entry.eye, expected)

    def test_both_ana_rifle_modes_obey_authored_eye_cover_in_both_stances(self):
        for crouched, height in ((False, 1.25), (True, .875)):
            for scoped in (False, True):
                for blocked in (False, True):
                    with self.subTest(crouched=crouched, scoped=scoped, blocked=blocked):
                        run, shooter, victim = self.arena('Ana', crouch=crouched,
                                                         cover=height + (.02 if blocked else -.02))
                        held = 0x80 if crouched else 0
                        if scoped:
                            for _ in range(35):
                                run.step({shooter: (0, 0, held | 2)})
                        run.step({shooter: (0, 0, held | 1 | (2 if scoped else 0))})
                        for _ in range(80):
                            run.step({shooter: (0, 0, held | (2 if scoped else 0))})
                        entry = next(t for t in run.match.combat._targets() if t.owner is victim)
                        self.assertEqual(run.match.combat.vitals_of(entry).total, 200 if blocked else 130)

    def test_zarya_beam_and_enemy_mccree_use_corrected_low_cover_origins(self):
        for hero, standing, crouched in (('Zarya', 1.6, 1.12), ('Cassidy', 1.425, .9975)):
            for stance, height in ((False, standing), (True, crouched)):
                for blocked in (False, True):
                    with self.subTest(hero=hero, crouched=stance, blocked=blocked):
                        run, shooter, victim = self.arena(hero, crouch=stance,
                                                         cover=height + (.02 if blocked else -.02))
                        for _ in range(10):
                            run.step({shooter: (0, 0, 1 | (0x80 if stance else 0))})
                        entry = next(t for t in run.match.combat._targets() if t.owner is victim)
                        self.assertEqual(run.match.combat.vitals_of(entry).total < 200, not blocked)

    def test_unaudited_body_fallback_stays_explicit(self):
        self.assertEqual(combat.eye_height(0), 1.55)
        self.assertEqual(combat.eye_height(0, True), 1.15)
