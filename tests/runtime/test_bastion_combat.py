"""Actual 104319 graphs and Match inputs; server evidence, not client acceptance."""

import math
import unittest
from dataclasses import replace
from unittest.mock import patch

from test_game_combat import KANEZAKA_DM, Run, aim

from ow174.game import combat
from ow174.game.collision import World
from ow174.game.commands import Command
from ow174.game.content import heroes
from ow174.game.script.driver import BASTION, SOLDIER, ULT_COST


class BastionCombatTests(unittest.TestCase):
    def fixture(self, enemy_position=(0, 1, 10), bystander_position=(5, 1, 10), *, enemy_hero=None):
        run = Run(KANEZAKA_DM, ("Bastion", "Enemy", "Bystander"))
        player, enemy, bystander = run.players
        run.match.bots.clear()
        run.match.switch_hero(player, heroes()[BASTION])
        if enemy_hero is not None:
            run.match.switch_hero(enemy, heroes()[enemy_hero])
        for actor, position in ((player, [0, 1, 0]), (enemy, enemy_position),
                                (bystander, bystander_position)):
            actor.mover.position[:] = position
        self.run, self.player, self.enemy, self.bystander = run, player, enemy, bystander
        self.command_aim = (0, 0)
        self.fight = run.match.combat
        # Keep the intended forward shot/off-axis miss. Aim at a declared torso
        # height for forward targets;Bastion's authored eye is above most heroes.
        if enemy_position[0] == 0 and enemy_position[2] > 0:
            self.command_aim = aim(self.fight._eye(player),
                                   (0, enemy_position[1] + 1.0, enemy_position[2]))
        self.step(0, 40)
        return player.body_script

    def step(self, buttons=0, frames=1):
        for _ in range(frames):
            self.run.step({self.player: (*self.command_aim, buttons)})

    def target(self, owner):
        return next(target for target in self.fight._targets() if target.owner is owner)

    def health(self, owner):
        return self.fight.vitals_of(self.target(owner))

    def configure(self):
        self.step(8)
        self.step(0, 100)

    def tank(self):
        script = self.player.body_script
        script.set_ult_charge(script.component.entity_var(ULT_COST).value())
        self.step(32)
        self.step(0, 110)

    def test_recon_sentry_and_return_use_actual_weapon_damage(self):
        self.fixture()
        before = self.health(self.enemy).total
        self.step(1, 10)
        self.assertEqual(before - self.health(self.enemy).total, 40)
        self.step()
        self.configure()
        before = self.health(self.enemy).total
        self.step(1, 10)
        self.assertEqual(before - self.health(self.enemy).total, 75)
        self.step()
        self.configure()
        self.assertEqual(self.player.body_script.component.entity_var(1216).value(), 0)

    def test_sentry_no_decay_spread_output_uses_authored40_shot_ramp(self):
        script = self.fixture(enemy_position=(10, 1, 0))
        self.configure()
        self.step(1)
        weapon = next(i for i in script.component.instances.values() if i.graph.index == 0x13A)
        state = weapon.states[11]
        self.assertEqual(state.spread_ramp, 40)
        self.assertAlmostEqual(weapon.vars[231].value(), .025, places=7)
        self.step(1, 9)
        self.assertEqual(state.reported, 5)
        self.assertEqual(weapon.vars[231].value(), .125)
        self.step(0, 2)
        self.assertFalse(state.active)
        self.assertEqual(weapon.vars[231].value(), .125)
        self.configure()
        self.step(1)
        recon = next(i for i in script.component.instances.values() if i.graph.index == 0x12E)
        recon_state = recon.states[14]
        self.assertEqual(recon_state.spread_ramp, 8)
        self.assertAlmostEqual(recon_state.spread_decay, .075)
        self.assertAlmostEqual(recon_state.spread_at(0), .125)
        self.assertLess(recon_state.spread_at(16), .125)

    def test_recon_and_sentry_spread_hits_targets_off_the_center_ray(self):
        for sentry, x, z, damage in ((False, .5625, 90, 6), (True, -.75, 60, 13.5)):
            with self.subTest(sentry=sentry):
                self.fixture(enemy_position=(x, 1, z), bystander_position=(12, 1, 10))
                if sentry:
                    self.configure()
                self.command_aim = aim(self.fight._eye(self.player), (0, 2, z))
                self.assertGreater(abs(x), self.target(self.enemy).radius)
                before = self.health(self.enemy).total
                self.step(1, 10)
                self.assertAlmostEqual(before - self.health(self.enemy).total, damage, places=5)

    def test_repair_heals_only_self_and_stops_on_release(self):
        script = self.fixture()
        own = self.health(self.player)
        own.damage(150)
        self.fight._sync_body_health(self.player, own)
        self.health(self.enemy).damage(50)
        self.health(self.bystander).damage(50)
        self.step(2, 80)
        self.assertGreater(own.total, 155)
        self.assertLessEqual(own.total, 300)
        self.assertEqual(self.health(self.enemy).total, 150)
        self.assertEqual(self.health(self.bystander).total, 150)
        self.assertEqual(script.component.entity_var(1732).value(), own.total)
        self.step()
        healed = own.total
        self.step(0, 30)
        self.assertEqual(own.total, healed)
        # Consuming the same command cannot replay already consumed health reports.
        self.fight.command(self.player, Command(self.run.frame, buttons=2))
        self.assertEqual(own.total, healed)

    def test_repair_resource_exhausts_under_sustained_damage(self):
        script = self.fixture()
        own = self.health(self.player)
        own.damage(150)
        self.fight._sync_body_health(self.player, own)
        for _ in range(250):
            self.step(2)
            if own.total > 150:
                own.damage(own.total - 150)
                self.fight._sync_body_health(self.player, own)
        state = next(i for i in script.component.instances.values() if i.graph.index == 0x153).states[0]
        self.assertFalse(state.active)
        before = own.total
        self.step(2, 30)
        self.assertEqual(own.total, before)

    def test_full_health_does_not_overheal_or_consume_fuel(self):
        script = self.fixture()
        self.step(2, 100)
        repair = next(i for i in script.component.instances.values() if i.graph.index == 0x153)
        self.assertEqual(self.health(self.player).total, 300)
        self.assertEqual(repair.vars[87].value(), 100)

    def test_dead_body_discards_pending_repair_and_cannot_revive(self):
        script = self.fixture()
        target = self.target(self.player)
        script.self_repairs.append(90)
        self.fight._damage(self.enemy, target, 10000, False, None, self.run.frame)
        self.fight.command(self.player, Command(self.run.frame + 1, buttons=2))
        self.assertTrue(self.health(self.player).dead)
        self.assertEqual(self.health(self.player).total, 0)
        self.assertEqual(script.self_repairs, [])

    def test_ironclad_reduces_before_armour_and_retires_on_recon(self):
        self.fixture()
        self.configure()
        target = self.target(self.player)
        before = self.health(self.player).total
        self.fight._damage(self.enemy, target, 100, False, None, self.run.frame)
        self.assertAlmostEqual(before - self.health(self.player).total, 75, places=5)
        self.configure()
        before = self.health(self.player).total
        self.fight._damage(self.enemy, target, 20, False, None, self.run.frame)
        self.assertAlmostEqual(before - self.health(self.player).total, 15)

    def test_tank_projectile_causes_authoritative_death(self):
        script = self.fixture()
        script.set_ult_charge(script.component.entity_var(ULT_COST).value())
        self.step(32)
        self.step(0, 110)
        self.step(1, 20)
        self.step(0, 30)
        self.assertTrue(self.health(self.enemy).dead)
        self.assertTrue(self.enemy.client.messages(combat.DEATH_NOTICE))
        self.assertTrue(self.player.client.messages(combat.KILL_NOTICE))

    def test_melee_damage_uses_actual_input(self):
        self.fixture(enemy_position=(0, 1, 1.4))
        self.step(0x800, 30)
        self.assertEqual(self.health(self.enemy).total, 170)

    def wall(self, z):
        self.run.match.collision_world = World([
            (-500, 1, -500, -500, 1, 500, 500, 1, 500),
            (-500, 1, -500, 500, 1, 500, 500, 1, -500),
            (-10, 0, z, 10, 0, z, 10, 10, z),
            (-10, 0, z, 10, 10, z, -10, 10, z),
        ])

    def test_recon_and_sentry_miss_range_and_world_occlusion(self):
        for sentry in (False, True):
            for reason in ("aim", "range", "wall"):
                with self.subTest(sentry=sentry, reason=reason):
                    self.fixture(enemy_position=(0, 1, 110 if reason == "range" else 10))
                    if sentry:
                        self.configure()
                    if reason == "wall":
                        self.wall(5)
                    for _ in range(10):
                        self.run.step({self.player: (16384 if reason == "aim" else 0, 0, 1)})
                    self.assertEqual(self.health(self.enemy).total, 200)

    def test_melee_miss_range_and_world_occlusion(self):
        for reason in ("aim", "range", "wall"):
            with self.subTest(reason=reason):
                self.fixture(enemy_position=(0, 1, 5 if reason == "range" else 1.4))
                if reason == "wall":
                    self.wall(0.7)
                for _ in range(30):
                    self.run.step({self.player: (16384 if reason == "aim" else 0, 0, 0x800)})
                self.assertEqual(self.health(self.enemy).total, 200)

    def test_team_allies_are_excluded_from_recon_fire(self):
        self.fixture()
        self.run.match.game_map = replace(self.run.match.game_map, free_for_all=False, team_sizes=(6, 6))
        self.player.team = self.bystander.team = 0
        self.enemy.team = 1
        self.bystander.mover.position = [0, 1, 5]
        self.step(0, 2)
        self.step(1, 10)
        self.assertEqual(self.health(self.bystander).total, 200)
        self.assertEqual(self.health(self.enemy).total, 160)

    def test_missing_pose_releases_repair_without_healing_the_entire_gap(self):
        self.fixture()
        own = self.health(self.player)
        own.damage(150)
        self.fight._sync_body_health(self.player, own)
        self.step(2, 50)
        before = own.total
        frame = self.run.frame + 200
        self.player.take_commands([Command(frame)])
        self.assertEqual(own.total, before)

    def test_reconfigure_rejects_reentry_during_transition_then_reuses(self):
        script = self.fixture()
        self.step(8)
        self.assertEqual(script.component.entity_var(1216).value(), 1)
        self.step()
        self.step(8)
        self.assertEqual(script.component.entity_var(1216).value(), 1)
        self.step(0, 100)
        self.assertEqual(script.component.entity_var(1216).value(), 2)
        self.configure()
        self.assertEqual(script.component.entity_var(1216).value(), 0)

    def test_tank_requires_charge_consumes_it_and_expires(self):
        script = self.fixture()
        script.set_ult_charge(0)
        self.step(32)
        self.step(0, 20)
        self.assertNotEqual(script.component.entity_var(1216).value(), 4)
        script.set_ult_charge(script.component.entity_var(ULT_COST).value())
        self.step(32)
        self.assertEqual(script.component.entity_var(342).value(), 0)
        self.assertEqual(script.component.entity_var(1216).value(), 4)
        self.step(0, 110)
        self.assertEqual(script.component.entity_var(1216).value(), 6)
        self.assertAlmostEqual(self.fight._bastion_damage_scalar(self.player), 0.8)
        self.step(0, 700)
        self.assertEqual(script.component.entity_var(1216).value(), 0)
        self.assertEqual(self.fight._bastion_damage_scalar(self.player), 1)

    def test_tank_direct_and_splash_damage_values(self):
        self.fixture(bystander_position=(2, 1, 10))
        self.tank()
        self.step(1)
        self.step(0, 20)
        hits = self.player.client.messages(combat.HIT)
        direct_hits = [hit["+0x78"]["+0x4"] for hit in hits
                       if hit["+0x78"]["+0x0"]["+0x0"] == self.enemy.body]
        self.assertEqual(direct_hits, [65, 135])  # 140 blast capped by remaining HP
        self.assertTrue(self.health(self.enemy).dead)
        # Radius .5 target capsule; impact at the near hemisphere of a capsule
        # two metres across. Authored blast rings: 140 at 1 m, 42 at 4 m.
        distance = math.sqrt(4.25) - 0.5
        expected = 140 * (1 - (distance - 1) * 0.7 / 3)
        self.assertAlmostEqual(200 - self.health(self.bystander).total, expected, places=4)

    def test_tank_wall_impact_does_not_splash_through_the_wall(self):
        self.fixture(enemy_position=(0, 1, 6), bystander_position=(0.8, 1, 4))
        self.tank()
        self.wall(5)
        self.step(1)
        self.step(0, 20)
        self.assertEqual(self.health(self.enemy).total, 200)
        self.assertLess(self.health(self.bystander).total, 200)

    def test_tank_sustained_fire_uses_authored_one_second_cadence(self):
        self.fixture(enemy_position=(10, 1, 0))
        self.tank()
        rockets, fired = [], []
        for frame in range(1, 191):
            self.step(1)
            for rocket in self.fight.rockets:
                if not any(previous is rocket for previous in rockets):
                    rockets.append(rocket)
                    fired.append(frame)
        # Authored one-second intervals sampled on the 16 ms simulation clock.
        self.assertEqual(fired, [1, 63, 126, 189])
        self.assertEqual(self.health(self.enemy).total, 200)

    def test_tank_aim_miss_and_complete_range_lifetime(self):
        for reason in ("aim", "within_range", "beyond_range"):
            with self.subTest(reason=reason):
                self.fixture(enemy_position=(0, 1, 101 if reason == "beyond_range" else 100))
                self.tank()
                self.run.step({self.player: (16384 if reason == "aim" else 0, 0, 1)})
                self.assertEqual(len(self.fight.rockets), 1)
                rocket = self.fight.rockets[0]
                self.assertEqual(rocket.speed, 60)
                self.assertAlmostEqual(rocket.left, 100 / 60)
                self.step(0, 110)
                self.assertFalse(self.fight.rockets)
                if reason == "within_range":
                    self.assertTrue(self.health(self.enemy).dead)
                else:
                    self.assertEqual(self.health(self.enemy).total, 200)
                    self.assertAlmostEqual(rocket.flown, 100 / 60)

    def test_sentry_melee_and_tank_pass_allies_and_preserve_friendly_pools(self):
        for attack in ("sentry", "melee", "tank_direct", "tank_splash"):
            with self.subTest(attack=attack):
                self.fixture(enemy_position=(0, 1, 1.4 if attack == "melee" else 10),
                             bystander_position=(2, 1, 10) if attack == "tank_splash" else (0, 1, 1))
                self.run.match.game_map = replace(self.run.match.game_map,
                                                  free_for_all=False, team_sizes=(6, 6))
                self.player.team = self.bystander.team = 0
                self.enemy.team = 1
                if attack == "sentry":
                    self.configure()
                elif attack.startswith("tank"):
                    self.tank()
                self.step(0x800 if attack == "melee" else 1)
                self.step(0, 30)
                self.assertEqual(self.health(self.bystander).total, 200)
                self.assertLess(self.health(self.enemy).total, 200)

    def test_tank_entry_active_and_exit_boundaries_from_both_configurations(self):
        for sentry in (False, True):
            with self.subTest(sentry=sentry):
                script = self.fixture(enemy_position=(10, 1, 0))
                if sentry:
                    self.configure()
                script.set_ult_charge(script.component.entity_var(ULT_COST).value())
                self.step(32 | 1)
                self.assertEqual(script.component.entity_var(1216).value(), 5 if sentry else 4)
                self.step(1, 92)
                self.assertFalse(self.fight.rockets)
                self.assertEqual(script.component.entity_var(342).value(), 0)
                self.step(1)
                self.assertEqual(script.component.entity_var(1216).value(), 6)
                self.assertEqual(len(self.fight.rockets), 1)
                self.step(0, 499)
                self.assertEqual(script.component.entity_var(1216).value(), 6)
                self.step()
                self.assertEqual(script.component.entity_var(1216).value(), 7)
                self.assertEqual(script.component.entity_var(342).value(), 0)
                self.step(1, 62)
                self.assertEqual(script.component.entity_var(1216).value(), 7)
                self.assertFalse(self.fight.rockets)
                self.step()
                self.assertEqual(script.component.entity_var(1216).value(), 0)

    def test_recon_sentry_reload_reject_fire_until_ammo_refill(self):
        for sentry, wait_frames, clip in ((False, 120, 35), (True, 180, 300)):
            with self.subTest(sentry=sentry):
                script = self.fixture(enemy_position=(10, 1, 0))
                if sentry:
                    self.configure()
                weapon = next(i for i in script.component.instances.values()
                              if i.graph.index == (0x13a if sentry else 0x12e))
                self.step(1, 10)
                self.step(0, 2)
                ammo = weapon.vars[53].value()
                self.assertLess(ammo, clip)
                self.step(0x400)
                self.step(0, 15)
                self.step(1, 10)
                self.assertEqual(weapon.vars[53].value(), ammo)
                self.step(0, wait_frames)
                self.assertEqual(weapon.vars[53].value(), clip)
                self.step(1)
                self.assertEqual(weapon.vars[53].value(), clip - 1)

    def test_reload_has_distinct_ammo_refill_and_fire_recovery_boundaries(self):
        for sentry, clip, initial_wait, refill_ms, total_ms, damage in (
                (False, 35, 21, 734, 1500, 20), (True, 300, 26, 1546, 2000, 15)):
            with self.subTest(sentry=sentry):
                script = self.fixture()
                if sentry:
                    self.configure()
                weapon = next(i for i in script.component.instances.values()
                              if i.graph.index == (0x13a if sentry else 0x12e))
                self.step(1, 10)
                self.step(0, 2)
                self.step(0x400)
                for _ in range(20):
                    wait = weapon.states.get(initial_wait)
                    if wait is not None and wait.active:
                        break
                    self.step()
                self.assertIsNotNone(wait)
                self.assertTrue(wait.active)
                self.assertEqual(wait.ms, refill_ms)
                self.assertEqual(weapon.vars[581].value() * 1000, total_ms)
                ammo, health = weapon.vars[53].value(), self.health(self.enemy).total
                refill_frames = math.ceil(refill_ms / 16)
                recovery_frames = math.ceil(total_ms / 16)
                self.step(1, refill_frames - 1)
                self.assertEqual(weapon.vars[53].value(), ammo)
                self.assertEqual(self.health(self.enemy).total, health)
                self.step(1)
                self.assertEqual(weapon.vars[53].value(), clip)
                self.assertEqual(self.health(self.enemy).total, health)
                self.step(1, recovery_frames - refill_frames - 1)
                self.assertEqual(weapon.vars[53].value(), clip)
                self.assertEqual(self.health(self.enemy).total, health)
                self.step(1)
                self.assertEqual(weapon.vars[53].value(), clip - 1)
                self.assertEqual(health - self.health(self.enemy).total, damage)

    def test_reconfigure_during_reload_preserves_recon_ammo_and_refills_sentry(self):
        for sentry, graph, refill, recovery, clip, damage in (
            (False, 0x12E, 21, 20, 35, 20),
            (True, 0x13A, 26, 25, 300, 15),
        ):
            for after_refill in (False, True):
                with self.subTest(sentry=sentry, after_refill=after_refill):
                    script = self.fixture()
                    if sentry:
                        self.configure()
                    weapon = next(i for i in script.component.instances.values() if i.graph.index == graph)
                    mode = script.component.entity_var(1216)
                    self.step(1, 10)
                    self.step(0, 2)
                    spent = weapon.vars[53].value()
                    self.assertLess(spent, clip)
                    self.step(0x400)
                    wait_index = recovery if after_refill else refill
                    for _ in range(120):
                        wait = weapon.states.get(wait_index)
                        if wait is not None and wait.active:
                            break
                        self.step()
                    self.assertIsNotNone(wait)
                    self.assertTrue(wait.active)
                    loaded = clip if after_refill else spent
                    self.assertEqual(weapon.vars[53].value(), loaded)

                    enemy = self.health(self.enemy)
                    before = enemy.total
                    self.step(8)
                    self.assertEqual(mode.value(), 3 if sentry else 1)
                    self.assertEqual(wait.active, after_refill)
                    self.assertEqual(weapon.vars[53].value(), loaded)
                    self.step(1)
                    self.step(0, 150)
                    self.assertEqual(enemy.total, before)
                    self.assertFalse(wait.active)
                    self.assertEqual(mode.value(), 0 if sentry else 2)
                    self.configure()
                    self.assertEqual(mode.value(), 2 if sentry else 0)
                    returned_ammo = clip if sentry else loaded
                    self.assertEqual(weapon.vars[53].value(), returned_ammo)
                    self.step(1)
                    self.assertEqual(weapon.vars[53].value(), returned_ammo - 1)
                    self.assertEqual(before - enemy.total, damage)

    def test_repair_cancels_reload_before_refill_and_can_reload_again(self):
        for sentry in (False, True):
            with self.subTest(sentry=sentry):
                script = self.fixture()
                if sentry:
                    self.configure()
                weapon = next(i for i in script.component.instances.values()
                              if i.graph.index == (0x13a if sentry else 0x12e))
                own = self.health(self.player)
                own.damage(100)
                self.fight._sync_body_health(self.player, own)
                self.step(1, 10)
                self.step(0, 2)
                self.step(0x400)
                self.step(0, 19)
                ammo = weapon.vars[53].value()
                self.assertLess(ammo, weapon.vars[198].value())
                self.step(2, 50)
                self.assertGreater(own.total, 200)
                self.step(0, 150)
                self.assertEqual(weapon.vars[53].value(), ammo)
                self.step(0x400)
                self.step(0, 200)
                self.assertEqual(weapon.vars[53].value(), weapon.vars[198].value())
                before = self.health(self.enemy).total
                self.step(1)
                self.assertLess(self.health(self.enemy).total, before)

    def test_repair_cancelling_after_refill_preserves_the_loaded_ammunition(self):
        for sentry in (False, True):
            with self.subTest(sentry=sentry):
                script = self.fixture()
                if sentry:
                    self.configure()
                weapon = next(i for i in script.component.instances.values()
                              if i.graph.index == (0x13a if sentry else 0x12e))
                self.step(1, 10)
                self.step(0, 2)
                self.step(0x400)
                for _ in range(150):
                    if weapon.vars[53].value() == weapon.vars[198].value():
                        break
                    self.step()
                self.assertEqual(weapon.vars[53].value(), weapon.vars[198].value())
                recovery = weapon.states[25 if sentry else 20]
                self.assertTrue(recovery.active)
                self.step(2, 50)
                self.assertFalse(recovery.active)
                self.step(0, 150)
                self.assertEqual(weapon.vars[53].value(), weapon.vars[198].value())
                before = self.health(self.enemy).total
                self.step(1)
                self.assertLess(self.health(self.enemy).total, before)

    def test_sentry_and_tank_repair_and_recon_reuses_after_tank_while_melee_stays_blocked(self):
        for tank in (False, True):
            with self.subTest(tank=tank):
                script = self.fixture(enemy_position=(0, 1, 1.4))
                self.tank() if tank else self.configure()
                self.step(0x800, 40)
                self.assertEqual(self.health(self.enemy).total, 200)
                own = self.health(self.player)
                own.damage(150)
                self.fight._sync_body_health(self.player, own)
                before = own.total
                self.step(2, 50)
                self.assertAlmostEqual(own.total - before, 50.4)
                if tank:
                    repair = next(i for i in script.component.instances.values() if i.graph.index == 0x153)
                    self.assertTrue(repair.states[0].active)
                    self.assertLess(repair.vars[87].value(), 100)
                    self.step(0, 500)
                    self.assertEqual(script.component.entity_var(1216).value(), 0)
                    before = own.total
                    self.step(2, 50)
                    self.assertAlmostEqual(own.total - before, 50.4)

    def test_sentry_static_core_uses_visible_rear_pose_for_critical_damage(self):
        for case, yaw, attacker_z, x, height, damage in (
            ('rear', 0, -3, 0, 1.259, 54),
            ('front', 0, 3, 0, 1.259, 22),
            ('rear body', 0, -3, 0.24, 1.259, 22),
            ('front upper', 0, 3, 0, 1.795, 22),
            ('rotated rear', -32768, 3, 0, 1.259, 54),
            ('rotated front', -32768, -3, 0, 1.259, 22),
            ('rear wall', 0, -3, 0, 1.259, 0),
            ('rear friendly', 0, -3, 0, 1.259, 0),
        ):
            with self.subTest(case=case):
                self.fixture(enemy_position=(0, 1, attacker_z), enemy_hero=SOLDIER)
                self.command_aim = (yaw, 0)
                self.configure()
                self.assertEqual(self.target(self.player).sentry_yaw, yaw)
                if case == 'rear wall':
                    self.wall(-1.5)
                elif case == 'rear friendly':
                    match = self.run.match
                    match.game_map = replace(match.game_map, free_for_all=False, team_sizes=(6, 6))
                    self.enemy.team = self.player.team
                core_z = 0.61 if yaw else -0.61
                yaw_at, pitch = aim(self.fight._eye(self.enemy), (x, 1 + height, core_z))
                health = self.health(self.player)
                before = health.total
                with patch.object(self.fight, '_tell_hit', wraps=self.fight._tell_hit) as report:
                    for _ in range(10):
                        self.run.step({self.player: (yaw, 0, 0), self.enemy: (yaw_at, pitch, 1)})
                self.assertAlmostEqual(before - health.total, damage, places=5)
                self.assertEqual([bool(call.args[3] & combat.CRITICAL) for call in report.call_args_list],
                                 [damage == 54] * (2 if damage else 0))
                old_tick = self.run.frame
                self.command_aim = (0 if yaw else -32768, 0)
                self.step(0, 3)
                current = self.target(self.player)
                self.assertNotEqual(current.sentry_yaw, yaw)
                self.assertEqual(self.fight.seen(current, old_tick).sentry_yaw, yaw)

    def test_recon_head_hits_double_damage_and_sentry_head_hits_do_not(self):
        for sentry, height, damage, critical in (
            (False, 'torso', 40, False), (False, 'head', 80, True), (False, 'miss', 0, False),
            (True, 'torso', 75, False), (True, 'head', 75, False), (True, 'miss', 0, False),
        ):
            with self.subTest(sentry=sentry, height=height):
                self.fixture(enemy_position=(0, 1, 3))
                if sentry:
                    self.configure()
                target = self.target(self.enemy)
                aim_height = {'torso': 1, 'head': target.eye - 0.025, 'miss': target.height + 0.5}[height]
                self.command_aim = aim(self.fight._eye(self.player), (0, target.feet[1] + aim_height, 3))
                health = self.health(self.enemy)
                before = health.total
                with patch.object(self.fight, '_tell_hit', wraps=self.fight._tell_hit) as report:
                    self.step(1, 10)
                self.assertEqual(before - health.total, damage)
                self.assertEqual(report.call_count, (5 if sentry else 2) if damage else 0)
                self.assertEqual([bool(call.args[3] & combat.CRITICAL) for call in report.call_args_list],
                                 [critical] * report.call_count)

    def test_recon_sentry_falloff_changes_actual_damage(self):
        for sentry, middle, start, end, full in ((False, 35, 25, 45, 40),
                                                (True, 40, 30, 50, 75)):
            for distance in (10, middle, 60):
                with self.subTest(sentry=sentry, distance=distance):
                    self.fixture(enemy_position=(0, 1, distance))
                    if sentry:
                        self.configure()
                    # Isolate falloff at the center-ray distance. Spread has its own hit test.
                    with patch.object(combat.weapon_spread, 'direction',
                                      side_effect=lambda forward, *_: forward):
                        self.step(1, 10)
                    # The aimed ray meets the capsule's cylindrical side at
                    # z=distance-radius. Include the quantized downward angle.
                    angle = self.command_aim[1] * 2 * math.pi / 65536
                    reach = (distance - 0.5) / math.cos(angle)
                    scalar = 1 if reach <= start else 0.3 if reach >= end else (
                        1 - 0.7 * (reach - start) / (end - start))
                    self.assertAlmostEqual(200 - self.health(self.enemy).total, full * scalar, places=4)

    def walk(self, forward, right=0, buttons=0, frames=30):
        for _ in range(frames):
            self.run.frame += 1
            self.run.match.update(self.run.frame * combat.TICK, self.run.frame)
            for player in self.run.players:
                player.take_commands([Command(self.run.frame,
                    forward=forward if player is self.player else 0,
                    right=right if player is self.player else 0,
                    buttons=buttons if player is self.player else 0)])

    def test_sentry_blocks_movement_jump_and_crouch_and_recon_restores_movement(self):
        for forward, right in ((127, 0), (-127, 0), (0, 127), (0, -127)):
            with self.subTest(forward=forward, right=right):
                self.fixture(enemy_position=(10, 1, 0))
                self.configure()
                origin = tuple(self.player.mover.position)
                self.walk(forward, right, buttons=0xC0)
                self.assertEqual(tuple(self.player.mover.position), origin)
                self.assertFalse(self.player.mover.crouched)
                self.step(0, 2)
                self.configure()
                self.walk(forward, right)
                self.assertGreater(math.dist(self.player.mover.position, origin), 1)

    def test_death_cancels_all_modes_and_rejects_further_attacks_and_skills(self):
        for mode in ("recon", "sentry", "tank", "repair"):
            with self.subTest(mode=mode):
                script = self.fixture()
                if mode == "sentry":
                    self.configure()
                elif mode == "tank":
                    self.tank()
                elif mode == "repair":
                    own = self.health(self.player)
                    own.damage(150)
                    self.fight._sync_body_health(self.player, own)
                    self.step(2, 30)
                self.fight._damage(self.enemy, self.target(self.player), 10000,
                                   False, None, self.run.frame)
                self.step(1 | 2 | 8 | 32, 30)
                self.assertTrue(self.health(self.player).dead)
                self.assertEqual(self.health(self.player).total, 0)
                self.assertEqual(self.health(self.enemy).total, 200)
                self.assertEqual(script.component.entity_var(1216).value(), 0)
                self.assertFalse(any(st.active for i in script.component.instances.values()
                                     for st in i.states.values()
                                     if st.node.cls.endswith(("WeaponVolley", "ModifyHealth"))))

    def test_death_during_each_transform_retires_the_transition(self):
        for mode in (1, 3, 4, 5, 7):
            with self.subTest(mode=mode):
                script = self.fixture(enemy_position=(10, 1, 0))
                if mode in (3, 5):
                    self.configure()
                if mode in (1, 3):
                    self.step(8)
                else:
                    script.set_ult_charge(script.component.entity_var(ULT_COST).value())
                    self.step(32)
                    if mode == 7:
                        self.step(0, 593)
                self.assertEqual(script.component.entity_var(1216).value(), mode)
                self.fight._damage(self.enemy, self.target(self.player), 10000,
                                   False, None, self.run.frame)
                self.step(1 | 2 | 8 | 32, 120)
                self.assertTrue(self.health(self.player).dead)
                self.assertEqual(script.component.entity_var(1216).value(), 0)
                self.assertFalse(self.fight.rockets)
                self.assertEqual(self.health(self.enemy).total, 200)
                self.assertFalse(any(st.active for i in script.component.instances.values()
                                     for st in i.states.values()
                                     if st.node.cls.endswith(("WeaponVolley", "ModifyHealth"))))

    def test_melee_cooldown_rejects_repeated_press_and_reuses_after_expiry(self):
        script = self.fixture(enemy_position=(0, 1, 1.4))
        ability = next(i for i in script.component.instances.values() if i.graph.index == 0x43).states[6]
        self.step(0x800)
        self.assertEqual(self.health(self.enemy).total, 170)
        self.assertGreater(ability.remaining(), 0.98)
        self.step()
        self.step(0x800)
        self.assertEqual(self.health(self.enemy).total, 170)
        self.assertGreater(ability.remaining(), 0.95)
        self.step(0, 65)  # the early press buffer also expires before the cooldown
        self.assertEqual(ability.remaining(), 0)
        self.assertEqual(self.health(self.enemy).total, 170)
        self.step(0x800)
        self.assertEqual(self.health(self.enemy).total, 140)

    def test_empty_clip_cannot_damage_then_automatic_reload_restores_fire(self):
        for sentry, clip, damage in ((False, 35, 40), (True, 300, 75)):
            with self.subTest(sentry=sentry):
                script = self.fixture()
                if sentry:
                    self.configure()
                weapon = next(i for i in script.component.instances.values()
                              if i.graph.index == (0x13a if sentry else 0x12e))
                for _ in range(800):
                    self.run.step({self.player: (16384, 0, 1)})  # expend actual rounds away from target
                    if weapon.vars[53].value() == 0:
                        break
                self.assertEqual(weapon.vars[53].value(), 0)
                self.step(1, 10)  # aim back at the live target while the clip is empty
                self.assertEqual(self.health(self.enemy).total, 200)
                self.assertEqual(weapon.vars[53].value(), 0)
                self.step(0, 200)
                self.assertEqual(weapon.vars[53].value(), clip)
                self.step(1, 10)
                self.assertEqual(200 - self.health(self.enemy).total, damage)

    def test_repair_start_delay_minimum_duration_and_fuel_recovery_reuse(self):
        script = self.fixture(enemy_position=(10, 1, 0))
        own = self.health(self.player)
        own.damage(150)
        self.fight._sync_body_health(self.player, own)
        initial = own.total
        self.step(2, 15)
        self.assertEqual(own.total, initial)
        self.step(2)
        self.assertAlmostEqual(own.total - initial, 1.44)
        repair = next(i for i in script.component.instances.values() if i.graph.index == 0x153)
        self.assertEqual(repair.vars[1357].value(), 0.5)  # authored minimum healing window
        self.step(0, 30)
        self.assertTrue(repair.states[0].active)  # early release cannot skip that window
        self.step(0, 3)
        self.assertFalse(repair.states[0].active)
        healed = own.total
        fuel = repair.vars[87].value()
        self.step(0, 200)
        self.assertEqual(own.total, healed)
        self.assertGreater(repair.vars[87].value(), fuel)
        self.assertEqual(repair.vars[87].value(), 100)
        self.step(2, 30)
        self.assertGreater(own.total, healed)


if __name__ == "__main__":
    unittest.main()
