"""Actual Valkyrie input/physics; native callback parity and chains stay open."""

import math
import unittest
from unittest.mock import patch

import test_game_mercy_staff as staff_tests
from test_game_mover import FLOOR, box, wall

from ow174.game import combat
from ow174.game.collision import World
from ow174.game.commands import CROUCH, FIRE, JUMP, Command
from ow174.game.content import SOLDIER, heroes
from ow174.game.mover import COMPONENT_FIELDS, JUMPED
from ow174.game.physics_snapshot import snapshot_mover
from ow174.game.script import nodes
from ow174.game.script.driver import ULT_CHARGE, ULT_COST

ULTIMATE = 0x20


class ValkyrieTests(unittest.TestCase):
    def setUp(self):
        fixture = staff_tests.MercyStaffTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        self.fixture = fixture
        self.mercy = fixture.mercy
        self.step(35)
        self.script = self.mercy.body_script
        self.instance = next(i for i in self.script.component.instances.values()
                             if i.graph.index == 0x10DA)
        self.cost = self.script.component.entity_var(ULT_COST).value()

    def step(self, count, buttons=0):
        self.fixture.step(count, buttons=buttons)

    def walk(self, count, forward=0, *, right=0, buttons=0, yaw=0, pitch=0):
        run = self.fixture.run
        for _ in range(count):
            run.frame += 1
            self.fixture.match.update(run.frame * combat.TICK, run.frame)
            for player in run.players:
                player.take_commands([Command(run.frame, forward=forward, right=right, buttons=buttons,
                                              yaw=yaw, pitch=pitch) if player is self.mercy
                                      else Command(run.frame)])

    def running(self):
        return bool(self.instance.state(9).flags & nodes.Ability.RUNNING)

    def charge(self):
        return self.script.component.entity_var(ULT_CHARGE).value()

    def start(self):
        self.script.set_ult_charge(self.cost)
        self.step(1, ULTIMATE)
        self.assertTrue(self.running())

    def test_insufficient_charge_rejects_activation_without_spending_charge(self):
        self.script.set_ult_charge(self.cost - 10)
        self.step(1, ULTIMATE)
        self.assertFalse(self.running())
        self.assertEqual(self.charge(), self.cost - 10)  # original passive timer has not elapsed
        self.assertLess(self.charge(), self.cost)

    def test_actual_start_spends_authored_cost_and_suspends_passive_charge(self):
        self.assertEqual(self.cost, 1820)
        self.start()
        self.assertEqual(self.charge(), 0)
        self.step(60)
        self.assertTrue(self.running())
        self.assertEqual(self.charge(), 0)
        self.assertTrue(self.script.component.entity_var(842).value())

    def test_release_keeps_fifteen_second_duration_then_clears_mods_and_resumes_charge(self):
        self.start()
        self.step(900)
        self.assertTrue(self.running())
        self.assertGreater(self.instance.find_var(0, 215).value(), 0)
        self.assertTrue(any(m.index == 11 and m.value == 0 for m in self.mercy.mover.mods.values()))
        self.step(50)
        self.assertFalse(self.running())
        self.assertEqual(self.instance.find_var(0, 215).value(), 0)
        self.assertFalse(self.script.component.entity_var(842).value())
        self.assertIsNone(self.mercy.mover.flight_scale)
        self.assertFalse(any(m.index == 11 for m in self.mercy.mover.mods.values()))
        self.assertIsNone(self.mercy.mover.script_data)
        self.assertEqual(self.charge(), 0)  # expiry restarts the original one-second timer
        self.step(64)
        self.assertGreater(self.charge(), 0)

    def test_repeated_press_does_not_reset_duration_or_reconsume_charge(self):
        self.start()
        self.step(20)
        before = self.instance.find_var(0, 215).value()
        for _ in range(10):
            self.step(1, ULTIMATE)
            self.step(1)
        self.assertLess(self.instance.find_var(0, 215).value(), before - 0.3)
        self.assertEqual(self.charge(), 0)

    def test_world_expires_valkyrie_and_resumes_charge_without_owner_commands(self):
        self.start()
        run = self.fixture.run
        start = run.frame
        before = tuple(self.mercy.mover.position)
        run.frame = start + 937
        self.fixture.match.update(run.frame * combat.TICK, run.frame)
        self.assertEqual(self.script.frame, start)
        self.assertTrue(self.running())
        self.assertEqual(self.charge(), 0)

        run.frame = start + 938
        self.fixture.match.update(run.frame * combat.TICK, run.frame)
        self.assertEqual(self.script.frame, start)
        self.assertEqual(tuple(self.mercy.mover.position), before)
        self.assertFalse(self.running())
        self.assertEqual(self.instance.find_var(0, 215).value(), 0)
        self.assertFalse(self.script.component.entity_var(842).value())
        self.assertIsNone(self.mercy.mover.flight_scale)
        self.assertIsNone(self.mercy.mover.script_data)
        self.assertEqual(self.charge(), 0)
        self.assertEqual(self.instance.find_var(0, 1971).value(), 0)
        self.assertTrue(self.instance.state(18).active)
        run.frame = start + 955
        self.fixture.match.update(run.frame * combat.TICK, run.frame)
        self.assertTrue(self.instance.state(17).active)
        run.frame = start + 956
        self.fixture.match.update(run.frame * combat.TICK, run.frame)
        self.assertEqual(self.script.frame, start)
        self.assertFalse(any(self.instance.state(index).active for index in (5, 17, 18)))
        self.assertEqual(self.instance.find_var(0, 1971).value(), 0)
        run.frame = start + 1002
        self.fixture.match.update(run.frame * combat.TICK, run.frame)
        self.assertEqual(self.charge(), 5)
        owner_frame = self.script.frame
        run.frame += 64
        self.fixture.match.update(run.frame * combat.TICK, run.frame)
        self.assertEqual(self.script.frame, owner_frame)
        self.assertEqual(self.charge(), 10)

    def test_late_owner_jump_replays_the_expired_flight_pose(self):
        self.start()
        self.step(900)
        run = self.fixture.run
        owner_frame = self.script.frame
        self.assertTrue(self.running())
        run.frame += 100
        self.fixture.match.update(run.frame * combat.TICK, run.frame)
        self.assertFalse(self.running())
        self.assertEqual(self.script.frame, owner_frame)
        mover = self.mercy.mover
        self.assertIsNone(mover.flight_scale)
        y = mover.position[1]
        self.mercy.take_commands([Command(owner_frame + 1, buttons=JUMP)])
        self.assertEqual(self.script.frame, owner_frame + 1)
        self.assertFalse(self.running())
        self.assertIsNone(mover.flight_scale)
        self.assertFalse(mover.flags & JUMPED)
        self.assertEqual(mover.position[1] - y, 0.0185546875)
        self.assertEqual(mover.velocity[1], 1.1513671875)

    def test_first_pose_after_world_expiry_cannot_use_retained_flight(self):
        self.start()
        run = self.fixture.run
        start = run.frame
        run.frame = start + 1000
        self.fixture.match.update(run.frame * combat.TICK, run.frame)
        self.assertFalse(self.running())
        frame = self.instance.state(2).ended_at // self.script.frame_ms + 1
        self.mercy.take_commands([Command(frame, buttons=JUMP)])
        self.assertEqual(self.script.frame, frame)
        self.assertTrue(self.mercy.mover.flags & JUMPED)
        self.assertIsNone(self.mercy.mover.flight_scale)
        self.assertFalse(hasattr(self.mercy, "valkyrie_pose"))

    def test_late_command_during_recovery_keeps_the_authored_wait_deadline(self):
        self.start()
        run = self.fixture.run
        owner_frame = self.script.frame
        run.frame += 944
        self.fixture.match.update(run.frame * combat.TICK, run.frame)
        recovery = self.instance.state(17)
        self.assertTrue(recovery.active)
        deadline = recovery.start + recovery.ms
        self.mercy.take_commands([Command(owner_frame + 1)])
        self.assertTrue(recovery.active)
        self.assertEqual(recovery.start + recovery.ms, deadline)
        run.frame = (deadline // self.script.frame_ms) + 1
        self.fixture.match.update(run.frame * combat.TICK, run.frame)
        self.assertEqual(self.script.frame, owner_frame + 1)
        self.assertFalse(recovery.active)
        self.assertEqual(self.instance.find_var(0, 1971).value(), 0)

    def test_late_ultimate_press_cannot_spend_charge_earned_after_world_expiry(self):
        self.start()
        run = self.fixture.run
        owner_frame = self.script.frame
        run.frame += 24000
        self.fixture.match.update(run.frame * combat.TICK, run.frame)
        self.assertFalse(self.running())
        self.assertEqual(self.charge(), self.cost)
        self.mercy.take_commands([Command(owner_frame + 1)])
        self.mercy.take_commands([Command(owner_frame + 2, buttons=ULTIMATE)])
        self.assertEqual(self.script.frame, owner_frame + 2)
        self.assertFalse(self.running())
        self.assertEqual(self.charge(), self.cost)
        self.mercy.take_commands([Command(run.frame)])
        self.step(1, ULTIMATE)
        self.assertTrue(self.running())
        self.assertEqual(self.charge(), 0)

    def test_duplicate_command_does_not_advance_duration(self):
        self.start()
        duration = self.instance.find_var(0, 215).value()
        self.mercy.take_commands([Command(self.fixture.run.frame, buttons=ULTIMATE)])
        self.assertEqual(self.instance.find_var(0, 215).value(), duration)
        self.assertEqual(self.charge(), 0)

    def test_active_self_regeneration_continues_after_repeated_damage(self):
        self.start()
        target = next(t for t in self.fixture.fight._targets() if t.owner is self.mercy)
        health = self.fixture.fight.vitals_of(target)
        self.fixture.fight._damage(self.fixture.enemy, target, 100, False, None,
                                   self.fixture.run.frame)
        self.step(10)
        self.assertGreater(health.total, 102)
        self.fixture.fight._damage(self.fixture.enemy, target, 10, False, None,
                                   self.fixture.run.frame)
        before = health.total
        self.step(10)
        self.assertAlmostEqual(health.total - before, 3.2, places=4)

    def test_blaster_fires_more_than_one_magazine_without_depleting_during_ultimate(self):
        self.start()
        self.step(35)
        run = self.fixture.run
        run.frame += 1
        self.fixture.match.update(run.frame * combat.TICK, run.frame)
        self.mercy.take_commands([Command(run.frame, action=2)])
        self.step(35)
        weapon = next(i for i in self.script.component.instances.values() if i.graph.index == 0x006D)
        with patch.object(self.fixture.fight, "_shoot", wraps=self.fixture.fight._shoot) as shoot:
            self.step(300, FIRE)
            self.assertGreater(shoot.call_count, 20)
        self.assertEqual(weapon.find_var(0, 53).value(), 20)
        self.step(650)
        self.assertFalse(self.running())
        self.step(20, FIRE)
        self.assertLess(weapon.find_var(0, 53).value(), 20)

    def test_caster_death_ends_ultimate_and_prevents_self_regeneration(self):
        self.start()
        target = next(t for t in self.fixture.fight._targets() if t.owner is self.mercy)
        self.fixture.fight._damage(self.fixture.enemy, target, 500, False, None,
                                   self.fixture.run.frame)
        self.step(30)
        self.assertFalse(self.running())
        self.assertEqual(self.fixture.fight.vitals_of(target).total, 0)
        self.assertFalse(self.script.component.entity_var(842).value())
        self.assertIsNone(self.mercy.mover.flight_scale)

    def test_hero_replacement_does_not_keep_ultimate_movement_modifiers(self):
        self.start()
        self.fixture.match.switch_hero(self.mercy, heroes()[SOLDIER])
        self.step(1)
        self.assertFalse(any(m.index == 11 for m in self.mercy.mover.mods.values()))
        self.assertFalse(any(i.graph.index == 0x10DA
                             for i in self.mercy.body_script.component.instances.values()))

    def test_embedded_component_uses_the_existing_extractor_field_bindings(self):
        from tools.extract_game_movers import FIELDS

        self.assertEqual(COMPONENT_FIELDS, FIELDS)
        self.start()
        data = self.mercy.mover.script_data
        self.assertIsNotNone(data)
        self.assertEqual(data.run_backward, 5.5)
        self.assertEqual(data.accel_air, 8)
        self.assertEqual(data.decel_air_base, 3)
        self.assertEqual(self.mercy.mover.data.run_backward, 4.95)

    def test_actual_backward_movement_uses_embedded_profile_before_speed_multiplier(self):
        self.walk(60, -127)
        self.assertAlmostEqual(self.mercy.mover.velocity[2], -4.95, delta=0.02)
        self.start()
        self.walk(60, -127)
        self.assertAlmostEqual(self.mercy.mover.velocity[2], -8.998, delta=0.02)
        self.assertEqual(self.mercy.mover.values.decel_air_base, 3)

    def test_pending_embedded_profile_changes_stored_physics_before_next_pose(self):
        self.start()
        mover = self.mercy.mover
        data = mover.script_data
        before = snapshot_mover(mover)
        mover.script_data = None
        try:
            self.assertNotEqual(snapshot_mover(mover), before)
        finally:
            mover.script_data = data

    def test_actual_jump_input_ascends_and_crouch_descends_without_ground_jumps(self):
        self.start()
        self.walk(60, buttons=JUMP)
        high = self.mercy.mover.position[1]
        self.assertGreater(high, 7)
        self.assertAlmostEqual(self.mercy.mover.velocity[1], 8.998, delta=0.02)
        self.assertFalse(self.mercy.mover.crouched)
        self.walk(30, buttons=CROUCH)
        self.assertLess(self.mercy.mover.position[1], high)
        self.assertLess(self.mercy.mover.velocity[1], -8.9)
        self.assertFalse(self.mercy.mover.crouched)

    def test_actual_forward_flight_follows_upward_pitch_in_three_dimensions(self):
        self.start()
        self.walk(60, 127, pitch=-8192)
        x, y, z = self.mercy.mover.position
        self.assertAlmostEqual(x, 0, delta=0.01)
        self.assertGreater(y, 5)
        self.assertGreater(z, 4)
        vx, vy, vz = self.mercy.mover.velocity
        self.assertAlmostEqual(vy, vz, delta=0.02)
        self.assertAlmostEqual(math.sqrt(vx * vx + vy * vy + vz * vz), 9, delta=0.02)

    def test_strafe_and_rise_combine_without_exceeding_authored_speed(self):
        self.start()
        self.walk(60, right=127, buttons=JUMP)
        x, y, _ = self.mercy.mover.position
        self.assertLess(x, -4)
        self.assertGreater(y, 5)
        self.assertLessEqual(math.hypot(*self.mercy.mover.velocity), 9.02)

    def test_opposing_vertical_inputs_cancel_without_crouching_or_ground_jump(self):
        self.start()
        self.walk(40, buttons=JUMP | CROUCH)
        self.assertAlmostEqual(self.mercy.mover.position[1], 1, delta=0.01)
        self.assertAlmostEqual(self.mercy.mover.velocity[1], 0, delta=0.01)
        self.assertFalse(self.mercy.mover.crouched)

    def test_flight_still_sweeps_capsule_against_static_walls(self):
        collision = World(FLOOR + wall(-5, 2, 5, 2))
        self.fixture.match.collision_world = collision
        self.mercy.mover.use(world=collision)
        self.start()
        self.walk(60, 127)
        self.assertGreater(self.mercy.mover.position[2], 1)
        self.assertLess(self.mercy.mover.position[2], 1.6)
        self.assertLess(abs(self.mercy.mover.velocity[2]), 0.01)

    def test_flight_hits_ceiling_and_descending_capsule_stops_on_floor(self):
        collision = World(FLOOR + box(-5, 5, 6, 7, -5, 5))
        self.fixture.match.collision_world = collision
        self.mercy.mover.use(world=collision)
        self.start()
        self.walk(80, buttons=JUMP)
        self.assertGreater(self.mercy.mover.position[1], 2)
        self.assertLess(self.mercy.mover.position[1], 4)
        self.assertLess(abs(self.mercy.mover.velocity[1]), 0.01)
        self.walk(100, buttons=CROUCH)
        body = self.mercy.mover.body
        self.assertGreaterEqual(body.position[1], 0.99)  # CharacterBody.position is the capsule's bottom
        self.assertLess(abs(self.mercy.mover.velocity[1]), 0.01)

    def test_forward_release_brakes_in_flight_and_expiry_restores_falling(self):
        self.start()
        self.walk(50, buttons=JUMP)
        self.walk(60, 127)
        self.walk(30)
        self.assertLess(math.hypot(*self.mercy.mover.velocity), 0.02)
        high = self.mercy.mover.position[1]
        self.step(850)
        self.assertFalse(self.running())
        self.assertIsNone(self.mercy.mover.flight_scale)
        self.assertLess(self.mercy.mover.position[1], high)
        self.assertLess(self.mercy.mover.velocity[1], 0)

    def test_duplicate_flight_command_does_not_add_movement(self):
        self.start()
        self.walk(20, buttons=JUMP)
        position = tuple(self.mercy.mover.position)
        self.mercy.take_commands([Command(self.fixture.run.frame, buttons=JUMP)])
        self.assertEqual(tuple(self.mercy.mover.position), position)

    def test_guardian_angel_overrides_flight_then_release_allows_free_ascent(self):
        self.fixture.ally.spawn = ((0.0, 1.0, 12.0), 0.0)
        self.fixture.ally.mover = self.fixture.ally.new_mover()
        self.start()
        self.walk(5, buttons=0x08)
        guardian = next(i for i in self.script.component.instances.values() if i.graph.index == 0x006E)
        self.assertFalse(guardian.state(0).active)  # original ultimate startup is still busy
        self.assertEqual(guardian.find_var(0, 61).value(), 0)
        self.step(35)
        self.walk(5, buttons=0x08)
        self.assertTrue(guardian.state(0).active)
        self.assertGreater(self.mercy.mover.position[2], 0.5)
        self.walk(1)
        self.assertFalse(guardian.state(0).active)
        height = self.mercy.mover.position[1]
        self.walk(40, buttons=JUMP)
        self.assertGreater(self.mercy.mover.position[1], height + 3)

    def test_pending_flight_hook_is_preserved_in_stored_physics(self):
        self.start()
        mover = self.mercy.mover
        self.assertEqual(mover.flight_scale, 1)
        before = snapshot_mover(mover)
        mover.flight_scale = None
        try:
            self.assertNotEqual(snapshot_mover(mover), before)
        finally:
            mover.flight_scale = 1.0

    def test_without_ultimate_held_jump_cannot_turn_descent_into_free_ascent(self):
        self.walk(120, buttons=JUMP)
        self.assertIsNone(self.mercy.mover.flight_scale)
        self.assertLessEqual(self.mercy.mover.position[1], 1.01)
        self.start()
        self.walk(60, buttons=JUMP)
        self.assertGreater(self.mercy.mover.position[1], 7)


if __name__ == "__main__":
    unittest.main()
