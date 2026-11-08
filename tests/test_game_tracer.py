"""Tracer damage acceptance through actual build-104319 graphs and player input.

These server fixtures do not establish spread, abilities or original-client acceptance.
"""

import math
import unittest
from dataclasses import replace
from unittest.mock import patch

from test_game_combat import KANEZAKA_DM, Run, aim

from ow174.game import combat, stats, tracer
from ow174.game.collision import World
from ow174.game.commands import FIRE, Command
from ow174.game.content import heroes

TRACER = 0x02E0000000000003


class TracerPistolTests(unittest.TestCase):
    def fight(self):
        run = Run(KANEZAKA_DM, ("Tracer", "Enemy"))
        player, enemy = run.players
        run.match.bots.clear()
        run.match._pending_bot_scripts.clear()
        run.match.switch_hero(player, heroes()[TRACER])
        x, y, z = player.mover.position
        enemy.mover.position = [x, y, z + 5]
        fight = run.match.combat
        target = next(t for t in fight._targets() if t.owner is enemy)
        return run, player, enemy, fight.vitals_of(target)

    def fire(self, run, player, enemy, frames=1):
        yaw, pitch = aim(run.match.combat._eye(player),
                         (enemy.mover.position[0], enemy.mover.position[1] + 1,
                          enemy.mover.position[2]))
        for _ in range(frames):
            run.step({player: (yaw, pitch, FIRE)})

    def test_one_input_shot_deals_two_six_damage_bullets(self):
        run, player, enemy, vitals = self.fight()
        self.fire(run, player, enemy)
        self.assertEqual(vitals.total, 188)
        self.assertEqual(player.stats.value(stats.HERO_DAMAGE), 12)
        self.assertEqual(len(player.client.messages(combat.HIT)), 2)

    def test_armour_is_applied_to_each_bullet(self):
        run, player, enemy, _ = self.fight()
        vitals = combat.Vitals(enemy.body, 200, armour=50)
        run.match.combat.vitals[enemy.body] = vitals
        self.fire(run, player, enemy)
        self.assertEqual(vitals.now[combat.ARMOUR_POOL], 44)
        self.assertEqual(vitals.now[combat.HEALTH_POOL], 200)

    def test_bullets_cross_shields_into_health(self):
        run, player, enemy, _ = self.fight()
        vitals = combat.Vitals(enemy.body, 200, shields=5)
        run.match.combat.vitals[enemy.body] = vitals
        self.fire(run, player, enemy)
        self.assertEqual(vitals.now[combat.SHIELDS_POOL], 0)
        self.assertEqual(vitals.now[combat.HEALTH_POOL], 193)

    def test_first_lethal_bullet_emits_one_death(self):
        run, player, enemy, _ = self.fight()
        run.match.combat.vitals[enemy.body] = combat.Vitals(enemy.body, 5)
        self.fire(run, player, enemy)
        self.assertTrue(run.match.combat.is_dead(enemy))
        self.assertEqual(len(player.client.messages(combat.HIT)), 1)
        self.assertEqual(len(player.client.messages(combat.KILL_NOTICE)), 1)
        self.assertEqual(len(enemy.client.messages(combat.DEATH_NOTICE)), 1)
        self.assertEqual(player.stats.value(stats.HERO_DAMAGE), 5)

    def test_full_magazine_deals_forty_bullets(self):
        run, player, enemy, _ = self.fight()
        vitals = combat.Vitals(enemy.body, 1000)
        run.match.combat.vitals[enemy.body] = vitals
        self.fire(run, player, enemy, frames=61)
        self.assertEqual(vitals.total, 760)
        self.assertEqual(len(player.client.messages(combat.HIT)), 40)

    def test_wall_stops_both_bullets(self):
        run, player, enemy, vitals = self.fight()
        with patch.object(combat, "ray_world", return_value=0.1):
            self.fire(run, player, enemy)
        self.assertEqual(vitals.total, 200)
        self.assertEqual(player.client.messages(combat.HIT), [])

    def test_same_team_is_not_damaged(self):
        run, player, enemy, vitals = self.fight()
        run.match.game_map = replace(run.match.game_map, free_for_all=False)
        enemy.team = player.team
        self.fire(run, player, enemy)
        self.assertEqual(vitals.total, 200)

    def test_reload_restores_the_next_pair_of_bullets(self):
        run, player, enemy, _ = self.fight()
        vitals = combat.Vitals(enemy.body, 1000)
        run.match.combat.vitals[enemy.body] = vitals
        self.fire(run, player, enemy, frames=61)
        for frame in range(90):
            run.step({player: (0, 0, 0x400 if frame == 1 else 0)})
        self.fire(run, player, enemy)
        self.assertEqual(vitals.total, 748)
        self.assertEqual(len(player.client.messages(combat.HIT)), 42)

    def test_quick_melee_deals_thirty_once(self):
        run, player, enemy, vitals = self.fight()
        x, y, z = player.mover.position
        enemy.mover.position = [x, y, z + 1.5]
        yaw, pitch = aim(run.match.combat._eye(player), (x, y + 1, z + 1.5))
        run.step({player: (yaw, pitch, 0x800)})
        for _ in range(40):
            run.step({player: (yaw, pitch, 0)})
        self.assertEqual(vitals.total, 170)
        self.assertEqual(len(player.client.messages(combat.HIT)), 1)

    def test_enemy_player_fire_damages_tracer(self):
        run, player, enemy, _ = self.fight()
        self.fire(run, enemy, player)
        target = next(t for t in run.match.combat._targets() if t.owner is player)
        self.assertEqual(run.match.combat.vitals_of(target).total, 130)

    def test_missed_shot_and_target_beyond_authored_range_take_no_damage(self):
        for distance, yaw in ((5, 16384), (105, 0)):
            with self.subTest(distance=distance, yaw=yaw):
                run, player, enemy, vitals = self.fight()
                x, y, z = player.mover.position
                enemy.mover.position = [x, y, z + distance]
                run.step({player: (yaw, 0, FIRE)})
                self.assertEqual(vitals.total, 200)
                self.assertEqual(player.client.messages(combat.HIT), [])

    def test_distant_body_hit_uses_authored_falloff_floor(self):
        run, player, enemy, vitals = self.fight()
        x, y, z = player.mover.position
        enemy.mover.position = [x, y, z + 25]
        self.fire(run, player, enemy)
        self.assertAlmostEqual(vitals.total, 200 - 12 * 0.3, places=5)

    def test_empty_magazine_cannot_keep_damaging_until_reload(self):
        run, player, enemy, _ = self.fight()
        vitals = combat.Vitals(enemy.body, 1000)
        run.match.combat.vitals[enemy.body] = vitals
        self.fire(run, player, enemy, frames=110)
        self.assertEqual(vitals.total, 760)
        self.assertEqual(len(player.client.messages(combat.HIT)), 40)

    @patch("ow174.game.script.graph.entity_body", return_value=None)
    def test_pair_kills_a_training_npc_and_retains_its_corpse(self, _entity_body):
        run = Run()
        player, = run.players
        run.match.switch_hero(player, heroes()[TRACER])
        bot = next(bot for bot in run.match.bots if bot.team != player.team)
        run.match.bots[:] = [bot]
        run.match._pending_bot_scripts.clear()
        bot.body_script = None
        bot.route = []
        bot.firing = False
        bot.retain_corpse = True
        bot.health = 11
        x, y, z = player.mover.position
        bot.position = (x, y, z + 5)
        yaw, pitch = aim(run.match.combat._eye(player), (x, y + 1, z + 5))
        run.step({player: (yaw, pitch, FIRE)})
        self.assertTrue(bot.dead)
        self.assertEqual(run.match.combat.vitals[bot.entity].total, 0)
        self.assertEqual(player.stats.value(stats.HERO_DAMAGE), 11)
        for _ in range(200):
            run.step({})
        self.assertIn(bot, run.match.bots)
        self.assertFalse(any(who is bot for _, who in run.match.combat.respawns))


