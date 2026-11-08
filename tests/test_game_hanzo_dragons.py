"""Reconstructed moving dragons execute actual command, capsule and health paths.

Requires ORPHEUS_HANZO_PROFILE and ORPHEUS_NPC_TEST_DATA, both external read-only
inputs. Rate units/geometry/overlap policy are reconstructed, not native proof.
"""

import os
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

import test_game_hanzo as hero_tests
import test_game_hanzo_npc as npc_tests
import test_server_snapshot as snapshot_tests
from test_game_combat import KANEZAKA_DM, Run, aim

from ow174.game import bodytrace, combat, dragon, movelog
from ow174.game.content import SOLDIER, heroes
from ow174.game.server_snapshot import snapshot_digest


def prepared_profile():
    path = os.environ.get("ORPHEUS_HANZO_PROFILE")
    if not path:
        raise unittest.SkipTest("requires explicitly extracted Hanzo profile")
    return dragon.load_profile(Path(path), reconstructed_rates=True)


class DragonPlayerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.profile = prepared_profile()

    def setUp(self):
        for module in (bodytrace, movelog):
            self.enterContext(patch.object(module, "ENABLED", False))

    def arena(self, distance=20):
        run, hero, victim = hero_tests.HanzoCombatTests().arena(distance)
        run.match.combat.configure_dragonstrike(self.profile)
        hero.body_script.ult_seconds = 0
        hero.body_script.set_ult_charge(1680)
        return run, hero, victim

    @staticmethod
    def cast(run, hero, frames=240, yaw=0):
        run.step({hero: (yaw, 0, 32)})
        for _ in range(frames):
            run.step({hero: (yaw, 0, 0)})

    def test_profile_requires_declared_rate_reconstruction(self):
        with self.assertRaises(ValueError):
            dragon.load_profile(Path(os.environ["ORPHEUS_HANZO_PROFILE"]))
        self.assertEqual((self.profile.lifetime, self.profile.speed, self.profile.radius), (20, 12, 4))
        self.assertEqual((self.profile.segments, self.profile.spacing, self.profile.initial_hit_limit),
                         (7, 5, 99))

    def test_range_transition_creates_dragons_that_damage_and_kill(self):
        run, hero, victim = self.arena()
        origin = tuple(run.match.combat._eye(hero))
        self.cast(run, hero)
        fight = run.match.combat
        self.assertTrue(fight.vitals[victim.body].dead)
        self.assertEqual(fight.vitals[victim.body].killer, hero.body)
        flight, = fight.dragons
        self.assertAlmostEqual(flight.origin[2] - origin[2], 10)
        self.assertFalse(fight.rockets)

    def test_same_world_tick_does_not_transform_a_future_subframe_arrow(self):
        run, hero, _ = self.arena()
        fight = run.match.combat
        run.step({hero: (0, 0, 32)})
        for _ in range(100):
            run.step({})
            if fight.rockets:
                break
        arrow, = fight.rockets
        self.assertGreater(arrow.started_tick, run.frame)
        run.match.update(run.frame * combat.TICK, run.frame)
        self.assertIn(arrow, fight.rockets)
        self.assertFalse(fight.dragons)
        self.assertEqual(arrow.flown, 0)
        run.step({})
        self.assertAlmostEqual(arrow.flown, (run.frame - arrow.started_tick) * combat.TICK)

    def test_initial_arrow_pierces_multiple_bodies_without_repeating_damage(self):
        run = Run(KANEZAKA_DM, ("Hanzo", "Near", "Far"))
        match = run.match
        match.bots.clear()
        match._pending_bot_scripts.clear()
        hero, near, far = run.players
        match.switch_hero(hero, heroes()[dragon.HANZO])
        x, y, z = hero.mover.position
        near.mover.position, far.mover.position = [x, y, z + 5], [x, y, z + 8]
        for _ in range(20):
            run.step({})
        match.combat.configure_dragonstrike(self.profile)
        hero.body_script.ult_seconds = 0
        hero.body_script.set_ult_charge(1680)
        self.cast(run, hero, frames=120)
        self.assertEqual(match.combat.vitals[near.body].total, 75)
        self.assertEqual(match.combat.vitals[far.body].total, 75)
        self.assertEqual(len(hero.client.messages(combat.HIT)), 2)
        self.assertEqual(len(match.combat.dragons), 1)

    def test_off_axis_and_allied_players_take_no_damage(self):
        for allied in (False, True):
            with self.subTest(allied=allied):
                run, hero, victim = self.arena()
                if allied:
                    run.match.game_map = replace(run.match.game_map, free_for_all=False)
                    victim.team = hero.team
                self.cast(run, hero, yaw=0 if allied else 16384)
                target = next(t for t in run.match.combat._targets() if t.owner is victim)
                self.assertEqual(run.match.combat.vitals_of(target).total, 200)
                self.assertFalse(hero.client.messages(combat.HIT))

    def test_launched_dragon_survives_caster_body_change_with_original_kill_source(self):
        run, hero, victim = self.arena(distance=40)
        original_body = hero.body
        self.cast(run, hero, frames=100)
        self.assertTrue(run.match.combat.dragons)
        self.assertTrue(run.match.switch_hero(hero, heroes()[SOLDIER]))
        self.assertNotEqual(hero.body, original_body)
        for _ in range(220):
            run.step({})
        vitals = run.match.combat.vitals[victim.body]
        self.assertTrue(vitals.dead)
        self.assertEqual(vitals.killer, original_body)
        notice, = victim.client.messages(combat.DEATH_NOTICE)
        self.assertEqual(notice["+0x7C"]["+0x0"], original_body)

    def test_launched_dragon_survives_caster_death(self):
        run, hero, enemy = self.arena(distance=40)
        self.cast(run, hero, frames=100)
        fight = run.match.combat
        x, y, z = hero.mover.position
        yaw, pitch = aim(fight._eye(enemy), (x, y + 1.55, z))
        for _ in range(220):
            run.step({enemy: (yaw, pitch, 1)})
        self.assertTrue(fight.vitals[hero.body].dead)
        self.assertTrue(fight.vitals[enemy.body].dead)
        self.assertEqual(fight.vitals[enemy.body].killer, hero.body)

    def test_expiry_and_source_leave_retire_flights(self):
        for left in (False, True):
            with self.subTest(left=left):
                run, hero, victim = self.arena(distance=300)
                self.cast(run, hero, frames=100)
                fight = run.match.combat
                flight, = fight.dragons
                if left:
                    hero.gone = True
                    tick = run.frame + 1
                else:
                    tick = run.frame + 1400
                run.match.update(tick * combat.TICK, tick)
                self.assertFalse(fight.dragons)
                if not left:
                    self.assertAlmostEqual(flight.age, 20)
                target = next(t for t in fight._targets() if t.owner is victim)
                self.assertEqual(fight.vitals_of(target).total, 200)

    def test_active_profile_and_flight_age_are_in_stored_state(self):
        case = snapshot_tests.ServerSnapshotTests()
        session, _, players = case.scene()
        player = players[0]
        fight = player.match.combat
        before = snapshot_digest(case.capture(session))
        fight.configure_dragonstrike(self.profile)
        configured = snapshot_digest(case.capture(session))
        self.assertNotEqual(before, configured)
        flight = dragon.Flight(player, player.body, player.team, fight._eye(player), (0, 0, 1),
                               self.profile, player.match.tick)
        fight.dragons.append(flight)
        active = snapshot_digest(case.capture(session))
        self.assertNotEqual(configured, active)
        flight.age = 0.25
        self.assertNotEqual(active, snapshot_digest(case.capture(session)))


class DragonCatalogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.profile = prepared_profile()
        npc_tests.HanzoCatalogNpcTests.setUpClass()
        cls.catalog = npc_tests.HanzoCatalogNpcTests.catalog

    @classmethod
    def tearDownClass(cls):
        npc_tests.HanzoCatalogNpcTests.tearDownClass()

    def setUp(self):
        for module in (bodytrace, movelog):
            self.enterContext(patch.object(module, "ENABLED", False))

    def arena(self, definition, situation="hit", lateral=0):
        run, hero, bot = npc_tests.HanzoCatalogNpcTests().arena(
            definition, "bow", situation, distance=20, lateral=lateral,
        )
        run.match.combat.configure_dragonstrike(self.profile)
        hero.body_script.ult_seconds = 0
        hero.body_script.set_ult_charge(1680)
        return run, hero, bot

    def test_all_five_bodies_take_actual_continuing_damage_and_keep_corpses(self):
        for definition in self.catalog.values():
            with self.subTest(npc=definition.name):
                run, hero, bot = self.arena(definition)
                DragonPlayerTests.cast(run, hero, frames=340)
                fight = run.match.combat
                vitals = fight.vitals[bot.entity]
                self.assertLess(vitals.total, definition.health + definition.shields - 125)
                self.assertEqual(bot.dead, definition.name != "Heavy Assault")
                if bot.dead:
                    self.assertIsNotNone(bot.body_script)
                    self.assertTrue(bot.body_script.component.owner_dead)
                    self.assertEqual([entity for entity, _ in hero.client.npc_deaths], [bot.entity])
                self.assertFalse(any(who is bot for _, who in fight.respawns))
                tick = run.frame + 1000
                run.match.update(tick * combat.TICK, tick)
                self.assertIn(bot, run.match.bots)

    def test_allied_npcs_and_off_axis_attacks_are_excluded(self):
        for situation in ("ally", "miss"):
            for definition in self.catalog.values():
                with self.subTest(npc=definition.name, situation=situation):
                    run, hero, bot = self.arena(definition, situation)
                    DragonPlayerTests.cast(run, hero, yaw=16384 if situation == "miss" else 0)
                    self.assertEqual(run.match.combat.vitals[bot.entity].total,
                                     definition.health + definition.shields)

    def test_wall_transforms_arrow_and_dragons_damage_the_npc_beyond_it(self):
        run, hero, bot = self.arena(self.catalog["5"], "wall")
        origin = run.match.combat._eye(hero)
        DragonPlayerTests.cast(run, hero)
        fight = run.match.combat
        self.assertLess(fight.vitals[bot.entity].total, 4000)
        flight, = fight.dragons
        self.assertAlmostEqual(flight.origin[2] - origin[2], 2.5)

    def test_overlapping_sections_deduplicate_and_outer_band_uses_half_rate(self):
        for lateral, dose in ((0, 4.8), (3, 2.4)):
            with self.subTest(lateral=lateral):
                run, hero, bot = self.arena(self.catalog["5"], lateral=lateral)
                DragonPlayerTests.cast(run, hero, frames=220)
                fight = run.match.combat
                flight, = fight.dragons
                self.assertGreater(flight.age, 2)
                health = fight.vitals[bot.entity].total
                run.step({})
                self.assertAlmostEqual(health - fight.vitals[bot.entity].total, dose)

    def test_skipped_ticks_match_contiguous_damage_and_do_not_extend_lifetime(self):
        outcomes = []
        for skipped in (False, True):
            run, hero, bot = self.arena(self.catalog["5"])
            DragonPlayerTests.cast(run, hero, frames=100)
            fight = run.match.combat
            flight, = fight.dragons
            if skipped:
                tick = run.frame + 1400
                run.match.update(tick * combat.TICK, tick)
            else:
                for _ in range(1400):
                    run.step({})
            self.assertFalse(fight.dragons)
            self.assertAlmostEqual(flight.age, 20)
            outcomes.append(fight.vitals[bot.entity].total)
        self.assertAlmostEqual(*outcomes)
