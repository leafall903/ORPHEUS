"""Command/collision checks for explicitly reconstructed Genji passive mobility."""

import unittest

from test_game_combat import aim
from test_game_mover import FLOOR, box, wall
from test_genji_combat import GENJI, GenjiFixture

from ow174.game import combat
from ow174.game.collision import World
from ow174.game.commands import Command
from ow174.game.content import SOLDIER, heroes
from ow174.game.mover import JUMP_INTENT, SET, Mod
from ow174.game.script import driver, expr, graph, runtime


class GenjiClimbGraphTests(unittest.TestCase):
    def start_graph(self, values, *, world=None, strict=False):
        component = runtime.Component(123, world=world, strict=strict)
        instance = component.create(graph.graph(0x0157), init=False, values=values)
        instance.state(44).plug_enter("m_beginPlug")
        instance.drain()
        return instance

    def jump_edge(self, instance, previous, buttons):
        for edge in driver.button_edges(previous, buttons):
            instance.button_edge(*edge)
        instance.drain()

    def test_climb_relay_activates_switches_in_strict_vm(self):
        instance = self.start_graph({(expr.INSTANCE, 1347): True}, strict=True)
        self.jump_edge(instance, 0, 64)
        self.assertTrue(all(instance.state(index).active for index in (4, 7, 10, 37)))
        self.assertTrue({3, 5}.isdisjoint(instance.states))

    def test_jump_release_ends_both_authored_movement_subgraphs(self):
        instance = self.start_graph({(expr.ENTITY, 417): True})
        self.assertTrue(instance.state(37).active)
        self.assertFalse(any(instance.state(index).active for index in (3, 5)))
        self.jump_edge(instance, 0, 64)
        self.assertTrue(all(instance.state(index).active for index in (3, 5)))
        self.jump_edge(instance, 64, 0)
        self.assertFalse(any(instance.state(index).active for index in (3, 5)))

    def test_bypass_and_latch_recovery_use_separate_graph_conditions(self):
        class ClimbInputWorld(runtime.World):
            def native(self, cfg, instance):
                if cfg.get("$") == "STU_021D01CD":
                    return expr.Vec3(0, 0, 1)
                if cfg.get("$") == "STU_310D6529":
                    return expr.Vec3(0, 1, 0)
                return super().native(cfg, instance)

        instance = self.start_graph({
            (expr.ENTITY, 7642): True,
            (expr.ENTITY, 417): True,
            (expr.INSTANCE, 1207): expr.Vec3(0, 0, -1),
        }, world=ClimbInputWorld())
        self.assertFalse(instance.state(37).active)
        self.assertTrue(all(instance.state(index).active for index in (3, 5)))
        self.jump_edge(instance, 0, 64)
        self.jump_edge(instance, 64, 0)
        self.assertTrue(all(instance.state(index).active for index in (3, 5)))

        instance.write((expr.ENTITY, 1204), True)
        instance.drain()
        self.assertTrue(instance.state(3).active)
        instance.write((expr.INSTANCE, 1207), expr.Vec3(0, 0, 1))
        instance.drain()
        self.assertIs(instance.find_var(expr.INSTANCE, 1347).value(), True)
        self.assertFalse(instance.state(3).active)
        self.assertTrue(instance.state(5).active)

        instance.write((expr.ENTITY, 1204), False)
        instance.drain()
        self.assertTrue(instance.find_var(expr.INSTANCE, 1347).value())
        instance.write((expr.ENTITY, 545), True)
        instance.drain()
        self.assertIs(instance.find_var(expr.INSTANCE, 1347).value(), False)
        self.assertTrue(instance.state(3).active)
        instance.write((expr.ENTITY, 1956), True)
        instance.drain()
        self.assertFalse(any(instance.state(index).active for index in (3, 5)))


