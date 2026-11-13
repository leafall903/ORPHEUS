"""Actual staged58 reaches canonical mode4; original health/source routes stay closed."""

import json
import unittest
from pathlib import Path
from unittest.mock import patch

from test_doomfist_combat import DoomfistFixture

from ow174.game import combat, defenses, doomfist, doomfist_helper_velocity
from ow174.game.commands import Command
from ow174.game.content import SOLDIER, heroes
from ow174.game.script import expr, graph, play, play_queue


class HelperVelocityTests(DoomfistFixture, unittest.TestCase):
    def stage(self, response=1, caller_r=1, advance_recipient=True):
        self.b.commands.last_frame = 9000
        hero = defenses.ORISA if response == 0 else SOLDIER
        self.match.switch_hero(self.b, heroes()[hero], start_frame=9000)
        self.b.take_commands([Command(9001, buttons=8 if response == 0 else 0)])
        x, y, z = self.a.mover.position
        self.place_player(self.b, (x, y, z + 3))
        self.a.body_script.component.set(10959, caller_r)
        source = next(i for i in self.a.body_script.component.instances.values() if i.graph.index == 0xD06)
        node = source.graph.node(16)
        captured = []
        original_damage = self.fight._damage

        def contact(*args, **kwargs):
            if len(args) > 4 and args[4] == doomfist.PUNCH:
                evaluate = source.evaluate

                def target(config, *values, **options):
                    if config is node.field('m_targets'):
                        return (expr.Entity(self.b.body),)
                    return evaluate(config, *values, **options)

                with patch.object(source, 'evaluate', side_effect=target):
                    captured.append(play.resolve_request(source, node, play.resolve_resource(source, node),
                        context_provider=lambda: play.InvocationContext(self.a.body, self.a.body,
                                                                        source.source_entity())))
                return False  # Suppress legacy damage/passive/force before mutation.
            return original_damage(*args, **kwargs)

        before, serial = self.vitals().total, self.fight.knockbacks.serial
        def source_step(commands):
            self.run.step(commands)
            if advance_recipient:
                self.b.take_commands([Command(self.b.commands.last_frame + 1)])

        with patch.object(self.fight, '_damage', side_effect=contact):
            for _ in range(18):
                source_step({self.a: (0, 0, 2)})
            source_step({})
            for _ in range(60):
                if captured:
                    break
                source_step({})
        request, = captured
        self.assertIsNone(source.vars.get(375).value() if 375 in source.vars else None)
        component = self.b.body_script.component
        ids = set(component.instances)
        queue = play_queue.Queue(component, lambda guid: play_queue.Load('ready', graph.graph(0xD07))
                                 if guid == request.resource.guid else None)
        queue.submit(request)
        helper, = [i for key, i in component.instances.items() if key not in ids]
        bridge = doomfist_helper_velocity.StagedVelocityBridge(component.world, self.a, self.b)
        original_velocity = component.world.velocity_action

        def velocity(instance, action):
            value = bridge(instance, action)
            return original_velocity(instance, action) if value is NotImplemented else value

        self.b.mover.velocity = (4.0, 0.0, 30.0)
        with patch.object(component.world, 'velocity_action', side_effect=velocity):
            component.run_now()
        self.assertEqual(self.vitals().total, before)
        self.assertFalse(self.a.client.messages(combat.HIT))
        return helper, bridge, queue, serial

    def step_recipient_clocks(self):
        self.run.frame += 1
        self.match.update(self.run.frame * combat.TICK, self.run.frame)
        for player in self.run.players:
            player.take_commands([Command(player.commands.last_frame + 1)])

    def test_actual58_admits_one_world_set_and_original48_alone_consumes_brakes(self):
        spec = json.loads((Path(__file__).resolve().parents[2] / 'fixtures/behavior'
                           / 'doomfist-helper-velocity58-v1.json').read_text())
        helper, bridge, queue, serial = self.stage()
        c, physical = helper.component, self.b.mover
        self.assertEqual(self.fight.knockbacks.serial, serial + 1)
        change, = physical.velocity_changes
        self.assertEqual((change.mode, change.priority, change.frame), (0, 1, c.frame + 1))
        vector = helper.evaluate(helper.graph.node(58).field('m_BC5E91CF')[0]['m_9B7A63EA'])
        self.assertEqual(change.value, tuple(vector))
        self.assertNotEqual(change.frame, self.a.body_script.component.frame + 1)
        self.assertFalse(any(key[0] == -3 for key in physical.mods))
        self.assertTrue(bridge(helper, helper.graph.node(58)))
        self.assertEqual(self.fight.knockbacks.serial, serial + 1)
        self.assertEqual(len(physical.velocity_changes), 1)
        self.assertTrue(helper.node_state(helper.graph.node(14)).active)
        self.assertTrue(helper.node_state(helper.graph.node(47)).active)
        for tick in range(1, 22):
            self.step_recipient_clocks()
            self.assertIs(self.b.mover, physical)
            if tick == 1:
                for actual, expected in zip(physical.velocity, vector, strict=True):
                    self.assertAlmostEqual(actual, expected, delta=1 / 2048)
            if tick in (2, 20):
                self.assertAlmostEqual(physical.values.decel_ground_base,
                                       physical.data.decel_ground_base * spec['ground_factor'], places=6)
                self.assertEqual(physical.values.decel_air_per_speed, spec['air_factor'])
                rows = [v for k, v in physical.mods.items() if k[0] == helper.id and k[1] == 23]
                self.assertEqual([(v.index, v.priority) for v in rows], [(4, 0), (7, 1)])
                self.assertFalse(any(k[0] == -3 for k in physical.mods))
            if tick == 21:
                self.assertAlmostEqual(physical.values.decel_ground_base, physical.data.decel_ground_base)
                self.assertAlmostEqual(physical.values.decel_air_per_speed, physical.data.decel_air_per_speed)
        self.assertEqual(self.fight.knockbacks.serial, serial + 1)
        self.assertFalse(self.a.client.messages(combat.HIT))
        c.destroy(helper)
        self.assertFalse(bridge(helper, helper.graph.node(58)))
        bridge.close()
        queue.retire()

    def test_live_zero_response_skips58_but_zero_vector_is_a_real_world_reset(self):
        helper, bridge, queue, serial = self.stage(response=0)
        self.assertFalse(bridge.results)
        self.assertFalse(helper.node_state(helper.graph.node(47)).active)
        self.assertEqual(self.fight.knockbacks.serial, serial)
        helper.component.destroy(helper)
        bridge.close()
        queue.retire()
        self.prepare()
        helper, bridge, queue, serial = self.stage(caller_r=0)
        self.assertTrue(bridge(helper, helper.graph.node(58)))
        self.assertTrue(helper.node_state(helper.graph.node(47)).active)
        self.assertEqual(self.fight.knockbacks.serial, serial + 1)
        change, = self.b.mover.velocity_changes
        self.assertEqual((change.mode, change.value), (0, (0.0, 0.0, 0.0)))
        self.step_recipient_clocks()
        self.assertEqual(self.b.mover.velocity, (0.0, 0.0, 0.0))
        self.assertFalse(any(k[0] == -3 for k in self.b.mover.mods))
        bridge.close()
        helper.component.destroy(helper)
        queue.retire()

    def test_stale_physical_clock_and_replaced_source_cannot_admit_or_replay(self):
        helper, bridge, queue, serial = self.stage(advance_recipient=False)
        self.assertFalse(bridge(helper, helper.graph.node(58)))
        self.assertEqual(self.fight.knockbacks.serial, serial)
        self.assertFalse(self.b.mover.velocity_changes)
        self.assertFalse(helper.node_state(helper.graph.node(47)).active)
        helper.component.destroy(helper)
        bridge.close()
        queue.retire()

    def test_shared_internal4_projection_still_preserves_a_faster_consumption_velocity(self):
        target = self.fight.player_target(self.b)
        component = self.b.body_script.component
        self.b.mover.velocity = (0.0, 2.0, 30.0)
        self.assertTrue(self.fight.knockbacks.apply_script_projected(
            target, expr.Vec3(0, 1, 15), recipient_component=component,
            source_entity=self.a.body, priority=1))
        change, = self.b.mover.velocity_changes
        self.assertEqual(change.mode, 4)
        self.step_recipient_clocks()
        self.assertGreater(self.b.mover.velocity[2], 20)
        self.prepare()
        helper, bridge, queue, serial = self.stage()
        self.assertEqual(self.fight.knockbacks.serial, serial + 1)
        self.match.switch_hero(self.a, heroes()[SOLDIER])
        self.assertFalse(bridge(helper, helper.graph.node(58)))
        self.assertEqual(self.fight.knockbacks.serial, serial + 1)
        helper.component.destroy(helper)
        bridge.close()
        queue.retire()
