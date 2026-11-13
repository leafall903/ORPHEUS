"""Original Accretion projectile output drives its authored recovery branch."""

import json
import unittest
from pathlib import Path

from test_sigma_combat import SigmaCombatTests

from ow174.game import accretion
from ow174.game.script import expr


class SigmaAccretionOutputTests(unittest.TestCase):
    def test_admitted_rock_exports_shared_identity_and_recovers_without_timers(self):
        fixture = json.loads((Path(__file__).resolve().parents[2] /
                              "fixtures/behavior/sigma-accretion-output-v1.json").read_text())
        run, owner, enemy, health = SigmaCombatTests().arena(
            offset=0, distance=fixture["input"]["target_distance_m"])
        run.step({owner: (0, 0, fixture["input"]["button"])})
        component = owner.body_script.component
        instance = next(i for i in component.instances.values() if i.graph.index == accretion.GRAPH)
        windup = instance.node_state(instance.graph.node(31))
        recovery = instance.node_state(instance.graph.node(44))
        self.assertTrue(windup.active)
        self.assertEqual(component.entity_var(20360).value(),
                         fixture["expected"]["projectile_output_before_launch"])
        shot = None
        saw_recovery = False
        for _ in range(fixture["input"]["frames"]):
            run.step({})
            if run.match.combat.rockets:
                rocket, = run.match.combat.rockets
                shot = rocket.shot
                self.assertIs(accretion.lookup(run.match.combat, rocket.entity), rocket)
                self.assertIsNotNone(component.world._lookup_entity_query(rocket.entity))
                self.assertEqual(component.entity_var(20360).value(), expr.Entity(rocket.entity))
                self.assertEqual({d.viewer for d in shot.observers}, {owner, enemy})
                self.assertEqual({d.record.ids for d in shot.observers}, {(rocket.entity,)})
            if recovery.active:
                saw_recovery = True
                self.assertFalse(windup.active)
        self.assertIsNotNone(shot)
        self.assertTrue(saw_recovery)
        self.assertFalse(windup.active)
        self.assertFalse(recovery.active)
        self.assertFalse(instance.node_state(instance.graph.node(1)).active)
        self.assertIsNone(component.world._lookup_entity_query(shot.entity))
        self.assertEqual(health.total, fixture["expected"]["health_after"])


if __name__ == "__main__":
    unittest.main()
