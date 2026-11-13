"""Actual sentry input with declared changing pose matrices and real collision.

The matrices are synthetic current host inputs, not recovered live native poses.
No RaycastResult, hit variable, effect or graph state is injected.
"""
import json
import unittest
from pathlib import Path
from unittest.mock import patch

from test_symmetra_combat import fire, health
from test_symmetra_turrets import SentryTests, wall

from ow174.game.body_vm_snapshot import export_component_vm, snapshot_digest
from ow174.game.props import ModelPose
from ow174.game.script import expr, runtime


class SentryCurrentRayTests(unittest.TestCase):
    def test_late_current_pose_hit_miss_obstruction_withdrawal_and_link_cleanup(self):
        path = (Path(__file__).resolve().parents[2]
                / 'fixtures/behavior/symmetra-sentry-current-ray-v1.json')
        contract = json.loads(path.read_text(encoding='utf-8'))
        inputs = contract['input']
        fixture = SentryTests()
        run, owner, enemy = fixture.setup_arena()
        fight = run.match.combat
        with patch.object(fight, '_collision', return_value=wall()):
            fixture.launch(run, owner)
            fire(run, owner, 80, buttons=0)
            sentry, = fight.sentries.sentries
            beam = fight.sentries._beam(sentry)
            state = beam.node_state(beam.graph.node(9))
            self.assertTrue(state.active)
            self.assertIsNone(beam.find_var(0, 535).value())
            # Explicit pose matrices are independent of the actor's feet. A
            # fallback to bot.position or a bind-pose offset cannot pass this.
            points = {sentry.bot.entity: tuple(inputs['source_point']),
                      enemy.body: tuple(inputs['target_point'])}
            def matrix(entity):
                return (1., 0., 0., 0., 0., 1., 0., 0., 0., 0., 1., 0., *points[entity], 1.)
            poses = {
                sentry.bot.entity: ModelPose({0x0DC0000000000006: (0, (0., 0., 0.))},
                                             lambda _: matrix(sentry.bot.entity)),
                enemy.body: ModelPose({0x0DC0000000000001: (0, (0., 0., 0.))},
                                     lambda _: matrix(enemy.body)),
            }
            fight.model_pose_for = poses.get
            fire(run, owner, 2, buttons=0)
            self.assertEqual(beam.find_var(0, 535).value(), expr.Entity(enemy.body))
            self.assertIs(beam.find_var(0, 2413).value(), True)
            self.assertTrue(beam.node_state(beam.graph.node(3)).active)
            first = beam.find_var(0, 432).value()
            self.assertIsInstance(first, expr.Vec3)
            self.assertEqual(beam.find_var(0, 431).value(), first)
            self.assertNotEqual(tuple(first), points[enemy.body])
            exported = export_component_vm(beam.component)
            self.assertEqual(snapshot_digest(exported), snapshot_digest(export_component_vm(beam.component)))
            target_pose = poses[enemy.body]
            old_body = enemy.body
            self.assertTrue(run.match.switch_hero(enemy, enemy.hero))
            self.assertNotEqual(enemy.body, old_body)
            enemy.mover.position = [2, 1, 1]
            self.assertFalse(state.active)
            self.assertIsNone(beam.find_var(0, 535).value())
            poses.pop(old_body)
            points[enemy.body] = tuple(inputs['target_point'])
            fire(run, owner, 1, buttons=0)
            self.assertTrue(state.active)
            self.assertIsNone(state.result)
            self.assertIsNone(beam.find_var(0, 535).value(),
                              {'retiredBody': old_body, 'newTarget': enemy.body,
                               'healthGateActive': beam.node_state(beam.graph.node(3)).active})
            self.assertFalse(beam.node_state(beam.graph.node(3)).active)
            poses[enemy.body] = target_pose
            fire(run, owner, 1, buttons=0)
            self.assertEqual(beam.find_var(0, 535).value(), expr.Entity(enemy.body))
            before = health(run, enemy).total
            fire(run, owner, 1, buttons=0)
            rate = contract['expected']['damage_per_second']
            self.assertAlmostEqual(before - health(run, enemy).total, .016 * rate, places=4)
            # Move the declared current endpoint away from actual body geometry.
            points[enemy.body] = tuple(inputs['miss_endpoint'])
            fire(run, owner, 1, buttons=0)
            self.assertEqual(beam.find_var(0, 535).value(), expr.Entity(0))
            self.assertIs(beam.find_var(0, 2413).value(), False)
            self.assertEqual(tuple(beam.find_var(0, 432).value()), points[enemy.body])
            # Actual wall geometry intercepts this new current hardpoint segment.
            points[enemy.body] = tuple(inputs['target_point'])
            points[sentry.bot.entity] = tuple(inputs['obstructed_source'])
            fire(run, owner, 1, buttons=0)
            self.assertEqual(beam.find_var(0, 535).value(), expr.Entity(0))
            self.assertIs(beam.find_var(0, 2413).value(), True)
            self.assertAlmostEqual(beam.find_var(0, 432).value().z, 3., places=5)
            poses.pop(enemy.body)
            fire(run, owner, 1, buttons=0)
            self.assertIsNone(state.result)
            self.assertIsNone(beam.find_var(0, 535).value())
            self.assertIs(beam.find_var(0, 2413).value(), False)
            self.assertIsNone(beam.find_var(0, 432).value())
            self.assertFalse(beam.node_state(beam.graph.node(3)).active)
            self.assertNotEqual(snapshot_digest(exported),
                                snapshot_digest(export_component_vm(beam.component)))
            poses[enemy.body] = target_pose
            points[sentry.bot.entity] = tuple(inputs['source_point'])
            fire(run, owner, 1, buttons=0)
            self.assertEqual(beam.find_var(0, 535).value(), expr.Entity(enemy.body))
            fight.sentries._retire(sentry)
            self.assertIsNone(beam.find_var(0, 535).value())
            self.assertIs(beam.find_var(0, 2413).value(), False)
            self.assertIsNone(state.result)
            for key in (431, 432):
                self.assertFalse(any(link.state is state for link in beam.find_var(0, key).links))
            self.assertIsNone(beam.component.fault)

    def test_generic_and_strict_track_ray_factories_are_unchanged(self):
        from ow174.game.script.nodes import CompatibleTrackRay, TrackRay

        self.assertIs(runtime.state_type('STUStatescriptStateTrackRay', False, 0x1590, 9,
                                         hero=1), CompatibleTrackRay)
        self.assertIs(runtime.state_type('STUStatescriptStateTrackRay', True, 0x1590, 9,
                                         hero=0x02E0000000000016), TrackRay)