class GenjiMobilityTests(GenjiFixture):
    def step(self, run, player, *, buttons=0, forward=0, yaw=0, pitch=0):
        run.frame += 1
        run.match.update(run.frame * 0.016, run.frame)
        player.take_commands([Command(run.frame, buttons=buttons, forward=forward, yaw=yaw, pitch=pitch)])

    def airborne(self, run, player):
        for frame in range(20):
            self.step(run, player, buttons=64 if frame == 0 else 0)
        self.assertTrue(player.mover.airborne)

    def wall_scene(self, ceiling=False):
        run, player = self.spawn()
        geometry = FLOOR + wall(-5, 0.8, 5, 0.8, y1=20)
        if ceiling:
            geometry += box(-5, 5, 4, 5, -5, 5)
        run.match.collision_world = World(geometry)
        return run, player

    def test_second_press_adds_one_authored_jump_impulse(self):
        run, player = self.spawn()
        self.airborne(run, player)
        before = player.mover.position[1]
        self.step(run, player, buttons=64)
        self.assertTrue(player.genji_mobility.air_jump_used)
        self.assertGreater(player.mover.velocity[1], 5)
        self.assertGreater(player.mover.position[1], before)

    def test_air_jump_and_primary_share_one_input_without_losing_burst(self):
        run, player = self.spawn(4)
        enemy = run.players[1]
        self.airborne(run, player)
        for frame in range(24):
            yaw, pitch = aim(run.match.combat._eye(player), (0, 1.85, 4))
            run.step({player: (yaw, pitch, 65 if frame == 0 else 0)})
            self.assertTrue(player.genji_mobility.air_jump_used)
            self.assertTrue(player.mover.airborne)
        hits = player.client.messages(combat.HIT)
        self.assertEqual([hit["+0x78"]["+0x4"] for hit in hits], [29] * 3)
        self.assertEqual(self.health(run, enemy), 113)
        self.assertEqual(player.body_script.component.entity_var(342).value(), 87)

    def test_holding_jump_does_not_repeat_in_air(self):
        run, player = self.spawn()
        highest = 0
        for _ in range(40):
            self.step(run, player, buttons=64)
            highest = max(highest, player.mover.position[1])
        self.assertFalse(player.genji_mobility.air_jump_used)
        self.assertLess(highest, 2.2)

    def test_third_press_cannot_add_another_impulse(self):
        run, player = self.spawn()
        self.airborne(run, player)
        self.step(run, player, buttons=64)
        for _ in range(14):
            self.step(run, player)
        self.step(run, player, buttons=64)
        self.assertLess(player.mover.velocity[1], 2)
        self.assertTrue(player.genji_mobility.air_jump_used)

    def test_landing_refreshes_double_jump(self):
        run, player = self.spawn()
        script = player.body_script.component
        self.step(run, player)
        self.assertFalse(player.mover.airborne)
        self.assertIs(script.entity_var(driver.GENJI_ON_GROUND).value(), True)
        self.airborne(run, player)
        self.assertIs(script.entity_var(driver.GENJI_IN_AIR).value(), True)
        self.assertIs(script.entity_var(driver.GENJI_ON_GROUND).value(), False)
        self.step(run, player, buttons=64)
        for _ in range(120):
            self.step(run, player)
        self.assertFalse(player.mover.airborne)
        self.assertIs(script.entity_var(driver.GENJI_IN_AIR).value(), False)
        self.assertIs(script.entity_var(driver.GENJI_ON_GROUND).value(), True)
        self.assertFalse(player.genji_mobility.air_jump_used)
        self.airborne(run, player)
        self.step(run, player, buttons=64)
        self.assertGreater(player.mover.velocity[1], 5)

    def test_other_hero_does_not_get_genji_air_jump(self):
        run, player = self.spawn()
        run.match.switch_hero(player, heroes()[SOLDIER])
        player.mover.position[:] = [0, 1, 0]
        self.airborne(run, player)
        self.step(run, player, buttons=64)
        self.assertLess(player.mover.velocity[1], 1)

    def test_jump_intent_block_does_not_queue_a_later_air_jump(self):
        run, player = self.spawn()
        self.airborne(run, player)
        key = (999, 1, 0)
        player.mover.mods[key] = Mod(JUMP_INTENT, SET, 0, run.frame + 1, key=key)
        self.step(run, player, buttons=64)
        self.assertFalse(player.genji_mobility.air_jump_used)
        self.step(run, player, buttons=64)
        self.assertFalse(player.genji_mobility.air_jump_used)

    def test_new_body_resets_passive_resources(self):
        run, player = self.spawn()
        self.airborne(run, player)
        self.step(run, player, buttons=64)
        run.match.switch_hero(player, heroes()[GENJI])
        self.assertFalse(player.genji_mobility.air_jump_used)
        self.assertFalse(player.genji_mobility.jump_held)
        self.assertFalse(player.genji_mobility.climb_spent)

    def test_death_stops_air_mobility(self):
        run, player = self.spawn(4)
        enemy = run.players[1]
        fight = run.match.combat
        for frame in range(65):
            x, y, z = player.mover.position
            yaw, pitch = aim(fight._eye(enemy), (x, y + 1.3, z))
            run.step({
                player: (0, 0, 64 if frame in (0, 20) else 0),
                enemy: (yaw, pitch, 1 if frame >= 12 else 0),
            })
            if fight.is_dead(player):
                break
        self.assertTrue(fight.is_dead(player))
        self.assertEqual(fight.vitals[player.body].killer, enemy.body)
        self.assertTrue(player.mover.airborne)
        position = tuple(player.mover.position)
        self.step(run, player, buttons=64)
        self.assertEqual(tuple(player.mover.position), position)

    def test_rifle_kill_during_wall_climb_stops_motion_until_respawn(self):
        run, player = self.wall_scene()
        enemy = run.players[1]
        enemy.mover.position[:] = [0, 1, -4]
        player.spawn = ((0, 1, 0), 0)
        old_body = player.body
        fight = run.match.combat
        yaw, pitch = aim(fight._eye(enemy), (0, 2.3, 0))
        for _ in range(30):
            run.step({enemy: (yaw, pitch, 1)})
            if self.health(run, player) <= 80:
                break
        self.assertGreater(self.health(run, player), 0)
        self.assertLessEqual(self.health(run, player), 80)
        for _ in range(12):
            self.step(run, player, buttons=64, forward=127)
        self.assertTrue(player.genji_mobility.climbing)

        for _ in range(32):
            self.step(run, player, buttons=64, forward=127)
            self.assertTrue(player.genji_mobility.climbing)
            x, y, z = player.mover.position
            yaw, pitch = aim(fight._eye(enemy), (x, y + 1.3, z))
            enemy.take_commands([Command(run.frame, yaw=yaw, pitch=pitch, buttons=1)])
            if fight.is_dead(player):
                break
        self.assertTrue(fight.is_dead(player))
        self.assertEqual(fight.vitals[old_body].killer, enemy.body)
        position = tuple(player.mover.position)
        for _ in range(10):
            self.step(run, player, buttons=64, forward=127)
            self.assertEqual(tuple(player.mover.position), position)

        for _ in range(650):
            run.step({})
            if player.body != old_body:
                break
        self.assertNotEqual(player.body, old_body)
        self.assertEqual(self.health(run, player), 200)
        self.assertFalse(player.genji_mobility.climb_spent)
        self.assertEqual(player.genji_mobility.climb_elapsed, 0)
        for _ in range(12):
            self.step(run, player, buttons=64, forward=127)
        self.assertTrue(player.genji_mobility.climbing)

    def test_wall_climb_opens_primary_line_over_low_cover(self):
        run, player = self.spawn(4)
        enemy = run.players[1]
        run.match.collision_world = World(FLOOR + wall(-5, .5, 5, .5, y1=2.6))
        point = (0, 1.85, 4)
        yaw, pitch = aim(run.match.combat._eye(player), point)
        for frame in range(60):
            self.step(run, player, yaw=yaw, pitch=pitch, buttons=1 if frame == 0 else 0)
        self.assertEqual(self.health(run, enemy), 200)
        self.assertFalse(player.client.messages(combat.HIT))

        for _ in range(25):
            self.step(run, player, buttons=64, forward=127)
            if player.genji_mobility.climbing and player.mover.position[1] >= 1.55:
                break
        self.assertTrue(player.genji_mobility.climbing)
        for frame in range(24):
            yaw, pitch = aim(run.match.combat._eye(player), point)
            self.step(run, player, yaw=yaw, pitch=pitch, forward=127,
                      buttons=65 if frame == 0 else 64)
            if frame == 0:
                self.assertTrue(player.genji_mobility.climbing)
        hits = player.client.messages(combat.HIT)
        self.assertEqual([hit["+0x78"]["+0x4"] for hit in hits], [29])
        self.assertEqual(self.health(run, enemy), 171)
        component = player.body_script.component
        weapon = next(i for i in component.instances.values() if i.graph.index == 0x0304)
        self.assertEqual(weapon.vars[53].value(), 24)
        self.assertEqual(component.entity_var(342).value(), 29)

    def test_wall_climb_uses_authored_speed_and_exhausts(self):
        run, player = self.wall_scene()
        velocities = []
        for _ in range(80):
            self.step(run, player, buttons=64, forward=127)
            if player.genji_mobility.climbing:
                velocities.append(player.mover.velocity[1])
        self.assertGreater(len(velocities), 40)
        self.assertTrue(all(abs(v - 7.5) < 0.01 for v in velocities))
        self.assertTrue(player.genji_mobility.climb_spent)
        self.assertFalse(player.genji_mobility.climbing)
        self.assertGreaterEqual(player.genji_mobility.climb_elapsed, 0.75)
        self.assertLess(player.genji_mobility.climb_elapsed, 0.77)

    def test_without_wall_jump_does_not_climb(self):
        run, player = self.spawn()
        for _ in range(40):
            self.step(run, player, buttons=64, forward=127)
        self.assertFalse(player.genji_mobility.climbing)
        self.assertEqual(player.genji_mobility.climb_elapsed, 0)

    def test_wall_behind_or_no_forward_does_not_climb(self):
        for forward, yaw in ((0, 0), (127, -32768)):
            with self.subTest(forward=forward, yaw=yaw):
                run, player = self.wall_scene()
                for _ in range(40):
                    self.step(run, player, buttons=64, forward=forward, yaw=yaw)
                self.assertEqual(player.genji_mobility.climb_elapsed, 0)

    def test_release_cancels_climb_and_refreshes_one_air_jump(self):
        run, player = self.wall_scene()
        for _ in range(20):
            self.step(run, player, buttons=64, forward=127)
        self.assertTrue(player.genji_mobility.climbing)
        self.step(run, player)
        self.assertFalse(player.genji_mobility.climbing)
        self.assertTrue(player.genji_mobility.climb_spent)
        self.step(run, player, buttons=64)
        self.assertTrue(player.genji_mobility.air_jump_used)
        self.assertGreater(player.mover.velocity[1], 5)

    def test_ceiling_collision_prevents_climbing_through_geometry(self):
        run, player = self.wall_scene(ceiling=True)
        highest = 0
        for _ in range(80):
            self.step(run, player, buttons=64, forward=127)
            highest = max(highest, player.mover.position[1])
        self.assertLess(highest, 2.2)

    def test_missing_command_gap_cannot_extend_climb_duration(self):
        run, player = self.wall_scene()
        for _ in range(20):
            self.step(run, player, buttons=64, forward=127)
        self.assertTrue(player.genji_mobility.climbing)
        run.frame += 200
        self.step(run, player, buttons=64, forward=127)
        self.assertFalse(player.genji_mobility.climbing)
        self.assertTrue(player.genji_mobility.climb_spent)


if __name__ == "__main__":
    unittest.main()
