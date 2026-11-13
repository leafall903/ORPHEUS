"""Actual acquisition drives authored input/target; no supplied ray hit or pose."""
import json
import unittest
from pathlib import Path
from unittest.mock import patch

from test_game_combat import in_world
from test_symmetra_combat import fire, health
from test_symmetra_turrets import SentryTests, wall

from ow174.game.content import SOLDIER, heroes
from ow174.game.script import expr


class SentryTargetEndpointTests(unittest.TestCase):
    def test_actual_acquisition_loss_reacquisition_and_single_damage_authority(self):
        path = (Path(__file__).resolve().parents[2]
                / 'fixtures/behavior/symmetra-sentry-target-endpoint-v1.json')
        contract = json.loads(path.read_text(encoding='utf-8'))
        button = contract['input']['logical_fire_button']
        fixture = SentryTests()
        run, owner, enemy = fixture.setup_arena()
        fight = run.match.combat
        with patch.object(fight, '_collision', return_value=wall()):
            fixture.launch(run, owner)
            sentry, = fight.sentries.sentries
            script = sentry.bot.body_script
            component = script.component
            beam = fight.sentries._beam(sentry)
            edges = []
            original = component.edges

            def observe(values):
                values = tuple(values)
                edges.extend(values)
                return original(values)

            with patch.object(component, 'edges', side_effect=observe):
                fire(run, owner, 80, buttons=0)
                self.assertEqual(sentry.target, enemy.body)
                self.assertIn(button, component.held)
                self.assertEqual(beam.find_var(expr.INSTANCE, 536).value(), expr.Entity(enemy.body))
                self.assertTrue(beam.node_state(beam.graph.node(9)).active)
                self.assertIsNone(beam.find_var(expr.INSTANCE, 535).value())
                self.assertIsNone(beam.find_var(expr.INSTANCE, 432).value())
                self.assertIsNone(script.controller)
                self.assertIsNone(sentry.bot.pet_owner)
                self.assertEqual(edges.count((20, True)), 1)
                duration = fight.now - (sentry.attached_at + .5)
                rate = contract['expected']['damage_per_second']
                self.assertAlmostEqual(200 - health(run, enemy).total, rate * duration, places=4)
                before = health(run, enemy).total
                state = beam.node_state(beam.graph.node(3))
                self.assertIs(component.world.modify_health(state, 1000), True)
                self.assertEqual(health(run, enemy).total, before)
                # An unrelated expression cannot acquire this exact endpoint.
                cfg = beam.graph.node(7).field('m_value')
                self.assertIs(fight.sentries.native(dict(cfg), beam), NotImplemented)
                self.assertIs(component.world.native(cfg['m_152B6961'], beam), None)
                other = run.match.add_player(3, 'Other', SOLDIER, 1, False)
                in_world(run.match, other)
                self.assertTrue(run.match.switch_hero(other, heroes()[SOLDIER]))
                other.mover.position = [2, 1, 2.5]
                run.players.append(other)
                fire(run, owner, 3, buttons=0)
                self.assertEqual(sentry.target, other.body)
                self.assertEqual(beam.find_var(expr.INSTANCE, 536).value(), expr.Entity(other.body))
                self.assertEqual(edges.count((20, True)), 1)
                self.assertEqual(edges.count((20, False)), 0)
                with patch.object(fight, '_collision', return_value=wall(True)):
                    fire(run, owner, 3, buttons=0)
                self.assertIsNone(sentry.target)
                self.assertNotIn(20, component.held)
                self.assertFalse(beam.node_state(beam.graph.node(9)).active)
                self.assertEqual(edges.count((20, False)), 1)
                fire(run, owner, 3, buttons=0)
                self.assertEqual(beam.find_var(expr.INSTANCE, 536).value(), expr.Entity(other.body))
                self.assertEqual(edges.count((20, True)), 2)
                fight.sentries._retire(sentry)
                self.assertNotIn(20, component.held)
                self.assertEqual(edges.count((20, False)), 2)
                self.assertIs(fight.sentries.native(cfg, beam), NotImplemented)
                self.assertIsNone(component.fault)
