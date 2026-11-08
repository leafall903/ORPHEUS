"""Actual Tracer ultimate input drives reconstructed bomb physics/damage lifecycle."""

import unittest
from dataclasses import replace

import test_game_tracer as tracer_tests

from ow174.game import combat, stats
from ow174.game.collision import World
from ow174.game.content import SOLDIER, heroes
from ow174.game.script import expr


class PulseBombTests(tracer_tests.TracerCommandFixture, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.fight = self.run.match.combat
        self.player.body_script.ult_seconds = 0  # isolate resource spending from passive gain
        self.target = next(t for t in self.fight._targets() if t.owner is self.enemy)
        self.vitals = self.fight.vitals_of(self.target)
        self.cost = expr.to_float(self.player.body_script.component.entity_var(338).value())
        self.manager = self.fight.pulse_bombs

    def launch(self, pitch=0):
        self.player.body_script.set_ult_charge(self.cost)
        self.step(32, pitch=pitch)
        for _ in range(15):
            if self.manager.bombs:
                break
            self.step(pitch=pitch)  # actual authored0.15-second release delay
        self.assertEqual(len(self.manager.bombs), 1)
        return self.manager.bombs[0]

    def contact(self, bomb):
        for _ in range(60):
            if bomb.stuck:
                return
            self.step()
        self.fail("authored bomb did not contact the body/ground in one second")

    def test_undercharged_rejected_then_spends_exact_cost_and_cannot_repeat(self):
        self.player.body_script.set_ult_charge(self.cost - 1)
        self.step(32)
        self.assertEqual(self.manager.bombs, [])
        self.assertEqual(self.player.body_script.component.entity_var(342).value(), self.cost - 1)
        self.step()
        bomb = self.launch()
        self.assertEqual(self.player.body_script.component.entity_var(342).value(), 0)
        for _ in range(10):
            self.step()
            self.step(32)
        self.assertEqual(self.manager.bombs, [bomb])

    def test_contact_follows_target_and_waits_for_fuse_before_350_blast(self):
        bomb = self.launch()
        self.contact(bomb)
        self.assertEqual(bomb.attached, self.enemy.body)
        self.assertEqual(self.vitals.total, 195)
        self.assertEqual(bomb.blast.amount, 350)
        self.assertGreater(bomb.fuse_left, 0.98)
        before = bomb.position
        self.enemy.mover.position[0] += 2
        self.step()
        self.assertAlmostEqual(bomb.position[0] - before[0], 2)
        self.wait(45)
        self.assertEqual(self.vitals.total, 195)
        self.wait(25)
        self.assertTrue(self.vitals.dead)
        self.assertEqual(self.manager.bombs, [])
        self.assertEqual(len(self.player.client.messages(combat.KILL_NOTICE)), 1)

    def test_miss_lands_without_contact_damage_and_expires_once(self):
        self.enemy.mover.position[0] += 100
        bomb = self.launch()
        self.contact(bomb)
        self.assertIsNone(bomb.attached)
        self.assertEqual(self.vitals.total, 200)
        self.wait(80)
        self.assertEqual(self.manager.bombs, [])
        self.assertEqual(self.vitals.total, 200)
        self.assertEqual(self.player.client.messages(combat.HIT), [])

    def test_allies_are_not_stuck_or_damaged(self):
        self.run.match.game_map = replace(self.run.match.game_map, free_for_all=False)
        self.enemy.team = self.player.team
        bomb = self.launch()
        self.contact(bomb)
        self.assertIsNone(bomb.attached)
        self.wait(80)
        self.assertEqual(self.vitals.total, 200)

    def test_real_wall_blocks_blast_to_a_nearby_enemy(self):
        from test_game_mover import wall

        x, y, z = self.player.mover.position
        floor = [(-100, y, -100, 100, y, 100, 100, y, -100),
                 (-100, y, -100, -100, y, 100, 100, y, 100)]
        collision = World(floor + wall(x - 5, z + 3, x + 5, z + 3, y, y + 5))
        self.run.match.collision_world = collision
        self.player.mover.use(world=collision)
        bomb = self.launch()
        self.contact(bomb)
        self.assertIsNone(bomb.attached)
        self.assertLess(bomb.position[2], z + 3)
        self.wait(80)
        self.assertEqual(self.vitals.total, 200)

    def test_self_explosion_kills_without_self_damage_credit_or_elimination(self):
        self.enemy.mover.position[0] += 100
        self.launch(pitch=16384)
        self.wait(90)
        self.assertTrue(self.fight.is_dead(self.player))
        self.assertEqual(self.player.stats.value(stats.HERO_DAMAGE), 0)
        self.assertEqual(self.player.stats.value(stats.ELIMINATIONS), 0)
        self.assertEqual(self.player.stats.value(stats.DEATHS), 1)
        self.assertEqual(self.player.client.messages(combat.HIT), [])
        self.assertEqual(len(self.player.client.messages(combat.DEATH_NOTICE)), 1)

    def test_owner_death_does_not_cancel_an_already_thrown_bomb(self):
        bomb = self.launch()
        self.contact(bomb)
        owner = next(t for t in self.fight._targets() if t.owner is self.player)
        self.fight._damage(self.enemy, owner, 200, False, None, self.run.frame)
        self.assertTrue(self.fight.is_dead(self.player))
        self.wait(80)
        self.assertTrue(self.vitals.dead)
        self.assertEqual(self.player.stats.value(stats.ELIMINATIONS), 1)

    def test_source_body_retirement_cancels_without_detonating(self):
        bomb = self.launch()
        self.contact(bomb)
        self.run.match.switch_hero(self.player, self.player.hero)
        self.assertEqual(self.manager.bombs, [])
        self.wait(80)
        self.assertEqual(self.vitals.total, 195)

    def test_recall_detaches_the_bomb_and_prevents_explosion_damage(self):
        self.run.match.switch_hero(self.enemy, heroes()[tracer_tests.TRACER])
        x, y, z = self.player.mover.position
        self.enemy.mover.position = [x, y, z + 5]
        target = next(t for t in self.fight._targets() if t.owner is self.enemy)
        vitals = self.fight.vitals_of(target)
        self.run.step({})  # record the target's pre-hit history
        bomb = self.launch()
        self.contact(bomb)
        self.assertEqual(bomb.attached, self.enemy.body)
        self.run.step({self.enemy: (0, 0, 16)})
        self.step()
        self.assertIsNone(bomb.attached)
        for _ in range(90):
            self.run.step({})
        self.assertEqual(vitals.total, 150)
        self.assertFalse(vitals.dead)

    def test_scheduler_gap_does_not_duplicate_or_extend_a_live_bomb(self):
        bomb = self.launch()
        self.step()  # admit the new shot to the world clock
        tick = self.run.frame + 150
        self.run.match.update(tick * combat.TICK, tick)
        self.assertNotIn(bomb, self.manager.bombs)
        self.assertTrue(self.vitals.dead)
        self.assertEqual(self.player.stats.value(stats.ELIMINATIONS), 1)
        self.assertEqual(self.player.stats.value(stats.HERO_DAMAGE), 200)

    def test_actual_pve_mode_branch_deals_450_instead_of_default_350(self):
        self.run.match.game_map = replace(self.run.match.game_map, mode_guid=0x0230000000000025)
        self.run.match.switch_hero(self.player, self.player.hero)
        self.player.body_script.ult_seconds = 0
        self.vitals = combat.Vitals(self.enemy.body, 1000)
        self.fight.vitals[self.enemy.body] = self.vitals
        bomb = self.launch()
        self.contact(bomb)
        self.assertEqual(bomb.blast.amount, 450)
        self.wait(80)
        self.assertEqual(self.vitals.total, 545)

    def test_three_metre_blast_surface_distance_deals_210_damage(self):
        from test_game_combat import in_world

        bomb = self.launch(pitch=-800)
        self.contact(bomb)
        third = self.run.match.add_player(3, "Ring target", SOLDIER, 1, False)
        in_world(self.run.match, third)
        self.run.match.switch_hero(third, third.hero)
        # Sphere-to-capsule surface distance3m, within the2..4m falloff band.
        third.mover.position = [bomb.position[0] + 3.5, self.player.mover.position[1], bomb.position[2]]
        self.assertGreater(bomb.position[1], third.mover.position[1] + 0.5)
        self.assertLess(bomb.position[1], third.mover.position[1] + 1.5)
        vitals = combat.Vitals(third.body, 500)
        self.fight.vitals[third.body] = vitals
        self.wait(80)
        self.assertAlmostEqual(vitals.total, 290, places=4)

    def test_ultimate_ends_and_can_be_used_again_after_recharging(self):
        self.launch()
        self.wait(210)
        script = self.player.body_script
        ability = next(i for i in script.component.instances.values()
                       if i.graph.index == 0x34).state(9)
        self.assertFalse(ability.flags & ability.RUNNING)
        self.assertEqual(self.manager.bombs, [])
        self.launch()
        self.assertEqual(script.component.entity_var(342).value(), 0)

    def test_cancel_before_release_spends_charge_but_does_not_launch(self):
        script = self.player.body_script
        script.set_ult_charge(self.cost)
        self.step(32)
        ability = next(i for i in script.component.instances.values()
                       if i.graph.index == 0x34).state(9)
        self.assertTrue(ability.flags & ability.RUNNING)
        ability.stop()
        self.wait(30)
        self.assertEqual(self.manager.bombs, [])
        self.assertEqual(script.component.entity_var(342).value(), 0)
        self.assertEqual(self.vitals.total, 200)

    def test_dead_body_cannot_cast_even_with_full_charge(self):
        self.wait(2)  # establish the actual world clock before creating a death deadline
        self.player.body_script.set_ult_charge(self.cost)
        owner = next(t for t in self.fight._targets() if t.owner is self.player)
        self.fight._damage(self.enemy, owner, 200, False, None, self.run.frame)
        self.step(32)
        self.wait(30)
        self.assertEqual(self.manager.bombs, [])
        self.assertEqual(self.player.body_script.component.entity_var(342).value(), self.cost)

    def test_death_during_windup_cancels_the_unreleased_projectile(self):
        self.wait(2)
        self.player.body_script.set_ult_charge(self.cost)
        self.step(32)
        self.assertEqual(self.manager.bombs, [])
        owner = next(t for t in self.fight._targets() if t.owner is self.player)
        self.fight._damage(self.enemy, owner, 200, False, None, self.run.frame)
        self.wait(30)
        self.assertEqual(self.manager.bombs, [])
        self.assertEqual(self.vitals.total, 200)

    def test_blink_during_windup_throws_from_the_new_body_position(self):
        self.player.body_script.set_ult_charge(self.cost)
        self.step(32)
        before = tuple(self.player.mover.position)
        self.step(8)
        self.assertAlmostEqual(self.player.mover.position[2] - before[2], 7.5, places=3)
        for _ in range(15):
            if self.manager.bombs:
                break
            self.step()
        self.assertEqual(len(self.manager.bombs), 1)
        self.assertEqual(self.manager.bombs[0].position, self.fight._eye(self.player))

    def test_no_contact_flight_is_bounded_by_authored_five_second_lifetime(self):
        self.enemy.mover.position[0] += 100
        collision = World([])
        self.run.match.collision_world = collision
        self.player.mover.use(world=collision)
        bomb = self.launch(pitch=-8000)
        self.wait(320)
        self.assertEqual(self.manager.bombs, [])
        self.assertAlmostEqual(bomb.age, 5, places=6)
        self.assertFalse(bomb.stuck)
        self.assertEqual(self.vitals.total, 200)

    def test_contact_and_blast_use_armour_and_shield_pools_separately(self):
        for armour, shields, expected in ((100, 0, 752.5), (0, 100, 745)):
            with self.subTest(armour=armour, shields=shields):
                self.setUp()
                vitals = combat.Vitals(self.enemy.body, 1000, armour=armour, shields=shields)
                self.fight.vitals[self.enemy.body] = vitals
                bomb = self.launch()
                self.contact(bomb)
                self.assertGreater(vitals.total, expected)
                self.wait(80)
                self.assertAlmostEqual(vitals.now[combat.HEALTH_POOL], expected)
                self.assertEqual(vitals.now[combat.ARMOUR_POOL], 0)
                self.assertEqual(vitals.now[combat.SHIELDS_POOL], 0)


if __name__ == "__main__":
    unittest.main()
