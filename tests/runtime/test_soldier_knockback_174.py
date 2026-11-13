"""104319 Soldier force omissions; wire readability is separate from native adoption."""

import json
import os
import unittest
from pathlib import Path

from ow174.game import combat
from ow174.game.collision import flat_world
from ow174.game.soldier_knockback import helix_force_axes
from tests.runtime.test_game_combat import Run


class SoldierKnockback174Tests(unittest.TestCase):
    def scene(self):
        run = Run(names=('Soldier', 'Enemy'))
        run.match.collision_world = flat_world(0)
        soldier, enemy = run.players
        for owner, position in ((soldier, (1000, 0, 1000)), (enemy, (1000, 0, 1001.5))):
            owner.mover.position = list(position)
            owner.mover.body = None
            owner.body_script.create_arrived = True
        for _ in range(20):
            run.step({})
        return run, soldier, enemy

    def test_helix_force_axes_follow_original_rings_and_outer_boundary(self):
        _run, soldier, _enemy = self.scene()
        weapon = next(i for i in soldier.body_script.component.instances.values() if i.graph.index == 0x254)
        state = weapon.state(16)
        blast = combat.hit_def(state, state.node.field('m_D2D11CE9'))
        self.assertEqual((blast.horizontal_impulse, blast.vertical_impulse), (4, 6))
        self.assertEqual(blast.impulse_rings, ((.5, 1, 1), (3, .75, .25)))
        fixture_root = Path(os.environ.get(
            "ORPHEUS_BEHAVIOR_FIXTURES", Path(__file__).resolve().parents[2] / "fixtures/behavior",
        ))
        fixture = json.loads((fixture_root / "soldier-helix-force-axes-v1.json").read_text(encoding="utf-8"))
        for case in fixture["cases"]:
            distance, expected = case["input"]["distance"], case["expected"]["velocity"]
            with self.subTest(distance=distance):
                horizontal, vertical = helix_force_axes(blast, distance)
                for actual, want in zip((horizontal, vertical, 0), expected, strict=True):
                    self.assertAlmostEqual(actual, want, places=5)
        self.assertEqual((blast.self_scale, blast.source_impulse_scale), (.5, 1))
