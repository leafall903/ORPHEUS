"""Actual raw-input Hypersphere wall state remains causal in strict storage."""
import unittest
from unittest.mock import patch

import test_server_snapshot as stored
from test_server_journal import pick
from test_game_mover import box
from ow174.game.collision import World
from ow174.game.commands import FIRE
from ow174.game.script import runtime
from ow174.game.body_vm_snapshot import export_component_vm, snapshot_digest as vm_digest
from ow174.game.server_snapshot import snapshot_digest


class SigmaSphereStorageTests(unittest.TestCase):
    def test_raw_bounced_sphere_keeps_its_real_vm_and_all_causal_flight_state(self):
        case = stored.ServerSnapshotTests()
        session, peers, players = case.scene()
        player, = players
        now = 101.1
        peers[0].message(21616, pick(player.entity, 0x02E000000000023B), now=now)
        for _ in range(8):
            now += .016
            session.tick(now)
            peers[0].drain()
            peers[0].send(now=now)
        fight = player.match.combat
        x, y, z = player.mover.position
        floor = [(-5000,y,-5000,-5000,y,5000,5000,y,5000),
                 (-5000,y,-5000,5000,y,5000,5000,y,-5000)]
        player.match.collision_world = World(floor + box(x-4,x+4,y,y+4,z+6,z+7))
        player.mover.use(world=player.match.collision_world)
        peers[0].commands(player.body_script.frame+1, 1, FIRE, now=now)
        sphere = None
        for _ in range(70):
            now += .016
            session.tick(now)
            peers[0].drain()
            peers[0].send(now=now)
            sphere = next((r for r in fight.rockets if r.source_graph == 0x187F
                           and r.terrain_bounce_limit == 99), None)
            if sphere is not None:
                break
            peers[0].commands(player.body_script.frame+1, 1, 0, now=now)
        self.assertIsNotNone(sphere)
        self.assertLess(sphere.speed, 2)
        self.assertLess(sphere.velocity[2], 0)
        self.assertGreater(sphere.left, 0)
        component = player.body_script.component
        self.assertIs(sphere.source_state.instance.component, component)
        vm_digest(export_component_vm(component))
        frame = player.body_script.frame
        self.assertNotEqual(frame, fight.tick)
        baseline = snapshot_digest(case.capture(session))
        with (patch.object(runtime.Var, "value", side_effect=AssertionError("snapshot evaluated variable")),
              patch.object(runtime.Component, "run_now", side_effect=AssertionError("snapshot ran VM"))):
            self.assertEqual(snapshot_digest(case.capture(session)), baseline)
            for field, value in (
                ("terrain_bounce_limit", 98),
                ("terrain_bounce", (sphere.terrain_bounce[0]+.01, *sphere.terrain_bounce[1:])),
                ("velocity", (sphere.velocity[0], sphere.velocity[1]+.01, sphere.velocity[2])),
                ("left", sphere.left+.001),
            ):
                old = getattr(sphere, field)
                setattr(sphere, field, value)
                self.assertNotEqual(snapshot_digest(case.capture(session)), baseline)
                setattr(sphere, field, old)
                self.assertEqual(snapshot_digest(case.capture(session)), baseline)
        for _ in range(2):
            now += .016
            session.tick(now)
            peers[0].drain()
            peers[0].send(now=now)
        self.assertEqual(player.body_script.frame, frame)
        self.assertIs(sphere.source_state.instance.component, component)
        self.assertEqual(sphere.terrain_bounce_limit, 99)
        self.assertLess(sphere.speed, 2)
        snapshot_digest(case.capture(session))
