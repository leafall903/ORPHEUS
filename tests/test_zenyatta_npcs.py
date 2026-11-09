"""Opt-in actual five-body fixture. No AI/conditional difficulty/client claim.

Set ORPHEUS_NPC_DATA to the prepared read-only folder containing the graph
bundle and pve_npcs_174.json. ORPHEUS_RUNTIME_DIR must also contain the actual
five NPC mover rows, e.g. the separate npc-navigation-runtime metadata. Generic
fallback collision dimensions are explicitly rejected. Ordinary tests require
no mission assets; flat collision fixtures do not prove live deployment.
"""

import math
import os
import unittest
from pathlib import Path

from test_game_combat import Run, aim
from test_game_mover import box, wall

from ow174.game import combat, npc, npc_control
from ow174.game.barriers import PROJECTED, ZARYA
from ow174.game.collision import World
from ow174.game.commands import FIRE, SECONDARY
from ow174.game.content import heroes
from ow174.game.mover import mover_data
from ow174.game.script import graph
from ow174.game.zenyatta import HERO


@unittest.skipUnless(os.environ.get("ORPHEUS_NPC_DATA"), "needs explicit external prepared NPC data")
class ZenyattaNpcTests(unittest.TestCase):
    @classmethod
    def clear_graph_cache(cls):
        for name in ("_data", "_records", "graph", "bodies", "entity_body", "entity_script_component"):
            function = getattr(graph, name)
            if hasattr(function, "cache_clear"):
                function.cache_clear()

    @classmethod
    def setUpClass(cls):
        cls.old_path = graph.GRAPHS_PATH
        folder = Path(os.environ["ORPHEUS_NPC_DATA"])
        cls.catalog = npc.load_catalog(folder / "pve_npcs_174.json")
        dimensions = {'1': (1.7, .5), '2': (1.9, .5), '3': (1.75, .5),
                      '4': (1.5, .5), '5': (2.3, .7)}
        for key, (height, radius) in dimensions.items():
            data = mover_data(cls.catalog[key].body)
            if not (math.isclose(data.stand_height, height, abs_tol=1e-5)
                    and math.isclose(data.stand_radius, radius, abs_tol=1e-5)):
                raise AssertionError('NPC combat evidence requires actual104319 mover rows; '
                                     'generic fallback geometry is not accepted')
        graph.GRAPHS_PATH = folder / "game_graphs_174.json.gz"
        cls.clear_graph_cache()

    @classmethod
    def tearDownClass(cls):
        graph.GRAPHS_PATH = cls.old_path
        cls.clear_graph_cache()

    def arena(self, definition, team=1, names=('A',)):
        run = Run(names=names)
        zen = run.players[0]
        run.match.switch_hero(zen, heroes()[HERO])
        run.match.bots.clear()
        run.step({})
        for player in run.players:
            player.client.queue_bot_death = lambda *args: None
            player.client.queue_bot_death_update = lambda *args: None
            player.client.bot_stream = lambda body: None  # adapter has no observer transport stream
        x, y, z = zen.mover.position
        bot = npc.spawn(run.match, definition, (x, y, z+8), team=team, yaw=-32768)
        self.assertIsNotNone(bot.body_script)
        self.assertTrue(bot.retain_corpse)
        return run, zen, bot, run.match.combat.vitals[bot.entity]

    def test_primary_and_discord_hit_each_actual_enemy_body(self):
        for definition in self.catalog.values():
            with self.subTest(npc=definition.name):
                run, zen, bot, vitals = self.arena(definition)
                expected = combat.Vitals(0, definition.health, definition.armour, definition.shields)
                expected.damage(60)
                run.step({zen: (0, 0, 0x08)})
                run.step({zen: (0, 600, FIRE)})
                for _ in range(20):
                    run.step({})
                self.assertEqual(vitals.total, expected.total)
                self.assertEqual(zen.client.messages(combat.HIT)[0]["+0x78"]["+0x0"]["+0x0"], bot.entity)

    def test_discord_range_expiry_restores_base_damage_on_each_actual_npc(self):
        for definition in self.catalog.values():
            with self.subTest(npc=definition.name):
                run, zen, bot, vitals = self.arena(definition)
                fight = run.match.combat
                run.step({zen: (0, 0, 0x08)})
                owner = fight.zenyatta.owner(zen)
                self.assertEqual(owner.discord.target, bot.entity)
                last_visible = owner.discord.last_visible
                x, y, z = bot.position
                bot.position = (x, y, z+40)
                for _ in range(187):
                    run.step({})
                self.assertEqual(owner.discord.target, bot.entity)
                self.assertEqual(owner.discord.last_visible, last_visible)
                run.step({})
                self.assertIsNone(owner.discord)
                bot.position = (x, y, z)
                run.step({zen: (0, 600, FIRE)})
                for _ in range(20):
                    run.step({})
                expected = combat.Vitals(0, definition.health, definition.armour, definition.shields)
                expected.damage(48)
                self.assertEqual(vitals.total, expected.total)
                self.assertIsNone(owner.discord)
                hit, = zen.client.messages(combat.HIT)
                self.assertEqual(hit["+0x78"]["+0x0"]["+0x0"], bot.entity)

    def test_team_changes_retire_both_orb_types_on_each_actual_npc(self):
        for definition in self.catalog.values():
            with self.subTest(npc=definition.name):
                run, zen, bot, vitals = self.arena(definition)
                enemy_team = bot.team
                vitals.damage(100)
                run.step({zen: (0, 0, 0x08)})
                owner = run.match.combat.zenyatta.owner(zen)
                self.assertEqual(owner.discord.target, bot.entity)
                bot.team = zen.team
                run.step({zen: (0, 0, 0x10)})
                self.assertIsNone(owner.discord)
                self.assertEqual(owner.harmony.target, bot.entity)
                before = vitals.total
                for _ in range(10):
                    run.step({})
                self.assertAlmostEqual(vitals.total, before+4.8)
                before = vitals.total
                bot.team = enemy_team
                run.step({})
                self.assertIsNone(owner.harmony)
                self.assertIsNone(owner.discord)
                self.assertEqual(vitals.total, before)
                run.step({zen: (0, 600, FIRE)})
                for _ in range(20):
                    run.step({})
                hit, = zen.client.messages(combat.HIT)
                self.assertEqual(hit["+0x78"]["+0x0"]["+0x0"], bot.entity)
                self.assertLess(vitals.total, before)

    def test_projected_bubble_cleanses_and_consumes_zen_burst_on_each_actual_npc(self):
        for definition in self.catalog.values():
            with self.subTest(npc=definition.name):
                run, zen, bot, vitals = self.arena(definition, names=('Zen', 'Zarya'))
                zarya = run.players[1]
                run.match.switch_hero(zarya, heroes()[ZARYA])
                zarya.team = bot.team
                x, y, z = bot.position
                zarya.mover.position = [x+4, y, z]
                zarya.mover.body = None
                fight = run.match.combat
                target = next(t for t in fight._targets() if t.entity == bot.entity)
                target_aim = aim(fight._eye(zen), (x, y+target.eye, z))
                body_aim = aim(fight._eye(zen), (x, y+target.height/2, z))
                projected_aim = aim(fight._eye(zarya), (x, y+target.eye, z))
                run.step({zen: (*target_aim, 8)})
                owner = fight.zenyatta.owners[zen.body]
                self.assertEqual(owner.discord.target, bot.entity)
                for _ in range(130):
                    run.step({zen: (*body_aim, 2)})
                self.assertEqual(owner.discord.target, bot.entity)
                run.step({zarya: (*projected_aim, 16), zen: (*body_aim, 2)})
                bubble = fight.barriers.bubbles[zarya.body, PROJECTED]
                self.assertEqual((bubble.target, bubble.hp), (bot.entity, 200))
                self.assertIsNone(owner.discord)
                initial = vitals.total
                for _ in range(60):
                    run.step({})
                self.assertEqual(bubble.hp, 0)
                self.assertNotIn((zarya.body, PROJECTED), fight.barriers.bubbles)
                self.assertEqual(vitals.total, initial)
                self.assertFalse(zen.client.messages(combat.HIT))
                instance = zarya.body_script.component.instances[bubble.instance]
                self.assertGreater(instance.vars[61].value(), 7)
                run.step({zen: (*target_aim, 8)})
                self.assertEqual(owner.discord.target, bot.entity)
                run.step({zen: (*body_aim, FIRE)})
                for _ in range(25):
                    run.step({zen: (*body_aim, 0)})
                expected = combat.Vitals(0, definition.health, definition.armour, definition.shields)
                expected.damage(60)
                self.assertEqual(vitals.total, expected.total)
                hit, = zen.client.messages(combat.HIT)
                self.assertEqual(hit['+0x78']['+0x0']['+0x0'], bot.entity)

    def test_primary_edge_hit_and_miss_use_each_actual_npc_radius(self):
        for definition in self.catalog.values():
            for hit in (False, True):
                with self.subTest(npc=definition.name, hit=hit):
                    run, zen, bot, vitals = self.arena(definition)
                    radius = mover_data(definition.body).stand_radius
                    x, y, z = zen.mover.position
                    bot.position = (x+radius+(-.05 if hit else .05), y, z+8)
                    expected = combat.Vitals(0, definition.health, definition.armour, definition.shields)
                    if hit:
                        expected.damage(48)
                    run.step({zen: (0, 600, FIRE)})
                    for _ in range(25):
                        run.step({})
                    self.assertEqual(vitals.total, expected.total)
                    self.assertEqual(len(zen.client.messages(combat.HIT)), int(hit))

    def test_primary_wall_team_and_range_boundaries_use_actual_npc_geometry(self):
        for definition in self.catalog.values():
            for reason in ('wall', 'team', 'range', 'near_range'):
                with self.subTest(npc=definition.name, reason=reason):
                    run, zen, bot, vitals = self.arena(definition)
                    x, y, z = zen.mover.position
                    if reason == 'team':
                        bot.team = zen.team
                    elif reason in ('range', 'near_range'):
                        bot.position = (x, y, z+(190 if reason == 'range' else 170))
                    else:
                        run.match.collision_world = World([
                            (x-5, y-1, z+4, x+5, y-1, z+4, x+5, y+5, z+4),
                            (x-5, y-1, z+4, x+5, y+5, z+4, x-5, y+5, z+4),
                        ])
                    expected = combat.Vitals(0, definition.health, definition.armour, definition.shields)
                    if reason == 'near_range':
                        expected.damage(48)
                    bx, by, bz = bot.position
                    point = (bx, by+mover_data(definition.body).stand_height*.5, bz)
                    angles = aim(run.match.combat._eye(zen), point)
                    run.step({zen: (*angles, FIRE)})
                    for _ in range(140):
                        run.step({})
                    self.assertEqual(vitals.total, expected.total)
                    self.assertEqual(len(zen.client.messages(combat.HIT)), int(reason == 'near_range'))
                    self.assertFalse(run.match.combat.rockets)

    def test_harmony_heals_each_actual_friendly_npc(self):
        for definition in self.catalog.values():
            with self.subTest(npc=definition.name):
                run, zen, _, vitals = self.arena(definition, team=0)
                before = vitals.total
                vitals.damage(100)
                damaged = vitals.total
                run.step({zen: (0, 0, 0x10)})
                for _ in range(60):
                    run.step({})
                self.assertAlmostEqual(vitals.total, min(before, damaged+28.8))

    def test_harmony_los_grace_expires_on_each_actual_friendly_npc(self):
        for definition in self.catalog.values():
            with self.subTest(npc=definition.name):
                run, zen, bot, vitals = self.arena(definition, team=0)
                vitals.damage(115)
                run.step({zen: (0, 0, 0x10)})
                owner = run.match.combat.zenyatta.owner(zen)
                self.assertEqual(owner.harmony.target, bot.entity)
                before = vitals.total
                x, y, z = zen.mover.position
                clear = run.match.collision_world
                run.match.collision_world = World(
                    box(x-50, x+50, y-2, y, z-50, z+50)
                    + wall(x-5, z+4, x+5, z+4, y-1, y+5)
                )
                last_visible = owner.harmony.last_visible
                for _ in range(187):  # 2.992 s since the last clear update
                    run.step({})
                self.assertEqual(owner.harmony.target, bot.entity)
                self.assertEqual(owner.harmony.last_visible, last_visible)
                self.assertAlmostEqual(vitals.total, before+89.76)
                run.step({})
                self.assertIsNone(owner.harmony)  # 3.008 s: no healing on the expiry tick
                self.assertAlmostEqual(vitals.total, before+89.76)
                run.match.collision_world = clear
                run.step({})
                self.assertIsNone(owner.harmony)
                self.assertAlmostEqual(vitals.total, before+89.76)

    def test_full_secondary_damages_each_actual_enemy_npc(self):
        for definition in self.catalog.values():
            for marked, critical in ((False, False), (True, False), (False, True), (True, True)):
                with self.subTest(npc=definition.name, discord=marked, critical=critical):
                    run, zen, bot, vitals = self.arena(definition)
                    fight = run.match.combat
                    if marked:
                        run.step({zen: (0, 0, 0x08)})
                        owner = fight.zenyatta.owner(zen)
                        self.assertEqual(owner.discord.target, bot.entity)
                    angles = (0, 600)
                    if critical:
                        target = next(t for t in fight._targets() if t.owner is bot)
                        x, y, z = bot.position
                        angles = aim(fight._eye(zen), (x, y+min(target.eye, target.height-.08), z))
                    charge = zen.body_script.component.entity_var(342)
                    start_charge = charge.value()
                    start_frame = run.frame
                    before = vitals.total
                    expected = combat.Vitals(0, definition.health, definition.armour, definition.shields)
                    expected_hits = []
                    for _ in range(5):
                        dealt = expected.damage((60 if marked else 48) * (2 if critical else 1))
                        if dealt:
                            flags = combat.KILLING if expected.total == 0 else 0
                            if critical:
                                flags |= combat.CRITICAL
                            expected_hits.append((bot.entity, round(dealt), flags))
                    for _ in range(130):
                        run.step({zen: (*angles, SECONDARY)})
                    self.assertEqual(vitals.total, before)
                    for _ in range(60):
                        run.step({zen: (*angles, 0)})
                    self.assertEqual(vitals.total, expected.total)
                    hits = [msg["+0x78"] for msg in zen.client.messages(combat.HIT)]
                    self.assertEqual([(hit["+0x0"]["+0x0"], hit["+0x4"], hit["+0x8"])
                                      for hit in hits], expected_hits)
                    passive = (run.frame-start_frame)*.016*5
                    self.assertAlmostEqual(charge.value()-start_charge, before-vitals.total+passive, places=2)

    def test_full_secondary_rejects_wall_range_and_team_on_each_actual_npc(self):
        for definition in self.catalog.values():
            for reason in ("wall", "range", "team"):
                with self.subTest(npc=definition.name, reason=reason):
                    run, zen, bot, vitals = self.arena(definition)
                    x, y, z = zen.mover.position
                    if reason == "wall":
                        run.match.collision_world = World(
                            box(x-50, x+50, y-2, y, z-50, z+50)
                            + wall(x-5, z+4, x+5, z+4, y-1, y+5)
                        )
                    elif reason == "range":
                        bot.position = (x, y, z+190)
                    else:
                        bot.team = zen.team
                    bx, by, bz = bot.position
                    point = (bx, by+mover_data(definition.body).stand_height*.5, bz)
                    angles = aim(run.match.combat._eye(zen), point)
                    before = vitals.total
                    for _ in range(130):
                        run.step({zen: (*angles, SECONDARY)})
                    for _ in range(180):
                        run.step({zen: (*angles, 0)})
                    self.assertEqual(vitals.total, before)
                    self.assertFalse(zen.client.messages(combat.HIT))
                    self.assertFalse(run.match.combat.rockets)
                    weapon = next(i for i in zen.body_script.component.instances.values()
                                  if i.graph.index == 0x0164)
                    self.assertEqual(weapon.vars[53].value(), 15)

    def test_melee_and_discord_damage_each_actual_enemy_npc(self):
        for definition in self.catalog.values():
            for marked in (False, True):
                with self.subTest(npc=definition.name, discord=marked):
                    run, zen, bot, vitals = self.arena(definition)
                    x, y, z = zen.mover.position
                    if marked:
                        fight = run.match.combat
                        run.step({zen: (0, 0, 8)})
                        orb = fight.zenyatta.owners[zen.body].discord
                        self.assertIsNotNone(orb)
                        self.assertEqual(orb.target, bot.entity)
                    bot.position = (x, y, z+1.5)
                    expected = combat.Vitals(0, definition.health, definition.armour, definition.shields)
                    damage = expected.damage(37.5 if marked else 30)
                    run.step({zen: (0, 0, 0x800)})
                    for _ in range(30):
                        run.step({})
                    self.assertEqual(vitals.total, expected.total)
                    hit, = zen.client.messages(combat.HIT)
                    self.assertEqual(hit["+0x78"]["+0x0"]["+0x0"], bot.entity)
                    self.assertEqual(hit["+0x78"]["+0x4"], round(damage))
                    self.assertEqual(hit["+0x78"]["+0x8"], 0)

    def test_projected_bubble_blocks_melee_on_each_npc_until_expiry(self):
        for definition in self.catalog.values():
            with self.subTest(npc=definition.name):
                run, zen, bot, hp = self.arena(definition, names=("Zen", "Zarya"))
                zarya = run.players[1]
                self.assertTrue(run.match.switch_hero(zarya, heroes()[ZARYA]))
                zarya.team = bot.team
                x, y, z = zen.mover.position
                bot.position = (x, y, z+1.5)
                zarya.mover.position = [x+4, y, z+1.5]
                zarya.mover.body = None
                fight = run.match.combat
                target = next(t for t in fight._targets() if t.entity == bot.entity)
                direction = aim(fight._eye(zarya), (x, y+target.eye, z+1.5))
                run.step({zarya: (*direction, 16)})
                bubble = fight.barriers.bubbles[zarya.body, PROJECTED]
                self.assertEqual(bubble.target, bot.entity)
                self.assertEqual(bubble.hp, 200)
                before = hp.total
                run.step({zen: (0, 0, 0x800)})
                for _ in range(32):
                    run.step({})
                self.assertEqual(bubble.hp, 170)
                self.assertEqual(hp.total, before)
                self.assertFalse(zen.client.messages(combat.HIT))
                for _ in range(120):
                    run.step({})
                self.assertNotIn((zarya.body, PROJECTED), fight.barriers.bubbles)
                expected = combat.Vitals(0, definition.health, definition.armour, definition.shields)
                dealt = expected.damage(30)
                run.step({zen: (0, 0, 0x800)})
                for _ in range(32):
                    run.step({})
                self.assertEqual(hp.total, expected.total)
                hit, = zen.client.messages(combat.HIT)
                self.assertEqual(hit["+0x78"]["+0x0"]["+0x0"], bot.entity)
                self.assertEqual(hit["+0x78"]["+0x4"], dealt)

    def test_transcendence_heals_each_actual_friendly_npc(self):
        for definition in self.catalog.values():
            with self.subTest(npc=definition.name):
                run, zen, _, vitals = self.arena(definition, team=0)
                before = vitals.total
                vitals.damage(100)
                damaged = vitals.total
                zen.body_script.set_ult_charge(zen.body_script.component.entity_var(338).value())
                run.step({zen: (0, 0, 0x20)})
                for _ in range(10):
                    run.step({})
                self.assertAlmostEqual(vitals.total, min(before, damaged+48))

    def test_active_transcendence_rechecks_team_range_and_los_for_each_actual_npc(self):
        for definition in self.catalog.values():
            with self.subTest(npc=definition.name):
                run, zen, bot, vitals = self.arena(definition)
                fight = run.match.combat
                x, y, z = zen.mover.position
                clear_world = run.match.collision_world
                wall = World([
                    (x-5, y-1, z+4, x+5, y-1, z+4, x+5, y+5, z+4),
                    (x-5, y-1, z+4, x+5, y+5, z+4, x-5, y+5, z+4),
                ])
                vitals.damage(120)
                damaged = vitals.total
                zen.body_script.set_ult_charge(zen.body_script.component.entity_var(338).value())
                run.step({zen: (0, 0, 0x20)})
                self.assertTrue(fight.zenyatta.ultimate(zen))
                for team, distance, world in ((1, 8, clear_world), (0, 10.1, clear_world), (0, 9.9, wall)):
                    bot.team = team
                    bot.position = (x, y, z+distance)
                    run.match.collision_world = world
                    for _ in range(10):
                        run.step({})
                        self.assertEqual(vitals.total, damaged)
                run.match.collision_world = clear_world
                for _ in range(10):
                    run.step({})
                self.assertTrue(fight.zenyatta.ultimate(zen))
                self.assertAlmostEqual(vitals.total, damaged+48, places=4)

    def test_melee_respects_wall_range_and_team_on_each_actual_npc(self):
        for definition in self.catalog.values():
            for reason in ("wall", "range", "team"):
                with self.subTest(npc=definition.name, reason=reason):
                    run, zen, bot, hp = self.arena(definition)
                    x, y, z = zen.mover.position
                    clear = run.match.collision_world
                    enemy_team = bot.team
                    bot.position = (x, y, z+(6 if reason == "range" else 1.5))
                    if reason == "wall":
                        run.match.collision_world = World(
                            box(x-50, x+50, y-2, y, z-50, z+50)
                            + wall(x-5, z+.75, x+5, z+.75, y-1, y+5)
                        )
                    elif reason == "team":
                        bot.team = zen.team
                    before = hp.total
                    run.step({zen: (0, 0, 0x800)})
                    for _ in range(60):
                        run.step({})
                    self.assertEqual(hp.total, before)
                    self.assertFalse(zen.client.messages(combat.HIT))

                    run.match.collision_world = clear
                    bot.position = (x, y, z+1.5)
                    bot.team = enemy_team
                    run.step({zen: (0, 0, 0x800)})
                    for _ in range(32):
                        run.step({})
                    expected = combat.Vitals(0, definition.health, definition.armour, definition.shields)
                    expected.damage(30)
                    self.assertEqual(hp.total, expected.total)
                    hit, = zen.client.messages(combat.HIT)
                    self.assertEqual(hit["+0x78"]["+0x0"]["+0x0"], bot.entity)

    def test_active_transcendence_does_not_revive_any_actual_npc_after_lethal_orb(self):
        for definition in self.catalog.values():
            with self.subTest(npc=definition.name):
                run, zen, bot, vitals = self.arena(definition, team=0, names=('Healer', 'Attacker'))
                attacker = run.players[1]
                run.match.switch_hero(attacker, heroes()[HERO])
                attacker.team = 1
                x, y, z = bot.position
                attacker.mover.position = [x, y, z+2]
                attacker.mover.body = None
                for _ in range(20):
                    run.step({})
                vitals.damage(vitals.total-1)
                wounded = vitals.total
                zen.body_script.set_ult_charge(zen.body_script.component.entity_var(338).value())
                run.step({zen: (0, 0, 0x20)})
                run.step({})
                self.assertAlmostEqual(vitals.total, wounded+4.8, places=4)
                fight = run.match.combat
                angles = aim(fight._eye(attacker), (x, y+mover_data(bot.body).stand_height*.5, z))
                run.step({attacker: (*angles, FIRE)})
                for _ in range(8):
                    if bot.dead:
                        break
                    run.step({attacker: (*angles, 0)})
                self.assertTrue(bot.dead)
                self.assertEqual(vitals.total, 0)
                for _ in range(30):
                    run.step({})
                    self.assertTrue(fight.zenyatta.ultimate(zen))
                    self.assertIs(fight.vitals[bot.entity], vitals)
                    self.assertEqual(vitals.total, 0)
                self.assertIn(bot, run.match.bots)

    def test_four_ranged_npcs_damage_zen_and_ultimate_rejects_damage(self):
        for key in ("1", "2", "3", "5"):
            definition = self.catalog[key]
            with self.subTest(npc=definition.name):
                run, zen, bot, _ = self.arena(definition)
                self.assertEqual(npc_control.start(run.match, zen), bot.entity)
                own = next(t for t in run.match.combat._targets() if t.owner is zen)
                vitals = run.match.combat.vitals_of(own)
                for _ in range(400):
                    run.step({})
                    if vitals.total < 200:
                        break
                self.assertLess(vitals.total, 200)
                self.assertGreater(vitals.total, 0)
                zen.body_script.set_ult_charge(zen.body_script.component.entity_var(338).value())
                run.step({zen: (0, 0, 0x20)})
                for _ in range(300):
                    run.step({})
                self.assertEqual(vitals.total, 200)

    def test_actual_enemy_discord_increases_each_npc_damage(self):
        for key, expected in (("1", 20), ("2", 28), ("3", 100), ("4", 25), ("5", 5)):
            for discord in (False, True):
                with self.subTest(npc=self.catalog[key].name, discord=discord):
                    run, zen, bot, _ = self.arena(self.catalog[key], names=("Zen", "Enemy Zen"))
                    enemy = run.players[1]
                    self.assertTrue(run.match.switch_hero(enemy, heroes()[HERO]))
                    enemy.team = bot.team
                    fight = run.match.combat
                    x, y, z = zen.mover.position
                    enemy.mover.position = [x+6, y, z]
                    enemy.mover.body = None
                    if key == "4":
                        bot.position = (x, y, z+2)
                    bx, by, bz = bot.position
                    select = aim(fight._eye(zen), (bx, by+mover_data(bot.body).stand_height*.5, bz))
                    mark = aim(fight._eye(enemy), (x, y+1, z))
                    for _ in range(8):
                        run.step({zen: (*select, 0), enemy: (*mark, 0)})
                    run.step({zen: (*select, 0), enemy: (*mark, 8 if discord else 0)})
                    if discord:
                        self.assertEqual(fight.zenyatta.owners[enemy.body].discord.target, zen.body)
                    self.assertEqual(npc_control.start(run.match, zen), bot.entity)
                    hp = fight.vitals[zen.body]
                    for _ in range(180):
                        run.step({})
                        if hp.total < 200:
                            break
                    if key == "2":
                        for _ in range(8):
                            run.step({})
                    self.assertAlmostEqual(200-hp.total, expected*(1.25 if discord else 1), places=4)

    def test_projected_barrier_intercepts_each_actual_ranged_npc_attacking_zen(self):
        for key in ('1', '2', '3', '5'):
            definition = self.catalog[key]
            with self.subTest(npc=definition.name):
                run, zen, bot, _ = self.arena(definition, names=('Zen', 'Zarya'))
                zarya = run.players[1]
                run.match.switch_hero(zarya, heroes()[ZARYA])
                zarya.team = zen.team
                x, y, z = zen.mover.position
                zarya.mover.position = [x+4, y, z]
                zarya.mover.body = None
                fight = run.match.combat
                own = next(t for t in fight._targets() if t.owner is zen)
                vitals = fight.vitals_of(own)
                self.assertEqual(npc_control.start(run.match, zen), bot.entity)
                if key == '3':
                    # The authored Sniper scope winds up past the two-second
                    # barrier duration when both skills start on the same tick.
                    for _ in range(32):
                        run.step({})
                    self.assertEqual(vitals.total, 200)
                angles = aim(fight._eye(zarya), fight._eye(zen))
                run.step({zarya: (*angles, 16)})
                bubble = fight.barriers.bubbles[zarya.body, PROJECTED]
                self.assertEqual(bubble.target, zen.body)
                for _ in range(124):
                    run.step({})
                    if bubble.hp < 200:
                        break
                self.assertLess(bubble.hp, 200)
                self.assertEqual(vitals.total, 200)
                for _ in range(400):
                    run.step({})
                    if vitals.total < 200:
                        break
                self.assertNotIn((zarya.body, PROJECTED), fight.barriers.bubbles)
                self.assertLess(vitals.total, 200)
                self.assertGreater(vitals.total, 0)

    def test_enforcer_uses_all_twenty_five_pellets_and_four_authored_damage_entries(self):
        for index, total_damage in enumerate((28, 56, 84, 112)):
            with self.subTest(index=index):
                run, zen, bot, _ = self.arena(self.catalog['2'])
                bot.body_script.component.set(9948, index)
                npc_control.start(run.match, zen)
                target = next(t for t in run.match.combat._targets() if t.owner is zen)
                vitals = run.match.combat.vitals_of(target)
                maximum_projectiles = 0
                for _ in range(100):
                    run.step({})
                    maximum_projectiles = max(maximum_projectiles, len(run.match.combat.rockets))
                self.assertEqual(maximum_projectiles, 25)
                self.assertEqual(vitals.total, 200-total_damage)
                weapon = next(
                    i for i in bot.body_script.component.instances.values() if i.graph.index == 0x13EB
                )
                self.assertEqual(weapon.state(14).counter, 1)
                self.assertEqual(weapon.vars[53].value(), 3)

    def test_enforcer_stop_prevents_subsequent_damage(self):
        run, zen, bot, _ = self.arena(self.catalog['2'])
        npc_control.start(run.match, zen)
        target = next(t for t in run.match.combat._targets() if t.owner is zen)
        vitals = run.match.combat.vitals_of(target)
        for _ in range(100):
            run.step({})
        self.assertLess(vitals.total, 200)
        npc_control.stop(run.match)
        before = vitals.total
        for _ in range(80):
            run.step({})
        self.assertEqual(vitals.total, before)
        self.assertFalse(bot.attack_input)
        self.assertFalse(run.match.combat.rockets)

    def test_enforcer_rejects_invalid_damage_index_without_starting_attack(self):
        for index in (-1, 4, True, 0.5):
            with self.subTest(index=index):
                run, zen, bot, _ = self.arena(self.catalog['2'])
                bot.body_script.component.set(9948, index)
                with self.assertRaises(ValueError):
                    npc_control.start(run.match, zen)
                self.assertFalse(bot.firing)
                self.assertIsNone(bot.attack_target)

    def test_wall_range_and_team_deny_attacks_then_original_target_takes_damage(self):
        for key in ("1", "2", "3", "4", "5"):
            definition = self.catalog[key]
            for reason in ("wall", "range", "team"):
                with self.subTest(npc=definition.name, reason=reason):
                    run, zen, bot, _ = self.arena(definition)
                    fight = run.match.combat
                    hp = fight.vitals[zen.body]
                    clear = run.match.collision_world
                    x, y, z = zen.mover.position
                    if key == "4":
                        bot.position = (x, y, z+2)
                        angles = aim(fight._eye(zen), (x, y+mover_data(bot.body).stand_height*.5, z+2))
                        run.step({zen: (*angles, 0)})
                    position, team = bot.position, bot.team
                    self.assertEqual(npc_control.start(run.match, zen), bot.entity)
                    if reason == "wall":
                        run.match.collision_world = World(
                            box(x-50, x+50, y-2, y, z-50, z+50)
                            + wall(x-5, z+1, x+5, z+1, y-1, y+5)
                        )
                    elif reason == "range":
                        bot.position = (x, y, z+180)
                    else:
                        bot.team = zen.team
                    for _ in range(125):
                        run.step({})
                    self.assertEqual(hp.total, 200)
                    self.assertFalse(bot.attack_input)
                    self.assertNotIn(bot.entity, fight.pins)
                    run.match.collision_world = clear
                    bot.position, bot.team = position, team
                    for _ in range(250):
                        run.step({})
                        if hp.total < 200:
                            break
                    self.assertGreater(hp.total, 0)
                    self.assertLess(hp.total, 200)
                    if key == "4":
                        self.assertEqual(fight.pins.get(bot.entity), zen.body)
                        self.assertEqual(hp.total, 175)

    def test_wall_team_and_ultimate_are_observed_after_launch(self):
        for key, count, clear_damage in (("2", 25, 28), ("3", 1, 100)):
            for mode in ("clear", "wall", "team", "ultimate"):
                with self.subTest(npc=self.catalog[key].name, mode=mode):
                    run, zen, bot, _ = self.arena(self.catalog[key])
                    fight = run.match.combat
                    hp = fight.vitals[zen.body]
                    self.assertEqual(npc_control.start(run.match, zen), bot.entity)
                    for _ in range(160):
                        run.step({})
                        volley = [r for r in fight.rockets if r.shooter is bot]
                        if volley:
                            break
                    self.assertEqual(len(volley), count)
                    self.assertEqual(hp.total, 200)
                    x, y, z = zen.mover.position
                    if mode == "wall":
                        run.match.collision_world = World(
                            box(x-50, x+50, y-2, y, z-50, z+50)
                            + wall(x-8, z+2, x+8, z+2, y-1, y+5)
                        )
                    elif mode == "team":
                        zen.team = bot.team
                    elif mode == "ultimate":
                        zen.body_script.set_ult_charge(zen.body_script.component.entity_var(338).value())
                        run.step({zen: (0, 0, 32)})
                        self.assertTrue(fight.zenyatta.ultimate(zen))
                    for _ in range(25):
                        run.step({})
                    self.assertEqual(hp.total, 200-clear_damage if mode == "clear" else 200)
                    if mode == "wall":
                        self.assertTrue(all(r not in fight.rockets for r in volley))
                    if mode == "ultimate":
                        self.assertTrue(any(r not in fight.rockets for r in volley))
                    if mode == "team":
                        self.assertTrue(any(r in fight.rockets and r.position[2] < z for r in volley))

    def test_enforcer_authored_cadence_and_reload_continue_beyond_two_magazines(self):
        run, zen, bot, _ = self.arena(self.catalog['2'])
        # Explicit durable test actor: verify repeated volleys without player death/respawn.
        vitals = combat.Vitals(zen.body, 5000)
        run.match.combat.vitals[zen.body] = vitals
        npc_control.start(run.match, zen)
        for _ in range(1200):
            run.step({})
        weapon = next(i for i in bot.body_script.component.instances.values() if i.graph.index == 0x13EB)
        self.assertEqual(weapon.state(14).counter, 8)
        self.assertEqual(vitals.total, 4776)
        self.assertEqual(weapon.vars[53].value(), 4)

    def test_sniper_manager_and_authored_weapons_are_hosted(self):
        run, _, bot, _ = self.arena(self.catalog['3'])
        scripts = {instance.graph.index for instance in bot.body_script.component.instances.values()}
        self.assertTrue({0x0015, 0x1122, 0x1134} <= scripts)
        # This allocation check deliberately does not claim that Sniper fires.
        self.assertIn(bot, run.match.bots)

    def test_each_manual_npc_killed_by_orbs_keeps_its_corpse_past_respawn_deadline(self):
        for definition in self.catalog.values():
            with self.subTest(npc=definition.name):
                run, zen, bot, vitals = self.arena(definition)
                for _ in range(4000):
                    run.step({zen: (0, 600, FIRE)})
                    if bot.dead:
                        break
                self.assertTrue(bot.dead)
                self.assertEqual(vitals.total, 0)
                for _ in range(220):
                    run.step({})
                self.assertIn(bot, run.match.bots)
                self.assertIs(run.match.combat.vitals[bot.entity], vitals)
                self.assertFalse(any(who is bot for _, who in run.match.combat.respawns))


if __name__ == "__main__":
    unittest.main()
