"""Soldier attacks against real authored defensive states, reconstructed consumers."""

import unittest
from unittest.mock import patch

import test_soldier_combat_lane as lane
from test_game_combat import Run, aim, in_world, first_canister_sweep

from ow174.game import combat, defenses, projectiles, reinhardt_barrier, stats, winston_barrier, zarya_barriers as zarya_barrier
from ow174.game.collision import World, flat_world
from ow174.game.commands import Command
from ow174.game.content import heroes


class SoldierDefensesTests(unittest.TestCase):
    arena = lane.SoldierCombatLaneTests.arena
    input = lane.SoldierCombatLaneTests.input
    vitals = lane.SoldierCombatLaneTests.vitals
    fly = lane.SoldierCombatLaneTests.fly
    add_bot = lane.SoldierCombatLaneTests.add_bot

    def opponent(self, hero):
        run, soldier, other = self.arena()
        self.assertTrue(run.match.switch_hero(other, heroes()[hero]))
        other.team = 1 - soldier.team
        other.mover.position = (0.0, 0.0, 1.0)
        run.match.collision_world = flat_world(0.0)
        soldier.body_script.ult_seconds = 0
        other.body_script.ult_seconds = 0
        self.input(run, other)
        self.input(run, soldier)
        run.match.combat.history.clear()  # fixture teleports have no earlier viewed pose
        return run, soldier, other

    def test_fortify_body_head_and_visor_rifle_use_same_reduction_before_armour(self):
        for head, visor in ((False, False), (True, False), (False, True)):
            with self.subTest(head=head, visor=visor):
                run, soldier, other = self.opponent(defenses.ORISA)
                self.input(run, other, 8)
                self.assertAlmostEqual(defenses.fortify_scale(other), 0.6)
                if visor:
                    lane.SoldierCombatLaneTests.start_visor(self, run, soldier)
                before = self.vitals(run, other).total
                yaw, pitch = aim(run.match.combat._eye(soldier), (0.0, 1.9 if head else 0.8, 1.0))
                self.input(run, soldier, 1, yaw, pitch)
                self.assertAlmostEqual(before - self.vitals(run, other).total, 7, places=4)
                self.assertFalse(soldier.client.messages(combat.HIT)[-1]['+0x78']['+0x8'] & combat.CRITICAL)
                self.assertAlmostEqual(soldier.body_script.component.entity_var(342).value(),
                                       0 if visor else 7, places=4)

    def test_fortify_helix_reduces_each_direct_and_splash_hit_then_armour(self):
        run, soldier, other = self.opponent(defenses.ORISA)
        self.input(run, other, 8)
        before = self.vitals(run, other).total
        yaw, pitch = aim(run.match.combat._eye(soldier), (0.0, 0.8, 1.0))
        self.input(run, soldier, 2, yaw, pitch)
        rocket, = run.match.combat.rockets
        self.fly(run, rocket)
        self.assertAlmostEqual(before - self.vitals(run, other).total, 62, places=4)

    def test_in_flight_helix_orders_fortify_reduction_at_contact(self):
        for offset, damage in ((236, 62), (245, 110)):
            for jump in (False, True):
                with self.subTest(offset=offset, jump=jump):
                    run, soldier, orisa = self.player_arena(defenses.ORISA)
                    run.step({orisa: (0, 0, 8)})
                    launch = run.frame + offset
                    run.match.update(launch * combat.TICK, launch)
                    self.assertAlmostEqual(defenses.fortify_scale(orisa), 0.6)
                    before = self.vitals(run, orisa).total
                    yaw, pitch = aim(run.match.combat._eye(soldier), (0, .8, 1))
                    soldier.take_commands([Command(launch, yaw=yaw, pitch=pitch, buttons=2)])
                    run.match.update((launch + 1) * combat.TICK, launch + 1)
                    rocket, = run.match.combat.rockets
                    self.assertEqual(rocket.last_tick, launch + 1)

                    end = launch + 300
                    for tick in ((end,) if jump else range(launch + 2, end + 1)):
                        run.match.update(tick * combat.TICK, tick)
                    self.assertIsNone(defenses.fortify_scale(orisa))
                    self.assertAlmostEqual(before - self.vitals(run, orisa).total, damage, places=4)
                    self.assertEqual(soldier.body_script.component.entity_var(342).value(), damage)

    def test_fortify_melee_expires_and_does_not_leak_across_body_replacement(self):
        run, soldier, other = self.opponent(defenses.ORISA)
        other.mover.position = (0.0, 0.0, -1.5)
        self.input(run, other, 8)
        before = self.vitals(run, other).total
        self.input(run, soldier, 0x800)
        self.assertEqual(other.mover.velocity, (0, 0, 0))
        for _ in range(40):
            self.input(run, soldier)
        self.assertAlmostEqual(before - self.vitals(run, other).total, 13, places=4)
        for _ in range(270):
            self.input(run, other)
        self.assertIsNone(defenses.fortify_scale(other))
        self.assertTrue(run.match.switch_hero(other, heroes()[defenses.ORISA]))
        self.assertIsNone(defenses.fortify_scale(other))

    def test_transcendence_blocks_rifle_helix_melee_without_charge_then_expires(self):
        for button in (1, 2, 0x800):
            with self.subTest(button=button):
                run, soldier, other = self.opponent(defenses.ZENYATTA)
                other.mover.position = (0.0, 0.0, -1.5)
                script = other.body_script
                script.set_ult_charge(script.component.entity_var(338).value())
                self.input(run, other, 0x20)
                self.assertTrue(defenses.invulnerable(other))
                before = self.vitals(run, other).total
                yaw, pitch = aim(run.match.combat._eye(soldier), (0.0, 0.8, -1.5))
                self.input(run, soldier, button, yaw, pitch)
                for _ in range(45):
                    self.input(run, soldier)
                self.assertEqual(self.vitals(run, other).total, before)
                self.assertEqual(soldier.body_script.component.entity_var(342).value(), 0)
                self.assertEqual(soldier.stats.value(stats.HERO_DAMAGE), 0)
                for _ in range(380):
                    self.input(run, other)
                self.assertFalse(defenses.invulnerable(other))
                self.input(run, soldier, 1, yaw, pitch)
                self.assertEqual(self.vitals(run, other).total, before - 20)

    def test_transcendence_requires_charge_blocks_visor_and_retires_with_body(self):
        run, soldier, other = self.opponent(defenses.ZENYATTA)
        self.input(run, other, 0x20)
        self.assertFalse(defenses.invulnerable(other))
        self.input(run, other)
        script = other.body_script
        script.set_ult_charge(script.component.entity_var(338).value())
        self.input(run, other, 0x20)
        self.assertTrue(defenses.invulnerable(other))
        self.assertEqual(script.component.entity_var(342).value(), 0)
        lane.SoldierCombatLaneTests.start_visor(self, run, soldier)
        before = self.vitals(run, other).total
        self.input(run, soldier, 1)
        self.assertEqual(self.vitals(run, other).total, before)
        self.assertEqual(soldier.stats.value(stats.HERO_DAMAGE), 0)
        self.assertTrue(run.match.switch_hero(other, heroes()[defenses.ZENYATTA]))
        self.assertFalse(defenses.invulnerable(other))

    def test_transcendence_idle_expiry_restores_bidirectional_combat(self):
        run, soldier, zen = self.player_arena(defenses.ZENYATTA)
        fight = run.match.combat
        script = zen.body_script
        script.set_ult_charge(script.component.entity_var(338).value())
        reverse = aim(fight._eye(zen), (0, .8, -3))
        run.step({zen: (0, 0, 0x20)})
        activation = run.frame
        self.assertTrue(defenses.invulnerable(zen))
        run.step({zen: (*reverse, 1)})
        self.assertEqual(fight.rockets, [])
        self.assertEqual(self.vitals(run, soldier).total, 200)

        run.frame = activation + 380
        run.match.update(run.frame * combat.TICK, run.frame)
        self.assertFalse(defenses.invulnerable(zen))
        self.assertEqual(script.frame, run.frame - 1)
        forward = aim(fight._eye(soldier), (0, .8, 1))
        run.step({zen: (*reverse, 1), soldier: (*forward, 1)})
        self.assertEqual(self.vitals(run, zen).total, 180)
        self.assertEqual(len(fight.rockets), 1)
        for _ in range(15):
            run.step({})
        self.assertEqual(self.vitals(run, soldier).total, 152)
        self.assertEqual(script.component.entity_var(342).value(), 48)

    def test_transcendent_body_stops_rifle_instead_of_passing_it_to_an_enemy_behind(self):
        run, soldier, other = self.opponent(defenses.ZENYATTA)
        bot = self.add_bot(run, other.team, (0.0, 0.0, 4.0))
        script = other.body_script
        script.set_ult_charge(script.component.entity_var(338).value())
        self.input(run, other, 0x20)
        self.assertTrue(defenses.invulnerable(other))
        yaw, pitch = aim(run.match.combat._eye(soldier), (0.0, 1.1, 4.0))
        self.input(run, soldier, 1, yaw, pitch)
        self.assertEqual(self.vitals(run, other).total, 200)
        self.assertEqual(self.vitals(run, bot).total, 200)

    def player_arena(self, hero=defenses.REAPER, z=1.0, quanta=16000):
        run, soldier, other = self.arena()
        other.quanta = quanta
        self.assertTrue(run.match.switch_hero(other, heroes()[hero]))
        run.match.collision_world = flat_world(0.0)
        soldier.mover.position = [0.0, 0.0, -3.0]
        other.mover.position = [0.0, 0.0, z]
        for player in run.players:
            player.body_script.ult_seconds = 0
        run.step({})
        return run, soldier, other

    def test_wraith_lets_rifle_and_helix_reach_the_enemy_behind(self):
        for button, damage in ((1, 20), (2, 120)):
            with self.subTest(button=button):
                run, soldier, reaper = self.player_arena()
                bot = self.add_bot(run, reaper.team, (0.0, 0.0, 3.0))
                run.step({reaper: (0, 0, 8)})
                self.assertTrue(defenses.intangible(reaper))
                before = self.vitals(run, reaper).total
                yaw, pitch = aim(run.match.combat._eye(soldier), (0.0, 1.0, 3.0))
                run.step({soldier: (yaw, pitch, button)})
                for _ in range(16):
                    run.step({})
                self.assertEqual(self.vitals(run, reaper).total, before)
                self.assertEqual(self.vitals(run, bot).total, 200 - damage)
                self.assertAlmostEqual(soldier.body_script.component.entity_var(342).value(), damage)

    def test_wraith_blocks_melee_and_rifle_hits_resume_on_phase_expiry(self):
        run, soldier, reaper = self.player_arena(z=-1.5)
        run.step({reaper: (0, 0, 8)})
        self.assertTrue(defenses.intangible(reaper))
        before = self.vitals(run, reaper).total
        run.step({soldier: (0, 0, 0x800)})
        for _ in range(45):
            run.step({})
        self.assertEqual(self.vitals(run, reaper).total, before)
        self.assertEqual(soldier.client.messages(combat.HIT), [])
        self.assertEqual(soldier.stats.value(stats.HERO_DAMAGE), 0)
        for _ in range(141):
            run.step({})
        self.assertTrue(defenses.intangible(reaper))
        run.step({})
        self.assertFalse(defenses.intangible(reaper))
        self.assertIsNotNone(defenses.active_state(reaper, defenses.REAPER, 0x0020, 7))
        yaw, pitch = aim(run.match.combat._eye(soldier), (0.0, 0.8, -1.5))
        run.step({soldier: (yaw, pitch, 1)})
        self.assertEqual(self.vitals(run, reaper).total, before - 20)

    def test_visor_skips_wraith_and_acquires_an_off_axis_enemy(self):
        run, soldier, reaper = self.player_arena()
        bot = self.add_bot(run, reaper.team, (1.5, 0.0, 4.0))
        script = soldier.body_script
        script.set_ult_charge(script.component.entity_var(338).value())
        run.step({soldier: (0, 0, 0x20)})
        for _ in range(80):
            run.step({})
        run.step({reaper: (0, 0, 8)})
        self.assertTrue(defenses.intangible(reaper))
        before = self.vitals(run, reaper).total
        run.step({soldier: (0, 0, 1)})
        self.assertEqual(self.vitals(run, reaper).total, before)
        self.assertEqual(self.vitals(run, bot).total, 180)
        self.assertEqual(script.component.entity_var(342).value(), 0)

    def test_wraith_expires_with_no_owner_commands_and_late_input_cannot_restart_it(self):
        for jump in (False, True):
            with self.subTest(jump=jump):
                run, soldier, reaper = self.player_arena()
                run.step({reaper: (0, 0, 8)})
                script = reaper.body_script
                anchor = run.frame
                self.assertTrue(defenses.intangible(reaper))
                for tick in (range(anchor + 1, anchor + 189) if not jump else (anchor + 188,)):
                    run.match.update(tick * 0.016, tick)
                self.assertTrue(defenses.intangible(reaper))
                run.frame = anchor + 189
                run.match.update(run.frame * 0.016, run.frame)
                self.assertFalse(defenses.intangible(reaper))
                recovery = defenses.active_state(reaper, defenses.REAPER, 0x0020, 7)
                self.assertIsNotNone(recovery)
                self.assertEqual(script.frame, run.frame - 1)
                before = self.vitals(run, reaper).total
                yaw, pitch = aim(run.match.combat._eye(soldier), (0.0, 0.8, 1.0))
                soldier.take_commands([Command(run.frame, yaw=yaw, pitch=pitch, buttons=1)])
                self.assertEqual(self.vitals(run, reaper).total, before - 20)

                last_pose = reaper.mover.last_command
                reaper.take_commands([Command(run.frame - 1, buttons=8)])
                self.assertIs(reaper.mover.last_command, last_pose)
                reaper.take_commands([Command(run.frame, buttons=8)])
                self.assertEqual(reaper.mover.last_command.frame, run.frame)
                self.assertFalse(defenses.intangible(reaper))

    def test_wraith_recovery_and_cooldown_finish_during_a_long_command_gap(self):
        for quanta in (8000, 16000, 32000):
            with self.subTest(quanta=quanta):
                run, _, reaper = self.player_arena(quanta=quanta)
                script = reaper.body_script
                ticks = max(1, quanta // 16000)
                activation_frame = script.frame + ticks * 16000 // quanta
                run.frame += ticks
                run.match.update(run.frame * 0.016, run.frame)
                reaper.take_commands([Command(activation_frame, buttons=8)])
                self.assertTrue(defenses.intangible(reaper))
                frame = script.frame + 800 * 16000 // quanta
                run.frame += 800
                run.match.update(run.frame * 0.016, run.frame)
                self.assertFalse(defenses.intangible(reaper))
                self.assertIsNone(defenses.active_state(reaper, defenses.REAPER, 0x0020, 7))
                self.assertEqual(script.frame, frame - 1)
                reaper.take_commands([Command(frame, buttons=8)])
                self.assertTrue(defenses.intangible(reaper))

    def test_wraith_primary_cancel_restores_rifle_contact_and_starts_cooldown(self):
        run, soldier, reaper = self.player_arena()
        run.step({reaper: (0, 0, 8)})
        run.step({reaper: (0, 0, 1)})
        self.assertTrue(defenses.intangible(reaper))
        for _ in range(29):
            run.step({})
        run.step({reaper: (0, 0, 1)})
        self.assertFalse(defenses.intangible(reaper))

        recovery = defenses.active_state(reaper, defenses.REAPER, 0x0020, 7)
        self.assertIsNotNone(recovery)
        before = self.vitals(run, reaper).total
        yaw, pitch = aim(run.match.combat._eye(soldier), (0.0, 0.8, 1.0))
        run.step({soldier: (yaw, pitch, 1)})
        self.assertEqual(self.vitals(run, reaper).total, before - 20)
        for _ in range(25):
            run.step({})
        self.assertGreater(recovery.instance.vars[61].value(), 7.8)
        run.step({reaper: (0, 0, 8)})
        self.assertFalse(defenses.intangible(reaper))

    def test_in_flight_helix_resolves_against_protection_at_the_contact_tick(self):
        for hero, launch_offset, expected in (
            (defenses.REAPER, 176, (250, 80)),
            (defenses.REAPER, 186, (130, 200)),
            (defenses.MEI, 236, (250, 200)),
            (defenses.MEI, 248, (130, 200)),
            (defenses.ZENYATTA, 365, (200, 200)),
            (defenses.ZENYATTA, 375, (80, 200)),
        ):
            for jump in (False, True):
                with self.subTest(hero=hero, launch_offset=launch_offset, jump=jump):
                    run, soldier, protected = self.player_arena(hero)
                    bot = self.add_bot(run, protected.team, (0.0, 0.0, 6.0))
                    if hero == defenses.ZENYATTA:
                        script = protected.body_script
                        script.set_ult_charge(script.component.entity_var(338).value())
                    run.step({protected: (0, 0, 0x20 if hero == defenses.ZENYATTA else 8)})
                    launch = run.frame + launch_offset
                    run.match.update(launch * 0.016, launch)
                    self.assertTrue(defenses.invulnerable(protected))
                    yaw, pitch = aim(run.match.combat._eye(soldier), (0.0, 1.0, 6.0))
                    soldier.take_commands([Command(launch, yaw=yaw, pitch=pitch, buttons=2)])
                    run.match.update((launch + 1) * 0.016, launch + 1)
                    rocket, = run.match.combat.rockets
                    self.assertEqual(rocket.last_tick, launch + 1)
                    # Go beyond the ordinary retained NPC window.
                    end = launch + 300
                    for tick in ((end,) if jump else range(launch + 2, end + 1)):
                        run.match.update(tick * 0.016, tick)
                    self.assertFalse(defenses.invulnerable(protected))
                    remaining = self.vitals(run, protected).total, self.vitals(run, bot).total
                    self.assertEqual(remaining, expected)


    def test_field_heals_living_allies_during_wraith_cryo_and_transcendence(self):
        for hero, button in ((defenses.REAPER, 8), (defenses.MEI, 8), (defenses.ZENYATTA, 0x20)):
            with self.subTest(hero=hero):
                run, soldier, ally = self.player_arena(hero)
                soldier.mover.position = [1.7, 0, -3]
                soldier.mover.body = None
                ally.team = soldier.team
                enemy = run.match.add_player(3, "Enemy", soldier.hero.guid, 1 - soldier.team, False)
                in_world(run.match, enemy)
                run.players.append(enemy)
                self.assertTrue(run.match.switch_hero(enemy, soldier.hero))
                enemy.mover.position = [0, 0, 4]
                enemy.mover.body = None
                fight = run.match.combat
                pool = self.vitals(run, ally)
                full = pool.total
                angles = aim(fight._eye(enemy), (0, .8, 1))
                for _ in range(60):
                    run.step({enemy: (*angles, 1)})
                    if pool.total <= full - 60:
                        break
                self.assertEqual(pool.total, full - 60)
                self.assertEqual(pool.ult_healing_deficit, 60)

                run.step({soldier: (0, 0, 16)})
                for _ in range(80):
                    run.step({})
                    if fight.projectiles.shots:
                        break
                field, = fight.projectiles.shots
                self.assertEqual(field.spawn, projectiles.FIELD_ENTITY)
                self.assertIsNone(field.field_tick)
                if hero == defenses.ZENYATTA:
                    script = ally.body_script
                    script.set_ult_charge(script.component.entity_var(338).value())
                run.step({ally: (0, 0, button)})
                for _ in range(40):
                    if field.field_tick is not None:
                        break
                    run.step({})
                self.assertIsNotNone(field.field_tick)
                self.assertTrue(defenses.invulnerable(ally))
                before = pool.total
                charge = soldier.body_script.component.entity_var(342)
                points = charge.value()
                field_healing = soldier.stats.value(stats.HEALING)
                self_healing = ally.stats.value(stats.HEALING)
                ammo = enemy.body_script.component.instances[11].vars[53]
                rounds = ammo.value()
                enemy_damage = enemy.stats.value(stats.HERO_DAMAGE)
                for _ in range(12):
                    run.step({enemy: (*angles, 1)})
                    self.assertTrue(defenses.invulnerable(ally))
                expected = 12 * 35 * combat.TICK
                self.assertLess(ammo.value(), rounds)
                self.assertEqual(enemy.stats.value(stats.HERO_DAMAGE), enemy_damage)
                self.assertAlmostEqual(soldier.stats.value(stats.HEALING) - field_healing, expected)
                self.assertAlmostEqual(charge.value() - points, expected, places=4)
                own = ally.stats.value(stats.HEALING) - self_healing
                self.assertEqual(own > 0, hero == defenses.MEI)
                self.assertAlmostEqual(pool.total - before, expected + own)

                ally.team = enemy.team
                field_healing = soldier.stats.value(stats.HEALING)
                points = charge.value()
                before = pool.total
                self_healing = ally.stats.value(stats.HEALING)
                for _ in range(8):
                    run.step({})
                    self.assertTrue(defenses.invulnerable(ally))
                self.assertEqual(soldier.stats.value(stats.HEALING), field_healing)
                self.assertEqual(charge.value(), points)
                self.assertAlmostEqual(pool.total - before, ally.stats.value(stats.HEALING) - self_healing)
                ally.team = soldier.team
                run.step({})
                self.assertTrue(defenses.invulnerable(ally))
                self.assertAlmostEqual(soldier.stats.value(stats.HEALING) - field_healing, 35 * combat.TICK)
                self.assertAlmostEqual(charge.value() - points, 35 * combat.TICK, places=4)

    def test_cryo_remains_solid_against_rifle_helix_and_visor(self):
        for button, visor in ((1, False), (2, False), (1, True)):
            with self.subTest(button=button, visor=visor):
                run, soldier, mei = self.player_arena(defenses.MEI)
                rear = self.add_bot(run, mei.team, (0.0, 0.0, 6.0))
                script = soldier.body_script
                if visor:
                    script.set_ult_charge(script.component.entity_var(338).value())
                    run.step({soldier: (0, 0, 0x20)})
                    for _ in range(80):
                        run.step({})
                run.step({mei: (0, 0, 8)})
                self.assertTrue(defenses.cryo_protected(mei))
                yaw, pitch = aim(run.match.combat._eye(soldier), (0.0, 1.0, 6.0))
                weapon = script.component.instances[11]
                ammo = weapon.vars[53].value()
                run.step({soldier: (yaw, pitch, button)})
                if button == 2:
                    self.assertEqual(len(run.match.combat.rockets), 1)
                else:
                    self.assertEqual(weapon.vars[53].value(), ammo - 1)
                for _ in range(20):
                    run.step({})
                self.assertEqual(self.vitals(run, mei).total, 250)
                self.assertEqual(self.vitals(run, rear).total, 200)
                self.assertEqual(soldier.client.messages(combat.HIT), [])
                self.assertEqual(soldier.stats.value(stats.HERO_DAMAGE), 0)
                self.assertEqual(script.component.entity_var(342).value(), 0)
                self.assertEqual(run.match.combat.rockets, [])

    def test_cryo_blocks_melee_until_cancel_and_rejects_immediate_reentry(self):
        run, soldier, mei = self.player_arena(defenses.MEI, z=-1.5)
        run.step({mei: (0, 0, 8)})
        self.assertTrue(defenses.cryo_protected(mei))
        run.step({soldier: (0, 0, 0x800)})
        for _ in range(45):
            run.step({})
        self.assertEqual(self.vitals(run, mei).total, 250)
        self.assertEqual(soldier.client.messages(combat.HIT), [])
        run.step({mei: (0, 0, 2)})
        self.assertFalse(defenses.cryo_protected(mei))
        run.step({mei: (0, 0, 8)})
        self.assertFalse(defenses.cryo_protected(mei))
        yaw, pitch = aim(run.match.combat._eye(soldier), (0.0, 0.8, -1.5))
        run.step({soldier: (yaw, pitch, 1)})
        self.assertEqual(self.vitals(run, mei).total, 230)

    def test_cryo_expires_and_cooldown_recovers_without_mei_commands(self):
        run, soldier, mei = self.player_arena(defenses.MEI)
        run.step({mei: (0, 0, 8)})
        activation = run.frame
        self.assertTrue(defenses.cryo_protected(mei))
        run.match.update((activation + 249) * 0.016, activation + 249)
        self.assertTrue(defenses.cryo_protected(mei))
        run.frame = activation + 252
        run.match.update(run.frame * 0.016, run.frame)
        self.assertFalse(defenses.cryo_protected(mei))
        yaw, pitch = aim(run.match.combat._eye(soldier), (0.0, 0.8, 1.0))
        soldier.take_commands([Command(run.frame, yaw=yaw, pitch=pitch, buttons=1)])
        self.assertEqual(self.vitals(run, mei).total, 230)
        run.frame = activation + 1100
        run.match.update(run.frame * 0.016, run.frame)
        mei.take_commands([Command(run.frame, buttons=8)])
        self.assertTrue(defenses.cryo_protected(mei))

    def test_actual_rifle_wounds_heal_during_cryo_with_or_without_mei_commands(self):
        for jump in (False, True):
            with self.subTest(jump=jump):
                run, soldier, mei = self.player_arena(defenses.MEI)
                vitals = self.vitals(run, mei)
                yaw, pitch = aim(run.match.combat._eye(soldier), (0.0, 0.8, 1.0))
                for _ in range(128):
                    run.step({soldier: (yaw, pitch, 1)})
                    if vitals.total == 30:
                        break
                self.assertEqual(vitals.total, 30)
                self.vitals(run, soldier).damage(50)
                run.step({mei: (0, 0, 8)})
                activation = run.frame
                if jump:
                    run.frame += 246
                    run.match.update(run.frame * 0.016, run.frame)
                else:
                    for _ in range(245):
                        run.step({})
                self.assertTrue(defenses.cryo_protected(mei))
                self.assertAlmostEqual(vitals.total, 230)
                self.assertAlmostEqual(mei.stats.value(stats.HEALING), 200)
                self.assertEqual(self.vitals(run, soldier).total, 150)
                run.frame = activation + 260
                run.match.update(run.frame * 0.016, run.frame)
                self.assertFalse(defenses.cryo_protected(mei))
                self.assertAlmostEqual(vitals.total, 230)

    def test_actual_cryo_clears_ana_anti_heal_and_restores_rifle_wounds(self):
        for freeze in (False, True):
            with self.subTest(freeze=freeze):
                run, soldier, mei = self.player_arena(defenses.MEI)
                fight = run.match.combat
                pool = self.vitals(run, mei)
                angles = aim(fight._eye(soldier), (0, .8, 1))
                for _ in range(128):
                    run.step({soldier: (*angles, 1)})
                    if pool.total == 110:
                        break
                self.assertEqual(pool.total, 110)
                mei.team = soldier.team
                ana_hero = heroes()[0x02E000000000013B]
                ana = run.match.add_player(3, "Ana", ana_hero.guid, 1 - soldier.team, False)
                in_world(run.match, ana)
                run.players.append(ana)
                self.assertTrue(run.match.switch_hero(ana, ana_hero))
                ana.mover.position = [0, 0, 4]
                ana.mover.body = None
                ana.body_script.ult_seconds = 0
                run.step({})
                angles = aim(fight._eye(ana), tuple(mei.mover.position))
                run.step({ana: (*angles, 16)})
                for _ in range(35):
                    modifier = fight.heal_modifiers.get(mei.body)
                    if modifier is not None and modifier.anti_until > run.frame:
                        break
                    run.step({})
                self.assertIsNotNone(modifier)
                deadline = modifier.anti_until
                self.assertGreater(deadline, run.frame + 30)
                self.assertEqual(pool.total, 50)
                self.assertEqual(fight._healing_factor(mei.body), 0)

                run.step({mei: (0, 0, 8)} if freeze else {})
                for _ in range(25):
                    run.step({})
                self.assertLess(run.frame, deadline)
                self.assertEqual(defenses.cryo_protected(mei), freeze)
                self.assertEqual(fight._healing_factor(mei.body), int(freeze))
                healed = mei.stats.value(stats.HEALING)
                self.assertEqual(healed > 0, freeze)
                self.assertAlmostEqual(pool.total, 50 + healed)

    def test_cryo_cancel_stops_healing_and_full_health_cannot_bank_healing(self):
        for cancel in (False, True):
            with self.subTest(cancel=cancel):
                run, soldier, mei = self.player_arena(defenses.MEI)
                vitals = self.vitals(run, mei)
                yaw, pitch = aim(run.match.combat._eye(soldier), (0.0, 0.8, 1.0))
                run.step({soldier: (yaw, pitch, 1)})
                self.assertEqual(vitals.total, 230)
                run.step({mei: (0, 0, 8)})
                if cancel:
                    for _ in range(20):
                        run.step({})
                    run.step({mei: (0, 0, 2)})
                    self.assertGreater(vitals.total, 245)
                    self.assertLess(vitals.total, 250)
                else:
                    run.frame += 260
                    run.match.update(run.frame * 0.016, run.frame)
                    self.assertAlmostEqual(vitals.total, 250)
                    self.assertAlmostEqual(mei.stats.value(stats.HEALING), 20)
                self.assertFalse(defenses.cryo_protected(mei))
                healed = vitals.total
                run.step({soldier: (yaw, pitch, 1)})
                for _ in range(30):
                    run.step({})
                self.assertAlmostEqual(vitals.total, healed - 20)


    def test_cryo_replacement_discards_unfinished_healing(self):
        run, soldier, mei = self.player_arena(defenses.MEI)
        yaw, pitch = aim(run.match.combat._eye(soldier), (0.0, 0.8, 1.0))
        run.step({soldier: (yaw, pitch, 1)})
        run.step({mei: (0, 0, 8)})
        for _ in range(10):
            run.step({})
        healed = mei.stats.value(stats.HEALING)
        self.assertGreater(healed, 0)
        body = mei.body
        self.assertIn(body, run.match.combat.cryo_samples)
        self.assertTrue(run.match.switch_hero(mei, heroes()[defenses.MEI]))
        self.assertNotIn(body, run.match.combat.cryo_samples)
        self.assertFalse(defenses.cryo_protected(mei))
        run.frame += 300
        run.match.update(run.frame * 0.016, run.frame)
        self.assertEqual(mei.stats.value(stats.HEALING), healed)


    def test_particle_barrier_consumes_rifle_then_breaking_helix_without_overflow(self):
        run, soldier, zarya = self.player_arena(zarya_barrier.HERO)
        fight = run.match.combat
        run.step({zarya: (0, 0, 8)})
        phase = fight.zarya_barriers.sync(zarya)
        self.assertIsNotNone(phase)
        self.assertEqual(fight.zarya_barriers.pools[zarya.body][1], 200)
        yaw, pitch = aim(fight._eye(soldier), (0.0, 0.8, 1.0))
        for _ in range(55):
            run.step({soldier: (yaw, pitch, 1)})
            if fight.zarya_barriers.pools[zarya.body][1] <= 100:
                break
        self.assertEqual(fight.zarya_barriers.pools[zarya.body][1], 100)
        run.step({soldier: (yaw, pitch, 2)})
        for _ in range(24):
            run.step({})
        self.assertFalse(phase.active)
        self.assertEqual(zarya.body_script.component.entity_var(87).value(), 40)
        self.assertEqual(self.vitals(run, zarya).total, 400)
        self.assertEqual(soldier.client.messages(combat.HIT), [])
        self.assertEqual(soldier.body_script.component.entity_var(342).value(), 0)
        run.step({zarya: (0, 0, 8)})
        self.assertFalse(phase.active)
        for _ in range(20):
            run.step({soldier: (yaw, pitch, 1)})
            if self.vitals(run, zarya).total < 400:
                break
        self.assertEqual(self.vitals(run, zarya).total, 380)
        self.assertEqual(soldier.body_script.component.entity_var(342).value(), 20)

    def test_particle_barrier_stops_a_body_miss_and_drops_cover_on_replacement(self):
        run, soldier, zarya = self.player_arena(zarya_barrier.HERO)
        rear = self.add_bot(run, zarya.team, (1.5, 0.0, 4.0))
        fight = run.match.combat
        run.step({zarya: (0, 0, 8)})
        yaw, pitch = aim(fight._eye(soldier), (1.5, 0.8, 4.0))
        run.step({soldier: (yaw, pitch, 1)})
        self.assertEqual(fight.zarya_barriers.pools[zarya.body][1], 180)
        self.assertEqual(self.vitals(run, rear).total, 200)
        old = zarya.body
        self.assertTrue(run.match.switch_hero(zarya, heroes()[zarya_barrier.HERO]))
        self.assertNotIn(old, fight.zarya_barriers.pools)
        for _ in range(10):
            run.step({})
        run.step({soldier: (yaw, pitch, 1)})
        self.assertEqual(self.vitals(run, rear).total, 180)

    def test_particle_barrier_consumes_actual_melee_and_visor(self):
        for visor in (False, True):
            with self.subTest(visor=visor):
                run, soldier, zarya = self.player_arena(zarya_barrier.HERO, z=1.0 if visor else -1.5)
                if visor:
                    script = soldier.body_script
                    script.set_ult_charge(script.component.entity_var(338).value())
                    run.step({soldier: (0, 0, 0x20)})
                    for _ in range(80):
                        run.step({})
                run.step({zarya: (0, 0, 8)})
                run.step({soldier: (0, 0, 1 if visor else 0x800)})
                self.assertEqual(zarya.mover.velocity, (0, 0, 0))
                for _ in range(35):
                    run.step({})
                self.assertEqual(run.match.combat.zarya_barriers.pools[zarya.body][1], 180 if visor else 170)
                self.assertEqual(self.vitals(run, zarya).total, 400)
                self.assertEqual(soldier.client.messages(combat.HIT), [])

    def test_rifle_and_visor_check_particle_barrier_at_the_viewed_pose(self):
        for visor in (False, True):
            for viewed, current in ((0, 30), (30, 0)):
                with self.subTest(visor=visor, viewed=viewed, current=current):
                    run, soldier, zarya = self.player_arena(zarya_barrier.HERO)
                    if visor:
                        script = soldier.body_script
                        script.set_ult_charge(script.component.entity_var(338).value())
                        run.step({soldier: (0, 0, 0x20)})
                        for _ in range(80):
                            run.step({})
                    zarya.mover.position[0] = viewed
                    zarya.mover.body = None
                    run.step({zarya: (0, 0, 8)})
                    for _ in range(12):
                        run.step({})
                    barrier = run.match.combat.zarya_barriers
                    self.assertEqual(barrier.pools[zarya.body][1], 200)
                    zarya.mover.position[0] = current
                    zarya.mover.body = None
                    run.frame += 1
                    run.match.update(run.frame * combat.TICK, run.frame)
                    soldier.take_commands([Command(run.frame, buttons=1, extra=128)])
                    self.assertEqual(barrier.pools[zarya.body][1], 180 if viewed == 0 else 200)
                    self.assertEqual(self.vitals(run, zarya).total, 400)
                    self.assertFalse(soldier.client.messages(combat.HIT))
                    for _ in range(12):
                        run.step({})
                    run.step({soldier: (0, 0, 1)})
                    self.assertEqual(barrier.pools[zarya.body][1], 180)
                    self.assertEqual(self.vitals(run, zarya).total, 400)

    def test_helix_checks_particle_barrier_at_flight_contact(self):
        for initial, final in ((0, 30), (30, 0)):
            with self.subTest(launch_x=initial, impact_x=final):
                run, soldier, zarya = self.player_arena(zarya_barrier.HERO, z=8)
                zarya.mover.position[0] = initial
                zarya.mover.body = None
                run.step({zarya: (0, 0, 8)})
                for _ in range(12):
                    run.step({})
                fight = run.match.combat
                run.step({soldier: (0, 0, 2)})
                rocket, = fight.rockets
                zarya.mover.position[0] = final
                zarya.mover.body = None
                for _ in range(20):
                    run.step({})
                if final == 0:
                    self.assertNotIn(rocket, fight.rockets)
                else:
                    self.assertIn(rocket, fight.rockets)
                    self.assertGreater(rocket.position[2], 10)
                self.assertEqual(fight.zarya_barriers.pools[zarya.body][1], 80 if final == 0 else 200)
                self.assertEqual(self.vitals(run, zarya).total, 400)
                self.assertFalse(soldier.client.messages(combat.HIT))

    def test_melee_checks_active_particle_barrier_at_the_viewed_pose(self):
        near, far = -1.5, 2.0
        for before, after, covered in ((near, far, False), (far, near, False), (-.5, far, True)):
            with self.subTest(viewed=before, current=after, covered=covered):
                run, soldier, zarya = self.player_arena(zarya_barrier.HERO, z=before)
                run.step({zarya: (0, 0, 8)})
                for _ in range(12):
                    run.step({})
                barrier = run.match.combat.zarya_barriers
                self.assertEqual(barrier.pools[zarya.body][1], 200)
                zarya.mover.position[2] = after
                zarya.mover.body = None
                if covered:
                    run.match.collision_world = World([
                        (-5, -1, -2.5, 5, -1, -2.5, 5, 5, -2.5),
                        (-5, -1, -2.5, 5, 5, -2.5, -5, 5, -2.5),
                    ])
                run.frame += 1
                run.match.update(run.frame * combat.TICK, run.frame)
                soldier.take_commands([Command(run.frame, buttons=0x800, extra=128)])
                remaining = 170 if before == near else 200
                self.assertEqual(barrier.pools[zarya.body][1], remaining)
                self.assertEqual(self.vitals(run, zarya).total, 400)
                self.assertEqual(zarya.mover.velocity, (0, 0, 0))
                if covered:
                    continue
                run.step({})
                run.step({soldier: (0, 0, 0x800)})
                self.assertEqual(barrier.pools[zarya.body][1], remaining)
                for _ in range(70):
                    run.step({})
                run.step({soldier: (0, 0, 0x800)})
                self.assertEqual(barrier.pools[zarya.body][1], 170)
                self.assertEqual(self.vitals(run, zarya).total, 400)
                self.assertEqual(zarya.mover.velocity, (0, 0, 0))

    def test_breaking_melee_does_not_spill_damage_or_force_through_particle_barrier(self):
        run, soldier, zarya = self.player_arena(zarya_barrier.HERO, z=-.5)
        run.step({zarya: (0, 0, 8)})
        barrier = run.match.combat.zarya_barriers
        for _ in range(100):
            run.step({soldier: (0, 0, 1)})
            if barrier.pools[zarya.body][1] <= 20:
                break
        self.assertEqual(barrier.pools[zarya.body][1], 20)
        run.step({soldier: (0, 0, 0x800)})
        self.assertIsNone(barrier.phase(zarya))
        self.assertEqual(self.vitals(run, zarya).total, 400)
        self.assertEqual(zarya.mover.velocity, (0, 0, 0))
        self.assertFalse(soldier.client.messages(combat.HIT))
        for _ in range(70):
            run.step({})
        run.step({soldier: (0, 0, 0x800)})
        self.assertEqual(self.vitals(run, zarya).total, 370)
        self.assertEqual(len(soldier.client.messages(combat.HIT)), 1)

    def test_allied_particle_barrier_does_not_stop_soldier_rifle(self):
        run, soldier, zarya = self.player_arena(zarya_barrier.HERO)
        zarya.team = soldier.team
        rear = self.add_bot(run, 1 - soldier.team, (0.0, 0.0, 4.0))
        run.step({zarya: (0, 0, 8)})
        fight = run.match.combat
        yaw, pitch = aim(fight._eye(soldier), (0.0, 0.8, 4.0))
        run.step({soldier: (yaw, pitch, 1)})
        self.assertEqual(fight.zarya_barriers.pools[zarya.body][1], 200)
        self.assertEqual(self.vitals(run, rear).total, 180)


    def test_particle_barrier_expires_and_rearms_without_zarya_commands(self):
        run, soldier, zarya = self.player_arena(zarya_barrier.HERO)
        fight = run.match.combat
        run.step({zarya: (0, 0, 8)})
        activation = run.frame
        self.assertIsNotNone(fight.zarya_barriers.sync(zarya))
        run.frame = activation + 129
        run.match.update(run.frame * 0.016, run.frame)
        self.assertIsNone(fight.zarya_barriers.sync(zarya))
        yaw, pitch = aim(fight._eye(soldier), (0.0, 0.8, 1.0))
        soldier.take_commands([Command(run.frame, yaw=yaw, pitch=pitch, buttons=1)])
        self.assertEqual(self.vitals(run, zarya).total, 380)
        run.frame = activation + 800
        run.match.update(run.frame * 0.016, run.frame)
        zarya.take_commands([Command(run.frame, buttons=8)])
        self.assertIsNotNone(fight.zarya_barriers.sync(zarya))
        self.assertEqual(fight.zarya_barriers.pools[zarya.body][1], 200)


    def projected_arena(self):
        run = Run(names=("Soldier", "Zarya", "Ally"))
        run.match.bots.clear()
        run.match._pending_bot_scripts.clear()
        run.match.collision_world = flat_world(0.0)
        soldier, zarya, ally = run.players
        ally.team = zarya.team
        self.assertTrue(run.match.switch_hero(zarya, heroes()[zarya_barrier.HERO]))
        for player, position in zip(run.players, ((0., 0., -3.), (0., 0., 6.), (0., 0., 1.)), strict=True):
            player.mover.position = list(position)
            player.body_script.ult_seconds = 0
        run.step({})
        return run, soldier, zarya, ally

    def test_projected_barrier_consumes_soldier_rifle_and_helix_for_ally(self):
        run, soldier, zarya, ally = self.projected_arena()
        fight = run.match.combat
        yaw, pitch = aim(fight._eye(zarya), (0.0, 1.0, 1.0))
        run.step({zarya: (yaw, pitch, 16)})
        self.assertEqual(fight.zarya_barriers.projected[zarya.body][1:], (200, ally.body))
        yaw, pitch = aim(fight._eye(soldier), (0.0, 1.0, 1.0))
        for _ in range(55):
            run.step({soldier: (yaw, pitch, 1)})
            if fight.zarya_barriers.projected[zarya.body][1] <= 100:
                break
        self.assertEqual(fight.zarya_barriers.projected[zarya.body][1], 100)
        run.step({soldier: (yaw, pitch, 2)})
        for _ in range(25):
            run.step({})
        self.assertNotIn(zarya.body, fight.zarya_barriers.projected)
        self.assertEqual(zarya.body_script.component.entity_var(87).value(), 40)
        self.assertEqual(self.vitals(run, ally).total, 200)
        self.assertEqual(soldier.client.messages(combat.HIT), [])
        self.assertEqual(soldier.body_script.component.entity_var(342).value(), 0)
        for _ in range(20):
            run.step({soldier: (yaw, pitch, 1)})
            if self.vitals(run, ally).total < 200:
                break
        self.assertEqual(self.vitals(run, ally).total, 180)

    def test_projected_barrier_rejects_enemy_off_axis_out_of_range_and_wall(self):
        for condition in ("enemy", "off-axis", "range", "wall"):
            with self.subTest(condition=condition):
                run, soldier, zarya, ally = self.projected_arena()
                if condition == "enemy":
                    ally.team = soldier.team
                elif condition == "range":
                    ally.mover.position[2] = -40
                elif condition == "wall":
                    run.match.collision_world = World([
                        (-10, 0, 3, 10, 0, 3, 10, 10, 3),
                        (-10, 0, 3, 10, 10, 3, -10, 10, 3),
                    ])
                run.step({zarya: (0 if condition == "off-axis" else -32768, 0, 16)})
                self.assertIsNone(run.match.combat.zarya_barriers.projected_phase(zarya))

    def test_projected_barrier_retires_with_recipient_and_rearms_after_idle_cooldown(self):
        run, soldier, zarya, ally = self.projected_arena()
        fight = run.match.combat
        yaw, pitch = aim(fight._eye(zarya), (0.0, 1.0, 1.0))
        run.step({zarya: (yaw, pitch, 16)})
        self.assertIsNotNone(fight.zarya_barriers.projected_phase(zarya))
        self.assertTrue(run.match.switch_hero(ally, ally.hero))
        self.assertNotIn(zarya.body, fight.zarya_barriers.projected)
        self.assertIsNone(fight.zarya_barriers.projected_phase(zarya))
        ally.mover.position = [0, 0, 1]
        run.step({})
        run.step({zarya: (yaw, pitch, 16)})
        self.assertIsNone(fight.zarya_barriers.projected_phase(zarya))
        run.frame += 680
        run.match.update(run.frame * 0.016, run.frame)
        zarya.take_commands([Command(run.frame, yaw=yaw, pitch=pitch, buttons=16)])
        self.assertEqual(fight.zarya_barriers.projected[zarya.body][1:], (200, ally.body))
        run.frame += 129
        run.match.update(run.frame * 0.016, run.frame)
        self.assertIsNone(fight.zarya_barriers.projected_phase(zarya))
        yaw, pitch = aim(fight._eye(soldier), (0.0, 1.0, 1.0))
        soldier.take_commands([Command(run.frame, yaw=yaw, pitch=pitch, buttons=1)])
        self.assertEqual(self.vitals(run, ally).total, 180)


    def deploy_winston_dome(self, z):
        run, soldier, winston = self.player_arena(winston_barrier.HERO, z=z)
        manager = run.match.combat.winston_barriers
        run.step({winston: (0, 0, 16)})
        for _ in range(45):
            if manager.actors:
                break
            run.step({})
        dome, = manager.actors.values()
        self.assertEqual(dome.health, 700)
        return run, soldier, winston, dome

    def test_front_barrier_shields_the_rear_barrier_from_the_same_helix(self):
        for front, breaking in (("dome", False), ("particle", False), ("dome", True), ("particle", True)):
            with self.subTest(front=front, breaking=breaking):
                run, soldier, winston, dome = self.deploy_winston_dome(4.0)
                zarya = run.match.add_player(3, "Zarya", zarya_barrier.HERO, winston.team, False)
                in_world(run.match, zarya)
                run.players.append(zarya)
                run.match.switch_hero(zarya, heroes()[zarya_barrier.HERO])
                zarya.mover.position = [0, 0, 1 if front == "dome" else -.5]
                zarya.mover.body = None
                run.step({zarya: (0, 0, 8)})
                fight = run.match.combat
                pools = fight.zarya_barriers.pools
                self.assertEqual(pools[zarya.body][1], 200)
                if breaking:
                    # Limit the front pool to isolate its last impact.
                    if front == "dome":
                        dome.health = 100
                    else:
                        stamp, _ = pools[zarya.body]
                        pools[zarya.body] = (stamp, 100)
                for _ in range(8):
                    run.step({})
                run.step({soldier: (0, 0, 2)})
                for _ in range(20):
                    run.step({})
                self.assertFalse(fight.rockets)
                self.assertEqual(dome.health, (0 if breaking else 580) if front == "dome" else 700)
                bubble_hp = pools.get(zarya.body, (None, 0))[1]
                self.assertEqual(bubble_hp, 200 if front == "dome" else (0 if breaking else 80))
                self.assertEqual(self.vitals(run, winston).total, 500)
                self.assertEqual(self.vitals(run, zarya).total, 400)
                self.assertFalse(soldier.client.messages(combat.HIT))
                if breaking:
                    if front == "particle":
                        zarya.mover.position[0] = 3
                        zarya.mover.body = None
                    for _ in range(8):
                        run.step({})
                    for _ in range(30):
                        run.step({soldier: (0, 0, 1)})
                        if dome.health == 680 or pools.get(zarya.body, (None, 0))[1] == 180:
                            break
                    self.assertEqual(dome.health, 0 if front == "dome" else 680)
                    self.assertEqual(pools.get(zarya.body, (None, 0))[1], 180 if front == "dome" else 0)
                    self.assertFalse(soldier.client.messages(combat.HIT))

    def test_winston_dome_only_intercepts_attacks_crossing_its_shell(self):
        for z, absorbed in ((4.0, True), (1.0, False)):
            for button, amount, body_damage in ((1, 20, 15), (2, 120, 110)):
                with self.subTest(z=z, button=button):
                    run, soldier, winston, dome = self.deploy_winston_dome(z)
                    fight = run.match.combat
                    before = self.vitals(run, winston).total
                    yaw, pitch = aim(fight._eye(soldier), (0.0, 0.8, z))
                    run.step({soldier: (yaw, pitch, button)})
                    for _ in range(23):
                        run.step({})
                    self.assertEqual(dome.health, 700 - amount if absorbed else 700)
                    expected = before if absorbed else before - body_damage
                    self.assertEqual(self.vitals(run, winston).total, expected)
                    if absorbed:
                        self.assertFalse(soldier.client.messages(combat.HIT))
                        self.assertEqual(soldier.body_script.component.entity_var(342).value(), 0)

    def test_winston_dome_expires_on_its_world_clock_without_owner_input(self):
        run, soldier, winston, dome = self.deploy_winston_dome(4.0)
        fight = run.match.combat
        run.frame += 580
        run.match.update(run.frame * 0.016, run.frame)
        self.assertTrue(dome.retired)
        self.assertFalse(fight.winston_barriers.actors)
        before = self.vitals(run, winston).total
        yaw, pitch = aim(fight._eye(soldier), (0.0, 0.8, 4.0))
        run.step({soldier: (yaw, pitch, 1)})
        self.assertEqual(self.vitals(run, winston).total, before - 15)

    def test_held_rifle_resumes_body_hits_as_winston_dome_expires(self):
        run, soldier, winston, dome = self.deploy_winston_dome(4.0)
        fight = run.match.combat
        pool = self.vitals(run, winston)
        yaw, pitch = aim(fight._eye(soldier), (0.0, 0.8, 4.0))
        run.frame += 540
        run.match.update(run.frame * combat.TICK, run.frame)
        self.assertFalse(dome.retired)
        fire = {soldier: (yaw, pitch, 1)}
        run.step(fire)
        self.assertEqual(dome.health, 680)
        self.assertEqual(pool.total, 500)
        for _ in range(40):
            run.step(fire)
            if dome.retired:
                break
            self.assertEqual(pool.total, 500)
            self.assertFalse(soldier.client.messages(combat.HIT))
        self.assertTrue(dome.retired)
        last_shield_health = dome.health
        for _ in range(12):
            if pool.total < 500:
                break
            run.step(fire)
        self.assertEqual(dome.health, last_shield_health)
        self.assertEqual(pool.total, 485)
        self.assertEqual(len(soldier.client.messages(combat.HIT)), 1)


    def test_held_visor_rifle_hits_off_axis_winston_when_dome_expires(self):
        run, soldier, winston, dome = self.deploy_winston_dome(4.0)
        pool = self.vitals(run, winston)
        yaw, pitch = 4096, 0
        run.frame += 460
        run.match.update(run.frame * combat.TICK, run.frame)
        script = soldier.body_script
        script.set_ult_charge(script.component.entity_var(338).value())
        run.step({soldier: (yaw, pitch, 0x20)})
        for _ in range(79):
            run.step({})
        self.assertFalse(dome.retired)
        fire = {soldier: (yaw, pitch, 1)}
        run.step(fire)
        self.assertEqual(dome.health, 680)
        self.assertEqual(pool.total, 500)
        for _ in range(40):
            run.step(fire)
            if dome.retired:
                break
            self.assertEqual(pool.total, 500)
            self.assertFalse(soldier.client.messages(combat.HIT))
        self.assertTrue(dome.retired)
        last_shield_health = dome.health
        for _ in range(12):
            if pool.total < 500:
                break
            run.step(fire)
        self.assertEqual(dome.health, last_shield_health)
        self.assertEqual(pool.total, 485)
        self.assertEqual(len(soldier.client.messages(combat.HIT)), 1)
        self.assertEqual(script.component.entity_var(342).value(), 0)


    def test_winston_dome_cooldown_rejects_early_cast_then_restores_cover(self):
        run, soldier, winston, first = self.deploy_winston_dome(4.0)
        fight = run.match.combat
        manager = fight.winston_barriers
        yaw, pitch = aim(fight._eye(soldier), (0.0, 0.8, 4.0))
        run.step({soldier: (yaw, pitch, 1)})
        self.assertEqual(first.health, 680)
        pool = self.vitals(run, winston)
        self.assertEqual(pool.total, 500)

        run.frame += 580
        run.match.update(run.frame * combat.TICK, run.frame)
        self.assertTrue(first.retired)
        run.step({winston: (0, 0, 0), soldier: (yaw, pitch, 0)})
        run.step({winston: (0, 0, 16), soldier: (yaw, pitch, 1)})
        self.assertEqual(pool.total, 485)
        run.step({winston: (0, 0, 0), soldier: (yaw, pitch, 0)})
        for _ in range(45):
            run.step({})
            self.assertFalse(manager.actors)

        run.frame += 240
        run.match.update(run.frame * combat.TICK, run.frame)
        run.step({winston: (0, 0, 16)})
        for _ in range(45):
            if manager.actors:
                break
            run.step({})
        second, = manager.actors.values()
        self.assertNotEqual(second.body, first.body)
        self.assertEqual(second.health, 700)
        run.step({soldier: (yaw, pitch, 1)})
        self.assertEqual(second.health, 680)
        self.assertEqual(pool.total, 485)


    def test_in_flight_helix_resolves_before_or_after_winston_dome_expiry(self):
        for offset, expected in ((553, (580, 500)), (561, (700, 390))):
            for jump in (False, True):
                with self.subTest(offset=offset, jump=jump):
                    run, soldier, winston, dome = self.deploy_winston_dome(4.0)
                    fight = run.match.combat
                    launch = run.frame + offset
                    run.match.update(launch * combat.TICK, launch)
                    self.assertFalse(dome.retired)
                    angles = aim(fight._eye(soldier), (0.0, 0.8, 4.0))
                    soldier.take_commands([Command(launch, yaw=angles[0], pitch=angles[1], buttons=2)])
                    run.match.update((launch + 1) * combat.TICK, launch + 1)
                    rocket, = fight.rockets
                    self.assertEqual(rocket.last_tick, launch + 1)
                    end = launch + 300
                    for tick in ((end,) if jump else range(launch + 2, end + 1)):
                        run.match.update(tick * combat.TICK, tick)
                    self.assertTrue(dome.retired)
                    self.assertEqual((dome.health, self.vitals(run, winston).total), expected)

    def test_winston_dome_consumes_whole_breaking_helix_and_allied_rifle_passes(self):
        run, soldier, winston, dome = self.deploy_winston_dome(4.0)
        fight = run.match.combat
        yaw, pitch = aim(fight._eye(soldier), (0.0, 0.8, 4.0))
        before = self.vitals(run, winston).total
        for _ in range(320):
            run.step({soldier: (yaw, pitch, 1)})
            if dome.health == 100:
                break
        self.assertEqual(dome.health, 100)
        for _ in range(150):
            run.step({soldier: (yaw, pitch, 2)})
            if dome.health <= 0:
                break
        self.assertEqual(dome.health, 0)
        self.assertEqual(self.vitals(run, winston).total, before)
        self.assertFalse(soldier.client.messages(combat.HIT))
        for _ in range(130):
            run.step({soldier: (yaw, pitch, 4)})
        self.assertEqual(soldier.body_script.component.instances[11].vars[53].value(), 30)
        for _ in range(35):
            yaw, pitch = aim(fight._eye(soldier), (0.0, 0.8, 4.0))
            run.step({soldier: (yaw, pitch, 1)})
            if self.vitals(run, winston).total < before:
                break
        self.assertEqual(self.vitals(run, winston).total, before - 15)
        self.assertTrue(dome.retired)

        run, soldier, winston, dome = self.deploy_winston_dome(4.0)
        # Team is captured at creation. Arrange an allied shooter to fire through the existing dome.
        soldier.team = winston.team
        rear = self.add_bot(run, 1 - soldier.team, (0.0, 0.0, 5.0))
        fight = run.match.combat
        yaw, pitch = aim(fight._eye(soldier), (0.0, 0.8, 5.0))
        run.step({soldier: (yaw, pitch, 1)})
        self.assertEqual(dome.health, 700)
        self.assertEqual(self.vitals(run, rear).total, 180)

    def test_winston_dome_protects_rear_target_after_owner_is_killed(self):
        run, soldier, winston, dome = self.deploy_winston_dome(4.0)
        fight = run.match.combat
        soldier.mover.position = [0, 0, 2]
        soldier.mover.body = None
        yaw, pitch = aim(fight._eye(soldier), (0.0, 1.9, 4.0))
        for _ in range(250):
            run.step({soldier: (yaw, pitch, 1)})
            if fight.is_dead(winston):
                break
        self.assertTrue(fight.is_dead(winston))
        self.assertEqual(dome.health, 700)
        self.assertIs(fight.winston_barriers.actors[dome.body], dome)

        rear = self.add_bot(run, winston.team, (1, 0, 5))
        soldier.mover.position = [0, 0, -3]
        soldier.mover.body = None
        yaw, pitch = aim(fight._eye(soldier), (1.0, 0.8, 5.0))
        for _ in range(8):
            run.step({soldier: (yaw, pitch, 0)})
        for _ in range(20):
            run.step({soldier: (yaw, pitch, 1)})
            if dome.health < 700:
                break
        self.assertEqual(dome.health, 680)
        self.assertEqual(self.vitals(run, rear).total, 200)

        run.frame += 580
        run.match.update(run.frame * combat.TICK, run.frame)
        self.assertTrue(dome.retired)
        self.assertNotIn(dome.body, fight.winston_barriers.actors)
        run.step({soldier: (yaw, pitch, 0)})
        run.step({soldier: (yaw, pitch, 1)})
        self.assertEqual(self.vitals(run, rear).total, 180)


    def test_winston_dome_intercepts_actual_visor_rifle(self):
        run, soldier, winston, dome = self.deploy_winston_dome(4.0)
        script = soldier.body_script
        script.set_ult_charge(script.component.entity_var(338).value())
        run.step({soldier: (0, 0, 0x20)})
        for _ in range(80):
            run.step({})
        run.step({soldier: (0, 0, 1)})
        self.assertEqual(dome.health, 680)
        self.assertEqual(self.vitals(run, winston).total, 500)
        self.assertFalse(soldier.client.messages(combat.HIT))


    def deploy_orisa_barrier(self):
        run, soldier, orisa = self.player_arena(defenses.ORISA, z=5.0)
        service = run.match.combat.orisa_barriers
        if service.geometry is None:
            self.skipTest("original Orisa collision descriptor is required")
        run.step({orisa: (-32768, 8192, 16)})
        flight = None
        for _ in range(60):
            flight = service.flights.get(orisa.body, flight)
            if orisa.body in service.actors:
                break
            run.step({orisa: (-32768, 8192, 0)})
        self.assertIsNotNone(flight)
        self.assertAlmostEqual(flight.gravity, 9.8, places=5)
        shield = service.actors[orisa.body]
        self.assertLess(shield.position[2], orisa.mover.position[2])
        self.assertEqual(shield.health, 600)
        return run, soldier, orisa, shield

    def test_field_canister_contacts_particle_dome_and_orisa_barriers(self):
        for kind in ("particle", "dome", "orisa"):
            with self.subTest(barrier=kind):
                if kind == "particle":
                    run, soldier, defender = self.player_arena(zarya_barrier.HERO, z=1.0)
                    run.step({defender: (0, 0, 8)})
                    shield = defender
                    launch_z, ally_z = -.8, 1.5
                elif kind == "dome":
                    run, soldier, defender, shield = self.deploy_winston_dome(4.0)
                    launch_z, ally_z = -1.35, 1.0
                else:
                    run, soldier, defender, shield = self.deploy_orisa_barrier()
                    launch_z, ally_z = shield.position[2] - .3, 4.0
                soldier.mover.position = [0, 0, launch_z]
                soldier.mover.body = None
                fight = run.match.combat
                service = {
                    "particle": fight.zarya_barriers,
                    "dome": fight.winston_barriers,
                    "orisa": fight.orisa_barriers,
                }[kind]
                defender_health = self.vitals(run, defender).total
                ally = run.match.add_player(3, "Ally", soldier.hero.guid, soldier.team, False)
                in_world(run.match, ally)
                run.players.append(ally)
                self.assertTrue(run.match.switch_hero(ally, soldier.hero))
                ally.mover.position = [1.2, 0, ally_z]
                ally.mover.body = None
                pool = self.vitals(run, ally)
                pool.damage(20)
                before = pool.total
                run.step({soldier: (0, 0, 16)})
                for _ in range(60):
                    run.step({})
                    if fight.rockets:
                        break
                canister, = fight.rockets
                field = canister.shot
                self.assertEqual(field.spawn, projectiles.FIELD_ENTITY)
                ray = first_canister_sweep(canister)
                contact = service.trace(soldier, *ray)
                self.assertIsNotNone(contact)
                self.assertIs(contact[1], shield)
                self.assertGreater(ally_z, ray[0][2] + contact[0] * ray[1][2])
                for _ in range(60):
                    run.step({})
                    if field.field_tick is not None and run.frame > field.field_tick:
                        break
                self.assertNotIn(canister, fight.rockets)
                self.assertIsNotNone(field.field_tick)
                self.assertAlmostEqual(canister.distance, contact[0], places=5)
                if kind == "particle":
                    self.assertEqual(service.pools[defender.body][1], 200)
                else:
                    self.assertEqual(shield.health, 700 if kind == "dome" else 600)
                self.assertEqual(self.vitals(run, defender).total, defender_health)
                self.assertIs(field.player, soldier)
                self.assertEqual(field.body, soldier.body)
                self.assertAlmostEqual(pool.total - before, 35 * combat.TICK)
                self.assertFalse(soldier.client.messages(combat.HIT))

    def test_orisa_thrown_barrier_blocks_actual_rifle_helix_and_visor(self):
        for button, visor, damage in ((1, False, 20), (2, False, 120), (1, True, 20)):
            with self.subTest(button=button, visor=visor):
                run, soldier, orisa, shield = self.deploy_orisa_barrier()
                fight = run.match.combat
                if visor:
                    script = soldier.body_script
                    script.set_ult_charge(script.component.entity_var(338).value())
                    run.step({soldier: (0, 0, 0x20)})
                    for _ in range(80):
                        run.step({})
                yaw, pitch = aim(fight._eye(soldier), (0.0, 0.8, 5.0))
                before = self.vitals(run, orisa).total
                run.step({soldier: (yaw, pitch, button)})
                for _ in range(24):
                    run.step({})
                self.assertEqual(shield.health, 600 - damage)
                self.assertEqual(self.vitals(run, orisa).total, before)
                self.assertFalse(soldier.client.messages(combat.HIT))
                self.assertEqual(soldier.body_script.component.entity_var(342).value(), 0)

    def test_nearer_mesh_takes_rifle_and_helix_while_the_rear_mesh_stays_full(self):
        for rein_front in (False, True):
            with self.subTest(rein_front=rein_front):
                run, soldier, orisa, shield = self.deploy_orisa_barrier()
                if run.match.combat.barriers.geometry is None:
                    self.skipTest("original Reinhardt collision descriptor is required")
                rein = run.match.add_player(3, "Reinhardt", reinhardt_barrier.HERO, orisa.team, False)
                in_world(run.match, rein)
                run.players.append(rein)
                run.match.switch_hero(rein, heroes()[reinhardt_barrier.HERO])
                rein.mover.position = [0, 0, shield.position[2] + (0 if rein_front else 2)]
                rein.mover.body = None
                for _ in range(25):
                    run.step({})
                for _ in range(8):
                    run.step({rein: (-32768, 0, 2)})
                fight = run.match.combat
                barrier_field = fight.barriers.states[rein.body]
                self.assertTrue(barrier_field.active)
                aim_angles = aim(fight._eye(soldier), (0, .8, 6))
                run.step({soldier: (*aim_angles, 1), rein: (-32768, 0, 2)})
                self.assertEqual(barrier_field.current, 1580 if rein_front else 1600)
                self.assertEqual(shield.health, 600 if rein_front else 580)
                for _ in range(12):
                    run.step({rein: (-32768, 0, 2)})
                run.step({soldier: (*aim_angles, 2), rein: (-32768, 0, 2)})
                self.assertEqual(len(fight.rockets), 1)
                for _ in range(24):
                    run.step({rein: (-32768, 0, 2)})
                self.assertFalse(fight.rockets)
                self.assertEqual(barrier_field.current, 1460 if rein_front else 1600)
                self.assertEqual(shield.health, 600 if rein_front else 460)
                self.assertEqual(self.vitals(run, rein).total, 500)
                self.assertEqual(self.vitals(run, orisa).total, 450)
                self.assertFalse(soldier.client.messages(combat.HIT))

    def test_orisa_barrier_breaking_helix_keeps_same_blast_off_body(self):
        run, soldier, orisa, shield = self.deploy_orisa_barrier()
        shield.health = 100  # Isolate the final impact on the deployed actor.
        before = self.vitals(run, orisa).total
        yaw, pitch = aim(run.match.combat._eye(soldier), (0.0, 0.8, 5.0))
        run.step({soldier: (yaw, pitch, 2)})
        for _ in range(35):
            run.step({})
        self.assertEqual(shield.health, 0)
        self.assertFalse(shield.active())
        self.assertEqual(self.vitals(run, orisa).total, before)
        self.assertFalse(soldier.client.messages(combat.HIT))
        for _ in range(35):
            run.step({soldier: (yaw, pitch, 1)})
            if self.vitals(run, orisa).total < before:
                break
        self.assertEqual(self.vitals(run, orisa).total, before - 15)

    def test_orisa_deployed_barrier_expires_without_owner_input(self):
        run, soldier, orisa, shield = self.deploy_orisa_barrier()
        fight = run.match.combat
        run.frame += 1800
        run.match.update(run.frame * combat.TICK, run.frame)
        self.assertTrue(shield.retired)
        self.assertFalse(fight.orisa_barriers.actors)
        before = self.vitals(run, orisa).total
        yaw, pitch = aim(fight._eye(soldier), (0.0, 0.8, 5.0))
        run.step({soldier: (yaw, pitch, 1)})
        self.assertEqual(self.vitals(run, orisa).total, before - 15)

    def test_in_flight_helix_orders_contact_against_actual_orisa_landing(self):
        cases = (
            (3, (600, 340)),  # Body impact before the barrier lands.
            (4, (520, 340)),  # Rocket passes the future mesh before landing; splash hits both.
            (5, (480, 450)),  # Mesh intercepts before the rocket reaches the body.
        )
        for launch_offset, expected in cases:
            for jump in (False, True):
                with self.subTest(launch_offset=launch_offset, jump=jump):
                    run, soldier, orisa = self.player_arena(defenses.ORISA, z=5.0)
                    fight = run.match.combat
                    service = fight.orisa_barriers
                    if service.geometry is None:
                        self.skipTest("original Orisa collision descriptor is required")
                    yaw, pitch = aim(fight._eye(soldier), (0, .8, 5))
                    for offset in range(16):
                        run.step({
                            orisa: (-32768, 8192, 16 if offset == 0 else 0),
                            soldier: (yaw, pitch, 2 if offset == launch_offset else 0),
                        })
                        if service.flights and any(rocket.last_tick is not None for rocket in fight.rockets):
                            break
                    self.assertTrue(service.flights)
                    self.assertFalse(service.actors)
                    rocket, = fight.rockets
                    self.assertEqual(rocket.last_tick, run.frame)
                    self.assertEqual(self.vitals(run, orisa).total, 450)
                    end = run.frame + 300
                    for tick in ((end,) if jump else range(run.frame + 1, end + 1)):
                        run.match.update(tick * combat.TICK, tick)
                    shield, = service.actors.values()
                    self.assertFalse(fight.rockets)
                    self.assertEqual((shield.health, self.vitals(run, orisa).total), expected)

    def test_orisa_releases_admitted_throw_once_without_new_owner_input(self):
        expected = None
        for mode in ("posed", "idle", "jump"):
            with self.subTest(mode=mode):
                run, soldier, orisa = self.player_arena(defenses.ORISA, z=5)
                fight = run.match.combat
                service = fight.orisa_barriers
                if service.geometry is None:
                    self.skipTest("original Orisa collision descriptor is required")
                with patch.object(service, "land", wraps=service.land) as land:
                    run.step({orisa: (-32768, 8192, 16)})
                    activation = run.frame
                    self.assertFalse(service.flights)
                    self.assertTrue(service.pending(orisa))
                    end = run.frame + 300
                    if mode == "posed":
                        for _ in range(300):
                            run.step({orisa: (-32768, 8192, 0)})
                    else:
                        for tick in ((end,) if mode == "jump" else range(run.frame + 1, end + 1)):
                            run.match.update(tick * combat.TICK, tick)
                        run.frame = end
                        last_pose = orisa.mover.last_command
                        orisa.take_commands([Command(activation + 2, buttons=16)])
                        self.assertIs(orisa.mover.last_command, last_pose)
                land.assert_called_once()
                shield, = service.actors.values()
                self.assertEqual(shield.health, 600)
                self.assertFalse(service.flights)
                result = shield.position, land.call_args.args[2]
                if expected is None:
                    expected = result
                self.assertEqual(result, expected)
                yaw, pitch = aim(fight._eye(soldier), (0, .8, 5))
                run.step({soldier: (yaw, pitch, 1)})
                self.assertEqual(shield.health, 580)
                self.assertEqual(self.vitals(run, orisa).total, 450)

    def test_unposed_orisa_release_keeps_existing_helix_contact_order(self):
        for offset, expected in ((3, (600, 340)), (9, (480, 450))):
            with self.subTest(offset=offset):
                run, soldier, orisa = self.player_arena(defenses.ORISA, z=5)
                fight = run.match.combat
                service = fight.orisa_barriers
                if service.geometry is None:
                    self.skipTest("original Orisa collision descriptor is required")
                run.step({orisa: (-32768, 8192, 16)})
                launch = run.frame + offset
                run.match.update(launch * combat.TICK, launch)
                yaw, pitch = aim(fight._eye(soldier), (0, .8, 5))
                soldier.take_commands([Command(launch, yaw=yaw, pitch=pitch, buttons=2)])
                run.match.update((launch + 1) * combat.TICK, launch + 1)
                rocket, = fight.rockets
                self.assertEqual(rocket.last_tick, launch + 1)
                self.assertFalse(service.actors)
                end = launch + 300
                run.match.update(end * combat.TICK, end)
                shield, = service.actors.values()
                self.assertEqual((shield.health, self.vitals(run, orisa).total), expected)

    def test_switch_during_orisa_windup_drops_old_unposed_throw(self):
        run, soldier, orisa = self.player_arena(defenses.ORISA, z=5)
        service = run.match.combat.orisa_barriers
        if service.geometry is None:
            self.skipTest("original Orisa collision descriptor is required")
        run.step({orisa: (-32768, 8192, 16)})
        self.assertTrue(service.pending(orisa))
        self.assertTrue(run.match.switch_hero(orisa, soldier.hero))
        run.frame += 60
        run.match.update(run.frame * combat.TICK, run.frame)
        self.assertFalse(service.flights)
        self.assertFalse(service.actors)

    def test_in_flight_helix_orders_contact_against_orisa_barrier_expiry(self):
        for launch_offset, expected in ((1238, (480, 450)), (1248, (600, 340))):
            for jump in (False, True):
                with self.subTest(launch_offset=launch_offset, jump=jump):
                    run, soldier, orisa, shield = self.deploy_orisa_barrier()
                    fight = run.match.combat
                    launch = shield.component.frame + launch_offset
                    run.match.update(launch * combat.TICK, launch)
                    self.assertTrue(shield.active())
                    yaw, pitch = aim(fight._eye(soldier), (0, .8, 5))
                    soldier.take_commands([Command(launch, yaw=yaw, pitch=pitch, buttons=2)])
                    run.match.update((launch + 1) * combat.TICK, launch + 1)
                    rocket, = fight.rockets
                    self.assertEqual(rocket.last_tick, launch + 1)
                    end = launch + 300
                    for tick in ((end,) if jump else range(launch + 2, end + 1)):
                        run.match.update(tick * combat.TICK, tick)
                    self.assertTrue(shield.retired)
                    self.assertEqual((shield.health, self.vitals(run, orisa).total), expected)

    def test_orisa_cooldown_refuses_early_tap_and_replaces_only_on_new_landing(self):
        run, soldier, orisa, old = self.deploy_orisa_barrier()
        fight = run.match.combat
        service = fight.orisa_barriers
        instance = next(item for item in orisa.body_script.component.instances.values()
                        if item.graph.index == 0x0BD7)
        rear = self.add_bot(run, orisa.team, (0, 0, 7))
        orisa.mover.position = [7, 0, 5]
        orisa.mover.body = None
        angles = aim(fight._eye(soldier), (0, .8, 7))
        run.step({orisa: (-32768, 8192, 16), soldier: (*angles, 1)})
        self.assertEqual(old.health, 580)
        for _ in range(35):
            run.step({})
        self.assertFalse(service.flights)
        self.assertIs(service.actors[orisa.body], old)
        self.assertGreater(instance.vars[61].value(), 0)
        run.frame += 650
        run.match.update(run.frame * combat.TICK, run.frame)
        self.assertEqual(instance.vars[61].value(), 0)
        self.assertFalse(service.flights)
        self.assertIs(service.actors[orisa.body], old)
        run.step({orisa: (-32768, 8192, 16)})
        for _ in range(40):
            if service.flights:
                break
            run.step({orisa: (-32768, 8192, 0)})
        self.assertTrue(service.flights)
        self.assertIs(service.actors[orisa.body], old)
        run.step({soldier: (*angles, 1), orisa: (-32768, 8192, 0)})
        self.assertEqual(old.health, 560)
        self.assertEqual(self.vitals(run, rear).total, 200)
        for _ in range(40):
            if service.actors[orisa.body] is not old:
                break
            run.step({orisa: (-32768, 8192, 0)})
        new = service.actors[orisa.body]
        self.assertTrue(old.retired)
        self.assertGreater(new.body, old.body)
        self.assertEqual(new.health, 600)
        self.assertGreater(new.position[0], old.position[0] + 6)
        for _ in range(12):
            run.step({})
        run.step({soldier: (*angles, 1)})
        self.assertEqual(self.vitals(run, rear).total, 180)
        self.assertEqual(new.health, 600)

    def test_orisa_body_replacement_cancels_flight_but_landed_barrier_keeps_cover(self):
        for landed in (False, True):
            with self.subTest(landed=landed):
                if landed:
                    run, soldier, orisa, shield = self.deploy_orisa_barrier()
                else:
                    run, soldier, orisa = self.player_arena(defenses.ORISA, z=5.0)
                    if run.match.combat.orisa_barriers.geometry is None:
                        self.skipTest("original Orisa collision descriptor is required")
                    run.step({orisa: (-32768, 8192, 16)})
                    for _ in range(40):
                        if run.match.combat.orisa_barriers.flights:
                            break
                        run.step({orisa: (-32768, 8192, 0)})
                fight = run.match.combat
                service = fight.orisa_barriers
                self.assertEqual(bool(service.flights), not landed)
                self.assertTrue(run.match.switch_hero(orisa, soldier.hero))
                orisa.mover.position = [0, 0, 5]
                for _ in range(40):
                    run.step({})
                self.assertFalse(service.flights)
                angles = aim(fight._eye(soldier), (0, .8, 5))
                run.step({soldier: (*angles, 1)})
                self.assertEqual(self.vitals(run, orisa).total, 200 if landed else 180)
                if landed:
                    self.assertEqual(shield.health, 580)
                else:
                    self.assertFalse(service.actors)

    def test_orisa_landed_barrier_keeps_cover_through_owner_death_and_respawn(self):
        run, soldier, orisa, shield = self.deploy_orisa_barrier()
        fight = run.match.combat
        source = orisa.body
        soldier.mover.position = [0, 0, 8]
        soldier.mover.body = None
        angles = aim(fight._eye(soldier), (0, 1.9, 5))
        for _ in range(250):
            run.step({soldier: (*angles, 1)})
            if fight.is_dead(orisa):
                break
        self.assertTrue(fight.is_dead(orisa))
        self.assertEqual(shield.health, 600)
        self.assertIs(fight.orisa_barriers.actors[source], shield)
        run.frame += 650
        run.match.update(run.frame * combat.TICK, run.frame)
        self.assertNotEqual(orisa.body, source)
        self.assertFalse(fight.is_dead(orisa))
        self.assertIs(fight.orisa_barriers.actors[source], shield)
        rear = self.add_bot(run, orisa.team, (0, 0, 7))
        soldier.mover.position = [0, 0, -3]
        soldier.mover.body = None
        run.step({})
        angles = aim(fight._eye(soldier), (0, .8, 7))
        run.step({soldier: (*angles, 1)})
        self.assertEqual(shield.health, 580)
        self.assertEqual(self.vitals(run, rear).total, 200)
        run.step({orisa: (-32768, 8192, 16)})
        for _ in range(60):
            if orisa.body in fight.orisa_barriers.actors:
                break
            run.step({orisa: (-32768, 8192, 0)})
        self.assertTrue(shield.retired)
        self.assertNotIn(source, fight.orisa_barriers.actors)
        self.assertEqual(fight.orisa_barriers.actors[orisa.body].health, 600)


if __name__ == "__main__":
    unittest.main()
