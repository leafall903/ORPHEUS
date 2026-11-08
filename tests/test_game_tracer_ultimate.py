"""Actual0B8B timers and damage-message plugs own normal live Tracer charge."""

import unittest
from dataclasses import replace

import test_game_tracer as tracer_tests
from test_game_combat import Run, aim

from ow174.game import combat
from ow174.game.commands import FIRE
from ow174.game.content import heroes


class TracerUltimateTests(tracer_tests.TracerCommandFixture, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.script = self.player.body_script
        self.script.ult_seconds = 0  # only passive fill is disabled in damage fixtures
        self.component = self.script.component

    def charge(self):
        return self.component.entity_var(342).value()

    def fire(self):
        tracer_tests.TracerPistolTests().fire(self.run, self.player, self.enemy)

    def test_authored_passive_loop_adds_five_per_second_and_caps(self):
        self.script.ult_seconds = 60  # normal hosted policy uses authored0B8B, not60s debug fill
        self.wait(62)
        self.assertEqual(self.charge(), 0)
        self.step()
        self.assertEqual(self.charge(), 5)
        self.wait(125)
        self.assertEqual(self.charge(), 15)
        cost = self.component.entity_var(338).value()
        self.script.set_ult_charge(cost - 2)
        self.wait(63)
        self.assertEqual(self.charge(), cost)
        self.wait(125)
        self.assertEqual(self.charge(), cost)

    def test_authored_passive_gate_stops_and_restarts_without_backlog(self):
        self.script.ult_seconds = 60
        self.step()
        self.component.set(11942, True)
        self.wait(130)
        self.assertEqual(self.charge(), 0)
        self.component.set(11942, False)
        self.wait(62)
        self.assertEqual(self.charge(), 0)
        self.wait(2)
        self.assertEqual(self.charge(), 5)

    def test_actual_pistol_damage_grants_resolved_loss_and_clamps_on_kill(self):
        self.fire()
        self.assertEqual(self.charge(), 12)
        self.wait(20)  # isolate charge/cap behavior from the authored0.2s post-volley guard
        target = next(t for t in self.run.match.combat._targets() if t.owner is self.enemy)
        vitals = self.run.match.combat.vitals_of(target)
        vitals.now[combat.HEALTH_POOL] = 5
        cost = self.component.entity_var(338).value()
        self.script.set_ult_charge(cost - 1)
        self.fire()
        self.assertTrue(vitals.dead)
        self.assertEqual(self.charge(), cost)

    def test_armour_and_shields_grant_only_actual_pool_loss(self):
        self.run.match.combat.vitals[self.enemy.body] = combat.Vitals(
            self.enemy.body, 1000, armour=50, shields=5)
        self.fire()
        self.assertEqual(self.charge(), 8.5)

    def test_damage_multiplier_vars_are_evaluated_by_the_authored_action(self):
        self.component.set(9532, 2.0)
        self.component.set(13022, 3.0)
        self.fire()
        self.assertEqual(self.charge(), 72)

    def test_eligibility_gate_blocks_charge_without_blocking_actual_damage(self):
        self.step()
        self.component.set(2426, False)
        self.fire()
        vitals = self.run.match.combat.vitals[self.enemy.body]
        self.assertEqual(vitals.total, 188)
        self.assertEqual(self.charge(), 0)
        self.component.set(2426, True)
        self.wait(20)
        self.fire()
        self.assertEqual(self.charge(), 12)

    def test_friendly_and_missed_shots_grant_nothing(self):
        self.run.match.game_map = replace(self.run.match.game_map, free_for_all=False)
        self.enemy.team = self.player.team
        self.fire()
        self.assertEqual(self.charge(), 0)
        self.wait(10)
        self.enemy.team = 1 - self.player.team
        self.enemy.mover.position[0] += 100
        self.step(FIRE)
        self.assertEqual(self.charge(), 0)

    def test_ultimate_damage_tags_exclude_contact_and_blast_charge(self):
        cost = self.component.entity_var(338).value()
        self.script.set_ult_charge(cost)
        self.step(32)
        self.wait(125)
        self.assertTrue(self.run.match.combat.is_dead(self.enemy))
        self.assertEqual(self.charge(), 0)

    def test_recall_self_healing_grants_no_ultimate_charge(self):
        self.wait(200)
        target = next(t for t in self.run.match.combat._targets() if t.owner is self.player)
        self.run.match.combat._damage(self.enemy, target, 70, False, None, self.run.frame)
        self.step(16)
        self.wait(81)
        self.assertEqual(self.run.match.combat.vitals_of(target).total, 150)
        self.assertEqual(self.charge(), 0)

    def test_training_target_grants_full_damage_gain_without_special_target_tags(self):
        run = Run()
        player, = run.players
        run.match.switch_hero(player, heroes()[tracer_tests.TRACER])
        player.body_script.ult_seconds = 0
        bot = next(b for b in run.match.bots if b.team != player.team)
        run.match.bots[:] = [bot]
        run.match._pending_bot_scripts.clear()
        bot.body_script = None
        bot.route = []
        bot.firing = False
        x, y, z = player.mover.position
        bot.position = (x, y, z + 5)
        yaw, pitch = aim(run.match.combat._eye(player), (x, y + 1, z + 5))
        run.step({player: (yaw, pitch, FIRE)})
        self.assertEqual(run.match.combat.vitals[bot.entity].total, 188)
        self.assertEqual(player.body_script.component.entity_var(342).value(), 12)

    def test_death_respawn_preserves_earned_charge_but_explicit_hero_switch_resets_it(self):
        self.wait(2)
        self.script.set_ult_charge(120)
        old_body = self.player.body
        target = next(t for t in self.run.match.combat._targets() if t.owner is self.player)
        self.run.match.combat._damage(self.enemy, target, 200, False, None, self.run.frame)
        for _ in range(650):
            self.step()
            if self.player.body != old_body:
                break
        self.assertNotEqual(self.player.body, old_body)
        self.assertEqual(self.player.body_script.component.entity_var(342).value(), 120)
        self.run.match.switch_hero(self.player, self.enemy.hero)
        self.assertEqual(self.player.body_script.component.entity_var(342).value(), 0)


if __name__ == "__main__":
    unittest.main()
