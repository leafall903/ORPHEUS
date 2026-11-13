"""Real primary contact preserves 022B's independent self-damage/self-force settings."""
import json
from pathlib import Path
import unittest
from unittest.mock import patch

import test_junkrat_knockback_producers_r2 as scenes
from test_game_combat import aim
from ow174.game.commands import FIRE
from ow174.game.mover import Mover, mover_data


class JunkratOriginalSelfImpulseTests(unittest.TestCase):
    def test_actual_frag_contact_self_pushes_once_without_self_damage(self):
        fixture = Path(__file__).resolve().parents[2] / "fixtures/behavior/junkrat-original-self-impulse-v1.json"
        expected = json.loads(fixture.read_text(encoding="utf-8"))["expected"]
        scene = scenes.JunkratForceProducerR2Tests()
        scene.setUp()
        scene.victim.mover = Mover((1000, 0, 1001.5), world=scene.run.match.collision_world,
                                   data=mover_data(scene.victim.hero.body))
        yaw, pitch = aim(scene.fight._eye(scene.source), (1000, .9, 1001.5))
        before = scene.health(scene.source)
        blasts = []
        explode = scene.fight._explode

        def observe(rocket, point, **kwargs):
            blasts.append((rocket.blast.self_scale, rocket.blast.source_impulse_scale))
            return explode(rocket, point, **kwargs)

        with patch.object(scene.fight, "_explode", side_effect=observe), \
                patch.object(scene.fight.knockbacks, "apply", wraps=scene.fight.knockbacks.apply) as apply:
            scene.step(FIRE, yaw=yaw, pitch=pitch)
            for _ in range(20):
                scene.step(yaw=yaw, pitch=pitch)
                if apply.call_count:
                    break
        self.assertEqual(blasts, [(0, .5)])
        own = [c for c in apply.call_args_list if c.args[0].entity == scene.source.body]
        enemy = [c for c in apply.call_args_list if c.args[0].entity == scene.victim.body]
        self.assertEqual((len(own), len(enemy)), (1, 1))
        for actual, wanted in zip(own[0].args[1], expected["source_velocity_delta"], strict=True):
            self.assertAlmostEqual(actual, wanted, places=5)
        self.assertEqual(own[0].kwargs, dict(impact_tick=expected["impact_world_tick"],
                                            source_entity=scene.source.body))
        self.assertEqual(scene.health(scene.source), before)
        self.assertEqual(before, expected["source_health"])
        self.assertEqual(tuple(bot.entity for bot in scene.run.match.bots), scene.npcs)
