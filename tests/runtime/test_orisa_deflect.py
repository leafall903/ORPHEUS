"""Real Genji Deflect inputs against Fusion Driver projectiles."""

import math
import os
import unittest
from dataclasses import replace

import test_orisa_barrier as barrier_fixture
import test_orisa_combat as fixture
from test_game_combat import SOLDIER, in_world

from ow174.game import combat, deflection, wraith
from ow174.game.commands import FIRE
from ow174.game.content import find_hero, heroes


class OrisaDeflectTests(unittest.TestCase):
    def arena(self):
        helper = fixture.OrisaCombatTests()
        run, orisa, genji = helper.arena()
        self.assertTrue(run.match.switch_hero(genji, heroes()[0x02E0000000000029]))
        x, y, z = orisa.mover.position
        genji.mover.position = [x, y, z + 5]
        orisa.body_script.ult_seconds = 0
        run.step({})
        return helper, run, orisa, genji

    def test_actual_deflect_returns_fusion_round_with_reflector_credit(self):
        for fortified, damage in ((False, 17), (True, 3.3)):
            with self.subTest(fortified=fortified):
                helper, run, orisa, genji = self.arena()
                fight = run.match.combat
                health = fight.vitals_of(helper.target(run, orisa))
                enemy = fight.vitals_of(helper.target(run, genji))
                before, enemy_before = health.total, enemy.total
                run.step({genji: (-32768, 0, 16), orisa: (0, 0, 8 if fortified else 0)})
                run.step({genji: (-32768, 0, 0), orisa: (0, 0, FIRE)})
                rocket, = fight.rockets
                for _ in range(12):
                    run.step({genji: (-32768, 0, 0)})
                self.assertTrue(rocket.reflected)
                self.assertIs(rocket.shooter, genji)
                self.assertEqual(rocket.source_body, genji.body)
                self.assertEqual(rocket.ended, "body")
                self.assertAlmostEqual(before - health.total, damage, places=5)
                self.assertEqual(enemy.total, enemy_before)
                self.assertEqual(orisa.body_script.component.entity_var(342).value(), 0)
                self.assertTrue(genji.client.messages(combat.HIT))
                self.assertFalse(orisa.client.messages(combat.HIT))

    def test_front_quick_melee_recovers_after_deflect_filter_ends(self):
        helper, run, orisa, genji = self.arena()
        x, y, z = orisa.mover.position
        barrier_fixture.BarrierTests.place(genji, (x, y, z + 1.5))
        fight = run.match.combat
        health = fight.vitals_of(helper.target(run, genji))
        before = health.total
        facing = (-32768, 0, 0)
        run.step({genji: (-32768, 0, 16)})
        instance, = (i for i in genji.body_script.component.instances.values()
                     if i.graph.index == 0x03A3)
        filter_state = instance.states[instance.graph.node(107).state]
        self.assertTrue(filter_state.active)
        run.step({genji: facing, orisa: (0, 0, 0x800)})
        for _ in range(25):
            run.step({genji: facing})
        self.assertTrue(filter_state.active)
        self.assertEqual(health.total, before)
        self.assertFalse(orisa.client.messages(combat.HIT))

        for _ in range(135):
            run.step({genji: facing})
            if not filter_state.active:
                break
        self.assertFalse(filter_state.active)
        for _ in range(25):
            run.step({genji: facing})
        self.assertEqual(health.total, before)
        self.assertFalse(orisa.client.messages(combat.HIT))

        run.step({genji: facing, orisa: (0, 0, 0x800)})
        for _ in range(25):
            run.step({genji: facing})
        self.assertEqual(before - health.total, 30)
        self.assertEqual(len(orisa.client.messages(combat.HIT)), 1)
        self.assertFalse(genji.client.messages(combat.HIT))

    def test_flashbang_interrupts_front_melee_protection(self):
        helper, run, orisa, genji = self.arena()
        run.match.game_map = replace(run.match.game_map, free_for_all=False)
        caster = run.match.add_player(3, "Ally McCree", SOLDIER, 2, False)
        in_world(run.match, caster)
        run.players.append(caster)
        self.assertTrue(run.match.switch_hero(caster, heroes()[0x02E0000000000042]))
        caster.team = orisa.team
        x, y, z = orisa.mover.position
        barrier_fixture.BarrierTests.place(genji, (x, y, z + 1.5))
        barrier_fixture.BarrierTests.place(caster, (x, y, z + 4.5))
        fight = run.match.combat
        health = fight.vitals_of(helper.target(run, genji))
        before = health.total
        facing = (-32768, 0, 0)
        activation_frame = run.frame
        run.step({genji: (-32768, 0, 16)})
        instance, = (i for i in genji.body_script.component.instances.values()
                     if i.graph.index == 0x03A3)
        filter_state = instance.states[instance.graph.node(107).state]
        self.assertTrue(filter_state.active)
        run.step({genji: facing, caster: (-32768, 0, 16)})
        for _ in range(50):
            if fight.is_stunned(genji):
                break
            run.step({genji: facing, caster: facing})
        self.assertTrue(fight.is_stunned(genji))
        self.assertEqual(before - health.total, 25)
        self.assertFalse(filter_state.active)
        after_flash = health.total
        run.step({genji: facing, orisa: (0, 0, 0x800)})
        for _ in range(25):
            if health.total < after_flash:
                break
            run.step({genji: facing})
        self.assertTrue(fight.is_stunned(genji))
        self.assertEqual(after_flash - health.total, 30)
        self.assertEqual(len(orisa.client.messages(combat.HIT)), 1)

        while fight.is_stunned(genji):
            run.step({genji: facing})
        for _ in range(25):
            run.step({genji: facing})
        self.assertLess(run.frame - activation_frame, 125)
        self.assertFalse(filter_state.active)
        before_recovery_hit = health.total
        run.step({genji: facing, orisa: (0, 0, 0x800)})
        for _ in range(25):
            if health.total < before_recovery_hit:
                break
            run.step({genji: facing})
        self.assertLess(run.frame - activation_frame, 125)
        self.assertEqual(before_recovery_hit - health.total, 30)
        self.assertEqual(len(orisa.client.messages(combat.HIT)), 2)

    def test_reflected_round_passes_wraith_and_keeps_genji_credit(self):
        for intangible in (False, True):
            with self.subTest(intangible=intangible):
                helper, run, orisa, genji = self.arena()
                run.match.game_map = replace(run.match.game_map, free_for_all=False)
                reaper = run.match.add_player(3, "Reaper", SOLDIER, 2, False)
                in_world(run.match, reaper)
                run.players.append(reaper)
                run.match.switch_hero(reaper, find_hero("Reaper"))
                reaper.team = orisa.team
                x, y, z = orisa.mover.position
                barrier_fixture.BarrierTests.place(reaper, (x, y, z + 2.5))
                fight = run.match.combat
                orisa_health = fight.vitals_of(helper.target(run, orisa))
                reaper_health = fight.vitals_of(helper.target(run, reaper))
                before = orisa_health.total, reaper_health.total

                run.step({genji: (-32768, 0, 16), reaper: (0, 0, 8 if intangible else 0)})
                self.assertEqual(wraith.intangible(reaper), intangible)
                run.step({genji: (-32768, 0, 0), orisa: (0, 0, FIRE)})
                rocket, = fight.rockets
                for _ in range(12):
                    run.step({genji: (-32768, 0, 0)})
                self.assertTrue(rocket.reflected)
                self.assertIs(rocket.shooter, genji)
                self.assertEqual(rocket.source_body, genji.body)
                self.assertEqual(rocket.ended, "body")
                self.assertEqual(wraith.intangible(reaper), intangible)
                self.assertEqual(before[0] - orisa_health.total, 17 if intangible else 0)
                self.assertEqual(before[1] - reaper_health.total, 0 if intangible else 22)
                self.assertEqual(len(genji.client.messages(combat.HIT)), 1)
                self.assertFalse(orisa.client.messages(combat.HIT))

    @unittest.skipUnless(os.environ.get("ORPHEUS_ORISA_GEOMETRY"), "actual barrier mesh required")
    @unittest.skipUnless(os.environ.get("ORPHEUS_ORISA_SUPERCHARGER"), "authored device required")
    def test_deflect_does_not_intercept_orisa_deployable_projectiles(self):
        for button, graph, name, health in (
            (16, 0xBD7, "barriers", 600),
            (32, 0x0CF5, "superchargers", 200),
        ):
            with self.subTest(deployable=name):
                _, run, orisa, genji = self.arena()
                fight = run.match.combat
                manager = getattr(fight.orisa, name)
                if button == 32:
                    x, y, z = orisa.mover.position
                    barrier_fixture.BarrierTests.place(genji, (x + 0.75, y, z + 1.25))
                    orisa.body_script.set_ult_charge(orisa.body_script.component.entity_var(338).value())
                run.step({genji: (-32768, 0, 16)})
                rocket = None
                crosses_deflect = False
                for frame in range(120):
                    previous = rocket.position if rocket is not None else None
                    run.step({genji: (-32768, 0, 0), orisa: (0, 0, button if frame == 0 else 0)})
                    rocket = next((r for r in fight.rockets if r.source_graph == graph), rocket)
                    if rocket is not None and previous is not None:
                        distance = math.dist(previous, rocket.position)
                        if distance:
                            direction = tuple((b - a) / distance for a, b in zip(previous, rocket.position))
                            # Reclassify only the diagnostic copy to evaluate the active boxes.
                            sample = replace(rocket, position=previous, source_graph=0xBD4)
                            hit = deflection.interception(fight, sample, direction, distance)
                            crosses_deflect |= hit is not None and hit[1] is genji
                    if manager.active:
                        break
                self.assertTrue(crosses_deflect, "flight never crossed an active Deflect box")
                deployed, = manager.active.values()
                self.assertEqual(rocket.ended, "world")
                self.assertFalse(rocket.reflected)
                self.assertIs(rocket.shooter, orisa)
                self.assertEqual(deployed.source_body, orisa.body)
                self.assertEqual(deployed.health, health)
                self.assertEqual(deployed.position, rocket.position)

    def deploy_supercharger(self, run, team, position):
        caster = run.match.add_player(3, "Supercharger owner", SOLDIER, 2, False)
        in_world(run.match, caster)
        run.players.append(caster)
        run.match.switch_hero(caster, heroes()[fixture.ORISA])
        caster.team = team
        barrier_fixture.BarrierTests.place(caster, position)
        caster.body_script.ult_seconds = 0
        caster.body_script.set_ult_charge(caster.body_script.component.entity_var(338).value())
        fight = run.match.combat
        manager = fight.orisa.superchargers
        for frame in range(70):
            run.step({caster: (0, 0, 32 if frame == 0 else 0)})
            if manager.active:
                break
        self.assertEqual(len(manager.active), 1)
        return manager

    @unittest.skipUnless(os.environ.get("ORPHEUS_ORISA_SUPERCHARGER"), "authored device required")
    def test_returned_round_uses_reflector_supercharger_instead_of_original_shooter(self):
        for boost_reflector, fortified, expected in (
            (False, False, 17), (True, False, 28),
            (False, True, 3.3), (True, True, 4.95),
        ):
            with self.subTest(boost_reflector=boost_reflector, fortified=fortified):
                helper, run, orisa, genji = self.arena()
                run.match.game_map = replace(run.match.game_map, free_for_all=False)
                x, y, z = orisa.mover.position
                team = genji.team if boost_reflector else orisa.team
                manager = self.deploy_supercharger(run, team, (x + 6, y, z))
                fight = run.match.combat
                self.assertEqual(manager.scale(orisa), 1 if boost_reflector else 1.5)
                self.assertEqual(manager.scale(genji), 1.5 if boost_reflector else 1)

                health = fight.vitals_of(helper.target(run, orisa))
                before = health.total
                run.step({genji: (-32768, 0, 16), orisa: (0, 0, 8 if fortified else 0)})
                run.step({genji: (-32768, 0, 0), orisa: (0, 0, FIRE)})
                rocket, = (r for r in fight.rockets if r.source_graph == 0xBD4)
                for _ in range(12):
                    run.step({genji: (-32768, 0, 0)})
                self.assertTrue(rocket.reflected)
                self.assertIs(rocket.shooter, genji)
                self.assertEqual(rocket.ended, "body")
                self.assertAlmostEqual(before - health.total, expected, places=5)
                hit = genji.client.messages(combat.HIT)[-1]
                self.assertEqual(bool(hit["+0x78"]["+0x8"] & combat.CRITICAL), not fortified)
                self.assertFalse(orisa.client.messages(combat.HIT))

    @unittest.skipUnless(os.environ.get("ORPHEUS_ORISA_GEOMETRY"), "actual barrier mesh required")
    def test_own_barrier_passes_outgoing_round_but_stops_genji_return(self):
        self.check_barrier_intercepts_return(boosted=False)

    @unittest.skipUnless(os.environ.get("ORPHEUS_ORISA_GEOMETRY"), "actual barrier mesh required")
    @unittest.skipUnless(os.environ.get("ORPHEUS_ORISA_SUPERCHARGER"), "authored device required")
    def test_supercharger_boosts_genji_return_damage_to_protective_barrier(self):
        self.check_barrier_intercepts_return(boosted=True)

    def check_barrier_intercepts_return(self, *, boosted):
        run, orisa, genji, _, shield = barrier_fixture.BarrierTests().deploy()
        run.match.game_map = replace(run.match.game_map, free_for_all=False)
        run.match.switch_hero(genji, heroes()[0x02E0000000000029])
        x, y, z = shield.position
        barrier_fixture.BarrierTests.place(genji, (x, y, z + 5))
        if boosted:
            manager = self.deploy_supercharger(run, genji.team, (x + 6, y, z + 5))
            self.assertEqual(manager.scale(genji), 1.5)
            self.assertEqual(manager.scale(orisa), 1)
        fight = run.match.combat
        helper = fixture.OrisaCombatTests()
        orisa_vitals = fight.vitals_of(helper.target(run, orisa))
        genji_vitals = fight.vitals_of(helper.target(run, genji))
        health = orisa_vitals.total, genji_vitals.total
        run.step({genji: (-32768, 0, 16)})
        run.step({genji: (-32768, 0, 0), orisa: (0, 0, FIRE)})
        rocket, = (r for r in fight.rockets if r.source_graph == 0xBD4)
        for _ in range(30):
            run.step({genji: (-32768, 0, 0)})
            if rocket not in fight.rockets:
                break
        self.assertTrue(rocket.reflected)
        self.assertIs(rocket.shooter, genji)
        self.assertEqual(rocket.ended, "barrier")
        self.assertEqual(600 - shield.health, 16.5 if boosted else 11)
        self.assertEqual((orisa_vitals.total, genji_vitals.total), health)
        self.assertFalse(orisa.client.messages(combat.HIT))
        self.assertFalse(genji.client.messages(combat.HIT))

    def test_reflector_aim_can_return_a_round_that_misses(self):
        helper, run, orisa, genji = self.arena()
        fight = run.match.combat
        health = fight.vitals_of(helper.target(run, orisa))
        before = health.total
        yaw = -32768 + 4096
        run.step({genji: (yaw, 0, 16)})
        run.step({genji: (yaw, 0, 0), orisa: (0, 0, FIRE)})
        rocket, = fight.rockets
        for _ in range(12):
            run.step({genji: (yaw, 0, 0)})
        self.assertTrue(rocket.reflected)
        self.assertEqual(rocket.launch_direction, combat.aim(yaw, 0))
        self.assertEqual(health.total, before)

    def test_back_attack_and_expired_deflect_leave_original_projectile(self):
        for expired in (False, True):
            with self.subTest(expired=expired):
                helper, run, orisa, genji = self.arena()
                fight = run.match.combat
                yaw = -32768 if expired else 0
                run.step({genji: (yaw, 0, 16)})
                if expired:
                    for _ in range(135):
                        run.step({genji: (yaw, 0, 0)})
                enemy = fight.vitals_of(helper.target(run, genji))
                before = enemy.total
                run.step({genji: (yaw, 0, 0), orisa: (0, 0, FIRE)})
                rocket, = fight.rockets
                for _ in range(7):
                    run.step({genji: (yaw, 0, 0)})
                self.assertFalse(rocket.reflected)
                self.assertLess(enemy.total, before)

    def test_cover_and_friendly_team_prevent_reflection(self):
        for ally in (False, True):
            with self.subTest(ally=ally):
                helper, run, orisa, genji = self.arena()
                if ally:
                    run.match.game_map = replace(run.match.game_map, free_for_all=False)
                    genji.team = orisa.team
                else:
                    helper.wall(run, orisa, 2)
                fight = run.match.combat
                enemy = fight.vitals_of(helper.target(run, genji))
                before = enemy.total
                run.step({genji: (-32768, 0, 16)})
                run.step({genji: (-32768, 0, 0), orisa: (0, 0, FIRE)})
                rocket, = fight.rockets
                for _ in range(8):
                    run.step({genji: (-32768, 0, 0)})
                self.assertFalse(rocket.reflected)
                self.assertEqual(enemy.total, before)
                if not ally:
                    self.assertEqual(rocket.ended, "world")

    def test_reflector_body_replacement_retires_the_returned_round(self):
        _, run, orisa, genji = self.arena()
        fight = run.match.combat
        run.step({genji: (-32768, 0, 16)})
        run.step({genji: (-32768, 0, 0), orisa: (0, 0, FIRE)})
        rocket, = fight.rockets
        for _ in range(5):
            run.step({genji: (-32768, 0, 0)})
            if rocket.reflected:
                break
        self.assertTrue(rocket.reflected)
        self.assertIn(rocket, fight.rockets)
        self.assertTrue(run.match.switch_hero(genji, genji.hero))
        self.assertNotIn(rocket, fight.rockets)
