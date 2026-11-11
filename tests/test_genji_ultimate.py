"""Effective-damage ultimate credit and authored Swift Strike reset integration."""

import unittest
from unittest.mock import patch

from test_game_combat import aim
from test_genji_combat import GenjiFixture

from ow174.game import combat, stats
from ow174.game.content import SOLDIER, heroes
from ow174.game.script import expr, graph, runtime


class AuthoredChargeMessageTests(unittest.TestCase):
    def test_damage_message_entries_apply_distinct_tags_and_breakdown(self):
        cases = (
            (0x12, (), 8, 24),
            (0x12, (), 80, 20),
            (0x12, (0x1FD,), 8, 20),
            (0x12, (0x25A,), 8, 20),
            (0x148, (), 8, 20),
            (0x148, (0x90D,), 8, 25),
        )
        for message, tags, filtered_amount, expected in cases:
            with self.subTest(message=hex(message), tags=tags, filtered_amount=filtered_amount):
                component = runtime.Component(123)
                instance = component.create(graph.graph(0x0B8B), init=False, values={
                    (expr.ENTITY, 338): 100,
                    (expr.ENTITY, 342): 20,
                    (expr.ENTITY, 2426): True,
                    (expr.ENTITY, 9532): 0.5,
                    (expr.ENTITY, 13022): 0.25,
                })
                original_native = component.world.native
                breakdown = (expr.Map(((16056, expr.Asset(0x0D80000000003EBB)),
                                       (499, expr.f32(filtered_amount)))),)

                def native(cfg, caller):
                    kind = cfg.get("$")
                    if kind in {"STU_AC829876", "STU_A58C1E63"}:
                        return True
                    if kind == "STU_25ECB55F":
                        return False
                    return original_native(cfg, caller)

                params = {
                    0x13: 40,
                    0x15: expr.Entity(124),
                    0x3D: tuple(expr.Asset(0x0D80000000000000 | tag) for tag in tags),
                    0x624: breakdown,
                }
                with patch.object(component.world, "native", side_effect=native):
                    delivered = runtime.send_instance_message(
                        instance, 0x0240000000000000 | message, params, 123,
                    )
                    self.assertEqual(delivered, 1)
                    instance.drain()
                self.assertIsNone(component.fault)
                self.assertEqual(component.entity_var(342).value(), expected)

    def test_deflect_reward_graph_sends_one_scaled_charge_grant(self):
        component = runtime.Component(0xA0000100)
        reward = component.create(graph.graph(0x22F0), init=False)
        charge = component.create(graph.graph(0x0B8B), init=False, values={
            (expr.ENTITY, 338): 100,
            (expr.ENTITY, 342): 0,
            (expr.ENTITY, 2426): True,
            (expr.ENTITY, 9532): 0.5,
            (expr.ENTITY, 13022): 0.25,
        })
        # Inject only the interception callback. The graph sends the grant.
        node = reward.graph.node(14)
        reward.follow(node, "m_160B5DBE")
        charge.drain()
        self.assertEqual(component.entity_var(342).value(), 60)
        self.assertIs(reward.find_var(expr.INSTANCE, 7121).value(), True)
        reward.follow(node, "m_160B5DBE")
        charge.drain()
        self.assertEqual(component.entity_var(342).value(), 60)

    def dispatch(self, message, amount, *, initial=20, enabled=True, paused=False, scales=None):
        component = runtime.Component(123)
        values = {338: 100, 342: initial, 2426: enabled, 842: paused}
        if scales is not None:
            values.update({9532: scales[0], 13022: scales[1]})
        instance = component.create(graph.graph(0x0B8B), init=False,
                                    values={(expr.ENTITY, key): value for key, value in values.items()})
        params = {0x40: amount, 0x41: expr.Entity(124), 0x3F: ()} if message == 0x44 else {0x2A: amount}
        self.assertEqual(runtime.send_instance_message(instance, 0x0240000000000000 | message, params, 123), 1)
        instance.drain()
        return component.entity_var(342).value()

    def test_authored_charge_messages_have_distinct_scalars_and_caps(self):
        for message, expected_scaled, expected_negative in ((0x44, 25, 15), (0xEB, 40, 0)):
            with self.subTest(message=hex(message)):
                self.assertEqual(self.dispatch(message, 40), 60)
                self.assertEqual(self.dispatch(message, 40, scales=(0.5, 0.25)), expected_scaled)
                self.assertEqual(self.dispatch(message, -40, scales=(0.5, 0.25)), expected_negative)
                self.assertEqual(self.dispatch(message, 40, initial=95), 100)
                self.assertEqual(self.dispatch(message, 40, paused=True), 20)
                self.assertEqual(self.dispatch(message, 40, enabled=False), 20)


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

    def test_reflected_damage_unlocks_blade_then_shuriken_returns(self):
        for reflected in (False, True):
            with self.subTest(reflected=reflected):
                run, player = self.scene()
                enemy = run.players[1]
                cost = player.body_script.component.entity_var(338).value()
                player.body_script.set_ult_charge(cost - 40)

                facing = 0 if reflected else -32768
                run.step({player: (facing, 0, 16)})
                for frame in range(24):
                    run.step({player: (facing, 0, 0), enemy: (-32768, 0, 1 if frame == 4 else 0)})
                self.assertEqual(self.charge(player), cost if reflected else cost - 40)
                self.assertEqual(self.health(run, player), 200 if reflected else 160)
                self.assertEqual(self.health(run, enemy), 160 if reflected else 200)

                deflect = next(i for i in player.body_script.component.instances.values() if i.graph.index == 0x03A3)
                self.assertTrue(deflect.state(10).active)
                run.step({player: (0, 0, 32)})
                self.assertEqual(bool(ultimate(player).flags & 1), reflected)
                self.assertEqual(self.charge(player), 0 if reflected else cost - 40)
                if not reflected:
                    continue
                self.assertFalse(deflect.state(10).active)

                for _ in range(76):
                    run.step({})
                run.step({player: (0, 0, 1)})
                for _ in range(35):
                    run.step({})
                self.assertEqual(self.health(run, enemy), 50)
                self.assertEqual(self.charge(player), 0)
                self.assertTrue(ultimate(player).flags & 1)

                for _ in range(500):
                    if not ultimate(player).flags & 1:
                        break
                    run.step({})
                self.assertFalse(ultimate(player).flags & 1)
                self.assertEqual(self.charge(player), 0)
                weapon = next(i for i in player.body_script.component.instances.values() if i.graph.index == 0x0304)
                self.assertEqual(weapon.vars[53].value(), 30)
                aimed_burst(run, player)
                self.assertEqual(weapon.vars[53].value(), 27)
                self.assertEqual(self.health(run, enemy), 0)
                self.assertEqual(self.charge(player), 50)
                hits = player.client.messages(combat.HIT)
                self.assertEqual([hit["+0x78"]["+0x4"] for hit in hits], [40, 110, 29, 21])

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
    def used_dash(self, run, player, yaw=0):
        for frame in range(40):
            run.step({player: (yaw, 0, 8 if frame == 0 else 0)})
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
        run, player = self.spawn(4, ("Genji", "Charge target", "Blade target"))
        enemy = run.players[1]
        blade_target = run.players[2]
        run.match.switch_hero(blade_target, player.hero)
        blade_target.mover.position[:] = [0, 1, -34]
        run.match.switch_hero(enemy, heroes()[0x02E000000000023B])
        enemy.spawn = ((0, 1, 4), 0)
        enemy.mover.position[:] = [0, 1, 4]
        component = player.body_script.component
        cost = component.entity_var(338).value()
        self.assertEqual(component.entity_var(342).value(), 0)
        yaw, pitch = aim(run.match.combat._eye(player), (0, 2, 4))
        for _ in range(5000):
            run.step({player: (yaw, pitch, 1)})
            if component.entity_var(342).value() == cost:
                break
        self.assertEqual(component.entity_var(342).value(), cost)
        hit_damage = sum(hit["+0x78"]["+0x4"] for hit in player.client.messages(combat.HIT))
        self.assertGreaterEqual(hit_damage, cost)

        # Dash away so no enemy elimination can reset the cooldown under test.
        self.used_dash(run, player, yaw=-32768)
        run.step({player: (0, 0, 32)})
        self.assertTrue(ultimate(player).flags & 1)
        self.assertEqual(component.entity_var(342).value(), 0)
        run.step({})  # deliver authored self-message05BE
        self.assertGreater(dash_instance(player).state(0).var.value(), 0)
        for _ in range(80):
            run.step({})
        self.assertEqual(dash_instance(player).state(0).var.value(), 0)
        run.step({player: (-32768, 0, 8)})
        self.assertEqual(dash_instance(player).state(1).counter, 2)

        for _ in range(40):
            run.step({})
        self.assertTrue(ultimate(player).flags & 1)
        self.assertEqual(player.mover.position, [0, 1, -30])
        hits_before = len(player.client.messages(combat.HIT))
        run.step({player: (-32768, 0, 1)})
        for _ in range(35):
            run.step({player: (-32768, 0, 0)})
        self.assertEqual(self.health(run, blade_target), 90)
        self.assertEqual(component.entity_var(342).value(), 0)
        self.assertTrue(ultimate(player).flags & 1)

        for _ in range(500):
            run.step({})
            if not ultimate(player).flags & 1:
                break
        self.assertFalse(ultimate(player).flags & 1)
        for _ in range(60):
            run.step({})
        weapon = next(i for i in component.instances.values() if i.graph.index == 0x0304)
        self.assertEqual(weapon.vars[53].value(), 30)
        yaw, pitch = aim(run.match.combat._eye(player), (0, 2, -34))
        for frame in range(60):
            run.step({player: (yaw, pitch, 1 if frame == 0 else 0)})
        self.assertEqual(weapon.vars[53].value(), 27)
        self.assertTrue(run.match.combat.is_dead(blade_target))
        self.assertEqual(component.entity_var(342).value(), 90)
        hits = player.client.messages(combat.HIT)[hits_before:]
        self.assertEqual(hits[0]["+0x78"]["+0x4"], 110)
        self.assertEqual(sum(hit["+0x78"]["+0x4"] for hit in hits[1:]), 90)
        self.assertTrue(hits[-1]["+0x78"]["+0x8"] & combat.KILLING)

    def test_recent_damage_assist_resets_dash(self):
        run, player, victim, ally = self.party()
        self.ally_kill(run, victim, ally)
        self.assertEqual(dash_instance(player).state(0).var.value(), 0)
        self.assertEqual(player.stats.values[stats.ELIMINATIONS], 1)
        self.assertEqual(ally.stats.values[stats.ELIMINATIONS], 1)
        self.assertEqual(len(player.client.messages(combat.KILL_NOTICE)), 1)
        run.step({player: (0, 0, 8)})
        self.assertEqual(dash_instance(player).state(1).counter, 2)

    def test_expired_damage_contribution_does_not_reset_dash(self):
        run, player, victim, ally = self.party()
        for _ in range(340):
            run.step({})
        self.ally_kill(run, victim, ally)
        self.assertGreater(dash_instance(player).state(0).var.value(), 0)

    def test_replacement_body_cannot_borrow_old_contribution(self):
        run, player, victim, ally = self.party()
        old_body = player.body
        run.match.switch_hero(player, heroes()[player.hero.guid])
        self.assertNotEqual(player.body, old_body)
        player.mover.position[:] = [0, 1, 15]
        self.used_dash(run, player)
        self.assertEqual(self.health(run, victim), 150)
        instance = dash_instance(player)
        self.ally_kill(run, victim, ally)
        self.assertGreater(instance.state(0).var.value(), 0)
        self.assertFalse(player.client.messages(combat.KILL_NOTICE))
        run.step({player: (0, 0, 8)})
        self.assertEqual(instance.state(1).counter, 1)


if __name__ == "__main__":
    unittest.main()
