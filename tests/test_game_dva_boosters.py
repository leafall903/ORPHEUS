"""Actual Boosters role16 motion and reconstructed contact damage."""

import unittest
from dataclasses import replace
from unittest.mock import patch

import test_game_dva_mech as mech_tests
import test_server_snapshot as snapshot_tests
from test_game_mover import FLOOR
from test_game_mover import box as collision_box
from test_game_mover import wall as collision_wall

from ow174.game import body_entities, bots, mover, sigma_grasp
from ow174.game.collision import MOVER, World
from ow174.game.commands import Command
from ow174.game.content import PRACTICE_RANGE, find_hero
from ow174.game.script.driver import ULT_CHARGE
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

    def test_flight_parameter_and_retirement_are_causal_in_existing_mod_storage(self):
        case = snapshot_tests.ServerSnapshotTests()
        session, _, players = case.scene()
        body = players[0].mover
        key = (1, 36, 123)
        body.track_mods({key: (mover.FLIGHT_ROLE, mover.SET, 0.0, 1.0)}, 100)
        first = snapshot_digest(case.capture(session))
        body.mods[key].value = .5
        second = snapshot_digest(case.capture(session))
        self.assertNotEqual(second, first)
        body.track_mods({}, 101)
        self.assertEqual(body.mods[key].end, 102)
        self.assertNotEqual(snapshot_digest(case.capture(session)), second)

    def test_contact_target_history_is_causal_storage(self):
        case = snapshot_tests.ServerSnapshotTests()
        session, _, players = case.scene()
        contacts = set()
        player = players[0]
        player.match.combat.booster_contacts[player.body] = (7, 1, contacts)
        before = snapshot_digest(case.capture(session))
        contacts.add(0xC000100F)
        self.assertNotEqual(snapshot_digest(case.capture(session)), before)

    def test_active_npc_impulse_is_causal_storage(self):
        case = snapshot_tests.ServerSnapshotTests()
        session, _, players = case.scene()
        match = players[0].match
        bot = bots.make(PRACTICE_RANGE.map_guid)[0]
        match.bots.append(bot)
        bot.apply_impulse((0.0, 4.0, 17.0), match.collision_world, MOVER, match.tick)
        before = snapshot_digest(case.capture(session))
        bot.impulse_mover.velocity = (0.0, 3.0, 16.0)
        self.assertNotEqual(snapshot_digest(case.capture(session)), before)

    def test_recovered_command_gap_matches_explicit_poses_across_flight_scope_end(self):
        data = replace(mover.MoverData(), accel_air=35)
        def actor():
            result = mover.Mover((0, 10, 0), data=data)
            result.mods = {
                (1, 0, 0): mover.Mod(mover.FLIGHT_ROLE, mover.SET, 0.0, 11, 14),
                (1, 1, 0): mover.Mod(17, mover.SET, 1.0, 11, 14),
            }
            return result
        explicit, recovered = actor(), actor()
        for frame in range(10, 16):
            explicit.step(Command(frame, pitch=-8192), .016, 20)
        recovered.step(Command(10, pitch=-8192), .016, 20)
        recovered.step(Command(15, pitch=-8192), .016, 20)
        self.assertEqual(tuple(recovered.history), tuple(explicit.history))
        self.assertGreater(explicit.position[1], 10)


