"""Actual Hand Cannon commands and authored ammo clocks; no resource writes."""

import unittest
from itertools import pairwise

from test_doomfist_combat import DoomfistFixture

from ow174.game import combat
from ow174.game.commands import FIRE, Command


class HandCannonResourceTests(DoomfistFixture, unittest.TestCase):
    def setUp(self):
        self.prepare()
        self.weapon = next(i for i in self.a.body_script.component.instances.values()
                           if i.graph.index == 0xD0A)

    def step(self, buttons=0):
        # Face away from the victim: resource tests cannot kill/reset it.
        self.run.step({self.a: (-32768, 0, buttons)})

    def ammo(self):
        return self.weapon.vars[53].value()

    def volleys(self):
        return self.weapon.state(2).counter

    def empty(self):
        for _ in range(100):
            self.step(FIRE)
            if self.ammo() == 0:
                return
        self.fail('real held fire did not empty the four-round magazine')

    def test_held_fire_exhausts_four_rounds_rejects_empty_then_fires_replenished_round(self):
        self.assertEqual(self.ammo(), 4)
        shots = []
        for tick in range(140):
            before = self.volleys()
            self.step(FIRE)
            if self.volleys() != before:
                shots.append(tick)
            self.assertGreaterEqual(self.ammo(), 0)
            self.assertLessEqual(self.ammo(), 4)
        self.assertEqual(len(shots), 5)
        # Authored3Hz, quantized at16ms. The initial ready clock can shorten
        # the first interval by one frame; subsequent empty time is longer.
        for first, second in pairwise(shots[:4]):
            self.assertLessEqual(abs((second - first) * 16 - 1000 / 3), 16)
        self.assertGreaterEqual((shots[4] - shots[3]) * 16, 960)
        self.assertEqual(self.vitals().total, 200)
        self.assertFalse(self.a.client.messages(combat.HIT))

    def test_release_replenishes_one_round_per_authored_wait_and_caps_at_four(self):
        self.empty()
        count = self.volleys()
        events = []
        wait = self.weapon.state(19)
        for _ in range(280):
            previous = self.ammo()
            start, active = wait.start, wait.active
            self.step()
            if self.ammo() != previous:
                self.assertTrue(active)
                self.assertEqual(self.ammo(), previous + 1)
                duration = self.weapon.evaluate(wait.node.field('m_timeout')) * 1000
                elapsed = self.weapon.component.now - start
                self.assertGreaterEqual(elapsed, duration)
                self.assertLess(elapsed, duration + self.weapon.component.frame_ms)
                events.append(self.ammo())
        self.assertEqual(events, [1, 2, 3, 4])
        self.assertEqual(self.ammo(), 4)
        self.assertEqual(self.volleys(), count)

    def test_empty_rejected_pulse_expires_before_refill_without_delayed_shot(self):
        self.empty()
        count = self.volleys()
        self.step()
        self.step(FIRE)
        self.assertEqual(self.ammo(), 0)
        self.assertEqual(self.volleys(), count)
        for _ in range(280):
            self.step()
        self.assertEqual(self.ammo(), 4)
        self.assertEqual(self.volleys(), count)
        self.step(FIRE)
        self.assertEqual(self.ammo(), 3)
        self.assertEqual(self.volleys(), count + 1)

    def test_manual_reload_cannot_bypass_replenishment_wait(self):
        self.empty()
        self.step()
        before = self.ammo()
        count = self.volleys()
        for tick in range(35):
            self.step(0x400 if tick % 2 == 0 else 0)
            self.assertEqual(self.ammo(), before)
        self.assertEqual(self.volleys(), count)
        for _ in range(250):
            self.step()
        self.assertEqual(self.ammo(), 4)

    def test_duplicate_and_old_commands_cannot_repeat_volley_or_ammo_cost(self):
        self.fire()
        before = (self.ammo(), self.volleys(), len(self.fight.rockets))
        self.a.take_commands([
            Command(self.run.frame, buttons=FIRE),
            Command(self.run.frame - 1, buttons=FIRE),
            Command(self.run.frame, buttons=FIRE),
        ])
        self.assertEqual((self.ammo(), self.volleys(), len(self.fight.rockets)), before)
        self.settle()
        self.assertEqual(self.vitals().total, 134)
        self.assertEqual(len(self.a.client.messages(combat.HIT)), 11)


if __name__ == '__main__':
    unittest.main()
