"""Executed effective-charge reconstruction; native coefficients remain open."""

import unittest
from dataclasses import replace
from unittest.mock import patch

import test_mei_blizzard as blizzard
import test_mei_combat as fixtures
import test_mei_cryo as cryo
import test_mei_deflect as deflect
import test_mei_shields as shields
import test_mei_wall as walls

from ow174.game import combat, mei_ultimate
from ow174.game.content import SOLDIER, heroes
from ow174.game.script import rulesets


class UltimateTests(unittest.TestCase):
    def custom(self, settings):
        original = rulesets.default

        def value(key, hero, mode):
            return settings[key] if hero == fixtures.MEI and key in settings else original(key, hero, mode)

        override = patch.object(rulesets, 'default', side_effect=value)
        override.start()
        self.addCleanup(override.stop)

    def charge(self, actor):
        return actor.body_script.component.entity_var(342).value()

    def reset(self, actor):
        actor.body_script.ult_seconds = 0
        actor.body_script.set_ult_charge(0.)

    def test_default_passive_rate_fills_points_and_caps_at_authored_cost(self):
        run, mei, _enemy, _target = fixtures.MeiCombatTests().duel()
        self.assertEqual(mei.body_script.ult_seconds, mei_ultimate.DEFAULT_SECONDS)
        mei.body_script.set_ult_charge(0.)
        for _ in range(125):
            run.step({})
        self.assertAlmostEqual(self.charge(mei), 10., places=4)
        mei.body_script.set_ult_charge(1609.9)
        for _ in range(5):
            run.step({})
        self.assertEqual(self.charge(mei), 1610.)

    def test_actual_weapons_credit_effective_enemy_damage(self):
        for button, distance, height in ((2, 5., .9), (2, 5., 1.55), (1, 5., .9), (0x800, 1.5, .9)):
            with self.subTest(button=button, height=height):
                run, mei, enemy, _target = fixtures.MeiCombatTests().duel()
                p = mei.mover.position
                run.match.combat.walls.place_player(enemy, (p[0], p[1], p[2] + distance))
                target = next(t for t in run.match.combat._targets() if t.owner is enemy)
                self.reset(mei)
                fixtures.MeiCombatTests().fire(run, mei, target, button, height)
                effective = 200. - run.match.combat.vitals_of(target).total
                self.assertGreater(effective, 0.)
                self.assertAlmostEqual(self.charge(mei), effective, places=5)

    def test_overkill_and_dead_target_cannot_duplicate_damage_credit(self):
        run, mei, _enemy, target = fixtures.MeiCombatTests().duel()
        self.reset(mei)
        run.match.combat.vitals_of(target).now[combat.HEALTH_POOL] = 12.
        fixtures.MeiCombatTests().fire(run, mei, target, 2, .9)
        self.assertEqual(self.charge(mei), 12.)
        run.match.combat._damage(mei, target, 1000., False, None, run.frame)
        self.assertEqual(self.charge(mei), 12.)

    def test_allied_hits_and_misses_give_no_credit(self):
        run, mei, enemy, target = fixtures.MeiCombatTests().duel()
        run.match.game_map = replace(run.match.game_map, free_for_all=False)
        enemy.team = mei.team
        self.reset(mei)
        fixtures.MeiCombatTests().fire(run, mei, target, 2, .9)
        self.assertEqual(self.charge(mei), 0.)
        enemy.team += 1
        for _ in range(45):
            run.step({mei: (16384, 0, 2)})
        self.assertEqual(self.charge(mei), 0.)

    def test_personal_shield_damage_gives_no_health_credit(self):
        run, mei, zarya, _other = shields.ShieldTests().scene()
        self.reset(mei)
        bubble = shields.ShieldTests().cast(run, zarya)
        target = shields.ShieldTests().target(run, zarya)
        fixtures.MeiCombatTests().fire(run, mei, target, 2, .9)
        self.assertEqual(bubble.health, 125.)
        self.assertEqual(self.charge(mei), 0.)

    def test_reflected_icicle_cannot_credit_original_mei(self):
        test = deflect.DeflectTests()
        run, mei, _genji, target = test.scene()
        self.reset(mei)
        test.fire(run, mei, target, 2, .9)
        self.assertLess(test.health(run, mei), 250.)
        self.assertEqual(self.charge(mei), 0.)

    def launch_distant_icicle(self, *, allied_at_launch=None):
        run, mei, enemy, _target = fixtures.MeiCombatTests().duel()
        fight = run.match.combat
        if allied_at_launch is not None:
            run.match.game_map = replace(run.match.game_map, free_for_all=False)
            mei.team, enemy.team = 0, int(not allied_at_launch)
        x, y, z = mei.mover.position
        fight.walls.place_player(enemy, (x, y, z + 40.))
        for _ in range(20):
            run.step({})
        target = next(t for t in fight._targets() if t.owner is enemy)
        self.reset(mei)
        mei.body_script.set_ult_charge(100.)
        attack = fixtures.aim(fight._eye(mei), (x, y + .9, z + 40.))
        for frame in range(45):
            run.step({mei: (*attack, 2 if frame < 20 else 0)})
            if fight.rockets:
                break
        rocket, = fight.rockets
        self.assertIs(rocket.shooter, mei)
        self.assertEqual(fight.vitals_of(target).total, 200.)
        return run, mei, enemy, target, rocket

    def test_in_flight_icicle_hits_after_mei_dies_without_giving_charge(self):
        run, mei, enemy, target, rocket = self.launch_distant_icicle()
        fight = run.match.combat
        own = next(t for t in fight._targets() if t.owner is mei)
        fight.vitals_of(own).damage(249.)  # Low-health fixture; the rifle delivers the kill.
        rifle = fixtures.aim(fight._eye(enemy), fight._eye(mei))
        run.step({enemy: (*rifle, 1)})
        self.assertTrue(fight.is_dead(mei))
        self.assertIn(rocket, fight.rockets)
        self.assertEqual(fight.vitals_of(target).total, 200.)
        for _ in range(45):
            run.step({})
        self.assertNotIn(rocket, fight.rockets)
        self.assertEqual(fight.vitals_of(target).total, 125.)
        self.assertEqual(self.charge(mei), 100.)

    def test_hero_switch_retires_unreflected_icicle_before_impact(self):
        run, mei, _enemy, target, rocket = self.launch_distant_icicle()
        fight = run.match.combat
        old_body = mei.body
        self.assertIsNotNone(rocket.shot)
        self.assertIn(rocket.shot, fight.projectiles.shots)
        self.assertTrue(run.match.switch_hero(mei, heroes()[SOLDIER]))
        self.assertNotEqual(mei.body, old_body)
        self.assertNotIn(rocket, fight.rockets)
        self.reset(mei)
        run.step({})
        self.assertNotIn(rocket.shot, fight.projectiles.shots)
        self.assertTrue(rocket.shot.observers)
        self.assertTrue(all(view.finished and not view.explode for view in rocket.shot.observers))
        for _ in range(45):
            run.step({})
        self.assertEqual(fight.vitals_of(target).total, 200.)
        self.assertEqual(mei.client.messages(combat.HIT), [])
        self.assertEqual(self.charge(mei), 0.)

    def test_in_flight_icicle_uses_replacement_target_body_and_position(self):
        for stays_in_path in (False, True):
            with self.subTest(stays_in_path=stays_in_path):
                run, mei, enemy, target, rocket = self.launch_distant_icicle()
                fight = run.match.combat
                old_body = enemy.body
                x, y, z = target.feet
                enemy.spawn = ((x if stays_in_path else x + 5., y, z), enemy.spawn[1])
                self.assertTrue(run.match.switch_hero(enemy, enemy.hero))
                self.assertNotEqual(enemy.body, old_body)
                self.assertNotIn(old_body, fight.vitals)
                self.assertIn(rocket, fight.rockets)
                replacement = next(t for t in fight._targets() if t.owner is enemy)
                self.assertEqual(replacement.entity, enemy.body)
                for _ in range(70):
                    run.step({})
                self.assertNotIn(rocket, fight.rockets)
                self.assertNotIn(old_body, fight.vitals)
                damage = 75. if stays_in_path else 0.
                self.assertEqual(fight.vitals_of(replacement).total, 200. - damage)
                self.assertEqual(self.charge(mei), 100. + damage)
                hits = mei.client.messages(combat.HIT)
                self.assertEqual(len(hits), int(stays_in_path))
                if hits:
                    self.assertEqual(hits[0]["+0x78"]["+0x0"]["+0x0"], replacement.entity)

    def test_in_flight_icicle_uses_current_team_for_damage_and_charge(self):
        for allied_at_launch in (False, True):
            for switch_shooter in (False, True):
                with self.subTest(allied_at_launch=allied_at_launch, switch_shooter=switch_shooter):
                    run, mei, enemy, target, rocket = self.launch_distant_icicle(
                        allied_at_launch=allied_at_launch,
                    )
                    fight = run.match.combat
                    actor = mei if switch_shooter else enemy
                    actor.team = 1 - actor.team
                    for _ in range(70):
                        run.step({})
                    self.assertNotIn(rocket, fight.rockets)
                    damage = 75. if allied_at_launch else 0.
                    self.assertEqual(fight.vitals_of(target).total, 200. - damage)
                    self.assertEqual(self.charge(mei), 100. + damage)
                    hits = mei.client.messages(combat.HIT)
                    self.assertEqual(len(hits), int(allied_at_launch))
                    if hits:
                        self.assertEqual(hits[0]["+0x78"]["+0x0"]["+0x0"], enemy.body)

    def test_actual_cryo_credits_only_effective_healing_and_not_overheal(self):
        run, mei, _enemy, _target, vitals = cryo.CryoTests().duel()
        self.reset(mei)
        vitals.damage(50.)
        cryo.CryoTests().step(run, mei, 1, 8)
        cryo.CryoTests().step(run, mei, 260)
        self.assertEqual(vitals.total, 250.)
        self.assertAlmostEqual(self.charge(mei), 50., places=4)

    def test_blizzard_damage_gives_no_ultimate_damage_credit(self):
        run, mei, _enemy, target = fixtures.MeiCombatTests().duel()
        self.reset(mei)
        test = blizzard.BlizzardTests()
        test.launch(run, mei)
        area = test.landed(run)
        test.finish(run, area)
        self.assertAlmostEqual(run.match.combat.vitals_of(target).total, 115.)
        self.assertEqual(self.charge(mei), 0.)

    def test_icicle_rebuilds_charge_during_landed_blizzard_without_area_credit(self):
        run, mei, _enemy, target = fixtures.MeiCombatTests().duel()
        self.reset(mei)
        storm = blizzard.BlizzardTests()
        storm.launch(run, mei)
        area = storm.landed(run)
        for _ in range(25):
            run.step({})
        fight = run.match.combat
        self.assertIn(area.entity, fight.blizzard.areas)
        self.assertLess(fight.vitals_of(target).total, 200.)
        self.assertEqual(self.charge(mei), 0.)
        fixtures.MeiCombatTests().fire(run, mei, target, 2, .9)
        self.assertIn(area.entity, fight.blizzard.areas)
        self.assertEqual(self.charge(mei), 75.)
        storm.finish(run, area)
        self.assertAlmostEqual(fight.vitals_of(target).total, 40.)
        self.assertEqual(self.charge(mei), 75.)

    def test_enemy_wall_damage_gives_no_charge_but_body_hit_after_cancel_does(self):
        run, mei, enemy, _target = fixtures.MeiCombatTests().duel()
        self.assertTrue(run.match.switch_hero(enemy, heroes()[fixtures.MEI]))
        fight = run.match.combat
        x, y, z = mei.mover.position
        fight.walls.place_player(enemy, (x, y, z + 5.))
        for _ in range(30):
            run.step({})
        wall = walls.WallTests()
        aim = fixtures.aim(fight._eye(enemy), (x, y, z + 3.))
        wall.tap(run, enemy, 16, aim)
        wall.tap(run, enemy, 1, aim)
        pieces = list(fight.walls.pieces.values())
        self.assertEqual(len(pieces), 5)
        target = next(t for t in fight._targets() if t.owner is enemy)
        self.reset(mei)
        fixtures.MeiCombatTests().fire(run, mei, target, 2, .9)
        self.assertEqual(pieces[2].health, 325.)
        self.assertEqual(fight.vitals_of(target).total, 250.)
        self.assertEqual(self.charge(mei), 0.)
        wall.tap(run, enemy, 16, aim)
        self.assertFalse(fight.walls.pieces)
        fixtures.MeiCombatTests().fire(run, mei, target, 2, .9)
        self.assertEqual(fight.vitals_of(target).total, 175.)
        self.assertEqual(self.charge(mei), 75.)

    def test_effective_damage_caps_then_actual_q_spends_once(self):
        run, mei, _enemy, target = fixtures.MeiCombatTests().duel()
        self.reset(mei)
        mei.body_script.set_ult_charge(1600.)
        fixtures.MeiCombatTests().fire(run, mei, target, 2, .9)
        self.assertEqual(self.charge(mei), 1610.)
        for n in range(32):
            run.step({mei: (0, 9000, 32 if n == 0 else 0)})
        self.assertEqual(self.charge(mei), 0.)
        self.assertEqual(len(run.match.combat.blizzard.flights), 1)

    def test_death_stops_passive_respawn_retains_charge_and_hero_change_resets(self):
        run, mei, enemy, _target = fixtures.MeiCombatTests().duel()
        mei.body_script.set_ult_charge(234.)
        own = next(t for t in run.match.combat._targets() if t.owner is mei)
        run.match.combat._damage(enemy, own, 1000., False, None, run.frame)
        for _ in range(60):
            run.step({})
        self.assertEqual(self.charge(mei), 234.)
        run.match.combat._respawn(mei)
        self.assertFalse(run.match.combat.is_dead(mei))
        self.assertEqual(self.charge(mei), 234.)
        run.match.switch_hero(mei, heroes()[SOLDIER])
        self.assertEqual(self.charge(mei), 0.)

    def test_combat_generation_zero_disables_damage_and_effective_cryo_income(self):
        run, mei, _enemy, target = fixtures.MeiCombatTests().duel()
        self.reset(mei)
        mei.body_script.component.set(mei_ultimate.COMBAT_GENERATION, 0.)
        fixtures.MeiCombatTests().fire(run, mei, target, 2, .9)
        own = next(t for t in run.match.combat._targets() if t.owner is mei)
        vitals = run.match.combat.vitals_of(own)
        vitals.damage(50.)
        cryo.CryoTests().step(run, mei, 1, 8)
        cryo.CryoTests().step(run, mei, 260)
        self.assertEqual(vitals.total, 250.)
        self.assertEqual(self.charge(mei), 0.)

    def test_fractional_combat_composition_and_entity_override_of_mode_default(self):
        self.custom({mei_ultimate.GENERATION: .25, mei_ultimate.COMBAT_GENERATION: 2.})
        run, mei, _enemy, target = fixtures.MeiCombatTests().duel()
        self.reset(mei)
        fixtures.MeiCombatTests().fire(run, mei, target, 2, .9)
        self.assertEqual(self.charge(mei), 37.5)
        mei.body_script.component.set(mei_ultimate.GENERATION, .5)
        fixtures.MeiCombatTests().fire(run, mei, target, 2, .9)
        self.assertEqual(self.charge(mei), 112.5)

    def test_passive_zero_preserves_combat_income(self):
        self.custom({mei_ultimate.PASSIVE_GENERATION: 0.})
        run, mei, _enemy, target = fixtures.MeiCombatTests().duel()
        mei.body_script.set_ult_charge(0.)
        for _ in range(125):
            run.step({})
        self.assertEqual(self.charge(mei), 0.)
        fixtures.MeiCombatTests().fire(run, mei, target, 2, .9)
        self.assertEqual(self.charge(mei), 75.)

    def test_overall_and_passive_scalars_compose_and_can_change_live(self):
        run, mei, _enemy, _target = fixtures.MeiCombatTests().duel()
        mei.body_script.set_ult_charge(0.)
        component = mei.body_script.component
        component.set(mei_ultimate.GENERATION, .5)
        component.set(mei_ultimate.PASSIVE_GENERATION, 3.)
        for _ in range(125):
            run.step({})
        self.assertAlmostEqual(self.charge(mei), 15., places=4)
        component.set(mei_ultimate.PASSIVE_GENERATION, 0.)
        paused = self.charge(mei)
        for _ in range(125):
            run.step({})
        self.assertEqual(self.charge(mei), paused)

    def test_resource_minimum_and_maximum_combat_scalars_respect_cost_cap(self):
        for total, combat_rate, expected in ((.1, 1., 7.5), (5., 5., 1610.)):
            with self.subTest(total=total, combat=combat_rate):
                run, mei, _enemy, target = fixtures.MeiCombatTests().duel()
                self.reset(mei)
                mei.body_script.component.set(mei_ultimate.GENERATION, total)
                mei.body_script.component.set(mei_ultimate.COMBAT_GENERATION, combat_rate)
                fixtures.MeiCombatTests().fire(run, mei, target, 2, .9)
                self.assertAlmostEqual(self.charge(mei), expected, places=5)

    def test_spawn_ready_precedes_first_input_and_actual_q_spends_without_refill(self):
        self.custom({mei_ultimate.SPAWN_READY: True, mei_ultimate.PASSIVE_GENERATION: 0.})
        run, mei, _enemy, _target = fixtures.MeiCombatTests().duel()
        self.assertEqual(self.charge(mei), 1610.)
        for n in range(40):
            run.step({mei: (0, 9000, 32 if n == 0 else 0)})
        self.assertEqual(self.charge(mei), 0.)
        ultimate = next(i for i in mei.body_script.component.instances.values() if i.graph.index == 0x64A)
        self.assertEqual(ultimate.state(3).counter, 1)

    def test_spawn_ready_wins_over_retained_charge_on_combat_respawn(self):
        self.custom({mei_ultimate.SPAWN_READY: True, mei_ultimate.PASSIVE_GENERATION: 0.})
        run, mei, enemy, _target = fixtures.MeiCombatTests().duel()
        mei.body_script.set_ult_charge(234.)
        own = next(t for t in run.match.combat._targets() if t.owner is mei)
        run.match.combat._damage(enemy, own, 1000., False, None, run.frame)
        run.match.combat._respawn(mei)
        self.assertFalse(run.match.combat.is_dead(mei))
        self.assertEqual(self.charge(mei), 1610.)

    def test_enabling_spawn_ready_on_living_body_does_not_refill(self):
        run, mei, _enemy, _target = fixtures.MeiCombatTests().duel()
        self.reset(mei)
        mei.body_script.component.set(mei_ultimate.SPAWN_READY, True)
        for _ in range(80):
            run.step({})
        self.assertEqual(self.charge(mei), 0.)

    def test_ultimate_disabled_cannot_bypass_with_full_charge_and_repeated_q(self):
        self.custom({mei_ultimate.ULTIMATE_ENABLED: False, mei_ultimate.PASSIVE_GENERATION: 0.})
        run, mei, _enemy, _target = fixtures.MeiCombatTests().duel()
        mei.body_script.set_ult_charge(1610.)
        for n in range(60):
            run.step({mei: (0, 9000, 32 if n % 2 else 0)})
        ultimate = next(i for i in mei.body_script.component.instances.values() if i.graph.index == 0x64A)
        self.assertEqual(ultimate.state(3).counter, 0)
        self.assertEqual(self.charge(mei), 1610.)
        mei.body_script.component.set(mei_ultimate.ULTIMATE_ENABLED, True)
        run.step({})
        for n in range(32):
            run.step({mei: (0, 9000, 32 if n == 0 else 0)})
        self.assertEqual(ultimate.state(3).counter, 1)
        self.assertEqual(self.charge(mei), 0.)

    def test_disabling_during_windup_cancels_release_without_refunding_charge(self):
        run, mei, _enemy, _target = fixtures.MeiCombatTests().duel()
        self.reset(mei)
        mei.body_script.set_ult_charge(1610.)
        for n in range(10):
            run.step({mei: (0, 9000, 32 if n == 0 else 0)})
        self.assertEqual(self.charge(mei), 0.)
        mei.body_script.component.set(mei_ultimate.ULTIMATE_ENABLED, False)
        for _ in range(80):
            run.step({mei: (0, 9000, 32)})
        self.assertFalse(run.match.combat.blizzard.flights)
        self.assertFalse(run.match.combat.blizzard.areas)
        self.assertEqual(self.charge(mei), 0.)

    def test_disabling_after_release_keeps_existing_blizzard_area(self):
        run, mei, _enemy, target = fixtures.MeiCombatTests().duel()
        self.reset(mei)
        test = blizzard.BlizzardTests()
        test.launch(run, mei)
        mei.body_script.component.set(mei_ultimate.ULTIMATE_ENABLED, False)
        area = test.landed(run)
        self.assertIs(area.owner, mei)
        test.finish(run, area)
        self.assertAlmostEqual(run.match.combat.vitals_of(target).total, 115.)
        self.assertEqual(self.charge(mei), 0.)

    def test_authored_q_short_press_inside_150ms_window_survives_release(self):
        run, mei, _enemy, _target = fixtures.MeiCombatTests().duel()
        self.reset(mei)
        mei.body_script.set_ult_charge(1600.)
        run.step({mei: (0, 9000, 32)})
        for _ in range(3):
            run.step({mei: (0, 9000, 0)})
        mei.body_script.set_ult_charge(1610.)
        for _ in range(40):
            run.step({mei: (0, 9000, 0)})
        instance = next(i for i in mei.body_script.component.instances.values() if i.graph.index == 0x64A)
        self.assertEqual(instance.state(3).counter, 1)
        self.assertEqual(self.charge(mei), 0.)

    def test_authored_q_released_early_press_expires_before_late_charge(self):
        run, mei, _enemy, _target = fixtures.MeiCombatTests().duel()
        self.reset(mei)
        mei.body_script.set_ult_charge(1600.)
        run.step({mei: (0, 9000, 32)})
        for _ in range(20):
            run.step({mei: (0, 9000, 0)})
        mei.body_script.set_ult_charge(1610.)
        for _ in range(40):
            run.step({mei: (0, 9000, 0)})
        instance = next(i for i in mei.body_script.component.instances.values() if i.graph.index == 0x64A)
        self.assertEqual(instance.state(3).counter, 0)
        self.assertEqual(self.charge(mei), 1610.)

    def test_authored_q_held_input_starts_once_after_readiness(self):
        run, mei, _enemy, _target = fixtures.MeiCombatTests().duel()
        self.reset(mei)
        mei.body_script.set_ult_charge(1600.)
        for _ in range(20):
            run.step({mei: (0, 9000, 32)})
        mei.body_script.set_ult_charge(1610.)
        for _ in range(40):
            run.step({mei: (0, 9000, 32)})
        instance = next(i for i in mei.body_script.component.instances.values() if i.graph.index == 0x64A)
        self.assertEqual(instance.state(3).counter, 1)
        self.assertEqual(self.charge(mei), 0.)

    def test_authored_q_fresh_press_after_readiness_starts_once(self):
        run, mei, _enemy, _target = fixtures.MeiCombatTests().duel()
        self.reset(mei)
        mei.body_script.set_ult_charge(1610.)
        run.step({mei: (0, 9000, 0)})
        for n in range(40):
            run.step({mei: (0, 9000, 32 if n == 0 else 0)})
        instance = next(i for i in mei.body_script.component.instances.values() if i.graph.index == 0x64A)
        self.assertEqual(instance.state(3).counter, 1)
        self.assertEqual(self.charge(mei), 0.)


if __name__ == '__main__':
    unittest.main()
