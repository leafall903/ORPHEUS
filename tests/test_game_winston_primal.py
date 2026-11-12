"""Actual Primal input/timers -> health, damage, cooldowns and collision motion."""

import unittest
from itertools import pairwise

from test_game_combat import Run, aim
from test_delivered_winston import bubble_for
from test_game_mover import box, wall

from ow174.game import combat, winston
from ow174.game.collision import World
from ow174.game.commands import Command
from ow174.game.content import heroes
from ow174.game.script.driver import ULT_CHARGE, ULT_COST


class PrimalTests(unittest.TestCase):
    def setUp(self):
        self.run = Run(names=('Winston', 'Enemy', 'Ally'))
        self.match = self.run.match
        self.match.bots.clear()
        self.owner, self.enemy, self.ally = self.run.players
        self.match.switch_hero(self.owner, heroes()[winston.HERO])
        self.owner.team = self.ally.team = 0
        self.enemy.team = 1
        self.move(self.owner, (0, 1, 0))
        self.move(self.enemy, (0, 1, 1.5))
        self.move(self.ally, (.2, 1, 1.5))
        self.fight = self.match.combat
        self.fight.now = self.run.frame * combat.TICK
        self.owner.body_script.ult_seconds = 0

    @staticmethod
    def move(player, point):
        player.mover.position[:] = point
        player.mover.body = None
        player.mover.velocity = (0, 0, 0)

    def health(self, player):
        return self.fight.vitals_of(next(t for t in self.fight._targets()
                                           if t.owner is player)).total

    def rage(self):
        script = self.owner.body_script
        script.set_ult_charge(script.component.entity_var(ULT_COST).value())
        self.run.step({self.owner: (0, 0, 32)})
        self.assertTrue(winston.primal_active(script.component))

    def step(self, button=1, *, yaw=0, pitch=0):
        self.run.step({self.owner: (yaw, pitch, button)})

    def first_hit(self):
        for _ in range(80):
            self.step()
            if self.health(self.enemy) < 200:
                return
        self.fail('actual Primal primary failed to damage nearby enemy')

    def test_punch_has_windup_then_forty_damage_no_ammo_charge_or_ally_hit(self):
        self.rage()
        for _ in range(25):
            self.step()
        self.assertEqual(self.health(self.enemy), 200)
        self.first_hit()
        self.assertEqual(self.health(self.enemy), 160)
        self.assertEqual(self.health(self.ally), 200)
        script = self.owner.body_script
        weapon = next(i for i in script.component.instances.values() if i.graph.index == 0xFE)
        self.assertEqual(weapon.vars[53].value(), 100)
        self.assertEqual(script.component.entity_var(ULT_CHARGE).value(), 0)

    def test_primal_expiry_discards_pending_punch_and_first_tesla_tick_charges(self):
        for start, held in ((590, False), (617, False), (590, True), (617, True)):
            with self.subTest(start=start, held=held):
                self.setUp()
                self.rage()
                script = self.owner.body_script
                charge = script.component.entity_var(ULT_CHARGE)
                expires = self.run.frame + 625
                self.run.frame += start
                self.match.update(self.run.frame * combat.TICK, self.run.frame, frames=False)
                while self.run.frame < expires - 1:
                    self.step(1)
                self.assertTrue(winston.primal_active(script.component))
                self.assertEqual(charge.value(), 0)
                health = 160 if start == 590 else 200
                self.assertEqual(self.health(self.enemy), health)
                weapon = next(i for i in script.component.instances.values() if i.graph.index == 0xFE)
                self.assertEqual(weapon.vars[53].value(), 100)
                if start == 617:
                    self.assertTrue(any(weapon.states.get(i) is not None and weapon.states[i].active
                                        for i in (20, 21)))
                self.step(int(held))
                self.assertFalse(winston.primal_active(script.component))
                self.assertEqual(self.health(self.enemy), health)
                self.assertEqual(charge.value(), 0)
                self.assertEqual(weapon.vars[53].value(), 100)
                self.move(self.enemy, (0, 1, 1.5))
                for _ in range(50):
                    self.step(1)
                    if self.health(self.enemy) < health:
                        break
                    self.assertEqual(charge.value(), 0)
                self.assertAlmostEqual(self.health(self.enemy), health - .96)
                self.assertAlmostEqual(charge.value(), .96, places=5)
                ammo = weapon.vars[53].value()
                self.owner.take_commands([Command(self.run.frame, buttons=1),
                                          Command(self.run.frame - 1, buttons=1)])
                self.assertAlmostEqual(self.health(self.enemy), health - .96)
                self.assertAlmostEqual(charge.value(), .96, places=5)
                self.assertEqual(weapon.vars[53].value(), ammo)
                for _ in range(12):
                    self.step(1)
                self.assertLess(weapon.vars[53].value(), 100)

    def test_punch_changes_actual_enemy_position_and_lands(self):
        self.rage()
        self.first_hit()
        for _ in range(20):
            self.step(0)
        self.assertGreater(self.enemy.mover.position[2], 5)
        self.assertGreater(self.enemy.mover.position[1], 1)
        for _ in range(150):
            self.step(0)
        self.assertAlmostEqual(self.enemy.mover.position[1], 1)
        self.assertEqual(self.enemy.mover.velocity[1], 0)

    def test_active_damage_state_cannot_repeat_hit_from_duplicate_or_old_packet(self):
        self.rage()
        self.first_hit()
        before = self.health(self.enemy)
        for frame in (self.run.frame, self.run.frame - 1):
            self.fight.command(self.owner, Command(frame, buttons=1))
        self.assertEqual(self.health(self.enemy), before)

    def test_alternating_punches_can_damage_on_next_real_attack_cycle(self):
        self.rage()
        self.first_hit()
        first = self.run.frame
        for _ in range(25):
            self.step()
        self.assertEqual(self.health(self.enemy), 160)
        self.move(self.enemy, (0, 1, 1.5))
        for _ in range(50):
            self.step()
            if self.health(self.enemy) < 160:
                break
        self.assertEqual(self.health(self.enemy), 120)
        self.assertGreater(self.run.frame - first, 25)

    def test_held_punches_keep_the_authored_swing_period(self):
        self.rage()
        self.health(self.enemy)
        self.fight.vitals[self.enemy.body] = combat.Vitals(self.enemy.body, 5000)
        hits = []
        before = self.health(self.enemy)
        for _ in range(240):
            # A controlled stationary target isolates swing timing from knockback.
            self.move(self.enemy, (0, 1, 1.5))
            self.step()
            after = self.health(self.enemy)
            if after != before:
                self.assertEqual(before - after, 40)
                hits.append(self.run.frame)
            before = after
        self.assertGreaterEqual(len(hits), 4)
        self.assertTrue(all(40 <= b - a <= 42 for a, b in pairwise(hits)))

    def test_range_rear_and_static_occlusion_reject_punch(self):
        self.rage()
        self.move(self.enemy, (0, 1, 10))
        self.ally.team = 1
        self.move(self.ally, (0, 1, -1.5))
        for _ in range(100):
            self.step()
        self.assertEqual(self.health(self.enemy), 200)
        self.assertEqual(self.health(self.ally), 200)
        self.move(self.enemy, (0, 1, 1.5))
        floor = box(-30, 30, -1, 1, -30, 30)
        self.match.collision_world = World(floor + wall(-8, .75, 8, .75))
        for _ in range(80):
            self.step()
        self.assertEqual(self.health(self.enemy), 200)
        self.match.collision_world = World(floor)
        self.first_hit()
        self.assertEqual(self.health(self.enemy), 160)

    def test_pitched_and_rotated_punch_follows_the_current_aim(self):
        cases = (
            ((0, 3.4, 1.06), 0, -8192, 160),
            ((0, 3.4, 1.06), 0, 0, 200),
            ((1.06, 3.4, 0), 16384, -8192, 160),
            ((1.06, 3.4, 0), -16384, -8192, 200),
        )
        for target, yaw, pitch, expected in cases:
            with self.subTest(target=target, yaw=yaw, pitch=pitch):
                self.setUp()
                self.rage()
                for _ in range(75):
                    # Hold the elevated test pose against gravity until the swing.
                    self.move(self.enemy, target)
                    self.step(yaw=yaw, pitch=pitch)
                    if self.fight.primal_punches.seen.get(self.owner.body):
                        break
                self.assertTrue(self.fight.primal_punches.seen.get(self.owner.body))
                self.assertEqual(self.health(self.enemy), expected)

    def test_release_stops_new_swings_after_current_attack_finishes(self):
        self.rage()
        self.first_hit()
        for _ in range(50):
            self.step(0)
        self.move(self.enemy, (0, 1, 1.5))
        for _ in range(80):
            self.step(0)
        self.assertEqual(self.health(self.enemy), 160)

    def test_secondary_in_primal_does_not_start_punch_or_spend_ammo(self):
        script = self.owner.body_script
        charge = script.component.entity_var(ULT_CHARGE)
        weapon = next(i for i in script.component.instances.values() if i.graph.index == 0xFE)
        ammo = weapon.vars[53]

        def hold_secondary(frames):
            for _ in range(frames):
                self.step(2)
            self.assertEqual(self.health(self.enemy), 200)
            self.assertEqual(self.health(self.ally), 200)
            self.assertEqual(ammo.value(), 100)
            self.assertEqual(charge.value(), 0)

        hold_secondary(15)
        script.set_ult_charge(script.component.entity_var(ULT_COST).value())
        self.step(2 | 32)
        self.assertTrue(winston.primal_active(script.component))
        hold_secondary(624)
        self.assertTrue(winston.primal_active(script.component))
        hold_secondary(50)
        self.assertFalse(winston.primal_active(script.component))

        self.step(0)
        self.step(1)
        self.assertAlmostEqual(self.health(self.enemy), 199.04)
        self.assertAlmostEqual(charge.value(), .96, places=5)
        self.assertLess(ammo.value(), 100)

    def test_primal_replaces_in_progress_manual_reload_and_restores_full_tesla(self):
        for primal in (False, True):
            with self.subTest(primal=primal):
                self.setUp()
                script = self.owner.body_script
                weapon = next(i for i in script.component.instances.values() if i.graph.index == 0xFE)
                ammo = weapon.vars[53]
                for _ in range(40):
                    self.step()
                spent = ammo.value()
                self.assertGreater(spent, 0)
                self.assertLess(spent, 100)
                self.step(0)
                before = self.health(self.enemy)
                self.step(0x400)
                reload_wait = weapon.state(5)
                reload_gate = weapon.vars[51]
                self.assertTrue(reload_wait.active)
                self.assertTrue(reload_gate.value())
                for _ in range(10):
                    self.step(0)
                self.assertEqual(ammo.value(), spent)
                if primal:
                    self.rage()
                    self.assertFalse(reload_wait.active)
                    self.assertEqual(ammo.value(), 100)
                    counter = reload_wait.counter
                    for _ in range(30):
                        self.step(0x400)
                    self.assertEqual(reload_wait.counter, counter)
                    self.assertEqual(ammo.value(), 100)
                    self.run.frame += 625
                    self.match.update(self.run.frame * combat.TICK, self.run.frame, frames=False)
                    self.assertFalse(winston.primal_active(script.component))
                else:
                    for _ in range(150):
                        self.step(0)
                        if ammo.value() == 100:
                            break
                    self.assertEqual(ammo.value(), 100)
                    self.assertTrue(reload_gate.value())
                    self.step()
                    self.assertEqual(self.health(self.enemy), before)
                    for _ in range(40):
                        self.step(0)
                        if not reload_gate.value():
                            break
                    self.assertFalse(reload_gate.value())
                self.assertEqual(ammo.value(), 100)
                self.assertEqual(self.health(self.enemy), before)
                self.step()
                self.assertAlmostEqual(before - self.health(self.enemy), .96)
                self.assertLess(ammo.value(), 100)
                self.assertFalse(reload_gate.value())

    def test_death_during_windup_cancels_damage(self):
        self.move(self.enemy, (0, 1, 3.5))
        self.move(self.ally, (15, 1, 15))
        self.rage()
        script = self.owner.body_script
        weapon = next(i for i in script.component.instances.values() if i.graph.index == 0xFE)
        for _ in range(45):
            self.step()
            if any(weapon.states.get(i) is not None and weapon.states[i].active for i in (20, 21)):
                break
        self.assertTrue(any(weapon.states.get(i) is not None and weapon.states[i].active for i in (20, 21)))
        self.assertEqual(self.health(self.enemy), 200)
        target = next(t for t in self.fight._targets() if t.owner is self.owner)
        vitals = self.fight.vitals_of(target)
        vitals.damage(vitals.total - 1)
        yaw, pitch = aim(self.fight._eye(self.enemy), (0, 2, 0))
        self.run.step({self.enemy: (yaw, pitch, 1)})
        self.assertTrue(self.fight.is_dead(self.owner))
        self.assertEqual(vitals.killer, self.enemy.body)
        self.assertFalse(winston.primal_active(script.component))
        self.assertNotIn(self.owner.body, self.fight.primal_bonus)
        self.assertEqual(sum(vitals.most.values()), 500)
        self.assertFalse(any(weapon.states.get(i) is not None and weapon.states[i].active
                             for i in (20, 21, 35, 36)))
        self.move(self.enemy, (0, 1, 1.5))
        self.run.frame += 40
        self.match.update(self.run.frame * combat.TICK, self.run.frame, frames=False)
        self.step()
        self.assertEqual(self.health(self.enemy), 200)

    def test_jump_reset_two_second_cooldown_and_restore_on_expiry(self):
        self.step(8)
        for _ in range(100):
            self.step(0)
        jump = next(i for i in self.owner.body_script.component.instances.values()
                    if i.graph.index == 0x10A)
        ability = jump.states[19]
        self.assertGreater(ability.remaining(), 4)
        self.rage()
        self.assertEqual(ability.remaining(), 0)
        self.assertEqual(jump.vars[472].value(), 2)
        for _ in range(35):
            self.step(0)
        self.step(8)
        self.assertGreater(ability.remaining(), 1.9)
        first = ability.last
        self.step(0)
        self.step(8)
        self.assertEqual(ability.last, first)
        for _ in range(625):
            self.step(0)
        self.assertFalse(winston.primal_active(self.owner.body_script.component))
        self.assertEqual(jump.vars[472].value(), 6)
        self.assertEqual(ability.remaining(), 0)

    def test_primal_expiry_resets_jump_cooldown_without_replaying_active_flight(self):
        self.rage()
        component = self.owner.body_script.component
        jump = next(i for i in component.instances.values() if i.graph.index == 0x10A)
        ability = jump.states[19]
        expiry = self.run.frame + 625
        self.run.frame = expiry - 6
        self.match.update(self.run.frame * combat.TICK, self.run.frame, frames=False)
        self.step(8, pitch=-16384)
        launch_frame = self.run.frame
        flights = self.fight.winston_jump.flights
        flight = flights[self.owner.body]
        first = ability.last
        self.assertEqual(ability.cur, 2)
        self.assertEqual(jump.states[2].counter, 1)

        while self.run.frame < expiry:
            self.step(0)
        self.assertFalse(winston.primal_active(component))
        self.assertEqual(jump.vars[472].value(), 6)
        self.assertEqual(ability.cur, 0)
        self.assertGreater(ability.last, first)
        self.assertEqual(ability.remaining(), 0)
        self.assertIs(flights[self.owner.body], flight)

        self.owner.take_commands([Command(launch_frame, buttons=8, pitch=-16384)])
        self.assertIs(flights[self.owner.body], flight)
        self.assertEqual(jump.states[2].counter, 1)
        self.assertEqual(ability.remaining(), 0)
        self.step(8, pitch=-16384)
        self.assertIs(flights[self.owner.body], flight)
        self.assertEqual(jump.states[2].counter, 1)
        self.assertEqual(ability.remaining(), 0)
        for _ in range(260):
            self.step(0)
            if self.owner.body not in flights:
                break
        self.assertNotIn(self.owner.body, flights)
        self.assertEqual(jump.states[2].counter, 1)
        self.step(8, pitch=-16384)
        self.assertEqual(ability.cur, 6)
        self.assertEqual(jump.states[2].counter, 2)
        self.assertIsNot(flights[self.owner.body], flight)

    def test_primal_air_jump_recast_has_a_fresh_movement_effect(self):
        self.rage()
        for _ in range(35):
            self.step(0)
        self.step(8, pitch=-16384)
        for _ in range(128):
            self.step(0)
        jump = next(i for i in self.owner.body_script.component.instances.values()
                    if i.graph.index == 0x10A)
        self.assertIn(self.owner.body, self.fight.winston_jump.flights)
        self.assertLess(self.owner.mover.velocity[1], 0)
        self.step(8, pitch=-16384)
        self.assertGreater(self.owner.mover.velocity[1], 29)
        self.assertTrue(jump.states[2].active)
        self.assertGreater(jump.states[2].counter, 1)

    def test_primal_actual_speed_modifier_ends_with_transformation(self):
        def walk(frames):
            for _ in range(frames):
                self.run.frame += 1
                self.match.update(self.run.frame * combat.TICK, self.run.frame)
                self.owner.take_commands([Command(self.run.frame, forward=127)])

        walk(80)
        self.assertAlmostEqual(self.owner.mover.velocity[2], 5.5, places=3)
        self.rage()
        walk(80)
        self.assertAlmostEqual(self.owner.mover.velocity[2], 7.15, places=3)
        walk(630)
        self.assertFalse(winston.primal_active(self.owner.body_script.component))
        self.assertAlmostEqual(self.owner.mover.velocity[2], 5.5, places=3)

    def test_knockback_collision_stops_enemy_at_wall(self):
        # Use a real static collision fixture, not a mocked knockback result.
        geometry = World(box(-30, 30, -1, 1, -30, 30) + wall(-10, 4, 10, 4))
        self.match.collision_world = geometry
        self.rage()
        self.first_hit()
        for _ in range(70):
            self.step(0)
        self.assertLess(self.enemy.mover.position[2], 4)
        self.assertGreater(self.enemy.mover.position[2], 2)

    def test_primal_shield_contact_respects_static_occlusion(self):
        for occluded in (False, True):
            with self.subTest(occluded=occluded):
                self.setUp()
                self.match.switch_hero(self.enemy, heroes()[winston.HERO])
                self.move(self.enemy, (0, 1, 6.5))
                self.run.step({self.enemy: (0, 0, 16)})
                for _ in range(3):
                    self.run.step({})
                barrier = bubble_for(self.fight, self.enemy.body)
                self.assertIsNotNone(barrier)
                if occluded:
                    self.match.collision_world = World(box(-30, 30, -1, 1, -30, 30)
                                                       + wall(-10, .8, 10, .8))
                self.rage()
                for _ in range(100):
                    self.step()
                if occluded:
                    self.assertEqual(barrier.health, 700)
                else:
                    self.assertLess(barrier.health, 700)


if __name__ == '__main__':
    unittest.main()
