"""Original receiver response through two actual skill inputs, no transport duplication."""

import json
import math
import unittest
from pathlib import Path
from unittest.mock import patch

import test_delivered_reinhardt as delivered

from ow174.game import combat, defenses
from ow174.game.content import heroes

FIXTURE = Path(__file__).resolve().parents[2] / "fixtures/behavior/reinhardt-force-response-v1.json"
EVIDENCE = {}


class ReinhardtForceResponseTests(unittest.TestCase):
    def expected(self, kind):
        return json.loads(FIXTURE.read_text())["runtime_checks"][kind]

    def test_hammer_uses_victim_response_once_and_preserves_damage(self):
        case = delivered.HammerTests()
        case.setUp()
        case.match.switch_hero(case.enemy, heroes()[combat.REINHARDT])
        case.place(case.enemy, (0.0, 1.0, 3.0))
        case.place(case.other, (100.0, 1.0, 0.0))
        for _ in range(30):
            case.run.step({})
        before = case.health(case.enemy)
        scale = defenses.knockback_scale(case.enemy)
        fight = case.match.combat
        with patch.object(fight.knockbacks, "apply", wraps=fight.knockbacks.apply) as applied:
            case.advance(40)
        rows = [call for call in applied.call_args_list if call.args[0].owner is case.enemy]
        expected = self.expected("hammer")
        EVIDENCE["hammer"] = {
            "response": scale,
            "damage": before - case.health(case.enemy),
            "impulses": [list(call.args[1]) for call in rows],
        }
        self.assertAlmostEqual(scale, 0.7)
        self.assertEqual(before - case.health(case.enemy), expected["damage"])
        self.assertEqual(len(rows), expected["force_count"])
        self.assertAlmostEqual(
            math.hypot(rows[0].args[1].x, rows[0].args[1].z), expected["horizontal"], places=5
        )

    def test_charge_side_response_scales_the_formed_delta_once(self):
        case = delivered.ChargeMovementTests()
        case.setUp()
        case.match.switch_hero(case.enemy, heroes()[combat.REINHARDT])
        case.place(case.enemy, (100.0, 1.0, 0.0))
        for _ in range(30):
            case.run.step({})
        case.start()
        case.advance(50, buttons=0)
        # Keep the larger Reinhardt capsule outside the authored capture width.
        target = case.match.combat._target_entity(case.enemy.body)
        side_x = float(case.charge().find_var(0, 11861).value()) + target.radius + 0.1
        case.place(case.enemy, (side_x, 1.0, case.rein.mover.position[2] + 1.0))
        case.enemy.mover.velocity = (0.0, 20.0, 0.0)
        before = case.health(case.enemy)
        scale = defenses.knockback_scale(case.enemy)
        fight = case.match.combat
        with patch.object(fight.knockbacks, "apply", wraps=fight.knockbacks.apply) as applied:
            case.advance(1, buttons=0)
        rows = [call for call in applied.call_args_list if call.args[0].owner is case.enemy]
        expected = self.expected("charge_side")
        EVIDENCE["charge_side"] = {
            "response": scale,
            "damage": before - case.health(case.enemy),
            "impulses": [list(call.args[1]) for call in rows],
        }
        self.assertAlmostEqual(scale, 0.7)
        self.assertEqual(before - case.health(case.enemy), expected["damage"])
        self.assertEqual(len(rows), expected["force_count"])
        self.assertAlmostEqual(
            math.hypot(rows[0].args[1].x, rows[0].args[1].z), expected["horizontal"], places=5
        )
        self.assertAlmostEqual(rows[0].args[1].y, expected["vertical"], places=5)
