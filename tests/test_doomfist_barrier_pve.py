"""Five actual NPC bodies behind the declared enemy Reinhardt mesh host."""

import os
import unittest

import test_doomfist_barrier as barrier_tests
import test_doomfist_pve as npc_tests
from test_doomfist_combat import DoomfistFixture
from test_game_combat import aim

from ow174.game import defenses, npc, npc_control, stats
from ow174.game.content import heroes


@unittest.skipUnless(os.environ.get('ORPHEUS_REINHARDT_COLLISION')
                     and os.environ.get('ORPHEUS_NPC_DATA_DIR'),
                     'requires explicit barrier geometry and five-body NPC inputs')
class ActualNpcBarrierTests(DoomfistFixture, unittest.TestCase):
    arena = barrier_tests.HandCannonBarrierTests.arena
    raise_shield = barrier_tests.HandCannonBarrierTests.raise_shield
    step = barrier_tests.HandCannonBarrierTests.step
    wait = barrier_tests.HandCannonBarrierTests.wait
    shield = barrier_tests.HandCannonBarrierTests.shield
    volley = barrier_tests.HandCannonBarrierTests.volley

    @classmethod
    def setUpClass(cls):
        npc_tests.ActualNpcDamageTests.setUpClass.__func__(cls)

    @classmethod
    def tearDownClass(cls):
        npc_tests.ActualNpcDamageTests.tearDownClass.__func__(cls)

    def test_actual_five_npcs_are_protected_then_exposed_after_normal_shield_release(self):
        for definition in self.catalog.values():
            with self.subTest(npc=definition.name):
                self.arena()
                self.match.game_map = self.match.game_map.__class__(**{
                    **self.match.game_map.__dict__, 'free_for_all': False,
                })
                self.a.team, self.b.team = 0, 1
                x, y, z = self.b.mover.position
                bot = npc.spawn(self.match, definition, (x + 1.5, y, z + 2), team=1)
                self.assertIsNotNone(bot.body_script)
                self.assertTrue(bot.retain_corpse)
                vitals = self.fight.vitals[bot.entity]
                before = vitals.total
                # The fixture aim helper accepts any actor with a mover; a
                # target adapter only supplies a position, not health/ability state.
                from types import SimpleNamespace
                target = SimpleNamespace(mover=SimpleNamespace(position=bot.position))
                self.volley(target=target)
                self.assertEqual(vitals.total, before)
                self.assertEqual(self.shield().current, 1534)
                self.wait(120, defend=0, target=target)
                self.volley(defend=0, target=target)
                self.assertAlmostEqual(before - vitals.total, 66)

    def test_allied_reinhardt_blocks_four_actual_npc_weapons_then_doomfist_is_exposed(self):
        expected = {'Trooper': 20, 'Enforcer': 2, 'Sniper': 100, 'Heavy Assault': 5}
        for definition in self.catalog.values():
            if definition.name not in expected:
                continue
            with self.subTest(npc=definition.name):
                self.arena()
                self.match.game_map = self.match.game_map.__class__(**{
                    **self.match.game_map.__dict__, 'free_for_all': False,
                })
                self.a.team = self.b.team = 0
                x, y, z = self.a.mover.position
                self.place_player(self.b, (x + 1.5, y, z + 4))
                self.facing = 0
                bot = npc.spawn(self.match, definition, (x, y, z + 10), team=1)
                for player in self.match.players:
                    player.client.bot_stream = lambda entity: None
                self.a.mover.yaw, self.a.mover.pitch = aim(self.fight._eye(self.a), (x, y + 1, z + 10))
                self.assertEqual(npc_control.start(self.match, self.a), bot.entity)
                own = npc_tests.ActualNpcDamageTests.own_vitals(self)
                saw_projectile = False
                for _ in range(350):
                    self.step()
                    saw_projectile |= any(
                        p.shooter is bot and p.npc_direct_projectile for p in self.fight.rockets
                    )
                    if self.shield().current < 1600:
                        break
                self.assertEqual(saw_projectile, definition.name in ('Enforcer', 'Sniper'))
                self.assertEqual(1600 - self.shield().current, expected[definition.name])
                self.assertEqual(own.total, 250)
                self.assertEqual(self.vitals().total, 500)
                for _ in range(350):
                    self.step(defend=0)
                    if own.total < 250:
                        break
                self.assertFalse(self.shield().active)
                if definition.name == 'Enforcer':
                    # At 10m the body is beyond the authored 8m falloff start.
                    # The closer shield still takes the full two points.
                    self.assertAlmostEqual(250 - own.total, 1.70, delta=.03)
                else:
                    self.assertEqual(250 - own.total, expected[definition.name])
                npc_control.stop(self.match)

    def npc_weapon_scene(self, name, defender):
        self.prepare()
        self.match.game_map = self.match.game_map.__class__(**{
            **self.match.game_map.__dict__, 'free_for_all': False,
        })
        self.a.team = self.b.team = 0
        self.match.switch_hero(self.b, heroes()[defender])
        x, y, z = self.a.mover.position
        self.place_player(self.b, (x + 20, y, z))
        definition = next(d for d in self.catalog.values() if d.name == name)
        bot = npc.spawn(self.match, definition, (x, y, z + 10), team=1)
        for player in self.match.players:
            player.client.bot_stream = lambda entity: None
        self.a.mover.yaw, self.a.mover.pitch = aim(self.fight._eye(self.a), (x, y + 1, z + 10))
        npc_control.start(self.match, self.a)
        return bot

    def npc_projectile_scene(self, name, defender, button):
        bot = self.npc_weapon_scene(name, defender)
        for _ in range(350):
            self.run.step({})
            shots = [p for p in self.fight.rockets if p.shooter is bot]
            if shots:
                break
        self.assertTrue(shots)
        self.assertTrue(all(p.npc_direct_projectile for p in shots))
        own = npc_tests.ActualNpcDamageTests.own_vitals(self)
        self.assertEqual(own.total, 250)
        x, y, z = self.a.mover.position
        self.place_player(self.b, (x, y, z + 4))
        self.run.step({self.b: (0, 0, button)})
        return bot, own

    def test_genji_reflects_actual_enforcer_and_sniper_projectiles_away_from_doomfist(self):
        for name in ('Enforcer', 'Sniper'):
            with self.subTest(npc=name):
                bot, own = self.npc_projectile_scene(name, defenses.GENJI, 16)
                self.assertIsNotNone(defenses.deflect(self.b))
                health = self.fight.vitals[bot.entity]
                before = health.total
                for _ in range(50):
                    self.run.step({})
                    if health.total < before:
                        break
                self.assertEqual(own.total, 250)
                self.assertEqual(self.vitals().total, 200)
                dealt = before - health.total
                self.assertGreater(dealt, 0)
                self.assertAlmostEqual(self.b.stats.value(stats.HERO_DAMAGE), dealt, places=5)
                self.assertEqual(self.a.stats.value(stats.HERO_DAMAGE), 0)
                npc_control.stop(self.match)

    def test_sigma_absorbs_actual_enforcer_and_sniper_projectiles_before_they_hit_doomfist(self):
        for name, expected in (('Enforcer', 2), ('Sniper', 100)):
            with self.subTest(npc=name):
                _, own = self.npc_projectile_scene(name, defenses.SIGMA, 8)
                grasp = defenses.grasp(self.b)
                self.assertIsNotNone(grasp)
                for _ in range(50):
                    self.run.step({})
                    if grasp.vars[21312].value() > 0:
                        break
                self.assertEqual(grasp.vars[21312].value(), expected)
                npc_control.stop(self.match)
                self.settle(130)
                self.assertIsNone(defenses.grasp(self.b))
                self.assertAlmostEqual(self.vitals().temporary_shields, expected * .6, places=4)
                self.assertEqual(own.total, 250)

    def test_genji_redirects_actual_trooper_and_heavy_hitscan_rays(self):
        for name, expected in (('Trooper', 20), ('Heavy Assault', 5)):
            with self.subTest(npc=name):
                bot = self.npc_weapon_scene(name, defenses.GENJI)
                own = npc_tests.ActualNpcDamageTests.own_vitals(self)
                health = self.fight.vitals[bot.entity]
                before = health.total
                x, y, z = self.a.mover.position
                self.place_player(self.b, (x, y, z + 4))
                self.run.step({self.b: (0, 0, 16)})
                self.assertIsNotNone(defenses.deflect(self.b))
                for _ in range(120):
                    self.run.step({})
                    if health.total < before:
                        break
                self.assertEqual(before - health.total, expected)
                self.assertEqual(self.b.stats.value(stats.HERO_DAMAGE), expected)
                self.assertEqual(own.total, 250)
                self.assertEqual(self.vitals().total, 200)
                self.assertFalse(self.fight.rockets)
                npc_control.stop(self.match)

    def test_sigma_absorbs_actual_trooper_and_heavy_hitscan_rays(self):
        for name, expected in (('Trooper', 20), ('Heavy Assault', 5)):
            with self.subTest(npc=name):
                self.npc_weapon_scene(name, defenses.SIGMA)
                own = npc_tests.ActualNpcDamageTests.own_vitals(self)
                x, y, z = self.a.mover.position
                self.place_player(self.b, (x, y, z + 4))
                self.run.step({self.b: (0, 0, 8)})
                grasp = defenses.grasp(self.b)
                self.assertIsNotNone(grasp)
                for _ in range(120):
                    self.run.step({})
                    if grasp.vars[21312].value() > 0:
                        break
                self.assertEqual(grasp.vars[21312].value(), expected)
                npc_control.stop(self.match)
                self.settle(130)
                self.assertEqual(own.total, 250)
                self.assertFalse(self.fight.rockets)
                self.assertIsNone(defenses.grasp(self.b))
                self.assertAlmostEqual(self.vitals().temporary_shields, expected * .6, places=4)
