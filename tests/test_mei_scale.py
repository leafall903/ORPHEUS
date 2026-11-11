"""Real Mei weapon interactions across authored body-scale configurations."""

import os
import unittest
from unittest.mock import patch

import test_mei_combat as fixtures
import test_mei_deflect as deflect
import test_mei_matrix as matrix
from test_game_combat import aim

from ow174.game import combat, dva_matrix, genji_deflect
from ow174.game.commands import SECONDARY
from ow174.game.script import expr
from ow174.game.script.driver import BODY_SCALE_OVERRIDE


class MeiScaleTests(unittest.TestCase):
    def test_scaled_genji_deflects_a_real_icicle_past_the_unit_width(self):
        for scale in (1., 2.):
            with self.subTest(scale=scale):
                fixture = deflect.DeflectTests()
                run, mei, genji, _ = fixture.scene()
                fight = run.match.combat
                x, y, z = mei.mover.position
                fight.walls.place_player(genji, (x + 1., y, z + 5.))
                genji.body_script.component.set(BODY_SCALE_OVERRIDE, scale)
                for _ in range(3):
                    run.step({genji: (-32768, 0, 0)})
                self.assertAlmostEqual(genji.mover.body_scale, scale)
                state = genji_deflect.active(fight, genji)
                box = state.node.field('m_boxes')[0]
                corner = state.evaluate(box['m_AEF67365'], watch=False)
                self.assertEqual(corner, expr.Vec3(.75 * scale, .75 * scale, scale))
                angle = aim(fight._eye(mei), (x, y + 1.4, z + 5.))
                for frame in range(45):
                    run.step({mei: (*angle, SECONDARY if frame < 20 else 0), genji: (-32768, 0, 0)})
                self.assertEqual(len(genji.client.messages(combat.HIT)), int(scale == 2.))
                self.assertEqual(fixture.health(run, mei), 250. if scale == 1. else 100.)

    def test_scaled_mei_melee_reaches_the_target_and_resets_to_unit_scale(self):
        for scale, expected in ((1., 200.), (2., 170.)):
            with self.subTest(scale=scale):
                fixture = fixtures.MeiCombatTests()
                run, mei, enemy, _ = fixture.duel()
                fight = run.match.combat
                x, y, z = mei.mover.position
                fight.walls.place_player(enemy, (x, y, z + 3.6))
                component = mei.body_script.component
                component.set(BODY_SCALE_OVERRIDE, scale)
                for _ in range(12):
                    run.step({})
                target = next(t for t in fight._targets() if t.owner is enemy)
                self.assertAlmostEqual(mei.mover.body_scale, scale)
                self.assertAlmostEqual(mei.mover.values.stand_radius, mei.mover.data.stand_radius * scale)
                fixture.fire(run, mei, target, 0x800, .9, frames=32)
                self.assertEqual(fight.vitals_of(target).total, expected)
                component.set(BODY_SCALE_OVERRIDE, 1.)
                for _ in range(3):
                    run.step({})
                self.assertEqual(mei.mover.body_scale, 1.)

    @unittest.skipUnless(os.environ.get('ORPHEUS_DVA_MECH_DATA'), 'explicit 104319 mech supplement required')
    def test_scaled_matrix_absorbs_a_real_icicle_past_the_unit_width(self):
        fixture = matrix.MatrixTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        stock = None
        for scale, offset in ((1., 0.), (1., 2.), (2., 2.), (2., 3.)):
            with self.subTest(scale=scale, offset=offset):
                run, mei, dva, instance, _ = fixture.scene()
                fight = run.match.combat
                x, y, z = mei.mover.position
                fight.walls.place_player(dva, (x + offset, y, z + 5.))
                component = dva.body_script.component
                # The fixture supplies scale in place of the native controller owner.
                startup = next(i for i in component.instances.values() if i.graph.index == 0x276)
                startup.node_state(startup.graph.node(130)).end(False)
                component.set(BODY_SCALE_OVERRIDE, scale)
                for _ in range(12):
                    run.step({dva: (-32768, 0, 0)})
                self.assertAlmostEqual(dva.mover.body_scale, scale)
                run.step({dva: (-32768, 0, SECONDARY)})
                state = instance.state(3)
                self.assertTrue(state.active)
                corners = tuple(tuple(state.evaluate(box[key], watch=False)
                                      for key in ('m_CBA118B2', 'm_AEF67365'))
                                for box in state.node.field('m_boxes'))
                if stock is None:
                    stock = corners
                for actual_box, base_box in zip(corners, stock, strict=True):
                    for actual, base in zip(actual_box, base_box, strict=True):
                        self.assertEqual(actual, expr.Vec3(base.x * scale, base.y * scale, base.z * scale))
                angle = aim(fight._eye(mei), (x, fight._eye(dva)[1], z + 5.))
                original = dva_matrix.cast
                caught = []

                def record(*args, original=original, caught=caught):
                    result = original(*args)
                    if result is not None:
                        caught.append(result)
                    return result

                with patch.object(dva_matrix, 'cast', side_effect=record):
                    for frame in range(45):
                        run.step({mei: (*angle, SECONDARY if frame < 20 else 0),
                                  dva: (-32768, 0, SECONDARY)})
                self.assertEqual(len(caught), int(offset < 1.25 * scale))


if __name__ == '__main__':
    unittest.main(argv=[__file__], verbosity=2)

