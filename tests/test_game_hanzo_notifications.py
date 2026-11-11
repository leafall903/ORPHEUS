"""Actual Sonic graph with a controlled interrupt/reentry boundary, then real input.

The explicit BooleanSwitch end/reentry isolates a scheduler boundary. It does
not establish a natural enemy-control path or original-client presentation.
"""

import unittest
from unittest.mock import patch

import test_game_hanzo_sonic_cooldown as sonic_tests

from ow174.game import bodytrace, movelog
from ow174.game.script import runtime


class HanzoNotificationTests(unittest.TestCase):
    def setUp(self):
        for module in (bodytrace, movelog):
            self.enterContext(patch.object(module, 'ENABLED', False))

    def test_sonic_prepared_stack_updates_after_canceled_change_and_reentry(self):
        case = sonic_tests.SonicCooldownTests()
        run, hero, target, instance = case.arena()
        case.step(run, hero, target, 8)
        case.step(run, hero, target)
        component = instance.component
        switch = instance.node_state(instance.graph.node(22))
        self.assertTrue(switch.current)
        self.assertTrue(component.entity_var(10467).value())
        changed = runtime.Component.changed
        interrupted = []

        def interrupt_after_cleanup(owner, var, before, **kwargs):
            value = changed(owner, var, before, **kwargs)
            if (owner is component and var is component.entity_var(627)
                    and not var.value() and not interrupted):
                interrupted.append(True)
                self.assertTrue(switch.flags_changed)
                switch.end(False)
                switch.request(runtime.BEGIN)
            return value

        # Interrupt only this watcher after real toggle input changes627, before
        # dispatching its queued notification. No variable or graph is patched.
        with patch.object(runtime.Component, 'changed', new=interrupt_after_cleanup):
            case.step(run, hero, target, 8)
            case.step(run, hero, target)
        self.assertTrue(interrupted)
        self.assertFalse(component.entity_var(627).value())
        self.assertFalse(switch.current)
        case.step(run, hero, target, 8)
        case.step(run, hero, target)
        self.assertTrue(component.entity_var(627).value())
        self.assertTrue(switch.current)
        self.assertTrue(component.entity_var(10467).value())
        case.step(run, hero, target, 8)
        case.step(run, hero, target)
        self.assertFalse(switch.current)
        self.assertFalse(component.entity_var(10467).value())

    def test_storm_activation_stop_retains_owned_arrows_then_restores_bow(self):
        for interrupt in (False, True):
            with self.subTest(interrupt=interrupt):
                case = sonic_tests.SonicCooldownTests()
                run, hero, target, _ = case.arena()
                fight = run.match.combat
                ability = next(i for i in hero.body_script.component.instances.values()
                               if i.graph.index == 0x146A)
                case.step(run, hero, target, 16)
                case.step(run, hero, target)
                with patch.object(fight.projectiles, 'fired', wraps=fight.projectiles.fired) as fired:
                    for _ in range(140):
                        case.step(run, hero, target, 1)
                        if interrupt and any(c.args[3] == 0x040000000000270F for c in fired.call_args_list):
                            break
                    case.step(run, hero, target)
                    if interrupt:
                        self.assertTrue(ability.states[0].flags & 1)
                        ability.states[0].stop()  # controlled host interruption, not an enemy-control claim
                        ability.component.run_now()
                    for _ in range(20):
                        case.step(run, hero, target)
                    storms = [call for call in fired.call_args_list if call.args[3] == 0x040000000000270F]
                    self.assertEqual(len(storms), 1 if interrupt else 5)
                    if interrupt:
                        # The activation scope is stopped, but its separately
                        # granted weapon mode retains the four unused arrows.
                        self.assertFalse(ability.states[0].flags & 1)
                        for _ in range(140):
                            case.step(run, hero, target, 1)
                        case.step(run, hero, target)
                        for _ in range(20):
                            case.step(run, hero, target)
                        storms = [c for c in fired.call_args_list if c.args[3] == 0x040000000000270F]
                        self.assertEqual(len(storms), 5)
                    self.assertFalse(ability.states[0].flags & 1)
                    self.assertFalse(fight.vitals[target.body].dead)
                    health = fight.vitals[target.body].total
                    before = fired.call_count
                    with patch.object(fight, '_damage', wraps=fight._damage) as damage:
                        case.draw(run, hero, target)
                    self.assertEqual(fired.call_count, before + 1)
                    self.assertEqual(fired.call_args.args[1].instance.graph.index, 0x00B9)
                    self.assertEqual(damage.call_args.args[2], 125)
                    self.assertEqual(fight.vitals[target.body].total, health - 125)

    def test_stacked_melee_completion_and_stop_restore_bow_after_spent_arrow(self):
        for interrupt in (False, True):
            with self.subTest(interrupt=interrupt):
                case = sonic_tests.SonicCooldownTests()
                run, hero, target, _ = case.arena()
                fight = run.match.combat
                case.draw(run, hero, target)
                self.assertEqual(fight.vitals[target.body].total, 475)
                case.step(run, hero, target, 0x800)
                case.step(run, hero, target)
                instance = next(i for i in hero.body_script.component.instances.values()
                                if i.graph.index == 0x43)
                melee = instance.node_state(instance.graph.node(14))
                self.assertTrue(melee.flags & 3)
                if interrupt:
                    melee.stop()
                    melee.instance.component.run_now()
                for _ in range(40):
                    case.step(run, hero, target)
                self.assertFalse(melee.flags & 3)
                with patch.object(fight.projectiles, 'fired', wraps=fight.projectiles.fired) as fired:
                    case.draw(run, hero, target)
                self.assertEqual(fired.call_count, 1)
                self.assertEqual(fired.call_args.args[1].instance.graph.index, 0x00B9)
                self.assertEqual(fight.vitals[target.body].total, 350)
