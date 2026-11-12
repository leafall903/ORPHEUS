"""Real Fan rays, deterministic event identity and generated native tables."""

import hashlib
import math
import struct
import unittest
from unittest.mock import patch

import test_mccree_combat as scenes
from test_game_combat import aim

from ow174.game import combat, mccree, weapon_spread
from ow174.game.commands import Command


class McCreeSpreadTests(unittest.TestCase):
    def test_generated_tables_match_complete_native_capture_fingerprints(self):
        self.assertEqual(hashlib.sha256(struct.pack('<16384I', *weapon_spread.uniform_table())).hexdigest(),
                         '4f99ff210621528fff6737582c39f0ed5464cdf1c9980f03d503dcf311b356f6')
        self.assertEqual(hashlib.sha256(struct.pack('<16384f', *weapon_spread.float_table())).hexdigest(),
                         'b3d0a3ea584bfc99c11bc39a490e7a2765c9ef74e55db6d8aafb1803c6a25281')

    def test_same_event_and_cold_cache_produce_identical_vectors(self):
        def vectors():
            return [weapon_spread.direction((0, 0, 1), math.radians(6), 77, i,
                                             gaussian=bool(i % 2)) for i in range(32)]
        expected = vectors()
        weapon_spread.uniform_table.cache_clear()
        weapon_spread.float_table.cache_clear()
        self.assertEqual(expected, vectors())
        self.assertGreater(len(set(expected)), 24)

    def test_uniform_samples_remain_inside_authored_six_degree_cone(self):
        for forward in ((0, 0, 1), (1, 0, 0), (0, 1, 0), (0, -1, 0)):
            for count in range(128):
                vector = weapon_spread.direction(forward, math.radians(6), count, 0)
                self.assertAlmostEqual(sum(v*v for v in vector), 1, delta=5e-7)
                self.assertGreaterEqual(sum(a*b for a,b in zip(forward, vector, strict=True)),
                                        math.cos(math.radians(6)) - 1e-7)

    def test_ninety_degree_camera_keeps_native_tiny_horizontal_basis(self):
        # Captured-code D80090/884090 result, scalar libm stubs only. The
        # previous1e-7 length cutoff flipped both horizontal components.
        vector = weapon_spread.direction((0, 1, math.cos(math.pi/2)),
                                         math.radians(6), 284, 46, 3)
        expected = (-0.023236999288201332, 0.9946451783180237, -0.10070230811834335)
        for actual, native in zip(vector, expected, strict=True):
            self.assertAlmostEqual(actual, native, delta=2e-6)

    def fire(self, *, npc=False, distance=2, wall=False, ally=False):
        run, shooter, target = scenes.McCreeMeleeTests().arena(
            npc=npc, distance=distance, wall=wall, ally=ally)
        for other in run.players:
            if other is not shooter and other is not target:
                other.mover.position[0] += 20
        fight = run.match.combat
        body = next(t for t in fight._targets() if t.owner is target)
        health = fight.vitals_of(body)
        before = health.total
        samples = []
        original = mccree.fan_direction

        def observe(player, state, forward, number, time, activation):
            vector = original(player, state, forward, number, time, activation)
            if player is shooter and state.node.pos == 37 and state.instance.graph.index == 0x1d2:
                samples.append((vector, forward, activation))
            return vector

        yaw, pitch = aim(fight._eye(shooter), (body.feet[0], body.feet[1] + 1, body.feet[2]))
        with patch.object(mccree, 'fan_direction', side_effect=observe):
            for _ in range(100):
                run.step({shooter: (yaw, pitch, 2)})
                # Network retransmission of this same input must not generate another shot.
                shooter.take_commands([Command(run.frame, yaw=yaw, pitch=pitch, buttons=2)])
                if len(samples) == 6:
                    break
        weapon = next(i for i in shooter.body_script.component.instances.values() if i.graph.index == 0x1d2)
        self.assertEqual(len(samples), 6)
        self.assertEqual(weapon.vars[53].value(), 0)
        self.assertEqual(len({activation for _, _, activation in samples}), 6)
        self.assertEqual(len({vector for vector, _, _ in samples}), 6)
        for vector, forward, _ in samples:
            self.assertNotEqual(vector, forward)
            self.assertGreater(sum(a*b for a,b in zip(vector, forward, strict=True)),
                               math.cos(math.radians(6.001)))
        self.assertFalse(any(hit['+0x78']['+0x8'] & combat.CRITICAL
                             for hit in shooter.client.messages(combat.HIT)))
        return before - health.total, samples

    def test_close_enemy_player_and_npc_take_damage_with_real_spread(self):
        for npc in (False, True):
            with self.subTest(npc=npc):
                damage, _ = self.fire(npc=npc)
                self.assertGreater(damage, 0)

    def test_walls_and_allies_block_real_fan_damage(self):
        for npc in (False, True):
            for options in ({'wall': True}, {'ally': True}):
                with self.subTest(npc=npc, options=options):
                    damage, _ = self.fire(npc=npc, **options)
                    self.assertEqual(damage, 0)

    def test_far_crosshair_target_is_not_guaranteed_six_hits(self):
        for npc in (False, True):
            with self.subTest(npc=npc):
                damage, _ = self.fire(npc=npc, distance=80)
                self.assertLess(damage, 90)

    def test_activation_seed_survives_later_state_counter_changes(self):
        run, shooter, _ = scenes.McCreeMeleeTests().arena(distance=2)
        run.step({shooter: (0, 0, 2)})
        weapon = next(i for i in shooter.body_script.component.instances.values() if i.graph.index == 0x1d2)
        state = next(s for s in weapon.states.values() if s.node.pos == 37)
        activation = state.counter, state.start
        first = mccree.fan_direction(shooter, state, (0, 0, 1), 0, state.start, activation)
        state.counter += 20
        later = mccree.fan_direction(shooter, state, (0, 0, 1), 0, state.start, activation)
        self.assertEqual(first, later)
        self.assertNotEqual(first, mccree.fan_direction(
            shooter, state, (0, 0, 1), 0, state.start, (activation[0] + 1, activation[1])))

    def test_primary_scope_keeps_the_command_direction(self):
        run, shooter, _ = scenes.McCreeMeleeTests().arena(distance=2)
        run.step({shooter: (0, 0, 1)})
        weapon = next(i for i in shooter.body_script.component.instances.values() if i.graph.index == 0x1d2)
        state = next(s for s in weapon.states.values() if s.node.pos == 31)
        forward = 0.0, 0.0, 1.0
        self.assertEqual(mccree.fan_direction(shooter, state, forward, 0, state.start,
                                             (state.counter, state.start)), forward)
