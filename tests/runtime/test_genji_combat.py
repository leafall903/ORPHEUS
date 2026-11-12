"""Command-path Genji shuriken damage; not original-client hero acceptance."""

import math
import unittest
from dataclasses import replace
from unittest.mock import patch

from test_game_combat import KANEZAKA_DM, Run, aim
from test_game_mover import FLOOR, wall

from ow174.game import bodytrace, bots, combat, movelog
from ow174.game.collision import World
from ow174.game.content import heroes

GENJI = 0x02E0000000000029


class GenjiFixture(unittest.TestCase):
    def setUp(self):
        self.enterContext(patch.object(bodytrace, "ENABLED", False))
        self.enterContext(patch.object(movelog, "ENABLED", False))

    def spawn(self, distance=0.8, names=("Genji", "Enemy"), *, passive_charge=False):
        run = Run(KANEZAKA_DM, names)
        run.match.bots.clear()
        player = run.players[0]
        run.match.switch_hero(player, heroes()[GENJI])
        if not passive_charge:
            # Damage/ability fixtures isolate resource credit from ambient charge.
            player.body_script.component.set(2426, False)
            player.body_script.component.run_now()
        player.mover.position[:] = [0.0, 1.0, 0.0]
        for enemy in run.players[1:]:
            enemy.mover.position[:] = [0.0, 1.0, distance]
        return run, player

    def fire(self, run, player, button):
        target = next(t for t in run.match.combat._targets() if t.owner is not player)
        _, y, z = target.feet
        x = player.mover.position[0]
        yaw, pitch = aim(run.match.combat._eye(player), (x, y + 1.0, z))
        for frame in range(60):
            run.step({player: (yaw, pitch, button if frame == 0 else 0)})

    def health(self, run, player):
        target = next(t for t in run.match.combat._targets() if t.owner is player)
        return run.match.combat.vitals_of(target).total


