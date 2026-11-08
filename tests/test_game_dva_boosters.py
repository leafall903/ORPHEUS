"""Throttle/typed mover integration and actual horizontal Boosters; full flight OPEN."""

import unittest
from dataclasses import replace

import test_game_dva_mech as mech_tests
import test_server_snapshot as snapshot_tests
from test_game_mover import FLOOR
from test_game_mover import wall as collision_wall

from ow174.game import mover
from ow174.game.collision import World
from ow174.game.commands import Command
from ow174.game.script.nodes import Ability
from ow174.game.server_snapshot import snapshot_digest


class ThrottleTests(unittest.TestCase):
    def test_forced_forward_cancels_reverse_and_preserves_strafe_sign(self):
        mods = [mover.Mod(17, mover.SET, 1, 0), mover.Mod(18, mover.SET, 0, 0),
                mover.Mod(19, mover.SET, 0.75, 0)]
        self.assertEqual(mover.throttle_mods(mods, -0.5, -1), (-0.75, 1))
        self.assertEqual(mover.throttle_mods(mods, 0, 0), (0.75, 1))
        self.assertEqual(mover.throttle_mods([], -0.25, -0.75), (-0.25, -0.75))

    def test_clamp_is_after_forward_subtraction_and_strafe_sign(self):
        mods = [mover.Mod(17, mover.SET, 2, 0), mover.Mod(18, mover.SET, 1.5, 0),
                mover.Mod(19, mover.MULTIPLY, 4, 0)]
        self.assertEqual(mover.throttle_mods(mods, -0.75, 0), (-1, 0.5))
        self.assertEqual(mover.throttle_mods([mover.Mod(18, mover.ADD, 2, 0)], 0, 1), (0, -1))

    def test_typed_override_scalar_order_expiration_and_base_restoration(self):
        body = mover.Mover((0, 1, 0), 0, data=mover.MoverData())
        slow = replace(body.data, run_forward=3, accel_air=20)
        fast = replace(body.data, run_forward=7, accel_air=35)
        a, b, c = (1, 0, 0), (1, 0, 1), (1, 0, 2)
        body.track_mods({a: (-1, mover.SET, slow, 0), b: (-1, mover.SET, fast, 2),
                         c: (mover.SPEED, mover.MULTIPLY, 2, 0)}, 10)
        def active(frame):
            return sorted((m for m in body.mods.values() if m.acts(frame)), key=mover.Mod.order)
        self.assertIs(mover.modded(body.data, active(10)), body.data)
        self.assertEqual(mover.modded(body.data, active(11)).run_forward, 14)
        self.assertEqual(mover.modded(body.data, active(11)).accel_air, 35)
        body.track_mods({}, 12)
        self.assertEqual(mover.modded(body.data, active(12)).run_forward, 14)
        self.assertIs(mover.modded(body.data, active(13)), body.data)

    def test_new_throttle_mod_affects_next_pose_without_fabricating_raw_input(self):
        body = mover.Mover((0, 1, 0), 0)
        body.track_mods({(1, 0, 0): (17, mover.SET, 1, 0)}, 10)
        body.step(Command(10), 0.016, 10)
        before = body.position[2]
        body.step(Command(11), 0.016, 11)
        self.assertGreater(body.position[2], before)
        self.assertEqual(body.throttles, (0, 0))
        self.assertIsNone(body.input_frame)

    def test_typed_override_is_causal_passive_storage(self):
        case = snapshot_tests.ServerSnapshotTests()
        session, _, players = case.scene()
        body = players[0].mover
        key = (1, 2, 3)
        body.track_mods({key: (-1, mover.SET, replace(body.data, accel_air=35), 0)}, 100)
        first = snapshot_digest(case.capture(session))
        body.mods[key].value = replace(body.mods[key].value, accel_air=36)
        self.assertNotEqual(snapshot_digest(case.capture(session)), first)


