"""Actual00C1/00B9 Sonic prepare/cancel and12s cooldown/input-buffer boundaries."""

import os
import unittest
from pathlib import Path
from unittest.mock import patch

import test_game_hanzo as hero_tests
from test_game_combat import aim

from ow174.game import bodytrace, movelog, sonic
from ow174.game.content import heroes


class SonicCooldownTests(unittest.TestCase):
    def setUp(self):
        for module in (bodytrace, movelog):
            self.enterContext(patch.object(module, "ENABLED", False))

    def arena(self):
        run, hero, target = hero_tests.HanzoCombatTests().arena()
        point = tuple(target.mover.position)
        tank = next(h for h in heroes().values() if h.name == "Roadhog")
        run.match.switch_hero(target, tank)
        target.mover.position[:] = point
        target.mover.body = None
        path = os.environ.get("ORPHEUS_HANZO_SONIC_PROFILE")
        if path:
            run.match.combat.configure_sonic(
                sonic.load_profile(Path(path), reconstructed_geometry=True),
            )
        instance = next(i for i in hero.body_script.component.instances.values() if i.graph.index == 0xC1)
        self.assertEqual(instance.vars[472].value(), 12)
        self.assertAlmostEqual(instance.graph.node(12).field("m_246438AD")["m_value"], 0.15, places=6)
        return run, hero, target, instance

    @staticmethod
    def step(run, hero, target, buttons=0):
        x, y, z = target.mover.position
        yaw, pitch = aim(run.match.combat._eye(hero), (x, y + 1, z))
        run.step({hero: (yaw, pitch, buttons)})

    def draw(self, run, hero, target):
        for _ in range(70):
            self.step(run, hero, target, 1)
        self.step(run, hero, target)
        released = run.frame
        for _ in range(15):
            self.step(run, hero, target)
        return released

    def first_shot(self, run, hero, target):
        self.step(run, hero, target, 8)
        self.step(run, hero, target)
        return self.draw(run, hero, target)

    def test_prepare_toggle_cancel_spends_no_cooldown_but_release_starts_twelve_seconds(self):
        run, hero, target, instance = self.arena()
        fight = run.match.combat
        with patch.object(fight.projectiles, "fired", wraps=fight.projectiles.fired) as fire:
            self.step(run, hero, target, 8)
            self.step(run, hero, target)
            self.assertEqual(instance.component.entity_var(627).value(), 1)
            self.assertEqual(instance.vars[61].value(), 0)
            self.step(run, hero, target, 8)
            self.step(run, hero, target)
            self.assertEqual(instance.component.entity_var(627).value(), 0)
            self.assertEqual(instance.vars[61].value(), 0)
            self.assertFalse(fire.called)
            released = self.first_shot(run, hero, target)
        self.assertEqual([call.args[3] for call in fire.call_args_list], [sonic.DEFINITIONS[0]])
        self.assertEqual(instance.component.entity_var(627).value(), 0)
        self.assertAlmostEqual(instance.vars[61].value(), 12 - (run.frame - released) * 0.016 - 0.015,
                               places=4)
        self.assertEqual(fight.vitals[target.body].total, 475)

    def test_expired_early_tap_does_not_autostart_and_fresh_twelve_second_press_reuses(self):
        run, hero, target, instance = self.arena()
        fight = run.match.combat
        with patch.object(fight.projectiles, "fired", wraps=fight.projectiles.fired) as fire:
            released = self.first_shot(run, hero, target)
            self.step(run, hero, target, 8)  # far more than the authored150ms before ready
            self.step(run, hero, target)
            while run.frame < released + 749:
                self.step(run, hero, target)
                self.assertEqual(instance.component.entity_var(627).value(), 0)
            self.assertGreater(instance.vars[61].value(), 0)
            self.step(run, hero, target)
            self.assertEqual(instance.vars[61].value(), 0)
            self.assertEqual(instance.component.entity_var(627).value(), 0)
            self.step(run, hero, target, 8)
            self.step(run, hero, target)
            self.assertEqual(instance.component.entity_var(627).value(), 1)
            self.draw(run, hero, target)
        self.assertEqual([call.args[3] for call in fire.call_args_list], [sonic.DEFINITIONS[0]] * 2)
        self.assertEqual(fight.vitals[target.body].total, 350)

    def test_near_deadline_tap_buffers_without_activating_during_cooldown(self):
        run, hero, target, instance = self.arena()
        released = self.first_shot(run, hero, target)
        while run.frame < released + 744:
            self.step(run, hero, target)
        self.step(run, hero, target, 8)  #80ms before the authored12s deadline
        self.assertEqual(instance.component.entity_var(627).value(), 0)
        while run.frame < released + 749:
            self.step(run, hero, target)
            self.assertGreater(instance.vars[61].value(), 0)
            self.assertEqual(instance.component.entity_var(627).value(), 0)
        self.step(run, hero, target)
        self.assertEqual(instance.vars[61].value(), 0)
        self.assertEqual(instance.component.entity_var(627).value(), 1)
        self.draw(run, hero, target)
        self.assertEqual(run.match.combat.vitals[target.body].total, 350)

    def test_secondary_and_reload_cancel_draw_but_retain_prepared_sonic_without_cooldown(self):
        for cancel in (2, 0x400):
            with self.subTest(cancel=cancel):
                run, hero, target, instance = self.arena()
                fight = run.match.combat
                with patch.object(fight.projectiles, "fired", wraps=fight.projectiles.fired) as fire:
                    self.step(run, hero, target, 8)
                    self.step(run, hero, target)
                    for _ in range(20):
                        self.step(run, hero, target, 1)
                    for _ in range(15):
                        self.step(run, hero, target, 1 | cancel)
                    self.step(run, hero, target)
                    self.assertFalse(fire.called)
                    self.assertEqual(instance.vars[61].value(), 0)
                    self.assertEqual(instance.component.entity_var(627).value(), 1)
                    self.draw(run, hero, target)
                self.assertEqual([call.args[3] for call in fire.call_args_list], [sonic.DEFINITIONS[0]])
                self.assertEqual(fight.vitals[target.body].total, 475)
                self.assertGreater(instance.vars[61].value(), 11)

    def test_held_retry_survives_cooldown_and_prepares_once(self):
        run, hero, target, instance = self.arena()
        self.first_shot(run, hero, target)
        state = next(s for s in instance.states.values() if s.node.pos == 12)
        with patch.object(state, 'start_ability', wraps=state.start_ability) as starts:
            for _ in range(800):
                if instance.component.entity_var(627).value():
                    break
                self.step(run, hero, target, 8)
            self.assertEqual(instance.vars[61].value(), 0)
            self.assertEqual(instance.component.entity_var(627).value(), 1)
            self.assertEqual(starts.call_count, 1)
            for _ in range(10):
                self.step(run, hero, target, 8)
            self.assertEqual(instance.component.entity_var(627).value(), 1)
            self.assertEqual(starts.call_count, 1)

    def test_dead_prepared_owner_cannot_release_and_new_body_resets_preparation(self):
        run, hero, target, _instance = self.arena()
        self.step(run, hero, target, 8)
        self.step(run, hero, target)
        for _ in range(20):
            self.step(run, hero, target, 1)
        fight = run.match.combat
        victim = next(t for t in fight._targets() if t.owner is hero)
        fight._damage(target, victim, 200, False, None, run.frame)
        with patch.object(fight.projectiles, "fired", wraps=fight.projectiles.fired) as fire:
            self.step(run, hero, target)
            self.assertFalse(fire.called)
            old = hero.body_script
            run.match.switch_hero(hero, hero.hero)  # explicit fresh body lifecycle after death
            self.assertTrue(old.retired)
            current = next(i for i in hero.body_script.component.instances.values() if i.graph.index == 0xC1)
            self.assertEqual(current.vars[61].value(), 0)
            self.assertFalse(current.component.entity_var(627).value())
            self.first_shot(run, hero, target)
        self.assertEqual([call.args[3] for call in fire.call_args_list], [sonic.DEFINITIONS[0]])
        self.assertEqual(fight.vitals[target.body].total, 475)
