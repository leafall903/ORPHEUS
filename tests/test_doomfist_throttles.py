"""Actual command movement scopes; native direction-prefix parity is audited separately."""

import unittest

from test_doomfist_combat import DoomfistFixture
from test_doomfist_meteor import charge_by_neutral_commands

from ow174.game.commands import Command
from ow174.game.mover import MOVEMENT_MOD


class DoomfistThrottleTests(DoomfistFixture, unittest.TestCase):
    def pose(self, *, buttons=0, forward=0, right=0, pitch=0):
        # Same match/command path as Run.step, extended with raw throttle bytes.
        self.run.frame += 1
        frame = self.run.frame
        self.match.update(frame * .016, frame)
        for player in self.run.players:
            player.client.queue_entities(self.match.remote_movements(player))
            player.take_commands([Command(frame, forward=forward if player is self.a else 0,
                                          right=right if player is self.a else 0,
                                          pitch=pitch if player is self.a else 0,
                                          buttons=buttons if player is self.a else 0)])

    def instance(self, index):
        return next(i for i in self.a.body_script.component.instances.values() if i.graph.index == index)

    def locks(self, index):
        return {s.index for s in self.instance(index).states.values()
                if s.active and s.node.cls == MOVEMENT_MOD
                and any(m.get('m_A9561CA0') in (17, 18, 19)
                        for m in s.node.fields.get('m_BC5E91CF') or [])}

    def test_actual_uppercut_zero_axes_preserve_raw_bytes_then_restore_on_next_pose(self):
        for forward in (127, -127):
            with self.subTest(forward=forward):
                self.prepare(30)
                start = tuple(self.a.mover.position)
                self.pose(buttons=8)
                phase = self.instance(0xDD8).state(4)
                self.assertTrue(phase.active)
                observed = 0
                while phase.active:
                    self.pose(forward=forward, right=127)
                    observed += 1
                    self.assertEqual(self.a.mover.velocity[0::2], (0, 0))
                    self.assertEqual(tuple(self.a.mover.position[0::2]), start[0::2])
                    self.assertEqual(self.a.mover.history[-1].throttles, (127, forward))
                    self.assertLess(observed, 240)
                self.assertGreater(observed, 0)
                self.pose(forward=forward, right=127)
                self.assertNotEqual(self.a.mover.velocity[0::2], (0, 0))

    def test_actual_ground_and_aerial_slam_block_lateral_wish_without_erasing_motion(self):
        for aerial, phase in ((False, 3), (True, 12)):
            with self.subTest(aerial=aerial):
                self.prepare(30)
                x, y, z = self.a.mover.position
                if aerial:
                    self.place_player(self.a, (x, y + 6, z))
                pitch = 8192 if aerial else 0
                self.pose(buttons=16, pitch=pitch)
                self.assertIn(phase, self.locks(0xB27))
                observed, count = set(), 0
                while active := self.locks(0xB27):
                    observed |= active
                    self.pose(buttons=16 if aerial and count < 10 else 0,
                              pitch=pitch, forward=-127, right=127)
                    self.assertEqual(self.a.mover.position[0], x)
                    self.assertEqual(self.a.mover.history[-1].throttles, (127, -127))
                    count += 1
                    self.assertLess(count, 240)
                self.assertIn(43, observed)  # the separate impact/recovery scope
                self.assertGreater(self.a.mover.position[2], z + 1)
                self.pose(right=127)
                self.assertLess(self.a.mover.velocity[0], 0)

    def test_actual_punch_forced_forward_scope_rejects_opposite_axes_then_restores(self):
        self.prepare(30)
        x = self.a.mover.position[0]
        for _ in range(40):
            self.pose(buttons=2)
        self.pose()
        self.assertIn(42, self.locks(0xD06))
        count = 0
        while self.locks(0xD06):
            self.pose(forward=-127, right=127)
            self.assertEqual(self.a.mover.position[0], x)
            self.assertEqual(self.a.mover.history[-1].throttles, (127, -127))
            count += 1
            self.assertLess(count, 100)
        self.assertGreater(count, 0)
        self.pose(right=127)
        self.assertLess(self.a.mover.velocity[0], 0)

    def test_actual_meteor_intro_locks_axes_and_aim_phase_restores_selection_movement(self):
        self.prepare(30)
        charge_by_neutral_commands(self)
        start = tuple(self.a.mover.position)
        self.pose(buttons=32)
        phase = self.instance(0xC51).state(5)
        self.assertTrue(phase.active)
        count = 0
        while phase.active:
            self.pose(forward=127, right=127)
            self.assertEqual(tuple(self.a.mover.position), start)
            self.assertEqual(self.a.mover.history[-1].throttles, (127, 127))
            count += 1
            self.assertLess(count, 100)
        self.assertTrue(self.instance(0xC51).state(18).active)
        self.pose(forward=127, right=127)
        self.assertLess(self.a.mover.position[0], start[0])
        self.assertGreater(self.a.mover.position[2], start[2])
        self.assertNotEqual(self.fight.meteors.casts[self.a.body].target, start)

    def test_actual_invalid_aerial_target_does_not_install_a_throttle_lock(self):
        self.prepare(30)
        x, y, z = self.a.mover.position
        self.place_player(self.a, (x, y + 6, z))
        self.pose(buttons=16, pitch=-8192)
        for _ in range(8):
            self.assertFalse(self.locks(0xB27))
            self.pose(buttons=16, pitch=-8192, right=127)
        self.assertLess(self.a.mover.position[0], x)
        self.pose(pitch=-8192)
        self.assertFalse(self.locks(0xB27))

    def test_actual_enemy_flashbang_clears_slam_lock_and_normal_input_returns_after_stun(self):
        # Reuse the actual enemy-cast fixture without importing extra TestCases
        # into this module's loader or assigning a status deadline.
        import test_doomfist_interrupts as interrupts

        fixture = interrupts.FlashbangInteractionTests()
        fixture.setUp()
        fixture.step(16)
        fixture.close_caster()
        fixture.step(flash=True)
        fixture.wait_for_flash()
        self.assertTrue(fixture.fight.is_stunned(fixture.a))
        for _ in range(150):
            fixture.step(forward=127)
        self.assertFalse(fixture.fight.is_stunned(fixture.a))
        self.assertFalse(any(s.active and s.node.cls == MOVEMENT_MOD
                             and any(m.get('m_A9561CA0') in (17, 18, 19)
                                     for m in s.node.fields.get('m_BC5E91CF') or [])
                             for i in fixture.a.body_script.component.instances.values()
                             for s in i.states.values()))
        self.assertGreater(fixture.a.mover.velocity[2], 0)
