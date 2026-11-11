"""Actual Symmetra attacks against Reaper's Wraith and Moira's Fade."""

import math
import os
import unittest
from unittest.mock import patch

import test_mccree_flashbang as flashbang
from test_game_combat import aim, in_world
from test_symmetra_combat import arena, health
from test_symmetra_turrets import wall

from ow174.game import combat, cryo, phase_defenses, stats
from ow174.game.commands import Command
from ow174.game.content import SOLDIER, heroes


class SymmetraPhaseTests(unittest.TestCase):
    def scene(self, hero, position=(0, 1, 2)):
        run, owner, defender = arena()
        self.assertTrue(run.match.switch_hero(defender, heroes()[hero]))
        defender.mover.position[:] = position
        owner.body_script.component.set(12918, 0)
        fight = run.match.combat

        def step(attack=0, defense=0, count=1):
            for _ in range(count):
                x, y, z = defender.mover.position
                angles = aim(fight._eye(owner), (x, y + 1, z))
                run.step({owner: (*angles, attack), defender: (0, 0, defense)})

        step(count=15)
        return run, owner, defender, step

    def test_beam_orb_and_melee_do_not_hit_either_phase(self):
        for hero in phase_defenses.PHASES:
            for attack in (1, 2, 0x800):
                with self.subTest(hero=hero, attack=attack):
                    run, owner, defender, step = self.scene(hero)
                    before = health(run, defender).total
                    if attack == 2:
                        step(2, count=90)
                    step(2 if attack == 2 else 0, 8)
                    self.assertTrue(phase_defenses.active(defender))
                    step(attack if attack != 2 else 0, count=5 if attack == 1 else 1)
                    if attack == 2:
                        [orb] = run.match.combat.rockets
                        step(count=10)
                        self.assertIn(orb, run.match.combat.rockets)
                        self.assertGreater(orb.position[2], defender.mover.position[2])
                    self.assertTrue(phase_defenses.active(defender))
                    self.assertEqual(health(run, defender).total, before)
                    self.assertFalse(owner.client.messages(combat.HIT))
                    self.assertEqual(owner.stats.value(stats.HERO_DAMAGE), 0)
                    self.assertEqual(owner.body_script.component.entity_var(342).value(), 0)

    def test_actual_beam_passes_through_phase_then_hits_defender_after_expiry(self):
        for hero in phase_defenses.PHASES:
            with self.subTest(hero=hero):
                run, _, defender, step = self.scene(hero)
                rear = run.match.add_player(3, "Rear", SOLDIER, 0, False)
                in_world(run.match, rear)
                run.players.append(rear)
                self.assertTrue(run.match.switch_hero(rear, heroes()[SOLDIER]))
                rear.mover.position[:] = (0, 1, 4)
                step(defense=8)
                self.assertTrue(phase_defenses.active(defender))
                before = health(run, defender).total
                rear_hp = health(run, rear).total
                step(1, count=5)
                self.assertEqual(health(run, defender).total, before)
                self.assertLess(health(run, rear).total, rear_hp)
                script = defender.body_script
                graph_index, _ = phase_defenses.PHASES[hero]
                instance = next(i for i in script.component.instances.values()
                                if i.graph.index == graph_index)
                wait = instance.state(3)
                self.assertEqual(wait.ms, 3000 if hero == 0x02E0000000000002 else 750)
                deadline = wait.start + wait.ms
                while script.component.now + script.frame_ms < deadline:
                    step()
                    self.assertTrue(phase_defenses.active(defender))
                step()
                self.assertFalse(phase_defenses.active(defender))
                step(1, count=5)
                self.assertLess(health(run, defender).total, before)

    def test_phase_expires_without_owner_commands_and_resumes_with_current_input(self):
        for hero in phase_defenses.PHASES:
            with self.subTest(hero=hero):
                run, owner, defender, step = self.scene(hero)
                step(defense=8)
                script = defender.body_script
                fight = run.match.combat
                self.assertTrue(phase_defenses.active(defender))
                index, _ = phase_defenses.PHASES[hero]
                instance = next(i for i in script.component.instances.values() if i.graph.index == index)
                wait = instance.state(3)
                deadline = wait.start + wait.ms
                for _ in range(200):
                    if not phase_defenses.active(defender):
                        break
                    run.frame += 1
                    run.match.update(run.frame * combat.TICK, run.frame)
                    if script.component.now < deadline:
                        self.assertTrue(phase_defenses.active(defender))
                self.assertFalse(phase_defenses.active(defender))
                self.assertGreaterEqual(script.component.now, deadline)
                self.assertEqual(script.frame, run.frame - 1)
                frame = script.frame
                counter = wait.counter
                defender.take_commands([Command(frame, buttons=8)])
                self.assertEqual((script.frame, wait.counter), (frame, counter))
                self.assertFalse(phase_defenses.active(defender))
                before = health(run, defender).total
                angles = aim(fight._eye(owner), (0, 2, 2))
                owner.take_commands([Command(run.frame, yaw=angles[0], pitch=angles[1], buttons=1)])
                self.assertLess(health(run, defender).total, before)
                step(defense=8)
                self.assertFalse(phase_defenses.active(defender))
                run.frame += 750
                run.match.update(run.frame * combat.TICK, run.frame)
                self.assertNotIn(defender.body, fight.phase_clocks)
                step(defense=8)
                self.assertTrue(phase_defenses.active(defender))
                self.assertGreater(wait.counter, counter)
                old_body = defender.body
                self.assertTrue(run.match.switch_hero(defender, heroes()[SOLDIER]))
                self.assertNotIn(old_body, fight.phase_clocks)
                self.assertFalse(phase_defenses.active(defender))

    def test_reaper_cancel_ends_protection_and_early_recast_does_not_restore_it(self):
        run, _, defender, step = self.scene(0x02E0000000000002)
        step(defense=8)
        step(count=30)
        self.assertTrue(phase_defenses.active(defender))
        step(defense=1)
        self.assertFalse(phase_defenses.active(defender))
        before = health(run, defender).total
        step(1, count=27)
        self.assertLess(health(run, defender).total, before)
        step(defense=8)
        self.assertFalse(phase_defenses.active(defender))

    def test_actual_flashbang_passes_phased_target_without_stunning(self):
        fixture = flashbang.FlashbangTests()
        for hero in phase_defenses.PHASES:
            with self.subTest(hero=hero):
                run, caster, defender = fixture.arena(distance=3)
                position = tuple(defender.mover.position)
                self.assertTrue(run.match.switch_hero(defender, heroes()[hero]))
                defender.mover.position[:] = position
                fixture.step(run, caster, defender, target_buttons=8)
                self.assertTrue(phase_defenses.active(defender))
                before = health(run, defender).total
                fixture.step(run, caster, defender, cast=16)
                self.assertTrue(run.match.combat.rockets)
                for _ in range(20):
                    fixture.step(run, caster, defender)
                self.assertTrue(phase_defenses.active(defender))
                self.assertEqual(health(run, defender).total, before)
                self.assertFalse(run.match.combat.is_stunned(defender))

    def test_phased_reaper_and_moira_cannot_melee_symmetra(self):
        for hero in phase_defenses.PHASES:
            with self.subTest(hero=hero):
                run, owner, defender, step = self.scene(hero)
                fight = run.match.combat
                angles = aim(fight._eye(defender), (0, 2, 0))
                before = health(run, owner).total
                step(defense=8)
                self.assertTrue(phase_defenses.active(defender))
                run.step({defender: (*angles, 0x800)})
                step(count=15)
                self.assertEqual(health(run, owner).total, before)
                for _ in range(210):
                    if not phase_defenses.active(defender):
                        break
                    step()
                step(count=45)
                self.assertEqual(health(run, owner).total, before)
                run.step({defender: (*angles, 0x800)})
                step(count=15)
                self.assertEqual(health(run, owner).total, before - 30)

    @unittest.skipUnless(os.environ.get("ORPHEUS_EXTRA_GRAPH_BUNDLES"), "requires summon graph shard")
    def test_sentry_clears_existing_slow_and_reacquires_after_phase(self):
        for hero in phase_defenses.PHASES:
            with self.subTest(hero=hero):
                run, owner, defender, step = self.scene(hero, (2, 1, 1))
                fight = run.match.combat
                with patch.object(fight, "_collision", return_value=wall()):
                    for _ in range(40):
                        run.step({owner: (0, 0, 8)})
                    [sentry] = fight.sentries.sentries
                    step(count=40)
                    self.assertIsNotNone(sentry.slow)
                    step(defense=8)
                    self.assertTrue(phase_defenses.active(defender))
                    before = health(run, defender).total
                    step(count=5)
                    self.assertIsNone(sentry.target)
                    self.assertIsNone(sentry.slow)
                    self.assertEqual(health(run, defender).total, before)
                    for _ in range(210):
                        if not phase_defenses.active(defender):
                            break
                        step()
                    self.assertFalse(phase_defenses.active(defender))
                    step(count=5)
                    self.assertIsNotNone(sentry.slow)
                    self.assertLess(health(run, defender).total, before)

    @unittest.skipUnless(os.environ.get("ORPHEUS_EXTRA_GRAPH_BUNDLES"), "requires summon graph shard")
    def test_sentry_gap_across_phase_or_cryo_expiry_matches_contiguous_damage(self):
        for hero, duration in ((0x02E0000000000002, 3), (0x02E00000000001A2, .75),
                               (cryo.HERO, 4)):
            with self.subTest(hero=hero):
                contiguous = self.sentry_damage_across_defense_expiry(hero, duration, 1)
                self.assertAlmostEqual(contiguous, 5 * 40 * combat.TICK, places=4)
                gaps = (16, 40) if duration < 1 else (16, 64, 80)
                for gap in gaps:
                    with self.subTest(gap=gap):
                        self.assertAlmostEqual(
                            self.sentry_damage_across_defense_expiry(hero, duration, gap),
                            contiguous, places=4,
                        )

    def sentry_damage_across_defense_expiry(self, hero, duration, gap):
        run, owner, defender, step = self.scene(hero, (2, 1, 1))
        fight = run.match.combat
        protected = cryo.active if hero == cryo.HERO else phase_defenses.active
        with patch.object(fight, "_collision", return_value=wall()):
            for _ in range(40):
                run.step({owner: (0, 0, 8)})
            step(count=40)
            [sentry] = fight.sentries.sentries
            self.assertIsNotNone(sentry.slow)
            step(defense=8)
            self.assertTrue(protected(defender))
            final_tick = run.frame + math.ceil(duration / combat.TICK) + 5
            damage_before = owner.stats.value(stats.HERO_DAMAGE)
            run.frame += 1
            run.match.update(run.frame * combat.TICK, run.frame)
            self.assertIsNone(sentry.target)
            self.assertIsNone(sentry.slow)
            while run.frame < final_tick - gap:
                run.frame += 1
                run.match.update(run.frame * combat.TICK, run.frame)
            if gap > 1:
                self.assertTrue(protected(defender))
                self.assertEqual(owner.stats.value(stats.HERO_DAMAGE), damage_before)
            run.frame += gap
            run.match.update(run.frame * combat.TICK, run.frame)
            self.assertFalse(protected(defender))
            self.assertEqual(sentry.target, defender.body)
            self.assertIsNotNone(sentry.slow)
            damage = owner.stats.value(stats.HERO_DAMAGE)
            run.match.update(run.frame * combat.TICK, run.frame)
            self.assertEqual(owner.stats.value(stats.HERO_DAMAGE), damage)
            return damage - damage_before
