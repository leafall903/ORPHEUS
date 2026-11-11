"""Real Flashbang input against Torbjorn under the reconstructed control host."""

import os
import unittest
from unittest import mock

import test_game_torbjorn_molten as area_tests
import test_mccree_flashbang as flash_tests
from test_game_combat import aim

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

    def test_overload_cooldown_finishes_during_flashbang_without_buffering_blocked_input(self):
        for held in (False, True):
            with self.subTest(held=held):
                run, caster, target = self.arena()
                fight = run.match.combat
                hp = fight.vitals[target.body]
                self.step(run, caster, target, target_buttons=16)
                source = hp.over_armour.source
                self.assertEqual(hp.over_armour.amount, 100)
                for _ in range(1000):
                    if hero_torbjorn.overload_cooldown_remaining(target) <= .4:
                        break
                    self.step(run, caster, target)
                self.assertIsNone(hp.over_armour.source)
                self.assertGreater(hero_torbjorn.overload_cooldown_remaining(target), 0)

                self.cast(run, caster, target)
                self.assertEqual(hp.total, 230)
                self.assertGreater(hero_torbjorn.overload_cooldown_remaining(target), 0)
                end = fight.stuns[target.body]
                while run.frame < end - 1:
                    button = 16 if held or run.frame < end - 3 else 0
                    self.step(run, caster, target, target_buttons=button)
                    self.assertTrue(fight.is_stunned(target))
                    self.assertFalse(target.body_script.component.held)
                    self.assertIsNone(hp.over_armour.source)
                    self.assertEqual(hp.total, 230)
                self.assertEqual(hero_torbjorn.overload_cooldown_remaining(target), 0)

                if not held:
                    for _ in range(12):
                        self.step(run, caster, target)
                        self.assertIsNone(hp.over_armour.source)
                    self.assertFalse(fight.is_stunned(target))
                self.step(run, caster, target, target_buttons=16)
                self.assertFalse(fight.is_stunned(target))
                if held:
                    self.assertEqual(run.frame, end)
                self.assertEqual(hp.over_armour.amount, 100)
                self.assertEqual(hp.over_armour.source, (source[0], source[1] + 1))
                self.assertEqual(hp.total, 330)

    def test_turret_dismantle_cooldown_finishes_during_flashbang_but_throw_waits(self):
        for held in (False, True):
            with self.subTest(held=held):
                run, caster, torb = self.arena()
                fight = run.match.combat
                x, y, z = torb.mover.position
                caster.mover.position = [x + 100, y, z]
                caster.mover.body = None
                run.step({torb: (0, 4000, 8)})
                for _ in range(225):
                    run.step({})
                first = fight.turrets[torb.entity]
                run.step({torb: (0, 0, 4)})
                self.assertNotIn(torb.entity, fight.turrets)
                self.assertNotIn(first.actor, run.match.bots)
                self.assertGreater(hero_torbjorn.turret_cooldown_remaining(torb), 9.9)
                run.step({torb: (0, 4000, 8)})
                for _ in range(660):
                    if hero_torbjorn.turret_cooldown_remaining(torb) <= .4:
                        break
                    run.step({})
                    self.assertFalse(fight.turrets)
                    self.assertFalse(fight.rockets)
                self.assertGreater(hero_torbjorn.turret_cooldown_remaining(torb), 0)

                caster.mover.position = [x + 3, y, z]
                caster.mover.body = None
                self.cast(run, caster, torb)
                self.assertEqual(self.health(run, torb), 230)
                self.assertGreater(hero_torbjorn.turret_cooldown_remaining(torb), 0)
                end = fight.stuns[torb.body]
                while run.frame < end - 1:
                    button = 8 if held or run.frame < end - 3 else 0
                    run.step({torb: (0, 4000, button)})
                    self.assertTrue(fight.is_stunned(torb))
                    self.assertFalse(torb.body_script.component.held)
                    self.assertFalse(fight.turrets)
                    self.assertFalse(fight.rockets)
                self.assertEqual(hero_torbjorn.turret_cooldown_remaining(torb), 0)

                if not held:
                    for _ in range(35):
                        run.step({})
                        self.assertFalse(fight.turrets)
                        self.assertFalse(fight.rockets)
                run.step({torb: (0, 4000, 8)})
                self.assertFalse(fight.is_stunned(torb))
                if held:
                    self.assertEqual(run.frame, end)
                launched = False
                for _ in range(100):
                    launched |= any(rocket.shooter is torb and rocket.spawn == hero_torbjorn.TURRET
                                    for rocket in fight.rockets)
                    if torb.entity in fight.turrets:
                        break
                    run.step({})
                self.assertTrue(launched)
                replacement = fight.turrets[torb.entity]
                self.assertIs(replacement.owner, torb)
                self.assertNotEqual(replacement.actor.entity, first.actor.entity)

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

    def test_flashbang_cancels_pending_turret_repair_and_fresh_hammer_recovers(self):
        run, caster, torb = self.arena()
        fight = run.match.combat
        x, y, z = torb.mover.position
        caster.mover.position = [x + 100, y, z]
        caster.mover.body = None
        run.step({torb: (0, 4000, 8)})
        for _ in range(225):
            run.step({})
        turret = fight.turrets[torb.entity]
        hp = fight.vitals[turret.actor.entity]
        self.assertEqual(hp.total, 250)
        hp.damage(120)
        fight._sync_body_health(turret.actor, hp)
        x, y, z = turret.actor.position
        torb.mover.position = [x - 1.5, y, z]
        torb.mover.body = None
        run.frame += 1
        torb.take_commands([Command(run.frame, action=2)])
        for _ in range(45):
            run.step({})
        caster.mover.position = [x - 1.5, y, z - 2]
        caster.mover.body = None
        repair_aim = aim(fight._eye(torb), (x, y + .5, z))
        flash_aim = aim(fight._eye(caster), (x - 1.5, y + 1, z))
        run.step({torb: (*repair_aim, 1)})
        contact = fight.hammer_contacts[torb.body]
        self.assertLess(torb.body_script.component.now, contact.begin)
        run.step({torb: (*repair_aim, 1), caster: (*flash_aim, 16)})
        for _ in range(35):
            if fight.is_stunned(torb):
                break
            run.step({torb: (*repair_aim, 1)})
        self.assertTrue(fight.is_stunned(torb))
        self.assertEqual(hp.total, 105)  # Flashbang also deals 25 to the nearby turret.
        self.assertTrue(fight.is_stunned(turret.actor))
        end = fight.stuns[torb.body]
        self.assertEqual(fight.stuns[turret.actor.entity], end)
        shots = turret.shots
        while run.frame < end - 1:
            run.step({torb: (*repair_aim, 1)})
            self.assertEqual(hp.total, 105)
            self.assertEqual(turret.shots, shots)
        for _ in range(30):
            run.step({})
        self.assertFalse(fight.is_stunned(torb))
        self.assertEqual(hp.total, 105)
        self.assertIs(fight.turrets[torb.entity], turret)
        run.step({torb: (*repair_aim, 1)})
        for _ in range(35):
            run.step({})
            if hp.total > 105:
                break
        self.assertEqual(hp.total, 155)

    def test_existing_turret_and_ground_burn_continue_during_owner_stun(self):
        run, caster, target = self.arena()
        fight = run.match.combat
        x, y, z = target.mover.position
        turret = hero_torbjorn.deploy_turret(fight, target, (x + 6, y, z))
        for _ in range(190):
            self.step(run, caster, target)
        pool = molten_core.create_pool(fight, target, tuple(caster.mover.position))
        self.cast(run, caster, target)
        self.assertFalse(fight.is_stunned(turret.actor))
        shots, before = turret.shots, self.health(run, caster)
        end = fight.stuns[target.body]
        while run.frame < end - 1:
            self.step(run, caster, target, target_buttons=4)
        self.assertTrue(fight.is_stunned(target))
        self.assertGreater(turret.shots, shots)
        self.assertLess(self.health(run, caster), before)
        self.assertIn(pool.entity, fight.molten_pools)
        self.assertIn((target.entity, caster.body), fight.molten_burns)
        self.step(run, caster, target, target_buttons=4)
        self.assertNotIn(target.entity, fight.turrets)
        self.assertIn(pool.entity, fight.molten_pools)

    def test_flashbang_hit_on_turret_stops_fire_until_stun_expires(self):
        run, caster, torb = self.arena()
        fight = run.match.combat
        x, y, z = torb.mover.position
        caster.mover.position = [x + 100, y, z]
        caster.mover.body = None
        run.step({torb: (0, 4000, 8)})
        for _ in range(225):
            run.step({})
        turret = fight.turrets[torb.entity]
        turret_hp = fight.vitals[turret.actor.entity]
        self.assertEqual(turret_hp.total, 250)
        self.assertEqual(turret.shots, 0)
        x, y, z = turret.actor.position
        torb.mover.position = [x + 12, y, z]
        torb.mover.body = None
        caster.mover.position = [x, y, z + 3]
        caster.mover.body = None
        self.cast(run, caster, turret.actor)
        self.assertEqual(turret_hp.total, 225)
        self.assertFalse(fight.is_stunned(torb))
        self.assertEqual(self.health(run, torb), 250)
        deadline = fight.stuns[turret.actor.entity]
        shots = turret.shots
        caster_hp = self.health(run, caster)
        while run.frame < deadline - 1:
            self.step(run, caster, turret.actor)
            self.assertEqual(turret.shots, shots)
            self.assertEqual(self.health(run, caster), caster_hp)
        self.step(run, caster, turret.actor)
        self.assertFalse(fight.is_stunned(turret.actor))
        self.assertEqual(turret.shots, shots + 1)
        self.assertEqual(self.health(run, caster), caster_hp - 14)

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
