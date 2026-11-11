"""Actual Ball manual and automatic reload inputs and interruption paths."""

import unittest

import test_mccree_flashbang as flash_tests
import test_wrecking_ball_combat as ball_tests
import test_wrecking_ball_control as control_tests
import test_wrecking_ball_emp as emp_tests
import test_wrecking_ball_grapple as grapple_tests
import test_wrecking_ball_hack as hack_tests
import test_wrecking_ball_piledriver as pile_tests
from test_game_combat import KANEZAKA_DM, Run, aim
from test_game_graph_analysis import make_graph, node, output
from test_wrecking_ball_fireball import relocate

from ow174.game import ball, combat, sombra_emp
from ow174.game.content import heroes
from ow174.game.script import runtime
from ow174.game.script.driver import ULT_CHARGE, ULT_COST


class OutputBindingTests(unittest.TestCase):
    def test_last_shot_invalidates_the_empty_ammo_condition_after_equal_value_bake(self):
        ammo = {'$': 'STUConfigVarDynamic', 'm_identifier': 53, 'm_60DB8F99': 1}
        condition = {'$': 'STU_919FD47C', 'm_expression': {
            'm_opcodes': [8, 0, 6, 0, 37, 0], 'm_dynamicVars': [ammo], 'm_D99EF254': [0],
        }}
        def integer(value):
            return {'$': 'STUConfigVarInt', 'm_value': value}

        definition = make_graph(1, [
            node(0, 'STUStatescriptEntry', fields={'m_outPlug': output(
                [1, 'm_beginPlug'], [2, 'm_beginPlug'])}),
            node(1, 'STUStatescriptStateBooleanSwitch', state=0, fields={'m_condition': condition}),
            node(2, 'STUStatescriptStateWeaponVolley', state=1, fields={
                'm_24E351A4': ammo, 'm_numShotsPerSecond': integer(25),
                'm_A17BE89B': integer(3), 'm_A55BC904': integer(1),
            }),
        ], states=[1, 2], entries=[0])
        component = runtime.Component(123)
        component.set(53, 3)
        instance = component.create(definition)
        component.run_now()
        self.assertFalse(instance.states[0].current)
        component.run_frame(6)
        self.assertEqual(component.entity_var(53).value(), 0)
        self.assertFalse(instance.states[1].active)
        self.assertTrue(instance.states[0].current)


