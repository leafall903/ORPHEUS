"""Authored healing fuel and reconstructed successful-hit recovery callback."""

import unittest
from unittest.mock import patch

import test_game_moira as fixtures

from ow174.game import combat
from ow174.game.commands import Command
from ow174.game.script import expr


class MoiraFuelTests(unittest.TestCase):
    setup_fight = fixtures.MoiraSprayTests.setup_fight

    @staticmethod
    def weapon(source):
        return next(i for i in source.body_script.component.instances.values() if i.graph.index == 0x0F76)

    def test_sustained_heal_consumes_authored_fuel(self):
        run, source, _target, _vitals = self.setup_fight()
        instance = self.weapon(source)
        self.assertEqual(instance.find_var(expr.INSTANCE, 87).value(), 100)
        for _ in range(90):
            run.step({source: (0, 0, 1)})
        # Authored 0.2-second pulses at12.5/sec, not a synthetic per-shot counter.
        self.assertEqual(instance.find_var(expr.INSTANCE, 87).value(), 82.5)

    def test_empty_fuel_refuses_spray_and_passive_recovery_restores_availability(self):
        run, source, _target, vitals = self.setup_fight()
        instance = self.weapon(source)
        instance.write((expr.INSTANCE, 87), 0.0)
        before = vitals.total
        for _ in range(60):
            run.step({source: (0, 0, 1)})
        self.assertEqual(vitals.total, before)
        self.assertEqual(run.match.combat.rockets, [])
        self.assertGreater(instance.find_var(expr.INSTANCE, 87).value(), 0)
        for _ in range(150):
            run.step({})
        self.assertGreater(instance.find_var(expr.INSTANCE, 87).value(), 5)
        for _ in range(30):
            run.step({source: (0, 0, 1)})
        self.assertGreater(vitals.total, before)
        self.assertGreaterEqual(instance.find_var(expr.INSTANCE, 87).value(), 0)

    def test_fuel_regenerates_on_the_authored_clock_without_owner_input(self):
        checkpoints = (35, 117, 422)
        expected = []
        for mode in ("continuous", "silent", "jump"):
            with self.subTest(mode=mode):
                run, source, _, health = self.setup_fight()
                weapon = self.weapon(source)
                weapon.write((expr.INSTANCE, 87), 0.0)
                fuel = weapon.find_var(expr.INSTANCE, 87)
                start = run.frame
                before = health.total
                actual = []
                for elapsed in checkpoints if mode == "jump" else range(1, checkpoints[-1] + 1):
                    if mode == "continuous":
                        run.step({})
                    else:
                        run.frame = start + elapsed
                        run.match.update(run.frame * combat.TICK, run.frame)
                    if elapsed in checkpoints:
                        actual.append(fuel.value())
                if mode == "continuous":
                    expected = actual
                    self.assertGreater(expected[0], 0)
                    self.assertGreater(expected[-1], 5)
                else:
                    self.assertEqual(actual, expected)
                    self.assertEqual(health.total, before)
                    command = Command(run.frame)
                    source.take_commands([command, command])
                    self.assertEqual(fuel.value(), expected[-1])
                    for _ in range(20):
                        run.step({source: (0, 0, 1)})
                    self.assertGreater(health.total, before)

    def test_successful_grasp_executes_authored_recovery_but_miss_does_not(self):
        results = []
        for hit in (True, False):
            run, source, _target, _vitals = self.setup_fight(ally=False)
            instance = self.weapon(source)
            instance.write((expr.INSTANCE, 87), 10.0)
            run.step({source: (0 if hit else 16384, 0, 2)})
            results.append(instance.find_var(expr.INSTANCE, 87).value())
        self.assertAlmostEqual(results[0] - results[1], 3, places=4)

    def test_silent_owner_stops_spending_held_spray_fuel_and_recovers_to_cap(self):
        run, source, _, _ = self.setup_fight()
        fight = run.match.combat
        weapon = self.weapon(source)
        fuel = weapon.find_var(expr.INSTANCE, 87)
        for _ in range(35):
            run.step({source: (0, 0, 1)})
        consumed = fuel.value()
        self.assertLess(consumed, 100)
        with patch.object(fight, "_shoot", wraps=fight._shoot) as shots:
            for _ in range(80):
                run.frame += 1
                run.match.update(run.frame * combat.TICK, run.frame)
        shots.assert_not_called()
        self.assertGreater(fuel.value(), consumed)
        weapon.write((expr.INSTANCE, 87), 99.9)
        for _ in range(40):
            run.frame += 1
            run.match.update(run.frame * combat.TICK, run.frame)
        self.assertEqual(fuel.value(), weapon.find_var(expr.INSTANCE, 971).value())

    def test_grasp_respects_nonpositive_authored_target_cap(self):
        fuel = {}
        for cap in (None, -1, 0, 1):
            with self.subTest(cap=cap):
                run, source, _target, vitals = self.setup_fight(ally=False)
                instance = self.weapon(source)
                search = instance.state(106)
                instance.write((expr.INSTANCE, 87), 10.0)
                config = None if cap is None else {'$': 'STUConfigVarInt', 'm_value': cap}
                before = vitals.total
                with patch.dict(search.node.fields, m_C39691D9=config):
                    run.step({source: (0, 0, 2)})
                self.assertTrue(search.active)
                self.assertAlmostEqual(before - vitals.total, 0 if cap in (-1, 0) else 0.8, places=4)
                fuel[cap] = instance.find_var(expr.INSTANCE, 87).value()
        self.assertEqual(fuel[-1], fuel[0])
        self.assertEqual(fuel[None], fuel[1])
        self.assertAlmostEqual(fuel[1] - fuel[0], 3, places=4)

    def test_grasp_refill_caps_at_authored_maximum_and_cannot_heal_allies(self):
        for ally in (False, True):
            with self.subTest(ally=ally):
                run, source, _target, vitals = self.setup_fight(ally=ally)
                instance = self.weapon(source)
                instance.write((expr.INSTANCE, 87), 99.0)
                run.step({source: (0, 0, 2)})
                fuel = instance.find_var(expr.INSTANCE, 87).value()
                self.assertLessEqual(fuel, instance.find_var(expr.INSTANCE, 971).value())
                if not ally:
                    self.assertEqual(fuel, 100)
                    self.assertLess(vitals.total, 100)
                else:
                    self.assertEqual(vitals.total, 100)
                    self.assertLess(fuel, 100)

    def test_replayed_command_cannot_repeat_damage_or_recovery(self):
        run, source, _target, vitals = self.setup_fight(ally=False)
        instance = self.weapon(source)
        instance.write((expr.INSTANCE, 87), 10.0)
        run.step({source: (0, 0, 2)})
        before = vitals.total, instance.find_var(expr.INSTANCE, 87).value(), source.body_script.frame
        for _ in range(5):
            source.take_commands([Command(run.frame, buttons=2)])
        self.assertEqual((vitals.total, instance.find_var(expr.INSTANCE, 87).value(),
                          source.body_script.frame), before)

    def test_spamming_primary_cannot_bypass_empty_resource(self):
        run, source, _target, vitals = self.setup_fight()
        instance = self.weapon(source)
        instance.write((expr.INSTANCE, 87), 0.0)
        for frame in range(90):
            run.step({source: (0, 0, 1 if frame % 2 == 0 else 0)})
        self.assertEqual(vitals.total, 100)
        self.assertEqual(run.match.combat.rockets, [])
        self.assertGreater(instance.find_var(expr.INSTANCE, 87).value(), 0)
        self.assertLess(instance.find_var(expr.INSTANCE, 87).value(), 5)

    def test_respawn_creates_a_fresh_authored_resource_pool(self):
        run, source, target, _vitals = self.setup_fight(ally=False)
        instance = self.weapon(source)
        instance.write((expr.INSTANCE, 87), 10.0)
        fight = run.match.combat
        own = next(t for t in fight._targets() if t.owner is source)
        old = source.body
        fight._damage(target.owner, own, 1000, False, None, run.frame)
        for _ in range(210):
            run.step({})
        self.assertNotEqual(source.body, old)
        self.assertFalse(fight.is_dead(source))
        self.assertEqual(self.weapon(source).find_var(expr.INSTANCE, 87).value(), 100)
