"""Original live source scaling reaches the same physical Ice Wall pools once."""
import unittest
import test_game_ana_nano as nano
import test_mei_wall as walls
from test_game_combat import aim


class WallSourceDamageTests(unittest.TestCase):
    def hit(self, boosted):
        fixture = nano.AnaNanoTests()
        run, ana, shooter, mei = fixture.fight(ally_hero="Pharah", enemy_hero="Mei")
        fight = run.match.combat
        x, y, z = ana.mover.position
        for actor, position in ((mei, (x, y, z)), (shooter, (x, y, z + 8)),
                                (ana, (x + 4, y, z + 8))):
            actor.mover.position[:] = position
            actor.mover.body = None
        for _ in range(30):
            run.step({})
        pieces, _ = walls.WallTests().place(run, mei, distance=3)
        self.assertEqual(len(pieces), 5)
        fixture.cast(run, ana, shooter, charged=boosted)
        self.assertEqual(fight.now < fight.vitals[shooter.body].nano_until, boosted)
        center = pieces[2]
        angles = aim(fight._eye(shooter), (center.feet[0], center.feet[1] + 1, center.feet[2]))
        run.step({shooter: (*angles, 1)})
        for _ in range(60):
            run.step({})
            if center.health < 400:
                break
        self.assertEqual(tuple(piece.health for piece in pieces),
                         tuple(fight.vitals[piece.entity].total for piece in pieces))
        return tuple(400 - piece.health for piece in pieces)

    def test_actual_nano_scales_direct_and_adjacent_wall_splash_once(self):
        plain = self.hit(False)
        boosted = self.hit(True)
        self.assertEqual(plain[2], 120)
        self.assertEqual(boosted[2], 180)
        for first, second in zip(plain, boosted, strict=True):
            self.assertGreater(first, 0)
            self.assertAlmostEqual(second, first * 1.5)


    def test_actual_zarya_launch_nano_reaches_wall_after_live_nano_expires(self):
        from ow174.game import combat, nano as nano_state
        fixture = nano.AnaNanoTests()
        run, ana, shooter, mei = fixture.fight(ally_hero="Zarya", enemy_hero="Mei")
        fight = run.match.combat
        x, y, z = ana.mover.position
        for actor, position in ((mei, (x, y, z)), (shooter, (x, y, z + 8)),
                                (ana, (x + 4, y, z + 8))):
            actor.mover.position[:] = position
            actor.mover.body = None
        for _ in range(30):
            run.step({})
        fixture.cast(run, ana, shooter)
        run.frame = round(fight.vitals[shooter.body].nano_until / combat.TICK) - 12
        run.match.update(run.frame * combat.TICK, run.frame)
        pieces, _ = walls.WallTests().place(run, mei, distance=3)
        self.assertEqual(len(pieces), 5)
        center = pieces[2]
        angles = aim(fight._eye(shooter), (center.feet[0], center.feet[1] + 1, center.feet[2]))
        run.step({shooter: (*angles, 2)})
        rocket, = fight.rockets
        self.assertEqual(rocket.offense_scale, 1.5)
        self.assertAlmostEqual(rocket.blast.amount, 70.5)
        for _ in range(60):
            run.step({})
            if center.health < 400:
                break
        self.assertEqual(nano_state.offense(fight, shooter.body), 1)
        self.assertAlmostEqual(center.health, 329.5)
        self.assertEqual(center.health, fight.vitals[center.entity].total)