class GenjiShurikenTests(GenjiFixture):
    def test_primary_burst_deals_three_hits(self):
        run, player = self.spawn(4.0)
        self.fire(run, player, 1)
        self.assertEqual(self.health(run, run.players[1]), 113.0)
        self.assertEqual(len(player.client.messages(combat.HIT)), 3)

    def test_shuriken_select_the_nearest_eligible_collinear_player(self):
        for button in (1, 2):
            for nearer_slot in (1, 2):
                for nearer_is_ally in (False, True):
                    with self.subTest(button=button, nearer_slot=nearer_slot, ally=nearer_is_ally):
                        run, player = self.spawn(4.0, ("Genji", "First", "Second"))
                        run.match.game_map = replace(run.match.game_map, free_for_all=False)
                        near, far = run.players[nearer_slot], run.players[3 - nearer_slot]
                        far.mover.position[2] = 4.15
                        player.team = 0
                        near.team = 0 if nearer_is_ally else 1
                        far.team = 1

                        self.fire(run, player, button)

                        struck, untouched = (far, near) if nearer_is_ally else (near, far)
                        self.assertEqual(self.health(run, struck), 113.0)
                        self.assertEqual(self.health(run, untouched), 200.0)
                        hits = [notice["+0x78"] for notice in player.client.messages(combat.HIT)]
                        self.assertEqual(
                            [(hit["+0x0"]["+0x0"], hit["+0x4"]) for hit in hits],
                            [(struck.body, 29)] * 3,
                        )
                        self.assertEqual(player.body_script.component.entity_var(342).value(), 87)

    def test_primary_hits_from_inside_expanded_capsule_shell(self):
        for offset, expected in ((.55, 87), (.65, 0)):
            with self.subTest(offset=offset):
                run, player = self.spawn(0)
                enemy = run.players[1]
                enemy.mover.position[0] = offset
                for frame in range(35):
                    run.step({player: (0, 0, 1 if frame == 0 else 0)})
                self.assertEqual(self.health(run, enemy), 200 - expected)
                hits = player.client.messages(combat.HIT)
                self.assertEqual([hit["+0x78"]["+0x4"] for hit in hits], [29] * (expected // 29))

    def test_secondary_point_blank_deals_three_independent_hits(self):
        run, player = self.spawn()
        self.fire(run, player, 2)
        self.assertEqual(self.health(run, run.players[1]), 113.0)
        self.assertEqual(len(player.client.messages(combat.HIT)), 3)

    def test_secondary_distant_narrow_target_only_receives_centre(self):
        run, player = self.spawn(10.0)
        self.fire(run, player, 2)
        self.assertEqual(self.health(run, run.players[1]), 171.0)
        self.assertEqual(len(player.client.messages(combat.HIT)), 1)

    def test_secondary_fan_can_hit_three_different_enemies(self):
        run, player = self.spawn(10.0, ("Genji", "Left", "Centre", "Right"))
        for enemy, degrees in zip(run.players[1:], (-4.5, 0, 4.5), strict=True):
            enemy.mover.position[0] = math.tan(math.radians(degrees)) * 10
        self.fire(run, player, 2)
        self.assertEqual([self.health(run, p) for p in run.players[1:]], [171.0] * 3)

    def test_secondary_emits_native_fan_order_with_pitched_aim(self):
        run, player = self.spawn(names=("Genji",))
        pitch = -6712
        for frame in range(15):
            run.step({player: (0, pitch, 2 if frame == 0 else 0)})
            if run.match.combat.rockets:
                break
        directions = [rocket.direction for rocket in run.match.combat.rockets]
        self.assertEqual(len(directions), 3)
        right, centre, left = directions
        self.assertGreater(right[0], 0)
        self.assertLess(left[0], 0)
        self.assertAlmostEqual(right[0], -left[0])
        self.assertAlmostEqual(centre[0], 0)
        for outer in (right, left):
            self.assertAlmostEqual(sum(a * b for a, b in zip(outer, centre, strict=True)),
                                   math.cos(math.radians(4.5)))
        for axis in (1, 2):
            self.assertAlmostEqual(right[axis], left[axis])
            self.assertAlmostEqual(right[axis], centre[axis] * math.sqrt(1 - right[0] ** 2))

    def test_secondary_centre_wall_does_not_stop_outside_pellets(self):
        run, player = self.spawn(10.0, ("Genji", "Centre", "Left", "Right", "Ally"))
        centre, left, right, ally = run.players[1:]
        offset = math.tan(math.radians(4.5)) * 10
        left.mover.position[0], right.mover.position[0] = -offset, offset
        ally.mover.position[:] = [offset / 2, 1.0, 5.0]
        run.match.game_map = replace(run.match.game_map, free_for_all=False)
        player.team = ally.team = 0
        centre.team = left.team = right.team = 1
        run.match.collision_world = World(FLOOR + wall(-.25, 5, .25, 5))

        self.fire(run, player, 2)

        self.assertEqual([self.health(run, p) for p in (centre, left, right, ally)], [200, 171, 171, 200])
        self.assertEqual(len(player.client.messages(combat.HIT)), 2)
        self.assertEqual(player.body_script.component.entity_var(342).value(), 58)
        self.assertFalse(run.match.combat.rockets)

    def test_secondary_armour_mitigates_each_shuriken(self):
        run, player = self.spawn()
        enemy = run.players[1]
        run.match.combat.vitals[enemy.body] = combat.Vitals(enemy.body, 200, 100)
        self.fire(run, player, 2)
        self.assertEqual(self.health(run, enemy), 228.0)  # 3 * (29 - 5), not 87 - 5

    def test_secondary_respects_team_semantics(self):
        run, player = self.spawn()
        run.match.game_map = run.match.game_map.__class__(
            **{**run.match.game_map.__dict__, "free_for_all": False}
        )
        run.players[1].team = player.team
        self.fire(run, player, 2)
        self.assertEqual(self.health(run, run.players[1]), 200.0)

    def test_exact_flight_boundary_retires_on_next_step_without_a_second_sweep(self):
        run, player = self.spawn(names=("Genji",))
        fight = run.match.combat
        for frame in range(15):
            run.step({player: (0, 0, 2 if frame == 0 else 0)})
            if fight.rockets:
                break
        self.assertEqual(len(fight.rockets), 3)
        for rocket in fight.rockets:
            rocket.left = combat.TICK

        with patch.object(combat.interception, "absorption", wraps=combat.interception.absorption) as sweep:
            run.step({})
            self.assertEqual(sweep.call_count, 3)
            self.assertEqual([rocket.left for rocket in fight.rockets], [0.0, 0.0, 0.0])

            sweep.reset_mock()
            run.step({})
            sweep.assert_not_called()
        self.assertFalse(fight.rockets)

    def test_secondary_stops_at_static_collision(self):
        run, player = self.spawn(4.0)
        run.match.collision_world = World(FLOOR + wall(-5.0, 2.0, 5.0, 2.0))
        self.fire(run, player, 2)
        self.assertEqual(self.health(run, run.players[1]), 200.0)

    def test_primary_radius_clips_wall_edge_that_the_centreline_clears(self):
        for edge, expected in ((.05, 200), (.15, 113)):
            with self.subTest(edge=edge):
                run, player = self.spawn(4.0)
                run.match.collision_world = World(FLOOR + wall(edge, 2, 5, 2))
                self.fire(run, player, 1)
                self.assertEqual(self.health(run, run.players[1]), expected)
                self.assertFalse(run.match.combat.rockets)

    def test_projectile_sweep_hits_wall_before_expanded_body(self):
        run, player = self.spawn(4.0)
        target = next(t for t in run.match.combat._targets() if t.owner is run.players[1])
        depth = 4 - target.radius - .05
        run.match.collision_world = World(FLOOR + wall(-5, depth, 5, depth))
        self.fire(run, player, 1)
        self.assertEqual(self.health(run, run.players[1]), 200)
        self.assertFalse(player.client.messages(combat.HIT))

    def test_secondary_hits_a_real_training_bot_actor(self):
        run, player = self.spawn(names=("Genji",))
        hero_guid, data = next(iter(bots.table()["heroes"].items()))
        npc = bots.Bot(bots.BOT_ENTITY, {
            "hero": hero_guid, "team": 1, "position": [0, 1, 0.8],
            "rotation": [0, 0, 0, 1], "graphs": [],
        }, data, {})
        run.match.bots.append(npc)
        self.fire(run, player, 2)
        self.assertEqual(run.match.combat.vitals[npc.entity].total, data["health"] - 87)
        self.assertEqual(len(player.client.messages(combat.HIT)), 3)

    def test_dead_genji_cannot_fire_secondary(self):
        run, player = self.spawn()
        target = next(t for t in run.match.combat._targets() if t.owner is player)
        run.match.combat._damage(run.players[1], target, 200, False, None, run.frame)
        self.assertTrue(run.match.combat.is_dead(player))
        self.fire(run, player, 2)
        self.assertEqual(self.health(run, run.players[1]), 200.0)


if __name__ == "__main__":
    unittest.main()
