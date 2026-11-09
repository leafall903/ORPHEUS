"""Actual Flashbang across Bastion launch and reload recovery."""
import unittest

import test_bastion_combat as kit
import test_bastion_ironclad_transitions as ironclad
from test_bastion_flashbang import MCCREE
from test_game_combat import aim

from ow174.game import combat


class BastionControlCadenceTests(unittest.TestCase):
    def arena(self, mode):
        case = kit.BastionCombatTests()
        case.fixture(enemy_position=(3, 1, 0), bystander_position=(0, 1, 10), enemy_hero=MCCREE)
        if mode == "tank":
            case.tank()
        elif mode == "sentry":
            case.configure()
        case.command_aim = aim(case.fight._eye(case.player), (0, 2, 10))
        caster_aim = aim(case.fight._eye(case.enemy), (0, 2, 0))
        target = combat.Vitals(case.bystander.body, 10000)
        case.fight.vitals[case.bystander.body] = target
        case.fight._sync_body_health(case.bystander, target)
        return case, caster_aim, target

    def trace_tank(self, cast_frame, buttons):
        case, caster_aim, target = self.arena("tank")
        start = case.run.frame
        seen, launches, impacts = [], [], []
        control = None
        for frame in range(1, 111):
            before = target.total
            case.run.step({case.player: (*case.command_aim, buttons(frame)),
                           case.enemy: (*caster_aim, 16 if frame == cast_frame else 0)})
            if control is None and case.fight.is_stunned(case.player):
                control = frame, case.fight.stuns[case.player.body] - start
            for rocket in case.fight.rockets:
                if rocket.shooter is case.player and not any(rocket is old for old in seen):
                    seen.append(rocket)
                    launches.append(frame)
            if target.total != before:
                impacts.append((frame, before - target.total))
        self.assertEqual(case.player.body_script.component.entity_var(1216).value(), 6)
        return control, launches, impacts

    def test_tank_waits_for_both_control_and_fire_recovery(self):
        for cast, control, second, impact in (
            (1, (6, 56), 63, 74),
            (10, (15, 65), 65, 76),
        ):
            with self.subTest(cast=cast):
                observed = self.trace_tank(cast, lambda frame: 1)
                self.assertEqual(observed, (control, [1, second], [(11, 205), (impact, 205)]))

    def test_tank_tap_during_control_does_not_launch_after_recovery(self):
        control, launches, impacts = self.trace_tank(1, lambda frame: int(frame in (1, 25, 80)))
        self.assertEqual(control, (6, 56))
        self.assertEqual(launches, [1, 80])
        self.assertEqual(impacts, [(11, 205), (91, 205)])


    def test_recon_and_sentry_reload_clock_survives_control_before_and_after_refill(self):
        for mode, graph, wait_index, refill, recovery, clip, damage in (
            ("recon", 0x012E, 21, 46, 94, 35, 20),
            ("sentry", 0x013A, 26, 97, 125, 300, 15),
        ):
            for cast in (1, refill + 4):
                with self.subTest(mode=mode, cast=cast):
                    case, caster_aim, target = self.arena(mode)
                    weapon = next(i for i in case.player.body_script.component.instances.values()
                                  if i.graph.index == graph)
                    case.step(1, 10)
                    case.step(0, 2)
                    case.step(0x400)
                    for _ in range(20):
                        wait = weapon.states.get(wait_index)
                        if wait is not None and wait.active:
                            break
                        case.step()
                    self.assertTrue(wait.active)
                    self.assertLess(weapon.vars[53].value(), clip)
                    before = target.total
                    ready = max(recovery, cast + 55)
                    first_refill = None
                    for frame in range(1, ready + 1):
                        case.run.step({case.player: (*case.command_aim, 1),
                                       case.enemy: (*caster_aim, 16 if frame == cast else 0)})
                        if first_refill is None and weapon.vars[53].value() == clip:
                            first_refill = frame
                        if frame < ready:
                            self.assertEqual(target.total, before)
                    self.assertEqual(first_refill, refill)
                    self.assertEqual(before - target.total, damage)
                    self.assertEqual(weapon.vars[53].value(), clip - 1)
                    self.assertFalse(case.fight.is_stunned(case.player))


    def test_repair_during_each_transform_defers_held_input_but_drops_an_early_tap(self):
        fixtures = ironclad.BastionIroncladTransitionTests()
        for entered, final, boundary in ((1, 2, 62), (3, 0, 31), (4, 6, 93), (5, 6, 93), (7, 0, 63)):
            for held in (False, True):
                with self.subTest(entered=entered, held=held):
                    case, mode = fixtures.prepare(entered)
                    health = case.health(case.player)
                    health.damage(150)
                    case.fight._sync_body_health(case.player, health)
                    before = health.total
                    script = case.player.body_script
                    repair = next(i for i in script.component.instances.values() if i.graph.index == 0x153)
                    case.step(2)
                    case.step(2 if held else 0, boundary - 2)
                    self.assertEqual(mode.value(), entered)
                    self.assertEqual(health.total, before)
                    self.assertEqual(repair.vars[87].value(), 100)
                    case.step(2 if held else 0, 42)
                    self.assertEqual(mode.value(), final)
                    if not held:
                        self.assertEqual(health.total, before)
                        self.assertEqual(repair.vars[87].value(), 100)
                        case.step(2, 42)
                    self.assertGreater(health.total, before)
                    self.assertLess(repair.vars[87].value(), 100)


if __name__ == "__main__":
    unittest.main()
