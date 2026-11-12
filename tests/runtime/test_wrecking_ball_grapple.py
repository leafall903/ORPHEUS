"""Drive Wrecking Ball's 104319 Grappling Claw graph with real Match commands."""

import unittest
from dataclasses import replace

from ow174.game import observers
from ow174.game.body_vm_snapshot import export_body_vm
from ow174.game.collision import StaticCollisionProvider, World
from ow174.game.commands import Command
from ow174.game.content import PRACTICE_RANGE, Spawn, heroes
from ow174.game.match import Match
from ow174.game.script import decode, graph, grapple, nodes
from ow174.game.script.expr import Vec3
from ow174.game.statescript import chunk
from tests.runtime.test_body_vm_snapshot import elements, fields, raw
from tests.runtime.test_game_mover import FLOOR, wall
from tests.runtime.test_game_notices import into_world

WRECKING_BALL = 0x02E00000000001CA
ROLL = 0x08
GRAPPLE = 0x02
START = 1000
CAST = START + 352
SCENE = replace(PRACTICE_RANGE, spawns=(Spawn(None, (0.0, 1.0, 0.0), 0.0),))


class GrappleTests(unittest.TestCase):
    def setup_match(self, wall_z=None):
        geometry = FLOOR if wall_z is None else FLOOR + wall(-4, wall_z, 4, wall_z)
        match = Match(SCENE, collision_provider=StaticCollisionProvider(World(geometry)))
        hero = heroes()[WRECKING_BALL]
        player = match.add_player(1, hero.name, hero.guid, 0, False)
        into_world(match, player, START)
        self.assertTrue(match.switch_hero(player, hero))
        match.bots.clear()
        match._pending_bot_scripts.clear()
        instance = next(
            item for item in player.body_script.component.instances.values()
            if item.graph.index == grapple.GRAPH
        )
        for frame in range(START + 1, CAST - 2):
            self.command(player, frame)
        self.command(player, CAST - 2, ROLL)
        self.command(player, CAST - 1)
        return player, instance

    @staticmethod
    def command(player, frame, buttons=0, *, pitch=0):
        player.match.tick = frame
        player.take_commands([Command(frame, buttons=buttons, pitch=pitch)])

    def test_wall_hit_advances_at_authored_speed_and_releases(self):
        player, instance = self.setup_match(wall_z=8)
        script = player.body_script
        _, baseline = script.tree_frame()
        self.command(player, CAST, GRAPPLE)
        trace = instance.state(0)
        attached = instance.state(21)
        launch = instance.vars[431].value()
        self.assertIsInstance(trace, grapple.GrappleTrace)
        self.assertEqual(launch.x, player.mover.position[0])
        self.assertAlmostEqual(launch.y - player.mover.position[1], grapple.LAUNCH_HEIGHT, places=6)
        self.assertEqual(launch.z, player.mover.position[2])
        self.assertTrue(trace.active)
        self.assertFalse(attached.active)
        payload, _, launched = script.delta(baseline, set(), CAST, False)
        graphs = {owner: item[0] for owner, item in launched.instances.items()}
        wire = chunk(payload, 32, 0)
        frame = decode.read_chunk(wire.getvalue(), wire.count, graphs)
        self.assertTrue(frame.owner)
        self.assertEqual(frame.cmfd, CAST)
        self.assertEqual(
            frame.instances[instance.id].states[trace.index], (True, {"start": CAST * script.frame_ms}),
        )
        key = (instance.id, trace.index)
        self.assertIn(key, observers.snapshot(script).states)
        stored = fields(raw(export_body_vm(script)), ["state", instance.id, trace.index])
        self.assertEqual(elements(stored["flight"])[2:4], [200.0, 8.0])

        first_endpoint = instance.vars[432].value()
        self.assertAlmostEqual(first_endpoint.z - launch.z, 200.0 * 0.015, places=5)
        self.command(player, CAST + 1, GRAPPLE)
        self.assertAlmostEqual(instance.vars[432].value().z - first_endpoint.z, 200.0 * 0.016, places=5)
        self.assertFalse(attached.active)
        self.command(player, CAST + 2, GRAPPLE)
        self.assertFalse(trace.active)
        self.assertTrue(attached.active)
        self.assertIs(instance.vars[435].value(), True)
        point = Vec3(launch.x, launch.y, 8.0)
        self.assertEqual(instance.vars[431].value(), point)
        self.assertEqual(instance.vars[432].value(), point)
        self.assertEqual(instance.vars[2822].value(), Vec3(0.0, 0.0, -1.0))
        payload, _, _ = script.delta(launched, set(), CAST + 2, True)
        wire = chunk(payload, 33, 0)
        frame = decode.read_chunk(wire.getvalue(), wire.count, graphs)
        record = frame.instances[instance.id]
        self.assertEqual(record.states[trace.index], (False, {}))
        self.assertEqual(record.states[attached.index], (True, {}))
        self.assertEqual(record.vars[432], (point, ()))

        self.command(player, CAST + 3)
        self.assertFalse(attached.active)
        self.assertTrue(instance.state(1).active)  # the authored retract branch started
        self.assertIs(instance.vars[435].value(), False)
        self.assertFalse(instance.state(25).flags & nodes.Ability.RUNNING)

    def test_unmodeled_widowmaker_trace_stays_off_both_wire_views(self):
        player, _ = self.setup_match()
        script = player.body_script
        widow = script.component.create(graph.graph(0x005A), init=False)
        trace = widow.state(0)
        trace.begin()
        self.assertTrue(trace.active)
        self.assertEqual(trace.payload(), {})
        key = (widow.id, trace.index)
        self.assertNotIn(key, script.snapshot().states)
        self.assertNotIn(key, observers.snapshot(script).states)

    def test_out_of_range_wall_does_not_attach_or_leave_the_ability_running(self):
        player, instance = self.setup_match(wall_z=30)
        self.command(player, CAST, GRAPPLE)
        launch = instance.vars[431].value()
        for frame in range(CAST + 1, CAST + 6):
            self.command(player, frame, GRAPPLE)
            self.assertTrue(instance.state(0).active)
        self.command(player, CAST + 6, GRAPPLE)
        self.assertFalse(instance.state(0).active)
        self.assertFalse(instance.state(21).active)
        self.assertFalse(instance.state(25).flags & nodes.Ability.RUNNING)
        self.assertIs(instance.vars[435].value(), False)
        self.assertAlmostEqual(instance.vars[432].value().z - launch.z, 22.2, places=5)

    def test_releasing_while_the_hook_is_in_flight_prevents_attachment(self):
        player, instance = self.setup_match(wall_z=8)
        self.command(player, CAST, GRAPPLE)
        self.assertTrue(instance.state(0).active)
        self.command(player, CAST + 1)
        self.command(player, CAST + 2)
        self.assertFalse(instance.state(0).active)
        self.assertFalse(instance.state(21).active)
        self.assertFalse(instance.state(25).flags & nodes.Ability.RUNNING)

    def test_existing_graph_rejects_floor_contacts(self):
        player, instance = self.setup_match()
        self.command(player, CAST, GRAPPLE, pitch=8192)
        self.command(player, CAST + 1, GRAPPLE, pitch=8192)
        self.assertFalse(instance.state(0).active)
        self.assertFalse(instance.state(21).active)
        self.assertFalse(instance.state(25).flags & nodes.Ability.RUNNING)
        self.assertEqual(instance.vars[2822].value(), Vec3(0.0, 1.0, 0.0))

    def test_held_attachment_expires_at_the_authored_wait(self):
        player, instance = self.setup_match(wall_z=8)
        for frame in range(CAST, CAST + 3):
            self.command(player, frame, GRAPPLE)
        attached = instance.state(21)
        wait = instance.state(65)
        self.assertTrue(attached.active)
        self.assertTrue(wait.active)
        expiry = CAST + 2 + 6000 // player.body_script.frame_ms
        for frame in range(CAST + 3, expiry):
            self.command(player, frame, GRAPPLE)
        self.assertTrue(attached.active)
        self.command(player, expiry, GRAPPLE)
        self.assertFalse(wait.active)
        self.assertFalse(attached.active)
        self.assertFalse(instance.state(25).flags & nodes.Ability.RUNNING)
        self.assertIs(instance.vars[435].value(), False)
