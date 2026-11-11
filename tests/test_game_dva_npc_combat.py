"""Actual NPC scripts/manual primary inputs; autonomous controllers/specials OPEN."""

import math
import os
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from test_game_combat import KANEZAKA_DM, Run, aim, dva_pilot_input, in_world
from test_game_mover import FLOOR
from test_game_mover import wall as collision_wall

from ow174.game import combat, npc, npc_control, stats
from ow174.game.collision import World
from ow174.game.content import find_hero
from ow174.game.mover import STEP, defined_mover_data
from ow174.game.script import graph
from ow174.game.script.driver import DEFAULT_SLOT, OWNER_HEALTH, ULT_CHARGE, ULT_COST, WEAPON_SLOTS

MECH = 0x0400000000000407
PRIMARY = {"Trooper": (20, 15, 18), "Sniper": (100, 95, 110), "Heavy Assault": (10, 5, 18)}


class NpcIncomingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        path = os.environ.get("ORPHEUS_PVE_NPC_CATALOG")
        if not path:
            raise unittest.SkipTest("requires external actual NPC catalog/graphs/movers; acceptance OPEN")
        cls.catalog = npc.load_catalog(Path(path))
        cls.by_name = {row.name: row for row in cls.catalog.values()}
        if set(cls.by_name) != {"Trooper", "Enforcer", "Sniper", "Assassin", "Heavy Assault"}:
            raise ValueError("requires actual five-NPC catalog")
        if any(graph.entity_body(row.body) is None or defined_mover_data(row.body) is None
               for row in cls.catalog.values()) or graph.entity_body(MECH) is None:
            raise unittest.SkipTest("requires actual NPC and0407 graph/mover supplements; no fallback")

    def scenario(self, mech, name, case="hit"):
        run = Run(KANEZAKA_DM, ("DVa",))
        match = run.match
        match.bots.clear()
        match._pending_bot_scripts.clear()
        player = run.players[0]
        hero = find_hero("dva")
        if mech:
            hero = replace(hero, body=MECH)
        self.assertTrue(match.switch_hero(player, hero))
        dva_pilot_input(player)
        player.mover.position = [0, 1, 6]
        player.mover.body = None
        for _ in range(100):
            run.step({})
        if case == "wall":
            match.collision_world = World(FLOOR + collision_wall(-10, 3, 10, 3))
            player.mover.use(world=match.collision_world)
        if case == "ally":
            match.game_map = replace(match.game_map, free_for_all=False)
        bot = npc.spawn(match, self.by_name[name], (0, 1, 0),
                        team=player.team if case == "ally" else 1,
                        yaw=16384 if case == "miss" else 0)
        self.assertIsNotNone(bot.body_script)
        button = bot.body_script.component.world.ai_weapon_button(0)
        self.assertEqual(button, 36 if name == 'Assassin' else 20)
        bot.firing = True
        bot.body_script.component.edges([(button, True)])
        target = next(t for t in match.combat._targets() if t.owner is player)
        return run, player, bot, match.combat.vitals_of(target)

    def first_loss(self, run, vitals, maximum=300):
        before = vitals.total
        for frame in range(1, maximum + 1):
            run.step({})
            if vitals.total < before:
                return frame
        return None

    def test_boosters_hit_each_actual_npc_once(self):
        from test_game_dva_boosters import AuthoredBoostersTests

        case = AuthoredBoostersTests()
        for name, definition in self.by_name.items():
            with self.subTest(npc=name):
                run, mech, victim, _ = case.scenario()
                run.match.collision_world = World(FLOOR + collision_wall(-10, 5.5, 10, 5.5))
                victim.mover.position[0] = 20
                victim.mover.body = None
                bot = npc.spawn(run.match, definition, (0, 1, 4), team=1)
                target = next(t for t in run.match.combat._targets() if t.owner is bot)
                vitals = run.match.combat.vitals_of(target)
                armoured = vitals.now[combat.ARMOUR_POOL] > 0 and not vitals.now[combat.SHIELDS_POOL]
                expected = 5 if armoured else 10
                before = vitals.total
                case.step(run, mech, 3, 8)
                self.assertGreater(bot.position[1], 1)
                self.assertGreater(bot.position[2], 4)
                self.assertGreater(bot.velocity[2], 15)
                case.step(run, mech, 32, 8)
                self.assertLess(bot.position[2], 5.5)
                self.assertEqual(before - vitals.total, expected)
                self.assertEqual(len(mech.client.messages(25001)), 1)

    def test_controlled_boosters_filter_cover_team_and_lateral_miss_for_all_five_npcs(self):
        for case in ("wall", "ally", "miss"):
            with self.subTest(case=case):
                self.check_controlled_booster_npc_filter(case)

    def check_controlled_booster_npc_filter(self, case):
        from test_game_dva_created_body import CreatedBodyTests
        from test_game_mover import box

        run, pilot, other, _, mech = CreatedBodyTests().scenario()
        other.spawned = False
        match = run.match
        fight = match.combat
        match.game_map = replace(match.game_map, free_for_all=False)
        for _ in range(100):
            run.step({})
        x, y, z = mech.position
        if case == "wall":
            match.collision_world = World(
                box(x - 20, x + 20, 0, y, z - 20, z + 20)
                + collision_wall(x - 6, z + 3, x + 6, z + 3, y, y + 6)
            )
        position = (x + 3 if case == "miss" else x, y, z + 4)
        team = pilot.team if case == "ally" else pilot.team + 1
        targets = []
        for definition in self.by_name.values():
            bot = npc.spawn(match, definition, position, team=team)
            health = fight.vitals[bot.entity]
            targets.append((bot, health, dict(health.now)))
        run.step({pilot: (0, 0, 8)})
        for _ in range(34):
            run.step({})
        self.assertIs(pilot.controlled_mech, mech)
        self.assertGreater(mech.position[2], z + 1)
        if case == "wall":
            self.assertLess(mech.position[2], z + 3)
        else:
            self.assertGreater(mech.position[2], z + 4)
        for bot, health, before in targets:
            with self.subTest(npc=bot.name):
                self.assertEqual(health.now, before)
                self.assertEqual(bot.position, position)
                self.assertEqual(bot.velocity, (0, 0, 0))
                self.assertIsNone(bot.impulse_mover)
        self.assertFalse(pilot.client.messages(combat.HIT))

    def test_lethal_booster_contact_leaves_no_npc_impulse(self):
        from test_game_dva_boosters import AuthoredBoostersTests

        case = AuthoredBoostersTests()
        run, mech, victim, _ = case.scenario()
        victim.mover.position[0] = 20
        victim.mover.body = None
        bot = npc.spawn(run.match, self.by_name["Trooper"], (0, 1, 4), team=1)
        run.match.combat.vitals[bot.entity] = combat.Vitals(bot.entity, 10)
        case.step(run, mech, 8, 8)
        self.assertTrue(bot.dead)
        self.assertIsNone(bot.impulse_mover)
        self.assertEqual(bot.position, (0, 1, 4))

    def test_self_destruct_hits_all_five_actual_npcs_in_one_graph_iteration(self):
        from test_game_dva_mech import AuthoredMechTests

        case = AuthoredMechTests()
        run, mech, victim, _ = case.scenario()
        victim.mover.position[0] = 25
        victim.mover.body = None
        bots = [npc.spawn(run.match, row, (0, 1, 3), team=1) for row in self.by_name.values()]
        starting = {bot.entity: run.match.combat.vitals[bot.entity].total for bot in bots}
        script = mech.body_script
        ability, = script.ultimates
        script.set_ult_charge(script.component.entity_var(ULT_COST).value())
        case.step(run, mech, 1, 32)
        case.step(run, mech, 186)
        self.assertTrue(all(not bot.dead for bot in bots))
        case.step(run, mech, 1)
        self.assertEqual({target.id for target in ability.instance.vars[375].value()},
                         {bot.entity for bot in bots})
        self.assertEqual(ability.instance.state(7).counter, 5)
        for bot in bots:
            remaining = max(0, starting[bot.entity] - 1000)
            self.assertEqual(run.match.combat.vitals[bot.entity].total, remaining, bot.name)
            self.assertEqual(bot.dead, remaining == 0, bot.name)

    def test_micro_missiles_respect_cover_and_team_for_all_five_npcs(self):
        for name, definition in self.by_name.items():
            for covered in (False, True):
                with self.subTest(npc=name, covered=covered):
                    self.check_micro_missile_npc_filter(definition, covered=covered)

    def check_micro_missile_npc_filter(self, definition, *, covered):
        from test_game_combat import aim
        from test_game_dva_created_body import CreatedBodyTests
        from test_game_mover import box

        run, pilot, enemy, _, mech = CreatedBodyTests().scenario()
        match = run.match
        fight = match.combat
        match.game_map = replace(match.game_map, free_for_all=False)
        enemy.team = pilot.team + 1
        x, y, z = mech.position
        enemy.mover.position[:] = (x, y, z + 3)
        enemy.mover.body = None
        for _ in range(100):
            run.step({})
        if covered:
            match.collision_world = World(
                box(x - 20, x + 20, 0, y, z - 20, z + 20)
                + collision_wall(x - 6, z + 1.5, x + 6, z + 1.5, y, y + 6)
            )
        bot = npc.spawn(match, definition, (x, y, z + 2.5), team=enemy.team if covered else pilot.team)
        health = fight.vitals[bot.entity]
        before = dict(health.now)
        enemy_health = fight.vitals_of(fight.player_target(enemy))
        enemy_before = enemy_health.total
        yaw, pitch = aim(fight._eye(mech), (x, y + 1.1, z + 3))
        with patch.object(fight, "_shoot", wraps=fight._shoot) as shoot:
            run.step({pilot: (yaw, pitch, 16)})
            for _ in range(175):
                run.step({})
            self.assertEqual(sum(call.args[0] is mech for call in shoot.call_args_list), 18)
        self.assertEqual(health.now, before)
        self.assertFalse(fight.rockets)
        hits = pilot.client.messages(combat.HIT)
        if covered:
            self.assertEqual(enemy_health.total, enemy_before)
            self.assertFalse(hits)
        else:
            self.assertEqual(enemy_health.total, enemy_before - 126)
            self.assertEqual(len(hits), 36)
            self.assertEqual({hit["+0x78"]["+0x0"]["+0x0"] for hit in hits}, {enemy.body})

    def test_micro_missile_splash_rings_and_cover_for_all_five_npcs(self):
        for name, definition in self.by_name.items():
            with self.subTest(npc=name):
                self.check_micro_missile_splash_rings(definition)

    def check_micro_missile_splash_rings(self, definition):
        from test_game_combat import aim
        from test_game_dva_created_body import CreatedBodyTests
        from test_game_mover import box

        run, pilot, enemy, _, mech = CreatedBodyTests().scenario()
        fight = run.match.combat
        x, y, z = mech.position
        enemy.mover.position[:] = (x, y, z + 3)
        enemy.mover.body = None
        for _ in range(100):
            run.step({})
        charge = mech.body_script.component.entity_var(ULT_CHARGE)
        yaw, pitch = aim(fight._eye(mech), (x, y + .85, z + 3))
        run.step({pilot: (yaw, pitch, 16)})
        for _ in range(30):
            if fight.rockets:
                break
            run.step({pilot: (yaw, pitch, 0)})
        rocket, = fight.rockets
        self.assertEqual((rocket.direct.amount, rocket.blast.amount), (3, 4))
        (inner, full), (outer, floor) = rocket.blast.rings
        self.assertAlmostEqual(inner, .1)
        self.assertEqual((full, outer, floor), (1., 1.5, .25))

        direct = fight.player_target(enemy)
        contact = combat.ray_capsule(rocket.position, rocket.direction, *direct.segment(), direct.radius)
        self.assertIsNotNone(contact)
        self.assertAlmostEqual(rocket.collision_radius, .1)
        padded = combat.ray_capsule(
            rocket.position, rocket.direction, *direct.segment(), direct.radius + rocket.collision_radius,
        )
        self.assertIsNotNone(padded)
        step = rocket.speed * combat.TICK
        self.assertEqual(int(padded / step), int(contact / step))
        self.assertLess(padded + rocket.collision_radius, contact)
        self.assertEqual(rocket.blast.splash(.05), 1)
        impact_x, impact_y, impact_z = (
            start + axis * contact for start, axis in zip(rocket.position, rocket.direction, strict=True)
        )
        world = World(
            box(x - 10, x + 10, y - 1, y, z - 10, z + 10)
            + collision_wall(x - .65, impact_z - .5, x - .65, impact_z + .5, y, y + 5)
        )
        run.match.collision_world = world
        enemy.mover.use(world=world)
        targets = {"direct": direct}
        distances = {"inner_edge": .105, "ramp": .8, "outer": 1.49, "beyond": 1.51, "covered": .8}
        for label, distance in distances.items():
            bot = npc.spawn(run.match, definition, (impact_x, y, impact_z), team=enemy.team)
            body = next(target for target in fight._targets() if target.owner is bot)
            a, b = body.segment()
            vertical = max(a[1] - impact_y, impact_y - b[1], 0.)
            horizontal = math.sqrt((distance + body.radius) ** 2 - vertical ** 2)
            if label == "inner_edge":
                # A lateral inner-ring witness can take the first direct hit
                # with the authored projectile radius.
                bot.position = (impact_x, y, impact_z + horizontal)
            else:
                side = -horizontal if label == "covered" else horizontal
                bot.position = (impact_x + side, y, impact_z)
            body = next(target for target in fight._targets() if target.owner is bot)
            side_contact = combat.ray_capsule(rocket.position, rocket.direction, *body.segment(), body.radius)
            self.assertTrue(side_contact is None or side_contact > contact, label)
            expanded = combat.ray_capsule(
                rocket.position, rocket.direction, *body.segment(), body.radius + rocket.collision_radius,
            )
            self.assertTrue(expanded is None or expanded > contact, label)
            targets[label] = body

        health = {label: fight.vitals_of(body) for label, body in targets.items()}
        before = {label: pool.total for label, pool in health.items()}
        apply_damage = fight._damage
        damage_charge = []

        def measure_damage_charge(*args, **kwargs):
            previous = charge.value()
            dealt = apply_damage(*args, **kwargs)
            damage_charge.append(charge.value() - previous)
            return dealt

        with patch.object(fight, "_damage", side_effect=measure_damage_charge) as damage:
            for _ in range(12):
                run.step({})
                if rocket not in fight.rockets:
                    break
        self.assertNotIn(rocket, fight.rockets)
        self.assertAlmostEqual(rocket.flown * rocket.speed, contact, places=5)
        raw = {body.entity: [] for body in targets.values()}
        for call in damage.call_args_list:
            self.assertIs(call.args[0], mech)
            raw[call.args[1].entity].append(call.args[2])
        expected = {"direct": [3, 4], "inner_edge": [4 * (1 - .75 * (.105 - .1) / 1.4)], "ramp": [2.5],
                    "outer": [4 * (1 - .75 * (1.49 - .1) / (1.5 - .1))], "beyond": [], "covered": []}
        for label, body in targets.items():
            amounts = raw[body.entity]
            self.assertEqual(len(amounts), len(expected[label]), label)
            for actual, want in zip(amounts, expected[label], strict=True):
                self.assertAlmostEqual(actual, want, delta=.005, msg=label)
            self.assertEqual(health[label].total < before[label], bool(amounts), label)
        effective = sum(before[label] - pool.total for label, pool in health.items())
        hits = [hit["+0x78"] for hit in pilot.client.messages(combat.HIT)]
        self.assertEqual(len(hits), 5)
        self.assertEqual({hit["+0x0"]["+0x0"] for hit in hits},
                         {targets[label].entity for label in ("direct", "inner_edge", "ramp", "outer")})
        self.assertAlmostEqual(sum(damage_charge), effective, places=4)

    def test_detached_initial_mech_self_destruct_hits_all_five_actual_npcs(self):
        from test_game_dva_created_body import CreatedBodyTests

        run, pilot, _, _, mech = CreatedBodyTests().scenario()
        match = run.match
        for _ in range(100):
            run.step({})
        x, y, z = mech.position
        targets = [npc.spawn(match, row, (x, y, z + 3), team=1) for row in self.by_name.values()]
        starting = {bot.entity: match.combat.vitals[bot.entity].total for bot in targets}
        component = mech.body_script.component
        component.set(ULT_CHARGE, component.entity_var(ULT_COST).value())
        run.step({pilot: (0, 0, 32)})
        for _ in range(187):
            run.step({})
        self.assertIsNone(pilot.controlled_mech)
        self.assertTrue(mech.detached_bomb)
        for bot in targets:
            self.assertEqual(match.combat.vitals[bot.entity].total, max(0, starting[bot.entity] - 1000))

    def test_detached_self_destruct_credits_all_five_npc_kills_and_player_to_pilot(self):
        from test_game_dva_created_body import CreatedBodyTests

        run, pilot, enemy, _, mech = CreatedBodyTests().scenario()
        match, fight = run.match, run.match.combat
        x, y, z = mech.position
        enemy.mover.position[:] = (x + 3, y, z)
        enemy.mover.body = None
        for _ in range(100):
            run.step({})
        targets = {name: npc.spawn(match, definition, (x, y, z + 2), team=enemy.team)
                   for name, definition in self.by_name.items()}
        heavy = targets["Heavy Assault"]
        fight.vitals[heavy.entity] = combat.Vitals(heavy.entity, 750)
        fight.vitals[enemy.body] = combat.Vitals(enemy.body, 750)
        victims = [bot.entity for bot in targets.values()] + [enemy.body]
        effective_damage = sum(fight.vitals[entity].total for entity in victims)
        component = mech.body_script.component
        component.set(ULT_CHARGE, component.entity_var(ULT_COST).value())
        run.step({pilot: (0, 0, 32)})
        for _ in range(174):
            run.step({})
        assassin = targets["Assassin"]
        npc_control.engage(match, assassin, pilot)
        for _ in range(12):
            run.step({})
        self.assertEqual(fight.pins[assassin.entity], pilot.body)
        self.assertTrue(fight.is_stunned(pilot))
        run.step({})
        self.assertTrue(mech.detached_bomb)
        self.assertIsNone(pilot.controlled_mech)
        self.assertNotIn(assassin.entity, fight.pins)
        self.assertFalse(fight.is_stunned(pilot))
        for entity in victims:
            health = fight.vitals[entity]
            self.assertTrue(health.dead)
            self.assertEqual(health.killer, mech.entity)
        for bot in targets.values():
            self.assertTrue(bot.dead)
            self.assertIsNotNone(bot.body_script.death_frame)
            self.assertIsNone(bot.impulse_mover)
        hits = pilot.client.messages(combat.HIT)
        self.assertCountEqual([hit["+0x78"]["+0x0"]["+0x0"] for hit in hits], victims)
        self.assertTrue(all(hit["+0x78"]["+0x8"] == combat.KILLING for hit in hits))
        self.assertEqual(enemy.client.messages(combat.DEATH_NOTICE)[-1]["+0x7C"], {"+0x0": mech.entity})
        for _ in range(25):
            run.step({})
        self.assertNotIn(mech, match.bots)
        self.assert_eliminations(pilot, mech.entity, victims)
        self.assertEqual(pilot.stats.value(stats.HERO_DAMAGE), effective_damage)
        self.assertEqual(enemy.stats.value(stats.DEATHS), 1)

    def assert_eliminations(self, player, source, victims):
        notices = player.client.messages(combat.KILL_NOTICE)
        self.assertCountEqual([item["+0x84"]["+0x0"] for item in notices], victims)
        self.assertTrue(all(item["+0x80"] == {"+0x0": source} for item in notices))
        self.assertEqual(player.stats.value(stats.ELIMINATIONS), len(victims))

    def test_controlled_fusion_credits_each_npc_kill_to_pilot(self):
        from test_game_dva_created_body import CreatedBodyTests

        for name, definition in self.by_name.items():
            with self.subTest(npc=name):
                run, pilot, other, _, mech = CreatedBodyTests().scenario()
                other.spawned = False
                match, fight = run.match, run.match.combat
                x, y, z = mech.position
                bot = npc.spawn(match, definition, (x, y, z + 2), team=other.team)
                for _ in range(100):
                    run.step({})
                health = combat.Vitals(bot.entity, 1)
                fight.vitals[bot.entity] = health
                angles = aim(fight._eye(mech), (x, y + .75, z + 2))
                run.step({pilot: (*angles, 1)})
                self.assertTrue(bot.dead)
                self.assertEqual(health.killer, mech.entity)
                self.assertIsNotNone(bot.body_script.death_frame)
                for _ in range(10):
                    run.step({})
                self.assert_eliminations(pilot, mech.entity, [bot.entity])
                self.assertEqual(pilot.stats.value(stats.HERO_DAMAGE), 1)

    def test_detached_self_destruct_filters_cover_and_range_for_all_five_npcs(self):
        from test_game_dva_created_body import CreatedBodyTests
        from test_game_mover import box

        run, pilot, other, _, mech = CreatedBodyTests().scenario()
        other.spawned = False
        match = run.match
        fight = match.combat
        for _ in range(100):
            run.step({})
        x, y, z = mech.position
        match.collision_world = World(
            box(x - 30, x + 30, 0, y, z - 30, z + 30)
            + collision_wall(x + 1.5, z - 6, x + 1.5, z + 6, y, y + 6)
        )
        targets = []
        for definition in self.by_name.values():
            for case, offset in (("visible", -3), ("covered", 3), ("out_of_range", -25)):
                bot = npc.spawn(match, definition, (x + offset, y, z), team=1)
                health = fight.vitals[bot.entity]
                targets.append((case, bot, health, health.total))
        component = mech.body_script.component
        component.set(ULT_CHARGE, component.entity_var(ULT_COST).value())
        run.step({pilot: (0, 0, 32)})
        for _ in range(187):
            run.step({})
        self.assertTrue(mech.detached_bomb)
        self.assertIsNone(pilot.controlled_mech)
        expected_hits = []
        for case, bot, health, before in targets:
            with self.subTest(case=case, npc=bot.name):
                if case == "visible":
                    self.assertEqual(health.total, max(0, before - 1000))
                    expected_hits.append(bot.entity)
                else:
                    self.assertEqual(health.total, before)
                    self.assertEqual(bot.velocity, (0, 0, 0))
                    self.assertIsNone(bot.impulse_mover)
        hits = pilot.client.messages(combat.HIT)
        self.assertCountEqual([hit["+0x78"]["+0x0"]["+0x0"] for hit in hits], expected_hits)

    def test_detached_self_destruct_filters_allies_but_always_spares_own_pilot(self):
        from test_game_dva_created_body import CreatedBodyTests

        for free_for_all in (False, True):
            with self.subTest(free_for_all=free_for_all):
                run, pilot, enemy, _, mech = CreatedBodyTests().scenario()
                match = run.match
                fight = match.combat
                match.game_map = replace(match.game_map, free_for_all=free_for_all)
                ally = match.add_player(3, "Ally", enemy.hero.guid, pilot.team, False)
                in_world(match, ally)
                run.players.append(ally)
                self.assertTrue(match.switch_hero(ally, enemy.hero))
                x, y, z = mech.position
                players = []
                for player, side in ((ally, -1), (enemy, 1)):
                    player.mover.position[:] = (x + side * 3, y, z)
                    player.mover.body = None
                    health = combat.Vitals(player.body, 5000)
                    fight.vitals[player.body] = health
                    players.append((player, health, side))
                for _ in range(100):
                    run.step({})
                bots = []
                for team, side in ((pilot.team, -1), (enemy.team, 1)):
                    bot = npc.spawn(match, self.by_name["Heavy Assault"], (x + side * 3, y, z), team=team)
                    bots.append((bot, side))
                component = mech.body_script.component
                component.set(ULT_CHARGE, component.entity_var(ULT_COST).value())
                run.step({pilot: (0, 0, 32)})
                for _ in range(187):
                    run.step({})
                self.assertTrue(mech.detached_bomb)
                self.assertIsNone(pilot.controlled_mech)
                self.assertLess(math.dist(mech.position, pilot.mover.position), 4)
                self.assertEqual(fight.vitals_of(fight.player_target(pilot)).total, 150)
                self.assertEqual(pilot.mover.velocity, (0, 0, 0))
                for player, health, side in players:
                    hit = free_for_all or side == 1
                    self.assertEqual(health.total, 4000 if hit else 5000)
                    if hit:
                        self.assertGreater(side * player.mover.velocity[0], 15)
                    else:
                        self.assertEqual(player.mover.velocity, (0, 0, 0))
                for bot, side in bots:
                    hit = free_for_all or side == 1
                    self.assertEqual(fight.vitals[bot.entity].total, 3000 if hit else 4000)
                    self.assertEqual(bot.velocity, (side * 20, 10, 0) if hit else (0, 0, 0))
                expected = {player.body for player, _, side in players if free_for_all or side == 1}
                expected.update(bot.entity for bot, side in bots if free_for_all or side == 1)
                hits = pilot.client.messages(combat.HIT)
                self.assertCountEqual([hit["+0x78"]["+0x0"]["+0x0"] for hit in hits], expected)

    def test_detached_self_destruct_impulse_uses_inner_outer_ring_and_ruleset(self):
        from test_game_dva_created_body import CreatedBodyTests

        for enabled in (True, False):
            with self.subTest(enabled=enabled):
                run, pilot, other, _, mech = CreatedBodyTests().scenario()
                match = run.match
                x, y, z = mech.position
                other.mover.position = [x, y, z + 25]
                other.mover.body = None
                for _ in range(100):
                    run.step({})
                definition = self.by_name["Heavy Assault"]
                core = npc.spawn(match, definition, (x, y, z + 3), team=1)
                outer = npc.spawn(match, definition, (x, y, z + 9), team=1)
                pilot.body_script.component.set(10640, float(enabled))
                component = mech.body_script.component
                component.set(ULT_CHARGE, component.entity_var(ULT_COST).value())
                run.step({pilot: (0, 0, 32)})
                for _ in range(187):
                    run.step({})
                self.assertEqual(match.combat.vitals[core.entity].total, 3000)
                self.assertLess(match.combat.vitals[outer.entity].total, 4000)
                self.assertEqual(core.velocity, (0.0, 10.0, 20.0) if enabled else (0.0, 0.0, 0.0))
                self.assertEqual(outer.velocity, (0.0, 2.5, 15.0) if enabled else (0.0, 0.0, 0.0))

    def test_call_mech_replaces_owned_body_and_hits_player_and_all_five_npcs(self):
        from test_game_dva_created_body import CreatedBodyTests

        run, pilot, enemy, instance, old = CreatedBodyTests().ready_call_mech()
        script = pilot.body_script
        cost = script.component.entity_var(ULT_COST).value()
        self.assertAlmostEqual(cost, 319.2, places=3)
        run.step({pilot: (0, 0, 32)})
        self.assertEqual(script.component.entity_var(ULT_CHARGE).value(), 0)
        for _ in range(24):
            run.step({})
        mech, = run.match.bots
        self.assertIsNot(mech, old)
        self.assertEqual((mech.owner, mech.source_body), (pilot.entity, pilot.body))
        self.assertEqual(run.match.combat.vitals[mech.entity].total, 600)
        self.assertEqual(instance.state(19).counter, 0)
        entry_start = tuple(pilot.mover.position)

        x, y, z = mech.position
        contact = (x + 1.5, y, z)
        enemy.mover.position = list(contact)
        enemy.mover.body = None
        match = run.match
        match.combat.vitals[enemy.body] = combat.Vitals(enemy.body, 1000)
        targets = [npc.spawn(match, row, contact, team=1) for row in self.by_name.values()]
        starting = {bot.entity: match.combat.vitals[bot.entity].total for bot in targets}
        for _ in range(45):
            run.step({})
            if instance.state(20).counter:
                break
        self.assertEqual(instance.state(19).counter, 1)
        self.assertEqual(instance.state(20).counter, 1)
        self.assertEqual(match.combat.vitals[enemy.body].total, 750)
        self.assertGreater(enemy.mover.velocity[0], 20)
        self.assertGreater(enemy.mover.velocity[1], 2)
        self.assertEqual(match.combat.vitals[mech.entity].total, 600)
        for bot in targets:
            expected = max(0, starting[bot.entity] - 250)
            self.assertEqual(match.combat.vitals[bot.entity].total, expected, bot.name)
            if expected:
                self.assertAlmostEqual(math.hypot(bot.velocity[0], bot.velocity[2]), 25, places=4)
                self.assertEqual(bot.velocity[1], 3)
            else:
                self.assertIsNone(bot.impulse_mover)
        for _ in range(55):
            run.step({})
        entry = instance.state(47)
        destination = entry.evaluate(entry.node.field("m_865F3D01"), watch=False)
        self.assertNotEqual(tuple(pilot.mover.position), entry_start)
        self.assertLessEqual(math.dist(pilot.mover.position, destination), STEP)
        self.assertEqual(instance.state(99).counter, 1)

    def test_call_mech_landing_respects_cover_and_zero_impulse_ruleset(self):
        from test_game_dva_created_body import CreatedBodyTests
        from test_game_mover import box

        for covered in (False, True):
            with self.subTest(covered=covered):
                run, pilot, enemy, instance, _ = CreatedBodyTests().ready_call_mech()
                match = run.match
                if not covered:
                    instance.component.set(10876, 0.0)
                run.step({pilot: (0, 0, 32)})
                for _ in range(24):
                    run.step({})
                mech, = match.bots
                x, y, z = mech.position
                enemy.mover.position = [x + 1.8, y, z]
                enemy.mover.body = None
                if covered:
                    match.collision_world = World(
                        box(x - 20, x + 20, 0, y, z - 20, z + 20)
                        + collision_wall(x + 1.1, z - 6, x + 1.1, z + 6)
                    )
                vitals = combat.Vitals(enemy.body, 1000)
                match.combat.vitals[enemy.body] = vitals
                for _ in range(45):
                    run.step({})
                self.assertEqual(instance.state(19).counter, 1)
                self.assertEqual(vitals.total, 1000 if covered else 750)
                self.assertEqual(enemy.mover.velocity, (0.0, 0.0, 0.0))

    def test_call_mech_landing_filters_allies_from_one_player_and_npc_batch(self):
        from test_game_dva_created_body import CreatedBodyTests

        for free_for_all in (False, True):
            with self.subTest(free_for_all=free_for_all):
                run, pilot, enemy, instance, old = CreatedBodyTests().ready_call_mech()
                match = run.match
                fight = match.combat
                match.game_map = replace(match.game_map, free_for_all=free_for_all)
                ally = match.add_player(3, "Ally", enemy.hero.guid, pilot.team, False)
                in_world(match, ally)
                run.players.append(ally)
                self.assertTrue(match.switch_hero(ally, enemy.hero))
                run.step({pilot: (0, 0, 32)})
                for _ in range(24):
                    run.step({})
                mech, = match.bots
                self.assertIsNot(mech, old)
                x, y, z = mech.position
                players = []
                for player, side in ((ally, -1), (enemy, 1)):
                    player.mover.position[:] = (x + side * 1.5, y, z)
                    player.mover.body = None
                    health = combat.Vitals(player.body, 1000)
                    fight.vitals[player.body] = health
                    players.append((player, health, side))
                bots = []
                for team, side in ((pilot.team, -1), (enemy.team, 1)):
                    bot = npc.spawn(match, self.by_name["Heavy Assault"], (x + side * 1.5, y, z), team=team)
                    health = fight.vitals[bot.entity]
                    bots.append((bot, health, health.total, side))
                for _ in range(45):
                    run.step({})
                    if instance.state(20).counter:
                        break
                self.assertEqual(instance.state(20).counter, 1)
                for player, health, side in players:
                    hit = free_for_all or side == 1
                    self.assertEqual(health.total, 750 if hit else 1000)
                    if hit:
                        self.assertGreater(side * player.mover.velocity[0], 20)
                    else:
                        self.assertEqual(player.mover.velocity, (0, 0, 0))
                for bot, health, before, side in bots:
                    hit = free_for_all or side == 1
                    self.assertEqual(before - health.total, 250 if hit else 0)
                    self.assertEqual(bot.impulse_mover is not None, hit)

    def test_call_mech_landing_separates_capsule_damage_range_from_impulse_ring(self):
        from test_game_dva_created_body import CreatedBodyTests

        for direction, offset, knockback in ((1, -.01, True), (1, .01, False),
                                             (-1, -.01, False), (-1, .01, False), (-1, -.1, True)):
            with self.subTest(direction=direction, offset=offset):
                run, pilot, enemy, instance, old = CreatedBodyTests().ready_call_mech()
                fight = run.match.combat
                run.step({pilot: (0, 0, 32)})
                for _ in range(24):
                    run.step({})
                mech, = run.match.bots
                self.assertIsNot(mech, old)
                x, y, z = mech.position
                radius = fight.player_target(enemy).radius
                distance = 2.5 + radius + offset
                enemy.mover.position[:] = (x, y, z + direction * distance)
                enemy.mover.body = None
                vitals = combat.Vitals(enemy.body, 1000)
                fight.vitals[enemy.body] = vitals
                for _ in range(45):
                    run.step({})
                    if instance.state(20).counter:
                        break
                self.assertEqual(instance.state(19).counter, 1)
                self.assertEqual(vitals.total, 750 if offset < 0 else 1000)
                if knockback:
                    self.assertGreater(direction * enemy.mover.velocity[2], 20)
                    self.assertGreater(enemy.mover.velocity[1], 2)
                else:
                    self.assertEqual(enemy.mover.velocity, (0, 0, 0))

    def test_three_authored_primary_weapons_damage_both_forms_with_per_hit_armour(self):
        for mech in (False, True):
            for name, (pilot_damage, mech_damage, _) in PRIMARY.items():
                with self.subTest(mech=mech, npc=name):
                    run, player, bot, vitals = self.scenario(mech, name)
                    before = vitals.total
                    self.assertIsNotNone(self.first_loss(run, vitals))
                    self.assertIsNotNone(bot.body_script)
                    self.assertEqual(before - vitals.total, mech_damage if mech else pilot_damage)
                    if mech:
                        self.assertEqual(vitals.now[combat.HEALTH_POOL], 300)
                        self.assertEqual(vitals.now[combat.ARMOUR_POOL], 300 - mech_damage)
                    self.assertEqual(player.body_script.component.entity_var(OWNER_HEALTH).value(),
                                     vitals.total)

    def test_incoming_miss_wall_and_allies_preserve_both_forms(self):
        for mech in (False, True):
            for name in PRIMARY:
                for case in ("miss", "wall", "ally"):
                    with self.subTest(mech=mech, npc=name, case=case):
                        run, _, bot, vitals = self.scenario(mech, name, case)
                        before = vitals.total
                        for _ in range(150):
                            run.step({})
                        self.assertEqual(vitals.total, before)
                        self.assertIsNotNone(bot.body_script)

    def test_matrix_intercepts_actual_npc_fire_then_release_restores_incoming_damage(self):
        for name, (_, _, lead) in PRIMARY.items():
            with self.subTest(npc=name):
                run, player, _, vitals = self.scenario(True, name)
                for _ in range(lead):
                    run.step({})
                self.assertEqual(vitals.total, 600)
                distances = []
                original = run.match.combat._matrix_hit

                def observe(*args, _original=original, _distances=distances):
                    result = _original(*args)
                    _distances.append(result)
                    return result

                with patch.object(run.match.combat, "_matrix_hit", side_effect=observe):
                    for _ in range(40):
                        run.step({player: (-32768, 0, 2)})
                self.assertTrue(any(d is not None for d in distances))
                self.assertEqual(vitals.total, 600)
                self.assertIsNotNone(self.first_loss(run, vitals))

    def test_all_five_scripts_load_with_explicit_pools_and_sniper_sparse_weapon_slot(self):
        for name, definition in self.by_name.items():
            with self.subTest(npc=name):
                run, _, bot, _ = self.scenario(False, name)
                script = bot.body_script
                vitals = run.match.combat.vitals[bot.entity]
                self.assertEqual(vitals.total, definition.health + definition.armour + definition.shields)
                self.assertEqual(script.component.entity_var(OWNER_HEALTH).value(), vitals.total)
                self.assertFalse(script.component.owner_dead)
                if name == "Sniper":
                    self.assertEqual(script.component.entity_var(WEAPON_SLOTS).value(), (False, True, True))
                    self.assertEqual(script.component.entity_var(DEFAULT_SLOT).value(), 1)
                    self.assertTrue(any(i.graph.index == 0x1122 and i.vars[28].value() == 1
                                        for i in script.component.instances.values()))

    def test_removal_retires_npc_script_and_owned_flights_without_late_damage(self):
        run, _, bot, vitals = self.scenario(False, "Sniper")
        script = bot.body_script
        for _ in range(180):
            run.step({})
            if any(r.shooter is bot for r in run.match.combat.rockets):
                break
        self.assertTrue(any(r.shooter is bot for r in run.match.combat.rockets))
        self.assertEqual(vitals.total, 150)  # remove a real in-flight shot before its first impact
        before = vitals.total
        self.assertEqual(npc.remove_spawned(run.match), (bot.entity,))
        self.assertTrue(script.component.owner_dead)
        self.assertEqual(script.component.instances, {})
        self.assertFalse(any(r.shooter is bot for r in run.match.combat.rockets))
        for _ in range(160):
            run.step({})
        self.assertEqual(vitals.total, before)


if __name__ == "__main__":
    unittest.main()
