"""Actual Sigma commands versus the declared Hand Cannon absorption host."""

import unittest

from test_doomfist_combat import DoomfistFixture
from test_doomfist_deflect import deflect
from test_doomfist_meteor import charge_by_neutral_commands, instance_for
from test_game_combat import KANEZAKA_DM, Run, aim
from test_game_mover import box

from ow174.game import absorption, combat, defenses, stats
from ow174.game.body_vm_snapshot import export_component_vm, snapshot_digest
from ow174.game.collision import World
from ow174.game.content import heroes
from ow174.game.script.runtime import ScriptBudgetError


def grasp(player):
    instance = instance_for(player, 0x1D02)
    return (instance if instance is not None
            and absorption.window(instance, player.match.tick) is not None else None)


class AbsorbInteractionTests(DoomfistFixture, unittest.TestCase):
    def arena(self, distance=3, *, names=('Doomfist', 'Sigma')):
        self.run = Run(KANEZAKA_DM, names)
        self.a, self.b = self.run.players[:2]
        self.match = self.run.match
        self.match.bots.clear()
        self.match._pending_bot_scripts.clear()
        self.match.switch_hero(self.a, heroes()[0x02E000000000012F])
        self.match.switch_hero(self.b, heroes()[defenses.SIGMA])
        self.fight = self.match.combat
        x, y, z = self.a.mover.position
        self.place_player(self.b, (x, y, z + distance))
        self.facing = -32768
        self.step()

    def step(self, button=0, *, defend=0):
        x, y, z = self.b.mover.position
        yaw, pitch = aim(self.fight._eye(self.a), (x, y + 1, z))
        self.run.step({self.a: (yaw, pitch, button), self.b: (self.facing, 0, defend)})

    def wait(self, frames):
        for _ in range(frames):
            self.step()

    def start(self):
        self.step(defend=8)
        self.assertIsNotNone(grasp(self.b))
        return grasp(self.b)

    def own(self):
        return self.fight.vitals_of(next(t for t in self.fight._targets() if t.owner is self.a))

    def test_actual_grasp_absorbs_eleven_pellets_then_converts_authored_sixty_percent(self):
        self.arena()
        instance = self.start()
        charge = self.charge_checkpoint()
        self.step(1)
        self.wait(10)
        self.assertEqual(self.vitals().total, 400)
        self.assertEqual(self.own().total, 250)
        self.assertFalse(self.fight.rockets)
        self.assertEqual(instance.vars[21312].value(), 66)
        self.assertEqual(self.a.stats.value(stats.HERO_DAMAGE), 0)
        self.assert_damage_charge(charge, 0)
        self.assertEqual(self.b.stats.value(stats.HERO_DAMAGE), 0)
        self.assertFalse(self.a.client.messages(combat.HIT))
        self.assertEqual(self.own().temporary_shields, 0)
        self.assertEqual(self.vitals().temporary_shields, 0)
        for _ in range(140):
            self.step()
            if self.vitals().temporary_shields > 0:
                break
        self.assertIsNone(grasp(self.b))
        self.assertAlmostEqual(self.vitals().temporary_shields, 39.6, places=4)

    def test_back_attack_damages_intrinsic_shields_without_absorption_credit(self):
        self.arena()
        self.facing = 0
        instance = self.start()
        self.step(1)
        self.wait(10)
        self.assertEqual(self.vitals().total, 334)
        self.assertEqual(instance.vars[21312].value(), 0)
        self.assertEqual(self.a.stats.value(stats.HERO_DAMAGE), 66)
        self.wait(140)
        self.assertEqual(self.vitals().temporary_shields, 0)

    def test_wall_before_grasp_boxes_prevents_damage_and_shield_gain(self):
        self.arena(6)
        x, y, z = self.a.mover.position
        world = World(box(x - 10, x + 10, y - 1, y, z - 10, z + 10)
                      + box(x - 2, x + 2, y, y + 3, z + 1.5, z + 1.7))
        self.match.collision_world = world
        for player in self.run.players:
            player.mover.use(world=world)
        instance = self.start()
        self.step(1)
        self.wait(155)
        self.assertEqual(self.vitals().total, 400)
        self.assertEqual(instance.vars[21312].value(), 0)
        self.assertFalse(self.fight.rockets)

    def test_actual_melee_uppercut_and_ground_slam_bypass_grasp(self):
        for button, distance, expected, passive in ((0x800, 1.5, 370, 0), (8, 1.5, 350, 30),
                                                     (16, 10, None, 30)):
            with self.subTest(button=button):
                self.arena(distance)
                instance = self.start()
                self.step(button)
                self.wait(65 if button == 16 else 5)
                if button == 16:
                    cast = self.fight.slams.casts[self.a.body]
                    self.assertGreater(cast.damage, 0)
                    expected = 400 - cast.damage
                self.assertAlmostEqual(self.vitals().total, expected)
                self.assertEqual(instance.vars[21312].value(), 0)
                self.assertGreaterEqual(self.own().temporary_shields, passive - 1)

    def test_punch_interrupts_actual_grasp_and_discards_accumulated_conversion(self):
        self.arena(3)
        instance = self.start()
        self.step(1)
        self.wait(8)
        self.assertEqual(instance.vars[21312].value(), 66)
        self.step(2)
        self.wait(4)
        self.step()
        self.wait(12)
        self.assertIsNone(grasp(self.b))
        self.assertLess(self.vitals().total, 400)
        self.wait(150)
        self.assertEqual(self.vitals().temporary_shields, 0)
        self.assertGreater(instance.state(0).remaining(), 0)

    def test_actual_accretion_input_cancels_grasp_without_converting_early(self):
        self.arena()
        instance = self.start()
        self.step(1)
        self.wait(8)
        self.assertEqual(instance.vars[21312].value(), 66)
        self.step(defend=16)
        self.assertIsNone(grasp(self.b))
        self.wait(150)
        self.assertEqual(self.vitals().temporary_shields, 0)
        self.assertGreater(instance.state(0).remaining(), 0)

    def test_authored_duration_cooldown_rejection_and_fresh_reentry(self):
        self.arena()
        instance = self.start()
        phase = instance.state(1)
        started, counter = phase.start, phase.counter
        while grasp(self.b) is not None:
            self.step()
        self.assertGreaterEqual(instance.component.now - started, 1984)
        self.assertLessEqual(instance.component.now - started, 2000)
        self.assertTrue(instance.state(20).reached)
        self.assertGreater(instance.state(0).remaining(), 0)
        self.step(defend=8)
        self.wait(15)
        self.assertEqual(phase.counter, counter)
        while instance.state(0).remaining() > 0:
            self.step()
        self.wait(3)
        self.assertEqual(phase.counter, counter)
        self.step(defend=8)
        self.assertIsNotNone(grasp(self.b))
        self.assertEqual(phase.counter, counter + 1)

    def test_converted_shields_take_next_actual_volley_before_intrinsic_pool(self):
        self.arena()
        self.start()
        self.step(1)
        self.wait(140)
        sigma = self.vitals()
        self.assertAlmostEqual(sigma.temporary_shields, 39.6, places=4)
        deadline = sigma.temporary_decay_at
        charge = self.charge_checkpoint()
        self.step(1)
        self.wait(10)
        self.assertEqual(self.vitals().temporary_shields, 0)
        self.assertAlmostEqual(self.vitals().total, 373.6, places=4)
        self.assertEqual(self.vitals().now[combat.HEALTH_POOL], 300)
        self.assertAlmostEqual(self.vitals().now[combat.SHIELDS_POOL], 73.6, places=4)
        self.assertEqual(self.a.stats.value(stats.HERO_DAMAGE), 66)
        self.assertEqual(sigma.temporary_decay_at, deadline)
        self.assert_damage_charge(charge, 26.4)

    def test_converted_shield_authored_three_second_delay_then_five_per_second_decay(self):
        self.arena()
        self.start()
        self.step(1)
        self.wait(140)
        vitals = self.vitals()
        self.assertAlmostEqual(vitals.temporary_shields, 39.6, places=4)
        deadline = vitals.temporary_decay_at
        self.assertGreater(deadline - self.fight.now, 2.5)
        self.assertLessEqual(deadline - self.fight.now, 3)
        self.assertEqual(vitals.temporary_decay_rate, 5)
        while self.fight.now < deadline - .1:
            self.step()
        self.assertAlmostEqual(vitals.temporary_shields, 39.6, places=4)
        before, now = vitals.temporary_shields, self.fight.now
        self.wait(128)
        expected_loss = max(0, self.fight.now - deadline) * 5
        self.assertAlmostEqual(vitals.temporary_shields, before - expected_loss, places=4)
        self.assertGreater(self.fight.now, now)

    def test_actual_meteor_bypasses_grasp_and_grants_doomfist_shields(self):
        self.arena(1)
        charge_by_neutral_commands(self)
        self.step(32)
        self.wait(65)
        self.start()
        self.step(1)
        self.wait(66)
        self.assertEqual(self.vitals().total, 100)
        self.assertGreater(self.own().temporary_shields, 74)

    def test_actual_aerial_slam_bypasses_grasp_and_keeps_passive_gain(self):
        self.arena(10)
        x, y, z = self.a.mover.position
        self.place_player(self.a, (x, y + 8, z))
        instance = self.start()
        self.run.step({self.a: (0, 9000, 16), self.b: (self.facing, 0, 0)})
        cast = self.fight.slams.casts[self.a.body]
        self.assertEqual(cast.kind, 'aerial')
        self.wait(80)
        self.assertIsNotNone(cast.wave_at)
        self.assertIn(self.b.body, cast.hit)
        self.assertAlmostEqual(self.vitals().total, 400 - cast.damage)
        self.assertGreater(self.own().temporary_shields, 0)
        self.assertEqual(instance.vars[21312].value(), 0)

    def test_allied_grasp_does_not_intercept_pellets_for_an_enemy_behind_it(self):
        self.arena(names=('Doomfist', 'Sigma', 'Enemy'))
        enemy = self.run.players[2]
        self.match.game_map = self.match.game_map.__class__(**{
            **self.match.game_map.__dict__, 'free_for_all': False,
        })
        self.a.team = self.b.team = 0
        enemy.team = 1
        x, y, z = self.a.mover.position
        self.place_player(enemy, (x, y, z + 6))
        instance = self.start()
        self.step(1)
        self.wait(12)
        target = next(t for t in self.fight._targets() if t.owner is enemy)
        self.assertEqual(self.fight.vitals_of(target).total, 134)
        self.assertEqual(self.vitals().total, 400)
        self.assertEqual(instance.vars[21312].value(), 0)

    def test_allied_grasp_protects_doomfist_from_enemy_player_hand_cannon(self):
        self.arena(names=('Enemy Doomfist', 'Sigma', 'Protected Doomfist'))
        victim = self.run.players[2]
        self.match.game_map = self.match.game_map.__class__(**{
            **self.match.game_map.__dict__, 'free_for_all': False,
        })
        self.a.team, self.b.team, victim.team = 1, 0, 0
        self.match.switch_hero(victim, self.a.hero)
        x, y, z = self.a.mover.position
        self.place_player(self.b, (x, y, z + 6))
        self.place_player(victim, (x, y, z + 4.5))
        target = next(t for t in self.fight._targets() if t.owner is victim)
        vitals = self.fight.vitals_of(target)
        instance = self.start()
        charge = self.charge_checkpoint()
        yaw, pitch = aim(self.fight._eye(self.a), (x, y + 1, z + 4.5))
        self.run.step({self.a: (yaw, pitch, 1)})
        self.wait(15)
        self.assertEqual(instance.vars[21312].value(), 66)
        self.assertEqual(vitals.total, 250)
        self.assertEqual(self.vitals().total, 400)
        self.assertFalse(self.a.client.messages(combat.HIT))
        self.assert_damage_charge(charge, 0)

        self.wait(125)
        self.assertIsNone(grasp(self.b))
        self.assertAlmostEqual(self.vitals().temporary_shields, 39.6, places=4)
        self.run.step({self.a: (yaw, pitch, 1)})
        self.wait(15)
        self.assertEqual(vitals.total, 184)
        hits = self.a.client.messages(combat.HIT)
        self.assertEqual(len(hits), 11)
        self.assertTrue(all(hit['+0x78']['+0x0']['+0x0'] == victim.body for hit in hits))

    def test_far_projectile_released_before_entry_hits_after_natural_expiry(self):
        self.arena(185)
        self.step(1)
        # Place the target along the actual emitted pellet. At this range
        # the central ring's jitter can miss a target on the aim ray.
        self.align_target_with_first_pellet()
        self.start()
        self.wait(135)
        self.assertIsNone(grasp(self.b))
        self.assertEqual(self.vitals().total, 400)
        self.wait(100)
        self.assertAlmostEqual(self.vitals().total, 398.2, places=4)
        self.assertEqual(self.vitals().temporary_shields, 0)

    def test_body_replacement_discards_pending_conversion_and_existing_shields(self):
        self.arena()
        self.start()
        self.step(1)
        self.wait(10)
        old = self.b.body
        self.match.switch_hero(self.b, heroes()[defenses.SIGMA])
        self.wait(150)
        self.assertNotEqual(self.b.body, old)
        self.assertEqual(self.vitals().total, 400)
        self.assertIsNone(grasp(self.b))
        x, y, z = self.a.mover.position
        self.place_player(self.b, (x, y, z + 3))
        self.start()
        self.step(1)
        self.wait(140)
        self.assertAlmostEqual(self.vitals().temporary_shields, 39.6, places=4)
        self.match.switch_hero(self.b, heroes()[defenses.SIGMA])
        self.wait(150)
        self.assertEqual(self.vitals().total, 400)

    def test_eleven_actual_enemy_volleys_convert_with_authored_four_hundred_cap(self):
        self.arena(names=('Doomfist', 'Sigma', *(f'Doom{i}' for i in range(10))))
        shooters = [self.a, *self.run.players[2:]]
        self.match.game_map = self.match.game_map.__class__(**{
            **self.match.game_map.__dict__, 'free_for_all': False,
        })
        self.b.team = 1
        for shooter in shooters:
            if shooter is not self.a:
                self.match.switch_hero(shooter, heroes()[0x02E000000000012F])
                self.place_player(shooter, tuple(self.a.mover.position))
            shooter.team = 0
        instance = self.start()
        x, y, z = self.b.mover.position
        commands = {p: (*aim(self.fight._eye(p), (x, y + 1, z)), 1) for p in shooters}
        commands[self.b] = (self.facing, 0, 0)
        self.run.step(commands)
        self.wait(140)
        self.assertEqual(instance.vars[21312].value(), 726)
        self.assertEqual(self.vitals().temporary_shields, 400)
        self.assertEqual(self.vitals().total, 800)
        self.assertFalse(self.fight.rockets)

    def test_actual_genji_return_is_absorbed_by_original_shooters_sigma_ally(self):
        self.arena(names=('Doomfist', 'Sigma', 'Genji'))
        genji = self.run.players[2]
        self.match.switch_hero(genji, heroes()[defenses.GENJI])
        self.match.game_map = self.match.game_map.__class__(**{
            **self.match.game_map.__dict__, 'free_for_all': False,
        })
        self.a.team = self.b.team = 0
        genji.team = 1
        x, y, z = self.a.mover.position
        # Grasp's front box must precede Doomfist's wider body capsule.
        self.place_player(self.b, (x, y, z - 1.5))
        self.place_player(genji, (x, y, z + 3))
        self.facing = 0
        self.run.step({self.b: (0, 0, 8), genji: (-32768, 0, 16)})
        instance = grasp(self.b)
        self.assertIsNotNone(instance)
        self.assertIsNotNone(deflect(genji))
        charge = self.charge_checkpoint()
        yaw, pitch = aim(self.fight._eye(self.a), (x, y + 1, z + 3))
        self.run.step({self.a: (yaw, pitch, 1), self.b: (0, 0, 0), genji: (-32768, 0, 0)})
        for _ in range(15):
            self.run.step({self.b: (0, 0, 0), genji: (-32768, 0, 0)})
        self.assertEqual(instance.vars[21312].value(), 66)
        self.assertEqual(self.own().total, 250)
        target = next(t for t in self.fight._targets() if t.owner is genji)
        self.assertEqual(self.fight.vitals_of(target).total, 200)
        self.assertEqual(self.vitals().total, 400)
        for player in (self.a, self.b, genji):
            self.assertEqual(player.stats.value(stats.HERO_DAMAGE), 0)
            self.assertFalse(player.client.messages(combat.HIT))
        self.assert_damage_charge(charge, 0)
        self.assertFalse(self.fight.rockets)
        self.wait(140)
        sigma = self.vitals()
        self.assertAlmostEqual(sigma.temporary_shields, 39.6, places=4)
        self.assertEqual(self.own().temporary_shields, 0)
        self.assertEqual(sigma.temporary_decay_rate, 5)
        self.assertGreater(sigma.temporary_decay_at, self.fight.now)

    def pending_conversion(self):
        # Mechanical VM/host boundary, separate from posed command acceptance.
        # A real cast/volley accumulates damage, then run only its VM to finish.
        self.arena()
        self.start()
        self.step(1)
        self.wait(10)
        script = self.b.body_script
        script.command(script.frame + 130, 0)
        self.assertEqual(self.vitals().temporary_shields, 0)
        self.assertEqual(len(script.component.pool_reports), 1)
        return script

    def test_unposed_conversion_is_discarded_without_later_replay(self):
        script = self.pending_conversion()
        self.fight.command(self.b, None)
        self.assertFalse(script.component.pool_reports)
        self.wait(140)
        self.assertEqual(self.vitals().total, 400)

    def test_vm_fault_discards_pending_conversion_before_it_can_escape(self):
        script = self.pending_conversion()
        with self.assertRaises(ScriptBudgetError):
            script.component.budget_exceeded('test failure after authored conversion request')
        self.assertFalse(script.component.pool_reports)
        self.assertEqual(self.vitals().total, 400)

    def test_pending_conversion_changes_stored_vm_digest_before_health_changes(self):
        script = self.pending_conversion()
        queued = snapshot_digest(export_component_vm(script.component))
        script.component.pool_reports.clear()
        self.assertNotEqual(snapshot_digest(export_component_vm(script.component)), queued)
        self.assertEqual(self.vitals().total, 400)
