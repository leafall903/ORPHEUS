"""Actual catalog NPC damage fixtures; AI/specials/client acceptance remain open.

Set ORPHEUS_NPC_TEST_DATA to an external directory containing the prepared
pve_npcs_174.json and extended game_graphs_174.json.gz. Normal runtime is unchanged.
"""

import os
import unittest
from pathlib import Path
from unittest.mock import patch

from test_game_combat import FakeClient, Run, aim

from ow174.game import bodytrace, combat, movelog, mover, npc
from ow174.game.collision import World
from ow174.game.content import heroes
from ow174.game.script import graph

HANZO = 0x02E0000000000005
INPUTS = {"bow": (0, 125), "sonic": (8, 125), "storm": (16, 350), "melee": (0x800, 30)}


def clear_graphs():
    graph._data.cache_clear()
    graph._records.cache_clear()
    graph.graph.cache_clear()


class NpcFixtureClient(FakeClient):
    """Record death stream callbacks without claiming real client delivery."""

    def __init__(self, player):
        super().__init__(player)
        self.npc_deaths = []
        self.npc_death_updates = []

    def queue_bot_death(self, entity, snapshot):
        self.npc_deaths.append((entity, snapshot))

    def queue_bot_death_update(self, entity, snapshot):
        self.npc_death_updates.append((entity, snapshot))

    def bot_stream(self, entity):
        return None  # no real NPC transport ACKs in this damage fixture


class HanzoCatalogNpcTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        folder = os.environ.get("ORPHEUS_NPC_TEST_DATA")
        if not folder:
            raise unittest.SkipTest("requires explicit prepared NPC data; no synthetic fallback")
        folder = Path(folder)
        cls.catalog = npc.load_catalog(folder / "pve_npcs_174.json")
        if set(cls.catalog) != set("12345"):
            raise ValueError("this matrix requires all five NPC definitions")
        table = mover._movers()
        if any(definition.body not in table for definition in cls.catalog.values()):
            raise ValueError("NPC matrix requires explicit104319 NPC mover rows;"
                             "use prepared NPC navigation runtime")
        cls.graph_patch = patch.object(graph, "GRAPHS_PATH", folder / "game_graphs_174.json.gz")
        cls.graph_patch.start()
        clear_graphs()

    @classmethod
    def tearDownClass(cls):
        cls.graph_patch.stop()
        clear_graphs()

    def setUp(self):
        for module in (bodytrace, movelog):
            self.enterContext(patch.object(module, "ENABLED", False))

    def arena(self, definition, action, situation, *, distance=None, lateral=0):
        run = Run()
        match = run.match
        # Explicit headless reconstruction: authored pellet counts, eye muzzles,
        # fixed ring phase / paired corners. Native spread and muzzle remain open.
        match.combat.configure_volley_geometry(reconstructed_patterns=True)
        match.bots.clear()
        match._pending_bot_scripts.clear()
        hero, = run.players
        hero.client = NpcFixtureClient(hero)
        match.switch_hero(hero, heroes()[HANZO])
        x, y, z = hero.mover.position
        if distance is None:
            distance = 2 if action == "melee" else 5
        bot = npc.spawn(match, definition, (x + lateral, y, z + distance),
                        team=hero.team if situation == "ally" else 1 - hero.team)
        self.assertEqual(bot.body, definition.body)
        self.assertIsNotNone(bot.body_script)
        self.assertTrue(bot.retain_corpse)
        if situation == "wall":
            w = z + (1 if action == "melee" else 2.5)
            match.collision_world = World([
                (x-20, y, z-20, x-20, y, z+20, x+20, y, z+20),
                (x-20, y, z-20, x+20, y, z+20, x+20, y, z-20),
                (x-10, y, w, x+10, y, w, x+10, y+5, w),
                (x-10, y, w, x+10, y+5, w, x-10, y+5, w),
                (x-10, y, w, x+10, y+5, w, x+10, y, w),
                (x-10, y, w, x-10, y+5, w, x+10, y+5, w),
            ], name="explicit floor and NPC occluder")
            hero.mover.use(world=match.collision_world)
        for _ in range(20):
            run.step({})
        return run, hero, bot

    def exercise(self, situation):
        for definition in self.catalog.values():
            for action, (button, amount) in INPUTS.items():
                with self.subTest(npc=definition.name, action=action, situation=situation):
                    run, hero, bot = self.arena(definition, action, situation)
                    yaw, pitch = self.target_aim(run, hero, bot)
                    if situation == "miss":
                        yaw, pitch = 16384, 0
                    if action == "melee":
                        run.step({hero: (yaw, pitch, button)})
                    else:
                        if button:
                            run.step({hero: (yaw, pitch, button)})
                            run.step({})
                        for _ in range(140 if action == "storm" else 70):
                            run.step({hero: (yaw, pitch, 1)})
                        if action != "storm":
                            run.step({hero: (yaw, pitch, 0)})
                    if action != "storm":
                        for _ in range(30):
                            run.step({})
                    # Releasing after Storm Arrows ends also releases the normal
                    # bow that is already drawing again. Measure the five-arrow
                    # skill before that distinct primary attack is released.
                    vitals = run.match.combat.vitals[bot.entity]
                    total = definition.health + definition.armour + definition.shields
                    self.assertEqual(definition.armour, 0)  # this explicit base-pool catalog
                    expected = max(0, total - amount) if situation == "hit" else total
                    self.assertEqual(vitals.total, expected)
                    shields = (max(0, definition.shields - amount)
                               if situation == "hit" else definition.shields)
                    self.assertEqual(vitals.now[combat.SHIELDS_POOL], shields)
                    self.assertEqual(vitals.now[combat.HEALTH_POOL], expected - shields)
                    hits = hero.client.messages(combat.HIT)
                    self.assertEqual(sum(h["+0x78"]["+0x4"] for h in hits), total - expected)
                    self.assertEqual(bot.dead, expected == 0)
                    self.assertFalse(any(who is bot for _, who in run.match.combat.respawns))
                    if bot.dead:
                        self.assertIsNotNone(bot.body_script)
                        self.assertTrue(bot.body_script.component.owner_dead)
                        self.assertEqual([entity for entity, _ in hero.client.npc_deaths], [bot.entity])
                        tick = run.frame + 1000
                        run.match.update(tick * combat.TICK, tick)
                        self.assertIn(bot, run.match.bots)
                        self.assertTrue(bot.dead)
                        self.assertFalse(any(who is bot for _, who in run.match.combat.respawns))
                    self.assertEqual(npc.remove_spawned(run.match), (bot.entity,))

    def test_direct_damage_and_death(self):
        self.exercise("hit")

    @staticmethod
    def target_aim(run, hero, bot):
        height = mover.mover_data(bot.body).stand_height
        return aim(run.match.combat._eye(hero),
                   (bot.position[0], bot.position[1] + height / 2, bot.position[2]))

    def test_friendly_bodies_are_not_damaged(self):
        self.exercise("ally")

    def test_off_axis_attacks_miss(self):
        self.exercise("miss")

    def test_static_wall_blocks_direct_attacks(self):
        self.exercise("wall")

    def test_actual_npc_pool_queries_follow_hanzo_damage_and_expose_enforcer_controller_gate(self):
        for definition in self.catalog.values():
            with self.subTest(npc=definition.name):
                run, hero, bot = self.arena(definition, "bow", "hit")
                fight = run.match.combat
                instance = next(iter(bot.body_script.component.instances.values()))

                def query(cls, instance=instance):
                    return instance.evaluate({"$": cls, "m_entity": {"$": "STU_B5A0CAF0"}})

                total = definition.health + definition.armour + definition.shields
                for cls in ("STU_9B7648C3", "STU_32E6E4D8", "STU_7D7D2470"):
                    self.assertEqual(query(cls), total)
                yaw, pitch = self.target_aim(run, hero, bot)
                for _ in range(70):
                    run.step({hero: (yaw, pitch, 1)})
                run.step({hero: (yaw, pitch, 0)})
                for _ in range(30):
                    run.step({})
                self.assertEqual(query("STU_9B7648C3"), total - 125)
                self.assertEqual(query("STU_32E6E4D8"), total - 125)
                self.assertEqual(query("STU_7D7D2470"), total)
                self.assertEqual(tuple(fight.vitals[bot.entity].component(fight.vitals[bot.entity].ids)[51]),
                                 bot.body_script.component.world.health_pools)
                if definition.name == "Enforcer":
                    weapon = next(i for i in bot.body_script.component.instances.values()
                                  if i.graph.index == 0x13EB)
                    self.assertTrue(weapon.states[1].active)  # node1: now beyond node18's health gate
                    bot.firing = True
                    bot.attack_target = hero.entity
                    with patch.object(fight, "_shoot", wraps=fight._shoot) as fire:
                        for _ in range(200):
                            run.step({})
                    self.assertFalse(fire.called)  # missing controller context remains explicit
                if definition.name == "Trooper":
                    for _ in range(70):
                        run.step({hero: (yaw, pitch, 1)})
                    run.step({hero: (yaw, pitch, 0)})
                    for _ in range(30):
                        run.step({})
                    self.assertTrue(bot.dead)
                    self.assertEqual(query("STU_9B7648C3"), 0)
                    self.assertEqual(query("STU_7D7D2470"), total)
                npc.remove_spawned(run.match)

    def test_catalog_capsules_use_explicit_movers_in_combat_targets(self):
        expected = {"1": (1.7, 0.5, 5.75), "2": (1.9, 0.5, 5.0),
                    "3": (1.75, 0.5, 4.5), "4": (1.5, 0.5, 14.0), "5": (2.3, 0.7, 2.2)}
        for key, values in expected.items():
            with self.subTest(npc=self.catalog[key].name):
                definition = self.catalog[key]
                data = mover._movers()[definition.body]  # direct row access forbids fallback
                self.assertEqual((data.stand_height, data.stand_radius, data.run_forward), values)
                run, _hero, bot = self.arena(definition, "bow", "hit")
                target = next(t for t in run.match.combat._targets() if t.owner is bot)
                self.assertEqual((target.height, target.radius), values[:2])
                npc.remove_spawned(run.match)

    def test_real_heavy_capsule_extends_melee_range_and_static_wall_still_blocks(self):
        for key, definition in self.catalog.items():
            for blocked in (False, True):
                with self.subTest(npc=definition.name, wall=blocked):
                    run, hero, bot = self.arena(definition, "melee", "wall" if blocked else "hit",
                                                distance=3.1)
                    run.step({hero: (0, 0, 0x800)})
                    for _ in range(10):
                        run.step({})
                    before = definition.health + definition.armour + definition.shields
                    damage = before - run.match.combat.vitals[bot.entity].total
                    self.assertEqual(damage, 30 if key == "5" and not blocked else 0)
                    npc.remove_spawned(run.match)

    def test_short_assassin_misses_horizontal_bow_and_takes_capsule_aimed_arrow(self):
        definition = self.catalog["4"]
        for inside in (False, True):
            with self.subTest(aim_inside=inside):
                run, hero, bot = self.arena(definition, "bow", "hit")
                yaw, pitch = self.target_aim(run, hero, bot) if inside else (0, 0)
                for _ in range(70):
                    run.step({hero: (yaw, pitch, 1)})
                run.step({hero: (yaw, pitch, 0)})
                for _ in range(30):
                    run.step({})
                self.assertEqual(run.match.combat.vitals[bot.entity].total, 375 if inside else 500)
                npc.remove_spawned(run.match)

    def test_authored_npc_controller_hit_range_faction_and_wall(self):
        # The public reconstructed manual controller supplies actual slot input
        # and aim. Native autonomous acquisition/specials remain unestablished.
        for key, hit_health in (("1", 20), ("3", 100), ("5", 0)):
            definition = self.catalog[key]
            for situation in ("hit", "range", "ally", "wall"):
                with self.subTest(npc=definition.name, situation=situation):
                    run, hero, bot = self.arena(
                        definition, "bow", "hit" if situation == "range" else situation,
                    )
                    bot.firing = True
                    bot.attack_target = hero.entity
                    if situation == "range":
                        hero.mover.position[:] = (bot.position[0], bot.position[1], bot.position[2] + 110)
                        hero.mover.body = None
                    fight = run.match.combat
                    target = next(t for t in fight._targets() if t.owner is hero)
                    vitals = fight.vitals_of(target)
                    with patch.object(fight, "_shoot", wraps=fight._shoot) as fire:
                        for _ in range(200):
                            run.step({})
                    self.assertEqual(fire.called, situation == "hit")
                    self.assertIsNotNone(bot.body_script)
                    expected = hit_health if situation == "hit" else 200
                    self.assertEqual(vitals.total, expected)
                    self.assertEqual(vitals.dead, expected == 0)
                    self.assertEqual(npc.remove_spawned(run.match), (bot.entity,))
