"""Actual Wraith/Fade input against Mei; defense/entry policy reconstructed."""

import unittest

import test_mei_blizzard as blizzard
import test_mei_combat as fixtures
import test_mei_frost as frost
from test_game_combat import Run

from ow174.game import combat, phase_defenses
from ow174.game.content import heroes

REAPER, MOIRA = 0x02E0000000000002, 0x02E00000000001A2


class MeiPhaseTests(unittest.TestCase):
    def scene(self, hero, distance=5.):
        run, owner, enemy, _ = fixtures.MeiCombatTests().duel()
        self.assertTrue(run.match.switch_hero(enemy, heroes()[hero]))
        x, y, z = owner.mover.position
        run.match.combat.walls.place_player(enemy, (x, y, z + distance))
        for _ in range(30):
            run.step({})
        target = next(t for t in run.match.combat._targets() if t.owner is enemy)
        return run, owner, enemy, target

    def cast(self, run, enemy):
        run.step({enemy: (-32768, 0, 8)})
        self.assertTrue(phase_defenses.active(enemy))
        graph = phase_defenses.PHASES[enemy.hero.guid][0]
        return next(i for i in enemy.body_script.component.instances.values() if i.graph.index == graph)

    def finish(self, run, enemy):
        for _ in range(250):
            if not phase_defenses.active(enemy):
                return
            run.step({})
        self.fail('bounded phase never ended under actual input clocks')

    def test_actual_entry_and_authored_wait_bound_protection_not_10467_recovery(self):
        for hero, duration, cooldown in ((REAPER, 3000, 8.), (MOIRA, 750, 6.)):
            with self.subTest(hero=hero):
                run, _owner, enemy, _target = self.scene(hero)
                instance = self.cast(run, enemy)
                wait = instance.state(3)
                self.assertEqual(wait.ms, duration)
                self.assertEqual(instance.vars[472].value(), cooldown)
                deadline = wait.start + wait.ms
                while enemy.body_script.component.now < deadline:
                    self.assertTrue(phase_defenses.active(enemy))
                    run.step({})
                self.assertFalse(phase_defenses.active(enemy))
                self.assertTrue(enemy.body_script.component.entity_var(10467).value())

    def test_spray_icicle_and_melee_cannot_damage_or_charge_from_phase(self):
        for hero in (REAPER, MOIRA):
            for button in (1, 2, 0x800):
                with self.subTest(hero=hero, button=button):
                    run, owner, enemy, target = self.scene(hero, 1.5)
                    owner.body_script.ult_seconds = 0
                    owner.body_script.set_ult_charge(0.)
                    health = run.match.combat.vitals_of(target).total
                    self.cast(run, enemy)
                    fixtures.MeiCombatTests().fire(run, owner, target, button, .9, frames=44)
                    self.assertTrue(phase_defenses.active(enemy))
                    self.assertEqual(run.match.combat.vitals_of(target).total, health)
                    self.assertIsNone(run.match.combat.frost(enemy))
                    self.assertFalse(owner.client.messages(combat.HIT))
                    self.assertEqual(owner.body_script.component.entity_var(342).value(), 0.)

    def test_direct_damage_and_stun_paths_cannot_bypass_active_phase(self):
        for hero in (REAPER, MOIRA):
            with self.subTest(hero=hero):
                run, owner, enemy, target = self.scene(hero)
                before = run.match.combat.vitals_of(target).total
                self.cast(run, enemy)
                self.assertEqual(run.match.combat._damage(owner, target, 999., True, None, run.frame), 0.)
                run.match.combat._hit_effect(target, combat.FLASHBANG_EFFECT)
                self.assertEqual(run.match.combat.vitals_of(target).total, before)
                self.assertFalse(run.match.combat.is_stunned(enemy))

    def test_actual_phase_cleanses_existing_spray_slow(self):
        for hero in (REAPER, MOIRA):
            with self.subTest(hero=hero):
                run, owner, enemy, target = self.scene(hero)
                frost.FrostTests().one_spray(run, owner, target)
                self.assertIsNotNone(run.match.combat.frost(enemy))
                self.cast(run, enemy)
                self.assertIsNone(run.match.combat.frost(enemy))
                self.assertNotIn(enemy.body, run.match.combat.frost_sources)

    def test_icicle_passes_phase_body_and_hits_real_rear_training_bot(self):
        for hero in (REAPER, MOIRA):
            with self.subTest(hero=hero):
                run, owner, enemy, target = self.scene(hero)
                bot = Run().match.bots[0]
                bot.body_script, bot.route, bot.firing, bot.team = None, [], False, enemy.team
                x, y, z = owner.mover.position
                bot.position = (x, y, z + 8.)
                run.match.bots.append(bot)
                rear = next(t for t in run.match.combat._targets() if t.owner is bot)
                rear_health = run.match.combat.vitals_of(rear).total
                self.cast(run, enemy)
                before = run.match.combat.vitals_of(target).total
                fixtures.MeiCombatTests().fire(run, owner, rear, 2, .9, frames=44)
                self.assertEqual(run.match.combat.vitals_of(target).total, before)
                self.assertEqual(run.match.combat.vitals_of(rear).total, rear_health - 75.)

    def test_actual_icicle_damages_after_phase_end(self):
        for hero in (REAPER, MOIRA):
            with self.subTest(hero=hero):
                run, owner, enemy, target = self.scene(hero)
                self.cast(run, enemy)
                self.finish(run, enemy)
                before = run.match.combat.vitals_of(target).total
                fixtures.MeiCombatTests().fire(run, owner, target, 2, .9)
                self.assertEqual(run.match.combat.vitals_of(target).total, before - 75.)

    def test_blizzard_ignores_phase_then_resumes_damage_without_backfill(self):
        for hero in (REAPER, MOIRA):
            for midstorm in (False, True):
                with self.subTest(hero=hero, midstorm=midstorm):
                    run, owner, enemy, target = self.scene(hero)
                    fight = run.match.combat
                    fixture = blizzard.BlizzardTests()
                    fixture.launch(run, owner)
                    area = fixture.landed(run)
                    vitals = fight.vitals_of(target)
                    if midstorm:
                        initial_health = vitals.total
                        while run.frame * 16 < area.starts + 500:
                            run.step({})
                        self.assertLess(vitals.total, initial_health)
                        self.assertGreater(fight.frost(enemy)[0], .5)
                        self.assertIn(enemy.body, area.frost_due)
                    self.cast(run, enemy)
                    before = vitals.total
                    for _ in range(20):
                        run.step({})
                    self.assertEqual(vitals.total, before)
                    self.assertNotIn(enemy.body, area.frost_due)
                    self.assertNotIn(enemy.body, fight.frost_sources)
                    self.assertIsNone(fight.frost(enemy))
                    self.finish(run, enemy)
                    self.assertEqual(vitals.total, before)
                    run.step({})
                    loss = before - vitals.total
                    self.assertGreater(loss, 0.)
                    self.assertLessEqual(loss, .321)
                    self.assertAlmostEqual(fight.frost(enemy)[0], .5)

    def test_unbound_immunity_variable_does_not_grant_phase(self):
        for hero in (REAPER, MOIRA):
            with self.subTest(hero=hero):
                run, owner, enemy, target = self.scene(hero)
                enemy.body_script.component.set(phase_defenses.PHASES[hero][1], True)
                self.assertFalse(phase_defenses.active(enemy))
                before = run.match.combat.vitals_of(target).total
                fixtures.MeiCombatTests().fire(run, owner, target, 2, .9)
                self.assertEqual(run.match.combat.vitals_of(target).total, before - 75.)

    def test_authored_cooldown_rejects_repeated_early_input_then_fresh_cast_works(self):
        for hero in (REAPER, MOIRA):
            with self.subTest(hero=hero):
                run, _owner, enemy, _target = self.scene(hero)
                instance = self.cast(run, enemy)
                self.finish(run, enemy)
                stack_key = phase_defenses.PHASES[hero][1]
                stack = next(s for s in instance.states.values()
                             if s.node.cls == 'STUStatescriptStateStack'
                             and (s.node.field('m_out_Var') or {}).get('m_identifier') == stack_key)
                count = stack.counter
                for _ in range(60):
                    run.step({enemy: (-32768, 0, 8)})
                    run.step({})
                    self.assertFalse(phase_defenses.active(enemy))
                self.assertEqual(stack.counter, count)
                for _ in range(700):
                    run.step({})
                self.cast(run, enemy)
                self.assertEqual(stack.counter, count + 1)

    def test_phase_body_replacement_removes_protection_and_new_body_takes_damage(self):
        for hero in (REAPER, MOIRA):
            with self.subTest(hero=hero):
                run, owner, enemy, _target = self.scene(hero)
                self.cast(run, enemy)
                self.assertTrue(run.match.switch_hero(enemy, heroes()[0x02E000000000006E]))
                self.assertFalse(phase_defenses.active(enemy))
                target = next(t for t in run.match.combat._targets() if t.owner is enemy)
                before = run.match.combat.vitals_of(target).total
                fixtures.MeiCombatTests().fire(run, owner, target, 2, .9)
                self.assertEqual(run.match.combat.vitals_of(target).total, before - 75.)

    def test_actual_freeze_prevents_starting_phase(self):
        for hero in (REAPER, MOIRA):
            with self.subTest(hero=hero):
                run, owner, enemy, target = self.scene(hero)
                frost.FrostTests().freeze(run, owner, target)
                run.step({enemy: (-32768, 0, 8)})
                self.assertFalse(phase_defenses.active(enemy))
                self.assertTrue(run.match.combat.frozen(enemy))