class AuthoredBoostersTests(unittest.TestCase):
    scenario = mech_tests.AuthoredMechTests.scenario
    step = mech_tests.AuthoredMechTests.step

    @classmethod
    def setUpClass(cls):
        mech_tests.AuthoredMechTests.setUpClass()

    def booster(self, mech):
        return next(i for i in mech.body_script.component.instances.values() if i.graph.index == 0x047E)

    def input_step(self, run, mech, *, buttons=0, forward=0, right=0, yaw=0, pitch=0):
        run.frame += 1
        run.match.update(run.frame*0.016, run.frame)
        mech.take_commands([Command(run.frame, buttons=buttons, forward=forward, right=right,
                                    yaw=yaw, pitch=pitch)])

    def test_controlled_contact_bypasses_active_deflect_and_grasp(self):
        from test_game_dva_created_body import CreatedBodyTests

        self.enterContext(patch.object(body_entities, "ULT_SECONDS", 0))
        defenses = (("genji", 0x3A3, 10, 16), ("sigma", sigma_grasp.GRASP, 1, 8))
        for hero, graph_id, state_id, button in defenses:
            with self.subTest(hero=hero):
                run, pilot, defender, _, mech = CreatedBodyTests().scenario()
                fight = run.match.combat
                self.assertTrue(run.match.switch_hero(defender, find_hero(hero)))
                x, y, z = mech.position
                defender.mover.position[:] = (x, y, z + 2)
                defender.mover.body = None
                for _ in range(100):
                    run.step({})
                defense = next(i for i in defender.body_script.component.instances.values()
                               if i.graph.index == graph_id)
                run.step({defender: (-32768, 0, button)})
                for _ in range(30):
                    if defense.state(state_id).active:
                        break
                    run.step({defender: (-32768, 0, 0)})
                self.assertTrue(defense.state(state_id).active)
                health = fight.vitals_of(fight.player_target(defender))
                before = health.total
                charge = mech.body_script.component.entity_var(ULT_CHARGE)
                initial_charge = charge.value() or 0
                for _ in range(30):
                    run.step({pilot: (0, 0, 8), defender: (-32768, 0, 0)})
                    if health.total < before:
                        break
                self.assertTrue(defense.state(state_id).active)
                self.assertEqual(before - health.total, 10)
                self.assertGreater(defender.mover.velocity[2], 0)
                self.assertEqual(charge.value() - initial_charge, 10)
                self.assertFalse(defender.client.messages(25001))
                for _ in range(145):
                    run.step({})
                self.assertFalse(health.temporary_shields)
                self.assertNotIn(defender.body, fight.grasp_shields)

    def test_contact_damage_waits_for_overlap_and_hits_once_per_activation(self):
        run, mech, victim, vitals = self.scenario(armour=True)
        victim.mover.position[2] = 4.5
        victim.mover.body = None
        self.step(run, mech, 1, 8)
        self.assertEqual(vitals.total, 10000)
        self.step(run, mech, 20, 8)
        self.assertEqual(vitals.total, 9995)
        self.assertEqual(len(mech.client.messages(25001)), 1)
        self.step(run, mech, 400)
        mech.mover.position = [0.0, 1.0, 0.0]
        mech.mover.body = None
        victim.mover.position = [0.0, 1.0, 4.5]
        victim.mover.velocity = (0.0, 0.0, 0.0)
        victim.mover.body = None
        self.step(run, mech, 21, 8)
        self.assertEqual(vitals.total, 9990)

    def test_contact_excludes_wall_allies_and_lateral_miss(self):
        for case in ("wall", "ally", "miss"):
            with self.subTest(case=case):
                run, mech, victim, vitals = self.scenario()
                victim.mover.position[2] = 4
                if case == "wall":
                    run.match.collision_world = World(FLOOR + collision_wall(-10, 3, 10, 3))
                elif case == "ally":
                    run.match.game_map = replace(run.match.game_map, free_for_all=False)
                    victim.team = mech.team
                else:
                    victim.mover.position[0] = 3
                victim.mover.body = None
                self.step(run, mech, 35, 8)
                self.assertEqual(vitals.total, 5000)

    def test_contact_rotates_with_aim_and_stops_after_cancel(self):
        run, mech, victim, vitals = self.scenario()
        victim.mover.position = [4.0, 1.0, 0.0]
        victim.mover.body = None
        self.step(run, mech, 1, 8, yaw=16384)
        self.step(run, mech, 40, yaw=16384)
        self.assertEqual(vitals.total, 4990)
        self.step(run, mech, 1, 8, yaw=16384)
        self.assertFalse(self.booster(mech).state(7).active)
        victim.mover.position = list(mech.mover.position)
        victim.mover.body = None
        self.step(run, mech, 5, yaw=16384)
        self.assertEqual(vitals.total, 4990)

    def test_contact_impulse_moves_player_into_wall_without_tunnelling(self):
        run, mech, victim, vitals = self.scenario()
        run.match.collision_world = World(FLOOR + collision_wall(-10, 5.5, 10, 5.5))
        victim.mover.position = [0.0, 1.0, 4.0]
        victim.mover.body = None
        self.step(run, mech, 1, 8)
        self.assertEqual(vitals.total, 4990)
        vx, vy, vz = victim.mover.velocity
        self.assertAlmostEqual(vx, 0.0, delta=.001)
        self.assertGreater(vy, 3.0)
        self.assertGreater(vz, 16.0)
        self.assertGreater(victim.mover.position[1], 1.0)
        self.step(run, mech, 10, 8)
        self.assertAlmostEqual(victim.mover.position[2], 5.0, delta=.03)
        self.assertEqual(vitals.total, 4990)

    def test_zero_impulse_ruleset_preserves_contact_damage(self):
        run, mech, victim, vitals = self.scenario()
        mech.body_script.component.set(10611, 0.0)
        self.step(run, mech, 2, 8)
        self.assertEqual(vitals.total, 4990)
        self.assertEqual(victim.mover.velocity, (0.0, 0.0, 0.0))

    def test_authored_flight_suppresses_strafe_preserves_raw_input_then_restores_next_frame(self):
        for right in (-127, 127):
            with self.subTest(right=right):
                run, mech, _, _ = self.scenario()
                self.input_step(run, mech, buttons=8, forward=-127, right=right)
                self.assertLess(mech.mover.velocity[2], 0)  # modifiers begin after this input pose.
                self.assertEqual(mech.mover.values.run_forward, 5.5)
                self.input_step(run, mech, forward=-127, right=right)
                self.assertGreater(mech.mover.velocity[2], 0)
                for _ in range(40):  # use the already-verified mature cancellation phase.
                    self.input_step(run, mech, forward=-127, right=right)
                self.assertGreater(mech.mover.position[2], 0)
                # First input pose still had ordinary steering. Role16 then
                # suppresses sideways thrust, retaining only that small drift.
                self.assertLess(abs(mech.mover.position[0]), .15)
                self.assertAlmostEqual(mech.mover.velocity[0], 0, delta=.002)
                self.assertEqual(mech.mover.throttles, (right, -127))
                self.assertEqual(mech.mover.input_throttles, (right, -127))
                self.assertEqual(mech.mover.input_frame, run.frame)
                self.input_step(run, mech, buttons=8, forward=-127, right=right)
                self.assertFalse(self.booster(mech).state(2).active)
                self.assertAlmostEqual(mech.mover.values.run_forward, 11.99, places=3)
                self.input_step(run, mech, forward=-127, right=right)
                self.assertEqual(mech.mover.values.run_forward, 5.5)
                self.assertEqual(mech.mover.values.accel_air, 3.5)
                for _ in range(60):
                    self.input_step(run, mech, forward=-127, right=right)
                self.assertLess(mech.mover.velocity[2], 0)
                self.assertEqual(mech.mover.throttles, (right, -127))

    def test_forced_horizontal_thrust_uses_authored_speed_and_typed_air_acceleration(self):
        run, mech, _, _ = self.scenario()
        self.step(run, mech, 1, 8)
        self.assertEqual(mech.mover.position, [0, 1, 0])
        self.step(run, mech, 29, 8)
        # Native role16: first air acceleration step, then28 cruise steps,
        # with velocity and each position step on the mover's1/1024 grid.
        first = mover.quantise(35*11.99*.016)
        cruise = mover.quantise(11.99)
        expected = mover.quantise(first*.016)+28*mover.quantise(cruise*.016)
        self.assertAlmostEqual(mech.mover.position[2], expected, delta=.001)
        self.assertAlmostEqual(mech.mover.velocity[2], 11.99, delta=0.001)
        self.assertEqual(mech.mover.values.accel_air, 35)
        self.assertEqual(mech.mover.data.accel_air, 3.5)
        self.assertEqual(mech.mover.throttles, (0, 0))

    def test_second_press_cancels_then_cooldown_rejects_repress_and_allows_reuse(self):
        run, mech, _, _ = self.scenario()
        script = self.booster(mech)
        self.step(run, mech, 40, 8)
        first_role = next(mod for mod in mech.mover.mods.values() if mod.index == mover.FLIGHT_ROLE)
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
        next_role = next(mod for mod in mech.mover.mods.values() if mod.index == mover.FLIGHT_ROLE
                         and mod.acts(run.frame))
        self.assertNotEqual(next_role.key, first_role.key)

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

    def test_pitch_drives_3d_flight_and_cancel_restores_gravity_next_frame(self):
        for pitch in (-8192, 8192):
            for yaw in (0, 16384):
                with self.subTest(pitch=pitch, yaw=yaw):
                    run, mech, _, _ = self.scenario()
                    mech.mover.position = [0, 10, 0]
                    mech.mover.body = None
                    self.input_step(run, mech, buttons=8, pitch=pitch, yaw=yaw)
                    self.assertLess(mech.mover.velocity[1], 0)  # pre-role falling pose
                    roles = [mod for mod in mech.mover.mods.values() if mod.index == mover.FLIGHT_ROLE]
                    self.assertEqual(len(roles), 1)
                    self.assertEqual(roles[0].value, 0)
                    self.assertEqual(roles[0].start, run.frame+1)
                    for _ in range(41):
                        self.input_step(run, mech, pitch=pitch, yaw=yaw, right=127, forward=-127)
                    sign = 1 if pitch < 0 else -1
                    self.assertGreater((mech.mover.position[1]-10)*sign, 5)
                    self.assertGreater(mech.mover.velocity[1]*sign, 8)
                    along, across = (0, 2) if yaw else (2, 0)
                    self.assertGreater(mech.mover.position[along], 5)
                    self.assertAlmostEqual(mech.mover.velocity[across], 0, delta=.002)
                    self.assertTrue(mech.mover.airborne)
                    self.assertEqual(mech.mover.throttles, (127, -127))
                    self.input_step(run, mech, buttons=8, pitch=pitch, yaw=yaw)
                    self.assertFalse(self.booster(mech).state(2).active)
                    vertical = mech.mover.velocity[1]
                    self.assertEqual(roles[0].end, run.frame+1)
                    self.input_step(run, mech, pitch=pitch, yaw=yaw)
                    self.assertEqual(mech.mover.values.run_forward, 5.5)
                    self.assertLess(mech.mover.velocity[1], vertical-.2)

    def test_flight_capsule_stops_at_ceiling_floor_and_tall_wall(self):
        for obstacle in ('ceiling', 'floor', 'wall'):
            with self.subTest(obstacle=obstacle):
                run, mech, _, _ = self.scenario()
                triangles = FLOOR
                start, pitch = 10, -8192
                if obstacle == 'ceiling':
                    triangles = FLOOR+collision_box(-30, 30, 14, 15, -30, 30)
                elif obstacle == 'wall':
                    triangles = FLOOR+collision_wall(-30, 3, 30, 3, y1=30)
                else:
                    pitch = 8192
                collision = World(triangles)
                run.match.collision_world = collision
                mech.mover.use(world=collision)
                mech.mover.position = [0, start, 0]
                mech.mover.body = None
                self.input_step(run, mech, buttons=8, pitch=pitch)
                for _ in range(100):
                    self.input_step(run, mech, pitch=pitch)
                self.assertTrue(self.booster(mech).state(0).flags & Ability.RUNNING)
                if obstacle == 'ceiling':
                    body = mech.mover.body
                    self.assertLessEqual(body.position[1]+body.top+body.radius, 14.01)
                    self.assertAlmostEqual(mech.mover.velocity[1], 0, delta=.002)
                elif obstacle == 'floor':
                    # In air the capsule, rather than the walker's feet probe,
                    # meets ground. Its lowest point includes pogo above feet.
                    self.assertGreaterEqual(mech.mover.body.position[1], 1)
                    self.assertAlmostEqual(mech.mover.velocity[1], 0, delta=.002)
                else:
                    self.assertLessEqual(mech.mover.position[2], 3-mech.mover.values.stand_radius)
                    self.assertAlmostEqual(mech.mover.velocity[2], 0, delta=.002)


if __name__ == "__main__":
    unittest.main()
