"""Manual0101 cast lockout for the existing debug refill; native generation is open."""

import unittest
from dataclasses import replace
from unittest.mock import patch

import test_game_hanzo as hero_tests
import test_game_hanzo_control as control_tests
import test_game_hanzo_dragons as dragon_tests
from test_game_combat import aim, in_world

from ow174.game import bodytrace, combat, movelog
from ow174.game.content import SOLDIER, heroes
from ow174.game.script.driver import BodyScript

HANZO = 0x02E0000000000005


class HanzoManualChargeTests(unittest.TestCase):
    def setUp(self):
        for module in (bodytrace, movelog):
            self.enterContext(patch.object(module, "ENABLED", False))

    @staticmethod
    def phases(body):
        return {state.node.pos for instance in body.component.instances.values()
                if instance.graph.index == 0x101 for state in instance.states.values()
                if state.active and state.node.pos in (29, 37)}

    @staticmethod
    def charge(body):
        return body.component.entity_var(342).value()

    def test_no_debug_refill_during_both_phases_then_refill_and_fresh_recast(self):
        run, hero, target = hero_tests.HanzoCombatTests().arena()
        body = hero.body_script
        body.ult_seconds = 0.1  # amplify any missed gate; this is explicitly a debug rate
        body.set_ult_charge(1680)
        fight = run.match.combat
        with patch.object(fight, "_shoot", wraps=fight._shoot) as fire:
            run.step({hero: (0, 0, 32)})
            self.assertEqual(self.phases(body), {29, 37})
            self.assertEqual(self.charge(body), 0)
            for offset in range(1, 75):
                run.step({hero: (0, 0, 32 if offset == 65 else 0)})
                self.assertEqual(self.charge(body), 0, offset)
                self.assertEqual(self.phases(body), {29, 37} if offset < 62 else {37})
            self.assertEqual(sum(c.args[0] is hero for c in fire.call_args_list), 1)
            run.step({})  # authored1.2s recovery ends; refill may resume in this frame
            self.assertFalse(self.phases(body))
            self.assertGreater(self.charge(body), 0)
            self.assertLess(self.charge(body), 1680)
            for _ in range(10):
                run.step({})
            self.assertEqual(fight.vitals[target.body].total, 75)
            self.assertEqual(self.charge(body), 1680)
            run.step({hero: (0, 0, 32)})
            self.assertEqual(self.charge(body), 0)
            for _ in range(90):
                run.step({})
        self.assertEqual(sum(c.args[0] is hero for c in fire.call_args_list), 2)
        self.assertTrue(fight.vitals[target.body].dead)

    def test_actual_flashbang_abort_releases_debug_refill_without_a_late_arrow(self):
        control = control_tests.HanzoControlTests()
        run, caster, hero = control.arena()
        body = hero.body_script
        body.ult_seconds = 1
        body.set_ult_charge(1680)
        fight = run.match.combat
        with patch.object(fight, "_shoot", wraps=fight._shoot) as fire:
            control.step(run, caster, hero, target_buttons=32)
            self.assertEqual(self.charge(body), 0)
            control.cast(run, caster, hero)
            self.assertTrue(fight.is_stunned(hero))
            self.assertFalse(self.phases(body))
            self.assertGreater(self.charge(body), 0)
            for _ in range(150):
                control.step(run, caster, hero)
            self.assertFalse(any(c.args[0] is hero for c in fire.call_args_list))
            self.assertEqual(self.charge(body), 1680)
            control.step(run, caster, hero, target_buttons=32)
            self.assertEqual(self.charge(body), 0)
            for _ in range(90):
                control.step(run, caster, hero)
        self.assertEqual(sum(c.args[0] is hero for c in fire.call_args_list), 1)
        self.assertEqual(fight.vitals[caster.body].total, 100)

    def test_replacement_does_not_inherit_the_old_manual_cast_lockout(self):
        run, hero, _target = hero_tests.HanzoCombatTests().arena()
        body = hero.body_script
        body.ult_seconds = 0.1
        body.set_ult_charge(1680)
        run.step({hero: (0, 0, 32)})
        self.assertEqual(self.charge(body), 0)
        self.assertEqual(self.phases(body), {29, 37})
        run.match.switch_hero(hero, heroes()[SOLDIER])
        self.assertTrue(body.retired)
        replacement = hero.body_script
        replacement.ult_seconds = 1
        run.step({})
        self.assertFalse(self.phases(replacement))
        self.assertGreater(self.charge(replacement), 0)
        run.match.switch_hero(hero, heroes()[HANZO])
        replacement = hero.body_script
        replacement.ult_seconds = 1
        run.step({})
        self.assertFalse(self.phases(replacement))
        self.assertGreater(self.charge(replacement), 0)

    def test_skipped_commands_cross_phase_deadline_without_refilling_the_locked_frames(self):
        bodies = [BodyScript(HANZO, 100 + index, 1000, ult_seconds=1) for index in range(2)]
        for body in bodies:
            body.set_ult_charge(1680)
            body.command(1001, 32)
            self.assertEqual(self.charge(body), 0)
        continuous, skipped = bodies
        for frame in range(1002, 1081):
            continuous.command(frame, 0)
        skipped.command(1080, 0)
        self.assertFalse(self.phases(continuous))
        self.assertFalse(self.phases(skipped))
        self.assertEqual(self.charge(continuous), self.charge(skipped))
        self.assertGreater(self.charge(skipped), 0)
        self.assertLess(self.charge(skipped), 1680 / 8)

    def test_authored_passive_second_boundary_and_skipped_input_agree(self):
        continuous, skipped = [BodyScript(HANZO, 100 + i, 1000, ult_seconds=None) for i in range(2)]
        for frame in range(1001, 1063):
            continuous.command(frame, 0)
        skipped.command(1062, 0)
        self.assertEqual((self.charge(continuous), self.charge(skipped)), (0, 0))
        for body in (continuous, skipped):
            body.command(1063, 0)
            self.assertEqual(self.charge(body), 5)
            self.assertEqual(body.passive_charge_ms, 8)

    def test_actual_miss_and_lethal_overkill_credit_only_removed_health(self):
        run, hero, target = hero_tests.HanzoCombatTests().arena()
        script = hero.body_script
        fight = run.match.combat
        x, y, z = target.mover.position
        hit = aim(fight._eye(hero), (x, y + 1, z))
        initial = self.charge(script)
        elapsed = script.passive_charge_ms
        frame = script.frame
        for angles, loss in (((16384, 0), 0), (hit, 125), (hit, 200)):
            for _ in range(70):
                run.step({hero: (*angles, 1)})
            run.step({hero: (*angles, 0)})
            for _ in range(12):
                run.step({})
            passive = (elapsed + (script.frame - frame) * script.frame_ms) // 1000 * 5
            self.assertEqual(self.charge(script), initial + passive + loss)
        self.assertTrue(fight.vitals[target.body].dead)

    def test_bow_charge_counts_restored_health_only_when_removed_again(self):
        run, hero, victim = hero_tests.HanzoCombatTests().arena()
        match = run.match
        match.game_map = replace(match.game_map, free_for_all=False)
        medic = match.add_player(3, "Field owner", SOLDIER, victim.team, False)
        in_world(match, medic)
        run.players.append(medic)
        self.assertTrue(match.switch_hero(medic, heroes()[SOLDIER]))
        medic.team = victim.team
        x, y, z = victim.mover.position
        medic.mover.position[:] = (x + 2, y, z)
        medic.mover.body = None
        fight = match.combat
        pool = fight.vitals_of(next(t for t in fight._targets() if t.owner is victim))
        script = hero.body_script
        self.assertIsNone(script.ult_seconds)
        angles = aim(fight._eye(hero), (x, y + 1, z))

        run.step({medic: (0, 0, 16)})
        for _ in range(100):
            if any(shot.field_tick is not None for shot in fight.projectiles.shots):
                break
            run.step({})
        field, = [shot for shot in fight.projectiles.shots if shot.field_tick is not None]
        self.assertIs(field.player, medic)
        clock, frame, initial = script.passive_charge_ms, script.frame, self.charge(script)
        effective = []
        damage = fight._damage

        def observe(shooter, target, amount, *args, **kwargs):
            before = pool.total
            result = damage(shooter, target, amount, *args, **kwargs)
            if shooter is hero and target.owner is victim:
                effective.append(before - pool.total)
            return result

        with patch.object(fight, "_damage", side_effect=observe):
            for index in range(2):
                for _ in range(70):
                    run.step({hero: (*angles, 1)})
                run.step({hero: (*angles, 0)})
                for _ in range(12):
                    run.step({})
                self.assertEqual(len(effective), index + 1)
                passive = (clock + (script.frame - frame) * script.frame_ms) // 1000 * 5
                self.assertAlmostEqual(self.charge(script), initial + passive + sum(effective), places=4)
                if index == 0:
                    self.assertGreater(pool.total, 75)
        self.assertEqual(effective[0], 125)
        self.assertTrue(75 < effective[1] < 125)
        self.assertTrue(pool.dead)
        self.assertEqual(pool.killer, hero.body)
        self.assertEqual(len(hero.client.messages(combat.HIT)), 2)

    def test_real_death_freezes_charge_respawn_keeps_points_and_hero_swap_clears(self):
        run, hero, enemy = hero_tests.HanzoCombatTests().arena()
        script = hero.body_script
        fight = run.match.combat
        x, y, z = enemy.mover.position
        yaw, pitch = aim(fight._eye(hero), (x, y + 1, z))
        for _ in range(70):
            run.step({hero: (yaw, pitch, 1)})
        run.step({hero: (yaw, pitch, 0)})
        for _ in range(12):
            run.step({})
        self.assertGreaterEqual(self.charge(script), 125)
        x, y, z = hero.mover.position
        yaw, pitch = aim(fight._eye(enemy), (x, y + 1, z))
        old_body = hero.body
        for _ in range(110):
            run.step({enemy: (yaw, pitch, 1)})
            if fight.is_dead(hero):
                break
        self.assertTrue(fight.is_dead(hero))
        points = self.charge(script)
        self.assertGreaterEqual(points, 125)
        for _ in range(700):
            run.step({})
            if hero.body != old_body:
                break
            self.assertEqual(self.charge(script), points)
        self.assertNotEqual(hero.body, old_body)
        self.assertTrue(script.retired)
        self.assertEqual(self.charge(hero.body_script), points)
        self.assertLess(hero.body_script.passive_charge_ms, 1000)
        for selected in (SOLDIER, HANZO):
            self.assertTrue(run.match.switch_hero(hero, heroes()[selected]))
            self.assertEqual(self.charge(hero.body_script), 0)

    def test_initial_dragon_damage_charge_depends_on_cast_recovery_at_contact(self):
        for distance, locked, expected_charge in ((2.5, True, 0), (5, False, 125)):
            with self.subTest(distance=distance):
                run, hero, victim = hero_tests.HanzoCombatTests().arena(distance=distance)
                fight = run.match.combat
                fight.configure_dragonstrike(dragon_tests.prepared_profile())
                script = hero.body_script
                self.assertIsNone(script.ult_seconds)
                script.set_ult_charge(1680)
                target = next(t for t in fight._targets() if t.owner is victim)
                pool = fight.vitals_of(target)
                run.step({hero: (0, 0, 32)})
                self.assertEqual(self.charge(script), 0)
                for _ in range(100):
                    run.step({})
                    if pool.total < 200:
                        break
                    self.assertEqual(self.charge(script), 0)
                self.assertEqual(pool.total, 75)
                self.assertEqual(bool(self.phases(script)), locked)
                self.assertEqual(self.charge(script), expected_charge)
                self.assertFalse(fight.dragons)

    def test_live_dragon_and_fresh_bow_use_current_body_charge_after_recovery(self):
        run, hero, victim = hero_tests.HanzoCombatTests().arena(distance=20)
        fight = run.match.combat
        fight.configure_dragonstrike(dragon_tests.prepared_profile())
        script = hero.body_script
        self.assertIsNone(script.ult_seconds)
        script.set_ult_charge(1680)
        run.step({hero: (0, 0, 32)})
        self.assertTrue(self.phases(script))
        for _ in range(90):
            self.assertEqual(self.charge(script), 0)
            run.step({})
            if not self.phases(script):
                break
        self.assertFalse(self.phases(script))
        self.assertEqual(self.charge(script), 0)
        target = next(t for t in fight._targets() if t.owner is victim)
        pool = fight.vitals_of(target)
        self.assertEqual(pool.total, 200)
        clock = script.passive_charge_ms
        frame = script.frame

        def check_charge():
            elapsed = (script.frame - frame) * script.frame_ms
            passive = (clock + elapsed) // 1000 * 5
            self.assertAlmostEqual(self.charge(script), passive + 200 - pool.total, places=4)

        for _ in range(140):
            run.step({})
            check_charge()
            if pool.total < 200:
                break
        self.assertLess(pool.total, 200)
        self.assertGreater(self.charge(script), 0)
        self.assertFalse(fight.rockets)
        flight, = fight.dragons
        self.assertEqual(flight.source_entity, hero.body)

        x, y, z = hero.mover.position
        victim.mover.position[:] = (x + 5, y, z)
        victim.mover.body = None
        angles = aim(fight._eye(hero), (x + 5, y + 1, z))
        before = pool.total
        for _ in range(70):
            run.step({hero: (*angles, 1)})
        run.step({hero: (*angles, 0)})
        for _ in range(12):
            run.step({})
        self.assertTrue(flight in fight.dragons)
        self.assertAlmostEqual(before - pool.total, 125)
        check_charge()

    def test_old_dragon_kill_does_not_charge_replacement_hanzo(self):
        run, hero, enemy = hero_tests.HanzoCombatTests().arena(40)
        fight = run.match.combat
        fight.configure_dragonstrike(dragon_tests.prepared_profile())
        hero.body_script.set_ult_charge(1680)
        dragon_tests.DragonPlayerTests.cast(run, hero, frames=100)
        self.assertTrue(fight.dragons)
        original_body = hero.body
        self.assertTrue(run.match.switch_hero(hero, hero.hero))
        fresh = hero.body_script
        self.assertNotEqual(hero.body, original_body)
        self.assertIsNone(fresh.ult_seconds)
        initial_frame = fresh.frame
        for _ in range(220):
            run.step({})
            if fight.is_dead(enemy):
                break
        self.assertTrue(fight.is_dead(enemy))
        self.assertEqual(fight.vitals[enemy.body].killer, original_body)
        self.assertEqual(self.charge(fresh), (fresh.frame - initial_frame) * fresh.frame_ms // 1000 * 5)
