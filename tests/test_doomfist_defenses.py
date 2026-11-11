"""Actual Wraith input versus Doomfist; declared host defense policy only."""

import unittest

from test_doomfist_combat import DoomfistFixture
from test_doomfist_meteor import charge_by_neutral_commands
from test_game_combat import aim

from ow174.game import combat, defenses
from ow174.game.content import heroes
from ow174.game.script import nodes


class WraithInteractionTests(DoomfistFixture, unittest.TestCase):
    def arena(self, distance=3):
        self.prepare(distance)
        self.match.switch_hero(self.b, heroes()[defenses.REAPER])
        x, y, z = self.a.mover.position
        self.place_player(self.b, (x, y, z + distance))
        self.run.step({})

    def start(self):
        self.run.step({self.b: (0, 0, 8)})
        self.assertTrue(self.fight.protected(self.b.body))

    def own_shields(self):
        target = next(t for t in self.fight._targets() if t.owner is self.a)
        return self.fight.vitals_of(target).temporary_shields

    def test_actual_form_rejects_primary_melee_uppercut_punch_and_slam(self):
        for attack, distance, button, frames in (
            ('primary', 3, 1, 8), ('melee', 1.5, 0x800, 30),
            ('uppercut', 1.5, 8, 45), ('punch', 6, 2, 50), ('slam', 6, 16, 100),
        ):
            with self.subTest(attack=attack):
                self.arena(distance)
                self.start()
                charge = self.charge_checkpoint()
                position = tuple(self.b.mover.position)
                if attack == 'primary':
                    self.fire()
                else:
                    self.run.step({self.a: (0, 0, button)})
                self.settle(frames)
                self.assertTrue(self.fight.protected(self.b.body))
                self.assertEqual(self.vitals().total, 250)
                self.assertEqual(tuple(self.b.mover.position), position)
                self.assertFalse(self.fight.punches.controlled(self.b.body))
                self.assertEqual(self.own_shields(), 0)
                self.assertFalse(self.a.client.messages(combat.HIT))
                self.assert_damage_charge(charge, 0)

    def test_released_pellets_cannot_hit_a_body_that_enters_form_before_collision(self):
        self.arena()
        charge = self.charge_checkpoint()
        self.fire()
        self.assertEqual(len(self.fight.rockets), 11)
        self.start()
        self.settle(8)
        self.assertEqual(self.vitals().total, 250)
        self.assertFalse(self.a.client.messages(combat.HIT))
        self.assert_damage_charge(charge, 0)

    def test_actual_aerial_slam_impacts_phased_target_without_damage_or_passive(self):
        for in_form in (False, True):
            with self.subTest(in_form=in_form):
                self.arena(10)
                x, y, z = self.a.mover.position
                self.place_player(self.a, (x, y + 8, z))
                if in_form:
                    self.start()
                else:
                    self.run.step({})
                self.run.step({self.a: (0, 9000, 16)})
                cast = self.fight.slams.casts[self.a.body]
                self.assertEqual(cast.kind, 'aerial')
                self.settle(80)
                self.assertIsNotNone(cast.origin)
                self.assertIsNotNone(cast.wave_at)
                self.assertTrue(cast.finished)
                if in_form:
                    self.assertEqual(self.vitals().total, 250)
                    self.assertEqual(self.own_shields(), 0)
                    self.assertNotIn(self.b.body, cast.hit)
                else:
                    self.assertLess(self.vitals().total, 250)
                    self.assertGreater(self.own_shields(), 0)
                    self.assertIn(self.b.body, cast.hit)

    def test_authored_three_second_phase_ends_then_primary_hits_normally(self):
        self.arena()
        self.start()
        instance = defenses.wraith(self.b)
        phase = instance.state(0)
        start = phase.start
        for _ in range(240):
            self.settle(1)
            if not self.fight.protected(self.b.body):
                break
        self.assertFalse(self.fight.protected(self.b.body))
        self.assertEqual(phase.ended_at - start, 3000)
        charge = self.charge_checkpoint()
        self.fire()
        self.settle(8)
        self.assertEqual(self.vitals().total, 184)
        self.assertEqual(len(self.a.client.messages(combat.HIT)), 11)
        self.assert_damage_charge(charge, 66)

    def test_cooldown_rejects_old_pulse_and_fresh_press_after_expiry_reenters(self):
        self.arena()
        self.start()
        instance = defenses.wraith(self.b)
        ability = instance.state(10)
        activation = instance.state(0).counter
        self.settle(240)
        self.assertFalse(ability.flags & nodes.Ability.RUNNING)
        self.assertGreater(ability.remaining(), 0)
        self.run.step({self.b: (0, 0, 8)})
        self.settle(550)
        self.assertEqual(ability.remaining(), 0)
        self.assertEqual(instance.state(0).counter, activation)
        self.assertFalse(self.fight.protected(self.b.body))
        self.start()
        self.assertEqual(instance.state(0).counter, activation + 1)

    def test_actual_primary_and_shift_cancel_after_authored_minimum_without_firing(self):
        for button in (1, 8):
            with self.subTest(cancel_button=button):
                self.arena()
                self.start()
                weapon = next(i for i in self.b.body_script.component.instances.values()
                              if i.graph.index == 0x19)
                ammo = weapon.vars[53].value()
                volleys = {s.index: s.counter for s in weapon.states.values()
                           if s.node.cls == 'STUStatescriptStateWeaponVolley'}
                self.settle(30)
                yaw, pitch = aim(self.fight._eye(self.b), self.fight._eye(self.a))
                self.run.step({self.b: (yaw, pitch, button)})
                self.settle(70)
                self.assertFalse(self.fight.protected(self.b.body))
                self.assertEqual(weapon.vars[53].value(), ammo)
                self.assertEqual({s.index: s.counter for s in weapon.states.values()
                                  if s.node.cls == 'STUStatescriptStateWeaponVolley'}, volleys)
                self.assertFalse(self.b.client.messages(combat.HIT))
                own = next(t for t in self.fight._targets() if t.owner is self.a)
                self.assertEqual(self.fight.vitals_of(own).total, 250)

    def test_actual_meteor_impact_rejects_form_and_grants_no_passive(self):
        self.arena()
        charge_by_neutral_commands(self)
        self.run.step({self.a: (0, 0, 0x20)})
        self.settle(70)
        self.start()
        self.run.step({self.a: (0, 0, 1)})
        self.settle(100)
        self.assertTrue(self.fight.protected(self.b.body))
        self.assertEqual(self.vitals().total, 250)
        self.assertEqual(self.own_shields(), 0)
        self.assertFalse(self.a.client.messages(combat.HIT))
        self.assertLess(self.a.body_script.component.entity_var(342).value(), 1680)
        self.assertTrue(self.fight.meteors.casts[self.a.body].impacted)

    def test_body_replacement_during_form_does_not_protect_new_body(self):
        self.arena()
        self.start()
        old = self.b.body
        self.match.switch_hero(self.b, heroes()[defenses.REAPER])
        x, y, z = self.a.mover.position
        self.place_player(self.b, (x, y, z + 3))
        self.run.step({})
        self.assertNotEqual(self.b.body, old)
        self.assertFalse(self.fight.protected(old))
        self.assertFalse(self.fight.protected(self.b.body))
        self.fire()
        self.settle(8)
        self.assertEqual(self.vitals().total, 184)

    def test_far_pellet_hits_after_form_expires_instead_of_being_discarded_at_release(self):
        self.arena(250)
        charge = self.charge_checkpoint()
        self.fire()
        self.assertEqual(len(self.fight.rockets), 11)
        self.align_target_with_first_pellet()
        self.start()
        self.settle(190)
        self.assertFalse(self.fight.protected(self.b.body))
        self.assertEqual(self.vitals().total, 250)
        self.assert_damage_charge(charge, 0)
        self.settle(220)
        self.assertAlmostEqual(self.vitals().total, 248.2, places=6)
        self.assertEqual(len(self.a.client.messages(combat.HIT)), 1)
        self.assertFalse(self.fight.rockets)
        self.assert_damage_charge(charge, 1.8)

    def test_actual_flashbang_cannot_cancel_or_stun_active_form(self):
        for in_form in (False, True):
            with self.subTest(in_form=in_form):
                self.arena()
                self.match.switch_hero(self.a, heroes()[0x02E0000000000042])
                x, y, z = self.a.mover.position
                self.place_player(self.b, (x, y, z + 3))
                self.run.step({})
                if in_form:
                    self.start()
                yaw, pitch = aim(self.fight._eye(self.a), (x, y + 1, z + 3))
                self.run.step({self.a: (yaw, pitch, 16)})
                for _ in range(35):
                    self.settle(1)
                    if self.fight.is_stunned(self.b):
                        break
                self.assertEqual(self.vitals().total, 250 if in_form else 225)
                self.assertEqual(self.fight.is_stunned(self.b), not in_form)
                self.assertEqual(self.fight.protected(self.b.body), in_form)


if __name__ == '__main__':
    unittest.main()
