"""Actual Hand Cannon commands against the explicitly reconstructed mesh host."""

import os
import unittest

from test_doomfist_combat import DoomfistFixture
from test_game_combat import KANEZAKA_DM, Run, aim
from test_game_mover import box

from ow174.game import combat, stats
from ow174.game.collision import World
from ow174.game.content import heroes


@unittest.skipUnless(os.environ.get('ORPHEUS_REINHARDT_COLLISION'),
                     'requires explicit audited external104319 barrier mesh')
class HandCannonBarrierTests(DoomfistFixture, unittest.TestCase):
    def arena(self, distance=5, names=('Doomfist', 'Reinhardt')):
        self.run = Run(KANEZAKA_DM, names)
        self.a, self.b = self.run.players[:2]
        self.match = self.run.match
        self.match.bots.clear()
        self.match._pending_bot_scripts.clear()
        self.match.switch_hero(self.a, heroes()[0x02E000000000012F])
        self.match.switch_hero(self.b, heroes()[0x02E0000000000007])
        self.fight = self.match.combat
        self.host = self.fight.hand_cannon_barriers
        self.assertIsNotNone(self.host.geometry)
        x, y, z = self.a.mover.position
        self.place_player(self.b, (x, y, z + distance))
        self.facing = -32768
        self.step()
        self.raise_shield()

    def raise_shield(self):
        for _ in range(400):
            if self.shield().active:
                return
            self.step()
        self.fail('actual held secondary did not reach Pet17 in400 normal frames')

    def step(self, button=0, defend=2, target=None):
        x, y, z = (target or self.b).mover.position
        yaw, pitch = aim(self.fight._eye(self.a), (x, y + 1, z))
        self.run.step({self.a: (yaw, pitch, button), self.b: (self.facing, 0, defend)})

    def wait(self, frames, defend=2, target=None):
        for _ in range(frames):
            self.step(defend=defend, target=target)

    def shield(self):
        return self.host.states[self.b.body]

    def volley(self, defend=2, target=None):
        self.step(1, defend, target)
        self.wait(12, defend, target)

    def test_allied_barrier_stops_enemy_player_hand_cannon_before_doomfist(self):
        self.arena(names=('Enemy Doomfist', 'Reinhardt', 'Protected Doomfist'))
        victim = self.run.players[2]
        self.match.game_map = self.match.game_map.__class__(**{
            **self.match.game_map.__dict__, 'free_for_all': False,
        })
        self.a.team, self.b.team, victim.team = 1, 0, 0
        self.match.switch_hero(victim, self.a.hero)
        x, y, z = self.a.mover.position
        self.place_player(self.b, (x + 1.4, y, z + 3))
        self.place_player(victim, (x, y, z + 5))
        body = next(t for t in self.fight._targets() if t.owner is victim)
        vitals = self.fight.vitals_of(body)
        self.volley(target=victim)
        self.assertEqual(self.shield().current, 1534)
        self.assertEqual(vitals.total, 250)
        self.assertFalse(self.a.client.messages(combat.HIT))
        self.wait(75, defend=0, target=victim)
        self.assertFalse(self.shield().active)
        self.volley(defend=0, target=victim)
        self.assertEqual(vitals.total, 184)
        self.assertEqual(len(self.a.client.messages(combat.HIT)), 11)
        self.assertTrue(all(hit['+0x78']['+0x0']['+0x0'] == victim.body
                            for hit in self.a.client.messages(combat.HIT)))

    def test_front_mesh_intercepts_all_pellets_and_mirrors_graph_health(self):
        self.arena()
        self.wait(15)
        instance = self.host.instance(self.b)
        self.assertTrue(instance.state(17).active)
        self.assertIsNone(instance.vars[38].value())  # no native Pet entity claim
        self.assertEqual(self.shield().current, 1600)
        charge = self.charge_checkpoint()
        self.volley()
        self.assertEqual(self.shield().current, 1534)
        self.assertEqual(instance.vars[586].value(), 1534)
        self.assertEqual(self.vitals().total, 500)
        self.assertEqual(self.a.stats.value(stats.HERO_DAMAGE), 0)
        self.assert_damage_charge(charge, 0)
        self.assertFalse(self.a.client.messages(combat.HIT))
        self.assertEqual(self.fight.vitals[self.a.body].temporary_shields, 0)
        self.assertFalse(self.fight.rockets)

    def test_lowered_and_rear_exposure_allow_real_body_damage(self):
        for defend, facing in ((0, -32768), (2, 0)):
            with self.subTest(defend=defend, facing=facing):
                self.arena()
                self.facing = facing
                self.wait(15, defend)
                self.volley(defend)
                self.assertEqual(self.vitals().total, 467)
                self.assertEqual(self.shield().current, 1600)

    def test_same_team_mesh_passes_pellets_in_team_mode(self):
        self.arena(names=('Doomfist', 'Reinhardt', 'Enemy'))
        enemy = self.run.players[2]
        self.match.game_map = self.match.game_map.__class__(**{
            **self.match.game_map.__dict__, 'free_for_all': False,
        })
        self.a.team = self.b.team = 0
        enemy.team = 1
        x, y, z = self.b.mover.position
        self.place_player(enemy, (x, y, z + 3))
        self.wait(15, target=enemy)
        before = self.fight.vitals[enemy.body].total
        self.volley(target=enemy)
        self.assertEqual(before - self.fight.vitals[enemy.body].total, 66)
        self.assertEqual(self.shield().current, 1600)

    def test_mesh_protects_defenders_ally_behind_it(self):
        self.arena(names=('Doomfist', 'Reinhardt', 'Ally'))
        ally = self.run.players[2]
        self.match.game_map = self.match.game_map.__class__(**{
            **self.match.game_map.__dict__, 'free_for_all': False,
        })
        self.a.team = 0
        self.b.team = ally.team = 1
        x, y, z = self.b.mover.position
        self.place_player(ally, (x + 1.5, y, z + 2))
        self.wait(15, target=ally)
        before = self.fight.vitals[ally.body].total
        self.volley(target=ally)
        self.assertEqual(self.fight.vitals[ally.body].total, before)
        self.assertEqual(self.shield().current, 1534)

    def test_static_wall_precedes_mesh_and_leaves_shield_untouched(self):
        self.arena()
        x, y, z = self.a.mover.position
        world = World(box(x - 20, x + 20, y - 1, y, z - 20, z + 20)
                      + box(x - 8, x + 8, y, y + 8, z + 1.2, z + 1.4))
        self.match.collision_world = world
        for player in self.run.players:
            player.mover.use(world=world)
        self.wait(15)
        self.volley()
        self.assertEqual(self.shield().current, 1600)
        self.assertEqual(self.vitals().total, 500)

    def test_release_regeneration_delay_then_fresh_held_secondary(self):
        self.arena()
        self.wait(15)
        self.volley()
        self.wait(120, defend=0)
        self.assertFalse(self.shield().active)
        self.assertEqual(self.shield().current, 1534)
        self.wait(30, defend=0)
        self.assertEqual(self.shield().current, 1600)
        self.wait(15)
        self.assertTrue(self.shield().active)
        self.volley()
        self.assertEqual(self.shield().current, 1534)
        self.assertEqual(self.vitals().total, 500)

    def test_body_replacement_retires_old_shield_and_fresh_body_has_full_pool(self):
        self.arena()
        self.wait(15)
        self.volley()
        old = self.b.body
        self.match.switch_hero(self.b, heroes()[0x02E0000000000002])
        self.assertNotIn(old, self.host.states)
        self.match.switch_hero(self.b, heroes()[0x02E0000000000007])
        self.step()
        self.raise_shield()
        self.assertEqual(self.shield().current, 1600)
        self.assertTrue(self.shield().active)

    def test_real_volleys_deplete_shield_then_body_takes_remaining_pellets(self):
        self.arena()
        self.wait(15)
        charge = self.charge_checkpoint()
        for _ in range(24):
            self.volley()
            self.wait(100)
        self.assertEqual(self.shield().current, 16)
        self.assert_damage_charge(charge, 0)
        self.assertEqual(self.vitals().total, 500)
        charge = self.charge_checkpoint()
        self.volley()
        self.assertEqual(self.shield().current, 0)
        self.assertFalse(self.shield().active)
        self.assertEqual(self.vitals().total, 476)
        self.assert_damage_charge(charge, 24)
        self.assertGreater(self.shield().broken_until, self.fight.now)
        self.wait(100, defend=0)
        self.step(defend=2)  # actual short press well before broken cooldown ends
        self.step(defend=0)
        self.assertFalse(self.shield().active)
        self.wait(230, defend=0)
        self.assertFalse(self.shield().active)  # old rejected pulse must not auto-raise
        self.assertGreater(self.shield().current, 0)
        self.assertLess(self.shield().current, 1600)
        self.raise_shield()
        self.assertTrue(self.shield().active)
        restored = self.shield().current
        charge = self.charge_checkpoint()
        self.volley()
        self.assertAlmostEqual(self.shield().current, restored - 66, places=3)
        self.assert_damage_charge(charge, 0)
        self.assertEqual(self.vitals().total, 476)
        self.wait(625, defend=0)
        self.assertEqual(self.shield().current, 1600)
        self.raise_shield()
        self.assertTrue(self.shield().active)
