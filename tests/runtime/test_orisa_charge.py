"""Original0B8B event/timer formulas on real combat; native bridge fidelity stays open."""

import os
import unittest
from dataclasses import replace
from unittest.mock import patch

import test_orisa_barrier as barrier_fixture
import test_orisa_combat as fixture
import test_orisa_npc as npc_fixture
import test_orisa_supercharger as supercharger_fixture
from test_game_combat import aim

from ow174.game import combat, orisa_charge
from ow174.game.commands import FIRE
from ow174.game.content import find_hero


@unittest.skipUnless(os.environ.get("ORPHEUS_ORISA_CHARGE"), "explicit original-body tag metadata required")
class OrisaChargeTests(unittest.TestCase):
    def arena(self):
        run, player, enemy = fixture.OrisaCombatTests().arena()
        player.body_script.ult_seconds = 0
        self.assertEqual(player.body_script.component.entity_var(342).value(), 0)
        return run, player, enemy

    @staticmethod
    def charge(player):
        return player.body_script.component.entity_var(342).value()

    def shoot(self, run, player, *, yaw=0, pitch=0):
        run.step({player: (yaw, pitch, FIRE)})
        for _ in range(8):
            run.step({})

    def test_original_passive_timer_adds_five_per_second_and_manual_setting_stops_it(self):
        run, player, _ = self.arena()
        for _ in range(130):
            run.step({})
        self.assertEqual(self.charge(player), 0)
        player.body_script.ult_seconds = 60
        for _ in range(65):
            run.step({})
        self.assertEqual(self.charge(player), 5)
        for _ in range(63):
            run.step({})
        self.assertEqual(self.charge(player), 10)

    def test_actual_critical_hit_adds_actual_twenty_two_damage(self):
        run, player, _ = self.arena()
        self.shoot(run, player)
        self.assertEqual(self.charge(player), 22)

    def test_scatter_can_reverse_center_ray_hit_and_charge_outcomes(self):
        for hit in (False, True):
            with self.subTest(hit=hit):
                run, player, enemy = self.arena()
                fight = run.match.combat
                origin = fight._eye(player)
                _, pitch = aim(origin, (origin[0], origin[1] - .75, origin[2] + 10))
                run.step({player: (0, pitch, FIRE)})
                rocket, = fight.rockets
                direction = rocket.launch_direction
                target = fixture.OrisaCombatTests().target(run, enemy)
                offset = direction[0] * 10 / direction[2]
                self.assertGreater(abs(offset), 1e-3)
                side = 1 if offset > 0 else -1
                x = origin[0] + offset / 2 + side * target.radius * (1 if hit else -1)
                barrier_fixture.BarrierTests.place(enemy, (x, target.feet[1], origin[2] + 10))
                target = fixture.OrisaCombatTests().target(run, enemy)
                a, b = target.segment()
                self.assertEqual(combat.ray_capsule(origin, combat.aim(0, pitch), a, b,
                                                    target.radius) is not None, not hit)
                self.assertEqual(combat.ray_capsule(origin, direction, a, b, target.radius) is not None, hit)
                vitals = fight.vitals_of(target)
                before = vitals.total
                # First flight reaches 10 m before a following round can arrive.
                for _ in range(7):
                    run.step({})
                self.assertEqual(before - vitals.total, 11 if hit else 0)
                self.assertEqual(self.charge(player), 11 if hit else 0)

    def test_same_hero_replacement_preserves_earned_charge_before_initial_snapshot(self):
        run, player, _ = self.arena()
        self.shoot(run, player)
        old = player.body
        self.assertTrue(run.match.switch_hero(player, find_hero("Orisa")))
        self.assertNotEqual(player.body, old)
        self.assertAlmostEqual(self.charge(player), 22, places=4)
        self.assertAlmostEqual(player.body_script.snapshot().vars[(0, 342)][0], 22, places=4)

    def test_actual_death_and_scheduled_respawn_preserve_earned_charge(self):
        run, player, enemy = self.arena()
        self.shoot(run, player)
        old = player.body
        for _ in range(1000):
            run.step({enemy: (32768, 0, FIRE)})
            if run.match.combat.is_dead(player):
                break
        self.assertTrue(run.match.combat.is_dead(player))
        self.assertEqual(self.charge(player), 22)
        for _ in range(1000):
            run.step({})
            if player.body != old:
                break
        self.assertNotEqual(player.body, old)
        self.assertFalse(run.match.combat.is_dead(player))
        self.assertAlmostEqual(self.charge(player), 22, places=4)

    def test_different_hero_and_return_do_not_recover_old_charge(self):
        run, player, _ = self.arena()
        self.shoot(run, player)
        self.assertTrue(run.match.switch_hero(player, find_hero("Soldier: 76")))
        self.assertTrue(run.match.switch_hero(player, find_hero("Orisa")))
        self.assertEqual(self.charge(player), 0)

    def test_original_restore_message_clamps_and_respects_authored_entity_gate(self):
        _, player, _ = self.arena()
        script = player.body_script
        orisa_charge.restore(script, 2, player.entity)
        self.assertEqual(self.charge(player), 1680)
        # Exercise the actual graph's #32365 refusal, rather than bypassing it
        # with a direct assignment of charge#342 during replacement.
        script.component.set(32365, True)
        orisa_charge.restore(script, 0.25, player.entity)
        self.assertEqual(self.charge(player), 1680)

    def test_original_restore_respects_authored_ruleset_gate(self):
        _, player, _ = self.arena()
        script = player.body_script
        original = script.world.ruleset
        with patch.object(script.world, "ruleset", side_effect=lambda k: True if k == 13024 else original(k)):
            orisa_charge.restore(script, 0.5, player.entity)
        self.assertEqual(self.charge(player), 0)
        orisa_charge.restore(script, 0.5, player.entity)
        self.assertEqual(self.charge(player), 840)

    def test_miss_static_cover_and_team_ally_do_not_grant_charge(self):
        for case in ("miss", "wall", "ally"):
            with self.subTest(case=case):
                run, player, enemy = self.arena()
                if case == "wall":
                    fixture.OrisaCombatTests().wall(run, player, 2)
                elif case == "ally":
                    run.match.game_map = replace(run.match.game_map, free_for_all=False)
                    enemy.team = player.team
                self.shoot(run, player, yaw=16384 if case == "miss" else 0)
                self.assertEqual(self.charge(player), 0)

    def test_hero_armour_and_fortify_grant_only_actual_pool_loss(self):
        for fortify in (False, True):
            with self.subTest(fortify=fortify):
                run, player, enemy = self.arena()
                run.match.switch_hero(enemy, find_hero("Orisa"))
                x, y, z = player.mover.position
                barrier_fixture.BarrierTests.place(enemy, (x, y, z + 5))
                run.step({enemy: (32768, 0, 8 if fortify else 0)})
                fight = run.match.combat
                vitals = fight.vitals_of(fixture.OrisaCombatTests().target(run, enemy))
                before = vitals.total
                self.shoot(run, player)
                loss = before - vitals.total
                self.assertGreater(loss, 0)
                self.assertAlmostEqual(self.charge(player), loss, places=4)
                self.assertAlmostEqual(loss, 3.3 if fortify else 17, places=4)

    def test_melee_damage_grants_authored_charge(self):
        run, player, enemy = self.arena()
        x, y, z = player.mover.position
        barrier_fixture.BarrierTests.place(enemy, (x, y, z + 1.5))
        run.step({player: (0, 0, 0x800)})
        for _ in range(25):
            run.step({})
        self.assertEqual(self.charge(player), 30)

    def test_overkill_and_dead_body_do_not_grant_extra_charge(self):
        run, player, enemy = self.arena()
        for _ in range(180):
            run.step({player: (0, 0, FIRE)})
            if run.match.combat.is_dead(enemy):
                break
        self.assertTrue(run.match.combat.is_dead(enemy))
        self.assertEqual(self.charge(player), 200)
        for _ in range(40):
            run.step({player: (0, 0, FIRE)})
        self.assertEqual(self.charge(player), 200)

    @unittest.skipUnless(os.environ.get("ORPHEUS_ORISA_GEOMETRY"), "real barrier mesh required")
    def test_enemy_barrier_damage_does_not_grant_hero_charge(self):
        run, _, enemy, _, shield = barrier_fixture.BarrierTests().deploy()
        run.match.switch_hero(enemy, find_hero("Orisa"))
        enemy.body_script.ult_seconds = 0
        x, y, z = shield.position
        barrier_fixture.BarrierTests.place(enemy, (x, y, z + 5))
        self.shoot(run, enemy, yaw=32768)
        self.assertEqual(shield.health, 589)
        self.assertEqual(self.charge(enemy), 0)

    @unittest.skipUnless(os.environ.get("ORPHEUS_ORISA_SUPERCHARGER"), "authored device required")
    def test_deployed_callback_owns_charge_block_and_destroy_releases_it(self):
        run, player, enemy, manager, device = supercharger_fixture.SuperchargerTests().deploy()
        script = player.body_script
        self.assertTrue(script.component.entity_var(842).value())
        self.shoot(run, player)
        self.assertEqual(self.charge(player), 0)
        # Destruction follows actual enemy weapon inputs, not a direct deadline edit.
        x, y, z = device.position
        barrier_fixture.BarrierTests.place(enemy, (x, y, z + 5))
        yaw, pitch = aim(run.match.combat._eye(enemy), (x, y + 0.4, z))
        for _ in range(180):
            run.step({enemy: (yaw, pitch, FIRE)})
            if not manager.active:
                break
        self.assertFalse(manager.active)
        self.assertFalse(script.component.entity_var(842).value())
        before = self.charge(player)
        self.shoot(run, player)
        self.assertGreater(self.charge(player), before)

    @unittest.skipUnless(os.environ.get("ORPHEUS_ORISA_SUPERCHARGER"), "authored device required")
    def test_passive_charge_resumes_after_supercharger_destruction_or_expiry(self):
        for destruction in (True, False):
            with self.subTest(destruction=destruction):
                run, player, enemy = fixture.OrisaCombatTests().arena()
                script = player.body_script
                script.ult_seconds = 60
                script.set_ult_charge(script.component.entity_var(338).value())
                fight = run.match.combat
                manager = fight.orisa.superchargers
                for frame in range(70):
                    run.step({player: (0, 0, 32 if frame == 0 else 0)})
                    if manager.active:
                        break
                device, = manager.active.values()
                for _ in range(130):
                    run.step({})
                    self.assertEqual(self.charge(player), 0)
                self.assertTrue(script.component.entity_var(842).value())

                if destruction:
                    x, y, z = device.position
                    barrier_fixture.BarrierTests.place(enemy, (x, y, z + 5))
                    yaw, pitch = aim(fight._eye(enemy), (x, y + 0.4, z))
                    for _ in range(180):
                        run.step({enemy: (yaw, pitch, FIRE)})
                        if not manager.active:
                            break
                    self.assertEqual(device.health, 0)
                    self.assertLess(run.frame, device.until)
                else:
                    while run.frame < device.until:
                        self.assertEqual(self.charge(player), 0)
                        run.step({})
                    self.assertEqual(device.health, 200)
                self.assertFalse(manager.active)
                self.assertFalse(script.component.entity_var(842).value())

                for _ in range(70):
                    if self.charge(player) > 0:
                        break
                    run.step({})
                self.assertEqual(self.charge(player), 5)
                first = run.frame
                for _ in range(70):
                    if self.charge(player) > 5:
                        break
                    run.step({})
                self.assertEqual(self.charge(player), 10)
                self.assertIn(run.frame - first, (62, 63))

    @unittest.skipUnless(os.environ.get("ORPHEUS_ORISA_SUPERCHARGER"), "authored device required")
    def test_retained_device_rebinds_after_reselect_hero_return_or_respawn(self):
        for lifecycle in ("reselect", "hero_return", "respawn"):
            with self.subTest(lifecycle=lifecycle):
                run, player, enemy, manager, device = supercharger_fixture.SuperchargerTests().deploy()
                source = device.source_body
                deadline = device.until
                if lifecycle == "hero_return":
                    run.match.switch_hero(player, find_hero("Soldier: 76"))
                if lifecycle == "respawn":
                    for _ in range(1000):
                        run.step({enemy: (32768, 0, FIRE)})
                        if run.match.combat.is_dead(player):
                            break
                    self.assertTrue(run.match.combat.is_dead(player))
                    for _ in range(1000):
                        run.step({})
                        if player.body != source:
                            break
                else:
                    run.match.switch_hero(player, find_hero("Orisa"))
                player.body_script.ult_seconds = 0
                self.assertNotEqual(player.body, source)
                self.assertEqual(device.source_body, source)
                self.assertEqual(device.until, deadline)
                self.assertIs(manager.active[device.entity], device)
                self.assertTrue(player.body_script.component.entity_var(842).value())
                x, y, z = enemy.mover.position
                barrier_fixture.BarrierTests.place(player, (x, y, z - 5))
                before = run.match.combat.vitals_of(fixture.OrisaCombatTests().target(run, enemy)).total
                self.shoot(run, player)
                self.assertLess(
                    run.match.combat.vitals_of(fixture.OrisaCombatTests().target(run, enemy)).total, before)
                self.assertEqual(self.charge(player), 0)
                # Actual enemy fire retires the same retained device; the new
                # body's original existence switch must release immediately.
                x, y, z = device.position
                barrier_fixture.BarrierTests.place(enemy, (x, y, z + 5))
                yaw, pitch = aim(run.match.combat._eye(enemy), (x, y + 0.4, z))
                for _ in range(180):
                    run.step({enemy: (yaw, pitch, FIRE)})
                    if not manager.active:
                        break
                self.assertFalse(manager.active)
                self.assertFalse(player.body_script.component.entity_var(842).value())
                self.shoot(run, player)
                self.assertGreater(self.charge(player), 0)


