"""Actual player inputs against the reconstructed Cryo-Freeze protection phase."""
import unittest

import test_game_ana as fixture
from test_game_combat import aim, in_world

from ow174.game import cryo, stats
from ow174.game.commands import Command
from ow174.game.content import SOLDIER, find_hero


class AnaCryoTests(unittest.TestCase):
    def finish_exit(self, run, mei):
        for _ in range(20):
            if not cryo.active(mei):
                return
            run.step({})
        self.fail("Cryo exit did not clear protection")

    def test_cryo_protects_from_actual_ana_attacks_until_cancel(self):
        for button, scoped in ((1, False), (1, True), (8, False), (16, False), (0x800, False)):
            with self.subTest(button=button, scoped=scoped):
                run, ana, mei, old = fixture.AnaRifleTests().setup_fight(enemy=True, distance=1.5)
                self.assertTrue(run.match.switch_hero(mei, find_hero('Mei')))
                mei.mover.position[:] = old.feet
                for _ in range(25):
                    run.step({})
                fight = run.match.combat
                target = next(t for t in fight._targets() if t.owner is mei)
                vitals = fight.vitals_of(target)
                point = (target.feet[0], target.feet[1] + 1, target.feet[2])
                direction = aim(fight._eye(ana), point)
                run.step({mei: (0, 0, 8)})
                self.assertTrue(cryo.active(mei))
                for _ in range(35):
                    run.step({ana: (*direction, 2 if scoped else 0)})
                run.step({ana: (*direction, button | (2 if scoped else 0))})
                for _ in range(70):
                    run.step({})
                self.assertTrue(cryo.active(mei))
                self.assertEqual(vitals.total, 250)
                self.assertEqual(vitals.healing_block_until, 0)
                self.assertFalse(fight.is_asleep(mei))
                run.step({mei: (0, 0, 1)})
                self.assertTrue(cryo.active(mei))
                self.finish_exit(run, mei)
                run.step({ana: (*direction, 1)})
                for _ in range(65):
                    run.step({})
                self.assertEqual(vitals.total, 180)

    def test_self_healing_integrates_authored_total_and_cancel_endpoint(self):
        for cancel in (False, True):
            with self.subTest(cancel=cancel):
                run, _, mei, vitals = self.injured_mei()
                run.step({mei: (0, 0, 8)})
                self.assertTrue(cryo.active(mei))
                for _ in range(20 if cancel else 255):
                    run.step({})
                if cancel:
                    self.assertTrue(cryo.active(mei))
                    run.step({mei: (0, 0, 1)})
                    run.step({})
                self.finish_exit(run, mei)
                _, amount, _ = cryo.healing_sample(mei)
                if cancel:
                    self.assertGreater(amount, 0)
                    self.assertLess(amount, 200)
                else:
                    self.assertEqual(amount, 200)
                self.assertAlmostEqual(vitals.total, 30 + amount)
                self.assertAlmostEqual(mei.stats.value(stats.HEALING), amount)
                for _ in range(10):
                    run.step({})
                self.assertAlmostEqual(vitals.total, 30 + amount)

    def test_blocked_self_healing_is_not_replayed_after_antiheal(self):
        run, ana, mei, vitals = self.injured_mei()
        fight = run.match.combat
        target = next(t for t in fight._targets() if t.owner is mei)
        vitals.heal(80)
        fixture.AnaGrenadeTests().cast(run, ana, target)
        self.assertGreater(vitals.healing_block_until, fight.now)
        before = vitals.total
        run.step({mei: (0, 0, 8)})
        self.assertTrue(cryo.active(mei))
        while fight.now < vitals.healing_block_until:
            self.assertEqual(vitals.total, before)
            run.step({})
        first_unblocked = vitals.total - before
        self.assertGreater(first_unblocked, 0)
        self.assertLess(first_unblocked, 1)
        for _ in range(255):
            run.step({})
        given = vitals.total - before
        self.assertGreater(given, 0)
        self.assertLess(given, 100)
        self.assertAlmostEqual(mei.stats.value(stats.HEALING), given)

    def injured_mei(self):
        run, ana, mei, old = fixture.AnaRifleTests().setup_fight(enemy=True)
        self.assertTrue(run.match.switch_hero(mei, find_hero('Mei')))
        mei.mover.position[:] = old.feet
        for _ in range(25):
            run.step({})
        fight = run.match.combat
        target = next(t for t in fight._targets() if t.owner is mei)
        vitals = fight.vitals_of(target)
        vitals.damage(220, bypass_armour=True)
        return run, ana, mei, vitals

    def test_allied_cryo_blocks_rifle_to_enemy_behind(self):
        for button, scope, damage in ((1, 0, 70), (1, 2, 70), (8, 0, 5)):
            with self.subTest(button=button, scope=scope):
                run, ana, mei, old = fixture.AnaRifleTests().setup_fight()
                self.assertTrue(run.match.switch_hero(mei, find_hero('Mei')))
                mei.mover.position[:] = old.feet
                enemy = run.match.add_player(3, 'Behind', SOLDIER, 1, False)
                in_world(run.match, enemy)
                run.players.append(enemy)
                self.assertTrue(run.match.switch_hero(enemy, find_hero("Soldier: 76")))
                enemy.team = 1
                x, y, z = old.feet
                enemy.mover.position[:] = x, y, z + 4
                for _ in range(25):
                    run.step({})
                fight = run.match.combat
                entry = next(t for t in fight._targets() if t.owner is enemy)
                vitals = fight.vitals_of(entry)
                before = vitals.total
                direction = aim(fight._eye(ana), (x, y + 1, z + 4))
                run.step({mei: (0, 0, 8)})
                self.assertTrue(cryo.active(mei))
                if scope:
                    for _ in range(35):
                        run.step({ana: (*direction, scope)})
                run.step({ana: (*direction, button | scope)})
                for _ in range(70):
                    run.step({ana: (*direction, scope)})
                self.assertEqual(vitals.total, before)
                self.assertFalse(fight.is_asleep(enemy))
                run.step({mei: (0, 0, 1)})
                self.finish_exit(run, mei)
                if button == 8:
                    for _ in range(800):
                        run.step({})
                if scope:
                    for _ in range(35):
                        run.step({ana: (*direction, scope)})
                run.step({ana: (*direction, button | scope)})
                for _ in range(70):
                    run.step({ana: (*direction, scope)})
                self.assertEqual(vitals.total, before - damage)
                self.assertEqual(fight.is_asleep(enemy), button == 8)

    def test_allied_rifle_and_nano_apply_during_cryo(self):
        from test_game_ana_nano import AnaNanoTests

        helper = AnaNanoTests()
        run, ana, mei, _ = helper.fight(ally_hero='Mei')
        fight = run.match.combat
        vitals = fight.vitals_of(helper.target(fight, mei))
        vitals.damage(220, bypass_armour=True)
        run.step({mei: (0, 0, 8)})
        self.assertTrue(cryo.active(mei))
        helper.single_shot(run, ana, mei)
        self.assertTrue(cryo.active(mei))
        self.assertAlmostEqual(ana.stats.value(stats.HEALING), 70)
        self.assertAlmostEqual(vitals.total, 30 + 70 + mei.stats.value(stats.HEALING))
        self.assertEqual(vitals.nano_until, 0)
        helper.cast(run, ana, mei)
        self.assertTrue(cryo.active(mei))
        self.assertEqual(vitals.total, 250)
        self.assertGreater(vitals.nano_until, fight.now)
        self.assertGreater(ana.stats.value(stats.HEALING), 70)
        self.assertEqual(ana.body_script.component.vars[342].value(), 0)

    def test_cryo_intercepts_grenade_and_occludes_enemy_behind(self):
        from test_game_ana_nano import AnaNanoTests

        helper = AnaNanoTests()
        for frozen in (False, True):
            with self.subTest(frozen=frozen):
                run, ana, mei, rear = helper.fight(ally_hero='Mei', distance=3)
                mei.team = 1
                x, y, z = mei.mover.position
                rear.spawn = (x, y, z + 0.9), rear.spawn[1]
                rear.mover = rear.new_mover()
                for _ in range(5):
                    run.step({})
                fight = run.match.combat
                mei_target = helper.target(fight, mei)
                rear_vitals = fight.vitals_of(helper.target(fight, rear))
                if frozen:
                    run.step({mei: (0, 0, 8)})
                    self.assertTrue(cryo.active(mei))
                point = (x, y + 1, z)
                run.step({ana: (*fixture.grenade_aim(run, ana, point), 16)})
                for _ in range(70):
                    run.step({})
                self.assertEqual(fight.vitals_of(mei_target).total, 250 if frozen else 200)
                self.assertEqual(rear_vitals.total, 200 if frozen else 150)
                self.assertEqual(rear_vitals.healing_block_until > fight.now, not frozen)

    def test_skipped_frames_do_not_restore_antiheal_blocked_cryo_pulses(self):
        totals = []
        for skip in (False, True):
            run, _, mei, vitals = self.injured_mei()
            vitals.healing_block_until = run.match.combat.now + 1
            run.step({mei: (0, 0, 8)})
            for _ in range(30):
                run.step({})
            if skip:
                run.frame += 65
            else:
                for _ in range(65):
                    run.step({})
            run.step({})
            run.step({})
            totals.append(vitals.total)
        self.assertAlmostEqual(totals[0], 58.8)
        self.assertAlmostEqual(totals[1], totals[0])

    def test_actual_grenade_boost_expiry_during_cryo_is_stable_across_skipped_frames(self):
        totals = []
        for skip in (False, True):
            run, ana, mei, vitals = self.injured_mei()
            mei.team = ana.team
            fight = run.match.combat
            target = next(t for t in fight._targets() if t.owner is mei)
            point = (target.feet[0], target.feet[1] + 1, target.feet[2])
            run.step({ana: (*fixture.grenade_aim(run, ana, point), 16)})
            for _ in range(70):
                if vitals.healing_boost_until > fight.now:
                    break
                run.step({})
            self.assertEqual(vitals.total, 130)
            while fight.now + .6 < vitals.healing_boost_until:
                run.step({})
            run.step({mei: (0, 0, 8)})
            for _ in range(10):
                run.step({})
            self.assertTrue(cryo.active(mei))
            if skip:
                run.frame += 45
            else:
                for _ in range(45):
                    run.step({})
            run.step({})
            run.step({})
            self.assertGreater(fight.now, vitals.healing_boost_until)
            _, raw, _ = cryo.healing_sample(mei)
            credited = mei.stats.value(stats.HEALING)
            self.assertGreater(credited, raw)
            self.assertLess(credited, raw * 1.5)
            totals.append(credited)
        self.assertAlmostEqual(totals[1], totals[0])

    def test_cryo_locks_throttles_until_real_cancel_and_motion_recovers(self):
        run, _, mei, _ = self.injured_mei()
        run.step({mei: (0, 0, 8)})
        start = tuple(mei.mover.position)
        for _ in range(30):
            run.frame += 1
            run.match.update(run.frame * 0.016, run.frame)
            mei.take_commands([Command(run.frame, forward=127, right=127, buttons=0x40)])
            self.assertEqual(tuple(mei.mover.position), start)
        self.assertTrue(cryo.active(mei))
        run.step({mei: (0, 0, 1)})
        self.finish_exit(run, mei)
        for _ in range(35):
            run.frame += 1
            run.match.update(run.frame * 0.016, run.frame)
            mei.take_commands([Command(run.frame, forward=127)])
        self.assertNotEqual(tuple(mei.mover.position), start)

    def test_cancel_wait_and_cooldown_gate_actual_reentry(self):
        run, _, mei, _ = self.injured_mei()
        run.step({mei: (0, 0, 8)})
        instance = next(i for i in mei.body_script.component.instances.values() if i.graph.index == cryo.GRAPH)
        ability = instance.states[0]
        run.step({mei: (0, 0, 1)})
        for _ in range(18):
            run.step({})
        self.assertTrue(instance.states[13].active)
        run.step({mei: (0, 0, 1)})
        self.assertFalse(instance.states[13].active)
        self.assertTrue(cryo.active(mei))
        self.assertGreater(ability.remaining(), 11.9)
        self.assertLessEqual(ability.remaining(), 12)
        self.finish_exit(run, mei)
        run.step({mei: (0, 0, 8)})
        run.step({})
        self.assertFalse(cryo.active(mei))
        self.assertEqual(instance.states[13].counter, 1)
        for _ in range(760):
            if ability.remaining() == 0:
                break
            run.step({})
        self.assertEqual(ability.remaining(), 0)
        run.step({mei: (0, 0, 8)})
        self.assertTrue(cryo.active(mei))
        self.assertEqual(instance.states[13].counter, 2)

    def test_grenade_landing_during_cryo_does_not_boost_earlier_unsampled_healing(self):
        totals = []
        for skip in (False, True):
            run, ana, mei, vitals = self.injured_mei()
            mei.team = ana.team
            fight = run.match.combat
            target = next(t for t in fight._targets() if t.owner is mei)
            point = (target.feet[0], target.feet[1] + 1, target.feet[2])
            run.step({mei: (0, 0, 8)})
            for _ in range(8):
                run.step({})
            direction = fixture.grenade_aim(run, ana, point)
            if skip:
                run.players.remove(mei)
            run.step({ana: (*direction, 16)})
            for _ in range(40):
                run.step({})
            if skip:
                run.players.append(mei)
            for _ in range(10):
                run.step({})
            self.assertGreater(vitals.healing_boost_until, fight.now)
            self.assertAlmostEqual(ana.stats.value(stats.HEALING), 100)
            _, raw, _ = cryo.healing_sample(mei)
            credited = mei.stats.value(stats.HEALING)
            self.assertGreater(credited, raw)
            self.assertLess(credited, raw * 1.5)
            totals.append(credited)
        self.assertAlmostEqual(totals[1], totals[0])

    def test_pending_enemy_rifle_dose_only_applies_outside_cryo(self):
        run, ana, mei, vitals = self.injured_mei()
        vitals.heal(100)
        fight = run.match.combat
        target = next(t for t in fight._targets() if t.owner is mei)
        point = (target.feet[0], target.feet[1] + 1, target.feet[2])
        direction = aim(fight._eye(ana), point)
        run.step({ana: (*direction, 1)})
        for _ in range(30):
            if ana.stats.value(stats.HERO_DAMAGE) > 0:
                break
            run.step({})
        first = ana.stats.value(stats.HERO_DAMAGE)
        self.assertGreater(first, 0)
        self.assertLess(first, 70)
        run.step({mei: (0, 0, 8)})
        before_freeze = ana.stats.value(stats.HERO_DAMAGE)
        self.assertTrue(cryo.active(mei))
        for _ in range(14):
            run.step({})
        self.assertEqual(ana.stats.value(stats.HERO_DAMAGE), before_freeze)
        run.step({mei: (0, 0, 1)})
        self.assertTrue(cryo.active(mei))
        for _ in range(10):
            run.step({})
            self.assertTrue(cryo.active(mei))
            self.assertEqual(ana.stats.value(stats.HERO_DAMAGE), before_freeze)
        self.finish_exit(run, mei)
        for _ in range(45):
            run.step({})
        damage = ana.stats.value(stats.HERO_DAMAGE)
        self.assertGreater(damage, before_freeze)
        self.assertLess(damage, 70)
        self.assertAlmostEqual(vitals.total, 130 - damage + mei.stats.value(stats.HEALING))

    def test_hero_replacement_retires_cryo_immunity_and_healing(self):
        run, ana, mei, vitals = self.injured_mei()
        fight = run.match.combat
        old_body = mei.body
        run.step({mei: (0, 0, 8)})
        for _ in range(30):
            run.step({})
        self.assertTrue(cryo.active(mei))
        self.assertGreater(vitals.total, 30)
        self.assertIn(old_body, fight.cryo_samples)
        old_healing = mei.stats.value(stats.HEALING)
        self.assertTrue(run.match.switch_hero(mei, find_hero('Soldier: 76')))
        self.assertNotEqual(mei.body, old_body)
        self.assertFalse(cryo.active(mei))
        self.assertNotIn(old_body, fight.cryo_samples)
        x, y, z = ana.mover.position
        mei.mover.position = [x, y, z + 6]
        for _ in range(25):
            run.step({})
        entry = next(t for t in fight._targets() if t.owner is mei)
        direction = aim(fight._eye(ana), (x, y + 1, z + 6))
        run.step({ana: (*direction, 1)})
        for _ in range(70):
            run.step({})
        self.assertEqual(fight.vitals_of(entry).total, 130)
        self.assertEqual(mei.stats.value(stats.HEALING), old_healing)
        self.assertNotIn(mei.body, fight.cryo_samples)

    def test_world_updates_without_mei_commands_finish_healing_and_immunity(self):
        run, ana, mei, vitals = self.injured_mei()
        fight = run.match.combat
        x, y, z = mei.mover.position
        yaw, pitch = aim(fight._eye(ana), (x, y + 1, z))
        run.step({mei: (0, 0, 8)})
        entry = mei.body_script.frame

        def step(buttons=0):
            run.frame += 1
            run.match.update(run.frame * .016, run.frame, frames=False)
            ana.take_commands([Command(run.frame, yaw=yaw, pitch=pitch, buttons=buttons)])

        for _ in range(251):
            step()
        self.assertAlmostEqual(vitals.total, 230)
        self.assertTrue(cryo.active(mei))
        for _ in range(15):
            step()
        self.assertTrue(cryo.active(mei))
        step()
        self.assertFalse(cryo.active(mei))
        step(8)
        for _ in range(65):
            step()
        self.assertEqual(mei.body_script.frame, entry)
        self.assertAlmostEqual(vitals.total, 225)
        self.assertTrue(fight.is_asleep(mei))

    def test_cancel_then_no_commands_finishes_exit_without_more_healing(self):
        run, _, mei, vitals = self.injured_mei()
        run.step({mei: (0, 0, 8)})
        for _ in range(22):
            run.step({})
        run.step({mei: (0, 0, 1)})
        self.assertTrue(cryo.active(mei))
        cancelled_frame = mei.body_script.frame
        health = vitals.total
        run.frame += 15
        run.match.update(run.frame * .016, run.frame, frames=False)
        self.assertTrue(cryo.active(mei))
        run.frame += 1
        run.match.update(run.frame * .016, run.frame, frames=False)
        self.assertFalse(cryo.active(mei))
        self.assertEqual(mei.body_script.frame, cancelled_frame)
        self.assertEqual(vitals.total, health)

    def test_mei_can_reenter_after_the_world_cooldown_during_a_long_input_gap(self):
        for gap, allowed in ((820, False), (1100, True)):
            with self.subTest(gap=gap):
                run, _, mei, vitals = self.injured_mei()
                run.step({mei: (0, 0, 8)})
                entry = mei.body_script.frame
                run.frame += gap
                run.match.update(run.frame * .016, run.frame, frames=False)
                self.assertEqual(mei.body_script.frame, entry)
                self.assertFalse(cryo.active(mei))
                self.assertAlmostEqual(vitals.total, 230)
                mei.take_commands([Command(run.frame, buttons=8)])
                self.assertEqual(cryo.active(mei), allowed)
                state = cryo.healing_state(mei)
                self.assertEqual(state.counter, 2 if allowed else 1)

    def test_retained_dart_flight_resolves_on_its_side_of_the_world_expiry(self):
        for skipped in (False, True):
            for late in (False, True):
                with self.subTest(skipped=skipped, late=late):
                    run, ana, mei, vitals = self.injured_mei()
                    fight = run.match.combat
                    run.step({mei: (0, 0, 8)})
                    entry = run.frame
                    x, y, z = mei.mover.position
                    yaw, pitch = aim(fight._eye(ana), (x, y + 1, z))

                    def world_tick():
                        run.frame += 1
                        run.match.update(run.frame * .016, run.frame, frames=False)

                    for _ in range(244 if late else 232):
                        world_tick()
                    ana.take_commands([Command(run.frame, yaw=yaw, pitch=pitch, buttons=8)])
                    for _ in range(30):
                        if fight.rockets:
                            break
                        world_tick()
                        ana.take_commands([Command(run.frame, yaw=yaw, pitch=pitch)])
                    dart, = fight.rockets
                    self.assertTrue(cryo.active(mei))
                    world_tick()
                    self.assertIsNotNone(dart.last_tick)
                    if skipped:
                        run.frame = entry + 290
                        run.match.update(run.frame * .016, run.frame, frames=False)
                    else:
                        while run.frame < entry + 290:
                            world_tick()
                    self.assertFalse(cryo.active(mei))
                    self.assertEqual(fight.is_asleep(mei), late)
                    self.assertAlmostEqual(vitals.total, 225 if late else 230)
