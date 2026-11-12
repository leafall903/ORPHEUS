"""Partial five-species combat with prepared real body graphs and explicit base pools.

Set ORPHEUS_PVE_NPC_DATA to the external catalog directory. It is read-only.
Flat collision and FakeClient transport are explicit fixtures, not client acceptance.
"""

import math
from dataclasses import replace
import os
import unittest
from pathlib import Path
from unittest.mock import patch

import test_junkrat_projectile_combat as helpers
from test_game_combat import Run, aim, in_world
from test_game_mover import box, wall

from ow174.game import combat, enforcer, heavy_charge, immunity, npc, npc_console, npc_control
from ow174.game.collision import World
from ow174.game.commands import FIRE, JUMP, Command
from ow174.game.content import PRACTICE_RANGE_MODE, SOLDIER, find_hero, heroes
from ow174.game.mover import Mover, loaded_mover_table, mover_data
from ow174.game.script import expr, graph, health_query
from ow174.game.script.bot import DEATH_VOLLEY_GRAPH, DEATH_VOLLEY_MODE_FLAG


def clear_graph_cache():
    graph._data.cache_clear()
    graph._records.cache_clear()
    graph.graph.cache_clear()


class JunkratNpcCombatTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        directory = os.environ.get("ORPHEUS_PVE_NPC_DATA")
        if directory is None:
            raise unittest.SkipTest("prepared real NPC catalog not configured")
        directory = Path(directory)
        cls.catalog = npc.load_catalog(directory / "pve_npcs_174.json")
        if len(cls.catalog) != 5:
            raise ValueError("five-species acceptance requires all five actual NPC definitions")
        cls.data_patch = patch.object(graph, "GRAPHS_PATH", directory / "game_graphs_174.json.gz")
        cls.data_patch.start()
        clear_graph_cache()

    @classmethod
    def tearDownClass(cls):
        cls.data_patch.stop()
        clear_graph_cache()

    def create_fight(self, definition, distance=6, *, auto_spawn=False):
        run = Run()
        player, = run.players
        run.match.bots.clear()
        run.match._pending_bot_scripts.clear()
        run.match.switch_hero(player, find_hero("Junkrat"))
        player.client.queue_bot_death = lambda *args: None
        player.client.queue_bot_death_update = lambda *args: None
        player.client.bot_stream = lambda entity: None  # fixture has no observer transport stream
        for _ in range(30):
            run.step({})
        x, y, z = player.mover.position
        if auto_spawn:
            player.mover.yaw = 0
            console = npc_console.Console({"1": definition})
            self.assertTrue(console.press("=", run.match, player)["menu_open"])
            result = console.press("1", run.match, player, attack="auto")
            bot, = run.match.bots
            self.assertEqual(result["entity"], bot.entity)
        else:
            bot = npc.spawn(run.match, definition, (x, y, z + distance), team=1)
        self.assertIsNotNone(bot.body_script)
        self.assertEqual(bot.body, definition.body)
        return run, player, bot, run.match.combat.vitals[bot.entity]

    def test_primary_deals_120_to_each_real_npc_body(self):
        for definition in self.catalog.values():
            with self.subTest(npc=definition.name):
                run, player, bot, vitals = self.create_fight(definition)
                initial = vitals.total
                run.step({player: (0, 0, FIRE)})
                for _ in range(40):
                    run.step({})
                self.assertEqual(vitals.total, initial - 120)
                self.assertIsNotNone(bot.body_script)

    def test_npc_collision_uses_all_five_authored_mover_records(self):
        expected = {'Trooper': (1.7, .5), 'Enforcer': (1.9, .5), 'Sniper': (1.75, .5),
                    'Assassin': (1.5, .5), 'Heavy Assault': (2.3, .7)}
        for definition in self.catalog.values():
            with self.subTest(npc=definition.name):
                run, _player, bot, _vitals = self.create_fight(definition)
                self.assertIn(definition.body, loaded_mover_table(),
                              'NPC mover data must be explicit;generic fallback is not acceptance')
                target = next(t for t in run.match.combat._targets() if t.owner is bot)
                self.assertEqual((target.height, target.radius), expected[definition.name])
                self.assertEqual(target.height, mover_data(definition.body).stand_height)

    def test_real_npc_health_query_tracks_junkrat_primary_damage(self):
        for definition in self.catalog.values():
            with self.subTest(npc=definition.name):
                run, player, bot, vitals = self.create_fight(definition)
                component = bot.body_script.component
                instance = next(iter(component.instances.values()))
                component.set(0x7FFFFF10, expr.Entity(bot.entity))
                cfg = {'$': health_query.CLASS, 'm_entity': {'$': 'STUConfigVarDynamic',
                    'm_identifier': 0x7FFFFF10, 'm_60DB8F99': expr.ENTITY}}
                initial = vitals.total
                self.assertEqual(instance.evaluate(cfg), initial)
                run.step({player: (0, 0, FIRE)})
                for _ in range(40):
                    run.step({})
                self.assertEqual(instance.evaluate(cfg), initial - 120)

    def test_melee_deals_30_to_each_real_npc_body(self):
        for definition in self.catalog.values():
            with self.subTest(npc=definition.name):
                run, player, _bot, vitals = self.create_fight(definition, distance=1.5)
                initial = vitals.total
                run.step({player: (0, 0, 0x800)})
                for _ in range(20):
                    run.step({})
                self.assertEqual(vitals.total, initial - 30)

    def test_mine_deals_120_to_each_real_npc_body(self):
        for definition in self.catalog.values():
            with self.subTest(npc=definition.name):
                run, player, bot, vitals = self.create_fight(definition, distance=4.5)
                position = tuple(bot.position)
                initial = vitals.total
                for offset in range(50):
                    run.step({player: (0, 4096, 8 if offset == 0 else 0)})
                self.assertEqual(vitals.total, initial)
                self.assertEqual(len(run.match.combat.junkrat.mines), 1)
                mine, = run.match.combat.junkrat.mines.values()
                radius = mover_data(definition.body).stand_radius
                center = (position[0], position[1] + radius, position[2])
                self.assertLessEqual(math.dist(mine.flight.position, center) - radius, 1.5)
                run.step({player: (0, 0, 2)})
                for _ in range(20):
                    run.step({})
                self.assertEqual(vitals.total, initial - 120)
                self.assertGreater(bot.position[1], position[1])
                self.assertGreater(bot.position[2], position[2])

    def test_actual_five_metre_throw_uses_surface_distance_falloff_for_each_npc(self):
        for definition in self.catalog.values():
            with self.subTest(npc=definition.name):
                run, player, bot, vitals = self.create_fight(definition, distance=5)
                initial = vitals.total
                position = tuple(bot.position)
                for offset in range(50):
                    run.step({player: (0, 4096, 8 if offset == 0 else 0)})
                mine, = run.match.combat.junkrat.mines.values()
                self.assertTrue(mine.parked)
                radius = mover_data(definition.body).stand_radius
                # The parked point is below the capsule axis: use its lower end.
                center = (position[0], position[1] + radius, position[2])
                self.assertLess(mine.flight.position[1], center[1])
                distance = math.dist(mine.flight.position, center) - radius
                self.assertGreater(distance, 1.5)
                self.assertLess(distance, 3)
                # Authored 120 at 1.5m to 30 at 3m, independent of blast.scale.
                expected = 120 - 60 * (distance - 1.5)
                self.assertEqual(vitals.total, initial)
                run.step({player: (0, 0, 2)})
                for _ in range(20):
                    run.step({})
                self.assertAlmostEqual(initial - vitals.total, expected, places=6)
                self.assertGreater(bot.position[1], position[1])
                self.assertGreater(bot.position[2], position[2])

    def test_primary_kills_and_retains_each_real_npc_corpse(self):
        for definition in self.catalog.values():
            with self.subTest(npc=definition.name):
                run, player, bot, vitals = self.create_fight(definition)
                for _ in range(5000):
                    run.step({player: (0, 0, FIRE)})
                    if vitals.dead:
                        break
                self.assertTrue(vitals.dead)
                self.assertTrue(bot.dead)
                self.assertEqual(vitals.total, 0)
                for _ in range(int(combat.BOT_RESPAWN / combat.TICK) + 10):
                    run.step({})
                self.assertIn(bot, run.match.bots)
                self.assertFalse(any(who is bot for _, who in run.match.combat.respawns))

    def test_total_mayhem_six_explosions_damage_each_real_npc_body(self):
        expected_splash_hits = {"Trooper": 4, "Enforcer": 5, "Sniper": 5, "Assassin": 5, "Heavy Assault": 6}
        for definition in self.catalog.values():
            with self.subTest(npc=definition.name):
                run, player, bot, vitals = self.create_fight(definition)
                fight = run.match.combat
                x, y, z = player.mover.position
                bot.position = (x + 1, y, z)
                initial = vitals.total
                target = next(t for t in fight._targets() if t.owner is player)
                fight._damage(bot, target, 200, False, None, run.frame)  # fixture death trigger
                self.assertEqual(len(fight.rockets), 6)
                direct = fight.rockets[0].direct.amount
                splash = fight.rockets[0].blast.amount
                damage = fight._damage
                events = []

                def observe(source, victim, amount, *args, **kwargs):
                    if source is player and victim.owner is bot:
                        events.append(amount)
                    return damage(source, victim, amount, *args, **kwargs)

                with (patch.object(fight, "_damage", side_effect=observe),
                      patch.object(fight, "_explode", wraps=fight._explode) as explode):
                    for offset in range(1, 100):
                        at = run.frame + offset
                        run.match.update(at * combat.TICK, at, frames=False)
                self.assertEqual(explode.call_count, 6)
                self.assertEqual(splash, 50)
                expected = [splash] * expected_splash_hits[definition.name]
                if definition.name == "Heavy Assault":
                    expected.append(direct)
                self.assertCountEqual(events, expected)
                self.assertEqual(vitals.total, initial - sum(expected))
                self.assertIn(bot, run.match.bots)
                if vitals.dead:
                    self.assertEqual(vitals.killer, player.body)
                hits = player.client.messages(combat.HIT)
                self.assertTrue(hits)
                self.assertTrue(all(hit["+0x78"]["+0x4"] <= 50 for hit in hits))

    @unittest.skipUnless(os.environ.get("ORPHEUS_JUNKRAT_TIRE_PROFILE"), "tire profile not configured")
    def test_tire_deals_600_to_each_real_npc_body_and_retains_corpses(self):
        for definition in self.catalog.values():
            with self.subTest(npc=definition.name):
                run, player, bot, vitals = self.create_fight(definition, distance=8)
                player.body_script.ult_seconds = 0
                player.body_script.set_ult_charge(player.body_script.component.vars[338].value())
                run.step({player: (0, 0, 0x20)})
                system = run.match.combat.junkrat.tire
                tire, = system.actors.values()
                x, y, z = tire.mover.position
                bot.position = (x, y, z + 1.5)
                initial = vitals.total
                while run.frame < tire.arm_tick:
                    run.step({})
                run.step({player: (0, 0, FIRE)})
                self.assertEqual(vitals.total, max(0, initial - 600))
                self.assertFalse(system.actors)
                self.assertEqual(system.last_result, "detonated")
                self.assertIn(bot, run.match.bots)
                self.assertEqual(bot.dead, initial <= 600)
                self.assertEqual(player.body_script.component.vars[342].value(), 0)

    def test_authored_trooper_sniper_and_heavy_primary_damage_junkrat(self):
        damage = {"Trooper": 20, "Sniper": 100, "Heavy Assault": 5}
        for definition in self.catalog.values():
            if definition.name not in damage:
                continue  # Enforcer and Assassin remain explicit open audit rows
            with self.subTest(npc=definition.name):
                run, player, bot, _vitals = self.create_fight(definition, distance=-6)
                run.step({player: (-32768, 0, 0)})
                self.assertEqual(npc_control.start(run.match, player), bot.entity)
                player_vitals = helpers.JunkratProjectileTests.health(self, run, player)
                for _ in range(150):
                    run.step({})
                    if player_vitals.total < 200:
                        break
                self.assertEqual(player_vitals.total, 200 - damage[definition.name])
                self.assertIsNotNone(bot.body_script)

    def test_auto_trooper_reloads_empty_magazine_and_resumes_fire(self):
        definition = next(row for row in self.catalog.values() if row.name == "Trooper")
        run, player, bot, _ = self.create_fight(definition, auto_spawn=True)
        fight = run.match.combat
        health = helpers.JunkratProjectileTests.health(self, run, player)
        # Keep the same real target alive through all 30 authored rounds.
        health.most[combat.HEALTH_POOL] = health.now[combat.HEALTH_POOL] = 1000
        fight._sync_body_health(player, health)
        weapon = next(i for i in bot.body_script.component.instances.values() if i.graph.index == 0x102E)

        for _ in range(900):
            run.step({})
            ammo = weapon.vars.get(53)
            if ammo is not None and ammo.value() == 0:
                break
        self.assertEqual(ammo.value(), 0)
        self.assertEqual(health.total, 400)

        for _ in range(30):
            run.step({})
            windup = weapon.states.get(weapon.graph.node(47).state)
            if windup is not None and windup.active:
                break
        self.assertIsNotNone(windup)
        self.assertTrue(windup.active)
        self.assertFalse(bot.attack_input)
        self.assertEqual(npc_control.stop(run.match), 1)
        self.assertFalse(windup.active)
        for _ in range(100):
            run.step({})
        self.assertEqual((ammo.value(), health.total), (0, 400))

        self.assertEqual(npc_control.start(run.match, player, action="auto"), bot.entity)
        for _ in range(130):
            run.step({})
            self.assertEqual(health.total, 400)
            if ammo.value() != 0:
                break
        self.assertEqual(ammo.value(), 30)
        for _ in range(150):
            run.step({})
            if health.total < 400:
                break
        self.assertEqual((ammo.value(), health.total), (29, 380))
        self.assertEqual(bot.attack_target, player.entity)

    def test_actual_trooper_reload_refills_spent_ammo_without_firing(self):
        definition = next(row for row in self.catalog.values() if row.name == "Trooper")
        run, player, bot, _ = self.create_fight(definition)
        match = run.match
        fight = match.combat
        console = npc_console.Console({})
        instance = next(i for i in bot.body_script.component.instances.values() if i.graph.index == 0x102E)
        self.assertEqual(console.press("0", match, player)["entity"], bot.entity)
        health = helpers.JunkratProjectileTests.health(self, run, player)
        for _ in range(150):
            run.step({})
            if health.total < 200:
                break
        self.assertEqual(health.total, 180)
        console.press("{", match, player)
        for _ in range(30):
            run.step({})
        depleted = instance.vars[53].value()
        self.assertLess(depleted, 30)
        console.press("0", match, player, attack="reload")
        console.press("{", match, player)
        for _ in range(100):
            run.step({})
        self.assertEqual(instance.vars[53].value(), depleted)
        self.assertEqual(health.total, 180)

        console.press("0", match, player, attack="reload")
        run.step({})
        self.assertEqual(instance.vars[53].value(), depleted)
        for _ in range(120):
            run.step({})
            if instance.vars[53].value() == 30:
                break
        self.assertEqual(instance.vars[53].value(), 30)
        self.assertEqual(health.total, 180)
        self.assertFalse(bot.attack_input)

    def test_actual_trooper_fire_waits_for_reload_to_refill(self):
        definition = next(row for row in self.catalog.values() if row.name == "Trooper")
        run, player, bot, _ = self.create_fight(definition)
        match = run.match
        console = npc_console.Console({})
        instance = next(i for i in bot.body_script.component.instances.values() if i.graph.index == 0x102E)
        health = helpers.JunkratProjectileTests.health(self, run, player)
        console.press("0", match, player)
        for _ in range(150):
            run.step({})
            if health.total < 200:
                break
        self.assertEqual(health.total, 180)
        console.press("{", match, player)
        for _ in range(35):
            run.step({})
        depleted = instance.vars[53].value()
        self.assertLess(depleted, 30)

        console.press("0", match, player, attack="reload")
        for _ in range(8):
            run.step({})
        windup = instance.states[instance.graph.node(47).state]
        self.assertTrue(windup.active)
        console.press("0", match, player)
        for _ in range(100):
            run.step({})
            ammo = instance.vars[53].value()
            if ammo == 30:
                break
            self.assertEqual(ammo, depleted)
            self.assertEqual(health.total, 180)
        self.assertEqual(instance.vars[53].value(), 30)
        self.assertEqual(health.total, 180)
        for _ in range(115):
            run.step({})
            if health.total < 180:
                break
        self.assertEqual(health.total, 160)
        self.assertEqual(instance.vars[53].value(), 29)

    def test_junkrat_frag_kill_ends_active_trooper_reload(self):
        definition = next(row for row in self.catalog.values() if row.name == "Trooper")
        run, player, bot, bot_health = self.create_fight(definition)
        match = run.match
        console = npc_console.Console({})
        component = bot.body_script.component
        instance = next(i for i in component.instances.values() if i.graph.index == 0x102E)
        health = helpers.JunkratProjectileTests.health(self, run, player)
        console.press("0", match, player)
        for _ in range(150):
            run.step({})
            if health.total < 200:
                break
        self.assertEqual(health.total, 180)
        console.press("{", match, player)
        for _ in range(35):
            run.step({})
        self.assertLess(instance.vars[53].value(), 30)
        console.press("0", match, player, attack="reload")
        for _ in range(8):
            run.step({})
        windup = instance.states[instance.graph.node(47).state]
        self.assertTrue(windup.active)

        bot_health.now[combat.HEALTH_POOL] = 100
        run.step({player: (0, 0, FIRE)})
        for _ in range(35):
            run.step({})
            if bot.dead:
                break
        self.assertTrue(bot.dead)
        self.assertEqual(bot_health.killer, player.body)
        self.assertTrue(component.owner_dead)
        self.assertFalse(windup.active)
        remaining = instance.vars[53].value()
        self.assertLess(remaining, 30)
        for _ in range(110):
            run.step({})
        self.assertEqual(instance.vars[53].value(), remaining)
        self.assertFalse(component.shots)
        self.assertFalse(bot.attack_input)
        self.assertEqual(health.total, 180)
        self.assertIn(bot, match.bots)

    def test_flashbang_aborts_trooper_reload_and_allows_fresh_request(self):
        definition = next(row for row in self.catalog.values() if row.name == "Trooper")
        run, player, bot, bot_health = self.create_fight(definition, distance=3)
        match = run.match
        fight = match.combat
        match.switch_hero(player, heroes()[0x02E0000000000042])
        x, y, z = player.mover.position
        bot.position = (x, y, z + 3)
        angles = aim(fight._eye(player), (x, y + 1, z + 3))
        console = npc_console.Console({})
        instance = next(i for i in bot.body_script.component.instances.values() if i.graph.index == 0x102E)
        for _ in range(30):
            run.step({player: (*angles, 0)})
        console.press("0", match, player)
        for _ in range(120):
            run.step({player: (*angles, 0)})
            if instance.vars[53].value() < 30:
                break
        console.press("{", match, player)
        for _ in range(35):
            run.step({player: (*angles, 0)})
        depleted = instance.vars[53].value()
        self.assertLess(depleted, 30)
        console.press("0", match, player, attack="reload")
        for _ in range(8):
            run.step({player: (*angles, 0)})
        windup = instance.states[instance.graph.node(47).state]
        self.assertTrue(windup.active)
        run.step({player: (*angles, 16)})
        for _ in range(35):
            run.step({player: (*angles, 0)})
            if fight.is_stunned(bot):
                break
        self.assertTrue(fight.is_stunned(bot))
        self.assertEqual(bot_health.total, 175)
        self.assertFalse(windup.active)
        for _ in range(120):
            run.step({player: (*angles, 0)})
        self.assertFalse(fight.is_stunned(bot))
        self.assertEqual(instance.vars[53].value(), depleted)
        console.press("0", match, player, attack="reload")
        for _ in range(100):
            run.step({player: (*angles, 0)})
            if instance.vars[53].value() == 30:
                break
        self.assertEqual(instance.vars[53].value(), 30)

    def test_flashbang_interrupts_actual_trooper_and_heavy_melee(self):
        for name, damage in (("Trooper", 30), ("Heavy Assault", 80)):
            with self.subTest(name=name):
                definition = next(row for row in self.catalog.values() if row.name == name)
                run, player, bot, bot_health = self.create_fight(definition, distance=2.5)
                match = run.match
                fight = match.combat
                match.switch_hero(player, heroes()[0x02E0000000000042])
                x, y, z = player.mover.position
                bot.position = (x, y, z + 2.5)
                angles = aim(fight._eye(player), (x, y + 1, z + 2.5))
                for _ in range(30):
                    run.step({player: (*angles, 0)})
                health = helpers.JunkratProjectileTests.health(self, run, player)
                before, npc_before = health.total, bot_health.total
                self.assertEqual(npc_control.start(match, player, action="melee"), bot.entity)
                instance = heavy_charge.melee_instance(bot)
                for _ in range(12):
                    run.step({player: (*angles, 0)})
                    ability = heavy_charge.melee_ability(instance)
                    if ability is not None and ability.remaining() > 0:
                        break
                self.assertGreater(ability.remaining(), 0)
                self.assertNotIn(bot.entity, fight.npc_melee_seen)
                run.step({player: (*angles, 16)})
                for _ in range(30):
                    run.step({player: (*angles, 0)})
                    if fight.is_stunned(bot):
                        break
                self.assertTrue(fight.is_stunned(bot))
                self.assertEqual(bot_health.total, npc_before - 25)
                for _ in range(95):
                    run.step({player: (*angles, 0)})
                self.assertFalse(fight.is_stunned(bot))
                self.assertNotIn(bot.entity, fight.npc_melee_seen)
                self.assertEqual(health.total, before)
                self.assertEqual(npc_control.start(match, player, action="melee"), bot.entity)
                for _ in range(40):
                    run.step({player: (*angles, 0)})
                self.assertEqual(health.total, before - damage)

    def test_projected_barrier_absorbs_actual_trooper_and_heavy_primary(self):
        for name, damage in (("Trooper", 20), ("Heavy Assault", 5)):
            with self.subTest(name=name):
                definition = next(row for row in self.catalog.values() if row.name == name)
                run, player, bot, _ = self.create_fight(definition, distance=-6)
                match = run.match
                fight = match.combat
                match.game_map = replace(match.game_map, free_for_all=False)
                x, y, z = player.mover.position
                zarya = match.add_player(2, "Zarya", SOLDIER, player.team, False)
                in_world(match, zarya)
                zarya.spawn = ((x + 5, y, z), zarya.spawn[1])
                match.switch_hero(zarya, find_hero("Zarya"))
                run.players.append(zarya)
                commands = {player: (-32768, 0, 0)}
                for _ in range(25):
                    run.step(commands)
                body = next(t for t in fight._targets() if t.owner is player)
                a, b = body.segment()
                centre = tuple((lo + hi) * .5 for lo, hi in zip(a, b, strict=True))
                run.step({**commands, zarya: (*aim(fight._eye(zarya), centre), 16)})
                fight.barriers.sync(zarya)
                bubble, = fight.barriers.live.values()
                self.assertEqual((bubble.body, bubble.health), (player.body, 200))

                self.assertEqual(npc_control.start(match, player), bot.entity)
                with patch.object(fight, "_shoot", wraps=fight._shoot) as shoot:
                    for _ in range(110):
                        run.step(commands)
                        if shoot.called:
                            break
                    shoot.assert_called_once()
                    shooter, _, state, _, _ = shoot.call_args.args
                    self.assertIs(shooter, bot)
                    speed = state.node.field("m_projectileSpeed")
                    self.assertFalse(speed is not None and state.evaluate(speed, watch=False))
                    self.assertFalse(any(rocket.shooter is bot for rocket in fight.rockets))
                    self.assertEqual(bubble.health, 200 - damage)
                    self.assertEqual(fight.vitals_of(body).total, 200)
                    self.assertFalse(fight.is_stunned(player))

                self.assertEqual(npc_control.stop(match), 1)
                for _ in range(135):
                    if not bubble.phase.active:
                        break
                    run.step(commands)
                self.assertFalse(bubble.phase.active)
                self.assertEqual(npc_control.start(match, player), bot.entity)
                for _ in range(110):
                    run.step(commands)
                    if fight.vitals_of(body).total < 200:
                        break
                self.assertEqual(fight.vitals_of(body).total, 200 - damage)

    def test_projected_barrier_absorbs_actual_trooper_and_heavy_melee(self):
        for name, damage in (("Trooper", 30), ("Heavy Assault", 80)):
            with self.subTest(name=name):
                definition = next(row for row in self.catalog.values() if row.name == name)
                run, player, bot, _ = self.create_fight(definition, distance=-2.5)
                match = run.match
                fight = match.combat
                match.game_map = replace(match.game_map, free_for_all=False)
                x, y, z = player.mover.position
                zarya = match.add_player(2, "Zarya", SOLDIER, player.team, False)
                in_world(match, zarya)
                zarya.spawn = ((x + 5, y, z), zarya.spawn[1])
                match.switch_hero(zarya, find_hero("Zarya"))
                run.players.append(zarya)
                for _ in range(25):
                    run.step({})
                angles = aim(fight._eye(player), (x, y + 1, z - 2.5))
                body = next(target for target in fight._targets() if target.owner is player)
                a, b = body.segment()
                centre = tuple((lo + hi) * .5 for lo, hi in zip(a, b, strict=True))
                run.step({player: (*angles, 0), zarya: (*aim(fight._eye(zarya), centre), 16)})
                fight.barriers.sync(zarya)
                bubble, = fight.barriers.live.values()
                self.assertEqual(bubble.body, player.body)
                self.assertEqual(bubble.health, 200)
                position = tuple(player.mover.position)
                self.assertEqual(npc_control.start(match, player, action="melee"), bot.entity)
                for _ in range(55):
                    run.step({player: (*angles, 0)})
                self.assertIn(bot.entity, fight.npc_melee_seen)
                self.assertEqual(bubble.health, 200 - damage)
                self.assertEqual(fight.vitals_of(body).total, 200)
                self.assertEqual(tuple(player.mover.position), position)
                self.assertFalse(fight.is_stunned(player))

    def test_actual_npc_melee_respects_frontal_genji_deflect_after_barrier(self):
        for name, damage in (("Trooper", 30), ("Heavy Assault", 80)):
            for scenario in ("front", "rear", "projected"):
                with self.subTest(npc=name, scenario=scenario):
                    definition = next(row for row in self.catalog.values() if row.name == name)
                    run, genji, bot, npc_vitals = self.create_fight(definition, distance=2.5)
                    match = run.match
                    fight = match.combat
                    match.game_map = replace(match.game_map, free_for_all=False)
                    match.switch_hero(genji, find_hero("Genji"))
                    x, y, z = genji.mover.position
                    bot.position = (x, y, z + 2.5)
                    if scenario == "projected":
                        zarya = match.add_player(2, "Zarya", SOLDIER, genji.team, False)
                        in_world(match, zarya)
                        zarya.spawn = ((x + 5, y, z), zarya.spawn[1])
                        match.switch_hero(zarya, find_hero("Zarya"))
                        run.players.append(zarya)
                    for _ in range(25):
                        run.step({})
                    body = next(target for target in fight._targets() if target.owner is genji)
                    if scenario == "projected":
                        a, b = body.segment()
                        centre = tuple((lo + hi) * 0.5 for lo, hi in zip(a, b, strict=True))
                        run.step({zarya: (*aim(fight._eye(zarya), centre), 16)})
                        fight.barriers.sync(zarya)
                        bubble, = fight.barriers.live.values()
                        self.assertEqual((bubble.body, bubble.health), (genji.body, 200))
                    angles = aim(fight._eye(genji), (x, y + 1.0, z + 2.5))
                    run.step({genji: (*angles, 16)})
                    self.assertEqual(npc_control.start(match, genji, action="melee"), bot.entity)
                    yaw = -32768 if scenario == "rear" else 0
                    deflect = next(i for i in genji.body_script.component.instances.values()
                                   if i.graph.index == 0x03A3)
                    initial_position = tuple(genji.mover.position)
                    bot_health = npc_vitals.total
                    for _ in range(45):
                        run.step({genji: (yaw, 0, 0)})
                        if bot.entity in fight.npc_melee_seen:
                            break
                    self.assertIn(bot.entity, fight.npc_melee_seen)
                    self.assertTrue(deflect.states[10].active)
                    self.assertEqual(fight.vitals_of(body).total, 200 - damage if scenario == "rear" else 200)
                    self.assertEqual(npc_vitals.total, bot_health)
                    self.assertFalse(genji.client.messages(combat.HIT))
                    if scenario != "rear":
                        self.assertEqual(tuple(genji.mover.position), initial_position)
                        self.assertFalse(fight.is_stunned(genji))
                    if scenario == "projected":
                        self.assertEqual(bubble.health, 200 - damage)

    def test_actual_npc_melee_bypasses_grasp_and_respects_fortify(self):
        for name, damage in (("Trooper", 30), ("Heavy Assault", 80)):
            for hero in ("Sigma", "Orisa"):
                with self.subTest(npc=name, hero=hero):
                    definition = next(row for row in self.catalog.values() if row.name == name)
                    run, player, bot, _ = self.create_fight(definition, distance=2.5)
                    match = run.match
                    fight = match.combat
                    match.switch_hero(player, find_hero(hero))
                    x, y, z = player.mover.position
                    bot.position = (x, y, z + 2.5)
                    angles = aim(fight._eye(player), (x, y + 1, z + 2.5))
                    run.step({player: (*angles, 8)})
                    health = helpers.JunkratProjectileTests.health(self, run, player)
                    before = health.total
                    position = tuple(player.mover.position)
                    self.assertEqual(npc_control.start(match, player, action="melee"), bot.entity)
                    for _ in range(45):
                        run.step({player: (*angles, 0)})
                        if bot.entity in fight.npc_melee_seen:
                            break
                    self.assertIn(bot.entity, fight.npc_melee_seen)
                    if hero == "Orisa":
                        scale = immunity.damage_scale(player, player.body)
                        self.assertLess(scale, 1)
                        self.assertTrue(immunity.control_immune(player, player.body))
                        amount = damage * scale
                        expected = max(amount - combat.ARMOUR_BLOCK, amount / 2)
                        self.assertAlmostEqual(before - health.total, expected)
                        self.assertEqual(tuple(player.mover.position), position)
                        self.assertEqual(tuple(player.mover.velocity), (0.0, 0.0, 0.0))
                    else:
                        grasp = next(i for i in player.body_script.component.instances.values()
                                     if i.graph.index == 0x1D02)
                        self.assertTrue(grasp.states[1].active)
                        self.assertEqual(grasp.vars[21312].value(), 0)
                        self.assertEqual(before - health.total, damage)

    def test_actual_npc_melee_respects_wraith_and_cryo_then_hits_after_expiry(self):
        cases = (
            ("Trooper", "Reaper", immunity.wraith_active, 30),
            ("Heavy Assault", "Mei", immunity.cryo_active, 80),
        )
        for name, hero, active, damage in cases:
            with self.subTest(name=name, hero=hero):
                definition = next(row for row in self.catalog.values() if row.name == name)
                run, player, bot, _ = self.create_fight(definition, distance=2.5)
                match = run.match
                fight = match.combat
                match.switch_hero(player, find_hero(hero))
                x, y, z = player.mover.position
                bot.position = (x, y, z + 2.5)
                angles = aim(fight._eye(player), (x, y + 1, z + 2.5))
                run.step({player: (*angles, 8)})
                for _ in range(40):
                    if active(player, player.body):
                        break
                    run.step({player: (*angles, 0)})
                self.assertTrue(active(player, player.body))
                health = helpers.JunkratProjectileTests.health(self, run, player)
                self.assertEqual(npc_control.start(match, player, action="melee"), bot.entity)
                for _ in range(45):
                    run.step({player: (*angles, 0)})
                self.assertTrue(active(player, player.body))
                self.assertEqual(health.total, 250)
                self.assertIn(bot.entity, fight.npc_melee_seen)
                for _ in range(320):
                    if not active(player, player.body):
                        break
                    run.step({player: (*angles, 0)})
                self.assertFalse(active(player, player.body))
                self.assertEqual(health.total, 250)
                self.assertEqual(npc_control.start(match, player, action="melee"), bot.entity)
                for _ in range(45):
                    run.step({player: (*angles, 0)})
                self.assertEqual(health.total, 250 - damage)

    def test_auto_engagement_uses_authored_melee_on_junkrat(self):
        for name, damage in (("Trooper", 30), ("Heavy Assault", 80)):
            with self.subTest(name=name):
                definition = next(row for row in self.catalog.values() if row.name == name)
                run, player, bot, _ = self.create_fight(definition, distance=2.5)
                fight = run.match.combat
                health = helpers.JunkratProjectileTests.health(self, run, player)
                instance = heavy_charge.melee_instance(bot)
                self.assertEqual(npc_control.start(run.match, player, action="auto"), bot.entity)

                with patch.object(fight, "_shoot", wraps=fight._shoot) as shoot:
                    for _ in range(45):
                        run.step({})
                        if health.total < 200:
                            break
                    self.assertEqual(health.total, 200 - damage)
                    self.assertIn(bot.entity, fight.npc_melee_seen)
                    ability = heavy_charge.melee_ability(instance)
                    self.assertGreater(ability.remaining(), 0)
                    self.assertFalse(bot.attack_input)
                    shoot.assert_not_called()
                    self.assertEqual(bot.attack_target, player.entity)

                    for _ in range(120):
                        run.step({})
                        if bot.attack_input:
                            break
                    self.assertTrue(bot.attack_input)
                    self.assertGreater(ability.remaining(), 0)
                    if name == "Heavy Assault":
                        # Move the target beyond automatic charge reach to isolate rifle fire.
                        x, y, z = bot.position
                        player.mover = Mover((x, y, z - 30), world=run.match.collision_world,
                                             data=mover_data(player.hero.body))
                    for _ in range(120):
                        run.step({})
                        if shoot.called:
                            break
                    shoot.assert_called_once()

    def test_stopping_auto_melee_retires_its_authored_windup(self):
        for name in ("Trooper", "Heavy Assault"):
            with self.subTest(name=name):
                definition = next(row for row in self.catalog.values() if row.name == name)
                run, player, bot, _ = self.create_fight(definition, distance=2.5)
                fight = run.match.combat
                health = helpers.JunkratProjectileTests.health(self, run, player)
                instance = heavy_charge.melee_instance(bot)
                self.assertEqual(npc_control.start(run.match, player, action="auto"), bot.entity)
                for _ in range(12):
                    run.step({})
                    ability = heavy_charge.melee_ability(instance)
                    if ability is not None and ability.flags & ability.RUNNING:
                        break
                self.assertIsNotNone(ability)
                self.assertTrue(ability.flags & ability.RUNNING)
                self.assertEqual(health.total, 200)
                self.assertEqual(npc_control.stop(run.match), 1)
                for _ in range(85):
                    run.step({})
                self.assertEqual(health.total, 200)
                self.assertNotIn(bot.entity, fight.npc_melee_seen)
                self.assertFalse(bot.attack_input)
                self.assertFalse(ability.flags & ability.RUNNING)

    def test_actual_trooper_melee_hits_once_and_respects_wall_team_and_range(self):
        definition = next(row for row in self.catalog.values() if row.name == "Trooper")
        for mode in ("hit", "wall", "ally", "range"):
            with self.subTest(mode=mode):
                distance = 5 if mode == "range" else 2.5
                run, player, bot, _ = self.create_fight(definition, distance=-distance)
                match = run.match
                fight = match.combat
                match.game_map = replace(match.game_map, free_for_all=False)
                x, y, z = player.mover.position
                angles = aim(fight._eye(player), (x, y + 1, z - distance))
                run.step({player: (*angles, 0)})
                console = npc_console.Console({})
                self.assertEqual(console.press("0", match, player, attack="melee")["entity"], bot.entity)
                if mode == "wall":
                    plane = z - distance / 2
                    match.collision_world = World(
                        box(x - 10, x + 10, y - 1, y, z - 10, z + 10)
                        + wall(x - 5, plane, x + 5, plane, y0=y, y1=y + 5)
                    )
                elif mode == "ally":
                    bot.team = player.team
                health = helpers.JunkratProjectileTests.health(self, run, player)
                for _ in range(40):
                    run.step({player: (*angles, 0)})
                self.assertEqual(health.total, 170 if mode == "hit" else 200)
                self.assertEqual(bot.entity in fight.npc_melee_seen, mode == "hit")
                for _ in range(80):
                    run.step({player: (*angles, 0)})
                self.assertEqual(health.total, 170 if mode == "hit" else 200)

    def test_actual_trooper_stops_firing_and_can_be_restarted_or_removed(self):
        definition = next(row for row in self.catalog.values() if row.name == "Trooper")
        run, player, bot, _ = self.create_fight(definition, distance=-6)
        match = run.match
        fight = match.combat
        console = npc_console.Console({})
        health = helpers.JunkratProjectileTests.health(self, run, player)
        commands = {player: (-32768, 0, 0)}
        run.step(commands)
        with patch.object(fight, "_shoot", wraps=fight._shoot) as shoot:
            for action in ("{", "-"):
                with self.subTest(action=action):
                    before = health.total
                    shoot.reset_mock()
                    self.assertEqual(console.press("0", match, player)["entity"], bot.entity)
                    for _ in range(150):
                        run.step(commands)
                        if health.total < before:
                            break
                    self.assertEqual(health.total, before - 20)
                    shoot.assert_called_once()
                    self.assertIs(shoot.call_args.args[0], bot)
                    result = console.press(action, match, player)
                    self.assertTrue(result["handled"])
                    shoot.reset_mock()
                    for _ in range(50):
                        run.step(commands)
                    shoot.assert_not_called()
                    self.assertEqual(health.total, before - 20)
                    self.assertEqual(bot in match.bots, action == "{")

    def test_actual_heavy_charge_grazes_junkrat_once_and_stop_releases_input(self):
        definition = next(row for row in self.catalog.values() if row.name == "Heavy Assault")
        run, player, bot, _ = self.create_fight(definition, distance=-8)
        fight = run.match.combat
        x, y, z = player.mover.position
        run.step({player: (*aim(fight._eye(player), (x, y + 1, z - 8)), 0)})
        self.assertEqual(npc_control.start(run.match, player, action="charge"), bot.entity)
        player.mover = Mover((x + 1.7, y, z), world=run.match.collision_world, data=mover_data(player.hero.body))
        instance = heavy_charge.charge(bot)
        for _ in range(150):
            run.step({})
            if helpers.JunkratProjectileTests.health(self, run, player).total < 200:
                break
        self.assertEqual(helpers.JunkratProjectileTests.health(self, run, player).total, 150)
        self.assertIn(player.body, fight.heavy_charge_contacts[bot.entity])
        self.assertGreater(player.mover.velocity[0], 0)
        self.assertGreater(player.mover.velocity[1], 0)
        self.assertEqual(npc_control.stop(run.match), 1)
        for _ in range(8):
            run.step({})
        self.assertEqual(heavy_charge.phase(instance), 0)
        self.assertNotIn(heavy_charge.BUTTON, instance.component.held)
        self.assertNotIn(bot.entity, fight.heavy_charge_contacts)
        self.assertEqual(helpers.JunkratProjectileTests.health(self, run, player).total, 150)

    def test_auto_heavy_charge_pins_junkrat_then_stop_releases(self):
        definition = next(row for row in self.catalog.values() if row.name == "Heavy Assault")
        run, player, bot, _ = self.create_fight(definition, distance=8)
        fight = run.match.combat
        instance = heavy_charge.charge(bot)
        self.assertEqual(npc_control.start(run.match, player, action="auto"), bot.entity)
        with patch.object(fight, "_shoot", wraps=fight._shoot) as shoot:
            for _ in range(150):
                run.step({})
                if bot.entity in fight.heavy_charge_pins:
                    break
            self.assertIn(bot.entity, fight.heavy_charge_pins)
            self.assertEqual(fight.heavy_charge_pins[bot.entity].graph.index, 0x1407)
            self.assertEqual(fight.pins[bot.entity], player.body)
            self.assertTrue(fight.is_stunned(player))
            shoot.assert_not_called()
            self.assertEqual(npc_control.stop(run.match), 1)
            self.assertFalse(fight.is_stunned(player))
            self.assertNotIn(bot.entity, fight.heavy_charge_pins)
            self.assertEqual(player.mover.velocity, (0.0, 0.0, 0.0))
            self.assertFalse(instance.vars[536].value())
            for _ in range(100):
                run.step({})
            self.assertFalse(bot.firing)
            self.assertFalse(heavy_charge.clocked(bot))
            self.assertEqual(heavy_charge.phase(instance), 0)
            shoot.assert_not_called()

    def test_auto_heavy_charge_front_impulse_precedes_pin_and_rechecks_team(self):
        definition = next(row for row in self.catalog.values() if row.name == "Heavy Assault")
        for change_team in (False, True):
            with self.subTest(change_team=change_team):
                run, player, bot, _ = self.create_fight(definition, distance=8)
                match = run.match
                match.game_map = replace(match.game_map, free_for_all=False)
                fight = match.combat
                health = helpers.JunkratProjectileTests.health(self, run, player)
                before = health.total
                self.assertEqual(npc_control.start(match, player, action="auto"), bot.entity)
                for _ in range(150):
                    run.frame += 1
                    match.update(run.frame * combat.TICK, run.frame, frames=False)
                    if player.mover.velocity[1] > 0 or bot.entity in fight.pins:
                        break
                    player.take_commands([Command(run.frame)])
                self.assertNotIn(bot.entity, fight.pins)
                self.assertEqual(player.mover.velocity, (0.0, 10.0, -1.0))
                self.assertEqual(health.total, before)
                self.assertFalse(fight.is_stunned(player))
                feet = player.mover.position[1]
                player.take_commands([Command(run.frame)])
                self.assertGreater(player.mover.position[1], feet)
                if change_team:
                    player.team = bot.team
                run.step({})
                if change_team:
                    self.assertNotIn(bot.entity, fight.pins)
                    self.assertLess(player.mover.velocity[1], 10.0)
                else:
                    self.assertEqual(fight.pins.get(bot.entity), player.body)
                    self.assertEqual(fight.heavy_charge_pins[bot.entity].graph.index, 0x1407)
                    self.assertEqual(player.mover.velocity, (0.0, 0.0, 0.0))
                self.assertEqual(health.total, before)

    def test_auto_heavy_charge_natural_release_uses_authored_impulse_once(self):
        definition = next(row for row in self.catalog.values() if row.name == "Heavy Assault")
        run, player, bot, _ = self.create_fight(definition, distance=8)
        fight = run.match.combat
        health = helpers.JunkratProjectileTests.health(self, run, player)
        before = health.total
        self.assertEqual(npc_control.start(run.match, player, action="auto"), bot.entity)
        instance = heavy_charge.charge(bot)
        for _ in range(150):
            run.step({})
            if bot.entity in fight.heavy_charge_pins:
                break
        app = fight.heavy_charge_pins[bot.entity]
        self.assertEqual(app.component.entity, player.body)
        self.assertEqual(app.graph.index, 0x1407)
        self.assertEqual(player.mover.velocity, (0.0, 0.0, 0.0))

        for _ in range(250):
            run.frame += 1
            run.match.update(run.frame * combat.TICK, run.frame, frames=False)
            if bot.entity not in fight.pins:
                break
        self.assertFalse(fight.is_stunned(player))
        self.assertEqual(heavy_charge.phase(instance), 0)
        self.assertNotIn(app.id, app.component.instances)
        self.assertNotIn(bot.entity, fight.heavy_charge_pins)
        self.assertEqual(health.total, before)
        self.assertEqual(player.mover.velocity, (0.0, 5.0, -10.0))
        release = instance.states[instance.graph.node(heavy_charge.RELEASE_HEALTH).state]
        self.assertFalse(release.active)
        self.assertFalse(instance.vars[536].value())

        run.step({player: (0, 0, FIRE)})
        self.assertTrue(any(rocket.shooter is player for rocket in fight.rockets))
        self.assertLess(player.mover.velocity[1], 5.0)
        for _ in range(10):
            run.step({})
            self.assertFalse(release.active)
        self.assertFalse(instance.vars[536].value())
        self.assertEqual(health.total, before)

    def test_auto_heavy_charge_rejects_a_low_obstacle_and_long_range(self):
        definition = next(row for row in self.catalog.values() if row.name == "Heavy Assault")
        for distance, low_wall in ((8, True), (30, False)):
            with self.subTest(distance=distance):
                run, player, bot, _ = self.create_fight(definition, distance=distance)
                fight = run.match.combat
                self.assertEqual(npc_control.start(run.match, player, action="auto"), bot.entity)
                if low_wall:
                    x, y, z = player.mover.position
                    run.match.collision_world = World(
                        box(x - 40, x + 40, y - 1, y, z - 40, z + 40)
                        + wall(x - 5, z + 4, x + 5, z + 4, y0=y, y1=y + 1.25)
                    )
                with patch.object(fight, "_shoot", wraps=fight._shoot) as shoot:
                    for _ in range(100):
                        run.step({})
                        self.assertFalse(heavy_charge.clocked(bot))
                        if shoot.called:
                            break
                    shoot.assert_called_once()

    def test_actual_heavy_charge_attaches_junkrat_then_stop_releases(self):
        definition = next(row for row in self.catalog.values() if row.name == "Heavy Assault")
        run, player, bot, _ = self.create_fight(definition, distance=-8)
        fight = run.match.combat
        x, y, z = player.mover.position
        run.step({player: (*aim(fight._eye(player), (x, y + 1, z - 8)), 0)})
        self.assertEqual(npc_control.start(run.match, player, action="charge"), bot.entity)
        for _ in range(150):
            run.step({})
            if bot.entity in fight.heavy_charge_pins:
                break
        app = fight.heavy_charge_pins[bot.entity]
        self.assertEqual(app.graph.index, 0x1407)
        self.assertEqual(fight.pins[bot.entity], player.body)
        self.assertTrue(fight.is_stunned(player))
        first = player.mover.position[2]
        for _ in range(4):
            run.step({player: (0, 0, FIRE)})
        self.assertGreater(player.mover.position[2], first)
        self.assertFalse(any(rocket.shooter is player for rocket in fight.rockets))
        self.assertEqual(npc_control.stop(run.match), 1)
        self.assertNotIn(bot.entity, fight.heavy_charge_pins)
        self.assertNotIn(app.id, app.component.instances)
        self.assertFalse(fight.is_stunned(player))

    def test_actual_heavy_charge_crushes_attached_junkrat_at_a_wall(self):
        definition = next(row for row in self.catalog.values() if row.name == "Heavy Assault")
        run, player, bot, _ = self.create_fight(definition, distance=-8)
        fight = run.match.combat
        x, y, z = player.mover.position
        run.match.collision_world = World(
            box(x - 20, x + 20, y - 1, y, z - 20, z + 20)
            + wall(x - 5, z + 3, x + 5, z + 3, y0=y, y1=y + 5)
        )
        run.step({player: (*aim(fight._eye(player), (x, y + 1, z - 8)), 0)})
        self.assertEqual(npc_control.start(run.match, player, action="charge"), bot.entity)
        attached = False
        for _ in range(150):
            run.step({})
            attached |= bot.entity in fight.heavy_charge_pins
            if fight.is_dead(player):
                break
        self.assertTrue(attached)
        self.assertTrue(fight.is_dead(player))
        self.assertEqual(helpers.JunkratProjectileTests.health(self, run, player).killer, bot.entity)
        self.assertNotIn(bot.entity, fight.heavy_charge_pins)

    def test_armed_trap_interrupts_live_heavy_charge_before_contact(self):
        self.check_trap_interrupts_heavy_charge(automatic=False)

    def test_armed_trap_interrupts_auto_charge_then_ranged_fire_resumes(self):
        self.check_trap_interrupts_heavy_charge(automatic=True)

    def check_trap_interrupts_heavy_charge(self, *, automatic):
        definition = next(row for row in self.catalog.values() if row.name == "Heavy Assault")
        run, player, bot, bot_health = self.create_fight(definition, distance=-8)
        fight = run.match.combat
        run.step({player: (0, 0, 0x10)})
        for _ in range(70):
            run.step({})
        trap, = fight.junkrat.traps.values()
        self.assertTrue(trap.parked)
        self.assertGreaterEqual(fight.now, trap.armed_at)
        x, _, z = trap.flight.position
        y = player.mover.position[1]
        player.mover = Mover((x, y, z + 5), world=run.match.collision_world, data=mover_data(player.hero.body))
        bot.position = (x, y, z - 8)
        run.step({player: (*aim(fight._eye(player), (x, y + 1.35, z - 8)), 0)})
        action = "auto" if automatic else "charge"
        self.assertEqual(npc_control.start(run.match, player, action=action), bot.entity)
        instance = heavy_charge.charge(bot)
        driving = False
        before = bot_health.total
        for _ in range(160):
            run.step({})
            driving |= heavy_charge.driving(instance)
            if trap.victim is bot:
                break
        self.assertTrue(driving)
        self.assertIs(trap.victim, bot)
        self.assertTrue(fight.junkrat.rooted(bot.entity, run.frame))
        self.assertEqual(before - bot_health.total, trap.damage.amount)
        for _ in range(4):
            run.step({})
        self.assertEqual(heavy_charge.phase(instance), 0)
        self.assertNotIn(heavy_charge.BUTTON, instance.component.held)
        self.assertNotIn(bot.entity, fight.heavy_charge_pins)
        position = tuple(bot.position)
        health = helpers.JunkratProjectileTests.health(self, run, player)
        if automatic:
            self.assertTrue(bot.firing)
            self.assertGreater(heavy_charge.ability(instance).remaining(), 0)
            for _ in range(160):
                run.step({})
                self.assertEqual(heavy_charge.phase(instance), 0)
                if health.total < 200:
                    break
            self.assertEqual(health.total, 195)
            self.assertEqual(bot.attack_target, player.entity)
        else:
            self.assertIsNone(bot.attack_target)
            for _ in range(30):
                run.step({})
            self.assertEqual(health.total, 200)
        self.assertEqual(tuple(bot.position), position)

    def test_actual_heavy_charge_cooldown_requires_a_fresh_request(self):
        definition = next(row for row in self.catalog.values() if row.name == "Heavy Assault")
        run, player, bot, _ = self.create_fight(definition, distance=-8)
        fight = run.match.combat

        def request():
            x, y, z = bot.position
            run.step({player: (*aim(fight._eye(player), (x, y + 1, z)), 0)})
            return npc_control.start(run.match, player, action="charge")

        self.assertEqual(request(), bot.entity)
        instance = heavy_charge.charge(bot)
        for _ in range(100):
            run.step({})
            if heavy_charge.driving(instance):
                break
        self.assertTrue(heavy_charge.driving(instance))
        self.assertEqual(npc_control.stop(run.match), 1)
        run.step({})
        ability = instance.states[instance.graph.node(heavy_charge.ABILITY).state]
        self.assertAlmostEqual(ability.remaining(), 15, delta=.05)
        with self.assertRaisesRegex(ValueError, "cooling down"):
            request()
        self.assertIsNone(bot.attack_target)
        for _ in range(1000):
            if not ability.remaining():
                break
            run.step({})
        self.assertEqual(ability.remaining(), 0)
        self.assertFalse(heavy_charge.driving(instance))
        self.assertNotIn(heavy_charge.BUTTON, instance.component.held)
        self.assertEqual(request(), bot.entity)
        for _ in range(100):
            run.step({})
            if heavy_charge.driving(instance):
                break
        self.assertTrue(heavy_charge.driving(instance))

    def test_projected_zarya_barrier_absorbs_heavy_charge_without_pinning_junkrat(self):
        definition = next(row for row in self.catalog.values() if row.name == "Heavy Assault")
        run, player, bot, _ = self.create_fight(definition, distance=-8)
        match = run.match
        fight = match.combat
        match.game_map = replace(match.game_map, free_for_all=False)
        x, y, z = player.mover.position
        zarya = match.add_player(2, "Zarya", SOLDIER, player.team, False)
        in_world(match, zarya)
        zarya.spawn = ((x + 5, y, z), zarya.spawn[1])
        match.switch_hero(zarya, find_hero("Zarya"))
        run.players.append(zarya)
        for _ in range(25):
            run.step({})
        run.step({player: (*aim(fight._eye(player), (x, y + 1, z - 8)), 0)})
        self.assertEqual(npc_control.start(match, player, action="charge"), bot.entity)
        instance = heavy_charge.charge(bot)
        for _ in range(100):
            run.step({})
            if heavy_charge.driving(instance):
                break
        self.assertTrue(heavy_charge.driving(instance))
        body = next(t for t in fight._targets() if t.owner is player)
        a, b = body.segment()
        centre = tuple((lo + hi) * .5 for lo, hi in zip(a, b, strict=True))
        run.step({zarya: (*aim(fight._eye(zarya), centre), 16)})
        fight.barriers.sync(zarya)
        bubble, = fight.barriers.live.values()
        self.assertEqual(bubble.health, 200)
        for _ in range(65):
            run.step({})
            self.assertNotIn(bot.entity, fight.heavy_charge_pins)
            if bubble.health < 200:
                break
        self.assertEqual(bubble.health, 25)
        self.assertEqual(fight.vitals_of(body).total, 200)
        self.assertFalse(fight.is_stunned(player))

    def test_actual_heavy_melee_hits_once_and_respects_wall_team_and_range(self):
        definition = next(row for row in self.catalog.values() if row.name == "Heavy Assault")
        for mode in ("hit", "wall", "ally", "range"):
            with self.subTest(mode=mode):
                distance = 6 if mode == "range" else 2.5
                run, player, bot, _ = self.create_fight(definition, distance=-distance)
                match = run.match
                fight = match.combat
                x, y, z = player.mover.position
                match.game_map = replace(match.game_map, free_for_all=False)
                run.step({player: (*aim(fight._eye(player), (x, y + 1, z - distance)), 0)})
                self.assertEqual(npc_control.start(match, player, action="melee"), bot.entity)
                if mode == "wall":
                    plane = z - distance / 2
                    match.collision_world = World(
                        box(x - 10, x + 10, y - 1, y, z - 10, z + 10)
                        + wall(x - 5, plane, x + 5, plane, y0=y, y1=y + 5)
                    )
                elif mode == "ally":
                    bot.team = player.team
                health = helpers.JunkratProjectileTests.health(self, run, player)
                for _ in range(30):
                    run.step({})
                self.assertIn(bot.entity, fight.npc_melee_seen)
                self.assertEqual(health.total, 120 if mode == "hit" else 200)
                for _ in range(90):
                    run.step({})
                self.assertEqual(health.total, 120 if mode == "hit" else 200)

    def test_actual_npc_melee_cancel_and_cooldown_require_fresh_input(self):
        for name, damage in (("Heavy Assault", 80), ("Trooper", 30)):
            with self.subTest(name=name):
                definition = next(row for row in self.catalog.values() if row.name == name)
                run, player, bot, _ = self.create_fight(definition, distance=-2.5)
                fight = run.match.combat
                x, y, z = bot.position
                angles = aim(fight._eye(player), (x, y + 1, z))
                run.step({player: (*angles, 0)})
                self.assertEqual(npc_control.start(run.match, player, action="melee"), bot.entity)
                self.assertEqual(npc_control.stop(run.match), 1)
                for _ in range(80):
                    run.step({player: (*angles, 0)})
                self.assertNotIn(bot.entity, fight.npc_melee_seen)
                self.assertEqual(npc_control.start(run.match, player, action="melee"), bot.entity)
                for _ in range(10):
                    run.step({player: (*angles, 0)})
                instance = heavy_charge.melee_instance(bot)
                ability = heavy_charge.melee_ability(instance)
                self.assertGreater(ability.remaining(), 0)
                self.assertEqual(npc_control.stop(run.match), 1)
                with self.assertRaisesRegex(ValueError, "cooling down"):
                    npc_control.start(run.match, player, action="melee")
                for _ in range(80):
                    run.step({player: (*angles, 0)})
                self.assertNotIn(bot.entity, fight.npc_melee_seen)
                health = helpers.JunkratProjectileTests.health(self, run, player)
                self.assertEqual(health.total, 200)
                self.assertEqual(ability.remaining(), 0)
                self.assertEqual(npc_control.start(run.match, player, action="melee"), bot.entity)
                for _ in range(35):
                    run.step({})
                self.assertEqual(health.total, 200 - damage)

    def test_actual_sniper_switches_weapon_slots_and_hits_with_each(self):
        definition = next(row for row in self.catalog.values() if row.name == "Sniper")
        run, player, bot, _ = self.create_fight(definition, distance=-15)
        match = run.match
        fight = match.combat
        health = helpers.JunkratProjectileTests.health(self, run, player)
        commands = {player: (-32768, 0, 0)}
        run.step(commands)
        component = bot.body_script.component
        self.assertEqual(component.vars[31].value(), 1)
        with patch.object(fight, "_shoot", wraps=fight._shoot) as shoot:
            self.assertEqual(npc_control.start(match, player, action="secondary"), bot.entity)
            for _ in range(45):
                run.step(commands)
                if health.total < 200:
                    break
            self.assertEqual(component.vars[31].value(), 2)
            self.assertEqual(health.total, 195)
            shoot.assert_called_once()
            state = shoot.call_args.args[2]
            self.assertEqual((state.instance.graph.index, state.node.pos), (0x1134, 6))

            self.assertEqual(npc_control.start(match, player), bot.entity)
            for _ in range(230):
                run.step(commands)
                if health.total < 195:
                    break
            self.assertEqual(component.vars[31].value(), 1)
            self.assertEqual(health.total, 95)
            self.assertEqual(shoot.call_count, 2)
            state = shoot.call_args.args[2]
            self.assertEqual((state.instance.graph.index, state.node.pos), (0x1122, 6))

            self.assertEqual(npc_control.start(match, player, action="secondary"), bot.entity)
            for _ in range(8):
                run.step(commands)
            self.assertEqual(npc_control.stop(match), 1)
            for _ in range(150):
                run.step(commands)
            self.assertFalse(bot.attack_input)
            self.assertEqual(shoot.call_count, 2)
            self.assertEqual(health.total, 95)

    def test_auto_spawn_sniper_switches_weapons_as_junkrat_moves(self):
        definition = next(row for row in self.catalog.values() if row.name == "Sniper")
        run, player, bot, _ = self.create_fight(definition, auto_spawn=True)
        fight = run.match.combat
        health = helpers.JunkratProjectileTests.health(self, run, player)
        component = bot.body_script.component

        with patch.object(fight, "_shoot", wraps=fight._shoot) as shoot:
            for _ in range(160):
                run.step({})
                if health.total < 200:
                    break
            self.assertEqual(component.vars[31].value(), 2)
            self.assertEqual(health.total, 195)
            shoot.assert_called_once()
            state = shoot.call_args.args[2]
            self.assertEqual((state.instance.graph.index, state.node.pos), (0x1134, 6))

            for _ in range(30):
                run.frame += 1
                run.match.update(run.frame * .016, run.frame)
                if component.vars[31].value() == 1:
                    break
                player.take_commands([Command(run.frame, forward=-127)])
            self.assertEqual(component.vars[31].value(), 1)
            self.assertFalse(bot.attack_input)
            shoot.assert_called_once()
            for _ in range(160):
                run.step({})
                if health.total < 195:
                    break
            self.assertEqual(health.total, 95)
            self.assertEqual(bot.attack_target, player.entity)
            self.assertEqual(shoot.call_count, 2)
            state = shoot.call_args.args[2]
            self.assertEqual((state.instance.graph.index, state.node.pos), (0x1122, 6))

    def test_projected_barrier_intercepts_each_actual_sniper_weapon_slot(self):
        definition = next(row for row in self.catalog.values() if row.name == "Sniper")
        for action, graph_index, damage in (("primary", 0x1122, 100), ("secondary", 0x1134, 5)):
            with self.subTest(action=action):
                run, player, bot, _ = self.create_fight(definition, distance=-15)
                match = run.match
                fight = match.combat
                match.game_map = replace(match.game_map, free_for_all=False)
                x, y, z = player.mover.position
                zarya = match.add_player(2, "Zarya", SOLDIER, player.team, False)
                in_world(match, zarya)
                zarya.client.bot_stream = lambda entity: None
                zarya.spawn = ((x + 5, y, z), zarya.spawn[1])
                match.switch_hero(zarya, find_hero("Zarya"))
                run.players.append(zarya)
                commands = {player: (-32768, 0, 0)}
                for _ in range(25):
                    run.step(commands)
                body = next(target for target in fight._targets() if target.owner is player)
                a, b = body.segment()
                centre = tuple((lo + hi) * .5 for lo, hi in zip(a, b, strict=True))
                cast_angles = aim(fight._eye(zarya), centre)

                with patch.object(fight, "_shoot", wraps=fight._shoot) as shoot:
                    self.assertEqual(npc_control.start(match, player, action=action), bot.entity)
                    for _ in range(230):
                        run.step(commands)
                        if any(rocket.shooter is bot for rocket in fight.rockets):
                            break
                    round_in_flight, = (rocket for rocket in fight.rockets if rocket.shooter is bot)
                    shoot.assert_called_once()
                    first_state = shoot.call_args.args[2]
                    self.assertEqual((first_state.instance.graph.index, first_state.node.pos), (graph_index, 6))
                    self.assertEqual(fight.vitals_of(body).total, 200)
                    run.step({**commands, zarya: (*cast_angles, 16)})
                    fight.barriers.sync(zarya)
                    bubble, = fight.barriers.live.values()
                    self.assertEqual(bubble.body, player.body)
                    for _ in range(40):
                        run.step(commands)
                        if round_in_flight not in fight.rockets:
                            break
                    self.assertNotIn(round_in_flight, fight.rockets)
                    self.assertEqual(bubble.health, 200 - damage)
                    self.assertEqual(fight.vitals_of(body).total, 200)
                    self.assertFalse(fight.is_stunned(player))
                    self.assertEqual(npc_control.stop(match), 1)

                    for _ in range(150):
                        if not bubble.phase.active:
                            break
                        run.step(commands)
                    self.assertFalse(bubble.phase.active)
                    self.assertFalse(bot.attack_input)
                    shoot.assert_called_once()
                    self.assertEqual(fight.vitals_of(body).total, 200)
                    self.assertEqual(npc_control.start(match, player, action=action), bot.entity)
                    for _ in range(230):
                        run.step(commands)
                        if fight.vitals_of(body).total < 200:
                            break
                    self.assertEqual(shoot.call_count, 2)
                    fresh_state = shoot.call_args.args[2]
                    self.assertEqual((fresh_state.instance.graph.index, fresh_state.node.pos), (graph_index, 6))
                    self.assertEqual(fight.vitals_of(body).total, 200 - damage)

    def test_allied_genji_returns_each_sniper_round_and_protects_junkrat(self):
        definition = next(row for row in self.catalog.values() if row.name == "Sniper")
        for action, damage in (("primary", 100), ("secondary", 5)):
            with self.subTest(action=action):
                run, player, bot, npc_vitals = self.create_fight(definition, distance=-15)
                match = run.match
                fight = match.combat
                match.game_map = replace(match.game_map, free_for_all=False)
                x, y, z = player.mover.position
                genji = match.add_player(2, "Genji", SOLDIER, player.team, False)
                in_world(match, genji)
                genji.client.bot_stream = lambda entity: None
                match.switch_hero(genji, find_hero("Genji"))
                run.players.append(genji)
                commands = {player: (-32768, 0, 0), genji: (-32768, 0, 0)}
                for _ in range(25):
                    run.step(commands)
                player_vitals = helpers.JunkratProjectileTests.health(self, run, player)
                genji_vitals = helpers.JunkratProjectileTests.health(self, run, genji)
                before = npc_vitals.total

                self.assertEqual(npc_control.start(match, player, action=action), bot.entity)
                genji.mover = Mover((x, y, z - 5), world=match.collision_world,
                                    data=mover_data(genji.hero.body))
                for _ in range(230):
                    run.step(commands)
                    if any(rocket.shooter is bot for rocket in fight.rockets):
                        break
                projectile, = (rocket for rocket in fight.rockets if rocket.shooter is bot)
                self.assertTrue(projectile.direct.deflectable)
                self.assertEqual(projectile.direct.amount, damage)
                original_source = projectile.source_entity
                self.assertEqual(original_source, bot.entity)
                run.step({**commands, genji: (-32768, 0, 16)})
                deflect = next(instance for instance in genji.body_script.component.instances.values()
                               if instance.graph.index == 0x03A3)
                for _ in range(35):
                    if projectile.shooter is genji:
                        break
                    run.step(commands)
                self.assertTrue(deflect.states[10].active)
                self.assertIs(projectile.shooter, genji)
                self.assertEqual((projectile.team, projectile.source_entity), (player.team, genji.body))
                self.assertIsNone(projectile.shot)
                self.assertEqual(npc_control.stop(match), 1)
                self.assertIn(projectile, fight.rockets)
                for _ in range(35):
                    run.step(commands)
                    if projectile not in fight.rockets:
                        break
                self.assertNotIn(projectile, fight.rockets)
                self.assertEqual(npc_vitals.total, before - damage)
                self.assertEqual((player_vitals.total, genji_vitals.total), (200, 200))
                hit, = genji.client.messages(combat.HIT)
                self.assertEqual(hit["+0x78"]["+0x0"]["+0x0"], bot.entity)
                self.assertEqual(hit["+0x78"]["+0x4"], damage)
                self.assertFalse(player.client.messages(combat.HIT))

    def test_allied_genji_redirects_tagged_trooper_and_heavy_hitscan(self):
        for name, damage in (("Trooper", 20), ("Heavy Assault", 5)):
            for yaw, returned, suffered in ((-32768, damage, 0), (-24576, 0, 0), (0, 0, damage)):
                with self.subTest(npc=name, yaw=yaw):
                    definition = next(row for row in self.catalog.values() if row.name == name)
                    run, player, bot, npc_vitals = self.create_fight(definition, distance=-10)
                    match = run.match
                    fight = match.combat
                    match.game_map = replace(match.game_map, free_for_all=False)
                    x, y, z = player.mover.position
                    genji = match.add_player(2, "Genji", SOLDIER, player.team, False)
                    in_world(match, genji)
                    genji.client.bot_stream = lambda entity: None
                    match.switch_hero(genji, find_hero("Genji"))
                    run.players.append(genji)
                    commands = {player: (-32768, 0, 0), genji: (yaw, 0, 0)}
                    for _ in range(25):
                        run.step(commands)
                    self.assertEqual(npc_control.start(match, player), bot.entity)
                    genji.mover = Mover((x, y, z - 4), world=match.collision_world,
                                        data=mover_data(genji.hero.body))
                    run.step({**commands, genji: (yaw, 0, 16)})
                    deflect = next(instance for instance in genji.body_script.component.instances.values()
                                   if instance.graph.index == 0x03A3)
                    genji_vitals = helpers.JunkratProjectileTests.health(self, run, genji)
                    junkrat_vitals = helpers.JunkratProjectileTests.health(self, run, player)
                    before = npc_vitals.total
                    with patch.object(fight, "_shoot", wraps=fight._shoot) as shoot:
                        for _ in range(110):
                            run.step(commands)
                            if shoot.called:
                                break
                        shoot.assert_called_once()
                    self.assertTrue(deflect.states[10].active)
                    self.assertEqual((genji_vitals.total, junkrat_vitals.total), (200 - suffered, 200))
                    self.assertEqual(npc_vitals.total, before - returned)
                    hits = genji.client.messages(combat.HIT)
                    if returned:
                        hit, = hits
                        self.assertEqual(hit["+0x78"]["+0x0"]["+0x0"], bot.entity)
                        self.assertEqual(hit["+0x78"]["+0x4"], returned)
                    else:
                        self.assertFalse(hits)
                    self.assertFalse(player.client.messages(combat.HIT))

    def test_allied_sigma_absorbs_actual_trooper_and_heavy_hitscan(self):
        for name, damage in (("Trooper", 20), ("Heavy Assault", 5)):
            with self.subTest(npc=name):
                definition = next(row for row in self.catalog.values() if row.name == name)
                run, player, bot, _ = self.create_fight(definition, distance=-10)
                match = run.match
                fight = match.combat
                match.game_map = replace(match.game_map, free_for_all=False)
                x, y, z = player.mover.position
                sigma = match.add_player(2, "Sigma", SOLDIER, player.team, False)
                in_world(match, sigma)
                sigma.client.bot_stream = lambda entity: None
                match.switch_hero(sigma, find_hero("Sigma"))
                run.players.append(sigma)
                commands = {player: (-32768, 0, 0), sigma: (-32768, 0, 0)}
                for _ in range(25):
                    run.step(commands)
                self.assertEqual(npc_control.start(match, player), bot.entity)
                sigma.mover = Mover((x + 0.75, y, z - 4), world=match.collision_world,
                                    data=mover_data(sigma.hero.body))
                run.step({**commands, sigma: (-32768, 0, 8)})
                instance = next(i for i in sigma.body_script.component.instances.values()
                                if i.graph.index == 0x1D02)
                self.assertTrue(instance.states[1].active)
                player_vitals = helpers.JunkratProjectileTests.health(self, run, player)
                sigma_vitals = helpers.JunkratProjectileTests.health(self, run, sigma)
                with patch.object(fight, "_shoot", wraps=fight._shoot) as shoot:
                    for _ in range(110):
                        run.step(commands)
                        if shoot.called:
                            break
                    shoot.assert_called_once()
                self.assertEqual(instance.vars[21312].value(), damage)
                self.assertEqual((player_vitals.total, sigma_vitals.total), (200, 400))
                self.assertEqual(npc_control.stop(match), 1)
                for _ in range(160):
                    if not instance.states[1].active:
                        break
                    run.step(commands)
                self.assertFalse(instance.states[1].active)
                self.assertEqual(npc_control.start(match, player), bot.entity)
                for _ in range(110):
                    run.step(commands)
                    if player_vitals.total < 200:
                        break
                self.assertEqual(player_vitals.total, 200 - damage)

    def test_allied_sigma_absorbs_each_sniper_round_and_protects_junkrat(self):
        definition = next(row for row in self.catalog.values() if row.name == "Sniper")
        for action, damage in (("primary", 100), ("secondary", 5)):
            with self.subTest(action=action):
                run, player, bot, _ = self.create_fight(definition, distance=-15)
                match = run.match
                fight = match.combat
                match.game_map = replace(match.game_map, free_for_all=False)
                x, y, z = player.mover.position
                sigma = match.add_player(2, "Sigma", SOLDIER, player.team, False)
                in_world(match, sigma)
                sigma.client.bot_stream = lambda entity: None
                match.switch_hero(sigma, find_hero("Sigma"))
                run.players.append(sigma)
                commands = {player: (-32768, 0, 0), sigma: (-32768, 0, 0)}
                for _ in range(25):
                    run.step(commands)
                player_vitals = helpers.JunkratProjectileTests.health(self, run, player)
                sigma_vitals = helpers.JunkratProjectileTests.health(self, run, sigma)
                before = sigma_vitals.total

                self.assertEqual(npc_control.start(match, player, action=action), bot.entity)
                sigma.mover = Mover((x + 0.75, y, z - 5), world=match.collision_world,
                                    data=mover_data(sigma.hero.body))
                for _ in range(230):
                    run.step(commands)
                    if any(rocket.shooter is bot for rocket in fight.rockets):
                        break
                projectile, = (rocket for rocket in fight.rockets if rocket.shooter is bot)
                self.assertEqual(projectile.direct.amount, damage)
                run.step({**commands, sigma: (-32768, 0, 8)})
                instance = next(i for i in sigma.body_script.component.instances.values() if i.graph.index == 0x1D02)
                self.assertTrue(instance.states[1].active)
                for _ in range(40):
                    run.step(commands)
                    if projectile not in fight.rockets:
                        break
                self.assertNotIn(projectile, fight.rockets)
                self.assertEqual(npc_control.stop(match), 1)
                self.assertEqual(instance.vars[21312].value(), damage)
                self.assertEqual(player_vitals.total, 200)
                self.assertEqual(sigma_vitals.total, before)

    def test_actual_sniper_secondary_rejects_new_ally_and_stop_retires_round(self):
        definition = next(row for row in self.catalog.values() if row.name == "Sniper")
        for mode in ("ally", "stop"):
            with self.subTest(mode=mode):
                run, player, bot, _ = self.create_fight(definition, distance=-15)
                match = run.match
                fight = match.combat
                match.game_map = replace(match.game_map, free_for_all=False)
                health = helpers.JunkratProjectileTests.health(self, run, player)
                commands = {player: (-32768, 0, 0)}
                run.step(commands)
                console = npc_console.Console({})
                result = console.press("0", match, player, attack="secondary")
                self.assertEqual(result["entity"], bot.entity)
                for _ in range(45):
                    run.step(commands)
                    if any(rocket.shooter is bot for rocket in fight.rockets):
                        break
                self.assertTrue(any(rocket.shooter is bot for rocket in fight.rockets))
                if mode == "ally":
                    bot.team = player.team
                else:
                    self.assertEqual(console.press("{", match, player)["count"], 1)
                    self.assertFalse(any(rocket.shooter is bot for rocket in fight.rockets))
                for _ in range(45):
                    run.step(commands)
                self.assertFalse(bot.attack_input)
                self.assertEqual(health.total, 200)

    def test_actual_sniper_windup_cancels_and_fresh_shot_obeys_projectile_los(self):
        definition = next(row for row in self.catalog.values() if row.name == "Sniper")
        run, player, bot, _ = self.create_fight(definition, distance=-15)
        match = run.match
        fight = match.combat
        health = helpers.JunkratProjectileTests.health(self, run, player)
        x, y, z = player.mover.position
        run.step({player: (-32768, 0, 0)})
        original_world = match.collision_world

        with patch.object(fight, "_shoot", wraps=fight._shoot) as shoot:
            self.assertEqual(npc_control.start(match, player), bot.entity)
            for _ in range(60):
                run.step({player: (-32768, 0, 0)})
            self.assertTrue(bot.attack_input)
            self.assertEqual(npc_control.stop(match), 1)
            for _ in range(140):
                run.step({player: (-32768, 0, 0)})
            shoot.assert_not_called()
            self.assertEqual(health.total, 200)

            self.assertEqual(npc_control.start(match, player), bot.entity)
            for _ in range(150):
                run.step({player: (-32768, 0, 0)})
                if shoot.called:
                    break
            shoot.assert_called_once()
            shooter, _, state, _, _ = shoot.call_args.args
            self.assertIs(shooter, bot)
            self.assertEqual((state.instance.graph.index, state.node.pos), (0x1122, 6))
            self.assertEqual(health.total, 200)
            plane = z - 7.5
            match.collision_world = World(
                box(x - 10, x + 10, y - 1, y, z - 20, z + 10)
                + wall(x - 5, plane, x + 5, plane, y0=y, y1=y + 5)
            )
            for _ in range(15):
                run.step({player: (-32768, 0, 0)})
            self.assertFalse(bot.attack_input)
            self.assertEqual(health.total, 200)
            self.assertFalse(any(rocket.shooter is bot for rocket in fight.rockets))

            match.collision_world = original_world
            for _ in range(160):
                run.step({player: (-32768, 0, 0)})
                if health.total < 200:
                    break
            self.assertEqual(shoot.call_count, 2)
            self.assertEqual(health.total, 100)

    def test_auto_assassin_lunge_launches_and_pins_without_injected_messages(self):
        definition = next(row for row in self.catalog.values() if row.name == "Assassin")
        run, player, bot, _ = self.create_fight(definition, auto_spawn=True)
        fight = run.match.combat
        health = helpers.JunkratProjectileTests.health(self, run, player)
        position = tuple(bot.position)
        launch = next(i for i in bot.body_script.component.instances.values() if i.graph.index == 0x13C3)
        run.step({})
        self.assertIsNotNone(bot.lunge)
        self.assertEqual(bot.lunge.target, player.body)
        self.assertEqual(tuple(bot.position), position)
        self.assertFalse(bot.attack_input)
        for _ in range(70):
            run.step({})
            if bot.displacement is not None:
                break
            self.assertEqual(tuple(bot.position), position)
            self.assertEqual(health.total, 200)
        self.assertIsNotNone(bot.displacement)
        self.assertTrue(bot.lunge.launched)
        self.assertAlmostEqual(launch.vars[1002].value().z, -12.5)
        for _ in range(75):
            run.step({})
            if fight.pins.get(bot.entity) == player.body:
                break
        self.assertEqual(fight.pins.get(bot.entity), player.body)
        self.assertEqual(health.total, 175)
        self.assertIsNotNone(bot.lunge)
        self.assertEqual(npc_control.stop(run.match), 1)
        self.assertFalse(fight.pins)
        stopped_x, _, stopped_z = bot.position
        for _ in range(40):
            run.step({})
        self.assertEqual((bot.position[0], bot.position[2]), (stopped_x, stopped_z))
        self.assertIsNone(bot.displacement)
        self.assertEqual(health.total, 175)

    def test_auto_assassin_stop_or_invalid_target_cancels_lunge_windup(self):
        definition = next(row for row in self.catalog.values() if row.name == "Assassin")
        for cancel in ("stop", "wall", "range", "team"):
            with self.subTest(cancel=cancel):
                run, player, bot, _ = self.create_fight(definition, auto_spawn=True)
                match = run.match
                fight = match.combat
                health = helpers.JunkratProjectileTests.health(self, run, player)
                run.step({})
                self.assertIsNotNone(bot.lunge)
                if cancel == "stop":
                    self.assertEqual(npc_control.stop(match), 1)
                elif cancel == "team":
                    match.game_map = replace(match.game_map, free_for_all=False)
                    player.team = bot.team
                elif cancel == "range":
                    x, y, z = bot.position
                    player.mover = Mover((x, y, z - 20), world=match.collision_world,
                                         data=mover_data(player.hero.body))
                else:
                    x, y, z = player.mover.position
                    match.collision_world = World(
                        box(x - 12, x + 12, y - 1, y, z - 12, z + 12)
                        + wall(x - 13, z + 2, x + 13, z + 2, y0=y, y1=y + 4)
                    )
                for _ in range(70):
                    run.step({})
                    self.assertIsNone(bot.displacement)
                self.assertIsNone(bot.lunge)
                self.assertFalse(fight.pins)
                self.assertEqual(health.total, 200)
                if cancel == "stop":
                    while run.frame < bot.lunge_ready:
                        run.step({})
                    self.assertEqual(npc_control.start(match, player, action="auto"), bot.entity)
                    for _ in range(70):
                        run.step({})
                        if bot.displacement is not None:
                            break
                    self.assertIsNotNone(bot.displacement)
                    self.assertTrue(bot.lunge.launched)

    def test_auto_assassin_approaches_before_pin_and_respects_low_wall(self):
        definition = next(row for row in self.catalog.values() if row.name == "Assassin")
        for blocked in (False, True):
            with self.subTest(blocked=blocked):
                run, player, bot, _ = self.create_fight(definition, auto_spawn=True)
                match = run.match
                fight = match.combat
                start = tuple(bot.position)
                # Isolate walking while a previous lunge is still recovering.
                bot.lunge_ready = run.frame + 160
                x, y, z = player.mover.position
                if blocked:
                    match.collision_world = World(
                        box(x - 12, x + 12, y - 1, y, z - 12, z + 12)
                        + wall(x - 13, z + 2, x + 13, z + 2, y0=y, y1=y + 1.15)
                    )
                health = helpers.JunkratProjectileTests.health(self, run, player)
                run.step({})
                self.assertFalse(bot.attack_input)
                self.assertEqual(health.total, 200)
                for _ in range(145):
                    run.step({})
                    if fight.pins.get(bot.entity) == player.body:
                        break
                self.assertEqual(bot.attack_target, player.entity)
                self.assertLess(bot.position[2], start[2])
                if blocked:
                    self.assertGreaterEqual(bot.position[2], z + 2)
                    self.assertFalse(bot.attack_input)
                    self.assertNotIn(bot.entity, fight.pins)
                    self.assertEqual(health.total, 200)
                else:
                    self.assertEqual(fight.pins.get(bot.entity), player.body)
                    self.assertEqual(health.total, 175)
                self.assertEqual(npc_control.stop(match), 1)
                stopped = tuple(bot.position)
                for _ in range(20):
                    run.step({})
                self.assertEqual(tuple(bot.position), stopped)
                self.assertFalse(fight.pins)
                self.assertEqual(bot.velocity, (0.0, 0.0, 0.0))

    def test_auto_assassin_routes_around_wall_or_teammate_before_pin(self):
        definition = next(row for row in self.catalog.values() if row.name == "Assassin")
        for obstacle in ("wall", "teammate"):
            with self.subTest(obstacle=obstacle):
                run, player, bot, _ = self.create_fight(definition, auto_spawn=True)
                match = run.match
                fight = match.combat
                match.game_map = replace(match.game_map, free_for_all=False)
                x, y, z = player.mover.position
                if obstacle == "wall":
                    match.collision_world = World(
                        box(x - 12, x + 12, y - 1, y, z - 12, z + 12)
                        + wall(x - 2, z + 2, x + 2, z + 2, y0=y, y1=y + 4)
                    )
                else:
                    npc.spawn(match, definition, (x, y, z + 2.5), team=bot.team)
                health = helpers.JunkratProjectileTests.health(self, run, player)
                lateral = 0.0
                searched = False
                for _ in range(260):
                    run.step({})
                    lateral = max(lateral, abs(bot.position[0] - x))
                    searched |= bot.pursuit is not None
                    if fight.pins.get(bot.entity) == player.body:
                        break
                    self.assertEqual(health.total, 200)
                self.assertTrue(searched)
                self.assertGreater(lateral, 2 if obstacle == "wall" else .75)
                self.assertEqual(fight.pins.get(bot.entity), player.body)
                self.assertEqual(health.total, 175)
                self.assertIsNone(bot.pursuit)

    def test_steel_trap_stops_assassin_approach_until_root_releases(self):
        definition = next(row for row in self.catalog.values() if row.name == "Assassin")
        run, player, bot, bot_health = self.create_fight(definition, distance=-8)
        match = run.match
        fight = match.combat
        run.step({player: (0, 0, 0x10)})
        for _ in range(70):
            run.step({})
        trap, = fight.junkrat.traps.values()
        self.assertTrue(trap.parked)
        self.assertGreaterEqual(fight.now, trap.armed_at)
        x, _, z = trap.flight.position
        y = player.mover.position[1]
        player.mover = Mover((x, y, z + 5), world=match.collision_world,
                             data=mover_data(player.hero.body))
        bot.position = (x, y, z - 6)
        angles = aim(fight._eye(player), (x, y + 1.4, z - 6))
        run.step({player: (*angles, 0)})
        self.assertEqual(npc_control.start(match, player, action="auto"), bot.entity)

        for _ in range(160):
            run.step({})
            if trap.victim is bot:
                break
        self.assertIs(trap.victim, bot)
        self.assertEqual(bot_health.total, 420)
        self.assertTrue(fight.junkrat.rooted(bot.entity, run.frame))
        health = helpers.JunkratProjectileTests.health(self, run, player)
        position = tuple(bot.position)
        for _ in range(30):
            run.step({})
        self.assertEqual(tuple(bot.position), position)
        self.assertFalse(bot.attack_input)
        self.assertEqual(health.total, 200)

        for _ in range(320):
            run.step({})
            if fight.pins.get(bot.entity) == player.body:
                break
        self.assertFalse(fight.junkrat.rooted(bot.entity, run.frame))
        self.assertEqual(fight.pins.get(bot.entity), player.body)
        self.assertEqual(health.total, 175)

    def test_actual_assassin_pin_damages_and_controls_junkrat_then_releases(self):
        definition = next(row for row in self.catalog.values() if row.name == "Assassin")
        run, player, bot, bot_health = self.create_fight(definition, distance=2.5)
        fight = run.match.combat
        target = next(target for target in fight._targets() if target.owner is player)
        health = fight.vitals_of(target)
        self.assertEqual(npc_control.start(run.match, player), bot.entity)
        for _ in range(100):
            run.step({})
            if fight.pins.get(bot.entity) == player.body:
                break
        self.assertEqual(fight.pins.get(bot.entity), player.body)
        self.assertEqual(health.total, 175)
        victim_component = player.body_script.component
        victim_graphs = [i for i in victim_component.instances.values() if i.graph.index == 0x1177]
        self.assertEqual(len(victim_graphs), 1)
        victim_graph, = victim_graphs
        self.assertEqual(victim_graph.source_entity(), bot.entity)
        self.assertTrue(any(state.active and state.node.pos in (2, 3)
                            for state in victim_graph.states.values()))
        instance = next(i for i in bot.body_script.component.instances.values() if i.graph.index == 0x1157)
        phase = instance.node_state(instance.graph.node(15))
        for _ in range(100):
            if phase.active:
                break
            run.step({})
        self.assertTrue(phase.active)
        before = health.total
        feet = tuple(player.mover.position)
        for _ in range(10):
            run.frame += 1
            run.match.update(run.frame * combat.TICK, run.frame)
            player.take_commands([Command(run.frame, buttons=FIRE, forward=127)])
        self.assertAlmostEqual(before - health.total, 16, delta=.001)
        self.assertEqual(tuple(player.mover.position), feet)
        self.assertEqual(bot_health.total, 500)
        self.assertFalse(fight.rockets)
        npc_control.stop(run.match)
        self.assertNotIn(victim_graph.id, victim_component.instances)
        stopped = health.total
        for _ in range(15):
            run.frame += 1
            run.match.update(run.frame * combat.TICK, run.frame)
            player.take_commands([Command(run.frame, forward=127)])
        self.assertFalse(fight.pins)
        self.assertEqual(health.total, stopped)
        self.assertNotEqual(tuple(player.mover.position), feet)

    @unittest.skipUnless(os.environ.get("ORPHEUS_ENFORCER_GRAPHS"), "prepared Enforcer controller bundle required")
    def test_auto_spawn_engages_junkrat_without_manual_start(self):
        self.addCleanup(clear_graph_cache)
        with patch.object(graph, "GRAPHS_PATH", Path(os.environ["ORPHEUS_ENFORCER_GRAPHS"])):
            clear_graph_cache()
            for definition in self.catalog.values():
                with self.subTest(npc=definition.name):
                    run, player, bot, _ = self.create_fight(definition, auto_spawn=True)
                    self.assertTrue(bot.firing)
                    self.assertIsNone(bot.attack_target)
                    health = helpers.JunkratProjectileTests.health(self, run, player)
                    for _ in range(160):
                        run.step({})
                        if health.total < 200:
                            break
                    self.assertEqual(bot.attack_target, player.entity)
                    self.assertLess(health.total, 200)
                    self.assertEqual(npc_control.stop(run.match), 1)
                    self.assertFalse(bot.firing)

    @unittest.skipUnless(os.environ.get("ORPHEUS_ENFORCER_GRAPHS"), "prepared Enforcer controller bundle required")
    def test_auto_engagement_uses_closest_hostile_and_real_attack(self):
        self.addCleanup(clear_graph_cache)
        with patch.object(graph, "GRAPHS_PATH", Path(os.environ["ORPHEUS_ENFORCER_GRAPHS"])):
            clear_graph_cache()
            for definition in self.catalog.values():
                with self.subTest(npc=definition.name):
                    # Keep the operator outside the Heavy Assault's melee sweep.
                    run, operator, bot, _ = self.create_fight(definition, distance=6)
                    match = run.match
                    fight = match.combat
                    match.game_map = replace(match.game_map, free_for_all=False)
                    target = match.add_player(2, "Junkrat Target", SOLDIER, operator.team, False)
                    ally = match.add_player(3, "NPC Ally", SOLDIER, bot.team, False)
                    for player in (target, ally):
                        in_world(match, player)
                        player.client.bot_stream = lambda entity: None
                        player.client.queue_bot_death = lambda *args: None
                        player.client.queue_bot_death_update = lambda *args: None
                        match.switch_hero(player, find_hero("Junkrat"))
                        run.players.append(player)
                    x, y, z = bot.position
                    target.mover.position[:] = [x + 1.5, y, z - 1.5]
                    ally.mover.position[:] = [x - .8, y, z - .6]
                    result = npc_console.Console({}).press("0", match, operator, attack="auto")
                    self.assertEqual(result["entity"], bot.entity)
                    self.assertIsNone(bot.attack_target)
                    target_health = helpers.JunkratProjectileTests.health(self, run, target)
                    sniper = bot.body == npc_control.SNIPER_BODY
                    repositioned = False
                    # The close-range Sniper retreats before starting its rifle cycle.
                    for _ in range(256 if sniper else 160):
                        run.step({})
                        if sniper and npc_control.sniper_reposition.repositioning(bot):
                            repositioned = True
                            self.assertFalse(bot.attack_input)
                            self.assertEqual(target_health.total, 200)
                        if target_health.total < 200:
                            break
                    self.assertEqual(bot.attack_target, target.entity)
                    if sniper:
                        self.assertTrue(repositioned)
                        self.assertEqual(target_health.total, 100)
                    else:
                        self.assertLess(target_health.total, 200)
                    for unharmed in (operator, ally):
                        self.assertEqual(helpers.JunkratProjectileTests.health(self, run, unharmed).total, 200)
                    self.assertEqual(npc_control.stop(match), 1)
                    self.assertFalse(bot.firing)
                    self.assertFalse(bot.attack_input)

    def test_enforcer_death_volley_uses_authored_mode_gate_and_normal_combat(self):
        definition = next(row for row in self.catalog.values() if row.name == "Enforcer")
        mode = graph.mode_variables(PRACTICE_RANGE_MODE)
        cases = ((None, False), (True, True), (True, False), (False, True))
        for start_flag, death_enabled in cases:
            with self.subTest(start_flag=start_flag, death_enabled=death_enabled):
                with patch.dict(mode):
                    mode.pop(DEATH_VOLLEY_MODE_FLAG, None)
                    if start_flag is not None:
                        mode[DEATH_VOLLEY_MODE_FLAG] = start_flag
                    run, player, bot, bot_health = self.create_fight(definition, distance=6)
                    bot.yaw = -32768
                    fight = run.match.combat
                    health = helpers.JunkratProjectileTests.health(self, run, player)
                    component = bot.body_script.component
                    self.assertEqual(any(i.graph.index == DEATH_VOLLEY_GRAPH
                                         for i in component.instances.values()), start_flag is not None)
                    mode[DEATH_VOLLEY_MODE_FLAG] = death_enabled
                    for _ in range(350):
                        run.step({player: (0, 0, FIRE)})
                        if bot_health.dead:
                            break
                    self.assertTrue(bot_health.dead)
                    self.assertEqual(bot_health.killer, player.body)
                    flights = [rocket for rocket in fight.rockets if rocket.shooter is bot]
                    self.assertEqual(len(flights), int(death_enabled))
                    if flights:
                        self.assertEqual(flights[0].direct.amount, 25)
                        self.assertEqual(flights[0].speed, 30)
                    self.assertFalse(component.shots)
                    for _ in range(45):
                        run.step({})
                    self.assertEqual(health.total, 175 if death_enabled else 200)
                    self.assertFalse(any(rocket.shooter is bot for rocket in fight.rockets))
                    self.assertFalse(component.shots)

    def test_enforcer_missing_controller_rejects_attack_admission_without_allocating(self):
        definition = next(row for row in self.catalog.values() if row.name == "Enforcer")
        entity_body = graph.entity_body
        missing_controller = patch.object(
            graph, "entity_body",
            side_effect=lambda entity: None if entity == enforcer.CONTROLLER else entity_body(entity),
        )
        self.enterContext(missing_controller)
        run, player, existing, _ = self.create_fight(definition, distance=8)
        match = run.match
        console = npc_console.Console({"1": definition})
        self.assertTrue(console.press("=", match, player)["menu_open"])
        entity = match.combat.next_bot
        with self.assertRaisesRegex(ValueError, "227A controller is unavailable"):
            console.press("1", match, player, attack="auto")
        self.assertEqual(match.bots, [existing])
        self.assertEqual(match.combat.next_bot, entity)
        self.assertNotIn(entity, match.combat.vitals)
        self.assertNotIn(entity, match.combat.replacement_ticks)

        for attack in ("primary", "auto"):
            with self.subTest(attack=attack):
                with self.assertRaisesRegex(ValueError, "227A controller is unavailable"):
                    console.press("0", match, player, attack=attack)
                self.assertFalse(existing.firing)
                self.assertIsNone(existing.attack_target)
                self.assertNotIn("auto_engage", existing.spawner)

    def test_auto_engagement_switches_from_covered_target_to_visible_enemy(self):
        definition = next(row for row in self.catalog.values() if row.name == "Trooper")
        run, operator, bot, _ = self.create_fight(definition)
        match = run.match
        match.game_map = replace(match.game_map, free_for_all=False)
        target = match.add_player(2, "Junkrat Target", SOLDIER, operator.team, False)
        in_world(match, target)
        target.client.bot_stream = lambda entity: None
        match.switch_hero(target, find_hero("Junkrat"))
        run.players.append(target)
        x, y, z = bot.position
        # Use a nearer opponent who is still outside the Trooper's melee sweep.
        target.mover.position[:] = [x + 4.5, y, z]
        health = helpers.JunkratProjectileTests.health
        target_health = health(self, run, target)
        operator_health = health(self, run, operator)
        result = npc_console.Console({}).press("0", match, operator, attack="auto")
        self.assertEqual(result["entity"], bot.entity)
        for _ in range(120):
            run.step({})
            if target_health.total < 200:
                break
        self.assertEqual(bot.attack_target, target.entity)
        self.assertEqual((target_health.total, operator_health.total), (180, 200))
        floor = box(x - 15, x + 15, y - 1, y, z - 15, z + 15)
        occluder = wall(x + 1.5, z - 2, x + 1.5, z + 2, y0=y, y1=y + 6)
        match.collision_world = World(floor + occluder)
        run.step({})
        self.assertEqual(bot.attack_target, operator.entity)
        self.assertFalse(bot.attack_input)  # release before turning to the new target
        for _ in range(120):
            run.step({})
            if operator_health.total < 200:
                break
        self.assertEqual((target_health.total, operator_health.total), (180, 180))
        match.collision_world = World(floor)
        run.step({})
        self.assertEqual(bot.attack_target, operator.entity)

    def test_auto_engagement_waits_for_range_team_and_line_of_sight(self):
        definition = next(row for row in self.catalog.values() if row.name == "Trooper")
        run, operator, bot, _ = self.create_fight(definition)
        match = run.match
        match.game_map = replace(match.game_map, free_for_all=False)
        fight = match.combat
        target = match.add_player(2, "Junkrat Target", SOLDIER, operator.team, False)
        in_world(match, target)
        target.client.bot_stream = lambda entity: None
        match.switch_hero(target, find_hero("Junkrat"))
        run.players.append(target)
        x, y, z = bot.position
        target.mover.position[:] = [x + 101, y, z]
        self.assertEqual(npc_console.Console({}).press("0", match, operator, attack="auto")["entity"], bot.entity)
        operator.team = bot.team

        def waiting():
            for _ in range(20):
                run.step({})
            self.assertTrue(bot.firing)
            self.assertFalse(bot.attack_input)
            self.assertIsNone(bot.attack_target)
            self.assertEqual(helpers.JunkratProjectileTests.health(self, run, target).total, 200)

        waiting()
        target.mover.position[:] = [x + 5, y, z]
        floor = box(x - 15, x + 15, y - 1, y, z - 15, z + 15)
        match.collision_world = World(floor + wall(x + 2, z - 2, x + 2, z + 2, y0=y, y1=y + 6))
        waiting()
        match.collision_world = World(floor)
        target_health = helpers.JunkratProjectileTests.health(self, run, target)
        for _ in range(120):
            run.step({})
            if target_health.total < 200:
                break
        self.assertEqual(bot.attack_target, target.entity)
        self.assertEqual(target_health.total, 180)
        self.assertEqual(helpers.JunkratProjectileTests.health(self, run, operator).total, 200)

    @unittest.skipUnless(os.environ.get("ORPHEUS_ENFORCER_GRAPHS"), "prepared Enforcer controller bundle required")
    def test_engaged_npcs_reacquire_a_new_junkrat_after_killing_first(self):
        self.addCleanup(clear_graph_cache)
        with patch.object(graph, "GRAPHS_PATH", Path(os.environ["ORPHEUS_ENFORCER_GRAPHS"])):
            clear_graph_cache()
            for definition in self.catalog.values():
                with self.subTest(npc=definition.name):
                    self.check_npc_reacquires(definition)

    def check_npc_reacquires(self, definition):
        distance = 8 if definition.name == "Sniper" else 2.5
        run, player, bot, _ = self.create_fight(definition, distance=distance)
        match = run.match
        fight = match.combat
        health = helpers.JunkratProjectileTests.health(self, run, player)
        health.now[combat.HEALTH_POOL] = 1
        fight._sync_body_health(player, health)
        self.assertEqual(npc_control.start(match, player), bot.entity)
        for _ in range(160):
            run.step({})
            if health.dead:
                break
        self.assertTrue(health.dead)
        self.assertEqual(health.killer, bot.entity)
        run.step({})
        self.assertIsNone(bot.attack_target)
        self.assertFalse(bot.attack_input)

        replacement = match.add_player(2, "Replacement", SOLDIER, player.team, False)
        in_world(match, replacement)
        replacement.client.bot_stream = lambda entity: None
        replacement.client.queue_bot_death = lambda *args: None
        replacement.client.queue_bot_death_update = lambda *args: None
        self.assertTrue(match.switch_hero(replacement, find_hero("Junkrat")))
        replacement.mover.position[:] = player.mover.position
        run.players.append(replacement)
        fresh = helpers.JunkratProjectileTests.health(self, run, replacement)
        self.assertEqual(fresh.total, 200)
        for _ in range(160):
            run.step({})
            if fresh.total < 200:
                break
        self.assertEqual(bot.attack_target, replacement.entity)
        self.assertLess(fresh.total, 200)
        self.assertNotIn(player.body, fight.pins.values())
        self.assertEqual(npc_control.stop(match), 1)
        self.assertIsNone(bot.attack_target)

    def test_engaged_trooper_keeps_covered_target_then_skips_allies_and_occluded_enemies(self):
        definition = next(row for row in self.catalog.values() if row.name == "Trooper")
        run, first, bot, _ = self.create_fight(definition, distance=6)
        match = run.match
        fight = match.combat
        match.game_map = replace(match.game_map, free_for_all=False)
        self.assertEqual(npc_control.start(match, first), bot.entity)
        x, y, z = bot.position

        def add_junkrat(slot, team, position):
            player = match.add_player(slot, str(slot), SOLDIER, team, False)
            in_world(match, player)
            player.client.bot_stream = lambda entity: None
            self.assertTrue(match.switch_hero(player, find_hero("Junkrat")))
            player.mover.position[:] = position
            run.players.append(player)
            return player

        ally = add_junkrat(2, bot.team, (x - 2, y, z))
        covered = add_junkrat(3, first.team, (x, y, z - 3))
        visible = add_junkrat(4, first.team, (x + 4, y, z - 3))
        match.collision_world = World(
            box(x - 12, x + 12, y - 1, y, z - 12, z + 12)
            + wall(x - .65, z - 1, x + .65, z - 1, y0=y, y1=y + 4)
        )
        health = {actor: helpers.JunkratProjectileTests.health(self, run, actor)
                  for actor in (first, ally, covered, visible)}
        for _ in range(20):
            run.step({})
        self.assertEqual(bot.attack_target, first.entity)
        self.assertFalse(bot.attack_input)
        self.assertTrue(all(pool.total == 200 for pool in health.values()))

        first.team = bot.team
        for _ in range(80):
            run.step({})
            if health[visible].total < 200:
                break
        self.assertEqual(bot.attack_target, visible.entity)
        self.assertEqual(health[visible].total, 180)
        self.assertTrue(all(health[actor].total == 200 for actor in (first, ally, covered)))

    def test_actual_assassin_pin_kills_junkrat_then_reacquires_after_respawn(self):
        definition = next(row for row in self.catalog.values() if row.name == "Assassin")
        run, player, bot, _ = self.create_fight(definition, distance=2.5)
        fight = run.match.combat
        body = player.body
        health = helpers.JunkratProjectileTests.health(self, run, player)
        self.assertEqual(npc_control.start(run.match, player), bot.entity)
        for _ in range(100):
            run.step({})
            if fight.pins.get(bot.entity) == body:
                break
        self.assertEqual(fight.pins.get(bot.entity), body)
        victim_component = player.body_script.component
        victim_graph, = (i for i in victim_component.instances.values() if i.graph.index == 0x1177)
        for _ in range(250):
            run.step({})
            if health.dead:
                break
        self.assertTrue(health.dead)
        self.assertEqual(health.killer, bot.entity)
        self.assertFalse(fight.pins)
        self.assertNotIn(victim_graph.id, victim_component.instances)
        for _ in range(20):
            run.step({})
        self.assertFalse(bot.attack_input)
        self.assertIsNone(bot.body_script.component.world.manual_target)
        self.assertNotIn(body, fight.pins.values())

        self.assertEqual(npc_control.stop(run.match), 1)
        for _ in range(round(combat.respawn_seconds(run.match) / combat.TICK) + 5):
            run.step({})
            if player.body != body:
                break
        replacement = player.body
        self.assertNotEqual(replacement, body)
        self.assertFalse(fight.is_dead(player))
        self.assertFalse(fight.pins)
        self.assertNotIn(body, fight.vitals)

        x, y, z = player.mover.position
        bot.position = (x, y, z + 2.5)
        angles = aim(fight._eye(player), (x, y + 1, z + 2.5))
        run.step({player: (*angles, 0)})
        fresh = helpers.JunkratProjectileTests.health(self, run, player)
        self.assertEqual(fresh.total, 200)
        self.assertEqual(npc_control.start(run.match, player), bot.entity)
        for _ in range(100):
            run.step({})
            if fight.pins.get(bot.entity) == replacement:
                break
        self.assertEqual(fight.pins.get(bot.entity), replacement)
        self.assertEqual(fresh.total, 175)
        fresh_graph, = (i for i in player.body_script.component.instances.values() if i.graph.index == 0x1177)
        self.assertEqual(fresh_graph.source_entity(), bot.entity)
        self.assertIsNot(fresh_graph, victim_graph)

    def test_frag_in_flight_kills_pinning_assassin_and_releases_junkrat(self):
        definition = next(row for row in self.catalog.values() if row.name == "Assassin")
        run, player, bot, bot_health = self.create_fight(definition, distance=2.9)
        fight = run.match.combat
        bot_health.now[combat.HEALTH_POOL] = 15
        health = helpers.JunkratProjectileTests.health(self, run, player)
        x, y, z = bot.position
        angles = aim(fight._eye(player), (x, y + 0.9, z))
        run.step({player: (*angles, FIRE)})
        self.assertTrue(any(rocket.shooter is player for rocket in fight.rockets))
        self.assertEqual(npc_control.start(run.match, player), bot.entity)
        victim_component = player.body_script.component
        victim_graph = None
        for _ in range(40):
            run.step({})
            if fight.pins.get(bot.entity) == player.body:
                victim_graph, = (i for i in victim_component.instances.values() if i.graph.index == 0x1177)
            if bot.dead:
                break
        self.assertIsNotNone(victim_graph)
        self.assertNotIn(victim_graph.id, victim_component.instances)
        self.assertTrue(bot.dead)
        self.assertEqual(bot_health.killer, player.body)
        self.assertFalse(fight.pins)
        after = health.total
        for _ in range(40):
            run.step({})
        self.assertEqual(health.total, after)

    def test_teammate_quick_melee_rescues_junkrat_from_automatic_assassin_pin(self):
        definition = next(row for row in self.catalog.values() if row.name == "Assassin")
        for mode in ("ally", "outsider", "wall", "ffa"):
            with self.subTest(mode=mode):
                run, player, bot, bot_health = self.create_fight(definition, auto_spawn=True)
                match = run.match
                match.game_map = replace(match.game_map, free_for_all=mode == "ffa")
                fight = match.combat
                for _ in range(120):
                    run.step({})
                    if fight.pins.get(bot.entity) == player.body and bot.displacement is None:
                        break
                self.assertEqual(fight.pins.get(bot.entity), player.body)
                victim_component = player.body_script.component
                victim_graph, = (i for i in victim_component.instances.values() if i.graph.index == 0x1177)

                team = player.team if mode != "outsider" else max(player.team, bot.team) + 1
                rescuer = match.add_player(2, "Rescuer", SOLDIER, team, False)
                in_world(match, rescuer)
                rescuer.client.bot_stream = lambda entity: None
                run.players.append(rescuer)
                x, y, z = bot.position
                helpers.JunkratProjectileTests.place(self, run, rescuer, (x + 1.5, y, z))
                if mode == "wall":
                    match.collision_world = World(
                        box(x - 12, x + 12, y - 1, y, z - 12, z + 12)
                        + wall(x + .75, z - 1.5, x + .75, z + 1.5, y0=y, y1=y + 4)
                    )
                angles = aim(fight._eye(rescuer), (x, y + 1, z))
                for _ in range(20):
                    run.step({rescuer: (*angles, 0)})
                self.assertEqual(fight.pins.get(bot.entity), player.body)
                before = bot_health.total
                run.step({rescuer: (*angles, 0x800)})
                for _ in range(16):
                    if bot_health.total < before:
                        break
                    run.step({})
                self.assertEqual(before - bot_health.total, 0 if mode == "wall" else 30)
                if mode != "ally":
                    self.assertEqual(fight.pins.get(bot.entity), player.body)
                    self.assertIn(victim_graph.id, victim_component.instances)
                    continue

                self.assertNotIn(bot.entity, fight.pins)
                self.assertNotIn(victim_graph.id, victim_component.instances)
                self.assertFalse(fight.is_stunned(player))
                instance = next(i for i in bot.body_script.component.instances.values() if i.graph.index == 0x1157)
                recovery = expr.to_float(instance.evaluate(instance.graph.node(37).field("m_timeout")))
                deadline = run.frame + math.ceil(recovery / combat.TICK)
                self.assertEqual(fight.stuns[bot.entity], deadline)
                health = helpers.JunkratProjectileTests.health(self, run, player)
                released_at = health.total
                run.step({player: (-32768, 0, FIRE)})
                self.assertTrue(any(rocket.shooter is player for rocket in fight.rockets))
                while run.frame + 1 < deadline:
                    run.step({})
                    self.assertNotIn(bot.entity, fight.pins)
                    self.assertEqual(health.total, released_at)
                for _ in range(100):
                    run.step({})
                    if fight.pins.get(bot.entity) == player.body:
                        break
                self.assertEqual(fight.pins.get(bot.entity), player.body)
                self.assertEqual(health.total, released_at - 25)

    def test_removing_a_live_assassin_releases_junkrat_and_ends_pin_damage(self):
        definition = next(row for row in self.catalog.values() if row.name == "Assassin")
        run, player, bot, _ = self.create_fight(definition, distance=2.5)
        fight = run.match.combat
        self.assertEqual(npc_control.start(run.match, player), bot.entity)
        for _ in range(100):
            run.step({})
            if fight.pins.get(bot.entity) == player.body:
                break
        self.assertEqual(fight.pins.get(bot.entity), player.body)
        victim_component = player.body_script.component
        victim_graph, = (i for i in victim_component.instances.values() if i.graph.index == 0x1177)
        self.assertEqual(victim_graph.source_entity(), bot.entity)
        health = helpers.JunkratProjectileTests.health(self, run, player)
        before = health.total
        console = npc_console.Console({})
        self.assertEqual(console.press("-", run.match, player)["entities"], (bot.entity,))
        self.assertFalse(fight.pins)
        self.assertNotIn(victim_graph.id, victim_component.instances)
        self.assertNotIn(bot, run.match.bots)
        for _ in range(40):
            run.step({})
        self.assertEqual(health.total, before)

    def test_projected_barrier_prevents_assassin_pin_until_expiry(self):
        definition = next(row for row in self.catalog.values() if row.name == "Assassin")
        for already_pinned in (False, True):
            with self.subTest(already_pinned=already_pinned):
                run, player, bot, _ = self.create_fight(definition, distance=2.5)
                match = run.match
                fight = match.combat
                match.game_map = replace(match.game_map, free_for_all=False)
                x, y, z = player.mover.position
                zarya = match.add_player(2, "Zarya", SOLDIER, player.team, False)
                in_world(match, zarya)
                zarya.spawn = ((x + 5, y, z), zarya.spawn[1])
                match.switch_hero(zarya, find_hero("Zarya"))
                run.players.append(zarya)
                for _ in range(25):
                    run.step({})
                body = next(target for target in fight._targets() if target.owner is player)
                a, b = body.segment()
                centre = tuple((lo + hi) * .5 for lo, hi in zip(a, b, strict=True))
                if already_pinned:
                    self.assertEqual(npc_control.start(match, player), bot.entity)
                    for _ in range(100):
                        run.step({})
                        if fight.pins.get(bot.entity) == player.body:
                            break
                    self.assertEqual(fight.pins.get(bot.entity), player.body)
                expected = 175 if already_pinned else 200
                self.assertEqual(fight.vitals_of(body).total, expected)
                run.step({zarya: (*aim(fight._eye(zarya), centre), 16)})
                fight.barriers.sync(zarya)
                bubble, = fight.barriers.live.values()
                self.assertEqual(bubble.body, player.body)
                self.assertEqual(bubble.health, 200)
                if not already_pinned:
                    self.assertEqual(npc_control.start(match, player), bot.entity)
                for _ in range(175):
                    run.step({})
                    if bubble not in fight.barriers.live.values():
                        break
                    self.assertNotIn(bot.entity, fight.pins)
                    self.assertEqual(fight.vitals_of(body).total, expected)
                    self.assertEqual(bubble.health, 200)
                self.assertNotIn(bubble, fight.barriers.live.values())
                for _ in range(100):
                    run.step({})
                    if fight.pins.get(bot.entity) == player.body:
                        break
                self.assertEqual(fight.pins.get(bot.entity), player.body)
                self.assertEqual(fight.vitals_of(body).total, expected - 25)

    def test_actual_wraith_and_cryo_prevent_assassin_pin_until_expiry(self):
        definition = next(row for row in self.catalog.values() if row.name == "Assassin")
        for hero, active in (("Reaper", immunity.wraith_active), ("Mei", immunity.cryo_active)):
            with self.subTest(hero=hero):
                run, player, bot, _ = self.create_fight(definition, distance=2.5)
                match = run.match
                fight = match.combat
                match.switch_hero(player, find_hero(hero))
                x, y, z = player.mover.position
                bot.position = (x, y, z + 2.5)
                angles = aim(fight._eye(player), (x, y + 1, z + 2.5))
                run.step({player: (*angles, 8)})
                for _ in range(40):
                    if active(player, player.body):
                        break
                    run.step({player: (*angles, 0)})
                self.assertTrue(active(player, player.body))
                self.assertEqual(npc_control.start(match, player), bot.entity)
                health = helpers.JunkratProjectileTests.health(self, run, player)
                for _ in range(320):
                    run.step({player: (*angles, 0)})
                    if not active(player, player.body):
                        break
                    self.assertNotIn(bot.entity, fight.pins)
                    self.assertEqual(health.total, 250)
                self.assertFalse(active(player, player.body))
                for _ in range(100):
                    run.step({player: (*angles, 0)})
                    if fight.pins.get(bot.entity) == player.body:
                        break
                self.assertEqual(fight.pins.get(bot.entity), player.body)
                self.assertEqual(health.total, 225)

    def test_assassin_pin_releases_when_contact_is_lost(self):
        definition = next(row for row in self.catalog.values() if row.name == "Assassin")
        for cause in ("range", "wall", "team"):
            with self.subTest(cause=cause):
                run, player, bot, _ = self.create_fight(definition, distance=2.5)
                fight = run.match.combat
                self.assertEqual(npc_control.start(run.match, player), bot.entity)
                for _ in range(80):
                    run.step({})
                    if fight.pins.get(bot.entity) == player.body:
                        break
                self.assertEqual(fight.pins.get(bot.entity), player.body)
                health = fight.vitals_of(next(t for t in fight._targets() if t.owner is player))
                before = health.total
                x, y, z = player.mover.position
                if cause == "range":
                    bot.position = (x, y, z + 6)
                elif cause == "wall":
                    wall = z + 1.25
                    run.match.collision_world = World([
                        (x - 8, y, z - 8, x + 8, y, z - 8, x + 8, y, z + 8),
                        (x - 8, y, z - 8, x + 8, y, z + 8, x - 8, y, z + 8),
                        (x - 3, y, wall, x + 3, y, wall, x + 3, y + 3, wall),
                        (x - 3, y, wall, x + 3, y + 3, wall, x - 3, y + 3, wall),
                    ])
                else:
                    bot.team = player.team
                for _ in range(30):
                    run.step({})
                self.assertFalse(fight.pins)
                self.assertEqual(health.total, before)

    @unittest.skipUnless(os.environ.get("ORPHEUS_JUNKRAT_TIRE_PROFILE"), "tire profile not configured")
    def test_jumping_tire_intercepts_authored_trooper_and_heavy_shots(self):
        for definition in self.catalog.values():
            if definition.name not in ("Trooper", "Heavy Assault"):
                continue
            with self.subTest(npc=definition.name):
                run, player, bot, _ = self.create_fight(definition, distance=8)
                player.body_script.ult_seconds = 0
                player.body_script.set_ult_charge(player.body_script.component.vars[338].value())
                run.step({player: (0, 0, 0x20)})
                system = run.match.combat.junkrat.tire
                tire, = system.actors.values()
                while run.frame < tire.drive_tick - 21:
                    run.step({})
                self.assertEqual(npc_control.start(run.match, player), bot.entity)
                # Let the NPC begin its firing cycle during preparation. Jump as
                # the tire starts driving through the line toward the exposed hero.
                for _ in range(80):
                    jump = JUMP if run.frame + 1 == tire.drive_tick else 0
                    run.step({player: (0, 0, jump)})
                    if tire.vitals.total < 100:
                        break
                self.assertEqual(tire.vitals.total, 80 if definition.name == "Trooper" else 95)
                self.assertIn(tire.entity, system.actors)
                self.assertGreater(tire.mover.position[1], player.mover.position[1])

    @unittest.skipUnless(os.environ.get("ORPHEUS_JUNKRAT_TIRE_PROFILE"), "tire profile not configured")
    def test_sniper_primary_and_secondary_projectiles_can_hit_rip_tire(self):
        definition = next(row for row in self.catalog.values() if row.name == "Sniper")
        for action, amount in (("primary", 100), ("secondary", 5)):
            with self.subTest(action=action):
                run, player, bot, _ = self.create_fight(definition, distance=8)
                fight = run.match.combat
                system = fight.junkrat.tire
                player.body_script.ult_seconds = 0
                player.body_script.set_ult_charge(player.body_script.component.vars[338].value())
                run.step({player: (0, 0, 0x20)})
                tire, = system.actors.values()
                self.assertEqual(npc_control.start(run.match, player, action=action), bot.entity)
                for _ in range(160):
                    run.step({})
                    shots = [shot for shot in fight.rockets if shot.shooter is bot]
                    if shots:
                        break
                shot, = shots
                self.assertEqual(shot.direct.amount, amount)
                point = combat._along(shot.position, shot.direction, 1.0)
                tire.mover.position[:] = (point[0], point[1] - .4, point[2])
                run.step({})
                self.assertEqual(tire.vitals.total, 100 - amount)
                self.assertEqual(helpers.JunkratProjectileTests.health(self, run, player).total, 200)
                if amount == 100:
                    self.assertEqual(tire.vitals.killer, bot.entity)
                    self.assertEqual(system.last_result, "destroyed")
                    self.assertNotIn(tire.entity, system.actors)
                else:
                    self.assertIn(tire.entity, system.actors)

    @unittest.skipUnless(os.environ.get("ORPHEUS_JUNKRAT_TIRE_PROFILE"), "tire profile not configured")
    def test_assassin_pins_creator_while_rip_tire_can_still_detonate(self):
        definition = next(row for row in self.catalog.values() if row.name == "Assassin")
        run, player, bot, bot_health = self.create_fight(definition, distance=2.5)
        fight = run.match.combat
        system = fight.junkrat.tire
        player.body_script.ult_seconds = 0
        player.body_script.set_ult_charge(player.body_script.component.vars[338].value())
        run.step({player: (0, 0, 0x20)})
        tire, = system.actors.values()
        while run.frame < tire.arm_tick:
            run.step({})
        x, y, z = player.mover.position
        tire.mover.position[:] = (x, y, z + 1.5)
        self.assertEqual(npc_control.start(run.match, player), bot.entity)
        for _ in range(35):
            run.step({})
            if bot.entity in fight.pins:
                break
        self.assertEqual(fight.pins.get(bot.entity), player.body)
        self.assertNotIn(tire.entity, fight.pins.values())
        self.assertEqual(tire.vitals.total, 100)
        self.assertEqual(helpers.JunkratProjectileTests.health(self, run, player).total, 175)
        run.step({player: (0, 0, FIRE)})
        self.assertEqual(system.last_result, "detonated")
        self.assertTrue(bot_health.dead)
        self.assertEqual(bot_health.killer, player.body)
        self.assertFalse(fight.pins)
        self.assertFalse(system.actors)

    @unittest.skipUnless(os.environ.get("ORPHEUS_JUNKRAT_TIRE_PROFILE"), "tire profile not configured")
    def test_heavy_charge_destroys_preparing_tire_without_pinning_creator(self):
        definition = next(row for row in self.catalog.values() if row.name == "Heavy Assault")
        run, player, bot, _ = self.create_fight(definition, distance=-8)
        fight = run.match.combat
        system = fight.junkrat.tire
        x, y, z = player.mover.position
        angles = aim(fight._eye(player), (x, y + 1, z - 8))
        player.body_script.set_ult_charge(player.body_script.component.vars[338].value())
        run.step({player: (*angles, 0x20)})
        tire, = system.actors.values()
        self.assertEqual(npc_control.start(run.match, player, action="charge"), bot.entity)
        for _ in range(120):
            run.step({player: (*angles, 0)})
            self.assertFalse(fight.pins)
            if tire.vitals.dead:
                break
        self.assertTrue(tire.vitals.dead)
        self.assertLess(tire.vitals.death_tick, tire.drive_tick)
        self.assertEqual(tire.vitals.killer, bot.entity)
        self.assertEqual(system.last_result, "destroyed")
        self.assertFalse(system.actors)
        self.assertFalse(fight.is_stunned(player))
        self.assertEqual(helpers.JunkratProjectileTests.health(self, run, player).total, 200)
        self.assertEqual(player.body_script.component.vars[342].value(), 0)

    def test_sniper_primary_destroys_trap_and_releases_captured_trooper(self):
        definitions = {row.name: row for row in self.catalog.values()}
        run, player, sniper, _ = self.create_fight(definitions["Sniper"], distance=8)
        fight = run.match.combat
        run.step({player: (0, 0, 0x10)})
        for _ in range(70):
            run.step({})
        trap, = fight.junkrat.traps.values()
        self.assertTrue(trap.parked)
        x, trap_y, z = trap.flight.position
        y = player.mover.position[1]
        self.assertEqual(npc_control.start(run.match, player), sniper.entity)
        shot_y = trap_y + .25
        floor = shot_y - combat.EYE
        run.match.collision_world = World(
            box(x - 12, x + 12, floor - 1, floor, z - 12, z + 12)
            + box(x - .4, x + 1.4, floor, y, z - .4, z + .4)
        )
        sniper.position = (x, floor, z + 5)
        hero_eye = fight._eye(player)[1] - y
        player.mover = Mover(
            (x, shot_y - hero_eye, z - 5), world=run.match.collision_world,
            data=mover_data(player.hero.body),
        )
        trooper = npc.spawn(run.match, definitions["Trooper"], (x + .8, y, z), team=sniper.team)
        run.step({})
        self.assertIs(trap.victim, trooper)
        self.assertTrue(fight.junkrat.rooted(trooper.entity, run.frame))
        self.assertEqual(trap.vitals.total, 100)
        for _ in range(160):
            run.step({})
            shots = [shot for shot in fight.rockets if shot.shooter is sniper]
            if shots:
                break
        shot, = shots
        self.assertEqual(shot.direct.amount, 100)
        parked_position = trap.flight.position
        health = fight.vitals[trooper.entity].total
        for _ in range(12):
            run.step({})
            if trap.vitals.dead:
                break
        self.assertEqual(trap.flight.position, parked_position)
        self.assertTrue(trap.vitals.dead)
        self.assertEqual(trap.vitals.killer, sniper.entity)
        self.assertNotIn(trap.entity, fight.junkrat.traps)
        self.assertFalse(fight.junkrat.rooted(trooper.entity, run.frame))
        self.assertLess(run.frame, trap.release_tick)
        self.assertEqual(fight.vitals[trooper.entity].total, health)
        self.assertEqual(helpers.JunkratProjectileTests.health(self, run, player).total, 200)

    def test_captured_trooper_kill_or_removal_releases_trap_synchronously(self):
        definition = next(row for row in self.catalog.values() if row.name == "Trooper")
        for removed in (False, True):
            with self.subTest(removed=removed):
                run, player, bot, vitals = self.create_fight(definition, distance=8)
                fight = run.match.combat
                run.step({player: (0, 0, 16)})
                for _ in range(60):
                    run.step({})
                trap, = fight.junkrat.traps.values()
                x, _, z = trap.flight.position
                bot.position = (x, bot.position[1], z)
                run.step({})
                self.assertEqual(vitals.total, 120)
                self.assertIs(trap.victim, bot)
                status = trap.victim_status

                def released():
                    self.assertNotIn(trap.entity, fight.junkrat.traps)
                    self.assertNotIn(bot.entity, fight.junkrat.roots)
                    self.assertEqual(bot.rooted_until_tick, 0)
                    self.assertIsNone(trap.victim_status)
                    if status is not None:
                        self.assertNotIn(status.id, status.component.instances)

                if removed:
                    frame = fight.tick
                    result = npc_console.Console({}).press("-", run.match, player)
                    self.assertIn(bot.entity, result["entities"])
                    self.assertEqual(fight.tick, frame)
                    released()
                    self.assertNotIn(bot, run.match.bots)
                else:
                    died = fight._bot_died

                    def observe(*args):
                        died(*args)
                        released()

                    with patch.object(fight, "_bot_died", side_effect=observe) as death:
                        run.step({player: (0, 0, FIRE)})
                        for _ in range(40):
                            run.step({})
                            if bot.dead:
                                break
                        death.assert_called_once()
                    self.assertEqual(vitals.total, 0)
                    self.assertEqual(vitals.killer, player.body)
                    self.assertIn(bot, run.match.bots)
                    self.assertIsNotNone(bot.body_script)
                    self.assertIsNotNone(bot.body_script.death_frame)

    def test_trap_damage_root_and_release_each_real_npc_body(self):
        for definition in self.catalog.values():
            with self.subTest(npc=definition.name):
                run, player, bot, vitals = self.create_fight(definition, distance=8)
                initial = vitals.total
                run.step({player: (0, 0, 0x10)})
                for _ in range(60):
                    run.step({})
                trap, = run.match.combat.junkrat.traps.values()
                x, _, z = trap.flight.position
                bot.position = (x, bot.position[1], z)
                run.step({})
                self.assertEqual(vitals.total, initial - 80)
                self.assertTrue(run.match.combat.junkrat.rooted(bot.entity, run.frame))
                position = tuple(bot.position)
                bot.add_impulse((0, 15, 15), run.match.collision_world)
                self.assertIsNone(bot.displacement)  # blocked impulses are not queued until release
                for _ in range(20):
                    run.step({})
                self.assertEqual(tuple(bot.position), position)
                while run.frame < trap.release_tick:
                    run.step({})
                self.assertFalse(run.match.combat.junkrat.rooted(bot.entity, run.frame))
                self.assertNotIn(trap.entity, run.match.combat.junkrat.traps)
                bot.add_impulse((0, 15, 15), run.match.collision_world, tick=run.frame)
                run.step({})
                self.assertGreater(bot.position[1], position[1])
                self.assertGreater(bot.position[2], position[2])

    def test_steel_trap_roots_trooper_and_heavy_without_disabling_melee(self):
        for name, damage in (("Trooper", 30), ("Heavy Assault", 80)):
            with self.subTest(name=name):
                definition = next(row for row in self.catalog.values() if row.name == name)
                run, player, bot, bot_health = self.create_fight(definition, distance=8)
                fight = run.match.combat
                run.step({player: (0, 0, 0x10)})
                for _ in range(60):
                    run.step({})
                trap, = fight.junkrat.traps.values()
                x, _, z = trap.flight.position
                y = bot.position[1]
                for _ in range(35):
                    if player.mover.position[2] >= z - 2.0:
                        break
                    run.frame += 1
                    run.match.update(run.frame * combat.TICK, run.frame)
                    player.take_commands([Command(run.frame, forward=127)])
                self.assertLess(z - player.mover.position[2], 2.1)
                bot.position = (x, y, z)
                angles = aim(fight._eye(player), (x, y + 1, z))
                run.step({player: (*angles, 0)})
                self.assertIs(trap.victim, bot)
                self.assertEqual(bot_health.total, definition.health + definition.armour + definition.shields - 80)
                self.assertTrue(fight.junkrat.rooted(bot.entity, run.frame))
                self.assertFalse(fight.is_stunned(bot))
                position = bot.position
                health = helpers.JunkratProjectileTests.health(self, run, player)
                self.assertEqual(npc_control.start(run.match, player, action="melee"), bot.entity)
                for _ in range(45):
                    run.step({player: (*angles, 0)})
                self.assertEqual(health.total, 200 - damage)
                self.assertTrue(fight.junkrat.rooted(bot.entity, run.frame))
                self.assertEqual(bot.position, position)

    def test_trapped_trooper_sniper_and_heavy_can_still_attack(self):
        damage = {"Trooper": 20, "Sniper": 100, "Heavy Assault": 5}
        for definition in self.catalog.values():
            if definition.name not in damage:
                continue
            with self.subTest(npc=definition.name):
                run, player, bot, _vitals = self.create_fight(definition, distance=8)
                run.step({player: (0, 0, 0x10)})
                for _ in range(60):
                    run.step({})
                trap, = run.match.combat.junkrat.traps.values()
                x, _, z = trap.flight.position
                bot.position = (x, bot.position[1], z)
                run.step({})
                position = tuple(bot.position)
                self.assertEqual(npc_control.start(run.match, player), bot.entity)
                health = helpers.JunkratProjectileTests.health(self, run, player)
                for _ in range(150):
                    run.step({})
                    if health.total < 200:
                        break
                self.assertEqual(health.total, 200 - damage[definition.name])
                self.assertEqual(tuple(bot.position), position)
                self.assertTrue(run.match.combat.junkrat.rooted(bot.entity, run.frame))


if __name__ == "__main__":
    unittest.main()
