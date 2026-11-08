"""Actual Sonic input, impact, authored reveal stack and reconstructed search/attachment."""

import math
import os
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

import test_game_hanzo as hero_tests
import test_game_hanzo_npc as npc_tests
import test_server_snapshot as snapshot_tests
from test_game_combat import KANEZAKA_DM, Run, aim, in_world
from test_game_mover import box, wall

from ow174.game import bodytrace, movelog, sonic
from ow174.game.collision import World
from ow174.game.content import SOLDIER, heroes
from ow174.game.script import expr
from ow174.game.server_snapshot import snapshot_digest


def profile():
    path = os.environ.get("ORPHEUS_HANZO_SONIC_PROFILE")
    if not path:
        raise unittest.SkipTest("requires explicitly extracted Sonic profile")
    return sonic.load_profile(Path(path), reconstructed_geometry=True)


class SonicPlayerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.profile = profile()

    def setUp(self):
        for module in (bodytrace, movelog):
            self.enterContext(patch.object(module, "ENABLED", False))

    def arena(self):
        run, hero, target = hero_tests.HanzoCombatTests().arena()
        run.match.combat.configure_sonic(self.profile)
        return run, hero, target

    @staticmethod
    def fire(run, hero, target, frames=70):
        point = target.mover.position if hasattr(target, "mover") else target.position
        yaw, pitch = aim(run.match.combat._eye(hero), (point[0], point[1] + 1, point[2]))
        run.step({hero: (yaw, pitch, 8)})
        run.step({})
        for _ in range(frames):
            run.step({hero: (yaw, pitch, 1)})
        run.step({hero: (yaw, pitch, 0)})
        for _ in range(25):
            run.step({})

    @staticmethod
    def flagged(actor):
        return expr.truthy(actor.body_script.component.entity_var(7982).value())

    def test_full_and_short_draw_impact_execute_real_reveal_stack(self):
        for frames, definition in ((70, sonic.DEFINITIONS[0]), (5, sonic.DEFINITIONS[1])):
            with self.subTest(frames=frames):
                run, hero, target = self.arena()
                fight = run.match.combat
                with patch.object(fight.projectiles, "fired", wraps=fight.projectiles.fired) as shots:
                    self.fire(run, hero, target, frames)
                self.assertEqual([c.args[3] for c in shots.call_args_list], [definition])
                self.assertEqual(len(fight.sonars), 1)
                self.assertTrue(self.flagged(target))
                self.assertTrue(fight.revealed_to(hero, target))
                self.assertFalse(fight.revealed_to(target, target))
                self.assertLess(fight.vitals[target.body].total, 200)
                instance = next(i for i in target.body_script.component.instances.values()
                                if i.graph.index == 0x10E)
                self.assertTrue(instance.state(instance.graph.node(9).state).active)

    def test_static_wall_impact_reveals_enemy_beyond_wall_without_direct_damage(self):
        run, hero, target = self.arena()
        x, y, z = hero.mover.position
        run.match.collision_world = World(box(x - 20, x + 20, y - 1, y, z - 20, z + 20)
                                          + wall(x - 5, z + 3, x + 5, z + 3, y0=y, y1=y + 6))
        self.fire(run, hero, target)
        fight = run.match.combat
        beacon, = fight.sonars
        self.assertIsNone(beacon.attached)
        self.assertEqual(fight.vitals[target.body].total, 200)
        self.assertTrue(fight.revealed_to(hero, target))
        self.assertTrue(self.flagged(target))

    def test_team_projection_and_capsule_range_boundary_refresh(self):
        run, hero, target = self.arena()
        run.match.game_map = replace(run.match.game_map, free_for_all=False)
        ally = run.match.add_player(99, "Observer ally", SOLDIER, hero.team, False)
        in_world(run.match, ally)
        run.match.switch_hero(ally, heroes()[SOLDIER])
        ally.mover.position[:] = target.mover.position
        ally.mover.position[0] += 2
        self.fire(run, hero, target)
        fight = run.match.combat
        beacon, = fight.sonars
        self.assertTrue(fight.revealed_to(ally, target))
        self.assertFalse(fight.revealed_to(target, target))
        self.assertFalse(self.flagged(ally))
        # Freeze the body's attachment before independently testing target range.
        beacon.attached = None
        bx, by, bz = beacon.position
        for distance, expected in ((9.4, True), (9.6, False), (8, True)):
            target.mover.position[:] = (bx + distance, by - 1, bz)
            target.mover.body = None
            run.step({})
            self.assertEqual(fight.revealed_to(hero, target), expected)
            self.assertEqual(self.flagged(target), expected)

    def test_body_attachment_moves_then_missing_body_freezes_last_point(self):
        run, hero, target = self.arena()
        self.fire(run, hero, target)
        fight = run.match.combat
        beacon, = fight.sonars
        self.assertEqual(beacon.attached, target.body)
        before = beacon.position
        target.mover.position[0] += 12
        target.mover.body = None
        run.step({})
        self.assertAlmostEqual(beacon.position[0] - before[0], 12)
        self.assertTrue(fight.revealed_to(hero, target))
        point = beacon.position
        run.match.switch_hero(target, heroes()[SOLDIER])
        run.step({})
        self.assertIsNone(beacon.attached)
        self.assertEqual(beacon.position, point)

    def test_expiry_and_source_death_leave_cleanup_restore_owned_flag(self):
        for leave in (False, True):
            with self.subTest(leave=leave):
                run, hero, target = self.arena()
                self.fire(run, hero, target)
                fight = run.match.combat
                beacon, = fight.sonars
                victim = next(t for t in fight._targets() if t.owner is hero)
                fight._damage(target, victim, 200, False, None, run.frame)
                run.step({})
                self.assertTrue(fight.revealed_to(hero, target))
                if leave:
                    hero.gone = True
                    run.step({})
                else:
                    tick = math.ceil(beacon.born + 6 / 0.016)
                    fight.match.tick = tick
                    fight.update(tick * 0.016, tick, frames=False)
                self.assertFalse(fight.sonars)
                self.assertFalse(self.flagged(target))

    def test_overlap_uses_independent_authored_stacks_and_does_not_erase_base(self):
        run = Run(replace(KANEZAKA_DM, free_for_all=False), ("A", "B", "Target"))
        a, b, target = run.players
        for hero in (a, b):
            run.match.switch_hero(hero, heroes()[sonic.HANZO])
            hero.team = 0
        tank = next(h for h in heroes().values() if h.name == "Roadhog")
        run.match.switch_hero(target, tank)
        target.team = 1
        x, y, z = a.mover.position
        b.mover.position[:] = (x + 1.5, y, z)
        target.mover.position[:] = (x, y, z + 5)
        run.match.bots.clear()
        run.match.combat.configure_sonic(self.profile)
        self.fire(run, a, target)
        self.fire(run, b, target)
        fight = run.match.combat
        first, second = sorted(fight.sonars, key=lambda value: value.born)
        self.assertEqual(len(first.statuses), 1)
        self.assertEqual(len(second.statuses), 1)
        self.assertIs(first.statuses[target.body].graph, second.statuses[target.body].graph)
        tick = math.ceil(first.born + 6 / 0.016)
        fight.match.tick = tick
        fight.update(tick * 0.016, tick, frames=False)
        self.assertEqual(len(fight.sonars), 1)
        self.assertTrue(self.flagged(target))
        target.body_script.component.set(7982, True)  # independent pre-existing reveal provider/base
        tick = math.ceil(second.born + 6 / 0.016)
        fight.match.tick = tick
        fight.update(tick * 0.016, tick, frames=False)
        self.assertFalse(fight.sonars)
        self.assertTrue(self.flagged(target))

    def test_profile_and_beacon_lifetime_change_stored_state(self):
        case = snapshot_tests.ServerSnapshotTests()
        session, _, players = case.scene(two=True)
        fight = players[0].match.combat
        before = snapshot_digest(case.capture(session))
        fight.configure_sonic(self.profile)
        configured = snapshot_digest(case.capture(session))
        self.assertNotEqual(before, configured)
        source = players[0]
        target = players[1]
        target.team = source.team + 1
        beacon = sonic.Beacon(source, source.body, source.team, fight._eye(target), None,
                              (0, 0, 0), source.match.tick, self.profile)
        fight.sonars.append(beacon)
        self.assertTrue(sonic.advance(fight, beacon, source.match.tick))
        self.assertTrue(self.flagged(target))
        active = snapshot_digest(case.capture(session))
        beacon.born += 1
        self.assertNotEqual(active, snapshot_digest(case.capture(session)))
        sonic.remove(beacon, target.body)
        self.assertFalse(self.flagged(target))
        self.assertNotEqual(active, snapshot_digest(case.capture(session)))

    def test_unimpacted_lifetime_expiry_does_not_create_a_sonar_field(self):
        run, hero, target = self.arena()
        run.match.collision_world = World([], name="declared empty Sonic lifetime fixture")
        target.spawned = False
        run.step({hero: (0, -16384, 8)})
        run.step({})
        for _ in range(70):
            run.step({hero: (0, -16384, 1)})
        run.step({hero: (0, -16384, 0)})
        run.step({})
        fight = run.match.combat
        self.assertEqual(len(fight.rockets), 1)
        tick = run.frame + 2000
        fight.match.tick = tick
        fight.update(tick * 0.016, tick, frames=False)
        self.assertFalse(fight.rockets)
        self.assertFalse(fight.sonars)


class SonicCatalogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.profile = profile()
        npc_tests.HanzoCatalogNpcTests.setUpClass()
        cls.catalog = npc_tests.HanzoCatalogNpcTests.catalog

    @classmethod
    def tearDownClass(cls):
        npc_tests.HanzoCatalogNpcTests.tearDownClass()

    def setUp(self):
        for module in (bodytrace, movelog):
            self.enterContext(patch.object(module, "ENABLED", False))

    def test_actual_sonic_impacts_reveal_all_five_actual_npc_bodies(self):
        for definition in self.catalog.values():
            with self.subTest(npc=definition.name):
                run, hero, target = npc_tests.HanzoCatalogNpcTests().arena(definition, "sonic", "hit")
                fight = run.match.combat
                fight.configure_sonic(self.profile)
                SonicPlayerTests.fire(run, hero, target)
                self.assertTrue(fight.revealed_to(hero, target))
                self.assertTrue(SonicPlayerTests.flagged(target))
                self.assertEqual(fight.vitals[target.entity].total,
                                 definition.health + definition.shields - 125)