@unittest.skipUnless(os.environ.get("ORPHEUS_ORISA_CHARGE") and os.environ.get("ORPHEUS_NPC_RUNTIME"),
                     "explicit tag metadata and actual Talon package required")
class OrisaNpcChargeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        npc_fixture.OrisaTalonTests.setUpClass()
        cls.catalog = npc_fixture.OrisaTalonTests.catalog

    @classmethod
    def tearDownClass(cls):
        npc_fixture.OrisaTalonTests.tearDownClass()

    def test_actual_five_talon_damage_enters_original_charge_graph(self):
        for definition in self.catalog.values():
            with self.subTest(npc=definition.name):
                run, player, bot = npc_fixture.OrisaTalonTests().arena(definition)
                player.body_script.ult_seconds = 0
                x, y, z = bot.position
                yaw, pitch = aim(run.match.combat._eye(player), (x, y + 1, z))
                run.step({player: (yaw, pitch, FIRE)})
                for _ in range(8):
                    run.step({})
                self.assertEqual(player.body_script.component.entity_var(342).value(), 11)

    @unittest.skipUnless(os.environ.get("ORPHEUS_ORISA_SUPERCHARGER"), "authored device required")
    def test_full_real_charge_from_unmodified_heavy_health_can_cast_ultimate(self):
        definition = next(d for d in self.catalog.values() if d.name == "Heavy Assault")
        run, player, bot = npc_fixture.OrisaTalonTests().arena(definition)
        script = player.body_script
        script.ult_seconds = 0
        self.assertEqual(run.match.combat.vitals[bot.entity].total, 4000)
        self.assertEqual(script.component.entity_var(342).value(), 0)
        cost = script.component.entity_var(338).value()
        self.assertEqual(cost, 1680)
        x, y, z = bot.position
        yaw, pitch = aim(run.match.combat._eye(player), (x, y + 1, z))
        for _ in range(2500):
            run.step({player: (yaw, pitch, FIRE)})
            if script.component.entity_var(342).value() == cost:
                break
        self.assertEqual(script.component.entity_var(342).value(), cost)
        self.assertFalse(bot.dead)
        self.assertGreater(run.match.combat.vitals[bot.entity].total, 2000)
        rifle = next(i for i in script.component.instances.values() if i.graph.index == 0xBD4)
        self.assertGreater(rifle.state(19).counter, 0)  # authored automatic reload was necessary
        for i in range(70):
            run.step({player: (yaw, pitch, 32 if i == 0 else 0)})
            if run.match.combat.orisa.superchargers.active:
                break
        self.assertTrue(run.match.combat.orisa.superchargers.active)
        self.assertEqual(script.component.entity_var(342).value(), 0)
        self.assertTrue(script.component.entity_var(842).value())
