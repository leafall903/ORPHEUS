"""Actual raw Grasp receipt phases drive the same pool without advancing body input."""
import math
import unittest
from unittest.mock import patch

import test_server_snapshot as stored
from test_server_journal import pick
from test_game_combat import aim
from ow174.game import interception
from ow174.game.collision import World
from ow174.game.commands import FIRE
from ow174.game.script import runtime
from ow174.game.body_vm_snapshot import export_component_vm, snapshot_digest as vm_digest
from ow174.game.server_snapshot import snapshot_digest


class SigmaDecayClockTests(unittest.TestCase):
    def test_actual_unequal_epoch_grant_decays_world_only_and_is_strictly_stored(self):
        case = stored.ServerSnapshotTests()
        session, peers, players = case.scene(two=True)
        sigma, enemy = players
        now = 101.1
        peers[0].message(21616, pick(sigma.entity, 0x02E000000000023B), now=now)
        for _ in range(8):
            now += .016
            session.tick(now)
            for peer in peers:
                peer.drain()
                peer.send(now=now)
        fight = sigma.match.combat
        x, y, z = sigma.mover.position
        sigma.match.collision_world = World([
            (-5000,y,-5000,-5000,y,5000,5000,y,5000),
            (-5000,y,-5000,5000,y,5000,5000,y,-5000)])
        for player in players:
            player.mover.use(world=sigma.match.collision_world)
        fight.walls.place_player(enemy, (x,y,z+6))
        face = aim(fight._eye(enemy), fight._eye(sigma))
        peers[0].commands(sigma.body_script.frame+1, 1, 8, now=now)
        target = next(t for t in fight._targets() if t.owner is sigma)
        vitals = fight.vitals_of(target)
        observed = []
        deliver = interception.play_shield_script
        def producer(world, instance, node):
            observed.append((instance.component.frame, instance.component.now, fight.tick))
            return deliver(world, instance, node)
        with patch.object(interception, "play_shield_script", side_effect=producer):
            for frame in range(180):
                peers[1].commands(enemy.body_script.frame+1, 1, FIRE if frame < 20 else 0,
                                  yaw=face[0], pitch=face[1], now=now)
                now += .016
                session.tick(now)
                for peer in peers:
                    peer.drain()
                    peer.send(now=now)
                if sigma.body in fight.absorption.decay_clocks and vitals.temporary_shields > 0:
                    break
                peers[0].commands(sigma.body_script.frame+1, 1, 0, now=now)
        self.assertIn(sigma.body, fight.absorption.decay_clocks)
        clock = fight.absorption.decay_clocks[sigma.body]
        self.assertNotEqual(clock[2], clock[4])
        self.assertEqual(clock[2:5], observed[-1])
        frame = sigma.body_script.frame
        component = sigma.body_script.component
        body_ms = component.now
        initial = vitals.temporary_shields
        self.assertGreater(initial, 0)
        source = component.instances[clock[0]]
        wait = source.states[source.graph.node(71).state]
        # Independent original entered-Wait deadline, not the stored delay value.
        body_deadline = wait.start + wait.ms
        producer_frame, _producer_ms, producer_world = observed[-1]
        deadline = producer_world + (body_deadline-producer_frame*sigma.body_script.frame_ms)/16
        while fight.tick < math.floor(deadline):
            now += .016
            session.tick(now)
            for peer in peers:
                peer.drain()
                peer.send(now=now)
        self.assertEqual(vitals.temporary_shields, initial)
        through = math.ceil(deadline)+125
        while fight.tick < through:
            now += .016
            session.tick(now)
            for peer in peers:
                peer.drain()
                peer.send(now=now)
        expected = initial-clock[6]*(fight.tick-deadline)*.016
        self.assertAlmostEqual(vitals.temporary_shields, expected, places=5)
        self.assertEqual(sigma.body_script.frame, frame)
        self.assertEqual(component.now, body_ms)
        vm_digest(export_component_vm(component))
        baseline = snapshot_digest(case.capture(session))
        with (patch.object(runtime.Var, "value", side_effect=AssertionError("snapshot evaluated variable")),
              patch.object(runtime.Component, "run_now", side_effect=AssertionError("snapshot ran VM"))):
            self.assertEqual(snapshot_digest(case.capture(session)), baseline)
            changed = (*clock[:3],clock[3]+1,*clock[4:])
            fight.absorption.decay_clocks[sigma.body] = changed
            self.assertNotEqual(snapshot_digest(case.capture(session)), baseline)
            fight.absorption.decay_clocks[sigma.body] = clock
            self.assertEqual(snapshot_digest(case.capture(session)), baseline)
