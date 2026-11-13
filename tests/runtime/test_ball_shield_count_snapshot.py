"""Original node14 keeps live count consistent with its existing shield snapshot."""

import unittest

import test_delivered_ball_mine_factory as factory
from test_delivered_wrecking_ball_fireball import relocate

from ow174.game import ball
from ow174.game.content import heroes


class ShieldCountSnapshotTests(unittest.TestCase):
    def test_pre_snapshot_count_excludes_mines_and_keeps_real_enemy(self):
        for own_mine, real_enemy, count in ((True, False, 0), (False, False, 0), (False, True, 1)):
            with self.subTest(own_mine=own_mine, real_enemy=real_enemy):
                run, caster, viewer, mine = factory.MineTests().flying()
                run.match.switch_hero(viewer, heroes()[ball.HERO])
                for _ in range(40):
                    run.step({})
                focus, other = (caster, viewer) if own_mine else (viewer, caster)
                x, y, z = mine.position
                relocate(run, focus, (x, y, z + 1))
                relocate(run, other, (x + (2 if real_enemy else 100), y, z + 1))
                fight = run.match.combat
                self.assertFalse(mine.armed)
                self.assertEqual(ball.shield_enemy_count(fight, focus, 8), count)
                run.step({focus: (0, 0, 16)})
                shield = ball.instance(focus, 0x1566)
                self.assertEqual(shield.vars[11398].value(), count)
                self.assertEqual(shield.vars[11].value(), count)
                self.assertEqual(fight.vitals[focus.body].total, 700 + 75 * count)
                self.assertIn(mine.entity, fight.mines.entities)
                self.assertFalse(mine.triggered)
