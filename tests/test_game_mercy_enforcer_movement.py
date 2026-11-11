"""Mercy combat admission against the Enforcer's original speed modifier."""

import unittest
from pathlib import Path
from unittest.mock import patch

import test_game_mercy_npc_geometry as geometry
from test_game_mercy_blaster_critical import equip, shot

from ow174.game import enforcer_movement, npc, npc_control, stats
from ow174.game.script import graph


class MercyEnforcerMovementTests(unittest.TestCase):
    def encounter(self):
        geo = geometry.MercyNpcGeometryTests()
        geo.setUp()
        self.addCleanup(geo.doCleanups)
        fixture = geo.npc_fixture()
        self.enterContext(patch.object(
            graph, "GRAPH_SUPPLEMENT_PATH", Path(__file__).resolve().parents[2]
            / "research/enforcer-controller-supplement.json.gz",
        ))
        definition = next(d for d in fixture.catalog.values() if d.name == "Enforcer")
        run, mercy, bot, target = fixture.spawn(definition, 1)
        bot.position = (0, 1, 4.6)
        fixture.tick(run, mercy, bot, 2, buttons=0)
        controller = bot.body_script.component.world.enforcer_controller
        self.assertIsNotNone(controller)
        self.assertNotEqual(controller.entity, bot.entity)
        return run, mercy, bot, run.match.combat.vitals_of(target), fixture, controller

    def test_controller_gate_changes_route_speed_and_actual_mercy_melee_admission(self):
        for slowed in (False, True):
            with self.subTest(slowed=slowed):
                run, mercy, bot, health, fixture, controller = self.encounter()
                if slowed:
                    self.assertEqual(npc_control.enforcer_walk(run.match, mercy), bot.entity)
                    self.assertEqual(controller.entity_var(enforcer_movement.SPEED_GATE).value(), 1)
                else:
                    bot.body_script.component.set(enforcer_movement.SPEED_GATE, 1)
                bot.route = [(0, 1, 0)]
                bot.wait = 0
                fixture.tick(run, mercy, bot, 33, buttons=0)
                scale = 0.4 if slowed else 1.0
                self.assertAlmostEqual(enforcer_movement.speed_scale(bot), scale)
                self.assertAlmostEqual(bot.pace, bot.speed * scale)
                bot.route.clear()
                before = health.total
                fixture.tick(run, mercy, bot, 1, buttons=0x800)
                fixture.tick(run, mercy, bot, 25, buttons=0)
                self.assertEqual(before - health.total, 0 if slowed else 30)
                if slowed:
                    self.assertEqual(npc_control.stop(run.match), 1)
                    fixture.tick(run, mercy, bot, 4, buttons=0)
                    self.assertAlmostEqual(enforcer_movement.speed_scale(bot), 1)
                    self.assertFalse(controller.entity_var(enforcer_movement.SPEED_GATE).value())
                    bot.route = [(0, 1, 0)]
                    maximum = 0
                    for _ in range(35):
                        fixture.tick(run, mercy, bot, 1, buttons=0)
                        maximum = max(maximum, bot.pace)
                        if bot.position[2] <= 2.4:
                            break
                    self.assertGreater(maximum, bot.speed * 0.4)
                    self.assertLessEqual(bot.position[2], 2.4)
                    bot.route.clear()
                    fixture.tick(run, mercy, bot, 55, buttons=0)
                    fixture.tick(run, mercy, bot, 1, buttons=0x800)
                    fixture.tick(run, mercy, bot, 25, buttons=0)
                    self.assertEqual(before - health.total, 30)

    def test_spawned_controller_loads_authored_thresholds_before_behavior_selection(self):
        _, _, bot, _, _, controller = self.encounter()
        instance, = (i for i in controller.instances.values() if i.graph.index == enforcer_movement.CONTROL_GRAPH)
        self.assertEqual(instance.vars[9781].value(), 16)
        self.assertEqual(instance.vars[13965].value(), 15)
        self.assertTrue(instance.node_state(instance.graph.node(1)).active)
        self.assertFalse(instance.node_state(instance.graph.node(enforcer_movement.CONTROL_NODE)).active)
        self.assertEqual(enforcer_movement.speed_scale(bot), 1)

    def test_blaster_kill_retires_active_enforcer_movement_controller(self):
        run, mercy, bot, health, fixture, controller = self.encounter()
        fight = run.match.combat
        equip(run, mercy)
        target = next(t for t in fight._targets() if t.owner is bot)
        fight._damage(mercy, target, health.total - 35, False, None, run.frame)
        self.assertEqual(health.total, 35)

        self.assertEqual(npc_control.enforcer_walk(run.match, mercy), bot.entity)
        bot.route = [(0, 1, 20)]
        bot.wait = 0
        fixture.tick(run, mercy, bot, 10, buttons=0)
        self.assertAlmostEqual(enforcer_movement.speed_scale(bot), 0.4)
        self.assertAlmostEqual(bot.pace, bot.speed * 0.4)
        before = bot.position[2]
        eliminations = mercy.stats.value(stats.ELIMINATIONS)
        shot(run, mercy, bot.position, 0.8, frames=30)
        self.assertEqual(health.total, 15)
        self.assertGreater(bot.position[2], before)
        self.assertFalse(controller.owner_dead)
        self.assertEqual(controller.entity_var(enforcer_movement.SPEED_GATE).value(), 1)
        self.assertAlmostEqual(enforcer_movement.speed_scale(bot), 0.4)

        shot(run, mercy, bot.position, 0.8, frames=25)
        self.assertTrue(bot.dead)
        self.assertEqual(health.total, 0)
        self.assertTrue(controller.owner_dead)
        self.assertFalse(controller.instances)
        self.assertEqual(mercy.stats.value(stats.ELIMINATIONS), eliminations + 1)
        mercy.client.queue_bot_death.assert_called_once()
        stopped = bot.position
        fixture.tick(run, mercy, bot, 30, buttons=0)
        self.assertEqual(bot.position, stopped)

    def test_slow_walk_and_real_shotgun_volley_coexist_until_stop(self):
        run, mercy, bot, _, fixture, controller = self.encounter()
        fight = run.match.combat
        target = next(t for t in fight._targets() if t.owner is mercy)
        health = fight.vitals_of(target)
        self.assertEqual(npc_control.enforcer_walk(run.match, mercy), bot.entity)
        bot.route = [(0, 1, 0)]
        bot.wait = 0
        fixture.tick(run, mercy, bot, 8, buttons=0)
        self.assertEqual(npc_control.start(run.match, mercy), bot.entity)
        start = bot.position[2]
        volley = []
        for _ in range(100):
            fixture.tick(run, mercy, bot, 1, buttons=0)
            volley = [r for r in fight.rockets if r.shooter is bot]
            if volley:
                break
        self.assertEqual(len(volley), 25)
        self.assertEqual(len({ray.direction for ray in volley}), 25)
        self.assertAlmostEqual(enforcer_movement.speed_scale(bot), 0.4)
        self.assertAlmostEqual(bot.pace, bot.speed * 0.4)
        self.assertLess(bot.position[2], start)
        for _ in range(25):
            fixture.tick(run, mercy, bot, 1, buttons=0)
            if not any(r.shooter is bot for r in fight.rockets):
                break
        self.assertEqual(health.total, 150)
        self.assertEqual(controller.entity_var(enforcer_movement.SPEED_GATE).value(), 1)
        self.assertEqual(npc_control.stop(run.match), 1)
        fixture.tick(run, mercy, bot, 35, buttons=0)
        self.assertFalse(bot.attack_input)
        self.assertFalse(controller.entity_var(enforcer_movement.SPEED_GATE).value())
        self.assertEqual(enforcer_movement.speed_scale(bot), 1)
        self.assertFalse(any(r.shooter is bot for r in fight.rockets))
        self.assertEqual(health.total, 150)

    def test_manual_removal_retires_controller_and_reserves_distinct_ids(self):
        run, mercy, bot, _, fixture, controller = self.encounter()
        match = run.match
        npc_control.enforcer_walk(match, mercy)
        definition = next(d for d in fixture.catalog.values() if d.name == "Enforcer")
        other = npc.spawn(match, definition, (4, 1, 5), team=1)
        self.assertNotEqual(other.entity, controller.entity)
        self.assertIn(bot.entity, npc.remove_spawned(match))
        self.assertTrue(controller.owner_dead)
        self.assertFalse(controller.instances)
