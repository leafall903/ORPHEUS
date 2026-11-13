"""Small production packet-size check for Winston's two relevant lifecycles."""

import json
import unittest
from pathlib import Path
from types import SimpleNamespace

import test_server_journal as journal

from ow174.game import winston


class WinstonPacketClockTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        path = Path(__file__).resolve().parents[2] / "fixtures/behavior/winston-packet-clock-v1.json"
        cls.contract = json.loads(path.read_text(encoding="utf-8"))

    def sample(self, batch, buttons):
        fixture = journal.ServerJournalTests()
        session, sent = fixture.make()
        peers, players = fixture.connected(session, sent, two=True, first_hero=winston.HERO)
        owner, enemy = players
        peer = peers[0]
        owner.match.bots.clear()
        owner.mover.position[:] = (0, 0, 0)
        enemy.mover.position[:] = (0, 0, 16)
        base = owner.body_script.frame
        fight = owner.match.combat
        events = []
        impact = fight.winston_jump.impact

        def contact(player, instance, frame, *, landing):
            events.append((frame, landing, tuple(player.mover.position)))
            return impact(player, instance, frame, landing=landing)

        fight.winston_jump.impact = contact
        rows = []
        sphere = None
        for step in range(1, 161):
            now = 100.928 + step * .016
            session.tick(now)
            for ack in peers:
                ack.drain()
                ack.send(now=now)
            if step % batch == 0:
                peer.commands(base + step - batch + 1, batch, buttons,
                              pitch=self.contract["input"]["pitch"], now=now)
            if step in self.contract["input"]["query_steps"]:
                actors = tuple(fight.barriers.world.actors.values())
                if actors:
                    sphere, = actors
                rows.append((owner.match.tick, tuple(owner.mover.position), tuple(owner.mover.velocity),
                             owner.body in fight.winston_jump.flights, fight.vitals[enemy.body].total,
                             tuple((actor.born_tick, actor.component.frame,
                                    fight.barriers.world.expiry_tick(actor)) for actor in actors)))
        destroyed = None
        if sphere is not None:
            expiry = fight.barriers.world.expiry_tick(sphere)
            # Let the real world/transport retire the actor, without owner input.
            step = 160
            while owner.match.tick <= expiry:
                step += 1
                now = 100.928 + step * .016
                session.tick(now)
                for ack in peers:
                    ack.drain()
                    ack.send(now=now)
                if sphere.entity not in fight.barriers.world.actors:
                    destroyed = owner.match.tick
                    break
            self.assertTrue(sphere.retired)
            self.assertTrue(all(sphere.entity not in viewer.seen for viewer in players))
        return rows, events, destroyed

    def test_sphere_clock_and_destruction_do_not_restart_at_packet_arrival(self):
        samples = [self.sample(batch, 16) for batch in self.contract["input"]["packet_frames"]]
        self.assertEqual(samples[0], samples[1])
        self.assertEqual(samples[0], samples[2])
        expected = self.contract["expected"]
        self.assertEqual(samples[0][0][0][-1][0][0], expected["sphere_born_world_tick"])
        self.assertEqual(samples[0][0][0][-1][0][2], expected["sphere_expiry_world_tick"])
        self.assertEqual(samples[0][2], expected["sphere_expiry_world_tick"])

    def test_jump_pose_landing_damage_and_retirement_are_packet_invariant(self):
        samples = [self.sample(batch, 8) for batch in self.contract["input"]["packet_frames"]]
        self.assertEqual(samples[0], samples[1])
        self.assertEqual(samples[0], samples[2])
        rows, events, _ = samples[0]
        expected = self.contract["expected"]
        self.assertEqual([event[:2] for event in events],
                         [(expected["jump_cast_command_frame"], False),
                          (expected["jump_landing_command_frame"], True)])
        self.assertFalse(rows[-1][3])
        self.assertAlmostEqual(rows[-1][4], expected["landing_enemy_health"])

    def test_event_mapping_uses_actual_unequal_body_and_world_epochs(self):
        script = SimpleNamespace(winston_birth=(1000, 7000), frame_ms=16)
        self.assertEqual(winston.world_tick_at_body(script, 7004), 1004)
        script.frame_ms = 8
        self.assertEqual(winston.world_tick_at_body(script, 7003), 1002)