class AuthoredBoostersTests(unittest.TestCase):
    scenario = mech_tests.AuthoredMechTests.scenario
    step = mech_tests.AuthoredMechTests.step

    @classmethod
    def setUpClass(cls):
        mech_tests.AuthoredMechTests.setUpClass()

    def booster(self, mech):
        return next(i for i in mech.body_script.component.instances.values() if i.graph.index == 0x047E)

    def test_forced_horizontal_thrust_uses_authored_speed_and_typed_air_acceleration(self):
        run, mech, _, _ = self.scenario()
        self.step(run, mech, 1, 8)
        self.assertEqual(mech.mover.position, [0, 1, 0])
        self.step(run, mech, 29, 8)
        self.assertAlmostEqual(mech.mover.position[2], 5.4443, delta=0.01)
        self.assertAlmostEqual(mech.mover.velocity[2], 11.99, delta=0.001)
        self.assertEqual(mech.mover.values.accel_air, 35)
        self.assertEqual(mech.mover.data.accel_air, 3.5)
        self.assertEqual(mech.mover.throttles, (0, 0))

    def test_second_press_cancels_then_cooldown_rejects_repress_and_allows_reuse(self):
        run, mech, _, _ = self.scenario()
        script = self.booster(mech)
        self.step(run, mech, 40, 8)
        self.step(run, mech, 1)
        self.assertTrue(script.state(2).active)
        self.step(run, mech, 1, 8)
        self.assertFalse(script.state(2).active)
        self.step(run, mech, 40)
        self.assertEqual(mech.mover.velocity, (0, 0, 0))
        self.assertGreater(script.vars[61].value(), 3)
        self.step(run, mech, 1, 8)
        self.assertFalse(script.state(2).active)
        self.assertEqual(mech.mover.values.accel_air, 3.5)
        while script.vars[61].value() > 0:
            self.step(run, mech, 1)
        self.step(run, mech, 2, 8)
        self.assertTrue(script.state(2).active)
        self.assertGreater(mech.mover.velocity[2], 0)

    def test_duration_ends_propulsion_and_normal_movement_values_return(self):
        run, mech, _, _ = self.scenario()
        script = self.booster(mech)
        self.step(run, mech, 100, 8)
        self.assertTrue(script.state(2).active)
        self.assertTrue(script.state(0).flags & Ability.RUNNING)
        self.step(run, mech, 70, 8)
        self.assertFalse(script.state(2).active)
        self.assertEqual(mech.mover.values.run_forward, 5.5)
        self.assertEqual(mech.mover.velocity, (0, 0, 0))
        self.assertGreater(script.vars[61].value(), 0)

    def test_yaw_steers_horizontal_thrust_and_static_wall_stops_capsule(self):
        for wall in (False, True):
            run, mech, _, _ = self.scenario()
            if wall:
                run.match.collision_world = World(FLOOR + collision_wall(3, -10, 3, 10))
                mech.mover.use(world=run.match.collision_world)
            self.step(run, mech, 50, 8, yaw=16384)
            if wall:
                self.assertLess(mech.mover.position[0], 3 - mech.mover.values.stand_radius)
            else:
                self.assertGreater(mech.mover.position[0], 8)
                self.assertAlmostEqual(mech.mover.position[2], 0, delta=0.01)

    def test_typed_component_reads_authored_fields_and_rejects_missing_or_nonfinite(self):
        _run, mech, _, _ = self.scenario()
        state = self.booster(mech).state(2)
        raw = state.node.field("m_BC5E91CF")[0]["m_9B7A63EA"]
        data = mover.component_data(raw)
        self.assertEqual((data.stand_radius, data.accel_air), (mover.f32(0.7), 35))
        for field, value in (("m_E631BA84", None), ("m_CFD3E635", float("nan")),
                             ("m_5C6B4602", 1.5)):
            with self.assertRaises(ValueError):
                mover.component_data({**raw, field: value})


if __name__ == "__main__":
    unittest.main()