class TracerCommandFixture:
    def setUp(self):
        self.run, self.player, self.enemy, _ = TracerPistolTests().fight()
        self.blink = next(i for i in self.player.body_script.component.instances.values()
                          if i.graph.index == 0x24)

    def step(self, buttons=0, *, yaw=0, pitch=0, forward=0, right=0):
        run, player = self.run, self.player
        run.frame += 1
        run.match.update(run.frame * combat.TICK, run.frame)
        player.take_commands([Command(run.frame, yaw=yaw, pitch=pitch, buttons=buttons,
                                      forward=forward, right=right)])

    def wait(self, frames):
        for _ in range(frames):
            self.step()


class TracerBlinkTests(TracerCommandFixture, unittest.TestCase):
    def test_forward_blink_is_seven_point_five_and_corrects_same_frame(self):
        before = tuple(self.player.mover.position)
        self.step(8, pitch=16000)
        after = tuple(self.player.mover.position)
        self.assertAlmostEqual(after[2] - before[2], 7.5, places=3)
        self.assertEqual(after[:2], before[:2])
        self.assertEqual(self.player.mover.history[-1].position, after)
        self.assertEqual(self.blink.vars[11].value(), 2)

    def test_side_and_backward_throttle_and_diagonal_normalisation(self):
        for forward, right in ((-127, 0), (0, 127), (127, 127)):
            with self.subTest(forward=forward, right=right):
                self.setUp()
                before = tuple(self.player.mover.position)
                # Account separately for the normal movement preceding the action.
                self.step(0, forward=forward, right=right)
                before = tuple(self.player.mover.position)
                self.step(8, forward=forward, right=right)
                delta = tuple(a - b for a, b in zip(self.player.mover.position, before, strict=True))
                self.assertGreaterEqual(math.hypot(delta[0], delta[2]), 7.49)
                self.assertLess(math.hypot(delta[0], delta[2]), 7.7)
                if right:
                    self.assertLess(delta[0], 0)
                if forward:
                    self.assertEqual(delta[2] > 0, forward > 0)

    def test_three_charges_exhaust_and_recharge_without_button_bypass(self):
        for expected in (2, 1, 0):
            before = tuple(self.player.mover.position)
            self.step(8)
            self.assertAlmostEqual(math.dist(before, self.player.mover.position), 7.5, places=3)
            self.assertEqual(self.blink.vars[11].value(), expected)
            self.wait(10)
        before = tuple(self.player.mover.position)
        for _ in range(10):
            self.step(8)
            self.step(0)
        self.assertEqual(tuple(self.player.mover.position), before)
        self.assertEqual(self.blink.vars[11].value(), 0)
        self.wait(140)  # total since first cast is just over the authored 3 seconds
        self.assertEqual(self.blink.vars[11].value(), 1)
        self.step(8)
        self.assertAlmostEqual(math.dist(before, self.player.mover.position), 7.5, places=3)
        self.assertEqual(self.blink.vars[11].value(), 0)

    def test_wall_sweeps_the_full_capsule(self):
        from test_game_mover import wall

        x, y, z = self.player.mover.position
        # Wall spans the Blink path. Use real static collision, not a ray mock.
        floor = [((-100, y, -100), (100, y, 100), (100, y, -100)),
                 ((-100, y, -100), (-100, y, 100), (100, y, 100))]
        floor = [tuple(axis for point in triangle for axis in point) for triangle in floor]
        collision = World(floor + wall(x - 5, z + 3, x + 5, z + 3, y, y + 5))
        self.run.match.collision_world = collision
        self.player.mover.use(world=collision)
        self.step(8)
        self.assertGreater(self.player.mover.position[2], z)
        self.assertLess(self.player.mover.position[2], z + 3)
        self.assertEqual(self.blink.vars[11].value(), 2)

    def test_dead_body_cannot_blink(self):
        target = next(t for t in self.run.match.combat._targets() if t.owner is self.player)
        self.run.match.combat._damage(self.enemy, target, 200, False, None, self.run.frame)
        before = tuple(self.player.mover.position)
        self.step(8)
        self.assertEqual(tuple(self.player.mover.position), before)
        self.assertEqual(self.blink.vars[11].value(), 3)

    def test_secondary_blinks_and_passes_through_enemy_without_damage(self):
        before = tuple(self.player.mover.position)
        self.step(2)
        self.assertAlmostEqual(math.dist(before, self.player.mover.position), 7.5, places=3)
        target = next(t for t in self.run.match.combat._targets() if t.owner is self.enemy)
        self.assertEqual(self.run.match.combat.vitals_of(target).total, 200)
        self.assertEqual(self.blink.vars[11].value(), 2)

    def test_held_and_duplicate_commands_do_not_spend_more_charges(self):
        before = tuple(self.player.mover.position)
        for _ in range(50):
            self.step(8)
        self.player.take_commands([Command(self.run.frame, buttons=8)])
        self.assertAlmostEqual(math.dist(before, self.player.mover.position), 7.5, places=3)
        self.assertEqual(self.blink.vars[11].value(), 2)

    def test_recharge_stops_at_three_charges(self):
        self.step(8)
        self.wait(620)
        self.assertEqual(self.blink.vars[11].value(), 3)
        before = tuple(self.player.mover.position)
        self.step(8)
        self.assertAlmostEqual(math.dist(before, self.player.mover.position), 7.5, places=3)
        self.assertEqual(self.blink.vars[11].value(), 2)


