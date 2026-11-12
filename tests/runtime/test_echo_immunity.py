"""Real Wraith inputs and authored phase lifetime; native startup/filters remain reconstructed."""
import unittest
from dataclasses import replace

import test_echo_combat as echo_tests
import test_mccree_flashbang as flash
from test_game_combat import aim, in_world

from ow174.game import combat, echo_movement, immunity
from ow174.game.commands import JUMP
from ow174.game.content import SOLDIER, heroes
from ow174.game.mover import Mover, mover_data

REAPER = 0x02E0000000000002


class EchoWraithTests(unittest.TestCase):
    def session(self, distance=5):
        run, echo, reaper, _, angles = echo_tests.EchoPrimaryTests().session(distance=distance)
        run.match.switch_hero(reaper, heroes()[REAPER], preserve_pose=True)
        for _ in range(25):
            run.step({})
        echo.body_script.ult_seconds = 0
        echo.body_script.set_ult_charge(0)
        fight = run.match.combat
        target = next(t for t in fight._targets() if t.owner is reaper)
        return run, echo, reaper, target, angles

    @staticmethod
    def wraith(reaper):
        return next(i for i in reaper.body_script.component.instances.values() if i.graph.index == 0x20)

    def start(self, run, reaper):
        run.step({reaper: (0, 0, 8)})
        for _ in range(5):
            run.step({})
        self.assertTrue(self.wraith(reaper).states[10].flags & 1)
        self.assertTrue(immunity.wraith_active(reaper))

    def test_real_wraith_ignores_echo_primary_without_hit_credit_or_charge(self):
        run, echo, reaper, target, angles = self.session()
        self.start(run, reaper)
        run.step({echo: (*angles, 1)})
        for _ in range(25):
            run.step({echo: (*angles, 0)})
        self.assertEqual(run.match.combat.vitals_of(target).total, 250)
        self.assertEqual(echo.client.messages(combat.HIT), [])
        self.assertEqual(echo.body_script.component.entity_var(342).value(), 0)

    def test_real_wraith_ignores_sticky_beam_and_melee_without_charge(self):
        for button, frames, distance in ((2, 140, 5), (16, 20, 5), (0x800, 30, 2)):
            with self.subTest(button=button):
                run, echo, reaper, target, angles = self.session(distance)
                self.start(run, reaper)
                run.step({echo: (*angles, button)})
                for _ in range(frames):
                    run.step({echo: (*angles, 0)})
                self.assertEqual(run.match.combat.vitals_of(target).total, 250)
                self.assertEqual(echo.client.messages(combat.HIT), [])
                self.assertEqual(echo.body_script.component.entity_var(342).value(), 0)

    def test_projectiles_pass_through_wraith_and_hit_the_enemy_behind(self):
        self.check_tri_shot_passes_through_wraith(0)

    def test_airborne_tri_shot_passes_through_wraith(self):
        for mode in (8, JUMP):
            with self.subTest(mode=mode):
                self.check_tri_shot_passes_through_wraith(mode)

    def check_tri_shot_passes_through_wraith(self, mode):
        run, echo, reaper, target, _ = self.session()
        behind = run.match.add_player(3, 'Behind', SOLDIER, reaper.team, False)
        in_world(run.match, behind)
        run.match.switch_hero(behind, heroes()[SOLDIER])
        run.players.append(behind)
        x, y, z = echo.mover.position
        behind.mover = Mover((x, y, z+8), world=run.match.collision_world,
                             data=mover_data(behind.hero.body))
        for _ in range(15):
            run.step({})
        self.start(run, reaper)
        held = JUMP if mode == JUMP else 0
        if mode == 8:
            for _ in range(20):
                run.step({echo: (0, 0, 8)})
            self.assertIsNotNone(echo_movement.flight(echo))
        elif mode == JUMP:
            echo.mover = Mover((x, y + 1.5, z), world=run.match.collision_world,
                               data=mover_data(echo.hero.body))
            run.step({echo: (0, 0, JUMP)})
            self.assertIsNotNone(echo_movement.glide_properties(echo, JUMP))
        angles = aim(run.match.combat._eye(echo), (x, y+1, z+8))
        run.step({echo: (*angles, held | 1)})
        fight = run.match.combat
        a, b = target.segment()
        rays = [r for r in fight.rockets if r.shooter is echo]
        self.assertEqual(len(rays), 3)
        self.assertTrue(all(combat.ray_capsule(r.position, r.direction, a, b, target.radius) is not None
                            for r in rays))
        for _ in range(25):
            run.step({echo: (*angles, held)})
        self.assertTrue(immunity.wraith_active(reaper))
        other = next(t for t in fight._targets() if t.owner is behind)
        self.assertEqual(fight.vitals_of(target).total, 250)
        self.assertEqual(fight.vitals_of(other).total, 149)
        self.assertEqual(echo.body_script.component.entity_var(342).value(), 51)
        self.assertEqual({hit['+0x78']['+0x0']['+0x0'] for hit in echo.client.messages(combat.HIT)},
                         {behind.body})

    def test_wraith_main_exit_cooldown_rejection_and_reuse_follow_authored_graph(self):
        run, echo, reaper, target, angles = self.session()
        self.start(run, reaper)
        instance = self.wraith(reaper)
        started = instance.states[3].start
        for _ in range(240):
            if not immunity.wraith_active(reaper):
                break
            run.step({})
        self.assertFalse(immunity.wraith_active(reaper))
        self.assertAlmostEqual(reaper.body_script.component.now - started, 3000, delta=16)
        run.step({echo: (*angles, 1)})
        for _ in range(30):
            run.step({echo: (*angles, 0)})
        self.assertEqual(run.match.combat.vitals_of(target).total, 199)
        self.assertFalse(instance.states[10].flags & 1)
        self.assertGreater(instance.vars[61].value(), 7)
        run.step({reaper: (0, 0, 8)})
        for _ in range(5):
            run.step({})
        self.assertFalse(immunity.wraith_active(reaper))
        for _ in range(600):
            if instance.vars[61].value() <= 0:
                break
            run.step({})
        self.assertEqual(instance.vars[61].value(), 0)
        self.assertFalse(immunity.wraith_active(reaper))  # rejected press must not stay buffered
        self.start(run, reaper)
        run.step({echo: (*angles, 1)})
        for _ in range(25):
            run.step({echo: (*angles, 0)})
        self.assertEqual(run.match.combat.vitals_of(target).total, 199)

    def test_early_cancel_is_rejected_then_real_shift_cancel_restores_damage(self):
        run, echo, reaper, target, angles = self.session()
        self.start(run, reaper)
        run.step({reaper: (0, 0, 8)})  # before authored0.4s cancellation input opens
        for _ in range(22):
            run.step({})
        self.assertTrue(immunity.wraith_active(reaper))
        run.step({reaper: (0, 0, 8)})
        for _ in range(3):
            run.step({})
        self.assertFalse(immunity.wraith_active(reaper))
        run.step({echo: (*angles, 1)})
        for _ in range(30):
            run.step({echo: (*angles, 0)})
        self.assertEqual(run.match.combat.vitals_of(target).total, 199)
        self.assertGreater(self.wraith(reaper).vars[61].value(), 7)

    def test_wraith_cannot_fire_or_spend_ammo_before_cancel_and_recovers_after_exit(self):
        run, echo, reaper, _, _ = self.session()
        fight = run.match.combat
        target = next(t for t in fight._targets() if t.owner is echo)
        weapon = next(i for i in reaper.body_script.component.instances.values() if i.graph.index == 0x19)
        ammo = weapon.vars[53].value()
        angles = aim(fight._eye(reaper), (echo.mover.position[0], echo.mover.position[1]+1,
                                         echo.mover.position[2]))
        run.step({reaper: (*angles, 1)})
        for _ in range(45):
            run.step({})
        self.assertEqual(weapon.vars[53].value(), ammo-1)
        self.start(run, reaper)
        self.assertEqual(weapon.vars[53].value(), ammo)  # authored7D message refills the actual magazine
        health = fight.vitals_of(target).total
        for _ in range(8):
            run.step({reaper: (*angles, 1)})
        self.assertTrue(immunity.wraith_active(reaper))
        self.assertEqual(fight.vitals_of(target).total, health)
        self.assertEqual(weapon.vars[53].value(), ammo)
        for _ in range(20):
            run.step({})
        for _ in range(50):
            run.step({reaper: (*angles, 1)})
        self.assertFalse(immunity.wraith_active(reaper))
        self.assertLess(fight.vitals_of(target).total, health)
        self.assertLess(weapon.vars[53].value(), ammo)

    def test_copied_reaper_wraith_rejects_real_flashbang_then_takes_it_after_expiry(self):
        run, echo, enemy, _, angles = self.session()
        echo.body_script.set_ult_charge(echo.body_script.component.entity_var(338).value())
        run.step({echo: (*angles, 32)})
        self.assertTrue(run.match.combat.echo_duplicates.active(echo))
        self.assertEqual(echo.hero.guid, REAPER)
        run.match.switch_hero(enemy, heroes()[0x02E0000000000042], preserve_pose=True)
        for _ in range(25):
            run.step({})
        self.start(run, echo)
        fixture = flash.FlashbangTests()
        fixture.step(run, enemy, echo, cast=16)
        for _ in range(35):
            fixture.step(run, enemy, echo)
        fight = run.match.combat
        target = next(t for t in fight._targets() if t.owner is echo)
        self.assertEqual(fight.vitals_of(target).total, 250)
        self.assertFalse(fight.is_stunned(echo))
        self.assertTrue(immunity.wraith_active(echo))
        for _ in range(640):
            fixture.step(run, enemy, echo)
        self.assertFalse(immunity.wraith_active(echo))
        self.assertTrue(fight.echo_duplicates.active(echo))
        fixture.cast(run, enemy, echo)
        self.assertEqual(fight.vitals_of(target).total, 225)
        self.assertTrue(fight.is_stunned(echo))

    def test_real_allied_biotic_field_heals_wraith_but_not_the_enemy_echo(self):
        run, echo, reaper, target, _ = self.session()
        run.match.game_map = replace(run.match.game_map, free_for_all=False)
        healer = run.match.add_player(3, 'Allied healer', SOLDIER, reaper.team, False)
        in_world(run.match, healer)
        run.match.switch_hero(healer, heroes()[SOLDIER])
        run.players.append(healer)
        x, y, z = reaper.mover.position
        healer.mover = Mover((x+2, y, z), world=run.match.collision_world,
                             data=mover_data(healer.hero.body))
        echo.mover = Mover((x, y, z-3), world=run.match.collision_world,
                           data=mover_data(echo.hero.body))
        fight = run.match.combat
        own = next(t for t in fight._targets() if t.owner is echo)
        # Injured fixture pools; subsequent healing must come from the actual field volley.
        fight.vitals_of(target).damage(100)
        fight._sync_body_health(reaper, fight.vitals_of(target))
        fight.vitals_of(own).damage(50)
        fight._sync_body_health(echo, fight.vitals_of(own))
        self.start(run, reaper)
        angles = aim(fight._eye(echo), (x, y+1, z))
        run.step({healer: (0, 16000, 16)})
        for _ in range(140):
            run.step({echo: (*angles, 1)})
        self.assertTrue(immunity.wraith_active(reaper))
        self.assertGreater(fight.vitals_of(target).total, 150)
        self.assertEqual(fight.vitals_of(own).total, 150)
        self.assertEqual(echo.body_script.component.entity_var(342).value(), 0)
        healer_target = next(t for t in fight._targets() if t.owner is healer)
        self.assertEqual(fight.vitals_of(healer_target).total, 200)
        self.assertEqual(echo.client.messages(combat.HIT), [])

    def check_copied_wraith_blocks_ongoing_attack(self, button):
        run, echo, enemy, _, angles = self.session()
        echo.body_script.set_ult_charge(echo.body_script.component.entity_var(338).value())
        run.step({echo: (*angles, 32)})
        fight = run.match.combat
        self.assertTrue(fight.echo_duplicates.active(echo))
        self.assertEqual(echo.hero.guid, REAPER)
        run.match.switch_hero(enemy, heroes()[echo_tests.ECHO], preserve_pose=True)
        for _ in range(25):
            run.step({})

        x, y, z = echo.mover.position
        incoming = aim(fight._eye(enemy), (x, y + 1, z))
        copy = next(t for t in fight._targets() if t.owner is echo)
        vitals = fight.vitals_of(copy)
        run.step({enemy: (*incoming, button)})
        for _ in range(35 if button == 2 else 20):
            run.step({enemy: (*incoming, 0)})
        self.assertLess(vitals.total, 250)
        if button == 2:
            self.assertEqual(vitals.total, 220)
            self.assertEqual(len(fight.rockets), 6)
            self.assertTrue(all(rocket.attached_entity == copy.entity for rocket in fight.rockets))
        else:
            beam = next(i for i in enemy.body_script.component.instances.values()
                        if i.graph.index == 0x1FBD).states[17]
            self.assertTrue(beam.active)

        run.step({echo: (0, 0, 8), enemy: (*incoming, 0)})
        self.assertTrue(immunity.wraith_active(echo))
        protected = vitals.total
        for _ in range(90):
            run.step({enemy: (*incoming, 0)})
            self.assertTrue(immunity.wraith_active(echo))
            self.assertEqual(vitals.total, protected)
        self.assertFalse(fight.rockets)
        for _ in range(130):
            if not immunity.wraith_active(echo):
                break
            run.step({})
        self.assertFalse(immunity.wraith_active(echo))
        self.assertTrue(fight.echo_duplicates.active(echo))
        before = vitals.total
        run.step({enemy: (*incoming, 1)})
        for _ in range(25):
            run.step({})
        self.assertAlmostEqual(before - vitals.total, 51)

    def test_copied_wraith_blocks_armed_sticky_fuses_and_active_beam(self):
        for button in (2, 16):
            with self.subTest(button=button):
                self.check_copied_wraith_blocks_ongoing_attack(button)

    def test_copy_expiry_during_wraith_does_not_protect_the_restored_echo_body(self):
        run, echo, enemy, _, angles = self.session()
        echo.body_script.set_ult_charge(echo.body_script.component.entity_var(338).value())
        run.step({echo: (*angles, 32)})
        fight = run.match.combat
        self.assertTrue(fight.echo_duplicates.active(echo))
        copied_body = echo.body
        run.match.switch_hero(enemy, heroes()[echo_tests.ECHO], preserve_pose=True)
        for _ in range(825):
            run.step({})
        self.start(run, echo)  # authored3s Wraith extends beyond the remaining copy duration
        for _ in range(200):
            if not fight.echo_duplicates.active(echo):
                break
            run.step({})
        self.assertFalse(fight.echo_duplicates.active(echo))
        self.assertEqual(echo.hero.guid, echo_tests.ECHO)
        self.assertNotEqual(echo.body, copied_body)
        self.assertFalse(immunity.wraith_active(echo))
        target = next(t for t in fight._targets() if t.owner is echo)
        self.assertEqual(fight.vitals_of(target).total, 200)
        incoming = aim(fight._eye(enemy), (echo.mover.position[0], echo.mover.position[1]+1,
                                          echo.mover.position[2]))
        run.step({enemy: (*incoming, 1)})
        for _ in range(25):
            run.step({})
        self.assertEqual(fight.vitals_of(target).total, 149)
