"""Real Flashbang input against Torbjorn under the reconstructed control host."""

import os
import unittest
from unittest import mock

import test_game_torbjorn_molten as area_tests
import test_mccree_flashbang as flash_tests

from ow174.game import hero_torbjorn, molten_core
from ow174.game.commands import Command
from ow174.game.content import heroes


@unittest.skipUnless(os.environ.get("ORPHEUS_TORBJORN_GRAPH_BUNDLE"), "needs prepared Torbjorn bundle")
class TorbjornControlTests(unittest.TestCase):
    setUpClass = classmethod(lambda cls: area_tests.TorbjornMoltenTests.setUpClass())
    tearDownClass = classmethod(lambda cls: area_tests.TorbjornMoltenTests.tearDownClass())
    step = flash_tests.FlashbangTests.step
    cast = flash_tests.FlashbangTests.cast
    health = flash_tests.FlashbangTests.health

    def arena(self, distance=3.0):
        run, caster, target = flash_tests.FlashbangTests().arena(distance=distance)
        position = list(target.mover.position)
        self.assertTrue(run.match.switch_hero(target, heroes()[hero_torbjorn.HERO]))
        target.mover.position[:] = position
        for _ in range(35):
            self.step(run, caster, target)
        return run, caster, target

    def test_damage_input_and_movement_denial_then_actual_recovery(self):
        run, caster, target = self.arena()
        self.cast(run, caster, target)
        fight = run.match.combat
        self.assertEqual(self.health(run, target), 230.0)
        end = fight.stuns[target.body]
        position = tuple(target.mover.position)
        before = self.health(run, caster)
        while run.frame < end - 1:
            self.step(run, caster, target, target_buttons=1 | 2 | 8 | 16 | 32 | 0x800, forward=127)
            self.assertEqual(tuple(target.mover.position), position)
            self.assertEqual(self.health(run, caster), before)
            self.assertFalse(target.body_script.component.held)
            self.assertEqual(fight.vitals[target.body].over_armour.amount, 0.0)
            self.assertEqual(fight.turrets, {})
            self.assertEqual(fight.molten_pools, {})
        for _ in range(30):
            self.step(run, caster, target, target_buttons=1, forward=127)
        self.assertNotEqual(tuple(target.mover.position), position)
        self.assertLess(self.health(run, caster), before)
        self.assertNotIn(target.body, fight.stuns)

    def test_existing_overload_buff_survives_stun_without_refill_and_expires(self):
        run, caster, target = self.arena()
        self.step(run, caster, target, target_buttons=16)
        started = run.frame
        hp = run.match.combat.vitals[target.body]
        self.assertEqual(hp.over_armour.amount, 100.0)
        source = hp.over_armour.source
        self.cast(run, caster, target, target_buttons=16)
        self.assertEqual(hp.over_armour.amount, 80.0)
        while run.frame < started + 300:
            self.step(run, caster, target, target_buttons=16)
            self.assertEqual(hp.over_armour.source, source)
            self.assertEqual(hp.over_armour.amount, 80.0)
        while run.frame < started + 320:
            self.step(run, caster, target, target_buttons=16)
        self.assertEqual(hp.over_armour.amount, 0.0)
        self.assertIsNone(hp.over_armour.source)
        while run.frame < started + 900:
            self.step(run, caster, target, target_buttons=16)
            self.assertEqual(hp.over_armour.amount, 0.0)
        while run.frame < started + 1000:
            self.step(run, caster, target, target_buttons=16)
        self.assertEqual(hp.over_armour.amount, 100.0)
        self.assertNotEqual(hp.over_armour.source, source)

    def test_hammer_swing_interrupted_before_damage_and_can_attack_after_recovery(self):
        run, caster, target = self.arena(distance=2.0)
        run.frame += 1
        target.take_commands([Command(run.frame, action=2)])
        for _ in range(45):
            self.step(run, caster, target)
        self.step(run, caster, target, target_buttons=1)
        self.cast(run, caster, target, target_buttons=1)
        before = self.health(run, caster)
        end = run.match.combat.stuns[target.body]
        while run.frame < end - 1:
            self.step(run, caster, target, target_buttons=1)
            self.assertEqual(self.health(run, caster), before)
        for _ in range(65):
            self.step(run, caster, target, target_buttons=1)
        self.assertLess(self.health(run, caster), before)

    def test_existing_turret_and_ground_burn_continue_during_owner_stun(self):
        run, caster, target = self.arena()
        fight = run.match.combat
        x, y, z = target.mover.position
        turret = hero_torbjorn.deploy_turret(fight, target, (x + 1.5, y, z))
        for _ in range(190):
            self.step(run, caster, target)
        pool = molten_core.create_pool(fight, target, tuple(caster.mover.position))
        self.cast(run, caster, target)
        shots, before = turret.shots, self.health(run, caster)
        end = fight.stuns[target.body]
        while run.frame < end - 1:
            self.step(run, caster, target)
        self.assertTrue(fight.is_stunned(target))
        self.assertGreater(turret.shots, shots)
        self.assertLess(self.health(run, caster), before)
        self.assertIn(pool.entity, fight.molten_pools)
        self.assertIn((target.entity, caster.body), fight.molten_burns)

    def test_full_charge_ultimate_is_denied_during_stun_and_starts_after_recovery(self):
        run, caster, target = self.arena()
        target.body_script.set_ult_charge(10000.0)
        self.cast(run, caster, target)
        end = run.match.combat.stuns[target.body]
        while run.frame < end - 1:
            self.step(run, caster, target, target_buttons=32)
            self.assertEqual(target.body_script.component.vars[342].value(), 10000.0)
        for _ in range(10):
            self.step(run, caster, target, target_buttons=32)
        self.assertLess(target.body_script.component.vars[342].value(), 10000.0)

    def test_active_ultimate_pauses_fire_during_stun_and_resumes_within_original_duration(self):
        run, caster, target = self.arena()
        fight = run.match.combat
        target.body_script.set_ult_charge(10000.0)
        self.step(run, caster, target, target_buttons=32)
        started = run.frame
        for _ in range(45):
            self.step(run, caster, target)
        with mock.patch.object(fight, "_shoot", wraps=fight._shoot) as shoot:
            def launches():
                return [call for call in shoot.call_args_list
                        if call.args[2].instance.graph.index == 0x16DA and call.args[2].index == 17]

            for _ in range(20):
                self.step(run, caster, target, target_buttons=1)
            self.assertGreater(len(launches()), 0)
            self.cast(run, caster, target, target_buttons=1)
            before = len(launches())
            end = fight.stuns[target.body]
            while run.frame < end - 1:
                self.step(run, caster, target, target_buttons=1)
                self.assertEqual(len(launches()), before)
            for _ in range(30):
                self.step(run, caster, target, target_buttons=1)
            self.assertGreater(len(launches()), before)
            self.assertLess(len(launches()), 10)  # retirement below must be timed, not ammo exhaustion
            while run.frame < started + 410:
                self.step(run, caster, target)
            before = len(launches())
            for _ in range(80):
                self.step(run, caster, target, target_buttons=1)
            self.assertEqual(len(launches()), before)
