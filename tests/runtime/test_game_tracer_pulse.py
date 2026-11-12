"""Actual Tracer ultimate input drives reconstructed bomb physics/damage lifecycle."""

import unittest
from dataclasses import replace

import test_game_tracer as tracer_tests

from ow174.game import combat, stats, tracer
from ow174.game.collision import World
from ow174.game.commands import CROUCH, Command
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

    def test_pulse_bomb_contact_uses_target_tracers_stance_after_release(self):
        for crouch_at_contact in (False, True):
            with self.subTest(crouch_at_contact=crouch_at_contact):
                self.setUp()
                self.assertTrue(self.run.match.switch_hero(self.enemy, heroes()[tracer_tests.TRACER]))
                x, y, z = self.player.mover.position
                self.enemy.mover.position[:] = (x, y, z + 5)
                self.enemy.mover.body = None
                initial = 0 if crouch_at_contact else CROUCH
                for _ in range(12):
                    self.step()
                    self.enemy.take_commands([Command(self.run.frame, buttons=initial)])
                self.assertEqual(self.enemy.mover.crouched, bool(initial))
                target = next(t for t in self.fight._targets() if t.owner is self.enemy)
                health = self.fight.vitals_of(target)
                # This trajectory crosses the standing capsule above the crouched capsule.
                bomb = self.launch(pitch=-2800)
                self.assertFalse(bomb.stuck)
                held = CROUCH if crouch_at_contact else 0
                self.step()
                self.enemy.take_commands([Command(self.run.frame, buttons=held)])
                self.assertEqual(self.enemy.mover.crouched, crouch_at_contact)
                self.assertFalse(bomb.stuck)
                self.contact(bomb)
                self.assertEqual(self.enemy.mover.crouched, crouch_at_contact)
                self.assertEqual(bomb.attached, None if crouch_at_contact else self.enemy.body)
                self.assertEqual(health.total, 150 if crouch_at_contact else 145)
                hits = self.player.client.messages(combat.HIT)
                self.assertEqual(len(hits), int(not crouch_at_contact))
                if hits:
                    self.assertEqual(hits[0]["+0x78"]["+0x4"], 5)

    def test_pulse_bomb_batch_preserves_stance_at_contact(self):
        for crouch_after_crossing in (False, True):
            with self.subTest(crouch_after_crossing=crouch_after_crossing):
                continuous = self.pulse_across_stance_batch(crouch_after_crossing, batched=False)
                batched = self.pulse_across_stance_batch(crouch_after_crossing, batched=True)
                self.assertEqual(continuous, (crouch_after_crossing, 145 if crouch_after_crossing else 150))
                self.assertEqual(batched, continuous)

    def pulse_across_stance_batch(self, crouch_after_crossing, *, batched):
        self.setUp()
        self.assertTrue(self.run.match.switch_hero(self.enemy, heroes()[tracer_tests.TRACER]))
        x, y, z = self.player.mover.position
        self.enemy.mover.position[:] = (x, y, z + 5)
        self.enemy.mover.body = None
        initial = 0 if crouch_after_crossing else CROUCH
        final = CROUCH if crouch_after_crossing else 0
        for _ in range(12):
            self.step()
            self.enemy.take_commands([Command(self.run.frame, buttons=initial)])
        target = next(t for t in self.fight._targets() if t.owner is self.enemy)
        health = self.fight.vitals_of(target)
        bomb = self.launch(pitch=-2800)
        for _ in range(15):
            self.step()
            self.enemy.take_commands([Command(self.run.frame, buttons=initial)])
        self.assertFalse(bomb.stuck)
        commands = [initial] * 12 + [final] * 3
        if batched:
            self.enemy.take_commands([
                Command(self.run.frame + i, buttons=buttons) for i, buttons in enumerate(commands, 1)
            ])
            self.run.frame += len(commands)
            self.run.match.update(self.run.frame * combat.TICK, self.run.frame)
        else:
            for buttons in commands:
                self.step()
                self.enemy.take_commands([Command(self.run.frame, buttons=buttons)])
        self.assertEqual(self.enemy.mover.crouched, bool(final))
        return bomb.attached == self.enemy.body, health.total

    def test_pulse_bomb_batch_uses_attached_target_pose_at_fuse_expiry(self):
        continuous = self.pulse_fuse_during_movement(batched=False)
        batched = self.pulse_fuse_during_movement(batched=True)
        for actual, expected in zip(batched, continuous, strict=True):
            self.assertAlmostEqual(actual, expected, places=5)

    def pulse_fuse_during_movement(self, *, batched):
        self.setUp()
        self.assertTrue(self.run.match.switch_hero(self.enemy, heroes()[tracer_tests.TRACER]))
        x, y, z = self.player.mover.position
        self.enemy.mover.position[:] = (x, y, z + 5)
        self.enemy.mover.body = None
        # Keep the actual target alive to compare its remaining movement after the blast.
        health = self.fight.vitals[self.enemy.body] = combat.Vitals(self.enemy.body, 1000)
        bomb = self.launch()
        self.contact(bomb)
        self.assertEqual(bomb.attached, self.enemy.body)
        for _ in range(70):
            if bomb.fuse_left <= 0.13:
                break
            self.step()
            self.enemy.take_commands([Command(self.run.frame)])
        self.assertIn(bomb, self.manager.bombs)
        self.assertGreater(bomb.fuse_left, 0)
        self.assertLessEqual(bomb.fuse_left, 0.13)
        before = bomb.position[0]
        gap = 14
        if batched:
            self.enemy.take_commands([Command(self.run.frame + i, right=-127) for i in range(1, gap + 1)])
            self.run.frame += gap
            self.run.match.update(self.run.frame * combat.TICK, self.run.frame)
        else:
            for _ in range(gap):
                self.step()
                self.enemy.take_commands([Command(self.run.frame, right=-127)])
        self.assertNotIn(bomb, self.manager.bombs)
        self.assertEqual(health.total, 645)
        self.assertGreater(bomb.position[0], before)
        self.assertLess(bomb.position[0], self.enemy.mover.position[0])
        hits = self.player.client.messages(combat.HIT)
        self.assertEqual([hit["+0x78"]["+0x4"] for hit in hits], [5, 350])
        return (*bomb.position, *self.enemy.mover.position, health.total, hits[-1]["+0x84"])

    def test_batched_recall_detaches_pulse_at_last_unprotected_position(self):
        results = [self.check_pulse_recall_batch(batched) for batched in (False, True)]
        for continuous, batched in zip(*results, strict=True):
            self.assertAlmostEqual(continuous, batched, places=5)

    def check_pulse_recall_batch(self, batched):
        self.setUp()
        self.assertTrue(self.run.match.switch_hero(self.enemy, heroes()[tracer_tests.TRACER]))
        x, y, z = self.player.mover.position
        self.enemy.mover.position[:] = (x, y, z + 5)
        self.enemy.mover.body = None
        for _ in range(195):
            self.step()
            self.enemy.take_commands([Command(self.run.frame)])
        target = next(t for t in self.fight._targets() if t.owner is self.enemy)
        health = self.fight.vitals_of(target)
        bomb = self.launch()
        self.contact(bomb)
        self.assertEqual(bomb.attached, self.enemy.body)
        self.assertEqual(health.total, 145)
        for _ in range(4):
            self.step()
            self.enemy.take_commands([Command(self.run.frame)])
        first = self.run.frame
        original = bomb.position
        commands = [Command(first + i, right=-127, buttons=16 if i == 6 else 0) for i in range(1, 13)]
        if batched:
            self.enemy.take_commands(commands)
            self.run.frame += len(commands)
            self.run.match.update(self.run.frame * combat.TICK, self.run.frame)
        else:
            for command in commands:
                self.step()
                self.enemy.take_commands([command])
        self.assertTrue(tracer.recalling(self.enemy))
        self.assertIsNone(bomb.attached)
        self.assertGreater(bomb.position[0], original[0])
        before_recall = next(p for p in self.enemy.mover.history if p.frame == first + 5)
        expected = tuple(a + b for a, b in zip(before_recall.position, bomb.offset, strict=True))
        for actual, position in zip(bomb.position, expected, strict=True):
            self.assertAlmostEqual(actual, position)
        detached = bomb.position
        for _ in range(66):
            self.step()
            self.enemy.take_commands([Command(self.run.frame)])
            if bomb not in self.manager.bombs:
                break
        self.assertNotIn(bomb, self.manager.bombs)
        self.assertTrue(tracer.recalling(self.enemy))
        self.assertEqual(health.total, 145)
        self.assertEqual(bomb.position, detached)
        self.assertEqual(len(self.player.client.messages(combat.HIT)), 1)
        return detached

    def test_actual_release_applies_authored_upward_bias_at_level_aim(self):
        bomb = self.launch()
        self.assertEqual(bomb.velocity[0], 0)
        self.assertAlmostEqual(bomb.velocity[1] / bomb.velocity[2], 0.2, places=6)
        self.assertAlmostEqual(sum(axis * axis for axis in bomb.velocity), 15 ** 2, places=4)

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

    def test_blast_falloff_and_outer_range_use_capsule_surface(self):
        from test_game_combat import in_world

        for distance, damage in ((3.0, 210), (3.875, 87.5), (4.125, 0)):
            with self.subTest(surface_distance=distance):
                self.setUp()
                bomb = self.launch(pitch=-800)
                self.contact(bomb)
                self.assertEqual(bomb.attached, self.enemy.body)
                third = self.run.match.add_player(3, "Ring target", SOLDIER, 1, False)
                in_world(self.run.match, third)
                self.run.match.switch_hero(third, third.hero)
                x, y, z = bomb.position
                third.mover.position = [x + distance + 0.5, self.player.mover.position[1], z]
                self.assertGreater(y, third.mover.position[1] + 0.5)
                self.assertLess(y, third.mover.position[1] + 1.5)
                vitals = combat.Vitals(third.body, 500)
                self.fight.vitals[third.body] = vitals
                self.wait(80)
                self.assertNotIn(bomb, self.manager.bombs)
                self.assertTrue(self.vitals.dead)
                self.assertAlmostEqual(vitals.total, 500 - damage, places=4)

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
