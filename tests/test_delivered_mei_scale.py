"""Original00DD scale consumers on current single character/defense hosts."""
import unittest
from unittest.mock import patch
import test_mei_combat as fixtures
import test_mei_deflect as deflect
from test_game_combat import aim
from ow174.game import combat,defenses
from ow174.game.commands import SECONDARY
from ow174.game.script import expr
from ow174.game.script.driver import BODY_SCALE_OVERRIDE

class MeiScaleTests(unittest.TestCase):

    def test_scaled_genji_deflects_a_real_icicle_past_the_unit_width(self):
        for scale in (1.0, 2.0):
            with self.subTest(scale=scale):
                fixture = deflect.DeflectTests()
                run, mei, genji, _ = fixture.scene()
                fight = run.match.combat
                x, y, z = mei.mover.position
                fight.walls.place_player(genji, (x + 1.0, y, z + 5.0))
                genji.body_script.component.set(BODY_SCALE_OVERRIDE, scale)
                for _ in range(3):
                    run.step({genji: (-32768, 0, 0)})
                self.assertAlmostEqual(genji.mover.body_scale, scale)
                state = defenses.deflect_state(genji)
                box = state.node.field('m_boxes')[0]
                corner = state.evaluate(box['m_AEF67365'], watch=False)
                self.assertEqual(corner, expr.Vec3(0.75 * scale, 0.75 * scale, scale))
                angle = aim(fight._eye(mei), (x, y + 1.4, z + 5.0))
                for frame in range(45):
                    run.step({mei: (*angle, SECONDARY if frame < 20 else 0), genji: (-32768, 0, 0)})
                self.assertEqual(len(genji.client.messages(combat.HIT)), int(scale == 2.0))
                self.assertEqual(fixture.health(run, mei), 250.0 if scale == 1.0 else 100.0)

    def test_scaled_mei_melee_reaches_the_target_and_resets_to_unit_scale(self):
        for scale, expected in ((1.0, 200.0), (2.0, 170.0)):
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
                target = next((t for t in fight._targets() if t.owner is enemy))
                self.assertAlmostEqual(mei.mover.body_scale, scale)
                self.assertAlmostEqual(mei.mover.values.stand_radius, mei.mover.data.stand_radius * scale)
                fixture.fire(run, mei, target, 2048, 0.9, frames=32)
                self.assertEqual(fight.vitals_of(target).total, expected)
                component.set(BODY_SCALE_OVERRIDE, 1.0)
                for _ in range(3):
                    run.step({})
                self.assertEqual(mei.mover.body_scale, 1.0)