class TracerRecallTests(TracerCommandFixture, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.recall = next(i for i in self.player.body_script.component.instances.values()
                           if i.graph.index == 0x1A)
        self.target = next(t for t in self.run.match.combat._targets() if t.owner is self.player)
        self.vitals = self.run.match.combat.vitals_of(self.target)

    def hurt(self, amount):
        self.run.match.combat._damage(self.enemy, self.target, amount, False, None, self.run.frame)

    def test_restores_three_second_position_health_ammo_and_same_frame_correction(self):
        for _ in range(200):
            self.step(forward=127)
        before = tuple(self.player.mover.position)
        self.hurt(70)
        weapon = next(i for i in self.player.body_script.component.instances.values()
                      if i.graph.index == 0x16)
        weapon.write((0, 53), 2)
        self.step(16)
        self.wait(80)
        self.assertFalse(tracer.recalling(self.player))
        self.assertLess(self.player.mover.position[2], before[2] - 15)
        self.assertEqual(self.vitals.total, 150)
        self.assertEqual(weapon.vars[53].value(), weapon.vars[198].value())
        self.assertEqual(tuple(self.player.mover.position), self.player.mover.history[-1].position)
        self.assertGreater(self.recall.vars[61].value(), 11.9)

    def test_invulnerable_and_untargetable_during_both_phases_then_damage_resumes(self):
        self.wait(2)
        self.step(16)
        for frames in (1, 30):
            self.wait(frames)
            self.assertTrue(tracer.recalling(self.player))
            self.hurt(200)
            self.assertEqual(self.vitals.total, 150)
            self.assertFalse(any(t.owner is self.player
                                 for t in self.run.match.combat._enemies(self.enemy)))
        self.wait(60)
        self.assertFalse(tracer.recalling(self.player))
        self.hurt(20)
        self.assertEqual(self.vitals.total, 130)

    def test_translation_is_locked_until_restore_and_pistols_are_suppressed(self):
        self.wait(2)
        self.hurt(20)
        before = tuple(self.player.mover.position)
        self.step(16)
        for _ in range(60):
            self.step(FIRE, forward=127, right=127)
            self.assertEqual(tuple(self.player.mover.position), before)
        self.assertEqual(self.player.client.messages(combat.HIT), [])

    def test_cooldown_rejects_spam_and_reenables_after_authored_twelve_seconds(self):
        self.wait(2)
        self.step(16)
        self.wait(81)
        before = self.recall.state(16).counter
        for _ in range(20):
            self.step(16)
            self.step(0)
        self.assertEqual(self.recall.state(16).counter, before)
        self.assertFalse(tracer.recalling(self.player))
        self.wait(720)
        self.assertEqual(self.recall.vars[61].value(), 0)
        self.step(16)
        self.assertTrue(tracer.recalling(self.player))

    def test_recall_never_lowers_recently_healed_health(self):
        self.hurt(70)
        self.wait(200)
        self.vitals.heal(70)
        self.step(16)
        self.wait(81)
        self.assertEqual(self.vitals.total, 150)

    def test_dead_and_replacement_bodies_cannot_restore_old_history(self):
        self.wait(2)
        self.hurt(200)
        self.step(16)
        self.assertFalse(tracer.recalling(self.player))
        self.assertTrue(self.vitals.dead)
        self.run.match.switch_hero(self.player, self.player.hero)
        self.assertFalse(hasattr(self.player.body_script.world, "recall_history"))
        self.wait(2)
        self.step(16)
        self.wait(81)
        target = next(t for t in self.run.match.combat._targets() if t.owner is self.player)
        self.assertEqual(self.run.match.combat.vitals_of(target).total, 150)

    def test_cancel_does_not_restore_health_or_bypass_cooldown(self):
        for phase_frames in (1, 35):
            with self.subTest(phase_frames=phase_frames):
                self.setUp()
                self.wait(2)
                self.hurt(70)
                self.step(16)
                self.wait(phase_frames)
                self.recall.state(16).stop()
                self.wait(2)
                self.assertFalse(tracer.recalling(self.player))
                self.assertEqual(self.vitals.total, 80)
                self.assertGreater(self.recall.vars[61].value(), 11)
                self.step(16)
                self.assertFalse(tracer.recalling(self.player))

    def test_blink_and_secondary_cannot_move_or_spend_charges_during_recall(self):
        self.wait(2)
        self.step(16)
        before = tuple(self.player.mover.position)
        for button in (8, 2):
            self.step(button)
            self.step(0)
        self.assertEqual(tuple(self.player.mover.position), before)
        self.assertEqual(self.blink.vars[11].value(), 3)

    def test_old_recall_timer_cannot_modify_a_replacement_body(self):
        self.wait(2)
        self.step(16)
        old_script = self.player.body_script
        self.run.match.switch_hero(self.player, self.player.hero)
        target = next(t for t in self.run.match.combat._targets() if t.owner is self.player)
        vitals = self.run.match.combat.vitals_of(target)
        self.run.match.combat._damage(self.enemy, target, 20, False, None, self.run.frame)
        before = tuple(self.player.mover.position)
        old_script.component.run_frame(old_script.frame + 100)
        self.assertEqual(vitals.total, 130)
        self.assertEqual(tuple(self.player.mover.position), before)


if __name__ == "__main__":
    unittest.main()
