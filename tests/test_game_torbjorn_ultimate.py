"""Command-driven Molten Core release, timed end and owner-death evidence."""

import os
import unittest
from unittest import mock

import test_game_torbjorn_hammer as hammer_tests
import test_game_torbjorn_molten as area_tests
import test_game_torbjorn_resources as resource_tests
from test_game_combat import aim

from ow174.game import hero_torbjorn, molten_core, stats
from ow174.game.commands import Command


@unittest.skipUnless(os.environ.get("ORPHEUS_TORBJORN_GRAPH_BUNDLE"), "needs prepared053 bundle")
class TorbjornUltimateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        area_tests.TorbjornMoltenTests.setUpClass()

    @classmethod
    def tearDownClass(cls):
        area_tests.TorbjornMoltenTests.tearDownClass()

    def arena(self, *, ready=True):
        run, owner, victim, pool, hp = area_tests.TorbjornMoltenTests().arena()
        molten_core.end_pool(run.match.combat, pool)
        owner.body_script.set_ult_charge(10000.0)
        run.step({owner: (0, 4000, 32)})
        if ready:
            self.advance(run, owner, 45)
        return run, owner, victim, hp

    @staticmethod
    def advance(run, owner, ticks, buttons=0):
        for _ in range(ticks):
            run.step({owner: (0, 4000, buttons)})

    @staticmethod
    def launches(shoot):
        return [call for call in shoot.call_args_list
                if call.args[2].instance.graph.index == 0x16DA and call.args[2].index == 17]

    def test_release_stops_globules_and_repress_resumes_without_refilling(self):
        run, owner, _, _ = self.arena()
        fight = run.match.combat
        with mock.patch.object(fight, "_shoot", wraps=fight._shoot) as shoot:
            self.advance(run, owner, 1, 1)
            self.advance(run, owner, 40)
            self.assertEqual(len(self.launches(shoot)), 1)
            self.assertEqual(len(fight.molten_pools), 1)
            self.advance(run, owner, 1, 1)
            self.advance(run, owner, 40)
            self.assertEqual(len(self.launches(shoot)), 2)
            self.assertEqual(len(fight.molten_pools), 2)
            self.advance(run, owner, 250, 1)
            self.assertEqual(len(self.launches(shoot)), 10)

    def test_repeated_ultimate_button_without_firing_does_not_extend_timed_end(self):
        run, owner, _, _ = self.arena()
        fight = run.match.combat
        with mock.patch.object(fight, "_shoot", wraps=fight._shoot) as shoot:
            for _ in range(190):
                self.advance(run, owner, 1, 32)
                self.advance(run, owner, 1)
            self.assertEqual(self.launches(shoot), [])
            self.advance(run, owner, 80, 1 | 32)
            self.assertEqual(self.launches(shoot), [])
            self.assertEqual(fight.molten_pools, {})

    def test_molten_fires_from_empty_rivet_magazine_and_rivet_fire_recovers(self):
        resources = resource_tests.TorbjornResourceTests()
        run, owner, victim, weapon = resources.arena()
        fight = run.match.combat
        with mock.patch.object(fight, "_shoot", wraps=fight._shoot) as shoot:
            resources.advance(run, owner, 430, 2)
            self.assertEqual(len(resources.launches(shoot, weapon)), 9)
            self.assertEqual(weapon.vars[53].value(), 0)
            owner.body_script.set_ult_charge(10000)
            self.advance(run, owner, 1, 32)
            activated_at = run.frame
            self.advance(run, owner, 45)
            self.assertEqual(weapon.vars[53].value(), 0)
            run.step({owner: (16384, 4000, 1)})
            self.assertEqual(weapon.vars[53].value(), 18)
            self.assertEqual(len(self.launches(shoot)), 1)
            self.advance(run, owner, 55)
            self.assertEqual(len(self.launches(shoot)), 1)
            self.assertEqual(len(fight.molten_pools), 1)
            self.assertEqual(len(resources.launches(shoot, weapon)), 9)
            self.advance(run, owner, activated_at + 360 - run.frame)
            resources.advance(run, owner, 51, 2)
            self.assertEqual(run.frame - activated_at, 411)
            self.assertEqual(weapon.vars[53].value(), 18)
            self.assertEqual(len(resources.launches(shoot, weapon)), 9)
            self.assertEqual(len(self.launches(shoot)), 1)
            resources.advance(run, owner, 1, 2)
            self.assertEqual(len(resources.launches(shoot, weapon)), 10)
            self.assertEqual(weapon.vars[53].value(), 16)
            self.advance(run, owner, 50)
            self.assertEqual(fight.vitals[victim.body].total, 200)
            x, y, z = victim.mover.position
            direction = aim(fight._eye(owner), (x, y + .8, z))
            run.step({owner: (*direction, 1)})
            self.advance(run, owner, 30)
            self.assertEqual(len(resources.launches(shoot, weapon)), 11)
            self.assertEqual(weapon.vars[53].value(), 15)
            self.assertEqual(len(self.launches(shoot)), 1)
            self.assertEqual(fight.vitals[victim.body].total, 130)

    def test_molten_interrupts_manual_reload_and_refills_on_frame_46(self):
        for reload_age in (20, 70):
            for fire_globule in (False, True):
                with self.subTest(reload_age=reload_age, fire_globule=fire_globule):
                    resources = resource_tests.TorbjornResourceTests()
                    run, owner, victim, rifle = resources.arena()
                    fight = run.match.combat
                    target = next(item for item in fight._targets() if item.owner is victim)
                    victim_hp = fight.vitals_of(target)
                    ammo = rifle.vars[53]
                    charge = owner.body_script.component.entity_var(342)
                    resources.advance(run, owner, 1, 1)
                    resources.advance(run, owner, 50)
                    resources.advance(run, owner, 1, 0x400)
                    reload = rifle.state(8)
                    self.assertTrue(reload.active)
                    activation = reload.counter
                    resources.advance(run, owner, reload_age - 1)
                    retained = 17 if reload_age < 62 else 18
                    self.assertEqual(ammo.value(), retained)
                    owner.body_script.set_ult_charge(owner.body_script.component.entity_var(338).value())
                    self.advance(run, owner, 1, 32)
                    cast_frame = run.frame
                    self.assertEqual(charge.value(), 0)
                    self.assertFalse(reload.active)
                    self.assertEqual(reload.counter, activation)

                    with mock.patch.object(fight, "_shoot", wraps=fight._shoot) as shoot:
                        self.advance(run, owner, 45)
                        self.assertEqual(ammo.value(), retained)
                        self.assertFalse(reload.active)
                        self.assertFalse(rifle.state(25).active)
                        run.step({owner: (16384, 4000, 1 if fire_globule else 0)})
                        self.assertEqual(run.frame - cast_frame, 46)
                        self.assertEqual(ammo.value(), 18)
                        self.assertEqual(len(self.launches(shoot)), int(fire_globule))
                        self.advance(run, owner, cast_frame + 411 - run.frame)
                        self.assertEqual(len(self.launches(shoot)), int(fire_globule))
                        self.assertFalse(resources.launches(shoot, rifle))
                        self.assertEqual(len(fight.molten_pools), int(fire_globule))
                        self.assertEqual(ammo.value(), 18)
                        self.assertEqual(victim_hp.total, 200)
                        x, y, z = victim.mover.position
                        direction = aim(fight._eye(owner), (x, y + .8, z))
                        run.step({owner: (*direction, 1)})
                        self.assertEqual(len(resources.launches(shoot, rifle)), 1)
                        self.assertEqual(len(self.launches(shoot)), int(fire_globule))
                    self.advance(run, owner, 25)
                    self.assertEqual(victim_hp.total, 130)
                    self.assertEqual(ammo.value(), 17)
                    self.assertEqual(owner.stats.value(stats.HERO_DAMAGE), 70)


    def test_overload_startup_gates_molten_but_active_buff_does_not_change_globule_cadence(self):
        for scenario in ("baseline", "during_molten", "before_molten"):
            with self.subTest(scenario=scenario):
                run, owner, _, pool, _ = area_tests.TorbjornMoltenTests().arena()
                fight = run.match.combat
                molten_core.end_pool(fight, pool)
                owner.body_script.set_ult_charge(10000)
                charge = owner.body_script.component.entity_var(342)
                hp = fight.vitals[owner.body]
                if scenario == "before_molten":
                    self.advance(run, owner, 1, 16)
                    self.assertEqual(hp.over_armour.amount, 100)
                    self.advance(run, owner, 1, 32)
                    self.assertEqual(charge.value(), 10000)
                    self.advance(run, owner, 45)
                self.advance(run, owner, 1, 32)
                self.assertEqual(charge.value(), 0)
                self.advance(run, owner, 45)
                start = run.frame
                with mock.patch.object(fight, "_shoot", wraps=fight._shoot) as shoot:
                    for frame in range(130):
                        buttons = 17 if scenario == "during_molten" and frame == 0 else 1
                        run.step({owner: (16384, 4000, buttons)})
                    launch_frames = [call.args[4] // 16 - start for call in self.launches(shoot)]
                self.assertEqual(launch_frames, [1, 11, 21, 30, 40, 49, 59, 69, 78, 88])
                self.assertEqual(len(fight.molten_pools), 10)
                if scenario == "before_molten":
                    self.assertEqual(hp.over_armour.amount, 100)
                    self.advance(run, owner, 100)
                    self.assertEqual(hp.over_armour.amount, 0)
                    self.assertGreater(hero_torbjorn.overload_cooldown_remaining(owner), 0)
                else:
                    self.assertEqual(hp.over_armour.amount, 0)
                    self.assertEqual(hero_torbjorn.overload_cooldown_remaining(owner), 0)

    def test_molten_blocks_new_turret_input_but_existing_turret_keeps_firing(self):
        for deployed in (False, True):
            with self.subTest(deployed=deployed):
                run, owner, victim, pool, victim_hp = area_tests.TorbjornMoltenTests().arena()
                fight = run.match.combat
                molten_core.end_pool(fight, pool)
                x, y, z = owner.mover.position
                victim.mover.position = [x + 100, y, z + 100]
                victim.mover.body = None
                if deployed:
                    self.advance(run, owner, 1, 8)
                    self.advance(run, owner, 240)
                    turret = fight.turrets[owner.entity]
                    self.assertEqual(turret.shots, 0)
                owner.body_script.set_ult_charge(10000)
                self.advance(run, owner, 1, 32)
                self.advance(run, owner, 45)
                if deployed:
                    tx, ty, tz = turret.actor.position
                    victim.mover.position = [tx, ty, tz + 8]
                    victim.mover.body = None
                else:
                    self.assertEqual(hero_torbjorn.turret_cooldown_remaining(owner), 0)
                run.step({owner: (16384, 4000, 1 | 8)})
                self.advance(run, owner, 55)
                self.assertEqual(len(fight.molten_pools), 1)
                if deployed:
                    self.assertIs(fight.turrets[owner.entity], turret)
                    self.assertGreater(turret.shots, 0)
                    self.assertEqual(200 - victim_hp.total, turret.shots * 14)
                    self.assertEqual(owner.stats.value(stats.HERO_DAMAGE), turret.shots * 14)
                else:
                    self.assertFalse(fight.turrets)
                    self.assertEqual(hero_torbjorn.turret_cooldown_remaining(owner), 0)
                    self.advance(run, owner, 350)
                    self.advance(run, owner, 1, 8)
                    self.advance(run, owner, 240)
                    self.assertIn(owner.entity, fight.turrets)

    def test_molten_preserves_turret_throw_in_windup_and_in_flight(self):
        for delay in (1, 10):
            with self.subTest(molten_input_after_throw_frames=delay):
                run, owner, victim, pool, hp = area_tests.TorbjornMoltenTests().arena()
                fight = run.match.combat
                molten_core.end_pool(fight, pool)
                x, y, z = owner.mover.position
                victim.mover.position = [x + 100, y, z + 100]
                victim.mover.body = None
                owner.body_script.set_ult_charge(10000)
                run.step({owner: (16384, -4500, 8)})
                self.advance(run, owner, delay)
                flights = [rocket for rocket in fight.rockets if rocket.spawn == hero_torbjorn.TURRET]
                self.assertEqual(len(flights), int(delay == 10))
                self.assertFalse(fight.turrets)
                before = hero_torbjorn.turret_cooldown_remaining(owner)
                self.assertGreater(before, 0)
                self.advance(run, owner, 1, 32)
                self.assertEqual(owner.body_script.component.entity_var(342).value(), 0)
                after = hero_torbjorn.turret_cooldown_remaining(owner)
                self.assertAlmostEqual(after, before - .016, places=4)
                if flights:
                    self.assertIn(flights[0], fight.rockets)
                self.advance(run, owner, 45)
                turret, = fight.turrets.values()
                self.assertIs(turret.actor.combat_owner, owner)
                self.assertEqual(turret.shots, 0)
                run.step({owner: (16384, 4000, 1)})
                self.advance(run, owner, 190)
                self.assertEqual(len(fight.molten_pools), 1)
                self.assertEqual(fight.vitals[turret.actor.entity].total, 250)
                tx, ty, tz = turret.actor.position
                victim.mover.position = [tx, ty, tz + 10]
                victim.mover.body = None
                for _ in range(20):
                    if turret.shots:
                        break
                    self.advance(run, owner, 1)
                self.assertEqual(turret.shots, 1)
                self.assertEqual(hp.total, 186)
                self.assertEqual(owner.stats.value(stats.HERO_DAMAGE), 14)
                self.assertEqual(list(fight.turrets.values()), [turret])

    def test_held_quick_melee_waits_for_molten_end_without_spending_a_globule(self):
        run, owner, victim, hp = self.arena(ready=False)
        fight = run.match.combat
        activated_at = run.frame
        self.advance(run, owner, 45)
        x, y, z = owner.mover.position
        victim.mover.position = [x, y, z + 1.5]
        victim.mover.body = None
        self.assertEqual(hp.total, 200)
        with mock.patch.object(fight, "_shoot", wraps=fight._shoot) as shoot:
            for _ in range(366):
                run.step({owner: (0, 0, 0x800)})
                self.assertEqual(hp.total, 200)
            self.assertEqual(run.frame - activated_at, 411)
            self.assertEqual(owner.stats.value(stats.HERO_DAMAGE), 0)
            run.step({owner: (0, 0, 0x800)})
            self.assertEqual(run.frame - activated_at, 412)
            self.assertEqual(hp.total, 170)
            self.assertEqual(owner.stats.value(stats.HERO_DAMAGE), 30)
            self.assertFalse(shoot.call_args_list)
        self.assertFalse(fight.molten_pools)

    def test_molten_start_cancels_pending_hammer_contact(self):
        run, owner, victim, hp = hammer_tests.TorbjornHammerTests().arena()
        fight = run.match.combat
        owner.body_script.set_ult_charge(10000)
        for _ in range(5):
            run.step({owner: (0, 0, 1)})
        contact = fight.hammer_contacts[owner.body]
        self.assertLess(owner.body_script.component.now, contact.begin)
        self.assertEqual(hp.total, 200)
        run.step({owner: (0, 0, 32)})
        self.advance(run, owner, 45)
        self.assertNotIn(owner.body, fight.hammer_contacts)
        self.assertEqual(hp.total, 200)
        x, y, z = victim.mover.position
        victim.mover.position = [x + 100, y, z + 100]
        victim.mover.body = None
        run.step({owner: (-16384, 4000, 1)})
        self.advance(run, owner, 55)
        self.assertEqual(len(fight.molten_pools), 1)
        self.assertEqual(hp.total, 200)

    def test_molten_blocks_hammer_repair_until_weapon_recovers(self):
        run, owner, victim, pool, _ = area_tests.TorbjornMoltenTests().arena()
        fight = run.match.combat
        molten_core.end_pool(fight, pool)
        x, y, z = owner.mover.position
        victim.mover.position = [x + 100, y, z + 100]
        victim.mover.body = None
        run.step({owner: (0, 4000, 8)})
        self.advance(run, owner, 240)
        turret = fight.turrets[owner.entity]
        x, y, z = turret.actor.position
        turret_hp = fight.vitals[turret.actor.entity]
        self.assertEqual(turret_hp.total, 250)
        victim.mover.position = [x, y, z + 6]
        victim.mover.body = None
        for _ in range(70):
            run.step({victim: (*aim(fight._eye(victim), (x, y + .4, z)), 1)})
            if turret_hp.total <= 200:
                break
        damaged = turret_hp.total
        self.assertLessEqual(damaged, 200)
        self.assertGreater(damaged, 0)
        victim.mover.position = [x + 100, y, z + 100]
        victim.mover.body = None
        owner.mover.position = [x - 1.6, y, z]
        owner.mover.body = None
        run.frame += 1
        owner.take_commands([Command(run.frame, action=2)])
        self.advance(run, owner, 45)
        owner.body_script.set_ult_charge(10000.0)
        run.step({owner: (16384, 4000, 32)})
        self.advance(run, owner, 45)
        run.frame += 1
        owner.take_commands([Command(run.frame, action=2)])
        repair_aim = aim(fight._eye(owner), (x, y + .4, z))
        run.step({owner: (*repair_aim, 1)})
        self.advance(run, owner, 55)
        self.assertEqual(turret_hp.total, damaged)
        self.assertEqual(len(fight.molten_pools), 1)
        self.advance(run, owner, 350)
        run.frame += 1
        owner.take_commands([Command(run.frame, action=2)])
        self.advance(run, owner, 45)
        run.step({owner: (*repair_aim, 1)})
        self.advance(run, owner, 45)
        self.assertEqual(turret_hp.total, damaged + 50)
        self.assertEqual(len(fight.molten_pools), 1)

    def test_death_cancels_future_globules_but_existing_pool_still_damages(self):
        for startup in (True, False):
            with self.subTest(startup=startup):
                run, owner, victim, hp = self.arena(ready=not startup)
                fight = run.match.combat
                with mock.patch.object(fight, "_shoot", wraps=fight._shoot) as shoot:
                    if not startup:
                        self.advance(run, owner, 1, 1)
                        self.advance(run, owner, 40)
                        self.assertEqual(len(fight.molten_pools), 1)
                        pool = next(iter(fight.molten_pools.values()))
                        victim.mover.position = list(pool.position)
                    target = next(t for t in fight._targets() if t.owner is owner)
                    fight._damage(victim, target, 1000.0, False, None, run.frame)
                    self.assertTrue(fight.vitals[owner.body].dead)
                    before = len(self.launches(shoot))
                    health = hp.total
                    self.advance(run, owner, 80, 1 | 32)
                    self.assertEqual(len(self.launches(shoot)), before)
                    if startup:
                        self.assertEqual(fight.molten_pools, {})
                    else:
                        self.assertIn(pool.entity, fight.molten_pools)
                        self.assertLess(hp.total, health)
