"""Original arithmetic oracle and real sole-mover deferred consumption."""
import json
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

import test_lucio_velocity as movement_fixture
import test_canonical_knockback_174 as combat_fixture
from ow174.game import physics_snapshot, velocity
from ow174.game.commands import Command
from ow174.game.mover import ADD, MULTIPLY, SET
from ow174.game.script import expr


class ProjectedVelocityTests(unittest.TestCase):
    def test_eight_original_native_arithmetic_results_and_squared_tiny_guard(self):
        oracle = json.loads((Path(__file__).resolve().parents[2] /
                             'fixtures/behavior/velocity-mode4-native-arithmetic-v1.json').read_text())
        for case in oracle['cases']:
            with self.subTest(case=case['case']):
                result = velocity.consume(case['current'], 6., [velocity.projected(4, case['requested'])], 4)
                for actual, expected in zip(result, case['resolved'], strict=True):
                    self.assertAlmostEqual(actual, expected, delta=2e-5)
        self.assertEqual(velocity.consume((1., 2., 3.), 6.,
                         [velocity.projected(4, (1e-20, 0., 0.))], 4), (1., 2., 3.))
        # A small but supported request is far below the unrelated scalar norm TINY.
        self.assertGreater(velocity.consume((0., 0., 0.), 6.,
                           [velocity.projected(4, (1e-8, 0., 0.))], 4)[0], 0.)

    def test_actual_mover_uses_changed_consumption_velocity_and_consumes_once(self):
        actor = movement_fixture.LucioVelocityTests().mover()
        actor.data = replace(actor.data, gravity_scale=0.)
        actor.velocity = (0., 0., 0.)
        row = replace(velocity.projected(2, (0., 0., 15.)), key=7)
        velocity.enqueue(actor.velocity_changes, row)
        captured = physics_snapshot.snapshot_mover(actor)
        actor.velocity_changes[0] = replace(row, mode=1)
        self.assertNotEqual(captured, physics_snapshot.snapshot_mover(actor))
        actor.velocity_changes[0] = row
        actor.velocity = (4., 0., 30.)  # changed after request; already faster along requested axis
        actor.step(Command(1), .016)
        self.assertEqual(len(actor.velocity_changes), 1)
        actor.step(Command(2), .016)
        self.assertEqual(actor.velocity, (4., 0., 30.))
        self.assertEqual(actor.velocity_changes, [])
        actor.step(Command(3), .016)
        self.assertEqual(actor.velocity, (4., 0., 30.))

    def test_numeric_reset_then_stored_vector_order_and_same_key_replacement(self):
        queue = []
        velocity.enqueue(queue, replace(velocity.vector(4, ADD, (3., 0., 0.), (0., 0., 0.)), key=1))
        velocity.enqueue(queue, replace(velocity.projected(4, (0., 1., 15.)), key=2))
        velocity.enqueue(queue, replace(velocity.numeric(4, 0, SET, 0.), key=2))
        result = velocity.consume((4., 2., 6.), 6., queue, 4)
        self.assertAlmostEqual(result[0], 3., delta=1e-5)
        self.assertAlmostEqual(result[1], 1., delta=1e-5)
        self.assertAlmostEqual(result[2], 15., delta=1e-5)
        velocity.enqueue(queue, replace(velocity.projected(4, (0., 2., 30.)), key=2))
        self.assertEqual(len(queue), 3)
        self.assertAlmostEqual(velocity.consume((0., 0., 0.), 6., queue, 4)[2], 30., delta=1e-5)

    def test_delayed_npc_admission_retains_mode_and_original_body_identity(self):
        fixture = combat_fixture.CanonicalKnockbackTests()
        run, source, victim, bot, identities = fixture.scene(npc=True)
        match = run.match
        fight = match.combat
        target = next(t for t in fight._targets() if t.owner is bot)
        impact = match.tick + 3
        self.assertTrue(fight.knockbacks.apply(target, expr.Vec3(0., 1., 15.),
                        impact_tick=impact, source_entity=source.body, velocity_mode=4, velocity_priority=1.,
                        launch_modifiers=((4, MULTIPLY, .01, .32, 0.), (7, MULTIPLY, 0., .32, 1.))))
        self.assertIsNone(bot.impulse_mover)
        consumed = []
        consume = velocity.consume
        def capture(current, jump, rows, frame):
            consumed.extend(row for row in rows if row.frame == frame and row.mode == 4)
            return consume(current, jump, rows, frame)
        with patch.object(velocity, 'consume', side_effect=capture):
            for _ in range(7):
                fixture.step(run, 0, 1000)
        self.assertEqual(len(consumed), 1)
        self.assertEqual(consumed[0].value, (0., 1., 15.))
        self.assertEqual(consumed[0].priority, 1.)
        self.assertEqual(fight.knockbacks.serial, 1)
        self.assertEqual(identities, tuple(b.entity for b in match.bots))
        self.assertIs(fight.knockbacks.active[bot.entity], bot.impulse_mover)
        braking = [m for m in bot.impulse_mover.mods.values() if m.index in (4, 7)]
        self.assertEqual({(m.index, m.start, m.end, m.priority) for m in braking},
                         {(4, impact+1, impact+21, 0.), (7, impact+1, impact+21, 1.)})

    def test_player_launch_and_finite_braking_share_shifted_recipient_epoch(self):
        fixture = combat_fixture.CanonicalKnockbackTests()
        run, source, victim, _target, _identities = fixture.scene(victim_birth=9000)
        fight = run.match.combat
        target = next(t for t in fight._targets() if t.owner is victim)
        self.assertTrue(fight.knockbacks.apply(target, expr.Vec3(0., 1., 15.),
                        impact_tick=run.match.tick, source_entity=source.body,
                        velocity_mode=4, velocity_priority=1.,
                        launch_modifiers=((4, MULTIPLY, .01, .32, 0.), (7, MULTIPLY, 0., .32, 1.))))
        row, = (r for r in victim.mover.velocity_changes if r.mode == 4)
        self.assertGreater(row.frame, run.match.tick + 1000)
        braking = [m for m in victim.mover.mods.values() if m.index in (4, 7)]
        self.assertEqual({(m.index, m.start, m.end, m.priority) for m in braking},
                         {(4, row.frame, row.frame+20, 0.), (7, row.frame, row.frame+20, 1.)})
        for mod in braking:
            self.assertFalse(mod.acts(row.frame-1))
            self.assertTrue(mod.acts(row.frame))
            self.assertFalse(mod.acts(row.frame+20))
        fixture.step(run, 0, 9000)
        self.assertGreater(victim.mover.velocity[2], 14.)
        self.assertGreater(victim.mover.data.decel_ground_base, 0.)
        self.assertAlmostEqual(victim.mover.values.decel_ground_base,
                               victim.mover.data.decel_ground_base * .01, delta=1e-6)
        self.assertEqual(fight.knockbacks.serial, 1)
        for _ in range(20):
            fixture.step(run, 0, 9000)
        self.assertEqual(victim.mover.values.decel_ground_base, victim.mover.data.decel_ground_base)
        self.assertFalse(any(m.index in (4, 7) for m in victim.mover.mods.values()))
