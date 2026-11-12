"""FirstPerson-derived ray origins and target records; animated muzzle remains open."""

import unittest

import test_game_brigitte as brigitte_tests
from test_game_mover import box

from ow174.game.brigitte import HERO
from ow174.game.collision import StaticCollisionProvider, World
from ow174.game.commands import CROUCH, FIRE
from ow174.game.content import heroes

MCCREE = 0x02E0000000000042


class BrigitteEyeTests(unittest.TestCase):
    def scene(self, *, mccree=False, crouched=False, cover=None):
        run, brigitte = brigitte_tests.BrigitteTests().fixture()
        other = run.players[1]
        run.match.switch_hero(other, heroes()[MCCREE] if mccree else other.hero)
        source, target = (other, brigitte) if mccree else (brigitte, other)
        x, y, z = source.mover.position
        source.mover.position, source.mover.body = [x, y, z], None
        target.mover.position, target.mover.body = [x, y, z + 10], None
        triangles = box(x - 30, x + 30, y - 1, y, z - 30, z + 30)
        if cover is not None:
            triangles += box(x - 4, x + 4, y, y + cover, z + 4, z + 4.2)
        collision = World(triangles)
        run.match.collision_provider = StaticCollisionProvider(collision)
        run.match.collision_world = collision
        for player in run.players:
            player.mover.use(world=collision)
        buttons = CROUCH if crouched else 0
        for _ in range(20):
            run.step({source: (0, 0, buttons)})
        self.assertEqual(source.mover.crouched, crouched)
        return run, source, target, buttons

    def health(self, run, player):
        fight = run.match.combat
        return fight.vitals_of(next(t for t in fight._targets() if t.owner is player)).total

    def test_brigitte_whip_clears_cover_between_old_and_authored_height(self):
        for crouched, cover in ((False, 1.565), (True, 1.175)):
            with self.subTest(crouched=crouched):
                run, source, target, buttons = self.scene(crouched=crouched, cover=cover)
                run.step({source: (0, 0, buttons | 8)})
                for _ in range(60):
                    run.step({source: (0, 0, buttons)})
                self.assertEqual(self.health(run, target), 130)

    def test_enemy_mccree_origin_is_occluded_by_low_cover_in_both_stances(self):
        for crouched, cover in ((False, 1.48), (True, 1.05)):
            for blocked in (False, True):
                with self.subTest(crouched=crouched, blocked=blocked):
                    run, source, target, buttons = self.scene(
                        mccree=True, crouched=crouched, cover=cover if blocked else None,
                    )
                    run.step({source: (0, 0, buttons | FIRE)})
                    for _ in range(20):
                        run.step({source: (0, 0, buttons)})
                    if blocked:
                        self.assertEqual(self.health(run, target), 200)
                    else:
                        self.assertLess(self.health(run, target), 200)

    def test_own_and_enemy_target_and_rewind_records_use_firstperson_fields(self):
        for mccree, heights in ((False, (1.58, 1.2)), (True, (1.425, 0.9975))):
            for crouched, expected in enumerate(heights):
                with self.subTest(mccree=mccree, crouched=crouched):
                    run, source, _target, _buttons = self.scene(mccree=mccree, crouched=bool(crouched))
                    fight = run.match.combat
                    target = next(t for t in fight._targets() if t.owner is source)
                    self.assertAlmostEqual(fight._eye(source)[1] - source.mover.position[1], expected)
                    self.assertEqual(target.eye, expected)
                    self.assertEqual(fight.seen(target, run.frame).eye, expected)
                    self.assertEqual(source.hero.guid, MCCREE if mccree else HERO)


if __name__ == "__main__":
    unittest.main()
