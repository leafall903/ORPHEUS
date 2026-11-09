"""Actual manual13EB emission reaches all25 independent physical payloads."""

import unittest
from unittest.mock import patch

import test_bastion_npc_combat as fixtures

from ow174.game import combat, npc_control


class BastionEnforcerPelletTests(unittest.TestCase):
    fixture = fixtures.BastionNpcCombatTests.fixture

    @classmethod
    def setUpClass(cls):
        fixtures.BastionNpcCombatTests.setUpClass.__func__(cls)

    @classmethod
    def tearDownClass(cls):
        fixtures.BastionNpcCombatTests.tearDownClass.__func__(cls)

    def first_emission(self, mode):
        self.fixture('2', mode, distance=1.4)
        case, bot = self.case, self.bot
        captured = []
        original = combat.Combat._shoot

        def observe(fight, shooter, *args, **kwargs):
            before = {id(r) for r in fight.rockets}
            original(fight, shooter, *args, **kwargs)
            if shooter is bot:
                captured.append([r for r in fight.rockets if id(r) not in before])

        npc_control.start(case.run.match, case.player)
        with patch.object(combat.Combat, '_shoot', observe):
            for _ in range(300):
                case.step()
                if captured:
                    break
        self.assertTrue(captured, 'Actual13EB never emitted a volley')
        return captured[0]

    def test_three_forms_receive25_distinct_authored_pellets_with_per_pellet_armour(self):
        for mode, damage in (('recon', 25), ('sentry', 20), ('tank', 20)):
            with self.subTest(mode=mode):
                rockets = self.first_emission(mode)
                case = self.case
                self.assertEqual(len(rockets), 25)
                self.assertEqual(len({r.direction for r in rockets}), 25)
                self.assertTrue(all(r.shot is None for r in rockets))  # no >7-pellet prediction lease claim
                self.assertEqual({(r.speed, r.direct.amount, r.left) for r in rockets}, {(80, 2, 1.25)})
                before = case.health(case.player).total
                case.step(0, 10)
                self.assertAlmostEqual(before - case.health(case.player).total, damage, places=5)
                self.assertFalse(case.fight.rockets)
                # Already-retired payloads cannot settle again on later frames.
                after = case.health(case.player).total
                case.step(0, 10)
                self.assertEqual(case.health(case.player).total, after)

    def test_wall_stops_every_already_flying_pellet_then_manual_fire_recovers(self):
        for mode in ('recon', 'sentry', 'tank'):
            with self.subTest(mode=mode):
                rockets = self.first_emission(mode)
                self.assertEqual(len(rockets), 25)
                case = self.case
                world = case.run.match.collision_world
                before = case.health(case.player).total
                case.wall(.7)
                case.step(0, 30)
                self.assertEqual(case.health(case.player).total, before)
                self.assertFalse(case.fight.rockets)
                self.assertFalse(self.bot.attack_input)
                case.run.match.collision_world = world
                for _ in range(300):
                    case.step()
                    if case.health(case.player).total < before:
                        break
                self.assertLess(case.health(case.player).total, before)

    def test_manual_stop_retires_all_pending_pellets_before_any_damage(self):
        for mode in ('recon', 'sentry', 'tank'):
            with self.subTest(mode=mode):
                self.assertEqual(len(self.first_emission(mode)), 25)
                case = self.case
                before = case.health(case.player).total
                self.assertEqual(npc_control.stop(case.run.match), 1)
                self.assertFalse(case.fight.rockets)
                case.step(0, 100)
                self.assertEqual(case.health(case.player).total, before)

    def test_missing_launch_service_does_not_substitute_a_single_or_coincident_payload(self):
        self.fixture('2', distance=1.4)
        case = self.case
        npc_control.start(case.run.match, case.player)
        world = self.bot.body_script.component.world
        with (patch.object(world, 'weapon_launch_inputs', return_value=None),
              self.assertLogs('ow174.game', level='WARNING')):
            case.step(0, 90)
        self.assertEqual(case.health(case.player).total, 300)
        self.assertFalse(case.fight.rockets)
        for _ in range(300):
            case.step()
            if case.health(case.player).total < 300:
                break
        self.assertLess(case.health(case.player).total, 300)
