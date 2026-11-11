"""Actual Wraith/Fade inputs against Sombra; defense policy is reconstructed."""

import unittest

import test_game_sombra_combat as sombra

from ow174.game import combat, phase_defenses
from ow174.game.commands import Command
from ow174.game.content import heroes
from ow174.game.script import nodes

REAPER, MOIRA = 0x02E0000000000002, 0x02E00000000001A2


class SombraPhaseTests(unittest.TestCase):
    def scene(self, hero):
        f = sombra.SombraCombatTests()
        f.setUp()
        self.assertTrue(f.run.match.switch_hero(f.b, heroes()[hero]))
        f.b.mover.position[:] = (0, 0, 1.5)
        f.b.mover.body = None
        f.a.body_script.ult_seconds = 0
        f.press(0, 50)
        return f

    def cast(self, f):
        f.press(8, shooter=f.b, target=f.a)
        self.assertTrue(phase_defenses.active(f.b))
        index = phase_defenses.PHASES[f.b.hero.guid][0]
        return next(i for i in f.b.body_script.component.instances.values() if i.graph.index == index)

    def finish(self, f):
        for _ in range(250):
            if not phase_defenses.active(f.b):
                return
            f.press(0)
        self.fail('authored phase lifetime did not end')

    def test_actual_inputs_use_authored_duration_and_exclude_recovery(self):
        for hero, duration, cooldown in ((REAPER, 3000, 8.), (MOIRA, 750, 6.)):
            with self.subTest(hero=hero):
                f = self.scene(hero)
                root = self.cast(f)
                self.assertEqual(root.state(3).ms, duration)
                self.assertEqual(root.vars[472].value(), cooldown)
                self.finish(f)
                self.assertTrue(f.b.body_script.component.entity_var(10467).value())
                self.assertFalse(phase_defenses.active(f.b))

    def test_pistol_and_melee_cannot_damage_or_report_hits_during_phase(self):
        for hero in (REAPER, MOIRA):
            for button in (1, 0x800):
                with self.subTest(hero=hero, button=button):
                    f = self.scene(hero)
                    before = f.health(f.b)
                    self.cast(f)
                    f.press(button, 25)
                    self.assertTrue(phase_defenses.active(f.b))
                    self.assertEqual(f.health(f.b), before)
                    self.assertFalse(f.a.client.messages(combat.HIT))

    def test_hack_cannot_acquire_phase_or_spend_cooldown(self):
        for hero in (REAPER, MOIRA):
            with self.subTest(hero=hero):
                f = self.scene(hero)
                self.cast(f)
                f.press(2, 25)
                self.assertFalse(f.weapon().vars[375].value())
                self.assertEqual(f.weapon().state(53).remaining(), 0)
                self.assertEqual(f.hack_applications(), [])

    def test_emp_spends_owner_charge_without_applying_to_phase(self):
        for hero in (REAPER, MOIRA):
            with self.subTest(hero=hero):
                f = self.scene(hero)
                f.a.body_script.set_ult_charge(1400)
                self.cast(f)
                f.press(32, 30)
                self.assertTrue(phase_defenses.active(f.b))
                self.assertEqual(f.hack_applications(), [])
                self.assertNotIn(f.b.body, f.run.match.combat.hacks)
                self.assertLess(f.a.body_script.component.entity_var(342).value(), 1400)

    def test_direct_damage_and_control_cannot_bypass_phase(self):
        for hero in (REAPER, MOIRA):
            with self.subTest(hero=hero):
                f = self.scene(hero)
                fight = f.run.match.combat
                target = next(t for t in fight._targets() if t.owner is f.b)
                before = f.health(f.b)
                self.cast(f)
                fight._damage(f.a, target, 999, False, None, f.run.frame)
                fight._hit_effect(target, combat.FLASHBANG_EFFECT)
                self.assertEqual(f.health(f.b), before)
                self.assertFalse(fight.is_stunned(f.b))

    def test_actual_hack_and_emp_block_phase_then_expiry_restores_input(self):
        for hero in (REAPER, MOIRA):
            for button in (2, 32):
                with self.subTest(hero=hero, button=button):
                    f = self.scene(hero)
                    fight = f.run.match.combat
                    f.a.body_script.set_ult_charge(1400 if button == 32 else 0)
                    f.press(button, 50 if button == 32 else 100)
                    effect, = fight.hacks[f.b.body]
                    f.press(8, shooter=f.b, target=f.a)
                    self.assertFalse(phase_defenses.active(f.b))
                    f.press(0, effect.until_tick - f.run.frame + 2)
                    self.assertNotIn(f.b.body, fight.hacks)
                    self.assertFalse(phase_defenses.active(f.b))
                    self.cast(f)

    def test_emp_checks_phase_at_pulse_after_windup(self):
        for hero in (REAPER, MOIRA):
            for phase_at_pulse in (True, False):
                with self.subTest(hero=hero, phase_at_pulse=phase_at_pulse):
                    f = self.scene(hero)
                    fight = f.run.match.combat
                    f.a.body_script.set_ult_charge(1400)
                    emp = next(i for i in f.a.body_script.component.instances.values()
                               if i.graph.index == 0x0B31)
                    pulse = emp.node_state(emp.graph.node(40))
                    if not phase_at_pulse:
                        clock = self.cast(f).state(3)
                        remaining = clock.start + clock.ms - clock.instance.component.now
                        f.press(0, remaining // clock.instance.component.frame_ms - 8)
                        self.assertTrue(phase_defenses.active(f.b))
                    f.press(32)
                    f.press(0, 9)
                    self.assertEqual(pulse.counter, 0)
                    self.assertNotIn(f.b.body, fight.hacks)
                    if phase_at_pulse:
                        self.cast(f)
                    else:
                        self.assertFalse(phase_defenses.active(f.b))
                    f.press(0, 25)
                    self.assertEqual(pulse.counter, 1)
                    self.assertEqual(phase_defenses.active(f.b), phase_at_pulse)
                    self.assertEqual(f.b.body in fight.hacks, not phase_at_pulse)
                    self.assertEqual(f.a.body_script.component.entity_var(342).value(), 0)

    def test_pistol_hits_after_authored_phase_ends(self):
        for hero in (REAPER, MOIRA):
            with self.subTest(hero=hero):
                f = self.scene(hero)
                self.cast(f)
                self.finish(f)
                before = f.health(f.b)
                f.press(1)
                self.assertEqual(f.health(f.b), before - 8)

    def test_unbound_boolean_and_replaced_body_do_not_grant_phase(self):
        for hero in (REAPER, MOIRA):
            with self.subTest(hero=hero):
                f = self.scene(hero)
                f.b.body_script.component.set(phase_defenses.PHASES[hero][1], True)
                self.assertFalse(phase_defenses.active(f.b))
                self.cast(f)
                self.assertTrue(f.run.match.switch_hero(f.b, heroes()[0x02E000000000006E]))
                self.assertFalse(phase_defenses.active(f.b))
                f.b.mover.position[:] = (0, 0, 1.5)
                f.b.mover.body = None
                f.press(0, 30)
                before = f.health(f.b)
                f.press(1)
                self.assertEqual(f.health(f.b), before - 8)

    def test_phase_start_interrupts_an_existing_hack_channel(self):
        for hero in (REAPER, MOIRA):
            with self.subTest(hero=hero):
                f = self.scene(hero)
                f.press(2, 15)
                self.assertTrue(f.weapon().vars[375].value())
                self.cast(f)
                f.press(2, 25)
                self.assertTrue(phase_defenses.active(f.b))
                self.assertEqual(f.hack_applications(), [])
                self.assertNotIn(f.b.body, f.run.match.combat.hacks)

    def test_pistol_passes_phased_body_and_hits_actual_rear_player(self):
        for hero in (REAPER, MOIRA):
            with self.subTest(hero=hero):
                f = self.scene(hero)
                from test_game_combat import SOLDIER, in_world

                rear = f.run.match.add_player(3, 'Rear', SOLDIER, 2, False)
                in_world(f.run.match, rear)
                f.run.players.append(rear)
                f.run.match.switch_hero(rear, heroes()[SOLDIER])
                rear.mover.position[:] = (0, 0, 4)
                rear.mover.body = None
                f.press(0, 30)
                before = f.health(rear)
                phase_health = f.health(f.b)
                self.cast(f)
                f.press(1, target=rear)
                self.assertEqual(f.health(f.b), phase_health)
                self.assertEqual(f.health(rear), before - 8)

    def test_phase_cannot_melee_sombra_and_does_not_backfill_on_exit(self):
        for hero in (REAPER, MOIRA):
            with self.subTest(hero=hero):
                f = self.scene(hero)
                self.cast(f)
                before = f.health(f.a)
                f.press(0x800, 25, shooter=f.b, target=f.a)
                self.assertEqual(f.health(f.a), before)
                self.finish(f)
                f.press(0, 30)
                self.assertEqual(f.health(f.a), before)
                f.press(0x800, 25, shooter=f.b, target=f.a)
                self.assertEqual(f.health(f.a), before - 30)

    def test_world_replay_retires_phase_and_cooldown_with_fresh_recast(self):
        for hero in (REAPER, MOIRA):
            cooldowns = []
            for gap in (False, True):
                with self.subTest(hero=hero, gap=gap):
                    f = self.scene(hero)
                    match = f.run.match
                    root = self.cast(f)
                    ability = next(state for state in root.states.values()
                                   if isinstance(state, nodes.Ability))
                    start = f.run.frame
                    early = start + 251
                    if gap:
                        match.update(early * combat.TICK, early)
                    else:
                        f.press(0, 250)
                    self.assertFalse(phase_defenses.active(f.b))
                    remaining = ability.remaining()
                    self.assertGreater(remaining, 0)
                    cooldowns.append(remaining)

                    if gap:
                        mover = f.b.mover
                        f.b.take_commands([Command(early, yaw=mover.yaw, pitch=mover.pitch, buttons=8)])
                        self.assertFalse(phase_defenses.active(f.b))
                        late = start + 750
                        match.update(late * combat.TICK, late)
                        self.assertEqual(ability.remaining(), 0)
                        last = mover.last_command
                        f.b.take_commands([Command(last.frame, yaw=last.yaw, pitch=last.pitch, buttons=8)])
                        self.assertEqual(mover.last_command, last)
                        self.assertFalse(phase_defenses.active(f.b))
                        f.b.take_commands([Command(late, yaw=last.yaw, pitch=last.pitch, buttons=8)])
                        self.assertTrue(phase_defenses.active(f.b))
                    else:
                        f.press(8, shooter=f.b, target=f.a)
                        self.assertFalse(phase_defenses.active(f.b))
                        f.press(0, 498)
                        self.assertEqual(ability.remaining(), 0)
                        self.cast(f)
            self.assertEqual(cooldowns[:1], cooldowns[1:])

    def test_authored_cooldown_rejects_early_input_and_allows_fresh_cast(self):
        for hero in (REAPER, MOIRA):
            with self.subTest(hero=hero):
                f = self.scene(hero)
                self.cast(f)
                self.finish(f)
                f.press(0, 30)
                f.press(8, shooter=f.b, target=f.a)
                self.assertFalse(phase_defenses.active(f.b))
                f.press(0, 550)
                self.cast(f)
