"""Effective-damage ultimate credit and authored Swift Strike reset integration."""

import unittest

from test_game_combat import aim
from test_genji_combat import GenjiFixture

from ow174.game import combat, stats
from ow174.game.content import SOLDIER, heroes
from ow174.game.script.expr import INSTANCE


def aimed_burst(run, player, *, height=1.0):
    x, y, z = run.players[1].mover.position
    yaw, pitch = aim(run.match.combat._eye(player), (x, y + height, z))
    for frame in range(60):
        run.step({player: (yaw, pitch, 1 if frame == 0 else 0)})


def dash_instance(player):
    return next(i for i in player.body_script.component.instances.values() if i.graph.index == 0x01DB)


def ultimate(player):
    instance = next(i for i in player.body_script.component.instances.values() if i.graph.index == 0x0317)
    return instance.state(3)


class GenjiChargeTests(GenjiFixture):
    def scene(self, distance=4):
        run, player = self.spawn(distance)
        player.body_script.ult_seconds = 0  # disable diagnostic passive fill for exact combat accounting
        return run, player

    def charge(self, player):
        return player.body_script.component.entity_var(342).value()

    def test_body_damage_credits_87_points(self):
        run, player = self.scene()
        aimed_burst(run, player)
        self.assertEqual(self.charge(player), 87)

    def test_head_damage_credits_174_points(self):
        run, player = self.scene()
        aimed_burst(run, player, height=1.55)
        self.assertEqual(self.charge(player), 174)

    def test_armour_credits_effective_damage(self):
        run, player = self.scene()
        enemy = run.players[1]
        run.match.combat.vitals[enemy.body] = combat.Vitals(enemy.body, 200, 100)
        aimed_burst(run, player)
        self.assertEqual(self.charge(player), 72)

    def test_shield_health_damage_credits_actual_loss(self):
        run, player = self.scene()
        enemy = run.players[1]
        run.match.combat.vitals[enemy.body] = combat.Vitals(enemy.body, 200, shields=100)
        aimed_burst(run, player)
        self.assertEqual(self.charge(player), 87)

    def test_overkill_does_not_generate_excess_charge(self):
        run, player = self.scene()
        enemy = run.players[1]
        vitals = combat.Vitals(enemy.body, 200)
        vitals.damage(180)
        run.match.combat.vitals[enemy.body] = vitals
        aimed_burst(run, player)
        self.assertEqual(self.charge(player), 20)

    def test_charge_caps_at_evaluated_cost(self):
        run, player = self.scene()
        cost = player.body_script.component.entity_var(338).value()
        player.body_script.set_ult_charge(cost - 10)
        aimed_burst(run, player)
        self.assertEqual(self.charge(player), cost)

    def test_active_blade_does_not_generate_charge(self):
        run, player = self.scene()
        player.body_script.set_ult_charge(player.body_script.component.entity_var(338).value())
        for frame in range(110):
            run.step({player: (0, 0, 32 if frame == 0 else (1 if frame > 70 else 0))})
        self.assertEqual(self.health(run, run.players[1]), 90)
        self.assertEqual(self.charge(player), 0)

    def test_actual_damage_can_finish_charge_then_start_blade(self):
        run, player = self.scene()
        cost = player.body_script.component.entity_var(338).value()
        player.body_script.set_ult_charge(cost - 87)
        aimed_burst(run, player)
        self.assertEqual(self.charge(player), cost)
        run.step({player: (0, 0, 32)})
        self.assertTrue(ultimate(player).flags & 1)
        self.assertEqual(self.charge(player), 0)

    def test_cost_minus_one_rejects_ultimate(self):
        run, player = self.scene()
        cost = player.body_script.component.entity_var(338).value()
        player.body_script.set_ult_charge(cost - 1)
        run.step({player: (0, 0, 32)})
        self.assertFalse(ultimate(player).flags & 1)
        self.assertEqual(self.charge(player), cost - 1)

    def test_misses_and_self_damage_generate_no_charge(self):
        run, player = self.scene()
        for frame in range(60):
            run.step({player: (-32768, 0, 1 if frame == 0 else 0)})
        target = next(t for t in run.match.combat._targets() if t.owner is player)
        run.match.combat._damage(player, target, 10, False, None, run.frame)
        self.assertEqual(self.charge(player), 0)

    def test_reflected_rifle_damage_credits_genji(self):
        run, player = self.scene()
        source = run.players[1]
        for frame in range(5):
            run.step({player: (0, 0, 16 if frame == 0 else 0)})
        for frame in range(20):
            run.step({source: (-32768, 0, 1 if frame == 0 else 0)})
        self.assertEqual(self.health(run, source), 160)
        self.assertEqual(self.charge(player), 40)

    def test_owned_projectile_can_credit_after_death_before_body_retirement(self):
        run, player = self.scene(50)
        enemy = run.players[1]
        yaw, pitch = aim(run.match.combat._eye(player), (0, 2, 50))
        run.step({player: (yaw, pitch, 1)})
        target = next(t for t in run.match.combat._targets() if t.owner is player)
        run.match.combat._damage(enemy, target, 200, False, None, run.frame)
        for _ in range(90):
            run.step({})
        self.assertTrue(run.match.combat.is_dead(player))
        self.assertEqual(self.health(run, enemy), 171)
        self.assertEqual(self.charge(player), 29)

    def test_body_replacement_retires_unleased_shuriken(self):
        run, player = self.scene(50)
        run.step({player: (0, 0, 1)})
        self.assertTrue(run.match.combat.rockets)
        run.match.switch_hero(player, heroes()[SOLDIER])
        player.body_script.ult_seconds = 0
        self.assertFalse(any(r.shooter is player for r in run.match.combat.rockets))
        for _ in range(100):
            run.step({})
        self.assertEqual(self.health(run, run.players[1]), 200)
        self.assertEqual(self.charge(player), 0)

    def test_death_interrupts_active_ultimate(self):
        run, player = self.scene()
        player.body_script.set_ult_charge(player.body_script.component.entity_var(338).value())
        run.step({player: (0, 0, 32)})
        self.assertTrue(ultimate(player).flags & 1)
        target = next(t for t in run.match.combat._targets() if t.owner is player)
        run.match.combat._damage(run.players[1], target, 200, False, None, run.frame)
        run.step({})
        self.assertFalse(ultimate(player).flags & 1)


