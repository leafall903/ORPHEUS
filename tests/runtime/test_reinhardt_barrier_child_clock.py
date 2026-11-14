"""Barrier clock consistency through encrypted input and a real hero switch."""
import json
import unittest
from pathlib import Path

import test_server_snapshot as snapshot_tests
from test_server_journal import pick

from ow174.game import reinhardt_projectiles
from ow174.game.combat import TICK

CONTRACT = json.loads((Path(__file__).resolve().parents[2]
                       / 'fixtures/behavior/reinhardt-barrier-child-clock-v1.json').read_text())


class BarrierChildClockTests(unittest.TestCase):
    def test_world_steps_advance_same_barrier_then_neutral_input_lowers_it(self):
        session, peers, players = snapshot_tests.ServerSnapshotTests().scene(two=True)
        player = players[0]
        now = 101.1
        old = player.body_script
        peers[0].commands(old.frame + 1, 40, 0, now=now)
        peers[0].message(21616, pick(player.entity, 0x02E0000000000007), now=now)
        script = player.body_script
        for _ in range(8):
            now += .016
            session.tick(now)
            for peer in peers:
                peer.drain()
                peer.send(now=now)
        peers[0].commands(script.frame + 1, 40, 0, now=now)
        peers[0].commands(script.frame + 1, CONTRACT['raise_commands'], 2, now=now)
        fight = player.match.combat
        child = fight.barriers.authored_for(player.body)
        self.assertIsNotNone(child)
        component = child.component
        before = component.frame
        frozen_owner = script.frame, script.component.now
        origin = reinhardt_projectiles.clock_origin(fight, player)
        self.assertIsNotNone(origin)
        world_birth, body_birth, frame_ms = origin
        self.assertNotEqual(world_birth, body_birth)
        quantum = round(TICK * 1000)
        elapsed = (before + CONTRACT['child_frame_step'] - body_birth) * frame_ms
        wanted = world_birth + (elapsed + quantum - 1) // quantum
        amount = wanted - session.server.tick
        self.assertGreater(amount, 0)
        now += amount * .016
        for peer in peers:
            peer.send(now=now - .001)
        session.tick(now, missed=amount - 1)
        for peer in peers:
            peer.drain()
            peer.send(now=now)
        self.assertEqual(fight.tick, wanted)
        self.assertIs(fight.barriers.authored_for(player.body), child)
        self.assertIs(child.component, component)
        self.assertIs(child.source, script.component)
        self.assertEqual((script.frame, script.component.now), frozen_owner)
        self.assertEqual(reinhardt_projectiles.clock_origin(fight, player), origin)
        self.assertEqual(component.frame, before + CONTRACT['child_frame_step'])
        self.assertEqual(component.now, (component.frame + 1) * frame_ms - 1)
        peers[0].commands(script.frame + 1, CONTRACT['lower_commands'], 0, now=now)
        now += .016
        session.tick(now)
        self.assertNotIn(child.entity, fight.barriers.scripted)
        self.assertIsNone(fight.barriers.authored_for(player.body))
        self.assertIs(player.body_script, script)
