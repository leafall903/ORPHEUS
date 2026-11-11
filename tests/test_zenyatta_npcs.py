"""Opt-in actual five-body fixture for manual combat and explicit difficulty.

Set ORPHEUS_NPC_DATA to the prepared read-only folder containing the graph
bundle and pve_npcs_174.json. ORPHEUS_RUNTIME_DIR must also contain the actual
five NPC mover rows, e.g. the separate npc-navigation-runtime metadata. Generic
fallback collision dimensions are explicitly rejected. Ordinary tests require
no mission assets; flat collision fixtures do not prove live deployment.
"""

import math
import os
import unittest
from itertools import product
from pathlib import Path
from unittest.mock import patch

from test_game_combat import Run, aim
from test_game_mover import box, wall

from ow174.game import combat, npc, npc_context, npc_control, npc_shields, stats
from ow174.game.barriers import PROJECTED, WINSTON, ZARYA
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

    def arena(self, definition, team=1, names=('A',), *, difficulty_index=None):
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
        bot = npc.spawn(run.match, definition, (x, y, z+8), team=team, yaw=-32768,
                        difficulty_index=difficulty_index)
        self.assertIsNotNone(bot.body_script)
        self.assertTrue(bot.retain_corpse)
        return run, zen, bot, run.match.combat.vitals[bot.entity]

    def test_actual_chain_hook_pulls_all_five_npcs_across_world_updates(self):
        roadhog = 0x02E0000000000040
        for definition, skipped in product(self.catalog.values(), (False, True)):
            with self.subTest(npc=definition.name, skipped=skipped):
                run, zen, bot, vitals = self.arena(definition, names=("Zen", "Roadhog"))
                hog = run.players[1]
                self.assertTrue(run.match.switch_hero(hog, heroes()[roadhog]))
                hog.team = zen.team
                x, y, z = bot.position
                hog.mover.position = [x + 8, y, z]
                hog.mover.body = None
                for _ in range(25):
                    run.step({})
                fight = run.match.combat
                direction = aim(fight._eye(hog), (x, y + mover_data(bot.body).stand_height * .5, z))
                run.step({hog: (*direction, 0x08)})
                for _ in range(30):
                    run.step({hog: (*direction, 0)})
                    hook = fight.chain_hooks.get(hog.body)
                    if hook is not None and hook.victim == bot.entity:
                        break
                self.assertIsNotNone(hook)
                self.assertEqual(hook.victim, bot.entity)
                self.assertTrue(fight.is_stunned(bot))
                self.assertEqual(bot.position, (x, y, z))
                if skipped:
                    run.frame += 4
                for _ in range(30):
                    run.step({})
                self.assertIsNone(hook.victim)
                self.assertFalse(fight.is_stunned(bot))
                self.assertAlmostEqual(bot.position[0], x + 5, delta=0.05)
                self.assertAlmostEqual(bot.position[2], z, delta=0.02)
                self.assertEqual(bot.velocity, (0.0, 0.0, 0.0))
                self.assertIsNone(bot.impulse_mover)
                expected = combat.Vitals(0, definition.health, definition.armour, definition.shields)
                damage = expected.damage(30)
                self.assertEqual(vitals.total, expected.total)
                hits = hog.client.messages(combat.HIT)
                self.assertEqual(len(hits), 1)
                self.assertEqual(hits[0]["+0x78"]["+0x0"]["+0x0"], bot.entity)
                self.assertEqual(hits[0]["+0x78"]["+0x4"], damage)

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
                orb = owner.discord
                first_visible = orb.last_visible
                x, y, z = bot.position
                bot.position = (x, y, z+40)
                for _ in range(170):
                    run.step({})
                self.assertIs(owner.discord, orb)
                self.assertEqual(orb.last_visible, first_visible)

                bot.position = (x, y, z)
                run.step({zen: (0, 600, FIRE)})
                self.assertIs(owner.discord, orb)
                self.assertGreater(orb.last_visible, first_visible)
                for _ in range(30):
                    run.step({})
                expected = combat.Vitals(0, definition.health, definition.armour, definition.shields)
                first_damage = expected.damage(60)
                self.assertEqual(vitals.total, expected.total)

                last_visible = orb.last_visible
                bot.position = (x, y, z+40)
                for _ in range(187):
                    run.step({})
                self.assertEqual(owner.discord.target, bot.entity)
                self.assertEqual(owner.discord.last_visible, last_visible)
                run.step({})
                self.assertIsNone(owner.discord)
                bot.position = (x, y, z)
                regen = None
                if definition.hero in npc_shields.SHIELDED_HEROES and definition.shields:
                    component = bot.body_script.component
                    recovery, = (i for i in component.instances.values() if i.graph.index == 0x0E62)
                    regen = recovery.states[recovery.graph.node(51).state]
                    self.assertTrue(regen.active)
                run.step({zen: (0, 600, FIRE)})
                for _ in range(20):
                    if regen is not None:
                        before_hit_ms = component.now
                        before_hit_shields = vitals.now[combat.SHIELDS_POOL]
                    run.step({})
                    if regen is not None and not regen.active:
                        expected.now[combat.SHIELDS_POOL] += (before_hit_ms - regen.start) * .03
                        self.assertAlmostEqual(before_hit_shields, expected.now[combat.SHIELDS_POOL], places=5)
                        regen = None
                second_damage = expected.damage(48)
                self.assertAlmostEqual(vitals.total, expected.total, places=5)
                self.assertIsNone(owner.discord)
                for _ in range(12):
                    run.step({})
                run.step({zen: (0, 0, 0x08)})
                self.assertEqual(owner.discord.target, bot.entity)
                self.assertIsNot(owner.discord, orb)
                run.step({zen: (0, 600, FIRE)})
                for _ in range(20):
                    run.step({})
                third_damage = expected.damage(60)
                self.assertAlmostEqual(vitals.total, expected.total, places=5)
                hits = [notice["+0x78"] for notice in zen.client.messages(combat.HIT)]
                self.assertEqual(
                    [(hit["+0x0"]["+0x0"], hit["+0x4"]) for hit in hits],
                    [(bot.entity, round(damage)) for damage in (first_damage, second_damage, third_damage)],
                )

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
        for definition, tier in product(self.catalog.values(), (None, 0, 1, 2, 3)):
            with self.subTest(npc=definition.name, tier=tier):
                run, zen, bot, vitals = self.arena(definition, names=('Zen', 'Zarya'), difficulty_index=tier)
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
                initial_base = vitals.now.copy()
                conditional = getattr(vitals, "conditional_pools", {})
                initial_extra = {kind: pool.now for kind, pool in conditional.items()}
                charge = zen.body_script.component.entity_var(342)
                start_charge, start_frame = charge.value(), run.frame
                transitions = [(bubble.hp, initial)]
                for _ in range(60):
                    run.step({})
                    state = (bubble.hp, vitals.total)
                    if state != transitions[-1]:
                        transitions.append(state)
                self.assertEqual(transitions, [(hp, initial) for hp in (200, 152, 104, 56, 8, 0)])
                self.assertAlmostEqual(charge.value()-start_charge, (run.frame-start_frame)*.016*5, places=4)
                weapon = next(i for i in zen.body_script.component.instances.values() if i.graph.index == 0x164)
                self.assertEqual(weapon.vars[53].value(), 15)
                self.assertNotIn((zarya.body, PROJECTED), fight.barriers.bubbles)
                self.assertEqual(vitals.total, initial)
                self.assertEqual(vitals.now, initial_base)
                self.assertEqual({kind: pool.now for kind, pool in conditional.items()}, initial_extra)
                self.assertFalse(zen.client.messages(combat.HIT))
                instance = zarya.body_script.component.instances[bubble.instance]
                self.assertGreater(instance.vars[61].value(), 7)
                energy_graph = next(i for i in zarya.body_script.component.instances.values()
                                    if i.graph.index == 0x0242)
                self.assertAlmostEqual(energy_graph.find_var(1, 87, create=False).value(), 40, places=5)
                run.step({zen: (*target_aim, 8)})
                self.assertEqual(owner.discord.target, bot.entity)
                run.step({zen: (*body_aim, FIRE)})
                for _ in range(25):
                    run.step({zen: (*body_aim, 0)})
                expected = combat.Vitals(0, vitals.pool_maximum(combat.HEALTH_POOL),
                                         definition.armour, vitals.pool_maximum(combat.SHIELDS_POOL))
                expected.damage(60)
                self.assertEqual(vitals.total, expected.total)
                self.assertEqual(vitals.pool_total(combat.SHIELDS_POOL), expected.now[combat.SHIELDS_POOL])
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

    def test_harmony_and_transcendence_heal_through_projected_bubble_expiry(self):
        for definition in self.catalog.values():
            with self.subTest(npc=definition.name):
                run, zen, bot, vitals = self.arena(definition, team=0, names=("Zen", "Zarya"))
                zarya = run.players[1]
                self.assertTrue(run.match.switch_hero(zarya, heroes()[ZARYA]))
                zarya.team = zen.team
                x, y, z = bot.position
                zarya.mover.position = [x+4, y, z]
                zarya.mover.body = None
                fight = run.match.combat
                target = next(t for t in fight._targets() if t.entity == bot.entity)
                projected_aim = aim(fight._eye(zarya), (x, y+target.eye, z))
                mark = aim(fight._eye(zen), (x, y+target.eye, z))
                vitals.damage(180)
                run.step({zarya: (*projected_aim, 16)})
                key = (zarya.body, PROJECTED)
                bubble = fight.barriers.bubbles[key]
                self.assertEqual(bubble.target, bot.entity)
                for _ in range(105):
                    run.step({})
                self.assertIs(fight.barriers.bubbles[key], bubble)
                charge = zen.body_script.component.entity_var(342)
                charge_before, frame_before, hp_before = charge.value(), run.frame, vitals.total
                run.step({zen: (*mark, 0x10)})
                owner = fight.zenyatta.owners[zen.body]
                orb = owner.harmony
                self.assertEqual(orb.target, bot.entity)
                for _ in range(10):
                    run.step({})
                self.assertAlmostEqual(vitals.total, hp_before+4.8, places=4)
                self.assertAlmostEqual(charge.value()-charge_before,
                                       4.8+(run.frame-frame_before)*.016*5, places=2)

                zen.body_script.set_ult_charge(zen.body_script.component.entity_var(338).value())
                run.step({zen: (0, 0, 0x20)})
                self.assertTrue(fight.zenyatta.ultimate(zen))
                self.assertIs(fight.barriers.bubbles[key], bubble)
                hp_before = vitals.total
                for tick in range(1, 21):
                    run.step({})
                    self.assertIs(owner.harmony, orb)
                    self.assertAlmostEqual(vitals.total, hp_before+5.28*tick, places=4)
                    self.assertEqual(charge.value(), 0)
                self.assertNotIn(key, fight.barriers.bubbles)
                self.assertEqual(bubble.hp, 200)

    def test_winston_dome_expiry_reopens_transcendence_on_all_five_npcs(self):
        run, zen, trooper, _ = self.arena(
            self.catalog["1"], team=0, names=("Zen", "Winston", "Outside Ally"),
        )
        _, winston, outside = run.players
        zen.team = outside.team = 0
        winston.team = 1
        self.assertTrue(run.match.switch_hero(winston, heroes()[WINSTON]))
        x, y, z = zen.mover.position
        winston.mover.position = [x, y, z+8]
        outside.mover.position = [x+4, y, z+2]
        winston.mover.body = outside.mover.body = None
        inside = [trooper]
        for key, offset in zip(("2", "3", "4", "5"), (-3, -1.5, 1.5, 3), strict=True):
            bot = npc.spawn(run.match, self.catalog[key], (x+offset, y, z+8), team=0, yaw=-32768)
            self.assertIsNotNone(bot.body_script)
            inside.append(bot)
        for _ in range(90):
            run.step({})
        fight = run.match.combat
        run.step({winston: (-32768, 0, 0x10)})
        for _ in range(45):
            dome = fight.barriers.domes.get(winston.body)
            if dome is not None and dome.hp == 700:
                break
            run.step({})
        else:
            self.fail("Winston did not create the authored dome")
        child, = dome.component.instances.values()
        self.assertEqual(child.graph.index, 0xFC)
        timer = child.states[11]
        self.assertTrue(timer.active)
        for _ in range(560):
            if dome.component.now - timer.start >= 8800:
                break
            run.step({})
            self.assertTrue(timer.active)
        else:
            self.fail("Winston dome clock did not reach the pre-expiry window")

        health = {bot.entity: fight.vitals[bot.entity] for bot in inside}
        health[outside.body] = fight.vitals[outside.body]
        for vitals in health.values():
            vitals.damage(100)
        troop_target = next(target for target in fight._targets() if target.owner is trooper)
        tx, ty, tz = troop_target.feet
        harmony = aim(fight._eye(zen), (tx, ty+troop_target.eye, tz))
        run.step({zen: (*harmony, 0x10)})
        owner = fight.zenyatta.owners[zen.body]
        orb = owner.harmony
        self.assertEqual(orb.target, trooper.entity)
        zen.body_script.set_ult_charge(zen.body_script.component.entity_var(338).value())
        run.step({zen: (*harmony, 0x20)})
        self.assertTrue(fight.zenyatta.ultimate(zen))
        charge = zen.body_script.component.entity_var(342)

        protected_ticks = 0
        open_ticks = 0
        for _ in range(24):
            before = {entity: vitals.total for entity, vitals in health.items()}
            run.step({})
            self.assertTrue(fight.zenyatta.ultimate(zen))
            self.assertIs(owner.harmony, orb)
            self.assertEqual(charge.value(), 0)
            blocked = winston.body in fight.barriers.domes
            self.assertEqual(timer.active, blocked)
            self.assertEqual(dome.hp, 700)
            for entity, vitals in health.items():
                healing = 0.48 if entity == trooper.entity else 0
                if entity == outside.body or not blocked:
                    healing += 4.8
                self.assertAlmostEqual(vitals.total-before[entity], healing, places=4)
            if blocked:
                protected_ticks += 1
            else:
                open_ticks += 1
                if open_ticks == 3:
                    break
        self.assertGreaterEqual(protected_ticks, 8)
        self.assertEqual(open_ticks, 3)
        self.assertAlmostEqual(timer.ended_at-timer.start, 9000, delta=16)

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
                blocked = World(
                    box(x-50, x+50, y-2, y, z-50, z+50)
                    + wall(x-5, z+4, x+5, z+4, y-1, y+5)
                )
                orb = owner.harmony
                charge = zen.body_script.component.entity_var(342)
                start_charge, start_frame = charge.value(), run.frame
                run.match.collision_world = blocked
                for _ in range(45):
                    run.step({})
                self.assertIs(owner.harmony, orb)
                self.assertAlmostEqual(vitals.total, before+21.6)
                old_visible = orb.last_visible

                run.match.collision_world = clear
                run.step({})
                self.assertIs(owner.harmony, orb)
                self.assertGreater(orb.last_visible, old_visible)
                self.assertAlmostEqual(vitals.total, before+22.08)
                run.match.collision_world = blocked
                last_visible = owner.harmony.last_visible
                for _ in range(187):  # 2.992 s since the last clear update
                    run.step({})
                self.assertEqual(owner.harmony.target, bot.entity)
                self.assertEqual(owner.harmony.last_visible, last_visible)
                self.assertAlmostEqual(vitals.total, before+111.84)
                run.step({})
                self.assertIsNone(owner.harmony)  # 3.008 s: no healing on the expiry tick
                self.assertAlmostEqual(vitals.total, before+111.84)
                run.match.collision_world = clear
                run.step({})
                self.assertIsNone(owner.harmony)
                self.assertAlmostEqual(vitals.total, before+111.84)
                passive = (run.frame-start_frame)*.016*5
                self.assertAlmostEqual(charge.value()-start_charge, 111.84+passive, places=2)

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

    def test_npc_team_changes_during_charge_and_between_volley_impacts(self):
        for definition in self.catalog.values():
            with self.subTest(npc=definition.name):
                run, zen, bot, vitals = self.arena(definition)
                fight = run.match.combat
                enemy_team = bot.team
                run.step({zen: (0, 0, 8)})
                owner = fight.zenyatta.owners[zen.body]
                self.assertEqual(owner.discord.target, bot.entity)
                for _ in range(60):
                    run.step({zen: (0, 600, SECONDARY)})
                bot.team = zen.team
                for _ in range(15):
                    run.step({zen: (0, 600, SECONDARY)})
                self.assertIsNone(owner.discord)
                bot.team = enemy_team
                for _ in range(55):
                    run.step({zen: (0, 600, SECONDARY)})
                self.assertIsNone(owner.discord)

                charge = zen.body_script.component.entity_var(342)
                before_charge, before_frame, before_hp = charge.value(), run.frame, vitals.total
                for _ in range(60):
                    run.step({zen: (0, 600, 0)})
                    if len(zen.client.messages(combat.HIT)) >= 2:
                        break
                else:
                    self.fail("The first two charged orbs did not hit the hostile NPC")
                self.assertEqual(len(zen.client.messages(combat.HIT)), 2)
                bot.team = zen.team
                for _ in range(80):
                    run.step({zen: (0, 600, 0)})

                expected = combat.Vitals(0, definition.health, definition.armour, definition.shields)
                damages = [round(expected.damage(48)) for _ in range(2)]
                hits = [event["+0x78"] for event in zen.client.messages(combat.HIT)]
                self.assertEqual([(hit["+0x0"]["+0x0"], hit["+0x4"]) for hit in hits],
                                 [(bot.entity, damage) for damage in damages])
                self.assertEqual(vitals.total, expected.total)
                weapon = next(i for i in zen.body_script.component.instances.values() if i.graph.index == 0x164)
                self.assertEqual(weapon.vars[53].value(), 15)
                passive = (run.frame-before_frame)*.016*5
                self.assertAlmostEqual(charge.value()-before_charge, before_hp-vitals.total+passive, places=2)

    @unittest.skipUnless(os.environ.get("ORPHEUS_BARRIER_DATA"), "needs prepared Reinhardt barrier mesh")
    def test_npc_discord_distinguishes_reinhardt_barrier_and_solid_cover(self):
        for definition, friendly in product(self.catalog.values(), (False, True)):
            with self.subTest(npc=definition.name, friendly=friendly):
                run, zen, bot, vitals = self.arena(definition, names=("Zen", "Reinhardt"), difficulty_index=3)
                rein = run.players[1]
                self.assertTrue(run.match.switch_hero(rein, heroes()[0x02E0000000000007]))
                rein.team = bot.team
                x, y, z = zen.mover.position
                rein.mover.position = [x+1.5, y, z+4]
                rein.mover.body = None
                fight = run.match.combat
                target = next(t for t in fight._targets() if t.entity == bot.entity)
                mark = aim(fight._eye(zen), (x, y+target.eye, z+8))
                direction = aim(fight._eye(zen), (x, y+target.height/2, z+8))
                raised = {rein: (-32768, 0, SECONDARY)}
                for _ in range(30):
                    run.step(raised)
                shield = fight.barriers.owners[rein.body]
                self.assertTrue(shield.raised)

                run.step({**raised, zen: (*mark, 8)})
                owner = fight.zenyatta.owner(zen)
                self.assertIsNone(owner.discord)
                self.assertNotIn("discord", owner.ready)
                for _ in range(10):
                    run.step({})
                run.step({zen: (*mark, 8)})
                discord = owner.discord
                self.assertEqual(discord.target, bot.entity)

                for _ in range(205):
                    run.step(raised)
                self.assertTrue(shield.raised)
                self.assertIs(owner.discord, discord)
                self.assertEqual(discord.last_visible, fight.now)
                charge = zen.body_script.component.entity_var(342)
                before_hp, before_shield = vitals.total, shield.hp
                before_charge, before_frame = charge.value(), run.frame
                run.step({**raised, zen: (*direction, FIRE)})
                for _ in range(33):
                    run.step(raised)
                self.assertEqual(shield.hp, before_shield-48)
                self.assertEqual(vitals.total, before_hp)
                self.assertFalse(zen.client.messages(combat.HIT))
                passive = (run.frame-before_frame)*.08
                self.assertAlmostEqual(charge.value()-before_charge, passive, places=3)

                if friendly:
                    rein.team = zen.team
                    commands = raised
                else:
                    commands = {}
                    for _ in range(10):
                        run.step({})
                self.assertEqual(shield.raised, friendly)
                run.step({**commands, zen: (*direction, FIRE)})
                for _ in range(25):
                    run.step(commands)
                expected = combat.Vitals(0, vitals.pool_maximum(combat.HEALTH_POOL),
                                         definition.armour, vitals.pool_maximum(combat.SHIELDS_POOL))
                damage = expected.damage(60)
                hit, = (notice["+0x78"] for notice in zen.client.messages(combat.HIT))
                self.assertEqual((hit["+0x0"]["+0x0"], hit["+0x4"]), (bot.entity, damage))
                self.assertEqual(vitals.total, expected.total)
                self.assertIs(owner.discord, discord)
                passive = (run.frame-before_frame)*.08
                self.assertAlmostEqual(charge.value()-before_charge, damage+passive, places=3)

                last_visible = discord.last_visible
                run.match.collision_world = World(
                    box(x-50, x+50, y-2, y, z-50, z+50)
                    + wall(x-5, z+4, x+5, z+4, y-1, y+5)
                )
                self.assertFalse(fight.zenyatta.visible(zen, target))
                for _ in range(187):
                    run.step(commands)
                self.assertEqual(discord.last_visible, last_visible)
                self.assertIs(owner.discord, discord)
                run.step(commands)
                self.assertIsNone(owner.discord)

    def test_npc_volley_rechecks_cover_between_launch_and_impact(self):
        for definition in self.catalog.values():
            with self.subTest(npc=definition.name):
                run, zen, bot, vitals = self.arena(definition, difficulty_index=3)
                fight = run.match.combat
                clear = run.match.collision_world
                x, y, z = zen.mover.position
                bot.position = (x, y, z+24)
                target = next(t for t in fight._targets() if t.entity == bot.entity)
                direction = aim(fight._eye(zen), (x, y+target.height/2, z+24))
                run.step({zen: (*direction, 8)})
                owner = fight.zenyatta.owners[zen.body]
                discord = owner.discord
                self.assertEqual(discord.target, bot.entity)
                for _ in range(130):
                    run.step({zen: (*direction, SECONDARY)})

                charge = zen.body_script.component.entity_var(342)
                start_frame, start_charge, start_health = run.frame, charge.value(), vitals.total
                run.step({zen: (*direction, 0)})
                first, = fight.rockets
                self.assertIs(first.shooter, zen)
                a, b = (x-4, y-1, z+4), (x+4, y-1, z+4)
                c, d = (x+4, y+4, z+4), (x-4, y+4, z+4)
                run.match.collision_world = World([a+b+c, a+c+d])
                issued = [first]
                for _ in range(40):
                    run.step({zen: (*direction, 0)})
                    for rocket in fight.rockets:
                        if not any(rocket is previous for previous in issued):
                            issued.append(rocket)
                    if len(issued) >= 2 and not fight.rockets:
                        break
                self.assertEqual(len(issued), 2)
                self.assertFalse(fight.rockets)
                self.assertEqual(vitals.total, start_health)
                self.assertFalse(zen.client.messages(combat.HIT))
                self.assertIs(owner.discord, discord)
                self.assertAlmostEqual(charge.value()-start_charge, (run.frame-start_frame)*.08, places=3)

                run.match.collision_world = clear
                for _ in range(75):
                    run.step({zen: (*direction, 0)})
                expected = combat.Vitals(0, vitals.pool_maximum(combat.HEALTH_POOL),
                                         definition.armour, vitals.pool_maximum(combat.SHIELDS_POOL))
                damage = [round(expected.damage(60)) for _ in range(3)]
                hits = [event["+0x78"] for event in zen.client.messages(combat.HIT)]
                self.assertEqual([(hit["+0x0"]["+0x0"], hit["+0x4"]) for hit in hits],
                                 [(bot.entity, amount) for amount in damage])
                self.assertEqual(vitals.total, expected.total)
                self.assertIs(owner.discord, discord)
                self.assertFalse(fight.rockets)
                weapon = next(i for i in zen.body_script.component.instances.values()
                              if i.graph.index == 0x164)
                self.assertEqual(weapon.vars[53].value(), 15)
                passive = (run.frame-start_frame)*.08
                effective = start_health - vitals.total
                self.assertAlmostEqual(charge.value()-start_charge, effective+passive, places=3)

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
        for definition, tier in product(self.catalog.values(), (None, 0, 1, 2, 3)):
            with self.subTest(npc=definition.name, tier=tier):
                run, zen, bot, hp = self.arena(definition, names=("Zen", "Zarya"), difficulty_index=tier)
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
                run.step({})
                mark = aim(fight._eye(zen), (x, y+target.eye, z+1.5))
                body_aim = aim(fight._eye(zen), (x, y+target.height*.5, z+1.5))
                run.step({zen: (*mark, 8)})
                owner = fight.zenyatta.owners[zen.body]
                self.assertEqual(owner.discord.target, bot.entity)
                orb = owner.discord
                run.step({zarya: (*direction, 16)})
                self.assertIsNone(owner.discord)
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
                ready = owner.ready.get("discord")
                run.step({zen: (*mark, 8)})
                self.assertIsNone(owner.discord)
                self.assertEqual(owner.ready.get("discord"), ready)
                for _ in range(120):
                    run.step({})
                self.assertNotIn((zarya.body, PROJECTED), fight.barriers.bubbles)
                self.assertEqual(bubble.hp, 170)
                self.assertIsNone(owner.discord)
                expected = combat.Vitals(0, hp.pool_maximum(combat.HEALTH_POOL),
                                         definition.armour, hp.pool_maximum(combat.SHIELDS_POOL))
                dealt = expected.damage(30)
                run.step({zen: (0, 0, 0x800)})
                for _ in range(32):
                    run.step({})
                self.assertEqual(hp.total, expected.total)
                hit, = zen.client.messages(combat.HIT)
                self.assertEqual(hit["+0x78"]["+0x0"]["+0x0"], bot.entity)
                self.assertEqual(hit["+0x78"]["+0x4"], dealt)

                run.step({zen: (*mark, 8)})
                self.assertEqual(owner.discord.target, bot.entity)
                self.assertIsNot(owner.discord, orb)
                for _ in range(130):
                    run.step({zen: (*body_aim, SECONDARY)})
                for _ in range(60):
                    run.step({zen: (*body_aim, 0)})
                expected_damage = [round(damage) for _ in range(5) if (damage := expected.damage(60))]
                self.assertEqual(hp.total, expected.total)
                hits = [event["+0x78"] for event in zen.client.messages(combat.HIT)]
                self.assertEqual([(hit["+0x0"]["+0x0"], hit["+0x4"]) for hit in hits[1:]],
                                 [(bot.entity, damage) for damage in expected_damage])
                weapon = next(i for i in zen.body_script.component.instances.values() if i.graph.index == 0x164)
                self.assertEqual(weapon.vars[53].value(), 15)

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
                charge = zen.body_script.component.entity_var(342)
                for team, distance, world in ((1, 8, clear_world), (0, 10.1, clear_world), (0, 9.9, wall)):
                    bot.team = team
                    bot.position = (x, y, z+distance)
                    run.match.collision_world = world
                    before = vitals.total
                    for _ in range(10):
                        run.step({})
                        self.assertEqual(vitals.total, before)
                        self.assertEqual(charge.value(), 0)

                    bot.team = zen.team
                    bot.position = (x, y, z+9.9)
                    run.match.collision_world = clear_world
                    for tick in range(1, 5):
                        run.step({})
                        self.assertTrue(fight.zenyatta.ultimate(zen))
                        self.assertAlmostEqual(vitals.total, before+4.8*tick, places=4)
                        self.assertEqual(charge.value(), 0)

                run.match.collision_world = wall
                still = vitals.total
                self.assertAlmostEqual(still, damaged+57.6, places=4)
                for _ in range(400):
                    run.step({})
                    self.assertEqual(vitals.total, still)
                    if not fight.zenyatta.ultimate(zen):
                        break
                else:
                    self.fail("Transcendence did not expire")
                run.match.collision_world = clear_world
                for _ in range(10):
                    run.step({})
                    self.assertEqual(vitals.total, still)
                self.assertGreater(charge.value(), 0)

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

    def test_released_volley_finishes_during_transcendence_against_each_npc(self):
        for definition, use_ultimate in product(self.catalog.values(), (False, True)):
            with self.subTest(npc=definition.name, use_ultimate=use_ultimate):
                run, zen, bot, vitals = self.arena(definition, difficulty_index=3)
                fight = run.match.combat
                x, y, z = zen.mover.position
                bot.position = (x, y, z+24)
                target = next(t for t in fight._targets() if t.entity == bot.entity)
                mark = aim(fight._eye(zen), (x, y+target.eye, z+24))
                direction = aim(fight._eye(zen), (x, y+target.height/2, z+24))
                run.step({zen: (*mark, 8)})
                owner = fight.zenyatta.owner(zen)
                self.assertEqual(owner.discord.target, bot.entity)
                for _ in range(130):
                    run.step({zen: (*direction, SECONDARY)})

                origin, start_hp = run.frame, vitals.total
                launches = []

                def step(button=0):
                    run.step({zen: (*direction, button)})
                    for rocket in fight.rockets:
                        if not any(rocket is old for _, old in launches):
                            launches.append((run.frame-origin, rocket))

                with patch.object(fight, "_shoot", wraps=fight._shoot) as dispatch:
                    for _ in range(7):
                        step()
                    self.assertEqual([tick for tick, _ in launches], [1, 7])
                    self.assertEqual(len(fight.rockets), 2)
                    self.assertEqual(vitals.total, start_hp)
                    self.assertFalse(zen.client.messages(combat.HIT))
                    script = zen.body_script
                    weapon = next(i for i in script.component.instances.values() if i.graph.index == 0x164)
                    self.assertEqual(weapon.vars[53].value(), 15)
                    charge = script.component.entity_var(342)
                    before_charge = charge.value()
                    if use_ultimate:
                        script.set_ult_charge(script.component.entity_var(338).value())
                    step(0x20 if use_ultimate else 0)
                    self.assertEqual(fight.zenyatta.ultimate(zen), use_ultimate)
                    self.assertEqual(len(fight.rockets), 2)
                    for elapsed in range(70):
                        step(FIRE if use_ultimate and elapsed == 40 else 0)

                self.assertEqual([tick for tick, _ in launches], [1, 7, 14, 21, 28])
                self.assertTrue(all(rocket.shooter is zen and rocket.definition == 0x0400000000000261
                                    for _, rocket in launches))
                self.assertEqual([call.args[3] for call in dispatch.call_args_list], list(range(5)))
                activations = {(call.args[2].instance.id, call.args[2].index, call.kwargs["activation"])
                               for call in dispatch.call_args_list}
                self.assertEqual(len(activations), 1)
                expected = combat.Vitals(0, vitals.pool_maximum(combat.HEALTH_POOL),
                                         definition.armour, vitals.pool_maximum(combat.SHIELDS_POOL))
                damage = [round(dealt) for _ in range(5) if (dealt := expected.damage(60))]
                notices = [notice["+0x78"] for notice in zen.client.messages(combat.HIT)]
                self.assertEqual([(hit["+0x0"]["+0x0"], hit["+0x4"]) for hit in notices],
                                 [(bot.entity, amount) for amount in damage])
                self.assertEqual(vitals.total, expected.total)
                self.assertFalse(fight.rockets)
                self.assertEqual(weapon.vars[53].value(), 20 if use_ultimate else 15)
                if use_ultimate:
                    self.assertEqual(charge.value(), 0)
                    self.assertTrue(fight.zenyatta.ultimate(zen))
                else:
                    self.assertAlmostEqual(charge.value()-before_charge,
                                           start_hp-vitals.total+71*.08, places=3)

    def test_actual_npc_attacks_sample_transcendence_at_expiry(self):
        # Delay from the Q command to NPC engagement, followed by the observed impact batches.
        cases = {
            "1": ((346, ((368, 1), (371, 1), (374, 1))), (347, ((369, 1), (372, 1), (375, 1)))),
            "2": ((305, ((374, 12), (375, 2))), (306, ((375, 12), (376, 2)))),
            "3": ((245, ((374, 1),)), (246, ((375, 1),))),
            "4": ((373, ()), (374, ((375, 1),))),
            "5": ((342, ((374, 1),)), (343, ((375, 1),))),
        }
        amounts = {"1": 20, "2": 8, "3": 100, "4": 25, "5": 5}
        end = 374  # World combat still sees Q active before this update's body command.
        for key, trials in cases.items():
            for delay, batches in trials:
                with self.subTest(npc=self.catalog[key].name, delay=delay):
                    run, zen, bot, _ = self.arena(self.catalog[key], difficulty_index=3)
                    fight = run.match.combat
                    vitals = fight.vitals[zen.body]
                    if key == "4":
                        x, y, z = zen.mover.position
                        bot.position = (x, y, z+2)
                    x, y, z = bot.position
                    target = next(t for t in fight._targets() if t.owner is bot)
                    direction = aim(fight._eye(zen), (x, y+target.height/2, z))
                    script = zen.body_script
                    script.set_ult_charge(script.component.entity_var(338).value())
                    run.step({zen: (*direction, 0x20)})
                    self.assertTrue(fight.zenyatta.ultimate(zen))
                    origin = run.frame
                    owner = fight.zenyatta.owner(zen)
                    original_clock = owner.last_damage
                    impact = []
                    contact_tick = None
                    damage = fight._damage

                    def observe(shooter, victim, amount, *args, **kwargs):
                        active = fight.zenyatta.ultimate(zen)
                        previous = vitals.total
                        result = damage(shooter, victim, amount, *args, **kwargs)
                        if shooter is bot and victim.owner is zen:
                            impact.append((run.frame-origin, active, amount, previous-vitals.total))
                        return result

                    with patch.object(fight, "_damage", side_effect=observe):
                        for tick in range(1, end+3):
                            if tick == delay+1:
                                self.assertEqual(npc_control.start(run.match, zen), bot.entity)
                            run.step({zen: (*direction, 0)})
                            self.assertEqual(fight.zenyatta.ultimate(zen), tick < end)
                            if tick < end:
                                self.assertEqual(script.component.entity_var(342).value(), 0)
                            if key == "4" and tick > delay and contact_tick is None:
                                phase = next(i for i in bot.body_script.component.instances.values()
                                             if i.graph.index == 0x1157)
                                contact = phase.states.get(phase.graph.node(25).state)
                                if contact is not None and contact.counter:
                                    contact_tick = tick
                    amount = amounts[key]
                    expected = [
                        (tick, tick <= end, amount, 0 if tick <= end else amount)
                        for tick, count in batches for _ in range(count)
                    ]
                    self.assertEqual(impact, expected)
                    damaging_ticks = [tick for tick, _, _, dealt in expected if dealt]
                    self.assertEqual(vitals.total, 200-amount*len(damaging_ticks))
                    expected_clock = (origin+damaging_ticks[-1])*.016 if damaging_ticks else original_clock
                    self.assertAlmostEqual(owner.last_damage, expected_clock)
                    self.assertEqual(contact_tick, delay+1 if key == "4" else None)
                    pinned = key == "4" and bool(damaging_ticks)
                    self.assertEqual(fight.pins.get(bot.entity), zen.body if pinned else None)
                    self.assertEqual(fight.is_stunned(zen), pinned)

    def test_four_ranged_npcs_damage_zen_before_and_after_ultimate(self):
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
                fight = run.match.combat
                self.assertTrue(fight.zenyatta.ultimate(zen))
                owner = fight.zenyatta.owners[zen.body]
                damage_clock = owner.last_damage
                with patch.object(fight, "_damage", wraps=fight._damage) as observed:
                    for _ in range(400):
                        if not fight.zenyatta.ultimate(zen):
                            break
                        self.assertEqual(vitals.total, 200)
                        run.step({})
                self.assertTrue(any(call.args[0] is bot and call.args[1].owner is zen
                                    for call in observed.call_args_list))
                self.assertEqual(owner.last_damage, damage_clock)
                self.assertFalse(fight.zenyatta.ultimate(zen))
                for _ in range(400):
                    if vitals.total < 200:
                        break
                    run.step({})
                self.assertLess(vitals.total, 200)
                self.assertGreater(owner.last_damage, damage_clock)

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

    def test_winston_dome_filters_reverse_attacks_from_each_actual_npc(self):
        cases = [(key, False) for key in self.catalog] + [("2", True)]
        for key, at_edge in cases:
            definition = self.catalog[key]
            with self.subTest(npc=definition.name, at_edge=at_edge):
                run, zen, bot, _ = self.arena(definition, names=("Zen", "Winston"))
                winston = run.players[1]
                self.assertTrue(run.match.switch_hero(winston, heroes()[WINSTON]))
                winston.team = zen.team
                x, y, z = zen.mover.position
                offset = (4, -2) if key == "4" or at_edge else (2, -1)
                winston.mover.position = [x+offset[0], y, z+offset[1]]
                winston.mover.body = None
                if key == "4":
                    bot.position = (x, y, z+2)
                for _ in range(90):
                    run.step({})
                fight = run.match.combat
                run.step({winston: (0, 0, 0x10)})
                for _ in range(45):
                    dome = fight.barriers.domes.get(winston.body)
                    if dome is not None and dome.hp == 700:
                        break
                    run.step({})
                else:
                    self.fail("Winston did not create the authored dome")
                self.assertLess(math.dist(dome.centre, fight._eye(zen)), 5)
                self.assertGreater(math.dist(dome.centre, bot.position), 5)
                target = next(t for t in fight._targets() if t.owner is zen)
                capsule_reach = max(math.dist(dome.centre, point) for point in target.segment())+target.radius
                if key != "4":
                    self.assertEqual(capsule_reach > 5, at_edge)
                bx, by, bz = bot.position
                aim_at_bot = aim(fight._eye(zen), (bx, by+mover_data(bot.body).stand_height*.5, bz))
                run.step({zen: (*aim_at_bot, 0)})
                self.assertEqual(npc_control.start(run.match, zen), bot.entity)
                health = fight.vitals[zen.body]
                owner = fight.zenyatta.owners[zen.body]
                initial_clock = owner.last_damage
                with patch.object(fight.barriers, "damage", wraps=fight.barriers.damage) as barrier_damage:
                    for _ in range(220):
                        run.step({})
                        if health.total < 200 or dome.hp < 700:
                            break
                self.assertIs(fight.barriers.domes[winston.body], dome)
                if key == "4":
                    self.assertEqual(dome.hp, 700)
                    barrier_damage.assert_not_called()
                    self.assertEqual(health.total, 175)
                    self.assertEqual(fight.pins[bot.entity], zen.body)
                    self.assertGreater(owner.last_damage, initial_clock)
                    continue

                self.assertLess(dome.hp, 700)
                self.assertTrue(all(call.args[0] is dome and call.args[2] == bot.entity
                                    for call in barrier_damage.call_args_list))
                self.assertEqual(health.total, 194 if at_edge else 200)
                self.assertEqual(owner.last_damage == initial_clock, not at_edge)
                protected_health = health.total
                protected_hp = dome.hp
                winston.team = bot.team
                for _ in range(220):
                    run.step({})
                    if health.total < protected_health:
                        break
                self.assertLess(health.total, protected_health)
                self.assertGreater(owner.last_damage, initial_clock)
                self.assertIs(fight.barriers.domes[winston.body], dome)
                self.assertEqual(dome.hp, protected_hp)

    def test_projected_npc_damage_keeps_zen_shield_regen_deadline(self):
        def engage(run, zen, bot):
            x, y, z = bot.position
            center = x, y+mover_data(bot.body).stand_height*.5, z
            run.step({zen: (*aim(run.match.combat._eye(zen), center), 0)})
            self.assertEqual(npc_control.start(run.match, zen), bot.entity)

        for key, definition in self.catalog.items():
            with self.subTest(npc=definition.name):
                run, zen, bot, _ = self.arena(definition, names=('Zen', 'Zarya'))
                zarya = run.players[1]
                self.assertTrue(run.match.switch_hero(zarya, heroes()[ZARYA]))
                zarya.team = zen.team
                x, y, z = zen.mover.position
                zarya.mover.position = [x+4, y, z]
                zarya.mover.body = None
                if key == '4':
                    bot.position = (x, y, z+2)
                fight = run.match.combat
                target = next(target for target in fight._targets() if target.owner is zen)
                vitals = fight.vitals_of(target)
                owner = fight.zenyatta.owner(zen)

                engage(run, zen, bot)
                for _ in range(250):
                    run.step({})
                    if vitals.total < 200:
                        break
                self.assertLess(vitals.total, 200)
                damaged_at = run.frame
                clock = owner.last_damage
                health = vitals.now[combat.HEALTH_POOL]
                shields = vitals.now[combat.SHIELDS_POOL]
                self.assertEqual(clock, fight.now)
                self.assertEqual(npc_control.stop(run.match), 1)
                for _ in range(24):
                    run.step({})

                if key == '3':
                    # Give the authored scope a head start so the shot reaches the bubble.
                    engage(run, zen, bot)
                    for _ in range(32):
                        run.step({})
                direction = aim(fight._eye(zarya), fight._eye(zen))
                run.step({zarya: (*direction, 16)})
                bubble_key = zarya.body, PROJECTED
                bubble = fight.barriers.bubbles[bubble_key]
                self.assertEqual((bubble.target, bubble.hp), (zen.body, 200))
                if key != '3':
                    engage(run, zen, bot)
                while run.frame < damaged_at+187 and bubble.hp == 200:
                    run.step({})
                self.assertLess(bubble.hp, 200)
                self.assertIn(bubble_key, fight.barriers.bubbles)
                self.assertEqual(owner.last_damage, clock)
                self.assertEqual(vitals.now[combat.SHIELDS_POOL], shields)
                self.assertEqual(npc_control.stop(run.match), 1)

                while run.frame < damaged_at+187:
                    run.step({})
                self.assertEqual(vitals.now[combat.SHIELDS_POOL], shields)
                run.step({})
                self.assertAlmostEqual(vitals.now[combat.SHIELDS_POOL], shields+.24, places=5)
                self.assertEqual(vitals.now[combat.HEALTH_POOL], health)
                self.assertEqual(owner.last_damage, clock)
                self.assertNotIn(bubble_key, fight.barriers.bubbles)

                engage(run, zen, bot)
                for _ in range(250):
                    previous = vitals.total
                    run.step({})
                    if owner.last_damage > clock:
                        break
                self.assertGreater(owner.last_damage, clock)
                self.assertEqual(owner.last_damage, fight.now)
                self.assertLess(vitals.total, previous)

    def test_enforcer_controller_gate_controls_actual_shell_and_ammunition(self):
        for components, ready in (
            (None, False), ((), False), ((75,), True), ((92,), False),
            ((75, 92), False), ((43, 44, 75), True),
        ):
            with self.subTest(components=components):
                # Supply controller data at creation. The original weapon graph
                # still owns its readiness branches, ammunition, and shot.
                with patch.object(npc_context, "manual_components", return_value=components):
                    run, zen, bot, _ = self.arena(self.catalog["2"])
                component = bot.body_script.component
                self.assertEqual(component.world.controller_components, components)
                weapon, = (i for i in component.instances.values() if i.graph.index == 0x13EB)
                fight = run.match.combat
                target = next(t for t in fight._targets() if t.owner is zen)
                vitals = fight.vitals_of(target)
                self.assertEqual(npc_control.start(run.match, zen), bot.entity)
                peak = 0
                for _ in range(140):
                    run.step({})
                    peak = max(peak, len(fight.rockets))
                    if vitals.total < 200:
                        for _ in range(8):
                            run.step({})
                        break
                self.assertEqual(peak, 25 if ready else 0)
                self.assertEqual(vitals.total, 172 if ready else 200)
                self.assertEqual(weapon.vars[53].value(), 3 if ready else 4)

    def test_enforcer_authored_shield_wait_rearms_on_real_zen_hits_and_recovers(self):
        run, zen, bot, vitals = self.arena(self.catalog["2"])
        component = bot.body_script.component
        graph, = (i for i in component.instances.values() if i.graph.index == 0x0E62)
        shields = combat.SHIELDS_POOL

        def fire(expected):
            run.step({zen: (0, 600, FIRE)})
            for _ in range(25):
                run.step({})
                if vitals.now[shields] == expected:
                    break
            self.assertEqual(vitals.now[shields], expected)

        fire(52)
        wait = graph.states[graph.graph.node(52).state]
        self.assertTrue(wait.active)
        self.assertEqual(wait.ms, 3000)
        first_deadline = wait.start + wait.ms
        for _ in range(80):
            run.step({})
        fire(4)
        deadline = wait.start + wait.ms
        self.assertGreater(deadline, first_deadline)
        while component.now + 16 < deadline:
            run.step({})
            self.assertEqual(vitals.now[shields], 4)
        for _ in range(3):
            run.step({})
            if vitals.now[shields] > 4:
                break
        self.assertFalse(wait.active)
        self.assertGreater(component.now, deadline)
        self.assertAlmostEqual(vitals.now[shields], 4 + .03*(component.now-deadline), places=5)
        regen = graph.states[graph.graph.node(51).state]
        self.assertTrue(regen.active)
        charge = zen.body_script.component.entity_var(342).value()
        first_regen_frame = run.frame
        for _ in range(220):
            run.step({})
        self.assertEqual(vitals.now[shields], 100)
        self.assertEqual(vitals.now[combat.HEALTH_POOL], 250)
        self.assertFalse(regen.active)
        self.assertAlmostEqual(zen.body_script.component.entity_var(342).value()-charge,
                               (run.frame-first_regen_frame)*.08, places=2)

    def test_npc_shield_wait_and_recovery_keep_time_across_skipped_updates(self):
        for key, skipped in product(("2", "3", "4"), (False, True)):
            with self.subTest(npc=key, skipped=skipped):
                tier = None if key == "2" else 3
                run, zen, bot, vitals = self.arena(self.catalog[key], difficulty_index=tier)
                component = bot.body_script.component
                recovery, = (i for i in component.instances.values() if i.graph.index == 0x0E62)
                shields = combat.SHIELDS_POOL
                wounded_shield = vitals.pool_maximum(shields)-48
                starting_health = vitals.pool_total(combat.HEALTH_POOL)
                self.assertGreater(wounded_shield, 0)
                run.step({zen: (0, 600, FIRE)})
                for _ in range(25):
                    run.step({})
                    if vitals.pool_total(shields) == wounded_shield:
                        break
                self.assertEqual(vitals.pool_total(shields), wounded_shield)
                wait = recovery.states[recovery.graph.node(52).state]
                self.assertTrue(wait.active)
                deadline = wait.start+wait.ms
                for _ in range(195):
                    if component.now+32 >= deadline:
                        break
                    run.step({})
                self.assertTrue(wait.active)
                self.assertEqual(vitals.pool_total(shields), wounded_shield)
                self.assertLess(component.now, deadline)
                self.assertLessEqual(deadline-component.now, 32)

                charge = zen.body_script.component.entity_var(342)
                charge_before = charge.value()
                if skipped:
                    run.frame += 11
                    run.step({})
                else:
                    for _ in range(12):
                        run.step({})
                self.assertFalse(wait.active)
                regen = recovery.states[recovery.graph.node(51).state]
                self.assertTrue(regen.active)
                self.assertEqual(regen.start, deadline)
                self.assertAlmostEqual(vitals.pool_total(shields),
                                       wounded_shield+.03*(component.now-deadline), places=4)
                self.assertEqual(vitals.pool_total(combat.HEALTH_POOL), starting_health)
                self.assertAlmostEqual(charge.value()-charge_before, 12*.08, places=3)

    def test_enforcer_projected_shield_interception_does_not_rearm_recovery(self):
        run, zen, bot, vitals = self.arena(self.catalog["2"], names=("Zen", "Zarya"))
        zarya = run.players[1]
        self.assertTrue(run.match.switch_hero(zarya, heroes()[ZARYA]))
        zarya.team = bot.team
        x, y, z = bot.position
        zarya.mover.position = [x+4, y, z]
        zarya.mover.body = None
        fight = run.match.combat
        component = bot.body_script.component
        graph, = (i for i in component.instances.values() if i.graph.index == 0x0E62)
        run.step({zen: (0, 600, FIRE)})
        for _ in range(45):
            run.step({})
        self.assertEqual(vitals.now[combat.SHIELDS_POOL], 52)
        wait = graph.states[graph.graph.node(52).state]
        start = wait.start
        angles = aim(fight._eye(zarya), (x, y+1, z))
        run.step({zarya: (*angles, 16)})
        bubble = fight.barriers.bubbles[zarya.body, PROJECTED]
        self.assertEqual(bubble.target, bot.entity)
        run.step({zen: (0, 600, FIRE)})
        for _ in range(25):
            run.step({})
        self.assertEqual(bubble.hp, 152)
        self.assertEqual(vitals.now[combat.SHIELDS_POOL], 52)
        self.assertTrue(wait.active)
        self.assertEqual(wait.start, start)

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

    def test_actual_enforcer_shell_deflects_back_and_arms_shield_recovery(self):
        for front in (False, True):
            with self.subTest(front=front):
                run, zen, bot, hp = self.arena(self.catalog["2"], names=("Zen", "Genji"))
                fight = run.match.combat
                genji = run.players[1]
                self.assertTrue(run.match.switch_hero(genji, heroes()[0x02E0000000000029]))
                genji.team = zen.team
                x, y, z = zen.mover.position
                genji.mover.position = [x + 4, y, z + 3]
                genji.mover.body = None
                bot.position = (x, y, z + 8)
                for _ in range(20):
                    run.step({})
                self.assertEqual(npc_control.start(run.match, zen), bot.entity)
                genji.mover.position = [x, y, z + 3]
                genji.mover.body = None
                direction = aim(fight._eye(genji), (x, y + 1, z + 8)) if front else (-32768, 0)
                guard = {genji: (*direction, 0x10)}
                component = bot.body_script.component
                weapon = next(i for i in component.instances.values() if i.graph.index == 0x13EB)
                recovery = next(i for i in component.instances.values() if i.graph.index == 0x0E62)
                wait_node = recovery.graph.node(52)
                zen_charge = zen.body_script.component.entity_var(342)
                for _ in range(90):
                    run.step(guard)
                    if fight.rockets:
                        break
                shell = [pellet for pellet in fight.rockets if pellet.shooter is bot]
                self.assertEqual(len(shell), 25)
                self.assertTrue(all(pellet.direct.amount == 2 for pellet in shell))
                self.assertEqual(weapon.vars[53].value(), 3)
                for _ in range(35):
                    run.step(guard)
                    if not fight.rockets:
                        break
                self.assertFalse(fight.rockets)
                self.assertEqual(weapon.state(14).counter, 1)
                self.assertEqual(fight.vitals[zen.body].total, 200)
                hits = [message["+0x78"] for message in genji.client.messages(combat.HIT)]
                reflected = sum(pellet.shooter is genji for pellet in shell)
                if front:
                    self.assertGreater(reflected, 0)
                    self.assertEqual(len(hits), reflected)
                    self.assertEqual(fight.vitals[genji.body].total, 200)
                    self.assertEqual([(hit["+0x0"]["+0x0"], hit["+0x4"]) for hit in hits],
                                     [(bot.entity, 2)] * reflected)
                    self.assertEqual(genji.stats.value(stats.HERO_DAMAGE), 2 * reflected)
                    self.assertEqual(hp.now[combat.SHIELDS_POOL], 100 - 2 * reflected)
                    wait = recovery.states[wait_node.state]
                    self.assertTrue(wait.active)
                    self.assertEqual(wait.ms, 3000)
                    first_deadline = wait.start + wait.ms
                    self.assertEqual(npc_control.stop(run.match), 1)
                    before = zen_charge.value()
                    start_frame = run.frame
                    run.step({zen: (*aim(fight._eye(zen), (x, y + 1, z + 8)), FIRE)})
                    for _ in range(20):
                        if zen.client.messages(combat.HIT):
                            break
                        run.step({})
                    hit, = zen.client.messages(combat.HIT)
                    self.assertEqual(hit["+0x78"]["+0x0"]["+0x0"], bot.entity)
                    self.assertEqual(hit["+0x78"]["+0x4"], 48)
                    self.assertEqual(hp.now[combat.SHIELDS_POOL], 52 - 2 * reflected)
                    self.assertGreater(wait.start + wait.ms, first_deadline)
                    self.assertAlmostEqual(zen_charge.value() - before, 48 + (run.frame - start_frame) * .08,
                                           places=4)
                else:
                    self.assertEqual(reflected, 0)
                    self.assertFalse(hits)
                    self.assertLess(fight.vitals[genji.body].total, 200)
                    self.assertEqual(hp.now[combat.SHIELDS_POOL], 100)
                    self.assertFalse(recovery.states.get(wait_node.state))
                self.assertEqual(hp.now[combat.HEALTH_POOL], 250)

    def test_enforcer_pellets_sample_transcendence_after_the_volley_launches(self):
        for index in range(4):
            with self.subTest(index=index):
                run, zen, bot, _ = self.arena(self.catalog["2"])
                fight = run.match.combat
                owner = fight.zenyatta.owners[zen.body]
                damage_clock = owner.last_damage
                bot.body_script.component.set(9948, index)
                self.assertEqual(npc_control.start(run.match, zen), bot.entity)
                script = zen.body_script
                script.set_ult_charge(script.component.entity_var(338).value())
                for _ in range(90):
                    run.step({})
                    airborne = [rocket for rocket in fight.rockets if rocket.shooter is bot]
                    if airborne:
                        break
                self.assertEqual(len(airborne), 25)
                self.assertEqual(fight.vitals[zen.body].total, 200)
                self.assertFalse(fight.zenyatta.ultimate(zen))
                run.step({zen: (0, 0, 0x20)})
                self.assertTrue(fight.zenyatta.ultimate(zen))
                self.assertEqual(script.component.entity_var(342).value(), 0)

                with patch.object(fight, "_damage", wraps=fight._damage) as observed:
                    for _ in range(25):
                        run.step({})
                        self.assertEqual(fight.vitals[zen.body].total, 200)
                        self.assertEqual(owner.last_damage, damage_clock)
                attempts = [call for call in observed.call_args_list
                            if call.args[0] is bot and call.args[1].owner is zen]
                self.assertEqual(len(attempts), 14)
                self.assertTrue(all(call.args[2] == 2*(index+1) for call in attempts))
                weapon = next(i for i in bot.body_script.component.instances.values() if i.graph.index == 0x13EB)
                self.assertEqual(weapon.state(14).counter, 1)
                self.assertEqual(weapon.vars[53].value(), 3)

    def test_enforcer_pellet_impacts_straddle_transcendence_activation(self):
        run, zen, bot, _ = self.arena(self.catalog["2"])
        fight = run.match.combat
        self.assertEqual(npc_control.start(run.match, zen), bot.entity)
        script = zen.body_script
        script.set_ult_charge(script.component.entity_var(338).value())
        for _ in range(90):
            run.step({})
            if len(fight.rockets) == 25:
                break
        else:
            self.fail("Enforcer did not launch its full shell")
        for _ in range(5):
            run.step({})
        hp = fight.vitals[zen.body]
        owner = fight.zenyatta.owners[zen.body]
        self.assertEqual(hp.total, 200)
        self.assertEqual(len(fight.rockets), 25)

        damage = fight._damage
        hits = []

        def observe(source, target, amount, *args, **kwargs):
            before = hp.total
            immune = fight.zenyatta.ultimate(zen)
            result = damage(source, target, amount, *args, **kwargs)
            if source is bot and target.owner is zen:
                hits.append((run.frame, immune, amount, before-hp.total))
            return result

        with patch.object(fight, "_damage", side_effect=observe):
            run.step({zen: (0, 0, 0x20)})
            first_frame = run.frame
            self.assertEqual(hits, [(first_frame, False, 2, 2)]*12)
            first_damage_time = owner.last_damage
            self.assertEqual(first_damage_time, fight.now)
            self.assertTrue(fight.zenyatta.ultimate(zen))
            self.assertEqual(script.component.entity_var(342).value(), 0)
            run.step({})
            self.assertEqual(hits[12:], [(first_frame+1, True, 2, 0)]*2)
        self.assertEqual(hp.total, 200)
        self.assertEqual(owner.last_damage, first_damage_time)

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
