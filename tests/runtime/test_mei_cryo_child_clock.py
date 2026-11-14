"""Cryo child progression after a real unequal-epoch encrypted hero switch."""
import json
import unittest
from pathlib import Path

import test_server_snapshot as snapshot_tests
from test_server_journal import pick

from ow174.game import mei

CONTRACT = json.loads((Path(__file__).resolve().parents[2]
                       / 'fixtures/behavior/mei-cryo-child-clock-v1.json').read_text())


class CryoChildClockTests(unittest.TestCase):
    def test_world_steps_advance_same_child_while_owner_commands_stop(self):
        session, peers, players = snapshot_tests.ServerSnapshotTests().scene(two=True)
        player = players[0]
        now = 101.1
        old = player.body_script
        peers[0].commands(old.frame + 1, 40, 0, now=now)
        peers[0].message(21616, pick(player.entity, mei.HERO), now=now)
        script = player.body_script
        self.assertNotEqual(*script.cryo_birth)
        for _ in range(8):
            now += .016
            session.tick(now)
            for peer in peers:
                peer.drain()
                peer.send(now=now)
        peers[0].commands(script.frame + 1, 40, 0, now=now)
        peers[0].commands(script.frame + 1, 1, 8, now=now)
        fight = player.match.combat
        child = next(b for b in fight.barriers.scripted.values() if b.parent is player)
        component = child.component
        before = component.frame
        frozen_owner = script.frame, script.component.now
        wanted = mei.world_frame(player, component.frame) + CONTRACT['child_frame_step']
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
        self.assertIs(fight.barriers.scripted[child.entity], child)
        self.assertIs(child.component, component)
        self.assertEqual((script.frame, script.component.now), frozen_owner)
        self.assertIs(child.source, script.component)
        self.assertEqual(component.frame, before + CONTRACT['child_frame_step'])
        self.assertEqual(component.now, (component.frame + 1) * component.frame_ms - 1)
        deadline = mei.cryo_world_deadline(player)
        amount = deadline - session.server.tick
        self.assertGreater(amount, 0)
        now += amount * .016
        for peer in peers:
            peer.send(now=now - .001)
        session.tick(now, missed=amount - 1)
        for peer in peers:
            peer.drain()
            peer.send(now=now)
        self.assertEqual(fight.tick, deadline)
        self.assertNotIn(child.entity, fight.barriers.scripted)
        self.assertEqual((script.frame, script.component.now), frozen_owner)
