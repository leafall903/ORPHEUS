"""Original source14 publication from actual commands and registered capsules."""

import json
import os
import unittest
from pathlib import Path
from unittest.mock import patch

from test_doomfist_combat import DoomfistFixture
from test_game_mover import box

from ow174.game import npc
from ow174.game.collision import World, flat_world
from ow174.game.script import expr


class Source14QueryTests(DoomfistFixture, unittest.TestCase):
    def scene(self, *, bot=False):
        self.prepare(3)
        self.match.collision_world = flat_world(0)
        self.place_player(self.a, (1000, 0, 1000))
        self.place_player(self.b, (1000, 0, 1003))
        self.run.step({})
        recipient = self.b
        if bot:
            self.place_player(self.b, (1100, 0, 1100))
            catalog = npc.load_catalog(Path(os.environ['ORPHEUS_RUNTIME_DIR']) / 'data/pve_npcs_174.json')
            definition = next(d for d in catalog.values() if d.name == 'Trooper')
            recipient = npc.spawn(self.match, definition, (1000, 0, 1003), team=1-self.a.team)
            recipient.route, recipient.firing = (), False
            self.run.step({})
        source = next(i for i in self.a.body_script.component.instances.values() if i.graph.index == 0xD06)
        return source, recipient

    def test_original_source_query_origins_are_typed_eye_offsets(self):
        source, _ = self.scene()
        self.assertIs(source.component.world.tracked_targets(source.state(6)), NotImplemented)
        fields = source.graph.node(14)
        spec = json.loads((Path(__file__).resolve().parents[2] / 'fixtures/behavior'
                           / 'doomfist-source14-query-v1.json').read_text())
        for key, offset in spec['eye_offsets'].items():
            actual = source.evaluate(fields.field(key))
            self.assertIsInstance(actual, expr.Vec3)
            expected = self.fight._eye(self.a)
            self.assertAlmostEqual(actual.x, expected[0])
            self.assertAlmostEqual(actual.y, expected[1]+offset, places=5)
            self.assertAlmostEqual(actual.z, expected[2])

    def test_actual_commands_original_state_publishes_registered_player_and_npc(self):
        from ow174.game import doomfist_targets

        spec = json.loads((Path(__file__).resolve().parents[2] / 'fixtures/behavior'
                           / 'doomfist-source14-query-v1.json').read_text())
        for is_bot in (False, True):
            with self.subTest(bot=is_bot):
                source, recipient = self.scene(bot=is_bot)
                entity = recipient.entity if is_bot else recipient.body
                world = source.component.world
                old = world.tracked_targets
                published, queries, calls = [], [], []
                original_write, original_node = source.write, source.run_node
                serial = self.fight.knockbacks.serial
                def query(state, world=world, old=old, queries=queries, source=source):
                    result = doomfist_targets.tracked_targets(world, state)
                    if result is NotImplemented:
                        return old(state)
                    queries.append((self.match.tick, source.component.frame, result))
                    return result
                def write(key, value, published=published, original_write=original_write):
                    if key == (expr.INSTANCE, 375):
                        published.append(value)
                    return original_write(key, value)
                def execute(node, caller, calls=calls, source=source, original_node=original_node):
                    if node.pos == 16:
                        calls.append((self.match.tick, source.vars[375].value()))
                    return original_node(node, caller)
                with patch.object(world, 'tracked_targets', side_effect=query), \
                     patch.object(source, 'write', side_effect=write), \
                     patch.object(source, 'run_node', side_effect=execute), \
                     patch.object(doomfist_targets, 'Volume', wraps=doomfist_targets.Volume) as volumes, \
                     patch.object(self.fight, '_damage', return_value=0):
                    self.run.step({self.a: (0, 0, 2)})
                    for _ in range(25):
                        self.run.step({})
                self.assertIn((expr.Entity(entity),), published)
                self.assertEqual(list(volumes.call_args_list[0].args[2]),
                                 spec['native_minimum_charge_volume']['outer_cos'])
                self.assertEqual(list(volumes.call_args_list[0].args[3]),
                                 spec['native_minimum_charge_volume']['outer_extent'])
                # Publication alone does not qualify the original transition,
                # paired output, normal PlayScript route or health completion.
                self.assertEqual(calls, [])
                self.assertTrue(all(world_tick == caller_tick for world_tick, caller_tick, _ in queries))
                self.assertNotIn(23530, source.vars)
                self.assertEqual(self.fight.knockbacks.serial, serial)
                self.assertFalse(any(i.graph.index == 0xD07
                                     for i in recipient.body_script.component.instances.values()))

    def test_query_uses_geometry_and_live_identity_without_legacy_contact(self):
        from ow174.game import doomfist_targets

        for mode in ('front', 'behind', 'side', 'wall', 'ally', 'retired', 'stale_registry'):
            with self.subTest(mode=mode):
                source, recipient = self.scene()
                self.match.game_map = self.match.game_map.__class__(**{
                    **self.match.game_map.__dict__, 'free_for_all': False})
                recipient.team = 1-self.a.team
                self.place_player(recipient, (1000, 0, 1001.6))
                if mode == 'behind':
                    self.place_player(recipient, (1000, 0, 998.4))
                elif mode == 'side':
                    self.place_player(recipient, (1001.6, 0, 1000))
                elif mode == 'wall':
                    self.match.collision_world = World(box(999, 1001, -1, 3, 1000.5, 1000.6))
                elif mode == 'ally':
                    recipient.team = self.a.team
                elif mode == 'retired':
                    recipient.body_script.retired = True
                elif mode == 'stale_registry':
                    self.match.body_components[recipient.body] = source.component
                world = source.component.world
                old, results = world.tracked_targets, []
                def query(state, world=world, old=old, results=results):
                    result = doomfist_targets.tracked_targets(world, state)
                    if result is NotImplemented:
                        return old(state)
                    results.append(result)
                    return result
                # The original state's first query runs before any legacy contact.
                with patch.object(world, 'tracked_targets', side_effect=query), \
                     patch.object(self.fight, '_damage', return_value=0):
                    self.run.step({self.a: (0, 0, 2)})
                    for _ in range(13):
                        self.run.step({})
                expected = (recipient.body,) if mode == 'front' else ()
                self.assertTrue(results)
                self.assertEqual(results[0], expected)
