"""Authored Enforcer weapon tests through manual controller and direct consumer paths.

The prepared controller bundle exercises live admission. Both paths supply
reconstructed launch words for the unresolved engine spread context.
"""

import math
import os
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

import test_junkrat_npc_combat as npc_helpers
from test_game_combat import aim, in_world
from test_game_mover import box, wall

from ow174.game import combat, launch_ring, npc_control
from ow174.game.collision import World
from ow174.game.content import SOLDIER, find_hero
from ow174.game.mover import Mover, mover_data
from ow174.game.script import graph


class EnforcerPelletConsumerTests(unittest.TestCase):
    create_fight = npc_helpers.JunkratNpcCombatTests.create_fight

    @classmethod
    def setUpClass(cls):
        npc_helpers.JunkratNpcCombatTests.setUpClass.__func__(cls)

    @classmethod
    def tearDownClass(cls):
        npc_helpers.JunkratNpcCombatTests.tearDownClass.__func__(cls)

    def arena(self, *, context=True, distance=6):
        definition = next(row for row in self.catalog.values() if row.name == 'Enforcer')
        run, player, bot, _ = self.create_fight(definition, distance=-distance)
        instance = next(i for i in bot.body_script.component.instances.values() if i.graph.index == 0x13EB)
        state = instance.node_state(instance.graph.node(24))
        self.assertIsNotNone(state)
        calls = []
        if context:
            def inputs(state, number, activation, count):
                calls.append((number, activation, count))
                words = tuple(((i * 2654435761) & 0xFFFFFFFF,
                               ((i + 7) * 2246822519) & 0xFFFFFFFF) for i in range(count))
                return launch_ring.Inputs(0.0, words)
            bot.body_script.component.world.weapon_launch_inputs = inputs
        target = next(t for t in run.match.combat._targets() if t.owner is player)
        return run, player, bot, state, run.match.combat.vitals_of(target), calls

    def emit(self, run, bot, state):
        run.match.combat._shoot(bot, None, state, 0, run.frame * 16, activation=(1, run.frame * 16))

    def advance(self, run):
        for _ in range(12):
            run.step({})

    @unittest.skipUnless(os.environ.get("ORPHEUS_ENFORCER_GRAPHS")
                         and os.environ.get("ORPHEUS_JUNKRAT_TIRE_PROFILE"),
                         "prepared Enforcer and tire data required")
    def test_live_enforcer_volley_hits_tire_without_damaging_junkrat(self):
        path = Path(os.environ["ORPHEUS_ENFORCER_GRAPHS"])
        with patch.object(graph, "GRAPHS_PATH", path):
            npc_helpers.clear_graph_cache()
            try:
                definition = next(row for row in self.catalog.values() if row.name == "Enforcer")
                run, player, bot, _ = self.create_fight(definition, distance=-6)
                fight = run.match.combat
                system = fight.junkrat.tire
                x, y, z = bot.position
                angles = aim(fight._eye(player), (x, y + 1, z))
                player.body_script.ult_seconds = 0
                player.body_script.set_ult_charge(player.body_script.component.vars[338].value())
                run.step({player: (*angles, 0x20)})
                tire, = system.actors.values()
                self.assertEqual(npc_control.start(run.match, player), bot.entity)
                for _ in range(150):
                    run.step({})
                    if fight.rockets:
                        break
                pellets = [rocket for rocket in fight.rockets if rocket.shooter is bot]
                self.assertEqual(len(pellets), 25)
                point = combat._along(pellets[0].position, pellets[0].direction, .65)
                tire.mover.position[:] = (point[0], point[1] - .4, point[2])
                run.step({})
                self.assertEqual(tire.vitals.total, 50)
                self.assertIn(tire.entity, system.actors)
                owner = next(target for target in fight._targets() if target.owner is player)
                self.assertEqual(fight.vitals_of(owner).total, 200)
                self.assertFalse(any(rocket.shooter is bot for rocket in fight.rockets))
            finally:
                npc_helpers.clear_graph_cache()

    @unittest.skipUnless(os.environ.get("ORPHEUS_ENFORCER_GRAPHS"), "prepared Enforcer controller bundle required")
    def test_live_manual_controller_emits_authored_volley_and_stop_retires_it(self):
        path = Path(os.environ["ORPHEUS_ENFORCER_GRAPHS"])
        with patch.object(graph, "GRAPHS_PATH", path):
            npc_helpers.clear_graph_cache()
            try:
                definition = next(row for row in self.catalog.values() if row.name == "Enforcer")
                run, player, bot, _ = self.create_fight(definition, distance=-6)
                controller = bot.body_script.component.world.enforcer_controller
                self.assertIsNotNone(controller)
                self.assertTrue(controller.host.entity_var(9134, create=False).value())
                height = mover_data(bot.body).stand_height
                pitch = round(-math.atan2(height / 2 - combat.EYE, 6) * 65536 / (2 * math.pi))
                run.step({player: (-32768, pitch, 0)})
                self.assertEqual(npc_control.start(run.match, player), bot.entity)
                fight = run.match.combat
                target = next(t for t in fight._targets() if t.owner is player)
                for _ in range(150):
                    run.step({})
                    if fight.rockets:
                        break
                pellets = [r for r in fight.rockets if r.shooter is bot]
                self.assertEqual(len(pellets), 25)
                self.assertEqual(len({r.direction for r in pellets}), 25)
                self.assertEqual({(r.speed, r.direct.amount) for r in pellets}, {(80, 2)})
                for _ in range(12):
                    run.step({})
                self.assertEqual(fight.vitals_of(target).total, 150)
                self.assertEqual(npc_control.stop(run.match), 1)
                for _ in range(60):
                    run.step({})
                self.assertFalse(fight.rockets)
                self.assertEqual(fight.vitals_of(target).total, 150)
                run.step({player: (-32768, pitch, 0)})
                self.assertEqual(npc_control.start(run.match, player), bot.entity)
                for _ in range(150):
                    run.step({})
                    if fight.rockets:
                        break
                self.assertEqual(len(fight.rockets), 25)
                self.assertEqual(npc_control.stop(run.match), 1)
                self.assertFalse(fight.rockets)
                for _ in range(12):
                    run.step({})
                self.assertEqual(fight.vitals_of(target).total, 150)
            finally:
                npc_helpers.clear_graph_cache()

    @unittest.skipUnless(os.environ.get("ORPHEUS_ENFORCER_GRAPHS"),
                         "prepared Enforcer controller bundle required")
    def test_allied_sigma_absorbs_the_enforcer_volley_and_protects_junkrat(self):
        path = Path(os.environ["ORPHEUS_ENFORCER_GRAPHS"])
        with patch.object(graph, "GRAPHS_PATH", path):
            npc_helpers.clear_graph_cache()
            try:
                definition = next(row for row in self.catalog.values() if row.name == "Enforcer")
                run, player, bot, _ = self.create_fight(definition, distance=-8)
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
                sigma.mover = Mover((x, y, z - 2.5), world=match.collision_world,
                                    data=mover_data(sigma.hero.body))
                vitals = {t.owner: fight.vitals_of(t) for t in fight._targets() if t.owner in (player, sigma)}
                before = vitals[sigma].total
                for _ in range(150):
                    run.step(commands)
                    if any(rocket.shooter is bot for rocket in fight.rockets):
                        break
                pellets = [rocket for rocket in fight.rockets if rocket.shooter is bot]
                self.assertEqual(len(pellets), 25)
                self.assertEqual({rocket.direct.amount for rocket in pellets}, {2})
                run.step({**commands, sigma: (-32768, 0, 8)})
                instance = next(i for i in sigma.body_script.component.instances.values()
                                if i.graph.index == 0x1D02)
                self.assertTrue(instance.states[1].active)
                for _ in range(20):
                    if not any(rocket.shooter is bot for rocket in fight.rockets):
                        break
                    run.step(commands)
                self.assertFalse(any(rocket.shooter is bot for rocket in fight.rockets))
                self.assertEqual(npc_control.stop(match), 1)
                self.assertEqual(instance.vars[21312].value(), 50)
                self.assertEqual(vitals[player].total, 200)
                self.assertEqual(vitals[sigma].total, before)
            finally:
                npc_helpers.clear_graph_cache()

    @unittest.skipUnless(os.environ.get("ORPHEUS_ENFORCER_GRAPHS"), "prepared Enforcer controller bundle required")
    def test_live_manual_enforcer_applies_falloff_and_releases_on_los_team_and_range(self):
        path = Path(os.environ["ORPHEUS_ENFORCER_GRAPHS"])
        with patch.object(graph, "GRAPHS_PATH", path):
            npc_helpers.clear_graph_cache()
            try:
                definition = next(row for row in self.catalog.values() if row.name == "Enforcer")
                run, player, bot, _ = self.create_fight(definition, distance=-12)
                match = run.match
                fight = match.combat
                x, y, z = player.mover.position
                match.game_map = replace(match.game_map, free_for_all=False)
                target = next(t for t in fight._targets() if t.owner is player)
                health = fight.vitals_of(target)
                angles = aim(fight._eye(player), (x, y + mover_data(bot.body).stand_height * .5, z - 12))
                run.step({player: (*angles, 0)})
                self.assertEqual(npc_control.start(match, player), bot.entity)
                run.step({})
                self.assertTrue(bot.attack_input)
                floor = match.collision_world
                match.collision_world = World(
                    box(x - 25, x + 25, y - 1, y, z - 25, z + 25)
                    + wall(x - 10, z - 6, x + 10, z - 6, y0=y, y1=y + 6)
                )
                for _ in range(90):
                    run.step({})
                self.assertFalse(bot.attack_input)
                self.assertFalse(fight.rockets)
                self.assertEqual(health.total, 200)

                match.collision_world = floor
                for _ in range(150):
                    run.step({})
                    if fight.rockets:
                        break
                self.assertTrue(bot.attack_input)
                self.assertEqual(len(fight.rockets), 25)
                target = next(t for t in fight._targets() if t.owner is player)
                a, b = target.segment()
                distances = [distance for shot in fight.rockets
                             if (distance := combat.ray_capsule(
                                 shot.position, shot.direction, a, b, target.radius)) is not None]
                self.assertTrue(distances)
                self.assertTrue(all(8 < distance < 15 for distance in distances))
                expected = sum(2 * (1 - 0.8 * (distance - 8) / 7) for distance in distances)
                self.advance(run)
                self.assertAlmostEqual(health.total, 200 - expected, delta=.001)

                team = bot.team
                bot.team = player.team
                for _ in range(90):
                    run.step({})
                self.assertFalse(bot.attack_input)
                self.assertFalse(fight.rockets)
                self.assertAlmostEqual(health.total, 200 - expected, delta=.001)
                bot.team = team
                bot.position = (x, y, z - 101)
                for _ in range(90):
                    run.step({})
                self.assertFalse(bot.attack_input)
                self.assertFalse(fight.rockets)
                self.assertAlmostEqual(health.total, 200 - expected, delta=.001)
            finally:
                npc_helpers.clear_graph_cache()

    def test_authored25_unique_projectiles_deal50_close_damage_once(self):
        run, _player, bot, state, health, calls = self.arena()
        self.emit(run, bot, state)
        rockets = list(run.match.combat.rockets)
        self.assertEqual(len(rockets), 25)
        self.assertEqual(len({r.direction for r in rockets}), 25)
        self.assertEqual({(r.speed, r.direct.amount) for r in rockets}, {(80, 2)})
        self.assertEqual(calls, [(0, (1, run.frame * 16), 25)])
        self.advance(run)
        self.assertEqual(health.total, 150)
        self.advance(run)
        self.assertEqual(health.total, 150)

    def test_missing_context_does_not_substitute_one_or_coincident_pellets(self):
        run, _player, bot, state, health, _calls = self.arena(context=False)
        with self.assertLogs('ow174.game', level='WARNING'):
            self.emit(run, bot, state)
        self.assertEqual(run.match.combat.rockets, [])
        self.advance(run)
        self.assertEqual(health.total, 200)

    def test_actual_volley_applies_falloff_across_multiple_world_steps(self):
        run, player, bot, state, health, _calls = self.arena(distance=12)
        self.emit(run, bot, state)
        fight = run.match.combat
        target = next(t for t in fight._targets() if t.owner is player)
        a, b = target.segment()
        distances = []
        for rocket in fight.rockets:
            distance = combat.ray_capsule(rocket.position, rocket.direction, a, b, target.radius)
            if distance is not None:
                distances.append(distance)
        self.assertTrue(distances)
        self.assertTrue(all(8 < distance < 15 for distance in distances))
        expected = sum(2 * (1 - 0.8 * (distance - 8) / 7) for distance in distances)
        self.advance(run)
        # World stepping settles the mover onto the floor between launch and impact.
        self.assertAlmostEqual(health.total, 200 - expected, delta=0.001)

    def test_each_pellet_misses_when_aimed_away(self):
        run, _player, bot, state, health, _calls = self.arena()
        bot.yaw = 16384
        self.emit(run, bot, state)
        self.assertEqual(len(run.match.combat.rockets), 25)
        self.advance(run)
        self.assertEqual(health.total, 200)

    def test_wall_blocks_all25_pellets(self):
        run, player, bot, state, health, _calls = self.arena()
        x, y, z = player.mover.position
        z -= 3
        run.match.collision_world = World([
            (x-10, y, z, x+10, y, z, x+10, y+10, z),
            (x-10, y, z, x+10, y+10, z, x-10, y+10, z),
        ])
        self.emit(run, bot, state)
        self.advance(run)
        self.assertEqual(health.total, 200)
        self.assertEqual(run.match.combat.rockets, [])

    def test_friendly_filter_applies_to_every_pellet(self):
        run, player, bot, state, health, _calls = self.arena()
        run.match.game_map = replace(run.match.game_map, free_for_all=False)
        bot.team = player.team
        self.emit(run, bot, state)
        self.advance(run)
        self.assertEqual(health.total, 200)
