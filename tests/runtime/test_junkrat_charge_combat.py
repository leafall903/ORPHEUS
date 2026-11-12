"""Actual Match charge/lifecycle with original configs and disclosed server services."""

import os
import unittest
from dataclasses import replace
from unittest.mock import patch

import test_junkrat_npc_combat as npc_helpers
import test_junkrat_projectile_combat as helpers
from test_game_combat import aim

from ow174.game import combat, immunity, npc
from ow174.game.commands import FIRE, Command
from ow174.game.content import find_hero


class JunkratChargeCombatTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        for key in ('ORPHEUS_CHARGE_ENTITY_INPUTS', 'ORPHEUS_PVE_NPC_DATA',
                    'ORPHEUS_JUNKRAT_TIRE_PROFILE'):
            if key not in os.environ:
                raise unittest.SkipTest(key + ' not configured')
        npc_helpers.JunkratNpcCombatTests.setUpClass()
        cls.catalog = npc_helpers.JunkratNpcCombatTests.catalog

    @classmethod
    def tearDownClass(cls):
        npc_helpers.JunkratNpcCombatTests.tearDownClass()

    def fight(self, passive=False):
        run, player, enemy = helpers.JunkratProjectileTests().setup_fight()
        self.assertIn(player.body, run.match.combat.junkrat.charge.bindings)
        if not passive:
            player.body_script.component.set(11942, True)  # authored passive-only suppression
        return run, player, enemy

    def points(self, player):
        return player.body_script.component.entity_var(342).value()

    def shoot(self, run, player, frames=45):
        run.step({player: (0, 0, FIRE)})
        for _ in range(frames):
            run.step({})

    def kill_with_enemy_primary(self, run, player, enemy):
        script = player.body_script
        yaw, pitch = aim(run.match.combat._eye(enemy), run.match.combat._eye(player))
        for _ in range(100):
            run.step({enemy: (yaw, pitch, FIRE)})
            if script.component.owner_dead:
                break
        self.assertTrue(script.component.owner_dead)

    def heavy(self):
        definition = next(d for d in self.catalog.values() if d.name == 'Heavy Assault')
        run, player, bot, vitals = npc_helpers.JunkratNpcCombatTests().create_fight(definition)
        return run, player, bot, vitals

    def test_world_passive_survives_input_silence_without_owner_frame_or_double_catchup(self):
        run, player, _enemy = self.fight(passive=True)
        script = player.body_script
        initial_frame = script.frame
        for _ in range(160):
            run.frame += 1
            run.match.update(run.frame * combat.TICK, run.frame, frames=False)
        self.assertEqual(script.frame, initial_frame)
        self.assertEqual(self.points(player), 15)
        run.match.update(run.frame * combat.TICK, run.frame, frames=False)
        self.assertEqual(self.points(player), 15)
        run.step({})  # the actual late input replays owner timers,not passive awards
        self.assertEqual(self.points(player), 15)
        self.assertEqual(script.frame, run.frame)

    def test_actual_primary_and_melee_award_actual_pool_loss_including_overkill_cap(self):
        run, player, enemy = self.fight()
        self.shoot(run, player)
        self.assertEqual(self.points(player), 120)
        self.shoot(run, player)
        self.assertEqual(helpers.JunkratProjectileTests().health(run, enemy).total, 0)
        self.assertEqual(self.points(player), 200)  # second blast cannot award beyond remaining80
        run, player, _enemy = self.fight()
        enemy = run.players[1]
        x, y, z = player.mover.position
        helpers.JunkratProjectileTests().place(run, enemy, (x, y, z + 1.5))
        run.step({player: (0, 0, 0x800)})
        for _ in range(20):
            run.step({})
        self.assertEqual(self.points(player), 30)

    def test_actual_player_miss_wall_and_friendly_damage_do_not_award_charge(self):
        for scene in ('miss', 'wall', 'friendly'):
            with self.subTest(scene=scene):
                run, player, enemy = self.fight()
                if scene == 'miss':
                    x, y, z = enemy.mover.position
                    helpers.JunkratProjectileTests().place(run, enemy, (x + 100, y, z))
                elif scene == 'wall':
                    helpers.JunkratProjectileTests().install_wall(run, player)
                else:
                    run.match.game_map = replace(run.match.game_map, free_for_all=False)
                    enemy.team = player.team
                self.shoot(run, player)
                self.assertEqual(self.points(player), 0)

    def test_deflected_absorbed_and_barrier_hits_award_no_junkrat_charge(self):
        cases = (("Genji", 16), ("Sigma", 8), ("Zarya", 8))
        for name, button in cases:
            with self.subTest(hero=name):
                run, player, enemy = self.fight()
                fight = run.match.combat
                x, y, z = player.mover.position
                run.match.switch_hero(enemy, find_hero(name))
                helper = helpers.JunkratProjectileTests()
                helper.place(run, enemy, (x, y, z + 4))
                for _ in range(25):
                    run.step({enemy: (-32768, 0, 0)})
                before = helper.health(run, enemy).total
                run.step({enemy: (-32768, 0, button)})
                bubble = fight.barriers.sync(enemy) if name == "Zarya" else None
                run.step({enemy: (-32768, 0, 0), player: (0, 0, FIRE)})
                for _ in range(45):
                    run.step({enemy: (-32768, 0, 0)})
                self.assertEqual(self.points(player), 0)
                self.assertEqual(helper.health(run, enemy).total, before)
                if name == "Genji":
                    self.assertEqual(helper.health(run, player).total, 80)
                elif name == "Sigma":
                    grasp = next(i for i in enemy.body_script.component.instances.values()
                                 if i.graph.index == 0x1D02)
                    self.assertEqual(grasp.vars[21312].value(), 120)
                else:
                    self.assertIsNotNone(bubble)
                    self.assertEqual(bubble.health, 80)

                for _ in range(175):
                    run.step({})
                self.assertEqual(self.points(player), 0)
                before = helper.health(run, enemy).total
                self.shoot(run, player)
                self.assertEqual(before - helper.health(run, enemy).total, 120)
                self.assertEqual(self.points(player), 120)

    def test_primary_charge_uses_actual_pool_loss_after_fortify_and_immunity(self):
        for name in ("Orisa", "Reaper", "Mei"):
            with self.subTest(hero=name):
                run, player, enemy = self.fight()
                x, y, z = player.mover.position
                run.match.switch_hero(enemy, find_hero(name))
                helper = helpers.JunkratProjectileTests()
                helper.place(run, enemy, (x, y, z + 4))
                for _ in range(25):
                    run.step({})
                before = helper.health(run, enemy).total
                run.step({enemy: (0, 0, 8)})
                if name == "Orisa":
                    self.assertLess(immunity.damage_scale(enemy, enemy.body), 1)
                elif name == "Reaper":
                    self.assertTrue(immunity.wraith_active(enemy, enemy.body))
                else:
                    self.assertTrue(immunity.cryo_active(enemy, enemy.body))
                self.shoot(run, player)
                dealt = before - helper.health(run, enemy).total
                if name == "Orisa":
                    self.assertGreater(dealt, 0)
                    self.assertLess(dealt, 120)
                else:
                    self.assertEqual(dealt, 0)
                self.assertAlmostEqual(self.points(player), dealt, places=4)

    def test_barrier_absorbed_trap_contact_does_not_award_charge(self):
        run, player, enemy = self.fight()
        match = run.match
        fight = match.combat
        helper = helpers.JunkratProjectileTests()
        run.step({player: (0, 0, 16)})
        for _ in range(60):
            run.step({})
        trap, = fight.junkrat.traps.values()
        x, _, z = trap.flight.position
        match.switch_hero(enemy, find_hero("Zarya"))
        helper.place(run, enemy, (x, enemy.mover.position[1], z - 1.8))
        for _ in range(25):
            run.step({})
        run.step({enemy: (0, 0, 8)})
        bubble = fight.barriers.sync(enemy)
        self.assertEqual(bubble.health, 200)
        for _ in range(60):
            if trap.entity not in fight.junkrat.traps:
                break
            run.frame += 1
            match.update(run.frame * combat.TICK, run.frame)
            player.take_commands([Command(run.frame)])
            enemy.take_commands([Command(run.frame, forward=127)])
        self.assertNotIn(trap.entity, fight.junkrat.traps)
        self.assertEqual(bubble.health, 120)
        self.assertEqual(helper.health(run, enemy).total, 400)
        self.assertEqual(self.points(player), 0)

    def test_actual_primary_awards_120_for_each_of_five_npc_bodies(self):
        for definition in self.catalog.values():
            with self.subTest(npc=definition.name):
                run, player, _bot, vitals = npc_helpers.JunkratNpcCombatTests().create_fight(definition)
                player.body_script.component.set(11942, True)
                before = vitals.total
                self.shoot(run, player, frames=40)
                self.assertEqual(before-vitals.total, 120)
                self.assertEqual(self.points(player), 120)

    def test_actual_mine_and_trap_damage_award_charge(self):
        for button, distance, damage in ((8, 4.5, 120), (0x10, 8, 80)):
            with self.subTest(button=button):
                run, player, bot, vitals = npc_helpers.JunkratNpcCombatTests().create_fight(
                    next(d for d in self.catalog.values() if d.name == 'Heavy Assault'), distance=distance)
                player.body_script.component.set(11942, True)
                before = vitals.total
                for offset in range(60):
                    run.step({player: (0, 4096 if button == 8 else 0, button if offset == 0 else 0)})
                if button == 8:
                    run.step({player: (0, 0, 2)})
                    self.assertEqual(len(run.match.combat.junkrat.pending), 1)
                    run.step({})  # remote messages are consumed on the next world update
                else:
                    trap, = run.match.combat.junkrat.traps.values()
                    x, _y, z = trap.flight.position
                    bot.position = (x, bot.position[1], z)
                    run.step({})
                self.assertEqual(before-vitals.total, damage)
                self.assertEqual(self.points(player), damage)

    def test_passive_and_damage_multipliers_use_original_configs(self):
        run, player, _bot, vitals = self.heavy()
        component = player.body_script.component
        component.set(9532, 2.0)
        component.set(12918, 3.0)
        for _ in range(40):
            run.step({})
        self.assertEqual(self.points(player), 30)
        component.set(11942, True)
        component.set(13022, 3.0)
        before = vitals.total
        self.shoot(run, player)
        self.assertEqual(before-vitals.total, 120)
        self.assertEqual(self.points(player), 750)  #30+120*2*3

    def test_effective_added_tags_apply_half_priority_then_three_quarters(self):
        run, player, bot, _vitals = self.heavy()
        player.body_script.component.set(11942, True)
        system = run.match.combat.junkrat.charge
        for tags, total in (((0x3C3, 0x11E), 60), ((0x11E,), 150), ((), 270)):
            system.set_tags(bot.entity, added=tags)
            self.shoot(run, player)
            self.assertEqual(self.points(player), total)

    def test_passive_suppression_resumes_with_a_new_deadline(self):
        run, player, _enemy = self.fight(passive=True)
        component = player.body_script.component
        component.set(11942, True)
        for _ in range(100):
            run.step({})
        self.assertEqual(self.points(player), 0)
        component.set(11942, False)
        for _ in range(63):
            run.step({})
        self.assertEqual(self.points(player), 0)
        run.step({})
        self.assertEqual(self.points(player), 5)

    def test_actual_construct_destruction_and_self_launch_give_no_charge(self):
        run, player, enemy = self.fight()
        run.match.switch_hero(enemy, find_hero('Junkrat'))
        position = tuple(player.mover.position)
        helpers.JunkratProjectileTests().place(run, enemy, position)
        for _ in range(30):
            run.step({})
        for offset in range(50):
            run.step(dict.fromkeys((player, enemy), (0, 4096, 8 if offset == 0 else 0)))
        system = run.match.combat.junkrat
        enemy_mine, = [mine for mine in system.mines.values() if mine.creator is enemy]
        enemy_body = enemy.body
        start_x = enemy.mover.position[0]
        # Walk the existing body away;place() replaces it and retires its mine.
        for _ in range(100):
            run.frame += 1
            run.match.update(run.frame*combat.TICK, run.frame)
            player.take_commands([Command(run.frame)])
            enemy.take_commands([Command(run.frame, right=127)])
        self.assertGreater(abs(enemy.mover.position[0]-start_x), 6)
        self.assertEqual(enemy.body, enemy_body)
        self.assertIn(enemy_mine.entity, system.mines)
        run.step({player: (0, 0, 2)})
        self.assertEqual(len(system.pending), 1)
        run.step({})
        self.assertTrue(enemy_mine.vitals.dead)
        self.assertEqual(self.points(player), 0)
        self.assertEqual(helpers.JunkratProjectileTests().health(run, player).total, 200)

    def test_duplicate_and_future_owner_frames_cannot_award_charge(self):
        run, player, _enemy = self.fight()
        self.shoot(run, player)
        points = self.points(player)
        before = player.body_script.frame
        player.take_commands([Command(before, buttons=FIRE)], not_after=run.match.tick)
        player.take_commands([Command(before+1000, buttons=FIRE)], not_after=run.match.tick)
        run.match.update(run.frame*combat.TICK, run.frame, frames=False)
        self.assertEqual(self.points(player), points)
        self.assertEqual(player.body_script.frame, before)

    def test_retired_death_source_cannot_credit_replacement(self):
        run, player, enemy = self.fight()
        x, y, z = player.mover.position
        helpers.JunkratProjectileTests().place(run, enemy, (x, y, z+1))
        old_script, old_body = player.body_script, player.body
        self.kill_with_enemy_primary(run, player, enemy)
        self.assertTrue(run.match.combat.junkrat.charge.bindings[old_body].owned())
        self.assertEqual({r.source_entity for r in run.match.combat.rockets}, {old_body})
        run.match.switch_hero(player, find_hero('Junkrat'))
        player.body_script.component.set(11942, True)
        for _ in range(100):
            run.step({})
        self.assertEqual(self.points(player), 0)
        self.assertTrue(helpers.JunkratProjectileTests().health(run, enemy).dead)
        self.assertNotIn(old_body, run.match.combat.junkrat.charge.bindings)
        before = old_script.component.entity_var(342).value()
        old_script.command(run.frame+1, FIRE)
        self.assertEqual(old_script.component.entity_var(342).value(), before)

    def test_in_flight_damage_is_suppressed_during_tire_then_charge_recovers(self):
        run, player, enemy = self.fight()
        fight = run.match.combat
        system = fight.junkrat.tire
        helper = helpers.JunkratProjectileTests()
        x, y, z = player.mover.position
        helper.place(run, enemy, (x, y, z + 6))
        for _ in range(25):
            run.step({})
        player.body_script.set_ult_charge(1925)
        run.step({player: (0, 0, FIRE)})
        self.assertEqual(len(fight.rockets), 1)
        run.step({player: (0, 0, 0x20)})
        tire, = system.actors.values()
        self.assertEqual(self.points(player), 0)
        health = helper.health(run, enemy)
        for _ in range(35):
            run.step({})
            if health.total < 200:
                break
        self.assertLess(run.frame, tire.arm_tick)
        self.assertEqual(health.total, 80)
        self.assertTrue(system.busy(player.body))
        self.assertEqual(self.points(player), 0)
        while run.frame < tire.arm_tick:
            run.step({})

        helper.place(run, enemy, (x, y, z + 15))
        run.step({player: (0, 0, FIRE)})
        self.assertEqual(system.last_result, "detonated")
        self.assertEqual(self.points(player), 0)
        for _ in range(200):
            run.step({})
            if not system.busy(player.body):
                break
        self.assertFalse(system.busy(player.body))
        helper.place(run, enemy, (x, y, z + 6))
        for _ in range(25):
            run.step({})
        self.assertEqual(self.points(player), 0)
        self.shoot(run, player)
        self.assertEqual(helper.health(run, enemy).total, 80)
        self.assertEqual(self.points(player), 120)

    def test_destroyed_tire_does_not_refund_and_later_hits_charge_normally(self):
        run, player, enemy = self.fight()
        fight = run.match.combat
        system = fight.junkrat.tire
        helper = helpers.JunkratProjectileTests()
        player.body_script.set_ult_charge(1925)
        run.step({player: (0, 0, 0x20)})
        tire, = system.actors.values()
        self.assertEqual(self.points(player), 0)
        while run.frame < tire.arm_tick:
            run.step({})
        for _ in range(60):
            x, y, z = tire.mover.position
            direction = aim(fight._eye(enemy), (x, y + .4, z))
            run.step({enemy: (*direction, FIRE)})
            if tire.vitals.dead:
                break
        self.assertTrue(tire.vitals.dead)
        self.assertEqual(tire.vitals.killer, enemy.body)
        self.assertEqual(system.last_result, "destroyed")
        self.assertFalse(system.actors)
        self.assertEqual(helper.health(run, player).total, 200)
        self.assertEqual(helper.health(run, enemy).total, 200)
        self.assertEqual(self.points(player), 0)
        for _ in range(200):
            run.step({})
            if not system.busy(player.body):
                break
        self.assertFalse(system.busy(player.body))
        run.step({player: (0, 0, 0x20)})
        self.assertFalse(system.actors)
        self.assertEqual(self.points(player), 0)
        x, y, z = player.mover.position
        helper.place(run, enemy, (x, y, z + 6))
        for _ in range(25):
            run.step({})
        self.shoot(run, player)
        self.assertEqual(helper.health(run, enemy).total, 80)
        self.assertEqual(self.points(player), 120)

    def test_tire_timeout_kill_does_not_award_charge_and_recovery_restores_it(self):
        run, player, enemy = self.fight()
        match = run.match
        fight = match.combat
        system = fight.junkrat.tire
        helper = helpers.JunkratProjectileTests()
        player.body_script.set_ult_charge(1925)
        run.step({player: (0, 0, 0x20)})
        tire, = system.actors.values()
        self.assertEqual(self.points(player), 0)
        run.frame = tire.end_tick - 1
        match.update(run.frame * combat.TICK, run.frame, frames=False)
        x, y, z = tire.mover.position
        match.switch_hero(enemy, find_hero("Roadhog"))
        helper.place(run, enemy, (x, y, z + 1.5))
        health = helper.health(run, enemy)
        self.assertEqual(health.total, 600)
        run.frame += 1
        match.update(run.frame * combat.TICK, run.frame, frames=False)
        self.assertEqual(system.last_result, "timeout")
        self.assertTrue(health.dead)
        self.assertEqual(health.killer, player.body)
        self.assertEqual(self.points(player), 0)
        self.assertTrue(system.busy(player.body))

        for _ in range(200):
            run.step({})
            if not system.busy(player.body):
                break
        self.assertFalse(system.busy(player.body))
        self.assertEqual(self.points(player), 0)
        x, y, z = player.mover.position
        match.switch_hero(enemy, find_hero("Soldier: 76"))
        helper.place(run, enemy, (x, y, z + 6))
        for _ in range(25):
            run.step({})
        self.shoot(run, player)
        self.assertEqual(helper.health(run, enemy).total, 80)
        self.assertEqual(self.points(player), 120)

    def test_hero_swap_retires_tire_without_late_blast_or_charge(self):
        run, player, enemy = self.fight()
        match = run.match
        fight = match.combat
        system = fight.junkrat.tire
        helper = helpers.JunkratProjectileTests()
        original_body = player.body
        player.body_script.set_ult_charge(1925)
        run.step({player: (0, 0, 0x20)})
        tire, = system.actors.values()
        while run.frame < tire.arm_tick:
            run.step({})
        self.assertTrue(system.busy(original_body))
        self.assertEqual(self.points(player), 0)
        x, y, z = tire.mover.position
        helper.place(run, enemy, (x, y, z + 1.5))

        self.assertTrue(match.switch_hero(player, enemy.hero))
        run.step({})
        self.assertNotIn(original_body, fight.junkrat.charge.bindings)
        self.assertFalse(system.actors)
        self.assertEqual(system.last_result, "owner-retired")
        self.assertTrue(match.switch_hero(player, find_hero("Junkrat")))
        self.assertNotEqual(player.body, original_body)
        player.body_script.component.set(11942, True)
        self.assertEqual(self.points(player), 0)
        run.frame = tire.end_tick + 1
        match.update(run.frame * combat.TICK, run.frame, frames=False)
        self.assertEqual(helper.health(run, enemy).total, 200)
        self.assertFalse(system.actors)
        self.assertEqual(self.points(player), 0)

        x, y, z = player.mover.position
        helper.place(run, enemy, (x, y, z + 6))
        for _ in range(25):
            run.step({})
        self.shoot(run, player)
        self.assertEqual(helper.health(run, enemy).total, 80)
        self.assertEqual(self.points(player), 120)

    def test_posthumous_tire_kill_does_not_credit_old_or_respawned_body(self):
        run, player, enemy = self.fight()
        fight = run.match.combat
        system = fight.junkrat.tire
        helper = helpers.JunkratProjectileTests()
        old_body, old_script = player.body, player.body_script
        old_script.set_ult_charge(1925)
        run.step({player: (0, 0, 0x20)})
        tire, = system.actors.values()
        while run.frame < tire.arm_tick:
            run.step({})
        self.kill_with_enemy_primary(run, player, enemy)
        self.assertIn(tire.entity, system.actors)
        self.assertEqual(tire.vitals.total, 100)
        self.assertEqual(self.points(player), 0)
        health = helper.health(run, enemy)
        run.step({player: (0, 0, FIRE)})
        self.assertEqual(system.last_result, "detonated")
        self.assertFalse(system.actors)
        self.assertTrue(health.dead)
        self.assertEqual(health.killer, old_body)
        self.assertEqual(old_script.component.entity_var(342).value(), 0)

        for _ in range(round(combat.respawn_seconds(run.match) / combat.TICK) + 5):
            run.step({})
            if player.body != old_body:
                break
        self.assertNotEqual(player.body, old_body)
        self.assertTrue(old_script.retired)
        self.assertNotIn(old_body, fight.junkrat.charge.bindings)
        self.assertEqual(self.points(player), 0)
        player.body_script.component.set(11942, True)
        x, y, z = player.mover.position
        helper.place(run, enemy, (x, y, z + 6))
        for _ in range(25):
            run.step({})
        self.shoot(run, player)
        self.assertEqual(helper.health(run, enemy).total, 80)
        self.assertEqual(self.points(player), 120)
        self.assertEqual(old_script.component.entity_var(342).value(), 0)

    def test_natural_charge_cast_detonate_recover_and_second_natural_reuse(self):
        run, player, bot, vitals = self.heavy()
        system = run.match.combat.junkrat.tire
        for cycle in range(2):
            with self.subTest(cycle=cycle):
                if cycle:
                    x, y, z = player.mover.position
                    definition = next(d for d in self.catalog.values() if d.name == 'Heavy Assault')
                    bot.position = (x, y, z+6)
                    bot = npc.spawn(run.match, definition, (x, y, z+6), team=1)
                    vitals = run.match.combat.vitals_of(next(
                        t for t in run.match.combat._targets() if t.owner is bot))
                for _ in range(2200):
                    run.step({player: (0, 0, FIRE)})
                    if self.points(player) == 1925:
                        break
                self.assertEqual(self.points(player), 1925)
                for _ in range(50):
                    run.step({})
                run.step({player: (0, 0, 0x20)})
                tire, = system.actors.values()
                self.assertEqual(self.points(player), 0)
                x, y, z = tire.mover.position
                bot.position = (x, y, z+1.5)
                before = vitals.total
                while run.frame < tire.arm_tick:
                    run.step({})
                run.step({player: (0, 0, FIRE)})
                self.assertEqual(before-vitals.total, min(600, before))
                self.assertEqual(self.points(player), 0)
                for _ in range(200):
                    run.step({})
                self.assertFalse(system.busy(player.body))
                self.assertGreater(self.points(player), 0)

    def test_missing_inputs_refuse_charge_and_do_not_restore_diagnostic_fill(self):
        with patch.dict(os.environ, {'ORPHEUS_CHARGE_ENTITY_INPUTS': 'missing-charge-input.json'}):
            run, player, _enemy = helpers.JunkratProjectileTests().setup_fight()
        self.assertNotIn(player.body, run.match.combat.junkrat.charge.bindings)
        self.shoot(run, player, frames=100)
        self.assertEqual(self.points(player), 0)

    def test_removed_npc_retires_tags_and_next_body_has_full_damage_charge(self):
        run, player, bot, _vitals = self.heavy()
        player.body_script.component.set(11942, True)
        system = run.match.combat.junkrat.charge
        system.set_tags(bot.entity, added=(0x3C3,))
        self.shoot(run, player)
        self.assertEqual(self.points(player), 60)
        binding = system.bindings[player.body]
        old_target = binding.filter_target(bot.entity)
        removed = npc.remove_spawned(run.match)
        self.assertIn(bot.entity, removed)
        run.step({})
        self.assertTrue(old_target.retired)
        self.assertNotIn(bot.entity, binding.targets)
        self.assertNotIn(bot.entity, system.tag_overrides)
        x, y, z = player.mover.position
        definition = next(d for d in self.catalog.values() if d.name == 'Heavy Assault')
        npc.spawn(run.match, definition, (x, y, z+6), team=1)
        self.shoot(run, player)
        self.assertEqual(self.points(player), 180)

    def test_earned_points_survive_death_and_automatic_respawn_preserves_them(self):
        run, player, enemy = self.fight()
        old_script, old_body = player.body_script, player.body
        self.shoot(run, player, frames=40)
        self.assertEqual(self.points(player), 120)
        self.kill_with_enemy_primary(run, player, enemy)
        self.assertEqual(self.points(player), 120)
        for _ in range(700):
            run.step({})
        self.assertNotEqual(player.body, old_body)
        self.assertTrue(old_script.retired)
        self.assertEqual(old_script.component.entity_var(342).value(), 120)
        self.assertEqual(self.points(player), 125)
        self.assertNotIn(old_body, run.match.combat.junkrat.charge.bindings)

    def test_same_hero_handoff_preserves_earned_charge_and_other_hero_resets_it(self):
        run, player, _enemy = self.fight()
        self.shoot(run, player, frames=40)
        self.assertEqual(self.points(player), 120)
        self.assertTrue(run.match.switch_hero(player, find_hero('Junkrat')))
        self.assertEqual(self.points(player), 120)
        self.assertNotIn(player.entity, run.match.combat.junkrat.charge.reserves)
        self.assertTrue(run.match.switch_hero(player, find_hero('Soldier: 76')))
        self.assertTrue(run.match.switch_hero(player, find_hero('Junkrat')))
        self.assertEqual(self.points(player), 0)

    def test_still_owned_dead_body_earns_original_passive_rate(self):
        run, player, enemy = self.fight(passive=True)
        self.kill_with_enemy_primary(run, player, enemy)
        before = self.points(player)
        body = player.body
        for _ in range(125):
            run.step({})
        self.assertEqual(player.body, body)
        self.assertTrue(player.body_script.component.owner_dead)
        self.assertEqual(self.points(player), before+10)

    def test_total_mayhem_damage_earns_charge_for_still_owned_dead_body(self):
        run, player, enemy = self.fight()
        x, y, z = player.mover.position
        helpers.JunkratProjectileTests().place(run, enemy, (x, y, z+1))
        self.kill_with_enemy_primary(run, player, enemy)
        body = player.body
        for _ in range(100):
            run.step({})
        self.assertEqual(player.body, body)
        self.assertTrue(player.body_script.component.owner_dead)
        self.assertTrue(helpers.JunkratProjectileTests().health(run, enemy).dead)
        self.assertEqual(self.points(player), 200)


if __name__ == '__main__':
    unittest.main()
