"""Actual authored cast/cooldown and reconstructed friendly-player revival.

Same-body/full-pool revival and subject control/immunity windows are declared
host policies. Full native effect ownership and client presentation remain open.
"""

import unittest

import test_game_mercy_staff as staff_tests
from test_game_combat import aim
from test_game_mover import FLOOR, wall

from ow174.game import combat, pools, stats
from ow174.game.collision import World
from ow174.game.commands import FIRE, Command
from ow174.game.content import find_hero

RESURRECT = 0x10


class ResurrectTests(unittest.TestCase):
    def setUp(self):
        fixture = staff_tests.MercyStaffTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        self.fixture = fixture
        self.run, self.match, self.fight = fixture.run, fixture.match, fixture.fight
        self.mercy, self.ally, self.enemy = fixture.mercy, fixture.ally, fixture.enemy
        self.ally.spawn = ((0.0, 1.0, 3.0), 0.0)
        self.ally.mover = self.ally.new_mover()
        self.target = next(t for t in self.fight._targets() if t.owner is self.ally)
        self.fight._damage(self.enemy, self.target, 500, False, None, self.run.frame)
        self.instance = next(i for i in self.mercy.body_script.component.instances.values()
                             if i.graph.index == 0x00CA)
        self.step(3)

    def step(self, count, button=0, *, target=None, ally_attack=0):
        target = target or self.ally
        for _ in range(count):
            x, y, z = target.mover.position
            yaw, pitch = aim(self.fight._eye(self.mercy), (x, y + 1, z))
            x, y, z = self.enemy.mover.position
            ayaw, apitch = aim(self.fight._eye(self.ally), (x, y + 0.8, z))
            self.run.step({self.mercy: (yaw, pitch, button), self.ally: (ayaw, apitch, ally_attack)})

    def cast(self):
        self.step(1, RESURRECT)
        self.assertTrue(self.fight.is_dead(self.ally))
        for _ in range(120):
            self.step(1)
            if not self.fight.is_dead(self.ally):
                break
        self.assertFalse(self.fight.is_dead(self.ally))
        self.assertIsNotNone(self.mercy.body_script)
        self.assertFalse(self.mercy.body_script_failed)

    def cooldown(self):
        return self.instance.find_var(0, 61).value()

    def test_one_press_runs_authored_cast_then_restores_real_health_and_starts_thirty_second_cooldown(self):
        self.step(1, RESURRECT)
        self.step(100)
        self.assertTrue(self.fight.is_dead(self.ally))
        self.assertEqual(self.cooldown(), 0)
        self.step(15)
        self.assertFalse(self.fight.is_dead(self.ally))
        self.assertEqual(self.fight.vitals_of(self.target).total, 200)
        self.assertGreater(self.cooldown(), 29)
        self.assertLessEqual(self.cooldown(), 30)
        self.assertEqual(self.ally.stats.value(stats.DEATHS), 1)
        self.assertEqual(self.mercy.stats.value(stats.HEALING), 0)
        self.assertEqual(self.mercy.stats.value(0x086000000000026A), 1)

    def test_same_body_pose_is_revived_and_original_respawn_deadline_is_cancelled(self):
        body, position = self.ally.body, tuple(self.ally.mover.position)
        self.cast()
        self.assertEqual(self.ally.body, body)
        self.assertEqual(tuple(self.ally.mover.position), position)
        self.assertFalse(any(who is self.ally for _, who in self.fight.respawns))
        self.step(250)
        self.assertEqual(self.ally.body, body)
        self.assertFalse(self.fight.is_dead(self.ally))

    def test_subject_is_temporarily_immune_then_can_take_real_damage_again(self):
        self.cast()
        vitals = self.fight.vitals_of(self.target)
        self.fight._damage(self.enemy, self.target, 500, False, None, self.run.frame)
        self.assertEqual(vitals.total, 200)
        self.step(140)
        self.fight._damage(self.enemy, self.target, 50, False, None, self.run.frame)
        self.assertEqual(vitals.total, 200)  #140 ticks=2.24s, still under2.25s
        self.step(2)
        self.fight._damage(self.enemy, self.target, 50, False, None, self.run.frame)
        self.assertEqual(vitals.total, 150)

    def test_subject_cannot_fire_during_rising_lock_then_can_fire(self):
        self.cast()
        before = self.ally.stats.value(stats.HERO_DAMAGE)
        self.step(80, ally_attack=FIRE)
        self.assertEqual(self.ally.stats.value(stats.HERO_DAMAGE), before)
        self.step(20)
        self.step(1, ally_attack=FIRE)
        self.step(5)
        self.assertGreater(self.ally.stats.value(stats.HERO_DAMAGE), before)

    def test_caster_death_interrupts_cast_without_reviving_subject(self):
        self.step(1, RESURRECT)
        self.step(30)
        caster = next(t for t in self.fight._targets() if t.owner is self.mercy)
        self.fight._damage(self.enemy, caster, 500, False, None, self.run.frame)
        self.step(100)
        self.assertTrue(self.fight.is_dead(self.ally))
        self.assertEqual(self.fight.vitals_of(self.target).total, 0)

    def test_moving_outside_cast_tether_interrupts_without_reviving_subject(self):
        self.step(1, RESURRECT)
        self.step(30)
        self.mercy.spawn = ((20.0, 1.0, 0.0), 0.0)
        self.mercy.mover = self.mercy.new_mover()
        self.step(100)
        self.assertTrue(self.fight.is_dead(self.ally))
        self.assertEqual(self.fight.vitals_of(self.target).total, 0)

    def test_living_friendly_target_is_not_resurrectable(self):
        self.fight._respawn(self.ally)
        self.step(3)
        self.step(1, RESURRECT)
        self.step(120)
        self.assertEqual(self.cooldown(), 0)
        self.assertEqual(self.mercy.stats.value(0x086000000000026A), 0)

    def test_hostile_corpse_in_range_is_not_resurrectable(self):
        self.enemy.spawn = ((3.0, 1.0, 0.0), 0.0)
        self.enemy.mover = self.enemy.new_mover()
        enemy = next(t for t in self.fight._targets() if t.owner is self.enemy)
        self.fight._damage(self.mercy, enemy, 500, False, None, self.run.frame)
        self.step(3, target=self.enemy)
        self.step(1, RESURRECT, target=self.enemy)
        self.step(120, target=self.enemy)
        self.assertTrue(self.fight.is_dead(self.enemy))
        self.assertEqual(self.cooldown(), 0)

    def test_cooldown_rejects_a_second_dead_target_and_weapon_switch_does_not_reset_it(self):
        self.cast()
        self.step(150)
        self.fight._damage(self.enemy, self.target, 500, False, None, self.run.frame)
        self.step(3)
        self.run.frame += 1
        self.match.update(self.run.frame * combat.TICK, self.run.frame)
        self.mercy.take_commands([Command(self.run.frame, action=2)])
        self.step(1, RESURRECT)
        self.step(120)
        self.assertTrue(self.fight.is_dead(self.ally))
        self.assertGreater(self.cooldown(), 20)

    def test_cooldown_end_allows_another_real_cast_on_a_fresh_death(self):
        self.cast()
        self.run.frame += 2000
        self.match.update(self.run.frame * combat.TICK, self.run.frame)
        for player in self.run.players:
            player.take_commands([Command(self.run.frame)])
        self.assertEqual(self.cooldown(), 0)
        self.target = next(t for t in self.fight._targets() if t.owner is self.ally)
        self.fight._damage(self.enemy, self.target, 500, False, None, self.run.frame)
        self.step(3)
        self.cast()
        self.assertEqual(self.mercy.stats.value(0x086000000000026A), 2)

    def test_repeated_press_during_cast_does_not_duplicate_resurrection(self):
        self.step(1, RESURRECT)
        self.step(50)
        self.step(1, RESURRECT)
        self.step(65)
        self.assertFalse(self.fight.is_dead(self.ally))
        self.assertEqual(self.mercy.stats.value(0x086000000000026A), 1)
        self.assertGreater(self.cooldown(), 29)

    def test_initial_range_and_line_of_sight_are_required(self):
        self.ally.spawn = ((0.0, 1.0, 6.0), 0.0)
        self.ally.mover = self.ally.new_mover()
        self.step(3)
        self.step(1, RESURRECT)
        self.step(100)
        self.assertTrue(self.fight.is_dead(self.ally))
        self.assertEqual(self.cooldown(), 0)

    def test_initial_static_occlusion_rejects_cast(self):
        self.match.collision_world = World(FLOOR + wall(-5, 1.5, 5, 1.5))
        self.step(3)
        self.step(1, RESURRECT)
        self.step(100)
        self.assertTrue(self.fight.is_dead(self.ally))
        self.assertEqual(self.cooldown(), 0)

    def test_losing_static_sight_after_cast_start_keeps_the_authored_tether_cast(self):
        self.step(1, RESURRECT)
        self.step(20)
        self.match.collision_world = World(FLOOR + wall(-5, 1.5, 5, 1.5))
        self.step(100)
        self.assertFalse(self.fight.is_dead(self.ally))

    def test_old_corpse_replaced_by_normal_respawn_does_not_revive_a_new_body(self):
        self.step(100)
        old = self.ally.body
        self.step(1, RESURRECT)
        self.step(120)
        self.assertNotEqual(self.ally.body, old)
        target = next(t for t in self.fight._targets() if t.owner is self.ally)
        self.assertEqual(self.fight.vitals_of(target).invulnerable_until, 0)
        self.assertEqual(self.mercy.stats.value(0x086000000000026A), 0)

    def test_subject_movement_is_blocked_during_rising_lock_then_restored(self):
        self.cast()
        position = tuple(self.ally.mover.position)
        self.run.frame += 1
        self.match.update(self.run.frame * combat.TICK, self.run.frame)
        self.ally.take_commands([Command(self.run.frame, forward=127)])
        self.assertEqual(tuple(self.ally.mover.position), position)
        self.step(100)
        for _ in range(5):
            self.run.frame += 1
            self.match.update(self.run.frame * combat.TICK, self.run.frame)
            self.ally.take_commands([Command(self.run.frame, forward=127)])
        self.assertNotEqual(tuple(self.ally.mover.position), position)

    def test_health_armour_and_shield_subject_pools_restore_from_their_actual_hero_data(self):
        for name in ("Reinhardt", "Zarya", "Mercy"):
            with self.subTest(hero=name):
                other = ResurrectTests()
                other.setUp()
                try:
                    hero = find_hero(name)
                    other.match.switch_hero(other.ally, hero)
                    other.target = next(t for t in other.fight._targets() if t.owner is other.ally)
                    other.fight._damage(other.enemy, other.target, 10000, False, None, other.run.frame)
                    other.step(3)
                    other.cast()
                    vitals = other.fight.vitals_of(other.target)
                    self.assertEqual(vitals.now, vitals.most)
                    self.assertEqual(vitals.total, sum(pools.body_pools(hero.guid)))
                finally:
                    other.doCleanups()
