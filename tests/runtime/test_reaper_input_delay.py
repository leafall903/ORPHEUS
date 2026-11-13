"""Reaper owner commands retain fire and placement edges across packet delay."""

import json
import unittest
from pathlib import Path
from types import SimpleNamespace

from ow174.game import combat, messages, reaper
from ow174.game.bits import BitWriter
from ow174.game.commands import Command
from ow174.game.server import Client
from tests.runtime import test_game_reaper as reaper_tests
from tests.runtime.test_game_combat import aim


class ReaperInputDelayTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        path = Path(__file__).resolve().parents[2] / "fixtures/behavior/reaper-input-delay-v1.json"
        cls.fixture = json.loads(path.read_text(encoding="utf-8"))

    def test_delayed_selection_cancel_reopen_and_fire_recovery(self):
        run, owner, victim, target = reaper_tests.ReaperTests().scenario()
        match = run.match
        script = owner.body_script
        script.create_arrived = True
        shadow = next(instance for instance in script.component.instances.values()
                      if instance.graph.index == reaper.SHADOW_GRAPH)
        start = run.frame
        first_cast = None
        inputs, expected = self.fixture["input"], self.fixture["expected"]
        for step in range(1, inputs["simulation_steps"] + 1):
            tick = start + step
            match.update(tick * combat.TICK, tick)
            frame = tick - (inputs["initial_packet_delay_frames"] if step < 60
                            else inputs["packet_delay_from_step_60"])
            buttons = {100: 16, 120: 2, 140: 16, 150: 2, 170: 1}.get(step, 0)
            yaw, pitch = aim(match.combat._eye(owner), match.combat._eye(victim))
            owner.take_commands([Command(frame, yaw=yaw, pitch=5000 if buttons != 1 else pitch,
                                         buttons=buttons)])
            if step == 100:
                self.assertTrue(shadow.state(0).active)
                first_cast = shadow.state(0).counter
            if step in (120, 150):
                self.assertFalse(shadow.state(0).active)
                self.assertFalse(shadow.state(6).active)
                self.assertEqual(shadow.vars[61].value(), 0)
                self.assertFalse(reaper.intangible(owner))
            if step == 140:
                self.assertTrue(shadow.state(0).active)
                self.assertGreater(shadow.state(0).counter, first_cast)
        self.assertEqual(script.buttons, 0)
        self.assertEqual(script.frame, start + expected["final_owner_frame_relative_to_start"])
        self.assertLess(match.combat.vitals_of(target).total, 200)
        hits = owner.client.messages(combat.HIT)
        self.assertGreater(len(hits), 0)
        self.assertTrue(all(hit["+0x78"]["+0x0"]["+0x0"] == victim.body for hit in hits))
        self.assertAlmostEqual(match.combat.vitals_of(target).total,
                               200 - sum(hit["+0x78"]["+0x4"] for hit in hits))

    def test_delayed_fire_release_keeps_each_shell_and_damage_registration(self):
        run, owner, victim, target = reaper_tests.ReaperTests().scenario()
        match = run.match
        script = owner.body_script
        script.create_arrived = True
        health = match.combat.vitals_of(target)
        inputs, expected = self.fixture["input"], self.fixture["expected"]
        initial_health = inputs["separate_fire_target_health"]
        health.now[combat.HEALTH_POOL] = initial_health
        start = run.frame
        heading = aim(match.combat._eye(owner), match.combat._eye(victim))
        for step in range(1, inputs["simulation_steps"] + 1):
            tick = start + step
            match.update(tick * combat.TICK, tick)
            frame = tick - (inputs["initial_packet_delay_frames"] if step < 60
                            else inputs["packet_delay_from_step_60"])
            owner.take_commands([Command(frame, yaw=heading[0], pitch=heading[1],
                                         buttons=1 if step in inputs["separate_fire_steps"] else 0)])
        hits = owner.client.messages(combat.HIT)
        self.assertEqual(len(hits), expected["separate_registered_pellet_hits"])
        self.assertEqual(len({hit["+0x84"] for hit in hits}), expected["separate_shells"])
        damage = sum(hit["+0x78"]["+0x4"] for hit in hits)
        self.assertAlmostEqual(health.total, initial_health - damage)
        self.assertEqual(script.buttons, 0)
        self.assertEqual(owner.mover.history[-1].frame, owner.commands.last_frame)

    def deliver_packet(self, run, owner, *, count, delay, buttons, heading=(0, 5000)):
        first = owner.body_script.frame + 1
        packet_end = first + count - 1
        until = max(run.frame, packet_end) + delay
        while run.frame < until:
            run.frame += 1
            run.match.update(run.frame * combat.TICK, run.frame)
        body = BitWriter()
        body.bits(delay * 16, 16)
        body.bits(0, 8)
        body.bits(count, 8)
        body.bits(first, 32)
        body.bit(0)  # no throttles
        body.signed(heading[0], 16)
        body.signed(heading[1], 16)
        body.bits(buttons, 24)
        body.bits(0, 8)  # no action
        body.bits(0, 16)  # view delay
        for index in range(1, count):
            body.bit(0)  # consecutive frame
            body.bit(index == 1)  # release the first command's buttons
            if index == 1:
                for _ in range(4):
                    body.bit(0)  # unchanged throttles, pitch, yaw, view delay
                body.bit(1)
                body.bits(0, 24)
                body.bit(0)  # unchanged action
        payload = BitWriter()
        messages.write_channel(payload)
        payload.bit(1)
        payload.bits(body.count, 32)
        payload.append(body)

        def unexpected_log(message, *args):
            self.fail(message)

        peer = SimpleNamespace(player=owner, log=unexpected_log)
        Client._payload_in(peer, payload.getvalue(), run.frame * combat.TICK)
        self.assertEqual(owner.commands.last_frame, packet_end)
        self.assertEqual(owner.body_script.frame, packet_end)
        return first

    def test_wire_batches_keep_first_fire_shift_and_shadow_edges(self):
        for count in self.fixture["input"]["wire_packet_counts"]:
            for delay in self.fixture["input"]["wire_delivery_delays"]:
                for action in ("fire", "wraith", "shadow"):
                    with self.subTest(count=count, delay=delay, action=action):
                        run, owner, victim, target = reaper_tests.ReaperTests().scenario()
                        script = owner.body_script
                        script.create_arrived = True
                        first = self.deliver_packet(
                            run, owner, count=count, delay=delay,
                            buttons={"fire": 1, "wraith": 8, "shadow": 16}[action],
                            heading=aim(run.match.combat._eye(owner), run.match.combat._eye(victim))
                            if action == "fire" else (0, 5000),
                        )
                        if action == "fire":
                            hits = owner.client.messages(combat.HIT)
                            self.assertGreater(len(hits), 0)
                            self.assertEqual({hit["+0x84"] for hit in hits}, {first})
                            loss = sum(hit["+0x78"]["+0x4"] for hit in hits)
                            self.assertAlmostEqual(run.match.combat.vitals_of(target).total, 200 - loss)
                        elif action == "wraith":
                            self.assertTrue(reaper.intangible(owner))
                            self.deliver_packet(run, owner, count=32, delay=delay, buttons=0)
                            self.assertTrue(reaper.intangible(owner))
                            self.deliver_packet(run, owner, count=count, delay=delay, buttons=1)
                            self.assertFalse(reaper.intangible(owner))
                            self.assertEqual(run.match.combat.vitals_of(target).total, 200)
                            self.deliver_packet(run, owner, count=26, delay=delay, buttons=0)
                            wraith = next(i for i in script.component.instances.values()
                                          if i.graph.index == reaper.WRAITH_GRAPH)
                            self.assertGreater(wraith.vars[61].value(), 7)
                        else:
                            shadow = next(i for i in script.component.instances.values()
                                          if i.graph.index == reaper.SHADOW_GRAPH)
                            self.assertTrue(shadow.state(0).active)
                            self.deliver_packet(run, owner, count=count, delay=delay, buttons=2)
                            self.assertFalse(shadow.state(0).active)
                            self.assertFalse(shadow.state(6).active)
                            self.assertEqual(shadow.vars[61].value(), 0)
                            self.deliver_packet(run, owner, count=count, delay=delay, buttons=0)
                            self.deliver_packet(run, owner, count=count, delay=delay, buttons=16)
                            self.assertTrue(shadow.state(0).active)
                            self.deliver_packet(run, owner, count=count, delay=delay, buttons=1)
                            self.assertTrue(shadow.state(32).active)
