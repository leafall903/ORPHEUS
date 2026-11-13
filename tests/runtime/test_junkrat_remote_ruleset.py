"""096B's live creator scalar, independently of primary-fire ruleset10638."""
import math
import unittest
from unittest.mock import patch

import test_junkrat_mines as mines
import test_junkrat_projectile_combat as scenes
from ow174.game.commands import FIRE
from ow174.game.script import expr


class JunkratRemoteRulesetTests(unittest.TestCase):
    def test_remote_samples_live_source_scalar_once_and_keeps_damage_independent(self):
        for scalar in (0.0, .5, 2.0):
            with self.subTest(scalar=scalar):
                scene = mines.JunkratMineTests()
                scene.setUp()
                scene.throw()
                mine, = scene.fight.junkrat.mines.values()
                # The source changes its value after the original throw.
                mine.instance.write((expr.ENTITY, 10614), scalar)
                mine.instance.write((expr.ENTITY, 10638), 7.0)
                with patch.object(scene.fight.knockbacks, "apply", return_value=True) as apply:
                    scene.fight.junkrat.consume_message(mine, 0x003F)
                hits = [call for call in apply.call_args_list if call.args[0].entity == scene.enemy.body]
                self.assertEqual(len(hits), int(scalar != 0))
                if hits:
                    x, y, z = hits[0].args[1]
                    self.assertAlmostEqual(math.hypot(x, z), 15 * scalar)
                    self.assertEqual(y, 15 * scalar)
                self.assertEqual(scene.health(scene.run, scene.enemy).total, 80)
                self.assertNotIn(mine.entity, scene.fight.junkrat.mines)

    def test_remote_null_override_uses_original_schema_default(self):
        scene = mines.JunkratMineTests()
        scene.setUp()
        scene.throw()
        mine, = scene.fight.junkrat.mines.values()
        with patch.object(scene.fight.knockbacks, "apply", return_value=True) as apply:
            scene.fight.junkrat.consume_message(mine, 0x003F)
        hits = [call for call in apply.call_args_list if call.args[0].entity == scene.enemy.body]
        self.assertEqual(len(hits), 1)
        x, y, z = hits[0].args[1]
        self.assertAlmostEqual(math.hypot(x, z), 15)
        self.assertEqual(y, 15)

    def test_frag_uses_10638_at_release_and_does_not_read_mine_scalar(self):
        scene = scenes.JunkratProjectileTests()
        run, player, enemy = scene.setup_fight()
        script = player.body_script
        instance = next(iter(script.component.instances.values()))
        instance.write((expr.ENTITY, 10638), .5)
        instance.write((expr.ENTITY, 10614), 4.0)
        run.step({player: (0, 0, FIRE)})
        rocket, = run.match.combat.rockets
        self.assertEqual((rocket.blast.horizontal_impulse, rocket.blast.vertical_impulse), (2.5, 4))
