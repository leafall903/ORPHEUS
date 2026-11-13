"""Minimal original007D force operands through actual Charge command dispatch.

Offline host verification; shared recipient delivery/native acceptance pending.
"""
import unittest
from unittest.mock import patch

import test_delivered_reinhardt as delivered


class ChargeForceTests(unittest.TestCase):
    def test_side_bump_adds_vertical_two_and_clamps_to_ten(self):
        for incoming, delta in ((20., 2.), (-20., 30.)):
            with self.subTest(incoming=incoming):
                case = delivered.ChargeMovementTests()
                case.setUp()
                case.start()
                case.advance(50, buttons=0)
                case.place(case.enemy, (1.1, 1., case.rein.mover.position[2] + 1.))
                case.enemy.mover.velocity = (0., incoming, 0.)
                fight = case.match.combat
                with patch.object(fight.knockbacks, 'apply', wraps=fight.knockbacks.apply) as applied:
                    case.advance(1, buttons=0)
                rows = [call for call in applied.call_args_list if call.args[0].owner is case.enemy]
                self.assertEqual(case.health(case.enemy), 150.)
                self.assertEqual(len(rows), 1)
                self.assertEqual(rows[0].args[1].y, delta)

    def test_frontal_capture_consumes_state65_once_without_bump_damage(self):
        case = delivered.ChargeMovementTests()
        case.setUp()
        case.start()
        case.advance(50, buttons=0)
        case.place(case.enemy, (0., 1., case.rein.mover.position[2] + 1.))
        fight = case.match.combat
        # Original117 rejects candidate entity flag602 before force or capture.
        case.enemy.body_script.component.set(602, True)
        with patch.object(fight.knockbacks, 'apply', wraps=fight.knockbacks.apply) as rejected:
            case.advance(1, buttons=0)
        self.assertFalse(rejected.call_args_list)
        self.assertIsNone(fight.charges[case.rein.body].pinned)
        case.enemy.body_script.component.set(602, False)
        with patch.object(fight.knockbacks, 'apply', wraps=fight.knockbacks.apply) as applied:
            case.advance(10, buttons=0)
        rows = [call for call in applied.call_args_list if call.args[0].owner is case.enemy]
        self.assertEqual(case.health(case.enemy), 200.)
        self.assertEqual(fight.charges[case.rein.body].pinned, case.enemy.body)
        self.assertEqual(len(rows), 1)
        impulse = rows[0].args[1]
        self.assertAlmostEqual(impulse.z, 1.)
        self.assertEqual(impulse.y, 10.)


if __name__ == '__main__':
    unittest.main()
