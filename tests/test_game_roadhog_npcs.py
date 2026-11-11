"""Real five-body NPC inputs, ordinary Roadhog commands and authoritative pools.

Explicit external inputs are required. The catalog's base pools do not represent
authored difficulty totals; permissive NPC body VM/fixture transport are not AI
or original-client acceptance. NPC attacks/specials still need a live input host.
"""

import math
import os
import unittest
from dataclasses import replace
from itertools import pairwise
from pathlib import Path
from unittest.mock import patch

import test_game_roadhog_defenses as phases
import test_game_roadhog_graviton as graviton_tests
import test_game_roadhog_lucio_barrier as sound_tests
import test_game_roadhog_mei_wall as wall_tests
import test_game_roadhog_stun as stun_tests
from test_game_combat import KANEZAKA_DM, SOLDIER, FakeClient, Run, aim, in_world

from ow174.game import combat, defenses, lucio_barrier, mover, npc, npc_control, reinhardt_charge, sniper_reposition, sombra
from ow174.game.collision import World
from ow174.game.content import heroes
from ow174.game.server import GameServer
from ow174.game.script import graph, nodes
from ow174.game.script.driver import ULT_CHARGE, ULT_COST
from ow174.game.stats import HEALING

ROADHOG = 0x02E0000000000040


class NpcClient(FakeClient):
    """Record death dispatch explicitly; fixture transport is still not a client."""

    def __init__(self, player):
        super().__init__(player)
        self.deaths = []
        self.death_updates = []

    def queue_bot_death(self, entity, payload):
        self.deaths.append((entity, payload))

    def queue_bot_death_update(self, entity, payload):
        self.death_updates.append((entity, payload))

    def bot_stream(self, entity):
        # This pool-damage fixture has no acknowledged projectile observer stream.
        return None


@unittest.skipUnless(os.environ.get("ORPHEUS_NPC_CATALOG") and os.environ.get("ORPHEUS_NPC_GRAPHS"),
                     "explicit prepared five-NPC catalog and graph bundle required")
class RoadhogFiveNpcTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.definitions = npc.load_catalog(Path(os.environ["ORPHEUS_NPC_CATALOG"]))
        known = mover._movers()
        missing = [d.name for d in cls.definitions.values() if d.body not in known]
        if missing:
            raise AssertionError(f"actual NPC mover inputs required, missing:{missing}")
        cls.graph_patch = patch.object(graph, "GRAPHS_PATH", Path(os.environ["ORPHEUS_NPC_GRAPHS"]))
        cls.graph_patch.start()
        cls.clear_graphs()

    @classmethod
    def tearDownClass(cls):
        cls.graph_patch.stop()
        cls.clear_graphs()

    @staticmethod
    def clear_graphs():
        graph._data.cache_clear()
        graph._records.cache_clear()
        graph.graph.cache_clear()

    def session(self, definition, *, distance=2.0, caster=False, difficulty_index=None):
        run = Run(KANEZAKA_DM, ("Roadhog", "McCree") if caster else ("A",))
        match = run.match
        match.bots.clear()
        match._pending_bot_scripts.clear()
        hog = run.players[0]
        hog.client = NpcClient(hog)
        match.switch_hero(hog, heroes()[ROADHOG])
        x, y, z = hog.mover.position
        if caster:
            other = run.players[1]
            match.switch_hero(other, heroes()[stun_tests.MCCREE])
            other.mover.position[:] = (x + 2.5, y, z)
        bot = npc.spawn(match, definition, (x, y, z + distance), team=1, difficulty_index=difficulty_index)
        self.assertIsNotNone(bot.body_script)
        target = next(t for t in match.combat._targets() if t.owner is bot)
        vitals = match.combat.vitals_of(target)
        if difficulty_index is None:
            self.assertEqual(vitals.total, definition.health + definition.armour + definition.shields)
        self.assertEqual(vitals.now[2], definition.shields)
        low, high = target.segment()
        centre = tuple((a + b) / 2 for a, b in zip(low, high, strict=True))
        angles = aim(match.combat._eye(hog), centre)
        return run, hog, bot, vitals, angles

    def test_actual_npc_mover_shapes_drive_capsule_queries(self):
        expected = {"Trooper": (1.7, 0.5), "Enforcer": (1.9, 0.5),
                    "Sniper": (1.75, 0.5), "Assassin": (1.5, 0.5),
                    "Heavy Assault": (2.3, 0.7)}
        for definition in self.definitions.values():
            with self.subTest(npc=definition.name):
                run, _, bot, _, _ = self.session(definition)
                target = next(t for t in run.match.combat._targets() if t.owner is bot)
                height, radius = expected[definition.name]
                self.assertAlmostEqual(target.height, height)
                self.assertAlmostEqual(target.radius, radius)
                a, b = target.segment()
                self.assertAlmostEqual(a[1], target.feet[1] + radius)
                self.assertAlmostEqual(b[1], target.feet[1] + height - radius)

    def test_competing_hooks_on_each_npc_keep_the_first_pull(self):
        from test_game_roadhog_hook import assert_competing_hooks

        for definition in self.definitions.values():
            for reverse in (False, True):
                with self.subTest(npc=definition.name, reverse=reverse):
                    run, first, bot, _, _ = self.session(definition, distance=8, caster=True)
                    second = run.players[1]
                    self.assertTrue(run.match.switch_hero(second, heroes()[ROADHOG]))
                    if reverse:
                        run.players.reverse()
                    assert_competing_hooks(self, run, (first, second), bot, bot.entity)

    def test_actual_ice_wall_blocks_roadhog_slug_and_hook_to_all_five_npcs(self):
        for definition in self.definitions.values():
            with self.subTest(npc=definition.name):
                run, hog, bot, vitals, angles = self.session(definition, distance=12, caster=True)
                mei = run.players[1]
                self.assertTrue(run.match.switch_hero(mei, heroes()[0x02E00000000000DD]))
                run.match.game_map = replace(run.match.game_map, free_for_all=False)
                hog.team, mei.team = 0, bot.team
                fight = run.match.combat
                x, y, z = hog.mover.position
                mei.mover.position[:] = (x, y, z + 8)
                mei.mover.body = None
                for _ in range(12):
                    run.step({})
                _, wall_aim = wall_tests.RoadhogMeiWallTests().place(run, mei)
                mei.mover.position[0] += 12
                mei.mover.body = None
                initial = vitals.total
                run.step({hog: (*angles, 2)})
                for _ in range(48):
                    run.step({hog: (*angles, 0)})
                self.assertEqual(vitals.total, initial)
                self.assertTrue(any(piece.health < 400 for piece in fight.walls.pieces.values()))
                run.step({hog: (*angles, 8)})
                for _ in range(38):
                    run.step({hog: (*angles, 0)})
                    self.assertFalse(fight.hooks.controlled(bot.entity))
                self.assertEqual(vitals.total, initial)
                run.step({mei: (*wall_aim, 16)})
                self.assertFalse(fight.walls.pieces)
                run.step({hog: (*angles, 1)})
                for _ in range(44):
                    run.step({})
                self.assertLess(vitals.total, initial)

    def test_sound_barrier_absorbs_roadhog_slug_on_all_five_npcs(self):
        for definition in self.definitions.values():
            with self.subTest(npc=definition.name):
                run, hog, bot, vitals, angles = self.session(definition, distance=4, caster=True)
                lucio = run.players[1]
                self.assertTrue(run.match.switch_hero(lucio, heroes()[lucio_barrier.HERO]))
                run.match.game_map = replace(run.match.game_map, free_for_all=False)
                hog.team, lucio.team = 0, bot.team
                hog.body_script.ult_seconds = 0
                hog.body_script.set_ult_charge(0)
                x, y, z = hog.mover.position
                lucio.mover.position[:] = (x + 5, y, z)
                lucio.mover.body = None
                for _ in range(12):
                    run.step({})
                fight = run.match.combat
                before = vitals.total
                sound_tests.RoadhogSoundBarrierTests().cast(run, lucio)
                shield = vitals.temporary_shields[lucio_barrier.POOL]
                self.assertEqual(shield.now, 750)
                cast_tick = fight.tick
                run.step({hog: (*angles, 2)})
                for _ in range(30):
                    run.step({})
                    spent = 750 - 2 * (fight.tick - cast_tick) - shield.now
                    if spent:
                        break
                self.assertEqual(spent, 50)
                self.assertEqual(sum(vitals.now.values()), before)
                self.assertEqual(hog.body_script.component.entity_var(342).value(), 0)

    def test_soundwave_damages_and_pushes_all_five_npcs(self):
        for definition in self.definitions.values():
            with self.subTest(npc=definition.name):
                run, hog, bot, vitals, _ = self.session(definition, distance=4, caster=True)
                lucio = run.players[1]
                self.assertTrue(run.match.switch_hero(lucio, heroes()[lucio_barrier.HERO]))
                fight = run.match.combat
                run.match.game_map = replace(run.match.game_map, free_for_all=False)
                hog.team = lucio.team = 0
                for _ in range(12):
                    run.step({})
                target = next(t for t in fight._targets() if t.owner is bot)
                low, high = target.segment()
                centre = tuple((a + b) / 2 for a, b in zip(low, high, strict=True))
                look = aim(fight._eye(lucio), centre)
                before = vitals.total
                position = bot.position
                run.step({lucio: (*look, 2)})
                self.assertIn(bot.entity, fight.impulses.live)
                for _ in range(5):
                    run.step({})
                self.assertEqual(vitals.total, before - (20 if definition.armour else 25))
                self.assertGreater(bot.position[2], position[2])
                self.assertGreater(bot.position[1], position[1])

    def test_soundwave_on_hook_owner_keeps_clear_pulls_on_all_five_npcs(self):
        for definition in self.definitions.values():
            with self.subTest(npc=definition.name):
                run, hog, bot, vitals, angles = self.session(definition, distance=18, caster=True)
                lucio = run.players[1]
                self.assertTrue(run.match.switch_hero(lucio, heroes()[lucio_barrier.HERO]))
                fight = run.match.combat
                run.match.game_map = replace(run.match.game_map, free_for_all=False)
                hog.team, lucio.team = 0, bot.team
                x, y, z = hog.mover.position
                lucio.mover.position[:] = (x, y, z - 4)
                lucio.mover.body = None
                for _ in range(12):
                    run.step({})
                before = vitals.total
                run.step({hog: (*angles, 8)})
                for _ in range(60):
                    run.step({hog: (*angles, 0)})
                    if fight.hooks.controlled(bot.entity):
                        break
                self.assertTrue(fight.hooks.controlled(bot.entity))
                self.assertIsNone(fight.hooks.flights[hog.body].arrived_at)
                look = aim(fight._eye(lucio), (x, y + 1.2, z))
                run.step({lucio: (*look, 2)})
                self.assertIn(hog.body, fight.impulses.live)
                run.step({})
                self.assertGreater(hog.mover.position[2], z)
                self.assertTrue(fight.hooks.controlled(bot.entity))
                self.assertEqual(vitals.total, before - 30)

    def test_rocket_punch_moves_or_kills_each_five_npc_body(self):
        for definition in self.definitions.values():
            for lethal in (False, True):
                with self.subTest(npc=definition.name, lethal=lethal):
                    run, hog, bot, pool, _ = self.session(definition, distance=6, caster=True)
                    fight = run.match.combat
                    doom = run.players[1]
                    doom.client = NpcClient(doom)
                    hero = next(hero for hero in heroes().values() if hero.name == "Doomfist")
                    self.assertTrue(run.match.switch_hero(doom, hero))
                    run.match.game_map = replace(run.match.game_map, free_for_all=False)
                    hog.team = doom.team = 0
                    doom.mover.position[:] = hog.mover.position
                    doom.mover.body = None
                    for _ in range(12):
                        run.step({})
                    if lethal:
                        pool.damage(pool.total - 1)
                    initial = pool.total
                    position = tuple(bot.position)
                    look = aim(fight._eye(doom), (position[0], position[1] + 1.2, position[2]))
                    run.step({doom: (*look, 2)})
                    for _ in range(65):
                        run.step({doom: (*look, 0)})
                    if lethal:
                        self.assertTrue(bot.dead)
                        self.assertEqual(pool.killer, doom.body)
                        self.assertEqual([entity for entity, _ in hog.client.deaths], [bot.entity])
                        self.assertIn(bot, run.match.bots)
                        self.assertNotIn(bot.entity, fight.punch_walls)
                    else:
                        self.assertEqual(pool.total, initial - (45 if definition.armour else 50))
                        self.assertGreater(bot.position[2], position[2] + .3)
                        self.assertNotIn(bot.entity, fight.impulses.live)

    def test_rocket_punch_on_hook_owner_releases_each_five_npc_pull(self):
        for definition in self.definitions.values():
            with self.subTest(npc=definition.name):
                run, hog, bot, pool, angles = self.session(definition, distance=18, caster=True)
                fight = run.match.combat
                doom = run.players[1]
                hero = next(hero for hero in heroes().values() if hero.name == "Doomfist")
                self.assertTrue(run.match.switch_hero(doom, hero))
                run.match.game_map = replace(run.match.game_map, free_for_all=False)
                hog.team, doom.team = 0, bot.team
                x, y, z = hog.mover.position
                doom.mover.position[:] = (x, y, z - 6)
                doom.mover.body = None
                look = aim(fight._eye(doom), (x, y + 1.2, z))
                before = pool.total
                for _ in range(47):
                    run.step({doom: (*look, 2)})
                run.step({doom: (*look, 2), hog: (*angles, 8)})
                for _ in range(43):
                    run.step({doom: (*look, 2), hog: (*angles, 0)})
                    if fight.hooks.controlled(bot.entity):
                        break
                self.assertTrue(fight.hooks.controlled(bot.entity))
                for _ in range(27):
                    run.step({doom: (*look, 0), hog: (*angles, 0)})
                    if fight.is_stunned(hog):
                        break
                self.assertTrue(fight.is_stunned(hog))
                self.assertFalse(fight.hooks.controlled(bot.entity))
                self.assertEqual(bot.velocity, (0, 0, 0))
                self.assertEqual(pool.total, before - 30)

    def test_uppercut_lifts_or_kills_each_five_npc_body(self):
        hero = next(hero for hero in heroes().values() if hero.name == "Doomfist")
        for definition in self.definitions.values():
            for lethal in (False, True):
                with self.subTest(npc=definition.name, lethal=lethal):
                    run, hog, bot, pool, _ = self.session(definition, distance=1.5, caster=True)
                    fight = run.match.combat
                    doom = run.players[1]
                    doom.client = NpcClient(doom)
                    self.assertTrue(run.match.switch_hero(doom, hero))
                    run.match.game_map = replace(run.match.game_map, free_for_all=False)
                    hog.team = doom.team = 0
                    doom.mover.position[:] = hog.mover.position
                    doom.mover.body = None
                    for _ in range(12):
                        run.step({})
                    if lethal:
                        pool.damage(pool.total - 1)
                    before = pool.total
                    x, y, z = bot.position
                    look = aim(fight._eye(doom), (x, y + 1.2, z))
                    run.step({doom: (*look, 8)})
                    if lethal:
                        self.assertTrue(bot.dead)
                        self.assertEqual(pool.killer, doom.body)
                        self.assertEqual([entity for entity, _ in hog.client.deaths], [bot.entity])
                        self.assertIn(bot, run.match.bots)
                        self.assertNotIn(bot.entity, fight.impulses.live)
                    else:
                        self.assertEqual(pool.total, before - (45 if definition.armour else 50))
                        self.assertAlmostEqual(fight.impulses.velocity(bot.entity)[1], 14)
                        for _ in range(6):
                            run.step({})
                        self.assertGreater(bot.position[1], y + 1)
                        self.assertGreater(bot.position[2], z)
                        self.assertFalse(fight.is_stunned(bot))

    def test_uppercut_on_hook_owner_preserves_each_clear_npc_pull(self):
        hero = next(hero for hero in heroes().values() if hero.name == "Doomfist")
        for definition in self.definitions.values():
            with self.subTest(npc=definition.name):
                run, hog, bot, pool, angles = self.session(definition, distance=18, caster=True)
                fight = run.match.combat
                doom = run.players[1]
                self.assertTrue(run.match.switch_hero(doom, hero))
                run.match.game_map = replace(run.match.game_map, free_for_all=False)
                hog.team, doom.team = 0, bot.team
                x, y, z = hog.mover.position
                doom.mover.position[:] = (x, y, z - 1.5)
                doom.mover.body = None
                for _ in range(12):
                    run.step({})
                before = pool.total
                run.step({hog: (*angles, 8)})
                for _ in range(60):
                    run.step({hog: (*angles, 0)})
                    if fight.hooks.controlled(bot.entity):
                        break
                self.assertTrue(fight.hooks.controlled(bot.entity))
                self.assertIsNone(fight.hooks.flights[hog.body].arrived_at)
                look = aim(fight._eye(doom), (x, y + 1.2, z))
                run.step({doom: (*look, 8)})
                self.assertIn(hog.body, fight.impulses.live)
                run.step({})
                self.assertGreater(hog.mover.position[1], y)
                self.assertTrue(fight.hooks.controlled(bot.entity))
                self.assertEqual(pool.total, before - 30)

    def test_slam_ground_lifts_and_aerial_kills_each_five_npc_body(self):
        hero = next(hero for hero in heroes().values() if hero.name == "Doomfist")
        for definition in self.definitions.values():
            for aerial in (False, True):
                with self.subTest(npc=definition.name, aerial=aerial):
                    run, hog, bot, pool, _ = self.session(definition, distance=10, caster=True)
                    fight = run.match.combat
                    doom = run.players[1]
                    doom.client = NpcClient(doom)
                    self.assertTrue(run.match.switch_hero(doom, hero))
                    run.match.game_map = replace(run.match.game_map, free_for_all=False)
                    hog.team = doom.team = 0
                    x, y, z = hog.mover.position
                    doom.mover.position[:] = (x, y + (7 if aerial else 0), z)
                    doom.mover.body = None
                    for _ in range(8):
                        run.step({})
                    if aerial:
                        pool.damage(pool.total - 1)
                    before = pool.total
                    bx, by, bz = bot.position
                    point = (bx, by, bz - 3) if aerial else (bx, by + 1.2, bz)
                    look = aim(fight._eye(doom), point)
                    run.step({doom: (*look, 16)})
                    cast = fight.slams[doom.body]
                    self.assertEqual(cast.destination is not None, aerial)
                    for _ in range(85):
                        run.step({doom: (*look, 0)})
                        if bot.entity in cast.hit:
                            break
                    self.assertIn(bot.entity, cast.hit)
                    if aerial:
                        self.assertTrue(bot.dead)
                        self.assertEqual(pool.killer, doom.body)
                        self.assertEqual([entity for entity, _ in hog.client.deaths], [bot.entity])
                        self.assertIn(bot, run.match.bots)
                        self.assertNotIn(bot.entity, fight.impulses.live)
                    else:
                        self.assertLess(pool.total, before)
                        self.assertGreater(fight.impulses.velocity(bot.entity)[1], 0)
                        for _ in range(6):
                            run.step({})
                        self.assertGreater(bot.position[1], by)
                        self.assertLess(bot.position[2], bz)

    def test_slam_on_hook_owner_preserves_each_clear_npc_pull(self):
        hero = next(hero for hero in heroes().values() if hero.name == "Doomfist")
        for definition in self.definitions.values():
            with self.subTest(npc=definition.name):
                run, hog, bot, pool, angles = self.session(definition, distance=18, caster=True)
                fight = run.match.combat
                doom = run.players[1]
                self.assertTrue(run.match.switch_hero(doom, hero))
                run.match.game_map = replace(run.match.game_map, free_for_all=False)
                hog.team, doom.team = 0, bot.team
                x, y, z = hog.mover.position
                doom.mover.position[:] = (x, y, z - 10)
                doom.mover.body = None
                for _ in range(12):
                    run.step({})
                before = pool.total
                look = aim(fight._eye(doom), (x, y + 1.2, z))
                run.step({doom: (*look, 16), hog: (*angles, 8)})
                cast = fight.slams[doom.body]
                for _ in range(85):
                    run.step({doom: (*look, 0), hog: (*angles, 0)})
                    if hog.body in cast.hit:
                        break
                self.assertIn(hog.body, cast.hit)
                self.assertTrue(fight.hooks.controlled(bot.entity))
                self.assertFalse(fight.is_stunned(hog))
                self.assertEqual(pool.total, before - 30)

    def test_meteor_ring_moves_and_core_kills_each_five_npc_body(self):
        import test_game_roadhog_meteor as meteor_tests

        fixture = meteor_tests.RoadhogMeteorTests()
        hero = next(hero for hero in heroes().values() if hero.name == "Doomfist")
        for definition in self.definitions.values():
            for lethal in (False, True):
                with self.subTest(npc=definition.name, lethal=lethal):
                    run, hog, bot, pool, _ = self.session(definition, distance=1.5 if lethal else 5, caster=True)
                    fight = run.match.combat
                    doom = run.players[1]
                    doom.client = NpcClient(doom)
                    self.assertTrue(run.match.switch_hero(doom, hero))
                    run.match.game_map = replace(run.match.game_map, free_for_all=False)
                    hog.team = doom.team = 0
                    doom.mover.position[:] = hog.mover.position
                    doom.mover.body = None
                    for _ in range(12):
                        run.step({})
                    if lethal:
                        pool.damage(pool.total - 1)
                    before = pool.total
                    _, y, z = bot.position
                    cast = fixture.start(run, doom)
                    fixture.select(run, doom)
                    fixture.land(run, doom, cast)
                    self.assertEqual(cast.hit, (bot.entity,))
                    if lethal:
                        self.assertTrue(bot.dead)
                        self.assertEqual(pool.killer, doom.body)
                        self.assertEqual([entity for entity, _ in hog.client.deaths], [bot.entity])
                        self.assertIn(bot, run.match.bots)
                        self.assertNotIn(bot.entity, fight.impulses.live)
                    else:
                        self.assertAlmostEqual(before - pool.total, 102.5 if definition.armour else 107.5)
                        self.assertGreater(fight.impulses.velocity(bot.entity)[1], 0)
                        for _ in range(5):
                            run.step({})
                        self.assertGreater(bot.position[1], y)
                        self.assertGreater(bot.position[2], z)

    def test_meteor_lift_on_hook_owner_preserves_each_clear_npc_pull(self):
        import test_game_roadhog_meteor as meteor_tests

        fixture = meteor_tests.RoadhogMeteorTests()
        hero = next(hero for hero in heroes().values() if hero.name == "Doomfist")
        for definition in self.definitions.values():
            with self.subTest(npc=definition.name):
                run, hog, bot, pool, angles = self.session(definition, distance=18, caster=True)
                fight = run.match.combat
                doom = run.players[1]
                self.assertTrue(run.match.switch_hero(doom, hero))
                run.match.game_map = replace(run.match.game_map, free_for_all=False)
                hog.team, doom.team = 0, bot.team
                x, y, z = hog.mover.position
                doom.mover.position[:] = (x, y, z - 5)
                doom.mover.body = None
                for _ in range(12):
                    run.step({})
                before = pool.total
                cast = fixture.start(run, doom)
                fixture.select(run, doom)
                run.step({doom: (0, 0, 1)})
                for _ in range(25):
                    run.step({})
                run.step({hog: (*angles, 8)})
                for _ in range(80):
                    run.step({hog: (*angles, 0)})
                    if cast.impacted:
                        break
                self.assertTrue(cast.impacted)
                self.assertEqual(cast.hit, (hog.body,))
                self.assertIn(hog.body, fight.impulses.live)
                self.assertTrue(fight.hooks.controlled(bot.entity))
                self.assertFalse(fight.is_stunned(hog))
                self.assertEqual(pool.total, before - 30)

    def test_piledriver_lifts_or_kills_each_five_npc_body(self):
        import test_game_roadhog_piledriver as pile_tests

        fixture = pile_tests.RoadhogPiledriverTests()
        hero = next(hero for hero in heroes().values() if hero.name == "Wrecking Ball")
        for definition in self.definitions.values():
            for lethal in (False, True):
                with self.subTest(npc=definition.name, lethal=lethal):
                    run, hog, bot, pool, _ = self.session(definition, distance=1.5, caster=True)
                    fight = run.match.combat
                    ball = run.players[1]
                    ball.client = NpcClient(ball)
                    self.assertTrue(run.match.switch_hero(ball, hero))
                    run.match.game_map = replace(run.match.game_map, free_for_all=False)
                    hog.team = ball.team = 0
                    ball.mover.position[:] = hog.mover.position
                    ball.mover.body = None
                    for _ in range(45):
                        run.step({})
                    if lethal:
                        pool.damage(pool.total - 1)
                    before = pool.total
                    y = bot.position[1]
                    fixture.lift(run, ball, 8)
                    fixture.land(run, ball, fixture.start(run, ball))
                    if lethal:
                        self.assertTrue(bot.dead)
                        self.assertEqual(pool.killer, ball.body)
                        self.assertEqual([entity for entity, _ in hog.client.deaths], [bot.entity])
                        self.assertIn(bot, run.match.bots)
                        self.assertNotIn(bot.entity, fight.impulses.live)
                    else:
                        self.assertEqual(before - pool.total, 95 if definition.armour else 100)
                        self.assertGreater(fight.impulses.velocity(bot.entity)[1], 0)
                        for _ in range(6):
                            run.step({})
                        self.assertGreater(bot.position[1], y)

    def test_piledriver_on_hook_owner_preserves_each_clear_npc_pull(self):
        import test_game_roadhog_piledriver as pile_tests

        fixture = pile_tests.RoadhogPiledriverTests()
        hero = next(hero for hero in heroes().values() if hero.name == "Wrecking Ball")
        for definition in self.definitions.values():
            with self.subTest(npc=definition.name):
                run, hog, bot, pool, angles = self.session(definition, distance=18, caster=True)
                fight = run.match.combat
                ball = run.players[1]
                self.assertTrue(run.match.switch_hero(ball, hero))
                run.match.game_map = replace(run.match.game_map, free_for_all=False)
                hog.team, ball.team = 0, bot.team
                x, y, z = hog.mover.position
                ball.mover.position[:] = (x, y, z - 1.5)
                ball.mover.body = None
                for _ in range(45):
                    run.step({})
                before = pool.total
                fixture.lift(run, ball, 8)
                instance = fixture.start(run, ball)
                for _ in range(39):
                    run.step({})
                run.step({hog: (*angles, 8)})
                fixture.land(run, ball, instance)
                self.assertIn(hog.body, fight.impulses.live)
                self.assertTrue(fight.hooks.controlled(bot.entity))
                self.assertFalse(fight.is_stunned(hog))
                self.assertEqual(pool.total, before - 30)

    def test_adaptive_shield_counts_each_five_npc_body_and_still_allows_roadhog_hook(self):
        from ow174.game import adaptive_shield

        hero = next(hero for hero in heroes().values() if hero.name == "Wrecking Ball")
        for definition in self.definitions.values():
            with self.subTest(npc=definition.name):
                run, hog, bot, _, _ = self.session(definition, distance=3, caster=True)
                fight = run.match.combat
                ball = run.players[1]
                self.assertTrue(run.match.switch_hero(ball, hero))
                run.match.game_map = replace(run.match.game_map, free_for_all=False)
                hog.team, ball.team = bot.team, 1 - bot.team
                x, y, z = hog.mover.position
                ball.mover.position[:] = (x + 3, y, z)
                ball.mover.body = None
                for _ in range(45):
                    run.step({})
                run.step({ball: (0, 0, 16)})
                shield = fight.vitals[ball.body].temporary_shields[adaptive_shield.POOL]
                self.assertEqual(shield.now, 250)
                look = aim(fight._eye(hog), fight._eye(ball))
                run.step({hog: (*look, 8)})
                for _ in range(55):
                    run.step({hog: (*look, 0)})
                    if fight.hooks.controlled(ball.body):
                        break
                self.assertTrue(fight.hooks.controlled(ball.body))
                self.assertEqual(shield.now, 220)

    def test_hand_cannon_hits_armour_or_kills_each_five_npc_body(self):
        from ow174.game import hand_cannon

        hero = heroes()[hand_cannon.HERO]
        for definition in self.definitions.values():
            for lethal in (False, True):
                with self.subTest(npc=definition.name, lethal=lethal):
                    run, hog, bot, pool, _ = self.session(definition, distance=1.5, caster=True)
                    fight = run.match.combat
                    doom = run.players[1]
                    doom.client = NpcClient(doom)
                    self.assertTrue(run.match.switch_hero(doom, hero))
                    run.match.game_map = replace(run.match.game_map, free_for_all=False)
                    hog.team = doom.team = 1 - bot.team
                    doom.mover.position[:] = hog.mover.position
                    doom.mover.body = None
                    doom.body_script.ult_seconds = 0
                    doom.body_script.set_ult_charge(0)
                    for _ in range(20):
                        run.step({})
                    if lethal:
                        pool.damage(pool.total - 1)
                    before = pool.total
                    x, y, z = bot.position
                    look = aim(fight._eye(doom), (x, y + 1, z))
                    run.step({doom: (*look, 1)})
                    self.assertEqual(sum(p.shooter is doom for p in fight.rockets), 11)
                    for _ in range(25):
                        run.step({})
                    if lethal:
                        self.assertTrue(bot.dead)
                        self.assertEqual(pool.killer, doom.body)
                        self.assertEqual([entity for entity, _ in hog.client.deaths], [bot.entity])
                        self.assertIn(bot, run.match.bots)
                        self.assertEqual(doom.body_script.component.entity_var(342).value(), 1)
                    else:
                        expected = 11 if definition.armour else 66
                        self.assertEqual(before - pool.total, expected)
                        self.assertEqual(doom.body_script.component.entity_var(342).value(), expected)

    def test_hook_range_and_wall_with_actual_five_npc_shapes(self):
        from ow174.game.collision import World

        for definition in self.definitions.values():
            for distance, wall, caught in ((19.5, False, True), (22.5, False, False),
                                           (10, True, False)):
                with self.subTest(npc=definition.name, distance=distance, wall=wall):
                    run, hog, bot, vitals, angles = self.session(definition, distance=distance)
                    before = vitals.total
                    if wall:
                        x, y, z = hog.mover.position
                        run.match.collision_world = World([
                            (x - 10, y - 10, z + 5, x + 10, y - 10, z + 5,
                             x + 10, y + 10, z + 5),
                            (x - 10, y - 10, z + 5, x + 10, y + 10, z + 5,
                             x - 10, y + 10, z + 5),
                        ])
                    run.step({hog: (*angles, 8)})
                    observed = False
                    for _ in range(100):
                        run.step({})
                        observed |= run.match.combat.hooks.controlled(bot.entity)
                    self.assertEqual(observed, caught)
                    self.assertEqual(vitals.total, before - 30 if caught else before)

    def test_reinhardt_mesh_protects_all_five_actual_npcs_from_roadhog_inputs(self):
        from test_game_roadhog_barriers import RoadhogBarrierTests

        if not os.environ.get("ORPHEUS_REINHARDT_COLLISION"):
            self.skipTest("prepared external104319 Reinhardt descriptor required")
        case = RoadhogBarrierTests()
        for definition in self.definitions.values():
            for button in (1, 2, 8, 32):
                with self.subTest(npc=definition.name, button=button):
                    run, hog, rein, rear, shield = case.session()
                    rear.mover.position[0] = 20
                    rear.mover.body = None
                    y = hog.mover.position[1]
                    bot = npc.spawn(run.match, definition, (0, y, 9), team=1)
                    run.match.game_map = replace(run.match.game_map, free_for_all=False)
                    hog.team, rein.team = 0, 1
                    target = next(t for t in run.match.combat._targets() if t.owner is bot)
                    vitals = run.match.combat.vitals_of(target)
                    before = vitals.total
                    if button == 32:
                        hog.body_script.set_ult_charge(2240)
                    case.step(run, hog, rein, fire=button)
                    for _ in range(70 if button == 32 else 45):
                        case.step(run, hog, rein)
                        self.assertFalse(run.match.combat.hooks.controlled(bot.entity))
                    self.assertEqual(vitals.total, before)
                    self.assertFalse(bot.dead)
                    self.assertLess(shield.current, 1600)
                    self.assertEqual(case.health(run, rein), 20000)
                    self.assertEqual(hog.body_script.component.entity_var(342).value(), 0)

    def test_four_actual_manual_npc_guns_spend_damage_on_ally_mesh_before_roadhog(self):
        from test_game_roadhog_barriers import RoadhogBarrierTests

        if not os.environ.get("ORPHEUS_REINHARDT_COLLISION"):
            self.skipTest("prepared external104319 Reinhardt descriptor required")
        case = RoadhogBarrierTests()
        for definition in self.definitions.values():
            if definition.name == "Assassin":
                continue  # pin is a separate contact/control path, not a projectile gun
            with self.subTest(npc=definition.name):
                run, hog, rein, rear, shield = case.session(6, raised=False)
                for player in run.players:
                    player.client = NpcClient(player)
                run.match.game_map = replace(run.match.game_map, free_for_all=False)
                hog.team = rein.team = 0
                rein.mover.position[0] = 2.5  # selectable NPC ray clears ally body, still crosses mesh
                rear.mover.position[0] = 20
                rein.mover.body = rear.mover.body = None
                bot = npc.spawn(run.match, definition, (0, hog.mover.position[1], 12), team=1)
                for _ in range(15):
                    case.step(run, hog, rein, yaw=0)
                self.assertTrue(shield.active)
                hog.mover.yaw = 0
                hog.mover.pitch = 0
                npc_control.start(run.match, hog)
                for _ in range(250):
                    case.step(run, hog, rein, yaw=0)
                    if shield.current < 1600:
                        break
                self.assertLess(shield.current, 1600)
                self.assertEqual(case.health(run, hog), 600)
                self.assertEqual(case.health(run, rein), 20000)
                npc_control.stop(run.match)
                before = shield.current
                for _ in range(100):
                    case.step(run, hog, rein, yaw=0)
                self.assertEqual(shield.current, before)
                self.assertEqual(case.health(run, hog), 600)
                self.assertFalse(bot.dead)

    def enforcer_engagement(self, distance=2, *, support=None):
        definition = next(d for d in self.definitions.values() if d.name == "Enforcer")
        run, hog, bot, vitals, angles = self.session(definition, distance=distance, caster=support is not None)
        self.assertEqual(bot.body_script.component.world.controller_components, (75,))
        if support is not None:
            ally = run.players[1]
            self.assertTrue(run.match.switch_hero(ally, heroes()[support]))
            run.match.game_map = replace(run.match.game_map, free_for_all=False)
            hog.team = ally.team = 0
            x, y, z = bot.position
            ally.mover.position[:] = (x + 2, y, z)
            ally.mover.body = None
        hog.mover.yaw, hog.mover.pitch = angles
        npc_control.start(run.match, hog)
        target = next(t for t in run.match.combat._targets() if t.owner is hog)
        return run, hog, bot, vitals, angles, run.match.combat.vitals_of(target)

    def test_trooper_control_and_stop_preserve_only_released_rounds(self):
        first, second = self.trooper_interrupted_burst()
        for mode, offset, committed in (
            ("stop before launch", first, 0), ("stop after launch", first + 1, 1),
            ("switch to melee", first + 1, 1),
            ("hook at first shot", first - 14, 0), ("hook at second shot", second - 14, 1),
        ):
            with self.subTest(mode=mode):
                self.trooper_interrupted_burst(mode, offset, committed)

    def test_trooper_reload_follows_real_empty_magazine_under_hook_or_stop(self):
        empty, refill, _ = self.trooper_reload_trace()
        last_round = self.trooper_reload_trace("last round Hook", empty - 14)
        self.assertGreater(last_round[0], empty)
        self.assertEqual(last_round[1] - last_round[0], refill - empty)
        for mode in ("reload Hook", "reload stop"):
            with self.subTest(mode=mode):
                offset = refill - 22 if mode == "reload Hook" else empty + 1
                observed = self.trooper_reload_trace(mode, offset)
                self.assertEqual(observed[:2], (empty, refill))

    def trooper_reload_trace(self, mode=None, offset=None):
        definition = next(d for d in self.definitions.values() if d.name == "Trooper")
        run, hog, bot, _, look = self.session(definition, distance=4)
        fight = run.match.combat
        # Keep the same Roadhog body alive for a full magazine and refill.
        own = fight.vitals[hog.body] = combat.Vitals(hog.body, 20000)
        hog.mover.yaw, hog.mover.pitch = look
        self.assertEqual(npc_control.start(run.match, hog), bot.entity)
        instance = next(i for i in bot.body_script.component.instances.values() if i.graph.index == 0x102E)
        ammo = instance.find_var(0, 53)
        initial = run.frame
        original = fight._shoot
        shots = []
        empty = refill = None
        latched = False
        resumed = False

        def observe(shooter, *args, **kwargs):
            if shooter is bot:
                shots.append((run.frame - initial, ammo.value()))
            return original(shooter, *args, **kwargs)

        with patch.object(fight, "_shoot", side_effect=observe):
            for tick in range(1, 1350):
                pressed = tick == offset
                if pressed and mode == "reload stop":
                    self.assertEqual(ammo.value(), 0)
                    self.assertEqual(npc_control.stop(run.match), 1)
                before = ammo.value()
                hook = pressed and mode != "reload stop"
                run.step({hog: (*look, 8 if hook else 0)})
                after = ammo.value()
                controlled = fight.hooks.controlled(bot.entity)
                if controlled:
                    latched = True
                    self.assertFalse(bot.attack_input)
                    if mode == "last round Hook":
                        self.assertEqual((len(shots), after), (29, 1))
                    else:
                        self.assertEqual(len(shots), 30)
                if after == 0 and before == 1:
                    empty = tick
                    self.assertEqual(len(shots), 30)
                    self.assertEqual(own.total, 19400)
                if before == 0 and after == 30:
                    self.assertIsNotNone(empty)
                    refill = tick
                    if mode == "reload Hook":
                        self.assertTrue(controlled)
                    self.assertEqual(len(shots), 30)
                if mode == "reload stop" and offset <= tick and not resumed:
                    self.assertEqual(len(shots), 30)
                    self.assertEqual(own.total, 19400)
                    if refill is not None and tick == refill + 100:
                        self.assertEqual(after, 30)
                        hog.mover.yaw, hog.mover.pitch = look
                        self.assertEqual(npc_control.start(run.match, hog), bot.entity)
                        resumed = True
                if len(shots) == 31:
                    self.assertFalse(controlled)
                    break
        self.assertEqual([rounds for _, rounds in shots], [*range(29, -1, -1), 29])
        self.assertIsNotNone(empty)
        self.assertIsNotNone(refill)
        self.assertLess(refill, shots[-1][0])
        self.assertEqual(own.total, 19380)
        if mode is not None and mode.endswith("Hook"):
            self.assertTrue(latched)
        return empty, refill, shots[-1][0]

    def trooper_interrupted_burst(self, mode=None, offset=None, committed=0):
        definition = next(d for d in self.definitions.values() if d.name == "Trooper")
        run, hog, bot, _, look = self.session(definition, distance=4)
        fight = run.match.combat
        hog.mover.yaw, hog.mover.pitch = look
        self.assertEqual(npc_control.start(run.match, hog), bot.entity)
        instance = next(i for i in bot.body_script.component.instances.values() if i.graph.index == 0x102E)
        ammo = instance.find_var(0, 53)
        own = fight.vitals_of(next(target for target in fight._targets() if target.owner is hog))
        initial = run.frame
        hooked = mode is not None and mode.startswith("hook")
        shots = []
        original = fight._shoot

        def observe(shooter, *args, **kwargs):
            if shooter is bot:
                shots.append(run.frame - initial)
            return original(shooter, *args, **kwargs)

        with patch.object(fight, "_shoot", side_effect=observe):
            if mode is None:
                for _ in range(85):
                    run.step({})
                    if len(shots) == 3:
                        break
                self.assertEqual(ammo.value(), 27)
                return shots[0], shots[1]

            while run.frame < initial + offset - 1:
                run.step({hog: (*look, 0)})
            if hooked:
                run.step({hog: (*look, 8)})
                for _ in range(30):
                    if fight.hooks.controlled(bot.entity):
                        break
                    run.step({hog: (*look, 0)})
                self.assertTrue(fight.hooks.controlled(bot.entity))
                self.assertEqual(run.frame, initial + offset + 14)
                while fight.hooks.controlled(bot.entity):
                    self.assertEqual(ammo.value(), 30 - committed)
                    self.assertEqual(len(shots), committed)
                    run.step({hog: (*look, 0)})
            else:
                if mode == "switch to melee":
                    hog.mover.yaw, hog.mover.pitch = look
                    self.assertEqual(npc_control.start(run.match, hog, action="melee"), bot.entity)
                else:
                    self.assertEqual(npc_control.stop(run.match), 1)
                for _ in range(25):
                    run.step({hog: (*look, 0)})
                    self.assertEqual(ammo.value(), 30 - committed)
                    self.assertEqual(len(shots), committed)
                hog.mover.yaw, hog.mover.pitch = look
                self.assertEqual(npc_control.start(run.match, hog), bot.entity)

            self.assertEqual(ammo.value(), 30 - committed)
            self.assertEqual(len(shots), committed)
            self.assertEqual(own.total, 600 - 20 * committed)
            for _ in range(125):
                run.step({hog: (*look, 0)})
                if len(shots) > committed:
                    break
            self.assertEqual(len(shots), committed + 1)
            self.assertEqual(ammo.value(), 29 - committed)

    def test_enforcer_actual_manual_attack_stop_restart_and_pool_health_sync(self):
        from ow174.game.script.driver import OWNER_HEALTH

        run, hog, bot, vitals, _, own = self.enforcer_engagement()
        self.assertEqual(bot.body_script.component.entity_var(OWNER_HEALTH).value(), vitals.total)
        fight = run.match.combat
        with patch.object(fight, "_shoot_pellet", wraps=fight._shoot_pellet) as fired:
            for _ in range(250):
                run.step({})
                if own.total < 600:
                    break
        pellets = [call for call in fired.call_args_list if call.args[0] is bot]
        self.assertEqual(len(pellets), 25)
        self.assertEqual(len({call.args[6] for call in pellets}), 25)
        self.assertEqual(len({call.kwargs["activation"] for call in pellets}), 1)
        self.assertEqual(own.total, 550)
        npc_control.stop(run.match)
        for _ in range(100):
            run.step({})
        self.assertEqual(own.total, 550)
        npc_control.start(run.match, hog)
        for _ in range(250):
            run.step({})
            if own.total < 550:
                break
        self.assertEqual(own.total, 500)

    def test_actual_enforcer_volley_depletes_magazine_and_reloads_once(self):
        run, _, bot, _, _, own = self.enforcer_engagement()
        fight = run.match.combat
        instance = next(i for i in bot.body_script.component.instances.values() if i.graph.index == 0x13EB)
        ammo = instance.find_var(0, 53)
        self.assertEqual(ammo.value(), 4)
        changes = []
        shots = []
        original = fight._shoot

        def observe(shooter, command, state, number, time, **kwargs):
            if shooter is bot:
                shots.append((run.frame, time, ammo.value()))
            return original(shooter, command, state, number, time, **kwargs)

        with patch.object(fight, "_shoot", side_effect=observe):
            previous = ammo.value()
            for _ in range(1000):
                run.step({})
                current = ammo.value()
                if current != previous:
                    changes.append((run.frame, current))
                    previous = current
                if len(shots) == 5:
                    break

        self.assertEqual([count for _, count in changes], [3, 2, 1, 0, 4, 3])
        self.assertEqual([count for _, _, count in shots], [3, 2, 1, 0, 3])
        for first, second in pairwise(shots):
            self.assertGreaterEqual(second[1] - first[1], 1000)
        empty_frame, refill_frame = changes[3][0], changes[4][0]
        self.assertLess(empty_frame, refill_frame)
        self.assertLess(refill_frame, shots[4][0])
        self.assertEqual(own.total, 400)
        for _ in range(15):
            run.step({})
        self.assertEqual(own.total, 350)

    def test_enforcer_hook_preserves_reload_but_cancels_unreleased_volley(self):
        refill, fire = self.enforcer_reload_after_hook()
        for phase, offset in (("reload", refill - 22), ("windup", fire - 20), ("release frame", fire - 13)):
            with self.subTest(phase=phase):
                observed_refill, observed_fire = self.enforcer_reload_after_hook(offset, phase=phase, fire_at=fire)
                self.assertEqual(observed_refill, refill)
                if phase == "reload":
                    self.assertEqual(observed_fire, fire)
                else:
                    self.assertGreater(observed_fire, fire)

    def test_enforcer_lost_attack_input_cancels_unreleased_volley(self):
        for mode in ("stop", "immediate restart", "sleep", "cover"):
            with self.subTest(mode=mode):
                self.check_enforcer_windup_cancel(mode)

    def check_enforcer_windup_cancel(self, mode):
        sleeping = mode == "sleep"
        support = 0x02E000000000013B if sleeping else None
        run, hog, bot, vitals, angles, own = self.enforcer_engagement(support=support)
        fight = run.match.combat
        instance = next(i for i in bot.body_script.component.instances.values() if i.graph.index == 0x13EB)
        ammo = instance.find_var(0, 53)
        for _ in range(80):
            run.step({})
            windup = instance.states.get(9)
            if windup is not None and windup.active:
                break
        self.assertTrue(windup.active)
        activation = windup.counter
        self.assertEqual(ammo.value(), 4)
        if sleeping:
            ana = run.players[1]
            x, y, z = bot.position
            look = aim(fight._eye(ana), (x, y + 1.15, z))
            before = vitals.total
            run.step({ana: (*look, 8)})
            for _ in range(35):
                if bot.entity in fight.sleep:
                    break
                run.step({ana: (*look, 0)})
            self.assertEqual(vitals.total, before - 5)
            deadline = fight.sleep[bot.entity]
        elif mode == "cover":
            world = run.match.collision_world
            x, y, z = hog.mover.position
            wall = (z + bot.position[2]) / 2
            run.match.collision_world = World([
                (x - 20, y, z - 20, x - 20, y, z + 20, x + 20, y, z + 20),
                (x - 20, y, z - 20, x + 20, y, z + 20, x + 20, y, z - 20),
                (x - 3, y - 3, wall, x + 3, y - 3, wall, x + 3, y + 3, wall),
                (x - 3, y - 3, wall, x + 3, y + 3, wall, x - 3, y + 3, wall),
            ])
            deadline = run.frame + 130
        else:
            self.assertEqual(npc_control.stop(run.match), 1)
            deadline = run.frame if mode == "immediate restart" else run.frame + 130
        while run.frame < deadline - 1:
            run.step({})
            self.assertFalse(bot.attack_input)
            self.assertEqual(windup.counter, activation)
            self.assertEqual(ammo.value(), 4)
            self.assertEqual(own.total, 600)
        self.assertFalse(windup.active)
        if mode == "cover":
            run.match.collision_world = world
        elif not sleeping:
            hog.mover.yaw, hog.mover.pitch = angles
            self.assertEqual(npc_control.start(run.match, hog), bot.entity)
        for _ in range(250):
            run.step({})
            if own.total < 600:
                break
        self.assertGreater(windup.counter, activation)
        self.assertEqual(own.total, 550)
        self.assertEqual(ammo.value(), 3)

    def test_enforcer_released_pellet_reaches_roadhog_during_later_stun(self):
        run, _, bot, vitals, _, own = self.enforcer_engagement(distance=12, support=stun_tests.MCCREE)
        fight = run.match.combat
        caster = run.players[1]
        instance = next(i for i in bot.body_script.component.instances.values() if i.graph.index == 0x13EB)
        ammo = instance.find_var(0, 53)
        for _ in range(180):
            run.step({})
            if ammo.value() < 4:
                break
        self.assertEqual(ammo.value(), 3)
        self.assertEqual(own.total, 600)
        x, y, z = bot.position
        look = aim(fight._eye(caster), (x, y + 1.15, z))
        before = vitals.total
        run.step({caster: (*look, 16)})
        for _ in range(25):
            if fight.is_stunned(bot):
                break
            run.step({caster: (*look, 0)})
        self.assertTrue(fight.is_stunned(bot))
        self.assertEqual(vitals.total, before - 25)
        self.assertEqual(own.total, 600)
        for _ in range(30):
            run.step({})
            self.assertTrue(fight.is_stunned(bot))
            self.assertEqual(ammo.value(), 3)
            if own.total < 600:
                break
        self.assertLess(own.total, 600)

    def enforcer_reload_after_hook(self, offset=None, *, phase=None, fire_at=None):
        run, hog, bot, vitals, angles, own = self.enforcer_engagement()
        fight = run.match.combat
        instance = next(i for i in bot.body_script.component.instances.values() if i.graph.index == 0x13EB)
        ammo = instance.find_var(0, 53)
        first, refill, fire = None, None, None
        latched_at, released_at = None, None
        shots = []
        original = fight._shoot

        def observe(shooter, *args, **kwargs):
            if shooter is bot:
                shots.append((run.frame, ammo.value()))
            return original(shooter, *args, **kwargs)

        with patch.object(fight, "_shoot", side_effect=observe):
            for _ in range(1000):
                cast = offset is not None and first is not None and run.frame == first + offset
                previous = ammo.value()
                run.step({hog: (*angles, 8 if cast else 0)})
                current = ammo.value()
                if first is None:
                    if current == 0:
                        first = run.frame
                    continue
                controlled = fight.hooks.controlled(bot.entity)
                if controlled:
                    if latched_at is None:
                        latched_at = run.frame
                        self.assertEqual(vitals.total, 320)
                        if phase != "reload":
                            self.assertEqual(previous, 4)
                            self.assertGreater(instance.states[9].counter, 4)
                            self.assertFalse(instance.states[9].active)
                    self.assertFalse(bot.attack_input)
                    self.assertGreaterEqual(current, previous, "Hook must not spend an unreleased round")
                    self.assertEqual(len(shots), 4)
                    self.assertEqual(own.total, 400)
                elif latched_at is not None and released_at is None:
                    released_at = run.frame
                if previous == 0 and current == 4:
                    refill = run.frame
                if len(shots) == 5:
                    fire = run.frame
                    break
        self.assertIsNotNone(refill)
        self.assertIsNotNone(fire)
        self.assertEqual([count for _, count in shots], [3, 2, 1, 0, 3])
        if phase == "reload":
            self.assertLess(latched_at, refill)
            self.assertLess(refill, released_at)
        elif phase is not None:
            self.assertLess(refill, latched_at)
            if phase == "release frame":
                self.assertEqual(latched_at, first + fire_at)
            else:
                self.assertLess(latched_at, first + fire_at)
            self.assertLess(first + fire_at, released_at)
            self.assertGreater(fire, released_at)
        for _ in range(15):
            run.step({})
        self.assertEqual(own.total, 350)
        return refill - first, fire - first

    def test_breather_reduces_actual_enforcer_hit_at_impact(self):
        # Find the actual first shot time with an unchanged resource/clock, then
        # begin E40 frames before that hit in a fresh identical engagement.
        run, _, _, _, _, own = self.enforcer_engagement()
        first = None
        for elapsed in range(250):
            run.step({})
            if own.total < 600:
                first = elapsed
                break
        self.assertIsNotNone(first)
        self.assertGreater(first, 40)
        run, hog, bot, _, angles, own = self.enforcer_engagement()
        fight = run.match.combat
        original = fight._damage
        hits = []

        def observe(shooter, target, *args, **kwargs):
            before = own.total
            scale = fight.incoming_damage_scale(hog)
            result = original(shooter, target, *args, **kwargs)
            if shooter is bot and target.owner is hog:
                hits.append((before - own.total, scale))
            return result

        with patch.object(fight, "_damage", side_effect=observe):
            for elapsed in range(first + 1):
                run.step({hog: (*angles, 16 if elapsed == first - 40 else 0)})
        self.assertEqual(hits, [(1.0, 0.5)] * 25)

    def test_hook_stops_actual_enforcer_attack_and_release_recovers(self):
        run, hog, bot, vitals, angles, own = self.enforcer_engagement(distance=10)
        run.step({hog: (*angles, 8)})
        caught = False
        for _ in range(100):
            run.step({})
            if run.match.combat.hooks.controlled(bot.entity):
                caught = True
                self.assertEqual(own.total, 600)
        self.assertTrue(caught)
        self.assertEqual(vitals.total, sum(vitals.most.values()) - 30)
        self.assertFalse(run.match.combat.hooks.controlled(bot.entity))
        for _ in range(250):
            run.step({})
            if own.total < 600:
                break
        self.assertLess(own.total, 600)

    def test_flashbang_on_hook_owner_releases_all_five_npc_pulls_and_velocity(self):
        for definition in self.definitions.values():
            with self.subTest(npc=definition.name):
                run, hog, bot, vitals, angles = self.session(definition, distance=18, caster=True)
                caster = run.players[1]
                fight = run.match.combat
                run.step({hog: (*angles, 8)})
                for _ in range(60):
                    run.step({hog: (*angles, 0)})
                    if fight.hooks.controlled(bot.entity):
                        break
                self.assertTrue(fight.hooks.controlled(bot.entity))
                self.assertIsNone(fight.hooks.flights[hog.body].arrived_at)
                stun_tests.RoadhogStunTests().cast(run, caster, hog)
                self.assertTrue(fight.is_stunned(hog))
                self.assertFalse(fight.hooks.controlled(bot.entity))
                self.assertEqual(bot.velocity, (0, 0, 0))
                position = tuple(bot.position)
                for _ in range(80):
                    run.step({})
                self.assertEqual(tuple(bot.position), position)
                self.assertEqual(bot.velocity, (0, 0, 0))
                self.assertEqual(vitals.total,
                                 definition.health + definition.armour + definition.shields - 30)

    def test_accretion_on_hook_owner_releases_all_five_npc_pulls(self):
        for definition in self.definitions.values():
            with self.subTest(npc=definition.name):
                self.check_control_releases_npc_pull(definition, earthshatter=False)

    def test_earthshatter_on_hook_owner_releases_all_five_npc_pulls(self):
        for definition in self.definitions.values():
            with self.subTest(npc=definition.name):
                self.check_control_releases_npc_pull(definition, earthshatter=True)

    def check_control_releases_npc_pull(self, definition, *, earthshatter):
        run, hog, bot, vitals, angles = self.session(definition, distance=18, caster=True)
        caster = run.players[1]
        hero = 0x02E0000000000007 if earthshatter else 0x02E000000000023B
        self.assertTrue(run.match.switch_hero(caster, heroes()[hero]))
        if earthshatter:
            run.match.game_map = replace(run.match.game_map, free_for_all=False)
            hog.team, caster.team = 0, bot.team
            script = caster.body_script
            script.set_ult_charge(script.component.entity_var(ULT_COST).value())
        cast = 32 if earthshatter else 16
        control_frames = 157 if earthshatter else 50
        fight = run.match.combat
        x, y, z = hog.mover.position
        caster.mover.position[:] = (x + (8 if earthshatter else 2.5), y, z)
        caster.mover.body = None
        look = aim(fight._eye(caster), fight._eye(hog))
        for _ in range(20):
            run.step({caster: (*look, 0)})
        initial = vitals.total
        latched = False
        for offset in range(100):
            run.step({
                hog: (*angles, 8 if offset == 10 else 0),
                caster: (*look, cast if offset == 0 else 0),
            })
            if fight.is_stunned(hog):
                break
            flight = fight.hooks.flights.get(hog.body)
            if flight is not None and flight.arrived_at is None:
                latched |= fight.hooks.controlled(bot.entity)
        self.assertTrue(latched)
        self.assertTrue(fight.is_stunned(hog))
        self.assertEqual(fight.stuns[hog.body] - run.frame, control_frames)
        self.assertNotIn(hog.body, fight.hooks.flights)
        self.assertFalse(fight.hooks.controlled(bot.entity))
        self.assertEqual(bot.velocity, (0, 0, 0))
        position = tuple(bot.position)
        for _ in range(control_frames + 15):
            run.step({})
        self.assertEqual(tuple(bot.position), position)
        self.assertEqual(bot.velocity, (0, 0, 0))
        self.assertEqual(vitals.total, initial - 30)
        self.assertGreater(stun_tests.RoadhogStunTests().ability(hog, 0x01C8).remaining(), 0)

    def test_mei_primary_freeze_releases_all_five_live_npc_hook_pulls(self):
        for definition in self.definitions.values():
            with self.subTest(npc=definition.name):
                self.check_new_control_releases_npc_pull(definition, freeze=True)

    def test_reinhardt_charge_pin_releases_all_five_live_npc_hook_pulls(self):
        for definition in self.definitions.values():
            with self.subTest(npc=definition.name):
                self.check_new_control_releases_npc_pull(definition, freeze=False)

    def check_new_control_releases_npc_pull(self, definition, *, freeze):
        run, hog, bot, vitals, angles = self.session(definition, distance=14, caster=True)
        caster = run.players[1]
        hero = 0x02E00000000000DD if freeze else reinhardt_charge.HERO
        self.assertTrue(run.match.switch_hero(caster, heroes()[hero]))
        run.match.game_map = replace(run.match.game_map, free_for_all=False)
        hog.team, caster.team = 0, bot.team
        fight = run.match.combat
        x, y, z = hog.mover.position
        caster.mover.position[:] = (x + (4 if freeze else 6), y, z)
        caster.mover.body = None
        sight = aim(fight._eye(caster), (x, y + 1, z))
        initial = vitals.total
        for _ in range(16):
            run.step({caster: (*sight, 0)})
        pair = caster.body, hog.body, 0
        started = False
        for offset in range(145):
            if freeze:
                cast_hook = not started and fight.mei_frost.get(pair, (0, 0))[0] >= 7130
                caster_input = (*sight, 1)
            else:
                cast_hook = offset == 18
                caster_input = (sight[0], 0, 8 if offset == 0 else 0)
            started |= cast_hook
            flight = fight.hooks.flights.get(hog.body)
            live_pull = flight is not None and flight.arrived_at is None and fight.hooks.controlled(bot.entity)
            run.step({hog: (*angles, 8 if cast_hook else 0), caster: caster_input})
            if fight.is_stunned(hog):
                break
        self.assertTrue(started)
        self.assertTrue(fight.is_stunned(hog))
        self.assertTrue(live_pull)
        if freeze:
            self.assertEqual(fight.control_deadline(hog.body) - fight.tick, 82)
        else:
            self.assertTrue(reinhardt_charge.pinned(fight, hog.body))
        self.assertNotIn(hog.body, fight.hooks.flights)
        self.assertFalse(fight.hooks.controlled(bot.entity))
        self.assertEqual(bot.velocity, (0, 0, 0))
        position = tuple(bot.position)
        for _ in range(30):
            run.step({hog: (*angles, 8)})
        self.assertNotIn(hog.body, fight.hooks.flights)
        self.assertEqual(tuple(bot.position), position)
        self.assertEqual(bot.velocity, (0, 0, 0))
        self.assertEqual(vitals.total, initial - 30)
        self.assertGreater(stun_tests.RoadhogStunTests().ability(hog, 0x01C8).remaining(), 0)

    def test_mei_blizzard_freeze_releases_all_five_live_npc_hook_pulls(self):
        for definition in self.definitions.values():
            with self.subTest(npc=definition.name):
                run, hog, bot, vitals, angles = self.session(definition, distance=14, caster=True)
                caster = run.players[1]
                self.assertTrue(run.match.switch_hero(caster, heroes()[0x02E00000000000DD]))
                run.match.game_map = replace(run.match.game_map, free_for_all=False)
                hog.team, caster.team = 0, bot.team
                fight = run.match.combat
                x, y, z = hog.mover.position
                caster.mover.position[:] = (x + 5, y, z)
                caster.mover.body = None
                for _ in range(12):
                    run.step({})
                initial = vitals.total
                area = phases.RoadhogDefenseTests().launch_mei_blizzard(run, hog, caster)
                while fight.tick * 16 < area.starts + 2600 - 480:
                    run.step({})
                run.step({hog: (*angles, 8)})
                for _ in range(50):
                    flight = fight.hooks.flights.get(hog.body)
                    live = flight is not None and flight.arrived_at is None and fight.hooks.controlled(bot.entity)
                    run.step({hog: (*angles, 0)})
                    if fight.is_stunned(hog):
                        break
                self.assertTrue(fight.is_stunned(hog))
                self.assertTrue(live)
                self.assertEqual(fight.control_deadline(hog.body) - fight.tick, 82)
                self.assertNotIn(hog.body, fight.hooks.flights)
                self.assertFalse(fight.hooks.controlled(bot.entity))
                self.assertEqual(bot.velocity, (0, 0, 0))
                position = tuple(bot.position)
                for _ in range(35):
                    run.step({hog: (*angles, 8)})
                self.assertEqual(tuple(bot.position), position)
                self.assertNotIn(hog.body, fight.hooks.flights)
                self.assertEqual(vitals.total, initial - 30)

    def test_roadhog_hook_still_pulls_all_five_npcs_under_trap_or_graviton(self):
        for definition in self.definitions.values():
            for trap in (False, True):
                with self.subTest(npc=definition.name, trap=trap):
                    self.check_restrained_roadhog_can_pull_npc(definition, trap=trap)

    def check_restrained_roadhog_can_pull_npc(self, definition, *, trap):
        run, hog, bot, vitals, _ = self.session(definition, distance=12, caster=True)
        caster = run.players[1]
        hero = 0x02E0000000000065 if trap else 0x02E0000000000068
        self.assertTrue(run.match.switch_hero(caster, heroes()[hero]))
        run.match.game_map = replace(run.match.game_map, free_for_all=False)
        hog.team, caster.team = 0, bot.team
        fight = run.match.combat
        x, y, z = hog.mover.position
        caster.mover.position[:] = (x + 5, y, z)
        caster.mover.body = None
        if trap:
            hog.mover.position[:] = (x, y, z - 3)
            hog.mover.body = None
        for _ in range(12):
            run.step({})
        if trap:
            run.step({caster: (-16384, 0, 16)})
            for _ in range(70):
                run.step({caster: (-16384, 0, 0)})
            device, = fight.traps.live.values()
            self.assertIsNone(device.victim)
            hog.mover.position[:] = device.flight.position
            hog.mover.body = None
        else:
            graviton_tests.RoadhogGravitonTests().launch(run, caster, (x + 2, y, z))
        run.step({})

        def restrained():
            if trap:
                return fight.traps.rooted(hog.body, fight.tick)
            return hog.body in fight.gravitons.pulled

        self.assertTrue(restrained())
        initial = vitals.total
        start = math.dist(bot.position, hog.mover.position)
        target = next(t for t in fight._targets() if t.owner is bot)
        a, b = target.segment()
        centre = tuple((lo + hi) / 2 for lo, hi in zip(a, b, strict=True))
        angles = aim(fight._eye(hog), centre)
        run.step({hog: (*angles, 8)})
        caught = False
        for _ in range(110):
            run.step({hog: (*angles, 0)})
            tethered = fight.hooks.controlled(bot.entity)
            caught |= tethered
            if caught and not tethered:
                break
        self.assertTrue(caught)
        self.assertTrue(restrained())
        self.assertFalse(fight.hooks.controlled(bot.entity))
        self.assertLess(math.dist(bot.position, hog.mover.position), start)
        self.assertEqual(vitals.total, initial - 30)

    def test_sleep_dart_on_hook_owner_releases_all_five_npc_pulls(self):
        for definition in self.definitions.values():
            with self.subTest(npc=definition.name):
                self.check_sleep_dart_releases_npc_pull(definition)

    def check_sleep_dart_releases_npc_pull(self, definition):
        run, hog, bot, vitals, angles = self.session(definition, distance=18, caster=True)
        ana = run.players[1]
        self.assertTrue(run.match.switch_hero(ana, heroes()[0x02E000000000013B]))
        fight = run.match.combat
        x, y, z = hog.mover.position
        ana.mover.position[:] = (x + 3, y, z)
        ana.mover.body = None
        sight = aim(fight._eye(ana), (x, y + 1, z))
        for _ in range(20):
            run.step({ana: (*sight, 0)})
        initial = vitals.total
        latched = False
        for offset in range(65):
            run.step({hog: (*angles, 8 if offset == 0 else 0), ana: (*sight, 8 if offset == 10 else 0)})
            if hog.body in fight.sleep:
                break
            flight = fight.hooks.flights.get(hog.body)
            if flight is not None and flight.arrived_at is None:
                latched |= fight.hooks.controlled(bot.entity)
        self.assertTrue(latched)
        deadline = fight.sleep[hog.body]
        self.assertEqual(deadline - run.frame, math.ceil(5 / combat.TICK))
        self.assertNotIn(hog.body, fight.hooks.flights)
        self.assertFalse(fight.hooks.controlled(bot.entity))
        self.assertEqual(bot.velocity, (0, 0, 0))
        position = tuple(bot.position)
        while run.frame < deadline - 1:
            run.step({hog: (*angles, 8)})
            self.assertNotIn(hog.body, fight.hooks.flights)
        run.step({hog: (*angles, 0)})
        self.assertFalse(fight.is_stunned(hog))
        self.assertNotIn(hog.body, fight.hooks.flights)
        self.assertEqual(tuple(bot.position), position)
        self.assertEqual(bot.velocity, (0, 0, 0))
        self.assertEqual(vitals.total, initial - 30)
        self.assertGreater(stun_tests.RoadhogStunTests().ability(hog, 0x01C8).remaining(), 0)

    def test_emp_on_hook_owner_releases_all_five_npc_pulls(self):
        for definition in self.definitions.values():
            with self.subTest(npc=definition.name):
                self.check_sombra_releases_npc_pull(definition, emp=True)

    def test_hack_on_hook_owner_releases_all_five_npc_pulls(self):
        for definition in self.definitions.values():
            with self.subTest(npc=definition.name):
                self.check_sombra_releases_npc_pull(definition, emp=False)

    def check_sombra_releases_npc_pull(self, definition, *, emp):
        run, hog, bot, vitals, angles = self.session(definition, distance=18, caster=True)
        caster = run.players[1]
        self.assertTrue(run.match.switch_hero(caster, heroes()[sombra.SOMBRA]))
        fight = run.match.combat
        x, y, z = hog.mover.position
        caster.mover.position[:] = (x + 3, y, z)
        caster.mover.body = None
        sight = aim(fight._eye(caster), fight._eye(hog))
        for _ in range(25):
            run.step({caster: (*sight, 0)})
        initial = vitals.total
        script = caster.body_script
        cost = script.component.entity_var(ULT_COST).value()
        if not emp:
            script.set_ult_charge(cost / 2)
        run.step({hog: (*angles, 8), caster: (*sight, 0 if emp else 2)})
        if emp:
            for _ in range(55):
                if fight.hooks.controlled(bot.entity):
                    break
                run.step({hog: (*angles, 0)})
            self.assertTrue(fight.hooks.controlled(bot.entity))
            script.set_ult_charge(cost)
        for offset in range(70):
            flight = fight.hooks.flights.get(hog.body)
            button = (32 if offset == 0 else 0) if emp else 2
            run.step({caster: (*sight, button)})
            if sombra.locked(hog):
                break
        self.assertTrue(sombra.locked(hog))
        self.assertIsNotNone(flight)
        self.assertIsNotNone(flight.target)
        self.assertIsNone(flight.arrived_at)
        charge = script.component.entity_var(ULT_CHARGE).value()
        if emp:
            self.assertEqual(charge, 0)
        else:
            self.assertGreaterEqual(charge, cost / 2)
        self.assertNotIn(hog.body, fight.hooks.flights)
        self.assertFalse(fight.hooks.controlled(bot.entity))
        self.assertEqual(bot.velocity, (0, 0, 0))
        position = tuple(bot.position)
        for _ in range(320):
            if not sombra.locked(hog):
                break
            run.step({hog: (*angles, 8), caster: (*sight, 0 if emp else 2)})
            self.assertNotIn(hog.body, fight.hooks.flights)
        self.assertFalse(sombra.locked(hog))
        self.assertNotIn(hog.body, fight.hooks.flights)
        self.assertEqual(tuple(bot.position), position)
        self.assertEqual(vitals.total, initial - 30)
        self.assertGreater(stun_tests.RoadhogStunTests().ability(hog, 0x01C8).remaining(), 0)

    def test_allied_grasp_converts_four_actual_npc_guns(self):
        for definition in self.definitions.values():
            if definition.name != "Assassin":
                with self.subTest(npc=definition.name):
                    self.check_incoming_grasp(definition)

    def check_incoming_grasp(self, definition):
        run, hog, sigma, rear = phases.RoadhogDefenseTests().session("Sigma", distance=5)
        match = run.match
        fight = match.combat
        match.game_map = replace(match.game_map, free_for_all=False)
        hog.team = sigma.team = 0
        sigma.mover.position[0] = 1
        rear.mover.position[0] = 20
        sigma.mover.body = rear.mover.body = None
        for player in run.players:
            player.client = NpcClient(player)
        bot = npc.spawn(match, definition, (0, hog.mover.position[1], 12), team=1)
        for _ in range(15):
            run.step({})
        self.assertEqual(npc_control.start(match, hog), bot.entity)
        if definition.name == "Sniper":
            sigma.mover.position[0] = 0
            sigma.mover.body = None
            for _ in range(65):
                run.step({})
        own = fight.vitals_of(next(target for target in fight._targets() if target.owner is hog))
        sigma_vitals = fight.vitals[sigma.body]
        hog_before = own.total
        base = dict(sigma_vitals.now)
        run.step({sigma: (0, 0, 8)})
        grasp = next(i for i in sigma.body_script.component.instances.values() if i.graph.index == 0x1D02)
        for _ in range(100):
            run.step({sigma: (0, 0, 0)})
            if grasp.vars[21312].value() > 0:
                break
        self.assertTrue(grasp.states[1].active)
        self.assertGreater(grasp.vars[21312].value(), 0)
        self.assertEqual(own.total, hog_before)
        self.assertEqual(sigma_vitals.now, base)
        npc_control.stop(match)
        for _ in range(140):
            run.step({sigma: (0, 0, 0)})
        self.assertFalse(grasp.states[1].active)
        absorbed = grasp.vars[21312].value()
        shield = sigma_vitals.temporary_shields[defenses.SHIELD_TAG]
        self.assertAlmostEqual(shield.now, min(400, absorbed * 0.6), delta=0.0001)
        self.assertEqual(sigma_vitals.now, base)
        self.assertEqual(own.total, hog_before)

    def test_assassin_contact_pins_roadhog_inside_allied_grasp(self):
        run, hog, sigma, rear = phases.RoadhogDefenseTests().session("Sigma", distance=5)
        match = run.match
        fight = match.combat
        match.game_map = replace(match.game_map, free_for_all=False)
        hog.team = sigma.team = 0
        y = hog.mover.position[1]
        sigma.mover.position[:] = (0, y, -1.5)
        rear.mover.position[0] = 20
        sigma.mover.body = rear.mover.body = None
        for player in run.players:
            player.client = NpcClient(player)
        definition = next(d for d in self.definitions.values() if d.name == "Assassin")
        bot = npc.spawn(match, definition, (0, y, 2), team=1)
        for _ in range(20):
            run.step({})
        run.step({sigma: (0, 0, 8)})
        for _ in range(6):
            run.step({sigma: (0, 0, 0)})
        grasp = next(i for i in sigma.body_script.component.instances.values() if i.graph.index == 0x1D02)
        self.assertTrue(grasp.states[1].active)
        sigma_target = next(target for target in fight._targets() if target.owner is sigma)
        x, eye_y, z = fight._eye(hog)
        gate = defenses.projectile_gate((x, eye_y, z + 2), (0, 0, -1), 2, (sigma_target,))
        self.assertIsNotNone(gate)
        own = fight.vitals_of(next(target for target in fight._targets() if target.owner is hog))
        target = next(target for target in fight._targets() if target.owner is bot)
        low, high = target.segment()
        centre = tuple((a + b) / 2 for a, b in zip(low, high, strict=True))
        hog.mover.yaw, hog.mover.pitch = aim(fight._eye(hog), centre)
        self.assertEqual(npc_control.start(match, hog), bot.entity)
        for _ in range(100):
            run.step({sigma: (0, 0, 0)})
            if fight.pins.get(bot.entity) == hog.body:
                break
        self.assertEqual(fight.pins.get(bot.entity), hog.body)
        self.assertTrue(grasp.states[1].active)
        self.assertEqual(own.total, 575)
        self.assertEqual(grasp.vars[21312].value(), 0)
        npc_control.stop(match)
        for _ in range(150):
            run.step({sigma: (0, 0, 0)})
        self.assertNotIn(bot.entity, fight.pins)
        self.assertFalse(fight.vitals[sigma.body].temporary_shields)
        self.assertEqual(phases.RoadhogDefenseTests().health(run, sigma), 20000)

    def test_reflected_real_primary_hits_all_five_npc_bodies_with_defender_credit(self):
        for definition in self.definitions.values():
            with self.subTest(npc=definition.name):
                _, hog, genji, _, vitals, before = self.reflected_primary(definition)
                self.assertAlmostEqual(before - vitals.total, 165, places=3)
                self.assertEqual(len(genji.client.messages(combat.HIT)), 25)
                self.assertFalse(hog.client.messages(combat.HIT))
                self.assertEqual(hog.body_script.component.entity_var(ULT_CHARGE).value(), 0)

    def reflected_primary(self, definition, *, remaining=None):
        run, hog, bot, vitals, _ = self.session(definition, distance=-3, caster=True)
        genji = run.players[1]
        run.match.switch_hero(genji, heroes()[0x02E0000000000029])
        genji.client = NpcClient(genji)
        x, y, z = hog.mover.position
        genji.mover.position[:] = (x, y, z + 3)
        hog.body_script.ult_seconds = genji.body_script.ult_seconds = 0
        if remaining is not None:
            vitals.damage(vitals.total - remaining)
            run.match.combat._sync_body_health(bot, vitals)
        target = next(t for t in run.match.combat._targets() if t.owner is bot)
        low, high = target.segment()
        point = tuple((a + b) / 2 for a, b in zip(low, high, strict=True))
        angles = aim(run.match.combat._eye(genji), point)
        run.step({genji: (*angles, 16)})
        for _ in range(5):
            run.step({genji: (*angles, 0)})
        shoot = aim(run.match.combat._eye(hog), (x, genji.mover.position[1] + 1, z + 3))
        before = vitals.total
        run.step({hog: (*shoot, 1), genji: (*angles, 0)})
        # Fixture relocation exposes the rear NPC to the redirected shot.
        hog.mover.position[0] += 4
        hog.mover.body = None
        for _ in range(35):
            run.step({genji: (*angles, 0)})
        return run, hog, genji, bot, vitals, before

    def test_reflected_npc_death_credit_and_retained_corpses_for_all_five(self):
        for definition in self.definitions.values():
            with self.subTest(npc=definition.name):
                run, hog, genji, bot, vitals, _ = self.reflected_primary(definition, remaining=10)
                self.assertTrue(bot.dead)
                self.assertEqual(vitals.killer, genji.body)
                self.assertEqual(len(genji.client.messages(combat.HIT)), 2)
                self.assertEqual(len(genji.client.deaths), 1)
                self.assertFalse(hog.client.messages(combat.HIT))
                entity = bot.entity
                for _ in range(240):
                    run.step({})
                self.assertIn(bot, run.match.bots)
                self.assertEqual(bot.entity, entity)
                self.assertTrue(bot.dead)
                self.assertEqual(vitals.total, 0)

    def test_real_primary_secondary_and_melee_damage_all_five_authored_bodies(self):
        self.assertEqual({d.name for d in self.definitions.values()},
                         {"Trooper", "Enforcer", "Sniper", "Assassin", "Heavy Assault"})
        for definition in self.definitions.values():
            for button in (1, 2, 0x800):
                with self.subTest(npc=definition.name, button=button):
                    run, hog, _, vitals, angles = self.session(definition, distance=1.0)
                    hog.body_script.ult_seconds = 0
                    before = vitals.total
                    run.step({hog: (*angles, button)})
                    for _ in range(40):
                        run.step({hog: (*angles, 0)})
                    self.assertLess(vitals.total, before)
                    self.assertGreater(len(hog.client.messages(combat.HIT)), 0)
                    self.assertAlmostEqual(hog.body_script.component.entity_var(ULT_CHARGE).value(),
                                           before - vitals.total, places=3)

    def test_allied_earthshatter_damages_and_controls_all_five_npc_bodies(self):
        for definition in self.definitions.values():
            with self.subTest(npc=definition.name):
                run, hog, bot, vitals, angles = self.session(definition, distance=8, caster=True)
                fight = run.match.combat
                reinhardt = run.players[1]
                reinhardt.client = NpcClient(reinhardt)
                run.match.switch_hero(reinhardt, heroes()[0x02E0000000000007])
                run.match.game_map = replace(run.match.game_map, free_for_all=False)
                hog.team = reinhardt.team = 0
                x, y, z = hog.mover.position
                reinhardt.mover.position[:] = (x+2, y, z)
                reinhardt.mover.body = None
                for _ in range(40):
                    run.step({})
                own = fight.vitals_of(next(t for t in fight._targets() if t.owner is hog))
                firing = definition.name != "Assassin"
                if firing:
                    hog.mover.yaw, hog.mover.pitch = angles
                    self.assertEqual(npc_control.start(run.match, hog), bot.entity)
                    for _ in range(200):
                        run.step({})
                        if own.total < 600:
                            break
                    self.assertLess(own.total, 600)
                    self.assertTrue(bot.attack_input)
                before = vitals.total
                script = reinhardt.body_script
                script.set_ult_charge(script.component.entity_var(ULT_COST).value())
                run.step({reinhardt: (0, 0, 32)})
                for _ in range(85):
                    if fight.is_stunned(bot):
                        break
                    run.step({})
                self.assertTrue(fight.is_stunned(bot))
                deadline = fight.stuns[bot.entity]
                self.assertEqual(deadline-run.frame, 157)
                self.assertLess(vitals.total, before)
                after = vitals.total
                # Let already-launched projectiles resolve before checking
                # that the knocked-down NPC generates no new damage.
                for _ in range(25):
                    run.step({})
                    self.assertFalse(bot.attack_input)
                stopped = own.total
                while run.frame < deadline:
                    run.step({})
                    if run.frame < deadline:
                        self.assertFalse(bot.attack_input)
                        self.assertEqual(own.total, stopped)
                self.assertFalse(fight.is_stunned(bot))
                self.assertEqual(vitals.total, after)
                if firing:
                    for _ in range(240):
                        run.step({})
                        if own.total < stopped:
                            break
                    self.assertLess(own.total, stopped)
                    self.assertTrue(bot.attack_input)
                else:
                    self.assertEqual(own.total, 600)

    def test_charged_whole_hog_damages_each_authored_npc(self):
        for definition in self.definitions.values():
            with self.subTest(npc=definition.name):
                run, hog, _, vitals, angles = self.session(definition)
                before = vitals.total
                component = hog.body_script.component
                component.set(ULT_CHARGE, component.entity_var(ULT_COST).value())
                run.step({hog: (*angles, 32)})
                for _ in range(100):
                    run.step({hog: (*angles, 0)})
                self.assertLess(vitals.total, before)
                self.assertGreater(len(hog.client.messages(combat.HIT)), 0)

    def test_secondary_expiry_fragments_hit_all_five_authored_npcs(self):
        for definition in self.definitions.values():
            with self.subTest(npc=definition.name):
                run, hog, _, vitals, angles = self.session(definition, distance=12)
                before = vitals.total
                run.step({hog: (*angles, 2)})
                for _ in range(50):
                    run.step({hog: (*angles, 0)})
                self.assertLess(vitals.total, before)
                self.assertGreater(len(hog.client.messages(combat.HIT)), 1)

    def test_real_hook_damages_pulls_and_releases_every_authored_npc(self):
        for definition in self.definitions.values():
            with self.subTest(npc=definition.name):
                run, hog, bot, vitals, angles = self.session(definition, distance=10)
                before = vitals.total
                run.step({hog: (*angles, 8)})
                controlled = False
                for _ in range(150):
                    run.step({hog: (*angles, 0)})
                    controlled |= run.match.combat.hooks.controlled(bot.entity)
                self.assertTrue(controlled)
                self.assertEqual(vitals.total, before - 30)
                distance = ((bot.position[0] - hog.mover.position[0]) ** 2
                            + (bot.position[2] - hog.mover.position[2]) ** 2) ** 0.5
                self.assertLess(distance, 10)
                self.assertGreaterEqual(distance, 2.8)
                self.assertFalse(run.match.combat.hooks.controlled(bot.entity))

    def test_hook_npc_pull_scheduler_gap_matches_retained_contiguous_ticks(self):
        definition = next(d for d in self.definitions.values() if d.name == "Trooper")
        results = []
        for skipped in (False, True):
            run, hog, bot, vitals, angles = self.session(definition, distance=10)
            run.step({hog: (*angles, 8)})
            for _ in range(12):
                run.step({hog: (*angles, 0)})
            if skipped:
                frame = run.frame + 40
                run.match.update(frame * combat.TICK, frame)
            else:
                for _ in range(40):
                    run.step({hog: (*angles, 0)})
            results.append((tuple(bot.position), vitals.total, run.match.combat.hooks.controlled(bot.entity)))
        self.assertEqual(results[0], results[1])
        self.assertEqual(results[0][1], definition.health - 30)

    def test_all_six_attacks_pass_allied_npcs_and_hit_an_enemy_player(self):
        cases = (
            ("primary", 1, 2, 4),
            ("slug", 2, 2, 5),
            ("fragments", 2, 10.5, 12),
            ("melee", 0x800, 1, 2),
            ("hook", 8, 4, 8),
            ("whole hog", 32, 3, 5),
        )
        for definition in self.definitions.values():
            for attack, button, ally_distance, enemy_distance in cases:
                with self.subTest(npc=definition.name, attack=attack):
                    run, hog, ally, ally_pool, _ = self.session(definition, distance=ally_distance)
                    match = run.match
                    fight = match.combat
                    match.game_map = replace(match.game_map, free_for_all=False)
                    hog.team = ally.team
                    enemy = match.add_player(9, "Rear enemy", SOLDIER, 0, False)
                    in_world(match, enemy)
                    self.assertTrue(match.switch_hero(enemy, heroes()[SOLDIER]))
                    self.assertNotEqual(enemy.team, ally.team)
                    x, y, z = hog.mover.position
                    enemy.mover.position[:] = (x, y, z + enemy_distance)
                    enemy.mover.body = None
                    target = next(t for t in fight._targets() if t.owner is enemy)
                    enemy_pool = fight.vitals_of(target)
                    ally_health = ally_pool.total
                    enemy_health = enemy_pool.total
                    angles = aim(fight._eye(hog), (x, y + 0.85, z + enemy_distance))
                    if button == 32:
                        hog.body_script.set_ult_charge(hog.body_script.component.entity_var(ULT_COST).value())
                    run.step({hog: (*angles, button)})
                    for _ in range(90):
                        run.step({hog: (*angles, 0)})
                        if enemy_pool.total < enemy_health:
                            break

                    self.assertLess(enemy_pool.total, enemy_health)
                    self.assertEqual(ally_pool.total, ally_health)
                    self.assertFalse(fight.hooks.controlled(ally.entity))
                    self.assertNotIn(ally.entity, fight.impulses.live)
                    if button == 8:
                        self.assertIs(fight.hooks.flights[hog.body].target.owner, enemy)
                    elif button == 32:
                        self.assertGreater(fight.impulses.velocity(enemy.body)[2], 0)

    def test_roadhog_contact_boundaries_on_each_npc(self):
        cases = (
            ("primary", 1, 105),
            ("fragments", 2, 114),
            ("melee", 0x800, 4),
            ("hook", 8, 24),
            ("whole hog", 32, 105),
        )
        for definition in self.definitions.values():
            for attack, button, distance in cases:
                for offset in (-0.01, 0.01):
                    with self.subTest(npc=definition.name, attack=attack, offset=offset):
                        self.assert_npc_contact_boundary(definition, button, distance, offset)

    def assert_npc_contact_boundary(self, definition, button, distance, offset):
        run, hog, bot, pool, look = self.session(definition, distance=distance)
        fight = run.match.combat
        before = pool.total
        origin, direction = fight._eye(hog), combat.aim(*look)
        reach = 1.5 if button == 0x800 else 100
        padding = 1 if button == 0x800 else 0
        if button in (1, 2):
            run.step({hog: (*look, button)})
            for _ in range(12):
                if len(fight.rockets) == 25:
                    break
                run.step({hog: (*look, 0)})
            self.assertEqual(len(fight.rockets), 25)
            projectile = max(fight.rockets, key=lambda shot: combat._dot(shot.direction, direction))
            origin, direction = projectile.position, projectile.direction
            self.assertAlmostEqual(projectile.speed * (projectile.left + projectile.flown), 100)
            reach = projectile.speed * projectile.left
        elif button == 8:
            run.step({hog: (*look, button)})
            for _ in range(40):
                if hog.body in fight.hooks.flights:
                    break
                run.step({hog: (*look, 0)})
            flight = fight.hooks.flights[hog.body]
            self.assertEqual(flight.reach, 20)
            origin, direction, reach, padding = flight.origin, flight.direction, flight.reach, flight.radius

        target = next(t for t in fight._targets() if t.owner is bot)
        a, b = target.segment()
        contact = combat.ray_capsule(origin, direction, a, b, target.radius + padding)
        self.assertIsNotNone(contact)
        adjustment = reach + offset - contact
        bot.position = tuple(p + d * adjustment for p, d in zip(target.feet, direction, strict=True))
        if button in (0x800, 32):
            if button == 32:
                hog.body_script.set_ult_charge(hog.body_script.component.entity_var(ULT_COST).value())
            for _ in range(4):
                run.step({})
            run.step({hog: (*look, button)})
        for _ in range(105):
            run.step({hog: (*look, 0)})
            if pool.total < before:
                break

        self.assertEqual(pool.total < before, offset < 0)
        self.assertEqual(bool(hog.client.messages(combat.HIT)), offset < 0)
        if button == 32:
            self.assertEqual(bot.entity in fight.impulses.live, offset < 0)
        elif button == 8:
            self.assertEqual(fight.hooks.controlled(bot.entity), offset < 0)

    def test_all_lethal_roadhog_inputs_retain_each_manual_npc_corpse(self):
        self.assert_lethal_roadhog_inputs(difficulty_index=None)

    def test_all_lethal_roadhog_inputs_retire_selected_npc_pools(self):
        self.assert_lethal_roadhog_inputs(difficulty_index=3)

    def assert_lethal_roadhog_inputs(self, *, difficulty_index):
        cases = (("primary", 1, 1.0), ("slug", 2, 2.0), ("fragments", 2, 10.0),
                 ("melee", 0x800, 1.0), ("hook", 8, 6.0), ("whole hog", 32, 2.0))
        for definition in self.definitions.values():
            for attack, button, distance in cases:
                with self.subTest(npc=definition.name, attack=attack, difficulty=difficulty_index):
                    run, hog, bot, vitals, angles = self.session(
                        definition, distance=distance, difficulty_index=difficulty_index,
                    )
                    fight = run.match.combat
                    expected = difficulty_index is not None and definition.name != "Trooper"
                    self.assertEqual(bool(vitals.conditional_pools), expected)
                    vitals.damage(vitals.total - 1)
                    if button == 32:
                        hog.body_script.set_ult_charge(hog.body_script.component.entity_var(ULT_COST).value())
                    run.step({hog: (*angles, button)})
                    for _ in range(240):
                        run.step({hog: (*angles, 0)})
                    self.assertTrue(vitals.dead)
                    self.assertTrue(bot.dead)
                    self.assertEqual(vitals.killer, hog.body)
                    self.assertIn(bot, run.match.bots)
                    self.assertEqual(vitals.total, 0)
                    self.assertTrue(all(pool.most == 0 for pool in vitals.conditional_pools.values()))
                    self.assertFalse(fight.hooks.controlled(bot.entity))
                    self.assertFalse(any(who is bot for _, who in fight.respawns))
                    self.assertIn(bot.entity, fight.vitals)
                    self.assertIsNotNone(bot.body_script)
                    self.assertEqual([entity for entity, _ in hog.client.deaths], [bot.entity])

    def manual_npc_scene(self, name, *, distance=1.55):
        definition = next(d for d in self.definitions.values() if d.name == name)
        run, hog, bot, vitals, angles = self.session(definition, distance=distance)
        for _ in range(8):
            run.step({hog: (*angles, 0)})
        server = GameServer()
        server.matches.append(run.match)
        return run, hog, bot, vitals, angles, server

    def test_actual_npc_melee_hits_roadhog_and_respects_breather(self):
        for name, expected in (("Trooper", 30), ("Heavy Assault", 80)):
            for reduction in (False, True):
                with self.subTest(npc=name, breather=reduction):
                    run, hog, bot, _, angles, server = self.manual_npc_scene(name)
                    fight = run.match.combat
                    pool = fight.vitals_of(next(t for t in fight._targets() if t.owner is hog))
                    if reduction:
                        run.step({hog: (*angles, 16)})
                        for _ in range(70):
                            run.step({hog: (*angles, 0)})
                            if fight.incoming_damage_scale(hog) == 0.5:
                                break
                        self.assertEqual(fight.incoming_damage_scale(hog), 0.5)
                    result = server.npc_key(hog.account_lo, "0", {}, attack_action="melee")
                    self.assertEqual(result["entity"], bot.entity)
                    self.assertEqual(npc_control.button(bot), 31)
                    for _ in range(50):
                        before = pool.total
                        run.step({})
                        if pool.total < before:
                            break
                    self.assertEqual(before - pool.total, expected * (0.5 if reduction else 1))
                    if name == "Heavy Assault":
                        self.assertGreater(hog.mover.velocity[1], 0.0)
                    self.assertIsNotNone(bot.body_script)
                    stopped = pool.total
                    server.npc_key(hog.account_lo, "{", {})
                    for _ in range(45):
                        run.step({})
                    self.assertGreaterEqual(pool.total, stopped)

    def test_manual_npc_melee_resolves_all_mapped_player_protection_scopes(self):
        protections = (*phases.CASTS, ("Doomfist", 32, True))
        for definition in self.definitions.values():
            if definition.name not in ("Trooper", "Heavy Assault"):
                continue
            for name, button, ghost in protections:
                with self.subTest(npc=definition.name, hero=name):
                    self.check_manual_npc_melee_protection(definition, name, button, ghost)

    def check_manual_npc_melee_protection(self, definition, name, button, ghost):
        run, hog, bot, _, look = self.session(definition, distance=1.55, caster=True)
        match = run.match
        fight = match.combat
        guard = run.players[1]
        self.assertTrue(match.switch_hero(guard, next(hero for hero in heroes().values() if hero.name == name)))
        guard.client = NpcClient(guard)
        match.game_map = replace(match.game_map, free_for_all=False)
        hog.team = guard.team = 0
        x, y, z = hog.mover.position
        stance = (x + .6, y, z + .25)
        guard.mover.position[:] = stance
        guard.mover.body = None
        for _ in range(40):
            run.step({hog: (*look, 0)})
        own = fight.vitals_of(next(t for t in fight._targets() if t.owner is hog))
        front = fight.vitals_of(next(t for t in fight._targets() if t.owner is guard))
        before = front.total
        if button == 32:
            script = guard.body_script
            script.set_ult_charge(script.component.entity_var(ULT_COST).value())
        run.step({hog: (*look, 0), guard: (0, 0, button)})
        self.assertEqual(defenses.protection(guard), (True, ghost))

        contacts = {}
        apply_damage = fight._damage

        def observe(shooter, target, *args, **kwargs):
            pool = fight.vitals_of(target)
            prior = pool.total
            result = apply_damage(shooter, target, *args, **kwargs)
            if shooter is bot:
                contacts.setdefault(target.owner, []).append(prior - pool.total)
            return result

        def swing():
            contacts.clear()
            # Select the NPC before placing the allied guard in the melee volume.
            guard.mover.position[0] = x + 3
            guard.mover.body = None
            self.assertEqual(npc_control.start(match, hog, action="melee"), bot.entity)
            guard.mover.position[:] = stance
            guard.mover.body = None
            with patch.object(fight, "_damage", side_effect=observe):
                for _ in range(50):
                    run.step({hog: (*look, 0)})
                    if hog in contacts:
                        break
            npc_control.stop(match)

        damage = 30 if definition.name == "Trooper" else 80
        swing()
        self.assertEqual(defenses.protection(guard), (True, ghost))
        self.assertEqual(contacts.get(hog), [damage])
        self.assertEqual(contacts.get(guard, []), [] if ghost else [0])
        self.assertEqual(front.total, before)
        if definition.name == "Heavy Assault":
            self.assertGreater(hog.mover.velocity[1], 0)
            self.assertEqual(guard.mover.velocity[1], 0)

        if name == "Doomfist":
            # Let Meteor Strike land away from the NPC used for the next swing.
            guard.mover.position[0] = x + 20
            guard.mover.body = None
        for _ in range(580):
            run.step({hog: (*look, 0)})
            if not defenses.protection(guard)[0]:
                break
        self.assertEqual(defenses.protection(guard), (False, False))
        self.assertFalse(bot.dead)
        self.assertEqual(front.total, before)

        for player, position in ((hog, (x, y, z)), (guard, stance)):
            player.mover.position[:] = position
            player.mover.velocity = (0, 0, 0)
            player.mover.body = None
        for _ in range(15):
            run.step({hog: (*look, 0)})
        hog_health = own.total
        swing()
        self.assertEqual(contacts.get(hog), [damage])
        self.assertEqual(contacts.get(guard), [damage])
        self.assertEqual(own.total, hog_health - damage)
        self.assertEqual(front.total, before - damage)
        if definition.name == "Heavy Assault":
            self.assertGreater(guard.mover.velocity[1], 0)

    def test_actual_npc_melee_respects_range_team_and_wall(self):
        for name in ("Trooper", "Heavy Assault"):
            for case in ("range", "team", "wall"):
                with self.subTest(npc=name, case=case):
                    run, hog, bot, _, _, server = self.manual_npc_scene(name, distance=8 if case == "range" else 1.55)
                    fight = run.match.combat
                    pool = fight.vitals_of(next(t for t in fight._targets() if t.owner is hog))
                    before = pool.total
                    server.npc_key(hog.account_lo, "0", {}, attack_action="melee")
                    x, y, z = hog.mover.position
                    if case == "team":
                        run.match.game_map = replace(run.match.game_map, free_for_all=False)
                        hog.team = bot.team
                    if case == "wall":
                        wall = (z + bot.position[2]) / 2
                        run.match.collision_world = World([
                            (x - 5, y - 1, wall, x + 5, y - 1, wall, x + 5, y + 5, wall),
                            (x - 5, y - 1, wall, x + 5, y + 5, wall, x - 5, y + 5, wall),
                        ])
                    for _ in range(65):
                        run.step({})
                    self.assertEqual(pool.total, before)
                    self.assertIsNotNone(bot.body_script)

    def test_hook_interrupts_npc_melee_windup_without_incoming_damage(self):
        for name in ("Trooper", "Heavy Assault"):
            with self.subTest(npc=name):
                run, hog, bot, vitals, angles, server = self.manual_npc_scene(name)
                fight = run.match.combat
                pool = fight.vitals_of(next(t for t in fight._targets() if t.owner is hog))
                before, npc_before = pool.total, vitals.total
                run.step({hog: (*angles, 8)})
                for _ in range(5):
                    run.step({hog: (*angles, 0)})
                server.npc_key(hog.account_lo, "0", {}, attack_action="melee")
                caught = False
                for _ in range(55):
                    run.step({})
                    caught |= fight.hooks.controlled(bot.entity)
                    if caught and not fight.hooks.controlled(bot.entity):
                        break
                self.assertTrue(caught)
                self.assertEqual(vitals.total, npc_before - 30)
                self.assertEqual(pool.total, before)
                self.assertIsNotNone(bot.body_script)

    def test_projected_barrier_absorbs_both_actual_npc_melee_attacks(self):
        for name, expected in (("Trooper", 30), ("Heavy Assault", 80)):
            with self.subTest(npc=name):
                definition = next(d for d in self.definitions.values() if d.name == name)
                run, hog, bot, _, angles = self.session(definition, distance=1.55, caster=True)
                caster = run.players[1]
                self.assertTrue(run.match.switch_hero(caster, heroes()[0x02E0000000000068]))
                run.match.game_map = replace(run.match.game_map, free_for_all=False)
                hog.team = caster.team = 0
                fight = run.match.combat
                target = next(t for t in fight._targets() if t.owner is hog)
                pool = fight.vitals_of(target)
                before = pool.total
                x, y, z = hog.mover.position
                caster.mover.position[:] = (x + 4, y, z)
                caster.mover.body = None
                look = aim(fight._eye(caster), fight._eye(hog))
                for _ in range(12):
                    run.step({caster: (*look, 0)})
                run.step({caster: (*look, 16)})
                for _ in range(25):
                    run.step({hog: (*angles, 0), caster: (*look, 0)})
                    bubble = fight.zarya_bubbles.for_target(hog)
                    if bubble is not None:
                        break
                self.assertIsNotNone(bubble)
                server = GameServer()
                server.matches.append(run.match)
                self.assertEqual(server.npc_key(hog.account_lo, "0", {}, attack_action="melee")["entity"],
                                 bot.entity)
                for _ in range(45):
                    run.step({})
                    if bubble.health < 200:
                        break
                self.assertEqual(bubble.health, 200 - expected)
                self.assertEqual(pool.total, before)
                self.assertIsNotNone(bot.body_script)

    def test_lethal_npc_melee_preserves_npc_credit_and_retires_roadhog(self):
        for name in ("Trooper", "Heavy Assault"):
            with self.subTest(npc=name):
                run, hog, bot, _, _, server = self.manual_npc_scene(name)
                fight = run.match.combat
                pool = fight.vitals_of(next(t for t in fight._targets() if t.owner is hog))
                pool.damage(pool.total - 20)
                fight._sync_body_health(hog, pool)
                server.npc_key(hog.account_lo, "0", {}, attack_action="melee")
                for _ in range(55):
                    run.step({})
                    if fight.is_dead(hog):
                        break
                self.assertTrue(fight.is_dead(hog))
                self.assertEqual(pool.killer, bot.entity)
                self.assertEqual(sum(who is hog for _, who in fight.respawns), 1)

    def test_sniper_reposition_moves_pauses_primary_and_obeys_cooldown(self):
        run, hog, bot, _, angles, server = self.manual_npc_scene("Sniper", distance=2)
        fight = run.match.combat
        health = phases.RoadhogDefenseTests().health
        before = health(run, hog)
        server.npc_key(hog.account_lo, "0", {})
        result = server.npc_key(hog.account_lo, "0", {}, attack_action="reposition")
        self.assertEqual(result["entity"], bot.entity)
        state = sniper_reposition.move_state(bot)
        destination, speed = state.move_request()
        self.assertEqual(speed, 20)
        with self.assertRaises(ValueError):
            server.npc_key(hog.account_lo, "0", {}, attack_action="reposition")
        start = tuple(bot.position)
        run.step({hog: (*angles, 0)})
        self.assertFalse(bot.attack_input)
        self.assertNotEqual(tuple(bot.position), start)
        end = run.frame + 24
        run.match.update(end * combat.TICK, end)
        run.frame = end
        self.assertFalse(state.active)
        self.assertEqual(tuple(bot.position), tuple(destination))
        self.assertEqual(bot.velocity, (0, 0, 0))
        self.assertEqual(health(run, hog), before)
        for _ in range(200):
            run.step({hog: (*angles, 0)})
            if health(run, hog) < before:
                break
        self.assertEqual(health(run, hog), before - 100)
        server.npc_key(hog.account_lo, "{", {})
        x, y, z = bot.position
        hog.mover.position[:] = (x, y, z - 2)
        hog.mover.body = None
        run.step({hog: (*angles, 0)})
        with self.assertRaises(ValueError):
            server.npc_key(hog.account_lo, "0", {}, attack_action="reposition")
        end = run.frame + math.ceil(sniper_reposition.COOLDOWN_MS / (1000 * combat.TICK))
        run.match.update(end * combat.TICK, end)
        run.frame = end
        run.step({hog: (*angles, 0)})
        self.assertEqual(server.npc_key(hog.account_lo, "0", {}, attack_action="reposition")["entity"], bot.entity)
        self.assertEqual(state.counter, 2)

    def test_sniper_reposition_distinguishes_pending_and_released_rifle_fire(self):
        first = self.sniper_reposition_fire_trace()
        resume_delays = []
        for mode, request in (("windup", first - 20), ("release frame", first), ("in flight", first + 1)):
            with self.subTest(mode=mode):
                resume_delays.append(self.sniper_reposition_fire_trace(request, released=mode == "in flight"))
        self.assertEqual(resume_delays, [resume_delays[0]] * 3)

    def sniper_reposition_fire_trace(self, request=None, *, released=False):
        run, hog, bot, _, look, server = self.manual_npc_scene("Sniper", distance=2)
        fight = run.match.combat
        own = fight.vitals_of(next(target for target in fight._targets() if target.owner is hog))
        server.npc_key(hog.account_lo, "0", {})
        initial = run.frame
        original = fight._shoot
        shots = []
        moved_at = None
        hit_during_move = False

        def observe(shooter, command, state, number, time, **kwargs):
            if shooter is bot:
                self.assertEqual((state.instance.graph.index, state.node.pos), (0x1122, 6))
                shots.append(run.frame - initial)
            return original(shooter, command, state, number, time, **kwargs)

        with patch.object(fight, "_shoot", side_effect=observe):
            for tick in range(1, 460):
                if tick == request:
                    self.assertEqual(own.total, 600)
                    self.assertEqual(len(shots), int(released))
                    result = server.npc_key(hog.account_lo, "0", {}, attack_action="reposition")
                    self.assertEqual(result["entity"], bot.entity)
                    state = sniper_reposition.move_state(bot)
                    destination, speed = state.move_request()
                    self.assertEqual(speed, 20)
                run.step({hog: (*look, 0)})
                if request is None:
                    if shots:
                        self.assertEqual(own.total, 600)
                        return shots[0]
                    continue
                if tick < request:
                    continue
                if state.active:
                    self.assertFalse(bot.attack_input)
                    self.assertEqual(len(shots), int(released))
                    if own.total < 600:
                        self.assertTrue(released)
                        self.assertEqual(own.total, 500)
                        hit_during_move = True
                elif moved_at is None:
                    moved_at = tick
                    self.assertEqual(bot.position, tuple(destination))
                    self.assertEqual(bot.velocity, (0, 0, 0))
                    self.assertEqual(own.total, 500 if released else 600)
                if own.total == (400 if released else 500) and len(shots) == int(released) + 1:
                    break
        self.assertIsNotNone(moved_at)
        self.assertEqual(hit_during_move, released)
        self.assertEqual(len(shots), int(released) + 1)
        self.assertEqual(own.total, 400 if released else 500)
        self.assertGreater(shots[-1], moved_at)
        return shots[-1] - moved_at

    def test_roadhog_hook_interrupts_live_sniper_reposition(self):
        run, hog, bot, vitals, angles, server = self.manual_npc_scene("Sniper", distance=2)
        fight = run.match.combat
        before = vitals.total
        run.step({hog: (*angles, 8)})
        for _ in range(8):
            run.step({hog: (*angles, 0)})
        self.assertEqual(server.npc_key(hog.account_lo, "0", {}, attack_action="reposition")["entity"], bot.entity)
        state = sniper_reposition.move_state(bot)
        self.assertTrue(state.active)
        start = tuple(bot.position)
        for _ in range(24):
            run.step({hog: (*angles, 0)})
            if fight.hooks.controlled(bot.entity):
                break
        self.assertTrue(fight.hooks.controlled(bot.entity))
        self.assertNotEqual(tuple(bot.position), start)
        self.assertFalse(state.active)
        self.assertNotIn(bot.entity, fight.sniper_repositions)
        self.assertEqual(vitals.total, before - 30)

    def test_sniper_reposition_rejects_allies_range_and_static_occlusion(self):
        for case in ("team", "range", "wall"):
            with self.subTest(case=case):
                run, hog, bot, _, _, server = self.manual_npc_scene("Sniper", distance=6 if case == "range" else 2)
                fight = run.match.combat
                x, y, z = hog.mover.position
                if case == "team":
                    run.match.game_map = replace(run.match.game_map, free_for_all=False)
                    hog.team = bot.team
                elif case == "wall":
                    wall = z + 1
                    run.match.collision_world = World([
                        (x - 5, y - 1, wall, x + 5, y - 1, wall, x + 5, y + 5, wall),
                        (x - 5, y - 1, wall, x + 5, y + 5, wall, x - 5, y + 5, wall),
                    ])
                with self.assertRaises(ValueError):
                    server.npc_key(hog.account_lo, "0", {}, attack_action="reposition")
                self.assertFalse(fight.sniper_repositions)
                self.assertEqual(sniper_reposition.move_state(bot).counter, 0)

    def test_sniper_reposition_stop_removal_and_roadhog_lethal_hit_release_state(self):
        for case in ("stop", "remove", "lethal"):
            with self.subTest(case=case):
                run, hog, bot, vitals, angles, server = self.manual_npc_scene("Sniper", distance=2)
                fight = run.match.combat
                if case == "lethal":
                    vitals.damage(vitals.total - 20)
                    fight._sync_body_health(bot, vitals)
                    run.step({hog: (*angles, 2)})
                server.npc_key(hog.account_lo, "0", {}, attack_action="reposition")
                state = sniper_reposition.move_state(bot)
                start = tuple(bot.position)
                for _ in range(5):
                    run.step({hog: (*angles, 0)})
                self.assertNotEqual(tuple(bot.position), start)
                if case == "stop":
                    server.npc_key(hog.account_lo, "{", {})
                elif case == "remove":
                    self.assertIn(bot.entity, server.npc_key(hog.account_lo, "-", {})["entities"])
                else:
                    for _ in range(20):
                        if bot.dead:
                            break
                        run.step({hog: (*angles, 0)})
                    self.assertTrue(bot.dead)
                    self.assertEqual(vitals.killer, hog.body)
                self.assertFalse(state.active)
                self.assertNotIn(bot.entity, fight.sniper_repositions)
                position = tuple(bot.position)
                for _ in range(20):
                    run.step({})
                self.assertEqual(tuple(bot.position), position)
                self.assertEqual(bot.velocity, (0, 0, 0))

    def heavy_charge_scene(self, *, wall=False, support=None, support_position=(-5, 1, 6), wall_z=10):
        definition = next(d for d in self.definitions.values() if d.name == "Heavy Assault")
        run, hog, bot, vitals, _ = self.session(definition, distance=6, caster=support is not None)
        fight = run.match.combat
        hog.mover.position[:] = (0, 1, 6)
        hog.mover.body = None
        bot.position = (0, 1, 0)
        if wall:
            run.match.collision_world = World([
                (-30, 1, -30, -30, 1, 80, 30, 1, 80),
                (-30, 1, -30, 30, 1, 80, 30, 1, -30),
                (-15, -5, wall_z, 15, 10, wall_z, 15, -5, wall_z),
                (-15, -5, wall_z, -15, 10, wall_z, 15, 10, wall_z),
            ])
        commands = {hog: (-32768, 0, 0)}
        if support is not None:
            ally = run.players[1]
            self.assertTrue(run.match.switch_hero(ally, heroes()[support]))
            run.match.game_map = replace(run.match.game_map, free_for_all=False)
            hog.team = ally.team = 0
            ally.mover.position[:] = support_position
            ally.mover.body = None
            commands[ally] = (*aim(fight._eye(ally), fight._eye(hog)), 0)
        for _ in range(12):
            run.step(commands)
        server = GameServer()
        server.matches.append(run.match)
        result = server.npc_key(hog.account_lo, "0", {}, attack_action="charge")
        self.assertEqual(result["entity"], bot.entity)
        self.assertEqual(npc_control.button(bot), 35)
        return run, hog, bot, vitals, server

    def test_projected_barrier_blocks_heavy_charge_pin_or_absorbs_wall_impact(self):
        for already_pinned in (False, True):
            with self.subTest(already_pinned=already_pinned):
                self.check_projected_barrier_heavy_charge(already_pinned)

    def check_projected_barrier_heavy_charge(self, already_pinned):
        run, hog, bot, _, _ = self.heavy_charge_scene(wall=True, support=0x02E0000000000068)
        fight = run.match.combat
        zarya = run.players[1]
        health = phases.RoadhogDefenseTests().health
        if already_pinned:
            for _ in range(100):
                run.step({})
                if reinhardt_charge.pinned(fight, hog.body):
                    break
            self.assertTrue(reinhardt_charge.pinned(fight, hog.body))
        self.assertEqual(health(run, hog), 600)
        look = aim(fight._eye(zarya), fight._eye(hog))
        run.step({zarya: (*look, 0)})
        run.step({zarya: (*look, 16)})
        bubble = fight.zarya_bubbles.for_target(hog)
        self.assertIsNotNone(bubble)
        self.assertEqual(bubble.health, 200)
        self.assertEqual(reinhardt_charge.pinned(fight, hog.body), already_pinned)
        z = hog.mover.position[2]
        energy = zarya.body_script.component.entity_var(87)
        before_energy = energy.value() or 0
        for _ in range(100):
            run.step({})
            self.assertEqual(health(run, hog), 600)
            if not already_pinned:
                self.assertFalse(reinhardt_charge.pinned(fight, hog.body))
                self.assertEqual(hog.mover.position[2], z)
            if bubble.health < 200 and bot.entity not in fight.heavy_charges:
                break
        self.assertFalse(reinhardt_charge.pinned(fight, hog.body))
        self.assertNotIn(bot.entity, fight.heavy_charges)
        self.assertEqual(bot.velocity, (0, 0, 0))
        self.assertEqual(bubble.health, 0 if already_pinned else 150)
        self.assertIs(fight.zarya_bubbles.for_target(hog), None if already_pinned else bubble)
        self.assertAlmostEqual(energy.value() - before_energy, 40 if already_pinned else 10, places=3)
        if already_pinned:
            self.assertGreater(hog.mover.position[2], z)
        released = tuple(hog.mover.position)
        for _ in range(20):
            run.step({})
            self.assertFalse(reinhardt_charge.pinned(fight, hog.body))
            self.assertEqual(health(run, hog), 600)
            self.assertEqual(tuple(hog.mover.position), released)

    def test_allied_sleep_and_flashbang_release_heavy_charge_before_wall_impact(self):
        for support, button in ((0x02E000000000013B, 8), (stun_tests.MCCREE, 16)):
            with self.subTest(support=heroes()[support].name):
                self.check_ally_interrupts_heavy_charge(support, button)

    def check_ally_interrupts_heavy_charge(self, support, button):
        run, hog, bot, vitals, _ = self.heavy_charge_scene(
            wall=True, support=support, support_position=(0, 1, 13 if button == 8 else 8), wall_z=16,
        )
        fight = run.match.combat
        ally = run.players[1]
        for _ in range(90):
            run.step({})
            if reinhardt_charge.pinned(fight, hog.body):
                break
        self.assertTrue(reinhardt_charge.pinned(fight, hog.body))
        instance = next(i for i in bot.body_script.component.instances.values() if i.graph.index == 0x1408)
        drive = instance.states[3]
        activation = drive.counter
        before = vitals.total
        for elapsed in range(45):
            x, y, z = bot.position
            look = aim(fight._eye(ally), (x, y + 1.2, z))
            run.step({ally: (*look, button if elapsed == 0 else 0)})
            if fight.is_stunned(bot):
                break
            self.assertTrue(reinhardt_charge.pinned(fight, hog.body))
        self.assertTrue(fight.is_stunned(bot))
        deadline = (fight.sleep if button == 8 else fight.stuns)[bot.entity]
        self.assertEqual(deadline - run.frame, 313 if button == 8 else 50)
        self.assertEqual(vitals.total, before - (5 if button == 8 else 25))
        self.assertFalse(reinhardt_charge.pinned(fight, hog.body))
        self.assertNotIn(bot.entity, fight.heavy_charges)
        self.assertFalse(drive.active)
        self.assertEqual(bot.velocity, (0, 0, 0))
        self.assertLess(hog.mover.position[2], 15)
        released = tuple(hog.mover.position)
        health = phases.RoadhogDefenseTests().health
        run.step({hog: (-32768, 0, 16)})
        self.assertEqual(fight.incoming_damage_scale(hog), 0.5)
        while run.frame < deadline - 1:
            run.step({})
            self.assertFalse(fight.is_stunned(hog))
            self.assertFalse(bot.attack_input)
            self.assertFalse(reinhardt_charge.pinned(fight, hog.body))
            self.assertEqual(drive.counter, activation)
            self.assertEqual(health(run, hog), 600)
            self.assertEqual(tuple(hog.mover.position), released)

    def test_heavy_charge_pins_roadhog_and_slams_for_300(self):
        run, hog, bot, _, _ = self.heavy_charge_scene(wall=True)
        fight = run.match.combat
        health = phases.RoadhogDefenseTests().health
        before = health(run, hog)
        script = hog.body_script
        ultimate, = script.ultimates
        script.set_ult_charge(script.component.entity_var(ULT_COST).value())
        run.step({hog: (16384, 0, 32)})
        self.assertTrue(ultimate.flags & 1)
        for _ in range(100):
            run.step({hog: (16384, 0, 0)})
            if reinhardt_charge.pinned(fight, hog.body):
                break
        self.assertTrue(reinhardt_charge.pinned(fight, hog.body))
        self.assertFalse(ultimate.flags & 1)
        self.assertEqual(health(run, hog), before)
        pinned_z = hog.mover.position[2]
        for _ in range(60):
            run.step({})
            if not reinhardt_charge.pinned(fight, hog.body):
                break
        self.assertGreater(hog.mover.position[2], pinned_z)
        self.assertFalse(reinhardt_charge.pinned(fight, hog.body))
        self.assertEqual(health(run, hog), before - 300)
        self.assertNotIn(bot.entity, fight.heavy_charges)

    def test_heavy_charge_discards_buffered_melee_on_both_release_paths(self):
        for wall in (True, False):
            with self.subTest(wall=wall):
                self.check_heavy_charge_discards_melee(wall)

    def check_heavy_charge_discards_melee(self, wall):
        run, hog, bot, _, server = self.heavy_charge_scene(wall=wall, support=stun_tests.MCCREE)
        match = run.match
        fight = match.combat
        victim = run.players[1]
        victim.team = bot.team
        health = phases.RoadhogDefenseTests().health
        before = health(run, victim)
        x, y, z = hog.mover.position
        victim.mover.position[:] = (x + 1.5, y, z)
        victim.mover.body = None
        run.step({})
        look = (16384, 0)
        held = {hog: (*look, 0x800)}
        run.step(held)
        first = run.frame
        run.step({})
        run.step(held)
        melee = stun_tests.RoadhogStunTests().ability(hog, 0x0043)
        weapon = next(i for i in hog.body_script.component.instances.values() if i.graph.index == 0x01C6)
        ammo = weapon.vars[53].value()
        hit = melee.instance.state(4)
        self.assertEqual(health(run, victim), before - 30)
        self.assertEqual(hit.counter, 1)
        self.assertTrue(melee.presses)
        while run.frame < first + 61 and not reinhardt_charge.pinned(fight, hog.body):
            run.step(held)
        self.assertTrue(reinhardt_charge.pinned(fight, hog.body))
        self.assertEqual(fight.heavy_charges[bot.entity].pinned, hog.body)
        self.assertFalse(melee.presses)
        self.assertEqual(health(run, hog), 600)
        for _ in range(4):
            run.step(held)
            self.assertTrue(reinhardt_charge.pinned(fight, hog.body))
        self.assertEqual(hit.counter, 1)
        run.step({})
        if wall:
            while run.frame < first + 125 and reinhardt_charge.pinned(fight, hog.body):
                run.step({})
            self.assertEqual(health(run, hog), 300)
        else:
            self.assertEqual(server.npc_key(hog.account_lo, "{", {})["count"], 1)
            self.assertEqual(health(run, hog), 600)
        self.assertFalse(reinhardt_charge.pinned(fight, hog.body))
        self.assertNotIn(bot.entity, fight.heavy_charges)

        x, y, z = hog.mover.position
        victim.mover.position[:] = (x + 1.5, y, z)
        victim.mover.body = None
        while run.frame <= first + 125:
            run.step({})
        self.assertEqual(hit.counter, 1)
        self.assertEqual(health(run, victim), before - 30)
        run.step(held)
        self.assertEqual(hit.counter, 2)
        self.assertEqual(health(run, victim), before - 60)
        self.assertEqual(weapon.vars[53].value(), ammo)

    def test_stopping_heavy_charge_releases_without_impact(self):
        run, hog, bot, _, server = self.heavy_charge_scene()
        fight = run.match.combat
        health = phases.RoadhogDefenseTests().health
        before = health(run, hog)
        for _ in range(100):
            run.step({})
            if reinhardt_charge.pinned(fight, hog.body):
                break
        self.assertTrue(reinhardt_charge.pinned(fight, hog.body))
        self.assertEqual(server.npc_key(hog.account_lo, "{", {})["count"], 1)
        self.assertFalse(reinhardt_charge.pinned(fight, hog.body))
        self.assertNotIn(bot.entity, fight.heavy_charges)
        self.assertEqual(bot.velocity, (0, 0, 0))
        for _ in range(40):
            run.step({})
        self.assertEqual(health(run, hog), before)
        self.assertFalse(fight.is_stunned(hog))

    def test_roadhog_hook_interrupts_heavy_charge_windup_and_drive(self):
        for cast, expected_drive in ((3, False), (39, True)):
            with self.subTest(cast=cast):
                run, hog, bot, vitals, _ = self.heavy_charge_scene()
                fight = run.match.combat
                before = vitals.total
                live = caught = False
                for offset in range(115):
                    run.step({hog: (-32768, 0, 8 if offset == cast else 0)})
                    live |= bot.entity in fight.heavy_charges
                    caught |= fight.hooks.controlled(bot.entity)
                    self.assertFalse(reinhardt_charge.pinned(fight, hog.body))
                self.assertTrue(caught)
                self.assertEqual(live, expected_drive)
                self.assertFalse(fight.heavy_charges)
                self.assertEqual(vitals.total, before - 30)

    def test_heavy_charge_interrupts_breather_before_full_wall_damage(self):
        run, hog, bot, _, _ = self.heavy_charge_scene(wall=True)
        fight = run.match.combat
        target = next(t for t in fight._targets() if t.owner is hog)
        pool = fight.vitals_of(target)
        pool.damage(200)
        fight._sync_body_health(hog, pool)
        for offset in range(100):
            reduced = fight.incoming_damage_scale(hog) == 0.5
            run.step({hog: (16384, 0, 16 if offset == 14 else 0)})
            if reinhardt_charge.pinned(fight, hog.body):
                break
        self.assertTrue(reinhardt_charge.pinned(fight, hog.body))
        self.assertTrue(reduced)
        self.assertEqual(fight.incoming_damage_scale(hog), 1)
        before_impact = pool.total
        for _ in range(60):
            run.step({hog: (16384, 0, 16)})
            if not reinhardt_charge.pinned(fight, hog.body):
                break
        self.assertEqual(pool.total, before_impact - 300)
        self.assertGreater(stun_tests.RoadhogStunTests().ability(hog, 0x0240).remaining(), 0)
        self.assertNotIn(bot.entity, fight.heavy_charges)

    def test_heavy_charge_lethal_impact_credits_npc_and_releases_roadhog(self):
        run, hog, bot, _, _ = self.heavy_charge_scene(wall=True)
        fight = run.match.combat
        target = next(t for t in fight._targets() if t.owner is hog)
        pool = fight.vitals_of(target)
        pool.damage(pool.total - 250)
        fight._sync_body_health(hog, pool)
        captured = False
        for _ in range(130):
            run.step({})
            captured |= reinhardt_charge.pinned(fight, hog.body)
            if fight.is_dead(hog):
                break
        self.assertTrue(captured)
        self.assertTrue(fight.is_dead(hog))
        self.assertEqual(pool.killer, bot.entity)
        self.assertNotIn(bot.entity, fight.heavy_charges)
        self.assertFalse(reinhardt_charge.pinned(fight, hog.body))
        self.assertEqual(sum(who is hog for _, who in fight.respawns), 1)

    def test_roadhog_secondary_kill_during_heavy_charge_windup_prevents_pin(self):
        run, hog, bot, vitals, _ = self.heavy_charge_scene()
        fight = run.match.combat
        vitals.damage(vitals.total - 20)
        fight._sync_body_health(bot, vitals)
        angles = aim(fight._eye(hog), (bot.position[0], bot.position[1] + 1, bot.position[2]))
        before = phases.RoadhogDefenseTests().health(run, hog)
        for offset in range(115):
            run.step({hog: (*angles, 2 if offset == 6 else 0)})
            self.assertFalse(reinhardt_charge.pinned(fight, hog.body))
        self.assertTrue(bot.dead)
        self.assertEqual(vitals.killer, hog.body)
        self.assertNotIn(bot.entity, fight.heavy_charges)
        self.assertEqual(phases.RoadhogDefenseTests().health(run, hog), before)

    def test_heavy_charge_natural_expiry_releases_roadhog_without_impact(self):
        run, hog, bot, _, _ = self.heavy_charge_scene()
        fight = run.match.combat
        health = phases.RoadhogDefenseTests().health
        before = health(run, hog)
        captured = False
        for _ in range(270):
            run.step({})
            captured |= reinhardt_charge.pinned(fight, hog.body)
            if captured and bot.entity not in fight.heavy_charges:
                break
        self.assertTrue(captured)
        self.assertFalse(fight.is_stunned(hog))
        self.assertEqual(health(run, hog), before)
        self.assertEqual(bot.velocity, (0, 0, 0))
        stopped = tuple(bot.position)
        for _ in range(30):
            run.step({})
        self.assertEqual(tuple(bot.position), stopped)
        self.assertFalse(fight.heavy_charges)

    def test_heavy_authored_volley_can_damage_roadhog_through_npc_source_path(self):
        definition = next(d for d in self.definitions.values() if d.name == "Heavy Assault")
        run, hog, bot, _, _ = self.session(definition, distance=2.0)
        bot.yaw = -32768.0
        fight = run.match.combat
        own = fight.vitals_of(next(t for t in fight._targets() if t.owner is hog))
        state = next(i.state(n.state) for i in bot.body_script.component.instances.values()
                     for n in i.graph.nodes if n and n.cls == "STUStatescriptStateWeaponVolley")
        # Explicit weapon admission fixture, NOT authored AI target acquisition or
        # fire-control acceptance. Damage and source/team/flight are ordinary Combat.
        run.step({})  # Establish the authoritative world tick after both body creates.
        fight._shoot(bot, None, state, 0, run.frame * 16, activation=(0, 0))
        for _ in range(30):
            run.step({})
        self.assertAlmostEqual(own.total, 595.0)

    def test_breather_halves_actual_heavy_volley_damage(self):
        definition = next(d for d in self.definitions.values() if d.name == "Heavy Assault")
        run, hog, bot, _, _ = self.session(definition, distance=2.0)
        bot.yaw = -32768.0
        fight = run.match.combat
        own = fight.vitals_of(next(t for t in fight._targets() if t.owner is hog))
        state = next(i.state(n.state) for i in bot.body_script.component.instances.values()
                     for n in i.graph.nodes if n and n.cls == "STUStatescriptStateWeaponVolley")
        run.step({hog: (0, 0, 16)})
        fight._shoot(bot, None, state, 0, run.frame * 16, activation=(0, 0))
        self.assertAlmostEqual(own.total, 597.5)

    def test_five_actual_npc_attacks_kill_roadhog_and_allow_natural_respawn(self):
        self.check_npc_lethal_attacks_and_respawn()

    def test_five_actual_npc_attacks_terminate_whole_hog_before_respawn(self):
        self.check_npc_lethal_attacks_and_respawn(whole_hog=True)

    def check_npc_lethal_attacks_and_respawn(self, *, whole_hog=False):
        for definition in self.definitions.values():
            with self.subTest(npc=definition.name):
                distance = 2 if definition.name == "Assassin" else 10
                run, hog, bot, bot_pool, angles = self.session(definition, distance=distance)
                match = run.match
                fight = match.combat
                body = hog.body
                script = hog.body_script
                position = tuple(hog.mover.position)
                own = fight.vitals_of(next(t for t in fight._targets() if t.owner is hog))
                own.damage(own.total - 1)
                fight._sync_body_health(hog, own)
                if whole_hog:
                    ultimate, = script.ultimates
                    charge = script.component.entity_var(ULT_CHARGE)
                    script.ult_seconds = 0
                    script.set_ult_charge(script.component.entity_var(ULT_COST).value())
                    away = (angles[0] % 65536 - 32768, 0)
                    run.step({hog: (*away, 32)})
                    for _ in range(50):
                        run.step({hog: (*away, 0)})
                    self.assertTrue(ultimate.flags & nodes.Ability.RUNNING)
                    self.assertEqual(charge.value(), 0)
                    bot_before = bot_pool.total
                hog.mover.yaw, hog.mover.pitch = angles
                self.assertEqual(npc_control.start(match, hog), bot.entity)
                for _ in range(250):
                    if whole_hog and definition.name != "Assassin":
                        self.assertTrue(ultimate.flags & nodes.Ability.RUNNING)
                    run.step({hog: (*away, 0)} if whole_hog else {})
                    if own.dead:
                        break
                self.assertTrue(own.dead)
                self.assertEqual(own.killer, bot.entity)
                self.assertEqual(own.total, 0)
                self.assertFalse(fight.pins)
                self.assertFalse(fight.hooks.controlled(body))
                if whole_hog:
                    self.assertFalse(ultimate.flags & nodes.Ability.RUNNING)
                    self.assertEqual(charge.value(), 0)
                    self.assertEqual(bot_pool.total, bot_before)
                    self.assertFalse(hog.client.messages(combat.HIT))
                deadline, = (when for when, actor in fight.respawns if actor is hog)
                self.assertAlmostEqual(deadline - fight.now, combat.respawn_seconds(match))
                with patch.object(fight, "_shoot", wraps=fight._shoot) as shoot:
                    while (run.frame + 1) * combat.TICK < deadline:
                        run.step({})
                        self.assertEqual(hog.body, body)
                        self.assertFalse(bot.attack_input)
                        self.assertFalse(fight.pins)
                    shoot.assert_not_called()
                self.assertTrue(bot.firing)
                self.assertEqual(bot.attack_target, hog.entity)
                run.step({})
                self.assertNotEqual(hog.body, body)
                self.assertTrue(script.retired)
                self.assertFalse(fight.is_dead(hog))
                current = fight.vitals_of(next(t for t in fight._targets() if t.owner is hog))
                self.assertEqual(current.total, 600)
                self.assertNotIn(body, fight.vitals)
                weapon = next(i for i in hog.body_script.component.instances.values()
                              if i.graph.index == 0x01C6)
                self.assertEqual(weapon.vars[53].value(), 5)
                if whole_hog:
                    fresh, = hog.body_script.ultimates
                    self.assertFalse(fresh.flags & nodes.Ability.RUNNING)
                    self.assertEqual(hog.body_script.component.entity_var(ULT_CHARGE).value(), 0)
                # Restore the same sight line, including the short-range Assassin case.
                hog.mover.position[:] = position
                hog.mover.body = None
                with patch.object(fight, "_shoot", wraps=fight._shoot) as shoot:
                    for _ in range(250):
                        run.step({})
                        if current.total < 600:
                            break
                self.assertTrue(bot.attack_input)
                self.assertLess(current.total, 600)
                self.assertGreater(current.total, 0)
                self.assertEqual(own.killer, bot.entity)
                self.assertNotIn(body, fight.vitals)
                if definition.name == "Assassin":
                    self.assertEqual(current.total, 575)
                    self.assertEqual(fight.pins, {bot.entity: hog.body})
                else:
                    self.assertTrue(any(call.args[0] is bot for call in shoot.call_args_list))

    def first_manual_npc_impact(self, definition, distance):
        run, player, attacker, _, look = self.session(definition, distance=distance)
        fight = run.match.combat
        pool = fight.vitals_of(next(t for t in fight._targets() if t.owner is player))
        order_frame = run.frame + 50
        while run.frame < order_frame:
            run.step({})
        player.mover.yaw, player.mover.pitch = look
        self.assertEqual(npc_control.start(run.match, player), attacker.entity)
        for _ in range(250):
            run.step({})
            if pool.total < 600:
                return order_frame, run.frame
        self.fail(f"No manual attack contact for {definition.name}")

    def test_five_actual_npc_attacks_cancel_active_breather_on_death(self):
        for definition in self.definitions.values():
            with self.subTest(npc=definition.name):
                distance = 2 if definition.name == "Assassin" else 10
                order_frame, impact_frame = self.first_manual_npc_impact(definition, distance)
                run, hog, bot, _, look = self.session(definition, distance=distance)
                self.assertEqual(run.frame + 50, order_frame)
                fight = run.match.combat
                old_body = hog.body
                script = hog.body_script
                breather = next(i for i in script.component.instances.values() if i.graph.index == 0x0240)
                heal = breather.state(breather.graph.node(18).state)
                pool = fight.vitals_of(next(t for t in fight._targets() if t.owner is hog))
                pool.damage(400)
                fight._sync_body_health(hog, pool)
                for frame in range(run.frame + 1, impact_frame + 1):
                    if run.frame == order_frame:
                        hog.mover.yaw, hog.mover.pitch = look
                        self.assertEqual(npc_control.start(run.match, hog), bot.entity)
                    if frame == impact_frame:
                        self.assertTrue(heal.active)
                        self.assertGreater(hog.stats.value(HEALING), 0)
                        self.assertEqual(fight.incoming_damage_scale(hog), .5)
                        pool.damage(pool.total - 1)
                        fight._sync_body_health(hog, pool)
                    run.step({hog: (*look, 16)} if frame == impact_frame - 50 else {})
                self.assertTrue(pool.dead)
                self.assertEqual(pool.killer, bot.entity)
                self.assertFalse(heal.active)
                self.assertNotIn(old_body, fight.breather_deadlines)
                stopped = hog.stats.value(HEALING)
                npc_control.stop(run.match)
                deadline, = (when for when, actor in fight.respawns if actor is hog)
                while (run.frame + 1) * combat.TICK < deadline:
                    run.step({})
                    self.assertEqual(pool.total, 0)
                    self.assertEqual(hog.stats.value(HEALING), stopped)
                run.step({})
                self.assertNotEqual(hog.body, old_body)
                self.assertTrue(script.retired)
                fresh = fight.vitals_of(next(t for t in fight._targets() if t.owner is hog))
                self.assertEqual(fresh.total, 600)
                self.assertEqual(fight.incoming_damage_scale(hog), 1)
                fresh.damage(50)
                fight._sync_body_health(hog, fresh)
                for _ in range(65):
                    run.step({})
                self.assertEqual(fresh.total, 550)
                self.assertEqual(hog.stats.value(HEALING), stopped)
                run.step({hog: (*look, 16)})
                for _ in range(120):
                    run.step({})
                self.assertEqual(fresh.total, 600)
                self.assertAlmostEqual(hog.stats.value(HEALING), stopped + 50)

    def test_five_actual_npc_attacks_kill_hook_owner_and_release_other_npc(self):
        for definition in self.definitions.values():
            with self.subTest(npc=definition.name):
                distance = 2 if definition.name == "Assassin" else 10
                order_frame, impact_frame = self.first_manual_npc_impact(definition, distance)
                run, hog, attacker, attacker_pool, attack_look = self.session(definition, distance=distance)
                fight = run.match.combat
                old_body = hog.body
                start = tuple(hog.mover.position)
                x, y, z = start
                caught = npc.spawn(run.match, definition, (x, y, z - 12), team=1)
                target = next(t for t in fight._targets() if t.owner is caught)
                victim = fight.vitals_of(target)
                before = victim.total
                attacker_health = attacker_pool.total
                low, high = target.segment()
                centre = tuple((a + b) / 2 for a, b in zip(low, high, strict=True))
                hook_look = aim(fight._eye(hog), centre)
                owner = fight.vitals_of(next(t for t in fight._targets() if t.owner is hog))

                for frame in range(run.frame + 1, impact_frame + 1):
                    if run.frame == order_frame:
                        hog.mover.yaw, hog.mover.pitch = attack_look
                        self.assertEqual(npc_control.start(run.match, hog), attacker.entity)
                    if frame == impact_frame:
                        self.assertTrue(fight.hooks.controlled(caught.entity))
                        self.assertIs(fight.hooks.flights[old_body].target.owner, caught)
                        self.assertEqual(victim.total, before - 30)
                        self.assertGreater(caught.position[2], z - 12)
                        owner.damage(owner.total - 1)
                        fight._sync_body_health(hog, owner)
                    run.step({hog: (*hook_look, 8 if frame == impact_frame - 40 else 0)})
                self.assertTrue(owner.dead)
                self.assertEqual(owner.killer, attacker.entity)
                run.step({})
                self.assertNotIn(old_body, fight.hooks.flights)
                self.assertFalse(fight.hooks.controlled(caught.entity))
                self.assertEqual(attacker_pool.total, attacker_health)
                self.assertEqual(victim.total, before - 30)

                npc_control.stop(run.match)
                deadline, = (when for when, player in fight.respawns if player is hog)
                while run.frame * combat.TICK < deadline:
                    run.step({})
                self.assertNotEqual(hog.body, old_body)
                self.assertFalse(fight.hooks.controlled(caught.entity))
                self.assertEqual(victim.total, before - 30)
                hog.mover.position[:] = start
                hog.mover.body = None
                for _ in range(10):
                    run.step({})
                run.step({hog: (*hook_look, 8)})
                for _ in range(60):
                    run.step({hog: (*hook_look, 0)})
                    if fight.hooks.controlled(caught.entity):
                        break
                self.assertTrue(fight.hooks.controlled(caught.entity))
                self.assertIs(fight.hooks.flights[hog.body].target.owner, caught)
                self.assertEqual(victim.total, before - 60)

    def test_all_five_manual_attacks_recheck_team_while_order_remains_active(self):
        for definition in self.definitions.values():
            with self.subTest(npc=definition.name):
                run, hog, bot, _, angles = self.session(definition, distance=2)
                match = run.match
                fight = match.combat
                match.game_map = replace(match.game_map, free_for_all=False)
                hog.mover.yaw, hog.mover.pitch = angles
                pool = fight.vitals_of(next(t for t in fight._targets() if t.owner is hog))
                enemy_team = bot.team
                self.assertNotEqual(hog.team, enemy_team)
                bot.team = hog.team
                with self.assertRaises(ValueError):
                    npc_control.start(match, hog)
                self.assertFalse(bot.firing)

                bot.team = enemy_team
                self.assertEqual(npc_control.start(match, hog), bot.entity)
                for _ in range(240):
                    run.step({})
                    if pool.total < 600:
                        break
                before_switch = pool.total
                self.assertLess(before_switch, 600)
                self.assertTrue(bot.attack_input)

                bot.team = hog.team
                with patch.object(fight, "_shoot", wraps=fight._shoot) as shoot:
                    for _ in range(100):
                        run.step({})
                        self.assertFalse(bot.attack_input)
                        self.assertFalse(fight.pins)
                    shoot.assert_not_called()
                self.assertEqual(pool.total, before_switch)
                self.assertEqual(bot.attack_target, hog.entity)
                self.assertTrue(bot.firing)

                bot.team = enemy_team
                for _ in range(240):
                    run.step({})
                    if pool.total < before_switch:
                        break
                self.assertTrue(bot.attack_input)
                self.assertLess(pool.total, before_switch)

    @staticmethod
    def cover_world(hog, distance=1):
        from test_game_mover import box

        x, y, z = hog.mover.position
        ground = box(x - 20, x + 20, y - 1, y, z - 20, z + 20)
        wall = box(x - 8, x + 8, y, y + 8, z + distance - 0.05, z + distance + 0.05)
        return World([*ground, *wall])

    def test_static_cover_blocks_all_six_roadhog_attacks_against_each_npc(self):
        cases = (
            ("primary", 1, 4, 2),
            ("slug", 2, 5, 2),
            ("fragments", 2, 12, 10.5),
            ("melee", 0x800, 2, 1),
            ("hook", 8, 8, 4),
            ("whole hog", 32, 5, 3),
        )
        for definition in self.definitions.values():
            for attack, button, target_distance, wall_distance in cases:
                with self.subTest(npc=definition.name, attack=attack):
                    run, hog, bot, pool, angles = self.session(definition, distance=target_distance)
                    fight = run.match.combat
                    run.match.collision_world = self.cover_world(hog, wall_distance)
                    hog.body_script.ult_seconds = 0
                    hog.body_script.set_ult_charge(0)
                    component = hog.body_script.component
                    charge = component.entity_var(ULT_CHARGE)
                    weapon = next(i for i in component.instances.values() if i.graph.index == 0x01C6)
                    ammo = weapon.vars[53].value()
                    if button == 32:
                        hog.body_script.set_ult_charge(component.entity_var(ULT_COST).value())
                    before = pool.total
                    run.step({hog: (*angles, button)})
                    burst = False
                    for _ in range(90):
                        run.step({hog: (*angles, 0)})
                        burst |= len(fight.rockets) > 1
                        self.assertFalse(fight.hooks.controlled(bot.entity))
                        self.assertNotIn(bot.entity, fight.impulses.live)

                    self.assertEqual(pool.total, before)
                    self.assertFalse(hog.client.messages(combat.HIT))
                    self.assertEqual(charge.value(), 0)
                    self.assertFalse(fight.rockets)
                    self.assertFalse(fight.hooks.flights)
                    if button in (1, 2):
                        self.assertEqual(weapon.vars[53].value(), ammo - 1)
                        if attack == "fragments":
                            self.assertTrue(burst)
                    elif button == 8:
                        hook = next(i for i in component.instances.values() if i.graph.index == 0x01C8)
                        self.assertGreater(hook.state(hook.graph.node(11).state).remaining(), 0)
                    elif button == 0x800:
                        self.assertTrue(any(fight.melee_seen[hog.body].values()))
                    else:
                        ultimate, = hog.body_script.ultimates
                        self.assertTrue(ultimate.flags & 1)

    def test_all_five_manual_attacks_pause_and_resume_at_static_cover(self):
        for definition in self.definitions.values():
            with self.subTest(npc=definition.name):
                run, hog, bot, _, angles = self.session(definition, distance=2)
                match = run.match
                fight = match.combat
                clear = match.collision_world
                cover = self.cover_world(hog)
                pool = fight.vitals_of(next(t for t in fight._targets() if t.owner is hog))
                hog.mover.yaw, hog.mover.pitch = angles
                match.collision_world = cover
                with self.assertRaises(ValueError):
                    npc_control.start(match, hog)
                self.assertFalse(bot.firing)

                match.collision_world = clear
                self.assertEqual(npc_control.start(match, hog), bot.entity)
                for _ in range(240):
                    run.step({})
                    if pool.total < 600:
                        break
                before_cover = pool.total
                self.assertLess(before_cover, 600)
                self.assertTrue(bot.attack_input)

                match.collision_world = cover
                with patch.object(fight, "_shoot", wraps=fight._shoot) as shoot:
                    for _ in range(100):
                        run.step({})
                        self.assertFalse(bot.attack_input)
                        self.assertFalse(fight.pins)
                    shoot.assert_not_called()
                self.assertEqual(pool.total, before_cover)
                self.assertEqual(bot.attack_target, hog.entity)
                self.assertTrue(bot.firing)

                match.collision_world = clear
                for _ in range(240):
                    run.step({})
                    if pool.total < before_cover:
                        break
                self.assertTrue(bot.attack_input)
                self.assertLess(pool.total, before_cover)

    def test_new_cover_blocks_already_released_sniper_and_enforcer_projectiles(self):
        for name in ("Sniper", "Enforcer"):
            with self.subTest(npc=name):
                definition = next(d for d in self.definitions.values() if d.name == name)
                run, hog, bot, _, angles = self.session(definition, distance=10)
                match = run.match
                fight = match.combat
                pool = fight.vitals_of(next(t for t in fight._targets() if t.owner is hog))
                hog.mover.yaw, hog.mover.pitch = angles
                self.assertEqual(npc_control.start(match, hog), bot.entity)
                for _ in range(240):
                    run.step({})
                    if fight.rockets:
                        break
                self.assertTrue(fight.rockets)
                self.assertTrue(all(rocket.shooter is bot for rocket in fight.rockets))
                self.assertTrue(all(rocket.position[2] > hog.mover.position[2] + 1.05
                                    for rocket in fight.rockets))
                self.assertEqual(pool.total, 600)

                match.collision_world = self.cover_world(hog)
                with patch.object(fight, "_shoot", wraps=fight._shoot) as shoot:
                    for _ in range(40):
                        run.step({})
                    shoot.assert_not_called()
                self.assertFalse(fight.rockets)
                self.assertEqual(pool.total, 600)
                self.assertFalse(bot.attack_input)

    def test_manual_npc_engagement_range_rechecks_existing_orders(self):
        for definition in self.definitions.values():
            with self.subTest(npc=definition.name):
                run, hog, bot, _, look = self.session(definition, distance=2)
                fight = run.match.combat
                pool = fight.vitals_of(next(t for t in fight._targets() if t.owner is hog))
                hog.mover.yaw, hog.mover.pitch = look
                self.assertEqual(npc_control.start(run.match, hog), bot.entity)
                for _ in range(240):
                    run.step({})
                    if pool.total < 600:
                        break
                before = pool.total
                self.assertLess(before, 600)
                self.assertTrue(bot.attack_input)

                is_assassin = definition.name == "Assassin"
                outside, inside = (3.25, 2.75) if is_assassin else (101, 99)
                bx, _, bz = bot.position
                original = tuple(hog.mover.position)
                hog.mover.position[:] = (bx + outside, original[1], bz)
                hog.mover.body = None
                with patch.object(fight, "_shoot", wraps=fight._shoot) as shoot:
                    for _ in range(100):
                        run.step({})
                        self.assertFalse(bot.attack_input)
                        self.assertFalse(fight.pins)
                    shoot.assert_not_called()
                self.assertEqual(pool.total, before)
                self.assertEqual(bot.attack_target, hog.entity)
                self.assertTrue(bot.firing)

                hog.mover.position[:] = (bx + inside, original[1], bz)
                hog.mover.body = None
                run.step({})
                self.assertTrue(bot.attack_input)
                if not is_assassin:
                    # The host admits the order here. The weapon has its own reach.
                    hog.mover.position[:] = original
                    hog.mover.body = None
                for _ in range(240):
                    run.step({})
                    if pool.total < before:
                        break
                self.assertLess(pool.total, before)

    def test_four_manual_npc_guns_resolve_ghost_and_solid_invulnerability(self):
        for definition in self.definitions.values():
            if definition.name == "Assassin":
                continue
            for hero, ghost in (("Reaper", True), ("Mei", False)):
                with self.subTest(npc=definition.name, hero=hero):
                    self.assert_incoming_gun_protection(definition, hero, ghost)

    def test_four_manual_npc_guns_resolve_fade_transcendence_and_meteor_protection(self):
        for definition in self.definitions.values():
            if definition.name == "Assassin":
                continue
            for hero, button, ghost in (("Moira", 8, True), ("Zenyatta", 32, False), ("Doomfist", 32, True)):
                with self.subTest(npc=definition.name, hero=hero):
                    self.assert_incoming_gun_protection(definition, hero, ghost, button=button)

    def assert_incoming_gun_protection(self, definition, hero, ghost, *, button=8):
        run, hog, bot, _, look = self.session(definition, distance=12, caster=True)
        match = run.match
        fight = match.combat
        guard = run.players[1]
        self.assertTrue(match.switch_hero(guard, next(h for h in heroes().values() if h.name == hero)))
        guard.client = NpcClient(guard)
        match.game_map = replace(match.game_map, free_for_all=False)
        hog.team = guard.team = 0
        self.assertNotEqual(bot.team, hog.team)
        x, y, z = hog.mover.position
        guard.mover.position[:] = (x + 3, y, z + 3)
        guard.mover.body = None
        for _ in range(40):
            run.step({})
        # Preserve the same target through the full duration of longer ghost scopes.
        fight.vitals[hog.body] = combat.Vitals(hog.body, 20000)
        targets = {t.owner: t for t in fight._targets()}
        own, front = (fight.vitals_of(targets[actor]) for actor in (hog, guard))
        before, guard_health = own.total, front.total
        hog.mover.yaw, hog.mover.pitch = look
        self.assertEqual(npc_control.start(match, hog), bot.entity)
        if definition.name in ("Sniper", "Enforcer"):
            for _ in range(180):
                run.step({})
                if fight.rockets:
                    break
            self.assertTrue(fight.rockets)
            self.assertTrue(all(rocket.shooter is bot for rocket in fight.rockets))
        if button == 32:
            script = guard.body_script
            script.set_ult_charge(script.component.entity_var(ULT_COST).value())
        run.step({guard: (0, 0, button)})
        self.assertEqual(defenses.protection(guard), (True, ghost))
        self.assertEqual(own.total, before)
        if definition.name in ("Sniper", "Enforcer"):
            self.assertTrue(fight.rockets)
            self.assertTrue(all(rocket.position[2] > guard.mover.position[2] for rocket in fight.rockets))
        guard.mover.position[0] = x
        guard.mover.body = None

        damage = fight._damage
        contacts = []

        def observe(shooter, target, *args, **kwargs):
            if shooter is bot:
                contacts.append(target.owner)
            return damage(shooter, target, *args, **kwargs)

        with patch.object(fight, "_damage", side_effect=observe):
            for _ in range(120):
                run.step({})
                if contacts:
                    break
        self.assertEqual(defenses.protection(guard), (True, ghost))
        self.assertTrue(contacts)
        self.assertTrue(all(actor is (hog if ghost else guard) for actor in contacts))
        self.assertEqual(front.total, guard_health)
        self.assertEqual(own.total < before, ghost)

        rear_after = own.total
        for _ in range(560):
            run.step({})
            if front.total < guard_health:
                break
            rear_after = own.total
        self.assertEqual(defenses.protection(guard), (False, False))
        self.assertLess(front.total, guard_health)
        self.assertEqual(own.total, rear_after)
        self.assertTrue(bot.attack_input)

    def test_deflect_returns_each_npc_gun_to_its_shooter_with_genji_credit(self):
        for definition in self.definitions.values():
            if definition.name == "Assassin":
                continue
            for lethal in (False, True):
                with self.subTest(npc=definition.name, lethal=lethal):
                    self.assert_reflected_npc_gun(definition, lethal=lethal)

    def assert_reflected_npc_gun(self, definition, *, lethal):
        run, hog, bot, npc_pool, look = self.session(definition, distance=12, caster=True)
        match = run.match
        fight = match.combat
        genji = run.players[1]
        self.assertTrue(match.switch_hero(genji, heroes()[0x02E0000000000029]))
        genji.client = NpcClient(genji)
        match.game_map = replace(match.game_map, free_for_all=False)
        hog.team = genji.team = 0
        x, y, z = hog.mover.position
        genji.mover.position[:] = (x + 3, y, z + 3)
        genji.mover.body = None
        for _ in range(40):
            run.step({})
        targets = {t.owner: t for t in fight._targets()}
        own, guard = (fight.vitals_of(targets[actor]) for actor in (hog, genji))
        if lethal:
            npc_pool.damage(npc_pool.total - 1)
            fight._sync_body_health(bot, npc_pool)
        before = npc_pool.total
        guard_health = guard.total
        hog.mover.yaw, hog.mover.pitch = look
        self.assertEqual(npc_control.start(match, hog), bot.entity)
        if definition.name in ("Sniper", "Enforcer"):
            for _ in range(180):
                run.step({})
                if fight.rockets:
                    break
            self.assertTrue(fight.rockets)
            self.assertTrue(all(rocket.shooter is bot for rocket in fight.rockets))

        genji.mover.position[0] = x
        genji.mover.body = None
        low, high = targets[bot].segment()
        point = tuple((a + b) / 2 for a, b in zip(low, high, strict=True))
        redirect = aim(fight._eye(genji), point)
        with patch.object(fight, "_damage", wraps=fight._damage) as damage:
            run.step({genji: (*redirect, 16)})
            for _ in range(115):
                run.step({genji: (*redirect, 0)})
                if npc_pool.total < before:
                    break
        self.assertLess(npc_pool.total, before)
        self.assertEqual(own.total, 600)
        self.assertEqual(guard.total, guard_health)
        hits = [(call.args[0], call.args[1].owner) for call in damage.call_args_list]
        self.assertTrue(hits)
        self.assertTrue(all(shooter is genji and victim is bot for shooter, victim in hits))
        self.assertTrue(genji.client.messages(combat.HIT))
        self.assertFalse(hog.client.messages(combat.HIT))
        if lethal:
            self.assertTrue(bot.dead)
            self.assertEqual(npc_pool.killer, genji.body)
            self.assertEqual([entity for entity, _ in genji.client.deaths], [bot.entity])
        else:
            self.assertFalse(bot.dead)

    def test_manual_weapon_input_causes_real_incoming_damage_and_stop(self):
        for name in ("Trooper", "Heavy Assault", "Sniper"):
            with self.subTest(npc=name):
                definition = next(d for d in self.definitions.values() if d.name == name)
                run, hog, bot, _, angles = self.session(definition, distance=10)
                hog.mover.yaw, hog.mover.pitch = angles
                self.assertEqual(npc_control.start(run.match, hog), bot.entity)
                own = run.match.combat.vitals_of(
                    next(t for t in run.match.combat._targets() if t.owner is hog),
                )
                for _ in range(180):
                    run.step({})
                self.assertLess(own.total, 600)
                self.assertIsNotNone(bot.body_script)
                if name == "Sniper":
                    record = graph.entity_body(bot.body)
                    loaded = {i.graph.index for i in bot.body_script.component.instances.values()}
                    self.assertIn(record["manager"], loaded)
                    self.assertTrue({i for i in record["weapons"] if i is not None} <= loaded)
                self.assertTrue(bot.attack_input)
                npc_control.stop(run.match)
                stopped = own.total
                for _ in range(80):
                    run.step({})
                self.assertEqual(own.total, stopped)
                self.assertFalse(bot.attack_input)

    def test_hook_blocks_manual_guns_until_release_then_resumes(self):
        for name, damage in (("Trooper", 20), ("Heavy Assault", 5), ("Sniper", 100)):
            with self.subTest(npc=name):
                self.check_manual_gun_hook_recovery(name, damage)

    def check_manual_gun_hook_recovery(self, name, damage):
        definition = next(d for d in self.definitions.values() if d.name == name)
        run, hog, bot, _, angles = self.session(definition, distance=10)
        hog.mover.yaw, hog.mover.pitch = angles
        npc_control.start(run.match, hog)
        own = run.match.combat.vitals_of(next(t for t in run.match.combat._targets() if t.owner is hog))
        for _ in range(240):
            run.step({hog: (*angles, 0)})
            if own.total < 600:
                break
        self.assertLess(own.total, 600)
        run.step({hog: (*angles, 8)})
        hooks = run.match.combat.hooks
        for _ in range(60):
            run.step({hog: (*angles, 0)})
            if hooks.controlled(bot.entity):
                break
        self.assertTrue(hooks.controlled(bot.entity))
        held = own.total
        held_ticks = 0
        for _ in range(100):
            run.step({hog: (*angles, 0)})
            if not hooks.controlled(bot.entity):
                break
            held_ticks += 1
            self.assertEqual(own.total, held)
            self.assertFalse(bot.attack_input)
        self.assertGreater(held_ticks, 10)
        self.assertFalse(hooks.controlled(bot.entity))
        resumed = own.total
        for _ in range(240):
            run.step({hog: (*angles, 0)})
            if own.total < resumed:
                break
        self.assertLess(own.total, resumed)
        self.assertEqual(resumed - own.total, damage)  # No catch-up burst from the held period.
        self.assertTrue(bot.attack_input)

    def test_breather_reduces_manual_heavy_input_damage_before_healing_starts(self):
        definition = next(d for d in self.definitions.values() if d.name == "Heavy Assault")
        losses = []
        for active in (False, True):
            run, hog, bot, _, angles = self.session(definition, distance=10)
            hog.mover.yaw, hog.mover.pitch = angles
            npc_control.start(run.match, hog)
            own = run.match.combat.vitals_of(next(t for t in run.match.combat._targets() if t.owner is hog))
            for _ in range(120):
                run.step({})
                if own.total < 600:
                    break
            self.assertLess(own.total, 600)
            run.step({hog: (*angles, 16 if active else 0)})
            before = own.total
            for _ in range(20):
                run.step({})
            losses.append(before - own.total)
            self.assertTrue(bot.attack_input)
        self.assertGreater(losses[0], 0)
        self.assertAlmostEqual(losses[1], losses[0] / 2)

    def test_breather_halves_actual_trooper_and_sniper_first_impact(self):
        for name, damage in (("Trooper", 20), ("Sniper", 100)):
            with self.subTest(npc=name):
                definition = next(d for d in self.definitions.values() if d.name == name)
                first = None
                for active in (False, True):
                    run, hog, bot, _, angles = self.session(definition, distance=10)
                    hog.mover.yaw, hog.mover.pitch = angles
                    self.assertEqual(npc_control.start(run.match, hog), bot.entity)
                    fight = run.match.combat
                    own = fight.vitals_of(next(t for t in fight._targets() if t.owner is hog))
                    for elapsed in range(240):
                        cast = active and elapsed == max(0, first - 20)
                        run.step({hog: (*angles, 16 if cast else 0)})
                        if own.total < 600:
                            break
                    self.assertEqual(own.total, 600 - damage * (0.5 if active else 1))
                    if active:
                        self.assertEqual(elapsed, first)
                        self.assertEqual(fight.incoming_damage_scale(hog), 0.5)
                    else:
                        first = elapsed

    def test_mine_hits_all_five_npcs_and_preserves_roadhog_hook_on_them(self):
        import test_game_roadhog_minefield as mine_tests
        from ow174.game import ball_mines

        if graph.entity_body(ball_mines.BODY) is None:
            self.skipTest("prepared NPC bundle with 2578/151A required")
        fixture = mine_tests.RoadhogMinefieldTests()
        for definition in self.definitions.values():
            for victim in ("npc", "hog"):
                with self.subTest(npc=definition.name, victim=victim):
                    run, hog, bot, pool, _ = self.session(definition, distance=35, caster=True)
                    fight = run.match.combat
                    ball = run.players[1]
                    self.assertTrue(run.match.switch_hero(ball, heroes()[ball_mines.HERO]))
                    run.match.game_map = replace(run.match.game_map, free_for_all=False)
                    hog.team, ball.team, bot.team = (0, 0, 1) if victim == "npc" else (0, 1, 1)
                    x, y, z = ball.mover.position
                    fixture.place(hog, (x + 15, y, z))
                    bot.position = (x + 22, y, z)
                    for _ in range(45):
                        run.step({})
                    mine = fixture.isolate(run, ball)
                    x, y, z = mine.position
                    before = pool.total
                    if victim == "npc":
                        bot.position = (x + .55, y, z)
                        run.step({})
                        self.assertTrue(mine.triggered)
                        self.assertEqual(before - pool.total, 125 if definition.armour else 130)
                        self.assertEqual(fight.impulses.velocity(bot.entity), (5, 5, 0))
                        self.assertEqual(fixture.pool(run, hog).total, 600)
                        continue

                    fixture.place(hog, (x, y, z - 4))
                    bot.position = (x, y, z + 13)
                    for _ in range(8):
                        run.step({})
                    look = aim(fight._eye(hog), (x, y + 1.2, z + 13))
                    run.step({hog: (*look, 8)})
                    for _ in range(65):
                        run.step({hog: (*look, 0)})
                        if fight.hooks.controlled(bot.entity):
                            break
                    self.assertTrue(fight.hooks.controlled(bot.entity))
                    fixture.place(hog, (x + .55, y, z))
                    run.step({})
                    self.assertTrue(mine.triggered)
                    self.assertEqual(fixture.pool(run, hog).total, 470)
                    self.assertTrue(fight.hooks.controlled(bot.entity))
                    self.assertEqual(before - pool.total, 25 if definition.armour else 30)

    def test_four_actual_npc_guns_can_destroy_mines_before_contact(self):
        import test_game_roadhog_minefield as mine_tests
        from ow174.game import ball_mines

        if graph.entity_body(ball_mines.BODY) is None:
            self.skipTest("prepared NPC bundle with 2578/151A required")
        fixture = mine_tests.RoadhogMinefieldTests()
        for definition in self.definitions.values():
            if definition.name == "Assassin":
                continue  # This body has a pin action instead of an authored gun.
            with self.subTest(npc=definition.name):
                run, hog, bot, _, _ = self.session(definition, distance=35, caster=True)
                fight = run.match.combat
                ball = run.players[1]
                ball.client = NpcClient(ball)
                self.assertTrue(run.match.switch_hero(ball, heroes()[ball_mines.HERO]))
                x, y, z = ball.mover.position
                fixture.place(hog, (x + 20, y, z))
                bot.position = (x + 28, y, z)
                for _ in range(45):
                    run.step({})
                mine = fixture.isolate(run, ball)
                x, y, z = mine.position
                bot.position = (x - (3 if definition.name == "Enforcer" else 6), y, z)
                # The manual target selector aims only at player bodies. This fixture
                # supplies the real AI weapon edge and pose without target acquisition.
                bot.spawner = {**bot.spawner, "manual_spawn": False}
                bot.yaw, bot.pitch = aim(fight._eye(bot), mine.centre())
                bot.firing = True
                component = bot.body_script.component
                button = component.world.ai_weapon_button(0)
                self.assertIsInstance(button, int)
                component.edges([(button, True)])
                for _ in range(260):
                    run.step({})
                    if not mine.health:
                        break
                component.edges([(button, False)])
                bot.firing = False
                self.assertEqual(mine.health, 0)
                self.assertFalse(mine.triggered)
                self.assertEqual(fixture.pool(run, hog).total, 600)
                for _ in range(100):
                    if mine.entity not in fight.mines.entities:
                        break
                    run.step({})
                self.assertNotIn(mine.entity, fight.mines.entities)


    def test_fireball_hits_all_five_npcs_and_preserves_roadhog_hook_on_them(self):
        import test_game_roadhog_grapple as grapple_tests
        from ow174.game import ball_grapple, stats

        fixture = grapple_tests.RoadhogGrappleTests()
        for definition in self.definitions.values():
            for hook in (False, True):
                with self.subTest(npc=definition.name, hook=hook):
                    run, hog, bot, pool, _ = self.session(definition, distance=35, caster=True)
                    fight = run.match.combat
                    ball = run.players[1]
                    self.assertTrue(run.match.switch_hero(ball, heroes()[ball_grapple.HERO]))
                    run.match.game_map = replace(run.match.game_map, free_for_all=False)
                    hog.team, ball.team, bot.team = (0, 1, 1) if hook else (0, 0, 1)
                    x, y, z = ball.mover.position
                    hog.mover.position[:] = (x if hook else x + 20, y, z + 6)
                    hog.mover.body = None
                    bot.position = (x + 12 if hook else x, y, z + 6)
                    fixture.prepare(run, ball)
                    before = pool.total
                    look = aim(fight._eye(hog), (x + 12, y + 1.2, z + 6))
                    for at in range(100):
                        run.step({ball: (0, 0, 2), hog: (*look, 8 if hook and at == 35 else 0)})
                        if ball.stats.value(stats.HERO_DAMAGE):
                            break
                    if hook:
                        self.assertEqual(fixture.pool(run, hog).total, 550)
                        self.assertTrue(fight.hooks.controlled(bot.entity))
                        self.assertEqual(before - pool.total, 25 if definition.armour else 30)
                    else:
                        amount = 45 if definition.armour else 50
                        self.assertEqual(before - pool.total, amount)
                        self.assertEqual(ball.body_script.component.entity_var(ULT_CHARGE).value(), amount)
                        self.assertEqual(fight.impulses.velocity(bot.entity), (0, 5, 22))
                        self.assertEqual(fixture.pool(run, hog).total, 600)


    def test_quad_cannons_damage_all_five_npcs_and_keep_roadhog_hook_live(self):
        import test_game_roadhog_quad_cannons as quad_tests
        from ow174.game import quad_cannons

        fixture = quad_tests.RoadhogQuadCannonsTests()
        for definition in self.definitions.values():
            for hook in (False, True):
                with self.subTest(npc=definition.name, hook=hook):
                    run, hog, bot, pool, _ = self.session(definition, distance=35, caster=True)
                    fight = run.match.combat
                    ball = run.players[1]
                    self.assertTrue(run.match.switch_hero(ball, heroes()[quad_cannons.HERO]))
                    run.match.game_map = replace(run.match.game_map, free_for_all=False)
                    hog.team, ball.team, bot.team = (0, 1, 1) if hook else (0, 0, 1)
                    x, y, z = ball.mover.position
                    fixture.place(hog, (x if hook else x + 20, y, z + 5))
                    bot.position = (x + 12 if hook else x, y, z + 5)
                    ball.body_script.ult_seconds = 0
                    ball.body_script.set_ult_charge(0)
                    for _ in range(50):
                        run.step({})
                    before = pool.total
                    if hook:
                        angles = fixture.look(run, hog, bot, 1.15)
                        run.step({hog: (*angles, 8)})
                        for _ in range(65):
                            if fight.hooks.controlled(bot.entity):
                                break
                            run.step({hog: (*angles, 0)})
                        self.assertTrue(fight.hooks.controlled(bot.entity))
                        run.step({ball: (*fixture.look(run, ball, hog), 1)})
                        self.assertEqual(fixture.pool(run, hog).total, 595)
                        self.assertTrue(fight.hooks.controlled(bot.entity))
                        self.assertEqual(before - pool.total, 25 if definition.armour else 30)
                        amount = 5
                    else:
                        run.step({ball: (*fixture.look(run, ball, bot, .65), 1)})
                        amount = 2.5 if definition.armour else 5
                        self.assertEqual(before - pool.total, amount)
                        self.assertEqual(fixture.pool(run, hog).total, 600)
                    self.assertEqual(fixture.weapon(ball).vars[53].value(), 79)
                    self.assertEqual(fixture.charge(ball), amount * .75 if hook else amount)


    def test_ball_quick_melee_damages_all_five_npcs_and_keeps_roadhog_hook_live(self):
        import test_game_roadhog_ball_melee as melee_tests
        import test_game_roadhog_quad_cannons as quad_tests

        from ow174.game import ball_grapple

        fixture = quad_tests.RoadhogQuadCannonsTests()
        melee = melee_tests.RoadhogBallMeleeTests()
        for definition in self.definitions.values():
            for hook in (False, True):
                with self.subTest(npc=definition.name, hook=hook):
                    run, hog, bot, pool, _ = self.session(definition, distance=35, caster=True)
                    fight = run.match.combat
                    ball = run.players[1]
                    self.assertTrue(run.match.switch_hero(ball, heroes()[ball_grapple.HERO]))
                    run.match.game_map = replace(run.match.game_map, free_for_all=False)
                    hog.team, ball.team, bot.team = (0, 1, 1) if hook else (0, 0, 1)
                    x, y, z = ball.mover.position
                    fixture.place(hog, (x if hook else x + 20, y, z + 1.7))
                    bot.position = (x + 12 if hook else x, y, z + 1.7)
                    ball.body_script.ult_seconds = 0
                    ball.body_script.set_ult_charge(0)
                    for _ in range(50):
                        run.step({})
                    before = pool.total
                    if hook:
                        look = fixture.look(run, hog, bot, 1.15)
                        run.step({hog: (*look, 8)})
                        for _ in range(65):
                            if fight.hooks.controlled(bot.entity):
                                break
                            run.step({hog: (*look, 0)})
                        self.assertTrue(fight.hooks.controlled(bot.entity))
                        look = fixture.look(run, ball, hog)
                        run.step({ball: (*look, 0x800)})
                        for _ in range(24):
                            if fixture.pool(run, hog).total < 600:
                                break
                            run.step({ball: (*look, 0)})
                        self.assertEqual(fixture.pool(run, hog).total, 570)
                        self.assertTrue(fight.hooks.controlled(bot.entity))
                        self.assertEqual(before - pool.total, 25 if definition.armour else 30)
                        amount = 30
                    else:
                        melee.melee(run, ball, bot)
                        amount = 25 if definition.armour else 30
                        self.assertEqual(before - pool.total, amount)
                        self.assertEqual(fixture.pool(run, hog).total, 600)
                    self.assertEqual(fixture.charge(ball), amount * .75 if hook else amount)


if __name__ == "__main__":
    unittest.main()