class GenjiDashResetTests(GenjiFixture):
    def used_dash(self, run, player):
        for frame in range(40):
            run.step({player: (0, 0, 8 if frame == 0 else 0)})
        self.assertGreater(dash_instance(player).state(0).var.value(), 7)

    def party(self):
        run, player = self.spawn(4, ("Genji", "Victim", "Ally"))
        victim, ally = run.players[1:]
        run.match.game_map = run.match.game_map.__class__(
            **{**run.match.game_map.__dict__, "free_for_all": False}
        )
        player.team = ally.team = 0
        victim.team = 1
        ally.mover.position[:] = [0, 1, 0]
        self.used_dash(run, player)
        return run, player, victim, ally

    def ally_kill(self, run, victim, ally):
        yaw, pitch = aim(run.match.combat._eye(ally), (0, victim.mover.position[1] + 1.55, 4))
        for _ in range(100):
            run.step({ally: (yaw, pitch, 1)})
            if run.match.combat.is_dead(victim):
                break
        self.assertTrue(run.match.combat.is_dead(victim))

    def test_final_blow_resets_and_allows_a_new_dash(self):
        run, player = self.spawn(4)
        self.used_dash(run, player)
        aimed_burst(run, player, height=1.55)
        self.assertTrue(run.match.combat.is_dead(run.players[1]))
        self.assertEqual(dash_instance(player).state(0).var.value(), 0)
        run.step({player: (0, 0, 8)})
        self.assertEqual(dash_instance(player).state(1).counter, 2)

    def test_nonlethal_damage_does_not_reset(self):
        run, player = self.spawn(4)
        self.used_dash(run, player)
        aimed_burst(run, player)
        self.assertFalse(run.match.combat.is_dead(run.players[1]))
        self.assertGreater(dash_instance(player).state(0).var.value(), 0)

    def test_ultimate_start_resets_authored_dash_cooldown(self):
        run, player = self.spawn(4)
        self.used_dash(run, player)
        player.body_script.set_ult_charge(player.body_script.component.entity_var(338).value())
        run.step({player: (0, 0, 32)})
        run.step({})  # deliver authored self-message05BE
        self.assertGreater(dash_instance(player).state(0).var.value(), 0)
        for _ in range(80):
            run.step({})
        self.assertEqual(dash_instance(player).state(0).var.value(), 0)
        run.step({player: (0, 0, 8)})
        self.assertEqual(dash_instance(player).state(1).counter, 2)

    def test_recent_damage_assist_resets_dash(self):
        run, player, victim, ally = self.party()
        self.ally_kill(run, victim, ally)
        self.assertEqual(dash_instance(player).state(0).var.value(), 0)
        self.assertEqual(player.stats.values[stats.ELIMINATIONS], 1)
        self.assertEqual(ally.stats.values[stats.ELIMINATIONS], 1)
        self.assertEqual(len(player.client.messages(combat.KILL_NOTICE)), 1)

    def test_expired_damage_contribution_does_not_reset_dash(self):
        run, player, victim, ally = self.party()
        for _ in range(340):
            run.step({})
        self.ally_kill(run, victim, ally)
        self.assertGreater(dash_instance(player).state(0).var.value(), 0)

    def test_replacement_body_cannot_borrow_old_contribution(self):
        run, player, victim, ally = self.party()
        run.match.switch_hero(player, heroes()[player.hero.guid])
        instance = dash_instance(player)
        instance.write((INSTANCE, 61), 8)
        instance.component.run_now()
        self.ally_kill(run, victim, ally)
        self.assertGreater(instance.state(0).var.value(), 0)


if __name__ == "__main__":
    unittest.main()
