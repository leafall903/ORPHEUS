"""104319 FirstPerson offsets drive actual standing/crouched cover shots."""

import unittest

import test_mei_combat as fixtures

from ow174.game import combat, mei
from ow174.game.collision import World
from ow174.game.commands import CROUCH
from ow174.game.content import heroes

MCCREE = 0x02E0000000000042


class MeiEyeTests(unittest.TestCase):
    def scene(self, hero, crouched, height):
        run, owner, enemy, _target = fixtures.MeiCombatTests().duel()
        if hero == MCCREE:
            self.assertTrue(run.match.switch_hero(owner, heroes()[hero]))
            self.assertTrue(run.match.switch_hero(enemy, heroes()[mei.HERO]))
        x, y, z = owner.mover.position
        run.match.combat.walls.place_player(enemy, (x, y, z + 5.))
        top, at = y + height, z + 2.5
        triangles = [
            (x - 200., y, z - 200., x - 200., y, z + 200., x + 200., y, z + 200.),
            (x - 200., y, z - 200., x + 200., y, z + 200., x + 200., y, z - 200.),
            (x - 5., y, at, x + 5., y, at, x + 5., top, at),
            (x - 5., y, at, x + 5., top, at, x - 5., top, at),
            (x - 5., y, at, x + 5., top, at, x + 5., y, at),
            (x - 5., y, at, x - 5., top, at, x + 5., top, at),
        ]
        scene = World(triangles, name='Mei/McCree resource eye low cover')
        run.match.collision_world = scene
        for player in run.players:
            player.mover.use(world=scene)
        stance = CROUCH if crouched else 0
        for _ in range(30):
            run.step({owner: (0, 0, stance)})
        self.assertEqual(owner.mover.crouched, crouched)
        target = next(t for t in run.match.combat._targets() if t.owner is enemy)
        return run, owner, target, stance

    def test_stance_offsets_are_shared_by_shooter_target_and_history(self):
        for hero, standing, crouching in ((mei.HERO, 1.3, .91), (MCCREE, 1.425, .9975)):
            for crouched in (False, True):
                with self.subTest(hero=hero, crouched=crouched):
                    run, owner, _target, stance = self.scene(hero, crouched, .5)
                    expected = crouching if crouched else standing
                    eye = run.match.combat._eye(owner)
                    self.assertAlmostEqual(eye[1] - owner.mover.position[1], expected)
                    target = next(t for t in run.match.combat._targets() if t.owner is owner)
                    self.assertEqual(target.eye, expected)
                    run.step({owner: (0, 0, stance)})
                    self.assertEqual(run.match.combat.history[owner.body][-1][3], expected)

    def test_actual_standing_and_crouched_shots_respect_resource_eye_cover(self):
        for hero, standing, crouching, button in ((mei.HERO, 1.3, .91, 2),
                                                  (MCCREE, 1.425, .9975, 1)):
            for crouched in (False, True):
                for blocked in (False, True):
                    with self.subTest(hero=hero, crouched=crouched, blocked=blocked):
                        eye = crouching if crouched else standing
                        height = eye + .05 if blocked else eye - .05
                        run, owner, target, stance = self.scene(hero, crouched, height)
                        before = run.match.combat.vitals_of(target).total
                        for frame in range(50):
                            run.step({owner: (0, 0, stance | (button if frame < 20 else 0))})
                        after = run.match.combat.vitals_of(target).total
                        if blocked:
                            self.assertEqual(after, before)
                            self.assertFalse(owner.client.messages(combat.HIT))
                        else:
                            self.assertLess(after, before)
                            self.assertTrue(owner.client.messages(combat.HIT))
