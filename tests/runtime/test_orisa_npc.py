"""Actual Talon bodies from explicit read-only external prepared inputs.

Set ORPHEUS_NPC_RUNTIME to the prepared package. Transport and collision remain
fixtures; the permissive NPC VM is not evidence of authored AI/specials.
"""

import math
import os
import unittest
from pathlib import Path
from unittest.mock import patch

import test_orisa_barrier as barrier_fixture
from test_game_combat import SOLDIER, Run, aim, in_world

from ow174.game import combat, npc
from ow174.game.commands import FIRE
from ow174.game.content import find_hero, heroes
from ow174.game.script import graph
from ow174.game.script.driver import OWNER_HEALTH

ORISA = 0x02E000000000013E


@unittest.skipUnless(os.environ.get("ORPHEUS_NPC_RUNTIME"), "explicit external Talon package required")
class OrisaTalonTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        directory = Path(os.environ["ORPHEUS_NPC_RUNTIME"]) / "data"
        cls.catalog = npc.load_catalog(directory / "pve_npcs_174.json")
        cls.graphs = patch.object(graph, "GRAPHS_PATH", directory / "game_graphs_174.json.gz")
        cls.graphs.start()
        cls.clear_graphs()

    @staticmethod
    def clear_graphs():
        for function in (graph.graph, graph._records, graph._data):
            function.cache_clear()

    @classmethod
    def tearDownClass(cls):
        cls.graphs.stop()
        cls.clear_graphs()

    def arena(self, definition, *, ahead=8, team=1):
        run = Run()
        player, = run.players
        run.match.bots.clear()
        run.match._pending_bot_scripts.clear()
        run.match.switch_hero(player, heroes()[ORISA])
        events = []
        player.client.queue_bot_death = lambda entity, event: events.append((entity, event))
        player.client.queue_bot_death_update = lambda entity, event: events.append((entity, event))
        x, y, z = player.mover.position
        bot = npc.spawn(run.match, definition, (x, y, z + ahead), team=team)
        self.assertIsNotNone(bot.body_script)
        self.assertEqual(bot.body_script.component.entity_var(OWNER_HEALTH).value(),
                         definition.health + definition.armour + definition.shields)
        return run, player, bot

    def test_actual_five_talon_bodies_take_commanded_fusion_driver_damage(self):
        self.assertEqual({d.name for d in self.catalog.values()},
                         {"Trooper", "Enforcer", "Sniper", "Assassin", "Heavy Assault"})
        for definition in self.catalog.values():
            with self.subTest(npc=definition.name):
                run, player, bot = self.arena(definition)
                fight = run.match.combat
                vitals = fight.vitals[bot.entity]
                initial = vitals.total
                x, y, z = bot.position
                yaw, pitch = aim(fight._eye(player), (x, y + 1, z))
                run.step({player: (yaw, pitch, FIRE)})
                for _ in range(8):
                    run.step({})
                self.assertEqual(initial - vitals.total, 11)
                self.assertEqual(bot.body_script.component.entity_var(OWNER_HEALTH).value(), vitals.total)

    def test_genji_reflects_fusion_driver_into_each_talon_body(self):
        for definition in self.catalog.values():
            with self.subTest(npc=definition.name):
                run, orisa, bot = self.arena(definition, ahead=1.5, team=0)
                orisa.team = 0
                genji = run.match.add_player(2, "Genji", SOLDIER, 1, False)
                in_world(run.match, genji)
                run.players.append(genji)
                run.match.switch_hero(genji, find_hero("Genji"))
                genji.team = 1
                x, y, z = orisa.mover.position
                barrier_fixture.BarrierTests.place(genji, (x, y, z + 5))
                fight = run.match.combat
                bot_health = fight.vitals[bot.entity]
                orisa_health = fight.vitals_of(next(t for t in fight._targets() if t.owner is orisa))
                before = bot_health.total, orisa_health.total
                yaw, pitch = aim(fight._eye(genji), (x, y + 0.2, z + 1.5))
                run.step({genji: (yaw, pitch, 16)})
                run.step({genji: (yaw, pitch, 0), orisa: (0, 0, FIRE)})
                rocket, = fight.rockets
                for _ in range(12):
                    run.step({genji: (yaw, pitch, 0)})
                self.assertTrue(rocket.reflected)
                self.assertEqual(rocket.source_body, genji.body)
                self.assertEqual(rocket.ended, "body")
                self.assertEqual(before[0] - bot_health.total, 11)
                self.assertEqual(orisa_health.total, before[1])
                self.assertEqual(bot.body_script.component.entity_var(OWNER_HEALTH).value(), bot_health.total)
                self.assertEqual(len(genji.client.messages(combat.HIT)), 1)
                self.assertFalse(orisa.client.messages(combat.HIT))

    def test_actual_five_allied_talon_bodies_are_not_hurt_by_fusion_driver(self):
        for definition in self.catalog.values():
            with self.subTest(npc=definition.name):
                run, player, bot = self.arena(definition, team=0)
                fight = run.match.combat
                initial = fight.vitals[bot.entity].total
                x, y, z = bot.position
                yaw, pitch = aim(fight._eye(player), (x, y + 1, z))
                run.step({player: (yaw, pitch, FIRE)})
                for _ in range(8):
                    run.step({})
                self.assertEqual(fight.vitals[bot.entity].total, initial)

    def test_actual_five_talon_bodies_take_one_quick_melee(self):
        for definition in self.catalog.values():
            with self.subTest(npc=definition.name):
                run, player, bot = self.arena(definition, ahead=1.5)
                vitals = run.match.combat.vitals[bot.entity]
                initial = vitals.total
                run.step({player: (0, 0, 0x800)})
                for _ in range(25):
                    run.step({})
                self.assertEqual(initial - vitals.total, 30)

    def test_actual_trooper_halt_uses_collision_mover_and_death_retires_control(self):
        definition = next(d for d in self.catalog.values() if d.name == "Trooper")
        run, player, bot = self.arena(definition, ahead=10)
        original = bot.position
        vitals = run.match.combat.vitals[bot.entity]
        initial = vitals.total
        steps = []
        for index in range(60):
            previous = bot.position
            run.step({player: (0, 0, 2 if index in (0, 25) else 0)})
            steps.append(math.dist(previous, bot.position))
            if index < 50:
                self.assertEqual(bot.position, original)
        # Original0CCE waits .15 + .25 seconds, then requests20m/s. Idle
        # startup ticks must not become mover catch-up steps at activation.
        self.assertTrue(any(distance > 0.3 for distance in steps))
        self.assertLessEqual(max(steps), 20 * 0.016 + 2 / 1024)
        self.assertLess(bot.position[2], original[2] - 1)
        self.assertEqual(vitals.total, initial)
        self.assertIn(bot.entity, run.match.combat.orisa.pulls)
        self.assertEqual(tuple(bot.movement(run.frame).position), bot.position)
        fight = run.match.combat
        target = next(t for t in fight._targets() if t.owner is bot)
        fight._damage(player, target, 1000, False, None, run.frame)
        terminal = bot.position
        run.step({})
        self.assertNotIn(bot.entity, fight.orisa.pulls)
        self.assertEqual(bot.position, terminal)
        self.assertTrue(bot.retain_corpse)

    def test_actual_talon_halt_post_slow_scales_patrol_and_recovers(self):
        for name in ("Trooper", "Enforcer", "Sniper", "Assassin"):
            with self.subTest(npc=name):
                definition = next(d for d in self.catalog.values() if d.name == name)
                self.check_halt_post_slow(definition)

    @staticmethod
    def start_patrol(bot):
        x, y, z = bot.position
        bot.route = [(x + 20, y, z)]
        bot.index = -1
        bot.wait = 0.0

    def cast_until_halt_post_slow(self, run, player, bot):
        manager = run.match.combat.orisa
        for index in range(100):
            run.step({player: (0, 0, 2 if index in (0, 25) else 0)})
            pull = manager.pulls.get(bot.entity)
            if pull is not None:
                wait = pull.script.states.get(pull.script.graph.node(5).state)
                if wait is not None and wait.active:
                    return pull, wait
        self.fail("NPC did not reach the authored post-movement wait")

    def check_halt_post_slow(self, definition):
        run, player, bot = self.arena(definition, ahead=10)
        _, wait = self.cast_until_halt_post_slow(run, player, bot)
        manager = run.match.combat.orisa
        self.start_patrol(bot)
        slowed = []
        recovered = []
        for _ in range(90):
            was_slow = wait.active
            run.step({})
            speed = math.hypot(bot.velocity[0], bot.velocity[2])
            (slowed if was_slow else recovered).append(speed)
        self.assertGreater(len(slowed), 30)
        self.assertAlmostEqual(max(slowed), bot.speed * 0.35, places=5)
        self.assertAlmostEqual(max(recovered), bot.speed, places=5)
        self.assertNotIn(bot.entity, manager.pulls)

    def test_actual_primary_or_melee_kill_retires_halt_post_slow(self):
        for definition in self.catalog.values():
            if definition.name != "Heavy Assault":
                for melee in (False, True):
                    with self.subTest(npc=definition.name, melee=melee):
                        self.check_kill_retires_halt_post_slow(definition, melee=melee)

    def check_kill_retires_halt_post_slow(self, definition, *, melee):
        run, player, bot = self.arena(definition, ahead=10)
        fight = run.match.combat
        vitals = fight.vitals[bot.entity]

        def fire():
            x, y, z = bot.position
            yaw, pitch = aim(fight._eye(player), (x, y + 1, z))
            run.step({player: (yaw, pitch, FIRE)})

        # Lower health through actual primary fire so a later hit can kill
        # inside the post-slow window without changing health or timers.
        for _ in range(600):
            fire()
            if 0 < vitals.total <= 22:
                break
        self.assertGreater(vitals.total, 0)
        self.assertLessEqual(vitals.total, 22)
        pull, wait = self.cast_until_halt_post_slow(run, player, bot)
        component = bot.body_script.component
        self.assertAlmostEqual(component.entity_var(23186).value(), 0.65)
        self.start_patrol(bot)
        run.step({})
        self.assertTrue(fight.orisa.npc_movement_mods(bot.entity, run.frame))
        if melee:
            x, y, z = bot.position
            barrier_fixture.BarrierTests.place(player, (x, y, z - 1.5))
        for frame in range(30):
            self.assertTrue(wait.active)
            if melee:
                run.step({player: (0, 0, 0x800 if frame == 0 else 0)})
            else:
                fire()
            if bot.dead:
                break
        self.assertTrue(bot.dead)
        self.assertTrue(bot.retain_corpse)
        terminal = bot.position
        run.step({})
        self.assertNotIn(bot.entity, fight.orisa.pulls)
        self.assertTrue(pull.script.stopped)
        self.assertFalse(component.entity_var(23186).value())
        self.assertIsNone(fight.orisa.npc_movement_mods(bot.entity, run.frame))
        self.assertEqual(bot.position, terminal)
        self.assertEqual(bot.velocity, (0, 0, 0))

    def test_actual_heavy_halt_immunity_preserves_speed_but_allows_melee_damage(self):
        definition = next(d for d in self.catalog.values() if d.name == "Heavy Assault")
        run, player, bot = self.arena(definition, ahead=10)
        manager = run.match.combat.orisa
        for index in range(27):
            run.step({player: (0, 0, 2 if index in (0, 25) else 0)})
        self.assertIn(bot.entity, manager.results[-1][1])
        immunity = bot.body_script.component.entity_var(6601)
        self.assertTrue(immunity.value())
        self.assertTrue(any(link.state.instance.graph.index == 0x1254 and link.state.node.pos == 29
                            for link in immunity.links))
        self.start_patrol(bot)
        speeds = []
        for _ in range(90):
            run.step({})
            self.assertNotIn(bot.entity, manager.pulls)
            speeds.append(math.hypot(bot.velocity[0], bot.velocity[2]))
        self.assertTrue(all(math.isclose(speed, bot.speed, rel_tol=1e-6) for speed in speeds[-30:]))

        bot.route = []
        x, y, z = bot.position
        barrier_fixture.BarrierTests.place(player, (x, y, z - 1.5))
        run.step({})
        vitals = run.match.combat.vitals[bot.entity]
        before = vitals.total
        run.step({player: (0, 0, 0x800)})
        for _ in range(25):
            run.step({})
        self.assertEqual(before - vitals.total, 30)
        self.assertTrue(immunity.value())
        self.assertEqual(bot.body_script.component.entity_var(OWNER_HEALTH).value(), vitals.total)

    def test_actual_five_talon_bodies_die_with_auto_reloads_and_retain_corpses(self):
        for definition in self.catalog.values():
            with self.subTest(npc=definition.name):
                run, player, bot = self.arena(definition)
                fight = run.match.combat
                x, y, z = bot.position
                yaw, pitch = aim(fight._eye(player), (x, y + 1, z))
                for _ in range(4000):
                    run.step({player: (yaw, pitch, FIRE)})
                    if bot.dead:
                        break
                self.assertTrue(bot.dead, definition.name)
                self.assertEqual(fight.vitals[bot.entity].total, 0)
                self.assertFalse(any(who is bot for _, who in fight.respawns))
                for _ in range(200):
                    run.step({})
                self.assertIn(bot, run.match.bots)
                self.assertNotIn(bot, [target.owner for target in fight._enemies(player)])
                self.assertEqual(npc.remove_spawned(run.match), (bot.entity,))


if __name__ == "__main__":
    unittest.main()