class BallReloadTests(unittest.TestCase):
    def empty(self):
        run, player, _, _, _ = ball_tests.BallCombatTests().scenario()
        weapon = next(i for i in player.body_script.component.instances.values() if i.graph.index == 0x1514)
        for _ in range(240):
            run.step({player: (-32768, 0, 1)})
            if weapon.vars[53].value() == 0:
                break
        self.assertEqual(weapon.vars[53].value(), 0)
        self.assertEqual(weapon.states[9].reported, 80)
        self.assertFalse(player.client.messages(combat.HIT))
        return run, player, weapon

    def test_held_primary_waits_for_automatic_reload_then_resumes_firing(self):
        run, player, weapon = self.empty()
        for _ in range(50):
            run.step({player: (-32768, 0, 1)})
            self.assertEqual(weapon.vars[53].value(), 0)
            self.assertFalse(weapon.states[9].active)
        for _ in range(150):
            run.step({player: (-32768, 0, 1)})
            if weapon.vars[53].value() > 0:
                break
        self.assertGreaterEqual(weapon.vars[53].value(), 79)
        # The graph fills the magazine before its separate reload-exit Wait20.
        # A held trigger must not bypass that remaining authored phase.
        self.assertTrue(weapon.states[20].active)
        for _ in range(100):
            run.step({player: (-32768, 0, 1)})
            if not weapon.states[20].active:
                break
            self.assertEqual(weapon.vars[53].value(), 80)
            self.assertFalse(weapon.states[9].active)
        self.assertFalse(weapon.states[20].active)
        for _ in range(15):
            run.step({player: (-32768, 0, 1)})
        self.assertTrue(weapon.states[9].active)
        self.assertLess(weapon.vars[53].value(), 79)
        self.assertFalse(player.client.messages(combat.HIT))

    def test_released_primary_finishes_automatic_reload_without_extra_shots(self):
        run, player, weapon = self.empty()
        for _ in range(150):
            run.step({})
        self.assertEqual(weapon.vars[53].value(), 80)
        self.assertFalse(weapon.states[9].active)
        run.step({player: (-32768, 0, 1)})
        self.assertEqual(weapon.vars[53].value(), 79)

    def test_multiple_automatic_reload_cycles_keep_notifications_and_ammo_live(self):
        run, player, weapon = self.empty()
        volley = weapon.states[9]
        reports = {(volley.counter, volley.start): volley.reported}
        for _ in range(800):
            run.step({player: (-32768, 0, 1)})
            self.assertGreaterEqual(weapon.vars[53].value(), 0)
            self.assertLessEqual(weapon.vars[53].value(), 80)
            key = volley.counter, volley.start
            reports[key] = max(reports.get(key, 0), volley.reported)
        self.assertGreaterEqual(len(reports), 3)
        self.assertGreaterEqual(sum(value == 80 for value in reports.values()), 2)
        self.assertFalse(player.client.messages(combat.HIT))

    def test_sleep_blocks_fire_during_manual_and_automatic_reload(self):
        phases = ('manual-before-fill', 'manual-after-fill', 'automatic-before-fill', 'automatic-after-fill')
        for phase in phases:
            with self.subTest(phase=phase):
                run, player, caster, _, _ = ball_tests.BallCombatTests().scenario(distance=3)
                position = tuple(caster.mover.position)
                run.match.switch_hero(caster, heroes()[control_tests.ANA])
                relocate(run, caster, position)
                for _ in range(35):
                    run.step({})
                weapon = next(i for i in player.body_script.component.instances.values()
                              if i.graph.index == 0x1514)
                ammo = weapon.vars[53]
                automatic = phase.startswith('automatic')
                after_fill = phase.endswith('after-fill')
                for _ in range(240 if automatic else 30):
                    run.step({player: (-32768, 0, 1)})
                    if automatic and ammo.value() == 0:
                        break
                if not automatic:
                    run.step({})
                    run.step({player: (0, 0, 1024)})
                if after_fill:
                    for _ in range(85):
                        if ammo.value() == 80:
                            break
                        run.step({})
                wait = 20 if after_fill else 21
                expected_ammo = 80 if wait == 20 else (0 if automatic else 67)
                _, deadline = control_tests.BallControlTests().sleep_dart(run, caster, player)
                self.assertTrue(weapon.states[wait].active)
                self.assertEqual(ammo.value(), expected_ammo)

                fight = run.match.combat
                target = next(t for t in fight._targets() if t.owner is caster)
                vitals = fight.vitals_of(target)
                health = vitals.total
                angles = aim(fight._eye(player), (target.feet[0], target.feet[1] + 1, target.feet[2]))
                while run.frame + 1 < deadline:
                    run.step({player: (*angles, 1)})
                    self.assertFalse(weapon.states[9].active)
                    self.assertEqual(vitals.total, health)
                self.assertEqual(ammo.value(), 80)
                self.assertFalse(weapon.states[20].active)
                self.assertFalse(weapon.states[21].active)

                run.step({player: (*angles, 1)})
                self.assertFalse(fight.is_stunned(player))
                self.assertEqual(ammo.value(), 79)
                self.assertEqual(vitals.total, health - 5)

    def test_flashbang_recovery_does_not_bypass_reload_exit(self):
        for automatic in (False, True):
            for filled in (False, True):
                with self.subTest(automatic=automatic, filled=filled):
                    self.check_flashbang_reload(automatic=automatic, filled=filled)

    def check_flashbang_reload(self, *, automatic, filled):
        run, player, caster, _, _ = ball_tests.BallCombatTests().scenario(distance=3)
        position = tuple(caster.mover.position)
        run.match.switch_hero(caster, heroes()[0x02E0000000000042])
        relocate(run, caster, position)
        for _ in range(35):
            run.step({})
        weapon = next(i for i in player.body_script.component.instances.values() if i.graph.index == 0x1514)
        phase = self.enter_reload(run, player, weapon, automatic=automatic, filled=filled)
        ammo = weapon.vars[53].value()
        if filled:
            # Let some of Wait20 elapse so this stun outlasts the reload.
            for _ in range(20):
                run.step({})
        flash_tests.FlashbangTests().cast(run, caster, player)
        self.assertTrue(phase.active)
        self.assertEqual(weapon.vars[53].value(), ammo)

        fight = run.match.combat
        target = next(t for t in fight._targets() if t.owner is caster)
        vitals = fight.vitals_of(target)
        health = vitals.total
        angles = aim(fight._eye(player), (target.feet[0], target.feet[1] + 1, target.feet[2]))
        saw_stun_only = saw_reload_only = False
        for _ in range(180):
            run.step({player: (*angles, 1)})
            stunned = fight.is_stunned(player)
            reloading = weapon.state(21).active or weapon.state(20).active
            saw_stun_only |= stunned and not reloading
            saw_reload_only |= reloading and not stunned
            if stunned or reloading:
                self.assertFalse(weapon.states[9].active)
                self.assertEqual(vitals.total, health)
            elif vitals.total < health:
                break
        self.assertEqual(saw_stun_only, filled)
        self.assertEqual(saw_reload_only, not filled)
        self.assertFalse(fight.is_stunned(player))
        self.assertFalse(weapon.states[20].active)
        self.assertEqual(weapon.vars[53].value(), 79)
        self.assertEqual(vitals.total, health - 5)

    def test_sleep_and_flashbang_overlap_during_both_reload_phases(self):
        for automatic in (False, True):
            for filled in (False, True):
                for after_grace in (False, True):
                    with self.subTest(automatic=automatic, filled=filled, after_grace=after_grace):
                        self.check_overlapping_control_reload(automatic, filled, after_grace)

    def check_overlapping_control_reload(self, automatic, filled, after_grace):
        run = Run(KANEZAKA_DM, ('Ball', 'Ana', 'McCree'))
        player, ana, mccree = run.players
        match = run.match
        fight = match.combat
        match.bots.clear()
        match._pending_bot_scripts.clear()
        match.switch_hero(player, heroes()[ball.HERO])
        match.switch_hero(ana, heroes()[control_tests.ANA])
        match.switch_hero(mccree, heroes()[0x02E0000000000042])
        x, y, z = player.mover.position
        relocate(run, ana, (x - 6, y, z))
        relocate(run, mccree, (x, y, z + 1.5))
        for _ in range(40):
            run.step({})
        weapon = ball.instance(player, 0x1514)
        phase = self.enter_reload(run, player, weapon, automatic=automatic, filled=filled)
        fill, exit_wait = weapon.state(21), weapon.state(20)
        body = player.body
        start, original_end = control_tests.BallControlTests().sleep_dart(run, ana, player)
        self.assertTrue(phase.active)
        if after_grace:
            while (run.frame - start) * combat.TICK <= combat.SLEEP_DAMAGE_GRACE:
                run.step({})
        flash = flash_tests.FlashbangTests()
        flash.step(run, mccree, player, cast=16)
        for _ in range(50):
            if body in fight.stuns:
                break
            flash.step(run, mccree, player)
        self.assertIn(body, fight.stuns)
        sleep_end = fight.sleeps[body][1]
        stun_end = fight.stuns[body]
        if after_grace:
            self.assertEqual(sleep_end, run.frame + 32)
            self.assertLess(sleep_end, stun_end)
        else:
            self.assertEqual(sleep_end, original_end)
            self.assertLess(stun_end, sleep_end)

        player.body_script.ult_seconds = 0
        player.body_script.set_ult_charge(0)
        target = next(t for t in fight._targets() if t.owner is mccree)
        vitals = fight.vitals_of(target)
        health = vitals.total
        x, y, z = target.feet
        angles = aim(fight._eye(player), (x, y + 1, z))
        hits_before = len(player.client.messages(combat.HIT))
        last_blocked = run.frame
        for _ in range(400):
            run.step({player: (*angles, 1)})
            self.assertEqual(body in fight.sleeps, run.frame < sleep_end)
            self.assertEqual(body in fight.stuns, run.frame < stun_end)
            blocked = fight.is_stunned(player) or fill.active or exit_wait.active
            if blocked:
                last_blocked = run.frame
                self.assertFalse(weapon.state(9).active)
                self.assertEqual(vitals.total, health)
            elif vitals.total < health:
                break
        self.assertEqual(run.frame, last_blocked + 1)
        self.assertGreaterEqual(run.frame, max(sleep_end, stun_end))
        self.assertEqual((fill.counter, exit_wait.counter), (1, 1))
        self.assertEqual(weapon.vars[53].value(), 79)
        self.assertEqual(vitals.total, health - 10)
        self.assertEqual(player.body_script.component.entity_var(ULT_CHARGE).value(), 10)
        hit, = player.client.messages(combat.HIT)[hits_before:]
        self.assertEqual(hit["+0x78"]["+0x0"]["+0x0"], target.entity)
        self.assertEqual(hit["+0x78"]["+0x4"], 10)
        self.assertEqual(hit["+0x78"]["+0x8"], combat.CRITICAL)


    def test_hack_and_emp_lock_minefield_without_bypassing_or_stalling_reload(self):
        for use_emp in (False, True):
            for automatic in (False, True):
                for filled in (False, True):
                    with self.subTest(emp=use_emp, automatic=automatic, filled=filled):
                        self.check_sombra_reload(use_emp=use_emp, automatic=automatic, filled=filled)

    def check_sombra_reload(self, *, use_emp, automatic, filled):
        run, player, sombra, hack_aim, _ = hack_tests.BallHackTests().scene()
        script = player.body_script
        weapon = next(i for i in script.component.instances.values() if i.graph.index == 0x1514)
        ammo = weapon.vars[53]
        phase = self.enter_reload(run, player, weapon, automatic=automatic, filled=filled)
        original_ammo = ammo.value()
        original_activation = phase.counter
        if use_emp:
            emp_tests.BallEmpTests().cast(run, sombra)
        else:
            for _ in range(50):
                run.step({sombra: (*hack_aim, 2)})
                if sombra_emp.locked(script, run.frame):
                    break
        self.assertTrue(sombra_emp.locked(script, run.frame))
        self.assertTrue(phase.active)
        self.assertEqual(phase.counter, original_activation)
        self.assertEqual(ammo.value(), original_ammo)

        fight = run.match.combat
        target = next(t for t in fight._targets() if t.owner is sombra)
        vitals = fight.vitals_of(target)
        health = vitals.total
        angles = aim(fight._eye(player), (target.feet[0], target.feet[1] + 1, target.feet[2]))
        charge = script.component.entity_var(ULT_CHARGE)
        initial_charge = charge.value()
        self.assertEqual(initial_charge, script.component.entity_var(ULT_COST).value())
        self.assertGreater(initial_charge, 0)
        for _ in range(140):
            run.step({player: (*angles, 1 | 32)})
            self.assertTrue(sombra_emp.locked(script, run.frame))
            if weapon.state(20).active or weapon.state(21).active:
                self.assertFalse(weapon.state(9).active)
                self.assertEqual(ammo.value(), 80 if weapon.state(20).active else (0 if automatic else 67))
                self.assertEqual(vitals.total, health)
                self.assertEqual(charge.value(), initial_charge)
            elif vitals.total < health:
                break
        self.assertFalse(weapon.state(20).active)
        self.assertFalse(weapon.state(21).active)
        self.assertEqual(phase.counter, original_activation)
        self.assertEqual(ammo.value(), 79)
        self.assertEqual(vitals.total, health - 5)
        self.assertEqual(charge.value(), initial_charge)
        self.assertFalse(fight.mines.seen)
        self.assertFalse(fight.mines.entities)

    def test_lethal_rifle_hit_during_reload_replaces_weapon_and_recovers(self):
        for automatic in (False, True):
            for filled in (False, True):
                with self.subTest(automatic=automatic, filled=filled):
                    self.check_lethal_rifle_reload(automatic=automatic, filled=filled)

    def check_lethal_rifle_reload(self, *, automatic, filled):
        run, player, enemy, target, own = ball_tests.BallCombatTests().scenario(distance=3)
        script = player.body_script
        weapon = next(i for i in script.component.instances.values() if i.graph.index == 0x1514)
        ammo = weapon.vars[53]
        fight = run.match.combat
        x, y, z = player.mover.position
        incoming = aim(fight._eye(enemy), (x, y + 1, z))
        for _ in range(640):
            run.step({enemy: (*incoming, 1)})
            if own.total <= 20:
                break
        self.assertGreater(own.total, 0)
        self.assertLessEqual(own.total, 20)

        phase = self.enter_reload(run, player, weapon, automatic=automatic, filled=filled)
        expected_ammo = ammo.value()
        for _ in range(20):
            self.assertTrue(phase.active)
            run.step({enemy: (*incoming, 1)})
            if own.dead:
                break
        self.assertTrue(own.dead)
        self.assertTrue(script.component.owner_dead)
        self.assertEqual(ammo.value(), expected_ammo)

        enemy_health = fight.vitals_of(target).total
        angles = aim(fight._eye(player), (target.feet[0], target.feet[1] + 1, target.feet[2]))
        for _ in range(20):
            run.step({player: (*angles, 1)})
            self.assertFalse(weapon.states[9].active)
        for _ in range(640):
            run.step({})
            if player.body_script is not script:
                break
            self.assertFalse(weapon.states[9].active)
        self.assertTrue(script.retired)
        self.assertEqual(fight.vitals_of(target).total, enemy_health)
        replacement = next(i for i in player.body_script.component.instances.values()
                           if i.graph.index == 0x1514)
        self.assertIsNot(weapon, replacement)
        self.assertFalse(replacement.state(20).active)
        self.assertFalse(replacement.state(21).active)
        for _ in range(15):
            run.step({})
        self.assertEqual(replacement.vars[53].value(), 80)
        self.assertEqual(fight.vitals_of(target).total, enemy_health)
        angles = aim(fight._eye(player), (target.feet[0], target.feet[1] + 1, target.feet[2]))
        for _ in range(80):
            run.step({player: (*angles, 1)})
            if fight.vitals_of(target).total < enemy_health:
                break
        self.assertEqual(replacement.vars[53].value(), 79)
        self.assertEqual(fight.vitals_of(target).total, enemy_health - 5)

    def test_piledriver_arbitrates_manual_and_automatic_reload(self):
        for automatic in (False, True):
            for filled in (False, True):
                with self.subTest(automatic=automatic, filled=filled):
                    self.check_piledriver_reload(automatic=automatic, filled=filled)

    def check_piledriver_reload(self, *, automatic, filled):
        run, player, enemy, target, _ = ball_tests.BallCombatTests().scenario(distance=1.5)
        script = player.body_script
        weapon = ball.instance(player, 0x1514)
        ammo = weapon.vars[53]
        wait = self.enter_reload(run, player, weapon, automatic=automatic, filled=filled)
        counter = wait.counter
        initial_ammo = ammo.value()
        pile = pile_tests.pile_instance(player)
        fight = run.match.combat
        vitals = fight.vitals_of(target)
        before = vitals.total
        script.ult_seconds = 0
        script.set_ult_charge(0)
        charge = script.component.entity_var(ULT_CHARGE)

        run.step({player: (0, 0, 128)})
        self.assertTrue(wait.active)
        self.assertEqual(wait.counter, counter)
        self.assertEqual(ammo.value(), initial_ammo)
        self.assertEqual(pile.state(24).remaining(), 0)

        pile_tests.lift(run, player)
        run.step({player: (0, 0, 128)})
        self.assertTrue(pile.state(18).active)
        self.assertGreater(pile.state(24).remaining(), 0)
        self.assertEqual(wait.active, filled)
        self.assertEqual(wait.counter, counter)

        landing = pile.state(7)
        for _ in range(100):
            x, y, z = enemy.mover.position
            run.step({player: (*aim(fight._eye(player), (x, y + 1, z)), 1)})
            self.assertFalse(weapon.state(9).active)
            self.assertEqual(ammo.value(), initial_ammo)
            self.assertEqual(wait.counter, counter)
            if landing.counter:
                break
            self.assertEqual(vitals.total, before)
        self.assertEqual(landing.counter, 1)
        self.assertEqual(vitals.total, before - 100)
        self.assertEqual(charge.value(), 100)
        self.assertFalse(wait.active)

        for _ in range(220):
            x, y, z = enemy.mover.position
            run.step({player: (*aim(fight._eye(player), (x, y + 1, z)), 1)})
            if weapon.state(9).active:
                break
            self.assertEqual(vitals.total, before - 100)
        self.assertTrue(weapon.state(9).active)
        self.assertFalse(weapon.state(20).active)
        self.assertFalse(weapon.state(21).active)
        self.assertEqual(wait.counter, counter + int(automatic and not filled))
        self.assertEqual(ammo.value(), 79 if automatic or filled else 66)
        self.assertEqual(vitals.total, before - 105)
        self.assertEqual(charge.value(), 105)
        self.assertEqual(landing.counter, 1)

    def test_quick_melee_arbitrates_manual_and_automatic_reload(self):
        for automatic in (False, True):
            for filled in (False, True):
                with self.subTest(automatic=automatic, filled=filled):
                    self.check_quick_melee_reload(automatic=automatic, filled=filled)

    def test_quick_melee_preempts_refill_on_the_same_frame(self):
        for automatic in (False, True):
            delay = self.reload_fill_delay(automatic=automatic)
            for offset in (-1, 0, 1):
                with self.subTest(automatic=automatic, offset_from_refill=offset):
                    self.check_quick_melee_reload(
                        automatic=automatic, filled=offset > 0, before_press=delay + offset - 1,
                    )

    def reload_fill_delay(self, *, automatic):
        run, player, _, _, _ = ball_tests.BallCombatTests().scenario(distance=1.5)
        weapon = ball.instance(player, 0x1514)
        self.enter_reload(run, player, weapon, automatic=automatic, filled=False)
        start = run.frame
        for _ in range(100):
            run.step({})
            if weapon.state(20).active:
                break
        self.assertFalse(weapon.state(21).active)
        self.assertTrue(weapon.state(20).active)
        self.assertEqual(weapon.vars[53].value(), 80)
        return run.frame - start

    def test_adaptive_shield_preserves_reload_at_refill_boundary(self):
        for automatic in (False, True):
            delay = self.reload_fill_delay(automatic=automatic)
            for offset in (-1, 0, 1):
                with self.subTest(automatic=automatic, offset_from_refill=offset):
                    self.check_adaptive_shield_at_refill(automatic=automatic, delay=delay, offset=offset)

    def check_adaptive_shield_at_refill(self, *, automatic, delay, offset):
        run, player, enemy, target, own = ball_tests.BallCombatTests().scenario(distance=1.5)
        weapon = ball.instance(player, 0x1514)
        refill = self.enter_reload(run, player, weapon, automatic=automatic, filled=False)
        exit_wait = weapon.state(20)
        ammo = weapon.vars[53]
        initial_ammo = ammo.value()
        for _ in range(delay + offset - 1):
            run.step({})
        self.assertEqual(refill.active, offset <= 0)
        self.assertEqual(exit_wait.active, offset > 0)
        fight = run.match.combat
        vitals = fight.vitals_of(target)
        before = vitals.total
        x, y, z = enemy.mover.position
        angles = aim(fight._eye(player), (x, y + 1, z))
        cannon = weapon.state(9)

        run.step({player: (*angles, 17)})
        self.assertEqual(own.temporary_shields, 175)
        deadline = own.adaptive_until
        self.assertGreater(deadline, run.frame)
        self.assertEqual(refill.active, offset < 0)
        self.assertEqual(exit_wait.active, offset >= 0)
        self.assertEqual(ammo.value(), initial_ammo if offset < 0 else 80)
        self.assertFalse(cannon.active)
        self.assertEqual(vitals.total, before)

        if offset < 0:
            run.step({player: (*angles, 1)})
            self.assertFalse(refill.active)
            self.assertTrue(exit_wait.active)
            self.assertEqual(ammo.value(), 80)
            self.assertFalse(cannon.active)
        for _ in range(100):
            run.step({player: (*angles, 1)})
            if cannon.active:
                break
            self.assertEqual(vitals.total, before)
        self.assertTrue(cannon.active)
        self.assertFalse(refill.active)
        self.assertFalse(exit_wait.active)
        self.assertEqual((refill.counter, exit_wait.counter), (1, 1))
        self.assertEqual(ammo.value(), 79)
        self.assertEqual(vitals.total, before - 5)
        self.assertEqual((own.temporary_shields, own.adaptive_until), (175, deadline))

    def check_quick_melee_reload(self, *, automatic, filled, before_press=0):
        run, player, enemy, target, _ = ball_tests.BallCombatTests().scenario(distance=1.5)
        script = player.body_script
        weapon = ball.instance(player, 0x1514)
        self.enter_reload(run, player, weapon, automatic=automatic, filled=filled and not before_press)
        for _ in range(before_press):
            run.step({})
        wait = weapon.state(20 if filled else 21)
        self.assertTrue(wait.active)
        counter = wait.counter
        ammo = weapon.vars[53]
        initial_ammo = ammo.value()
        self.assertEqual(initial_ammo, 80 if filled else (0 if automatic else 67))
        fight = run.match.combat
        vitals = fight.vitals_of(target)
        before = vitals.total
        x, y, z = enemy.mover.position
        angles = aim(fight._eye(player), (x, y + 1, z))
        script.ult_seconds = 0
        script.set_ult_charge(0)
        charge = script.component.entity_var(ULT_CHARGE)
        cannon = weapon.state(9)
        restarts_reload = automatic and not filled

        run.step({player: (*angles, 0x801)})
        self.assertEqual(vitals.total, before - 30)
        self.assertEqual(charge.value(), 30)
        self.assertEqual(wait.active, filled)
        self.assertEqual(wait.counter, counter)
        self.assertEqual(ammo.value(), initial_ammo)
        self.assertFalse(cannon.active)

        melee = ball.instance(player, 0x0043)
        swing, recovery = melee.state(24), melee.state(9)
        self.assertTrue(swing.active)
        for _ in range(60):
            run.step({player: (*angles, 1)})
            if not (swing.active or recovery.active):
                break
            self.assertFalse(cannon.active)
            self.assertEqual(wait.counter, counter)
            self.assertEqual(ammo.value(), initial_ammo)
            self.assertEqual(vitals.total, before - 30)
        self.assertFalse(swing.active or recovery.active)

        if restarts_reload:
            self.assertTrue(wait.active)
            self.assertEqual(wait.counter, counter + 1)
            self.assertEqual(ammo.value(), 0)
            self.assertFalse(cannon.active)
        for _ in range(150):
            if cannon.active:
                break
            self.assertEqual(vitals.total, before - 30)
            run.step({player: (*angles, 1)})
        self.assertTrue(cannon.active)
        self.assertFalse(weapon.state(20).active)
        self.assertFalse(weapon.state(21).active)
        self.assertEqual(wait.counter, counter + int(restarts_reload))
        self.assertEqual(ammo.value(), 79 if automatic or filled else 66)
        self.assertEqual(vitals.total, before - 35)
        self.assertEqual(charge.value(), 35)

    def test_grapple_launch_and_release_arbitrate_reload(self):
        for automatic in (False, True):
            for filled in (False, True):
                for attach in (False, True):
                    with self.subTest(automatic=automatic, filled=filled, attach=attach):
                        self.check_grapple_reload(automatic=automatic, filled=filled, attach=attach)

    def check_grapple_reload(self, *, automatic, filled, attach):
        run, player, enemy, target, _ = grapple_tests.scene()
        script = player.body_script
        weapon = ball.instance(player, 0x1514)
        wait = self.enter_reload(run, player, weapon, automatic=automatic, filled=filled)
        counter = wait.counter
        ammo = weapon.vars[53]
        initial_ammo = ammo.value()
        cannon = weapon.state(9)
        fight = run.match.combat
        vitals = fight.vitals_of(target)
        before = vitals.total
        script.ult_seconds = 0
        script.set_ult_charge(0)
        charge = script.component.entity_var(ULT_CHARGE)
        x, y, z = enemy.mover.position
        angles = aim(fight._eye(player), (x, y + 1, z))

        run.step({player: (0, 0, 2 if attach else 3)})
        hook = fight.ball_grapples[player.body]
        self.assertFalse(hook.attached)
        self.assertTrue(ball.rolling(player))
        self.assertEqual(wait.active, filled)
        self.assertEqual(wait.counter, counter)
        self.assertEqual(ammo.value(), initial_ammo)
        self.assertFalse(cannon.active)
        if attach:
            grapple_tests.hold(run, player, 4)
            self.assertIs(fight.ball_grapples[player.body], hook)
            self.assertTrue(hook.attached)
            run.step({player: (*angles, 1)})
        else:
            run.step({player: (0, 0, 3)})
            self.assertFalse(hook.attached)
        self.assertNotIn(player.body, fight.ball_grapples)
        self.assertGreater(ball.instance(player, ball.GRAPPLE).state(25).remaining(), 4.9)
        self.assertEqual(wait.active, filled)
        self.assertEqual(wait.counter, counter)
        self.assertEqual(ammo.value(), initial_ammo)
        self.assertFalse(cannon.active)
        self.assertEqual(vitals.total, before)
        self.assertEqual(charge.value(), 0)

        for _ in range(200):
            run.step({player: (*angles, 1)})
            self.assertNotIn(player.body, fight.ball_grapples)
            if cannon.active:
                break
            self.assertEqual(vitals.total, before)
        self.assertTrue(cannon.active)
        self.assertFalse(ball.rolling(player))
        self.assertFalse(weapon.state(20).active)
        self.assertFalse(weapon.state(21).active)
        self.assertEqual(wait.counter, counter + int(automatic and not filled))
        self.assertEqual(ammo.value(), 79 if automatic or filled else 66)
        self.assertEqual(vitals.total, before - 5)
        self.assertEqual(charge.value(), 5)

    def test_hostile_piledriver_preserves_manual_and_automatic_reload(self):
        for automatic in (False, True):
            for filled in (False, True):
                with self.subTest(automatic=automatic, filled=filled):
                    self.check_hostile_piledriver_reload(automatic=automatic, filled=filled)

    def check_hostile_piledriver_reload(self, *, automatic, filled):
        run, player, enemy, _, own = ball_tests.BallCombatTests().scenario(distance=1.5)
        position = tuple(enemy.mover.position)
        self.assertTrue(run.match.switch_hero(enemy, heroes()[ball_tests.BALL]))
        relocate(run, enemy, position)
        for _ in range(40):
            run.step({})
        fight = run.match.combat
        weapon = ball.instance(player, 0x1514)
        ammo = weapon.vars[53]
        refill, exit_wait = weapon.state(21), weapon.state(20)
        cannon = weapon.state(9)
        for _ in range(240 if automatic else 30):
            run.step({player: (-32768, 0, 1)})
            if automatic and ammo.value() == 1:
                break
        run.step({})
        self.assertEqual(ammo.value(), 1 if automatic else 67)
        for actor in (player, enemy):
            actor.body_script.ult_seconds = 0
            actor.body_script.set_ult_charge(0)

        def start_refill():
            for _ in range(30):
                run.step({player: (-32768, 0, 1 if automatic else 1024)})
                if refill.active:
                    break
            self.assertTrue(refill.active)
            self.assertEqual(ammo.value(), 0 if automatic else 67)

        def start_piledriver():
            pile_tests.lift(run, enemy)
            run.step({enemy: (0, 0, 128)})
            self.assertTrue(pile_tests.pile_instance(enemy).state(18).active)

        if filled:
            start_refill()
            for _ in range(43):
                run.step({})
            start_piledriver()
        else:
            start_piledriver()
            for _ in range(22):
                run.step({})
            start_refill()
        for _ in range(100):
            run.step({})
            if player.body in fight.pile_air_locks:
                break
        self.assertIn(player.body, fight.pile_air_locks)
        _, deadline = fight.pile_air_locks[player.body]
        self.assertGreater(deadline, run.frame)
        self.assertTrue(player.mover.airborne)
        self.assertFalse(fight.is_stunned(player))
        self.assertEqual(own.total, 505)
        self.assertEqual(enemy.body_script.component.entity_var(ULT_CHARGE).value(), 95)
        self.assertEqual(refill.active, not filled)
        self.assertEqual(exit_wait.active, filled)
        self.assertEqual(ammo.value(), 80 if filled else (0 if automatic else 67))

        enemy_vitals = fight.vitals[enemy.body]
        before = enemy_vitals.total
        for _ in range(140):
            x, y, z = enemy.mover.position
            run.step({player: (*aim(fight._eye(player), (x, y + 1, z)), 1)})
            if cannon.active:
                break
            self.assertEqual(enemy_vitals.total, before)
        self.assertTrue(cannon.active)
        self.assertFalse(refill.active)
        self.assertFalse(exit_wait.active)
        self.assertEqual((refill.counter, exit_wait.counter), (1, 1))
        self.assertEqual(ammo.value(), 79)
        self.assertEqual(enemy_vitals.total, before - 2.5)
        self.assertEqual(player.body_script.component.entity_var(ULT_CHARGE).value(), 2.5)
        self.assertEqual(run.frame < deadline, filled)
        if filled:
            self.assertTrue(player.mover.airborne)

    def test_hostile_fireball_preserves_manual_and_automatic_reload(self):
        for automatic in (False, True):
            for filled in (False, True):
                with self.subTest(automatic=automatic, filled=filled):
                    self.check_hostile_fireball_reload(automatic, filled)

    def check_hostile_fireball_reload(self, automatic, filled):
        run, enemy, player, _, _ = grapple_tests.scene()
        match = run.match
        fight = match.combat
        x, y, z = enemy.mover.position
        self.assertTrue(match.switch_hero(player, heroes()[ball.HERO]))
        relocate(run, player, (x, y, z + 6))
        for _ in range(40):
            run.step({})
        weapon = ball.instance(player, 0x1514)
        ammo = weapon.vars[53]
        refill, exit_wait = weapon.state(21), weapon.state(20)
        own = fight.vitals[player.body]
        for _ in range(240 if automatic else 30):
            run.step({player: (0, 0, 1)})
            if automatic and ammo.value() == 1:
                break
        run.step({})
        self.assertEqual(ammo.value(), 1 if automatic else 67)
        for actor in (player, enemy):
            actor.body_script.ult_seconds = 0
            actor.body_script.set_ult_charge(0)

        def start_refill(swinging):
            for _ in range(30):
                run.step({player: (0, 0, 1 if automatic else 1024),
                          enemy: (0, 0, 2 if swinging else 0)})
                if refill.active:
                    break
            self.assertTrue(refill.active)
            self.assertEqual(ammo.value(), 0 if automatic else 67)

        if filled:
            start_refill(False)
            for _ in range(43):
                run.step({})
            pile_tests.lift(run, enemy)
        else:
            pile_tests.lift(run, enemy)
            grapple_tests.hold(run, enemy, 22)
            start_refill(True)
        for _ in range(100):
            grapple_tests.hold(run, enemy, 1)
            if player.body in fight.ball_contacts.get(enemy.body, ()):
                break
        self.assertIn(player.body, fight.ball_contacts.get(enemy.body, ()))
        self.assertEqual(own.total, 555)
        self.assertTrue(player.mover.airborne)
        self.assertGreater(player.mover.velocity[1], 0)
        self.assertFalse(fight.is_stunned(player))
        self.assertNotIn(player.body, fight.pile_air_locks)
        self.assertEqual(enemy.body_script.component.entity_var(ULT_CHARGE).value(), 45)
        self.assertEqual((refill.active, exit_wait.active), (not filled, filled))
        self.assertEqual(ammo.value(), 80 if filled else (0 if automatic else 67))

        # Preserve the live weapon state while settling both bodies in a clear firing lane.
        run.step({})
        x, _, z = player.mover.position
        relocate(run, player, (x, y, z))
        relocate(run, enemy, (x + 6, y, z))
        enemy_vitals = fight.vitals[enemy.body]
        before = enemy_vitals.total
        for _ in range(140):
            x, y, z = enemy.mover.position
            run.step({player: (*aim(fight._eye(player), (x, y + 0.7, z)), 1)})
            if weapon.state(9).active:
                break
            self.assertEqual(enemy_vitals.total, before)
        self.assertTrue(weapon.state(9).active)
        self.assertFalse(refill.active or exit_wait.active)
        self.assertEqual((refill.counter, exit_wait.counter), (1, 1))
        self.assertEqual(ammo.value(), 79)
        self.assertEqual(enemy_vitals.total, before - 2.5)
        self.assertEqual(player.body_script.component.entity_var(ULT_CHARGE).value(), 2.5)


    def enter_reload(self, run, player, weapon, *, automatic, filled):
        ammo = weapon.vars[53]
        for _ in range(240 if automatic else 30):
            run.step({player: (-32768, 0, 1)})
            if automatic and ammo.value() == 0:
                break
        run.step({})
        if not automatic:
            run.step({player: (0, 0, 1024)})
        phase = weapon.state(20 if filled else 21)
        for _ in range(100):
            if phase.active:
                break
            run.step({})
        self.assertTrue(phase.active)
        self.assertEqual(ammo.value(), 80 if filled else (0 if automatic else 67))
        return phase

    def test_roll_refills_and_returns_to_real_fire_after_each_reload_phase(self):
        for reload_wait in (None, 20, 80):
            with self.subTest(manual_reload_frames=reload_wait):
                run, player, _, target, _ = ball_tests.BallCombatTests().scenario()
                weapon = next(i for i in player.body_script.component.instances.values()
                              if i.graph.index == 0x1514)
                for _ in range(30):
                    run.step({player: (-32768, 0, 1)})
                run.step({})
                self.assertEqual(weapon.vars[53].value(), 67)
                if reload_wait is not None:
                    run.step({player: (0, 0, 1024)})
                    for _ in range(reload_wait):
                        run.step({})
                run.step({player: (0, 0, 8)})
                self.assertTrue(ball.rolling(player))
                for _ in range(90):
                    run.step({})
                if reload_wait != 80:
                    self.assertEqual(weapon.vars[53].value(), 67)
                for _ in range(110):
                    run.step({})
                self.assertTrue(ball.rolling(player))
                self.assertEqual(weapon.vars[53].value(), 80)
                fight = run.match.combat
                vitals = fight.vitals_of(target)
                before = vitals.total
                angles = aim(fight._eye(player),
                             (target.feet[0], target.feet[1] + 1, target.feet[2]))
                for _ in range(60):
                    run.step({player: (*angles, 1)})
                    if vitals.total < before:
                        break
                self.assertFalse(ball.rolling(player))
                self.assertEqual(vitals.total, before - 5)
                self.assertEqual(weapon.vars[53].value(), 79)


if __name__ == '__main__':
    unittest.main()
