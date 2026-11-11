"""Live Sleep Dart defenses and body lifetime against Junkrat."""
import unittest
from dataclasses import replace

import test_junkrat_projectile_combat as fixtures
from test_game_combat import KANEZAKA_DM, Run, aim
from test_game_mover import box

from ow174.game.collision import World
from ow174.game.content import find_hero


class JunkratSleepDartTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.JunkratProjectileTests()
        heroes = ("Ana", "Zarya", "Junkrat")
        self.run = Run(replace(KANEZAKA_DM, free_for_all=False), heroes)
        self.ana, self.zarya, self.junkrat = self.run.players
        self.match = self.run.match
        self.fight = self.match.combat
        self.match.bots.clear()
        self.match._pending_bot_scripts.clear()
        for player, hero in zip(self.run.players, heroes, strict=True):
            self.assertTrue(self.match.switch_hero(player, find_hero(hero)))
        self.ana.team = 0
        self.zarya.team = self.junkrat.team = 1
        x, y, z = self.junkrat.mover.position
        self.fixture.place(self.run, self.ana, (x, y, z - 5))
        self.fixture.place(self.run, self.zarya, (x + 6, y, z))
        for _ in range(30):
            self.run.step({})
        self.health = self.fixture.health(self.run, self.junkrat)

    def fire_dart(self):
        angles = aim(self.fight._eye(self.ana), self.fight._eye(self.junkrat))
        fired = False
        for frame in range(55):
            self.run.step({self.ana: (*angles, 8 if frame == 0 else 0)})
            airborne = any(rocket.shooter is self.ana and rocket.definition == 0x04000000000015E8
                           for rocket in self.fight.rockets)
            if fired and not airborne:
                return
            fired |= airborne
        self.fail("Sleep Dart did not launch and resolve")

    def assert_awake(self):
        self.assertFalse(self.fight.is_stunned(self.junkrat))
        self.assertEqual((self.health.sleep_until, self.health.sleep_wake_floor), (0, 0))

    def test_projected_barrier_consumes_dart(self):
        target = next(target for target in self.fight._targets() if target.owner is self.junkrat)
        start, end = target.segment()
        center = tuple((lo + hi) * .5 for lo, hi in zip(start, end, strict=True))
        self.run.step({self.zarya: (*aim(self.fight._eye(self.zarya), center), 16)})
        self.fight.barriers.sync(self.zarya)
        bubble, = self.fight.barriers.live.values()
        self.assertIs(bubble.owner, self.junkrat)
        self.assertEqual(bubble.health, 200)
        self.fire_dart()
        self.assertEqual(bubble.health, 195)
        self.assertEqual(self.health.total, 200)
        self.assert_awake()

    def test_world_cover_blocks_dart(self):
        x, y, z = self.junkrat.mover.position
        ground = box(x - 16, x + 16, y - 1, y, z - 16, z + 16)
        wall = box(x - 3, x + 3, y, y + 4, z - 2.6, z - 2.4)
        self.match.collision_world = World(ground + wall)
        self.fire_dart()
        self.assertEqual(self.health.total, 200)
        self.assert_awake()

    def test_ally_does_not_take_damage_or_sleep(self):
        self.junkrat.team = self.ana.team
        self.fire_dart()
        self.assertEqual(self.health.total, 200)
        self.assert_awake()

    def test_lethal_dart_does_not_leave_sleep_on_corpse(self):
        self.health.damage(196)
        self.fire_dart()
        self.assertTrue(self.health.dead)
        self.assertEqual(self.health.killer, self.ana.body)
        self.assertEqual(self.health.total, 0)
        self.assert_awake()

    def test_replacement_body_does_not_inherit_live_sleep(self):
        self.fire_dart()
        old_body = self.junkrat.body
        self.assertTrue(self.fight.is_stunned(self.junkrat))
        self.assertEqual(self.health.total, 195)
        self.assertTrue(self.match.switch_hero(self.junkrat, self.junkrat.hero))
        self.run.step({})
        self.assertNotEqual(self.junkrat.body, old_body)
        self.assertNotIn(old_body, self.fight.vitals)
        self.health = self.fixture.health(self.run, self.junkrat)
        self.assertEqual(self.health.total, 200)
        self.assert_awake()

    def defended_dart(self, hero, button, graph, state, *, active):
        defender = self.zarya
        self.assertTrue(self.match.switch_hero(defender, find_hero(hero)))
        defender.team = self.junkrat.team
        x, y, z = self.junkrat.mover.position
        self.fixture.place(self.run, defender, (x + .48, y, z - 2.5))
        facing = (-32768, 0)
        for _ in range(25):
            self.run.step({defender: (*facing, 0)})
        self.run.step({defender: (*facing, button if active else 0)})
        instance = next(i for i in defender.body_script.component.instances.values() if i.graph.index == graph)
        self.assertEqual(bool(instance.states.get(state) and instance.states[state].active), active)
        ana_health = self.fixture.health(self.run, self.ana)
        defender_health = self.fixture.health(self.run, defender)
        angles = aim(self.fight._eye(self.ana), self.fight._eye(self.junkrat))
        seen = set()
        for frame in range(78):
            self.run.step({self.ana: (*angles, 8 if frame == 0 else 0), defender: (*facing, 0)})
            seen.update(rocket.shooter for rocket in self.fight.rockets
                        if rocket.definition == 0x04000000000015E8)
        self.assertIn(self.ana, seen)
        self.assertEqual(self.health.total, 200)
        self.assert_awake()
        return defender, instance, ana_health, defender_health, seen

    def test_front_deflect_returns_sleep_to_ana(self):
        for active in (False, True):
            with self.subTest(active=active):
                self.setUp()
                genji, _, ana, defender, shooters = self.defended_dart("Genji", 16, 0x03A3, 10, active=active)
                self.assertEqual(genji in shooters, active)
                self.assertEqual((ana.total, defender.total), (195, 200) if active else (200, 195))
                self.assertEqual(self.fight.is_stunned(self.ana), active)
                self.assertEqual(self.fight.is_stunned(genji), not active)

    def test_front_grasp_absorbs_dart_without_sleep(self):
        for active in (False, True):
            with self.subTest(active=active):
                self.setUp()
                sigma, grasp, ana, defender, shooters = self.defended_dart("Sigma", 8, 0x1D02, 1, active=active)
                self.assertNotIn(sigma, shooters)
                self.assertEqual(ana.total, 200)
                self.assertFalse(self.fight.is_stunned(self.ana))
                self.assertEqual(defender.total, 400 if active else 395)
                self.assertEqual(self.fight.is_stunned(sigma), not active)
                if active:
                    self.assertEqual(grasp.vars[21312].value(), 5)
