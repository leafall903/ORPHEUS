"""Actual enemy Flashbang interrupts, authored exits and collision recovery."""

import unittest

from test_doomfist_combat import DoomfistFixture
from test_game_combat import aim

from ow174.game import combat, stats
from ow174.game.content import heroes
from ow174.game.script import nodes


class FlashbangInteractionTests(DoomfistFixture, unittest.TestCase):
    def setUp(self):
        self.prepare(3)
        self.match.switch_hero(self.b, heroes()[0x02E0000000000042])
        x, y, z = self.a.mover.position
        self.place_player(self.b, (x + 3, y, z + 3))
        self.run.step({})

    def instance(self, index):
        return next(i for i in self.a.body_script.component.instances.values() if i.graph.index == index)

    def step(self, buttons=0, *, flash=False, pitch=0, forward=0):
        x, y, z = self.a.mover.position
        yaw, target_pitch = aim(self.fight._eye(self.b), (x, y + 1, z))
        if forward:
            from ow174.game.commands import Command

            self.run.frame += 1
            self.match.update(self.run.frame * .016, self.run.frame)
            self.a.take_commands([Command(self.run.frame, buttons=buttons, pitch=pitch, forward=forward)])
            self.b.take_commands([Command(self.run.frame, yaw=yaw, pitch=target_pitch,
                                         buttons=16 if flash else 0)])
        else:
            self.run.step({self.a: (0, pitch, buttons),
                           self.b: (yaw, target_pitch, 16 if flash else 0)})

    def wait_for_flash(self, buttons=0, *, pitch=0):
        for _ in range(60):
            if self.fight.is_stunned(self.a):
                return
            self.step(buttons, pitch=pitch)
        self.fail('actual enemy Flashbang did not hit this cast')

    def close_caster(self):
        # Put the thrower near the already-moving target for an actual impact;
        # no stun, ability, input, cooldown or projectile field is written.
        x, y, z = self.a.mover.position
        self.place_player(self.b, (x + 1, y, z + 1))

    def test_flash_interrupts_punch_charge_consumes_cooldown_and_fresh_input_recovers(self):
        for _ in range(30):
            self.step(2)
        punch = self.instance(0xD06)
        self.assertTrue(punch.state(0).flags & nodes.Ability.RUNNING)
        self.step(2, flash=True)
        self.wait_for_flash(2)
        self.assertFalse(punch.state(0).flags & nodes.Ability.RUNNING)
        self.assertGreater(punch.state(0).remaining(), 0)
        self.assertNotIn(self.a.body, self.fight.punches.dashes)
        end = self.fight.stuns[self.a.body]
        position, hp = tuple(self.a.mover.position), self.vitals().total
        while self.run.frame < end - 1:
            self.step(2 | 1, forward=127)
            self.assertEqual(tuple(self.a.mover.position), position)
            self.assertEqual(self.vitals().total, hp)
        self.step()
        self.step(2)  # cooldown pulse must expire, not auto-cast later
        self.step()
        for _ in range(300):
            self.step()
        self.assertEqual(punch.state(0).remaining(), 0)
        self.assertNotIn(self.a.body, self.fight.punches.dashes)
        x, y, z = self.a.mover.position
        self.place_player(self.b, (x, y, z + 6))
        self.step(2)
        for _ in range(40):
            self.step()
        self.assertEqual(self.vitals().total, hp - 50)

    def test_flash_interrupts_a_started_dash_without_late_movement_or_damage(self):
        for _ in range(30):
            self.step(2)
        self.step(2, flash=True)
        for _ in range(4):
            self.step(2)
        self.step()
        self.assertIn(self.a.body, self.fight.punches.dashes)
        self.wait_for_flash()
        position, hp = tuple(self.a.mover.position), self.vitals().total
        self.assertFalse(self.instance(0xD06).state(7).active)
        for _ in range(100):
            self.step()
            self.assertEqual(tuple(self.a.mover.position), position)
            self.assertEqual(self.vitals().total, hp)
        self.assertTrue(self.fight.punches.dashes[self.a.body].finished)

    def test_flash_interrupts_ground_slam_before_landing_without_delayed_impact(self):
        hp = self.vitals().total
        self.step(16)
        slam = self.instance(0xB27)
        self.assertTrue(slam.state(0).flags & nodes.Ability.RUNNING)
        self.close_caster()
        self.step(flash=True)
        self.wait_for_flash()
        self.assertFalse(slam.state(0).flags & nodes.Ability.RUNNING)
        for _ in range(180):
            self.step()
        self.assertFalse(self.a.mover.airborne)
        self.assertEqual(self.vitals().total, hp)
        self.assertFalse(self.a.client.messages(combat.HIT))

    def test_flash_after_uppercut_keeps_gravity_and_does_not_replay_hit(self):
        self.step(8)
        self.assertTrue(self.a.mover.airborne)
        self.close_caster()
        self.step(flash=True)
        self.wait_for_flash()
        uppercut = self.instance(0xDD8)
        hp = self.vitals().total
        self.assertFalse(uppercut.state(0).flags & nodes.Ability.RUNNING)
        self.assertGreater(uppercut.state(0).remaining(), 0)
        for _ in range(180):
            self.step()
        self.assertFalse(self.a.mover.airborne)
        self.assertEqual(self.vitals().total, hp)

    def test_flash_interrupts_committed_aerial_slam_without_landing_damage(self):
        x, y, z = self.a.mover.position
        self.place_player(self.a, (x, y + 8, z))
        self.step(16, pitch=9000)
        cast = self.fight.slams.casts[self.a.body]
        self.assertEqual(cast.kind, 'aerial')
        for _ in range(3):
            self.step(pitch=9000)
        # The declared Slam host supplies this request because native target
        # query/MoveTo services remain incomplete. Verify real movement too.
        self.assertIsNotNone(self.fight.slams.movement(self.a))
        self.assertGreater(self.a.mover.position[2], z)
        self.close_caster()
        hp = self.vitals().total
        self.step(flash=True)
        self.wait_for_flash()
        self.assertFalse(self.instance(0xB27).state(0).flags & nodes.Ability.RUNNING)
        self.assertIsNone(self.fight.slams.movement(self.a))
        for _ in range(180):
            self.step()
        self.assertFalse(self.a.mover.airborne)
        self.assertEqual(self.vitals().total, hp)
        self.assertFalse(self.a.client.messages(combat.HIT))


