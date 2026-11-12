"""Authored graph/state and actual input damage; native aura fidelity remains open."""

import math
import os
import unittest
from dataclasses import replace

import test_orisa_barrier as barrier_fixture
import test_orisa_combat as fixture
from test_game_combat import SOLDIER, aim, in_world

from ow174.game import bots, combat
from ow174.game.commands import FIRE
from ow174.game.content import find_hero


@unittest.skipUnless(os.environ.get("ORPHEUS_ORISA_SUPERCHARGER"), "external authored supplement required")
class SuperchargerTests(unittest.TestCase):
    def deploy(self):
        run, player, enemy = fixture.OrisaCombatTests().arena()
        player.body_script.ult_seconds = 0
        player.body_script.set_ult_charge(player.body_script.component.entity_var(338).value())
        manager = run.match.combat.orisa.superchargers
        for i in range(70):
            run.step({player: (0, 0, 32 if i == 0 else 0)})
            if manager.active:
                break
        self.assertEqual(len(manager.active), 1)
        return run, player, enemy, manager, next(iter(manager.active.values()))

    def test_actual_ultimate_command_initializes_original_graph_and_boost_status(self):
        run, player, enemy, manager, device = self.deploy()
        self.assertEqual(device.health, 200)
        self.assertEqual(device.until - run.frame, math.ceil(15 / combat.TICK))
        self.assertTrue(device.script.state(1).active)
        self.assertEqual(device.script.state(1).node.cls, "STUStatescriptStateTrackTargets")
        self.assertEqual(manager.scale(player), 1.5)
        self.assertEqual(manager.scale(enemy), 1)
        status = manager.buffs[(device.entity, player.body)]
        self.assertEqual(status.graph.index, 0xD01)
        self.assertEqual(status.vars[174].value(), 0.5)
        self.assertTrue(status.state(3).active)
        self.assertEqual(player.body_script.component.entity_var(342).value(), 0)

    def test_unfinished_supercharger_retires_on_owner_leave_or_switch(self):
        for phase in ("windup", "flight"):
            for retirement in ("leave", "switch"):
                with self.subTest(phase=phase, retirement=retirement):
                    run, owner, _ = fixture.OrisaCombatTests().arena()
                    fight = run.match.combat
                    manager = fight.orisa.superchargers
                    script = owner.body_script
                    script.ult_seconds = 0
                    script.set_ult_charge(script.component.entity_var(338).value())
                    run.step({owner: (0, 0, 32)})
                    self.assertEqual(script.component.entity_var(342).value(), 0)
                    ultimate = next(i for i in script.component.instances.values() if i.graph.index == 0xCF5)
                    pending = ultimate.state(ultimate.graph.node(30).state)
                    self.assertTrue(pending.active)
                    if phase == "flight":
                        for _ in range(60):
                            run.step({})
                            if any(rocket.source_graph == 0xCF5 for rocket in fight.rockets):
                                break
                        self.assertTrue(any(rocket.source_graph == 0xCF5 for rocket in fight.rockets))
                    else:
                        self.assertFalse(fight.rockets)
                    self.assertFalse(manager.active)

                    if retirement == "leave":
                        run.match.leave(owner)
                        run.players.remove(owner)
                    else:
                        self.assertTrue(run.match.switch_hero(owner, find_hero("Soldier")))
                    self.assertTrue(script.retired)
                    self.assertIsNot(owner.body_script, script)
                    retired_frame = script.frame
                    for _ in range(100):
                        self.assertFalse(fight.rockets)
                        self.assertFalse(manager.active)
                        self.assertFalse(manager.buffs)
                        run.step({})
                    self.assertEqual(script.frame, retired_frame)
                    self.assertFalse(manager.active)

    def test_owner_leave_preserves_allied_boost_until_device_expires(self):
        run, owner, target, manager, device = self.deploy()
        run.match.game_map = replace(run.match.game_map, free_for_all=False)
        ally = run.match.add_player(3, "Allied Orisa", SOLDIER, 2, False)
        in_world(run.match, ally)
        run.players.append(ally)
        run.match.switch_hero(ally, find_hero("Orisa"))
        ally.team = owner.team
        x, y, z = device.position
        barrier_fixture.BarrierTests.place(ally, (x + 3, y, z))
        barrier_fixture.BarrierTests.place(target, (x + 3, y, z + 5))
        run.step({})
        status = manager.buffs[(device.entity, ally.body)]
        body, deadline = device.source_body, device.until
        run.match.leave(owner)
        run.players.remove(owner)
        run.step({})
        self.assertTrue(owner.gone)
        self.assertIs(manager.active[device.entity], device)
        self.assertEqual((device.source_body, device.until), (body, deadline))
        self.assertIs(manager.buffs[(device.entity, ally.body)], status)
        self.assertNotIn((device.entity, body), manager.buffs)
        self.assertEqual(manager.scale(ally), 1.5)

        fight = run.match.combat
        health = fight.vitals_of(fixture.OrisaCombatTests().target(run, target))
        yaw, pitch = aim(fight._eye(ally), (x + 3, y + 0.8, z + 5))

        def fire():
            before = health.total
            run.step({ally: (yaw, pitch, FIRE)})
            for _ in range(10):
                run.step({})
            return before - health.total

        self.assertEqual(fire(), 16.5)
        while run.frame < deadline - 1:
            run.step({})
        self.assertIs(manager.active[device.entity], device)
        run.step({})
        self.assertNotIn(device.entity, manager.active)
        self.assertFalse(device.component.instances)
        self.assertTrue(status.stopped)
        self.assertEqual(manager.scale(ally), 1)
        self.assertEqual(fire(), 11)

    def test_overlapping_allied_devices_boost_once_and_expire_independently(self):
        run, player, ally, manager, first = self.deploy()
        run.match.game_map = replace(run.match.game_map, free_for_all=False)
        ally.team = player.team
        run.match.switch_hero(ally, find_hero("Orisa"))
        x, y, z = player.mover.position
        barrier_fixture.BarrierTests.place(ally, (x + 5, y, z))
        ally.body_script.ult_seconds = 0
        ally.body_script.set_ult_charge(ally.body_script.component.entity_var(338).value())
        for frame in range(90):
            run.step({ally: (0, 0, 32 if frame == 0 else 0)})
            if len(manager.active) == 2:
                break
        second, = (device for device in manager.active.values() if device is not first)
        self.assertEqual(second.source_body, ally.body)
        self.assertGreater(second.until, first.until + 12)

        fight = run.match.combat
        target = run.match.add_player(3, "Enemy target", SOLDIER, 2, False)
        in_world(run.match, target)
        run.players.append(target)
        run.match.switch_hero(target, find_hero("Soldier: 76"))
        target.team = player.team + 1
        barrier_fixture.BarrierTests.place(target, (x, y, z + 5))
        run.step({})
        vitals = fight.vitals_of(fixture.OrisaCombatTests().target(run, target))
        yaw, pitch = aim(fight._eye(player), (x, y + 0.8, z + 5))

        def shot_damage():
            health = vitals.total
            run.step({player: (yaw, pitch, FIRE)})
            for _ in range(12):
                run.step({})
            return health - vitals.total

        def charges():
            return tuple(actor.body_script.component.entity_var(342).value() for actor in (player, ally))

        charge_samples = []
        self.assertIn((first.entity, player.body), manager.buffs)
        self.assertIn((second.entity, player.body), manager.buffs)
        self.assertEqual(shot_damage(), 16.5)
        charge_samples.append(charges())
        while run.frame < first.until:
            run.step({})
        self.assertNotIn(first.entity, manager.active)
        self.assertNotIn((first.entity, player.body), manager.buffs)
        self.assertIn((second.entity, player.body), manager.buffs)
        self.assertEqual(shot_damage(), 16.5)
        charge_samples.append(charges())
        while run.frame < second.until:
            run.step({})
        self.assertFalse(manager.active)
        self.assertFalse(manager.buffs)
        self.assertEqual(shot_damage(), 11)
        charge_samples.append(charges())
        with self.subTest(check="charge attribution"):
            if fight.orisa.charge_metadata is None:
                self.skipTest("external charge metadata required")
            self.assertEqual(charge_samples, [(0, 0), (16.5, 0), (27.5, 0)])

    @unittest.skipUnless(os.environ.get("ORPHEUS_ORISA_CHARGE"), "authored charge metadata required")
    def test_allied_boosted_shot_charges_only_actual_fortify_and_armour_loss(self):
        run, owner, shooter, manager, _ = self.deploy()
        run.match.game_map = replace(run.match.game_map, free_for_all=False)
        shooter.team = owner.team
        run.match.switch_hero(shooter, find_hero("Orisa"))
        shooter.body_script.ult_seconds = 0
        x, y, z = owner.mover.position
        barrier_fixture.BarrierTests.place(shooter, (x + 5, y, z))
        enemy = run.match.add_player(3, "Enemy Orisa", SOLDIER, 2, False)
        in_world(run.match, enemy)
        run.players.append(enemy)
        run.match.switch_hero(enemy, find_hero("Orisa"))
        enemy.team = owner.team + 1
        barrier_fixture.BarrierTests.place(enemy, (x + 5, y, z + 5))

        fight = run.match.combat
        target = fixture.OrisaCombatTests().target(run, enemy)
        vitals = fight.vitals_of(target)
        yaw, pitch = aim(fight._eye(shooter), (x + 5, y + 0.8, z + 5))

        def check_hit(expected_loss):
            meter = shooter.body_script.component.entity_var(342)
            before = meter.value()
            health = vitals.total
            self.assertEqual(manager.scale(shooter), 1.5)
            run.step({shooter: (yaw, pitch, FIRE)})
            for _ in range(12):
                run.step({})
            self.assertAlmostEqual(health - vitals.total, expected_loss, places=4)
            self.assertAlmostEqual(meter.value() - before, health - vitals.total, places=4)

        run.step({enemy: (0, 0, 8)})
        self.assertAlmostEqual(fight._fortify_reduction(target), 0.6)
        check_hit(4.95)
        for _ in range(260):
            if fight._fortify_reduction(target) is None:
                break
            run.step({})
        self.assertIsNone(fight._fortify_reduction(target))
        check_hit(11.5)

        meter = shooter.body_script.component.entity_var(342)
        for _ in range(500):
            before = meter.value()
            remaining_health = vitals.total
            run.step({shooter: (yaw, pitch, FIRE)})
            self.assertEqual(manager.scale(shooter), 1.5)
            if vitals.dead:
                break
        self.assertTrue(vitals.dead)
        self.assertGreater(remaining_health, 0)
        self.assertLess(remaining_health, 16.5)
        self.assertAlmostEqual(meter.value() - before, remaining_health, places=3)

        charge_at_death = meter.value()
        for _ in range(30):
            run.step({shooter: (yaw, pitch, FIRE)})
        self.assertTrue(fight.is_dead(enemy))
        self.assertEqual(manager.scale(shooter), 1.5)
        self.assertEqual(meter.value(), charge_at_death)

    @unittest.skipUnless(os.environ.get("ORPHEUS_ORISA_CHARGE"), "authored charge metadata required")
    @unittest.skipUnless(os.environ.get("ORPHEUS_ORISA_CHARGE"), "authored charge tags required")
    def test_allied_boosted_quick_melee_earns_charge_only_after_deflect_ends(self):
        run, owner, attacker, manager, device = self.deploy()
        run.match.game_map = replace(run.match.game_map, free_for_all=False)
        self.assertTrue(run.match.switch_hero(attacker, find_hero("Orisa")))
        attacker.team = owner.team
        attacker.body_script.ult_seconds = 0
        genji = run.match.add_player(3, "Enemy Genji", SOLDIER, 2, False)
        in_world(run.match, genji)
        run.players.append(genji)
        self.assertTrue(run.match.switch_hero(genji, find_hero("Genji")))
        genji.team = owner.team + 1
        x, y, z = device.position
        barrier_fixture.BarrierTests.place(attacker, (x + 3, y, z))
        barrier_fixture.BarrierTests.place(genji, (x + 3, y, z + 1.5))
        fight = run.match.combat
        health = fight.vitals_of(fixture.OrisaCombatTests().target(run, genji))
        before = health.total
        meter = attacker.body_script.component.entity_var(342)
        charge = meter.value()
        facing = (-32768, 0, 0)
        run.step({genji: (-32768, 0, 16)})
        instance, = (i for i in genji.body_script.component.instances.values()
                     if i.graph.index == 0x03A3)
        filter_state = instance.states[instance.graph.node(107).state]
        self.assertTrue(filter_state.active)
        self.assertEqual(manager.scale(attacker), 1.5)

        run.step({genji: facing, attacker: (0, 0, 0x800)})
        for _ in range(25):
            run.step({genji: facing})
        self.assertTrue(filter_state.active)
        self.assertEqual((health.total, meter.value()), (before, charge))
        self.assertFalse(attacker.client.messages(combat.HIT))
        for _ in range(135):
            run.step({genji: facing})
            if not filter_state.active:
                break
        self.assertFalse(filter_state.active)
        self.assertEqual((health.total, meter.value()), (before, charge))
        self.assertEqual(manager.scale(attacker), 1.5)

        run.step({genji: facing, attacker: (0, 0, 0x800)})
        for _ in range(25):
            run.step({genji: facing})
        self.assertEqual(manager.scale(attacker), 1.5)
        self.assertEqual(before - health.total, 45)
        self.assertEqual(meter.value() - charge, 45)
        self.assertEqual(len(attacker.client.messages(combat.HIT)), 1)

    def test_boosted_quick_melee_credits_post_fortify_armour_loss(self):
        run, owner, attacker, manager, device = self.deploy()
        run.match.game_map = replace(run.match.game_map, free_for_all=False)
        run.match.switch_hero(attacker, find_hero("Orisa"))
        attacker.team = owner.team
        attacker.body_script.ult_seconds = 0
        victim = run.match.add_player(3, "Enemy Orisa", SOLDIER, 2, False)
        in_world(run.match, victim)
        run.players.append(victim)
        run.match.switch_hero(victim, find_hero("Orisa"))
        victim.team = owner.team + 1
        x, y, z = device.position
        barrier_fixture.BarrierTests.place(attacker, (x + 3, y, z))
        barrier_fixture.BarrierTests.place(victim, (x + 3, y, z + 1.5))
        fight = run.match.combat
        target = fixture.OrisaCombatTests().target(run, victim)
        health = fight.vitals_of(target)
        meter = attacker.body_script.component.entity_var(342)

        def melee(expected):
            before, charge = health.total, meter.value()
            self.assertEqual(manager.scale(attacker), 1.5)
            run.step({attacker: (0, 0, 0x800)})
            for _ in range(25):
                run.step({})
            self.assertAlmostEqual(before - health.total, expected, places=4)
            self.assertAlmostEqual(meter.value() - charge, expected, places=4)

        run.step({victim: (0, 0, 8)})
        self.assertAlmostEqual(fight._fortify_reduction(target), 0.6)
        melee(22)
        for _ in range(260):
            if fight._fortify_reduction(target) is None:
                break
            run.step({})
        self.assertIsNone(fight._fortify_reduction(target))
        melee(40)

    def test_actual_fusion_driver_damage_is_boosted_once(self):
        run, player, enemy, _, _ = self.deploy()
        vitals = run.match.combat.vitals_of(fixture.OrisaCombatTests().target(run, enemy))
        run.step({player: (0, 0, FIRE)})
        for _ in range(8):
            run.step({})
        self.assertEqual(vitals.total, 167)  # 11*2*1.5, not 22+status display only

    def test_ally_gains_status_but_enemy_and_ffa_peers_do_not(self):
        run, player, enemy, manager, _ = self.deploy()
        enemy.team = player.team
        run.step({})
        self.assertEqual(manager.scale(enemy), 1)  # FFA: same team number is insufficient
        run.match.game_map = replace(run.match.game_map, free_for_all=False)
        run.step({})
        self.assertEqual(manager.scale(enemy), 1.5)
        enemy.team += 1
        run.step({})
        self.assertEqual(manager.scale(enemy), 1)

    def test_out_of_range_removes_status_and_return_reacquires_it(self):
        run, player, _, manager, device = self.deploy()
        x, y, z = device.position
        barrier_fixture.BarrierTests.place(player, (x, y, z - 27))
        run.step({})
        self.assertEqual(manager.scale(player), 1)
        self.assertNotIn((device.entity, player.body), manager.buffs)
        barrier_fixture.BarrierTests.place(player, (x, y, z - 2))
        run.step({})
        self.assertEqual(manager.scale(player), 1.5)

    def test_fusion_driver_uses_aura_at_impact_after_midflight_los_change(self):
        for gains_boost in (False, True):
            with self.subTest(gains_boost=gains_boost):
                run, shooter, enemy, manager, _ = self.deploy()
                fight = run.match.combat
                x, y, z = shooter.mover.position
                barrier_fixture.BarrierTests.place(enemy, (x, y, z - 18))
                clear_world = run.match.collision_world
                if gains_boost:
                    fixture.OrisaCombatTests().wall(run, shooter, 0.6)
                run.step({})
                self.assertEqual(manager.scale(shooter), 1 if gains_boost else 1.5)
                target = fixture.OrisaCombatTests().target(run, enemy)
                vitals = fight.vitals_of(target)
                health = vitals.total
                yaw, pitch = aim(fight._eye(shooter), (x, y + 0.8, z - 18))
                run.step({shooter: (yaw, pitch, FIRE)})
                rocket, = (r for r in fight.rockets if r.source_graph == 0xBD4)
                self.assertEqual(vitals.total, health)

                # The wall is behind the shooter relative to the projectile.
                if gains_boost:
                    run.match.collision_world = clear_world
                else:
                    fixture.OrisaCombatTests().wall(run, shooter, 0.6)
                run.step({})
                self.assertIn(rocket, fight.rockets)
                self.assertEqual(manager.scale(shooter), 1.5 if gains_boost else 1)
                for _ in range(40):
                    run.step({})
                    if rocket not in fight.rockets:
                        break
                self.assertNotIn(rocket, fight.rockets)
                self.assertEqual(health - vitals.total, 16.5 if gains_boost else 11)

    def test_static_wall_removes_boost(self):
        run, player, _, manager, device = self.deploy()
        self.assertGreater(device.position[2], player.mover.position[2])
        fixture.OrisaCombatTests().wall(run, player, 0.6)
        run.step({})
        self.assertEqual(manager.scale(player), 1)

    @unittest.skipUnless(os.environ.get("ORPHEUS_ORISA_GEOMETRY"), "actual barrier mesh required")
    def test_protective_barrier_occludes_aura_only_when_hostile(self):
        run, player, opponent, manager, device = self.deploy()
        run.match.game_map = replace(run.match.game_map, free_for_all=False)
        run.match.switch_hero(opponent, find_hero("Orisa"))
        x, y, z = device.position
        barrier_fixture.BarrierTests.place(opponent, (x, y, z - 3))
        barriers = run.match.combat.orisa.barriers
        for frame in range(120):
            run.step({opponent: (0, 0, 16 if frame == 0 else 0)})
            if barriers.active:
                break
        shield, = barriers.active.values()
        self.assertEqual(shield.source_body, opponent.body)
        sx, sy, sz = shield.position
        self.assertGreater(sz, z)
        barrier_fixture.BarrierTests.place(player, (sx, sy, sz + 3))
        fight = run.match.combat
        target = bots.Bot(fight.next_bot, {
            "hero": "0x02E000000000016B", "team": 2, "position": (sx, sy, sz + 8),
            "rotation": (0, 0, 0, 1), "graphs": [],
        }, {"name": "Enemy target", "body": "0x0400000000001403", "health": 200}, {})
        fight.next_bot += 1
        run.match.bots.append(target)
        vitals = fight.vitals_of(fixture.OrisaCombatTests().target(run, target))

        def shot_damage():
            health = vitals.total
            yaw, pitch = aim(fight._eye(player), (sx, sy + 0.8, sz + 8))
            run.step({player: (yaw, pitch, FIRE)})
            for _ in range(12):
                run.step({})
            return health - vitals.total

        run.step({})
        self.assertEqual(manager.scale(player), 1)
        self.assertNotIn((device.entity, player.body), manager.buffs)
        self.assertEqual(shot_damage(), 11)

        hostile_team = player.team
        player.team = shield.team
        run.step({})
        self.assertEqual(manager.scale(player), 1.5)
        self.assertEqual(shot_damage(), 16.5)
        player.team = hostile_team
        run.step({})
        self.assertEqual(manager.scale(player), 1)
        self.assertEqual(shot_damage(), 11)
        self.assertIn(shield.entity, barriers.active)
        self.assertEqual(shield.health, 600)

    def test_enemy_mesh_hits_deplete_real200_health_and_remove_boost(self):
        run, player, enemy, manager, device = self.deploy()
        x, y, z = device.position
        barrier_fixture.BarrierTests.place(enemy, (x, y, z + 5))
        yaw, pitch = aim(run.match.combat._eye(enemy), (x, y + 0.4, z))
        rounds = 0
        for _ in range(180):
            previous = device.health
            run.step({enemy: (yaw, pitch, FIRE)})
            if device.health < previous:
                rounds += 1
                self.assertEqual(previous - device.health, 20)
            if device.entity not in manager.active:
                break
        self.assertEqual(rounds, 10)
        self.assertEqual(device.health, 0)
        self.assertFalse(manager.buffs)
        self.assertEqual(manager.scale(player), 1)

    def test_deadline_clears_status_and_original_graphs_without_refresh(self):
        _, player, _, manager, device = self.deploy()
        manager.update(device.until - 1)
        self.assertEqual(manager.scale(player), 1.5)
        manager.update(device.until)
        self.assertFalse(manager.active)
        self.assertFalse(manager.buffs)
        self.assertFalse(device.component.instances)
        self.assertEqual(manager.scale(player), 1)

    def test_target_death_and_body_switch_remove_status(self):
        run, player, _, manager, device = self.deploy()
        fight = run.match.combat
        enemy = run.players[1]
        for _ in range(250):
            run.step({enemy: (32768, 0, FIRE)})
            if fight.is_dead(player):
                break
        self.assertTrue(fight.is_dead(player))
        run.step({})
        self.assertEqual(manager.scale(player), 1)
        old_body = player.body
        run.match.switch_hero(player, find_hero("Soldier: 76"))
        self.assertTrue(all(key[1] != old_body for key in manager.buffs))
        self.assertIn(device.entity, manager.active)  # deployed-owner persistence remains a policy gap

    def test_allied_soldier_real_body_shot_is_boosted_against_enemy_bot(self):
        run, player, ally, manager, _ = self.deploy()
        run.match.game_map = replace(run.match.game_map, free_for_all=False)
        ally.team = player.team
        fight = run.match.combat
        x, y, z = ally.mover.position
        bot = bots.Bot(fight.next_bot, {
            "hero": "0x02E000000000016B", "team": 1, "position": (x, y, z + 5),
            "rotation": (0, 0, 0, 1), "graphs": [],
        }, {"name": "Enemy target", "body": "0x0400000000001403", "health": 200}, {})
        fight.next_bot += 1
        run.match.bots.append(bot)
        run.step({})
        self.assertEqual(manager.scale(ally), 1.5)
        yaw, pitch = aim(fight._eye(ally), (x, y + 0.8, z + 5))
        vitals = fight.vitals_of(fixture.OrisaCombatTests().target(run, bot))
        run.step({ally: (yaw, pitch, FIRE)})
        self.assertEqual(vitals.total, 170)


if __name__ == "__main__":
    unittest.main()
