"""Ashe command-to-health checks with external build-104319 graphs.

These are offline server checks, not original-client or five-PvE-NPC acceptance.
Separate acceptance matrices retain mechanisms that these focused checks do not cover.
"""

import math
import struct
import unittest
from unittest.mock import patch

from test_game_combat import KANEZAKA_DM, Run, aim
from test_game_mover import FLOOR, wall

from ow174.game import ashe, combat, world
from ow174.game.collision import World
from ow174.game.content import HEROES_PATH, PRACTICE_RANGE, heroes
from ow174.game.script import graph, nodes

ASHE = 0x02E0000000000200


@unittest.skipUnless(HEROES_PATH.is_file() and graph.GRAPHS_PATH.is_file(), "requires external 104319 data")
class AsheCombatTests(unittest.TestCase):
    def fight(self, distance=1.0, team_game=False):
        run = Run(PRACTICE_RANGE if team_game else KANEZAKA_DM, ("Ashe", "Enemy"))
        player, enemy = run.players
        run.match.bots = []
        run.match.switch_hero(player, heroes()[ASHE])
        player.mover.position[:] = [0.0, 0.0, 0.0]
        enemy.mover.position[:] = [0.0, 0.0, distance]
        for _ in range(100):
            run.step({})
        return run, player, enemy

    def press(self, run, player, button, frames=20, pitch=0):
        for index in range(frames):
            run.step({player: (0, pitch, button if index < 5 else 0)})

    def test_coach_gun_resolves_all_fifteen_pellets(self):
        run, player, enemy = self.fight()
        queued_before = [len(viewer.client.entities) for viewer in (player, enemy)]
        self.press(run, player, 8)
        self.assertEqual(run.match.combat.vitals[enemy.body].total, 110.0)
        hits = player.client.messages(combat.HIT)
        self.assertEqual(len(hits), 15)
        self.assertEqual([hit["+0x78"]["+0x4"] for hit in hits], [6] * 15)
        self.assertEqual({hit["+0x78"]["+0x0"]["+0x0"] for hit in hits}, {enemy.body})

        for viewer, before in zip((player, enemy), queued_before, strict=True):
            payloads = [
                record.build(0).getvalue()
                for record in viewer.client.entities[before:]
                if record.entity == enemy.body and record.op == world.OP_UPDATE
            ]
            health, = (payload for payload in payloads if payload[:2] == bytes((1, 51)))
            mask_end = 3 + health[2]
            self.assertEqual(int.from_bytes(health[3:mask_end], "little"), 1)
            pool_id, maximum, current, kind = struct.unpack_from("<IffI", health, mask_end)
            self.assertEqual((pool_id, maximum, current, kind), (1, 200, 110, world.HEALTH_POOL))

    def test_coach_gun_spread_can_miss_at_distance(self):
        run, player, enemy = self.fight(distance=10)
        self.press(run, player, 8)
        damage = 200 - run.match.combat.vitals[enemy.body].total
        self.assertGreater(damage, 6)
        self.assertLess(damage, 90)

    def test_dead_ashe_cannot_fire(self):
        run, player, enemy = self.fight()
        run.match.combat.vitals[enemy.body] = combat.Vitals(enemy.body, 200)
        target = next(item for item in run.match.combat._targets() if item.owner is player)
        run.match.combat._damage(enemy, target, 1000, False, None, run.frame)
        self.press(run, player, 8)
        self.assertTrue(player.body_script.component.owner_dead)
        self.assertEqual(run.match.combat.vitals[enemy.body].total, 200)
        self.assertEqual(player.client.messages(combat.HIT), [])

    def test_coach_gun_armour_reduction_is_per_pellet(self):
        run, player, enemy = self.fight()
        vitals = combat.Vitals(enemy.body, 200, armour=100)
        run.match.combat.vitals[enemy.body] = vitals
        self.press(run, player, 8)
        self.assertEqual(vitals.now[world.ARMOUR_POOL], 55)
        self.assertEqual(vitals.now[world.HEALTH_POOL], 200)

    def test_coach_gun_shields_then_health(self):
        run, player, enemy = self.fight()
        vitals = combat.Vitals(enemy.body, 200, shields=25)
        run.match.combat.vitals[enemy.body] = vitals
        self.press(run, player, 8)
        self.assertEqual(vitals.now[world.SHIELDS_POOL], 0)
        self.assertEqual(vitals.now[world.HEALTH_POOL], 135)

    def test_coach_gun_does_not_damage_allies(self):
        run, player, enemy = self.fight(team_game=True)
        enemy.team = player.team
        run.match.combat.vitals[enemy.body] = combat.Vitals(enemy.body, 200)
        self.press(run, player, 8)
        self.assertEqual(run.match.combat.vitals[enemy.body].total, 200)
        self.assertEqual(player.client.messages(combat.HIT), [])

    def test_coach_gun_stops_each_pellet_at_static_collision(self):
        run, player, enemy = self.fight(distance=2)
        run.match.collision_world = World(FLOOR + wall(-10, 1, 10, 1))
        with patch.object(combat, "ray_world", wraps=combat.ray_world) as cast:
            self.press(run, player, 8)
        self.assertEqual(run.match.combat.vitals[enemy.body].total, 200)
        self.assertEqual(cast.call_count, 15)
        self.assertEqual(player.client.messages(combat.HIT), [])
        self.assertLess(player.mover.position[2], -1)
        self.assertEqual(enemy.mover.position[2], 2)

    def test_coach_gun_kills_once_without_hitting_the_corpse_again(self):
        run, player, enemy = self.fight()
        run.match.combat.vitals[enemy.body] = combat.Vitals(enemy.body, 6)
        self.press(run, player, 8)
        self.assertTrue(run.match.combat.vitals[enemy.body].dead)
        self.assertEqual(len(player.client.messages(combat.HIT)), 1)
        self.assertEqual(len(player.client.messages(combat.KILL_NOTICE)), 1)
        # Terminal pellets must not issue repeated notices or damage a dead body.
        self.assertEqual(len(run.match.combat.respawns), 1)

    def test_coach_gun_kills_retained_stock_npc_without_respawn(self):
        run = Run()
        player = run.players[0]
        run.match.switch_hero(player, heroes()[ASHE])
        bot = run.match.bots[0]
        run.match.bots = [bot]
        bot.position = (0.0, run.match.game_map.spawns[0].position[1], 1.0)
        bot.route = []
        bot.team = 1 - player.team
        bot.retain_corpse = True
        player.mover.position[:] = [0.0, 0.0, 0.0]
        run.match.combat.vitals[bot.entity] = combat.Vitals(bot.entity, 6)
        for _ in range(100):
            run.step({})
        self.press(run, player, 8)
        self.assertTrue(bot.dead)
        self.assertEqual(run.match.combat.respawns, [])
        self.assertIn(bot, run.match.bots)

    def test_viper_hip_ads_and_melee_use_real_command_reports(self):
        for mode, damage in (("hip", 40), ("ads", 75), ("melee", 30)):
            with self.subTest(mode=mode):
                run, player, enemy = self.fight(distance=1 if mode == "melee" else 10)
                _, torso_pitch = aim(run.match.combat._eye(player),
                                    (0, enemy.mover.position[1] + 1, enemy.mover.position[2]))
                if mode == "ads":
                    for _ in range(40):
                        run.step({player: (0, torso_pitch, 2)})
                    for _ in range(20):
                        run.step({player: (0, torso_pitch, 3)})
                else:
                    self.press(run, player, 0x800 if mode == "melee" else 1, pitch=torso_pitch)
                self.assertEqual(run.match.combat.vitals[enemy.body].total, 200 - damage)

    def test_ashe_receives_enemy_player_weapon_damage(self):
        run, player, enemy = self.fight(distance=10)
        yaw, pitch = aim((0, 1.55, 10), (0, 1, 0))
        for _ in range(5):
            run.step({enemy: (yaw, pitch, 1)})
        self.assertEqual(run.match.combat.vitals[player.body].total, 180)

    def test_skill_cooldown_starts_rejects_spam_and_expires(self):
        for graph_id, button, seconds in ((0x1513, 8, 10), (0x14FD, 16, 12)):
            with self.subTest(graph_id=graph_id):
                run, player, enemy = self.fight()
                run.match.combat.vitals[enemy.body] = combat.Vitals(enemy.body, 10000)
                ability = next(state for item in player.body_script.component.instances.values()
                               if item.graph.index == graph_id for state in item.states.values()
                               if isinstance(state, nodes.Ability))
                with patch.object(run.match.combat, "_shoot", wraps=run.match.combat._shoot) as shoot:
                    self.press(run, player, button, frames=60)
                    self.assertEqual(shoot.call_count, 1)
                    self.assertEqual(ability.cur, seconds)
                    self.assertGreater(ability.remaining(), 0)
                    for _ in range(8):
                        self.press(run, player, button)
                    self.assertEqual(shoot.call_count, 1)
                    for _ in range(math.ceil(ability.remaining() / combat.TICK) + 1):
                        run.step({})
                    self.assertEqual(ability.remaining(), 0)
                    self.assertEqual(shoot.call_count, 1)
                    self.press(run, player, button, frames=60)
                    self.assertEqual(shoot.call_count, 2)

    def test_viper_empty_magazine_and_per_round_reload(self):
        run, player, enemy = self.fight()
        enemy.mover.position[0] = 100  # ammunition must be spent on misses, too
        weapon = next(item for item in player.body_script.component.instances.values()
                      if item.graph.index == 0x14FC)
        with patch.object(run.match.combat, "_shoot", wraps=run.match.combat._shoot) as shoot:
            for _ in range(11):
                self.press(run, player, 1, frames=80)
            self.press(run, player, 1, frames=5)
            self.assertEqual(weapon.vars[53].value(), 0)
            self.assertEqual(shoot.call_count, 12)
            run.step({})
            self.press(run, player, 1, frames=5)
            self.assertEqual(shoot.call_count, 12)
        self.press(run, player, 0x400, frames=50)
        self.assertGreater(weapon.vars[53].value(), 0)
        self.assertLess(weapon.vars[53].value(), 12)
        for _ in range(300):
            run.step({})
        self.assertEqual(weapon.vars[53].value(), 12)

    def test_bob_charge_gate_and_consumption(self):
        run, player, _enemy = self.fight()
        component = player.body_script.component
        player.body_script.set_ult_charge(0)
        self.press(run, player, 32, frames=60)
        self.assertGreater(component.entity_var(342).value(), 0)  # passive charge advances
        cost = component.entity_var(338).value()
        self.assertEqual(cost, 2240)
        player.body_script.set_ult_charge(cost)
        self.press(run, player, 32, frames=60)
        self.assertLess(component.entity_var(342).value(), cost / 10)
        # This proves resource gating/consumption only: B.O.B. actor/attacks remain OPEN.

    def test_melee_player_visibility_wall_ally_and_range_policy(self):
        from test_ashe_impulses import wall_scene

        for case, distance, expected in (("visible", 2, 30), ("wall", 2, 0),
                                         ("ally", 2, 0), ("range", 5, 0)):
            with self.subTest(case=case):
                run, player, enemy = self.fight(distance=distance, team_game=case == "ally")
                if case == "wall":
                    run.match.collision_world = wall_scene(1)
                if case == "ally":
                    enemy.team = player.team
                target = next(item for item in run.match.combat._targets() if item.owner is enemy)
                vitals = run.match.combat.vitals_of(target)
                _yaw, pitch = aim(run.match.combat._eye(player),
                                   (0, enemy.mover.position[1] + 1, distance))
                self.press(run, player, 0x800, frames=40, pitch=pitch)
                self.assertEqual(200 - vitals.total, expected)
                self.assertEqual(len(player.client.messages(combat.HIT)), int(expected > 0))

    def test_damage_charges_bob_from_actual_pool_loss_and_caps_at_cost(self):
        for armour, initial, expected_gain in ((0, 0, 90), (100, 0, 45), (0, 2220, 20)):
            with self.subTest(armour=armour, initial=initial):
                run, player, enemy = self.fight()
                player.body_script.ult_seconds = 0  # isolate damage from passive charge
                player.body_script.set_ult_charge(initial)
                run.match.combat.vitals[enemy.body] = combat.Vitals(enemy.body, 200, armour=armour)
                self.press(run, player, 8)
                self.assertEqual(player.body_script.component.entity_var(342).value(),
                                 initial + expected_gain)

    def test_fixed_pattern_is_unit_length_and_rotates_with_vertical_aim(self):
        _run, player, _ = self.fight()
        instance = next(item for item in player.body_script.component.instances.values()
                        if item.graph.index == 0x1513)
        state = instance.state(16)
        for direction in ((0, 0, 1), (0, 1, 0), (1, 0, 0)):
            rays = ashe.coach_directions(state, direction)
            self.assertEqual(len(rays), 15)
            self.assertEqual(rays[0], direction)
            for ray in rays:
                self.assertAlmostEqual(math.sqrt(sum(value * value for value in ray)), 1)

    def test_viper_critical_multiplier_hip_and_ads(self):
        for ads, expected in ((False, 120), (True, 50)):
            with self.subTest(ads=ads):
                run, player, enemy = self.fight(distance=10)
                if ads:
                    for _ in range(40):
                        run.step({player: (0, 0, 2)})
                    for _ in range(20):
                        run.step({player: (0, 0, 3)})
                else:
                    self.press(run, player, 1)
                self.assertEqual(run.match.combat.vitals[enemy.body].total, expected)
                self.assertTrue(player.client.messages(combat.HIT)[0]["+0x78"]["+0x8"] & combat.CRITICAL)

    def test_dynamite_contact_waits_for_fuse_or_shot(self):
        run, player, enemy = self.fight()
        run.match.combat.vitals[enemy.body] = combat.Vitals(enemy.body, 200)
        self.press(run, player, 16)
        self.assertEqual(run.match.combat.vitals[enemy.body].total, 200)


if __name__ == "__main__":
    unittest.main()