class IncomingDeathTests(DoomfistFixture, unittest.TestCase):
    def test_released_pellets_survive_owner_death_and_complete_a_real_trade(self):
        self.prepare(3)
        for _ in range(3):
            self.fire()
            self.settle(25)
        self.assertEqual(self.vitals().total, 2)
        own = next(t for t in self.fight._targets() if t.owner is self.a)
        health = self.fight.vitals_of(own)
        for _ in range(100):
            yaw, pitch = aim(self.fight._eye(self.b), self.fight._eye(self.a))
            self.run.step({self.b: (yaw, pitch, 1)})
            if health.total <= 10:
                break
        self.assertEqual(health.total, 10)
        self.settle(12)  # real weapon recovery; resources and clocks untouched
        x, y, z = self.b.mover.position
        ay, ap = aim(self.fight._eye(self.a), (x, y + 1, z))
        by, bp = aim(self.fight._eye(self.b), self.fight._eye(self.a))
        self.run.step({self.a: (ay, ap, 1), self.b: (by, bp, 1)})
        self.assertTrue(self.fight.is_dead(self.a))
        self.assertFalse(self.fight.is_dead(self.b))
        self.assertEqual(len(self.fight.rockets), 11)
        self.settle(5)
        self.assertTrue(self.fight.is_dead(self.b))
        for player in (self.a, self.b):
            self.assertEqual(player.stats.value(stats.DEATHS), 1)
            self.assertEqual(player.stats.value(stats.ELIMINATIONS), 1)
            self.assertEqual(len(player.client.messages(combat.KILL_NOTICE)), 1)

    def test_opposed_hand_cannon_volleys_kill_both_players_on_same_tick(self):
        self.prepare(3)
        self.match.switch_hero(self.b, self.a.hero)
        x, y, z = self.a.mover.position
        self.place_player(self.b, (x, y, z + 3))
        self.run.step({})
        pools = [self.fight.vitals[p.body] for p in (self.a, self.b)]
        for pool in pools:
            pool.now[combat.HEALTH_POOL] = 5
        self.run.step({self.a: (0, 0, 1), self.b: (-32768, 0, 1)})
        self.assertEqual(len(self.fight.rockets), 22)
        self.assertFalse(any(pool.dead for pool in pools))
        self.settle(10)
        self.assertTrue(all(pool.dead for pool in pools))
        self.assertEqual(pools[0].death_tick, pools[1].death_tick)
        self.assertEqual([pool.killer for pool in pools], [self.b.body, self.a.body])
        for player in (self.a, self.b):
            self.assertEqual(player.stats.value(stats.DEATHS), 1)
            self.assertEqual(player.stats.value(stats.ELIMINATIONS), 1)
            self.assertEqual(len(player.client.messages(combat.KILL_NOTICE)), 1)

    def test_real_rifle_kills_charging_doomfist_then_natural_respawn_has_fresh_weapon(self):
        self.prepare(3)
        old_body = self.a.body
        for _ in range(100):
            yaw, pitch = aim(self.fight._eye(self.b), self.fight._eye(self.a))
            self.run.step({self.a: (0, 0, 2), self.b: (yaw, pitch, 1)})
            if self.fight.is_dead(self.a):
                break
        self.assertTrue(self.fight.is_dead(self.a))
        self.assertEqual(self.a.stats.value(stats.DEATHS), 1)
        self.assertEqual(self.b.stats.value(stats.ELIMINATIONS), 1)
        self.assertNotIn(old_body, self.fight.punches.dashes)
        deadline = next(when for when, owner in self.fight.respawns if owner is self.a)
        # No clock advance, direct respawn call or resource reset: command
        # every normal tick and prove the old body persists until the deadline.
        while (self.run.frame + 1) * combat.TICK < deadline:
            self.run.step({self.a: (0, 0, 2)})
            self.assertEqual(self.a.body, old_body)
            self.assertEqual(self.vitals().total, 200)
        self.run.step({})
        self.assertNotEqual(self.a.body, old_body)
        self.assertFalse(self.fight.is_dead(self.a))
        self.assertEqual(self.a.hero.guid, 0x02E000000000012F)
        own = next(t for t in self.fight._targets() if t.owner is self.a)
        self.assertEqual(self.fight.vitals_of(own).total, 250)
        self.assertEqual(self.fight.vitals_of(own).temporary_shields, 0)
        self.assertNotIn(old_body, self.fight.punches.dashes)
        weapon = next(i for i in self.a.body_script.component.instances.values()
                      if i.graph.index == 0xD0A)
        self.assertEqual(weapon.vars[53].value(), 4)
        x, y, z = self.a.mover.position
        self.place_player(self.b, (x, y, z + 3))
        self.run.step({})
        self.fire()
        self.settle()
        self.assertEqual(self.vitals().total, 134)
        self.assertEqual(weapon.vars[53].value(), 3)


if __name__ == '__main__':
    unittest.main()
