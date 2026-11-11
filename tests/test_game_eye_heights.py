"""Static resource camera fields and actual low-cover shooting, without native muzzle claims."""

import unittest
from unittest.mock import patch

from test_game_combat import KANEZAKA_DM, Run, aim
from test_game_mover import box, wall

from ow174.game import bodytrace, movelog
from ow174.game.collision import World
from ow174.game.commands import CROUCH
from ow174.game.content import heroes


class PlayerEyeHeightTests(unittest.TestCase):
    def setUp(self):
        for module in (bodytrace, movelog):
            self.enterContext(patch.object(module, 'ENABLED', False))

    def arena(self, name, crouched, cover=None, target_name=None):
        run = Run(KANEZAKA_DM, ('Shooter', 'Target'))
        match = run.match
        match.bots.clear()
        match._pending_bot_scripts.clear()
        shooter, target = run.players
        # The local hero-name catalog calls body02A3 Cassidy; the104319 resource is McCree.
        hero = next(h for h in heroes().values()
                    if (h.body == 0x04000000000002A3 if name == 'McCree' else h.name == name))
        match.switch_hero(shooter, hero)
        if target_name is not None:
            other = next(h for h in heroes().values()
                         if (h.body == 0x04000000000002A3 if target_name == 'McCree'
                             else h.name == target_name))
            match.switch_hero(target, other)
        x, y, z = shooter.mover.position
        target.mover.position[:] = (x, y, z + 5)
        target.mover.body = None
        mask = CROUCH if crouched else 0
        for _ in range(10):
            run.step({shooter: (0, 0, mask)})
        if cover is not None:
            x, y, z = shooter.mover.position
            match.collision_world = World(
                box(x - 20, x + 20, y - 1, y, z - 20, z + 20)
                + wall(x - 10, z + 1, x + 10, z + 1, y0=y, y1=y + cover),
                name='authored player eye-height low-cover fixture',
            )
        return run, shooter, target, mask

    def test_real_standing_and_crouched_pose_agree_with_target_eye(self):
        for name, heights in [('Hanzo', (1.35, 0.945)), ('McCree', (1.425, 0.9975)),
                              ('Soldier: 76', (1.55, 1.085))]:
            for crouched, expected in zip((False, True), heights, strict=True):
                with self.subTest(hero=name, crouched=crouched):
                    run, shooter, _, _ = self.arena(name, crouched)
                    self.assertEqual(shooter.mover.crouched, crouched)
                    fight = run.match.combat
                    self.assertAlmostEqual(fight._eye(shooter)[1] - shooter.mover.position[1], expected)
                    target = next(t for t in fight._targets() if t.owner is shooter)
                    self.assertEqual(target.eye, expected)

    def test_hanzo_fixed_horizontal_arrow_obeys_standing_and_crouched_cover(self):
        for crouched, blocked, clear in [(False, 1.45, 1.2), (True, 1.05, 0.8)]:
            for cover, expected in [(blocked, 200), (clear, 75)]:
                with self.subTest(crouched=crouched, cover=cover):
                    run, shooter, target, mask = self.arena('Hanzo', crouched, cover)
                    for _ in range(70):
                        run.step({shooter: (0, 0, 1 | mask)})
                    run.step({shooter: (0, 0, mask)})
                    for _ in range(20):
                        run.step({shooter: (0, 0, mask)})
                    self.assertEqual(run.match.combat.vitals[target.body].total, expected)

    def test_mccree_fixed_horizontal_primary_obeys_both_pose_heights(self):
        for crouched, blocked, clear in [(False, 1.48, 1.3), (True, 1.07, 0.9)]:
            for cover, hit in [(blocked, False), (clear, True)]:
                with self.subTest(crouched=crouched, cover=cover):
                    run, shooter, target, mask = self.arena('McCree', crouched, cover)
                    run.step({shooter: (0, 0, 1 | mask)})
                    for _ in range(25):
                        run.step({shooter: (0, 0, mask)})
                    health = run.match.combat.vitals[target.body].total
                    self.assertEqual(health < 200, hit)

    def test_hanzo_and_enemy_mccree_aim_and_cover_work_bidirectionally(self):
        for name, target_name in [('Hanzo', 'McCree'), ('McCree', 'Hanzo')]:
            for crouched, blocked, clear in [(False, 1.475, 1.25), (True, 1.0475, 0.845)]:
                for cover, hit in [(blocked, False), (clear, True)]:
                    with self.subTest(shooter=name, crouched=crouched, cover=cover):
                        run, shooter, target, mask = self.arena(name, crouched, cover, target_name)
                        for _ in range(10):
                            run.step({shooter: (0, 0, mask), target: (0, 0, mask)})
                        fight = run.match.combat
                        yaw, pitch = aim(fight._eye(shooter), fight._eye(target))
                        for _ in range(70 if name == 'Hanzo' else 1):
                            run.step({shooter: (yaw, pitch, 1 | mask), target: (0, 0, mask)})
                        for _ in range(25):
                            run.step({shooter: (yaw, pitch, mask), target: (0, 0, mask)})
                        self.assertEqual(fight.vitals[target.body].total < 200, hit)
