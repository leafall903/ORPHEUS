"""Actual Winston weapon graph -> server health; reconstructed geometry, no client acceptance."""
import unittest

from test_game_combat import Run, aim
from test_game_mover import box, wall

from ow174.game import combat, winston
from ow174.game.collision import World
from ow174.game.commands import FIRE, Command
from ow174.game.content import heroes
from ow174.game.script.driver import HUD_MAX_HEALTH, ULT_CHARGE, ULT_COST

WINSTON = 0x02E0000000000009


class TeslaTests(unittest.TestCase):
    def setUp(self):
        self.run = Run(names=("Winston", "Enemy", "Ally"))
        self.match = self.run.match
        self.npc = self.match.bots[0]
        self.match.bots.clear()
        self.winston, self.enemy, self.ally = self.run.players
        self.match.switch_hero(self.winston, heroes()[WINSTON])
        self.winston.team = self.ally.team = 0
        self.enemy.team = 1
        self.winston.mover.position = [0, 0, 0]
        self.enemy.mover.position = [0, 0, 4]
        self.ally.mover.position = [0, 0, 3]
        self.fight = self.match.combat
        self.fight.now = self.run.frame * combat.TICK
        self.targets = {t.owner: t for t in self.fight._targets()}

    def health(self, who):
        return self.fight.vitals_of(self.targets[who]).total

    def step(self, buttons=FIRE):
        self.run.step({self.winston: (0, 0, buttons)})

    def test_actual_fire_state_damages_enemy_and_preserves_ally(self):
        before = self.health(self.enemy)
        self.step()
        self.assertAlmostEqual(before - self.health(self.enemy), 60 * combat.TICK)
        self.assertEqual(self.health(self.ally), 200)
        self.assertEqual(self.health(self.winston), 500)
        self.assertTrue(self.winston.client.messages(combat.HIT))

    def test_continuous_damage_and_release(self):
        for _ in range(20):
            self.step()
        self.assertAlmostEqual(self.health(self.enemy), 200 - 20 * 60 * combat.TICK)
        self.step(0)
        stopped = self.health(self.enemy)
        for _ in range(5):
            self.step(0)
        self.assertEqual(self.health(self.enemy), stopped)

    def test_one_command_cannot_damage_twice_or_replay_backwards(self):
        self.step()
        before = self.health(self.enemy)
        for frame in (self.run.frame, self.run.frame - 1):
            self.fight.command(self.winston, Command(frame, buttons=FIRE))
        self.assertEqual(self.health(self.enemy), before)

    def test_beam_hits_multiple_enemies(self):
        self.ally.team = 1
        self.step()
        self.assertLess(self.health(self.enemy), 200)
        self.assertLess(self.health(self.ally), 200)

    def test_range_and_rear_targets_are_rejected(self):
        self.enemy.mover.position = [0, 0, 12]
        self.ally.team = 1
        self.ally.mover.position = [0, 0, -3]
        self.step()
        self.assertEqual(self.health(self.enemy), 200)
        self.assertEqual(self.health(self.ally), 200)

    def test_world_occlusion(self):
        floor = box(-30, 30, -1, 1, -30, 30)
        self.match.collision_world = World(floor + wall(-8, 2, 8, 2))
        self.winston.mover.position[:] = [0, 1, 0]
        self.enemy.mover.position[:] = [0, 1, 4]
        script = self.winston.body_script
        script.ult_seconds = 0
        charge = script.component.entity_var(ULT_CHARGE)
        self.step()
        self.assertEqual(self.health(self.enemy), 200)
        self.assertEqual(charge.value(), 0)
        self.match.collision_world = World(floor)
        self.step()
        self.assertAlmostEqual(self.health(self.enemy), 199.04)
        self.assertAlmostEqual(charge.value(), .96)

    def test_beam_uses_reconstructed_continuous_armour_rule(self):
        self.fight.vitals[self.enemy.body] = combat.Vitals(self.enemy.body, 100, 100)
        self.step()
        self.assertAlmostEqual(self.health(self.enemy), 200 - 60 * combat.TICK * .8)

    def test_retained_npc_is_damaged_killed_and_not_replaced(self):
        npc = self.npc
        npc.position, npc.team, npc.route = (0, 0, 4), 1, []
        npc.retain_corpse = True
        self.match.bots.append(npc)
        self.fight.vitals[npc.entity] = combat.Vitals(npc.entity, 1.5)
        self.step()
        self.assertAlmostEqual(self.fight.vitals[npc.entity].total, .54)
        self.step()
        self.assertTrue(npc.dead)
        self.assertEqual(self.fight.vitals[npc.entity].total, 0)
        for _ in range(200):
            self.step(0)
        self.assertIn(npc, self.match.bots)
        self.assertFalse(any(who is npc for _, who in self.fight.respawns))

    def test_player_attack_reaches_winston_armour(self):
        self.fight._damage(self.enemy, self.targets[self.winston], 100,
                           False, None, self.run.frame)
        self.assertEqual(self.health(self.winston), 405)

    def test_retiring_body_discards_beam_frame_guard(self):
        self.step()
        old = self.winston.body
        self.assertIn(old, self.fight.tesla_frames)
        self.fight.retire_player_body(self.winston)
        self.assertNotIn(old, self.fight.tesla_frames)

    def test_npc_attacks_damage_winston_armour_and_death_stops_fire(self):
        source = self.npc
        target = self.targets[self.winston]
        self.fight._damage(source, target, 100, False, None, self.run.frame)
        self.assertEqual(self.health(self.winston), 405)
        self.fight._damage(source, target, 1000, False, None, self.run.frame)
        self.step()
        self.assertTrue(self.fight.is_dead(self.winston))
        self.assertEqual(self.health(self.enemy), 200)

    def test_enemy_death_records_winston_killer_and_stops_further_damage(self):
        self.fight.vitals[self.enemy.body] = combat.Vitals(self.enemy.body, .5)
        self.step()
        vitals = self.fight.vitals[self.enemy.body]
        self.assertTrue(vitals.dead)
        self.assertEqual(vitals.killer, self.winston.body)
        self.step()
        self.assertEqual(vitals.total, 0)

    def test_actual_damage_adds_charge_and_caps_at_authored_cost(self):
        script = self.winston.body_script
        script.ult_seconds = 0
        self.step()
        self.assertAlmostEqual(script.component.entity_var(ULT_CHARGE).value(), .96, places=5)
        cost = script.component.entity_var(ULT_COST).value()
        script.set_ult_charge(cost - .2)
        self.step()
        self.assertEqual(script.component.entity_var(ULT_CHARGE).value(), cost)

    def test_uncharged_ultimate_cannot_create_health(self):
        self.step(0x20)
        self.assertFalse(winston.primal_active(self.winston.body_script.component))
        self.assertEqual(self.health(self.winston), 500)
        self.assertNotIn(self.winston.body, self.fight.primal_bonus)

    def rage(self):
        script = self.winston.body_script
        script.set_ult_charge(script.component.entity_var(ULT_COST).value())
        self.step(0x20)
        return script

    def test_primal_restores_actual_temporary_maximum_and_consumes_charge(self):
        self.fight._damage(self.enemy, self.targets[self.winston], 100,
                           False, None, self.run.frame)
        script = self.rage()
        self.assertTrue(winston.primal_active(script.component))
        self.assertEqual(self.health(self.winston), 1000)
        self.assertEqual(script.component.entity_var(HUD_MAX_HEALTH).value(), 1000)
        self.assertEqual(script.component.entity_var(ULT_CHARGE).value(), 0)
        for _ in range(60):
            self.step(0)
        self.assertEqual(script.component.entity_var(ULT_CHARGE).value(), 0)
        self.assertEqual(self.health(self.winston), 1000)

    def test_primal_expiry_removes_bonus_without_healing_damage(self):
        script = self.rage()
        self.fight._damage(self.enemy, self.targets[self.winston], 900,
                           False, None, self.run.frame)
        before = self.health(self.winston)
        self.assertEqual(before, 105)
        for _ in range(630):
            self.step(0)
        self.assertFalse(winston.primal_active(script.component))
        self.assertEqual(self.health(self.winston), before)
        self.assertEqual(script.component.entity_var(HUD_MAX_HEALTH).value(), 500)
        self.assertNotIn(self.winston.body, self.fight.primal_bonus)

    def test_primal_death_does_not_resurrect_or_recharge(self):
        script = self.rage()
        self.fight._damage(self.enemy, self.targets[self.winston], 2000,
                           False, None, self.run.frame)
        for _ in range(3):
            self.step(0x20)
        self.assertTrue(self.fight.is_dead(self.winston))
        self.assertEqual(self.health(self.winston), 0)
        self.assertEqual(script.component.entity_var(ULT_CHARGE).value(), 0)
        self.assertNotIn(self.winston.body, self.fight.primal_bonus)

    def test_actual_ammo_exhaustion_suppresses_damage_until_reload(self):
        # Keep the enemy alive to measure health changes over the whole magazine.
        self.fight.vitals[self.enemy.body] = combat.Vitals(self.enemy.body, 5000)
        for _ in range(330):
            self.step()
        ins = next(i for i in self.winston.body_script.component.instances.values()
                   if i.graph.index == 0xFE)
        self.assertEqual(ins.vars[53].value(), 0)
        self.assertFalse(ins.states[13].active)
        before = self.health(self.enemy)
        for _ in range(25):
            self.step()
        self.assertEqual(self.health(self.enemy), before)
        for _ in range(100):
            self.step()
        self.assertGreater(ins.vars[53].value(), 0)
        self.assertLess(self.health(self.enemy), before)

    def test_winston_melee_hits_once_for_thirty_and_excludes_ally(self):
        self.match.collision_world = World(box(-30, 30, -1, 1, -30, 30))
        self.winston.mover.position = [0, 1, 0]
        self.enemy.mover.position = [0, 2.25, 1.5]
        self.ally.mover.position = [0, 2.25, 1.5]
        self.step(0x800)
        self.assertGreater(self.enemy.mover.velocity[2], 0)
        self.assertEqual(self.ally.mover.velocity[2], 0)
        for _ in range(40):
            self.step(0)
        self.assertEqual(self.health(self.enemy), 170)
        self.assertEqual(self.health(self.ally), 200)
        self.assertGreater(self.enemy.mover.position[2], 1.5)
        self.assertEqual(self.ally.mover.position[2], 1.5)

    def test_manual_reload_refills_and_unlocks_without_owner_input(self):
        script = self.winston.body_script
        weapon = next(i for i in script.component.instances.values() if i.graph.index == 0xFE)
        for _ in range(40):
            self.step()
        self.step(0)
        self.step(0x400)
        accepted = self.run.frame
        ammo, gate = weapon.vars[53], weapon.vars[51]
        self.assertLess(ammo.value(), 100)
        self.assertTrue(gate.value())
        before = self.health(self.enemy)

        self.run.frame = accepted + 79
        self.match.update(self.run.frame * combat.TICK, self.run.frame, frames=False)
        self.assertEqual(ammo.value(), 100)
        self.assertTrue(gate.value())
        self.assertEqual(self.health(self.enemy), before)

        self.run.frame = accepted + 106
        self.match.update(self.run.frame * combat.TICK, self.run.frame, frames=False)
        self.assertFalse(gate.value())
        self.assertEqual(script.frame, self.run.frame - 1)
        self.assertEqual(self.health(self.enemy), before)
        self.winston.take_commands([Command(self.run.frame, buttons=FIRE)])
        self.assertAlmostEqual(before - self.health(self.enemy), .96)
        self.assertLess(ammo.value(), 100)

    def test_empty_magazine_reloads_while_idle_and_rejects_replayed_fire(self):
        self.fight.vitals[self.enemy.body] = combat.Vitals(self.enemy.body, 5000)
        script = self.winston.body_script
        script.ult_seconds = 0
        charge = script.component.entity_var(ULT_CHARGE)
        weapon = next(i for i in script.component.instances.values() if i.graph.index == 0xFE)
        ammo, gate = weapon.vars[53], weapon.vars[51]
        for _ in range(350):
            self.step()
            if ammo.value() == 0:
                break
        self.assertEqual(ammo.value(), 0)
        self.assertTrue(gate.value())
        empty_frame = self.run.frame
        before_health = self.health(self.enemy)
        before_charge = charge.value()

        self.run.frame += 150
        self.match.update(self.run.frame * combat.TICK, self.run.frame, frames=False)
        self.assertEqual(ammo.value(), 100)
        self.assertFalse(gate.value())
        self.winston.take_commands([
            Command(frame, buttons=FIRE) for frame in
            (empty_frame, self.run.frame - 1, self.run.frame + 1)
        ])
        self.assertEqual(ammo.value(), 100)
        self.assertEqual(self.health(self.enemy), before_health)
        self.assertEqual(charge.value(), before_charge)
        self.winston.take_commands([Command(self.run.frame, buttons=FIRE)])
        self.assertAlmostEqual(before_health - self.health(self.enemy), .96)
        self.assertAlmostEqual(charge.value() - before_charge, .96, places=4)
        self.assertLess(ammo.value(), 100)

    def test_secondary_outside_primal_has_no_tesla_damage_or_ammo_cost(self):
        self.step(2)
        for _ in range(20):
            self.step(2)
        ins = next(i for i in self.winston.body_script.component.instances.values()
                   if i.graph.index == 0xFE)
        self.assertEqual(self.health(self.enemy), 200)
        self.assertEqual(ins.vars[53].value(), 100)

    def jump_ability(self):
        ins = next(i for i in self.winston.body_script.component.instances.values()
                   if i.graph.index == 0x10A)
        return ins, ins.states[19]

    def test_jump_launches_moves_lands_and_retires_movement_subtree(self):
        self.enemy.mover.position = [0, 1, 100]
        self.step(8)
        ins, ability = self.jump_ability()
        self.assertGreater(self.winston.mover.velocity[1], 12)
        self.assertGreater(self.winston.mover.velocity[2], 27)
        self.assertGreater(ability.remaining(), 5.9)
        initial = tuple(self.winston.mover.position)
        for _ in range(120):
            self.step(0)
        self.assertGreater(self.winston.mover.position[2] - initial[2], 10)
        self.assertNotIn(self.winston.body, self.fight.winston_jump.flights)
        self.assertFalse(ins.states[2].active)
        self.assertEqual(self.winston.mover.velocity[1], 0)

    def test_jump_cooldown_rejects_old_click_without_casting_on_expiry(self):
        self.step(8)
        for _ in range(100):
            self.step(0)
        ins, ability = self.jump_ability()
        started = ability.last
        self.step(8)
        for _ in range(300):
            self.step(0)
        self.assertEqual(ability.last, started)
        self.assertEqual(ability.remaining(), 0)
        self.assertEqual(ins.states[2].counter, 1)
        self.step(8)
        self.assertGreater(ability.last, started)
        self.assertEqual(ins.states[2].counter, 2)
        self.assertIn(self.winston.body, self.fight.winston_jump.flights)

    def test_jump_cooldown_write_cannot_launch_or_replace_a_flight(self):
        for airborne in (False, True):
            with self.subTest(airborne=airborne):
                self.setUp()
                body = self.winston.body
                flights = self.fight.winston_jump.flights
                if airborne:
                    self.step(8)
                    self.assertIn(body, flights)
                self.step(0)
                flight = flights.get(body)
                before = self.health(self.enemy)
                instance, ability = self.jump_ability()
                timestamp = ability.last
                instance.write((0, 61), 6.0)
                self.assertGreater(ability.last, timestamp)
                self.step(0)
                self.assertIs(flights.get(body), flight)
                self.assertEqual(self.health(self.enemy), before)

    def test_jump_near_ready_buffer_and_held_button_cannot_bypass_cooldown(self):
        self.step(8)
        ins, ability = self.jump_ability()
        first = ability.last
        for _ in range(360):
            self.step(0)
        self.assertGreater(ability.remaining(), .15)
        for _ in range(10):
            self.step(0)
        self.assertLess(ability.remaining(), .15)
        self.step(8)
        self.assertEqual(ability.last, first)
        for _ in range(12):
            self.step(0)
        self.assertGreater(ability.last, first)
        second = ability.last
        for _ in range(100):
            self.step(8)
        self.assertEqual(ability.last, second)
        self.assertEqual(ins.states[2].counter, 2)

    def test_jump_duplicate_or_reordered_command_cannot_relaunch(self):
        self.step(8)
        velocity = self.winston.mover.velocity
        first = self.fight.winston_jump.seen[self.winston.body]
        for frame in (self.run.frame, self.run.frame - 1):
            self.fight.command(self.winston, Command(frame, buttons=8))
        self.assertEqual(self.winston.mover.velocity, velocity)
        self.assertEqual(self.fight.winston_jump.seen[self.winston.body], first)

    def test_jump_held_input_waits_for_cooldown_then_starts_one_fresh_flight(self):
        self.enemy.mover.position = [0, 1, 100]
        self.step(8)
        ins, ability = self.jump_ability()
        for _ in range(120):
            self.step(0)
        self.assertEqual(ins.states[2].counter, 1)
        self.step(8)
        for _ in range(250):
            self.step(8)
        self.assertGreater(ability.remaining(), 0)
        self.assertEqual(ins.states[2].counter, 1)
        for _ in range(20):
            self.step(8)
        self.assertEqual(ins.states[2].counter, 2)
        self.assertIn(self.winston.body, self.fight.winston_jump.flights)

    def test_jump_death_cancels_flight_without_landing_damage(self):
        self.winston.mover.position[:] = (0, 1, 0)
        self.enemy.mover.position[:] = (3, 1, 0)
        self.run.step({self.winston: (0, -16384, 8)})
        ins, _ = self.jump_ability()
        self.assertIn(self.winston.body, self.fight.winston_jump.flights)
        self.assertTrue(ins.states[2].active)
        vitals = self.fight.vitals_of(self.targets[self.winston])
        vitals.damage(vitals.total - 1)  # leave the lethal hit to the authored rifle
        before = self.health(self.enemy)
        yaw, pitch = aim(self.fight._eye(self.enemy), self.fight._eye(self.winston))
        self.run.step({self.enemy: (yaw, pitch, FIRE)})
        self.assertTrue(vitals.dead)
        self.assertEqual(vitals.killer, self.enemy.body)
        self.assertNotIn(self.winston.body, self.fight.winston_jump.flights)
        self.assertFalse(ins.states[2].active)
        end = self.run.frame + 60
        self.match.update(end * combat.TICK, end, frames=False)
        self.assertNotIn(self.winston.body, self.fight.winston_jump.flights)
        self.assertEqual(self.health(self.enemy), before)

    def test_jump_takeoff_damage_is_one_and_allies_are_excluded(self):
        self.enemy.mover.position = [0, 1, 2]
        self.ally.mover.position = [0, 1, 2]
        self.step(8)
        self.assertEqual(self.health(self.enemy), 199)
        self.assertEqual(self.health(self.ally), 200)
        for _ in range(10):
            self.step(0)
        self.assertEqual(self.health(self.enemy), 199)

    def test_replacement_retires_airborne_jump_and_accepts_a_fresh_cast(self):
        self.enemy.mover.position[:] = (0, 1, 20)
        self.run.step({self.winston: (0, -16384, 8)})
        old = self.winston.body
        self.assertIn(old, self.fight.winston_jump.flights)
        self.assertTrue(self.match.switch_hero(self.winston, heroes()[WINSTON]))
        self.assertNotEqual(self.winston.body, old)
        self.assertNotIn(old, self.fight.winston_jump.flights)
        self.assertNotIn(old, self.fight.winston_jump.seen)
        self.winston.mover.position[:] = (0, 1, 0)
        self.run.step({self.winston: (0, -16384, 8)})
        self.assertIn(self.winston.body, self.fight.winston_jump.flights)
        self.assertEqual(self.jump_ability()[0].states[2].counter, 1)
        self.assertEqual(self.health(self.enemy), 200)

    def test_jump_takeoff_rejects_outside_radius_and_occluded_target(self):
        self.enemy.mover.position = [0, 1, 10]
        self.step(8)
        self.assertEqual(self.health(self.enemy), 200)
        # A fresh lifecycle lets the same real input exercise the LOS branch.
        self.match.switch_hero(self.winston, heroes()[WINSTON])
        self.winston.mover.position[:] = [0, 1, 0]
        self.enemy.mover.position = [0, 1, 2]
        self.match.collision_world = World(
            box(-30, 30, -1, 1, -30, 30) + wall(-8, 1, 8, 1)
        )
        self.step(8)
        self.assertIn(self.winston.body, self.fight.winston_jump.flights)
        self.assertEqual(self.health(self.enemy), 200)

    def test_jump_landing_respects_static_wall_without_losing_flight(self):
        for blocked in (False, True):
            with self.subTest(blocked=blocked):
                self.setUp()
                geometry = box(-30, 30, -1, 1, -30, 30)
                if blocked:
                    geometry += wall(-8, 1, 8, 1)
                self.match.collision_world = World(geometry)
                self.winston.mover.position[:] = [0, 1, 0]
                self.enemy.mover.position[:] = [0, 1, 10]
                script = self.winston.body_script
                script.ult_seconds = 0
                charge = script.component.entity_var(ULT_CHARGE)
                self.run.step({self.winston: (0, -16384, 8)})
                self.assertIn(self.winston.body, self.fight.winston_jump.flights)
                self.enemy.mover.position[:] = [0, 1, 2]
                self.enemy.mover.body = None
                for _ in range(150):
                    self.step(0)
                    if self.winston.body not in self.fight.winston_jump.flights:
                        break
                self.assertNotIn(self.winston.body, self.fight.winston_jump.flights)
                damage = 200 - self.health(self.enemy)
                self.assertEqual(damage > 0, not blocked)
                self.assertAlmostEqual(charge.value(), damage, places=5)

    def test_jump_real_collision_landing_damages_for_fifty(self):
        self.enemy.mover.position = [0, 1, 100]
        self.run.step({self.winston: (0, -16384, 8)})
        for _ in range(150):
            # Set the target at the landing point, after launch so takeoff cannot hit it.
            self.enemy.mover.position[:] = [0, 1, 0]
            self.step(0)
            if self.winston.body not in self.fight.winston_jump.flights:
                break
        self.assertNotIn(self.winston.body, self.fight.winston_jump.flights)
        self.assertEqual(self.health(self.enemy), 150)

    def test_jump_landing_in_recovered_command_frames_hits_once(self):
        self.match.collision_world = World(box(-30, 30, -1, 1, -30, 30))
        self.winston.mover.position[:] = (0, 1, 0)
        self.enemy.mover.position[:] = (0, 1, 10)
        script = self.winston.body_script
        script.ult_seconds = 0
        charge = script.component.entity_var(ULT_CHARGE)
        self.run.step({self.winston: (0, -16384, 8)})
        self.enemy.mover.position[:] = (0, 1, 0)
        self.enemy.mover.body = None
        mover = self.winston.mover
        for _ in range(150):
            self.step(0)
            if mover.velocity[1] < 0 and 1.5 < mover.position[1] < 3:
                break
        self.assertLess(mover.velocity[1], 0)
        self.assertLess(mover.position[1], 3)
        self.assertIn(self.winston.body, self.fight.winston_jump.flights)
        first_missing = self.run.frame + 1
        self.run.frame += 5
        self.match.update(self.run.frame * combat.TICK, self.run.frame, frames=False)
        self.assertEqual(self.health(self.enemy), 200)

        self.step(0)
        self.assertNotIn(self.winston.body, self.fight.winston_jump.flights)
        self.assertEqual(self.health(self.enemy), 150)
        self.assertEqual(charge.value(), 50)
        self.winston.take_commands([Command(first_missing)])
        self.step(0)
        self.assertEqual(self.health(self.enemy), 150)
        self.assertEqual(charge.value(), 50)


if __name__ == '__main__':
    unittest.main()
